#!/usr/bin/env python3
"""Offline-Tests fuer die Jina-kompatiblen Endpunkte (app/compat/jina.py).

Kein Netz: read_pages, web_search und die DNS-Pruefung werden per monkeypatch
ersetzt. Die Test-App enthaelt nur den Jina-Router.
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import pages as pages_mod  # noqa: E402
from app.compat import reader as reader_mod  # noqa: E402
from app.extract import extract_links as extract_links_fn  # noqa: E402
from app import search as search_mod  # noqa: E402
from app.compat import jina  # noqa: E402

PUBLIC_IP = "93.184.216.34"


def _doc(url, title="Beispiel", content="# Ueberschrift\n\nHaupttext.", links=None, published="2026-10-01"):
    return {
        "url": url,
        "title": title,
        "published": published,
        "author": None,
        "description": "Kurzbeschreibung",
        "content": content,
        "links": links or [],
    }


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)
    # Die Suche laeuft ueber jina.ranked_hits (eigene Pipeline mit Ranking). Hier auf
    # search_mod.web_search umgelenkt, damit die Tests sie ohne Netz faken koennen.
    monkeypatch.setattr(jina, "ranked_hits", lambda q, lang: search_mod.web_search(
        q, jina.SEARCH_RESULTS, search_depth="basic", topic="general", language=lang
    )[: jina.SEARCH_RESULTS])

    def fake_getaddrinfo(host, *args, **kwargs):
        if host.endswith(".invalid"):
            raise socket.gaierror("nicht aufloesbar")
        ip = "10.0.0.5" if host == "intern.example" else PUBLIC_IP
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))]

    monkeypatch.setattr(jina.socket, "getaddrinfo", fake_getaddrinfo)
    calls = []

    def fake_read_pages(urls, fmt="text", timeout=None, **_):
        calls.append({"urls": list(urls), "fmt": fmt, "timeout": timeout})
        docs = [_doc(u, links=[{"text": "Jetzt bewerben", "url": "https://x.org/bewerben"},
                               {"text": "Impressum", "url": "https://x.org/impressum"}])
                for u in urls if "kaputt" not in u]
        failed = [{"url": u, "error": "Seite nicht ladbar"} for u in urls if "kaputt" in u]
        return docs, failed

    monkeypatch.setattr(pages_mod, "read_pages", fake_read_pages)  # Search
    monkeypatch.setattr(reader_mod, "read_pages", fake_read_pages)  # Reader (Lese-Modus)
    app = FastAPI()
    app.include_router(jina.router)
    c = TestClient(app)
    c.calls = calls
    return c


def test_reader_text_format(client):
    r = client.get("/jina/r/https://example.com/artikel")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    assert r.text.startswith(
        "Title: Beispiel\n\nURL Source: https://example.com/artikel\n\n"
        "Published Time: 2026-10-01\n\nMarkdown Content:\n# Ueberschrift"
    )
    assert "Links/Buttons" not in r.text
    assert client.calls[0]["fmt"] == "markdown"


def test_reader_json_via_accept(client):
    r = client.get("/jina/r/https://example.com/", headers={"Accept": "application/json"})
    body = r.json()
    assert body["code"] == 200 and body["status"] == 20000
    data = body["data"]
    assert data["title"] == "Beispiel"
    assert data["url"] == "https://example.com/"
    assert data["publishedTime"] == "2026-10-01"
    assert data["description"] == "Kurzbeschreibung"
    assert "links" not in data


def test_reader_links_summary_action_first(client):
    r = client.get("/jina/r/https://example.com/", headers={"X-With-Links-Summary": "true"})
    assert "Links/Buttons:\n- [Jetzt bewerben](https://x.org/bewerben)\n- [Impressum]" in r.text
    j = client.get(
        "/jina/r/https://example.com/",
        headers={"X-With-Links-Summary": "true", "Accept": "application/json"},
    ).json()
    assert list(j["data"]["links"]) == ["Jetzt bewerben", "Impressum"]


def test_reader_url_without_scheme_keeps_query(client):
    r = client.get("/jina/r/example.com/suche?a=1&b=zwei")
    assert r.status_code == 200
    assert client.calls[-1]["urls"] == ["https://example.com/suche?a=1&b=zwei"]
    assert "URL Source: https://example.com/suche?a=1&b=zwei" in r.text


def test_normalize_target_repairs_collapsed_slash():
    assert jina.normalize_target("https:/example.com/x") == "https://example.com/x"
    assert jina.normalize_target("http://a.de/p?x=1", "y=2") == "http://a.de/p?x=1&y=2"


def test_reader_post_return_format_and_timeout(client):
    r = client.post(
        "/jina/r", json={"url": "example.com"},
        headers={"X-Return-Format": "text", "X-Timeout": "60"},
    )
    assert r.status_code == 200
    assert client.calls[-1] == {"urls": ["https://example.com"], "fmt": "text", "timeout": 15.0}


def test_reader_no_cache_drops_entry(client):
    cache = pages_mod._docs()
    cache.put("reader-markdown|https://example.com/c", {"content": "alt"})
    cache.put("markdown|https://example.com/c", {"content": "such"})
    client.get("/jina/r/https://example.com/c", headers={"X-No-Cache": "true"})
    assert cache.get("reader-markdown|https://example.com/c") is None
    assert cache.get("markdown|https://example.com/c") == {"content": "such"}


@pytest.mark.parametrize(
    "target", ["http://127.0.0.1/admin", "http://localhost:8888/", "intern.example/x",
               "http://169.254.169.254/latest", "file:///etc/passwd"],
)
def test_reader_blocks_internal(client, target):
    r = client.get(f"/jina/r/{target}")
    assert r.status_code == 400
    assert r.json()["data"] is None
    assert client.calls == []


def test_reader_unloadable_is_422(client):
    r = client.get("/jina/r/https://example.com/kaputt")
    assert r.status_code == 422
    assert "nicht laden" in r.json()["message"]
    assert client.get("/jina/r/https://gibtsnicht.invalid/").status_code == 422


def test_reader_utf8_accents(client, monkeypatch):
    """FR-/ES-Seite: Akzente kommen unveraendert und als UTF-8 an."""
    texte = {"fr": "L'été à Montréal: crème brûlée, garçon, œuvre.",
             "es": "¿Dónde está la estación? Año, niño, pingüino."}

    def fake(urls, fmt="text", timeout=None):
        lang = "fr" if "fr" in urls[0] else "es"
        return [_doc(urls[0], title=f"Titre {lang} é", content=texte[lang])], []

    monkeypatch.setattr(reader_mod, "read_pages", fake)
    for lang, text in texte.items():
        r = client.get(f"/jina/r/https://{lang}.example.org/")
        assert "charset=utf-8" in r.headers["content-type"]
        assert text in r.content.decode("utf-8")
        j = client.get(f"/jina/r/https://{lang}.example.org/", headers={"Accept": "application/json"})
        assert j.json()["data"]["content"] == text


def _fake_hits(n=7):
    hits = [{"title": f"Treffer {i}", "url": f"https://site{i}.example/seite", "content": f"Snippet {i}"}
            for i in range(1, n + 1)]
    hits[2]["url"] = "https://kaputt.example/seite"
    return hits


def test_search_text_five_hits_and_snippet_fallback(client, monkeypatch):
    seen = {}

    def fake_search(query, n, **kwargs):
        seen.update(query=query, n=n, **kwargs)
        return _fake_hits()

    monkeypatch.setattr(search_mod, "web_search", fake_search)
    r = client.get("/jina/s/beste Suchmaschine fuer Agenten")
    assert r.status_code == 200
    assert seen["query"] == "beste Suchmaschine fuer Agenten"
    assert seen["language"] == "de"
    text = r.text
    assert text.count("Title: ") == 5 and "[5] Title:" in text and "[6]" not in text
    assert "[1] Title: Beispiel\n[1] URL Source: https://site1.example/seite\n" \
           "[1] Description: Snippet 1\n" in text
    assert "[1] Markdown Content:\n# Ueberschrift" in text
    # Treffer 3 laedt nicht: Snippet als Inhalt
    assert "[3] Markdown Content:\nSnippet 3" in text
    assert len(client.calls[-1]["urls"]) == 5


def test_search_json_and_post(client, monkeypatch):
    seen = {}

    def fake_search(query, n, **kwargs):
        seen.update(query=query, **kwargs)
        return _fake_hits()

    monkeypatch.setattr(search_mod, "web_search", fake_search)
    r = client.post("/jina/s", json={"q": "climate news", "hl": "fr"},
                    headers={"Accept": "application/json"})
    body = r.json()
    assert body["code"] == 200 and len(body["data"]) == 5
    assert set(body["data"][0]) >= {"title", "url", "description", "content"}
    assert body["data"][2]["content"] == "Snippet 3"
    assert seen["language"] == "fr"


@pytest.mark.parametrize(
    "query,headers,params,expected",
    [
        ("weather tomorrow", {}, {}, None),
        ("¿Cuál es la mejor playa de España?", {}, {}, "es"),
        ("comment faire une crêpe", {}, {}, "fr"),
        ("wie spät ist es", {}, {}, "de"),
        ("weather tomorrow", {"Accept-Language": "es-ES,es;q=0.9"}, {}, "es"),
        ("wie spät ist es", {"Accept-Language": "de"}, {"hl": "en"}, "en"),
        ("weather", {"Accept-Language": "ja-JP"}, {}, None),
    ],
)
def test_search_language(client, monkeypatch, query, headers, params, expected):
    seen = {}
    monkeypatch.setattr(search_mod, "web_search",
                        lambda q, n, **kw: seen.update(kw) or _fake_hits())
    client.get(f"/jina/s/{query}", headers=headers, params=params)
    assert seen["language"] == expected


def test_search_backend_down_is_502(client, monkeypatch):
    def boom(*a, **k):
        raise search_mod.SearchBackendError("weg")

    monkeypatch.setattr(search_mod, "web_search", boom)
    assert client.get("/jina/s/irgendwas").status_code == 502


def test_auth_with_service_key(client, monkeypatch):
    monkeypatch.setenv("SERVICE_API_KEY", "geheim-test")
    monkeypatch.setattr(search_mod, "web_search", lambda q, n, **kw: _fake_hits())
    assert client.get("/jina/r/https://example.com/").status_code == 401
    assert client.get("/jina/s/test", headers={"Authorization": "Bearer falsch"}).status_code == 401
    ok = {"Authorization": "Bearer geheim-test"}
    assert client.get("/jina/r/https://example.com/", headers=ok).status_code == 200
    assert client.post("/jina/s", json={"q": "test"}, headers=ok).status_code == 200


def test_ranked_hits_nutzt_die_gerankte_pipeline(monkeypatch):
    from app import main as main_mod
    from app.schema import SearchResponse, SearchResult

    seen = {}

    def fake_run_search(req):
        seen["req"] = req
        return SearchResponse(query=req.query, results=[
            SearchResult(title="Ottawa", url="https://ottawa.example/", content="Capital", score=0.9)
        ], response_time=0.1, request_id="x")

    monkeypatch.setattr(main_mod, "run_search", fake_run_search)
    hits = jina.ranked_hits("What is the capital of Canada", "en")
    assert hits == [{"title": "Ottawa", "url": "https://ottawa.example/", "content": "Capital"}]
    assert seen["req"].max_results == jina.SEARCH_RESULTS and seen["req"].language == "en"


# ---------------------------------------------------------------- Lese-Modus (app/compat/reader.py)


def test_reader_titel_og_und_ohne_wikipedia_suffix():
    from lxml import html as lxml_html

    og = lxml_html.fromstring('<html><head><title>Lang | Seite</title>'
                              '<meta property="og:title" content="Kurz"></head><body></body></html>')
    assert reader_mod.reader_title(og, None) == "Kurz"
    wiki = lxml_html.fromstring("<html><head><title>Berlin – Wikipedia</title></head><body></body></html>")
    assert reader_mod.reader_title(wiki, None) == "Berlin"
    es = lxml_html.fromstring("<html><head><title>Madrid - Wikipedia, la enciclopedia libre</title></head></html>")
    assert reader_mod.reader_title(es, None) == "Madrid"
    assert reader_mod.reader_title(None, "Meta-Titel") == "Meta-Titel"


def test_reader_links_mehr_als_25_und_selbstlink():
    anchors = "".join(f'<a href="/seite{i}">Seite {i}</a>' for i in range(60))
    html = (f'<html><body><a href="#bewerben">Jetzt bewerben</a>{anchors}'
            '<a href="javascript:void(0)">Nix</a></body></html>')
    from lxml import html as lxml_html

    links = reader_mod.reader_links(lxml_html.fromstring(html), "https://x.org/programm")
    assert len(links) == 61
    assert links[0] == {"text": "Jetzt bewerben", "url": "https://x.org/programm#bewerben"}
    assert all(not l["url"].startswith("javascript") for l in links)


def test_reader_links_knopf_mit_sprungmarke_nach_selbstlink():
    """Erst "Zum Inhalt springen" (#main), dann der Knopf "Apply" (#modal_box): ohne
    Sprungmarke waere der Knopf eine Dublette des ersten Selbstlinks und fiele weg."""
    html = ('<html><body><a href="#main">Skip to main content</a><a href="/about">About</a>'
            '<a href="#modal_box" class="btn">Apply</a></body></html>')
    from lxml import html as lxml_html

    links = reader_mod.reader_links(lxml_html.fromstring(html), "https://x.org/opp/1")
    assert links[0] == {"text": "Apply", "url": "https://x.org/opp/1#modal_box"}
    assert {"text": "Skip to main content", "url": "https://x.org/opp/1#main"} in links


def test_extract_links_suche_ohne_sprungmarken():
    """Such-Pipeline (keep_self=False) unveraendert: Sprungmarken und Selbstlinks raus."""
    html = ('<html><body><a href="#x">Oben</a><a href="/a#teil">A</a><a href="/a">A2</a>'
            '<a href="https://x.org/p#y">Selbst</a></body></html>')
    links = extract_links_fn(html, "https://x.org/p")
    assert links == [{"text": "A", "url": "https://x.org/a"}]


def test_reader_formular_beschriftungen_bleiben():
    """Bewerbungsformular: Fragen in <label> sind Inhalt, Auswahllisten nicht."""
    fragen = "".join(
        f'<label for="f{i}">Bitte beschreiben Sie Frage{i} ausfuehrlich (max. 1000 Zeichen)</label>'
        f'<textarea id="f{i}"></textarea>' for i in range(8))
    html = ('<html><head><title>Antrag</title></head><body><h1>Antrag stellen</h1>'
            f'<form action="/senden">{fragen}<select><option>Afghanistan</option>'
            '<option>Albanien</option></select><input type="submit" value="Senden"></form>'
            '</body></html>')
    text = reader_mod.reader_content(html, "https://x.org/antrag", "markdown")
    for i in (0, 7):
        assert f"Frage{i}" in text
    assert "Albanien" not in text


def test_reader_teaser_startseite_liefert_ganze_seite():
    """Startseite aus vielen kurzen Teasern: der Haupttext-Extraktor nimmt nur einen
    Teil, der Lese-Modus faellt auf die ganze Seite zurueck."""
    teaser = "".join(
        f'<div class="teaser"><h3><a href="/a{i}">Schlagzeile Nummer {i} zu Thema{i}</a></h3>'
        f"<p>Kurzer Anreisser {i} mit Woertern wie Regierung Wirtschaft Klima{i}.</p></div>"
        for i in range(30)
    )
    html = f"<html><head><title>News</title></head><body><main>{teaser}</main></body></html>"
    doc = reader_mod.reader_document(html, "https://news.example/", "markdown")
    for i in (0, 15, 29):
        assert f"Klima{i}" in doc["content"]
    assert doc["title"] == "News"


def test_reader_kodierter_pfad_bleibt_erhalten(client):
    """%3F im Pfad ist Teil der Ziel-URL (kein Query-Beginn), wie beim Original."""
    r = client.get("/jina/r/https://git-scm.com/book/de/v2/Was-ist-Versionsverwaltung%3F")
    assert r.status_code == 200
    assert client.calls[-1]["urls"] == ["https://git-scm.com/book/de/v2/Was-ist-Versionsverwaltung%3F"]
    client.get("/jina/r/https%3A%2F%2Fexample.com%2Fseite")
    assert client.calls[-1]["urls"] == ["https://example.com/seite"]
    client.get("/jina/r/https://example.com/a?x=1")
    assert client.calls[-1]["urls"] == ["https://example.com/a?x=1"]


def test_reader_titel_wie_readability():
    from lxml import html as lxml_html

    def title(doc):
        return reader_mod.reader_title(lxml_html.fromstring(doc), None)

    # Seitenname hinter dem letzten Trenner faellt weg, wenn genug Woerter bleiben
    assert title("<html><head><title>Bourses pour les étudiants étrangers | Campus France"
                 "</title></head><body><p>x</p></body></html>") == "Bourses pour les étudiants étrangers"
    # bleibt vorn zu wenig, faellt der erste Teil weg
    assert title("<html><head><title>BOE.es - Agencia Estatal Boletín Oficial del Estado</title>"
                 "</head><body><p>x</p></body></html>") == "Agencia Estatal Boletín Oficial del Estado"
    # kurze Titel bleiben ganz
    assert title("<html><head><title>Introduction - JavaScript | MDN</title></head>"
                 "<body><p>x</p></body></html>") == "Introduction - JavaScript | MDN"
    # JSON-LD-Artikel vor Meta-Titeln
    assert title('<html><head><title>T | S</title><meta property="og:title" content="OG">'
                 '<script type="application/ld+json">{"@context":"https://schema.org",'
                 '"@type":"NewsArticle","headline":"Schlagzeile"}</script></head><body></body></html>'
                 ) == "Schlagzeile"
    # kaputtes JSON-LD stoert nicht
    assert title('<html><head><meta property="og:title" content="OG">'
                 '<script type="application/ld+json">{kaputt</script></head><body></body></html>') == "OG"


def test_reader_wiki_tabelle_als_markdown_zeilen():
    html = ('<html><body><div class="mw-parser-output"><h2>Liste</h2>'
            '<table><tr><th>Rang</th><th>Stadt<br>Name</th></tr>'
            '<tr><td>1</td><td>Paris<span style="display: none">zzsort</span></td></tr>'
            '<tr><td>2</td><td>Marseille</td></tr></table></div></body></html>')
    text = reader_mod._wiki_text(html, "markdown")
    assert "| Rang | Stadt Name |" in text
    assert "| --- | --- |" in text
    assert "| 1 | Paris |" in text and "| 2 | Marseille |" in text
    assert "zzsort" not in text


def test_reader_links_im_markdown_wie_original():
    """Markdown traegt die Links wie r.jina.ai: [Text](absolute URL "Titel"), Anker
    in Dokument-Reihenfolge, nur ganze Woerter."""
    from lxml import html as lxml_html

    html = ('<html><body><nav><a href="/x">Paris</a></nav><p>Die Hauptstadt ist '
            '<a href="/wiki/Paris" title="Paris (Stadt)">Paris</a>, nicht Parisienne.</p>'
            '<p>Mehr unter <a href="https://ex.org/a b">Quelle</a>.</p></body></html>')
    tree = lxml_html.fromstring(html)
    text = "Die Hauptstadt ist Paris, nicht Parisienne.\n\nMehr unter Quelle."
    out = reader_mod.inline_links(text, tree, "https://de.example/seite")
    assert out.count("](") == 2
    assert '[Paris](https://de.example/x)' in out  # erster Anker mit diesem Text
    assert '"Paris (Stadt)"' not in out  # zweiter Paris-Anker findet keinen Text mehr
    assert "Parisienne" in out and "[Parisienne" not in out
    assert "[Quelle](https://ex.org/a%20b)" in out
    # Text ohne passende Anker bleibt unveraendert
    assert reader_mod.inline_links("Nichts davon.", tree, "https://de.example/") == "Nichts davon."


def test_reader_dokument_markdown_mit_links_und_ohne_leerzeilen_stapel():
    html = ('<html><head><title>Antrag stellen bei der Stiftung</title></head><body><article>'
            '<h1>Antrag</h1><p>' + "Lange Beschreibung des Programms mit vielen Woertern. " * 20
            + '<a href="/regeln">Regeln</a> lesen.</p><form action="/s"><label>Projekttitel</label>'
            '<input><label>Budget in Euro</label><input></form></article></body></html>')
    doc = reader_mod.reader_document(html, "https://stiftung.example/antrag", "markdown")
    assert "[Regeln](https://stiftung.example/regeln)" in doc["content"]
    assert "Projekttitel" in doc["content"] and "Budget in Euro" in doc["content"]
    assert "\n\n\n" not in doc["content"]
    text = reader_mod.reader_document(html, "https://stiftung.example/antrag", "text")["content"]
    assert "](" not in text


def test_reader_metadaten_ohne_autorsuche_auf_grossen_seiten(monkeypatch):
    """Autor-Suche im Seitentext (teuer) nur auf kleinen Seiten; Meta-Autor immer."""
    from lxml import html as lxml_html
    import trafilatura.metadata as tm

    calls = []
    monkeypatch.setattr(tm, "extract_author", lambda tree: calls.append(1) or "Heuristik")
    small = ('<html><head><meta name="description" content="Kurz &amp; gut">'
             '<meta property="article:published_time" content="2024-03-05"></head>'
             '<body><p>Text</p></body></html>')
    meta = reader_mod._reader_metadata(small, "https://ex.org/a", lxml_html.fromstring(small))
    assert meta["date"] == "2024-03-05" and meta["description"] == "Kurz & gut"
    assert meta["author"] == "Heuristik" and calls == [1]
    big = small.replace("<p>Text</p>", "<p>x</p>" * (reader_mod.AUTHOR_SCAN_MAX_CHARS // 8))
    meta = reader_mod._reader_metadata(big, "https://ex.org/b", lxml_html.fromstring(big))
    assert meta["author"] is None and calls == [1]
    with_meta = big.replace("<head>", '<head><meta name="author" content="Ada Lovelace">')
    meta = reader_mod._reader_metadata(with_meta, "https://ex.org/c", lxml_html.fromstring(with_meta))
    assert meta["author"] == "Ada Lovelace"


def test_reader_wiki_ohne_trafilatura():
    """MediaWiki mit genug Text: Seite ohne Rahmen, ohne Bearbeiten-Links."""
    body = "<p>" + "Berlin ist die Hauptstadt Deutschlands und eine grosse Stadt. " * 30 + "</p>"
    html = ('<html><body><div id="mw-navigation">Navigation Hauptseite</div>'
            '<div class="mw-parser-output"><div class="mw-heading"><h2>Geschichte</h2>'
            '<span class="mw-editsection">[<a href="?action=edit">Bearbeiten</a>]</span></div>'
            + body + '</div></body></html>')
    text = reader_mod.reader_content(html, "https://de.wikipedia.org/wiki/Berlin", "markdown")
    assert "## Geschichte" in text and "Bearbeiten" not in text and "Navigation" not in text
