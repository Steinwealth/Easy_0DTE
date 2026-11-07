"""Shared execution-signal TTL for Trendline / Breakout capital-availability waits.

Once a TL/BO trade is quality-approved and ready-to-execute, it may wait
briefly for slots/capital under ``0DTE_EXEC_SIGNAL_EXPIRE_MINUTES`` (default 1).
After that the signal expires:

  - Trendline → current TL1/TL2/TL3 candidate reject; sibling candidates stay open
  - Breakout  → current ready attempt expires and re-arms for future breaks

Path overrides (optional):
  TRENDLINE_EXEC_SIGNAL_EXPIRE_MINUTES
  BREAKOUT_EXEC_SIGNAL_EXPIRE_MINUTES
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

log = logging.getLogger(__name__)

EXEC_READY_AT_KEY = "exec_ready_at"
EXEC_READY_TTL_REASON = "exec_ready_ttl_expired"
# Oct 13 2026: with EXEC_READY_TTL_RETIRE_NO_COOLDOWN=true a TTL expiry (capital/slot
# wait, never sent to the executor) retires only the current break and re-arms the
# line for the next break. No 1h slot/side cooldown. Label below.
EXEC_READY_TTL_RETIRE_REASON = "exec_ready_ttl_retired"
EXEC_READY_TTL_RETIRE_FLAG = "EXEC_READY_TTL_RETIRE_NO_COOLDOWN"


def ttl_retire_no_cooldown_enabled() -> bool:
    import os

    raw = os.getenv(EXEC_READY_TTL_RETIRE_FLAG)
    if raw is None:
        try:
            from modules.config_loader import get_config_value

            raw = get_config_value(EXEC_READY_TTL_RETIRE_FLAG, "false")
        except Exception:
            raw = "false"
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def is_exec_ready_ttl_reason(reason: Any) -> bool:
    r = str(reason or "").strip()
    return r == EXEC_READY_TTL_REASON or r.startswith(EXEC_READY_TTL_RETIRE_REASON)
DEFAULT_EXEC_SIGNAL_EXPIRE_MINUTES = 1.0

_PATH_OVERRIDE_KEYS = {
    "trendline": "TRENDLINE_EXEC_SIGNAL_EXPIRE_MINUTES",
    "tl": "TRENDLINE_EXEC_SIGNAL_EXPIRE_MINUTES",
    "breakout": "BREAKOUT_EXEC_SIGNAL_EXPIRE_MINUTES",
    "bo": "BREAKOUT_EXEC_SIGNAL_EXPIRE_MINUTES",
}


def _as_aware_utc(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            # Accept ISO strings; treat trailing Z as UTC.
            if raw.endswith("Z"):
                raw = raw[:-1] + "+00:00"
            dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            return None
    return None


def exec_signal_expire_minutes(path: str = "") -> float:
    """Minutes an exec-ready signal may wait for a slot/fill before expiring."""
    try:
        from modules.config_loader import get_config_value
    except Exception:
        get_config_value = None  # type: ignore

    def _read(key: str, default: float) -> float:
        if get_config_value is None:
            return default
        try:
            raw = get_config_value(key, default)
        except Exception:
            return default
        try:
            return float(raw)
        except (TypeError, ValueError):
            return default

    shared = _read("0DTE_EXEC_SIGNAL_EXPIRE_MINUTES", DEFAULT_EXEC_SIGNAL_EXPIRE_MINUTES)
    path_key = _PATH_OVERRIDE_KEYS.get(str(path or "").strip().lower())
    if not path_key:
        return max(0.0, shared)
    # Path override only when explicitly set (non-empty); else shared default.
    if get_config_value is None:
        return max(0.0, shared)
    try:
        raw = get_config_value(path_key, None)
    except Exception:
        raw = None
    if raw is None or str(raw).strip() == "":
        return max(0.0, shared)
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return max(0.0, shared)


def stamp_exec_ready_at(
    store: Any,
    *,
    now: Optional[datetime] = None,
    key: str = EXEC_READY_AT_KEY,
) -> Optional[datetime]:
    """Stamp first ready-to-execute time on a dict-like metadata store.

    Preserves an existing stamp (slot-defer / rearm must not reset the clock).
    Returns the effective stamp, or None if ``store`` is not a dict.
    """
    if not isinstance(store, dict):
        return None
    existing = _as_aware_utc(store.get(key))
    if existing is not None:
        return existing
    stamp = now or datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    store[key] = stamp
    return stamp


def read_exec_ready_at(store: Any, *, key: str = EXEC_READY_AT_KEY) -> Optional[datetime]:
    if isinstance(store, dict):
        return _as_aware_utc(store.get(key))
    return _as_aware_utc(getattr(store, key, None))


def exec_ready_age_sec(
    store: Any,
    *,
    now: Optional[datetime] = None,
    key: str = EXEC_READY_AT_KEY,
    ready_at: Optional[datetime] = None,
) -> Optional[float]:
    stamped = ready_at or read_exec_ready_at(store, key=key)
    if stamped is None:
        return None
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        clock = clock.replace(tzinfo=timezone.utc)
    return max(0.0, (clock - stamped).total_seconds())


def is_exec_ready_expired(
    store: Any = None,
    *,
    path: str = "",
    now: Optional[datetime] = None,
    expire_minutes: Optional[float] = None,
    ready_at: Optional[datetime] = None,
    key: str = EXEC_READY_AT_KEY,
) -> bool:
    """True when an exec-ready stamp is older than the configured TTL.

    ``expire_minutes <= 0`` disables expiry.
    Missing stamp → not expired (caller must stamp when entering ready).
    """
    minutes = (
        float(expire_minutes)
        if expire_minutes is not None
        else exec_signal_expire_minutes(path)
    )
    if minutes <= 0:
        return False
    age = exec_ready_age_sec(store, now=now, key=key, ready_at=ready_at)
    if age is None:
        return False
    return age > minutes * 60.0


def log_exec_ready_ttl_expired(
    *,
    path: str,
    symbol: str,
    age_sec: Optional[float],
    expire_minutes: float,
    extra: str = "",
) -> None:
    log.warning(
        "EXEC_SIGNAL_TTL | path=%s | symbol=%s | reason=%s | age_sec=%.1f | "
        "expire_min=%.1f%s",
        str(path or "").strip().lower(),
        str(symbol or "").strip().upper(),
        EXEC_READY_TTL_REASON,
        float(age_sec or 0.0),
        float(expire_minutes),
        f" | {extra}" if extra else "",
    )
