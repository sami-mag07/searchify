#!/usr/bin/env python3
"""Smoke-/Dry-Run-Tests ohne externe Infrastruktur.

Geprueft wird:
- Pydantic-Schema (Request/Response).
- Chunking (reine Funktion).
- Dedup + URL-Kanonisierung (reine Funktionen).
- Kompletter Pipeline-Pfad run_search() mit gestubbtem SearXNG/Extract/Rerank/Answer
  (kein Netzwerk, kein bge-Download, kein Claude-Call).

Start: `python -m pytest tests/ -q`.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Repo-Root importierbar machen (app/ liegt daneben).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import answer as answer_mod  # noqa: E402

_REAL_GENERATE = answer_mod.generate_answer
from app import extract as extract_mod  # noqa: E402
from app import main as main_mod  # noqa: E402
from app import rerank as rerank_mod  # noqa: E402
from app import search as search_mod  # noqa: E402
from app.extract import chunk_text  # noqa: E402
from app.rerank import canonical_url, dedup_chunks  # noqa: E402
from app.schema import SearchRequest, SearchResponse  # noqa: E402


def test_schema_roundtrip():
    req = SearchRequest(query="Testfrage", max_results=3)
    assert req.query == "Testfrage"
    assert req.include_answer is False  # Tavily-Standard
    assert req.max_results == 3
    assert SearchRequest(query="x", include_answer="basic").include_answer == "basic"


def test_chunking():
    text = " ".join(f"wort{i}" for i in range(1000))
    chunks = chunk_text(text, chunk_size=100, overlap=20)
    assert len(chunks) > 1
    # Overlap vorhanden: letztes Wort von Chunk 0 taucht in Chunk 1 auf.
    first_words = chunks[0].split()
    second_words = chunks[1].split()
    assert set(first_words) & set(second_words)
    # Leerer Text -> keine Chunks.
    assert chunk_text("", 100, 20) == []


def test_canonical_url():
    assert canonical_url("https://www.Example.com/path/") == "https://example.com/path"
    a = canonical_url("http://example.com/x?a=1#frag")
    b = canonical_url("http://example.com/x")
    assert a == b


def test_dedup():
    chunks = [
        {"url": "u1", "content": "die schnelle braune katze springt ueber den zaun jeden tag"},
        {"url": "u1", "content": "die schnelle braune katze springt ueber den zaun jeden tag"},
        {"url": "u2", "content": "voellig anderer inhalt ohne jede ueberschneidung hier drin text"},
    ]
    kept = dedup_chunks(chunks, threshold=0.8)
    assert len(kept) == 2


def _install_stubs(monkeypatch):
    """Ersetzt alle Infrastruktur-Aufrufe durch Fakes (kein Netz, kein Modell).
    monkeypatch setzt nach jedem Test zurueck, damit andere Tests die echten
    Funktionen sehen."""

    def fake_searxng(query, max_candidates, **kwargs):
        return [
            {
                "title": "Quelle A",
                "url": "https://example.org/a",
                "content": "RAG steht fuer Retrieval-Augmented Generation.",
                "engine": "fake",
            },
            {
                "title": "Quelle B",
                "url": "https://example.net/b",
                "content": "RAG kombiniert Suche mit einem Sprachmodell.",
                "engine": "fake",
            },
        ]

    def fake_rerank(query, chunks, top_pool=None):
        scored = []
        for i, ch in enumerate(chunks):
            item = dict(ch)
            item["score"] = round(0.9 - i * 0.1, 4)
            scored.append(item)
        scored.sort(key=lambda c: c["score"], reverse=True)
        return scored

    def fake_generate_answer(query, results):
        return ("RAG steht fuer Retrieval-Augmented Generation [1].", {"prompt_tokens": 42, "completion_tokens": 12})

    def fake_iter_pages(candidates, budget_s=None):
        return iter([[(i, c["content"] + " Mehr Kontext im Volltext.") for i, c in enumerate(candidates)]])

    monkeypatch.setattr(search_mod, "web_search", fake_searxng)
    monkeypatch.setattr(extract_mod, "iter_pages", fake_iter_pages)
    monkeypatch.setattr(rerank_mod, "rerank", fake_rerank)
    monkeypatch.setattr(answer_mod, "generate_answer", fake_generate_answer)


def test_dry_run_full_pipeline(monkeypatch):
    _install_stubs(monkeypatch)
    req = SearchRequest(query="Wofuer steht RAG?", max_results=5, include_answer=True, include_raw_content=True)
    resp = main_mod.run_search(req)

    assert isinstance(resp, SearchResponse)
    assert resp.query == "Wofuer steht RAG?"
    assert resp.answer and "Retrieval-Augmented" in resp.answer
    assert len(resp.results) == 2
    # Nach score sortiert.
    assert resp.results[0].score >= resp.results[1].score
    # raw_content ist der geladene Volltext, Seiten-Chunks landen im content.
    assert all(r.raw_content.endswith("Mehr Kontext im Volltext.") for r in resp.results)
    assert any("Mehr Kontext im Volltext." in r.content for r in resp.results)
    for r in resp.results:
        assert r.title and r.url and r.content and isinstance(r.score, float)
    assert resp.request_id
    assert resp.response_time >= 0.0
    assert resp.usage.prompt_tokens == 42
    # JSON serialisierbar.
    assert resp.model_dump_json()


def test_dry_run_no_answer(monkeypatch):
    _install_stubs(monkeypatch)
    req = SearchRequest(query="Test", include_answer=False)
    resp = main_mod.run_search(req)
    assert resp.answer is None
    assert len(resp.results) == 2


def _client_with_stubs(monkeypatch):
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from app import stream as stream_mod  # noqa: PLC0415

    _install_stubs(monkeypatch)
    monkeypatch.setattr(stream_mod, "gather", lambda *a, **k: None)
    monkeypatch.setattr(search_mod, "peek_cache", lambda *a, **k: None)
    monkeypatch.setattr(main_mod, "require_key", lambda *a, **k: None)
    return TestClient(main_mod.app)


def test_search_defaults_without_llm_is_200(monkeypatch):
    """Standardwerte duerfen ohne LLM in .env nie 500 geben (frueher include_answer=true)."""
    c = _client_with_stubs(monkeypatch)
    monkeypatch.setattr(answer_mod, "generate_answer", lambda q, r: (_ for _ in ()).throw(
        AssertionError("Standard darf kein LLM rufen")))
    r = c.post("/search", json={"query": "Wofuer steht RAG?"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["answer"] is None and len(body["results"]) == 2
    assert body["usage"]["answer_error"] is None and "x-answer-status" not in r.headers


def test_search_answer_without_llm_still_returns_results(monkeypatch):
    c = _client_with_stubs(monkeypatch)
    monkeypatch.setattr(answer_mod, "generate_answer", _REAL_GENERATE)
    monkeypatch.setattr(answer_mod, "get_settings", lambda: type("S", (), {"openai_api_key": ""})())
    for flag in (True, "basic", "advanced"):
        r = c.post("/search", json={"query": "Wofuer steht RAG?", "include_answer": flag})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["answer"] is None and len(body["results"]) == 2
        assert body["usage"]["answer_error"] == "no_llm_configured"
        assert r.headers["x-answer-status"] == "no_llm_configured"


def test_search_answer_llm_unreachable_still_returns_results(monkeypatch):
    c = _client_with_stubs(monkeypatch)

    def down(query, results):
        raise ConnectionError("LLM weg")

    monkeypatch.setattr(answer_mod, "generate_answer", down)
    r = c.post("/search", json={"query": "Wofuer steht RAG?", "include_answer": True})
    assert r.status_code == 200, r.text
    assert r.json()["answer"] is None and r.json()["usage"]["answer_error"] == "llm_unavailable"
