"""Compact qg_admit carry-over. Neutral dict helpers, no Priority Optimizer.

Discovery, the quality gate, and the watch marker share this snapshot.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict

QG_ADMIT_KEYS: tuple[str, ...] = (
    "session_vs_avg_volume",
    "session_vs_avg",
    "implied_volatility",
    "iv",
    "representative_iv",
    "seconds_since_break",
    "direction_volume_pct",
    "call_chain_volume",
    "put_chain_volume",
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
    "rs_vs_spy",
    "spy_change_pct",
    "spy_price",
    "vwap_distance_pct",
    "abs_vwap_distance_pct",
    "vwap_position",
    "vwap_with",
    "volume_ratio",
    "rsi",
    "bollinger_position",
    "bollinger_width",
    "l1_delta_5s_with",
    "underlying_l1_imbalance",
    "underlying_l1_imbalance_delta_5s",
    "volume_vs_prior_1m",
    "average_volume",
    "trendline_slot",
    "break_timestamp",
    "close_at_break",
    "material_break_at",
    "break_distance_atr",
    "price_volume_efficiency_1m",
    "price_volume_efficiency_5m",
    "entry_bar_body_pct",
    "entry_bar_upper_wick_pct",
    "entry_bar_lower_wick_pct",
    "entry_bar_close_position",
    "otm_pct",
    "delta",
    "chosen_delta",
    "representative_delta",
    "representative_strike",
    "wick_against",
    "close_with",
    # Oct 7 2026: 10.5 chain vol/OI ratio keys (central_tech.chain_ratios).
    "total_chain_vol_oi",
    "call_chain_vol_oi",
    "put_chain_vol_oi",
    # 9.21 Path SS admit keys (PC-verified knives)
    "atr",
    "avg_bid_ask_spread_pct",
    "break_distance_pct",
    "call_chain_open_interest",
    "chain_sanity_best_same_side_spread_pct",
    "chain_sanity_best_same_side_volume",
    "consecutive_directional_bars",
    "cumulative_volume",
    "distance_from_orb_edge_atr",
    "distance_from_vwap_atr",
    "entry_bar_range_pct",
    "gamma",
    "macd",
    "macd_signal",
    "minutes_from_730_to_break",
    "momentum_10",
    "open_interest",
    "option_ask_size",
    "option_bid_size",
    "option_l1_imbalance",
    "orb_range_pct",
    "orb_volume_ratio",
    "price_change_1m_pct",
    "price_change_3m_pct",
    "price_change_5m_pct",
    "price_volume_efficiency_3m",
    "put_call_volume_ratio",
    "put_chain_open_interest",
    "quality_score",
    "recent_higher_high_count",
    "relative_volume_time_adjusted",
    "representative_bid",
    "representative_ask",
    "representative_spread_pct",
    "session_bar_count",
    "session_volume",
    "spread_candidate_count",
    "theta",
    "total_chain_open_interest",
    "total_chain_volume",
    "underlying_ask_size",
    "underlying_bid_size",
    "underlying_l1_imbalance_delta_15s",
    "underlying_quote_age_sec",
    "underlying_spread_pct",
    "vega",
    "volume_1m",
    "volume_3m",
    "volume_5m",
    "volume_acceleration_1m_vs_5m",
    "volume_pct_of_day_5m",
    "volume_rate_1m",
    "volume_vs_prior_5m",
)

def _qg_admit_json_value(val: Any) -> Any:
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            val = val.replace(tzinfo=timezone.utc)
        return val.isoformat()
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        try:
            f = float(val)
        except (TypeError, ValueError):
            return None
        if f != f:  # NaN
            return None
        return f
    if isinstance(val, str):
        return val
    return None


def collect_qg_admit_snapshot(row: Dict[str, Any]) -> Dict[str, Any]:
    """Compact JSON-safe QG/SS keys for watch-FSM GCS. No bars, no L1 ring."""
    if not isinstance(row, dict):
        return {}
    promote_qg_admit_fields(row)
    out: Dict[str, Any] = {}
    for key in QG_ADMIT_KEYS:
        v = _qg_admit_json_value(row.get(key))
        if v is not None:
            out[key] = v
    return out


def apply_qg_admit_snapshot(target: Dict[str, Any], snap: Any, *, overwrite: bool = False) -> int:
    """Copy a persisted QG snapshot onto a row / metadata dict. No I/O."""
    if not isinstance(target, dict) or not isinstance(snap, dict):
        return 0
    n = 0
    for key in QG_ADMIT_KEYS:
        if not overwrite and target.get(key) is not None:
            continue
        v = snap.get(key)
        if v is None or v == "":
            continue
        target[key] = v
        n += 1
    if n:
        target["qg_admit"] = dict(snap)
    return n


def promote_qg_admit_fields(row: Dict[str, Any]) -> Dict[str, Any]:
    """Lift nested metadata QG keys onto the row used by GateContext. No I/O."""
    if not isinstance(row, dict):
        return {}
    md = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    setup = md.get("setup_payload") if isinstance(md.get("setup_payload"), dict) else {}
    if not isinstance(setup, dict):
        setup = row.get("setup_payload") if isinstance(row.get("setup_payload"), dict) else {}
    nested = md.get("qg_admit") if isinstance(md.get("qg_admit"), dict) else {}
    if not nested:
        nested = setup.get("qg_admit") if isinstance(setup.get("qg_admit"), dict) else {}
    if not nested:
        nested = row.get("qg_admit") if isinstance(row.get("qg_admit"), dict) else {}
    apply_qg_admit_snapshot(row, nested, overwrite=False)
    for key in QG_ADMIT_KEYS:
        if row.get(key) is not None:
            continue
        if md.get(key) is not None:
            row[key] = md[key]
        elif setup.get(key) is not None:
            row[key] = setup[key]
    if row.get("trendline_slot") is None:
        slot = md.get("trendline_slot") or setup.get("trendline_slot")
        if slot is not None:
            row["trendline_slot"] = slot
    if row.get("seconds_since_break") is None:
        sec = md.get("seconds_from_material_break")
        if sec is None:
            sec = setup.get("seconds_from_material_break")
        if sec is not None:
            row["seconds_since_break"] = sec
    return row

