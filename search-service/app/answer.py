"""LLM-Summary: erzeugt den `answer` aus den Top-Quellen.

Antwort-LLM ist Claude, angesprochen ueber claude-code-api (OpenAI-kompatibler
Wrapper). Base-URL, Key und Modell kommen AUSSCHLIESSLICH aus der Umgebung
(OPENAI_BASE_URL / OPENAI_API_KEY / OPENAI_MODEL). Der openai-Client wird lazy
importiert, damit dieses Modul ohne installierte Abhaengigkeit importierbar bleibt.

Prompt-Regel gegen Halluzination: Nur aus den uebergebenen Quellen antworten,
jede Aussage mit [n]-Quellenmarker, "keine Quelle -> kein Satz".
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional, Tuple

from .config import get_settings

log = logging.getLogger("rag-search")

SYSTEM_PROMPT = (
    "Du bist ein praeziser Recherche-Assistent. Beantworte die Frage AUSSCHLIESSLICH "
    "auf Basis der nummerierten Quellen. Erfinde nichts. Wenn die Quellen die Frage "
    "nicht abdecken, sage das klar. Belege jede Aussage mit einem Quellenmarker wie "
    "[1], [2]. Keine Quelle -> kein Satz. Antworte in der Sprache der Frage, knapp "
    "und ohne Vorrede."
)


LLM_TIMEOUT_S = 60.0


class AnswerUnavailable(RuntimeError):
    """Kein LLM konfiguriert. Eigene Klasse, damit Aufrufer das von echten
    Pipeline-Fehlern unterscheiden koennen."""


def configured() -> bool:
    return bool(get_settings().openai_api_key)


def try_answer(query: str, results: List[Dict]) -> Tuple[Optional[str], Dict, Optional[str]]:
    """generate_answer ohne Ausnahme nach aussen: (answer|None, usage, Grund|None).
    Fehlt das LLM oder ist es nicht erreichbar, bleiben die Treffer gueltig."""
    try:
        text, usage = generate_answer(query, results)
        return text, usage, None
    except AnswerUnavailable:
        return None, {}, "no_llm_configured"
    except Exception as exc:  # noqa: BLE001 - LLM ist Beiwerk, nie 500 fuer die Suche
        log.warning("LLM-Antwort fehlgeschlagen: %s", type(exc).__name__)
        return None, {}, "llm_unavailable"


def build_context(results: List[Dict], max_chars_per_source: int = 1500) -> str:
    """Baut den nummerierten Quellenblock fuer den Prompt."""
    lines: List[str] = []
    for i, r in enumerate(results, start=1):
        content = (r.get("content") or "")[:max_chars_per_source]
        title = r.get("title") or r.get("url", "")
        url = r.get("url", "")
        lines.append(f"[{i}] {title} ({url})\n{content}")
    return "\n\n".join(lines)


def generate_answer(query: str, results: List[Dict]) -> Tuple[str, Dict]:
    """Ruft Claude ueber den OpenAI-kompatiblen Wrapper auf.

    Rueckgabe: (answer_text, usage_dict). usage_dict enthaelt ggf.
    prompt_tokens/completion_tokens, falls der Wrapper sie liefert.
    """
    if not results:
        return ("Keine Quellen gefunden, daher keine belegbare Antwort.", {})

    try:
        from openai import OpenAI  # noqa: PLC0415 (bewusst lazy)
    except ImportError as exc:  # pragma: no cover - nur ohne Installation
        raise ImportError(
            "openai-Client ist nicht installiert. `pip install openai` "
            "(steht in requirements.txt)."
        ) from exc

    settings = get_settings()
    if not settings.openai_api_key:
        raise AnswerUnavailable(
            "OPENAI_API_KEY ist nicht gesetzt. Key nur ueber die Umgebung/.env "
            "bereitstellen, nie im Code."
        )

    # Kurze Frist, ein Wiederholungsversuch: ein haengendes LLM darf die Suche nicht
    # minutenlang blockieren (openai-Standard waeren 600 s und 2 Wiederholungen).
    client = OpenAI(base_url=settings.openai_base_url, api_key=settings.openai_api_key,
                    timeout=LLM_TIMEOUT_S, max_retries=1)
    context = build_context(results)
    user_prompt = (
        f"Frage: {query}\n\n"
        f"Quellen:\n{context}\n\n"
        "Antworte jetzt nur aus diesen Quellen, mit [n]-Markern."
    )

    completion = client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.0,
    )
    answer = (completion.choices[0].message.content or "").strip()

    usage: Dict = {}
    raw_usage = getattr(completion, "usage", None)
    if raw_usage is not None:
        usage = {
            "prompt_tokens": getattr(raw_usage, "prompt_tokens", None),
            "completion_tokens": getattr(raw_usage, "completion_tokens", None),
        }
    return (answer, usage)
