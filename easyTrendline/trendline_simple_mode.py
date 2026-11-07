#!/usr/bin/env python3
"""
Simplified Trendline 0DTE break → quality → emit path.

ARMED → BREAK_DETECTED → QUALITY_CONFIRMED → EMITTED → EXECUTED

Hard quality / reclaim / post-exec rejects expire that slot/side envelope
(EXPIRED). A fill marks EXECUTED. Opposite envelope and sibling slots stay live.
Soft window / capital wait still re-arm ARMED (WAITING_FOR_BREAK).
"""

from __future__ import annotations

import logging
import math
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from modules.trendline_telemetry import (
    compute_break_velocity_pct,
    derive_orb_zone,
    log_velocity_capture_audit,
)
from .trendline_intent_adapter import build_execution_intent_from_trendline_signal
from .trendline_models import (
    BreakStatus,
    MomentumStatus,
    OHLCVBar,
    TrendlineBreakEvent,
    TrendlineCandidate,
    TrendlineCandidateState,
    TrendlineConfig,
    TrendlineDirection,
    TrendlineMomentumConfirmation,
    TrendlineSetupType,
    TrendlineTradeSignal,
)

log = logging.getLogger(__name__)

PROMOTION_RATIO = 1.05

from .trendline_emit_gates import (
    apply_priority_emit_gates,
    bar_on_break_side,
    init_post_break_tracking,
    update_follow_through_bars,
)


def effective_min_break_distance_pct(
    candidate: TrendlineCandidate, config: TrendlineConfig
) -> float:
    """Single detect break-distance floor (Aug 12: index split collapsed)."""
    _ = candidate
    return float(config.simple_min_break_distance_pct)


class SimpleTrendlineState(str, Enum):
    ARMED = "armed"
    BREAK_DETECTED = "break_detected"
    QUALITY_CONFIRMED = "quality_confirmed"
    EMITTED = "emitted"
    EXECUTED = "executed"
    REJECTED = "rejected"
    EXPIRED = "expired"


VOLUME_GATE_ENFORCED = "enforced"
VOLUME_GATE_NEUTRAL_NO_DATA = "neutral_no_volume_data"


def _is_hard_simple_quality_reject(reasons: List[str]) -> bool:
    """Hard setup-quality gates reject this TL candidate; maturation gates can keep waiting."""
    hard_tokens = (
        "gate_stack:",
        "vwap",
        "volume",
        "rsi",
        "dir_volume",
        "directional_volume",
        "bb_anti",
        "bb_mid",
        "orb_zone",
        "entry_distance_extended",
        "reclaim_persisted",
    )
    soft_tokens = (
        "follow_through",
        "break_distance_below_min",
        "emit_break_distance_below_min",
        "material_break_score_below_min",
        "post_break_chop",
        "test_defer",
        "no_valid_break",
    )

    def _token_in_reason(reason: str, token: str) -> bool:
        # Short tokens (e.g. rsi) require non-alnum boundaries to avoid substring false hits.
        if len(token) <= 3:
            return (
                re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", reason) is not None
            )
        return token in reason

    normalized = [str(r or "").strip().lower() for r in reasons]
    if not normalized:
        return False
    return any(any(_token_in_reason(r, tok) for tok in hard_tokens) for r in normalized) and not all(
        any(_token_in_reason(r, tok) for tok in soft_tokens) for r in normalized
    )


@dataclass(frozen=True)
class VolumeRatioResolution:
    ratio: float
    gate_status: str


@dataclass
class SimpleBreakQualityResult:
    score: float
    passed: bool
    failed_reasons: List[str] = field(default_factory=list)
    vwap: float = 0.0
    vwap_position: str = ""
    vwap_distance_pct: Optional[float] = None
    rsi: Optional[float] = None
    volume_ratio: Optional[float] = None
    volume_gate_status: str = VOLUME_GATE_ENFORCED
    break_distance_pct: float = 0.0
    entry_distance_pct: float = 0.0
    seconds_from_material_break: float = 0.0
    orb_position: Optional[float] = None
    immediate_reclaim: bool = False
    rsi_missing: bool = False
    volume_ratio_missing: bool = False
    macd_histogram: Optional[float] = None
    ema_12: Optional[float] = None
    ema_26: Optional[float] = None
    macd_missing: bool = False
    bollinger_position: Optional[float] = None
    atr: Optional[float] = None


@dataclass
class SimpleBreakSnapshot:
    symbol: str
    setup_type: str
    direction: str
    line_price: float
    close_price: float
    break_distance_pct: float
    break_bar_time: datetime
    orb_position: Optional[float]
    vwap_position: str
    rsi: float
    volume_ratio: float
    promoted: bool = False


def _simple_payload(candidate: TrendlineCandidate) -> Dict[str, Any]:
    setup = candidate.setup_payload
    if setup is None:
        candidate.setup_payload = {}
        setup = candidate.setup_payload
    simple = setup.get("simple_mode")
    if not isinstance(simple, dict):
        simple = {}
        setup["simple_mode"] = simple
    return simple


def get_simple_state(candidate: TrendlineCandidate) -> SimpleTrendlineState:
    raw = str(_simple_payload(candidate).get("state") or SimpleTrendlineState.ARMED.value)
    try:
        return SimpleTrendlineState(raw)
    except ValueError:
        return SimpleTrendlineState.ARMED


def set_simple_state(candidate: TrendlineCandidate, state: SimpleTrendlineState, reason: str = "") -> None:
    sp = _simple_payload(candidate)
    sp["state"] = state.value
    if reason:
        sp["state_reason"] = reason
    _sync_legacy_state(candidate, state)


def candidate_expected_option_side(candidate: TrendlineCandidate) -> str:
    """Resolve call/put for cooldown keying from candidate / setup payload."""
    side = str(getattr(candidate, "option_side", "") or "").strip().lower()
    if side in {"call", "put"}:
        return side
    setup = getattr(candidate, "setup_payload", None) or {}
    if isinstance(setup, dict):
        side = str(
            setup.get("option_side")
            or setup.get("expected_option_side")
            or setup.get("side")
            or ""
        ).strip().lower()
        if side in {"call", "put"}:
            return side
    direction = str(getattr(getattr(candidate, "direction", None), "value", getattr(candidate, "direction", "")) or "").lower()
    if direction == "bear":
        return "call"
    if direction == "bull":
        return "put"
    return ""


def line_activate_draw_ts(candidate: TrendlineCandidate) -> Optional[datetime]:
    """UTC draw/inject time for the 3h trade-activate window (TL1–TL10)."""
    tl = getattr(candidate, "trendline", None)
    for raw in (
        getattr(tl, "built_at", None) if tl is not None else None,
        getattr(candidate, "qualified_at", None),
        getattr(candidate, "created_at", None),
        getattr(candidate, "start_time", None),
    ):
        if isinstance(raw, datetime):
            return raw if raw.tzinfo is not None else raw.replace(tzinfo=timezone.utc)
    return None


def line_activate_age_expired(
    candidate: TrendlineCandidate,
    now: datetime,
    *,
    max_age_sec: float,
) -> tuple[bool, float, Optional[datetime]]:
    """Return (expired, age_sec, draw_ts). max_age_sec<=0 disables the gate."""
    try:
        max_age = float(max_age_sec or 0.0)
    except (TypeError, ValueError):
        max_age = 0.0
    if max_age <= 0:
        return False, 0.0, None
    draw_ts = line_activate_draw_ts(candidate)
    if draw_ts is None:
        return False, 0.0, None
    now_u = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    age = (now_u - draw_ts).total_seconds()
    return age > max_age, age, draw_ts


def expire_simple_candidate_after_attempt(
    candidate: TrendlineCandidate,
    reason: str,
    *,
    clear_emitted_ids: Optional[set] = None,
) -> None:
    """Consume this slot/side envelope after one reject or fill.

    A1/A2/slope stay on the candidate for day-session plots. Opposite envelope
    and sibling slots are untouched. Soft window / fake-break still use
    ``rearm_simple_candidate_for_next_break``.
    """
    reason_s = str(reason or "attempt_consumed")[:180]
    sp = _simple_payload(candidate)
    sp["attempt_consumed"] = True
    sp["attempt_consumed_reason"] = reason_s
    if isinstance(candidate.setup_payload, dict):
        candidate.setup_payload["attempt_consumed"] = True
        candidate.setup_payload["attempt_consumed_reason"] = reason_s
    set_simple_state(candidate, SimpleTrendlineState.EXPIRED, reason_s)
    candidate.state_reason = reason_s
    candidate.updated_at = datetime.now(timezone.utc)
    cid = str(getattr(candidate, "candidate_id", "") or "")
    if cid and clear_emitted_ids is not None and hasattr(clear_emitted_ids, "discard"):
        try:
            clear_emitted_ids.discard(cid)
        except Exception:
            pass


def rearm_simple_candidate_for_next_break(
    candidate: TrendlineCandidate,
    reason: str,
    *,
    clear_emitted_ids: Optional[set] = None,
) -> None:
    """Drop a failed break attempt and return the line to WAITING_FOR_BREAK.

    Soft maturation / fake-break / capital wait only. Hard reject and fill
    expire the envelope via ``expire_simple_candidate_after_attempt``.
    """
    reason_s = str(reason or "rearm_wait_break")[:180]
    sp = _simple_payload(candidate)
    for key in (
        "material_break_at",
        "entry_window_at",
        "material_break_distance_pct",
        "reclaim_bars",
        "degraded",
        "exec_ready_at",
        "break_bar_time",
        "promoted_break_distance_pct",
    ):
        sp.pop(key, None)
    sp.pop("entry_watch_wait", None)
    sp["last_rearm_reason"] = reason_s
    set_simple_state(candidate, SimpleTrendlineState.ARMED, reason_s)
    candidate.state_reason = reason_s
    candidate.updated_at = datetime.now(timezone.utc)
    try:
        candidate.break_event = None
        candidate.has_broken_once = False
        candidate.break_attempt_count = 0
        candidate.break_bar_index = None
        candidate.break_timestamp = None
    except Exception:
        pass
    cid = str(getattr(candidate, "candidate_id", "") or "")
    if cid and clear_emitted_ids is not None and hasattr(clear_emitted_ids, "discard"):
        try:
            clear_emitted_ids.discard(cid)
        except Exception:
            pass


def _sync_legacy_state(candidate: TrendlineCandidate, simple: SimpleTrendlineState) -> None:
    mapping = {
        SimpleTrendlineState.ARMED: TrendlineCandidateState.WAITING_FOR_BREAK,
        SimpleTrendlineState.BREAK_DETECTED: TrendlineCandidateState.WAITING_FOR_CONFIRMATION,
        SimpleTrendlineState.QUALITY_CONFIRMED: TrendlineCandidateState.READY_TO_EXECUTE,
        SimpleTrendlineState.EMITTED: TrendlineCandidateState.READY_TO_EXECUTE,
        SimpleTrendlineState.EXECUTED: TrendlineCandidateState.EXECUTED,
        SimpleTrendlineState.REJECTED: TrendlineCandidateState.INVALIDATED,
        SimpleTrendlineState.EXPIRED: TrendlineCandidateState.EXPIRED,
    }
    candidate.state = mapping.get(simple, TrendlineCandidateState.WAITING_FOR_BREAK)


def compute_session_vwap(bars: List[OHLCVBar]) -> float:
    """Delegate to unified session-tech hub (same typical-price VWAP)."""
    from modules.session_tech_indicators import (
        ohlcv_bars_to_session_dicts,
        session_vwap_from_bars,
    )

    return float(session_vwap_from_bars(ohlcv_bars_to_session_dicts(bars)))


def compute_rsi(bars: List[OHLCVBar], period: int = 14) -> float:
    """Compute RSI, adapting the lookback for short early-session trendline windows."""
    from modules.session_tech_indicators import rsi_from_closes

    closes = [float(b.close) for b in bars if float(b.close or 0) > 0]
    adaptive_period = min(int(period), max(1, len(closes) - 1))
    return float(rsi_from_closes(closes, period=adaptive_period))


def _bar_volume_value(bar: OHLCVBar) -> float:
    try:
        vol = float(bar.volume or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(vol) or vol < 0:
        return 0.0
    return vol


def bars_lack_usable_volume(bars: List[OHLCVBar]) -> bool:
    """True when no bar in the list carries positive, finite volume."""
    if not bars:
        return True
    for b in bars:
        if _bar_volume_value(b) > 0:
            return False
    return True


def compute_volume_ratio(bars: List[OHLCVBar], lookback: int = 20) -> float:
    """Delegate to unified session-tech hub."""
    from modules.session_tech_indicators import (
        ohlcv_bars_to_session_dicts,
        volume_ratio_from_bars,
    )

    return float(volume_ratio_from_bars(ohlcv_bars_to_session_dicts(bars), lookback=lookback))


def resolve_volume_ratio_for_gate(
    bars: List[OHLCVBar],
    lookback: int = 20,
    *,
    demo_mode: Optional[bool] = None,
) -> VolumeRatioResolution:
    """
    Volume gate input for simple break quality.

    Quote-synthesized watch bars often carry volume=0. Treat that as neutral (pass)
    rather than a hard reject at ratio 0.000.
    """
    if demo_mode is None:
        try:
            from modules.runtime_mode import is_demo_mode

            demo_mode = is_demo_mode()
        except ImportError:
            demo_mode = os.getenv("MODE", "demo").lower() == "demo"
    if bars_lack_usable_volume(bars):
        return VolumeRatioResolution(ratio=1.0, gate_status=VOLUME_GATE_NEUTRAL_NO_DATA)
    ratio = compute_volume_ratio(bars, lookback=lookback)
    try:
        usable = [
            _bar_volume_value(b)
            for b in list(bars or [])[-lookback:]
            if _bar_volume_value(b) > 0
        ]
        if len(usable) >= 2:
            raw_ratio = usable[-1] / max(sum(usable[:-1]) / len(usable[:-1]), 1e-9)
            # Trendline has its own volume floor. If volume is measurable but tiny,
            # enforce the floor instead of neutralizing to 1.0 as "missing".
            if math.isfinite(raw_ratio) and 0.0 < raw_ratio < 0.01:
                ratio = raw_ratio
    except Exception:
        pass
    if ratio <= 0 or not math.isfinite(ratio):
        return VolumeRatioResolution(ratio=1.0, gate_status=VOLUME_GATE_NEUTRAL_NO_DATA)
    # Measurable volume (even micro-ratio) must honor GATE_TL_Vol_Ratio_MIN.
    # Zero-volume quote bars are already neutral via bars_lack_usable_volume above.
    return VolumeRatioResolution(ratio=ratio, gate_status=VOLUME_GATE_ENFORCED)


def orb_position_pct(close: float, orb_low: Optional[float], orb_high: Optional[float]) -> Optional[float]:
    try:
        lo = float(orb_low)
        hi = float(orb_high)
        if hi <= lo:
            return None
        return (close - lo) / (hi - lo)
    except (TypeError, ValueError):
        return None


def live_break_distance_pct(
    candidate: TrendlineCandidate, bar: OHLCVBar
) -> Tuple[float, float, bool]:
    """Return (line_price, break_distance_pct, is_break). Close-based — not wick."""
    if candidate.trendline is None:
        return 0.0, 0.0, False
    line_px = float(candidate.trendline.value_at(bar.ts))
    close_px = float(bar.close)
    if line_px <= 0:
        return line_px, 0.0, False
    if candidate.direction == TrendlineDirection.BEAR:
        dist = (close_px - line_px) / line_px
        return line_px, dist, close_px > line_px
    dist = (line_px - close_px) / line_px
    return line_px, dist, close_px < line_px


def body_beyond_line(candidate: TrendlineCandidate, bar: OHLCVBar) -> bool:
    """True when open or close is on the break side (not wick). Watch arm only."""
    if candidate.trendline is None:
        return False
    line_px = float(candidate.trendline.value_at(bar.ts))
    if line_px <= 0:
        return False
    open_px = float(bar.open)
    close_px = float(bar.close)
    if candidate.direction == TrendlineDirection.BEAR:
        return open_px > line_px or close_px > line_px
    return open_px < line_px or close_px < line_px


def compute_trendline_break_quality_score(
    candidate: TrendlineCandidate,
    bar: OHLCVBar,
    bars: List[OHLCVBar],
    config: TrendlineConfig,
    *,
    material_break_at: Optional[datetime] = None,
    session_tech: Optional[Dict[str, Any]] = None,
) -> SimpleBreakQualityResult:
    setup = candidate.setup_payload or {}
    line_px, break_dist, is_break = live_break_distance_pct(candidate, bar)
    close_px = float(bar.close)

    # Prefer unified session-tech stamp when provided; else compute via hub from bars.
    tech = session_tech
    if not isinstance(tech, dict):
        try:
            from modules.session_tech_indicators import (
                compute_session_tech_from_bars,
                ohlcv_bars_to_session_dicts,
            )

            tech = compute_session_tech_from_bars(
                ohlcv_bars_to_session_dicts(bars), price=close_px
            )
        except Exception:
            tech = None

    rsi_missing = bool(isinstance(tech, dict) and tech.get("rsi_missing"))
    vol_missing = bool(isinstance(tech, dict) and tech.get("volume_ratio_missing"))
    macd_missing = bool(isinstance(tech, dict) and tech.get("macd_missing"))
    macd_histogram = None
    ema_12 = None
    ema_26 = None
    bollinger_position = None
    atr = None
    if isinstance(tech, dict):
        try:
            if tech.get("bollinger_position") is not None:
                bollinger_position = float(tech["bollinger_position"])
        except (TypeError, ValueError):
            bollinger_position = None
        try:
            if tech.get("atr") is not None:
                atr = float(tech["atr"])
        except (TypeError, ValueError):
            atr = None
    if isinstance(tech, dict) and tech.get("vwap") is not None:
        vwap = float(tech["vwap"])
        if tech.get("macd_histogram") is not None and not macd_missing:
            macd_histogram = float(tech["macd_histogram"])
        else:
            macd_missing = True
        if tech.get("ema_12") is not None:
            ema_12 = float(tech["ema_12"])
        if tech.get("ema_26") is not None:
            ema_26 = float(tech["ema_26"])
        if tech.get("rsi") is not None and not rsi_missing:
            rsi = float(tech["rsi"])
        else:
            rsi = None
            rsi_missing = True
        if tech.get("volume_ratio_missing"):
            vol_res = VolumeRatioResolution(
                ratio=1.0, gate_status=VOLUME_GATE_NEUTRAL_NO_DATA
            )
            vol_missing = True
        else:
            vol_res = VolumeRatioResolution(
                ratio=float(tech.get("volume_ratio") or 1.0),
                gate_status=VOLUME_GATE_ENFORCED,
            )
            vol_missing = False
    else:
        vwap = compute_session_vwap(bars)
        try:
            from modules.session_tech_indicators import rsi_is_trusted

            closes = [float(b.close) for b in bars if float(b.close or 0) > 0]
            rsi_missing = not rsi_is_trusted(closes, 14)
        except Exception:
            rsi_missing = len(bars) < 15
        rsi = None if rsi_missing else float(compute_rsi(bars))
        vol_res = resolve_volume_ratio_for_gate(bars)
        vol_missing = vol_res.gate_status == VOLUME_GATE_NEUTRAL_NO_DATA
        macd_missing = True

    vol_ratio = vol_res.ratio
    vol_gate_status = vol_res.gate_status
    orb_pos = orb_position_pct(close_px, setup.get("orb_low"), setup.get("orb_high"))
    option_side = "call" if candidate.direction == TrendlineDirection.BEAR else "put"

    vwap_pos = "at"
    if close_px > vwap * 1.0001:
        vwap_pos = "above"
    elif close_px < vwap * 0.9999:
        vwap_pos = "below"
    vwap_dist_pct = ((close_px - vwap) / max(abs(vwap), 1e-9)) * 100.0

    immediate_reclaim = False
    if candidate.direction == TrendlineDirection.BEAR and close_px <= line_px:
        immediate_reclaim = True
    if candidate.direction == TrendlineDirection.BULL and close_px >= line_px:
        immediate_reclaim = True

    entry_dist = abs(close_px - line_px) / max(abs(line_px), 1e-9)
    now = bar.ts if bar.ts.tzinfo else bar.ts.replace(tzinfo=timezone.utc)
    mat = material_break_at
    if mat is None and candidate.break_event is not None:
        mat = getattr(candidate.break_event, "candle_ts", None)
    if mat is None:
        mat = now
    if mat.tzinfo is None:
        mat = mat.replace(tzinfo=timezone.utc)
    seconds_from = max(0.0, (now - mat).total_seconds())

    failed: List[str] = []
    score = 0.0

    min_break = effective_min_break_distance_pct(candidate, config)
    max_entry = float(config.simple_max_entry_distance_pct)

    if not is_break and break_dist < min_break:
        failed.append("no_valid_break")
    if break_dist < min_break:
        failed.append(f"break_distance_below_min:{break_dist:.6f}<{min_break:.6f}")
    else:
        score += min(35.0, (break_dist / max(min_break, 1e-9)) * 12.0)

    # Volume / VWAP / RSI: score scaffolding only — admit knives live on GATE_TL_* QG.
    if vol_gate_status == VOLUME_GATE_ENFORCED and not vol_missing:
        score += min(20.0, vol_ratio * 10.0)

    if option_side == "call":
        if vwap_pos != "below":
            score += 20.0
        else:
            score += 5.0
    else:
        if vwap_pos != "above":
            score += 20.0
        else:
            score += 5.0

    if not rsi_missing:
        if option_side == "call":
            score += min(15.0, max(0.0, rsi * 0.3))
        else:
            score += min(15.0, max(0.0, (100.0 - rsi) * 0.3))

    if not immediate_reclaim:
        score += 10.0

    # Entry distance: scaffolding bonus only (no chase hard-reject; QG owns anti-chase).
    if entry_dist <= max_entry:
        score += 5.0

    # Opening-bar defer / chaos / dual-lane / pre-broken chase knives removed (list B).
    # Admit for VWAP/vol/RSI/BB is GATE_TL_* via quality_gate_stack cutover.

    score = max(0.0, min(100.0, score))
    # Geometry failures only here; QG cutover owns VWAP/vol/RSI/BB admit.
    # Score remains scaffolding for logs / material_break_quality_score stamps.
    passed = not failed

    return SimpleBreakQualityResult(
        score=score,
        passed=passed,
        failed_reasons=failed,
        vwap=vwap,
        vwap_position=vwap_pos,
        vwap_distance_pct=vwap_dist_pct,
        rsi=None if rsi_missing else rsi,
        volume_ratio=None if vol_missing else vol_ratio,
        volume_gate_status=vol_gate_status,
        break_distance_pct=break_dist,
        entry_distance_pct=entry_dist,
        seconds_from_material_break=seconds_from,
        orb_position=orb_pos,
        immediate_reclaim=immediate_reclaim,
        rsi_missing=bool(rsi_missing),
        volume_ratio_missing=bool(vol_missing),
        macd_histogram=macd_histogram,
        ema_12=ema_12,
        ema_26=ema_26,
        macd_missing=bool(macd_missing),
        bollinger_position=bollinger_position,
        atr=atr,
    )


def build_break_event(
    candidate: TrendlineCandidate,
    bar: OHLCVBar,
    line_px: float,
    break_dist: float,
) -> TrendlineBreakEvent:
    return TrendlineBreakEvent(
        symbol=candidate.symbol,
        direction=candidate.direction,
        status=BreakStatus.DETECTED,
        candle_ts=bar.ts,
        close_price=float(bar.close),
        trendline_price=line_px,
        break_distance=abs(float(bar.close) - line_px),
        break_distance_pct=break_dist,
        threshold_used=float(break_dist),
        reason="simple_valid_close_break",
        metadata={"break_archetype": "SIMPLE_BREAK", "distance_increasing": True},
    )


def build_simple_trade_signal(
    candidate: TrendlineCandidate,
    quality: SimpleBreakQualityResult,
) -> TrendlineTradeSignal:
    if candidate.break_event is None or candidate.trendline is None:
        raise ValueError("simple emit requires break_event and trendline")
    setup = candidate.setup_payload or {}
    setup_type = str(setup.get("setup_type") or TrendlineSetupType.from_direction(candidate.direction).value)
    option_side = "call" if candidate.direction == TrendlineDirection.BEAR else "put"
    trigger_direction = "breakout_up" if option_side == "call" else "breakdown_down"
    momentum = TrendlineMomentumConfirmation(
        symbol=candidate.symbol,
        direction=candidate.direction,
        status=MomentumStatus.CONFIRMED,
        confirmed_at=datetime.now(timezone.utc),
        bars_used=1,
        velocity_pct=quality.break_distance_pct,
        range_expansion_ratio=quality.volume_ratio,
        pullback_ratio=0.0,
        reason="simple_quality_pass",
    )
    candidate.momentum_confirmation = momentum
    signal = TrendlineTradeSignal(
        symbol=candidate.symbol,
        direction=candidate.direction,
        emitted_at=datetime.now(timezone.utc),
        trendline=candidate.trendline,
        break_event=candidate.break_event,
        momentum_confirmation=momentum,
        confidence=min(1.0, quality.score / 100.0),
        priority_score=quality.score,
        option_side=option_side,
        metadata={
            "state_reason": "simple_quality_pass",
            "trade_id": str(candidate.candidate_id or ""),
            "setup_payload": setup,
            "candidate_id": candidate.candidate_id,
            "strategy_name": "easyTrendline_0DTE",
            "entry_type": "trendline_simple_break",
            "entry_mode": "simple_break",
            "setup_type": setup_type,
            "trigger_direction": trigger_direction,
            "expected_option_side": option_side,
            "trendline_slot": setup.get("trendline_slot") or "trendline1",
            "envelope_role": setup.get("envelope_role"),
            "simple_quality_score": quality.score,
            "priority_score": quality.score,
            "simple_failed_reasons": quality.failed_reasons,
            "rsi": quality.rsi,
            "volume_ratio": quality.volume_ratio,
            "vwap": quality.vwap,
            "vwap_position": quality.vwap_position,
            "vwap_distance_pct": quality.vwap_distance_pct,
            "entry_distance_pct": quality.entry_distance_pct,
            "break_distance_pct": quality.break_distance_pct,
            "bollinger_position": quality.bollinger_position,
            "seconds_since_break": quality.seconds_from_material_break,
            "seconds_from_material_break": quality.seconds_from_material_break,
            "close_at_break": (
                float(candidate.break_event.close_price)
                if candidate.break_event is not None
                else None
            ),
            "break_timestamp": (
                candidate.break_event.candle_ts
                if candidate.break_event is not None
                else None
            ),
            "material_break_at": (candidate.setup_payload or {}).get("material_break_at"),
        },
    )
    # Carry Layer A/B quality_gate_report from simple_mode stamp onto emit metadata
    try:
        qgr = (_simple_payload(candidate) or {}).get("quality_gate_report")
        if isinstance(qgr, dict):
            signal.metadata["quality_gate_report"] = qgr
    except Exception:
        pass
    try:
        from modules.qg_admit_snapshot import collect_qg_admit_snapshot

        snap = collect_qg_admit_snapshot(dict(signal.metadata or {}))
        if snap:
            signal.metadata["qg_admit"] = snap
            sp = candidate.setup_payload if isinstance(getattr(candidate, "setup_payload", None), dict) else {}
            sp["qg_admit"] = snap
            candidate.setup_payload = sp
    except Exception:
        pass
    intent = build_execution_intent_from_trendline_signal(signal)
    signal.metadata["execution_intent"] = intent
    return signal


def log_simple_break_detected(snapshot: SimpleBreakSnapshot, *, promoted: bool = False) -> None:
    tag = "TRENDLINE_SIMPLE_BREAK_PROMOTED" if promoted else "TRENDLINE_SIMPLE_BREAK_DETECTED"
    log.warning(
        "%s | symbol=%s | setup_type=%s | direction=%s | line_price=%.4f | close_price=%.4f | "
        "break_distance_pct=%.6f | break_bar_time=%s | orb_position=%s | vwap_position=%s | "
        "rsi=%.2f | volume_ratio=%.3f",
        tag,
        snapshot.symbol,
        snapshot.setup_type,
        snapshot.direction,
        snapshot.line_price,
        snapshot.close_price,
        snapshot.break_distance_pct,
        snapshot.break_bar_time.isoformat(),
        snapshot.orb_position,
        snapshot.vwap_position,
        snapshot.rsi,
        snapshot.volume_ratio,
    )


def log_simple_quality(candidate: TrendlineCandidate, quality: SimpleBreakQualityResult) -> None:
    rsi_display = "missing" if quality.rsi is None else f"{float(quality.rsi):.2f}"
    vol_display = "missing" if quality.volume_ratio is None else f"{float(quality.volume_ratio):.3f}"
    log.info(
        "TRENDLINE_SIMPLE_QUALITY_SCORE | symbol=%s | direction=%s | score=%.2f | passed=%s | "
        "failed_reasons=%s | vwap=%.4f | vwap_position=%s | rsi=%s | volume_ratio=%s | "
        "break_distance_pct=%.6f | entry_distance_pct=%.6f | seconds_from_material_break=%.1f",
        candidate.symbol,
        candidate.direction.value,
        quality.score,
        str(quality.passed).lower(),
        ",".join(quality.failed_reasons) or "none",
        quality.vwap,
        quality.vwap_position,
        rsi_display,
        vol_display,
        quality.break_distance_pct,
        quality.entry_distance_pct,
        quality.seconds_from_material_break,
    )


class SimpleTrendlineProcessor:
    """Single-path processor invoked from TrendlineSignalEngine."""

    def __init__(
        self,
        config: TrendlineConfig,
        *,
        on_dataset_capture: Optional[Callable[..., None]] = None,
        on_first_break_snapshot: Optional[Callable[..., None]] = None,
        trade_id_fn: Optional[Callable[[TrendlineCandidate], str]] = None,
        emitted_ids: Optional[set] = None,
        session_tech_system: Any = None,
    ) -> None:
        self.config = config
        self._on_dataset_capture = on_dataset_capture
        self._on_first_break_snapshot = on_first_break_snapshot
        self._trade_id_fn = trade_id_fn or (lambda c: f"{c.symbol}_simple")
        self._emitted_ids = emitted_ids if emitted_ids is not None else set()
        self._reclaim_reject_bars = 3
        self._session_tech_system = session_tech_system

    def bind_session_tech_system(self, system: Any) -> None:
        """Attach PrimeTradingSystem (or stub) so quality reads the shared hub stamp."""
        self._session_tech_system = system

    def _hub_session_tech(self, symbol: str, price: float) -> Optional[Dict[str, Any]]:
        system = self._session_tech_system
        if system is None:
            return None
        try:
            from modules.volume_data import request_equity_session_tech

            return request_equity_session_tech(system, symbol, price=price)
        except Exception:
            return None

    def _reject_rearm_with_side_cooldown(
        self,
        candidate: TrendlineCandidate,
        reason: str,
        *,
        arm_cooldown: bool = True,
    ) -> None:
        """Hard reject: expire this slot/side envelope (one attempt). Geometry stays."""
        reason_s = str(reason or "quality_reject")[:180]
        system = self._session_tech_system
        if arm_cooldown and system is not None:
            try:
                reject_fn = getattr(system, "_trendline_reject_current_exec_signal", None)
                if callable(reject_fn):
                    # Prefer the Prime helper (expire envelope + persist + emitted-id clear).
                    reject_fn(candidate, reason_s, arm_slot_side_cooldown=True)
                    return
            except Exception as exc:
                log.debug(
                    "TRENDLINE_PIPELINE | stage=reject_expire | symbol=%s | fallback=%s",
                    getattr(candidate, "symbol", ""),
                    exc,
                )
            try:
                from easyTrendline.trendline_models import trendline_slot_from_payload
                from modules.path_exec_day_exclude import mark_trendline_slot_side_cooldown

                side = candidate_expected_option_side(candidate)
                slot = trendline_slot_from_payload(getattr(candidate, "setup_payload", None) or {})
                if side:
                    mark_trendline_slot_side_cooldown(
                        system,
                        str(getattr(candidate, "symbol", "") or ""),
                        side,
                        slot=slot,
                        reason=reason_s,
                    )
            except Exception as arm_err:
                log.debug(
                    "TRENDLINE_PIPELINE | stage=slot_side_cooldown_arm | symbol=%s | error=%s",
                    getattr(candidate, "symbol", ""),
                    arm_err,
                )
        expire_simple_candidate_after_attempt(
            candidate,
            reason_s,
            clear_emitted_ids=self._emitted_ids,
        )
        log.warning(
            "TRENDLINE_PIPELINE | stage=reject_expire | symbol=%s | reason=%s | "
            "action=expire_envelope_keep_geometry",
            getattr(candidate, "symbol", ""),
            reason_s,
        )

    def advance(
        self,
        candidate: TrendlineCandidate,
        bar: OHLCVBar,
        bars: List[OHLCVBar],
        now: datetime,
    ) -> Optional[TrendlineTradeSignal]:
        sp = _simple_payload(candidate)
        state = get_simple_state(candidate)

        if state in {
            SimpleTrendlineState.EXECUTED,
            SimpleTrendlineState.REJECTED,
            SimpleTrendlineState.EXPIRED,
            SimpleTrendlineState.EMITTED,
        }:
            return None

        if state == SimpleTrendlineState.ARMED:
            set_simple_state(candidate, SimpleTrendlineState.ARMED, "selector_built")

        # TL1–TL10: expire ARMED lines after trade-activate max age from draw/inject (3h).
        # Breaks that already started (BREAK_DETECTED+) within the window may finish.
        # TL0 additionally hard-expires at 07:10 PT (discovery + geometry cutoff).
        try:
            from easyTrendline.trendline_models import trendline_slot_from_payload

            _slot = trendline_slot_from_payload(getattr(candidate, "setup_payload", None) or {})
        except Exception:
            _slot = ""
        if _slot == "trendline0":
            try:
                from zoneinfo import ZoneInfo

                now_pt = now.astimezone(ZoneInfo("America/Los_Angeles")).time() if now.tzinfo else now.time()
                from modules.session_clock import past_orb1_tl0_cutoff, orb1_tl0_window_pt, fmt_hhmm

                if past_orb1_tl0_cutoff(now_pt):
                    _, cutoff = orb1_tl0_window_pt()
                    set_simple_state(
                        candidate,
                        SimpleTrendlineState.EXPIRED,
                        "tl0_geometry_cutoff_0710",
                    )
                    candidate.state_reason = "tl0_geometry_cutoff_0710"
                    log.warning(
                        "TRENDLINE_PIPELINE | stage=expired | symbol=%s | reason=tl0_geometry_cutoff_0710 | "
                        "now_pt=%s | cutoff_pt=%s | action=no_tl0_after_0710",
                        candidate.symbol,
                        now_pt.strftime("%H:%M:%S"),
                        cutoff.strftime("%H:%M"),
                    )
                    self._capture(
                        candidate,
                        bar,
                        bars,
                        decision="expired",
                        reason="tl0_geometry_cutoff_0710",
                    )
                    return None
            except Exception:
                pass

        if state == SimpleTrendlineState.ARMED:
            max_age = float(getattr(self.config, "trade_activate_max_age_sec", 0.0) or 0.0)
            expired, age_sec, draw_ts = line_activate_age_expired(
                candidate, now, max_age_sec=max_age
            )
            if expired:
                set_simple_state(
                    candidate,
                    SimpleTrendlineState.EXPIRED,
                    "line_activate_max_age",
                )
                candidate.state_reason = "line_activate_max_age"
                log.warning(
                    "TRENDLINE_PIPELINE | stage=expired | symbol=%s | reason=line_activate_max_age | "
                    "age_sec=%.1f | max_age_sec=%.1f | draw_ts=%s | action=no_new_break_activate",
                    candidate.symbol,
                    age_sec,
                    max_age,
                    draw_ts.isoformat() if draw_ts else "",
                )
                self._capture(
                    candidate, bar, bars, decision="expired", reason="line_activate_max_age"
                )
                return None

        # 1h slot+side reject cooldown: keep the line, but do not re-detect / re-alert.
        try:
            from modules.path_exec_day_exclude import (
                is_trendline_candidate_on_slot_side_cooldown,
            )

            if is_trendline_candidate_on_slot_side_cooldown(
                self._session_tech_system, candidate
            ):
                return None
        except Exception:
            pass

        material_at = sp.get("material_break_at")
        material_dt: Optional[datetime] = None
        if material_at:
            material_dt = material_at if isinstance(material_at, datetime) else None

        line_px, break_dist, is_break = live_break_distance_pct(candidate, bar)
        min_break = effective_min_break_distance_pct(candidate, self.config)
        if body_beyond_line(candidate, bar):
            sp["body_beyond_watch"] = True

        # Entry-window clock (separate from material_break_at so Jun-15 defer/FT
        # clocks stay anchored to first material break). Promotion refreshes this.
        window_anchor = sp.get("entry_window_at") or material_at
        window_dt: Optional[datetime] = None
        if window_anchor:
            window_dt = window_anchor if isinstance(window_anchor, datetime) else None
            if window_dt is not None and window_dt.tzinfo is None:
                window_dt = window_dt.replace(tzinfo=timezone.utc)

        # A stronger break can promote and refresh entry_window_at — do not expire
        # before that promotion is applied.
        would_promote = False
        if is_break and break_dist >= min_break:
            prev_material = float(sp.get("material_break_distance_pct") or 0.0)
            would_promote = prev_material > 0 and break_dist > prev_material * PROMOTION_RATIO

        if (
            window_dt
            and state == SimpleTrendlineState.BREAK_DETECTED
            and not would_promote
        ):
            window = float(self.config.simple_entry_window_sec)
            elapsed = (now - window_dt).total_seconds()
            if elapsed > window:
                # Soft maturation miss: re-arm for a later break (do not kill the line).
                # No 1h cooldown — that is reserved for hard rejects.
                rearm_simple_candidate_for_next_break(
                    candidate,
                    "simple_entry_window_expired",
                    clear_emitted_ids=self._emitted_ids,
                )
                log.warning(
                    "TRENDLINE_PIPELINE | stage=expired | symbol=%s | reason=simple_entry_window_expired | "
                    "elapsed_sec=%.1f | window_sec=%.1f | action=rearm_wait_break",
                    candidate.symbol,
                    elapsed,
                    window,
                )
                self._capture(candidate, bar, bars, decision="expired", reason="entry_window_expired")
                return None

        # Exec-ready TTL: QUALITY_CONFIRMED may wait for slots/cash up to N minutes
        # (Shared 0DTE_EXEC_SIGNAL_EXPIRE_MINUTES). Distinct from break entry window.
        if state == SimpleTrendlineState.QUALITY_CONFIRMED:
            try:
                from modules.path_exec_ready_ttl import (
                    EXEC_READY_TTL_REASON,
                    exec_ready_age_sec,
                    exec_signal_expire_minutes,
                    is_exec_ready_expired,
                    log_exec_ready_ttl_expired,
                    stamp_exec_ready_at,
                )

                stamp_exec_ready_at(sp, now=now)
                expire_min = exec_signal_expire_minutes("trendline")
                # Causal-data holds keep the break until the strategy entry
                # window ends. They do not spend the capital exec-ready clock.
                if sp.get("data_readiness_hold") and window_dt is not None:
                    window = float(self.config.simple_entry_window_sec)
                    elapsed = (now - window_dt).total_seconds()
                    if window > 0 and elapsed > window:
                        self._capture(
                            candidate,
                            bar,
                            bars,
                            decision="expired",
                            reason="entry_window_expired",
                        )
                        rearm_simple_candidate_for_next_break(
                            candidate,
                            "simple_entry_window_expired",
                            clear_emitted_ids=self._emitted_ids,
                        )
                        return None
                elif is_exec_ready_expired(sp, path="trendline", now=now, expire_minutes=expire_min):
                    age = exec_ready_age_sec(sp, now=now)
                    log_exec_ready_ttl_expired(
                        path="trendline",
                        symbol=str(candidate.symbol or ""),
                        age_sec=age,
                        expire_minutes=expire_min,
                    )
                    self._capture(
                        candidate,
                        bar,
                        bars,
                        decision="expired",
                        reason=EXEC_READY_TTL_REASON,
                    )
                    from modules.path_exec_ready_ttl import ttl_retire_no_cooldown_enabled

                    if ttl_retire_no_cooldown_enabled():
                        # Oct 13 2026: retire this break, re-arm for the next one, no cooldown.
                        reject_fn = getattr(self._session_tech_system, "_trendline_reject_current_exec_signal", None)
                        if callable(reject_fn):
                            reject_fn(candidate, EXEC_READY_TTL_REASON)
                        else:
                            rearm_simple_candidate_for_next_break(
                                candidate,
                                "exec_ready_ttl_retired",
                                clear_emitted_ids=self._emitted_ids,
                            )
                        return None
                    self._reject_rearm_with_side_cooldown(
                        candidate,
                        EXEC_READY_TTL_REASON,
                        arm_cooldown=True,
                    )
                    return None
            except Exception as ttl_exc:
                log.debug("TRENDLINE exec_ready TTL check skipped: %s", ttl_exc)

        quality: Optional[SimpleBreakQualityResult] = None
        post_break_bar_handled = False

        if is_break and break_dist >= min_break:
            prev_material = float(sp.get("material_break_distance_pct") or 0.0)
            promoted = prev_material > 0 and break_dist > prev_material * PROMOTION_RATIO
            is_first_material = prev_material <= 0

            if is_first_material or promoted:
                if promoted:
                    # Restart entry window only — keep material_break_at for defer/FT (Jun 15).
                    sp["entry_window_at"] = bar.ts
                    log.warning(
                        "TRENDLINE_SIMPLE_BREAK_PROMOTED | symbol=%s | prev=%.6f | new=%.6f | "
                        "entry_window_reset=true",
                        candidate.symbol,
                        prev_material,
                        break_dist,
                    )
                else:
                    sp["material_break_at"] = bar.ts
                    sp["entry_window_at"] = bar.ts
                candidate.break_event = build_break_event(candidate, bar, line_px, break_dist)
                candidate.has_broken_once = True
                candidate.first_break_at = candidate.first_break_at or bar.ts
                sp["material_break_distance_pct"] = break_dist
                sp["reclaim_bars"] = 0
                sp["degraded"] = False
                material_at = sp.get("material_break_at") or bar.ts
                material_dt = material_at if isinstance(material_at, datetime) else bar.ts
                if material_dt.tzinfo is None:
                    material_dt = material_dt.replace(tzinfo=timezone.utc)
                set_simple_state(candidate, SimpleTrendlineState.BREAK_DETECTED, "simple_break_detected")
                candidate.state_reason = "simple_break_detected"

                vwap = compute_session_vwap(bars)
                vwap_pos = "above" if float(bar.close) > vwap else ("below" if float(bar.close) < vwap else "at")
                setup = candidate.setup_payload or {}
                snap = SimpleBreakSnapshot(
                    symbol=candidate.symbol,
                    setup_type=str(setup.get("setup_type") or ""),
                    direction=candidate.direction.value,
                    line_price=line_px,
                    close_price=float(bar.close),
                    break_distance_pct=break_dist,
                    break_bar_time=bar.ts,
                    orb_position=orb_position_pct(float(bar.close), setup.get("orb_low"), setup.get("orb_high")),
                    vwap_position=vwap_pos,
                    rsi=compute_rsi(bars),
                    volume_ratio=compute_volume_ratio(bars),
                    promoted=promoted,
                )
                log_simple_break_detected(snap, promoted=promoted)
                break_quality = compute_trendline_break_quality_score(
                    candidate,
                    bar,
                    bars,
                    self.config,
                    material_break_at=material_dt,
                    session_tech=self._hub_session_tech(
                        str(candidate.symbol or ""), float(bar.close)
                    ),
                )
                velocity_pct = compute_break_velocity_pct(bar, bars, candidate.direction)
                log_velocity_capture_audit(candidate.symbol, velocity_pct)
                setup_orb = candidate.setup_payload or {}
                orb_pct_fb = getattr(break_quality, "orb_position", None)
                if orb_pct_fb is None:
                    orb_pct_fb = orb_position_pct(
                        float(bar.close),
                        setup_orb.get("orb_low"),
                        setup_orb.get("orb_high"),
                    )
                orb_zone = derive_orb_zone(
                    close=float(bar.close),
                    orb_low=setup_orb.get("orb_low"),
                    orb_high=setup_orb.get("orb_high"),
                    orb_pct_from_low=orb_pct_fb,
                )
                self._capture(
                    candidate,
                    bar,
                    bars,
                    decision="break_detected" if not promoted else "break_promoted",
                    reason="initial_break" if is_first_material else "promoted_break",
                    quality=break_quality,
                    velocity_pct=velocity_pct,
                    orb_zone=orb_zone,
                )
                if is_first_material:
                    self._maybe_first_break_snapshot(
                        candidate,
                        bar,
                        break_dist,
                        quality=break_quality,
                        velocity_pct=velocity_pct,
                        orb_zone=orb_zone,
                    )
                    init_post_break_tracking(sp, bar, quality_score=break_quality.score)
                    sp["follow_through_bars"] = 1 if bar_on_break_side(candidate, bar) else 0
                else:
                    prev_mat_score = float(sp.get("material_break_quality_score") or 0.0)
                    sp["material_break_quality_score"] = max(
                        prev_mat_score, float(break_quality.score)
                    )
                    if sp.get("material_break_close") is not None:
                        update_follow_through_bars(sp, candidate, bar)
                    else:
                        init_post_break_tracking(sp, bar, quality_score=break_quality.score)
                        sp["follow_through_bars"] = 1 if bar_on_break_side(candidate, bar) else 0
                quality = break_quality
                post_break_bar_handled = True

        if quality is None:
            quality = compute_trendline_break_quality_score(
                candidate,
                bar,
                bars,
                self.config,
                material_break_at=material_dt,
                session_tech=self._hub_session_tech(
                    str(candidate.symbol or ""), float(bar.close)
                ),
            )

        if (
            sp.get("material_break_close") is not None
            and quality is not None
            and not post_break_bar_handled
        ):
            prev_mat_score = float(sp.get("material_break_quality_score") or 0.0)
            if float(quality.score) > prev_mat_score:
                sp["material_break_quality_score"] = float(quality.score)

        if get_simple_state(candidate) in {
            SimpleTrendlineState.BREAK_DETECTED,
            SimpleTrendlineState.QUALITY_CONFIRMED,
        } and sp.get("material_break_close") is not None:
            if not post_break_bar_handled:
                update_follow_through_bars(sp, candidate, bar)
            quality = apply_priority_emit_gates(candidate, quality, self.config)
        log_simple_quality(candidate, quality)

        # Quality Gate Stack Layer A/B — cutover when QUALITY_GATE_STACK_CUTOVER
        try:
            from easy0DTE.modules.quality_gate_stack import (
                PATH_TL,
                GateContext,
                apply_equity_geom_cutover,
                shadow_equity_geom_report,
            )

            option_side_u = (
                "CALL" if candidate.direction == TrendlineDirection.BEAR else "PUT"
            )
            reasons = list(quality.failed_reasons or [])
            geom_ok = not any(
                r.startswith("no_valid_break") or r.startswith("break_distance_below_min")
                or r.startswith("emit_break_distance_below_min")
                or "geometry_side" in r
                for r in reasons
            )
            geom_ready = bool(geom_ok)
            bb_pos = (
                float(quality.bollinger_position)
                if getattr(quality, "bollinger_position", None) is not None
                else None
            )
            if bb_pos is None:
                try:
                    hub = self._hub_session_tech(
                        str(candidate.symbol or ""), float(bar.close)
                    )
                    if isinstance(hub, dict) and hub.get("bollinger_position") is not None:
                        bb_pos = float(hub["bollinger_position"])
                except Exception:
                    bb_pos = None
            try:
                from easyTrendline.trendline_models import trendline_slot_from_payload

                tl_slot = trendline_slot_from_payload(
                    getattr(candidate, "setup_payload", None) or {}
                )
            except Exception:
                tl_slot = str(
                    (_simple_payload(candidate) or {}).get("trendline_slot") or ""
                )
            # Vol floor is shared GATE_TL_Vol_Ratio_MIN (QG default 0.012).
            ctx_ab = GateContext(
                path=PATH_TL,
                symbol=str(candidate.symbol or "").upper(),
                side=option_side_u,
                signal=dict(sp) if isinstance(sp, dict) else {},
                equity_volume_ratio=(
                    None if quality.volume_ratio is None else float(quality.volume_ratio)
                ),
                equity_volume_ratio_min=None,
                vwap_position=str(quality.vwap_position or "") or None,
                vwap_distance_pct=(
                    None
                    if quality.vwap_distance_pct is None
                    else float(quality.vwap_distance_pct)
                ),
                rsi=None if quality.rsi is None else float(quality.rsi),
                macd_histogram=(
                    None if quality.macd_histogram is None else float(quality.macd_histogram)
                ),
                ema_12=None if quality.ema_12 is None else float(quality.ema_12),
                ema_26=None if quality.ema_26 is None else float(quality.ema_26),
                quality_score=float(quality.score),
                quality_failed_reasons=[],
                geom_ready=geom_ready,
                geom_reason=(
                    "tl_break_ok"
                    if geom_ready
                    else (reasons[0] if reasons else "tl_quality_reject")
                ),
                followthrough_ok=True,
                followthrough_reason="emit_ok",
                extras={
                    "volume_ratio_missing": bool(quality.volume_ratio_missing),
                    "rsi_missing": bool(quality.rsi_missing),
                    "macd_missing": bool(quality.macd_missing),
                    "bollinger_position": bb_pos,
                    "atr": getattr(quality, "atr", None),
                    "trendline_slot": tl_slot or None,
                    "session_vs_avg_volume": (
                        (sp.get("session_vs_avg_volume") if isinstance(sp, dict) else None)
                        or (sp.get("session_vs_avg") if isinstance(sp, dict) else None)
                    ),
                    "implied_volatility": (
                        (sp.get("implied_volatility") if isinstance(sp, dict) else None)
                        or (sp.get("iv") if isinstance(sp, dict) else None)
                    ),
                    "iv": (sp.get("iv") if isinstance(sp, dict) else None),
                    "seconds_since_break": (
                        float(quality.seconds_from_material_break)
                        if quality.seconds_from_material_break is not None
                        else (
                            sp.get("seconds_since_break") if isinstance(sp, dict) else None
                        )
                    ),
                    "l1_delta_5s_with": (
                        sp.get("l1_delta_5s_with") if isinstance(sp, dict) else None
                    ),
                    "break_timestamp": (
                        (sp.get("break_timestamp") if isinstance(sp, dict) else None)
                        or (sp.get("material_break_at") if isinstance(sp, dict) else None)
                        or (
                            candidate.break_event.candle_ts
                            if getattr(candidate, "break_event", None) is not None
                            else None
                        )
                    ),
                    "material_break_at": (
                        (sp.get("material_break_at") if isinstance(sp, dict) else None)
                    ),
                },
            )
            # Refresh break age at QG evaluate (do not keep same-bar stale 0.0).
            try:
                from modules.session_tech_admit import refresh_seconds_since_break

                _sec_row = dict(ctx_ab.extras or {})
                refresh_seconds_since_break(_sec_row)
                if _sec_row.get("seconds_since_break") is not None:
                    ctx_ab.extras["seconds_since_break"] = float(
                        _sec_row["seconds_since_break"]
                    )
            except Exception:
                pass
            if bb_pos is None or quality.rsi is None or not quality.vwap_position:
                log.info(
                    "TL_QG_FIELD_GAP | symbol=%s | side=%s | bb=%s | rsi=%s | vwap_pos=%s | "
                    "grep=TL_QG_FIELD_GAP",
                    candidate.symbol,
                    option_side_u,
                    bb_pos,
                    quality.rsi,
                    quality.vwap_position,
                )
            stamp = _simple_payload(candidate)
            ab_report = shadow_equity_geom_report(
                PATH_TL, ctx_ab, legacy_admitted=bool(quality.passed), stamp_target=stamp
            )
            cut_pass, cut_reasons = apply_equity_geom_cutover(
                ab_report,
                legacy_passed=bool(quality.passed),
                failed_reasons=list(quality.failed_reasons or []),
            )
            if cut_pass != bool(quality.passed):
                quality.passed = cut_pass
                quality.failed_reasons = cut_reasons
        except Exception as _qgs_exc:
            log.debug("GATE_STACK TL quality shadow skipped: %s", _qgs_exc)

        if quality.immediate_reclaim:
            # Track reclaim bars for diagnostics only — list B reclaim_persisted hard reject removed.
            reclaim_bars = int(sp.get("reclaim_bars") or 0) + 1
            sp["reclaim_bars"] = reclaim_bars
            sp["degraded"] = True
        elif sp.get("degraded") and quality.passed:
            sp["degraded"] = False
            sp["reclaim_bars"] = 0

        if not quality.passed:
            if get_simple_state(candidate) == SimpleTrendlineState.BREAK_DETECTED:
                reason = ",".join(quality.failed_reasons)
                if _is_hard_simple_quality_reject(list(quality.failed_reasons or [])):
                    self._capture(candidate, bar, bars, decision="reject", reason=reason, quality=quality)
                    self._reject_rearm_with_side_cooldown(
                        candidate,
                        f"quality_reject:{reason[:160]}",
                        arm_cooldown=True,
                    )
                else:
                    self._capture(candidate, bar, bars, decision="skip", reason=reason, quality=quality)
            return None

        set_simple_state(candidate, SimpleTrendlineState.QUALITY_CONFIRMED, "simple_quality_pass")
        candidate.state_reason = "simple_quality_pass"
        try:
            from modules.path_exec_ready_ttl import stamp_exec_ready_at

            stamp_exec_ready_at(sp, now=now)
        except Exception:
            pass

        if candidate.candidate_id and candidate.candidate_id in self._emitted_ids:
            return None

        signal = build_simple_trade_signal(candidate, quality)
        if self._trade_id_fn:
            try:
                signal.metadata["break_trade_id"] = self._trade_id_fn(candidate)
            except Exception:
                pass
        set_simple_state(candidate, SimpleTrendlineState.EMITTED, "simple_signal_emit")
        candidate.state_reason = "simple_signal_emit"
        if candidate.candidate_id:
            self._emitted_ids.add(candidate.candidate_id)

        log.warning(
            "TRENDLINE_PIPELINE | stage=signal_emit | symbol=%s | path=simple_break_mode | "
            "option_side=%s | score=%.2f",
            candidate.symbol,
            signal.option_side,
            quality.score,
        )
        log.warning(
            "TRENDLINE_SIMPLE_SIGNAL_EMITTED | symbol=%s | option_side=%s | score=%.2f | "
            "break_distance_pct=%.6f | entry_distance_pct=%.6f",
            candidate.symbol,
            signal.option_side,
            quality.score,
            quality.break_distance_pct,
            quality.entry_distance_pct,
        )
        self._capture(candidate, bar, bars, decision="execute", reason="quality_pass", quality=quality, signal=signal)
        return signal

    def _capture(
        self,
        candidate: TrendlineCandidate,
        bar: OHLCVBar,
        bars: List[OHLCVBar],
        *,
        decision: str,
        reason: str,
        quality: Optional[SimpleBreakQualityResult] = None,
        signal: Optional[TrendlineTradeSignal] = None,
        velocity_pct: Optional[float] = None,
        orb_zone: Optional[str] = None,
    ) -> None:
        if not self._on_dataset_capture:
            return
        if orb_zone is None or velocity_pct is None:
            setup = candidate.setup_payload or {}
            orb_pos = (
                quality.orb_position
                if quality and quality.orb_position is not None
                else orb_position_pct(float(bar.close), setup.get("orb_low"), setup.get("orb_high"))
            )
            if orb_zone is None:
                orb_zone = derive_orb_zone(
                    close=float(bar.close),
                    orb_low=setup.get("orb_low"),
                    orb_high=setup.get("orb_high"),
                    orb_pct_from_low=orb_pos,
                )
            if velocity_pct is None:
                velocity_pct = compute_break_velocity_pct(bar, bars, candidate.direction)
        try:
            self._on_dataset_capture(
                candidate=candidate,
                bar=bar,
                bars=bars,
                trade_id=self._trade_id_fn(candidate),
                decision=decision,
                decision_reason=reason,
                quality=quality,
                signal=signal,
                velocity_pct=velocity_pct,
                orb_zone=orb_zone,
            )
            log.info(
                "TRENDLINE_PRIORITY_DATASET_CAPTURED | symbol=%s | decision=%s | reason=%s",
                candidate.symbol,
                decision,
                reason,
            )
        except Exception as exc:
            log.warning(
                "TRENDLINE_PRIORITY_DATASET_CAPTURED | symbol=%s | action=failed | reason=%s",
                candidate.symbol,
                exc,
            )

    def _maybe_first_break_snapshot(
        self,
        candidate: TrendlineCandidate,
        bar: OHLCVBar,
        break_dist: float,
        *,
        quality: Optional[SimpleBreakQualityResult] = None,
        velocity_pct: Optional[float] = None,
        orb_zone: Optional[str] = None,
    ) -> None:
        if not self._on_first_break_snapshot or candidate.break_event is None:
            return
        body = abs(float(bar.close) - float(bar.open))
        rng = float(bar.high) - float(bar.low)
        body_ratio = body / max(rng, 1e-9) if rng > 0 else 0.0
        close_pos: Optional[float] = None
        if rng > 0:
            close_pos = (float(bar.close) - float(bar.low)) / rng
        try:
            self._on_first_break_snapshot(
                candidate=candidate,
                break_event=candidate.break_event,
                break_bar=bar,
                trade_id=self._trade_id_fn(candidate),
                body_ratio_now=body_ratio,
                expected_move_pct=break_dist,
                velocity=float(velocity_pct if velocity_pct is not None else 0.0),
                close_position_in_candle=close_pos,
                rsi=quality.rsi if quality else None,
                vwap_position=quality.vwap_position if quality else None,
                volume_ratio=quality.volume_ratio if quality else None,
                vwap=quality.vwap if quality else None,
                entry_distance_pct=quality.entry_distance_pct if quality else None,
                score=quality.score if quality else None,
                orb_zone=orb_zone,
            )
        except Exception as exc:
            log.warning(
                "TRENDLINE_BREAK_SNAPSHOT | symbol=%s | action=hook_failed | reason=%s",
                candidate.symbol,
                exc,
            )
