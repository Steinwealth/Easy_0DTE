"""Required-decision classification for the post-hydration gate pass.

Classifications drive admission. They are not a shadow log.

VALID            compare the number
DATA_ERROR       the producer should have supplied a finite number
NOT_READY        the clock has not elapsed (or the chain fetch was yielded)
NON_APPLICABLE   other side, disabled gate, or research field
"""

from __future__ import annotations

import math
from typing import Any, Optional, Sequence, Tuple

VALID = "VALID"
NOT_READY = "NOT_READY"
DATA_ERROR = "DATA_ERROR"
NON_APPLICABLE = "NON_APPLICABLE"

POCKET_TRUE = "TRUE"
POCKET_FALSE = "FALSE"
POCKET_UNKNOWN = "UNKNOWN"
POCKET_ERROR = "DATA_ERROR"

# Bar windows already used by the session indicators. Not new strategy cuts.
PVE_MIN_BARS = 3
RSI_MIN_BARS = 14
VWAP_MIN_BARS = 1
ATR_MIN_BARS = 14
VOLUME_RATIO_MIN_BARS = 2


def finite_float(value: Any) -> Tuple[str, Optional[float]]:
    """MISSING, DATA_ERROR, or VALID. Zero is VALID."""
    if value is None or value == "":
        return "MISSING", None
    if isinstance(value, bool):
        return DATA_ERROR, None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return DATA_ERROR, None
    if not math.isfinite(number):
        return DATA_ERROR, None
    return VALID, number


def session_bar_count(ctx: Any) -> Optional[int]:
    for src in (getattr(ctx, "extras", None), getattr(ctx, "signal", None)):
        if not isinstance(src, dict):
            continue
        raw = src.get("session_bar_count")
        if raw is None or raw == "":
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None
    return None


def clock_ready(bar_count: Optional[int], minimum: int) -> Optional[bool]:
    """None means the bar clock was not stamped."""
    if bar_count is None:
        return None
    return int(bar_count) >= int(minimum)


def missing_after_clock(bar_count: Optional[int], minimum: int) -> str:
    """A missing derived value. Unknown clock stays NOT_READY."""
    ready = clock_ready(bar_count, minimum)
    if ready is True:
        return DATA_ERROR
    return NOT_READY


def decision_hydrated(ctx: Any) -> bool:
    """The hydration sequence for this attempt finished.

    True means stamp + chain attempt were recorded before GateContext.
    It does not mean every required value is VALID. Readiness still
    classifies each key as VALID, NOT_READY, DATA_ERROR, or NON_APPLICABLE.
    """
    for src in (getattr(ctx, "extras", None), getattr(ctx, "signal", None)):
        if isinstance(src, dict) and src.get("decision_hydrated"):
            return True
    return False


def chain_attempt(ctx: Any) -> str:
    for src in (getattr(ctx, "extras", None), getattr(ctx, "signal", None)):
        if isinstance(src, dict) and src.get("chain_attempt"):
            return str(src.get("chain_attempt"))
    return ""


def and_pocket(leg_states: Sequence[str], leg_matches: Sequence[bool]) -> str:
    """Policy C. A VALID leg that is already false ends the pocket."""
    pairs = list(zip(leg_states, leg_matches))
    if any(state == VALID and not matched for state, matched in pairs):
        return POCKET_FALSE
    if any(state == DATA_ERROR for state, _matched in pairs):
        return POCKET_ERROR
    if any(state == NOT_READY for state, _matched in pairs):
        return POCKET_UNKNOWN
    if pairs and all(state == VALID and matched for state, matched in pairs):
        return POCKET_TRUE
    return POCKET_FALSE


def single_pocket(state: str, matched: bool) -> str:
    if state == DATA_ERROR:
        return POCKET_ERROR
    if state == NOT_READY:
        return POCKET_UNKNOWN
    if state == NON_APPLICABLE:
        return POCKET_FALSE
    if state == VALID and matched:
        return POCKET_TRUE
    return POCKET_FALSE
