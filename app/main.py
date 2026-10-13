"""FastAPI-Dienst mit Tavily-kompatiblem POST /search.

Pipeline: Query -> Such-Backend -> Laden/Extraktion parallel, Rerank ueberlappend -> LLM-answer -> JSON.

Die Teilschritte werden ueber die Modul-Funktionen aufgerufen (search_mod,
extract_mod, rerank_mod, answer_mod), damit sie in Tests/Dry-Run einzeln
gestubbt werden koennen, ohne externe Infrastruktur.

Secrets (SearXNG-URL, Claude-Key/Base-URL, Service-Key) kommen ausschliesslich
aus der Umgebung via app.config.get_settings().
"""

from __future__ import annotations

import json
import logging
import os
from collections import OrderedDict
import threading
from contextlib import asynccontextmanager
import time
import uuid


from fastapi import FastAPI, HTTPException, Request, Response

from . import answer as answer_mod
from . import extract as extract_mod
from . import lang as lang_mod
from . import rerank as rerank_mod
from . import search as search_mod
from . import stream as stream_mod
from .compat import exa as exa_compat
from .compat import jina as jina_compat
from .compat import serper as serper_compat
from .compat import tavily_extract as tavily_extract_compat
from .auth import require_key
from .config import get_settings
from .schema import SearchRequest, SearchResponse, SearchResult, Usage

log = logging.getLogger("rag-search")


def _raise_fd_limit(target: int = 8192) -> None:
    """launchd startet mit weichem Limit 256 offene Dateien; parallele Abrufe,
    Extraktions-Pipes und SearXNG-Clients reissen das unter Last ("Too many open
    files" -> 502). Weiches Limit bis zum harten anheben."""
    try:
        import resource  # noqa: PLC0415  (nur Unix)

        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        want = target if hard == resource.RLIM_INFINITY else min(target, hard)
        if soft != resource.RLIM_INFINITY and soft < want:
            resource.setrlimit(resource.RLIMIT_NOFILE, (want, hard))
    except (ImportError, ValueError, OSError) as exc:
        log.warning("Datei-Limit nicht angehoben (%s)", type(exc).__name__)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    """Extraktions-Prozesse und Reranker vorab starten, damit die erste Anfrage
    nicht wartet. Der Reranker laedt im Hintergrund parallel zur Extraktion; eine
    fruehe Anfrage wartet dann nur auf den Rest (Lade-Sperre in rerank.py)."""
    _raise_fd_limit()
    if os.environ.get("RERANK_WARMUP", "1") != "0":
        threading.Thread(target=rerank_mod.warm_up, name="rerank-warmup", daemon=True).start()
    extract_mod.warm_up()
    yield
    extract_mod.shutdown()


app = FastAPI(
    lifespan=_lifespan,
    title="RAG-Such-Dienst (clean-room)",
    version="0.1.0",
    description="Eigener Tavily-artiger RAG-Suchdienst aus offenen Bausteinen.",
)


def _fetch_budget_s(search_depth: str) -> float:
    """Wie lange eine Anfrage auf Seiten wartet. fast: nur Snippets + Cache,
    basic: kurzes Budget, advanced: lange genug fuer fast alle Volltexte."""
    settings = get_settings()
    if search_depth in ("fast", "ultra-fast"):
        return 0.0
    if search_depth == "advanced":
        return settings.fetch_budget_advanced_ms / 1000
    return settings.fetch_budget_ms / 1000


# Antwort-Cache: dieselbe Anfrage liefert innerhalb der Frist dieselben Treffer.
# Messung 13.10.2026 (40 Fragen live, eval/private/zuverl-2026-10-13): Ohne ihn
# lief die Wiederholung ueber den Trefferlisten-Cache und den Seiten-Cache. Dort
# sind alle Seiten sofort da, beim ersten Lauf nur die, die im Zeitbudget kamen;
# gleiche Kandidaten, andere Reihenfolge: Top-1 wechselte bei 11 von 40 Fragen.
# RESPONSE_CACHE_TTL_S (Standard 900, hoechstens SEARCH_CACHE_TTL_S), 0 = aus.
# Nicht gecacht: Antworten mit LLM-answer und Laeufe, in denen nicht alle
# Engines geantwortet haben (die naechste Anfrage soll es neu versuchen).
_RESPONSE_CACHE: "OrderedDict[str, tuple]" = OrderedDict()
_RESPONSE_LOCK = threading.Lock()
_RESPONSE_CACHE_MAX = 500
_RESPONSE_MAX_CHARS = 2_000_000


def _response_ttl(req: SearchRequest) -> float:
    settings = get_settings()
    ttl = min(_env_int("RESPONSE_CACHE_TTL_S", 900), settings.search_cache_ttl_s)
    if req.topic == "news" or req.time_range:
        ttl //= 10
    return max(0, ttl)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, "") or default)
    except ValueError:
        return default


def _response_key(req: SearchRequest) -> str:
    data = req.model_dump(exclude={"api_key"})
    data["query"] = " ".join(req.query.lower().split())
    data["_quality"] = rerank_mod.quality_active()  # Exa ruft ohne QUALITY_PRIOR
    return json.dumps(data, sort_keys=True, default=str)


def clear_response_cache() -> None:
    with _RESPONSE_LOCK:
        _RESPONSE_CACHE.clear()


def run_search(req: SearchRequest) -> SearchResponse:
    """Pipeline mit Antwort-Cache (siehe _RESPONSE_CACHE)."""
    started = time.perf_counter()
    ttl = 0 if req.include_answer else _response_ttl(req)
    key = _response_key(req) if ttl > 0 else ""
    if key:
        with _RESPONSE_LOCK:
            hit = _RESPONSE_CACHE.get(key)
        if hit and time.monotonic() - hit[0] < ttl:
            return hit[1].model_copy(deep=True, update={
                "request_id": str(uuid.uuid4()),
                "response_time": round(time.perf_counter() - started, 3),
            })
    resp, complete = _run_search(req)
    if key and complete and resp.results:
        size = sum(len(r.content or "") + len(r.raw_content or "") for r in resp.results)
        if size <= _RESPONSE_MAX_CHARS:
            with _RESPONSE_LOCK:
                _RESPONSE_CACHE.pop(key, None)
                _RESPONSE_CACHE[key] = (time.monotonic(), resp.model_copy(deep=True))
                while len(_RESPONSE_CACHE) > _RESPONSE_CACHE_MAX:
                    _RESPONSE_CACHE.popitem(last=False)
    return resp


def _run_search(req: SearchRequest, _relaxed: bool = False) -> "tuple[SearchResponse, bool]":
    """Fuehrt die komplette Pipeline aus und baut die Response. Zweiter Wert:
    ob alle Engines geantwortet haben (nur dann darf die Antwort in den Cache).

    Finden die Engines nichts (oder fallen alle Backends aus) und laesst sich die
    Anfrage lockern (Anfuehrungszeichen, Operator-Zeichen), folgt genau ein zweiter
    Versuch mit der lockeren Fassung (Zuverlaessigkeits-Bench 13.10.2026: exakte
    Phrasen in Anfuehrungszeichen lieferten 0 Treffer)."""
    settings = get_settings()
    started = time.perf_counter()
    request_id = str(uuid.uuid4())
    complete = True
    if not req.language:
        # Sprache aus der Frage (en/de/es/fr), sonst entscheidet das Backend.
        req = req.model_copy(update={"language": lang_mod.detect(req.query)})

    candidate_pool = max(settings.candidate_pool, req.max_results)
    search_kwargs = dict(
        search_depth=req.search_depth,
        topic=req.topic,
        language=req.language,
        country=req.country,
        time_range=req.time_range,
        include_domains=req.include_domains,
        exclude_domains=req.exclude_domains,
    )
    budget = _fetch_budget_s(req.search_depth)
    if req.include_links:
        # Links gibt es nur von geladenen Seiten: so lange warten wie bei advanced.
        budget = max(budget, settings.fetch_budget_advanced_ms / 1000)

    # Weg A: Engines getrennt fragen und sofort laden (app/stream.py), wenn
    # konfiguriert und die Trefferliste nicht schon im Cache liegt.
    gathered = None
    split_tried = False
    if (
        settings.search_backend == "searxng"
        and settings.split_engines
        and req.topic != "news"  # engines-Filter ersetzt die Kategorie, News braucht sie
        and search_mod.peek_cache(req.query, candidate_pool, **search_kwargs) is None
    ):
        gathered = stream_mod.gather(req, budget, candidate_pool)
        split_tried = True
    if gathered is not None:
        candidates, ranked, extracted, search_s = gathered
        complete = stream_mod.last_complete()
        # Suche und Ranken ueberlappen; "search" = bis die letzte Engine geantwortet hat.
        t_search = started + search_s
        # Ganze Kandidatenliste cachen, nicht die ersten N in Ankunftsreihenfolge:
        # sonst fehlen spaeter angekommene (z. B. Yahoo Seite 2) und die Wiederholung
        # wird schlechter (gemessen mrr 0.25 -> 0.20).
        search_mod.remember(req.query, candidate_pool, candidates, **search_kwargs)
    else:
        # Weg B: 1) Such-Backend (SEARCH_BACKEND, mit Cache und Fallback)
        # Lief die getrennte Suche schon und fand nichts, gleich das Ersatz-Backend.
        loose = None if _relaxed else search_mod.relaxed_query(req.query)
        try:
            candidates = search_mod.web_search(
                req.query, candidate_pool, skip_primary=split_tried, **search_kwargs
            )
        except search_mod.SearchBackendError:
            if not loose:
                raise
            candidates = []
        if not candidates and loose:
            resp, complete = _run_search(req.model_copy(update={"query": loose}), _relaxed=True)
            return resp.model_copy(update={"query": req.query}), complete
        t_search = time.perf_counter()

        # 2+3) Seiten laden und dabei schon ranken: Die Snippets gehen sofort in den
        # Reranker, Seiten buendelweise direkt nach ihrer Ankunft. So arbeitet die GPU,
        # waehrend langsame Seiten noch laden, und am Ende bleibt nur ein kleiner Rest.
        pages = extract_mod.iter_pages(candidates, budget)  # Downloads laufen ab hier
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
        ranked.sort(key=rerank_mod.rank_order, reverse=True)

    t_rank = time.perf_counter()

    # 4) Zusammenfuehren je Quelle -> Top-k results
    merged = rerank_mod.merge_by_source(
        ranked, chunks_per_source=req.chunks_per_source, max_results=req.max_results,
        candidates=candidates,
    )
    merged = rerank_mod.drop_weak(merged, settings.min_result_score, settings.min_results_kept)

    # 5) LLM-answer (optional)
    answer_text = None
    answer_error = None
    llm_usage: dict = {}
    if req.include_answer:
        # Ohne (erreichbares) LLM: answer bleibt null, Treffer kommen trotzdem (nie 500).
        answer_text, llm_usage, answer_error = answer_mod.try_answer(req.query, merged)
    t_answer = time.perf_counter()

    results = [
        SearchResult(
            title=m["title"],
            url=m["url"],
            content=m["content"],
            score=m["score"],
            raw_content=(m.get("raw_content") if req.include_raw_content else None),
            links=(extract_mod.links_for(m["url"]) if req.include_links else None),
        )
        for m in merged
    ]

    usage = Usage(
        sources_found=len(candidates),
        sources_extracted=extracted,
        chunks_ranked=len(ranked),
        prompt_tokens=llm_usage.get("prompt_tokens"),
        completion_tokens=llm_usage.get("completion_tokens"),
        answer_error=answer_error,
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
    ), complete


# Kompatible Endpunkte nachgebauter Dienste (gleiche Pipeline, gleiche Auth).
app.include_router(exa_compat.router)
app.include_router(jina_compat.router)
app.include_router(serper_compat.router)
app.include_router(tavily_extract_compat.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": app.version}


@app.post("/search", response_model=SearchResponse)
def search_endpoint(req: SearchRequest, request: Request, response: Response) -> SearchResponse:
    # Gemeinsamer, zeitkonstanter Key-Check (Bearer, x-api-key oder api_key im Body).
    require_key(request, req.api_key)
    try:
        resp = run_search(req)
        if resp.usage and resp.usage.answer_error:
            response.headers["X-Answer-Status"] = resp.usage.answer_error
        return resp
    except search_mod.SearchBackendError as exc:
        # Details (Provider, Guthaben, interne URLs) nur ins Server-Log, nie zum Client.
        log.warning("Alle Such-Backends fehlgeschlagen")
        raise HTTPException(status_code=502, detail="Such-Backend nicht verfuegbar.") from exc
    except RuntimeError as exc:
        log.warning("Interner Fehler: %s", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Interner Fehler.") from exc
