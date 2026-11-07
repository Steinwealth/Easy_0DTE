#!/usr/bin/env python3
"""Recording window for Profit Optimizer premium paths.

Live recording always runs through session EOD flatten start
(``EOD_FLATTEN_START_PT``, 12:50 PT) on the entry's PT date.
After exit:
  * trades closed in profit keep shadow recording through that EOD deadline
  * trades closed at a loss or flat skip shadow when
    ``PROFIT_OPTIMIZER_LOSS_SHADOW_SECONDS`` is ``0`` (default)
  * a positive loss-shadow window (legacy 30 min) is still honored when set

``PROFIT_OPTIMIZER_RECORDING_WINDOW_SECONDS`` is fallback only if the session
clock cannot be imported.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from profit_optimizer.audit import parse_timestamp_utc


DEFAULT_RECORDING_WINDOW_SECONDS = 3 * 60 * 60
DEFAULT_LOSS_SHADOW_SECONDS = 0.0
# 0 = every profitable leftover (matches Shared.env / deploy pins). A positive
# default silently starved Sep 11 winners outside the top-N MFE ranked set.
DEFAULT_MAX_SHADOW_CONTINUATIONS = 0


def recording_window_seconds() -> float:
    try:
        from modules.config_loader import get_config_value

        raw = get_config_value(
            "PROFIT_OPTIMIZER_RECORDING_WINDOW_SECONDS",
            str(DEFAULT_RECORDING_WINDOW_SECONDS),
        )
        return max(60.0, float(str(raw).strip()))
    except Exception:
        return float(DEFAULT_RECORDING_WINDOW_SECONDS)


def loss_shadow_seconds() -> float:
    try:
        from modules.config_loader import get_config_value

        raw = get_config_value(
            "PROFIT_OPTIMIZER_LOSS_SHADOW_SECONDS",
            str(DEFAULT_LOSS_SHADOW_SECONDS),
        )
        return max(0.0, float(str(raw).strip()))
    except Exception:
        return float(DEFAULT_LOSS_SHADOW_SECONDS)


def max_shadow_continuations() -> int:
    """Cap post-exit OCC quotes. ``0`` means unlimited. Ranked by MFE, then P&L."""
    try:
        from modules.config_loader import get_config_value

        raw = get_config_value(
            "PROFIT_OPTIMIZER_MAX_SHADOW_CONTINUATIONS",
            str(DEFAULT_MAX_SHADOW_CONTINUATIONS),
        )
        return max(0, int(float(str(raw).strip() or DEFAULT_MAX_SHADOW_CONTINUATIONS)))
    except Exception:
        return int(DEFAULT_MAX_SHADOW_CONTINUATIONS)


def session_eod_recording_deadline_utc(entry: datetime) -> Optional[datetime]:
    """UTC instant of ``EOD_FLATTEN_START_PT`` on the entry's Pacific date."""
    try:
        from modules.session_clock import eod_flatten_window_pt, tz_pt
    except Exception:
        try:
            from session_clock import eod_flatten_window_pt, tz_pt  # type: ignore
        except Exception:
            return None
    try:
        pt = tz_pt()
        local = entry.astimezone(pt)
        start, _end = eod_flatten_window_pt()
        naive = datetime.combine(local.date(), start)
        if hasattr(pt, "localize"):
            deadline_pt = pt.localize(naive)
        else:
            deadline_pt = naive.replace(tzinfo=pt)
        return deadline_pt.astimezone(timezone.utc)
    except Exception:
        return None


def recording_deadline(entry_time: Any) -> Optional[datetime]:
    entry = parse_timestamp_utc(str(entry_time or ""))
    if entry is None:
        return None
    eod = session_eod_recording_deadline_utc(entry)
    if eod is not None:
        return eod
    return entry + timedelta(seconds=recording_window_seconds())


def recording_deadline_iso(entry_time: Any) -> str:
    deadline = recording_deadline(entry_time)
    return deadline.isoformat() if deadline is not None else ""


def _as_utc(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        current = value
        if current.tzinfo is None:
            return current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc)
    if value is None:
        return datetime.now(timezone.utc)
    return parse_timestamp_utc(str(value))


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        parsed = float(value)
        return parsed if parsed == parsed else None
    except (TypeError, ValueError):
        return None


def exit_is_recorded_loss(
    *,
    entry_premium: Any = None,
    exit_premium: Any = None,
    realized_pnl: Any = None,
    realized_pnl_pct: Any = None,
) -> bool:
    """True when the live exit was a minus (desk ``(-)``)."""
    dollars = _to_float(realized_pnl)
    if dollars is not None:
        return dollars < 0.0
    pct = _to_float(realized_pnl_pct)
    if pct is not None:
        return pct < 0.0
    exit_p = _to_float(exit_premium)
    entry_p = _to_float(entry_premium)
    if exit_p is not None and entry_p is not None:
        return exit_p < entry_p
    return False


def exit_is_recorded_profit(
    *,
    entry_premium: Any = None,
    exit_premium: Any = None,
    realized_pnl: Any = None,
    realized_pnl_pct: Any = None,
) -> bool:
    """True when the live exit was a plus (desk ``(+)``). Flat is not profit."""
    dollars = _to_float(realized_pnl)
    if dollars is not None:
        return dollars > 0.0
    pct = _to_float(realized_pnl_pct)
    if pct is not None:
        return pct > 0.0
    exit_p = _to_float(exit_premium)
    entry_p = _to_float(entry_premium)
    if exit_p is not None and entry_p is not None:
        return exit_p > entry_p
    return False


def should_shadow_after_exit(
    *,
    entry_premium: Any = None,
    exit_premium: Any = None,
    realized_pnl: Any = None,
    realized_pnl_pct: Any = None,
    extra: Any = None,
) -> bool:
    """Post-exit OCC leftover is for profitable fills only (default).

    Unfilled emit shadows never enroll. Losers enroll only when
    ``PROFIT_OPTIMIZER_LOSS_SHADOW_SECONDS`` is positive.
    """
    bag = extra if isinstance(extra, dict) else {}
    if bag.get("emit_shadow"):
        return False
    kwargs = dict(
        entry_premium=entry_premium,
        exit_premium=exit_premium,
        realized_pnl=realized_pnl,
        realized_pnl_pct=realized_pnl_pct,
    )
    if exit_is_recorded_loss(**kwargs):
        return loss_shadow_seconds() > 0.0
    return exit_is_recorded_profit(**kwargs)


def loss_exit_shadow_deadline(entry_time: Any, exit_time: Any) -> Optional[datetime]:
    """Shadow cutoff for a losing exit: exit + 30m, never past session EOD."""
    eod = recording_deadline(entry_time)
    exit_dt = _as_utc(exit_time) if exit_time is not None else None
    if exit_dt is None:
        return eod
    loss_end = exit_dt + timedelta(seconds=loss_shadow_seconds())
    if eod is not None and eod < loss_end:
        return eod
    return loss_end


def loss_exit_shadow_deadline_iso(entry_time: Any, exit_time: Any) -> str:
    deadline = loss_exit_shadow_deadline(entry_time, exit_time)
    return deadline.isoformat() if deadline is not None else ""


def recording_window_active(
    entry_time: Any,
    *,
    now: Optional[Any] = None,
    deadline_at: Optional[Any] = None,
) -> bool:
    deadline = None
    if deadline_at:
        deadline = _as_utc(deadline_at)
    if deadline is None:
        deadline = recording_deadline(entry_time)
    if deadline is None:
        return False
    current = _as_utc(now)
    if current is None:
        return False
    return current < deadline
