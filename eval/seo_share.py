#!/usr/bin/env python3
"""SEO-/Kommerz-Anteil in den Top 3, QUALITY_PRIOR aus gegen an (Replay, offline).

Unabhaengig vom Blindtest und von app/quality.py gemessen: Eine Seite zaehlt als
kommerziell, wenn ihr (aufgezeichnetes) HTML Affiliate-Links oder Werbenetz-Skripte
enthaelt. Das Affiliate-Merkmal folgt Bevendorff et al., "Is Google Getting Worse?
A Longitudinal Investigation of SEO Spam in Search Engines" (ECIR 2024), die
Affiliate-Links als Marker fuer SEO-optimierte Produkt-/Ratgeberseiten nutzen.
Werbenetze (AdSense, Ezoic, Mediavine, Raptive, Taboola, Outbrain) sind der zweite,
breitere Marker (trifft auch Nachrichtenseiten).

Fragen: queries_big.jsonl + queries.jsonl (Faktenfragen) und die Recherche-Kassette
Feld "queries" (Stichwort-Suchen). Seiten ohne Aufnahme zaehlen als "unbekannt".

  seo_share.py [--out FILE]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
EVALS = Path.home() / ".claude/skills/research-agent/evals"

AFFILIATE = re.compile(
    r"""(?:amazon\.[a-z.]+/[^"'\s>]*[?&]tag=|amzn\.to/|awin1\.com|prf\.hn/|shareasale\.com/r|"""
    r"""click\.linksynergy\.com|go\.skimresources\.com|redirect\.viglink\.com|"""
    r"""(?:anrdoezrs|dpbolvw|jdoqocy|kqzyfj|tkqlhce)\.net|rel=["'][^"']*sponsored)""",
    re.IGNORECASE,
)
ADS = re.compile(
    r"(?:adsbygoogle|googlesyndication\.com|data-ad-slot|ezoic|mediavine|adthrive|raptive|"
    r"taboola|outbrain)",
    re.IGNORECASE,
)


def page_html(url: str):
    base = os.path.join(os.environ["REPLAY_DIR"], "pages", hashlib.sha1(url.encode("utf-8")).hexdigest())
    if os.path.exists(base + ".html"):
        return open(base + ".html", encoding="utf-8", errors="replace").read()
    return None


def label(url: str):
    html = page_html(url)
    if html is None:
        return None
    return {"aff": bool(AFFILIATE.search(html)), "ads": bool(ADS.search(html))}


def questions():
    out = []
    for f in ("queries_big.jsonl", "queries.jsonl"):
        for line in open(HERE / f, encoding="utf-8"):
            if line.strip():
                q = json.loads(line)
                out.append(("fakten", q["query"], q.get("lang")))
    tasks = json.load(open(EVALS / "tasks.json", encoding="utf-8"))["tasks"]
    for t in tasks:
        for q in t.get("queries", []):
            out.append(("recherche", q, None))
    return out


def run(qs, top=3):
    from app.main import run_search
    from app.schema import SearchRequest

    rows = []
    for kind, q, lang in qs:
        resp = run_search(SearchRequest(query=q, max_results=10, include_answer=False, language=lang))
        for r in resp.results[:top]:
            rows.append({"kind": kind, "query": q, "url": r.url, "label": label(r.url)})
    return rows


def share(rows, kind=None):
    sel = [r for r in rows if kind in (None, r["kind"])]
    known = [r for r in sel if r["label"] is not None]
    n = len(known) or 1
    return {
        "top3": len(sel), "bekannt": len(known),
        "affiliate": round(sum(r["label"]["aff"] for r in known) / n, 3),
        "werbung": round(sum(r["label"]["ads"] for r in known) / n, 3),
        "eins_von_beiden": round(sum(r["label"]["aff"] or r["label"]["ads"] for r in known) / n, 3),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    os.environ["SEARCH_CACHE_TTL_S"] = "0"
    os.environ["PAGE_CACHE_TTL_S"] = "0"
    os.environ.setdefault("REPLAY_DIR", str(HERE / "private" / "replay"))
    os.environ.setdefault("FETCH_BUDGET_MS", "3000")
    os.environ.setdefault("SPLIT_TAIL_MS", "3000")
    sys.path.insert(0, str(ROOT))
    from app.extract import warm_up

    warm_up()
    qs = questions()
    out = {}
    for state in ("0", "1"):
        os.environ["QUALITY_PRIOR"] = state  # get_settings() liest die Umgebung je Aufruf
        rows = run(qs)
        out["an" if state == "1" else "aus"] = {
            "gesamt": share(rows), "fakten": share(rows, "fakten"), "recherche": share(rows, "recherche"),
            "rows": rows,
        }
        print("QUALITY_PRIOR=" + state, json.dumps({k: v for k, v in out["an" if state == "1" else "aus"].items()
                                                   if k != "rows"}, ensure_ascii=False))
    if a.out:
        Path(a.out).write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
