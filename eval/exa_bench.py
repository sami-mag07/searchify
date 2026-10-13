#!/usr/bin/env python3
"""Exa-Nachbau im Prozess messen (FastAPI TestClient, Replay, kein Upstream-Verkehr).

Fakten (dev = queries_big.jsonl, 50) ueber POST /exa/search mit
contents.highlights=true (so ruft der research-agent own-exa auf):
  top1_text  Antwort im Volltext von Treffer 1
  top1_hl    Antwort in den Highlights von Treffer 1 (das Snippet, das der Agent sieht)
  hl1        Antwort im ersten Highlight von Treffer 1
  top3_hl    Antwort in irgendeinem Highlight der Treffer 1-3
  hl_mrr     1/Rang des ersten Treffers, dessen Highlights die Antwort enthalten
  hl_chars   mittlere Zeichen je Ergebnis-Snippet (Lesekosten)
Recherche (Kassette Feld "queries", own-exa, numResults 20): hist_site30, hist_page30, hist_mrr.

Replay ist hart: fehlt eine Aufnahme, gibt es keine Treffer bzw. keine Seite
(gezaehlt als miss_searx / miss_pages), nie einen Live-Abruf.

  exa_bench.py [--facts FILE] [--no-history] [--out FILE]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
EVALS = Path.home() / ".claude/skills/research-agent/evals"
MISSES = {"searx": 0, "pages": 0}


def _setup() -> None:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())
    os.environ["SEARCH_CACHE_TTL_S"] = "0"
    os.environ["PAGE_CACHE_TTL_S"] = "0"
    os.environ.setdefault("REPLAY_DIR", str(HERE / "private" / "replay"))
    os.environ.setdefault("FETCH_BUDGET_MS", "3000")
    os.environ.setdefault("SPLIT_TAIL_MS", "3000")
    os.environ.pop("API_KEYS", None)
    sys.path.insert(0, str(ROOT))

    from app import extract, search

    orig_page = search._searxng_page

    def page_only_replay(client, url, params):
        path = search._replay_path("searxng", url + "?" + json.dumps(sorted((k, str(v)) for k, v in params.items())))
        if not (path and os.path.exists(path)):
            MISSES["searx"] += 1
            return {"results": []}
        return orig_page(client, url, params)

    def no_live(*_a, **_k):
        MISSES["pages"] += 1
        return None

    search._searxng_page = page_only_replay
    extract._fetch_live = no_live


def _rx(pattern):
    rx = re.compile(pattern, re.IGNORECASE)
    tight = re.compile(re.sub(r"(\\s|\s)[?*+]?", "", pattern), re.IGNORECASE)
    return lambda t: bool(t) and bool(rx.search(t) or tight.search(re.sub(r"\s+", "", t)))


def run_facts(client, files):
    rows, lats, chars = [], [], []
    for f in files:
        for line in open(HERE / f, encoding="utf-8"):
            if not line.strip():
                continue
            q = json.loads(line)
            body = {"query": q["query"], "numResults": 10, "type": "auto", "contents": {"highlights": True}}
            if q.get("lang"):
                body["language"] = q["lang"]
            t0 = time.perf_counter()
            res = client.post("/exa/search", json=body).json().get("results", [])
            lats.append(time.perf_counter() - t0)
            ok = _rx(q["answer_re"])
            hls = [r.get("highlights") or [] for r in res]
            chars += [len(" ".join(h)) for h in hls]
            hl_rank = next((i for i, h in enumerate(hls, 1) if ok(" ".join(h))), None)
            rows.append({
                "id": q.get("id"),
                "top1_hl": bool(hls) and ok(" ".join(hls[0])),
                "hl1": bool(hls and hls[0]) and ok(hls[0][0]),
                "top3_hl": any(ok(" ".join(h)) for h in hls[:3]),
                "hl_rank": hl_rank,
            })
    n = len(rows)
    out = {
        "n": n,
        "top1_hl": round(sum(r["top1_hl"] for r in rows) / n, 3),
        "hl1": round(sum(r["hl1"] for r in rows) / n, 3),
        "top3_hl": round(sum(r["top3_hl"] for r in rows) / n, 3),
        "hl_mrr": round(sum(1 / r["hl_rank"] for r in rows if r["hl_rank"]) / n, 3),
        "hl_chars": round(statistics.mean(chars)) if chars else 0,
        "p50_s": round(statistics.median(lats), 3),
    }
    return out, rows


def run_facts_text(client, files):
    """Top-1-Volltext (eigener Lauf mit text=true, damit Highlights unbeeinflusst bleiben)."""
    hits, n = 0, 0
    for f in files:
        for line in open(HERE / f, encoding="utf-8"):
            if not line.strip():
                continue
            q = json.loads(line)
            body = {"query": q["query"], "numResults": 10, "contents": {"text": True}}
            if q.get("lang"):
                body["language"] = q["lang"]
            res = client.post("/exa/search", json=body).json().get("results", [])
            n += 1
            hits += bool(res) and _rx(q["answer_re"])((res[0].get("text") or "")[:20000])
    return round(hits / n, 3)


def run_history(client, field="queries", n=20):
    sys.path.insert(0, str(EVALS))
    import search_eval  # noqa: E402

    if "own-exa" not in search_eval.search.CHAIN:
        search_eval.search.CHAIN.append("own-exa")
    tasks = json.load(open(EVALS / "tasks.json", encoding="utf-8"))["tasks"]
    runs = []
    for t in tasks:
        for q in t.get(field, []):
            t0 = time.perf_counter()
            body = {"query": q, "numResults": n, "type": "auto", "contents": {"highlights": True}}
            res = client.post("/exa/search", json=body).json().get("results", [])
            runs.append({"task": t["id"], "query": q, "engine": "own-exa", "sec": round(time.perf_counter() - t0, 2),
                         "error": None, "hits": [{"url": r["url"], "title": r.get("title") or ""} for r in res]})
    tmp = HERE / "private" / f"exa-bench-cassette-{os.getpid()}.json"
    tmp.write_text(json.dumps({"engines": ["own-exa"], "n": n, "field": field, "runs": runs}), encoding="utf-8")
    rows = search_eval.evaluate(str(tmp), "count", engines={"own-exa"}, fields=(field,))
    tmp.unlink()
    avg = lambda k: round(sum(r[k] for r in rows) / len(rows), 3)  # noqa: E731
    return {"hist_site30": avg("site@30"), "hist_page30": avg("page@30"), "hist_mrr": avg("mrr"),
            "hist_p50_s": round(statistics.median(r["sec"] for r in runs), 3)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--facts", default="queries_big.jsonl")
    ap.add_argument("--no-history", action="store_true")
    ap.add_argument("--no-text", action="store_true")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    _setup()
    from fastapi.testclient import TestClient

    from app.extract import warm_up
    from app.main import app

    warm_up()
    client = TestClient(app)
    m, rows = run_facts(client, a.facts.split(","))
    if not a.no_text:
        m["top1_text"] = run_facts_text(client, a.facts.split(","))
    if not a.no_history:
        m.update(run_history(client))
    m["miss_searx"], m["miss_pages"] = MISSES["searx"], MISSES["pages"]
    print(json.dumps(m, ensure_ascii=False))
    if a.out:
        Path(a.out).write_text(json.dumps({"metrics": m, "rows": rows}, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
