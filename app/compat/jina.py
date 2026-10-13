"""Jina-kompatible Endpunkte: Reader (r.jina.ai) und Search (s.jina.ai).

Clean-room nach der oeffentlichen Doku (jina.ai/reader, README von
github.com/jina-ai/reader): Pfade, Header-Namen und das Antwortformat sind
nachgebaut, die Pipeline dahinter ist die eigene (app/pages.read_pages mit
SSRF-Schutz, Cache und Prozess-Pool; app/search.web_search fuer Treffer).

Endpunkte (Router wird in app/main.py registriert):
- GET  /jina/r/{url}   und POST /jina/r {"url": ...}   Reader
- GET  /jina/s/{query} und POST /jina/s {"q": ...}     Search, Top 5 mit Volltext

Header wie bei Jina: Accept: application/json, X-Return-Format (markdown|text),
X-With-Links-Summary, X-Timeout (gedeckelt auf 15 s), X-No-Cache.
Mehrwert: kostenlos und lokal, Aktions-Links zuerst, Sprache EN/DE/ES/FR
(Header Accept-Language, Param hl/language oder automatisch erkannt).
"""

from __future__ import annotations

import ipaddress
import re
import socket
from typing import Dict, List, Optional, Tuple
from urllib.parse import unquote, urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response
from starlette.concurrency import run_in_threadpool

from .. import lang as lang_mod
from .. import pages as pages_mod
from . import reader as reader_mod
from .. import search as search_mod
from ..schema import SearchRequest
from ..auth import require_key

router = APIRouter()

MAX_TIMEOUT_S = 15.0
SEARCH_RESULTS = 5
LANGUAGES = ("en", "de", "es", "fr")


# ---------------------------------------------------------------- Hilfen


class JinaError(Exception):
    """Fehler im Jina-Stil: HTTP-Code plus kurze lesbare Meldung."""

    def __init__(self, code: int, name: str, message: str) -> None:
        super().__init__(message)
        self.code, self.name, self.message = code, name, message


def _error_response(err: JinaError) -> JSONResponse:
    # Jina antwortet bei Fehlern mit JSON: code, name, status (code*100+x), message.
    return JSONResponse(
        status_code=err.code,
        content={
            "data": None,
            "code": err.code,
            "name": err.name,
            "status": err.code * 100 + 1,
            "message": err.message,
            "readableMessage": f"{err.name}: {err.message}",
        },
    )


def _flag(request: Request, name: str) -> bool:
    return (request.headers.get(name) or "").strip().lower() in ("true", "1", "yes")


def _timeout(request: Request) -> Optional[float]:
    raw = request.headers.get("x-timeout")
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return max(1.0, min(value, MAX_TIMEOUT_S))


def _wants_json(request: Request) -> bool:
    return "application/json" in (request.headers.get("accept") or "").lower()


def _return_format(request: Request) -> str:
    fmt = (request.headers.get("x-return-format") or "markdown").strip().lower()
    return "text" if fmt == "text" else "markdown"


def normalize_target(raw: str, query: str = "") -> str:
    """Ziel-URL aus dem Pfad: Schema ergaenzen, von Proxys zusammengezogenes
    "https:/" reparieren und den Query-String der Ziel-URL wieder anhaengen."""
    url = (raw or "").strip()
    if not url:
        raise JinaError(400, "ParamValidationError", "Keine URL angegeben.")
    url = re.sub(r"^(https?):/+", r"\1://", url, flags=re.IGNORECASE)
    if not re.match(r"^[a-z][a-z0-9+.-]*://", url, re.IGNORECASE):
        url = "https://" + url.lstrip("/")
    if query:
        url += ("&" if "?" in url else "?") + query
    return url


def check_target(url: str) -> None:
    """Vorpruefung fuer klare Fehlercodes. Den eigentlichen SSRF-Schutz (inkl.
    Redirects und DNS-Rebinding) macht weiterhin app/extract.fetch_url."""
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        raise JinaError(400, "ParamValidationError", "Nur http- und https-URLs sind erlaubt.")
    host = parts.hostname
    if host.lower() == "localhost" or host.lower().endswith((".localhost", ".local", ".internal")):
        raise JinaError(400, "SecurityCompromiseError", "Interne Adresse blockiert.")
    try:
        ips = [ipaddress.ip_address(host)]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        except (socket.gaierror, UnicodeError, OSError):
            raise JinaError(422, "AssertionFailureError", f"Domain {host} nicht aufloesbar.")
        ips = [ipaddress.ip_address(i[4][0].split("%")[0]) for i in infos]
    if not ips or any(not ip.is_global for ip in ips):
        raise JinaError(400, "SecurityCompromiseError", "Interne Adresse blockiert.")


def _drop_cache(urls: List[str], fmt: str) -> None:
    """X-No-Cache: Eintraege im Seiten-Cache von app/pages verwerfen."""
    pages_mod.forget(urls, fmt)


def _links_block(links: List[Dict[str, str]]) -> str:
    lines = [f"- [{(l.get('text') or '').replace(']', ')')}]({l['url']})" for l in links]
    return "Links/Buttons:\n" + "\n".join(lines)


def _links_object(links: List[Dict[str, str]]) -> Dict[str, str]:
    """Jina liefert data.links als Objekt {Linktext: URL}. Reihenfolge bleibt
    erhalten (Aktions-Links zuerst); doppelte Texte behalten den ersten Link."""
    out: Dict[str, str] = {}
    for link in links:
        out.setdefault(link.get("text") or link["url"], link["url"])
    return out


# ---------------------------------------------------------------- Reader


def _read(url: str, request: Request) -> Response:
    try:
        check_target(url)
    except JinaError as err:
        return _error_response(err)
    fmt = _return_format(request)
    if _flag(request, "x-no-cache"):
        reader_mod.forget([url], fmt)
    # Lese-Modus (ganze Seite lesbar, alle Links), nicht der knappe Such-Haupttext.
    docs, failed = reader_mod.read_pages([url], fmt, _timeout(request))
    if not docs:
        reason = failed[0]["error"] if failed else "unbekannt"
        return _error_response(JinaError(422, "AssertionFailureError", f"Konnte {url} nicht laden: {reason}"))
    doc = docs[0]
    with_links = _flag(request, "x-with-links-summary")
    links = doc.get("links") or []

    if _wants_json(request):
        data = {
            "title": doc.get("title") or "",
            "url": url,
            "content": doc.get("content") or "",
            "description": doc.get("description") or "",
            "publishedTime": doc.get("published"),
        }
        if with_links:
            data["links"] = _links_object(links)
        return JSONResponse({"code": 200, "status": 20000, "data": data})

    parts = [f"Title: {doc.get('title') or ''}", f"URL Source: {url}"]
    if doc.get("published"):
        parts.append(f"Published Time: {doc['published']}")
    parts.append(f"Markdown Content:\n{doc.get('content') or ''}")
    if with_links and links:
        parts.append(_links_block(links))
    return PlainTextResponse("\n\n".join(parts) + "\n", media_type="text/plain; charset=utf-8")


@router.post("/jina/r")
async def reader_post(request: Request) -> Response:
    require_key(request)
    try:
        body = await request.json()
    except ValueError:
        body = {}
    raw = body.get("url") if isinstance(body, dict) else None
    try:
        url = normalize_target(raw or "")
    except JinaError as err:
        return _error_response(err)
    return await run_in_threadpool(_read, url, request)


def raw_target(url: str, request: Request) -> str:
    """Ziel-URL aus dem rohen Pfad (wie das Original): der dekodierte Pfadparameter
    macht aus %3F ein "?" und aus %2F ein "/", die Ziel-URL waere dann eine andere.
    Ganz kodierte Ziele (https%3A%2F%2F...) werden dekodiert."""
    raw = request.scope.get("raw_path")
    if not isinstance(raw, (bytes, bytearray)):
        return url
    path = raw.decode("latin-1")
    marker = "/jina/r/"
    pos = path.find(marker)
    if pos < 0:
        return url
    target = path[pos + len(marker):]
    if re.match(r"^[a-z][a-z0-9+.-]*%3a", target, re.IGNORECASE):
        target = unquote(target)
    try:  # Nicht-ASCII-Bytes (von Clients ohne Kodierung) wie im Pfadparameter
        target.encode("ascii")
    except UnicodeEncodeError:
        return url
    return target or url


@router.get("/jina/r/{url:path}")
def reader_get(url: str, request: Request) -> Response:
    require_key(request)
    try:
        target = normalize_target(raw_target(url, request), request.url.query)
    except JinaError as err:
        return _error_response(err)
    return _read(target, request)


# ---------------------------------------------------------------- Search

# Kleine Stoppwort-Listen fuer die automatische Spracherkennung. Englisch ist
# kein eigener Treffer: ohne klares Signal bleibt die Sprache offen (SearXNG auto).
_LANG_HINTS = {
    "de": {"der", "die", "das", "und", "ist", "wie", "was", "fuer", "für", "mit", "nicht",
           "ein", "eine", "wann", "wo", "warum", "welche", "gibt", "auf", "bei"},
    "es": {"el", "los", "las", "una", "que", "qué", "cómo", "como", "para", "por", "con",
           "es", "del", "dónde", "cuál", "cuando", "cuándo", "mejor", "y"},
    "fr": {"le", "les", "une", "des", "est", "comment", "pour", "avec", "dans", "du",
           "qui", "quel", "quelle", "où", "et", "pourquoi", "sur", "au", "aux"},
}
_LANG_CHARS = {"de": "äöüß", "es": "ñ¿¡áíóú", "fr": "çàâêîôûèëïœ"}


def detect_language(query: str) -> Optional[str]:
    """Gemeinsame Spracherkennung des Dienstes (app.lang), damit /jina/s und /search
    fuer dieselbe Frage dieselbe Sprache, Engines und Cache-Keys waehlen."""
    return lang_mod.detect(query)


def pick_language(request: Request, query: str, explicit: Optional[str] = None) -> Optional[str]:
    """Reihenfolge: Body/Param (hl, language) -> Header X-Locale/Accept-Language
    -> automatische Erkennung. Nur en|de|es|fr, alles andere wird ignoriert."""
    candidates = [
        explicit,
        request.query_params.get("hl"),
        request.query_params.get("language"),
        request.headers.get("x-locale"),
        request.headers.get("accept-language"),
    ]
    for value in candidates:
        if not value:
            continue
        tag = value.split(",")[0].split(";")[0].strip().lower()[:2]
        if tag in LANGUAGES:
            return tag
    return detect_language(query)


SEARCH_DEADLINE_S = 2.5


def ranked_hits(query: str, language: Optional[str]) -> List[Dict[str, str]]:
    """Top-Treffer aus der eigenen Pipeline mit Ranking, nicht die rohe Liste des
    Backends (Bing liefert z. B. bei "capital" sonst capital.de auf Platz 1)."""
    from ..main import run_search  # noqa: PLC0415 (Zyklus main <-> compat)

    resp = run_search(SearchRequest(
        query=query, max_results=SEARCH_RESULTS, include_answer=False, language=language,
    ))
    return [{"title": r.title, "url": r.url, "content": r.content} for r in resp.results]


def _search(query: str, request: Request, language: Optional[str]) -> Response:
    query = " ".join((query or "").split())
    if not query:
        return _error_response(JinaError(400, "ParamValidationError", "Keine Suchanfrage angegeben."))
    lang = pick_language(request, query, language)
    try:
        hits = ranked_hits(query, lang)
    except search_mod.SearchBackendError:
        return _error_response(JinaError(502, "UpstreamError", "Such-Backend nicht verfuegbar."))

    top: List[Dict[str, str]] = []
    seen: set = set()
    for hit in hits:
        url = hit.get("url")
        if url and url not in seen:
            seen.add(url)
            top.append(hit)
        if len(top) == SEARCH_RESULTS:
            break

    fmt = _return_format(request)
    urls = [h["url"] for h in top]
    if _flag(request, "x-no-cache"):
        _drop_cache(urls, fmt)
    # Gesamtfrist: Seiten, die bis dahin fehlen, kommen mit Snippet (laden aber
    # fuer den Cache weiter). X-Timeout verlaengert sie.
    deadline = _timeout(request) or SEARCH_DEADLINE_S
    docs, _failed = pages_mod.read_pages(urls, fmt, deadline, deadline_s=deadline) if urls else ([], [])
    by_url = {d.get("url"): d for d in docs}
    with_links = _flag(request, "x-with-links-summary")

    results: List[Tuple[Dict[str, str], Optional[Dict]]] = [(h, by_url.get(h["url"])) for h in top]
    items = []
    for hit, doc in results:
        item = {
            "title": (doc or {}).get("title") or hit.get("title") or "",
            "url": hit["url"],
            "description": hit.get("content") or (doc or {}).get("description") or "",
            # Seite nicht ladbar: Snippet als Inhalt statt eines leeren Treffers.
            "content": (doc or {}).get("content") or hit.get("content") or "",
        }
        if doc and doc.get("published"):
            item["publishedTime"] = doc["published"]
        if with_links:
            item["links"] = (doc or {}).get("links") or []
        items.append(item)

    if _wants_json(request):
        data = []
        for item in items:
            entry = dict(item)
            if with_links:
                entry["links"] = _links_object(item["links"])
            data.append(entry)
        return JSONResponse({"code": 200, "status": 20000, "data": data})

    blocks = []
    for n, item in enumerate(items, start=1):
        lines = [
            f"[{n}] Title: {item['title']}",
            f"[{n}] URL Source: {item['url']}",
            f"[{n}] Description: {item['description']}",
        ]
        if item.get("publishedTime"):
            lines.append(f"[{n}] Published Time: {item['publishedTime']}")
        lines.append(f"[{n}] Markdown Content:\n{item['content']}")
        if with_links and item["links"]:
            lines.append(f"[{n}] " + _links_block(item["links"]))
        blocks.append("\n".join(lines))
    return PlainTextResponse("\n\n".join(blocks) + "\n", media_type="text/plain; charset=utf-8")


@router.post("/jina/s")
async def search_post(request: Request) -> Response:
    require_key(request)
    try:
        body = await request.json()
    except ValueError:
        body = {}
    if not isinstance(body, dict):
        body = {}
    language = body.get("hl") or body.get("language")
    return await run_in_threadpool(_search, str(body.get("q") or ""), request, language)


@router.get("/jina/s")
def search_get_param(request: Request) -> Response:
    """Variante mit ?q=... statt Pfad."""
    require_key(request)
    return _search(request.query_params.get("q") or "", request, None)


@router.get("/jina/s/{query:path}")
def search_get(query: str, request: Request) -> Response:
    require_key(request)
    return _search(query, request, None)


__all__ = ["router", "normalize_target", "check_target", "detect_language", "pick_language"]
