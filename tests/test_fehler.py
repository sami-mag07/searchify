"""Reason codes, thin pages, status output and the worker-crash pool reset (no network)."""
from concurrent.futures import Future
from concurrent.futures.process import BrokenProcessPool

from searchify.extract import htmltext, pages

HTML = "<html><body><article><p>" + " ".join(["wort"] * 80) + "</p></article></body></html>"


class _DeadPool:
    """A pool whose worker died: every job comes back as BrokenProcessPool."""

    def submit(self, fn, *args, **kwargs):
        fut = Future()
        fut.set_exception(BrokenProcessPool("worker died"))
        return fut


def test_read_one_replaces_a_broken_extract_pool(monkeypatch):
    dead = _DeadPool()
    monkeypatch.setattr(htmltext, "_EXTRACT_POOL", dead)
    monkeypatch.setattr(htmltext, "fetch_url", lambda url, timeout, max_bytes=None: HTML)
    monkeypatch.setattr(pages, "_docs", lambda: htmltext.PageCache(10, 60))
    doc, err = pages._read_one("https://a.example/", "text", 5.0)
    assert err is None and doc["content"]  # the page is still read, in a thread
    assert htmltext._EXTRACT_POOL is not dead  # the next call starts a fresh pool


# ------------------------------------------------------------ reason codes
import json
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from searchify import extract_report, reasons
from searchify.extract import reader


@pytest.mark.parametrize("text, code", [
    ("HTTP 403", "blocked"),
    ("HTTP 403, also after retry", "blocked"),
    ("HTTP 429", "rate_limited"),
    ("HTTP 503", "unavailable"),
    ("HTTP 404", "unavailable"),
    ("timeout", "timeout"),
    ("Zeitlimit", "timeout"),
    ("Extraktion zu langsam", "timeout"),
    ("not HTML: application/pdf", "not_html"),
    ("address not allowed", "address_blocked"),
    ("address not public or not resolvable", "address_blocked"),
    ("network error (ConnectError)", "unavailable"),
    ("too many redirects", "unavailable"),
    ("Kein Haupttext gefunden", "no_main_text"),
    ("Extraktion fehlgeschlagen (ValueError)", "extract_failed"),
    ("Zu wenig Text", "thin_content"),
    ("something nobody wrote down", "extract_failed"),
    (None, "extract_failed"),
])
def test_reason_code_mapping(text, code):
    assert reasons.code_for(text) == code
    assert code in reasons.REASONS


def test_search_errors_map_to_codes_without_exception_text():
    assert reasons.parse_search_error("duckduckgo: ddgs error: RatelimitException") == {
        "engine": "duckduckgo", "reason": "rate_limited"}
    assert reasons.parse_search_error("serper: Serper error 429: slow down") == {
        "engine": "serper", "reason": "rate_limited"}
    assert reasons.parse_search_error("searxng: SearXNG unreachable: ConnectError") == {
        "engine": "searxng", "reason": "unavailable"}
    assert reasons.parse_search_error("weird: boom")["engine"] == ""


def test_failed_pages_carry_error_text_and_reason(monkeypatch):
    def no_page(url, timeout, max_bytes=None):
        htmltext._note_failure("HTTP 429")
        return None
    monkeypatch.setattr(htmltext, "fetch_url", no_page)
    monkeypatch.setattr(pages, "_docs", lambda: htmltext.PageCache(10, 60))
    docs, failed = pages.read_pages(["https://a.example/"])
    assert docs == []
    assert failed == [{"url": "https://a.example/", "error": "HTTP 429", "reason": "rate_limited"}]


def test_progress_is_reported_per_page(monkeypatch):
    def fetch(url, timeout, max_bytes=None):
        if "bad" in url:
            htmltext._note_failure("HTTP 403")
            return None
        return HTML
    monkeypatch.setattr(htmltext, "fetch_url", fetch)
    monkeypatch.setattr(htmltext, "_extract_pool", lambda: ThreadPoolExecutor(1))
    monkeypatch.setattr(pages, "_docs", lambda: htmltext.PageCache(10, 60))
    seen = []
    pages.read_pages(["https://good.example/", "https://bad.example/"],
                     progress=lambda *a: seen.append(a))
    assert sorted(seen)[-1] == (2, 2, 1, {"blocked": 1})


# ------------------------------------------------------------ thin pages
THIN_HTML = "<html><body><p>Just a moment... verifying you are human.</p></body></html>"


def _browser_stages(monkeypatch, enabled=True):
    # the browser stages are on, but every one of them fails
    monkeypatch.setattr(reader.render_mod, "enabled", lambda: enabled)
    monkeypatch.setattr(reader.stealth_mod, "enabled", lambda: enabled)
    monkeypatch.setattr(reader.render_mod, "try_render", lambda *a, **k: ("failed", None))
    monkeypatch.setattr(reader.render_mod, "try_render_direct", lambda *a, **k: ("failed", None))
    monkeypatch.setattr(reader.stealth_mod, "try_stealth", lambda *a, **k: ("failed", None))
    monkeypatch.setattr(htmltext, "_extract_pool", lambda: ThreadPoolExecutor(1))


def test_thin_page_counts_as_read_but_thin_without_touching_the_document(monkeypatch):
    _browser_stages(monkeypatch)
    cache = htmltext.PageCache(10, 60)
    monkeypatch.setattr(pages, "_docs", lambda: cache)
    monkeypatch.setattr(htmltext, "fetch_url", lambda url, timeout, max_bytes=None: THIN_HTML)
    url = "https://wall.example/"
    docs, failed, thin = reader.read_pages_ex([url], "text")
    assert failed == [] and len(docs) == 1
    assert thin == {url: "challenge"}
    assert "reason" not in docs[0] and "thin" not in docs[0]
    cached = cache.get(f"reader-text|{url}")
    assert set(cached) == set(docs[0])  # no extra key landed in the shared entry

    report = extract_report([url], "text")
    assert report["status"] == {"read": 1, "failed": 0, "thin": 1, "reasons": {"challenge": 1}}


def test_plain_short_page_is_thin_content_not_challenge(monkeypatch):
    _browser_stages(monkeypatch)
    monkeypatch.setattr(pages, "_docs", lambda: htmltext.PageCache(10, 60))
    monkeypatch.setattr(htmltext, "fetch_url",
                        lambda url, timeout, max_bytes=None: "<html><body><p>Hello there friend.</p></body></html>")
    _docs, _failed, thin = reader.read_pages_ex(["https://tiny.example/"], "text")
    assert thin == {"https://tiny.example/": "thin_content"}


def test_without_browser_stages_nothing_is_flagged(monkeypatch):
    _browser_stages(monkeypatch, enabled=False)
    monkeypatch.setattr(pages, "_docs", lambda: htmltext.PageCache(10, 60))
    monkeypatch.setattr(htmltext, "fetch_url", lambda url, timeout, max_bytes=None: THIN_HTML)
    _docs, _failed, thin = reader.read_pages_ex(["https://wall.example/"], "text")
    assert thin == {}


# ------------------------------------------------------------ /extract and /search
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from searchify import service  # noqa: E402

client = TestClient(service.app)


def _clean_pipeline(monkeypatch):
    monkeypatch.setattr(reader.render_mod, "enabled", lambda: False)
    monkeypatch.setattr(reader.stealth_mod, "enabled", lambda: False)
    monkeypatch.setattr(pages, "_docs", lambda: htmltext.PageCache(10, 60))
    monkeypatch.setattr(htmltext, "_extract_pool", lambda: ThreadPoolExecutor(1))

    def fetch(url, timeout, max_bytes=None):
        if "bad" in url:
            htmltext._note_failure("HTTP 403")
            return None
        return HTML
    monkeypatch.setattr(htmltext, "fetch_url", fetch)


def test_golden_extract_body_is_unchanged_on_full_success(monkeypatch):
    _clean_pipeline(monkeypatch)
    urls = ["https://good.example/a", "https://good.example/b"]
    old_docs, old_failed = reader.read_pages(urls, "text")  # the unchanged public function
    assert old_failed == []
    expected = json.dumps({"results": old_docs, "failed": []}, ensure_ascii=False,
                          allow_nan=False, separators=(",", ":")).encode()
    resp = client.post("/extract", json={"urls": urls})
    assert resp.status_code == 200
    assert resp.content == expected  # byte for byte what the endpoint returned before
    assert "x-searchify-status" not in resp.headers


def test_extract_status_appears_only_when_something_went_wrong(monkeypatch):
    _clean_pipeline(monkeypatch)
    resp = client.post("/extract", json={"urls": ["https://good.example/a", "https://bad.example/"]})
    body = resp.json()
    assert body["status"] == {"read": 1, "failed": 1, "thin": 0, "reasons": {"blocked": 1}}
    assert body["failed"] == [{"url": "https://bad.example/", "error": "HTTP 403"}]
    assert resp.headers["x-searchify-status"] == "blocked=1"
    detail = client.post("/extract", json={"urls": ["https://bad.example/"], "detail": True}).json()
    assert detail["failed"] == [{"url": "https://bad.example/", "error": "HTTP 403", "reason": "blocked"}]


def test_status_header_never_carries_free_text(monkeypatch):
    evil = {"url": "https://x.example/", "error": "boom\r\nX-Evil: 1", "reason": "x\r\nX-Evil: 1"}
    monkeypatch.setattr(service, "read_pages_ex", lambda urls, fmt, timeout=None, **_: ([], [evil], {}))
    resp = client.post("/extract", json={"urls": ["https://x.example/"]})
    assert resp.status_code == 200
    assert "x-evil" not in resp.headers
    assert "x-searchify-status" not in resp.headers  # unknown code: dropped, not echoed


def _search(monkeypatch, report):
    monkeypatch.setattr(service, "search_report", lambda q, n, backend=None, **f: report)
    return client.post("/search", json={"query": "q"})


def test_search_status_only_for_an_empty_list_after_a_backend_error(monkeypatch):
    err = [{"engine": "duckduckgo", "reason": "rate_limited"}]
    empty = _search(monkeypatch, {"results": [], "error": "duckduckgo: ddgs error: RatelimitException",
                                  "backend": None, "errors": err})
    assert empty.json()["status"] == {"reason": "rate_limited", "engine": "duckduckgo"}
    assert empty.headers["x-searchify-status"] == "duckduckgo=rate_limited"
    assert "Ratelimit" not in empty.headers["x-searchify-status"]

    hit = [{"title": "t", "url": "https://x", "content": "", "engine": "searxng"}]
    fallback = _search(monkeypatch, {"results": hit, "error": None, "backend": "searxng", "errors": err})
    assert "status" not in fallback.json()  # a fallback delivered: nothing in the body
    assert fallback.headers["x-searchify-status"] == "duckduckgo=rate_limited"

    plain = _search(monkeypatch, {"results": [], "error": None, "backend": None})
    assert plain.json() == {"results": [], "error": None, "backend": None}
    assert "x-searchify-status" not in plain.headers


# ------------------------------------------------------------ Python API
def test_extract_report_shape(monkeypatch):
    _clean_pipeline(monkeypatch)
    rep = extract_report(["https://good.example/a", "https://bad.example/"])
    assert set(rep) == {"results", "failed", "status"}
    assert rep["failed"] == [{"url": "https://bad.example/", "error": "HTTP 403", "reason": "blocked"}]
    assert rep["status"] == {"read": 1, "failed": 1, "thin": 0, "reasons": {"blocked": 1}}


# ------------------------------------------------------------ CLI ticker
from searchify import cli  # noqa: E402


class _Tty:
    def __init__(self, tty):
        self._tty = tty

    def isatty(self):
        return self._tty


@pytest.mark.parametrize("stdin, stderr, env, quiet, allowed", [
    (True, True, {}, False, True),
    (False, True, {}, False, False),   # piped input: an agent or a script
    (True, False, {}, False, False),   # stderr captured
    (True, True, {"CI": "true"}, False, False),
    (True, True, {}, True, False),     # --quiet
])
def test_ticker_only_at_a_real_terminal(monkeypatch, stdin, stderr, env, quiet, allowed):
    for var in cli._CI_VARS:
        monkeypatch.delenv(var, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(sys, "stdin", _Tty(stdin))
    monkeypatch.setattr(sys, "stderr", _Tty(stderr))
    assert cli._ticker_allowed(quiet) is allowed


def test_cli_extract_prints_no_ticker_when_piped(monkeypatch):
    from typer.testing import CliRunner

    seen = {}

    def fake(urls, fmt, timeout=None, progress=None, **_):
        seen["progress"] = progress
        return [], [{"url": "https://b.com", "error": "HTTP 429"}], {}
    monkeypatch.setattr(sys.modules["searchify.extract"], "read_pages_ex", fake)
    result = CliRunner().invoke(cli.app, ["extract", "https://b.com"])
    assert seen["progress"] is None  # CliRunner is no TTY
    assert json.loads(result.stdout)["failed"][0]["reason"] == "rate_limited"
