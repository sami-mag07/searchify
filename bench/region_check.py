"""Region check: do Dutch questions find their answer better with nl-nl than with wt-wt?

    python bench/region_check.py [--runs 3] [--gap 4]

Five Dutch questions, each searched `runs` times per region through the DuckDuckGo
backend, with a pause between searches. A question counts as found when its answer
pattern appears in title or snippet of the top 5. Errors (rate limit, timeout) are
counted on their own and never count as a miss. Writes bench/region_check.md.
"""
from __future__ import annotations

import argparse
import re
import time
from pathlib import Path

from searchify.search import SearchBackendError
from searchify.search.duckduckgo import ddgs_search

QUESTIONS = [
    ("Wat is de hoofdstad van Nederland?", r"Amsterdam"),
    ("Wanneer is Koningsdag in Nederland?", r"27 april"),
    ("Hoeveel inwoners heeft Nederland?", r"\b1[78][,.]\d|\b1[78] miljoen|17\.\d{3}\.\d{3}|18\.\d{3}\.\d{3}"),
    ("Wie is de directeur van het Rijksmuseum?", r"Taco Dibbits"),
    ("Hoe hoog is de Domtoren in Utrecht?", r"112[, ]?\d*\s*(m|meter)|\b112\b"),
]
REGIONS = ("wt-wt", "nl-nl")


def one(question: str, pattern: str, region: str):
    lang = None if region == "wt-wt" else "nl"
    try:
        hits = ddgs_search(question, 5, language=lang)
    except SearchBackendError as exc:
        return "error", 0, str(exc).split(":", 1)[0]
    found = any(re.search(pattern, f"{h['title']} {h['content']}", re.I) for h in hits[:5])
    return ("found" if found else "missed"), len(hits), ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--gap", type=float, default=4.0)
    args = ap.parse_args()
    stats = {r: {"found": 0, "missed": 0, "error": 0, "hits": 0, "codes": {}} for r in REGIONS}
    per_q = []
    for question, pattern in QUESTIONS:
        row = {}
        for region in REGIONS:
            row[region] = 0
            for _ in range(args.runs):
                status, n, code = one(question, pattern, region)
                st = stats[region]
                st[status] += 1
                st["hits"] += n
                if code:
                    st["codes"][code] = st["codes"].get(code, 0) + 1
                row[region] += status == "found"
                time.sleep(args.gap)
        per_q.append((question, row))
        print(question, row, flush=True)

    lines = [
        "# Region check: Dutch questions, wt-wt vs nl-nl",
        "",
        f"{len(QUESTIONS)} questions x {args.runs} runs per region, {args.gap:g} s gap, "
        "DuckDuckGo via ddgs. Found = answer pattern in title or snippet of the top 5.",
        "",
        "| Region | Found | Missed | Errors | Hits (sum) |",
        "|---|---|---|---|---|",
    ]
    for r in REGIONS:
        s = stats[r]
        codes = ", ".join(f"{k} {v}" for k, v in s["codes"].items())
        err = f"{s['error']}" + (f" ({codes})" if codes else "")
        lines.append(f"| {r} | {s['found']} | {s['missed']} | {err} | {s['hits']} |")
    lines += ["", f"Found per question (of {args.runs} runs):", "", "| Question | wt-wt | nl-nl |", "|---|---|---|"]
    for q, row in per_q:
        lines.append(f"| {q} | {row['wt-wt']} | {row['nl-nl']} |")
    Path(__file__).with_name("region_check.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
