"""The CLI and Parallel MCP backend against a local Streamable HTTP server."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from typer.testing import CliRunner

from searchify import __version__
from searchify.cli import app
from searchify.search import search_report


SOURCE = "https://docs.example/p?ref=%2Fsource#section"
HIT = {"url": SOURCE, "title": "Source", "excerpts": ["First excerpt", "Second excerpt"]}


@pytest.fixture
def mcp_server(monkeypatch):
    calls = []
    mode = {"response": "sse"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def respond(self, status, *, body=b"", content_type="application/json", session=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            if session:
                self.send_header("Mcp-Session-Id", session)
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            method = request["method"]
            calls.append((method, dict(self.headers), request))
            session = None
            if method == "initialize":
                session = f"fixture-{len(calls)}"
                result = {"protocolVersion": request["params"]["protocolVersion"]}
            elif method == "notifications/initialized":
                self.respond(202)
                return
            elif mode["response"] == "rate_limit":
                self.respond(429, body=b"provider detail should stay out of errors")
                return
            elif mode["response"] == "tool_error":
                result = {"isError": True, "content": [{"type": "text", "text": "provider detail"}]}
            else:
                hits = [
                    {"url": "javascript:alert(1)"},
                    {"url": "https://[broken"},
                    {"url": "https://blog.example/post"},
                    {"url": "https://excluded.docs.example/"},
                    HIT,
                    HIT,
                    {"url": "https://docs.example/other"},
                ]
                if mode["response"] == "empty":
                    hits = []
                data = {"results": hits}
                result = {"structuredContent": data} if mode["response"] == "json" else {
                    "content": [{"type": "text", "text": json.dumps(data)}]
                }
            reply = json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result})
            if mode["response"] == "sse":
                # An intervening server notification must not be mistaken for the reply.
                notification = '{"jsonrpc":"2.0","method":"notifications/message"}'
                self.respond(
                    200, session=session, content_type="text/event-stream",
                    body=f": keepalive\n\ndata: {notification}\n\nevent: message\ndata: {reply}\n\n".encode(),
                )
            else:
                self.respond(200, body=reply.encode(), session=session)

        def do_DELETE(self):
            calls.append(("DELETE", dict(self.headers), None))
            self.respond(200)

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as server:
        monkeypatch.setenv("PARALLEL_MCP_URL", f"http://127.0.0.1:{server.server_port}/mcp")
        thread = threading.Thread(target=server.serve_forever, args=(0.05,), daemon=True)
        thread.start()
        try:
            yield calls, mode
        finally:
            server.shutdown()
            thread.join()


def test_cli_parallel_search_sends_project_user_agent_across_sessions(mcp_server):
    calls, mode = mcp_server
    for response in ("sse", "json"):
        mode["response"] = response
        run = CliRunner().invoke(app, [
            "search", "public documentation", "--backend", "parallel", "-n", "1",
            "--include-domain", "docs.example", "--exclude-domain", "excluded.docs.example",
        ])
        assert run.exit_code == 0, run.stdout
        assert json.loads(run.stdout) == {"results": [{
            "title": "Source", "url": SOURCE, "engine": "parallel",
            "content": "First excerpt\n\nSecond excerpt",
        }]}

    assert [method for method, _, _ in calls] == [
        "initialize", "notifications/initialized", "tools/call", "DELETE",
    ] * 2
    for _, headers, _ in calls:
        assert headers["User-Agent"] == f"searchify/{__version__} (+https://github.com/sami-mag07/searchify)"
        assert "authorization" not in {name.lower() for name in headers}
    for start in (0, 4):
        session = f"fixture-{start + 1}"
        assert "mcp-session-id" not in {name.lower() for name in calls[start][1]}
        for _, headers, _ in calls[start + 1:start + 4]:
            normalized = {key.lower(): value for key, value in headers.items()}
            assert normalized["mcp-session-id"] == session
            assert normalized["mcp-protocol-version"] == "2025-03-26"
        assert calls[start + 2][2]["params"] == {
            "name": "web_search",
            "arguments": {
                "objective": "public documentation",
                "search_queries": ["public documentation site:docs.example"],
            },
        }


@pytest.mark.parametrize("response, reason", [("rate_limit", "rate_limited"), ("tool_error", "unavailable")])
def test_parallel_failures_use_searchify_error_reporting(mcp_server, response, reason):
    _, mode = mcp_server
    mode["response"] = response
    report = search_report("public documentation", backend="parallel")
    assert report["results"] == []
    assert report["errors"] == [{"engine": "parallel", "reason": reason}]
    assert "provider detail" not in report["error"]


def test_parallel_empty_results_are_not_an_error(mcp_server):
    _, mode = mcp_server
    mode["response"] = "empty"
    assert search_report("public documentation", backend="parallel") == {
        "results": [], "error": None, "backend": None,
    }


def test_parallel_requires_an_operator_configured_endpoint(monkeypatch):
    monkeypatch.delenv("PARALLEL_MCP_URL", raising=False)
    run = CliRunner().invoke(app, ["search", "public documentation", "--backend", "parallel"])
    assert run.exit_code == 1
    assert "PARALLEL_MCP_URL" in json.loads(run.stdout)["error"]
