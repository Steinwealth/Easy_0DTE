#!/usr/bin/env python3
"""
Unified 0DTE options stealth trailing — premium-based exits for ORB1 Breakout + Trendline 0DTE.

Single source of truth for 0DTE option position exits when wired from PrimeTradingSystem.
ORB ETF path continues to use prime_stealth_trailing_tp.py unchanged.
"""

from __future__ import annotations

import logging
import math
import os
import re
import time
from dataclasses import dataclass, field, fields
from datetime import datetime, timedelta, timezone
from enum import Enum
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

try:
    from .config_loader import get_config_value
except ImportError:
    from config_loader import get_config_value

try:
    from .execution_telemetry import log_option_exit_diagnosis
except ImportError:
    from modules.execution_telemetry import log_option_exit_diagnosis

from easy0DTE.modules.options_exit_reason_catalog import (
    OptionsStealthExitReason as XER,
    normalize_exit_reason,
)

log = logging.getLogger("prime_options_stealth_trailing")

# trade_id|symbol -> monotonic time of last OPTION_PRICE_RESOLUTION_AUDIT INFO emit
_option_price_resolution_audit_last: Dict[str, float] = {}
_option_degraded_live_quote_last: Dict[str, float] = {}
_option_price_source_log_last: Dict[str, float] = {}
_option_degraded_mode_log_last: Dict[str, float] = {}
_option_hwm_skip_log_last: Dict[str, float] = {}
_orb_exit_deferred_audit_last: Dict[str, float] = {}
# Throttle consolidated premium lifecycle lines (OPTION_PREMIUM_MARK_UPDATE / OPTION_EXIT_TRIGGER_EVAL).
_option_premium_lifecycle_tick_last: Dict[str, float] = {}
_option_exit_trigger_eval_last: Dict[str, float] = {}
# ORB1 Breakout: throttled OPTION_*_SUPPRESSED_EXIT_GRADE lines (per trade_id|kind).
_orb_exit_grade_suppress_log_last: Dict[str, float] = {}
_orb_degraded_hold_escalated_last: Dict[str, float] = {}

# Hold reasons that block time/no-progress exits but must NOT count as degraded quotes.
_ORB_TRUSTED_EXIT_GRADE_HOLD_REASONS = frozenset(
    {
        "below_trailing_trigger",
        "waiting_for_trailing_arm",
        "no_progress_not_met",
    }
)


def resolve_orb_exit_quality_hold_reason(
    *,
    favorable_underlying: bool,
    timeout_clock_frozen_now: bool,
    good_mark_tick_count: int,
    required_good_ticks: int,
    mark_is_exit_grade_now: bool,
    pnl_pct: float,
    trailing_trigger_pct: float,
) -> Tuple[str, bool]:
    """
    Classify why an ORB1 Breakout time/no-progress exit is being held.

    Returns (blocked_reason, counts_as_degraded_for_monitor_ratio).
    When marks are exit-grade and good ticks are sufficient, never label as non_exit_grade_mark.
    """
    if favorable_underlying:
        return "favorable_underlying", False
    if timeout_clock_frozen_now:
        return "timeout_clock_frozen", True
    if int(good_mark_tick_count) < int(required_good_ticks):
        return "insufficient_good_marks", True
    if not mark_is_exit_grade_now:
        return "non_exit_grade_mark", True
    if float(pnl_pct) < float(trailing_trigger_pct):
        return "below_trailing_trigger", False
    return "waiting_for_trailing_arm", False


def should_force_wall_clock_time_exit(
    *,
    is_0dte_single: bool,
    max_pnl_pct: float,
    mfe_giveback_trigger_pct: float = 0.10,
) -> bool:
    """Force time_exit on wall clock when MFE never reached giveback arm (+10%).

    Prevents wide_spread / timeout_clock_frozen from holding a sub-giveback trade to EOD.
    """
    if not is_0dte_single:
        return False
    trigger = float(mfe_giveback_trigger_pct or 0.10)
    return float(max_pnl_pct or 0.0) + 1e-12 < trigger



def near_arm_hold_after_wraps(*, near_arm_protects: bool, wrap_would_fire: bool) -> bool:
    """Band B evaluate order: near-ARM skip only when no pre-ARM/low-ARM/CAP wrap applies."""
    return bool(near_arm_protects) and not bool(wrap_would_fire)


def near_arm_mfe_protects_weak_red_exit(
    *,
    is_0dte_single: bool,
    max_pnl_pct: float,
    current_pnl_pct: float,
    protect_mfe_pct: float = 0.20,
    winner_arm_pct: float = 0.30,
    mark_usable: bool = True,
) -> bool:
    """Hold weak timeout/scratch exits red after meaningful MFE that never fully armed."""
    if not is_0dte_single or not mark_usable:
        return False
    protect = float(protect_mfe_pct or 0.0)
    arm = float(winner_arm_pct or 0.0)
    if protect <= 0.0 or arm <= protect:
        return False
    max_pnl = float(max_pnl_pct or 0.0)
    current_pnl = float(current_pnl_pct or 0.0)
    return max_pnl + 1e-12 >= protect and max_pnl < arm - 1e-12 and current_pnl < 0.0


def near_arm_still_green_skips_no_progress(
    *,
    is_0dte_single: bool,
    max_pnl_pct: float,
    current_pnl_pct: float,
    protect_mfe_pct: float = 0.20,
    winner_arm_pct: float = 0.30,
    mark_usable: bool = True,
) -> bool:
    """Hold still-green never-armed Band B (20–30% MFE) through weak timeouts.

    Skips no_progress, time_exit, and 60m never-armed BE. HOOD/COIN 2026-09-22:
    +25–28% HWM, still +9% at 60m, cut by no_progress because skip_if_mfe is
    floored to ARM +30%. 60m BE is evaluated first and would also cut the same
    class if current had faded into [0, +2%). Band B goal is don't kill recoveries.
    """
    if not is_0dte_single or not mark_usable:
        return False
    protect = float(protect_mfe_pct or 0.0)
    arm = float(winner_arm_pct or 0.0)
    if protect <= 0.0 or arm <= protect:
        return False
    max_pnl = float(max_pnl_pct or 0.0)
    current_pnl = float(current_pnl_pct or 0.0)
    return max_pnl + 1e-12 >= protect and max_pnl < arm - 1e-12 and current_pnl >= 0.0


_PRE_ARM_PROFIT_PROTECT_WEAK_EXIT_REASONS = {
    "breakeven",
    "mfe_trail",
    "mfe_trailing_stop",
    "no_progress",
    "premium_stop",
    "price_stop",
    "scratch",
    "time_exit",
}


def pre_arm_profit_protect_should_exit(
    *,
    enabled: bool,
    is_0dte_single: bool,
    protection_quote_ok: bool,
    arm_trail_armed: bool,
    max_pnl_pct: float,
    current_pnl_pct: float,
    held_minutes: float,
    seconds_since_hwm: float,
    candidate_exit_reason: Optional[str] = None,
    trigger_mfe_pct: float = 0.20,
    floor_pnl_pct: float = 0.02,
    min_current_pnl_pct: float = -0.05,
    winner_arm_pct: float = 0.30,
    min_hold_minutes: float = 20.0,
    min_seconds_since_hwm: float = 600.0,
    allow_proactive: bool = False,
) -> bool:
    """Pre-ARM protective floor for +20% HWM trades that failed to reach full ARM.

    Wait until the trade has slipped red, then settle at the configured green
    floor. This protects MSFT-class fades without clipping still-green dips that
    can recover into the normal +30% ARM path.

    ``allow_proactive=True`` fires when gates are met without a weak candidate
    (Band B hole: hold/HWM/current gates true from ~20m but no_progress waits 60m).
    """
    if not enabled or not is_0dte_single or not protection_quote_ok or arm_trail_armed:
        return False
    reason = str(candidate_exit_reason or "").strip().lower()
    if allow_proactive:
        if reason and reason not in _PRE_ARM_PROFIT_PROTECT_WEAK_EXIT_REASONS and reason != "proactive":
            return False
    elif reason not in _PRE_ARM_PROFIT_PROTECT_WEAK_EXIT_REASONS:
        return False
    trigger = float(trigger_mfe_pct or 0.0)
    floor = float(floor_pnl_pct or 0.0)
    arm = float(winner_arm_pct or 0.0)
    min_current = float(min_current_pnl_pct if min_current_pnl_pct is not None else -0.05)
    if trigger <= 0.0 or floor < 0.0 or arm <= trigger:
        return False
    if float(max_pnl_pct or 0.0) + 1e-12 < trigger:
        return False
    if float(max_pnl_pct or 0.0) >= arm - 1e-12:
        return False
    if float(held_minutes or 0.0) + 1e-12 < float(min_hold_minutes or 0.0):
        return False
    if float(seconds_since_hwm or 0.0) + 1e-12 < float(min_seconds_since_hwm or 0.0):
        return False
    return float(current_pnl_pct or 0.0) <= min_current + 1e-12


def low_arm_profit_protect_should_exit(
    *,
    enabled: bool,
    is_0dte_single: bool,
    protection_quote_ok: bool,
    arm_trail_armed: bool,
    max_pnl_pct: float,
    current_pnl_pct: float,
    held_minutes: float,
    seconds_since_hwm: float,
    candidate_exit_reason: Optional[str] = None,
    trigger_mfe_pct: float = 0.10,
    max_mfe_pct: float = 0.20,
    floor_pnl_pct: float = 0.0,
    min_current_pnl_pct: float = -0.05,
    min_hold_minutes: float = 20.0,
    min_seconds_since_hwm: float = 600.0,
    allow_proactive: bool = False,
) -> bool:
    """Protect +10% to <20% MFE trades from weak/degraded exits deep red.

    ``allow_proactive=True`` fires when gates are met without a weak candidate.
    """
    if not enabled or not is_0dte_single or not protection_quote_ok or arm_trail_armed:
        return False
    reason = str(candidate_exit_reason or "").strip().lower()
    if allow_proactive:
        if reason and reason not in _PRE_ARM_PROFIT_PROTECT_WEAK_EXIT_REASONS and reason != "proactive":
            return False
    elif reason not in _PRE_ARM_PROFIT_PROTECT_WEAK_EXIT_REASONS:
        return False
    trigger = float(trigger_mfe_pct or 0.0)
    max_mfe = float(max_mfe_pct or 0.0)
    floor = float(floor_pnl_pct or 0.0)
    min_current = float(min_current_pnl_pct if min_current_pnl_pct is not None else -0.05)
    max_pnl = float(max_pnl_pct or 0.0)
    if trigger <= 0.0 or max_mfe <= trigger or floor < 0.0:
        return False
    if max_pnl + 1e-12 < trigger:
        return False
    if max_pnl >= max_mfe - 1e-12:
        return False
    if float(held_minutes or 0.0) + 1e-12 < float(min_hold_minutes or 0.0):
        return False
    if float(seconds_since_hwm or 0.0) + 1e-12 < float(min_seconds_since_hwm or 0.0):
        return False
    return float(current_pnl_pct or 0.0) <= min_current + 1e-12




def weak_loss_cap_should_exit(
    *,
    enabled: bool,
    is_0dte_single: bool,
    protection_quote_ok: bool,
    arm_trail_armed: bool,
    max_pnl_pct: float,
    current_pnl_pct: float,
    candidate_exit_reason: Optional[str] = None,
    max_mfe_pct: float = 0.30,
    floor_pnl_pct: float = -0.10,
) -> bool:
    """CAP_weak: on weak exits, peak MFE in (0, max], loss worse than floor → clamp to floor.

    Decision floor (not fill); fill = liquidable bid. Bid-calibrated floor −10% (was −15%).
    Does not fire once peak MFE reaches max_mfe (keeps ≥30% / armed capture clean when max=0.30).
    """
    if not enabled or not is_0dte_single or not protection_quote_ok or arm_trail_armed:
        return False
    reason = str(candidate_exit_reason or "").strip().lower()
    if reason not in _PRE_ARM_PROFIT_PROTECT_WEAK_EXIT_REASONS:
        return False
    max_mfe = float(max_mfe_pct or 0.0)
    floor = float(floor_pnl_pct if floor_pnl_pct is not None else -0.10)
    max_pnl = float(max_pnl_pct or 0.0)
    if max_mfe <= 0.0 or floor >= 0.0:
        return False
    if max_pnl <= 1e-12:
        return False
    if max_pnl + 1e-12 >= max_mfe:
        return False
    return float(current_pnl_pct or 0.0) < floor - 1e-12


def early_be_protect_should_exit(
    *,
    enabled: bool,
    is_0dte_single: bool,
    protection_quote_ok: bool,
    arm_trail_armed: bool,
    max_pnl_pct: float,
    current_pnl_pct: float,
    held_minutes: float,
    seconds_since_hwm: float,
    max_mfe_pct: float = 0.20,
    floor_pnl_pct: float = 0.0,
    min_current_pnl_pct: float = -0.05,
    min_hold_minutes: float = 5.0,
    min_seconds_since_hwm: float = 0.0,
) -> bool:
    """Band D EARLY_BE / aggressive FADE: never-armed, peak MFE still <20% → BE.

    Live-honest: uses peak-so-far (max_pnl). No weak-candidate requirement.
    LIVE 2026-09-22: maxMFE 20% / hold≥5m / ≤−5% / HWM age 0 → bid-aware working BE.
    Does not fire once peak MFE reaches max_mfe (default 20%). Decision floor;
    fill = liquidable bid (protect_floor_not_fill).
    """
    if not enabled or not is_0dte_single or not protection_quote_ok or arm_trail_armed:
        return False
    max_mfe = float(max_mfe_pct or 0.0)
    floor = float(floor_pnl_pct or 0.0)
    min_current = float(min_current_pnl_pct if min_current_pnl_pct is not None else -0.05)
    max_pnl = float(max_pnl_pct or 0.0)
    if max_mfe <= 0.0 or floor < 0.0:
        return False
    # Peak must stay strictly below band ceiling (live aggressive FADE → 0.20)
    if max_pnl + 1e-12 >= max_mfe:
        return False
    if float(held_minutes or 0.0) + 1e-12 < float(min_hold_minutes or 0.0):
        return False
    if float(seconds_since_hwm or 0.0) + 1e-12 < float(min_seconds_since_hwm or 0.0):
        return False
    return float(current_pnl_pct or 0.0) <= min_current + 1e-12


def _bid_aware_be_clear(osnap: Dict[str, Any]) -> None:
    """Clear working bid-aware BE state from osnap."""
    for k in (
        "protect_bid_aware_be_armed",
        "protect_bid_aware_be_armed_at",
        "protect_bid_aware_be_deadline_ts",
        "protect_bid_aware_be_floor_pnl_pct",
        "protect_bid_aware_be_hard_stop_pct",
        "protect_bid_aware_be_target_pnl_pct",
        "protect_bid_aware_be_rule",
        "protect_bid_aware_be_rule_max_mfe",
        "protect_bid_aware_be_cancel_if_peak_above_rule_max",
    ):
        osnap.pop(k, None)


def _bid_aware_be_arm(
    osnap: Dict[str, Any],
    *,
    now: datetime,
    rule: str,
    floor_pnl_pct: float,
    rule_max_mfe: float,
    wait_minutes: float,
    hard_stop_pct: float,
    target_pnl_pct: float,
    cancel_if_peak_above_rule_max: bool,
    position_id: str,
    position_symbol: str,
    current_pnl_pct: float,
    max_pnl_pct: float,
) -> None:
    """Arm working BE: hold for reclaim to target (default 0% BE) instead of immediate red bid exit."""
    wait_m = max(0.0, float(wait_minutes or 0.0))
    deadline = now + timedelta(minutes=wait_m) if wait_m > 0 else now
    # Decision floors must not stamp Demo/Live fills while we wait for a liquidable bid reclaim.
    for clamp_key in (
        "early_be_protect_clamp",
        "pre_arm_profit_protect_clamp",
        "low_arm_profit_protect_clamp",
        "weak_loss_cap_clamp",
    ):
        osnap.pop(clamp_key, None)
    osnap.pop("protect_floor_not_fill", None)
    osnap["protect_bid_aware_be_armed"] = True
    osnap["protect_bid_aware_be_armed_at"] = now.isoformat()
    osnap["protect_bid_aware_be_deadline_ts"] = deadline.isoformat()
    osnap["protect_bid_aware_be_floor_pnl_pct"] = float(floor_pnl_pct)
    osnap["protect_bid_aware_be_hard_stop_pct"] = float(hard_stop_pct)
    osnap["protect_bid_aware_be_target_pnl_pct"] = float(target_pnl_pct)
    osnap["protect_bid_aware_be_rule"] = str(rule or "")
    osnap["protect_bid_aware_be_rule_max_mfe"] = float(rule_max_mfe)
    osnap["protect_bid_aware_be_cancel_if_peak_above_rule_max"] = bool(
        cancel_if_peak_above_rule_max
    )
    log.info(
        "OPTION_BID_AWARE_BE_ARM | trade_id=%s | symbol=%s | rule=%s | "
        "current_pnl_pct=%.4f | max_pnl_pct=%.4f | target_pnl_pct=%.4f | "
        "hard_stop_pct=%.4f | floor_pnl_pct=%.4f | rule_max_mfe=%.4f | "
        "wait_minutes=%.2f | deadline=%s | grep=OPTION_BID_AWARE_BE_ARM",
        position_id,
        position_symbol,
        str(rule or ""),
        float(current_pnl_pct),
        float(max_pnl_pct),
        float(target_pnl_pct),
        float(hard_stop_pct),
        float(floor_pnl_pct),
        float(rule_max_mfe),
        float(wait_m),
        deadline.isoformat(),
    )


def _bid_aware_be_resolve(
    osnap: Dict[str, Any],
    *,
    now: datetime,
    current_pnl_pct: float,
    max_pnl_pct: float,
    position_id: str,
    position_symbol: str,
) -> Optional[str]:
    """Resolve armed working BE.

    Returns:
      'fill' | 'hardstop' | 'timeout' — caller should exit at liquidable bid
      'cancel' — clear state and resume normal PC
      None — still waiting (caller should return None / no exit)
    """
    if not bool(osnap.get("protect_bid_aware_be_armed")):
        return None
    target = float(
        osnap.get("protect_bid_aware_be_target_pnl_pct")
        if osnap.get("protect_bid_aware_be_target_pnl_pct") is not None
        else 0.0
    )
    hard_stop = float(
        osnap.get("protect_bid_aware_be_hard_stop_pct")
        if osnap.get("protect_bid_aware_be_hard_stop_pct") is not None
        else -0.25
    )
    rule = str(osnap.get("protect_bid_aware_be_rule") or "")
    rule_max = float(osnap.get("protect_bid_aware_be_rule_max_mfe") or 0.0)
    cancel_if_peak = bool(osnap.get("protect_bid_aware_be_cancel_if_peak_above_rule_max", True))
    cur = float(current_pnl_pct or 0.0)
    peak = float(max_pnl_pct or 0.0)

    if cancel_if_peak and rule_max > 0.0 and peak + 1e-12 >= rule_max:
        log.info(
            "OPTION_BID_AWARE_BE_CANCEL | trade_id=%s | symbol=%s | rule=%s | "
            "reason=peak_above_rule_max | max_pnl_pct=%.4f | rule_max_mfe=%.4f | "
            "current_pnl_pct=%.4f | grep=OPTION_BID_AWARE_BE_CANCEL",
            position_id,
            position_symbol,
            rule,
            peak,
            rule_max,
            cur,
        )
        _bid_aware_be_clear(osnap)
        return "cancel"

    if cur + 1e-12 >= target:
        log.info(
            "OPTION_BID_AWARE_BE_FILL | trade_id=%s | symbol=%s | rule=%s | "
            "current_pnl_pct=%.4f | target_pnl_pct=%.4f | max_pnl_pct=%.4f | "
            "grep=OPTION_BID_AWARE_BE_FILL",
            position_id,
            position_symbol,
            rule,
            cur,
            target,
            peak,
        )
        return "fill"

    if cur <= hard_stop + 1e-12:
        log.info(
            "OPTION_BID_AWARE_BE_HARDSTOP | trade_id=%s | symbol=%s | rule=%s | "
            "current_pnl_pct=%.4f | hard_stop_pct=%.4f | max_pnl_pct=%.4f | "
            "grep=OPTION_BID_AWARE_BE_HARDSTOP",
            position_id,
            position_symbol,
            rule,
            cur,
            hard_stop,
            peak,
        )
        return "hardstop"

    deadline_raw = osnap.get("protect_bid_aware_be_deadline_ts")
    deadline = None
    if deadline_raw:
        try:
            deadline = datetime.fromisoformat(str(deadline_raw).replace("Z", "+00:00"))
            if deadline.tzinfo is not None and getattr(now, "tzinfo", None) is None:
                deadline = deadline.replace(tzinfo=None)
            elif deadline.tzinfo is None and getattr(now, "tzinfo", None) is not None:
                deadline = deadline.replace(tzinfo=now.tzinfo)
        except Exception:
            deadline = None
    if deadline is not None and now >= deadline:
        log.info(
            "OPTION_BID_AWARE_BE_TIMEOUT | trade_id=%s | symbol=%s | rule=%s | "
            "current_pnl_pct=%.4f | max_pnl_pct=%.4f | deadline=%s | "
            "grep=OPTION_BID_AWARE_BE_TIMEOUT",
            position_id,
            position_symbol,
            rule,
            cur,
            peak,
            str(deadline_raw),
        )
        return "timeout"

    return None


def early_prem_trail_should_exit(
    *,
    enabled: bool,
    is_0dte_single: bool,
    before_cutoff: bool,
    protection_quote_ok: bool,
    hold_suppressed: bool,
    max_pnl_pct: float,
    current_pnl_pct: float,
    min_hwm_pct: float,
    trail_pct: float,
    min_current_pnl_pct: float,
) -> bool:
    """Dual-sided pre-cutoff premium trail for exceptional option premium trades."""
    if not enabled or not is_0dte_single or not before_cutoff:
        return False
    if not protection_quote_ok or hold_suppressed:
        return False
    if float(max_pnl_pct or 0.0) + 1e-12 < float(min_hwm_pct or 0.0):
        return False
    if float(current_pnl_pct or 0.0) + 1e-12 < float(min_current_pnl_pct or 0.0):
        return False
    return (float(max_pnl_pct or 0.0) - float(current_pnl_pct or 0.0)) + 1e-12 >= float(
        trail_pct or 0.0
    )


def classify_breakeven_or_scratch_exit(
    *,
    is_0dte_single: bool,
    held_minutes: float,
    current_pnl_pct: float,
    breakeven_minutes: float = 60.0,
    scratch_minutes: Optional[float] = None,
    exit_below_pnl_pct: float = 0.02,
    scratch_below_pnl_pct: float = -0.0005,
    breakeven_enabled: bool = True,
    max_pnl_pct: float = 0.0,
    mfe_giveback_trigger_pct: float = 0.10,
    current_executable_bid: Optional[float] = None,
    position: Any = None,
    # Compat alias used by older call sites / tests
    breakeven_max_pnl_pct: Optional[float] = None,
) -> Tuple[Optional[str], Dict[str, Any]]:
    """0DTE single BE/scratch (never-armed only).

    breakeven: ≥60m · −0.05% ≤ PnL < +2%
    scratch:   ≥62m · PnL < −0.05%
    ARM_TRAIL-armed winners are owned by the price runner.
    """
    from modules.fee_aware_breakeven import classify_breakeven_or_scratch

    ceiling = float(
        breakeven_max_pnl_pct
        if breakeven_max_pnl_pct is not None
        else (exit_below_pnl_pct if exit_below_pnl_pct is not None else 0.02)
    )
    return classify_breakeven_or_scratch(
        is_0dte_single=bool(is_0dte_single),
        held_minutes=float(held_minutes or 0.0),
        current_pnl_pct=float(current_pnl_pct or 0.0),
        max_pnl_pct=float(max_pnl_pct or 0.0),
        mfe_giveback_trigger_pct=float(mfe_giveback_trigger_pct or 0.10),
        breakeven_minutes=float(breakeven_minutes or 60.0),
        scratch_minutes=scratch_minutes,
        breakeven_enabled=bool(breakeven_enabled),
        exit_below_pnl_pct=ceiling,
        scratch_below_pnl_pct=float(
            scratch_below_pnl_pct if scratch_below_pnl_pct is not None else -0.0005
        ),
        position=position,
        current_executable_bid=current_executable_bid,
    )


def should_exit_breakeven(
    *,
    is_0dte_single: bool,
    held_minutes: float,
    current_pnl_pct: float,
    breakeven_minutes: float = 60.0,
    scratch_minutes: Optional[float] = None,
    breakeven_max_pnl_pct: float = 0.02,
    breakeven_enabled: bool = True,
    max_pnl_pct: float = 0.0,
    mfe_giveback_trigger_pct: float = 0.10,
    current_executable_bid: Optional[float] = None,
    position: Any = None,
    scratch_below_pnl_pct: float = -0.0005,
) -> bool:
    """True when breakeven or scratch band would fire."""
    reason, _ = classify_breakeven_or_scratch_exit(
        is_0dte_single=is_0dte_single,
        held_minutes=held_minutes,
        current_pnl_pct=current_pnl_pct,
        breakeven_minutes=breakeven_minutes,
        scratch_minutes=scratch_minutes,
        exit_below_pnl_pct=breakeven_max_pnl_pct,
        scratch_below_pnl_pct=scratch_below_pnl_pct,
        breakeven_enabled=breakeven_enabled,
        max_pnl_pct=max_pnl_pct,
        mfe_giveback_trigger_pct=mfe_giveback_trigger_pct,
        current_executable_bid=current_executable_bid,
        position=position,
    )
    return reason is not None



def orb_exit_hold_log_token(counts_as_degraded: bool) -> str:
    return "ORB_OPTIONS_DEGRADED_DATA_HOLD" if counts_as_degraded else "ORB_OPTIONS_EXIT_HOLD"


def orb_trusted_exit_grade_hold_active(osnap: Dict[str, Any]) -> bool:
    if not isinstance(osnap, dict):
        return False
    if bool(osnap.get("orb_trusted_exit_grade_hold")):
        return True
    hold = str(osnap.get("orb_last_exit_hold_reason") or "")
    if bool(osnap.get("mark_is_exit_grade")) and any(
        r in hold for r in _ORB_TRUSTED_EXIT_GRADE_HOLD_REASONS
    ):
        return True
    return False


def _orb_exit_grade_suppression_log_min_seconds() -> float:
    try:
        return float(get_config_value("OPTION_ORB_EXIT_GRADE_SUPPRESSION_LOG_MIN_SECONDS", 55.0) or 0.0)
    except Exception:
        return 55.0


def _maybe_emit_orb_exit_grade_timeout_suppression(
    *,
    suppress_kind: str,
    position_id: str,
    position_symbol: str,
    blocked_reason: str,
    prem_src: str,
    osnap: Dict[str, Any],
    st: Any,
    required_good_ticks: int,
    now: datetime,
) -> None:
    """
    High-visibility ORB-only telemetry when time/no-progress exits are held for exit-grade policy.

    Emits OPTION_TIMEOUT_SUPPRESSED_EXIT_GRADE or OPTION_NO_PROGRESS_SUPPRESSED_EXIT_GRADE (throttled).
    Call sites should continue to emit OPTION_EXIT_TRIGGER_SUPPRESSED_QUOTE_QUALITY / ORB_OPTIONS_DEGRADED_DATA_HOLD as today.
    """
    iso_key = (
        "orb_time_exit_suppress_started_iso"
        if suppress_kind == "time_exit"
        else "orb_no_progress_suppress_started_iso"
    )
    if not osnap.get(iso_key):
        osnap[iso_key] = now.replace(tzinfo=timezone.utc).isoformat()
    started_iso = str(osnap.get(iso_key) or "")
    supp_sec = 0.0
    try:
        t0 = datetime.fromisoformat(started_iso.replace("Z", "+00:00"))
        if t0.tzinfo is None:
            t0 = t0.replace(tzinfo=timezone.utc)
        supp_sec = max(0.0, (now - t0.astimezone(timezone.utc)).total_seconds())
    except Exception:
        supp_sec = 0.0
    qg = str(osnap.get("orb_quote_grade") or osnap.get("quote_grade") or "unknown")
    quote_src = str(prem_src or osnap.get("last_premium_source") or "unknown")
    dedupe = f"{str(position_id or '')}|{suppress_kind}"
    min_sec = float(_orb_exit_grade_suppression_log_min_seconds())
    if min_sec > 0:
        tmono = time.monotonic()
        last = _orb_exit_grade_suppress_log_last.get(dedupe, -1e30)
        if (tmono - last) < min_sec:
            return
        _orb_exit_grade_suppress_log_last[dedupe] = tmono
    log_token = (
        "OPTION_TIMEOUT_SUPPRESSED_EXIT_GRADE"
        if suppress_kind == "time_exit"
        else "OPTION_NO_PROGRESS_SUPPRESSED_EXIT_GRADE"
    )
    log.info(
        "%s | trade_id=%s | symbol=%s | quote_grade=%s | quote_src=%s | mark_is_exit_grade=%s | "
        "good_mark_tick_count=%d | required_tick_count=%d | blocked_reason=%s | suppression_duration_sec=%.1f | "
        "timeout_clock_frozen=%s | mark_quality=%s",
        log_token,
        str(position_id or "-"),
        str(position_symbol or "-"),
        qg,
        quote_src,
        str(bool(osnap.get("mark_is_exit_grade"))).lower(),
        int(getattr(st, "good_mark_tick_count", 0) or 0),
        int(max(1, int(required_good_ticks or 1))),
        str(blocked_reason or ""),
        float(supp_sec),
        str(bool(osnap.get("timeout_clock_frozen") or getattr(st, "timeout_clock_frozen", False))).lower(),
        str(osnap.get("mark_quality") or "unknown"),
    )
    if supp_sec >= 180.0:
        ekey = f"esc|{dedupe}"
        now_e = time.monotonic()
        last_e = float(_orb_degraded_hold_escalated_last.get(ekey, -1e30))
        if now_e - last_e >= 120.0:
            _orb_degraded_hold_escalated_last[ekey] = now_e
            try:
                bypass = str(
                    bool(get_config_value("ORB_OPTIONS_SAFETY_EXIT_BYPASS_EXIT_GRADE", True))
                ).lower()
            except Exception:
                bypass = "true"
            log.warning(
                "ORB_OPTIONS_DEGRADED_HOLD_ESCALATED | trade_id=%s | symbol=%s | suppress_kind=%s | "
                "suppression_duration_sec=%.1f | blocked_reason=%s | safety_bypass_exit_grade_config=%s",
                str(position_id or "-"),
                str(position_symbol or "-"),
                str(suppress_kind or ""),
                float(supp_sec),
                str(blocked_reason or ""),
                bypass,
            )


def _option_premium_lifecycle_log_min_seconds() -> float:
    try:
        return float(get_config_value("OPTION_PREMIUM_LIFECYCLE_LOG_MIN_SECONDS", 7.0) or 0.0)
    except Exception:
        return 7.0


def _lifecycle_tick_should_emit(cache: Dict[str, float], dedupe_key: str) -> bool:
    min_sec = float(_option_premium_lifecycle_log_min_seconds())
    if min_sec <= 0:
        return True
    now = time.monotonic()
    last = cache.get(dedupe_key, -1e30)
    if (now - last) < min_sec:
        return False
    cache[dedupe_key] = now
    return True


def _log_orb_exit_suppressed_quote_quality(
    *,
    candidate_exit_reason: str,
    blocked_reason: str,
    position_id: str,
    position_symbol: str,
    prem_src: str,
    osnap: Dict[str, Any],
    st: Any,
) -> None:
    log.info(
        "OPTION_EXIT_TRIGGER_SUPPRESSED_QUOTE_QUALITY | trade_id=%s | symbol=%s | strategy_type=orb_0dte | "
        "candidate_exit_reason=%s | blocked_reason=%s | premium_source=%s | mark_quality=%s | mark_is_exit_grade=%s | "
        "good_mark_tick_count=%d | timeout_clock_frozen=%s | quote_age_seconds=%s",
        str(position_id or "-"),
        str(position_symbol or "-"),
        str(candidate_exit_reason or ""),
        str(blocked_reason or ""),
        str(prem_src or ""),
        str(osnap.get("mark_quality") or "unknown"),
        str(bool(osnap.get("mark_is_exit_grade"))).lower(),
        int(getattr(st, "good_mark_tick_count", 0) or 0),
        str(bool(osnap.get("timeout_clock_frozen"))).lower(),
        str(osnap.get("quote_age_seconds") if osnap.get("quote_age_seconds") is not None else "n/a"),
    )


def _premium_updates_favorable_hwm(
    prem_src: str,
    detail: Dict[str, Any],
    *,
    is_spread: bool,
) -> bool:
    """
    Gate favorable high-water-mark (premium_hwm / spread_hwm) and max_pnl_pct / had_opportunity updates.

    Trust matrix (intentionally conservative on synthetic / proxy paths):
    - Single-leg: True when ``price_source_telemetry == live``, OR when ``prem_src`` is
      ``exact`` / ``nearest`` / ``cached_quote`` (E*TRADE contract path; may still be
      ``mark_quality=cached_recent`` — exit-grade is separate via ``mark_is_exit_grade``).
    - Spreads: True only when ``price_source_telemetry == live`` (e.g. both legs resolved
      for ``spread_mtm_mid_last`` with ``spread_premium_source_quality=mtm_leg_mid_last``).
      Otherwise HWM / MFE do not ratchet up on incomplete or modeled marks.

    Exit triggers use additional checks (``live_non_synthetic_quote``, ``mark_is_exit_grade``,
    timeout clock freeze, ORB relief tiers) — see ``OPTION_EXIT_TRIGGER_EVAL`` and
    ``ORB_OPTIONS_EXIT_DEFERRED_AUDIT`` in this module.
    """
    pt = str(detail.get("price_source_telemetry") or "").strip().lower()
    if pt == "live":
        return True
    if is_spread:
        return False
    try:
        from modules.exit_grade_durability import premium_source_is_live_contract_quote

        return bool(premium_source_is_live_contract_quote(prem_src))
    except Exception:
        src = str(prem_src or "").strip().lower()
        return src in (
            "exact_quote",
            "exit_grade",
            "nearest",
            "cached_quote",
            "chain_strike_match",
        ) or ("exact_contract" in src)


def _tline_trail_distance_pct_from_mfe(cfg: "OptionStealthConfig", max_pnl_pct: float) -> float:
    """
    Trendline single-leg trail distance from premium MFE (not underlying).
    stop = premium_hwm * (1 - distance). See docs/0DTEProfitCapture.md §13.
    """
    pnl = float(max_pnl_pct)
    if pnl >= float(cfg.tline_runner_trigger_pct):
        return float(cfg.tline_runner_trailing_pct)
    if pnl >= float(cfg.tline_explosive_trigger_pct):
        return float(cfg.tline_explosive_trailing_pct)
    if pnl >= float(cfg.tline_aggressive_trigger_pct):
        return float(cfg.tline_aggressive_trailing_pct)
    return float(cfg.tline_base_trailing_pct)


def _tline_impulse_trail_activation_pct(cfg: "OptionStealthConfig") -> float:
    """Earliest premium-MFE % to arm impulse trailing (Trendline0DTE + Shared env)."""
    act = float(cfg.tline_impulse_trail_activate_pct or 0.0)
    alt = float(cfg.tline_impulse_trailing_trigger_pct or 0.0)
    if act > 0.0 and alt > 0.0:
        return min(act, alt)
    return act if act > 0.0 else alt


_option_stealth_state_snapshot_last: Dict[str, float] = {}


def _emit_option_stealth_state_if_due(*, cfg: Any, trade_id: str, symbol: str, message: str) -> None:
    """Throttled Cloud-friendly OPTION_STEALTH_STATE (see option_stealth_state_log_seconds)."""
    interval = float(getattr(cfg, "option_stealth_state_log_seconds", 15.0) or 0.0)
    if interval <= 0.0:
        return
    key = f"oss|{str(trade_id or '-')}|{str(symbol or '-')}"
    _log_info_throttled(key, message, interval_sec=interval)


def _log_info_throttled(key: str, message: str, *args: Any, interval_sec: float = 15.0) -> None:
    """INFO at most once per key per interval (monitor heartbeats / stale flags)."""
    now_m = time.monotonic()
    last = float(_option_stealth_state_snapshot_last.get(key, -1e30))
    if (now_m - last) < max(0.0, float(interval_sec or 0.0)):
        return
    _option_stealth_state_snapshot_last[key] = now_m
    if args:
        log.info(message, *args)
    else:
        log.info(message)


def _tline_quote_age_ok(cfg: Any, quote_age_seconds: Any) -> bool:
    try:
        age = float(quote_age_seconds or 0.0)
    except (TypeError, ValueError):
        age = 9999.0
    return age <= float(getattr(cfg, "quote_stale_max_age_seconds", 90.0) or 90.0)


def _tline_bid_ask_sanity_ok(detail: Dict[str, Any], prem: float) -> bool:
    try:
        bid = float(detail.get("option_bid") or 0.0)
        ask = float(detail.get("option_ask") or 0.0)
    except (TypeError, ValueError):
        return True
    if bid <= 0 or ask <= 0:
        return True
    if ask < bid:
        return False
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return True
    sp = (ask - bid) / max(mid, 1e-9)
    return sp <= 0.65


def _premium_absurd_vs_entry_modeled(
    prem: float,
    entry_premium: float,
    modeled_premium: Optional[float],
    cfg: Any,
    *,
    modeled_cap_mult: float = 2.5,
) -> bool:
    """True when a live premium is implausible vs entry/modeled (wrong strike, bad chain leg).

    Covers both phantom highs (wrong OCC / fat finger) and collapse ticks that print
    near-penny while entry was a real debit — those must not refresh live clocks or
    poison ``last_valid_option_price``.
    """
    try:
        p = float(prem or 0.0)
        ep = float(entry_premium or 0.0)
    except (TypeError, ValueError):
        return False
    if p <= 0 or ep <= 0:
        return False
    max_mult = float(getattr(cfg, "premium_max_mult_from_entry", 12.0) or 12.0)
    if p > ep * max(0.05, max_mult):
        return True
    # Collapse / one-tick wipe: live print ≪ entry (e.g. $0.01 vs $0.26 entry).
    # Do NOT reuse premium_min_mult_from_entry (that floors clamps UP at ~2%).
    collapse_mult = float(
        getattr(cfg, "premium_absurd_collapse_mult_from_entry", 0.08) or 0.08
    )
    if ep >= 0.05 and p + 1e-12 < ep * max(0.01, collapse_mult):
        return True
    try:
        modeled = float(modeled_premium or 0.0)
    except (TypeError, ValueError):
        modeled = 0.0
    if modeled > 0 and p > modeled * float(modeled_cap_mult):
        return True
    return False


_SYNTHETIC_ENTRY_CHAIN_SOURCES = frozenset(
    {"demo_synthetic", "estimated", "cache", "synthetic", "demo_or_synthetic"}
)


def is_synthetic_trendline_entry_chain(source: str) -> bool:
    """True when Trendline fill used estimated/synthetic chain pricing (not live desk fill)."""
    return str(source or "").strip().lower() in _SYNTHETIC_ENTRY_CHAIN_SOURCES


def resolve_tline_synthetic_exit_premium(
    *,
    entry_premium: float,
    live_premium: float,
    modeled_premium: Optional[float],
    mark_is_exit_grade: bool,
    premium_jump_detected: bool,
    spread_pct: Optional[float] = None,
    max_spread_pct: float = 0.30,
) -> Tuple[float, str]:
    """
    True-to-live exit mark selection.

    Prefer desk-realizable live chain premium (caller passes STC bid for longs).
    When quotes are not exit-grade / wide / synthetic, ``modeled_premium`` may
    ceiling phantom mids — never invent PnL via an artificial ``entry * 1.25`` cap
    (removed Aug 7 2026 — IREN/DELL stamped every winner at +25%).

    ``premium_jump_detected`` is retained for callers / telemetry only.
    """
    _ = bool(premium_jump_detected)
    entry = float(entry_premium or 0.0)
    live = float(live_premium or 0.0)
    modeled = float(modeled_premium) if modeled_premium is not None else 0.0
    wide_spread = _tline_spread_too_wide(spread_pct, max_spread_pct)
    if mark_is_exit_grade and not wide_spread and live > 0:
        return live, "live_exit_grade"
    if modeled > 0:
        if live > 0:
            return min(live, modeled), "modeled_cap"
        return modeled, "modeled_only"
    # No model ceiling: persist live chain mark as-is (no entry×N artificial cap).
    if live > 0:
        return live, "live_chain"
    if entry > 0:
        return entry, "entry_fallback"
    return live, "live_fallback"


def _tline_conservative_executable_premium(
    *,
    prem_detail: Dict[str, Any],
    mid_premium: float,
    option_side: str,
    use_bid_for_long: bool,
) -> float:
    """Long option exit = bid (STC); avoid inflated mids on wide spreads."""
    from modules.option_executable_pricing import (
        is_long_single_leg_option_side,
        long_option_sell_to_close_price,
    )

    side = str(option_side or "").strip().lower()
    if not use_bid_for_long or not is_long_single_leg_option_side(side):
        return float(mid_premium or 0.0)
    bid = prem_detail.get("option_bid")
    ask = prem_detail.get("option_ask")
    mark = prem_detail.get("option_mark")
    last = prem_detail.get("option_last")
    return long_option_sell_to_close_price(
        bid=bid,
        ask=ask,
        mid=mark or mid_premium,
        last=last,
    )


def _tline_spread_too_wide(spread_pct: Any, max_spread_pct: float) -> bool:
    """True only when spread is known and exceeds the max.

    Missing spread must not be treated as wide — that forced exit settlement onto
    ``entry_fallback`` whenever bid/ask were absent from prem_detail (SMH 2026-07-23).
    """
    if spread_pct is None:
        return False
    try:
        return float(spread_pct) > float(max_spread_pct)
    except (TypeError, ValueError):
        return False


def _hydrate_exit_prem_detail(
    prem_detail: Optional[Dict[str, Any]],
    *,
    osnap: Optional[Dict[str, Any]] = None,
    exit_premium: float = 0.0,
) -> Dict[str, Any]:
    """Ensure bid/ask/spread are present for settlement when exact quotes exist."""
    detail = dict(prem_detail) if isinstance(prem_detail, dict) else {}
    snap = osnap if isinstance(osnap, dict) else {}
    exact = snap.get("exact_quote") if isinstance(snap.get("exact_quote"), dict) else (snap.get("exact_contract_quote") if isinstance(snap.get("exact_contract_quote"), dict) else {})
    oq = snap.get("option_quote") if isinstance(snap.get("option_quote"), dict) else {}
    ps = snap.get("premium_snapshot") if isinstance(snap.get("premium_snapshot"), dict) else {}

    def _first(*vals: Any) -> Optional[float]:
        for v in vals:
            if v is None:
                continue
            try:
                f = float(v)
            except (TypeError, ValueError):
                continue
            if f > 0:
                return f
        return None

    bid = _first(
        detail.get("option_bid"),
        detail.get("bid"),
        exact.get("bid"),
        oq.get("bid"),
        ps.get("bid"),
        ps.get("executable_exit_price"),
        snap.get("option_bid"),
    )
    ask = _first(
        detail.get("option_ask"),
        detail.get("ask"),
        exact.get("ask"),
        oq.get("ask"),
        ps.get("ask"),
        snap.get("option_ask"),
    )
    mark = _first(
        detail.get("option_mark"),
        detail.get("mark"),
        exact.get("mark"),
        exact.get("mid"),
        oq.get("mark"),
        oq.get("mid"),
        ps.get("mark"),
        float(exit_premium or 0.0) if float(exit_premium or 0.0) > 0 else None,
    )
    if bid is not None:
        detail["option_bid"] = bid
        detail.setdefault("bid", bid)
    if ask is not None:
        detail["option_ask"] = ask
        detail.setdefault("ask", ask)
    if mark is not None:
        detail["option_mark"] = mark
        detail.setdefault("mark", mark)
    if detail.get("spread_pct") is None and bid is not None and ask is not None and ask > 0:
        mid = (bid + ask) / 2.0
        if mid > 0:
            detail["spread_pct"] = (ask - bid) / mid
    return detail


def finalize_tline_true_to_live_exit_premium(
    *,
    strategy_type: str,
    option_side: str,
    entry_chain_source: str,
    entry_premium: float,
    exit_premium: float,
    prem_detail: Optional[Dict[str, Any]],
    mark_is_exit_grade: bool,
    premium_jump_detected: bool,
    use_bid_for_long_exit: bool = True,
    max_spread_pct: float = 0.30,
    true_to_live_enabled: bool = True,
) -> Tuple[float, bool, str]:
    """Return (exit_premium, adjusted, reason) using desk-realizable marks."""
    st = str(strategy_type or "").lower()
    is_0dte_long_path = st in ("trendline_0dte", "orb_0dte", "breakout_0dte", "single_leg", "lotto") or "0dte" in st
    if not true_to_live_enabled or not is_0dte_long_path:
        return float(exit_premium or 0.0), False, ""
    detail = _hydrate_exit_prem_detail(prem_detail, exit_premium=float(exit_premium or 0.0))
    spread_pct = detail.get("spread_pct")
    wide = _tline_spread_too_wide(spread_pct, max_spread_pct)
    synth = is_synthetic_trendline_entry_chain(entry_chain_source)
    executable = _tline_conservative_executable_premium(
        prem_detail=detail,
        mid_premium=float(exit_premium or 0.0),
        option_side=option_side,
        use_bid_for_long=use_bid_for_long_exit,
    )
    # Exit-grade monitoring marks are already bid-executable for long STC. If the
    # bid field was stripped from prem_detail, do not fall through to entry_fallback.
    if executable <= 0.0 and float(exit_premium or 0.0) > 0.0 and bool(mark_is_exit_grade) and not wide:
        executable = float(exit_premium or 0.0)
    # Jump latch forced re-eval during monitor; it must NOT rewrite settlement
    # away from live chain bids (Aug 7 IREN/DELL entry×1.25 wash — removed).
    needs_conservative = bool(synth) or bool(wide) or (not bool(mark_is_exit_grade))
    if not needs_conservative:
        if abs(executable - float(exit_premium or 0.0)) > 1e-6 and executable > 0:
            return executable, True, "bid_executable"
        return float(exit_premium or 0.0), False, ""
    resolved, reason = resolve_tline_synthetic_exit_premium(
        entry_premium=float(entry_premium or 0.0),
        live_premium=executable if executable > 0 else float(exit_premium or 0.0),
        modeled_premium=detail.get("modeled_premium"),
        mark_is_exit_grade=bool(mark_is_exit_grade) and not wide,
        premium_jump_detected=bool(premium_jump_detected),
        spread_pct=spread_pct,
        max_spread_pct=max_spread_pct,
    )
    # Never replace a positive exit-grade live mark with bare entry fallback.
    if (
        reason == "entry_fallback"
        and bool(mark_is_exit_grade)
        and not wide
        and executable > 0
    ):
        return float(executable), True, "exit_grade_bid_preserved"
    if (
        reason == "entry_fallback"
        and bool(mark_is_exit_grade)
        and not wide
        and float(exit_premium or 0.0) > 0
    ):
        return float(exit_premium or 0.0), False, "exit_grade_live_preserved"
    # SMH/AMD 2026-07-23: missing bid + fee-loaded entry_fallback stamped BE
    # (entry+$0.65/100) into the latch while executable bid was 1.35 / 0.60.
    # Only for entry_fallback — never undo modeled_cap / live_chain marks.
    if (
        reason == "entry_fallback"
        and float(exit_premium or 0.0) > 0
        and float(exit_premium or 0.0) > float(resolved or 0.0) + 1e-6
    ):
        return float(exit_premium or 0.0), False, "decision_premium_preserved"
    if (
        reason == "entry_fallback"
        and float(entry_premium or 0.0) > 0
        and float(resolved or 0.0) > 0
        and float(exit_premium or 0.0) > float(resolved or 0.0) + 1e-6
        and float(resolved or 0.0) <= float(entry_premium or 0.0) * 1.02
        and float(exit_premium or 0.0) >= float(entry_premium or 0.0) * 1.03
    ):
        return float(exit_premium or 0.0), True, "reject_fee_entry_wash"
    if wide and reason == "live_exit_grade":
        reason = "wide_spread_cap"
    if synth and reason in ("live_exit_grade", "modeled_cap", "live_chain"):
        reason = f"synthetic_entry_{reason}"
    adjusted = abs(resolved - float(exit_premium or 0.0)) > 1e-6
    return resolved, adjusted, reason


def adjust_tline_exit_premium_for_synthetic_entry(
    *,
    strategy_type: str,
    entry_chain_source: str,
    entry_premium: float,
    exit_premium: float,
    prem_detail: Optional[Dict[str, Any]],
    mark_is_exit_grade: bool,
    premium_jump_detected: bool,
    option_side: str = "",
    use_bid_for_long_exit: bool = True,
    max_spread_pct: float = 0.30,
    true_to_live_enabled: bool = True,
) -> Tuple[float, bool, str]:
    """Backward-compatible wrapper — applies full true-to-live exit mark policy."""
    return finalize_tline_true_to_live_exit_premium(
        strategy_type=strategy_type,
        option_side=option_side,
        entry_chain_source=entry_chain_source,
        entry_premium=entry_premium,
        exit_premium=exit_premium,
        prem_detail=prem_detail,
        mark_is_exit_grade=mark_is_exit_grade,
        premium_jump_detected=premium_jump_detected,
        use_bid_for_long_exit=use_bid_for_long_exit,
        max_spread_pct=max_spread_pct,
        true_to_live_enabled=true_to_live_enabled,
    )


def settle_true_to_live_exit_premium(
    *,
    strategy_type: str,
    option_side: str,
    metadata: Optional[Dict[str, Any]],
    exit_premium: float,
    telem: Optional[Dict[str, Any]] = None,
    option_quote: Optional[Dict[str, Any]] = None,
    stealth_cfg: Any = None,
    entry_premium: Optional[float] = None,
) -> Tuple[float, bool, str]:
    """Demo/live-quote STC: prefer executable bid over a stale entry/current_value fill."""
    md = metadata if isinstance(metadata, dict) else {}
    osnap = md.get("option_stealth") if isinstance(md.get("option_stealth"), dict) else {}
    view = dict(osnap) if isinstance(osnap, dict) else {}
    if isinstance(option_quote, dict):
        view["option_quote"] = option_quote
    if isinstance(md.get("option_quote"), dict) and "option_quote" not in view:
        view["option_quote"] = md.get("option_quote")
    if isinstance(md.get("premium_snapshot"), dict):
        view["premium_snapshot"] = md.get("premium_snapshot")
    if isinstance(md.get("exact_quote"), dict):
        view["exact_quote"] = md.get("exact_quote")
    side = str(option_side or "").strip()
    if not side:
        side = str(md.get("option_side") or view.get("option_side") or "").strip()
    if not side:
        pid = str(md.get("trade_id") or md.get("position_id") or "")
        if re.search(r"\d+(?:\.\d+)?c(?:-|_|$)", pid, re.I) or pid.lower().endswith("c"):
            side = "call"
        elif re.search(r"\d+(?:\.\d+)?p(?:-|_|$)", pid, re.I) or pid.lower().endswith("p"):
            side = "put"
    ed: Dict[str, Any] = {}
    if isinstance(telem, dict):
        if isinstance(telem.get("exit_diagnostics"), dict):
            ed = dict(telem.get("exit_diagnostics") or {})
        elif isinstance(telem.get("premium_detail"), dict):
            ed = dict(telem.get("premium_detail") or {})
    prem_detail = _hydrate_exit_prem_detail(
        ed, osnap=view, exit_premium=float(exit_premium or 0.0)
    )
    entry = 0.0
    snap = md.get("premium_snapshot") if isinstance(md.get("premium_snapshot"), dict) else {}
    for raw in (
        entry_premium,
        md.get("entry_fill_price"),
        view.get("entry_premium"),
        snap.get("entry_fill_price"),
        md.get("entry_price"),
    ):
        try:
            v = float(raw or 0.0)
        except (TypeError, ValueError):
            v = 0.0
        if v > 0:
            entry = v
            break
    max_sp = float(getattr(stealth_cfg, "tline_exit_grade_max_spread_pct", 0.30) or 0.30)
    return finalize_tline_true_to_live_exit_premium(
        strategy_type=str(strategy_type or ""),
        option_side=str(side or ""),
        entry_chain_source=str(
            md.get("entry_chain_source")
            or view.get("entry_chain_source")
            or (md.get("execution_payload") or {}).get("chain_source")
            or ""
        ),
        entry_premium=float(entry or 0.0),
        exit_premium=float(exit_premium or 0.0),
        prem_detail=prem_detail,
        mark_is_exit_grade=bool(view.get("mark_is_exit_grade") or (md.get("premium_snapshot") or {}).get("exit_grade")),
        premium_jump_detected=bool(view.get("premium_jump_detected")),
        use_bid_for_long_exit=bool(getattr(stealth_cfg, "dte_use_bid_for_long_exit", True)),
        max_spread_pct=max_sp,
        true_to_live_enabled=bool(getattr(stealth_cfg, "dte_true_to_live_executable_marks", True)),
    )


def clamp_tline_monitor_premium_true_to_live(
    *,
    prem: float,
    detail: Dict[str, Any],
    entry_chain_source: str,
    entry_premium: float,
    option_side: str,
    cfg: Any,
) -> Tuple[float, Dict[str, Any]]:
    """During monitor, cap premium marks that are not desk-realizable (wide spread / synthetic entry)."""
    if not bool(getattr(cfg, "dte_true_to_live_executable_marks", True)):
        return prem, detail
    max_sp = float(getattr(cfg, "tline_exit_grade_max_spread_pct", 0.30) or 0.30)
    mark_exit = bool(detail.get("mark_is_exit_grade"))
    wide = _tline_spread_too_wide(detail.get("spread_pct"), max_sp)
    synth = is_synthetic_trendline_entry_chain(entry_chain_source)
    absurd = _premium_absurd_vs_entry_modeled(
        float(prem or 0.0),
        float(entry_premium or 0.0),
        detail.get("modeled_premium"),
        cfg,
    )
    if mark_exit and not wide and not synth and not absurd:
        return prem, detail
    use_bid = bool(getattr(cfg, "dte_use_bid_for_long_exit", True))
    conservative = _tline_conservative_executable_premium(
        prem_detail=detail,
        mid_premium=float(prem or 0.0),
        option_side=option_side,
        use_bid_for_long=use_bid,
    )
    modeled_f = 0.0
    try:
        modeled_f = float(detail.get("modeled_premium") or 0.0)
    except (TypeError, ValueError):
        modeled_f = 0.0
    if modeled_f > 0:
        clamped = min(conservative, modeled_f) if conservative > 0 else modeled_f
    else:
        clamped = conservative if conservative > 0 else float(prem or 0.0)
    if clamped < float(prem or 0.0) - 1e-6:
        out = dict(detail)
        out["true_to_live_monitor_clamp"] = True
        out["monitor_premium_raw"] = float(prem or 0.0)
        out["monitor_premium_clamped"] = clamped
        return clamped, out
    return prem, detail


def mfe_giveback_blocked_for_synthetic_entry(
    *,
    entry_chain_source: str,
    mark_is_exit_grade: bool,
    premium_jump_detected: bool,
    max_pnl_pct: float,
    spread_pct: Optional[float] = None,
    max_spread_pct: float = 0.30,
    drawdown_from_max_pct: float = 0.0,
    mfe_giveback_exit_pct: float = 0.07,
    premium_source: str = "",
    protection_quote_ok: bool = False,
) -> bool:
    """Block MFE giveback exits that rely on inflated HWM vs non-desk-realizable marks.

    Age/freshness blips on an otherwise trusted exact-contract mark must not defer
    a already-triggered giveback for the full ``mfe_giveback_defer_max_seconds``
    window (AMD 2026-07-23: 90s bleed after +13.5%→+1.7% while exit-grade
    flickered on ``quote_age_exceeded`` around the 3s gate).

    Discontinuous gap-down cliffs are handled separately by
    ``mfe_giveback_gap_guard_should_defer`` (DELL 2026-07-24 P0).
    """
    if _tline_spread_too_wide(spread_pct, max_spread_pct):
        return True
    if not mark_is_exit_grade:
        trusted_exact = False
        try:
            from modules.exit_grade_durability import premium_source_is_trusted_exact

            trusted_exact = bool(premium_source_is_trusted_exact(premium_source))
        except Exception:
            src_l = str(premium_source or "").strip().lower()
            trusted_exact = "exact_contract" in src_l or src_l == "exit_grade"
        # Trusted exact + protection path: allow giveback despite brief age miss.
        if not (trusted_exact and bool(protection_quote_ok)):
            return True
    if premium_jump_detected:
        # Only defer giveback while still near the spike peak; allow cleanup after real giveback.
        if float(drawdown_from_max_pct or 0.0) < float(mfe_giveback_exit_pct or 0.07):
            return True
    if is_synthetic_trendline_entry_chain(entry_chain_source):
        if float(max_pnl_pct or 0.0) > 1.0:
            return True
    return False


def mfe_giveback_adverse_gap_tick(
    *,
    premium_jump_down: bool,
    drawdown_from_max_pct: float,
    mfe_giveback_exit_pct: float,
    overshoot_pct: float,
) -> bool:
    """True when this tick looks like a quote cliff rather than a clean −Npp trail.

    DELL 2026-07-24: live HWM $4.10 (+12.3%) → $3.35 (−8.2%) in one print (~18% jump,
    ~20pp drawdown vs −10pp trail). A clean trail exit should fire near +2.3%.
    """
    if bool(premium_jump_down):
        return True
    try:
        dd = float(drawdown_from_max_pct or 0.0)
        exit_pp = float(mfe_giveback_exit_pct or 0.0)
        over = max(0.0, float(overshoot_pct or 0.0))
    except (TypeError, ValueError):
        return False
    return dd > exit_pp + over + 1e-12


def mfe_giveback_gap_quote_fingerprint(
    *,
    broker_quote_timestamp: Any = None,
    option_bid: Any = None,
    quote_age_seconds: Any = None,
    monitor_tick_id: Any = None,
    premium: Any = None,
) -> str:
    """Stable id for one broker observation used by gap-confirm integrity."""
    parts = [
        str(broker_quote_timestamp or "").strip(),
        str(monitor_tick_id or "").strip(),
    ]
    try:
        if option_bid is not None:
            parts.append(f"b={float(option_bid):.6f}")
    except (TypeError, ValueError):
        pass
    try:
        if premium is not None:
            parts.append(f"p={float(premium):.6f}")
    except (TypeError, ValueError):
        pass
    try:
        if quote_age_seconds is not None:
            parts.append(f"age={float(quote_age_seconds):.3f}")
    except (TypeError, ValueError):
        pass
    fp = "|".join(p for p in parts if p)
    return fp or ""


def mfe_giveback_gap_observation_is_fresh(
    *,
    reused_last_exact: bool = False,
    awaiting_market_data: bool = False,
    quote_fingerprint: Optional[str] = None,
    last_confirm_fingerprint: Optional[str] = None,
    premium_source: str = "",
) -> bool:
    """Gap-guard confirms require a distinct fresh broker observation.

    Cached / reused_last_exact repeats of the same cliff print must not increment
    confirmation_count (DELL-class hole → same bid re-applied each tick).
    """
    if bool(reused_last_exact):
        return False
    src = str(premium_source or "").strip().lower()
    if "reused" in src or src in {"cached_quote", "last_valid_stale", "synthetic_modeled"}:
        return False
    if bool(awaiting_market_data) and ("exact_contract" not in src):
        # Still awaiting a new exact mark — not a fresh observation for confirm.
        return False
    fp = str(quote_fingerprint or "").strip()
    last = str(last_confirm_fingerprint or "").strip()
    if fp and last and fp == last:
        return False
    return True


def mfe_giveback_gap_guard_should_defer(
    *,
    enabled: bool,
    adverse_gap_tick: bool,
    confirm_count: int,
    confirm_required: int,
    gap_pending: bool,
    observation_fresh: bool = True,
) -> Tuple[bool, int, bool]:
    """Decide whether to defer an armed ``mfe_giveback`` latch after a gap-down print.

    Returns ``(should_defer, new_confirm_count, new_gap_pending)``.

    State machine (giveback thresholds already met):
    - Adverse cliff tick on a **fresh** broker observation → pending; defer until
      ``confirm_required`` consecutive fresh adverse ticks (real crash) or a later
      continuous tick lands in the clean trail band (allow exit).
    - Adverse cliff on a **cached/reused** mark → stay deferred, do **not** increment.
    - Continuous tick with no prior gap → allow immediately (clean −10pp path).
    """
    if not enabled:
        return False, 0, False
    req = max(1, int(confirm_required or 1))
    count = max(0, int(confirm_count or 0))
    pending = bool(gap_pending)

    if adverse_gap_tick:
        pending = True
        if not bool(observation_fresh):
            # Same cached cliff — hold defer, do not advance confirmation.
            return True, count, True
        count = count + 1
        if count < req:
            return True, count, True
        # Confirmed deep/cliff giveback — allow latch (real crash / persistent hole).
        return False, count, True

    if pending:
        # Prior gap, now a continuous tick still under giveback → clean-band exit.
        return False, 0, False

    return False, 0, False


def tline_trusted_degraded_mark(
    cfg: Any,
    *,
    prem_src: str,
    mark_quality: str,
    quote_age_seconds: Any,
    synthetic_active: bool,
) -> bool:
    """Trusted non-synthetic contract marks for Trendline 0DTE protection/HWM (not modeled synth)."""
    if synthetic_active:
        return False
    if not _tline_quote_age_ok(cfg, quote_age_seconds):
        return False
    src = str(prem_src or "").strip().lower()
    mq = str(mark_quality or "").strip().lower()
    try:
        from modules.exit_grade_durability import (
            premium_source_is_live_contract_quote,
            premium_source_is_trusted_exact,
        )

        if premium_source_is_trusted_exact(src) or premium_source_is_live_contract_quote(src):
            return True
    except Exception:
        if src in (
            "exact_quote",
            "exit_grade",
            "nearest",
            "cached_quote",
            "chain_strike_match",
        ) or "exact_contract" in src:
            return True
    if mq in ("exit_grade", "live_single_leg", "live_two_leg_spread") and src not in (
        "synthetic_modeled",
        "synthetic_proxy",
        "underlying_proxy",
        "delta_estimate",
    ):
        return True
    if mq == "cached_recent" and src not in ("synthetic_modeled", "synthetic_proxy", "underlying_proxy", "delta_estimate"):
        return True
    return False


def _premium_hwm_allow_tline_single(
    cfg: Any,
    prem_src: str,
    detail: Dict[str, Any],
    *,
    prem: float,
    underlying_move_confirms: bool,
    synthetic_active: bool,
    entry_chain_source: str = "",
    entry_premium: float = 0.0,
) -> bool:
    synth_entry = is_synthetic_trendline_entry_chain(entry_chain_source)
    if bool(detail.get("absurd_live_premium_rejected")):
        return False
    if synth_entry:
        if not underlying_move_confirms:
            return False
        if not tline_trusted_degraded_mark(
            cfg,
            prem_src=prem_src,
            mark_quality=str(detail.get("mark_quality") or ""),
            quote_age_seconds=detail.get("quote_age_seconds"),
            synthetic_active=bool(synthetic_active),
        ):
            return False
        if prem <= 0:
            return False
        ep = float(entry_premium or detail.get("entry_premium") or 0.0)
        max_mult = float(getattr(cfg, "tline_synthetic_entry_hwm_max_mult", 1.5) or 1.5)
        if ep > 0 and prem > ep * max_mult:
            return False
        return _tline_bid_ask_sanity_ok(detail, prem)

    if _premium_updates_favorable_hwm(prem_src, detail, is_spread=False):
        pt = str(detail.get("price_source_telemetry") or "").strip().lower()
        if pt == "live" and underlying_move_confirms:
            return prem > 0 and _tline_bid_ask_sanity_ok(detail, prem)
    if not underlying_move_confirms:
        return False
    if not tline_trusted_degraded_mark(
        cfg,
        prem_src=prem_src,
        mark_quality=str(detail.get("mark_quality") or ""),
        quote_age_seconds=detail.get("quote_age_seconds"),
        synthetic_active=bool(synthetic_active),
    ):
        return False
    if prem <= 0:
        return False
    return _tline_bid_ask_sanity_ok(detail, prem)


def _premium_hwm_allow_orb_single(
    cfg: Any,
    prem_src: str,
    detail: Dict[str, Any],
    *,
    prem: float,
    underlying_move_confirms: bool,
    synthetic_active: bool,
    entry_premium: float = 0.0,
) -> bool:
    """ORB1 Breakout single-leg: ratchet HWM on live marks or trusted degraded + favorable underlying."""
    if bool(detail.get("absurd_live_premium_rejected")):
        return False
    ep = float(entry_premium or detail.get("entry_premium") or 0.0)
    if ep > 0 and prem > ep * float(getattr(cfg, "premium_max_mult_from_entry", 12.0) or 12.0):
        return False
    if not bool(getattr(cfg, "orb_single_trusted_degraded_hwm_enable", True)):
        return _premium_updates_favorable_hwm(prem_src, detail, is_spread=False)
    if _premium_updates_favorable_hwm(prem_src, detail, is_spread=False):
        return True
    if not underlying_move_confirms:
        return False
    if not tline_trusted_degraded_mark(
        cfg,
        prem_src=prem_src,
        mark_quality=str(detail.get("mark_quality") or ""),
        quote_age_seconds=detail.get("quote_age_seconds"),
        synthetic_active=bool(synthetic_active),
    ):
        return False
    if prem <= 0:
        return False
    return _tline_bid_ask_sanity_ok(detail, prem)


def _dte_single_exit_min_hold_seconds(cfg: Any, exit_kind: str) -> float:
    """Per-exit min-hold for 0DTE singles (0 = no min hold). No bypass paths."""
    kind = str(exit_kind or "").strip().lower()
    if kind in ("mfe_trail", "mfe_giveback", "max_pnl_drawdown", "mfe_take_profit", "premium_trail", "prem_giveback", "no_data", "degraded_data"):
        return 0.0
    if kind in ("mfe_trailing_stop", "trailing_activation", "no_progress", "time_exit"):
        return float(getattr(cfg, "dte_min_hold_seconds", 384.0) or 0.0)
    if kind in ("breakeven", "scratch"):
        return 0.0
    return float(getattr(cfg, "dte_min_hold_seconds", 384.0) or 0.0)


def _dte_single_exit_min_hold_blocks(cfg: Any, *, exit_kind: str, held_seconds: float) -> bool:
    min_sec = _dte_single_exit_min_hold_seconds(cfg, exit_kind)
    if min_sec <= 0:
        return False
    return float(held_seconds) < float(min_sec)


def _option_degraded_live_quote_should_emit(cfg: Any, dedupe_key: str) -> bool:
    """Throttle OPTION_DEGRADED_LIVE_QUOTE per dedupe_key (e.g. trade_id|symbol or skip|symbol)."""
    min_sec = float(getattr(cfg, "option_price_resolution_audit_min_seconds", 30.0) or 0.0)
    if min_sec <= 0:
        return True
    key = f"deg|{dedupe_key}"
    now = time.monotonic()
    last = _option_degraded_live_quote_last.get(key, -1e30)
    if (now - last) < min_sec:
        return False
    _option_degraded_live_quote_last[key] = now
    return True


def _emit_option_degraded_live_quote_log(
    cfg: Any,
    *,
    symbol: str,
    trade_id: str,
    reason: str,
    action_taken: str,
    dedupe_key: str,
    force: bool = False,
) -> None:
    if not bool(getattr(cfg, "require_live_quotes", False)):
        return
    if not force and not _option_degraded_live_quote_should_emit(cfg, dedupe_key):
        return
    log.warning(
        "OPTION_DEGRADED_LIVE_QUOTE | symbol=%s | trade_id=%s | reason=%s | action_taken=%s",
        str(symbol or "-"),
        str(trade_id or "-"),
        str(reason or "unknown"),
        str(action_taken or "unknown"),
    )


def _option_price_resolution_audit_should_emit(
    cfg: Any,
    trade_id: str,
    position_symbol: str,
) -> bool:
    """Throttle OPTION_PRICE_RESOLUTION_AUDIT per trade_id + symbol."""
    min_sec = float(getattr(cfg, "option_price_resolution_audit_min_seconds", 30.0) or 0.0)
    if min_sec <= 0:
        return True
    tid = str(trade_id or "").strip() or "-"
    sym = str(position_symbol or "").strip() or "-"
    key = f"{tid}|{sym}"
    now = time.monotonic()
    last = _option_price_resolution_audit_last.get(key, -1e30)
    if (now - last) < min_sec:
        return False
    _option_price_resolution_audit_last[key] = now
    return True


def _option_telemetry_log_should_emit(cfg: Any, cache: Dict[str, float], dedupe_key: str) -> bool:
    min_sec = float(getattr(cfg, "option_price_resolution_audit_min_seconds", 30.0) or 0.0)
    if min_sec <= 0:
        return True
    now = time.monotonic()
    last = cache.get(dedupe_key, -1e30)
    if (now - last) < min_sec:
        return False
    cache[dedupe_key] = now
    return True


def _apply_synthetic_conservative_premium(
    raw_synthetic: float,
    *,
    last_valid_cap: Optional[float],
    is_short_premium: bool,
    cfg: Any,
) -> float:
    """Cap modeled premium vs last live; bias conservative for long vs short premium."""
    p = max(0.01, float(raw_synthetic))
    if last_valid_cap is not None and float(last_valid_cap) > 0:
        p = min(p, float(last_valid_cap))
    long_pct = max(0.02, min(0.05, float(getattr(cfg, "option_synthetic_long_devalue_pct", 0.035) or 0.035)))
    short_pct = max(0.02, min(0.05, float(getattr(cfg, "option_synthetic_short_penalize_pct", 0.035) or 0.035)))
    if is_short_premium:
        p *= 1.0 + short_pct
    else:
        p *= 1.0 - long_pct
    return max(0.01, p)


class OptionStealthMode(str, Enum):
    INACTIVE = "inactive"
    BREAKEVEN = "breakeven"
    TRAILING = "trailing"
    EXPLOSIVE = "explosive"
    MOON = "moon"


class OptionPositionType(str, Enum):
    SINGLE_LEG_LONG_CALL = "single_leg_long_call"
    SINGLE_LEG_LONG_PUT = "single_leg_long_put"
    DEBIT_SPREAD = "debit_spread"
    CREDIT_SPREAD = "credit_spread"


@dataclass
class OptionStealthConfig:
    delta_itm_threshold: float = 0.60
    delta_atm_threshold: float = 0.40
    trailing_trigger_pct: float = 0.35
    base_trailing_pct: float = 0.22
    explosive_trailing_pct: float = 0.14
    moon_trailing_pct: float = 0.10
    explosive_pnl_threshold_pct: float = 0.50
    moon_pnl_threshold_pct: float = 1.00
    time_exit_minutes: float = 12.0
    no_progress_exit_minutes: float = 60.0
    disable_tp_ladder: bool = True
    premium_move_sensitivity: float = 2.5
    premium_max_jump_pct: float = 0.55
    premium_max_mult_from_entry: float = 12.0
    premium_min_mult_from_entry: float = 0.02
    max_stale_seconds: float = 2.0
    premium_jump_recheck_pct: float = 0.15
    force_reeval_on_premium_jump: bool = True
    spread_trailing_trigger_pct: float = 0.28
    spread_base_trailing_pct: float = 0.12
    spread_time_exit_minutes: float = 9.0
    spread_no_progress_exit_minutes: float = 6.0
    # ORB1 Breakout spread: if underlying keeps moving favorably, do not classify as no-progress.
    orb_spread_no_progress_favorable_underlying_move_pct: float = 0.0035
    # Exit-grade/staleness policy for timeout-style exits.
    timeout_min_good_mark_ticks: int = 3
    quote_exit_grade_max_age_seconds: float = 30.0
    quote_stale_max_age_seconds: float = 90.0
    freeze_timeouts_on_degraded_marks: bool = True
    require_exit_grade_for_timeouts: bool = True
    require_exit_grade_for_weak_exits: bool = True
    # ORB1 Breakout: allow time/no-progress (and matching weak exits) without exit-grade / tick gates to avoid alertless stalls.
    orb_safety_exits_bypass_exit_grade_gates: bool = True
    no_progress_favorable_underlying_move_pct: float = 0.0035
    # Unified 0DTE single-leg (ORB + Trendline singles share these; path keys are deprecated aliases).
    dte_single_no_progress_minutes: float = 60.0
    dte_single_mfe_trail_trigger_pct: float = 0.10
    dte_single_mfe_trail_exit_pct: float = 0.10
    # All-day flag. When false, ARM stays +30%. After DISCOVERY_ORB2_TL_END_PT,
    # mfe_giveback still runs with price_trail (first latch wins).
    dte_single_mfe_trail_enabled: bool = False
    # Early premium trail: dual-sided pre-12:30 protection for +175% winners.
    dte_single_early_prem_trail_enabled: bool = False
    dte_single_early_prem_trail_min_hwm_pct: float = 1.75
    dte_single_early_prem_trail_pct: float = 0.05
    dte_single_early_prem_trail_min_current_pnl_pct: float = 0.75
    dte_single_early_prem_trail_before_cutoff_only: bool = True
    # Premium arm gate for price-runner (+30% Shared). Not a take-profit bank.
    dte_single_arm_trail_pct: float = 0.30
    # Always-bank / thin TP30 REMOVED — fields kept False for loader/tests compatibility.
    dte_single_mfe_take_profit_enabled: bool = False
    dte_single_mfe_take_profit_pct: float = 0.30
    dte_single_mfe_take_profit_if_price_thin: bool = False
    dte_single_mfe_take_profit_thin_max_und_pct: float = 0.005
    # PC-PX2 (Aug 17): stall-bank + 20pp premium_trail + lock@200% + post-ARM BE.
    dte_single_px2_enabled: bool = True
    dte_single_px2_giveback_pct: float = 0.20
    dte_single_px2_lock_moon_pct: float = 2.0
    dte_single_px2_stall_ticks: int = 8
    # PX2.6: stall-bank HWM retention (0=off; Shared.env default 0.90).
    dte_single_px2_stall_min_retention_of_mfe: float = 0.90
    # PX2.6b: delay stall-bank until open premium MFE >= this (0=off).
    dte_single_px2_stall_min_mfe_pct: float = 0.50
    # PX2.6c: widen giveback after high open MFE (0 trigger/giveback = off).
    dte_single_px2_high_mfe_trail_trigger_pct: float = 1.00
    dte_single_px2_high_mfe_giveback_pct: float = 0.30
    dte_single_px2_be_floor: bool = True
    dte_single_px2_und_trail_need_pct: float = 0.005
    dte_single_px2_und_trail_prem_pct: float = 0.60
    # PC-PX2.5: existing-position exit extension after winning PX2/PX3 exits.
    dte_px25_enabled: bool = False
    dte_px25_min_prem_hwm_pct: float = 1.50
    dte_px25_exclude_ultra_clean_pnl_pct: float = 7.50
    dte_px25_exclude_ultra_clean_capture_ratio: float = 0.85
    dte_px25_price_trail_pct: float = 0.01
    dte_px25_max_extension_seconds: float = 900.0
    dte_px25_prem_floor_pct: float = 0.00
    dte_px25_mfe_thin_und_bank_enabled: bool = True
    dte_px25_mfe_thin_und_fav_hwm_pct: float = 0.003
    dte_px25_mfe_thin_und_min_prem_hwm_pct: float = 1.50
    # PC-PX3 Protected Runner (research / default OFF): intercept PX2 premium_trail only.
    dte_single_px3_enabled: bool = False
    dte_single_px3_trend_mfe_pct: float = 1.00
    dte_single_px3_trend_und_hwm_pct: float = 0.004
    dte_single_px3_price_trail_pct: float = 0.004
    dte_single_px3_trend_prem_floor_pct: float = 0.50
    dte_single_px3_moon_mfe_pct: float = 2.00
    dte_single_px3_moon_patience_seconds: float = 300.0
    dte_single_px3_moon_prem_floor_pct: float = 0.75
    dte_single_px3_ultra_clean_pnl_pct: float = 7.50
    dte_single_px3_ultra_clean_capture_ratio: float = 0.85
    dte_single_px3_clean_trend_pnl_pct: float = 2.50
    dte_single_px3_clean_trend_capture_ratio: float = 0.90
    dte_single_px3_exceptional_enabled: bool = False
    dte_single_px3_exceptional_min_current_pnl_pct: float = 1.00
    dte_single_px3_exceptional_min_prem_hwm_pct: float = 0.60
    dte_single_px3_exceptional_min_und_hwm_pct: float = 0.005
    dte_single_px3_exceptional_price_trail_pct: float = 0.005
    dte_single_px3_exceptional_prem_floor_pct: float = 0.75
    dte_single_px3_thin_lead_exceptional_enabled: bool = False
    dte_single_px3_thin_lead_exceptional_min_current_pnl_pct: float = 1.00
    dte_single_px3_thin_lead_exceptional_min_prem_hwm_pct: float = 0.60
    dte_single_px3_thin_lead_exceptional_min_und_hwm_pct: float = 0.001
    dte_single_px3_thin_lead_exceptional_price_trail_pct: float = 0.004
    dte_single_px3_thin_lead_exceptional_prem_floor_pct: float = 0.25
    dte_single_px3_high_hwm_exceptional_enabled: bool = False
    dte_single_px3_high_hwm_exceptional_min_current_pnl_pct: float = 1.00
    dte_single_px3_high_hwm_exceptional_min_prem_hwm_pct: float = 1.25
    dte_single_px3_high_hwm_exceptional_min_und_hwm_pct: float = 0.004
    dte_single_px3_high_hwm_exceptional_price_trail_pct: float = 0.004
    dte_single_px3_high_hwm_exceptional_prem_floor_pct: float = 0.50
    dte_single_px3_near_trend_enabled: bool = False
    dte_single_px3_near_trend_min_current_pnl_pct: float = 0.70
    dte_single_px3_near_trend_min_prem_hwm_pct: float = 0.90
    dte_single_px3_near_trend_min_und_hwm_pct: float = 0.0009
    dte_single_px3_near_trend_price_trail_pct: float = 0.004
    dte_single_px3_near_trend_prem_floor_pct: float = 0.50
    dte_single_px3_mfe_confirm_enabled: bool = False
    dte_single_px3_mfe_confirm_min_current_pnl_pct: float = 0.20
    dte_single_px3_mfe_confirm_min_prem_hwm_pct: float = 0.30
    dte_single_px3_mfe_confirm_min_und_hwm_pct: float = 0.0025
    dte_single_px3_mfe_confirm_normal_min_entry_age_seconds: float = 1200.0
    dte_single_px3_mfe_confirm_normal_max_entry_age_seconds: float = 1800.0
    dte_single_px3_mfe_confirm_delta_pct: float = 0.15
    dte_single_px3_mfe_confirm_window_seconds: float = 120.0
    dte_single_px3_mfe_confirm_thin_recovery_enabled: bool = True
    dte_single_px3_mfe_confirm_thin_recovery_min_current_pnl_pct: float = 0.15
    dte_single_px3_mfe_confirm_thin_recovery_deadzone_min_pnl_pct: float = 0.20
    dte_single_px3_mfe_confirm_thin_recovery_deadzone_max_pnl_pct: float = 0.35
    dte_single_px3_mfe_confirm_thin_recovery_min_und_hwm_pct: float = 0.001
    dte_single_px3_mfe_confirm_thin_recovery_max_und_hwm_pct: float = 0.0025
    dte_single_px3_mfe_confirm_thin_recovery_max_prem_hwm_pct: float = 0.75
    dte_single_px3_mfe_confirm_thin_recovery_window_seconds: float = 600.0
    dte_single_px3_mfe_confirm_thin_recovery_floor_giveback_pct: float = 0.20
    dte_single_px3_mfe_confirm_thin_recovery_runner_giveback_pct: float = 0.50
    dte_single_px3_mfe_confirm_strong_recovery_min_current_pnl_pct: float = 0.29
    dte_single_px3_mfe_confirm_strong_und_hwm_pct: float = 0.0035
    dte_single_px3_mfe_confirm_recovery_max_entry_age_seconds: float = 1200.0
    dte_single_px3_mfe_confirm_recovery_window_seconds: float = 900.0
    dte_single_px3_mfe_confirm_recovery_floor_giveback_pct: float = 0.30
    dte_single_px3_mfe_confirm_late_strong_recovery_enabled: bool = False
    dte_single_px3_mfe_confirm_late_strong_recovery_min_current_pnl_pct: float = 0.35
    dte_single_px3_mfe_confirm_late_strong_recovery_min_prem_hwm_pct: float = 0.60
    dte_single_px3_mfe_confirm_late_strong_recovery_max_prem_hwm_pct: float = 1.50
    dte_single_px3_mfe_confirm_late_strong_recovery_min_und_hwm_pct: float = 0.0075
    dte_single_px3_mfe_confirm_late_strong_recovery_max_entry_age_seconds: float = 3600.0
    dte_single_px3_mfe_confirm_price_trail_pct: float = 0.003
    dte_single_px3_mfe_confirm_prem_floor_pct: float = 0.30
    dte_single_px3_mfe_confirm_runner_giveback_pct: float = 0.20
    # Asset stop loss: −buffer under/over entry underlying; only before arm-trail (Jul 31).
    dte_single_asset_stop_enabled: bool = False
    dte_single_asset_stop_pct: float = 0.005
    dte_single_asset_stop_before_arm_trail: bool = True
    # Premium hard stop: never-armed · prem ≤ −pct · peak MFE < max (Aug 12).
    dte_single_premium_stop_enabled: bool = True
    dte_single_premium_stop_pct: float = 0.60
    dte_single_premium_stop_max_mfe_pct: float = 0.15
    dte_single_premium_stop_before_arm: bool = True
    dte_single_premium_stop_settle_at_stop: bool = True
    # Non-starter cap: never-armed · older than min hold · peak MFE < max · red enough.
    dte_single_nonstarter_stop_enabled: bool = True
    dte_single_nonstarter_stop_pct: float = 0.40
    dte_single_nonstarter_stop_max_mfe_pct: float = 0.10
    dte_single_nonstarter_stop_min_hold_minutes: float = 20.0
    dte_single_nonstarter_stop_before_arm: bool = True
    dte_single_nonstarter_stop_settle_at_stop: bool = True
    # Price runner after arm-trail gate: confirm price fav → trail / price TP / prem floor.
    dte_single_price_runner_enabled: bool = False
    dte_single_price_runner_arm_price_fav_pct: float = 0.0005
    dte_single_price_runner_confirm_ticks: int = 3
    dte_single_price_runner_trail_pct: float = 0.002
    # After premium MFE ≥ this, widen und trail (0 = off).
    dte_single_price_runner_wide_trail_mfe_pct: float = 0.60
    dte_single_price_runner_wide_trail_pct: float = 0.005
    dte_single_price_runner_take_profit_pct: float = 0.0
    dte_single_price_runner_prem_floor_pct: float = 0.0
    # When und HWM fav from entry stays < max, raise prem floor (secondary thin safety).
    dte_single_price_runner_prem_floor_price_thin_max_pct: float = 0.005
    dte_single_price_runner_prem_floor_price_thin_pct: float = 0.20
    dte_single_favorable_underlying_pct: float = 0.0035
    dte_single_exit_grade_bypass: bool = True
    # Unified 0DTE single-leg profit capture (ORB + Trendline, call + put).
    dte_min_hold_seconds: float = 384.0
    dte_trailing_trigger_pct: float = 0.30
    dte_time_exit_minutes: float = 150.0
    # Skip no_progress only once MFE giveback owns the trade (armed at +10%).
    # AAPL 2026-07-23: skip@2% left a −73% loser open until 150m time_exit.
    dte_no_progress_skip_if_mfe_pct: float = 0.10
    # Near-ARM protection: +20% to < ARM MFE blocks weak red exits until flat/green.
    dte_near_arm_protect_mfe_pct: float = 0.20
    # Pre-ARM profit protect: +20% to < ARM MFE gets a +2% protective floor.
    dte_pre_arm_profit_protect_enabled: bool = True
    dte_pre_arm_profit_protect_trigger_mfe_pct: float = 0.20
    dte_pre_arm_profit_protect_floor_pnl_pct: float = 0.02
    dte_pre_arm_profit_protect_min_current_pnl_pct: float = -0.05
    dte_pre_arm_profit_protect_min_hold_minutes: float = 30.0
    dte_pre_arm_profit_protect_min_seconds_since_hwm: float = 600.0
    dte_pre_arm_profit_protect_settle_at_floor: bool = True
    # Low-ARM rescue: +10% to <20% MFE weak/degraded exits settle at BE.
    dte_low_arm_profit_protect_enabled: bool = True
    dte_low_arm_profit_protect_trigger_mfe_pct: float = 0.10
    dte_low_arm_profit_protect_max_mfe_pct: float = 0.20
    dte_low_arm_profit_protect_floor_pnl_pct: float = 0.0
    dte_low_arm_profit_protect_min_current_pnl_pct: float = -0.05
    dte_low_arm_profit_protect_min_hold_minutes: float = 20.0
    dte_low_arm_profit_protect_min_seconds_since_hwm: float = 600.0
    dte_low_arm_profit_protect_settle_at_floor: bool = True
    dte_early_be_enabled: bool = True
    dte_early_be_max_mfe_pct: float = 0.20
    dte_early_be_floor_pnl_pct: float = 0.0
    dte_early_be_min_current_pnl_pct: float = -0.05
    dte_early_be_min_hold_minutes: float = 5.0
    dte_early_be_min_seconds_since_hwm: float = 0.0
    dte_early_be_settle_at_floor: bool = True
    dte_weak_loss_cap_enabled: bool = True
    dte_weak_loss_cap_max_mfe_pct: float = 0.30
    dte_weak_loss_cap_floor_pnl_pct: float = -0.10
    dte_weak_loss_cap_settle_at_floor: bool = True
    # Bid-aware working BE: when EARLY_BE / PRE_ARM / LOW_ARM / CAP_weak fire red,
    # arm a short wait for liquidable bid reclaim to target (default 0% BE) instead
    # of immediately exiting at the then-red bid. Hard-stop / timeout / late-bloomer cancel.
    dte_protect_bid_aware_be_enabled: bool = True
    dte_protect_bid_aware_be_wait_minutes: float = 2.0
    dte_protect_bid_aware_be_hard_stop_pct: float = -0.25
    dte_protect_bid_aware_be_cancel_if_peak_above_rule_max: bool = True
    dte_protect_bid_aware_be_target_pnl_pct: float = 0.0
    # Never-armed BE/scratch bands (Aug 11): BE ≥60m [−0.05%, +2%); scratch ≥62m < −0.05%.
    dte_breakeven_exit_enabled: bool = True
    dte_breakeven_exit_minutes: float = 60.0
    # Exit BE when premium PnL < this after BE window (default +2%).
    dte_breakeven_exit_max_pnl_pct: float = 0.02
    # Scratch band: premium PnL < this (default −0.05%); else breakeven band.
    dte_scratch_below_pnl_pct: float = -0.0005
    # Scratch wall hold (can differ from BE minutes).
    dte_scratch_exit_minutes: float = 62.0
    # Early scratch when MFE < max and red (Aug 12 max-book PC).
    dte_scratch_early_minutes: float = 30.0
    dte_scratch_early_max_mfe_pct: float = 0.10
    # Early no-progress when MFE < max (Aug 12).
    dte_single_no_progress_early_minutes: float = 30.0
    dte_single_no_progress_early_max_mfe_pct: float = 0.05
    # When giveback is blocked (non–exit-grade), force trusted-bid exit after this many seconds.
    mfe_giveback_defer_max_seconds: float = 90.0
    # P0 gap-down guard (DELL 2026-07-24): defer cliff giveback until confirmed or clean trail.
    mfe_giveback_gap_guard_enabled: bool = True
    mfe_giveback_gap_confirm_ticks: int = 2
    mfe_giveback_gap_overshoot_pct: float = 0.05
    # Phase 2 Hold Exit — Hold@0.55 (reclaim→re-trail −10pp). Dir% gate at first −10pp fire.
    hold_exit_enabled: bool = False
    hold_exit_trigger_giveback_pct: float = 0.10
    hold_exit_retrail_giveback_pct: float = 0.10
    hold_exit_reclaim_minutes: float = 10.0
    hold_exit_max_minutes: float = 45.0
    hold_exit_hard_floor_pnl_pct: float = -0.05
    hold_exit_require_trigger_bid_floor: bool = False
    hold_exit_dir_volume_min_pct: float = 0.55
    hold_exit_require_dir_volume: bool = True
    # Moon hold (Aug 12 H2) removed Aug 17 — +60% MFE → 0.50% price_trail is the runner.
    exit_trailing_stop_enabled: bool = False
    orb_trailing_trigger_mult: float = 0.85
    # Trendline 0DTE: thesis can take >12m; optional overlays (0 time = use global time_exit).
    tline_time_exit_minutes: float = 240.0
    tline_no_progress_exit_minutes: float = 6.0
    tline_min_hold_seconds: float = 240.0
    tline_breakeven_activate_pct: float = 0.22
    tline_breakeven_offset_pct: float = 0.03
    tline_trail_activate_pct: float = 0.28
    tline_trail_giveback_pct: float = 0.22
    tline_impulse_breakeven_activate_pct: float = 0.18
    tline_impulse_trail_activate_pct: float = 0.24
    tline_impulse_trail_giveback_pct: float = 0.14
    tline_slow_trend_breakeven_activate_pct: float = 0.30
    tline_slow_trend_trail_activate_pct: float = 0.50
    tline_slow_trend_trail_giveback_pct: float = 0.35
    tline_retest_breakeven_activate_pct: float = 0.25
    tline_retest_trail_activate_pct: float = 0.40
    tline_retest_trail_giveback_pct: float = 0.30
    tline_be_trigger_mult: float = 0.50
    tline_trailing_trigger_mult: float = 0.82
    tline_be_delta_high_threshold: float = 0.50
    tline_be_delta_mid_threshold: float = 0.35
    tline_be_trigger_high_delta_pct: float = 0.10
    tline_be_trigger_mid_delta_pct: float = 0.12
    tline_be_trigger_low_delta_pct: float = 0.15
    tline_min_seconds_before_be: float = 60.0
    tline_base_trailing_pct: float = 0.22
    tline_explosive_trailing_pct: float = 0.14
    tline_runner_trailing_pct: float = 0.10
    tline_explosive_trigger_pct: float = 0.55
    tline_runner_trigger_pct: float = 0.85
    tline_aggressive_trailing_pct: float = 0.18
    # Trendline single-leg: "early" no-progress scratch (runs before BE/trailing gates). Keep conservative:
    # do not cut while structure is still valid or underlying has moved favorably vs entry — see evaluate path.
    tline_no_progress_early_minutes: float = 7.0
    tline_no_progress_early_max_pnl_pct: float = 0.05
    tline_degraded_non_exact_tighten_after_seconds: float = 150.0
    tline_degraded_non_exact_trail_tighten_scale: float = 0.90
    tline_min_seconds_after_be_activation: float = 20.0
    tline_degraded_exit_min_duration_seconds: float = 10.0
    # Trendline single-leg: while pnl >= this, skip time_exit and no_progress exits (slow thesis grind).
    tline_chop_hold_min_pnl_pct: float = 0.10
    # Trendline impulse mode: quick capture profile.
    tline_impulse_tp_target_pct: float = 0.50
    tline_impulse_trailing_trigger_pct: float = 0.24
    tline_impulse_trailing_pct: float = 0.12
    tline_impulse_time_exit_minutes: float = 12.0
    tline_impulse_no_progress_exit_minutes: float = 5.0
    tline_early_trailing_trigger_pct: float = 0.30
    tline_early_trailing_pct: float = 0.24
    tline_early_time_exit_minutes: float = 30.0
    tline_aggressive_trigger_pct: float = 0.45
    # Trendline 0DTE: optional giveback exit (mirrors ORB reversal protection, config-driven).
    tline_drawdown_exit_min_max_pnl_pct: float = 0.10
    tline_drawdown_exit_min_drawdown_pct: float = 0.07
    tline_option_no_data_grace_seconds: float = 180.0
    tline_option_force_exit_no_data_seconds: float = 300.0
    tline_option_require_exit_grade_before_force_exit: bool = True
    tline_min_diagnostic_hold_seconds: float = 120.0
    tline_no_data_favorable_underlying_move_pct: float = 0.0010
    # True-to-live: reject wide-spread marks for exit-grade; use bid + modeled caps on exit/monitor.
    tline_exit_grade_max_spread_pct: float = 0.30
    tline_true_to_live_exit_marks: bool = True
    tline_use_bid_for_long_exit: bool = True
    tline_synthetic_entry_hwm_max_mult: float = 1.5
    # 0DTE single-leg (ORB + Trendline): BTO @ ask / STC @ bid executable marks.
    dte_true_to_live_executable_marks: bool = True
    dte_use_bid_for_long_exit: bool = True
    require_live_option_data: bool = True
    # Strict single-leg pricing: never use delta/underlying proxy; hold last/entry until live bid/ask/mark.
    require_live_quotes: bool = False
    option_quote_fresh_max_age_seconds: float = 2.0
    option_0dte_fast_underlying_move_abs: float = 0.50
    option_0dte_fast_entry_minutes: float = 10.0
    option_price_resolution_audit_min_seconds: float = 30.0
    allow_safety_exits_on_degraded_data: bool = True
    no_data_outage_seconds: float = 90.0
    # Max seconds without a fresh live option quote (bid/ask/mark/last) before fail-safe exit (0DTE single-leg).
    option_max_degraded_seconds: float = 90.0
    option_synthetic_long_devalue_pct: float = 0.035
    option_synthetic_short_penalize_pct: float = 0.035
    position_health_log_sec: float = 60.0
    degraded_tighten_after_minutes: float = 5.0
    degraded_trailing_trigger_scale: float = 0.80
    degraded_trailing_pct_scale: float = 0.75
    orb_spread_open_grace_seconds: float = 60.0
    orb_spread_open_grace_max_attempts: int = 6
    orb_spread_open_grace_enable_synthetic: bool = True
    # ORB1 Breakout single-leg @ 7:30: block weak exits until SO-mirror BE window (Rev 00196 = 6.4 min).
    orb_min_hold_seconds: float = 384.0
    orb_opening_no_progress_minutes: float = 60.0
    orb_opening_time_exit_minutes: float = 150.0
    orb_opening_window_max_minutes: float = 25.0
    orb_opening_no_progress_favorable_underlying_move_pct: float = 0.0015
    orb_no_progress_skip_if_mfe_pct: float = 0.02
    orb_no_progress_underlying_defer_move_pct: float = 0.0015
    orb_no_progress_underlying_defer_minutes: float = 5.0
    # ORB1 Breakout debit/credit spreads: avoid indefinite timeout holds when quotes stay partial_leg / non–exit-grade.
    orb_options_spread_degraded_exit_enable: bool = True
    orb_options_spread_conservative_exit_enable: bool = True
    orb_spread_conservative_min_trade_minutes: float = 5.0
    orb_spread_conservative_min_partial_minutes: float = 4.0
    orb_spread_conservative_min_deferrals: int = 15
    orb_spread_conservative_max_leg_age_sec: float = 240.0
    orb_spread_forced_min_trade_minutes: float = 28.0
    orb_spread_forced_min_partial_minutes: float = 20.0
    orb_spread_forced_min_deferrals: int = 55
    orb_spread_forced_watchdog_accum_seconds: float = 480.0
    orb_spread_forced_abs_max_minutes: float = 90.0
    orb_spread_forced_allow_incomplete_mtm: bool = True
    # ORB1 Breakout spread: trusted degraded marks (partial_leg / cached paths) for timeout-style exits without HWM ratchet.
    orb_trusted_degraded_spread_enable: bool = True
    orb_trusted_degraded_mark_max_age_seconds: float = 90.0
    orb_trusted_degraded_min_fresh_leg_age_seconds: float = 45.0
    orb_underlying_opportunity_pct: float = 0.0025
    orb_degraded_protect_enable: bool = True
    orb_spread_degraded_time_exit_slack_minutes: float = 1.5
    orb_spread_degraded_no_progress_slack_minutes: float = 1.5
    orb_no_progress_relax_exit_grade_min_minutes: float = 11.0
    orb_no_progress_relax_max_mfe_pct: float = 0.06
    orb_watchdog_trusted_skip_clock_freeze: bool = True
    orb_degraded_drawdown_tighten_scale: float = 0.88
    # Unified 0DTE premium protections (applies via profile resolution across ORB + Trendline).
    max_pnl_drawdown_enabled: bool = True
    max_pnl_drawdown_trigger_pct: float = 0.10
    max_pnl_drawdown_exit_pct: float = 0.07
    orb_mfe_trigger_pct: float = 0.08
    orb_mfe_drawdown_exit_pct: float = 0.055
    tline_impulse_mfe_trigger_pct: float = 0.10
    tline_impulse_mfe_drawdown_exit_pct: float = 0.07
    tline_retest_mfe_trigger_pct: float = 0.12
    tline_retest_mfe_drawdown_exit_pct: float = 0.08
    tline_slow_mfe_trigger_pct: float = 0.15
    tline_slow_mfe_drawdown_exit_pct: float = 0.10
    # Trendline 0DTE: underlying excursion + min-hold bypass + trusted degraded marks (May 2026).
    tline_underlying_opportunity_pct: float = 0.0025
    tline_underlying_reclaim_protect: bool = True
    tline_impulse_trail_activation_cap_degraded: float = 0.22
    tline_min_hold_bypass_mfe_pct: float = 0.05
    orb_min_hold_bypass_mfe_pct: float = 0.05
    dte_single_min_hold_bypass_mfe_pct: float = 0.05
    tline_be_relax_hwm_with_underlying: bool = True
    tline_be_relax_hwm_min_pnl_pct: float = 0.02
    tline_no_progress_single_relax_exit_grade: bool = True
    tline_no_progress_single_max_mfe_pct: float = 0.05
    tline_be_giveback_exit_drawdown_pct: float = 0.06
    orb_no_progress_single_relax_exit_grade: bool = True
    orb_single_trusted_degraded_hwm_enable: bool = True
    orb_opening_trail_enabled: bool = False
    orb_opening_hwm_max_pnl_pct: float = 0.50
    orb_opening_hwm_min_cap_pnl_pct: float = 0.25
    orb_opening_hwm_underlying_full_trust_pct: float = 0.03
    option_stealth_state_log_seconds: float = 15.0


@dataclass
class OptionExitProfile:
    source_path: str
    position_type: str
    entry_archetype: str
    trail_trigger_pct: float
    trail_distance_pct: float
    max_pnl_drawdown_trigger_pct: float
    max_pnl_drawdown_exit_pct: float
    no_progress_minutes: float
    time_exit_minutes: float
    min_hold_seconds: float
    allow_underlying_structure_exit: bool
    max_pnl_drawdown_enabled: bool = True
    explosive_pnl_threshold_pct: float = 0.50
    moon_pnl_threshold_pct: float = 1.00


def log_trendline_stealth_config_loaded(cfg: "OptionStealthConfig") -> None:
    """Emit resolved Trendline stealth knobs (defaults are explicit in code / env loader)."""
    log.info(
        "TRENDLINE_STEALTH_CONFIG_LOADED | tline_time_exit_minutes=%.2f | tline_no_progress_exit_minutes=%.2f | "
        "tline_degraded_exit_min_duration_seconds=%.2f | tline_min_seconds_after_be_activation=%.2f | "
        "tline_be_delta_high=%.3f | tline_be_delta_mid=%.3f | tline_be_trigger_high_delta_pct=%.4f | "
        "tline_be_trigger_mid_delta_pct=%.4f | tline_be_trigger_low_delta_pct=%.4f | "
        "tline_base_trailing_pct=%.4f | tline_aggressive_trailing_pct=%.4f | tline_explosive_trailing_pct=%.4f | "
        "tline_runner_trailing_pct=%.4f | tline_aggressive_trigger_pct=%.4f | tline_explosive_trigger_pct=%.4f | "
        "tline_runner_trigger_pct=%.4f | tline_drawdown_exit_min_max_pnl_pct=%.4f | tline_drawdown_exit_min_drawdown_pct=%.4f",
        float(cfg.tline_time_exit_minutes),
        float(cfg.tline_no_progress_exit_minutes),
        float(cfg.tline_degraded_exit_min_duration_seconds),
        float(cfg.tline_min_seconds_after_be_activation),
        float(cfg.tline_be_delta_high_threshold),
        float(cfg.tline_be_delta_mid_threshold),
        float(cfg.tline_be_trigger_high_delta_pct),
        float(cfg.tline_be_trigger_mid_delta_pct),
        float(cfg.tline_be_trigger_low_delta_pct),
        float(cfg.tline_base_trailing_pct),
        float(cfg.tline_aggressive_trailing_pct),
        float(cfg.tline_explosive_trailing_pct),
        float(cfg.tline_runner_trailing_pct),
        float(cfg.tline_aggressive_trigger_pct),
        float(cfg.tline_explosive_trigger_pct),
        float(cfg.tline_runner_trigger_pct),
        float(cfg.tline_drawdown_exit_min_max_pnl_pct),
        float(cfg.tline_drawdown_exit_min_drawdown_pct),
    )


def log_trendline_exit_config_audit(cfg: "OptionStealthConfig") -> None:
    """Cloud-friendly consolidated Trendline exit policy (grep=TRENDLINE_EXIT_CONFIG_AUDIT)."""
    log.info(
        "TRENDLINE_EXIT_CONFIG_AUDIT | grep=TRENDLINE_EXIT_CONFIG_AUDIT | "
        "tline_min_hold_seconds=%.1f | tline_be_delta_high=%.3f | tline_be_delta_mid=%.3f | "
        "tline_be_trigger_high_delta_pct=%.4f | tline_be_trigger_mid_delta_pct=%.4f | tline_be_trigger_low_delta_pct=%.4f | "
        "tline_impulse_trailing_trigger_pct=%.4f | tline_impulse_trailing_pct=%.4f | tline_impulse_trail_cap_degraded=%.4f | "
        "tline_impulse_no_progress_min=%.2f | tline_no_progress_min=%.2f | tline_time_exit_min=%.2f | "
        "require_exit_grade_timeouts=%s | require_exit_grade_weak=%s | freeze_timeouts_degraded=%s | "
        "tline_underlying_opportunity_pct=%.5f | tline_underlying_reclaim_protect=%s | quote_stale_max_age_sec=%.1f | "
        "tline_no_progress_single_relax_exit_grade=%s | tline_no_progress_single_max_mfe_pct=%.4f | "
        "tline_be_relax_hwm_with_underlying=%s | tline_be_relax_hwm_min_pnl_pct=%.4f | tline_min_hold_bypass_mfe_pct=%.4f | "
        "option_stealth_state_log_seconds=%.1f",
        float(cfg.tline_min_hold_seconds),
        float(cfg.tline_be_delta_high_threshold),
        float(cfg.tline_be_delta_mid_threshold),
        float(cfg.tline_be_trigger_high_delta_pct),
        float(cfg.tline_be_trigger_mid_delta_pct),
        float(cfg.tline_be_trigger_low_delta_pct),
        float(cfg.tline_impulse_trailing_trigger_pct),
        float(cfg.tline_impulse_trailing_pct),
        float(cfg.tline_impulse_trail_activation_cap_degraded),
        float(cfg.tline_impulse_no_progress_exit_minutes),
        float(cfg.tline_no_progress_exit_minutes),
        float(cfg.tline_time_exit_minutes),
        str(bool(cfg.require_exit_grade_for_timeouts)).lower(),
        str(bool(cfg.require_exit_grade_for_weak_exits)).lower(),
        str(bool(cfg.freeze_timeouts_on_degraded_marks)).lower(),
        float(cfg.tline_underlying_opportunity_pct),
        str(bool(cfg.tline_underlying_reclaim_protect)).lower(),
        float(cfg.quote_stale_max_age_seconds),
        str(bool(cfg.tline_no_progress_single_relax_exit_grade)).lower(),
        float(cfg.tline_no_progress_single_max_mfe_pct),
        str(bool(cfg.tline_be_relax_hwm_with_underlying)).lower(),
        float(cfg.tline_be_relax_hwm_min_pnl_pct),
        float(cfg.tline_min_hold_bypass_mfe_pct),
        float(cfg.option_stealth_state_log_seconds),
    )


def _config_first_present(g: Callable[[str, Any], Any], key: str) -> bool:
    raw = g(key, None)
    if raw is None:
        return False
    token = str(raw).strip().lower()
    return token not in ("", "none", "na", "n/a")


def _config_first_float(
    g: Callable[[str, Any], Any],
    keys: tuple,
    default: float,
) -> float:
    for key in keys:
        if not _config_first_present(g, key):
            continue
        try:
            return float(g(key, default))
        except (TypeError, ValueError):
            continue
    return float(default)


def _config_first_bool(
    g: Callable[[str, Any], Any],
    keys: tuple,
    default: bool,
) -> bool:
    for key in keys:
        if not _config_first_present(g, key):
            continue
        v = g(key, default)
        if isinstance(v, bool):
            return v
        s = str(v).strip().lower()
        if s in ("true", "1", "yes", "on"):
            return True
        if s in ("false", "0", "no", "off"):
            return False
    return bool(default)


def load_option_stealth_config(
    get_config_value_fn: Optional[Callable[[str, Any], Any]] = None,
) -> OptionStealthConfig:
    g = get_config_value_fn or get_config_value

    def f(key: str, default: float) -> float:
        try:
            return float(g(key, default))
        except (TypeError, ValueError):
            return float(default)

    def i(key: str, default: float) -> float:
        return float(f(key, default))

    def b(key: str, default: bool) -> bool:
        v = g(key, default)
        if isinstance(v, bool):
            return v
        s = str(v).strip().lower()
        if s in ("true", "1", "yes", "on"):
            return True
        if s in ("false", "0", "no", "off"):
            return False
        return bool(default)

    def _pick(*keys: str, default=None):
        """Prefer first key; fall back through legacy aliases for one release."""
        for key in keys:
            v = g(key, None)
            if v is None:
                continue
            if isinstance(v, str) and not str(v).strip():
                continue
            return v
        return default

    def _pick_b(*keys: str, default: bool = False) -> bool:
        v = _pick(*keys, default=default)
        if isinstance(v, bool):
            return v
        s = str(v).strip().lower()
        if s in ("true", "1", "yes", "on"):
            return True
        if s in ("false", "0", "no", "off"):
            return False
        return bool(default)

    def _pick_f(*keys: str, default: float = 0.0) -> float:
        try:
            return float(_pick(*keys, default=default))
        except (TypeError, ValueError):
            return float(default)

    no_data_outage_default = f("OPTION_STEALTH_NO_DATA_OUTAGE_SECONDS", 90.0)

    dte_single_no_progress_minutes = _pick_f(
        "OPTION_SINGLE_NO_PROGRESS_MINUTES",
        "OPTION_STEALTH_0DTE_SINGLE_NO_PROGRESS_MINUTES",
        default=60.0,
    )
    dte_single_mfe_trail_trigger_pct = f("OPTION_SINGLE_MFE_TRAIL_TRIGGER", 0.10)
    dte_single_mfe_trail_exit_pct = f("OPTION_SINGLE_MFE_TRAIL_EXIT", 0.10)
    # All-day MFE trail REMOVED (recut ARM +10%). 12:30 companion uses trigger/exit only.
    dte_single_mfe_trail_enabled = False
    dte_single_early_prem_trail_enabled = _pick_b(
        "PX2_EARLY_PREM_TRAIL_ENABLED",
        "OPTION_SINGLE_EARLY_PREM_TRAIL_ENABLED",
        default=False,
    )
    dte_single_early_prem_trail_min_hwm_pct = _pick_f(
        "PX2_EARLY_PREM_TRAIL_MIN_HWM_PCT",
        "OPTION_SINGLE_EARLY_PREM_TRAIL_MIN_HWM_PCT",
        default=1.75,
    )
    dte_single_early_prem_trail_pct = _pick_f(
        "PX2_EARLY_PREM_TRAIL_PCT",
        "OPTION_SINGLE_EARLY_PREM_TRAIL_PCT",
        default=0.05,
    )
    dte_single_early_prem_trail_min_current_pnl_pct = _pick_f(
        "PX2_EARLY_PREM_TRAIL_MIN_CURRENT_PNL_PCT",
        "OPTION_SINGLE_EARLY_PREM_TRAIL_MIN_CURRENT_PNL_PCT",
        default=0.75,
    )
    dte_single_early_prem_trail_before_cutoff_only = _pick_b(
        "PX2_EARLY_PREM_TRAIL_BEFORE_CUTOFF_ONLY",
        "OPTION_SINGLE_EARLY_PREM_TRAIL_BEFORE_CUTOFF_ONLY",
        default=True,
    )
    dte_single_arm_trail_pct = _pick_f(
        "0DTE_ARM_TRAIL_PCT",
        "OPTION_SINGLE_ARM_TRAIL_PCT",
        default=0.30,
    )
    # Always-bank TP30 REMOVED — ignore env if present.
    dte_single_mfe_take_profit_enabled = False
    dte_single_mfe_take_profit_pct = _pick_f(
        "0DTE_MFE_TAKE_PROFIT_PCT",
        "OPTION_SINGLE_MFE_TAKE_PROFIT_PCT",
        default=0.30,
    )
    # Canonical 0DTE_* / PX2_* / PX25_* / PX3_*; legacy SINGLE_* and OPTION_SINGLE_* aliases accepted.
    # Thin TP30 REMOVED — ignore env if present.
    dte_single_mfe_take_profit_if_price_thin = False
    dte_single_px2_enabled = _pick_b(
        "PX2_ENABLED",
        "SINGLE_PX2_ENABLED",
        "OPTION_SINGLE_PX2_ENABLED",
        default=True,
    )
    dte_single_px2_giveback_pct = _pick_f(
        "PX2_TRAIL_PCT",
        "PX2_GIVEBACK_PCT",
        "SINGLE_PX2_GIVEBACK_PCT",
        "OPTION_SINGLE_PX2_GIVEBACK_PCT",
        default=0.20,
    )
    dte_single_px2_lock_moon_pct = _pick_f(
        "PX2_LOCK_MOON_PCT",
        "SINGLE_PX2_LOCK_MOON_PCT",
        "OPTION_SINGLE_PX2_LOCK_MOON_PCT",
        default=2.0,
    )
    dte_single_px2_stall_ticks = int(
        max(
            1,
            _pick_f(
                "PX2_STALL_TICKS",
                "SINGLE_PX2_STALL_TICKS",
                "OPTION_SINGLE_PX2_STALL_TICKS",
                default=8,
            ),
        )
    )
    dte_single_px2_stall_min_retention_of_mfe = max(
        0.0,
        float(
            _pick_f(
                "PX2_STALL_MIN_RETENTION_OF_MFE",
                "SINGLE_PX2_STALL_MIN_RETENTION_OF_MFE",
                "OPTION_SINGLE_PX2_STALL_MIN_RETENTION_OF_MFE",
                default=0.90,
            )
            or 0.0
        ),
    )
    dte_single_px2_stall_min_mfe_pct = max(
        0.0,
        float(
            _pick_f(
                "PX2_STALL_MIN_MFE_PCT",
                "SINGLE_PX2_STALL_MIN_MFE_PCT",
                "OPTION_SINGLE_PX2_STALL_MIN_MFE_PCT",
                default=0.50,
            )
            or 0.0
        ),
    )
    dte_single_px2_high_mfe_trail_trigger_pct = max(
        0.0,
        float(
            _pick_f(
                "PX2_HIGH_MFE_TRAIL_TRIGGER_PCT",
                "SINGLE_PX2_HIGH_MFE_TRAIL_TRIGGER_PCT",
                "OPTION_SINGLE_PX2_HIGH_MFE_TRAIL_TRIGGER_PCT",
                default=1.00,
            )
            or 0.0
        ),
    )
    dte_single_px2_high_mfe_giveback_pct = max(
        0.0,
        float(
            _pick_f(
                "PX2_HIGH_MFE_TRAIL_PCT",
                "SINGLE_PX2_HIGH_MFE_TRAIL_PCT",
                "OPTION_SINGLE_PX2_HIGH_MFE_TRAIL_PCT",
                default=0.30,
            )
            or 0.0
        ),
    )
    dte_single_px2_be_floor = _pick_b(
        "PX2_BE_FLOOR",
        "SINGLE_PX2_BE_FLOOR",
        "OPTION_SINGLE_PX2_BE_FLOOR",
        default=True,
    )
    dte_single_px2_und_trail_need_pct = _pick_f(
        "PX2_PRICE_TRAIL_NEED_PCT",
        "SINGLE_PX2_UND_TRAIL_NEED_PCT",
        "OPTION_SINGLE_PX2_UND_TRAIL_NEED_PCT",
        default=0.005,
    )
    dte_single_px2_und_trail_prem_pct = _pick_f(
        "PX2_PRICE_TRAIL_PREM_PCT",
        "SINGLE_PX2_UND_TRAIL_PREM_PCT",
        "OPTION_SINGLE_PX2_UND_TRAIL_PREM_PCT",
        default=0.60,
    )
    dte_px25_enabled = _pick_b("PX25_ENABLED", default=False)
    dte_px25_min_prem_hwm_pct = _pick_f("PX25_MIN_PREM_HWM_PCT", default=1.50)
    dte_px25_exclude_ultra_clean_pnl_pct = _pick_f(
        "PX25_EXCLUDE_ULTRA_CLEAN_PNL_PCT", default=7.50
    )
    dte_px25_exclude_ultra_clean_capture_ratio = _pick_f(
        "PX25_EXCLUDE_ULTRA_CLEAN_CAPTURE_RATIO", default=0.85
    )
    dte_px25_price_trail_pct = _pick_f("PX25_PRICE_TRAIL_PCT", default=0.01)
    dte_px25_max_extension_seconds = _pick_f("PX25_MAX_EXTENSION_SECONDS", default=900.0)
    dte_px25_prem_floor_pct = _pick_f("PX25_PREM_FLOOR_PCT", default=0.00)
    dte_px25_mfe_thin_und_bank_enabled = _pick_b("PX25_MFE_THIN_UND_BANK_ENABLED", default=True)
    dte_px25_mfe_thin_und_fav_hwm_pct = _pick_f("PX25_MFE_THIN_UND_FAV_HWM_PCT", default=0.003)
    dte_px25_mfe_thin_und_min_prem_hwm_pct = _pick_f(
        "PX25_MFE_THIN_UND_MIN_PREM_HWM_PCT", default=1.50
    )
    dte_single_px3_enabled = _pick_b(
        "PX3_ENABLED",
        "SINGLE_PX3_ENABLED",
        "OPTION_SINGLE_PX3_ENABLED",
        default=False,
    )
    dte_single_px3_trend_mfe_pct = _pick_f("PX3_TREND_MFE_PCT", default=1.00)
    dte_single_px3_trend_und_hwm_pct = _pick_f(
        "PX3_TREND_PRICE_HWM_PCT",
        "PX3_TREND_UND_HWM_PCT",
        default=0.004,
    )
    dte_single_px3_price_trail_pct = _pick_f("PX3_PRICE_TRAIL_PCT", default=0.004)
    dte_single_px3_trend_prem_floor_pct = _pick_f("PX3_TREND_PREM_FLOOR_PCT", default=0.50)
    dte_single_px3_moon_mfe_pct = _pick_f("PX3_MOON_MFE_PCT", default=2.00)
    dte_single_px3_moon_patience_seconds = _pick_f("PX3_MOON_PATIENCE_SECONDS", default=300.0)
    dte_single_px3_moon_prem_floor_pct = _pick_f("PX3_MOON_PREM_FLOOR_PCT", default=0.75)
    dte_single_px3_ultra_clean_pnl_pct = _pick_f("PX3_ULTRA_CLEAN_PNL_PCT", default=7.50)
    dte_single_px3_ultra_clean_capture_ratio = _pick_f(
        "PX3_ULTRA_CLEAN_CAPTURE_RATIO", default=0.85
    )
    dte_single_px3_clean_trend_pnl_pct = _pick_f("PX3_CLEAN_TREND_PNL_PCT", default=2.50)
    dte_single_px3_clean_trend_capture_ratio = _pick_f(
        "PX3_CLEAN_TREND_CAPTURE_RATIO", default=0.90
    )
    dte_single_px3_exceptional_enabled = _pick_b("PX3_EXCEPTIONAL_ENABLED", default=False)
    dte_single_px3_exceptional_min_current_pnl_pct = _pick_f(
        "PX3_EXCEPTIONAL_MIN_CURRENT_PNL_PCT", default=1.00
    )
    dte_single_px3_exceptional_min_prem_hwm_pct = _pick_f(
        "PX3_EXCEPTIONAL_MIN_PREM_HWM_PCT", default=0.60
    )
    dte_single_px3_exceptional_min_und_hwm_pct = _pick_f(
        "PX3_EXCEPTIONAL_MIN_PRICE_HWM_PCT",
        "PX3_EXCEPTIONAL_MIN_UND_HWM_PCT",
        default=0.005,
    )
    dte_single_px3_exceptional_price_trail_pct = _pick_f(
        "PX3_EXCEPTIONAL_PRICE_TRAIL_PCT", default=0.005
    )
    dte_single_px3_exceptional_prem_floor_pct = _pick_f(
        "PX3_EXCEPTIONAL_PREM_FLOOR_PCT", default=0.75
    )
    dte_single_px3_thin_lead_exceptional_enabled = _pick_b(
        "PX3_THIN_LEAD_EXCEPTIONAL_ENABLED", default=False
    )
    dte_single_px3_thin_lead_exceptional_min_current_pnl_pct = _pick_f(
        "PX3_THIN_LEAD_EXCEPTIONAL_MIN_CURRENT_PNL_PCT", default=1.00
    )
    dte_single_px3_thin_lead_exceptional_min_prem_hwm_pct = _pick_f(
        "PX3_THIN_LEAD_EXCEPTIONAL_MIN_PREM_HWM_PCT", default=0.60
    )
    dte_single_px3_thin_lead_exceptional_min_und_hwm_pct = _pick_f(
        "PX3_THIN_LEAD_EXCEPTIONAL_MIN_PRICE_HWM_PCT",
        "PX3_THIN_LEAD_EXCEPTIONAL_MIN_UND_HWM_PCT",
        default=0.001,
    )
    dte_single_px3_thin_lead_exceptional_price_trail_pct = _pick_f(
        "PX3_THIN_LEAD_EXCEPTIONAL_PRICE_TRAIL_PCT", default=0.004
    )
    dte_single_px3_thin_lead_exceptional_prem_floor_pct = _pick_f(
        "PX3_THIN_LEAD_EXCEPTIONAL_PREM_FLOOR_PCT", default=0.25
    )
    dte_single_px3_high_hwm_exceptional_enabled = _pick_b(
        "PX3_HIGH_HWM_EXCEPTIONAL_ENABLED", default=False
    )
    dte_single_px3_high_hwm_exceptional_min_current_pnl_pct = _pick_f(
        "PX3_HIGH_HWM_EXCEPTIONAL_MIN_CURRENT_PNL_PCT", default=1.00
    )
    dte_single_px3_high_hwm_exceptional_min_prem_hwm_pct = _pick_f(
        "PX3_HIGH_HWM_EXCEPTIONAL_MIN_PREM_HWM_PCT", default=1.25
    )
    dte_single_px3_high_hwm_exceptional_min_und_hwm_pct = _pick_f(
        "PX3_HIGH_HWM_EXCEPTIONAL_MIN_PRICE_HWM_PCT",
        "PX3_HIGH_HWM_EXCEPTIONAL_MIN_UND_HWM_PCT",
        default=0.004,
    )
    dte_single_px3_high_hwm_exceptional_price_trail_pct = _pick_f(
        "PX3_HIGH_HWM_EXCEPTIONAL_PRICE_TRAIL_PCT", default=0.004
    )
    dte_single_px3_high_hwm_exceptional_prem_floor_pct = _pick_f(
        "PX3_HIGH_HWM_EXCEPTIONAL_PREM_FLOOR_PCT", default=0.50
    )
    dte_single_px3_near_trend_enabled = _pick_b("PX3_NEAR_TREND_ENABLED", default=False)
    dte_single_px3_near_trend_min_current_pnl_pct = _pick_f(
        "PX3_NEAR_TREND_MIN_CURRENT_PNL_PCT", default=0.70
    )
    dte_single_px3_near_trend_min_prem_hwm_pct = _pick_f(
        "PX3_NEAR_TREND_MIN_PREM_HWM_PCT", default=0.90
    )
    dte_single_px3_near_trend_min_und_hwm_pct = _pick_f(
        "PX3_NEAR_TREND_MIN_PRICE_HWM_PCT",
        "PX3_NEAR_TREND_MIN_UND_HWM_PCT",
        default=0.0009,
    )
    dte_single_px3_near_trend_price_trail_pct = _pick_f(
        "PX3_NEAR_TREND_PRICE_TRAIL_PCT", default=0.004
    )
    dte_single_px3_near_trend_prem_floor_pct = _pick_f(
        "PX3_NEAR_TREND_PREM_FLOOR_PCT", default=0.50
    )
    dte_single_px3_mfe_confirm_enabled = _pick_b("PX3_MFE_CONFIRM_ENABLED", default=False)
    dte_single_px3_mfe_confirm_min_current_pnl_pct = _pick_f(
        "PX3_MFE_CONFIRM_MIN_CURRENT_PNL_PCT", default=0.20
    )
    dte_single_px3_mfe_confirm_min_prem_hwm_pct = _pick_f(
        "PX3_MFE_CONFIRM_MIN_PREM_HWM_PCT", default=0.30
    )
    dte_single_px3_mfe_confirm_min_und_hwm_pct = _pick_f(
        "PX3_MFE_CONFIRM_MIN_PRICE_HWM_PCT",
        "PX3_MFE_CONFIRM_MIN_UND_HWM_PCT",
        default=0.0025,
    )
    dte_single_px3_mfe_confirm_normal_min_entry_age_seconds = _pick_f(
        "PX3_MFE_CONFIRM_NORMAL_MIN_ENTRY_AGE_SECONDS", default=1200.0
    )
    dte_single_px3_mfe_confirm_normal_max_entry_age_seconds = _pick_f(
        "PX3_MFE_CONFIRM_NORMAL_MAX_ENTRY_AGE_SECONDS", default=1800.0
    )
    dte_single_px3_mfe_confirm_delta_pct = _pick_f(
        "PX3_MFE_CONFIRM_DELTA_PCT", default=0.15
    )
    dte_single_px3_mfe_confirm_window_seconds = _pick_f(
        "PX3_MFE_CONFIRM_WINDOW_SECONDS", default=120.0
    )
    dte_single_px3_mfe_confirm_thin_recovery_enabled = _pick_b(
        "PX3_MFE_CONFIRM_THIN_RECOVERY_ENABLED", default=True
    )
    dte_single_px3_mfe_confirm_thin_recovery_min_current_pnl_pct = _pick_f(
        "PX3_MFE_CONFIRM_THIN_RECOVERY_MIN_CURRENT_PNL_PCT", default=0.15
    )
    dte_single_px3_mfe_confirm_thin_recovery_deadzone_min_pnl_pct = _pick_f(
        "PX3_MFE_CONFIRM_THIN_RECOVERY_DEADZONE_MIN_PNL_PCT", default=0.20
    )
    dte_single_px3_mfe_confirm_thin_recovery_deadzone_max_pnl_pct = _pick_f(
        "PX3_MFE_CONFIRM_THIN_RECOVERY_DEADZONE_MAX_PNL_PCT", default=0.35
    )
    dte_single_px3_mfe_confirm_thin_recovery_min_und_hwm_pct = _pick_f(
        "PX3_MFE_CONFIRM_THIN_RECOVERY_MIN_UND_HWM_PCT",
        "PX3_MFE_CONFIRM_THIN_RECOVERY_MIN_PRICE_HWM_PCT",
        default=0.001,
    )
    dte_single_px3_mfe_confirm_thin_recovery_max_und_hwm_pct = _pick_f(
        "PX3_MFE_CONFIRM_THIN_RECOVERY_MAX_UND_HWM_PCT",
        "PX3_MFE_CONFIRM_THIN_RECOVERY_MAX_PRICE_HWM_PCT",
        default=0.0025,
    )
    dte_single_px3_mfe_confirm_thin_recovery_max_prem_hwm_pct = _pick_f(
        "PX3_MFE_CONFIRM_THIN_RECOVERY_MAX_PREM_HWM_PCT", default=0.75
    )
    dte_single_px3_mfe_confirm_thin_recovery_window_seconds = _pick_f(
        "PX3_MFE_CONFIRM_THIN_RECOVERY_WINDOW_SECONDS", default=600.0
    )
    dte_single_px3_mfe_confirm_thin_recovery_floor_giveback_pct = _pick_f(
        "PX3_MFE_CONFIRM_THIN_RECOVERY_FLOOR_GIVEBACK_PCT", default=0.20
    )
    dte_single_px3_mfe_confirm_thin_recovery_runner_giveback_pct = _pick_f(
        "PX3_MFE_CONFIRM_THIN_RECOVERY_RUNNER_GIVEBACK_PCT", default=0.50
    )
    dte_single_px3_mfe_confirm_strong_recovery_min_current_pnl_pct = _pick_f(
        "PX3_MFE_CONFIRM_STRONG_RECOVERY_MIN_CURRENT_PNL_PCT", default=0.29
    )
    dte_single_px3_mfe_confirm_strong_und_hwm_pct = _pick_f(
        "PX3_MFE_CONFIRM_STRONG_UND_HWM_PCT",
        "PX3_MFE_CONFIRM_STRONG_PRICE_HWM_PCT",
        default=0.0035,
    )
    dte_single_px3_mfe_confirm_recovery_window_seconds = _pick_f(
        "PX3_MFE_CONFIRM_RECOVERY_WINDOW_SECONDS", default=900.0
    )
    dte_single_px3_mfe_confirm_recovery_floor_giveback_pct = _pick_f(
        "PX3_MFE_CONFIRM_RECOVERY_FLOOR_GIVEBACK_PCT", default=0.30
    )
    dte_single_px3_mfe_confirm_recovery_max_entry_age_seconds = _pick_f(
        "PX3_MFE_CONFIRM_RECOVERY_MAX_ENTRY_AGE_SECONDS", default=1200.0
    )
    dte_single_px3_mfe_confirm_late_strong_recovery_enabled = _pick_b(
        "PX3_MFE_CONFIRM_LATE_STRONG_RECOVERY_ENABLED", default=False
    )
    dte_single_px3_mfe_confirm_late_strong_recovery_min_current_pnl_pct = _pick_f(
        "PX3_MFE_CONFIRM_LATE_STRONG_RECOVERY_MIN_CURRENT_PNL_PCT", default=0.35
    )
    dte_single_px3_mfe_confirm_late_strong_recovery_min_prem_hwm_pct = _pick_f(
        "PX3_MFE_CONFIRM_LATE_STRONG_RECOVERY_MIN_PREM_HWM_PCT", default=0.60
    )
    dte_single_px3_mfe_confirm_late_strong_recovery_max_prem_hwm_pct = _pick_f(
        "PX3_MFE_CONFIRM_LATE_STRONG_RECOVERY_MAX_PREM_HWM_PCT", default=1.50
    )
    dte_single_px3_mfe_confirm_late_strong_recovery_min_und_hwm_pct = _pick_f(
        "PX3_MFE_CONFIRM_LATE_STRONG_RECOVERY_MIN_UND_HWM_PCT",
        "PX3_MFE_CONFIRM_LATE_STRONG_RECOVERY_MIN_PRICE_HWM_PCT",
        default=0.0075,
    )
    dte_single_px3_mfe_confirm_late_strong_recovery_max_entry_age_seconds = _pick_f(
        "PX3_MFE_CONFIRM_LATE_STRONG_RECOVERY_MAX_ENTRY_AGE_SECONDS", default=3600.0
    )
    dte_single_px3_mfe_confirm_price_trail_pct = _pick_f(
        "PX3_MFE_CONFIRM_PRICE_TRAIL_PCT", default=0.003
    )
    dte_single_px3_mfe_confirm_prem_floor_pct = _pick_f(
        "PX3_MFE_CONFIRM_PREM_FLOOR_PCT", default=0.30
    )
    dte_single_px3_mfe_confirm_runner_giveback_pct = _pick_f(
        "PX3_MFE_CONFIRM_RUNNER_GIVEBACK_PCT", default=0.20
    )
    dte_single_mfe_take_profit_thin_max_und_pct = _pick_f(
        "0DTE_MFE_TAKE_PROFIT_THIN_MAX_UND_PCT",
        "SINGLE_MFE_TAKE_PROFIT_THIN_MAX_UND_PCT",
        "OPTION_SINGLE_MFE_TAKE_PROFIT_THIN_MAX_UND_PCT",
        default=0.005,
    )
    dte_single_asset_stop_enabled = _pick_b(
        "OPTION_SINGLE_STOP_ENABLED",
        "OPTION_STEALTH_0DTE_SINGLE_ASSET_STOP_ENABLED",
        default=False,
    )
    dte_single_asset_stop_pct = _pick_f(
        "OPTION_SINGLE_STOP_PCT",
        "OPTION_STEALTH_0DTE_SINGLE_ASSET_STOP_PCT",
        default=0.008,
    )
    dte_single_asset_stop_before_arm_trail = b("OPTION_SINGLE_STOP_BEFORE_ARM_TRAIL", True)
    dte_single_premium_stop_enabled = _pick_b(
        "OPTION_SINGLE_PREMIUM_STOP_ENABLED",
        "OPTION_STEALTH_0DTE_SINGLE_PREMIUM_STOP_ENABLED",
        default=True,
    )
    dte_single_premium_stop_pct = _pick_f(
        "OPTION_SINGLE_PREMIUM_STOP_PCT",
        "OPTION_STEALTH_0DTE_SINGLE_PREMIUM_STOP_PCT",
        default=0.60,
    )
    dte_single_premium_stop_max_mfe_pct = _pick_f(
        "OPTION_SINGLE_PREMIUM_STOP_MAX_MFE_PCT",
        "OPTION_STEALTH_0DTE_SINGLE_PREMIUM_STOP_MAX_MFE_PCT",
        default=0.15,
    )
    dte_single_premium_stop_before_arm = _pick_b(
        "OPTION_SINGLE_PREMIUM_STOP_BEFORE_ARM",
        "OPTION_STEALTH_0DTE_SINGLE_PREMIUM_STOP_BEFORE_ARM",
        default=True,
    )
    dte_single_premium_stop_settle_at_stop = _pick_b(
        "OPTION_SINGLE_PREMIUM_STOP_SETTLE_AT_STOP",
        "OPTION_STEALTH_0DTE_SINGLE_PREMIUM_STOP_SETTLE_AT_STOP",
        default=True,
    )
    dte_single_nonstarter_stop_enabled = _pick_b(
        "OPTION_SINGLE_NONSTARTER_STOP_ENABLED",
        "OPTION_STEALTH_0DTE_SINGLE_NONSTARTER_STOP_ENABLED",
        default=True,
    )
    dte_single_nonstarter_stop_pct = _pick_f(
        "OPTION_SINGLE_NONSTARTER_STOP_PCT",
        "OPTION_STEALTH_0DTE_SINGLE_NONSTARTER_STOP_PCT",
        default=0.40,
    )
    dte_single_nonstarter_stop_max_mfe_pct = _pick_f(
        "OPTION_SINGLE_NONSTARTER_STOP_MAX_MFE_PCT",
        "OPTION_STEALTH_0DTE_SINGLE_NONSTARTER_STOP_MAX_MFE_PCT",
        default=0.10,
    )
    dte_single_nonstarter_stop_min_hold_minutes = _pick_f(
        "OPTION_SINGLE_NONSTARTER_STOP_MIN_HOLD_MINUTES",
        "OPTION_STEALTH_0DTE_SINGLE_NONSTARTER_STOP_MIN_HOLD_MINUTES",
        default=20.0,
    )
    dte_single_nonstarter_stop_before_arm = _pick_b(
        "OPTION_SINGLE_NONSTARTER_STOP_BEFORE_ARM",
        "OPTION_STEALTH_0DTE_SINGLE_NONSTARTER_STOP_BEFORE_ARM",
        default=True,
    )
    dte_single_nonstarter_stop_settle_at_stop = _pick_b(
        "OPTION_SINGLE_NONSTARTER_STOP_SETTLE_AT_STOP",
        "OPTION_STEALTH_0DTE_SINGLE_NONSTARTER_STOP_SETTLE_AT_STOP",
        default=True,
    )
    dte_single_price_runner_enabled = _pick_b(
        "OPTION_SINGLE_PRICE_RUNNER_ENABLED",
        "OPTION_STEALTH_0DTE_SINGLE_PRICE_RUNNER_ENABLED",
        "OPTION_STEALTH_0DTE_SINGLE_UND_RUNNER_ENABLED",
        default=False,
    )
    dte_single_price_runner_arm_price_fav_pct = _pick_f(
        "OPTION_SINGLE_PRICE_RUNNER_ARM_PRICE_PCT",
        "OPTION_STEALTH_0DTE_SINGLE_PRICE_RUNNER_ARM_PRICE_FAV_PCT",
        "OPTION_STEALTH_0DTE_SINGLE_UND_RUNNER_ARM_UND_FAV_PCT",
        default=0.0005,
    )
    dte_single_price_runner_confirm_ticks = int(
        max(
            1,
            _pick_f(
                "OPTION_SINGLE_PRICE_RUNNER_CONFIRM_TICKS",
                "OPTION_STEALTH_0DTE_SINGLE_PRICE_RUNNER_CONFIRM_TICKS",
                "OPTION_STEALTH_0DTE_SINGLE_UND_RUNNER_CONFIRM_TICKS",
                default=3,
            ),
        )
    )
    dte_single_price_runner_trail_pct = _pick_f(
        "OPTION_SINGLE_PRICE_RUNNER_TRAIL_PCT",
        "OPTION_STEALTH_0DTE_SINGLE_PRICE_RUNNER_TRAIL_PCT",
        "OPTION_STEALTH_0DTE_SINGLE_UND_RUNNER_TRAIL_PCT",
        default=0.002,
    )
    dte_single_price_runner_wide_trail_mfe_pct = _pick_f(
        "SINGLE_RUNNER_WIDE_TRAIL_MFE_PCT",
        "OPTION_SINGLE_PRICE_RUNNER_WIDE_TRAIL_MFE_PCT",
        default=0.60,
    )
    dte_single_price_runner_wide_trail_pct = _pick_f(
        "SINGLE_RUNNER_WIDE_TRAIL_PCT",
        "OPTION_SINGLE_PRICE_RUNNER_WIDE_TRAIL_PCT",
        default=0.005,
    )
    dte_single_price_runner_take_profit_pct = _pick_f(
        "OPTION_SINGLE_PRICE_RUNNER_TAKE_PROFIT_PCT",
        "OPTION_STEALTH_0DTE_SINGLE_PRICE_RUNNER_TAKE_PROFIT_PCT",
        "OPTION_STEALTH_0DTE_SINGLE_UND_RUNNER_TAKE_PROFIT_PCT",
        default=0.0,
    )
    dte_single_price_runner_prem_floor_pct = _pick_f(
        "SINGLE_RUNNER_PREM_FLOOR_PCT",
        "OPTION_SINGLE_RUNNER_PREM_FLOOR_PCT",  # legacy alias
        default=0.0,
    )
    dte_single_price_runner_prem_floor_price_thin_max_pct = _pick_f(
        "SINGLE_RUNNER_PREM_FLOOR_PRICE_THIN_MAX_PCT",
        "OPTION_SINGLE_RUNNER_PREM_FLOOR_PRICE_THIN_MAX_PCT",  # legacy alias
        default=0.005,
    )
    dte_single_price_runner_prem_floor_price_thin_pct = _pick_f(
        "SINGLE_RUNNER_PREM_FLOOR_PRICE_THIN_PCT",
        "OPTION_SINGLE_RUNNER_PREM_FLOOR_PRICE_THIN_PCT",  # legacy alias
        default=0.20,
    )
    dte_min_hold_seconds = _pick_f(
        "OPTION_MIN_HOLD_SECONDS",
        "OPTION_STEALTH_0DTE_MIN_HOLD_SECONDS",
        default=384.0,
    )
    dte_trailing_trigger_pct = _pick_f(
        "OPTION_TRAILING_TRIGGER_PCT",
        "OPTION_STEALTH_0DTE_TRAILING_TRIGGER_PCT",
        default=0.30,
    )
    dte_time_exit_minutes = f("OPTION_TIME_EXIT_MINUTES", 150.0)
    dte_no_progress_skip_if_mfe_pct = _pick_f(
        "OPTION_NO_PROGRESS_SKIP_IF_MFE_PCT",
        "OPTION_STEALTH_0DTE_NO_PROGRESS_SKIP_IF_MFE_PCT",
        default=0.10,
    )
    dte_near_arm_protect_mfe_pct = _pick_f(
        "OPTION_NEAR_ARM_PROTECT_MFE_PCT",
        "OPTION_STEALTH_0DTE_NEAR_ARM_PROTECT_MFE_PCT",
        default=0.20,
    )
    dte_pre_arm_profit_protect_enabled = _pick_b(
        "OPTION_PRE_ARM_PROFIT_PROTECT_ENABLED",
        "OPTION_STEALTH_0DTE_PRE_ARM_PROFIT_PROTECT_ENABLED",
        default=True,
    )
    dte_pre_arm_profit_protect_trigger_mfe_pct = _pick_f(
        "OPTION_PRE_ARM_PROFIT_PROTECT_TRIGGER_MFE_PCT",
        "OPTION_STEALTH_0DTE_PRE_ARM_PROFIT_PROTECT_TRIGGER_MFE_PCT",
        default=0.20,
    )
    dte_pre_arm_profit_protect_floor_pnl_pct = _pick_f(
        "OPTION_PRE_ARM_PROFIT_PROTECT_FLOOR_PNL_PCT",
        "OPTION_STEALTH_0DTE_PRE_ARM_PROFIT_PROTECT_FLOOR_PNL_PCT",
        default=0.02,
    )
    dte_pre_arm_profit_protect_min_current_pnl_pct = _pick_f(
        "OPTION_PRE_ARM_PROFIT_PROTECT_MIN_CURRENT_PNL_PCT",
        "OPTION_STEALTH_0DTE_PRE_ARM_PROFIT_PROTECT_MIN_CURRENT_PNL_PCT",
        default=-0.05,
    )
    dte_pre_arm_profit_protect_min_hold_minutes = _pick_f(
        "OPTION_PRE_ARM_PROFIT_PROTECT_MIN_HOLD_MINUTES",
        "OPTION_STEALTH_0DTE_PRE_ARM_PROFIT_PROTECT_MIN_HOLD_MINUTES",
        default=30.0,
    )
    dte_pre_arm_profit_protect_min_seconds_since_hwm = _pick_f(
        "OPTION_PRE_ARM_PROFIT_PROTECT_MIN_SECONDS_SINCE_HWM",
        "OPTION_STEALTH_0DTE_PRE_ARM_PROFIT_PROTECT_MIN_SECONDS_SINCE_HWM",
        default=600.0,
    )
    dte_pre_arm_profit_protect_settle_at_floor = _pick_b(
        "OPTION_PRE_ARM_PROFIT_PROTECT_SETTLE_AT_FLOOR",
        "OPTION_STEALTH_0DTE_PRE_ARM_PROFIT_PROTECT_SETTLE_AT_FLOOR",
        default=True,
    )
    dte_low_arm_profit_protect_enabled = _pick_b(
        "OPTION_LOW_ARM_PROFIT_PROTECT_ENABLED",
        "OPTION_STEALTH_0DTE_LOW_ARM_PROFIT_PROTECT_ENABLED",
        default=True,
    )
    dte_low_arm_profit_protect_trigger_mfe_pct = _pick_f(
        "OPTION_LOW_ARM_PROFIT_PROTECT_TRIGGER_MFE_PCT",
        "OPTION_STEALTH_0DTE_LOW_ARM_PROFIT_PROTECT_TRIGGER_MFE_PCT",
        default=0.10,
    )
    dte_low_arm_profit_protect_max_mfe_pct = _pick_f(
        "OPTION_LOW_ARM_PROFIT_PROTECT_MAX_MFE_PCT",
        "OPTION_STEALTH_0DTE_LOW_ARM_PROFIT_PROTECT_MAX_MFE_PCT",
        default=0.20,
    )
    dte_low_arm_profit_protect_floor_pnl_pct = _pick_f(
        "OPTION_LOW_ARM_PROFIT_PROTECT_FLOOR_PNL_PCT",
        "OPTION_STEALTH_0DTE_LOW_ARM_PROFIT_PROTECT_FLOOR_PNL_PCT",
        default=0.0,
    )
    dte_low_arm_profit_protect_min_current_pnl_pct = _pick_f(
        "OPTION_LOW_ARM_PROFIT_PROTECT_MIN_CURRENT_PNL_PCT",
        "OPTION_STEALTH_0DTE_LOW_ARM_PROFIT_PROTECT_MIN_CURRENT_PNL_PCT",
        default=-0.05,
    )
    dte_low_arm_profit_protect_min_hold_minutes = _pick_f(
        "OPTION_LOW_ARM_PROFIT_PROTECT_MIN_HOLD_MINUTES",
        "OPTION_STEALTH_0DTE_LOW_ARM_PROFIT_PROTECT_MIN_HOLD_MINUTES",
        default=30.0,
    )
    dte_low_arm_profit_protect_min_seconds_since_hwm = _pick_f(
        "OPTION_LOW_ARM_PROFIT_PROTECT_MIN_SECONDS_SINCE_HWM",
        "OPTION_STEALTH_0DTE_LOW_ARM_PROFIT_PROTECT_MIN_SECONDS_SINCE_HWM",
        default=600.0,
    )
    dte_low_arm_profit_protect_settle_at_floor = _pick_b(
        "OPTION_LOW_ARM_PROFIT_PROTECT_SETTLE_AT_FLOOR",
        "OPTION_STEALTH_0DTE_LOW_ARM_PROFIT_PROTECT_SETTLE_AT_FLOOR",
        default=True,
    )
    dte_early_be_enabled = _pick_b(
        "OPTION_EARLY_BE_ENABLED",
        "OPTION_STEALTH_0DTE_EARLY_BE_ENABLED",
        default=True,
    )
    dte_early_be_max_mfe_pct = _pick_f(
        "OPTION_EARLY_BE_MAX_MFE_PCT",
        "OPTION_STEALTH_0DTE_EARLY_BE_MAX_MFE_PCT",
        default=0.20,
    )
    dte_early_be_floor_pnl_pct = _pick_f(
        "OPTION_EARLY_BE_FLOOR_PNL_PCT",
        "OPTION_STEALTH_0DTE_EARLY_BE_FLOOR_PNL_PCT",
        default=0.0,
    )
    dte_early_be_min_current_pnl_pct = _pick_f(
        "OPTION_EARLY_BE_MIN_CURRENT_PNL_PCT",
        "OPTION_STEALTH_0DTE_EARLY_BE_MIN_CURRENT_PNL_PCT",
        default=-0.05,
    )
    dte_early_be_min_hold_minutes = _pick_f(
        "OPTION_EARLY_BE_MIN_HOLD_MINUTES",
        "OPTION_STEALTH_0DTE_EARLY_BE_MIN_HOLD_MINUTES",
        default=5.0,
    )
    dte_early_be_min_seconds_since_hwm = _pick_f(
        "OPTION_EARLY_BE_MIN_SECONDS_SINCE_HWM",
        "OPTION_STEALTH_0DTE_EARLY_BE_MIN_SECONDS_SINCE_HWM",
        default=0.0,
    )
    dte_early_be_settle_at_floor = _pick_b(
        "OPTION_EARLY_BE_SETTLE_AT_FLOOR",
        "OPTION_STEALTH_0DTE_EARLY_BE_SETTLE_AT_FLOOR",
        default=True,
    )
    dte_weak_loss_cap_enabled = _pick_b(
        "OPTION_WEAK_LOSS_CAP_ENABLED",
        "OPTION_STEALTH_0DTE_WEAK_LOSS_CAP_ENABLED",
        default=True,
    )
    dte_weak_loss_cap_max_mfe_pct = _pick_f(
        "OPTION_WEAK_LOSS_CAP_MAX_MFE_PCT",
        "OPTION_STEALTH_0DTE_WEAK_LOSS_CAP_MAX_MFE_PCT",
        default=0.30,
    )
    dte_weak_loss_cap_floor_pnl_pct = _pick_f(
        "OPTION_WEAK_LOSS_CAP_FLOOR_PNL_PCT",
        "OPTION_STEALTH_0DTE_WEAK_LOSS_CAP_FLOOR_PNL_PCT",
        default=-0.10,
    )
    dte_weak_loss_cap_settle_at_floor = _pick_b(
        "OPTION_WEAK_LOSS_CAP_SETTLE_AT_FLOOR",
        "OPTION_STEALTH_0DTE_WEAK_LOSS_CAP_SETTLE_AT_FLOOR",
        default=True,
    )
    dte_protect_bid_aware_be_enabled = _pick_b(
        "OPTION_PROTECT_BID_AWARE_BE_ENABLED",
        "OPTION_STEALTH_0DTE_PROTECT_BID_AWARE_BE_ENABLED",
        default=True,
    )
    dte_protect_bid_aware_be_wait_minutes = _pick_f(
        "OPTION_PROTECT_BID_AWARE_BE_WAIT_MINUTES",
        "OPTION_STEALTH_0DTE_PROTECT_BID_AWARE_BE_WAIT_MINUTES",
        default=2.0,
    )
    dte_protect_bid_aware_be_hard_stop_pct = _pick_f(
        "OPTION_PROTECT_BID_AWARE_BE_HARD_STOP_PCT",
        "OPTION_STEALTH_0DTE_PROTECT_BID_AWARE_BE_HARD_STOP_PCT",
        default=-0.25,
    )
    dte_protect_bid_aware_be_cancel_if_peak_above_rule_max = _pick_b(
        "OPTION_PROTECT_BID_AWARE_BE_CANCEL_IF_PEAK_ABOVE_RULE_MAX",
        "OPTION_STEALTH_0DTE_PROTECT_BID_AWARE_BE_CANCEL_IF_PEAK_ABOVE_RULE_MAX",
        default=True,
    )
    dte_protect_bid_aware_be_target_pnl_pct = _pick_f(
        "OPTION_PROTECT_BID_AWARE_BE_TARGET_PNL_PCT",
        "OPTION_STEALTH_0DTE_PROTECT_BID_AWARE_BE_TARGET_PNL_PCT",
        default=0.0,
    )
    dte_breakeven_exit_enabled = _pick_b(
        "OPTION_BREAKEVEN_EXIT_ENABLED",
        "OPTION_STEALTH_0DTE_BREAKEVEN_EXIT_ENABLED",
        default=True,
    )
    dte_breakeven_exit_minutes = _pick_f(
        "OPTION_BREAKEVEN_EXIT_MINUTES",
        "OPTION_STEALTH_0DTE_BREAKEVEN_EXIT_MINUTES",
        default=60.0,
    )
    dte_breakeven_exit_max_pnl_pct = _pick_f(
        "OPTION_BREAKEVEN_EXIT_MAX_PNL_PCT",
        "OPTION_STEALTH_0DTE_BREAKEVEN_EXIT_MAX_PNL_PCT",
        default=0.02,
    )
    dte_scratch_below_pnl_pct = _pick_f(
        "OPTION_SCRATCH_BELOW_PNL_PCT",
        "OPTION_STEALTH_0DTE_SCRATCH_BELOW_PNL_PCT",
        default=-0.0005,
    )
    dte_scratch_exit_minutes = _pick_f(
        "OPTION_SCRATCH_EXIT_MINUTES",
        "OPTION_STEALTH_0DTE_SCRATCH_EXIT_MINUTES",
        default=62.0,
    )
    dte_scratch_early_minutes = _pick_f(
        "OPTION_SCRATCH_EARLY_MINUTES",
        "OPTION_STEALTH_0DTE_SCRATCH_EARLY_MINUTES",
        default=30.0,
    )
    dte_scratch_early_max_mfe_pct = _pick_f(
        "OPTION_SCRATCH_EARLY_MAX_MFE_PCT",
        "OPTION_STEALTH_0DTE_SCRATCH_EARLY_MAX_MFE_PCT",
        default=0.10,
    )
    dte_single_no_progress_early_minutes = _pick_f(
        "OPTION_SINGLE_NO_PROGRESS_EARLY_MINUTES",
        "OPTION_STEALTH_0DTE_SINGLE_NO_PROGRESS_EARLY_MINUTES",
        default=30.0,
    )
    dte_single_no_progress_early_max_mfe_pct = _pick_f(
        "OPTION_SINGLE_NO_PROGRESS_EARLY_MAX_MFE_PCT",
        "OPTION_STEALTH_0DTE_SINGLE_NO_PROGRESS_EARLY_MAX_MFE_PCT",
        default=0.05,
    )
    mfe_giveback_defer_max_seconds = f("OPTION_STEALTH_MFE_GIVEBACK_DEFER_MAX_SECONDS", 90.0)
    mfe_giveback_gap_guard_enabled = b("OPTION_STEALTH_MFE_GIVEBACK_GAP_GUARD_ENABLED", True)
    mfe_giveback_gap_confirm_ticks = int(
        max(1, f("OPTION_STEALTH_MFE_GIVEBACK_GAP_CONFIRM_TICKS", 2))
    )
    mfe_giveback_gap_overshoot_pct = f("OPTION_STEALTH_MFE_GIVEBACK_GAP_OVERSHOOT_PCT", 0.05)
    # Hold Exit family removed from Shared.env — path retained dead (always off).
    hold_exit_enabled = False
    hold_exit_trigger_giveback_pct = 0.10
    hold_exit_retrail_giveback_pct = 0.10
    hold_exit_reclaim_minutes = 10.0
    hold_exit_max_minutes = 45.0
    hold_exit_hard_floor_pnl_pct = -0.05
    hold_exit_require_trigger_bid_floor = False
    hold_exit_dir_volume_min_pct = 0.55
    hold_exit_require_dir_volume = True
    exit_trailing_stop_enabled = _pick_b(
        "OPTION_MFE_TRAILING_STOP_ENABLED",
        "OPTION_STEALTH_EXIT_TRAILING_STOP_ENABLED",
        default=False,
    )
    dte_single_favorable_underlying_pct = _pick_f(
        "OPTION_SINGLE_FAVORABLE_PRICE_PCT",
        "OPTION_STEALTH_0DTE_SINGLE_FAVORABLE_UNDERLYING_PCT",
        default=0.0035,
    )
    dte_single_exit_grade_bypass = _pick_b(
        "OPTION_SINGLE_EXIT_GRADE_BYPASS",
        "OPTION_STEALTH_0DTE_SINGLE_EXIT_GRADE_BYPASS",
        default=True,
    )

    _osc = OptionStealthConfig(
        delta_itm_threshold=f("OPTION_STEALTH_DELTA_ITM_THRESHOLD", 0.60),
        delta_atm_threshold=f("OPTION_STEALTH_DELTA_ATM_THRESHOLD", 0.40),
        trailing_trigger_pct=f("OPTION_STEALTH_TRAILING_TRIGGER_PCT", 0.35),
        base_trailing_pct=f("OPTION_STEALTH_BASE_TRAILING_PCT", 0.22),
        explosive_trailing_pct=f("OPTION_STEALTH_EXPLOSIVE_TRAILING_PCT", 0.14),
        moon_trailing_pct=f("OPTION_STEALTH_MOON_TRAILING_PCT", 0.10),
        time_exit_minutes=i("OPTION_STEALTH_TIME_EXIT_MINUTES", 12.0),
        no_progress_exit_minutes=i("OPTION_STEALTH_NO_PROGRESS_EXIT_MINUTES", 60.0),
        # Legacy partial TP ladder removed Jul 1 — always disabled (stealth owns exits).
        disable_tp_ladder=True,
        premium_move_sensitivity=f("TRENDLINE_OPTION_SENSITIVITY", 2.5),
        premium_max_jump_pct=f("OPTION_STEALTH_PREMIUM_MAX_JUMP_PCT", 0.55),
        premium_max_mult_from_entry=f("OPTION_STEALTH_PREMIUM_MAX_MULT", 12.0),
        premium_min_mult_from_entry=f("OPTION_STEALTH_PREMIUM_MIN_MULT", 0.02),
        max_stale_seconds=f("OPTION_STEALTH_MAX_STALE_SECONDS", 2.0),
        premium_jump_recheck_pct=f("OPTION_STEALTH_PREMIUM_JUMP_RECHECK_PCT", 0.15),
        force_reeval_on_premium_jump=b("OPTION_STEALTH_FORCE_REEVAL_ON_PREMIUM_JUMP", True),
        spread_trailing_trigger_pct=f("OPTION_STEALTH_SPREAD_TRAILING_TRIGGER_PCT", 0.28),
        spread_base_trailing_pct=f("OPTION_STEALTH_SPREAD_BASE_TRAILING_PCT", 0.12),
        spread_time_exit_minutes=i("OPTION_STEALTH_SPREAD_TIME_EXIT_MINUTES", 9.0),
        spread_no_progress_exit_minutes=i("OPTION_STEALTH_SPREAD_NO_PROGRESS_EXIT_MINUTES", 6.0),
        orb_spread_no_progress_favorable_underlying_move_pct=f(
            "OPTION_STEALTH_SPREAD_NO_PROGRESS_FAVORABLE_UNDERLYING_MOVE_PCT", 0.0035
        ),
        timeout_min_good_mark_ticks=int(max(1, f("OPTION_STEALTH_TIMEOUT_MIN_GOOD_MARK_TICKS", 3))),
        quote_exit_grade_max_age_seconds=f("OPTION_QUOTE_EXIT_GRADE_MAX_AGE_SECONDS", 30.0),
        quote_stale_max_age_seconds=f("OPTION_QUOTE_STALE_MAX_AGE_SECONDS", 90.0),
        freeze_timeouts_on_degraded_marks=b("OPTION_STEALTH_FREEZE_TIMEOUTS_ON_DEGRADED_MARKS", True),
        require_exit_grade_for_timeouts=b("OPTION_STEALTH_REQUIRE_EXIT_GRADE_FOR_TIMEOUTS", True),
        require_exit_grade_for_weak_exits=b("OPTION_STEALTH_REQUIRE_EXIT_GRADE_FOR_WEAK_EXITS", True),
        orb_safety_exits_bypass_exit_grade_gates=bool(dte_single_exit_grade_bypass),
        no_progress_favorable_underlying_move_pct=float(dte_single_favorable_underlying_pct),
        dte_single_no_progress_minutes=float(dte_single_no_progress_minutes),
        dte_single_mfe_trail_trigger_pct=float(dte_single_mfe_trail_trigger_pct),
        dte_single_mfe_trail_exit_pct=float(dte_single_mfe_trail_exit_pct),
        dte_single_mfe_trail_enabled=bool(dte_single_mfe_trail_enabled),
        dte_single_early_prem_trail_enabled=bool(dte_single_early_prem_trail_enabled),
        dte_single_early_prem_trail_min_hwm_pct=float(
            dte_single_early_prem_trail_min_hwm_pct
        ),
        dte_single_early_prem_trail_pct=float(dte_single_early_prem_trail_pct),
        dte_single_early_prem_trail_min_current_pnl_pct=float(
            dte_single_early_prem_trail_min_current_pnl_pct
        ),
        dte_single_early_prem_trail_before_cutoff_only=bool(
            dte_single_early_prem_trail_before_cutoff_only
        ),
        dte_single_arm_trail_pct=float(dte_single_arm_trail_pct),
        dte_single_mfe_take_profit_enabled=bool(dte_single_mfe_take_profit_enabled),
        dte_single_mfe_take_profit_pct=float(dte_single_mfe_take_profit_pct),
        dte_single_mfe_take_profit_if_price_thin=bool(dte_single_mfe_take_profit_if_price_thin),
        dte_single_mfe_take_profit_thin_max_und_pct=float(
            dte_single_mfe_take_profit_thin_max_und_pct
        ),
        dte_single_px2_enabled=bool(dte_single_px2_enabled),
        dte_single_px2_giveback_pct=float(dte_single_px2_giveback_pct),
        dte_single_px2_lock_moon_pct=float(dte_single_px2_lock_moon_pct),
        dte_single_px2_stall_ticks=int(dte_single_px2_stall_ticks),
        dte_single_px2_stall_min_retention_of_mfe=float(
            dte_single_px2_stall_min_retention_of_mfe
        ),
        dte_single_px2_stall_min_mfe_pct=float(dte_single_px2_stall_min_mfe_pct),
        dte_single_px2_high_mfe_trail_trigger_pct=float(
            dte_single_px2_high_mfe_trail_trigger_pct
        ),
        dte_single_px2_high_mfe_giveback_pct=float(
            dte_single_px2_high_mfe_giveback_pct
        ),
        dte_single_px2_be_floor=bool(dte_single_px2_be_floor),
        dte_single_px2_und_trail_need_pct=float(dte_single_px2_und_trail_need_pct),
        dte_single_px2_und_trail_prem_pct=float(dte_single_px2_und_trail_prem_pct),
        dte_px25_enabled=bool(dte_px25_enabled),
        dte_px25_min_prem_hwm_pct=float(dte_px25_min_prem_hwm_pct),
        dte_px25_exclude_ultra_clean_pnl_pct=float(dte_px25_exclude_ultra_clean_pnl_pct),
        dte_px25_exclude_ultra_clean_capture_ratio=float(
            dte_px25_exclude_ultra_clean_capture_ratio
        ),
        dte_px25_price_trail_pct=float(dte_px25_price_trail_pct),
        dte_px25_max_extension_seconds=float(dte_px25_max_extension_seconds),
        dte_px25_prem_floor_pct=float(dte_px25_prem_floor_pct),
        dte_px25_mfe_thin_und_bank_enabled=bool(dte_px25_mfe_thin_und_bank_enabled),
        dte_px25_mfe_thin_und_fav_hwm_pct=float(dte_px25_mfe_thin_und_fav_hwm_pct),
        dte_px25_mfe_thin_und_min_prem_hwm_pct=float(
            dte_px25_mfe_thin_und_min_prem_hwm_pct
        ),
        dte_single_px3_enabled=bool(dte_single_px3_enabled),
        dte_single_px3_trend_mfe_pct=float(dte_single_px3_trend_mfe_pct),
        dte_single_px3_trend_und_hwm_pct=float(dte_single_px3_trend_und_hwm_pct),
        dte_single_px3_price_trail_pct=float(dte_single_px3_price_trail_pct),
        dte_single_px3_trend_prem_floor_pct=float(dte_single_px3_trend_prem_floor_pct),
        dte_single_px3_moon_mfe_pct=float(dte_single_px3_moon_mfe_pct),
        dte_single_px3_moon_patience_seconds=float(dte_single_px3_moon_patience_seconds),
        dte_single_px3_moon_prem_floor_pct=float(dte_single_px3_moon_prem_floor_pct),
        dte_single_px3_ultra_clean_pnl_pct=float(dte_single_px3_ultra_clean_pnl_pct),
        dte_single_px3_ultra_clean_capture_ratio=float(
            dte_single_px3_ultra_clean_capture_ratio
        ),
        dte_single_px3_clean_trend_pnl_pct=float(dte_single_px3_clean_trend_pnl_pct),
        dte_single_px3_clean_trend_capture_ratio=float(
            dte_single_px3_clean_trend_capture_ratio
        ),
        dte_single_px3_exceptional_enabled=bool(dte_single_px3_exceptional_enabled),
        dte_single_px3_exceptional_min_current_pnl_pct=float(
            dte_single_px3_exceptional_min_current_pnl_pct
        ),
        dte_single_px3_exceptional_min_prem_hwm_pct=float(
            dte_single_px3_exceptional_min_prem_hwm_pct
        ),
        dte_single_px3_exceptional_min_und_hwm_pct=float(
            dte_single_px3_exceptional_min_und_hwm_pct
        ),
        dte_single_px3_exceptional_price_trail_pct=float(
            dte_single_px3_exceptional_price_trail_pct
        ),
        dte_single_px3_exceptional_prem_floor_pct=float(
            dte_single_px3_exceptional_prem_floor_pct
        ),
        dte_single_px3_thin_lead_exceptional_enabled=bool(
            dte_single_px3_thin_lead_exceptional_enabled
        ),
        dte_single_px3_thin_lead_exceptional_min_current_pnl_pct=float(
            dte_single_px3_thin_lead_exceptional_min_current_pnl_pct
        ),
        dte_single_px3_thin_lead_exceptional_min_prem_hwm_pct=float(
            dte_single_px3_thin_lead_exceptional_min_prem_hwm_pct
        ),
        dte_single_px3_thin_lead_exceptional_min_und_hwm_pct=float(
            dte_single_px3_thin_lead_exceptional_min_und_hwm_pct
        ),
        dte_single_px3_thin_lead_exceptional_price_trail_pct=float(
            dte_single_px3_thin_lead_exceptional_price_trail_pct
        ),
        dte_single_px3_thin_lead_exceptional_prem_floor_pct=float(
            dte_single_px3_thin_lead_exceptional_prem_floor_pct
        ),
        dte_single_px3_high_hwm_exceptional_enabled=bool(
            dte_single_px3_high_hwm_exceptional_enabled
        ),
        dte_single_px3_high_hwm_exceptional_min_current_pnl_pct=float(
            dte_single_px3_high_hwm_exceptional_min_current_pnl_pct
        ),
        dte_single_px3_high_hwm_exceptional_min_prem_hwm_pct=float(
            dte_single_px3_high_hwm_exceptional_min_prem_hwm_pct
        ),
        dte_single_px3_high_hwm_exceptional_min_und_hwm_pct=float(
            dte_single_px3_high_hwm_exceptional_min_und_hwm_pct
        ),
        dte_single_px3_high_hwm_exceptional_price_trail_pct=float(
            dte_single_px3_high_hwm_exceptional_price_trail_pct
        ),
        dte_single_px3_high_hwm_exceptional_prem_floor_pct=float(
            dte_single_px3_high_hwm_exceptional_prem_floor_pct
        ),
        dte_single_px3_near_trend_enabled=bool(dte_single_px3_near_trend_enabled),
        dte_single_px3_near_trend_min_current_pnl_pct=float(
            dte_single_px3_near_trend_min_current_pnl_pct
        ),
        dte_single_px3_near_trend_min_prem_hwm_pct=float(
            dte_single_px3_near_trend_min_prem_hwm_pct
        ),
        dte_single_px3_near_trend_min_und_hwm_pct=float(
            dte_single_px3_near_trend_min_und_hwm_pct
        ),
        dte_single_px3_near_trend_price_trail_pct=float(
            dte_single_px3_near_trend_price_trail_pct
        ),
        dte_single_px3_near_trend_prem_floor_pct=float(
            dte_single_px3_near_trend_prem_floor_pct
        ),
        dte_single_px3_mfe_confirm_enabled=bool(dte_single_px3_mfe_confirm_enabled),
        dte_single_px3_mfe_confirm_min_current_pnl_pct=float(
            dte_single_px3_mfe_confirm_min_current_pnl_pct
        ),
        dte_single_px3_mfe_confirm_min_prem_hwm_pct=float(
            dte_single_px3_mfe_confirm_min_prem_hwm_pct
        ),
        dte_single_px3_mfe_confirm_min_und_hwm_pct=float(
            dte_single_px3_mfe_confirm_min_und_hwm_pct
        ),
        dte_single_px3_mfe_confirm_normal_min_entry_age_seconds=float(
            dte_single_px3_mfe_confirm_normal_min_entry_age_seconds
        ),
        dte_single_px3_mfe_confirm_normal_max_entry_age_seconds=float(
            dte_single_px3_mfe_confirm_normal_max_entry_age_seconds
        ),
        dte_single_px3_mfe_confirm_delta_pct=float(dte_single_px3_mfe_confirm_delta_pct),
        dte_single_px3_mfe_confirm_window_seconds=float(
            dte_single_px3_mfe_confirm_window_seconds
        ),
        dte_single_px3_mfe_confirm_thin_recovery_enabled=bool(
            dte_single_px3_mfe_confirm_thin_recovery_enabled
        ),
        dte_single_px3_mfe_confirm_thin_recovery_min_current_pnl_pct=float(
            dte_single_px3_mfe_confirm_thin_recovery_min_current_pnl_pct
        ),
        dte_single_px3_mfe_confirm_thin_recovery_deadzone_min_pnl_pct=float(
            dte_single_px3_mfe_confirm_thin_recovery_deadzone_min_pnl_pct
        ),
        dte_single_px3_mfe_confirm_thin_recovery_deadzone_max_pnl_pct=float(
            dte_single_px3_mfe_confirm_thin_recovery_deadzone_max_pnl_pct
        ),
        dte_single_px3_mfe_confirm_thin_recovery_min_und_hwm_pct=float(
            dte_single_px3_mfe_confirm_thin_recovery_min_und_hwm_pct
        ),
        dte_single_px3_mfe_confirm_thin_recovery_max_und_hwm_pct=float(
            dte_single_px3_mfe_confirm_thin_recovery_max_und_hwm_pct
        ),
        dte_single_px3_mfe_confirm_thin_recovery_max_prem_hwm_pct=float(
            dte_single_px3_mfe_confirm_thin_recovery_max_prem_hwm_pct
        ),
        dte_single_px3_mfe_confirm_thin_recovery_window_seconds=float(
            dte_single_px3_mfe_confirm_thin_recovery_window_seconds
        ),
        dte_single_px3_mfe_confirm_thin_recovery_floor_giveback_pct=float(
            dte_single_px3_mfe_confirm_thin_recovery_floor_giveback_pct
        ),
        dte_single_px3_mfe_confirm_thin_recovery_runner_giveback_pct=float(
            dte_single_px3_mfe_confirm_thin_recovery_runner_giveback_pct
        ),
        dte_single_px3_mfe_confirm_strong_recovery_min_current_pnl_pct=float(
            dte_single_px3_mfe_confirm_strong_recovery_min_current_pnl_pct
        ),
        dte_single_px3_mfe_confirm_strong_und_hwm_pct=float(
            dte_single_px3_mfe_confirm_strong_und_hwm_pct
        ),
        dte_single_px3_mfe_confirm_recovery_window_seconds=float(
            dte_single_px3_mfe_confirm_recovery_window_seconds
        ),
        dte_single_px3_mfe_confirm_recovery_floor_giveback_pct=float(
            dte_single_px3_mfe_confirm_recovery_floor_giveback_pct
        ),
        dte_single_px3_mfe_confirm_recovery_max_entry_age_seconds=float(
            dte_single_px3_mfe_confirm_recovery_max_entry_age_seconds
        ),
        dte_single_px3_mfe_confirm_late_strong_recovery_enabled=bool(
            dte_single_px3_mfe_confirm_late_strong_recovery_enabled
        ),
        dte_single_px3_mfe_confirm_late_strong_recovery_min_current_pnl_pct=float(
            dte_single_px3_mfe_confirm_late_strong_recovery_min_current_pnl_pct
        ),
        dte_single_px3_mfe_confirm_late_strong_recovery_min_prem_hwm_pct=float(
            dte_single_px3_mfe_confirm_late_strong_recovery_min_prem_hwm_pct
        ),
        dte_single_px3_mfe_confirm_late_strong_recovery_max_prem_hwm_pct=float(
            dte_single_px3_mfe_confirm_late_strong_recovery_max_prem_hwm_pct
        ),
        dte_single_px3_mfe_confirm_late_strong_recovery_min_und_hwm_pct=float(
            dte_single_px3_mfe_confirm_late_strong_recovery_min_und_hwm_pct
        ),
        dte_single_px3_mfe_confirm_late_strong_recovery_max_entry_age_seconds=float(
            dte_single_px3_mfe_confirm_late_strong_recovery_max_entry_age_seconds
        ),
        dte_single_px3_mfe_confirm_price_trail_pct=float(
            dte_single_px3_mfe_confirm_price_trail_pct
        ),
        dte_single_px3_mfe_confirm_prem_floor_pct=float(
            dte_single_px3_mfe_confirm_prem_floor_pct
        ),
        dte_single_px3_mfe_confirm_runner_giveback_pct=float(
            dte_single_px3_mfe_confirm_runner_giveback_pct
        ),
        dte_single_asset_stop_enabled=bool(dte_single_asset_stop_enabled),
        dte_single_asset_stop_pct=float(dte_single_asset_stop_pct),
        dte_single_asset_stop_before_arm_trail=bool(
            dte_single_asset_stop_before_arm_trail
        ),
        dte_single_premium_stop_enabled=bool(dte_single_premium_stop_enabled),
        dte_single_premium_stop_pct=float(dte_single_premium_stop_pct),
        dte_single_premium_stop_max_mfe_pct=float(dte_single_premium_stop_max_mfe_pct),
        dte_single_premium_stop_before_arm=bool(dte_single_premium_stop_before_arm),
        dte_single_premium_stop_settle_at_stop=bool(dte_single_premium_stop_settle_at_stop),
        dte_single_nonstarter_stop_enabled=bool(dte_single_nonstarter_stop_enabled),
        dte_single_nonstarter_stop_pct=float(dte_single_nonstarter_stop_pct),
        dte_single_nonstarter_stop_max_mfe_pct=float(
            dte_single_nonstarter_stop_max_mfe_pct
        ),
        dte_single_nonstarter_stop_min_hold_minutes=float(
            dte_single_nonstarter_stop_min_hold_minutes
        ),
        dte_single_nonstarter_stop_before_arm=bool(dte_single_nonstarter_stop_before_arm),
        dte_single_nonstarter_stop_settle_at_stop=bool(
            dte_single_nonstarter_stop_settle_at_stop
        ),
        dte_single_price_runner_enabled=bool(dte_single_price_runner_enabled),
        dte_single_price_runner_arm_price_fav_pct=float(dte_single_price_runner_arm_price_fav_pct),
        dte_single_price_runner_confirm_ticks=int(dte_single_price_runner_confirm_ticks),
        dte_single_price_runner_trail_pct=float(dte_single_price_runner_trail_pct),
        dte_single_price_runner_wide_trail_mfe_pct=float(
            dte_single_price_runner_wide_trail_mfe_pct
        ),
        dte_single_price_runner_wide_trail_pct=float(
            dte_single_price_runner_wide_trail_pct
        ),
        dte_single_price_runner_take_profit_pct=float(dte_single_price_runner_take_profit_pct),
        dte_single_price_runner_prem_floor_pct=float(dte_single_price_runner_prem_floor_pct),
        dte_single_price_runner_prem_floor_price_thin_max_pct=float(
            dte_single_price_runner_prem_floor_price_thin_max_pct
        ),
        dte_single_price_runner_prem_floor_price_thin_pct=float(
            dte_single_price_runner_prem_floor_price_thin_pct
        ),
        dte_min_hold_seconds=float(dte_min_hold_seconds),
        dte_trailing_trigger_pct=float(dte_trailing_trigger_pct),
        dte_time_exit_minutes=float(dte_time_exit_minutes),
        dte_no_progress_skip_if_mfe_pct=float(dte_no_progress_skip_if_mfe_pct),
        dte_near_arm_protect_mfe_pct=float(dte_near_arm_protect_mfe_pct),
        dte_pre_arm_profit_protect_enabled=bool(dte_pre_arm_profit_protect_enabled),
        dte_pre_arm_profit_protect_trigger_mfe_pct=float(
            dte_pre_arm_profit_protect_trigger_mfe_pct
        ),
        dte_pre_arm_profit_protect_floor_pnl_pct=float(
            dte_pre_arm_profit_protect_floor_pnl_pct
        ),
        dte_pre_arm_profit_protect_min_current_pnl_pct=float(
            dte_pre_arm_profit_protect_min_current_pnl_pct
        ),
        dte_pre_arm_profit_protect_min_hold_minutes=float(
            dte_pre_arm_profit_protect_min_hold_minutes
        ),
        dte_pre_arm_profit_protect_min_seconds_since_hwm=float(
            dte_pre_arm_profit_protect_min_seconds_since_hwm
        ),
        dte_pre_arm_profit_protect_settle_at_floor=bool(
            dte_pre_arm_profit_protect_settle_at_floor
        ),
        dte_low_arm_profit_protect_enabled=bool(dte_low_arm_profit_protect_enabled),
        dte_low_arm_profit_protect_trigger_mfe_pct=float(
            dte_low_arm_profit_protect_trigger_mfe_pct
        ),
        dte_low_arm_profit_protect_max_mfe_pct=float(
            dte_low_arm_profit_protect_max_mfe_pct
        ),
        dte_low_arm_profit_protect_floor_pnl_pct=float(
            dte_low_arm_profit_protect_floor_pnl_pct
        ),
        dte_low_arm_profit_protect_min_current_pnl_pct=float(
            dte_low_arm_profit_protect_min_current_pnl_pct
        ),
        dte_low_arm_profit_protect_min_hold_minutes=float(
            dte_low_arm_profit_protect_min_hold_minutes
        ),
        dte_low_arm_profit_protect_min_seconds_since_hwm=float(
            dte_low_arm_profit_protect_min_seconds_since_hwm
        ),
        dte_low_arm_profit_protect_settle_at_floor=bool(
            dte_low_arm_profit_protect_settle_at_floor
        ),
        dte_early_be_enabled=bool(dte_early_be_enabled),
        dte_early_be_max_mfe_pct=float(dte_early_be_max_mfe_pct),
        dte_early_be_floor_pnl_pct=float(dte_early_be_floor_pnl_pct),
        dte_early_be_min_current_pnl_pct=float(dte_early_be_min_current_pnl_pct),
        dte_early_be_min_hold_minutes=float(dte_early_be_min_hold_minutes),
        dte_early_be_min_seconds_since_hwm=float(dte_early_be_min_seconds_since_hwm),
        dte_early_be_settle_at_floor=bool(dte_early_be_settle_at_floor),
        dte_weak_loss_cap_enabled=bool(dte_weak_loss_cap_enabled),
        dte_weak_loss_cap_max_mfe_pct=float(dte_weak_loss_cap_max_mfe_pct),
        dte_weak_loss_cap_floor_pnl_pct=float(dte_weak_loss_cap_floor_pnl_pct),
        dte_weak_loss_cap_settle_at_floor=bool(dte_weak_loss_cap_settle_at_floor),
        dte_protect_bid_aware_be_enabled=bool(dte_protect_bid_aware_be_enabled),
        dte_protect_bid_aware_be_wait_minutes=float(dte_protect_bid_aware_be_wait_minutes),
        dte_protect_bid_aware_be_hard_stop_pct=float(dte_protect_bid_aware_be_hard_stop_pct),
        dte_protect_bid_aware_be_cancel_if_peak_above_rule_max=bool(
            dte_protect_bid_aware_be_cancel_if_peak_above_rule_max
        ),
        dte_protect_bid_aware_be_target_pnl_pct=float(dte_protect_bid_aware_be_target_pnl_pct),
        dte_breakeven_exit_enabled=bool(dte_breakeven_exit_enabled),
        dte_breakeven_exit_minutes=float(dte_breakeven_exit_minutes),
        dte_breakeven_exit_max_pnl_pct=float(dte_breakeven_exit_max_pnl_pct),
        dte_scratch_below_pnl_pct=float(dte_scratch_below_pnl_pct),
        dte_scratch_exit_minutes=float(dte_scratch_exit_minutes),
        dte_scratch_early_minutes=float(dte_scratch_early_minutes),
        dte_scratch_early_max_mfe_pct=float(dte_scratch_early_max_mfe_pct),
        dte_single_no_progress_early_minutes=float(dte_single_no_progress_early_minutes),
        dte_single_no_progress_early_max_mfe_pct=float(
            dte_single_no_progress_early_max_mfe_pct
        ),
        mfe_giveback_defer_max_seconds=float(mfe_giveback_defer_max_seconds),
        mfe_giveback_gap_guard_enabled=bool(mfe_giveback_gap_guard_enabled),
        mfe_giveback_gap_confirm_ticks=int(mfe_giveback_gap_confirm_ticks),
        mfe_giveback_gap_overshoot_pct=float(mfe_giveback_gap_overshoot_pct),
        hold_exit_enabled=bool(hold_exit_enabled),
        hold_exit_trigger_giveback_pct=float(hold_exit_trigger_giveback_pct),
        hold_exit_retrail_giveback_pct=float(hold_exit_retrail_giveback_pct),
        hold_exit_reclaim_minutes=float(hold_exit_reclaim_minutes),
        hold_exit_max_minutes=float(hold_exit_max_minutes),
        hold_exit_hard_floor_pnl_pct=float(hold_exit_hard_floor_pnl_pct),
        hold_exit_require_trigger_bid_floor=bool(hold_exit_require_trigger_bid_floor),
        hold_exit_dir_volume_min_pct=float(hold_exit_dir_volume_min_pct),
        hold_exit_require_dir_volume=bool(hold_exit_require_dir_volume),
        exit_trailing_stop_enabled=bool(exit_trailing_stop_enabled),
        dte_single_favorable_underlying_pct=float(dte_single_favorable_underlying_pct),
        dte_single_exit_grade_bypass=bool(dte_single_exit_grade_bypass),
        tline_option_no_data_grace_seconds=f("TRENDLINE_OPTION_NO_DATA_GRACE_SECONDS", 180.0),
        tline_option_force_exit_no_data_seconds=f("TRENDLINE_OPTION_FORCE_EXIT_NO_DATA_SECONDS", 300.0),
        tline_option_require_exit_grade_before_force_exit=b(
            "TRENDLINE_OPTION_REQUIRE_EXIT_GRADE_BEFORE_FORCE_EXIT", True
        ),
        tline_min_diagnostic_hold_seconds=f("TRENDLINE_MIN_DIAGNOSTIC_HOLD_SECONDS", 120.0),
        tline_no_data_favorable_underlying_move_pct=f(
            "TRENDLINE_OPTION_NO_DATA_FAVORABLE_UNDERLYING_MOVE_PCT", 0.0010
        ),
        tline_exit_grade_max_spread_pct=f("OPTION_STEALTH_EXIT_GRADE_MAX_SPREAD_PCT", 0.30),
        dte_true_to_live_executable_marks=_pick_b(
            "OPTION_TRUE_TO_LIVE_EXECUTABLE_MARKS",
            "OPTION_STEALTH_0DTE_TRUE_TO_LIVE_EXECUTABLE_MARKS",
            default=True,
        ),
        dte_use_bid_for_long_exit=_pick_b(
            "OPTION_USE_BID_FOR_LONG_EXIT",
            "OPTION_STEALTH_0DTE_USE_BID_FOR_LONG_EXIT",
            default=True,
        ),
        require_live_option_data=b("OPTION_STEALTH_REQUIRE_LIVE_OPTION_DATA", True),
        require_live_quotes=b("OPTION_REQUIRE_LIVE_QUOTES", False),
        option_quote_fresh_max_age_seconds=f("OPTION_QUOTE_FRESH_MAX_AGE_SECONDS", 2.0),
        option_0dte_fast_underlying_move_abs=f("OPTION_0DTE_FAST_UNDERLYING_MOVE_ABS", 0.50),
        option_0dte_fast_entry_minutes=f("OPTION_0DTE_FAST_ENTRY_MINUTES", 10.0),
        option_price_resolution_audit_min_seconds=f("OPTION_PRICE_RESOLUTION_AUDIT_MIN_SECONDS", 30.0),
        allow_safety_exits_on_degraded_data=b("OPTION_STEALTH_ALLOW_SAFETY_EXITS_ON_DEGRADED_DATA", True),
        no_data_outage_seconds=no_data_outage_default,
        option_max_degraded_seconds=f("OPTION_MAX_DEGRADED_SECONDS", no_data_outage_default),
        option_synthetic_long_devalue_pct=max(
            0.02,
            min(0.05, f("OPTION_SYNTHETIC_LONG_DEVALUE_PCT", 0.035)),
        ),
        option_synthetic_short_penalize_pct=max(
            0.02,
            min(0.05, f("OPTION_SYNTHETIC_SHORT_PENALIZE_PCT", 0.035)),
        ),
        position_health_log_sec=f("OPTION_STEALTH_POSITION_HEALTH_LOG_SEC", 60.0),
        degraded_tighten_after_minutes=f("OPTION_STEALTH_DEGRADED_TIGHTEN_AFTER_MINUTES", 5.0),
        degraded_trailing_trigger_scale=f("OPTION_STEALTH_DEGRADED_TRAILING_TRIGGER_SCALE", 0.80),
        degraded_trailing_pct_scale=f("OPTION_STEALTH_DEGRADED_TRAILING_PCT_SCALE", 0.75),
        orb_spread_open_grace_seconds=f("ORB_0DTE_SPREAD_OPEN_GRACE_SECONDS", 60.0),
        orb_spread_open_grace_max_attempts=int(max(1, f("ORB_0DTE_SPREAD_OPEN_GRACE_MAX_ATTEMPTS", 6))),
        orb_spread_open_grace_enable_synthetic=b("ORB_0DTE_SPREAD_OPEN_GRACE_ENABLE_SYNTHETIC", True),
        orb_options_spread_degraded_exit_enable=b("ORB_OPTIONS_SPREAD_DEGRADED_EXIT_ENABLE", True),
        orb_options_spread_conservative_exit_enable=b("ORB_OPTIONS_SPREAD_CONSERVATIVE_EXIT_ENABLE", True),
        orb_spread_conservative_min_trade_minutes=f("ORB_SPREAD_CONSERVATIVE_EXIT_MIN_TRADE_MINUTES", 5.0),
        orb_spread_conservative_min_partial_minutes=f("ORB_SPREAD_CONSERVATIVE_EXIT_MIN_PARTIAL_MINUTES", 4.0),
        orb_spread_conservative_min_deferrals=int(max(1, f("ORB_SPREAD_CONSERVATIVE_EXIT_MIN_DEFERRALS", 15))),
        orb_spread_conservative_max_leg_age_sec=f("ORB_SPREAD_CONSERVATIVE_EXIT_MAX_LEG_AGE_SECONDS", 240.0),
        orb_spread_forced_min_trade_minutes=f("ORB_SPREAD_FORCED_DEGRADED_EXIT_MIN_TRADE_MINUTES", 28.0),
        orb_spread_forced_min_partial_minutes=f("ORB_SPREAD_FORCED_DEGRADED_MIN_PARTIAL_LEG_MINUTES", 20.0),
        orb_spread_forced_min_deferrals=int(max(1, f("ORB_SPREAD_FORCED_DEGRADED_MIN_DEFERRALS", 55))),
        orb_spread_forced_watchdog_accum_seconds=f("ORB_SPREAD_FORCED_DEGRADED_WATCHDOG_ACCUM_SECONDS", 480.0),
        orb_spread_forced_abs_max_minutes=f("ORB_SPREAD_FORCED_DEGRADED_ABS_MAX_MINUTES", 90.0),
        orb_spread_forced_allow_incomplete_mtm=b("ORB_SPREAD_FORCED_ALLOW_INCOMPLETE_MTM", True),
        orb_trusted_degraded_spread_enable=b("ORB_TRUSTED_DEGRADED_SPREAD_ENABLE", True),
        orb_trusted_degraded_mark_max_age_seconds=f("ORB_TRUSTED_DEGRADED_MAX_MARK_AGE_SECONDS", 90.0),
        orb_trusted_degraded_min_fresh_leg_age_seconds=f("ORB_TRUSTED_DEGRADED_MIN_FRESH_LEG_AGE_SECONDS", 45.0),
        orb_degraded_protect_enable=b("ORB_DEGRADED_PROTECT_ENABLE", True),
        orb_spread_degraded_time_exit_slack_minutes=f("ORB_DEGRADED_RUNNING_TIME_EXIT_SLACK_MIN", 1.5),
        orb_spread_degraded_no_progress_slack_minutes=f("ORB_DEGRADED_RUNNING_NO_PROGRESS_SLACK_MIN", 1.5),
        orb_no_progress_relax_exit_grade_min_minutes=f("ORB_NO_PROGRESS_RELAX_MIN_MINUTES", 11.0),
        orb_no_progress_relax_max_mfe_pct=f("ORB_NO_PROGRESS_RELAX_MAX_MFE_PCT", 0.06),
        orb_watchdog_trusted_skip_clock_freeze=b("ORB_WATCHDOG_TRUSTED_SKIP_CLOCK_FREEZE", True),
        orb_degraded_drawdown_tighten_scale=f("ORB_DEGRADED_DRAWDOWN_TIGHTEN_SCALE", 0.88),
        max_pnl_drawdown_enabled=b("OPTION_STEALTH_MAX_PNL_DRAWDOWN_ENABLED", True),
        max_pnl_drawdown_trigger_pct=f("OPTION_STEALTH_MAX_PNL_DRAWDOWN_TRIGGER_PCT", 0.10),
        max_pnl_drawdown_exit_pct=f("OPTION_STEALTH_MAX_PNL_DRAWDOWN_EXIT_PCT", 0.07),
        option_stealth_state_log_seconds=f("OPTION_STEALTH_STATE_LOG_SECONDS", 15.0),
        explosive_pnl_threshold_pct=f("OPTION_STEALTH_EXPLOSIVE_PNL_THRESHOLD_PCT", 0.50),
        moon_pnl_threshold_pct=f("OPTION_STEALTH_MOON_PNL_THRESHOLD_PCT", 1.00),
    )
    log_trendline_stealth_config_loaded(_osc)
    log_trendline_exit_config_audit(_osc)
    return _osc


@dataclass
class OptionStealthState:
    mode: OptionStealthMode = OptionStealthMode.INACTIVE
    entry_premium: float = 0.0
    premium_hwm: float = 0.0
    premium_lwm: float = 0.0
    breakeven_floor: Optional[float] = None
    breakeven_activated_at: Optional[datetime] = None
    trailing_activated_at: Optional[datetime] = None
    trail_stop_premium: Optional[float] = None
    last_hwm_at: Optional[datetime] = None
    opened_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    trendline: Dict[str, Any] = field(default_factory=dict)
    option_side: str = ""
    line_geometry: str = ""
    delta_at_entry: float = 0.0
    strike: float = 0.0
    last_effective_premium: float = 0.0
    last_premium_source: str = ""
    last_trail_pct: float = 0.0
    no_progress_timeout_triggered: bool = False
    favorable_advance_seen: bool = False
    max_pnl_pct: float = 0.0
    had_opportunity: bool = False
    had_underlying_opportunity: bool = False
    max_underlying_favorable_move_pct: float = 0.0
    trade_behavior: str = "unknown"
    last_update_at: Optional[datetime] = None
    last_underlying_update_at: Optional[datetime] = None
    last_premium_update_at: Optional[datetime] = None
    stale_data_detected: bool = False
    premium_jump_detected: bool = False
    be_trigger_pct_used: Optional[float] = None
    be_lock_pct_used: Optional[float] = None
    delta_bucket: str = "unknown"
    position_type: str = OptionPositionType.SINGLE_LEG_LONG_CALL.value
    strategy_name: str = ""
    strategy_type: str = ""
    entry_value: float = 0.0
    entry_debit: Optional[float] = None
    entry_credit: Optional[float] = None
    leg_count: int = 0
    legs: List[Dict[str, Any]] = field(default_factory=list)
    spread_hwm: float = 0.0
    spread_lwm: float = 0.0
    trendline_mode: str = "STANDARD"
    impulse_mode: bool = False
    break_archetype: str = ""
    good_mark_tick_count: int = 0
    timeout_clock_frozen: bool = False
    timeout_frozen_seconds: float = 0.0
    timeout_clock_last_resumed_at: Optional[datetime] = None
    timeout_clock_last_frozen_at: Optional[datetime] = None
    watchdog_gap_seen: bool = False


def _parse_stealth_dt(val: Any) -> Optional[datetime]:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def option_stealth_state_to_dict(st: OptionStealthState) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for f in fields(OptionStealthState):
        val = getattr(st, f.name)
        if isinstance(val, Enum):
            out[f.name] = val.value
        elif isinstance(val, datetime):
            out[f.name] = val.isoformat()
        else:
            out[f.name] = val
    return out


def option_stealth_state_from_dict(data: Dict[str, Any]) -> OptionStealthState:
    known = {f.name for f in fields(OptionStealthState)}
    kwargs: Dict[str, Any] = {}
    for key, val in data.items():
        if key not in known:
            continue
        if key == "mode":
            try:
                kwargs[key] = OptionStealthMode(str(val).lower())
            except ValueError:
                kwargs[key] = OptionStealthMode.INACTIVE
        elif key.endswith("_at") or key == "opened_at":
            parsed = _parse_stealth_dt(val)
            if parsed is not None:
                kwargs[key] = parsed
            elif key == "opened_at":
                kwargs[key] = datetime.now(timezone.utc)
        else:
            kwargs[key] = val
    return OptionStealthState(**kwargs)


def option_stealth_state_from_osnap(block: Dict[str, Any], osnap: Dict[str, Any]) -> OptionStealthState:
    """Rebuild in-memory stealth state from flat option_stealth snapshot (post-GCS reload)."""
    entry_premium = float(
        osnap.get("entry_premium") or osnap.get("current_effective_premium") or block.get("entry_premium") or 0.0
    )
    spread_entry = float(
        osnap.get("spread_entry_value") or osnap.get("entry_value") or block.get("entry_value") or entry_premium
    )
    mode_raw = osnap.get("mode") or "inactive"
    try:
        mode = OptionStealthMode(str(mode_raw).lower())
    except ValueError:
        mode = OptionStealthMode.INACTIVE
    opened_at = _parse_stealth_dt(block.get("opened_at")) or datetime.now(timezone.utc)
    legs = osnap.get("legs") if isinstance(osnap.get("legs"), list) else block.get("legs")
    legs_list = [dict(x) for x in legs if isinstance(x, dict)] if isinstance(legs, list) else []
    return OptionStealthState(
        mode=mode,
        entry_premium=entry_premium,
        premium_hwm=float(osnap.get("premium_hwm") or entry_premium),
        premium_lwm=float(osnap.get("premium_lwm") or entry_premium),
        breakeven_floor=float(osnap["breakeven_floor"]) if osnap.get("breakeven_floor") is not None else None,
        breakeven_activated_at=_parse_stealth_dt(osnap.get("breakeven_activated_at")),
        trailing_activated_at=_parse_stealth_dt(osnap.get("trailing_activated_at")),
        opened_at=opened_at,
        trendline=dict(block.get("trendline") or {}),
        option_side=str(osnap.get("option_side") or block.get("option_side") or ""),
        line_geometry=str(block.get("line_geometry") or osnap.get("line_geometry") or ""),
        delta_at_entry=float(osnap.get("delta_at_entry") or block.get("delta_at_entry") or 0.0),
        strike=float(osnap.get("strike") or block.get("strike") or 0.0),
        last_effective_premium=float(
            osnap.get("current_effective_premium") or osnap.get("entry_premium") or entry_premium
        ),
        last_premium_source=str(osnap.get("last_premium_source") or "entry"),
        position_type=str(
            osnap.get("position_type") or block.get("position_type") or OptionPositionType.SINGLE_LEG_LONG_CALL.value
        ),
        strategy_name=str(osnap.get("strategy_name") or block.get("strategy_name") or ""),
        strategy_type=str(osnap.get("strategy_type") or block.get("strategy_type") or ""),
        entry_value=spread_entry,
        entry_debit=float(osnap["entry_debit"]) if osnap.get("entry_debit") is not None else None,
        entry_credit=float(osnap["entry_credit"]) if osnap.get("entry_credit") is not None else None,
        leg_count=int(osnap.get("leg_count") or len(legs_list)),
        legs=legs_list,
        spread_hwm=float(osnap.get("spread_high_water_mark") or spread_entry),
        spread_lwm=float(osnap.get("spread_low_water_mark") or spread_entry),
        trendline_mode=str(block.get("trendline_mode") or osnap.get("trendline_mode") or "STANDARD").upper(),
        impulse_mode=bool(block.get("impulse_mode") or osnap.get("impulse_mode")),
        break_archetype=str(block.get("break_archetype") or osnap.get("break_archetype") or ""),
        be_trigger_pct_used=float(osnap["be_trigger_pct_used"]) if osnap.get("be_trigger_pct_used") is not None else None,
        delta_bucket=str(osnap.get("delta_bucket") or "unknown"),
        max_pnl_pct=float(osnap.get("max_pnl_pct") or 0.0),
        had_opportunity=bool(osnap.get("had_opportunity")),
        had_underlying_opportunity=bool(osnap.get("had_underlying_opportunity")),
        max_underlying_favorable_move_pct=float(
            osnap.get("max_underlying_favorable_move_pct") or 0.0
        ),
        good_mark_tick_count=int(osnap.get("good_mark_tick_count") or 0),
        timeout_clock_frozen=bool(osnap.get("timeout_clock_frozen")),
        timeout_frozen_seconds=float(osnap.get("timeout_frozen_seconds") or 0.0),
        watchdog_gap_seen=bool(osnap.get("watchdog_gap_seen")),
        last_hwm_at=_parse_stealth_dt(osnap.get("last_hwm_at")),
    )


def coerce_option_stealth_engine_state(
    block: Dict[str, Any],
    osnap: Dict[str, Any],
) -> Optional[OptionStealthState]:
    """Ensure engine.state is a live OptionStealthState (GCS json default=str corrupts it)."""
    if not isinstance(block, dict) or not isinstance(osnap, dict):
        return None
    raw = block.get("state")
    if isinstance(raw, OptionStealthState):
        return raw
    if isinstance(raw, str):
        # Trendline ledger save used json.dumps(..., default=str) — state becomes unusable repr.
        log.warning(
            "OPTIONS_STEALTH | stage=engine_state_str_corrupt | action=rehydrate_from_osnap | preview=%s",
            raw[:120],
        )
        block.pop("state", None)
        raw = None
    if isinstance(raw, dict):
        try:
            st = option_stealth_state_from_dict(raw)
            block["state"] = st
            return st
        except Exception as exc:
            log.warning(
                "OPTIONS_STEALTH | stage=engine_state_dict_parse_failed | reason=%s",
                exc,
            )
    try:
        st = option_stealth_state_from_osnap(block, osnap)
        block["state"] = st
        log.info("OPTIONS_STEALTH | stage=engine_state_rehydrated | source=osnap_fallback")
        return st
    except Exception as exc:
        log.warning("OPTIONS_STEALTH | stage=engine_state_rehydrate_failed | reason=%s", exc)
        return None


def _modeled_option_premium(
    entry_underlying: float,
    current_underlying: float,
    option_side: str,
    entry_premium: float,
    base_sensitivity: float,
    delta_at_entry: float,
    strike: float,
) -> float:
    """Lightweight delta- and moneyness-aware proxy (not a full Greek model)."""
    if entry_underlying <= 0 or current_underlying <= 0 or entry_premium <= 0:
        return max(0.01, float(entry_premium))
    side = str(option_side).lower()
    ref_delta = max(0.08, min(0.55, float(delta_at_entry or 0.30)))
    if side == "put":
        move = (entry_underlying - current_underlying) / entry_underlying
        m0 = (strike - entry_underlying) / max(entry_underlying, 1e-9)
        m1 = (strike - current_underlying) / max(current_underlying, 1e-9)
    else:
        move = (current_underlying - entry_underlying) / entry_underlying
        m0 = (entry_underlying - strike) / max(entry_underlying, 1e-9)
        m1 = (current_underlying - strike) / max(current_underlying, 1e-9)
    drift = 0.40 * max(0.0, m1 - m0) - 0.18 * max(0.0, m0 - m1)
    delta_eff = ref_delta + drift
    delta_eff = max(0.06, min(0.92, delta_eff))
    scale = base_sensitivity * (delta_eff / 0.35)
    raw_mult = 1.0 + move * scale
    prem = float(entry_premium) * raw_mult
    return max(0.01, prem)


def _clamp_premium_step(
    candidate: float,
    previous: Optional[float],
    entry_premium: float,
    cfg: OptionStealthConfig,
) -> float:
    """Limit unrealistic tick-to-tick jumps while allowing trend."""
    p = max(0.01, float(candidate))
    ep = max(0.01, float(entry_premium))
    p = min(p, ep * max(0.05, cfg.premium_max_mult_from_entry))
    p = max(p, ep * cfg.premium_min_mult_from_entry)
    if previous is not None and previous > 0:
        lo = previous * (1.0 - cfg.premium_max_jump_pct)
        hi = previous * (1.0 + cfg.premium_max_jump_pct)
        p = min(hi, max(lo, p))
    return max(0.01, p)


def _underlying_proxy_premium(
    entry_underlying: float,
    current_underlying: float,
    option_side: str,
    entry_premium: float,
    cfg: OptionStealthConfig,
) -> float:
    """Underlying-only proxy used when delta-aware estimate cannot be trusted."""
    if entry_underlying <= 0 or current_underlying <= 0:
        return max(0.01, float(entry_premium))
    side = str(option_side or "").lower()
    move = (
        (current_underlying - entry_underlying) / entry_underlying
        if side == "call"
        else (entry_underlying - current_underlying) / entry_underlying
    )
    raw_mult = 1.0 + move * max(0.1, float(cfg.premium_move_sensitivity) * 0.45)
    return max(0.01, float(entry_premium) * raw_mult)


def asset_stop_underlying_adverse(
    *,
    option_side: str,
    entry_underlying: float,
    current_underlying: float,
    buffer_pct: float,
) -> bool:
    """True when underlying has moved adverse of entry by ≥ buffer (CALL under / PUT over)."""
    try:
        entry_u = float(entry_underlying or 0.0)
        cur_u = float(current_underlying or 0.0)
        buf = float(buffer_pct or 0.0)
    except (TypeError, ValueError):
        return False
    if entry_u <= 0 or cur_u <= 0 or buf <= 0:
        return False
    side = str(option_side or "").strip().lower()
    if "put" in side or side == "short":
        return cur_u >= entry_u * (1.0 + buf) - 1e-12
    return cur_u <= entry_u * (1.0 - buf) + 1e-12


def resolve_option_price(
    *,
    entry_underlying: float,
    current_underlying: float,
    option_side: str,
    entry_premium: float,
    cfg: OptionStealthConfig,
    option_quote: Optional[Dict[str, Any]],
    previous_effective: Optional[float],
    delta_at_entry: float,
    strike: float,
    position_symbol: str,
    is_0dte: bool = False,
    seconds_since_entry: Optional[float] = None,
    trade_id: str = "",
    stored_last_valid_option_price: Optional[float] = None,
    is_short_premium: bool = False,
) -> Tuple[float, str, Dict[str, Any]]:
    """
    Resolution order (non-0DTE unchanged):
      1) mid from bid/ask
      2) option last
      3) cached_recent_quote (<= option_quote_fresh_max_age_seconds)
      4) delta_estimate / underlying_proxy (or strict hold when require_live_quotes)

    0DTE single-leg tier when fresh live quote unavailable:
      A) last_valid_option_price from monitor snapshot (stale OK)
      B) synthetic modeled (delta) or underlying proxy, conservatively capped/biased
    """
    modeled = _modeled_option_premium(
        entry_underlying,
        current_underlying,
        option_side,
        entry_premium,
        cfg.premium_move_sensitivity,
        delta_at_entry,
        strike,
    )
    proxy = _underlying_proxy_premium(
        entry_underlying,
        current_underlying,
        option_side,
        entry_premium,
        cfg,
    )
    detail: Dict[str, Any] = {
        "option_bid": None,
        "option_ask": None,
        "option_mark": None,
        "option_last": None,
        "modeled_premium": round(modeled, 6),
        "underlying_proxy_premium": round(proxy, 6),
        "effective_premium_used": None,
        "premium_source": "delta_estimate",
        "quote_age_seconds": None,
        "spread_pct": None,
        "fallback_used": True,
        "clamp_skipped_fast_market": False,
        "pricing_degraded": False,
        "live_quotes_strict_block": False,
        "quote_slice": None,
        "live_degraded_reason": None,
        "price_source_telemetry": None,
        "synthetic_pricing_active": False,
        "stale_last_valid_option": False,
    }

    def _f(x: Any) -> Optional[float]:
        try:
            v = float(x)
            return v if v > 0 else None
        except (TypeError, ValueError):
            return None

    source = "delta_estimate"
    raw_effective: float = modeled

    if option_quote and isinstance(option_quote, dict):
        bid = _f(option_quote.get("bid"))
        ask = _f(option_quote.get("ask"))
        last = _f(option_quote.get("last"))
        mark = _f(option_quote.get("mark")) or _f(option_quote.get("mid_price"))
        quote_age = _f(
            option_quote.get("quote_age_seconds")
            or option_quote.get("age_seconds")
            or option_quote.get("quote_age")
        )
        max_age = float(max(0.05, cfg.option_quote_fresh_max_age_seconds))
        if quote_age is None:
            ts_raw = option_quote.get("quote_timestamp") or option_quote.get("timestamp")
            if isinstance(ts_raw, str):
                try:
                    qdt = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                    quote_age = max(
                        0.0,
                        (datetime.now(timezone.utc) - qdt.astimezone(timezone.utc)).total_seconds(),
                    )
                except Exception:
                    quote_age = None
        quote_is_fresh = (quote_age is None) or (float(quote_age) <= max_age)
        detail["option_bid"] = bid
        detail["option_ask"] = ask
        detail["option_last"] = last
        detail["quote_age_seconds"] = quote_age
        spread_pct = None
        if bid is not None and ask is not None and (bid + ask) > 0:
            spread_pct = abs(ask - bid) / ((ask + bid) / 2.0)
        detail["spread_pct"] = spread_pct
        quote_hint = str(
            option_quote.get("quote_source") or option_quote.get("source") or ""
        ).strip().lower()
        use_bid_executable = (
            is_0dte
            and not is_short_premium
            and str(option_side or "").strip().lower() in ("call", "put")
            and bool(getattr(cfg, "dte_true_to_live_executable_marks", True))
            and bool(getattr(cfg, "dte_use_bid_for_long_exit", True))
        )

        def _classify_live_source(hint: str) -> str:
            h = str(hint or "").strip().lower()
            if "nearest" in h:
                return "nearest_strike"
            # Canonical Market Quote tag is exact_quote; keep exact_contract* aliases.
            if "exact_quote" in h or "exact_contract" in h or h == "exit_grade":
                return "exact_quote"
            if "fresh_chain" in h or "bridge" in h:
                return h if h else "fresh_chain_refresh"
            if "chain_strike" in h:
                return "chain_strike_match"
            if h in ("cached_quote", "cached", "cache"):
                return "cached_quote"
            # Never invent exact-contract from an empty/unknown hint.
            return h or "unverified_quote"

        if use_bid_executable and quote_is_fresh:
            from modules.option_executable_pricing import long_option_sell_to_close_price

            exec_px = long_option_sell_to_close_price(
                bid=bid,
                ask=ask,
                mid=mark,
                last=last,
            )
            if exec_px > 0:
                raw_effective = exec_px
                source = _classify_live_source(quote_hint)
                detail["quote_slice"] = "bid_executable"
        elif mark is not None and quote_is_fresh:
            detail["option_mark"] = mark
            raw_effective = mark
            source = _classify_live_source(quote_hint)
            detail["quote_slice"] = "mark"
        elif bid is not None and ask is not None and quote_is_fresh:
            mid = (bid + ask) / 2.0
            if mid > 0:
                raw_effective = mid
                source = _classify_live_source(quote_hint)
                detail["quote_slice"] = "mid"
        elif last is not None and quote_is_fresh:
            raw_effective = last
            source = _classify_live_source(quote_hint)
            detail["quote_slice"] = "last"
        else:
            cached_price = _f(option_quote.get("cached_price"))
            cached_age = _f(option_quote.get("cached_age_seconds"))
            if cached_price is not None and cached_age is not None and cached_age <= max_age:
                raw_effective = cached_price
                source = "cached_quote"
                detail["quote_age_seconds"] = cached_age
                detail["quote_slice"] = "cached_recent"

    if is_0dte and source in ("delta_estimate", "underlying_proxy"):
        slv = _f(stored_last_valid_option_price) if stored_last_valid_option_price is not None else None
        if slv is not None:
            raw_effective = float(slv)
            source = "last_valid_stale"
            detail["quote_slice"] = "last_valid_stale"
            detail["price_source_telemetry"] = "last_valid"
            detail["stale_last_valid_option"] = True
            detail["synthetic_pricing_active"] = False
        else:
            use_modeled = isinstance(delta_at_entry, (int, float)) and float(delta_at_entry) > 0
            base_syn = float(modeled) if use_modeled else float(proxy)
            src_syn = "synthetic_modeled" if use_modeled else "synthetic_proxy"
            raw_effective = _apply_synthetic_conservative_premium(
                base_syn,
                last_valid_cap=None,
                is_short_premium=bool(is_short_premium),
                cfg=cfg,
            )
            source = src_syn
            detail["quote_slice"] = "synthetic"
            detail["price_source_telemetry"] = "synthetic"
            detail["synthetic_pricing_active"] = True
    elif source == "delta_estimate":
        if isinstance(delta_at_entry, (int, float)) and float(delta_at_entry) > 0:
            raw_effective = modeled
            source = "delta_estimate"
            detail["quote_slice"] = "modeled_delta"
        else:
            raw_effective = proxy
            source = "underlying_proxy"
            detail["quote_slice"] = "underlying_proxy"
    else:
        raw_effective = max(0.01, float(raw_effective))

    if source in (
        "exact_quote",
        "nearest_strike",
        "chain_strike_match",
        "cached_quote",
        "unverified_quote",
        "fresh_chain_refresh",
        "fresh_chain_force_refresh",  # legacy
        "fresh_chain_bridge",
    ) or str(source or "").strip().lower() == "exact":
        # Bare ``exact`` is legacy chain strike match — still live telemetry.
        detail["price_source_telemetry"] = "live"
        detail["synthetic_pricing_active"] = False
        if str(source or "").strip().lower() == "exact":
            source = "chain_strike_match"

    if cfg.require_live_quotes and (not is_0dte) and source in ("delta_estimate", "underlying_proxy"):
        oq_r = option_quote if isinstance(option_quote, dict) else None
        if not oq_r:
            detail["live_degraded_reason"] = "no_quote"
        else:
            has_any_quote_field = any(
                _f(option_quote.get(k)) is not None
                for k in ("bid", "ask", "mark", "mid_price", "last", "cached_price")
            )
            detail["live_degraded_reason"] = "stale_quote" if has_any_quote_field else "no_quote"
        hold_px = previous_effective if previous_effective is not None and float(previous_effective) > 0 else max(0.01, float(entry_premium))
        raw_effective = float(hold_px)
        source = "live_quote_unavailable"
        detail["pricing_degraded"] = True
        detail["live_quotes_strict_block"] = True
        detail["quote_slice"] = "hold_last_or_entry"

    if detail.get("price_source_telemetry") is None:
        if source in (
            "exact_quote",
            "nearest_strike",
            "chain_strike_match",
            "cached_quote",
            "unverified_quote",
            "fresh_chain_refresh",
            "fresh_chain_force_refresh",  # legacy
            "fresh_chain_bridge",
        ):
            detail["price_source_telemetry"] = "live"
        elif source == "last_valid_stale":
            detail["price_source_telemetry"] = "last_valid"
        elif source in ("synthetic_modeled", "synthetic_proxy"):
            detail["price_source_telemetry"] = "synthetic"
        elif source in ("delta_estimate", "underlying_proxy"):
            detail["price_source_telemetry"] = "synthetic"
        elif source == "live_quote_unavailable":
            detail["price_source_telemetry"] = "last_valid"

    if is_0dte and detail.get("price_source_telemetry") not in (None, "live"):
        detail["pricing_degraded"] = True

    fast_market = False
    if is_0dte and float(entry_underlying or 0.0) > 0:
        move_abs = abs(float(current_underlying) - float(entry_underlying))
        if move_abs > float(cfg.option_0dte_fast_underlying_move_abs):
            fast_market = True
        if seconds_since_entry is not None and float(seconds_since_entry) < float(cfg.option_0dte_fast_entry_minutes) * 60.0:
            fast_market = True

    absurd_raw = _premium_absurd_vs_entry_modeled(
        float(raw_effective or 0.0),
        float(entry_premium),
        float(modeled or 0.0),
        cfg,
    )
    if absurd_raw:
        detail["absurd_live_premium_rejected"] = True
        detail["absurd_live_premium_raw"] = round(float(raw_effective or 0.0), 6)
        # Collapse ticks must not keep "live" telemetry (would refresh last_valid + live clock).
        try:
            ep_chk = float(entry_premium or 0.0)
            p_chk = float(raw_effective or 0.0)
        except (TypeError, ValueError):
            ep_chk, p_chk = 0.0, 0.0
        _collapse_mult = float(
            getattr(cfg, "premium_absurd_collapse_mult_from_entry", 0.08) or 0.08
        )
        collapse = bool(
            ep_chk >= 0.05 and p_chk + 1e-12 < ep_chk * max(0.01, _collapse_mult)
        )
        if collapse:
            hold_candidates = []
            for cand in (
                previous_effective,
                stored_last_valid_option_price,
                entry_premium,
            ):
                try:
                    cf = float(cand) if cand is not None else 0.0
                except (TypeError, ValueError):
                    cf = 0.0
                if cf > p_chk + 1e-12:
                    hold_candidates.append(cf)
            if hold_candidates:
                raw_effective = max(hold_candidates)
                source = "last_valid_stale"
                detail["quote_slice"] = "last_valid_absurd_collapse"
                detail["price_source_telemetry"] = "last_valid"
                detail["stale_last_valid_option"] = True
                detail["synthetic_pricing_active"] = False

    if fast_market and not absurd_raw:
        effective = max(0.01, float(raw_effective))
        detail["clamp_skipped_fast_market"] = True
    elif (
        is_0dte
        and str(detail.get("quote_slice") or "") == "bid_executable"
        and bool(getattr(cfg, "dte_true_to_live_executable_marks", True))
        and not absurd_raw
    ):
        effective = max(0.01, float(raw_effective))
        detail["clamp_skipped_exit_grade_bid"] = True
    else:
        effective = _clamp_premium_step(raw_effective, previous_effective, entry_premium, cfg)

    if absurd_raw:
        mod_cap = float(modeled or 0.0)
        if mod_cap > 0 and float(effective or 0.0) > mod_cap:
            effective = min(float(effective), mod_cap * 1.15)
        detail["pricing_degraded"] = True
        if detail.get("price_source_telemetry") == "live":
            detail["live_degraded_reason"] = "absurd_live_premium"
            # High phantom bids: keep mark path but do not claim clean live for clocks.
            detail["price_source_telemetry"] = "last_valid"
            detail["live_clock_hold_absurd"] = True

    detail["effective_premium_used"] = round(effective, 6)
    detail["premium_source"] = source
    detail["fallback_used"] = source not in {
        "exact_quote",
        "nearest_strike",
        "chain_strike_match",
        "cached_quote",
        "unverified_quote",
        "fresh_chain_refresh",
        "fresh_chain_force_refresh",  # legacy
        "fresh_chain_bridge",
    }
    max_exit_age = float(max(1.0, getattr(cfg, "quote_exit_grade_max_age_seconds", 30.0)))
    stale_age = float(max(max_exit_age, getattr(cfg, "quote_stale_max_age_seconds", 90.0)))
    # Prefer explicit quote_age_seconds; else derive from quote_age_ms / freshness basis.
    qa = detail.get("quote_age_seconds")
    if qa is None and isinstance(option_quote, dict):
        q_ms = option_quote.get("quote_age_ms")
        if q_ms is not None:
            try:
                qa = float(q_ms) / 1000.0
                detail["quote_age_seconds"] = qa
            except (TypeError, ValueError):
                qa = None
        detail["quote_age_basis"] = option_quote.get("quote_age_basis") or detail.get("quote_age_basis")
        detail["freshness_status"] = option_quote.get("freshness_status") or detail.get("freshness_status")
    qa_f = float(qa) if isinstance(qa, (int, float)) else 0.0
    try:
        from modules.exit_grade_durability import (
            canonicalize_quote_source,
            premium_source_is_live_contract_quote,
            quote_source_is_exact_contract,
        )

        source = canonicalize_quote_source(source)
        live_ish = bool(
            premium_source_is_live_contract_quote(source)
            or source
            in {
                "unverified_quote",
                "nearest_strike",
            }
        )
        exact_contract = bool(quote_source_is_exact_contract(source))
    except Exception:
        live_ish = source in {
            "exact_quote",
            "nearest_strike",
            "chain_strike_match",
            "cached_quote",
            "unverified_quote",
            "fresh_chain_refresh",
            "fresh_chain_force_refresh",  # legacy
            "fresh_chain_bridge",
        }
        exact_contract = "exact_contract" in str(source or "").lower()
    if live_ish:
        # Age present (incl. 0.0 receipt) → live when within max; missing age → cached_recent.
        if qa is not None and qa_f <= max_exit_age:
            mark_quality = "live_single_leg"
        else:
            mark_quality = "cached_recent"
            if exact_contract:
                status = str(
                    (option_quote or {}).get("freshness_status")
                    if isinstance(option_quote, dict)
                    else ""
                )
                basis = str(
                    (option_quote or {}).get("quote_age_basis")
                    if isinstance(option_quote, dict)
                    else ""
                )
                detail["freshness_block_reason"] = status or basis or "quote_age_missing"
    elif source == "last_valid_stale":
        mark_quality = "stale_last_valid" if qa_f <= stale_age else "missing"
    elif source in {"synthetic_modeled", "synthetic_proxy", "delta_estimate", "underlying_proxy"}:
        mark_quality = "synthetic"
    else:
        mark_quality = "missing"
    detail["mark_quality"] = mark_quality
    max_exit_spread = float(getattr(cfg, "tline_exit_grade_max_spread_pct", 0.30) or 0.30)
    # Wide-spread / sanity applies whenever we have a live-ish quote.
    if live_ish or (
        detail.get("option_bid") is not None and detail.get("option_ask") is not None
    ):
        if _tline_spread_too_wide(spread_pct, max_exit_spread):
            mark_quality = "wide_spread"
            detail["mark_quality"] = mark_quality
        elif not _tline_bid_ask_sanity_ok(detail, float(raw_effective or 0.0)):
            mark_quality = "wide_spread"
            detail["mark_quality"] = mark_quality
        elif _premium_absurd_vs_entry_modeled(
            float(raw_effective or 0.0),
            float(entry_premium),
            float(modeled or 0.0),
            cfg,
        ):
            if str(detail.get("quote_slice") or "") == "last_valid_absurd_collapse":
                mark_quality = "stale_last_valid"
                detail["mark_quality"] = mark_quality
            else:
                mark_quality = "wide_spread"
                detail["mark_quality"] = mark_quality
                detail["absurd_live_premium_rejected"] = True

    # Exact-contract Exit-grade only: identity-matched Market Quote — never nearest/chain.
    identity_ok = True
    if isinstance(option_quote, dict):
        if "contract_identity_matched" in option_quote or "contract_matched" in option_quote:
            identity_ok = bool(
                option_quote.get("contract_identity_matched")
                if "contract_identity_matched" in option_quote
                else option_quote.get("contract_matched")
            )
    exact_contract_src = "exact_contract" in str(source or "").lower()
    detail["mark_is_exit_grade"] = bool(
        exact_contract_src
        and identity_ok
        and mark_quality == "live_single_leg"
    )
    if "nearest" in str(source or "").lower() or "chain_strike" in str(source or "").lower():
        detail["mark_is_exit_grade"] = False
        if mark_quality == "live_single_leg":
            # Keep live valuation quality for degraded path, but never Exit-grade.
            detail["mark_quality_exit_blocked"] = "nearest_or_chain_not_exact_contract"
    detail["leg_completeness"] = "single_leg"
    detail["using_synthetic_mark"] = bool(mark_quality == "synthetic")
    detail["using_stale_mark"] = bool(mark_quality in {"stale_last_valid", "cached_recent"})
    if (
        is_0dte
        and mark_quality == "wide_spread"
        and str(detail.get("quote_slice") or "") != "last_valid_absurd_collapse"
        and str(option_side or "").strip().lower() in ("call", "put")
        and bool(getattr(cfg, "dte_true_to_live_executable_marks", True))
    ):
        conservative = _tline_conservative_executable_premium(
            prem_detail=detail,
            mid_premium=float(effective or 0.0),
            option_side=str(option_side or ""),
            use_bid_for_long=bool(getattr(cfg, "dte_use_bid_for_long_exit", True)),
        )
        if float(modeled or 0.0) > 0 and conservative > 0:
            effective = min(conservative, float(modeled))
        elif float(modeled or 0.0) > 0:
            effective = float(modeled)
        elif conservative > 0:
            effective = conservative
        detail["effective_premium_used"] = round(float(effective), 6)
        detail["true_to_live_wide_spread_mark"] = True
    if is_0dte and _option_price_resolution_audit_should_emit(cfg, trade_id, position_symbol):
        log.info(
            "OPTION_PRICE_RESOLUTION_AUDIT | trade_id=%s | symbol=%s | source=%s | quote_slice=%s | raw_effective=%.6f | effective=%.6f | "
            "quote_age_seconds=%s | spread_pct=%s | clamp_skipped=%s | fast_market=%s | audit_throttle_sec=%.1f",
            str(trade_id or "-"),
            position_symbol,
            source,
            detail.get("quote_slice"),
            float(raw_effective),
            float(effective),
            detail.get("quote_age_seconds"),
            detail.get("spread_pct"),
            str(bool(detail.get("clamp_skipped_fast_market"))).lower(),
            str(fast_market).lower(),
            float(getattr(cfg, "option_price_resolution_audit_min_seconds", 30.0) or 0.0),
        )
    return effective, source, detail


def resolve_option_premium(
    *,
    entry_underlying: float,
    current_underlying: float,
    option_side: str,
    entry_premium: float,
    cfg: OptionStealthConfig,
    option_quote: Optional[Dict[str, Any]],
    previous_effective: Optional[float],
    delta_at_entry: float,
    strike: float,
    position_symbol: str,
    is_0dte: bool = False,
    seconds_since_entry: Optional[float] = None,
    trade_id: str = "",
    stored_last_valid_option_price: Optional[float] = None,
    is_short_premium: bool = False,
) -> Tuple[float, str, Dict[str, Any]]:
    """Backward-compatible alias for resolve_option_price()."""
    return resolve_option_price(
        entry_underlying=entry_underlying,
        current_underlying=current_underlying,
        option_side=option_side,
        entry_premium=entry_premium,
        cfg=cfg,
        option_quote=option_quote,
        previous_effective=previous_effective,
        delta_at_entry=delta_at_entry,
        strike=strike,
        position_symbol=position_symbol,
        is_0dte=is_0dte,
        seconds_since_entry=seconds_since_entry,
        trade_id=trade_id,
        stored_last_valid_option_price=stored_last_valid_option_price,
        is_short_premium=is_short_premium,
    )


def _normalize_position_type(position_type: Optional[str], option_side: str) -> str:
    raw = str(position_type or "").strip().lower()
    if raw in (
        OptionPositionType.SINGLE_LEG_LONG_CALL.value,
        OptionPositionType.SINGLE_LEG_LONG_PUT.value,
        OptionPositionType.DEBIT_SPREAD.value,
        OptionPositionType.CREDIT_SPREAD.value,
    ):
        return raw
    if raw in ("lotto", "single_leg", "long_option"):
        return (
            OptionPositionType.SINGLE_LEG_LONG_PUT.value
            if str(option_side).lower() == "put"
            else OptionPositionType.SINGLE_LEG_LONG_CALL.value
        )
    return (
        OptionPositionType.SINGLE_LEG_LONG_PUT.value
        if str(option_side).lower() == "put"
        else OptionPositionType.SINGLE_LEG_LONG_CALL.value
    )


def validate_stealth_registration_inputs(
    *,
    norm_position_type: str,
    underlying_symbol: str,
    expiration_ymd: str,
    norm_legs: List[Dict[str, Any]],
    spread_entry_value: float,
    entry_debit: Optional[float],
    entry_credit: Optional[float],
) -> Tuple[List[str], List[str]]:
    """
    Pre-register completeness check by structural position type.
    Returns (missing_or_weak, warnings). Does not raise — callers log and may apply fallbacks.
    """
    missing: List[str] = []
    warnings: List[str] = []
    if not str(underlying_symbol or "").strip():
        warnings.append("underlying_symbol_empty")
    if not str(expiration_ymd or "").strip():
        warnings.append("expiration_ymd_empty")
    pt = norm_position_type
    if pt in (
        OptionPositionType.SINGLE_LEG_LONG_CALL.value,
        OptionPositionType.SINGLE_LEG_LONG_PUT.value,
    ):
        if len(norm_legs) != 1:
            warnings.append(f"single_leg_expected_1_leg_got_{len(norm_legs)}")
        if spread_entry_value <= 0:
            missing.append("entry_value_non_positive")
    elif pt == OptionPositionType.DEBIT_SPREAD.value:
        if len(norm_legs) < 2:
            warnings.append(f"debit_spread_expected_2plus_legs_got_{len(norm_legs)}")
        if (entry_debit is None or float(entry_debit) <= 0) and spread_entry_value <= 0:
            missing.append("entry_debit_or_entry_value")
    elif pt == OptionPositionType.CREDIT_SPREAD.value:
        if len(norm_legs) < 2:
            warnings.append(f"credit_spread_expected_2plus_legs_got_{len(norm_legs)}")
        if (entry_credit is None or float(entry_credit) <= 0) and spread_entry_value <= 0:
            missing.append("entry_credit_or_entry_value")
    return missing, warnings


def _normalize_legs(legs: Optional[List[Dict[str, Any]]], option_side: str, strike: float, delta: float) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if isinstance(legs, list):
        for idx, leg in enumerate(legs):
            if not isinstance(leg, dict):
                continue
            out.append(
                {
                    "leg_id": leg.get("leg_id") or leg.get("symbol") or f"leg_{idx+1}",
                    "symbol": leg.get("symbol"),
                    "long_or_short": str(leg.get("long_or_short") or "long").lower(),
                    "option_side": str(leg.get("option_side") or option_side or "").lower(),
                    "strike": float(leg.get("strike") or 0.0),
                    "quantity": int(max(1, int(leg.get("quantity") or 1))),
                    "entry_price": float(leg.get("entry_price") or 0.0),
                    "delta_at_entry": float(leg.get("delta_at_entry") or 0.0),
                }
            )
    if out:
        return out
    return [
        {
            "leg_id": "single_leg",
            "symbol": None,
            "long_or_short": "long",
            "option_side": str(option_side or "").lower(),
            "strike": float(strike or 0.0),
            "quantity": 1,
            "entry_price": 0.0,
            "delta_at_entry": float(delta or 0.0),
        }
    ]


def _mtm_positive_float(x: Any) -> Optional[float]:
    try:
        v = float(x)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def _leg_mark_mid_or_last(
    leg_quote: Optional[Dict[str, Any]],
    leg_role: str = "",
) -> Tuple[float, str, str]:
    """
    Per-leg mark for 0DTE monitoring/close:
    - long: bid (STC) when true-to-live enabled
    - short: ask (BTC) when true-to-live enabled
    Otherwise mid-derived marks with conservative scaling for spreads.
    """
    if not isinstance(leg_quote, dict):
        return 0.0, "missing", ""
    role = str(leg_role or "").lower()
    try:
        from modules.option_executable_pricing import (
            leg_executable_mark_price,
            true_to_live_executable_marks_enabled,
        )
    except ImportError:
        true_to_live_executable_marks_enabled = lambda: False  # type: ignore
        leg_executable_mark_price = None  # type: ignore

    bid = _mtm_positive_float(leg_quote.get("bid"))
    ask = _mtm_positive_float(leg_quote.get("ask"))
    mid = _mtm_positive_float(leg_quote.get("mid_price")) or _mtm_positive_float(leg_quote.get("mark"))
    last = _mtm_positive_float(leg_quote.get("last"))
    if leg_executable_mark_price is not None and true_to_live_executable_marks_enabled():
        px = float(
            leg_executable_mark_price(
                leg_role=role,
                bid=bid,
                ask=ask,
                mid=mid,
                last=last,
                true_to_live=True,
            )
        )
        if px > 0:
            src = "bid_executable" if role == "long" else "ask_executable"
            adj = "long_bid" if role == "long" else "short_ask"
            return px, src, adj
    if mid is not None:
        px = float(mid)
        adj = ""
        if role == "long":
            px *= 0.98
            adj = "long_mid_0.98"
        elif role == "short":
            px *= 1.02
            adj = "short_mid_1.02"
        return px, "mid", adj
    last = _mtm_positive_float(leg_quote.get("last"))
    if last is not None:
        return float(last), "last", ""
    bid = _mtm_positive_float(leg_quote.get("bid"))
    ask = _mtm_positive_float(leg_quote.get("ask"))
    if bid is not None and ask is not None and (bid + ask) > 0:
        px = (float(bid) + float(ask)) / 2.0
        adj = ""
        if role == "long":
            px *= 0.98
            adj = "long_bidask_mid_0.98"
        elif role == "short":
            px *= 1.02
            adj = "short_bidask_mid_1.02"
        return px, "bidask_mid", adj
    return 0.0, "missing", ""


def _pick_long_short_legs(legs: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    long_leg: Optional[Dict[str, Any]] = None
    short_leg: Optional[Dict[str, Any]] = None
    for leg in legs:
        if not isinstance(leg, dict):
            continue
        los = str(leg.get("long_or_short") or "").lower()
        if los == "long" and long_leg is None:
            long_leg = leg
        elif los == "short" and short_leg is None:
            short_leg = leg
    return long_leg, short_leg


def resolve_spread_net_value(
    *,
    current_underlying: float,
    entry_underlying: float,
    cfg: OptionStealthConfig,
    previous_effective: Optional[float],
    position_symbol: str,
    legs: List[Dict[str, Any]],
    option_quote: Optional[Dict[str, Any]],
    entry_value: float,
    position_type: str = OptionPositionType.DEBIT_SPREAD.value,
    trade_id: str = "",
) -> Tuple[float, str, Dict[str, Any]]:
    """
    Vertical spread mark-to-market from leg quotes only (no resolve_option_price, no abs net,
    no entry/cached inflation, no _clamp_premium_step).

    Debit: long (conservative sell mark: mid*0.98 or last) minus short (conservative cover: mid*1.02 or last),
    clamped to [0, strike width].
    Credit: short - long (cost to close), clamped to [0, strike width].
    """
    _ = (current_underlying, entry_underlying, cfg, previous_effective)

    detail: Dict[str, Any] = {
        "leg_quote_sources": [],
        "spread_current_value": None,
        "spread_entry_value": float(entry_value or 0.0),
        "spread_premium_source_quality": "mtm_incomplete",
        "spread_monitoring_mode": "spread_mtm",
    }
    if not legs:
        detail["spread_monitoring_mode"] = "spread_mtm_no_legs"
        detail["spread_premium_source_quality"] = "no_legs"
        detail["price_source_telemetry"] = "synthetic"
        return 0.0, "spread_mtm_no_legs", detail

    leg_quotes_by_id: Dict[str, Dict[str, Any]] = {}
    if isinstance(option_quote, dict):
        sca_in = option_quote.get("spread_chain_source_audit")
        if isinstance(sca_in, dict):
            detail["spread_chain_source_audit"] = dict(sca_in)
        for lq in option_quote.get("leg_quotes", []) or []:
            if not isinstance(lq, dict):
                continue
            leg_id = str(lq.get("leg_id") or lq.get("symbol") or "")
            if leg_id:
                leg_quotes_by_id[leg_id] = lq

    long_leg, short_leg = _pick_long_short_legs(legs)
    if long_leg is None or short_leg is None:
        detail["spread_monitoring_mode"] = "spread_mtm_missing_leg_role"
        detail["spread_premium_source_quality"] = "incomplete_structure"
        detail["price_source_telemetry"] = "synthetic"
        return 0.0, "spread_mtm_missing_leg_role", detail

    long_strike = float(long_leg.get("strike") or 0.0)
    short_strike = float(short_leg.get("strike") or 0.0)
    spread_width = abs(float(short_strike) - float(long_strike))

    long_id = str(long_leg.get("leg_id") or long_leg.get("symbol") or "")
    short_id = str(short_leg.get("leg_id") or short_leg.get("symbol") or "")
    long_q = leg_quotes_by_id.get(long_id) if long_id else None
    short_q = leg_quotes_by_id.get(short_id) if short_id else None

    long_px, long_src, long_mid_adj = _leg_mark_mid_or_last(long_q, "long")
    short_px, short_src, short_mid_adj = _leg_mark_mid_or_last(short_q, "short")
    def _qf(q: Optional[Dict[str, Any]], key: str) -> float:
        try:
            if not isinstance(q, dict):
                return 0.0
            v = float(q.get(key) or 0.0)
            return v if v > 0 else 0.0
        except (TypeError, ValueError):
            return 0.0
    long_bid = _qf(long_q, "bid")
    long_ask = _qf(long_q, "ask")
    short_bid = _qf(short_q, "bid")
    short_ask = _qf(short_q, "ask")
    long_mid_live = ((long_bid + long_ask) / 2.0) if long_bid > 0 and long_ask > 0 else 0.0
    short_mid_live = ((short_bid + short_ask) / 2.0) if short_bid > 0 and short_ask > 0 else 0.0
    long_age = _qf(long_q, "quote_age_seconds")
    short_age = _qf(short_q, "quote_age_seconds")

    is_credit = str(position_type or "").strip().lower() == OptionPositionType.CREDIT_SPREAD.value
    if is_credit:
        raw_spread = float(short_px) - float(long_px)
        mid_value_raw = (short_mid_live - long_mid_live) if short_mid_live > 0 and long_mid_live > 0 else raw_spread
        liq_value_raw = (short_ask - long_bid) if short_ask > 0 and long_bid > 0 else raw_spread
    else:
        raw_spread = float(long_px) - float(short_px)
        mid_value_raw = (long_mid_live - short_mid_live) if long_mid_live > 0 and short_mid_live > 0 else raw_spread
        liq_value_raw = (long_bid - short_ask) if long_bid > 0 and short_ask > 0 else raw_spread

    if spread_width > 0.0:
        eff = max(0.0, min(float(spread_width), float(raw_spread)))
    else:
        eff = max(0.0, float(raw_spread))

    if float(eff) < 0.0:
        log.error(
            "SPREAD_VALUE_NEGATIVE | trade_id=%s | symbol=%s | spread_value=%.6f | raw_spread=%.6f | spread_width=%.6f",
            str(trade_id or ""),
            str(position_symbol or ""),
            float(eff),
            float(raw_spread),
            float(spread_width),
        )
    if spread_width > 0.0:
        if float(raw_spread) > float(spread_width) + 0.05:
            log.error(
                "SPREAD_VALUE_EXCEEDS_WIDTH | trade_id=%s | symbol=%s | phase=raw_pre_clamp | "
                "raw_spread=%.6f | spread_width=%.6f | clamped=%.6f",
                str(trade_id or ""),
                str(position_symbol or ""),
                float(raw_spread),
                float(spread_width),
                float(eff),
            )
        if float(eff) > float(spread_width) + 0.05:
            log.error(
                "SPREAD_VALUE_EXCEEDS_WIDTH | trade_id=%s | symbol=%s | phase=post_clamp | "
                "spread_value=%.6f | spread_width=%.6f",
                str(trade_id or ""),
                str(position_symbol or ""),
                float(eff),
                float(spread_width),
            )

    entry_debit = float(entry_value or 0.0)
    qty = int(max(1, int(long_leg.get("quantity") or short_leg.get("quantity") or 1)))

    if long_px <= 0.0 or short_px <= 0.0:
        src = "spread_mtm_incomplete_quotes"
        detail["spread_premium_source_quality"] = "incomplete_quotes"
        detail["spread_monitoring_mode"] = "spread_mtm_incomplete_quotes"
    else:
        src = "spread_mtm_mid_last"
        detail["spread_premium_source_quality"] = "mtm_leg_mid_last"

    detail["leg_quote_sources"] = [
        {
            "leg_id": long_id,
            "source": long_src,
            "value": round(float(long_px), 6),
            "long_or_short": "long",
            "quantity": qty,
            "mid_conservative": long_mid_adj or None,
        },
        {
            "leg_id": short_id,
            "source": short_src,
            "value": round(float(short_px), 6),
            "long_or_short": "short",
            "quantity": qty,
            "mid_conservative": short_mid_adj or None,
        },
    ]
    mid_value = max(0.0, min(float(spread_width), float(mid_value_raw))) if spread_width > 0 else max(0.0, float(mid_value_raw))
    liq_value = max(0.0, min(float(spread_width), float(liq_value_raw))) if spread_width > 0 else max(0.0, float(liq_value_raw))
    detail["spread_current_value"] = round(float(eff), 6)
    detail["spread_mid_value"] = round(float(mid_value), 6)
    detail["spread_liquidation_value"] = round(float(liq_value), 6)
    detail["long_leg_quote_age_seconds"] = float(long_age)
    detail["short_leg_quote_age_seconds"] = float(short_age)
    detail["long_leg_bid"] = float(long_bid)
    detail["long_leg_ask"] = float(long_ask)
    detail["long_leg_mid"] = float(long_mid_live)
    detail["short_leg_bid"] = float(short_bid)
    detail["short_leg_ask"] = float(short_ask)
    detail["short_leg_mid"] = float(short_mid_live)
    detail["leg_completeness"] = "complete" if (long_mid_live > 0 and short_mid_live > 0) else ("partial" if (long_px > 0 or short_px > 0) else "missing")
    if str(detail.get("spread_premium_source_quality") or "") == "mtm_leg_mid_last" and long_px > 0.0 and short_px > 0.0:
        detail["price_source_telemetry"] = "live"
    else:
        detail["price_source_telemetry"] = "synthetic"
    max_age = float(max(1.0, getattr(cfg, "quote_exit_grade_max_age_seconds", 30.0)))
    is_fresh_both = bool(long_age > 0 and short_age > 0 and long_age <= max_age and short_age <= max_age)
    mark_quality = (
        "live_two_leg_spread"
        if (detail["leg_completeness"] == "complete" and is_fresh_both)
        else "partial_leg"
        if detail["leg_completeness"] == "partial"
        else "missing"
    )
    detail["mark_quality"] = mark_quality
    detail["mark_is_exit_grade"] = bool(mark_quality == "live_two_leg_spread")
    detail["using_synthetic_mark"] = bool(detail.get("price_source_telemetry") != "live")
    detail["using_stale_mark"] = bool(not is_fresh_both and detail["leg_completeness"] != "missing")
    entry_debit_for_pnl = float(entry_debit if entry_debit > 0 else entry_value or 0.0)
    if entry_debit_for_pnl > 0:
        detail["current_pnl_pct_mid"] = float((mid_value - entry_debit_for_pnl) / entry_debit_for_pnl)
        detail["current_pnl_pct_liquidation"] = float((liq_value - entry_debit_for_pnl) / entry_debit_for_pnl)
    else:
        detail["current_pnl_pct_mid"] = 0.0
        detail["current_pnl_pct_liquidation"] = 0.0
    detail["long_price"] = float(long_px)
    detail["short_price"] = float(short_px)
    detail["raw_spread"] = float(raw_spread)
    detail["spread_width"] = float(spread_width)
    detail["entry_debit"] = float(entry_debit)
    detail["pnl_dollars"] = float(pnl_dollars)
    detail["quantity"] = int(qty)

    sc_audit = detail.get("spread_chain_source_audit")
    ch_src = str(sc_audit.get("source")) if isinstance(sc_audit, dict) and sc_audit.get("source") is not None else ""
    ch_ts = str(sc_audit.get("timestamp")) if isinstance(sc_audit, dict) and sc_audit.get("timestamp") is not None else ""
    log.info(
        "SPREAD_VALUATION_AUDIT | trade_id=%s | symbol=%s | position_type=%s | long_price=%.6f | short_price=%.6f | "
        "raw_spread=%.6f | spread_value=%.6f | spread_width=%.6f | entry_debit=%.6f | qty=%d | pnl_dollars=%.2f | "
        "long_src=%s | short_src=%s | mtm_source=%s | chain_source=%s | chain_ts=%s",
        str(trade_id or ""),
        str(position_symbol or ""),
        str(position_type or ""),
        float(long_px),
        float(short_px),
        float(raw_spread),
        float(eff),
        float(spread_width),
        float(entry_debit),
        int(qty),
        float(pnl_dollars),
        str(long_src),
        str(short_src),
        str(src),
        ch_src or "n/a",
        ch_ts or "n/a",
    )
    log.info(
        "OPTION_SPREAD_NET_MARK_AUDIT | trade_id=%s | symbol=%s | position_type=%s | option_side=%s | entry_net_debit=%.6f | "
        "mid_value=%.6f | liquidation_value=%.6f | current_pnl_pct_mid=%.4f | current_pnl_pct_liquidation=%.4f | mark_quality=%s | "
        "mark_is_exit_grade=%s | quote_source=%s | quote_age_seconds=%.1f | long_leg_bid=%.6f | long_leg_ask=%.6f | long_leg_mid=%.6f | "
        "short_leg_bid=%.6f | short_leg_ask=%.6f | short_leg_mid=%.6f | leg_completeness=%s",
        str(trade_id or ""),
        str(position_symbol or ""),
        str(position_type or ""),
        str((long_leg or {}).get("option_side") or ""),
        float(entry_debit_for_pnl),
        float(mid_value),
        float(liq_value),
        float(detail.get("current_pnl_pct_mid") or 0.0),
        float(detail.get("current_pnl_pct_liquidation") or 0.0),
        str(mark_quality),
        str(bool(detail.get("mark_is_exit_grade"))).lower(),
        str(src),
        float(max(long_age, short_age)),
        float(long_bid),
        float(long_ask),
        float(long_mid_live),
        float(short_bid),
        float(short_ask),
        float(short_mid_live),
        str(detail.get("leg_completeness") or "missing"),
    )

    try:
        from modules.option_executable_pricing import true_to_live_executable_marks_enabled
    except ImportError:
        true_to_live_executable_marks_enabled = lambda: False  # type: ignore
    settlement_value = float(liq_value if true_to_live_executable_marks_enabled() else eff)
    if is_credit:
        pnl_dollars = (entry_debit - settlement_value) * float(qty) * 100.0
    else:
        pnl_dollars = (settlement_value - entry_debit) * float(qty) * 100.0
    detail["pnl_dollars"] = float(pnl_dollars)
    if true_to_live_executable_marks_enabled() and settlement_value != float(eff):
        detail["spread_monitor_executable_value"] = settlement_value
        detail["spread_monitor_mid_blend_value"] = float(eff)
    return settlement_value, str(src), detail


def _line_value_at(trendline: Dict[str, Any], ts: datetime) -> float:
    slope = float(trendline.get("slope_per_second", 0.0) or 0.0)
    intercept = float(trendline.get("intercept", 0.0) or 0.0)
    return (slope * ts.timestamp()) + intercept


class TrendlineOptionsStealthEngine:
    """
    Single normal 0DTE options exit brain (premium-based) for ORB1 Breakout + Trendline 0DTE.
    ORB SO ETF path remains separate in prime_stealth_trailing_tp.py.
    """

    def __init__(self, config: Optional[OptionStealthConfig] = None) -> None:
        self.config = config or load_option_stealth_config()
        self._session: Dict[str, Any] = {
            "opens": 0,
            "breakeven_exits": 0,
            "trailing_exits": 0,
            "invalidation_exits": 0,
            "no_progress_exits": 0,
            "time_cap_exits": 0,
            "eod_exits": 0,
            "pnl_pcts": [],
            "max_excursions": [],
            "drawdowns_hwm": [],
            "min_to_breakeven": [],
            "min_to_trailing": [],
            "exit_premium_sources": {"exact": 0, "nearest": 0, "delta_estimate": 0, "underlying_proxy": 0},
            "degraded_quote_count": 0,
            "degraded_exit_count": 0,
            "skipped_entry_due_to_no_quote": 0,
            "live_quote_tick_hits": 0,
            "live_quote_tick_total": 0,
        }
        self._fix_session_types()
        log.info(
            "TRENDLINE_PIPELINE | stage=option_stealth_init | "
            "trailing_trigger=%.3f | base_trail=%.3f | tp_ladder_disabled=%s | "
            "tline_time_exit_min=%.0f | tline_no_progress_min=%.0f | "
            "tline_trail_mult=%.2f | tline_chop_hold_pnl=%.3f | "
            "breakeven_exit_min=%.0f | scratch_exit_min=%.0f | breakeven_max_pnl=%.4f | scratch_below_pnl=%.4f",
            self.config.trailing_trigger_pct,
            self.config.base_trailing_pct,
            self.config.disable_tp_ladder,
            self.config.tline_time_exit_minutes,
            self.config.tline_no_progress_exit_minutes,
            self.config.tline_trailing_trigger_mult,
            self.config.tline_chop_hold_min_pnl_pct,
            float(getattr(self.config, "dte_breakeven_exit_minutes", 60.0) or 60.0),
            float(getattr(self.config, "dte_scratch_exit_minutes", 62.0) or 62.0),
            float(getattr(self.config, "dte_breakeven_exit_max_pnl_pct", 0.02) or 0.02),
            float(getattr(self.config, "dte_scratch_below_pnl_pct", -0.0005) or -0.0005),
        )
        log.info(
            "0DTE_RUNTIME_CONFIG | revision_id=%s | system_version=%s | outage_seconds=%.1f | data_source_priority=%s | "
            "require_live_option_data=%s | require_live_quotes=%s | quote_fresh_max_age_sec=%.2f | "
            "price_resolution_audit_min_sec=%.1f",
            os.getenv("K_REVISION", "unknown"),
            os.getenv("APP_VERSION", "unknown"),
            float(self.config.option_max_degraded_seconds),
            "live>last_valid>synthetic",
            str(bool(self.config.require_live_option_data)).lower(),
            str(bool(self.config.require_live_quotes)).lower(),
            float(self.config.option_quote_fresh_max_age_seconds),
            float(self.config.option_price_resolution_audit_min_seconds),
        )

    @staticmethod
    def _normalize_dt(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _orb_spread_timeout_exit_relief(
        self,
        *,
        is_orb_0dte: bool,
        is_spread: bool,
        is_trendline_0dte: bool,
        st: OptionStealthState,
        osnap: Dict[str, Any],
        detail: Dict[str, Any],
        prem_src: str,
        prem: float,
        held_min: float,
        mark_is_exit_grade_now: bool,
        mark_quality: str,
        now: datetime,
    ) -> Tuple[bool, str, str]:
        """
        ORB1 Breakout spread-only relief from strict exit-grade + good-tick + timeout-freeze gates
        for timeout-style exits (time / no-progress), using conservative MTM first
        then a hard forced tier so spreads cannot remain open indefinitely on partial_leg quotes.
        """
        if not is_orb_0dte or is_trendline_0dte or not is_spread:
            return False, "", ""
        cfg = self.config
        if not bool(getattr(cfg, "orb_options_spread_degraded_exit_enable", True)):
            return False, "", ""
        if mark_is_exit_grade_now:
            return False, "", ""
        defer_n = int(osnap.get("orb_exit_defer_total") or 0)
        partial_iso = osnap.get("orb_partial_leg_first_iso")
        partial_min = 0.0
        if isinstance(partial_iso, str) and partial_iso.strip():
            try:
                partial_min = max(
                    0.0,
                    (now - self._normalize_dt(datetime.fromisoformat(partial_iso))).total_seconds() / 60.0,
                )
            except Exception:
                partial_min = 0.0
        wd_accum = float(osnap.get("orb_watchdog_gap_accum_sec") or 0.0)
        long_age = float(detail.get("long_leg_quote_age_seconds") or 0.0)
        short_age = float(detail.get("short_leg_quote_age_seconds") or 0.0)
        max_leg_age = max(long_age, short_age)
        lpx = float(detail.get("long_price") or 0.0)
        spx = float(detail.get("short_price") or 0.0)
        spread_val = float(detail.get("spread_current_value") or prem or 0.0)
        src_q = str(detail.get("spread_premium_source_quality") or "")
        unsafe = prem_src in {
            "spread_modeled_fallback",
            "spread_position_value_fallback",
            "delta_estimate",
            "underlying_proxy",
            "synthetic_proxy",
            "synthetic_modeled",
            "synthetic_open_grace",
        }
        abs_max = float(max(15.0, getattr(cfg, "orb_spread_forced_abs_max_minutes", 90.0)))
        forced_min_trade = float(max(5.0, getattr(cfg, "orb_spread_forced_min_trade_minutes", 28.0)))
        forced_partial = float(max(3.0, getattr(cfg, "orb_spread_forced_min_partial_minutes", 20.0)))
        forced_defers = int(max(5, getattr(cfg, "orb_spread_forced_min_deferrals", 55)))
        forced_wd = float(max(60.0, getattr(cfg, "orb_spread_forced_watchdog_accum_seconds", 480.0)))
        allow_incomplete = bool(getattr(cfg, "orb_spread_forced_allow_incomplete_mtm", True))
        forced_ok_src = (not unsafe) or (
            allow_incomplete
            and held_min >= abs_max - 5.0
            and prem_src in ("spread_mtm_incomplete_quotes", "spread_mtm_mid_last")
        )
        forced_price_ok = spread_val > 0.0 and (lpx > 0.0 or spx > 0.0)
        tier_b = forced_ok_src and forced_price_ok and (
            held_min >= abs_max
            or (held_min >= forced_min_trade and partial_min >= forced_partial)
            or defer_n >= forced_defers
            or (wd_accum >= forced_wd and partial_min >= 5.0)
        )
        if tier_b:
            return (
                True,
                "forced",
                f"held_min={held_min:.2f}|partial_min={partial_min:.2f}|defers={defer_n}|wd_accum={wd_accum:.0f}|prem_src={prem_src}|mq={mark_quality}",
            )
        cons_trade = float(max(1.0, getattr(cfg, "orb_spread_conservative_min_trade_minutes", 5.0)))
        cons_partial = float(max(1.0, getattr(cfg, "orb_spread_conservative_min_partial_minutes", 4.0)))
        cons_defers = int(max(3, getattr(cfg, "orb_spread_conservative_min_deferrals", 15)))
        cons_max_age = float(max(30.0, getattr(cfg, "orb_spread_conservative_max_leg_age_sec", 240.0)))
        mtm_ok = (
            prem_src == "spread_mtm_mid_last"
            and src_q == "mtm_leg_mid_last"
            and lpx > 0.0
            and spx > 0.0
            and spread_val > 0.0
        )
        if bool(getattr(cfg, "orb_options_spread_conservative_exit_enable", True)) and mtm_ok:
            if (
                max_leg_age <= cons_max_age
                and held_min >= cons_trade
                and (partial_min >= cons_partial or defer_n >= cons_defers)
            ):
                return (
                    True,
                    "conservative",
                    f"held_min={held_min:.2f}|max_leg_age={max_leg_age:.1f}|defers={defer_n}|partial_min={partial_min:.2f}|prem_src={prem_src}",
                )
        return False, "", ""

    def _orb_trusted_degraded_spread_mark_eligible(
        self,
        *,
        is_orb_0dte: bool,
        is_spread: bool,
        synthetic_quote: bool,
        prem_src: str,
        mark_quality: str,
        quote_age_seconds: float,
        detail: Dict[str, Any],
        u_move_pct_eval: float,
        entry_debit: float,
        spread_mid: float,
        spread_liq: float,
        position_type: str,
    ) -> Tuple[bool, str]:
        """
        ORB1 Breakout spread-only: allow timeout-style exits under partial_leg/cached marks when quotes are stale
        but not garbage — does NOT enable HWM ratchet (see _premium_updates_favorable_hwm).
        """
        cfg = self.config
        if not is_orb_0dte or not is_spread:
            return False, "not_orb_spread"
        if not bool(getattr(cfg, "orb_trusted_degraded_spread_enable", True)):
            return False, "disabled"
        if synthetic_quote:
            return False, "synthetic_quote"
        src = str(prem_src or "").strip().lower()
        mq = str(mark_quality or "").strip().lower()
        unsafe = {
            "spread_modeled_fallback",
            "spread_position_value_fallback",
            "delta_estimate",
            "underlying_proxy",
            "synthetic_proxy",
            "synthetic_modeled",
            "synthetic_open_grace",
        }
        if src in unsafe:
            return False, "unsafe_prem_src"
        allowed_mq = {"partial_leg", "cached_recent", "nearest", "cached_quote"}
        if mq not in allowed_mq:
            return False, f"mark_quality={mq}"
        stale_cap = float(getattr(cfg, "orb_trusted_degraded_mark_max_age_seconds", 90.0) or 90.0)
        if float(quote_age_seconds or 0.0) > stale_cap:
            return False, "quote_age_over_cap"
        long_age = float(detail.get("long_leg_quote_age_seconds") or 0.0)
        short_age = float(detail.get("short_leg_quote_age_seconds") or 0.0)
        fresh_cap = float(getattr(cfg, "orb_trusted_degraded_min_fresh_leg_age_seconds", 45.0) or 45.0)
        if min(long_age, short_age) > fresh_cap:
            return False, "no_recent_leg"
        spread_val = float(detail.get("spread_current_value") or spread_mid or 0.0)
        if spread_val <= 0.0 or float(spread_liq or 0.0) <= 0.0:
            return False, "nonpositive_spread_val"
        ed = float(entry_debit or 0.0)
        if ed > 0.0:
            mmx = float(getattr(cfg, "premium_max_mult_from_entry", 12.0) or 12.0)
            mmn = float(getattr(cfg, "premium_min_mult_from_entry", 0.02) or 0.02)
            if str(position_type or "").lower() == OptionPositionType.DEBIT_SPREAD.value:
                if spread_val > ed * mmx or spread_val < ed * mmn:
                    return False, "spread_bounds_fail"
        if float(u_move_pct_eval or 0.0) < -0.0005:
            return False, "underlying_adverse"
        return True, "ok"

    def _emit_orb_options_exit_deferred_audit(
        self,
        *,
        position_id: str,
        position_symbol: str,
        position_type: str,
        candidate_exit_reason: str,
        blocked_reason: str,
        st: OptionStealthState,
        osnap: Dict[str, Any],
        detail: Dict[str, Any],
        prem_src: str,
        prem: float,
        relief_active: bool,
        relief_tier: str,
        relief_detail: str,
    ) -> None:
        dedupe_key = f"orb_def_audit|{position_id}|{candidate_exit_reason}"
        if not _option_telemetry_log_should_emit(self.config, _orb_exit_deferred_audit_last, dedupe_key):
            return
        defer_n = int(osnap.get("orb_exit_defer_total") or 0)
        lc = str(detail.get("leg_completeness") or osnap.get("leg_completeness") or "unknown")
        mtm_method = str(detail.get("spread_premium_source_quality") or prem_src or "unknown")
        qage = osnap.get("quote_age_seconds")
        syn_live = str(detail.get("price_source_telemetry") or osnap.get("option_price_source_telemetry") or "unknown")
        est_exec = float(detail.get("spread_liquidation_value") or detail.get("spread_mid_value") or prem or 0.0)
        why = (
            f"exit_grade_required:blocked={blocked_reason}"
            if not relief_active
            else f"relief_active:tier={relief_tier}:{relief_detail}"
        )
        log.warning(
            "ORB_OPTIONS_EXIT_DEFERRED_AUDIT | trade_id=%s | symbol=%s | position_type=%s | candidate_exit_reason=%s | "
            "mark_quality=%s | leg_quote_availability=%s | spread_mtm_method=%s | timeout_clock_frozen=%s | quote_age_sec=%s | "
            "synthetic_vs_live=%s | good_mark_tick_count=%d | defer_total=%d | why_exit_deferred=%s | estimated_executable_exit_value=%.6f",
            str(position_id or ""),
            str(position_symbol or ""),
            str(position_type or ""),
            str(candidate_exit_reason or ""),
            str(osnap.get("mark_quality") or "unknown"),
            lc,
            mtm_method,
            str(bool(osnap.get("timeout_clock_frozen"))).lower(),
            str(qage if qage is not None else "n/a"),
            syn_live,
            int(st.good_mark_tick_count),
            defer_n,
            why,
            float(est_exec),
        )

    def _delta_bucket(self, delta_at_entry: float) -> str:
        d = float(delta_at_entry or 0.0)
        if d >= self.config.delta_itm_threshold:
            return "ITM"
        if d >= self.config.delta_atm_threshold:
            return "ATM"
        if d > 0:
            return "OTM"
        return "UNKNOWN"

    def _resolve_entry_archetype(
        self,
        *,
        source_path: str,
        is_orb_0dte: bool,
        trendline_mode: str,
        impulse_mode: bool,
        is_trendline_0dte: bool,
        trendline_break_archetype: str = "",
    ) -> str:
        if is_orb_0dte:
            return "opening_impulse"
        sp = str(source_path or "").lower()
        tm = str(trendline_mode or "").upper()
        barn = str(trendline_break_archetype or "").strip().lower()
        if "orb" in sp:
            return "opening_impulse"
        if is_trendline_0dte:
            if barn in {"impulse_exhaustion", "exhaustion_reversal"}:
                return "trendline_impulse"
            if barn == "impulse_break":
                return "trendline_impulse"
            if barn == "weak_break_failure":
                return "trendline_weak_break_failure"
            if impulse_mode or tm == "IMPULSE":
                return "trendline_impulse"
            if tm == "RETEST":
                return "trendline_retest"
            if tm == "SLOW_TREND":
                return "trendline_slow_trend"
        return "generic"

    def _resolve_exit_profile(
        self,
        *,
        source_path: str,
        position_type: str,
        entry_archetype: str,
        is_orb_0dte: bool,
        is_trendline_0dte: bool,
        trendline_break_archetype: str = "",
    ) -> OptionExitProfile:
        cfg = self.config
        is_spread = position_type in (OptionPositionType.DEBIT_SPREAD.value, OptionPositionType.CREDIT_SPREAD.value)

        trail_trigger = float(cfg.trailing_trigger_pct)
        trail_dist = float(cfg.spread_base_trailing_pct if is_spread else cfg.base_trailing_pct)
        no_progress = float(cfg.spread_no_progress_exit_minutes if is_spread else cfg.no_progress_exit_minutes)
        time_exit = float(cfg.spread_time_exit_minutes if is_spread else cfg.time_exit_minutes)
        min_hold_seconds = 0.0
        allow_structure_exit = False
        max_pnl_drawdown_trigger_pct = float(cfg.max_pnl_drawdown_trigger_pct)
        max_pnl_drawdown_exit_pct = float(cfg.max_pnl_drawdown_exit_pct)

        is_0dte_single = (is_orb_0dte or is_trendline_0dte) and (not is_spread)
        max_pnl_drawdown_enabled = bool(cfg.max_pnl_drawdown_enabled)
        if is_0dte_single:
            min_hold_seconds = float(cfg.dte_min_hold_seconds)
            trail_trigger = float(cfg.dte_trailing_trigger_pct)
            trail_dist = float(cfg.base_trailing_pct)
            no_progress = float(cfg.dte_single_no_progress_minutes)
            time_exit = float(cfg.dte_time_exit_minutes)
            max_pnl_drawdown_trigger_pct = float(cfg.dte_single_mfe_trail_trigger_pct)
            max_pnl_drawdown_exit_pct = float(cfg.dte_single_mfe_trail_exit_pct)
            # Jul 29 max-profit: TP30 primary — disable single-leg mfe_giveback when flag off.
            if not bool(getattr(cfg, "dte_single_mfe_trail_enabled", False)):
                max_pnl_drawdown_enabled = False
        elif is_spread:
            max_pnl_drawdown_trigger_pct = float(cfg.max_pnl_drawdown_trigger_pct)
            max_pnl_drawdown_exit_pct = float(cfg.max_pnl_drawdown_exit_pct)

        return OptionExitProfile(
            source_path=str(source_path or "unknown"),
            position_type=str(position_type or "unknown"),
            entry_archetype=str(entry_archetype or "generic"),
            trail_trigger_pct=float(trail_trigger),
            trail_distance_pct=float(trail_dist),
            max_pnl_drawdown_trigger_pct=float(max_pnl_drawdown_trigger_pct),
            max_pnl_drawdown_exit_pct=float(max_pnl_drawdown_exit_pct),
            no_progress_minutes=float(no_progress),
            time_exit_minutes=float(time_exit),
            min_hold_seconds=float(min_hold_seconds),
            allow_underlying_structure_exit=bool(allow_structure_exit),
            max_pnl_drawdown_enabled=bool(max_pnl_drawdown_enabled),
        )

    def _0dte_winner_arm_pct(self) -> float:
        """Winner ARM is always ARM_TRAIL (+30%). All-day MFE trail that recut ARM to +10% was removed.
        """
        return float(getattr(self.config, "dte_single_arm_trail_pct", 0.30) or 0.30)

    def _0dte_mfe_trail_with_price_trail(self, *, is_0dte_single: bool, now: datetime) -> bool:
        """MFE trail (``mfe_giveback``) with ``price_trail`` only after 12:30 no-new-entries.

        All-day MFE trail (ENABLED=true) was removed — it recut ARM to +10% and hurt moons.
        Morning stays price_trail-only; after ``DISCOVERY_ORB2_TL_END_PT`` both latches live.
        """
        if not is_0dte_single:
            return False
        try:
            from modules.session_clock import past_orb2_tl_cutoff, tz_pt
        except Exception:
            try:
                from session_clock import past_orb2_tl_cutoff, tz_pt  # type: ignore
            except Exception:
                return False
        try:
            local = self._normalize_dt(now).astimezone(tz_pt())
            return bool(past_orb2_tl_cutoff(local.time()))
        except Exception:
            return False

    def _0dte_before_orb2_tl_cutoff(self, *, is_0dte_single: bool, now: datetime) -> bool:
        """True before the 12:30 PT no-new-entries cutoff used by 0DTE singles."""
        if not is_0dte_single:
            return False
        try:
            from modules.session_clock import past_orb2_tl_cutoff, tz_pt
        except Exception:
            try:
                from session_clock import past_orb2_tl_cutoff, tz_pt  # type: ignore
            except Exception:
                return False
        try:
            local = self._normalize_dt(now).astimezone(tz_pt())
            return not bool(past_orb2_tl_cutoff(local.time()))
        except Exception:
            return False

    def _fix_session_types(self) -> None:
        s = self._session
        s.setdefault("opens", 0)
        s.setdefault("breakeven_exits", 0)
        s.setdefault("trailing_exits", 0)
        s.setdefault("invalidation_exits", 0)
        s.setdefault("no_progress_exits", 0)
        s.setdefault("time_cap_exits", 0)
        s.setdefault("eod_exits", 0)
        s.setdefault("pnl_pcts", [])
        s.setdefault("max_excursions", [])
        s.setdefault("drawdowns_hwm", [])
        s.setdefault("min_to_breakeven", [])
        s.setdefault("min_to_trailing", [])
        s.setdefault(
            "exit_premium_sources",
            {"exact": 0, "nearest": 0, "delta_estimate": 0, "underlying_proxy": 0},
        )
        s.setdefault("degraded_quote_count", 0)
        s.setdefault("degraded_exit_count", 0)
        s.setdefault("skipped_entry_due_to_no_quote", 0)
        s.setdefault("live_quote_tick_hits", 0)
        s.setdefault("live_quote_tick_total", 0)

    def reset_session_exit_metrics(self) -> None:
        self._session = {
            "opens": 0,
            "breakeven_exits": 0,
            "trailing_exits": 0,
            "invalidation_exits": 0,
            "no_progress_exits": 0,
            "time_cap_exits": 0,
            "eod_exits": 0,
            "pnl_pcts": [],
            "max_excursions": [],
            "drawdowns_hwm": [],
            "min_to_breakeven": [],
            "min_to_trailing": [],
            "exit_premium_sources": {"exact": 0, "nearest": 0, "delta_estimate": 0, "underlying_proxy": 0},
            "degraded_quote_count": 0,
            "degraded_exit_count": 0,
            "skipped_entry_due_to_no_quote": 0,
            "live_quote_tick_hits": 0,
            "live_quote_tick_total": 0,
        }

    def register_on_open(
        self,
        position_id: str,
        *,
        entry_premium_per_contract: float,
        underlying_entry: float,
        option_side: str,
        line_geometry: str,
        strike: float,
        delta: float,
        trendline_dict: Dict[str, Any],
        metadata_target: Dict[str, Any],
        underlying_symbol: str = "",
        expiration_ymd: str = "",
        setup_type: Optional[str] = None,
        trigger_direction: Optional[str] = None,
        trendline_mode: Optional[str] = None,
        impulse_mode: Optional[bool] = None,
        early_entry_mode: Optional[bool] = None,
        position_type: Optional[str] = None,
        strategy_name: str = "",
        strategy_type: str = "",
        entry_value: Optional[float] = None,
        entry_debit: Optional[float] = None,
        entry_credit: Optional[float] = None,
        legs: Optional[List[Dict[str, Any]]] = None,
        source_path: str = "",
        break_archetype: Optional[str] = None,
        structure_invalidation_enabled: Optional[bool] = None,
    ) -> None:
        norm_position_type = _normalize_position_type(position_type, option_side)
        norm_legs = _normalize_legs(legs, option_side, strike, delta)
        arch_norm = str(break_archetype or "").strip().lower()
        spread_entry_value = float(
            entry_value
            if entry_value is not None
            else (entry_credit if norm_position_type == OptionPositionType.CREDIT_SPREAD.value else entry_debit)
            if (entry_debit is not None or entry_credit is not None)
            else entry_premium_per_contract
        )
        miss, reg_warn = validate_stealth_registration_inputs(
            norm_position_type=norm_position_type,
            underlying_symbol=underlying_symbol,
            expiration_ymd=expiration_ymd,
            norm_legs=norm_legs,
            spread_entry_value=spread_entry_value,
            entry_debit=entry_debit,
            entry_credit=entry_credit,
        )
        if miss or reg_warn:
            log.info(
                "OPTIONS_STEALTH | stage=runtime_validation_warning | trade_id=%s | missing=%s | warnings=%s",
                position_id,
                ",".join(miss) if miss else "none",
                ",".join(reg_warn) if reg_warn else "none",
            )
        st = OptionStealthState(
            entry_premium=entry_premium_per_contract,
            premium_hwm=entry_premium_per_contract,
            premium_lwm=entry_premium_per_contract,
            last_effective_premium=entry_premium_per_contract,
            option_side=option_side,
            line_geometry=str(line_geometry).lower(),
            delta_at_entry=delta,
            strike=strike,
            trendline=dict(trendline_dict or {}),
            last_hwm_at=datetime.now(timezone.utc),
            position_type=norm_position_type,
            strategy_name=str(strategy_name or ""),
            strategy_type=str(strategy_type or ""),
            entry_value=spread_entry_value,
            entry_debit=float(entry_debit) if entry_debit is not None else None,
            entry_credit=float(entry_credit) if entry_credit is not None else None,
            legs=norm_legs,
            leg_count=len(norm_legs),
            spread_hwm=spread_entry_value,
            spread_lwm=spread_entry_value,
            trendline_mode=str(trendline_mode or "STANDARD").upper(),
            impulse_mode=bool(impulse_mode),
            break_archetype=arch_norm,
            # early-entry is represented via trendline_mode tag.
        )
        delta_bucket = self._delta_bucket(delta)
        be_trigger_used = 0.0
        st.be_trigger_pct_used = be_trigger_used
        st.delta_bucket = delta_bucket
        expiry_block: Dict[str, Any] = {}
        try:
            from modules.calendar_expiry_metadata import (
                copy_expiry_fields,
                merge_expiry_trade_fields,
            )

            md_src = metadata_target if isinstance(metadata_target, dict) else {}
            norm_src = md_src.get("normalized_options") if isinstance(md_src.get("normalized_options"), dict) else {}
            exec_src = md_src.get("execution_payload") if isinstance(md_src.get("execution_payload"), dict) else {}
            copy_expiry_fields(norm_src, expiry_block)
            copy_expiry_fields(exec_src, expiry_block)
            copy_expiry_fields(md_src, expiry_block)
            merge_expiry_trade_fields(
                expiry_block,
                selected_expiry=expiry_block.get("selected_expiry_ymd")
                or expiry_block.get("expiry_ymd")
                or expiration_ymd,
                requested_expiry=expiry_block.get("requested_expiry_ymd"),
                session_date=expiry_block.get("session_date"),
                expiry_selection_mode=expiry_block.get("expiry_selection_mode"),
                fallback_reason=expiry_block.get("fallback_reason"),
                fallback_days=expiry_block.get("fallback_days"),
                prefer_existing=True,
            )
        except Exception:
            expiry_block = {
                "expiration_ymd": expiration_ymd or "",
                "is_0dte": False,
                "calendar_dte": None,
                "dte": None,
            }
        block = {
            "state": st,
            "entry_underlying": underlying_entry,
            "underlying_symbol": underlying_symbol or "",
            "expiration_ymd": expiration_ymd or "",
            "setup_type": setup_type or "",
            "trigger_direction": trigger_direction or "",
            "source_path": str(source_path or ""),
            "trendline_mode": str(trendline_mode or "STANDARD").upper(),
            "impulse_mode": bool(impulse_mode),
            "early_entry_mode": bool(early_entry_mode),
            "break_archetype": arch_norm,
            "structure_invalidation_enabled": (
                bool(structure_invalidation_enabled)
                if structure_invalidation_enabled is not None
                else None
            ),
        }
        for _ek in (
            "session_date",
            "expiry_ymd",
            "calendar_dte",
            "is_0dte",
            "expiry_selection_mode",
            "requested_expiry_ymd",
            "selected_expiry_ymd",
            "fallback_days",
            "fallback_reason",
            "dte",
        ):
            if _ek in expiry_block:
                block[_ek] = expiry_block.get(_ek)
        # Calendar truth only — never treat "has expiry string" as 0DTE.
        if "is_0dte" not in block:
            block["is_0dte"] = False
        metadata_target["option_stealth"] = {
            "engine": block,
            "position_type": norm_position_type,
            "leg_count": len(norm_legs),
            "legs": norm_legs,
            "strategy_name": strategy_name or "",
            "strategy_type": strategy_type or "",
            "underlying_symbol": underlying_symbol or "",
            "expiration_ymd": expiration_ymd or "",
            "entry_value": spread_entry_value,
            "entry_debit": float(entry_debit) if entry_debit is not None else None,
            "entry_credit": float(entry_credit) if entry_credit is not None else None,
            "entry_premium": entry_premium_per_contract,
            "entry_bid": (
                float(norm_legs[0].get("entry_bid") or norm_legs[0].get("bid") or 0.0)
                if norm_legs and (norm_legs[0].get("entry_bid") or norm_legs[0].get("bid"))
                else None
            ),
            "entry_ask": (
                float(norm_legs[0].get("entry_ask") or norm_legs[0].get("ask") or 0.0)
                if norm_legs and (norm_legs[0].get("entry_ask") or norm_legs[0].get("ask"))
                else None
            ),
            "entry_chain_source": str(
                metadata_target.get("entry_chain_source")
                or (metadata_target.get("execution_payload") or {}).get("chain_source")
                or ""
            ),
            "premium_hwm": entry_premium_per_contract,
            "premium_lwm": entry_premium_per_contract,
            "delta_at_entry": delta,
            "strike": strike,
            "breakeven_activated_at": None,
            "trailing_activated_at": None,
            "breakeven_active": False,
            "trailing_active": False,
            "exit_reason": None,
            "exit_premium": None,
            "current_effective_premium": entry_premium_per_contract,
            "last_premium_source": "entry",
            "delta_bucket": delta_bucket,
            "be_trigger_pct_used": be_trigger_used,
            "be_lock_pct_used": None,
            "be_activation_hwm": None,
            "effective_stop_level": None,
            "last_premium_update_ts": None,
            "last_underlying_update_ts": None,
            "stale_data_detected": False,
            "degraded_start_timestamp": None,
            "premium_jump_detected": False,
            "spread_current_value": spread_entry_value,
            "spread_entry_value": spread_entry_value,
            "spread_high_water_mark": spread_entry_value,
            "spread_low_water_mark": spread_entry_value,
            "spread_exit_reason": None,
            "spread_premium_source_quality": None,
            "leg_quote_sources": [],
            "source_path": str(source_path or ""),
            "trendline_mode": str(trendline_mode or "STANDARD").upper(),
            "impulse_mode": bool(impulse_mode),
            "early_entry_mode": bool(early_entry_mode),
            "break_archetype": arch_norm,
        }
        # Stamp entry-time chain dir% fields for Hold@0.55 (fail-closed if absent at exit).
        for _ck in (
            "direction_volume_pct",
            "call_chain_volume",
            "put_chain_volume",
            "put_call_oi_ratio",
            "valid_bid_ask_contracts",
        ):
            _cv = metadata_target.get(_ck)
            if _cv is None:
                _sig = metadata_target.get("signal_snapshot")
                if isinstance(_sig, Mapping):
                    _cv = _sig.get(_ck)
            if _cv is None:
                _ep = metadata_target.get("execution_payload")
                if isinstance(_ep, Mapping):
                    _cv = _ep.get(_ck)
            if _cv is not None:
                metadata_target["option_stealth"][_ck] = _cv
                if metadata_target.get(_ck) is None:
                    metadata_target[_ck] = _cv
        try:
            from modules.path_v2_exit import freeze_entry_distance_from_vwap_atr

            freeze_entry_distance_from_vwap_atr(
                metadata_target["option_stealth"],
                metadata_target,
                metadata_target.get("signal_snapshot"),
                metadata_target.get("execution_payload"),
                metadata_target.get("path_session_tech"),
                symbol=underlying_symbol or "",
            )
        except Exception:
            pass
        for _ek in (
            "session_date",
            "expiry_ymd",
            "calendar_dte",
            "is_0dte",
            "expiry_selection_mode",
            "requested_expiry_ymd",
            "selected_expiry_ymd",
            "fallback_days",
            "fallback_reason",
            "dte",
        ):
            if _ek in block:
                metadata_target["option_stealth"][_ek] = block.get(_ek)
                metadata_target[_ek] = block.get(_ek)
        self._session["opens"] = int(self._session.get("opens", 0)) + 1
        log.info(
            "OPTIONS_STEALTH | stage=register_on_open | trade_id=%s | position_type=%s | strategy_type=%s | "
            "strategy_name=%s | leg_count=%d | expiry=%s | calendar_dte=%s | is_0dte=%s | "
            "expiry_selection_mode=%s | source_path=%s",
            position_id,
            norm_position_type,
            strategy_type or "",
            strategy_name or "",
            len(norm_legs),
            expiration_ymd or "",
            block.get("calendar_dte"),
            block.get("is_0dte"),
            block.get("expiry_selection_mode") or "",
            str(source_path or "") or "unspecified",
        )
        log.info(
            "OPTION_LIFECYCLE_OPEN | trade_id=%s | symbol=%s | strategy_type=%s | position_type=%s | "
            "entry_premium=%.6f | entry_underlying=%.6f | source_path=%s | "
            "entry_distance_from_vwap_atr=%s | entry_atr=%s",
            position_id,
            underlying_symbol or "",
            str(strategy_type or ""),
            norm_position_type,
            float(entry_premium_per_contract or 0.0),
            float(underlying_entry or 0.0),
            str(source_path or "") or "unspecified",
            metadata_target["option_stealth"].get("entry_distance_from_vwap_atr"),
            metadata_target["option_stealth"].get("entry_atr"),
        )
        log.info(
            "OPTIONS_STEALTH | stage=position_type_detected | trade_id=%s | position_type=%s | leg_count=%d | strategy_type=%s",
            position_id,
            norm_position_type,
            len(norm_legs),
            strategy_type or "",
        )
        log.info(
            "TRENDLINE_PIPELINE | stage=option_stealth_init | trade_id=%s | strike=%.2f | delta=%.3f | premium=%.4f | side=%s",
            position_id,
            strike,
            delta,
            entry_premium_per_contract,
            option_side,
        )

    def _merge_live_snapshot(
        self,
        osnap: Dict[str, Any],
        st: OptionStealthState,
        prem: float,
        prem_src: str,
        detail: Dict[str, Any],
        current_underlying: float,
        pnl_pct: float,
        structure_ok: bool,
    ) -> None:
        osnap["current_effective_premium"] = prem
        osnap["premium_hwm"] = st.premium_hwm
        osnap["premium_lwm"] = st.premium_lwm
        # Never overwrite a good stamp with 0 from an unavailable und tick
        # (SOFI 2026-09-11: Exit $ alert showed $0.00 after premium-only eval).
        try:
            _u = float(current_underlying or 0.0)
        except (TypeError, ValueError):
            _u = 0.0
        if _u > 0.0:
            osnap["current_underlying_price"] = _u
        osnap["current_pnl_pct_per_contract"] = pnl_pct
        try:
            entry_for_liq = float(st.entry_premium or st.entry_value or 0.0)
            bid_for_liq = float(detail.get("option_bid") or prem or 0.0)
            if entry_for_liq > 0:
                osnap["current_pnl_pct_liquidation"] = (bid_for_liq - entry_for_liq) / entry_for_liq
                osnap["current_pnl_pct_mid"] = float(pnl_pct)
        except (TypeError, ValueError):
            pass
        osnap["last_premium_source"] = prem_src
        osnap["option_bid"] = detail.get("option_bid")
        osnap["option_ask"] = detail.get("option_ask")
        osnap["option_mark"] = detail.get("option_mark")
        osnap["option_last"] = detail.get("option_last")
        osnap["modeled_premium"] = detail.get("modeled_premium")
        osnap["effective_premium_used"] = detail.get("effective_premium_used")
        osnap["breakeven_active"] = st.breakeven_floor is not None
        osnap["breakeven_lock_premium"] = st.breakeven_floor
        osnap["trailing_active"] = st.trailing_activated_at is not None
        osnap["trailing_mode"] = st.mode.value
        osnap["trailing_stop_level"] = st.trail_stop_premium
        osnap["structure_still_valid"] = structure_ok
        osnap["delta_bucket"] = st.delta_bucket
        osnap["be_trigger_pct_used"] = st.be_trigger_pct_used
        osnap["be_lock_pct_used"] = st.be_lock_pct_used
        osnap["last_premium_update_ts"] = st.last_premium_update_at.isoformat() if st.last_premium_update_at else None
        osnap["last_underlying_update_ts"] = st.last_underlying_update_at.isoformat() if st.last_underlying_update_at else None
        osnap["stale_data_detected"] = st.stale_data_detected
        osnap["premium_jump_detected"] = st.premium_jump_detected
        effective_stop = st.trail_stop_premium if st.trail_stop_premium is not None else st.breakeven_floor
        osnap["effective_stop_level"] = effective_stop
        osnap["position_type"] = st.position_type
        osnap["leg_count"] = st.leg_count
        osnap["strategy_name"] = st.strategy_name
        osnap["strategy_type"] = st.strategy_type
        osnap["spread_entry_value"] = st.entry_value
        osnap["spread_high_water_mark"] = st.spread_hwm
        osnap["spread_low_water_mark"] = st.spread_lwm
        if st.position_type in (
            OptionPositionType.DEBIT_SPREAD.value,
            OptionPositionType.CREDIT_SPREAD.value,
        ):
            osnap["spread_premium_source_quality"] = detail.get("spread_premium_source_quality")
            osnap["spread_monitoring_mode"] = detail.get("spread_monitoring_mode")
            osnap["leg_quote_sources"] = detail.get("leg_quote_sources", [])

    def _apply_settlement_exit_premium(
        self,
        *,
        st: OptionStealthState,
        osnap: Dict[str, Any],
        exit_premium: float,
        prem_detail: Dict[str, Any],
    ) -> float:
        """True-to-live fill: executable bid (demo) / live mark. Protect floors are decisions, not fills."""
        if osnap.get("exit_premium_adjusted") and osnap.get("_settlement_exit_premium") is not None:
            return float(osnap.get("_settlement_exit_premium") or exit_premium or 0.0)
        if osnap.get("px2_be_clamp"):
            osnap["protect_floor_not_fill"] = "px2_be_clamp"
        elif osnap.get("pre_arm_profit_protect_clamp"):
            osnap["protect_floor_not_fill"] = "pre_arm_profit_protect"
        elif osnap.get("low_arm_profit_protect_clamp"):
            osnap["protect_floor_not_fill"] = "low_arm_profit_protect"
        elif osnap.get("early_be_protect_clamp"):
            osnap["protect_floor_not_fill"] = "early_be_protect"
        elif osnap.get("weak_loss_cap_clamp"):
            osnap["protect_floor_not_fill"] = "weak_loss_cap"
        elif osnap.get("nonstarter_stop_settle_at_stop"):
            osnap["protect_floor_not_fill"] = "nonstarter_stop"
        elif osnap.get("premium_stop_settle_at_stop"):
            osnap["protect_floor_not_fill"] = "premium_stop"
        entry_chain = str(osnap.get("entry_chain_source") or "")
        base_entry = float(st.entry_value or st.entry_premium or 0.0)
        hydrated = _hydrate_exit_prem_detail(
            prem_detail if isinstance(prem_detail, dict) else {},
            osnap=osnap if isinstance(osnap, dict) else None,
            exit_premium=float(exit_premium or 0.0),
        )
        exit_prem_adj, adjusted, adj_reason = finalize_tline_true_to_live_exit_premium(
            strategy_type=str(st.strategy_type or ""),
            option_side=str(st.option_side or ""),
            entry_chain_source=entry_chain,
            entry_premium=base_entry,
            exit_premium=float(exit_premium or 0.0),
            prem_detail=hydrated,
            mark_is_exit_grade=bool(osnap.get("mark_is_exit_grade")),
            premium_jump_detected=bool(st.premium_jump_detected or osnap.get("premium_jump_detected")),
            use_bid_for_long_exit=bool(getattr(self.config, "dte_use_bid_for_long_exit", True)),
            max_spread_pct=float(getattr(self.config, "tline_exit_grade_max_spread_pct", 0.30) or 0.30),
            true_to_live_enabled=bool(getattr(self.config, "dte_true_to_live_executable_marks", True)),
        )
        if adjusted:
            osnap["exit_premium_adjusted"] = True
            osnap["exit_premium_adjustment_reason"] = adj_reason
            osnap["exit_premium_raw_live"] = float(exit_premium or 0.0)
        osnap["_settlement_exit_premium"] = float(exit_prem_adj or 0.0)
        return float(exit_prem_adj or 0.0)

    def _finalize_exit_return(
        self,
        prem: float,
        reason: str,
        telemetry: Dict[str, Any],
        *,
        st: OptionStealthState,
        osnap: Dict[str, Any],
        detail: Dict[str, Any],
    ) -> Optional[Tuple[float, str, Dict[str, Any]]]:
        settled = self._apply_settlement_exit_premium(
            st=st,
            osnap=osnap,
            exit_premium=float(prem or 0.0),
            prem_detail=detail if isinstance(detail, dict) else {},
        )
        return settled, reason, telemetry

    def _build_exit_diagnostics(
        self,
        *,
        position_id: str,
        position_symbol: str,
        block: Dict[str, Any],
        st: OptionStealthState,
        osnap: Dict[str, Any],
        exit_premium: float,
        exit_reason: str,
        now: datetime,
        prem_detail: Dict[str, Any],
        premium_source_at_exit: str,
    ) -> Dict[str, Any]:
        exit_premium = self._apply_settlement_exit_premium(
            st=st,
            osnap=osnap,
            exit_premium=float(exit_premium or 0.0),
            prem_detail=prem_detail if isinstance(prem_detail, dict) else {},
        )
        entry_ts = self._normalize_dt(st.opened_at)
        entry_prem = float(st.entry_value or st.entry_premium or 0.0)
        if st.position_type == OptionPositionType.CREDIT_SPREAD.value:
            pnl_pct = (entry_prem - exit_premium) / entry_prem if entry_prem > 0 else 0.0
        else:
            pnl_pct = (exit_premium - entry_prem) / entry_prem if entry_prem > 0 else 0.0
        contracts = 1
        realized_pnl = (exit_premium - entry_prem) * 100.0 * contracts
        held_min = (now - entry_ts).total_seconds() / 60.0
        hwm = st.premium_hwm
        lwm = st.premium_lwm
        max_exc = (hwm - entry_prem) / entry_prem if entry_prem > 0 else 0.0
        dd_hwm = (hwm - exit_premium) / hwm if hwm > 0 else 0.0
        be_min: Optional[float] = None
        if st.breakeven_activated_at:
            be_min = (self._normalize_dt(st.breakeven_activated_at) - entry_ts).total_seconds() / 60.0
        tr_min: Optional[float] = None
        if st.trailing_activated_at:
            tr_min = (self._normalize_dt(st.trailing_activated_at) - entry_ts).total_seconds() / 60.0

        summary = {
            "symbol": position_symbol,
            "underlying_symbol": block.get("underlying_symbol") or position_symbol,
            "trade_id": position_id,
            "option_side": st.option_side,
            "strike": st.strike,
            "expiration_ymd": block.get("expiration_ymd") or "",
            "is_0dte": block.get("is_0dte"),
            "session_date": block.get("session_date"),
            "expiry_ymd": block.get("expiry_ymd") or block.get("selected_expiry_ymd"),
            "calendar_dte": block.get("calendar_dte"),
            "expiry_selection_mode": block.get("expiry_selection_mode"),
            "requested_expiry_ymd": block.get("requested_expiry_ymd"),
            "selected_expiry_ymd": block.get("selected_expiry_ymd"),
            "fallback_days": block.get("fallback_days"),
            "fallback_reason": block.get("fallback_reason"),
            "dte": block.get("dte") if block.get("dte") is not None else block.get("calendar_dte"),
            "setup_type": block.get("setup_type") or "",
            "trigger_direction": block.get("trigger_direction") or "",
            "source_path": block.get("source_path") or "",
            "entry_archetype": block.get("entry_archetype") or "generic",
            "entry_timestamp": entry_ts.isoformat(),
            "entry_premium": entry_prem,
            "premium_high_water_mark": hwm,
            "premium_low_water_mark": lwm,
            "effective_exit_premium": exit_premium,
            "exit_price": exit_premium,
            "premium_source_at_exit": premium_source_at_exit,
            "exit_source": premium_source_at_exit,
            "option_bid_at_exit": prem_detail.get("option_bid"),
            "option_ask_at_exit": prem_detail.get("option_ask"),
            "option_mark_at_exit": prem_detail.get("option_mark"),
            "option_last_at_exit": prem_detail.get("option_last"),
            "modeled_premium_at_exit": prem_detail.get("modeled_premium"),
            "realized_pnl_per_contract_dollars": realized_pnl,
            "realized_pnl_pct": pnl_pct,
            "pnl_pct": pnl_pct,
            # Prefer stealth liquidatable MFE (bid path) when present on osnap.
            "max_pnl_pct": float(
                osnap.get("max_pnl_pct")
                if isinstance(osnap.get("max_pnl_pct"), (int, float))
                else max_exc
            ),
            "exit_reason": exit_reason,
            "canonical_exit_reason": normalize_exit_reason(exit_reason),
            "exit_timestamp": now.isoformat(),
            "breakeven_activated": st.breakeven_floor is not None,
            "breakeven_activation_time": st.breakeven_activated_at.isoformat() if st.breakeven_activated_at else None,
            "breakeven_lock_premium": st.breakeven_floor,
            "trailing_activated": st.trailing_activated_at is not None,
            "trailing_activation_time": st.trailing_activated_at.isoformat() if st.trailing_activated_at else None,
            "trailing_mode": st.mode.value,
            "trailing_stop_level": st.trail_stop_premium,
            "no_progress_timeout_triggered": st.no_progress_timeout_triggered,
            "time_in_trade_minutes": held_min,
            "max_premium_excursion_pct": max_exc,
            "drawdown_from_premium_hwm_pct": dd_hwm,
            "minutes_to_breakeven": be_min,
            "minutes_to_trailing_activation": tr_min,
            "delta_bucket": st.delta_bucket,
            "be_trigger_pct_used": st.be_trigger_pct_used,
            "be_lock_pct_used": st.be_lock_pct_used,
            "stale_data_detected": st.stale_data_detected,
            "premium_jump_detected": st.premium_jump_detected,
            "position_type": st.position_type,
            "leg_count": st.leg_count,
            "strategy_name": st.strategy_name,
            "strategy_type": st.strategy_type,
            "trendline_mode": st.trendline_mode,
            "entry_mode": (
                "impulse"
                if bool(st.impulse_mode) or str(st.trendline_mode or "").upper() == "IMPULSE"
                else "slow_trend"
                if str(st.trendline_mode or "").upper() == "SLOW_TREND"
                else "early_entry"
                if str(st.trendline_mode or "").upper() == "EARLY_ENTRY"
                else "standard"
            ),
            "spread_entry_value": st.entry_value,
            "spread_current_value": exit_premium,
            "spread_high_water_mark": st.spread_hwm,
            "spread_low_water_mark": st.spread_lwm,
            "spread_exit_reason": exit_reason,
            "spread_premium_source_quality": prem_detail.get("spread_premium_source_quality"),
            "spread_monitoring_mode": prem_detail.get("spread_monitoring_mode"),
            "leg_quote_sources": prem_detail.get("leg_quote_sources", []),
            "data_quality": {
                "quote_age_seconds": prem_detail.get("quote_age_seconds"),
                "spread_pct": prem_detail.get("spread_pct"),
                "fallback_used": prem_detail.get("fallback_used"),
            },
            "degraded_data_source": osnap.get("degraded_data_source"),
            "live_degraded_reason": osnap.get("last_live_degraded_reason"),
            "mark_quality_at_exit": prem_detail.get("mark_quality") or osnap.get("mark_quality"),
            "mark_is_exit_grade_at_exit": osnap.get("mark_is_exit_grade"),
            "had_underlying_opportunity_at_exit": bool(getattr(st, "had_underlying_opportunity", False)),
            "max_underlying_favorable_move_pct_at_exit": float(getattr(st, "max_underlying_favorable_move_pct", 0.0) or 0.0),
            "current_pnl_pct_liquidation": (
                float(prem_detail["current_pnl_pct_liquidation"])
                if isinstance(prem_detail.get("current_pnl_pct_liquidation"), (int, float))
                else (
                    float(
                        (
                            float(
                                prem_detail.get("option_bid")
                                if prem_detail.get("option_bid") is not None
                                else exit_premium
                            )
                            - entry_prem
                        )
                        / entry_prem
                    )
                    if entry_prem > 0
                    else float(pnl_pct)
                )
            ),
            "current_pnl_pct_mid": (
                float(prem_detail["current_pnl_pct_mid"])
                if isinstance(prem_detail.get("current_pnl_pct_mid"), (int, float))
                else float(pnl_pct)
            ),
            "armed_at": osnap.get("mfe_armed_at") or osnap.get("had_opportunity_armed_at"),
            "hwm_timestamp": osnap.get("hwm_timestamp"),
            "hwm_bid": float(hwm) if hwm else None,
            "hwm_pnl_pct": float(
                osnap.get("max_pnl_pct")
                if isinstance(osnap.get("max_pnl_pct"), (int, float))
                else max_exc
            ),
            "giveback_floor_bid": (
                float(entry_prem) * (1.0 + float(osnap.get("max_pnl_pct") or max_exc) - 0.10)
                if entry_prem > 0
                else None
            ),
            "exit_decision_bid": prem_detail.get("option_bid"),
            "gap_guard_state": (
                "pending"
                if osnap.get("mfe_giveback_gap_pending")
                else (
                    "defer_timeout_forced"
                    if osnap.get("mfe_giveback_defer_timeout_forced")
                    else (
                        "confirmed_or_cleared"
                        if osnap.get("mfe_giveback_gap_confirm_count")
                        else "inactive"
                    )
                )
            ),
            "gap_guard_confirm_count": osnap.get("mfe_giveback_gap_confirm_count"),
            "quote_continuity_status": (
                "gap_open"
                if block.get("awaiting_market_data")
                else ("exact_present" if prem_detail.get("option_bid") is not None else "unknown")
            ),
            "peak_pct": float(
                osnap.get("max_pnl_pct")
                if isinstance(osnap.get("max_pnl_pct"), (int, float))
                else max_exc
            ),
            "peak_price": float(hwm) if hwm else None,
        }
        return summary

    def _record_exit(self, reason: str, pnl_pct: float, src: str, diag: Dict[str, Any]) -> None:
        self._fix_session_types()
        s = self._session
        log.info(
            "OPTION_LIFECYCLE_EXIT | trade_id=%s | symbol=%s | strategy_type=%s | reason=%s | "
            "pnl_pct=%.4f | exit_premium=%.6f | entry_premium=%.6f | premium_source=%s",
            str(diag.get("trade_id") or ""),
            str(diag.get("symbol") or ""),
            str(diag.get("strategy_type") or ""),
            reason,
            float(pnl_pct),
            float(diag.get("exit_premium_per_contract") or 0.0),
            float(diag.get("entry_premium_per_contract") or 0.0),
            src,
        )
        max_pnl_pct = float(diag.get("max_pnl_pct") or diag.get("max_premium_excursion_pct") or 0.0)
        exit_pnl_pct = float(diag.get("realized_pnl_pct") or diag.get("pnl_pct") or pnl_pct)
        profit_leak_pct = max(0.0, max_pnl_pct - exit_pnl_pct)
        retained_mfe_ratio = 0.0
        if max_pnl_pct > 1e-9:
            retained_mfe_ratio = max(0.0, min(1.0, exit_pnl_pct / max_pnl_pct))
        log.info(
            "OPTIONS_STEALTH_MFE_RETENTION | trade_id=%s | symbol=%s | source_path=%s | entry_archetype=%s | position_type=%s | "
            "max_pnl_pct=%.4f | exit_pnl_pct=%.4f | profit_leak_pct=%.4f | retained_mfe_ratio=%.4f | exit_reason=%s | premium_source_at_exit=%s",
            str(diag.get("trade_id") or ""),
            str(diag.get("symbol") or ""),
            str(diag.get("source_path") or "unknown"),
            str(diag.get("entry_archetype") or "generic"),
            str(diag.get("position_type") or ""),
            max_pnl_pct,
            exit_pnl_pct,
            profit_leak_pct,
            retained_mfe_ratio,
            reason,
            str(diag.get("premium_source_at_exit") or src),
        )
        stype = str(diag.get("strategy_type") or "").lower()
        sname = str(diag.get("strategy_name") or "").lower()
        if stype == "trendline_0dte" or sname == "easytrendline_0dte":
            log.info(
                "TRENDLINE_TRADE_OUTCOME | trade_id=%s | symbol=%s | pnl_pct=%.6f | max_pnl_pct=%.6f | exit_reason=%s | time_held_minutes=%.4f",
                str(diag.get("trade_id") or ""),
                str(diag.get("symbol") or ""),
                float(diag.get("realized_pnl_pct") or diag.get("pnl_pct") or pnl_pct),
                float(diag.get("max_premium_excursion_pct") or diag.get("max_pnl_pct") or 0.0),
                reason,
                float(diag.get("time_in_trade_minutes") or 0.0),
            )
            ddm = float(diag.get("drawdown_from_premium_hwm_pct") or max(0.0, max_pnl_pct - exit_pnl_pct))
            held_sec = float(diag.get("time_in_trade_minutes") or 0.0) * 60.0
            log.info(
                "OPTION_EXIT_DECISION_AUDIT | trade_id=%s | symbol=%s | grep=TRENDLINE_EXIT_POSTMORTEM | final_reason=%s | "
                "pnl_pct=%.6f | max_pnl_pct=%.6f | drawdown_from_mfe=%.6f | held_sec=%.1f | mark_quality=%s | mark_is_exit_grade=%s | "
                "had_underlying_opportunity=%s | max_underlying_favorable_pct=%.5f | exit_allowed=true",
                str(diag.get("trade_id") or ""),
                str(diag.get("symbol") or ""),
                reason,
                float(diag.get("realized_pnl_pct") or diag.get("pnl_pct") or pnl_pct),
                max_pnl_pct,
                ddm,
                held_sec,
                str(diag.get("mark_quality_at_exit") or ""),
                str(diag.get("mark_is_exit_grade_at_exit")).lower()
                if diag.get("mark_is_exit_grade_at_exit") is not None
                else "unknown",
                str(bool(diag.get("had_underlying_opportunity_at_exit"))).lower(),
                float(diag.get("max_underlying_favorable_move_pct_at_exit") or 0.0),
            )
        log_option_exit_diagnosis(
            stage="stealth_decision",
            strategy=str(diag.get("strategy_type") or diag.get("strategy_name") or "unknown"),
            trade_id=str(diag.get("trade_id") or ""),
            symbol=str(diag.get("symbol") or ""),
            reason=str(reason or ""),
            pnl_pct=float(diag.get("realized_pnl_pct") or diag.get("pnl_pct") or pnl_pct),
            exit_premium=float(diag.get("exit_premium_per_contract") or 0.0),
            extra={
                "premium_source": str(diag.get("premium_source_at_exit") or src),
                "canonical_reason": str(diag.get("canonical_exit_reason") or ""),
            },
        )
        norm_reason = normalize_exit_reason(reason)
        if norm_reason == XER.BREAKEVEN.value:
            s["breakeven_exits"] = int(s["breakeven_exits"]) + 1
        elif norm_reason == XER.SCRATCH.value:
            s["scratch_exits"] = int(s.get("scratch_exits", 0)) + 1
        elif norm_reason == XER.MFE_TRAILING_STOP.value:
            s["trailing_exits"] = int(s["trailing_exits"]) + 1
        elif norm_reason == XER.NO_PROGRESS.value:
            s["no_progress_exits"] = int(s["no_progress_exits"]) + 1
        elif norm_reason == XER.TIME_EXIT.value:
            s["time_cap_exits"] = int(s["time_cap_exits"]) + 1
        elif norm_reason in (XER.END_OF_DAY_EXIT.value, "eod_no_state"):
            s["eod_exits"] = int(s["eod_exits"]) + 1
        elif norm_reason == XER.MFE_GIVEBACK.value:
            s["mfe_giveback_exits"] = int(s.get("mfe_giveback_exits", 0)) + 1
        elif norm_reason == XER.MFE_TAKE_PROFIT.value:
            s["take_profit_exits"] = int(s.get("take_profit_exits", 0)) + 1
        elif norm_reason == XER.PRICE_STOP.value:
            s["asset_stop_exits"] = int(s.get("asset_stop_exits", 0)) + 1
        elif norm_reason == XER.PREMIUM_STOP.value:
            s["premium_stop_exits"] = int(s.get("premium_stop_exits", 0)) + 1
        elif norm_reason == XER.PRICE_TRAIL.value:
            s["price_trail_exits"] = int(s.get("price_trail_exits", 0)) + 1
        elif norm_reason == XER.TAKE_PROFIT.value:
            s["take_profit_exits"] = int(s.get("take_profit_exits", 0)) + 1
        elif norm_reason == XER.UL_TRAIL.value:
            s["ul_trail_exits"] = int(s.get("ul_trail_exits", 0)) + 1
        elif norm_reason == XER.UL_BE.value:
            s["ul_be_exits"] = int(s.get("ul_be_exits", 0)) + 1
        elif norm_reason == XER.CATASTROPHIC_PREMIUM_STOP.value:
            s["catastrophic_premium_stop_exits"] = int(s.get("catastrophic_premium_stop_exits", 0)) + 1
        elif norm_reason == XER.IMMATURE_STRESS.value:
            s["immature_stress_exits"] = int(s.get("immature_stress_exits", 0)) + 1
        elif norm_reason == XER.HWM_RTC.value:
            s["hwm_rtc_exits"] = int(s.get("hwm_rtc_exits", 0)) + 1
        elif norm_reason == XER.GB30_GIVEBACK.value:
            s["gb30_giveback_exits"] = int(s.get("gb30_giveback_exits", 0)) + 1
        elif norm_reason == XER.GB32_TIMED_GIVEBACK.value:
            s["gb32_timed_giveback_exits"] = int(s.get("gb32_timed_giveback_exits", 0)) + 1
        elif norm_reason == XER.DEEP_GREEN_STOP.value:
            s["deep_green_stop_exits"] = int(s.get("deep_green_stop_exits", 0)) + 1
        elif norm_reason == XER.SHALLOW_PROOF_STOP.value:  # Oct 6 SHIP: counter was missing
            s["shallow_proof_stop_exits"] = int(s.get("shallow_proof_stop_exits", 0)) + 1
        elif norm_reason == XER.SHALLOW_TIME_STOP.value:  # Oct 6 SHIP: counter was missing
            s["shallow_time_stop_exits"] = int(s.get("shallow_time_stop_exits", 0)) + 1
        elif norm_reason == XER.SHALLOW_FLOOR_STOP.value:  # Oct 6 V3A SF
            s["shallow_floor_stop_exits"] = int(s.get("shallow_floor_stop_exits", 0)) + 1
        elif norm_reason == XER.STALL_FLOOR_STOP.value:  # Oct 6 V3A SBE v2-A
            s["stall_floor_stop_exits"] = int(s.get("stall_floor_stop_exits", 0)) + 1
        elif norm_reason == XER.ABSORPTION_EXIT.value:
            s["absorption_exit_exits"] = int(s.get("absorption_exit_exits", 0)) + 1
        elif norm_reason == XER.SHALLOW_GREEN_EXIT.value:
            s["shallow_green_exit_exits"] = int(s.get("shallow_green_exit_exits", 0)) + 1
        elif norm_reason == XER.CONTRACT_FADE_EXIT.value:
            s["contract_fade_exit_exits"] = int(s.get("contract_fade_exit_exits", 0)) + 1
        elif norm_reason == XER.PREMIUM_TRAIL_GT50_25TRAIL.value:
            s["premium_trail_gt50_25trail_exits"] = int(s.get("premium_trail_gt50_25trail_exits", 0)) + 1
        elif norm_reason == XER.PREMIUM_TRAIL_GT30_10TRAIL.value:
            s["premium_trail_gt30_10trail_exits"] = int(s.get("premium_trail_gt30_10trail_exits", 0)) + 1
        elif norm_reason == XER.PREMIUM_SPIKE_FAIL.value:
            s["premium_spike_fail_exits"] = int(s.get("premium_spike_fail_exits", 0)) + 1
        elif norm_reason == XER.UNDERLYING_MFE_CAPTURE.value:
            s["underlying_mfe_capture_exits"] = int(s.get("underlying_mfe_capture_exits", 0)) + 1
        elif norm_reason == XER.GUARDED_RETRACE_EXIT.value:
            s["guarded_retrace_exits"] = int(s.get("guarded_retrace_exits", 0)) + 1
        elif norm_reason == XER.UL_GIVEBACK_RATIO_EXIT.value:
            s["ul_giveback_ratio_exits"] = int(s.get("ul_giveback_ratio_exits", 0)) + 1
        elif norm_reason == XER.ATR_LOSS_CUT.value:  # Oct 27 STEP 29
            s["atr_loss_cut_exits"] = int(s.get("atr_loss_cut_exits", 0)) + 1
        elif norm_reason == XER.VWAP_CROSS_EXIT.value:  # Oct 27 STEP 30
            s["vwap_cross_exits"] = int(s.get("vwap_cross_exits", 0)) + 1
        elif norm_reason == XER.BOUNDED_MFE_CAPTURE.value:
            s["bounded_mfe_capture_exits"] = int(s.get("bounded_mfe_capture_exits", 0)) + 1
        elif norm_reason == XER.TWENTY_PERCENT_SAVE.value:
            s["twenty_percent_save_exits"] = int(s.get("twenty_percent_save_exits", 0)) + 1
        elif norm_reason == XER.PREMIUM_STALE_FAIL.value:
            s["premium_stale_fail_exits"] = int(s.get("premium_stale_fail_exits", 0)) + 1
        elif norm_reason == XER.RUNNER_TIMED_CAPTURE.value:
            s["runner_timed_capture_exits"] = int(s.get("runner_timed_capture_exits", 0)) + 1
        elif norm_reason == XER.RUNNER_PROFIT_FLOOR.value:
            s["runner_profit_floor_exits"] = int(s.get("runner_profit_floor_exits", 0)) + 1
        elif norm_reason == XER.RUNNER_EXTENDED_CAPTURE.value:  # Oct 6 R2X
            s["runner_extended_capture_exits"] = int(s.get("runner_extended_capture_exits", 0)) + 1
        elif norm_reason == XER.RUNNER_EXTENSION_FLOOR.value:  # Oct 6 R2X
            s["runner_extension_floor_exits"] = int(s.get("runner_extension_floor_exits", 0)) + 1
        elif norm_reason == XER.THETA_EXIT.value:
            s["theta_exit_exits"] = int(s.get("theta_exit_exits", 0)) + 1
        s["pnl_pcts"].append(float(pnl_pct))
        s["max_excursions"].append(float(diag.get("max_premium_excursion_pct") or 0.0))
        s["drawdowns_hwm"].append(float(diag.get("drawdown_from_premium_hwm_pct") or 0.0))
        mb = diag.get("minutes_to_breakeven")
        if mb is not None:
            s["min_to_breakeven"].append(float(mb))
        mt = diag.get("minutes_to_trailing_activation")
        if mt is not None:
            s["min_to_trailing"].append(float(mt))
        ps = s["exit_premium_sources"]
        if isinstance(ps, dict):
            ps[src] = int(ps.get(src, 0)) + 1
        if self.config.require_live_quotes and norm_reason in (
            XER.DEGRADED_DATA.value,
            XER.NO_DATA.value,
        ):
            dds = str(diag.get("degraded_data_source") or "")
            pse = str(diag.get("premium_source_at_exit") or src)
            if (
                dds == "live_quote_unavailable"
                or pse == "live_quote_unavailable"
                or norm_reason == XER.NO_DATA.value
            ):
                s["degraded_exit_count"] = int(s.get("degraded_exit_count", 0)) + 1

    def bump_skipped_entry_no_quote(self, symbol: str, trade_id: str = "-") -> None:
        """Entry blocked due to missing live option chain/quotes (Trendline 0DTE path)."""
        if not self.config.require_live_quotes:
            return
        self._fix_session_types()
        self._session["skipped_entry_due_to_no_quote"] = int(self._session.get("skipped_entry_due_to_no_quote", 0)) + 1
        _emit_option_degraded_live_quote_log(
            self.config,
            symbol=str(symbol or "-"),
            trade_id=str(trade_id or "-"),
            reason="no_quote",
            action_taken="skip_entry",
            dedupe_key=f"skip|{symbol}|{trade_id}",
        )

    def log_options_exit_summary(self) -> None:
        self._fix_session_types()
        s = self._session
        n = len(s["pnl_pcts"])
        avg_pnl = sum(s["pnl_pcts"]) / n if n else 0.0
        avg_exc = sum(s["max_excursions"]) / len(s["max_excursions"]) if s["max_excursions"] else 0.0
        avg_dd = sum(s["drawdowns_hwm"]) / len(s["drawdowns_hwm"]) if s["drawdowns_hwm"] else 0.0
        avg_be = sum(s["min_to_breakeven"]) / len(s["min_to_breakeven"]) if s["min_to_breakeven"] else 0.0
        avg_tr = sum(s["min_to_trailing"]) / len(s["min_to_trailing"]) if s["min_to_trailing"] else 0.0
        ps = s.get("exit_premium_sources", {})
        ps_s = ",".join(f"{k}:{v}" for k, v in sorted(ps.items())) if isinstance(ps, dict) else ""
        lq_total = int(s.get("live_quote_tick_total", 0) or 0)
        lq_hits = int(s.get("live_quote_tick_hits", 0) or 0)
        if bool(self.config.require_live_quotes) and lq_total > 0:
            live_quote_availability_pct = 100.0 * float(lq_hits) / float(lq_total)
        elif bool(self.config.require_live_quotes):
            live_quote_availability_pct = 0.0
        else:
            live_quote_availability_pct = -1.0
        log.info(
            "TRENDLINE_PIPELINE | stage=options_exit_summary | executed=%s | breakeven_exits=%s | trailing_exits=%s | "
            "invalidation_exits=%s | no_progress_exits=%s | time_cap_exits=%s | eod_exits=%s | avg_pnl_pct=%.6f | "
            "avg_max_premium_excursion_pct=%.6f | avg_drawdown_from_hwm_pct=%.6f | avg_min_to_breakeven=%.4f | "
            "avg_min_to_trailing=%.4f | premium_sources=%s | live_quote_availability_pct=%s | "
            "live_quote_ticks=%d/%d | degraded_quote_count=%d | degraded_exit_count=%d | skipped_entry_due_to_no_quote=%d | "
            "OPTION_REQUIRE_LIVE_QUOTES=%s",
            s.get("opens", 0),
            s.get("breakeven_exits", 0),
            s.get("trailing_exits", 0),
            s.get("invalidation_exits", 0),
            s.get("no_progress_exits", 0),
            s.get("time_cap_exits", 0),
            s.get("eod_exits", 0),
            avg_pnl,
            avg_exc,
            avg_dd,
            avg_be,
            avg_tr,
            ps_s,
            (f"{live_quote_availability_pct:.1f}" if live_quote_availability_pct >= 0.0 else "n/a"),
            lq_hits,
            lq_total,
            int(s.get("degraded_quote_count", 0) or 0),
            int(s.get("degraded_exit_count", 0) or 0),
            int(s.get("skipped_entry_due_to_no_quote", 0) or 0),
            str(bool(self.config.require_live_quotes)).lower(),
        )

    def evaluate(
        self,
        *,
        position: Any,
        premium: Any,
        policy: Any = None,
        now: Optional[datetime] = None,
        current_underlying: Optional[float] = None,
    ) -> Any:
        """Pure exit API: consume PremiumSnapshot, return frozen ExitDecision.

        Does not fetch chains/quotes, ratchet HWM/LWM, classify premium buckets,
        start degraded timers, update registry membership, or submit closes.
        Threshold rules are applied unchanged via process_position with
        premium=snapshot and mutate_premium_state=False.
        """
        from modules.position_registry_contract import (
            EXIT_ACTION_HOLD,
            ExitDecision,
            exit_action_from_reason,
        )

        now_dt = self._normalize_dt(now or datetime.now(timezone.utc))
        if position is None or premium is None:
            return ExitDecision(
                action=EXIT_ACTION_HOLD,
                reason="missing_position_or_premium",
                diagnostics={"evaluate": "missing_inputs"},
            )

        if isinstance(position, dict):
            position_id = str(position.get("position_id") or "")
            position_symbol = str(position.get("underlying") or position.get("symbol") or "")
            position_metadata = (
                position.get("metadata") if isinstance(position.get("metadata"), dict) else {}
            )
        else:
            position_id = str(getattr(position, "position_id", "") or "")
            position_symbol = str(getattr(position, "symbol", "") or "")
            position_metadata = getattr(position, "metadata", None)
            if not isinstance(position_metadata, dict):
                position_metadata = {}

        osnap = position_metadata.get("option_stealth")
        if not isinstance(osnap, dict):
            osnap = {}
            position_metadata["option_stealth"] = osnap

        # Prefer live monitor spot (threaded from _0dte_run_stealth_exit_decision).
        # Fall back to osnap / premium / entry — never stamp exit==entry when spot is known.
        live_u = 0.0
        try:
            if current_underlying is not None and float(current_underlying) > 0:
                live_u = float(current_underlying)
        except (TypeError, ValueError):
            live_u = 0.0
        if live_u <= 0:
            try:
                live_u = float(osnap.get("current_underlying_price") or 0.0)
            except (TypeError, ValueError):
                live_u = 0.0
        if live_u <= 0 and isinstance(position, dict):
            try:
                live_u = float(position.get("current_underlying") or 0.0)
            except (TypeError, ValueError):
                live_u = 0.0
        if live_u <= 0:
            try:
                live_u = float(getattr(premium, "underlying_price", None) or 0.0)
            except (TypeError, ValueError):
                live_u = 0.0
        if live_u <= 0:
            try:
                from modules.open_position_recovery import _entry_underlying_from_position

                if isinstance(position, dict):
                    live_u = float(
                        position.get("current_underlying")
                        or (
                            position_metadata.get("entry_underlying_price")
                            if isinstance(position_metadata, dict)
                            else 0.0
                        )
                        or 0.0
                    )
                else:
                    live_u = float(_entry_underlying_from_position(position) or 0.0)
            except Exception:
                live_u = 0.0

        if live_u > 0:
            osnap["current_underlying_price"] = float(live_u)

        if getattr(premium, "premium_hwm", None) is not None:
            osnap["premium_hwm"] = premium.premium_hwm
        if getattr(premium, "premium_lwm", None) is not None:
            osnap["premium_lwm"] = premium.premium_lwm
        if getattr(premium, "executable_exit_price", None) is not None:
            osnap["last_effective_premium"] = premium.executable_exit_price
        if getattr(premium, "mfe_pct", None) is not None:
            try:
                prev_mfe = float(osnap.get("max_pnl_pct") or 0.0)
                osnap["max_pnl_pct"] = max(prev_mfe, float(premium.mfe_pct))
                from modules.po_mfe20_snapshot import mark_po_20p_due
                mark_po_20p_due(osnap, prev_mfe, osnap.get("max_pnl_pct"))
            except (TypeError, ValueError):
                osnap["max_pnl_pct"] = premium.mfe_pct
                from modules.po_mfe20_snapshot import mark_po_20p_due

                mark_po_20p_due(osnap, None, osnap.get("max_pnl_pct"))

        result = self.process_position(
            position_id,
            position_symbol=position_symbol,
            position_metadata=position_metadata,
            current_underlying=float(live_u or 0.0),
            now=now_dt,
            option_quote=None,
            premium=premium,
            mutate_premium_state=False,
        )

        diagnostics: Dict[str, Any] = {
            "policy": str(policy) if policy is not None else None,
            "premium_source": getattr(premium, "quote_source", None),
            "premium_bucket": getattr(premium, "premium_bucket", None),
            "degraded": bool(getattr(premium, "degraded", False)),
            "evaluate_api": "premium_snapshot",
            "mutate_premium_state": False,
        }
        current_pnl = getattr(premium, "unrealized_pnl_pct", None)
        mfe = getattr(premium, "mfe_pct", None)
        if result is None:
            return ExitDecision(
                action=EXIT_ACTION_HOLD,
                reason=None,
                current_pnl_pct=float(current_pnl) if current_pnl is not None else None,
                mfe_pct=float(mfe) if mfe is not None else None,
                triggered_value=getattr(premium, "executable_exit_price", None),
                trigger_value=getattr(premium, "executable_exit_price", None),
                diagnostics=diagnostics,
            )

        exit_premium, reason, telemetry = result
        if isinstance(telemetry, dict):
            diagnostics.update(telemetry)
            if telemetry.get("pnl_pct") is not None:
                current_pnl = telemetry.get("pnl_pct")
        action = exit_action_from_reason(str(reason or ""), sell=True)
        threshold = None
        threshold_name = None
        if isinstance(telemetry, dict):
            for key in (
                "trail_stop",
                "policy_threshold",
                "threshold",
                "drawdown_limit",
                "mfe_giveback_threshold",
                "time_exit_minutes",
            ):
                if telemetry.get(key) is not None:
                    try:
                        threshold = float(telemetry.get(key))
                        threshold_name = key
                        break
                    except (TypeError, ValueError):
                        threshold_name = key
                        break
        return ExitDecision(
            action=action,
            reason=str(reason or "") or None,
            current_pnl_pct=float(current_pnl) if current_pnl is not None else None,
            mfe_pct=float(mfe) if mfe is not None else None,
            threshold_name=threshold_name,
            threshold_value=threshold,
            triggered_value=float(exit_premium) if exit_premium is not None else None,
            trigger_value=float(exit_premium) if exit_premium is not None else None,
            threshold=threshold,
            diagnostics=diagnostics,
        )

    def process_position(
        self,
        position_id: str,
        *,
        position_symbol: str,
        position_metadata: Dict[str, Any],
        current_underlying: float,
        now: Optional[datetime] = None,
        option_quote: Optional[Dict[str, Any]] = None,
        premium: Any = None,
        mutate_premium_state: bool = True,
    ) -> Optional[Tuple[float, str, Dict[str, Any]]]:
        """
        Return (exit_premium, reason, telemetry) to close, or None to hold.

        When ``premium`` (PremiumSnapshot) is provided, skip chain/quote resolution and
        use PE executable economics. When ``mutate_premium_state`` is False, do not
        ratchet HWM/LWM or start degraded timers (evaluate / pure path).
        """
        now = self._normalize_dt(now or datetime.now(timezone.utc))
        osnap = position_metadata.get("option_stealth")
        if not isinstance(osnap, dict):
            return None
        block = osnap.get("engine")
        if not isinstance(block, dict):
            return None
        st = coerce_option_stealth_engine_state(block, osnap)
        if st is None:
            return None
        if not osnap.get("entry_chain_source"):
            osnap["entry_chain_source"] = str(
                position_metadata.get("entry_chain_source")
                or (position_metadata.get("execution_payload") or {}).get("chain_source")
                or ""
            )
        entry_u = float(block.get("entry_underlying", current_underlying) or current_underlying)

        if not osnap.get("_stealth_process_handoff_logged"):
            osnap["_stealth_process_handoff_logged"] = True
            log.info(
                "OPTIONS_STEALTH | stage=process_position | trade_id=%s | position_type=%s | strategy_type=%s | is_spread=%s",
                position_id,
                st.position_type,
                st.strategy_type or "",
                st.position_type
                in (
                    OptionPositionType.DEBIT_SPREAD.value,
                    OptionPositionType.CREDIT_SPREAD.value,
                ),
            )

        prev_eff = st.last_effective_premium if st.last_effective_premium > 0 else None
        is_spread = st.position_type in (
            OptionPositionType.DEBIT_SPREAD.value,
            OptionPositionType.CREDIT_SPREAD.value,
        )
        _st_type = str(st.strategy_type or "").lower()
        _st_name = str(st.strategy_name or "").lower()
        is_trendline_0dte = (
            "trendline" in _st_type
            or _st_name in ("easytrendline_0dte", "trendline_0dte")
            or "trendline" in str(osnap.get("source_path") or "").lower()
        )
        is_breakout_0dte = (
            "breakout" in _st_type
            or _st_name in ("easybreakout_0dte", "breakout_0dte")
            or "breakout" in str(osnap.get("source_path") or "").lower()
        )
        is_orb_0dte = (not is_trendline_0dte) and (
            _st_type == "orb_0dte"
            or is_breakout_0dte
            or "orb" in str(osnap.get("source_path") or block.get("source_path") or "").lower()
            or "easy0dte" in str(osnap.get("source_path") or block.get("source_path") or "").lower()
            or _st_type in ("lotto", "single_leg", "orb")
        )
        is_0dte_single = bool(is_orb_0dte or is_trendline_0dte or is_breakout_0dte) and (not is_spread)
        trendline_mode = str(getattr(st, "trendline_mode", "") or osnap.get("trendline_mode") or "").upper()
        is_impulse_mode = bool(
            getattr(st, "impulse_mode", False)
            or str(getattr(st, "trendline_mode", "") or "").upper() == "IMPULSE"
            or bool(osnap.get("impulse_mode"))
            or str(osnap.get("trendline_mode") or "").upper() == "IMPULSE"
        )
        is_slow_trend_mode = trendline_mode == "SLOW_TREND" or bool(osnap.get("slow_trend_mode"))
        is_early_entry_mode = trendline_mode == "EARLY_ENTRY" or bool(osnap.get("early_entry_mode"))
        is_retest_mode = trendline_mode == "RETEST" or bool(osnap.get("retest_mode"))
        osnap["trendline_mode"] = trendline_mode or "STANDARD"
        osnap["entry_mode"] = (
            "impulse"
            if is_impulse_mode
            else "slow_trend"
            if is_slow_trend_mode
            else "early_entry"
            if is_early_entry_mode
            else "standard"
        )
        u_move_pct_eval = 0.0
        if entry_u > 0 and current_underlying > 0:
            if str(st.option_side or "").lower() == "put":
                u_move_pct_eval = (float(entry_u) - float(current_underlying)) / float(entry_u)
            else:
                u_move_pct_eval = (float(current_underlying) - float(entry_u)) / float(entry_u)
        underlying_confirms_hwm = False
        if is_trendline_0dte and (not is_spread) and entry_u > 0 and current_underlying > 0:
            u_thr_opp = float(self.config.tline_underlying_opportunity_pct)
            underlying_confirms_hwm = bool(float(u_move_pct_eval) >= float(u_thr_opp) - 1e-15)
            try:
                st.max_underlying_favorable_move_pct = max(
                    float(getattr(st, "max_underlying_favorable_move_pct", 0.0) or 0.0),
                    float(u_move_pct_eval),
                )
            except Exception:
                pass
            if underlying_confirms_hwm:
                st.had_underlying_opportunity = True
                osnap["had_underlying_opportunity"] = True
                st.had_opportunity = True
                osnap["had_opportunity"] = True
        elif is_orb_0dte and entry_u > 0 and current_underlying > 0:
            if bool(getattr(self.config, "orb_degraded_protect_enable", True)):
                u_thr_opp = float(getattr(self.config, "orb_underlying_opportunity_pct", 0.0025))
                orb_under_confirms = bool(float(u_move_pct_eval) >= float(u_thr_opp) - 1e-15)
                try:
                    st.max_underlying_favorable_move_pct = max(
                        float(getattr(st, "max_underlying_favorable_move_pct", 0.0) or 0.0),
                        float(u_move_pct_eval),
                    )
                except Exception:
                    pass
                if orb_under_confirms:
                    st.had_underlying_opportunity = True
                    osnap["had_underlying_opportunity"] = True
                    st.had_opportunity = True
                    osnap["had_opportunity"] = True
        source_path = str(block.get("source_path") or osnap.get("source_path") or "").lower()
        break_arch = str(
            getattr(st, "break_archetype", "")
            or block.get("break_archetype")
            or osnap.get("break_archetype")
            or ""
        ).strip().lower()
        if break_arch:
            osnap.setdefault("break_archetype", break_arch)
        parent_normalized = (
            position_metadata.get("normalized_options")
            if isinstance(position_metadata.get("normalized_options"), dict)
            else None
        )
        osnap_normalized = osnap.get("normalized_options") if isinstance(osnap.get("normalized_options"), dict) else None
        normalized = parent_normalized if isinstance(parent_normalized, dict) else osnap_normalized or {}
        if not isinstance(normalized, dict) or not normalized.get("position_type"):
            log.warning(
                "OPTIONS_STEALTH_METADATA_INVALID | trade_id=%s | symbol=%s | reason=missing_normalized_options",
                position_id,
                position_symbol,
            )
        if not is_orb_0dte and not is_trendline_0dte and isinstance(normalized, dict):
            norm_strategy = str(normalized.get("strategy_type") or "").lower()
            norm_source = str(normalized.get("source_path") or source_path or "").lower()
            if norm_strategy == "orb_0dte" or norm_source in ("easy0dte", "orb_0dte", "easy_orb_0dte"):
                is_orb_0dte = True
                is_0dte_single = not is_spread
        resolved_strategy_type = str(
            normalized.get("strategy_type")
            or normalized.get("source_path")
            or st.strategy_type
            or ""
        )
        premium_driven = (
            premium is not None
            and getattr(premium, "executable_exit_price", None) is not None
            and float(getattr(premium, "executable_exit_price") or 0.0) > 0
        )
        # NOTE: entry_archetype / profile resolution continues below; premium_driven skips resolve_*
        entry_archetype = self._resolve_entry_archetype(
            source_path=source_path,
            is_orb_0dte=is_orb_0dte,
            trendline_mode=trendline_mode,
            impulse_mode=is_impulse_mode,
            is_trendline_0dte=is_trendline_0dte,
            trendline_break_archetype=break_arch,
        )
        log.info(
            "OPTIONS_STEALTH_PROFILE_SOURCE_AUDIT | trade_id=%s | symbol=%s | parent_normalized_metadata_present=%s | "
            "osnap_normalized_metadata_present=%s | resolved_source_path=%s | resolved_strategy_type=%s | is_orb_0dte=%s | "
            "is_trendline_0dte=%s | entry_archetype=%s",
            position_id,
            position_symbol,
            str(bool(isinstance(parent_normalized, dict))).lower(),
            str(bool(isinstance(osnap_normalized, dict))).lower(),
            source_path or "unknown",
            resolved_strategy_type or "unknown",
            str(bool(is_orb_0dte)).lower(),
            str(bool(is_trendline_0dte)).lower(),
            entry_archetype,
        )
        profile = self._resolve_exit_profile(
            source_path=source_path,
            position_type=str(st.position_type or ""),
            entry_archetype=entry_archetype,
            is_orb_0dte=is_orb_0dte,
            is_trendline_0dte=is_trendline_0dte,
            trendline_break_archetype=break_arch,
        )
        block["entry_archetype"] = entry_archetype
        block["source_path"] = source_path or str(block.get("source_path") or "")
        profile_sig = (
            profile.source_path,
            profile.entry_archetype,
            profile.position_type,
            round(profile.trail_trigger_pct, 6),
            round(profile.trail_distance_pct, 6),
            round(profile.max_pnl_drawdown_trigger_pct, 6),
            round(profile.max_pnl_drawdown_exit_pct, 6),
            round(profile.no_progress_minutes, 4),
            round(profile.time_exit_minutes, 4),
            round(profile.min_hold_seconds, 4),
            bool(profile.allow_underlying_structure_exit),
        )
        if osnap.get("_profile_sig") != profile_sig:
            osnap["_profile_sig"] = profile_sig
            log.warning(
                "OPTIONS_STEALTH_PROFILE_RESOLVED | trade_id=%s | symbol=%s | source_path=%s | entry_archetype=%s | position_type=%s | "
                "trail_trigger_pct=%.4f | trail_distance_pct=%.4f | "
                "max_pnl_drawdown_trigger_pct=%.4f | "
                "max_pnl_drawdown_exit_pct=%.4f | no_progress_minutes=%.2f | time_exit_minutes=%.2f",
                position_id,
                position_symbol,
                profile.source_path,
                profile.entry_archetype,
                profile.position_type,
                profile.trail_trigger_pct,
                profile.trail_distance_pct,
                profile.max_pnl_drawdown_trigger_pct,
                profile.max_pnl_drawdown_exit_pct,
                profile.no_progress_minutes,
                profile.time_exit_minutes,
            )

        if premium_driven:
            prem = float(premium.executable_exit_price)
            prem_src = str(getattr(premium, "quote_source", None) or "premium_snapshot")
            pe_degraded = bool(getattr(premium, "degraded", False))
            def _positive_quote(value: Any) -> Optional[float]:
                try:
                    number = float(value)
                except (TypeError, ValueError):
                    return None
                if number != number or number <= 0.0:
                    return None
                return number

            option_bid = _positive_quote(getattr(premium, "bid", None))
            if option_bid is None:
                option_bid = _positive_quote(getattr(premium, "current_bid", None))
            option_ask = _positive_quote(getattr(premium, "ask", None))
            if option_ask is None:
                option_ask = _positive_quote(getattr(premium, "current_ask", None))
            detail = {
                "from_premium_engine": True,
                "price_source_telemetry": "live" if not pe_degraded else "degraded",
                "mark_quality": "live_single_leg" if not pe_degraded else "synthetic",
                "quote_age_seconds": getattr(premium, "quote_age_seconds", None),
                "synthetic_pricing_active": pe_degraded,
                "premium_bucket": getattr(premium, "premium_bucket", None),
                "mark_is_exit_grade": (not pe_degraded),
                "unrealized_pnl_pct": getattr(premium, "unrealized_pnl_pct", None),
                "mfe_pct": getattr(premium, "mfe_pct", None),
                "option_bid": option_bid,
                "option_ask": option_ask,
            }
            if option_bid is not None:
                osnap["option_bid"] = option_bid
            if option_ask is not None:
                osnap["option_ask"] = option_ask
            # Seed extremes from Premium Engine (authoritative this tick)
            if getattr(premium, "premium_hwm", None) is not None:
                st.premium_hwm = float(premium.premium_hwm)
                if is_spread:
                    st.spread_hwm = float(premium.premium_hwm)
            if getattr(premium, "premium_lwm", None) is not None:
                st.premium_lwm = float(premium.premium_lwm)
                if is_spread:
                    st.spread_lwm = float(premium.premium_lwm)
            if getattr(premium, "mfe_pct", None) is not None:
                try:
                    _prev_mfe = osnap.get("max_pnl_pct")
                    st.max_pnl_pct = max(float(st.max_pnl_pct or 0.0), float(premium.mfe_pct))
                    osnap["max_pnl_pct"] = st.max_pnl_pct
                    from modules.po_mfe20_snapshot import mark_po_20p_due

                    mark_po_20p_due(osnap, _prev_mfe, st.max_pnl_pct)
                except Exception:
                    pass
            # PE owns MFE; arm opportunity even on evaluate(mutate=False) so giveback/trail/time
            # cutovers stay consistent with Premium Engine extremes.
            try:
                _opp_trig = float(
                    getattr(self.config, "dte_single_mfe_trail_trigger_pct", 0.10) or 0.10
                )
                if (not st.had_opportunity) and float(st.max_pnl_pct or 0.0) + 1e-12 >= _opp_trig:
                    st.had_opportunity = True
                    osnap["had_opportunity"] = True
                    log.info(
                        "OPTION_OPPORTUNITY_DETECTED | symbol=%s | trade_id=%s | max_pnl_pct=%.4f | source=premium_engine_mfe",
                        position_symbol,
                        position_id,
                        float(st.max_pnl_pct or 0.0),
                    )
            except Exception:
                pass
            age = getattr(premium, "quote_age_seconds", None)
            if age is not None:
                osnap["seconds_since_live_option_quote"] = float(age)
                osnap["quote_age_seconds"] = float(age)
            osnap["mark_is_exit_grade"] = bool(detail["mark_is_exit_grade"])
            osnap["last_premium_source"] = prem_src
            osnap["degraded_data_active"] = pe_degraded
            if pe_degraded:
                osnap["degraded_data_source"] = str(
                    getattr(premium, "degradation_reason", None) or prem_src
                )
            else:
                osnap["degraded_data_source"] = None
            # PE owns degraded_since — do not start stealth timers on premium path
            log.info(
                "OPTIONS_STEALTH | stage=premium_engine_consume | trade_id=%s | symbol=%s | "
                "executable=%.6f | source=%s | degraded=%s | mutate_state=%s",
                position_id,
                position_symbol,
                prem,
                prem_src,
                str(pe_degraded).lower(),
                str(bool(mutate_premium_state)).lower(),
            )
        elif is_spread:
            prem, prem_src, detail = resolve_spread_net_value(
                current_underlying=current_underlying,
                entry_underlying=entry_u,
                cfg=self.config,
                previous_effective=prev_eff,
                position_symbol=position_symbol,
                legs=st.legs,
                option_quote=option_quote,
                entry_value=max(0.01, st.entry_value or st.entry_premium),
                position_type=str(st.position_type or OptionPositionType.DEBIT_SPREAD.value),
                trade_id=str(position_id or ""),
            )
            mon_mode = str(detail.get("spread_monitoring_mode") or "")
            spread_log_key = (prem_src, mon_mode)
            if osnap.get("_last_spread_value_log_key") != spread_log_key:
                osnap["_last_spread_value_log_key"] = spread_log_key
                log.info(
                    "OPTIONS_STEALTH | stage=spread_value_update | trade_id=%s | symbol=%s | current_value=%.6f | source=%s | monitoring_mode=%s | quality=%s",
                    position_id,
                    position_symbol,
                    prem,
                    prem_src,
                    mon_mode,
                    detail.get("spread_premium_source_quality"),
                )
            prev_mode = osnap.get("_last_spread_monitoring_mode")
            if mon_mode and mon_mode != prev_mode:
                osnap["_last_spread_monitoring_mode"] = mon_mode
                log.info(
                    "OPTIONS_STEALTH | stage=spread_monitoring_mode | trade_id=%s | symbol=%s | monitoring_mode=%s | premium_source=%s",
                    position_id,
                    position_symbol,
                    mon_mode,
                    prem_src,
                )
            if prem_src in (
                "spread_modeled_fallback",
                "spread_position_value_fallback",
                "spread_mtm_incomplete_quotes",
                "spread_mtm_no_legs",
                "spread_mtm_missing_leg_role",
            ):
                log.info(
                    "OPTIONS_STEALTH | stage=spread_quote_fallback | trade_id=%s | symbol=%s | source=%s | monitoring_mode=%s | degraded=true",
                    position_id,
                    position_symbol,
                    prem_src,
                    mon_mode,
                )
            weak_modes = (
                "leg_derived_mixed_degraded",
                "leg_derived_all_modeled_degraded",
                "position_value_fallback",
                "entry_anchor_fallback",
                "position_value_fallback_no_legs",
                "entry_anchor_fallback_no_legs",
                "spread_mtm_incomplete_quotes",
                "spread_mtm_no_legs",
                "spread_mtm_missing_leg_role",
            )
            if mon_mode in weak_modes and not osnap.get("_spread_degraded_monitoring_logged"):
                osnap["_spread_degraded_monitoring_logged"] = True
                log.info(
                    "OPTIONS_STEALTH | stage=runtime_validation_warning | trade_id=%s | missing=none | degraded_spread_monitoring=%s",
                    position_id,
                    mon_mode,
                )
            if self.config.require_live_option_data and prem_src in (
                "spread_modeled_fallback",
                "spread_position_value_fallback",
                "spread_mtm_incomplete_quotes",
                "spread_mtm_no_legs",
                "spread_mtm_missing_leg_role",
            ):
                if not osnap.get("_live_data_required_skip_logged"):
                    osnap["_live_data_required_skip_logged"] = True
                    log.warning(
                        "OPTIONS_STEALTH | stage=live_data_required_skip | trade_id=%s | symbol=%s | "
                        "reason=degraded_spread_source | source=%s",
                        position_id,
                        position_symbol,
                        prem_src,
                    )
                osnap["degraded_data_active"] = True
                osnap["degraded_data_source"] = prem_src
                if mutate_premium_state and not osnap.get("degraded_data_since"):
                    osnap["degraded_data_since"] = now.isoformat()
                if mutate_premium_state and not osnap.get("degraded_start_timestamp"):
                    osnap["degraded_start_timestamp"] = now.isoformat()
            # ORB spread open grace: protect opening-minute winners from transient leg quote gaps.
            if is_orb_0dte and is_spread:
                seconds_since_entry_spread = max(
                    0.0,
                    (now - self._normalize_dt(st.opened_at)).total_seconds(),
                )
                open_grace_active = bool(osnap.get("open_grace_active"))
                open_grace_source = str(prem_src or "")
                open_grace_eligible = open_grace_source in {
                    "spread_mtm_incomplete_quotes",
                    "spread_mtm_no_legs",
                    "spread_mtm_missing_leg_role",
                    "spread_modeled_fallback",
                    "spread_position_value_fallback",
                }
                open_grace_window = max(30.0, float(self.config.orb_spread_open_grace_seconds))
                if open_grace_eligible and seconds_since_entry_spread <= open_grace_window:
                    open_grace_attempts = int(osnap.get("open_grace_attempts", 0) or 0) + 1
                    osnap["open_grace_attempts"] = open_grace_attempts
                    osnap["open_grace_active"] = True
                    osnap["open_grace_start_ts"] = osnap.get("open_grace_start_ts") or now.isoformat()
                    if self.config.orb_spread_open_grace_enable_synthetic and float(prem or 0.0) <= 0.0:
                        prem = max(
                            0.01,
                            float(st.entry_debit or 0.0) or float(st.entry_value or 0.0) or float(st.entry_premium or 0.0),
                            float(osnap.get("last_valid_option_price") or 0.0),
                        )
                        prem_src = "synthetic_open_grace"
                        detail["price_source_telemetry"] = "synthetic"
                        detail["fallback_used"] = True
                    log.warning(
                        "OPTIONS_STEALTH_OPEN_GRACE_ACTIVE | trade_id=%s | symbol=%s | source=%s | attempts=%d | "
                        "seconds_since_entry=%.1f | grace_seconds=%.1f",
                        position_id,
                        position_symbol,
                        open_grace_source,
                        open_grace_attempts,
                        seconds_since_entry_spread,
                        open_grace_window,
                    )
                elif open_grace_active and prem_src == "spread_mtm_mid_last":
                    osnap["open_grace_active"] = False
                    osnap["open_grace_recovered_ts"] = now.isoformat()
                    log.info(
                        "OPTIONS_STEALTH_OPEN_GRACE_RECOVERED | trade_id=%s | symbol=%s | source=%s | attempts=%s",
                        position_id,
                        position_symbol,
                        prem_src,
                        int(osnap.get("open_grace_attempts", 0) or 0),
                    )
                elif (
                    open_grace_active
                    and seconds_since_entry_spread > open_grace_window
                    and open_grace_eligible
                    and int(osnap.get("open_grace_attempts", 0) or 0)
                    >= int(max(1, self.config.orb_spread_open_grace_max_attempts))
                ):
                    osnap["open_grace_active"] = False
                    osnap["open_grace_failed"] = True
                    log.warning(
                        "OPTIONS_STEALTH_OPEN_GRACE_FAILED | trade_id=%s | symbol=%s | source=%s | attempts=%s | "
                        "seconds_since_entry=%.1f",
                        position_id,
                        position_symbol,
                        open_grace_source,
                        int(osnap.get("open_grace_attempts", 0) or 0),
                        seconds_since_entry_spread,
                    )
        else:
            oq_single = dict(option_quote or {}) if isinstance(option_quote, dict) else {}
            last_valid_px = None
            try:
                last_valid_px = float(osnap.get("last_valid_option_price")) if osnap.get("last_valid_option_price") is not None else None
            except (TypeError, ValueError):
                last_valid_px = None
            last_valid_ts = osnap.get("last_valid_option_quote_ts")
            if (not is_0dte_single) and last_valid_px is not None and isinstance(last_valid_ts, str):
                try:
                    lv_dt = self._normalize_dt(datetime.fromisoformat(last_valid_ts))
                    cached_age = max(0.0, (now - lv_dt).total_seconds())
                    oq_single["cached_price"] = last_valid_px
                    oq_single["cached_age_seconds"] = cached_age
                except Exception:
                    pass
            sec_since_entry = (now - self._normalize_dt(st.opened_at)).total_seconds()
            is_short_prem = bool(osnap.get("is_short_premium"))
            prem, prem_src, detail = resolve_option_price(
                entry_underlying=entry_u,
                current_underlying=current_underlying,
                option_side=st.option_side,
                entry_premium=st.entry_premium,
                cfg=self.config,
                option_quote=oq_single,
                previous_effective=prev_eff,
                delta_at_entry=st.delta_at_entry,
                strike=st.strike,
                position_symbol=position_symbol,
                is_0dte=is_0dte_single,
                seconds_since_entry=sec_since_entry,
                trade_id=str(position_id or ""),
                stored_last_valid_option_price=last_valid_px,
                is_short_premium=is_short_prem,
            )
            if is_0dte_single and (not is_spread):
                prem, detail = clamp_tline_monitor_premium_true_to_live(
                    prem=prem,
                    detail=detail,
                    entry_chain_source=str(osnap.get("entry_chain_source") or ""),
                    entry_premium=float(st.entry_premium or 0.0),
                    option_side=str(st.option_side or ""),
                    cfg=self.config,
                )
            px_telem = str(detail.get("price_source_telemetry") or "").strip().lower()
            if px_telem == "live":
                osnap["last_live_degraded_reason"] = None
            elif prem_src == "live_quote_unavailable":
                osnap["last_live_degraded_reason"] = str(detail.get("live_degraded_reason") or "unknown")
            if self.config.require_live_quotes and (not is_spread) and is_0dte_single:
                self._fix_session_types()
                self._session["live_quote_tick_total"] = int(self._session.get("live_quote_tick_total", 0)) + 1
                if px_telem == "live":
                    self._session["live_quote_tick_hits"] = int(self._session.get("live_quote_tick_hits", 0)) + 1
                if prem_src == "live_quote_unavailable":
                    self._session["degraded_quote_count"] = int(self._session.get("degraded_quote_count", 0)) + 1
                    _emit_option_degraded_live_quote_log(
                        self.config,
                        symbol=position_symbol,
                        trade_id=str(position_id or ""),
                        reason=str(detail.get("live_degraded_reason") or "unknown"),
                        action_taken="hold",
                        dedupe_key=f"{str(position_id or '-')}|{position_symbol}",
                    )
            if osnap.get("_last_single_leg_src_logged") != prem_src:
                osnap["_last_single_leg_src_logged"] = prem_src
                log.info(
                    "OPTIONS_STEALTH | stage=single_leg_value_update | trade_id=%s | symbol=%s | current_value=%.6f | source=%s",
                    position_id,
                    position_symbol,
                    prem,
                    prem_src,
                )
            degraded_single = (
                self.config.require_live_option_data
                and prem_src
                in (
                    "delta_estimate",
                    "underlying_proxy",
                    "last_valid_stale",
                    "synthetic_modeled",
                    "synthetic_proxy",
                )
            ) or (self.config.require_live_quotes and prem_src == "live_quote_unavailable")
            if is_0dte_single and px_telem and px_telem != "live":
                degraded_single = True
            if degraded_single:
                log_flag = (
                    "_live_data_required_skip_logged"
                    if prem_src
                    in (
                        "delta_estimate",
                        "underlying_proxy",
                        "last_valid_stale",
                        "synthetic_modeled",
                        "synthetic_proxy",
                    )
                    else "_live_quotes_strict_skip_logged"
                )
                if not osnap.get(log_flag):
                    osnap[log_flag] = True
                    reason = (
                        "degraded_single_leg_source"
                        if prem_src
                        in (
                            "delta_estimate",
                            "underlying_proxy",
                            "last_valid_stale",
                            "synthetic_modeled",
                            "synthetic_proxy",
                        )
                        else "live_quotes_strict_no_fresh_quote"
                    )
                    log.warning(
                        "OPTIONS_STEALTH | stage=live_data_required_skip | trade_id=%s | symbol=%s | "
                        "reason=%s | source=%s",
                        position_id,
                        position_symbol,
                        reason,
                        prem_src,
                    )
                osnap["degraded_data_active"] = True
                osnap["degraded_data_source"] = prem_src
                if mutate_premium_state and not osnap.get("degraded_data_since"):
                    osnap["degraded_data_since"] = now.isoformat()
                if mutate_premium_state and not osnap.get("degraded_start_timestamp"):
                    osnap["degraded_start_timestamp"] = now.isoformat()
        fallback_sources = {
            "delta_estimate",
            "underlying_proxy",
            "live_quote_unavailable",
            "last_valid_stale",
            "synthetic_modeled",
            "synthetic_proxy",
            "spread_position_value_fallback",
            "spread_modeled_fallback",
            "spread_mtm_no_legs",
            "spread_mtm_missing_leg_role",
            "spread_mtm_incomplete_quotes",
            "synthetic_open_grace",
            "missing_or_invalid",
            "modeled_proxy",
            "current_position_value_fallback",
        }
        try:
            from modules.exit_grade_durability import (
                canonicalize_quote_source,
                premium_source_is_live_contract_quote,
            )

            prem_src_canon = canonicalize_quote_source(prem_src)
            has_live_contract = bool(
                premium_source_is_live_contract_quote(prem_src_canon)
                or prem_src_canon in {"mark_or_mid"}
            )
        except Exception:
            prem_src_canon = str(prem_src or "").strip().lower()
            has_live_contract = prem_src_canon in {
                "exact_quote",
                "nearest",
                "cached_quote",
                "chain_strike_match",
                "spread_leg_quotes",
                "spread_mtm_mid_last",
                "premium_snapshot",
                "mark_or_mid",
                "fresh_chain_refresh",
                "fresh_chain_force_refresh",  # legacy
                "fresh_chain_bridge",
                "live_chain_monitor",
            }
        px_telem_g = str(detail.get("price_source_telemetry") or "").strip().lower()
        has_live_data = has_live_contract or (not is_spread and px_telem_g == "live") or (
            premium_driven and px_telem_g == "live"
        )
        has_fallback_data = prem_src_canon in fallback_sources and float(prem or 0.0) > 0.0
        # Premium-engine path: keep quote freshness clocks current even under evaluate(mutate=False).
        _allow_clock_mutation = bool(mutate_premium_state) or bool(premium_driven)
        if _allow_clock_mutation and has_live_data:
            osnap["last_live_data_ts"] = now.isoformat()
        if _allow_clock_mutation and (has_live_data or has_fallback_data):
            osnap["last_any_data_ts"] = now.isoformat()
        if _allow_clock_mutation and (not is_spread) and px_telem_g == "live":
            # Never refresh last_valid / live clocks from absurd wrong-strike or collapse ticks.
            if not bool(detail.get("absurd_live_premium_rejected")):
                osnap["last_valid_option_price"] = float(prem)
                osnap["last_valid_option_quote_ts"] = now.isoformat()
                osnap["last_live_option_quote_ts"] = now.isoformat()
        elif _allow_clock_mutation and has_live_contract:
            if not bool(detail.get("absurd_live_premium_rejected")):
                osnap["last_valid_option_price"] = float(prem)
                osnap["last_valid_option_quote_ts"] = now.isoformat()
        osnap["option_price_source_telemetry"] = px_telem_g
        osnap["synthetic_pricing_active"] = bool(detail.get("synthetic_pricing_active"))
        if is_0dte_single:
            if premium_driven and getattr(premium, "quote_age_seconds", None) is not None:
                secs_no_live_opt = float(premium.quote_age_seconds)
            else:
                ref_live_raw = osnap.get("last_live_option_quote_ts")
                try:
                    if isinstance(ref_live_raw, str) and str(ref_live_raw).strip():
                        live_dt_ref = self._normalize_dt(datetime.fromisoformat(str(ref_live_raw)))
                    else:
                        live_dt_ref = self._normalize_dt(st.opened_at)
                    secs_no_live_opt = max(0.0, (now - live_dt_ref).total_seconds())
                except Exception:
                    secs_no_live_opt = max(0.0, (now - self._normalize_dt(st.opened_at)).total_seconds())
            osnap["seconds_since_live_option_quote"] = secs_no_live_opt
            psrc_key = f"psrc|{position_id}|{position_symbol}"
            if _option_telemetry_log_should_emit(self.config, _option_price_source_log_last, psrc_key):
                log.info(
                    "OPTION_PRICE_SOURCE | trade_id=%s | symbol=%s | source=%s | premium_source=%s",
                    str(position_id or "-"),
                    position_symbol,
                    px_telem_g or "-",
                    prem_src,
                )
            if px_telem_g != "live":
                deg_key = f"degm|{position_id}|{position_symbol}"
                if _option_telemetry_log_should_emit(self.config, _option_degraded_mode_log_last, deg_key):
                    log.warning(
                        "OPTION_DEGRADED_MODE | trade_id=%s | symbol=%s | duration_sec=%.1f",
                        str(position_id or "-"),
                        position_symbol,
                        float(secs_no_live_opt),
                    )
        log.info(
            "OPTIONS_DATA_SOURCE_UPDATE | trade_id=%s | symbol=%s | data_source=%s | quote_age_seconds=%s | spread_pct=%s | fallback_used=%s",
            position_id,
            position_symbol,
            prem_src,
            detail.get("quote_age_seconds"),
            detail.get("spread_pct"),
            str(bool(detail.get("fallback_used"))).lower(),
        )
        if bool(detail.get("fallback_used")):
            log.warning(
                "OPTIONS_DATA_FALLBACK_USED | trade_id=%s | symbol=%s | source=%s | quote_age_seconds=%s",
                position_id,
                position_symbol,
                prem_src,
                detail.get("quote_age_seconds"),
            )
        if not osnap.get("degraded_data_active"):
            osnap["degraded_data_since"] = None
            osnap["degraded_data_source"] = None
            osnap["degraded_start_timestamp"] = None
        quote_age_seconds = 0.0
        try:
            if is_spread:
                quote_age_seconds = max(
                    float(detail.get("long_leg_quote_age_seconds") or 0.0),
                    float(detail.get("short_leg_quote_age_seconds") or 0.0),
                )
            else:
                quote_age_seconds = float(detail.get("quote_age_seconds") or 0.0)
        except (TypeError, ValueError):
            quote_age_seconds = 0.0
        mark_quality = str(detail.get("mark_quality") or "").strip().lower()
        if not mark_quality:
            if is_spread:
                mark_quality = "live_two_leg_spread" if prem_src == "spread_mtm_mid_last" else "partial_leg"
            else:
                mark_quality = "live_single_leg" if px_telem_g == "live" else "synthetic"
        mark_is_exit_grade = bool(detail.get("mark_is_exit_grade"))
        if not mark_is_exit_grade:
            mark_is_exit_grade = mark_quality in {"live_two_leg_spread", "live_single_leg"}
        osnap["mark_quality"] = mark_quality
        osnap["mark_is_exit_grade"] = bool(mark_is_exit_grade)
        osnap["quote_age_seconds"] = float(quote_age_seconds)
        osnap["leg_completeness"] = str(detail.get("leg_completeness") or ("single_leg" if not is_spread else "missing"))
        osnap["long_leg_quote_age_seconds"] = float(detail.get("long_leg_quote_age_seconds") or 0.0)
        osnap["short_leg_quote_age_seconds"] = float(detail.get("short_leg_quote_age_seconds") or 0.0)
        osnap["using_synthetic_mark"] = bool(detail.get("using_synthetic_mark") or mark_quality in {"synthetic", "partial_leg", "missing"})
        osnap["using_stale_mark"] = bool(detail.get("using_stale_mark") or mark_quality in {"stale_last_valid", "cached_recent"})
        trusted_td, trusted_td_why = self._orb_trusted_degraded_spread_mark_eligible(
            is_orb_0dte=is_orb_0dte,
            is_spread=is_spread,
            synthetic_quote=bool(detail.get("synthetic_pricing_active")),
            prem_src=str(prem_src or ""),
            mark_quality=mark_quality,
            quote_age_seconds=float(quote_age_seconds or 0.0),
            detail=detail,
            u_move_pct_eval=float(u_move_pct_eval or 0.0),
            entry_debit=float(st.entry_debit or st.entry_value or st.entry_premium or 0.0),
            spread_mid=float(detail.get("spread_mid_value") or prem or 0.0),
            spread_liq=float(detail.get("spread_liquidation_value") or detail.get("spread_mid_value") or prem or 0.0),
            position_type=str(st.position_type or ""),
        )
        osnap["trusted_degraded_spread_mark"] = bool(trusted_td)
        osnap["trusted_degraded_spread_mark_reason"] = str(trusted_td_why or "")
        stale_cat = float(getattr(self.config, "quote_stale_max_age_seconds", 90.0) or 90.0)
        osnap["orb_spread_degraded_running"] = bool(
            trusted_td
            and is_orb_0dte
            and is_spread
            and mark_quality in {"partial_leg", "cached_recent", "nearest", "cached_quote"}
            and float(quote_age_seconds or 0.0) <= stale_cat
        )
        if bool(osnap.get("orb_spread_degraded_running")):
            _emit_option_stealth_state_if_due(
                cfg=self.config,
                trade_id=str(position_id or "-"),
                symbol=str(position_symbol or "-"),
                message=(
                    f"ORB_0DTE_STEALTH_STATE | path=orb_0dte_spread | trade_id={position_id} | symbol={position_symbol} | "
                    f"degraded_running=true | trusted_degraded=true | mark_quality={mark_quality} | "
                    f"quote_age_seconds={float(quote_age_seconds or 0.0):.2f} | timeout_clock_frozen_pre="
                    f"{str(bool(st.timeout_clock_frozen)).lower()} | watchdog_gap_seconds={float(osnap.get('watchdog_gap_seconds') or 0.0):.2f} | "
                    f"trusted_reason={trusted_td_why}"
                ),
            )
        freeze_reasons: List[str] = []
        watchdog_gap_seconds = float(osnap.get("watchdog_gap_seconds") or 0.0)
        wd_trusted_skip = bool(
            is_orb_0dte
            and is_spread
            and bool(trusted_td)
            and bool(getattr(self.config, "orb_watchdog_trusted_skip_clock_freeze", True))
        )
        if watchdog_gap_seconds > 0:
            st.watchdog_gap_seen = True
            if not wd_trusted_skip:
                freeze_reasons.append("watchdog_gap")
                log.warning(
                    "OPTION_MONITOR_WATCHDOG_GAP_APPLIED | trade_id=%s | symbol=%s | gap_seconds=%.1f | reason=watchdog_gap",
                    str(position_id or ""),
                    position_symbol,
                    float(watchdog_gap_seconds),
                )
            else:
                log.info(
                    "OPTION_MONITOR_CLOCK_DEGRADED_RUNNING | trade_id=%s | symbol=%s | reason=watchdog_gap_trusted_degraded | "
                    "gap_seconds=%.1f | skip_timeout_freeze=true",
                    str(position_id or ""),
                    position_symbol,
                    float(watchdog_gap_seconds),
                )
            if is_orb_0dte and is_spread:
                osnap["orb_watchdog_gap_accum_sec"] = float(osnap.get("orb_watchdog_gap_accum_sec") or 0.0) + float(
                    watchdog_gap_seconds
                )
        if not mark_is_exit_grade:
            if not (is_orb_0dte and is_spread and bool(trusted_td)):
                freeze_reasons.append(f"mark_quality:{mark_quality}")
        if bool(getattr(self.config, "freeze_timeouts_on_degraded_marks", True)) and freeze_reasons:
            if not st.timeout_clock_frozen:
                st.timeout_clock_frozen = True
                st.timeout_clock_last_frozen_at = now
                log.info(
                    "OPTION_TIMEOUT_CLOCK_FROZEN | trade_id=%s | symbol=%s | previous_clock_state=running | new_clock_state=frozen | reason=%s",
                    str(position_id or ""),
                    position_symbol,
                    "|".join(freeze_reasons),
                )
            elif st.timeout_clock_last_frozen_at is not None:
                st.timeout_frozen_seconds = float(st.timeout_frozen_seconds or 0.0) + max(
                    0.0,
                    (now - self._normalize_dt(st.timeout_clock_last_frozen_at)).total_seconds(),
                )
                st.timeout_clock_last_frozen_at = now
            st.good_mark_tick_count = 0
        else:
            if st.timeout_clock_frozen:
                st.timeout_clock_frozen = False
                st.timeout_clock_last_resumed_at = now
                resume_reason = "exit_grade_mark" if mark_is_exit_grade else "trusted_degraded_or_watchdog_cleared"
                log.info(
                    "OPTION_TIMEOUT_CLOCK_RESUMED | trade_id=%s | symbol=%s | previous_clock_state=frozen | new_clock_state=running | reason=%s",
                    str(position_id or ""),
                    position_symbol,
                    resume_reason,
                )
                if resume_reason != "exit_grade_mark":
                    log.info(
                        "OPTION_MONITOR_CLOCK_RECOVERED | trade_id=%s | symbol=%s | reason=%s | mark_quality=%s | trusted_degraded=%s",
                        str(position_id or ""),
                        position_symbol,
                        resume_reason,
                        mark_quality,
                        str(bool(trusted_td)).lower(),
                    )
                if st.timeout_clock_last_frozen_at is not None:
                    st.timeout_frozen_seconds = float(st.timeout_frozen_seconds or 0.0) + max(
                        0.0,
                        (now - self._normalize_dt(st.timeout_clock_last_frozen_at)).total_seconds(),
                    )
                st.timeout_clock_last_frozen_at = None
            st.good_mark_tick_count = int(st.good_mark_tick_count) + 1
        osnap["good_mark_tick_count"] = int(st.good_mark_tick_count)
        osnap["timeout_clock_frozen"] = bool(st.timeout_clock_frozen)
        osnap["timeout_frozen_seconds"] = float(st.timeout_frozen_seconds or 0.0)
        osnap["watchdog_gap_seen"] = bool(st.watchdog_gap_seen)
        if is_orb_0dte and is_spread:
            if (not bool(osnap.get("mark_is_exit_grade"))) and str(osnap.get("mark_quality") or "") == "partial_leg":
                if not osnap.get("orb_partial_leg_first_iso"):
                    osnap["orb_partial_leg_first_iso"] = now.isoformat()
            elif bool(osnap.get("mark_is_exit_grade")):
                osnap["orb_partial_leg_first_iso"] = None
        log.info(
            "OPTION_POSITION_MARK_AUDIT | trade_id=%s | symbol=%s | strategy_type=%s | position_type=%s | option_side=%s | "
            "entry_net_debit=%.6f | mid_value=%.6f | liquidation_value=%.6f | current_pnl_pct_mid=%.4f | current_pnl_pct_liquidation=%.4f | "
            "mark_quality=%s | mark_is_exit_grade=%s | quote_source=%s | quote_age_seconds=%.1f | leg_completeness=%s",
            str(position_id or ""),
            position_symbol,
            str(st.strategy_type or ""),
            str(st.position_type or ""),
            str(st.option_side or ""),
            float(st.entry_debit or st.entry_value or st.entry_premium or 0.0),
            float(detail.get("spread_mid_value") if is_spread else prem or 0.0),
            float(detail.get("spread_liquidation_value") if is_spread else detail.get("option_bid") or prem or 0.0),
            float(detail.get("current_pnl_pct_mid") if is_spread else ((float(prem or 0.0) - float(st.entry_premium or 0.0)) / float(st.entry_premium or 1.0))),
            float(detail.get("current_pnl_pct_liquidation") if is_spread else ((float(detail.get("option_bid") or prem or 0.0) - float(st.entry_premium or 0.0)) / float(st.entry_premium or 1.0))),
            mark_quality,
            str(bool(mark_is_exit_grade)).lower(),
            str(prem_src),
            float(quote_age_seconds),
            str(osnap.get("leg_completeness") or "missing"),
        )
        st.last_effective_premium = prem
        st.last_premium_source = prem_src
        st.last_update_at = now
        st.last_underlying_update_at = now
        st.last_premium_update_at = now
        non_exact_quote_seconds = 0.0
        if is_trendline_0dte and (not is_spread):
            try:
                from modules.exit_grade_durability import premium_source_is_trusted_exact
            except Exception:
                premium_source_is_trusted_exact = None  # type: ignore
            trusted_exact = False
            if premium_source_is_trusted_exact is not None:
                trusted_exact = bool(
                    premium_source_is_trusted_exact(
                        str(prem_src or ""),
                        mark_is_exit_grade=bool(mark_is_exit_grade),
                    )
                )
            else:
                _ps = str(prem_src or "").strip().lower()
                trusted_exact = (
                    _ps in {"exact_quote", "exit_grade"}
                    or "exact_contract" in _ps
                    or bool(mark_is_exit_grade)
                )
            if not trusted_exact:
                if not osnap.get("non_exact_quote_since"):
                    osnap["non_exact_quote_since"] = now.isoformat()
                try:
                    ne_dt = self._normalize_dt(datetime.fromisoformat(str(osnap.get("non_exact_quote_since"))))
                    non_exact_quote_seconds = max(0.0, (now - ne_dt).total_seconds())
                except Exception:
                    non_exact_quote_seconds = 0.0
            else:
                # Trusted exact / exit-grade mark — restore normal trail immediately.
                if osnap.get("non_exact_quote_since"):
                    log.info(
                        "OPTIONS_STEALTH | stage=degraded_non_exact_cleared | trade_id=%s | symbol=%s | "
                        "source=%s | mark_is_exit_grade=%s",
                        position_id,
                        position_symbol,
                        prem_src,
                        str(bool(mark_is_exit_grade)).lower(),
                    )
                osnap["non_exact_quote_since"] = None
                osnap.pop("degraded_non_exact_trail_active", None)
                non_exact_quote_seconds = 0.0

        prev_logged_src = osnap.get("last_logged_premium_source")
        if prem_src != prev_logged_src:
            osnap["last_logged_premium_source"] = prem_src
            log.info(
                "TRENDLINE_PIPELINE | stage=premium_update | symbol=%s | premium_source=%s | premium=%.6f",
                position_symbol,
                prem_src,
                prem,
            )
        if (
            prem_src
            in (
                "delta_estimate",
                "underlying_proxy",
                "last_valid_stale",
                "synthetic_modeled",
                "synthetic_proxy",
            )
            and not osnap.get("_premium_fallback_logged")
        ):
            fb_reason = (
                "no_option_quote_supplied"
                if option_quote is None
                else "option_quote_missing_usable_fields"
            )
            log.info(
                "TRENDLINE_PIPELINE | stage=premium_fallback | symbol=%s | reason=%s | fallback_source=%s",
                position_symbol,
                fb_reason,
                prem_src,
            )
            osnap["_premium_fallback_logged"] = True

        premium_jump_this_tick = False
        if prev_eff and prev_eff > 0:
            jump_pct = abs(prem - prev_eff) / prev_eff
            if jump_pct >= float(self.config.premium_jump_recheck_pct):
                premium_jump_this_tick = True
                st.premium_jump_detected = True
                osnap["premium_jump_detected"] = True
                log.info(
                    "TRENDLINE_PIPELINE | stage=option_premium_jump | symbol=%s | jump_pct=%.4f",
                    position_symbol,
                    jump_pct,
                )
                if self.config.force_reeval_on_premium_jump:
                    _log_info_throttled(
                        f"reeval|{position_symbol}|premium_jump",
                        "TRENDLINE_PIPELINE | stage=option_forced_reeval | symbol=%s | reason=premium_jump",
                        position_symbol,
                    )

        allow_hwm = _premium_updates_favorable_hwm(prem_src, detail, is_spread=is_spread)
        if is_trendline_0dte and (not is_spread):
            entry_chain_hwm = str(
                osnap.get("entry_chain_source")
                or (block.get("entry_chain_source") if isinstance(block, dict) else "")
                or ""
            )
            allow_hwm = _premium_hwm_allow_tline_single(
                self.config,
                prem_src,
                detail,
                prem=float(prem or 0.0),
                underlying_move_confirms=bool(underlying_confirms_hwm),
                synthetic_active=bool(detail.get("synthetic_pricing_active")),
                entry_chain_source=entry_chain_hwm,
                entry_premium=float(st.entry_premium or 0.0),
            )
        elif is_orb_0dte and (not is_spread):
            orb_u_confirms = bool(getattr(st, "had_underlying_opportunity", False)) or bool(
                osnap.get("had_underlying_opportunity")
            )
            if not orb_u_confirms and entry_u > 0 and current_underlying > 0:
                u_thr = float(getattr(self.config, "orb_underlying_opportunity_pct", 0.0025))
                if str(st.option_side or "").lower() == "put":
                    orb_u_confirms = bool(float(u_move_pct_eval) >= float(u_thr) - 1e-15)
                else:
                    orb_u_confirms = bool(float(u_move_pct_eval) >= float(u_thr) - 1e-15)
            allow_hwm = _premium_hwm_allow_orb_single(
                self.config,
                prem_src,
                detail,
                prem=float(prem or 0.0),
                underlying_move_confirms=bool(orb_u_confirms),
                synthetic_active=bool(detail.get("synthetic_pricing_active")),
                entry_premium=float(st.entry_premium or 0.0),
            )
        if premium_jump_this_tick:
            allow_hwm = False
        prev_ps_life = str(osnap.get("_lifecycle_prev_premium_source") or "")
        prev_mq_life = str(osnap.get("_lifecycle_prev_mark_quality") or "")
        mq_tick = str(osnap.get("mark_quality") or "")
        pt_tick = str(detail.get("price_source_telemetry") or osnap.get("option_price_source_telemetry") or "")
        if prem_src != prev_ps_life or mq_tick != prev_mq_life:
            log.info(
                "OPTION_PREMIUM_SOURCE_TRANSITION | trade_id=%s | symbol=%s | strategy_type=%s | "
                "premium_source=%s->%s | mark_quality=%s->%s | price_source_telemetry=%s | allow_hwm=%s | "
                "mark_is_exit_grade=%s | spread_premium_source_quality=%s",
                str(position_id or "-"),
                position_symbol,
                str(st.strategy_type or ""),
                prev_ps_life or "init",
                prem_src,
                prev_mq_life or "init",
                mq_tick,
                pt_tick,
                str(bool(allow_hwm)).lower(),
                str(bool(osnap.get("mark_is_exit_grade"))).lower(),
                str(detail.get("spread_premium_source_quality") or ""),
            )
            osnap["_lifecycle_prev_premium_source"] = prem_src
            osnap["_lifecycle_prev_mark_quality"] = mq_tick
        if mutate_premium_state and allow_hwm:
            if prem > st.premium_hwm:
                st.premium_hwm = prem
                st.last_hwm_at = now
                if prem > st.entry_premium:
                    st.favorable_advance_seen = True
                if (
                    is_trendline_0dte
                    and (not is_spread)
                    and (not _premium_updates_favorable_hwm(prem_src, detail, is_spread=False))
                ):
                    hwm_acc_key = f"hwm_acc|{position_id}|{position_symbol}"
                    if _option_telemetry_log_should_emit(self.config, _option_hwm_skip_log_last, hwm_acc_key):
                        log.info(
                            "OPTION_HWM_UPDATE_ACCEPTED | trade_id=%s | symbol=%s | premium_source=%s | mark_quality=%s | "
                            "reason=tline_trusted_degraded_underlying | underlying_confirms_hwm=%s | premium_hwm=%.6f",
                            str(position_id or "-"),
                            position_symbol,
                            prem_src,
                            str(detail.get("mark_quality") or osnap.get("mark_quality") or ""),
                            str(bool(underlying_confirms_hwm)).lower(),
                            float(st.premium_hwm or 0.0),
                        )
            if is_spread and prem > st.spread_hwm:
                st.spread_hwm = prem
        elif mutate_premium_state and (
            prem > float(st.premium_hwm or 0.0) + 1e-12
            or (is_spread and prem > float(st.spread_hwm or 0.0) + 1e-12)
        ):
            hwm_skip_key = f"hwm_skip|{position_id}|{position_symbol}"
            if _option_telemetry_log_should_emit(self.config, _option_hwm_skip_log_last, hwm_skip_key):
                skip_reason = "non_live_price_source"
                td_elig = False
                if is_trendline_0dte and (not is_spread):
                    td_elig = tline_trusted_degraded_mark(
                        self.config,
                        prem_src=prem_src,
                        mark_quality=str(detail.get("mark_quality") or osnap.get("mark_quality") or ""),
                        quote_age_seconds=detail.get("quote_age_seconds"),
                        synthetic_active=bool(detail.get("synthetic_pricing_active")),
                    )
                    if not bool(underlying_confirms_hwm):
                        skip_reason = "tline_underlying_not_confirming"
                    elif not td_elig:
                        skip_reason = "tline_not_trusted_degraded_mark"
                    else:
                        skip_reason = "tline_bid_ask_sanity_or_positive_mark"
                log.info(
                    "OPTION_HWM_UPDATE_SKIPPED | trade_id=%s | symbol=%s | premium_source=%s | mark_quality=%s | reason=%s | "
                    "underlying_confirms_hwm=%s | trusted_degraded_eligible=%s",
                    str(position_id or "-"),
                    position_symbol,
                    prem_src,
                    str(detail.get("mark_quality") or osnap.get("mark_quality") or ""),
                    skip_reason,
                    str(bool(underlying_confirms_hwm)).lower(),
                    str(bool(td_elig)).lower(),
                )
        if mutate_premium_state and prem < st.premium_lwm:
            st.premium_lwm = prem
        if mutate_premium_state and is_spread and prem < st.spread_lwm:
            st.spread_lwm = prem

        osnap["premium_hwm"] = st.premium_hwm
        osnap["premium_lwm"] = st.premium_lwm

        if is_spread and st.position_type == OptionPositionType.CREDIT_SPREAD.value:
            base = float(st.entry_credit or st.entry_value or st.entry_premium or 0.0)
            pnl_pct = (base - prem) / base if base > 0 else 0.0
        elif is_spread:
            base = float(st.entry_debit or st.entry_value or st.entry_premium or 0.0)
            pnl_pct = (prem - base) / base if base > 0 else 0.0
        else:
            pnl_pct = (prem - st.entry_premium) / st.entry_premium if st.entry_premium > 0 else 0.0
        current_pnl_pct = float(pnl_pct)
        prev_max_pnl_pct = float(getattr(st, "max_pnl_pct", current_pnl_pct))
        if (
            mutate_premium_state
            and premium_jump_this_tick
            and prev_eff
            and float(prev_eff) > 0
            and float(prem or 0.0) < float(prev_eff)
            and float(st.premium_hwm or 0.0) > float(prem or 0.0) * (1.0 + float(self.config.premium_jump_recheck_pct))
        ):
            reset_hwm = max(float(st.entry_premium or 0.0), float(prem or 0.0))
            log.info(
                "OPTION_HWM_SPIKE_RESET | trade_id=%s | symbol=%s | reason=downward_premium_jump | "
                "prev_hwm=%.6f | reset_hwm=%.6f | prev_max_pnl_pct=%.4f | reset_max_pnl_pct=%.4f",
                str(position_id or "-"),
                position_symbol,
                float(st.premium_hwm or 0.0),
                float(reset_hwm),
                float(st.max_pnl_pct or 0.0),
                float(current_pnl_pct),
            )
            st.premium_hwm = float(reset_hwm)
            st.max_pnl_pct = float(current_pnl_pct)
            st.premium_jump_detected = False
            osnap["premium_jump_detected"] = False
            prev_max_pnl_pct = float(st.max_pnl_pct)
        if mutate_premium_state and allow_hwm:
            st.max_pnl_pct = max(prev_max_pnl_pct, current_pnl_pct)
            from modules.po_mfe20_snapshot import mark_po_20p_due
            mark_po_20p_due(osnap, prev_max_pnl_pct, st.max_pnl_pct)
            if st.max_pnl_pct > prev_max_pnl_pct + 1e-9:
                osnap["hwm_timestamp"] = now.isoformat()
                log.info(
                    "OPTION_MAX_PNL_UPDATE | symbol=%s | trade_id=%s | current_pnl_pct=%.4f | max_pnl_pct=%.4f",
                    position_symbol,
                    position_id,
                    current_pnl_pct,
                    st.max_pnl_pct,
                )
                log.info(
                    "OPTION_MFE_UPDATE | trade_id=%s | symbol=%s | premium_source=%s | allow_hwm=%s | "
                    "current_pnl_pct=%.4f | max_pnl_pct=%.4f | mark_quality=%s | mark_is_exit_grade=%s",
                    str(position_id or "-"),
                    position_symbol,
                    prem_src,
                    str(bool(allow_hwm)).lower(),
                    float(current_pnl_pct),
                    float(st.max_pnl_pct),
                    str(osnap.get("mark_quality") or ""),
                    str(bool(osnap.get("mark_is_exit_grade"))).lower(),
                )
            if (not st.had_opportunity) and st.max_pnl_pct >= float(
                self.config.dte_single_mfe_trail_trigger_pct
            ):
                st.had_opportunity = True
                osnap["had_opportunity"] = True
                osnap["mfe_armed_at"] = now.isoformat()
                osnap["had_opportunity_armed_at"] = now.isoformat()
                osnap["hwm_timestamp"] = now.isoformat()
                log.info(
                    "OPTION_OPPORTUNITY_DETECTED | symbol=%s | trade_id=%s | max_pnl_pct=%.4f",
                    position_symbol,
                    position_id,
                    st.max_pnl_pct,
                )
                try:
                    from modules.exit_observation_telemetry import (
                        log_mfe_decision,
                        mfe_decision_fields,
                    )

                    log_mfe_decision(
                        "MFE_ARMED",
                        mfe_decision_fields(
                            trade_id=str(position_id or ""),
                            current_bid=float(detail.get("option_bid") or prem or 0.0) or None,
                            entry_premium=float(st.entry_premium or 0.0) or None,
                            hwm_bid=float(st.premium_hwm or 0.0) or None,
                            hwm_pnl_pct=float(st.max_pnl_pct or 0.0),
                            giveback_exit_pct=float(
                                getattr(self.config, "dte_single_mfe_trail_exit_pct", 0.10)
                                or 0.10
                            ),
                            current_drawdown_pp=0.0,
                            quote_age=detail.get("quote_age_seconds"),
                            quote_source=str(prem_src or ""),
                            confirmation_count=0,
                        ),
                    )
                except Exception:
                    pass
        elif bool(detail.get("absurd_live_premium_rejected")) and (not is_spread):
            mod_p = float(detail.get("modeled_premium") or 0.0)
            ep_m = float(st.entry_premium or 0.0)
            if mod_p > ep_m > 0:
                u_confirms = bool(underlying_confirms_hwm)
                if is_orb_0dte and not u_confirms:
                    u_confirms = bool(getattr(st, "had_underlying_opportunity", False)) or bool(
                        osnap.get("had_underlying_opportunity")
                    )
                    if not u_confirms and entry_u > 0 and current_underlying > 0:
                        u_thr = float(getattr(self.config, "orb_underlying_opportunity_pct", 0.0025))
                        u_confirms = bool(float(u_move_pct_eval) >= float(u_thr) - 1e-15)
                if u_confirms:
                    modeled_pnl = (mod_p - ep_m) / ep_m
                    if modeled_pnl > float(st.max_pnl_pct or 0.0) + 1e-9:
                        st.max_pnl_pct = float(modeled_pnl)
                        st.premium_hwm = max(float(st.premium_hwm or 0.0), mod_p)
                        st.favorable_advance_seen = True
                        osnap["modeled_mfe_fallback"] = True
                        if (not st.had_opportunity) and st.max_pnl_pct >= float(
                            self.config.dte_single_mfe_trail_trigger_pct
                        ):
                            st.had_opportunity = True
                            osnap["had_opportunity"] = True
                        log.info(
                            "OPTION_MFE_MODELED_FALLBACK | trade_id=%s | symbol=%s | modeled_premium=%.6f | "
                            "modeled_pnl_pct=%.4f | max_pnl_pct=%.4f | absurd_raw=%.6f",
                            str(position_id or "-"),
                            position_symbol,
                            mod_p,
                            float(modeled_pnl),
                            float(st.max_pnl_pct),
                            float(detail.get("absurd_live_premium_raw") or 0.0),
                        )
        osnap["current_pnl_pct"] = current_pnl_pct
        _prev_osnap_mfe = osnap.get("max_pnl_pct")
        osnap["max_pnl_pct"] = st.max_pnl_pct
        try:
            from modules.po_mfe20_snapshot import mark_po_20p_due

            mark_po_20p_due(osnap, _prev_osnap_mfe, st.max_pnl_pct)
        except Exception:
            pass
        osnap["had_opportunity"] = bool(getattr(st, "had_opportunity", False))
        try:
            from modules.stealth_restart_gap import (
                apply_restart_gap_hold,
                giveback_drawdown_pct,
                trail_hwm_for_px2,
            )
        except Exception:
            from stealth_restart_gap import (  # type: ignore
                apply_restart_gap_hold,
                giveback_drawdown_pct,
                trail_hwm_for_px2,
            )
        now_epoch = time.time()
        if apply_restart_gap_hold(
            osnap,
            now_epoch=now_epoch,
            current_pnl_pct=float(current_pnl_pct or 0.0),
            current_underlying=float(current_underlying or 0.0),
            watchdog_gap_seconds=float(osnap.get("watchdog_gap_seconds") or 0.0),
        ):
            log.warning(
                "OPTION_STEALTH_RESTART_GAP_HOLD | trade_id=%s | symbol=%s | "
                "session_hwm_pct=%.4f | current_pnl_pct=%.4f | gap_seconds=%.1f | "
                "action=rebase_trail_keep_session_max",
                str(position_id or "-"),
                position_symbol,
                float(osnap.get("restart_gap_session_hwm_pct") or st.max_pnl_pct or 0.0),
                float(current_pnl_pct or 0.0),
                float(osnap.get("restart_gap_seconds") or 0.0),
            )
        osnap["last_monitor_tick_epoch"] = float(now_epoch)
        drawdown_from_max_pct = max(0.0, float(st.max_pnl_pct) - float(current_pnl_pct))
        hwm_age_seconds = max(
            0.0,
            (now - self._normalize_dt(st.last_hwm_at)).total_seconds() if st.last_hwm_at else 0.0,
        )
        log.info(
            "OPTION_STEALTH_PREMIUM_PNL_AUDIT | symbol=%s | trade_id=%s | position_type=%s | premium_source=%s | entry_premium=%.6f | "
            "current_effective_premium=%.6f | current_pnl_pct=%.4f | max_pnl_pct=%.4f | premium_hwm=%.6f",
            position_symbol,
            position_id,
            str(st.position_type or ""),
            prem_src,
            float(st.entry_premium or 0.0),
            float(prem or 0.0),
            float(current_pnl_pct),
            float(st.max_pnl_pct),
            float(st.premium_hwm or 0.0),
        )
        log.info(
            "OPTIONS_STEALTH_PREMIUM_STATE | trade_id=%s | symbol=%s | source_path=%s | position_type=%s | entry_archetype=%s | "
            "current_pnl_pct=%.4f | max_pnl_pct=%.4f | drawdown_from_max_pct=%.4f | be_active=%s | "
            "trailing_active=%s | protection_floor_pct=%.4f | trailing_distance_pct=%.4f | premium_source=%s | quote_age_seconds=%s | "
            "degraded_mode=%s | elapsed_seconds=%.1f | high_watermark_age_seconds=%.1f",
            position_id,
            position_symbol,
            profile.source_path,
            str(st.position_type or ""),
            profile.entry_archetype,
            float(current_pnl_pct),
            float(st.max_pnl_pct),
            float(drawdown_from_max_pct),
            str(bool(st.breakeven_floor is not None)).lower(),
            str(bool(st.trailing_activated_at is not None)).lower(),
            float(
                ((float(st.breakeven_floor) / float(st.entry_premium)) - 1.0)
                if st.breakeven_floor is not None and float(st.entry_premium or 0.0) > 0
                else 0.0
            ),
            float(st.last_trail_pct or profile.trail_distance_pct or 0.0),
            prem_src,
            detail.get("quote_age_seconds"),
            str(bool(osnap.get("degraded_data_active"))).lower(),
            float(max(0.0, (now - self._normalize_dt(st.opened_at)).total_seconds())),
            float(hwm_age_seconds),
        )
        last_eval_raw = osnap.get("_last_eval_ts")
        seconds_since_last_tick = 0.0
        if isinstance(last_eval_raw, str):
            try:
                seconds_since_last_tick = max(
                    0.0,
                    (now - self._normalize_dt(datetime.fromisoformat(str(last_eval_raw)))).total_seconds(),
                )
            except Exception:
                seconds_since_last_tick = 0.0
        lc_key = f"{str(position_id or '-')}:{position_symbol}"
        if _lifecycle_tick_should_emit(_option_premium_lifecycle_tick_last, f"cadence:{lc_key}"):
            log.info(
                "OPTIONS_STEALTH_MONITOR_CADENCE | trade_id=%s | symbol=%s | seconds_since_last_tick=%.3f | premium_source=%s | "
                "quote_age_seconds=%s | current_pnl_pct=%.4f | max_pnl_pct=%.4f",
                position_id,
                position_symbol,
                float(seconds_since_last_tick),
                prem_src,
                detail.get("quote_age_seconds"),
                float(current_pnl_pct),
                float(st.max_pnl_pct),
            )
        if _lifecycle_tick_should_emit(_option_premium_lifecycle_tick_last, lc_key):
            spq = str(detail.get("spread_premium_source_quality") or osnap.get("spread_premium_source_quality") or "")
            log.info(
                "OPTION_PREMIUM_MARK_UPDATE | trade_id=%s | symbol=%s | strategy_type=%s | ts=%s | "
                "effective_premium=%.6f | premium_source=%s | mark_quality=%s | mark_is_exit_grade=%s | "
                "good_mark_tick_count=%d | timeout_clock_frozen=%s | allow_hwm=%s | "
                "current_pnl_pct=%.4f | max_pnl_pct=%.4f | drawdown_from_max_pct=%.4f | premium_hwm=%.6f | "
                "entry_ref=%.6f | quote_age_seconds=%.1f | long_leg_age=%.1f | short_leg_age=%.1f | "
                "degraded_data_active=%s | spread_premium_source_quality=%s | price_source_telemetry=%s",
                str(position_id or "-"),
                position_symbol,
                str(st.strategy_type or ""),
                now.isoformat(),
                float(prem or 0.0),
                prem_src,
                str(osnap.get("mark_quality") or ""),
                str(bool(osnap.get("mark_is_exit_grade"))).lower(),
                int(st.good_mark_tick_count),
                str(bool(st.timeout_clock_frozen or osnap.get("timeout_clock_frozen"))).lower(),
                str(bool(allow_hwm)).lower(),
                float(current_pnl_pct),
                float(st.max_pnl_pct),
                float(drawdown_from_max_pct),
                float(st.premium_hwm or 0.0),
                float(st.entry_debit or st.entry_value or st.entry_premium or 0.0),
                float(quote_age_seconds),
                float(osnap.get("long_leg_quote_age_seconds") or 0.0),
                float(osnap.get("short_leg_quote_age_seconds") or 0.0),
                str(bool(osnap.get("degraded_data_active"))).lower(),
                spq,
                str(detail.get("price_source_telemetry") or osnap.get("option_price_source_telemetry") or ""),
            )
        held_min = (now - self._normalize_dt(st.opened_at)).total_seconds() / 60.0
        elapsed_seconds = max(0.0, held_min * 60.0)
        if is_orb_0dte or is_trendline_0dte:
            prior_behavior = str(getattr(st, "trade_behavior", "unknown") or "unknown").lower()
            next_behavior = prior_behavior
            if float(st.max_pnl_pct) >= 0.08 and elapsed_seconds <= 180.0:
                next_behavior = "impulse"
            elif elapsed_seconds >= 240.0:
                next_behavior = "trend"
            if next_behavior not in {"unknown", "impulse", "trend"}:
                next_behavior = "unknown"
            if next_behavior != prior_behavior:
                st.trade_behavior = next_behavior
                log.info(
                    "OPTION_TRADE_BEHAVIOR_CLASSIFIED | symbol=%s | trade_behavior=%s | max_pnl_pct=%.4f | elapsed_seconds=%.1f",
                    position_symbol,
                    st.trade_behavior,
                    float(st.max_pnl_pct),
                    float(elapsed_seconds),
                )
        osnap["trade_behavior"] = str(getattr(st, "trade_behavior", "unknown") or "unknown")

        structure_ok = True
        held_seconds = held_min * 60.0
        is_0dte_single_eval = bool(
            is_0dte_single and (is_orb_0dte or is_trendline_0dte) and (not is_spread)
        )

        def _dte_exit_min_hold_blocks(exit_kind: str) -> bool:
            if not is_0dte_single_eval:
                return False
            return _dte_single_exit_min_hold_blocks(
                self.config, exit_kind=exit_kind, held_seconds=held_seconds
            )

        weak_exit_min_hold_blocks = _dte_exit_min_hold_blocks("no_progress")
        if weak_exit_min_hold_blocks and is_0dte_single_eval and not osnap.get("_dte_min_hold_logged"):
            osnap["_dte_min_hold_logged"] = True
            log.info(
                "OPTION_0DTE_MIN_HOLD_ACTIVE | trade_id=%s | held_seconds=%.1f | min_hold_seconds=%.1f | "
                "blocking_exits=no_progress,time_exit",
                position_id,
                held_seconds,
                float(self.config.dte_min_hold_seconds),
            )
        self._merge_live_snapshot(osnap, st, prem, prem_src, detail, current_underlying, pnl_pct, structure_ok)
        try:
            from modules.options_snapshot_fields import stamp_live_owned_greeks

            prem_diag = getattr(premium, "diagnostics", None) if premium is not None else None
            stamp_live_owned_greeks(
                osnap,
                option_quote,
                detail,
                prem_diag,
                overwrite=True,
            )
            stamp_live_owned_greeks(detail, option_quote, osnap, overwrite=False)
        except Exception:
            pass
        if is_spread:
            osnap["spread_current_value"] = prem

        if not osnap.get("_telemetry_snapshot_logged"):
            wanted = (
                "position_type",
                "leg_count",
                "legs",
                "entry_value",
                "entry_debit",
                "entry_credit",
                "current_effective_premium",
                "last_premium_source",
                "premium_hwm",
                "premium_lwm",
                "spread_current_value",
                "spread_entry_value",
                "spread_high_water_mark",
                "spread_low_water_mark",
                "spread_premium_source_quality",
                "spread_monitoring_mode",
                "structure_still_valid",
                "strategy_type",
                "source_path",
            )
            present = [k for k in wanted if osnap.get(k) is not None and osnap.get(k) != ""]
            log.info(
                "OPTIONS_STEALTH | stage=telemetry_snapshot | trade_id=%s | fields_present=%s",
                position_id,
                ",".join(present),
            )
            osnap["_telemetry_snapshot_logged"] = True

        telemetry: Dict[str, Any] = {
            "current_premium_est": prem,
            "premium_source": prem_src,
            "pnl_pct": pnl_pct,
            "mode": st.mode.value,
            "structure_still_valid": structure_ok,
        }
        synthetic_active = bool(detail.get("synthetic_pricing_active"))
        non_live_premium = str(px_telem_g or "").lower() != "live"
        telemetry["synthetic_pricing_active"] = synthetic_active
        telemetry["non_live_premium"] = non_live_premium
        telemetry["price_source_telemetry"] = px_telem_g
        if is_0dte_single:
            # Unified ORB + Trendline 0DTE: OPTION_MAX_DEGRADED_SECONDS (~90s). Legacy
            # TRENDLINE_OPTION_NO_DATA_* 180/300 + diagnostic hold no longer apply to singles.
            max_deg = max(1.0, float(self.config.option_max_degraded_seconds))
            snl = float(osnap.get("seconds_since_live_option_quote") or 0.0)
            no_data_force_threshold = float(max_deg)
            no_data_grace_threshold = 0.0
            if px_telem_g != "live":
                mark_exit_grade = bool(osnap.get("mark_is_exit_grade"))
                if snl >= no_data_force_threshold:
                    log.info(
                        "OPTION_EXIT_DECISION_AUDIT | trade_id=%s | symbol=%s | strategy_type=%s | position_type=%s | option_side=%s | "
                        "candidate_exit_reason=no_data | final_exit_reason=no_data | exit_allowed=true | blocked_reason=none | "
                        "mark_quality=%s | mark_is_exit_grade=%s | quote_age_seconds=%s | good_mark_tick_count=%d | timeout_clock_frozen=%s | seconds_since_live=%.1f",
                        str(position_id or ""),
                        position_symbol,
                        str(st.strategy_type or ""),
                        str(st.position_type or ""),
                        str(st.option_side or ""),
                        str(osnap.get("mark_quality") or "unknown"),
                        str(bool(mark_exit_grade)).lower(),
                        osnap.get("quote_age_seconds"),
                        int(st.good_mark_tick_count),
                        str(bool(st.timeout_clock_frozen or osnap.get("timeout_clock_frozen"))).lower(),
                        float(snl),
                    )
                    log.warning(
                        "OPTION_FORCE_EXIT_NO_DATA | trade_id=%s | symbol=%s | seconds_since_live=%.1f | limit_sec=%.1f | premium_source=%s",
                        str(position_id or "-"),
                        position_symbol,
                        snl,
                        no_data_force_threshold,
                        prem_src,
                    )
                    # Prefer usable bid / last_valid over a poisoned collapse print when forcing out.
                    no_data_exit_prem = float(prem or 0.0)
                    try:
                        ep_nd = float(st.entry_premium or st.entry_value or 0.0)
                    except (TypeError, ValueError):
                        ep_nd = 0.0
                    hold_cands = []  # type: list
                    for _k in ("option_bid", "bid"):
                        try:
                            _bv = float(detail.get(_k) or 0.0)
                        except (TypeError, ValueError):
                            _bv = 0.0
                        if _bv > 0:
                            hold_cands.append(_bv)
                    try:
                        _lv = float(osnap.get("last_valid_option_price") or 0.0)
                    except (TypeError, ValueError):
                        _lv = 0.0
                    if _lv > 0:
                        hold_cands.append(_lv)
                    try:
                        _hwm = float(st.premium_hwm or 0.0)
                    except (TypeError, ValueError):
                        _hwm = 0.0
                    collapse_prem = bool(
                        ep_nd >= 0.05
                        and no_data_exit_prem + 1e-12
                        < ep_nd
                        * max(
                            0.01,
                            float(
                                getattr(
                                    self.config,
                                    "premium_absurd_collapse_mult_from_entry",
                                    0.08,
                                )
                                or 0.08
                            ),
                        )
                    )
                    if hold_cands and (
                        collapse_prem
                        or bool(detail.get("absurd_live_premium_rejected"))
                        or no_data_exit_prem <= 0.01
                    ):
                        best_hold = max(hold_cands)
                        if best_hold > no_data_exit_prem + 1e-12:
                            no_data_exit_prem = float(best_hold)
                            detail["no_data_exit_premium_rescued"] = True
                            detail["no_data_exit_premium_rescue_from"] = round(float(prem or 0.0), 6)
                    # Never settle no_data above HWM when we only have stale holds.
                    if _hwm > 0 and no_data_exit_prem > _hwm + 1e-12:
                        no_data_exit_prem = float(_hwm)
                    diag = self._build_exit_diagnostics(
                        position_id=position_id,
                        position_symbol=position_symbol,
                        block=block,
                        st=st,
                        osnap=osnap,
                        exit_premium=no_data_exit_prem,
                        exit_reason=XER.NO_DATA.value,
                        now=now,
                        prem_detail=detail,
                        premium_source_at_exit=prem_src,
                    )
                    diag["seconds_since_live_option_quote"] = snl
                    diag["option_max_degraded_seconds"] = no_data_force_threshold
                    diag["price_source_telemetry"] = px_telem_g
                    diag["0dte_no_data_grace_seconds"] = no_data_grace_threshold
                    diag["no_data_exit_premium"] = float(no_data_exit_prem)
                    self._record_exit(XER.NO_DATA.value, pnl_pct, prem_src, diag)
                    telemetry["exit_diagnostics"] = diag
                    return self._finalize_exit_return(
                        no_data_exit_prem, XER.NO_DATA.value, telemetry, st=st, osnap=osnap, detail=detail
                    )
        if is_orb_0dte:
            _log_info_throttled(
                f"lifecycle|{position_id}",
                "OPTION_LIFECYCLE_TICK | trade_id=%s | symbol=%s | strategy_type=orb_0dte | trade_behavior=%s | "
                "current_premium=%.6f | premium_source=%s | current_pnl_pct=%.4f | max_pnl_pct=%.4f | "
                "had_opportunity=%s | mode=%s",
                position_id,
                position_symbol,
                str(getattr(st, "trade_behavior", "unknown") or "unknown"),
                float(prem or 0.0),
                prem_src,
                float(current_pnl_pct),
                float(st.max_pnl_pct),
                str(bool(st.had_opportunity)).lower(),
                st.mode.value,
            )


        prev_eval_ts = osnap.get("_last_eval_ts")
        if isinstance(prev_eval_ts, str):
            try:
                prev_dt = self._normalize_dt(datetime.fromisoformat(prev_eval_ts))
                seconds_stale = max(0.0, (now - prev_dt).total_seconds())
                if seconds_stale > float(self.config.max_stale_seconds):
                    st.stale_data_detected = True
                    osnap["stale_data_detected"] = True
                    _log_info_throttled(
                        f"stale|{position_symbol}",
                        "TRENDLINE_PIPELINE | stage=option_monitor_stale | symbol=%s | seconds_stale=%.3f",
                        position_symbol,
                        seconds_stale,
                    )
                    if self.config.force_reeval_on_premium_jump:
                        _log_info_throttled(
                            f"reeval|{position_symbol}|stale",
                            "TRENDLINE_PIPELINE | stage=option_forced_reeval | symbol=%s | reason=stale_monitor_data",
                            position_symbol,
                        )
            except Exception:
                pass
        osnap["_last_eval_ts"] = now.isoformat()

        def _underlying_signal_confirmed_exit(signal_name: str) -> bool:
            confirmed = bool(st.had_opportunity) and (float(current_pnl_pct) < float(st.max_pnl_pct))
            if confirmed:
                log.info(
                    "OPTION_UNDERLYING_SIGNAL_CONFIRMED_EXIT | symbol=%s | trade_id=%s | signal=%s | current_pnl_pct=%.4f | max_pnl_pct=%.4f | had_opportunity=%s",
                    position_symbol,
                    position_id,
                    signal_name,
                    float(current_pnl_pct),
                    float(st.max_pnl_pct),
                    str(bool(st.had_opportunity)).lower(),
                )
            else:
                log.info(
                    "OPTION_STEALTH_EXIT_PRIORITY_AUDIT | symbol=%s | trade_id=%s | underlying_signal=%s | candidate=true | confirmed=false | "
                    "had_opportunity=%s | in_drawdown=%s | current_pnl_pct=%.4f | max_pnl_pct=%.4f",
                    position_symbol,
                    position_id,
                    signal_name,
                    str(bool(st.had_opportunity)).lower(),
                    str(bool(float(current_pnl_pct) < float(st.max_pnl_pct))).lower(),
                    float(current_pnl_pct),
                    float(st.max_pnl_pct),
                )
            return confirmed

        degraded_mode = bool(osnap.get("degraded_data_active"))
        degraded_min = 0.0
        degraded_reason = ""
        degraded_tighten_active = False
        if degraded_mode:
            degraded_since = osnap.get("degraded_data_since")
            if isinstance(degraded_since, str):
                try:
                    degraded_dt = self._normalize_dt(datetime.fromisoformat(degraded_since))
                    degraded_min = max(0.0, (now - degraded_dt).total_seconds() / 60.0)
                except Exception:
                    degraded_min = 0.0
            telemetry["degraded_data_active"] = True
            telemetry["degraded_data_minutes"] = degraded_min
            telemetry["degraded_data_source"] = osnap.get("degraded_data_source")
            degraded_reason = str(osnap.get("degraded_data_source") or prem_src or "unknown_degraded_source")
            # Availability-based safety: continue managing with fallback data, but force exit on true outage.
            if not has_live_data and not has_fallback_data:
                if bool(osnap.get("open_grace_active")):
                    telemetry["open_grace_active"] = True
                    telemetry["open_grace_attempts"] = int(osnap.get("open_grace_attempts", 0) or 0)
                    log.warning(
                        "OPTIONS_STEALTH | stage=degraded_no_fallback_but_open_grace_hold | symbol=%s | trade_id=%s | source=%s",
                        position_symbol,
                        position_id,
                        str(osnap.get("degraded_data_source") or prem_src or "unknown"),
                    )
                    return None
                log.warning(
                    "OPTIONS_DATA_OUTAGE | trade_id=%s | symbol=%s | reason=no_live_and_no_fallback | degraded_min=%.2f",
                    position_id,
                    position_symbol,
                    degraded_min,
                )
                log.warning(
                    "OPTIONS_STEALTH | stage=degraded_data_no_fallback_exit | symbol=%s | trade_id=%s | degraded_min=%.2f | source=%s",
                    position_symbol,
                    position_id,
                    degraded_min,
                    osnap.get("degraded_data_source"),
                )
                diag = self._build_exit_diagnostics(
                    position_id=position_id,
                    position_symbol=position_symbol,
                    block=block,
                    st=st,
                    osnap=osnap,
                    exit_premium=prem,
                    exit_reason=XER.DEGRADED_DATA.value,
                    now=now,
                    prem_detail=detail,
                    premium_source_at_exit=prem_src,
                )
                diag["degraded_data_minutes"] = degraded_min
                diag["degraded_data_source"] = osnap.get("degraded_data_source")
                if self.config.require_live_quotes and str(osnap.get("degraded_data_source") or "") == "live_quote_unavailable":
                    _emit_option_degraded_live_quote_log(
                        self.config,
                        symbol=position_symbol,
                        trade_id=str(position_id or ""),
                        reason=str(osnap.get("last_live_degraded_reason") or "unknown"),
                        action_taken="exit",
                        dedupe_key=f"exitnf|{position_id}|{position_symbol}",
                        force=True,
                    )
                diag["degraded_detail"] = "no_fallback"
                self._record_exit(XER.DEGRADED_DATA.value, pnl_pct, prem_src, diag)
                telemetry["exit_diagnostics"] = diag
                return self._finalize_exit_return(prem, XER.DEGRADED_DATA.value, telemetry, st=st, osnap=osnap, detail=detail)

            last_any_data_ts = osnap.get("last_any_data_ts")
            outage_seconds = 0.0
            if isinstance(last_any_data_ts, str):
                try:
                    last_any_dt = self._normalize_dt(datetime.fromisoformat(last_any_data_ts))
                    outage_seconds = max(0.0, (now - last_any_dt).total_seconds())
                except Exception:
                    outage_seconds = 0.0
            max_outage_seconds = max(10.0, float(self.config.no_data_outage_seconds))
            telemetry["degraded_outage_seconds"] = outage_seconds
            telemetry["degraded_outage_limit_seconds"] = max_outage_seconds
            degraded_duration_seconds = 0.0
            if isinstance(osnap.get("degraded_start_timestamp"), str):
                try:
                    degraded_dt = self._normalize_dt(datetime.fromisoformat(str(osnap.get("degraded_start_timestamp"))))
                    degraded_duration_seconds = max(0.0, (now - degraded_dt).total_seconds())
                except Exception:
                    degraded_duration_seconds = 0.0
            degraded_min_duration = max(0.0, float(self.config.tline_degraded_exit_min_duration_seconds))
            degraded_exit_allowed = (outage_seconds >= max_outage_seconds) and (degraded_duration_seconds >= degraded_min_duration)
            log.info(
                "TRENDLINE_DEGRADED_EXIT_CHECK | trade_id=%s | symbol=%s | degraded_duration=%.1f | threshold=%.1f | exit_allowed=%s",
                position_id,
                position_symbol,
                degraded_duration_seconds,
                degraded_min_duration,
                str(bool(degraded_exit_allowed)).lower(),
            )
            if degraded_exit_allowed and (not is_0dte_single):
                log.warning(
                    "OPTIONS_DATA_OUTAGE | trade_id=%s | symbol=%s | reason=outage_seconds_exceeded | outage_sec=%.1f | limit_sec=%.1f",
                    position_id,
                    position_symbol,
                    outage_seconds,
                    max_outage_seconds,
                )
                log.warning(
                    "OPTIONS_STEALTH | stage=degraded_data_outage_exit | symbol=%s | trade_id=%s | outage_sec=%.1f | limit_sec=%.1f | source=%s",
                    position_symbol,
                    position_id,
                    outage_seconds,
                    max_outage_seconds,
                    osnap.get("degraded_data_source"),
                )
                diag = self._build_exit_diagnostics(
                    position_id=position_id,
                    position_symbol=position_symbol,
                    block=block,
                    st=st,
                    osnap=osnap,
                    exit_premium=prem,
                    exit_reason=XER.DEGRADED_DATA.value,
                    now=now,
                    prem_detail=detail,
                    premium_source_at_exit=prem_src,
                )
                diag["degraded_data_minutes"] = degraded_min
                diag["degraded_data_source"] = osnap.get("degraded_data_source")
                diag["outage_seconds"] = outage_seconds
                diag["outage_limit_seconds"] = max_outage_seconds
                diag["degraded_duration_seconds"] = degraded_duration_seconds
                if self.config.require_live_quotes and str(osnap.get("degraded_data_source") or "") == "live_quote_unavailable":
                    _emit_option_degraded_live_quote_log(
                        self.config,
                        symbol=position_symbol,
                        trade_id=str(position_id or ""),
                        reason=str(osnap.get("last_live_degraded_reason") or "unknown"),
                        action_taken="exit",
                        dedupe_key=f"exitout|{position_id}|{position_symbol}",
                        force=True,
                    )
                diag["degraded_detail"] = "outage"
                self._record_exit(XER.DEGRADED_DATA.value, pnl_pct, prem_src, diag)
                telemetry["exit_diagnostics"] = diag
                return self._finalize_exit_return(prem, XER.DEGRADED_DATA.value, telemetry, st=st, osnap=osnap, detail=detail)
            tighten_after_min = max(0.0, float(self.config.degraded_tighten_after_minutes))
            degraded_tighten_active = degraded_min >= tighten_after_min and tighten_after_min > 0.0
        else:
            telemetry["degraded_data_active"] = False

        log.info(
            "OPTIONS_MONITOR_MODE | trade_id=%s | symbol=%s | quote_source=%s | exit_logic_active=true | reason=%s",
            position_id,
            position_symbol,
            prem_src,
            degraded_reason if degraded_mode else "none",
        )
        if is_0dte_single_eval:
            from modules.path_v2_exit import (
                evaluate_path_v2_tick,
                live_path_v2_pret_from_tick,
                path_v2_architecture_active,
            )

            if path_v2_architecture_active():
                from modules.po_lifecycle_snapshots import (
                    arm_lifecycle_from_live,
                    freeze_due_lifecycle_tech,
                )

                # Research capture only. Does not change the Path V2 decision.
                peak_mfe = current_pnl_pct
                try:
                    peak_mfe = max(
                        float(current_pnl_pct or 0.0),
                        float(getattr(st, "max_pnl_pct", 0.0) or 0.0),
                        float(osnap.get("max_pnl_pct") or 0.0),
                    )
                except (TypeError, ValueError):
                    peak_mfe = current_pnl_pct
                from modules.path_v2_exit import load_path_v2_config as _load_save20_cfg
                from modules.po_mfe20_snapshot import capture_po_20p_first_touch

                _save20_cfg = _load_save20_cfg()
                capture_po_20p_first_touch(
                    getattr(self, "system", None) or getattr(self, "_system", None),
                    osnap if isinstance(osnap, dict) else {},
                    bid_pret=current_pnl_pct,
                    now_utc=now,
                    symbol=str(position_symbol or ""),
                    max_age_s=float(_save20_cfg.twenty_percent_save_max_pve3_age_seconds),
                    # Snapshot only. A 3s hub refresh here serializes the
                    # 20–38 name evaluate loop; bars come from the tick prime.
                    allow_sync_refresh=False,
                    refresh_timeout_s=float(_save20_cfg.twenty_percent_save_sync_timeout_s),
                )
                arm_lifecycle_from_live(
                    osnap,
                    current_pnl_pct,
                    peak_mfe,
                    now=now,
                    opened_at=getattr(st, "opened_at", None),
                    trade_id=position_id,
                    # Pullback ladder: pre-Path-V2 call only tracks the bid high H.
                    pb_track_only=True,
                )
                freeze_due_lifecycle_tech(
                    getattr(self, "system", None) or getattr(self, "_system", None),
                    osnap if isinstance(osnap, dict) else {},
                    now_utc=now,
                    symbol=str(position_symbol or ""),
                )
                # REPLACE: no_data / degraded already returned above.
                # end_of_day_exit stays on flatten_all_paths_for_eod_scheduler.
                # mfe_trail (−10pp) runs here after 12:30 PT.
                # Bypass EARLY_BE / PX2 / PX3 / the legacy premium stack.
                # 20% Save reads live session PVE, not the entry stamp on osnap.
                if isinstance(osnap, dict) and isinstance(position_metadata, dict):
                    pst = position_metadata.get("path_session_tech")
                    if isinstance(pst, dict):
                        try:
                            from profit_optimizer.path_chain_skew import (
                                overlay_live_session_tech,
                            )

                            overlay_live_session_tech(osnap, pst)
                        except Exception:
                            for _k, _v in pst.items():
                                if _v is not None:
                                    osnap[_k] = _v
                # STEP 0: no this-tick bid → pret None (HOLD, do not update peak).
                # Never feed current_pnl_pct / chain mark as pret; that locked
                # IBIT/SLV/MU into mature on a non-executable print.
                _this_bid = None
                _this_ask = None
                if isinstance(detail, dict):
                    _this_bid = detail.get("option_bid")
                    _this_ask = detail.get("option_ask")
                path_pret = live_path_v2_pret_from_tick(
                    option_bid=_this_bid,
                    entry_premium=getattr(st, "entry_premium", None),
                    osnap_option_bid=(
                        osnap.get("option_bid") if isinstance(osnap, dict) else None
                    ),
                    current_pnl_pct=current_pnl_pct,
                    mark=(
                        detail.get("mark")
                        if isinstance(detail, dict)
                        else None
                    ),
                )
                if isinstance(osnap, dict):
                    osnap["path_pret"] = path_pret
                path_out = evaluate_path_v2_tick(
                    osnap=osnap,
                    option_side=str(st.option_side or osnap.get("option_side") or ""),
                    entry_underlying=block.get("entry_underlying"),
                    current_underlying=current_underlying,
                    pret=path_pret,
                    opened_at=st.opened_at,
                    now=now,
                    ul_quote_ts=osnap.get("underlying_observation_timestamp"),
                    ul_age_ms=osnap.get("underlying_age_ms"),
                    ul_source=osnap.get("underlying_source"),
                    mfe_trail_active=self._0dte_mfe_trail_with_price_trail(
                        is_0dte_single=True,
                        now=now,
                    ),
                    mfe_trail_trigger_pct=None,
                    mfe_trail_exit_pct=None,
                    theta=(
                        osnap.get("theta")
                        if isinstance(osnap, dict) and osnap.get("theta") is not None
                        else (
                            detail.get("theta")
                            if isinstance(detail, dict) and detail.get("theta") is not None
                            else (
                                option_quote.get("theta")
                                if isinstance(option_quote, dict)
                                else None
                            )
                        )
                    ),
                    option_bid=_this_bid,
                    option_ask=_this_ask,
                    valid_bid_ask_contracts=(
                        osnap.get("valid_bid_ask_contracts")
                        if isinstance(osnap, dict) and osnap.get("valid_bid_ask_contracts") is not None
                        else (
                            block.get("valid_bid_ask_contracts")
                            if isinstance(block, dict)
                            else None
                        )
                    ),
                )
                if isinstance(osnap, dict):
                    pv = osnap.get("path_v2") if isinstance(osnap.get("path_v2"), dict) else {}
                    if pv.get("peak") is not None:
                        osnap["path_v2_peak"] = pv.get("peak")
                    if path_out.exit_reason:
                        osnap["path_v2_reason"] = str(path_out.exit_reason)
                if isinstance(path_out.telemetry, dict):
                    telemetry.update(path_out.telemetry)
                    if isinstance(osnap, dict) and path_out.telemetry.get("prem_vel_10") is not None:
                        osnap["prem_vel_10"] = path_out.telemetry.get("prem_vel_10")
                    try:
                        _post_peak = max(
                            float(path_out.telemetry.get("running_mfe") or 0.0),
                            float(osnap.get("po_lifecycle_peak") or 0.0),
                            float(osnap.get("max_pnl_pct") or 0.0),
                            float(getattr(st, "max_pnl_pct", 0.0) or 0.0),
                        )
                    except (TypeError, ValueError):
                        _post_peak = path_out.telemetry.get("running_mfe")
                    arm_lifecycle_from_live(
                        osnap,
                        path_out.telemetry.get("pret"),
                        _post_peak,
                        now=now,
                        opened_at=getattr(st, "opened_at", None)
                        or osnap.get("po_opened_at")
                        or osnap.get("opened_at"),
                        trade_id=position_id,
                        # Pullback ladder fires on ticks before the exit tick only.
                        exit_tick=bool(path_out.exit_reason),
                    )
                    freeze_due_lifecycle_tech(
                        getattr(self, "system", None) or getattr(self, "_system", None),
                        osnap if isinstance(osnap, dict) else {},
                        now_utc=now,
                        symbol=str(position_symbol or ""),
                    )
                if path_out.exit_reason:
                    log.info(
                        "PATH_V2_EXIT | trade_id=%s | symbol=%s | side=%s | reason=%s | detail=%s | "
                        "pret=%s | running_mfe=%s | stress=%s | frac=%s | vel=%s | age_s=%s | "
                        "mfe_trail=%s | ae_spare=%s",
                        position_id,
                        position_symbol,
                        path_out.telemetry.get("side"),
                        path_out.exit_reason,
                        path_out.detail,
                        path_out.telemetry.get("pret"),
                        path_out.telemetry.get("running_mfe"),
                        path_out.telemetry.get("live_stress"),
                        path_out.telemetry.get("frac_retrace"),
                        path_out.telemetry.get("prem_vel_10"),
                        path_out.telemetry.get("age_seconds"),
                        path_out.telemetry.get("mfe_trail_active"),
                        path_out.telemetry.get("ae_shadow_would_spare"),
                    )
                    return self._finalize_exit_return(
                        prem,
                        path_out.exit_reason,
                        telemetry,
                        st=st,
                        osnap=osnap,
                        detail=detail if isinstance(detail, dict) else {},
                    )
                return None
        try:
            from modules.exit_grade_durability import premium_source_is_live_contract_quote as _live_cq

            _prem_live = bool(_live_cq(prem_src))
        except Exception:
            _ps_l = str(prem_src or "").strip().lower()
            _prem_live = _ps_l in {
                "exact_quote",
                "nearest",
                "cached_quote",
                "chain_strike_match",
                "spread_leg_quotes",
                "spread_mtm_mid_last",
            } or "exact_contract" in _ps_l
        live_non_synthetic_quote = (not synthetic_active) and (
            str(detail.get("price_source_telemetry") or "").strip().lower() == "live"
            or _prem_live
        ) and not bool(detail.get("absurd_live_premium_rejected"))
        tline_trusted_degraded = False
        if (is_trendline_0dte or is_orb_0dte) and (not is_spread):
            tline_trusted_degraded = tline_trusted_degraded_mark(
                self.config,
                prem_src=prem_src,
                mark_quality=str(mark_quality or ""),
                quote_age_seconds=quote_age_seconds,
                synthetic_active=bool(synthetic_active),
            )
        live_or_tline_protection_quote = bool(live_non_synthetic_quote) or bool(tline_trusted_degraded)
        protection_quote_ok = live_non_synthetic_quote
        if (is_trendline_0dte or is_orb_0dte) and (not is_spread):
            protection_quote_ok = live_or_tline_protection_quote
        if (
            is_orb_0dte
            and is_spread
            and bool(osnap.get("trusted_degraded_spread_mark"))
            and bool(getattr(self.config, "orb_degraded_protect_enable", True))
        ):
            protection_quote_ok = bool(live_non_synthetic_quote) or bool(osnap.get("trusted_degraded_spread_mark"))
        dd_trig = float(profile.max_pnl_drawdown_trigger_pct)
        dd_exit = float(profile.max_pnl_drawdown_exit_pct)
        if (
            is_orb_0dte
            and is_spread
            and bool(getattr(st, "had_underlying_opportunity", False))
            and bool(osnap.get("trusted_degraded_spread_mark"))
            and bool(getattr(self.config, "orb_degraded_protect_enable", True))
        ):
            sc = float(getattr(self.config, "orb_degraded_drawdown_tighten_scale", 0.88) or 0.88)
            dd_trig *= sc
            dd_exit *= sc
            osnap["orb_drawdown_thresholds_tightened"] = True
        else:
            osnap["orb_drawdown_thresholds_tightened"] = False

        # Asset stop loss (Aug 21): −0.5% under/over entry underlying for all 0DTE
        # singles (ORB1 / Trendline / ORB2 BO). Active only before premium ARM +30%.
        # Path override BREAKOUT_OPTION_SINGLE_STOP_PCT removed — Shared is the one stop.
        asset_stop_enabled = bool(getattr(self.config, "dte_single_asset_stop_enabled", False))
        asset_stop_pct = float(getattr(self.config, "dte_single_asset_stop_pct", 0.005) or 0.005)
        asset_stop_only_before_tp = bool(
            getattr(self.config, "dte_single_asset_stop_before_arm_trail", True)
        )
        # Always-bank TP30 REMOVED (inferior to PX2 stall stack).
        take_profit_enabled = False
        take_profit_pct = 0.30
        arm_trail_pct = float(getattr(self.config, "dte_single_arm_trail_pct", 0.30) or 0.30)
        if arm_trail_pct <= 0:
            arm_trail_pct = 0.30
        arm_trail_armed = float(st.max_pnl_pct or 0.0) + 1e-12 >= float(arm_trail_pct) or (
            float(current_pnl_pct) + 1e-12 >= float(arm_trail_pct)
        )
        pre_arm_floor_enabled = bool(
            getattr(self.config, "dte_pre_arm_profit_protect_enabled", True)
        )
        pre_arm_trigger_mfe = float(
            getattr(self.config, "dte_pre_arm_profit_protect_trigger_mfe_pct", 0.20) or 0.20
        )
        pre_arm_floor_pct = float(
            getattr(self.config, "dte_pre_arm_profit_protect_floor_pnl_pct", 0.02) or 0.02
        )
        pre_arm_min_current_pnl = float(
            getattr(self.config, "dte_pre_arm_profit_protect_min_current_pnl_pct", -0.10)
            if getattr(self.config, "dte_pre_arm_profit_protect_min_current_pnl_pct", None) is not None
            else -0.10
        )
        pre_arm_min_hold = float(
            getattr(self.config, "dte_pre_arm_profit_protect_min_hold_minutes", 30.0) or 30.0
        )
        pre_arm_min_hwm_age = float(
            getattr(self.config, "dte_pre_arm_profit_protect_min_seconds_since_hwm", 600.0)
            or 600.0
        )
        low_arm_floor_enabled = bool(
            getattr(self.config, "dte_low_arm_profit_protect_enabled", True)
        )
        low_arm_trigger_mfe = float(
            getattr(self.config, "dte_low_arm_profit_protect_trigger_mfe_pct", 0.10) or 0.10
        )
        low_arm_max_mfe = float(
            getattr(self.config, "dte_low_arm_profit_protect_max_mfe_pct", 0.20) or 0.20
        )
        low_arm_floor_pct = float(
            getattr(self.config, "dte_low_arm_profit_protect_floor_pnl_pct", 0.0) or 0.0
        )
        low_arm_min_current_pnl = float(
            getattr(self.config, "dte_low_arm_profit_protect_min_current_pnl_pct", -0.10)
            if getattr(self.config, "dte_low_arm_profit_protect_min_current_pnl_pct", None) is not None
            else -0.10
        )
        low_arm_min_hold = float(
            getattr(self.config, "dte_low_arm_profit_protect_min_hold_minutes", 30.0) or 30.0
        )
        low_arm_min_hwm_age = float(
            getattr(self.config, "dte_low_arm_profit_protect_min_seconds_since_hwm", 600.0)
            or 600.0
        )

        def _apply_pre_arm_profit_protect_floor(candidate_exit_reason: str) -> bool:
            if not pre_arm_profit_protect_should_exit(
                enabled=bool(pre_arm_floor_enabled),
                is_0dte_single=bool(is_0dte_single),
                protection_quote_ok=bool(protection_quote_ok),
                arm_trail_armed=bool(arm_trail_armed),
                candidate_exit_reason=str(candidate_exit_reason or ""),
                max_pnl_pct=float(st.max_pnl_pct or 0.0),
                current_pnl_pct=float(current_pnl_pct),
                held_minutes=float(held_min),
                seconds_since_hwm=float(hwm_age_seconds),
                trigger_mfe_pct=float(pre_arm_trigger_mfe),
                floor_pnl_pct=float(pre_arm_floor_pct),
                min_current_pnl_pct=float(pre_arm_min_current_pnl),
                winner_arm_pct=float(arm_trail_pct),
                min_hold_minutes=float(pre_arm_min_hold),
                min_seconds_since_hwm=float(pre_arm_min_hwm_age),
            ):
                return False
            pre_arm_floor_premium = float(st.entry_premium or st.entry_value or 0.0) * (
                1.0 + float(pre_arm_floor_pct)
            )
            if bool(getattr(self.config, "dte_pre_arm_profit_protect_settle_at_floor", True)):
                osnap["pre_arm_profit_protect_clamp"] = True
                osnap["pre_arm_profit_protect_floor_pnl_pct"] = float(pre_arm_floor_pct)
            osnap["pre_arm_profit_protect_triggered"] = True
            osnap["pre_arm_profit_protect_candidate_exit_reason"] = str(candidate_exit_reason or "")
            osnap["pre_arm_profit_protect_trigger_mfe_pct"] = float(pre_arm_trigger_mfe)
            osnap["pre_arm_profit_protect_min_current_pnl_pct"] = float(pre_arm_min_current_pnl)
            osnap["pre_arm_profit_protect_floor_premium"] = float(pre_arm_floor_premium)
            log.info(
                "OPTION_PRE_ARM_PROFIT_PROTECT_EXIT | trade_id=%s | symbol=%s | "
                "candidate_exit_reason=%s | held_min=%.2f | hwm_age_seconds=%.1f | "
                "current_pnl_pct=%.4f | min_current_pnl_pct=%.4f | max_pnl_pct=%.4f | "
                "trigger_mfe_pct=%.4f | floor_pnl_pct=%.4f | raw_premium=%.4f | "
                "floor_premium=%.4f | grep=OPTION_PRE_ARM_PROFIT_PROTECT_EXIT",
                position_id,
                position_symbol,
                str(candidate_exit_reason or ""),
                float(held_min),
                float(hwm_age_seconds),
                float(current_pnl_pct),
                float(pre_arm_min_current_pnl),
                float(st.max_pnl_pct or 0.0),
                float(pre_arm_trigger_mfe),
                float(pre_arm_floor_pct),
                float(prem or 0.0),
                float(pre_arm_floor_premium),
            )
            return True

        def _apply_low_arm_profit_protect_floor(candidate_exit_reason: str) -> bool:
            if not low_arm_profit_protect_should_exit(
                enabled=bool(low_arm_floor_enabled),
                is_0dte_single=bool(is_0dte_single),
                protection_quote_ok=bool(protection_quote_ok),
                arm_trail_armed=bool(arm_trail_armed),
                candidate_exit_reason=str(candidate_exit_reason or ""),
                max_pnl_pct=float(st.max_pnl_pct or 0.0),
                current_pnl_pct=float(current_pnl_pct),
                held_minutes=float(held_min),
                seconds_since_hwm=float(hwm_age_seconds),
                trigger_mfe_pct=float(low_arm_trigger_mfe),
                max_mfe_pct=float(low_arm_max_mfe),
                floor_pnl_pct=float(low_arm_floor_pct),
                min_current_pnl_pct=float(low_arm_min_current_pnl),
                min_hold_minutes=float(low_arm_min_hold),
                min_seconds_since_hwm=float(low_arm_min_hwm_age),
            ):
                return False
            low_arm_floor_premium = float(st.entry_premium or st.entry_value or 0.0) * (
                1.0 + float(low_arm_floor_pct)
            )
            if bool(getattr(self.config, "dte_low_arm_profit_protect_settle_at_floor", True)):
                osnap["low_arm_profit_protect_clamp"] = True
                osnap["low_arm_profit_protect_floor_pnl_pct"] = float(low_arm_floor_pct)
            osnap["low_arm_profit_protect_triggered"] = True
            osnap["low_arm_profit_protect_candidate_exit_reason"] = str(candidate_exit_reason or "")
            osnap["low_arm_profit_protect_trigger_mfe_pct"] = float(low_arm_trigger_mfe)
            osnap["low_arm_profit_protect_max_mfe_pct"] = float(low_arm_max_mfe)
            osnap["low_arm_profit_protect_min_current_pnl_pct"] = float(low_arm_min_current_pnl)
            osnap["low_arm_profit_protect_floor_premium"] = float(low_arm_floor_premium)
            log.info(
                "OPTION_LOW_ARM_PROFIT_PROTECT_EXIT | trade_id=%s | symbol=%s | "
                "candidate_exit_reason=%s | held_min=%.2f | hwm_age_seconds=%.1f | "
                "current_pnl_pct=%.4f | min_current_pnl_pct=%.4f | max_pnl_pct=%.4f | "
                "trigger_mfe_pct=%.4f | max_mfe_pct=%.4f | floor_pnl_pct=%.4f | "
                "raw_premium=%.4f | floor_premium=%.4f | grep=OPTION_LOW_ARM_PROFIT_PROTECT_EXIT",
                position_id,
                position_symbol,
                str(candidate_exit_reason or ""),
                float(held_min),
                float(hwm_age_seconds),
                float(current_pnl_pct),
                float(low_arm_min_current_pnl),
                float(st.max_pnl_pct or 0.0),
                float(low_arm_trigger_mfe),
                float(low_arm_max_mfe),
                float(low_arm_floor_pct),
                float(prem or 0.0),
                float(low_arm_floor_premium),
            )
            return True


        def _apply_weak_loss_cap_floor(candidate_exit_reason: str) -> bool:
            if not weak_loss_cap_should_exit(
                enabled=bool(getattr(self.config, "dte_weak_loss_cap_enabled", True)),
                is_0dte_single=bool(is_0dte_single),
                protection_quote_ok=bool(protection_quote_ok),
                arm_trail_armed=bool(arm_trail_armed),
                candidate_exit_reason=str(candidate_exit_reason or ""),
                max_pnl_pct=float(st.max_pnl_pct or 0.0),
                current_pnl_pct=float(current_pnl_pct),
                max_mfe_pct=float(
                    getattr(self.config, "dte_weak_loss_cap_max_mfe_pct", 0.30) or 0.30
                ),
                floor_pnl_pct=float(
                    getattr(self.config, "dte_weak_loss_cap_floor_pnl_pct", -0.10)
                    if getattr(self.config, "dte_weak_loss_cap_floor_pnl_pct", None) is not None
                    else -0.10
                ),
            ):
                return False
            cap_floor = float(
                getattr(self.config, "dte_weak_loss_cap_floor_pnl_pct", -0.10)
                if getattr(self.config, "dte_weak_loss_cap_floor_pnl_pct", None) is not None
                else -0.15
            )
            cap_max_mfe = float(
                getattr(self.config, "dte_weak_loss_cap_max_mfe_pct", 0.30) or 0.30
            )
            cap_floor_premium = float(st.entry_premium or st.entry_value or 0.0) * (
                1.0 + float(cap_floor)
            )
            if bool(getattr(self.config, "dte_weak_loss_cap_settle_at_floor", True)):
                osnap["weak_loss_cap_clamp"] = True
                osnap["weak_loss_cap_floor_pnl_pct"] = float(cap_floor)
            osnap["weak_loss_cap_triggered"] = True
            osnap["weak_loss_cap_candidate_exit_reason"] = str(candidate_exit_reason or "")
            osnap["weak_loss_cap_max_mfe_pct"] = float(cap_max_mfe)
            osnap["weak_loss_cap_floor_premium"] = float(cap_floor_premium)
            log.info(
                "OPTION_WEAK_LOSS_CAP_EXIT | trade_id=%s | symbol=%s | "
                "candidate_exit_reason=%s | current_pnl_pct=%.4f | max_pnl_pct=%.4f | "
                "max_mfe_pct=%.4f | floor_pnl_pct=%.4f | raw_premium=%.4f | "
                "floor_premium=%.4f | grep=OPTION_WEAK_LOSS_CAP_EXIT",
                position_id,
                position_symbol,
                str(candidate_exit_reason or ""),
                float(current_pnl_pct),
                float(st.max_pnl_pct or 0.0),
                float(cap_max_mfe),
                float(cap_floor),
                float(prem or 0.0),
                float(cap_floor_premium),
            )
            return True



        def _weak_exit_wrap_would_fire(candidate_exit_reason: str) -> bool:
            """Band B: True when pre-ARM / low-ARM / CAP_weak would wrap this weak candidate.

            near-ARM red hold must run only when no rescue floor applies — otherwise
            near_arm_protect_skip starves pre-ARM/+2% and CAP_weak (GLD-397p 2026-09-21).
            """
            if pre_arm_profit_protect_should_exit(
                enabled=bool(pre_arm_floor_enabled),
                is_0dte_single=bool(is_0dte_single),
                protection_quote_ok=bool(protection_quote_ok),
                arm_trail_armed=bool(arm_trail_armed),
                candidate_exit_reason=str(candidate_exit_reason or ""),
                max_pnl_pct=float(st.max_pnl_pct or 0.0),
                current_pnl_pct=float(current_pnl_pct),
                held_minutes=float(held_min),
                seconds_since_hwm=float(hwm_age_seconds),
                trigger_mfe_pct=float(pre_arm_trigger_mfe),
                floor_pnl_pct=float(pre_arm_floor_pct),
                min_current_pnl_pct=float(pre_arm_min_current_pnl),
                winner_arm_pct=float(arm_trail_pct),
                min_hold_minutes=float(pre_arm_min_hold),
                min_seconds_since_hwm=float(pre_arm_min_hwm_age),
            ):
                return True
            if low_arm_profit_protect_should_exit(
                enabled=bool(low_arm_floor_enabled),
                is_0dte_single=bool(is_0dte_single),
                protection_quote_ok=bool(protection_quote_ok),
                arm_trail_armed=bool(arm_trail_armed),
                candidate_exit_reason=str(candidate_exit_reason or ""),
                max_pnl_pct=float(st.max_pnl_pct or 0.0),
                current_pnl_pct=float(current_pnl_pct),
                held_minutes=float(held_min),
                seconds_since_hwm=float(hwm_age_seconds),
                trigger_mfe_pct=float(low_arm_trigger_mfe),
                max_mfe_pct=float(low_arm_max_mfe),
                floor_pnl_pct=float(low_arm_floor_pct),
                min_current_pnl_pct=float(low_arm_min_current_pnl),
                min_hold_minutes=float(low_arm_min_hold),
                min_seconds_since_hwm=float(low_arm_min_hwm_age),
            ):
                return True
            if weak_loss_cap_should_exit(
                enabled=bool(getattr(self.config, "dte_weak_loss_cap_enabled", True)),
                is_0dte_single=bool(is_0dte_single),
                protection_quote_ok=bool(protection_quote_ok),
                arm_trail_armed=bool(arm_trail_armed),
                candidate_exit_reason=str(candidate_exit_reason or ""),
                max_pnl_pct=float(st.max_pnl_pct or 0.0),
                current_pnl_pct=float(current_pnl_pct),
                max_mfe_pct=float(
                    getattr(self.config, "dte_weak_loss_cap_max_mfe_pct", 0.30) or 0.30
                ),
                floor_pnl_pct=float(
                    getattr(self.config, "dte_weak_loss_cap_floor_pnl_pct", -0.10)
                    if getattr(self.config, "dte_weak_loss_cap_floor_pnl_pct", None) is not None
                    else -0.10
                ),
            ):
                return True
            return False


        # Always bind before any exit branch — deployed 00542 crashed asset_stop with
        # UnboundLocalError on ``_pre_arm_floor_active`` (OWNED_FAIR_EVAL_FAIL → defer).
        _pre_arm_floor_active = False
        _low_arm_floor_active = False
        _weak_cap_floor_active = False

        def _profit_protect_record_pnl(
            pre_arm_active: bool,
            low_arm_active: bool,
            fallback_pnl_pct: float,
        ) -> float:
            # Protect floors are exit decisions, not fills. Record live mark P&L.
            return float(fallback_pnl_pct)

        def _annotate_weak_loss_cap(diag: Dict[str, Any]) -> None:
            diag["weak_loss_cap"] = True
            diag["weak_loss_cap_floor_pnl_pct"] = float(
                getattr(self.config, "dte_weak_loss_cap_floor_pnl_pct", -0.10)
                if getattr(self.config, "dte_weak_loss_cap_floor_pnl_pct", None) is not None
                else -0.15
            )
            diag["weak_loss_cap_max_mfe_pct"] = float(
                getattr(self.config, "dte_weak_loss_cap_max_mfe_pct", 0.30) or 0.30
            )

        def _annotate_low_arm_profit_protect(diag: Dict[str, Any]) -> None:
            diag["low_arm_profit_protect"] = True
            diag["low_arm_profit_protect_floor_pnl_pct"] = float(low_arm_floor_pct)
            diag["low_arm_profit_protect_min_current_pnl_pct"] = float(low_arm_min_current_pnl)
            diag["low_arm_profit_protect_trigger_mfe_pct"] = float(low_arm_trigger_mfe)
            diag["low_arm_profit_protect_max_mfe_pct"] = float(low_arm_max_mfe)



        # --- Bid-aware working BE (ship 2026-09-22): resolve armed state before other protect exits ---
        _bid_aware_enabled = bool(
            getattr(self.config, "dte_protect_bid_aware_be_enabled", True)
        )
        _bid_aware_wait = float(
            getattr(self.config, "dte_protect_bid_aware_be_wait_minutes", 2.0) or 2.0
        )
        _bid_aware_hard_stop = float(
            getattr(self.config, "dte_protect_bid_aware_be_hard_stop_pct", -0.25)
            if getattr(self.config, "dte_protect_bid_aware_be_hard_stop_pct", None) is not None
            else -0.25
        )
        _bid_aware_target = float(
            getattr(self.config, "dte_protect_bid_aware_be_target_pnl_pct", 0.0)
            if getattr(self.config, "dte_protect_bid_aware_be_target_pnl_pct", None) is not None
            else 0.0
        )
        _bid_aware_cancel_peak = bool(
            getattr(
                self.config,
                "dte_protect_bid_aware_be_cancel_if_peak_above_rule_max",
                True,
            )
        )

        def _bid_aware_be_try_arm_protect(
            *,
            rule: str,
            floor_pnl_pct: float,
            rule_max_mfe: float,
        ) -> bool:
            """If bid-aware enabled, arm working BE and return True (caller must return None)."""
            if not _bid_aware_enabled:
                return False
            _bid_aware_be_arm(
                osnap,
                now=now,
                rule=str(rule or ""),
                floor_pnl_pct=float(floor_pnl_pct),
                rule_max_mfe=float(rule_max_mfe),
                wait_minutes=float(_bid_aware_wait),
                hard_stop_pct=float(_bid_aware_hard_stop),
                target_pnl_pct=float(_bid_aware_target),
                cancel_if_peak_above_rule_max=bool(_bid_aware_cancel_peak),
                position_id=str(position_id or ""),
                position_symbol=str(position_symbol or ""),
                current_pnl_pct=float(current_pnl_pct),
                max_pnl_pct=float(st.max_pnl_pct or 0.0),
            )
            return True

        def _bid_aware_be_divert_after_wrap() -> bool:
            """When a settle-protect wrap armed floors, divert to working BE instead of exiting red."""
            if not _bid_aware_enabled:
                return False
            if bool(osnap.get("protect_bid_aware_be_armed")):
                return True
            if bool(osnap.get("pre_arm_profit_protect_triggered")):
                return _bid_aware_be_try_arm_protect(
                    rule="PRE_ARM",
                    floor_pnl_pct=float(pre_arm_floor_pct),
                    rule_max_mfe=float(arm_trail_pct),
                )
            if bool(osnap.get("low_arm_profit_protect_triggered")):
                return _bid_aware_be_try_arm_protect(
                    rule="LOW_ARM",
                    floor_pnl_pct=float(low_arm_floor_pct),
                    rule_max_mfe=float(low_arm_max_mfe),
                )
            if bool(osnap.get("weak_loss_cap_triggered")):
                cap_floor = float(
                    getattr(self.config, "dte_weak_loss_cap_floor_pnl_pct", -0.10)
                    if getattr(self.config, "dte_weak_loss_cap_floor_pnl_pct", None) is not None
                    else -0.10
                )
                cap_max = float(
                    getattr(self.config, "dte_weak_loss_cap_max_mfe_pct", 0.30) or 0.30
                )
                return _bid_aware_be_try_arm_protect(
                    rule="CAP_WEAK",
                    floor_pnl_pct=float(cap_floor),
                    rule_max_mfe=float(cap_max),
                )
            return False

        if bool(osnap.get("protect_bid_aware_be_armed")):
            _ba_res = _bid_aware_be_resolve(
                osnap,
                now=now,
                current_pnl_pct=float(current_pnl_pct),
                max_pnl_pct=float(st.max_pnl_pct or 0.0),
                position_id=str(position_id or ""),
                position_symbol=str(position_symbol or ""),
            )
            if _ba_res in ("fill", "hardstop", "timeout"):
                _exit_id = XER.BREAKEVEN.value
                if _ba_res == "hardstop":
                    _exit_id = XER.PREMIUM_STOP.value
                diag = self._build_exit_diagnostics(
                    position_id=position_id,
                    position_symbol=position_symbol,
                    block=block,
                    st=st,
                    osnap=osnap,
                    exit_premium=prem,
                    exit_reason=_exit_id,
                    now=now,
                    prem_detail=detail,
                    premium_source_at_exit=prem_src,
                )
                diag["current_pnl_pct"] = float(current_pnl_pct)
                diag["protect_bid_aware_be"] = True
                diag["protect_bid_aware_be_resolve"] = str(_ba_res)
                diag["protect_bid_aware_be_rule"] = str(
                    osnap.get("protect_bid_aware_be_rule") or ""
                )
                # Fill remains liquidable bid — never stamp decision floor.
                osnap.pop("early_be_protect_clamp", None)
                osnap.pop("pre_arm_profit_protect_clamp", None)
                osnap.pop("low_arm_profit_protect_clamp", None)
                osnap.pop("weak_loss_cap_clamp", None)
                self._record_exit(_exit_id, float(current_pnl_pct), prem_src, diag)
                telemetry["exit_diagnostics"] = diag
                _bid_aware_be_clear(osnap)
                return self._finalize_exit_return(
                    prem, _exit_id, telemetry, st=st, osnap=osnap, detail=detail
                )
            if _ba_res is None:
                # Still waiting — keep updating HWM/MFE (already done above); no conflicting protect exit.
                return None
            # cancel → fall through to normal PC

                # Sep 19 band D: EARLY_BE aggressive FADE — no weak candidate; peak MFE must stay <20%.
        if early_be_protect_should_exit(
            enabled=bool(getattr(self.config, "dte_early_be_enabled", True)),
            is_0dte_single=bool(is_0dte_single),
            protection_quote_ok=bool(protection_quote_ok),
            arm_trail_armed=bool(arm_trail_armed),
            max_pnl_pct=float(st.max_pnl_pct or 0.0),
            current_pnl_pct=float(current_pnl_pct),
            held_minutes=float(held_min),
            seconds_since_hwm=float(hwm_age_seconds),
            max_mfe_pct=float(
                getattr(self.config, "dte_early_be_max_mfe_pct", 0.20) or 0.20
            ),
            floor_pnl_pct=float(
                getattr(self.config, "dte_early_be_floor_pnl_pct", 0.0) or 0.0
            ),
            min_current_pnl_pct=float(
                getattr(self.config, "dte_early_be_min_current_pnl_pct", -0.05)
                if getattr(self.config, "dte_early_be_min_current_pnl_pct", None) is not None
                else -0.05
            ),
            min_hold_minutes=float(
                getattr(self.config, "dte_early_be_min_hold_minutes", 5.0) or 5.0
            ),
            min_seconds_since_hwm=float(
                getattr(self.config, "dte_early_be_min_seconds_since_hwm", 0.0) or 0.0
            ),
        ):
            early_be_floor_pct = float(
                getattr(self.config, "dte_early_be_floor_pnl_pct", 0.0) or 0.0
            )
            early_be_max_mfe = float(
                getattr(self.config, "dte_early_be_max_mfe_pct", 0.20) or 0.20
            )
            early_be_min_current = float(
                getattr(self.config, "dte_early_be_min_current_pnl_pct", -0.05)
                if getattr(self.config, "dte_early_be_min_current_pnl_pct", None) is not None
                else -0.05
            )
            early_be_floor_premium = float(st.entry_premium or st.entry_value or 0.0) * (
                1.0 + float(early_be_floor_pct)
            )
            if bool(getattr(self.config, "dte_early_be_settle_at_floor", True)):
                osnap["early_be_protect_clamp"] = True
                osnap["early_be_protect_floor_pnl_pct"] = float(early_be_floor_pct)
            osnap["early_be_protect_triggered"] = True
            osnap["early_be_protect_max_mfe_pct"] = float(early_be_max_mfe)
            osnap["early_be_protect_min_current_pnl_pct"] = float(early_be_min_current)
            osnap["early_be_protect_floor_premium"] = float(early_be_floor_premium)
            if _bid_aware_be_try_arm_protect(
                rule="EARLY_BE",
                floor_pnl_pct=float(early_be_floor_pct),
                rule_max_mfe=float(early_be_max_mfe),
            ):
                return None
            _exit_id = XER.BREAKEVEN.value
            diag = self._build_exit_diagnostics(
                position_id=position_id,
                position_symbol=position_symbol,
                block=block,
                st=st,
                osnap=osnap,
                exit_premium=prem,
                exit_reason=_exit_id,
                now=now,
                prem_detail=detail,
                premium_source_at_exit=prem_src,
            )
            diag["current_pnl_pct"] = float(current_pnl_pct)
            diag["early_be_protect"] = True
            diag["early_be_protect_floor_pnl_pct"] = float(early_be_floor_pct)
            diag["early_be_protect_max_mfe_pct"] = float(early_be_max_mfe)
            diag["early_be_protect_min_current_pnl_pct"] = float(early_be_min_current)
            log.info(
                "OPTION_EARLY_BE_EXIT | trade_id=%s | symbol=%s | exit_id=%s | "
                "held_min=%.2f | hwm_age_seconds=%.1f | current_pnl_pct=%.4f | "
                "max_pnl_pct=%.4f | max_mfe_pct=%.4f | floor_pnl_pct=%.4f | "
                "grep=OPTION_EARLY_BE_EXIT",
                position_id,
                position_symbol,
                _exit_id,
                float(held_min),
                float(hwm_age_seconds),
                float(current_pnl_pct),
                float(st.max_pnl_pct or 0.0),
                float(early_be_max_mfe),
                float(early_be_floor_pct),
            )
            self._record_exit(_exit_id, float(pnl_pct), prem_src, diag)
            telemetry["exit_diagnostics"] = diag
            return self._finalize_exit_return(
                prem, _exit_id, telemetry, st=st, osnap=osnap, detail=detail
            )

        # Sep 22 Band B/C proactive protect: same gates as wrap floors, no weak-candidate wait.
        # Fixes 20–60m dump (SMH-595p / IBIT-48p): pre-ARM gates true from ~20m but no_progress=60m.
        if pre_arm_profit_protect_should_exit(
            enabled=bool(pre_arm_floor_enabled),
            is_0dte_single=bool(is_0dte_single),
            protection_quote_ok=bool(protection_quote_ok),
            arm_trail_armed=bool(arm_trail_armed),
            max_pnl_pct=float(st.max_pnl_pct or 0.0),
            current_pnl_pct=float(current_pnl_pct),
            held_minutes=float(held_min),
            seconds_since_hwm=float(hwm_age_seconds),
            candidate_exit_reason="proactive",
            trigger_mfe_pct=float(pre_arm_trigger_mfe),
            floor_pnl_pct=float(pre_arm_floor_pct),
            min_current_pnl_pct=float(pre_arm_min_current_pnl),
            winner_arm_pct=float(arm_trail_pct),
            min_hold_minutes=float(pre_arm_min_hold),
            min_seconds_since_hwm=float(pre_arm_min_hwm_age),
            allow_proactive=True,
        ):
            pre_arm_floor_premium = float(st.entry_premium or st.entry_value or 0.0) * (
                1.0 + float(pre_arm_floor_pct)
            )
            if bool(getattr(self.config, "dte_pre_arm_profit_protect_settle_at_floor", True)):
                osnap["pre_arm_profit_protect_clamp"] = True
                osnap["pre_arm_profit_protect_floor_pnl_pct"] = float(pre_arm_floor_pct)
            osnap["pre_arm_profit_protect_triggered"] = True
            osnap["pre_arm_profit_protect_proactive"] = True
            osnap["pre_arm_profit_protect_candidate_exit_reason"] = "proactive"
            osnap["pre_arm_profit_protect_trigger_mfe_pct"] = float(pre_arm_trigger_mfe)
            osnap["pre_arm_profit_protect_min_current_pnl_pct"] = float(pre_arm_min_current_pnl)
            osnap["pre_arm_profit_protect_floor_premium"] = float(pre_arm_floor_premium)
            if _bid_aware_be_try_arm_protect(
                rule="PRE_ARM",
                floor_pnl_pct=float(pre_arm_floor_pct),
                rule_max_mfe=float(arm_trail_pct),
            ):
                return None
            _exit_id = XER.BREAKEVEN.value
            diag = self._build_exit_diagnostics(
                position_id=position_id,
                position_symbol=position_symbol,
                block=block,
                st=st,
                osnap=osnap,
                exit_premium=prem,
                exit_reason=_exit_id,
                now=now,
                prem_detail=detail,
                premium_source_at_exit=prem_src,
            )
            diag["current_pnl_pct"] = float(current_pnl_pct)
            diag["pre_arm_profit_protect"] = True
            diag["pre_arm_profit_protect_proactive"] = True
            diag["pre_arm_profit_protect_floor_pnl_pct"] = float(pre_arm_floor_pct)
            diag["pre_arm_profit_protect_min_current_pnl_pct"] = float(pre_arm_min_current_pnl)
            log.info(
                "OPTION_PRE_ARM_PROFIT_PROTECT_EXIT | trade_id=%s | symbol=%s | "
                "candidate_exit_reason=proactive | held_min=%.2f | hwm_age_seconds=%.1f | "
                "current_pnl_pct=%.4f | min_current_pnl_pct=%.4f | max_pnl_pct=%.4f | "
                "trigger_mfe_pct=%.4f | floor_pnl_pct=%.4f | raw_premium=%.4f | "
                "floor_premium=%.4f | grep=OPTION_PRE_ARM_PROFIT_PROTECT_EXIT",
                position_id,
                position_symbol,
                float(held_min),
                float(hwm_age_seconds),
                float(current_pnl_pct),
                float(pre_arm_min_current_pnl),
                float(st.max_pnl_pct or 0.0),
                float(pre_arm_trigger_mfe),
                float(pre_arm_floor_pct),
                float(prem or 0.0),
                float(pre_arm_floor_premium),
            )
            self._record_exit(_exit_id, float(current_pnl_pct), prem_src, diag)
            telemetry["exit_diagnostics"] = diag
            return self._finalize_exit_return(
                prem, _exit_id, telemetry, st=st, osnap=osnap, detail=detail
            )

        if low_arm_profit_protect_should_exit(
            enabled=bool(low_arm_floor_enabled),
            is_0dte_single=bool(is_0dte_single),
            protection_quote_ok=bool(protection_quote_ok),
            arm_trail_armed=bool(arm_trail_armed),
            max_pnl_pct=float(st.max_pnl_pct or 0.0),
            current_pnl_pct=float(current_pnl_pct),
            held_minutes=float(held_min),
            seconds_since_hwm=float(hwm_age_seconds),
            candidate_exit_reason="proactive",
            trigger_mfe_pct=float(low_arm_trigger_mfe),
            max_mfe_pct=float(low_arm_max_mfe),
            floor_pnl_pct=float(low_arm_floor_pct),
            min_current_pnl_pct=float(low_arm_min_current_pnl),
            min_hold_minutes=float(low_arm_min_hold),
            min_seconds_since_hwm=float(low_arm_min_hwm_age),
            allow_proactive=True,
        ):
            low_arm_floor_premium = float(st.entry_premium or st.entry_value or 0.0) * (
                1.0 + float(low_arm_floor_pct)
            )
            if bool(getattr(self.config, "dte_low_arm_profit_protect_settle_at_floor", True)):
                osnap["low_arm_profit_protect_clamp"] = True
                osnap["low_arm_profit_protect_floor_pnl_pct"] = float(low_arm_floor_pct)
            osnap["low_arm_profit_protect_triggered"] = True
            osnap["low_arm_profit_protect_proactive"] = True
            osnap["low_arm_profit_protect_candidate_exit_reason"] = "proactive"
            osnap["low_arm_profit_protect_trigger_mfe_pct"] = float(low_arm_trigger_mfe)
            osnap["low_arm_profit_protect_max_mfe_pct"] = float(low_arm_max_mfe)
            osnap["low_arm_profit_protect_min_current_pnl_pct"] = float(low_arm_min_current_pnl)
            osnap["low_arm_profit_protect_floor_premium"] = float(low_arm_floor_premium)
            if _bid_aware_be_try_arm_protect(
                rule="LOW_ARM",
                floor_pnl_pct=float(low_arm_floor_pct),
                rule_max_mfe=float(low_arm_max_mfe),
            ):
                return None
            _exit_id = XER.BREAKEVEN.value
            diag = self._build_exit_diagnostics(
                position_id=position_id,
                position_symbol=position_symbol,
                block=block,
                st=st,
                osnap=osnap,
                exit_premium=prem,
                exit_reason=_exit_id,
                now=now,
                prem_detail=detail,
                premium_source_at_exit=prem_src,
            )
            diag["current_pnl_pct"] = float(current_pnl_pct)
            diag["low_arm_profit_protect"] = True
            diag["low_arm_profit_protect_proactive"] = True
            diag["low_arm_profit_protect_floor_pnl_pct"] = float(low_arm_floor_pct)
            diag["low_arm_profit_protect_min_current_pnl_pct"] = float(low_arm_min_current_pnl)
            log.info(
                "OPTION_LOW_ARM_PROFIT_PROTECT_EXIT | trade_id=%s | symbol=%s | "
                "candidate_exit_reason=proactive | held_min=%.2f | hwm_age_seconds=%.1f | "
                "current_pnl_pct=%.4f | min_current_pnl_pct=%.4f | max_pnl_pct=%.4f | "
                "trigger_mfe_pct=%.4f | max_mfe_pct=%.4f | floor_pnl_pct=%.4f | "
                "raw_premium=%.4f | floor_premium=%.4f | grep=OPTION_LOW_ARM_PROFIT_PROTECT_EXIT",
                position_id,
                position_symbol,
                float(held_min),
                float(hwm_age_seconds),
                float(current_pnl_pct),
                float(low_arm_min_current_pnl),
                float(st.max_pnl_pct or 0.0),
                float(low_arm_trigger_mfe),
                float(low_arm_max_mfe),
                float(low_arm_floor_pct),
                float(prem or 0.0),
                float(low_arm_floor_premium),
            )
            self._record_exit(_exit_id, float(current_pnl_pct), prem_src, diag)
            telemetry["exit_diagnostics"] = diag
            return self._finalize_exit_return(
                prem, _exit_id, telemetry, st=st, osnap=osnap, detail=detail
            )

        if (
            asset_stop_enabled
            and is_0dte_single
            and protection_quote_ok
            and asset_stop_pct > 0
            and entry_u > 0
            and float(current_underlying or 0.0) > 0
            and str(osnap.get("underlying_source") or "").strip().lower() != "unavailable"
            and (not asset_stop_only_before_tp or not arm_trail_armed)
            and asset_stop_underlying_adverse(
                option_side=str(st.option_side or osnap.get("option_side") or ""),
                entry_underlying=float(entry_u),
                current_underlying=float(current_underlying),
                buffer_pct=float(asset_stop_pct),
            )
        ):
            side_l = str(st.option_side or osnap.get("option_side") or "").strip().lower()
            if "put" in side_l:
                price_adverse_pct = (float(current_underlying) - float(entry_u)) / float(entry_u)
            else:
                price_adverse_pct = (float(entry_u) - float(current_underlying)) / float(entry_u)
            log.info(
                "OPTIONS_STEALTH_ASSET_STOP_EXIT | trade_id=%s | symbol=%s | side=%s | "
                "entry_underlying=%.6f | current_underlying=%.6f | adverse_pct=%.5f | "
                "buffer_pct=%.5f | current_pnl_pct=%.4f | max_pnl_pct=%.4f | premium=%.4f",
                position_id,
                position_symbol,
                side_l or "call",
                float(entry_u),
                float(current_underlying),
                float(price_adverse_pct),
                float(asset_stop_pct),
                float(current_pnl_pct),
                float(st.max_pnl_pct or 0.0),
                float(prem or 0.0),
            )
            diag = self._build_exit_diagnostics(
                position_id=position_id,
                position_symbol=position_symbol,
                block=block,
                st=st,
                osnap=osnap,
                exit_premium=prem,
                exit_reason=XER.PRICE_STOP.value,
                now=now,
                prem_detail=detail,
                premium_source_at_exit=prem_src,
            )
            diag["asset_stop_pct"] = float(asset_stop_pct)
            diag["underlying_adverse_pct"] = float(price_adverse_pct)
            diag["entry_underlying"] = float(entry_u)
            diag["current_underlying"] = float(current_underlying)
            diag["current_pnl_pct"] = float(current_pnl_pct)
            _pre_arm_floor_active = _apply_pre_arm_profit_protect_floor(XER.PRICE_STOP.value)
            _low_arm_floor_active = False
            _weak_cap_floor_active = False
            if not _pre_arm_floor_active:
                _low_arm_floor_active = _apply_low_arm_profit_protect_floor(XER.PRICE_STOP.value)
                if not _low_arm_floor_active:
                    _weak_cap_floor_active = _apply_weak_loss_cap_floor(XER.PRICE_STOP.value)
            if _pre_arm_floor_active:
                diag["pre_arm_profit_protect"] = True
                diag["pre_arm_profit_protect_floor_pnl_pct"] = float(pre_arm_floor_pct)
                diag["pre_arm_profit_protect_min_current_pnl_pct"] = float(pre_arm_min_current_pnl)
            if _low_arm_floor_active:
                _annotate_low_arm_profit_protect(diag)
            if _weak_cap_floor_active:
                _annotate_weak_loss_cap(diag)
            if (_pre_arm_floor_active or _low_arm_floor_active or _weak_cap_floor_active) and _bid_aware_be_divert_after_wrap():
                return None
            self._record_exit(
                XER.PRICE_STOP.value,
                _profit_protect_record_pnl(
                    _pre_arm_floor_active,
                    _low_arm_floor_active,
                    pnl_pct,
                ),
                prem_src,
                diag,
            )
            telemetry["exit_diagnostics"] = diag
            return self._finalize_exit_return(
                prem, XER.PRICE_STOP.value, telemetry, st=st, osnap=osnap, detail=detail
            )

        # Premium hard stop (Aug 14): never-armed · prem ≤ −60% · peak MFE < 15%.
        # Protects lotto bleed when und price_stop alone misses (SMH/LRCX class).
        premium_stop_enabled = bool(
            getattr(self.config, "dte_single_premium_stop_enabled", True)
        )
        premium_stop_pct = float(
            getattr(self.config, "dte_single_premium_stop_pct", 0.60) or 0.60
        )
        premium_stop_max_mfe = float(
            getattr(self.config, "dte_single_premium_stop_max_mfe_pct", 0.15) or 0.15
        )
        premium_stop_before_arm = bool(
            getattr(self.config, "dte_single_premium_stop_before_arm", True)
        )
        premium_stop_settle_at_stop = bool(
            getattr(self.config, "dte_single_premium_stop_settle_at_stop", True)
        )
        nonstarter_stop_enabled = bool(
            getattr(self.config, "dte_single_nonstarter_stop_enabled", True)
        )
        nonstarter_stop_pct = float(
            getattr(self.config, "dte_single_nonstarter_stop_pct", 0.40) or 0.40
        )
        nonstarter_stop_max_mfe = float(
            getattr(self.config, "dte_single_nonstarter_stop_max_mfe_pct", 0.10) or 0.10
        )
        nonstarter_stop_min_hold = float(
            getattr(self.config, "dte_single_nonstarter_stop_min_hold_minutes", 20.0) or 20.0
        )
        nonstarter_stop_before_arm = bool(
            getattr(self.config, "dte_single_nonstarter_stop_before_arm", True)
        )
        nonstarter_stop_settle_at_stop = bool(
            getattr(self.config, "dte_single_nonstarter_stop_settle_at_stop", True)
        )
        peak_mfe = float(st.max_pnl_pct or 0.0)
        if (
            nonstarter_stop_enabled
            and is_0dte_single
            and protection_quote_ok
            and nonstarter_stop_pct > 0
            and (not nonstarter_stop_before_arm or not arm_trail_armed)
            and float(held_min) + 1e-12 >= float(nonstarter_stop_min_hold)
            and peak_mfe + 1e-12 < float(nonstarter_stop_max_mfe)
            and float(current_pnl_pct) + 1e-12 <= -float(nonstarter_stop_pct)
        ):
            log.info(
                "OPTIONS_STEALTH_NONSTARTER_STOP_EXIT | trade_id=%s | symbol=%s | "
                "held_min=%.2f | current_pnl_pct=%.4f | max_pnl_pct=%.4f | "
                "stop_pct=%.4f | max_mfe_cap=%.4f | premium=%.4f | "
                "grep=OPTIONS_STEALTH_NONSTARTER_STOP_EXIT",
                position_id,
                position_symbol,
                float(held_min),
                float(current_pnl_pct),
                float(peak_mfe),
                float(nonstarter_stop_pct),
                float(nonstarter_stop_max_mfe),
                float(prem or 0.0),
            )
            diag = self._build_exit_diagnostics(
                position_id=position_id,
                position_symbol=position_symbol,
                block=block,
                st=st,
                osnap=osnap,
                exit_premium=prem,
                exit_reason=XER.PREMIUM_STOP.value,
                now=now,
                prem_detail=detail,
                premium_source_at_exit=prem_src,
            )
            diag["nonstarter_stop"] = True
            diag["nonstarter_stop_pct"] = float(nonstarter_stop_pct)
            diag["nonstarter_stop_max_mfe_pct"] = float(nonstarter_stop_max_mfe)
            diag["nonstarter_stop_min_hold_minutes"] = float(nonstarter_stop_min_hold)
            diag["nonstarter_stop_settle_at_stop"] = bool(nonstarter_stop_settle_at_stop)
            diag["current_pnl_pct"] = float(current_pnl_pct)
            diag["max_pnl_pct"] = float(peak_mfe)
            if bool(nonstarter_stop_settle_at_stop):
                osnap["nonstarter_stop_settle_at_stop"] = True
                osnap["nonstarter_stop_pct"] = float(nonstarter_stop_pct)
            self._record_exit(
                XER.PREMIUM_STOP.value,
                pnl_pct,
                prem_src,
                diag,
            )
            telemetry["exit_diagnostics"] = diag
            return self._finalize_exit_return(
                prem, XER.PREMIUM_STOP.value, telemetry, st=st, osnap=osnap, detail=detail
            )

        if (
            premium_stop_enabled
            and is_0dte_single
            and protection_quote_ok
            and premium_stop_pct > 0
            and (not premium_stop_before_arm or not arm_trail_armed)
            and peak_mfe + 1e-12 < float(premium_stop_max_mfe)
            and float(current_pnl_pct) + 1e-12 <= -float(premium_stop_pct)
        ):
            log.info(
                "OPTIONS_STEALTH_PREMIUM_STOP_EXIT | trade_id=%s | symbol=%s | "
                "current_pnl_pct=%.4f | max_pnl_pct=%.4f | stop_pct=%.4f | "
                "max_mfe_cap=%.4f | premium=%.4f | grep=OPTIONS_STEALTH_PREMIUM_STOP_EXIT",
                position_id,
                position_symbol,
                float(current_pnl_pct),
                float(peak_mfe),
                float(premium_stop_pct),
                float(premium_stop_max_mfe),
                float(prem or 0.0),
            )
            _pre_arm_floor_active = _apply_pre_arm_profit_protect_floor(XER.PREMIUM_STOP.value)
            _low_arm_floor_active = False
            _weak_cap_floor_active = False
            if not _pre_arm_floor_active:
                _low_arm_floor_active = _apply_low_arm_profit_protect_floor(XER.PREMIUM_STOP.value)
                if not _low_arm_floor_active:
                    _weak_cap_floor_active = _apply_weak_loss_cap_floor(XER.PREMIUM_STOP.value)
            diag = self._build_exit_diagnostics(
                position_id=position_id,
                position_symbol=position_symbol,
                block=block,
                st=st,
                osnap=osnap,
                exit_premium=prem,
                exit_reason=XER.PREMIUM_STOP.value,
                now=now,
                prem_detail=detail,
                premium_source_at_exit=prem_src,
            )
            diag["premium_stop_pct"] = float(premium_stop_pct)
            diag["premium_stop_max_mfe_pct"] = float(premium_stop_max_mfe)
            diag["premium_stop_settle_at_stop"] = bool(premium_stop_settle_at_stop)
            diag["current_pnl_pct"] = float(current_pnl_pct)
            diag["max_pnl_pct"] = float(peak_mfe)
            if bool(premium_stop_settle_at_stop):
                osnap["premium_stop_settle_at_stop"] = True
                osnap["premium_stop_pct"] = float(premium_stop_pct)
            if _pre_arm_floor_active:
                diag["pre_arm_profit_protect"] = True
                diag["pre_arm_profit_protect_floor_pnl_pct"] = float(pre_arm_floor_pct)
                diag["pre_arm_profit_protect_min_current_pnl_pct"] = float(pre_arm_min_current_pnl)
            if _low_arm_floor_active:
                _annotate_low_arm_profit_protect(diag)
            if _weak_cap_floor_active:
                _annotate_weak_loss_cap(diag)
            if (_pre_arm_floor_active or _low_arm_floor_active or _weak_cap_floor_active) and _bid_aware_be_divert_after_wrap():
                return None
            self._record_exit(
                XER.PREMIUM_STOP.value,
                _profit_protect_record_pnl(
                    _pre_arm_floor_active,
                    _low_arm_floor_active,
                    pnl_pct,
                ),
                prem_src,
                diag,
            )
            telemetry["exit_diagnostics"] = diag
            return self._finalize_exit_return(
                prem, XER.PREMIUM_STOP.value, telemetry, st=st, osnap=osnap, detail=detail
            )

        # Und runner after +30% ARM_TRAIL (Aug 17 PC-PX2 max-net):
        # stall-bank + 20pp giveback + lock@200% + BE floor.
        # 0.50% und trail only after und HWM ≥0.5% AND prem ≥60%.
        # Thin TP30 + always-bank TP30 REMOVED (inferior).
        price_runner_enabled = bool(getattr(self.config, "dte_single_price_runner_enabled", False))
        price_runner_consumed_tp = False
        px2_enabled = bool(getattr(self.config, "dte_single_px2_enabled", True))
        # ARM-tick thin TP30 REMOVED (old 21% stack; PX2 is max-net).
        thin_tp_enabled = False
        thin_und_max = float(
            getattr(self.config, "dte_single_mfe_take_profit_thin_max_und_pct", 0.005) or 0.005
        )
        if (
            price_runner_enabled
            and is_0dte_single
            and protection_quote_ok
            and entry_u > 0
            and (
                float(current_underlying or 0.0) > 0
                or (thin_tp_enabled and bool(arm_trail_armed))
                or (px2_enabled and bool(arm_trail_armed))
            )
        ):
            try:
                from modules.price_runner_exit import (
                    apply_price_runner_state_updates,
                    arm_trail_price_is_thin,
                    clear_price_runner_state,
                    evaluate_price_runner_tick,
                    resolve_runner_prem_floor_pct,
                    resolve_runner_trail_pct,
                    underlying_favorable_move_pct,
                )
            except Exception:
                from price_runner_exit import (  # type: ignore
                    apply_price_runner_state_updates,
                    arm_trail_price_is_thin,
                    clear_price_runner_state,
                    evaluate_price_runner_tick,
                    resolve_runner_prem_floor_pct,
                    resolve_runner_trail_pct,
                    underlying_favorable_move_pct,
                )
            arm_price = float(getattr(self.config, "dte_single_price_runner_arm_price_fav_pct", 0.0005) or 0.0005)
            conf_ticks = int(getattr(self.config, "dte_single_price_runner_confirm_ticks", 3) or 3)
            trail_pct, trail_wide = resolve_runner_trail_pct(
                base_trail_pct=float(
                    getattr(self.config, "dte_single_price_runner_trail_pct", 0.002) or 0.002
                ),
                wide_trail_pct=float(
                    getattr(self.config, "dte_single_price_runner_wide_trail_pct", 0.005) or 0.0
                ),
                wide_mfe_pct=float(
                    getattr(self.config, "dte_single_price_runner_wide_trail_mfe_pct", 0.60) or 0.0
                ),
                premium_mfe_pct=max(
                    float(getattr(st, "max_pnl_pct", 0.0) or 0.0),
                    float(current_pnl_pct or 0.0),
                ),
            )
            osnap["price_runner_trail_wide"] = bool(trail_wide)
            # 0 disables hard und TP (Aug 7 ship). Do not use `or 0.01` — that revived +1% when OFF.
            _price_tp_raw = getattr(self.config, "dte_single_price_runner_take_profit_pct", 0.01)
            try:
                price_tp_pct = float(_price_tp_raw)
            except (TypeError, ValueError):
                price_tp_pct = 0.01

            base_prem_floor = float(
                getattr(self.config, "dte_single_price_runner_prem_floor_pct", 0.0) or 0.0
            )
            thin_max = float(
                getattr(
                    self.config,
                    "dte_single_price_runner_prem_floor_price_thin_max_pct",
                    0.0,
                )
                or 0.0
            )
            thin_floor = float(
                getattr(
                    self.config,
                    "dte_single_price_runner_prem_floor_price_thin_pct",
                    0.0,
                )
                or 0.0
            )
            side_l = str(st.option_side or osnap.get("option_side") or "")
            past_gate = bool(arm_trail_armed)
            was_active = bool(osnap.get("price_runner_active"))

            # PC-PX2 after ARM: stall-bank / 20pp / lock@200% / never red.
            # PC-PX3 (default OFF): optional premium_trail handoff to 0.4% und trail.
            if px2_enabled and past_gate and protection_quote_ok:
                try:
                    from modules.px2_exit import (
                        apply_px2_state_updates,
                        evaluate_px2_tick,
                        px2_be_floor_fill_premium,
                        px2_und_hwm_fav,
                    )
                except Exception:
                    from px2_exit import (  # type: ignore
                        apply_px2_state_updates,
                        evaluate_px2_tick,
                        px2_be_floor_fill_premium,
                        px2_und_hwm_fav,
                    )
                try:
                    from modules.px3_exit import (
                        apply_px3_state_updates,
                        evaluate_px3_at_mfe_take_profit,
                        evaluate_px3_at_prem_trail,
                        evaluate_px3_mfe_confirmation_tick,
                        evaluate_px3_runner_tick,
                        px3_active,
                        px3_config_from_engine,
                        px3_mfe_confirm_pending,
                        px3_und_hwm_price_from_fav,
                    )
                    px3_cfg = px3_config_from_engine(self.config)
                except Exception:
                    try:
                        from px3_exit import (  # type: ignore
                            apply_px3_state_updates,
                            evaluate_px3_at_mfe_take_profit,
                            evaluate_px3_at_prem_trail,
                            evaluate_px3_mfe_confirmation_tick,
                            evaluate_px3_runner_tick,
                            px3_active,
                            px3_config_from_engine,
                            px3_mfe_confirm_pending,
                            px3_und_hwm_price_from_fav,
                        )
                        px3_cfg = px3_config_from_engine(self.config)
                    except Exception:
                        def px3_active(_s):  # type: ignore
                            return False

                        def px3_mfe_confirm_pending(_s):  # type: ignore
                            return False

                        px3_cfg = SimpleNamespace(enabled=False)
                try:
                    from modules.px25_exit import (
                        apply_px25_state_updates,
                        evaluate_px25_at_exit,
                        evaluate_px25_extension_tick,
                        px25_active,
                        px25_config_from_engine,
                        px25_und_hwm_price_from_current_or_existing,
                    )
                    px25_cfg = px25_config_from_engine(self.config)
                except Exception:
                    try:
                        from px25_exit import (  # type: ignore
                            apply_px25_state_updates,
                            evaluate_px25_at_exit,
                            evaluate_px25_extension_tick,
                            px25_active,
                            px25_config_from_engine,
                            px25_und_hwm_price_from_current_or_existing,
                        )
                        px25_cfg = px25_config_from_engine(self.config)
                    except Exception:
                        def px25_active(_s):  # type: ignore
                            return False

                        px25_cfg = SimpleNamespace(enabled=False)
                px3_now = now.isoformat() if hasattr(now, "isoformat") else str(now or "")
                px3_skip_px2 = False
                if px25_cfg.enabled and px25_active(osnap):
                    p25 = evaluate_px25_extension_tick(
                        osnap=osnap,
                        option_side=side_l,
                        current_underlying=float(current_underlying or 0.0),
                        current_pnl_pct=float(current_pnl_pct),
                        now_ts=px3_now,
                        cfg=px25_cfg,
                    )
                    apply_px25_state_updates(osnap, p25.state_updates)
                    price_runner_consumed_tp = True
                    px3_skip_px2 = True
                    if p25.action == "exit" and p25.reason:
                        exit_reason = normalize_exit_reason(p25.reason) or str(p25.reason)
                        fill_prem = float(prem or 0.0)
                        log.info(
                            "OPTIONS_STEALTH_PX25_EXIT | trade_id=%s | symbol=%s | "
                            "reason=%s | detail=%s | current_pnl_pct=%.4f | premium=%.4f | "
                            "grep=OPTIONS_STEALTH_PX25",
                            position_id,
                            position_symbol,
                            str(exit_reason or ""),
                            str(p25.detail or ""),
                            float(current_pnl_pct),
                            float(prem or 0.0),
                        )
                        diag = self._build_exit_diagnostics(
                            position_id=position_id,
                            position_symbol=position_symbol,
                            block=block,
                            st=st,
                            osnap=osnap,
                            exit_premium=fill_prem,
                            exit_reason=exit_reason,
                            now=now,
                            prem_detail=detail,
                            premium_source_at_exit=prem_src,
                        )
                        diag["px25_detail"] = str(p25.detail or "")
                        diag["px25_original_reason"] = str(osnap.get("px25_original_reason") or "")
                        diag["arm_trail_branch"] = "px25"
                        diag["current_pnl_pct"] = float(current_pnl_pct)
                        self._record_exit(exit_reason, pnl_pct, prem_src, diag)
                        telemetry["exit_diagnostics"] = diag
                        return self._finalize_exit_return(
                            fill_prem, exit_reason, telemetry, st=st, osnap=osnap, detail=detail
                        )
                if px3_cfg.enabled and px3_mfe_confirm_pending(osnap):
                    p3m = evaluate_px3_mfe_confirmation_tick(
                        osnap=osnap,
                        option_side=side_l,
                        current_underlying=float(current_underlying or 0.0),
                        current_pnl_pct=float(current_pnl_pct),
                        now_ts=px3_now,
                        cfg=px3_cfg,
                    )
                    apply_px3_state_updates(osnap, p3m.state_updates)
                    price_runner_consumed_tp = True
                    px3_skip_px2 = True
                    if p3m.action == "continue":
                        p3m_event = (
                            "OPTIONS_STEALTH_PX3_MFE_CONFIRM_HANDOFF"
                            if str(p3m.detail or "") == "mfe_confirm_handoff"
                            else "OPTIONS_STEALTH_PX3_MFE_CONFIRM_PENDING"
                        )
                        log.info(
                            "%s | trade_id=%s | "
                            "symbol=%s | mode=%s | detail=%s | current_pnl_pct=%.4f | "
                            "original_pnl_pct=%.4f | grep=OPTIONS_STEALTH_PX3",
                            p3m_event,
                            position_id,
                            position_symbol,
                            str(p3m.mode or osnap.get("px3_mode") or ""),
                            str(p3m.detail or ""),
                            float(current_pnl_pct),
                            float(osnap.get("px3_mfe_confirm_original_pnl_pct") or 0.0),
                        )
                        return None
                    if p3m.action == "exit" and p3m.reason:
                        exit_reason = normalize_exit_reason(p3m.reason) or str(p3m.reason)
                        fill_prem = float(prem or 0.0)
                        log.info(
                            "OPTIONS_STEALTH_%s_EXIT | trade_id=%s | symbol=%s | detail=%s | "
                            "current_pnl_pct=%.4f | premium=%.4f | grep=OPTIONS_STEALTH_PX3",
                            str(exit_reason or "PX3").upper(),
                            position_id,
                            position_symbol,
                            str(p3m.detail or ""),
                            float(current_pnl_pct),
                            float(fill_prem or 0.0),
                        )
                        diag = self._build_exit_diagnostics(
                            position_id=position_id,
                            position_symbol=position_symbol,
                            block=block,
                            st=st,
                            osnap=osnap,
                            exit_premium=fill_prem,
                            exit_reason=exit_reason,
                            now=now,
                            prem_detail=detail,
                            premium_source_at_exit=prem_src,
                        )
                        diag["px3_detail"] = str(p3m.detail or "")
                        diag["px3_mode"] = str(p3m.mode or osnap.get("px3_mode") or "")
                        diag["arm_trail_branch"] = "px3_mfe_confirm"
                        diag["current_pnl_pct"] = float(current_pnl_pct)
                        self._record_exit(exit_reason, pnl_pct, prem_src, diag)
                        telemetry["exit_diagnostics"] = diag
                        return self._finalize_exit_return(
                            fill_prem, exit_reason, telemetry, st=st, osnap=osnap, detail=detail
                        )
                if px3_cfg.enabled and px3_active(osnap):
                    p3 = evaluate_px3_runner_tick(
                        osnap=osnap,
                        option_side=side_l,
                        current_underlying=float(current_underlying or 0.0),
                        current_pnl_pct=float(current_pnl_pct),
                        now_ts=px3_now,
                        cfg=px3_cfg,
                    )
                    apply_px3_state_updates(osnap, p3.state_updates)
                    price_runner_consumed_tp = True
                    px3_skip_px2 = True
                    if p3.action == "exit" and p3.reason:
                        exit_reason = normalize_exit_reason(p3.reason) or str(p3.reason)
                        fill_prem = float(prem or 0.0)
                        px25_override = False
                        if px25_cfg.enabled:
                            prem_hwm_now = max(
                                float(getattr(st, "max_pnl_pct", 0.0) or 0.0),
                                float(current_pnl_pct or 0.0),
                                float(osnap.get("px2_prem_hwm_pct") or 0.0),
                                float(osnap.get("px25_start_prem_hwm_pct") or 0.0),
                            )
                            p25 = evaluate_px25_at_exit(
                                osnap=osnap,
                                candidate_reason=exit_reason,
                                current_pnl_pct=float(current_pnl_pct),
                                prem_hwm_pct=float(prem_hwm_now),
                                option_side=side_l,
                                und_hwm_price=px25_und_hwm_price_from_current_or_existing(
                                    osnap=osnap,
                                    option_side=side_l,
                                    current_underlying=float(current_underlying or 0.0),
                                ),
                                current_underlying=float(current_underlying or 0.0),
                                now_ts=px3_now,
                                cfg=px25_cfg,
                            )
                            apply_px25_state_updates(osnap, p25.state_updates)
                            if p25.action == "continue":
                                px25_override = True
                                log.info(
                                    "OPTIONS_STEALTH_PX25_HANDOFF | trade_id=%s | symbol=%s | "
                                    "from=px3 | reason=%s | detail=%s | current_pnl_pct=%.4f | "
                                    "prem_hwm_pct=%.4f | grep=OPTIONS_STEALTH_PX25",
                                    position_id,
                                    position_symbol,
                                    str(exit_reason or ""),
                                    str(p25.detail or ""),
                                    float(current_pnl_pct),
                                    float(prem_hwm_now),
                                )
                        if px25_override:
                            return None
                        log.info(
                            "OPTIONS_STEALTH_%s_EXIT | trade_id=%s | symbol=%s | detail=%s | "
                            "px3_mode=%s | current_pnl_pct=%.4f | premium=%.4f | "
                            "grep=OPTIONS_STEALTH_PX3",
                            str(exit_reason or "PX3").upper(),
                            position_id,
                            position_symbol,
                            str(p3.detail or ""),
                            str(p3.mode or osnap.get("px3_mode") or ""),
                            float(current_pnl_pct),
                            float(prem or 0.0),
                        )
                        diag = self._build_exit_diagnostics(
                            position_id=position_id,
                            position_symbol=position_symbol,
                            block=block,
                            st=st,
                            osnap=osnap,
                            exit_premium=fill_prem,
                            exit_reason=exit_reason,
                            now=now,
                            prem_detail=detail,
                            premium_source_at_exit=prem_src,
                        )
                        diag["px3_detail"] = str(p3.detail or "")
                        diag["px3_mode"] = str(p3.mode or osnap.get("px3_mode") or "")
                        diag["arm_trail_branch"] = "px3"
                        diag["current_pnl_pct"] = float(current_pnl_pct)
                        self._record_exit(exit_reason, pnl_pct, prem_src, diag)
                        telemetry["exit_diagnostics"] = diag
                        return self._finalize_exit_return(
                            fill_prem, exit_reason, telemetry, st=st, osnap=osnap, detail=detail
                        )
                if not px3_skip_px2:
                    px2 = evaluate_px2_tick(
                        osnap=osnap,
                        option_side=side_l,
                        entry_underlying=float(entry_u),
                        current_underlying=float(current_underlying or 0.0),
                        current_pnl_pct=float(current_pnl_pct),
                        prem_hwm_pct=trail_hwm_for_px2(
                            osnap,
                            session_max_pnl_pct=float(getattr(st, "max_pnl_pct", 0.0) or 0.0),
                            current_pnl_pct=float(current_pnl_pct or 0.0),
                        ),
                        stall_ticks=int(getattr(self.config, "dte_single_px2_stall_ticks", 8) or 8),
                        giveback_pct=float(getattr(self.config, "dte_single_px2_giveback_pct", 0.20) or 0.20),
                        lock_moon_pct=float(getattr(self.config, "dte_single_px2_lock_moon_pct", 2.0) or 2.0),
                        post_arm_be_floor=bool(getattr(self.config, "dte_single_px2_be_floor", True)),
                        underlying_usable=str(osnap.get("underlying_source") or "").strip().lower() != "unavailable",
                        stall_min_retention_of_mfe=float(
                            getattr(self.config, "dte_single_px2_stall_min_retention_of_mfe", 0.90)
                            or 0.0
                        ),
                        stall_min_mfe_pct=float(
                            getattr(self.config, "dte_single_px2_stall_min_mfe_pct", 0.50)
                            or 0.0
                        ),
                        high_mfe_trail_trigger_pct=float(
                            getattr(self.config, "dte_single_px2_high_mfe_trail_trigger_pct", 1.00)
                            or 0.0
                        ),
                        high_mfe_giveback_pct=float(
                            getattr(self.config, "dte_single_px2_high_mfe_giveback_pct", 0.30)
                            or 0.0
                        ),
                    )
                    apply_px2_state_updates(osnap, px2.state_updates)
                    price_runner_consumed_tp = True
                    px3_override = False
                    if (
                        px3_cfg.enabled
                        and px2.action == "exit"
                        and str(px2.reason or "") == "premium_trail"
                    ):
                        prem_hwm_now = max(
                            float(getattr(st, "max_pnl_pct", 0.0) or 0.0),
                            float(current_pnl_pct or 0.0),
                            float(osnap.get("px2_prem_hwm_pct") or 0.0),
                        )
                        hand = evaluate_px3_at_prem_trail(
                            realized_pnl_pct=float(current_pnl_pct),
                            prem_hwm_pct=float(prem_hwm_now),
                            und_hwm_fav_pct=px2_und_hwm_fav(osnap),
                            option_side=side_l,
                            und_hwm_price=px3_und_hwm_price_from_fav(
                                option_side=side_l,
                                entry_underlying=float(entry_u),
                                und_hwm_fav_pct=px2_und_hwm_fav(osnap),
                                current_underlying=float(current_underlying or 0.0),
                            ),
                            current_underlying=float(current_underlying or 0.0),
                            entry_ts=getattr(st, "opened_at", None),
                            now_ts=px3_now,
                            cfg=px3_cfg,
                        )
                        apply_px3_state_updates(osnap, hand.state_updates)
                        if hand.action == "continue":
                            px3_override = True
                            log.info(
                                "OPTIONS_STEALTH_PX3_HANDOFF | trade_id=%s | symbol=%s | "
                                "mode=%s | detail=%s | current_pnl_pct=%.4f | "
                                "prem_hwm_pct=%.4f | grep=OPTIONS_STEALTH_PX3",
                                position_id,
                                position_symbol,
                                str(hand.mode or ""),
                                str(hand.detail or ""),
                                float(current_pnl_pct),
                                float(prem_hwm_now),
                            )
                    if (
                        px3_cfg.enabled
                        and px2.action == "exit"
                        and str(px2.reason or "") == "mfe_take_profit"
                    ):
                        prem_hwm_now = max(
                            float(getattr(st, "max_pnl_pct", 0.0) or 0.0),
                            float(current_pnl_pct or 0.0),
                            float(osnap.get("px2_prem_hwm_pct") or 0.0),
                        )
                        hand = evaluate_px3_at_mfe_take_profit(
                            realized_pnl_pct=float(current_pnl_pct),
                            prem_hwm_pct=float(prem_hwm_now),
                            und_hwm_fav_pct=px2_und_hwm_fav(osnap),
                            option_side=side_l,
                            und_hwm_price=px3_und_hwm_price_from_fav(
                                option_side=side_l,
                                entry_underlying=float(entry_u),
                                und_hwm_fav_pct=px2_und_hwm_fav(osnap),
                                current_underlying=float(current_underlying or 0.0),
                            ),
                            current_underlying=float(current_underlying or 0.0),
                            entry_ts=getattr(st, "opened_at", None),
                            now_ts=px3_now,
                            cfg=px3_cfg,
                        )
                        apply_px3_state_updates(osnap, hand.state_updates)
                        if hand.action == "continue":
                            px3_override = True
                            log.info(
                                "OPTIONS_STEALTH_PX3_MFE_CONFIRM_PENDING | trade_id=%s | "
                                "symbol=%s | detail=%s | current_pnl_pct=%.4f | "
                                "prem_hwm_pct=%.4f | und_hwm_fav=%s | grep=OPTIONS_STEALTH_PX3",
                                position_id,
                                position_symbol,
                                str(hand.detail or ""),
                                float(current_pnl_pct),
                                float(prem_hwm_now),
                                (
                                    f"{float(px2_und_hwm_fav(osnap)):.5f}"
                                    if px2_und_hwm_fav(osnap) is not None
                                    else "none"
                                ),
                            )
                    if px2.action == "exit" and px2.reason and not px3_override:
                        exit_reason = normalize_exit_reason(px2.reason) or str(px2.reason)
                        entry_prem = float(st.entry_premium or st.entry_value or 0.0)
                        fill_prem = px2_be_floor_fill_premium(
                            detail=str(px2.detail or ""),
                            entry_premium=entry_prem,
                            current_premium=float(prem or 0.0),
                        )
                        if str(px2.detail or "") == "post_arm_be_floor" and entry_prem > 0:
                            osnap["px2_be_clamp"] = True
                            osnap["px2_market_bid"] = float(prem or 0.0)
                        px25_override = False
                        if px25_cfg.enabled:
                            prem_hwm_now = max(
                                float(getattr(st, "max_pnl_pct", 0.0) or 0.0),
                                float(current_pnl_pct or 0.0),
                                float(osnap.get("px2_prem_hwm_pct") or 0.0),
                            )
                            p25 = evaluate_px25_at_exit(
                                osnap=osnap,
                                candidate_reason=exit_reason,
                                current_pnl_pct=float(current_pnl_pct),
                                prem_hwm_pct=float(prem_hwm_now),
                                option_side=side_l,
                                und_hwm_price=px25_und_hwm_price_from_current_or_existing(
                                    osnap=osnap,
                                    option_side=side_l,
                                    current_underlying=float(current_underlying or 0.0),
                                ),
                                current_underlying=float(current_underlying or 0.0),
                                now_ts=px3_now,
                                cfg=px25_cfg,
                            )
                            apply_px25_state_updates(osnap, p25.state_updates)
                            if p25.action == "continue":
                                px25_override = True
                                log.info(
                                    "OPTIONS_STEALTH_PX25_HANDOFF | trade_id=%s | symbol=%s | "
                                    "from=px2 | reason=%s | detail=%s | current_pnl_pct=%.4f | "
                                    "prem_hwm_pct=%.4f | grep=OPTIONS_STEALTH_PX25",
                                    position_id,
                                    position_symbol,
                                    str(exit_reason or ""),
                                    str(p25.detail or ""),
                                    float(current_pnl_pct),
                                    float(prem_hwm_now),
                                )
                        if px25_override:
                            return None
                        log.info(
                            "OPTIONS_STEALTH_%s_EXIT | trade_id=%s | symbol=%s | detail=%s | "
                            "current_pnl_pct=%.4f | prem_hwm_pct=%.4f | und_hwm_fav=%s | "
                            "stall_und=%s | stall_prem=%s | premium=%.4f | fill=%.4f | "
                            "grep=OPTIONS_STEALTH_PX2",
                            str(exit_reason or "PX2").upper(),
                            position_id,
                            position_symbol,
                            str(px2.detail or ""),
                            float(current_pnl_pct),
                            float(osnap.get("px2_prem_hwm_pct") or 0.0),
                            (
                                f"{float(px2_und_hwm_fav(osnap)):.5f}"
                                if px2_und_hwm_fav(osnap) is not None
                                else "none"
                            ),
                            osnap.get("px2_stall_und_ticks"),
                            osnap.get("px2_stall_prem_ticks"),
                            float(prem or 0.0),
                            float(fill_prem or 0.0),
                        )
                        diag = self._build_exit_diagnostics(
                            position_id=position_id,
                            position_symbol=position_symbol,
                            block=block,
                            st=st,
                            osnap=osnap,
                            exit_premium=fill_prem,
                            exit_reason=exit_reason,
                            now=now,
                            prem_detail=detail,
                            premium_source_at_exit=prem_src,
                        )
                        diag["px2_detail"] = str(px2.detail or "")
                        diag["px2_und_hwm_fav"] = px2_und_hwm_fav(osnap)
                        diag["px2_stall_und_ticks"] = osnap.get("px2_stall_und_ticks")
                        diag["px2_stall_prem_ticks"] = osnap.get("px2_stall_prem_ticks")
                        diag["current_pnl_pct"] = float(current_pnl_pct)
                        diag["arm_trail_branch"] = "px2"
                        diag["px2_be_clamp"] = bool(osnap.get("px2_be_clamp"))
                        diag["px2_market_bid"] = osnap.get("px2_market_bid")
                        self._record_exit(exit_reason, pnl_pct, prem_src, diag)
                        telemetry["exit_diagnostics"] = diag
                        return self._finalize_exit_return(
                            fill_prem, exit_reason, telemetry, st=st, osnap=osnap, detail=detail
                        )


            # Strong (or thin-branch off): require live und for runner trail.
            # PX2: 0.50% und trail only after und HWM ≥0.5% AND prem ≥60%.
            run_price_runner = float(current_underlying or 0.0) > 0
            if px2_enabled:
                try:
                    from modules.px2_exit import px2_und_hwm_fav as _px2_und
                except Exception:
                    from px2_exit import px2_und_hwm_fav as _px2_und  # type: ignore
                try:
                    from modules.px3_exit import px3_active as _px3_on
                except Exception:
                    try:
                        from px3_exit import px3_active as _px3_on  # type: ignore
                    except Exception:
                        def _px3_on(_s):  # type: ignore
                            return False
                try:
                    from modules.px25_exit import px25_active as _px25_on
                except Exception:
                    try:
                        from px25_exit import px25_active as _px25_on  # type: ignore
                    except Exception:
                        def _px25_on(_s):  # type: ignore
                            return False
                if _px3_on(osnap) or _px25_on(osnap):
                    run_price_runner = False
                else:
                    und_need = float(getattr(self.config, "dte_single_px2_und_trail_need_pct", 0.005) or 0.005)
                    prem_need = float(getattr(self.config, "dte_single_px2_und_trail_prem_pct", 0.60) or 0.60)
                    und_hwm_now = _px2_und(osnap)
                    prem_hwm_now = max(
                        float(getattr(st, "max_pnl_pct", 0.0) or 0.0),
                        float(current_pnl_pct or 0.0),
                    )
                    run_price_runner = (
                        run_price_runner
                        and und_hwm_now is not None
                        and float(und_hwm_now) + 1e-12 >= und_need
                        and prem_hwm_now + 1e-12 >= prem_need
                    )
                    if run_price_runner:
                        trail_pct = float(
                            getattr(self.config, "dte_single_price_runner_wide_trail_pct", 0.005) or 0.005
                        )
                        trail_wide = True
                        osnap["price_runner_trail_wide"] = True
                        conf_ticks = 1
                        arm_price = 0.0
            if not run_price_runner:
                price_runner_consumed_tp = bool(past_gate) or bool(px2_enabled and past_gate)
            else:
                prem_floor, prem_floor_price_thin, price_peak_fav = resolve_runner_prem_floor_pct(
                    base_floor_pct=float(base_prem_floor),
                    price_thin_max_pct=float(thin_max),
                    price_thin_floor_pct=float(thin_floor),
                    option_side=side_l,
                    entry_underlying=float(entry_u),
                    price_hwm=osnap.get("price_runner_hwm"),
                    current_underlying=float(current_underlying),
                )
                ur = evaluate_price_runner_tick(
                    osnap=osnap,
                    option_side=side_l,
                    entry_underlying=float(entry_u),
                    current_underlying=float(current_underlying),
                    current_pnl_pct=float(current_pnl_pct),
                    past_premium_gate=past_gate,
                    arm_price_fav_pct=float(arm_price),
                    confirm_ticks_required=int(max(1, conf_ticks)),
                    trail_pct=float(trail_pct),
                    price_take_profit_pct=float(price_tp_pct),
                    prem_floor_pct=float(prem_floor),
                    now_iso=now.isoformat() if hasattr(now, "isoformat") else str(now or ""),
                )
                apply_price_runner_state_updates(osnap, ur.state_updates)
                if (not was_active) and bool(osnap.get("price_runner_active")):
                    fav_now = underlying_favorable_move_pct(
                        option_side=side_l,
                        entry_underlying=float(entry_u),
                        current_underlying=float(current_underlying),
                    )
                    log.info(
                        "OPTIONS_STEALTH_PRICE_RUNNER_ARMED | trade_id=%s | symbol=%s | side=%s | "
                        "price_fav_pct=%.5f | arm_pct=%.5f | confirm_ticks=%s | price_hwm=%.6f | "
                        "current_pnl_pct=%.4f | trail_pct=%.5f | trail_wide=%s | price_tp_pct=%.5f | "
                        "arm_trail_branch=%s",
                        position_id,
                        position_symbol,
                        side_l or "call",
                        float(fav_now) if fav_now is not None else float("nan"),
                        float(arm_price),
                        int(osnap.get("price_runner_confirm_ticks") or 0),
                        float(osnap.get("price_runner_hwm") or 0.0),
                        float(current_pnl_pct),
                        float(trail_pct),
                        bool(trail_wide),
                        float(price_tp_pct),
                        str(osnap.get("arm_trail_branch") or "trail_strong"),
                    )
                # Once past the +30% gate (or already armed), und-runner owns the winner path —
                # do not also fire immediate always-bank take_profit on this tick.
                if past_gate or bool(osnap.get("price_runner_active")):
                    price_runner_consumed_tp = True
                if ur.action == "exit" and ur.reason:
                    exit_reason = normalize_exit_reason(ur.reason) or str(ur.reason)
                    fav_now = underlying_favorable_move_pct(
                        option_side=side_l,
                        entry_underlying=float(entry_u),
                        current_underlying=float(current_underlying),
                    )
                    log.info(
                        "OPTIONS_STEALTH_%s_EXIT | trade_id=%s | symbol=%s | side=%s | detail=%s | "
                        "entry_underlying=%.6f | current_underlying=%.6f | price_fav_pct=%.5f | "
                        "price_hwm=%.6f | trail_pct=%.5f | price_tp_pct=%.5f | current_pnl_pct=%.4f | "
                        "premium=%.4f",
                        str(exit_reason or "PRICE_RUNNER").upper(),
                        position_id,
                        position_symbol,
                        side_l or "call",
                        str(ur.detail or ""),
                        float(entry_u),
                        float(current_underlying),
                        float(fav_now) if fav_now is not None else float("nan"),
                        float(osnap.get("price_runner_hwm") or 0.0),
                        float(trail_pct),
                        float(price_tp_pct),
                        float(current_pnl_pct),
                        float(prem or 0.0),
                    )
                    diag = self._build_exit_diagnostics(
                        position_id=position_id,
                        position_symbol=position_symbol,
                        block=block,
                        st=st,
                        osnap=osnap,
                        exit_premium=prem,
                        exit_reason=exit_reason,
                        now=now,
                        prem_detail=detail,
                        premium_source_at_exit=prem_src,
                    )
                    diag["price_runner_detail"] = str(ur.detail or "")
                    diag["price_runner_trail_pct"] = float(trail_pct)
                    diag["price_runner_take_profit_pct"] = float(price_tp_pct)
                    diag["price_runner_prem_floor_pct"] = float(prem_floor)
                    diag["price_runner_prem_floor_base_pct"] = float(base_prem_floor)
                    diag["price_runner_prem_floor_price_thin"] = bool(prem_floor_price_thin)
                    diag["price_runner_price_peak_fav_pct"] = (
                        float(price_peak_fav) if price_peak_fav is not None else None
                    )
                    diag["price_favorable_pct"] = float(fav_now) if fav_now is not None else None
                    diag["price_runner_hwm"] = float(osnap.get("price_runner_hwm") or 0.0)
                    diag["current_pnl_pct"] = float(current_pnl_pct)
                    diag["arm_trail_branch"] = str(osnap.get("arm_trail_branch") or "trail_strong")
                    if exit_reason == XER.BREAKEVEN.value:
                        diag["price_runner_prem_floor"] = True
                        if prem_floor_price_thin:
                            diag["price_runner_prem_floor_detail"] = "price_thin"
                    clear_price_runner_state(osnap)
                    self._record_exit(exit_reason, pnl_pct, prem_src, diag)
                    telemetry["exit_diagnostics"] = diag
                    return self._finalize_exit_return(
                        prem, exit_reason, telemetry, st=st, osnap=osnap, detail=detail
                    )

        # Always-bank TP30 REMOVED Sep 19 — PX2 stall/trail is the max-net winner path.
        # (mfe_take_profit as an exit *reason* from PX2/PX3 stall-bank remains valid.)

        # Phase 2 Hold Exit: Hold@0.55 at first base −10pp fire → reclaim → re-trail −10pp.
        try:
            from modules.mfe_hold_exit import (
                apply_hold_state_updates,
                clear_hold_state,
                evaluate_hold_tick,
                hold_dir_volume_allows_defer,
                hold_exit_eligible,
                hold_pp_allows_call_defer,
                hold_trigger_giveback_pct,
                resolve_same_side_dir_pct,
                start_hold_state,
            )
        except Exception:
            from mfe_hold_exit import (  # type: ignore
                apply_hold_state_updates,
                clear_hold_state,
                evaluate_hold_tick,
                hold_dir_volume_allows_defer,
                hold_exit_eligible,
                hold_pp_allows_call_defer,
                hold_trigger_giveback_pct,
                resolve_same_side_dir_pct,
                start_hold_state,
            )

        hold_eligible = hold_exit_eligible(
            enabled=bool(getattr(self.config, "hold_exit_enabled", False)),
            is_0dte_single=bool(is_0dte_single),
            is_spread=bool(is_spread),
        )
        hold_active = bool(hold_eligible and osnap.get("hold_exit_active"))
        # Hold defers the *same* base giveback band (default −10pp); do not widen to −20.
        effective_dd_exit = float(dd_exit)
        if hold_eligible and (not hold_active):
            hold_trig = float(hold_trigger_giveback_pct(self.config, dd_exit))
            # Only allow a tighter-or-equal Hold trigger vs base; never widen first fire.
            if hold_trig + 1e-12 <= float(dd_exit) + 1e-9:
                effective_dd_exit = hold_trig
            else:
                effective_dd_exit = float(dd_exit)

        if hold_active and protection_quote_ok:
            # Keep Hold-active ticks on current hub tech (same as dir-eval start).
            if isinstance(osnap, dict) and isinstance(position_metadata, dict):
                pst = position_metadata.get("path_session_tech")
                if isinstance(pst, dict):
                    for _k, _v in pst.items():
                        if _v is not None:
                            osnap[_k] = _v
            started_hold = _parse_stealth_dt(osnap.get("hold_exit_started_iso"))
            if started_hold is None:
                started_hold = now
                osnap["hold_exit_started_iso"] = now.isoformat()
            try:
                hold_elapsed_min = max(
                    0.0,
                    (self._normalize_dt(now) - self._normalize_dt(started_hold)).total_seconds() / 60.0,
                )
            except Exception:
                hold_elapsed_min = 0.0
            jump_down_hold = bool(
                premium_jump_this_tick
                and prev_eff
                and float(prev_eff) > 0
                and float(prem or 0.0) + 1e-12 < float(prev_eff)
            )
            hold_dec = evaluate_hold_tick(
                osnap=osnap,
                now_iso=now.isoformat(),
                elapsed_minutes=float(hold_elapsed_min),
                current_bid=float(prem or 0.0),
                current_pnl_pct=float(current_pnl_pct),
                reclaim_minutes=float(
                    getattr(self.config, "hold_exit_reclaim_minutes", 10.0) or 10.0
                ),
                hold_max_minutes=float(getattr(self.config, "hold_exit_max_minutes", 45.0) or 45.0),
                hard_floor_pnl_pct=float(
                    getattr(self.config, "hold_exit_hard_floor_pnl_pct", -0.05) or -0.05
                ),
                re_trail_giveback_pct=float(
                    getattr(self.config, "hold_exit_retrail_giveback_pct", 0.10) or 0.10
                ),
                require_trigger_bid_floor=bool(
                    getattr(self.config, "hold_exit_require_trigger_bid_floor", False)
                ),
                premium_jump_down=bool(jump_down_hold),
            )
            apply_hold_state_updates(osnap, hold_dec.state_updates)
            if hold_dec.log_event:
                log.info(
                    "%s | trade_id=%s | symbol=%s | detail=%s | elapsed_min=%.2f | "
                    "bid=%.6f | pnl=%.4f | trigger_bid=%s | trigger_hwm=%s | post_hwm=%s | "
                    "reclaimed=%s | dir_pct=%s",
                    str(hold_dec.log_event),
                    str(position_id or "-"),
                    position_symbol,
                    str(hold_dec.detail),
                    float(hold_elapsed_min),
                    float(prem or 0.0),
                    float(current_pnl_pct),
                    osnap.get("hold_exit_trigger_bid"),
                    osnap.get("hold_exit_trigger_hwm"),
                    osnap.get("hold_exit_post_hwm"),
                    str(bool(osnap.get("hold_exit_reclaimed"))).lower(),
                    osnap.get("hold_exit_dir_pct"),
                )
            if hold_dec.action == "exit":
                diag = self._build_exit_diagnostics(
                    position_id=position_id,
                    position_symbol=position_symbol,
                    block=block,
                    st=st,
                    osnap=osnap,
                    exit_premium=prem,
                    exit_reason=XER.HOLD_EXIT.value,
                    now=now,
                    prem_detail=detail,
                    premium_source_at_exit=prem_src,
                )
                diag["drawdown_from_max_pct"] = float(drawdown_from_max_pct)
                diag["hold_exit_detail"] = str(hold_dec.detail)
                diag["hold_exit_elapsed_minutes"] = float(hold_elapsed_min)
                diag["hold_exit_trigger_bid"] = osnap.get("hold_exit_trigger_bid")
                diag["hold_exit_trigger_hwm"] = osnap.get("hold_exit_trigger_hwm")
                diag["hold_exit_post_hwm"] = osnap.get("hold_exit_post_hwm")
                diag["hold_exit_reclaimed"] = bool(osnap.get("hold_exit_reclaimed"))
                diag["hold_exit_dir_pct"] = osnap.get("hold_exit_dir_pct")
                self._record_exit(XER.HOLD_EXIT.value, pnl_pct, prem_src, diag)
                telemetry["exit_diagnostics"] = diag
                clear_hold_state(osnap)
                return self._finalize_exit_return(
                    prem, XER.HOLD_EXIT.value, telemetry, st=st, osnap=osnap, detail=detail
                )
            # Still holding — do not latch base mfe_giveback this tick.
            osnap["hold_exit_suppress_mfe_latch"] = True
        else:
            osnap.pop("hold_exit_suppress_mfe_latch", None)

        early_prem_trail_before_cutoff = (
            self._0dte_before_orb2_tl_cutoff(is_0dte_single=bool(is_0dte_single), now=now)
            if bool(
                getattr(
                    self.config,
                    "dte_single_early_prem_trail_before_cutoff_only",
                    True,
                )
            )
            else True
        )
        early_prem_trail_hit = early_prem_trail_should_exit(
            enabled=bool(getattr(self.config, "dte_single_early_prem_trail_enabled", False)),
            is_0dte_single=bool(is_0dte_single),
            before_cutoff=bool(early_prem_trail_before_cutoff),
            protection_quote_ok=bool(protection_quote_ok),
            hold_suppressed=bool(osnap.get("hold_exit_suppress_mfe_latch")),
            max_pnl_pct=float(st.max_pnl_pct or 0.0),
            current_pnl_pct=float(current_pnl_pct or 0.0),
            min_hwm_pct=float(
                getattr(self.config, "dte_single_early_prem_trail_min_hwm_pct", 1.75)
                or 1.75
            ),
            trail_pct=float(
                getattr(self.config, "dte_single_early_prem_trail_pct", 0.05)
                or 0.05
            ),
            min_current_pnl_pct=float(
                getattr(
                    self.config,
                    "dte_single_early_prem_trail_min_current_pnl_pct",
                    0.75,
                )
                or 0.75
            ),
        )
        if early_prem_trail_hit:
            diag = self._build_exit_diagnostics(
                position_id=position_id,
                position_symbol=position_symbol,
                block=block,
                st=st,
                osnap=osnap,
                exit_premium=prem,
                exit_reason=XER.PREM_TRAIL.value,
                now=now,
                prem_detail=detail,
                premium_source_at_exit=prem_src,
            )
            diag["early_prem_trail"] = True
            diag["early_prem_trail_min_hwm_pct"] = float(
                getattr(self.config, "dte_single_early_prem_trail_min_hwm_pct", 1.75)
                or 1.75
            )
            diag["early_prem_trail_pct"] = float(
                getattr(self.config, "dte_single_early_prem_trail_pct", 0.05)
                or 0.05
            )
            diag["early_prem_trail_min_current_pnl_pct"] = float(
                getattr(
                    self.config,
                    "dte_single_early_prem_trail_min_current_pnl_pct",
                    0.75,
                )
                or 0.75
            )
            diag["early_prem_trail_before_cutoff"] = bool(early_prem_trail_before_cutoff)
            diag["drawdown_from_max_pct"] = float(drawdown_from_max_pct)
            osnap["early_prem_trail"] = True
            osnap["early_prem_trail_exit"] = True
            osnap["early_prem_trail_drawdown_pct"] = float(drawdown_from_max_pct)
            log.info(
                "OPTION_EARLY_PREM_TRAIL_EXIT | trade_id=%s | symbol=%s | "
                "current_pnl_pct=%.4f | max_pnl_pct=%.4f | trail_pct=%.4f | "
                "min_hwm_pct=%.4f | min_current_pnl_pct=%.4f | before_cutoff=%s",
                str(position_id or "-"),
                position_symbol,
                float(current_pnl_pct or 0.0),
                float(st.max_pnl_pct or 0.0),
                float(diag["early_prem_trail_pct"]),
                float(diag["early_prem_trail_min_hwm_pct"]),
                float(diag["early_prem_trail_min_current_pnl_pct"]),
                str(bool(early_prem_trail_before_cutoff)).lower(),
            )
            self._record_exit(XER.PREM_TRAIL.value, pnl_pct, prem_src, diag)
            telemetry["exit_diagnostics"] = diag
            return self._finalize_exit_return(
                prem, XER.PREM_TRAIL.value, telemetry, st=st, osnap=osnap, detail=detail
            )

        mfe_trail_live = bool(profile.max_pnl_drawdown_enabled) or self._0dte_mfe_trail_with_price_trail(
            is_0dte_single=bool(is_0dte_single),
            now=now,
        )
        if mfe_trail_live and (not bool(profile.max_pnl_drawdown_enabled)):
            osnap["mfe_trail_with_price_trail"] = True
        mfe_dd = giveback_drawdown_pct(
            osnap,
            session_max_pnl_pct=float(st.max_pnl_pct or 0.0),
            current_pnl_pct=float(current_pnl_pct or 0.0),
        )
        if (
            mfe_trail_live
            and protection_quote_ok
            and float(st.max_pnl_pct) >= float(dd_trig)
            and float(mfe_dd) >= float(effective_dd_exit)
            and (not bool(osnap.get("hold_exit_suppress_mfe_latch")))
        ):
            entry_chain_mfe = str(osnap.get("entry_chain_source") or "")
            max_sp_mfe = float(getattr(self.config, "tline_exit_grade_max_spread_pct", 0.30) or 0.30)
            mfe_blocked = mfe_giveback_blocked_for_synthetic_entry(
                entry_chain_source=entry_chain_mfe,
                mark_is_exit_grade=bool(osnap.get("mark_is_exit_grade")),
                premium_jump_detected=bool(st.premium_jump_detected or osnap.get("premium_jump_detected")),
                max_pnl_pct=float(st.max_pnl_pct or 0.0),
                spread_pct=(detail.get("spread_pct") if isinstance(detail, dict) else None),
                max_spread_pct=max_sp_mfe,
                drawdown_from_max_pct=float(drawdown_from_max_pct),
                mfe_giveback_exit_pct=float(effective_dd_exit),
                premium_source=str(prem_src or ""),
                protection_quote_ok=bool(protection_quote_ok),
            )
            if mfe_blocked:
                osnap["mfe_giveback_deferred_synthetic_entry"] = True
                defer_started = _parse_stealth_dt(osnap.get("mfe_giveback_defer_started_iso"))
                if defer_started is None:
                    osnap["mfe_giveback_defer_started_iso"] = now.isoformat()
                    defer_started = now
                defer_max = float(
                    getattr(self.config, "mfe_giveback_defer_max_seconds", 90.0) or 90.0
                )
                try:
                    defer_age_sec = max(
                        0.0,
                        (self._normalize_dt(now) - self._normalize_dt(defer_started)).total_seconds(),
                    )
                except Exception:
                    defer_age_sec = 0.0
                # After defer window, force giveback on trusted protection quote (bid path).
                if (
                    defer_max > 0
                    and defer_age_sec + 1e-9 >= defer_max
                    and protection_quote_ok
                    and float(drawdown_from_max_pct) + 1e-12 >= float(effective_dd_exit)
                ):
                    mfe_blocked = False
                    osnap["mfe_giveback_defer_timeout_forced"] = True
                    log.warning(
                        "OPTION_MFE_GIVEBACK_DEFER_TIMEOUT | trade_id=%s | symbol=%s | "
                        "defer_age_sec=%.1f | defer_max_sec=%.1f | current_pnl_pct=%.4f | "
                        "max_pnl_pct=%.4f | drawdown_from_max_pct=%.4f | mark_is_exit_grade=%s | "
                        "protection_quote_ok=%s",
                        str(position_id or "-"),
                        position_symbol,
                        float(defer_age_sec),
                        float(defer_max),
                        float(current_pnl_pct),
                        float(st.max_pnl_pct or 0.0),
                        float(drawdown_from_max_pct),
                        str(bool(osnap.get("mark_is_exit_grade"))).lower(),
                        str(bool(protection_quote_ok)).lower(),
                    )
                else:
                    log.info(
                        "OPTION_MFE_GIVEBACK_DEFERRED | trade_id=%s | symbol=%s | strategy_type=%s | "
                        "current_pnl_pct=%.4f | max_pnl_pct=%.4f | drawdown_from_max_pct=%.4f | "
                        "mark_is_exit_grade=%s | premium_jump_detected=%s | protection_quote_ok=%s | "
                        "defer_age_sec=%.1f | defer_max_sec=%.1f",
                        str(position_id or "-"),
                        position_symbol,
                        str(st.strategy_type or ""),
                        float(current_pnl_pct),
                        float(st.max_pnl_pct or 0.0),
                        float(drawdown_from_max_pct),
                        str(bool(osnap.get("mark_is_exit_grade"))).lower(),
                        str(bool(st.premium_jump_detected or osnap.get("premium_jump_detected"))).lower(),
                        str(bool(protection_quote_ok)).lower(),
                        float(defer_age_sec),
                        float(defer_max),
                    )
            if not mfe_blocked:
                # P0 gap-down guard (DELL 2026-07-24): evaluate() uses mutate_premium_state=False
                # so HWM spike-reset never runs; a single cliff print must not latch giveback.
                gap_defer = False
                if bool(getattr(self.config, "mfe_giveback_gap_guard_enabled", True)):
                    jump_down = bool(
                        premium_jump_this_tick
                        and prev_eff
                        and float(prev_eff) > 0
                        and float(prem or 0.0) + 1e-12 < float(prev_eff)
                    )
                    adverse_gap = mfe_giveback_adverse_gap_tick(
                        premium_jump_down=jump_down,
                        drawdown_from_max_pct=float(drawdown_from_max_pct),
                        mfe_giveback_exit_pct=float(effective_dd_exit),
                        overshoot_pct=float(
                            getattr(self.config, "mfe_giveback_gap_overshoot_pct", 0.05) or 0.05
                        ),
                    )
                    reused_mark = bool(
                        (isinstance(detail, dict) and detail.get("reused_last_exact"))
                        or (isinstance(option_quote, dict) and option_quote.get("reused_last_exact"))
                        or str(prem_src or "").lower().find("reused") >= 0
                    )
                    awaiting_md = bool(
                        position_metadata.get("awaiting_market_data")
                        if isinstance(position_metadata, dict)
                        else False
                    )
                    fp = mfe_giveback_gap_quote_fingerprint(
                        broker_quote_timestamp=(
                            (detail.get("broker_quote_timestamp") if isinstance(detail, dict) else None)
                            or (option_quote.get("broker_quote_timestamp") if isinstance(option_quote, dict) else None)
                            or (option_quote.get("quote_timestamp") if isinstance(option_quote, dict) else None)
                        ),
                        option_bid=(detail.get("option_bid") if isinstance(detail, dict) else None),
                        quote_age_seconds=(
                            detail.get("quote_age_seconds") if isinstance(detail, dict) else None
                        ),
                        monitor_tick_id=(
                            position_metadata.get("_unified_0dte_monitor_tick_id")
                            if isinstance(position_metadata, dict)
                            else None
                        )
                        or osnap.get("last_monitor_tick_id"),
                        premium=prem,
                    )
                    fresh = mfe_giveback_gap_observation_is_fresh(
                        reused_last_exact=reused_mark,
                        awaiting_market_data=awaiting_md,
                        quote_fingerprint=fp,
                        last_confirm_fingerprint=str(
                            osnap.get("mfe_giveback_gap_last_confirm_fp") or ""
                        ),
                        premium_source=str(prem_src or ""),
                    )
                    prior_count = 0
                    try:
                        prior_count = int(osnap.get("mfe_giveback_gap_confirm_count") or 0)
                    except (TypeError, ValueError):
                        prior_count = 0
                    should_defer, new_count, new_pending = mfe_giveback_gap_guard_should_defer(
                        enabled=True,
                        adverse_gap_tick=bool(adverse_gap),
                        confirm_count=prior_count,
                        confirm_required=int(
                            getattr(self.config, "mfe_giveback_gap_confirm_ticks", 2) or 2
                        ),
                        gap_pending=bool(osnap.get("mfe_giveback_gap_pending")),
                        observation_fresh=bool(fresh),
                    )
                    if should_defer:
                        gap_defer = True
                        osnap["mfe_giveback_gap_confirm_count"] = int(new_count)
                        osnap["mfe_giveback_gap_pending"] = bool(new_pending)
                        if bool(adverse_gap) and bool(fresh) and int(new_count) > int(prior_count):
                            osnap["mfe_giveback_gap_last_confirm_fp"] = fp
                        log.info(
                            "OPTION_MFE_GIVEBACK_GAP_DEFERRED | trade_id=%s | symbol=%s | "
                            "current_pnl_pct=%.4f | max_pnl_pct=%.4f | drawdown_from_max_pct=%.4f | "
                            "exit_pct=%.4f | jump_down=%s | adverse_gap=%s | observation_fresh=%s | "
                            "reused_last_exact=%s | confirm_count=%s | confirm_required=%s | "
                            "prev_eff=%s | prem=%.6f",
                            str(position_id or "-"),
                            position_symbol,
                            float(current_pnl_pct),
                            float(st.max_pnl_pct or 0.0),
                            float(drawdown_from_max_pct),
                            float(effective_dd_exit),
                            str(bool(jump_down)).lower(),
                            str(bool(adverse_gap)).lower(),
                            str(bool(fresh)).lower(),
                            str(bool(reused_mark)).lower(),
                            int(new_count),
                            int(getattr(self.config, "mfe_giveback_gap_confirm_ticks", 2) or 2),
                            f"{float(prev_eff):.6f}" if prev_eff else "n/a",
                            float(prem or 0.0),
                        )
                        try:
                            from modules.exit_observation_telemetry import (
                                log_mfe_decision,
                                mfe_decision_fields,
                            )

                            log_mfe_decision(
                                "MFE_GIVEBACK_GAP_DEFERRED",
                                mfe_decision_fields(
                                    trade_id=str(position_id or ""),
                                    current_bid=float(detail.get("option_bid") or prem or 0.0) or None,
                                    entry_premium=float(st.entry_premium or 0.0) or None,
                                    hwm_bid=float(st.premium_hwm or 0.0) or None,
                                    hwm_pnl_pct=float(st.max_pnl_pct or 0.0),
                                    giveback_exit_pct=float(effective_dd_exit),
                                    current_drawdown_pp=float(drawdown_from_max_pct),
                                    quote_age=detail.get("quote_age_seconds"),
                                    quote_source=str(prem_src or ""),
                                    confirmation_count=int(new_count),
                                ),
                            )
                        except Exception:
                            pass
                    else:
                        # Confirmed adverse gap OR clean-band continuation after pending.
                        if bool(osnap.get("mfe_giveback_gap_pending")) or int(prior_count or 0) > 0:
                            try:
                                from modules.exit_observation_telemetry import (
                                    log_mfe_decision,
                                    mfe_decision_fields,
                                )

                                evt = (
                                    "MFE_GIVEBACK_GAP_CONFIRMED"
                                    if bool(adverse_gap)
                                    else "MFE_GIVEBACK_CLEAN_BAND_EXIT"
                                )
                                log_mfe_decision(
                                    evt,
                                    mfe_decision_fields(
                                        trade_id=str(position_id or ""),
                                        current_bid=float(detail.get("option_bid") or prem or 0.0)
                                        or None,
                                        entry_premium=float(st.entry_premium or 0.0) or None,
                                        hwm_bid=float(st.premium_hwm or 0.0) or None,
                                        hwm_pnl_pct=float(st.max_pnl_pct or 0.0),
                                        giveback_exit_pct=float(dd_exit),
                                        current_drawdown_pp=float(drawdown_from_max_pct),
                                        quote_age=detail.get("quote_age_seconds"),
                                        quote_source=str(prem_src or ""),
                                        confirmation_count=int(new_count or prior_count or 0),
                                    ),
                                )
                            except Exception:
                                pass
                        osnap.pop("mfe_giveback_gap_confirm_count", None)
                        osnap.pop("mfe_giveback_gap_pending", None)
                        osnap.pop("mfe_giveback_gap_last_confirm_fp", None)
                if not gap_defer:
                    if osnap.get("mfe_giveback_defer_started_iso") or osnap.get(
                        "mfe_giveback_deferred_synthetic_entry"
                    ):
                        try:
                            from modules.exit_observation_telemetry import (
                                log_mfe_decision,
                                mfe_decision_fields,
                            )

                            log_mfe_decision(
                                "MFE_GIVEBACK_DEFER_CANCELLED",
                                mfe_decision_fields(
                                    trade_id=str(position_id or ""),
                                    current_bid=float(detail.get("option_bid") or prem or 0.0) or None,
                                    entry_premium=float(st.entry_premium or 0.0) or None,
                                    hwm_bid=float(st.premium_hwm or 0.0) or None,
                                    hwm_pnl_pct=float(st.max_pnl_pct or 0.0),
                                    giveback_exit_pct=float(effective_dd_exit),
                                    current_drawdown_pp=float(drawdown_from_max_pct),
                                    quote_age=detail.get("quote_age_seconds"),
                                    quote_source=str(prem_src or ""),
                                    confirmation_count=osnap.get("mfe_giveback_gap_confirm_count"),
                                ),
                            )
                        except Exception:
                            pass
                    osnap.pop("mfe_giveback_defer_started_iso", None)
                    osnap.pop("mfe_giveback_deferred_synthetic_entry", None)

                    # Phase 2: enter Hold@0.55 instead of latching base mfe_giveback.
                    if hold_eligible and (not bool(osnap.get("hold_exit_active"))):
                        hold_option_side = str(
                            getattr(st, "option_side", "")
                            or osnap.get("option_side")
                            or position_metadata.get("option_side")
                            or ""
                        )
                        pp_ok, pp_note = hold_pp_allows_call_defer(
                            option_side=hold_option_side,
                        )
                        if not pp_ok:
                            log.info(
                                "HOLD_EXIT_PP_CALL_REJECT | trade_id=%s | symbol=%s | "
                                "option_side=%s | note=%s | action=mfe_giveback",
                                str(position_id or "-"),
                                position_symbol,
                                hold_option_side,
                                pp_note,
                            )
                            try:
                                from modules.exit_observation_telemetry import (
                                    log_mfe_decision,
                                    mfe_decision_fields,
                                )

                                fields = mfe_decision_fields(
                                    trade_id=str(position_id or ""),
                                    current_bid=float(detail.get("option_bid") or prem or 0.0)
                                    or None,
                                    entry_premium=float(st.entry_premium or 0.0) or None,
                                    hwm_bid=float(st.premium_hwm or 0.0) or None,
                                    hwm_pnl_pct=float(st.max_pnl_pct or 0.0),
                                    giveback_exit_pct=float(effective_dd_exit),
                                    current_drawdown_pp=float(drawdown_from_max_pct),
                                    quote_age=detail.get("quote_age_seconds"),
                                    quote_source=str(prem_src or ""),
                                    confirmation_count=0,
                                )
                                fields["hold_pp_note"] = pp_note
                                fields["option_side"] = hold_option_side
                                log_mfe_decision("HOLD_EXIT_PP_CALL_REJECT", fields)
                            except Exception:
                                pass
                            # Fall through to normal mfe_giveback latch below.
                        else:
                            # Current hub session tech for Hold review (VWAP/MACD/RSI/ATR/RS/BB).
                            # Overwrite with path_session_tech when present — never leave stale entry.
                            if isinstance(osnap, dict):
                                pst = None
                                if isinstance(position_metadata, dict):
                                    pst = position_metadata.get("path_session_tech")
                                if isinstance(pst, dict):
                                    for _k, _v in pst.items():
                                        if _v is not None:
                                            osnap[_k] = _v
                                for _flag in (
                                    "volume_ratio_missing",
                                    "rsi_missing",
                                    "macd_missing",
                                    "vwap_missing",
                                ):
                                    if isinstance(pst, dict) and _flag in pst:
                                        osnap[_flag] = bool(pst.get(_flag))
                                    elif (
                                        isinstance(position_metadata, dict)
                                        and _flag in position_metadata
                                    ):
                                        osnap[_flag] = bool(position_metadata.get(_flag))
                                # Persist a compact Hold tech snapshot for diagnostics.
                                osnap["hold_exit_tech"] = {
                                    k: osnap.get(k)
                                    for k in (
                                        "vwap",
                                        "vwap_distance_pct",
                                        "macd",
                                        "macd_histogram",
                                        "rsi",
                                        "atr",
                                        "momentum_10",
                                        "bollinger_middle",
                                        "bollinger_upper",
                                        "bollinger_lower",
                                        "rs_vs_spy",
                                        "volume_ratio",
                                    )
                                    if osnap.get(k) is not None
                                }
                            dir_pct, dir_src = resolve_same_side_dir_pct(
                                osnap=osnap if isinstance(osnap, dict) else {},
                                position_metadata=position_metadata
                                if isinstance(position_metadata, dict)
                                else {},
                                option_side=hold_option_side,
                            )
                            if isinstance(osnap, dict) and dir_pct is not None:
                                osnap["hold_exit_dir_pct"] = float(dir_pct)
                            dir_ok, dir_note = hold_dir_volume_allows_defer(
                                dir_pct=dir_pct,
                                min_pct=float(
                                    getattr(self.config, "hold_exit_dir_volume_min_pct", 0.55)
                                    or 0.55
                                ),
                                require_dir=bool(
                                    getattr(self.config, "hold_exit_require_dir_volume", True)
                                ),
                            )
                            if not dir_ok:
                                log.info(
                                    "HOLD_EXIT_DIR_REJECT | trade_id=%s | symbol=%s | dir_pct=%s | "
                                    "dir_source=%s | note=%s | action=mfe_giveback",
                                    str(position_id or "-"),
                                    position_symbol,
                                    dir_pct,
                                    dir_src,
                                    dir_note,
                                )
                                try:
                                    from modules.exit_observation_telemetry import (
                                        log_mfe_decision,
                                        mfe_decision_fields,
                                    )

                                    fields = mfe_decision_fields(
                                        trade_id=str(position_id or ""),
                                        current_bid=float(detail.get("option_bid") or prem or 0.0)
                                        or None,
                                        entry_premium=float(st.entry_premium or 0.0) or None,
                                        hwm_bid=float(st.premium_hwm or 0.0) or None,
                                        hwm_pnl_pct=float(st.max_pnl_pct or 0.0),
                                        giveback_exit_pct=float(effective_dd_exit),
                                        current_drawdown_pp=float(drawdown_from_max_pct),
                                        quote_age=detail.get("quote_age_seconds"),
                                        quote_source=str(prem_src or ""),
                                        confirmation_count=0,
                                    )
                                    fields["hold_dir_pct"] = dir_pct
                                    fields["hold_dir_note"] = dir_note
                                    log_mfe_decision("HOLD_EXIT_DIR_REJECT", fields)
                                except Exception:
                                    pass
                                # Fall through to normal mfe_giveback latch below.
                            else:
                                hold_state = start_hold_state(
                                    now_iso=now.isoformat(),
                                    trigger_bid=float(prem or 0.0),
                                    trigger_hwm=float(st.premium_hwm or prem or 0.0),
                                    trigger_hwm_pnl=float(st.max_pnl_pct or 0.0),
                                    entry_premium=float(st.entry_premium or 0.0),
                                    dir_pct=dir_pct,
                                    dir_source=dir_src,
                                )
                                osnap.update(hold_state)
                                osnap.pop("mfe_giveback_defer_timeout_forced", None)
                                log.info(
                                    "HOLD_EXIT_ENTERED | trade_id=%s | symbol=%s | trigger_bid=%.6f | "
                                    "trigger_hwm=%.6f | trigger_hwm_pnl=%.4f | drawdown_from_max_pct=%.4f | "
                                    "hold_trigger_giveback=%.4f | reclaim_min=%.1f | hold_max_min=%.1f | "
                                    "dir_pct=%s | dir_source=%s | dir_note=%s | pp_note=%s",
                                    str(position_id or "-"),
                                    position_symbol,
                                    float(prem or 0.0),
                                    float(st.premium_hwm or 0.0),
                                    float(st.max_pnl_pct or 0.0),
                                    float(drawdown_from_max_pct),
                                    float(effective_dd_exit),
                                    float(
                                        getattr(self.config, "hold_exit_reclaim_minutes", 10.0)
                                        or 10.0
                                    ),
                                    float(
                                        getattr(self.config, "hold_exit_max_minutes", 45.0) or 45.0
                                    ),
                                    dir_pct,
                                    dir_src,
                                    dir_note,
                                    pp_note,
                                )
                                try:
                                    from modules.exit_observation_telemetry import (
                                        log_mfe_decision,
                                        mfe_decision_fields,
                                    )

                                    fields = mfe_decision_fields(
                                        trade_id=str(position_id or ""),
                                        current_bid=float(detail.get("option_bid") or prem or 0.0)
                                        or None,
                                        entry_premium=float(st.entry_premium or 0.0) or None,
                                        hwm_bid=float(st.premium_hwm or 0.0) or None,
                                        hwm_pnl_pct=float(st.max_pnl_pct or 0.0),
                                        giveback_exit_pct=float(effective_dd_exit),
                                        current_drawdown_pp=float(drawdown_from_max_pct),
                                        quote_age=detail.get("quote_age_seconds"),
                                        quote_source=str(prem_src or ""),
                                        confirmation_count=0,
                                    )
                                    fields["hold_dir_pct"] = dir_pct
                                    fields["hold_dir_note"] = dir_note
                                    fields["hold_pp_note"] = pp_note
                                    log_mfe_decision("HOLD_EXIT_ENTERED", fields)
                                except Exception:
                                    pass
                                return None

                    try:
                        from modules.exit_observation_telemetry import (
                            log_mfe_decision,
                            mfe_decision_fields,
                        )

                        log_mfe_decision(
                            "MFE_GIVEBACK_OBSERVED",
                            mfe_decision_fields(
                                trade_id=str(position_id or ""),
                                current_bid=float(detail.get("option_bid") or prem or 0.0) or None,
                                entry_premium=float(st.entry_premium or 0.0) or None,
                                hwm_bid=float(st.premium_hwm or 0.0) or None,
                                hwm_pnl_pct=float(st.max_pnl_pct or 0.0),
                                giveback_exit_pct=float(effective_dd_exit),
                                current_drawdown_pp=float(drawdown_from_max_pct),
                                quote_age=detail.get("quote_age_seconds"),
                                quote_source=str(prem_src or ""),
                                confirmation_count=osnap.get("mfe_giveback_gap_confirm_count"),
                            ),
                        )
                    except Exception:
                        pass
                    log.info(
                        "OPTIONS_STEALTH_MAX_PNL_DRAWDOWN_EXIT | trade_id=%s | symbol=%s | current_pnl_pct=%.4f | max_pnl_pct=%.4f | "
                        "drawdown_from_max_pct=%.4f | trigger_pct=%.4f | exit_pct=%.4f | defer_timeout_forced=%s",
                        position_id,
                        position_symbol,
                        float(current_pnl_pct),
                        float(st.max_pnl_pct),
                        float(drawdown_from_max_pct),
                        float(dd_trig),
                        float(effective_dd_exit),
                        str(bool(osnap.pop("mfe_giveback_defer_timeout_forced", False))).lower(),
                    )
                    diag = self._build_exit_diagnostics(
                        position_id=position_id,
                        position_symbol=position_symbol,
                        block=block,
                        st=st,
                        osnap=osnap,
                        exit_premium=prem,
                        exit_reason=XER.MFE_GIVEBACK.value,
                        now=now,
                        prem_detail=detail,
                        premium_source_at_exit=prem_src,
                    )
                    diag["drawdown_from_max_pct"] = float(drawdown_from_max_pct)
                    _pre_arm_floor_active = _apply_pre_arm_profit_protect_floor(XER.MFE_GIVEBACK.value)
                    _low_arm_floor_active = False
                    _weak_cap_floor_active = False
                    if not _pre_arm_floor_active:
                        _low_arm_floor_active = _apply_low_arm_profit_protect_floor(XER.MFE_GIVEBACK.value)
                        if not _low_arm_floor_active:
                            _weak_cap_floor_active = _apply_weak_loss_cap_floor(XER.MFE_GIVEBACK.value)
                    if _pre_arm_floor_active:
                        diag["pre_arm_profit_protect"] = True
                        diag["pre_arm_profit_protect_floor_pnl_pct"] = float(pre_arm_floor_pct)
                        diag["pre_arm_profit_protect_min_current_pnl_pct"] = float(pre_arm_min_current_pnl)
                    if _low_arm_floor_active:
                        _annotate_low_arm_profit_protect(diag)
                    if _weak_cap_floor_active:
                        _annotate_weak_loss_cap(diag)
                    if (_pre_arm_floor_active or _low_arm_floor_active or _weak_cap_floor_active) and _bid_aware_be_divert_after_wrap():
                        return None
                    self._record_exit(
                        XER.MFE_GIVEBACK.value,
                        _profit_protect_record_pnl(
                            _pre_arm_floor_active,
                            _low_arm_floor_active,
                            pnl_pct,
                        ),
                        prem_src,
                        diag,
                    )
                    telemetry["exit_diagnostics"] = diag
                    return self._finalize_exit_return(prem, XER.MFE_GIVEBACK.value, telemetry, st=st, osnap=osnap, detail=detail)
        else:
            osnap.pop("mfe_giveback_gap_confirm_count", None)
            osnap.pop("mfe_giveback_gap_pending", None)

        # Never-armed BE/scratch: BE ≥60m [−0.05%, +2%); scratch ≥62m < −0.05%.
        # Early scratch ≥30m when peak MFE < 10% and red (Aug 12).
        _be_bid = None
        try:
            _be_bid = float(prem) if prem is not None else None
        except (TypeError, ValueError):
            _be_bid = None
        from types import SimpleNamespace

        _be_entry = float(
            osnap.get("entry_premium")
            or block.get("entry_premium")
            or position_metadata.get("entry_price")
            or 0.0
        )
        _be_qty = int(position_metadata.get("quantity") or block.get("quantity") or 1)
        _be_pos = SimpleNamespace(
            position_id=position_id,
            symbol=position_symbol,
            entry_price=_be_entry,
            quantity=max(1, _be_qty),
            position_type=str(position_metadata.get("position_type") or "lotto"),
            metadata=position_metadata,
        )
        _scratch_minutes = float(getattr(self.config, "dte_scratch_exit_minutes", 62.0) or 62.0)
        _scratch_early_min = float(
            getattr(self.config, "dte_scratch_early_minutes", 30.0) or 30.0
        )
        _scratch_early_mfe = float(
            getattr(self.config, "dte_scratch_early_max_mfe_pct", 0.10) or 0.10
        )
        _scratch_floor = float(
            getattr(self.config, "dte_scratch_below_pnl_pct", -0.0005) or -0.0005
        )
        if (
            is_0dte_single
            and float(st.max_pnl_pct or 0.0) + 1e-12 < float(_scratch_early_mfe)
            and float(current_pnl_pct) + 1e-12 < float(_scratch_floor)
            and float(_scratch_early_min) > 0
            and float(_scratch_early_min) < float(_scratch_minutes)
        ):
            _scratch_minutes = float(_scratch_early_min)
        _be_reason, _be_snap = classify_breakeven_or_scratch_exit(
            is_0dte_single=bool(is_0dte_single),
            held_minutes=float(held_min),
            current_pnl_pct=float(current_pnl_pct),
            breakeven_minutes=float(getattr(self.config, "dte_breakeven_exit_minutes", 60.0) or 60.0),
            scratch_minutes=float(_scratch_minutes),
            exit_below_pnl_pct=float(
                getattr(self.config, "dte_breakeven_exit_max_pnl_pct", 0.02) or 0.02
            ),
            scratch_below_pnl_pct=float(_scratch_floor),
            breakeven_enabled=bool(getattr(self.config, "dte_breakeven_exit_enabled", True)),
            max_pnl_pct=float(st.max_pnl_pct or 0.0),
            mfe_giveback_trigger_pct=float(self._0dte_winner_arm_pct()),
            current_executable_bid=_be_bid,
            position=_be_pos,
        )
        _near_arm_protect_mfe = float(
            getattr(self.config, "dte_near_arm_protect_mfe_pct", 0.20) or 0.0
        )
        _near_arm_protect_red = near_arm_mfe_protects_weak_red_exit(
            is_0dte_single=bool(is_0dte_single),
            max_pnl_pct=float(st.max_pnl_pct or 0.0),
            current_pnl_pct=float(current_pnl_pct),
            protect_mfe_pct=float(_near_arm_protect_mfe),
            winner_arm_pct=float(self._0dte_winner_arm_pct()),
            mark_usable=not bool(detail.get("absurd_live_premium_rejected")),
        )
        # Band B: try pre-ARM / low-ARM / CAP wrap before near-ARM skip (don't starve rescue).
        if (
            _be_reason == XER.SCRATCH.value
            and _near_arm_protect_red
            and not _weak_exit_wrap_would_fire(XER.SCRATCH.value)
        ):
            if isinstance(osnap, dict):
                _snap = dict(_be_snap or {})
                _snap["near_arm_protect_mfe_pct"] = float(_near_arm_protect_mfe)
                _snap["near_arm_protect_skip"] = True
                osnap["fee_aware_breakeven"] = _snap
                osnap["orb_last_exit_hold_reason"] = "scratch:near_arm_mfe_protect_underwater"
            log.info(
                "OPTIONS_STEALTH_SCRATCH_SKIP | trade_id=%s | symbol=%s | "
                "reason=near_arm_mfe_protect_underwater | held_min=%.2f | "
                "current_pnl_pct=%.4f | max_pnl_pct=%.4f | threshold=%.4f | winner_arm=%.4f",
                position_id,
                position_symbol,
                float(held_min),
                float(current_pnl_pct),
                float(st.max_pnl_pct or 0.0),
                float(_near_arm_protect_mfe),
                float(self._0dte_winner_arm_pct()),
            )
            _be_reason = None
        elif (
            _be_reason == XER.BREAKEVEN.value
            and near_arm_still_green_skips_no_progress(
                is_0dte_single=bool(is_0dte_single),
                max_pnl_pct=float(st.max_pnl_pct or 0.0),
                current_pnl_pct=float(current_pnl_pct),
                protect_mfe_pct=float(_near_arm_protect_mfe),
                winner_arm_pct=float(self._0dte_winner_arm_pct()),
                mark_usable=not bool(detail.get("absurd_live_premium_rejected")),
            )
        ):
            # 60m BE is evaluated before no_progress. Band B still in [0, +2%)
            # after ≥20% MFE must hold toward ARM — same HOOD/COIN recovery class.
            if isinstance(osnap, dict):
                _snap = dict(_be_snap or {})
                _snap["near_arm_still_green_skip"] = True
                osnap["fee_aware_breakeven"] = _snap
                osnap["orb_last_exit_hold_reason"] = "breakeven:near_arm_still_green"
                osnap["orb_exit_defer_total"] = int(osnap.get("orb_exit_defer_total") or 0) + 1
            log.info(
                "OPTIONS_STEALTH_BREAKEVEN_SKIP | trade_id=%s | symbol=%s | "
                "reason=near_arm_still_green | held_min=%.2f | "
                "current_pnl_pct=%.4f | max_pnl_pct=%.4f | threshold=%.4f | winner_arm=%.4f",
                position_id,
                position_symbol,
                float(held_min),
                float(current_pnl_pct),
                float(st.max_pnl_pct or 0.0),
                float(_near_arm_protect_mfe),
                float(self._0dte_winner_arm_pct()),
            )
            _be_reason = None
        if _be_reason and (not _dte_exit_min_hold_blocks(_be_reason)):
            if isinstance(osnap, dict):
                osnap["fee_aware_breakeven"] = _be_snap
            _exit_id = (
                XER.SCRATCH.value if _be_reason == XER.SCRATCH.value else XER.BREAKEVEN.value
            )
            _pre_arm_floor_active = _apply_pre_arm_profit_protect_floor(_exit_id)
            _low_arm_floor_active = False
            _weak_cap_floor_active = False
            if not _pre_arm_floor_active:
                _low_arm_floor_active = _apply_low_arm_profit_protect_floor(_exit_id)
                if not _low_arm_floor_active:
                    _weak_cap_floor_active = _apply_weak_loss_cap_floor(_exit_id)
            log.info(
                "OPTIONS_STEALTH_%s_EXIT | trade_id=%s | symbol=%s | held_min=%.2f | "
                "current_pnl_pct=%.4f | max_pnl_pct=%.4f | exit_band=%s | cash_be=%s | bid=%s",
                str(_exit_id).upper(),
                position_id,
                position_symbol,
                float(held_min),
                float(current_pnl_pct),
                float(st.max_pnl_pct or 0.0),
                str((_be_snap or {}).get("exit_band") or _exit_id),
                (_be_snap or {}).get("breakeven_exit_premium"),
                _be_bid,
            )
            diag = self._build_exit_diagnostics(
                position_id=position_id,
                position_symbol=position_symbol,
                block=block,
                st=st,
                osnap=osnap,
                exit_premium=prem,
                exit_reason=_exit_id,
                now=now,
                prem_detail=detail,
                premium_source_at_exit=prem_src,
            )
            if _pre_arm_floor_active:
                diag["pre_arm_profit_protect"] = True
                diag["pre_arm_profit_protect_floor_pnl_pct"] = float(pre_arm_floor_pct)
                diag["pre_arm_profit_protect_min_current_pnl_pct"] = float(pre_arm_min_current_pnl)
            if _low_arm_floor_active:
                _annotate_low_arm_profit_protect(diag)
            if _weak_cap_floor_active:
                _annotate_weak_loss_cap(diag)
            if (_pre_arm_floor_active or _low_arm_floor_active or _weak_cap_floor_active) and _bid_aware_be_divert_after_wrap():
                return None
            self._record_exit(
                _exit_id,
                _profit_protect_record_pnl(
                    _pre_arm_floor_active,
                    _low_arm_floor_active,
                    pnl_pct,
                ),
                prem_src,
                diag,
            )
            telemetry["exit_diagnostics"] = diag
            return self._finalize_exit_return(prem, _exit_id, telemetry, st=st, osnap=osnap, detail=detail)

        drawdown_exit_eligible = bool(
            mfe_trail_live
            and protection_quote_ok
            and float(st.max_pnl_pct) >= float(dd_trig)
            and float(drawdown_from_max_pct) >= float(effective_dd_exit)
            and (not bool(osnap.get("hold_exit_suppress_mfe_latch")))
        )
        log.info(
            "OPTIONS_STEALTH_PROTECTION_STATE | trade_id=%s | "
            "trailing_active=%s | drawdown_exit_eligible=%s | live_non_synthetic_quote=%s | tline_trusted_degraded=%s | protection_quote_ok=%s",
            position_id,
            str(bool(st.trailing_activated_at is not None)).lower(),
            str(bool(drawdown_exit_eligible)).lower(),
            str(bool(live_non_synthetic_quote)).lower(),
            str(bool(tline_trusted_degraded)).lower(),
            str(bool(protection_quote_ok)).lower(),
        )
        eval_key = f"ev|{str(position_id or '-')}:{position_symbol}"
        if _lifecycle_tick_should_emit(_option_exit_trigger_eval_last, eval_key):
            log.info(
                "OPTION_EXIT_TRIGGER_EVAL | trade_id=%s | symbol=%s | strategy_type=%s | synthetic_active=%s | "
                "live_non_synthetic_quote=%s | mark_is_exit_grade=%s | mark_quality=%s | premium_source=%s | "
                "drawdown_exit_eligible=%s | current_pnl_pct=%.4f | max_pnl_pct=%.4f | drawdown_from_max_pct=%.4f | "
                "good_mark_tick_count=%d | timeout_clock_frozen=%s",
                str(position_id or "-"),
                position_symbol,
                str(st.strategy_type or ""),
                str(bool(synthetic_active)).lower(),
                str(bool(live_non_synthetic_quote)).lower(),
                str(bool(osnap.get("mark_is_exit_grade"))).lower(),
                str(osnap.get("mark_quality") or ""),
                prem_src,
                str(bool(drawdown_exit_eligible)).lower(),
                float(current_pnl_pct),
                float(st.max_pnl_pct),
                float(drawdown_from_max_pct),
                int(st.good_mark_tick_count),
                str(bool(st.timeout_clock_frozen or osnap.get("timeout_clock_frozen"))).lower(),
            )

        trailing_trigger_scale = 1.0
        trailing_pct_scale = 1.0
        if degraded_tighten_active:
            trailing_trigger_scale = max(0.25, min(1.0, float(self.config.degraded_trailing_trigger_scale)))
            trailing_pct_scale = max(0.25, min(1.0, float(self.config.degraded_trailing_pct_scale)))
            log.warning(
                "OPTIONS_STEALTH | stage=degraded_risk_tighten | trade_id=%s | symbol=%s | degraded_min=%.2f | "
                "trailing_trigger_scale=%.2f | trailing_pct_scale=%.2f",
                position_id,
                position_symbol,
                degraded_min,
                trailing_trigger_scale,
                trailing_pct_scale,
            )
            log.info(
                "OPTIONS_STEALTH | stage=degraded_data_continue | symbol=%s | trade_id=%s | source=%s | has_live=%s | has_fallback=%s | outage_sec=%.1f",
                position_symbol,
                position_id,
                prem_src,
                str(bool(has_live_data)).lower(),
                str(bool(has_fallback_data)).lower(),
                outage_seconds,
            )
        # Preserve earlier normalized ORB detection (strategy_type may be lotto/single_leg).
        if not is_orb_0dte and not is_trendline_0dte:
            _st_chk = str(st.strategy_type or "").lower()
            _sp_chk = str(osnap.get("source_path") or block.get("source_path") or "").lower()
            is_orb_0dte = (
                _st_chk == "orb_0dte"
                or "orb" in _sp_chk
                or "easy0dte" in _sp_chk
                or _st_chk in ("lotto", "single_leg", "orb")
            )
        # Emit effective runtime thresholds for ORB/Trendline exit logic.
        if is_orb_0dte:
            effective_orb_trailing_trigger_pct = max(
                0.03,
                float(self.config.trailing_trigger_pct) * float(trailing_trigger_scale) * float(self.config.orb_trailing_trigger_mult),
            )
            orb_sig = (round(effective_orb_trailing_trigger_pct, 6),)
            if osnap.get("_orb_threshold_sig") != orb_sig:
                osnap["_orb_threshold_sig"] = orb_sig
                log.info(
                    "OPTION_STEALTH_EFFECTIVE_THRESHOLDS | path=orb_0dte | symbol=%s | trade_id=%s | "
                    "effective_orb_trailing_trigger_pct=%.4f",
                    position_symbol,
                    position_id,
                    effective_orb_trailing_trigger_pct,
                )
        elif is_trendline_0dte:
            effective_tline_trailing_trigger_pct = max(
                0.01,
                float(self.config.tline_trail_activate_pct) * float(trailing_trigger_scale),
            )
            effective_tline_impulse_trail_activate_pct = max(
                0.01,
                _tline_impulse_trail_activation_pct(self.config) * float(trailing_trigger_scale),
            )
            tline_sig = (
                round(effective_tline_trailing_trigger_pct, 6),
                round(effective_tline_impulse_trail_activate_pct, 6),
                round(float(self.config.tline_impulse_trailing_trigger_pct), 6),
                round(float(self.config.tline_retest_trail_activate_pct), 6),
                round(float(self.config.tline_slow_trend_trail_activate_pct), 6),
            )
            if osnap.get("_tline_threshold_sig") != tline_sig:
                osnap["_tline_threshold_sig"] = tline_sig
                log.info(
                    "OPTION_STEALTH_EFFECTIVE_THRESHOLDS | path=trendline_0dte | symbol=%s | trade_id=%s | "
                    "effective_tline_trailing_trigger_pct=%.4f | "
                    "impulse_trail_activate_pct=%.4f | impulse_trailing_trigger_pct=%.4f | "
                    "retest_trailing_trigger_pct=%.4f | "
                    "slow_trend_trailing_trigger_pct=%.4f",
                    position_symbol,
                    position_id,
                    effective_tline_trailing_trigger_pct,
                    effective_tline_impulse_trail_activate_pct,
                    float(self.config.tline_impulse_trailing_trigger_pct),
                    float(self.config.tline_retest_trail_activate_pct),
                    float(self.config.tline_slow_trend_trail_activate_pct),
                )

        adaptive_spread_trailing_trigger_pct: Optional[float] = None
        if is_spread and st.position_type == OptionPositionType.DEBIT_SPREAD.value:
            try:
                entry_debit_val = float(st.entry_debit or st.entry_value or st.entry_premium or 0.0)
                long_strike = None
                short_strike = None
                for leg in st.legs or []:
                    if not isinstance(leg, dict):
                        continue
                    ls = str(leg.get("long_or_short") or "").lower()
                    strike_v = float(leg.get("strike") or 0.0)
                    if strike_v <= 0:
                        continue
                    if ls == "long" and long_strike is None:
                        long_strike = strike_v
                    elif ls == "short" and short_strike is None:
                        short_strike = strike_v
                spread_width = (
                    abs(float(long_strike) - float(short_strike))
                    if long_strike is not None and short_strike is not None
                    else 0.0
                )
                max_profit = max(0.0, spread_width - entry_debit_val)
                max_profit_pct = (max_profit / entry_debit_val) if entry_debit_val > 0 else 0.0
                if max_profit_pct > 0:
                    adaptive_spread_trailing_trigger_pct = min(
                        float(self.config.spread_trailing_trigger_pct),
                        max(0.06, max_profit_pct * 0.85),
                    )
                    prev_cap = osnap.get("_spread_trigger_cap_logged")
                    cap_sig = (
                        round(float(max_profit_pct), 4),
                        round(float(adaptive_spread_trailing_trigger_pct), 4),
                    )
                    if prev_cap != cap_sig:
                        osnap["_spread_trigger_cap_logged"] = cap_sig
                        log.info(
                            "OPTIONS_STEALTH | stage=spread_trigger_adaptation | trade_id=%s | max_profit_pct=%.4f | "
                            "trailing_trigger=%.4f",
                            position_id,
                            max_profit_pct,
                            adaptive_spread_trailing_trigger_pct,
                        )
            except Exception as spread_cap_err:
                log.debug(
                    "OPTIONS_STEALTH | stage=spread_trigger_adaptation_skip | trade_id=%s | reason=%s",
                    position_id,
                    spread_cap_err,
                )

        time_exit_minutes = float(profile.time_exit_minutes)
        no_progress_exit_minutes = float(profile.no_progress_minutes)
        # Early no-progress when peak MFE < 5% (Aug 12 max-book PC).
        _np_early_min = float(
            getattr(self.config, "dte_single_no_progress_early_minutes", 30.0) or 30.0
        )
        _np_early_mfe = float(
            getattr(self.config, "dte_single_no_progress_early_max_mfe_pct", 0.05) or 0.05
        )
        if (
            is_0dte_single
            and float(_np_early_min) > 0
            and float(_np_early_min) < float(no_progress_exit_minutes)
            and float(st.max_pnl_pct or 0.0) + 1e-12 < float(_np_early_mfe)
        ):
            no_progress_exit_minutes = float(_np_early_min)
        if (
            is_orb_0dte
            and (not is_trendline_0dte)
            and is_spread
            and bool(osnap.get("orb_spread_degraded_running"))
            and bool(getattr(self.config, "orb_degraded_protect_enable", True))
        ):
            time_exit_minutes = float(time_exit_minutes) + float(
                getattr(self.config, "orb_spread_degraded_time_exit_slack_minutes", 1.5) or 0.0
            )
            no_progress_exit_minutes = float(no_progress_exit_minutes) + float(
                getattr(self.config, "orb_spread_degraded_no_progress_slack_minutes", 1.5) or 0.0
            )
        trailing_trigger_pct = (
            float(adaptive_spread_trailing_trigger_pct)
            if adaptive_spread_trailing_trigger_pct is not None
            else float(profile.trail_trigger_pct)
        )
        is_0dte_single = bool((not is_spread) and (is_orb_0dte or is_trendline_0dte))
        tline_hold_chop_no_progress = (
            is_trendline_0dte
            and (not is_spread)
            and (not is_impulse_mode)
            and pnl_pct >= float(self.config.tline_chop_hold_min_pnl_pct)
        )
        required_good_ticks = int(max(1, getattr(self.config, "timeout_min_good_mark_ticks", 3)))
        mark_is_exit_grade_now = bool(osnap.get("mark_is_exit_grade"))
        timeout_clock_frozen_now = bool(st.timeout_clock_frozen or osnap.get("timeout_clock_frozen"))
        favorable_underlying_move_pct = 0.0
        if entry_u > 0 and current_underlying > 0:
            if str(st.option_side).lower() == "put":
                favorable_underlying_move_pct = (float(entry_u) - float(current_underlying)) / float(entry_u)
            else:
                favorable_underlying_move_pct = (float(current_underlying) - float(entry_u)) / float(entry_u)
        peak_underlying_favorable_pct = float(getattr(st, "max_underlying_favorable_move_pct", 0.0) or 0.0)
        effective_underlying_move_pct = max(
            float(favorable_underlying_move_pct),
            float(peak_underlying_favorable_pct),
        )
        favorable_underlying = False
        orb_relief_active, orb_relief_tier, orb_relief_detail = self._orb_spread_timeout_exit_relief(
            is_orb_0dte=is_orb_0dte,
            is_spread=is_spread,
            is_trendline_0dte=is_trendline_0dte,
            st=st,
            osnap=osnap,
            detail=detail,
            prem_src=str(prem_src or ""),
            prem=float(prem or 0.0),
            held_min=float(held_min),
            mark_is_exit_grade_now=mark_is_exit_grade_now,
            mark_quality=str(osnap.get("mark_quality") or ""),
            now=now,
        )
        if is_orb_0dte and (not is_trendline_0dte) and is_spread:
            osnap["orb_spread_relief_active"] = bool(orb_relief_active)
            osnap["orb_spread_relief_tier"] = str(orb_relief_tier or "") if orb_relief_active else ""
            osnap["orb_spread_relief_detail"] = str(orb_relief_detail or "") if orb_relief_active else ""
            if orb_relief_active:
                _rk = f"{str(orb_relief_tier or '')}"
                if str(osnap.get("_orb_spread_relief_armed_logged_tier") or "") != _rk:
                    osnap["_orb_spread_relief_armed_logged_tier"] = _rk
                    log.info(
                        "OPTION_SPREAD_EXIT_RELIEF_ARMED | trade_id=%s | symbol=%s | relief_tier=%s | relief_detail=%s | "
                        "mark_is_exit_grade=%s | prem_src=%s | is_spread=true",
                        str(position_id or "-"),
                        str(position_symbol or "-"),
                        str(orb_relief_tier or ""),
                        str(orb_relief_detail or ""),
                        str(bool(mark_is_exit_grade_now)).lower(),
                        str(prem_src or ""),
                    )
        orb_trusted_exit_grade_bypass = bool(
            is_orb_0dte
            and (not is_trendline_0dte)
            and is_spread
            and bool(osnap.get("trusted_degraded_spread_mark"))
            and bool(getattr(self.config, "orb_degraded_protect_enable", True))
        )
        osnap["orb_trusted_exit_grade_bypass"] = bool(orb_trusted_exit_grade_bypass)
        orb_np_relax_spread = bool(
            orb_trusted_exit_grade_bypass
            and held_min >= float(getattr(self.config, "orb_no_progress_relax_exit_grade_min_minutes", 11.0))
            and float(st.max_pnl_pct or 0.0) <= float(getattr(self.config, "orb_no_progress_relax_max_mfe_pct", 0.06))
            and (not favorable_underlying)
        )
        dte_np_relax_single = bool(
            is_0dte_single
            and bool(getattr(self.config, "dte_single_exit_grade_bypass", True))
            and tline_trusted_degraded_mark(
                self.config,
                prem_src=str(prem_src or ""),
                mark_quality=str(osnap.get("mark_quality") or ""),
                quote_age_seconds=detail.get("quote_age_seconds"),
                synthetic_active=bool(synthetic_active),
            )
            and held_min >= float(no_progress_exit_minutes) * 0.85
            and float(st.max_pnl_pct or 0.0) <= float(getattr(self.config, "orb_no_progress_relax_max_mfe_pct", 0.06))
            and (not favorable_underlying)
        )
        orb_np_relax = bool(orb_np_relax_spread or dte_np_relax_single)
        osnap["orb_no_progress_relax_active"] = bool(orb_np_relax)
        orb_grade_timeout_bypass = bool(
            is_0dte_single and bool(getattr(self.config, "dte_single_exit_grade_bypass", True))
        )
        osnap["orb_grade_timeout_bypass"] = bool(orb_grade_timeout_bypass)
        if is_0dte_single:
            if (
                mark_is_exit_grade_now
                and (not timeout_clock_frozen_now)
                and int(st.good_mark_tick_count) >= required_good_ticks
            ):
                osnap.pop("orb_time_exit_suppress_started_iso", None)
                osnap.pop("orb_no_progress_suppress_started_iso", None)
        if (
            (not synthetic_active)
            and held_min >= time_exit_minutes
            and pnl_pct < trailing_trigger_pct
            and not weak_exit_min_hold_blocks
        ):
            wall_clock_time_exit_force = should_force_wall_clock_time_exit(
                is_0dte_single=bool(is_0dte_single),
                max_pnl_pct=float(st.max_pnl_pct or 0.0),
                mfe_giveback_trigger_pct=float(self._0dte_winner_arm_pct()),
            )
            osnap["wall_clock_time_exit_force"] = bool(wall_clock_time_exit_force)
            grade_and_tick_blocked = (
                (not mark_is_exit_grade_now)
                or timeout_clock_frozen_now
                or int(st.good_mark_tick_count) < required_good_ticks
            )
            if orb_relief_active or orb_trusted_exit_grade_bypass:
                grade_and_tick_blocked = False
            if orb_grade_timeout_bypass or wall_clock_time_exit_force:
                grade_and_tick_blocked = False
            if wall_clock_time_exit_force and timeout_clock_frozen_now:
                log.warning(
                    "OPTION_TIME_EXIT_WALL_CLOCK_FORCE | trade_id=%s | symbol=%s | held_min=%.2f | "
                    "max_pnl_pct=%.4f | giveback_trigger=%.4f | timeout_clock_frozen=true | "
                    "mark_quality=%s | reason=mfe_below_giveback_trigger",
                    str(position_id or ""),
                    position_symbol,
                    float(held_min),
                    float(st.max_pnl_pct or 0.0),
                    float(self._0dte_winner_arm_pct()),
                    str(osnap.get("mark_quality") or "unknown"),
                )
            if (
                bool(getattr(self.config, "require_exit_grade_for_timeouts", True))
                and (not wall_clock_time_exit_force)
                and (favorable_underlying or grade_and_tick_blocked)
            ):
                if is_0dte_single:
                    hold_reason, counts_degraded = resolve_orb_exit_quality_hold_reason(
                        favorable_underlying=bool(favorable_underlying),
                        timeout_clock_frozen_now=bool(timeout_clock_frozen_now),
                        good_mark_tick_count=int(st.good_mark_tick_count),
                        required_good_ticks=int(required_good_ticks),
                        mark_is_exit_grade_now=bool(mark_is_exit_grade_now),
                        pnl_pct=float(pnl_pct),
                        trailing_trigger_pct=float(trailing_trigger_pct),
                    )
                    osnap["orb_trusted_exit_grade_hold"] = not bool(counts_degraded)
                else:
                    # Spreads: same classifier as singles (never catch-all to non_exit_grade_mark
                    # when marks are already exit-grade).
                    hold_reason, counts_degraded = resolve_orb_exit_quality_hold_reason(
                        favorable_underlying=bool(favorable_underlying),
                        timeout_clock_frozen_now=bool(timeout_clock_frozen_now),
                        good_mark_tick_count=int(st.good_mark_tick_count),
                        required_good_ticks=int(required_good_ticks),
                        mark_is_exit_grade_now=bool(mark_is_exit_grade_now),
                        pnl_pct=float(pnl_pct),
                        trailing_trigger_pct=float(trailing_trigger_pct),
                    )
                hold_token = (
                    "TRENDLINE_OPTIONS_DEGRADED_DATA_HOLD"
                    if is_trendline_0dte and (not is_0dte_single)
                    else orb_exit_hold_log_token(bool(counts_degraded))
                )
                if is_0dte_single:
                    osnap["orb_last_exit_hold_reason"] = f"time_exit:{hold_reason}"
                log.warning(
                    "%s | trade_id=%s | symbol=%s | candidate_exit_reason=time_exit | blocked_reason=%s | mark_quality=%s | mark_is_exit_grade=%s | "
                    "good_mark_tick_count=%d | timeout_clock_frozen=%s | quote_age_seconds=%s",
                    hold_token,
                    str(position_id or ""),
                    position_symbol,
                    hold_reason,
                    str(osnap.get("mark_quality") or "unknown"),
                    str(bool(mark_is_exit_grade_now)).lower(),
                    int(st.good_mark_tick_count),
                    str(bool(timeout_clock_frozen_now)).lower(),
                    osnap.get("quote_age_seconds"),
                )
                if is_0dte_single:
                    _log_orb_exit_suppressed_quote_quality(
                        candidate_exit_reason="time_exit",
                        blocked_reason=str(hold_reason),
                        position_id=str(position_id or ""),
                        position_symbol=position_symbol,
                        prem_src=str(prem_src or ""),
                        osnap=osnap,
                        st=st,
                    )
                    if hold_reason != "favorable_underlying":
                        _maybe_emit_orb_exit_grade_timeout_suppression(
                            suppress_kind="time_exit",
                            position_id=str(position_id or ""),
                            position_symbol=position_symbol,
                            blocked_reason=str(hold_reason),
                            prem_src=str(prem_src or ""),
                            osnap=osnap,
                            st=st,
                            required_good_ticks=int(required_good_ticks),
                            now=now,
                        )
                    osnap["orb_exit_defer_total"] = int(osnap.get("orb_exit_defer_total") or 0) + 1
                    self._emit_orb_options_exit_deferred_audit(
                        position_id=str(position_id or ""),
                        position_symbol=position_symbol,
                        position_type=str(st.position_type or ""),
                        candidate_exit_reason="time_exit",
                        blocked_reason=str(hold_reason),
                        st=st,
                        osnap=osnap,
                        detail=detail,
                        prem_src=str(prem_src or ""),
                        prem=float(prem or 0.0),
                        relief_active=bool(orb_relief_active),
                        relief_tier=str(orb_relief_tier or ""),
                        relief_detail=str(orb_relief_detail or ""),
                    )
            elif near_arm_mfe_protects_weak_red_exit(
                is_0dte_single=bool(is_0dte_single),
                max_pnl_pct=float(st.max_pnl_pct or 0.0),
                current_pnl_pct=float(current_pnl_pct),
                protect_mfe_pct=float(getattr(self.config, "dte_near_arm_protect_mfe_pct", 0.20) or 0.0),
                winner_arm_pct=float(self._0dte_winner_arm_pct()),
                mark_usable=not bool(detail.get("absurd_live_premium_rejected")),
            ) and not _weak_exit_wrap_would_fire("time_exit"):
                if is_0dte_single:
                    osnap["orb_last_exit_hold_reason"] = "time_exit:near_arm_mfe_protect_underwater"
                    osnap["orb_exit_defer_total"] = int(osnap.get("orb_exit_defer_total") or 0) + 1
                log.info(
                    "OPTION_TIME_EXIT_SKIP | trade_id=%s | symbol=%s | "
                    "reason=near_arm_mfe_protect_underwater | held_min=%.2f | "
                    "current_pnl_pct=%.4f | max_pnl_pct=%.4f | threshold=%.4f | winner_arm=%.4f",
                    position_id,
                    position_symbol,
                    float(held_min),
                    float(current_pnl_pct),
                    float(st.max_pnl_pct or 0.0),
                    float(getattr(self.config, "dte_near_arm_protect_mfe_pct", 0.20) or 0.0),
                    float(self._0dte_winner_arm_pct()),
                )
            elif near_arm_still_green_skips_no_progress(
                is_0dte_single=bool(is_0dte_single),
                max_pnl_pct=float(st.max_pnl_pct or 0.0),
                current_pnl_pct=float(current_pnl_pct),
                protect_mfe_pct=float(getattr(self.config, "dte_near_arm_protect_mfe_pct", 0.20) or 0.0),
                winner_arm_pct=float(self._0dte_winner_arm_pct()),
                mark_usable=not bool(detail.get("absurd_live_premium_rejected")),
            ):
                if is_0dte_single:
                    osnap["orb_last_exit_hold_reason"] = "time_exit:near_arm_still_green"
                    osnap["orb_exit_defer_total"] = int(osnap.get("orb_exit_defer_total") or 0) + 1
                log.info(
                    "OPTION_TIME_EXIT_SKIP | trade_id=%s | symbol=%s | "
                    "reason=near_arm_still_green | held_min=%.2f | "
                    "current_pnl_pct=%.4f | max_pnl_pct=%.4f | threshold=%.4f | winner_arm=%.4f",
                    position_id,
                    position_symbol,
                    float(held_min),
                    float(current_pnl_pct),
                    float(st.max_pnl_pct or 0.0),
                    float(getattr(self.config, "dte_near_arm_protect_mfe_pct", 0.20) or 0.0),
                    float(self._0dte_winner_arm_pct()),
                )
            else:
                if orb_relief_active:
                    osnap["orb_last_degraded_exit_tier"] = str(orb_relief_tier or "")
                    osnap["orb_last_degraded_exit_detail"] = str(orb_relief_detail or "")
                    log.warning(
                        "ORB_OPTIONS_FORCED_DEGRADED_EXIT_ATTEMPT | trade_id=%s | symbol=%s | tier=%s | "
                        "candidate_exit_reason=time_exit | detail=%s | prem_src=%s",
                        str(position_id or ""),
                        position_symbol,
                        str(orb_relief_tier or ""),
                        str(orb_relief_detail or ""),
                        str(prem_src or ""),
                    )
                    log.warning(
                        "ORB_OPTIONS_FORCED_DEGRADED_EXIT | trade_id=%s | symbol=%s | tier=%s | candidate_exit_reason=time_exit | detail=%s | "
                        "prem_src=%s | mark_quality=%s | est_liquidation=%.6f",
                        str(position_id or ""),
                        position_symbol,
                        str(orb_relief_tier or ""),
                        str(orb_relief_detail or ""),
                        str(prem_src or ""),
                        str(osnap.get("mark_quality") or "unknown"),
                        float(detail.get("spread_liquidation_value") or detail.get("spread_mid_value") or prem or 0.0),
                    )
                log.info(
                    "OPTION_EXIT_DECISION_AUDIT | trade_id=%s | symbol=%s | strategy_type=%s | position_type=%s | option_side=%s | "
                    "candidate_exit_reason=time_exit | final_exit_reason=time_exit | exit_allowed=true | blocked_reason=none | "
                    "mark_quality=%s | mark_is_exit_grade=%s | quote_age_seconds=%s | good_mark_tick_count=%d | timeout_clock_frozen=%s | "
                    "underlying_move_pct=%.4f | favorable_underlying=%s | current_pnl_pct_mid=%.4f | current_pnl_pct_liquidation=%.4f | "
                    "max_pnl_pct=%.4f | had_opportunity=%s | elapsed_seconds=%.1f | adjusted_elapsed_seconds=%.1f",
                    str(position_id or ""),
                    position_symbol,
                    str(st.strategy_type or ""),
                    str(st.position_type or ""),
                    str(st.option_side or ""),
                    str(osnap.get("mark_quality") or "unknown"),
                    str(bool(mark_is_exit_grade_now)).lower(),
                    osnap.get("quote_age_seconds"),
                    int(st.good_mark_tick_count),
                    str(bool(timeout_clock_frozen_now)).lower(),
                    float(favorable_underlying_move_pct),
                    str(bool(favorable_underlying)).lower(),
                    float(detail.get("current_pnl_pct_mid") if is_spread else current_pnl_pct),
                    float(detail.get("current_pnl_pct_liquidation") if is_spread else current_pnl_pct),
                    float(st.max_pnl_pct),
                    str(bool(st.had_opportunity)).lower(),
                    float(held_min * 60.0),
                    float(max(0.0, (held_min * 60.0) - float(st.timeout_frozen_seconds or 0.0))),
                )
                if is_trendline_0dte and is_impulse_mode:
                    log.warning(
                        "TRENDLINE_IMPULSE_EXIT | trade_id=%s | symbol=%s | reason=impulse_time_exit | held_min=%.2f | pnl_pct=%.4f",
                        position_id,
                        position_symbol,
                        held_min,
                        pnl_pct,
                    )
                log.info(
                    "TRENDLINE_PIPELINE | stage=option_exit | trade_id=%s | reason=time_exit | held_min=%.2f",
                    position_id,
                    held_min,
                )
                if is_0dte_single:
                    osnap.pop("orb_time_exit_suppress_started_iso", None)
                    osnap.pop("orb_last_exit_hold_reason", None)
                _pre_arm_floor_active = _apply_pre_arm_profit_protect_floor("time_exit")
                _low_arm_floor_active = False
                _weak_cap_floor_active = False
                if not _pre_arm_floor_active:
                    _low_arm_floor_active = _apply_low_arm_profit_protect_floor("time_exit")
                    if not _low_arm_floor_active:
                        _weak_cap_floor_active = _apply_weak_loss_cap_floor("time_exit")
                diag = self._build_exit_diagnostics(
                    position_id=position_id,
                    position_symbol=position_symbol,
                    block=block,
                    st=st,
                    osnap=osnap,
                    exit_premium=prem,
                    exit_reason="time_exit",
                    now=now,
                    prem_detail=detail,
                    premium_source_at_exit=prem_src,
                )
                if _pre_arm_floor_active:
                    diag["pre_arm_profit_protect"] = True
                    diag["pre_arm_profit_protect_floor_pnl_pct"] = float(pre_arm_floor_pct)
                    diag["pre_arm_profit_protect_min_current_pnl_pct"] = float(pre_arm_min_current_pnl)
                if _low_arm_floor_active:
                    _annotate_low_arm_profit_protect(diag)
                if _weak_cap_floor_active:
                    _annotate_weak_loss_cap(diag)
                if (_pre_arm_floor_active or _low_arm_floor_active or _weak_cap_floor_active) and _bid_aware_be_divert_after_wrap():
                    return None
                self._record_exit(
                    "time_exit",
                    _profit_protect_record_pnl(
                        _pre_arm_floor_active,
                        _low_arm_floor_active,
                        pnl_pct,
                    ),
                    prem_src,
                    diag,
                )
                log.info(
                    "OPTIONS_STEALTH_TIME_EXIT | trade_id=%s | symbol=%s | held_min=%.2f | current_pnl_pct=%.4f | max_pnl_pct=%.4f",
                    position_id,
                    position_symbol,
                    float(held_min),
                    float(current_pnl_pct),
                    float(st.max_pnl_pct),
                )
                telemetry["exit_diagnostics"] = diag
                return self._finalize_exit_return(prem, "time_exit", telemetry, st=st, osnap=osnap, detail=detail)
        # Keep strong runners alive: no-progress timeout is only for weak/non-trending trades.
        # ORB + Trendline single-leg share the same HWM stall path; Trendline spreads keep structure overlays.
        if (
            st.last_hwm_at
            and held_min >= 2.0
            and pnl_pct < trailing_trigger_pct
            and (not tline_hold_chop_no_progress)
        ):
            stall_minutes = max(
                0.0,
                ((now - self._normalize_dt(st.last_hwm_at)).total_seconds() - float(st.timeout_frozen_seconds or 0.0)) / 60.0,
            )
            stalled = stall_minutes >= no_progress_exit_minutes
            if bool(getattr(self.config, "require_exit_grade_for_timeouts", True)) and (not orb_grade_timeout_bypass) and (
                (not mark_is_exit_grade_now) or timeout_clock_frozen_now or int(st.good_mark_tick_count) < required_good_ticks
            ) and (not orb_relief_active) and (not orb_np_relax):
                stalled = False
            if stalled:
                held_seconds = held_min * 60.0
                if (
                    is_trendline_0dte
                    and is_spread
                    and held_seconds < float(self.config.tline_min_hold_seconds)
                ):
                    log.info(
                        "TRENDLINE_TIMEOUT_SKIPPED | trade_id=%s | symbol=%s | reason=min_hold | held_seconds=%.1f | min_hold_seconds=%.1f",
                        position_id,
                        position_symbol,
                        held_seconds,
                        float(self.config.tline_min_hold_seconds),
                    )
                    stalled = False
                elif is_orb_0dte and is_spread and entry_u > 0 and current_underlying > 0:
                    side = str(st.option_side or "").lower()
                    favorable_move_pct = (
                        (float(current_underlying) - float(entry_u)) / float(entry_u)
                        if side != "put"
                        else (float(entry_u) - float(current_underlying)) / float(entry_u)
                    )
                    favorable_threshold = float(
                        self.config.orb_spread_no_progress_favorable_underlying_move_pct
                    )
                    if favorable_move_pct >= favorable_threshold:
                        log.info(
                            "ORB_OPTIONS_NO_PROGRESS_BYPASS | trade_id=%s | symbol=%s | reason=favorable_underlying_continuation | "
                            "favorable_move_pct=%.4f | threshold=%.4f | premium_source=%s",
                            position_id,
                            position_symbol,
                            float(favorable_move_pct),
                            float(favorable_threshold),
                            str(prem_src or "unknown"),
                        )
                        stalled = False
                if stalled and is_0dte_single:
                    winner_arm = float(self._0dte_winner_arm_pct())
                    skip_mfe = float(
                        getattr(self.config, "dte_no_progress_skip_if_mfe_pct", winner_arm)
                        or winner_arm
                    )
                    # When mfe_giveback is off, align skip floor to take_profit arm (TP30).
                    if not bool(getattr(self.config, "dte_single_mfe_trail_enabled", False)):
                        skip_mfe = max(float(skip_mfe), float(winner_arm))
                    giveback_arm = float(winner_arm)
                    max_pnl = float(st.max_pnl_pct or 0.0)
                    # Winner-armed trades are owned by take_profit / mfe_giveback — never no_progress.
                    # Mild MFE while still green may skip; underwater never-armed losers must cut.
                    if max_pnl >= giveback_arm - 1e-9:
                        log.info(
                            "OPTION_NO_PROGRESS_SKIP | trade_id=%s | symbol=%s | "
                            "reason=winner_exit_armed | max_pnl_pct=%.4f | giveback_arm=%.4f",
                            position_id,
                            position_symbol,
                            max_pnl,
                            float(giveback_arm),
                        )
                        stalled = False
                    elif near_arm_mfe_protects_weak_red_exit(
                        is_0dte_single=bool(is_0dte_single),
                        max_pnl_pct=float(max_pnl),
                        current_pnl_pct=float(current_pnl_pct),
                        protect_mfe_pct=float(getattr(self.config, "dte_near_arm_protect_mfe_pct", 0.20) or 0.0),
                        winner_arm_pct=float(giveback_arm),
                        mark_usable=not bool(detail.get("absurd_live_premium_rejected")),
                    ) and not _weak_exit_wrap_would_fire(XER.NO_PROGRESS.value):
                        if isinstance(osnap, dict):
                            osnap["orb_last_exit_hold_reason"] = "no_progress:near_arm_mfe_protect_underwater"
                            osnap["orb_exit_defer_total"] = int(osnap.get("orb_exit_defer_total") or 0) + 1
                        log.info(
                            "OPTION_NO_PROGRESS_SKIP | trade_id=%s | symbol=%s | "
                            "reason=near_arm_mfe_protect_underwater | max_pnl_pct=%.4f | "
                            "current_pnl_pct=%.4f | threshold=%.4f | winner_arm=%.4f",
                            position_id,
                            position_symbol,
                            max_pnl,
                            float(current_pnl_pct),
                            float(getattr(self.config, "dte_near_arm_protect_mfe_pct", 0.20) or 0.0),
                            float(giveback_arm),
                        )
                        stalled = False
                    elif near_arm_still_green_skips_no_progress(
                        is_0dte_single=bool(is_0dte_single),
                        max_pnl_pct=float(max_pnl),
                        current_pnl_pct=float(current_pnl_pct),
                        protect_mfe_pct=float(getattr(self.config, "dte_near_arm_protect_mfe_pct", 0.20) or 0.0),
                        winner_arm_pct=float(giveback_arm),
                        mark_usable=not bool(detail.get("absurd_live_premium_rejected")),
                    ):
                        if isinstance(osnap, dict):
                            osnap["orb_last_exit_hold_reason"] = "no_progress:near_arm_still_green"
                            osnap["orb_exit_defer_total"] = int(osnap.get("orb_exit_defer_total") or 0) + 1
                        log.info(
                            "OPTION_NO_PROGRESS_SKIP | trade_id=%s | symbol=%s | "
                            "reason=near_arm_still_green | max_pnl_pct=%.4f | "
                            "current_pnl_pct=%.4f | threshold=%.4f | winner_arm=%.4f",
                            position_id,
                            position_symbol,
                            max_pnl,
                            float(current_pnl_pct),
                            float(getattr(self.config, "dte_near_arm_protect_mfe_pct", 0.20) or 0.0),
                            float(giveback_arm),
                        )
                        stalled = False
                    elif max_pnl >= skip_mfe - 1e-9 and float(current_pnl_pct) >= 0.0:
                        log.info(
                            "OPTION_NO_PROGRESS_SKIP | trade_id=%s | symbol=%s | "
                            "reason=premium_mfe_armed_still_green | max_pnl_pct=%.4f | "
                            "current_pnl_pct=%.4f | threshold=%.4f",
                            position_id,
                            position_symbol,
                            max_pnl,
                            float(current_pnl_pct),
                            float(skip_mfe),
                        )
                        stalled = False
                    elif max_pnl >= skip_mfe - 1e-9 and float(current_pnl_pct) < 0.0:
                        log.info(
                            "OPTION_NO_PROGRESS_ALLOW | trade_id=%s | symbol=%s | "
                            "reason=underwater_after_mfe_not_giveback_armed | max_pnl_pct=%.4f | "
                            "current_pnl_pct=%.4f | giveback_arm=%.4f",
                            position_id,
                            position_symbol,
                            max_pnl,
                            float(current_pnl_pct),
                            float(giveback_arm),
                        )
                if is_trendline_0dte and is_spread and is_slow_trend_mode:
                    log.info(
                        "TRENDLINE_TIMEOUT_SKIPPED | trade_id=%s | symbol=%s | reason=valid_structure | detail=slow_trend_mode",
                        position_id,
                        position_symbol,
                    )
                    stalled = False
            if stalled and non_live_premium:
                log.info(
                    "ORB_OPTIONS_NO_PROGRESS_BYPASS | trade_id=%s | symbol=%s | reason=non_live_premium_source | premium_source=%s | price_source_telemetry=%s",
                    position_id,
                    position_symbol,
                    str(prem_src or "unknown"),
                    str(px_telem_g or "unknown"),
                )
                stalled = False
            if stalled and (not synthetic_active):
                if orb_relief_active:
                    osnap["orb_last_degraded_exit_tier"] = str(orb_relief_tier or "")
                    osnap["orb_last_degraded_exit_detail"] = str(orb_relief_detail or "")
                    log.warning(
                        "ORB_OPTIONS_FORCED_DEGRADED_EXIT_ATTEMPT | trade_id=%s | symbol=%s | tier=%s | "
                        "candidate_exit_reason=no_progress | detail=%s | prem_src=%s",
                        str(position_id or ""),
                        position_symbol,
                        str(orb_relief_tier or ""),
                        str(orb_relief_detail or ""),
                        str(prem_src or ""),
                    )
                    log.warning(
                        "ORB_OPTIONS_FORCED_DEGRADED_EXIT | trade_id=%s | symbol=%s | tier=%s | candidate_exit_reason=no_progress | detail=%s | "
                        "prem_src=%s | mark_quality=%s | est_liquidation=%.6f",
                        str(position_id or ""),
                        position_symbol,
                        str(orb_relief_tier or ""),
                        str(orb_relief_detail or ""),
                        str(prem_src or ""),
                        str(osnap.get("mark_quality") or "unknown"),
                        float(detail.get("spread_liquidation_value") or detail.get("spread_mid_value") or prem or 0.0),
                    )
                log.info(
                    "OPTION_EXIT_DECISION_AUDIT | trade_id=%s | symbol=%s | strategy_type=%s | position_type=%s | option_side=%s | "
                    "candidate_exit_reason=no_progress | final_exit_reason=no_progress | exit_allowed=true | blocked_reason=none | "
                    "mark_quality=%s | mark_is_exit_grade=%s | quote_age_seconds=%s | good_mark_tick_count=%d | timeout_clock_frozen=%s | "
                    "underlying_move_pct=%.4f | favorable_underlying=%s | current_pnl_pct_mid=%.4f | current_pnl_pct_liquidation=%.4f | "
                    "max_pnl_pct=%.4f | had_opportunity=%s | elapsed_seconds=%.1f | adjusted_elapsed_seconds=%.1f",
                    str(position_id or ""),
                    position_symbol,
                    str(st.strategy_type or ""),
                    str(st.position_type or ""),
                    str(st.option_side or ""),
                    str(osnap.get("mark_quality") or "unknown"),
                    str(bool(mark_is_exit_grade_now)).lower(),
                    osnap.get("quote_age_seconds"),
                    int(st.good_mark_tick_count),
                    str(bool(timeout_clock_frozen_now)).lower(),
                    float(favorable_underlying_move_pct),
                    str(bool(favorable_underlying)).lower(),
                    float(detail.get("current_pnl_pct_mid") if is_spread else current_pnl_pct),
                    float(detail.get("current_pnl_pct_liquidation") if is_spread else current_pnl_pct),
                    float(st.max_pnl_pct),
                    str(bool(st.had_opportunity)).lower(),
                    float(held_min * 60.0),
                    float(max(0.0, (held_min * 60.0) - float(st.timeout_frozen_seconds or 0.0))),
                )
                st.no_progress_timeout_triggered = True
                if is_0dte_single:
                    osnap.pop("orb_no_progress_suppress_started_iso", None)
                    osnap.pop("orb_last_exit_hold_reason", None)
                if is_trendline_0dte and is_impulse_mode:
                    log.warning(
                        "TRENDLINE_IMPULSE_EXIT | trade_id=%s | symbol=%s | reason=impulse_no_progress | stalled_min>=%.1f | pnl_pct=%.4f",
                        position_id,
                        position_symbol,
                        no_progress_exit_minutes,
                        pnl_pct,
                    )
                log.info(
                    "TRENDLINE_PIPELINE | stage=option_exit | trade_id=%s | reason=no_progress_timeout | stalled_min>=%.1f",
                    position_id,
                    no_progress_exit_minutes,
                )
                _pre_arm_floor_active = _apply_pre_arm_profit_protect_floor(XER.NO_PROGRESS.value)
                _low_arm_floor_active = False
                _weak_cap_floor_active = False
                if not _pre_arm_floor_active:
                    _low_arm_floor_active = _apply_low_arm_profit_protect_floor(XER.NO_PROGRESS.value)
                    if not _low_arm_floor_active:
                        _weak_cap_floor_active = _apply_weak_loss_cap_floor(XER.NO_PROGRESS.value)
                diag = self._build_exit_diagnostics(
                    position_id=position_id,
                    position_symbol=position_symbol,
                    block=block,
                    st=st,
                    osnap=osnap,
                    exit_premium=prem,
                    exit_reason=XER.NO_PROGRESS.value,
                    now=now,
                    prem_detail=detail,
                    premium_source_at_exit=prem_src,
                )
                if _pre_arm_floor_active:
                    diag["pre_arm_profit_protect"] = True
                    diag["pre_arm_profit_protect_floor_pnl_pct"] = float(pre_arm_floor_pct)
                    diag["pre_arm_profit_protect_min_current_pnl_pct"] = float(pre_arm_min_current_pnl)
                if _low_arm_floor_active:
                    _annotate_low_arm_profit_protect(diag)
                if _weak_cap_floor_active:
                    _annotate_weak_loss_cap(diag)
                if (_pre_arm_floor_active or _low_arm_floor_active or _weak_cap_floor_active) and _bid_aware_be_divert_after_wrap():
                    return None
                self._record_exit(
                    XER.NO_PROGRESS.value,
                    _profit_protect_record_pnl(
                        _pre_arm_floor_active,
                        _low_arm_floor_active,
                        pnl_pct,
                    ),
                    prem_src,
                    diag,
                )
                log.info(
                    "OPTIONS_STEALTH_NO_PROGRESS_EXIT | trade_id=%s | symbol=%s | stalled_min=%.2f | current_pnl_pct=%.4f | max_pnl_pct=%.4f",
                    position_id,
                    position_symbol,
                    float(no_progress_exit_minutes),
                    float(current_pnl_pct),
                    float(st.max_pnl_pct),
                )
                telemetry["exit_diagnostics"] = diag
                return self._finalize_exit_return(prem, XER.NO_PROGRESS.value, telemetry, st=st, osnap=osnap, detail=detail)
            elif (
                bool(getattr(self.config, "require_exit_grade_for_timeouts", True))
                and (not orb_grade_timeout_bypass)
                and (not orb_relief_active)
                and (not orb_np_relax)
                and st.last_hwm_at
                and held_min >= 2.0
                # Only emit hold telemetry once the no-progress stall timer has actually elapsed.
                # Previously held_min>=2 alone spammed DEGRADED holds (e.g. MSTR live today)
                # with catch-all non_exit_grade_mark while marks were already exit-grade.
                and float(stall_minutes) >= float(no_progress_exit_minutes)
                and pnl_pct < trailing_trigger_pct
                and (not tline_hold_chop_no_progress)
            ):
                hold_reason, counts_degraded = resolve_orb_exit_quality_hold_reason(
                    favorable_underlying=bool(favorable_underlying),
                    timeout_clock_frozen_now=bool(timeout_clock_frozen_now),
                    good_mark_tick_count=int(st.good_mark_tick_count),
                    required_good_ticks=int(required_good_ticks),
                    mark_is_exit_grade_now=bool(mark_is_exit_grade_now),
                    pnl_pct=float(pnl_pct),
                    trailing_trigger_pct=float(trailing_trigger_pct),
                )
                if is_0dte_single:
                    osnap["orb_trusted_exit_grade_hold"] = not bool(counts_degraded)
                hold_token = (
                    "TRENDLINE_OPTIONS_DEGRADED_DATA_HOLD"
                    if is_trendline_0dte and (not is_0dte_single)
                    else orb_exit_hold_log_token(bool(counts_degraded))
                )
                if is_0dte_single:
                    osnap["orb_last_exit_hold_reason"] = f"no_progress:{hold_reason}"
                log.warning(
                    "%s | trade_id=%s | symbol=%s | candidate_exit_reason=no_progress | blocked_reason=%s | mark_quality=%s | "
                    "mark_is_exit_grade=%s | good_mark_tick_count=%d | timeout_clock_frozen=%s | quote_age_seconds=%s",
                    hold_token,
                    str(position_id or ""),
                    position_symbol,
                    hold_reason,
                    str(osnap.get("mark_quality") or "unknown"),
                    str(bool(mark_is_exit_grade_now)).lower(),
                    int(st.good_mark_tick_count),
                    str(bool(timeout_clock_frozen_now)).lower(),
                    osnap.get("quote_age_seconds"),
                )
                if is_0dte_single:
                    _log_orb_exit_suppressed_quote_quality(
                        candidate_exit_reason=XER.NO_PROGRESS.value,
                        blocked_reason=str(hold_reason),
                        position_id=str(position_id or ""),
                        position_symbol=position_symbol,
                        prem_src=str(prem_src or ""),
                        osnap=osnap,
                        st=st,
                    )
                    if hold_reason != "favorable_underlying":
                        _maybe_emit_orb_exit_grade_timeout_suppression(
                            suppress_kind="no_progress",
                            position_id=str(position_id or ""),
                            position_symbol=position_symbol,
                            blocked_reason=str(hold_reason),
                            prem_src=str(prem_src or ""),
                            osnap=osnap,
                            st=st,
                            required_good_ticks=int(required_good_ticks),
                            now=now,
                        )
                    osnap["orb_exit_defer_total"] = int(osnap.get("orb_exit_defer_total") or 0) + 1
                    self._emit_orb_options_exit_deferred_audit(
                        position_id=str(position_id or ""),
                        position_symbol=position_symbol,
                        position_type=str(st.position_type or ""),
                        candidate_exit_reason=XER.NO_PROGRESS.value,
                        blocked_reason=str(hold_reason),
                        st=st,
                        osnap=osnap,
                        detail=detail,
                        prem_src=str(prem_src or ""),
                        prem=float(prem or 0.0),
                        relief_active=bool(orb_relief_active),
                        relief_tier=str(orb_relief_tier or ""),
                        relief_detail=str(orb_relief_detail or ""),
                    )

        trail_pct = float(profile.trail_distance_pct)
        if not is_spread:
            if pnl_pct >= self.config.moon_pnl_threshold_pct:
                st.mode = OptionStealthMode.MOON
                trail_pct = self.config.moon_trailing_pct
            elif pnl_pct >= self.config.explosive_pnl_threshold_pct:
                st.mode = OptionStealthMode.EXPLOSIVE
                trail_pct = self.config.explosive_trailing_pct

        trailing_gate = float(profile.trail_trigger_pct)
        if is_trendline_0dte and (not is_spread):
            trail_pct = _tline_trail_distance_pct_from_mfe(self.config, float(st.max_pnl_pct))
        trailing_gate = max(0.01, float(trailing_gate) * float(trailing_trigger_scale))
        trail_pct = max(0.01, float(trail_pct) * float(trailing_pct_scale))
        try:
            from modules.exit_grade_durability import premium_source_is_trusted_exact as _trusted_exact_fn
        except Exception:
            _trusted_exact_fn = None  # type: ignore
        prem_trusted_exact = False
        if _trusted_exact_fn is not None:
            prem_trusted_exact = bool(
                _trusted_exact_fn(
                    str(prem_src or ""),
                    mark_is_exit_grade=bool(mark_is_exit_grade),
                )
            )
        else:
            _ps = str(prem_src or "").strip().lower()
            prem_trusted_exact = (
                _ps in {"exact_quote", "exit_grade"}
                or "exact_contract" in _ps
                or bool(mark_is_exit_grade)
            )
        if (
            is_trendline_0dte
            and (not is_spread)
            and (not prem_trusted_exact)
            and non_exact_quote_seconds >= float(self.config.tline_degraded_non_exact_tighten_after_seconds)
        ):
            tighten_scale = max(0.80, min(0.95, float(self.config.tline_degraded_non_exact_trail_tighten_scale)))
            trail_pct = max(0.01, float(trail_pct) * float(tighten_scale))
            osnap["degraded_non_exact_trail_active"] = True
            # Rate-limit spam: log on enter and every 60s thereafter.
            last_tighten_log = float(osnap.get("_degraded_non_exact_tighten_log_mono") or 0.0)
            now_mono = time.monotonic()
            if (now_mono - last_tighten_log) >= 60.0:
                osnap["_degraded_non_exact_tighten_log_mono"] = now_mono
                log.warning(
                    "OPTIONS_STEALTH | stage=degraded_non_exact_trail_tighten | trade_id=%s | symbol=%s | source=%s | non_exact_quote_seconds=%.1f | tighten_scale=%.2f",
                    position_id,
                    position_symbol,
                    prem_src,
                    non_exact_quote_seconds,
                    tighten_scale,
                )
        elif is_trendline_0dte and (not is_spread) and prem_trusted_exact:
            osnap.pop("degraded_non_exact_trail_active", None)
        hwm_pct = (
            (float(st.premium_hwm) / float(st.entry_premium) - 1.0)
            if float(st.entry_premium or 0.0) > 0
            else 0.0
        )
        if is_trendline_0dte and (not is_spread):
            if pnl_pct >= float(self.config.tline_runner_trigger_pct):
                exit_phase = "runner"
            elif pnl_pct >= float(self.config.tline_explosive_trigger_pct):
                exit_phase = "explosive"
            elif pnl_pct >= float(self.config.tline_aggressive_trigger_pct):
                exit_phase = "aggressive"
            elif st.trailing_activated_at is not None:
                exit_phase = "base"
            else:
                exit_phase = "early"
            log.info(
                "EXIT_ENGINE_STATE | trade_id=%s | symbol=%s | pnl_pct=%.4f | hwm_pct=%.4f | trailing_pct=%.4f | be_locked=%s | phase=%s | quote_source=%s",
                position_id,
                position_symbol,
                float(pnl_pct),
                float(hwm_pct),
                float(trail_pct),
                str(bool(st.breakeven_floor is not None)).lower(),
                exit_phase,
                prem_src,
            )
        _src_l = str(prem_src or "").lower()
        degraded_mode_for_trailing = not (
            prem_trusted_exact
            or _src_l in {"nearest", "cached_quote"}
        )
        required_trailing_activation_threshold = float(trailing_gate) + (0.05 if degraded_mode_for_trailing else 0.0)
        if is_trendline_0dte and (not is_spread) and is_impulse_mode:
            cap_act = float(self.config.tline_impulse_trail_activation_cap_degraded)
            if cap_act > 0.0:
                required_trailing_activation_threshold = min(float(required_trailing_activation_threshold), cap_act)
        trailing_can_activate = pnl_pct >= required_trailing_activation_threshold
        log.info(
            "TRENDLINE_TRAILING_DEGRADED_ADJUSTMENT | trade_id=%s | symbol=%s | degraded_mode=%s | required_activation_threshold=%.4f | actual_gain_pct=%.4f | trailing_activated=%s",
            position_id,
            position_symbol,
            str(bool(degraded_mode_for_trailing)).lower(),
            float(required_trailing_activation_threshold),
            float(pnl_pct),
            str(bool(st.trailing_activated_at is not None or trailing_can_activate)).lower(),
        )
        if is_trendline_0dte and (not is_spread):
            try:
                eu_snap = float(osnap.get("entry_underlying") or 0.0)
            except (TypeError, ValueError):
                eu_snap = 0.0
            blocked_reason = "none"
            if weak_exit_min_hold_blocks:
                blocked_reason = "min_hold"
            elif (
                bool(getattr(self.config, "require_exit_grade_for_timeouts", True))
                and (
                    (not mark_is_exit_grade_now)
                    or timeout_clock_frozen_now
                    or int(st.good_mark_tick_count) < required_good_ticks
                )
            ):
                blocked_reason = "timeout_quote_grade_or_clock"
            cand_exit = "evaluating"
            if weak_exit_min_hold_blocks:
                cand_exit = "min_hold"
            elif float(st.max_pnl_pct) < float(getattr(self.config, "dte_no_progress_skip_if_mfe_pct", 0.02) or 0.02) and held_min >= max(
                2.0,
                float(no_progress_exit_minutes),
            ):
                cand_exit = "no_progress"
            msg = (
                "OPTION_STEALTH_STATE | trade_id=%s | symbol=%s | path=trendline_0dte | side=%s | held_sec=%.1f | "
                "entry_premium=%.6f | current_premium=%.6f | pnl_pct=%.4f | max_pnl_pct=%.4f | premium_hwm=%.6f | "
                "underlying_entry=%.6f | underlying_now=%.6f | underlying_favorable_pct=%.5f | had_opportunity=%s | "
                "had_underlying_opportunity=%s | trail_armed=%s | "
                "trail_trigger_effective=%.4f | trail_stop=%s | "
                "premium_source=%s | mark_quality=%s | mark_is_exit_grade=%s | trusted_degraded=%s | synthetic_active=%s | "
                "min_hold_active=%s | candidate_exit_reason=%s | blocked_reason=%s"
                % (
                    str(position_id or "-"),
                    position_symbol,
                    str(st.option_side or ""),
                    float(held_seconds),
                    float(st.entry_premium or 0.0),
                    float(prem or 0.0),
                    float(pnl_pct),
                    float(st.max_pnl_pct),
                    float(st.premium_hwm or 0.0),
                    float(eu_snap),
                    float(current_underlying or 0.0),
                    float(getattr(st, "max_underlying_favorable_move_pct", 0.0) or 0.0),
                    str(bool(st.had_opportunity)).lower(),
                    str(bool(getattr(st, "had_underlying_opportunity", False))).lower(),
                    str(bool(st.trailing_activated_at is not None)).lower(),
                    float(required_trailing_activation_threshold),
                    f"{float(st.trail_stop_premium):.6f}" if st.trail_stop_premium is not None else "none",
                    str(prem_src or ""),
                    str(osnap.get("mark_quality") or ""),
                    str(bool(mark_is_exit_grade_now)).lower(),
                    str(bool(tline_trusted_degraded)).lower(),
                    str(bool(synthetic_active)).lower(),
                    str(bool(weak_exit_min_hold_blocks)).lower(),
                    cand_exit,
                    blocked_reason,
                )
            )
            _emit_option_stealth_state_if_due(
                cfg=self.config,
                trade_id=str(position_id or "-"),
                symbol=str(position_symbol or "-"),
                message=msg,
            )
        if (
            pnl_pct >= trailing_gate
            and (st.trailing_activated_at is not None or trailing_can_activate)
            and not _dte_exit_min_hold_blocks("trailing_activation")
        ):
            if st.trailing_activated_at is None:
                st.trailing_activated_at = now
                st.mode = OptionStealthMode.TRAILING
                osnap["trailing_activated_at"] = now.isoformat()
                if is_trendline_0dte and (not is_spread):
                    log.info(
                        "TRENDLINE_TRAIL_ACTIVATED | symbol=%s | trade_id=%s | trendline_mode=%s | pnl_pct=%.4f | max_pnl_pct=%.4f | current_premium=%.4f | entry_premium=%.4f | breakeven_floor=%.4f | trailing_stop_pct=%.4f | data_source=%s",
                        position_symbol,
                        position_id,
                        str(osnap.get("trendline_mode") or "STANDARD"),
                        pnl_pct,
                        float((st.premium_hwm / st.entry_premium - 1.0) if st.entry_premium > 0 else 0.0),
                        prem,
                        st.entry_premium,
                        float(st.breakeven_floor or 0.0),
                        trail_pct,
                        prem_src,
                    )
                log.info(
                    "TRENDLINE_PIPELINE | stage=option_trailing | trade_id=%s | reason=activate | pnl_pct=%.4f | "
                    "trail_pct=%.4f | trigger_pct=%.4f",
                    position_id,
                    pnl_pct,
                    trail_pct,
                    trailing_gate,
                )
                _trail_stop_preview = float(st.premium_hwm or 0.0) * (1.0 - float(trail_pct))
                log.info(
                    "OPTION_TRAILING_STATE | trade_id=%s | symbol=%s | strategy_type=%s | event=activated | "
                    "trail_pct=%.4f | trailing_gate=%.4f | required_activation_threshold=%.4f | premium_hwm=%.6f | "
                    "initial_trail_stop=%.6f | premium_source=%s | mark_is_exit_grade=%s | degraded_mode_for_trailing=%s",
                    str(position_id or "-"),
                    position_symbol,
                    str(st.strategy_type or ""),
                    float(trail_pct),
                    float(trailing_gate),
                    float(required_trailing_activation_threshold),
                    float(st.premium_hwm or 0.0),
                    float(_trail_stop_preview),
                    prem_src,
                    str(bool(osnap.get("mark_is_exit_grade"))).lower(),
                    str(bool(degraded_mode_for_trailing)).lower(),
                )
            stop = st.premium_hwm * (1.0 - trail_pct)
            prev_stop = st.trail_stop_premium
            st.trail_stop_premium = stop
            st.last_trail_pct = trail_pct
            if prev_stop is not None and stop > prev_stop + 1e-9:
                log.info(
                    "TRENDLINE_PIPELINE | stage=option_trailing | trade_id=%s | action=ratchet_stop | stop=%.4f -> %.4f | hwm=%.4f",
                    position_id,
                    prev_stop,
                    stop,
                    st.premium_hwm,
                )
                log.info(
                    "OPTION_TRAILING_STATE | trade_id=%s | symbol=%s | strategy_type=%s | event=ratchet | "
                    "trail_stop_prev=%.6f | trail_stop_new=%.6f | premium_hwm=%.6f | trail_pct=%.4f | premium_source=%s",
                    str(position_id or "-"),
                    position_symbol,
                    str(st.strategy_type or ""),
                    float(prev_stop),
                    float(stop),
                    float(st.premium_hwm or 0.0),
                    float(trail_pct),
                    prem_src,
                )
            if self.config.exit_trailing_stop_enabled and prem <= stop and not _dte_exit_min_hold_blocks("mfe_trailing_stop"):
                if is_trendline_0dte and is_impulse_mode:
                    log.warning(
                        "TRENDLINE_IMPULSE_EXIT | trade_id=%s | symbol=%s | reason=impulse_trailing_stop | pnl_pct=%.4f | stop=%.4f",
                        position_id,
                        position_symbol,
                        pnl_pct,
                        stop,
                    )
                log.info(
                    "TRENDLINE_PIPELINE | stage=option_exit | trade_id=%s | reason=trailing_stop | prem=%.4f stop=%.4f",
                    position_id,
                    prem,
                    stop,
                )
                if is_trendline_0dte and (not is_spread):
                    log.info(
                        "TRENDLINE_TRAIL_EXIT | symbol=%s | trade_id=%s | trendline_mode=%s | pnl_pct=%.4f | max_pnl_pct=%.4f | current_premium=%.4f | entry_premium=%.4f | breakeven_floor=%.4f | trailing_stop_pct=%.4f | data_source=%s",
                        position_symbol,
                        position_id,
                        str(osnap.get("trendline_mode") or "STANDARD"),
                        pnl_pct,
                        float((st.premium_hwm / st.entry_premium - 1.0) if st.entry_premium > 0 else 0.0),
                        prem,
                        st.entry_premium,
                        float(st.breakeven_floor or 0.0),
                        trail_pct,
                        prem_src,
                    )
                if is_spread:
                    log.info(
                        "OPTIONS_STEALTH | stage=spread_exit | trade_id=%s | reason=trailing_stop | spread_current=%.6f | spread_hwm=%.6f",
                        position_id,
                        prem,
                        st.spread_hwm,
                    )
                diag = self._build_exit_diagnostics(
                    position_id=position_id,
                    position_symbol=position_symbol,
                    block=block,
                    st=st,
                    osnap=osnap,
                    exit_premium=prem,
                    exit_reason="mfe_trailing_stop",
                    now=now,
                    prem_detail=detail,
                    premium_source_at_exit=prem_src,
                )
                _pre_arm_floor_active = _apply_pre_arm_profit_protect_floor("mfe_trailing_stop")
                _low_arm_floor_active = False
                _weak_cap_floor_active = False
                if not _pre_arm_floor_active:
                    _low_arm_floor_active = _apply_low_arm_profit_protect_floor("mfe_trailing_stop")
                    if not _low_arm_floor_active:
                        _weak_cap_floor_active = _apply_weak_loss_cap_floor("mfe_trailing_stop")
                if _pre_arm_floor_active:
                    diag["pre_arm_profit_protect"] = True
                    diag["pre_arm_profit_protect_floor_pnl_pct"] = float(pre_arm_floor_pct)
                    diag["pre_arm_profit_protect_min_current_pnl_pct"] = float(pre_arm_min_current_pnl)
                if _low_arm_floor_active:
                    _annotate_low_arm_profit_protect(diag)
                if _weak_cap_floor_active:
                    _annotate_weak_loss_cap(diag)
                if (_pre_arm_floor_active or _low_arm_floor_active or _weak_cap_floor_active) and _bid_aware_be_divert_after_wrap():
                    return None
                self._record_exit(
                    "mfe_trailing_stop",
                    _profit_protect_record_pnl(
                        _pre_arm_floor_active,
                        _low_arm_floor_active,
                        pnl_pct,
                    ),
                    prem_src,
                    diag,
                )
                log.info(
                    "OPTIONS_STEALTH_TRAIL_EXIT | trade_id=%s | symbol=%s | current_pnl_pct=%.4f | max_pnl_pct=%.4f | trail_pct=%.4f",
                    position_id,
                    position_symbol,
                    float(current_pnl_pct),
                    float(st.max_pnl_pct),
                    float(trail_pct),
                )
                telemetry["exit_diagnostics"] = diag
                return self._finalize_exit_return(prem, "mfe_trailing_stop", telemetry, st=st, osnap=osnap, detail=detail)
            log.debug(
                "TRENDLINE_PIPELINE | stage=option_trailing | trade_id=%s | pnl_pct=%.4f hwm=%.4f stop=%.4f",
                position_id,
                pnl_pct,
                st.premium_hwm,
                stop,
            )

        log_every = max(0.0, float(self.config.position_health_log_sec))
        if log_every > 0.0:
            last_health = osnap.get("_last_position_health_log_ts")
            should_log = False
            if isinstance(last_health, str):
                try:
                    last_health_dt = self._normalize_dt(datetime.fromisoformat(last_health))
                    should_log = (now - last_health_dt).total_seconds() >= log_every
                except Exception:
                    should_log = True
            else:
                should_log = True
            if should_log:
                osnap["_last_position_health_log_ts"] = now.isoformat()
                effective_stop = (
                    st.trail_stop_premium if st.trail_stop_premium is not None else st.breakeven_floor
                )
                log.info(
                    "OPTIONS_STEALTH | stage=position_health | symbol=%s | trade_id=%s | delta_entry=%.3f | premium_current=%.4f | "
                    "premium_source=%s | pnl_pct=%.4f | mode=%s | be_active=%s | trail_active=%s | stop=%.4f | degraded=%s",
                    position_symbol,
                    position_id,
                    float(st.delta_at_entry or 0.0),
                    float(prem),
                    prem_src,
                    float(pnl_pct),
                    st.mode.value,
                    st.breakeven_floor is not None,
                    st.trailing_activated_at is not None,
                    float(effective_stop or 0.0),
                    bool(osnap.get("degraded_data_active")),
                )

        return None

    def force_eod_close_premium(
        self,
        position_metadata: Dict[str, Any],
        current_underlying: float,
        option_quote: Optional[Dict[str, Any]] = None,
        position_symbol: str = "",
        trade_id: str = "",
    ) -> Tuple[float, str, Dict[str, Any]]:
        osnap = position_metadata.get("option_stealth")
        if not isinstance(osnap, dict):
            return 0.0, "eod_no_state", {"premium_source": "none", "effective_premium_used": 0.0}
        block = osnap.get("engine")
        if not isinstance(block, dict):
            return 0.0, "eod_no_state", {"premium_source": "none", "effective_premium_used": 0.0}
        st = coerce_option_stealth_engine_state(block, osnap)
        if st is None:
            return 0.0, "eod_no_state", {"premium_source": "none", "effective_premium_used": 0.0}
        entry_u = float(block.get("entry_underlying", current_underlying) or current_underlying)
        sym = position_symbol or str(block.get("underlying_symbol") or "")
        prev_eff = st.last_effective_premium if st.last_effective_premium > 0 else None
        is_spread = st.position_type in (
            OptionPositionType.DEBIT_SPREAD.value,
            OptionPositionType.CREDIT_SPREAD.value,
        )
        if is_spread:
            prem, prem_src, detail = resolve_spread_net_value(
                current_underlying=current_underlying,
                entry_underlying=entry_u,
                cfg=self.config,
                previous_effective=prev_eff,
                position_symbol=sym or "UNKNOWN",
                legs=st.legs,
                option_quote=option_quote,
                entry_value=max(0.01, st.entry_value or st.entry_premium),
                position_type=str(st.position_type or OptionPositionType.DEBIT_SPREAD.value),
                trade_id="",
            )
        else:
            is_0dte_eod = (
                str(st.strategy_type or "").lower() in ("orb_0dte", "trendline_0dte")
                or str(st.strategy_name or "").lower() == "easytrendline_0dte"
            )
            eod_now = datetime.now(timezone.utc)
            sec_eod = (self._normalize_dt(eod_now) - self._normalize_dt(st.opened_at)).total_seconds()
            eod_lv = None
            try:
                eod_lv = float(osnap.get("last_valid_option_price")) if osnap.get("last_valid_option_price") is not None else None
            except (TypeError, ValueError):
                eod_lv = None
            prem, prem_src, detail = resolve_option_price(
                entry_underlying=entry_u,
                current_underlying=current_underlying,
                option_side=st.option_side,
                entry_premium=st.entry_premium,
                cfg=self.config,
                option_quote=option_quote,
                previous_effective=prev_eff,
                delta_at_entry=st.delta_at_entry,
                strike=st.strike,
                position_symbol=sym or "UNKNOWN",
                is_0dte=is_0dte_eod,
                seconds_since_entry=sec_eod,
                trade_id=str(trade_id or ""),
                stored_last_valid_option_price=eod_lv,
                is_short_premium=bool(osnap.get("is_short_premium")),
            )
        st.last_effective_premium = prem
        if _premium_updates_favorable_hwm(prem_src, detail, is_spread=is_spread) and prem > st.premium_hwm:
            st.premium_hwm = prem
        if prem < st.premium_lwm:
            st.premium_lwm = prem
        extra = {
            "premium_source": prem_src,
            "effective_premium_used": prem,
            "premium_detail": detail,
        }
        return prem, XER.END_OF_DAY_EXIT.value, extra

    def complete_eod_close(
        self,
        position_id: str,
        position_symbol: str,
        position_metadata: Dict[str, Any],
        current_underlying: float,
        option_quote: Optional[Dict[str, Any]] = None,
        now: Optional[datetime] = None,
    ) -> Tuple[float, str, Dict[str, Any]]:
        """
        Resolve EOD exit premium, update HWM/LWM, record session metrics, return diagnostics for logging.
        """
        now = self._normalize_dt(now or datetime.now(timezone.utc))
        prem, reason, extra = self.force_eod_close_premium(
            position_metadata,
            current_underlying,
            option_quote=option_quote,
            position_symbol=position_symbol,
            trade_id=str(position_id or ""),
        )
        osnap = position_metadata.get("option_stealth")
        if not isinstance(osnap, dict):
            return prem, reason, {**extra, "exit_diagnostics": {}}
        block = osnap.get("engine")
        if not isinstance(block, dict):
            return prem, reason, {**extra, "exit_diagnostics": {}}
        st = coerce_option_stealth_engine_state(block, osnap)
        if st is None:
            return prem, reason, {**extra, "exit_diagnostics": {}}
        detail = extra.get("premium_detail") or {}
        prem_src = str(extra.get("premium_source") or "delta_estimate")
        is_spread_eod = st.position_type in (
            OptionPositionType.DEBIT_SPREAD.value,
            OptionPositionType.CREDIT_SPREAD.value,
        )
        if is_spread_eod and st.position_type == OptionPositionType.CREDIT_SPREAD.value:
            base = float(st.entry_credit or st.entry_value or st.entry_premium or 0.0)
            pnl_pct = (base - prem) / base if base > 0 else 0.0
        elif is_spread_eod:
            base = float(st.entry_debit or st.entry_value or st.entry_premium or 0.0)
            pnl_pct = (prem - base) / base if base > 0 else 0.0
        else:
            pnl_pct = (prem - st.entry_premium) / st.entry_premium if st.entry_premium > 0 else 0.0
        diag = self._build_exit_diagnostics(
            position_id=position_id,
            position_symbol=position_symbol,
            block=block,
            st=st,
            osnap=osnap,
            exit_premium=prem,
            exit_reason=XER.END_OF_DAY_EXIT.value,
            now=now,
            prem_detail=detail if isinstance(detail, dict) else {},
            premium_source_at_exit=prem_src,
        )
        self._record_exit(XER.END_OF_DAY_EXIT.value, pnl_pct, prem_src, diag)
        settled = float(osnap.get("_settlement_exit_premium") or prem or 0.0)
        return settled, reason, {**extra, "exit_diagnostics": diag}


def dev_stealth_wiring_smoke(engine: Optional[TrendlineOptionsStealthEngine] = None) -> None:
    """
    Optional REPL/dev check: exercises register_on_open + one process_position tick per structural type.
    Does not substitute integration tests; safe to no-op if misconfigured.
    """
    eng = engine or TrendlineOptionsStealthEngine()
    now = datetime.now(timezone.utc)
    for label, meta, und, quote in (
        (
            "single_leg_call",
            {},
            100.0,
            {"bid": 1.0, "ask": 1.1, "last": 1.05},
        ),
        (
            "debit_spread",
            {},
            100.0,
            {"bid": 0.40, "ask": 0.44, "last": 0.42},
        ),
        (
            "credit_spread",
            {},
            100.0,
            {"bid": 0.34, "ask": 0.36, "last": 0.35},
        ),
    ):
        pid = f"smoke_{label}"
        eng.register_on_open(
            pid,
            entry_premium_per_contract=1.0,
            underlying_entry=und,
            option_side="call",
            line_geometry="bear",
            strike=100.0,
            delta=0.3,
            trendline_dict={},
            metadata_target=meta,
            underlying_symbol="SMOKE",
            expiration_ymd="20990101",
            position_type=(
                "single_leg_long_call"
                if label == "single_leg_call"
                else ("debit_spread" if label == "debit_spread" else "credit_spread")
            ),
            strategy_name="smoke",
            strategy_type="orb_0dte",
            entry_value=1.0 if label == "single_leg_call" else (0.5 if label == "debit_spread" else 0.4),
            entry_debit=1.0 if label == "single_leg_call" else (0.5 if label == "debit_spread" else None),
            entry_credit=None if label != "credit_spread" else 0.4,
            legs=(
                [
                    {
                        "leg_id": "A",
                        "long_or_short": "long",
                        "option_side": "call",
                        "strike": 99.0,
                        "quantity": 1,
                        "entry_price": 0.6,
                        "delta_at_entry": 0.35,
                    }
                ]
                if label == "single_leg_call"
                else [
                    {
                        "leg_id": "L",
                        "long_or_short": "long",
                        "option_side": "call",
                        "strike": 99.0,
                        "quantity": 1,
                        "entry_price": 0.35,
                        "delta_at_entry": 0.35,
                    },
                    {
                        "leg_id": "S",
                        "long_or_short": "short",
                        "option_side": "call",
                        "strike": 101.0,
                        "quantity": 1,
                        "entry_price": 0.15,
                        "delta_at_entry": 0.25,
                    },
                ]
            ),
            source_path="dev_smoke",
        )
        out = eng.process_position(
            pid,
            position_symbol="SMOKE",
            position_metadata=meta,
            current_underlying=und + 0.1,
            now=now,
            option_quote=quote,
        )
        log.info("OPTIONS_STEALTH | stage=dev_smoke | label=%s | process_result=%s", label, out is not None)
