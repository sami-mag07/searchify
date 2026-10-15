#!/usr/bin/env python3
"""Quellen-Ledger und Verify-Gate für den research-agent (nur stdlib).

Zweck: jede Behauptung im Report bekommt eine Nummer [sNN] und ein wörtliches
Zitat, das nachweislich im Quelltext steht. Vor "fertig" prüft `verify`, ob
jede zitierte Nummer wirklich einen belegten Beleg hat.

Befehle:
  add <url> [--ledger sources.json]
      Legt die URL an (idempotent) und gibt ihre Nummer als [sNN] aus.
  quote <id> --from <seite.txt> --text "<zitat>" [--ledger ...]
      Prüft, ob <zitat> wörtlich in <seite.txt> steht (Whitespace normalisiert),
      und hängt es an die Quelle. Exit != 0, wenn es nicht wörtlich vorkommt.
      <id> darf nackt (1) oder in Klammern ([s01]) übergeben werden.
  verify <entwurf.md> [--min-coverage 1.0] [--check-urls] [--ledger ...]
      Sammelt alle [sNN] im Entwurf. Deckung = belegte / zitierte. Default
      verlangt volle Deckung (1.0); mit --min-coverage bewusst lockern. Exit
      != 0 unter der Schwelle. --check-urls holt jede URL (2xx) und prüft ein
      gespeichertes Zitat live erneut, sonst gilt die Nummer als offen.
      --numbers prüft zusätzlich jeden Satz: jede Zahl darin muss in einem
      Zitat der Quellen stehen, die dieser Satz zitiert. Fängt geänderte
      Zahlen und vertauschte Zitate, die der reine Zitat-Check durchlässt.
  list [--ledger ...]
      Zeigt das Ledger.

Keys/Secrets kommen NICHT hierher. Netzzugriff nur mit --check-urls.
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error

CITE_RE = re.compile(r"\[s0*(\d+)\]")
NUM_RE = re.compile(r"\d+(?:[.,]\d+)*")
SENT_RE = re.compile(r"(?<=[.!?])\s+|\n+")
_WS = re.compile(r"\s+")
_TRANS = {ord("“"): '"', ord("”"): '"', ord("„"): '"', ord("’"): "'",
          ord("‘"): "'", ord("—"): "-", ord("–"): "-", ord(" "): " "}


def norm(text):
    """Whitespace zusammenziehen und typografische Zeichen vereinheitlichen."""
    return _WS.sub(" ", text.translate(_TRANS)).strip()


def parse_id(raw):
    """Nimmt 1 oder [s01] und gibt die kanonische Ledger-Nummer '1' zurück."""
    digits = re.sub(r"\D", "", str(raw))
    if not digits:
        sys.exit(f"FEHLER: '{raw}' ist keine Quellen-Nummer.")
    return str(int(digits))


def load(path):
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return {"sources": {}, "url_index": {}}


def save(path, data):
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def cmd_add(args):
    data = load(args.ledger)
    url = args.url.strip()
    if url in data["url_index"]:
        sid = data["url_index"][url]
    else:
        sid = str(len(data["sources"]) + 1)
        data["sources"][sid] = {"url": url, "quotes": [], "added": int(time.time())}
        data["url_index"][url] = sid
        save(args.ledger, data)
    print(f"[s{int(sid):02d}]")


def cmd_quote(args):
    data = load(args.ledger)
    sid = parse_id(args.id)
    if sid not in data["sources"]:
        sys.exit(f"FEHLER: Quelle [s{int(sid):02d}] gibt es nicht. Erst `add`.")
    with open(args.from_file, encoding="utf-8") as fh:
        page = norm(fh.read())
    quote = norm(args.text)
    if not quote:
        sys.exit("FEHLER: leeres Zitat.")
    if quote not in page:
        sys.exit(f"FEHLER: Zitat steht nicht wörtlich in {args.from_file}. "
                 "Behauptung als [unverifiziert] markieren oder streichen.")
    if quote not in data["sources"][sid]["quotes"]:
        data["sources"][sid]["quotes"].append(quote)
        save(args.ledger, data)
    print(f"OK: belegt für [s{int(sid):02d}].")


def _fetch_ok(url, want_quote=None, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "research-agent/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if not (200 <= resp.status < 300):
                return False, f"HTTP {resp.status}"
            if want_quote:
                body = norm(resp.read(2_000_000).decode("utf-8", "replace"))
                if want_quote not in body:
                    return False, "Zitat nicht mehr auf der Seite"
            return True, "2xx"
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, ValueError) as exc:
        return False, str(exc)


def number_mismatches(draft, data):
    """Sätze, deren Zahlen in keinem Zitat der dort zitierten Quellen stehen."""
    out = []
    for sent in SENT_RE.split(draft):
        cites = {str(int(n)) for n in CITE_RE.findall(sent)}
        if not cites:
            continue
        quoted = set()
        for sid in cites:
            for q in data["sources"].get(sid, {}).get("quotes", []):
                quoted.update(NUM_RE.findall(q))
        missing = [n for n in NUM_RE.findall(CITE_RE.sub("", sent)) if n not in quoted]
        if missing:
            out.append(f"Zahl {', '.join(missing)} steht in keinem Zitat von "
                       f"{' '.join(f'[s{int(c):02d}]' for c in sorted(cites, key=int))}: "
                       f"{sent.strip()[:90]}")
    return out


def cmd_verify(args):
    data = load(args.ledger)
    with open(args.draft, encoding="utf-8") as fh:
        draft = fh.read()
    cited = sorted({int(m) for m in CITE_RE.findall(draft)})
    if not cited:
        sys.exit("FEHLER: keine [sNN]-Zitate im Entwurf gefunden.")
    proven, reasons = [], []
    for n in cited:
        src = data["sources"].get(str(n))
        if not src or not src["quotes"]:
            reasons.append(f"[s{n:02d}] kein belegtes Zitat")
            continue
        if args.check_urls:
            ok, why = _fetch_ok(src["url"], want_quote=src["quotes"][0])
            if not ok:
                reasons.append(f"[s{n:02d}] URL/Zitat-Check: {why}")
                continue
        proven.append(n)
    if args.numbers:
        mismatches = number_mismatches(draft, data)
        reasons += mismatches
        if mismatches:
            proven = []
    coverage = len(proven) / len(cited)
    print(f"Zitiert: {len(cited)}  Belegt: {len(proven)}  Deckung: {coverage:.0%}"
          f"  (Schwelle {args.min_coverage:.0%})")
    for msg in reasons:
        print("  OFFEN:", msg)
    if coverage < args.min_coverage:
        sys.exit(f"GATE ROT: Deckung {coverage:.0%} unter Schwelle "
                 f"{args.min_coverage:.0%}. Report noch nicht 'fertig'.")
    print("GATE GRÜN.")


def cmd_list(args):
    data = load(args.ledger)
    for sid, src in sorted(data["sources"].items(), key=lambda kv: int(kv[0])):
        print(f"[s{int(sid):02d}] {src['url']}  ({len(src['quotes'])} Zitate)")


def main():
    ap = argparse.ArgumentParser(description="Quellen-Ledger und Verify-Gate")
    ap.add_argument("--ledger", default="sources.json")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add"); a.add_argument("url"); a.set_defaults(fn=cmd_add)
    q = sub.add_parser("quote")
    q.add_argument("id"); q.add_argument("--from", dest="from_file", required=True)
    q.add_argument("--text", required=True); q.set_defaults(fn=cmd_quote)
    v = sub.add_parser("verify")
    v.add_argument("draft"); v.add_argument("--min-coverage", type=float, default=1.0)
    v.add_argument("--check-urls", action="store_true")
    v.add_argument("--numbers", action="store_true"); v.set_defaults(fn=cmd_verify)
    l = sub.add_parser("list"); l.set_defaults(fn=cmd_list)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
