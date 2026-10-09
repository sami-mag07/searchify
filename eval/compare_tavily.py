#!/usr/bin/env python3
# ============================================================================
# compare_tavily.py  -  SEPARATES, EIGENSTAENDIGES Vergleichs-Skript.
#
# WARNUNG / NUTZUNGSBEDINGUNGEN:
#   - Laeuft NUR mit gesetztem TAVILY_API_KEY (Umgebungsvariable).
#   - Die Nutzung der Tavily-API erfolgt auf EIGENES ToS-Risiko des Betreibers.
#     Tavily untersagt laut Terms u. a. Zugriff zum Bau eines Konkurrenzprodukts
#     sowie die Weitergabe von Leistungsvergleichen an Dritte (siehe 00-legal.md).
#   - Die Ergebnisse dieses Skripts sind NUR fuer den internen Eigengebrauch.
#     NICHT veroeffentlichen (kein Blog, README, Pitch, Marketing).
#   - Ausgaben landen ausschliesslich in eval/private/ (gitignored).
#   - Dieses Skript wurde vom Build-Prozess NICHT ausgefuehrt. Starten muss es
#     der Betreiber bewusst selbst mit seinem eigenen Key.
#
# Es misst rein deskriptiv: Latenz, Ergebnis-Overlap (RBO + Jaccard der URLs)
# und answer-Laenge. Kein Qualitaetsurteil, kein veroeffentlichungsfaehiger Wert.
# ============================================================================

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Dict, List
from urllib.parse import urlparse

import httpx

PRIVATE_DIR = Path(__file__).resolve().parent / "private"


def _canon_host_path(url: str) -> str:
    try:
        p = urlparse(url)
    except ValueError:
        return url
    host = p.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return f"{host}{p.path.rstrip('/')}"


def rbo(list_a: List[str], list_b: List[str], p: float = 0.9) -> float:
    """Rank-Biased Overlap zweier Ranglisten (0..1). Eigene Standard-Implementierung."""
    a = [_canon_host_path(x) for x in list_a]
    b = [_canon_host_path(x) for x in list_b]
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    depth = max(len(a), len(b))
    seen_a, seen_b = set(), set()
    overlap = 0
    score = 0.0
    for d in range(depth):
        if d < len(a):
            seen_a.add(a[d])
        if d < len(b):
            seen_b.add(b[d])
        overlap = len(seen_a & seen_b)
        score += (p ** d) * (overlap / (d + 1))
    return round((1 - p) * score, 4)


def jaccard_urls(list_a: List[str], list_b: List[str]) -> float:
    a = {_canon_host_path(x) for x in list_a}
    b = {_canon_host_path(x) for x in list_b}
    if not a and not b:
        return 1.0
    inter = len(a & b)
    union = len(a | b)
    return round(inter / union, 4) if union else 0.0


def call_tavily(query: str, api_key: str, timeout: float) -> Dict:
    """Ein einzelner Tavily-Call. Key kommt aus der Umgebung, nie aus dem Code."""
    t0 = time.perf_counter()
    resp = httpx.post(
        "https://api.tavily.com/search",
        json={"query": query, "max_results": 5, "include_answer": True},
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    data["_latency_s"] = round(time.perf_counter() - t0, 3)
    return data


def call_own(service_url: str, query: str, timeout: float) -> Dict:
    headers = {}
    service_key = os.environ.get("SERVICE_API_KEY", "")
    if service_key:
        headers["Authorization"] = f"Bearer {service_key}"
    t0 = time.perf_counter()
    resp = httpx.post(
        f"{service_url.rstrip('/')}/search",
        json={"query": query, "max_results": 5, "include_answer": True},
        headers=headers,
        timeout=timeout,
    )
    resp.raise_for_status()
    data = resp.json()
    data["_latency_s"] = round(time.perf_counter() - t0, 3)
    return data


def load_queries(path: Path) -> List[Dict]:
    items = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


def main() -> None:
    api_key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not api_key:
        raise SystemExit(
            "Abbruch: TAVILY_API_KEY ist nicht gesetzt. Dieses Skript laeuft nur mit "
            "eigenem Key und auf eigenes ToS-Risiko. Nichts wurde abgefragt."
        )

    ap = argparse.ArgumentParser(description="Interner, nicht-veroeffentlichbarer Vergleich.")
    ap.add_argument("--service-url", default="http://localhost:8000")
    ap.add_argument("--queries", default=str(Path(__file__).resolve().parent / "queries.jsonl"))
    ap.add_argument("--timeout", type=float, default=60.0)
    args = ap.parse_args()

    queries = load_queries(Path(args.queries))
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)

    rows: List[Dict] = []
    for q in queries:
        query = q["query"]
        try:
            tav = call_tavily(query, api_key, args.timeout)
        except Exception as exc:
            rows.append({"id": q["id"], "query": query, "error_tavily": str(exc)})
            continue
        try:
            own = call_own(args.service_url, query, args.timeout)
        except Exception as exc:
            rows.append({"id": q["id"], "query": query, "error_own": str(exc)})
            continue

        tav_urls = [r.get("url", "") for r in tav.get("results", [])]
        own_urls = [r.get("url", "") for r in own.get("results", [])]
        rows.append(
            {
                "id": q["id"],
                "query": query,
                "latency_tavily_s": tav.get("_latency_s"),
                "latency_own_s": own.get("_latency_s"),
                "rbo_url": rbo(tav_urls, own_urls),
                "jaccard_url": jaccard_urls(tav_urls, own_urls),
                "answer_len_tavily": len(tav.get("answer") or ""),
                "answer_len_own": len(own.get("answer") or ""),
                "n_results_tavily": len(tav_urls),
                "n_results_own": len(own_urls),
            }
        )

    out = PRIVATE_DIR / "compare_tavily_table.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    # Zusaetzlich eine schlichte TSV-Tabelle fuer schnelle Durchsicht.
    tsv = PRIVATE_DIR / "compare_tavily_table.tsv"
    cols = [
        "id",
        "latency_tavily_s",
        "latency_own_s",
        "rbo_url",
        "jaccard_url",
        "answer_len_tavily",
        "answer_len_own",
    ]
    with tsv.open("w", encoding="utf-8") as fh:
        fh.write("\t".join(cols) + "\n")
        for r in rows:
            fh.write("\t".join(str(r.get(c, "")) for c in cols) + "\n")

    print("Interner Vergleich geschrieben (NICHT veroeffentlichen, gitignored):")
    print(f"  {out}")
    print(f"  {tsv}")


if __name__ == "__main__":
    main()
