#!/usr/bin/env python3
"""Engine-Messung je Regal (Sami 15.10.2026, Grundlage fuer den Bandit).

Aus den aufgezeichneten SearXNG-Antworten (eval/private/replay/searxng, je Engine und
Seite getrennt) zu den Suchanfragen in tasks.json: Anteil der Gold-Domains der Aufgabe,
die eine Engine in ihren Treffern hat (site@alle), und MRR der ersten Gold-Domain.
Dazu die Originale aus der Kassette vom 01.10. (tavily, serper, exa) zum Vergleich.
Nur Funde bis 01.10. (tasks.json), also auch fuer den Bandit-Start erlaubt.
"""
import glob
import json
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent.parent
EVALS = Path.home() / ".claude/skills/research-agent/evals"


def site(u):
    return (urlsplit(u).hostname or "").lower().removeprefix("www.")


def main():
    tasks = json.load(open(EVALS / "tasks.json"))["tasks"]
    by_query = {q.strip().lower(): t for t in tasks for q in t["queries"] + t.get("queries_niche", [])}
    found = defaultdict(lambda: defaultdict(set))  # regal -> engine -> gold sites
    rr = defaultdict(lambda: defaultdict(list))
    n_files = 0
    for path in glob.glob(str(ROOT / "eval/private/replay/searxng/*")):
        try:
            d = json.load(open(path))
        except (OSError, ValueError):
            continue
        t = by_query.get((d.get("query") or "").strip().lower())
        if not t:
            continue
        n_files += 1
        gold = {site(u) for u in t["gold"]}
        per_engine = defaultdict(list)
        for r in d.get("results", []):
            for e in r.get("engines") or [r.get("engine")]:
                per_engine[e].append(r["url"])
        for e, urls in per_engine.items():
            hits = {site(u) for u in urls} & gold
            found[t["id"]][e] |= hits
            first = next((i for i, u in enumerate(urls, 1) if site(u) in gold), None)
            rr[t["id"]][e].append(1 / first if first else 0.0)
    for path in sorted(glob.glob(str(EVALS / "cassettes/2026-10-01_*.json"))):
        for r in json.load(open(path))["runs"]:
            t = next((x for x in tasks if x["id"] == r["task"]), None)
            if not t or r.get("error"):
                continue
            gold = {site(u) for u in t["gold"]}
            urls = [h["url"] for h in r.get("hits", [])]
            found[t["id"]][r["engine"]] |= {site(u) for u in urls} & gold
            first = next((i for i, u in enumerate(urls, 1) if site(u) in gold), None)
            rr[t["id"]][r["engine"]].append(1 / first if first else 0.0)
    out = {}
    engines = sorted({e for v in found.values() for e in v})
    print(f"SearXNG-Aufnahmen zu tasks.json: {n_files}")
    print("| Regal | " + " | ".join(engines) + " | primaer (SearXNG) |")
    print("|---|" + "---|" * (len(engines) + 1))
    for t in tasks:
        gold = {site(u) for u in t["gold"]}
        row = {e: {"site": round(len(found[t["id"]][e]) / len(gold), 3),
                   "mrr": round(sum(rr[t["id"]][e]) / len(rr[t["id"]][e]), 3) if rr[t["id"]][e] else 0.0}
               for e in engines if e in found[t["id"]] or rr[t["id"]].get(e)}
        own = {e: v for e, v in row.items() if e not in ("tavily", "serper", "exa", "donsetch", "hound")}
        primary = max(own, key=lambda e: (own[e]["site"], own[e]["mrr"])) if own else None
        out[t["id"]] = {"engines": row, "primaer": primary}
        cells = [f"{row[e]['site']:.2f}/{row[e]['mrr']:.2f}" if e in row else "-" for e in engines]
        print(f"| {t['id']} | " + " | ".join(cells) + f" | {primary} |")
    dest = ROOT / "eval/private/bibliothek/engines.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    json.dump(out, open(dest, "w"), indent=1)
    print("Werte: Anteil Gold-Domains / MRR erste Gold-Domain. Datei:", dest)


if __name__ == "__main__":
    main()
