"""Release gate, point 4: searchify against paid services on the same fixed lists.

    PYTHONPATH=src python bench/paid_bench.py --keys <path to .env> [shops pdfs region]

Reads TAVILY_API_KEY and JINA_API_KEY from the given file only, never from the
environment, and removes both from the environment first, so searchify itself runs
without its Tavily fallback. A service without a key is left out. Writes
OUT/paid/<set>.json and prints a summary.

shops   each product page: read? price somewhere in the text? (searchify also: price as field)
pdfs    each PDF: text read? answer pattern found?
region  each regional question: answer pattern in the top 5 hits (searchify with language
        and country on DuckDuckGo, Tavily search with the country where it takes one)
Credits: Tavily extract 1 per 5 pages, Tavily search 1 per query; Jina Reader by tokens.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import time
from pathlib import Path

import httpx

from common import DATA, OUT, answer_in

PAID = OUT / "paid"
COUNTRY = {"NL": "netherlands", "BE": "belgium", "DE": "germany", "FR": "france", "CH": "switzerland",
           "ES": "spain"}


def keys_from(path: str) -> dict:
    found = {}
    for line in Path(path).expanduser().read_text(encoding="utf-8").splitlines():
        name, _, value = line.partition("=")
        if name.strip() in ("TAVILY_API_KEY", "JINA_API_KEY") and value.strip():
            found[name.strip()] = value.strip().strip("'\"")
    return found


def rows(name: str):
    return [json.loads(x) for x in (DATA / f"{name}.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]


def price_pattern(price: str) -> str:
    """'1299.5' matches 1299,50 / 1.299,50 / 1,299.50; '100' also matches 100,00 and 100.-"""
    value = float(str(price).replace(",", "."))
    whole, dec = f"{value:.2f}".split(".")
    groups = [whole[max(0, i - 3):i] for i in range(len(whole), 0, -3)][::-1]
    head = r"[.,\s  ']?".join(groups)
    tail = rf"(?:[.,]{dec})?" if dec == "00" else rf"[.,]{dec}"
    return rf"(?<![\d.,]){head}{tail}(?![\d])"


# ------------------------------------------------------------------ readers
def read_searchify(url: str):
    from searchify import extract_report

    t = time.time()
    rep = extract_report([url])
    doc = rep["results"][0] if rep["results"] else None
    return (doc or {}).get("content") or "", doc, round(time.time() - t, 2)


def read_tavily(url: str, key: str):
    t = time.time()
    try:
        r = httpx.post("https://api.tavily.com/extract", timeout=90,
                       headers={"Authorization": f"Bearer {key}"},
                       json={"urls": [url], "extract_depth": "basic", "format": "markdown"})
        res = (r.json().get("results") or [{}])[0] if r.status_code == 200 else {}
    except httpx.HTTPError:
        res = {}
    return res.get("raw_content") or "", round(time.time() - t, 2)


def read_jina(url: str, key: str):
    t = time.time()
    try:
        r = httpx.get(f"https://r.jina.ai/{url}", timeout=90, headers={"Authorization": f"Bearer {key}"})
        text = r.text if r.status_code == 200 else ""
    except httpx.HTTPError:
        text = ""
    return text, round(time.time() - t, 2)


def readers(keys):
    return [(name, fn) for name, fn in (("tavily", read_tavily), ("jina", read_jina))
            if f"{name.upper()}_API_KEY" in keys]


# ------------------------------------------------------------------ sets
def bench_shops(keys):
    out = []
    for r in rows("shops"):
        if r.get("note") == "blocked" or not r.get("price"):
            continue
        pat = price_pattern(r["price"])
        text, doc, sec = read_searchify(r["url"])
        prod = (doc or {}).get("product") or {}
        field = prod.get("price") is not None and abs(float(str(prod["price"]).replace(",", ".")) - float(r["price"])) < 0.01
        row = {"url": r["url"], "searchify": {"read": bool(text), "price_text": bool(re.search(pat, text)),
                                              "price_field": field, "s": sec}}
        for name, fn in readers(keys):
            text, sec = fn(r["url"], keys[f"{name.upper()}_API_KEY"])
            row[name] = {"read": bool(text.strip()), "price_text": bool(re.search(pat, text)), "s": sec}
        out.append(row)
    return out


def bench_pdfs(keys):
    out = []
    for r in rows("pdfs"):
        text, _, sec = read_searchify(r["url"])
        row = {"url": r["url"], "searchify": {"read": bool(text), "found": bool(text) and answer_in(r["answer_re"], text), "s": sec}}
        for name, fn in readers(keys):
            text, sec = fn(r["url"], keys[f"{name.upper()}_API_KEY"])
            row[name] = {"read": bool(text.strip()), "found": bool(text) and answer_in(r["answer_re"], text), "s": sec}
        out.append(row)
    return out


def bench_region(keys):
    from searchify.search import search

    out = []
    for r in rows("region"):
        row = {"id": r["id"]}
        t = time.time()
        try:
            hits = search(r["query"], 5, backend="duckduckgo", language=r["lang"], country=r.get("country"))
        except Exception:  # a DDG error counts as not found
            hits = []
        text = " ".join(f"{h.get('title', '')} {h.get('content', '')}" for h in hits)
        row["searchify"] = {"found": answer_in(r["answer_re"], text), "s": round(time.time() - t, 2)}
        t = time.time()
        body = {"query": r["query"], "max_results": 5, "search_depth": "basic", "topic": "general"}
        if COUNTRY.get(r.get("country")):
            body["country"] = COUNTRY[r["country"]]
        try:
            resp = httpx.post("https://api.tavily.com/search", timeout=60, json=body,
                              headers={"Authorization": f"Bearer {keys['TAVILY_API_KEY']}"})
            res = resp.json().get("results", []) if resp.status_code == 200 else []
        except httpx.HTTPError:
            res = []
        text = " ".join(f"{h.get('title', '')} {h.get('content', '')}" for h in res)
        row["tavily"] = {"found": answer_in(r["answer_re"], text), "s": round(time.time() - t, 2)}
        out.append(row)
        time.sleep(4)  # DuckDuckGo rate limit
    return out


def summary(name: str, data) -> dict:
    res = {}
    for tool in ("searchify", "tavily", "jina"):
        got = [d[tool] for d in data if tool in d]
        if not got:
            continue
        line = {k: f"{sum(bool(g[k]) for g in got)}/{len(got)}" for k in got[0] if k != "s"}
        line["median_s"] = statistics.median(g["s"] for g in got)
        res[tool] = line
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("sets", nargs="*", default=["shops", "pdfs", "region"])
    ap.add_argument("--keys", required=True)
    args = ap.parse_args()
    for name in ("TAVILY_API_KEY", "JINA_API_KEY"):
        os.environ.pop(name, None)  # searchify must not use its Tavily fallback here
    keys = keys_from(args.keys)
    if "TAVILY_API_KEY" not in keys:
        raise SystemExit(f"no TAVILY_API_KEY in {args.keys}")
    print("services:", ", ".join(["searchify"] + [n for n, _ in readers(keys)]), flush=True)
    PAID.mkdir(parents=True, exist_ok=True)
    for name in args.sets:
        data = {"shops": bench_shops, "pdfs": bench_pdfs, "region": bench_region}[name](keys)
        (PAID / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print(name, json.dumps(summary(name, data), ensure_ascii=False), flush=True)


if __name__ == "__main__":  # extraction uses worker processes, so the guard is required
    main()
# Stand: 03.11.2026
