"""Reserve fuer den Reader: Seite im Headless-Chromium rendern (JS-Seiten, SPAs, Bot-Schutz).

Springt nur an, wenn das statische Laden nichts Lesbares liefert; der schnelle Weg
bleibt unberuehrt. Zwei Stufen, die zweite nur, wenn die erste scheitert:

- Stufe 2, `try_render`: geteilter Chromium, der offen bleibt (Start bei Bedarf,
  nach IDLE_CLOSE_S ohne Render wieder zu), je Render ein frischer Kontext, SLOTS
  Renders gleichzeitig. Jede Anfrage des Browsers laeuft ueber den gepinnten
  httpx-Client (siehe unten), der Browser selbst geht nie ins Netz.
- Stufe 3, `try_render_direct`: eigener Chromium je Aufruf, der das Hauptdokument
  SELBST laedt (echter Chrome-TLS-Fingerabdruck, darum kommt er an Cloudflare &
  Co. vorbei, wo httpx die Challenge bekommt; so macht es Crawl4AI). Der SSRF-
  Schutz bleibt: nur vorab gepruefte Hosts (Start-Host, www-Variante, Challenge-
  Hosts) duerfen direkt, ihre IP ist per --host-resolver-rules festgenagelt
  (kein DNS-Rebinding), jeder andere Name loest zu nichts auf und jeder andere
  Host (auch IP-Literale, Redirect-Ziele) geht an den toten Proxy. Unterressourcen
  fremder Hosts laufen wie in Stufe 2 ueber httpx.

Braucht das optionale Paket `playwright` und dessen Chromium
(`.venv/bin/python -m playwright install chromium-headless-shell`); fehlt beides oder
steht READER_RENDER=0, ist die Funktion aus. RENDER_DIRECT=0 schaltet nur Stufe 3 ab.
Stufe 3 nimmt ebenfalls die headless-shell: gemessen 15.10. auf 30 Bot-Seiten ~760 MB
weniger RAM als der volle Chromium und nicht weniger Treffer
(RENDER_DIRECT_CHANNEL=chromium waehlt den vollen).

Sicherheit (SSRF, gleiche Regeln wie app/extract._public_ips: nur is_global):
- Start-URL nur http(s) mit oeffentlichem Host (file:, chrome:, data: & Co. nie).
- Jede Anfrage des Browser-Kontexts (Dokument, Unterressourcen, iframes, Popups,
  XHR/fetch, jeder Redirect-Sprung, JS-Weiterleitungen) faengt context.route ab und
  fuehrt sie ueber den gepinnten httpx-Client aus app/extract aus (Pruefung je
  Anfrage und je Verbindung, kein Redirect-Folgen im Client: der Browser stellt
  den naechsten Sprung neu, der wieder hier durchlaeuft). Interne Ziele -> abort.
  Ausnahme Stufe 3: gepinnte Hosts (siehe oben).
- Was die Abfangschicht nicht sieht (Worker, WebSocket, Vorabladen), geht ueber
  einen toten Proxy (127.0.0.1:9, auch fuer Loopback) und scheitert; WebRTC und
  Service Worker sind aus, Bilder/Medien/Schriften werden gar nicht geladen.
"""

from __future__ import annotations

import asyncio
import importlib.util
import os
import re
import threading
import time
from typing import Dict, Optional, Tuple
from urllib.parse import urlparse

from .. import extract as extract_mod

SLOTS = max(1, int(os.environ.get("RENDER_SLOTS", "3")))  # Stufe 2 gleichzeitig (ein Browser)
DIRECT_SLOTS = max(1, int(os.environ.get("RENDER_DIRECT_SLOTS", "1")))  # Stufe 3: je ein Browser
IDLE_CLOSE_S = 300.0  # geteilten Browser nach so viel Leerlauf schliessen (RAM)
_SEM = threading.Semaphore(SLOTS)
_DIRECT_SEM = threading.Semaphore(DIRECT_SLOTS)
_SKIP_TYPES = {"image", "media", "font", "websocket", "manifest", "texttrack", "eventsource", "ping"}
_DROP_HEADERS = {"content-encoding", "content-length", "transfer-encoding", "connection"}
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")
# Stufe 3: UA passend zum echten Browser (Plattform und Version), sonst faellt der Widerspruch auf
_UA_DIRECT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{ver} Safari/537.36"
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
# Hosts, die Challenge-Seiten selbst nachladen; sie duerfen in Stufe 3 direkt (gepinnt).
_CHALLENGE_HOSTS = ("challenges.cloudflare.com",)
_CHALLENGE_TITLE_RE = re.compile(
    r"just a moment|attention required|checking your browser|verifying|security check|un moment", re.IGNORECASE)


def enabled() -> bool:
    if os.environ.get("READER_RENDER", "1").strip() in ("0", "false", "no", ""):
        return False
    try:
        return importlib.util.find_spec("playwright.sync_api") is not None
    except ImportError:  # Paket fehlt ganz
        return False


def direct_enabled() -> bool:
    return enabled() and os.environ.get("RENDER_DIRECT", "1").strip() not in ("0", "false", "no", "")


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


async def _proxy(route) -> None:
    try:
        req = route.request
        if req.resource_type in _SKIP_TYPES or not await asyncio.to_thread(url_allowed, req.url):
            await route.abort("blockedbyclient")
            return
        headers = {k: v for k, v in req.headers.items() if k.lower() not in ("host", "content-length")}
        headers.setdefault("user-agent", _UA)
        got = await asyncio.to_thread(_fetch, req.method, req.url, headers, req.post_data_buffer)
        if got is None:
            await route.abort("blockedbyclient")
            return
        status, resp_headers, body = got
        await route.fulfill(
            status=status,
            headers={k: v for k, v in resp_headers.items() if k.lower() not in _DROP_HEADERS},
            body=body,
        )
    except Exception:  # noqa: BLE001 - Kontext schon zu (Render fertig oder abgebrochen)
        pass


# ---------------------------------------------------------------- Browser-Thread


class _Engine:
    """Ein Hintergrund-Thread mit eigener asyncio-Schleife, darin Playwright und der
    geteilte Chromium. Playwright-Objekte gehoeren diesem Thread; die Lese-Threads
    reichen nur Auftraege herein (run) und warten auf das Ergebnis."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.pw = None
        self.browser = None
        self.active = 0
        self.last_used = 0.0
        self._launch_lock: Optional[asyncio.Lock] = None

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self.loop is None:
                loop = asyncio.new_event_loop()
                threading.Thread(target=loop.run_forever, name="render-loop", daemon=True).start()
                asyncio.run_coroutine_threadsafe(self._idle_closer(), loop)
                self.loop = loop
        return self.loop

    def run(self, make_coro, timeout: float):
        """make_coro() im Browser-Thread ausfuehren, hoechstens `timeout` Sekunden.
        None bei Zeitlimit oder Fehler."""
        fut = asyncio.run_coroutine_threadsafe(self._guard(make_coro, timeout), self._ensure_loop())
        try:
            return fut.result(timeout + 3.0)
        except Exception:  # noqa: BLE001 - Zeitlimit: Auftrag abbrechen
            fut.cancel()
            return None

    async def _guard(self, make_coro, timeout: float):
        self.active += 1
        try:
            return await asyncio.wait_for(make_coro(), timeout)
        except Exception:  # noqa: BLE001 - kein Chromium, Timeout, Absturz: Reserve faellt aus
            return None
        finally:
            self.active -= 1
            self.last_used = time.monotonic()

    async def playwright(self):
        if self.pw is None:
            from playwright.async_api import async_playwright  # noqa: PLC0415

            self.pw = await async_playwright().start()
        return self.pw

    async def shared_browser(self):
        if self._launch_lock is None:
            self._launch_lock = asyncio.Lock()
        async with self._launch_lock:
            if self.browser is None or not self.browser.is_connected():
                pw = await self.playwright()
                self.browser = await pw.chromium.launch(headless=True, proxy=_DEAD_PROXY, args=_ARGS)
        return self.browser

    async def _idle_closer(self) -> None:
        while True:
            await asyncio.sleep(30)
            if (self.browser is not None and self.active == 0
                    and time.monotonic() - self.last_used > IDLE_CLOSE_S):
                browser, self.browser = self.browser, None
                try:
                    await browser.close()
                except Exception:  # noqa: BLE001
                    pass


_ENGINE = _Engine()


def _ms(deadline: float) -> int:
    return max(1000, int((deadline - time.monotonic()) * 1000))


async def _title(page) -> str:
    try:
        return await page.title()
    except Exception:  # noqa: BLE001 - Seite navigiert gerade (Challenge laedt neu)
        return "just a moment"


async def _load(page, url: str, deadline: float, challenge: bool = False) -> Optional[str]:
    """Seite oeffnen, kurz auf Nachladen warten, HTML zurueck. `challenge`: auf eine
    Bot-Challenge warten, bis sie sich selbst loest (Cloudflare antwortet mit 403
    und laedt nach bestandener Pruefung neu)."""
    resp = await page.goto(url, wait_until="domcontentloaded", timeout=_ms(deadline))
    status = resp.status if resp is not None else 200
    if challenge and _CHALLENGE_TITLE_RE.search(await _title(page)):
        while time.monotonic() < deadline - 0.5:
            await asyncio.sleep(0.5)
            if not _CHALLENGE_TITLE_RE.search(await _title(page)):
                status = 200
                break
        else:
            return None
    if status >= 400:
        return None
    rest = deadline - time.monotonic()
    if rest > 0.3:
        try:
            await page.wait_for_load_state("networkidle", timeout=int(min(rest, 4.0) * 1000))
        except Exception:  # noqa: BLE001 - Dauer-Polling: nehmen, was da ist
            pass
    if not await asyncio.to_thread(url_allowed, page.url):  # Endstand nach JS-Weiterleitung
        return None
    return await page.content()


# ---------------------------------------------------------------- Stufe 2


SEM_WAIT_S = 1.0  # so lange auf einen freien Render-Platz warten, dann ueberspringen


def render_html(url: str, timeout: float = 12.0, wait: Optional[float] = None) -> Optional[str]:
    """Gerendertes HTML der Seite oder None. Blockiert hoechstens ~wait + timeout Sekunden."""
    return try_render(url, timeout, wait)[1]


def try_render(url: str, timeout: float = 12.0, wait: Optional[float] = None
               ) -> Tuple[str, Optional[str]]:
    """(status, html). status: "off" (aus/kein Budget), "blocked" (URL nicht erlaubt),
    "busy" (alle Render-Plaetze blieben `wait` Sekunden, Standard SEM_WAIT_S, belegt)
    oder "tried" (echter Render-Versuch, html ggf. None). Das Budget `timeout` beginnt
    erst mit dem Platz, die kurze Wartezeit kommt hinzu."""
    if timeout <= 0 or not enabled():
        return "off", None
    if not url_allowed(url):
        return "blocked", None
    if not _SEM.acquire(timeout=max(0.0, min(SEM_WAIT_S if wait is None else wait, timeout))):
        return "busy", None
    try:
        return "tried", _ENGINE.run(lambda: _render_shared(url, time.monotonic() + timeout), timeout)
    finally:
        _SEM.release()


async def _render_shared(url: str, deadline: float) -> Optional[str]:
    browser = await _ENGINE.shared_browser()
    ctx = await browser.new_context(
        user_agent=_UA, locale="en-US", service_workers="block", java_script_enabled=True,
    )
    try:
        await ctx.add_init_script(_INIT_JS)
        await ctx.route("**/*", _proxy)
        page = await ctx.new_page()
        return await _load(page, url, deadline)
    finally:
        try:
            await ctx.close()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------- Stufe 3


def _pins(url: str) -> Dict[str, str]:
    """Hosts, die der Browser in Stufe 3 selbst laden darf, je mit gepruefter
    oeffentlicher IP (IPv4 bevorzugt). Leer, wenn der Start-Host nicht dabei ist."""
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    if not host:
        return {}
    twin = host[4:] if host.startswith("www.") else "www." + host
    pins: Dict[str, str] = {}
    for name in (host, twin, *_CHALLENGE_HOSTS):
        ips = extract_mod._public_ips(name)
        if ips:
            pins[name] = next((ip for ip in ips if ":" not in ip), ips[0])
    return pins if host in pins else {}


def _direct_launch(pins: Dict[str, str]) -> Dict:
    """Start-Optionen fuer Stufe 3: nur die gepinnten Hosts umgehen den toten Proxy,
    und nur sie loesen auf (zur geprueften IP); jeder andere Name -> NOTFOUND."""
    rules = ", ".join(f"MAP {h} {f'[{ip}]' if ':' in ip else ip}" for h, ip in pins.items())
    return {
        "proxy": {"server": _DEAD_PROXY["server"], "bypass": ",".join(["<-loopback>", *pins])},
        "args": _ARGS + [
            f"--host-resolver-rules={rules}, MAP * ~NOTFOUND",
            "--disable-blink-features=AutomationControlled",
        ],
    }


def try_render_direct(url: str, timeout: float = 15.0, wait: Optional[float] = None
                      ) -> Tuple[str, Optional[str]]:
    """Stufe 3, Status wie try_render. Nur aufrufen, wenn Stufe 2 gescheitert ist
    (eigener Browserstart, ~1 s mehr)."""
    if timeout <= 0 or not direct_enabled():
        return "off", None
    if not url_allowed(url):
        return "blocked", None
    pins = _pins(url)
    if not pins:
        return "blocked", None
    if not _DIRECT_SEM.acquire(timeout=max(0.0, min(SEM_WAIT_S if wait is None else wait, timeout))):
        return "busy", None
    try:
        return "tried", _ENGINE.run(lambda: _render_direct(url, time.monotonic() + timeout, pins), timeout)
    finally:
        _DIRECT_SEM.release()


def _direct_route(pins: Dict[str, str]):
    async def handler(route) -> None:
        try:
            req = route.request
            parts = urlparse(req.url)
            if req.resource_type not in _SKIP_TYPES and parts.scheme in ("http", "https") \
                    and (parts.hostname or "").lower() in pins:
                await route.continue_()  # Browser laedt selbst, Proxy-Bypass + Resolver-Pin
                return
        except Exception:  # noqa: BLE001 - Kontext schon zu
            return
        await _proxy(route)
    return handler


async def _render_direct(url: str, deadline: float, pins: Dict[str, str]) -> Optional[str]:
    pw = await _ENGINE.playwright()
    opts = _direct_launch(pins)
    channel = os.environ.get("RENDER_DIRECT_CHANNEL", "").strip()  # "chromium": voller Browser
    try:
        browser = await pw.chromium.launch(headless=True, channel=channel or None, **opts)
    except Exception:  # noqa: BLE001 - Kanal nicht installiert
        browser = await pw.chromium.launch(headless=True, **opts)
    try:
        ctx = await browser.new_context(
            user_agent=_UA_DIRECT.format(ver=browser.version), locale="en-US",
            service_workers="block", viewport={"width": 1366, "height": 768},
        )
        await ctx.add_init_script(_INIT_JS)
        await ctx.route("**/*", _direct_route(pins))
        page = await ctx.new_page()
        return await _load(page, url, deadline, challenge=True)
    finally:
        try:
            await browser.close()
        except Exception:  # noqa: BLE001
            pass
