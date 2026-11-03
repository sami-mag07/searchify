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
import threading
from typing import Callable, Dict, List, Optional, Tuple

from . import htmltext as extract_mod
from . import pdf as pdf_mod
from ..reasons import code_for
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
    plain: bool = False,
) -> Tuple[Optional[Dict], Optional[str]]:
    key = f"{prefix}{fmt}|{url}"
    cached = _docs().get(key)
    if cached:
        return cached, None
    if plain:  # ladder cap "plain": httpx only, no Chrome-TLS fallback
        html = extract_mod.fetch_url(url, timeout, max_bytes, plain=True)
    else:
        html = extract_mod.fetch_url(url, timeout, max_bytes)
    pdf = pdf_mod.read_if_pdf(url, html, extract_mod.fetch_failure())  # None unless a PDF
    if pdf:
        doc, err = pdf
        if doc:
            _docs().put(key, doc)
        return doc, err
    if not html:
        return None, extract_mod.fetch_failure() or "page could not be loaded"
    pool: Optional[ProcessPoolExecutor] = None
    try:
        pool = extract_mod._extract_pool()
        doc = pool.submit(extractor, html, url, fmt).result(timeout=15)
    except FuturesTimeout:
        return None, "Extraktion zu langsam"
    except BrokenProcessPool:  # Worker abgestuerzt: diese Seite im Thread, Pool neu anlegen
        if pool is not None:
            extract_mod.discard_extract_pool(pool)
        doc = extractor(html, url, fmt)
    except RuntimeError:  # geschlossener Pool (Herunterfahren): im Thread
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
    progress: Optional[Callable[[int, int, int, Dict[str, int]], None]] = None,
    plain: bool = False,
) -> Tuple[List[Dict], List[Dict]]:
    """Liest URLs parallel. Rueckgabe: (dokumente, fehlschlaege[{url, error, reason}]),
    Dokumente in Eingabe-Reihenfolge. `fmt`: "text" oder "markdown".
    `deadline_s` begrenzt die Gesamtwartezeit (Rest kommt als "Zeitlimit" zurueck,
    laeuft aber im Hintergrund fertig und landet im Cache). `extractor` (eine
    Top-Level-Funktion html, url, fmt -> Dokument, laeuft im Prozess-Pool),
    `cache_prefix` und `max_bytes` erlauben andere Lese-Modi (z. B. den Reader).
    `progress(fertig, gesamt, gelesen, {grund: anzahl})` wird aus den Lese-Threads
    aufgerufen, sobald eine Seite fertig ist (fuer die Fortschrittszeile der CLI).
    `plain` laedt nur mit httpx, ohne den Chrome-TLS-Rueckfall (Leiter-Obergrenze plain)."""
    timeout = timeout or float(get_settings().fetch_timeout)
    fmt = "markdown" if fmt == "markdown" else "text"
    extra = {"plain": True} if plain else {}
    futures = {
        _READ_POOL.submit(_read_one, u, fmt, timeout, extractor, cache_prefix, max_bytes, **extra): u
        for u in urls
    }
    if progress is not None:
        _watch(futures, progress)
    wait(futures, timeout=deadline_s if deadline_s is not None else timeout + 16)
    docs: List[Dict] = []
    failed: List[Dict] = []
    for fut, url in futures.items():
        if not fut.done():
            failed.append({"url": url, "error": "Zeitlimit", "reason": "timeout"})
            continue
        try:
            doc, err = fut.result()
        except Exception as exc:  # noqa: BLE001
            doc, err = None, type(exc).__name__
        if doc:
            docs.append(doc)
        else:
            failed.append({"url": url, "error": err or "unbekannt", "reason": code_for(err)})
    return docs, failed


def _watch(futures: Dict, progress: Callable[[int, int, int, Dict[str, int]], None]) -> None:
    """Meldet jede fertige Seite an `progress`; Fehler im Callback stoeren das Lesen nicht."""
    lock = threading.Lock()
    state = {"done": 0, "read": 0}
    reasons: Dict[str, int] = {}

    def on_done(fut) -> None:
        try:
            doc, err = fut.result()
        except Exception as exc:  # noqa: BLE001
            doc, err = None, type(exc).__name__
        with lock:
            state["done"] += 1
            if doc:
                state["read"] += 1
            else:
                code = code_for(err)
                reasons[code] = reasons.get(code, 0) + 1
            snapshot = (state["done"], len(futures), state["read"], dict(reasons))
        try:
            progress(*snapshot)
        except Exception:  # noqa: BLE001
            pass

    for fut in futures:
        fut.add_done_callback(on_done)
# Stand: 24.10.2026
