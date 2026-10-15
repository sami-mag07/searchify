#!/usr/bin/env python3
"""Such-Eval: findet die Suchkette die Programme, die sich in echten Recherchen bewährt haben?

Goldliste = Links aus tasks.json (von Sami übernommene Funde). Pro Aufgabe laufen
feste Start-Queries gegen jede Suchmaschine einzeln. `record` speichert die rohen
Treffer (mit Rang je Maschine) als Kassette, `score` rechnet offline daraus, damit
Ranking-Varianten nichts kosten.

  search_eval.py record [--engines tavily,serper,...] [--n 20]   # live, kostet Credits
  search_eval.py score [--cassette FILE] [--ranking count|rrf|...]
  search_eval.py engines                                          # Beitrag je Suchmaschine

Metriken je Aufgabe:
  site@k  Anteil der Gold-Domains, die in den Top k irgendeiner Query vorkommen
  page@k  Anteil der Gold-URLs (normalisiert), die genau so gefunden werden
  mrr     mittlerer Kehrwert des besten Gold-Rangs je Query (Ranking-Güte)
"""
import argparse
import json
import os
import sys
import time
import types
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "references"))
import search  # noqa: E402

TASKS = os.path.join(HERE, "tasks.json")
CASS_DIR = os.path.join(HERE, "cassettes")


def host(u):
    return urllib.parse.urlsplit(u).netloc.lower().removeprefix("www.")


# Sammel-Domains: ein Treffer auf der Startseite ist dort kein Fund des Programms.
BROAD = {"linkedin.com", "instagram.com", "facebook.com", "x.com", "twitter.com",
         "youtube.com", "medium.com", "google.com", "forms.gle", "docs.google.com",
         "eventbrite.com", "eventbrite.de", "lu.ma", "luma.com", "meetup.com", "github.com"}


def site_key(u):
    """Domain, bei Sammel-Plattformen Domain plus erstes Pfadsegment."""
    h = host(u)
    if h in BROAD:
        seg = urllib.parse.urlsplit(u).path.strip("/").split("/")[0]
        return f"{h}/{seg}"
    return h


def opts(n):
    return types.SimpleNamespace(n=n, site=[], exclude=[], lang=None, news=False,
                                 since=None, since_d=None, engine=None, fan=False)


def record(a):
    tasks = json.load(open(TASKS))["tasks"]
    keys = search.load_keys()
    engines = a.engines.split(",")
    jobs = [(t["id"], q, e) for t in tasks for q in t[a.field] for e in engines]
    o = opts(a.n)

    def one(job):
        tid, q, e = job
        t0 = time.time()
        try:
            hits = search.ENGINES[e](q, o, keys)
            err = None
        except search.EngineDown as ex:
            hits, err = [], str(ex)
        except Exception as ex:  # eine kaputte Maschine darf den Lauf nicht abbrechen
            hits, err = [], f"{type(ex).__name__}: {ex}"
        return {"task": tid, "query": q, "engine": e, "sec": round(time.time() - t0, 1),
                "error": err, "hits": [{"url": h["url"], "title": h["title"]} for h in hits]}

    with ThreadPoolExecutor(a.workers) as ex:
        runs = []
        for i, r in enumerate(ex.map(one, jobs), 1):
            runs.append(r)
            print(f"\r{i}/{len(jobs)}", end="", file=sys.stderr)
    print(file=sys.stderr)
    os.makedirs(CASS_DIR, exist_ok=True)
    path = os.path.join(CASS_DIR, time.strftime("%Y-%m-%d_%H%M") + ".json")
    json.dump({"engines": engines, "n": a.n, "field": a.field, "runs": runs}, open(path, "w"), ensure_ascii=False, indent=1)
    print(path)


# ---- Ranking-Varianten: (Liste von (engine, [urls]) einer Query) -> geordnete URLs ----

def rank_count(per_engine):
    """Heutiges search.py-Verhalten: Maschinen in Kettenfolge anhängen, dann merge."""
    hits = [search.hit("", u, "", "", e) for e, urls in per_engine for u in urls]
    return [h["url"] for h in search.merge(hits)]


def rank_rrf(per_engine, k=60):
    score, first = {}, {}
    for e, urls in per_engine:
        for r, u in enumerate(urls, 1):
            key = search.norm_url(u)
            score[key] = score.get(key, 0) + 1 / (k + r)
            first.setdefault(key, u)
    return [first[x] for x in sorted(score, key=lambda x: -score[x])]


RANKINGS = {"count": rank_count, "rrf": rank_rrf}


def latest():
    files = sorted(f for f in os.listdir(CASS_DIR) if f.endswith(".json"))
    if not files:
        sys.exit("keine Kassette, erst `record`")
    return os.path.join(CASS_DIR, files[-1])


def load_runs(path, engines=None):
    """path = eine Kassette; mit ALL alle Kassetten, neuere überschreiben ältere."""
    files = ([os.path.join(CASS_DIR, f) for f in sorted(os.listdir(CASS_DIR)) if f.endswith(".json")]
             if path == "ALL" else [path])
    cas = {"engines": [], "runs": []}
    for f in files:
        c = json.load(open(f))
        cas["engines"] += [e for e in c["engines"] if e not in cas["engines"]]
        cas["runs"] += c["runs"]
    order = [e for e in search.CHAIN if e in cas["engines"]]
    grouped = {}
    for r in cas["runs"]:
        if engines and r["engine"] not in engines:
            continue
        grouped.setdefault((r["task"], r["query"]), {})[r["engine"]] = [h["url"] for h in r["hits"]]
    return cas, order, grouped


def evaluate(path, ranking, engines=None, ks=(10, 30), fields=("queries",)):
    tasks = json.load(open(TASKS))["tasks"]
    cas, order, grouped = load_runs(path, engines)
    rank = RANKINGS[ranking]
    rows = []
    for t in tasks:
        gold_pages = {search.norm_url(u) for u in t["gold"]}
        gold_sites = {site_key(u) for u in t["gold"]}
        found_sites = {k: set() for k in ks}
        found_pages = {k: set() for k in ks}
        rr = []
        for q in [q for f in fields for q in t.get(f, [])]:
            per = [(e, grouped.get((t["id"], q), {}).get(e, [])) for e in order]
            ranked = rank(per)
            best = next((i for i, u in enumerate(ranked, 1) if site_key(u) in gold_sites), None)
            rr.append(1 / best if best else 0)
            for k in ks:
                top = ranked[:k]
                found_sites[k] |= {site_key(u) for u in top} & gold_sites
                found_pages[k] |= {search.norm_url(u) for u in top} & gold_pages
        rows.append({"task": t["id"], "gold": len(gold_sites),
                     **{f"site@{k}": len(found_sites[k]) / len(gold_sites) for k in ks},
                     **{f"page@{k}": len(found_pages[k]) / len(gold_pages) for k in ks},
                     "mrr": sum(rr) / len(rr)})
    return rows


def show(rows, title):
    cols = [c for c in rows[0] if c not in ("task", "gold")]
    print(f"\n{title}\n| Aufgabe | Gold-Domains | " + " | ".join(cols) + " |")
    print("|---|---:|" + "---:|" * len(cols))
    for r in rows:
        print(f"| {r['task']} | {r['gold']} | " + " | ".join(f"{r[c]:.2f}" for c in cols) + " |")
    avg = {c: sum(r[c] for r in rows) / len(rows) for c in cols}
    print("| **Mittel** | | " + " | ".join(f"**{avg[c]:.2f}**" for c in cols) + " |")
    return avg


def score(a):
    path = a.cassette or "ALL"
    fields = tuple(a.fields.split(","))
    show(evaluate(path, a.ranking, engines=set(a.engines.split(",")) if a.engines else None, fields=fields),
         f"Kassette {os.path.basename(path)}, Ranking {a.ranking}, Queries {'+'.join(fields)}")


def engines_report(a):
    path = a.cassette or latest()
    cas, order, _ = load_runs(path)
    print(f"\nBeitrag je Suchmaschine allein (Ranking count), Kassette {os.path.basename(path)}")
    print("| Suchmaschine | site@30 | page@30 | mrr | Fehler | Sek. median |\n|---|---:|---:|---:|---:|---:|")
    for e in order:
        rows = evaluate(path, "count", engines={e})
        runs = [r for r in cas["runs"] if r["engine"] == e]
        secs = sorted(r["sec"] for r in runs)
        errs = sum(1 for r in runs if r["error"])
        avg = lambda c: sum(r[c] for r in rows) / len(rows)
        print(f"| {e} | {avg('site@30'):.2f} | {avg('page@30'):.2f} | {avg('mrr'):.2f} | {errs}/{len(runs)} | {secs[len(secs) // 2]:.1f} |")
    for rk in RANKINGS:
        rows = evaluate(path, rk)
        avg = lambda c: sum(r[c] for r in rows) / len(rows)
        print(f"| alle, {rk} | {avg('site@30'):.2f} | {avg('page@30'):.2f} | {avg('mrr'):.2f} | | |")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("--engines", default="tavily,serper,exa,donsetch,hound")
    r.add_argument("--n", type=int, default=20)
    r.add_argument("--workers", type=int, default=6)
    r.add_argument("--field", default="queries", help="Query-Feld in tasks.json")
    r.set_defaults(fn=record)
    s = sub.add_parser("score")
    s.add_argument("--cassette")
    s.add_argument("--ranking", choices=list(RANKINGS), default="count")
    s.add_argument("--fields", default="queries")
    s.add_argument("--engines")
    s.set_defaults(fn=score)
    e = sub.add_parser("engines")
    e.add_argument("--cassette")
    e.set_defaults(fn=engines_report)
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
