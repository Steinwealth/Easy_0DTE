"""
Execution-time feature snapshots for ORB and Trendline execution paths (Priority Optimizer).

Rev 00333: Persist per-symbol technical fields + Red Day / enhanced gating context at execution
decision time so offline analysis can relate profitable vs losing days.

Scope: snapshot payloads can represent multiple execution paths (for example ORB SO 7:30 execution
or Trendline 0DTE options execution). `snapshot_strategy` tags the source strategy so downstream
analysis can split cohorts cleanly. `market_context.total_scanned` remains context-specific to the
snapshot producer.

Writes:
- GCS: priority_optimizer/execution_snapshots/{date}_{time}_{stage}.json (when GCS enabled)
- Local: priority_optimizer/execution_snapshots/ (same filename, repo-relative)

Trendline first-break daily file (see trendline_break_snapshot.py):
- GCS/local: priority_optimizer/trendline_break_snapshots/YYYY-MM-DD_breaks.json
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .orb_0dte_priority_fields import normalize_0dte_pipeline_row_orb_fields
from .po_feature_schema import (
    ENTRY_SNAPSHOT_EXCLUDED,
    FEATURE_KEYS_0DTE_UNIFIED,
    FEATURE_KEYS_89,
    FEATURE_KEYS_ORB_0DTE,
    FEATURE_KEYS_ORB_SO,
    FEATURE_KEYS_TRENDLINE_0DTE,
    SCHEMA_VERSION,
    is_entry_excluded_field,
    schema_for_strategy,
)

log = logging.getLogger(__name__)


def _skip_local_snapshot_write() -> bool:
    """Cloud Run: GCS only — avoid filling ephemeral disk."""
    explicit = os.getenv("EXECUTION_SNAPSHOT_LOCAL", "").strip().lower()
    if explicit in ("1", "true", "yes"):
        return False
    if explicit in ("0", "false", "no"):
        return True
    return bool(os.getenv("K_SERVICE"))


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _tech_from_signal_or_metadata(sig: Dict[str, Any], key: str) -> Any:
    """Prefer real indicator values; ignore invent placeholders when missing flags set."""
    md = sig.get("metadata") if isinstance(sig.get("metadata"), dict) else {}
    if key in ("rsi", "rsi_14") and (
        bool(sig.get("rsi_missing")) or bool(md.get("rsi_missing"))
    ):
        return None
    if key == "macd_histogram" and (
        bool(sig.get("macd_missing")) or bool(md.get("macd_missing"))
    ):
        return None
    if key in ("volume_ratio", "orb_volume_ratio") and (
        bool(sig.get("volume_ratio_missing")) or bool(md.get("volume_ratio_missing"))
    ):
        return None
    val = sig.get(key)
    if key in ("rsi", "vwap_distance_pct", "volume_ratio", "orb_volume_ratio", "rs_vs_spy", "spy_change_pct"):
        try:
            if val is not None and float(val) != 0.0:
                return val
        except (TypeError, ValueError):
            pass
        md_val = md.get(key)
        if md_val is not None:
            try:
                if float(md_val) != 0.0:
                    return md_val
            except (TypeError, ValueError):
                return md_val
        # Invent / missing sentinel zeros must densify as null (not a real fill).
        return None
    if key == "macd_histogram":
        if val is not None:
            return val
        return md.get(key)
    return val


def _sanitize_invent_tech_zeros(out: Dict[str, Any], sig: Dict[str, Any]) -> None:
    """Null invent sentinel zeros that would otherwise densify as fake fills."""
    for key in ("rs_vs_spy", "spy_change_pct", "rsi", "vwap_distance_pct", "volume_ratio", "orb_volume_ratio"):
        if key not in out and key not in (sig or {}):
            continue
        cleaned = _tech_from_signal_or_metadata(sig if isinstance(sig, dict) else out, key)
        if key in out:
            out[key] = cleaned
        # Also force from raw if still invent zero.
        try:
            if out.get(key) is not None and float(out.get(key)) == 0.0 and key in (
                "rs_vs_spy",
                "spy_change_pct",
            ):
                out[key] = None
        except (TypeError, ValueError):
            pass
    # Owned strike backfill for ORB execute rows.
    if out.get("po_options_owned_strike") in (None, ""):
        for sk in (
            "strike",
            "representative_strike",
            "matched_strike",
            "requested_strike",
            "listed_exchange_strike",
        ):
            sv = out.get(sk)
            if sv in (None, ""):
                sv = sig.get(sk) if isinstance(sig, dict) else None
            if sv in (None, ""):
                continue
            try:
                sf = float(sv)
            except (TypeError, ValueError):
                continue
            if sf > 0:
                out["po_options_owned_strike"] = sf
                break
    if bool(out.get("macd_missing")) or bool(
        (sig.get("macd_missing") if isinstance(sig, dict) else False)
    ):
        out["macd_histogram"] = None
        out["prior_macd_histogram"] = None
        if out.get("macd_signal") is None:
            out["macd"] = None
            out["macd_signal"] = None
        out["macd_missing"] = True


def _first_tech_value(sig: Dict[str, Any], *keys: str) -> Any:
    for key in keys:
        val = _tech_from_signal_or_metadata(sig, key)
        if val is not None:
            return val
    return None


def _tech_flag_from_signal_or_metadata(sig: Dict[str, Any], key: str) -> Optional[bool]:
    md = sig.get("metadata") if isinstance(sig.get("metadata"), dict) else {}
    if key in sig:
        return bool(sig.get(key))
    if key in md:
        return bool(md.get(key))
    return None


def _tech_source_from_signal_or_metadata(sig: Dict[str, Any], key: str) -> Any:
    md = sig.get("metadata") if isinstance(sig.get("metadata"), dict) else {}
    return sig.get(key) or md.get(key)


def _technical_provenance_features(sig: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "volume_ratio_missing": _tech_flag_from_signal_or_metadata(sig, "volume_ratio_missing"),
        "vwap_missing": _tech_flag_from_signal_or_metadata(sig, "vwap_missing"),
        "rsi_missing": _tech_flag_from_signal_or_metadata(sig, "rsi_missing"),
        "macd_missing": _tech_flag_from_signal_or_metadata(sig, "macd_missing"),
        "volume_ratio_source": _tech_source_from_signal_or_metadata(sig, "volume_ratio_source"),
        "vwap_source": _tech_source_from_signal_or_metadata(sig, "vwap_source"),
        "tech_source": _tech_source_from_signal_or_metadata(sig, "tech_source"),
        "session_bar_count": _tech_from_signal_or_metadata(sig, "session_bar_count"),
    }


def _orb_from_signal(sig: Dict[str, Any]) -> Dict[str, Any]:
    od = sig.get("orb_data")
    if isinstance(od, dict):
        return {
            "orb_high": od.get("orb_high"),
            "orb_low": od.get("orb_low"),
            "orb_range": od.get("orb_range"),
        }
    if od is not None and hasattr(od, "orb_high"):
        return {
            "orb_high": getattr(od, "orb_high", None),
            "orb_low": getattr(od, "orb_low", None),
            "orb_range": getattr(od, "orb_range", None),
        }
    return {
        "orb_high": sig.get("orb_high"),
        "orb_low": sig.get("orb_low"),
        "orb_range": sig.get("orb_range"),
    }


def extract_signal_features(sig: Dict[str, Any]) -> Dict[str, Any]:
    """Flat technical + rank fields for one SO signal (JSON-serializable)."""
    orb = _orb_from_signal(sig)
    features = {
        "symbol": (sig.get("symbol") or "").strip().upper() or None,
        "side": str(sig.get("side", "LONG")).upper(),
        "current_price": sig.get("current_price") or sig.get("price"),
        "confidence": sig.get("confidence"),
        "priority_score": sig.get("priority_score"),
        "priority_rank": sig.get("priority_rank"),
        "rsi": _tech_from_signal_or_metadata(sig, "rsi"),
        "macd_histogram": _tech_from_signal_or_metadata(sig, "macd_histogram"),
        "volume_ratio": _tech_from_signal_or_metadata(sig, "volume_ratio"),
        "underlying_symbol": sig.get("underlying_symbol"),
        "underlying_volume_ratio": sig.get("underlying_volume_ratio"),
        "underlying_rsi": sig.get("underlying_rsi"),
        "underlying_macd_histogram": sig.get("underlying_macd_histogram"),
        "orb_volume_ratio": _tech_from_signal_or_metadata(sig, "orb_volume_ratio"),
        "rs_vs_spy": _tech_from_signal_or_metadata(sig, "rs_vs_spy"),
        "vwap_distance_pct": _tech_from_signal_or_metadata(sig, "vwap_distance_pct"),
        "orb_high": orb.get("orb_high"),
        "orb_low": orb.get("orb_low"),
        "orb_range": orb.get("orb_range"),
        "volume_color": sig.get("volume_color") or sig.get("validation_volume_color"),
        "quantity": sig.get("quantity"),
        "position_size_pct": sig.get("position_size_pct"),
        "risk_reduction_applied": sig.get("risk_reduction_applied"),
    }
    features.update(_technical_provenance_features(sig))
    return features


def extract_0dte_unified_features(
    sig: Dict[str, Any],
    *,
    snapshot_strategy: str = "easy_orb_0dte",
    include_nulls: bool = False,
    include_exit_fields: bool = False,
) -> Dict[str, Any]:
    """
    Unified ORB1 Breakout + Trendline 0DTE feature map (Rev 00401 / 00428).

    Both paths share ``FEATURE_KEYS_0DTE_UNIFIED`` — underlying core, options
    microstructure (bid/ask/spread/OI/volume), ORB pipeline funnel fields, and
    Trendline structure fields. When ``include_nulls`` is True, every schema key
    is present (null when unavailable) so 7:30 / execute / exit payloads match.
    """
    strategy = str(snapshot_strategy or "easy_orb_0dte")
    if strategy not in ("easy_orb_0dte", "easy_trendline_0dte", "easy_breakout_0dte"):
        strategy = "easy_orb_0dte"

    row = dict(sig)
    if strategy == "easy_orb_0dte":
        row = normalize_0dte_pipeline_row_orb_fields(row)

    full = extract_path_features(
        row,
        snapshot_strategy=strategy,
        include_nulls=False,
        include_exit_fields=include_exit_fields,
    )
    orb = _orb_from_signal(row)
    md = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}

    from .options_snapshot_fields import canonicalize_option_side_label

    # Trade Side/Direction = CALL|PUT only (never LONG/SHORT). Line geometry stays in line_geometry.
    side = canonicalize_option_side_label(row)
    opt_label = str(row.get("option_type_label") or side).upper()
    if opt_label not in ("CALL", "PUT"):
        opt_label = side

    full.update(
        {
            "symbol": (row.get("symbol") or "").strip().upper() or None,
            "side": side,
            "direction": side,
            "option_side": side.lower() if side in ("CALL", "PUT") else side,
            "expected_option_side": (
                str(row.get("expected_option_side") or md.get("expected_option_side") or "").strip().lower()
                or (side.lower() if side in ("CALL", "PUT") else side)
            ),
            "option_type": str(
                row.get("option_type")
                or md.get("expected_option_side")
                or side
            ).strip().lower(),
            "option_type_label": opt_label,
            "current_price": row.get("current_price") or row.get("price") or row.get("close_at_break"),
            "confidence": row.get("confidence") or row.get("eligibility_score"),
            "priority_score": row.get("priority_score"),
            "priority_rank": row.get("priority_rank"),
            "eligibility_score": row.get("eligibility_score"),
            "convex_score": row.get("convex_score"),
            "convex_eligible": row.get("convex_eligible"),
            "convex_top_reason": row.get("convex_top_reason") or row.get("top_reason"),
            "convex_all_reasons_short": row.get("convex_all_reasons_short") or row.get("all_reasons_short"),
            "pipeline_decision_stage": row.get("pipeline_decision_stage"),
            "pipeline_decision_reason": row.get("pipeline_decision_reason"),
            "hard_gate_passed": row.get("hard_gate_passed"),
            "hard_gate_reason": row.get("hard_gate_reason"),
            "strategy_type": row.get("strategy_type"),
            "momentum_score": row.get("momentum_score"),
            "target_delta": row.get("target_delta"),
            "spread_width": row.get("spread_width"),
            "spread_type": row.get("spread_type"),
            "rsi": _tech_from_signal_or_metadata(row, "rsi"),
            "macd_histogram": _tech_from_signal_or_metadata(row, "macd_histogram"),
            "volume_ratio": _first_tech_value(row, "volume_ratio", "orb_volume_ratio"),
            "orb_volume_ratio": _first_tech_value(row, "orb_volume_ratio", "volume_ratio"),
            "rs_vs_spy": _tech_from_signal_or_metadata(row, "rs_vs_spy"),
            "vwap_distance_pct": _tech_from_signal_or_metadata(row, "vwap_distance_pct"),
            **_technical_provenance_features(row),
            "orb_high": orb.get("orb_high"),
            "orb_low": orb.get("orb_low"),
            "orb_range": orb.get("orb_range"),
            "orb_range_pct": row.get("orb_range_pct")
            or (
                (row.get("orb_data") or {}).get("orb_range_pct")
                if isinstance(row.get("orb_data"), dict)
                else None
            ),
            "momentum_10": row.get("momentum_10") or row.get("momentum"),
            "quantity": row.get("quantity"),
            "setup_type": row.get("setup_type") or md.get("setup_type"),
            "expected_option_side": row.get("expected_option_side") or md.get("expected_option_side"),
            "trigger_direction": row.get("trigger_direction") or md.get("trigger_direction"),
            "structure_fit_mse": row.get("structure_fit_mse") or md.get("structure_fit_mse"),
            "structure_selection_reason": row.get("structure_selection_reason")
            or md.get("structure_selection_reason"),
            "trendline_structure_source": row.get("trendline_structure_source")
            or md.get("trendline_structure_source"),
            "trendline_slot": row.get("trendline_slot") or md.get("trendline_slot"),
            "a1_policy": row.get("a1_policy") or md.get("a1_policy"),
            "line_geometry": row.get("line_geometry") or md.get("line_geometry"),
            "break_distance_pct": row.get("break_distance_pct") or md.get("break_distance_pct"),
            "entry_distance_pct": row.get("entry_distance_pct") or md.get("entry_distance_pct"),
            "orb_zone": row.get("orb_zone") or md.get("orb_zone"),
            "vwap_position": row.get("vwap_position") or md.get("vwap_position"),
            "trade_id": row.get("trade_id") or md.get("trade_id"),
            "break_snapshot_joined": row.get("break_snapshot_joined") or md.get("break_snapshot_joined"),
            "broker_success": row.get("broker_success") if row.get("broker_success") is not None else md.get("broker_success"),
            "broker_error": row.get("broker_error") or md.get("broker_error"),
            "reject_reason": row.get("reject_reason") or md.get("reject_reason"),
            "velocity_pct": row.get("velocity_pct") or md.get("velocity_pct"),
            "body_ratio_vs_avg": row.get("body_ratio_vs_avg") or md.get("body_ratio_vs_avg"),
            "score": row.get("score") or md.get("simple_quality_score") or row.get("priority_score"),
            "line_price_at_break": row.get("line_price_at_break") or md.get("line_price_at_break"),
            "close_at_break": row.get("close_at_break") or md.get("close_at_break"),
            "break_archetype": row.get("break_archetype") or md.get("break_archetype"),
            "minutes_from_730_to_break": row.get("minutes_from_730_to_break")
            or md.get("minutes_from_730_to_break"),
            "pressure_score": row.get("pressure_score"),
            "entry_mode": row.get("entry_mode"),
            "candidate_state": row.get("candidate_state") or md.get("candidate_state"),
            # QG / contract — first-class for desk matrix (survive densify)
            "quality_gate_report": (
                row.get("quality_gate_report")
                if isinstance(row.get("quality_gate_report"), dict)
                else (
                    md.get("quality_gate_report")
                    if isinstance(md.get("quality_gate_report"), dict)
                    else None
                )
            ),
            "otm_pct": row.get("otm_pct") if row.get("otm_pct") is not None else md.get("otm_pct"),
            "chosen_delta": row.get("chosen_delta")
            if row.get("chosen_delta") is not None
            else md.get("chosen_delta"),
            "fill_delta": row.get("fill_delta") if row.get("fill_delta") is not None else md.get("fill_delta"),
            "otm_research_exception": row.get("otm_research_exception")
            if row.get("otm_research_exception") is not None
            else md.get("otm_research_exception"),
            "post_break_chop_pct": row.get("post_break_chop_pct")
            if row.get("post_break_chop_pct") is not None
            else md.get("post_break_chop_pct"),
            "quality_score": row.get("quality_score")
            if row.get("quality_score") is not None
            else md.get("quality_score"),
        }
    )
    # Desk volume aliases when chain vols present
    if full.get("call_volume") is None and (
        full.get("call_chain_volume") is not None or row.get("call_chain_volume") is not None
    ):
        full["call_volume"] = full.get("call_chain_volume") or row.get("call_chain_volume")
    if full.get("put_volume") is None and (
        full.get("put_chain_volume") is not None or row.get("put_chain_volume") is not None
    ):
        full["put_volume"] = full.get("put_chain_volume") or row.get("put_chain_volume")
    # Drop empty strings; keep None only when densifying below.
    sparse = {k: v for k, v in full.items() if v is not None and v != ""}
    if include_nulls:
        from .po_snapshot_hydration import (
            densify_feature_map,
            ensure_orb_range_pct,
            overlay_quality_gate_into_row,
        )

        overlay_quality_gate_into_row(sparse)
        overlay_quality_gate_into_row(row)
        ensure_orb_range_pct(row)
        if sparse.get("orb_range_pct") is None and row.get("orb_range_pct") is not None:
            sparse["orb_range_pct"] = row.get("orb_range_pct")
        # Preserve nested QG report through densify (ensure_* may not touch it)
        qgr = sparse.get("quality_gate_report")
        densified = densify_feature_map(
            sparse,
            snapshot_strategy=strategy,
            include_exit_fields=include_exit_fields,
        )
        if qgr is not None and densified.get("quality_gate_report") is None:
            densified["quality_gate_report"] = qgr
        if side in ("CALL", "PUT"):
            densified["option_side"] = side.lower()
            densified["side"] = side
            if not densified.get("expected_option_side"):
                densified["expected_option_side"] = side.lower()
        return densified
    if side in ("CALL", "PUT"):
        sparse["option_side"] = side.lower()
        sparse["side"] = side
        if not sparse.get("expected_option_side"):
            sparse["expected_option_side"] = side.lower()
    return sparse


def extract_orb_0dte_pipeline_features(
    row: Dict[str, Any],
    *,
    include_nulls: bool = False,
    include_exit_fields: bool = False,
) -> Dict[str, Any]:
    """ORB1 Breakout pipeline row — unified 0DTE schema."""
    return extract_0dte_unified_features(
        row,
        snapshot_strategy="easy_orb_0dte",
        include_nulls=include_nulls,
        include_exit_fields=include_exit_fields,
    )


def _safe_json_value(value: Any) -> Any:
    """Best-effort conversion of nested values to JSON-safe output."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _safe_json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_safe_json_value(v) for v in value]
    if hasattr(value, "to_dict"):
        try:
            return _safe_json_value(value.to_dict())
        except Exception:
            return str(value)
    return str(value)


def _apply_orb_data_fallbacks(out: Dict[str, Any], sig: Dict[str, Any]) -> None:
    od = sig.get("orb_data")
    if isinstance(od, dict):
        for dst, src in (
            ("orb_high", "orb_high"),
            ("orb_low", "orb_low"),
            ("orb_open", "orb_open"),
            ("orb_close", "orb_close"),
            ("orb_volume", "orb_volume"),
            ("orb_range_pct", "orb_range_pct"),
        ):
            if out.get(dst) is None and od.get(src) is not None:
                out[dst] = _safe_json_value(od.get(src))
    elif od is not None:
        for dst, attr in (
            ("orb_high", "orb_high"),
            ("orb_low", "orb_low"),
            ("orb_open", "orb_open"),
            ("orb_close", "orb_close"),
            ("orb_volume", "orb_volume"),
            ("orb_range_pct", "orb_range_pct"),
        ):
            if out.get(dst) is None:
                out[dst] = _safe_json_value(getattr(od, attr, None))


def _apply_common_aliases(out: Dict[str, Any], sig: Dict[str, Any]) -> None:
    md = sig.get("metadata") if isinstance(sig.get("metadata"), dict) else {}
    if out.get("rank") is None and sig.get("priority_rank") is not None:
        out["rank"] = _safe_json_value(sig.get("priority_rank"))
    if out.get("shares") is None and sig.get("quantity") is not None:
        out["shares"] = _safe_json_value(sig.get("quantity"))
    if out.get("close") is None:
        out["close"] = _safe_json_value(sig.get("close") or sig.get("current_price") or sig.get("price"))
    if out.get("current_price") is None:
        out["current_price"] = _safe_json_value(sig.get("current_price") or sig.get("price"))
    # Canonical live equity for asset-stop research (alias current_price/close).
    live_und = _safe_json_value(
        sig.get("underlying_price")
        or out.get("underlying_price")
        or out.get("current_price")
        or out.get("close")
        or sig.get("current_price")
        or sig.get("close")
        or sig.get("price")
    )
    if live_und is not None:
        out["underlying_price"] = live_und
        if out.get("current_price") is None:
            out["current_price"] = live_und
        if out.get("close") is None:
            out["close"] = live_und
    entry_und = _safe_json_value(
        sig.get("entry_underlying")
        or sig.get("entry_underlying_price")
        or md.get("entry_underlying_price")
        or out.get("entry_underlying")
    )
    if entry_und is not None:
        out["entry_underlying"] = entry_und
        out["entry_underlying_price"] = entry_und
    elif live_und is not None and out.get("entry_underlying") is None:
        # Entry snaps: freeze entry to live when not already set.
        out["entry_underlying"] = live_und
        out["entry_underlying_price"] = live_und
    if out.get("exit_underlying") is None and sig.get("exit_underlying") is not None:
        out["exit_underlying"] = _safe_json_value(sig.get("exit_underlying"))
    if out.get("underlying_price_missing") is None:
        out["underlying_price_missing"] = bool(out.get("underlying_price") is None)
    if out.get("entry_price") is None:
        out["entry_price"] = _safe_json_value(sig.get("entry_price") or sig.get("current_price") or sig.get("price"))
    if out.get("volume") is None and sig.get("volume") is not None:
        out["volume"] = _safe_json_value(sig.get("volume"))
    if out.get("rsi") is None:
        out["rsi"] = _safe_json_value(_tech_from_signal_or_metadata(sig, "rsi"))
    if out.get("volume_ratio") is None:
        out["volume_ratio"] = _safe_json_value(_first_tech_value(sig, "volume_ratio", "orb_volume_ratio"))
    if out.get("orb_volume_ratio") is None and out.get("volume_ratio") is not None:
        out["orb_volume_ratio"] = out["volume_ratio"]
    for key, val in _technical_provenance_features(sig).items():
        if out.get(key) is None and val is not None:
            out[key] = _safe_json_value(val)
    if out.get("vwap") is None:
        out["vwap"] = _safe_json_value(sig.get("vwap") or md.get("vwap"))
    if out.get("vwap_distance_pct") is None:
        out["vwap_distance_pct"] = _safe_json_value(
            _tech_from_signal_or_metadata(sig, "vwap_distance_pct") or md.get("entry_distance_pct")
        )
    if out.get("macd_histogram") is None:
        out["macd_histogram"] = _safe_json_value(_tech_from_signal_or_metadata(sig, "macd_histogram"))
    if out.get("rs_vs_spy") is None:
        out["rs_vs_spy"] = _safe_json_value(_tech_from_signal_or_metadata(sig, "rs_vs_spy"))
    if out.get("momentum_10") is None:
        out["momentum_10"] = _safe_json_value(sig.get("momentum_10") or sig.get("momentum"))
    if out.get("priority_score") is None and sig.get("priority_score") is not None:
        out["priority_score"] = _safe_json_value(sig.get("priority_score"))
    if out.get("confidence") is None and sig.get("confidence") is not None:
        out["confidence"] = _safe_json_value(sig.get("confidence"))


def extract_path_features(
    sig: Dict[str, Any],
    *,
    snapshot_strategy: str = "easy_orb_etf_so",
    include_nulls: bool = False,
    include_exit_fields: bool = False,
) -> Dict[str, Any]:
    """
    Path-specific feature map (schema v2).

    By default omits null placeholders. For unified 0DTE payloads set
    ``include_nulls=True`` so every schema key is present for cohort join.
    """
    from .po_snapshot_hydration import densify_feature_map, ensure_orb_range_pct

    keys = schema_for_strategy(snapshot_strategy)
    md = sig.get("metadata") if isinstance(sig.get("metadata"), dict) else {}
    out: Dict[str, Any] = {}
    for k in keys:
        if (not include_exit_fields) and is_entry_excluded_field(k):
            continue
        val = sig.get(k)
        if val is None and isinstance(md, dict):
            val = md.get(k)
        if val is not None:
            out[k] = _safe_json_value(val)
    if include_exit_fields:
        for k in ENTRY_SNAPSHOT_EXCLUDED:
            val = sig.get(k)
            if val is None and isinstance(md, dict):
                val = md.get(k)
            if val is not None:
                out[k] = _safe_json_value(val)
    _apply_orb_data_fallbacks(out, sig)
    _apply_common_aliases(out, sig)
    _sanitize_invent_tech_zeros(out, sig)
    ensure_orb_range_pct(out)
    ensure_orb_range_pct(sig)
    if out.get("orb_range_pct") is None and sig.get("orb_range_pct") is not None:
        out["orb_range_pct"] = _safe_json_value(sig.get("orb_range_pct"))
    from .po_snapshot_hydration import attach_tp_profile_extras

    attach_tp_profile_extras(out, sig, md)
    if include_nulls:
        return densify_feature_map(
            out,
            snapshot_strategy=snapshot_strategy,
            include_exit_fields=include_exit_fields,
        )
    return {k: v for k, v in out.items() if v is not None}


def extract_signal_features_89(sig: Dict[str, Any]) -> Dict[str, Any]:
    """Backward-compatible alias — sparse ORB SO schema (no null placeholders)."""
    return extract_path_features(sig, snapshot_strategy="easy_orb_etf_so")


def merge_comprehensive_market_data(
    target: Dict[str, Any],
    comprehensive_data: Optional[Dict[str, Any]],
    *,
    overwrite: bool = False,
    snapshot_strategy: str = "easy_orb_etf_so",
) -> None:
    """Merge E*TRADE comprehensive market data onto a flat signal dict (path schema)."""
    if not comprehensive_data or not isinstance(target, dict):
        return
    allowed = set(schema_for_strategy(snapshot_strategy))
    for key, val in comprehensive_data.items():
        if val is None or is_entry_excluded_field(key):
            continue
        if key not in allowed and key not in ("momentum", "previous_day_high", "previous_day_low"):
            continue
        if not overwrite and target.get(key) is not None:
            continue
        target[key] = _safe_json_value(val)
    aliases = {
        "momentum_10": comprehensive_data.get("momentum"),
        "spy_price": comprehensive_data.get("spy_price"),
        "spy_change_pct": comprehensive_data.get("spy_change_pct"),
        "prev_day_high": comprehensive_data.get("prev_day_high") or comprehensive_data.get("previous_day_high"),
        "prev_day_low": comprehensive_data.get("prev_day_low") or comprehensive_data.get("previous_day_low"),
        "rsi_14": comprehensive_data.get("rsi_14") or comprehensive_data.get("rsi"),
    }
    for key, val in aliases.items():
        if val is None or is_entry_excluded_field(key):
            continue
        if overwrite or target.get(key) is None:
            target[key] = _safe_json_value(val)


def extract_trendline_pool_features(
    sig: Dict[str, Any],
    *,
    include_nulls: bool = False,
    include_exit_fields: bool = False,
) -> Dict[str, Any]:
    """Trendline break/emit/execute row — unified 0DTE schema."""
    return extract_0dte_unified_features(
        sig,
        snapshot_strategy="easy_trendline_0dte",
        include_nulls=include_nulls,
        include_exit_fields=include_exit_fields,
    )


def extract_breakout_pool_features(
    sig: Dict[str, Any],
    *,
    include_nulls: bool = False,
    include_exit_fields: bool = False,
) -> Dict[str, Any]:
    """Breakout ORB-range execute row — unified 0DTE schema."""
    return extract_0dte_unified_features(
        sig,
        snapshot_strategy="easy_breakout_0dte",
        include_nulls=include_nulls,
        include_exit_fields=include_exit_fields,
    )


def _risk_assessment_to_dict(obj: Any) -> Any:
    if obj is None:
        return None
    if is_dataclass(obj):
        return asdict(obj)
    return {"repr": str(obj)}


def _order_snapshot_envelope(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Put identity, then clocks, then the feature arrays. Values stay put.

    Provenance is envelope metadata. It is not one of the 267 feature keys.
    """
    identity = (
        "schema_version",
        "date",
        "captured_at_pt",
        "stage",
        "snapshot_strategy",
        "source_app",
        "app",
        "gcs_namespace",
    )
    clocks = (
        "po_snapshot_event_at",
        "po_decision_freeze_at",
        "po_decision_freeze_mode",
        "po_20p_event_at",
        "po_age_120s_event_at",
        "po_first_red_event_at",
        "po_mfe_50p_event_at",
        "po_pullback_event_at",
        "po_exit_event_at",
        "lifecycle_event",
        "po_tech_asof",
        "po_tech_asof_at",
        "po_session_tech_asof_at",
        "po_quote_asof_at",
        "po_spy_asof_at",
        "po_chain_asof_at",
        "po_premium_path_asof_at",
        "po_sma_horizon",
        "po_clock_gaps",
        "po_clock_rejects",
        "po_chain_source",
        "po_session_tech_source",
        "po_entry_training_eligible",
        "po_entry_sealed",
        "po_decision_gate_at",
        "po_decision_freeze",
        "po_freeze_parity_diffs",
        "po_handoff_link",
        "po_dir5m",
    )
    ordered: Dict[str, Any] = {}
    for key in identity + clocks:
        if key in payload:
            ordered[key] = payload[key]
    for key, value in payload.items():
        if key not in ordered:
            ordered[key] = value
    return ordered


# t194u: dir_pct_change_5m telemetry persisted on EVERY PO snapshot envelope
# (handoff / entry / 20p / exit). The 267-key feature rows keep their fixed schema,
# so these ride in ``po_dir5m`` (values as stamped on the owned row at its own clock).
PO_DIR5M_KEYS: Tuple[str, ...] = (
    "dir_pct_change_5m",
    "dir_pct_prior_age_s",
    "dir_pct_prior_window",
    "dir_pct_missing_reason",
    "dir_pct_approach_source",
    "dir_pct_approach_lead_s",
    "dir_pct_now_chain_age_s",
    "dir_pct_asof_epoch",
    # t204u causality clocks (ISO UTC): prior < now-chain <= asof <= stage event.
    "dir_pct_asof_at",
    "dir_pct_now_chain_observed_at",
    "dir_pct_prior_observed_at",
)


def po_dir5m_block(
    pools: Any, *, extra: Optional[Dict[str, Any]] = None, freeze: Optional[Dict[str, Any]] = None
) -> Optional[Dict[str, Any]]:
    """First owned row carrying dir5m telemetry -> {key: value}; None when nothing stamped."""
    src: Optional[Dict[str, Any]] = None
    for pool in pools or ():
        for s in pool or []:
            if isinstance(s, dict) and any(k in s for k in PO_DIR5M_KEYS[1:]):
                src = s
                break
        if src is not None:
            break
    out: Dict[str, Any] = {}
    if src is not None:
        out = {k: _safe_json_value(src.get(k)) for k in PO_DIR5M_KEYS}
    elif isinstance(freeze, dict) and isinstance(freeze.get("dir5m"), dict):
        d = freeze["dir5m"]
        vals = freeze.get("values") if isinstance(freeze.get("values"), dict) else {}
        out = {
            "dir_pct_change_5m": vals.get("dir_pct_change_5m"),
            "dir_pct_prior_age_s": d.get("prior_age_s"),
            "dir_pct_prior_window": d.get("window"),
            "dir_pct_missing_reason": d.get("missing_reason"),
            "dir_pct_approach_source": d.get("approach_source"),
            "dir_pct_approach_lead_s": d.get("approach_lead_s"),
            "dir_pct_now_chain_age_s": d.get("now_chain_age_s"),
            "dir_pct_asof_epoch": None,
            "dir_pct_asof_at": d.get("asof_at"),
            "dir_pct_now_chain_observed_at": d.get("now_chain_observed_at"),
            "dir_pct_prior_observed_at": d.get("prior_observed_at"),
        }
    if not out and isinstance(extra, dict) and "dir_pct_change_5m" in extra:
        out = {k: _safe_json_value(extra.get(k)) for k in PO_DIR5M_KEYS}
    if not out:
        return None
    out["source"] = "row" if src is not None else ("freeze" if freeze else "extra")
    return out


def build_execution_snapshot_payload(
    *,
    date_key: str,
    stage: str,
    signals_full_pool: List[Dict[str, Any]],
    signals_selected_for_execution: List[Dict[str, Any]],
    red_day_filter_blocked: bool,
    portfolio_red_day_pattern: bool,
    red_day_reason: Optional[str],
    red_day_metrics: Optional[Dict[str, Any]],
    enhanced_assessment: Any,
    spy_momentum: float,
    vix_level: float,
    total_scanned: int,
    snapshot_strategy: str = "easy_orb_etf_so",
    extra: Optional[Dict[str, Any]] = None,
    feature_extractor=None,
) -> Dict[str, Any]:
    from zoneinfo import ZoneInfo

    extract = feature_extractor or extract_signal_features
    is_0dte = str(snapshot_strategy or "") in (
        "easy_orb_0dte",
        "easy_trendline_0dte",
        "easy_breakout_0dte",
    )
    # 20p uses the same fixed key set as an exit so participation and HWM
    # fields are present. It is not an exit: the writer leaves outcome keys null.
    _stage_l = str(stage or "").lower()
    try:
        from .po_lifecycle_moments import is_lifecycle_moment

        is_exit_stage = "exited" in _stage_l or is_lifecycle_moment(stage=stage)
    except Exception:
        is_exit_stage = "exited" in _stage_l or "options_20p" in _stage_l

    def _with_extra(s: Dict[str, Any]) -> Dict[str, Any]:
        if not extra:
            return s
        from .po_snapshot_hydration import merge_snapshot_extra_into_row

        merged = dict(s)
        merge_snapshot_extra_into_row(merged, extra)
        return merged

    def _extract_unified(s: Dict[str, Any]) -> Dict[str, Any]:
        s = _with_extra(s)
        raw = extract(s)
        if not is_0dte:
            return raw
        # Force fixed-key payload for ORB/Trendline 7:30 + execute + exit.
        from .po_snapshot_hydration import densify_feature_map, ensure_orb_range_pct

        ensure_orb_range_pct(s)
        if isinstance(raw, dict) and raw.get("orb_range_pct") is None and s.get("orb_range_pct") is not None:
            raw = dict(raw)
            raw["orb_range_pct"] = s.get("orb_range_pct")
        densified = densify_feature_map(
            raw if isinstance(raw, dict) else {},
            snapshot_strategy=str(snapshot_strategy),
            include_exit_fields=is_exit_stage,
        )
        from .po_snapshot_hydration import attach_tp_profile_extras

        attach_tp_profile_extras(densified, raw if isinstance(raw, dict) else {}, s)
        return densified

    def _extract_dense(s: Dict[str, Any]) -> Dict[str, Any]:
        return extract_path_features(
            _with_extra(s),
            snapshot_strategy=snapshot_strategy,
            include_nulls=is_0dte,
            include_exit_fields=is_exit_stage,
        )

    pt = ZoneInfo("America/Los_Angeles")
    now_pt = datetime.now(pt)
    try:
        from .runtime_mode import gcs_app_label, gcs_namespace
        _app = gcs_app_label()
        _ns = gcs_namespace()
    except Exception:
        _app, _ns = "RESEARCH", "research_app"
    # Prefer a real stamped spy_change from signals over Gate 3b invent 0.0.
    mc_spy: Any = spy_momentum
    try:
        if mc_spy is not None and abs(float(mc_spy)) < 1e-12:
            mc_spy = None
            for _pool in (signals_selected_for_execution, signals_full_pool):
                for _s in _pool or []:
                    if not isinstance(_s, dict):
                        continue
                    _sc = _s.get("spy_change_pct")
                    if _sc is None:
                        continue
                    try:
                        if abs(float(_sc)) >= 1e-12:
                            mc_spy = float(_sc)
                            break
                    except (TypeError, ValueError):
                        continue
                if mc_spy is not None:
                    break
    except (TypeError, ValueError):
        mc_spy = spy_momentum
    payload: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "date": date_key,
        "captured_at_pt": now_pt.isoformat(),
        "stage": stage,
        "source_app": _app,
        "app": _app,
        "gcs_namespace": _ns,
        "snapshot_strategy": str(snapshot_strategy or "unknown"),
        "market_context": {
            "spy_momentum_pct": mc_spy,
            "vix_level": vix_level,
            "total_scanned": total_scanned,
        },
        "red_day": {
            "execution_blocked_by_portfolio_flag": red_day_filter_blocked,
            "portfolio_pattern_triggered": portfolio_red_day_pattern,
            "reason": red_day_reason,
            "metrics": red_day_metrics,
        },
        "enhanced_red_day": _risk_assessment_to_dict(enhanced_assessment),
        "signal_count_full_pool": len(signals_full_pool),
        "signal_count_selected": len(signals_selected_for_execution),
        "symbols_full_pool": [
            (s.get("symbol") or "").strip().upper()
            for s in signals_full_pool
            if isinstance(s, dict) and s.get("symbol")
        ],
        "symbols_selected": [
            (s.get("symbol") or "").strip().upper()
            for s in signals_selected_for_execution
            if isinstance(s, dict) and s.get("symbol")
        ],
        "features_full_pool": [_extract_unified(s) for s in signals_full_pool if isinstance(s, dict)],
        "features_selected": [_extract_unified(s) for s in signals_selected_for_execution if isinstance(s, dict)],
        "feature_schema_keys": list(schema_for_strategy(snapshot_strategy)),
        "feature_schema_89_keys": list(schema_for_strategy(snapshot_strategy)),
        "features_full_pool_dense": [
            _extract_dense(s)
            for s in signals_full_pool
            if isinstance(s, dict)
        ],
        "features_selected_dense": [
            _extract_dense(s)
            for s in signals_selected_for_execution
            if isinstance(s, dict)
        ],
        "features_89_full_pool": [
            _extract_dense(s)
            for s in signals_full_pool
            if isinstance(s, dict)
        ],
        "features_89_selected": [
            _extract_dense(s)
            for s in signals_selected_for_execution
            if isinstance(s, dict)
        ],
    }
    if extra:
        payload["extra"] = extra
    # Decision-freeze provenance on the snapshot envelope (not densified schema #234).
    freeze_at = None
    freeze_mode = None
    for pool in (signals_selected_for_execution, signals_full_pool):
        for s in pool or []:
            if not isinstance(s, dict):
                continue
            if s.get("po_decision_freeze_at") is not None:
                freeze_at = s.get("po_decision_freeze_at")
                freeze_mode = s.get("po_decision_freeze_mode") or "closed_bar"
                break
        if freeze_at is not None:
            break
    if freeze_at is not None:
        payload["po_decision_freeze_at"] = freeze_at
        payload["po_decision_freeze_mode"] = freeze_mode or "closed_bar"
    tech_asof = None
    tech_asof_at = None
    for pool in (signals_selected_for_execution, signals_full_pool):
        for s in pool or []:
            if not isinstance(s, dict):
                continue
            if s.get("po_tech_asof"):
                tech_asof = s.get("po_tech_asof")
                tech_asof_at = s.get("po_tech_asof_at")
                break
        if tech_asof:
            break
    if tech_asof:
        payload["po_tech_asof"] = tech_asof
        if tech_asof_at not in (None, ""):
            payload["po_tech_asof_at"] = tech_asof_at
    clock_names = (
        "po_snapshot_event_at",
        "po_chain_asof_at",
        "po_premium_path_asof_at",
        "po_session_tech_asof_at",
        "po_quote_asof_at",
        "po_spy_asof_at",
        "po_20p_event_at",
        "po_20p_seal_status",
        "po_age_120s_event_at",
        "po_first_red_event_at",
        "po_mfe_50p_event_at",
        "po_pullback_event_at",
        "po_exit_event_at",
        "po_exit_seal_status",
        "lifecycle_event",
        "po_sma_horizon",
        "po_clock_gaps",
        "po_clock_rejects",
        "po_chain_source",
        "po_session_tech_source",
        "po_entry_training_eligible",
        "po_entry_sealed",
    )
    for pool in (signals_selected_for_execution, signals_full_pool):
        for s in pool or []:
            if not isinstance(s, dict):
                continue
            for name in clock_names:
                if s.get(name) not in (None, "") and payload.get(name) in (None, ""):
                    payload[name] = s.get(name)
    # Oct 7 2026 (A6): the one gate decision freeze (values the Trade Profile
    # scored + per-source ages + gate clock) rides on the envelope, so the
    # snapshot of record can be checked against the gate key-for-key.
    for pool in (signals_selected_for_execution, signals_full_pool):
        found = False
        for s in pool or []:
            if not isinstance(s, dict):
                continue
            fz = s.get("po_decision_freeze")
            if isinstance(fz, dict):
                payload["po_decision_freeze"] = fz
                payload["po_decision_gate_at"] = fz.get("gate_at")
                if s.get("po_freeze_parity_diffs") is not None:
                    payload["po_freeze_parity_diffs"] = s.get("po_freeze_parity_diffs")
                found = True
                break
        if found:
            break
    try:
        from .po_source_contract import stamp_chain_age_fields

        stamp_chain_age_fields(payload)
    except Exception:
        pass
    for pool in (signals_selected_for_execution, signals_full_pool):
        for s in pool or []:
            if isinstance(s, dict) and s.get("po_handoff_link") and "po_handoff_link" not in payload:
                payload["po_handoff_link"] = s.get("po_handoff_link")
    try:
        _d5 = po_dir5m_block(
            (signals_selected_for_execution, signals_full_pool),
            extra=extra if isinstance(extra, dict) else None,
            freeze=payload.get("po_decision_freeze") if isinstance(payload.get("po_decision_freeze"), dict) else None,
        )
        if _d5 is not None:
            payload["po_dir5m"] = _d5
    except Exception:
        pass
    stage_s = str(stage or "")
    if "emit_handoff" in stage_s or stage_s.endswith("_handoff") or "options_executed" in stage_s:
        from .po_decision_hydrate import promote_entry_freeze_clocks
        from .po_source_contract import (
            clear_observe_stack_ineligibility,
            entry_training_eligible,
        )

        promote_entry_freeze_clocks(payload, stage=stage_s)
        payload["po_entry_training_eligible"] = (
            entry_training_eligible(payload)
            and str(payload.get("po_tech_asof") or "") != "executed_unsealed"
            and payload.get("po_handoff_link") != "missing"
        )
        if payload.get("po_entry_training_eligible") is True:
            clear_observe_stack_ineligibility(payload)
        try:
            from .po_source_contract import stale_chain_ineligibility

            _stale = stale_chain_ineligibility(payload)
        except Exception:
            _stale = None
        if _stale:
            from .po_source_contract import apply_training_ineligibility

            apply_training_ineligibility(payload, _stale)
        try:
            from .po_source_contract import quality_gate_reject_ineligibility

            _qg_rej = quality_gate_reject_ineligibility(payload)
        except Exception:
            _qg_rej = None
        if _qg_rej:
            from .po_source_contract import apply_training_ineligibility

            apply_training_ineligibility(payload, _qg_rej)
    return _order_snapshot_envelope(payload)


def preserve_sealed_event(payload: Dict[str, Any]) -> Optional[str]:
    """Write only the seal object. The canonical snapshot is not uploaded yet."""
    from .po_canonical_store import canonical_object_name, commit_bytes, seal_object_name

    canon = canonical_object_name(payload)
    if not canon:
        return None
    payload["po_canonical_event"] = canon[:-5]
    body = json.dumps(payload, separators=(",", ":"), default=str)
    status = commit_bytes(seal_object_name(canon), body)
    if status in ("created", "identical"):
        return seal_object_name(canon)
    return None


def persist_execution_snapshot(payload: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    """
    Save snapshot to GCS and local priority_optimizer/execution_snapshots/.
    Returns (gcs_path_or_none, local_path_or_none).
    """
    try:
        from .config_loader import get_config_value
    except Exception:
        get_config_value = lambda k, d=None: d  # type: ignore

    enabled = str(get_config_value("ENABLE_EXECUTION_FEATURE_SNAPSHOT", "true")).lower() in (
        "1",
        "true",
        "yes",
    )
    if not enabled:
        return None, None
    try:
        keep_sessions = int(get_config_value("EXECUTION_SNAPSHOT_KEEP_SESSIONS", 2))
    except Exception:
        keep_sessions = 2
    if keep_sessions <= 0:
        keep_sessions = 2
    keep_sessions = min(2, keep_sessions)

    date_key = payload.get("date") or "unknown"
    stage = str(payload.get("stage") or "unknown").replace("/", "_")
    from .po_canonical_store import (
        body_sha256,
        canonical_object_name,
        commit_bytes,
        seal_object_name,
    )

    app_tag = str(payload.get("source_app") or payload.get("app") or "UNKNOWN").replace("/", "_")
    canon = canonical_object_name(payload)
    if canon:
        payload["po_canonical_event"] = canon[:-5]
        fname = canon
    else:
        time_part = datetime.now().strftime("%H%M%S")
        fname = f"{date_key}_{time_part}_{app_tag}_{stage}.json"

    root = Path(__file__).resolve().parent.parent
    local_dir = root / "priority_optimizer" / "execution_snapshots"
    local_dir.mkdir(parents=True, exist_ok=True)
    local_path = local_dir / fname
    body = json.dumps(payload, separators=(",", ":"), default=str)
    if canon:
        # Seal object first. A crash before the canonical upload can still
        # promote these exact bytes. It does not rebuild from a later market.
        seal_status = commit_bytes(seal_object_name(fname), body)
        if seal_status == "conflict":
            log.error("PO_SEAL_CONFLICT | object=%s", seal_object_name(fname))
            return None, None
        status = commit_bytes(fname, body)
        if status == "conflict":
            log.error("PO_CANONICAL_CONFLICT | object=%s | sha=%s", fname, body_sha256(body)[:12])
            return None, None
        if status in ("created", "identical"):
            gcs_path_written = f"canonical:{fname}"
        elif status == "failed":
            log.warning("PO_CANONICAL_SEAL_ONLY | object=%s | canonical_upload_failed=1", fname)
            return None, None
        else:
            gcs_path_written = None
    else:
        gcs_path_written = None
        try:
            from .gcs_persistence import get_gcs_persistence

            from .runtime_mode import gcs_execution_snapshot_object

            gcs = get_gcs_persistence()
            gcs_path = gcs_execution_snapshot_object(fname)
            if gcs.enabled and gcs.upload_string(gcs_path, body):
                gcs_path_written = gcs_path
        except Exception as exc:
            log.warning("Execution snapshot GCS upload skipped: %s", exc)

    if not _skip_local_snapshot_write():
        try:
            local_path.write_text(
                json.dumps(payload, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception as exc:
            log.error("Execution snapshot local write failed: %s", exc)
            return gcs_path_written, None
    else:
        local_path = None

    log.info(
        "EXECUTION_FEATURE_SNAPSHOT | app=%s | stage=%s | gcs=%s | local=%s | pool=%s | selected=%s",
        app_tag,
        stage,
        gcs_path_written or "-",
        str(local_path),
        payload.get("signal_count_full_pool"),
        payload.get("signal_count_selected"),
    )
    if local_path is not None:
        _prune_execution_snapshots(local_dir=local_dir, keep_sessions=keep_sessions)
    try:
        from .gcs_persistence import get_gcs_persistence

        gcs = get_gcs_persistence()
        if gcs.enabled:
            _prune_execution_snapshots_gcs(gcs=gcs, keep_sessions=keep_sessions)
    except Exception as exc:
        log.debug("Execution snapshot GCS retention prune skipped: %s", exc)
    return gcs_path_written, str(local_path) if local_path else None


def _extract_session_date_from_name(name: str) -> Optional[str]:
    date_part = name.split("_", 1)[0]
    if not _DATE_RE.match(date_part):
        return None
    return date_part


def _prune_execution_snapshots(local_dir: Path, keep_sessions: int) -> None:
    files = sorted(local_dir.glob("*.json"))
    if not files:
        return
    date_to_files: Dict[str, List[Path]] = {}
    for p in files:
        date_key = _extract_session_date_from_name(p.name)
        if not date_key:
            continue
        date_to_files.setdefault(date_key, []).append(p)
    if len(date_to_files) <= keep_sessions:
        return
    keep_dates = set(sorted(date_to_files.keys(), reverse=True)[:keep_sessions])
    deleted = 0
    for date_key, paths in date_to_files.items():
        if date_key in keep_dates:
            continue
        for p in paths:
            try:
                p.unlink(missing_ok=True)
                deleted += 1
            except Exception as exc:
                log.debug("Local snapshot prune failed for %s: %s", p, exc)
    if deleted:
        log.info("Execution snapshot retention(local): removed %s files", deleted)


def _prune_execution_snapshots_gcs(gcs: Any, keep_sessions: int) -> None:
    from .runtime_mode import gcs_execution_snapshot_list_prefixes

    paths: List[str] = []
    for prefix in gcs_execution_snapshot_list_prefixes():
        paths.extend(gcs.list_files(prefix))
    paths = sorted(set(paths))
    if not paths:
        return
    date_to_paths: Dict[str, List[str]] = {}
    for gcs_path in paths:
        name = Path(gcs_path).name
        date_key = _extract_session_date_from_name(name)
        if not date_key or not name.endswith(".json"):
            continue
        date_to_paths.setdefault(date_key, []).append(gcs_path)
    if len(date_to_paths) <= keep_sessions:
        return
    keep_dates = set(sorted(date_to_paths.keys(), reverse=True)[:keep_sessions])
    deleted = 0
    for date_key, group in date_to_paths.items():
        if date_key in keep_dates:
            continue
        for gcs_path in group:
            try:
                if gcs.delete_file(gcs_path):
                    deleted += 1
            except Exception as exc:
                log.debug("GCS snapshot prune failed for %s: %s", gcs_path, exc)
    if deleted:
        log.info("Execution snapshot retention(gcs): removed %s files", deleted)
