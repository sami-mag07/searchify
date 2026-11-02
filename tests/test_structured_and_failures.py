"""Structured data on documents, failure reasons and the stage field (no network)."""
import json

import pytest

from searchify.extract import htmltext, pages, reader
from searchify.search import SearchBackendError, search_report

HTML = """<html><head>
<title>Era 100 | Shop</title>
<meta property="og:title" content="Sonos Era 100">
<meta property="og:price:amount" content="249.00">
<meta name="twitter:card" content="summary">
<meta name="description" content="A speaker.">
<script type="application/ld+json">
{"@context": "https://schema.org", "@type": "Product", "name": "Sonos Era 100",
 "gtin13": "8717755778000",
 "offers": {"@type": "Offer", "price": "249.00", "priceCurrency": "EUR", "availability": "https://schema.org/InStock"}}
</script>
<script type="application/ld+json">not json at all</script>
</head><body><article><h1>Sonos Era 100</h1>
<p>The Era 100 is a compact speaker with a bigger sound than the One. It plays over Wi-Fi and Bluetooth
and fits on a shelf. The price is 249,00 euro and it ships in two colours. More words so the extractor
keeps the paragraph as main text of the page without doubt.</p></article></body></html>"""


def test_document_carries_json_ld_and_meta():
    doc = reader.reader_document(HTML, "https://shop.example/era-100", "text")
    s = doc["structured"]
    assert s["json_ld"][0]["@type"] == "Product"
    assert s["json_ld"][0]["offers"]["price"] == "249.00"
    assert len(s["json_ld"]) == 1  # the broken block is skipped, not fatal
    assert s["meta"]["og:title"] == "Sonos Era 100"
    assert s["meta"]["og:price:amount"] == "249.00"
    assert s["meta"]["twitter:card"] == "summary"
    assert "description" not in s["meta"]  # plain meta names stay in the document fields


def test_document_without_structured_data_leaves_the_key_out():
    doc = reader.reader_document("<html><body><p>Just a paragraph of text here.</p></body></html>", "", "text")
    assert "structured" not in doc


def test_fetch_failure_reason_is_reported(monkeypatch):
    def no_page(url, timeout, max_bytes=None):
        htmltext._note_failure("HTTP 403")
        return None
    monkeypatch.setattr(htmltext, "fetch_url", no_page)
    monkeypatch.setattr(pages, "_docs", lambda: htmltext.PageCache(10, 60))
    doc, err = pages._read_one("https://blocked.example/", "text", 5.0)
    assert doc is None
    assert err == "HTTP 403"


def test_fetch_failure_reason_resets_per_fetch(monkeypatch):
    htmltext._note_failure("HTTP 403")
    htmltext._reset_failure()
    assert htmltext.fetch_failure() is None


def test_static_documents_carry_stage(monkeypatch):
    doc = {"url": "https://a.example/", "content": " ".join(["word"] * 60)}
    monkeypatch.setattr(pages, "read_pages", lambda *a, **k: ([dict(doc)], []))
    monkeypatch.setattr(reader.render_mod, "enabled", lambda: False)
    monkeypatch.setattr(reader.stealth_mod, "enabled", lambda: False)
    docs, failed = reader.read_pages(["https://a.example/"], "text")
    assert docs[0]["stage"] == "static" and failed == []


def test_service_extract_returns_failed_with_reasons(monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from searchify import service

    ok = {"url": "https://a.example/", "title": "A", "content": "text", "stage": "static"}
    monkeypatch.setattr(service, "read_pages_ex",
                        lambda urls, fmt, timeout=None, **_: ([ok], [{"url": "https://b.example/", "error": "HTTP 403", "static": {"x": 1}}], {}))
    out = TestClient(service.app).post("/extract", json={"urls": ["https://a.example/", "https://b.example/"]}).json()
    assert out["results"] == [ok]
    assert out["failed"] == [{"url": "https://b.example/", "error": "HTTP 403"}]


def test_search_report_tells_rate_limit_from_no_hits(monkeypatch):
    import sys

    search_pkg = sys.modules["searchify.search"]

    def limited(q, n, **f):
        raise SearchBackendError("ddgs error: RatelimitException")

    monkeypatch.setattr(search_pkg, "BACKENDS", {"duckduckgo": limited})
    monkeypatch.setattr(search_pkg, "_chain", lambda: ["duckduckgo"])
    monkeypatch.setattr(search_pkg, "get_settings", lambda: type("S", (), {"tavily_api_key": ""})())
    r = search_report("anything")
    assert r == {"results": [], "error": "duckduckgo: ddgs error: RatelimitException", "backend": None,
                 "errors": [{"engine": "duckduckgo", "reason": "rate_limited"}]}

    monkeypatch.setattr(search_pkg, "BACKENDS", {"duckduckgo": lambda q, n, **f: []})
    r = search_report("anything")
    assert r == {"results": [], "error": None, "backend": None}

    hit = [{"title": "t", "url": "https://x", "content": "", "engine": "duckduckgo"}]
    monkeypatch.setattr(search_pkg, "BACKENDS", {"duckduckgo": lambda q, n, **f: hit})
    r = search_report("anything")
    assert r == {"results": hit, "error": None, "backend": "duckduckgo"}


def test_service_search_reports_backend_error(monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from searchify import service

    monkeypatch.setattr(service, "search_report", lambda q, n, backend=None, **f: {"results": [], "error": "duckduckgo: ddgs error: RatelimitException", "backend": None})
    out = TestClient(service.app).post("/search", json={"query": "q"}).json()
    assert out["results"] == [] and "Ratelimit" in out["error"]


def test_cli_search_shows_backend_error(monkeypatch):
    import sys

    from typer.testing import CliRunner

    from searchify import cli

    monkeypatch.setattr(sys.modules["searchify.search"], "search_report",
                        lambda q, n, backend=None, **f: {"results": [], "error": "duckduckgo: down", "backend": None})
    result = CliRunner().invoke(cli.app, ["search", "q"])
    assert result.exit_code == 1
    assert json.loads(result.stdout) == {"error": "duckduckgo: down", "results": []}
