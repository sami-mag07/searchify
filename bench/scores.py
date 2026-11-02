"""Stage 3 (GPU helps): score every recorded chunk with every reranker, cached in sqlite.

    python bench/scores.py [--models mminilm,bge-m3] [--batch 64] [--limit N]

All chunkings and both search forms are scored against the full question. The current
default model also scores against the keyword form, for the question-vs-keywords test.
Scores already in the cache are skipped, so the stage resumes where it stopped. A model
that fails to load is logged and skipped; the night goes on with the others.
"""
from __future__ import annotations

import argparse
import sqlite3
import time
from typing import Dict, Optional, Tuple

from common import (CHUNKINGS, FORMS, OUT, candidates, load_models, load_queries, query_text,
                    sha, stop_at)


def log(event: str, **fields) -> None:
    print(event, " ".join(f"{k}={v}" for k, v in fields.items()), flush=True)


def open_cache(slug: str) -> sqlite3.Connection:
    path = OUT / "scores" / f"{slug}.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute("create table if not exists s (k text primary key, v real)")
    return db


def needed_pairs(queries, rerank_forms) -> Dict[str, Tuple[str, str]]:
    pairs: Dict[str, Tuple[str, str]] = {}
    for q in queries:
        for search_form in FORMS:
            for chunking in CHUNKINGS:
                for item in candidates(q, search_form, chunking):
                    for rerank_form in rerank_forms:
                        text = query_text(q, rerank_form)
                        pairs.setdefault(sha(text, item["text"]), (text, item["text"]))
    return pairs


def load_model(m: Dict, device: Optional[str] = None):
    from sentence_transformers import CrossEncoder

    return CrossEncoder(m["name"], revision=m["revision"], trust_remote_code=m["remote_code"],
                        max_length=512, device=device)


def predict(model, m: Dict, pairs, batch: int):
    """Scores on the GPU; if another program holds the GPU memory, the rest runs on the CPU."""
    try:
        return model, model.predict(pairs, batch_size=batch, show_progress_bar=False)
    except RuntimeError as exc:  # torch.cuda.OutOfMemoryError is a RuntimeError
        if "out of memory" not in str(exc).lower():
            raise
        import torch

        torch.cuda.empty_cache()
        log("scores.cpu_fallback", model=m["slug"], reason="cuda out of memory")
        model = load_model(m, device="cpu")
        return model, model.predict(pairs, batch_size=batch, show_progress_bar=False)


def score_model(m: Dict, queries, batch: int, end: float) -> None:
    forms = FORMS if m.get("current_default") else ("question",)
    db = open_cache(m["slug"])
    done = {row[0] for row in db.execute("select k from s")}
    todo = [(k, p) for k, p in needed_pairs(queries, forms).items() if k not in done]
    log("scores.start", model=m["slug"], cached=len(done), todo=len(todo))
    if not todo:
        return
    todo.sort(key=lambda kp: len(kp[1][1]))  # similar lengths per batch pad less
    model = load_model(m)
    t0, step = time.time(), 4096
    for start in range(0, len(todo), step):
        if time.time() > end:
            log("scores.stopped", model=m["slug"], reason="until", done=start, of=len(todo))
            return
        part = todo[start:start + step]
        model, values = predict(model, m, [p for _, p in part], batch)
        db.executemany("insert or replace into s values (?, ?)",
                       [(k, float(v)) for (k, _), v in zip(part, values)])
        db.commit()
        rate = (start + len(part)) / max(time.time() - t0, 1e-6)
        log("scores.progress", model=m["slug"], done=start + len(part), of=len(todo),
            pairs_per_s=round(rate, 1))
    log("scores.done", model=m["slug"], seconds=round(time.time() - t0))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="", help="Comma-separated slugs, default all.")
    ap.add_argument("--batch", type=int, default=64)
    ap.add_argument("--limit", type=int, default=0, help="Only the first N questions.")
    ap.add_argument("--until", default="", help="Stop scoring at HH:MM; the loop uses what is done.")
    args = ap.parse_args()
    end = stop_at(args.until)
    wanted = set(filter(None, args.models.split(",")))
    queries = load_queries("all")[: args.limit or None]
    for m in load_models():
        if wanted and m["slug"] not in wanted:
            continue
        try:
            score_model(m, queries, args.batch, end)
        except Exception as exc:  # one broken model must not end the night
            log("scores.failed", model=m["slug"], error=repr(exc)[:300])


if __name__ == "__main__":
    main()
# Stand: 02.11.2026
