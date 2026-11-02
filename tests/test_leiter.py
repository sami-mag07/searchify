"""Ladder cap and budget: precedence, caps and what each rung is asked (no network)."""
import dataclasses

import pytest
from leiter_pipeline import ARTICLE, install, snapshot

from searchify import limits
from searchify.extract import htmltext, reader


@pytest.fixture(autouse=True)
def _no_operator_cap(monkeypatch):
    monkeypatch.delenv("SEARCHIFY_MAX_STAGE", raising=False)


def _rungs(calls):
    return {c[0] for c in calls}


def _stages(snap):
    return {d["url"][8:14]: d["stage"] for d in snap["docs"]}


# ------------------------------------------------------------ precedence
def test_default_is_no_limit_and_normal_budget():
    got = limits.resolve()
    assert (got.stage, got.budget, got.cloud) == ("stealth", "normal", True)
    assert limits.detail()["max_stage"] == ("stealth", "default")


def test_request_beats_profile_beats_default():
    prof = {"max_stage": "tls", "budget": "low"}
    assert limits.detail(profile=prof)["max_stage"] == ("tls", "profile")
    assert limits.detail(profile=prof)["budget"] == ("low", "profile")
    assert limits.detail("browser", "high", profile=prof)["max_stage"] == ("browser", "request")
    assert limits.detail("browser", "high", profile=prof)["budget"] == ("high", "request")
    # a request can lift what the profile set, up to the operator cap
    assert limits.resolve("stealth", profile={"max_stage": "plain"}).stage == "stealth"


def test_operator_cap_beats_request_and_profile(monkeypatch):
    monkeypatch.setenv("SEARCHIFY_MAX_STAGE", "tls")
    assert limits.resolve("stealth").stage == "tls"
    assert limits.resolve(profile={"max_stage": "stealth"}).stage == "tls"
    assert limits.detail("stealth")["max_stage"] == ("tls", "env")
    assert limits.resolve("plain").stage == "plain"  # lower wishes still win
    assert limits.resolve().stage == "tls"  # the cap applies even to a bare call


def test_a_typo_in_the_operator_cap_fails_closed(monkeypatch):
    monkeypatch.setenv("SEARCHIFY_MAX_STAGE", "stelth")
    assert limits.resolve().stage == "plain"


@pytest.mark.parametrize("kwargs", [{"max_stage": "turbo"}, {"budget": "huge"}])
def test_unknown_values_are_refused(kwargs):
    with pytest.raises(ValueError):
        limits.resolve(**kwargs)


def test_unknown_profile_values_are_refused_not_guessed():
    with pytest.raises(ValueError):
        limits.resolve(profile={"max_stage": "turbo"})


# ------------------------------------------------------------ what each cap lets through
def _run(monkeypatch, **kw):
    calls = []
    install(monkeypatch, browser=True, stealth=True, tavily=True, calls=calls)
    return snapshot(**kw), calls


def test_no_cap_uses_every_rung(monkeypatch):
    snap, calls = _run(monkeypatch)
    assert _rungs(calls) == {"fetch", "render", "direct", "stealth", "tavily"}
    assert set(_stages(snap).values()) == {"static", "browser", "stealth", "tavily"}


def test_plain_uses_httpx_only_no_browser_no_stealth(monkeypatch):
    snap, calls = _run(monkeypatch, max_stage="plain", cloud_fallback=False)
    assert _rungs(calls) == {"fetch"}
    assert all(c[3] is True for c in calls)  # fetch_url asked for plain
    assert snap["thin"] == {"https://stuck.example/": "challenge", "https://wall.example/": "challenge"}
    assert {f["url"] for f in snap["failed"]} == {"https://blocked.example/", "https://cloud.example/"}


def test_tls_keeps_the_chrome_fallback_but_no_browser(monkeypatch):
    _snap, calls = _run(monkeypatch, max_stage="tls", cloud_fallback=False)
    assert _rungs(calls) == {"fetch"}
    assert all(c[3] is False for c in calls)  # the normal fetch, which has the fallback


def test_browser_includes_shared_and_direct_but_not_stealth(monkeypatch):
    snap, calls = _run(monkeypatch, max_stage="browser", cloud_fallback=False)
    assert {"render", "direct"} <= _rungs(calls) and "stealth" not in _rungs(calls)
    assert _stages(snap)["wall.e"] == "browser"


def test_stealth_adds_camoufox(monkeypatch):
    snap, calls = _run(monkeypatch, max_stage="stealth", cloud_fallback=False)
    assert "stealth" in _rungs(calls)
    assert _stages(snap)["blocke"] == "stealth"


def test_a_rung_that_is_not_installed_is_never_used_even_at_the_top(monkeypatch):
    calls = []
    install(monkeypatch, browser=False, stealth=False, tavily=False, calls=calls)
    snapshot(max_stage="stealth")
    assert _rungs(calls) == {"fetch"}


def test_stealth_env_cannot_override_a_plain_wish(monkeypatch):
    """READER_STEALTH=1 with Camoufox installed, and the user asked for plain."""
    calls = []
    install(monkeypatch, browser=True, stealth=True, calls=calls)
    monkeypatch.setenv("READER_STEALTH", "1")
    resolved = limits.resolve(profile={"max_stage": "plain"})
    snap = snapshot(max_stage=resolved.stage)
    assert _rungs(calls) == {"fetch"}
    assert "stealth" not in _stages(snap).values()


def test_operator_cap_is_enforced_inside_the_ladder(monkeypatch):
    monkeypatch.setenv("SEARCHIFY_MAX_STAGE", "browser")
    calls = []
    install(monkeypatch, browser=True, stealth=True, tavily=False, calls=calls)
    snapshot(max_stage="stealth")  # the request asks for more than the operator allows
    assert "stealth" not in _rungs(calls) and "render" in _rungs(calls)


def test_cloud_fallback_is_its_own_switch(monkeypatch):
    _snap, calls = _run(monkeypatch, max_stage="plain")  # plain, but cloud left as today
    assert _rungs(calls) == {"fetch", "tavily"}
    _snap, calls = _run(monkeypatch, cloud_fallback=False)
    assert "tavily" not in _rungs(calls) and "stealth" in _rungs(calls)
    _snap, calls = _run(monkeypatch, cloud_fallback=True)
    assert "tavily" in _rungs(calls)


# ------------------------------------------------------------ budget
def _fetch_timeouts(calls):
    return {round(c[2], 3) for c in calls if c[0] == "fetch"}


def test_normal_budget_is_exactly_today(monkeypatch):
    _snap, calls = _run(monkeypatch)
    _snap2, calls2 = _run(monkeypatch, budget="normal")
    assert calls == calls2
    assert _fetch_timeouts(calls) == {5.0}


def test_low_lowers_and_high_raises_timeouts(monkeypatch):
    _s, base = _run(monkeypatch)
    _s, low = _run(monkeypatch, budget="low")
    _s, high = _run(monkeypatch, budget="high")
    assert _fetch_timeouts(low) == {3.0} and _fetch_timeouts(high) == {10.0}
    for rung in ("render", "stealth", "tavily"):
        b = next(c[2] for c in base if c[0] == rung)
        assert next(c[2] for c in low if c[0] == rung) < b < next(c[2] for c in high if c[0] == rung)


def test_an_explicit_timeout_is_scaled_too(monkeypatch):
    calls = []
    install(monkeypatch, calls=calls)
    reader.read_pages_ex(["https://article.example/"], "text", 10, budget="low")
    assert _fetch_timeouts(calls) == {6.0}


def test_low_trims_after_the_cache_and_the_cache_keeps_the_full_text(monkeypatch):
    install(monkeypatch)
    big = "<html><body><article><p>" + " ".join(f"lang{i}" for i in range(3000)) + "</p></article></body></html>"
    monkeypatch.setattr(htmltext, "fetch_url", lambda u, timeout, max_bytes=None, plain=False: big)
    cut, _f, _t = reader.read_pages_ex(["https://big.example/"], "text", budget="low")
    assert cut[0]["truncated"] is True and len(cut[0]["content"]) <= limits.LOW_CHARS + 2
    assert cut[0]["content"].endswith("…")
    # the cache holds the full text, so a later normal request is not shortened
    again, _f, _t = reader.read_pages_ex(["https://big.example/"], "text")
    assert "truncated" not in again[0] and len(again[0]["content"]) > limits.LOW_CHARS * 2
    # and a high request after a low one gets everything too
    high, _f, _t = reader.read_pages_ex(["https://big.example/"], "text", budget="high")
    assert high[0]["content"] == again[0]["content"]


def test_short_pages_are_not_touched_by_low(monkeypatch):
    install(monkeypatch)
    short, _f, _t = reader.read_pages_ex(["https://article.example/"], "text", budget="low")
    normal, _f, _t = reader.read_pages_ex(["https://article.example/"], "text")
    assert short == normal and "truncated" not in short[0]


def test_limits_dataclass_is_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        limits.Limits().stage = "plain"  # type: ignore[misc]


def test_plain_flag_reaches_the_real_fetch_twin(monkeypatch):
    """fetch_url(plain=True) forces httpx and drops the Chrome-TLS fallback."""
    seen = {}

    def live(url, timeout, limit, settings):
        seen["settings"] = settings
        return ARTICLE
    monkeypatch.setattr(htmltext, "_fetch_live", live)
    monkeypatch.setenv("FETCH_CLIENT", "curl_cffi")
    monkeypatch.setenv("FETCH_FALLBACK", "1")
    htmltext.fetch_url("https://a.example/")
    assert seen["settings"].fetch_client == "curl_cffi" and seen["settings"].fetch_fallback
    htmltext.fetch_url("https://a.example/", plain=True)
    assert seen["settings"].fetch_client == "httpx" and seen["settings"].fetch_fallback is False
