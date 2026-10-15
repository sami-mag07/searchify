"""Robustheit (Zuverlaessigkeits-Bench 13.10.2026): Laenge, leere Anfragen,
lockerer zweiter Versuch, Dubletten im Serper-Nachbau. Ohne Netz und Modell."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import extract as extract_mod  # noqa: E402
from app import main as main_mod  # noqa: E402
from app import rerank as rerank_mod  # noqa: E402
from app import search as search_mod  # noqa: E402
from app.compat import serper as serper_mod  # noqa: E402
from app.schema import SearchRequest  # noqa: E402


def test_engine_query_clips_long_queries():
    long_q = " ".join(f"wort{i}" for i in range(200))
    q = search_mod.engine_query(long_q)
    assert len(q.split()) == search_mod.ENGINE_MAX_WORDS
    assert len(search_mod.engine_query("x" * 30 + " " + "y" * 900)) <= search_mod.ENGINE_MAX_CHARS
    # Normale Anfragen bleiben gleich (Replay-Schluessel der Messungen unveraendert).
    assert search_mod.engine_query("Wie hoch ist der Brocken?") == "Wie hoch ist der Brocken?"
    assert search_mod.engine_query("  a\t b\n") == "a b"


def test_searxng_receives_clipped_query(monkeypatch):
    sent = {}

    def fake_page(client, url, params):
        sent.update(params)
        return {"results": [{"url": "https://example.org/x", "title": "t", "content": "c"}]}

    monkeypatch.setattr(search_mod, "_searxng_page", fake_page)
    search_mod.searxng_search("frage " * 300, 5, engines="bing", page=1)
    assert len(str(sent["q"]).split()) == search_mod.ENGINE_MAX_WORDS


def test_relaxed_query():
    assert search_mod.relaxed_query('"Wie hoch ist der Teide?"') == "Wie hoch ist der Teide?"
    assert search_mod.relaxed_query("Wie hoch ist der Teide?") is None
    assert search_mod.relaxed_query('""') is None


def test_blank_query_rejected():
    with pytest.raises(ValidationError):
        SearchRequest(query="  \t\n ")


def test_blank_query_http(monkeypatch):
    from fastapi.testclient import TestClient  # noqa: PLC0415

    called = []
    monkeypatch.setattr(main_mod, "run_search", lambda *a, **k: called.append(1))
    client = TestClient(main_mod.app)
    assert client.post("/search", json={"query": "   "}).status_code == 422
    r = client.post("/exa/search", json={"query": "   "})
    assert r.status_code == 400 and r.json()["tag"] == "INVALID_REQUEST_BODY"
    assert not called


def _stub_core(monkeypatch, hits_for):
    monkeypatch.delenv("SEARXNG_SPLIT_ENGINES", raising=False)
    monkeypatch.setenv("SEARCH_BACKEND", "searxng")
    asked = []

    def fake_web_search(query, max_candidates, **kwargs):
        asked.append(query)
        return hits_for(query)

    def fake_rerank(query, chunks, top_pool=None):
        return [dict(ch, score=0.9 - i * 0.1) for i, ch in enumerate(chunks)]

    monkeypatch.setattr(search_mod, "web_search", fake_web_search)
    monkeypatch.setattr(extract_mod, "iter_pages", lambda c, b=None: iter([]))
    monkeypatch.setattr(rerank_mod, "rerank", fake_rerank)
    return asked


HIT = [{"title": "Teide", "url": "https://example.org/teide", "content": "3715 m", "engine": "f"}]


def test_relaxed_retry_when_quoted_query_finds_nothing(monkeypatch):
    asked = _stub_core(monkeypatch, lambda q: [] if '"' in q else HIT)
    resp = main_mod.run_search(SearchRequest(query='"Wie hoch ist der Teide?"'))
    assert asked == ['"Wie hoch ist der Teide?"', "Wie hoch ist der Teide?"]
    assert resp.query == '"Wie hoch ist der Teide?"'  # Antwort nennt die Original-Frage
    assert [r.url for r in resp.results] == ["https://example.org/teide"]


def test_relaxed_retry_after_backend_error(monkeypatch):
    def hits(q):
        if '"' in q:
            raise search_mod.SearchBackendError("alle weg")
        return HIT

    asked = _stub_core(monkeypatch, hits)
    resp = main_mod.run_search(SearchRequest(query='"Teide Hoehe"'))
    assert len(asked) == 2 and resp.results


def test_no_retry_without_looser_form(monkeypatch):
    asked = _stub_core(monkeypatch, lambda q: [])
    resp = main_mod.run_search(SearchRequest(query="Teide Hoehe"))
    assert asked == ["Teide Hoehe"] and resp.results == []


def test_serper_dup_key():
    k = serper_mod.dup_key
    assert k("https://www.example.org/a/") == k("http://example.org/a")
    assert k("https://example.org/a?id=1") != k("https://example.org/a?id=2")


class _DeadClient:
    def get(self, *a, **k):
        import httpx  # noqa: PLC0415

        raise httpx.ConnectError("weg")


def _serper_p(q="Teide Hoehe"):
    return {"q": q, "language": None, "tbs": None}


def test_serper_upstream_falls_back_when_searxng_down(monkeypatch):
    monkeypatch.setattr(search_mod, "_client", lambda: _DeadClient())
    monkeypatch.setenv("SEARCH_FALLBACK", "ddgs")
    seen = {}

    def fake_ddgs(query, n, **kw):
        seen["q"] = query
        return [{"title": "T", "url": "https://example.org/t", "content": "c", "engine": "ddgs"}]

    monkeypatch.setitem(search_mod._BACKENDS, "ddgs", fake_ddgs)
    data = serper_mod._upstream(_serper_p("wort " * 100), 1, False)
    assert data["results"][0]["url"] == "https://example.org/t"
    assert len(seen["q"].split()) == search_mod.ENGINE_MAX_WORDS
    # Folgeseiten: leer statt Fehler fuer die ganze Anfrage
    assert serper_mod._upstream(_serper_p(), 2, False) == {"results": []}


def test_serper_never_falls_back_to_paid_serper(monkeypatch):
    monkeypatch.setattr(search_mod, "_client", lambda: _DeadClient())
    monkeypatch.setenv("SEARCH_FALLBACK", "serper")
    monkeypatch.setitem(search_mod._BACKENDS, "serper", lambda *a, **k: pytest.fail("Serper gerufen"))
    with pytest.raises(search_mod.SearchBackendError):
        serper_mod._upstream(_serper_p(), 1, False)


def test_cross_scores_tokenizes_only_under_lock(monkeypatch):
    import torch  # noqa: PLC0415

    class Tok:
        def __call__(self, *a, **k):
            # Der schnelle HF-Tokenizer wirft bei parallelem Zugriff "Already borrowed".
            assert rerank_mod._INFER_LOCK.locked(), "Tokenizer ausserhalb der Sperre"
            if len(a) == 1:
                return {"input_ids": [1, 2, 3]}

            class Enc(dict):
                def to(self, _d):
                    return self

            return Enc(n=len(a[0]))

        def decode(self, ids):
            return "q"

    class Model:
        def __call__(self, n):
            return type("O", (), {"logits": torch.zeros(n, 1)})()

    monkeypatch.setattr(rerank_mod, "_get_reranker", lambda: (Tok(), Model(), "cpu"))
    assert rerank_mod._cross_scores("frage", ["a", "b"], 256) == [0.0, 0.0]
