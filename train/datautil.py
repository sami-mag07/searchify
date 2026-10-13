"""Reine Hilfsfunktionen fuer prepare_data.py und train_reranker.py.

Ohne torch/datasets importierbar, damit tests/test_train_datautil.py sie auf jedem
Rechner pruefen kann.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

# Anteil der Anfragen, die als Pruefmenge (dev) zurueckgelegt werden. Gilt fuer alle
# Quellen gleich, damit dieselbe Anfrage nie in Training und dev zugleich landet.
DEV_FRACTION = 0.004


def stable_unit(key: str) -> float:
    """Deterministische Zahl in [0, 1) aus einem Schluessel (gleich auf jedem Rechner)."""
    digest = hashlib.md5(key.encode("utf-8")).hexdigest()[:12]
    return int(digest, 16) / float(16**12)


def normalize_query(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def is_dev(key: str, fraction: Optional[float] = None) -> bool:
    return stable_unit("dev:" + key) < (DEV_FRACTION if fraction is None else fraction)


def assign_lang(key: str, weights: Dict[str, float]) -> str:
    """Ordnet einer Anfrage-ID genau eine Sprache zu (gewichtetes, deterministisches Los).

    Jede MS-MARCO-Anfrage landet so nur in einer Sprache: Das Modell sieht dieselbe
    Frage nicht viermal uebersetzt und lernt sie nicht auswendig."""
    items = [(k, w) for k, w in weights.items() if w > 0]
    if not items:
        raise ValueError("keine Sprache mit Gewicht > 0")
    total = sum(w for _, w in items)
    u = stable_unit("lang:" + key) * total
    acc = 0.0
    for lang, w in items:
        acc += w
        if u < acc:
            return lang
    return items[-1][0]


def pick_negatives(candidates: Sequence, k: int, key: str) -> List:
    """Bis zu k Kandidaten, deterministisch gemischt (nicht immer die ersten)."""
    ranked = sorted(candidates, key=lambda c: stable_unit(f"neg:{key}:{c}"))
    return list(ranked[:k])


def moment_match(teacher: Sequence[float], student: Sequence[float]) -> Tuple[float, float]:
    """a, b so dass a*teacher+b denselben Mittelwert und dieselbe Streuung hat wie student.

    Damit bleiben die Lehrer-Scores auf der Skala des Ausgangsmodells. Der Dienst
    nutzt feste Schwellen auf dieser Skala (Sigmoid, MIN_RESULT_SCORE)."""
    if len(teacher) != len(student) or len(teacher) < 2:
        raise ValueError("mindestens zwei Paare gleicher Laenge noetig")
    mt = sum(teacher) / len(teacher)
    ms = sum(student) / len(student)
    st = math.sqrt(sum((x - mt) ** 2 for x in teacher) / len(teacher))
    ss = math.sqrt(sum((y - ms) ** 2 for y in student) / len(student))
    if st == 0:
        return 0.0, ms
    a = ss / st
    return a, ms - a * mt


_CTX_SPLIT = re.compile(r"(?:^|\n)\[([^\[\]\n]{1,80})\] ")


def parse_miracl_prompt(content: str) -> Tuple[Optional[str], Dict[str, str]]:
    """Frage und Passagen aus einer Zeile von nthakur/miracl-monolingual-sft.

    Format: "Question: <q>\\n\\nContext: [docid] Titel - Text\\n[docid] ...\\n\\nInstruction: ..."
    Gibt (None, {}) zurueck, wenn das Format nicht passt."""
    if not content or not content.startswith("Question:") or "\n\nContext:" not in content:
        return None, {}
    head, rest = content.split("\n\nContext:", 1)
    question = head[len("Question:"):].strip()
    ctx = rest.split("\n\nInstruction:", 1)[0].strip()
    parts = _CTX_SPLIT.split(ctx)
    # parts = [vorlauf, id1, text1, id2, text2, ...]
    passages: Dict[str, str] = {}
    for i in range(1, len(parts) - 1, 2):
        text = parts[i + 1].strip()
        if text:
            passages[parts[i].strip()] = text
    return (question or None), passages


def split_lines(buffer: bytes) -> Tuple[List[bytes], bytes]:
    """Zerlegt einen Byte-Puffer in vollstaendige Zeilen und den unvollstaendigen Rest."""
    if b"\n" not in buffer:
        return [], buffer
    head, rest = buffer.rsplit(b"\n", 1)
    return head.split(b"\n"), rest


def parse_tsv_id_text(line: bytes) -> Optional[Tuple[int, str]]:
    """'<id>\\t<text>' -> (id, text). Kaputte Zeilen ergeben None."""
    try:
        raw_id, text = line.split(b"\t", 1)
        return int(raw_id), text.decode("utf-8", errors="replace").strip()
    except ValueError:
        return None


def group_dev(rows: Iterable[dict], max_negatives: int = 30) -> Iterator[dict]:
    """Fasst dev-Zeilen (query, passage, label) je Anfrage zu Rerank-Gruppen zusammen.

    Nur Gruppen mit mindestens einem Treffer und einem Nicht-Treffer bleiben."""
    groups: Dict[Tuple[str, str], dict] = {}
    for r in rows:
        key = (r.get("src", ""), r["qkey"])
        g = groups.setdefault(key, {"query": r["query"], "positive": [], "negative": [],
                                    "lang": r.get("lang", ""), "src": r.get("src", "")})
        bucket = g["positive"] if r["label"] >= 0.5 else g["negative"]
        if r["passage"] not in bucket:
            bucket.append(r["passage"])
    for g in groups.values():
        g["negative"] = g["negative"][:max_negatives]
        if g["positive"] and g["negative"]:
            yield g


def quotas(max_pairs: int, shares: Dict[str, float], lang_weights: Dict[str, float]) -> Dict[Tuple[str, str], int]:
    """Zielmenge je (Art, Sprache) aus Gesamtmenge, Anteil je Art und Sprachgewicht."""
    total_w = sum(lang_weights.values()) or 1.0
    out = {}
    for kind, share in shares.items():
        for lang, w in lang_weights.items():
            out[(kind, lang)] = int(max_pairs * share * w / total_w)
    return out


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024.0
    return f"{n:.1f} TB"
