"""Link filter on shop pages: does the price paragraph survive and rank, with and without it?

    PYTHONPATH=src python bench/shop_links.py [--shares 1.0,0.5]

For every page of data/shops.jsonl (annotated name and price) it reads the page once, asks
"How much does <name> cost?", splits the page like passages() with each max_link_share,
reranks and checks whether the price appears in the first 4800 characters (8 passages of
600) and at which rank. Writes OUT/shop_links.json and prints a table.
"""
from __future__ import annotations

import argparse
import json
import re

from common import DATA, OUT


def price_re(price: str) -> re.Pattern:
    whole, _, cents = price.partition(".")
    cents = cents.rstrip("0")
    tail = rf"[.,]{cents}0?" if cents else r"(?:[.,]0{1,2}|,-)?"
    return re.compile(rf"(?<![\d.,]){re.escape(whole)}{tail}(?![\d])")


def main() -> None:
    from searchify.extract import reader
    from searchify.passages import split_paragraphs, trim
    from searchify.rerank import rerank

    ap = argparse.ArgumentParser()
    ap.add_argument("--shares", default="1.0,0.5")
    args = ap.parse_args()
    shares = [float(x) for x in args.shares.split(",")]
    rows = [json.loads(line) for line in (DATA / "shops.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    out = []
    for r in rows:
        docs, _failed = reader.read_pages([r["url"]], "text")
        if not docs or not r.get("price"):
            continue
        doc, rx = docs[0], price_re(str(r["price"]))
        query = f"How much does {r['name']} cost?"
        row = {"url": r["url"], "price": r["price"]}
        for share in shares:
            items = split_paragraphs([doc], 12, max_link_share=share)
            ranked = rerank(query, items) if items else []
            used, rank = 0, None
            for pos, it in enumerate(ranked, 1):
                text = trim(it["content"], 600)
                used += len(text)
                if used > 4800:
                    break
                if rx.search(text):
                    rank = pos
                    break
            row[str(share)] = {"paragraphs": len(items), "rank": rank}
        out.append(row)
        print(row, flush=True)
    (OUT / "shop_links.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    print(f"\n{len(out)} shop pages with a price")
    for share in shares:
        ranks = [o[str(share)]["rank"] for o in out]
        found = sum(r is not None for r in ranks)
        mrr = sum(1 / r for r in ranks if r) / max(len(out), 1)
        paras = sum(o[str(share)]["paragraphs"] for o in out)
        print(f"max_link_share {share}: price in 4800 chars {found}/{len(out)}, MRR {mrr:.2f}, "
              f"paragraphs scored {paras}")


if __name__ == "__main__":
    main()
# Stand: 10.11.2026
