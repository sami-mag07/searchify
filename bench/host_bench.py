"""Host memory benchmark: the same pages read again and again, with and without the memory.

    PYTHONPATH=src python bench/host_bench.py [--runs 5] [--sets failures,shops]
    PYTHONPATH=src python bench/host_bench.py --report

Each run is its own process, so the page cache is empty every time and only the host
memory (its own file under OUT/hosts/) carries over. Arm "off" runs with
SEARCHIFY_HOST_MEMORY=0, arm "on" starts from an empty memory file. Every page of the
sets is read once per run with reader.read_pages; seconds, stage, skipped rungs and
whether the page came back are written to OUT/hosts/<arm>-<run>.json. The report
compares the runs: pages read, total and p50/p95 seconds, rungs skipped.
Browser stages need the [browser] extra; Camoufox needs READER_STEALTH=1 and
CAMOUFOX_PYTHON.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time

from common import DATA, OUT

HOSTS = OUT / "hosts"


def urls(sets):
    out = []
    for name in sets:
        for line in (DATA / f"{name}.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line)["url"])
    return list(dict.fromkeys(out))


def one_run(label: str, sets) -> None:
    from searchify.extract import reader

    rows = []
    for url in urls(sets):
        t = time.time()
        docs, failed = reader.read_pages([url], "text")
        doc = docs[0] if docs else {}
        rows.append({"url": url, "seconds": round(time.time() - t, 2), "read": bool(docs),
                     "stage": doc.get("stage"), "skipped": doc.get("skipped") or [],
                     "chars": len(doc.get("content") or ""),
                     "reason": (failed[0] if failed else {}).get("reason")})
        print(label, rows[-1]["seconds"], rows[-1]["stage"], url[:70], flush=True)
    HOSTS.mkdir(parents=True, exist_ok=True)
    (HOSTS / f"{label}.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")


def pct(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int(round(q * (len(values) - 1))))] if values else 0.0


def report() -> None:
    lines = ["| Lauf | Seiten gelesen | Summe s | p50 s | p95 s | Stufen übersprungen |",
             "|---|---|---|---|---|---|"]
    for path in sorted(HOSTS.glob("*.json"), key=lambda p: (p.stem.split("-")[0], int(p.stem.split("-")[1]))):
        rows = json.loads(path.read_text(encoding="utf-8"))
        secs = [r["seconds"] for r in rows]
        skipped = sum(len(r["skipped"]) for r in rows)
        lines.append(f"| {path.stem} | {sum(r['read'] for r in rows)}/{len(rows)} | {sum(secs):.0f} | "
                     f"{statistics.median(secs):.2f} | {pct(secs, 0.95):.2f} | {skipped} |")
    text = "\n".join(lines)
    (HOSTS / "report.md").write_text(text + "\n", encoding="utf-8")
    print(text)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--sets", default="failures,shops")
    ap.add_argument("--arms", default="off,on")
    ap.add_argument("--one", default="", help=argparse.SUPPRESS)
    ap.add_argument("--report", action="store_true")
    args = ap.parse_args()
    sets = args.sets.split(",")
    if args.one:
        one_run(args.one, sets)
        return
    if args.report:
        report()
        return
    memory = HOSTS / "memory.sqlite3"
    for arm in args.arms.split(","):
        if arm == "on" and memory.exists():
            memory.unlink()
        for run in range(1, args.runs + 1):
            env = dict(os.environ, SEARCHIFY_HOST_MEMORY="1" if arm == "on" else "0",
                       SEARCHIFY_HOST_MEMORY_PATH=str(memory))
            subprocess.run([sys.executable, __file__, "--one", f"{arm}-{run}", "--sets", args.sets],
                           env=env, check=False)
    report()


if __name__ == "__main__":
    main()
# Stand: 10.11.2026
