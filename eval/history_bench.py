#!/usr/bin/env python3
"""Such-Historien-Bench: eigene Endpunkte gegen historische Kassetten der Originale.

Fragt die Queries aus ~/.claude/skills/research-agent/evals/tasks.json gegen den lokalen
Dienst ab (own-tavily, own-serper, own-exa), schreibt eine Kassette im Format von
search_eval.py, fuehrt sie mit der historischen Kassette zusammen und bewertet je
Engine mit search_eval.evaluate (Ranking count, Engine allein).

  history_bench.py run   [--field queries|queries_niche|both] [--engines own-tavily,...]
  history_bench.py score --cassette eval/private/history-<datum>-<feld>.json [--field ...]

Ohne Netz bewertet `score` nur. Historie je Feld: queries -> 2026-10-01_0009,
queries_niche -> 2026-10-01_0012 (per --history ueberschreibbar).
Optional HISTORY_BENCH_KEY (Umgebungsvariable) als Bearer-Token, falls der Dienst Auth will.
"""
import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request

EVALS = os.path.expanduser("~/.claude/skills/research-agent/evals")
sys.path.insert(0, EVALS)
import search_eval  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
PRIV = os.path.join(HERE, "private")
BASE = "http://127.0.0.1:8000"
OWN = ["own-tavily", "own-serper", "own-exa"]
HISTORY = {"queries": "2026-10-01_0009.json", "queries_niche": "2026-10-01_0012.json"}
HIST_ENGINES = ["tavily", "serper", "exa"]  # donsetch/hound sind keine Originale mit Key

# Eigene Engines in die Kettenfolge haengen, sonst ignoriert load_runs sie (nur zur Laufzeit).
for _e in OWN:
    if _e not in search_eval.search.CHAIN:
        search_eval.search.CHAIN.append(_e)

ENDPOINTS = {
    "own-tavily": ("/search", lambda q, n: {"query": q, "max_results": n, "include_answer": False},
                   lambda d: [(r["url"], r.get("title", "")) for r in d.get("results", [])]),
    "own-serper": ("/serper/search", lambda q, n: {"q": q, "num": n},
                   lambda d: [(r["link"], r.get("title", "")) for r in d.get("organic", [])]),
    "own-exa": ("/exa/search", lambda q, n: {"query": q, "numResults": n},
                lambda d: [(r["url"], r.get("title") or "") for r in d.get("results", [])]),
}


def call(engine, query, n, timeout):
    path, body, parse = ENDPOINTS[engine]
    headers = {"content-type": "application/json"}
    if os.environ.get("HISTORY_BENCH_KEY"):
        headers["authorization"] = "Bearer " + os.environ["HISTORY_BENCH_KEY"]
    req = urllib.request.Request(BASE + path, json.dumps(body(query, n)).encode(), headers)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.load(resp)
        hits, err = parse(data), None
    except urllib.error.HTTPError as ex:
        hits, err = [], f"HTTP {ex.code}: {ex.read()[:300].decode('utf-8', 'replace')}"
    except Exception as ex:  # ein kaputter Endpunkt darf den Lauf nicht abbrechen
        hits, err = [], f"{type(ex).__name__}: {ex}"
    return {"engine": engine, "sec": round(time.time() - t0, 1), "error": err,
            "hits": [{"url": u, "title": t} for u, t in hits]}


def fields_of(a):
    return ["queries", "queries_niche"] if a.field == "both" else [a.field]


def record(field, engines, n, pause, timeout, stamp):
    tasks = json.load(open(search_eval.TASKS))["tasks"]
    jobs = [(t["id"], q, e) for t in tasks for q in t.get(field, []) for e in engines]
    runs = []
    for i, (tid, q, e) in enumerate(jobs, 1):
        if i > 1:
            time.sleep(pause)
        r = call(e, q, n, timeout)
        runs.append({"task": tid, "query": q, **r})
        flag = f" FEHLER {r['error'][:120]}" if r["error"] else ""
        print(f"{i}/{len(jobs)} {e} {r['sec']}s {len(r['hits'])} Treffer{flag}", file=sys.stderr)
    os.makedirs(PRIV, exist_ok=True)
    path = os.path.join(PRIV, f"history-{stamp}-{field}.json")
    json.dump({"engines": engines, "n": n, "field": field, "runs": runs}, open(path, "w"),
              ensure_ascii=False, indent=1)
    print(path)
    return path


def merge(own_path, hist_path, field):
    """Historische Laeufe (nur Originale) plus eigene, gleiche task+query."""
    own, hist = json.load(open(own_path)), json.load(open(hist_path))
    keys = {(r["task"], r["query"]) for r in own["runs"]}
    hist_runs = [r for r in hist["runs"] if r["engine"] in HIST_ENGINES and (r["task"], r["query"]) in keys]
    engines = [e for e in HIST_ENGINES if any(r["engine"] == e for r in hist_runs)] + own["engines"]
    out = own_path.replace(".json", "-merged.json")
    json.dump({"engines": engines, "n": own["n"], "field": field, "runs": hist_runs + own["runs"]},
              open(out, "w"), ensure_ascii=False, indent=1)
    return out, engines


def report(merged, engines, field):
    cas = json.load(open(merged))
    rows = []
    for e in engines:
        ev = search_eval.evaluate(merged, "count", engines={e}, fields=(field,))
        runs = [r for r in cas["runs"] if r["engine"] == e]
        secs = [r["sec"] for r in runs]
        avg = lambda c: sum(r[c] for r in ev) / len(ev)  # noqa: E731
        rows.append({"engine": e, "site@10": round(avg("site@10"), 3), "site@30": round(avg("site@30"), 3),
                     "page@30": round(avg("page@30"), 3), "mrr": round(avg("mrr"), 3),
                     "errors": sum(1 for r in runs if r["error"]), "runs": len(runs),
                     "sec_median": round(statistics.median(secs), 1) if secs else None,
                     "per_task": ev})
    print(f"\nFeld {field}, Kassette {os.path.basename(merged)}, Engine allein, Ranking count")
    print("| Engine | site@10 | site@30 | page@30 | mrr | Fehler | Sek. median |\n|---|---:|---:|---:|---:|---:|---:|")
    for r in rows:
        print(f"| {r['engine']} | {r['site@10']:.2f} | {r['site@30']:.2f} | {r['page@30']:.2f} | {r['mrr']:.2f} "
              f"| {r['errors']}/{r['runs']} | {r['sec_median']} |")
    return rows


def write_score(results, stamp):
    path = os.path.join(PRIV, f"history-score-{stamp}.json")
    json.dump(results, open(path, "w"), ensure_ascii=False, indent=1)
    print(f"\n{path}")


def hist_path(a, field):
    return a.history or os.path.join(search_eval.CASS_DIR, HISTORY[field])


def cmd_run(a):
    stamp = time.strftime("%Y-%m-%d_%H%M")
    engines = a.engines.split(",")
    results = {}
    for field in fields_of(a):
        own = record(field, engines, a.n, a.pause, a.timeout, stamp)
        merged, eng = merge(own, hist_path(a, field), field)
        results[field] = {"cassette": own, "history": hist_path(a, field), "engines": report(merged, eng, field)}
    write_score(results, stamp)


def cmd_score(a):
    own = json.load(open(a.cassette))
    field = a.field if a.field != "both" else own.get("field", "queries")
    merged, eng = merge(a.cassette, hist_path(a, field), field)
    stamp = time.strftime("%Y-%m-%d_%H%M")
    write_score({field: {"cassette": a.cassette, "history": hist_path(a, field),
                         "engines": report(merged, eng, field)}}, stamp)


def main():
    global BASE
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--field", default="queries", choices=["queries", "queries_niche", "both"])
    r.add_argument("--engines", default=",".join(OWN))
    r.add_argument("--n", type=int, default=20)
    r.add_argument("--pause", type=float, default=1.0)
    r.add_argument("--timeout", type=float, default=90)
    r.add_argument("--base", default=BASE, help="Dienst-Adresse (eigene Instanz je Agent, z. B. :8001)")
    r.add_argument("--history", help="historische Kassette statt Standard je Feld")
    r.set_defaults(fn=cmd_run)
    s = sub.add_parser("score")
    s.add_argument("--cassette", required=True)
    s.add_argument("--field", default="both", help="Standard: Feld aus der Kassette")
    s.add_argument("--history")
    s.set_defaults(fn=cmd_score)
    a = ap.parse_args()
    BASE = getattr(a, "base", BASE).rstrip("/")
    if a.cmd == "run" and a.pause < 1:
        sys.exit("--pause muss >= 1 s sein (Upstream schonen)")
    a.fn(a)


if __name__ == "__main__":
    main()
