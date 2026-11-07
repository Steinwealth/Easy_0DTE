#!/usr/bin/env python3
"""
Load TrendlineConfig / option selection from merged app config (get_config_value).

Post-draw surface: pre-draw selector, watch/session ops, and live TRENDLINE_* keys
(canonical names only — no TRENDLINE_SIMPLE_* fallbacks).
"""

from __future__ import annotations

import logging
import os
from dataclasses import fields
from typing import Any, Callable, FrozenSet, Iterable, List, Tuple

from .trendline_models import TrendlineConfig, TrendlineOptionSelectionConfig

log = logging.getLogger(__name__)

_DEPRECATED_TRENDLINE_ENV_KEYS: FrozenSet[str] = frozenset(
    {
        "TRENDLINE_MIN_HOLD_SECONDS",
        "TRENDLINE_MIN_ENTRY_DISTANCE",
        "TRENDLINE_IMMEDIATE_BREAK_MIN_DISTANCE",
        "TRENDLINE_IMPULSE_MIN_BREAK_DISTANCE",
        "TRENDLINE_ENTRY_PROFILE",
        "TARGET_TRENDLINE_ACTIVE",
        "TRENDLINE_MAX_TRADES_PER_DAY",
        "TRENDLINE_MAX_POSITION_PCT",
        "TRENDLINE_ACCOUNT_ALLOCATION_PCT",
        "TRENDLINE_DEMO_STARTING_BALANCE",
        "TRENDLINE_DEMO_START_BALANCE",
        # Aug 12 prune — legacy geometry contest / identity overlays / duplicate floors
        "TRENDLINE_MIN_BREAK_PCT",
        "TRENDLINE_USE_ATR_BREAK",
        "TRENDLINE_ATR_BREAK_MULTIPLIER",
        "TRENDLINE_CONTROL_LINE_V3_ENABLED",
        "TRENDLINE_CUTOFF_CONTROL_LINE_ENABLED",
        "TRENDLINE_MSE_OVERRIDE_BLOCK_RATIO",
        "TRENDLINE_MSE_OVERRIDE_REQUIRES_RELEVANCE_PASS",
        "TRENDLINE_MSE_DOMINANCE_EXPANSION_MAX_LINE_DIST_MULT",
        "TRENDLINE_FALSE_BREAK_BYPASS_COUNTER_ORB_ONLY",
        "TRENDLINE_EXTREME_ORB_UPPER_MIN_PCT_FROM_LOW",
        "TRENDLINE_EXTREME_ORB_LOWER_MAX_PCT_FROM_LOW",
        "TRENDLINE_WITHIN_ORB_PREFER_DESCENDING_CALL_ON_UNCLEAR",
        "TRENDLINE_WITHIN_ORB_PREFER_DESCENDING_CALL_MIN_PCT",
        "TRENDLINE_WITHIN_ORB_PREFER_DESCENDING_CALL_MAX_PCT",
        "TRENDLINE_LINE_RELEVANCE_RETRY_ALTERNATES",
        "TRENDLINE_INDEX_MIN_BREAK_DISTANCE_PCT",
        "TRENDLINE_EMIT_MIN_BREAK_DISTANCE_PCT",
        "TRENDLINE_EMIT_INDEX_MIN_BREAK_DISTANCE_PCT",
        "TRENDLINE_DUAL_LANE_BLOCK_PUT_IF_BEAR_STRONGER",
        "TRENDLINE_DUAL_LANE_BEAR_DOMINANCE_RATIO",
        "TRENDLINE_EXPANSION_CALL_BREAK_MULT",
        "TRENDLINE_EXPANSION_CALL_MAX_ENTRY_CAP_PCT",
        "TRENDLINE_EXPANSION_BREAK_MIN_VOLUME_RATIO",
        "TRENDLINE_LIQUID_REVERSAL_MIN_VOLUME_RATIO",
        "TRENDLINE_POST_MATERIAL_BREAK_MIN_VOLUME_RATIO",
        "TRENDLINE_INDEX_MIN_VOLUME_RATIO",
        "TRENDLINE_INDEX_PUT_MIN_VOLUME_RATIO",
        "TRENDLINE_POST_BREAK_CHOP_MAX_RANGE_PCT",
        "TRENDLINE_GRADE_A_SUSTAIN_MIN_SCORE",
        "TRENDLINE_GRADE_A_SUSTAIN_MIN_SCORE_INDEX",
        "TRENDLINE_GRADE_A_SUSTAIN_MIN_SCORE_INDEX_PUT",
        "TRENDLINE_GRADE_A_SUSTAIN_MIN_CONTINUATION_PCT",
        "TRENDLINE_GRADE_A_SUSTAIN_CHOP_CONTINUATION_FACTOR",
        "TRENDLINE_GRADE_A_SUSTAIN_EARLY_CHASE_MAX_BREAK_PCT",
        "TRENDLINE_GRADE_A_SUSTAIN_EARLY_CHASE_MIN_MINUTES_AFTER_730",
        "TRENDLINE_CALL_MIN_RSI",
        "TRENDLINE_PUT_MAX_RSI",
        "TRENDLINE_REQUIRE_VWAP_ALIGNMENT",
        "TRENDLINE_DEFER_OPENING_BAR_EMIT_SEC",
        "TRENDLINE_OPENING_BAR_MAX_RANGE_TO_ORB_RATIO",
        "TRENDLINE_INDEX_OPENING_BAR_MAX_RANGE_TO_ORB_RATIO",
        "TRENDLINE_GRADE_A_SUSTAIN_LANE_ENABLED",
        "TRENDLINE_BLOCK_INDEX_ABOVE_ORB_PUT",
        "TRENDLINE_REQUIRE_MATERIAL_BREAK_SCORE",
        "TRENDLINE_MIN_FOLLOW_THROUGH_BARS",
        "TRENDLINE_MIN_CONTINUATION_PCT",
        "TRENDLINE_SIMPLE_BREAK_MODE",
        "TRENDLINE_PRIMARY_LANE_DESCENDING_CALL_ONLY",
        "TRENDLINE_CONTROL_LINE_MIN_SCORE",
        "TRENDLINE_CONTROL_LINE_MAX_CANDIDATES_PER_SIDE",
        "TRENDLINE_DUAL_LANE_MIN_SCORE_RATIO",
        "TRENDLINE_COMPRESSION_CALL_BAND_LOW",
        "TRENDLINE_COMPRESSION_CALL_BAND_HIGH",
        "TRENDLINE_COMPRESSION_STRENGTH_THRESHOLD",
        "TRENDLINE_COMPRESSION_BONUS_POINTS",
        "TRENDLINE_COMPRESSION_PERSIST_MIN_STRENGTH",
        "TRENDLINE_CORRIDOR_PCT_FLOOR",
        "TRENDLINE_USE_CORRIDOR_BREAK_DETECTION",
        "TRENDLINE_CUTOFF_CONTROL_NEAR_CUTOFF_MINUTES",
    }
)

_LOGGED_DEPRECATED: set[str] = set()

# Keys read by load_trendline_config_from_env (simple-only surface).
_TRENDLINE_MAIN_ENV_KEYS: Tuple[str, ...] = (
    "DISCOVERY_ORB2_TL_START_PT",
    "DISCOVERY_ORB2_TL_END_PT",
    "TRENDLINE_BUILD_TIME_PT",
    "TRENDLINE_EXPIRATION_TIME_PT",
    "TRENDLINE_MIN_ANCHOR_DISTANCE_MINUTES",
    "TRENDLINE_MIN_STRUCTURE_BARS",
    "TRENDLINE_MIN_STRUCTURE_SECONDS",
    "TRENDLINE_MAX_ACTIVE_MINUTES",
    "TRENDLINE_TRADE_ACTIVATE_MAX_AGE_SEC",
    "TRENDLINE_NO_NEW_ENTRIES_AFTER_PT",
    "TRENDLINE_MIN_EXPECTED_MOVE_PCT",
    "TRENDLINE_ORB_BIAS_MSE_SOFT_RATIO",
    "TRENDLINE_PRIMARY_LANE_ENFORCE_GEOMETRY_ALIGNMENT",
    "TRENDLINE_CONFIRM_PENDING_LOG_SEC",
    "TRENDLINE_FALSE_BREAK_GATE_LOG_THROTTLE_SEC",
    "TRENDLINE_FUNNEL_SUMMARY_INTERVAL_SEC",
    "TRENDLINE_BREAK_MODE",
    "TRENDLINE_MIN_SCORE",
    "TRENDLINE_MIN_BREAK_DISTANCE_PCT",
    "GATE_TL_Vol_Ratio_MIN",
    "TRENDLINE_MAX_ENTRY_DISTANCE_PCT",
    "TRENDLINE_ENTRY_WINDOW_SEC",
    "TRENDLINE_ENFORCE_GEOMETRY_SIDE",
    "TRENDLINE_ORB2_EXTENSION_ENVELOPE_ENABLED",
    "TRENDLINE_ORB2_ENVELOPE_WICK_BUFFER_PCT",
    "TRENDLINE_ORB2_ENVELOPE_MIN_BREAK_DISTANCE_PCT",
    "TRENDLINE_ORB2_ENVELOPE_MIN_ANCHOR_SECONDS",
    "TRENDLINE_ORB2_RECLAIM_INSIDE_PCT",
    "TRENDLINE_PRE730_STRUCTURE_ENVELOPE_ENABLED",
    "TRENDLINE_PRE730_ENVELOPE_WICK_BUFFER_PCT",
    "TRENDLINE_PRE730_ENVELOPE_MIN_ANCHOR_SECONDS",
)

_TRENDLINE_CONSUMED_OUTSIDE_LOADER: Tuple[str, ...] = (
    "TRENDLINE_0DTE_MAX_POSITIONS",
    "TRENDLINE_ALLOW_SYNTHETIC_ENTRY",
    "TRENDLINE_BUILD_YF_1M_BACKFILL",
    "TRENDLINE_BUILD_YF_1M_BATCH_SIZE",
    "TRENDLINE_BUILD_YF_1M_BATCH_SLEEP_SEC",
    "TRENDLINE_BUILD_YF_1M_MAX_SYMBOLS",
    "TRENDLINE_DEFER_BUILD_UNTIL_ORB_BATCH",
    "TRENDLINE_DEFER_POST_BUILD_WATCH_UNTIL_ORB_BATCH",
    "TRENDLINE_DIR_VOLUME_PCT",
    "TRENDLINE_EXIT_CONFIRMATION_TIMEOUT_SECONDS",
    "TRENDLINE_OTM_GATE_ENABLED",
    "TRENDLINE_PREFER_MAX_OTM_PCT",
    "TRENDLINE_RESEARCH_MAX_OTM_PCT",
    "TRENDLINE_UNCLOSABLE_ALERT_AFTER_SEC",
    "TRENDLINE_BUILD_ANCHOR_LOG_MAX",
    "TRENDLINE_DATA_CHUNK_SIZE",
    "TRENDLINE_ENABLE_BUILD_DEGRADATION",
    "TRENDLINE_EXIT_MIN_HOLD_SECONDS",
    "TRENDLINE_GLOBAL_DAILY_CAP_ENABLED",
    "TRENDLINE_GLOBAL_DAILY_CAP_FAIL_OPEN",
    "TRENDLINE_GLOBAL_DAILY_CAP_NAMESPACE",
    "TRENDLINE_IMPULSE_BREAKEVEN_ACTIVATE_PCT",
    "TRENDLINE_IMPULSE_TRAIL_ACTIVATE_PCT",
    "TRENDLINE_IMPULSE_TRAIL_GIVEBACK_PCT",
    "TRENDLINE_MAX_BUILD_DURATION_MS",
    "TRENDLINE_MAX_INTRADAY_BATCH_CALLS_PER_BUILD",
    "TRENDLINE_MAX_QUOTE_BATCH_CALLS_PER_BUILD",
    "TRENDLINE_MAX_SYMBOLS_PER_BUILD",
    "TRENDLINE_MAX_OPEN_POSITIONS",
    "TRENDLINE_MIN_DIAGNOSTIC_HOLD_SECONDS",
    "TRENDLINE_MONITOR_ALERT_ENABLED",
    "TRENDLINE_MONITOR_ALERT_GLOBAL_DEDUP_ENABLED",
    "TRENDLINE_MONITOR_ALERT_GLOBAL_DEDUP_FAIL_OPEN",
    "TRENDLINE_MONITOR_ALERT_GLOBAL_DEDUP_NAMESPACE",
    "TRENDLINE_MONITOR_ALERT_INTERVAL_SEC",
    "TRENDLINE_OPENING_WATCH_INTERVAL_SEC",
    "TRENDLINE_MONITOR_ALERT_ONLY_ON_CHANGE",
    "TRENDLINE_OPTION_FORCE_EXIT_NO_DATA_SECONDS",
    "TRENDLINE_OPTION_NO_DATA_GRACE_SECONDS",
    "TRENDLINE_OPTION_NO_DATA_FAVORABLE_UNDERLYING_MOVE_PCT",
    "TRENDLINE_DELTA_TOLERANCE",
    "TRENDLINE_OPTION_REQUIRE_EXIT_GRADE_BEFORE_FORCE_EXIT",
    "TRENDLINE_OPTION_SENSITIVITY",
    "TRENDLINE_RETEST_BREAKEVEN_ACTIVATE_PCT",
    "TRENDLINE_RETEST_TRAIL_ACTIVATE_PCT",
    "TRENDLINE_RETEST_TRAIL_GIVEBACK_PCT",
    "TRENDLINE_RED_DAY_ALLOW_PUT",
    "TRENDLINE_RED_DAY_ALLOW_TIER1_CALL",
    "TRENDLINE_RED_DAY_GATE_ENABLED",
    "TRENDLINE_SLOT_COUNT",
    "TRENDLINE_SLOW_TREND_BREAKEVEN_ACTIVATE_PCT",
    "TRENDLINE_SLOW_TREND_TRAIL_ACTIVATE_PCT",
    "TRENDLINE_SLOW_TREND_TRAIL_GIVEBACK_PCT",
    "TRENDLINE_TRAIL_ACTIVATE_PCT",
    "TRENDLINE_TRAIL_GIVEBACK_PCT",
    "TRENDLINE_USE_FULL_0DTE_LIST",
    "TRENDLINE_WATCH_EMPTY_QUOTES_LOG_SEC",
    "TRENDLINE_WATCH_IDLE_LOG_SEC",
    "TRENDLINE_WATCH_NEARLINE_LOG_SEC",
    "TRENDLINE_WATCH_STATUS_LOG_SEC",
    "TRENDLINE_BREAKEVEN_ACTIVATE_PCT",
    "TRENDLINE_BREAKEVEN_OFFSET_PCT",
    "TRENDLINE_WATCH_INTERVAL_SEC",
    "TRENDLINE_WATCH_SYMBOLS_PER_TICK",
    "TRENDLINE_WATCH_YIELD_EVERY_N",
    "TRENDLINE_WATCH_HYDRATE_QUOTE_CAP",
    "TRENDLINE_WATCH_YF_1M_BACKFILL",
    "TRENDLINE_WATCH_YF_1M_MIN_BARS",
    "TRENDLINE_WATCH_YF_1M_MAX_SYMBOLS",
    "TRENDLINE_WATCH_YF_1M_BATCH_SIZE",
    "TRENDLINE_WATCH_YF_1M_BATCH_SLEEP_SEC",
    "TRENDLINE_WATCH_YF_1M_MAX_STALE_SEC",
    "TRENDLINE_WATCH_YF_1M_SYMBOL_COOLDOWN_SEC",
    "TRENDLINE_WATCH_YF_1M_FORCE_COOLDOWN_SEC",
    "TRENDLINE_WATCH_YF_1M_NEAR_BREAK_BUFFER_PCT",
    "YAHOO_BACKFILL_MANAGER_ENABLED",
    "YAHOO_BACKFILL_PACING_ENABLED",
    "YAHOO_BACKFILL_MAX_DOWNLOADS_PER_MIN",
    "YAHOO_BACKFILL_MAX_CONCURRENT_DOWNLOADS",
    "YAHOO_BACKFILL_COALESCE_MS",
    "YAHOO_BACKFILL_CACHE_TTL_SEC",
    "YAHOO_BACKFILL_WATCH_CACHE_TTL_SEC",
    "YAHOO_BACKFILL_BUILD_CACHE_TTL_SEC",
    "YAHOO_BACKFILL_SLOW_MS",
    "YAHOO_BACKFILL_BACKOFF_BASE_SEC",
    "YAHOO_BACKFILL_BACKOFF_MAX_SEC",
    "TRENDLINE_INDEX_MIN_BREAK_DISTANCE_PCT",
    "TRENDLINE_GEOMETRY_PHASE1_ENABLED",
    "TRENDLINE_GEOMETRY_AUDIT_LOGS",
    "TRENDLINE_MAX_LINE_DISTANCE_AT_CUTOFF_PCT",
    "TRENDLINE_LINE_RELEVANCE_RETRY_ALTERNATES",
    "TRENDLINE_MAX_LINE_DISTANCE_VOLATILITY_ADJUST",
    "TRENDLINE_MSE_OVERRIDE_BLOCK_RATIO",
    "TRENDLINE_MSE_OVERRIDE_REQUIRES_RELEVANCE_PASS",
    "TRENDLINE_MSE_DOMINANCE_EXPANSION_MAX_LINE_DIST_MULT",
    "TRENDLINE_GEOMETRY_SKIP_TREND_CONTINUATION",
)

_TRENDLINE_OPTION_ENV_KEYS: Tuple[str, ...] = (
    "TRENDLINE_OPTION_DELTA_MIN",
    "TRENDLINE_OPTION_DELTA_MAX",
    "TRENDLINE_OPTION_MAX_SPREAD_PCT",
    "TRENDLINE_OPTION_STRIKE_MODE",
    "TRENDLINE_OPTION_LOTTO_MODE",
    "TRENDLINE_OPTION_MIN_OPEN_INTEREST",
    "TRENDLINE_OPTION_MIN_VOLUME",
)

_OPTION_STEALTH_TLINE_ENV_KEYS: Tuple[str, ...] = (
    "OPTION_STEALTH_TLINE_TIME_EXIT_MINUTES",
    "OPTION_STEALTH_TLINE_NO_PROGRESS_EXIT_MINUTES",
    "OPTION_STEALTH_TLINE_DEGRADED_EXIT_MIN_DURATION",
    "OPTION_STEALTH_TLINE_MIN_SECONDS_AFTER_BE_ACTIVATION",
    "OPTION_STEALTH_TLINE_BE_TRIGGER_MULT",
    "OPTION_STEALTH_TLINE_TRAILING_TRIGGER_MULT",
    "OPTION_STEALTH_TLINE_BE_DELTA_HIGH_THRESHOLD",
    "OPTION_STEALTH_TLINE_BE_DELTA_MID_THRESHOLD",
    "OPTION_STEALTH_TLINE_BE_TRIGGER_HIGH_DELTA_PCT",
    "OPTION_STEALTH_TLINE_BE_TRIGGER_MID_DELTA_PCT",
    "OPTION_STEALTH_TLINE_BE_TRIGGER_LOW_DELTA_PCT",
    "OPTION_STEALTH_TLINE_MIN_SECONDS_BEFORE_BE",
    "OPTION_STEALTH_TLINE_BASE_TRAILING_PCT",
    "OPTION_STEALTH_TLINE_AGGRESSIVE_TRAILING_PCT",
    "OPTION_STEALTH_TLINE_AGGRESSIVE_TRIGGER_PCT",
    "OPTION_STEALTH_TLINE_EXPLOSIVE_TRAILING_PCT",
    "OPTION_STEALTH_TLINE_RUNNER_TRAILING_PCT",
    "OPTION_STEALTH_TLINE_EXPLOSIVE_TRIGGER_PCT",
    "OPTION_STEALTH_TLINE_RUNNER_TRIGGER_PCT",
    "OPTION_STEALTH_TLINE_NO_PROGRESS_EARLY_MINUTES",
    "OPTION_STEALTH_TLINE_NO_PROGRESS_EARLY_MAX_PNL_PCT",
    "OPTION_STEALTH_TLINE_DEGRADED_NON_EXACT_TIGHTEN_AFTER_SECONDS",
    "OPTION_STEALTH_TLINE_DEGRADED_NON_EXACT_TRAIL_TIGHTEN_SCALE",
    "OPTION_STEALTH_TLINE_CHOP_HOLD_MIN_PNL_PCT",
    "OPTION_STEALTH_TLINE_IMPULSE_TP_TARGET_PCT",
    "OPTION_STEALTH_TLINE_IMPULSE_TRAILING_TRIGGER_PCT",
    "OPTION_STEALTH_TLINE_IMPULSE_TRAILING_PCT",
    "OPTION_STEALTH_TLINE_IMPULSE_TIME_EXIT_MINUTES",
    "OPTION_STEALTH_TLINE_IMPULSE_NO_PROGRESS_EXIT_MINUTES",
    "OPTION_STEALTH_TLINE_EARLY_TRAILING_TRIGGER_PCT",
    "OPTION_STEALTH_TLINE_EARLY_TRAILING_PCT",
    "OPTION_STEALTH_TLINE_EARLY_TIME_EXIT_MINUTES",
    "OPTION_STEALTH_TLINE_DRAWDOWN_EXIT_MIN_MAX_PNL_PCT",
    "OPTION_STEALTH_TLINE_DRAWDOWN_EXIT_MIN_DRAWDOWN_PCT",
    "OPTION_STEALTH_TLINE_RETEST_MFE_TRIGGER_PCT",
    "OPTION_STEALTH_TLINE_RETEST_MFE_DRAWDOWN_EXIT_PCT",
    "OPTION_STEALTH_TLINE_IMPULSE_TRAIL_ACTIVATION_CAP_DEGRADED",
    "OPTION_STEALTH_TLINE_UNDERLYING_OPPORTUNITY_PCT",
    "OPTION_STEALTH_TLINE_UNDERLYING_RECLAIM_PROTECT",
    "OPTION_STEALTH_TLINE_MIN_HOLD_BYPASS_MFE_PCT",
    "OPTION_STEALTH_TLINE_MICRO_LOCK_TRIGGER_PCT",
    "OPTION_STEALTH_TLINE_BE_RELAX_HWM_WITH_UNDERLYING",
    "OPTION_STEALTH_TLINE_BE_RELAX_HWM_MIN_PNL_PCT",
    "OPTION_STEALTH_TLINE_NO_PROGRESS_SINGLE_RELAX_EXIT_GRADE",
    "OPTION_STEALTH_TLINE_NO_PROGRESS_SINGLE_MAX_MFE_PCT",
    "OPTION_STEALTH_STATE_LOG_SECONDS",
    "OPTION_STEALTH_TLINE_BE_GIVEBACK_EXIT_DRAWDOWN_PCT",
    "OPTION_STEALTH_TLINE_IMPULSE_MFE_TRIGGER_PCT",
    "OPTION_STEALTH_TLINE_IMPULSE_MFE_DRAWDOWN_EXIT_PCT",
    "OPTION_STEALTH_TLINE_SLOW_MFE_TRIGGER_PCT",
    "OPTION_STEALTH_TLINE_SLOW_MFE_DRAWDOWN_EXIT_PCT",
)

ALL_TRENDLINE_CONSUMED_ENV_KEYS: FrozenSet[str] = frozenset(
    _TRENDLINE_MAIN_ENV_KEYS
    + _TRENDLINE_OPTION_ENV_KEYS
    + _OPTION_STEALTH_TLINE_ENV_KEYS
    + _TRENDLINE_CONSUMED_OUTSIDE_LOADER
    + tuple(_DEPRECATED_TRENDLINE_ENV_KEYS)
)


def _warn_deprecated_trendline_env_keys(extra_scan_keys: Iterable[str] = ()) -> None:
    keys = sorted(_DEPRECATED_TRENDLINE_ENV_KEYS | frozenset(extra_scan_keys))
    for key in keys:
        if key in _LOGGED_DEPRECATED:
            continue
        if key not in os.environ:
            continue
        _LOGGED_DEPRECATED.add(key)
        log.warning("TRENDLINE_CONFIG_DEPRECATED | key=%s | detail=ignored_use_canonical_loader", key)


def warn_unused_trendline_related_env_keys() -> None:
    for key in sorted(os.environ.keys()):
        if not (key.startswith("TRENDLINE_") or key.startswith("OPTION_STEALTH_TLINE_")):
            continue
        if key in ALL_TRENDLINE_CONSUMED_ENV_KEYS:
            continue
        log.warning(
            "TRENDLINE_CONFIG_UNUSED_WARNING | key=%s | detail=present_in_environment_but_not_listed_as_consumed_by_trendline_config_loader",
            key,
        )


def _gv(get_config_value: Callable[[str, Any], Any], key: str, default: Any) -> Any:
    try:
        return get_config_value(key, default)
    except Exception:
        v = os.getenv(key)
        return default if v is None else v


def _raw_present(get_config_value: Callable[[str, Any], Any], key: str) -> bool:
    try:
        v = get_config_value(key, None)
    except Exception:
        v = os.getenv(key)
    if v is None:
        return False
    if isinstance(v, str) and not v.strip():
        return False
    return True

def _to_float(x: Any, default: float) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def _to_int(x: Any, default: int) -> int:
    try:
        return int(float(x))
    except (TypeError, ValueError):
        return default


def _to_bool(x: Any, default: bool) -> bool:
    if isinstance(x, bool):
        return x
    if x is None:
        return default
    s = str(x).strip().lower()
    if s in ("true", "1", "yes", "on"):
        return True
    if s in ("false", "0", "no", "off"):
        return False
    return default


def _read_f(
    get_config_value: Callable[[str, Any], Any],
    key: str,
    default: float,
    defaults_used: List[str],
) -> float:
    if not _raw_present(get_config_value, key):
        defaults_used.append(key)
    return _to_float(_gv(get_config_value, key, default), default)


def _read_i(
    get_config_value: Callable[[str, Any], Any],
    key: str,
    default: int,
    defaults_used: List[str],
) -> int:
    if not _raw_present(get_config_value, key):
        defaults_used.append(key)
    return _to_int(_gv(get_config_value, key, default), default)


def _read_b(
    get_config_value: Callable[[str, Any], Any],
    key: str,
    default: bool,
    defaults_used: List[str],
) -> bool:
    if not _raw_present(get_config_value, key):
        defaults_used.append(key)
    return _to_bool(_gv(get_config_value, key, default), default)


def _read_s(
    get_config_value: Callable[[str, Any], Any],
    key: str,
    default: str,
    defaults_used: List[str],
) -> str:
    if not _raw_present(get_config_value, key):
        defaults_used.append(key)
    raw = _gv(get_config_value, key, default)
    s = str(raw).strip() if raw is not None else ""
    return s if s else default


def _read_s_first(
    get_config_value: Callable[[str, Any], Any],
    keys: Tuple[str, ...],
    default: str,
    defaults_used: List[str],
) -> str:
    for key in keys:
        if _raw_present(get_config_value, key):
            return _read_s(get_config_value, key, default, defaults_used)
    if keys:
        defaults_used.append(keys[0])
    return default


def _read_f_first(
    get_config_value: Callable[[str, Any], Any],
    keys: Tuple[str, ...],
    default: float,
    defaults_used: List[str],
) -> float:
    for key in keys:
        if _raw_present(get_config_value, key):
            return _to_float(_gv(get_config_value, key, default), default)
    defaults_used.append(keys[0])
    return float(default)


def _read_i_first(
    get_config_value: Callable[[str, Any], Any],
    keys: Tuple[str, ...],
    default: int,
    defaults_used: List[str],
) -> int:
    for key in keys:
        if _raw_present(get_config_value, key):
            return _to_int(_gv(get_config_value, key, default), default)
    defaults_used.append(keys[0])
    return int(default)


def _log_loaded_kv(prefix: str, pairs: List[Tuple[str, Any]]) -> None:
    for name, val in pairs:
        log.info("%s | key=%s | value=%s", prefix, name, val)


def log_trendline_simple_config_audit(cfg: TrendlineConfig) -> None:
    log.warning(
        "TRENDLINE_SIMPLE_CONFIG_AUDIT | simple_break_mode=%s | min_score=%.2f | min_break_pct=%.6f | "
        "min_volume_ratio=%.3f | vwap_qg=GATE_TL_VWAP | "
        "rsi_qg=GATE_TL_RSI | max_entry_distance_pct=%.6f | entry_window_sec=%.1f",
        str(bool(cfg.simple_break_mode)).lower(),
        float(cfg.simple_min_score),
        float(cfg.simple_min_break_distance_pct),
        float(cfg.simple_min_volume_ratio),
        float(cfg.simple_max_entry_distance_pct),
        float(cfg.simple_entry_window_sec),
    )


def log_trendline_config_ownership_audit(get_config_value: Callable[[str, Any], Any]) -> None:
    try:
        from modules import config_profiles as cp

        repo_tl_flat = cp._repo_trendline_flat()
        active_tl_env = sorted(k for k in repo_tl_flat if k.startswith("TRENDLINE_") or k == "ENABLE_TRENDLINE_STRATEGY")
        unused_like = sorted(
            k for k in os.environ if k.startswith("TRENDLINE_") and k not in ALL_TRENDLINE_CONSUMED_ENV_KEYS
        )
        simple_on = _to_bool(_gv(get_config_value, "TRENDLINE_BREAK_MODE", True), True)
        log.warning(
            "TRENDLINE_CONFIG_OWNERSHIP_AUDIT | simple_break_mode=%s | trendline_file_key_count=%d | "
            "loader_main_key_count=%d | unused_env_key_count=%d",
            str(simple_on).lower(),
            len(active_tl_env),
            len(_TRENDLINE_MAIN_ENV_KEYS),
            len(unused_like),
        )
    except Exception as exc:  # pragma: no cover
        log.debug("TRENDLINE_CONFIG_OWNERSHIP_AUDIT_SKIP | reason=%s", exc)


def load_trendline_config_from_env(get_config_value: Callable[[str, Any], Any]) -> TrendlineConfig:
    """Build ``TrendlineConfig`` from env (simple-only loader surface)."""
    _warn_deprecated_trendline_env_keys(())
    defaults_used: List[str] = []
    g = get_config_value
    base = TrendlineConfig()

    primary_lane_geom = _read_b(
        g,
        "TRENDLINE_PRIMARY_LANE_ENFORCE_GEOMETRY_ALIGNMENT",
        base.primary_lane_enforce_geometry_alignment,
        defaults_used,
    )

    cfg = TrendlineConfig(
        build_time_pt=_read_s_first(
            g,
            ("DISCOVERY_ORB2_TL_START_PT", "TRENDLINE_BUILD_TIME_PT"),
            base.build_time_pt,
            defaults_used,
        ),
        expiration_time_pt=_read_s_first(
            g,
            ("DISCOVERY_ORB2_TL_END_PT", "TRENDLINE_EXPIRATION_TIME_PT"),
            base.expiration_time_pt,
            defaults_used,
        ),
        # Aug 12 prune Wave A — legacy ATR/min_break contest (model defaults only)
        min_break_pct=base.min_break_pct,
        use_atr_break=base.use_atr_break,
        atr_break_multiplier=base.atr_break_multiplier,
        min_anchor_distance_minutes=_read_i(
            g, "TRENDLINE_MIN_ANCHOR_DISTANCE_MINUTES", base.min_anchor_distance_minutes, defaults_used
        ),
        min_structure_bars=_read_i(g, "TRENDLINE_MIN_STRUCTURE_BARS", base.min_structure_bars, defaults_used),
        min_structure_seconds=_read_f(g, "TRENDLINE_MIN_STRUCTURE_SECONDS", base.min_structure_seconds, defaults_used),
        max_active_minutes=_read_f(g, "TRENDLINE_MAX_ACTIVE_MINUTES", base.max_active_minutes, defaults_used),
        trade_activate_max_age_sec=_read_f(
            g, "TRENDLINE_TRADE_ACTIVATE_MAX_AGE_SEC", base.trade_activate_max_age_sec, defaults_used
        ),
        no_new_entries_after_pt=_read_s_first(
            g,
            ("DISCOVERY_ORB2_TL_END_PT", "TRENDLINE_NO_NEW_ENTRIES_AFTER_PT"),
            base.no_new_entries_after_pt,
            defaults_used,
        ),
        min_expected_move_pct=_read_f(g, "TRENDLINE_MIN_EXPECTED_MOVE_PCT", base.min_expected_move_pct, defaults_used),
        # Aug 12 prune: v3 contest off. Dataclass defaults kept for unused control-line helpers.
        control_line_v3_enabled=base.control_line_v3_enabled,
        control_line_min_score=base.control_line_min_score,
        control_line_max_candidates_per_side=base.control_line_max_candidates_per_side,
        dual_lane_min_score_ratio=base.dual_lane_min_score_ratio,
        compression_call_band_low=base.compression_call_band_low,
        compression_call_band_high=base.compression_call_band_high,
        compression_strength_threshold=base.compression_strength_threshold,
        compression_bonus_points=base.compression_bonus_points,
        compression_persist_min_strength=base.compression_persist_min_strength,
        corridor_pct_floor=base.corridor_pct_floor,
        use_corridor_break_detection=base.use_corridor_break_detection,
        orb_bias_mse_soft_ratio=_read_f(
            g, "TRENDLINE_ORB_BIAS_MSE_SOFT_RATIO", base.orb_bias_mse_soft_ratio, defaults_used
        ),
        primary_lane_enforce_geometry_alignment=primary_lane_geom,
        # Aug 12 prune Wave A — extreme-ORB / within-ORB prefer / false-break bypass
        false_break_bypass_counter_orb_only=base.false_break_bypass_counter_orb_only,
        extreme_orb_upper_min_pct_from_low=base.extreme_orb_upper_min_pct_from_low,
        extreme_orb_lower_max_pct_from_low=base.extreme_orb_lower_max_pct_from_low,
        within_orb_prefer_descending_call_on_unclear=base.within_orb_prefer_descending_call_on_unclear,
        within_orb_prefer_descending_call_min_pct=base.within_orb_prefer_descending_call_min_pct,
        within_orb_prefer_descending_call_max_pct=base.within_orb_prefer_descending_call_max_pct,
        geometry_phase1_enabled=_read_b(
            g, "TRENDLINE_GEOMETRY_PHASE1_ENABLED", base.geometry_phase1_enabled, defaults_used
        ),
        max_line_distance_at_cutoff_pct=_read_f(
            g,
            "TRENDLINE_MAX_LINE_DISTANCE_AT_CUTOFF_PCT",
            base.max_line_distance_at_cutoff_pct,
            defaults_used,
        ),
        line_relevance_retry_alternates=base.line_relevance_retry_alternates,
        max_line_distance_volatility_adjust=_read_b(
            g,
            "TRENDLINE_MAX_LINE_DISTANCE_VOLATILITY_ADJUST",
            base.max_line_distance_volatility_adjust,
            defaults_used,
        ),
        mse_override_block_ratio=base.mse_override_block_ratio,
        mse_override_requires_relevance_pass=base.mse_override_requires_relevance_pass,
        mse_dominance_expansion_max_line_dist_mult=base.mse_dominance_expansion_max_line_dist_mult,
        geometry_skip_trend_continuation=_read_b(
            g,
            "TRENDLINE_GEOMETRY_SKIP_TREND_CONTINUATION",
            base.geometry_skip_trend_continuation,
            defaults_used,
        ),
        cutoff_control_line_enabled=base.cutoff_control_line_enabled,
        cutoff_control_near_cutoff_minutes=base.cutoff_control_near_cutoff_minutes,
        pre730_structure_envelope_enabled=_read_b(
            g,
            "TRENDLINE_PRE730_STRUCTURE_ENVELOPE_ENABLED",
            base.pre730_structure_envelope_enabled,
            defaults_used,
        ),
        pre730_envelope_wick_buffer_pct=_read_f(
            g,
            "TRENDLINE_PRE730_ENVELOPE_WICK_BUFFER_PCT",
            base.pre730_envelope_wick_buffer_pct,
            defaults_used,
        ),
        pre730_envelope_min_anchor_seconds=_read_f(
            g,
            "TRENDLINE_PRE730_ENVELOPE_MIN_ANCHOR_SECONDS",
            base.pre730_envelope_min_anchor_seconds,
            defaults_used,
        ),
        confirm_pending_log_sec=_read_f(
            g, "TRENDLINE_CONFIRM_PENDING_LOG_SEC", base.confirm_pending_log_sec, defaults_used
        ),
        false_break_gate_log_throttle_sec=_read_f(
            g, "TRENDLINE_FALSE_BREAK_GATE_LOG_THROTTLE_SEC", base.false_break_gate_log_throttle_sec, defaults_used
        ),
        trendline_funnel_summary_interval_sec=_read_f(
            g, "TRENDLINE_FUNNEL_SUMMARY_INTERVAL_SEC", base.trendline_funnel_summary_interval_sec, defaults_used
        ),
        simple_break_mode=_read_b(g, "TRENDLINE_BREAK_MODE", True, defaults_used),
        simple_min_score=_read_f(g, "TRENDLINE_MIN_SCORE", base.simple_min_score, defaults_used),
        simple_min_break_distance_pct=_read_f(
            g, "TRENDLINE_MIN_BREAK_DISTANCE_PCT", base.simple_min_break_distance_pct, defaults_used
        ),
        simple_min_volume_ratio=_read_f(
            g, "GATE_TL_Vol_Ratio_MIN", base.simple_min_volume_ratio, defaults_used
        ),
        simple_max_entry_distance_pct=_read_f(
            g, "TRENDLINE_MAX_ENTRY_DISTANCE_PCT", base.simple_max_entry_distance_pct, defaults_used
        ),
        simple_entry_window_sec=_read_f(
            g, "TRENDLINE_ENTRY_WINDOW_SEC", base.simple_entry_window_sec, defaults_used
        ),
        simple_enforce_geometry_side=_read_b(
            g,
            "TRENDLINE_ENFORCE_GEOMETRY_SIDE",
            base.simple_enforce_geometry_side,
            defaults_used,
        ),
        orb2_extension_envelope_enabled=_read_b(
            g,
            "TRENDLINE_ORB2_EXTENSION_ENVELOPE_ENABLED",
            base.orb2_extension_envelope_enabled,
            defaults_used,
        ),
        orb2_envelope_wick_buffer_pct=_read_f(
            g,
            "TRENDLINE_ORB2_ENVELOPE_WICK_BUFFER_PCT",
            base.orb2_envelope_wick_buffer_pct,
            defaults_used,
        ),
        orb2_envelope_min_break_distance_pct=_read_f(
            g,
            "TRENDLINE_ORB2_ENVELOPE_MIN_BREAK_DISTANCE_PCT",
            base.orb2_envelope_min_break_distance_pct,
            defaults_used,
        ),
        orb2_envelope_min_anchor_seconds=_read_f(
            g,
            "TRENDLINE_ORB2_ENVELOPE_MIN_ANCHOR_SECONDS",
            base.orb2_envelope_min_anchor_seconds,
            defaults_used,
        ),
        orb2_reclaim_inside_pct=_read_f(
            g,
            "TRENDLINE_ORB2_RECLAIM_INSIDE_PCT",
            base.orb2_reclaim_inside_pct,
            defaults_used,
        ),
    )

    pairs: List[Tuple[str, Any]] = [(f.name, getattr(cfg, f.name)) for f in fields(cfg)]
    _log_loaded_kv("TRENDLINE_CONFIG_LOADED", pairs)
    log_trendline_simple_config_audit(cfg)
    log_trendline_config_ownership_audit(get_config_value)

    for dk in sorted(set(defaults_used)):
        log.info("TRENDLINE_CONFIG_DEFAULT_USED | key=%s", dk)

    return cfg


def load_trendline_option_selection_config(
    get_config_value: Callable[[str, Any], Any],
) -> TrendlineOptionSelectionConfig:
    defaults_used: List[str] = []
    g = get_config_value
    base = TrendlineOptionSelectionConfig()
    cfg = TrendlineOptionSelectionConfig(
        delta_min=_read_f_first(g, ("0DTE_ENTRY_DELTA_MIN", "TRENDLINE_OPTION_DELTA_MIN"), base.delta_min, defaults_used),
        delta_max=_read_f_first(g, ("0DTE_ENTRY_DELTA_MAX", "TRENDLINE_OPTION_DELTA_MAX"), base.delta_max, defaults_used),
        max_bid_ask_spread_pct=_read_f(g, "TRENDLINE_OPTION_MAX_SPREAD_PCT", base.max_bid_ask_spread_pct, defaults_used),
        strike_mode=_read_s(g, "TRENDLINE_OPTION_STRIKE_MODE", base.strike_mode, defaults_used),
        lotto_mode=_read_b(g, "TRENDLINE_OPTION_LOTTO_MODE", base.lotto_mode, defaults_used),
        min_open_interest=_read_i_first(g, ("0DTE_ENTRY_MIN_OPEN_INTEREST", "TRENDLINE_OPTION_MIN_OPEN_INTEREST"), base.min_open_interest, defaults_used),
        min_volume=_read_i_first(g, ("0DTE_ENTRY_MIN_VOLUME", "TRENDLINE_OPTION_MIN_VOLUME"), base.min_volume, defaults_used),
    )
    log.info(
        "TRENDLINE_OPTION_CONFIG | strike_mode=%s | lotto=%s | oi=%d | volume=%d | delta_min=%.3f | delta_max=%.3f | max_spread_pct=%.4f",
        cfg.strike_mode,
        str(cfg.lotto_mode).lower(),
        cfg.min_open_interest,
        cfg.min_volume,
        cfg.delta_min,
        cfg.delta_max,
        cfg.max_bid_ask_spread_pct,
    )
    for dk in sorted(set(defaults_used)):
        if dk.startswith("TRENDLINE_OPTION_"):
            log.info("TRENDLINE_CONFIG_DEFAULT_USED | key=%s", dk)
    return cfg
