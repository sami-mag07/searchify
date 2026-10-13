"""Baut die Grafiken der README (docs/img/*.svg) aus den Messdateien in eval/private/.

Gelb = Original-Anbieter, Rot = Nachbau alt (11.10.2026), Gruen = Nachbau neu (13.10.2026).
Aufruf: .venv/bin/python eval/readme_charts.py
"""
import json
from collections import defaultdict
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRIV = ROOT / "eval" / "private"
OUT = ROOT / "docs" / "img"

COL = {"orig": "#E0A100", "alt": "#D64545", "neu": "#2E9E5B"}
NAME = {"orig": "Original", "alt": "Nachbau alt (11.10.)", "neu": "Nachbau neu (13.10.)"}
INK, MUTED, GRID, BG = "#1F2328", "#59636E", "#E6E8EB", "#FFFFFF"
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
W = 760


def load(name):
    return json.loads((PRIV / name).read_text())


def de(v, d=2):
    return f"{v:.{d}f}".replace(".", ",")


def text(x, y, s, size=13, color=INK, anchor="start", weight=400):
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{color}" '
            f'text-anchor="{anchor}" font-weight="{weight}">{escape(str(s))}</text>')


def legend(keys, y):
    parts, x = [], 24
    for k in keys:
        parts.append(f'<circle cx="{x + 6}" cy="{y - 4}" r="6" fill="{COL[k]}"/>')
        parts.append(text(x + 18, y, NAME[k], 12, MUTED))
        x += 34 + 6.3 * len(NAME[k])
    return parts


def frame(title, sub, body, h):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{h}" viewBox="0 0 {W} {h}" '
            f'font-family="{FONT}">'
            f'<rect width="{W}" height="{h}" rx="12" fill="{BG}" stroke="{GRID}"/>'
            + text(24, 34, title, 17, INK, weight=650) + text(24, 56, sub, 12.5, MUTED)
            + "".join(body) + "</svg>")


def dots(title, sub, rows, lo, hi, ticks, fmt, note=None, label_w=200):
    """Punktdiagramm: jede Zeile hat viele Einzelwerte (Punkte) und einen Schnitt (Strich)."""
    top, row_h = 96, 46
    x0, x1 = 24 + label_w, W - 110
    sx = lambda v: x0 + (v - lo) / (hi - lo) * (x1 - x0)
    h = top + row_h * len(rows) + 40 + (22 if note else 0)
    b = legend(sorted({r["c"] for r in rows}, key=list(COL).index), 80)
    b.append(text(W - 24, 80, "größer ist besser", 12, MUTED, "end"))
    for t in ticks:
        b.append(f'<line x1="{sx(t):.1f}" x2="{sx(t):.1f}" y1="{top - 6}" y2="{top + row_h * len(rows)}" stroke="{GRID}"/>')
        b.append(text(sx(t), top + row_h * len(rows) + 16, fmt(t), 11, MUTED, "middle"))
    for i, r in enumerate(rows):
        cy = top + row_h * i + row_h / 2
        if r.get("gap"):
            b.append(f'<line x1="24" x2="{W - 24}" y1="{top + row_h * i}" y2="{top + row_h * i}" stroke="{GRID}"/>')
        b.append(text(24, cy + 4, r["l"], 13))
        seen = defaultdict(int)
        for v in r["v"]:
            k = round(v, 2)
            off = (seen[k] % 5 - 2) * 4.2
            seen[k] += 1
            b.append(f'<circle cx="{sx(v):.1f}" cy="{cy + off:.1f}" r="5" fill="{COL[r["c"]]}" fill-opacity="0.45"/>')
        m = sum(r["v"]) / len(r["v"])
        b.append(f'<rect x="{sx(m) - 2:.1f}" y="{cy - 15}" width="4" height="30" rx="2" fill="{INK}"/>')
        b.append(text(W - 24, cy + 5, "Ø " + fmt(m), 13, INK, "end", 650))
    if note:
        b.append(text(24, h - 14, note, 11.5, MUTED))
    return frame(title, sub, b, h)


def bars(title, sub, groups, fmt, keys, note=None, label_w=200, better="kleiner ist besser"):
    """Balken je Gruppe, ein Balken pro Stand (Original / alt / neu)."""
    top, bar_h, gap = 96, 18, 16
    x0, x1 = 24 + label_w, W - 90
    hi = max(v for g in groups for k, v in g["v"].items() if v is not None) * 1.05
    rows_h = sum(len([k for k in keys if g["v"].get(k) is not None]) * (bar_h + 4) + gap for g in groups)
    h = top + rows_h + 14 + (22 if note else 0)
    b = legend(keys, 80)
    b.append(text(W - 24, 80, better, 12, MUTED, "end"))
    y = top
    for g in groups:
        ks = [k for k in keys if g["v"].get(k) is not None]
        b.append(text(24, y + len(ks) * (bar_h + 4) / 2 + 3, g["l"], 13))
        for k in ks:
            v = g["v"][k]
            wv = max(2, (v / hi) * (x1 - x0))
            b.append(f'<rect x="{x0}" y="{y}" width="{wv:.1f}" height="{bar_h}" rx="4" fill="{COL[k]}"/>')
            b.append(text(x0 + wv + 8, y + 13.5, fmt(v), 12.5, INK, weight=600))
            y += bar_h + 4
        y += gap
    if note:
        b.append(text(24, h - 14, note, 11.5, MUTED))
    return frame(title, sub, b, h)


def blind_rows():
    def per_question(folder, names):
        key, urteile = load(f"{folder}/key.json"), load(f"{folder}/urteile.json")
        acc = defaultdict(lambda: defaultdict(list))
        for u in urteile:
            q = u["id"].rsplit("-", 1)[0]
            acc[key[u["id"]]][q].append(u["vertrauen"])
        return {names[s]: [sum(v) / len(v) for v in qs.values()] for s, qs in acc.items() if s in names}

    old = per_question("blind", {"eigen": "alt"})
    new = per_question("zuverl-2026-10-13/blind", {"tavily": "orig", "b": "neu"})
    return [
        {"l": "Tavily (Original)", "c": "orig", "v": new["orig"]},
        {"l": "Nachbau alt", "c": "alt", "v": old["alt"]},
        {"l": "Nachbau neu", "c": "neu", "v": new["neu"]},
    ]


def research_rows():
    def per_task(fname):
        d = load(fname)["queries"]["engines"]
        return {e["engine"]: [t["mrr"] for t in e["per_task"]] for e in d}

    old, new = per_task("history-score-2026-10-11_1706.json"), per_task("history-score-2026-10-13_1040.json")
    return [
        {"l": "Tavily (Original)", "c": "orig", "v": new["tavily"]},
        {"l": "Tavily-Nachbau alt", "c": "alt", "v": old["own-tavily"]},
        {"l": "Tavily-Nachbau neu", "c": "neu", "v": new["own-tavily"]},
        {"l": "Serper (Original)", "c": "orig", "v": new["serper"], "gap": True},
        {"l": "Serper-Nachbau alt", "c": "alt", "v": old["own-serper"]},
        {"l": "Serper-Nachbau neu", "c": "neu", "v": new["own-serper"]},
        {"l": "Exa-Nachbau alt", "c": "alt", "v": old["own-exa"], "gap": True},
        {"l": "Exa-Nachbau neu", "c": "neu", "v": new["own-exa"]},
    ]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    charts = {}

    charts["blindtest"] = dots(
        "Wie vertrauenswürdig sind die Quellen?",
        "Blindtest, 30 Faktenfragen. Jeder Punkt = eine Frage (Schnitt der Top 3), Strich = Durchschnitt.",
        blind_rows(), 1, 5, [1, 2, 3, 4, 5], lambda v: de(v, 2) if v % 1 else str(int(v)),
        note="Bewertet von einem Agenten, der nicht wusste, welche Quelle von wem kommt. 1 = unseriös, 5 = sehr seriös.")

    charts["recherche"] = dots(
        "Wie weit oben stehen die guten Quellen bei echten Recherchen?",
        "9 Recherche-Aufgaben aus der Historie. Jeder Punkt = eine Aufgabe (MRR), Strich = Durchschnitt.",
        research_rows(), 0, 1, [0, 0.25, 0.5, 0.75, 1], lambda v: de(v, 2),
        note="MRR 1 = die erste gute Quelle steht auf Platz 1. Exa-Original ist ohne gültigen Key nicht messbar.")

    charts["themenfremd"] = bars(
        "Themenfremde Treffer bei einer echten Aufgabe",
        "8 Suchen nach Offshore-Kontakten. Gezählt: Shopping-Sender HSE, Wörterbücher, Banken mit ähnlichem Namen.",
        [{"l": "Tavily-Nachbau", "v": {"alt": 18, "neu": 1}},
         {"l": "Serper-Nachbau", "v": {"alt": 17, "neu": 2}}],
        lambda v: str(int(v)), ["alt", "neu"],
        note="Tavily-Nachbau: 18 von 77 Treffern vorher, 1 von 61 nachher. Serper-Nachbau: 17 von 80, dann 2 von 80.")

    charts["seo"] = bars(
        "SEO- und Werbeseiten unter den Top-Treffern",
        "Blindtest, 30 Fragen mal 3 Treffer = 90 Treffer je System.",
        [{"l": "Tavily-Suche", "v": {"orig": 15, "alt": 36, "neu": 23}}],
        lambda v: f"{int(v)} von 90", ["orig", "alt", "neu"])

    charts["tempo"] = bars(
        "Typische Antwortzeit",
        "Median in Sekunden. Original-Werte aus gespeicherten Antworten, Nachbau live auf dem Mac.",
        [{"l": "Tavily-Suche", "v": {"orig": 1.33, "alt": 1.11, "neu": 0.84}},
         {"l": "Serper", "v": {"orig": 1.6, "alt": 0.97, "neu": 0.79}},
         {"l": "Exa", "v": {"alt": 2.25, "neu": 1.69}},
         {"l": "Jina-Reader", "v": {"orig": 3.79, "alt": 1.0, "neu": 0.45}}],
        lambda v: de(v, 2) + " s", ["orig", "alt", "neu"],
        note="Exa-Original: kein gültiger Key, darum ohne Vergleich.")

    charts["zuverlaessigkeit"] = bars(
        "Fehler und Ausfälle",
        "Anteil der Anfragen, die mit Fehler oder ohne Treffer enden.",
        [{"l": "Normalbetrieb", "v": {"alt": 3 / 160 * 100, "neu": 0.0}},
         {"l": "Suchmaschine fällt aus", "v": {"alt": 25 / 49 * 100, "neu": 13 / 49 * 100}}],
        lambda v: de(v, 1) + " %", ["alt", "neu"],
        note="Normalbetrieb: 3 von 160 Anfragen vorher, 0 von 194 nachher. Ausfall: 25 von 49, dann 13 von 49.")

    charts["noten"] = bars(
        "Gesamtnote je Tool (Prüffragen)",
        "Eigene Prüffragen, die beim Bauen nie benutzt wurden. 1 = perfekt.",
        [{"l": "Tavily-Suche", "v": {"alt": 0.835, "neu": 0.865}},
         {"l": "Serper", "v": {"alt": 0.639, "neu": 0.705}},
         {"l": "Exa", "v": {"alt": 0.951, "neu": 0.951}},
         {"l": "Jina-Reader", "v": {"alt": 0.870, "neu": 0.885}}],
        lambda v: de(v, 3), ["alt", "neu"], better="größer ist besser")

    for name, svg in charts.items():
        (OUT / f"{name}.svg").write_text(svg)
        print("geschrieben:", OUT / f"{name}.svg")


if __name__ == "__main__":
    main()
