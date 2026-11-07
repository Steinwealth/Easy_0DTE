"""
Shared 0DTE pipeline priority-field helpers for PO execution snapshots.

Used by orb_shared_730_pipeline (merge before save) and execution_feature_snapshot (export).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional


def _is_missing_tech_value(val: Any, field: str) -> bool:
    """Treat placeholder zeros as missing for Priority Optimizer ranking fields."""
    if val is None or val == "":
        return True
    if field in ("rsi", "volume_ratio", "orb_volume_ratio", "vwap_distance_pct", "rs_vs_spy"):
        try:
            return float(val) == 0.0
        except (TypeError, ValueError):
            return True
    return False


_ORB_0DTE_SOURCE_MERGE_FIELDS = (
    "price",
    "current_price",
    "confidence",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "rsi",
    "macd_histogram",
    "volume_ratio",
    "orb_volume_ratio",
    "rs_vs_spy",
    "vwap_distance_pct",
    "vwap",
    "orb_high",
    "orb_low",
    "orb_range",
    "orb_range_pct",
    "orb_data",
    "metadata",
    "momentum_10",
    "momentum",
    "quality_gate_report",
    "bollinger_upper",
    "bollinger_middle",
    "bollinger_lower",
    "bollinger_width",
    "bollinger_position",
    "call_chain_volume",
    "put_chain_volume",
    "call_volume",
    "put_volume",
    "put_call_oi_ratio",
    "direction_volume_pct",
    "otm_pct",
    "representative_delta",
    "representative_price",
    "representative_spread_pct",
)


def build_symbol_side_signal_lookup(signals: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Index hydrated SO/0DTE rows by ``SYMBOL_SIDE`` for pipeline snapshot joins."""
    out: Dict[str, Dict[str, Any]] = {}
    for sig in signals or []:
        if not isinstance(sig, dict):
            continue
        sym = (sig.get("symbol") or "").strip().upper()
        if not sym:
            continue
        side = str(sig.get("side") or sig.get("direction") or "LONG").upper()
        out[f"{sym}_{side}"] = sig
    return out


def normalize_0dte_pipeline_row_orb_fields(row: Dict[str, Any]) -> Dict[str, Any]:
    """Promote nested ``orb_data`` onto flat ORB fields used by PO exports."""
    out = dict(row)
    od = out.get("orb_data")
    if isinstance(od, dict):
        for key in ("orb_high", "orb_low", "orb_range"):
            if out.get(key) is None and od.get(key) is not None:
                out[key] = od.get(key)
        if out.get("orb_range_pct") is None and od.get("orb_range_pct") is not None:
            out["orb_range_pct"] = od.get("orb_range_pct")
    return out


def merge_priority_fields_into_0dte_pipeline_row(
    row: Dict[str, Any],
    source: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Copy SO-ranked technical fields onto 0DTE pipeline rows before snapshot export."""
    if not source:
        return normalize_0dte_pipeline_row_orb_fields(row)
    out = dict(row)
    for field in _ORB_0DTE_SOURCE_MERGE_FIELDS:
        src_val = source.get(field)
        if src_val in (None, ""):
            continue
        dst_val = out.get(field)
        if field == "quality_gate_report":
            if not isinstance(dst_val, dict) and isinstance(src_val, dict):
                out[field] = src_val
            continue
        if field in ("rsi", "vwap_distance_pct", "volume_ratio", "orb_volume_ratio", "rs_vs_spy"):
            if _is_missing_tech_value(dst_val, field) and not _is_missing_tech_value(src_val, field):
                out[field] = src_val
        elif field == "macd_histogram":
            if dst_val is None and src_val is not None:
                out[field] = src_val
        elif field == "orb_data":
            if not out.get("orb_data") and src_val:
                out[field] = src_val
        elif dst_val in (None, "", 0) and src_val not in (None, ""):
            out[field] = src_val
    return normalize_0dte_pipeline_row_orb_fields(out)
