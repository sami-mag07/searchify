"""Sprache einer Suchanfrage erkennen (Englisch, Deutsch, Spanisch, Franzoesisch).

Bewusst klein und ohne Modell: Zeichen, die nur eine Sprache nutzt (¿ ¡ ñ fuer
Spanisch, ß ä ö ü fuer Deutsch, ç « » œ fuer Franzoesisch), dann Funktions-
woerter. Liefert None, wenn nichts eindeutig ist; dann entscheidet das
Such-Backend selbst. Wichtig, weil Bing ueber SearXNG bei spanischen oder
franzoesischen Fragen ohne Sprachangabe Unsinn liefert.
"""

from __future__ import annotations

import re
from typing import Optional

SUPPORTED = ("en", "de", "es", "fr")

_WORDS = {
    "en": {"the", "what", "who", "which", "how", "when", "where", "why", "is", "are", "was",
           "does", "did", "of", "in", "and", "to", "for", "with", "many", "much", "year"},
    "de": {"der", "die", "das", "wer", "wie", "was", "wann", "wo", "welche", "welcher",
           "welches", "ist", "sind", "hat", "und", "von", "mit", "für", "viele", "jahr", "den",
           "dem", "ein", "eine", "seit", "wofür", "warum", "welchen", "welchem", "bei", "im",
           "wurde", "nach", "nutzt", "auf", "nicht", "auch", "fuer", "ueber", "beste", "besten",
           "oder", "aber", "zum", "zur", "wird", "werden", "koennen", "muss", "gibt"},
    "es": {"el", "la", "los", "las", "qué", "que", "quién", "quien", "cuál", "cual", "cuántos",
           "cuántas", "cuánto", "cómo", "dónde", "cuándo", "es", "son", "de", "del", "y", "en",
           "por", "para", "con", "año", "una", "un"},
    "fr": {"le", "la", "les", "qui", "quel", "quelle", "quels", "quelles", "combien", "comment",
           "où", "quand", "pourquoi", "est", "sont", "de", "du", "des", "et", "en", "pour",
           "avec", "année", "une", "un", "a-t-il", "a-t-elle", "été"},
}


def detect(text: str) -> Optional[str]:
    """'en' | 'de' | 'es' | 'fr' oder None, wenn unklar."""
    t = text.lower()
    if re.search(r"[¿¡ñ]", t):
        return "es"
    if re.search(r"[ßäöü]", t):
        return "de"
    if re.search(r"[çœ«»]|\b(qu|l|d|j|n|c)'\w", t):
        return "fr"
    words = re.findall(r"[\wáéíóúàâèêëîïôûùç'-]+", t)
    if not words:
        return None
    scores = {lang: sum(1 for w in words if w in vocab) for lang, vocab in _WORDS.items()}
    best = max(scores, key=scores.get)
    ranked = sorted(scores.values(), reverse=True)
    if ranked[0] == 0 or ranked[0] == ranked[1]:
        return None
    return best
