"""Gemeinsame Test-Vorbereitung."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import main as main_mod  # noqa: E402


@pytest.fixture(autouse=True)
def _leerer_antwort_cache():
    """Kein Test sieht gecachte /search-Antworten eines anderen Tests."""
    main_mod.clear_response_cache()
    yield
    main_mod.clear_response_cache()


@pytest.fixture(autouse=True)
def _camoufox_aus(monkeypatch):
    """Camoufox-Stufe (stealth.py) startet in Tests nur, wenn ein Test sie einschaltet."""
    monkeypatch.setenv("READER_STEALTH", "0")
