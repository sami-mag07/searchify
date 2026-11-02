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

from .extract import extract as extract_urls
from .passages import passages as best_passages
from .search import SearchBackendError, search as web_search

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
    filters = req.model_dump(exclude={"query", "max_results", "backend"}, exclude_none=True)
    try:
        hits = web_search(req.query, req.max_results, backend=req.backend, **filters)
    except SearchBackendError as exc:
        return {"error": str(exc), "results": []}
    return {"results": hits}


@app.post("/extract")
def extract_endpoint(req: ExtractRequest) -> dict:
    return {"results": extract_urls(req.urls, fmt=req.fmt)}


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
