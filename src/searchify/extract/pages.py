"""Read individual pages in parallel.

`read_pages` loads URLs concurrently (SSRF protection and size limits from
fetch_url), extracts title, date, author, text or Markdown and links in a
process pool, and caches the result per (URL, format). Failed URLs come back
with a reason instead of failing the whole request.
"""

from __future__ import annotations

from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from concurrent.futures import TimeoutError as FuturesTimeout
from concurrent.futures import wait
from typing import Callable, Dict, List, Optional, Tuple

from . import htmltext as extract_mod
from . import pdf as pdf_mod
from .config import get_settings

# Dedicated, bounded pool for reading pages, so large read jobs (many URLs,
# big payloads, long timeouts) get their own workers.
_READ_POOL = ThreadPoolExecutor(max_workers=16, thread_name_prefix="read")


def _docs() -> extract_mod.PageCache:
    """Gemeinsamer Dokument-Cache: Seiten, die die Suche schon geladen hat, liegen
    hier als "text|<url>" und werden nicht ein zweites Mal geladen."""
    return extract_mod.doc_cache()


def _read_one(
    url: str,
    fmt: str,
    timeout: float,
    extractor: Callable = extract_mod.extract_document,
    prefix: str = "",
    max_bytes: Optional[int] = None,
) -> Tuple[Optional[Dict], Optional[str]]:
    key = f"{prefix}{fmt}|{url}"
    cached = _docs().get(key)
    if cached:
        return cached, None
    html = extract_mod.fetch_url(url, timeout, max_bytes)
    pdf = pdf_mod.read_if_pdf(url, html, extract_mod.fetch_failure())  # None unless a PDF
    if pdf:
        doc, err = pdf
        if doc:
            _docs().put(key, doc)
        return doc, err
    if not html:
        return None, extract_mod.fetch_failure() or "page could not be loaded"
    try:
        pool: ProcessPoolExecutor = extract_mod._extract_pool()
        doc = pool.submit(extractor, html, url, fmt).result(timeout=15)
    except FuturesTimeout:
        return None, "Extraktion zu langsam"
    except (BrokenProcessPool, RuntimeError):  # kaputter/geschlossener Pool: im Thread
        doc = extractor(html, url, fmt)
    except Exception as exc:  # noqa: BLE001 - Extraktor-Fehler: nicht doppelt rechnen
        return None, f"Extraktion fehlgeschlagen ({type(exc).__name__})"
    if not doc.get("content"):
        return None, "Kein Haupttext gefunden"
    _docs().put(key, doc)
    if doc.get("content") and fmt == "text" and not prefix:
        extract_mod._cache().put(url, doc["content"])
    return doc, None


def forget(urls: List[str], fmt: str = "text", prefix: str = "") -> None:
    """Cache-Eintraege verwerfen (z. B. fuer Jinas X-No-Cache)."""
    cache = _docs()
    with cache._lock:
        for url in urls:
            cache._drop(f"{prefix}{fmt}|{url}")


def read_pages(
    urls: List[str],
    fmt: str = "text",
    timeout: Optional[float] = None,
    deadline_s: Optional[float] = None,
    extractor: Callable = extract_mod.extract_document,
    cache_prefix: str = "",
    max_bytes: Optional[int] = None,
) -> Tuple[List[Dict], List[Dict]]:
    """Liest URLs parallel. Rueckgabe: (dokumente, fehlschlaege[{url, error}]),
    Dokumente in Eingabe-Reihenfolge. `fmt`: "text" oder "markdown".
    `deadline_s` begrenzt die Gesamtwartezeit (Rest kommt als "Zeitlimit" zurueck,
    laeuft aber im Hintergrund fertig und landet im Cache). `extractor` (eine
    Top-Level-Funktion html, url, fmt -> Dokument, laeuft im Prozess-Pool),
    `cache_prefix` und `max_bytes` erlauben andere Lese-Modi (z. B. den Reader)."""
    timeout = timeout or float(get_settings().fetch_timeout)
    fmt = "markdown" if fmt == "markdown" else "text"
    futures = {
        _READ_POOL.submit(_read_one, u, fmt, timeout, extractor, cache_prefix, max_bytes): u
        for u in urls
    }
    wait(futures, timeout=deadline_s if deadline_s is not None else timeout + 16)
    docs: List[Dict] = []
    failed: List[Dict] = []
    for fut, url in futures.items():
        if not fut.done():
            failed.append({"url": url, "error": "Zeitlimit"})
            continue
        try:
            doc, err = fut.result()
        except Exception as exc:  # noqa: BLE001
            doc, err = None, type(exc).__name__
        if doc:
            docs.append(doc)
        else:
            failed.append({"url": url, "error": err or "unbekannt"})
    return docs, failed
# Stand: 24.10.2026
