#!/usr/bin/env python3
"""Blindtest Quellenqualitaet: Top-3 von Tavily (Cache) und vom eigenen Dienst
(live ueber HTTP) je Frage mischen und anonymisieren. Der Richter sieht nur
items.json (ohne Herkunft); die Zuordnung liegt getrennt in key.json.
Ausgaben in eval/private/blind/ (nicht veroeffentlichen)."""

from __future__ import annotations

import json
import random
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "private" / "blind"
BASE = "http://127.0.0.1:8000"


def own_top3(query: str, lang: str) -> list:
    body = json.dumps({"query": query, "max_results": 3, "include_answer": False, "language": lang}).encode()
    req = urllib.request.Request(BASE + "/search", body, {"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)
    return [{"url": x["url"], "title": x.get("title") or "", "content": x.get("content") or ""} for x in data["results"]][:3]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    queries = [json.loads(l) for l in open(HERE / "queries.jsonl", encoding="utf-8") if l.strip()]
    cache = json.loads((HERE / "private" / "tavily_cache.json").read_text(encoding="utf-8"))
    rng = random.Random(20261011)
    items, key = [], {}
    for q in queries:
        tav = [{"url": r["url"], "title": "", "content": r.get("content") or ""}
               for r in cache[q["query"] + "|noanswer"]["results"][:3]]
        own = own_top3(q["query"], q.get("lang"))
        time.sleep(1)
        pool = [("tavily", r) for r in tav] + [("eigen", r) for r in own]
        rng.shuffle(pool)
        for side, r in pool:
            iid = f"{q['id']}-{rng.randrange(16**6):06x}"
            key[iid] = side
            items.append({"id": iid, "frage": q["query"], "url": r["url"],
                          "titel": r["title"], "auszug": r["content"][:700]})
    (OUT / "items.json").write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT / "key.json").write_text(json.dumps(key, indent=1), encoding="utf-8")
    print(len(items), "Items ->", OUT / "items.json")


if __name__ == "__main__":
    main()
