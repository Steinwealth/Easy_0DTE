"""
Priority Optimizer feature schemas — path-specific (Rev 00401+).

ORB SO retains ETF-specific fields. ORB1 Breakout and Trendline 0DTE share one
unified 0DTE schema (underlying + options microstructure + ORB pipeline funnel
+ Trendline structure) so break, emit, and execution snapshots capture the same
complete dataset; ``snapshot_strategy`` and ``stage`` label the path/moment.

SCHEMA_VERSION 3 (Sep 2026): drop densify keys that never filled on live 0DTE
PO snaps (legacy SO / unused monitor aliases / dead MFE placeholders). Sparse
but real keys (trajectory 5m deltas, rare pipeline stamps) are retained.

SCHEMA_VERSION 4 (Sep 18, 2026): drop 17 mining-dead densify keys (AUC ~0.5 /
never top-40 / not in B18, plus low-coverage observe leftovers). Live Path SS
admit still stamps candle body/wick on ``qg_admit``; watch continuation and
viability_diag contract counts stay off-snapshot.

SCHEMA_VERSION 5 (Sep 23, 2026): owned-contract premium trajectory. Current,
~30s, and ~5m anchors for premium, bid/mid/ask, underlying, option L1, spread,
IV, and delta, plus horizon-named change, slope/R², elasticity, and path-state
summaries. No bare ``premium_change_pct``. Observe-only; not a live gate.

SCHEMA_VERSION 6 (Sep 26, 2026): path-signal stamps on every entry, exit, and
20p densify. ``underlying_volume_1m``, ``break_level``, and ``break_timestamp``
join the unified list. Volume ratio, session volume, chain open interest, owned
open interest, and Bollinger width were already schema keys and stay filled by
the existing hydration.

SCHEMA_VERSION 7 (Sep 26, 2026): same key set plus the two near-spot moon
deltas. Dense snapshots use the stable semantic order in
``SNAPSHOT_SCHEMA_ORDER``. Research rank lives separately in
``USEFULNESS_ORDER`` and does not move snapshot columns. Gate evaluation
order is unchanged.

SCHEMA_VERSION 8 (Oct 19, 2026): same 267 densified keys. Mid-trade lifecycle
snapshots (age_120s, first_red, pullback) add an envelope
``lifecycle_event`` block. Old readers ignore the extra keys.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from .po_key_usefulness import SNAPSHOT_SCHEMA_ORDER, order_keys
from .premium_trajectory_v5 import FEATURE_KEYS_PREMIUM_TRAJECTORY_V5

SCHEMA_VERSION = 8

# Extra keys on mid-trade lifecycle snapshots. Not part of the 267-key densify.
FEATURE_KEYS_LIFECYCLE_EVENT: Tuple[str, ...] = (
    "snapshot_type",
    "event_ts",
    "seconds_since_entry",
    "pret",
    "peak_pret",
    "peak_ts",
    "seconds_since_peak",
    "pullback_depth",
    "rearm_cycle",
    "pullback_seq",
    "pullback_shots_max",
    "H_fire",
    "ul_ret_since_entry",
    "ul_retrace_from_fav_extreme",
    "ul_ret_30s",
    "ul_ret_60s",
    "ul_ret_120s",
    "prem_vel_10s",
    "prem_vel_30s",
    "iv_change_since_entry",
    "tick_count_since_entry",
    "tick_count_since_peak",
    "call_chain_volume_delta_since_entry",
    "put_chain_volume_delta_since_entry",
    "window_lt_5m",
)

# Never densify these into PO / Path SS reject snapshots (Sep 18 mining cut).
FEATURE_KEYS_SNAPSHOT_DROPPED: Tuple[str, ...] = (
    "entry_extension_from_break_pct",
    "fallback_price_contracts",
    "total_contracts",
    "underlying_move_pct",
    "underlying_vs_entry_pct",
    "entry_bar_lower_wick_pct",
    "entry_bar_upper_wick_pct",
    "entry_bar_body_pct",
    "prior_bar_body_pct",
    "prior_bar_close_position",
    "bars_since_break",
    "recent_lower_low_count",
    "retracement_from_post_break_high",
    "continuation_pct",
    "follow_through_bars",
    "max_extension_since_break",
    "volume_sma",
)

# --- Shared underlying core (proven value across paths) ---
FEATURE_KEYS_CORE_UNDERLYING: Tuple[str, ...] = (
    # Asset-stop research: live vs entry equity (aliases kept in identity too)
    "underlying_price",
    "entry_underlying",
    "entry_underlying_price",
    "exit_underlying",
    "underlying_price_missing",
    "underlying_price_source",
    "underlying_quote_age_sec",
    "underlying_stamped_at",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "sma_20",
    "sma_50",
    "ema_12",
    "ema_26",
    "rsi",
    "macd",
    "macd_signal",
    "macd_histogram",
    "prior_macd_histogram",
    "macd_above_zero",
    "macd_cross",
    "macd_hold_ok_call",
    "macd_exit_signal_call",
    "macd_hist_sign",
    "momentum_10",
    "atr",
    "bollinger_upper",
    "bollinger_middle",
    "bollinger_lower",
    "bollinger_width",
    "bollinger_position",
    "volume_ratio",
    "orb_volume_ratio",
    "volume_ratio_missing",
    "volume_ratio_source",
    "tech_source",
    "session_bar_count",
    "vwap",
    "vwap_distance_pct",
    "vwap_source",
    "vwap_missing",
    "rsi_missing",
    "macd_missing",
    "rs_vs_spy",
    "orb_high",
    "orb_low",
    "orb_range_pct",
    "spy_price",
    "spy_change_pct",
    "rank",
    "priority_score",
    "confidence",
)

# MFE / MAE — peak retained; unused placeholder MAE fields removed (v3)
FEATURE_KEYS_MFE_MAE: Tuple[str, ...] = (
    "peak_price",
    "peak_pct",
)

# Exit observation stamps (Priority Optimizer — DELL-class decision vs fill split)
FEATURE_KEYS_EXIT_OBSERVATION: Tuple[str, ...] = (
    "armed_at",
    "hwm_timestamp",
    "hwm_bid",
    "hwm_pnl_pct",
    "giveback_floor_bid",
    "exit_decision_bid",
    "exit_fill_bid",
    "decision_to_fill_slippage",
    "gap_guard_state",
    "quote_continuity_status",
)

# Exit-only fields — excluded from entry snapshots
ENTRY_SNAPSHOT_EXCLUDED: Tuple[str, ...] = (
    "exit_price",
    "exit_time",
    "exit_reason",
    "win",
    "holding_minutes",
    "pnl_dollars",
    "pnl_pct",
) + FEATURE_KEYS_EXIT_OBSERVATION

# ORB SO ETF-specific (retained for DEMO SO path) + SO-only prior-day / ORB bar fields
FEATURE_KEYS_ORB_SO_SPECIFIC: Tuple[str, ...] = (
    "entry_price",
    "shares",
    "position_value",
    "exec_volume_ratio",
    "current_stop_loss",
    "stop_loss_distance_pct",
    "opening_bar_protection_active",
    "trailing_activated",
    "trailing_distance_pct",
    "breakeven_activated",
    "gap_risk_pct",
    "volume_color",
    # Prior-day / ORB open-bar fields used on SO ETF path only (not 0DTE densify)
    "orb_open",
    "orb_close",
    "orb_volume",
    "prev_day_high",
    "prev_day_low",
    "execution_price_for_prev_day_check",
    "price_vs_prev_day_high",
    "price_vs_prev_day_low",
    "price_vs_prev_day_range",
    "trade_direction",
    "prev_day_entry_gate_passed",
    "category",
    "volatility",
)

# Options chain / microstructure (E*TRADE chain + viability diag)
FEATURE_KEYS_OPTIONS_MICROSTRUCTURE: Tuple[str, ...] = (
    "viability_score",
    "avg_bid_ask_spread_pct",
    "representative_spread_pct",
    # Viability or selected-contract delta. Not the Moon near-spot pair
    # representative_call_delta / representative_put_delta.
    "representative_delta",
    "representative_open_interest",
    "representative_volume",
    "representative_bid",
    "representative_ask",
    "representative_strike",
    "strike",
    "representative_price",
    "representative_price_source",
    "total_chain_volume",
    "total_chain_open_interest",
    "valid_bid_ask_contracts",
    "spread_candidate_count",
    "itm_contracts",
    "chain_fetch_source",
    "liquidity_class",
    "implied_volatility",
    "gamma",
    "theta",
    "vega",
    "put_call_volume_ratio",
    "put_call_oi_ratio",
    "call_chain_volume",
    "put_chain_volume",
    # Desk aliases of chain volumes (densify must not leave these null when chain_* exist)
    "call_volume",
    "put_volume",
    "call_chain_open_interest",
    "put_chain_open_interest",
    "direction_volume_pct",
    "delta",
    "open_interest",
    "chain_sanity_best_same_side_spread_pct",
    "chain_sanity_best_same_side_volume",
    "chain_sanity_best_same_side_oi",
    "atm_near_bid_ask",
    "selector_reject_summary",
    # Desk alias of implied_volatility (profit_optimizer path ticks use `iv`)
    "iv",
    # Exit/entry owned-contract stamp (Hold research vs ATM viability proxy)
    "po_options_owned_strike",
    # Exact-contract quote cumulative (monitor / exit research)
    "cumulative_volume",
    # Hold dir% stamp (exit research; null on entry OK)
    "hold_exit_dir_pct",
)

# ORB1 Breakout pipeline / funnel (first-class)
# Note: live equity is `underlying_price` (+ `current_price`/`close` aliases).
# Entry freeze is `entry_underlying` / `entry_underlying_price`.
FEATURE_KEYS_ORB_0DTE_PIPELINE: Tuple[str, ...] = (
    "direction",
    "option_type",
    "option_type_label",
    "eligibility_score",
    "convex_score",
    "convex_eligible",
    "convex_top_reason",
    "convex_all_reasons_short",
    "pipeline_decision_stage",
    "pipeline_decision_reason",
    "hard_gate_passed",
    "hard_gate_reason",
    "quality_gate_report",
    "strategy_type",
    "momentum_score",
    "target_delta",
    "spread_width",
    "spread_type",
    "priority_rank",
    # Contract / strike economics (Layer D — must survive densify)
    "otm_pct",
    "chosen_delta",
    "fill_delta",
    "otm_research_exception",
)

# Trendline structure (first-class)
FEATURE_KEYS_TRENDLINE_STRUCTURE: Tuple[str, ...] = (
    "setup_type",
    "expected_option_side",
    "trigger_direction",
    "break_distance_pct",
    "orb_zone",
    "velocity_pct",
    "body_ratio_vs_avg",
    "score",
    "quality_score",
    "line_geometry",
    "structure_fit_mse",
    "vwap_position",
    "minutes_from_730_to_break",
    "break_archetype",
    "line_price_at_break",
    "close_at_break",
    "pressure_score",
    "entry_mode",
    "candidate_state",
    "trendline_structure_source",
    "structure_selection_reason",
    "trendline_slot",
    "a1_policy",
    "trade_id",
    "break_snapshot_joined",
    "broker_success",
    "broker_error",
    "reject_reason",
)

# Observe-only trajectory block (Aug 8, 2026).
# L1 / participation / candle / ATR geometry / chain deltas from existing polls.
# NOT aggressor order-flow or L2 depth. Do not hard-gate until multi-session PO proof.
# Note: underlying_quote_age_sec already lives in FEATURE_KEYS_CORE_UNDERLYING.
# Monitor-only cadence keys (quote_age_ms, evaluated, …) stay on path ticks — not densify.
FEATURE_KEYS_TRAJECTORY_OBSERVE: Tuple[str, ...] = (
    # Underlying L1 (top-of-book)
    "underlying_bid_size",
    "underlying_ask_size",
    "underlying_l1_imbalance",
    "underlying_l1_imbalance_delta_5s",
    "underlying_l1_imbalance_delta_15s",
    "underlying_spread_pct",
    "underlying_quote_stale",
    # Side-signed Path SS / QG aliases (densify must keep when raw L1/VWAP exist)
    "l1_delta_5s_with",
    "vwap_with",
    "wick_against",
    "close_with",
    # Owned option L1
    "option_bid_size",
    "option_ask_size",
    "option_l1_imbalance",
    # Participation proxies (not order flow)
    "session_volume",
    "volume_delta_1m",
    "session_vs_avg_volume",
    "volume_1m",
    "volume_3m",
    "volume_5m",
    "volume_rate_1m",
    "volume_rate_3m",
    "volume_rate_5m",
    "volume_acceleration_1m_vs_5m",
    "volume_vs_prior_1m",
    "volume_vs_prior_5m",
    "volume_pct_of_day_5m",
    "relative_volume_time_adjusted",
    "relative_volume_baseline",
    # Price × volume response
    "price_change_1m_pct",
    "price_change_3m_pct",
    "price_change_5m_pct",
    "price_volume_efficiency_1m",
    "price_volume_efficiency_3m",
    "price_volume_efficiency_5m",
    # Candle structure around entry (body/wick stay on qg_admit for Path SS)
    "entry_bar_range_pct",
    "entry_bar_close_position",
    "consecutive_directional_bars",
    "recent_higher_high_count",
    # ATR-normalized / break quality
    "break_distance_atr",
    "break_distance_orb_ratio",
    "distance_from_vwap_atr",
    "distance_from_orb_edge_atr",
    "seconds_since_break",
    # Chain trajectory (existing hydrates only — null when history thin)
    "call_chain_volume_delta_5m",
    "put_chain_volume_delta_5m",
    "same_side_chain_volume_delta_5m",
    "opposite_side_chain_volume_delta_5m",
    "dir_pct_change_5m",
    "owned_contract_volume_delta_5m",
    "owned_contract_oi_change",
    "same_side_chain_volume",
    "same_side_chain_volume_5m_ago",
    "direction_volume_pct_5m_ago",
)

# Identity fields present on all path schemas
FEATURE_KEYS_IDENTITY: Tuple[str, ...] = (
    "symbol",
    "side",
    "option_side",
    "entry_premium",
    "current_price",
    # underlying_price / entry_underlying live in FEATURE_KEYS_CORE_UNDERLYING
    # (avoid duplicate keys in unified densify length checks).
)

FEATURE_KEYS_ORB_SO: Tuple[str, ...] = (
    FEATURE_KEYS_IDENTITY
    + FEATURE_KEYS_CORE_UNDERLYING
    + FEATURE_KEYS_MFE_MAE
    + FEATURE_KEYS_ORB_SO_SPECIFIC
)

# Path-signal names that were stamped in memory and then dropped by densify.
# The other path-signal fields (volume_vs_prior_1m, session_vs_avg_volume,
# open_interest, chain open interest, bollinger_width) are already in the
# blocks above.
# representative_call_delta / representative_put_delta are the near-spot chain
# values Moon evaluated. They are not aliases of representative_delta.
FEATURE_KEYS_PATH_SIGNAL: Tuple[str, ...] = (
    "underlying_volume_1m",
    "break_level",
    "break_timestamp",
    "representative_call_delta",
    "representative_put_delta",
)

# Unified 0DTE options path — ORB @ 7:30 and Trendline break/emit/execute share one schema.
# Block tuples above stay in implementation order. The persisted column order is
# SNAPSHOT_SCHEMA_ORDER. order_keys() is the research ranking only.
_FEATURE_KEYS_0DTE_MEMBERS: Tuple[str, ...] = (
    FEATURE_KEYS_IDENTITY
    + FEATURE_KEYS_CORE_UNDERLYING
    + FEATURE_KEYS_MFE_MAE
    + FEATURE_KEYS_OPTIONS_MICROSTRUCTURE
    + FEATURE_KEYS_ORB_0DTE_PIPELINE
    + FEATURE_KEYS_TRENDLINE_STRUCTURE
    + FEATURE_KEYS_TRAJECTORY_OBSERVE
    + FEATURE_KEYS_PREMIUM_TRAJECTORY_V5
    + FEATURE_KEYS_PATH_SIGNAL
)
_member_set = set(_FEATURE_KEYS_0DTE_MEMBERS)
_snapshot_set = set(SNAPSHOT_SCHEMA_ORDER)
if _member_set != _snapshot_set or len(SNAPSHOT_SCHEMA_ORDER) != len(_snapshot_set):
    raise RuntimeError(
        "SNAPSHOT_SCHEMA_ORDER must be the same 267 keys as the unified blocks, once each"
    )
FEATURE_KEYS_0DTE_UNIFIED: Tuple[str, ...] = SNAPSHOT_SCHEMA_ORDER

# Critical keys for entry/exit timing review (Calls & Puts). Must be populated on
# ORB/Trendline execution and exit snapshots when data is available.
FEATURE_KEYS_ENTRY_EXIT_CRITICAL: Tuple[str, ...] = order_keys((
    "symbol",
    "side",
    "current_price",
    "underlying_price",
    "entry_underlying",
    "entry_underlying_price",
    "exit_underlying",
    "underlying_price_missing",
    "trade_id",
    # Momentum / technicals
    "rsi",
    "sma_20",
    "sma_50",
    "ema_12",
    "ema_26",
    "macd",
    "macd_signal",
    "macd_histogram",
    "prior_macd_histogram",
    "macd_above_zero",
    "macd_cross",
    "macd_hold_ok_call",
    "macd_exit_signal_call",
    "macd_hist_sign",
    "momentum_10",
    "atr",
    "vwap",
    "vwap_distance_pct",
    "volume_ratio",
    "orb_volume_ratio",
    "volume_ratio_missing",
    "volume_ratio_source",
    "tech_source",
    "session_bar_count",
    "vwap_source",
    "vwap_missing",
    "rsi_missing",
    "macd_missing",
    "rs_vs_spy",
    # ORB structure
    "orb_high",
    "orb_low",
    "orb_range_pct",
    # Options chain / OI-PC (call + put)
    "put_call_oi_ratio",
    "put_call_volume_ratio",
    "call_chain_open_interest",
    "put_chain_open_interest",
    "call_chain_volume",
    "put_chain_volume",
    "call_volume",
    "put_volume",
    "total_chain_open_interest",
    "total_chain_volume",
    "direction_volume_pct",
    "representative_volume",
    "cumulative_volume",
    "delta",
    "open_interest",
    "viability_score",
    "representative_bid",
    "representative_ask",
    "representative_spread_pct",
    "representative_delta",
    "representative_open_interest",
    "liquidity_class",
    "implied_volatility",
    # Timing / structure
    "velocity_pct",
    "break_distance_pct",
    "setup_type",
    "score",
    "trigger_direction",
    "priority_rank",
    "hard_gate_passed",
    "quality_gate_report",
    "otm_pct",
    "chosen_delta",
    "fill_delta",
    "quality_score",
    "iv",
    "trendline_slot",
    # Hold research (exit snaps; null on entry is OK)
    "hold_exit_dir_pct",
    # Observe-only trajectory (desk matrices; not live hard gates)
    "underlying_l1_imbalance",
    "option_l1_imbalance",
    "session_volume",
    "volume_5m",
    "volume_acceleration_1m_vs_5m",
    "relative_volume_time_adjusted",
    "price_volume_efficiency_5m",
    "entry_bar_close_position",
    "break_distance_atr",
    "dir_pct_change_5m",
    "owned_contract_volume_delta_5m",
    "put_chain_volume_delta_5m",
    "call_chain_volume_delta_5m",
    "same_side_chain_volume_delta_5m",
    "opposite_side_chain_volume_delta_5m",
    "same_side_chain_volume",
    "same_side_chain_volume_5m_ago",
    "direction_volume_pct_5m_ago",
    "l1_delta_5s_with",
    "vwap_with",
    "wick_against",
    "close_with",
    # Path-signal stamps (entry, exit, and 20p). Null when the source is absent.
    "underlying_volume_1m",
    "volume_vs_prior_1m",
    "session_vs_avg_volume",
    "bollinger_width",
    "break_level",
    "break_timestamp",
))

# Backward-compatible aliases (same key list for both strategies).
FEATURE_KEYS_ORB_0DTE: Tuple[str, ...] = FEATURE_KEYS_0DTE_UNIFIED
FEATURE_KEYS_TRENDLINE_0DTE: Tuple[str, ...] = FEATURE_KEYS_0DTE_UNIFIED

# Legacy alias — tests and retrieve scripts may reference the old name
FEATURE_KEYS_89: List[str] = list(FEATURE_KEYS_ORB_SO)

SNAPSHOT_STRATEGY_SCHEMA: Dict[str, Tuple[str, ...]] = {
    "easy_orb_etf_so": FEATURE_KEYS_ORB_SO,
    "easy_orb_0dte": FEATURE_KEYS_0DTE_UNIFIED,
    "easy_trendline_0dte": FEATURE_KEYS_0DTE_UNIFIED,
    "easy_breakout_0dte": FEATURE_KEYS_0DTE_UNIFIED,
}


def schema_for_strategy(snapshot_strategy: str) -> Tuple[str, ...]:
    return SNAPSHOT_STRATEGY_SCHEMA.get(str(snapshot_strategy or ""), FEATURE_KEYS_ORB_SO)


def is_entry_excluded_field(key: str) -> bool:
    return key in ENTRY_SNAPSHOT_EXCLUDED


def is_snapshot_dropped_field(key: str) -> bool:
    return key in FEATURE_KEYS_SNAPSHOT_DROPPED
