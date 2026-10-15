"""Quellenqualitaet ohne Domain-Liste: erkennt SEO-/Kommerzseiten an Mustern.

Nur Merkmale aus URL, Titel und Frage (keine festen Domains), damit ein
Blindtest nicht zirkulaer misst. Jedes Merkmal ist 0/1 und einzeln erklaerbar;
die Gewichte kommen aus einer Kreuzvalidierung gegen Richter-Urteile
(180 Treffer, Typ seo_kommerz) und sind gerundet. seo_penalty() liefert einen
kleinen Abzug fuer den Logit im Reranking (app/rerank.py).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Callable, Dict, List, Optional
from urllib.parse import unquote, urlparse

_Q_WORDS = (
    # de
    "wie", "was", "wann", "wer", "wo", "warum", "welche", "welcher", "welches", "wieviel",
    # en
    "how", "what", "when", "who", "why", "which", "where", "is", "are", "does", "do", "can",
    # es / fr
    "que", "quien", "como", "cuando", "donde", "cual", "quel", "quelle", "comment", "pourquoi", "qui",
)
_Q_START = re.compile(r"^(?:%s)(?:[-_]|$)" % "|".join(_Q_WORDS))
_SEO_PATH = re.compile(
    r"/(?:blogs?|glossar|glossary|lexika|magazin|frage-und-antwort|fragen?|questions?|conversions?|"
    r"snippets|tutorials|answers?|faq)(?:/|$)"
)
_CLICKBAIT = re.compile(
    r"(?:\bexplained\b|\bexact\b|\bfull (?:chart|guide)\b|\bultimate\b|\bcomprehensive guide\b|"
    r"\beverything you need\b|\bwhat it is\b|\bhow it works\b|\bwhy it matters\b|\b(?:19|20)\d\d\)|"
    r"\(\s*(?:19|20)\d\d\s*\)|\beinfach erkl|\bauf einen blick\b|\balles was\b|\btipps\b|"
    r"\binfos? und tipps\b|\bdefinition,|\bwhat happened\b|\bstructure explained\b)",
    re.IGNORECASE,
)
# Blickfang-Zeichen im Titel (≡ Liste, ᐅ Angebot, ★ ...): bekannter SEO-Kniff fuer mehr
# Klicks in der Trefferliste. Anfuehrungszeichen (» «) und Pfeile als Trenner zaehlen nicht.
_TITLE_SYMBOL = re.compile(r"[≡★⭐ᐅ✅✓✔►▶➤⇒【】]")
_NEW_TLD = (".io", ".ai", ".dev", ".co", ".info", ".xyz", ".site", ".online", ".blog", ".app",
            ".me", ".biz", ".top", ".net", ".guide", ".wiki", ".club", ".tech", ".space", ".website",
            ".store", ".shop", ".page", ".one", ".today", ".world", ".zone", ".fyi", ".tips", ".how")
# Sprach-Subdomain (de.example.com): typisch fuer maschinell uebersetzte Antwort-Farmen.
# Nachschlagewerke mit Sprach-Subdomain (Wikipedia) haben einen festen Quellen-Prior
# und werden hier gar nicht bewertet.
_LANG_SUB = {"de", "en", "es", "fr", "it", "nl", "pt", "pl", "ru", "tr", "sv", "da", "fi", "no",
             "cs", "ja", "ko", "zh", "ar"}
_SLD2 = ("co.uk", "org.uk", "ac.uk", "com.au", "co.at", "or.at", "gv.at", "com.br", "co.jp")


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.replace("ß", "ss")


def _label(host: str) -> str:
    """Registrierter Name ohne TLD (bundesland24 aus www.bundesland24.de)."""
    parts = host.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in _SLD2:
        return parts[-3]
    return parts[-2] if len(parts) >= 2 else host


def features(url: str, title: str = "", query: str = "") -> Dict[str, int]:
    low = unquote(url or "").lower()
    p = urlparse(low)
    host = (p.hostname or "").removeprefix("www.")
    label = _label(host)
    segs = [s for s in p.path.split("/") if s]
    last = re.sub(r"\.(?:html?|php|aspx?)$", "", segs[-1]) if segs else ""
    t = (title or "").strip()
    q_tokens = {w for w in re.findall(r"\w+", _fold(query)) if len(w) >= 5}
    title_tokens = set(re.findall(r"\w+", _fold(t)))
    return {
        # /wie-hoch-ist-die-zugspitze, /how-many-bones-...
        "slug_question": int(bool(_Q_START.match(last)) and last.count("-") >= 2),
        # WordPress-Muster: ein einziges langes Pfadsegment direkt unter der Domain
        "root_slug": int(len(segs) == 1 and last.count("-") >= 2),
        # Doku-/Wiki-Pfade ausgenommen (MDN /docs/Glossary/ ist Referenz, kein SEO)
        "seo_path": int(bool(_SEO_PATH.search(p.path)) and not re.search(r"/(?:docs|wiki)/", p.path)),
        "digit_domain": int(bool(re.search(r"\d", label))),
        "new_tld": int(host.endswith(_NEW_TLD)),
        "lang_subdomain": int(len(host.split(".")) >= 3 and host.split(".")[0] in _LANG_SUB),
        "title_question": int(t.endswith("?") or "?" in t.split(" - ")[0].split(" | ")[0]),
        "title_clickbait": int(bool(_CLICKBAIT.search(t))),
        "title_symbol": int(bool(_TITLE_SYMBOL.search(t))),
        # Titel wiederholt fast die ganze Frage (Antwort-Farmen)
        "title_echo": int(len(q_tokens) >= 2 and len(q_tokens & title_tokens) >= 0.8 * len(q_tokens)),
        # Domainname enthaelt ein Fragewort (exact-match domain)
        "emd": int(any(w in label.replace("-", "") for w in q_tokens)),
    }


# Gewichte (logistische Regression auf Typ seo_kommerz, nur Treffer ohne
# festen Quellen-Prior, 5-fach nach Frage gruppiert, gerundet, nur positive).
WEIGHTS: Dict[str, float] = {
    "slug_question": 1.0,
    "root_slug": 2.0,
    "seo_path": 1.5,
    "digit_domain": 0.5,
    "new_tld": 0.6,
    "title_clickbait": 0.8,
    # 14.10.2026 von Hand gesetzt (nicht aus der Regression), Begruendung je Merkmal oben;
    # gegen den unabhaengigen Werbe-/Affiliate-Marker (eval/seo_share.py) geprueft
    "lang_subdomain": 0.6,
    "title_symbol": 0.8,
    # gemessen, aber ohne Gewicht (Kreuzvalidierung: kein Gewinn bzw. schadet)
    "title_question": 0.0,
    "title_echo": 0.0,
    "emd": 0.0,
}


def seo_score(url: str, title: str = "", query: str = "") -> float:
    """Summe der Gewichte der zutreffenden Merkmale (0 = unauffaellig)."""
    f = features(url, title, query)
    return sum(WEIGHTS.get(k, 0.0) * v for k, v in f.items())


def is_question(query: str) -> bool:
    """Faktenfrage in Satzform (Fragezeichen oder Fragewort vorn)."""
    q = _fold(query or "").strip()
    if q.endswith("?"):
        return True
    first = re.split(r"[\s\-_]+", q, maxsplit=1)[0] if q else ""
    return first in _Q_WORDS and first not in ("is", "are", "do", "does", "can")


PENALTY_CAP = 2.0
# Ab diesem seo_score gilt eine Seite fuer demote_farms als Antwort-Farm (ein Merkmal reicht).
FARM_MIN_SCORE = 0.5


def seo_penalty(url: str, title: str, query: str, weight: float, cap: Optional[float] = PENALTY_CAP) -> float:
    """Abzug fuer den Reranker-Logit: -weight * min(seo_score, cap)."""
    # Nur bei Fragen in Satzform: Dort konkurrieren Antwort-Farmen mit echten
    # Quellen. Bei Stichwort-Recherchen (Events, Programme) sind Slug-Seiten oft
    # genau das Gesuchte (gemessen: hist_mrr 0.257 -> 0.248 ohne diese Sperre).
    if weight <= 0 or not is_question(query):
        return 0.0
    s = seo_score(url, title, query)
    if cap is not None:
        s = min(s, cap)
    return -weight * s


def demote_farms(
    results: List[Dict], query: str, is_flagged: Callable[[Dict], bool], ce: Callable[[Dict], float],
    floor: float = 0.0,
) -> List[Dict]:
    """Hoechstens eine auffaellige Seite oben: nur auf Platz 1 darf eine SEO-/Antwortfarm-
    Seite stehen (dort sind sich Cross-Encoder und Engines am sichersten, und sie traegt oft
    die Antwort). Weitere markierte Seiten rutschen hinter die unauffaelligen Treffer, aber
    nie hinter themenfremde (Cross-Encoder-Logit < floor). Nichts wird entfernt.

    Grund: Der Logit-Abzug allein reicht nicht, weil Antwort-Farmen die Frage woertlich
    wiederholen (Logit 2-7 ueber Wikipedia) und die Engine-Fusion sie wieder hochzieht.
    Nur bei Fragen in Satzform, wie seo_penalty."""
    if not is_question(query) or len(results) < 2:
        return results
    out: List[Dict] = []
    held: List[Dict] = []
    for i, r in enumerate(results):
        flagged = is_flagged(r)
        if flagged and i > 0:
            held.append(r)
            continue
        if not flagged and held and ce(r) < floor:
            out.extend(held)
            held = []
        out.append(r)
    return out + held
