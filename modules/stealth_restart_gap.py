"""Cloud Run restart: do not flatten winners on unobserved giveback.

A mid-session kill (liveness blocked while GCS ran on the trading loop)
drops the in-process monitor clock. On reboot PX2 compared persisted
session HWM (~200%) to the first live print (~39%) and fired ``premium_trail``.

Hold the position, keep session max for Telegram, and re-arm the trail
from the first observed print after the gap.
"""

from __future__ import annotations

import time
from typing import Any, Dict

# In-process ticks are ~2s. A Cloud Run replacement is tens of seconds+.
RESTART_GAP_SEC = 45.0
# First tick after process start has no in-process last tick. Treat a large
# unobserved giveback vs persisted HWM as the same kill/restart gap.
# Units match engine pnl fractions (0.25 == 25 percentage points).
STALE_HWM_GIVEBACK_PCT = 0.25
# Distinguish a new process (Cloud Run replace) from a live starved tick.
# last_monitor_tick_epoch from *this* process must never rebase the 20pp trail.
PROCESS_START_EPOCH = time.time()
_PRIOR_PROCESS_SLACK_SEC = 1.0


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def apply_restart_gap_hold(
    osnap: Dict[str, Any],
    *,
    now_epoch: float,
    current_pnl_pct: float,
    current_underlying: float = 0.0,
    watchdog_gap_seconds: float = 0.0,
    process_start_epoch: float = 0.0,
) -> bool:
    """Rebase PX2/PX3 trail floors after a Cloud Run / process restart.

    In-process starved ticks (15–125s) must **not** rebase: that is how a
    100% HWM winner banks 32% (SLV 28 Aug). Only a last tick from a *prior*
    process, or a first tick with no last epoch and ≥25pp stale giveback,
    is a kill/restore.

    Keeps ``max_pnl_pct`` (session peak) for Telegram. Returns True when a
    rebase was applied.
    """
    if not isinstance(osnap, dict):
        return False
    last_f = _as_float(osnap.get("last_monitor_tick_epoch"), 0.0)
    now_f = _as_float(now_epoch, 0.0)
    boot = _as_float(process_start_epoch, 0.0) or float(PROCESS_START_EPOCH)
    current = _as_float(current_pnl_pct, 0.0)
    session_hwm = max(
        _as_float(osnap.get("max_pnl_pct"), 0.0),
        _as_float(osnap.get("px2_prem_hwm_pct"), 0.0),
        _as_float(osnap.get("session_max_pnl_pct"), 0.0),
        current,
    )
    unobserved_giveback = session_hwm - current
    prior_process = last_f > 0.0 and last_f < (boot - _PRIOR_PROCESS_SLACK_SEC)
    first_tick_stale_hwm = last_f <= 0.0 and unobserved_giveback >= STALE_HWM_GIVEBACK_PCT
    if not prior_process and not first_tick_stale_hwm:
        return False

    gap = (now_f - last_f) if last_f > 0.0 and now_f > last_f else 0.0
    if prior_process:
        gap = max(gap, _as_float(watchdog_gap_seconds, 0.0))
    prior_stale = prior_process and unobserved_giveback >= STALE_HWM_GIVEBACK_PCT
    long_prior_gap = prior_process and gap >= RESTART_GAP_SEC
    if not first_tick_stale_hwm and not prior_stale and not long_prior_gap:
        return False

    osnap["session_max_pnl_pct"] = float(session_hwm)
    osnap["px2_prem_hwm_pct"] = float(current)
    osnap["trail_anchor_pnl_pct"] = float(current)
    osnap["px2_trail_rebased"] = True
    osnap["px2_stall_und_ticks"] = 0
    osnap["px2_stall_prem_ticks"] = 0
    osnap["px3_skip_floor_once"] = True
    und = _as_float(current_underlying, 0.0)
    if und > 0.0:
        osnap["px3_und_hwm_price"] = float(und)
        osnap["px25_und_hwm_price"] = float(und)
    osnap["restart_gap_hold"] = True
    osnap["restart_gap_seconds"] = float(gap if gap > 0.0 else RESTART_GAP_SEC)
    osnap["restart_gap_session_hwm_pct"] = float(session_hwm)
    return True


def trail_hwm_for_px2(
    osnap: Dict[str, Any],
    *,
    session_max_pnl_pct: float,
    current_pnl_pct: float,
) -> float:
    """PX2 trail HWM. After a restart rebase, ignore session max."""
    current = _as_float(current_pnl_pct, 0.0)
    stored = max(
        _as_float(osnap.get("px2_prem_hwm_pct") if isinstance(osnap, dict) else 0.0, 0.0),
        _as_float(osnap.get("trail_anchor_pnl_pct") if isinstance(osnap, dict) else 0.0, 0.0),
        current,
    )
    if isinstance(osnap, dict) and osnap.get("px2_trail_rebased"):
        osnap["px2_prem_hwm_pct"] = float(stored)
        osnap["trail_anchor_pnl_pct"] = float(stored)
        return float(stored)
    return float(max(_as_float(session_max_pnl_pct, 0.0), stored, current))


def giveback_drawdown_pct(
    osnap: Dict[str, Any],
    *,
    session_max_pnl_pct: float,
    current_pnl_pct: float,
) -> float:
    """Drawdown used for mfe_trail. After restart rebase, measure vs trail anchor."""
    current = _as_float(current_pnl_pct, 0.0)
    if isinstance(osnap, dict) and osnap.get("px2_trail_rebased"):
        trail = max(
            _as_float(osnap.get("trail_anchor_pnl_pct"), 0.0),
            _as_float(osnap.get("px2_prem_hwm_pct"), 0.0),
            current,
        )
        osnap["trail_anchor_pnl_pct"] = float(trail)
        return max(0.0, trail - current)
    return max(0.0, _as_float(session_max_pnl_pct, 0.0) - current)
