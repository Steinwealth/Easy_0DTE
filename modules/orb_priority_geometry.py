"""
Shared ORB geometry helpers for SO ranking, adaptive filter, and 0DTE priority/Convex.

Rev 00352 (May 22, 2026 desk cohort): tight ORB + moderate break separate Tier A
continuation names from wide-box / chase setups (IONQ, QCOM) without requiring
large break at 7:30 (SOXL 0.13% winner).
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

try:
    from .config_loader import get_config_value
except ImportError:
    get_config_value = lambda k, d=None: os.getenv(k, d)  # type: ignore


def _float_cfg(key: str, default: float) -> float:
    raw = get_config_value(key, default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


def geometry_orb_range_pct(signal: Dict[str, Any]) -> float:
    orb_range_pct = signal.get("orb_range_pct")
    if orb_range_pct is not None:
        try:
            pct = float(orb_range_pct)
            if pct > 0:
                return pct
        except (TypeError, ValueError):
            pass
    orb_high = signal.get("orb_high")
    orb_low = signal.get("orb_low")
    if orb_high is None or orb_low is None:
        od = signal.get("orb_data")
        if isinstance(od, dict):
            orb_high = od.get("orb_high", od.get("high"))
            orb_low = od.get("orb_low", od.get("low"))
    try:
        oh, ol = float(orb_high or 0), float(orb_low or 0)
        if ol > 0 and oh >= ol:
            return (oh - ol) / ol * 100.0
    except (TypeError, ValueError):
        pass
    return 0.0


def geometry_breakout_pct(signal: Dict[str, Any], *, side: Optional[str] = None) -> float:
    cached = signal.get("breakout_extension_pct")
    if cached is not None:
        try:
            return max(0.0, float(cached))
        except (TypeError, ValueError):
            pass
    direction = str(side or signal.get("side") or "LONG").upper()
    entry = signal.get("current_price", signal.get("price"))
    try:
        px = float(entry or 0)
    except (TypeError, ValueError):
        px = 0.0
    orb_high = signal.get("orb_high")
    orb_low = signal.get("orb_low")
    if orb_high is None or orb_low is None:
        od = signal.get("orb_data")
        if isinstance(od, dict):
            orb_high = od.get("orb_high", od.get("high"))
            orb_low = od.get("orb_low", od.get("low"))
    try:
        oh = float(orb_high or 0)
        ol = float(orb_low or 0)
    except (TypeError, ValueError):
        return 0.0
    if direction == "SHORT":
        if ol > 0 and px > 0 and px < ol:
            return (ol - px) / ol * 100.0
        return 0.0
    if oh > 0 and px > oh:
        return (px - oh) / oh * 100.0
    return 0.0


def is_inverse_sleeve_signal(signal: Dict[str, Any]) -> bool:
    """Leveraged inverse ETF sleeves (e.g. MSTZ) — separate from underlying long."""
    meta = signal.get("metadata") if isinstance(signal.get("metadata"), dict) else {}
    if meta.get("is_inverse") or signal.get("is_inverse"):
        return True
    inv = str(meta.get("inverse_symbol") or signal.get("inverse_symbol") or "").strip()
    if inv:
        return True
    raw = str(
        get_config_value(
            "SO_INVERSE_SYMBOL_SUBSTRINGS",
            "MSTZ,SQQQ,SPXU,SPXS,SDS,QID,SOXS,UVXY",
        )
    )
    sym = str(signal.get("symbol") or "").upper()
    toks = [t.strip().upper() for t in raw.split(",") if t.strip()]
    return any(t in sym for t in toks)


def is_tier_a_geometry(
    signal: Dict[str, Any],
    *,
    max_orb_range_pct: Optional[float] = None,
    max_break_pct: Optional[float] = None,
) -> bool:
    max_orb = max_orb_range_pct if max_orb_range_pct is not None else _float_cfg("SO_TIER_A_MAX_ORB_RANGE_PCT", 4.0)
    max_brk = max_break_pct if max_break_pct is not None else _float_cfg("SO_TIER_A_MAX_BREAK_PCT", 4.0)
    orb_pct = geometry_orb_range_pct(signal)
    brk_pct = geometry_breakout_pct(signal)
    if orb_pct <= 0:
        return False
    return orb_pct <= max_orb and brk_pct < max_brk


def is_tier_b_geometry(
    signal: Dict[str, Any],
    *,
    min_orb_range_pct: Optional[float] = None,
    min_chase_break_pct: Optional[float] = None,
) -> bool:
    min_orb = min_orb_range_pct if min_orb_range_pct is not None else _float_cfg("SO_TIER_B_MIN_ORB_RANGE_PCT", 5.5)
    chase = min_chase_break_pct if min_chase_break_pct is not None else _float_cfg("SO_TIER_B_CHASE_BREAK_PCT", 4.0)
    orb_pct = geometry_orb_range_pct(signal)
    brk_pct = geometry_breakout_pct(signal)
    if orb_pct >= min_orb:
        return True
    if brk_pct >= chase:
        return True
    return False


def tier_a_geometry_momentum_proxy(signal: Dict[str, Any]) -> bool:
    """
    Convex / 0DTE momentum proxy when MACD is missing at 7:30 (May 22 snapshot gap).
    Requires price above ORB high and Tier-A geometry.
    """
    side = str(signal.get("side") or "LONG").upper()
    if side != "LONG":
        return False
    cp = signal.get("current_price", signal.get("price"))
    orb_high = signal.get("orb_high")
    if orb_high is None:
        od = signal.get("orb_data")
        if isinstance(od, dict):
            orb_high = od.get("orb_high")
    try:
        if not (orb_high and float(cp or 0) > float(orb_high)):
            return False
    except (TypeError, ValueError):
        return False
    return is_tier_a_geometry(signal)


def _legacy_distance_confidence(break_distance_frac: float) -> float:
    """Pre-Rev-00353 formula (monotonic with break — inverted vs desk outcomes)."""
    return min(0.95, 0.7 + abs(break_distance_frac) * 10.0)


def compute_orb_signal_confidence(
    *,
    break_distance_frac: float,
    orb_range_pct: float,
    volume_color: Optional[str] = None,
) -> float:
    """
    Continuation-shaped confidence at 7:30 signal emit (Rev 00353).

    Replaces ``0.7 + 10×|break|`` which rewarded chase breaks (QCOM 4.48% → 0.95)
    and penalized minimal-break winners (SOXL 0.13% → ~0.71). Desk evidence: tight
    ORB + moderate break = Tier A; wide ORB or break ≥4% = Tier B/C.
    """
    use_shape = str(get_config_value("SO_CONFIDENCE_CONTINUATION_SHAPE_ENABLED", "true")).strip().lower()
    if use_shape not in ("1", "true", "yes"):
        return _legacy_distance_confidence(break_distance_frac)

    break_pct = max(0.0, abs(break_distance_frac) * 100.0)
    base = _float_cfg("SO_CONFIDENCE_BASE", 0.72)
    peak_pct = _float_cfg("SO_CONFIDENCE_BREAK_PEAK_PCT", 2.5)
    peak_boost = _float_cfg("SO_CONFIDENCE_BREAK_PEAK_BOOST", 0.10)
    chase_start = _float_cfg("SO_CONFIDENCE_CHASE_START_PCT", 4.0)
    tight_orb = _float_cfg("SO_CONFIDENCE_TIGHT_ORB_PCT", 4.0)
    wide_orb = _float_cfg("SO_CONFIDENCE_WIDE_ORB_PCT", 5.5)

    if break_pct <= 0.05:
        brk_score = base
    elif break_pct <= peak_pct:
        brk_score = base + peak_boost * (break_pct / max(0.01, peak_pct))
    elif break_pct <= chase_start:
        span = max(0.01, chase_start - peak_pct)
        brk_score = (base + peak_boost) - 0.14 * ((break_pct - peak_pct) / span)
    else:
        brk_score = max(0.55, (base + peak_boost - 0.14) - 0.05 * (break_pct - chase_start))

    if orb_range_pct <= 0:
        rng_score = 0.75
    elif orb_range_pct <= tight_orb:
        rng_score = 0.90
    elif orb_range_pct <= wide_orb:
        rng_score = 0.72
    else:
        rng_score = 0.62

    conf = 0.55 * brk_score + 0.45 * rng_score
    if volume_color in ("GREEN", "RED"):
        conf *= 1.02

    cap = _float_cfg("SO_CONFIDENCE_MAX", 0.95)
    floor = _float_cfg("SO_CONFIDENCE_MIN", 0.52)
    return max(floor, min(cap, conf))


def collection_geometry_rank_score(signal: Dict[str, Any]) -> float:
    """
    Pre-Convex 0DTE alert sort key when ``priority_score`` is not yet set.
    Tier A > neutral > Tier B (desk May 22).
    """
    if is_tier_a_geometry(signal):
        return 1.0
    if is_tier_b_geometry(signal):
        return 0.32
    orb_pct = geometry_orb_range_pct(signal)
    brk_pct = geometry_breakout_pct(signal)
    if orb_pct <= 0:
        return 0.50
    tight = _float_cfg("SO_TIER_A_MAX_ORB_RANGE_PCT", 4.0)
    score = 0.55
    if orb_pct <= tight:
        score += 0.25
    if brk_pct < _float_cfg("SO_TIER_B_CHASE_BREAK_PCT", 4.0):
        score += 0.15
    return min(0.88, score)
