"""Reserve fuer den Reader: Seite im Headless-Chromium rendern (JS-Seiten, SPAs).

Nur wenn das statische Laden nichts Lesbares liefert (leere SPA-Huelle, Bot-Schutz
fuer einfache HTTP-Clients). Braucht das optionale Paket `playwright` und dessen
Chromium (`.venv/bin/python -m playwright install chromium-headless-shell`); fehlt
beides oder steht READER_RENDER=0, ist die Funktion aus.

Sicherheit (SSRF, gleiche Regeln wie app/extract._public_ips: nur is_global):
- Start-URL nur http(s) mit oeffentlichem Host (file:, chrome:, data: & Co. nie).
- Jede Anfrage des Browser-Kontexts (Dokument, Unterressourcen, iframes, Popups,
  XHR/fetch, jeder Redirect-Sprung, JS-Weiterleitungen) faengt context.route ab und
  fuehrt sie ueber den gepinnten httpx-Client aus app/extract aus (Pruefung je
  Anfrage und je Verbindung, kein Redirect-Folgen im Client: der Browser stellt
  den naechsten Sprung neu, der wieder hier durchlaeuft). Interne Ziele -> abort.
- Was die Abfangschicht nicht sieht (Worker, WebSocket, Vorabladen), geht ueber
  einen toten Proxy (127.0.0.1:9, auch fuer Loopback) und scheitert; WebRTC und
  Service Worker sind aus, Bilder/Medien/Schriften werden gar nicht geladen.
- Hoechstens ein Render gleichzeitig (RAM).
"""

from __future__ import annotations

import importlib.util
import os
import threading
import time
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse

from .. import extract as extract_mod

_SEM = threading.Semaphore(1)
_SKIP_TYPES = {"image", "media", "font", "websocket", "manifest", "texttrack", "eventsource", "ping"}
_DROP_HEADERS = {"content-encoding", "content-length", "transfer-encoding", "connection"}
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")
_MAX_BODY = 8_000_000
# Alles, was an context.route vorbei ins Netz will, landet hier und scheitert.
_DEAD_PROXY = {"server": "http://127.0.0.1:9", "bypass": "<-loopback>"}
_ARGS = [
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--dns-prefetch-disable",
    "--disable-background-networking",
]
_INIT_JS = "try{delete window.RTCPeerConnection;delete window.webkitRTCPeerConnection}catch(e){}"


def enabled() -> bool:
    if os.environ.get("READER_RENDER", "1").strip() in ("0", "false", "no", ""):
        return False
    try:
        return importlib.util.find_spec("playwright.sync_api") is not None
    except ImportError:  # Paket fehlt ganz
        return False


def url_allowed(url: str) -> bool:
    """Nur http(s) zu Hosts, deren IPs alle oeffentlich sind (extract._public_ips)."""
    parts = urlparse(url)
    return parts.scheme in ("http", "https") and bool(parts.hostname) and \
        extract_mod._host_is_public(parts.hostname)


def _fetch(method: str, url: str, headers: Dict[str, str], body: Optional[bytes]
           ) -> Optional[Tuple[int, Dict[str, str], bytes]]:
    """Eine Browser-Anfrage ueber den gepinnten Client (SSRF-Hook + Pinning),
    ohne Redirects zu folgen. None = blockiert oder Netzfehler."""
    if not url_allowed(url):
        return None
    try:
        with extract_mod._client().stream(method, url, headers=headers, content=body,
                                          follow_redirects=False, timeout=8.0) as resp:
            data = bytearray()
            for part in resp.iter_bytes():
                data.extend(part)
                if len(data) >= _MAX_BODY:
                    break
            return resp.status_code, dict(resp.headers), bytes(data)
    except Exception:  # noqa: BLE001 - blockiert (intern) oder Netzfehler
        return None


def _proxy(route) -> None:
    req = route.request
    if req.resource_type in _SKIP_TYPES or not url_allowed(req.url):
        route.abort("blockedbyclient")
        return
    headers = {k: v for k, v in req.headers.items() if k.lower() not in ("host", "content-length")}
    headers.setdefault("user-agent", _UA)
    got = _fetch(req.method, req.url, headers, req.post_data_buffer)
    if got is None:
        route.abort("blockedbyclient")
        return
    status, resp_headers, body = got
    route.fulfill(
        status=status,
        headers={k: v for k, v in resp_headers.items() if k.lower() not in _DROP_HEADERS},
        body=body,
    )


SEM_WAIT_S = 1.0  # so lange auf einen laufenden Render warten, dann ueberspringen


def render_html(url: str, timeout: float = 12.0, wait: Optional[float] = None) -> Optional[str]:
    """Gerendertes HTML der Seite oder None. Blockiert hoechstens ~wait + timeout Sekunden."""
    return try_render(url, timeout, wait)[1]


def try_render(url: str, timeout: float = 12.0, wait: Optional[float] = None
               ) -> Tuple[str, Optional[str]]:
    """(status, html). status: "off" (aus/kein Budget), "blocked" (URL nicht erlaubt),
    "busy" (der eine Render-Platz blieb `wait` Sekunden, Standard SEM_WAIT_S, belegt)
    oder "tried" (echter Render-Versuch, html ggf. None). Das Budget `timeout` beginnt
    erst mit dem Platz, die kurze Wartezeit kommt hinzu."""
    if timeout <= 0 or not enabled():
        return "off", None
    if not url_allowed(url):
        return "blocked", None
    if not _SEM.acquire(timeout=max(0.0, min(SEM_WAIT_S if wait is None else wait, timeout))):
        return "busy", None
    return "tried", _render_locked(url, timeout)


def _render_locked(url: str, timeout: float) -> Optional[str]:
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415

        deadline = time.monotonic() + timeout
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, proxy=_DEAD_PROXY, args=_ARGS)
            try:
                ctx = browser.new_context(
                    user_agent=_UA, locale="en-US", service_workers="block",
                    java_script_enabled=True,
                )
                ctx.add_init_script(_INIT_JS)
                ctx.route("**/*", _proxy)
                page = ctx.new_page()
                resp = page.goto(url, wait_until="domcontentloaded",
                                 timeout=max(1000, int((deadline - time.monotonic()) * 1000)))
                if resp is not None and resp.status >= 400:
                    return None
                rest = deadline - time.monotonic()
                if rest > 0.3:
                    try:
                        page.wait_for_load_state("networkidle", timeout=int(min(rest, 4.0) * 1000))
                    except Exception:  # noqa: BLE001 - Dauer-Polling: nehmen, was da ist
                        pass
                if not url_allowed(page.url):  # Endstand nach JS-Weiterleitung
                    return None
                return page.content()
            finally:
                browser.close()
    except Exception:  # noqa: BLE001 - kein Chromium, Timeout, Absturz: Reserve faellt aus
        return None
    finally:
        _SEM.release()
