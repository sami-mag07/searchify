"""Pipeline der Themen-Bibliothek: baut je Regal einen kleinen Index (Auftrag 14.10.2026).

  .venv/bin/python -m app.library_build [--out data/bibliothek/regale.json]

Quellen (nur Funde bis LIBRARY_CUTOFF, Standard 2026-10-01, Zeit-Trennung zum Pruefsatz):
- research-agent tasks.json: je Aufgabe Frage, Suchanfragen, Gold-Links (= ein Regal).
- Programm-Tracker-Seeds aus Emil (web/programme_start_*.json), Kategorie -> Regal.
Daraus je Regal:
- Stichwoerter fuer die Themen-Erkennung (TF-IDF ueber die Texte der Regale, kein LLM).
- Eintraege: die Programmseiten selbst (Name, Notiz, Titel/Beschreibung der Seite) und
  die Links, die heute auf diesen Seiten stehen und zum Regal passen. So kommen neue
  Ausschreibungen von Uebersichtsseiten (Hackathon-Listen, Luma-Kalender, Jugendringe)
  in die Bibliothek, ohne dass jemand sie eintraegt. Darum regelmaessig neu bauen
  (launchd, docs/bibliothek.md).
Geladen wird ueber app.extract.fetch_url (SSRF-Schutz, Zeitlimit), hoechstens 4 parallel.
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import subprocess
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, Iterable, List, Optional
from urllib.parse import urlsplit

from . import extract as extract_mod
from . import library as library_mod

ROOT = Path(__file__).resolve().parent.parent
TASKS = Path(os.environ.get("LIBRARY_TASKS", Path.home() / ".claude/skills/research-agent/evals/tasks.json"))
SEEDS = os.environ.get("LIBRARY_SEEDS", str(Path.home() / "Desktop/APP/telegram-agent/web/programme_start_*.json"))
CUTOFF = os.environ.get("LIBRARY_CUTOFF", "2026-10-01")

# Tracker-Kategorie -> Regal (Regale = Aufgaben in tasks.json)
KATEGORIE = {
    "Hackathon": "hackathon", "Gründer": "gruender", "Konferenz": "konferenz",
    "Politik": "politik", "UN": "international", "Erasmus+": "erasmus",
    "Planspiel": "planspiel", "Uni": "uni",
}
KEYWORDS_PER_REGAL = 40
ALPHA = 0.5
LINKS_PER_PAGE = 300
LISTING_DEPTH = 2  # so viele Elternpfade je Programmseite, dazu die Startseite
MIN_ANCHOR_WORDS = 2


def _git_date(path: str) -> Optional[str]:
    """Datum des letzten Commits einer Seed-Datei (YYYY-MM-DD) oder None."""
    try:
        out = subprocess.run(["git", "-C", str(Path(path).parent), "log", "-1", "--format=%cs", "--", path],
                             capture_output=True, text=True, timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return out or None


def load_sources(cutoff: str = CUTOFF) -> Dict[str, Dict]:
    """{regal: {"texte": [...], "seiten": [{url, text}]}} aus Quellen bis cutoff."""
    regale: Dict[str, Dict] = defaultdict(lambda: {"texte": [], "seiten": []})
    tasks = json.load(open(TASKS, encoding="utf-8"))
    for t in tasks["tasks"]:
        r = regale[t["id"]]
        r["texte"] += [t.get("frage", "")] + t.get("queries", []) + t.get("queries_niche", [])
        r["seiten"] += [{"url": u, "text": ""} for u in t["gold"]]
    for path in sorted(glob.glob(SEEDS)):
        date = _git_date(path)
        if not date or date > cutoff:
            continue  # nach dem Stichtag (oder nicht nachweisbar): nicht in den Bau
        for e in json.load(open(path, encoding="utf-8")):
            regal = KATEGORIE.get(e.get("kategorie", ""))
            url = (e.get("link") or "").strip()
            if not regal or not url.startswith("http"):
                continue
            text = " ".join(str(e.get(k) or "") for k in ("name", "notiz", "ort", "kosten"))
            regale[regal]["texte"].append(text)
            regale[regal]["seiten"].append({"url": url, "text": text})
    return dict(regale)


def keywords(texts: Dict[str, List[str]]) -> Dict[str, Dict[str, float]]:
    """Stichwoerter je Regal als Log-Verhaeltnis: wie viel haeufiger ein Wortstamm im
    Regal ist als in allen Regalen zusammen (geglaettet). Anders als IDF bleiben so
    Woerter wie "hackathon" stark, die auch anderswo vorkommen, im Regal aber dauernd.
    Nur Staemme, die im Regal mindestens zweimal vorkommen."""
    tf = {r: Counter(library_mod.stems(" ".join(v))) for r, v in texts.items()}
    total = Counter()
    for c in tf.values():
        total.update(c)
    n_all, vocab = sum(total.values()), len(total) or 1
    out = {}
    for r, c in tf.items():
        n_r = sum(c.values())
        scored = {}
        for s, k in c.items():
            if k < 2:
                continue
            w = math.log((k + ALPHA) / (n_r + ALPHA * vocab)) - math.log((total[s] + ALPHA) / (n_all + ALPHA * vocab))
            if w > 0:
                scored[s] = round(w, 4)
        out[r] = dict(sorted(scored.items(), key=lambda kv: kv[1] * math.log(1 + c[kv[0]]), reverse=True)
                      [:KEYWORDS_PER_REGAL])
    return out


BACKGROUND = "_allgemein"
BACKGROUND_FILES = ("eval/queries_big.jsonl",)


def _background() -> List[str]:
    out = []
    for name in BACKGROUND_FILES:
        try:
            for line in open(ROOT / name, encoding="utf-8"):
                q = json.loads(line).get("query")
                if q:
                    out.append(q)
        except (OSError, ValueError):
            continue
    return out


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower().removeprefix("www.")


def _page(url: str) -> Dict:
    html = extract_mod.fetch_url(url, timeout=8.0)
    if not html:
        return {"url": url, "ok": False}
    meta = extract_mod._metadata(html, url, extensive=False)
    links = extract_mod.extract_links(html, url, limit=LINKS_PER_PAGE)
    return {"url": url, "ok": True, "title": meta.get("title") or "",
            "description": meta.get("description") or "", "links": links}


def listing_urls(url: str, depth: int = LISTING_DEPTH) -> List[str]:
    """Moegliche Uebersichtsseiten zu einer Programmseite: Elternpfade und Startseite
    (/events/x -> /events/ -> /). Dort stehen meist die aktuellen Ausschreibungen."""
    parts = urlsplit(url)
    if not parts.scheme or not parts.hostname:
        return []
    segs = [x for x in parts.path.split("/") if x]
    out = []
    for cut in range(len(segs) - 1, max(-1, len(segs) - 1 - depth), -1):
        out.append(f"{parts.scheme}://{parts.netloc}/" + "/".join(segs[:cut]) + ("/" if cut else ""))
    root = f"{parts.scheme}://{parts.netloc}/"
    if root not in out:
        out.append(root)
    return [u for u in out if u.rstrip("/") != url.rstrip("/")]


def _fits(text: str, kw: Dict[str, float]) -> bool:
    return any(s in kw for s in library_mod.stems(text))


def build(out: Path, cutoff: str = CUTOFF, workers: int = 4) -> Dict:
    started = time.time()
    regale = load_sources(cutoff)
    urls = sorted({s["url"] for v in regale.values() for s in v["seiten"]})
    with ThreadPoolExecutor(workers) as pool:
        pages = {p["url"]: p for p in pool.map(_page, urls)}
    # Erkennung aus Fragen, Suchanfragen, Tracker-Texten und Titel/Beschreibung der
    # Programmseiten (nicht aus Link-Texten, die sind zu laut).
    # Uebersichtsseiten (Elternpfade, Startseite) der Programmseiten mitladen.
    listing_of: Dict[str, List[str]] = {u: listing_urls(u) for u in urls}
    extra = sorted({x for v in listing_of.values() for x in v} - set(pages))
    with ThreadPoolExecutor(workers) as pool:
        pages.update({p["url"]: p for p in pool.map(_page, extra)})
    texts = {r: v["texte"] + [f"{pages.get(x['url'], {}).get('title', '')} "
                              f"{pages.get(x['url'], {}).get('description', '')}" for x in v["seiten"]]
             for r, v in regale.items()}
    # Hintergrund aus allgemeinen Fragen (nur dev-Teil queries_big, nie test): Woerter,
    # die dort genauso oft vorkommen, zaehlen nicht als Thema.
    texts[BACKGROUND] = _background()
    kw = keywords(texts)
    shelves = {}
    for regal, v in regale.items():
        entries: Dict[str, Dict] = {}
        for s in v["seiten"]:
            p = pages.get(s["url"], {})
            text = " ".join(x for x in (s["text"], p.get("title", ""), p.get("description", "")) if x)
            entries[s["url"]] = {"url": s["url"], "text": text[:600], "art": "seite"}
            links = list(p.get("links", []))
            for lst in listing_of.get(s["url"], []):
                links += pages.get(lst, {}).get("links", [])
            for link in links:
                u, anchor = link["url"], link["text"]
                if u in entries or len(anchor.split()) < MIN_ANCHOR_WORDS:
                    continue
                probe = f"{anchor} {u.replace('-', ' ').replace('/', ' ')}"
                if not _fits(probe, kw[regal]):
                    continue
                entries[u] = {"url": u, "text": anchor, "art": "link", "von": _host(s["url"])}
        shelves[regal] = {"keywords": kw[regal], "eintraege": list(entries.values())}
    shelves[BACKGROUND] = {"keywords": kw[BACKGROUND], "eintraege": []}
    data = {"gebaut": time.strftime("%Y-%m-%dT%H:%M:%S"), "stichtag": cutoff,
            "quellen": {"tasks": str(TASKS), "seeds": SEEDS},
            "seiten_geladen": sum(p["ok"] for p in pages.values()), "seiten": len(pages),
            "uebersichten": len(extra),
            "regale": shelves, "dauer_s": round(time.time() - started, 1)}
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(out)
    return data


def _summary(data: Dict) -> Iterable[str]:
    yield f"gebaut {data['gebaut']}, Seiten {data['seiten_geladen']}/{data['seiten']}, {data['dauer_s']} s"
    for r, v in data["regale"].items():
        arten = Counter(e["art"] for e in v["eintraege"])
        top = ", ".join(list(v["keywords"])[:8])
        yield f"  {r:16} Seiten {arten['seite']:3}  Links {arten['link']:4}  Stichwoerter: {top}"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(library_mod.default_path()))
    ap.add_argument("--cutoff", default=CUTOFF)
    a = ap.parse_args()
    for line in _summary(build(Path(a.out), a.cutoff)):
        print(line)


if __name__ == "__main__":
    main()
