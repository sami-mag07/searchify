"""MCP server: searchify's web search, page reading and answer passages as tools.

    searchify-mcp            (or: python -m searchify.mcp_server)

Speaks MCP over stdio, so Claude Code, Claude Desktop, Cursor and any other MCP
host can start it as a subprocess. Configure searchify through the usual
environment variables (SEARCH_BACKEND, SEARXNG_URL, ...) in the host's config.

Needs the optional extra: ``pip install "searchify[mcp]"`` (Python 3.10+).
"""
from __future__ import annotations

from typing import Annotated, Any, Dict, List, Literal, Optional

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from . import __version__
from .extract import read_pages
from .passages import passages as best_passages, trim
from .search import SearchBackendError, search as web_search

READS_THE_WEB = ToolAnnotations(read_only_hint=True, open_world_hint=True)

server = MCPServer(
    "searchify",
    version=__version__,
    instructions="Web search without an API key. For a question, call passages first: it "
    "returns the paragraphs that answer it, with their sources. Use search for a list of "
    "links and extract to read pages in full.",
)

Question = Annotated[str, Field(
    description="The user's question as one complete question in the user's language, not "
    "keywords. Replace relative times with real dates: 'this year' becomes the year.")]
Language = Annotated[Optional[str], Field(description="Result language code, e.g. en or de.")]
# No "day" or "week" for models: they pick them for questions about past events and the
# filter then hides the answer (measured 02.11.2026: "day" found 0 of 3 answers). The
# command line and the HTTP service still accept them.
TimeRange = Annotated[Optional[Literal["month", "year"]], Field(
    description="Only results from this period before today. Leave empty unless the user "
    "explicitly asks for recent results.")]
Domains = Annotated[Optional[List[str]], Field(description='Domains, e.g. ["wikipedia.org"].')]


def _filters(language, time_range, include_domains, exclude_domains) -> Dict:
    given = {"language": language, "time_range": time_range,
             "include_domains": include_domains, "exclude_domains": exclude_domains}
    return {k: v for k, v in given.items() if v}


@server.tool(title="Answer passages", annotations=READS_THE_WEB)
def passages(
    query: Question,
    max_results: Annotated[int, Field(ge=1, le=10, description="Result pages to read.")] = 5,
    top_k: Annotated[int, Field(ge=1, le=20, description="Passages to return.")] = 8,
    per_site: Annotated[Optional[int], Field(
        ge=1, description="At most this many passages from one site.")] = None,
    language: Language = None,
    time_range: TimeRange = None,
    include_domains: Domains = None,
    exclude_domains: Domains = None,
) -> dict[str, Any]:  # builtin dict: the SDK sends it unwrapped
    """Search the web, read the top pages and return the paragraphs that answer the
    question, best first, each with its source URL."""
    try:
        found = best_passages(query, max_results, top_k=top_k, per_site=per_site,
                              **_filters(language, time_range, include_domains, exclude_domains))
    except SearchBackendError as exc:
        raise ToolError(f"Web search failed: {exc}") from exc
    return {"results": found}


@server.tool(title="Web search", annotations=READS_THE_WEB)
def search(
    query: Question,
    max_results: Annotated[int, Field(ge=1, le=20, description="Hits to return.")] = 10,
    language: Language = None,
    time_range: TimeRange = None,
    include_domains: Domains = None,
    exclude_domains: Domains = None,
) -> dict[str, Any]:
    """Search the web and return the hits: title, URL and the search engine's snippet."""
    try:
        hits = web_search(query, max_results,
                          **_filters(language, time_range, include_domains, exclude_domains))
    except SearchBackendError as exc:
        raise ToolError(f"Web search failed: {exc}") from exc
    return {"results": hits}


@server.tool(title="Read pages", annotations=READS_THE_WEB)
def extract(
    urls: Annotated[List[str], Field(min_length=1, max_length=5,
                                     description="Addresses of the pages to read.")],
    max_chars: Annotated[int, Field(ge=500, le=20000,
                                    description="Trim each page's text to this length.")] = 6000,
    format: Literal["text", "markdown"] = "text",
    max_stage: Annotated[Optional[Literal["plain", "tls", "browser", "stealth"]], Field(
        description="Highest way to load a page: plain (fast fetch only), tls, browser or "
        "stealth. Leave empty unless the user wants less load.")] = None,
    budget: Annotated[Optional[Literal["low", "normal", "high"]], Field(
        description="low: shorter waits, shorter pages. high: longer waits. Leave empty "
        "for normal.")] = None,
) -> dict[str, Any]:
    """Read web pages and return their clean main text. Pages that fail are listed
    under failed with the reason."""
    limits = {k: v for k, v in {"max_stage": max_stage, "budget": budget}.items() if v}
    docs, failed = read_pages(urls, format, **limits)
    return {
        "results": [{"url": d.get("url"), "title": d.get("title"),
                     "published": d.get("published"),
                     "content": trim(d.get("content") or "", max_chars)} for d in docs],
        "failed": [{"url": f.get("url"), "error": f.get("error")} for f in failed],
    }


def main() -> None:  # extraction uses worker processes, so entry points need a real main
    server.run()


if __name__ == "__main__":
    main()
# Stand: 02.11.2026
