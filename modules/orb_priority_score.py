"""
Shared ORB priority scoring.

- ORB SO stock: ``calculate_so_priority_score`` / PUT invert ``calculate_so_put_priority_score``
- ORB1 Breakout options (default): ``calculate_0dte_priority_score_v2`` — early-continuation ranker
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from modules.config_loader import get_config_value

log = logging.getLogger(__name__)


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _truthy_flag(row: Dict[str, Any], key: str) -> bool:
    md = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return bool(row.get(key) or md.get(key))


def _invert_signal_for_put_ranking(signal: Dict[str, Any]) -> Dict[str, Any]:
    """Map SHORT/PUT tape into SO scorer-friendly bullish-shaped inputs."""
    s = dict(signal)
    vd = _to_float(s.get("vwap_distance_pct"), 0.0)
    rs = _to_float(s.get("rs_vs_spy"), 0.0)
    s["vwap_distance_pct"] = -vd
    s["rs_vs_spy"] = -rs
    for key in ("momentum", "momentum_10", "momentum_5", "momentum_prev"):
        if s.get(key) is not None:
            s[key] = -_to_float(s.get(key), 0.0)
    if s.get("macd_histogram") is not None:
        s["macd_histogram"] = -_to_float(s.get("macd_histogram"), 0.0)
    orb_low = _to_float(s.get("orb_low"), 0.0)
    entry = _to_float(s.get("price", s.get("entry_price", s.get("current_price", 0.0))), 0.0)
    if orb_low > 0 and entry > 0:
        s["breakout_extension_pct"] = max(0.0, ((orb_low - entry) / orb_low) * 100.0)
    s["_0dte_put_ranking"] = True
    return s


def calculate_so_priority_score(signal: Dict[str, Any]) -> float:
    """
    ORB SO priority — **continuation quality first** (Rev 00348, May 2026).

    Philosophy (differs from legacy v2.1 “biggest VWAP / strongest tape”):
    - **Rank durable continuation into 7:30**, not largest ORB, farthest extension,
      raw confidence, or blow-off VWAP distance.
    - **Primary signal**: blended continuation score (momentum slope / acceleration /
      live MACD histogram when present).
    - **Participation**: ORB volume ratio (joint with continuation — not solo).
    - **Context**: RS vs SPY and RSI remain **secondary** tilts, not solo rankers.
    - **VWAP distance**: **capped / plateaued** — moderate separation above VWAP scores
      best; extreme positive distance is **not** treated as automatic quality.
    - **Confidence**: small base weight only; **never** boosted alone (see multiplier).

    Tunables: ``SO_CONTINUATION_MOMENTUM_WEIGHT`` = base blend weight for continuation
    quality (typical 0.28–0.36). Penalty knobs unchanged below.
    """
    symbol = signal.get('symbol', '')
    
    
    # Configurable refinement knobs (soft adjustments only).
    # SO_CONTINUATION_MOMENTUM_WEIGHT = share of **base** score from continuation quality
    # (momentum slope / accel / MACD). Remainder = VWAP/RS/volume/confidence/RSI/orb_range context.
    w_cont = _to_float(get_config_value("SO_CONTINUATION_MOMENTUM_WEIGHT", 0.36), 0.36)
    w_cont = max(0.18, min(0.42, w_cont))
    w_rem = max(0.58, 1.0 - w_cont)
    exhaustion_weight = max(0.0, _to_float(get_config_value("SO_EXHAUSTION_PENALTY_WEIGHT", 0.08), 0.08))
    max_extension_soft_penalty = max(0.0, _to_float(get_config_value("SO_MAX_EXTENSION_SOFT_PENALTY", 0.12), 0.12))
    orb_range_soft_penalty_weight = max(0.0, _to_float(get_config_value("SO_ORB_RANGE_SOFT_PENALTY", 0.05), 0.05))
    momentum_decel_penalty_weight = max(0.0, _to_float(get_config_value("SO_MOMENTUM_DECELERATION_PENALTY", 0.06), 0.06))
    entry_below_vwap_stack_penalty = max(
        0.0, _to_float(get_config_value("SO_ENTRY_BELOW_VWAP_STACK_PENALTY", 0.14), 0.14)
    )
    entry_vwap_chase_penalty = max(
        0.0, _to_float(get_config_value("SO_ENTRY_VWAP_CHASE_PENALTY", 0.18), 0.18)
    )
    entry_weak_cont_penalty = max(
        0.0, _to_float(get_config_value("SO_ENTRY_WEAK_CONTINUATION_PENALTY", 0.12), 0.12)
    )
    vwap_chase_pct = _to_float(get_config_value("SO_ENTRY_VWAP_CHASE_PCT", 35.0), 35.0)
    vwap_extreme_pct = _to_float(get_config_value("SO_ENTRY_MAX_VWAP_EXTENSION_PCT", 50.0), 50.0)
    below_vwap_pct = _to_float(get_config_value("SO_ENTRY_BELOW_VWAP_PCT", -8.0), -8.0)
    
    # RS vs SPY — secondary tape context (not a proxy for “how much already moved”).
    rs_vs_spy = signal.get('rs_vs_spy', 0)
    if rs_vs_spy is None:
        rs_vs_spy = 0.0
    else:
        try:
            rs_vs_spy = float(rs_vs_spy)
        except (TypeError, ValueError):
            rs_vs_spy = 0.0
    
    if rs_vs_spy >= 20.0:
        rs_score = 0.92
    elif rs_vs_spy >= 10.0:
        rs_score = 0.82
    elif rs_vs_spy >= 5.0:
        rs_score = 0.72
    elif rs_vs_spy >= 0.0:
        rs_score = 0.55
    elif rs_vs_spy >= -10.0:
        rs_score = 0.38
    else:
        rs_score = 0.22
    
    # VWAP distance — continuation-shaped (plateau / taper; not monotonic “farther = better”).
    vwap_distance = signal.get('vwap_distance_pct', 0)
    if vwap_distance is None:
        vwap_distance = 0.0
    else:
        try:
            vwap_distance = float(vwap_distance)
        except (TypeError, ValueError):
            vwap_distance = 0.0
    
    def _vwap_continuation_score(vd: float) -> float:
        if vd <= -10.0:
            return 0.12
        if vd < 0.0:
            return 0.22 + 0.05 * ((vd + 10.0) / 10.0)
        if vd <= 10.0:
            return 0.48 + 0.047 * vd
        if vd <= 22.0:
            return 0.95 - 0.020 * (vd - 10.0)
        if vd <= vwap_chase_pct:
            return max(0.45, 0.71 - 0.015 * (vd - 22.0))
        if vd <= vwap_extreme_pct:
            return max(0.28, 0.45 - 0.006 * (vd - vwap_chase_pct))
        return 0.22
    
    vwap_score = _vwap_continuation_score(vwap_distance)
    
    # Factor 3: ORB Volume Ratio (20%) - Rev 00104: Reduced from 40%
    # Still important but less than VWAP/RS discoveries
    volume_ratio_missing = _truthy_flag(signal, "volume_ratio_missing")
    orb_volume_ratio = signal.get('volume_ratio', 1.0)
    if orb_volume_ratio is None:
        volume_ratio_missing = True
        orb_volume_ratio = 1.0
    else:
        try:
            orb_volume_ratio = float(orb_volume_ratio)
        except (TypeError, ValueError):
            volume_ratio_missing = True
            orb_volume_ratio = 1.0
    if orb_volume_ratio <= 0.0 or orb_volume_ratio < 0.01:
        volume_ratio_missing = True
        orb_volume_ratio = 1.0
    
    if orb_volume_ratio >= 3.0:
        orb_vol_score = 1.0      # Exceptional
    elif orb_volume_ratio >= 2.0:
        orb_vol_score = 0.85     # Strong
    elif orb_volume_ratio >= 1.5:
        orb_vol_score = 0.70     # Good
    elif orb_volume_ratio >= 1.2:
        orb_vol_score = 0.50     # Moderate
    else:
        orb_vol_score = 0.25     # Weak
    if volume_ratio_missing:
        orb_vol_score = 0.25
    
    # Factor 4: Confidence (15%) - Rev 00104: RE-ADDED ⭐⭐
    # Nov 5 discovery: 95% confidence = top 2 performers
    # Was removed (Rev 00091) due to -0.298 correlation on holiday days
    # Re-added based on positive correlation on normal trading days
    confidence = signal.get('confidence', 0.5)
    if confidence is None:
        confidence = 0.5
    else:
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = 0.5
    
    # Scale 0.5-1.0 → 0-1.0
    if confidence >= 0.5:
        conf_score = (confidence - 0.5) / 0.5
        conf_score = min(1.0, conf_score)
    else:
        conf_score = 0.0
    
    # Factor 5: RSI Context-Aware (10%) - Rev 00104: REVISED ⭐⭐
    # Nov 5 discovery: High RSI (70-75) = top performers in BULL markets
    # Nov 1 data: High RSI (>65) = losers in weak markets
    # Solution: Context-aware scoring based on market regime
    rsi_missing = _truthy_flag(signal, "rsi_missing")
    rsi = signal.get('rsi', 55.0)
    if rsi is None:
        rsi_missing = True
        rsi = 55.0
    else:
        try:
            rsi = float(rsi)
        except (TypeError, ValueError):
            rsi_missing = True
            rsi = 55.0
    market_regime = signal.get('market_regime', 'MIXED')  # Will be added to signals
    
    if rsi_missing:
        rsi_score = 0.50
    elif market_regime == 'BULL':
        if 55 <= rsi < 68:
            rsi_score = 1.0
        elif 68 <= rsi <= 76:
            rsi_score = 0.78
        elif 50 <= rsi < 55:
            rsi_score = 0.88
        elif rsi > 76:
            rsi_score = 0.55
        elif 45 <= rsi < 50:
            rsi_score = 0.72
        else:
            rsi_score = 0.42
    else:
        if 50 <= rsi <= 62:
            rsi_score = 1.0
        elif 45 <= rsi < 50:
            rsi_score = 0.82
        elif 62 < rsi <= 68:
            rsi_score = 0.62
        elif rsi > 68:
            rsi_score = 0.32
        elif 40 <= rsi < 45:
            rsi_score = 0.72
        else:
            rsi_score = 0.58
    
    orb_high = signal.get('orb_high', 0)
    orb_low = signal.get('orb_low', 0)
    orb_range_pct = float(signal.get('orb_range_pct') or 0)
    if orb_range_pct <= 0 and orb_low > 0:
        from modules.prime_orb_strategy_manager import compute_orb_range_pct
        orb_range_pct = compute_orb_range_pct(orb_high, orb_low)
    orb_range_score = min(orb_range_pct / 6.0, 1.0)
    
    entry_price = _to_float(signal.get('price', signal.get('entry_price', 0.0)), 0.0)
    if signal.get("_0dte_put_ranking") and signal.get("breakout_extension_pct") is not None:
        breakout_extension_pct = max(0.0, _to_float(signal.get("breakout_extension_pct"), 0.0))
    else:
        breakout_extension_pct = 0.0
        if orb_high > 0 and entry_price > 0:
            breakout_extension_pct = max(0.0, ((entry_price - orb_high) / orb_high) * 100.0)
    signal['breakout_extension_pct'] = breakout_extension_pct
    
    _mom_raw = signal.get('momentum')
    if _mom_raw is None:
        _mom_raw = signal.get('momentum_10')
    momentum_available = _mom_raw is not None
    momentum_now = _to_float(_mom_raw, 0.0) if momentum_available else 0.0
    momentum_5 = _to_float(signal.get('momentum_5', momentum_now), momentum_now)
    momentum_prev = _to_float(signal.get('momentum_prev', momentum_5), momentum_5)
    if momentum_available:
        directional_consistency = 1.0 if (momentum_now >= 0 and momentum_5 >= 0) else 0.35
        slope_strength = max(-1.0, min(1.0, momentum_now * 50.0))
        acceleration_delta = max(-1.0, min(1.0, (momentum_now - momentum_5) * 80.0))
    else:
        # Missing momentum_10: do not treat 0.0 as “both legs positive” (inflates CIFR-class ranks).
        directional_consistency = 0.35
        slope_strength = 0.0
        acceleration_delta = 0.0
    macd_missing = _truthy_flag(signal, "macd_missing") or signal.get("macd_histogram") is None
    mh = None if macd_missing else _to_float(signal.get('macd_histogram'), 0.0)
    if macd_missing:
        macd_q = 0.22
    elif mh > 0.08:
        macd_q = min(1.0, 0.48 + 0.52 * min(1.0, mh / 7.0))
    elif mh > 0.0:
        macd_q = 0.40 + 0.45 * (mh / 0.08)
    elif mh > -0.6:
        macd_q = 0.22 + 0.28 * max(0.0, (mh + 0.6) / 0.6)
    else:
        macd_q = 0.12
    continuation_quality_score = (
        0.20 * directional_consistency
        + 0.28 * ((slope_strength + 1.0) / 2.0)
        + 0.22 * ((acceleration_delta + 1.0) / 2.0)
        + 0.30 * macd_q
    )
    continuation_quality_score = max(0.0, min(1.0, continuation_quality_score))
    
    # Distance/extension soft penalty to reduce chasing.
    extension_soft_threshold_pct = 0.35
    extension_excess_pct = max(0.0, breakout_extension_pct - extension_soft_threshold_pct)
    distance_from_orb_penalty = min(
        max_extension_soft_penalty,
        extension_excess_pct * 0.04
    )
    
    # Momentum deceleration + exhaustion penalties (soft only).
    momentum_decelerating = (momentum_now < momentum_5) or (momentum_5 < momentum_prev)
    large_orb_range = orb_range_pct >= 2.0
    large_extension = breakout_extension_pct >= 0.8
    exhaustion_penalty = 0.0
    if large_orb_range and large_extension and momentum_decelerating:
        severity = min(1.0, ((orb_range_pct - 2.0) / 4.0) + (extension_excess_pct / 2.0))
        exhaustion_penalty = exhaustion_weight * max(0.0, severity)
    
    deceleration_penalty = 0.0
    if breakout_extension_pct >= extension_soft_threshold_pct and momentum_now < momentum_5:
        deceleration_penalty = momentum_decel_penalty_weight * min(
            1.0,
            max(0.0, (momentum_5 - momentum_now) * 100.0)
        )
    
    vw_frac, rs_frac, vol_frac, conf_frac, rsi_frac, orb_frac = 0.24, 0.18, 0.28, 0.10, 0.14, 0.06
    base_priority_score = w_cont * continuation_quality_score + w_rem * (
        vwap_score * vw_frac
        + rs_score * rs_frac
        + orb_vol_score * vol_frac
        + conf_score * conf_frac
        + rsi_score * rsi_frac
        + orb_range_score * orb_frac
    )
    
    quality_multiplier = 1.0
    if (not volume_ratio_missing) and orb_volume_ratio >= 2.0:
        quality_multiplier *= 1.08 if continuation_quality_score >= 0.55 else 1.0
    elif (not volume_ratio_missing) and orb_volume_ratio >= 1.5:
        quality_multiplier *= 1.05 if continuation_quality_score >= 0.52 else 1.0
    elif (not volume_ratio_missing) and orb_volume_ratio > 0.65 and continuation_quality_score < 0.50:
        quality_multiplier *= 0.82
    if confidence >= 0.93 and continuation_quality_score >= 0.50:
        quality_multiplier *= 1.02
    elif confidence >= 0.90 and continuation_quality_score < 0.45:
        quality_multiplier *= 0.82
    elif confidence < 0.80:
        quality_multiplier *= 0.84
    # Rev 00352: high confidence without tight ORB or with chase break (May 22 AEHR/QCOM/MSTZ)
    if confidence >= 0.90 and orb_range_pct > _to_float(
        get_config_value("SO_TIER_A_MAX_ORB_RANGE_PCT", 4.0), 4.0
    ):
        quality_multiplier *= _to_float(
            get_config_value("SO_HIGH_CONF_WIDE_ORB_MULT", 0.80), 0.80
        )
    if confidence >= 0.90 and breakout_extension_pct >= _to_float(
        get_config_value("SO_TIER_B_CHASE_BREAK_PCT", 4.0), 4.0
    ):
        quality_multiplier *= _to_float(
            get_config_value("SO_HIGH_CONF_CHASE_BREAK_MULT", 0.78), 0.78
        )
    
    if not rsi_missing and market_regime != 'BULL' and rsi > 66:
        quality_multiplier *= 0.86
    
    if vwap_distance < 0:
        quality_multiplier *= 0.88
    
    if (not volume_ratio_missing) and orb_range_pct >= 6.0 and orb_volume_ratio < 1.0:
        quality_multiplier *= 0.85
    
    if (not volume_ratio_missing) and orb_volume_ratio < 0.80 and continuation_quality_score < 0.48:
        quality_multiplier *= 0.78
    if (not volume_ratio_missing) and orb_volume_ratio < 0.60 and continuation_quality_score < 0.45:
        quality_multiplier *= 0.65
    
    orb_range_soft_penalty = (
        orb_range_soft_penalty_weight * min(1.0, max(0.0, (orb_range_pct - 3.0) / 4.0))
        if orb_range_pct > 3.0 else 0.0
    )
    if orb_range_pct >= 5.0 and continuation_quality_score < 0.45:
        orb_range_soft_penalty += orb_range_soft_penalty_weight * 1.25
    extension_score = max(0.0, 1.0 - distance_from_orb_penalty)

    stack_penalty = 0.0
    if vwap_distance < below_vwap_pct and (
        (mh is None or mh <= 0.0) or continuation_quality_score < 0.48
    ):
        stack_penalty += entry_below_vwap_stack_penalty
    if vwap_distance > vwap_chase_pct:
        stack_penalty += entry_vwap_chase_penalty * min(
            1.0, (vwap_distance - vwap_chase_pct) / max(vwap_extreme_pct - vwap_chase_pct, 1.0)
        )
    if continuation_quality_score < 0.42:
        stack_penalty += entry_weak_cont_penalty
    
    priority_score = (base_priority_score * quality_multiplier)
    priority_score -= (
        exhaustion_penalty
        + distance_from_orb_penalty
        + orb_range_soft_penalty
        + deceleration_penalty
        + stack_penalty
    )
    # Rev 00352: Tier-A geometry boost / Tier-B deprioritize (May 22 desk cohort)
    try:
        from modules.orb_priority_geometry import (
            geometry_breakout_pct,
            is_inverse_sleeve_signal,
            is_tier_a_geometry,
            is_tier_b_geometry,
        )

        if is_tier_a_geometry(signal):
            priority_score += _to_float(
                get_config_value("SO_TIER_A_GEOMETRY_BOOST", 0.06), 0.06
            )
            signal["tier_a_geometry"] = True
        elif is_tier_b_geometry(signal):
            priority_score -= _to_float(
                get_config_value("SO_TIER_B_GEOMETRY_PENALTY", 0.10), 0.10
            )
            signal["tier_b_geometry"] = True
        inv_brk_cap = _to_float(
            get_config_value("SO_INVERSE_BREAK_CHASE_PCT", 3.0), 3.0
        )
        if is_inverse_sleeve_signal(signal) and geometry_breakout_pct(signal) >= inv_brk_cap:
            priority_score -= _to_float(
                get_config_value("SO_INVERSE_SLEEVE_PENALTY", 0.12), 0.12
            )
            signal["inverse_sleeve_penalty"] = True
    except Exception as geom_rank_err:
        log.debug("SO geometry rank adjustment skipped: %s", geom_rank_err)
    priority_score = max(0.0, priority_score)
    
    signal['priority_score'] = priority_score
    signal['priority_base_score'] = base_priority_score
    signal['priority_quality_multiplier'] = quality_multiplier
    signal['continuation_quality_score'] = continuation_quality_score
    signal['distance_from_orb_penalty'] = distance_from_orb_penalty
    signal['exhaustion_penalty'] = exhaustion_penalty
    signal['deceleration_penalty'] = deceleration_penalty
    signal['orb_range_soft_penalty'] = orb_range_soft_penalty
    signal['entry_stack_penalty'] = stack_penalty
    signal['rs_vs_spy'] = rs_vs_spy
    signal['vwap_distance_pct'] = vwap_distance
    signal['volume_ratio'] = orb_volume_ratio
    signal['orb_volume_ratio'] = orb_volume_ratio
    signal['volume_ratio_missing'] = bool(volume_ratio_missing)
    signal['rsi'] = None if rsi_missing else rsi
    signal['rsi_missing'] = bool(rsi_missing)
    signal['macd_histogram'] = None if macd_missing else mh
    signal['macd_missing'] = bool(macd_missing)
    signal['orb_range_pct'] = orb_range_pct

    # Telemetry only (Rev 00348+): contrast continuation vs chase / confidence stack — no rank impact.
    # extension_penalty = ORB chase soft penalty (see exhaustion/deceleration/orb_range in SO_RANK_BREAKDOWN).
    signal['continuation_vs_extension_bias'] = {
        'continuation_quality_score': float(continuation_quality_score),
        'vwap_score': float(vwap_score),
        'macd_histogram': None if mh is None else float(mh),
        'extension_penalty': float(distance_from_orb_penalty),
        'quality_multiplier': float(quality_multiplier),
        'final_priority_score': float(priority_score),
    }
    
    log.info(
        "SO_RANK_BREAKDOWN | symbol=%s | orb_range_score=%.4f | continuation_quality=%.4f | "
        "conf_score=%.4f | extension_soft=%.4f | vwap_continuation=%.4f | orb_vol=%.4f | "
        "exhaustion_penalty=%.4f | deceleration_penalty=%.4f | distance_from_orb_penalty=%.4f | "
        "orb_range_soft_penalty=%.4f | final_score=%.4f",
        symbol,
        orb_range_score,
        continuation_quality_score,
        conf_score,
        extension_score,
        vwap_score,
        orb_vol_score,
        exhaustion_penalty,
        deceleration_penalty,
        distance_from_orb_penalty,
        orb_range_soft_penalty,
        priority_score,
    )
    log.info(
        "SO_CONTINUATION_VS_EXTENSION_BIAS | symbol=%s | %s",
        symbol,
        __import__("json").dumps(
            signal["continuation_vs_extension_bias"], separators=(",", ":")
        ),
    )
    
    return priority_score

def calculate_so_put_priority_score(signal: Dict[str, Any]) -> float:
    """SO-style continuation rank for ORB1 Breakout PUT / SHORT paths."""
    put_signal = _invert_signal_for_put_ranking(signal)
    score = calculate_so_priority_score(put_signal)
    put_signal["so_put_priority_score"] = score
    signal["so_put_priority_score"] = score
    return score


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _aligned_dir_pct(signal: Dict[str, Any], side: str) -> float:
    """Side-aligned chain volume share (CALL=call share, PUT=put share)."""
    if signal.get("aligned_dir") is not None:
        return _to_float(signal.get("aligned_dir"), 0.60)
    cs = signal.get("direction_volume_pct")
    if cs is None:
        cs = signal.get("call_share")
    if cs is None:
        cv = signal.get("call_chain_volume")
        pv = signal.get("put_chain_volume")
        try:
            cv_f = float(cv) if cv is not None else None
            pv_f = float(pv) if pv is not None else None
            if cv_f is not None and pv_f is not None and (cv_f + pv_f) > 0:
                cs = cv_f / (cv_f + pv_f)
        except (TypeError, ValueError):
            cs = None
    call_share = _to_float(cs, 0.60)
    if side == "CALL":
        return call_share
    return 1.0 - call_share


def calculate_0dte_priority_score_v2(signal: Dict[str, Any]) -> float:
    """ORB1 Breakout early-continuation Priority Score (PS v2.1).

    Path-specific ranker for options @ 7:30 — does **not** replace ORB SO stock PS.
    Multi-day replay (Aug 5–6 · Jun 12/26): lifts opportunity PnL vs SO-mirror while
    preserving GLD/IBIT deadband quality and demoting Aug 5 soft-neg fade / SBUX-class.

    Blend: dir% · cool RSI · MACD regime · VWAP · vol · ORB geometry.
    """
    side_raw = str(signal.get("side") or signal.get("direction") or signal.get("option_type") or "CALL").upper()
    if side_raw in ("LONG", "C", "CALL"):
        side = "CALL"
    else:
        side = "PUT"

    ad = _aligned_dir_pct(signal, side)
    vol = _to_float(signal.get("volume_ratio", signal.get("orb_volume_ratio")), 0.5)
    orb = _to_float(signal.get("orb_range_pct"), 2.0)
    vwap = _to_float(signal.get("vwap_distance_pct"), 0.0)
    macd_missing = _truthy_flag(signal, "macd_missing") or signal.get("macd_histogram") is None
    macd = 0.0 if macd_missing else _to_float(signal.get("macd_histogram"), 0.0)
    rsi_missing = _truthy_flag(signal, "rsi_missing") or signal.get("rsi") is None
    rsi = 50.0 if rsi_missing else _to_float(signal.get("rsi", signal.get("rsi_14")), 50.0)
    conf = _to_float(signal.get("confidence"), 0.75)
    am = abs(macd)

    if ad >= 0.75:
        dir_q = 0.82 + 0.18 * _clamp01((ad - 0.75) / 0.20)
    elif ad >= 0.65:
        dir_q = 0.65 + 0.17 * ((ad - 0.65) / 0.10)
    elif ad > 0.54:
        dir_q = 0.40 + 0.25 * ((ad - 0.54) / 0.11)
    else:
        dir_q = 0.15

    if side == "CALL":
        if 32 <= rsi <= 50:
            rsi_q = 1.00
        elif 50 < rsi <= 55:
            rsi_q = 0.72
        elif 28 <= rsi < 32:
            rsi_q = 0.88
        elif 55 < rsi <= 62:
            rsi_q = 0.32
        elif 62 < rsi <= 75:
            rsi_q = 0.40
        else:
            rsi_q = 0.55 if rsi > 75 else 0.70
    else:
        if rsi <= 42:
            rsi_q = 0.90
        elif rsi <= 50:
            rsi_q = 0.65
        elif rsi <= 58:
            rsi_q = 0.35
        else:
            rsi_q = 0.22

    if side == "CALL":
        # Soft-neg allowed but not a free boost — Aug 5 SLV/GE fades vs GLD/IBIT.
        if macd_missing:
            macd_q = 0.40
        elif macd >= 0.05:
            macd_q = 0.80
        elif macd >= 0.0:
            macd_q = 0.45
        elif macd >= -0.50:
            if orb <= 1.2 and (vol < 0.25 or am >= 0.20):
                macd_q = 0.88  # GLD/IBIT deadband-quality
            elif orb <= 1.0:
                macd_q = 0.70
            else:
                macd_q = 0.28
        elif macd >= -3.0:
            macd_q = 0.78  # MU/BE/CRDO soft-neg continuation
        else:
            macd_q = 0.55
        if am < 0.08 and 54 <= rsi <= 65:
            macd_q *= 0.40
    else:
        if macd_missing:
            macd_q = 0.40
        elif macd <= -0.1:
            macd_q = 0.90
        elif macd <= 0:
            macd_q = 0.45
        else:
            macd_q = 0.20
        if am < 0.08 and 50 <= rsi <= 62:
            macd_q *= 0.35

    if side == "CALL":
        if 0.5 <= vwap <= 4.0:
            vwap_q = 0.95
        elif 0 <= vwap < 0.5:
            vwap_q = 0.75
        elif 4 < vwap <= 8:
            vwap_q = 0.65
        elif -8 <= vwap < 0:
            vwap_q = 0.82  # below-VWAP preserve (MELI/KTOS class)
        else:
            vwap_q = 0.35
    else:
        if -4 <= vwap <= -0.3:
            vwap_q = 0.95
        elif -8 <= vwap < -4:
            vwap_q = 0.70
        else:
            vwap_q = 0.40

    if vol >= 1.4:
        vol_q = 1.00
    elif vol >= 0.8:
        vol_q = 0.85
    elif vol >= 0.5:
        vol_q = 0.70
    else:
        vol_q = 0.50

    if 0.8 <= orb <= 5.0:
        orb_q = 0.90
    elif 5.0 < orb < 8.0:
        orb_q = 0.75 if (rsi <= 50 and macd < -0.2) else 0.30
    elif 0.25 <= orb < 0.8:
        orb_q = 0.85
    elif orb < 0.25:
        orb_q = 0.40
    else:
        orb_q = 0.15

    base = (
        0.26 * dir_q
        + 0.24 * rsi_q
        + 0.20 * macd_q
        + 0.12 * vwap_q
        + 0.10 * vol_q
        + 0.08 * orb_q
    )
    base *= 0.94 + 0.06 * _clamp01((conf - 0.70) / 0.20)

    if 54 <= rsi <= 65 and am < 0.10 and ad < 0.78:
        base *= 0.50  # SBUX/CSCO
    if orb >= 5.0 and rsi >= 55:
        base *= 0.55
    if side == "CALL" and -0.50 <= macd < 0 and orb > 1.2 and vol >= 0.25:
        base *= 0.55  # Aug 5 non-tight soft-neg noise
    if side == "CALL" and orb <= 1.2 and -0.50 <= macd <= 0 and (vol < 0.25 or am >= 0.20):
        base = max(base, 0.78)  # GLD/IBIT floor
    if side == "CALL" and rsi >= 75 and vol >= 1.2 and ad >= 0.75:
        base = max(base, 0.72)  # CRCL hot+vol rescue
    if side == "PUT" and macd >= 0 and rsi >= 50:
        base *= 0.45

    score = _clamp01(base)
    signal["priority_score"] = score
    signal["0dte_ps_v2"] = score
    signal["0dte_ps_v2_side"] = side
    signal["0dte_ps_v2_aligned_dir"] = ad
    log.info(
        "0DTE_PS_V2 | symbol=%s | side=%s | score=%.4f | dir=%.3f | rsi=%.1f | macd=%.3f | "
        "vwap=%.2f | vol=%.2f | orb=%.2f",
        signal.get("symbol"),
        side,
        score,
        ad,
        rsi,
        macd,
        vwap,
        vol,
        orb,
    )
    return score
