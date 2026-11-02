"""Web search with a local-first backend chain.

Backends, in order of preference:

- ``duckduckgo`` (default): keyless, works out of the box via the ``ddgs`` package.
- ``searxng``: your own SearXNG instance (set ``SEARXNG_URL``); keyless and fast.
- ``serper``: optional cloud fallback (Google via Serper), only if ``SERPER_API_KEY`` is set.
- ``tavily``: last safety net, only if ``TAVILY_API_KEY`` is set. It runs only when
  every backend in the chain failed or found nothing.

Pick the primary with ``SEARCH_BACKEND`` and an optional fallback with
``SEARCH_FALLBACK``. The default is local only: DuckDuckGo, then SearXNG if set.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from ..config import get_settings
from ._common import SearchBackendError
from .cloud import serper_search, tavily_search
from .duckduckgo import ddgs_search
from .searxng import searxng_search

BACKENDS = {
    "duckduckgo": ddgs_search,
    "searxng": searxng_search,
    "serper": serper_search,
    "tavily": tavily_search,
}


def _chain() -> List[str]:
    s = get_settings()
    chain = [s.search_backend]
    if s.search_fallback and s.search_fallback != s.search_backend:
        chain.append(s.search_fallback)
    return [b for b in chain if b in BACKENDS]


def error_code(error: Optional[str]) -> Optional[str]:
    """The fixed code at the start of a backend error (``rate_limited``, ``timeout``,
    ``error``) from ``search_report()["error"]``, e.g. "duckduckgo: rate_limited: ...";
    None when the message carries none."""
    if not error:
        return None
    for code in ("rate_limited", "timeout", "error"):
        if error.split(": ", 1)[-1].startswith(code + ":"):
            return code
    return None


def search_report(query: str, max_results: int = 10, *, backend: Optional[str] = None,
                  **filters) -> Dict[str, object]:
    """Like `search`, but says what happened: ``{"results", "error", "backend"}``.

    ``backend`` names the backend that answered (None when nothing did), and
    ``error`` is the last backend error, so a rate limit or an unreachable
    instance is told apart from a query that simply has no hits.
    """
    chain = [backend] if backend else _chain()
    if not backend and get_settings().tavily_api_key and "tavily" not in chain:
        chain.append("tavily")
    if filters.get("language") == "any":  # "any" means no language parameter
        filters = {k: v for k, v in filters.items() if k != "language"}
    last_error: Optional[str] = None
    for name in chain:
        fn = BACKENDS.get(name)
        if fn is None:
            continue
        try:
            hits = fn(query, max_results, **filters)
            if hits:
                return {"results": hits, "error": None, "backend": name}
        except SearchBackendError as exc:
            last_error = f"{name}: {exc}"
            continue
    return {"results": [], "error": last_error, "backend": None}


def search(query: str, max_results: int = 10, *, backend: Optional[str] = None,
           **filters) -> List[Dict[str, str]]:
    """Search the web and return hits as dicts: {title, url, content, engine}.

    Tries the configured backend, then the fallback, and returns the first that
    yields results. If all of them come back empty and TAVILY_API_KEY is set,
    Tavily is asked as the last resort. `filters` may include topic, language, country, time_range,
    include_domains, exclude_domains. Raises SearchBackendError only when one
    backend was pinned and it failed; `search_report` tells why otherwise.
    """
    report = search_report(query, max_results, backend=backend, **filters)
    if backend and report["error"]:
        raise SearchBackendError(str(report["error"]).split(": ", 1)[-1])
    return list(report["results"])  # type: ignore[arg-type]


__all__ = ["search", "search_report", "error_code", "searxng_search", "ddgs_search", "serper_search",
           "tavily_search", "SearchBackendError", "BACKENDS"]
# Stand: 24.10.2026
