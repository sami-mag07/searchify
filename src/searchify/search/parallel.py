"""Anonymous Parallel search over Streamable HTTP."""
from __future__ import annotations

import json
from typing import Dict, List, Optional
from urllib.parse import urlsplit

import httpx

from ..config import get_settings
from ._common import SearchBackendError, dedupe_and_filter, site_scoped

_PROTOCOL_VERSION = "2025-03-26"


def _rpc(client: httpx.Client, url: httpx.URL, method: str, params: dict,
         request_id: Optional[int] = None) -> Optional[dict]:
    message = {"jsonrpc": "2.0", "method": method, "params": params}
    if request_id is not None:
        message["id"] = request_id
    with client.stream("POST", url, json=message) as response:
        response.raise_for_status()
        if method == "initialize" and (session_id := response.headers.get("Mcp-Session-Id")):
            client.headers["Mcp-Session-Id"] = session_id
        if request_id is None:
            return None
        media_type = response.headers.get("Content-Type", "").partition(";")[0].strip().lower()
        if media_type == "application/json":
            reply = json.loads(response.read())
            if isinstance(reply, dict) and reply.get("id") == request_id:
                return _rpc_result(reply)
        elif media_type == "text/event-stream":
            data = []
            for line in response.iter_lines():
                if line.startswith("data:"):
                    data.append(line[5:].removeprefix(" "))
                elif not line and data:
                    reply = json.loads("\n".join(data))
                    data.clear()
                    if isinstance(reply, dict) and reply.get("id") == request_id:
                        return _rpc_result(reply)
    raise SearchBackendError("Parallel MCP returned no matching response.")


def _rpc_result(reply: dict) -> dict:
    result = reply.get("result")
    if reply.get("jsonrpc") != "2.0" or not isinstance(result, dict) or result.get("isError"):
        raise SearchBackendError("Parallel MCP rejected the request.")
    return result


def _search_result(result: dict) -> list:
    data = result.get("structuredContent")
    content = result.get("content")
    if data is None and isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                data = json.loads(block["text"])
                break
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise SearchBackendError("Parallel MCP returned invalid search results.")
    return data["results"]


def _source_hit(item: object) -> Optional[Dict[str, str]]:
    if not isinstance(item, dict) or not isinstance(item.get("url"), str):
        return None
    url = item["url"]
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username is not None:
            return None
    except ValueError:
        return None
    if any(char.isspace() or ord(char) < 32 for char in url):
        return None
    title = item.get("title")
    excerpts = item.get("excerpts")
    return {
        "title": title if isinstance(title, str) else "",
        "url": url,
        "content": "\n\n".join(text for text in excerpts if isinstance(text, str))
        if isinstance(excerpts, list) else "",
        "engine": "parallel",
    }


def parallel_search(
    query: str,
    max_results: int = 10,
    *,
    include_domains: Optional[List[str]] = None,
    exclude_domains: Optional[List[str]] = None,
    **_: object,
) -> List[Dict[str, str]]:
    """Search through an operator-configured Parallel MCP endpoint, without a key."""
    from .. import __version__

    settings = get_settings()
    if not settings.parallel_mcp_url:
        raise SearchBackendError("Set PARALLEL_MCP_URL to the anonymous Parallel MCP endpoint.")
    try:
        url = httpx.URL(settings.parallel_mcp_url)
    except httpx.InvalidURL:
        raise SearchBackendError("PARALLEL_MCP_URL must be an HTTP(S) endpoint.") from None
    if url.scheme not in ("http", "https") or not url.host or url.userinfo:
        raise SearchBackendError("PARALLEL_MCP_URL must be an HTTP(S) endpoint without credentials.")

    headers = {
        "Accept": "application/json, text/event-stream",
        "User-Agent": f"searchify/{__version__} (+https://github.com/sami-mag07/searchify)",
    }
    try:
        with httpx.Client(headers=headers, timeout=settings.fetch_timeout) as client:
            try:
                init = _rpc(client, url, "initialize", {
                    "protocolVersion": _PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "searchify", "version": __version__},
                }, 1)
                if not init or init.get("protocolVersion") != _PROTOCOL_VERSION:
                    raise SearchBackendError("Parallel MCP did not negotiate a supported protocol version.")
                client.headers["MCP-Protocol-Version"] = _PROTOCOL_VERSION
                _rpc(client, url, "notifications/initialized", {})
                result = _rpc(client, url, "tools/call", {
                    "name": "web_search",
                    "arguments": {
                        "objective": query,
                        "search_queries": [site_scoped(query, include_domains)],
                    },
                }, 2)
            finally:
                if "Mcp-Session-Id" in client.headers:
                    try:
                        client.delete(url)
                    except httpx.HTTPError:
                        pass  # Session termination is optional; preserve the original outcome.
        items = _search_result(result or {})
    except httpx.HTTPStatusError as exc:
        raise SearchBackendError(f"Parallel MCP HTTP {exc.response.status_code}") from None
    except httpx.TimeoutException:
        raise SearchBackendError("timeout: Parallel MCP did not respond in time.") from None
    except (httpx.HTTPError, ValueError):
        raise SearchBackendError("Parallel MCP request failed or returned an invalid response.") from None
    hits = [hit for item in items if (hit := _source_hit(item)) is not None]
    return dedupe_and_filter(hits, exclude_domains or [], max_results, include_domains)
