"""Reader-Bench: eigener Jina-Reader und Tavily-Extract gegen den Original-Jina-Reader.

Modi:
  run    holt fehlende Original-Antworten (r.jina.ai, dauerhaft gecacht, >= 4,2 s Pause,
         also unter 15/Minute) und fragt die eigenen Endpunkte ab (>= 1 s Pause),
         speichert Rohdaten nach eval/private/reader-raw-<datum>.json und wertet aus.
  score  nur Auswertung, ohne Netz, aus jina_cache/ und der neuesten reader-raw-*.json
         (oder --raw PFAD).

Aufruf:
  .venv/bin/python eval/reader_bench.py run   [--base http://127.0.0.1:8000] [--only ID,ID] [--tag r1]
  .venv/bin/python eval/reader_bench.py score [--raw eval/private/reader-raw-2026-10-11.json]

Metriken je URL und System (orig = r.jina.ai, jina = eigener /jina/r, extract = eigener /extract):
  ok        Status 200 und Inhalt > 200 Zeichen
  latency   Sekunden (Original: Zeit des ersten, gecachten Abrufs)
  title     vorhanden / gleich dem Original (normalisiert)
  recall    Anteil der Original-Woerter (>= 4 Buchstaben, Multimenge), die wir auch haben
  precision Anteil unserer Woerter, die auch im Original stehen (misst Boilerplate bei uns
            ODER Menues beim Original, darum beide Richtungen)
  links     Anzahl; Aktions-Links (Anmelden/Bewerben/Apply/Registr/Formular) gefunden,
            falls das Original welche hat
"""

from __future__ import annotations

import argparse
import datetime as dt
import glob
import hashlib
import json
import os
import re
import statistics
import sys
import time
import unicodedata
from collections import Counter
from typing import Dict, List, Optional, Tuple

import httpx

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRIVATE = os.path.join(ROOT, "eval", "private")
CACHE_DIR = os.path.join(PRIVATE, "jina_cache")

JINA_ORIG = "https://r.jina.ai/"
ORIG_PAUSE_S = 4.2  # max. 15 Abrufe pro Minute
OWN_PAUSE_S = 1.0
ORIG_TIMEOUT_S = 60.0
OWN_TIMEOUT_S = 45.0
MIN_CHARS = 200

# (id, gruppe, sprache, url) - feste Liste, reproduzierbar.
# gold: 20 URLs aus ~/.claude/skills/research-agent/evals/tasks.json (gold-Listen)
URLS: List[Tuple[str, str, str, str]] = [
    ("g01", "gold", "en", "https://ecosystem.siemens.com/hackathon/berlin/overview"),
    ("g02", "gold", "en", "https://hackathon.elevenlabs.io/"),
    ("g03", "gold", "de", "https://healthcare-hackathon.de/"),
    ("g04", "gold", "en", "https://luma.com/research-to-commerce"),
    ("g05", "gold", "en", "https://taikai.network/en/eclipse-foundation/hackathons/veles-hack-2026/"),
    ("g06", "gold", "en", "https://www.teknofest.org/en/competitions/technologies-for-humanity-competition-high-school-level"),
    ("g07", "gold", "en", "https://www.bimun.org/application"),
    ("g08", "gold", "en", "https://www.mymun.com/conferences/apromun-2026"),
    ("g09", "gold", "de", "https://www.simep.eu/anmeldung-2026/"),
    ("g10", "gold", "de", "https://calendar.boell.de/de/event/dialoge-warriors"),
    ("g11", "gold", "en", "https://www.coe.int/en/web/youth/calls-for-participants"),
    ("g12", "gold", "de", "https://www.eaberlin.de/seminars/data/2026/10/kein-recht-auf-gehorsam/"),
    ("g13", "gold", "de", "https://www.dfjw.org/ausschreibungen/peatland-peers"),
    ("g14", "gold", "en", "https://youth.europa.eu/solidarity/opportunity/54105_en"),
    ("g15", "gold", "de", "https://foundersfoundation.de/edtech-academy-bewerbung/"),
    ("g16", "gold", "de", "https://www.ibb.de/de/foerderprogramme/berliner-startup-stipendium.html"),
    ("g17", "gold", "en", "https://www.ycombinator.com/apply"),
    ("g18", "gold", "en", "https://www.oneyoungworld.com/scholarships"),
    ("g19", "gold", "de", "https://www.kas.de/de/web/begabtenfoerderung-und-kultur/bewerbung"),
    ("g20", "gold", "en", "https://beamlineforschools.cern/pre-registration/"),
    ("w01", "wiki", "de", "https://de.wikipedia.org/wiki/Berlin"),
    ("w02", "wiki", "de", "https://de.wikipedia.org/wiki/K%C3%BCnstliche_Intelligenz"),
    ("w03", "wiki", "en", "https://en.wikipedia.org/wiki/Python_(programming_language)"),
    ("w04", "wiki", "en", "https://en.wikipedia.org/wiki/Hackathon"),
    ("w05", "wiki", "es", "https://es.wikipedia.org/wiki/Madrid"),
    ("w06", "wiki", "es", "https://es.wikipedia.org/wiki/Inteligencia_artificial"),
    ("w07", "wiki", "fr", "https://fr.wikipedia.org/wiki/Paris"),
    ("w08", "wiki", "fr", "https://fr.wikipedia.org/wiki/Apprentissage_automatique"),
    ("d01", "docs", "en", "https://docs.python.org/3/library/asyncio.html"),
    ("d02", "docs", "en", "https://www.postgresql.org/docs/current/sql-select.html"),
    ("d03", "docs", "en", "https://developer.mozilla.org/en-US/docs/Web/HTTP/Status"),
    ("d04", "docs", "fr", "https://developer.mozilla.org/fr/docs/Web/JavaScript/Guide/Introduction"),
    ("d05", "docs", "es", "https://docs.python.org/es/3/tutorial/introduction.html"),
    ("d06", "docs", "en", "https://fastapi.tiangolo.com/tutorial/first-steps/"),
    ("n01", "news", "en", "https://simonwillison.net/2024/Dec/31/llms-in-2024/"),
    ("n02", "news", "en", "https://www.bbc.com/news/technology"),
    ("n03", "news", "es", "https://elpais.com/tecnologia/"),
    ("n04", "news", "es", "https://www.xataka.com/"),
    ("n05", "news", "fr", "https://www.lemonde.fr/pixels/"),
    ("n06", "news", "fr", "https://www.numerama.com/"),
]

SYSTEMS = ("orig", "jina", "extract")
ACTION_RE = re.compile(r"anmeld|bewerb|apply|registr|formular", re.I)
WORD_RE = re.compile(r"[^\W\d_]{4,}")


# ---------------------------------------------------------------- Netz


def _cache_path(url: str) -> str:
    return os.path.join(CACHE_DIR, hashlib.sha1(url.encode()).hexdigest() + ".json")


def load_orig(url: str) -> Optional[Dict]:
    path = _cache_path(url)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


_last_orig = [0.0]


def fetch_orig(client: httpx.Client, url: str) -> Dict:
    """Original-Jina einmal je URL; Antwort (auch Fehler) dauerhaft cachen."""
    cached = load_orig(url)
    if cached is not None:
        return cached
    wait = ORIG_PAUSE_S - (time.monotonic() - _last_orig[0])
    if wait > 0:
        time.sleep(wait)
    headers = {"Accept": "application/json", "X-With-Links-Summary": "true"}
    t0 = time.perf_counter()
    try:
        r = client.get(JINA_ORIG + url, headers=headers, timeout=ORIG_TIMEOUT_S)
        status, text = r.status_code, r.text
    except httpx.HTTPError as e:
        status, text = 0, f"{type(e).__name__}: {e}"
    latency = time.perf_counter() - t0
    _last_orig[0] = time.monotonic()
    entry = {"url": url, "fetched_at": dt.datetime.now().isoformat(timespec="seconds"),
             "status": status, "latency": round(latency, 3), "body": text}
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_path(url), "w", encoding="utf-8") as f:
        json.dump(entry, f, ensure_ascii=False)
    return entry


def fetch_own_extract(client: httpx.Client, base: str, url: str) -> Dict:
    body = {"urls": [url], "format": "markdown", "include_metadata": True}
    t0 = time.perf_counter()
    try:
        r = client.post(base + "/extract", json=body, timeout=OWN_TIMEOUT_S)
        status, text = r.status_code, r.text
    except httpx.HTTPError as e:
        status, text = 0, f"{type(e).__name__}: {e}"
    return {"status": status, "latency": round(time.perf_counter() - t0, 3), "body": text}


def fetch_own_jina(client: httpx.Client, base: str, url: str) -> Dict:
    # X-No-Cache: der Extract-Abruf davor soll die Latenz nicht schoenen.
    headers = {"Accept": "application/json", "X-With-Links-Summary": "true", "X-No-Cache": "true"}
    t0 = time.perf_counter()
    try:
        r = client.get(base + "/jina/r/" + url, headers=headers, timeout=OWN_TIMEOUT_S)
        status, text = r.status_code, r.text
    except httpx.HTTPError as e:
        status, text = 0, f"{type(e).__name__}: {e}"
    return {"status": status, "latency": round(time.perf_counter() - t0, 3), "body": text}


# ---------------------------------------------------------------- Normalisieren


def _json(text: str) -> Dict:
    try:
        v = json.loads(text)
        return v if isinstance(v, dict) else {}
    except (ValueError, TypeError):
        return {}


def parse_jina(entry: Optional[Dict]) -> Dict:
    """Jina-JSON (Original und Nachbau) -> status/title/content/links."""
    if not entry:
        return {"status": None, "latency": None, "title": "", "content": "", "links": []}
    data = _json(entry.get("body") or "").get("data") or {}
    links = data.get("links") or {}
    if isinstance(links, dict):
        links = [{"text": k, "url": v} for k, v in links.items()]
    else:
        links = [{"text": l.get("text", ""), "url": l.get("url", "")} for l in links if isinstance(l, dict)]
    return {"status": entry.get("status"), "latency": entry.get("latency"),
            "title": (data.get("title") or "").strip(), "content": data.get("content") or "",
            "links": links, "http_status": data.get("httpStatus")}


def parse_extract(entry: Optional[Dict]) -> Dict:
    if not entry:
        return {"status": None, "latency": None, "title": "", "content": "", "links": []}
    body = _json(entry.get("body") or "")
    res = (body.get("results") or [{}])[0] if body.get("results") else {}
    failed = body.get("failed_results") or []
    links = [{"text": l.get("text", ""), "url": l.get("url", "")}
             for l in (res.get("links") or []) if isinstance(l, dict)]
    return {"status": entry.get("status") if res else (entry.get("status") if not failed else 422),
            "latency": entry.get("latency"), "title": (res.get("title") or "").strip(),
            "content": res.get("raw_content") or "", "links": links,
            "error": (failed[0].get("error") if failed else None)}


_MD_PATTERNS = [
    (re.compile(r"!\[[^\]]*\]\([^)]*\)"), " "),          # Bilder
    (re.compile(r"\[([^\]]*)\]\([^)]*\)"), r"\1"),       # Links -> Text
    (re.compile(r"\[([^\]]*)\]\[[^\]]*\]"), r"\1"),      # Referenz-Links
    (re.compile(r"https?://\S+"), " "),                  # nackte URLs
    (re.compile(r"<[^>]+>"), " "),                       # HTML-Reste
    (re.compile(r"[#*_>|`~=\-]+"), " "),                 # Markdown-Zeichen
]


def strip_markdown(text: str) -> str:
    # Jina-Original haengt bei Text-Antworten "Links/Buttons:" an; im JSON nicht, sicherheitshalber:
    text = re.split(r"\n(?:Links/Buttons|Buttons & Links):\n", text)[0]
    for pat, rep in _MD_PATTERNS:
        text = pat.sub(rep, text)
    return text


def words(text: str) -> Counter:
    return Counter(WORD_RE.findall(strip_markdown(text).lower()))


def norm_title(t: str) -> str:
    t = unicodedata.normalize("NFKC", t or "").lower()
    t = re.sub(r"[^\w\s]", " ", t)
    return " ".join(t.split())


def coverage(orig: Counter, own: Counter) -> Tuple[Optional[float], Optional[float]]:
    inter = sum((orig & own).values())
    recall = inter / sum(orig.values()) if orig else None
    precision = inter / sum(own.values()) if own else None
    return recall, precision


# ---------------------------------------------------------------- Auswertung


def ok(p: Dict) -> bool:
    return p.get("status") == 200 and len(p.get("content") or "") > MIN_CHARS


def action_links(links: List[Dict]) -> List[str]:
    return [l["url"] for l in links if ACTION_RE.search((l.get("text") or "") + " " + (l.get("url") or ""))]


def hint(p: Dict, orig: Dict) -> str:
    """Grober Grund, warum eine URL fuer uns schlecht laeuft."""
    if p.get("status") is None:
        return "nicht abgefragt"
    if p.get("status") == 0:
        return "Timeout/Netz"
    if p.get("status") != 200:
        err = (p.get("error") or "").lower()
        if "pdf" in err:
            return "PDF"
        return f"HTTP {p.get('status')} " + (p.get("error") or "")[:60]
    n = len(p.get("content") or "")
    no = len(orig.get("content") or "")
    if n <= MIN_CHARS and no > 5 * MIN_CHARS:
        return "leer/kurz (JS-Seite oder Bot-Schutz)"
    if n < no / 4:
        return "Extraktion zu knapp"
    if n > no * 4:
        return "zu viel Boilerplate"
    return "Extraktion weicht ab"


def score_url(uid: str, group: str, lang: str, url: str, raw: Dict) -> Dict:
    parsed = {"orig": parse_jina(load_orig(url)),
              "jina": parse_jina(raw.get("jina")),
              "extract": parse_extract(raw.get("extract"))}
    o = parsed["orig"]
    ow = words(o["content"]) if ok(o) else Counter()
    o_title = norm_title(o["title"])
    o_actions = action_links(o["links"])
    row: Dict = {"id": uid, "group": group, "lang": lang, "url": url}
    for s in SYSTEMS:
        p = parsed[s]
        rec = prec = None
        if s != "orig" and ok(o):
            rec, prec = coverage(ow, words(p["content"])) if ok(p) else (0.0, None)
        acts = action_links(p["links"])
        row[s] = {
            "status": p.get("status"), "ok": ok(p), "chars": len(p["content"]),
            "latency": p.get("latency"), "title": p["title"][:120],
            "title_present": bool(p["title"]),
            "title_equal": (norm_title(p["title"]) == o_title) if (s != "orig" and o_title) else None,
            "recall": None if rec is None else round(rec, 3),
            "precision": None if prec is None else round(prec, 3),
            "links": len(p["links"]), "action_links": len(acts),
            "action_hit": (len(acts) > 0) if (s != "orig" and o_actions) else None,
        }
        if s != "orig" and (not ok(p) or (rec is not None and rec < 0.5)):
            row[s]["hint"] = hint(p, o)
    return row


def _q(vals: List[float], q: float) -> Optional[float]:
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    k = min(len(vals) - 1, max(0, int(round(q * (len(vals) - 1)))))
    return round(vals[k], 2)


def _mean(vals: List) -> Optional[float]:
    vals = [float(v) for v in vals if v is not None]
    return round(statistics.mean(vals), 3) if vals else None


def aggregate(rows: List[Dict], s: str) -> Dict:
    cells = [r[s] for r in rows]
    return {
        "n": len(cells),
        "ok_rate": _mean([c["ok"] for c in cells]),
        "latency_median": _q([c["latency"] for c in cells if c["ok"]], 0.5),
        "latency_p95": _q([c["latency"] for c in cells if c["ok"]], 0.95),
        "title_present": _mean([c["title_present"] for c in cells]),
        "title_equal": _mean([c["title_equal"] for c in cells]),
        "recall": _mean([c["recall"] for c in cells]),
        "precision": _mean([c["precision"] for c in cells]),
        "links_median": _q([c["links"] for c in cells], 0.5),
        "action_hit": _mean([c["action_hit"] for c in cells]),
        "action_base": sum(1 for c in cells if c["action_hit"] is not None),
    }


def fmt(v, pct=False) -> str:
    if v is None:
        return "-"
    return f"{v * 100:.0f}%" if pct else f"{v}"


def report(rows: List[Dict], out_path: str) -> Dict:
    groups = ["all"] + sorted({r["group"] for r in rows})
    summary = {g: {s: aggregate([r for r in rows if g == "all" or r["group"] == g], s)
                   for s in SYSTEMS} for g in groups}
    print(f"\n{'Gruppe':7} {'System':8} {'ok':>5} {'lat50':>6} {'lat95':>6} {'Titel':>6} "
          f"{'T=':>5} {'Recall':>7} {'Prec':>6} {'Links':>6} {'Aktion':>7}")
    for g in groups:
        for s in SYSTEMS:
            a = summary[g][s]
            print(f"{g:7} {s:8} {fmt(a['ok_rate'], 1):>5} {fmt(a['latency_median']):>6} "
                  f"{fmt(a['latency_p95']):>6} {fmt(a['title_present'], 1):>6} {fmt(a['title_equal'], 1):>5} "
                  f"{fmt(a['recall'], 1):>7} {fmt(a['precision'], 1):>6} {fmt(a['links_median']):>6} "
                  f"{fmt(a['action_hit'], 1):>7}")
    print(f"\n{'ID':4} {'orig':>11} {'jina R/P':>11} {'extr R/P':>11}  Hinweis  URL")
    for r in rows:
        o, j, e = r["orig"], r["jina"], r["extract"]
        print(f"{r['id']:4} {str(o['status']) + '/' + str(o['chars']):>11} "
              f"{fmt(j['recall'], 1) + '/' + fmt(j['precision'], 1):>11} "
              f"{fmt(e['recall'], 1) + '/' + fmt(e['precision'], 1):>11}  "
              f"{j.get('hint') or e.get('hint') or '':30.30} {r['url'][:60]}")
    worst = sorted(rows, key=lambda r: ((r["jina"]["recall"] if r["jina"]["recall"] is not None else 2)
                                        + (r["extract"]["recall"] if r["extract"]["recall"] is not None else 2)))
    worst5 = [{"id": r["id"], "url": r["url"], "jina_recall": r["jina"]["recall"],
               "extract_recall": r["extract"]["recall"],
               "hint": r["jina"].get("hint") or r["extract"].get("hint")} for r in worst[:5]]
    print("\nSchlechteste 5 fuer uns:")
    for w in worst5:
        print(f"  {w['id']} jina {fmt(w['jina_recall'], 1)} extract {fmt(w['extract_recall'], 1)}  "
              f"{w['hint']}  {w['url']}")
    out = {"created": dt.datetime.now().isoformat(timespec="seconds"), "summary": summary,
           "worst5": worst5, "rows": rows}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"\nJSON: {os.path.relpath(out_path, ROOT)}")
    return out


# ---------------------------------------------------------------- Ablauf


def run(base: str, only: Optional[set], tag: str = "") -> str:
    today = dt.date.today().isoformat() + (f"-{tag}" if tag else "")
    raw_path = os.path.join(PRIVATE, f"reader-raw-{today}.json")
    raw: Dict = {}
    if os.path.exists(raw_path):
        with open(raw_path, encoding="utf-8") as f:
            raw = json.load(f)
    with httpx.Client(follow_redirects=True) as client:
        for uid, _g, _l, url in URLS:
            if only and uid not in only:
                continue
            had = load_orig(url) is not None
            orig = fetch_orig(client, url)
            ex = fetch_own_extract(client, base, url)
            time.sleep(OWN_PAUSE_S)
            jn = fetch_own_jina(client, base, url)
            time.sleep(OWN_PAUSE_S)
            raw[url] = {"extract": ex, "jina": jn}
            with open(raw_path, "w", encoding="utf-8") as f:
                json.dump(raw, f, ensure_ascii=False)
            print(f"{uid} orig {orig['status']}{' (cache)' if had else ''} {orig['latency']}s | "
                  f"extract {ex['status']} {ex['latency']}s | jina {jn['status']} {jn['latency']}s  {url}",
                  flush=True)
    return raw_path


def latest_raw() -> str:
    found = sorted(glob.glob(os.path.join(PRIVATE, "reader-raw-*.json")))
    if not found:
        sys.exit("Keine reader-raw-*.json gefunden, erst 'run' ausfuehren.")
    return found[-1]


def score(raw_path: str) -> Dict:
    with open(raw_path, encoding="utf-8") as f:
        raw = json.load(f)
    rows = [score_url(uid, g, l, url, raw.get(url, {})) for uid, g, l, url in URLS]
    date = re.search(r"reader-raw-(.+)\.json$", raw_path)
    tag = date.group(1) if date else dt.date.today().isoformat()
    return report(rows, os.path.join(PRIVATE, f"reader-score-{tag}.json"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=("run", "score"))
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--only", default="", help="kommagetrennte IDs, z. B. g01,w03")
    ap.add_argument("--raw", default="", help="score: bestimmte reader-raw-*.json")
    ap.add_argument("--tag", default="", help="run: Zusatz im Dateinamen (reader-raw-<datum>-<tag>.json), "
                    "damit Laeufe gegen andere Instanzen die Baseline nicht ueberschreiben")
    args = ap.parse_args()
    if args.mode == "run":
        only = {x.strip() for x in args.only.split(",") if x.strip()} or None
        raw_path = run(args.base.rstrip("/"), only, args.tag)
    else:
        raw_path = args.raw or latest_raw()
    score(raw_path)


if __name__ == "__main__":
    main()
