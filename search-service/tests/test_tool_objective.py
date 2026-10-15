"""Hilfsfunktionen von eval/tool_objective.py (ohne Netz, ohne Modelle)."""

import importlib.util
import os
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "tool_objective", Path(__file__).resolve().parent.parent / "eval" / "tool_objective.py")
to = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(to)


def test_split_of_is_stable_and_balanced():
    urls = [f"https://example.org/page/{i}" for i in range(400)]
    first = [to.split_of(u) for u in urls]
    assert first == [to.split_of(u) for u in urls]
    assert set(first) == {"dev", "test"}
    assert 150 < first.count("dev") < 250


def test_answer_matcher_tolerates_spaces():
    ok = to.answer_matcher(r"325[,.]\d\s?(km|Kilometer)")
    assert ok("Er ist 325,7 km lang")
    assert ok("325 , 7km")  # ohne Leerzeichen verglichen
    assert not ok("")
    assert not ok(None)
    assert not ok("326 km")


def test_first_rank_and_rank_stats():
    ok = to.answer_matcher("Gerkan")
    assert to.first_rank(["a", "von Gerkan", "Gerkan"], ok) == 2
    assert to.first_rank([], ok) is None
    s = to.rank_stats([1, 2, None, 4])
    assert s == {"hit1": 0.25, "hit3": 0.5, "mrr": round((1 + 0.5 + 0.25) / 4, 3)}


def test_weighted_skips_missing_and_caps():
    assert to.weighted({"a": 1, "b": 1}, {"a": 2.0, "b": None}) == 1.0
    assert to.weighted({"a": 1, "b": 3}, {"a": 0.0, "b": 1.0}) == 0.75
    assert to.weighted({"a": 1}, {}) == 0.0


@pytest.mark.parametrize("fn,m", [
    (to.score_serper, {"f_mrr": 1, "f_hit1": 1, "hist_mrr": 5, "hist_site30": 5, "hist_page30": 5}),
    (to.score_exa, {"top1_text": 1, "hl1": 1, "top3_hl": 1, "sim_ans5": 1, "sim_valid": 1}),
    (to.score_reader, {s: {"recall": 1, "precision": 1, "title_equal": 1, "action_hit": 1, "ok_rate": 1}
                       for s in ("jina", "extract")}),
])
def test_scores_bounded(fn, m):
    assert fn(m) == 1.0
    def zeroed(d):
        return {k: zeroed(v) if isinstance(v, dict) else 0 for k, v in d.items()}

    assert fn(zeroed(m)) == 0.0


def test_score_serper_normalisation():
    m = {"f_mrr": 0.5, "f_hit1": 0.5, "hist_mrr": 0.2, "hist_site30": 0.1, "hist_page30": 0.025}
    assert to.score_serper(m) == 0.5


def test_deviation_nested():
    a = {"dev": {"score": 0.5, "n": 10, "x": {"r": 0.1}}, "tool": "exa"}
    b = {"dev": {"score": 0.52, "n": 10, "x": {"r": 0.1}}, "tool": "exa"}
    assert to.deviation(a, b) == {"dev.score": 0.02}
    assert to.deviation(a, a) == {}


def test_rate_gate_spaces_calls():
    now = [0.0]
    slept = []

    def sleep(d):
        slept.append(round(d, 3))
        now[0] += d

    gate = to.RateGate(1.0, clock=lambda: now[0], sleep=sleep)
    for _ in range(3):
        gate.wait()
    assert slept == [1.0, 1.0]
    now[0] += 5
    gate.wait()
    assert slept == [1.0, 1.0]


def test_replay_records_success_failure_and_offline(tmp_path, monkeypatch):
    monkeypatch.setattr(to, "MISSES", {"x": 0})
    monkeypatch.setattr(to, "LIVE", {"x": 0})
    rp = to.Replay(str(tmp_path), "x", None, False, lambda m: RuntimeError(m))
    assert rp.get("k1", lambda: {"results": [1]}) == {"results": [1]}
    assert rp.get("k1", lambda: pytest.fail("darf nicht live gehen")) == {"results": [1]}

    def boom():
        raise ValueError("weg")

    with pytest.raises(ValueError):
        rp.get("k2", boom)
    with pytest.raises(RuntimeError):  # Fehlschlag wird wiedergegeben, nicht neu geholt
        rp.get("k2", lambda: pytest.fail("darf nicht live gehen"))
    assert to.LIVE["x"] == 2

    off = to.Replay(str(tmp_path), "x", None, True, lambda m: RuntimeError(m))
    assert off.get("k3", lambda: pytest.fail("offline")) == {"results": []}
    assert to.MISSES["x"] == 1
    assert sorted(os.listdir(tmp_path / "x")) == sorted([
        to.Replay(str(tmp_path), "x", None, False, None).path("k1").split("/")[-1],
        to.Replay(str(tmp_path), "x", None, False, None).path("k2").split("/")[-1] + ".err"])
