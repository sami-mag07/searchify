"""Fetch path: the User-Agent and the curl_cffi fallback, against a local server."""
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from searchify.extract import htmltext as h

PAGE = b"<html><body><p>Local page for the curl fallback test.</p></body></html>"


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - http.server API
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *args):
        pass


@pytest.fixture
def local_site(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    # treat the test host as checked and public, so the SSRF check lets it through
    monkeypatch.setattr(h, "_public_ips", lambda host: ["127.0.0.1"])
    yield f"http://localhost:{server.server_address[1]}/page"
    server.shutdown()


def test_user_agent_names_a_contact_url():
    # Wikimedia answers 403 to agents without one, except for pages in its edge cache
    assert "searchify" in h._USER_AGENT and "+https://" in h._USER_AGENT


def test_curl_fallback_really_fetches(local_site):
    pytest.importorskip("curl_cffi")
    assert h._fetch_url_curl(local_site, 5) == PAGE.decode()


def test_curl_fallback_drops_a_page_from_an_unchecked_address(local_site, monkeypatch, caplog):
    pytest.importorskip("curl_cffi")
    monkeypatch.setattr(h, "_connected_ip", lambda resp: "10.0.0.1")
    with caplog.at_level("WARNING"):
        assert h._fetch_url_curl(local_site, 5) is None
    assert "unchecked address" in caplog.text


@pytest.mark.parametrize("url", ["http://faß.example.com/", "http://a%2eb.example.com/",
                                 "http://x%61.example.com/", "http://user@example.com/"])
def test_curl_refuses_hosts_curl_would_read_differently(url):
    # curl decodes %-escapes and uses IDNA2008, so it would resolve a host we never checked
    assert not h._plain_url(url)


def test_curl_accepts_plain_hosts():
    assert h._plain_url("https://en.wikipedia.org/wiki/Ballon_d%27Or")
# Stand: 02.11.2026
