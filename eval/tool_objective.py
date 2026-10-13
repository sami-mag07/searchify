#!/usr/bin/env python3
"""Gemeinsames, deterministisches Messgeruest fuer die drei Nachbauten Serper, Exa, Reader.

Vorbild eval/objective.py: im Prozess (FastAPI TestClient), ohne Server, mit Replay.
Optimiert wird NUR auf dev; test nur zur Kontrolle.

  tool_objective.py --tool serper|exa|reader --split dev|test|both [--out FILE]
                    [--record] [--offline]

Replay
  Serper/Exa: REPLAY_DIR (Standard eval/private/replay, geteilt mit objective.py).
  Reader:     READER_REPLAY_DIR (Standard eval/private/replay-reader, eigene Seiten mit
              4-MB-Grenze; die Such-Aufnahmen sind bei 1 MB abgeschnitten).
  Fehlt eine Aufnahme, wird live geholt und aufgezeichnet, auch Fehlschlaege
  (SearXNG-Fehler als <sha>.err, leere Antworten, Seiten als .fail, Browser-Renderings
  unter render/). SearXNG live hoechstens 1 Anfrage je Sekunde. Danach ist der Lauf
  deterministisch; "live" in der Ausgabe zaehlt die Live-Abrufe (Ziel 0).
  --record   grosszuegige Fristen, damit beim Aufnehmen nichts wegen Zeitlimit fehlt;
             so oft laufen lassen, bis live = 0.
  --offline  nie live, fehlende Aufnahmen zaehlen als miss.
  Nie Calls an Tavily/Serper/Exa (SEARCH_FALLBACK ist aus, kein ddgs).

Kennzahlen und score in [0, 1]
  serper  Recherche-Kassette (~/.claude/skills/research-agent/evals, Gold-Links),
          dev = Feld queries, test = queries_niche, own-serper num=20:
            hist_mrr, hist_site30, hist_page30
          Fakten ueber /serper/search (dev queries_big, test queries + queries_hard),
          Antwort in Titel+Snippet: f_hit1, f_hit3, f_mrr, f_answerbox
          score = 0.3*f_mrr + 0.1*f_hit1 + 0.3*min(1, hist_mrr/0.4)
                  + 0.2*min(1, hist_site30/0.2) + 0.1*min(1, hist_page30/0.05)
  exa     /exa/search, contents.highlights (wie der research-agent) und ein eigener Lauf mit
          contents.text; dev = queries_big, test = queries + queries_hard + queries_multi:
            top1_text, hl1 (erstes Highlight von Treffer 1), top3_hl, hl_mrr, hl_chars
          findSimilar (Plausibilitaet): Treffer-1-URL der Highlight-Suche als Quelle,
            numResults 5 mit Text: sim_ans5 (ein aehnliches Ergebnis enthaelt die Antwort
            auch), sim_valid (>= 3 Ergebnisse, Quelle nicht dabei)
          score = 0.25*top1_text + 0.25*hl1 + 0.25*top3_hl + 0.15*sim_ans5 + 0.1*sim_valid
  reader  /jina/r (JSON, Links) und /extract (markdown, metadata) gegen Original-Jina
          (eval/private/jina_cache); URL-Liste und dev/test-Aufteilung (fest per Hash der
          URL) in eval/private/reader_split.json:
            je System recall, precision, title_equal, action_hit, ok_rate
          score = Mittel ueber jina/extract von
                  0.35*recall + 0.2*precision + 0.15*title_equal + 0.1*action_hit + 0.2*ok_rate
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import statistics
import sys
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PRIV = HERE / "private"
EVALS = Path.home() / ".claude/skills/research-agent/evals"
SPLIT_FILE = PRIV / "reader_split.json"

LIVE: Dict[str, int] = {"searxng": 0, "serper": 0, "pages": 0, "render": 0, "jina_orig": 0}
MISSES: Dict[str, int] = {"searxng": 0, "serper": 0, "pages": 0, "render": 0, "jina_orig": 0}

FACTS = {
    "serper": {"dev": ["queries_big.jsonl"], "test": ["queries.jsonl", "queries_hard.jsonl"]},
    "exa": {"dev": ["queries_big.jsonl"], "test": ["queries.jsonl", "queries_hard.jsonl", "queries_multi.jsonl"]},
}
HIST_FIELD = {"dev": "queries", "test": "queries_niche"}


# ---------------------------------------------------------------- Hilfsfunktionen (getestet)


def split_of(url: str) -> str:
    """Feste 50/50-Aufteilung per Hash der URL (unabhaengig von Reihenfolge und Lauf)."""
    return "dev" if hashlib.sha1(url.encode("utf-8")).digest()[0] % 2 == 0 else "test"


def answer_matcher(pattern: str) -> Callable[[Optional[str]], bool]:
    """Antwort-Muster wie objective.py: normal oder ohne Leerzeichen (tolerant)."""
    rx = re.compile(pattern, re.IGNORECASE)
    tight = re.compile(re.sub(r"(\\s|\s)[?*+]?", "", pattern), re.IGNORECASE)
    return lambda t: bool(t) and bool(rx.search(t) or tight.search(re.sub(r"\s+", "", t)))


def first_rank(texts: Iterable[str], ok: Callable[[Optional[str]], bool]) -> Optional[int]:
    for i, t in enumerate(texts, start=1):
        if ok(t):
            return i
    return None


def rank_stats(ranks: List[Optional[int]]) -> Dict[str, float]:
    n = len(ranks) or 1
    return {
        "hit1": round(sum(1 for r in ranks if r == 1) / n, 3),
        "hit3": round(sum(1 for r in ranks if r and r <= 3) / n, 3),
        "mrr": round(sum(1 / r for r in ranks if r) / n, 3),
    }


def cap01(x: Optional[float]) -> float:
    return 0.0 if x is None else max(0.0, min(1.0, float(x)))


def weighted(parts: Dict[str, float], values: Dict[str, Optional[float]]) -> float:
    """Gewichtetes Mittel; fehlende Werte (None) fallen samt Gewicht raus."""
    tot = sum(w for k, w in parts.items() if values.get(k) is not None)
    if not tot:
        return 0.0
    return sum(w * cap01(values[k]) for k, w in parts.items() if values.get(k) is not None) / tot


def score_serper(m: Dict) -> float:
    return round(weighted({"f_mrr": 0.3, "f_hit1": 0.1, "h_mrr": 0.3, "h_site": 0.2, "h_page": 0.1}, {
        "f_mrr": m.get("f_mrr"), "f_hit1": m.get("f_hit1"),
        "h_mrr": None if m.get("hist_mrr") is None else m["hist_mrr"] / 0.4,
        "h_site": None if m.get("hist_site30") is None else m["hist_site30"] / 0.2,
        "h_page": None if m.get("hist_page30") is None else m["hist_page30"] / 0.05,
    }), 4)


def score_exa(m: Dict) -> float:
    return round(weighted({"top1_text": 0.25, "hl1": 0.25, "top3_hl": 0.25, "sim_ans5": 0.15,
                           "sim_valid": 0.1}, m), 4)


def score_reader(m: Dict) -> float:
    parts = {"recall": 0.35, "precision": 0.2, "title_equal": 0.15, "action_hit": 0.1, "ok_rate": 0.2}
    vals = [weighted(parts, m[s]) for s in ("jina", "extract") if s in m]
    return round(statistics.mean(vals), 4) if vals else 0.0


def deviation(a: Dict, b: Dict, prefix: str = "") -> Dict[str, float]:
    """Absolute Abweichung aller Zahlen zweier Laeufe (verschachtelt), nur != 0."""
    out: Dict[str, float] = {}
    for k in sorted(set(a) | set(b)):
        va, vb = a.get(k), b.get(k)
        key = f"{prefix}{k}"
        if isinstance(va, dict) and isinstance(vb, dict):
            out.update(deviation(va, vb, key + "."))
        elif isinstance(va, (int, float)) and isinstance(vb, (int, float)) and not isinstance(va, bool):
            if va != vb:
                out[key] = round(abs(va - vb), 4)
    return out


class RateGate:
    """Hoechstens ein Durchlass je `interval` Sekunden, auch ueber Threads."""

    def __init__(self, interval: float, clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.interval, self.clock, self.sleep = interval, clock, sleep
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            slot = max(self.clock(), self._next)
            self._next = slot + self.interval
        delay = slot - self.clock()
        if delay > 0:
            self.sleep(delay)


class Replay:
    """Aufnahme/Wiedergabe eines JSON-Upstreams; Fehlschlaege werden mit aufgezeichnet."""

    def __init__(self, base: str, kind: str, gate: Optional[RateGate], offline: bool,
                 make_error: Callable[[str], Exception]) -> None:
        self.folder = os.path.join(base, kind)
        self.kind, self.gate, self.offline, self.make_error = kind, gate, offline, make_error

    def path(self, key: str) -> str:
        # Gleiche Ablage wie app.search._replay_path: <kind>/<sha1(key)>
        return os.path.join(self.folder, hashlib.sha1(key.encode("utf-8")).hexdigest())

    def get(self, key: str, live: Callable[[], Dict]) -> Dict:
        path = self.path(key)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        if os.path.exists(path + ".err"):
            with open(path + ".err", encoding="utf-8") as fh:
                raise self.make_error(fh.read() or "aufgezeichneter Fehler")
        if self.offline:
            MISSES[self.kind] += 1
            return {"results": []}
        if self.gate:
            self.gate.wait()
        LIVE[self.kind] += 1
        os.makedirs(self.folder, exist_ok=True)
        try:
            data = live()
        except Exception as exc:  # noqa: BLE001 - jeder Fehler wird aufgezeichnet
            with open(path + ".err", "w", encoding="utf-8") as fh:
                fh.write(f"{type(exc).__name__}")
            raise
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        return data


# ---------------------------------------------------------------- Umgebung


def _setup(tool: str, record: bool, offline: bool) -> None:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    os.environ["SEARCH_FALLBACK"] = ""  # kein ddgs und kein bezahlter Dienst als Reserve
    os.environ["SEARCH_BACKEND"] = "searxng"
    os.environ["SEARCH_CACHE_TTL_S"] = "0"
    os.environ["PAGE_CACHE_TTL_S"] = "0"
    os.environ.setdefault("EXTRACT_WORKERS", "2")
    os.environ.pop("API_KEYS", None)
    if tool == "reader":
        os.environ["REPLAY_DIR"] = os.environ.get("READER_REPLAY_DIR", str(PRIV / "replay-reader"))
    else:
        os.environ.setdefault("REPLAY_DIR", str(PRIV / "replay"))
    budget = "15000" if record else "3000"
    for k in ("FETCH_BUDGET_MS", "FETCH_BUDGET_ADVANCED_MS", "SPLIT_TAIL_MS"):
        os.environ.setdefault(k, budget)
    # Keine Zweitanfragen (Hedging) und lange Suchfrist: SearXNG-Seiten kommen im
    # Replay sofort, beim Aufnehmen sorgt das Tor fuer 1/s.
    os.environ.setdefault("SPLIT_SEARCH_TIMEOUT_MS", "30000")
    os.environ.setdefault("SPLIT_HEDGE_MS", "60000")
    os.environ.setdefault("SEARCH_HEDGE_MS", "60000")
    sys.path.insert(0, str(ROOT))

    import httpx

    from app import extract, search
    from app.compat import render, serper

    base = os.environ["REPLAY_DIR"]
    gate = RateGate(1.0)

    sx = Replay(base, "searxng", gate, offline, lambda m: httpx.HTTPError(f"replay: {m}"))
    orig_page = search._searxng_page

    def searxng_page(client, url, params):
        key = url + "?" + json.dumps(sorted((k, str(v)) for k, v in params.items()))
        return sx.get(key, lambda: orig_page(client, url, params))

    search._searxng_page = searxng_page

    sp = Replay(base, "serper", gate, offline, lambda m: search.SearchBackendError(f"replay: {m}"))
    orig_up = serper._upstream

    def upstream(p, pageno, news):
        key = json.dumps([p, pageno, news], sort_keys=True, default=str)
        return sp.get(key, lambda: orig_up(p, pageno, news))

    serper._upstream = upstream

    orig_live = extract._fetch_live

    def fetch_live(*a, **k):
        if offline:
            MISSES["pages"] += 1
            return None
        LIVE["pages"] += 1
        if os.environ.get("TOOL_OBJECTIVE_LIVE_LOG"):
            with open(os.environ["TOOL_OBJECTIVE_LIVE_LOG"], "a", encoding="utf-8") as fh:
                fh.write(f"page {a[0] if a else k.get('url')}\n")
        return orig_live(*a, **k)

    extract._fetch_live = fetch_live

    orig_try = render.try_render
    render_dir = os.path.join(base, "render")

    def try_render(url, timeout=12.0, wait=None):
        # Ersetzt render.try_render (Reader-Reserve ruft nur das); kein DNS, kein Browser
        # im Replay. Fehlende Aufnahme offline: wie ein fehlgeschlagener Versuch.
        path = os.path.join(render_dir, hashlib.sha1(url.encode("utf-8")).hexdigest())
        if os.path.exists(path + ".html"):
            return "tried", Path(path + ".html").read_text(encoding="utf-8")
        if os.path.exists(path + ".fail"):
            return "tried", None
        if offline:
            MISSES["render"] += 1
            return "tried", None
        LIVE["render"] += 1
        status, html = orig_try(url, timeout, wait)
        if status == "tried":
            os.makedirs(render_dir, exist_ok=True)
            Path(path + (".html" if html else ".fail")).write_text(html or "", encoding="utf-8")
        return status, html

    render.try_render = try_render
    render.render_html = lambda url, timeout=12.0, wait=None: try_render(url, timeout, wait)[1]


def _client():
    from fastapi.testclient import TestClient

    from app.extract import warm_up
    from app.main import app

    warm_up()
    # 500er der App zaehlen als Fehlschlag statt den Lauf abzubrechen.
    return TestClient(app, raise_server_exceptions=False)


def _facts(files: List[str]) -> List[Dict]:
    out = []
    for f in files:
        for line in open(HERE / f, encoding="utf-8"):
            if line.strip():
                out.append(json.loads(line))
    return out


def _history(client, field: str, engine: str, call: Callable[[str], List[Dict]]) -> Dict:
    sys.path.insert(0, str(EVALS))
    import search_eval  # noqa: E402

    if engine not in search_eval.search.CHAIN:
        search_eval.search.CHAIN.append(engine)
    tasks = json.load(open(EVALS / "tasks.json", encoding="utf-8"))["tasks"]
    runs = []
    for t in tasks:
        for q in t.get(field, []):
            t0 = time.perf_counter()
            hits = call(q)
            runs.append({"task": t["id"], "query": q, "engine": engine, "sec": round(time.perf_counter() - t0, 2),
                         "error": None, "hits": hits})
    tmp = PRIV / f"tool-objective-cassette-{engine}-{field}-{os.getpid()}.json"
    tmp.write_text(json.dumps({"engines": [engine], "n": 20, "field": field, "runs": runs}), encoding="utf-8")
    try:
        rows = search_eval.evaluate(str(tmp), "count", engines={engine}, fields=(field,))
    finally:
        tmp.unlink()
    avg = lambda k: round(sum(r[k] for r in rows) / len(rows), 3)  # noqa: E731
    return {"hist_n": len(runs), "hist_site30": avg("site@30"), "hist_page30": avg("page@30"),
            "hist_mrr": avg("mrr")}


# ---------------------------------------------------------------- Serper


def run_serper(client, split: str) -> Dict:
    ranks, ab, lats = [], 0, []
    qs = _facts(FACTS["serper"][split])
    for q in qs:
        body = {"q": q["query"], "num": 10}
        if q.get("lang"):
            body["hl"] = q["lang"]
        t0 = time.perf_counter()
        d = client.post("/serper/search", json=body).json()
        lats.append(time.perf_counter() - t0)
        ok = answer_matcher(q["answer_re"])
        org = d.get("organic") or [] if isinstance(d, dict) else []
        ranks.append(first_rank([f"{o.get('title', '')} {o.get('snippet', '')}" for o in org], ok))
        box = (d.get("answerBox") or {}) if isinstance(d, dict) else {}
        ab += bool(ok(box.get("answer") or box.get("snippet")))
    rs = rank_stats(ranks)
    m = {"f_n": len(qs), "f_hit1": rs["hit1"], "f_hit3": rs["hit3"], "f_mrr": rs["mrr"],
         "f_answerbox": round(ab / max(1, len(qs)), 3), "f_p50_s": round(statistics.median(lats), 3)}

    def call(q):
        d = client.post("/serper/search", json={"q": q, "num": 20}).json()
        return [{"url": o["link"], "title": o.get("title", "")} for o in (d.get("organic") or [])]

    m.update(_history(client, HIST_FIELD[split], "own-serper", call))
    m["score"] = score_serper(m)
    return m


# ---------------------------------------------------------------- Exa


def _exa_body(q: Dict, contents: Dict, n: int = 10) -> Dict:
    body = {"query": q["query"], "numResults": n, "type": "auto", "contents": contents}
    if q.get("lang"):
        body["language"] = q["lang"]
    return body


def run_exa(client, split: str) -> Dict:
    qs = _facts(FACTS["exa"][split])
    rows, chars, lats = [], [], []
    for q in qs:
        ok = answer_matcher(q["answer_re"])
        t0 = time.perf_counter()
        res = client.post("/exa/search", json=_exa_body(q, {"highlights": True})).json().get("results", [])
        lats.append(time.perf_counter() - t0)
        hls = [r.get("highlights") or [] for r in res]
        chars += [len(" ".join(h)) for h in hls]
        txt = client.post("/exa/search", json=_exa_body(q, {"text": True})).json().get("results", [])
        row = {
            "top1_text": bool(txt) and ok((txt[0].get("text") or "")[:20000]),
            "hl1": bool(hls and hls[0]) and ok(hls[0][0]),
            "top3_hl": any(ok(" ".join(h)) for h in hls[:3]),
            "hl_rank": first_rank([" ".join(h) for h in hls], ok),
            "sim_ans5": None, "sim_valid": None,
        }
        if res:
            src = res[0]["url"]
            sim = client.post("/exa/findSimilar", json={"url": src, "numResults": 5,
                                                         "contents": {"text": True}})
            items = sim.json().get("results", []) if sim.status_code == 200 else []
            row["sim_status"] = sim.status_code
            row["sim_ans5"] = any(ok((r.get("text") or "")[:20000]) for r in items)
            row["sim_valid"] = len(items) >= 3 and all(r.get("url") != src for r in items)
        rows.append(row)
    n = max(1, len(rows))
    mean = lambda k: round(sum(bool(r[k]) for r in rows if r[k] is not None)  # noqa: E731
                           / max(1, sum(1 for r in rows if r[k] is not None)), 3)
    m = {"n": len(rows), "top1_text": mean("top1_text"), "hl1": mean("hl1"), "top3_hl": mean("top3_hl"),
         "hl_mrr": round(sum(1 / r["hl_rank"] for r in rows if r["hl_rank"]) / n, 3),
         "hl_chars": round(statistics.mean(chars)) if chars else 0,
         "sim_ans5": mean("sim_ans5"), "sim_valid": mean("sim_valid"),
         "sim_errors": sum(1 for r in rows if r.get("sim_status", 200) != 200),
         "p50_s": round(statistics.median(lats), 3)}
    m["score"] = score_exa(m)
    return m


# ---------------------------------------------------------------- Reader


def reader_urls(split: str) -> List[Dict]:
    data = json.loads(SPLIT_FILE.read_text(encoding="utf-8"))
    return [u for u in data["urls"] if split_of(u["url"]) == split]


def run_reader(client, split: str, offline: bool) -> Dict:
    import httpx

    sys.path.insert(0, str(HERE))
    import reader_bench as rb  # noqa: E402

    rows = []
    with httpx.Client(follow_redirects=True) as live:
        for u in reader_urls(split):
            url = u["url"]
            if rb.load_orig(url) is None:
                if offline:
                    MISSES["jina_orig"] += 1
                else:
                    LIVE["jina_orig"] += 1
                    rb.fetch_orig(live, url)  # r.jina.ai, >= 4,2 s Abstand, dauerhaft gecacht
            raw = {}
            t0 = time.perf_counter()
            r = client.get("/jina/r/" + url, headers={"Accept": "application/json", "X-With-Links-Summary": "true",
                                                      "X-No-Cache": "true"})
            raw["jina"] = {"status": r.status_code, "latency": round(time.perf_counter() - t0, 3), "body": r.text}
            t0 = time.perf_counter()
            r = client.post("/extract", json={"urls": [url], "format": "markdown", "include_metadata": True})
            raw["extract"] = {"status": r.status_code, "latency": round(time.perf_counter() - t0, 3),
                              "body": r.text}
            rows.append(rb.score_url(u["id"], u["group"], u["lang"], url, raw))
    m: Dict = {"n": len(rows), "n_orig_ok": sum(1 for r in rows if r["orig"]["ok"])}
    for s in ("jina", "extract"):
        a = rb.aggregate(rows, s)
        m[s] = {k: a[k] for k in ("ok_rate", "recall", "precision", "title_equal", "action_hit", "action_base")}
    m["score"] = score_reader(m)
    m["worst"] = [r["id"] for r in sorted(
        rows, key=lambda r: (r["jina"]["recall"] if r["jina"]["recall"] is not None else 2))[:5]]
    return m


# ---------------------------------------------------------------- Ablauf


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tool", required=True, choices=["serper", "exa", "reader"])
    ap.add_argument("--split", default="dev", choices=["dev", "test", "both"])
    ap.add_argument("--out", default="")
    ap.add_argument("--record", action="store_true", help="grosszuegige Fristen beim Aufnehmen")
    ap.add_argument("--offline", action="store_true", help="nie live, fehlende Aufnahmen = miss")
    a = ap.parse_args()
    _setup(a.tool, a.record, a.offline)
    client = _client()
    out: Dict = {"tool": a.tool}
    for split in (["dev", "test"] if a.split == "both" else [a.split]):
        t0 = time.perf_counter()
        before = dict(LIVE)
        if a.tool == "serper":
            m = run_serper(client, split)
        elif a.tool == "exa":
            m = run_exa(client, split)
        else:
            m = run_reader(client, split, a.offline)
        m["sec"] = round(time.perf_counter() - t0, 1)
        m["live"] = sum(LIVE.values()) - sum(before.values())
        m["live_by"] = {k: v - before[k] for k, v in LIVE.items() if v - before[k]}
        m["miss"] = dict(MISSES)
        out[split] = m
        print(a.tool, split, json.dumps(m, ensure_ascii=False), flush=True)
    if a.out:
        Path(a.out).write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    _hard_exit(a.record)


def _hard_exit(record: bool) -> None:
    """Ohne auf Hintergrund-Threads zu warten beenden, aber die Prozess-Pool-Worker
    vorher beenden (sonst bleiben sie verwaist und halten die Ausgabe-Pipe offen)."""
    import multiprocessing

    if record:
        time.sleep(5)  # spaete Seiten-Abrufe noch in die Aufnahme schreiben lassen
    sys.stdout.flush()
    for child in multiprocessing.active_children():
        child.terminate()
    for child in multiprocessing.active_children():
        child.join(timeout=2)
    os._exit(0)


if __name__ == "__main__":
    main()
