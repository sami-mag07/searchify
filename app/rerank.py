"""Re-Ranking + Dedup.

- BM25-Vorauswahl je Quelle, dann Cross-Encoder-Reranking Query<->Chunk (bge-reranker, MIT)
  ueber transformers, auf CUDA/MPS wenn vorhanden.
- Dedup/Kanonisierung als reine Python-Heuristik (keine Schwer-Abhaengigkeit).

Das Reranker-Modell wird LAZY geladen und als Singleton gecacht, damit dieses
Modul ohne installiertes transformers/torch importierbar bleibt.
score = sigmoid-normalisierter Reranker-Score auf 0..1 (eigene, offengelegte Skala).
"""

from __future__ import annotations

import math
import re
from typing import Dict, List, Optional
from urllib.parse import urlparse, urlunparse

from .config import get_settings

_RERANKER = None  # (tokenizer, model, device) - lazy Singleton


def _pick_device():
    import torch  # noqa: PLC0415 (bewusst lazy)

    if torch.cuda.is_available():
        return "cuda"
    if torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def _get_reranker():
    """Laedt den bge-Cross-Encoder direkt ueber transformers auf GPU/MPS, sonst CPU."""
    global _RERANKER
    if _RERANKER is not None:
        return _RERANKER
    try:
        import torch  # noqa: PLC0415
        from transformers import AutoModelForSequenceClassification, AutoTokenizer  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - nur ohne Installation
        raise ImportError(
            "transformers/torch sind nicht installiert (stehen in requirements.txt)."
        ) from exc

    settings = get_settings()
    device = _pick_device()
    dtype = torch.float16 if settings.reranker_use_fp16 and device != "cpu" else torch.float32
    tokenizer = AutoTokenizer.from_pretrained(settings.reranker_model)
    model = AutoModelForSequenceClassification.from_pretrained(
        settings.reranker_model, torch_dtype=dtype
    ).to(device).eval()
    _RERANKER = (tokenizer, model, device)
    return _RERANKER


def _cross_scores(query: str, texts: List[str], max_length: int, batch: int = 32) -> List[float]:
    import torch  # noqa: PLC0415

    tokenizer, model, device = _get_reranker()
    scores: List[float] = []
    with torch.inference_mode():
        for i in range(0, len(texts), batch):
            enc = tokenizer(
                [query] * len(texts[i : i + batch]),
                texts[i : i + batch],
                padding=True,
                truncation="only_second",
                max_length=max_length,
                return_tensors="pt",
            ).to(device)
            scores.extend(model(**enc).logits.view(-1).float().cpu().tolist())
    return scores


_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _tokens(text: str) -> List[str]:
    return [t for t in _TOKEN_RE.findall(text.lower()) if len(t) > 1]


def lexical_scores(query: str, texts: List[str], k1: float = 1.2, b: float = 0.75) -> List[float]:
    """BM25 der Query gegen jeden Text, IDF aus genau dieser Chunk-Menge.

    Billiger Vorfilter vor dem Cross-Encoder (reine Python-Funktion, testbar).
    """
    docs = [_tokens(t) for t in texts]
    if not docs:
        return []
    avg_len = sum(len(d) for d in docs) / len(docs) or 1.0
    q_terms = set(_tokens(query))
    df = {t: sum(1 for d in docs if t in d) for t in q_terms}
    n = len(docs)
    out: List[float] = []
    for d in docs:
        tf: Dict[str, int] = {}
        for t in d:
            if t in q_terms:
                tf[t] = tf.get(t, 0) + 1
        score = 0.0
        for t, f in tf.items():
            idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            score += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * len(d) / avg_len))
        out.append(score)
    return out


def best_chunks(query: str, chunks: List[Dict], limit: int) -> List[Dict]:
    """Die `limit` lexikalisch besten Chunks (BM25), Reihenfolge bleibt erhalten."""
    if len(chunks) <= limit:
        return chunks
    scores = lexical_scores(query, [c.get("content", "") for c in chunks])
    keep = set(sorted(range(len(chunks)), key=lambda i: scores[i], reverse=True)[:limit])
    return [c for i, c in enumerate(chunks) if i in keep]


def _sigmoid(x: float) -> float:
    try:
        return 1.0 / (1.0 + math.exp(-x))
    except OverflowError:  # pragma: no cover
        return 0.0 if x < 0 else 1.0


def canonical_url(url: str) -> str:
    """Kanonisiert eine URL fuer Dedup (Schema/Host klein, ohne Query/Fragment/Trailing-Slash)."""
    try:
        p = urlparse(url)
    except ValueError:
        return url
    scheme = (p.scheme or "http").lower()
    netloc = p.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = p.path.rstrip("/")
    return urlunparse((scheme, netloc, path, "", "", ""))


def _norm_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def _shingles(text: str, k: int = 5) -> set:
    words = _norm_text(text).split()
    if len(words) < k:
        return {" ".join(words)} if words else set()
    return {" ".join(words[i : i + k]) for i in range(len(words) - k + 1)}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def dedup_chunks(chunks: List[Dict], threshold: float = 0.8) -> List[Dict]:
    """Entfernt Near-Duplicate-Chunks (Jaccard ueber 5-Wort-Shingles).

    Reine Python-Heuristik, keine Modelle noetig - gut testbar.
    Reihenfolge bleibt erhalten (erster Treffer gewinnt).
    """
    kept: List[Dict] = []
    kept_shingles: List[set] = []
    for ch in chunks:
        sh = _shingles(ch.get("content", ""))
        if any(_jaccard(sh, prev) >= threshold for prev in kept_shingles):
            continue
        kept.append(ch)
        kept_shingles.append(sh)
    return kept


def rerank(query: str, chunks: List[Dict], top_pool: Optional[int] = None) -> List[Dict]:
    """Rerankt Chunks gegen die Query. Setzt 'score' (0..1) je Chunk.

    bge-Cross-Encoder ueber alle uebergebenen Chunks (Vorauswahl macht der Aufrufer
    mit best_chunks). Rueckgabe absteigend nach score sortiert.
    """
    if not chunks:
        return []
    settings = get_settings()
    raw_scores = _cross_scores(
        query, [ch.get("content", "") for ch in chunks], settings.rerank_max_length
    )

    scored: List[Dict] = []
    for ch, raw in zip(chunks, raw_scores):
        item = dict(ch)
        item["score"] = round(_sigmoid(float(raw)), 4)
        scored.append(item)

    scored.sort(key=lambda c: c["score"], reverse=True)
    if top_pool is not None:
        scored = scored[:top_pool]
    return scored


def merge_by_source(ranked_chunks: List[Dict], chunks_per_source: int, max_results: int) -> List[Dict]:
    """Fasst rerankte Chunks pro Quelle zusammen -> eine result-Zeile je URL.

    - score der Quelle = hoechster Chunk-Score dieser Quelle.
    - content = die besten chunks_per_source Chunks, zusammengefuehrt.
    Rueckgabe: bis zu max_results Quellen, absteigend nach score.
    """
    by_url: Dict[str, Dict] = {}
    order: List[str] = []
    for ch in ranked_chunks:  # bereits nach score sortiert
        key = canonical_url(ch.get("url", ""))
        if key not in by_url:
            by_url[key] = {
                "url": ch.get("url", ""),
                "title": ch.get("title", ""),
                "chunks": [],
                "score": ch.get("score", 0.0),
                "full_text": ch.get("full_text", ""),
            }
            order.append(key)
        bucket = by_url[key]
        if len(bucket["chunks"]) < chunks_per_source:
            bucket["chunks"].append(ch.get("content", ""))
        if not bucket["full_text"] or len(ch.get("full_text", "")) > len(bucket["full_text"]):
            bucket["full_text"] = ch.get("full_text", "")
        bucket["score"] = max(bucket["score"], ch.get("score", 0.0))

    results: List[Dict] = []
    for key in order:
        b = by_url[key]
        results.append(
            {
                "url": b["url"],
                "title": b["title"],
                "content": " [...] ".join(b["chunks"]).strip(),
                "score": round(b["score"], 4),
                "raw_content": b["full_text"],
            }
        )
    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:max_results]
