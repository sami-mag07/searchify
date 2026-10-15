#!/usr/bin/env python3
"""Eval des EIGENEN Dienstes gegen OEFFENTLICHE Ground-Truth.

Misst Faithfulness und Answer-Relevanz (ragas) plus Latenz und Trefferzahl.
KEIN Tavily-Call, KEIN Tavily-Vergleich (siehe 00-legal.md, Benchmark-Klausel).

Aufruf (Dienst muss laufen, Env via .env gesetzt):
    python eval/run_eval.py --service-url http://localhost:8000 \
        --queries eval/queries.jsonl

Ergebnisse werden nach eval/private/ geschrieben (gitignored).

Hinweis: ragas braucht selbst ein LLM/Embedding als Richter. Dieses Skript
nutzt dafuer denselben OpenAI-kompatiblen Wrapper (OPENAI_BASE_URL/OPENAI_API_KEY)
wie der Dienst. Ist ragas nicht installiert, werden nur Latenz/Trefferzahl
gemessen und das klar vermerkt.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import time
from pathlib import Path
from typing import Dict, List

import httpx

PRIVATE_DIR = Path(__file__).resolve().parent / "private"


def load_queries(path: Path) -> List[Dict]:
    items: List[Dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def call_service(service_url: str, query: str, timeout: float) -> Dict:
    """Ruft den eigenen Dienst auf. Key nur aus der Umgebung (optional)."""
    headers = {}
    service_key = os.environ.get("SERVICE_API_KEY", "")
    if service_key:
        headers["Authorization"] = f"Bearer {service_key}"
    payload = {"query": query, "max_results": 5, "include_answer": True, "include_raw_content": True}
    t0 = time.perf_counter()
    resp = httpx.post(
        f"{service_url.rstrip('/')}/search", json=payload, headers=headers, timeout=timeout
    )
    resp.raise_for_status()
    data = resp.json()
    data["_latency_s"] = round(time.perf_counter() - t0, 3)
    return data


def try_ragas(records: List[Dict]) -> Dict:
    """Versucht Faithfulness + Answer-Relevanz via ragas. Optional."""
    try:
        from datasets import Dataset
        from ragas import evaluate
        from ragas.metrics import answer_relevancy, faithfulness
    except ImportError:
        return {"ragas_available": False, "note": "ragas/datasets nicht installiert - uebersprungen."}

    rows = {
        "question": [r["query"] for r in records],
        "answer": [r.get("answer") or "" for r in records],
        "contexts": [r["contexts"] for r in records],
        "ground_truth": [r.get("ground_truth") or "" for r in records],
    }
    dataset = Dataset.from_dict(rows)
    try:
        result = evaluate(dataset, metrics=[faithfulness, answer_relevancy])
        return {"ragas_available": True, "scores": {k: float(v) for k, v in result.items()}}
    except Exception as exc:  # ragas-Richter braucht LLM-Zugang
        return {"ragas_available": True, "error": str(exc)}


def main() -> None:
    ap = argparse.ArgumentParser(description="Eval des eigenen RAG-Dienstes (kein Tavily).")
    ap.add_argument("--service-url", default="http://localhost:8000")
    ap.add_argument("--queries", default=str(Path(__file__).resolve().parent / "queries.jsonl"))
    ap.add_argument("--timeout", type=float, default=60.0)
    args = ap.parse_args()

    queries = load_queries(Path(args.queries))
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)

    records: List[Dict] = []
    latencies: List[float] = []
    hit_counts: List[int] = []

    for q in queries:
        try:
            data = call_service(args.service_url, q["query"], args.timeout)
        except Exception as exc:  # Dienst nicht erreichbar -> Eintrag vermerken
            records.append({"id": q["id"], "query": q["query"], "error": str(exc)})
            continue
        results = data.get("results", [])
        contexts = [r.get("raw_content") or r.get("content") or "" for r in results]
        records.append(
            {
                "id": q["id"],
                "query": q["query"],
                "answer": data.get("answer"),
                "contexts": contexts,
                "ground_truth": q.get("ground_truth"),
                "hit_count": len(results),
                "latency_s": data.get("_latency_s"),
            }
        )
        latencies.append(data.get("_latency_s", 0.0))
        hit_counts.append(len(results))

    summary = {
        "n_queries": len(queries),
        "n_answered": len(latencies),
        "latency_p50_s": round(statistics.median(latencies), 3) if latencies else None,
        "latency_p95_s": (
            round(sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)], 3)
            if latencies
            else None
        ),
        "avg_hit_count": round(statistics.mean(hit_counts), 2) if hit_counts else None,
    }
    gradeable = [r for r in records if r.get("answer") and r.get("contexts")]
    summary["ragas"] = try_ragas(gradeable) if gradeable else {"ragas_available": False}

    out_records = PRIVATE_DIR / "eval_records.json"
    out_summary = PRIVATE_DIR / "eval_summary.json"
    out_records.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    out_summary.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print("Eval fertig. Ausgaben (gitignored):")
    print(f"  {out_records}")
    print(f"  {out_summary}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
