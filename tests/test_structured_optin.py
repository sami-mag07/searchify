"""Das rohe `structured` ist opt-in: Standardausgabe waechst nicht, der Cache bleibt heil."""
import json

import pytest

from searchify import cli
from searchify.extract import pages, reader

STRUCT = {"json_ld": [{"@type": "Product", "name": "X"}], "meta": {"og:title": "X"}}
PRODUCT = {"name": "X", "price": 9}


def _cached():
    return {"url": "https://a.example/", "title": "T", "content": " ".join(["word"] * 60),
            "structured": STRUCT, "product": PRODUCT, "stage": "static"}


@pytest.fixture
def cached(monkeypatch):
    doc = _cached()
    monkeypatch.setattr(pages, "read_pages", lambda *a, **k: ([doc], []))
    monkeypatch.setattr(reader.render_mod, "enabled", lambda: False)
    monkeypatch.setattr(reader.stealth_mod, "enabled", lambda: False)
    return doc


def test_read_pages_drops_structured_by_default_but_not_from_the_cache(cached):
    docs, _ = reader.read_pages(["https://a.example/"], "text")
    assert "structured" not in docs[0] and docs[0]["product"] == PRODUCT
    assert cached["structured"] == STRUCT  # das geteilte Objekt bleibt unveraendert


def test_read_pages_keeps_structured_on_request(cached):
    docs, _ = reader.read_pages(["https://a.example/"], "text", structured=True)
    assert docs[0]["structured"] == STRUCT


def test_extract_report_default_and_opt_in(cached):
    from searchify.extract import extract_report

    assert "structured" not in extract_report(["https://a.example/"])["results"][0]
    assert extract_report(["https://a.example/"], structured=True)["results"][0]["structured"] == STRUCT


def test_cli_extract_structured_flag(cached):
    from typer.testing import CliRunner

    out = json.loads(CliRunner().invoke(cli.app, ["extract", "https://a.example/"]).stdout)
    assert "structured" not in out["results"][0] and out["results"][0]["product"] == PRODUCT
    out = json.loads(CliRunner().invoke(cli.app, ["extract", "--structured", "https://a.example/"]).stdout)
    assert out["results"][0]["structured"] == STRUCT


def test_service_extract_structured_field(cached):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from searchify import service

    client = TestClient(service.app)
    body = client.post("/extract", json={"urls": ["https://a.example/"]}).json()
    assert "structured" not in body["results"][0]
    body = client.post("/extract", json={"urls": ["https://a.example/"], "structured": True}).json()
    assert body["results"][0]["structured"] == STRUCT
