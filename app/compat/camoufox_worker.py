"""Ein Seitenaufruf in Camoufox, als eigener Prozess aus dem Camoufox-venv.

Laeuft NICHT im Dienst-venv (Camoufox verlangt playwright < 1.63, der Dienst nutzt
1.63) und importiert darum nichts aus app/. Aufruf durch app/compat/stealth.py mit
`python -I camoufox_worker.py`, Eingabe als JSON auf stdin:
  {"url": ..., "proxy_port": ..., "timeout": Sekunden, "settle": Sekunden, "max_chars": ...}
Ausgabe eine JSON-Zeile auf stdout: {"status": int, "url": Endstand, "html": ...}
oder {"error": ...}.

Alles Netz geht ueber den SSRF-Proxy (stealth_proxy.py), auch localhost (Firefox
umgeht den Proxy sonst fuer Loopback). WebRTC, Service Worker, DNS-Vorabfragen,
DoH und Vorabladen sind aus, Bilder werden nicht geladen. Kein GeoIP, keine
Standard-Addons (uBlock wuerde sonst ungepinnt von addons.mozilla.org kommen).
"""

import json
import sys
import time

PREFS = {
    "network.proxy.allow_hijacking_localhost": True,
    "network.proxy.no_proxies_on": "",
    "network.proxy.failover_direct": False,
    "network.trr.mode": 5,
    "network.dns.disablePrefetch": True,
    "network.prefetch-next": False,
    "network.http.speculative-parallel-limit": 0,
    "network.predictor.enabled": False,
    "media.peerconnection.enabled": False,
    "dom.serviceWorkers.enabled": False,
    "browser.cache.disk.enable": False,
}


def main() -> None:
    job = json.loads(sys.stdin.read())
    deadline = time.monotonic() + float(job["timeout"])
    from camoufox import DefaultAddons
    from camoufox.sync_api import Camoufox

    out = {}
    with Camoufox(
        headless=True, geoip=False, exclude_addons=list(DefaultAddons), block_images=True,
        block_webrtc=True, locale="de-DE", firefox_user_prefs=PREFS,
        proxy={"server": f"http://localhost:{int(job['proxy_port'])}", "bypass": ""},
    ) as browser:
        page = browser.new_page()
        rest_ms = max(1000, int((deadline - time.monotonic()) * 1000))
        resp = page.goto(job["url"], wait_until="domcontentloaded", timeout=rest_ms)
        settle = min(float(job.get("settle", 3.0)), deadline - time.monotonic() - 0.5)
        if settle > 0:
            page.wait_for_timeout(int(settle * 1000))
        html = page.content()
        limit = int(job.get("max_chars", 4_000_000))  # wie READER_MAX_BYTES der anderen Stufen
        out = {"status": resp.status if resp else 0, "url": page.url, "html": html[:limit]}
    sys.stdout.write(json.dumps(out))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # noqa: BLE001 - Fehler als JSON, der Dienst wertet aus
        sys.stdout.write(json.dumps({"error": f"{type(exc).__name__}: {str(exc)[:200]}"}))
