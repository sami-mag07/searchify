"""Konstanz: gleiche Anfrage -> gleiche Reihenfolge, unabhaengig von Ankunft und Cache.
Ohne Netz und ohne Modell."""

from __future__ import annotations

import itertools

from app import main as main_mod
from app import rerank as rerank_mod
from app import search as search_mod
from app import stream as stream_mod
from app.schema import SearchRequest, SearchResponse, SearchResult


def test_rank_order_bricht_gleichstand_unabhaengig_von_der_ankunft():
    chunks = [
        {"url": "https://b.example/x", "content": "b", "raw_score": 10.9140625},
        {"url": "https://a.example/x", "content": "a", "raw_score": 10.9140625},
        {"url": "https://c.example/x", "content": "c", "raw_score": 11.0},
    ]
    orders = set()
    for perm in itertools.permutations(chunks):
        ranked = sorted(perm, key=rerank_mod.rank_order, reverse=True)
        orders.add(tuple(c["url"] for c in ranked))
    assert len(orders) == 1
    assert next(iter(orders))[0] == "https://c.example/x"


def test_merge_by_source_gleichstand_deterministisch():
    a = {"url": "https://a.example/", "content": "a", "raw_score": 5.0, "score": 0.7}
    b = {"url": "https://b.example/", "content": "b", "raw_score": 5.0, "score": 0.7}
    one = rerank_mod.merge_by_source([a, b], 1, 5)
    two = rerank_mod.merge_by_source([b, a], 1, 5)
    assert [r["url"] for r in one] == [r["url"] for r in two]


def _fake_pipeline(monkeypatch, calls, complete=True):
    def fake(req):
        calls.append(req.query)
        resp = SearchResponse(
            query=req.query, results=[SearchResult(title="t", url=f"https://x.example/{len(calls)}",
                                                    content="c", score=0.5)],
            response_time=0.1, request_id="r",
        )
        return resp, complete
    monkeypatch.setattr(main_mod, "_run_search", fake)
    monkeypatch.setenv("SEARCH_CACHE_TTL_S", "3600")
    monkeypatch.delenv("RESPONSE_CACHE_TTL_S", raising=False)


def test_wiederholung_liefert_dieselben_treffer(monkeypatch):
    calls = []
    _fake_pipeline(monkeypatch, calls)
    first = main_mod.run_search(SearchRequest(query="Wie hoch ist der Koelner Dom?"))
    again = main_mod.run_search(SearchRequest(query="wie hoch  ist der koelner dom?"))
    assert len(calls) == 1
    assert [r.url for r in again.results] == [r.url for r in first.results]
    assert again.request_id != first.request_id
    # andere Parameter -> eigener Eintrag
    main_mod.run_search(SearchRequest(query="Wie hoch ist der Koelner Dom?", max_results=7))
    assert len(calls) == 2


def test_kein_cache_bei_unvollstaendiger_suche_answer_oder_ttl_null(monkeypatch):
    calls = []
    _fake_pipeline(monkeypatch, calls, complete=False)
    for _ in range(2):
        main_mod.run_search(SearchRequest(query="frage"))
    assert len(calls) == 2  # eine Engine fehlte: naechste Anfrage versucht es neu
    _fake_pipeline(monkeypatch, calls)
    for _ in range(2):
        main_mod.run_search(SearchRequest(query="mit answer", include_answer=True))
    assert len(calls) == 4
    monkeypatch.setenv("SEARCH_CACHE_TTL_S", "0")
    for _ in range(2):
        main_mod.run_search(SearchRequest(query="ohne cache"))
    assert len(calls) == 6


def test_gather_meldet_leere_engine_als_unvollstaendig(monkeypatch):
    monkeypatch.setenv("SEARXNG_SPLIT_ENGINES", "bing,yahoo")
    monkeypatch.setenv("SPLIT_HEDGE_MS", "0")

    def fake_search(query, n, engines=None, page=1, **kw):
        if engines == "yahoo":
            return []
        return [{"url": "https://a.example/", "title": "A", "content": "a", "engine_rank": 1}]

    monkeypatch.setattr(search_mod, "searxng_search", fake_search)
    monkeypatch.setattr(rerank_mod, "rerank", lambda q, ch, top_pool=None: [])
    monkeypatch.setattr(stream_mod.extract_mod, "PageFetcher", _NoFetch)
    req = SearchRequest(query="frage", language="en")
    assert stream_mod.gather(req, 0.0, 10) is not None
    assert stream_mod.last_complete() is False


class _NoFetch:
    def add(self, *a):
        pass

    def poll(self, timeout):
        return []

    def has_pending(self):
        return False
