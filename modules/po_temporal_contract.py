"""Temporal contract for unified Priority Optimizer keys.

Each densified key has one class. Entry writers may copy a later observation
into a key only when that key is fill identity. Market, path, and decision
keys on an entry file must stay at the handoff freeze. 20p and exit restamp
MOMENT_RESTAMP and PATH_HISTORY_ASOF_MOMENT. They carry the other classes.

``ENTRY_LOCK_KEYS`` is what ``apply_gate_tech_lock`` restores after post-fill
enrichment. It is the decision, moment, and path-history classes. Fill
identity and exit outcomes are not locked, so a broker result can attach
without replacing the frozen research state.
"""

from __future__ import annotations

from typing import Dict, Iterable, Tuple

from .po_feature_schema import (
    ENTRY_SNAPSHOT_EXCLUDED,
    FEATURE_KEYS_0DTE_UNIFIED,
    FEATURE_KEYS_PATH_SIGNAL,
    FEATURE_KEYS_PREMIUM_TRAJECTORY_V5,
)

ENTRY_DECISION_FROZEN = "ENTRY_DECISION_FROZEN"
ENTRY_FILL_IDENTITY = "ENTRY_FILL_IDENTITY"
MOMENT_RESTAMP = "MOMENT_RESTAMP"
PATH_HISTORY_ASOF_MOMENT = "PATH_HISTORY_ASOF_MOMENT"
EXIT_OUTCOME = "EXIT_OUTCOME"
IMMUTABLE_IDENTITY = "IMMUTABLE_IDENTITY"

# Labels and path identity. Same value on entry, 20p, and exit.
_IMMUTABLE: Tuple[str, ...] = (
    "symbol",
    "side",
    "option_side",
    "direction",
    "option_type",
    "option_type_label",
    "expected_option_side",
    "strategy_type",
    "setup_type",
    "trigger_direction",
    "trendline_slot",
    "a1_policy",
    "line_geometry",
    "entry_mode",
    "candidate_state",
    "trendline_structure_source",
    "structure_selection_reason",
    "break_archetype",
    "orb_zone",
    "break_snapshot_joined",
)

# Attached at fill or carried as the entry economic identity.
# chosen_delta / fill_delta are the contract that was ordered, not the
# near-spot moon pair and not representative_delta.
_FILL: Tuple[str, ...] = (
    "trade_id",
    "entry_premium",
    "entry_underlying",
    "entry_underlying_price",
    "fill_delta",
    "chosen_delta",
    "strike",
    "po_options_owned_strike",
    "otm_research_exception",
    "broker_success",
    "broker_error",
    "reject_reason",
    "target_delta",
)

# Known at the admission decision and carried unchanged.
# Moon deltas are the near-spot chain Moon read. They are not restamped
# from a later owned contract.
_DECISION: Tuple[str, ...] = (
    "representative_call_delta",
    "representative_put_delta",
    "representative_call_gamma",
    "representative_put_gamma",
    "break_level",
    "break_timestamp",
    "break_distance_pct",
    "break_distance_atr",
    "break_distance_orb_ratio",
    "line_price_at_break",
    "close_at_break",
    "minutes_from_730_to_break",
    "orb_high",
    "orb_low",
    "orb_range_pct",
    "otm_pct",
    "priority_score",
    "priority_rank",
    "confidence",
    "rank",
    "quality_score",
    "score",
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
    "momentum_score",
    "viability_score",
    "pressure_score",
    "body_ratio_vs_avg",
    "structure_fit_mse",
    "velocity_pct",
    "spread_width",
    "spread_type",
    "selector_reject_summary",
    "liquidity_class",
    "chain_fetch_source",
)

# Point-in-time market state. Entry uses the decision freeze. 20p and exit recompute.
_MOMENT: Tuple[str, ...] = (
    "current_price",
    "underlying_price",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "underlying_price_missing",
    "underlying_price_source",
    "underlying_quote_age_sec",
    "underlying_stamped_at",
    "sma_20",
    "sma_50",
    "ema_12",
    "ema_26",
    "rsi",
    "rsi_14",
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
    "vwap_position",
    "vwap_source",
    "vwap_missing",
    "rsi_missing",
    "macd_missing",
    "rs_vs_spy",
    "spy_price",
    "spy_change_pct",
    "distance_from_vwap_atr",
    "distance_from_orb_edge_atr",
    "implied_volatility",
    "iv",
    "gamma",
    "theta",
    "vega",
    "delta",
    "representative_delta",
    "representative_open_interest",
    "representative_volume",
    "representative_bid",
    "representative_ask",
    "representative_spread_pct",
    "representative_strike",
    "representative_price",
    "representative_price_source",
    "avg_bid_ask_spread_pct",
    "total_chain_volume",
    "total_chain_open_interest",
    "valid_bid_ask_contracts",
    "spread_candidate_count",
    "itm_contracts",
    "put_call_volume_ratio",
    "put_call_oi_ratio",
    "call_chain_volume",
    "put_chain_volume",
    "call_volume",
    "put_volume",
    "call_chain_open_interest",
    "put_chain_open_interest",
    "direction_volume_pct",
    "open_interest",
    "chain_sanity_best_same_side_spread_pct",
    "chain_sanity_best_same_side_volume",
    "chain_sanity_best_same_side_oi",
    "atm_near_bid_ask",
    "cumulative_volume",
    "underlying_bid_size",
    "underlying_ask_size",
    "underlying_l1_imbalance",
    "underlying_spread_pct",
    "underlying_quote_stale",
    "option_bid_size",
    "option_ask_size",
    "option_l1_imbalance",
    "l1_delta_5s_with",
    "vwap_with",
    "wick_against",
    "close_with",
    "entry_bar_range_pct",
    "entry_bar_close_position",
    "consecutive_directional_bars",
    "recent_higher_high_count",
    "underlying_volume_1m",
)

# Windows and path summaries that must end at the snapshot clock.
_PATH: Tuple[str, ...] = (
    "underlying_l1_imbalance_delta_5s",
    "underlying_l1_imbalance_delta_15s",
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
    "price_change_1m_pct",
    "price_change_3m_pct",
    "price_change_5m_pct",
    "price_volume_efficiency_1m",
    "price_volume_efficiency_3m",
    "price_volume_efficiency_5m",
    "seconds_since_break",
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
    "peak_price",
    "peak_pct",
) + tuple(FEATURE_KEYS_PREMIUM_TRAJECTORY_V5)

# Exit economics and exit-decision telemetry. Not an entry research feature.
_EXIT: Tuple[str, ...] = tuple(ENTRY_SNAPSHOT_EXCLUDED) + (
    "exit_underlying",
    "hold_exit_dir_pct",
)

_CLASS_MEMBERS: Dict[str, Tuple[str, ...]] = {
    IMMUTABLE_IDENTITY: _IMMUTABLE,
    ENTRY_FILL_IDENTITY: _FILL,
    ENTRY_DECISION_FROZEN: _DECISION,
    MOMENT_RESTAMP: _MOMENT,
    PATH_HISTORY_ASOF_MOMENT: _PATH,
    EXIT_OUTCOME: _EXIT,
}


def _index() -> Dict[str, str]:
    out: Dict[str, str] = {}
    for cls, keys in _CLASS_MEMBERS.items():
        for key in keys:
            if key in out and out[key] != cls:
                raise RuntimeError(f"PO temporal class overlap on {key}: {out[key]} and {cls}")
            out[key] = cls
    return out


KEY_CLASS: Dict[str, str] = _index()

# Restored onto options_executed from the handoff cache. Fill identity and
# exit outcomes stay out so broker economics can attach.
# Oct 7 2026 (A6): the 10.5 Trade Profile also gates the three chain vol/OI
# ratio keys. They are not in the 267-key schema (count unchanged) but must be
# locked from the gate onto the executed row like every other decision key.
TP_RATIO_LOCK_KEYS: Tuple[str, ...] = (
    "total_chain_vol_oi",
    "call_chain_vol_oi",
    "put_chain_vol_oi",
)
# Share and vol/OI keys the Trade Profile scores. They stay off the 267-key
# densify list and ride the sealed row so training can read the 27-key fill.
TP_PROFILE_EXTRA_KEYS: Tuple[str, ...] = TP_RATIO_LOCK_KEYS + (
    "call_share",
    "put_share",
)
ENTRY_LOCK_KEYS: Tuple[str, ...] = tuple(
    key
    for key, cls in KEY_CLASS.items()
    if cls in (ENTRY_DECISION_FROZEN, MOMENT_RESTAMP, PATH_HISTORY_ASOF_MOMENT)
) + tuple(k for k in TP_PROFILE_EXTRA_KEYS if k not in KEY_CLASS)


def temporal_class(key: str) -> str:
    cls = KEY_CLASS.get(key)
    if cls is None:
        raise KeyError(key)
    return cls


def unclassified_unified_keys() -> Tuple[str, ...]:
    return tuple(key for key in FEATURE_KEYS_0DTE_UNIFIED if key not in KEY_CLASS)


def assert_unified_coverage() -> None:
    missing = unclassified_unified_keys()
    if missing:
        raise RuntimeError("PO keys missing a temporal class: " + ", ".join(missing))
    path_signal = set(FEATURE_KEYS_PATH_SIGNAL)
    if not path_signal.issubset(KEY_CLASS):
        raise RuntimeError("path-signal keys missing a temporal class")


assert_unified_coverage()
