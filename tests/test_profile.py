"""The user profile file: path per OS, atomic write, read once, bad content ignored."""
import json
import os
from pathlib import Path

import pytest

from searchify import profile


@pytest.mark.parametrize("platform, env, home, expected", [
    ("darwin", {}, "/Users/ann", "/Users/ann/Library/Application Support/searchify"),
    ("darwin", {"XDG_CONFIG_HOME": "/x"}, "/Users/ann", "/Users/ann/Library/Application Support/searchify"),
    ("linux", {}, "/home/ann", "/home/ann/.config/searchify"),
    ("linux", {"XDG_CONFIG_HOME": "/home/ann/cfg"}, "/home/ann", "/home/ann/cfg/searchify"),
    ("linux", {"XDG_CONFIG_HOME": "relative/dir"}, "/home/ann", "/home/ann/.config/searchify"),
    ("linux", {"XDG_CONFIG_HOME": ""}, "/home/ann", "/home/ann/.config/searchify"),
    ("win32", {"APPDATA": "C:/Users/ann/AppData/Roaming"}, "C:/Users/ann",
     "C:/Users/ann/AppData/Roaming/searchify"),
    ("win32", {}, "C:/Users/ann", "C:/Users/ann/AppData/Roaming/searchify"),
    ("linux", {"SEARCHIFY_CONFIG_DIR": "/tmp/own"}, "/home/ann", "/tmp/own"),
])
def test_profile_directory_per_os(platform, env, home, expected):
    assert profile.config_dir(platform=platform, env=env, home=home) == Path(expected)


def test_profile_path_uses_the_real_platform_by_default(monkeypatch):
    monkeypatch.delenv("SEARCHIFY_CONFIG_DIR")
    assert profile.profile_path().parent.name == "searchify"
    assert profile.profile_path().name == "profile.json"


def test_save_then_load_roundtrip_and_file_mode():
    path = profile.save({"max_stage": "tls", "budget": "low", "region": "de-de"})
    assert json.loads(path.read_text()) == {"version": 1, "max_stage": "tls", "budget": "low",
                                           "region": "de-de"}
    assert (os.stat(path).st_mode & 0o077) == 0 or os.name == "nt"  # private to the user
    profile.reset_cache()
    assert profile.load() == {"max_stage": "tls", "budget": "low", "region": "de-de"}


def test_the_write_is_atomic_the_old_file_survives_a_failed_write(monkeypatch):
    profile.save({"max_stage": "plain"})
    path = profile.profile_path()

    def boom(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(profile.os, "replace", boom)
    with pytest.raises(OSError):
        profile.save({"max_stage": "stealth"})
    assert json.loads(path.read_text())["max_stage"] == "plain"  # not half-written, not replaced
    assert [p.name for p in path.parent.iterdir()] == ["profile.json"]  # no temp file left


def test_the_write_goes_through_a_temp_file_in_the_same_folder(monkeypatch):
    seen = []
    real = os.replace

    def spy(src, dst):
        seen.append((Path(src).parent, Path(dst)))
        real(src, dst)
    monkeypatch.setattr(profile.os, "replace", spy)
    profile.save({"budget": "high"})
    (src_dir, dst), = seen
    assert src_dir == dst.parent  # same filesystem, so the rename is atomic


def test_profile_is_read_once_per_process():
    profile.save({"budget": "low"})
    profile.reset_cache()
    assert profile.load() == {"budget": "low"}
    profile.profile_path().write_text(json.dumps({"budget": "high"}))  # changed behind our back
    assert profile.load() == {"budget": "low"}  # not read again
    assert profile.load(force=True) == {"budget": "high"}


@pytest.mark.parametrize("content", [
    "", "not json", "[1, 2]", '{"max_stage": "turbo", "budget": 5, "region": "xx-yy"}',
    '{"max_stage": ["plain"]}', "\x00\x01",
])
def test_broken_or_invalid_content_is_ignored_not_fatal(content):
    path = profile.profile_path()
    path.parent.mkdir(parents=True)
    path.write_text(content)
    assert profile.load() == {}


@pytest.mark.parametrize("content", [
    "[" * 30_000 + "]" * 30_000,          # under MAX_BYTES, but deeper than the JSON parser goes
    '{"a": ' * 20_000 + "1" + "}" * 20_000,
    "{" + " " * (profile.MAX_BYTES + 10) + "}",  # too large: only MAX_BYTES + 1 are read
    b"\xff\xfe".decode("latin-1"),         # not UTF-8
])
def test_a_deep_huge_or_undecodable_profile_is_broken_not_fatal(content, caplog):
    path = profile.profile_path()
    path.parent.mkdir(parents=True)
    path.write_text(content, encoding="latin-1")
    with caplog.at_level("WARNING"):
        assert profile.load() == {}
    assert any("profile" in r.getMessage() and "default" in r.getMessage() for r in caplog.records)


def test_config_replaces_a_profile_too_deep_to_read():
    from typer.testing import CliRunner

    from searchify import cli

    path = profile.profile_path()
    path.parent.mkdir(parents=True)
    path.write_text("[" * 30_000 + "]" * 30_000)
    runner = CliRunner()
    shown = runner.invoke(cli.app, ["config", "--show"])
    assert shown.exit_code == 0 and json.loads(shown.stdout)["values"]["budget"]["source"] == "default"
    assert runner.invoke(cli.app, ["config", "--budget", "low"]).exit_code == 0
    assert profile.load(force=True) == {"budget": "low"}


def test_unknown_keys_and_bad_values_are_dropped_on_load():
    path = profile.profile_path()
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"max_stage": "browser", "budget": "huge", "evil": "x",
                                "region": "de-de"}))
    assert profile.load() == {"max_stage": "browser", "region": "de-de"}


def test_a_missing_profile_is_empty_and_not_created_by_reading():
    assert profile.load() == {} and not profile.exists()
    assert not profile.profile_path().parent.exists()
