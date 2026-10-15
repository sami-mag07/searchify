"""Tests fuer die engine-getrennte Suche (app/stream.py). Ohne Netz und Modell."""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import extract as extract_mod  # noqa: E402
from app import rerank as rerank_mod  # noqa: E402
from app import search as search_mod  # noqa: E402
from app import stream as stream_mod  # noqa: E402
from app.schema import SearchRequest  # noqa: E402


class FakeFetcher:
    """Liefert fuer jede URL sofort einen Text, ausser fuer 'langsam'."""

    def __init__(self):
        self.added, self.ready = [], []

    def add(self, idx, url):
        self.added.append(url)
        if "langsam" not in url:
            self.ready.append((idx, f"Volltext von {url} mit Canberra als Hauptstadt."))

    def has_pending(self):
        return bool(self.ready)

    def poll(self, timeout):
        batch, self.ready = self.ready, []
        return batch


def _setup(monkeypatch, per_engine):
    monkeypatch.setenv("SEARXNG_SPLIT_ENGINES", "bing,yahoo")
    monkeypatch.setattr(extract_mod, "PageFetcher", FakeFetcher)
    monkeypatch.setattr(rerank_mod, "rerank",
                        lambda q, chunks, top_pool=None: [dict(c, score=0.5) for c in chunks])

    def fake_search(query, n, engines=None, **kw):
        if engines == "yahoo":
            time.sleep(per_engine.get("_yahoo_delay", 0.05))  # langsame Engine kommt spaeter
        return per_engine.get(engines, [])

    monkeypatch.setattr(search_mod, "searxng_search", fake_search)


def test_gather_mischt_engines_ohne_dubletten(monkeypatch):
    _setup(monkeypatch, {
        "bing": [{"title": "A", "url": "https://a.org/x", "content": "a"},
                 {"title": "B", "url": "https://b.org/", "content": "b"}],
        "yahoo": [{"title": "A2", "url": "https://www.a.org/x/", "content": "a2"},  # gleiche Seite
                  {"title": "C", "url": "https://c.org/", "content": "c"}],
    })
    req = SearchRequest(query="Hauptstadt Australien", include_answer=False)
    cands, ranked, extracted, search_s = stream_mod.gather(req, 0.2)
    assert [c["url"] for c in cands] == ["https://a.org/x", "https://b.org/", "https://c.org/"]
    assert extracted == 3
    assert any("Volltext" in r["content"] for r in ranked)  # Seiten-Chunks gerankt
    assert search_s >= 0.04  # erst fertig, wenn die langsame Engine da ist


def test_gather_ohne_treffer_gibt_none(monkeypatch):
    _setup(monkeypatch, {})
    assert stream_mod.gather(SearchRequest(query="x", include_answer=False), 0.2) is None


def test_searxng_engine_filter_ohne_kategorie(monkeypatch):
    sent = {}

    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"results": []}

    class Client:
        def __init__(self, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url, params=None, headers=None):
            sent.update(params)
            return Resp()

    monkeypatch.setattr(search_mod.httpx, "Client", Client)
    search_mod.searxng_search("q", 5, engines="bing")
    assert sent["engines"] == "bing" and "categories" not in sent


def test_gather_wartet_nicht_auf_haengende_engine(monkeypatch):
    monkeypatch.setenv("SPLIT_SEARCH_TIMEOUT_MS", "100")
    _setup(monkeypatch, {
        "bing": [{"title": "A", "url": "https://a.org/", "content": "a"}],
        "yahoo": [{"title": "C", "url": "https://c.org/", "content": "c"}],
        "_yahoo_delay": 2.0,
    })
    t0 = time.monotonic()
    cands, _, _, _ = stream_mod.gather(SearchRequest(query="x", include_answer=False), 0.05)
    assert time.monotonic() - t0 < 0.5
    assert [c["url"] for c in cands] == ["https://a.org/"]


def test_news_nimmt_nie_den_split_weg(monkeypatch):
    from app import main as main_mod

    monkeypatch.setenv("SEARCH_BACKEND", "searxng")
    monkeypatch.setenv("SEARXNG_SPLIT_ENGINES", "bing,yahoo")
    called = {}

    def no_gather(*a, **k):
        called["gather"] = True
        return None

    monkeypatch.setattr(stream_mod, "gather", no_gather)
    monkeypatch.setattr(search_mod, "web_search", lambda q, n, **k: called.setdefault("topic", k.get("topic")) and [])
    monkeypatch.setattr(extract_mod, "iter_pages", lambda c, b=None: iter([]))
    monkeypatch.setattr(rerank_mod, "rerank", lambda q, ch, top_pool=None: [])
    main_mod.run_search(SearchRequest(query="x", topic="news", include_answer=False))
    assert "gather" not in called and called["topic"] == "news"


def test_haengende_engine_bekommt_zweitanfrage(monkeypatch):
    monkeypatch.setenv("SEARXNG_SPLIT_ENGINES", "bing,yahoo")
    monkeypatch.setenv("SPLIT_HEDGE_MS", "50")
    monkeypatch.setenv("SPLIT_SEARCH_TIMEOUT_MS", "1000")
    monkeypatch.setattr(extract_mod, "PageFetcher", FakeFetcher)
    monkeypatch.setattr(rerank_mod, "rerank",
                        lambda q, chunks, top_pool=None: [dict(c, score=0.5) for c in chunks])
    calls = {"yahoo": 0}

    def fake_search(query, n, engines=None, **kw):
        if engines == "bing":
            return [{"title": "A", "url": "https://a.org/", "content": "a"}]
        calls["yahoo"] += 1
        if calls["yahoo"] == 1:
            time.sleep(0.8)  # erste Yahoo-Anfrage haengt
            return []
        return [{"title": "Y", "url": "https://y.org/", "content": "y"}]

    monkeypatch.setattr(search_mod, "searxng_search", fake_search)
    t0 = time.monotonic()
    cands, _, _, _ = stream_mod.gather(SearchRequest(query="x", include_answer=False), 0.05)
    assert time.monotonic() - t0 < 0.5  # nicht auf die haengende Anfrage gewartet
    assert {c["url"] for c in cands} == {"https://a.org/", "https://y.org/"}
    assert calls["yahoo"] == 2


def test_mehr_als_zehn_quellen_holt_seite_zwei_parallel(monkeypatch):
    monkeypatch.setenv("SEARXNG_SPLIT_ENGINES", "bing,yahoo")
    monkeypatch.setenv("SPLIT_PAGES_WIDE", "2")
    monkeypatch.setattr(extract_mod, "PageFetcher", FakeFetcher)
    monkeypatch.setattr(rerank_mod, "rerank",
                        lambda q, chunks, top_pool=None: [dict(c, score=0.5) for c in chunks])
    asked = []

    def fake_search(query, n, engines=None, page=None, **kw):
        asked.append((engines, page))
        return [{"title": f"{engines}{page}-{i}", "url": f"https://{engines}{page}.org/{i}", "content": "x"}
                for i in range(8)]

    monkeypatch.setattr(search_mod, "searxng_search", fake_search)
    cands, _, _, _ = stream_mod.gather(SearchRequest(query="x", include_answer=False), 0.05, 20)
    # Bing kann nicht blaettern: nur Seite 1; Yahoo Seiten 1 und 2.
    assert sorted(asked) == [("bing", 1), ("yahoo", 1), ("yahoo", 2)]
    assert len(cands) == 24
    asked.clear()
    stream_mod.gather(SearchRequest(query="x", include_answer=False), 0.05, 10)
    assert sorted(asked) == [("bing", 1), ("yahoo", 1)]


def test_breite_suche_holt_yahoo_seite_drei(monkeypatch):
    monkeypatch.setenv("SEARXNG_SPLIT_ENGINES", "bing,yahoo")
    monkeypatch.delenv("SPLIT_PAGES_WIDE", raising=False)
    monkeypatch.setattr(extract_mod, "PageFetcher", FakeFetcher)
    monkeypatch.setattr(rerank_mod, "rerank",
                        lambda q, chunks, top_pool=None: [dict(c, score=0.5) for c in chunks])
    asked = []

    def fake_search(query, n, engines=None, page=None, **kw):
        asked.append((engines, page))
        return [{"title": f"{engines}{page}-{i}", "url": f"https://{engines}{page}.org/{i}", "content": "x"}
                for i in range(7)]

    monkeypatch.setattr(search_mod, "searxng_search", fake_search)
    cands, _, _, _ = stream_mod.gather(SearchRequest(query="x", include_answer=False), 0.05, 20)
    # Standard: Yahoo Seiten 1-3 parallel, Bing nur Seite 1.
    assert sorted(asked) == [("bing", 1), ("yahoo", 1), ("yahoo", 2), ("yahoo", 3)]
    assert len(cands) == 28


def test_langsamer_reranker_verliert_keine_fertigen_engines(monkeypatch):
    """Haengt das Ranken der ersten Engine laenger als die Suchfrist (Modell laedt),
    zaehlen die inzwischen angekommenen Engines trotzdem."""
    _setup(monkeypatch, {
        "bing": [{"title": "A", "url": "https://a.org/", "content": "a"}],
        "yahoo": [{"title": "C", "url": "https://c.org/", "content": "c"}],
        "_yahoo_delay": 0.05,
    })
    monkeypatch.setenv("SPLIT_SEARCH_TIMEOUT_MS", "100")
    monkeypatch.setenv("SPLIT_HEDGE_MS", "0")
    calls = []

    def slow_rerank(q, chunks, top_pool=None):
        if not calls:
            time.sleep(0.3)  # erster Aufruf: ueber die Suchfrist hinaus
        calls.append(1)
        return [dict(c, score=0.5) for c in chunks]

    monkeypatch.setattr(rerank_mod, "rerank", slow_rerank)
    cands, _, _, _ = stream_mod.gather(SearchRequest(query="x", include_answer=False), 0.05)
    assert {c["url"] for c in cands} == {"https://a.org/", "https://c.org/"}
