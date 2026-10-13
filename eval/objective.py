#!/usr/bin/env python3
"""Gemeinsames Zielmass fuer Optimierungs-Runden (im Prozess, ohne Server).

Aufteilung gegen Ueberanpassung:
  dev  = queries_big.jsonl (50) + Recherche-Kassette Feld "queries" (27)
  test = queries.jsonl (30, Tavily-Vergleich) + queries_hard (15) + queries_fresh (10)
         + queries_multi (20 ES/FR) + Recherche-Kassette Feld "queries_niche" (27)
Optimiert wird NUR auf dev; test nur zur Kontrolle am Ende einer Runde.

Mit REPLAY_DIR (Standard eval/private/replay) werden Suchergebnisse und Seiten
einmal live aufgezeichnet und danach offline abgespielt: reproduzierbar, kein
Upstream-Verkehr. Latenz ist im Replay nicht aussagekraeftig (Seiten kommen sofort).

Kennzahlen je Split:
  hit1, hit3, mrr (Antwort-Muster im content der Treffer, Leerzeichen-tolerant)
  gold_rank (mittlerer Rang der ersten Antwort, ohne Treffer = 11) -> "Zeit bis Gold"
  hist_site30, hist_page30, hist_mrr (Recherche-Kassette, Gold-Links)
  score = 0.4*mrr + 0.2*hit1 + 0.2*hist_mrr/0.3 + 0.2*hist_site30/0.15  (Fakten- und Recherche-Qualitaet)

  objective.py [--split dev|test|both] [--live] [--out FILE]
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


def _dotenv() -> None:
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def hit_rank(results, pattern, cap=1000):
    rx = re.compile(pattern, re.IGNORECASE)
    rx_tight = re.compile(re.sub(r"(\\s|\s)[?*+]?", "", pattern), re.IGNORECASE)
    for i, r in enumerate(results, start=1):
        text = (r.get("content") or "")[:cap]
        if rx.search(text) or rx_tight.search(re.sub(r"\s+", "", text)):
            return i
    return None


def run_facts(files):
    from app.main import run_search
    from app.schema import SearchRequest

    ranks, lats, clen = [], [], []
    for f in files:
        for line in open(HERE / f, encoding="utf-8"):
            if not line.strip():
                continue
            q = json.loads(line)
            t0 = time.perf_counter()
            resp = run_search(SearchRequest(query=q["query"], max_results=10, include_answer=False,
                                            language=q.get("lang")))
            lats.append(time.perf_counter() - t0)
            res = [r.model_dump() for r in resp.results]
            ranks.append(hit_rank(res, q["answer_re"]))
            clen += [len(r["content"]) for r in res]
    n = len(ranks)
    return {
        "n": n,
        "hit1": round(sum(1 for r in ranks if r == 1) / n, 3),
        "hit3": round(sum(1 for r in ranks if r and r <= 3) / n, 3),
        "mrr": round(sum(1 / r for r in ranks if r) / n, 3),
        "gold_rank": round(statistics.mean(r or 11 for r in ranks), 3),
        "p50_s": round(statistics.median(lats), 3),
        "content_p50": statistics.median(clen) if clen else 0,
    }


def run_history(field):
    sys.path.insert(0, str(EVALS))
    import search_eval  # noqa: E402

    from app.main import run_search
    from app.schema import SearchRequest

    tasks = json.load(open(EVALS / "tasks.json", encoding="utf-8"))["tasks"]
    runs = []
    for t in tasks:
        for q in t.get(field, []):
            t0 = time.perf_counter()
            resp = run_search(SearchRequest(query=q, max_results=20, include_answer=False))
            runs.append({"task": t["id"], "query": q, "engine": "own", "sec": round(time.perf_counter() - t0, 2),
                         "error": None, "hits": [{"url": r.url, "title": r.title} for r in resp.results]})
    tmp = HERE / "private" / f"objective-cassette-{field}-{os.getpid()}.json"
    tmp.write_text(json.dumps({"engines": ["own"], "n": 20, "field": field, "runs": runs}), encoding="utf-8")
    rows = search_eval.evaluate(str(tmp), "count", engines={"own"}, fields=(field,))
    tmp.unlink()
    avg = lambda k: round(sum(r[k] for r in rows) / len(rows), 3)  # noqa: E731
    return {"hist_site30": avg("site@30"), "hist_page30": avg("page@30"), "hist_mrr": avg("mrr")}


def score(m):
    return round(0.4 * m["mrr"] + 0.2 * m["hit1"] + 0.2 * m["hist_mrr"] / 0.3 + 0.2 * m["hist_site30"] / 0.15, 4)


SPLITS = {
    "dev": (["queries_big.jsonl"], "queries"),
    "test": (["queries.jsonl", "queries_hard.jsonl", "queries_fresh.jsonl", "queries_multi.jsonl"], "queries_niche"),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev", choices=["dev", "test", "both"])
    ap.add_argument("--live", action="store_true", help="ohne Replay (echte Latenz, Upstream-Verkehr)")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    _dotenv()
    os.environ["SEARCH_CACHE_TTL_S"] = "0"
    os.environ["PAGE_CACHE_TTL_S"] = "0"
    if not a.live:
        os.environ.setdefault("REPLAY_DIR", str(HERE / "private" / "replay"))
        # Im Replay alle aufgezeichneten Seiten abwarten: sonst entscheidet die
        # Extraktions-Geschwindigkeit, welche Seiten ins Ranking kommen (Rauschen).
        os.environ.setdefault("FETCH_BUDGET_MS", "3000")
        os.environ.setdefault("SPLIT_TAIL_MS", "3000")
    else:
        os.environ.pop("REPLAY_DIR", None)
    sys.path.insert(0, str(ROOT))
    from app.extract import warm_up

    warm_up()
    out = {}
    for split in (["dev", "test"] if a.split == "both" else [a.split]):
        files, field = SPLITS[split]
        m = run_facts(files)
        m.update(run_history(field))
        m["score"] = score(m)
        out[split] = m
        print(split, json.dumps(m, ensure_ascii=False))
    if a.out:
        Path(a.out).write_text(json.dumps(out, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
