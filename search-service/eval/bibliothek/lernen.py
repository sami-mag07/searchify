#!/usr/bin/env python3
"""Schritt 5: kleines Sortier-Modell aus Samis uebernommenen Links (Auftrag 14.10.2026).

Training (nur Funde bis 01.10.): Trefferlisten aus den Kassetten des research-agent
(Originale 01.10. und eigener Dienst 13./14.10.) zu den Suchanfragen in tasks.json,
Label = Domain steht in den Gold-Links der Aufgabe (= von Sami in den Tracker
uebernommen). Merkmale nur aus URL und Platz (die Pruefsatz-Laeufe speichern keine
Titel): Platz, Anmeldewort, Startseite, Pfadtiefe, Jahreszahl, Endung, Anfrage-Woerter
in der URL, SEO-Merkmale aus app/quality.py. Kein Bibliotheks-Merkmal (wuerde im
Training die Antwort verraten).

Test: gespeicherte Pruefsatz-Laeufe (eval/private/bibliothek/*.json) werden mit dem
Modell neu sortiert, verglichen mit der Original-Reihenfolge (site@10, MRR; site@20
bleibt gleich, weil nur innerhalb der Top 20 umsortiert wird).

  lernen.py --runs aus2-1 aus2-2 bib2-1 bib2-2
"""
import argparse
import glob
import json
import math
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))
from app import quality as quality_mod  # noqa: E402
import bench  # noqa: E402

EVALS = Path.home() / ".claude/skills/research-agent/evals"
ACTION = re.compile(r"(apply|bewerb|anmeld|regist|signup|sign-up|teilnahm|ausschreib|call-for|application|tickets?\b|join)")
FEATURES = ["inv_rank", "action", "root", "depth", "year", "tld_org", "tld_de", "tld_eu_int", "tld_com",
            "query_in_url", *quality_mod.WEIGHTS.keys()]


def feats(url, rank, query):
    low = unquote(url).lower()
    p = urlsplit(low)
    host = (p.hostname or "").removeprefix("www.")
    segs = [s for s in p.path.split("/") if s]
    qt = {w for w in re.findall(r"\w+", query.lower()) if len(w) >= 5}
    f = {"inv_rank": 1 / rank, "action": int(bool(ACTION.search(low))), "root": int(not segs),
         "depth": min(len(segs), 5) / 5, "year": int(bool(re.search(r"202[6-8]", low))),
         "tld_org": int(host.endswith(".org")), "tld_de": int(host.endswith(".de")),
         "tld_eu_int": int(host.endswith((".eu", ".int"))), "tld_com": int(host.endswith(".com")),
         "query_in_url": min(sum(w[:6] in low for w in qt), 3) / 3}
    f.update(quality_mod.features(url, "", query))
    return [f[k] for k in FEATURES]


def training_rows():
    tasks = {t["id"]: t for t in json.load(open(EVALS / "tasks.json"))["tasks"]}
    files = sorted(glob.glob(str(EVALS / "cassettes" / "2026-10-01_*.json")))
    files += sorted(glob.glob(str(ROOT / "eval/private/history-2026-10-1[34]_*-queries*.json")))
    files = [f for f in files if "merged" not in f]
    X, y, groups = [], [], []
    for path in files:
        for r in json.load(open(path)).get("runs", []):
            t = tasks.get(r.get("task"))
            if not t or r.get("error") or not r.get("hits"):
                continue
            gold = {bench.site(u) for u in t["gold"]}
            for i, h in enumerate(r["hits"][:20], 1):
                X.append(feats(h["url"], i, r["query"]))
                y.append(int(bench.site(h["url"]) in gold))
                groups.append(r["task"])
    return X, y, groups


def fit(X, y, C):
    from sklearn.linear_model import LogisticRegression
    return LogisticRegression(C=C, max_iter=2000, class_weight="balanced").fit(X, y)


def cv_task(X, y, groups, C):
    """Kreuzvalidierung nach Aufgabe (Regal): MRR der ersten Gold-Domain je Liste."""
    import numpy as np
    X, y, g = np.array(X), np.array(y), np.array(groups)
    gain = []
    for task in sorted(set(groups)):
        tr, te = g != task, g == task
        if y[tr].sum() == 0:
            continue
        m = fit(X[tr], y[tr], C)
        s = m.decision_function(X[te])
        # Listen innerhalb der Aufgabe am inv_rank==1 trennen
        idx = np.where(te)[0]
        lists, cur = [], []
        for j, i in enumerate(idx):
            if X[i][0] == 1.0 and cur:
                lists.append(cur)
                cur = []
            cur.append((i, s[j]))
        lists.append(cur)
        for lst in lists:
            orig = next((k for k, (i, _) in enumerate(lst, 1) if y[i]), None)
            new = sorted(lst, key=lambda t: -t[1])
            mod = next((k for k, (i, _) in enumerate(new, 1) if y[i]), None)
            gain.append(((1 / mod) if mod else 0) - ((1 / orig) if orig else 0))
    return sum(gain) / len(gain)


def test_runs(model, names):
    out = {}
    for name in names:
        path = ROOT / "eval/private/bibliothek" / f"{name}.json"
        res = json.load(open(path))
        before = bench.score(path)["mittel"]["alle"]
        for r in res["runs"]:
            urls = r["urls"][:20]
            if urls:
                s = model.decision_function([feats(u, i, r["query"]) for i, u in enumerate(urls, 1)])
                r["urls"] = [u for _, u in sorted(zip(s, urls), key=lambda t: -t[0])]
        tmp = path.with_name(f"{name}-lernen.json")
        json.dump(res, open(tmp, "w"))
        after = bench.score(tmp)["mittel"]["alle"]
        out[name] = {"vorher": before, "nachher": after}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="+", required=True)
    a = ap.parse_args()
    X, y, groups = training_rows()
    print(f"Training: {len(y)} Treffer, {sum(y)} Gold-Domains, {len(set(groups))} Aufgaben")
    best = max(((cv_task(X, y, groups, C), C) for C in (0.01, 0.1, 1.0)), key=lambda t: t[0])
    print(f"Kreuzvalidierung nach Aufgabe: MRR-Gewinn {best[0]:+.3f} bei C={best[1]}")
    model = fit(X, y, best[1])
    print("Gewichte:", ", ".join(f"{k} {w:+.2f}" for k, w in zip(FEATURES, model.coef_[0]) if abs(w) >= 0.1))
    for name, v in test_runs(model, a.runs).items():
        print(f"{name:10} site@10 {v['vorher']['site@10']:.3f} -> {v['nachher']['site@10']:.3f}   "
              f"mrr {v['vorher']['mrr']:.3f} -> {v['nachher']['mrr']:.3f}")


if __name__ == "__main__":
    main()
