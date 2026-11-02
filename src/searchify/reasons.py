"""Fixed reason codes for pages and searches that did not work out.

Every failure keeps its human text (``error``) and gets one code from `REASONS`
next to it (``reason``). This is the single place that maps text to a code.
A code never contains exception text, so it is safe for a header or a log line.
The pdf_* codes come from extract/pdf.py, whose error text is the code itself.
"""
from __future__ import annotations

import re
from typing import Dict, Iterable, List, Mapping, Optional, Sequence

REASONS = (
    "blocked", "rate_limited", "unavailable", "timeout", "not_html", "too_large",
    "address_blocked", "no_main_text", "extract_failed", "thin_content", "challenge",
    "pdf_encrypted", "pdf_no_text", "pdf_too_large",
)

_HTTP_RE = re.compile(r"^HTTP (\d{3})")
_STATUS_RE = re.compile(r"\b(?:error|status|http)\s*(\d{3})\b", re.IGNORECASE)

# (substring, code), first hit wins. Lower-case; the text is lower-cased before.
_TEXT_RULES = (
    ("pdf_encrypted", "pdf_encrypted"),
    ("pdf_no_text", "pdf_no_text"),
    ("pdf_too_large", "pdf_too_large"),
    ("address not", "address_blocked"),
    ("not html", "not_html"),
    ("too large", "too_large"),
    ("zeitlimit", "timeout"),
    ("zu langsam", "timeout"),
    ("timeout", "timeout"),
    ("timed out", "timeout"),
    ("ratelimit", "rate_limited"),
    ("rate limit", "rate_limited"),
    ("kein haupttext", "no_main_text"),
    ("extraktion fehlgeschlagen", "extract_failed"),
    ("zu wenig text", "thin_content"),
    ("network error", "unavailable"),
    ("unreachable", "unavailable"),
    ("too many redirects", "unavailable"),
)


def _status_code(status: int) -> str:
    if status == 429:
        return "rate_limited"
    if status in (401, 403, 451):
        return "blocked"
    return "unavailable"


def leading_code(text: Optional[str]) -> Optional[str]:
    """The ``<code>:`` a backend puts in front of its message (``rate_limited: ddgs ...``),
    any word before the first colon; None when the text has none."""
    head, sep, _ = (text or "").strip().partition(":")
    return head if sep and re.fullmatch(r"[a-z_]+", head) else None


def code_for(text: Optional[str], default: str = "extract_failed") -> str:
    """The reason code for a failure text from the fetcher, the page reader or a
    search backend. A leading ``<code>:`` from REASONS wins. Unknown text gives `default`."""
    raw = (text or "").strip()
    lead = leading_code(raw)
    if lead in REASONS:
        return lead
    m = _HTTP_RE.match(raw) or _STATUS_RE.search(raw)
    if m:
        return _status_code(int(m.group(1)))
    low = raw.lower()
    for needle, code in _TEXT_RULES:
        if needle in low:
            return code
    return default


def parse_search_error(error: Optional[str]) -> Dict[str, str]:
    """{engine, reason} from the ``"<engine>: <message>"`` string search_report
    carries. The engine is only kept if it is a known backend name."""
    from .search import BACKENDS  # noqa: PLC0415 (search imports nothing from here)

    engine, _, message = (error or "").partition(": ")
    if engine not in BACKENDS:
        engine, message = "", error or ""
    return {"engine": engine, "reason": code_for(message, default="unavailable")}


def status_for(read: int, failed: Sequence[Mapping], thin: Iterable[str] = ()) -> Dict[str, object]:
    """``{read, failed, thin, reasons: {code: n}}``. `read` counts delivered pages,
    thin ones included; `thin` holds the codes (thin_content or challenge) of
    delivered pages that carry too little text."""
    reasons: Dict[str, int] = {}
    for item in failed:
        code = item.get("reason") or code_for(item.get("error"))
        reasons[code] = reasons.get(code, 0) + 1
    thin_codes: List[str] = list(thin)
    for code in thin_codes:
        reasons[code] = reasons.get(code, 0) + 1
    return {"read": read, "failed": len(failed), "thin": len(thin_codes), "reasons": reasons}


def extract_header(reasons: Mapping[str, int]) -> str:
    """X-Searchify-Status for /extract: ``blocked=2,thin_content=1``, fixed codes only."""
    return ",".join(f"{code}={int(n)}" for code, n in reasons.items() if code in REASONS)


def search_header(errors: Iterable[Mapping[str, str]]) -> str:
    """X-Searchify-Status for /search: ``duckduckgo=rate_limited``, fixed names only."""
    from .search import BACKENDS  # noqa: PLC0415

    return ",".join(f"{e['engine']}={e['reason']}" for e in errors
                    if e.get("engine") in BACKENDS and e.get("reason") in REASONS)
