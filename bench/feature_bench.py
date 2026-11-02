"""Night-2 benchmark: the new features against the old release, on fixed test sets.

    PYTHONPATH=<src of the version> python bench/feature_bench.py {failures,pdfs,shops,region} --label neu

Writes one JSON per set and label to OUT/features/ (BENCH_OUT, default bench/out). Run it
once with the old code (origin/main) and once with the new; `--compare` prints the table.

failures  every URL of data/failures.jsonl through read_pages: page read? which reason?
pdfs      every PDF of data/pdfs.jsonl: text read, answer pattern found, seconds
shops     every page of data/shops.jsonl: product price/currency/gtin as annotated
region    every question of data/region.jsonl, searched without and with its language
          and country: answer pattern in the top 5 hits (DuckDuckGo, 4 s apart)
"""
from __future__ import annotations

import argparse
import inspect
import json
import re
import time

from common import DATA, OUT, answer_in

FEATURES = OUT / "features"


def rows(name: str):
    path = DATA / f"{name}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def read_one(url: str):
    """(doc or None, failure or None, seconds) with whatever read API this version has."""
    from searchify.extract import reader

    t = time.time()
    docs, failed = reader.read_pages([url], "text")
    return (docs[0] if docs else None), (failed[0] if failed else None), round(time.time() - t, 2)


def bench_failures():
    out = []
    for r in rows("failures"):
        doc, fail, sec = read_one(r["url"])
        out.append({"url": r["url"], "expect": r["expect"], "read": doc is not None,
                    "reason": (fail or {}).get("reason"), "error": (fail or {}).get("error"),
                    "seconds": sec})
    return out


def bench_pdfs():
    out = []
    for r in rows("pdfs"):
        doc, fail, sec = read_one(r["url"])
        text = (doc or {}).get("content") or ""
        out.append({"url": r["url"], "chars": len(text), "found": bool(text) and answer_in(r["answer_re"], text),
                    "pages": (doc or {}).get("page_count"), "error": (fail or {}).get("error"), "seconds": sec})
    return out


def _num(x):
    try:
        return round(float(str(x).replace(",", ".")), 2)
    except (TypeError, ValueError):
        return None


def bench_shops():
    out = []
    for r in rows("shops"):
        if r.get("note") == "blocked":
            continue
        doc, fail, sec = read_one(r["url"])
        p = (doc or {}).get("product") or {}
        gtins = [str(g) for g in (p.get("gtin") or [])]
        out.append({"url": r["url"], "system": r.get("system"), "read": doc is not None,
                    "has_product": bool(p),
                    "price_ok": r.get("price") is not None and _num(p.get("price")) == _num(r.get("price")),
                    "currency_ok": bool(r.get("currency")) and p.get("priceCurrency") == r.get("currency"),
                    "gtin_ok": bool(r.get("gtin")) and str(r.get("gtin")) in gtins, "seconds": sec})
    return out


def bench_region():
    from searchify.search import search
    from searchify.search.duckduckgo import ddgs_search

    # the old release has no country filter; give it what it understands (language only)
    takes_country = "country" in inspect.signature(ddgs_search).parameters
    out = []
    for r in rows("region"):
        res = {"id": r["id"], "lang": r["lang"], "country": r.get("country")}
        for mode in ("default", "local"):
            filters = {}
            if mode == "local":
                filters = {"language": r["lang"]}
                if r.get("country") and takes_country:
                    filters["country"] = r["country"]
            try:
                hits = search(r["query"], 5, backend="duckduckgo", **filters)
                text = " ".join(f"{h.get('title', '')} {h.get('content', '')}" for h in hits)
                res[mode] = {"hits": len(hits), "found": answer_in(r["answer_re"], text)}
            except Exception as exc:  # old versions reject unknown filters, DDG errors
                res[mode] = {"error": re.sub(r"\s+", " ", str(exc))[:80]}
            time.sleep(4)
        out.append(res)
    return out


def summary(name: str, data):
    n = max(len(data), 1)
    if name == "failures":
        ok = [d for d in data if d["expect"] == "ok"]
        bad = [d for d in data if d["expect"] != "ok"]
        return {"ok_read": f"{sum(d['read'] for d in ok)}/{len(ok)}",
                "failures_with_reason": f"{sum(bool(d['reason']) for d in bad)}/{len(bad)}",
                "median_s": sorted(d["seconds"] for d in data)[len(data) // 2] if data else None}
    if name == "pdfs":
        return {"read": f"{sum(d['chars'] > 0 for d in data)}/{n}", "answer_found": f"{sum(d['found'] for d in data)}/{n}",
                "median_s": sorted(d["seconds"] for d in data)[len(data) // 2] if data else None}
    if name == "shops":
        return {k: f"{sum(d[k] for d in data)}/{n}" for k in ("read", "has_product", "price_ok", "currency_ok", "gtin_ok")}
    if name == "region":
        res = {}
        for mode in ("default", "local"):
            done = [d[mode] for d in data if "error" not in d[mode]]
            res[mode] = f"{sum(d['found'] for d in done)}/{len(done)} (errors {n - len(done)})"
        return res
    return {}


def compare() -> None:
    for name in ("failures", "pdfs", "shops", "region"):
        for path in sorted(FEATURES.glob(f"{name}.*.json")):
            label = path.stem.split(".", 1)[1]
            print(f"{name:9} {label:5}", json.dumps(summary(name, json.loads(path.read_text())), ensure_ascii=False))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("set", nargs="?", choices=["failures", "pdfs", "shops", "region"])
    ap.add_argument("--label", default="neu")
    ap.add_argument("--compare", action="store_true")
    args = ap.parse_args()
    if args.compare:
        compare()
        return
    data = {"failures": bench_failures, "pdfs": bench_pdfs, "shops": bench_shops, "region": bench_region}[args.set]()
    FEATURES.mkdir(parents=True, exist_ok=True)
    (FEATURES / f"{args.set}.{args.label}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1))
    print(args.set, args.label, json.dumps(summary(args.set, data), ensure_ascii=False))


if __name__ == "__main__":  # extraction uses worker processes, so the guard is required
    main()
# Stand: 03.11.2026
