"""Reine Helfer des Laptop-Trainings (train/datautil.py), ohne torch/datasets."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "train"))
import datautil as du  # noqa: E402


def test_stable_unit_is_deterministic_and_in_range():
    a = du.stable_unit("msmarco:123")
    assert a == du.stable_unit("msmarco:123")
    assert 0.0 <= a < 1.0
    assert a != du.stable_unit("msmarco:124")


def test_assign_lang_follows_weights_and_is_stable():
    w = {"en": 0.4, "de": 0.2, "es": 0.2, "fr": 0.2}
    langs = [du.assign_lang(str(i), w) for i in range(20000)]
    share_en = langs.count("en") / len(langs)
    assert 0.37 < share_en < 0.43
    assert du.assign_lang("42", w) == du.assign_lang("42", w)
    assert du.assign_lang("42", {"de": 1.0}) == "de"
    with pytest.raises(ValueError):
        du.assign_lang("1", {"en": 0})


def test_is_dev_fraction():
    hits = sum(du.is_dev(f"q{i}", 0.01) for i in range(50000))
    assert 350 < hits < 650


def test_moment_match_maps_scale():
    teacher = [-10.0, 0.0, 10.0]
    student = [-4.0, 1.0, 6.0]
    a, b = du.moment_match(teacher, student)
    mapped = [a * t + b for t in teacher]
    assert mapped == pytest.approx(student)
    assert du.moment_match([1.0, 1.0], [2.0, 4.0]) == (0.0, 3.0)
    with pytest.raises(ValueError):
        du.moment_match([1.0], [1.0])


def test_parse_miracl_prompt():
    content = (
        "Question: Wer schrieb Faust?\n\n"
        "Context: [12#0] Faust - Faust ist eine Tragoedie von Goethe.\n"
        "[12#3] Faust - Teil zwei erschien [1832] nach seinem Tod.\n"
        "[99#1] Mephisto - Figur im Faust.\n\n"
        "Instruction: Antworte auf Deutsch."
    )
    q, passages = du.parse_miracl_prompt(content)
    assert q == "Wer schrieb Faust?"
    assert list(passages) == ["12#0", "12#3", "99#1"]
    assert passages["12#3"] == "Faust - Teil zwei erschien [1832] nach seinem Tod."
    assert du.parse_miracl_prompt("kein Format") == (None, {})


def test_split_lines_and_tsv():
    lines, rest = du.split_lines(b"1\tA\n2\tB\n3\tC")
    assert lines == [b"1\tA", b"2\tB"] and rest == b"3\tC"
    assert du.split_lines(b"halb") == ([], b"halb")
    assert du.parse_tsv_id_text(b"17\tHallo Welt ") == (17, "Hallo Welt")
    assert du.parse_tsv_id_text(b"kaputt") is None
    assert du.parse_tsv_id_text(b"x\ty") is None


def test_group_dev_keeps_only_complete_groups():
    rows = [
        {"src": "s", "qkey": "a", "query": "qa", "passage": "p1", "label": 1, "lang": "de"},
        {"src": "s", "qkey": "a", "query": "qa", "passage": "n1", "label": 0, "lang": "de"},
        {"src": "s", "qkey": "a", "query": "qa", "passage": "n1", "label": 0, "lang": "de"},
        {"src": "s", "qkey": "b", "query": "qb", "passage": "n2", "label": 0, "lang": "de"},
    ]
    groups = list(du.group_dev(rows))
    assert len(groups) == 1
    assert groups[0]["positive"] == ["p1"] and groups[0]["negative"] == ["n1"]


def test_quotas_and_pick_negatives():
    q = du.quotas(1000, {"distill": 0.5}, {"en": 0.5, "de": 0.5})
    assert q == {("distill", "en"): 250, ("distill", "de"): 250}
    picked = du.pick_negatives([1, 2, 3, 4, 5], 2, "k")
    assert len(picked) == 2 and picked == du.pick_negatives([5, 4, 3, 2, 1], 2, "k")
