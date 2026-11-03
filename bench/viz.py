"""Data for the benchmark charts, after a loop night: how far dev results carry over to test.

    python bench/viz.py [--top 300] [--sample 1500]

Reads out/loop.jsonl and the cached scores, writes out/viz.json:
  points    the best settings on dev plus a random sample, each scored on dev and on test
  progress  the best dev result over the night (every 20 000 settings)
  answers   per question and variant: position and characters until the first answer,
            plus what 0.2.0 handed a model for the same question (alt_snippets: the
            search snippets in hit order; alt_seiten: the whole top pages in hit order,
            read in blocks of max_chars; neu_snippets: the same snippets ordered by
            the default reranker, as passages() does when no page can be read)
Nothing in here picks a setting; test is only read to show the gap.
"""
from __future__ import annotations

import argparse
import heapq
import json
import random

from common import FORMS, OUT, answer_in, load_models, load_queries, recorded_hits, recorded_page, write_json
from loop import DEFAULT, GOAL, LOOP_FILE, Data, evaluate, log, ranked, valid

VARIANTS = {
    "heute": {},
    "stichworte": {"search_form": "keywords", "rerank_form": "keywords"},
    "suche_stichworte": {"search_form": "keywords"},
    "msmarco-l6": {"model": "msmarco-l6"},
    "mxbai-xsmall": {"model": "mxbai-xsmall"},
    "ohne_reranker": {"model": "none"},
}


def first_answer(d: Data, q: dict, c: dict) -> dict:
    k, order, pairs = ranked(d, q, c)
    used = 0
    for pos, i in enumerate(order, 1):
        hit, length = d.hit(q, k, i, c["max_chars"])
        used += length
        if hit:
            return {"pos": pos, "chars": used, "pairs": pairs}
    return {"pos": None, "chars": None, "pairs": pairs}


def blocks(text: str, size: int):
    """A page cut into consecutive blocks of at most `size` characters, on line breaks."""
    out, buf = [], ""
    for line in (ln.strip() for ln in (text or "").split("\n")):
        if not line:
            continue
        while len(line) > size:
            if buf:
                out.append(buf)
                buf = ""
            out.append(line[:size])
            line = line[size:]
        if buf and len(buf) + 1 + len(line) > size:
            out.append(buf)
            buf = ""
        buf = f"{buf} {line}".strip()
    if buf:
        out.append(buf)
    return out


def first_in(q: dict, texts) -> dict:
    used = 0
    for pos, text in enumerate(texts, 1):
        used += len(text)
        if answer_in(q["answer_re"], text):
            return {"pos": pos, "chars": used, "pairs": 0}
    return {"pos": None, "chars": None, "pairs": 0}


def old_release(q: dict) -> dict:
    """What 0.2.0 gave a model for the full question: search snippets, or whole pages."""
    hits = recorded_hits("question", q["id"])[:DEFAULT["max_results"]]
    snippets = [f"{h.get('title') or ''} {h.get('content') or ''}".strip() for h in hits]
    pages = [b for h in hits for b in blocks(recorded_page(h["url"]).get("content") or "", DEFAULT["max_chars"])]
    return {"alt_snippets": first_in(q, snippets), "alt_seiten": first_in(q, pages),
            "neu_snippets": first_in(q, reranked(q["query"], snippets))}


def reranked(query: str, texts):
    """The texts best first by the default reranker (searchify.rerank, mMiniLM)."""
    from searchify.rerank import score

    scores = score(query, texts) if texts else []
    return [t for _, t in sorted(zip(scores, texts), key=lambda st: -st[0])]


def scan_loop(top_n: int, sample_n: int, seed: int):
    """One pass over loop.jsonl: the top settings by dev, a random sample, the running best."""
    rng = random.Random(seed)
    top, sample, progress = [], [], []
    best = (-1.0, -1.0)
    n = 0
    with LOOP_FILE.open(encoding="utf-8") as fh:
        for line in fh:
            row = json.loads(line)
            n += 1
            dev = row["dev"]
            score = (dev[f"found@{GOAL}"], dev["mrr"])
            best = max(best, score)
            item = (score, n, row["config"])
            if len(top) < top_n:
                heapq.heappush(top, item)
            else:
                heapq.heappushpop(top, item)
            if len(sample) < sample_n:
                sample.append(row["config"])
            elif (j := rng.randrange(n)) < sample_n:
                sample[j] = row["config"]
            if n % 20_000 == 0:
                progress.append({"n": n, "found": best[0], "mrr": best[1]})
    progress.append({"n": n, "found": best[0], "mrr": best[1]})
    return [c for _, _, c in sorted(top, reverse=True)], sample, progress, n


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=300)
    ap.add_argument("--sample", type=int, default=1500)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()
    queries = [q for q in load_queries("all") if all(recorded_hits(f, q["id"]) for f in FORMS)]
    dev = [q for q in queries if q["split"] == "dev"]
    test = [q for q in queries if q["split"] == "test"]
    d = Data(queries, load_models())
    top, sample, progress, total = scan_loop(args.top, args.sample, args.seed)
    log("viz.scanned", settings=total, top=len(top), sample=len(sample))

    points = []
    for group, configs in (("top", top), ("sample", sample)):
        for c in configs:
            if not valid(c, d):
                continue
            a, b = evaluate(d, c, dev), evaluate(d, c, test)
            points.append({"group": group, "model": c["model"], "search_form": c["search_form"],
                           "dev_mrr": a["mrr"], "test_mrr": b["mrr"],
                           "dev_found": a[f"found@{GOAL}"], "test_found": b[f"found@{GOAL}"]})
    today = {"dev": evaluate(d, DEFAULT, dev), "test": evaluate(d, DEFAULT, test)}

    answers = []
    for q in queries:
        row = {"id": q["id"], "split": q["split"], "lang": q["lang"]}
        for name, change in VARIANTS.items():
            c = dict(DEFAULT, **change)
            if valid(c, d):
                row[name] = first_answer(d, q, c)
        row.update(old_release(q))
        answers.append(row)

    write_json(OUT / "viz.json", {"settings_tried": total, "today": today, "points": points,
                                  "progress": progress, "answers": answers,
                                  "variants": {k: dict(DEFAULT, **v) for k, v in VARIANTS.items()}})
    log("viz.done", path=OUT / "viz.json", points=len(points))


if __name__ == "__main__":
    main()
# Stand: 03.11.2026
