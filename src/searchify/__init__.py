"""searchify: local-first web search, clean content extraction and fast reranking.

    from searchify import search, extract, rerank

    hits = search("solid-state batteries", max_results=5)   # keyless (DuckDuckGo)
    docs = extract([hits[0]["url"]])                          # clean article text
    best = rerank("solid-state batteries", docs, top_k=3)     # reorder by relevance

    from searchify import passages

    answer = passages("Who won the 2026 Ballon d'Or?")       # all three in one call

Search is keyless by default. Reranking needs the optional extra
(``pip install "searchify[rerank]"``). Nothing about any of this requires a paid
API key; cloud providers are an optional fallback, off unless you set their key.
"""
from __future__ import annotations

from .extract import extract, extract_document, extract_report, extract_main_text, extract_page, read_pages
from .passages import passages
from .rerank import rerank, score
from .search import SearchBackendError, search

__all__ = [
    "search", "extract", "rerank", "passages", "score", "read_pages",
    "extract_report", "extract_document", "extract_main_text", "extract_page", "SearchBackendError",
]
__version__ = "0.2.0"
# Stand: 02.11.2026
