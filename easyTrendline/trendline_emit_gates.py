#!/usr/bin/env python3
"""
Phase 2 emit filters: geometry→side consistency and min break distance.
Post-draw quality admit lives on GATE_TL_* (quality_gate_stack).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from .trendline_models import OHLCVBar, TrendlineCandidate, TrendlineConfig
    from .trendline_simple_mode import SimpleBreakQualityResult

_SETUP_TO_SIDE = {
    "ascending_support": "put",
    "descending_resistance": "call",
}


def option_side_for_candidate(candidate: "TrendlineCandidate") -> str:
    from .trendline_models import TrendlineDirection

    return "call" if candidate.direction == TrendlineDirection.BEAR else "put"


def validate_geometry_side_consistency(candidate: "TrendlineCandidate") -> Optional[str]:
    """Return failure reason when setup_type and direction disagree."""
    setup = candidate.setup_payload or {}
    setup_type = str(setup.get("setup_type") or "").strip().lower()
    expected = _SETUP_TO_SIDE.get(setup_type)
    if not expected:
        return None
    actual = option_side_for_candidate(candidate)
    payload_side = str(setup.get("expected_option_side") or "").strip().lower()
    if payload_side and payload_side != expected:
        return f"geometry_payload_side_mismatch:setup={setup_type},payload={payload_side},expected={expected}"
    if actual != expected:
        return f"geometry_side_mismatch:setup={setup_type},side={actual},expected={expected}"
    return None


def effective_emit_min_break_distance_pct(
    candidate: "TrendlineCandidate", config: "TrendlineConfig"
) -> float:
    """Single break-distance floor at emit (unified with detect floor)."""
    _ = candidate
    return float(getattr(config, "simple_min_break_distance_pct", 0.0008) or 0.0008)


def bar_on_break_side(candidate: "TrendlineCandidate", bar: "OHLCVBar") -> bool:
    from .trendline_simple_mode import live_break_distance_pct

    _, break_dist, is_break = live_break_distance_pct(candidate, bar)
    return bool(is_break and break_dist > 0)


def init_post_break_tracking(sp: Dict[str, Any], bar: "OHLCVBar", *, quality_score: float) -> None:
    sp["material_break_quality_score"] = float(quality_score)
    sp["material_break_close"] = float(bar.close)
    sp["post_break_high"] = float(bar.high)
    sp["post_break_low"] = float(bar.low)
    sp["follow_through_bars"] = 0


def update_post_break_extremes(sp: Dict[str, Any], bar: "OHLCVBar") -> None:
    if sp.get("material_break_close") is None:
        return
    sp["post_break_high"] = max(float(sp.get("post_break_high") or bar.high), float(bar.high))
    sp["post_break_low"] = min(float(sp.get("post_break_low") or bar.low), float(bar.low))


def update_follow_through_bars(sp: Dict[str, Any], candidate: "TrendlineCandidate", bar: "OHLCVBar") -> int:
    """Count consecutive closes on the break side since material break."""
    if sp.get("material_break_close") is None:
        return 0
    if bar_on_break_side(candidate, bar):
        sp["follow_through_bars"] = int(sp.get("follow_through_bars") or 0) + 1
    else:
        sp["follow_through_bars"] = 0
    update_post_break_extremes(sp, bar)
    return int(sp.get("follow_through_bars") or 0)


def compute_post_break_range_pct(sp: Dict[str, Any]) -> Optional[float]:
    ref = sp.get("material_break_close")
    hi = sp.get("post_break_high")
    lo = sp.get("post_break_low")
    if ref is None or hi is None or lo is None:
        return None
    try:
        ref_f = float(ref)
        if ref_f <= 0:
            return None
        return (float(hi) - float(lo)) / ref_f
    except (TypeError, ValueError):
        return None


def apply_priority_emit_gates(
    candidate: "TrendlineCandidate",
    quality: "SimpleBreakQualityResult",
    config: "TrendlineConfig",
) -> "SimpleBreakQualityResult":
    """Geometry side + emit break floor. GATE_TL_* owns post-draw quality admit."""
    from .trendline_simple_mode import SimpleBreakQualityResult

    failed: List[str] = list(quality.failed_reasons)

    if bool(getattr(config, "simple_enforce_geometry_side", True)):
        geom_fail = validate_geometry_side_consistency(candidate)
        if geom_fail:
            failed.append(geom_fail)

    emit_min = effective_emit_min_break_distance_pct(candidate, config)
    if quality.break_distance_pct < emit_min:
        failed.append(
            f"emit_break_distance_below_min:{quality.break_distance_pct:.6f}<{emit_min:.6f}"
        )

    passed = not failed
    return SimpleBreakQualityResult(
        score=quality.score,
        passed=passed,
        failed_reasons=failed,
        vwap=getattr(quality, "vwap", 0.0),
        vwap_position=getattr(quality, "vwap_position", ""),
        vwap_distance_pct=getattr(quality, "vwap_distance_pct", None),
        rsi=getattr(quality, "rsi", None),
        volume_ratio=getattr(quality, "volume_ratio", None),
        volume_gate_status=getattr(quality, "volume_gate_status", "enforced"),
        break_distance_pct=getattr(quality, "break_distance_pct", 0.0),
        entry_distance_pct=getattr(quality, "entry_distance_pct", 0.0),
        seconds_from_material_break=getattr(quality, "seconds_from_material_break", 0.0),
        orb_position=getattr(quality, "orb_position", None),
        immediate_reclaim=quality.immediate_reclaim,
        rsi_missing=bool(getattr(quality, "rsi_missing", getattr(quality, "rsi", None) is None)),
        volume_ratio_missing=bool(
            getattr(quality, "volume_ratio_missing", getattr(quality, "volume_ratio", None) is None)
        ),
        macd_histogram=getattr(quality, "macd_histogram", None),
        ema_12=getattr(quality, "ema_12", None),
        ema_26=getattr(quality, "ema_26", None),
        macd_missing=bool(getattr(quality, "macd_missing", False)),
    )
