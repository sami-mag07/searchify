"""Zentrale Konfiguration. Liest AUSSCHLIESSLICH aus Umgebungsvariablen.

Keine Secrets im Code - alle Keys/URLs kommen aus der Umgebung bzw. aus einer
nicht eingecheckten `.env`-Datei (siehe .env.example, .gitignore).
"""

from __future__ import annotations

import os
from dataclasses import dataclass


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _get_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # Such-Backend: searxng (self-host, Default) | serper (Google-SERP-API) | ddgs
    search_backend: str
    search_fallback: str
    searxng_url: str
    serper_api_key: str
    search_hedge_ms: int

    # Antwort-LLM (Claude ueber claude-code-api, OpenAI-kompatibel)
    openai_base_url: str
    openai_api_key: str
    openai_model: str

    # Reranker (lokaler Cross-Encoder ueber transformers)
    reranker_model: str
    reranker_use_fp16: bool

    # Schutz des eigenen Endpunkts (optional)
    service_api_key: str

    # Pipeline-Tuning
    candidate_pool: int
    chunk_size: int
    chunk_overlap: int
    fetch_timeout: int
    fetch_budget_ms: int
    fetch_budget_advanced_ms: int
    page_cache_size: int
    page_cache_ttl_s: int
    rerank_chunks_per_page: int
    rerank_max_length: int


def get_settings() -> Settings:
    """Settings frisch aus der Umgebung lesen (kein Caching, test-freundlich)."""
    return Settings(
        search_backend=_get("SEARCH_BACKEND", "searxng").strip().lower(),
        search_fallback=_get("SEARCH_FALLBACK", "").strip().lower(),
        searxng_url=_get("SEARXNG_URL", "http://localhost:8888").rstrip("/"),
        serper_api_key=_get("SERPER_API_KEY", ""),
        search_hedge_ms=_get_int("SEARCH_HEDGE_MS", 900),
        openai_base_url=_get("OPENAI_BASE_URL", "http://127.0.0.1:8080/v1").rstrip("/"),
        openai_api_key=_get("OPENAI_API_KEY", ""),
        openai_model=_get("OPENAI_MODEL", "opus"),
        reranker_model=_get("RERANKER_MODEL", "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"),
        reranker_use_fp16=_get_bool("RERANKER_USE_FP16", True),
        service_api_key=_get("SERVICE_API_KEY", ""),
        candidate_pool=_get_int("CANDIDATE_POOL", 10),
        chunk_size=_get_int("CHUNK_SIZE", 100),
        chunk_overlap=_get_int("CHUNK_OVERLAP", 20),
        fetch_timeout=_get_int("FETCH_TIMEOUT", 5),
        fetch_budget_ms=_get_int("FETCH_BUDGET_MS", 300),
        fetch_budget_advanced_ms=_get_int("FETCH_BUDGET_ADVANCED_MS", 1500),
        page_cache_size=_get_int("PAGE_CACHE_SIZE", 5000),
        page_cache_ttl_s=_get_int("PAGE_CACHE_TTL_S", 6 * 3600),
        rerank_chunks_per_page=_get_int("RERANK_CHUNKS_PER_PAGE", 3),
        rerank_max_length=_get_int("RERANK_MAX_LENGTH", 256),
    )
