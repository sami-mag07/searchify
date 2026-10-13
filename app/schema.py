"""Pydantic-Schemas fuer Request und Response.

Die Feld- und Parameter-Namen sind an die oeffentlich dokumentierte
Tavily-/search-Schnittstelle angelehnt (Namen/Schnittstellen sind
urheberrechtlich frei). Die Pipeline dahinter ist komplett eigen.
"""

from __future__ import annotations

from typing import Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, field_validator


class SearchRequest(BaseModel):
    """Eingabe fuer POST /search (Tavily-kompatible Teilmenge)."""

    query: str = Field(..., min_length=1, description="Suchanfrage (Freitext).")

    # Tiefe der Pipeline. Eigene Semantik, Namen angelehnt an die Doku.
    search_depth: str = Field(
        "basic",
        description="ultra-fast | fast | basic | advanced (eigene Umsetzung).",
    )
    topic: str = Field("general", description="general | news (eigene Umsetzung).")

    max_results: int = Field(5, ge=1, le=20, description="Anzahl Ergebnisse (Top-k).")
    chunks_per_source: int = Field(
        3, ge=1, le=5, description="Max. Chunks, die pro Quelle zusammengefuehrt werden."
    )

    include_answer: Union[bool, Literal["basic", "advanced"]] = Field(
        False,
        description="Wie bei Tavily: false (Standard), true/basic/advanced erzeugen einen "
        "LLM-answer aus den Top-Quellen. Ohne erreichbares LLM bleibt answer null "
        "(Grund in usage.answer_error), die Treffer kommen trotzdem.",
    )
    include_raw_content: bool = Field(
        False, description="Wenn true, kommt der extrahierte Volltext je Quelle mit."
    )
    include_links: bool = Field(
        False,
        description="Wenn true, kommen die Links jeder geladenen Seite mit, "
        "Aktions-Links (Anmeldung, Bewerbung, Formular) zuerst.",
    )

    @field_validator("query")
    @classmethod
    def _query_not_blank(cls, v: str) -> str:
        # Nur Leerraum: sonst geht eine leere Anfrage an alle Engines und Backends
        # und endet nach Sekunden als 502 (Zuverlaessigkeits-Bench 13.10.2026).
        if not v.strip():
            raise ValueError("query darf nicht leer sein")
        return v

    # Domain- und Sprachsteuerung (eigener DE/EU-Fokus als Mehrwert)
    include_domains: List[str] = Field(default_factory=list)
    exclude_domains: List[str] = Field(default_factory=list)
    language: Optional[str] = Field(
        None, description="en | de | es | fr; ohne Angabe wird die Sprache der Frage erkannt."
    )
    country: Optional[str] = Field(None, description="Laendercode fuer SearXNG.")
    time_range: Optional[str] = Field(
        None, description="day | week | month | year (an SearXNG durchgereicht)."
    )

    # Optionaler Auth-Key (Alternative zum Authorization-Header).
    api_key: Optional[str] = Field(
        None, description="Nur noetig, wenn der Dienst mit SERVICE_API_KEY geschuetzt ist."
    )


class Link(BaseModel):
    text: str
    url: str


class SearchResult(BaseModel):
    title: str
    url: str
    content: str = Field(..., description="Rerankter, zusammengefuehrter Snippet-Text.")
    score: float = Field(..., description="Normalisierter Reranker-Score 0..1 (eigene Skala).")
    raw_content: Optional[str] = Field(
        None, description="Nur bei include_raw_content: extrahierter Volltext."
    )
    links: Optional[List[Link]] = Field(
        None,
        description="Nur bei include_links: Links der Seite (leer, wenn nicht geladen). "
        "Ungeprueft von fremden Seiten uebernommen; vor dem Abrufen selbst pruefen "
        "(koennen auf interne Adressen oder Phishing zeigen).",
    )


class Usage(BaseModel):
    """Verbrauchs-/Diagnosedaten (eigene Felder, kein Credit-Modell)."""

    sources_found: int = 0
    sources_extracted: int = 0
    chunks_ranked: int = 0
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    answer_error: Optional[str] = Field(
        None, description="Warum answer trotz include_answer fehlt (kein LLM konfiguriert, "
        "LLM nicht erreichbar)."
    )
    timings_ms: Dict[str, int] = Field(
        default_factory=dict, description="Dauer je Stufe (search, rank, answer) in ms."
    )


class SearchResponse(BaseModel):
    query: str
    answer: Optional[str] = None
    results: List[SearchResult] = Field(default_factory=list)
    response_time: float = Field(..., description="Dauer der Anfrage in Sekunden.")
    request_id: str
    usage: Optional[Usage] = None
