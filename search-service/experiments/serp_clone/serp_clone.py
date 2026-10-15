"""serp_clone: Serper-kompatibles JSON aus dem lokalen SearXNG.

Liefert fuer POST /search dieselbe Form wie google.serper.dev/search
(searchParameters, knowledgeGraph, answerBox, organic, relatedSearches,
credits), damit Code, der Serper erwartet, ohne Umbau gegen eine lokale
Quelle laufen kann. Die Treffer kommen aus Bing/Yahoo/Wikipedia ueber
SearXNG, NICHT von Google: Google wird bewusst nicht abgefragt
(Begruendung in research/14-serper-reverse.md).

Spec nur aus oeffentlicher Serper-Doku und Integrationen, kein Probing.
peopleAlsoAsk bleibt leer, weil Bing/Yahoo es ueber SearXNG nicht liefern.

Nutzung:
    .venv/bin/python experiments/serp_clone/serp_clone.py "frage" [--hl de --gl de]
    .venv/bin/python experiments/serp_clone/serp_clone.py --serve [--port 8899]
    curl -s localhost:8899/search -H 'Content-Type: application/json' -d '{"q":"x"}'

Umgebung: SEARXNG_URL (Default http://localhost:8888),
SERP_CLONE_MIN_INTERVAL_S (Pause zwischen zwei Upstream-Anfragen, Default 2),
SERP_CLONE_KEY (optional; gesetzt, dann wird X-API-KEY verlangt).
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import threading
import time
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, List, Optional
from urllib.parse import urlparse

import httpx

SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://localhost:8888").rstrip("/")
MIN_INTERVAL_S = float(os.environ.get("SERP_CLONE_MIN_INTERVAL_S", "2"))
CACHE_SIZE = 256
TBS_TO_RANGE = {"qdr:d": "day", "qdr:w": "week", "qdr:m": "month", "qdr:y": "year"}
MAX_NUM = 100


class BadRequest(ValueError):
    pass


class _Throttle:
    """Mindestabstand zwischen Upstream-Anfragen, prozessweit."""

    def __init__(self, interval: float) -> None:
        self.interval = interval
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self.interval
        if delay > 0:
            time.sleep(delay)


_throttle = _Throttle(MIN_INTERVAL_S)
_cache: "OrderedDict[str, Dict]" = OrderedDict()
_cache_lock = threading.Lock()


def _params(body: Dict) -> Dict:
    q = str(body.get("q") or "").strip()
    if not q:
        raise BadRequest("Missing query parameter q")
    try:
        num = int(body.get("num", 10))
        page = int(body.get("page", 1))
    except (TypeError, ValueError) as exc:
        raise BadRequest("num and page must be integers") from exc
    if not 1 <= num <= MAX_NUM or page < 1:
        raise BadRequest(f"num must be 1..{MAX_NUM}, page >= 1")
    tbs = body.get("tbs")
    if tbs and tbs not in TBS_TO_RANGE:
        raise BadRequest("tbs supports qdr:d, qdr:w, qdr:m, qdr:y")
    return {
        "q": q,
        "gl": str(body.get("gl") or "us").lower(),
        "hl": str(body.get("hl") or "en").lower(),
        "num": num,
        "page": page,
        "tbs": tbs,
    }


def _upstream(p: Dict, pageno: int) -> Dict:
    query = {
        "q": p["q"],
        "format": "json",
        "engines": "bing,yahoo,wikipedia",
        "language": f"{p['hl']}-{p['gl'].upper()}",
        "pageno": pageno,
    }
    if p["tbs"]:
        query["time_range"] = TBS_TO_RANGE[p["tbs"]]
    _throttle.wait()
    resp = httpx.get(f"{SEARXNG_URL}/search", params=query, timeout=8)
    resp.raise_for_status()
    return resp.json()


def _knowledge_graph(infoboxes: List[Dict]) -> Optional[Dict]:
    if not infoboxes:
        return None
    ib = infoboxes[0]
    link = ib.get("id") or next((u.get("url") for u in ib.get("urls") or []), "")
    kg = {
        "title": ib.get("infobox") or "",
        "description": ib.get("content") or "",
        "descriptionSource": "Wikipedia",
        "descriptionLink": link,
    }
    if ib.get("img_src"):
        kg["imageUrl"] = ib["img_src"]
    attrs = {a.get("label"): a.get("value") for a in ib.get("attributes") or [] if a.get("label")}
    if attrs:
        kg["attributes"] = attrs
    return kg


def _organic(results: List[Dict], offset: int) -> List[Dict]:
    out = []
    for r in results:
        if not r.get("url"):
            continue
        item = {
            "title": (r.get("title") or "").strip(),
            "link": r["url"],
            "snippet": (r.get("content") or "").strip(),
            "position": offset + len(out) + 1,
        }
        if r.get("publishedDate"):
            item["date"] = str(r["publishedDate"])[:10]
        out.append(item)
    return out


def search(body: Dict) -> Dict:
    p = _params(body)
    key = json.dumps(p, sort_keys=True)
    with _cache_lock:
        if key in _cache:
            _cache.move_to_end(key)
            return _cache[key]

    # SearXNG liefert ~10 bis 20 Treffer je Seite; fuer num>10 Folgeseiten holen.
    offset = (p["page"] - 1) * p["num"]
    pageno = offset // 10 + 1
    data = _upstream(p, pageno)
    results = list(data.get("results") or [])
    skip = offset - (pageno - 1) * 10
    while len(results) < skip + p["num"] and pageno < 10:
        pageno += 1
        more = _upstream(p, pageno).get("results") or []
        if not more:
            break
        results.extend(more)
    organic = _organic(results[skip:skip + p["num"]], offset)

    out: Dict = {
        "searchParameters": {
            "q": p["q"], "gl": p["gl"], "hl": p["hl"], "type": "search",
            "num": p["num"], "page": p["page"], "engine": "serp-clone",
        }
    }
    if p["tbs"]:
        out["searchParameters"]["tbs"] = p["tbs"]
    kg = _knowledge_graph(data.get("infoboxes") or [])
    if kg:
        out["knowledgeGraph"] = kg
    answers = data.get("answers") or []
    if answers:
        a = answers[0]
        text = a.get("answer") if isinstance(a, dict) else str(a)
        out["answerBox"] = {"answer": text}
        if isinstance(a, dict) and a.get("url"):
            out["answerBox"]["link"] = a["url"]
    out["organic"] = organic
    out["peopleAlsoAsk"] = []
    out["relatedSearches"] = [{"query": s} for s in (data.get("suggestions") or [])[:8]]
    out["credits"] = 1

    with _cache_lock:
        _cache[key] = out
        if len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)
    return out


class _Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, payload) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self) -> None:  # noqa: N802 (http.server-API)
        if urlparse(self.path).path != "/search":
            self._send(404, {"message": "Only /search is implemented", "statusCode": 404})
            return
        required = os.environ.get("SERP_CLONE_KEY", "")
        given = self.headers.get("X-API-KEY", "")
        if required and not hmac.compare_digest(given, required):
            self._send(403, {"message": "Unauthorized.", "statusCode": 403})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            # Serper nimmt auch ein Array von Anfragen (Batch) und antwortet mit einem Array.
            if isinstance(body, list):
                self._send(200, [search(b) for b in body])
            elif isinstance(body, dict):
                self._send(200, search(body))
            else:
                raise BadRequest("Body must be an object or an array")
        except (BadRequest, json.JSONDecodeError) as exc:
            self._send(400, {"message": str(exc), "statusCode": 400})
        except httpx.HTTPError as exc:
            self._send(502, {"message": f"Upstream error: {type(exc).__name__}", "statusCode": 502})

    def log_message(self, fmt: str, *args) -> None:
        # Keine Queries ins Log: nur Status und Pfad.
        if args and str(args[0]).startswith("POST"):
            return
        super().log_message(fmt, *args)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("q", nargs="?")
    ap.add_argument("--gl", default="us")
    ap.add_argument("--hl", default="en")
    ap.add_argument("--num", type=int, default=10)
    ap.add_argument("--page", type=int, default=1)
    ap.add_argument("--tbs")
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--port", type=int, default=8899)
    args = ap.parse_args()
    if args.serve:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), _Handler)
        print(f"serp_clone auf http://127.0.0.1:{args.port}/search")
        server.serve_forever()
        return
    if not args.q:
        ap.error("Query fehlt (oder --serve)")
    t0 = time.perf_counter()
    out = search({"q": args.q, "gl": args.gl, "hl": args.hl, "num": args.num,
                  "page": args.page, "tbs": args.tbs})
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"# {time.perf_counter() - t0:.2f} s", flush=True)


if __name__ == "__main__":
    main()
