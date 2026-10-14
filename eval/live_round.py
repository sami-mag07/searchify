#!/usr/bin/env python3
"""Live-Messrunde gegen den laufenden Dienst (nur eigene Endpunkte, nie Tavily/Serper/Exa).

Unterbefehle, alle strikt nacheinander mit Pause (SearXNG hoechstens ~1 Anfrage/s):

  blind     neue Blind-Runde: Top 3 von /search fuer die 30 Fragen aus eval/queries.jsonl,
            dazu Tavily (Cache) und die alten Nachbau-Items (a, b) aus --old;
            alles neu gemischt mit neuen ids -> --out/{items,key}.json
  blindeval Auswertung --out/{key,urteile}.json je System und Frage
  kontakte  8 Offshore-Kontakt-Suchen gegen /search und /serper/search (je 10)
  news      15 Nachrichten-Themen gegen /search (topic news) und /serper/news, Alter der Treffer
  answers   Fragen mit gespeicherter Tavily-Antwort gegen /search include_answer=true
  latency   N frische Fragen reihum an /search, /serper/search, /exa/search

  .venv/bin/python eval/live_round.py news --out eval/private/news-2026-10-14.json
"""

from __future__ import annotations

import argparse
import collections
import json
import random
import re
import statistics
import time
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
PRIV = HERE / "private"
AUSZUG = 200


def post(base, path, body, timeout=90.0):
    req = urllib.request.Request(base + path, json.dumps(body).encode(), {"content-type": "application/json"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data, err = json.load(r), None
    except urllib.error.HTTPError as ex:
        data, err = None, f"HTTP {ex.code}"
    except Exception as ex:  # Messung darf nicht abbrechen
        data, err = None, type(ex).__name__
    return data, err, round(time.time() - t0, 2)


def clip(text, n=AUSZUG):
    text = re.sub(r"\s+", " ", text or "").strip()
    if len(text) <= n:
        return text
    cut = text[:n]
    sp = cut.rfind(" ")
    return (cut[:sp] if sp > n * 0.6 else cut).rstrip() + " ..."


# ---------- blind ----------

def cmd_blind(a):
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    queries = [json.loads(l) for l in open(HERE / "queries.jsonl", encoding="utf-8") if l.strip()]
    cache = json.loads((PRIV / "tavily_cache.json").read_text(encoding="utf-8"))
    old_items = json.loads((Path(a.old) / "items.json").read_text(encoding="utf-8"))
    old_key = json.loads((Path(a.old) / "key.json").read_text(encoding="utf-8"))
    rng = random.Random(20261014)
    items, key, errors = [], {}, 0
    for i, q in enumerate(queries):
        if i:
            time.sleep(a.pause)
        pool = [("tavily", r["url"], clip(r.get("content")))
                for r in cache[q["query"] + "|noanswer"]["results"][:3]]
        pool += [(old_key[it["id"]], it["url"], it["auszug"]) for it in old_items
                 if it["frage"] == q["query"] and old_key[it["id"]] in ("a", "b")]
        data, err, sec = post(a.base, "/search", {"query": q["query"], "max_results": 3,
                                                   "include_answer": False, "language": q.get("lang")})
        if err:
            errors += 1
            print("Fehler", q["id"], err)
        for r in (data or {}).get("results", [])[:3]:
            pool.append(("neu", r["url"], clip(r.get("content"))))
        print(q["id"], sec, "s")
        rng.shuffle(pool)
        for side, url, auszug in pool:
            iid = f"{q['id']}-{rng.randrange(16**6):06x}"
            while iid in key:
                iid = f"{q['id']}-{rng.randrange(16**6):06x}"
            key[iid] = side
            items.append({"id": iid, "frage": q["query"], "url": url, "titel": "", "auszug": auszug})
    (out / "items.json").write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "key.json").write_text(json.dumps(key, indent=1), encoding="utf-8")
    print(len(items), "Items", collections.Counter(key.values()), "Fehler", errors)


def cmd_blindeval(a):
    out = Path(a.out)
    key = json.loads((out / "key.json").read_text())
    urteile = json.loads((out / "urteile.json").read_text())
    items = {it["id"]: it for it in json.loads((out / "items.json").read_text())}
    assert sorted(u["id"] for u in urteile) == sorted(key), "ids passen nicht"
    per = collections.defaultdict(list)
    perq = collections.defaultdict(lambda: collections.defaultdict(list))
    for u in urteile:
        s = key[u["id"]]
        per[s].append(u)
        perq[items[u["id"]]["frage"]][s].append(u["vertrauen"])
    res = {"systeme": {}, "je_frage": {}}
    for s, us in sorted(per.items()):
        n = len(us)
        res["systeme"][s] = {
            "n": n, "vertrauen": round(sum(u["vertrauen"] for u in us) / n, 2),
            "anteil_4_5": round(sum(u["vertrauen"] >= 4 for u in us) / n, 3),
            "anteil_1_2": round(sum(u["vertrauen"] <= 2 for u in us) / n, 3),
            "beantwortet_ja": sum(u["beantwortet"] == "ja" for u in us),
            "beantwortet_ja_anteil": round(sum(u["beantwortet"] == "ja" for u in us) / n, 3),
            "seo_kommerz": sum(u["typ"] == "seo_kommerz" for u in us)}
    for other in ("neu", "a", "b"):
        w = l = t = 0
        for q, d in perq.items():
            if other not in d or "tavily" not in d:
                continue
            x, y = statistics.mean(d[other]), statistics.mean(d["tavily"])
            w, l, t = w + (x > y), l + (x < y), t + (x == y)
        res["je_frage"][f"{other}_vs_tavily"] = {"siege": w, "niederlagen": l, "gleich": t}
    (out / "auswertung.json").write_text(json.dumps(res, ensure_ascii=False, indent=1))
    print(json.dumps(res, ensure_ascii=False, indent=1))


# ---------- kontakte ----------

def cmd_kontakte(a):
    queries = json.loads((PRIV / "real-tasks" / "queries-offshore-kontakte.json").read_text())
    runs = []
    for i, q in enumerate(queries):
        for path, body, field, url_k in (
                ("/search", {"query": q, "max_results": 10, "include_answer": False}, "results", "url"),
                ("/serper/search", {"q": q, "num": 10}, "organic", "link")):
            time.sleep(a.pause)
            data, err, sec = post(a.base, path, body)
            res = [{"url": r[url_k], "title": r.get("title", ""),
                    "snippet": r.get("content") or r.get("snippet") or ""}
                   for r in (data or {}).get(field, [])]
            runs.append({"query": q, "endpoint": path, "latency_s": sec, "error": err, "results": res})
            print(path, sec, "s", len(res), err or "")
    Path(a.out).write_text(json.dumps(runs, ensure_ascii=False, indent=1))


# ---------- news ----------

NEWS = [
    ("Nobelpreis Wirtschaft 2026", "de"), ("Frankfurter Buchmesse 2026", "de"),
    ("Bundestag Haushalt 2027 Debatte", "de"), ("DAX aktuell Börse", "de"),
    ("Bundesliga Spieltag Ergebnisse", "de"), ("Bahnstreik GDL Oktober 2026", "de"),
    ("Unwetter Deutschland Warnung", "de"), ("Ukraine Krieg aktuelle Lage", "de"),
    ("Nobel Peace Prize 2026", "en"), ("Federal Reserve interest rate decision October 2026", "en"),
    ("OpenAI announcement this week", "en"), ("NVIDIA stock news", "en"),
    ("Premier League results this weekend", "en"), ("hurricane season 2026 latest", "en"),
    ("UK Budget 2026 Reeves", "en"),
]
_URL_DATE = re.compile(r"/(20\d\d)[/-](\d{1,2})[/-](\d{1,2})(?:/|-|$)")


def _parse_date(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")[:19]).date()
    except ValueError:
        pass
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(s))
    return date(int(m[1]), int(m[2]), int(m[3])) if m else None


def _url_date(u):
    m = _URL_DATE.search(u)
    try:
        return date(int(m[1]), int(m[2]), int(m[3])) if m else None
    except ValueError:
        return None


def _age_stats(ages, n_total):
    have = [x for x in ages if x is not None]
    return {"treffer": n_total, "mit_datum": len(have),
            "ohne_datum_anteil": round(1 - len(have) / n_total, 3) if n_total else None,
            "median_tage": statistics.median(have) if have else None,
            "max_tage": max(have) if have else None,
            "aelter_7_tage_anteil": round(sum(x > 7 for x in have) / len(have), 3) if have else None}


def cmd_news(a):
    today = date.fromisoformat(a.today)
    runs = []
    for q, lang in NEWS:
        for path, body, field, url_k in (
                ("/search", {"query": q, "topic": "news", "max_results": 10, "include_answer": False,
                             "language": lang}, "results", "url"),
                ("/serper/news", {"q": q, "num": 10, "hl": lang}, "news", "link")):
            time.sleep(a.pause)
            data, err, sec = post(a.base, path, body)
            res = []
            for r in (data or {}).get(field, []):
                d = _parse_date(r.get("published_date") or r.get("date"))
                ud = _url_date(r[url_k])
                res.append({"url": r[url_k], "title": r.get("title", ""), "datum": d and d.isoformat(),
                            "url_datum": ud and ud.isoformat(),
                            "alter": (today - d).days if d else None,
                            "alter_url": (today - ud).days if ud else None})
            runs.append({"query": q, "lang": lang, "endpoint": path, "latency_s": sec, "error": err,
                         "results": res})
            print(path, q, sec, "s", len(res), err or "")
    summary = {}
    for ep in ("/search", "/serper/news"):
        rs = [r for run in runs if run["endpoint"] == ep for r in run["results"]]
        summary[ep] = {"feld_datum": _age_stats([r["alter"] for r in rs], len(rs)),
                       "datum_oder_url": _age_stats([r["alter"] if r["alter"] is not None else r["alter_url"]
                                                     for r in rs], len(rs)),
                       "fehler": sum(1 for run in runs if run["endpoint"] == ep and run["error"]),
                       "leer": sum(1 for run in runs if run["endpoint"] == ep and not run["results"])}
    Path(a.out).write_text(json.dumps({"heute": a.today, "summary": summary, "runs": runs},
                                      ensure_ascii=False, indent=1))
    print(json.dumps(summary, indent=1))


# ---------- answers ----------

def cmd_answers(a):
    cache = json.loads((PRIV / "tavily_cache.json").read_text(encoding="utf-8"))
    queries = {json.loads(l)["query"]: json.loads(l) for l in open(HERE / "queries.jsonl", encoding="utf-8")
               if l.strip()}
    rows = []
    for i, (q, v) in enumerate((k, v) for k, v in cache.items() if not k.endswith("|noanswer") and v.get("answer")):
        if i:
            time.sleep(a.pause)
        meta = queries.get(q, {})
        data, err, sec = post(a.base, "/search", {"query": q, "max_results": 5, "include_answer": True,
                                                   "language": meta.get("lang")}, timeout=180)
        rows.append({"frage": q, "ground_truth": meta.get("ground_truth"),
                     "tavily": {"answer": v["answer"], "quellen": [r["url"] for r in v["results"][:5]]},
                     "neu": {"answer": (data or {}).get("answer"), "error": err, "sec": sec,
                             "quellen": [r["url"] for r in (data or {}).get("results", [])]}})
        print(i + 1, sec, "s", err or "", ((data or {}).get("answer") or "")[:80])
    Path(a.out).write_text(json.dumps(rows, ensure_ascii=False, indent=1))


# ---------- latency ----------

def _pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


def cmd_latency(a):
    qs = [json.loads(l)["query"] for l in open(HERE / a.queries, encoding="utf-8") if l.strip()]
    rng = random.Random(1014)
    rng.shuffle(qs)
    eps = [("/search", lambda q: {"query": q, "max_results": 5, "include_answer": False}),
           ("/serper/search", lambda q: {"q": q, "num": 10}),
           ("/exa/search", lambda q: {"query": q, "numResults": 10})]
    rows = []
    for i in range(a.n):
        path, body = eps[i % 3]
        q = qs[(i // 3) % len(qs)] if a.same else qs[i % len(qs)]
        if i:
            time.sleep(a.pause)
        data, err, sec = post(a.base, path, body(q), timeout=120)
        rows.append({"endpoint": path, "query": q, "sec": sec, "error": err})
        print(i + 1, path, sec, err or "")
    summ = {}
    for ep in [e for e, _ in eps] + ["alle"]:
        xs = [r["sec"] for r in rows if ep == "alle" or r["endpoint"] == ep]
        summ[ep] = {"n": len(xs), "median": statistics.median(xs), "p95": _pct(xs, 95), "p99": _pct(xs, 99),
                    "max": max(xs), "ueber_8s": sum(x > 8 for x in xs),
                    "fehler": sum(1 for r in rows if (ep == "alle" or r["endpoint"] == ep) and r["error"])}
    Path(a.out).write_text(json.dumps({"summary": summ, "rows": rows}, ensure_ascii=False, indent=1))
    print(json.dumps(summ, indent=1))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("blind", cmd_blind), ("blindeval", cmd_blindeval), ("kontakte", cmd_kontakte),
                     ("news", cmd_news), ("answers", cmd_answers), ("latency", cmd_latency)):
        p = sub.add_parser(name)
        p.add_argument("--base", default="http://127.0.0.1:8000")
        p.add_argument("--out", required=True)
        p.add_argument("--pause", type=float, default=2.0)
        p.set_defaults(fn=fn)
        if name == "blind":
            p.add_argument("--old", default=str(PRIV / "zuverl-2026-10-13" / "blind"))
        if name == "news":
            p.add_argument("--today", default=date.today().isoformat())
        if name == "latency":
            p.add_argument("--n", type=int, default=60)
            p.add_argument("--queries", default="queries_fresh.jsonl")
            p.add_argument("--same", action="store_true", help="jede Frage an alle drei Endpunkte")
    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
