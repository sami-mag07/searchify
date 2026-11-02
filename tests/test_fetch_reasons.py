"""Warum ein Abruf scheiterte: die echten _fetch_live-Pfade ueber httpx.MockTransport."""
from types import SimpleNamespace

import httpx
import pytest

from searchify import reasons
from searchify.extract import htmltext as h

SETTINGS = SimpleNamespace(fetch_client="httpx", fetch_fallback=True)


def _serve(monkeypatch, handler):
    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True,
                          event_hooks={"request": [h._block_internal]})
    monkeypatch.setattr(h, "_client", lambda: client)
    monkeypatch.setattr(h, "_host_is_public", lambda host: host not in ("127.0.0.1", "169.254.169.254"))
    h._reset_failure()


def _fetch(url="https://site.example/", timeout=5.0):
    return h._fetch_live(url, timeout, 10_000, SETTINGS)


def test_blocked_internal_address_says_so(monkeypatch):
    _serve(monkeypatch, lambda req: httpx.Response(200, text="<p>x</p>"))
    assert _fetch("http://127.0.0.1/admin") is None
    assert h.fetch_failure() == "address not allowed"
    assert reasons.code_for(h.fetch_failure()) == "address_blocked"


def test_redirect_to_an_internal_address_says_so(monkeypatch):
    def handler(req):
        if req.url.host == "site.example":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest"})
        return httpx.Response(200, text="secret")

    _serve(monkeypatch, handler)
    assert _fetch() is None
    assert h.fetch_failure() == "address not allowed"


def test_other_network_errors_keep_their_text(monkeypatch):
    def boom(req):
        raise httpx.ConnectError("down", request=req)

    _serve(monkeypatch, boom)
    assert _fetch() is None
    assert h.fetch_failure() == "network error (ConnectError)"


def _blocked_then(monkeypatch, curl_failure):
    _serve(monkeypatch, lambda req: httpx.Response(403, text="no"))

    def curl(url, timeout, limit):
        if curl_failure:
            h._note_failure(curl_failure)
        return None

    monkeypatch.setattr(h, "_fetch_url_curl", curl)


def test_retry_also_blocked_is_summed_up(monkeypatch):
    _blocked_then(monkeypatch, "HTTP 429")
    assert _fetch() is None
    assert h.fetch_failure() == "HTTP 403, also after retry"


def test_retry_failing_for_another_reason_keeps_that_reason(monkeypatch):
    _blocked_then(monkeypatch, "network error (ConnectError)")
    assert _fetch() is None
    assert h.fetch_failure() == "HTTP 403, retry: network error (ConnectError)"
    assert reasons.code_for(h.fetch_failure()) == "blocked"


def test_retry_blocked_with_the_same_status(monkeypatch):
    _blocked_then(monkeypatch, "HTTP 403")
    assert _fetch() is None
    assert h.fetch_failure() == "HTTP 403, also after retry"


def test_no_time_left_for_the_retry_keeps_the_403(monkeypatch):
    _blocked_then(monkeypatch, "HTTP 200")
    assert _fetch(timeout=0.1) is None
    assert h.fetch_failure() == "HTTP 403"


@pytest.mark.parametrize("status", [300, 304])
def test_final_3xx_without_location_is_an_error(monkeypatch, status):
    _serve(monkeypatch, lambda req: httpx.Response(status, text="<p>moved</p>"))
    assert _fetch() is None
    assert h.fetch_failure() == f"HTTP {status}"
