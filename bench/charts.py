"""Charts for the README: 0.3.0 against 0.2.0, drawn as plain SVG without extra packages.

    python bench/charts.py [--out docs/bench]

Reads out/features/*.{alt,neu}.json (feature_bench.py with 0.2.0 = alt and 0.3.0 = neu)
and out/viz.json (viz.py after a loop night). Writes one light and one dark file per chart:
features-*.svg, answers-*.svg and draws-*.svg. The functions take a theme, so other
layouts (a social card, a slide) can reuse them.
"""
from __future__ import annotations

import argparse
import json
import random
from html import escape
from pathlib import Path

from common import OUT

FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
THEMES = {
    "light": {"fg": "#1f2328", "muted": "#59636e", "grid": "#d8dee4", "old": "#a99a9a", "old2": "#6f6262",
              "new": "#1e8f6b", "new2": "#59c79d", "halo": "#ffffff", "bg": "none"},
    "dark": {"fg": "#e6edf3", "muted": "#9198a1", "grid": "#30363d", "old": "#7d6f6f", "old2": "#b3a5a5",
             "new": "#7ee2b8", "new2": "#2fa57c", "halo": "#0d1117", "bg": "none"},
}
VERSIONS = [  # key in viz.json answers, label, colour slot
    ("alt_snippets", "0.2.0 · search snippets", "old"),
    ("alt_seiten", "0.2.0 · whole pages in hit order", "old2"),
    ("neu_snippets", "0.3.0 · snippets, reranked", "new2"),
    ("heute", "0.3.0 · passages, reranked", "new"),
]
BUDGET = 4800  # characters a model reads: 8 passages of 600, today's default


def _text(x, y, s, t, *, size=13, anchor="start", fill="fg", weight=400):
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" font-weight="{weight}" '
            f'text-anchor="{anchor}" fill="{t[fill]}">{escape(s)}</text>')


def _svg(w, h, body, t, label):
    bg = "" if t["bg"] == "none" else f'<rect width="{w}" height="{h}" fill="{t["bg"]}"/>'
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'role="img" aria-label="{escape(label)}" font-family="{escape(FONT)}">{bg}{body}</svg>\n')


# ------------------------------------------------------------------ data
def feature_rows(features: Path):
    def load(name, label):
        return json.loads((features / f"{name}.{label}.json").read_text(encoding="utf-8"))

    def ratio(name, label, pred, base=None):
        rows = [r for r in load(name, label) if base is None or base(r)]
        return sum(1 for r in rows if pred(r)), len(rows)

    specs = [
        ("Normal pages read", "failures", lambda r: r["read"], lambda r: r["expect"] == "ok"),
        ("Failed pages with a reason", "failures", lambda r: bool(r["reason"]), lambda r: r["expect"] != "ok"),
        ("PDFs read", "pdfs", lambda r: r["chars"] > 0, None),
        ("Answer found in the PDF", "pdfs", lambda r: r["found"], None),
        ("Shop pages read", "shops", lambda r: r["read"], None),
        ("Shop price right", "shops", lambda r: r["price_ok"], None),
        ("Shop currency right", "shops", lambda r: r["currency_ok"], None),
        ("Shop GTIN right", "shops", lambda r: r["gtin_ok"], None),
        ("Regional question answered", "region", lambda r: r["local"].get("found"), None),
    ]
    out = []
    for name, file, pred, base in specs:
        old, n = ratio(file, "alt", pred, base)
        new, _ = ratio(file, "neu", pred, base)
        out.append((name, old, new, n))
    return out


def version_scores(viz: dict, draws: int = 20, seed: int = 42):
    """Per version: MRR and found share on test, plus `draws` resamples of dev and test."""
    def mrr(got):
        return sum(1 / g["pos"] for g in got if g["pos"] and g["pos"] <= 20) / len(got)

    def found(got):
        return sum(1 for g in got if g["chars"] and g["chars"] <= BUDGET) / len(got)

    rng = random.Random(seed)
    out = []
    for key, label, slot in VERSIONS:
        split = {s: [a[key] for a in viz["answers"] if a["split"] == s] for s in ("dev", "test")}
        cloud = []
        for _ in range(draws):
            dv = [rng.choice(split["dev"]) for _ in split["dev"]]
            ts = [rng.choice(split["test"]) for _ in split["test"]]
            cloud.append((mrr(dv), mrr(ts)))
        out.append({"key": key, "label": label, "slot": slot, "mrr": mrr(split["test"]),
                    "found": found(split["test"]), "dev": mrr(split["dev"]), "n": len(split["test"]),
                    "cloud": cloud})
    return out


# ------------------------------------------------------------------ charts
def features_svg(rows, t, w=760, legend=("0.2.0", "0.3.0")):
    label_w, row_h, top = 232, 40, 36
    bar_w = w - label_w - 64
    h = top + row_h * len(rows) + 6
    parts = []
    for i, (key, x0) in enumerate((("old", 0), ("new", 90))):
        parts.append(f'<rect x="{label_w + x0}" y="8" width="12" height="12" rx="3" fill="{t[key]}"/>')
        parts.append(_text(label_w + x0 + 18, 18.5, legend[i], t, fill="muted"))
    for i, (name, old, new, n) in enumerate(rows):
        y = top + i * row_h
        parts.append(_text(0, y + 17, name, t))
        for j, (val, key, fill) in enumerate(((old, "old", "muted"), (new, "new", "fg"))):
            yy = y + 4 + j * 14
            bw = bar_w * val / n if n else 0
            if val:
                parts.append(f'<rect x="{label_w}" y="{yy}" width="{max(bw, 2):.1f}" height="10" rx="3" fill="{t[key]}"/>')
            parts.append(_text(label_w + bw + 6, yy + 9.5, f"{val}/{n}", t, size=12, fill=fill,
                               weight=600 if key == "new" else 400))
    return _svg(w, h, "".join(parts), t, "0.2.0 against 0.3.0 on fixed test lists")


def answers_svg(scores, t, w=760, titles=("Answer in the first 4,800 characters", "MRR: right passage near the top"),
                stacked=False, label_w=232):
    """Two measures per version, side by side (README) or one above the other (narrow cards)."""
    row_h, head, gap = 30, 34, 36
    metrics = [(lambda s: s["found"], lambda s: f"{s['found']:.0%}", titles[0]),
               (lambda s: s["mrr"], lambda s: f"{s['mrr']:.2f}", titles[1])]
    if stacked:
        metrics.reverse()  # MRR first, it is what the card's headline is about
        panel, block = w - label_w, head + row_h * len(scores) + 18
        origins = [(label_w, i * block) for i in range(2)]
        h = 2 * block
    else:
        panel = (w - label_w - gap) / 2
        origins = [(label_w, 0), (label_w + panel + gap, 0)]
        h = head + row_h * len(scores) + 4
    parts = []
    for (value, fmt, title), (x0, y0) in zip(metrics, origins):
        parts.append(_text(x0, y0 + 16, title, t, fill="muted", size=12))
        for i, s in enumerate(scores):
            y = y0 + head + i * row_h
            strong = 600 if s["key"] == "heute" else 400
            if stacked or x0 == label_w:
                parts.append(_text(0, y + 14, s["label"], t, weight=strong))
            track = panel - 44
            parts.append(f'<rect x="{x0:.1f}" y="{y + 3}" width="{track:.1f}" height="14" rx="4" fill="{t["grid"]}" opacity="0.5"/>')
            parts.append(f'<rect x="{x0:.1f}" y="{y + 3}" width="{track * value(s):.1f}" height="14" rx="4" fill="{t[s["slot"]]}"/>')
            parts.append(_text(x0 + track + 6, y + 14.5, fmt(s), t, size=12, weight=strong))
    return _svg(w, h, "".join(parts), t, "Answer quality per version")


def draws_svg(scores, t, w=760, h=520, axes=("MRR on tuning questions (dev)", "MRR on held-out questions (test)"),
              short=None):
    short = short or {"alt_snippets": "0.2.0 snippets", "alt_seiten": "0.2.0 pages",
                      "neu_snippets": "0.3.0 snippets", "heute": "0.3.0 passages"}
    m = {"l": 52, "r": 16, "t": 16, "b": 46}
    vals = [v for s in scores for pt in s["cloud"] for v in pt]
    lo = min(0.5, int(min(vals) * 20) / 20)
    def sx(v):
        return m["l"] + (v - lo) / (1 - lo) * (w - m["l"] - m["r"])

    def sy(v):
        return h - m["b"] - (v - lo) / (1 - lo) * (h - m["t"] - m["b"])

    parts = []
    tick = lo
    while tick <= 1.0001:
        parts.append(f'<line x1="{sx(tick):.1f}" x2="{sx(tick):.1f}" y1="{m["t"]}" y2="{h - m["b"]}" stroke="{t["grid"]}"/>')
        parts.append(f'<line x1="{m["l"]}" x2="{w - m["r"]}" y1="{sy(tick):.1f}" y2="{sy(tick):.1f}" stroke="{t["grid"]}"/>')
        parts.append(_text(sx(tick), h - m["b"] + 18, f"{tick:.2f}", t, size=12, anchor="middle", fill="muted"))
        parts.append(_text(m["l"] - 8, sy(tick) + 4, f"{tick:.2f}", t, size=12, anchor="end", fill="muted"))
        tick = round(tick + 0.1, 2)
    parts.append(f'<line x1="{sx(lo):.1f}" y1="{sy(lo):.1f}" x2="{sx(1):.1f}" y2="{sy(1):.1f}" stroke="{t["muted"]}" stroke-dasharray="4 4"/>')
    parts.append(_text(w - m["r"], h - 6, axes[0], t, size=12, anchor="end", fill="muted"))
    parts.append(_text(m["l"] + 8, m["t"] + 14, axes[1], t, size=12, fill="muted"))
    for s in scores:
        for dv, ts in s["cloud"]:
            parts.append(f'<circle cx="{sx(dv):.1f}" cy="{sy(ts):.1f}" r="4" fill="{t[s["slot"]]}" fill-opacity="0.55"/>')
    nudge = {"heute": -16, "neu_snippets": 22}  # the two 0.3.0 values sit close: one label above, one below
    for s in scores:
        cx, cy = sx(s["dev"]), sy(s["mrr"])
        parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="8" fill="{t[s["slot"]]}" stroke="{t["halo"]}" stroke-width="2.5"/>')
        dy = nudge.get(s["key"], 0)
        parts.append(f'<text x="{cx - 14:.1f}" y="{cy + 4.5 + dy:.1f}" font-size="13" font-weight="600" text-anchor="end" '
                     f'fill="{t["fg"]}" stroke="{t["halo"]}" stroke-width="4" paint-order="stroke" '
                     f'stroke-linejoin="round">{escape(short[s["key"]])}</text>')
    return _svg(w, h, "".join(parts), t, "Twenty draws of the questions per version")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "docs" / "bench"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    rows = feature_rows(OUT / "features")
    scores = version_scores(json.loads((OUT / "viz.json").read_text(encoding="utf-8")))
    for name, t in THEMES.items():
        (out / f"features-{name}.svg").write_text(features_svg(rows, t), encoding="utf-8")
        (out / f"answers-{name}.svg").write_text(answers_svg(scores, t), encoding="utf-8")
        (out / f"draws-{name}.svg").write_text(draws_svg(scores, t), encoding="utf-8")
    for s in scores:
        print(f"{s['label']:36} found {s['found']:.0%}  MRR {s['mrr']:.2f}  (test, n={s['n']})")
    print("written to", out)


if __name__ == "__main__":
    main()
# Stand: 03.11.2026
