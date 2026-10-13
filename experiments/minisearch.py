"""minisearch: schlanker Meta-Such-Prototyp ohne SearXNG.

Fragt Bing, Yahoo und optional DuckDuckGo-HTML direkt und parallel ab,
fuehrt die Listen per Reciprocal Rank Fusion (RRF) zusammen und liefert
[{title, url, content, engine}] wie app/search.py.

Herkunft: eigene Implementierung. Die Wahl der Endpunkte, der XPath-Selektoren
und das Entpacken der Tracking-URLs (Bing ck/a?u=a1<base64>, Yahoo /RU=...)
orientieren sich an den SearXNG-Engines searx/engines/bing.py, yahoo.py und
duckduckgo.py (SearXNG, AGPL-3.0-or-later, https://github.com/searxng/searxng).
Wird diese Datei ausgeliefert, gilt fuer sie daher vorsorglich AGPL-3.0.

Nutzung:
    .venv/bin/python experiments/minisearch.py "frage" [--lang de] [--ddg]
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import time
from typing import Dict, List, Optional
from urllib.parse import parse_qs, unquote, urlparse, urlsplit, urlunsplit

from curl_cffi import AsyncSession
from lxml import html as lxml_html

# User-Agent, Accept & TLS-Fingerprint kommen von curl_cffi (impersonate="chrome").
BASE_HEADERS: Dict[str, str] = {}
RRF_K = 60
DEFAULT_DEADLINE = 0.7


def _text(el) -> str:
    if el is None:
        return ""
    return " ".join(el.text_content().split())


# ---------------------------------------------------------------- Bing
def _bing_unwrap(href: str) -> str:
    if href.startswith("https://www.bing.com/ck/a?"):
        u = parse_qs(urlparse(href).query).get("u", [""])[0]
        if u.startswith("a1"):
            enc = u[2:] + "=" * (-len(u[2:]) % 4)
            try:
                return base64.urlsafe_b64decode(enc).decode("utf-8", "replace")
            except ValueError:
                return href
    return href


async def bing(client: AsyncSession, q: str, lang: str) -> List[Dict[str, str]]:
    params = {"q": q, "adlt": "moderate"}
    if lang:
        params["setlang"] = lang
        if lang not in ("en",):
            params["cc"] = lang
    r = await client.get("https://www.bing.com/search", params=params,
                         headers={**BASE_HEADERS, "Accept-Language": f"{lang or 'en'};q=0.9"})
    doc = lxml_html.fromstring(r.content)
    out = []
    for li in doc.xpath('//ol[@id="b_results"]/li[contains(@class,"b_algo")]'):
        a = li.xpath(".//h2/a")
        if not a:
            continue
        href, title = a[0].get("href", ""), _text(a[0])
        if not href or not title:
            continue
        for icon in li.xpath('.//p//span[@class="algoSlug_icon"]'):
            icon.getparent().remove(icon)
        content = " ".join(_text(p) for p in li.xpath(".//p"))
        out.append({"title": title, "url": _bing_unwrap(href), "content": content})
    return out


# ---------------------------------------------------------------- Yahoo
def _yahoo_unwrap(u: str) -> str:
    start = u.find("http", u.find("/RU=") + 1)
    ends = [p for p in (u.rfind("/RS"), u.rfind("/RK")) if p > -1]
    if start <= 0 or not ends:
        return u
    return unquote(u[start:min(ends)])


async def yahoo(client: AsyncSession, q: str, lang: str) -> List[Dict[str, str]]:
    host = "de.search.yahoo.com" if lang == "de" else "search.yahoo.com"
    cookies = {"sB": f"v=1&vm=p&fl=1&vl=lang_{lang or 'en'}&pn=10&rw=new&userset=1"}
    r = await client.get(f"https://{host}/search", params={"p": q, "iscqry": ""},
                         headers=BASE_HEADERS, cookies=cookies, allow_redirects=True)
    doc = lxml_html.fromstring(r.content)
    out = []
    for div in doc.xpath('//div[contains(@class,"algo-sr")]'):
        hrefs = div.xpath('.//div[contains(@class,"compTitle")]//a/@href')
        if not hrefs:
            continue
        title = (div.xpath(".//h3//a/@aria-label") or [""])[0]
        if not title:
            h3 = div.xpath(".//a/h3")
            title = _text(h3[0]) if h3 else ""
        if not title:
            continue
        txt = div.xpath('.//div[contains(@class,"compText")]')
        out.append({"title": " ".join(title.split()), "url": _yahoo_unwrap(hrefs[0]),
                    "content": _text(txt[0]) if txt else ""})
    return out


# ---------------------------------------------------------------- DDG html
async def ddg(client: AsyncSession, q: str, lang: str) -> List[Dict[str, str]]:
    kl = "de-de" if lang == "de" else "wt-wt"
    r = await client.post("https://html.duckduckgo.com/html/", data={"q": q, "b": "", "kl": kl},
                          headers={**BASE_HEADERS, "Referer": "https://html.duckduckgo.com/",
                                   "Content-Type": "application/x-www-form-urlencoded"})
    doc = lxml_html.fromstring(r.content)
    if doc.xpath("//form[@id='challenge-form']"):
        raise RuntimeError("ddg captcha")
    out = []
    for div in doc.xpath('//div[@id="links"]/div[contains(@class,"web-result")]'):
        a = div.xpath(".//h2/a")
        if not a:
            continue
        sn = div.xpath('.//a[contains(@class,"result__snippet")]')
        out.append({"title": _text(a[0]), "url": a[0].get("href", ""),
                    "content": _text(sn[0]) if sn else ""})
    return out


ENGINES = {"bing": bing, "yahoo": yahoo, "duckduckgo": ddg}


# ---------------------------------------------------------------- Fusion
def norm_url(u: str) -> str:
    """Schluessel fuer Dubletten: ohne Schema, www., Fragment, Slash am Ende."""
    try:
        s = urlsplit(u)
    except ValueError:
        return u
    host = s.netloc.lower().removeprefix("www.")
    return urlunsplit(("", host, s.path.rstrip("/"), s.query, ""))


def rrf(lists: Dict[str, List[Dict[str, str]]], k: int = RRF_K) -> List[Dict[str, str]]:
    score: Dict[str, float] = {}
    best: Dict[str, Dict[str, str]] = {}
    for eng, items in lists.items():
        for rank, it in enumerate(items, 1):
            if not it["url"].startswith("http"):
                continue
            key = norm_url(it["url"])
            score[key] = score.get(key, 0.0) + 1.0 / (k + rank)
            cur = best.get(key)
            if cur is None:
                best[key] = {**it, "engine": eng}
            else:
                cur["engine"] += "," + eng
                if len(it["content"]) > len(cur["content"]):
                    cur["content"] = it["content"]
    return [best[key] for key in sorted(score, key=score.get, reverse=True)]


async def search(q: str, lang: str = "", engines=("bing", "yahoo"),
                 deadline: float = DEFAULT_DEADLINE,
                 client: Optional[AsyncSession] = None) -> Dict:
    """Alle Engines parallel; was nach `deadline` Sekunden nicht da ist, faellt raus."""
    own = client is None
    client = client or make_client()
    t0 = time.perf_counter()
    timing: Dict[str, Optional[float]] = {}

    async def run(name):
        s = time.perf_counter()
        try:
            return name, await ENGINES[name](client, q, lang), None
        except Exception as exc:  # Engine-Fehler darf die Suche nicht kippen
            return name, [], type(exc).__name__
        finally:
            timing[name] = round((time.perf_counter() - s) * 1000, 1)

    tasks = [asyncio.create_task(run(n)) for n in engines]
    done, pending = await asyncio.wait(tasks, timeout=deadline)
    for t in pending:
        t.cancel()
    lists, errors = {}, {}
    for t in done:
        name, items, err = t.result()
        lists[name] = items
        if err:
            errors[name] = err
    for t, n in zip(tasks, engines):
        if t in pending:
            errors[n] = "deadline"
            timing[n] = None
    if own:
        await client.close()
    return {"results": rrf(lists), "timing_ms": timing, "errors": errors,
            "total_ms": round((time.perf_counter() - t0) * 1000, 1),
            "per_engine": {k: len(v) for k, v in lists.items()}}


def make_client() -> AsyncSession:
    # Eine Session pro Prozess wiederverwenden: warme TLS-Verbindungen und der
    # Yahoo-YBV-Cookie (sonst 2-4 Redirect-Hops pro Anfrage) bleiben erhalten.
    return AsyncSession(impersonate="chrome", timeout=2.0)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--lang", default="")
    ap.add_argument("--ddg", action="store_true")
    ap.add_argument("--deadline", type=float, default=DEFAULT_DEADLINE)
    a = ap.parse_args()
    engs = ("bing", "yahoo", "duckduckgo") if a.ddg else ("bing", "yahoo")
    res = asyncio.run(search(a.query, a.lang, engs, a.deadline))
    print(json.dumps({**res, "results": res["results"][:10]}, ensure_ascii=False, indent=1))
