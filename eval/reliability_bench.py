#!/usr/bin/env python3
"""Zuverlaessigkeits-Bench gegen eine laufende Instanz (live, eigene Instanz!).

Erzeugt 150+ vielfaeltige Anfragen generisch aus den vorhandenen eval/*.jsonl
(Tippfehler, Gross/klein, Emoji, Sonderzeichen, Code, Zahlen, Sprachmix, sehr
kurz/lang, Unsinn ohne sinnvolle Treffer) und schickt sie reihum an /search,
/serper/search und /exa/search. URLs aus den Treffern gehen danach an /extract
und /jina/r (plus einige kaputte URLs fuer die Validierung).

Kennzahlen je Endpunkt: HTTP-Fehler (5xx / andere), Timeouts, leere Treffer,
Treffer ohne content, Duplikate (gleiche URL mehrfach), p50/p95/max Latenz.

Hoechstens eine Anfrage pro Sekunde (Start-Abstand --gap, Default 1.0 s).

  .venv/bin/python eval/reliability_bench.py --base http://127.0.0.1:8081 \
      --out eval/private/rel-vorher.json [--limit 160] [--urls 30]
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

HERE = Path(__file__).resolve().parent

_EMOJI = ["🔥", "🤔", "🚀", "✅", "😅", "🇩🇪", "📈"]
_NONSENSE = ["xqzvbnm kjhgfdsa poiuy", "asdfghjkl qwertz 0000", "zzzzzz yyyyyy xxxxxx",
             "flibbertigibbet quuxplonk 93847", "ñññ ççç ßßß"]
_CODE = ["python TypeError: 'NoneType' object is not subscriptable",
         "SELECT * FROM users WHERE id = 1; DROP TABLE users;--",
         "git rebase --onto main HEAD~3", "<script>alert(1)</script> xss",
         "fn main() { println!(\"hi\"); } rust borrow checker", "${jndi:ldap://x/a} log4j",
         "C:\\Windows\\System32\\drivers\\etc\\hosts", "regex ^(a+)+$ catastrophic backtracking"]
_NUMBERS = ["3.14159 * 2^10", "1e-9 Sekunden in Nanosekunden", "ISBN 978-3-16-148410-0",
            "+49 30 1234567 Vorwahl", "2026-10-13 Wochentag", "0x1F600 unicode", "42"]
_SHORT = ["a", "x", "?", "Berlin", "python", "AI", "€", "日本", "   ", "-", "Wort " * 700]
_FRESH = ["Nachrichten heute", "latest news today", "noticias de hoy", "actualités aujourd'hui",
          "Bundesliga Tabelle aktuell", "stock market today"]


def _typo(s: str, rng: random.Random) -> str:
    chars = list(s)
    for _ in range(max(1, len(chars) // 15)):
        i = rng.randrange(len(chars))
        op = rng.choice("dsw")
        if op == "d" and len(chars) > 3:
            del chars[i]
        elif op == "s" and i + 1 < len(chars):
            chars[i], chars[i + 1] = chars[i + 1], chars[i]
        else:
            chars.insert(i, chars[i])
    return "".join(chars)


def build_queries(n: int, seed: int = 7) -> List[Tuple[str, str]]:
    """(art, query) - generisch abgewandelt, deterministisch."""
    rng = random.Random(seed)
    base: List[str] = []
    for f in sorted(HERE.glob("*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                q = json.loads(line).get("query")
                if q:
                    base.append(q)
    rng.shuffle(base)
    out: List[Tuple[str, str]] = []
    out += [("short", s) for s in _SHORT]
    out += [("nonsense", s) for s in _NONSENSE]
    out += [("code", s) for s in _CODE]
    out += [("numbers", s) for s in _NUMBERS]
    out += [("fresh", s) for s in _FRESH]
    kinds = ["plain", "typo", "upper", "emoji", "special", "long", "mixed", "keywords", "quoted", "spaces"]
    i = 0
    while len(out) < n:
        q = base[i % len(base)]
        other = base[(i * 7 + 3) % len(base)]
        kind = kinds[i % len(kinds)]
        if kind == "typo":
            q = _typo(q, rng)
        elif kind == "upper":
            q = q.upper()
        elif kind == "emoji":
            q = f"{rng.choice(_EMOJI)} {q} {rng.choice(_EMOJI)}"
        elif kind == "special":
            q = q + ' "&<>%#\\/|~^`'
        elif kind == "long":
            q = " ".join([q, other, q.lower(), other.upper(), "bitte ausfuehrlich mit Quellen"] * 3)
        elif kind == "mixed":
            q = q.rstrip("?") + " " + other
        elif kind == "keywords":
            q = " ".join(w for w in q.strip("?¿").split() if len(w) > 4)[:60] or q
        elif kind == "quoted":
            q = f'"{q}"'
        elif kind == "spaces":
            q = "  \t" + q.replace(" ", "   ") + "\n  "
        out.append((kind, q))
        i += 1
    return out[:n]


def _call(base: str, method: str, path: str, body, timeout: float) -> Dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data, {"content-type": "application/json"}, method=method)
    t = time.perf_counter()
    rec: Dict = {"status": None, "timeout": False, "error": None, "json": None}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            rec["status"] = r.status
            try:
                rec["json"] = json.loads(raw)
            except ValueError:
                rec["text"] = raw.decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        rec["status"] = exc.code
        rec["error"] = exc.read()[:200].decode("utf-8", "replace")
    except (TimeoutError, OSError) as exc:  # socket.timeout ist TimeoutError
        if "timed out" in str(exc) or isinstance(exc, TimeoutError):
            rec["timeout"] = True
        rec["error"] = type(exc).__name__ + ": " + str(exc)[:120]
    rec["lat"] = time.perf_counter() - t
    return rec


def _items(endpoint: str, js) -> List[Tuple[str, str]]:
    """(url, content) je Treffer, endpunktunabhaengig."""
    if not isinstance(js, dict):
        return []
    if endpoint == "search":
        return [(r.get("url", ""), r.get("content") or "") for r in js.get("results") or []]
    if endpoint == "serper":
        return [(r.get("link", ""), r.get("snippet") or "") for r in js.get("organic") or []]
    if endpoint == "exa":
        return [(r.get("url", ""), r.get("text") or " ".join(r.get("highlights") or []))
                for r in js.get("results") or []]
    if endpoint == "extract":
        return [(r.get("url", ""), r.get("raw_content") or "") for r in js.get("results") or []]
    return []


def _norm(u: str) -> str:
    # Query bleibt (youtube.com/watch?v=A und ?v=B sind verschiedene Seiten).
    p = urllib.parse.urlsplit(u)
    return p.netloc.lower().removeprefix("www.") + p.path.rstrip("/") + "?" + p.query


def run(base: str, limit: int, n_urls: int, gap: float, timeout: float, only: Optional[str]) -> Dict:
    queries = build_queries(limit)
    rows: List[Dict] = []
    last = [0.0]

    def pace():
        wait = last[0] + gap - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        last[0] = time.monotonic()

    url_pool: List[str] = []
    endpoints = ["search", "serper", "exa"] if not only else [only]
    for i, (kind, q) in enumerate(queries):
        ep = endpoints[i % len(endpoints)]
        pace()
        if ep == "search":
            rec = _call(base, "POST", "/search", {"query": q, "max_results": 5}, timeout)
        elif ep == "serper":
            rec = _call(base, "POST", "/serper/search", {"q": q}, timeout)
        else:
            rec = _call(base, "POST", "/exa/search",
                        {"query": q, "numResults": 5, "contents": {"text": {"maxCharacters": 800}}}, timeout)
        items = _items(ep, rec.pop("json"))
        for u, _c in items[:2]:
            if u and u not in url_pool:
                url_pool.append(u)
        rows.append(_row(ep, kind, q, rec, items))
        print(f"{i:3d} {ep:7s} {kind:8s} {rec['status']} {rec['lat']:.2f}s n={len(items)}", flush=True)

    # Reader/Extract: URLs aus Treffern + kaputte URLs (Validierung).
    rng = random.Random(11)
    rng.shuffle(url_pool)
    urls = url_pool[:n_urls]
    bad = ["not a url", "ftp://example.org/x", "http://127.0.0.1:1/x", "https://nonexistent.invalid/"]
    for j, u in enumerate(urls + bad):
        ep = "extract" if j % 2 == 0 else "jina"
        pace()
        kind = "bad" if u in bad else "hit"
        if ep == "extract":
            rec = _call(base, "POST", "/extract", {"urls": [u]}, timeout)
            items = _items(ep, rec.pop("json"))
        else:
            rec = _call(base, "GET", "/jina/r/" + u, None, timeout)
            js = rec.pop("json")
            text = rec.pop("text", "") or (json.dumps(js) if js else "")
            items = [(u, text)] if rec["status"] == 200 else []
        rows.append(_row(ep, kind, u, rec, items))
        print(f"    {ep:7s} {kind:4s} {rec['status']} {rec['lat']:.2f}s n={len(items)}", flush=True)
    return {"base": base, "rows": rows, "summary": summarize(rows)}


def _row(ep, kind, q, rec, items) -> Dict:
    urls = [_norm(u) for u, _ in items if u]
    return {
        "endpoint": ep, "kind": kind, "query": q, "status": rec["status"], "timeout": rec["timeout"],
        "error": rec["error"], "lat": round(rec["lat"], 3), "n": len(items),
        "no_content": sum(1 for _u, c in items if not (c or "").strip()),
        "dups": len(urls) - len(set(urls)),
    }


def _pct(xs: List[float], p: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = min(len(xs) - 1, max(0, int(round(p * (len(xs) - 1)))))
    return round(xs[k], 3)


def summarize(rows: List[Dict]) -> Dict:
    out: Dict = {}
    for ep in sorted({r["endpoint"] for r in rows}):
        rs = [r for r in rows if r["endpoint"] == ep]
        good = [r for r in rs if r["kind"] not in ("bad", "nonsense")]
        lats = [r["lat"] for r in rs]
        out[ep] = {
            "n": len(rs),
            "http5xx": sum(1 for r in rs if (r["status"] or 0) >= 500),
            "http4xx": sum(1 for r in rs if 400 <= (r["status"] or 0) < 500),
            "timeouts": sum(1 for r in rs if r["timeout"]),
            "conn_err": sum(1 for r in rs if r["status"] is None and not r["timeout"]),
            "empty": sum(1 for r in good if r["status"] == 200 and r["n"] == 0),
            "no_content": sum(r["no_content"] for r in rs),
            "dups": sum(r["dups"] for r in rs),
            "p50": _pct(lats, 0.5), "p95": _pct(lats, 0.95), "max": round(max(lats), 3) if lats else 0,
            "mean": round(statistics.mean(lats), 3) if lats else 0,
        }
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8081")
    ap.add_argument("--out", required=True)
    ap.add_argument("--limit", type=int, default=160)
    ap.add_argument("--urls", type=int, default=30)
    ap.add_argument("--gap", type=float, default=1.0, help="Mindestabstand der Anfragen (s), >= 1")
    ap.add_argument("--timeout", type=float, default=30.0)
    ap.add_argument("--only", choices=["search", "serper", "exa"], default=None)
    ap.add_argument("--list", action="store_true", help="nur die Anfragen zeigen")
    a = ap.parse_args()
    if a.list:
        for kind, q in build_queries(a.limit):
            print(kind, repr(q))
        return
    res = run(a.base, a.limit, a.urls, max(1.0, a.gap), a.timeout, a.only)
    Path(a.out).write_text(json.dumps(res, indent=1, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(res["summary"], indent=1))


if __name__ == "__main__":
    main()
