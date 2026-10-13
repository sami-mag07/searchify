"""Builds the README charts (docs/img/*.svg) from the measurement files in eval/private/.

Yellow = original provider, red = our clone before (Oct 11), green = now (Oct 13),
blue = after fine-tuning (Oct 13 evening: weak-result cutoff, off-topic results demoted).
Run: .venv/bin/python eval/readme_charts.py
"""
import json
from collections import defaultdict
from html import escape
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRIV = ROOT / "eval" / "private"
OUT = ROOT / "docs" / "img"

COL = {"orig": "#E8A80C", "before": "#E4572E", "now": "#12A36B", "tuned": "#3B6FE0"}
NAME = {"orig": "Original provider", "before": "Our clone, before (Oct 11)",
        "now": "Our clone, now (Oct 13)", "tuned": "After fine-tuning"}
INK, MUTED, GRID, BG, GOOD = "#1F2328", "#59636E", "#EAECEF", "#FFFFFF", "#1D4ED8"
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
W = 800
TOP = 128  # drawing area starts below title, result line, how-to line and legend


def load(name):
    return json.loads((PRIV / name).read_text())


def num(v, d=2):
    return f"{v:.{d}f}"


def text(x, y, s, size=13, color=INK, anchor="start", weight=400):
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{color}" '
            f'text-anchor="{anchor}" font-weight="{weight}">{escape(str(s))}</text>')


def legend(keys, y):
    parts, x = [], 24
    for k in keys:
        parts.append(f'<circle cx="{x + 6}" cy="{y - 4}" r="6" fill="{COL[k]}"/>')
        parts.append(text(x + 18, y, NAME[k], 12, INK))
        x += 34 + 6.0 * len(NAME[k])
    return parts


def frame(title, result, howto, body, h):
    """Header of every chart: question, result in one sentence, how to read it."""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{h}" viewBox="0 0 {W} {h}" '
            f'font-family="{FONT}">'
            f'<rect width="{W}" height="{h}" rx="12" fill="{BG}" stroke="{GRID}"/>'
            + text(24, 34, title, 17, INK, weight=650)
            + text(24, 58, "Result: " + result, 13.5, GOOD, weight=650)
            + text(24, 80, howto, 12, MUTED)
            + "".join(body) + "</svg>")


def dots(title, result, howto, rows, lo, hi, ticks, fmt, ends, note=None, label_w=200):
    """Dot plot: each row has many single values (dots) and an average (bar with number)."""
    row_h = 40
    x0, x1 = 24 + label_w, W - 120
    sx = lambda v: x0 + (v - lo) / (hi - lo) * (x1 - x0)
    bottom = TOP + row_h * len(rows)
    h = bottom + 58 + (22 if note else 0)
    b = legend(sorted({r["c"] for r in rows}, key=list(COL).index), 108)
    b.append(text(W - 24, TOP - 4, "Average", 11.5, MUTED, "end"))
    for t in ticks:
        b.append(f'<line x1="{sx(t):.1f}" x2="{sx(t):.1f}" y1="{TOP}" y2="{bottom}" stroke="{GRID}"/>')
        b.append(text(sx(t), bottom + 16, fmt(t), 11, MUTED, "middle"))
    b.append(text(x0, bottom + 34, ends[0], 11.5, MUTED, "start"))
    b.append(text(x1, bottom + 34, ends[1] + "  →  better", 11.5, INK, "end", 600))
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
    """One bar per state within each group. Values as (number, label)."""
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


# Measurements after fine-tuning (Oct 13 evening)
TUNED_HISTORY = "history-score-2026-10-13_1911.json"
TUNED_TEMPO = "live-tempo-2026-10-13-tuned.json"


def blind_rows():
    def per_question(folder, names):
        key, verdicts = load(f"{folder}/key.json"), load(f"{folder}/urteile.json")
        acc = defaultdict(lambda: defaultdict(list))
        for u in verdicts:
            q = u["id"].rsplit("-", 1)[0]
            acc[key[u["id"]]][q].append(u["vertrauen"])
        return {names[s]: [sum(v) / len(v) for v in qs.values()] for s, qs in acc.items() if s in names}

    old = per_question("blind", {"eigen": "before"})
    new = per_question("zuverl-2026-10-13/blind", {"tavily": "orig", "b": "now"})
    return [
        {"l": "Tavily (original)", "c": "orig", "v": new["orig"]},
        {"l": "Clone, before", "c": "before", "v": old["before"]},
        {"l": "Clone, now", "c": "now", "v": new["now"]},
        # The fine-tuning only removes weak results below the top 3, so the judged top 3 are the same.
        {"l": "Clone, fine-tuned", "c": "tuned", "v": new["now"]},
    ]


def research_rows():
    def per_task(fname):
        d = load(fname)["queries"]["engines"]
        return {e["engine"]: [t["mrr"] for t in e["per_task"]] for e in d}

    old = per_task("history-score-2026-10-11_1706.json")
    new = per_task("history-score-2026-10-13_1040.json")
    tuned = per_task(TUNED_HISTORY)
    return [
        {"l": "Tavily (original)", "c": "orig", "v": new["tavily"]},
        {"l": "Tavily clone, before", "c": "before", "v": old["own-tavily"]},
        {"l": "Tavily clone, now", "c": "now", "v": new["own-tavily"]},
        {"l": "Tavily clone, fine-tuned", "c": "tuned", "v": tuned["own-tavily"]},
        {"l": "Serper (original)", "c": "orig", "v": new["serper"], "gap": True},
        {"l": "Serper clone, before", "c": "before", "v": old["own-serper"]},
        {"l": "Serper clone, now", "c": "now", "v": new["own-serper"]},
        {"l": "Serper clone, fine-tuned", "c": "tuned", "v": tuned["own-serper"]},
        {"l": "Exa clone, before", "c": "before", "v": old["own-exa"], "gap": True},
        {"l": "Exa clone, now", "c": "now", "v": new["own-exa"]},
        {"l": "Exa clone, fine-tuned", "c": "tuned", "v": tuned["own-exa"]},
    ]


def mean(v):
    return sum(v) / len(v)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    charts = {}
    sec = lambda v: (v, num(v, 2) + " s")
    research = research_rows()
    avg = {r["l"]: mean(r["v"]) for r in research}
    tuned_tempo = load(TUNED_TEMPO)
    t = lambda d, k: sec(d[k]["median_s"])
    # 81 research requests (0 errors, history-score-2026-10-13_1911) plus the live tempo run
    tuned_ok = sum(v["ok"] for v in tuned_tempo.values()) + 81
    tuned_n = sum(v["n"] for v in tuned_tempo.values()) + 81

    charts["blindtest"] = dots(
        "How trustworthy are the sources the search returns?",
        "Our clone is almost as good as Tavily (3.51 vs 3.56), up from 3.14.",
        "30 fact questions, judged blind. Each dot = one question, black bar = average.",
        blind_rows(), 1, 5, [1, 2, 3, 4, 5], lambda v: num(v, 2) if v % 1 else str(int(v)),
        ("1 = dubious", "5 = very trustworthy"),
        note="The judge did not know which system a source came from. Fine-tuning keeps the top 3 unchanged.")

    charts["recherche"] = dots(
        "In real research tasks, is a good source near the top?",
        f"Serper clone beats Serper ({num(avg['Serper clone, fine-tuned'])} vs 0.27), "
        f"Tavily still leads ({num(avg['Tavily (original)'])} vs {num(avg['Tavily clone, fine-tuned'])}).",
        "9 real research tasks. Each dot = one task, black bar = average.",
        research, 0, 1, [0, 0.25, 0.5, 0.75, 1], lambda v: num(v, 2),
        ("0 = no good source", "1 = good source at rank 1"),
        note="0.5 means the first good source is at rank 2 on average. Exa original: no valid key, not measurable.")

    charts["themenfremd"] = bars(
        "How many results have nothing to do with the question?",
        "Fine-tuning removed almost all off-topic results: 18 down to 1, 17 down to 2.",
        "8 searches for offshore contacts. Off-topic: TV shopping channel HSE, dictionaries, banks. Fewer is better.",
        [{"l": "Tavily clone", "v": {"now": (18, "18 of 77 results"), "tuned": (1, "1 of 61 results")}},
         {"l": "Serper clone", "v": {"now": (17, "17 of 80 results"), "tuned": (2, "2 of 80 results")}}],
        ["now", "tuned"],
        note="Not measured for the Oct 11 version.")

    charts["seo"] = bars(
        "How many SEO and ad pages are among the top results?",
        "Down from 36 to 23, Tavily is still better with 15.",
        "30 questions times top 3 results = 90 results per system. Fewer is better.",
        [{"l": "Tavily search", "v": {"orig": (15, "15 of 90"), "before": (36, "36 of 90"),
                                      "now": (23, "23 of 90"), "tuned": (23, "23 of 90")}}],
        ["orig", "before", "now", "tuned"],
        note="Fine-tuning only drops weak results below the top 3, so this count stays the same.")

    # Before and fine-tuned measured in one run (Oct 14): Oct 11 code on :8003 and the
    # current service on :8000, 30 fresh questions, order alternating per question.
    ab = load("live-tempo-ab-2026-10-14.json")
    m = lambda port, ep: sec(ab[f"{port}{ep}"]["median"])
    charts["tempo"] = bars(
        "How long does a typical request take?",
        "Fine-tuned is not slower: faster for Tavily search and Exa, equal for Serper.",
        "Typical response time in seconds (median). Shorter is better.",
        [{"l": "Tavily search", "v": {"orig": sec(1.33), "before": m(8003, "/search"),
                                      "tuned": m(8000, "/search")}},
         {"l": "Serper", "v": {"orig": sec(1.6), "before": m(8003, "/serper/search"),
                               "tuned": m(8000, "/serper/search")}},
         {"l": "Exa", "v": {"before": m(8003, "/exa/search"), "tuned": m(8000, "/exa/search")}},
         {"l": "Jina Reader", "v": {"orig": sec(3.79), "before": sec(1.0),
                                    "tuned": t(tuned_tempo, "/jina/r")}}],
        ["orig", "before", "tuned"],
        note="Before and fine-tuned ran side by side: 30 fresh questions, alternating order. "
             "Reader: separate runs. Exa original: no valid key.")

    charts["zuverlaessigkeit"] = bars(
        "How often does a request fail?",
        "Almost no errors in normal operation, half as many when the search engine is down.",
        "Requests that end with an error or without results. Fewer is better.",
        [{"l": "Normal operation", "v": {"before": (3 / 160 * 100, "3 of 160 (1.9 %)"),
                                         "now": (0, "0 of 194 (0 %)"),
                                         "tuned": ((tuned_n - tuned_ok) / tuned_n * 100,
                                                   f"{tuned_n - tuned_ok} of {tuned_n} ({(tuned_n - tuned_ok) / tuned_n * 100:.1f} %)")}},
         {"l": "Own search engine down", "v": {"before": (25 / 49 * 100, "25 of 49 (51 %)"),
                                               "now": (13 / 49 * 100, "13 of 49 (27 %)")}}],
        ["before", "now", "tuned"],
        note="Search engine down: SearXNG switched off on purpose to test the fallback. Not repeated after fine-tuning.")

    pts = lambda v: (v, num(v * 100, 1) + " of 100")
    charts["noten"] = bars(
        "How well does each tool answer our test questions?",
        "Three tools improved, Exa was already very good and stayed the same.",
        "Overall score on test questions never used while building. Higher is better.",
        [{"l": "Tavily search", "v": {"before": pts(0.835), "now": pts(0.865), "tuned": pts(0.865)}},
         {"l": "Serper", "v": {"before": pts(0.639), "now": pts(0.705), "tuned": pts(0.706)}},
         {"l": "Exa", "v": {"before": pts(0.951), "now": pts(0.951), "tuned": pts(0.951)}},
         {"l": "Jina Reader", "v": {"before": pts(0.870), "now": pts(0.885), "tuned": pts(0.885)}}],
        ["before", "now", "tuned"],
        note="The original providers were not measured with these test questions.")

    for name, svg in charts.items():
        (OUT / f"{name}.svg").write_text(svg)
        print("written:", OUT / f"{name}.svg")


if __name__ == "__main__":
    main()
