#!/usr/bin/env python3
"""Tempo + Trefferqualitaet: eigener Dienst gegen Tavily (NUR intern).

Misst je Query die Wall-Clock-Latenz und ob die richtige Antwort (Regex
`answer_re` aus queries.jsonl) im content der Top-1/3/5-Treffer steht.
Beide Seiten werden auf dieselbe content-Laenge gekappt, damit lange Chunks
nicht unfair mehr Treffer bekommen.

Tavily wird pro Query genau einmal abgefragt und in eval/private/tavily_cache.json
gecacht (Credits sparen). Eigene Seite laeuft ueber app.main.run_search, also
den echten Dienst-Code, ohne answer-Schritt (claude-code-api liegt auf dem VPS).
Ausgaben nur nach eval/private/ (gitignored, nicht veroeffentlichen).
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
PRIVATE = HERE / "private"
CACHE = PRIVATE / "tavily_cache.json"
CONTENT_CAP = 1000  # Zeichen je Treffer, fuer beide Seiten gleich


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


load_dotenv(ROOT / ".env")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from compare_tavily import jaccard_urls  # noqa: E402


def hit_rank(results: list, pattern: str) -> int | None:
    rx = re.compile(pattern, re.IGNORECASE)
    for i, r in enumerate(results, start=1):
        if rx.search((r.get("content") or "")[:CONTENT_CAP]):
            return i
    return None


def _ckey(query: str, with_answer: bool) -> str:
    return query if with_answer else query + "|noanswer"


def tavily_results(queries: list, with_answer: bool) -> dict:
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    missing = [q for q in queries if _ckey(q["query"], with_answer) not in cache]
    if missing:
        import httpx

        from compare_run import load_tavily_key

        key = load_tavily_key()
        with httpx.Client(timeout=30) as client:
            for q in missing:
                body = {"query": q["query"], "search_depth": "basic",
                        "include_answer": "basic" if with_answer else False, "max_results": 10}
                t0 = time.perf_counter()
                resp = client.post("https://api.tavily.com/search", json=body,
                                   headers={"Authorization": f"Bearer {key}"})
                resp.raise_for_status()
                data = resp.json()
                cache[_ckey(q["query"], with_answer)] = {
                    "latency_s": round(time.perf_counter() - t0, 3),
                    "answer": data.get("answer") or "",
                    "results": [{"url": r.get("url"), "content": r.get("content"),
                                 "score": r.get("score")} for r in data.get("results", [])],
                }
                time.sleep(0.5)
        PRIVATE.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return {q["query"]: cache[_ckey(q["query"], with_answer)] for q in queries}


def run_own(queries: list, repeat: int = 1, depth: str = "basic") -> dict:
    from app.main import run_search
    from app.schema import SearchRequest

    def req(q):
        return SearchRequest(query=q["query"], max_results=10, include_answer=False,
                             language=q.get("lang"), search_depth=depth)

    run_search(req({"query": "warmup", "lang": "en"}))  # Modell laden, Verbindungen oeffnen
    out = {}
    for rnd in range(repeat):
        for q in queries:
            t0 = time.perf_counter()
            resp = run_search(req(q))
            lat = round(time.perf_counter() - t0, 3)
            if rnd == 0:  # Qualitaet aus dem ersten Durchlauf, Latenz aus allen
                out[q["query"]] = {
                    "latency_s": lat, "latencies": [lat],
                    "results": [r.model_dump() for r in resp.results],
                    "usage": resp.usage.model_dump() if resp.usage else {},
                    "timings": [resp.usage.timings_ms],
                }
            else:
                out[q["query"]]["latencies"].append(lat)
                out[q["query"]]["timings"].append(resp.usage.timings_ms)
    return out


def pct(xs, p):
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(round(p * (len(s) - 1))))], 2)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", default=str(HERE / "queries.jsonl"))
    ap.add_argument("--tag", default="run")
    ap.add_argument("--repeat", type=int, default=2, help="Durchlaeufe der eigenen Seite (Latenz)")
    ap.add_argument("--depth", default="basic", help="search_depth der eigenen Seite")
    ap.add_argument("--cache", action="store_true",
                    help="Seiten-Cache an (Standard aus: jede Query kalt, ehrlicher Vergleich)")
    ap.add_argument("--tavily-answer", action="store_true",
                    help="Tavily mit answer messen (Standard: ohne, wie die eigene Seite)")
    args = ap.parse_args()

    if not args.cache:
        os.environ["PAGE_CACHE_TTL_S"] = "0"
    queries = [json.loads(l) for l in open(args.queries, encoding="utf-8") if l.strip()]
    tav = tavily_results(queries, args.tavily_answer)
    own = run_own(queries, args.repeat, args.depth)

    print(f"{'id':12} {'T s':>5} {'E s':>5}  T@  E@  jac  E-usage")
    agg = {"T": {"lat": [], "ranks": [], "clen": []}, "E": {"lat": [], "ranks": [], "clen": []}}
    tims: list = []
    for q in queries:
        t, e = tav[q["query"]], own[q["query"]]
        rt, re_ = hit_rank(t["results"], q["answer_re"]), hit_rank(e["results"], q["answer_re"])
        agg["T"]["lat"].append(t["latency_s"]); agg["T"]["ranks"].append(rt)
        agg["E"]["lat"].extend(e["latencies"]); agg["E"]["ranks"].append(re_)
        tims.extend(e["timings"])
        agg["T"]["clen"].extend(len(r.get("content") or "") for r in t["results"])
        agg["E"]["clen"].extend(len(r.get("content") or "") for r in e["results"])
        jac = jaccard_urls([r["url"] for r in t["results"]], [r["url"] for r in e["results"]])
        u = e["usage"]
        print(f"{q['id']:12} {t['latency_s']:5.2f} {e['latency_s']:5.2f}  {rt or '-':>2}  {re_ or '-':>2}  "
              f"{jac:.2f} src={u.get('sources_found')}/{u.get('sources_extracted')} ch={u.get('chunks_ranked')}")

    summary = {}
    for side, a in agg.items():
        n = len(a["ranks"])
        summary[side] = {
            "p50_s": pct(a["lat"], 0.5), "p95_s": pct(a["lat"], 0.95),
            "hit@1": sum(1 for r in a["ranks"] if r and r <= 1) / n,
            "hit@3": sum(1 for r in a["ranks"] if r and r <= 3) / n,
            "hit@5": sum(1 for r in a["ranks"] if r and r <= 5) / n,
            "mrr": round(sum(1 / r for r in a["ranks"] if r) / n, 3),
            "content_chars_p50": statistics.median(a["clen"]) if a["clen"] else 0,
        }
    print()
    for side, name in (("T", "Tavily"), ("E", "Eigen")):
        s = summary[side]
        print(f"{name:7} p50 {s['p50_s']}s  p95 {s['p95_s']}s  hit@1 {s['hit@1']:.2f}  "
              f"hit@3 {s['hit@3']:.2f}  hit@5 {s['hit@5']:.2f}  mrr {s['mrr']}  content {s['content_chars_p50']:.0f} Zeichen")

    for stage in ("search", "rank"):
        xs = [t[stage] / 1000 for t in tims]
        print(f"  Eigen {stage:6} p50 {pct(xs, 0.5)}s  p95 {pct(xs, 0.95)}s")

    PRIVATE.mkdir(parents=True, exist_ok=True)
    (PRIVATE / f"bench-{args.tag}.json").write_text(
        json.dumps({"summary": summary, "own": own}, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
