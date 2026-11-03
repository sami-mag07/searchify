"""Region table, fallback and ddgs error codes (no network)."""
import sys
import types

import pytest

from searchify.search import SearchBackendError, error_code, search_report
from searchify.search import duckduckgo
from searchify.search.regions import DDG_REGIONS, LANGUAGE_REGION, ddg_region, is_region


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch):
    """The DuckDuckGo retry waits about a second; the tests record the wait instead."""
    waits = []
    monkeypatch.setattr(duckduckgo, "_sleep", waits.append, raising=False)
    return waits


def test_table_is_complete_and_well_formed():
    assert len(DDG_REGIONS) == 64
    assert "wt-wt" in DDG_REGIONS and "be-nl" in DDG_REGIONS and "ct-ca" in DDG_REGIONS
    assert all(is_region(c) for c in DDG_REGIONS)
    assert all(v in DDG_REGIONS for v in LANGUAGE_REGION.values())


def test_default_and_any_are_world():
    assert ddg_region() == "wt-wt"
    assert ddg_region(None, "BE") == "wt-wt"
    assert ddg_region("any") == "wt-wt"
    assert ddg_region("any", "NL") == "wt-wt"


def test_language_and_country_fallback():
    assert ddg_region("nl") == "nl-nl"
    assert ddg_region("NL") == "nl-nl"
    assert ddg_region("nl", "BE") == "be-nl"
    assert ddg_region("fr", "ch") == "ch-fr"
    assert ddg_region("nl", "DE") == "nl-nl"      # de-nl does not exist, language wins
    assert ddg_region("xx", "NL") == "wt-wt"      # unknown language
    assert ddg_region("en") == "us-en" and ddg_region("de") == "de-de"


@pytest.mark.parametrize("lang,country,region", [
    ("en", "GB", "uk-en"), ("en-GB", None, "uk-en"), ("en_gb", None, "uk-en"), ("en", "UK", "uk-en"),
    ("ar", "SA", "xa-ar"), ("ar-SA", None, "xa-ar"),
    ("zh", "TW", "tw-tzh"), ("zh-TW", None, "tw-tzh"), ("zh", "HK", "hk-tzh"), ("zh-HK", None, "hk-tzh"),
    ("zh", "CN", "cn-zh"), ("zh", None, "cn-zh"), ("zh-CN", None, "cn-zh"),
])
def test_country_aliases_for_duckduckgo(lang, country, region):
    assert ddg_region(lang, country) == region


def test_ready_region_code_and_garbage():
    assert ddg_region("be-nl") == "be-nl"
    assert ddg_region("nl_BE") == "be-nl"
    assert ddg_region("nl; drop") == "wt-wt"
    assert not is_region("nl") and not is_region("NL-nl") and not is_region("zz-zz")


class DDGSException(Exception):
    pass


class RatelimitException(DDGSException):
    pass


class TimeoutException(DDGSException):
    pass


def _fake_ddgs(monkeypatch, exc=None, rows=None, seen=None):
    class DDGS:
        def text(self, q, **kw):
            if seen is not None:
                seen.update(kw)
            if exc:
                raise exc
            return rows or []
    monkeypatch.setitem(sys.modules, "ddgs", types.SimpleNamespace(DDGS=DDGS))


def test_no_results_is_empty_list_not_error(monkeypatch):
    _fake_ddgs(monkeypatch, DDGSException("No results found."))
    assert duckduckgo.ddgs_search("q") == []
    assert search_report("q", backend="duckduckgo")["error"] is None


@pytest.mark.parametrize("exc,code", [
    (RatelimitException("x"), "rate_limited"),
    (DDGSException("HTTP 429 too many"), "rate_limited"),
    (TimeoutException("x"), "timeout"),
    (DDGSException("operation timed out"), "timeout"),
    (DDGSException("boom"), "error"),
])
def test_error_codes(monkeypatch, exc, code):
    _fake_ddgs(monkeypatch, exc)
    with pytest.raises(SearchBackendError) as e:
        duckduckgo.ddgs_search("q")
    assert str(e.value).startswith(code + ":")
    rep = search_report("q", backend="duckduckgo")
    assert rep["results"] == [] and error_code(rep["error"]) == code


def test_region_reaches_ddgs(monkeypatch):
    seen = {}
    _fake_ddgs(monkeypatch, rows=[{"title": "t", "href": "https://a.nl", "body": "b"}], seen=seen)
    assert duckduckgo.ddgs_search("q", language="nl", country="BE")
    assert seen["region"] == "be-nl"
    duckduckgo.ddgs_search("q")
    assert seen["region"] == "wt-wt"


def test_any_language_is_dropped_centrally(monkeypatch):
    got = {}
    monkeypatch.setitem(sys.modules["searchify.search"].BACKENDS, "duckduckgo", lambda q, n, **f: got.update(f) or [])
    search_report("q", backend="duckduckgo", language="any", country="NL")
    assert "language" not in got and got["country"] == "NL"


ROW = {"title": "t", "href": "https://a.example/", "body": "b"}


def _flaky_ddgs(monkeypatch, answers, calls):
    """A DDGS whose text() gives the next answer each call: rows, or an exception to raise."""
    class DDGS:
        def text(self, q, **kw):
            calls.append(q)
            answer = answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer
    monkeypatch.setitem(sys.modules, "ddgs", types.SimpleNamespace(DDGS=DDGS))


def test_a_general_ddgs_error_is_retried_once(monkeypatch, _no_waiting):
    calls = []
    _flaky_ddgs(monkeypatch, [DDGSException("boom"), [ROW]], calls)
    assert [h["url"] for h in duckduckgo.ddgs_search("q")] == ["https://a.example/"]
    assert len(calls) == 2 and _no_waiting == [duckduckgo.RETRY_AFTER_ERROR_S]
    assert 0.5 <= duckduckgo.RETRY_AFTER_ERROR_S <= 2


def test_a_general_ddgs_error_twice_is_an_error(monkeypatch):
    calls = []
    _flaky_ddgs(monkeypatch, [DDGSException("boom"), DDGSException("boom")], calls)
    with pytest.raises(SearchBackendError, match="^error:"):
        duckduckgo.ddgs_search("q")
    assert len(calls) == 2


@pytest.mark.parametrize("exc", [RatelimitException("x"), TimeoutException("x"),
                                 DDGSException("HTTP 429 too many")])
def test_rate_limits_and_timeouts_are_not_retried(monkeypatch, _no_waiting, exc):
    calls = []
    _flaky_ddgs(monkeypatch, [exc, [ROW]], calls)
    with pytest.raises(SearchBackendError):
        duckduckgo.ddgs_search("q")
    assert len(calls) == 1 and _no_waiting == []


def test_an_empty_answer_is_tried_once_more(monkeypatch, _no_waiting):
    calls = []
    _flaky_ddgs(monkeypatch, [[], [ROW]], calls)
    assert [h["url"] for h in duckduckgo.ddgs_search("q", language="es")] == ["https://a.example/"]
    assert len(calls) == 2 and _no_waiting == [duckduckgo.RETRY_AFTER_EMPTY_S]
    assert 1 <= duckduckgo.RETRY_AFTER_EMPTY_S <= 2


def test_two_empty_answers_mean_no_hits(monkeypatch, _no_waiting):
    calls = []
    _flaky_ddgs(monkeypatch, [[], []], calls)
    assert duckduckgo.ddgs_search("q") == []
    assert len(calls) == 2


def test_no_results_found_counts_as_empty_and_is_tried_once_more(monkeypatch):
    calls = []
    _flaky_ddgs(monkeypatch, [DDGSException("No results found."), [ROW]], calls)
    assert len(duckduckgo.ddgs_search("q")) == 1 and len(calls) == 2


@pytest.mark.parametrize("answers, raises", [
    ([DDGSException("boom"), []], False),       # error, then empty: no third try
    ([[], DDGSException("boom")], True),        # empty, then error: no third try either
])
def test_at_most_one_retry_per_search(monkeypatch, _no_waiting, answers, raises):
    calls = []
    _flaky_ddgs(monkeypatch, answers + [[ROW]], calls)
    if raises:
        with pytest.raises(SearchBackendError):
            duckduckgo.ddgs_search("q")
    else:
        assert duckduckgo.ddgs_search("q") == []
    assert len(calls) == 2 and len(_no_waiting) == 1
