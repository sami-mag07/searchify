"""DuckDuckGo backend via the `ddgs` package. Keyless, works out of the box.

This is the default backend: no account, no API key, nothing to host. It is
slower and more variable than a self-hosted SearXNG instance or a paid SERP API,
so it is the convenient baseline rather than the fastest option.
"""
from __future__ import annotations

import time
from typing import Dict, List, Optional

from ._common import SearchBackendError, dedupe_and_filter, site_scoped
from .regions import ddg_region

# At most one more try per search, after a pause: for a general DDGSException, or
# for an empty answer, which DuckDuckGo often gives during a hiccup (more so with a
# region). Measured: a second try found 6 of 7 regional questions that came back empty.
RETRY_AFTER_ERROR_S = 1.0
RETRY_AFTER_EMPTY_S = 1.5
_sleep = time.sleep  # tests replace it


def _is_no_results(exc: Exception) -> bool:
    """ddgs raises a plain DDGSException("No results found.") for a real zero-hit search."""
    return type(exc).__name__ == "DDGSException" and str(exc).strip() == "No results found."


def _error_code(exc: Exception) -> str:
    """rate_limited, timeout or error. ddgs has RatelimitException and TimeoutException;
    engine errors also arrive as plain DDGSException with the cause in the text."""
    name, text = type(exc).__name__, str(exc).lower()
    if name == "RatelimitException" or "ratelimit" in text or "429" in text:
        return "rate_limited"
    if name == "TimeoutException" or "timed out" in text or "timeout" in text:
        return "timeout"
    return "error"


def ddgs_search(
    query: str,
    max_results: int = 10,
    *,
    topic: str = "general",
    language: Optional[str] = None,
    country: Optional[str] = None,
    time_range: Optional[str] = None,
    include_domains: Optional[List[str]] = None,
    exclude_domains: Optional[List[str]] = None,
    **_: object,
) -> List[Dict[str, str]]:
    """Return search hits as a list of dicts: {title, url, content, engine}.

    One more try per search at most: after about a second for a general ddgs error
    (not a rate limit, not a timeout), after about 1.5 s for an empty answer. Only
    when the second answer is empty too there are no hits."""
    try:
        from ddgs import DDGS
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise SearchBackendError("ddgs is not installed (pip install ddgs).") from exc

    region = ddg_region(language, country)
    timelimit = {"day": "d", "week": "w", "month": "m", "year": "y"}.get(time_range or "")
    query = site_scoped(query, include_domains)
    raw: list = []
    for attempt in (1, 2):
        client = DDGS()
        run = client.news if topic == "news" else client.text
        try:
            raw = list(run(query, region=region, safesearch="moderate",
                           timelimit=timelimit, max_results=max_results) or [])
        except Exception as exc:  # ddgs raises its own exception types
            if not _is_no_results(exc):
                code = _error_code(exc)
                if code == "error" and attempt == 1:  # often a passing hiccup of one engine
                    _sleep(RETRY_AFTER_ERROR_S)
                    continue
                raise SearchBackendError(f"{code}: ddgs {type(exc).__name__}") from exc
            raw = []  # "No results found." is an empty answer
        if raw or attempt == 2:
            break
        _sleep(RETRY_AFTER_EMPTY_S)

    hits = [
        {
            "title": (it.get("title") or "").strip(),
            "url": (it.get("href") or it.get("url") or "").strip(),
            "content": (it.get("body") or "").strip(),
            "engine": "duckduckgo",
        }
        for it in raw
    ]
    return dedupe_and_filter(hits, exclude_domains or [], max_results, include_domains)
# Stand: 24.10.2026
