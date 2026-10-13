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

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _leerer_trefferlisten_cache(monkeypatch):
    """Jeder Test startet ohne gecachte Trefferlisten aus anderen Tests."""
    monkeypatch.setattr(search_mod, "_RESULT_CACHE", {})


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


def test_hedged_wirft_wenn_beide_scheitern():
    def call():
        time.sleep(0.1)
        raise RuntimeError("weg")

    try:
        _hedged(call, 0.02)
    except RuntimeError as exc:
        assert str(exc) == "weg"
    else:
        raise AssertionError("Ausnahme fehlt")


def test_502_ohne_interne_details(monkeypatch):
    from fastapi.testclient import TestClient

    from app import main as main_mod

    def kaputt(*a, **k):
        raise SearchBackendError("Serper-Fehler 400: Not enough credits http://intern:8888")

    monkeypatch.setattr(search_mod, "web_search", kaputt)
    resp = TestClient(main_mod.app).post("/search", json={"query": "x"})
    assert resp.status_code == 502
    assert resp.json()["detail"] == "Such-Backend nicht verfuegbar."


def test_fetch_url_bricht_tropfenden_download_ab(monkeypatch):
    class Tropf:
        headers = {"content-type": "text/html"}
        encoding = "utf-8"

        def raise_for_status(self):
            pass

        def iter_bytes(self):
            while True:
                time.sleep(0.05)
                yield b"x"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    class Client:
        def stream(self, *a, **k):
            return Tropf()

    monkeypatch.setattr(extract_mod, "_client", lambda: Client())
    t0 = time.monotonic()
    assert extract_mod.fetch_url("https://langsam", timeout=0.2) is None
    assert time.monotonic() - t0 < 0.5


def test_claim_verhindert_doppeltes_laden_und_begrenzt(monkeypatch):
    monkeypatch.setattr(extract_mod, "_INFLIGHT", set())
    monkeypatch.setattr(extract_mod, "_MAX_INFLIGHT", 2)
    assert extract_mod._claim("a") and not extract_mod._claim("a")
    assert extract_mod._claim("b") and not extract_mod._claim("c")


def test_interne_adressen_werden_blockiert():
    import httpx

    for url in ("http://127.0.0.1:8080/v1", "http://169.254.169.254/", "http://localhost/", "file:///etc/passwd"):
        try:
            extract_mod._block_internal(httpx.Request("GET", url))
        except httpx.RequestError:
            continue
        raise AssertionError(f"nicht blockiert: {url}")


def test_include_domains_wird_hart_gefiltert():
    hits = [{"url": "https://de.wikipedia.org/a"}, {"url": "https://evil.com/wikipedia.org"},
            {"url": "https://wikipedia.org.evil.com/"}]
    assert search_mod._filter(hits, [], 10, ["wikipedia.org"]) == [hits[0]]
    assert search_mod._filter(hits, ["wikipedia.org"], 10) == hits[1:]


def test_web_search_faellt_bei_null_treffern_zurueck(monkeypatch):
    monkeypatch.setenv("SEARCH_BACKEND", "searxng")
    monkeypatch.setenv("SEARCH_FALLBACK", "ddgs")
    monkeypatch.setitem(search_mod._BACKENDS, "searxng", lambda q, n, **k: [])
    monkeypatch.setitem(search_mod._BACKENDS, "ddgs", lambda q, n, **k: [{"url": "https://y"}])
    assert search_mod.web_search("q", 10) == [{"url": "https://y"}]


def test_skip_primary_fragt_gleich_das_ersatz_backend(monkeypatch):
    monkeypatch.setenv("SEARCH_BACKEND", "searxng")
    monkeypatch.setenv("SEARCH_FALLBACK", "ddgs")

    def nie(*a, **k):
        raise AssertionError("SearXNG darf nicht noch einmal gefragt werden")

    monkeypatch.setitem(search_mod._BACKENDS, "searxng", nie)
    monkeypatch.setitem(search_mod._BACKENDS, "ddgs", lambda q, n, **k: [{"url": "https://z"}])
    assert search_mod.web_search("q-skip", 10, skip_primary=True) == [{"url": "https://z"}]


def test_skip_primary_ohne_ersatz_fragt_das_haupt_backend(monkeypatch):
    monkeypatch.setenv("SEARCH_BACKEND", "searxng")
    monkeypatch.setenv("SEARCH_FALLBACK", "")
    monkeypatch.setitem(search_mod._BACKENDS, "searxng", lambda q, n, **k: [{"url": "https://w"}])
    assert search_mod.web_search("q-skip2", 10, skip_primary=True) == [{"url": "https://w"}]


def test_letztes_backend_darf_leer_liefern(monkeypatch):
    monkeypatch.setenv("SEARCH_BACKEND", "searxng")
    monkeypatch.setenv("SEARCH_FALLBACK", "")
    monkeypatch.setitem(search_mod._BACKENDS, "searxng", lambda q, n, **k: [])
    assert search_mod.web_search("q", 10) == []


def test_extraktion_laeuft_im_prozess_und_faellt_sonst_zurueck(monkeypatch):
    html = "<html><body><article><p>" + "Canberra ist die Hauptstadt Australiens. " * 30 + "</p></article></body></html>"
    assert "Canberra" in (extract_mod._extract(html, "https://example.org")[0] or "")

    class Kaputt:
        def submit(self, *a, **k):
            from concurrent.futures.process import BrokenProcessPool
            raise BrokenProcessPool("pool weg")

    monkeypatch.setattr(extract_mod, "_extract_pool", lambda: Kaputt())
    assert "Canberra" in (extract_mod._extract(html, "https://example.org")[0] or "")


def test_trefferliste_cache_frisch_und_stale(monkeypatch):
    monkeypatch.setenv("SEARCH_BACKEND", "searxng")
    monkeypatch.setenv("SEARCH_FALLBACK", "")
    monkeypatch.setenv("SEARCH_CACHE_TTL_S", "60")
    monkeypatch.setattr(search_mod, "_RESULT_CACHE", {})
    calls = []

    def backend(q, n, **k):
        calls.append(q)
        return [{"url": f"https://r{len(calls)}"}]

    monkeypatch.setitem(search_mod._BACKENDS, "searxng", backend)
    first = search_mod.web_search("Frage  X", 10)
    assert search_mod.web_search("frage x", 10) == first and len(calls) == 1  # normalisiert, frisch
    key = next(iter(search_mod._RESULT_CACHE))
    stamp, hits = search_mod._RESULT_CACHE[key]
    search_mod._RESULT_CACHE[key] = (stamp - 120, hits)  # abgelaufen, aber < 24 h
    assert search_mod.web_search("frage x", 10) == first  # sofort die alte Liste
    for _ in range(50):
        if len(calls) == 2 and search_mod._RESULT_CACHE[key][1] != first:
            break
        time.sleep(0.02)
    assert search_mod._RESULT_CACHE[key][1] == [{"url": "https://r2"}]  # im Hintergrund erneuert


def test_leere_trefferliste_wird_nicht_gecacht(monkeypatch):
    monkeypatch.setenv("SEARCH_BACKEND", "searxng")
    monkeypatch.setenv("SEARCH_FALLBACK", "")
    monkeypatch.setenv("SEARCH_CACHE_TTL_S", "60")
    monkeypatch.setattr(search_mod, "_RESULT_CACHE", {})
    monkeypatch.setitem(search_mod._BACKENDS, "searxng", lambda q, n, **k: [])
    search_mod.web_search("leer", 10)
    assert search_mod._RESULT_CACHE == {}


def test_news_wird_nie_veraltet_ausgeliefert(monkeypatch):
    monkeypatch.setenv("SEARCH_BACKEND", "searxng")
    monkeypatch.setenv("SEARCH_FALLBACK", "")
    monkeypatch.setenv("SEARCH_CACHE_TTL_S", "600")
    calls = []
    monkeypatch.setitem(search_mod._BACKENDS, "searxng",
                        lambda q, n, **k: calls.append(1) or [{"url": f"https://n{len(calls)}"}])
    search_mod.web_search("q", 10, topic="news")
    key = next(iter(search_mod._RESULT_CACHE))
    stamp, hits = search_mod._RESULT_CACHE[key]
    search_mod._RESULT_CACHE[key] = (stamp - 120, hits)  # News-Frist 60 s abgelaufen
    assert search_mod.web_search("q", 10, topic="news") == [{"url": "https://n2"}]


def test_curl_client_blockt_interne_ziele_auch_nach_redirect(monkeypatch):
    class Resp:
        def __init__(self, status, headers):
            self.status_code, self.headers, self.encoding = status, headers, "utf-8"

        def iter_content(self):
            yield b"<html>ok</html>"

        def close(self):
            pass

    seen = []

    class Sess:
        def get(self, url, **k):
            seen.append(url)
            return Resp(302, {"location": "http://127.0.0.1:8080/v1"})

    monkeypatch.setattr(extract_mod, "_curl_session", lambda: Sess())
    monkeypatch.setattr(extract_mod, "_public_ips",
                        lambda h: [] if h == "127.0.0.1" else ["93.184.215.14"])
    assert extract_mod._fetch_url_curl("https://example.org/", 1.0) is None
    assert seen == ["https://example.org/"]  # der Sprung nach 127.0.0.1 wurde nie angefragt


def test_ui_zeilen_fliegen_raus():
    text = "Zum Inhalt springen\nMeine Merkliste\nFranz Kafka war ein deutschsprachiger Schriftsteller aus Prag.\nKurz.\nEinwohner 456.692\nTeilen"
    assert extract_mod._drop_ui_lines(text) == (
        "Franz Kafka war ein deutschsprachiger Schriftsteller aus Prag.\nKurz.\nEinwohner 456.692"
    )


def test_merge_sortiert_nach_logit_nicht_nach_gerundetem_score():
    from app.rerank import merge_by_source

    chunks = [
        {"url": "https://a.org", "title": "A", "content": "a", "score": 1.0, "raw_score": 10.1},
        {"url": "https://b.org", "title": "B", "content": "b", "score": 1.0, "raw_score": 10.9},
    ]
    out = merge_by_source(chunks, chunks_per_source=2, max_results=5)
    assert [r["url"] for r in out] == ["https://b.org", "https://a.org"]
    assert all("_rank" not in r and "raw_score" not in r for r in out)


def test_fusion_engine_rang_hebt_engine_spitze():
    from app.rerank import fuse_engine_rank

    # Cross-Encoder-Reihenfolge a, b, c; die Engines haben c auf Platz 1 bei beiden.
    results = [{"url": f"https://{x}.org"} for x in "abc"]
    cands = [
        {"url": "https://a.org", "engine_ranks": {"bing": 9}},
        {"url": "https://b.org", "engine": "yahoo", "engine_rank": 5},
        {"url": "https://c.org/", "engine_ranks": {"bing": 1, "yahoo": 1}},
    ]
    out = fuse_engine_rank(results, cands, weight=1.5, k=1)
    assert [r["url"] for r in out][0] == "https://c.org"
    assert fuse_engine_rank(results, cands, weight=0, k=30) == results
    assert fuse_engine_rank(results, [{"url": "https://a.org"}], weight=1.0, k=30) == results


def test_fusion_misstraut_engine_mit_fehltreffern():
    from app.rerank import engine_trust, fuse_engine_rank

    # Bing hat die Anfrage missverstanden: seine Treffer x, y, z haben negative
    # Cross-Encoder-Logits. Ohne Vertrauen zoege Bing-Platz 1 (x) nach vorn.
    results = [{"url": f"https://{u}.org"} for u in ("a", "b", "c", "x", "y", "z")]
    ce = [9.0, 8.0, 7.0, -6.0, -7.0, -8.0]
    cands = [{"url": f"https://{u}.org", "engine_ranks": {"yahoo": i}} for i, u in enumerate("abc", 3)]
    cands += [{"url": f"https://{u}.org", "engine_ranks": {"bing": i}} for i, u in enumerate("xyz", 1)]
    ranks = [c["engine_ranks"] for c in cands]
    assert engine_trust(results, ranks, ce, 0.0) == {"yahoo": 1.0, "bing": 0.0}
    plain = fuse_engine_rank(results, cands, weight=1.5, k=1)
    trusted = fuse_engine_rank(results, cands, weight=1.5, k=1, ce_scores=ce, trust_min=0.0)
    assert [r["url"] for r in plain][0] == "https://x.org"
    assert [r["url"] for r in trusted][:3] == ["https://a.org", "https://b.org", "https://c.org"]


def test_merge_mit_kandidaten_fusioniert(monkeypatch):
    from app.rerank import merge_by_source

    monkeypatch.setenv("FUSION_WEIGHT", "1.5")
    monkeypatch.setenv("FUSION_K", "1")
    chunks = [
        {"url": "https://a.org", "title": "A", "content": "a", "score": 1.0, "raw_score": 10.9},
        {"url": "https://b.org", "title": "B", "content": "b", "score": 1.0, "raw_score": 10.5},
        {"url": "https://c.org", "title": "C", "content": "c", "score": 1.0, "raw_score": 10.1},
    ]
    cands = [{"url": "https://c.org", "engine_ranks": {"bing": 1, "yahoo": 1}},
             {"url": "https://a.org", "engine_ranks": {"bing": 8}}]
    assert merge_by_source(chunks, 2, 5)[0]["url"] == "https://a.org"
    assert merge_by_source(chunks, 2, 5, candidates=cands)[0]["url"] == "https://c.org"


def test_temperatur_tippfehler_kippt_nichts(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("SCORE_TEMPERATURE", "4,5")
    assert get_settings().score_temperature == 4.5
    monkeypatch.setenv("SCORE_TEMPERATURE", "quatsch")
    assert get_settings().score_temperature == 4.0


def test_links_aktions_links_zuerst_und_sauber():
    html = """<html><body>
      <a href="/ueber-uns">Ueber uns</a>
      <a href="#oben">Nach oben</a>
      <a href="javascript:void(0)">Menue</a>
      <a href="mailto:info@x.de">Mail</a>
      <a href="https://portal.example.org/bewerbung">Jetzt bewerben</a>
      <a href="/ueber-uns">Ueber uns doppelt</a>
      <a href="/files/ausschreibung.pdf">Ausschreibung</a>
      <form action="/anmeldung/senden"><input name="x"></form>
      <form action="/_search/index.html"><input name="q"></form>
      <a href="/kontakt"><img alt="x"></a>
      <a href="/informationen-fuer/alumni/index.html">Alumni</a>
    </body></html>"""
    links = extract_mod.extract_links(html, "https://example.org/programm")
    urls = [l["url"] for l in links]
    assert urls[:3] == [
        "https://portal.example.org/bewerbung",
        "https://example.org/files/ausschreibung.pdf",
        "https://example.org/anmeldung/senden",
    ]
    assert "https://example.org/ueber-uns" in urls and urls.count("https://example.org/ueber-uns") == 1
    assert not any(u.startswith(("mailto:", "javascript:")) or "#" in u for u in urls)
    assert urls.index("https://example.org/informationen-fuer/alumni/index.html") > 2  # kein Aktions-Link
    assert "https://example.org/_search/index.html" not in urls  # Suchformular raus


def test_kaputter_link_kostet_nicht_den_text():
    html = ("<html><body><article><p>" + "Canberra ist die Hauptstadt Australiens. " * 30
            + '</p><a href="http://[bad">kaputt</a><a href="/ok">Anmelden</a></article></body></html>')
    text, links, _meta = extract_mod.extract_page(html, "https://example.org/")
    assert "Canberra" in (text or "")
    assert [l["url"] for l in links] == ["https://example.org/ok"]


def test_pinned_backend_verbindet_nur_die_gepruefte_ip(monkeypatch):
    import httpcore
    import socket

    seen = []
    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp",
                        lambda self, host, port, **k: seen.append((host, port)) or "stream")
    answers = {"good.example": "93.184.215.14", "rebind.example": "127.0.0.1"}
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda host, *a, **k: [(2, 1, 6, "", (answers[host], 0))])
    backend = extract_mod._PinnedBackend()
    assert backend.connect_tcp("good.example", 443) == "stream"
    assert seen == [("93.184.215.14", 443)]  # verbunden wird die gepruefte IP, nicht der Name
    try:
        backend.connect_tcp("rebind.example", 443)
    except httpcore.ConnectError:
        pass
    else:
        raise AssertionError("interne IP nicht blockiert")
    assert len(seen) == 1


def test_client_nutzt_den_gepinnten_backend():
    assert isinstance(extract_mod._client()._transport._pool._network_backend, extract_mod._PinnedBackend)


def test_pinned_backend_probiert_alle_geprueften_ips(monkeypatch):
    import httpcore
    import socket

    tried = []

    def connect(self, host, port, **k):
        tried.append(host)
        if host == "93.184.215.14":
            raise httpcore.ConnectError("tot")
        return "stream"

    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", connect)
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, *a, **k: [
        (2, 1, 6, "", ("93.184.215.14", 0)), (2, 1, 6, "", ("93.184.215.15", 0))])
    assert extract_mod._PinnedBackend().connect_tcp("multi.example", 443) == "stream"
    assert tried == ["93.184.215.14", "93.184.215.15"]


def test_aktions_links_auf_spanisch_und_franzoesisch():
    html = """<html><body>
      <a href="/a-propos">À propos</a>
      <a href="/inscription">S'inscrire au programme</a>
      <a href="/sobre">Sobre nosotros</a>
      <a href="/es/solicitud">Enviar solicitud</a>
      <a href="/candidature.html">Déposer une candidature</a>
      <a href="/convocatoria-2027">Convocatoria 2027</a>
    </body></html>"""
    urls = [l["url"] for l in extract_mod.extract_links(html, "https://example.org/")]
    assert set(urls[:4]) == {
        "https://example.org/inscription", "https://example.org/es/solicitud",
        "https://example.org/candidature.html", "https://example.org/convocatoria-2027",
    }


def test_sprachen_mappings():
    assert search_mod._COUNTRY_FOR_LANG["es"] == "es" and search_mod._DDGS_REGION["fr"] == "fr-fr"


def test_domain_vielfalt_verschiebt_nur():
    from app.rerank import diversify

    items = [{"url": f"https://{h}/{i}"} for i, h in enumerate(["a.org", "www.a.org", "a.org", "b.org", "a.org", "c.org"])]
    out = [r["url"] for r in diversify(items, 2)]
    assert out == ["https://a.org/0", "https://www.a.org/1", "https://b.org/3", "https://c.org/5",
                   "https://a.org/2", "https://a.org/4"]
    assert diversify(items, 0) == items


def test_quellen_prior():
    from app.rerank import source_prior

    assert source_prior("https://brainly.lat/tarea/1") < 0
    assert source_prior("https://www.tiktok.com/discover/x") < 0
    assert source_prior("https://es.wikipedia.org/wiki/Las_meninas") > 0
    assert source_prior("https://www.service.gouv.fr/x") > 0
    assert source_prior("https://www.bmbf.bund.de/x") > 0
    assert source_prior("https://elpais.com/x") == 0
    assert source_prior("https://en.wikipedia.org/wiki/Zugspitze", "de") < 0
    assert source_prior("https://de.wikipedia.org/wiki/Zugspitze", "de") > 0


def test_sprach_abzug():
    from app.rerank import language_prior

    en = "At 2,962 metres the Zugspitze is the highest peak of the Wetterstein Mountains and of Germany."
    de = "Die Zugspitze ist mit 2962 m der höchste Berg Deutschlands und liegt im Wettersteingebirge."
    assert language_prior(en, "de", 2.0) == -2.0
    assert language_prior(de, "de", 2.0) == 0.0
    assert language_prior(en, None, 2.0) == 0.0
    assert language_prior("2962", "de", 2.0) == 0.0  # unklar -> kein Abzug


def test_read_pages_nutzt_nicht_den_fetch_pool(monkeypatch):
    from app import pages as pages_mod

    class Verboten:
        def submit(self, *a, **k):
            raise AssertionError("read_pages darf den Fetch-Pool der Suche nicht nutzen")

    monkeypatch.setattr(extract_mod, "_pool", lambda: Verboten())
    monkeypatch.setattr(pages_mod, "_read_one", lambda u, fmt, t, *a: ({"url": u, "content": "x"}, None))
    docs, failed = pages_mod.read_pages(["https://a.example/"], "text", 1.0)
    assert docs and not failed


def test_page_cache_grenze_in_bytes():
    cache = PageCache(max_items=100, ttl_s=60, max_bytes=50)
    cache.put("a", "x" * 30)
    cache.put("b", "y" * 30)  # zusammen 60 > 50: aeltester faellt raus
    assert cache.get("a") is None and cache.get("b") == "y" * 30
    cache.put("c", {"content": "z" * 10, "links": []})
    assert cache.get("b") and cache.get("c")


def test_cross_scores_caps_overlong_query(monkeypatch):
    """findSimilar schickt Titel + Seitenbeschreibung als Query; laenger als die
    Grenze hat der Tokenizer mit only_second geworfen (500 bei /exa/findSimilar)."""
    import types

    import torch

    from app import rerank as rr

    seen = {}

    class Tok:
        def __call__(self, a, b=None, add_special_tokens=True, truncation=None, max_length=None, **kw):
            if b is None:
                return {"input_ids": a.split()}
            q = len(a[0].split())
            seen["q"] = q
            if truncation == "only_second" and q + 3 >= max_length:
                raise ValueError("Sequence to truncate too short")
            return types.SimpleNamespace(to=lambda d: {"n": len(b)})

        def decode(self, ids):
            return " ".join(ids)

    class Model:
        def __call__(self, n):
            return types.SimpleNamespace(logits=torch.zeros(n, 1))

    monkeypatch.setattr(rr, "_get_reranker", lambda: (Tok(), Model(), "cpu"))
    assert rr._cross_scores("wort " * 400, ["kurz", "auch kurz"], 256) == [0.0, 0.0]
    assert seen["q"] == 128
    rr._cross_scores("kurze frage", ["text"], 256)
    assert seen["q"] == 2
