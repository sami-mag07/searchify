"""Stage 4 (CPU): try settings on dev until the deadline, then check the best ones on test.

    python bench/loop.py [--until 07:30 | --hours 8] [--seed 1] [--max-configs N]

Every setting is scored from the recordings and the cached reranker scores, so one try
costs milliseconds and the loop covers a lot of ground in a night. It first tries every
combination of query form, chunking and model with today's numbers, then alternates
random settings with small changes to the best ones so far. Only dev is used for that;
test is read once at the end for the report (out/report.md, out/best.json).
"""
from __future__ import annotations

import argparse
import datetime as dt
import heapq
import json
import random
import sqlite3
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

from common import (CHUNKINGS, FORMS, OUT, answer_in, candidates, load_models, load_queries,
                    query_text, recorded_hits, recorded_page, sha, stop_at, write_json)
from searchify.passages import trim

BUDGETS = (1500, 4800, 9600)  # characters a model reads; 4800 = today's 8 passages x 600
GOAL = 4800
DEFAULT = {"search_form": "question", "rerank_form": "question", "chunking": "para",
           "model": "mminilm", "stage2": "", "max_results": 5, "min_words": 12,
           "max_chars": 600, "per_site": 0, "per_page": 60}
NUMERIC = {"max_results": [3, 4, 5, 6, 8, 10], "min_words": [3, 5, 8, 12, 16, 20, 30],
           "max_chars": [200, 300, 400, 600, 800, 1000, 1500], "per_site": [0, 1, 2, 3],
           "per_page": [20, 40, 60, 100, 1000]}
STAGE2_TOP = (10, 20, 40)
LOOP_FILE = OUT / "loop.jsonl"


def log(event: str, **fields) -> None:
    print(event, " ".join(f"{k}={v}" for k, v in fields.items()), flush=True)


def key(c: Dict) -> str:
    return json.dumps(c, sort_keys=True)


class Data:
    """Recordings and cached scores in memory, plus memoised answer checks."""

    def __init__(self, queries: List[Dict], models: List[Dict]):
        self.queries = queries
        self.cands: Dict[Tuple, List[Dict]] = {}
        for q in queries:
            for sf in FORMS:
                for ch in CHUNKINGS:
                    items = candidates(q, sf, ch)
                    for it in items:
                        it["host"] = urlparse(it["url"]).netloc.lower().removeprefix("www.")
                        it["words"] = len(it["text"].split())
                    self.cands[(q["id"], sf, ch)] = items
        self.scores: Dict[Tuple[str, str], Dict[Tuple, List[float]]] = {}
        self._keys = {(rf, k): [sha(query_text(q, rf), it["text"]) for it in self.cands[k]]
                      for q in queries for rf in FORMS for sf in FORMS for ch in CHUNKINGS
                      for k in [(q["id"], sf, ch)]}
        for m in models:
            self._load_scores(m["slug"])
        self._hits: Dict[Tuple, List[Optional[bool]]] = {}
        self._lens: Dict[Tuple, List[Optional[int]]] = {}

    def _load_scores(self, slug: str) -> None:
        path = OUT / "scores" / f"{slug}.sqlite"
        if not path.exists():
            return
        table = dict(sqlite3.connect(path).execute("select k, v from s"))
        for rf in FORMS:
            per, missing, total = {}, 0, 0
            for (form, k), keys in self._keys.items():
                if form != rf:
                    continue
                vals = [table.get(h) for h in keys]
                total += len(vals)
                missing += vals.count(None)
                per[k] = [float("-inf") if v is None else v for v in vals]
            if total and missing / total <= 0.01:
                self.scores[(slug, rf)] = per
                log("loop.scores", model=slug, rerank_form=rf, pairs=total, missing=missing)

    def hit(self, q: Dict, k: Tuple, i: int, max_chars: int) -> Tuple[bool, int]:
        memo = (k, max_chars)
        if memo not in self._hits:
            n = len(self.cands[k])
            self._hits[memo], self._lens[memo] = [None] * n, [None] * n
        if self._hits[memo][i] is None:
            text = trim(self.cands[k][i]["text"], max_chars)
            self._hits[memo][i] = answer_in(q["answer_re"], text)
            self._lens[memo][i] = len(text)
        return self._hits[memo][i], self._lens[memo][i]


def ranked(d: Data, q: Dict, c: Dict) -> Tuple[Tuple, List[int], int]:
    """Indices of the passages the setting returns, in order, plus pairs the reranker scores."""
    k = (q["id"], c["search_form"], c["chunking"])
    items = d.cands[k]
    idx, per_url = [], {}
    for i, it in enumerate(items):
        if it["rank"] >= c["max_results"] or it["words"] < c["min_words"]:
            continue
        if per_url.get(it["url"], 0) >= c["per_page"]:
            continue
        per_url[it["url"]] = per_url.get(it["url"], 0) + 1
        idx.append(i)
    pairs = 0
    if c["model"] != "none":
        s = d.scores[(c["model"], c["rerank_form"])][k]
        idx.sort(key=lambda i: -s[i])
        pairs = len(idx)
        if c["stage2"]:
            slug2, top = c["stage2"].split("@")
            s2 = d.scores[(slug2, "question")][k]
            head = sorted(idx[:int(top)], key=lambda i: -s2[i])
            idx = head + idx[int(top):]
    out, per_host = [], {}
    for i in idx:
        host = items[i]["host"]
        if c["per_site"] and per_host.get(host, 0) >= c["per_site"]:
            continue
        per_host[host] = per_host.get(host, 0) + 1
        out.append(i)
    return k, out, pairs


def evaluate(d: Data, c: Dict, queries: List[Dict]) -> Dict:
    found = {b: 0 for b in BUDGETS}
    mrr = hit1 = pairs_total = 0.0
    for q in queries:
        k, order, pairs = ranked(d, q, c)
        pairs_total += pairs
        used, first, got = 0, None, set()
        for pos, i in enumerate(order, 1):
            hit, length = d.hit(q, k, i, c["max_chars"])
            used += length
            if hit:
                first = first or pos
                got.update(b for b in BUDGETS if used <= b)
            if used > BUDGETS[-1]:
                break
        for b in got:
            found[b] += 1
        if first:
            mrr += 1 / first if first <= 20 else 0
            hit1 += first == 1
    n = max(len(queries), 1)
    row = {f"found@{b}": round(found[b] / n, 4) for b in BUDGETS}
    row.update(mrr=round(mrr / n, 4), hit1=round(hit1 / n, 4), pairs=round(pairs_total / n, 1))
    return row


def objective(row: Dict, config_key: str = "") -> Tuple[float, float, int]:
    """Higher is better: answer within the goal budget, then MRR, then fewer changes."""
    changed = sum(DEFAULT.get(k) != v for k, v in json.loads(config_key).items()) if config_key else 0
    return row[f"found@{GOAL}"], row["mrr"], -changed


def valid(c: Dict, d: Data) -> bool:
    if c["model"] == "none":
        return c["rerank_form"] == "question" and not c["stage2"]
    if (c["model"], c["rerank_form"]) not in d.scores:
        return False
    if c["stage2"]:
        slug2 = c["stage2"].split("@")[0]
        return slug2 != c["model"] and (slug2, "question") in d.scores
    return True


def options(d: Data) -> Dict[str, List]:
    models = sorted({slug for slug, _ in d.scores})
    return dict(NUMERIC, search_form=list(FORMS), rerank_form=list(FORMS),
                chunking=list(CHUNKINGS), model=["none"] + models,
                stage2=[""] + [f"{m}@{n}" for m in models for n in STAGE2_TOP])


def deadline(args) -> float:
    return time.time() + args.hours * 3600 if args.hours else stop_at(args.until)


def search(d: Data, dev: List[Dict], args) -> Dict[str, Dict]:
    seen: Dict[str, Dict] = {}
    if LOOP_FILE.exists():
        for line in LOOP_FILE.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            seen[key(row["config"])] = row["dev"]
    rng, opts, end = random.Random(args.seed), options(d), deadline(args)
    first = [DEFAULT] + [dict(DEFAULT, search_form=sf, rerank_form=rf, chunking=ch, model=m)
                         for sf in FORMS for rf in FORMS for ch in CHUNKINGS
                         for m in opts["model"]]
    top = heapq.nlargest(20, ((objective(r, s), s) for s, r in seen.items()))
    tries = misses = 0
    with LOOP_FILE.open("a", encoding="utf-8") as out:
        while time.time() < end and (not args.max_configs or tries < args.max_configs):
            if first:
                c = first.pop(0)
            elif rng.random() < 0.3 or not seen:
                c = {p: rng.choice(v) for p, v in opts.items()}
            else:
                c = dict(json.loads(rng.choice(top)[1]))
                p = rng.choice(list(opts))
                c[p] = rng.choice(opts[p])
            if not valid(c, d) or key(c) in seen:
                misses += 1
                if misses > 200_000:  # in a row: nothing new left near the best or at random
                    log("loop.exhausted", tries=tries)
                    break
                continue
            misses = 0
            seen[key(c)] = evaluate(d, c, dev)
            out.write(json.dumps({"config": c, "dev": seen[key(c)]}) + "\n")
            top = heapq.nlargest(20, top + [(objective(seen[key(c)], key(c)), key(c))])
            tries += 1
            if tries % 500 == 0:
                out.flush()
                lead = seen[top[0][1]]
                log("loop.progress", tries=tries, best_found=lead[f"found@{GOAL}"], best_mrr=lead["mrr"])
    return seen


def best_where(seen: Dict[str, Dict], cond) -> Optional[Dict]:
    picks = [s for s in seen if cond(json.loads(s))]
    return json.loads(max(picks, key=lambda s: objective(seen[s], s))) if picks else None


def recall_ceiling(d: Data, queries: List[Dict], form: str, max_results: int) -> float:
    """Share of questions whose answer is anywhere in the recorded top pages."""
    hits = 0
    for q in queries:
        pages = [recorded_page(h["url"]).get("content") or "" for h in recorded_hits(form, q["id"])[:max_results]]
        hits += any(answer_in(q["answer_re"], text) for text in pages)
    return round(hits / max(len(queries), 1), 4)


def report(d: Data, seen: Dict[str, Dict], dev: List[Dict], test: List[Dict]) -> None:
    latency = json.loads((OUT / "latency.json").read_text()) if (OUT / "latency.json").exists() else {}

    def line(name: str, c: Optional[Dict]) -> str:
        if not c or not valid(c, d):
            return f"| {name} | keine Daten | | | |"
        a, b = evaluate(d, c, dev), evaluate(d, c, test)
        cpu = 0 if c["model"] == "none" else latency.get(c["model"], {}).get("cpu4_ms")
        sec = "?" if cpu is None else f"{b['pairs'] * cpu / 1000:.1f} s"
        return (f"| {name} | {a[f'found@{GOAL}']:.0%} / {b[f'found@{GOAL}']:.0%} | "
                f"{a['mrr']:.2f} / {b['mrr']:.2f} | {sec} | `{changes(c)}` |")

    def changes(c: Dict) -> str:
        diff = {k: v for k, v in c.items() if DEFAULT.get(k) != v}
        return ", ".join(f"{k}={v}" for k, v in diff.items()) or "heutige Einstellung"

    head = ("| Einstellung | Antwort in 4800 Zeichen (dev / test) | MRR (dev / test) | "
            "Sortieren je Frage, CPU | Abweichung von heute |\n|---|---|---|---|---|")
    best = best_where(seen, lambda c: True)
    lines = ["# Passagen-Benchmark", "",
             f"Stand {dt.datetime.now():%d.%m.%Y %H:%M}, {len(seen)} Einstellungen auf dev "
             f"({len(dev)} Fragen) probiert, test ({len(test)} Fragen) nur hier gelesen.", "",
             "## Gesamt", "", head, line("Heute", DEFAULT), line("Bestes auf dev", best), "",
             "## 1. Passagen-Einstellungen (Standardmodell)", "", head]
    feature1 = best_where(seen, lambda c: c["model"] == "mminilm" and not c["stage2"]
                          and c["search_form"] == c["rerank_form"] == "question")
    lines += [line("Heute", DEFAULT), line("Bestes", feature1), "",
              "## 2. Reranker", "", head]
    for slug in sorted({c for c, _ in d.scores}) + ["none"]:
        lines.append(line(slug, best_where(seen, lambda c, s=slug: c["model"] == s and not c["stage2"])))
    lines.append(line("zweistufig", best_where(seen, lambda c: bool(c["stage2"]))))
    lines += ["", "## 3. Ganze Frage oder Stichworte (heutige Zahlen)", "", head]
    for sf in FORMS:
        for rf in FORMS:
            c = dict(DEFAULT, search_form=sf, rerank_form=rf)
            lines.append(line(f"Suche {sf}, Sortieren {rf}", c if valid(c, d) else None))
    lines += ["", "Obergrenze (Antwort steht irgendwo in den Top-5-Seiten), dev / test:"]
    for sf in FORMS:
        lines.append(f"- {sf}: {recall_ceiling(d, dev, sf, 5):.0%} / {recall_ceiling(d, test, sf, 5):.0%}")
    lines += ["", "Lesart: Eine Einstellung zählt erst als besser, wenn sie auch auf test vorne "
              "liegt. dev hat nur 50 Fragen; wer tausende Einstellungen probiert, findet auf dev "
              "immer eine, die zufällig gut aussieht."]
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    write_json(OUT / "best.json", {"overall": best, "passages": feature1})
    log("loop.report", path=OUT / "report.md")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--until", default="07:30")
    ap.add_argument("--hours", type=float, default=0)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--max-configs", type=int, default=0)
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args()
    queries = load_queries("all")
    dev = [q for q in queries if q["split"] == "dev"]
    test = [q for q in queries if q["split"] == "test"]
    d = Data(queries, load_models())
    seen = {}
    if args.report_only:
        for line in LOOP_FILE.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            seen[key(row["config"])] = row["dev"]
    else:
        seen = search(d, dev, args)
    report(d, seen, dev, test)


if __name__ == "__main__":
    main()
# Stand: 02.11.2026
