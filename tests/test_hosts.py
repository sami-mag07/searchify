"""Host memory: what it learns, when it skips a rung, and that it never costs a page."""
import os
import sqlite3
import stat

import pytest
from leiter_pipeline import install, snapshot

from searchify.extract import hosts, reader

DAY = 86400.0
ALL = list(hosts.RUNGS)


@pytest.fixture
def memory(tmp_path, monkeypatch):
    path = tmp_path / "mem" / "hosts.sqlite3"
    monkeypatch.setenv("SEARCHIFY_HOST_MEMORY", "1")
    monkeypatch.setenv("SEARCHIFY_HOST_MEMORY_PATH", str(path))
    monkeypatch.delenv("SEARCHIFY_MAX_STAGE", raising=False)
    return hosts.Memory.open(path)


def _fail(mem, url, rung="static", reason="blocked", times=hosts.STREAK, now=None):
    mem.record([(url, rung, reason)] * times, now=now)


def _plan(mem, url, rungs=ALL, now=None):
    return mem.plan([url], rungs, now=now).get(url, set())


# ------------------------------------------------------------ learning and deciding
def test_a_rung_is_skipped_after_it_failed_streak_times_in_a_row(memory):
    url = "https://www.shop.example/p/1"
    _plan(memory, url)  # first visit: a full climb
    _fail(memory, url, times=hosts.STREAK - 1)
    assert _plan(memory, url) == set()
    _fail(memory, url, times=1)
    assert _plan(memory, "https://shop.example/other") == {"static"}  # same host, www or not


def test_one_success_resets_the_streak(memory):
    url = "https://shop.example/"
    _plan(memory, url)
    _fail(memory, url)
    memory.record([(url, "static", "ok")])
    assert _plan(memory, url) == set()


@pytest.mark.parametrize("reason", ["timeout", "not_found", "unavailable", "rate_limited",
                                    "address_blocked", "not_html", "pdf_encrypted"])
def test_failures_that_are_not_the_sites_fault_teach_nothing(memory, reason):
    url = "https://slow.example/"
    _plan(memory, url)
    _fail(memory, url, reason=reason, times=10)
    assert _plan(memory, url) == set()


def test_every_tenth_visit_and_a_week_later_the_whole_ladder_is_climbed_again(memory):
    url, t = "https://wall.example/", 1000.0
    _fail(memory, url, now=t)
    skips = [_plan(memory, url, now=t) for _ in range(1, 21)]  # visits 1 .. 20
    assert skips[10 - 1] == set() and skips[20 - 1] == set()
    assert all(s == {"static"} for i, s in enumerate(skips, 1) if i % 10)
    assert _plan(memory, url, now=t + 8 * DAY) == set()  # a week without a full climb


def test_only_allowed_rungs_count_and_never_all_of_them(memory):
    url = "https://hard.example/"
    _plan(memory, url)
    for rung in ALL:
        _fail(memory, url, rung=rung, reason="challenge")
    assert _plan(memory, url) == set()  # everything fails: try everything, as before
    assert _plan(memory, url, rungs=["static"]) == set()  # a cap of plain or tls: nothing to skip
    memory.record([(url, "stealth", "ok")])
    assert _plan(memory, url) == {"static", "browser", "browser-direct"}
    assert _plan(memory, url, rungs=["static", "browser"]) == set()  # stealth not allowed now


def test_old_hosts_are_forgotten_and_the_file_has_a_cap(memory, monkeypatch):
    memory.record([("https://old.example/", "static", "ok")], now=1000.0)
    memory.record([("https://new.example/", "static", "ok")], now=1000.0 + 31 * DAY)
    assert [h["host"] for h in memory.describe()] == ["new.example"]
    monkeypatch.setattr(hosts, "MAX_HOSTS", 2)
    memory.record([(f"https://h{i}.example/", "static", "ok") for i in range(5)], now=1000.0 + 32 * DAY)
    assert len(memory.describe()) == 2


def test_describe_says_where_a_call_starts_and_forget_removes(memory):
    url = "https://coolblue.example/"
    _fail(memory, url, reason="blocked")
    memory.record([(url, "browser", "ok")], now=1000.0)
    (row,) = memory.describe()
    assert row["starts_at"] == "browser"
    assert row["rungs"]["static"] == {"ok": 0, "fail": 3, "streak": 3, "reason": "blocked"}
    assert memory.forget("www.coolblue.example") == 1 and memory.describe() == []


def test_the_file_is_only_readable_by_its_owner(memory):
    mode = stat.S_IMODE(os.stat(memory.path).st_mode)
    assert mode & 0o077 == 0


def test_no_url_and_no_page_text_is_stored(memory):
    memory.record([("https://shop.example/secret/path?token=abc", "static", "blocked")], now=1000.0)
    dump = "\n".join(sqlite3.connect(memory.path).iterdump())
    assert "secret" not in dump and "token" not in dump and "shop.example" in dump


def test_off_switch_and_a_broken_file(tmp_path, monkeypatch):
    monkeypatch.setenv("SEARCHIFY_HOST_MEMORY", "0")
    assert hosts.open_memory() is None
    broken = tmp_path / "broken.sqlite3"
    broken.write_bytes(b"this is not a database" * 100)
    monkeypatch.setenv("SEARCHIFY_HOST_MEMORY", "1")
    monkeypatch.setenv("SEARCHIFY_HOST_MEMORY_PATH", str(broken))
    assert hosts.open_memory() is None  # off for this call, no exception


# ------------------------------------------------------------ in the ladder
def _rungs_for(calls, host):
    return [c[0] for c in calls if host in c[1]]


def test_a_known_host_jumps_straight_to_the_rung_that_works(memory, monkeypatch):
    calls = []
    install(monkeypatch, browser=True, stealth=True, calls=calls)
    url = "https://blocked.example/"  # static 403, browser and direct empty, stealth reads it
    for _ in range(hosts.STREAK):
        install(monkeypatch, browser=True, stealth=True, calls=calls)  # a fresh page cache
        snap = snapshot([url])
        assert snap["docs"][0]["stage"] == "stealth"
    calls.clear()
    install(monkeypatch, browser=True, stealth=True, calls=calls)
    snap = snapshot([url])
    assert _rungs_for(calls, "blocked") == ["stealth"]
    (doc,) = snap["docs"]
    assert doc["stage"] == "stealth" and doc["skipped"] == ["static", "browser", "browser-direct"]


def test_when_the_known_rung_fails_the_skipped_static_fetch_still_runs(memory, monkeypatch):
    url = "https://wall.example/"  # static gives a challenge page
    _fail(memory, url, reason="challenge")
    _fail(memory, url, rung="browser", reason="challenge")
    memory.plan([url], ALL)  # the full climb after learning
    calls = []
    install(monkeypatch, browser=True, stealth=True, calls=calls)
    monkeypatch.setattr(reader.stealth_mod, "try_stealth", lambda u, timeout=0: ("tried", None))
    snap = snapshot([url])
    assert _rungs_for(calls, "wall") == ["direct", "fetch"]  # the skipped static fetch comes last
    assert snap["failed"] == []
    assert snap["thin"] == {url: "challenge"}  # the thin text is delivered as without memory
    assert snap["docs"][0]["stage"] == "static"


def test_a_page_that_fails_everywhere_says_what_was_tried(memory, monkeypatch):
    install(monkeypatch, browser=True, stealth=True)
    monkeypatch.setattr(reader.stealth_mod, "try_stealth", lambda u, timeout=0: ("tried", None))
    snap = snapshot(["https://cloud.example/"])
    (fail,) = snap["failed"]
    assert fail["tried"] == ["static:blocked", "browser:failed", "browser-direct:failed", "stealth:failed"]
    assert fail["reason"] == "blocked"


def test_a_cached_page_needs_no_ladder_even_for_a_known_host(memory, monkeypatch):
    calls = []
    install(monkeypatch, browser=True, stealth=True, calls=calls)
    url = "https://article.example/"
    snapshot([url])
    _fail(memory, url, reason="blocked")
    calls.clear()
    snap = snapshot([url])
    assert snap["docs"][0]["stage"] == "static" and calls == []


def test_the_ladder_learns_from_real_calls(memory, monkeypatch):
    install(monkeypatch, browser=True, stealth=True)
    snapshot(["https://blocked.example/"])
    rows = {r["host"]: r for r in memory.describe()}
    rungs = rows["blocked.example"]["rungs"]
    assert rungs["static"]["reason"] == "blocked" and rungs["stealth"]["ok"] == 1
    assert rungs["browser"]["reason"] == "failed"


def test_without_memory_the_ladder_is_unchanged(monkeypatch):
    monkeypatch.setenv("SEARCHIFY_HOST_MEMORY", "0")
    calls = []
    install(monkeypatch, browser=True, stealth=True, calls=calls)
    snapshot(["https://blocked.example/"])
    assert _rungs_for(calls, "blocked") == ["fetch", "render", "direct", "stealth"]



def test_a_missing_page_teaches_nothing_about_the_browser_on_that_site(memory, monkeypatch):
    from leiter_pipeline import PAGES

    monkeypatch.setitem(PAGES, "https://gone.example/x", None)
    install(monkeypatch, browser=True, stealth=True)
    monkeypatch.setattr(reader.extract_mod, "fetch_url", _status(404))
    monkeypatch.setattr(reader.stealth_mod, "try_stealth", lambda u, timeout=0: ("tried", None))
    snap = snapshot(["https://gone.example/x"])
    assert snap["failed"][0]["reason"] == "not_found"
    assert memory.describe() == []


def _status(code):
    from searchify.extract import htmltext

    def fetch(url, timeout, max_bytes=None, plain=False):
        htmltext._note_failure(f"HTTP {code}")
        return None
    return fetch


# ------------------------------------------------------------ found in review
def test_a_page_from_the_cache_does_not_count_again_against_the_static_rung(memory, monkeypatch):
    install(monkeypatch, browser=True, stealth=False)  # stuck.example stays a challenge page
    url = "https://stuck.example/"
    for _ in range(hosts.STREAK):
        snapshot([url])  # one real fetch, then the cached thin page
    (row,) = memory.describe()
    assert row["rungs"]["static"]["fail"] == 1
    memory.plan([url], ALL)
    assert "static" not in _plan(memory, url)


def test_a_busy_direct_browser_is_asked_once_when_the_shared_browser_is_skipped(monkeypatch):
    asked = []
    monkeypatch.setattr(reader.render_mod, "enabled", lambda: True)
    monkeypatch.setattr(reader.stealth_mod, "enabled", lambda: False)
    monkeypatch.setattr(reader.render_mod, "try_render_direct",
                        lambda url, timeout=0: asked.append(url) or ("busy", None))
    urls = [f"https://h{i}.example/" for i in range(4)]
    failed = [{"url": u, "error": "HTTP 403", "reason": "blocked"} for u in urls]
    _docs, still = reader._render_failed([], failed, "text", urls, skips={u: {"browser"} for u in urls})
    assert asked == urls[:1] and [f["url"] for f in still] == urls


def test_skipped_static_fetches_run_together_in_one_parallel_call(monkeypatch):
    calls = []
    monkeypatch.setattr(reader.render_mod, "enabled", lambda: True)
    monkeypatch.setattr(reader.stealth_mod, "enabled", lambda: False)
    monkeypatch.setattr(reader.render_mod, "try_render_direct", lambda url, timeout=0: ("tried", None))
    urls = ["https://a.example/", "https://b.example/"]
    failed = [{"url": u, "error": "skipped", "reason": "skipped", "jumped": True} for u in urls]

    def fetch_static(late, seconds):
        calls.append(list(late))
        return [], [{"url": u, "error": "HTTP 404", "reason": "not_found"} for u in late]
    events = []
    _docs, still = reader._render_failed([], failed, "text", urls, skips={u: {"static", "browser"} for u in urls},
                                         events=events, fetch_static=fetch_static)
    assert calls == [urls]
    assert [f["reason"] for f in still] == ["not_found", "not_found"]
    assert all(e[1] == "static" for e in events)  # after a 404 the direct browser learned nothing


def test_an_unknown_host_or_one_with_only_neutral_failures_is_not_stored(memory):
    memory.plan(["https://new.example/"], ALL)
    memory.record([("https://slow.example/", "static", "timeout")])
    assert sqlite3.connect(memory.path).execute("SELECT COUNT(*) FROM host").fetchone()[0] == 0
# Stand: 10.11.2026
