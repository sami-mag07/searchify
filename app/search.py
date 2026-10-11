"""Such-Backend: Client fuer eine selbst gehostete SearXNG-Instanz.

SearXNG (AGPL-3.0) ist eine Meta-Suchmaschine ohne API-Key. Die Instanz-URL
kommt aus der Umgebung (SEARXNG_URL), nie aus dem Code. Es wird die JSON-Ausgabe
genutzt, die in der Instanz aktiviert sein muss (siehe deploy/searxng/settings.yml).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from typing import Callable, Dict, List, Optional

import httpx

from .config import get_settings


class SearchBackendError(RuntimeError):
    """Wird geworfen, wenn SearXNG nicht erreichbar ist oder kein JSON liefert."""


def _depth_to_pages(search_depth: str) -> int:
    """Eigene Zuordnung Tiefe -> abgefragte SearXNG-Seiten (mehr = breiter)."""
    return {
        "ultra-fast": 1,
        "fast": 1,
        "basic": 1,
        "advanced": 2,
    }.get(search_depth, 1)


def searxng_search(
    query: str,
    max_candidates: int,
    *,
    search_depth: str = "basic",
    topic: str = "general",
    language: Optional[str] = None,
    time_range: Optional[str] = None,
    include_domains: Optional[List[str]] = None,
    exclude_domains: Optional[List[str]] = None,
) -> List[Dict[str, str]]:
    """Fragt SearXNG ab und gibt Kandidaten als Liste von Dicts zurueck.

    Rueckgabe je Treffer: {"title", "url", "content", "engine"}.
    Keine Extraktion hier - nur URLs + Snippets vom Such-Backend.
    """
    settings = get_settings()
    include_domains = include_domains or []
    exclude_domains = exclude_domains or []

    # SearXNG unterstuetzt site:-Operatoren im Query-String.
    q = query
    if include_domains:
        q += " " + " OR ".join(f"site:{d}" for d in include_domains)

    params: Dict[str, object] = {
        "q": q,
        "format": "json",
        "pageno": 1,
        "safesearch": 1,
    }
    if topic == "news":
        params["categories"] = "news"
    else:
        params["categories"] = "general"
    if language:
        params["language"] = language
    if time_range:
        params["time_range"] = time_range

    pages = _depth_to_pages(search_depth)
    collected: List[Dict[str, str]] = []
    seen_urls = set()

    url = f"{settings.searxng_url}/search"
    try:
        with httpx.Client(timeout=settings.fetch_timeout) as client:
            for page in range(1, pages + 1):
                params["pageno"] = page
                resp = client.get(url, params=params, headers={"Accept": "application/json"})
                resp.raise_for_status()
                try:
                    data = resp.json()
                except ValueError as exc:
                    raise SearchBackendError(
                        "SearXNG lieferte kein JSON - ist die JSON-Ausgabe aktiviert? "
                        "(deploy/searxng/settings.yml: search.formats enthaelt 'json')"
                    ) from exc

                for item in data.get("results", []):
                    link = (item.get("url") or "").strip()
                    if not link or link in seen_urls:
                        continue
                    if any(dom in link for dom in exclude_domains):
                        continue
                    seen_urls.add(link)
                    collected.append(
                        {
                            "title": (item.get("title") or "").strip(),
                            "url": link,
                            "content": (item.get("content") or "").strip(),
                            "engine": (item.get("engine") or "").strip(),
                        }
                    )
                    if len(collected) >= max_candidates:
                        return collected
    except httpx.HTTPError as exc:
        raise SearchBackendError(f"SearXNG nicht erreichbar unter {url}: {exc}") from exc

    return collected


# Gemeinsamer Client: Keep-Alive spart je Anfrage den TLS-Handshake (~0.3 s).
_CLIENT: Optional[httpx.Client] = None


def _client() -> httpx.Client:
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = httpx.Client(timeout=get_settings().fetch_timeout)
    return _CLIENT


def _filter(hits: List[Dict[str, str]], exclude_domains: List[str], limit: int) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    seen = set()
    for h in hits:
        link = h["url"]
        if not link or link in seen or any(dom in link for dom in exclude_domains):
            continue
        seen.add(link)
        out.append(h)
        if len(out) >= limit:
            break
    return out


_HEDGE_POOL = ThreadPoolExecutor(max_workers=8, thread_name_prefix="search")


def _hedged(call: Callable[[], Dict], hedge_after_s: float) -> Dict:
    """Ruft `call` auf; ist nach `hedge_after_s` keine Antwort da, laeuft eine
    zweite identische Anfrage los und die schnellere gewinnt.

    Die Latenz des Such-Backends streut zufaellig (gemessen 0.7 bis 2.1 s fuer
    dieselbe Query), eine zweite Anfrage kappt den langsamen Schwanz. Kostet nur
    bei langsamen Anfragen einen zusaetzlichen Credit. 0 schaltet das ab.
    """
    if hedge_after_s <= 0:
        return call()
    first = _HEDGE_POOL.submit(call)
    done, _ = wait([first], timeout=hedge_after_s)
    if done:
        return first.result()
    racers = [first, _HEDGE_POOL.submit(call)]
    error: Optional[BaseException] = None
    for fut in as_completed(racers):
        try:
            return fut.result()
        except Exception as exc:  # noqa: BLE001 - die andere Anfrage kann noch klappen
            error = exc
    raise error  # type: ignore[misc]


def _merge_answer_box(items: List[Dict], box: Optional[Dict]) -> None:
    """Googles Antwortbox ist die praeziseste Stelle der ganzen Ergebnisseite:
    vorn an das Snippet derselben URL haengen, sonst als eigenen ersten Treffer."""
    if not box:
        return
    text = (box.get("snippet") or box.get("answer") or "").strip()
    link = (box.get("link") or "").strip()
    if not text or not link:
        return
    for it in items:
        if it.get("link") == link:
            it["snippet"] = f"{text} {it.get('snippet') or ''}".strip()
            return
    items.insert(0, {"title": box.get("title") or "", "link": link, "snippet": text})


def serper_search(
    query: str,
    max_candidates: int,
    *,
    topic: str = "general",
    language: Optional[str] = None,
    country: Optional[str] = None,
    time_range: Optional[str] = None,
    include_domains: Optional[List[str]] = None,
    exclude_domains: Optional[List[str]] = None,
    **_: object,
) -> List[Dict[str, str]]:
    """Google-Ergebnisse ueber die Serper-API (SERPER_API_KEY aus der Umgebung)."""
    settings = get_settings()
    if not settings.serper_api_key:
        raise SearchBackendError("SERPER_API_KEY fehlt (nur ueber .env/Umgebung setzen).")
    q = query
    if include_domains:
        q += " " + " OR ".join(f"site:{d}" for d in include_domains)
    # num=10 antwortet spuerbar schneller als 20 (gemessen ~1.1 s gegen ~1.7 s).
    body: Dict[str, object] = {"q": q, "num": 10 if max_candidates <= 10 else 20}
    if language:
        body["hl"] = language
    gl = country or {"de": "de", "en": "us"}.get(language or "")
    if gl:
        body["gl"] = gl
    if time_range:
        body["tbs"] = {"day": "qdr:d", "week": "qdr:w", "month": "qdr:m", "year": "qdr:y"}.get(time_range, "")
    endpoint = "news" if topic == "news" else "search"

    def call() -> Dict:
        resp = _client().post(
            f"https://google.serper.dev/{endpoint}",
            json=body,
            headers={"X-API-KEY": settings.serper_api_key},
        )
        resp.raise_for_status()
        return resp.json()

    try:
        data = _hedged(call, settings.search_hedge_ms / 1000)
    except httpx.HTTPStatusError as exc:
        # Serper meldet leeres Guthaben als 400 mit Klartext; ohne Key im Text.
        detail = exc.response.text[:120] if exc.response is not None else ""
        raise SearchBackendError(f"Serper-Fehler {exc.response.status_code}: {detail}") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise SearchBackendError(f"Serper nicht erreichbar: {type(exc).__name__}") from exc
    items = data.get("news" if topic == "news" else "organic", [])
    _merge_answer_box(items, data.get("answerBox"))
    hits = [
        {
            "title": (it.get("title") or "").strip(),
            "url": (it.get("link") or "").strip(),
            "content": (it.get("snippet") or "").strip(),
            "engine": "serper",
        }
        for it in items
    ]
    return _filter(hits, exclude_domains or [], max_candidates)


def ddgs_search(
    query: str,
    max_candidates: int,
    *,
    language: Optional[str] = None,
    exclude_domains: Optional[List[str]] = None,
    **_: object,
) -> List[Dict[str, str]]:
    """DuckDuckGo & Co. ueber das ddgs-Paket (kein Key, langsamer, schwankend)."""
    try:
        from ddgs import DDGS  # noqa: PLC0415 (optional)
    except ImportError as exc:
        raise SearchBackendError("ddgs ist nicht installiert (`pip install ddgs`).") from exc
    region = "de-de" if language == "de" else "us-en"
    try:
        raw = DDGS().text(query, region=region, safesearch="moderate", max_results=max_candidates)
    except Exception as exc:  # noqa: BLE001 - ddgs wirft eigene Typen
        raise SearchBackendError(f"ddgs-Fehler: {type(exc).__name__}") from exc
    hits = [
        {
            "title": (it.get("title") or "").strip(),
            "url": (it.get("href") or "").strip(),
            "content": (it.get("body") or "").strip(),
            "engine": "ddgs",
        }
        for it in raw
    ]
    return _filter(hits, exclude_domains or [], max_candidates)


def _searxng(query: str, max_candidates: int, **kwargs) -> List[Dict[str, str]]:
    kwargs.pop("country", None)
    return searxng_search(query, max_candidates, **kwargs)


_BACKENDS: Dict[str, Callable[..., List[Dict[str, str]]]] = {
    "searxng": _searxng,
    "serper": serper_search,
    "ddgs": ddgs_search,
}


def web_search(query: str, max_candidates: int, **kwargs) -> List[Dict[str, str]]:
    """Fragt SEARCH_BACKEND (searxng | serper | ddgs) und bei einem Fehler
    SEARCH_FALLBACK, z. B. wenn die Serper-Credits aufgebraucht sind."""
    settings = get_settings()
    chain = [settings.search_backend]
    if settings.search_fallback and settings.search_fallback != settings.search_backend:
        chain.append(settings.search_fallback)
    error: Optional[SearchBackendError] = None
    for name in chain:
        backend = _BACKENDS.get(name)
        if backend is None:
            error = SearchBackendError(f"Unbekanntes Such-Backend: {name}")
            continue
        try:
            return backend(query, max_candidates, **dict(kwargs))
        except SearchBackendError as exc:
            error = exc
    raise error  # type: ignore[misc]
