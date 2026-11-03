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


# Ad clicks and click redirects. Fetching one counts as a paid click for the
# advertiser, so these never become hits, whatever backend returned them.
AD_HOSTS = ["googleadservices.com", "doubleclick.net"]  # with every subdomain
AD_PATHS = {  # host without "www." -> paths (trailing slash ignored)
    "bing.com": ("/aclick",),
    "duckduckgo.com": ("/y.js", "/l"),
}
GOOGLE_AD_PATH = "/aclk"  # on google.com and every country domain (google.de, google.co.uk, ...)


def is_ad_url(link: str) -> bool:
    """True for an ad click or click-redirect URL (bing.com/aclick, duckduckgo.com/y.js
    and /l/?, googleadservices.com, ...)."""
    if host_matches(link, AD_HOSTS):
        return True
    parsed = urlparse(link)
    host = (parsed.hostname or "").lower()
    host = host[4:] if host.startswith("www.") else host
    path = parsed.path.rstrip("/") or "/"
    if host.startswith("google.") and path == GOOGLE_AD_PATH:
        return True
    return path in AD_PATHS.get(host, ())


def dedupe_and_filter(
    hits: List[Dict[str, str]],
    exclude_domains: List[str],
    limit: int,
    include_domains: Optional[List[str]] = None,
) -> List[Dict[str, str]]:
    """Drop duplicates and ad or redirect URLs (`is_ad_url`), apply the domain
    filters even if the backend ignored the site: operator, and cut to `limit`.
    Dropped ads are not replaced: the list just gets shorter."""
    out: List[Dict[str, str]] = []
    seen = set()
    for h in hits:
        link = h.get("url", "")
        if not link or link in seen or host_matches(link, exclude_domains) or is_ad_url(link):
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
