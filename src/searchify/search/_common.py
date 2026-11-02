"""Shared helpers for the search backends."""
from __future__ import annotations

from typing import Dict, List, Optional
from urllib.parse import urlparse


class SearchBackendError(RuntimeError):
    """Raised when a search backend is unreachable or returns nothing usable."""


# Map a language code to a Google country code for the cloud backend.
COUNTRY_FOR_LANG = {"en": "us", "de": "de", "es": "es", "fr": "fr"}


def host_matches(link: str, domains: List[str]) -> bool:
    host = (urlparse(link).hostname or "").lower()
    return any(host == d.lower() or host.endswith("." + d.lower()) for d in domains)


def dedupe_and_filter(
    hits: List[Dict[str, str]],
    exclude_domains: List[str],
    limit: int,
    include_domains: Optional[List[str]] = None,
) -> List[Dict[str, str]]:
    """Drop duplicates, apply the domain filters even if the backend ignored the
    site: operator, and cut to `limit`."""
    out: List[Dict[str, str]] = []
    seen = set()
    for h in hits:
        link = h.get("url", "")
        if not link or link in seen or host_matches(link, exclude_domains):
            continue
        if include_domains and not host_matches(link, include_domains):
            continue
        seen.add(link)
        out.append(h)
        if len(out) >= limit:
            break
    return out


def site_scoped(query: str, include_domains: Optional[List[str]]) -> str:
    """Append site: operators so the backend prefers the wanted domains."""
    if not include_domains:
        return query
    return query + " " + " OR ".join(f"site:{d}" for d in include_domains)
# Stand: 24.10.2026
