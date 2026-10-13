"""Offline-Tests fuer den Tavily-Extract-kompatiblen Endpunkt (app/compat/tavily_extract.py).

Seitenabruf gestubbt, kein Netz. Ein Test laeuft durch die echte Extraktion
(app.compat.reader -> trafilatura) mit einer franzoesisch/spanischen HTML-Fixture.
"""

from __future__ import annotations

import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import extract as extract_mod  # noqa: E402
from app.compat import tavily_extract  # noqa: E402


@pytest.fixture()
def reads(monkeypatch):
    seen = []

    def fake_read(urls, fmt="text", timeout=None):
        seen.append({"urls": list(urls), "fmt": fmt, "timeout": timeout})
        docs, failed = [], []
        for u in urls:
            if "kaputt" in u:
                failed.append({"url": u, "error": "Seite nicht ladbar"})
            else:
                body = f"# Titel\n\nInhalt von {u}" if fmt == "markdown" else f"Inhalt von {u}"
                docs.append({"url": u, "title": "Titel", "published": "2026-10-01",
                             "author": "A", "description": "D", "content": body,
                             "links": [{"url": "https://x.example", "text": "x"}]})
        return docs, failed

    monkeypatch.setattr(tavily_extract.reader_mod, "read_pages", fake_read)
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)
    return seen


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(tavily_extract.router)
    return TestClient(app)


def test_liste_und_antwortform(client, reads):
    r = client.post("/extract", json={"urls": ["https://a.example/1", "https://b.example/2"]})
    assert r.status_code == 200
    d = r.json()
    assert set(d) == {"results", "failed_results", "response_time", "request_id"}
    assert [x["url"] for x in d["results"]] == ["https://a.example/1", "https://b.example/2"]
    first = d["results"][0]
    assert set(first) == {"url", "raw_content", "images"}
    assert first["images"] == [] and first["raw_content"].startswith("# Titel")
    assert reads[0]["fmt"] == "markdown"  # Default wie Tavily
    assert d["failed_results"] == []


def test_einzelstring(client, reads):
    d = client.post("/extract", json={"urls": "https://a.example/1"}).json()
    assert len(d["results"]) == 1
    assert reads[0]["urls"] == ["https://a.example/1"]


def test_failed_results(client, reads):
    d = client.post("/extract", json={
        "urls": ["https://a.example/ok", "https://kaputt.example/", "ftp://nicht.http/", "https://a.example/ok"],
    }).json()
    assert [x["url"] for x in d["results"]] == ["https://a.example/ok"]  # Dublette nur einmal
    errors = {f["url"]: f["error"] for f in d["failed_results"]}
    assert set(errors) == {"https://kaputt.example/", "ftp://nicht.http/"}
    assert "ftp://nicht.http/" not in reads[0]["urls"]


def test_format_text_und_depth(client, reads, monkeypatch):
    monkeypatch.setenv("FETCH_TIMEOUT", "5")
    d = client.post("/extract", json={"urls": "https://a.example/", "format": "text"}).json()
    assert reads[0]["fmt"] == "text" and reads[0]["timeout"] == 5.0
    assert not d["results"][0]["raw_content"].startswith("#")
    client.post("/extract", json={"urls": "https://a.example/", "extract_depth": "advanced"})
    assert reads[1]["timeout"] == 15.0
    r = client.post("/extract", json={"urls": "https://a.example/", "format": "html"})
    assert r.status_code == 422


def test_favicon_metadata_usage(client, reads):
    d = client.post("/extract", json={"urls": "https://a.example/x/y", "include_favicon": True,
                                      "include_metadata": True, "include_usage": True,
                                      "include_images": True}).json()
    item = d["results"][0]
    assert item["favicon"] == "https://a.example/favicon.ico"
    assert item["title"] == "Titel" and item["published"] == "2026-10-01"
    assert item["links"][0]["url"] == "https://x.example"
    assert item["images"] == []
    assert d["usage"] == {"credits": 0}


def test_grenzen(client, reads):
    assert client.post("/extract", json={"urls": []}).status_code == 400
    many = [f"https://a.example/{i}" for i in range(21)]
    r = client.post("/extract", json={"urls": many})
    assert r.status_code == 400 and reads == []


def test_auth(client, reads, monkeypatch):
    monkeypatch.setenv("SERVICE_API_KEY", "geheim-test")
    body = {"urls": "https://a.example/"}
    assert client.post("/extract", json=body).status_code == 401
    assert client.post("/extract", json=dict(body, api_key="falsch")).status_code == 401
    assert client.post("/extract", json=dict(body, api_key="geheim-test")).status_code == 200
    r = client.post("/extract", json=body, headers={"Authorization": "Bearer geheim-test"})
    assert r.status_code == 200


_FR_HTML = """<!doctype html><html lang="fr"><head><meta charset="utf-8">
<title>Élections à Montréal : ce qu'il faut savoir</title></head><body>
<nav>Accueil | Société</nav><article><h1>Élections à Montréal</h1>
<p>Les électeurs québécois se rendront aux urnes à l'été. « Ça coûte 5 € », a déclaré
la maire, évoquant la hausse des coûts. Où en est-on ? Voilà déjà une année que
l'économie ralentit, et les députés s'inquiètent de l'accès au logement.</p>
<p>En España, la señora Muñoz dijo: «¿Qué pasará con el año electoral?» El niño
respondió con acción y corazón. Les deux témoignages illustrent une inquiétude
partagée de part et d'autre des Pyrénées, à propos des élections et du pouvoir d'achat.</p>
</article></body></html>"""


def test_utf8_franzoesisch_spanisch_echt_extrahiert(client, monkeypatch):
    """Echte Extraktion (ohne Netz): Akzente, Guillemets, ñ und ¿ bleiben heil."""
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)
    monkeypatch.setattr(extract_mod, "fetch_url", lambda url, timeout=None, max_bytes=None: _FR_HTML)
    for fmt in ("markdown", "text"):
        url = f"https://fr-fixture.example/{fmt}"
        r = client.post("/extract", json={"urls": url, "format": fmt, "include_metadata": True})
        assert r.status_code == 200
        assert "utf-8" in r.headers["content-type"].lower() or "json" in r.headers["content-type"]
        item = r.json()["results"][0]
        text = item["raw_content"]
        for needle in ("québécois", "« Ça coûte 5 € »", "Muñoz", "¿Qué pasará", "acción"):
            assert needle in text, (fmt, needle)
        assert item["title"].startswith("Élections à Montréal")
