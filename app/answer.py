"""LLM-Summary: erzeugt den `answer` aus den Top-Quellen.

Antwort-LLM ist Claude, angesprochen ueber claude-code-api (OpenAI-kompatibler
Wrapper). Base-URL, Key und Modell kommen AUSSCHLIESSLICH aus der Umgebung
(OPENAI_BASE_URL / OPENAI_API_KEY / OPENAI_MODEL). Der openai-Client wird lazy
importiert, damit dieses Modul ohne installierte Abhaengigkeit importierbar bleibt.

Prompt-Regel gegen Halluzination: Nur aus den uebergebenen Quellen antworten,
jede Aussage mit [n]-Quellenmarker, "keine Quelle -> kein Satz".
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from .config import get_settings

SYSTEM_PROMPT = (
    "Du bist ein praeziser Recherche-Assistent. Beantworte die Frage AUSSCHLIESSLICH "
    "auf Basis der nummerierten Quellen. Erfinde nichts. Wenn die Quellen die Frage "
    "nicht abdecken, sage das klar. Belege jede Aussage mit einem Quellenmarker wie "
    "[1], [2]. Keine Quelle -> kein Satz. Antworte in der Sprache der Frage, knapp "
    "und ohne Vorrede."
)


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
        raise RuntimeError(
            "OPENAI_API_KEY ist nicht gesetzt. Key nur ueber die Umgebung/.env "
            "bereitstellen, nie im Code."
        )

    client = OpenAI(base_url=settings.openai_base_url, api_key=settings.openai_api_key)
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
