"""Vergleich minisearch vs. laufendes SearXNG auf den ersten N Eval-Queries.

Pro Query drei Aufrufe mit je ~1 s Pause (IP schonen):
  A) SearXNG wie app/search.py (format=json, language, alle Engines)
  B) SearXNG nur bing,yahoo
  C) minisearch bing+yahoo, Deadline 700 ms
Aus dem Server-Timing-Header von SearXNG werden Gesamt- und Engine-Zeiten gelesen.

    .venv/bin/python experiments/bench_minisearch.py [N]
"""

import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))
import minisearch  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
SEARX = "http://127.0.0.1:8888/search"
PAUSE = 1.0


def server_timing(h: str) -> dict:
    out = {}
    for part in h.split(","):
        name, _, dur = part.strip().partition(";dur=")
        if dur:
            out[name] = float(dur)
    return out


def searx(client, q, lang, engines=None):
    p = {"q": q, "format": "json", "pageno": 1}
    if lang:
        p["language"] = lang
    if engines:
        p["engines"] = engines
    t = time.perf_counter()
    r = client.get(SEARX, params=p)
    wall = (time.perf_counter() - t) * 1000
    d = r.json()
    st = server_timing(r.headers.get("server-timing", ""))
    eng = {k.split("_", 2)[2]: v for k, v in st.items() if k.startswith("total_")}
    load = {k.split("_", 2)[2]: v for k, v in st.items() if k.startswith("load_")}
    return {"wall": wall, "server_total": st.get("total"), "engine_total": eng, "engine_load": load,
            "urls": [x["url"] for x in d.get("results", [])],
            "unresponsive": d.get("unresponsive_engines", [])}


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, round(p / 100 * (len(xs) - 1)))]


def overlap(a, b, k=10):
    A = {minisearch.norm_url(u) for u in a[:k]}
    B = {minisearch.norm_url(u) for u in b[:k]}
    return len(A & B) / max(1, min(len(A), len(B)))


async def main(n):
    qs = [json.loads(l) for l in (ROOT / "eval/queries.jsonl").read_text().splitlines()[:n]]
    sess = minisearch.make_client()
    await minisearch.search("wetter berlin", "de", client=sess)  # Warmup: TLS + Yahoo-YBV
    hc = httpx.Client(timeout=5)
    rows = []
    for q in qs:
        lang = q.get("lang", "")
        a = searx(hc, q["query"], lang)
        time.sleep(PAUSE)
        b = searx(hc, q["query"], lang, "bing,yahoo")
        time.sleep(PAUSE)
        c = await minisearch.search(q["query"], lang, client=sess)
        time.sleep(PAUSE)
        mu = [r["url"] for r in c["results"]]
        row = {"id": q["id"], "A": a, "B": b, "C": {k: v for k, v in c.items() if k != "results"},
               "n_A": len(a["urls"]), "n_B": len(b["urls"]), "n_C": len(mu),
               "ov10_A": overlap(a["urls"], mu), "ov10_B": overlap(b["urls"], mu),
               "ovall_B": overlap(b["urls"], mu, 100)}
        rows.append(row)
        print(f'{q["id"]:12s} A {a["wall"]:6.0f}ms n{row["n_A"]:3d} | B {b["wall"]:6.0f}ms n{row["n_B"]:3d} | '
              f'C {c["total_ms"]:6.0f}ms n{row["n_C"]:3d} {c["timing_ms"]} {c["errors"]} | '
              f'ov10 A {row["ov10_A"]:.2f} B {row["ov10_B"]:.2f}', flush=True)
    await sess.close()

    def summ(xs):
        return f"p50 {statistics.median(xs):.0f} / p95 {pct(xs, 95):.0f} / max {max(xs):.0f}"

    print("\nA SearXNG alle   wall", summ([r["A"]["wall"] for r in rows]))
    print("A server_total     ", summ([r["A"]["server_total"] for r in rows]))
    print("A Overhead (server_total - langsamste Engine)",
          summ([r["A"]["server_total"] - max(r["A"]["engine_total"].values() or [0]) for r in rows]))
    print("A Overhead (wall - server_total)", summ([r["A"]["wall"] - r["A"]["server_total"] for r in rows]))
    for e in ("bing", "yahoo", "wikipedia", "duckduckgo"):
        v = [r["A"]["engine_total"][e] for r in rows if e in r["A"]["engine_total"]]
        ld = [r["A"]["engine_load"][e] for r in rows if e in r["A"]["engine_load"]]
        if v:
            print(f"A engine {e:10s} total", summ(v), "| http", summ(ld), f"| n={len(v)}")
    print("B SearXNG bing,yahoo wall", summ([r["B"]["wall"] for r in rows]))
    print("C minisearch     total", summ([r["C"]["total_ms"] for r in rows]))
    for e in ("bing", "yahoo"):
        v = [r["C"]["timing_ms"][e] for r in rows if r["C"]["timing_ms"].get(e)]
        print(f"C engine {e:10s}", summ(v), f"| n={len(v)}")
    print("Treffer Ø A/B/C", *(round(statistics.mean(r[k] for r in rows), 1) for k in ("n_A", "n_B", "n_C")))
    print("Overlap top10 C vs A", round(statistics.mean(r["ov10_A"] for r in rows), 2),
          "| C vs B", round(statistics.mean(r["ov10_B"] for r in rows), 2),
          "| alle C vs B", round(statistics.mean(r["ovall_B"] for r in rows), 2))
    print("Deadline-Ausfaelle C", sum(1 for r in rows if r["C"]["errors"]),
          [r["C"]["errors"] for r in rows if r["C"]["errors"]])
    print("Unresponsive A", [r["A"]["unresponsive"] for r in rows][:3])
    (ROOT / "experiments/bench_minisearch_result.json").write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 15))
