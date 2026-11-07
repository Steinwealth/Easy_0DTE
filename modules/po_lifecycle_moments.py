"""Shared labels for mid-trade Priority Optimizer record snapshots.

Record-only. Used by the 20p writer and the mid-trade lifecycle snapshots so
chain overlay, event clocks, and canonical families stay on one path.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

CHAIN_REUSE_MAX_AGE_S = 60.0

# Existing 20p plus age_120s, first_red, pullback. 50p is retired (Oct 23, 2026).
MID_TRADE_PURPOSES = ("20p", "age_120s", "first_red", "pullback")
NEW_LIFECYCLE_PURPOSES = ("age_120s", "first_red", "pullback")
# One bounded hub refresh when the bar cache is older than the 90s contract.
# first_red stays cache-only so the monitor does not miss ticks.
SESSION_TECH_EVENT_MAX_AGE_S = 90.0
SESSION_TECH_REFRESH_PURPOSES = frozenset({"20p", "age_120s", "exit", "pullback"})
CHAIN_REFRESH_CAP = 2
CHAIN_REFRESH_PURPOSES = frozenset({"20p", "pullback", "exit"})

PURPOSE_TO_TECH_ASOF = {
    "20p": "mfe_20p",
    "age_120s": "age_120s",
    "first_red": "first_red",
    "pullback": "pullback",
}

PURPOSE_TO_FAMILY = {
    "20p": "mfe_20p",
    "age_120s": "age_120s",
    "first_red": "first_red",
    "pullback": "pullback",
}

PURPOSE_TO_STAGE_SUFFIX = {
    "20p": "options_20p",
    "age_120s": "options_age120",
    "first_red": "options_firstred",
    "pullback": "options_pullback",
}

# Check options_50p before a generic "20p" substring so older files never map to mfe_20p.
_STAGE_FAMILY = (
    ("options_50p", "mfe_50p"),
    ("options_age120", "age_120s"),
    ("options_firstred", "first_red"),
    ("options_pullback", "pullback"),
    ("options_20p", "mfe_20p"),
)

_LIVE_EVENTS = frozenset(
    ("mfe_20p", "age_120s", "first_red", "pullback")
)


def session_tech_may_refresh(purpose: Optional[str]) -> bool:
    """True when this event may refresh a stale session-tech cache.

    The hub still no-ops when the cache is already inside 90s. It does not
    skip a needed refresh to protect the monitor.
    """
    text = str(purpose or "").strip()
    if not text or text == "exit":
        return True
    return text in SESSION_TECH_REFRESH_PURPOSES


def chain_may_force_refresh(
    purpose: Optional[str],
    refresh_n: int = 0,
    cap: int = CHAIN_REFRESH_CAP,
) -> bool:
    """20p and pullback share the two-per-trade cap. Exit always refreshes."""
    text = str(purpose or "exit").strip() or "exit"
    if text == "exit":
        return True
    if text not in CHAIN_REFRESH_PURPOSES:
        return False
    try:
        used = int(refresh_n or 0)
    except (TypeError, ValueError):
        used = 0
    return used < int(cap)


def _parse_chain_clock(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def chain_age_s(
    row: Optional[Dict[str, Any]] = None,
    osnap: Optional[Dict[str, Any]] = None,
    *,
    now: Optional[datetime] = None,
) -> Optional[float]:
    """Seconds since the last stamped chain clock. None when no clock exists."""
    pools = [p for p in (row, osnap) if isinstance(p, dict)]
    clock = None
    for pool in pools:
        clock = (
            pool.get("po_chain_asof_at")
            or pool.get("chain_observed_at")
            or pool.get("tp_chain_observed_at")
        )
        if clock not in (None, ""):
            break
    src = _parse_chain_clock(clock)
    if src is None:
        return None
    now_dt = now if isinstance(now, datetime) else datetime.now(timezone.utc)
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)
    return max(0.0, (now_dt - src).total_seconds())


def chain_should_live_refresh(
    purpose: Optional[str],
    refresh_n: int = 0,
    cap: int = CHAIN_REFRESH_CAP,
    *,
    row: Optional[Dict[str, Any]] = None,
    osnap: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
    max_age_s: float = CHAIN_REUSE_MAX_AGE_S,
) -> bool:
    """Live chain only when the cap allows and the cache is older than 60s."""
    if not chain_may_force_refresh(purpose, refresh_n, cap):
        return False
    age = chain_age_s(row, osnap, now=now)
    if age is not None and age <= float(max_age_s):
        return False
    return True


def is_mid_trade_purpose(purpose: Optional[str]) -> bool:
    return str(purpose or "").strip() in MID_TRADE_PURPOSES


def is_new_lifecycle_purpose(purpose: Optional[str]) -> bool:
    return str(purpose or "").strip() in NEW_LIFECYCLE_PURPOSES


def tech_asof_for_purpose(purpose: Optional[str]) -> str:
    return PURPOSE_TO_TECH_ASOF.get(str(purpose or "").strip(), "exit")


def family_for_purpose(purpose: Optional[str]) -> str:
    return PURPOSE_TO_FAMILY.get(str(purpose or "").strip(), "")


def event_clock_key(purpose: Optional[str]) -> str:
    p = str(purpose or "").strip()
    if p == "20p":
        return "po_20p_event_at"
    if p == "pullback":
        return "po_pullback_event_at"
    if p in NEW_LIFECYCLE_PURPOSES:
        return f"po_{p}_event_at"
    return "po_exit_event_at"


def family_from_stage(stage: Optional[str]) -> str:
    text = str(stage or "").lower()
    for marker, family in _STAGE_FAMILY:
        if marker in text:
            return family
    if "20p" in text:
        return "mfe_20p"
    return ""


def is_lifecycle_moment(
    purpose: Optional[str] = None,
    stage: Optional[str] = None,
    event: Optional[str] = None,
    po_tech_asof: Optional[str] = None,
) -> bool:
    """True for 20p and the mid-trade records (not a true exit)."""
    if is_mid_trade_purpose(purpose):
        return True
    moment = str(event or po_tech_asof or "").strip().lower()
    if moment in _LIVE_EVENTS:
        return True
    text = str(stage or "").lower()
    return bool(family_from_stage(text))


def is_live_chain_moment(
    *,
    is_exit: bool = False,
    purpose: Optional[str] = None,
    stage: Optional[str] = None,
    event: Optional[str] = None,
    po_tech_asof: Optional[str] = None,
) -> bool:
    """Exit or a mid-trade record: use the event-time chain, not the gate row."""
    return bool(is_exit) or is_lifecycle_moment(
        purpose=purpose, stage=stage, event=event, po_tech_asof=po_tech_asof
    )
