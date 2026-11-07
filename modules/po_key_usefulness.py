"""Two orders for the same 267 Priority Optimizer keys.

SNAPSHOT_SCHEMA_ORDER is the stable physical order of a dense snapshot.
It is grouped by what the field means so a row can be read: identity, then
the active research families, then chain, contract, session tech, volume,
break geometry, L1, candle path, pipeline metadata, fill identity, the
premium path by clock, compatibility aliases, and outcome fields.

USEFULNESS_ORDER is the research ranking. It may change when Entry, Skip,
or Moon evidence changes. That ranking does not reorder the snapshot.

Provenance clocks (decision freeze, chain as-of, training eligibility, and
the rest) are snapshot envelope fields. They are not members of these 267.

Gate evaluation order is neither list.
"""

from __future__ import annotations

from typing import Iterable, Tuple

# Compatibility names. They stay in the schema. They are not the canonical
# measurement, and they are not the research leaders.
ALIAS_OF = {
    "iv": "implied_volatility",
    "call_volume": "call_chain_volume",
    "put_volume": "put_chain_volume",
    "entry_underlying": "entry_underlying_price",
}

# Same spot when the writer stamps one price onto all three. Still three keys.
SAME_SPOT_KEYS = ("current_price", "close", "underlying_price")

# Near-spot moon deltas, the viability delta, the selected-contract greek,
# and the fill deltas. These are not aliases of each other.
DISTINCT_DELTAS = (
    "representative_call_delta",
    "representative_put_delta",
    "representative_delta",
    "delta",
    "chosen_delta",
    "fill_delta",
)

SNAPSHOT_SCHEMA_ORDER: Tuple[str, ...] = (
    "symbol", "trade_id", "strategy_type", "setup_type",
    "side", "option_side", "option_type", "option_type_label",
    "direction", "trigger_direction", "trendline_slot", "entry_mode",
    "candidate_state", "call_chain_volume", "put_chain_volume", "representative_call_delta",
    "representative_put_delta", "price_volume_efficiency_3m", "price_volume_efficiency_5m", "break_distance_pct",
    "call_chain_volume_delta_5m", "put_chain_volume_delta_5m", "same_side_chain_volume_delta_5m", "same_side_chain_volume_5m_ago",
    "total_chain_open_interest", "call_chain_open_interest", "put_chain_open_interest", "rsi",
    "implied_volatility", "theta", "delta", "gamma",
    "vega", "spy_change_pct", "distance_from_vwap_atr", "vwap_distance_pct",
    "dir_pct_change_5m", "relative_volume_time_adjusted", "quality_score", "atr",
    "underlying_volume_1m", "volume_1m", "break_level", "break_timestamp",
    "total_chain_volume", "same_side_chain_volume", "opposite_side_chain_volume_delta_5m", "owned_contract_volume_delta_5m",
    "owned_contract_oi_change", "direction_volume_pct", "direction_volume_pct_5m_ago", "put_call_oi_ratio",
    "put_call_volume_ratio", "chain_sanity_best_same_side_oi", "chain_sanity_best_same_side_spread_pct", "chain_sanity_best_same_side_volume",
    "spread_candidate_count", "itm_contracts", "chain_fetch_source", "representative_delta",
    "representative_price", "representative_bid", "representative_ask", "representative_volume",
    "representative_open_interest", "representative_spread_pct", "representative_strike", "representative_price_source",
    "open_interest", "otm_pct", "underlying_price", "current_price",
    "open", "high", "low", "close",
    "entry_underlying_price", "entry_underlying", "spy_price", "rs_vs_spy",
    "macd", "macd_signal", "macd_histogram", "prior_macd_histogram",
    "macd_hist_sign", "macd_cross", "macd_above_zero", "macd_hold_ok_call",
    "macd_exit_signal_call", "macd_missing", "bollinger_upper", "bollinger_middle",
    "bollinger_lower", "bollinger_width", "bollinger_position", "vwap",
    "vwap_position", "vwap_with", "vwap_source", "vwap_missing",
    "sma_20", "sma_50", "ema_12", "ema_26",
    "momentum_10", "session_bar_count", "rsi_missing", "tech_source",
    "underlying_price_missing", "underlying_stamped_at", "volume", "volume_3m",
    "volume_5m", "volume_rate_1m", "volume_rate_3m", "volume_rate_5m",
    "volume_delta_1m", "cumulative_volume", "session_volume", "session_vs_avg_volume",
    "relative_volume_baseline", "volume_vs_prior_1m", "volume_vs_prior_5m", "volume_acceleration_1m_vs_5m",
    "volume_pct_of_day_5m", "volume_ratio", "orb_volume_ratio", "volume_ratio_source",
    "volume_ratio_missing", "price_volume_efficiency_1m", "break_distance_atr", "break_distance_orb_ratio",
    "distance_from_orb_edge_atr", "line_price_at_break", "close_at_break", "structure_fit_mse",
    "line_geometry", "seconds_since_break", "minutes_from_730_to_break", "velocity_pct",
    "orb_high", "orb_low", "orb_range_pct", "orb_zone",
    "break_archetype", "break_snapshot_joined", "trendline_structure_source", "structure_selection_reason",
    "underlying_bid_size", "underlying_ask_size", "underlying_l1_imbalance", "underlying_l1_imbalance_delta_5s",
    "underlying_l1_imbalance_delta_15s", "l1_delta_5s_with", "underlying_quote_age_sec", "underlying_quote_stale",
    "underlying_price_source", "underlying_spread_pct", "liquidity_class", "avg_bid_ask_spread_pct",
    "atm_near_bid_ask", "valid_bid_ask_contracts", "price_change_1m_pct", "price_change_3m_pct",
    "price_change_5m_pct", "entry_bar_close_position", "entry_bar_range_pct", "body_ratio_vs_avg",
    "close_with", "wick_against", "consecutive_directional_bars", "recent_higher_high_count",
    "pressure_score", "priority_score", "priority_rank", "rank",
    "score", "confidence", "viability_score", "eligibility_score",
    "momentum_score", "convex_score", "convex_eligible", "convex_top_reason",
    "convex_all_reasons_short", "pipeline_decision_stage", "pipeline_decision_reason", "hard_gate_passed",
    "hard_gate_reason", "quality_gate_report", "selector_reject_summary", "reject_reason",
    "otm_research_exception", "expected_option_side", "a1_policy", "broker_success",
    "broker_error", "strike", "po_options_owned_strike", "chosen_delta",
    "fill_delta", "target_delta", "entry_premium", "spread_width",
    "spread_type", "premium", "option_bid", "option_mid",
    "option_ask", "option_bid_size", "option_ask_size", "option_l1_imbalance",
    "option_spread_pct", "option_spread_to_premium", "premium_30s_ago", "option_bid_30s_ago",
    "option_mid_30s_ago", "option_ask_30s_ago", "underlying_price_30s_ago", "option_bid_size_30s_ago",
    "option_ask_size_30s_ago", "option_l1_imbalance_30s_ago", "option_spread_pct_30s_ago", "option_spread_to_premium_30s_ago",
    "implied_volatility_30s_ago", "delta_30s_ago", "premium_5m_ago", "option_bid_5m_ago",
    "option_mid_5m_ago", "option_ask_5m_ago", "underlying_price_5m_ago", "option_bid_size_5m_ago",
    "option_ask_size_5m_ago", "option_l1_imbalance_5m_ago", "option_spread_pct_5m_ago", "option_spread_to_premium_5m_ago",
    "implied_volatility_5m_ago", "delta_5m_ago", "premium_change_pct_30s", "premium_change_pct_5m",
    "premium_slope_30s", "premium_slope_5m", "premium_slope_r2_30s", "premium_slope_r2_5m",
    "underlying_change_pct_30s", "underlying_change_pct_5m", "premium_ul_elasticity_30s", "premium_ul_elasticity_5m",
    "premium_hwm", "premium_drawdown_from_hwm_pct", "seconds_since_hwm", "premium_lwm",
    "premium_recovery_from_lwm_pct", "seconds_since_lwm", "premium_negative_velocity_streak_sec", "premium_positive_velocity_streak_sec",
    "premium_velocity_sign_flips_5m", "premium_ul_direction_agree", "premium_acceleration_30s_vs_5m", "premium_trajectory_quality",
    "iv", "call_volume", "put_volume", "peak_price",
    "peak_pct", "exit_underlying", "hold_exit_dir_pct",
)

USEFULNESS_ORDER: Tuple[str, ...] = (
    "call_chain_volume", "put_chain_volume", "representative_call_delta", "representative_put_delta",
    "price_volume_efficiency_3m", "price_volume_efficiency_5m", "break_distance_pct", "call_chain_volume_delta_5m",
    "put_chain_volume_delta_5m", "same_side_chain_volume_delta_5m", "same_side_chain_volume_5m_ago", "total_chain_open_interest",
    "call_chain_open_interest", "put_chain_open_interest", "rsi", "implied_volatility",
    "theta", "delta", "gamma", "vega",
    "spy_change_pct", "distance_from_vwap_atr", "vwap_distance_pct", "dir_pct_change_5m",
    "relative_volume_time_adjusted", "quality_score", "atr", "underlying_volume_1m",
    "volume_1m", "break_level", "total_chain_volume", "break_timestamp",
    "symbol", "side", "option_side", "trade_id",
    "current_price", "open_interest", "priority_score", "velocity_pct",
    "confidence", "otm_pct", "session_vs_avg_volume", "macd_signal",
    "put_call_oi_ratio", "chain_sanity_best_same_side_oi", "spy_price", "chain_sanity_best_same_side_spread_pct",
    "viability_score", "macd", "volume_vs_prior_1m", "representative_price",
    "bollinger_width", "cumulative_volume", "underlying_ask_size", "same_side_chain_volume",
    "chain_sanity_best_same_side_volume", "representative_volume", "representative_spread_pct", "spread_candidate_count",
    "volume_rate_5m", "volume_5m", "opposite_side_chain_volume_delta_5m", "volume_rate_1m",
    "volume_3m", "volume_rate_3m", "volume_delta_1m", "session_volume",
    "minutes_from_730_to_break", "avg_bid_ask_spread_pct", "structure_fit_mse", "line_price_at_break",
    "volume", "price_volume_efficiency_1m", "low", "open",
    "high", "orb_high", "underlying_price", "entry_underlying",
    "entry_underlying_price", "orb_low", "close", "close_at_break",
    "bollinger_upper", "bollinger_middle", "bollinger_lower", "vwap",
    "sma_50", "ema_26", "ema_12", "sma_20",
    "underlying_spread_pct", "direction_volume_pct_5m_ago", "owned_contract_volume_delta_5m", "l1_delta_5s_with",
    "liquidity_class", "trendline_structure_source", "structure_selection_reason", "valid_bid_ask_contracts",
    "trendline_slot", "underlying_quote_age_sec", "option_l1_imbalance", "volume_pct_of_day_5m",
    "strategy_type", "setup_type", "underlying_price_source", "orb_zone",
    "break_snapshot_joined", "trigger_direction", "underlying_l1_imbalance_delta_5s", "option_bid_size",
    "macd_histogram", "representative_open_interest", "underlying_l1_imbalance", "volume_vs_prior_5m",
    "prior_macd_histogram", "volume_ratio", "orb_volume_ratio", "underlying_quote_stale",
    "recent_higher_high_count", "vwap_with", "orb_range_pct", "underlying_l1_imbalance_delta_15s",
    "momentum_10", "chain_fetch_source", "option_ask_size", "representative_delta",
    "macd_hist_sign", "rs_vs_spy", "bollinger_position", "put_call_volume_ratio",
    "entry_bar_close_position", "owned_contract_oi_change", "macd_hold_ok_call", "session_bar_count",
    "macd_missing", "direction_volume_pct", "price_change_3m_pct", "representative_bid",
    "price_change_5m_pct", "entry_bar_range_pct", "macd_cross", "vwap_position",
    "distance_from_orb_edge_atr", "consecutive_directional_bars", "rsi_missing", "chosen_delta",
    "fill_delta", "volume_acceleration_1m_vs_5m", "price_change_1m_pct", "volume_ratio_missing",
    "underlying_bid_size", "representative_ask", "macd_above_zero", "line_geometry",
    "break_distance_atr", "close_with", "wick_against", "seconds_since_break",
    "representative_price_source", "break_distance_orb_ratio", "itm_contracts", "atm_near_bid_ask",
    "entry_premium", "exit_underlying", "underlying_price_missing", "underlying_stamped_at",
    "macd_exit_signal_call", "volume_ratio_source", "tech_source", "vwap_source",
    "vwap_missing", "rank", "peak_price", "peak_pct",
    "representative_strike", "strike", "selector_reject_summary", "po_options_owned_strike",
    "hold_exit_dir_pct", "direction", "option_type", "option_type_label",
    "eligibility_score", "convex_score", "convex_eligible", "convex_top_reason",
    "convex_all_reasons_short", "pipeline_decision_stage", "pipeline_decision_reason", "hard_gate_passed",
    "hard_gate_reason", "quality_gate_report", "momentum_score", "target_delta",
    "spread_width", "spread_type", "priority_rank", "otm_research_exception",
    "expected_option_side", "body_ratio_vs_avg", "score", "break_archetype",
    "pressure_score", "entry_mode", "candidate_state", "a1_policy",
    "broker_success", "broker_error", "reject_reason", "relative_volume_baseline",
    "iv", "call_volume", "put_volume", "premium",
    "premium_30s_ago", "premium_5m_ago", "option_bid", "option_bid_30s_ago",
    "option_bid_5m_ago", "option_mid", "option_mid_30s_ago", "option_mid_5m_ago",
    "option_ask", "option_ask_30s_ago", "option_ask_5m_ago", "underlying_price_30s_ago",
    "underlying_price_5m_ago", "option_bid_size_30s_ago", "option_bid_size_5m_ago", "option_ask_size_30s_ago",
    "option_ask_size_5m_ago", "option_l1_imbalance_30s_ago", "option_l1_imbalance_5m_ago", "option_spread_pct",
    "option_spread_pct_30s_ago", "option_spread_pct_5m_ago", "option_spread_to_premium", "option_spread_to_premium_30s_ago",
    "option_spread_to_premium_5m_ago", "implied_volatility_30s_ago", "implied_volatility_5m_ago", "delta_30s_ago",
    "delta_5m_ago", "premium_change_pct_30s", "premium_change_pct_5m", "premium_slope_30s",
    "premium_slope_5m", "premium_slope_r2_30s", "premium_slope_r2_5m", "underlying_change_pct_30s",
    "underlying_change_pct_5m", "premium_ul_elasticity_30s", "premium_ul_elasticity_5m", "premium_hwm",
    "premium_drawdown_from_hwm_pct", "seconds_since_hwm", "premium_lwm", "premium_recovery_from_lwm_pct",
    "seconds_since_lwm", "premium_negative_velocity_streak_sec", "premium_positive_velocity_streak_sec", "premium_velocity_sign_flips_5m",
    "premium_ul_direction_agree", "premium_acceleration_30s_vs_5m", "premium_trajectory_quality",
)


# Stay in the 267-key row. Do not count them in fill-% until a writer exists.
# Outcome keys are empty on entry; pipeline/convex keys are unused on later snaps.
FILL_PCT_SKIP_KEYS = frozenset(
    {
        "broker_error",
        "reject_reason",
        "otm_research_exception",
        "quality_gate_report",
        "selector_reject_summary",
        "convex_score",
        "convex_eligible",
        "convex_top_reason",
        "convex_all_reasons_short",
        "pipeline_decision_stage",
        "pipeline_decision_reason",
        "hard_gate_reason",
        "a1_policy",
        "eligibility_score",
        "momentum_score",
        "pressure_score",
        "spread_type",
        "spread_width",
        "target_delta",
    }
)


# breakout_watch_activated writes cache session-tech + the ORB box. Chain and
# Greek keys are out of scope. Score these, not the 27-key Trade Profile or
# the 267-key row. 6 of the 27 TP keys are in this set; 6/27 ≈ 22%. A 27-key
# watch fill near 31% is session-tech plus an occasional SPY or UL L1 print.
WATCH_ACTIVATION_SCORE_KEYS: Tuple[str, ...] = (
    "orb_high",
    "orb_low",
    "orb_range_pct",
    "rsi",
    "atr",
    "vwap",
    "vwap_distance_pct",
    "distance_from_vwap_atr",
    "volume_ratio",
    "price_volume_efficiency_3m",
    "bollinger_position",
    "bollinger_width",
    "macd",
    "macd_histogram",
)

WATCH_TP27_IN_SCOPE: Tuple[str, ...] = (
    "atr",
    "price_volume_efficiency_3m",
    "distance_from_vwap_atr",
    "rsi",
    "bollinger_position",
    "bollinger_width",
)

# Exit flatten primes remaining opens once. These keys stay blank when that
# shared read is still old or the bar close is after the event.
EXIT_SESSION_TECH_SCORE_KEYS: Tuple[str, ...] = (
    "rsi",
    "macd",
    "macd_signal",
    "macd_histogram",
    "bollinger_upper",
    "bollinger_middle",
    "bollinger_lower",
    "bollinger_width",
    "bollinger_position",
)


def order_keys(keys: Iterable[str]) -> Tuple[str, ...]:
    """Return keys in research usefulness order.

    Unknown names keep their incoming order at the end. This does not define
    the persisted snapshot column order. That is SNAPSHOT_SCHEMA_ORDER.
    """
    rank = {name: i for i, name in enumerate(USEFULNESS_ORDER)}
    seen = list(dict.fromkeys(keys))
    tail = len(USEFULNESS_ORDER)

    def sort_key(name: str) -> tuple:
        if name in rank:
            return (0, rank[name])
        return (1, tail + seen.index(name))

    return tuple(sorted(seen, key=sort_key))
