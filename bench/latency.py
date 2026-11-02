"""Stage 2: how long each reranker takes per (question, passage) pair.

    python bench/latency.py [--models mminilm] [--pairs 256]

Measured on the CPU with 4 threads (close to a normal laptop) and, if there is one, on
the GPU. Writes out/latency.json; the report turns it into seconds per question.
"""
from __future__ import annotations

import argparse
import json
import time

from common import OUT, candidates, load_models, load_queries, write_json
from scores import load_model, log


def sample_pairs(n: int):
    pairs = []
    for q in load_queries("dev"):
        for item in candidates(q, "question", "para"):
            pairs.append((q["query"], item["text"]))
            if len(pairs) == n:
                return pairs
    return pairs or [("Where is the Eiffel Tower?", "The Eiffel Tower is in Paris. " * 8)] * n


def ms_per_pair(model, pairs, device: str) -> float:
    model.predict(pairs[:32], batch_size=32, show_progress_bar=False, device=device)  # warm up
    t = time.perf_counter()
    model.predict(pairs, batch_size=32, show_progress_bar=False, device=device)
    return round((time.perf_counter() - t) * 1000 / len(pairs), 3)


def main() -> None:
    import torch

    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="")
    ap.add_argument("--pairs", type=int, default=256)
    args = ap.parse_args()
    wanted = set(filter(None, args.models.split(",")))
    path = OUT / "latency.json"
    result = json.loads(path.read_text()) if path.exists() else {}
    pairs = sample_pairs(args.pairs)
    torch.set_num_threads(4)
    for m in load_models():
        if (wanted and m["slug"] not in wanted) or m["slug"] in result:
            continue
        try:
            model = load_model(m)
            row = {"cpu4_ms": ms_per_pair(model, pairs, "cpu")}
            if torch.cuda.is_available():
                row["gpu_ms"] = ms_per_pair(model, pairs, "cuda")
            result[m["slug"]] = row
            write_json(path, result)
            log("latency.done", model=m["slug"], **row)
        except Exception as exc:
            log("latency.failed", model=m["slug"], error=repr(exc)[:300])


if __name__ == "__main__":
    main()
# Stand: 02.11.2026
