"""Themen-Bibliothek: erkennt das Thema einer Frage und holt passende Seiten aus dem Regal.

Gebaut wird sie von app/library_build.py (Pipeline, nur Funde bis zum Stichtag). Hier
laeuft nur der Lookup im heissen Pfad: Wortstaemme der Frage gegen die Stichwoerter je
Regal (kein LLM), dann BM25 ueber die Eintraege des einen Regals. Beides auf
vorberechneten Indizes, Ziel unter 1 ms. Die Treffer gehen als zusaetzliche Kandidaten
in dieselbe Pipeline wie die Live-Treffer (laden, Reranker, Fusion), die Live-Suche
laeuft immer mit.

Aus mit LIBRARY=0 oder wenn die Datei (LIBRARY_PATH, Standard data/bibliothek/regale.json)
fehlt. Die Datei wird alle 60 s auf Aenderung geprueft (Neubau ohne Neustart).
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
import unicodedata
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
_STOP = set("""
the and for with from that this you your are was were have has will can not all any how what when where
who why which into about over under more most best free online apply application
der die das und oder mit von fuer für auf aus bei ein eine einer eines einem einen ist sind wie was wer
wann wo zum zur den dem des im in an am als auch nach noch nur bis ohne ueber über unter mehr
les des une pour avec dans sur par que qui los las del para con una por que
""".split())
_STOP |= {"fur", "uber", "fuer", "ueber"}
_TOKEN = re.compile(r"[a-z0-9]{3,}")
STEM_LEN = 6
_CHECK_EVERY_S = 60.0

_LOCK = threading.Lock()
_STATE: Dict = {"path": None, "mtime": None, "checked": 0.0, "shelves": None}


def default_path() -> Path:
    return Path(os.environ.get("LIBRARY_PATH", ROOT / "data" / "bibliothek" / "regale.json"))


def _fold(text: str) -> str:
    text = text.lower().replace("ß", "ss")
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def stems(text: str) -> List[str]:
    """Wortstaemme: klein, ohne Akzente, ab 3 Zeichen, ohne Stoppwoerter und reine
    Zahlen (Jahreszahlen sagen nichts ueber das Thema), gekuerzt auf STEM_LEN."""
    out = []
    for tok in _TOKEN.findall(_fold(text)):
        if tok in _STOP or tok.isdigit():
            continue
        out.append(tok[:STEM_LEN])
    return out


class Shelf:
    """Ein Regal: Stichwoerter fuer die Erkennung und BM25-Index ueber die Eintraege."""

    def __init__(self, name: str, keywords: Dict[str, float], entries: List[Dict]):
        self.name = name
        self.keywords = keywords
        self.entries = entries
        self.postings: Dict[str, List[Tuple[int, int]]] = {}
        self.lengths: List[int] = []
        for i, e in enumerate(entries):
            toks = stems(f"{e.get('text', '')} {_url_words(e['url'])}")
            self.lengths.append(len(toks))
            for s, tf in Counter(toks).items():
                self.postings.setdefault(s, []).append((i, tf))
        self.avg_len = (sum(self.lengths) / len(self.lengths)) if self.lengths else 1.0

    def search(self, query_stems: List[str], k: int, k1: float = 1.2, b: float = 0.75) -> List[Tuple[float, Dict]]:
        n = len(self.entries)
        scores: Dict[int, float] = {}
        for s in set(query_stems):
            post = self.postings.get(s)
            if not post:
                continue
            idf = math.log(1 + (n - len(post) + 0.5) / (len(post) + 0.5))
            for i, tf in post:
                norm = tf * (k1 + 1) / (tf + k1 * (1 - b + b * self.lengths[i] / self.avg_len))
                scores[i] = scores.get(i, 0.0) + idf * norm
        best = sorted(scores.items(), key=lambda kv: (-kv[1], self.entries[kv[0]]["url"]))[:k]
        return [(sc, self.entries[i]) for i, sc in best]


def _url_words(url: str) -> str:
    return re.sub(r"[/\-_.?=&:]+", " ", url.split("://", 1)[-1])


def enabled() -> bool:
    return os.environ.get("LIBRARY", "1").strip() not in ("0", "false", "no", "")


def shelves() -> Optional[Dict[str, Shelf]]:
    """Geladene Regale (mit Neuladen bei geaenderter Datei) oder None."""
    now = time.monotonic()
    path = default_path()
    if _STATE["path"] == path and now - _STATE["checked"] < _CHECK_EVERY_S:
        return _STATE["shelves"]
    with _LOCK:
        _STATE["checked"] = now
        try:
            mtime = path.stat().st_mtime
        except OSError:
            _STATE.update(path=path, mtime=None, shelves=None)
            return None
        if _STATE["path"] == path and _STATE["mtime"] == mtime:
            return _STATE["shelves"]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            loaded = {name: Shelf(name, v["keywords"], v["eintraege"]) for name, v in data["regale"].items()}
        except (OSError, ValueError, KeyError):
            loaded = None
        _STATE.update(path=path, mtime=mtime, shelves=loaded)
        return loaded


def reset() -> None:
    """Fuer Tests: Zustand vergessen, naechster Aufruf liest neu."""
    with _LOCK:
        _STATE.update(path=None, mtime=None, checked=0.0, shelves=None)


BACKGROUND = "_allgemein"  # Pseudo-Regal aus allgemeinen Fragen, ohne Eintraege
DETECT_MIN = 1.5  # Summe der Log-Verhaeltnisse der Frage-Staemme im besten Regal
DETECT_MARGIN = 0.5  # Abstand zum zweitbesten Regal (Log-Einheiten)


def detect(query: str, loaded: Optional[Dict[str, Shelf]] = None) -> Optional[str]:
    """Regal der Frage oder None (kein klares Thema)."""
    loaded = loaded if loaded is not None else shelves()
    if not loaded:
        return None
    qs = set(stems(query))
    scored = sorted(((sum(sh.keywords.get(s, 0.0) for s in qs), name) for name, sh in loaded.items()),
                    reverse=True)
    if not scored or scored[0][0] < DETECT_MIN or scored[0][1] == BACKGROUND:
        return None
    if len(scored) > 1 and scored[0][0] - scored[1][0] < DETECT_MARGIN:
        return None
    return scored[0][1]


def hits(query: str, k: int = 5) -> List[Dict]:
    """Kandidaten aus dem passenden Regal im Format der Such-Backends
    ({url, title, content, engine}) oder [] (aus, kein Regal, nichts gefunden)."""
    if k <= 0 or not enabled():
        return []
    loaded = shelves()
    regal = detect(query, loaded)
    if not regal:
        return []
    out = []
    for score, e in loaded[regal].search(stems(query), k):
        text = e.get("text") or ""
        out.append({"url": e["url"], "title": text[:120], "content": text, "engine": "bibliothek",
                    "library": regal, "library_score": round(score, 3)})
    return out


def seed_hits(req, k: int) -> List[Dict]:
    """Regal-Treffer fuer eine Such-Anfrage, mit den Filtern der Anfrage: keine bei News
    (Programme sind kein Nachrichtenstoff), include/exclude_domains wie bei den Engines.
    Jeder Treffer traegt einen Platz der Pseudo-Engine "bibliothek", damit die
    Rang-Fusion ihn wie eine Engine behandelt (mit Vertrauen nach Cross-Encoder)."""
    if getattr(req, "topic", None) == "news":
        return []
    from .search import _host_matches  # noqa: PLC0415 - Zyklus vermeiden

    include = getattr(req, "include_domains", None) or []
    exclude = getattr(req, "exclude_domains", None) or []
    out = []
    for h in hits(req.query, k):
        if exclude and _host_matches(h["url"], exclude):
            continue
        if include and not _host_matches(h["url"], include):
            continue
        out.append(dict(h, engine_rank=len(out) + 1, engine_ranks={"bibliothek": len(out) + 1}))
    return out
