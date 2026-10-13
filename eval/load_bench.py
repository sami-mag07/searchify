#!/usr/bin/env python3
"""Lasttest: Latenz und Fehlerquote von POST /search bei 1, 5 und 10 gleichzeitigen
Anfragen, dazu kalt gegen Cache (dieselben Fragen sofort wiederholt). Jede Stufe
nutzt andere Fragen aus queries_big.jsonl, damit der Cache die Stufen nicht verfaelscht.
Ergebnis: eval/private/load-<datum>.json"""

from __future__ import annotations

import datetime
import json
import statistics
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
BASE = "http://127.0.0.1:8000"


def one(q: dict) -> tuple:
    body = json.dumps({"query": q["query"], "max_results": 10, "include_answer": False}).encode()
    req = urllib.request.Request(BASE + "/search", body, {"content-type": "application/json"})
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            n = len(json.load(r)["results"])
        return time.perf_counter() - t, n > 0
    except Exception:  # noqa: BLE001
        return time.perf_counter() - t, False


def stage(queries: list, conc: int) -> dict:
    t0 = time.perf_counter()
    with ThreadPoolExecutor(conc) as pool:
        res = list(pool.map(one, queries))
    wall = time.perf_counter() - t0
    lat = sorted(r[0] for r in res)
    return {
        "concurrency": conc, "n": len(res), "ok": sum(r[1] for r in res),
        "p50": round(statistics.median(lat), 3), "p95": round(lat[int(0.95 * (len(lat) - 1))], 3),
        "max": round(lat[-1], 3), "throughput_per_s": round(len(res) / wall, 2), "latencies": [round(x, 3) for x in lat],
    }


def main() -> None:
    qs = [json.loads(l) for l in open(HERE / "queries_big.jsonl", encoding="utf-8") if l.strip()]
    out = {"created": datetime.datetime.now().isoformat(timespec="seconds"), "stages": []}
    plan = [(1, qs[0:10]), (5, qs[10:30]), (10, qs[30:50])]
    for conc, part in plan:
        out["stages"].append(dict(stage(part, conc), label=f"{conc} gleichzeitig (kalt)"))
        time.sleep(2)
    # Cache: die Fragen der ersten Stufe sofort wiederholen
    out["stages"].append(dict(stage(qs[0:10], 1), label="1 gleichzeitig (Cache)"))
    for s in out["stages"]:
        print(f"{s['label']:26} n={s['n']} ok={s['ok']} p50={s['p50']}s p95={s['p95']}s max={s['max']}s {s['throughput_per_s']}/s")
    path = HERE / "private" / f"load-{datetime.date.today()}.json"
    path.write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
