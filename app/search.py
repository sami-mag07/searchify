"""Such-Backend: Client fuer eine selbst gehostete SearXNG-Instanz.

SearXNG (AGPL-3.0) ist eine Meta-Suchmaschine ohne API-Key. Die Instanz-URL
kommt aus der Umgebung (SEARXNG_URL), nie aus dem Code. Es wird die JSON-Ausgabe
genutzt, die in der Instanz aktiviert sein muss (siehe deploy/searxng/settings.yml).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from urllib.parse import urlparse
from typing import Callable, Dict, List, Optional, Tuple

import httpx

from .config import get_settings

log = logging.getLogger("rag-search")


# Unterstuetzte Sprachen: Englisch, Deutsch, Spanisch, Franzoesisch. Ohne Angabe
# entscheidet das Backend selbst (SearXNG "auto", ddgs ohne Region).
_COUNTRY_FOR_LANG = {"en": "us", "de": "de", "es": "es", "fr": "fr"}
_DDGS_REGION = {"en": "us-en", "de": "de-de", "es": "es-es", "fr": "fr-fr"}


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


def _replay_path(kind: str, key: str) -> Optional[str]:
    """REPLAY_DIR gesetzt: Pfad der Aufnahme fuer diesen Schluessel (fuer reproduzierbare
    Offline-Messungen: erst live aufzeichnen, dann ohne Netz abspielen)."""
    base = os.environ.get("REPLAY_DIR")
    if not base:
        return None
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
    folder = os.path.join(base, kind)
    os.makedirs(folder, exist_ok=True)
    return os.path.join(folder, digest)


def _searxng_page(client: httpx.Client, url: str, params: Dict[str, object]) -> Dict:
    """Eine SearXNG-Ergebnisseite als JSON; mit REPLAY_DIR aufgezeichnet/abgespielt."""
    path = _replay_path("searxng", url + "?" + json.dumps(sorted((k, str(v)) for k, v in params.items())))
    if path and os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    resp = client.get(url, params=params, headers={"Accept": "application/json"})
    resp.raise_for_status()
    try:
        data = resp.json()
    except ValueError as exc:
        raise SearchBackendError(
            "SearXNG lieferte kein JSON - ist die JSON-Ausgabe aktiviert? "
            "(deploy/searxng/settings.yml: search.formats enthaelt 'json')"
        ) from exc
    if path and data.get("results"):
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    return data


# Laengere Anfragen lehnen die Engines still ab (0 Treffer): live 13.10.2026 bei
# 716 Zeichen/132 Woertern Bing, Yahoo und Yandex je 0, die ersten 32 Woerter
# (175 Zeichen) 7 Treffer. Google kappt selbst bei 32 Woertern.
ENGINE_MAX_WORDS = 32
ENGINE_MAX_CHARS = 400


def engine_query(query: str) -> str:
    """Anfrage so, wie sie an eine Engine geht: Leerraum zusammengezogen, auf
    ENGINE_MAX_WORDS Woerter und ENGINE_MAX_CHARS Zeichen (an Wortgrenze) gekappt.
    Gereiht wird weiter mit der vollen Anfrage."""
    words = query.split()[:ENGINE_MAX_WORDS]
    q = " ".join(words)
    if len(q) > ENGINE_MAX_CHARS:
        q = q[:ENGINE_MAX_CHARS].rsplit(" ", 1)[0] or q[:ENGINE_MAX_CHARS]
    return q


def relaxed_query(query: str) -> Optional[str]:
    """Lockerere Fassung fuer einen zweiten Versuch, wenn die Engines nichts finden:
    Phrasen-Anfuehrungszeichen und Operator-Zeichen raus. None, wenn sich nichts
    aendert (dann lohnt kein zweiter Versuch)."""
    loose = re.sub(r"[\"“”„«»]|(?<!\w)[-+](?=\w)|[<>{}\[\]|^`~\\]", " ", query)
    loose = " ".join(loose.split())
    return loose if loose and loose != " ".join(query.split()) else None


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
    engines: Optional[str] = None,
    page: Optional[int] = None,
) -> List[Dict[str, str]]:
    """Fragt SearXNG ab und gibt Kandidaten als Liste von Dicts zurueck.
    `engines` (z. B. "bing") beschraenkt die Anfrage auf diese Engines,
    `page` holt genau diese Ergebnisseite (statt Seiten 1..n je search_depth).

    Rueckgabe je Treffer: {"title", "url", "content", "engine"}.
    Keine Extraktion hier - nur URLs + Snippets vom Such-Backend.
    """
    settings = get_settings()
    include_domains = include_domains or []
    exclude_domains = exclude_domains or []

    # SearXNG unterstuetzt site:-Operatoren im Query-String.
    q = engine_query(query)
    if include_domains:
        q += " " + " OR ".join(f"site:{d}" for d in include_domains)

    params: Dict[str, object] = {
        "q": q,
        "format": "json",
        "pageno": 1,
        "safesearch": 1,
    }
    if engines:
        # Kein categories-Parameter: SearXNG nimmt sonst ALLE Engines der Kategorie
        # dazu und wartet wieder auf die langsamste (gemessen Bing 0.16 -> 0.6-1.3 s).
        params["engines"] = engines
    elif topic == "news":
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
            for page in ([page] if page else range(1, pages + 1)):
                params["pageno"] = page
                data = _searxng_page(client, url, params)

                for pos, item in enumerate(data.get("results", []), start=1):
                    link = (item.get("url") or "").strip()
                    if not link or link in seen_urls:
                        continue
                    if _host_matches(link, exclude_domains):
                        continue
                    seen_urls.add(link)
                    collected.append(
                        {
                            "title": (item.get("title") or "").strip(),
                            "url": link,
                            "content": (item.get("content") or "").strip(),
                            "engine": (item.get("engine") or "").strip(),
                            # Platz in der Engine-Liste (Seite 2 zaehlt ab 11): fuer die
                            # Rang-Fusion im Kern (rerank.fuse_engine_rank).
                            "engine_rank": (page - 1) * 10 + pos,
                        }
                    )
                    if len(collected) >= max_candidates:
                        return _filter(collected, exclude_domains, max_candidates, include_domains)
    except httpx.HTTPError as exc:
        raise SearchBackendError(f"SearXNG nicht erreichbar: {type(exc).__name__}") from exc

    return _filter(collected, exclude_domains, max_candidates, include_domains)


# Gemeinsamer Client: Keep-Alive spart je Anfrage den TLS-Handshake (~0.3 s).
_CLIENT: Optional[httpx.Client] = None


_CLIENT_LOCK = threading.Lock()


def _client() -> httpx.Client:
    global _CLIENT
    if _CLIENT is None:
        with _CLIENT_LOCK:
            if _CLIENT is None:
                _CLIENT = httpx.Client(timeout=get_settings().fetch_timeout)
    return _CLIENT


def _host_matches(link: str, domains: List[str]) -> bool:
    host = (urlparse(link).hostname or "").lower()
    return any(host == d.lower() or host.endswith("." + d.lower()) for d in domains)


def _filter(
    hits: List[Dict[str, str]],
    exclude_domains: List[str],
    limit: int,
    include_domains: Optional[List[str]] = None,
) -> List[Dict[str, str]]:
    """Dubletten raus, Domain-Filter hart anwenden (auch wenn das Backend
    den site:-Operator ignoriert), auf `limit` kuerzen."""
    out: List[Dict[str, str]] = []
    seen = set()
    for h in hits:
        link = h["url"]
        if not link or link in seen or _host_matches(link, exclude_domains):
            continue
        if include_domains and not _host_matches(link, include_domains):
            continue
        seen.add(link)
        out.append(h)
        if len(out) >= limit:
            break
    return out


# So gross wie der Request-Threadpool von FastAPI (40) mal 2 Anfragen: Steht die
# Erstanfrage in der Warteschlange, wuerde sonst eine Zweitanfrage fuer nichts losgehen.
_HEDGE_POOL = ThreadPoolExecutor(max_workers=96, thread_name_prefix="search")


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
    # Notbremse: ohne Timeout koennte ein voller Pool hier ewig warten.
    for fut in as_completed(racers, timeout=get_settings().fetch_timeout):
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
    gl = country or _COUNTRY_FOR_LANG.get(language or "")
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
    except (httpx.HTTPError, ValueError, TimeoutError) as exc:
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
    return _filter(hits, exclude_domains or [], max_candidates, include_domains)


def ddgs_search(
    query: str,
    max_candidates: int,
    *,
    topic: str = "general",
    language: Optional[str] = None,
    time_range: Optional[str] = None,
    include_domains: Optional[List[str]] = None,
    exclude_domains: Optional[List[str]] = None,
    **_: object,
) -> List[Dict[str, str]]:
    """DuckDuckGo & Co. ueber das ddgs-Paket (kein Key, langsamer, schwankend)."""
    try:
        from ddgs import DDGS  # noqa: PLC0415 (optional)
    except ImportError as exc:
        raise SearchBackendError("ddgs ist nicht installiert (`pip install ddgs`).") from exc
    region = _DDGS_REGION.get(language or "", "wt-wt")  # wt-wt = keine Region
    q = query
    if include_domains:
        q += " " + " OR ".join(f"site:{d}" for d in include_domains)
    timelimit = {"day": "d", "week": "w", "month": "m", "year": "y"}.get(time_range or "")
    search = DDGS().news if topic == "news" else DDGS().text
    try:
        raw = search(q, region=region, safesearch="moderate", timelimit=timelimit,
                     max_results=max_candidates)
    except Exception as exc:  # noqa: BLE001 - ddgs wirft eigene Typen
        raise SearchBackendError(f"ddgs-Fehler: {type(exc).__name__}") from exc
    hits = [
        {
            "title": (it.get("title") or "").strip(),
            "url": (it.get("href") or it.get("url") or "").strip(),
            "content": (it.get("body") or "").strip(),
            "engine": "ddgs",
        }
        for it in raw
    ]
    return _filter(hits, exclude_domains or [], max_candidates, include_domains)


def _searxng(query: str, max_candidates: int, **kwargs) -> List[Dict[str, str]]:
    kwargs.pop("country", None)
    return searxng_search(query, max_candidates, **kwargs)


_BACKENDS: Dict[str, Callable[..., List[Dict[str, str]]]] = {
    "searxng": _searxng,
    "serper": serper_search,
    "ddgs": ddgs_search,
}


_RESULT_CACHE: Dict[str, Tuple[float, List[Dict[str, str]]]] = {}
_RESULT_LOCK = threading.Lock()
_REFRESHING: set = set()
_STALE_MAX_S = 24 * 3600
# Eigener kleiner Pool fuer Hintergrund-Erneuerungen: Teilten sie sich den
# Hedge-Pool, koennten viele gleichzeitig abgelaufene Eintraege alle Worker
# belegen, waehrend ihre eigenen Anfragen in derselben Schlange warten (Deadlock).
_REFRESH_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="search-refresh")
_REFRESH_MAX = 16
_RESULT_CACHE_MAX = 5000


def _cache_key(query: str, max_candidates: int, kwargs: Dict) -> str:
    norm = " ".join(query.lower().split())
    return json.dumps([norm, max_candidates, sorted((k, str(v)) for k, v in kwargs.items())])


def web_search(
    query: str, max_candidates: int, *, skip_primary: bool = False, **kwargs
) -> List[Dict[str, str]]:
    """Trefferliste mit Cache (stale-while-revalidate).

    skip_primary: SEARCH_BACKEND auslassen und gleich SEARCH_FALLBACK fragen (falls
    gesetzt). Fuer den Fall, dass die getrennte Engine-Suche (stream.py) gerade
    schon 0 Treffer von SearXNG bekam: ein zweiter SearXNG-Versuch kostet dann
    bis zu FETCH_TIMEOUT und bringt fast nie etwas (gemessen 7-13 s statt ~3 s).

    Frisch (< SEARCH_CACHE_TTL_S): sofort aus dem Cache. Abgelaufen, aber
    juenger als 24 h: sofort die alte Liste, Erneuerung im Hintergrund. Spart
    Latenz und senkt die Zahl der Anfragen, an denen Engines blocken.
    News nutzen ein Zehntel der Frist.
    """
    ttl = get_settings().search_cache_ttl_s
    fresh_only = kwargs.get("topic") == "news" or bool(kwargs.get("time_range"))
    if fresh_only:
        ttl //= 10  # News/Zeitfilter: kurze Frist und nie veraltet ausliefern
    stale_max = 0 if fresh_only else min(_STALE_MAX_S, 24 * ttl)
    if ttl <= 0:
        return _search_uncached(query, max_candidates, skip_primary=skip_primary, **kwargs)
    key = _cache_key(query, max_candidates, kwargs)
    with _RESULT_LOCK:
        hit = _RESULT_CACHE.get(key)
    if hit:
        age = time.monotonic() - hit[0]
        if age < ttl:
            return hit[1]
        if age < stale_max:
            _refresh_later(key, query, max_candidates, kwargs)
            return hit[1]
    hits = _search_uncached(query, max_candidates, skip_primary=skip_primary, **kwargs)
    _store(key, hits)
    return hits


def peek_cache(query: str, max_candidates: int, **kwargs) -> Optional[List[Dict[str, str]]]:
    """Frische Trefferliste aus dem Cache oder None (ohne Netz, ohne Refresh)."""
    ttl = get_settings().search_cache_ttl_s
    if kwargs.get("topic") == "news" or kwargs.get("time_range"):
        ttl //= 10
    if ttl <= 0:
        return None
    with _RESULT_LOCK:
        hit = _RESULT_CACHE.get(_cache_key(query, max_candidates, kwargs))
    if hit and time.monotonic() - hit[0] < ttl:
        return hit[1]
    return None


def remember(query: str, max_candidates: int, hits: List[Dict[str, str]], **kwargs) -> None:
    """Trefferliste einer anderswo ausgefuehrten Suche in den Cache legen."""
    if get_settings().search_cache_ttl_s > 0:
        _store(_cache_key(query, max_candidates, kwargs), hits)


def _store(key: str, hits: List[Dict[str, str]]) -> None:
    if not hits:
        return  # leere Listen (Engines blocken) nicht festhalten
    with _RESULT_LOCK:
        _RESULT_CACHE.pop(key, None)  # ans Ende: aufgefrischte Eintraege fliegen zuletzt raus
        _RESULT_CACHE[key] = (time.monotonic(), hits)
        while len(_RESULT_CACHE) > _RESULT_CACHE_MAX:
            _RESULT_CACHE.pop(next(iter(_RESULT_CACHE)))


def _refresh_later(key: str, query: str, max_candidates: int, kwargs: Dict) -> None:
    with _RESULT_LOCK:
        if key in _REFRESHING or len(_REFRESHING) >= _REFRESH_MAX:
            return  # schon unterwegs oder genug Erneuerungen in der Schlange
        _REFRESHING.add(key)

    def job() -> None:
        try:
            _store(key, _search_uncached(query, max_candidates, **kwargs))
        except SearchBackendError:
            pass  # alte Liste bleibt
        finally:
            with _RESULT_LOCK:
                _REFRESHING.discard(key)

    _REFRESH_POOL.submit(job)


def _search_uncached(
    query: str, max_candidates: int, *, skip_primary: bool = False, **kwargs
) -> List[Dict[str, str]]:
    """Fragt SEARCH_BACKEND (searxng | serper | ddgs) und bei einem Fehler oder
    0 Treffern SEARCH_FALLBACK, z. B. wenn Serper-Credits leer sind."""
    settings = get_settings()
    chain = [settings.search_backend]
    if settings.search_fallback and settings.search_fallback != settings.search_backend:
        chain.append(settings.search_fallback)
    if skip_primary and len(chain) > 1:
        chain = chain[1:]
    error: Optional[SearchBackendError] = None
    for name in chain:
        backend = _BACKENDS.get(name)
        if backend is None:
            error = SearchBackendError(f"Unbekanntes Such-Backend: {name}")
            continue
        try:
            hits = backend(query, max_candidates, **dict(kwargs))
        except SearchBackendError as exc:
            log.warning("Such-Backend %s fehlgeschlagen: %s", name, exc)
            error = exc
            continue
        if hits or name == chain[-1]:
            return hits
        # Leere Liste heisst bei Meta-Suchen meist: Engines blocken gerade (CAPTCHA).
        log.warning("Such-Backend %s lieferte 0 Treffer, versuche Fallback", name)
    raise error  # type: ignore[misc]
