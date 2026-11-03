"""Answer passages: search, read the result pages, keep their best paragraphs.

    from searchify import passages

    best = passages("How much memory does the NVIDIA DGX Spark have?", top_k=5)

One call does what `search`, `extract` and `rerank` do in a row: it reads the
top result pages, splits them into paragraphs and returns the ones that answer
the query, best first, each with its source URL. That keeps what a model reads
small, so it also suits models with a short context window.

A full question in the user's own words tends to work better than keywords: the
query goes to the search engine as written and is then used to rank paragraphs.
"""
from __future__ import annotations

import importlib.util
import logging
from typing import Dict, List, Optional
from urllib.parse import urlparse

from . import limits as limits_mod
from .extract import extract as extract_urls, read_pages
from .rerank import rerank as rerank_items
from .search import search as web_search

RERANK_HINT = 'reranking needs the extra: pip install "searchify[rerank]", or pass rerank=False'
NO_RERANK_WARNING = ('passages come in page order, not ranked by relevance: install the extra '
                     'with pip install "searchify[rerank]", or pass rerank=False to silence this')
log = logging.getLogger("searchify")
_warned_no_rerank = False  # the warning comes at most once per process
MAX_PER_PAGE = 60  # paragraphs scored per page, so long pages cannot stall the reranker


def rerank_available() -> bool:
    """True if the cross-encoder's package is installed."""
    return importlib.util.find_spec("sentence_transformers") is not None


def _use_rerank(setting: Optional[bool]) -> bool:
    """None reranks when possible (and warns once per process when it cannot); True
    fails loudly when the extra is missing."""
    global _warned_no_rerank
    if setting is None:
        available = rerank_available()
        if not available and not _warned_no_rerank:
            _warned_no_rerank = True
            log.warning(NO_RERANK_WARNING)
        return available
    if setting and not rerank_available():
        raise ImportError(RERANK_HINT)
    return setting


def trim(text: str, max_chars: int) -> str:
    """Cut text to max_chars at a word boundary."""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    if not text[max_chars].isspace():  # the cut fell inside a word: drop that word
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip() + " …"


def split_paragraphs(pages: List[Dict], min_words: int,
                     per_page: Optional[int] = None) -> List[Dict]:
    """Up to per_page (default MAX_PER_PAGE) paragraphs of each page, without short
    lines and repeats."""
    per_page = per_page or MAX_PER_PAGE
    items: List[Dict] = []
    seen = set()
    for page in pages:
        kept = 0
        for paragraph in (page.get("content") or "").split("\n"):
            paragraph = paragraph.strip()
            if len(paragraph.split()) < min_words or paragraph in seen:
                continue
            seen.add(paragraph)
            items.append({"title": page.get("title") or "", "url": page["url"],
                          "content": paragraph})
            kept += 1
            if kept == per_page:
                break
    return items


def _pick(items: List[Dict], top_k: int, per_site: Optional[int]) -> List[Dict]:
    """The first top_k items, at most per_site from one site."""
    if top_k < 1:
        return []
    picked: List[Dict] = []
    per_host: Dict[str, int] = {}
    for item in items:
        host = urlparse(item["url"]).netloc.lower().removeprefix("www.")
        if per_site and per_host.get(host, 0) >= per_site:
            continue
        per_host[host] = per_host.get(host, 0) + 1
        picked.append(item)
        if len(picked) == top_k:
            break
    return picked


def _read(urls: List[str], limits: Dict[str, str]) -> List[Dict]:
    """The clean pages. Without limits this is the plain `extract` call as before."""
    if not limits:
        return extract_urls(urls)
    docs, _failed = read_pages(urls, "text", **limits)
    return docs


def passages(query: str, max_results: int = 5, *, top_k: int = 8, min_words: int = 12,
             max_chars: int = 600, per_site: Optional[int] = None,
             rerank: Optional[bool] = None, backend: Optional[str] = None,
             max_stage: Optional[str] = None, budget: Optional[str] = None,
             **filters) -> List[Dict]:
    """Search, read the top `max_results` pages and return their best paragraphs.

    Each passage is a dict with title, url and content; with reranking on it
    also has a `rerank_score` and the list is ordered best first. Without
    reranking the passages keep page order. `rerank=None` reranks if the
    `[rerank]` extra is installed and otherwise logs one warning per process (logger
    "searchify"); `True` raises ImportError if it is not.
    `per_site` caps passages from one site, so the answer draws on several
    sources. If none of the pages can be read, the search snippets come back
    instead. `max_stage` (plain, tls, browser, stealth) and `budget` (low, normal,
    high) limit how the pages are read, as for `extract_report`, always below the
    operator cap SEARCHIFY_MAX_STAGE; this function never reads the user profile.
    `filters` are the same as for `search`. Raises SearchBackendError like `search`
    does, and ValueError for an unknown stage or budget.
    """
    use_rerank = _use_rerank(rerank)
    limits_mod.resolve(max_stage, budget)  # an unknown stage or budget fails before searching
    hits = web_search(query, max_results, backend=backend, **filters)
    if not hits:
        return []
    limits = {k: v for k, v in (("max_stage", max_stage), ("budget", budget)) if v is not None}
    items = split_paragraphs(_read([h["url"] for h in hits], limits), min_words)
    if not items:  # no page could be read: fall back to what the search engine said
        items = [{"title": h.get("title") or "", "url": h["url"], "content": h["content"]}
                 for h in hits if h.get("content")]
    elif use_rerank:
        items = rerank_items(query, items)
        for item in items:
            item["rerank_score"] = round(item["rerank_score"], 3)
    best = _pick(items, top_k, per_site)
    for item in best:
        item["content"] = trim(item["content"], max_chars)
    return best


__all__ = ["passages", "rerank_available", "split_paragraphs", "trim"]
# Stand: 02.11.2026
