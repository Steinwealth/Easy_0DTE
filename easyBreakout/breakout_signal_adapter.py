#!/usr/bin/env python3
"""Adapt BreakoutTradeSignal → TrendlineTradeSignal for shared lotto executor."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from easyTrendline.trendline_models import (
    BreakStatus,
    MomentumStatus,
    TrendlineAnchor,
    TrendlineBreakEvent,
    TrendlineDefinition,
    TrendlineDirection,
    TrendlineMomentumConfirmation,
    TrendlineTradeSignal,
)

from .breakout_models import BreakoutCandidate, BreakoutTradeSignal


def build_breakout_trade_signal(
    cand: BreakoutCandidate,
    *,
    price: float,
    confirm_break_distance_pct: float,
    confirm_continuation_pct: float,
    confirm_follow_through: int,
    quality_score: float,
    extra_meta: Optional[Dict[str, Any]] = None,
) -> BreakoutTradeSignal:
    side = str(cand.option_side or "").lower()
    now = datetime.now(timezone.utc)
    meta: Dict[str, Any] = {
        "setup_type": "orb_range_breakout",
        "trigger_direction": "above_orb_high" if side == "call" else "below_orb_low",
        "orb_high": float(cand.orb_high),
        "orb_low": float(cand.orb_low),
        "source_path": "breakout_0dte",
        "strategy_type": "breakout_0dte",
        "entry_underlying_price": float(price),
        "candidate_id": f"breakout_{cand.symbol}_{now.strftime('%H%M%S')}",
        "execution_reason": "executed_valid_confirmation",
    }
    if isinstance(cand.metadata, dict):
        meta.update({k: v for k, v in cand.metadata.items() if v is not None})
    if isinstance(extra_meta, dict):
        meta.update(extra_meta)
    return BreakoutTradeSignal(
        symbol=str(cand.symbol).upper(),
        option_side=side,
        emitted_at=now,
        orb_high=float(cand.orb_high),
        orb_low=float(cand.orb_low),
        break_price=float(cand.break_price or price),
        entry_underlying_price=float(price),
        confidence=min(0.99, max(0.55, float(quality_score) / 100.0)),
        priority_score=float(quality_score),
        quality_score=float(quality_score),
        break_distance_pct=float(confirm_break_distance_pct),
        continuation_pct=float(confirm_continuation_pct),
        follow_through_bars=int(confirm_follow_through),
        metadata=meta,
    )


def to_trendline_compatible_signal(signal: BreakoutTradeSignal) -> TrendlineTradeSignal:
    """
    Build a TrendlineTradeSignal the shared OptionsExecutor can consume.

    Geometry mapping (matches Trendline convention):
      CALL (break above ORB high) → BEAR resistance break
      PUT  (break below ORB low)  → BULL support break
    """
    side = str(signal.option_side or "").lower()
    direction = TrendlineDirection.BEAR if side == "call" else TrendlineDirection.BULL
    level = float(signal.orb_high if side == "call" else signal.orb_low)
    ts = signal.emitted_at if signal.emitted_at.tzinfo else signal.emitted_at.replace(tzinfo=timezone.utc)
    a1 = TrendlineAnchor(ts=ts, price=level, source="orb_level")
    a2 = TrendlineAnchor(ts=ts, price=level, source="orb_level")
    # Flat line at ORB high/low (slope 0): intercept = level
    tl = TrendlineDefinition(
        symbol=signal.symbol,
        direction=direction,
        anchor_one=a1,
        anchor_two=a2,
        slope_per_second=0.0,
        intercept=level,
        built_at=ts,
        metadata={"orb_breakout_level": level, "source": "breakout_0dte"},
    )
    bd_pct = float(signal.break_distance_pct)
    break_event = TrendlineBreakEvent(
        symbol=signal.symbol,
        direction=direction,
        status=BreakStatus.DETECTED,
        candle_ts=ts,
        close_price=float(signal.entry_underlying_price),
        trendline_price=level,
        break_distance=bd_pct * level,
        break_distance_pct=bd_pct,
        threshold_used=bd_pct,
        reason="orb_range_breakout",
        metadata={
            "break_distance_pct": signal.break_distance_pct,
            "continuation_pct": signal.continuation_pct,
            "follow_through_bars": signal.follow_through_bars,
        },
    )
    momentum = TrendlineMomentumConfirmation(
        symbol=signal.symbol,
        direction=direction,
        status=MomentumStatus.CONFIRMED,
        confirmed_at=ts,
        bars_used=int(signal.follow_through_bars),
        velocity_pct=float(signal.continuation_pct),
        range_expansion_ratio=1.0,
        pullback_ratio=0.0,
        reason="breakout_continuation_confirmed",
    )
    meta = dict(signal.metadata or {})
    meta["source_path"] = "breakout_0dte"
    meta["strategy_type"] = "breakout_0dte"
    meta["setup_type"] = meta.get("setup_type") or "orb_range_breakout"
    return TrendlineTradeSignal(
        symbol=signal.symbol,
        direction=direction,
        emitted_at=ts,
        trendline=tl,
        break_event=break_event,
        momentum_confirmation=momentum,
        confidence=float(signal.confidence),
        priority_score=float(signal.priority_score),
        option_side=side,
        metadata=meta,
    )
