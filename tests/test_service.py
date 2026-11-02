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


def test_passages_endpoint_reports_search_failures(monkeypatch):
    def boom(*a, **k):
        raise SearchBackendError("down")
    monkeypatch.setattr(service, "best_passages", boom)
    assert client.post("/passages", json={"query": "q"}).json() == {"error": "down", "results": []}


def test_passages_endpoint_validates_limits():
    assert client.post("/passages", json={"query": "q", "top_k": 0}).status_code == 422
# Stand: 02.11.2026
