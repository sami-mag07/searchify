"""Golden: with nothing set, the reading pipeline gives exactly what it gave before the ladder limits."""
import json
import os
from pathlib import Path

from leiter_pipeline import install, snapshot

GOLDEN = Path(__file__).parent / "golden" / "leiter_default.json"


def _current(monkeypatch):
    out = {}
    install(monkeypatch, browser=True, stealth=True, tavily=True)
    out["full"] = snapshot()
    install(monkeypatch, browser=True, stealth=False, tavily=False)
    out["browser_only"] = snapshot()
    install(monkeypatch, browser=False, stealth=False, tavily=False)
    out["static_only"] = snapshot()
    return out


def test_default_output_is_unchanged(monkeypatch):
    for var in ("SEARCHIFY_MAX_STAGE",):
        monkeypatch.delenv(var, raising=False)
    now = _current(monkeypatch)
    if os.environ.get("GOLDEN_WRITE"):
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(now, indent=1, sort_keys=True, ensure_ascii=False) + "\n")
    assert now == json.loads(GOLDEN.read_text())


def test_golden_covers_every_rung():
    gold = json.loads(GOLDEN.read_text())
    stages = {d["stage"] for run in gold.values() for d in run["docs"]}
    assert {"static", "browser", "stealth", "tavily"} <= stages
