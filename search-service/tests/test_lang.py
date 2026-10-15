"""Spracherkennung und sprachabhaengige Engine-Wahl."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.lang import detect  # noqa: E402
from app.stream import engines_for  # noqa: E402


def test_detect_vier_sprachen():
    assert detect("¿Quién escribió Cien años de soledad?") == "es"
    assert detect("Qui a écrit Les Misérables ?") == "fr"
    assert detect("Wie hoch ist die Zugspitze?") == "de"
    assert detect("What is the capital of Canada?") == "en"
    assert detect("Welchen Standardport nutzt SSH?") == "de"
    assert detect("Python") is None  # unklar -> Backend entscheidet


def test_engines_je_sprache(monkeypatch):
    monkeypatch.setenv("SEARXNG_SPLIT_ENGINES", "bing,yahoo")
    monkeypatch.delenv("SEARXNG_SPLIT_ENGINES_ES", raising=False)
    assert engines_for("de") == ["bing", "yahoo"]
    assert engines_for(None) == ["bing", "yahoo"]
    assert engines_for("es") == ["yandex", "yahoo"]
    monkeypatch.setenv("SEARXNG_SPLIT_ENGINES_FR", "yahoo")
    assert engines_for("fr") == ["yahoo"]
