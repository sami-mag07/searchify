"""Tavily as the last safety net (no network)."""
import importlib

search_mod = importlib.import_module("searchify.search")
reader = importlib.import_module("searchify.extract.reader")
cloud = importlib.import_module("searchify.search.cloud")


def _empty(*_a, **_k):
    return []


def test_tavily_not_asked_without_key(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setitem(search_mod.BACKENDS, "duckduckgo", _empty)
    monkeypatch.setitem(search_mod.BACKENDS, "searxng", _empty)
    called = []
    monkeypatch.setitem(search_mod.BACKENDS, "tavily", lambda *a, **k: called.append(1) or [])
    assert search_mod.search("q") == []
    assert called == []


def test_tavily_not_asked_when_local_finds_something(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    hit = [{"title": "t", "url": "https://a.com", "content": "c", "engine": "duckduckgo"}]
    monkeypatch.setitem(search_mod.BACKENDS, "duckduckgo", lambda *a, **k: hit)
    called = []
    monkeypatch.setitem(search_mod.BACKENDS, "tavily", lambda *a, **k: called.append(1) or [])
    assert search_mod.search("q") == hit
    assert called == []


def test_tavily_asked_last_when_everything_is_empty(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    monkeypatch.setitem(search_mod.BACKENDS, "duckduckgo", _empty)
    monkeypatch.setitem(search_mod.BACKENDS, "searxng", _empty)
    monkeypatch.setattr(cloud, "tavily_post", lambda path, body, timeout: {
        "results": [{"title": "T", "url": "https://b.com/x", "content": "found"}]})
    monkeypatch.setitem(search_mod.BACKENDS, "tavily", cloud.tavily_search)
    hits = search_mod.search("q")
    assert hits == [{"title": "T", "url": "https://b.com/x", "content": "found", "engine": "tavily"}]


def test_tavily_reads_only_failed_pages(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    sent = {}

    def fake_post(path, body, timeout):
        sent["path"], sent["urls"] = path, body["urls"]
        return {"results": [{"url": "https://blocked.com", "raw_content": "rescued text"}]}

    monkeypatch.setattr(reader, "tavily_post", fake_post)
    monkeypatch.setattr(reader.pages_mod, "_docs", lambda: type("C", (), {"put": lambda *a: None})())
    ok = {"url": "https://ok.com", "content": "local"}
    failed = [{"url": "https://blocked.com", "error": "403"}, {"url": "https://gone.com", "error": "404"}]
    docs, still = reader._tavily_failed([ok], failed, "markdown",
                                        ["https://ok.com", "https://blocked.com", "https://gone.com"])
    assert sent == {"path": "extract", "urls": ["https://blocked.com", "https://gone.com"]}
    assert [d["url"] for d in docs] == ["https://ok.com", "https://blocked.com"]
    assert docs[1]["content"] == "rescued text" and docs[1]["source"] == "tavily"
    assert [f["url"] for f in still] == ["https://gone.com"]


def test_tavily_never_gets_addresses_that_were_blocked_as_internal(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")
    sent = []

    def fake_post(path, body, timeout):
        sent.extend(body["urls"])
        return {"results": [{"url": u, "raw_content": "cloud text"} for u in body["urls"]]}

    monkeypatch.setattr(reader, "tavily_post", fake_post)
    monkeypatch.setattr(reader.pages_mod, "_docs", lambda: type("C", (), {"put": lambda *a: None})())
    internal = "http://10.0.0.5/admin?session=internal"
    failed = [
        {"url": internal, "error": "address not public or not resolvable", "reason": "address_blocked"},
        {"url": "http://localhost:8080/x", "error": "address not public or not resolvable"},  # code from the text
        {"url": "https://blocked.com", "error": "HTTP 403", "reason": "blocked"},
    ]
    docs, still = reader._tavily_failed([], failed, "text", [f["url"] for f in failed])
    assert sent == ["https://blocked.com"]
    assert [d["url"] for d in docs] == ["https://blocked.com"]
    assert [f["url"] for f in still] == [internal, "http://localhost:8080/x"]
    sent.clear()
    docs, still = reader._tavily_failed([], failed[:2], "text", [internal])
    assert sent == [] and docs == [] and len(still) == 2  # nothing left to ask: no call at all


def test_tavily_error_keeps_failures(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "test-key")

    def boom(*_a, **_k):
        raise cloud.SearchBackendError("down")

    monkeypatch.setattr(reader, "tavily_post", boom)
    failed = [{"url": "https://blocked.com", "error": "403"}]
    docs, still = reader._tavily_failed([], failed, "text", ["https://blocked.com"])
    assert docs == [] and still == failed
# Stand: 24.10.2026
