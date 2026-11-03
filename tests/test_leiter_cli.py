"""CLI: the flags, the profile precedence, `searchify config`, and the interview (only at a terminal)."""
import io
import json
import sys

import pytest
from leiter_pipeline import install
from typer.testing import CliRunner

from searchify import cli, interview, profile

runner = CliRunner()
GOLDEN = json.load(open(__import__("pathlib").Path(__file__).parent / "golden" / "leiter_default.json"))


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("SEARCHIFY_MAX_STAGE", raising=False)
    for var in interview.CI_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(interview, "_asked", False)


@pytest.fixture
def seen(monkeypatch):
    """Replace the reader; `seen` collects the keyword arguments of the call."""
    got = {}

    def fake(urls, fmt, timeout=None, progress=None, **kw):
        assert kw.pop("structured", False) is False  # raw structured stays opt-in
        got.clear()
        got.update(kw)
        return [{"url": urls[0], "title": "T", "content": "body"}], [], {}
    monkeypatch.setattr(sys.modules["searchify.extract"], "read_pages_ex", fake)
    return got


def _extract(*args):
    return runner.invoke(cli.app, ["extract", "https://a.example/", *args])


# ------------------------------------------------------------ flags and precedence
def test_nothing_set_calls_the_reader_without_limits(seen):
    assert _extract().exit_code == 0
    assert seen == {}


def test_flags_reach_the_reader(seen):
    assert _extract("--max-stage", "tls", "--budget", "low", "--no-cloud").exit_code == 0
    assert seen == {"max_stage": "tls", "budget": "low", "cloud_fallback": False}


def test_profile_fills_what_the_flags_leave_out(seen):
    profile.save({"max_stage": "plain", "budget": "high"})
    _extract()
    assert seen == {"max_stage": "plain", "budget": "high"}
    _extract("--max-stage", "browser")
    assert seen == {"max_stage": "browser", "budget": "high"}  # request > profile, per value


@pytest.mark.parametrize("args", [["--max-stage", "turbo"], ["--budget", "huge"]])
def test_bad_flag_values_are_a_clean_json_error(seen, args):
    result = _extract(*args)
    assert result.exit_code == 1
    assert "unknown" in json.loads(result.stdout)["error"]


def test_default_cli_output_is_unchanged_without_anything_set(monkeypatch):
    install(monkeypatch, browser=False, stealth=False, tavily=False)
    result = _extract_urls(["https://article.example/", "https://wall.example/", "https://stuck.example/"])
    out = json.loads(result.stdout)
    assert out["results"] == GOLDEN["static_only"]["docs"]
    assert set(out) == {"results", "failed"}


def _extract_urls(urls, *args):
    return runner.invoke(cli.app, ["extract", *urls, *args])


def test_plain_flag_through_the_real_pipeline(monkeypatch):
    calls = []
    install(monkeypatch, browser=True, stealth=True, calls=calls)
    result = _extract_urls(["https://blocked.example/", "https://wall.example/"], "--max-stage", "plain")
    assert {c[0] for c in calls} == {"fetch"}
    assert result.exit_code == 0  # wall.example is delivered thin
    assert json.loads(result.stdout)["failed"][0]["reason"] == "blocked"


# ------------------------------------------------------------ the interview never runs without a terminal
class _Tty(io.StringIO):
    def isatty(self):
        return True


@pytest.mark.parametrize("stdin, stderr, env, no_input, expected", [
    (True, True, {}, False, True),
    (False, True, {}, False, False),
    (True, False, {}, False, False),
    (False, False, {}, False, False),
    (True, True, {"CI": "true"}, False, False),
    (True, True, {"GITHUB_ACTIONS": "true"}, False, False),
    (True, True, {}, True, False),
])
def test_interactive_needs_both_ttys_no_ci_and_no_no_input(monkeypatch, stdin, stderr, env, no_input, expected):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(sys, "stdin", _Tty() if stdin else io.StringIO())
    monkeypatch.setattr(sys, "stderr", _Tty() if stderr else io.StringIO())
    assert interview.interactive(no_input) is expected


@pytest.mark.parametrize("command", [
    ["extract", "https://a.example/"], ["search", "q"], ["passages", "q"],
])
def test_no_command_interviews_without_a_terminal(monkeypatch, seen, command):
    def forbidden(*a, **k):
        raise AssertionError("the interview ran without a terminal")
    monkeypatch.setattr(interview, "run", forbidden)
    monkeypatch.setattr(sys.modules["searchify.search"], "search_report",
                        lambda *a, **k: {"results": [], "error": None, "backend": None})
    monkeypatch.setattr(sys.modules["searchify.passages"], "passages", lambda *a, **k: [])
    runner.invoke(cli.app, command, input="3\n")  # even with input on stdin
    assert not profile.exists()


def test_first_run_never_asks_when_stdout_is_redirected(monkeypatch):
    # `searchify search q > out.json` at a terminal: stdin and stderr are TTYs, stdout a file
    monkeypatch.setattr(sys, "stdin", _Tty())
    monkeypatch.setattr(sys, "stderr", _Tty())
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    monkeypatch.setattr(interview, "run", lambda *a, **k: pytest.fail("asked although stdout is a file"))
    interview.first_run()
    assert not profile.exists()
    asked = []
    monkeypatch.setattr(sys, "stdout", _Tty())
    monkeypatch.setattr(interview, "run", lambda *a, **k: asked.append(1))
    interview.first_run()
    assert asked == [1]  # all three at a terminal: the interview runs


def test_the_service_and_mcp_modules_do_not_even_import_the_interview():
    import searchify.mcp_server as mcp_mod
    import searchify.service as service_mod

    for mod in (mcp_mod, service_mod):
        assert "interview" not in vars(mod) and "profile" not in vars(mod)


def _terminal(monkeypatch):
    monkeypatch.setattr(interview, "interactive", lambda no_input=False, env=None: not no_input)
    monkeypatch.setattr(interview, "_stdout_is_tty", lambda: True)  # CliRunner captures stdout


def test_first_run_asks_on_stderr_and_stdout_stays_clean_json(monkeypatch, seen):
    _terminal(monkeypatch)
    monkeypatch.setattr(interview, "detect_region", lambda env=None: "de-de")
    result = runner.invoke(cli.app, ["extract", "https://a.example/"], input="plain\nlow\ny\n")
    assert result.exit_code == 0
    json.loads(result.stdout)  # nothing but the JSON on stdout
    assert "How far may searchify go" in result.stderr and "Germany (de-de)" in result.stderr
    assert profile.load(force=True) == {"max_stage": "plain", "budget": "low", "region": "de-de"}
    assert seen == {"max_stage": "plain", "budget": "low"}  # the answers apply to this very call


def test_enter_everywhere_means_todays_behaviour_and_is_not_asked_again(monkeypatch, seen):
    _terminal(monkeypatch)
    monkeypatch.setattr(interview, "detect_region", lambda env=None: "de-de")
    result = runner.invoke(cli.app, ["extract", "https://a.example/"], input="\n\n\n")
    assert result.exit_code == 0 and seen == {}
    assert profile.exists() and profile.load(force=True) == {}  # a region is never a silent default
    monkeypatch.setattr(interview, "_asked", False)
    monkeypatch.setattr(interview, "run", lambda *a, **k: pytest.fail("asked a second time"))
    runner.invoke(cli.app, ["extract", "https://a.example/"])


def test_later_writes_nothing_and_asks_again_next_time(monkeypatch, seen):
    _terminal(monkeypatch)
    result = runner.invoke(cli.app, ["extract", "https://a.example/"], input="later\n")
    assert result.exit_code == 0
    assert not profile.exists() and not profile.config_dir().exists()
    monkeypatch.setattr(interview, "_asked", False)
    result = runner.invoke(cli.app, ["extract", "https://a.example/"], input="tls\n\n\n")
    assert profile.load(force=True) == {"max_stage": "tls"}


def test_closed_input_counts_as_later(monkeypatch, seen):
    _terminal(monkeypatch)
    runner.invoke(cli.app, ["extract", "https://a.example/"], input="")
    assert not profile.exists()


def test_no_input_flag_skips_the_interview(monkeypatch, seen):
    _terminal(monkeypatch)
    monkeypatch.setattr(interview, "run", lambda *a, **k: pytest.fail("asked despite --no-input"))
    assert _extract("--no-input").exit_code == 0


def test_an_existing_profile_is_never_replaced_by_an_interview(monkeypatch, seen):
    _terminal(monkeypatch)
    profile.save({"max_stage": "browser"})
    monkeypatch.setattr(interview, "run", lambda *a, **k: pytest.fail("asked although a profile exists"))
    _extract()
    assert seen == {"max_stage": "browser"}


def test_at_most_three_questions_and_the_region_one_only_with_a_suggestion(monkeypatch):
    out = io.StringIO()
    monkeypatch.setattr(interview, "detect_region", lambda env=None: None)
    answers = interview.run(stdin=io.StringIO("plain\nlow\n"), stderr=out)
    assert answers == {"max_stage": "plain", "budget": "low"}
    assert "3/3" not in out.getvalue()
    out = io.StringIO()
    monkeypatch.setattr(interview, "detect_region", lambda env=None: "fr-fr")
    interview.run(stdin=io.StringIO("\n\nn\n"), stderr=out)
    assert out.getvalue().count("/3 ") == 3


def test_invalid_answers_are_asked_again_then_fall_back_to_enter():
    out = io.StringIO()
    answers = interview.run(stdin=io.StringIO("turbo\n3\n\n\n"), stderr=out, env={})
    assert answers["max_stage"] == "browser" and "please type one of" in out.getvalue()
    out = io.StringIO()
    answers = interview.run(stdin=io.StringIO("x\nx\nx\n\n"), stderr=out, env={})
    assert "max_stage" not in answers  # three misses: Enter's meaning, no limit


@pytest.mark.parametrize("env, expected", [
    ({"LANG": "de_DE.UTF-8"}, "de-de"),
    ({"LC_ALL": "nl_BE.UTF-8", "LANG": "en_US.UTF-8"}, "be-nl"),
    ({"LANG": "fr_FR@euro"}, "fr-fr"),
    ({"LANG": "de"}, "de-de"),
    ({"LANG": "C"}, None),
    ({"LANG": "POSIX"}, None),
    ({"LANG": "C.UTF-8"}, None),
    ({"LANG": "xx_YY"}, None),
    ({}, None),
])
def test_region_suggestion_from_the_system_locale(env, expected):
    assert interview.detect_region(env) == expected


# ------------------------------------------------------------ searchify config
def _config(*args, input=None):
    return runner.invoke(cli.app, ["config", *args], input=input)


def test_config_show_prints_each_value_with_its_source(monkeypatch):
    out = json.loads(_config("--show").stdout)
    assert out["values"]["max_stage"] == {"value": "stealth", "source": "default"}
    assert out["values"]["budget"] == {"value": "normal", "source": "default"}
    assert out["exists"] is False and out["operator_cap"] is None
    profile.save({"max_stage": "tls", "region": "de-de"})
    out = json.loads(_config("--show").stdout)
    assert out["values"]["max_stage"]["source"] == "profile"
    assert out["values"]["region"] == {"value": "de-de", "source": "profile"}
    monkeypatch.setenv("SEARCHIFY_MAX_STAGE", "plain")
    out = json.loads(_config("--show").stdout)
    assert out["values"]["max_stage"] == {"value": "plain", "source": "env"}
    assert out["operator_cap"] == "plain"
    text = _config("--show", "--no-json").stdout
    assert "max_stage: plain (env)" in text and "region: de-de (profile)" in text


def test_config_show_creates_nothing():
    _config("--show")
    assert not profile.config_dir().exists()


def test_config_sets_values_without_a_terminal_and_clears_them():
    result = _config("--max-stage", "browser", "--budget", "low", "--region", "nl-nl")
    assert result.exit_code == 0
    assert profile.load(force=True) == {"max_stage": "browser", "budget": "low", "region": "nl-nl"}
    _config("--budget", "default")
    assert profile.load(force=True) == {"max_stage": "browser", "region": "nl-nl"}


@pytest.mark.parametrize("args", [["--max-stage", "turbo"], ["--budget", "huge"], ["--region", "mars"]])
def test_config_refuses_bad_values_and_writes_nothing(args):
    result = _config(*args)
    assert result.exit_code == 1 and "unknown" in json.loads(result.stdout)["error"]
    assert not profile.exists()


def test_bare_config_needs_a_terminal():
    result = _config()
    assert result.exit_code == 1 and "needs a terminal" in json.loads(result.stdout)["error"]


def test_bare_config_at_a_terminal_keeps_current_answers_on_enter(monkeypatch):
    _terminal(monkeypatch)
    monkeypatch.setattr(interview, "detect_region", lambda env=None: None)
    profile.save({"max_stage": "plain", "budget": "high"})
    result = _config(input="\nlow\n")
    assert result.exit_code == 0
    assert profile.load(force=True) == {"max_stage": "plain", "budget": "low"}
    assert "Enter = keep plain" in result.stderr


def test_config_later_leaves_the_profile_alone(monkeypatch):
    _terminal(monkeypatch)
    profile.save({"max_stage": "plain"})
    before = profile.profile_path().read_text()
    _config(input="later\n")
    assert profile.profile_path().read_text() == before
