#!/usr/bin/env python3
"""Andere Reranker ohne Training auf den eigenen Offline-Pruefungen vergleichen.

Huelle um eval/objective.py (Kern) und eval/tool_objective.py (serper/exa/reader):
setzt RERANKER_MODEL, laedt Modelle mit eigenem Code (trust_remote_code) nur auf
Wunsch und schaltet die auf mMiniLM-Logits geeichten Schwellen um. app/ bleibt
unveraendert, alles per Umgebung und Monkeypatch nur in diesem Prozess.

  reranker_compare.py --model M [--mode raw|cal|neutral] [--cal A,B] [--remote-code]
                      --task kern|serper|exa|reader|latency|collect|logits
                      [--split dev|test|both] [--out FILE] [--pairs FILE]

Modi
  raw      Modell direkt eingesetzt, alle Schwellen wie fuer mMiniLM.
  cal      Logits affin auf die mMiniLM-Skala gebracht (logit' = A*logit + B, A/B aus
           --task logits auf dev-Paaren): Schwellen und Prioren behalten ihre Bedeutung.
  neutral  alle Logit-Schwellen und -Abzuege aus (MIN_RESULT_SCORE, FUSION_TRUST_MIN,
           SERPER_WEAK_CE/TRUST_MIN/DUP_CE_MIN, QUALITY_PRIOR, SOURCE_PRIOR,
           LANGUAGE_MISMATCH_PENALTY); fuer alle Modelle gleich -> reiner Modellvergleich.

Aufgaben
  collect  Kern dev im Replay mit dem aktuellen Modell, schreibt die Reranker-Eingaben
           (Query + Texte je Aufruf) nach --pairs.
  logits   Logits des Modells fuer --pairs, plus mMiniLM; gibt A/B (Mittelwert/Streuung
           angeglichen) aus.
  latency  20 Fragen (queries_fresh 10 + queries_hard 10) ueber run_search im Replay:
           Reranker-Zeit je /search-Anfrage (Summe aller Forward-Aufrufe) und Paare.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BASELINE = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"

NEUTRAL_ENV = {
    "MIN_RESULT_SCORE": "0",
    "FUSION_TRUST_MIN": "-1e9",
    "SERPER_WEAK_CE": "-1e9",
    "QUALITY_PRIOR": "0",
    "SOURCE_PRIOR": "0",
    "LANGUAGE_MISMATCH_PENALTY": "0",
}

TIMES: list = []  # (sekunden, paare) je _cross_scores-Aufruf


def _install(model: str, mode: str, cal: str, remote_code: bool, pairs_out: str = "") -> None:
    os.environ["RERANKER_MODEL"] = model
    if mode == "neutral":
        os.environ.update(NEUTRAL_ENV)
    sys.path.insert(0, str(ROOT))
    from app import rerank

    if remote_code:
        def load():
            import torch
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            device = rerank._pick_device()
            dtype = torch.float16 if device != "cpu" else torch.float32
            tok = AutoTokenizer.from_pretrained(model, trust_remote_code=True)
            mdl = AutoModelForSequenceClassification.from_pretrained(
                model, torch_dtype=dtype, trust_remote_code=True).to(device).eval()
            return (tok, mdl, device)

        rerank._load_reranker = load

    a, b = (float(x) for x in cal.split(",")) if (mode == "cal" and cal) else (1.0, 0.0)
    orig = rerank._cross_scores

    def cross(query, texts, max_length, batch=32):
        if pairs_out:
            with open(pairs_out, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"q": query, "t": texts, "m": max_length}, ensure_ascii=False) + "\n")
        t0 = time.perf_counter()
        out = orig(query, texts, max_length, batch)
        TIMES.append((time.perf_counter() - t0, len(texts)))
        return [a * s + b for s in out]

    rerank._cross_scores = cross

    if mode == "neutral":
        from app.compat import serper

        serper.TRUST_MIN = -1e9
        serper.DUP_CE_MIN = 1e9
        serper.WEAK_CE = -1e9


def _logits(pairs_file: str, out: str) -> None:
    """Logits fuer gespeicherte Paare (Modell aus RERANKER_MODEL, bereits installiert)."""
    from app import rerank

    rows = [json.loads(line) for line in open(pairs_file, encoding="utf-8")]
    vals = []
    for r in rows:
        vals.append(rerank._cross_scores(r["q"], r["t"], r["m"]))
    Path(out).write_text(json.dumps(vals), encoding="utf-8")
    flat = [x for v in vals for x in v]
    print(json.dumps({"n": len(flat), "mean": statistics.mean(flat), "sd": statistics.pstdev(flat),
                      "p05": sorted(flat)[len(flat) // 20], "p95": sorted(flat)[-len(flat) // 20]}))


def _latency(out: str) -> None:
    os.environ.setdefault("REPLAY_DIR", str(HERE / "private" / "replay"))
    os.environ["SEARCH_CACHE_TTL_S"] = "0"
    os.environ["PAGE_CACHE_TTL_S"] = "0"
    from app.extract import warm_up
    from app.main import run_search
    from app.rerank import warm_up as rr_warm
    from app.schema import SearchRequest

    warm_up()
    rr_warm()
    qs = []
    for f in ("queries_fresh.jsonl", "queries_hard.jsonl"):
        qs += [json.loads(x) for x in open(HERE / f, encoding="utf-8") if x.strip()][:10]
    rows = []
    for q in qs:
        TIMES.clear()
        t0 = time.perf_counter()
        run_search(SearchRequest(query=q["query"], max_results=10, include_answer=False, language=q.get("lang")))
        rows.append({"total": time.perf_counter() - t0, "rr": sum(t for t, _ in TIMES),
                     "pairs": sum(n for _, n in TIMES)})
    rr = sorted(r["rr"] for r in rows)
    res = {"n": len(rows), "rr_median": round(statistics.median(rr), 3),
           "rr_p90": round(rr[int(0.9 * (len(rr) - 1) + 0.5)], 3),
           "pairs_median": statistics.median(r["pairs"] for r in rows),
           "total_median": round(statistics.median(r["total"] for r in rows), 3)}
    print("latency", json.dumps(res))
    if out:
        Path(out).write_text(json.dumps({"summary": res, "rows": rows}, indent=1), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default=BASELINE)
    ap.add_argument("--mode", default="raw", choices=["raw", "cal", "neutral"])
    ap.add_argument("--cal", default="")
    ap.add_argument("--remote-code", action="store_true", help="trust_remote_code (nur offizielle Repos)")
    ap.add_argument("--task", required=True,
                    choices=["kern", "serper", "exa", "reader", "latency", "collect", "logits"])
    ap.add_argument("--split", default="dev")
    ap.add_argument("--out", default="")
    ap.add_argument("--pairs", default="")
    a = ap.parse_args()
    _install(a.model, a.mode, a.cal, a.remote_code, a.pairs if a.task == "collect" else "")
    sys.path.insert(0, str(HERE))
    if a.task == "logits":
        _logits(a.pairs, a.out)
    elif a.task == "latency":
        _latency(a.out)
    elif a.task in ("kern", "collect"):
        import objective

        sys.argv = ["objective.py", "--split", a.split] + (["--out", a.out] if a.out else [])
        objective.main()
        if TIMES:
            print("rerank_calls", len(TIMES), "rerank_s", round(sum(t for t, _ in TIMES), 1))
    else:
        import tool_objective

        sys.argv = ["tool_objective.py", "--tool", a.task, "--split", a.split, "--offline"] + (
            ["--out", a.out] if a.out else [])
        tool_objective.main()


if __name__ == "__main__":
    main()
