"""Shared helper for the example workflows. Base URL and key come from the environment."""
import os
import sys

import httpx

BASE = os.environ.get("SEARCH_API_URL", "http://127.0.0.1:8000").rstrip("/")
KEY = os.environ.get("SERVICE_API_KEY", "")


def call(method, path, **kw):
    headers = {"Authorization": f"Bearer {KEY}"} if KEY else {}
    r = httpx.request(method, BASE + path, headers=headers, timeout=60, **kw)
    r.raise_for_status()
    return r


def arg(usage):
    if len(sys.argv) < 2:
        sys.exit(f"usage: {usage}")
    return " ".join(sys.argv[1:])


def short(text, n=160):
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"
