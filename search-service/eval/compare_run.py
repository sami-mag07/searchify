#!/usr/bin/env python3
# ============================================================================
# compare_run.py  -  INTERNER, NICHT-VEROEFFENTLICHBARER Vergleich (lokal).
#
# Vergleicht die Tavily Search REST-API mit der EIGENEN Retrieval-Pipeline
# (clean-room) auf denselben Queries. Misst rein deskriptiv:
#   - Latenz (Wall-Clock je Query, p50/p95)
#   - Ergebnis-URL-Overlap: Jaccard (Top-10) + RBO (rang-gewichtet)
#   - Trefferzahl, Fehlerrate, answer-Laenge (nur Tavily)
#
# EIGENE SEITE (lokal, ohne Docker):
#   Such-Backend = ddgs (DuckDuckGo) als EVAL-SUBSTITUT fuer SearXNG.
#   Die echte Architektur bleibt SearXNG (app/search.py); hier nur ersetzt,
#   weil kein SearXNG-Container laeuft. Danach die ECHTEN App-Bausteine:
#   trafilatura-Extraktion + Chunking (app/extract.py), Dedup + bge-Reranker
#   (app/rerank.py), Merge je Quelle. Answer-Schritt (Claude) WEGGELASSEN,
#   weil claude-code-api auf dem VPS liegt -> nur Retrieval + Latenz.
#
# SICHERHEIT (Pflicht):
#   - Der Tavily-Key wird NUR zur Laufzeit aus einer .env gelesen (Regex),
#     in eine Variable/os.environ geladen und ausschliesslich im
#     Authorization-Header verwendet.
#   - Der Key wird NIE ausgegeben, geloggt, in argv gesetzt oder in eine
#     Ausgabedatei geschrieben. Alle Fehlertexte werden vor Ausgabe von
#     'tvly-...'-Vorkommen bereinigt (Schutzgurt).
#
# NUTZUNGSBEDINGUNGEN / ToS:
#   - Nutzung der Tavily-API auf eigenes ToS-Risiko des Betreibers.
#   - Ergebnisse NUR fuer internen Eigengebrauch, NICHT veroeffentlichen.
#   - Ausgaben landen ausschliesslich in eval/private/ (gitignored).
# ============================================================================

from __future__ import annotations

import json
import os
import re
import statistics
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional

import httpx

# --- Reranker-Modell festlegen, BEVOR App-Module die Settings lesen. ---
# Task-Vorgabe: bge-reranker-base (kleiner als der v2-m3-Default der App).
os.environ.setdefault("RERANKER_MODEL", "BAAI/bge-reranker-base")
os.environ.setdefault("RERANKER_USE_FP16", "false")
# SearXNG wird hier NICHT benutzt (eigenes ddgs-Backend). Platzhalter, damit
# app.config nicht auf einen echten Dienst zeigt.
os.environ.setdefault("SEARXNG_URL", "http://localhost:0")

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
PRIVATE_DIR = HERE / "private"
sys.path.insert(0, str(REPO_ROOT))

from app import extract as extract_mod  # noqa: E402
from app import rerank as rerank_mod  # noqa: E402

# RBO/Jaccard/Kanonisierung aus dem bestehenden Vergleichsskript wiederverwenden.
sys.path.insert(0, str(HERE))
from compare_tavily import jaccard_urls, rbo  # noqa: E402

TAVILY_URL = "https://api.tavily.com/search"
# Reihenfolge der Quellen, aus denen der Key gelesen werden darf (nur .env!).
ENV_KEY_SOURCES = [
    Path.home() / "Desktop/APP/telegram-agent/.env",
]
KEY_RE = re.compile(r"tvly-[A-Za-z0-9_\-]+")


def strip_key(text: str) -> str:
    """Schutzgurt: entfernt jedes 'tvly-...'-Vorkommen aus beliebigem Text."""
    return KEY_RE.sub("tvly-<redacted>", text or "")


def load_tavily_key() -> str:
    """Liest TAVILY_API_KEY NUR aus einer .env (Regex), nie aus argv/Code.

    Gibt den Key zurueck; druckt/loggt ihn NIE. Bricht sauber ab, wenn keiner
    gefunden wird.
    """
    # 1) Schon in der Umgebung? (Dann NICHT aus argv - nur falls extern gesetzt.)
    env_val = os.environ.get("TAVILY_API_KEY", "").strip()
    if env_val.startswith("tvly-"):
        return env_val
    # 2) Aus einer .env-Datei per Regex.
    for src in ENV_KEY_SOURCES:
        try:
            raw = src.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in raw.splitlines():
            if line.lstrip().startswith("#"):
                continue
            if "TAVILY_API_KEY" not in line:
                continue
            m = KEY_RE.search(line)
            if m:
                return m.group(0)
    raise SystemExit(
        "Abbruch: Kein TAVILY_API_KEY in den erlaubten .env-Quellen gefunden. "
        "Nichts wurde abgefragt. (Key NICHT in argv/Code setzen.)"
    )


def load_queries(path: Path) -> List[Dict]:
    items: List[Dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    return items


# --------------------------------------------------------------------------
# Tavily-Seite
# --------------------------------------------------------------------------
def call_tavily(client: httpx.Client, query: str, api_key: str, timeout: float) -> Dict:
    body = {
        "query": query,
        "search_depth": "basic",
        "include_answer": "basic",
        "max_results": 10,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    t0 = time.perf_counter()
    resp = client.post(TAVILY_URL, json=body, headers=headers, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    latency = round(time.perf_counter() - t0, 3)
    results = data.get("results", []) or []
    urls = [(r.get("url") or "").strip() for r in results if r.get("url")]
    scores = [r.get("score") for r in results if r.get("url")]
    answer = data.get("answer") or ""
    return {
        "urls": urls,
        "scores": scores,
        "answer_len": len(answer),
        "n_results": len(urls),
        "latency_s": latency,
    }


# --------------------------------------------------------------------------
# Eigene Seite (lokal): ddgs -> trafilatura+Chunking -> dedup+rerank -> merge
# --------------------------------------------------------------------------
def ddgs_candidates(query: str, n: int, region: str) -> List[Dict[str, str]]:
    """SearXNG-ERSATZ fuer die Eval. Liefert Kandidaten im App-Dict-Format."""
    from ddgs import DDGS

    raw = DDGS().text(query, region=region, safesearch="moderate", max_results=n)
    out: List[Dict[str, str]] = []
    seen = set()
    for item in raw:
        url = (item.get("href") or item.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        out.append(
            {
                "title": (item.get("title") or "").strip(),
                "url": url,
                "content": (item.get("body") or "").strip(),
                "engine": "ddgs-substitute",
            }
        )
    return out


def run_own(query: str, lang: str, candidate_pool: int, max_results: int, use_reranker: bool) -> Dict:
    region = "de-de" if lang == "de" else "us-en"
    t0 = time.perf_counter()
    candidates = ddgs_candidates(query, candidate_pool, region)
    chunks = extract_mod.fetch_and_extract(candidates)
    chunks = rerank_mod.dedup_chunks(chunks)
    if use_reranker:
        ranked = rerank_mod.rerank(query, chunks)
    else:
        # Fallback ohne neuronalen Reranker: reine Retrieval-Reihenfolge.
        # Pseudo-Score absteigend nach Kandidaten-/Chunk-Reihenfolge.
        ranked = []
        for i, ch in enumerate(chunks):
            item = dict(ch)
            item["score"] = round(1.0 - i / max(1, len(chunks)), 4)
            ranked.append(item)
    merged = rerank_mod.merge_by_source(ranked, chunks_per_source=3, max_results=max_results)
    latency = round(time.perf_counter() - t0, 3)
    urls = [(m.get("url") or "").strip() for m in merged if m.get("url")]
    return {
        "urls": urls,
        "n_results": len(urls),
        "n_sources_fetched": len({c["url"] for c in chunks}) if chunks else 0,
        "latency_s": latency,
    }


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------
def p50(xs: List[float]) -> Optional[float]:
    return round(statistics.median(xs), 3) if xs else None


def p95(xs: List[float]) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    idx = max(0, int(len(s) * 0.95) - 1)
    return round(s[idx], 3)


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="Interner Tavily-vs-eigen Vergleich (lokal).")
    ap.add_argument("--queries", default=str(HERE / "queries.jsonl"))
    ap.add_argument("--candidate-pool", type=int, default=12)
    ap.add_argument("--max-results", type=int, default=10)
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--pause", type=float, default=2.0, help="Pause zwischen Queries (hoeflich).")
    ap.add_argument("--no-reranker", action="store_true", help="Ohne bge-Reranker (reine Retrieval-Reihenfolge).")
    args = ap.parse_args()

    api_key = load_tavily_key()  # nur in-memory, nie ausgegeben
    queries = load_queries(Path(args.queries))
    PRIVATE_DIR.mkdir(parents=True, exist_ok=True)

    # Reranker-Status bestimmen (Warmup); bei Fehler sauber auf Fallback.
    use_reranker = not args.no_reranker
    reranker_note = "ohne (per Flag deaktiviert)"
    if use_reranker:
        try:
            print("Lade bge-reranker-base (einmaliger Warmup)...", flush=True)
            rerank_mod.rerank("warmup", [{"url": "u", "title": "t", "content": "warmup text", "full_text": "warmup"}])
            reranker_note = f"Cross-Encoder (transformers), Modell={os.environ['RERANKER_MODEL']}"
        except Exception as exc:  # noqa: BLE001
            use_reranker = False
            reranker_note = "FALLBACK ohne neuronalen Reranker: " + strip_key(f"{type(exc).__name__}: {exc}")
            print(strip_key(reranker_note), flush=True)

    rows: List[Dict] = []
    tav_lat: List[float] = []
    own_lat: List[float] = []
    jac: List[float] = []
    rbos: List[float] = []
    tav_hits: List[int] = []
    own_hits: List[int] = []
    tav_err = 0
    own_err = 0

    with httpx.Client() as client:
        for i, q in enumerate(queries):
            qid, query, lang = q["id"], q["query"], q.get("lang", "en")
            print(f"[{i+1}/{len(queries)}] {qid}", flush=True)
            row: Dict = {"id": qid, "lang": lang}

            tav = None
            try:
                tav = call_tavily(client, query, api_key, args.timeout)
                tav_lat.append(tav["latency_s"])
                tav_hits.append(tav["n_results"])
                row.update(
                    {
                        "tavily_urls": tav["urls"],
                        "tavily_scores": tav["scores"],
                        "tavily_answer_len": tav["answer_len"],
                        "tavily_n": tav["n_results"],
                        "tavily_latency_s": tav["latency_s"],
                    }
                )
            except Exception as exc:  # noqa: BLE001
                tav_err += 1
                row["tavily_error"] = strip_key(f"{type(exc).__name__}: {exc}")

            own = None
            try:
                own = run_own(query, lang, args.candidate_pool, args.max_results, use_reranker)
                own_lat.append(own["latency_s"])
                own_hits.append(own["n_results"])
                row.update(
                    {
                        "own_urls": own["urls"],
                        "own_n": own["n_results"],
                        "own_sources_fetched": own["n_sources_fetched"],
                        "own_latency_s": own["latency_s"],
                    }
                )
            except Exception as exc:  # noqa: BLE001
                own_err += 1
                row["own_error"] = strip_key(f"{type(exc).__name__}: {exc}")

            if tav and own:
                j = jaccard_urls(tav["urls"], own["urls"])
                r = rbo(tav["urls"], own["urls"], p=0.9)
                row["jaccard_top10"] = j
                row["rbo"] = r
                jac.append(j)
                rbos.append(r)

            rows.append(row)
            if i < len(queries) - 1:
                time.sleep(args.pause)

    summary = {
        "n_queries": len(queries),
        "reranker": reranker_note,
        "answer_step_own": "WEGGELASSEN (claude-code-api liegt auf dem VPS) - Antwort-Vergleich offen",
        "search_backend_own": "ddgs (DuckDuckGo) als EVAL-SUBSTITUT fuer SearXNG; echte Architektur bleibt SearXNG",
        "tavily_params": {"search_depth": "basic", "include_answer": "basic", "max_results": 10},
        "latency_tavily_p50_s": p50(tav_lat),
        "latency_tavily_p95_s": p95(tav_lat),
        "latency_own_p50_s": p50(own_lat),
        "latency_own_p95_s": p95(own_lat),
        "avg_hits_tavily": round(statistics.mean(tav_hits), 2) if tav_hits else None,
        "avg_hits_own": round(statistics.mean(own_hits), 2) if own_hits else None,
        "avg_jaccard_top10": round(statistics.mean(jac), 4) if jac else None,
        "median_jaccard_top10": round(statistics.median(jac), 4) if jac else None,
        "avg_rbo": round(statistics.mean(rbos), 4) if rbos else None,
        "median_rbo": round(statistics.median(rbos), 4) if rbos else None,
        "errors_tavily": tav_err,
        "errors_own": own_err,
        "error_rate_tavily": round(tav_err / len(queries), 3) if queries else None,
        "error_rate_own": round(own_err / len(queries), 3) if queries else None,
    }

    out_json = PRIVATE_DIR / "compare.json"
    out_json.write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    write_markdown(PRIVATE_DIR / "compare.md", summary, rows)

    print("\nFertig. Ausgaben (gitignored, NICHT veroeffentlichen):")
    print(f"  {out_json}")
    print(f"  {PRIVATE_DIR / 'compare.md'}")


def write_markdown(path: Path, summary: Dict, rows: List[Dict]) -> None:
    def g(row, key, default="-"):
        v = row.get(key, default)
        return default if v is None else v

    lines: List[str] = []
    lines.append("# Interner Vergleich: Tavily vs. eigener RAG-Dienst (Retrieval)")
    lines.append("")
    lines.append("> NUR interner Eigengebrauch. NICHT veroeffentlichen. Liegt in eval/private/ (gitignored).")
    lines.append("> Kein Qualitaetsurteil - rein deskriptiv (Latenz, URL-Overlap, Trefferzahl).")
    lines.append("")
    lines.append("## Aufbau")
    lines.append("")
    lines.append(f"- Queries: {summary['n_queries']} (DE/EN, eval/queries.jsonl)")
    lines.append(f"- Tavily: `POST api.tavily.com/search`, Bearer-Key, Parameter {summary['tavily_params']}")
    lines.append(f"- Eigene Seite Such-Backend: {summary['search_backend_own']}")
    lines.append(f"- Eigene Seite Reranker: {summary['reranker']}")
    lines.append("- Eigene Seite Pipeline: ddgs -> trafilatura + Chunking -> Dedup + Rerank -> Merge je Quelle (Top-10, max_results=10)")
    lines.append(f"- Answer-Schritt eigene Seite: {summary['answer_step_own']}")
    lines.append("")
    lines.append("## Gesamt")
    lines.append("")
    lines.append("| Metrik | Tavily | Eigener Dienst |")
    lines.append("|---|---|---|")
    lines.append(f"| Latenz p50 (s) | {summary['latency_tavily_p50_s']} | {summary['latency_own_p50_s']} |")
    lines.append(f"| Latenz p95 (s) | {summary['latency_tavily_p95_s']} | {summary['latency_own_p95_s']} |")
    lines.append(f"| Treffer je Query (Schnitt) | {summary['avg_hits_tavily']} | {summary['avg_hits_own']} |")
    lines.append(f"| Fehler (von {summary['n_queries']}) | {summary['errors_tavily']} | {summary['errors_own']} |")
    lines.append(f"| Fehlerrate | {summary['error_rate_tavily']} | {summary['error_rate_own']} |")
    lines.append("")
    lines.append("| Overlap-Metrik | Schnitt | Median |")
    lines.append("|---|---|---|")
    lines.append(f"| Jaccard (Top-10, URLs) | {summary['avg_jaccard_top10']} | {summary['median_jaccard_top10']} |")
    lines.append(f"| RBO (rang-gewichtet, p=0.9) | {summary['avg_rbo']} | {summary['median_rbo']} |")
    lines.append("")
    lines.append("## Je Query")
    lines.append("")
    lines.append("| ID | Lang | Lat. Tavily (s) | Lat. eigen (s) | Treffer T/E | Jaccard | RBO | answer-Len (T) |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for r in rows:
        tn = g(r, "tavily_n")
        on = g(r, "own_n")
        hits = f"{tn}/{on}"
        err = ""
        if r.get("tavily_error"):
            err += " [Tavily-Fehler]"
        if r.get("own_error"):
            err += " [eigen-Fehler]"
        lines.append(
            f"| {r['id']}{err} | {r.get('lang','-')} | {g(r,'tavily_latency_s')} | "
            f"{g(r,'own_latency_s')} | {hits} | {g(r,'jaccard_top10')} | {g(r,'rbo')} | "
            f"{g(r,'tavily_answer_len')} |"
        )
    lines.append("")
    lines.append("## Hinweise")
    lines.append("")
    lines.append("- Latenz eigener Dienst enthaelt Live-Fetch + Extraktion + Rerank je Query; Tavily liefert aus einem Vorab-Index. Der Latenzunterschied ist erwartet und kein Qualitaetsurteil.")
    lines.append("- URL-Overlap vergleicht kanonisierte Host+Pfad-Listen (Top-10). Niedriger Overlap heisst unterschiedliche Quellenauswahl, nicht schlechtere Qualitaet.")
    lines.append("- Antwort-Qualitaet (answer) wurde NICHT verglichen (eigener answer-Schritt ausgelassen).")
    lines.append("- Kein API-Key steht in dieser Datei oder in compare.json.")
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
