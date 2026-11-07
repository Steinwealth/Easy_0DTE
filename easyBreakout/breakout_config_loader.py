#!/usr/bin/env python3
"""Load BreakoutConfig from env / get_config_value."""

from __future__ import annotations

from typing import Any, Callable, Optional

from .breakout_models import BreakoutConfig


def _f(get: Callable, key: str, default: float) -> float:
    try:
        return float(get(key, default))
    except Exception:
        return float(default)


def _i(get: Callable, key: str, default: int) -> int:
    try:
        return int(float(get(key, default)))
    except Exception:
        return int(default)


def _b(get: Callable, key: str, default: bool) -> bool:
    try:
        raw = get(key, default)
    except Exception:
        return bool(default)
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _s(get: Callable, key: str, default: str) -> str:
    try:
        return str(get(key, default) or default).strip()
    except Exception:
        return str(default)


def _frac_from_points(
    get: Callable, new_key: str, old_key: str, default_points: float
) -> float:
    """New keys are desk % (0.08 → 0.0008). Old keys stay fraction."""
    raw = get(new_key, None)
    if raw is not None and str(raw).strip() != "":
        try:
            return float(raw) / 100.0
        except (TypeError, ValueError):
            pass
    return _f(get, old_key, default_points / 100.0)


def _i_alias(get: Callable, new_key: str, old_key: str, default: int) -> int:
    raw = get(new_key, None)
    if raw is not None and str(raw).strip() != "":
        return _i(lambda k, d=None: raw if k == new_key else d, new_key, default)
    return _i(get, old_key, default)


def load_breakout_config_from_env(get_config_value: Optional[Callable] = None) -> BreakoutConfig:
    if get_config_value is None:
        try:
            from modules.config_loader import get_config_value as _g

            get_config_value = _g
        except Exception:
            import os

            get_config_value = lambda k, d=None: os.getenv(k, d)  # noqa: E731

    get = get_config_value
    return BreakoutConfig(
        min_break_distance_pct=_frac_from_points(
            get, "BREAKOUT_BREAK_MIN", "BREAKOUT_MIN_BREAK_DISTANCE_PCT", 0.08
        ),
        max_break_distance_pct=_frac_from_points(
            get, "BREAKOUT_BREAK_MAX", "BREAKOUT_MAX_BREAK_DISTANCE_PCT", 2.50
        ),
        min_follow_through_bars=_i_alias(
            get, "BREAKOUT_FT_MIN", "BREAKOUT_MIN_FOLLOW_THROUGH_BARS", 1
        ),
        min_continuation_pct=_frac_from_points(
            get, "BREAKOUT_CONT_MIN", "BREAKOUT_MIN_CONTINUATION_PCT", 0.0
        ),
        post_break_chop_max_range_pct=_frac_from_points(
            get, "BREAKOUT_EXPAND_MIN", "BREAKOUT_POST_BREAK_CHOP_MAX_RANGE_PCT", 0.0
        ),
        reclaim_invalidates=_b(get, "BREAKOUT_RECLAIM_INVALIDATES", True),
        min_volume_ratio=_f(get, "BREAKOUT_MIN_VOLUME_RATIO", 0.012),
        volume_ratio_gate_enabled=_b(get, "BREAKOUT_VOLUME_RATIO_GATE_ENABLED", False),
        require_vwap_alignment=_b(get, "BREAKOUT_REQUIRE_VWAP_ALIGNMENT", False),
        vwap_extension_reject_pct=_f(get, "BREAKOUT_VWAP_EXTENSION_REJECT_PCT", 0.15),
        call_min_rsi=_f(get, "BREAKOUT_CALL_MIN_RSI", 0.0),
        put_max_rsi=_f(get, "BREAKOUT_PUT_MAX_RSI", 100.0),
        min_quality_score=_f(get, "BREAKOUT_MIN_QUALITY_SCORE", 0.0),
        pc_oi_confirmation_enabled=_b(get, "BREAKOUT_PC_OI_CONFIRMATION_ENABLED", True),
        vwap_missing_fail_open=_b(get, "BREAKOUT_VWAP_MISSING_FAIL_OPEN", False),
        volume_missing_fail_closed=_b(get, "BREAKOUT_VOLUME_MISSING_FAIL_CLOSED", True),
        simple_entry_window_sec=_f(get, "BREAKOUT_SIMPLE_ENTRY_WINDOW_SEC", 300.0),
        otm_gate_enabled=_b(get, "BREAKOUT_OTM_GATE_ENABLED", True),
        prefer_max_otm_pct=_f(get, "BREAKOUT_PREFER_MAX_OTM_PCT", 0.055),
        research_max_otm_pct=_f(get, "BREAKOUT_RESEARCH_MAX_OTM_PCT", 0.055),
        cheap_lotto_guard_enabled=_b(get, "BREAKOUT_CHEAP_LOTTO_GUARD_ENABLED", True),
        cheap_lotto_max_premium=_f(get, "BREAKOUT_CHEAP_LOTTO_MAX_PREMIUM", 0.15),
        cheap_lotto_max_qty=_i(get, "BREAKOUT_CHEAP_LOTTO_MAX_QTY", 8),
        cheap_lotto_near_atm_otm_pct=_f(get, "BREAKOUT_CHEAP_LOTTO_NEAR_ATM_OTM_PCT", 0.05),
        cheap_lotto_mode=_s(get, "BREAKOUT_CHEAP_LOTTO_MODE", "reject"),
        cheap_lotto_downsize_to=_i(get, "BREAKOUT_CHEAP_LOTTO_DOWNSIZE_TO", 1),
        no_new_entries_after_pt=_s(get, "DISCOVERY_ORB2_TL_END_PT", _s(get, "BREAKOUT_NO_NEW_ENTRIES_AFTER_PT", "12:30")),
        watch_interval_sec=_f(get, "BREAKOUT_WATCH_INTERVAL_SEC", 30.0),
        opening_watch_interval_sec=_f(get, "BREAKOUT_OPENING_WATCH_INTERVAL_SEC", 15.0),
        watch_symbols_per_tick=_i(get, "BREAKOUT_WATCH_SYMBOLS_PER_TICK", 24),
        wait_break_reserve=_i(
            get,
            "BREAKOUT_DISCOVERY_WAIT_BREAK_RESERVE",
            _i(get, "0DTE_DISCOVERY_WAIT_BREAK_RESERVE", 0),
        ),
        seed_above_high_mult=_f(get, "BREAKOUT_SEED_ABOVE_HIGH_MULT", 1.001),
        seed_below_low_mult=_f(get, "BREAKOUT_SEED_BELOW_LOW_MULT", 0.999),
    )
