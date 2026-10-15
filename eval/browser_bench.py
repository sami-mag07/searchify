#!/usr/bin/env python3
"""Browser-Vergleich fuer schwierige Seiten: normales Chrome, patchright (offener
Tarn-Playwright), ShardX (ShardBrowser, Anti-Detect-Chromium, Kern closed source)
und Camoufox (quelloffener getarnter Firefox, Messung 15.10.2026 auf dem Mac).

Gleiche URLs, gleiche Reihenfolge, gleiche Wartezeit, alle headless, ohne Proxy.
URL-Gruppen aus eval/browser_bench_urls.json (Messung 14.10.2026, 856 Trefferseiten):
  blockiert  33 Seiten, die auch mit Chrome-TLS und normalem Headless-Chrome blockten
  schwer     30 Seiten, die erst mit Chrome-TLS oder Browser lesbar wurden
  normal     30 Seiten, die schon mit einfachem HTTP lesbar sind (Tempo-Vergleich)

  browser_bench.py --backends chrome,patchright,shardx --out ergebnis.json

Gedacht fuer den Laptop (ShardX laedt beim ersten Start sein Chromium vom
Hersteller-CDN, darum nicht auf dem Mac). Pakete: playwright, patchright, shardx
(Versionen in train/START-browser.md).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import statistics
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BOT = re.compile(
    r"just a moment|cf-chl|verify you are human|are you a robot|access denied|"
    r"captcha-delivery|px-captcha|unusual traffic|request unsuccessful", re.I)
MIN_WORDS = 80
WAIT_MS = 3000
TIMEOUT_MS = 20000


async def visit(ctx, url: str) -> dict:
    """Eine Seite laden und bewerten: lesbar = Status < 400, mind. 80 Woerter
    Text und keine Bot-Seite."""
    page = await ctx.new_page()
    t0 = time.perf_counter()
    try:
        resp = await page.goto(url, timeout=TIMEOUT_MS, wait_until="domcontentloaded")
        await page.wait_for_timeout(WAIT_MS)
        text = await page.inner_text("body")
        html_head = (await page.content())[:20000]
        words = len(text.split())
        status = resp.status if resp else 0
        bot = bool(BOT.search(html_head)) and words < 200
        ok = 0 < status < 400 and words >= MIN_WORDS and not bot
        return {"ok": ok, "status": status, "words": words, "bot": bot,
                "sec": round(time.perf_counter() - t0, 2)}
    except Exception as e:  # noqa: BLE001 - Timeouts, Netzfehler, abgestuerzte Tabs
        return {"ok": False, "status": 0, "err": f"{type(e).__name__}: {str(e)[:160]}",
                "sec": round(time.perf_counter() - t0, 2)}
    finally:
        await page.close()


async def visit_all(open_ctx, urls) -> dict:
    """Alle URLs nacheinander. Stirbt der Browser nach einem Timeout (Tab haengt,
    Kontext geschlossen), wird er neu gestartet und die naechste URL laeuft weiter."""
    out = {}
    ctx, close = await open_ctx()
    for u in urls:
        r = await visit(ctx, u)
        if "closed" in r.get("err", "").lower():
            try:
                await close()
            except Exception:  # noqa: BLE001
                pass
            ctx, close = await open_ctx()
            r = await visit(ctx, u)
        out[u] = r
        print(("   OK " if r["ok"] else "   -- ") + str(r.get("status")) + " " + u[:80], flush=True)
    await close()
    return out


async def run_chrome(urls):
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        async def open_ctx():
            browser = await p.chromium.launch(headless=True)
            ctx = await browser.new_context(locale="de-DE")
            return ctx, browser.close
        return await visit_all(open_ctx, urls)


async def run_patchright(urls):
    import tempfile

    from patchright.async_api import async_playwright
    async with async_playwright() as p:
        async def open_ctx():
            ctx = await p.chromium.launch_persistent_context(
                tempfile.mkdtemp(prefix="pr-"), headless=True, no_viewport=True)
            return ctx, ctx.close
        return await visit_all(open_ctx, urls)


async def run_shardx(urls):
    from shardx import ShardX
    sdk = ShardX()
    profile = sdk.create_profile(platform="Windows")
    try:
        async def open_ctx():
            cm = sdk.session(profile, headless=True)
            browser = await cm.__aenter__()

            async def close():
                await cm.__aexit__(None, None, None)
            return browser.contexts[0], close
        return await visit_all(open_ctx, urls)
    finally:
        sdk.delete_profile(profile.id)


async def run_camoufox(urls):
    """Camoufox (quelloffener getarnter Firefox). Eigenes venv, Version gepinnt,
    GeoIP und Standard-Addons (uBlock) aus, damit beim Start nichts nachgeladen wird
    (Security-Vetting 15.10.). Die Binary liegt per Hand geladen und Hash-geprueft im
    Camoufox-Cache als aktive Version, darum braucht es kein `camoufox fetch`.
    executable_path geht auf dem Mac nicht (sucht properties.json neben der Binary)."""
    from camoufox.async_api import AsyncCamoufox
    async with AsyncCamoufox(headless=True, geoip=False, exclude_addons=_camoufox_addons(),
                             locale="de-DE") as browser:
        async def open_ctx():
            ctx = await browser.new_context()
            return ctx, ctx.close
        return await visit_all(open_ctx, urls)


def _camoufox_addons():
    from camoufox import DefaultAddons
    return list(DefaultAddons)


BACKENDS = {"chrome": run_chrome, "patchright": run_patchright, "shardx": run_shardx,
            "camoufox": run_camoufox}


def summary(groups: dict, res: dict) -> dict:
    s = {}
    for g, urls in groups.items():
        rows = [res[u] for u in urls if u in res]
        okt = [r["sec"] for r in rows if r["ok"]]
        s[g] = {"ok": sum(r["ok"] for r in rows), "n": len(rows),
                "median_s_ok": round(statistics.median(okt), 2) if okt else None}
    return s


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backends", default="chrome,patchright,shardx")
    ap.add_argument("--urls", default=str(HERE / "browser_bench_urls.json"))
    ap.add_argument("--out", default="browser_bench_result.json")
    ap.add_argument("--limit", type=int, default=0, help="nur die ersten N URLs je Gruppe (Rauchtest)")
    ap.add_argument("--groups", default="", help="nur diese Gruppen, z. B. blockiert")
    a = ap.parse_args()
    groups = json.load(open(a.urls, encoding="utf-8"))
    if a.groups:
        groups = {g: v for g, v in groups.items() if g in a.groups.split(",")}
    if a.limit:
        groups = {g: v[:a.limit] for g, v in groups.items()}
    urls = [u for v in groups.values() for u in v]
    result = {"groups": groups, "backends": {}}
    for name in a.backends.split(","):
        print(f"== {name}: {len(urls)} Seiten", flush=True)
        t0 = time.perf_counter()
        try:
            res = asyncio.run(BACKENDS[name](urls))
        except Exception as e:  # noqa: BLE001 - ein Backend darf ausfallen, die anderen laufen
            print(f"   {name} ausgefallen: {type(e).__name__}: {e}")
            result["backends"][name] = {"error": f"{type(e).__name__}: {e}"}
            continue
        result["backends"][name] = {"pages": res, "summary": summary(groups, res),
                                    "total_s": round(time.perf_counter() - t0, 1)}
        for g, v in result["backends"][name]["summary"].items():
            print(f"   {g:10} {v['ok']:3}/{v['n']}  Median {v['median_s_ok']} s", flush=True)
        json.dump(result, open(a.out, "w", encoding="utf-8"), indent=1)
    print("\n| Gruppe | " + " | ".join(result["backends"]) + " |")
    print("|---|" + "---|" * len(result["backends"]))
    for g in groups:
        cells = []
        for b in result["backends"].values():
            v = b.get("summary", {}).get(g)
            cells.append(f"{v['ok']}/{v['n']} ({v['median_s_ok']} s)" if v else "ausgefallen")
        print(f"| {g} | " + " | ".join(cells) + " |")
    print("\nErgebnis:", a.out)


if __name__ == "__main__":
    main()
