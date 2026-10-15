#!/usr/bin/env python3
"""Baut die Datensaetze fuer das Benchmark-Dashboard aus den Messdateien in
eval/private/ (keine abgetippten Zahlen). Ausgabe: eval/private/dashboard/*.json,
jeweils eine Liste von Zeilen."""

from __future__ import annotations

import collections
import json
import statistics
from pathlib import Path

HERE = Path(__file__).resolve().parent
P = HERE / "private"
OUT = P / "dashboard"


def load(name):
    return json.loads((P / name).read_text(encoding="utf-8"))


def write(name, rows):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{name}: {len(rows)} Zeilen")


def r3(x):
    return None if x is None else round(float(x), 3)


# 1) Loop-Verlauf: eigene Latenz je Runde (Haupt-Set, 30 bzw. anfangs 20 Fragen)
LOOP = [
    ("r0-baseline", 1, "Ausgangslage: alles nacheinander"),
    ("r1", 2, "Paralleles Laden, Reranker auf GPU"),
    ("r2", 3, "Ranken überlappt das Laden"),
    ("r4b", 4, "Zweitanfrage, 300-ms-Budget"),
    ("r6-mini", 5, "Kleineres Rerank-Modell"),
    ("s2", 6, "Kostenloses SearXNG statt Serper"),
    ("s4-500", 7, "Extraktion in Prozessen"),
    ("s7-resi", 8, "Schnelle Extraktion (resiliparse)"),
    ("L1b-2", 9, "Bing zuerst (Engines getrennt)"),
    ("L2-main", 10, "Sprach- und Quellen-Ranking"),
]
rows = []
for tag, step, label in LOOP:
    e = load(f"bench-{tag}.json")["summary"]["E"]
    rows.append({"step": step, "runde": label, "tag": tag, "p50_s": e["p50_s"], "p95_s": e["p95_s"],
                 "hit1": r3(e["hit@1"]), "content_p50": e.get("content_chars_p50")})
write("loop", rows)

# 2) Fragensets: aktueller Stand
SETS = [
    ("Faktenfragen (30, mit Tavily)", "bench-L2-main.json", True),
    ("Geprüftes Set (50)", "bench-L2-big.json", False),
    ("Long-Tail (15)", "bench-D-hard.json", False),
    ("Aktuelle Fakten 2026 (10)", "bench-D-fresh.json", False),
    ("Spanisch/Französisch (20)", "bench-L2-multi.json", False),
]
rows = []
for name, f, with_t in SETS:
    s = load(f)["summary"]
    for side, sysname in (("E", "Eigen"), ("T", "Tavily")):
        if side == "T" and not with_t:
            continue
        x = s[side]
        rows.append({"set": name, "system": sysname, "hit1": r3(x["hit@1"]), "hit3": r3(x["hit@3"]),
                     "mrr": r3(x["mrr"]), "p50_s": x["p50_s"], "p95_s": x["p95_s"],
                     "content_p50": x.get("content_chars_p50")})
write("sets", rows)

# 3) Latenz je Frage, Haupt-Set: Tavily (Cache, ohne answer) gegen eigen
main = load("bench-L2-main.json")["own"]
tcache = load("tavily_cache.json")
qs = [json.loads(l) for l in open(HERE / "queries.jsonl", encoding="utf-8") if l.strip()]
rows = []
for q in qs:
    rows.append({"id": q["id"], "system": "Tavily", "latency_s": tcache[q["query"] + "|noanswer"]["latency_s"]})
    rows.append({"id": q["id"], "system": "Eigen", "latency_s": main[q["query"]]["latency_s"]})
write("latency_main", rows)

# 4) Recherche-Historie (Kassetten des research-agent)
hist = load("history-score-2026-10-11_1706.json")
NAMES = {"tavily": "Tavily (Original)", "serper": "Serper (Original)", "own-tavily": "Tavily-Nachbau",
         "own-serper": "Serper-Nachbau", "own-exa": "Exa-Nachbau"}
rows = []
for field, label in (("queries", "Recherche-Fragen"), ("queries_niche", "Nischen-Fragen")):
    for e in hist[field]["engines"]:
        if e["engine"] not in NAMES:
            continue
        broken = e["errors"] > e["runs"] / 2
        rows.append({"feld": label, "engine": NAMES[e["engine"]], "original": not e["engine"].startswith("own"),
                     "site10": r3(e["site@10"]), "site30": r3(e["site@30"]), "page30": r3(e["page@30"]),
                     "mrr": r3(e["mrr"]), "sec": e["sec_median"], "fehler": e["errors"], "runs": e["runs"],
                     "unbrauchbar": broken})
write("history", rows)

# 5) Reader gegen Original-Jina
rs = load("reader-score-2026-10-11-kern.json")["summary"]
CAT = {"all": "Alle (40)", "docs": "Doku", "gold": "Programmseiten", "wiki": "Wikipedia", "news": "News"}
SYS = {"orig": "Jina (Original)", "jina": "Jina-Nachbau", "extract": "Extract-Nachbau"}
rows = []
for cat, label in CAT.items():
    for key, sysname in SYS.items():
        x = rs[cat][key]
        rows.append({"kategorie": label, "system": sysname, "ok_rate": r3(x["ok_rate"]),
                     "latency_median": x["latency_median"], "latency_p95": x["latency_p95"],
                     "recall": r3(x["recall"]), "precision": r3(x["precision"]),
                     "title_equal": r3(x["title_equal"]), "links_median": x["links_median"],
                     "action_hit": r3(x["action_hit"])})
write("reader", rows)

# 6) Lasttest
ld = load("load-2026-10-11.json")
write("load", [{k: v for k, v in s.items() if k != "latencies"} for s in ld["stages"]])

# 7) Endpunkte
write("endpoints", load("endpoints-2026-10-11.json"))

# 8) Blindtest (13.10.: Tavily gegen Nachbau mit und ohne Qualitaetsfilter, ein Richter)
key = load("zuverl-2026-10-13/blind/key.json")
urteile = load("zuverl-2026-10-13/blind/urteile.json")
by = collections.defaultdict(list)
for u in urteile:
    by[key[u["id"]]].append(u)
SIDE = {"tavily": "Tavily", "b": "Eigen", "a": "Eigen ohne Filter"}
summary, types, trust = [], [], []
for side, sysname in SIDE.items():
    xs = by[side]
    n = len(xs)
    vals = [x["vertrauen"] for x in xs]
    ans = collections.Counter(x["beantwortet"] for x in xs)
    summary.append({"system": sysname, "n": n, "vertrauen_mittel": round(statistics.mean(vals), 2),
                    "anteil_vertrauenswuerdig": r3(sum(v >= 4 for v in vals) / n),
                    "anteil_schwach": r3(sum(v <= 2 for v in vals) / n),
                    "beantwortet_ja": r3(ans["ja"] / n), "beantwortet_teilweise": r3(ans["teilweise"] / n),
                    "beantwortet_nein": r3(ans["nein"] / n)})
    for t, c in collections.Counter(x["typ"] for x in xs).items():
        types.append({"system": sysname, "typ": t, "anzahl": c})
    for level in range(1, 6):
        trust.append({"system": sysname, "stufe": level, "anzahl": sum(v == level for v in vals)})
perq = collections.defaultdict(lambda: collections.defaultdict(list))
for u in urteile:
    perq[u["id"].rsplit("-", 1)[0]][key[u["id"]]].append(u["vertrauen"])
wins = collections.Counter()
for d in perq.values():
    a, b = statistics.mean(d["tavily"]), statistics.mean(d["b"])
    wins["Tavily besser" if a > b else "Eigen besser" if b > a else "Gleich"] += 1
write("blind_summary", summary)
write("blind_types", types)
write("blind_trust", trust)
write("blind_wins", [{"ergebnis": k, "fragen": wins[k]} for k in ("Tavily besser", "Gleich", "Eigen besser")])

# 8b) Zielmasse je Tool: Stand vor der Optimierung gegen heute (Replay, eval/private/stand-2026-10-13)
base_tools = load("tool-baseline-2026-10-12.json")
base_kern = load("objective-baseline-1.json")
TOOLS = [("kern", "Tavily-Suche"), ("serper", "Serper"), ("exa", "Exa"), ("reader", "Jina-Reader")]
tools = []
for t, name in TOOLS:
    now = load(f"stand-2026-10-13/{t}.json")
    before = base_kern if t == "kern" else base_tools[t]
    for split, teil in (("dev", "Übungsfragen"), ("test", "Prüffragen")):
        tools.append({"tool": name, "teil": teil, "vorher": r3(before[split]["score"]),
                      "nachher": r3(now[split]["score"])})
write("tools", tools)

# 8c) Zuverlaessigkeit: Serverfehler und langsamste 5 % je Endpunkt (eval/reliability_bench.py)
rel_before = load("zuverl-2026-10-13/robust/eval/private/rel/vorher.json")["summary"]
rel_now = load("stand-2026-10-13/zuverlaessigkeit.json")["summary"]
EP = {"search": "Tavily-Suche", "serper": "Serper", "exa": "Exa", "jina": "Jina-Reader", "extract": "Extract"}
write("reliability", [{"endpunkt": EP[k], "anfragen": rel_now[k]["n"],
                       "fehler_vorher": rel_before[k]["http5xx"], "fehler_nachher": rel_now[k]["http5xx"],
                       "p95_vorher": r3(rel_before[k]["p95"]), "p95_nachher": r3(rel_now[k]["p95"])}
                      for k in EP])

# 9) Ablationen (A/B auf demselben Stand, Quelle je Zeile)
def e(tag):
    return load(f"bench-{tag}.json")["summary"]["E"]


def h(stamp, engine="own-tavily", field="queries"):
    for x in load(f"history-score-2026-10-11_{stamp}.json")[field]["engines"]:
        if x["engine"] == engine:
            return x


ABL = [
    ("Sprach-Abzug", "Treffer an Platz 1, ES/FR", e("L0-multi")["hit@1"], e("L2-multi")["hit@1"], "Anteil", "bench-L0/L2-multi"),
    ("Sprach-Abzug", "Treffer an Platz 1, Faktenfragen", e("L0-main")["hit@1"], e("L2-main")["hit@1"], "Anteil", "bench-L0/L2-main"),
    ("Kürzere Wartezeit (100 statt 250 ms)", "Inhalt je Treffer (Zeichen)", e("L1b-2")["content_chars_p50"], e("L4-100")["content_chars_p50"], "Zeichen", "bench-L1b-2/L4-100"),
    ("Kürzere Wartezeit (100 statt 250 ms)", "Latenz Median (s)", e("L1b-2")["p50_s"], e("L4-100")["p50_s"], "s", "bench-L1b-2/L4-100"),
    ("15 statt 10 Kandidaten", "Latenz Median (s)", e("s4-500")["p50_s"], e("s5-c15")["p50_s"], "s", "bench-s4-500/s5-c15"),
    ("Engines getrennt (Bing zuerst)", "Latenz Median (s)", e("s7-resi")["p50_s"], e("L1b-2")["p50_s"], "s", "bench-s7-resi/L1b-2"),
    ("Prozess-Extraktion", "Inhalt je Treffer (Zeichen)", e("s3-500")["content_chars_p50"], e("s4-500")["content_chars_p50"], "Zeichen", "bench-s3-500/s4-500"),
    ("Yandex als 3. Engine (de/en)", "Recherche mrr", h("1628")["mrr"] if (P / "history-score-2026-10-11_1628.json").exists() else None, h("1631")["mrr"], "mrr", "history 1628/1631"),
    ("Domain-Vielfalt", "Recherche mrr", h("1628")["mrr"] if (P / "history-score-2026-10-11_1628.json").exists() else None, h("1634")["mrr"], "mrr", "history 1628/1634"),
]
rows = [{"experiment": a, "messgroesse": m, "vorher": r3(v0), "nachher": r3(v1), "einheit": u, "quelle": src}
        for a, m, v0, v1, u, src in ABL if v0 is not None and v1 is not None]
write("ablations", rows)
