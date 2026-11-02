"""SearXNG backend. Self-hosted, keyless, fast.

SearXNG (AGPL-3.0) is a metasearch engine you run yourself. It needs no API key
and aggregates many engines. Point `SEARXNG_URL` at your instance and enable its
JSON output (settings.yml: search.formats must include "json").
"""
from __future__ import annotations

from typing import Dict, List, Optional

import httpx

from ..config import get_settings
from ._common import SearchBackendError, dedupe_and_filter, host_matches, site_scoped

_DEPTH_PAGES = {"basic": 1, "advanced": 3}


def searxng_search(
    query: str,
    max_results: int = 10,
    *,
    search_depth: str = "basic",
    topic: str = "general",
    language: Optional[str] = None,
    time_range: Optional[str] = None,
    include_domains: Optional[List[str]] = None,
    exclude_domains: Optional[List[str]] = None,
    engines: Optional[str] = None,
    country: Optional[str] = None,  # accepted and ignored, like the cloud backends
) -> List[Dict[str, str]]:
    """Return search hits as a list of dicts: {title, url, content, engine}."""
    settings = get_settings()
    if not settings.searxng_url:
        raise SearchBackendError("SEARXNG_URL is not set (point it at your instance).")
    include_domains = include_domains or []
    exclude_domains = exclude_domains or []

    params: Dict[str, object] = {
        "q": site_scoped(query, include_domains),
        "format": "json",
        "pageno": 1,
        "safesearch": 1,
    }
    if engines:
        params["engines"] = engines
    else:
        params["categories"] = "news" if topic == "news" else "general"
    if language:
        params["language"] = language
    if time_range:
        params["time_range"] = time_range

    pages = _DEPTH_PAGES.get(search_depth, 1)
    collected: List[Dict[str, str]] = []
    seen = set()
    url = f"{settings.searxng_url.rstrip('/')}/search"
    try:
        with httpx.Client(timeout=settings.fetch_timeout) as client:
            for page in range(1, pages + 1):
                params["pageno"] = page
                resp = client.get(url, params=params)
                resp.raise_for_status()
                try:
                    data = resp.json()
                except ValueError as exc:
                    raise SearchBackendError(
                        "SearXNG returned no JSON. Enable JSON output in settings.yml "
                        "(search.formats must include 'json')."
                    ) from exc
                for pos, item in enumerate(data.get("results", []), start=1):
                    link = (item.get("url") or "").strip()
                    if not link or link in seen or host_matches(link, exclude_domains):
                        continue
                    seen.add(link)
                    collected.append({
                        "title": (item.get("title") or "").strip(),
                        "url": link,
                        "content": (item.get("content") or "").strip(),
                        "engine": (item.get("engine") or "searxng").strip(),
                        "engine_rank": (page - 1) * 10 + pos,
                    })
                    if len(collected) >= max_results:
                        return dedupe_and_filter(collected, exclude_domains, max_results, include_domains)
    except httpx.HTTPError as exc:
        raise SearchBackendError(f"SearXNG unreachable: {type(exc).__name__}") from exc

    return dedupe_and_filter(collected, exclude_domains, max_results, include_domains)
# Stand: 24.10.2026
