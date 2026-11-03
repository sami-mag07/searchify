"""HTTP service: the passages endpoint, with the core replaced."""
import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from searchify import service  # noqa: E402
from searchify.search import SearchBackendError  # noqa: E402

client = TestClient(service.app)


def test_passages_endpoint_returns_results_and_passes_options(monkeypatch):
    seen = {}

    def fake(query, max_results, **kw):
        seen.update(kw, query=query, max_results=max_results)
        return [{"title": "T", "url": "https://a.com", "content": "In Paris."}]
    monkeypatch.setattr(service, "best_passages", fake)
    r = client.post("/passages", json={"query": "Where?", "top_k": 3, "language": "de"})
    assert r.status_code == 200
    assert r.json() == {"results": [{"title": "T", "url": "https://a.com", "content": "In Paris."}]}
    assert seen == {"query": "Where?", "max_results": 5, "top_k": 3, "per_site": None,
                    "max_chars": 600, "rerank": None, "backend": None, "language": "de"}


def test_passages_endpoint_passes_country_on(monkeypatch):
    seen = {}
    monkeypatch.setattr(service, "best_passages", lambda query, max_results, **kw: seen.update(kw) or [])
    client.post("/passages", json={"query": "q", "country": "BE", "language": "nl"})
    assert seen["country"] == "BE" and seen["language"] == "nl"


@pytest.mark.parametrize("exc, code", [
    (SearchBackendError("down at /home/someone/.config/x"), "unavailable"),
    (SearchBackendError("rate_limited: ddgs said no (/srv/app/ddgs.py)"), "rate_limited"),
    (SearchBackendError("timeout: no answer in 10 s"), "timeout"),
    (ImportError("No module named 'torch' in /opt/venv/lib"), "rerank_unavailable"),
])
def test_passages_endpoint_reports_failures_as_a_fixed_code(monkeypatch, exc, code):
    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(service, "best_passages", boom)
    body = client.post("/passages", json={"query": "q"}).json()
    assert body == {"error": code, "results": []}  # never the exception text, it may hold paths


def _no_search(monkeypatch):
    monkeypatch.setattr(service, "search_report",
                        lambda *a, **k: {"results": [], "error": None, "backend": None})
    monkeypatch.setattr(service, "best_passages", lambda *a, **k: [])


@pytest.mark.parametrize("path", ["/search", "/passages"])
def test_query_is_at_most_2000_characters(monkeypatch, path):
    _no_search(monkeypatch)
    assert client.post(path, json={"query": "q" * 2001}).status_code == 422
    assert client.post(path, json={"query": "q" * 2000}).status_code == 200


@pytest.mark.parametrize("path", ["/search", "/passages"])
@pytest.mark.parametrize("field", ["include_domains", "exclude_domains"])
def test_domain_lists_take_at_most_100_entries(monkeypatch, path, field):
    _no_search(monkeypatch)
    domains = [f"d{i}.example" for i in range(101)]
    assert client.post(path, json={"query": "q", field: domains}).status_code == 422
    assert client.post(path, json={"query": "q", field: domains[:100]}).status_code == 200


def test_extract_takes_at_most_50_urls(monkeypatch):
    monkeypatch.setattr(service, "read_pages_ex", lambda urls, fmt, **kw: ([], [], {}))
    urls = [f"https://a.example/{i}" for i in range(51)]
    assert client.post("/extract", json={"urls": urls}).status_code == 422
    assert client.post("/extract", json={"urls": urls[:50]}).status_code == 200


def test_passages_endpoint_validates_limits():
    assert client.post("/passages", json={"query": "q", "top_k": 0}).status_code == 422
# Stand: 02.11.2026
