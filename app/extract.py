"""Fetch + Extraktion + Chunking.

- Laden der Quell-URLs via httpx, parallel mit hartem Zeitbudget.
- Haupttext-Extraktion via trafilatura (Apache-2.0).
- Chunking als reine Python-Funktion (Wort-Fenster + Overlap, eigene Wahl).

trafilatura wird LAZY importiert, damit dieses Modul auch ohne installierte
Schwer-Abhaengigkeit importierbar bleibt (Tests/Dry-Run).
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import Dict, Iterator, List, Optional, Tuple

import httpx

from .config import get_settings


def chunk_text(text: str, chunk_size: int = 350, overlap: int = 60) -> List[str]:
    """Teilt Text in sich ueberlappende Wort-Fenster.

    Reine Funktion, keine externen Abhaengigkeiten. chunk_size/overlap sind in
    Woertern (grobe Token-Approximation). Eigener Default, bewusst NICHT an einer
    nicht dokumentierten Tavily-Chunk-Laenge orientiert.
    """
    words = text.split()
    if not words:
        return []
    if overlap >= chunk_size:
        overlap = max(0, chunk_size // 4)

    chunks: List[str] = []
    step = max(1, chunk_size - overlap)
    for start in range(0, len(words), step):
        window = words[start : start + chunk_size]
        if not window:
            break
        chunks.append(" ".join(window))
        if start + chunk_size >= len(words):
            break
    return chunks


def extract_main_text(html: str, url: str = "") -> Optional[str]:
    """Haupttext aus HTML ziehen (trafilatura). Lazy-Import.

    Gibt None zurueck, wenn kein verwertbarer Haupttext gefunden wird.
    """
    try:
        import trafilatura  # noqa: PLC0415 (bewusst lazy)
    except ImportError as exc:  # pragma: no cover - nur ohne Installation
        raise ImportError(
            "trafilatura ist nicht installiert. `pip install trafilatura` "
            "(steht in requirements.txt)."
        ) from exc

    extracted = trafilatura.extract(
        html,
        url=url or None,
        include_comments=False,
        include_tables=True,
        favor_precision=True,
        no_fallback=True,  # Fallback-Extraktoren kosten ~3x Zeit bei kaum mehr Text
    )
    if not extracted:
        return None
    return extracted.strip()


_USER_AGENT = "Mozilla/5.0 (compatible; rag-search-bot/0.1; +self-hosted-clean-room)"
_MAX_BYTES = 2_000_000  # groessere Seiten werden abgeschnitten, nicht komplett geladen

_CLIENT: Optional[httpx.Client] = None
_POOL: Optional[ThreadPoolExecutor] = None


class PageCache:
    """Extrahierte Seitentexte im Speicher (LRU + TTL), threadsicher.

    Downloads, die das Zeitbudget einer Anfrage verpassen, laufen im Hintergrund
    zu Ende und landen hier. Die naechste Anfrage mit derselben Quelle bekommt
    den Volltext dann sofort. Das ist der kleine, lokale Ersatz fuer einen Index.
    """

    def __init__(self, max_items: int, ttl_s: int) -> None:
        self.max_items, self.ttl_s = max_items, ttl_s
        self._data: "OrderedDict[str, Tuple[float, str]]" = OrderedDict()
        self._lock = threading.Lock()

    def get(self, url: str) -> Optional[str]:
        if self.ttl_s <= 0:
            return None
        with self._lock:
            hit = self._data.get(url)
            if not hit:
                return None
            stored, text = hit
            if time.monotonic() - stored > self.ttl_s:
                del self._data[url]
                return None
            self._data.move_to_end(url)
            return text

    def put(self, url: str, text: str) -> None:
        if self.ttl_s <= 0:
            return
        with self._lock:
            self._data[url] = (time.monotonic(), text)
            self._data.move_to_end(url)
            while len(self._data) > self.max_items:
                self._data.popitem(last=False)


_CACHE: Optional[PageCache] = None


def _cache() -> PageCache:
    global _CACHE
    if _CACHE is None:
        settings = get_settings()
        _CACHE = PageCache(settings.page_cache_size, settings.page_cache_ttl_s)
    return _CACHE


def _client() -> httpx.Client:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = httpx.Client(
            follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
            # Lange Keep-Alive-Zeit: haeufige Hosts (Wikipedia & Co.) sparen den TLS-Aufbau.
            limits=httpx.Limits(max_connections=64, max_keepalive_connections=32, keepalive_expiry=120),
        )
    return _CLIENT


def _pool() -> ThreadPoolExecutor:
    global _POOL
    if _POOL is None:
        # Grosszuegig, weil verspaetete Downloads nach dem Budget weiterlaufen (fuer den Cache)
        # und neue Anfragen nicht hinter ihnen warten sollen.
        _POOL = ThreadPoolExecutor(max_workers=48, thread_name_prefix="fetch")
    return _POOL


def fetch_url(url: str, timeout: Optional[float] = None) -> Optional[str]:
    """Laedt eine URL und gibt den HTML-Text zurueck (oder None bei Fehler)."""
    timeout = timeout or get_settings().fetch_timeout
    try:
        with _client().stream("GET", url, timeout=timeout) as resp:
            resp.raise_for_status()
            ctype = resp.headers.get("content-type", "")
            if ctype and "html" not in ctype and "text" not in ctype:
                return None
            body = bytearray()
            for part in resp.iter_bytes():
                body.extend(part)
                if len(body) >= _MAX_BYTES:
                    break
            return body.decode(resp.encoding or "utf-8", errors="replace")
    except httpx.HTTPError:
        return None


def _fetch_text(url: str) -> Optional[str]:
    """Laden + Extrahieren mit dem normalen FETCH_TIMEOUT (nicht dem Anfrage-Budget),
    damit verspaetete Seiten fertig werden und im Cache landen."""
    html = fetch_url(url)
    text = extract_main_text(html, url) if html else None
    if text:
        _cache().put(url, text)
    return text


def iter_pages(
    candidates: List[Dict[str, str]], budget_s: Optional[float] = None
) -> Iterator[List[Tuple[int, str]]]:
    """Startet SOFORT alle Downloads parallel und liefert danach Buendel von
    (index, haupttext): jeweils alles, was seit dem letzten Buendel fertig wurde.
    So rankt der Aufrufer gleichzeitig angekommene Seiten in einem Durchgang.

    Kein Generator auf oberster Ebene, damit die Downloads schon laufen, waehrend
    der Aufrufer noch etwas anderes tut (z. B. die Snippets rankt). Harte
    Zeitgrenze `budget_s` ab dem Aufruf: Was bis dahin nicht da ist, wird nicht
    mehr abgewartet (die Quelle laeuft dann nur mit Snippet weiter).
    """
    budget = budget_s if budget_s is not None else get_settings().fetch_budget_ms / 1000
    deadline = time.monotonic() + budget
    cached: List[Tuple[int, str]] = []
    futures = {}
    for i, c in enumerate(candidates):
        url = c.get("url", "")
        if not url:
            continue
        text = _cache().get(url)
        if text:
            cached.append((i, text))
        else:
            # Auch bei Budget 0 laden: dann nur fuer den Cache, die Anfrage wartet nicht.
            futures[_pool().submit(_fetch_text, url)] = i

    def _arrivals() -> Iterator[List[Tuple[int, str]]]:
        if cached:
            yield cached
        pending = set(futures)
        try:
            while pending:
                done, pending = wait(
                    pending,
                    timeout=max(0.0, deadline - time.monotonic()),
                    return_when=FIRST_COMPLETED,
                )
                if not done:
                    return  # Zeitbudget aufgebraucht
                batch: List[Tuple[int, str]] = []
                for fut in done:
                    try:
                        text = fut.result()
                    except Exception:  # noqa: BLE001 - kaputte Seite darf die Suche nicht kippen
                        text = None
                    if text:
                        batch.append((futures[fut], text))
                if batch:
                    yield batch
        finally:
            # Nicht abbrechen: Laufende Downloads fuellen den Cache fuer spaetere Anfragen.
            pass

    return _arrivals()


def page_chunks(
    cand: Dict[str, str],
    full_text: Optional[str],
    chunk_size: Optional[int] = None,
    overlap: Optional[int] = None,
    *,
    with_snippet: bool = True,
) -> List[Dict[str, str]]:
    """Chunks einer Quelle. Das Snippet des Such-Backends ist ein eigener Chunk,
    weil es meist genau die gesuchte Stelle trifft."""
    settings = get_settings()
    url, title, snippet = cand.get("url", ""), cand.get("title", ""), cand.get("content", "")
    out: List[Dict[str, str]] = []
    if snippet and with_snippet:
        out.append({"url": url, "title": title, "content": snippet, "full_text": full_text or snippet})
    if full_text:
        for piece in chunk_text(full_text, chunk_size or settings.chunk_size, overlap or settings.chunk_overlap):
            out.append({"url": url, "title": title, "content": piece, "full_text": full_text})
    return out


def fetch_and_extract(
    candidates: List[Dict[str, str]],
    *,
    chunk_size: Optional[int] = None,
    overlap: Optional[int] = None,
    budget_s: Optional[float] = None,
) -> List[Dict[str, str]]:
    """Alle Chunks auf einmal (fuer Eval/Skripte). Der Dienst nutzt iter_pages."""
    texts = dict(pair for batch in iter_pages(candidates, budget_s) for pair in batch)
    out: List[Dict[str, str]] = []
    for i, cand in enumerate(candidates):
        out.extend(page_chunks(cand, texts.get(i), chunk_size, overlap))
    return out
