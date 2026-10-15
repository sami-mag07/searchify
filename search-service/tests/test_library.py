"""Themen-Bibliothek (app/library.py): Erkennung, Regal-Suche, Filter, Einmischen."""

import json
import time
from types import SimpleNamespace

import pytest

from app import library as library_mod
from app import library_build as build_mod


@pytest.fixture()
def regale(tmp_path, monkeypatch):
    data = {"regale": {
        "hackathon": {"keywords": {"hackat": 3.0, "hack": 2.5, "preisg": 1.0},
                      "eintraege": [
                          {"url": "https://hackathon-fulda.de/", "text": "Hackathon Fulda KI Bildung Preisgeld"},
                          {"url": "https://luma.com/berlin-ai-hack", "text": "Berlin AI Hackathon Wochenende"},
                          {"url": "https://example.org/robotik", "text": "Robotik Hackathon Muenchen"}]},
        "politik": {"keywords": {"jugend": 2.0, "delegi": 3.0}, "eintraege": [
            {"url": "https://www.jugenddelegierte.de/", "text": "UN Jugenddelegierte Bewerbung"}]},
        "_allgemein": {"keywords": {"siedep": 3.0, "hauptst": 3.0}, "eintraege": []},
    }}
    path = tmp_path / "regale.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv("LIBRARY_PATH", str(path))
    monkeypatch.setenv("LIBRARY", "1")
    library_mod.reset()
    yield path
    library_mod.reset()


def test_stems_ohne_zahlen_und_stoppwoerter():
    assert library_mod.stems("Hackathons für Schüler 2026 in Berlin") == ["hackat", "schule", "berlin"]


def test_detect_regal_und_kein_regal(regale):
    assert library_mod.detect("Hackathon Berlin Preisgeld") == "hackathon"
    assert library_mod.detect("Jugenddelegierte UN") == "politik"
    assert library_mod.detect("UN Delegierte Jugend Bewerbung") == "politik"
    assert library_mod.detect("Siedepunkt Wasser Hauptstadt") is None  # Hintergrund gewinnt
    assert library_mod.detect("Wie hoch ist der Mount Everest") is None


def test_hits_aus_dem_regal(regale):
    hits = library_mod.hits("Hackathon Berlin Wochenende", k=2)
    assert [h["url"] for h in hits][0] == "https://luma.com/berlin-ai-hack"
    assert all(h["engine"] == "bibliothek" and h["library"] == "hackathon" for h in hits)
    assert library_mod.hits("Wie hoch ist der Mount Everest") == []


def test_aus_per_schalter_oder_ohne_datei(regale, monkeypatch):
    monkeypatch.setenv("LIBRARY", "0")
    assert library_mod.hits("Hackathon Berlin") == []
    monkeypatch.setenv("LIBRARY", "1")
    monkeypatch.setenv("LIBRARY_PATH", str(regale.parent / "fehlt.json"))
    library_mod.reset()
    assert library_mod.hits("Hackathon Berlin") == []


def test_seed_hits_filter_und_pseudo_engine(regale):
    req = SimpleNamespace(query="Hackathon Preisgeld", topic="general", include_domains=None,
                          exclude_domains=["luma.com"])
    got = library_mod.seed_hits(req, 5)
    assert "https://luma.com/berlin-ai-hack" not in [h["url"] for h in got]
    assert [h["engine_ranks"] for h in got] == [{"bibliothek": i + 1} for i in range(len(got))]
    assert library_mod.seed_hits(SimpleNamespace(query="Hackathon", topic="news"), 5) == []
    only = library_mod.seed_hits(SimpleNamespace(query="Hackathon Preisgeld", topic="general",
                                                 include_domains=["hackathon-fulda.de"], exclude_domains=None), 5)
    assert [h["url"] for h in only] == ["https://hackathon-fulda.de/"]


def test_lookup_schnell(regale):
    library_mod.hits("Hackathon Berlin", k=5)
    t0 = time.perf_counter()
    for _ in range(200):
        library_mod.hits("Hackathon Deutschland November Preisgeld Anmeldung", k=5)
    assert (time.perf_counter() - t0) / 200 < 0.002  # unter 2 ms je Lookup


def test_neue_datei_wird_ohne_neustart_gelesen(regale, monkeypatch):
    assert library_mod.detect("Hackathon Berlin Preisgeld") == "hackathon"
    data = json.loads(regale.read_text())
    data["regale"]["hackathon"]["keywords"] = {}
    regale.write_text(json.dumps(data))
    import os
    os.utime(regale, (time.time() + 5, time.time() + 5))
    monkeypatch.setattr(library_mod, "_CHECK_EVERY_S", 0.0)
    assert library_mod.detect("Hackathon Berlin Preisgeld") is None


def test_keywords_halten_regal_typische_woerter():
    texts = {"hackathon": ["Hackathon Berlin", "Hackathon Muenchen", "hackathon Preisgeld"],
             "politik": ["Jugend Politik Hackathon", "Jugenddelegierte Bewerbung", "Jugendforum Politik"],
             build_mod.BACKGROUND: ["Wie hoch ist der Berg", "Hauptstadt von Frankreich"]}
    kw = build_mod.keywords(texts)
    assert "hackat" in kw["hackathon"]  # kommt auch in politik vor, bleibt trotzdem stark
    assert "jugend" in kw["politik"] and "hackat" not in kw["politik"]


def test_stream_mischt_regal_seiten_ein(regale, monkeypatch):
    from app import stream as stream_mod
    monkeypatch.setenv("LIBRARY_K", "3")
    monkeypatch.setenv("SEARXNG_SPLIT_ENGINES", "bing")
    added = []

    class Fetcher:
        def add(self, idx, url):
            added.append(url)

        def poll(self, timeout):
            return []

        def has_pending(self):
            return False

    monkeypatch.setattr(stream_mod.extract_mod, "PageFetcher", Fetcher)
    monkeypatch.setattr(stream_mod.search_mod, "searxng_search", lambda q, n, **k: [
        {"url": "https://hackathon-fulda.de/", "title": "Fulda", "content": "Hackathon", "engine_rank": 1}])
    monkeypatch.setattr(stream_mod.rerank_mod, "rerank", lambda q, chunks: [])
    req = SimpleNamespace(query="Hackathon Preisgeld Berlin", language="de", topic="general",
                          search_depth="basic", time_range=None, include_domains=None, exclude_domains=None)
    candidates, _ranked, _n, _s = stream_mod.gather(req, 0.1, 10)
    urls = [c["url"] for c in candidates]
    assert urls[0] in ("https://luma.com/berlin-ai-hack", "https://hackathon-fulda.de/")
    assert urls.count("https://hackathon-fulda.de/") == 1  # Dublette: Engine-Rang landet beim Regal-Treffer
    fulda = candidates[urls.index("https://hackathon-fulda.de/")]
    assert fulda["engine_ranks"].get("bing") == 1 and "bibliothek" in fulda["engine_ranks"]
    assert set(added) == set(urls)


def test_stream_ohne_engine_treffer_faellt_zurueck(regale, monkeypatch):
    from app import stream as stream_mod
    monkeypatch.setenv("LIBRARY_K", "3")
    monkeypatch.setenv("SEARXNG_SPLIT_ENGINES", "bing")
    monkeypatch.setattr(stream_mod.search_mod, "searxng_search", lambda q, n, **k: [])
    monkeypatch.setattr(stream_mod.rerank_mod, "rerank", lambda q, chunks: [])
    monkeypatch.setattr(stream_mod.extract_mod.PageFetcher, "add", lambda self, i, u: None)
    req = SimpleNamespace(query="Hackathon Preisgeld Berlin", language="de", topic="general",
                          search_depth="basic", time_range=None, include_domains=None, exclude_domains=None)
    assert stream_mod.gather(req, 0.1, 10) is None
