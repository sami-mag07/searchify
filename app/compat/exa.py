"""Exa-kompatible Endpunkte (clean-room nach der oeffentlichen Exa-Doku).

POST /exa/search       Suche mit optionalen Inhalten (text, highlights, summary, extras)
POST /exa/contents     Inhalte fuer bekannte URLs (ids|urls), plus statuses je URL
POST /exa/findSimilar  Seite lesen, daraus eine Anfrage bauen, aehnliche Seiten finden

Exa hat einen eigenen neuralen Index. Wir nicht: Gesucht wird ueber das
Such-Backend (app.search / app.stream ueber app.main.run_search), gerankt mit dem
lokalen Cross-Encoder. Darum meldet `resolvedSearchType` ehrlich "keyword" oder
"neural-rerank" und `costDollars.total` ist 0 (lokal, kostenlos).

Mehrsprachig (en, de, es, fr): `language` wird an das Such-Backend gereicht,
Highlights entstehen aus Saetzen, deren Zerlegung Satzzeichen und Abkuerzungen
aller vier Sprachen kennt (¿ ¡ « » „ “, z. B., p. ej., p. ex., e.g. ...).
Spezifikation und Abweichungen: research/15-exa-spec.md.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import urlparse

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .. import answer as answer_mod
from .. import lang as lang_mod
from .. import pages as pages_mod
from .. import rerank as rerank_mod
from .. import search as search_mod
from ..auth import require_key
from ..config import get_settings
from ..schema import SearchRequest

log = logging.getLogger("rag-search.exa")

router = APIRouter()

MAX_RESULTS = 25
# Obergrenze fuer Suche + Nachladen der Volltexte (s) und Mindest-Wartezeit danach.
TOTAL_BUDGET_S = 2.4
MIN_DOC_WAIT_S = 0.3
LANGUAGES = ("en", "de", "es", "fr")
DEFAULT_SENTENCES = 3  # Saetze je Highlight
DEFAULT_PER_URL = 2  # Highlights je Ergebnis
BM25_KEEP = 8  # Satzfenster je Seite, die in den Cross-Encoder gehen
SEED_OVERLAP = 0.6  # Anteil der Abschnitt-Woerter, die ein Fenster abdecken muss
POOL_EXTRA = 10  # zusaetzliche Kandidaten, damit die Domain-Begrenzung auffuellen kann
POOL_MAX = 30
LEAD_CHARS = 400  # Quellen-Anriss fuer den findSimilar-Reranker (Tokenizer-Grenze)
DESC_MIN_CHARS = 200  # ab hier gilt eine Meta-Beschreibung als Fliesstext
PER_DOMAIN = 2  # hoechstens so viele Treffer je Domain vorne, der Rest rueckt nach hinten

# Eigener Pool fuer das Nachladen der Seiten-Metadaten (Datum, Autor, Volltext).
# read_pages wartet selbst auf den Extraktions-Pool, darum getrennt davon.
_META_POOL = ThreadPoolExecutor(max_workers=48, thread_name_prefix="exa-meta")


# ---------------------------------------------------------------------------
# Request-Modelle (Feldnamen wie in der Exa-Doku, unbekannte Felder ignoriert)
# ---------------------------------------------------------------------------


class _Loose(BaseModel):
    model_config = ConfigDict(extra="ignore")


class TextOptions(_Loose):
    maxCharacters: Optional[int] = Field(None, ge=1)
    includeHtmlTags: Optional[bool] = None


class HighlightOptions(_Loose):
    query: Optional[str] = None
    numSentences: Optional[int] = Field(None, ge=1, le=10)
    highlightsPerUrl: Optional[int] = Field(None, ge=1, le=10)
    maxCharacters: Optional[int] = Field(None, ge=1)


class SummaryOptions(_Loose):
    query: Optional[str] = None


class ExtrasOptions(_Loose):
    # Exa: Anzahl Links je Seite. true wird als "alle (bis 100)" verstanden.
    links: Optional[Union[bool, int]] = None


class ContentsOptions(_Loose):
    text: Optional[Union[bool, TextOptions]] = None
    highlights: Optional[Union[bool, HighlightOptions]] = None
    summary: Optional[Union[bool, SummaryOptions]] = None
    extras: Optional[ExtrasOptions] = None
    context: Optional[Union[bool, Dict[str, Any]]] = None


class _ContentsFields(_Loose):
    """Gemeinsame Inhalts-Felder: `contents` und die alten Top-Level-Felder."""

    contents: Optional[ContentsOptions] = None
    text: Optional[Union[bool, TextOptions]] = None
    highlights: Optional[Union[bool, HighlightOptions]] = None
    summary: Optional[Union[bool, SummaryOptions]] = None
    extras: Optional[ExtrasOptions] = None
    context: Optional[Union[bool, Dict[str, Any]]] = None
    language: Optional[str] = Field(None, description="Eigenes Feld: en | de | es | fr, sonst automatisch.")

    def options(self) -> ContentsOptions:
        c = self.contents or ContentsOptions()
        return ContentsOptions(
            text=c.text if c.text is not None else self.text,
            highlights=c.highlights if c.highlights is not None else self.highlights,
            summary=c.summary if c.summary is not None else self.summary,
            extras=c.extras or self.extras,
            context=c.context if c.context is not None else self.context,
        )


class _Filters(_ContentsFields):
    numResults: Optional[int] = 10
    includeDomains: Optional[List[str]] = None
    excludeDomains: Optional[List[str]] = None
    startPublishedDate: Optional[str] = None
    endPublishedDate: Optional[str] = None
    includeText: Optional[List[str]] = None
    excludeText: Optional[List[str]] = None
    category: Optional[str] = None
    userLocation: Optional[Union[str, Dict[str, Any]]] = None


class ExaSearchRequest(_Filters):
    query: str = Field(..., min_length=1)
    type: Optional[str] = "auto"


class ExaContentsRequest(_ContentsFields):
    ids: Optional[List[str]] = None
    urls: Optional[List[str]] = None


class ExaFindSimilarRequest(_Filters):
    url: str = Field(..., min_length=1)
    excludeSourceDomain: Optional[bool] = False


# ---------------------------------------------------------------------------
# Hilfen: Sprache, Domains, Datum
# ---------------------------------------------------------------------------


def _language(value: Optional[str]) -> Optional[str]:
    """en|de|es|fr (auch "de-DE", "Deutsch" ...), alles andere = automatisch."""
    if not value:
        return None
    v = value.strip().lower()
    aliases = {"deutsch": "de", "german": "de", "english": "en", "englisch": "en",
               "espanol": "es", "español": "es", "spanish": "es", "francais": "fr",
               "français": "fr", "french": "fr"}
    v = aliases.get(v, v[:2])
    return v if v in LANGUAGES else None


def _country(loc: Optional[Union[str, Dict[str, Any]]]) -> Optional[str]:
    if isinstance(loc, dict):
        loc = loc.get("country")
    if isinstance(loc, str) and len(loc.strip()) == 2:
        return loc.strip().lower()
    return None


def _domain_rule(entry: str) -> Tuple[str, str]:
    """Exa-Domain-Eintrag -> (host, pfad-praefix). Erlaubt: example.com,
    example.com/docs, *.example.com, auch mit https:// davor."""
    e = entry.strip().lower()
    if "://" in e:
        e = e.split("://", 1)[1]
    e = e.lstrip("*.")
    host, _, path = e.partition("/")
    if host.startswith("www."):
        host = host[4:]
    return host, ("/" + path.rstrip("/")) if path else ""


def _matches(url: str, entries: List[str]) -> bool:
    p = urlparse(url)
    host = (p.hostname or "").lower()
    for entry in entries:
        d, prefix = _domain_rule(entry)
        if d and (host == d or host.endswith("." + d)) and (not prefix or p.path.lower().startswith(prefix)):
            return True
    return False


def _hosts(entries: Optional[List[str]]) -> List[str]:
    out: List[str] = []
    for e in entries or []:
        host = _domain_rule(e)[0]
        if host and host not in out:
            out.append(host)
    return out


def _parse_date(value: Optional[str]) -> Optional[date]:
    """ISO-8601 (Datum oder Zeitpunkt, auch mit Z) -> date, sonst None."""
    if not value:
        return None
    v = str(value).strip()
    try:
        return datetime.fromisoformat(v.replace("Z", "+00:00")).date()
    except ValueError:
        pass
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", v)
    if m:
        try:
            return date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            return None
    m = re.match(r"(\d{4})-(\d{2})$", v)  # trafilatura liefert manchmal nur Jahr-Monat
    if m:
        return date(int(m[1]), int(m[2]), 1)
    return None


def _iso(d: Optional[date]) -> Optional[str]:
    return f"{d.isoformat()}T00:00:00.000Z" if d else None


def _time_range(start: Optional[date]) -> Optional[str]:
    """Startdatum -> SearXNG-Zeitfenster, damit die Engines schon vorfiltern."""
    if not start:
        return None
    days = (datetime.now(timezone.utc).date() - start).days
    if days < 0:
        return "day"
    for limit, name in ((1, "day"), (7, "week"), (31, "month"), (366, "year")):
        if days <= limit:
            return name
    return None


# ---------------------------------------------------------------------------
# Saetze und Highlights (mehrsprachig)
# ---------------------------------------------------------------------------

# Abkuerzungen, nach deren Punkt kein Satz endet (klein, ohne Punkt am Ende).
_ABBREV = {
    # Deutsch
    "z.b", "z", "b", "d.h", "u.a", "bzw", "ca", "nr", "dr", "prof", "usw", "vgl", "ggf",
    "inkl", "evtl", "str", "jh", "st", "hr", "fr", "abs", "bzgl", "zb", "mio", "mrd",
    "s", "sog", "u", "o", "ä", "v", "chr", "gem", "max", "min", "tel",
    # Englisch
    "e.g", "i.e", "mr", "mrs", "ms", "vs", "etc", "inc", "ltd", "jr", "sr", "no", "co",
    "corp", "approx", "fig", "al", "dept", "u.s", "u.k",
    # Spanisch
    "sra", "srta", "dra", "ud", "uds", "pág", "págs", "núm", "aprox", "p.ej", "ej", "av",
    "avda", "dña", "d", "ee.uu", "a.c", "d.c", "cía", "lic", "ing",
    # Franzoesisch
    "m", "mme", "mlle", "mm", "p.ex", "ex", "cf", "env", "bd", "j.-c", "n°", "éd", "vol",
    "chap", "p", "pp",
}

# Kandidat fuer ein Satzende: Satzzeichen, optional schliessende Anfuehrung/Klammer
# (auch mit Leerzeichen davor wie im Franzoesischen ". »"), dann Leerraum und ein
# Satzanfang (Grossbuchstabe, Ziffer, oder oeffnendes ¿ ¡ « „ “ " ( ).
_BOUNDARY = re.compile(
    r"([.!?…]+)((?:\s?[»\"”’')\]])*)\s+(?=[¿¡«„“\"'(\[]?\s?[A-ZÀ-ÖØ-ÞÑÇ0-9])"
)
_WORD_BEFORE = re.compile(r"([\w.°\-]+)$", re.UNICODE)


def split_sentences(text: str) -> List[str]:
    """Zerlegt Text in Saetze (en/de/es/fr). Zeilenumbrueche trennen immer
    (Absaetze, Listenpunkte). Kein Satzende nach Abkuerzungen, Initialen und
    Ordnungszahlen wie "3. Oktober"."""
    out: List[str] = []
    for para in re.split(r"\n+", text or ""):
        para = re.sub(r"\s+", " ", para).strip()
        if not para:
            continue
        start = 0
        for m in _BOUNDARY.finditer(para):
            if m.group(1).startswith("."):
                before = _WORD_BEFORE.search(para[: m.start(1)])
                word = (before.group(1) if before else "").lower().rstrip(".")
                if word in _ABBREV or (len(word) == 1 and word.isalpha()):
                    continue
                if word.isdigit() and len(word) <= 2:  # "3. Oktober", "1. Platz"
                    continue
            sentence = para[start : m.end(2)].strip()
            if sentence:
                out.append(sentence)
            start = m.end()
        rest = para[start:].strip()
        if rest:
            out.append(rest)
    return out


def _windows(sentences: List[str], size: int, max_chars: int = 900) -> List[str]:
    """Gleitende Fenster aus `size` Saetzen (Schritt 1), auf max_chars gekappt."""
    if not sentences:
        return []
    size = max(1, min(size, len(sentences)))
    out = []
    for i in range(len(sentences) - size + 1):
        w = " ".join(sentences[i : i + size])
        out.append(w if len(w) <= max_chars else w[:max_chars].rsplit(" ", 1)[0] + " ...")
    return out


def _echoes(query: str, window: str) -> bool:
    """True, wenn das Fenster im Kern nur die Frage wiederholt: ein Fragesatz,
    der fast alle Woerter der Query enthaelt, und kaum mehr Text drumherum."""
    q = {w for w in re.findall(r"\w+", query.lower()) if len(w) > 2}
    if not q:
        return False
    for sent in split_sentences(window):
        if "?" not in sent:
            continue
        words = set(re.findall(r"\w+", sent.lower()))
        if len(q & words) / len(q) >= 0.8 and len(window) < 2.5 * len(sent) + 80:
            return True
    return False


def _prefilter(query: str, wins: List[str], keep: int = BM25_KEEP) -> List[int]:
    """BM25-Vorauswahl der Satzfenster fuer den Cross-Encoder, gestreut: Fenster,
    die ein schon gewaehltes um weniger als 2 Saetze verschieben, zaehlen nicht.
    Sonst belegen 3-4 Nachbarfenster derselben Stelle die Plaetze, und die Stelle
    mit der Antwort kommt gar nicht erst in den Cross-Encoder (exa_bench dev:
    erstes Highlight von Treffer 1 enthaelt die Antwort 0.84 -> 0.90)."""
    if not wins:
        return []
    lex = rerank_mod.lexical_scores(query, wins)
    out: List[int] = []
    for k in sorted(range(len(wins)), key=lambda k: (-lex[k], k)):
        if len(out) >= keep:
            break
        if all(abs(k - j) >= 2 for j in out):
            out.append(k)
    return out


_SNIPPET_DATE = re.compile(r"^\w{3} \d{1,2}, \d{4} · ")


def _seed_windows(seed: str, wins: List[str], top: int = 2) -> Tuple[List[int], List[str]]:
    """Abschnitte des Kern-Rankings (content, bester zuerst, Trenner " [...] ") als
    Highlight-Kandidaten. Deckt ein Satzfenster einen Abschnitt zu SEED_OVERLAP ab,
    kommt dieses Fenster in den Cross-Encoder; sonst (Abschnitt stammt aus dem
    Such-Snippet oder fehlt im Volltext dieses Extraktors) der Abschnitt selbst.
    exa_bench dev: Highlights von Treffer 1 enthalten die Antwort 0.88 -> 0.96."""
    if not seed:
        return [], []
    idx: List[int] = []
    extra: List[str] = []
    win_tokens = [set(rerank_mod._tokens(w)) for w in wins]
    for chunk in [c.strip() for c in seed.split(" [...] ") if c.strip()][:top]:
        words = set(rerank_mod._tokens(chunk))
        if len(words) < 5:
            continue
        ov = [len(words & wt) / len(words) for wt in win_tokens]
        best = max(range(len(wins)), key=lambda k: (ov[k], -k)) if wins else -1
        if best >= 0 and ov[best] >= SEED_OVERLAP:
            idx.append(best)
        else:
            extra.append(_SNIPPET_DATE.sub("", chunk))
    return idx, extra


def highlights_for(
    query: str,
    items: List[Dict[str, str]],
    num_sentences: int = DEFAULT_SENTENCES,
    per_url: int = DEFAULT_PER_URL,
    max_chars: Optional[int] = None,
) -> List[Tuple[List[str], List[float]]]:
    """Query-relevante Satzfenster je Item ({"title", "text", optional "seed"}).

    1. Saetze splitten, Fenster aus num_sentences Saetzen bilden.
    2. BM25 waehlt je Seite BM25_KEEP gestreute Fenster vor (_prefilter), dazu die
       Fenster bzw. Abschnitte, die das Kern-Ranking schon als beste fand (seed).
    3. Ein einziger Cross-Encoder-Lauf ueber alle Fenster aller Seiten.
    4. Je Seite die besten nicht ueberlappenden Fenster; Score = sigmoid(Logit/T).
    Rueckgabe in Item-Reihenfolge: (highlights, highlightScores).
    """
    candidates: List[Dict] = []
    for i, item in enumerate(items):
        wins = _windows(split_sentences((item.get("text") or "")[:60000]), num_sentences)
        seeded, extra = _seed_windows(item.get("seed") or "", wins)
        if not wins and not extra:
            continue
        keep = _prefilter(query, wins)
        keep = list(dict.fromkeys(keep + seeded))
        candidates += [
            {"title": item.get("title") or "", "content": wins[k], "_i": i, "_w": k, "_seed": k in seeded}
            for k in keep
        ]
        candidates += [
            {"title": item.get("title") or "", "content": t, "_i": i, "_w": -1000 * (j + 1), "_seed": True}
            for j, t in enumerate(extra)
        ]
    picked: Dict[int, List[Dict]] = {}
    ranked = rerank_mod.rerank(query, candidates) if candidates else []
    # Fenster, die nur die Frage wiederholen ("Wie hoch ist die Zugspitze?"), mag der
    # Cross-Encoder, sie enthalten aber selten die Antwort: nach hinten stellen.
    ranked = [c for c in ranked if not _echoes(query, c["content"])] + [
        c for c in ranked if _echoes(query, c["content"])
    ]
    for ch in ranked:
        chosen = picked.setdefault(ch["_i"], [])
        if len(chosen) >= per_url:
            continue
        if any(abs(ch["_w"] - c["_w"]) < num_sentences for c in chosen):
            continue  # ueberlappt ein schon gewaehltes Fenster
        chosen.append(ch)
    out: List[Tuple[List[str], List[float]]] = []
    for i in range(len(items)):
        texts, scores, used = [], [], 0
        for ch in picked.get(i, []):
            t = ch["content"]
            if max_chars is not None:
                if used >= max_chars:
                    break
                t = t[: max_chars - used]
                used += len(t)
            texts.append(t)
            scores.append(round(float(ch.get("score", 0.0)), 4))
        out.append((texts, scores))
    return out


# ---------------------------------------------------------------------------
# Sprache raten, Begriffe fuer findSimilar
# ---------------------------------------------------------------------------

_STOP = {
    "en": set("the and of to in is that for on with as are was be by this it from or an at "
              "which have has not but you your we our they their more can will all also its".split()),
    "de": set("der die das und ist in zu den von mit sich des auf fuer für nicht ein eine als "
              "auch es an werden aus er hat dass sie nach wird bei einer um am sind noch wie "
              "einem über ueber so zum zur nur oder aber vor durch".split()),
    "es": set("el la de que y en los del se las por un una para con no es su al lo como más "
              "mas pero sus le ya o este sí porque esta entre cuando muy sin sobre también".split()),
    "fr": set("le la les de des et en un une du est que pour dans qui pas sur au avec ce il "
              "par plus ne se sont aux son sa ses ou mais nous vous leur cette été être".split()),
}
_ALL_STOP = set().union(*_STOP.values())


def guess_language(text: str) -> Optional[str]:
    """Gemeinsame Spracherkennung des Dienstes (app.lang) auf dem Textanfang."""
    return lang_mod.detect((text or "")[:2000])


def _clean_title(title: str, host: str = "") -> str:
    """Seitentitel ohne Site-Anhang ("Canberra – Wikipedia" -> "Canberra").
    Teile, die im Hostnamen vorkommen, fliegen raus; vom Rest gilt der erste
    (Titel stehen fast immer vor dem Seitennamen)."""
    parts = [p.strip() for p in re.split(r"\s+[|–—·:]\s+|\s+-\s+", title or "") if p.strip()]
    host_words = set(re.findall(r"[a-z0-9]{3,}", (host or "").lower()))
    keep = [p for p in parts if re.sub(r"[^a-z0-9]", "", p.lower()) not in host_words]
    return (keep or parts or [(title or "").strip()])[0]


def similar_query(title: str, text: str, n_terms: int = 4, host: str = "") -> str:
    """Suchanfrage aus Titel + markantesten Begriffen der Seite (haeufig, lang,
    kein Stoppwort, nicht schon im Titel)."""
    base = _clean_title(title, host)
    in_title = {w.lower() for w in re.findall(r"\w+", base)}
    words = re.findall(r"[^\W\d_][\w\-]{3,}", (text or "")[:20000])
    counts = Counter(w.lower() for w in words if w.lower() not in _ALL_STOP and w.lower() not in in_title)
    # Haeufigkeit zaehlt, lange Woerter (Fachbegriffe, Komposita) leicht bevorzugt.
    ranked = sorted(counts.items(), key=lambda kv: (-(kv[1] * (1 + min(len(kv[0]), 14) / 14)), kv[0]))
    terms = [w for w, c in ranked if c >= 2][:n_terms]
    return " ".join(([base] if base else []) + terms).strip()


def _clip(text: str, limit: int) -> str:
    """Text auf hoechstens `limit` Zeichen, an einer Wortgrenze gekuerzt."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    return (cut.rsplit(" ", 1)[0] if " " in cut else cut).rstrip(" ,;:-")


def with_description(text: str, description: Optional[str]) -> str:
    """Langen Seiten-Beschreibungstext voranstellen, wenn der Haupttext ihn nicht
    enthaelt. Manche Seiten (Statistik-Portale, Datenblaetter) tragen ihren
    eigentlichen Fliesstext nur dort, die Extraktion findet dann bloss
    Tabellen- und Linkreste. Kurze SEO-Beschreibungen (< DESC_MIN_CHARS) bleiben
    draussen, ebenso Beschreibungen, deren Anfang schon im Text steht."""
    desc = re.sub(r"\s+", " ", description or "").strip()
    if len(desc) < DESC_MIN_CHARS:
        return text or ""
    flat = re.sub(r"\s+", " ", text or "")
    probe = desc[:80].rsplit(" ", 1)[0]
    if probe and probe in flat:
        return text or ""
    return f"{desc}\n\n{text}" if text else desc


def _query_from_url(url: str) -> str:
    p = urlparse(url)
    words = re.findall(r"[^\W\d_]{3,}", p.path.replace("-", " ").replace("_", " "))
    host = (p.hostname or "").removeprefix("www.").split(".")[0]
    return " ".join(words[-6:] + ([host] if host else [])).strip()


# ---------------------------------------------------------------------------
# Pipeline-Bausteine
# ---------------------------------------------------------------------------


def _run_search(req: SearchRequest):
    """Eigene Such-Pipeline (app.main.run_search). Spaeter Import, weil app.main
    diesen Router selbst einbindet."""
    from .. import main as main_mod  # noqa: PLC0415

    if get_settings().quality_prior_compat:
        return main_mod.run_search(req)
    with rerank_mod.quality_disabled():
        return main_mod.run_search(req)


def _read_docs(urls: List[str], budget_s: Optional[float]) -> Tuple[Dict[str, Dict], Dict[str, str]]:
    """Seiten-Dokumente (title, published, author, content, links) parallel lesen.
    budget_s=None: auf alle warten. Sonst bleibt nach Ablauf, was da ist;
    der Rest laedt im Hintergrund weiter und landet im Seiten-Cache."""
    if not urls:
        return {}, {}
    futures = {_META_POOL.submit(pages_mod.read_pages, [u], "text"): u for u in urls}
    wait(futures, timeout=budget_s)
    docs: Dict[str, Dict] = {}
    errors: Dict[str, str] = {}
    for fut, url in futures.items():
        if not fut.done():
            errors[url] = "Zeitlimit"
            continue
        try:
            found, failed = fut.result()
        except Exception as exc:  # noqa: BLE001
            found, failed = [], [{"url": url, "error": type(exc).__name__}]
        if found:
            docs[url] = found[0]
        else:
            errors[url] = (failed[0].get("error") if failed else None) or "unbekannt"
    return docs, errors


def _text_ok(text: str, include: Optional[List[str]], exclude: Optional[List[str]]) -> bool:
    """includeText/excludeText wie bei Exa wortweise (nicht als exakte Phrase)."""
    low = (text or "").lower()

    def has(phrase: str) -> bool:
        return all(w in low for w in re.findall(r"\w+", phrase.lower()))

    if include and not all(has(p) for p in include):
        return False
    if exclude and any(has(p) for p in exclude):
        return False
    return True


def _summaries(items: List[Dict], query: Optional[str], language: Optional[str]) -> List[Optional[str]]:
    """Kurz-Zusammenfassung je Item ueber app.answer (nur mit OPENAI_API_KEY)."""
    if not get_settings().openai_api_key:
        return [None] * len(items)
    asks = {
        "de": "Fasse den Inhalt dieser Seite in zwei bis drei Saetzen zusammen.",
        "en": "Summarize this page in two to three sentences.",
        "es": "Resume el contenido de esta página en dos o tres frases.",
        "fr": "Résume le contenu de cette page en deux ou trois phrases.",
    }

    def one(item: Dict) -> Optional[str]:
        if not item.get("text"):
            return None
        ask = query or asks.get(language or guess_language(item["text"]) or "en", asks["en"])
        src = [{"title": item.get("title"), "url": item.get("url"), "content": item["text"][:4000]}]
        try:
            text, _ = answer_mod.generate_answer(ask, src)
        except Exception:  # noqa: BLE001 - Summary ist Beiwerk
            log.warning("Summary fehlgeschlagen")
            return None
        return re.sub(r"\s*\[\d+\]", "", text or "").strip() or None

    futures = [_META_POOL.submit(one, it) for it in items]
    wait(futures, timeout=60)
    return [f.result() if f.done() and not f.exception() else None for f in futures]


def _apply_contents(
    items: List[Dict], opts: ContentsOptions, query: Optional[str], language: Optional[str]
) -> Tuple[List[Dict], Optional[str]]:
    """Baut die Exa-Ergebnisse. items: {url, title, published, author, score?,
    text, links}. Rueckgabe: (results, context-String oder None)."""
    results: List[Dict] = []
    for it in items:
        r: Dict[str, Any] = {
            "id": it["url"],
            "url": it["url"],
            "title": it.get("title") or "",
            "publishedDate": _iso(it.get("published")),
            "author": it.get("author") or None,
        }
        if it.get("score") is not None:
            r["score"] = it["score"]
        if opts.text:
            text = it.get("text") or ""
            limit = opts.text.maxCharacters if isinstance(opts.text, TextOptions) else None
            r["text"] = text[:limit] if limit else text
        results.append(r)

    if opts.highlights:
        h = opts.highlights if isinstance(opts.highlights, HighlightOptions) else HighlightOptions()
        hl_items = [{"title": it.get("title"), "text": it.get("text"), "seed": it.get("seed")} for it in items]
        hq = h.query or query
        if hq:
            picked = highlights_for(hq, hl_items, h.numSentences or DEFAULT_SENTENCES,
                                    h.highlightsPerUrl or DEFAULT_PER_URL, h.maxCharacters)
        else:
            # Ohne Query (contents): Titel + Beschreibung der Seite als Leitfrage.
            picked = []
            for it, hi in zip(items, hl_items):
                q = f"{it.get('title') or ''}. {it.get('description') or ''}".strip(". ")
                picked += highlights_for(q or it["url"], [hi], h.numSentences or DEFAULT_SENTENCES,
                                         h.highlightsPerUrl or DEFAULT_PER_URL, h.maxCharacters)
        for r, (texts, scores) in zip(results, picked):
            r["highlights"] = texts
            r["highlightScores"] = scores

    if opts.summary:
        s = opts.summary if isinstance(opts.summary, SummaryOptions) else SummaryOptions()
        for r, summ in zip(results, _summaries(items, s.query, language)):
            if summ:  # ohne LLM-Key fehlt das Feld (siehe research/15-exa-spec.md)
                r["summary"] = summ

    if opts.extras and opts.extras.links:
        n = 100 if opts.extras.links is True else int(opts.extras.links)
        for r, it in zip(results, items):
            r["extras"] = {"links": [l["url"] for l in (it.get("links") or [])][:n]}

    context = None
    if opts.context:
        limit = opts.context.get("maxCharacters") if isinstance(opts.context, dict) else None
        parts = []
        for r, it in zip(results, items):
            body = " ".join(r.get("highlights") or []) or (it.get("text") or "")[:2000]
            parts.append(f"Title: {r['title']}\nURL: {r['url']}\n{body}")
        context = "\n\n".join(parts)
        if limit:
            context = context[: int(limit)]
    return results, context


def _wants_body(opts: ContentsOptions) -> bool:
    return bool(opts.text or opts.highlights or opts.summary or opts.context or (opts.extras and opts.extras.links))


_CATEGORY_DOMAINS = {
    "github": ["github.com"],
    "tweet": ["x.com", "twitter.com"],
    "linkedin profile": ["linkedin.com"],
    "research paper": ["arxiv.org", "semanticscholar.org", "researchgate.net",
                       "ncbi.nlm.nih.gov", "sciencedirect.com", "springer.com"],
}
_CATEGORY_DOMAINS["publication"] = _CATEGORY_DOMAINS["research paper"]


def _search_and_finish(
    body: _Filters,
    query: str,
    *,
    depth: str,
    rerank_query: Optional[str] = None,
    drop_urls: Tuple[str, ...] = (),
    extra_exclude: Tuple[str, ...] = (),
    highlight_query: Optional[str] = None,
) -> Tuple[List[Dict], Optional[str]]:
    """Gemeinsamer Weg fuer /search und /findSimilar."""
    t0 = time.monotonic()
    n = max(1, min(body.numResults or 10, MAX_RESULTS))
    start, end = _parse_date(body.startPublishedDate), _parse_date(body.endPublishedDate)
    opts = body.options()
    language = _language(body.language)
    filtering = bool(start or end or body.includeText or body.excludeText)

    category = (body.category or "").strip().lower()
    include = list(body.includeDomains or [])
    if not include and category in _CATEGORY_DOMAINS:
        include = list(_CATEGORY_DOMAINS[category])
    exclude = list(body.excludeDomains or []) + list(extra_exclude)
    if category == "pdf":
        query = f"{query} filetype:pdf"

    # Mehr Kandidaten holen, wenn danach gefiltert oder neu gerankt wird.
    pool = min(POOL_MAX, max(n * 2 if (filtering or rerank_query) else n, n + POOL_EXTRA))
    if not (filtering or rerank_query):
        # Ohne Filter gilt das Kern-Ranking 1:1 (es begrenzt Domains schon selbst):
        # genau n holen. Mehr als 10 loest im Kern die breite Suche (Seiten 2-3) aus,
        # das kostet bei n=10 zwei Yahoo-Anfragen und Seitenladungen mehr, ohne dass
        # sich die Spitze aendert (exa_bench 12.10.2026: Top-1 und hist gleich).
        pool = n
    fields = dict(
        query=query,
        search_depth=depth,
        topic="news" if category == "news" else "general",
        include_answer=False,
        include_raw_content=True,
        include_domains=_hosts(include),
        exclude_domains=_hosts(exclude),
        language=language,
        country=_country(body.userLocation),
        time_range=_time_range(start) if not end else None,
    )
    req = SearchRequest(**fields, max_results=min(pool, 20))
    if pool > 20:  # SearchRequest erlaubt 20, run_search selbst kommt mit mehr klar
        req = req.model_copy(update={"max_results": pool})
    resp = _run_search(req)

    hits = []
    dropped = {rerank_mod.canonical_url(u) for u in drop_urls}
    for r in resp.results:
        if rerank_mod.canonical_url(r.url) in dropped:
            continue
        if include and not _matches(r.url, include):
            continue
        if exclude and _matches(r.url, exclude):
            continue
        hits.append(r)

    if rerank_query and hits:
        # Aehnlichkeit zur Quellseite: Cross-Encoder mit Titel+Beschreibung der Quelle.
        ranked = rerank_mod.rerank(
            rerank_query,
            [{"title": h.title, "content": (h.content or "")[:1200], "_k": k} for k, h in enumerate(hits)],
        )
        order = [c["_k"] for c in ranked]
        scores = {c["_k"]: c["score"] for c in ranked}
        hits = [hits[k].model_copy(update={"score": scores[k]}) for k in order]

    # Metadaten (Datum, Autor) und Volltext je Treffer. Mit Filter oder Inhalten
    # laenger warten, sonst nur kurz (fehlt etwas, kommt der Snippet-Stand).
    # Seiten aus run_search liegen schon im Dokument-Cache (kein zweiter Abruf);
    # gewartet wird nur auf die, die im Such-Budget nicht ankamen.
    budget = 1.5 if filtering or _wants_body(opts) else (0.3 if depth == "fast" else 0.8)
    # Gesamtfrist: die Suche hat schon bis zu FETCH_BUDGET_ADVANCED_MS auf Seiten
    # gewartet; noch einmal volle 1.5 s machten den Schwanz (live p95 3.3 s, Bench
    # 13.10.2026). Was jetzt nicht da ist, kommt als Abschnitt des Kerns; die Seite
    # laedt im Hintergrund weiter in den Cache.
    budget = min(budget, max(MIN_DOC_WAIT_S, TOTAL_BUDGET_S - (time.monotonic() - t0)))
    docs, _ = _read_docs([h.url for h in hits], budget)

    dated, undated = [], []
    for h in hits:
        doc = docs.get(h.url) or {}
        text = with_description(doc.get("content") or h.raw_content or h.content or "",
                                doc.get("description"))
        published = _parse_date(doc.get("published"))
        if not _text_ok(text, body.includeText, body.excludeText):
            continue
        item = {
            "url": h.url,
            "title": doc.get("title") or h.title,
            "published": published,
            "author": doc.get("author"),
            "description": doc.get("description"),
            "score": round(float(h.score), 4),
            "text": text,
            "seed": h.content or "",  # bester Abschnitt laut Kern-Ranking
            "links": doc.get("links") or [],
        }
        if start or end:
            if published is None:
                undated.append(item)  # Datum unbekannt: nicht wegwerfen, hinten anstellen
                continue
            if (start and published < start) or (end and published > end):
                continue
        dated.append(item)
    items = diversify(dated + undated)[:n]
    return _apply_contents(items, opts, highlight_query or query, language)


def _site(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def diversify(items: List[Dict], per_domain: int = PER_DOMAIN) -> List[Dict]:
    """Stabile Domain-Begrenzung: je Domain die ersten `per_domain` Treffer an ihrem
    Platz, weitere rutschen (in ihrer Reihenfolge) ans Ende. Nichts wird verworfen."""
    seen: Counter = Counter()
    front, back = [], []
    for it in items:
        site = _site(it["url"])
        seen[site] += 1
        (front if seen[site] <= per_domain else back).append(it)
    return front + back


def _error(status: int, message: str, tag: str, request_id: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"requestId": request_id, "error": message, "tag": tag})


def _depth(search_type: Optional[str], opts: ContentsOptions) -> str:
    t = (search_type or "auto").lower()
    if t in ("fast", "instant"):
        return "fast"
    if t.startswith("deep") or _wants_body(opts):
        return "advanced"  # Volltexte sollen moeglichst vollstaendig da sein
    return "basic"


# ---------------------------------------------------------------------------
# Endpunkte
# ---------------------------------------------------------------------------


@router.post("/exa/search")
def exa_search(body: ExaSearchRequest, request: Request):
    require_key(request)
    started = time.perf_counter()
    request_id = uuid.uuid4().hex
    if not body.query.strip():
        # Nur Leerraum ging sonst an alle Backends und endete als 502.
        return _error(400, "query darf nicht leer sein.", "INVALID_REQUEST_BODY", request_id)
    opts = body.options()
    try:
        results, context = _search_and_finish(body, body.query, depth=_depth(body.type, opts))
    except search_mod.SearchBackendError:
        log.warning("Exa-Suche: Such-Backend nicht verfuegbar")
        return _error(502, "Such-Backend nicht verfuegbar.", "SEARCH_BACKEND_UNAVAILABLE", request_id)
    out: Dict[str, Any] = {
        "requestId": request_id,
        "resolvedSearchType": "keyword" if (body.type or "").lower() == "keyword" else "neural-rerank",
        "results": results,
        "searchTime": round((time.perf_counter() - started) * 1000),
        "costDollars": {"total": 0},
    }
    if context is not None:
        out["context"] = context
    return out


_ERROR_TAGS = (
    ("Zeitlimit", "CRAWL_TIMEOUT"),
    ("zu langsam", "CRAWL_TIMEOUT"),
    ("Kein Haupttext", "CRAWL_NOT_FOUND"),
    ("nicht ladbar", "SOURCE_NOT_AVAILABLE"),
)


@router.post("/exa/contents")
def exa_contents(body: ExaContentsRequest, request: Request):
    require_key(request)
    started = time.perf_counter()
    request_id = uuid.uuid4().hex
    urls = list(dict.fromkeys(body.urls or body.ids or []))
    if not urls:
        return _error(400, "ids oder urls fehlen.", "INVALID_REQUEST_BODY", request_id)
    if len(urls) > 100:
        return _error(400, "Hoechstens 100 URLs je Anfrage.", "INVALID_REQUEST_BODY", request_id)
    opts = body.options()
    if opts.text is None and not (opts.highlights or opts.summary):
        opts.text = True  # Exa liefert bei /contents ohne Angabe den Text

    valid = [u for u in urls if urlparse(u).scheme in ("http", "https") and urlparse(u).hostname]
    docs, errors = _read_docs(valid, None)
    items, statuses = [], []
    for u in urls:
        doc = docs.get(u)
        if doc:
            items.append({
                "url": u,
                "title": doc.get("title") or "",
                "published": _parse_date(doc.get("published")),
                "author": doc.get("author"),
                "description": doc.get("description"),
                "text": with_description(doc.get("content") or "", doc.get("description")),
                "links": doc.get("links") or [],
            })
            statuses.append({"id": u, "status": "success", "source": "crawled"})
            continue
        reason = errors.get(u, "ungueltige URL")
        tag = next((t for key, t in _ERROR_TAGS if key in reason), "CRAWL_UNKNOWN_ERROR")
        if u not in valid:
            tag = "INVALID_URL"
        statuses.append({"id": u, "status": "error", "error": {"tag": tag, "httpStatusCode": None}})

    results, context = _apply_contents(items, opts, None, _language(body.language))
    out: Dict[str, Any] = {
        "requestId": request_id,
        "results": results,
        "statuses": statuses,
        "searchTime": round((time.perf_counter() - started) * 1000),
        "costDollars": {"total": 0},
    }
    if context is not None:
        out["context"] = context
    return out


@router.post("/exa/findSimilar")
def exa_find_similar(body: ExaFindSimilarRequest, request: Request):
    require_key(request)
    started = time.perf_counter()
    request_id = uuid.uuid4().hex
    src_url = body.url.strip()
    if "://" not in src_url:
        src_url = "https://" + src_url
    if not urlparse(src_url).hostname:
        return _error(400, "url ist keine gueltige Adresse.", "INVALID_REQUEST_BODY", request_id)

    host = (urlparse(src_url).hostname or "").removeprefix("www.")
    docs, _ = _read_docs([src_url], None)
    src = docs.get(src_url)
    if src:
        title = _clean_title(src.get("title") or "", host)
        query = similar_query(src.get("title") or "", src.get("content") or "", host=host)
        lead = _clip(src.get("description") or src.get("content") or "", LEAD_CHARS)
        rerank_query = f"{title}. {lead}".strip(". ")
    else:
        query = _query_from_url(src_url)  # Seite nicht lesbar: Begriffe aus der Adresse
        title, rerank_query = query, query
    if not query:
        return _error(422, "Aus der Quellseite liess sich keine Suchanfrage bauen.", "SOURCE_NOT_AVAILABLE", request_id)
    if not body.language and src:
        body = body.model_copy(update={"language": guess_language(src.get("content") or "")})

    opts = body.options()
    try:
        results, context = _search_and_finish(
            body,
            query,
            depth="advanced" if _wants_body(opts) else "basic",
            rerank_query=rerank_query,
            drop_urls=(src_url,),
            extra_exclude=(host,) if body.excludeSourceDomain else (),
            highlight_query=title or query,
        )
    except search_mod.SearchBackendError:
        log.warning("Exa-findSimilar: Such-Backend nicht verfuegbar")
        return _error(502, "Such-Backend nicht verfuegbar.", "SEARCH_BACKEND_UNAVAILABLE", request_id)
    out: Dict[str, Any] = {
        "requestId": request_id,
        "results": results,
        "searchTime": round((time.perf_counter() - started) * 1000),
        "costDollars": {"total": 0},
    }
    if context is not None:
        out["context"] = context
    return out


__all__ = ["router", "split_sentences", "highlights_for", "similar_query", "guess_language"]
