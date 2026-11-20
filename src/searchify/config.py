"""Configuration, read exclusively from environment variables.

No secrets in code: every key and URL comes from the environment or a local,
untracked ``.env`` file (see ``.env.example`` and ``.gitignore``).
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _get(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _get_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if not raw or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    # Search: local-first. "duckduckgo" (keyless) or "searxng" (self-hosted).
    # "serper" is an optional cloud fallback, used only if its key is set.
    search_backend: str
    search_fallback: str
    searxng_url: str
    serper_api_key: str
    # Tavily: last safety net for search and reading, only if its key is set.
    tavily_api_key: str

    # Reranker: a local cross-encoder loaded by Hugging Face name, or a local path.
    reranker_model: str

    # Networking
    fetch_timeout: int
    # Operator-provided endpoint for the optional Parallel search backend.
    parallel_mcp_url: str = ""


def get_settings() -> Settings:
    """Read settings fresh from the environment (no caching, test friendly)."""
    return Settings(
        search_backend=_get("SEARCH_BACKEND", "duckduckgo"),
        search_fallback=_get("SEARCH_FALLBACK", "searxng"),
        searxng_url=_get("SEARXNG_URL", ""),
        serper_api_key=_get("SERPER_API_KEY", ""),
        tavily_api_key=_get("TAVILY_API_KEY", ""),
        reranker_model=_get("RERANKER_MODEL", "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"),
        fetch_timeout=_get_int("FETCH_TIMEOUT", 15),
        parallel_mcp_url=_get("PARALLEL_MCP_URL").strip(),
    )
# Stand: 24.10.2026
