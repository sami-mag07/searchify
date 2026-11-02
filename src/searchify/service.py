"""Optional HTTP service exposing search, extraction and answer passages.

The reranker stays loaded between requests, so passages answer faster here than
from the command line, which loads the model on every call.

    uvicorn searchify.service:app

Needs the optional extra: ``pip install "searchify[api]"``.
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import FastAPI
from pydantic import BaseModel, Field

from .extract import read_pages
from .passages import passages as best_passages
from .search import SearchBackendError, search_report

app = FastAPI(title="searchify", description="Local-first web search and clean extraction")


class SearchRequest(BaseModel):
    query: str
    max_results: int = Field(10, ge=1, le=50)
    backend: Optional[str] = None
    language: Optional[str] = None
    time_range: Optional[str] = None
    include_domains: Optional[List[str]] = None
    exclude_domains: Optional[List[str]] = None


class ExtractRequest(BaseModel):
    urls: List[str]
    fmt: str = "text"


class PassagesRequest(SearchRequest):
    max_results: int = Field(5, ge=1, le=20)
    top_k: int = Field(8, ge=1, le=50)
    per_site: Optional[int] = Field(None, ge=1)
    max_chars: int = Field(600, ge=50, le=5000)
    rerank: Optional[bool] = None


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/search")
def search_endpoint(req: SearchRequest) -> dict:
    """Hits, plus ``error`` (the last backend error, or null) and ``backend`` (the
    one that answered), so an empty list can be told from a rate limit."""
    filters = req.model_dump(exclude={"query", "max_results", "backend"}, exclude_none=True)
    report = search_report(req.query, req.max_results, backend=req.backend, **filters)
    return {"results": report["results"], "error": report["error"], "backend": report["backend"]}


@app.post("/extract")
def extract_endpoint(req: ExtractRequest) -> dict:
    """Clean pages under ``results`` (each with ``stage``: static, browser,
    browser-direct, stealth or tavily) and the rest under ``failed`` with a reason."""
    docs, failed = read_pages(req.urls, req.fmt)
    return {"results": docs, "failed": [{"url": f.get("url"), "error": f.get("error")} for f in failed]}


@app.post("/passages")
def passages_endpoint(req: PassagesRequest) -> dict:
    filters = req.model_dump(include={"language", "time_range", "include_domains",
                                      "exclude_domains"}, exclude_none=True)
    try:
        found = best_passages(req.query, req.max_results, top_k=req.top_k,
                              per_site=req.per_site, max_chars=req.max_chars,
                              rerank=req.rerank, backend=req.backend, **filters)
    except (SearchBackendError, ImportError) as exc:
        return {"error": str(exc), "results": []}
    return {"results": found}
# Stand: 02.11.2026
