#!/usr/bin/env python3
"""Latenz je nachgebautem Endpunkt (3 Laeufe je Endpunkt, verschiedene Anfragen,
damit der Cache nicht greift). Ergebnis: eval/private/endpoints-<datum>.json"""

from __future__ import annotations

import datetime
import json
import statistics
import time
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
B = "http://127.0.0.1:8000"
q = urllib.parse.quote

CASES = [
    ("Tavily Search", "POST", "/search", [{"query": x, "include_answer": False, "max_results": 5} for x in
        ("Wie lang ist die Elbe?", "Who invented the telephone?", "¿Cuál es el río más largo de España?")]),
    ("Tavily Extract", "POST", "/extract", [{"urls": [u]} for u in
        ("https://de.wikipedia.org/wiki/Elbe", "https://en.wikipedia.org/wiki/Telephone", "https://es.wikipedia.org/wiki/Tajo")]),
    ("Exa Search", "POST", "/exa/search", [{"query": x, "numResults": 5, "contents": {"highlights": True}} for x in
        ("Schülerwettbewerb Informatik 2027", "open source vector database comparison", "programa de becas Erasmus 2027")]),
    ("Exa Contents", "POST", "/exa/contents", [{"urls": [u], "text": True} for u in
        ("https://fr.wikipedia.org/wiki/Loire", "https://de.wikipedia.org/wiki/Rhein", "https://en.wikipedia.org/wiki/Danube")]),
    ("Serper Search", "POST", "/serper/search", [{"q": x} for x in
        ("Hauptstadt von Bayern", "largest desert in the world", "plus haute montagne de France")]),
    ("Serper News", "POST", "/serper/news", [{"q": x} for x in
        ("Bundestag", "climate summit", "élections municipales")]),
    ("Jina Reader", "GET", None, ["/jina/r/" + u for u in
        ("https://de.wikipedia.org/wiki/Main_(Fluss)", "https://en.wikipedia.org/wiki/Thames", "https://fr.wikipedia.org/wiki/Garonne")]),
    ("Jina Search", "GET", None, ["/jina/s/" + q(x) for x in
        ("Wie hoch ist der Brocken?", "What is the speed of sound?", "¿Quién escribió Don Quijote?")]),
]


def call(method, path, body):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(B + path, data, {"content-type": "application/json"}, method=method)
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            r.read()
            ok = r.status == 200
    except Exception:  # noqa: BLE001
        ok = False
    return time.perf_counter() - t, ok


def main() -> None:
    rows = []
    for name, method, path, items in CASES:
        lats, oks = [], 0
        for item in items:
            p, body = (path, item) if path else (item, None)
            lat, ok = call(method, p, body)
            lats.append(lat)
            oks += ok
            time.sleep(1)
        rows.append({"endpoint": name, "median_s": round(statistics.median(lats), 3), "max_s": round(max(lats), 3),
                     "ok": oks, "n": len(items)})
        print(rows[-1])
    out = HERE / "private" / f"endpoints-{datetime.date.today()}.json"
    out.write_text(json.dumps(rows, indent=1), encoding="utf-8")
    print(out)


if __name__ == "__main__":
    main()
