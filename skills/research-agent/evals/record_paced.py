#!/usr/bin/env python3
"""Kassette nur mit kostenlosen Suchmaschinen aufnehmen, mit Pause zwischen Anfragen.

Wrapper um `search_eval.py record`: ein Worker, mindestens --pause Sekunden
zwischen zwei Anfragen (Upstream schonen). Bezahlte Engines (search.PAID) werden
verweigert, damit kein Credit verbraucht wird.

  record_paced.py --engines own,own-exa [--n 20] [--pause 1.0]
  search_eval.py score --cassette cassettes/<neu>.json
"""
import argparse
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import search_eval  # noqa: E402
from search_eval import search  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engines", default="own,own-exa")
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--pause", type=float, default=1.0)
    ap.add_argument("--field", default="queries")
    a = ap.parse_args()
    paid = [e for e in a.engines.split(",") if e in search.PAID]
    if paid:
        sys.exit(f"bezahlte Engines verweigert: {', '.join(paid)}")
    last = [0.0]

    def paced(fn):
        def run(q, o, keys):
            wait = last[0] + a.pause - time.time()
            if wait > 0:
                time.sleep(wait)
            try:
                return fn(q, o, keys)
            finally:
                last[0] = time.time()
        return run

    for e in a.engines.split(","):
        search.ENGINES[e] = paced(search.ENGINES[e])
    a.workers = 1
    search_eval.record(a)


if __name__ == "__main__":
    main()
