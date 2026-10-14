#!/usr/bin/env python3
"""SearXNG-Ausfall messen: eigene Instanz mit totem SEARXNG_URL, nur ddgs-Rueckfall.

Zwei Teile:

  serve  startet die App auf --port (Default 8007) mit SEARXNG_URL auf einem toten
         Port, ohne Caches, und legt eine Huelle um das ddgs-Backend:
         - hoechstens ein ddgs-Aufruf pro --ddgs-gap Sekunden (DuckDuckGo schonen),
         - jede ddgs-Antwort als JSON-Zeile in --trace (Dauer, Treffer, Fehlertext
           inkl. Fehler der einzelnen ddgs-Engines). Code in app/ bleibt unveraendert.
  run    schickt die Anfragen aus eval/reliability_bench.build_queries reihum an
         /search, /serper/search, /exa/search (sequenziell, Start-Abstand --gap),
         ordnet die ddgs-Aufrufe aus dem Trace jeder Anfrage zu und wiederholt
         Anfragen, bei denen das Netz weg war (Hotspot), statt sie zu zaehlen.

  .venv/bin/python eval/searxng_down_bench.py serve --port 8007 --trace /tmp/x/ddgs.jsonl
  .venv/bin/python eval/searxng_down_bench.py run --base http://127.0.0.1:8007 \
      --trace /tmp/x/ddgs.jsonl --limit 90 --out eval/private/searxng-down.json

Nie Tavily/Serper/Exa: SEARCH_FALLBACK muss ddgs sein (serve prueft das).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import socket
import statistics
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))


# --------------------------------------------------------------------------- serve

def _load_env(path: Path) -> None:
    """.env als Daten lesen (wie deploy/run-local.sh), vorhandene Umgebung gewinnt nicht."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.rstrip("\r")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key.isidentifier():
            os.environ[key] = value


class _EngineErrors(logging.Handler):
    """Sammelt 'Error in engine X: ...' der ddgs-Bibliothek je Thread-Aufruf."""

    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.local = threading.local()

    def emit(self, record: logging.LogRecord) -> None:
        bucket = getattr(self.local, "bucket", None)
        msg = record.getMessage()
        if bucket is not None and msg.startswith("Error in engine"):
            bucket.append(msg[:200])


def serve(a: argparse.Namespace) -> None:
    _load_env(ROOT / ".env")
    os.environ["SEARXNG_URL"] = a.dead_url
    os.environ["SEARCH_CACHE_TTL_S"] = "0"
    os.environ["PAGE_CACHE_TTL_S"] = "0"
    if os.environ.get("SEARCH_FALLBACK", "").strip().lower() != "ddgs":
        sys.exit("SEARCH_FALLBACK ist nicht ddgs - Abbruch (nie bezahlte Backends).")
    os.environ["SEARCH_BACKEND"] = "searxng"

    from app import search as search_mod  # noqa: PLC0415

    handler = _EngineErrors()
    ddgs_log = logging.getLogger("ddgs")
    ddgs_log.setLevel(logging.INFO)
    ddgs_log.addHandler(handler)
    ddgs_log.propagate = False

    original = search_mod._BACKENDS["ddgs"]
    gate = threading.Lock()
    last = [0.0]
    trace = open(a.trace, "a", encoding="utf-8")  # noqa: SIM115
    trace_lock = threading.Lock()

    def paced_ddgs(query, max_candidates, **kwargs):
        with gate:  # globaler Abstand zwischen ddgs-Starts
            wait = last[0] + a.ddgs_gap - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            last[0] = time.monotonic()
        handler.local.bucket = []
        t0 = time.time()
        rec: Dict = {"t0": t0, "q": query[:80], "max": max_candidates,
                     "topic": kwargs.get("topic"), "lang": kwargs.get("language"),
                     "paced_s": round(max(0.0, wait), 3)}
        try:
            hits = original(query, max_candidates, **kwargs)
            rec.update(ok=True, n=len(hits))
            return hits
        except search_mod.SearchBackendError as exc:
            cause = exc.__cause__
            rec.update(ok=False, n=0, err=str(exc)[:120],
                       cause=(type(cause).__name__ + ": " + str(cause)[:200]) if cause else None)
            raise
        finally:
            rec["dur"] = round(time.time() - t0, 3)
            rec["engine_errors"] = handler.local.bucket
            handler.local.bucket = None
            with trace_lock:
                trace.write(json.dumps(rec, ensure_ascii=False) + "\n")
                trace.flush()

    search_mod._BACKENDS["ddgs"] = paced_ddgs

    import uvicorn  # noqa: PLC0415
    from app.main import app  # noqa: PLC0415

    uvicorn.run(app, host="127.0.0.1", port=a.port, access_log=False, log_level="warning")


# --------------------------------------------------------------------------- run

def _online(timeout: float = 3.0) -> bool:
    """TCP-Probe ohne DuckDuckGo zu belasten (Hotspot-Abbrueche erkennen)."""
    for host in (("1.1.1.1", 443), ("8.8.8.8", 53)):
        try:
            with socket.create_connection(host, timeout=timeout):
                return True
        except OSError:
            continue
    return False


def _trace_since(path: Path, offset: int):
    with open(path, encoding="utf-8") as fh:
        fh.seek(offset)
        data = fh.read()
        end = fh.tell()
    return [json.loads(x) for x in data.splitlines() if x.strip()], end


def _pct(xs: List[float], p: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = min(len(xs) - 1, max(0, int(round(p * (len(xs) - 1)))))
    return round(xs[k], 3)


def _cause_class(call: Dict) -> str:
    if call.get("ok"):
        return "ok" if call.get("n") else "ok_empty"
    text = (call.get("cause") or "") + " " + " ".join(call.get("engine_errors") or [])
    low = text.lower()
    if "ratelimit" in low or "202" in low or "429" in low or "captcha" in low:
        return "ratelimit"
    if "timed out" in low or "timeout" in low:
        return "timeout"
    if "no results found" in low and not call.get("engine_errors"):
        return "no_results"
    return "engine_errors"


def summarize(rows: List[Dict]) -> Dict:
    out: Dict = {}
    for ep in ["search", "serper", "exa", "all"]:
        rs = [r for r in rows if ep == "all" or r["endpoint"] == ep]
        if not rs:
            continue
        n = len(rs)
        err = sum(1 for r in rs if r["status"] is None or r["status"] >= 500)
        empty = sum(1 for r in rs if r["status"] == 200 and r["n"] == 0)
        lats = [r["lat"] for r in rs]
        out[ep] = {
            "n": n, "errors": err, "error_share": round(err / n, 3),
            "empty": empty, "empty_share": round(empty / n, 3),
            "fail_or_empty": err + empty, "fail_or_empty_share": round((err + empty) / n, 3),
            "http4xx": sum(1 for r in rs if r["status"] and 400 <= r["status"] < 500),
            "p50": _pct(lats, 0.5), "p95": _pct(lats, 0.95), "max": round(max(lats), 3),
            "mean": round(statistics.mean(lats), 3),
        }
    return out


def run(a: argparse.Namespace) -> None:
    from reliability_bench import _call, _items, build_queries  # noqa: PLC0415

    trace = Path(a.trace)
    trace.touch()
    offset = trace.stat().st_size
    queries = build_queries(a.limit)
    endpoints = ["search", "serper", "exa"]
    rows: List[Dict] = []
    net_drops = 0
    last = [0.0]

    def pace() -> None:
        wait = last[0] + a.gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        last[0] = time.monotonic()

    for i, (kind, q) in enumerate(queries):
        ep = endpoints[i % len(endpoints)]
        for attempt in range(4):
            pace()
            if ep == "search":
                rec = _call(a.base, "POST", "/search", {"query": q, "max_results": 5}, a.timeout)
            elif ep == "serper":
                rec = _call(a.base, "POST", "/serper/search", {"q": q}, a.timeout)
            else:
                rec = _call(a.base, "POST", "/exa/search",
                            {"query": q, "numResults": 5, "contents": {"text": {"maxCharacters": 800}}},
                            a.timeout)
            items = _items(ep, rec.pop("json"))
            rec.pop("text", None)
            calls, offset = _trace_since(trace, offset)
            failed = rec["status"] is None or rec["status"] >= 500 or (rec["status"] == 200 and not items)
            if failed and not _online():
                net_drops += 1
                print(f"    Netz weg bei #{i}, warte und wiederhole", flush=True)
                for _ in range(60):
                    time.sleep(5)
                    if _online():
                        break
                continue
            break
        row = {
            "i": i, "endpoint": ep, "kind": kind, "query": q[:120], "status": rec["status"],
            "timeout": rec["timeout"], "error": rec["error"], "lat": round(rec["lat"], 3),
            "n": len(items), "attempts": attempt + 1,
            "ddgs_calls": [{k: c.get(k) for k in ("ok", "n", "dur", "cause", "engine_errors", "paced_s", "max")}
                           | {"class": _cause_class(c)} for c in calls],
        }
        rows.append(row)
        print(f"{i:3d} {ep:6s} {kind:8s} {rec['status']} {rec['lat']:.2f}s n={len(items)} "
              f"ddgs={[x['class'] for x in row['ddgs_calls']]}", flush=True)

    calls = [c for r in rows for c in r["ddgs_calls"]]
    classes: Dict[str, int] = {}
    for c in calls:
        classes[c["class"]] = classes.get(c["class"], 0) + 1
    engine_err: Dict[str, int] = {}
    for c in calls:
        for e in c.get("engine_errors") or []:
            name = e.split(":")[0].replace("Error in engine ", "")
            engine_err[name] = engine_err.get(name, 0) + 1
    res = {
        "base": a.base, "limit": a.limit, "gap": a.gap, "net_drops_retried": net_drops,
        "summary": summarize(rows),
        "summary_first45": summarize([r for r in rows if r["i"] < 45]),
        "ddgs": {"calls": len(calls), "classes": classes, "engine_errors": engine_err,
                 "dur_p50": _pct([c["dur"] for c in calls], 0.5),
                 "dur_p95": _pct([c["dur"] for c in calls], 0.95)},
        "rows": rows,
    }
    Path(a.out).write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("summary", "summary_first45", "ddgs", "net_drops_retried")}, indent=1))


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=8007)
    s.add_argument("--dead-url", default="http://127.0.0.1:9")
    s.add_argument("--trace", required=True)
    s.add_argument("--ddgs-gap", type=float, default=1.0, help="Mindestabstand ddgs-Aufrufe (s)")
    r = sub.add_parser("run")
    r.add_argument("--base", default="http://127.0.0.1:8007")
    r.add_argument("--trace", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--limit", type=int, default=90)
    r.add_argument("--gap", type=float, default=1.5)
    r.add_argument("--timeout", type=float, default=30.0)
    a = ap.parse_args()
    if a.cmd == "serve":
        a.ddgs_gap = max(1.0, a.ddgs_gap)
        serve(a)
    else:
        a.gap = max(1.0, a.gap)
        run(a)


if __name__ == "__main__":
    main()
