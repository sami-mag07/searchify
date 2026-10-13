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
NAME = {"orig": "Original-Anbieter", "alt": "Unser Nachbau vorher (11.10.)", "neu": "Unser Nachbau jetzt (13.10.)"}
INK, MUTED, GRID, BG, GOOD = "#1F2328", "#59636E", "#E6E8EB", "#FFFFFF", "#1A7F45"
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
W = 760
TOP = 128  # Beginn der Zeichenflaeche unter Titel, Ergebnis, Lesehilfe und Legende


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
        parts.append(text(x + 18, y, NAME[k], 12, INK))
        x += 36 + 6.2 * len(NAME[k])
    return parts


def frame(title, result, howto, body, h):
    """Kopf jeder Grafik: Frage, Ergebnis in einem Satz, Lesehilfe."""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{h}" viewBox="0 0 {W} {h}" '
            f'font-family="{FONT}">'
            f'<rect width="{W}" height="{h}" rx="12" fill="{BG}" stroke="{GRID}"/>'
            + text(24, 34, title, 17, INK, weight=650)
            + text(24, 58, "Ergebnis: " + result, 13.5, GOOD, weight=650)
            + text(24, 80, howto, 12, MUTED)
            + "".join(body) + "</svg>")


def dots(title, result, howto, rows, lo, hi, ticks, fmt, ends, note=None, label_w=200):
    """Punktdiagramm: jede Zeile hat viele Einzelwerte (Punkte) und einen Schnitt (Strich mit Zahl)."""
    row_h = 46
    x0, x1 = 24 + label_w, W - 120
    sx = lambda v: x0 + (v - lo) / (hi - lo) * (x1 - x0)
    bottom = TOP + row_h * len(rows)
    h = bottom + 58 + (22 if note else 0)
    b = legend(sorted({r["c"] for r in rows}, key=list(COL).index), 108)
    b.append(text(W - 24, TOP - 4, "Durchschnitt", 11.5, MUTED, "end"))
    for t in ticks:
        b.append(f'<line x1="{sx(t):.1f}" x2="{sx(t):.1f}" y1="{TOP}" y2="{bottom}" stroke="{GRID}"/>')
        b.append(text(sx(t), bottom + 16, fmt(t), 11, MUTED, "middle"))
    b.append(text(x0, bottom + 34, ends[0], 11.5, MUTED, "start"))
    b.append(text(x1, bottom + 34, ends[1] + "  →  besser", 11.5, INK, "end", 600))
    for i, r in enumerate(rows):
        cy = TOP + row_h * i + row_h / 2
        if r.get("gap"):
            b.append(f'<line x1="24" x2="{W - 24}" y1="{TOP + row_h * i}" y2="{TOP + row_h * i}" stroke="{GRID}"/>')
        b.append(text(24, cy + 4, r["l"], 13))
        seen = defaultdict(int)
        for v in r["v"]:
            k = round(v, 2)
            off = (seen[k] % 5 - 2) * 4.2
            seen[k] += 1
            b.append(f'<circle cx="{sx(v):.1f}" cy="{cy + off:.1f}" r="5" fill="{COL[r["c"]]}" fill-opacity="0.45"/>')
        m = sum(r["v"]) / len(r["v"])
        b.append(f'<rect x="{sx(m) - 2:.1f}" y="{cy - 15}" width="4" height="30" rx="2" fill="{INK}"/>')
        b.append(text(W - 24, cy + 5, fmt(m), 15, INK, "end", 700))
    if note:
        b.append(text(24, h - 14, note, 11.5, MUTED))
    return frame(title, result, howto, b, h)


def bars(title, result, howto, groups, keys, note=None, label_w=200):
    """Balken je Gruppe, ein Balken pro Stand. Werte als (zahl, beschriftung)."""
    bar_h, gap = 20, 16
    x0, x1 = 24 + label_w, W - 150
    hi = max(v[0] for g in groups for v in g["v"].values() if v is not None) or 1
    rows_h = sum(len([k for k in keys if g["v"].get(k) is not None]) * (bar_h + 4) + gap for g in groups)
    h = TOP + rows_h + 6 + (22 if note else 0)
    b = legend(keys, 108)
    y = TOP
    for g in groups:
        ks = [k for k in keys if g["v"].get(k) is not None]
        b.append(text(24, y + len(ks) * (bar_h + 4) / 2 + 3, g["l"], 13.5, INK, weight=600))
        for k in ks:
            v, lab = g["v"][k]
            wv = max(3, (v / hi) * (x1 - x0))
            b.append(f'<rect x="{x0}" y="{y}" width="{wv:.1f}" height="{bar_h}" rx="4" fill="{COL[k]}"/>')
            b.append(text(x0 + wv + 8, y + 15, lab, 13, INK, weight=650))
            y += bar_h + 4
        y += gap
    if note:
        b.append(text(24, h - 14, note, 11.5, MUTED))
    return frame(title, result, howto, b, h)


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
        {"l": "Nachbau vorher", "c": "alt", "v": old["alt"]},
        {"l": "Nachbau jetzt", "c": "neu", "v": new["neu"]},
    ]


def research_rows():
    def per_task(fname):
        d = load(fname)["queries"]["engines"]
        return {e["engine"]: [t["mrr"] for t in e["per_task"]] for e in d}

    old, new = per_task("history-score-2026-10-11_1706.json"), per_task("history-score-2026-10-13_1040.json")
    return [
        {"l": "Tavily (Original)", "c": "orig", "v": new["tavily"]},
        {"l": "Tavily-Nachbau vorher", "c": "alt", "v": old["own-tavily"]},
        {"l": "Tavily-Nachbau jetzt", "c": "neu", "v": new["own-tavily"]},
        {"l": "Serper (Original)", "c": "orig", "v": new["serper"], "gap": True},
        {"l": "Serper-Nachbau vorher", "c": "alt", "v": old["own-serper"]},
        {"l": "Serper-Nachbau jetzt", "c": "neu", "v": new["own-serper"]},
        {"l": "Exa-Nachbau vorher", "c": "alt", "v": old["own-exa"], "gap": True},
        {"l": "Exa-Nachbau jetzt", "c": "neu", "v": new["own-exa"]},
    ]


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    charts = {}
    sek = lambda v: (v, de(v, 2) + " s")

    charts["blindtest"] = dots(
        "Wie seriös sind die Quellen, die die Suche liefert?",
        "Unser Nachbau ist fast so gut wie Tavily (3,51 zu 3,56), vorher 3,14.",
        "30 Faktenfragen, blind bewertet. Jeder Punkt = eine Frage, schwarzer Strich = Durchschnitt.",
        blind_rows(), 1, 5, [1, 2, 3, 4, 5], lambda v: de(v, 2) if v % 1 else str(int(v)),
        ("1 = unseriös", "5 = sehr seriös"),
        note="Der Bewerter wusste nicht, welche Quelle von welchem System kommt.")

    charts["recherche"] = dots(
        "Steht bei echten Recherchen eine gute Quelle weit oben?",
        "Serper-Nachbau schlägt Serper (0,31 zu 0,27), Tavily ist noch vorn (0,28 zu 0,22).",
        "9 echte Recherche-Aufgaben. Jeder Punkt = eine Aufgabe, schwarzer Strich = Durchschnitt.",
        research_rows(), 0, 1, [0, 0.25, 0.5, 0.75, 1], lambda v: de(v, 2),
        ("0 = keine gute Quelle", "1 = gute Quelle auf Platz 1"),
        note="0,5 heißt: die erste gute Quelle steht im Schnitt auf Platz 2. Exa-Original ohne gültigen Key nicht messbar.")

    charts["themenfremd"] = bars(
        "Wie viele Treffer haben nichts mit der Frage zu tun?",
        "Fast alle themenfremden Treffer sind weg: 18 auf 1 und 17 auf 2.",
        "8 Suchen nach Offshore-Kontakten. Themenfremd: Shopping-Sender HSE, Wörterbücher, Banken. Weniger ist besser.",
        [{"l": "Tavily-Nachbau", "v": {"alt": (18, "18 von 77 Treffern"), "neu": (1, "1 von 61 Treffern")}},
         {"l": "Serper-Nachbau", "v": {"alt": (17, "17 von 80 Treffern"), "neu": (2, "2 von 80 Treffern")}}],
        ["alt", "neu"])

    charts["seo"] = bars(
        "Wie viele SEO- und Werbeseiten sind unter den Top-Treffern?",
        "Von 36 auf 23 gesenkt, Tavily liegt mit 15 noch besser.",
        "30 Fragen mal 3 Top-Treffer = 90 Treffer je System. Weniger ist besser.",
        [{"l": "Tavily-Suche", "v": {"orig": (15, "15 von 90"), "alt": (36, "36 von 90"), "neu": (23, "23 von 90")}}],
        ["orig", "alt", "neu"])

    charts["tempo"] = bars(
        "Wie lange dauert eine typische Anfrage?",
        "Unser Nachbau ist bei jedem Tool schneller als das Original.",
        "Typische Antwortzeit in Sekunden (Median). Kürzer ist besser.",
        [{"l": "Tavily-Suche", "v": {"orig": sek(1.33), "alt": sek(1.11), "neu": sek(0.84)}},
         {"l": "Serper", "v": {"orig": sek(1.6), "alt": sek(0.97), "neu": sek(0.79)}},
         {"l": "Exa", "v": {"alt": sek(2.25), "neu": sek(1.69)}},
         {"l": "Jina-Reader", "v": {"orig": sek(3.79), "alt": sek(1.0), "neu": sek(0.45)}}],
        ["orig", "alt", "neu"],
        note="Exa-Original: kein gültiger Key, darum ohne gelben Balken.")

    charts["zuverlaessigkeit"] = bars(
        "Wie oft geht eine Anfrage schief?",
        "Im Normalbetrieb kein einziger Fehler mehr, beim Ausfall halb so viele.",
        "Anfragen, die mit Fehler oder ohne Treffer enden. Weniger ist besser.",
        [{"l": "Normalbetrieb", "v": {"alt": (3 / 160 * 100, "3 von 160 (1,9 %)"), "neu": (0, "0 von 194 (0 %)")}},
         {"l": "Eigene Suchmaschine aus", "v": {"alt": (25 / 49 * 100, "25 von 49 (51 %)"),
                                                "neu": (13 / 49 * 100, "13 von 49 (27 %)")}}],
        ["alt", "neu"],
        note="„Eigene Suchmaschine aus“: SearXNG absichtlich abgeschaltet, um den Notfall zu testen.")

    pkt = lambda v: (v, de(v * 100, 1) + " von 100")
    charts["noten"] = bars(
        "Wie gut beantwortet jedes Tool unsere Prüffragen?",
        "Drei Tools besser als vorher, Exa war schon sehr gut und blieb gleich.",
        "Gesamtnote aus Prüffragen, die beim Bauen nie benutzt wurden. Mehr ist besser.",
        [{"l": "Tavily-Suche", "v": {"alt": pkt(0.835), "neu": pkt(0.865)}},
         {"l": "Serper", "v": {"alt": pkt(0.639), "neu": pkt(0.705)}},
         {"l": "Exa", "v": {"alt": pkt(0.951), "neu": pkt(0.951)}},
         {"l": "Jina-Reader", "v": {"alt": pkt(0.870), "neu": pkt(0.885)}}],
        ["alt", "neu"],
        note="Die Original-Anbieter wurden mit diesen Prüffragen nicht gemessen.")

    for name, svg in charts.items():
        (OUT / f"{name}.svg").write_text(svg)
        print("geschrieben:", OUT / f"{name}.svg")


if __name__ == "__main__":
    main()
