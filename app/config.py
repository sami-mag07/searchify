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


def _get_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw.replace(",", "."))
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
    split_engines: str
    split_per_engine: int
    split_search_timeout_ms: int
    split_tail_ms: int
    split_hedge_ms: int
    split_pages_wide: int
    serper_api_key: str
    search_hedge_ms: int
    search_cache_ttl_s: int

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
    fetch_client: str
    extract_workers: int
    fetch_budget_ms: int
    fetch_budget_advanced_ms: int
    page_cache_size: int
    page_cache_ttl_s: int
    doc_cache_max_mb: int
    rerank_chunks_per_page: int
    rerank_max_length: int
    score_temperature: float
    results_per_domain: int
    fusion_weight: float
    fusion_k: int
    fusion_trust_min: float
    source_prior: bool
    language_mismatch_penalty: float
    quality_prior: bool
    quality_prior_weight: float
    quality_prior_compat: bool
    min_result_score: float
    min_results_kept: int


def get_settings() -> Settings:
    """Settings frisch aus der Umgebung lesen (kein Caching, test-freundlich)."""
    return Settings(
        search_backend=_get("SEARCH_BACKEND", "searxng").strip().lower(),
        search_fallback=_get("SEARCH_FALLBACK", "").strip().lower(),
        searxng_url=_get("SEARXNG_URL", "http://localhost:8888").rstrip("/"),
        split_engines=_get("SEARXNG_SPLIT_ENGINES", "").strip(),
        split_per_engine=_get_int("SPLIT_PER_ENGINE", 7),
        split_search_timeout_ms=_get_int("SPLIT_SEARCH_TIMEOUT_MS", 2500),
        split_hedge_ms=_get_int("SPLIT_HEDGE_MS", 1000),
        split_pages_wide=_get_int("SPLIT_PAGES_WIDE", 3),
        split_tail_ms=_get_int("SPLIT_TAIL_MS", 120),
        serper_api_key=_get("SERPER_API_KEY", ""),
        search_hedge_ms=_get_int("SEARCH_HEDGE_MS", 900),
        search_cache_ttl_s=_get_int("SEARCH_CACHE_TTL_S", 3600),
        openai_base_url=_get("OPENAI_BASE_URL", "http://127.0.0.1:8080/v1").rstrip("/"),
        openai_api_key=_get("OPENAI_API_KEY", ""),
        openai_model=_get("OPENAI_MODEL", "opus"),
        reranker_model=_get("RERANKER_MODEL", "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"),
        reranker_use_fp16=_get_bool("RERANKER_USE_FP16", True),
        service_api_key=_get("SERVICE_API_KEY", ""),
        candidate_pool=_get_int("CANDIDATE_POOL", 10),
        chunk_size=_get_int("CHUNK_SIZE", 50),
        chunk_overlap=_get_int("CHUNK_OVERLAP", 12),
        fetch_timeout=_get_int("FETCH_TIMEOUT", 5),
        fetch_client=_get("FETCH_CLIENT", "httpx").strip().lower(),
        extract_workers=_get_int("EXTRACT_WORKERS", 0),
        fetch_budget_ms=_get_int("FETCH_BUDGET_MS", 500),
        fetch_budget_advanced_ms=_get_int("FETCH_BUDGET_ADVANCED_MS", 1500),
        page_cache_size=_get_int("PAGE_CACHE_SIZE", 5000),
        page_cache_ttl_s=_get_int("PAGE_CACHE_TTL_S", 6 * 3600),
        doc_cache_max_mb=_get_int("DOC_CACHE_MAX_MB", 400),
        rerank_chunks_per_page=_get_int("RERANK_CHUNKS_PER_PAGE", 2),
        rerank_max_length=_get_int("RERANK_MAX_LENGTH", 256),
        score_temperature=_get_float("SCORE_TEMPERATURE", 4.0),
        results_per_domain=_get_int("RESULTS_PER_DOMAIN", 1),
        fusion_weight=_get_float("FUSION_WEIGHT", 0.5),
        fusion_k=_get_int("FUSION_K", 30),
        fusion_trust_min=_get_float("FUSION_TRUST_MIN", 0.0),
        source_prior=_get_bool("SOURCE_PRIOR", True),
        language_mismatch_penalty=_get_float("LANGUAGE_MISMATCH_PENALTY", 2.0),
        quality_prior=_get_bool("QUALITY_PRIOR", True),
        quality_prior_weight=_get_float("QUALITY_PRIOR_WEIGHT", 1.0),
        # Blindtest 13.10.: hilft /search, kostet Serper/Exa im Testteil -> dort aus
        quality_prior_compat=_get_bool("QUALITY_PRIOR_COMPAT", False),
        # Echte Aufgabe 13.10.: Woerterbuecher/Shops mit gleichem Wort lagen bei score ~0.07-0.11
        min_result_score=_get_float("MIN_RESULT_SCORE", 0.12),
        min_results_kept=_get_int("MIN_RESULTS_KEPT", 3),
    )
