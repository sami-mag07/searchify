"""How far one request may go: the load ladder cap, the budget and the cloud switch.

The effective stage is the lowest of three things: what is installed and switched
on by the environment (every rung still checks that itself), the operator's cap
``SEARCHIFY_MAX_STAGE`` and the wish of the request (or, for the CLI and the
Python API, the user profile). So ``READER_STEALTH=1`` can never override a wish
of ``plain``, and a rung that is not installed is never offered.

Stages, low to high: ``plain`` (httpx only), ``tls`` (plus the Chrome-TLS
fallback), ``browser`` (plus the shared and the direct browser), ``stealth``
(plus Camoufox). ``cloud_fallback`` (Tavily, costs credits) is its own switch.
Budgets: ``normal`` is exactly the old behaviour, ``low`` trims each delivered
page and shortens timeouts, ``high`` lengthens timeouts.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Dict, Mapping, Optional, Tuple

log = logging.getLogger(__name__)

STAGES = ("plain", "tls", "browser", "stealth")
BUDGETS = ("low", "normal", "high")
NO_LIMIT = STAGES[-1]  # the top rung: everything the environment allows
DEFAULT_BUDGET = "normal"

LOW_CHARS = 4000  # budget low: each delivered page is cut to this many characters
TIMEOUT_SCALE = {"low": 0.6, "normal": 1.0, "high": 2.0}


def _check(value: Optional[str], allowed: Tuple[str, ...], name: str) -> Optional[str]:
    if value is None:
        return None
    clean = str(value).strip().lower()
    if clean not in allowed:
        raise ValueError(f"unknown {name} {value!r}, use {', '.join(allowed)}")
    return clean


def operator_cap() -> Optional[str]:
    """``SEARCHIFY_MAX_STAGE`` or None. A value that is not a stage is a typo in a
    safety setting, so it fails closed to ``plain`` instead of lifting the cap."""
    raw = os.environ.get("SEARCHIFY_MAX_STAGE", "").strip().lower()
    if not raw:
        return None
    if raw in STAGES:
        return raw
    log.warning("SEARCHIFY_MAX_STAGE=%r is not one of %s, treating it as plain", raw, ", ".join(STAGES))
    return "plain"


@dataclass(frozen=True)
class Limits:
    stage: str = NO_LIMIT
    budget: str = DEFAULT_BUDGET
    cloud: bool = True  # Tavily may run if its key is set (today's behaviour)

    def allows(self, stage: str) -> bool:
        """True if `stage` is at or below the effective stage."""
        return STAGES.index(stage) <= STAGES.index(self.stage)

    @property
    def scale(self) -> float:
        return TIMEOUT_SCALE[self.budget]

    def scaled(self, seconds: float) -> float:
        """A timeout adjusted to the budget; unchanged for ``normal``."""
        return seconds if self.budget == DEFAULT_BUDGET else seconds * self.scale

    @property
    def max_chars(self) -> Optional[int]:
        return LOW_CHARS if self.budget == "low" else None


def detail(
    max_stage: Optional[str] = None,
    budget: Optional[str] = None,
    cloud_fallback: Optional[bool] = None,
    profile: Optional[Mapping[str, object]] = None,
) -> Dict[str, Tuple[object, str]]:
    """Every setting with the place it came from: ``{name: (value, source)}``.

    Request beats profile beats default; the stage is then capped by the operator
    (source ``env``). Raises ValueError for a stage or budget that does not exist."""
    profile = profile or {}
    wish_stage, stage_src = _check(max_stage, STAGES, "max_stage"), "request"
    if wish_stage is None:
        wish_stage, stage_src = _check(profile.get("max_stage"), STAGES, "max_stage"), "profile"  # type: ignore[arg-type]
    if wish_stage is None:
        wish_stage, stage_src = NO_LIMIT, "default"
    cap = operator_cap()
    if cap is not None and STAGES.index(cap) < STAGES.index(wish_stage):
        wish_stage, stage_src = cap, "env"
    wish_budget, budget_src = _check(budget, BUDGETS, "budget"), "request"
    if wish_budget is None:
        wish_budget, budget_src = _check(profile.get("budget"), BUDGETS, "budget"), "profile"  # type: ignore[arg-type]
    if wish_budget is None:
        wish_budget, budget_src = DEFAULT_BUDGET, "default"
    cloud = (True, "default") if cloud_fallback is None else (bool(cloud_fallback), "request")
    return {"max_stage": (wish_stage, stage_src), "budget": (wish_budget, budget_src),
            "cloud_fallback": cloud}


def resolve(
    max_stage: Optional[str] = None,
    budget: Optional[str] = None,
    cloud_fallback: Optional[bool] = None,
    profile: Optional[Mapping[str, object]] = None,
) -> Limits:
    """The limits for one request (see `detail` for the order of precedence)."""
    got = detail(max_stage, budget, cloud_fallback, profile)
    return Limits(stage=str(got["max_stage"][0]), budget=str(got["budget"][0]),
                  cloud=bool(got["cloud_fallback"][0]))


def trim_text(text: str, max_chars: int) -> str:
    """Cut at a word boundary, like passages.trim."""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    if not text[max_chars].isspace():
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip() + " …"
