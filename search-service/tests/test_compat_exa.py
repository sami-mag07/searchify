#!/usr/bin/env python3
"""Offline-Tests fuer die Exa-kompatiblen Endpunkte (app/compat/exa.py).

Kein Netz, kein Modell: run_search, read_pages und der Cross-Encoder werden per
monkeypatch ersetzt. Die Test-App enthaelt nur den Exa-Router.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import pages as pages_mod  # noqa: E402
from app import rerank as rerank_mod  # noqa: E402
from app import search as search_mod  # noqa: E402
from app.compat import exa  # noqa: E402
from app.schema import SearchResponse, SearchResult  # noqa: E402

TEXT_DE = (
    "Berlin ist eine grosse Stadt. Die Hauptstadt von Australien ist Canberra. "
    "Viele denken faelschlich an Sydney. Am 3. Oktober feiert Deutschland. Das Wetter war schoen."
)
TEXT_ES = (
    "Madrid es una ciudad grande. ¿Cuál es la capital de Australia? La capital es Canberra, "
    "p. ej. según el Sr. García. ¡Muchos creen que es Sídney! El clima fue bueno."
)
TEXT_FR = (
    "Paris est une grande ville. « La capitale de l’Australie est Canberra. » Vraiment ? "
    "Oui, selon M. Dupont, p. ex. dans son livre. Il faisait beau."
)

PAGES = {
    "https://a.org/neu": {"title": "Neu", "published": "2026-09-01", "content": TEXT_DE},
    "https://b.org/alt": {"title": "Alt", "published": "2019-01-01", "content": TEXT_DE},
    "https://c.org/ohne": {"title": "Ohne Datum", "published": None, "content": TEXT_DE},
    "https://quelle.org/artikel": {
        "title": "Canberra Hauptstadt Australien | Quelle",
        "published": "2026-01-01",
        "content": "Canberra Canberra Parlament Parlament Australien Hauptstadt " * 3,
    },
}


def _doc(url):
    page = PAGES.get(url, {"title": "Sonst", "published": None, "content": TEXT_DE})
    return {
        "url": url, "title": page["title"], "published": page["published"], "author": "Autorin",
        "description": "Beschreibung", "content": page["content"],
        "links": [{"text": "Mehr", "url": "https://x.org/mehr"}, {"text": "Kontakt", "url": "https://x.org/k"}],
    }


def fake_rerank(query, chunks, top_pool=None):
    """Score = Anteil der Query-Woerter im Chunk (statt Cross-Encoder)."""
    q = set(rerank_mod._tokens(query))
    out = []
    for ch in chunks:
        words = set(rerank_mod._tokens(ch.get("content", "")))
        raw = len(q & words) / (len(q) or 1)
        out.append(dict(ch, raw_score=raw, score=round(raw, 4)))
    out.sort(key=lambda c: c["raw_score"], reverse=True)
    return out


@pytest.fixture()
def env(monkeypatch):
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    state = {"hits": list(PAGES)[:3], "requests": [], "reads": []}

    def fake_run_search(req):
        state["requests"].append(req)
        hits = [u for u in state["hits"] if not search_mod._host_matches(u, req.exclude_domains)]
        results = [
            SearchResult(title=PAGES.get(u, {}).get("title", "T"), url=u, content=f"Snippet {u} Canberra",
                         score=0.9 - i * 0.1, raw_content=None)
            for i, u in enumerate(hits[: req.max_results])
        ]
        return SearchResponse(query=req.query, results=results, response_time=0.01, request_id="x")

    def fake_read_pages(urls, fmt="text", timeout=None):
        state["reads"].extend(urls)
        docs = [_doc(u) for u in urls if "kaputt" not in u]
        failed = [{"url": u, "error": "Seite nicht ladbar (Fehler)"} for u in urls if "kaputt" in u]
        return docs, failed

    monkeypatch.setattr(exa, "_run_search", fake_run_search)
    monkeypatch.setattr(pages_mod, "read_pages", fake_read_pages)
    monkeypatch.setattr(rerank_mod, "rerank", fake_rerank)
    app = FastAPI()
    app.include_router(exa.router)
    return TestClient(app), state


def test_search_antwortform(env):
    client, state = env
    r = client.post("/exa/search", json={"query": "Hauptstadt Australien", "numResults": 2,
                                         "contents": {"text": {"maxCharacters": 40}},
                                         "language": "de"})
    assert r.status_code == 200
    data = r.json()
    assert set(data) >= {"requestId", "resolvedSearchType", "results", "costDollars"}
    assert data["resolvedSearchType"] == "neural-rerank"
    assert data["costDollars"] == {"total": 0}
    assert len(data["results"]) == 2
    first = data["results"][0]
    assert first["id"] == first["url"] == "https://a.org/neu"
    assert first["publishedDate"] == "2026-09-01T00:00:00.000Z"
    assert first["author"] == "Autorin"
    assert 0 <= first["score"] <= 1
    assert len(first["text"]) == 40
    assert "highlights" not in first and "summary" not in first  # ohne LLM-Key kein summary
    assert state["requests"][0].language == "de"
    assert state["requests"][0].include_answer is False


def test_search_ohne_inhalte_und_keyword(env):
    client, _ = env
    data = client.post("/exa/search", json={"query": "x", "type": "keyword"}).json()
    assert data["resolvedSearchType"] == "keyword"
    assert all("text" not in r for r in data["results"])


def test_legacy_felder_und_links(env):
    client, _ = env
    data = client.post("/exa/search", json={"query": "Hauptstadt", "text": True, "numResults": 1,
                                            "extras": {"links": 1}}).json()
    res = data["results"][0]
    assert res["text"].startswith("Berlin")
    assert res["extras"]["links"] == ["https://x.org/mehr"]


def test_highlights_auswahl(env):
    client, _ = env
    data = client.post("/exa/search", json={
        "query": "Hauptstadt Australien Canberra", "numResults": 1,
        "contents": {"highlights": {"numSentences": 1, "highlightsPerUrl": 2}},
    }).json()
    res = data["results"][0]
    assert res["highlights"][0] == "Die Hauptstadt von Australien ist Canberra."
    assert len(res["highlights"]) == len(res["highlightScores"]) == 2
    assert res["highlightScores"][0] >= res["highlightScores"][1]


def test_highlights_spanisch_und_franzoesisch(monkeypatch):
    monkeypatch.setattr(rerank_mod, "rerank", fake_rerank)
    es, fr = exa.highlights_for(
        "capital Canberra",
        [{"title": "es", "text": TEXT_ES}, {"title": "fr", "text": TEXT_FR}],
        num_sentences=1, per_url=1,
    )
    assert es[0] == ["La capital es Canberra, p. ej. según el Sr. García."]
    assert fr[0] == ["« La capitale de l’Australie est Canberra. »"]


def test_satzzerlegung_vier_sprachen():
    assert exa.split_sentences(TEXT_ES)[1:4] == [
        "¿Cuál es la capital de Australia?",
        "La capital es Canberra, p. ej. según el Sr. García.",
        "¡Muchos creen que es Sídney!",
    ]
    assert exa.split_sentences(TEXT_FR)[2:4] == ["Vraiment ?", "Oui, selon M. Dupont, p. ex. dans son livre."]
    assert "Am 3. Oktober feiert Deutschland." in exa.split_sentences(TEXT_DE)
    assert exa.split_sentences("The U.S. economy, e.g. jobs, grew 5.4 percent. Mr. Smith agreed.") == [
        "The U.S. economy, e.g. jobs, grew 5.4 percent.", "Mr. Smith agreed."]


def test_datumsfilter(env):
    client, _ = env
    data = client.post("/exa/search", json={"query": "x", "numResults": 5,
                                            "startPublishedDate": "2025-01-01T00:00:00.000Z"}).json()
    urls = [r["url"] for r in data["results"]]
    # alt (2019) faellt raus, ohne Datum bleibt erhalten, aber hinten
    assert urls == ["https://a.org/neu", "https://c.org/ohne"]
    data = client.post("/exa/search", json={"query": "x", "endPublishedDate": "2020-01-01"}).json()
    assert [r["url"] for r in data["results"]] == ["https://b.org/alt", "https://c.org/ohne"]


def test_domain_pfad_und_include_text(env):
    client, state = env
    data = client.post("/exa/search", json={"query": "x", "excludeDomains": ["https://www.b.org/alt"],
                                            "includeText": ["Canberra"]}).json()
    assert "https://b.org/alt" not in [r["url"] for r in data["results"]]
    assert state["requests"][-1].exclude_domains == ["b.org"]
    data = client.post("/exa/search", json={"query": "x", "excludeText": ["Sydney"]}).json()
    assert data["results"] == []


def test_contents_mit_fehlschlag(env):
    client, _ = env
    r = client.post("/exa/contents", json={"urls": ["https://a.org/neu", "https://kaputt.org/", "keine-url"],
                                           "highlights": {"query": "Hauptstadt Australien"}})
    assert r.status_code == 200
    data = r.json()
    assert [x["url"] for x in data["results"]] == ["https://a.org/neu"]
    assert "Canberra" in data["results"][0]["highlights"][0]
    st = {s["id"]: s for s in data["statuses"]}
    assert st["https://a.org/neu"]["status"] == "success"
    assert st["https://kaputt.org/"]["error"]["tag"] == "SOURCE_NOT_AVAILABLE"
    assert st["keine-url"]["error"]["tag"] == "INVALID_URL"
    # ids statt urls, ohne Optionen -> Text wie bei Exa
    data = client.post("/exa/contents", json={"ids": ["https://a.org/neu"]}).json()
    assert data["results"][0]["text"].startswith("Berlin")


def test_find_similar_schliesst_quelle_aus(env):
    client, state = env
    state["hits"] = ["https://quelle.org/artikel", "https://quelle.org/anderes", "https://a.org/neu",
                     "https://c.org/ohne"]
    data = client.post("/exa/findSimilar", json={"url": "https://quelle.org/artikel",
                                                 "excludeSourceDomain": True, "numResults": 3}).json()
    urls = [r["url"] for r in data["results"]]
    assert urls and not any("quelle.org" in u for u in urls)
    req = state["requests"][-1]
    assert req.exclude_domains == ["quelle.org"]
    assert req.query.startswith("Canberra Hauptstadt Australien")
    assert "parlament" in req.query
    # ohne excludeSourceDomain: andere Seiten der Domain ja, die Quelle selbst nie
    data = client.post("/exa/findSimilar", json={"url": "https://quelle.org/artikel"}).json()
    urls = [r["url"] for r in data["results"]]
    assert "https://quelle.org/anderes" in urls and "https://quelle.org/artikel" not in urls


def test_summary_nur_mit_llm(env, monkeypatch):
    client, _ = env
    data = client.post("/exa/search", json={"query": "x", "numResults": 1, "contents": {"summary": True}}).json()
    assert "summary" not in data["results"][0]
    monkeypatch.setenv("OPENAI_API_KEY", "test-platzhalter")
    monkeypatch.setattr(exa.answer_mod, "generate_answer", lambda q, src: ("Kurz gesagt Canberra [1].", {}))
    data = client.post("/exa/search", json={"query": "x", "numResults": 1, "contents": {"summary": True}}).json()
    assert data["results"][0]["summary"] == "Kurz gesagt Canberra."


def test_auth_mit_service_key(env, monkeypatch):
    client, _ = env
    monkeypatch.setenv("SERVICE_API_KEY", "geheim-test")
    assert client.post("/exa/search", json={"query": "x"}).status_code == 401
    assert client.post("/exa/contents", json={"urls": ["https://a.org/neu"]},
                       headers={"x-api-key": "falsch"}).status_code == 401
    ok = client.post("/exa/search", json={"query": "x"}, headers={"x-api-key": "geheim-test"})
    assert ok.status_code == 200
    ok = client.post("/exa/findSimilar", json={"url": "https://quelle.org/artikel"},
                     headers={"Authorization": "Bearer geheim-test"})
    assert ok.status_code == 200


def test_backend_fehler_als_exa_fehler(env, monkeypatch):
    client, _ = env

    def boom(req):
        raise search_mod.SearchBackendError("intern")

    monkeypatch.setattr(exa, "_run_search", boom)
    r = client.post("/exa/search", json={"query": "x"})
    assert r.status_code == 502
    assert r.json()["tag"] == "SEARCH_BACKEND_UNAVAILABLE"
    assert "intern" not in r.text


def test_hilfsfunktionen():
    assert exa._language("de-DE") == "de" and exa._language("Français") == "fr"
    assert exa._language("it") is None
    assert exa.guess_language(TEXT_ES) == "es" and exa.guess_language(TEXT_FR) == "fr"
    assert exa._matches("https://docs.example.com/docs/a", ["*.example.com"])
    assert not exa._matches("https://example.com/blog", ["example.com/docs"])
    assert exa._parse_date("2024-05") .isoformat() == "2024-05-01"


def test_titel_ohne_seitenname():
    assert exa._clean_title("Canberra – Wikipedia", "de.wikipedia.org") == "Canberra"
    assert exa._clean_title("Die Energiewende | Tagesspiegel", "www.tagesspiegel.de") == "Die Energiewende"
    assert exa._clean_title("Le Monde - Actualités", "lemonde.fr") == "Actualités"


def test_diversify_moves_extra_same_domain_hits_back():
    from app.compat.exa import diversify

    urls = ["https://a.com/1", "https://www.a.com/2", "https://a.com/3", "https://b.com/1", "https://a.com/4"]
    out = [it["url"] for it in diversify([{"url": u} for u in urls], per_domain=2)]
    assert out == ["https://a.com/1", "https://www.a.com/2", "https://b.com/1", "https://a.com/3", "https://a.com/4"]



def test_vorfilter_streut_nachbarfenster():
    wins = ["Canberra Canberra a", "Canberra Canberra b", "Canberra c", "nichts", "Canberra d"]
    keep = exa._prefilter("Canberra", wins, keep=3)
    assert keep[0] in (0, 1)
    assert all(abs(a - b) >= 2 for a in keep for b in keep if a != b)


def test_highlight_aus_kern_abschnitt(monkeypatch):
    """Steht der beste Kern-Abschnitt (Such-Snippet) nicht im Volltext, wird er
    selbst Kandidat und kann Highlight werden; ein Datum vorne faellt weg."""
    monkeypatch.setattr(rerank_mod, "rerank", fake_rerank)
    seed = "Sep 17, 2026 · Die Hauptstadt von Australien ist Canberra laut Atlas. [...] Rest hier"
    (hl, _), = exa.highlights_for(
        "Hauptstadt Australien Canberra",
        [{"title": "t", "text": "Berlin ist gross. Das Wetter war schoen.", "seed": seed}],
        num_sentences=1, per_url=1,
    )
    assert hl == ["Die Hauptstadt von Australien ist Canberra laut Atlas."]
    idx, extra = exa._seed_windows("Die Hauptstadt von Australien ist Canberra.", ["x", TEXT_DE])
    assert idx == [1] and extra == []


def test_ohne_filter_genau_n_kandidaten(env):
    client, state = env
    client.post("/exa/search", json={"query": "Canberra", "numResults": 3})
    assert state["requests"][-1].max_results == 3


def test_with_description_prepends_long_missing_description():
    from app.compat.exa import with_description

    desc = "Die Bundesversammlung waehlt den Bundespraesidenten fuer eine Dauer von fuenf Jahren. " * 3
    assert with_description("Tabelle | Link", desc).startswith("Die Bundesversammlung")
    # schon im Text enthalten: unveraendert
    assert with_description("Intro. " + desc, desc) == "Intro. " + desc
    # kurze SEO-Beschreibung: unveraendert
    assert with_description("Body", "Jetzt guenstig kaufen!") == "Body"


def test_clip_cuts_at_word_boundary():
    from app.compat.exa import _clip

    assert _clip("eins zwei drei", 9) == "eins"
    assert len(_clip("wort " * 500, 400)) <= 400
