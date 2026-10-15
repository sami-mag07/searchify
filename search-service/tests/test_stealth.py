"""Camoufox-Stufe (app/compat/stealth.py) und ihr SSRF-Proxy (stealth_proxy.py).

Proxy-Tests laufen ohne Browser. Der Browser-Test braucht CAMOUFOX_PYTHON (eigenes
venv, docs/camoufox.md) und wird sonst uebersprungen: Eine "oeffentliche" Seite
(public.test, im Proxy auf einen lokalen Server umgebogen) versucht auf allen Wegen
interne Adressen zu erreichen. Ein zweiter lokaler Server zaehlt jeden Treffer.
"""

import http.server
import os
import socket
import threading

import pytest

from app.compat import reader as reader_mod
from app.compat import stealth as stealth_mod
from app.compat import stealth_proxy as proxy_mod


def _server(body_for):
    hits = []

    class H(http.server.BaseHTTPRequestHandler):
        def _any(self):
            hits.append((self.path, dict(self.headers)))
            code, headers, body = body_for(self.path)
            self.send_response(code)
            for k, v in headers.items():
                self.send_header(k, v)
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = _any

        def log_message(self, *a):
            pass

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, hits


@pytest.fixture()
def internal():
    srv, hits = _server(lambda p: (200, {"content-type": "text/html"}, b"<p>GEHEIM intern</p>"))
    yield srv.server_address[1], hits
    srv.shutdown()


def _ask(port, raw: bytes) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as s:
        s.sendall(raw)
        out = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                return out
            out += chunk


def test_resolve_public_nur_oeffentlich_und_80_443():
    for host, port in [("127.0.0.1", 443), ("10.1.2.3", 80), ("169.254.169.254", 80), ("::1", 443),
                       ("[::1]", 443), ("localhost", 443), ("192.168.0.1", 443), ("1.1.1.1", 22),
                       ("1.1.1.1", 8000), ("", 443)]:
        assert proxy_mod.resolve_public(host, port) is None, (host, port)
    assert proxy_mod.resolve_public("1.1.1.1", 443) == "1.1.1.1"


def test_proxy_blockt_interne_ziele(internal):
    port, hits = internal
    with proxy_mod.SsrfProxy() as proxy:
        for raw in [f"CONNECT 127.0.0.1:{port} HTTP/1.1\r\nHost: x\r\n\r\n",
                    "CONNECT localhost:443 HTTP/1.1\r\n\r\n",
                    "CONNECT [::1]:443 HTTP/1.1\r\n\r\n",
                    f"GET http://127.0.0.1:{port}/ HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n",
                    "GET http://169.254.169.254/latest/meta-data/ HTTP/1.1\r\n\r\n",
                    f"GET / HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n\r\n",
                    "GET file:///etc/passwd HTTP/1.1\r\n\r\n"]:
            assert _ask(proxy.port, raw.encode()).startswith(b"HTTP/1.1 403"), raw
        assert len(proxy.blocked) == 7
    assert hits == []


def test_proxy_leitet_oeffentliche_ziele_weiter(internal, monkeypatch):
    port, hits = internal
    real = proxy_mod.resolve_public
    monkeypatch.setattr(proxy_mod, "resolve_public",
                        lambda h, p: "127.0.0.1" if h == "public.test" else real(h, p))
    with proxy_mod.SsrfProxy() as proxy:
        out = _ask(proxy.port, f"GET http://public.test:{port}/a?b=1 HTTP/1.1\r\nHost: public.test\r\n"
                               "Proxy-Connection: keep-alive\r\n\r\n".encode())
        assert b"GEHEIM" in out
        out = _ask(proxy.port, f"CONNECT public.test:{port} HTTP/1.1\r\n\r\n"
                               "GET /tunnel HTTP/1.1\r\nHost: public.test\r\nConnection: close\r\n\r\n".encode())
        assert out.startswith(b"HTTP/1.1 200 Connection established") and b"GEHEIM" in out
    assert [h[0] for h in hits] == ["/a?b=1", "/tunnel"]
    assert hits[0][1].get("Connection") == "close" and "Proxy-Connection" not in hits[0][1]


def test_stealth_aus_ohne_camoufox_python(monkeypatch):
    monkeypatch.setenv("READER_STEALTH", "1")
    monkeypatch.setenv("CAMOUFOX_PYTHON", "/gibt/es/nicht/python")
    assert not stealth_mod.enabled()
    assert stealth_mod.try_stealth("https://example.com/", 10) == ("off", None)


def test_stealth_start_url_nur_oeffentlich(monkeypatch):
    monkeypatch.setattr(stealth_mod, "enabled", lambda: True)
    for url in ("http://127.0.0.1:8000/", "file:///etc/passwd", "http://localhost/", "data:text/html,x"):
        assert stealth_mod.try_stealth(url, 10) == ("blocked", None), url


def _fails(*urls):
    return [{"url": u, "error": "blockiert"} for u in urls]


def test_reader_nimmt_camoufox_nach_chromium(monkeypatch):
    text = "<html><body><article>" + "<p>" + "Wort " * 120 + "</p></article></body></html>"
    calls = []
    monkeypatch.setattr(reader_mod.render_mod, "enabled", lambda: True)
    monkeypatch.setattr(reader_mod.stealth_mod, "enabled", lambda: True)
    monkeypatch.setattr(reader_mod.render_mod, "try_render",
                        lambda url, timeout=0: calls.append(("chromium", url)) or ("tried", "<p>Just a moment</p>"))
    monkeypatch.setattr(reader_mod.stealth_mod, "try_stealth",
                        lambda url, timeout=0: calls.append(("camoufox", url)) or ("tried", text))
    docs, still = reader_mod._render_failed([], _fails("https://a.example/", "https://b.example/"),
                                            "markdown", ["https://a.example/", "https://b.example/"])
    assert [d["url"] for d in docs] == ["https://a.example/"]
    assert [f["url"] for f in still] == ["https://b.example/"]  # Camoufox nur einmal je Anfrage
    assert calls == [("chromium", "https://a.example/"), ("camoufox", "https://a.example/"),
                     ("chromium", "https://b.example/")]


def test_reader_ohne_restzeit_kein_camoufox(monkeypatch):
    import time
    monkeypatch.setattr(reader_mod.render_mod, "enabled", lambda: False)
    monkeypatch.setattr(reader_mod.stealth_mod, "enabled", lambda: True)
    monkeypatch.setattr(reader_mod.stealth_mod, "try_stealth", lambda *a, **k: pytest.fail("kein Aufruf"))
    _docs, still = reader_mod._render_failed([], _fails("https://a.example/"), "markdown",
                                             ["https://a.example/"], deadline=time.monotonic() + 3)
    assert len(still) == 1


@pytest.fixture()
def camoufox_on(monkeypatch):
    py = os.environ.get("CAMOUFOX_PYTHON", "")
    if not (py and os.path.isfile(py)):
        pytest.skip("CAMOUFOX_PYTHON nicht gesetzt")
    monkeypatch.setenv("READER_STEALTH", "1")


def test_camoufox_erreicht_nichts_internes(camoufox_on, internal, monkeypatch):
    iport, hits = internal
    attempts = (
        f'<img src="http://127.0.0.1:{iport}/img"><iframe src="http://127.0.0.1:{iport}/iframe"></iframe>'
        f'<iframe src="http://localhost:{iport}/iframe2"></iframe>'
        f'<link rel="prefetch" href="http://127.0.0.1:{iport}/prefetch">'
        f'<script>fetch("http://127.0.0.1:{iport}/fetch").catch(()=>{{}});'
        f'fetch("http://169.254.169.254/latest/meta-data/").catch(()=>{{}});'
        f'try{{new WebSocket("ws://127.0.0.1:{iport}/ws")}}catch(e){{}}'
        f'navigator.sendBeacon("http://127.0.0.1:{iport}/beacon","x");'
        f'window.open("http://127.0.0.1:{iport}/popup");'
        f'new Worker(URL.createObjectURL(new Blob(["fetch(\\"http://127.0.0.1:{iport}/worker\\")"])));</script>'
    )
    page = ("<html><body><article><p>" + "Oeffentlicher Inhalt " * 60 + "</p></article>"
            + attempts + "</body></html>").encode()

    def public(path):
        if path.startswith("/redir"):
            return 302, {"location": f"http://127.0.0.1:{iport}/redir-ziel"}, b""
        if path.startswith("/jsredir"):
            return 200, {"content-type": "text/html"}, (
                f'<script>location.href="http://127.0.0.1:{iport}/js-ziel"</script>').encode()
        return 200, {"content-type": "text/html"}, page

    pub, _pub_hits = _server(public)
    pport = pub.server_address[1]
    real = proxy_mod.resolve_public
    monkeypatch.setattr(proxy_mod, "resolve_public",
                        lambda h, p: "127.0.0.1" if h == "public.test" and p == pport else real(h, p))
    real_allowed = stealth_mod.render_mod.url_allowed
    monkeypatch.setattr(stealth_mod.render_mod, "url_allowed",
                        lambda u: u.startswith(f"http://public.test:{pport}/") or real_allowed(u))
    try:
        status, html = stealth_mod.try_stealth(f"http://public.test:{pport}/", 25)
        assert status == "tried" and html and "Oeffentlicher Inhalt" in html
        assert stealth_mod.try_stealth(f"http://public.test:{pport}/redir", 25)[1] is None
        stealth_mod.try_stealth(f"http://public.test:{pport}/jsredir", 25)
    finally:
        pub.shutdown()
    assert hits == []


def test_ip_is_public_sperrt_eingebettete_interne_ipv4():
    from app import extract as extract_mod
    for raw in ("64:ff9b::7f00:1", "64:ff9b::c0a8:1", "64:ff9b:1::a00:1", "::ffff:127.0.0.1",
                "2002:7f00:1::1", "2002:c0a8:101::1", "fec0::1", "::7f00:1"):
        assert not extract_mod.ip_is_public(raw), raw
        assert proxy_mod.resolve_public(raw, 443) is None, raw
    for raw in ("1.1.1.1", "2606:4700:4700::1111", "64:ff9b::101:101"):
        assert extract_mod.ip_is_public(raw), raw


def test_proxy_genau_eine_anfrage_je_klartext_verbindung(monkeypatch):
    """Ein Server, der Connection: close ignoriert, darf keine zweite Anfrage bekommen."""
    got = []
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    sport = srv.getsockname()[1]

    def serve():
        conn, _ = srv.accept()
        conn.settimeout(2)
        data = b""
        try:
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                data += chunk
                if data.count(b"\r\n\r\n") == 1:
                    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
        except OSError:
            pass
        got.append(data)
        conn.close()

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    real = proxy_mod.resolve_public
    monkeypatch.setattr(proxy_mod, "resolve_public",
                        lambda h, p: "127.0.0.1" if h == "evil.test" else real(h, p))
    with proxy_mod.SsrfProxy() as proxy:
        raw = (f"GET http://evil.test:{sport}/eins HTTP/1.1\r\nHost: evil.test\r\n\r\n"
               f"GET http://127.0.0.1/admin HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n"
               f"GET http://bank.example/ HTTP/1.1\r\nHost: bank.example\r\n\r\n").encode()
        out = _ask(proxy.port, raw)
    t.join(5)
    srv.close()
    assert out.endswith(b"ok")
    assert got and got[0].count(b"GET ") == 1 and b"/admin" not in got[0]


def test_proxy_lehnt_chunked_body_ab(internal, monkeypatch):
    port, hits = internal
    real = proxy_mod.resolve_public
    monkeypatch.setattr(proxy_mod, "resolve_public",
                        lambda h, p: "127.0.0.1" if h == "public.test" else real(h, p))
    with proxy_mod.SsrfProxy() as proxy:
        out = _ask(proxy.port, f"POST http://public.test:{port}/x HTTP/1.1\r\nHost: public.test\r\n"
                               "Transfer-Encoding: chunked\r\n\r\n0\r\n\r\n".encode())
    assert out.startswith(b"HTTP/1.1 400") and hits == []
