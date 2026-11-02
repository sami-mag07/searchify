"""CLI behavior with the network calls mocked."""
import json
import sys

from typer.testing import CliRunner

from searchify import cli
from searchify.search import SearchBackendError

runner = CliRunner()
HITS = [{"title": "Eiffel Tower", "url": "https://a.com", "content": "In Paris.", "engine": "x"}]


def test_search_json_by_default(monkeypatch):
    monkeypatch.setattr(sys.modules["searchify.search"], "search", lambda q, n, backend=None, **f: HITS)
    result = runner.invoke(cli.app, ["search", "Where is the Eiffel Tower?"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"results": HITS}


def test_search_no_json(monkeypatch):
    monkeypatch.setattr(sys.modules["searchify.search"], "search", lambda q, n, backend=None, **f: HITS)
    result = runner.invoke(cli.app, ["search", "q", "--no-json"])
    assert "1. Eiffel Tower" in result.stdout and "https://a.com" in result.stdout


def test_search_error_is_json_and_nonzero(monkeypatch):
    def boom(*a, **k):
        raise SearchBackendError("down")
    monkeypatch.setattr(sys.modules["searchify.search"], "search", boom)
    result = runner.invoke(cli.app, ["search", "q"])
    assert result.exit_code == 1
    assert json.loads(result.stdout)["error"] == "down"


def test_search_rejects_unknown_backend():
    result = runner.invoke(cli.app, ["search", "q", "--backend", "googel"])
    assert result.exit_code == 1
    assert "unknown backend" in json.loads(result.stdout)["error"]


def test_extract_json_and_failures(monkeypatch):
    doc = {"url": "https://a.com", "title": "T", "content": "body"}
    monkeypatch.setattr(sys.modules["searchify.extract"], "read_pages",
                        lambda urls, fmt: ([doc], [{"url": "https://b.com", "error": "403"}]))
    result = runner.invoke(cli.app, ["extract", "https://a.com", "https://b.com"])
    assert result.exit_code == 0
    out = json.loads(result.stdout)
    assert out["results"] == [doc] and out["failed"] == [{"url": "https://b.com", "error": "403"}]


def test_extract_rejects_bad_format():
    result = runner.invoke(cli.app, ["extract", "https://a.com", "-f", "pdf"])
    assert result.exit_code == 1


PASSAGES = [{"title": "T", "url": "https://a.com", "content": "The tower is in Paris.", "rerank_score": 2.1}]


def test_passages_json_passes_the_options_through(monkeypatch):
    seen = {}

    def fake(query, max_results, **kw):
        seen.update(kw, query=query, max_results=max_results)
        return PASSAGES
    monkeypatch.setattr(sys.modules["searchify.passages"], "passages", fake)
    result = runner.invoke(cli.app, ["passages", "Where is the tower?", "-n", "3", "-k", "2",
                                     "--per-site", "1", "--no-rerank", "--language", "de"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"results": PASSAGES}
    assert seen == {"query": "Where is the tower?", "max_results": 3, "top_k": 2, "per_site": 1,
                    "max_chars": 600, "rerank": False, "backend": None, "language": "de"}


def test_passages_rerank_defaults_to_auto(monkeypatch):
    seen = {}
    monkeypatch.setattr(sys.modules["searchify.passages"], "passages",
                        lambda q, n, **kw: seen.update(kw) or [])
    result = runner.invoke(cli.app, ["passages", "q", "--no-json"])
    assert seen["rerank"] is None and "No results." in result.stdout


def test_passages_missing_extra_is_a_json_error(monkeypatch):
    def boom(*a, **k):
        raise ImportError("reranking needs the extra")
    monkeypatch.setattr(sys.modules["searchify.passages"], "passages", boom)
    result = runner.invoke(cli.app, ["passages", "q", "--rerank"])
    assert result.exit_code == 1
    assert json.loads(result.stdout) == {"error": "reranking needs the extra", "results": []}


def test_mcp_entry_point_names_the_missing_extra(monkeypatch, capsys):
    import pytest
    monkeypatch.delitem(sys.modules, "searchify.mcp_server", raising=False)
    for name in [m for m in sys.modules if m == "mcp" or m.startswith("mcp.")] + ["mcp"]:
        monkeypatch.setitem(sys.modules, name, None)  # "import mcp" now fails like a missing package
    with pytest.raises(cli.typer.Exit):
        cli.mcp_main()
    assert 'pip install "searchify[mcp]"' in capsys.readouterr().err
# Stand: 02.11.2026
