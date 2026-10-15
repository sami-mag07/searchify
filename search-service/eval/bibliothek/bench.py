#!/usr/bin/env python3
"""Themen-Bibliothek messen: Testfragen aus pruefsatz.json live gegen eine Instanz.

Je Thema: Anteil der Domains aus den spaeteren Funden, die in den Top 10 bzw. Top 20
irgendeiner Testfrage auftauchen (site@k, wie search_eval in research-agent), Anteil
der genauen Seiten (page@20), MRR der ersten Fund-Domain je Frage. Getrennt fuer alle
Funde und nur fuer Domains, die bis 01.10. noch nicht in tasks.json standen ("neu").
Dazu Antwortzeit je Frage (median, p95) und Fehler.

  bench.py run --base http://127.0.0.1:8000 --label basis --out eval/private/bibliothek/basis.json
  bench.py show eval/private/bibliothek/basis.json [weitere.json]
"""
import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
SET = HERE / "pruefsatz.json"


def site(url):
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def norm(url):
    p = urlsplit(url)
    return (site(url) + p.path.rstrip("/") + ("?" + p.query if p.query else "")).lower()


def ask(base, q, n, timeout):
    body = json.dumps({"query": q, "max_results": n, "include_answer": False}).encode()
    req = urllib.request.Request(base + "/search", body, {"content-type": "application/json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            urls, err = [x["url"] for x in json.load(r).get("results", [])], None
    except Exception as e:  # noqa: BLE001
        urls, err = [], f"{type(e).__name__}: {str(e)[:120]}"
    return {"query": q, "urls": urls, "sec": round(time.perf_counter() - t0, 3), "error": err}


def run(a):
    ps = json.load(open(SET))
    runs = []
    for thema, t in ps["themen"].items():
        for q in t["fragen"]:
            r = ask(a.base, q, a.n, a.timeout)
            r["thema"] = thema
            runs.append(r)
            print(f"{thema:16} {r['sec']:6.2f}s {len(r['urls']):3} {r['error'] or ''} {q}", file=sys.stderr)
            time.sleep(a.pause)
    out = {"label": a.label, "base": a.base, "n": a.n, "zeit": time.strftime("%F %T"), "runs": runs}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    json.dump(out, open(a.out, "w"), ensure_ascii=False, indent=1)
    show_files([a.out])


def score(path):
    ps = json.load(open(SET))
    res = json.load(open(path))
    rows = {}
    for thema, t in ps["themen"].items():
        rs = [r for r in res["runs"] if r["thema"] == thema]
        for teil, funde in (("alle", t["funde"]), ("neu", [f for f in t["funde"] if not f["domain_schon_bekannt"]])):
            gs = {site(f["url"]) for f in funde}
            gp = {norm(f["url"]) for f in funde}
            if not gs:
                continue
            f10, f20, fp, rr = set(), set(), set(), []
            for r in rs:
                f10 |= {site(u) for u in r["urls"][:10]} & gs
                f20 |= {site(u) for u in r["urls"][:20]} & gs
                fp |= {norm(u) for u in r["urls"][:20]} & gp
                rank = next((i for i, u in enumerate(r["urls"], 1) if site(u) in gs), None)
                rr.append(1 / rank if rank else 0.0)
            rows.setdefault(teil, []).append({"thema": thema, "domains": len(gs), "site@10": len(f10) / len(gs),
                                              "site@20": len(f20) / len(gs), "page@20": len(fp) / len(gp),
                                              "mrr": sum(rr) / len(rr)})
    secs = sorted(r["sec"] for r in res["runs"])
    zeit = {"median_s": statistics.median(secs), "p95_s": secs[min(len(secs) - 1, int(0.95 * len(secs)))],
            "fehler": sum(bool(r["error"]) for r in res["runs"]), "fragen": len(secs)}
    mittel = {teil: {k: round(sum(r[k] for r in rs) / len(rs), 3) for k in ("site@10", "site@20", "page@20", "mrr")}
              for teil, rs in rows.items()}
    return {"label": res["label"], "themen": rows, "mittel": mittel, "zeit": zeit}


def show_files(paths):
    for p in paths:
        s = score(p)
        print(f"\n== {s['label']} ({p})  median {s['zeit']['median_s']:.2f} s  p95 {s['zeit']['p95_s']:.2f} s  "
              f"Fehler {s['zeit']['fehler']}/{s['zeit']['fragen']}")
        for teil, m in s["mittel"].items():
            print(f"   {teil:5} " + "  ".join(f"{k} {v:.3f}" for k, v in m.items()))
        for r in s["themen"]["alle"]:
            print(f"   {r['thema']:16} Domains {r['domains']:3}  site@20 {r['site@20']:.2f}  mrr {r['mrr']:.2f}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--base", default="http://127.0.0.1:8000")
    r.add_argument("--label", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--n", type=int, default=20)
    r.add_argument("--pause", type=float, default=2.0)
    r.add_argument("--timeout", type=float, default=60)
    s = sub.add_parser("show")
    s.add_argument("files", nargs="+")
    a = ap.parse_args()
    run(a) if a.cmd == "run" else show_files(a.files)


if __name__ == "__main__":
    main()
