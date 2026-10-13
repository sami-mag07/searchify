"""Offline-Tests fuer die Serper-kompatiblen Endpunkte (app/compat/serper.py).

SearXNG und Seitenabruf sind gestubbt, kein Netz.
"""

from __future__ import annotations

import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.compat import serper  # noqa: E402


def _page(pageno: int, news: bool = False) -> dict:
    tag = "n" if news else "w"
    results = [
        {
            "url": f"https://example.org/{tag}/{pageno}/{i}?utm_source=x&id={i}",
            "title": f"T{pageno}.{i}",
            "content": "Snippet",
            "publishedDate": "2026-10-09T08:15:00" if i == 0 else None,
        }
        for i in range(10)
    ]
    return {
        "results": results,
        "infoboxes": [{
            "infobox": "Berlin",
            "id": "https://de.wikipedia.org/wiki/Berlin",
            "content": "Hauptstadt Deutschlands",
            "img_src": "https://upload.wikimedia.org/berlin.png",
            "urls": [{"title": "Website", "url": "https://www.berlin.de"}],
            "attributes": [{"label": "Einwohner", "value": "3,8 Mio."}],
        }],
        "answers": [],
        "suggestions": ["berlin wetter", "berlin karte"],
    }


@pytest.fixture()
def calls(monkeypatch):
    seen = []

    def fake_upstream(p, pageno, news):
        seen.append({"pageno": pageno, "news": news, "language": p["language"], "tbs": p["tbs"]})
        return _page(pageno, news)

    monkeypatch.setattr(serper, "_upstream", fake_upstream)
    monkeypatch.setattr(serper, "_ce_scores", lambda q, results: None)  # kein Modell im Test
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)
    monkeypatch.setenv("SEARCH_CACHE_TTL_S", "3600")
    serper._CACHE.clear()
    monkeypatch.setattr(serper, "_NEWS_OFF_UNTIL", 0.0)
    return seen


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(serper.router)
    return TestClient(app)


def test_antwortform(client, calls):
    r = client.post("/serper/search", json={"q": "berlin"})
    assert r.status_code == 200
    d = r.json()
    assert set(d) >= {"searchParameters", "knowledgeGraph", "organic", "peopleAlsoAsk",
                      "relatedSearches", "credits"}
    sp = d["searchParameters"]
    assert sp["q"] == "berlin" and sp["type"] == "search" and sp["num"] == 10 and sp["page"] == 1
    assert len(d["organic"]) == 10
    first = d["organic"][0]
    assert set(first) >= {"title", "link", "snippet", "position"}
    assert first["position"] == 1
    assert first["date"] == "2026-10-09"  # ISO statt "2 days ago"
    assert "utm_source" not in first["link"] and "id=0" in first["link"]
    assert "date" not in d["organic"][1]
    assert d["peopleAlsoAsk"] == []
    assert d["relatedSearches"][0] == {"query": "berlin wetter"}
    assert d["credits"] == 0


def test_knowledge_graph_aus_infobox(client, calls):
    kg = client.post("/serper/search", json={"q": "berlin"}).json()["knowledgeGraph"]
    assert kg["title"] == "Berlin"
    assert kg["description"] == "Hauptstadt Deutschlands"
    assert kg["descriptionSource"] == "Wikipedia"
    assert kg["descriptionLink"] == "https://de.wikipedia.org/wiki/Berlin"
    assert kg["website"] == "https://www.berlin.de"
    assert kg["imageUrl"].endswith("berlin.png")
    assert kg["attributes"] == {"Einwohner": "3,8 Mio."}


def test_num_und_page(client, calls):
    d = client.post("/serper/search", json={"q": "x", "num": 20, "page": 2}).json()
    assert len(d["organic"]) == 20
    assert d["organic"][0]["position"] == 21
    assert d["organic"][0]["title"] == "T3.0"  # Offset 20 -> SearXNG-Seite 3
    assert sorted(c["pageno"] for c in calls) == [3, 4]
    calls.clear()
    d = client.post("/serper/search", json={"q": "y", "num": 100}).json()
    assert len(d["organic"]) == 100 and d["organic"][-1]["position"] == 100
    assert len(calls) == 10


def test_batch(client, calls):
    r = client.post("/serper/search", json=[{"q": "a"}, {"q": "b", "num": 20}])
    assert r.status_code == 200
    d = r.json()
    assert isinstance(d, list) and len(d) == 2
    assert d[0]["searchParameters"]["q"] == "a"
    assert len(d[1]["organic"]) == 20


def test_batch_ungueltig_ganz_400(client, calls):
    r = client.post("/serper/search", json=[{"q": "a"}, {"q": ""}])
    assert r.status_code == 400
    assert r.json()["statusCode"] == 400
    assert calls == []  # kein halber Batch


def test_news(client, calls):
    d = client.post("/serper/news", json={"q": "wahl", "tbs": "qdr:d"}).json()
    assert d["searchParameters"]["type"] == "news"
    assert "organic" not in d and len(d["news"]) == 10
    item = d["news"][0]
    assert item["source"] == "example.org" and item["date"] == "2026-10-09"
    assert all(c["news"] for c in calls)
    assert calls[0]["tbs"] == "qdr:d"


def test_news_ohne_news_kategorie_merkt_sich_das(client, monkeypatch, calls):
    """SearXNG ohne News-Engine liefert still allgemeine Treffer: Ersatz, danach
    10 Minuten keine News-Anfrage mehr."""
    def upstream(p, pageno, news):
        calls.append({"news": news, "tbs": p["tbs"]})
        page = _page(pageno)
        for r in page["results"]:
            r["category"] = "general"
        return page

    monkeypatch.setattr(serper, "_upstream", upstream)
    d = client.post("/serper/news", json={"q": "wahl"}).json()
    assert d["searchParameters"]["fallback"] == "general" and len(d["news"]) == 10
    calls.clear()
    client.post("/serper/news", json={"q": "andere frage"})
    assert calls and not any(c["news"] for c in calls)


def test_news_ersatz_ohne_news_engine(client, monkeypatch, calls):
    def upstream(p, pageno, news):
        calls.append({"news": news, "tbs": p["tbs"]})
        return {"results": []} if news else _page(pageno)

    monkeypatch.setattr(serper, "_upstream", upstream)
    d = client.post("/serper/news", json={"q": "wahl"}).json()
    assert d["searchParameters"]["fallback"] == "general"
    assert len(d["news"]) == 10
    assert calls[-1] == {"news": False, "tbs": "qdr:m"}


@pytest.mark.parametrize("body, lang", [
    ({"q": "x", "hl": "de", "gl": "de"}, "de-DE"),
    ({"q": "x", "hl": "es"}, "es"),
    ({"q": "x", "hl": "es", "gl": "mx"}, "es-MX"),
    ({"q": "x", "gl": "fr"}, "fr-FR"),
    ({"q": "x", "hl": "fr", "gl": "be"}, "fr-BE"),
    ({"q": "x", "hl": "en", "gl": "uk"}, "en-GB"),
    ({"q": "x"}, None),
])
def test_sprache(client, calls, body, lang):
    d = client.post("/serper/search", json=body).json()
    assert calls[0]["language"] == lang
    assert d["searchParameters"].get("language") == lang


def test_spanisch_und_franzoesisch_body(client, monkeypatch, calls):
    def upstream(p, pageno, news):
        calls.append({"language": p["language"]})
        return {"results": [{"url": "https://es.example/a", "title": "¿Qué es la energía?",
                             "content": "Año, niño, acción"}],
                "infoboxes": [{"infobox": "Énergie", "id": "https://fr.wikipedia.org/wiki/É",
                               "content": "Grandeur physique élémentaire"}]}

    monkeypatch.setattr(serper, "_upstream", upstream)
    es = client.post("/serper/search", json={"q": "qué es la energía", "hl": "es", "gl": "es", "num": 1}).json()
    fr = client.post("/serper/search", json={"q": "énergie", "hl": "fr", "gl": "fr", "num": 1}).json()
    assert calls[0]["language"] == "es-ES" and calls[-1]["language"] == "fr-FR"
    assert es["organic"][0]["title"] == "¿Qué es la energía?"
    assert es["searchParameters"]["hl"] == "es"
    assert fr["knowledgeGraph"]["title"] == "Énergie"
    assert fr["knowledgeGraph"]["description"] == "Grandeur physique élémentaire"


def test_ungueltige_parameter(client, calls):
    for body in ({"num": 10}, {"q": "x", "num": 101}, {"q": "x", "page": 0},
                 {"q": "x", "tbs": "qdr:z"}, {"q": "x", "gl": "deutschland"}):
        r = client.post("/serper/search", json=body)
        assert r.status_code == 400, body
        assert "message" in r.json()
    assert calls == []


def test_cache(client, calls):
    client.post("/serper/search", json={"q": "berlin"})
    client.post("/serper/search", json={"q": "Berlin "})  # q wird getrimmt, Gross/klein zaehlt
    client.post("/serper/search", json={"q": "berlin"})
    assert sum(c["pageno"] == 1 for c in calls) == 2


def test_auth(client, calls, monkeypatch):
    monkeypatch.setenv("SERVICE_API_KEY", "geheim-test")
    r = client.post("/serper/search", json={"q": "x"})
    assert r.status_code == 401 and r.json()["message"] == "Unauthorized."
    r = client.post("/serper/search", json={"q": "x"}, headers={"X-API-KEY": "falsch"})
    assert r.status_code == 401
    r = client.post("/serper/search", json={"q": "x"}, headers={"X-API-KEY": "geheim-test"})
    assert r.status_code == 200
    assert sum(c["pageno"] == 1 for c in calls) == 1


def test_extras_links(client, calls, monkeypatch):
    read = []

    def fake_read(urls, fmt="text", timeout=None):
        read.append(list(urls))
        return [{"url": u, "links": [{"url": "https://ziel.example", "text": "Ziel"}]} for u in urls[:2]], \
               [{"url": urls[2], "error": "x"}]

    monkeypatch.setattr(serper.pages_mod, "read_pages", fake_read)
    d = client.post("/serper/search", json={"q": "berlin", "extras": {"links": True}}).json()
    assert len(read[0]) == 3
    assert d["organic"][0]["links"][0]["url"] == "https://ziel.example"
    assert d["organic"][2]["links"] == []
    assert "links" not in d["organic"][3]
    # Cache darf die Links nicht behalten
    d2 = client.post("/serper/search", json={"q": "berlin"}).json()
    assert "links" not in d2["organic"][0]


def test_backend_fehler_502(client, monkeypatch):
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)
    serper._CACHE.clear()

    def kaputt(p, pageno, news):
        raise serper.search_mod.SearchBackendError("SearXNG: ConnectError")

    monkeypatch.setattr(serper, "_upstream", kaputt)
    r = client.post("/serper/search", json={"q": "x"})
    assert r.status_code == 502 and "SearXNG" not in r.json()["message"]


def test_rerank_fenster_rrf_und_vielfalt(client, calls, monkeypatch):
    # Cross-Encoder mag den ersten Treffer von Seite 2 am meisten, sonst SearXNG-Folge.
    def fake_scores(q, results):
        return [10.0 if r["url"].startswith("https://example.org/w/2/0") else -float(i)
                for i, r in enumerate(results)]

    monkeypatch.setattr(serper, "_ce_scores", fake_scores)
    # Operator-Query als Frage: ohne Yandex, dafuer eine Extraseite als Kandidaten
    d = client.post("/serper/search", json={"q": 'what is "rrf"?'}).json()
    assert sorted(c["pageno"] for c in calls) == [1, 2]
    links = [o["link"] for o in d["organic"]]
    assert len(links) == 10 and [o["position"] for o in d["organic"]] == list(range(1, 11))
    top = next(i for i, u in enumerate(links) if u.startswith("https://example.org/w/2/0"))
    assert top < 6  # RRF hebt ihn von Platz 11 nach vorn, Platz 1 bleibt der Engine-Erste
    assert links[0].startswith("https://example.org/w/1/0")
    # page=2 schneidet aus derselben sortierten Liste, keine Dubletten zu page=1
    d2 = client.post("/serper/search", json={"q": 'what is "rrf"?', "page": 2}).json()
    assert not set(links) & {o["link"] for o in d2["organic"]}


def test_reorder_domain_vielfalt(monkeypatch):
    monkeypatch.setattr(serper, "_ce_scores", lambda q, results: [0.0] * len(results))
    rs = [{"url": u} for u in ("https://a.de/1", "https://a.de/2", "https://b.de/1", "https://c.de/1")]
    out = [r["url"] for r in serper._reorder("q", rs)]
    assert out == ["https://a.de/1", "https://b.de/1", "https://c.de/1", "https://a.de/2"]


def test_reorder_zweiter_treffer_je_host_nur_wenn_klar_passend(monkeypatch):
    # Stichwort-Suche: a.de/2 (Logit >= DUP_CE_MIN) bleibt vorn, a.de/3 (dritter) und
    # schwache Zweittreffer (b.de/2) nach hinten; bei einer Frage weiter ein Treffer je Host.
    urls = ["https://a.de/1", "https://a.de/2", "https://a.de/3", "https://b.de/1",
            "https://b.de/2", "https://c.de/1"]
    logits = {"https://a.de/2": serper.DUP_CE_MIN, "https://a.de/3": serper.DUP_CE_MIN + 1,
              "https://b.de/2": serper.DUP_CE_MIN - 1}
    monkeypatch.setattr(serper, "_ce_scores", lambda q, results: [logits.get(r["url"], 0.0) for r in results])
    rs = [{"url": u, "engines": ["yahoo"], "positions": [i + 1], "_page": 1} for i, u in enumerate(urls)]
    out = [r["url"] for r in serper._reorder("anbieter liste berlin", rs)]
    assert out[:4] == ["https://a.de/1", "https://a.de/2", "https://b.de/1", "https://c.de/1"]
    assert set(out[4:]) == {"https://a.de/3", "https://b.de/2"}
    frage = [r["url"] for r in serper._reorder("wer bietet das an?", rs)]
    assert {urlparse_host(u) for u in frage[:3]} == {"a.de", "b.de", "c.de"}


def urlparse_host(u):
    return u.split("/")[2]


def test_reorder_engine_vertrauen(monkeypatch):
    # Bing versteht die Anfrage falsch (alle Logits negativ), Yahoo trifft: Yahoos
    # Plaetze zaehlen in der Fusion, Bings Platz 1 rutscht nach hinten.
    rs = [
        {"url": "https://bing1.de/", "engines": ["bing"], "positions": [1], "_page": 1},
        {"url": "https://bing2.de/", "engines": ["bing"], "positions": [2], "_page": 1},
        {"url": "https://yahoo1.de/", "engines": ["yahoo"], "positions": [1], "_page": 1},
        {"url": "https://yahoo2.de/", "engines": ["yahoo"], "positions": [2], "_page": 1},
    ]
    logits = {"https://bing1.de/": -1.0, "https://bing2.de/": -2.0,
              "https://yahoo1.de/": 2.0, "https://yahoo2.de/": 1.0}
    monkeypatch.setattr(serper, "_ce_scores", lambda q, results: [logits[r["url"]] for r in results])
    out = [r["url"] for r in serper._reorder("q", rs)]
    assert out[:2] == ["https://yahoo1.de/", "https://yahoo2.de/"]


def test_reorder_vertrauen_erst_ab_trust_min(monkeypatch):
    # Bing liefert nur themennahe Treffer (Logit knapp ueber 0, unter TRUST_MIN), Yahoo
    # klar passende: Bing bekommt kein Vertrauen, Yahoo-Plaetze fuehren.
    rs = [
        {"url": "https://bing1.de/", "engines": ["bing"], "positions": [1], "_page": 1},
        {"url": "https://bing2.de/", "engines": ["bing"], "positions": [2], "_page": 1},
        {"url": "https://yahoo1.de/", "engines": ["yahoo"], "positions": [3], "_page": 1},
        {"url": "https://yahoo2.de/", "engines": ["yahoo"], "positions": [4], "_page": 1},
    ]
    logits = {"https://bing1.de/": 1.0, "https://bing2.de/": 0.5,
              "https://yahoo1.de/": 3.0, "https://yahoo2.de/": 2.5}
    monkeypatch.setattr(serper, "_ce_scores", lambda q, results: [logits[r["url"]] for r in results])
    assert serper.TRUST_MIN > 1.0
    out = [r["url"] for r in serper._reorder("stichwort suche", rs)]
    assert out[:2] == ["https://yahoo1.de/", "https://yahoo2.de/"]


@pytest.mark.parametrize("q,frage", [
    ("Wer entwarf den Berliner Hauptbahnhof?", True),
    ("how tall is the eiffel tower", True),
    ("¿Cuál es la capital de Australia", True),
    ("Quelle est la capitale du Canada", True),
    ("hackathon Germany 2026 students", False),
    ("Stipendium junge Gründer Schüler 2026", False),
])
def test_is_question(q, frage):
    assert serper.is_question(q) is frage


def test_reorder_stichwort_folgt_engine_frage_dem_reranker(monkeypatch):
    # Cross-Encoder mag nur den letzten Engine-Treffer: bei einer Frage kommt er
    # nach vorn, bei einer Stichwort-Suche bleibt die Engine-Reihenfolge vorn.
    rs = [{"url": f"https://s{i}.de/", "engines": ["yahoo"], "positions": [i + 1], "_page": 1}
          for i in range(8)]
    monkeypatch.setattr(serper, "_ce_scores",
                        lambda q, results: [9.0 if r["url"] == "https://s7.de/" else 1.0 for r in results])
    frage = [r["url"] for r in serper._reorder("wer hat das gebaut?", rs)]
    stichwort = [r["url"] for r in serper._reorder("bau architekt berlin", rs)]
    assert frage.index("https://s7.de/") < 5
    assert stichwort[:5] == [f"https://s{i}.de/" for i in range(5)]


def test_reorder_ohne_reranker_unveraendert(monkeypatch):
    monkeypatch.setattr(serper, "_ce_scores", lambda q, results: None)
    rs = [{"url": "https://a.de/1"}, {"url": "https://a.de/2"}]
    assert serper._reorder("q", rs) == rs


@pytest.mark.parametrize("q,yandex", [
    ("youth forum 2026 fully funded", True),
    ('"call for participants" hackathon', False),
    ("site:devpost.com hackathon", False),
    ('"under 20" OR "under 21" fellowship', False),
    ("Oregon hackathon", True),  # "OR" nur als eigenes Wort
])
def test_engines_je_query(q, yandex):
    assert ("yandex" in serper._engines(q)) is yandex
    assert serper._engines(q).startswith(serper.GENERAL_ENGINES)


def test_base_order_yandex_nur_kandidat():
    rs = [
        {"url": "y", "engines": ["yandex"], "positions": [1], "_page": 1},
        {"url": "b2", "engines": ["bing"], "positions": [2], "_page": 1},
        {"url": "b1", "engines": ["bing", "yandex"], "positions": [1, 3], "_page": 1},
        {"url": "s2", "engines": ["yahoo"], "positions": [1], "_page": 2},
    ]
    assert [r["url"] for r in serper._base_order(rs)] == ["b1", "b2", "s2", "y"]


def test_ohne_operator_keine_extraseite(client, calls):
    client.post("/serper/search", json={"q": "plain"})
    assert [c["pageno"] for c in calls] == [1]


def test_batch_mit_zu_vielen_upstream_seiten_wird_abgelehnt():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.compat import serper as serper_mod

    app = FastAPI()
    app.include_router(serper_mod.router)
    body = [{"q": f"frage {i}", "num": 100} for i in range(10)]  # ~10 x 10 Seiten
    r = TestClient(app).post("/serper/search", json=body)
    assert r.status_code == 400 and "Batch too large" in r.text


def test_news_ohne_treffer_schaltet_news_nicht_ab(monkeypatch):
    from app.compat import serper as serper_mod

    monkeypatch.setattr(serper_mod, "_NEWS_OFF_UNTIL", 0.0)
    monkeypatch.setattr(serper_mod, "_collect", lambda p, news: ([], {}))
    serper_mod._build(serper_mod.parse_body({"q": "sehr nischiges thema"}), True)
    assert not serper_mod._news_disabled()


def test_base_order_global_ueber_seiten():
    """Yahoo-Platz 1 auf Seite 2 (global Platz 11) steht vor einem reinen Yandex-Treffer
    von Seite 1 (Engine-Score 0), aber hinter Yahoo-Platz 5 auf Seite 1."""
    rs = [
        {"url": "https://a.example/", "engines": ["yandex"], "positions": [1], "_page": 1},
        {"url": "https://b.example/", "engines": ["yahoo"], "positions": [1], "_page": 2},
        {"url": "https://c.example/", "engines": ["yahoo"], "positions": [5], "_page": 1},
    ]
    assert [r["url"][8] for r in serper._base_order(rs)] == ["c", "b", "a"]
