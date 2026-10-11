"""Tests fuer die Tempo-Bausteine: BM25-Vorauswahl, Seiten-Cache, Zeitbudget,
Hedging und Backend-Fallback. Ohne Netz und ohne Modell."""

from __future__ import annotations

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import extract as extract_mod  # noqa: E402
from app import search as search_mod  # noqa: E402
from app.extract import PageCache, page_chunks  # noqa: E402
from app.rerank import best_chunks, lexical_scores  # noqa: E402
from app.search import SearchBackendError, _hedged, _merge_answer_box  # noqa: E402


def test_lexical_scores_bevorzugt_treffer():
    scores = lexical_scores("Hauptstadt Australien", [
        "Canberra ist die Hauptstadt von Australien.",
        "Das Wetter in Spanien ist warm.",
    ])
    assert scores[0] > scores[1] == 0.0


def test_best_chunks_behaelt_reihenfolge():
    chunks = [{"content": t} for t in ("nichts", "katze katze", "hund", "katze")]
    picked = best_chunks("katze", chunks, 2)
    assert [c["content"] for c in picked] == ["katze katze", "katze"]


def test_page_chunks_ohne_snippet():
    cand = {"url": "u", "title": "t", "content": "snippet"}
    assert page_chunks(cand, None)[0]["content"] == "snippet"
    assert all(c["content"] != "snippet" for c in page_chunks(cand, "eins zwei drei", with_snippet=False))
    assert page_chunks({"url": "u", "title": "t", "content": ""}, None) == []


def test_page_cache_ttl_und_lru():
    cache = PageCache(max_items=2, ttl_s=60)
    cache.put("a", "A"); cache.put("b", "B"); cache.get("a"); cache.put("c", "C")
    assert cache.get("b") is None and cache.get("a") == "A" and cache.get("c") == "C"
    off = PageCache(max_items=2, ttl_s=0)
    off.put("a", "A")
    assert off.get("a") is None


def test_iter_pages_haelt_budget_und_liefert_cache_sofort(monkeypatch):
    cache = PageCache(10, 60)
    cache.put("https://cached", "aus dem cache")
    monkeypatch.setattr(extract_mod, "_cache", lambda: cache)
    release = threading.Event()

    def slow_fetch(url):
        if url == "https://schnell":
            return "schnell da"
        release.wait(2)
        return None

    monkeypatch.setattr(extract_mod, "_fetch_text", slow_fetch)
    cands = [{"url": "https://cached"}, {"url": "https://schnell"}, {"url": "https://langsam"}]
    t0 = time.monotonic()
    got = [pair for batch in extract_mod.iter_pages(cands, 0.2) for pair in batch]
    elapsed = time.monotonic() - t0
    release.set()
    assert dict(got) == {0: "aus dem cache", 1: "schnell da"}
    assert elapsed < 0.5


def test_hedged_nimmt_die_schnellere_anfrage():
    calls = []

    def call():
        calls.append(1)
        time.sleep(0.4 if len(calls) == 1 else 0.01)
        return {"n": len(calls)}

    t0 = time.monotonic()
    assert _hedged(call, 0.05) == {"n": 2}
    assert time.monotonic() - t0 < 0.3


def test_hedged_ohne_zweite_anfrage_wenn_schnell():
    calls = []
    assert _hedged(lambda: calls.append(1) or {"ok": True}, 0.5) == {"ok": True}
    assert len(calls) == 1


def test_answer_box_wird_vorangestellt():
    items = [{"link": "https://a", "snippet": "alt"}]
    _merge_answer_box(items, {"link": "https://a", "snippet": "Antwort"})
    assert items[0]["snippet"] == "Antwort alt"
    _merge_answer_box(items, {"link": "https://neu", "answer": "42"})
    assert items[0]["link"] == "https://neu"


def test_web_search_faellt_auf_zweites_backend_zurueck(monkeypatch):
    monkeypatch.setenv("SEARCH_BACKEND", "serper")
    monkeypatch.setenv("SEARCH_FALLBACK", "ddgs")

    def kaputt(*a, **k):
        raise SearchBackendError("Not enough credits")

    monkeypatch.setitem(search_mod._BACKENDS, "serper", kaputt)
    monkeypatch.setitem(search_mod._BACKENDS, "ddgs", lambda q, n, **k: [{"url": "https://x"}])
    assert search_mod.web_search("q", 10) == [{"url": "https://x"}]
