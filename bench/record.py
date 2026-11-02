"""Stage 1 (network): record search hits and page text once, so every later stage is offline.

    python bench/record.py [--split all] [--gap 4] [--limit N]

For each question and both query forms (the full question and its keywords) it stores
the top 10 hits and the text of every hit page under out/replay/. Already recorded
questions are skipped, so the stage can be stopped and started again. Search goes
through searchify's real code with the backend named in BENCH_BACKEND (duckduckgo).
"""
from __future__ import annotations

import argparse
import os
import sys
import time

from common import (FORMS, MAX_HITS, load_queries, page_path, query_text, read_json,
                    search_path, stop_at, write_json)


def log(event: str, **fields) -> None:
    print(event, " ".join(f"{k}={v}" for k, v in fields.items()), flush=True)


def search_with_retry(query: str, backend: str, tries: int = 4):
    from searchify.search import SearchBackendError, search

    error = ""
    for attempt in range(tries):
        try:
            hits = search(query, MAX_HITS, backend=backend)
            if hits:
                return hits, ""
            error = "no results"
        except SearchBackendError as exc:
            error = str(exc)[:200]
        time.sleep(10 * (attempt + 1))  # DuckDuckGo rate limits come in bursts
    return [], error


def record_pages(urls) -> int:
    from searchify.extract import read_pages

    todo = [u for u in urls if not page_path(u).exists()]
    if not todo:
        return 0
    docs, failed = read_pages(todo, "text")
    for doc in docs:
        write_json(page_path(doc["url"]), {"url": doc["url"], "title": doc.get("title") or "",
                                           "content": doc.get("content") or ""})
    for fail in failed:
        write_json(page_path(fail["url"]), {"url": fail["url"], "content": "",
                                            "error": str(fail.get("error"))[:200]})
    return len(todo)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="all")
    ap.add_argument("--gap", type=float, default=4.0, help="Seconds between searches.")
    ap.add_argument("--limit", type=int, default=0, help="Only the first N questions.")
    ap.add_argument("--until", default="", help="Stop recording at HH:MM and keep what is there.")
    args = ap.parse_args()
    end = stop_at(args.until)
    backend = os.environ.get("BENCH_BACKEND", "duckduckgo")
    queries = load_queries(args.split)[: args.limit or None]
    missing = 0
    for i, q in enumerate(queries, 1):
        for form in FORMS:
            path = search_path(form, q["id"])
            if path.exists() and read_json(path).get("hits"):
                continue  # recorded; searches that found nothing are tried again
            if time.time() > end:
                log("record.stopped", reason="until", at=f"{i}/{len(queries)}")
                return
            text = query_text(q, form)
            t = time.time()
            hits, error = search_with_retry(text, backend)
            write_json(path, {"id": q["id"], "form": form, "query": text, "hits": hits,
                              "error": error, "seconds": round(time.time() - t, 2)})
            pages = record_pages([h["url"] for h in hits])
            missing += not hits
            log("record.search", n=f"{i}/{len(queries)}", id=q["id"], form=form,
                hits=len(hits), new_pages=pages, error=error or "-")
            time.sleep(args.gap)
    log("record.done", questions=len(queries), searches_without_hits=missing)
    sys.exit(0)


if __name__ == "__main__":  # extraction uses worker processes, so the guard is required
    main()
# Stand: 02.11.2026
