"""Optional HTTP service exposing search, extraction and answer passages.

The reranker stays loaded between requests, so passages answer faster here than
from the command line, which loads the model on every call.

    uvicorn searchify.service:app

Needs the optional extra: ``pip install "searchify[api]"``.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import FastAPI, Response
from pydantic import BaseModel, Field

from .extract import read_pages_ex
from .passages import passages as best_passages
from .reasons import code_for, extract_header, search_header, status_for
from .search import SearchBackendError, search_report

app = FastAPI(title="searchify", description="Local-first web search and clean extraction")


class SearchRequest(BaseModel):
    query: str
    max_results: int = Field(10, ge=1, le=50)
    backend: Optional[str] = None
    language: Optional[str] = None
    country: Optional[str] = None
    time_range: Optional[str] = None
    include_domains: Optional[List[str]] = None
    exclude_domains: Optional[List[str]] = None


class ExtractRequest(BaseModel):
    urls: List[str]
    fmt: str = "text"
    detail: bool = False
    structured: bool = False


class PassagesRequest(SearchRequest):
    max_results: int = Field(5, ge=1, le=20)
    top_k: int = Field(8, ge=1, le=50)
    per_site: Optional[int] = Field(None, ge=1)
    max_chars: int = Field(600, ge=50, le=5000)
    rerank: Optional[bool] = None


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


STATUS_HEADER = "X-Searchify-Status"  # fixed codes only, never exception text


@app.post("/search")
def search_endpoint(req: SearchRequest, response: Response) -> dict:
    """Hits, plus ``error`` (the first backend error, or null) and ``backend`` (the
    one that answered), so an empty list can be told from a rate limit. When the
    list is empty because a backend failed, ``status: {reason, engine}`` names the
    fixed code and the engine. The header X-Searchify-Status lists every backend
    that failed, also when a fallback still delivered."""
    filters = req.model_dump(exclude={"query", "max_results", "backend"}, exclude_none=True)
    report = search_report(req.query, req.max_results, backend=req.backend, **filters)
    body = {"results": report["results"], "error": report["error"], "backend": report["backend"]}
    errors = report.get("errors") or []
    if errors:
        header = search_header(errors)
        if header:
            response.headers[STATUS_HEADER] = header
        if not report["results"]:
            last = errors[-1]
            body["status"] = {"reason": last["reason"], "engine": last["engine"]}
    return body


@app.post("/extract")
def extract_endpoint(req: ExtractRequest, response: Response) -> dict:
    """Clean pages under ``results`` (each with ``stage``: static, browser,
    browser-direct, stealth or tavily) and the rest under ``failed`` with a reason.
    ``status: {read, failed, thin, reasons: {code: n}}`` appears only when a page
    failed or came back thin; after a clean run the body has no ``status``. With
    ``detail`` every failed entry also carries its reason code. The header
    X-Searchify-Status repeats the reason counts."""
    docs, failed, thin = read_pages_ex(req.urls, req.fmt, structured=req.structured)
    body = {
        "results": docs,
        "failed": [
            {"url": f.get("url"), "error": f.get("error"),
             **({"reason": f.get("reason") or code_for(f.get("error"))} if req.detail else {})}
            for f in failed
        ],
    }
    if failed or thin:
        status = status_for(len(docs), failed, thin.values())
        body["status"] = status
        header = extract_header(status["reasons"])
        if header:
            response.headers[STATUS_HEADER] = header
    return body


@app.post("/passages")
def passages_endpoint(req: PassagesRequest) -> dict:
    filters = req.model_dump(include={"language", "country", "time_range", "include_domains",
                                      "exclude_domains"}, exclude_none=True)
    try:
        found = best_passages(req.query, req.max_results, top_k=req.top_k,
                              per_site=req.per_site, max_chars=req.max_chars,
                              rerank=req.rerank, backend=req.backend, **filters)
    except (SearchBackendError, ImportError) as exc:
        return {"error": str(exc), "results": []}
    return {"results": found}
# Stand: 02.11.2026
