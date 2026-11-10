"""Reading a part of a page: window(), offset/max_chars in every interface."""
import pytest
from fastapi.testclient import TestClient
from leiter_pipeline import PAGES, install, snapshot

from searchify.extract import extract, extract_report, reader

TEXT = "Erster Absatz mit Worten.\n\nZweiter Absatz, etwas länger als der erste.\n\nDritter Absatz am Ende."
DOC = {"url": "https://a.example/", "title": "T", "content": TEXT}


def test_a_window_starts_at_the_offset_and_ends_at_a_word_boundary():
    start = TEXT.index("Zweiter")
    part = reader.window(DOC, start, 20)
    assert part["content"] == "Zweiter Absatz,"
    assert part["offset"] == start and part["total_chars"] == len(TEXT)
    assert TEXT[part["next_offset"]:].startswith("etwas") and part["truncated"] is True


def test_windows_one_after_another_read_the_whole_text():
    offset, parts = 0, []
    while True:
        part = reader.window(DOC, offset, 17)
        parts.append(part["content"])
        if "next_offset" not in part:
            break
        offset = part["next_offset"]
    assert " ".join(" ".join(parts).split()) == " ".join(TEXT.split())


def test_an_offset_is_taken_as_it_is_and_past_the_end_gives_nothing():
    assert reader.window(DOC, 2)["content"].startswith("ster Absatz")
    tail = reader.window(DOC, 10_000)
    assert tail["content"] is None and "next_offset" not in tail
    assert reader.window(DOC, 0)["content"] == TEXT and "next_offset" not in reader.window(DOC, 0)


def test_a_word_longer_than_the_window_is_cut_and_the_next_part_goes_on_with_it():
    doc = {"url": "u", "content": "x" * 30 + " Ende"}
    first = reader.window(doc, 0, 10)
    second = reader.window(doc, first["next_offset"], 30)
    assert first["content"] == "x" * 10 and second["content"] == "x" * 20 + " Ende"


def test_tabs_and_line_breaks_are_word_boundaries_too():
    doc = {"url": "u", "content": "eins\tzwei\ndrei vier"}
    assert reader.window(doc, 0, 12)["content"] == "eins\tzwei"


def test_the_cached_page_is_not_changed_by_a_window():
    doc = dict(DOC)
    reader.window(doc, 5, 10)
    assert doc == DOC


LONG_URL = "https://long.example/"
LONG_HTML = ("<html><head><title>Lang</title></head><body><article>"
             + "".join(f"<p>Absatz {i} " + "wort " * 40 + "</p>" for i in range(40))
             + "</article></body></html>")


@pytest.fixture
def long_page(monkeypatch):
    monkeypatch.setitem(PAGES, LONG_URL, LONG_HTML)
    install(monkeypatch, browser=False)


def test_an_offset_reaches_past_the_low_budget_trim(long_page):
    full = snapshot([LONG_URL])["docs"][0]["content"]
    assert len(full) > 6000
    start = full.index("Absatz 30")
    (doc,) = snapshot([LONG_URL], budget="low", offset=start, max_chars=500)["docs"]
    assert doc["content"].startswith("Absatz 30") and len(doc["content"]) <= 500
    assert doc["offset"] == start and doc["total_chars"] == len(full)


def test_extract_and_extract_report_take_a_window(long_page):
    (doc,) = extract([LONG_URL], offset=0, max_chars=300)
    assert len(doc["content"]) <= 300 and doc["next_offset"] > 0
    report = extract_report([LONG_URL], offset=doc["next_offset"], max_chars=300,
                                    use_profile=False)
    assert report["results"][0]["offset"] == doc["next_offset"]


def test_the_http_service_takes_offset_and_max_chars(long_page):
    from searchify import service

    client = TestClient(service.app)
    body = client.post("/extract", json={"urls": [LONG_URL], "offset": 100, "max_chars": 200}).json()
    (doc,) = body["results"]
    assert doc["offset"] >= 100 and len(doc["content"]) <= 200 and "next_offset" in doc
    assert client.post("/extract", json={"urls": [LONG_URL], "offset": -1}).status_code == 422


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_the_mcp_tool_pages_through_with_next_offset(long_page):
    pytest.importorskip("mcp")
    from mcp import Client

    from searchify import mcp_server

    async with Client(mcp_server.server, raise_exceptions=True) as c:
        first = (await c.call_tool("extract", {"urls": [LONG_URL], "max_chars": 500})).structured_content
        page = first["results"][0]
        assert len(page["content"]) <= 500 and page["next_offset"] > 0 and page["total_chars"] > 6000
        second = (await c.call_tool("extract", {"urls": [LONG_URL], "max_chars": 500,
                                                "offset": page["next_offset"]})).structured_content
        assert second["results"][0]["offset"] == page["next_offset"]
        assert not second["results"][0]["content"].startswith(page["content"][:30])


@pytest.mark.anyio
async def test_the_mcp_tool_with_a_low_budget_still_says_where_to_read_on(long_page):
    pytest.importorskip("mcp")
    from mcp import Client

    from searchify import mcp_server

    async with Client(mcp_server.server, raise_exceptions=True) as c:
        out = (await c.call_tool("extract", {"urls": [LONG_URL], "budget": "low"})).structured_content
        page = out["results"][0]
        assert len(page["content"]) <= 4000 and page["next_offset"] > 0
# Stand: 10.11.2026
