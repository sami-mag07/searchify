"""MCP server, driven through the SDK's in-memory client with the core replaced."""
import pytest

pytest.importorskip("mcp")
from mcp import Client  # noqa: E402

from searchify import mcp_server  # noqa: E402
from searchify.search import SearchBackendError  # noqa: E402

PASSAGE = {"title": "T", "url": "https://a.com", "content": "In Paris.", "rerank_score": 2.1}


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client():
    async with Client(mcp_server.server, raise_exceptions=True) as c:
        yield c


@pytest.mark.anyio
async def test_lists_three_read_only_tools(client):
    tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == {"passages", "search", "extract"}
    assert all(t.annotations.read_only_hint for t in tools.values())
    assert "complete question" in tools["passages"].input_schema["properties"]["query"]["description"]
    time_range = tools["search"].input_schema["properties"]["time_range"]["anyOf"][0]["enum"]
    assert time_range == ["month", "year"]


@pytest.mark.anyio
async def test_passages_returns_results_and_drops_empty_filters(client, monkeypatch):
    seen = {}

    def fake(query, max_results, **kw):
        seen.update(kw, query=query, max_results=max_results)
        return [PASSAGE]
    monkeypatch.setattr(mcp_server, "best_passages", fake)
    result = await client.call_tool("passages", {"query": "Where is it?", "language": "de"})
    assert not result.is_error
    assert result.structured_content == {"results": [PASSAGE]}
    assert seen == {"query": "Where is it?", "max_results": 5, "top_k": 8, "per_site": None,
                    "language": "de"}


@pytest.mark.anyio
async def test_search_failure_is_a_tool_error_the_model_can_read(client, monkeypatch):
    def boom(*a, **k):
        raise SearchBackendError("rate limited")
    monkeypatch.setattr(mcp_server, "web_search", boom)
    result = await client.call_tool("search", {"query": "q"})
    assert result.is_error and "rate limited" in result.content[0].text


@pytest.mark.anyio
async def test_extract_trims_pages_and_lists_failures(client, monkeypatch):
    doc = {"url": "https://a.com", "title": "T", "published": None, "content": "word " * 300,
           "links": ["dropped"]}
    failed = [{"url": "https://b.com", "error": "HTTP 403, also after retry", "reason": "blocked"},
              {"url": "https://c.com", "error": "Zeitlimit"}]  # no code yet: derived from the text
    monkeypatch.setattr(mcp_server, "read_pages", lambda urls, fmt: ([doc], failed))
    result = await client.call_tool("extract", {"urls": ["https://a.com", "https://b.com", "https://c.com"],
                                                "max_chars": 500})
    out = result.structured_content
    assert len(out["results"][0]["content"]) <= 502 and "links" not in out["results"][0]
    assert out["failed"] == [
        {"url": "https://b.com", "error": "HTTP 403, also after retry", "reason": "blocked"},
        {"url": "https://c.com", "error": "Zeitlimit", "reason": "timeout"},
    ]


@pytest.mark.anyio
async def test_extract_refuses_more_than_five_urls(client):
    result = await client.call_tool("extract", {"urls": [f"https://a.com/{i}" for i in range(6)]})
    assert result.is_error
# Stand: 02.11.2026
