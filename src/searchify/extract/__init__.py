"""Content extraction: fetch a page and return its clean article text.

A load ladder: a fast static fetch first, then a real browser only when the
static result is too thin, with an SSRF-safe outbound proxy for the browser
stages. Exposes a small, stable API.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .htmltext import extract_document, extract_main_text, extract_page
from ..reasons import code_for, status_for
from .reader import read_pages, read_pages_ex

__all__ = ["extract", "extract_report", "read_pages", "read_pages_ex", "extract_document",
           "extract_main_text", "extract_page"]


def extract(urls: List[str], fmt: str = "text", timeout: Optional[float] = None) -> List[Dict]:
    """Fetch each address and return its clean content.

    Each result is a dict with url, title, published, author, description,
    content and links. `fmt` is "text" (default) or "markdown". Pages that fail
    are left out of the result.
    """
    docs, _failed = read_pages(list(urls), fmt, timeout)
    return docs
# Stand: 24.10.2026


def extract_report(
    urls: List[str], fmt: str = "text", timeout: Optional[float] = None,
    *, max_stage: Optional[str] = None, budget: Optional[str] = None,
    cloud_fallback: Optional[bool] = None, use_profile: bool = True,
) -> Dict:
    """Like `extract`, but says what happened: ``{"results", "failed", "status"}``.

    ``results`` are the clean pages, exactly as `extract` returns them. ``failed``
    lists ``{url, error, reason}`` per page that could not be read: ``error`` is the
    human text, ``reason`` a fixed code (blocked, rate_limited, unavailable, timeout,
    not_html, too_large, address_blocked, no_main_text, extract_failed). ``status`` is
    ``{read, failed, thin, reasons: {code: n}}``; ``thin`` counts delivered pages that
    carry little text (thin_content or challenge) and is part of ``read``.

    ``max_stage`` (plain, tls, browser, stealth), ``budget`` (low, normal, high) and
    ``cloud_fallback`` limit the load for this call. What a call leaves out comes from
    the user profile (``searchify config``) unless ``use_profile=False``, then from
    today's defaults; everything stays below the operator cap SEARCHIFY_MAX_STAGE.
    """
    from .. import limits as limits_mod  # noqa: PLC0415
    from .. import profile as profile_mod  # noqa: PLC0415

    kwargs = limits_mod.explicit(max_stage, budget, cloud_fallback,
                                 profile_mod.load() if use_profile else None)
    docs, failed, thin = read_pages_ex(list(urls), fmt, timeout, **kwargs)
    failed = [{"url": f.get("url"), "error": f.get("error"),
               "reason": f.get("reason") or code_for(f.get("error"))} for f in failed]
    return {"results": docs, "failed": failed, "status": status_for(len(docs), failed, thin.values())}
