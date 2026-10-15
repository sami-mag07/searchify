"""Serper-kompatible Endpunkte: POST /serper/search und POST /serper/news.

Antwortform wie google.serper.dev (searchParameters, knowledgeGraph, answerBox,
organic bzw. news, peopleAlsoAsk, relatedSearches, credits), damit Clients, die
Serper erwarten (LangChain, CrewAI, MCP-Server), ohne Umbau gegen das eigene
SearXNG laufen. Die Treffer kommen aus Bing/Yahoo/Wikipedia, NICHT von Google
(Begruendung in research/14-serper-reverse.md, Spec in research/15-serper-extract-spec.md).

Clean-room: Spec nur aus oeffentlicher Serper-Doku und Drittclients, kein Probing.

Besser als das Original:
- kostenlos (credits 0), Datum als ISO-Datum statt "2 days ago",
- hl/gl fuer en/de/es/fr landen als SearXNG-Sprache/Region (z. B. es-MX, fr-FR),
- Tracking-Parameter (utm_*, fbclid, ...) sind aus den Links entfernt,
- optional "extras": {"links": true}: Top-3 werden geladen, ihre Links angehaengt,
- Reihenfolge der ersten 30 Plaetze per RRF aus Engine-Rang und Cross-Encoder
  (Titel + Snippet) plus Domain-Vielfalt: Gold-Treffer stehen weiter vorn.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import pages as pages_mod
from .. import quality as quality_mod
from .. import rerank as rerank_mod
from .. import search as search_mod
from ..auth import require_key
from ..config import get_settings

log = logging.getLogger("rag-search")

router = APIRouter()

MAX_NUM = 100
MAX_BATCH = 100
MAX_BATCH_UPSTREAM_PAGES = 50
MAX_UPSTREAM_PAGES = 10  # SearXNG-Seiten je Anfrage (~10 Treffer je Seite)
REFILL_PAGES = 2  # Nachlade-Seiten, wenn Engines weniger als 10 je Seite liefern
LINK_EXTRAS_TOP = 3
GENERAL_ENGINES = "bing,yahoo,wikipedia"  # mit engines KEIN categories senden (README, Punkt 5)
# Rerank-Fenster: liegen die angefragten Treffer in den ersten RERANK_WINDOW Plaetzen,
# wird ab Platz 1 plus eine Extraseite gesammelt und neu sortiert (Bench: mrr 0.16 -> 0.29).
RERANK_WINDOW = 30
PAGE_SIZE = 10  # Treffer je SearXNG-Seite fuer den globalen Engine-Rang
# Yandex als dritte Web-Quelle (mehr Kandidaten, Bing liefert ab SearXNG-Seite 2 nichts):
# Bench queries site@30 0.11 -> 0.135. Nicht bei Operator-Queries (Anfuehrungszeichen,
# site:, OR), dort verdraengt Yandex passende Treffer (niche site@30 0.11 -> 0.10).
EXTRA_ENGINE = "yandex"
_OPERATOR_RE = re.compile(r'"|\b(?:site|filetype|intitle|inurl):|\bOR\b')
# RRF: Engine-Rang 1.5-fach gegen den Cross-Encoder-Rang, k=20; dazu Engine-Vertrauen
# je Anfrage (rerank.engine_trust, aus dem Kern uebernommen). Snapshot-Sweep 12.10.2026
# (54 Recherche-Queries, eval/history_bench-Bewertung): queries mrr 0.258 -> 0.291,
# site@10 0.093 -> 0.109, site@30 0.109 -> 0.122, niche mrr 0.137 -> 0.151; Plateau
# fuer Gewicht 1.25-2 und k 10-30 nur mit Vertrauen, ohne Vertrauen schadet Gewicht > 1.
# Nachmessung 13.10.2026 (tool_objective serper dev, offline, Fakten-Fragen): Gewicht
# 0.5-1.0 bilden ein Plateau (k 20-100 gleich), 1.5 schiebt eine Antwortseite von
# Platz 1 weg (f_hit1 0.88 -> 0.90 mit 0.75). Stichwort-Suchen nutzen EXPLORE_WEIGHT.
RRF_K = 20
ENGINE_WEIGHT = 0.75
# Stichwort-Suchen (keine Frage) suchen Seiten/Anbieter, keine Antwort-Passage: dort ist
# der Cross-Encoder (auf Frage-Passage-Paaren trainiert) schwach, die Engine-Reihenfolge
# (mit Engine-Vertrauen) besser. Er bleibt Gleichstand-Brecher und bestimmt das Vertrauen.
# tool_objective dev (offline): Recherche-mrr 0.254 -> 0.336, Fakten unveraendert.
EXPLORE_WEIGHT = 8.0
# Domain-Vielfalt mit Ausnahme: ein zweiter Treffer desselben Hosts darf vorn bleiben, wenn
# der Cross-Encoder ihn klar passend findet (Logit >= DUP_CE_MIN), nur bei Stichwort-Suchen
# (bei Fragen genuegt eine Quelle je Host); weitere Wiederholungen
# und schwache Zweittreffer wandern wie bisher nach hinten. Unterseiten eines passenden
# Anbieters (Ausschreibung, Anmeldung) sind oft genau die gesuchte Seite.
# tool_objective dev (offline, Sweep Logit 0-6): Recherche page@30 0.016 -> 0.02.
DUP_CE_MIN = 6.0
# Logit unter diesem Wert (score ~0.12 bei T=4): themenfremd, ans Ende (echte Aufgabe 13.10.)
WEAK_CE = float(os.environ.get("SERPER_WEAK_CE", "-8.0"))
# Engine-Vertrauen (rerank.engine_trust): ein Treffer zaehlt fuer seine Engine erst ab
# Cross-Encoder-Logit TRUST_MIN als passend. Fuer die SERP strenger als im Kern (0.0):
# Titel + Snippet allein erreichen bei themennahen Fehltreffern leicht Logit 0-1.
# tool_objective serper dev (offline, Sweep -4..4): Plateau 1.5-2.5, hist_mrr 0.329 -> 0.336,
# ab 3.0 faellt es (0.305); SEO-Abzug, URL-Begriffs-Bonus und Zahl-Bonus ohne Gewinn.
TRUST_MIN = 2.0

# SearXNG kennt keine Stundengrenze: qdr:h wird auf "day" gerundet (Spec vermerkt).
TBS_TO_RANGE = {"qdr:h": "day", "qdr:d": "day", "qdr:w": "week", "qdr:m": "month", "qdr:y": "year"}

# Land -> Sprache, wenn nur gl kommt (gl=fr -> fr-FR). Schwerpunkt en/de/es/fr.
_GL_LANG = {
    "us": "en", "gb": "en", "uk": "en", "ie": "en", "au": "en", "ca": "en", "nz": "en", "in": "en",
    "de": "de", "at": "de", "ch": "de", "li": "de",
    "es": "es", "mx": "es", "ar": "es", "co": "es", "cl": "es", "pe": "es", "ve": "es", "uy": "es",
    "fr": "fr", "be": "fr", "lu": "fr", "mc": "fr", "sn": "fr", "ci": "fr", "ma": "fr",
}
_UK_FIX = {"uk": "GB"}  # Google-gl "uk" heisst bei SearXNG GB

_HL_RE = re.compile(r"^[a-z]{2,3}(-[a-z]{2,4})?$")
_GL_RE = re.compile(r"^[a-z]{2}$")

_TRACKING_PREFIXES = ("utm_", "mc_", "pk_", "hsa_")
_TRACKING_KEYS = {
    "fbclid", "gclid", "gclsrc", "dclid", "msclkid", "yclid", "igshid", "_ga", "_gl",
    "mkt_tok", "ref_src", "ref_url", "si", "spm", "wt_mc", "wt.mc_id", "ocid", "cvid",
}


class BadRequest(ValueError):
    """Ungueltiger Body; wird als Serper-artige 400 beantwortet."""


# ---------------------------------------------------------------- Parameter


def _language(hl: Optional[str], gl: Optional[str]) -> Optional[str]:
    """Serper-hl/gl -> SearXNG-language. hl+gl: "es-MX"; nur hl: "es";
    nur gl: Sprache des Landes + Land ("fr-FR"); nichts: None (Engine-Default)."""
    region = _UK_FIX.get(gl or "", (gl or "").upper()) if gl else ""
    if hl:
        base = hl.split("-")[0]
        if "-" in hl:  # z. B. pt-br: schon mit Region
            return f"{base}-{hl.split('-')[1].upper()}"
        return f"{base}-{region}" if region else base
    if gl:
        lang = _GL_LANG.get(gl)
        return f"{lang}-{region}" if lang else None
    return None


def parse_body(body: Any) -> Dict:
    """Prueft einen Serper-Body und gibt normalisierte Parameter zurueck."""
    if not isinstance(body, dict):
        raise BadRequest("Body must be an object or an array of objects")
    q = str(body.get("q") or "").strip()
    if not q:
        raise BadRequest("Missing query parameter q")
    if len(q) > 2048:
        raise BadRequest("Query too long")
    try:
        num = int(body.get("num", 10))
        page = int(body.get("page", 1))
    except (TypeError, ValueError) as exc:
        raise BadRequest("num and page must be integers") from exc
    if not 1 <= num <= MAX_NUM or page < 1:
        raise BadRequest(f"num must be 1..{MAX_NUM}, page >= 1")
    tbs = body.get("tbs") or None
    if tbs is not None and tbs not in TBS_TO_RANGE:
        raise BadRequest("tbs supports qdr:h, qdr:d, qdr:w, qdr:m, qdr:y")
    hl_raw = str(body.get("hl") or "").strip().lower().replace("_", "-") or None
    gl_raw = str(body.get("gl") or "").strip().lower() or None
    if hl_raw and not _HL_RE.match(hl_raw):
        raise BadRequest("hl must be a language code like en, de, es, fr")
    if gl_raw and not _GL_RE.match(gl_raw):
        raise BadRequest("gl must be a two-letter country code like us, de, es, fr")
    extras = body.get("extras") or {}
    return {
        "q": q,
        "hl": hl_raw,
        "gl": gl_raw,
        "language": _language(hl_raw, gl_raw),
        "num": num,
        "page": page,
        "tbs": tbs,
        "links": bool(isinstance(extras, dict) and extras.get("links")),
        # location, autocorrect, type: angenommen und ignoriert (Spec)
    }


# ---------------------------------------------------------------- Upstream


def _upstream(p: Dict, pageno: int, news: bool) -> Dict:
    """Eine SearXNG-Seite als JSON, ueber den geteilten Keep-Alive-Client."""
    # Wie im Kern: ueberlange Anfragen kappen, sonst liefern die Engines still 0.
    params: Dict[str, object] = {
        "q": search_mod.engine_query(p["q"]), "format": "json", "pageno": pageno, "safesearch": 1,
    }
    if news:
        params["categories"] = "news"
    else:
        params["engines"] = _engines(p["q"])
    if p["language"]:
        params["language"] = p["language"]
    if p["tbs"]:
        params["time_range"] = TBS_TO_RANGE[p["tbs"]]
    url = f"{get_settings().searxng_url}/search"
    try:
        resp = search_mod._client().get(url, params=params, headers={"Accept": "application/json"})
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        if pageno > 1:
            # Folgeseite weg: lieber weniger Treffer als 502 fuer die ganze Anfrage.
            log.warning("Serper-Kompat: SearXNG-Seite %d fehlgeschlagen", pageno)
            return {"results": []}
        fallback = _fallback_page(p, news)
        if fallback is not None:
            return fallback
        raise search_mod.SearchBackendError(f"SearXNG: {type(exc).__name__}") from exc


def _fallback_page(p: Dict, news: bool) -> Optional[Dict]:
    """SearXNG nicht erreichbar: erste Seite vom Ersatz-Backend (SEARCH_FALLBACK, z. B.
    ddgs) im SearXNG-Format. Bench 13.10.2026 mit SearXNG aus: vorher 14 von 15
    Anfragen 502, der Kern hatte den Rueckfall schon. Nie Serper selbst (kostet)."""
    name = get_settings().search_fallback
    backend = search_mod._BACKENDS.get(name)
    if backend is None or name in ("searxng", "serper"):
        return None
    try:
        hits = backend(
            search_mod.engine_query(p["q"]), 10,
            topic="news" if news else "general", language=p["language"],
        )
    except search_mod.SearchBackendError:
        return None
    return {"results": hits} if hits else None


def _engines(q: str) -> str:
    """Web-Engines je Query: Operator-Queries ohne Yandex (siehe EXTRA_ENGINE)."""
    return GENERAL_ENGINES if _OPERATOR_RE.search(q) else f"{GENERAL_ENGINES},{EXTRA_ENGINE}"


# Folgeseiten parallel holen (num=100 braucht ~10 Seiten), klein gehalten fuer SearXNG.
_PAGE_POOL = ThreadPoolExecutor(max_workers=6, thread_name_prefix="serper-page")
_BATCH_POOL = ThreadPoolExecutor(max_workers=4, thread_name_prefix="serper-batch")


def _collect(p: Dict, news: bool) -> Tuple[List[Dict], Dict]:
    """Treffer ab Offset (page-1)*num sammeln. Rueckgabe: (treffer, erste Seite).

    Allgemeine Suche im Rerank-Fenster: ab Platz 1 sammeln, eine Seite mehr als noetig
    (mehr Kandidaten), neu sortieren (_reorder), dann erst den Ausschnitt schneiden.
    So bleiben page=1/2/3 mit num=10 untereinander konsistent."""
    offset = (p["page"] - 1) * p["num"]
    reorder = not news and offset + p["num"] <= RERANK_WINDOW
    first = 1 if reorder else offset // 10 + 1
    if first > MAX_UPSTREAM_PAGES:
        return [], {}
    skip = offset - (first - 1) * 10
    # Extraseite nur ohne Yandex: mit Yandex reichen die Kandidaten schon (~30 je Seite).
    extra = 1 if reorder and not news and _engines(p["q"]) == GENERAL_ENGINES else 0
    # Stichwort-Suchen ueber Platz 10 hinaus: auch mit Yandex eine Seite mehr (Yahoo/Yandex
    # Seite 3), dort stehen Anbieter-Seiten, die der globale Engine-Rang (_base_key) nach
    # vorn holen kann. tool_objective serper dev (offline): hist_site30 0.135 -> 0.153.
    if reorder and offset + p["num"] > 10 and not is_question(p["q"]):
        extra = 1
    need_pages = min(-(-(skip + p["num"]) // 10) + extra, MAX_UPSTREAM_PAGES - first + 1)
    pagenos = list(range(first, first + need_pages))
    datas = list(_PAGE_POOL.map(lambda n: _upstream(p, n, news), pagenos))
    results: List[Dict] = []
    seen = set()

    def add(items: List[Dict], pageno: int) -> int:
        added = 0
        for r in items:
            link = clean_url((r.get("url") or "").strip())
            key = dup_key(link)
            if not link or key in seen:
                continue
            seen.add(key)
            results.append(dict(r, url=link, _page=pageno))
            added += 1
        return added

    for n, d in zip(pagenos, datas):
        add(d.get("results") or [], n)
    # Liefern Engines weniger als 10 je Seite, einzeln nachladen, bis num reicht.
    nxt = pagenos[-1] + 1
    last = min(MAX_UPSTREAM_PAGES, pagenos[-1] + REFILL_PAGES)
    while len(results) < skip + p["num"] and nxt <= last:
        if not add(_upstream(p, nxt, news).get("results") or [], nxt):
            break
        nxt += 1
    if reorder:
        results = _reorder(p["q"], results)
    return results[skip:skip + p["num"]], datas[0] if datas else {}


def _engine_positions(r: Dict) -> Dict[str, int]:
    engines = r.get("engines") or [r.get("engine") or ""]
    positions = r.get("positions") or [10] * len(engines)
    return {e: max(int(pos), 1) for e, pos in zip(engines, positions)}


def _base_order(results: List[Dict], trust: Optional[Dict[str, float]] = None) -> List[Dict]:
    """Engine-Rang ohne Yandex: global (Seite 2 ab Platz 11) nach Summe 1/Position von Bing/Yahoo/
    Wikipedia, jede Engine mit ihrem Vertrauen fuer diese Anfrage gewichtet (trust,
    Standard 1). Yandex-Treffer liefern nur Kandidaten, ihren Rang setzt der Cross-Encoder
    (Bench offline: mrr 0.22 mit SearXNG-Score, 0.32 mit Yandex-Gewicht 0)."""
    return sorted(results, key=lambda r: _base_key(r, trust or {}))


def _base_key(r: Dict, trust: Dict[str, float]) -> float:
    """Globaler Engine-Rang: Platz 1 auf SearXNG-Seite 2 zaehlt wie Platz 11 (wie
    rerank.fuse_engine_rank). Vorher galt Seite vor Score, dann standen reine Yandex-
    Treffer (Score 0) von Seite 1 vor Yahoo-Platz 1 von Seite 2."""
    off = (r.get("_page", 1) - 1) * PAGE_SIZE
    return -sum(trust.get(e, 1.0) / (pos + off)
                for e, pos in _engine_positions(r).items() if e != EXTRA_ENGINE)


def _ce_scores(query: str, results: List[Dict]) -> Optional[List[float]]:
    """Cross-Encoder-Logits je Treffer (Titel + Snippet), None wenn der Reranker fehlt."""
    try:
        ranked = rerank_mod.rerank(query, [
            {"title": r.get("title") or "", "content": r.get("content") or "", "_i": i}
            for i, r in enumerate(results)
        ])
    except Exception as exc:  # torch/Modell fehlt: rohe SearXNG-Reihenfolge behalten
        log.warning("Serper-Kompat: Rerank aus (%s)", type(exc).__name__)
        return None
    scores = [0.0] * len(results)
    for ch in ranked:
        scores[ch["_i"]] = ch["raw_score"]
    return _quality_adjust(query, results, scores)


def _quality_adjust(query: str, results: List[Dict], scores: List[float]) -> List[float]:
    """QUALITY_PRIOR (app/quality.py) wie im Kern: SEO-/Antwortfarm-Abzug auf den Logit,
    nur fuer Quellen ohne festen Quellen-Prior. Nur mit QUALITY_PRIOR_COMPAT=1 (Standard aus)."""
    settings = get_settings()
    if not (settings.quality_prior and settings.quality_prior_compat) or settings.quality_prior_weight <= 0:
        return scores
    weight = settings.quality_prior_weight
    if not quality_mod.is_question(query):
        return scores
    out = []
    for r, s in zip(results, scores):
        url = r.get("url") or ""
        if url and rerank_mod.source_prior(url) == 0.0:
            s += quality_mod.seo_penalty(url, r.get("title") or "", query, weight)
        out.append(s)
    return out


_QUESTION_WORDS = {
    "who", "what", "when", "where", "which", "why", "how", "whom", "whose", "is", "are", "does", "do", "did", "can",
    "wer", "was", "wann", "wo", "welche", "welcher", "welches", "welchen", "warum", "wie", "wieso", "weshalb", "wem",
    "wen", "wessen", "woher", "wohin", "ist", "sind", "hat", "gibt",
    "quién", "quien", "qué", "que", "cuándo", "cuando", "dónde", "donde", "cuál", "cual", "cuáles", "cuánto",
    "cuántos", "cuántas", "por", "cómo", "como",
    "qui", "quoi", "quand", "où", "quel", "quelle", "quels", "quelles", "pourquoi", "comment", "combien", "est-ce",
}


def is_question(q: str) -> bool:
    """Natuerlichsprachige Frage (Fragezeichen oder Fragewort vorn, de/en/es/fr)?"""
    words = q.strip().lower().lstrip("¿¡").split()
    return q.rstrip().endswith("?") or bool(words and words[0].strip(",") in _QUESTION_WORDS)


def _reorder(query: str, results: List[Dict]) -> List[Dict]:
    """SERP neu sortieren: RRF aus Engine-Rang (mit Engine-Vertrauen, _base_order) und
    Cross-Encoder-Rang, danach je Host erst ein Treffer, Wiederholungen dahinter
    (Domain-Vielfalt). Gemessen gegen die Historie (eval/history_bench.py): mrr 0.16 -> ~0.29."""
    if len(results) < 2:
        return _base_order(results)
    scores = _ce_scores(query, results)
    if scores is None:
        return _base_order(results)
    ranks = [_engine_positions(r) for r in results]
    trust = rerank_mod.engine_trust(results, ranks, scores, TRUST_MIN)
    order = sorted(range(len(results)), key=lambda i: _base_key(results[i], trust))
    results = [results[i] for i in order]
    scores = [scores[i] for i in order]
    by_ce = sorted(range(len(results)), key=lambda i: -scores[i])
    ce_rank = {i: r for r, i in enumerate(by_ce)}
    question = is_question(query)
    w = ENGINE_WEIGHT if question else EXPLORE_WEIGHT
    fused = sorted(range(len(results)),
                   key=lambda i: -(1 / (RRF_K + ce_rank[i]) + w / (RRF_K + i)))
    head, tail, weak, seen = [], [], [], {}
    for i in fused:
        if scores[i] < WEAK_CE:  # themenfremd (Woerterbuch, Shop mit gleichem Kuerzel): ans Ende
            weak.append(results[i])
            continue
        host = urlparse(results[i]["url"]).hostname
        n = seen.get(host, 0)
        seen[host] = n + 1
        keep = n == 0 or (not question and n == 1 and scores[i] >= DUP_CE_MIN)
        (head if keep else tail).append(results[i])
    return head + tail + weak


# ---------------------------------------------------------------- Umformung


def dup_key(url: str) -> str:
    """Dubletten-Schluessel: http/https, www. und Schraegstrich am Ende egal, Query
    bleibt (andere Parameter = andere Seite). Bench 13.10.2026: dieselbe Seite kam
    von zwei Engines in zwei Schreibweisen doppelt in organic."""
    try:
        p = urlparse(url)
    except ValueError:
        return url
    host = p.netloc.lower().removeprefix("www.")
    return f"{host}{p.path.rstrip('/') or '/'}?{p.query}"


def clean_url(url: str) -> str:
    """Tracking-Parameter entfernen, alles andere (Pfad, Fragment, Reihenfolge) behalten."""
    try:
        parts = urlparse(url)
    except ValueError:
        return url
    if not parts.query:
        return url
    kept = [
        (k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not (k.lower().startswith(_TRACKING_PREFIXES) or k.lower() in _TRACKING_KEYS)
    ]
    return urlunparse(parts._replace(query=urlencode(kept, doseq=True)))


def iso_date(value: Any) -> Optional[str]:
    """SearXNG-publishedDate -> "YYYY-MM-DD" (Serper liefert Freitext wie "2 days ago")."""
    if not value:
        return None
    raw = str(value).strip()
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        m = re.match(r"^(\d{4}-\d{2}-\d{2})", raw)
        return m.group(1) if m else None


def knowledge_graph(infoboxes: List[Dict]) -> Optional[Dict]:
    """Erste SearXNG-Infobox (meist Wikipedia) -> Serper-knowledgeGraph."""
    if not infoboxes:
        return None
    ib = infoboxes[0]
    title = (ib.get("infobox") or "").strip()
    if not title:
        return None
    urls = ib.get("urls") or []
    link = ib.get("id") or next((u.get("url") for u in urls if u.get("url")), "")
    kg: Dict = {"title": title}
    website = next(
        (u.get("url") for u in urls
         if u.get("url") and "wikipedia.org" not in u["url"] and "wikidata.org" not in u["url"]),
        None,
    )
    if website:
        kg["website"] = website
    if ib.get("img_src"):
        kg["imageUrl"] = ib["img_src"]
    if ib.get("content"):
        kg["description"] = ib["content"].strip()
        kg["descriptionSource"] = "Wikipedia" if "wikipedia.org" in (link or "") else (ib.get("engine") or "")
        kg["descriptionLink"] = link
    attrs = {a.get("label"): a.get("value") for a in ib.get("attributes") or [] if a.get("label")}
    if attrs:
        kg["attributes"] = attrs
    return kg


def _organic(results: List[Dict], offset: int) -> List[Dict]:
    out = []
    for r in results:
        item: Dict = {
            "title": (r.get("title") or "").strip(),
            "link": r["url"],
            "snippet": (r.get("content") or "").strip(),
            "position": offset + len(out) + 1,
        }
        date = iso_date(r.get("publishedDate"))
        if date:
            item["date"] = date
        out.append(item)
    return out


def _news_items(results: List[Dict], offset: int) -> List[Dict]:
    out = []
    for r in results:
        item: Dict = {
            "title": (r.get("title") or "").strip(),
            "link": r["url"],
            "snippet": (r.get("content") or "").strip(),
            "source": (urlparse(r["url"]).hostname or "").removeprefix("www."),
            "position": offset + len(out) + 1,
        }
        date = iso_date(r.get("publishedDate"))
        if date:
            item["date"] = date
        if r.get("thumbnail") or r.get("img_src"):
            item["imageUrl"] = r.get("thumbnail") or r.get("img_src")
        out.append(item)
    return out


def _attach_links(items: List[Dict]) -> None:
    """extras.links: Top-3 ueber app.pages laden (Cache, SSRF-Schutz) und Links anhaengen."""
    top = items[:LINK_EXTRAS_TOP]
    if not top:
        return
    docs, _failed = pages_mod.read_pages([it["link"] for it in top], "text")
    by_url = {d.get("url"): d for d in docs}
    for it in top:
        doc = by_url.get(it["link"])
        it["links"] = (doc.get("links") or []) if doc else []


# ---------------------------------------------------------------- Cache

_CACHE: "OrderedDict[str, Tuple[float, Dict]]" = OrderedDict()
_CACHE_LOCK = threading.Lock()
_CACHE_MAX = 2000


def _ttl(p: Dict, news: bool) -> int:
    """Wie app.search.web_search: News und Zeitfilter nur ein Zehntel der Frist."""
    ttl = get_settings().search_cache_ttl_s
    return ttl // 10 if (news or p["tbs"]) else ttl


def _cache_get(key: str, ttl: int) -> Optional[Dict]:
    if ttl <= 0:
        return None
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and time.monotonic() - hit[0] < ttl:
            _CACHE.move_to_end(key)
            return hit[1]
    return None


def _cache_put(key: str, value: Dict, ttl: int) -> None:
    if ttl <= 0:
        return
    with _CACHE_LOCK:
        _CACHE.pop(key, None)
        _CACHE[key] = (time.monotonic(), value)
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)


# ---------------------------------------------------------------- Kern


def run_query(body: Any, kind: str = "search") -> Dict:
    """Ein Serper-Body -> Serper-Antwort. kind: "search" oder "news"."""
    p = parse_body(body)
    news = kind == "news"
    ttl = _ttl(p, news)
    key = json.dumps([kind, {k: v for k, v in p.items() if k != "links"}], sort_keys=True)
    base = _cache_get(key, ttl)
    if base is None:
        base = _build(p, news)
        has_hits = bool(base.get("news" if news else "organic"))
        if has_hits:  # leere Listen (Engines blocken) nicht festhalten
            _cache_put(key, base, ttl)
    out = json.loads(json.dumps(base))  # Kopie: extras duerfen den Cache nicht veraendern
    if p["links"]:
        _attach_links(out.get("news" if news else "organic") or [])
    return out


_NEWS_OFF_UNTIL = 0.0
_NEWS_OFF_S = 600


def _news_disabled() -> bool:
    return time.monotonic() < _NEWS_OFF_UNTIL


def _mark_news_disabled() -> None:
    global _NEWS_OFF_UNTIL
    _NEWS_OFF_UNTIL = time.monotonic() + _NEWS_OFF_S


def _build(p: Dict, news: bool) -> Dict:
    offset = (p["page"] - 1) * p["num"]
    params: Dict = {
        "q": p["q"],
        "gl": p["gl"] or "us",
        "hl": p["hl"] or "en",
        "type": "news" if news else "search",
        "num": p["num"],
        "page": p["page"],
        "engine": "searxng",
    }
    if p["tbs"]:
        params["tbs"] = p["tbs"]
    if p["language"]:
        params["language"] = p["language"]  # eigenes Feld: was wirklich an SearXNG ging
    out: Dict = {"searchParameters": params}

    results, first = ([], {}) if news and _news_disabled() else _collect(p, news)
    if news:
        # SearXNG faellt ohne News-Engine still auf allgemeine Treffer zurueck:
        # nur echte News-Treffer zaehlen. Abschalten (10 Minuten, fuer alle) nur,
        # wenn Treffer kamen, aber keiner aus der News-Kategorie: Dann fehlt die
        # News-Engine. Ein Nischenthema ohne Treffer schaltet nichts ab.
        raw_count = len(results)
        results = [r for r in results if r.get("category", "news") == "news"]
        if raw_count and not results:
            _mark_news_disabled()
    if news and not results:
        # Keine News-Engine aktiv (deploy/searxng/settings.yml): allgemeine Suche mit
        # Zeitfenster als Ersatz, offen gekennzeichnet.
        fallback = dict(p, tbs=p["tbs"] or "qdr:m")
        results, first = _collect(fallback, False)
        params["fallback"] = "general"
    if news:
        out["news"] = _news_items(results, offset)
        out["credits"] = 0
        return out

    kg = knowledge_graph(first.get("infoboxes") or [])
    if kg:
        out["knowledgeGraph"] = kg
    answers = first.get("answers") or []
    if answers:
        a = answers[0]
        text = a.get("answer") if isinstance(a, dict) else str(a)
        if text:
            out["answerBox"] = {"answer": text}
            if isinstance(a, dict) and a.get("url"):
                out["answerBox"]["link"] = a["url"]
    out["organic"] = _organic(results, offset)
    out["peopleAlsoAsk"] = []  # Bing/Yahoo liefern es ueber SearXNG nicht
    suggestions = first.get("suggestions") or []
    if suggestions:
        out["relatedSearches"] = [{"query": s} for s in suggestions[:8]]
    out["credits"] = 0  # kostenlos
    return out


def _max_pages(p: Dict) -> int:
    """Obergrenze der SearXNG-Seiten, die _collect fuer eine Anfrage holen kann
    (Folgeseiten, Rerank-Fenster ab Platz 1, Extraseite, Nachladen)."""
    offset = (p["page"] - 1) * p["num"]
    span = offset + p["num"] if offset + p["num"] <= RERANK_WINDOW else offset % 10 + p["num"]
    return min(-(-span // 10) + 1 + REFILL_PAGES, MAX_UPSTREAM_PAGES)


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"message": message, "statusCode": status}, status_code=status)


def _handle(request: Request, body: Any, kind: str):
    try:
        require_key(request)  # Serper-Clients senden X-API-KEY
    except HTTPException as exc:
        return _error(exc.status_code, "Unauthorized.")
    try:
        if isinstance(body, list):
            if not body or len(body) > MAX_BATCH:
                raise BadRequest(f"Batch must contain 1..{MAX_BATCH} queries")
            # erst alle pruefen, dann suchen: kein halber Batch
            parsed = [parse_body(b) for b in body]
            # Upstream schonen: Summe der SearXNG-Seiten je Request begrenzen, sonst
            # loest ein Batch hunderte Anfragen aus und provoziert CAPTCHA-Sperren.
            pages = sum(_max_pages(q) for q in parsed)
            if pages > MAX_BATCH_UPSTREAM_PAGES:
                raise BadRequest(
                    f"Batch too large: needs ~{pages} upstream pages, max {MAX_BATCH_UPSTREAM_PAGES}"
                )
            return list(_BATCH_POOL.map(lambda b: run_query(b, kind), body))
        return run_query(body, kind)
    except BadRequest as exc:
        return _error(400, str(exc))
    except search_mod.SearchBackendError:
        # Details nur ins Server-Log, nie zum Client.
        log.warning("Serper-Kompat: SearXNG nicht verfuegbar")
        return _error(502, "Search backend unavailable.")


@router.post("/serper/search")
def serper_search(request: Request, body: Any = Body(...)):
    """Wie google.serper.dev/search; ein Array als Body ergibt ein Array (Batch)."""
    return _handle(request, body, "search")


@router.post("/serper/news")
def serper_news(request: Request, body: Any = Body(...)):
    """Wie google.serper.dev/news (SearXNG categories=news, sonst Zeitfenster-Ersatz)."""
    return _handle(request, body, "news")
