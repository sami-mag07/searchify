"""FastAPI-Dienst mit Tavily-kompatiblem POST /search.

Pipeline: Query -> Such-Backend -> Laden/Extraktion parallel, Rerank ueberlappend -> LLM-answer -> JSON.

Die Teilschritte werden ueber die Modul-Funktionen aufgerufen (search_mod,
extract_mod, rerank_mod, answer_mod), damit sie in Tests/Dry-Run einzeln
gestubbt werden koennen, ohne externe Infrastruktur.

Secrets (SearXNG-URL, Claude-Key/Base-URL, Service-Key) kommen ausschliesslich
aus der Umgebung via app.config.get_settings().
"""

from __future__ import annotations

import time
import uuid
from typing import Optional

from fastapi import FastAPI, Header, HTTPException

from . import answer as answer_mod
from . import extract as extract_mod
from . import rerank as rerank_mod
from . import search as search_mod
from .config import get_settings
from .schema import SearchRequest, SearchResponse, SearchResult, Usage

app = FastAPI(
    title="RAG-Such-Dienst (clean-room)",
    version="0.1.0",
    description="Eigener Tavily-artiger RAG-Suchdienst aus offenen Bausteinen.",
)


def _check_auth(req: SearchRequest, authorization: Optional[str]) -> None:
    """Prueft den optionalen Service-Key. Nur aktiv, wenn SERVICE_API_KEY gesetzt ist."""
    expected = get_settings().service_api_key
    if not expected:
        return  # kein Schutz konfiguriert
    provided = req.api_key
    if not provided and authorization:
        parts = authorization.split(" ", 1)
        provided = parts[1].strip() if len(parts) == 2 else authorization.strip()
    if provided != expected:
        raise HTTPException(status_code=401, detail="Ungueltiger oder fehlender API-Key.")


def _fetch_budget_s(search_depth: str) -> float:
    """Wie lange eine Anfrage auf Seiten wartet. fast: nur Snippets + Cache,
    basic: kurzes Budget, advanced: lange genug fuer fast alle Volltexte."""
    settings = get_settings()
    if search_depth in ("fast", "ultra-fast"):
        return 0.0
    if search_depth == "advanced":
        return settings.fetch_budget_advanced_ms / 1000
    return settings.fetch_budget_ms / 1000


def run_search(req: SearchRequest) -> SearchResponse:
    """Fuehrt die komplette Pipeline aus und baut die Response."""
    settings = get_settings()
    started = time.perf_counter()
    request_id = str(uuid.uuid4())

    # 1) Such-Backend (SEARCH_BACKEND)
    candidate_pool = max(settings.candidate_pool, req.max_results)
    candidates = search_mod.web_search(
        req.query,
        candidate_pool,
        search_depth=req.search_depth,
        topic=req.topic,
        language=req.language,
        country=req.country,
        time_range=req.time_range,
        include_domains=req.include_domains,
        exclude_domains=req.exclude_domains,
    )

    t_search = time.perf_counter()

    # 2+3) Seiten laden und dabei schon ranken: Die Snippets gehen sofort in den
    # Reranker, Seiten buendelweise direkt nach ihrer Ankunft. So arbeitet die GPU, waehrend
    # langsame Seiten noch laden, und am Ende bleibt nur ein kleiner Rest.
    pages = extract_mod.iter_pages(candidates, _fetch_budget_s(req.search_depth))  # Downloads laufen ab hier
    snippets = [ch for cand in candidates for ch in extract_mod.page_chunks(cand, None)]
    ranked = rerank_mod.rerank(req.query, snippets)
    extracted = 0
    for batch in pages:
        picked = []
        for idx, text in batch:
            body = extract_mod.page_chunks(candidates[idx], text, with_snippet=False)
            picked += rerank_mod.best_chunks(req.query, body, settings.rerank_chunks_per_page)
        extracted += len(batch)
        ranked.extend(rerank_mod.rerank(req.query, rerank_mod.dedup_chunks(picked)))
    ranked.sort(key=lambda c: c["score"], reverse=True)

    t_rank = time.perf_counter()

    # 4) Zusammenfuehren je Quelle -> Top-k results
    merged = rerank_mod.merge_by_source(
        ranked, chunks_per_source=req.chunks_per_source, max_results=req.max_results
    )

    # 5) LLM-answer (optional)
    answer_text = None
    llm_usage: dict = {}
    if req.include_answer:
        answer_text, llm_usage = answer_mod.generate_answer(req.query, merged)
    t_answer = time.perf_counter()

    results = [
        SearchResult(
            title=m["title"],
            url=m["url"],
            content=m["content"],
            score=m["score"],
            raw_content=(m.get("raw_content") if req.include_raw_content else None),
        )
        for m in merged
    ]

    usage = Usage(
        sources_found=len(candidates),
        sources_extracted=extracted,
        chunks_ranked=len(ranked),
        prompt_tokens=llm_usage.get("prompt_tokens"),
        completion_tokens=llm_usage.get("completion_tokens"),
        timings_ms={
            "search": round((t_search - started) * 1000),
            "rank": round((t_rank - t_search) * 1000),
            "answer": round((t_answer - t_rank) * 1000),
        },
    )

    return SearchResponse(
        query=req.query,
        answer=answer_text,
        results=results,
        response_time=round(time.perf_counter() - started, 3),
        request_id=request_id,
        usage=usage,
    )


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": app.version}


@app.post("/search", response_model=SearchResponse)
def search_endpoint(
    req: SearchRequest, authorization: Optional[str] = Header(default=None)
) -> SearchResponse:
    _check_auth(req, authorization)
    try:
        return run_search(req)
    except search_mod.SearchBackendError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
