#!/usr/bin/env python3
"""
Geometry Phase 1 — dual-side pre-7:30 envelope draw, line relevance, telemetry.
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, replace
from datetime import datetime, time
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from .trendline_builder import OrbTestFailureClassification, TrendlineBuilder
from .trendline_models import (
    OHLCVBar,
    TrendlineConfig,
    TrendlineDefinition,
    TrendlineDirection,
    TrendlineSetupType,
)
from .trendline_setup_selector import (
    StructureSetupResult,
    _anchor_sources_from_tl,
    _eval_bars_for_line,
    _fit_ascending_support_mse,
    _fit_descending_resistance_mse,
    _pre_cutoff_bars,
    _safe_float,
    _session_cutoff_pt,
    _structure_window_bars,
)

log = logging.getLogger(__name__)


@dataclass
class GeometryCandidate:
    """One scored line in the Phase 1 contest."""

    trendline: TrendlineDefinition
    direction: TrendlineDirection
    mse: float
    control_score: float = 0.0
    touch_count: int = 0
    selection_source: str = "geometry_contest"
    line_distance_pct: float = 0.0


def line_distance_at_cutoff(
    trendline: TrendlineDefinition, cutoff: datetime, cutoff_price: float
) -> float:
    if cutoff_price <= 0:
        return float("inf")
    try:
        line_px = float(trendline.value_at(cutoff))
    except Exception:
        return float("inf")
    return abs(cutoff_price - line_px) / cutoff_price


def line_already_broken_at_cutoff(
    trendline: TrendlineDefinition, cutoff: datetime, cutoff_price: float
) -> bool:
    """
    True when price is already on the break side of an envelope line at ``cutoff``.

    Shared for every Trendline envelope (TL1 / TL2 / TL3):
      - bull / ascending support broken when price is below the line
      - bear / descending resistance broken when price is above the line

    Prefer intact envelopes when ranking, but do **not** flip to the opposite
    geometry solely because the correct-side line is lightly broken (HD CAP→CALL /
    GLD floor→PUT). Near-touch within ~5 bps counts as intact so micro close noise
    does not flip the broken flag.
    """
    if cutoff_price <= 0:
        return False
    try:
        line_px = float(trendline.value_at(cutoff))
    except Exception:
        return False
    if not math.isfinite(line_px):
        return False
    buffer = max(abs(line_px) * 0.0005, 1e-4)
    if trendline.direction == TrendlineDirection.BULL:
        return float(cutoff_price) < (line_px - buffer)
    return float(cutoff_price) > (line_px + buffer)


def _cutoff_close_inside_orb_band(
    cutoff_price: float,
    orb_high: Optional[float],
    orb_low: Optional[float],
) -> bool:
    """True when 7:30 close is strictly inside the ORB band (inclusive endpoints)."""
    oh = _safe_float(orb_high)
    ol = _safe_float(orb_low)
    if oh is None or ol is None or oh <= ol:
        return False
    return float(ol) <= float(cutoff_price) <= float(oh)


def passes_line_relevance(
    line_distance_pct: float, cfg: TrendlineConfig, *, orb_high: Optional[float], orb_low: Optional[float], cutoff_price: Optional[float]
) -> bool:
    max_pct = float(getattr(cfg, "max_line_distance_at_cutoff_pct", 0.02) or 0.02)
    if bool(getattr(cfg, "max_line_distance_volatility_adjust", False)) and orb_high and orb_low and cutoff_price and cutoff_price > 0:
        orb_rng = (float(orb_high) - float(orb_low)) / float(cutoff_price)
        max_pct = min(0.05, max_pct + 0.5 * orb_rng)
    return line_distance_pct <= max_pct + 1e-9


def compute_geometry_confidence(
    *,
    fit_mse: float,
    alt_mse: float,
    line_distance_pct: float,
    max_line_distance_pct: float,
    touch_count: int,
    selection_source: str,
    control_line_override_blocked: bool,
    mse_ratio_alt_to_primary: float,
    inside_orb: Optional[bool] = None,
) -> float:
    mse_ratio = alt_mse / fit_mse if fit_mse > 0 and math.isfinite(fit_mse) and math.isfinite(alt_mse) else 1.0
    mse_component = max(0.0, min(50.0, 50.0 * (1.0 - 1.0 / (1.0 + mse_ratio))))
    if max_line_distance_pct > 0:
        dist_component = max(0.0, min(35.0, 35.0 * (1.0 - line_distance_pct / max_line_distance_pct)))
    else:
        dist_component = 0.0
    touch_component = max(0.0, min(10.0, float(touch_count) * 2.0))
    source_component = 5.0
    if "structure_envelope" in selection_source or "pre730_structure_envelope" in selection_source:
        source_component = 10.0
    elif "control_line" in selection_source:
        source_component = 4.0
    elif "legacy" in selection_source or "mse" in selection_source:
        source_component = 3.0
    elif "cutoff" in selection_source:
        source_component = 2.0
    penalties = 0.0
    if not control_line_override_blocked and mse_ratio >= 3.0:
        penalties += 15.0
    if line_distance_pct > max_line_distance_pct:
        penalties += 20.0
    orb_component = 0.0
    if inside_orb is True:
        orb_component = 10.0
    elif inside_orb is False:
        orb_component = -10.0
    return max(
        0.0,
        min(
            100.0,
            mse_component + dist_component + touch_component + source_component + orb_component - penalties,
        ),
    )



def _geometry_audit_logs_enabled() -> bool:
    """TRENDLINE_GEOMETRY_AUDIT_LOGS (default false): per-candidate DRAW_AUDIT / STRUCTURE_COMPARISON at DEBUG."""
    try:
        from modules.config_loader import get_config_value

        raw = get_config_value("TRENDLINE_GEOMETRY_AUDIT_LOGS", "false")
    except Exception:
        raw = os.getenv("TRENDLINE_GEOMETRY_AUDIT_LOGS", "false")
    return str(raw or "").strip().lower() in ("1", "true", "yes", "on")


def _log_geometry_selection_audit(
    *,
    symbol: str,
    support_mse: float,
    resistance_mse: float,
    selected_geometry: str,
    selection_source: str,
    line_distance_pct: float,
    geometry_confidence: float,
    control_line_override: bool,
    control_line_override_blocked: bool,
    above_orb: bool,
    mse_ratio_alt_to_primary: float,
    candidates_considered: int,
    alternate_geometry: str,
) -> None:
    log.info(
        "GEOMETRY_SELECTION_AUDIT | symbol=%s | support_mse=%s | resistance_mse=%s | "
        "selected_geometry=%s | selection_source=%s | line_distance_pct=%.6f | "
        "geometry_confidence=%.2f | control_line_override=%s | control_line_override_blocked=%s | "
        "above_orb=%s | mse_ratio_alt_to_primary=%.4f | candidates_considered=%d | alternate_geometry=%s",
        symbol,
        f"{support_mse:.10g}" if math.isfinite(support_mse) else "inf",
        f"{resistance_mse:.10g}" if math.isfinite(resistance_mse) else "inf",
        selected_geometry,
        selection_source,
        line_distance_pct,
        geometry_confidence,
        str(control_line_override).lower(),
        str(control_line_override_blocked).lower(),
        str(above_orb).lower(),
        mse_ratio_alt_to_primary,
        candidates_considered,
        alternate_geometry,
    )


def _log_geometry_rejection_audit(
    *,
    symbol: str,
    reason: str,
    line_distance_pct: float,
    candidate_geometry: str,
    support_mse: float,
    resistance_mse: float,
    above_orb: bool,
    best_rejected_distance_pct: float,
) -> None:
    log.warning(
        "GEOMETRY_REJECTION_AUDIT | symbol=%s | reason=%s | line_distance_pct=%.6f | "
        "candidate_geometry=%s | support_mse=%s | resistance_mse=%s | above_orb=%s | "
        "best_rejected_distance_pct=%.6f",
        symbol,
        reason,
        line_distance_pct,
        candidate_geometry,
        f"{support_mse:.10g}" if math.isfinite(support_mse) else "inf",
        f"{resistance_mse:.10g}" if math.isfinite(resistance_mse) else "inf",
        str(above_orb).lower(),
        best_rejected_distance_pct,
    )


def _candidate_key(c: GeometryCandidate) -> Tuple[str, str, str]:
    a1, a2 = c.trendline.anchor_one, c.trendline.anchor_two
    return (c.direction.value, a1.ts.isoformat(), a2.ts.isoformat())


def _add_candidate(
    pool: Dict[Tuple[str, str, str], GeometryCandidate],
    candidate: GeometryCandidate,
) -> None:
    key = _candidate_key(candidate)
    existing = pool.get(key)
    if existing is None or candidate.mse < existing.mse:
        pool[key] = candidate


def _mse_for_direction(
    direction: TrendlineDirection,
    trendline: TrendlineDefinition,
    eval_bars: List[OHLCVBar],
) -> float:
    if direction == TrendlineDirection.BULL:
        return _fit_ascending_support_mse(trendline, eval_bars)
    return _fit_descending_resistance_mse(trendline, eval_bars)



def _candidate_to_setup_result(
    c: GeometryCandidate,
    struct_n: int,
    alternate_mse: Optional[float],
) -> StructureSetupResult:
    aos, ats = _anchor_sources_from_tl(c.trendline)
    if c.direction == TrendlineDirection.BEAR:
        return StructureSetupResult(
            direction=TrendlineDirection.BEAR,
            setup_type=TrendlineSetupType.DESCENDING_RESISTANCE,
            trendline=c.trendline,
            trigger_direction="breakout_up",
            expected_option_side="call",
            fit_mse=c.mse,
            alternate_fit_mse=alternate_mse,
            structure_bar_count=struct_n,
            selection_reason=c.selection_source,
            anchor_one_source=aos,
            anchor_two_source=ats,
        )
    return StructureSetupResult(
        direction=TrendlineDirection.BULL,
        setup_type=TrendlineSetupType.ASCENDING_SUPPORT,
        trendline=c.trendline,
        trigger_direction="breakdown_down",
        expected_option_side="put",
        fit_mse=c.mse,
        alternate_fit_mse=alternate_mse,
        structure_bar_count=struct_n,
        selection_reason=c.selection_source,
        anchor_one_source=aos,
        anchor_two_source=ats,
    )


def _collect_dual_side_watch_candidates(
    pool: List[GeometryCandidate],
    cfg: TrendlineConfig,
    *,
    oh: Optional[float],
    ol: Optional[float],
    cutoff_price: float,
    cutoff: datetime,
) -> Tuple[List[GeometryCandidate], str, float]:
    """
    Keep up to one relevance-passing intact envelope per direction (bull PUT + bear CALL).

    No mission side pin and no opposite-side fallback — live break / reject decides.
    """
    if not pool:
        return [], "no_valid_candidate", float("inf")

    finite = [c for c in pool if math.isfinite(c.mse)]
    if not finite:
        return [], "no_finite_mse", float("inf")

    best_rej = min(finite, key=lambda c: c.line_distance_pct)
    best_rej_dist = float(best_rej.line_distance_pct)

    by_dir: Dict[TrendlineDirection, GeometryCandidate] = {}
    for c in finite:
        if line_already_broken_at_cutoff(c.trendline, cutoff, cutoff_price):
            continue
        if not passes_line_relevance(
            c.line_distance_pct, cfg, orb_high=oh, orb_low=ol, cutoff_price=cutoff_price
        ):
            continue
        prev = by_dir.get(c.direction)
        if prev is None or (c.line_distance_pct, c.mse) < (prev.line_distance_pct, prev.mse):
            by_dir[c.direction] = c

    chosen = [
        by_dir[d]
        for d in (TrendlineDirection.BEAR, TrendlineDirection.BULL)
        if d in by_dir
    ]
    if not chosen:
        return [], "line_distance_exceeded", best_rej_dist
    return chosen, "dual_side_watch", best_rej_dist


def run_unified_geometry_contest(
    symbol: str,
    bars: List[OHLCVBar],
    orb_context: Dict[str, Any],
    config: TrendlineConfig,
    *,
    above_orb: bool,
    a1_policy: str = "tl1",
    cutoff_hour: int = 7,
    cutoff_minute: int = 30,
) -> List[StructureSetupResult]:
    """
    Phase 1 dual-side draw: build both A1→A2 envelopes and return every relevance-passing
    intact side (0–2). Mission side contest / pin is removed — no fallback winner.

    TL0: a1_policy=tl0, cutoff 06:45. TL1: a1_policy=tl1, cutoff 07:30.
    """
    cfg = config
    tz_pt = ZoneInfo("America/Los_Angeles")
    builder = TrendlineBuilder(cfg)
    policy = str(a1_policy or "tl1").strip().lower()
    if policy == "tl0":
        cutoff_hour, cutoff_minute = 6, 45
    cutoff = _session_cutoff_pt(
        bars[0].ts, tz_pt, hour=int(cutoff_hour), minute=int(cutoff_minute)
    )
    pre_cutoff = _pre_cutoff_bars(bars, cutoff)
    if len(pre_cutoff) < 2:
        return []

    structure_window = _structure_window_bars(
        pre_cutoff, tz_pt, end_hour=int(cutoff_hour), end_minute=int(cutoff_minute)
    )
    eval_pool = structure_window if len(structure_window) >= 2 else pre_cutoff
    struct_n = len(eval_pool)

    oh = _safe_float(orb_context.get("orb_high"))
    ol = _safe_float(orb_context.get("orb_low"))
    cutoff_price: Optional[float] = None
    try:
        cutoff_price = float(pre_cutoff[-1].close)
    except (TypeError, ValueError):
        pass
    if cutoff_price is None or cutoff_price <= 0:
        return []

    cls = builder.classify_orb_test_failure(symbol, pre_cutoff, orb_context)

    pool_map: Dict[Tuple[str, str, str], GeometryCandidate] = {}

    tl_env_bull: Optional[TrendlineDefinition] = None
    tl_env_bear: Optional[TrendlineDefinition] = None
    inside_orb = _cutoff_close_inside_orb_band(cutoff_price, oh, ol)

    ctx = dict(orb_context or {})
    if policy == "tl0":
        ctx.setdefault("envelope_cutoff_hm", (6, 45))
        ctx.setdefault("a1_policy", "tl0")
        ctx.setdefault("orb_box_source", "orb1_630_645")
        env_policy = "tl0"
        src_tag = "geometry_contest|tl0_structure_envelope"
    else:
        ctx.setdefault("a1_policy", "tl1")
        ctx.setdefault("orb_box_source", "orb2_630_730")
        env_policy = "tl1"
        src_tag = "geometry_contest|pre730_structure_envelope"

    # Dual-side envelope (same family as TL2/TL3).
    if bool(getattr(cfg, "pre730_structure_envelope_enabled", True)):
        env_pairs: List[Tuple[Optional[TrendlineDefinition], TrendlineDirection, str]] = [
            (
                builder.build_structure_envelope(
                    symbol,
                    TrendlineDirection.BULL,
                    pre_cutoff,
                    ctx,
                    a1_policy=env_policy,
                    allow_session_a1_fallback=above_orb,
                ),
                TrendlineDirection.BULL,
                src_tag,
            ),
            (
                builder.build_structure_envelope(
                    symbol,
                    TrendlineDirection.BEAR,
                    pre_cutoff,
                    ctx,
                    a1_policy=env_policy,
                    allow_session_a1_fallback=above_orb,
                ),
                TrendlineDirection.BEAR,
                src_tag,
            ),
        ]
        tl_env_bull = env_pairs[0][0]
        tl_env_bear = env_pairs[1][0]
        for tl, direction, src in env_pairs:
            if tl is None:
                continue
            eb = _eval_bars_for_line(tl, structure_window, pre_cutoff, cutoff)
            mse = _mse_for_direction(direction, tl, eb)
            if not math.isfinite(mse):
                continue
            dist = line_distance_at_cutoff(tl, cutoff, cutoff_price)
            _add_candidate(
                pool_map,
                GeometryCandidate(
                    trendline=tl,
                    direction=direction,
                    mse=mse,
                    selection_source=src,
                    line_distance_pct=dist,
                ),
            )

    control_override = False
    override_blocked = False
    pool = list(pool_map.values())

    if bool(getattr(cfg, "geometry_skip_trend_continuation", True)) and cls.failure_type == "trend_continuation":
        log.info(
            "TRENDLINE_PIPELINE | stage=setup_skipped | symbol=%s | reason=trend_continuation_orb_lane | "
            "detail=geometry_dual_side_skip",
            symbol,
        )
        _log_geometry_rejection_audit(
            symbol=symbol,
            reason="trend_continuation_orb_lane",
            line_distance_pct=float("inf"),
            candidate_geometry="none",
            support_mse=float("inf"),
            resistance_mse=float("inf"),
            above_orb=above_orb,
            best_rejected_distance_pct=float("inf"),
        )
        return []

    if not pool:
        _log_geometry_rejection_audit(
            symbol=symbol,
            reason="no_valid_candidate",
            line_distance_pct=float("inf"),
            candidate_geometry="none",
            support_mse=float("inf"),
            resistance_mse=float("inf"),
            above_orb=above_orb,
            best_rejected_distance_pct=float("inf"),
        )
        return []

    mse_bull = min((c.mse for c in pool if c.direction == TrendlineDirection.BULL), default=float("inf"))
    mse_bear = min((c.mse for c in pool if c.direction == TrendlineDirection.BEAR), default=float("inf"))

    winners, reject_reason, best_rej_dist = _collect_dual_side_watch_candidates(
        pool,
        cfg,
        oh=oh,
        ol=ol,
        cutoff_price=cutoff_price,
        cutoff=cutoff,
    )
    if not winners:
        _log_geometry_rejection_audit(
            symbol=symbol,
            reason=reject_reason,
            line_distance_pct=best_rej_dist,
            candidate_geometry="none",
            support_mse=mse_bull,
            resistance_mse=mse_bear,
            above_orb=above_orb,
            best_rejected_distance_pct=best_rej_dist,
        )
        return []

    from .trendline_setup_selector import (
        _build_structure_explainability,
        _log_trendline_draw_audit,
        _log_trendline_structure_comparison,
    )

    tl_bull_out = tl_env_bull
    tl_bear_out = tl_env_bear
    max_dist = float(getattr(cfg, "max_line_distance_at_cutoff_pct", 0.02) or 0.02)
    results: List[StructureSetupResult] = []

    for winner in winners:
        alt_mse = mse_bear if winner.direction == TrendlineDirection.BULL else mse_bull
        fit_mse = winner.mse
        mse_ratio = alt_mse / fit_mse if fit_mse > 0 and math.isfinite(alt_mse) else 1.0
        confidence = compute_geometry_confidence(
            fit_mse=fit_mse,
            alt_mse=alt_mse if math.isfinite(alt_mse) else fit_mse,
            line_distance_pct=winner.line_distance_pct,
            max_line_distance_pct=max_dist,
            touch_count=winner.touch_count,
            selection_source=winner.selection_source,
            control_line_override_blocked=override_blocked,
            mse_ratio_alt_to_primary=mse_ratio,
            inside_orb=inside_orb,
        )

        chosen = _candidate_to_setup_result(
            winner, struct_n, alternate_mse=alt_mse if math.isfinite(alt_mse) else None
        )
        disp, explain = _build_structure_explainability(
            symbol,
            chosen,
            cls,
            orb_context,
            cutoff,
            pre_cutoff,
            tl_bull_out,
            tl_bear_out,
            tl_bull_out is not None,
            tl_bear_out is not None,
            mse_bull if math.isfinite(mse_bull) else float("inf"),
            mse_bear if math.isfinite(mse_bear) else float("inf"),
            tz_pt,
        )
        explain = dict(explain)
        explain["geometry_confidence"] = confidence
        explain["geometry_phase1"] = True
        explain["geometry_dual_side_watch"] = True
        explain["dual_side_peer_count"] = len(winners)
        explain["orb_failure_type"] = cls.failure_type
        explain["line_distance_pct"] = winner.line_distance_pct
        explain["a1_policy"] = policy
        explain["trendline_slot"] = "trendline0" if policy == "tl0" else "trendline1"
        explain["orb_box_source"] = ctx.get("orb_box_source")
        legacy_line_at_cutoff: Optional[float] = None
        envelope_line_at_cutoff: Optional[float] = None
        env_ref = (
            tl_env_bull
            if winner.direction == TrendlineDirection.BULL
            else tl_env_bear
        )
        if env_ref is not None:
            try:
                envelope_line_at_cutoff = float(env_ref.value_at(cutoff))
            except Exception:
                envelope_line_at_cutoff = None
        selected_builder = "pre730_structure_envelope"
        anchor2_reason = ""
        if winner.trendline.metadata:
            anchor2_reason = str(
                winner.trendline.metadata.get("selected_anchor_2_reason")
                or winner.trendline.metadata.get("anchor_two_source")
                or ""
            )
        explain["legacy_line_at_cutoff"] = legacy_line_at_cutoff
        explain["cutoff_control_line_at_cutoff"] = None
        explain["envelope_line_at_cutoff"] = envelope_line_at_cutoff
        explain["cutoff_close"] = cutoff_price
        explain["line_price_gap_pct"] = winner.line_distance_pct
        explain["selected_builder"] = selected_builder
        explain["selected_anchor_2_reason"] = anchor2_reason
        chosen = replace(chosen, structure_display_label=disp, explainability=explain)

        _log_geometry_selection_audit(
            symbol=symbol,
            support_mse=mse_bull,
            resistance_mse=mse_bear,
            selected_geometry=winner.direction.value,
            selection_source=winner.selection_source,
            line_distance_pct=winner.line_distance_pct,
            geometry_confidence=confidence,
            control_line_override=control_override,
            control_line_override_blocked=override_blocked,
            above_orb=above_orb,
            mse_ratio_alt_to_primary=mse_ratio,
            candidates_considered=len(pool),
            alternate_geometry=(
                TrendlineDirection.BEAR.value
                if winner.direction == TrendlineDirection.BULL
                else TrendlineDirection.BULL.value
            ),
        )

        if _geometry_audit_logs_enabled():
            _log_trendline_structure_comparison(symbol, chosen, cls, explain)
            _log_trendline_draw_audit(
                symbol,
                tz_pt=tz_pt,
                cutoff=cutoff,
                pre_cutoff=pre_cutoff,
                orb_context=orb_context,
                cls=cls,
                chosen=chosen,
                tl_bull=tl_bull_out,
                tl_bear=tl_bear_out,
                mse_bull=mse_bull if math.isfinite(mse_bull) else float("inf"),
                mse_bear=mse_bear if math.isfinite(mse_bear) else float("inf"),
                builder=builder,
                explain=explain,
            )
        log.info(
            "TRENDLINE_PIPELINE | stage=setup_detected | symbol=%s | setup_type=%s | option_side=%s | "
            "selection_reason=%s | trendline_structure_source=geometry_dual_side_watch | "
            "geometry_confidence=%.2f | peer_count=%d",
            symbol,
            chosen.setup_type.value,
            chosen.expected_option_side,
            chosen.selection_reason,
            confidence,
            len(winners),
        )
        results.append(chosen)

    return results
