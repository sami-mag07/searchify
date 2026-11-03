"""Local cross-encoder reranking.

Scores a query against each candidate text with a small, fast cross-encoder and
reorders the candidates best-first. The default model is a public MiniLM, loaded
from Hugging Face on first use; nothing is bundled.

Set ``RERANKER_MODEL`` to a different Hugging Face name, or to a local directory,
to plug in your own fine-tuned reranker.

Needs the optional extra: ``pip install "searchify[rerank]"``.
"""
from __future__ import annotations

import threading
from typing import Dict, List, Optional, Sequence

from .config import get_settings

_model = None
_lock = threading.Lock()


def _load():
    global _model
    if _model is None:
        with _lock:
            if _model is None:
                try:
                    from sentence_transformers import CrossEncoder
                except ImportError as exc:  # pragma: no cover - optional dependency
                    raise ImportError(
                        'reranking needs the extra: pip install "searchify[rerank]"'
                    ) from exc
                _model = CrossEncoder(get_settings().reranker_model)
    return _model


def score(query: str, texts: Sequence[str]) -> List[float]:
    """Relevance score per text for the query (higher is more relevant)."""
    if not texts:
        return []
    model = _load()
    # no progress bar: in pipes and agents it would only be noise on stderr
    pairs = [(query, t or "") for t in texts]
    return [float(s) for s in model.predict(pairs, show_progress_bar=False)]


def rerank(query: str, items: List[Dict], *, key: str = "content",
           top_k: Optional[int] = None) -> List[Dict]:
    """Reorder a list of result dicts best-first by relevance to the query.

    `key` is the field holding the text to score (falls back to content/snippet/title).
    Each returned item gets a `rerank_score`. `top_k` keeps only the best n.
    """
    if not items:
        return []
    texts = [it.get(key) or it.get("content") or it.get("snippet") or it.get("title") or ""
             for it in items]
    scores = score(query, texts)
    ranked = sorted(
        (dict(it, rerank_score=s) for it, s in zip(items, scores)),
        key=lambda it: -it["rerank_score"],
    )
    return ranked[:top_k] if top_k else ranked
# Stand: 24.10.2026
