"""The limits through extract_report, the HTTP /extract request and the MCP extract tool."""
import sys

import pytest
from leiter_pipeline import install

from searchify import extract_report, profile


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.delenv("SEARCHIFY_MAX_STAGE", raising=False)
    monkeypatch.delenv("READER_STEALTH", raising=False)


def _rungs(calls):
    return {c[0] for c in calls}


# ------------------------------------------------------------ Python API
def test_extract_report_reads_the_profile(monkeypatch):
    calls = []
    install(monkeypatch, browser=True, stealth=True, calls=calls)
    profile.save({"max_stage": "plain"})
    rep = extract_report(["https://wall.example/", "https://blocked.example/"])
    assert _rungs(calls) == {"fetch"}
    assert rep["status"]["thin"] == 1 and rep["status"]["failed"] == 1


def test_reader_stealth_env_does_not_override_an_interview_answer_of_plain(monkeypatch):
    calls = []
    install(monkeypatch, browser=True, stealth=True, calls=calls)
    monkeypatch.setenv("READER_STEALTH", "1")  # the operator switched Camoufox on for the whole service
    profile.save({"max_stage": "plain"})
    extract_report(["https://blocked.example/"])
    assert "stealth" not in _rungs(calls) and "render" not in _rungs(calls)


def test_a_request_beats_the_profile(monkeypatch):
    calls = []
    install(monkeypatch, browser=True, stealth=True, calls=calls)
    profile.save({"max_stage": "plain"})
    extract_report(["https://blocked.example/"], max_stage="stealth")
    assert "stealth" in _rungs(calls)


def test_the_operator_cap_beats_request_and_profile(monkeypatch):
    calls = []
    install(monkeypatch, browser=True, stealth=True, calls=calls)
    monkeypatch.setenv("SEARCHIFY_MAX_STAGE", "plain")
    profile.save({"max_stage": "stealth"})
    extract_report(["https://blocked.example/"], max_stage="stealth")
    assert _rungs(calls) == {"fetch"}


def test_use_profile_false_ignores_the_file(monkeypatch):
    calls = []
    install(monkeypatch, browser=True, stealth=True, calls=calls)
    profile.save({"max_stage": "plain"})
    extract_report(["https://blocked.example/"], use_profile=False)
    assert "stealth" in _rungs(calls)


def test_profile_budget_and_a_request_budget(monkeypatch):
    calls = []
    install(monkeypatch, calls=calls)
    profile.save({"budget": "low"})
    extract_report(["https://article.example/"])
    assert {c[2] for c in calls if c[0] == "fetch"} == {3.0}
    calls.clear()
    extract_report(["https://stuck.example/"], budget="high")  # another page: not in the cache
    assert {c[2] for c in calls if c[0] == "fetch"} == {10.0}


def test_nothing_set_passes_no_limits_down(monkeypatch):
    seen = {}

    def fake(urls, fmt, timeout=None, **kw):
        assert kw.pop("structured", False) is False  # raw structured stays opt-in
        seen.update(kw)
        return [], [], {}
    monkeypatch.setattr(sys.modules["searchify.extract"], "read_pages_ex", fake)
    extract_report(["https://a.example/"])
    assert seen == {}


def test_bad_values_raise_in_the_python_api():
    with pytest.raises(ValueError):
        extract_report(["https://a.example/"], max_stage="turbo")


# ------------------------------------------------------------ HTTP service
fastapi = pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from searchify import service  # noqa: E402

client = TestClient(service.app)


def test_extract_request_carries_the_limits(monkeypatch):
    calls = []
    install(monkeypatch, browser=True, stealth=True, calls=calls)
    body = client.post("/extract", json={"urls": ["https://blocked.example/"],
                                         "max_stage": "plain", "cloud_fallback": False}).json()
    assert _rungs(calls) == {"fetch"}
    assert body["failed"][0]["url"] == "https://blocked.example/"


def test_extract_without_limits_calls_the_core_exactly_as_before(monkeypatch):
    seen = {}

    def fake(urls, fmt, timeout=None, **kw):
        assert kw.pop("structured", False) is False  # raw structured stays opt-in
        seen.update(kw, urls=urls)
        return [], [], {}
    monkeypatch.setattr(service, "read_pages_ex", fake)
    assert client.post("/extract", json={"urls": ["https://a.example/"]}).status_code == 200
    assert seen == {"urls": ["https://a.example/"]}


def test_extract_request_validates_the_values():
    assert client.post("/extract", json={"urls": ["https://a.example/"], "max_stage": "turbo"}).status_code == 422
    assert client.post("/extract", json={"urls": ["https://a.example/"], "budget": "huge"}).status_code == 422


def test_service_never_reads_the_user_profile(monkeypatch):
    install(monkeypatch)
    profile.save({"max_stage": "plain", "budget": "low"})

    def forbidden(*a, **k):
        raise AssertionError("the HTTP service read the user profile")
    monkeypatch.setattr(profile, "load", forbidden)
    monkeypatch.setattr(profile, "profile_path", forbidden)
    resp = client.post("/extract", json={"urls": ["https://article.example/"]})
    assert resp.status_code == 200
    assert "truncated" not in resp.json()["results"][0]  # a low budget profile had no effect


def test_service_uses_the_operator_cap(monkeypatch):
    calls = []
    install(monkeypatch, browser=True, stealth=True, calls=calls)
    monkeypatch.setenv("SEARCHIFY_MAX_STAGE", "plain")
    client.post("/extract", json={"urls": ["https://blocked.example/"], "max_stage": "stealth"})
    assert _rungs(calls) == {"fetch"}


# ------------------------------------------------------------ MCP
pytest.importorskip("mcp")
from mcp import Client  # noqa: E402

from searchify import mcp_server  # noqa: E402


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_mcp_extract_offers_only_max_stage_and_budget():
    async with Client(mcp_server.server, raise_exceptions=True) as c:
        tool = next(t for t in (await c.list_tools()).tools if t.name == "extract")
    props = set(tool.input_schema["properties"])
    assert {"max_stage", "budget"} <= props
    assert not props & {"cloud_fallback", "profile", "region"}


@pytest.mark.anyio
async def test_mcp_extract_passes_limits_and_never_reads_the_profile(monkeypatch):
    seen = {}

    def fake(urls, fmt, **kw):
        assert kw.pop("structured", False) is False  # raw structured stays opt-in
        seen.update(kw)
        return [], []
    monkeypatch.setattr(mcp_server, "read_pages", fake)

    def forbidden(*a, **k):
        raise AssertionError("the MCP server read the user profile")
    monkeypatch.setattr(profile, "load", forbidden)
    async with Client(mcp_server.server, raise_exceptions=True) as c:
        await c.call_tool("extract", {"urls": ["https://a.example/"],
                                      "max_stage": "tls", "budget": "low"})
        assert seen == {"max_stage": "tls", "budget": "low"}
        seen.clear()
        await c.call_tool("extract", {"urls": ["https://a.example/"]})
        assert seen == {}  # nothing set: the call is the old one


@pytest.mark.anyio
async def test_mcp_extract_rejects_unknown_stages():
    async with Client(mcp_server.server, raise_exceptions=False) as c:
        result = await c.call_tool("extract", {"urls": ["https://a.example/"], "max_stage": "turbo"})
    assert result.is_error
