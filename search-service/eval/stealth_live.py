#!/usr/bin/env python3
"""Reader live mit und ohne Camoufox-Stufe (Messung 15.10.2026).

Zwei eigene Instanzen: --mit (READER_STEALTH=1, CAMOUFOX_PYTHON gesetzt) und --ohne
(READER_STEALTH=0), sonst gleich. Jede blockierte URL aus browser_bench_urls.json
abwechselnd an beide, lesbar = 200 und mindestens 80 Woerter.
"""
import argparse
import json
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent


def read(base, url):
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(f"{base}/jina/r/{url}", timeout=60) as r:
            body = r.read().decode("utf-8", "replace")
            code = r.status
    except Exception as e:  # noqa: BLE001
        body, code = "", getattr(e, "code", 0)
    return {"code": code, "words": len(body.split()), "sec": round(time.perf_counter() - t0, 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mit", default="http://127.0.0.1:8090")
    ap.add_argument("--ohne", default="http://127.0.0.1:8091")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    urls = json.load(open(HERE / "browser_bench_urls.json"))["blockiert"]
    rows = []
    for i, u in enumerate(urls):
        order = [("mit", a.mit), ("ohne", a.ohne)] if i % 2 == 0 else [("ohne", a.ohne), ("mit", a.mit)]
        row = {"url": u}
        for name, base in order:
            row[name] = read(base, u)
            row[name]["ok"] = row[name]["code"] == 200 and row[name]["words"] >= 80
        rows.append(row)
        print(("+" if row["mit"]["ok"] else "-"), ("+" if row["ohne"]["ok"] else "-"),
              row["mit"]["sec"], row["ohne"]["sec"], u[:80], flush=True)
    summ = {k: {"ok": sum(r[k]["ok"] for r in rows), "n": len(rows),
                "median_s": sorted(r[k]["sec"] for r in rows)[len(rows) // 2],
                "max_s": max(r[k]["sec"] for r in rows)} for k in ("mit", "ohne")}
    json.dump({"rows": rows, "summary": summ}, open(a.out, "w"), indent=1)
    print(json.dumps(summ))


if __name__ == "__main__":
    main()
