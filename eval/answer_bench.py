"""Antwort-Qualitaet (include_answer) gegen gespeicherte Tavily-Antworten.

Ablauf:
1. `--base` (eigene Instanz, z. B. :8006 mit REPLAY_DIR) wird fuer jede Frage aus
   eval/private/tavily_cache.json mit gespeichertem `answer` gefragt
   (include_answer="basic", max_results 10, search_depth basic wie bench.py).
   Anfragen strikt nacheinander, mindestens `--gap` Sekunden Abstand.
2. Deterministisch: answer_re-Treffer, [n]-Marker, Laenge, Latenz.
3. Blind-Richter (OpenAI-kompatibler Endpunkt aus JUDGE_BASE_URL/JUDGE_API_KEY/JUDGE_MODEL):
   beide Antworten ohne [n]-Marker, Reihenfolge zufaellig (fester Seed), System verborgen;
   Urteil korrekt ja/teilweise/nein, erfundene Fakten, Note 1-5.

Nie Calls an Tavily: die Tavily-Seite kommt nur aus dem Cache.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import statistics
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
MARKER = re.compile(r"\s?\[\d+(?:\s*,\s*\d+)*\]")

JUDGE_PROMPT = """Du bewertest zwei Antworten (A und B) auf dieselbe Frage. Bekannte richtige Antwort: {truth}

Frage: {query}

Antwort A:
{a}

Antwort B:
{b}

Bewerte jede Antwort einzeln. Gib NUR JSON zurueck:
{{"A": {{"korrekt": "ja|teilweise|nein", "erfunden": <Anzahl sachlich falscher oder unbelegbarer Zusatzbehauptungen>, "note": <1-5, 5 = beste Antwort fuer einen Nutzer: richtig, direkt, nuetzlich, nicht aufgeblaeht>, "grund": "<max 20 Woerter>"}},
 "B": {{...gleich...}}}}"""


def load_items(limit: int):
    cache = json.loads((HERE / "private" / "tavily_cache.json").read_text())
    meta = {}
    for f in sorted(HERE.glob("queries*.jsonl")):
        for line in f.read_text().splitlines():
            if line.strip():
                o = json.loads(line)
                meta.setdefault(o["query"], o)
    items = []
    for q, v in cache.items():
        if v.get("answer") and q in meta:
            m = meta[q]
            items.append({"id": m["id"], "query": q, "lang": m.get("lang"),
                          "answer_re": m.get("answer_re"), "truth": m.get("ground_truth", ""),
                          "tavily": {"answer": v["answer"], "latency_s": v.get("latency_s")}})
    return items[:limit]


def ask_ours(client: httpx.Client, base: str, it: dict, tries: int = 3) -> dict:
    body = {"query": it["query"], "search_depth": "basic", "include_answer": "basic",
            "max_results": 10, "language": it.get("lang")}
    last = None
    for _ in range(tries):
        t0 = time.perf_counter()
        try:
            r = client.post(f"{base}/search", json=body, timeout=120)
            dt = time.perf_counter() - t0
            d = r.json()
            usage = d.get("usage") or {}
            out = {"answer": d.get("answer"), "latency_s": round(dt, 3),
                   "timings_ms": usage.get("timings") or usage.get("timings_ms"),
                   "answer_error": usage.get("answer_error"), "status": r.status_code}
            if out["answer"] or out["answer_error"] not in ("llm_unavailable",):
                return out
            last = out
        except httpx.HTTPError as exc:  # Hotspot-Abbruch: erneut versuchen
            last = {"answer": None, "error": type(exc).__name__}
        time.sleep(3)
    return last


def judge(client: httpx.Client, it: dict, ours: str, theirs: str, rng: random.Random) -> dict:
    flip = rng.random() < 0.5
    a, b = (theirs, ours) if flip else (ours, theirs)
    prompt = JUDGE_PROMPT.format(truth=it["truth"] or it["answer_re"], query=it["query"],
                                 a=MARKER.sub("", a or "(keine Antwort)"),
                                 b=MARKER.sub("", b or "(keine Antwort)"))
    for _ in range(3):
        try:
            r = client.post(f"{os.environ['JUDGE_BASE_URL']}/chat/completions", timeout=180,
                            headers={"Authorization": f"Bearer {os.environ['JUDGE_API_KEY']}"},
                            json={"model": os.environ.get("JUDGE_MODEL", "opus"), "temperature": 0,
                                  "messages": [{"role": "user", "content": prompt}]})
            txt = r.json()["choices"][0]["message"]["content"]
            v = json.loads(txt[txt.index("{"): txt.rindex("}") + 1])
            return {"ours": v["B" if flip else "A"], "tavily": v["A" if flip else "B"], "ours_was": "B" if flip else "A"}
        except Exception as exc:  # noqa: BLE001
            err = type(exc).__name__
            time.sleep(3)
    return {"error": err}


def det(answer: str, rx: str) -> dict:
    a = answer or ""
    return {"re_hit": bool(rx and re.search(rx, a, re.I)), "markers": len(MARKER.findall(a)),
            "chars": len(a), "words": len(a.split())}


def summarize(rows: list) -> dict:
    out = {}
    for side in ("ours", "tavily"):
        rs = [r for r in rows if r[side].get("answer")]
        j = [r["judge"][side] for r in rows if "error" not in r.get("judge", {"error": 1})]
        lat = [r[side]["latency_s"] for r in rs if r[side].get("latency_s")]
        out[side] = {
            "n": len(rows), "answered": len(rs),
            "re_hit": sum(r[side + "_det"]["re_hit"] for r in rows),
            "with_markers": sum(r[side + "_det"]["markers"] > 0 for r in rows),
            "median_words": statistics.median([r[side + "_det"]["words"] for r in rs]) if rs else None,
            "median_latency_s": statistics.median(lat) if lat else None,
            "judge_ja": sum(x.get("korrekt") == "ja" for x in j),
            "judge_teilweise": sum(x.get("korrekt") == "teilweise" for x in j),
            "judge_nein": sum(x.get("korrekt") == "nein" for x in j),
            "judge_invented": sum(int(x.get("erfunden") or 0) for x in j),
            "judge_note_mean": round(statistics.mean([float(x.get("note") or 0) for x in j]), 2) if j else None,
        }
    ans_ms = [r["ours"]["timings_ms"].get("answer") for r in rows
              if isinstance(r["ours"].get("timings_ms"), dict) and r["ours"]["timings_ms"].get("answer")]
    out["ours"]["median_answer_stage_s"] = round(statistics.median(ans_ms) / 1000, 2) if ans_ms else None
    wins = [r["judge"] for r in rows if "error" not in r.get("judge", {"error": 1})]
    out["note_wins"] = {"ours": sum(float(w["ours"].get("note", 0)) > float(w["tavily"].get("note", 0)) for w in wins),
                        "tavily": sum(float(w["ours"].get("note", 0)) < float(w["tavily"].get("note", 0)) for w in wins),
                        "tie": sum(float(w["ours"].get("note", 0)) == float(w["tavily"].get("note", 0)) for w in wins)}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8006")
    ap.add_argument("--limit", type=int, default=30)
    ap.add_argument("--gap", type=float, default=1.1, help="Sekunden zwischen Anfragen (SearXNG-Schonung)")
    ap.add_argument("--seed", type=int, default=14)
    ap.add_argument("--rows", default="", help="vorhandene Zeilen (JSON) wiederverwenden, nur fehlende neu")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    items = load_items(a.limit)
    prev = {}
    if a.rows and Path(a.rows).exists():
        prev = {r["id"]: r for r in json.loads(Path(a.rows).read_text()).get("rows", [])}
    rng = random.Random(a.seed)
    rows = []
    with httpx.Client() as client:
        for it in items:
            r = prev.get(it["id"])
            if not r or not r["ours"].get("answer"):
                r = {"id": it["id"], "query": it["query"], "tavily": it["tavily"]}
                r["ours"] = ask_ours(client, a.base, it)
                time.sleep(a.gap)
            r["ours_det"] = det(r["ours"].get("answer"), it["answer_re"])
            r["tavily_det"] = det(it["tavily"]["answer"], it["answer_re"])
            if "judge" not in r or "error" in r["judge"]:
                r["judge"] = judge(client, it, r["ours"].get("answer"), it["tavily"]["answer"], rng) \
                    if os.environ.get("JUDGE_BASE_URL") else {"error": "no_judge"}
            rows.append(r)
            print(it["id"], r["ours"].get("latency_s"), r["ours_det"]["re_hit"],
                  r["judge"].get("ours", {}).get("korrekt"), r["judge"].get("tavily", {}).get("korrekt"),
                  file=sys.stderr, flush=True)
    res = {"date": time.strftime("%Y-%m-%d %H:%M"), "base": a.base, "summary": summarize(rows), "rows": rows}
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print(json.dumps(res["summary"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
