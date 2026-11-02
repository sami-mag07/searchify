"""Hostile servers against the httpx fetch path (Security-Review 03.11.2026).

Everything runs against a local server on 127.0.0.1; the SSRF check is patched to
treat it as public only inside the fixture."""
import gzip
import socket
import threading
import time
import tracemalloc
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpcore
import pytest

from searchify import reasons
from searchify.extract import htmltext as h
from searchify.extract import pdf as pdf_mod

BOMB_MB = 32
ROUTES = {}  # path -> (status, headers, body)
SEEN = {}  # path -> request headers


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802 - http.server API
        SEEN[self.path] = self.headers  # case-insensitive lookups
        status, headers, body = ROUTES.get(self.path, (404, {}, b""))
        try:
            self.send_response(status)
            for key, value in headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, *args):
        pass


@pytest.fixture
def site(monkeypatch):
    ROUTES.clear()
    SEEN.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True).start()
    monkeypatch.setattr(h, "_public_ips", lambda host: ["127.0.0.1"])
    yield f"http://localhost:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


@pytest.fixture(scope="module")
def bombs():
    html = b"<html><body>" + b" " * (BOMB_MB << 20)
    out = {"gzip": gzip.compress(html, 9)}
    out["gzip, gzip"] = gzip.compress(out["gzip"], 9)
    try:
        import brotli  # noqa: PLC0415 - optional
    except ImportError:
        return out
    out["br"] = brotli.compress(html, quality=5)
    return out


def _peak_mb(fn):
    tracemalloc.start()
    try:
        result = fn()
        return result, tracemalloc.get_traced_memory()[1] / (1 << 20)
    finally:
        tracemalloc.stop()


HTML = {"Content-Type": "text/html; charset=utf-8"}


def test_stacked_encodings_are_refused(site, bombs):
    ROUTES["/gz2"] = (200, {**HTML, "Content-Encoding": "gzip, gzip"}, bombs["gzip, gzip"])
    html, peak = _peak_mb(lambda: h.fetch_url(site + "/gz2", 10, 100_000))
    assert html is None and peak < 8
    assert h.fetch_failure() == "network error (unsupported content encoding)"
    assert reasons.code_for(h.fetch_failure()) == "unavailable"


def test_a_gzip_bomb_is_cut_at_the_limit_without_inflating_it(site, bombs):
    ROUTES["/bomb"] = (200, {**HTML, "Content-Encoding": "gzip"}, bombs["gzip"])
    html, peak = _peak_mb(lambda: h.fetch_url(site + "/bomb", 10, 100_000))
    assert len(html) == 100_000 and html.startswith("<html><body>")
    assert peak < 8, f"{peak:.0f} MB in memory for a 100 KB limit"


def test_a_brotli_bomb_is_cut_or_refused(site, bombs):
    if "br" not in bombs:
        pytest.skip("brotli not installed")
    ROUTES["/bomb"] = (200, {**HTML, "Content-Encoding": "br"}, bombs["br"])
    html, peak = _peak_mb(lambda: h.fetch_url(site + "/bomb", 10, 100_000))
    # a Brotli module with an output cap decodes it, an older one is refused
    assert html is None or len(html) == 100_000
    assert peak < 8, f"{peak:.0f} MB in memory for a 100 KB limit"


def test_only_encodings_with_an_output_cap_are_asked_for(site):
    ROUTES["/p"] = (200, HTML, b"<p>x</p>")
    h.fetch_url(site + "/p", 10, 100_000)
    asked = {v.strip() for v in SEEN["/p"]["Accept-Encoding"].split(",")}
    assert asked <= {"gzip", "deflate", "br"}
    assert ("br" in asked) == (h._BROTLI is not None)


def test_the_body_of_a_redirect_is_never_decoded(site, bombs):
    page = b"<html><body><p>Ziel nach der Umleitung.</p></body></html>"
    ROUTES["/moved"] = (302, {**HTML, "Location": "/target", "Content-Encoding": "gzip"}, bombs["gzip"])
    ROUTES["/target"] = (200, HTML, page)
    html, peak = _peak_mb(lambda: h.fetch_url(site + "/moved", 10, 100_000))
    assert html == page.decode()
    assert peak < 8, f"{peak:.0f} MB in memory for a redirect body"


@pytest.mark.parametrize("enc,pack", [
    ("gzip", lambda b: gzip.compress(b)),
    ("deflate", zlib.compress),
    ("deflate", lambda b: zlib.compress(b)[2:-4]),  # raw deflate without the zlib wrapper
    ("identity", lambda b: b),
])
def test_normal_encoded_pages_still_read_whole(site, enc, pack):
    page = b"<html><body>" + b"<p>Ein ganz normaler Absatz.</p>" * 20_000 + b"</body></html>"
    ROUTES["/p"] = (200, {**HTML, "Content-Encoding": enc}, pack(page))
    assert h.fetch_url(site + "/p", 10, 2_000_000) == page.decode()


def test_a_broken_gzip_stream_is_a_network_error(site):
    ROUTES["/bad"] = (200, {**HTML, "Content-Encoding": "gzip"}, b"\x1f\x8b\x08\x00garbage-not-gzip")
    assert h.fetch_url(site + "/bad", 10, 100_000) is None
    assert h.fetch_failure() == "network error (DecodingError)"


def test_decoder_pieces_stay_small(bombs):
    pieces = list(h._Inflate("gzip").feed(bombs["gzip"]))
    assert sum(map(len, pieces)) == (BOMB_MB << 20) + len(b"<html><body>")
    assert max(map(len, pieces)) <= h._DECODE_STEP
    if h._BROTLI is not None:
        pieces = list(h._Brotli(h._BROTLI).feed(bombs["br"]))
        assert sum(map(len, pieces)) == (BOMB_MB << 20) + len(b"<html><body>")
        assert max(map(len, pieces)) <= 2 * h._DECODE_STEP


def test_pdf_download_asks_for_identity_and_refuses_encoded_bodies(site):
    pdf = b"%PDF-1.4\n" + b"0" * 5000
    ROUTES["/a.pdf"] = (200, {"Content-Type": "application/pdf", "Content-Encoding": "gzip"},
                        gzip.compress(pdf))
    assert pdf_mod._download(site + "/a.pdf") == (None, "network error (unsupported content encoding)")
    assert SEEN["/a.pdf"]["Accept-Encoding"] == "identity"
    ROUTES["/b.pdf"] = (200, {"Content-Type": "application/pdf"}, pdf)
    assert pdf_mod._download(site + "/b.pdf") == (pdf, None)


def test_the_browser_proxy_caps_a_bomb_too(site, bombs, monkeypatch):
    from searchify.extract import render  # noqa: PLC0415

    monkeypatch.setattr(render, "_MAX_BODY", 100_000)
    ROUTES["/bomb"] = (200, {**HTML, "Content-Encoding": "gzip"}, bombs["gzip"])
    browser = {"Accept-Encoding": "gzip, deflate, br, zstd", "user-agent": "Chrome"}
    got, peak = _peak_mb(lambda: render._fetch("GET", site + "/bomb", browser, None))
    assert got[0] == 200 and len(got[2]) == 100_000
    assert peak < 8, f"{peak:.0f} MB in memory for a 100 KB limit"
    assert SEEN["/bomb"]["Accept-Encoding"] == h.ACCEPT_ENCODING


# ---------------------------------------------------------------- overall deadline

DRIP = b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nX-Pad: aaaaaaaaaaaa\r\n\r\n<p>hi</p>"


@pytest.fixture
def dripping(monkeypatch):
    """A server that sends its status line and headers one byte every 50 ms (3.5 s
    in all): each gap is far below any read timeout."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    stop = threading.Event()

    def drip(conn):
        try:
            conn.recv(65536)
            for byte in DRIP:
                if stop.is_set():
                    return
                conn.sendall(bytes([byte]))
                time.sleep(0.05)
        except OSError:
            pass
        finally:
            conn.close()

    def serve():
        while not stop.is_set():
            try:
                conn, _ = sock.accept()
            except OSError:
                return
            threading.Thread(target=drip, args=(conn,), daemon=True).start()

    threading.Thread(target=serve, daemon=True).start()
    monkeypatch.setattr(h, "_public_ips", lambda host: ["127.0.0.1"])
    yield f"http://localhost:{sock.getsockname()[1]}"
    stop.set()
    sock.close()


def test_dripped_headers_end_at_the_overall_timeout(dripping):
    t = time.monotonic()
    assert h.fetch_url(dripping + "/", timeout=0.8) is None
    took = time.monotonic() - t
    assert took < 1.6, f"a 0.8 s budget took {took:.1f} s"
    assert h.fetch_failure() == "timeout"


def test_dripped_headers_end_the_pdf_download_in_time(dripping, monkeypatch):
    monkeypatch.setattr(pdf_mod, "DOWNLOAD_TIMEOUT_S", 0.8)
    t = time.monotonic()
    assert pdf_mod._download(dripping + "/x.pdf") == (None, "timeout")
    took = time.monotonic() - t
    assert took < 1.6, f"a 0.8 s budget took {took:.1f} s"


def test_the_deadline_is_per_thread_and_restored():
    assert getattr(h._TLS, "deadline", None) is None
    with h.overall_deadline(5) as outer:
        with h.overall_deadline(60) as inner:
            assert inner == outer  # an inner call never extends the outer budget
        assert h._TLS.deadline == outer
    assert h._TLS.deadline is None


def test_at_most_four_addresses_are_tried(monkeypatch):
    tried = []

    def refuse(self, host, port, timeout=None, local_address=None, socket_options=None):
        tried.append(host)
        raise httpcore.ConnectError("refused")

    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", refuse)
    monkeypatch.setattr(h, "_public_ips", lambda host: [f"203.0.113.{i}" for i in range(1, 11)])
    with pytest.raises(httpcore.ConnectError):
        h._PinnedBackend().connect_tcp("many.example", 443, timeout=1)
    assert tried == ["203.0.113.1", "203.0.113.2", "203.0.113.3", "203.0.113.4"]
