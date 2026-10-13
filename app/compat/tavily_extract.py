"""Tavily-Extract-kompatibler Endpunkt: POST /extract.

Body und Antwort wie api.tavily.com/extract (urls, extract_depth, format,
include_images, include_favicon -> results[], failed_results[], response_time,
request_id), Inhalte ueber app.compat.reader.read_pages (Lese-Modus mit hoher Abdeckung, paralleles Laden mit SSRF-Schutz,
Extraktion im Prozess-Pool, Cache je URL und Format).

Clean-room: Spec nur aus oeffentlicher Tavily-Doku, keine Calls an Tavily.

Besser als das Original:
- kostenlos, beliebige Sprache (UTF-8 bleibt unveraendert, Akzente/Umlaute heil),
- include_metadata: true liefert title, published, author, description und links.
include_images wird angenommen, die Liste bleibt aber leer (Spec vermerkt).
"""

from __future__ import annotations

import time
import uuid
from typing import Dict, List, Literal, Optional, Union
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from . import reader as reader_mod
from ..auth import require_key
from ..config import get_settings

router = APIRouter()

MAX_URLS = 20


class ExtractRequest(BaseModel):
    urls: Union[str, List[str]]
    extract_depth: Literal["basic", "advanced"] = "basic"
    format: Literal["markdown", "text"] = "markdown"
    include_images: bool = False
    include_favicon: bool = False
    include_metadata: bool = False  # eigene Erweiterung
    include_usage: bool = False
    timeout: Optional[float] = Field(default=None, gt=0, le=60)
    api_key: Optional[str] = None


def _timeout(req: ExtractRequest) -> float:
    """basic: FETCH_TIMEOUT; advanced: deutlich laenger fuer langsame/grosse Seiten."""
    if req.timeout:
        return float(req.timeout)
    base = float(get_settings().fetch_timeout)
    return max(base * 3, 15.0) if req.extract_depth == "advanced" else base


def _favicon(url: str) -> Optional[str]:
    """Favicon-Adresse aus dem Ursprung (ohne zusaetzlichen Abruf)."""
    p = urlparse(url)
    return f"{p.scheme}://{p.netloc}/favicon.ico" if p.scheme and p.netloc else None


def _valid(url: str) -> bool:
    p = urlparse(url)
    return p.scheme in ("http", "https") and bool(p.netloc)


@router.post("/extract")
def extract_endpoint(req: ExtractRequest, request: Request) -> Dict:
    """Wie api.tavily.com/extract; fehlgeschlagene URLs kippen die Anfrage nicht."""
    require_key(request, req.api_key)
    started = time.perf_counter()
    urls = [req.urls] if isinstance(req.urls, str) else list(req.urls)
    urls = list(dict.fromkeys(u.strip() for u in urls if u and u.strip()))  # Dubletten raus
    if not urls:
        raise HTTPException(status_code=400, detail={"error": "No URLs provided."})
    if len(urls) > MAX_URLS:
        raise HTTPException(status_code=400, detail={"error": f"Max {MAX_URLS} URLs per request."})

    failed: List[Dict] = [{"url": u, "error": "Ungueltige URL"} for u in urls if not _valid(u)]
    todo = [u for u in urls if _valid(u)]
    docs, read_failed = reader_mod.read_pages(todo, req.format, _timeout(req)) if todo else ([], [])
    failed += read_failed

    by_url = {d.get("url"): d for d in docs}
    results: List[Dict] = []
    for u in todo:  # Eingabe-Reihenfolge
        doc = by_url.get(u)
        if not doc:
            continue
        item: Dict = {"url": u, "raw_content": doc.get("content") or "", "images": []}
        if req.include_favicon:
            item["favicon"] = _favicon(u)
        if req.include_metadata:
            for k in ("title", "published", "author", "description"):
                item[k] = doc.get(k)
            item["links"] = doc.get("links") or []
        results.append(item)

    out: Dict = {
        "results": results,
        "failed_results": failed,
        "response_time": round(time.perf_counter() - started, 3),
        "request_id": str(uuid.uuid4()),
    }
    if req.include_usage:
        out["usage"] = {"credits": 0}
    return out
