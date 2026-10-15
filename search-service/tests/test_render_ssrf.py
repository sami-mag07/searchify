"""SSRF-Schutz des Browser-Renderings (app/compat/render.py) mit echtem Chromium.

Eine "oeffentliche" Testseite (public.test, ueber _fetch eingespielt) versucht auf
allen Wegen interne Adressen zu erreichen: Redirect, JS-Weiterleitung, Bild, iframe,
fetch, Worker, WebSocket, Beacon, Popup, Prefetch, 169.254.169.254. Ein lokaler
Server auf 127.0.0.1 zaehlt jeden Treffer, es muessen null sein.
Ohne playwright/Chromium werden die Browser-Tests uebersprungen.
"""

import http.server
import socket
import threading

import pytest

from app.compat import render as render_mod


def test_start_url_nur_oeffentlich_http():
    for url in ("file:///etc/passwd", "http://127.0.0.1:8000/", "http://169.254.169.254/latest/meta-data/",
                "http://localhost/", "chrome://version", "data:text/html,hi", "http://10.0.0.1/"):
        assert not render_mod.url_allowed(url), url
        assert render_mod.render_html(url) is None, url


def test_fetch_blockt_intern():
    for url in ("http://127.0.0.1:9/", "http://169.254.169.254/latest/meta-data/", "http://[::1]/"):
        assert render_mod._fetch("GET", url, {}, None) is None, url


@pytest.fixture()
def internal_server():
    hits = []

    class H(http.server.BaseHTTPRequestHandler):
        def _hit(self):
            hits.append(self.path)
            self.send_response(200)
            self.send_header("content-type", "text/html")
            self.end_headers()
            self.wfile.write(b"<html><body>GEHEIM intern</body></html>")

        do_GET = do_POST = _hit

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv.server_address[1], hits
    srv.shutdown()


def _need_browser():
    pytest.importorskip("playwright.sync_api")
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            p.chromium.launch(headless=True).close()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Chromium fehlt: {exc}")


def _fake_public(monkeypatch, pages, seen):
    real_fetch, real_allowed = render_mod._fetch, render_mod.url_allowed

    def allowed(url):
        return url.startswith("https://public.test/") or real_allowed(url)

    def fetch(method, url, headers, body):
        seen.append(url)
        if url in pages:
            return pages[url]
        return real_fetch(method, url, headers, body)

    monkeypatch.setattr(render_mod, "url_allowed", allowed)
    monkeypatch.setattr(render_mod, "_fetch", fetch)
    monkeypatch.setenv("READER_RENDER", "1")


def _html(body):
    return 200, {"content-type": "text/html; charset=utf-8"}, body.encode()


def test_browser_unterressourcen_und_js_intern_blockiert(monkeypatch, internal_server):
    _need_browser()
    port, hits = internal_server
    loc = f"http://127.0.0.1:{port}"
    page = f"""<html><head><link rel="prefetch" href="{loc}/prefetch"></head><body>
<p>Oeffentlicher Inhalt mit genug Woertern fuer den Test.</p>
<img src="{loc}/img"><img src="http://169.254.169.254/latest/meta-data/">
<iframe src="{loc}/iframe"></iframe>
<script>
fetch("{loc}/fetch").catch(()=>{{}});
fetch("http://169.254.169.254/latest/meta-data/").catch(()=>{{}});
try {{ new Worker(URL.createObjectURL(new Blob(['fetch("{loc}/worker").catch(()=>{{}})']))) }} catch(e) {{}}
try {{ new WebSocket("ws://127.0.0.1:{port}/ws") }} catch(e) {{}}
try {{ navigator.sendBeacon("{loc}/beacon", "x") }} catch(e) {{}}
try {{ window.open("{loc}/popup") }} catch(e) {{}}
</script></body></html>"""
    seen = []
    _fake_public(monkeypatch, {"https://public.test/": _html(page)}, seen)
    html = render_mod.render_html("https://public.test/", timeout=8.0)
    assert html and "Oeffentlicher Inhalt" in html
    assert hits == []
    assert "GEHEIM" not in html


def test_browser_redirects_intern_blockiert(monkeypatch, internal_server):
    _need_browser()
    port, hits = internal_server
    loc = f"http://127.0.0.1:{port}"
    pages = {
        "https://public.test/r302": (302, {"location": f"{loc}/r302"}, b""),
        "https://public.test/meta": (302, {"location": "http://169.254.169.254/latest/meta-data/"}, b""),
        "https://public.test/js": _html(f'<html><body><script>location.href="{loc}/js"</script></body></html>'),
        "https://public.test/refresh": _html(
            f'<html><head><meta http-equiv="refresh" content="0;url={loc}/refresh"></head><body>x</body></html>'),
    }
    seen = []
    _fake_public(monkeypatch, pages, seen)
    for url in pages:
        html = render_mod.render_html(url, timeout=6.0)
        assert not html or "GEHEIM" not in html, url
    assert hits == []


def test_kein_dienst_ohne_playwright(monkeypatch):
    monkeypatch.setattr(render_mod.importlib.util, "find_spec", lambda name: None)
    assert not render_mod.enabled()
    assert render_mod.render_html("https://example.org/") is None


def test_lokaler_port_frei():
    # Plausibilitaet: der tote Proxy-Port 9 nimmt nichts an (sonst waere die Reserve offen).
    s = socket.socket()
    s.settimeout(0.5)
    try:
        assert s.connect_ex(("127.0.0.1", 9)) != 0
    finally:
        s.close()
