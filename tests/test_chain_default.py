"""Die echte Standard-Kette (duckduckgo, dann searxng), ohne _chain wegzupatchen."""
import sys

import httpx
import pytest

from searchify.search import search_report
from searchify.search._common import SearchBackendError

search_pkg = sys.modules["searchify.search"]


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for k in ("SEARCH_BACKEND", "SEARCH_FALLBACK", "SEARXNG_URL", "SERPER_API_KEY", "TAVILY_API_KEY"):
        monkeypatch.delenv(k, raising=False)


def _backend(monkeypatch, name, fn):
    monkeypatch.setattr(search_pkg, "BACKENDS", {**search_pkg.BACKENDS, name: fn})


def _limited(q, n, **f):
    raise SearchBackendError("rate_limited: ddgs DDGSException")


def test_country_reaches_searxng_without_typeerror(monkeypatch):
    monkeypatch.setenv("SEARXNG_URL", "http://searx.test")
    _backend(monkeypatch, "duckduckgo", lambda q, n, **f: [])
    transport = httpx.MockTransport(lambda req: httpx.Response(200, json={"results": [
        {"url": "https://s.example/", "title": "S", "content": "c", "engine": "bing"}]}))
    real = httpx.Client
    monkeypatch.setattr(sys.modules["searchify.search.searxng"].httpx, "Client",
                        lambda **kw: real(transport=transport, **kw))
    r = search_report("q", country="DE", language="de")
    assert r["backend"] == "searxng" and r["results"][0]["url"] == "https://s.example/"


def test_zero_hits_without_searxng_url_is_not_an_error(monkeypatch):
    _backend(monkeypatch, "duckduckgo", lambda q, n, **f: [])
    assert search_report("q", country="DE") == {"results": [], "error": None, "backend": None}


def test_rate_limit_stays_visible_without_searxng_url(monkeypatch):
    _backend(monkeypatch, "duckduckgo", _limited)
    r = search_report("q")
    assert r["error"] == "duckduckgo: rate_limited: ddgs DDGSException"
    assert search_pkg.error_code(r["error"]) == "rate_limited"
    assert r["errors"] == [{"engine": "duckduckgo", "reason": "rate_limited"}]


def test_first_error_wins_over_later_one(monkeypatch):
    monkeypatch.setenv("SEARXNG_URL", "http://searx.test")

    def down(q, n, **f):
        raise SearchBackendError("SearXNG unreachable: ConnectError")

    _backend(monkeypatch, "duckduckgo", _limited)
    _backend(monkeypatch, "searxng", down)
    assert search_report("q")["error"].startswith("duckduckgo: rate_limited")


def test_clean_empty_answer_clears_earlier_error(monkeypatch):
    monkeypatch.setenv("SEARXNG_URL", "http://searx.test")
    _backend(monkeypatch, "duckduckgo", _limited)
    _backend(monkeypatch, "searxng", lambda q, n, **f: [])
    r = search_report("q")
    assert r["error"] is None and r["errors"][0]["engine"] == "duckduckgo"


def test_chain_skips_unconfigured_fallbacks(monkeypatch):
    assert search_pkg._chain() == ["duckduckgo"]
    monkeypatch.setenv("SEARXNG_URL", "http://searx.test")
    assert search_pkg._chain() == ["duckduckgo", "searxng"]
    monkeypatch.setenv("SEARCH_FALLBACK", "serper")
    assert search_pkg._chain() == ["duckduckgo"]
    monkeypatch.setenv("SERPER_API_KEY", "k")
    assert search_pkg._chain() == ["duckduckgo", "serper"]
