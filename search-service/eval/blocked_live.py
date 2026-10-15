#!/usr/bin/env python3
"""Sperrquote am Live-Stand: alle Trefferseiten der Messung vom 14.10.2026 (856 Seiten aus
echten Suchen, Recherche-Kassette + 50 Faktenfragen) durch den laufenden Reader
(/jina/r, alle Lade-Stufen inkl. Camoufox). Lesbar = 200, mindestens 80 Woerter, keine
Bot-Pruefseite. Nicht lesbare Seiten behalten die Einstufung vom 14.10. (404, kein HTML,
Netz-Timeout zaehlen nicht als gesperrt).

  blocked_live.py --src eval/private/camoufox-2026-10-15/botmess-2026-10-14.json --out ...
"""
import argparse
import json
import re
import time
import urllib.parse
import urllib.request
from collections import Counter

CHALLENGE = re.compile(r"just a moment|verify(ing)? you are human|captcha|access denied|are you a robot|"
                       r"enable javascript and cookies|unusual traffic", re.I)


def read(base, url):
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(f"{base}/jina/r/{url}", timeout=90) as r:
            body, code = r.read().decode("utf-8", "replace"), r.status
    except Exception as e:  # noqa: BLE001
        body, code = "", getattr(e, "code", 0)
    words = len(body.split())
    ok = code == 200 and words >= 80 and not (words < 300 and CHALLENGE.search(body))
    return {"code": code, "words": words, "ok": ok, "sec": round(time.perf_counter() - t0, 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument("--pause", type=float, default=0.3)
    a = ap.parse_args()
    src = json.load(open(a.src))
    rows = {}
    for i, (url, old) in enumerate(src.items(), 1):
        r = read(a.base, url)
        r["cat_14_10"] = old["cat"]
        rows[url] = r
        if i % 50 == 0:
            print(i, sum(x["ok"] for x in rows.values()), flush=True)
            json.dump(rows, open(a.out, "w"), indent=1)
        time.sleep(a.pause)
    json.dump(rows, open(a.out, "w"), indent=1)
    bad = {u: r for u, r in rows.items() if not r["ok"]}
    print("lesbar", len(rows) - len(bad), "von", len(rows))
    print("nicht lesbar nach Einstufung 14.10.:", Counter(r["cat_14_10"] for r in bad.values()))


if __name__ == "__main__":
    main()
