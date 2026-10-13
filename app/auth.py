"""Gemeinsamer Key-Check fuer alle Endpunkte.

Aktiv nur, wenn SERVICE_API_KEY gesetzt ist. Akzeptiert die Header-Varianten
der nachgebauten Dienste, damit deren Clients ohne Umbau funktionieren:
Authorization: Bearer (Tavily, Jina), x-api-key (Exa), X-API-KEY (Serper).
"""

from __future__ import annotations

import hmac
from typing import Optional

from fastapi import HTTPException, Request

from .config import get_settings


def provided_key(request: Request, body_key: Optional[str] = None) -> Optional[str]:
    if body_key:
        return body_key
    auth = request.headers.get("authorization")
    if auth:
        parts = auth.split(" ", 1)
        return parts[1].strip() if len(parts) == 2 else auth.strip()
    return request.headers.get("x-api-key")


def require_key(request: Request, body_key: Optional[str] = None) -> None:
    expected = get_settings().service_api_key
    if not expected:
        return
    given = provided_key(request, body_key) or ""
    if not hmac.compare_digest(given.encode(), expected.encode()):
        raise HTTPException(status_code=401, detail="Ungueltiger oder fehlender API-Key.")
