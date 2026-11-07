#!/usr/bin/env python3
"""
ORB Quality Gate helpers + thin admit shim
==========================================

Shared kernels (pc_oi, dir%, v2.5, E10/E11/E7, chase skip) live here and are
called only from ``quality_gate_stack`` evaluators. Score is the shared
extension-confidence ranker (observe / rank only — not an admit floor).

``ConvexEligibilityFilter`` / ``QualityGateFilter`` is a compatibility shim:
``is_eligible`` / ``filter_signals`` call ``admit_pre_exec(PATH_ORB)`` and map
``GateReport`` → legacy ``(ok, eligibility_reasons, rejection_reasons)``.

Logs: ``QUALITY_GATE_PASS/REJECT`` primary; ``CONVEX_PASS/REJECT`` kept as
aliases for one release.

Env: 0DTE_CONVEX_V25_SYMMETRIC_ENABLED, 0DTE_CONVEX_VWAP_EXTENSION_REJECT_PCT,
     0DTE_CONVEX_ORB_RANGE_MAX_PCT, 0DTE_SHALLOW_E14_ENABLED (default false),
     0DTE_SHALLOW_CALL_MACD_MIN, 0DTE_SHALLOW_PUT_MACD_MAX,
     0DTE_CONVEX_CHASE_SKIP_ENABLED, 0DTE_CONVEX_E10_ENABLED,
     0DTE_CONVEX_E11_ENABLED, 0DTE_CONVEX_E7_ENABLED,
     BREAKOUT_BREAK_MIN (shared ORB1/ORB2 min distance 0.08%)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key, str(default))
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None or str(raw).strip() == "":
        return default
    s = str(raw).strip().lower()
    if s in ("true", "1", "yes", "on"):
        return True
    if s in ("false", "0", "no", "off"):
        return False
    return default


def _signal_side(signal: Dict[str, Any]) -> str:
    """Normalize to LONG/SHORT for Convex internal gates (CALL→LONG, PUT→SHORT)."""
    side = str(signal.get("side") or signal.get("direction") or "LONG").upper()
    if side in ("LONG", "SHORT"):
        return side
    if side in ("CALL", "BULLISH", "C"):
        return "LONG"
    if side in ("PUT", "BEARISH", "P"):
        return "SHORT"
    # Prefer explicit option fields over ambiguous geometry labels (bear/bull).
    for key in ("option_type", "option_type_label", "expected_option_side", "option_side"):
        raw = signal.get(key)
        if raw is None:
            continue
        s = str(raw).strip().upper()
        if s in ("CALL", "C", "LONG"):
            return "LONG"
        if s in ("PUT", "P", "SHORT"):
            return "SHORT"
    return "LONG"


def is_leveraged_0dte_symbol(symbol: str) -> bool:
    raw = os.getenv(
        "0DTE_CONVEX_LEVERAGED_SUBSTRINGS",
        "TQQQ,SPXL,UPRO,SSO,QLD,SQQQ,SPXU,SPXS,SDS,QID",
    )
    toks = [t.strip().upper() for t in str(raw).split(",") if t.strip()]
    su = str(symbol or "").upper()
    return any(t in su for t in toks)


def pc_oi_confirmation_pass(
    signal: Dict[str, Any],
    direction: Optional[str] = None,
) -> Tuple[bool, str]:
    """CALL: put_call_oi_ratio < 1. PUT: put_call_oi_ratio > 1."""
    direction = (direction or _signal_side(signal)).upper()
    raw = signal.get("put_call_oi_ratio")
    if raw is None:
        return False, "pc_oi_missing_chain_skew"
    try:
        pc = float(raw)
    except (TypeError, ValueError):
        return False, "pc_oi_invalid"
    if direction == "LONG":
        if pc < 1.0:
            return True, f"pc_oi_bullish_skew_{pc:.3f}_CALL"
        return False, f"pc_oi_bearish_skew_{pc:.3f}_CALL_reject"
    if pc > 1.0:
        return True, f"pc_oi_bearish_skew_{pc:.3f}_PUT"
    return False, f"pc_oi_bullish_skew_{pc:.3f}_PUT_reject"


def same_side_direction_volume_pct(
    signal: Dict[str, Any],
    direction: Optional[str] = None,
) -> Optional[float]:
    """Same-side session dir% = call/(call+put) for CALL, put/(call+put) for PUT."""
    direction = (direction or _signal_side(signal)).upper()
    cv_raw = signal.get("call_chain_volume")
    pv_raw = signal.get("put_chain_volume")
    if cv_raw is None or pv_raw is None:
        return None
    try:
        cv = float(cv_raw)
        pv = float(pv_raw)
    except (TypeError, ValueError):
        return None
    total = cv + pv
    if total <= 0:
        return None
    if direction == "SHORT":
        return pv / total
    return cv / total


def direction_volume_confirmation_pass(
    signal: Dict[str, Any],
    direction: Optional[str] = None,
    *,
    min_pct: Optional[float] = None,
    enabled: Optional[bool] = None,
) -> Tuple[bool, str]:
    """Same-side session chain volume must be strictly > min_pct (default 0.54).

    Research lock (Jul 28): ORB losers/coin-flips ≤52.6%; scorable winners ≥54.9%.
    Rule is ``dir% > 0.54`` (not ≥0.55) so borderline winners stay eligible.
    Shared by ORB Convex + Trendline/Breakout pc_oi handoff gates.
    """
    if enabled is None:
        enabled = _env_bool("0DTE_DIR_VOLUME_GATE_ENABLED", True)
    if not enabled:
        return True, "dir_vol_gate_disabled"
    direction = (direction or _signal_side(signal)).upper()
    floor = float(min_pct if min_pct is not None else _env_float("0DTE_DIR_VOLUME_MIN_PCT", 0.54))
    dir_pct = same_side_direction_volume_pct(signal, direction)
    if dir_pct is None:
        return False, "dir_vol_missing_chain_volume"
    side_label = "PUT" if direction == "SHORT" else "CALL"
    if dir_pct > floor:
        return True, f"dir_vol_{side_label}_{dir_pct:.3f}_gt_{floor:.2f}"
    return False, f"dir_vol_{side_label}_{dir_pct:.3f}_le_{floor:.2f}_reject"


def compute_orb_range_pct(orb_high: float, orb_low: float) -> float:
    try:
        oh, ol = float(orb_high), float(orb_low)
        if ol <= 0 or oh < ol:
            return 0.0
        return (oh - ol) / ol * 100.0
    except (TypeError, ValueError):
        return 0.0


def _long_breakout_pct(signal: Dict[str, Any]) -> float:
    orb_high = signal.get("orb_high")
    cp = float(signal.get("current_price") or signal.get("price") or 0)
    if not orb_high or cp <= 0:
        return 0.0
    oh = float(orb_high)
    if oh <= 0 or cp <= oh:
        return 0.0
    return (cp - oh) / oh * 100.0


def _short_breakdown_pct(signal: Dict[str, Any]) -> float:
    orb_low = signal.get("orb_low")
    cp = float(signal.get("current_price") or signal.get("price") or 0)
    if not orb_low or cp <= 0:
        return 0.0
    ol = float(orb_low)
    if ol <= 0 or cp >= ol:
        return 0.0
    return (ol - cp) / ol * 100.0


def _merge_orb_into_signal(signal: Dict[str, Any]) -> Dict[str, Any]:
    signal = dict(signal)
    orb_raw = signal.get("orb_data")
    if orb_raw is not None and not isinstance(orb_raw, dict):
        if hasattr(orb_raw, "to_dict"):
            signal["orb_data"] = orb_raw.to_dict()
        elif hasattr(orb_raw, "orb_high") and hasattr(orb_raw, "orb_low"):
            oh = float(getattr(orb_raw, "orb_high"))
            ol = float(getattr(orb_raw, "orb_low"))
            signal["orb_high"] = signal.get("orb_high") or oh
            signal["orb_low"] = signal.get("orb_low") or ol
            pct = float(getattr(orb_raw, "orb_range_pct", 0) or 0) or compute_orb_range_pct(oh, ol)
            signal["orb_data"] = {"orb_high": oh, "orb_low": ol, "orb_range_pct": pct}
    orb_data = signal.get("orb_data") or {}
    if isinstance(orb_data, dict):
        if signal.get("orb_high") is None and orb_data.get("orb_high") is not None:
            signal["orb_high"] = orb_data["orb_high"]
        if signal.get("orb_low") is None and orb_data.get("orb_low") is not None:
            signal["orb_low"] = orb_data["orb_low"]
        if orb_data.get("orb_range_pct") is not None:
            signal["orb_range_pct"] = float(orb_data["orb_range_pct"])
    oh, ol = signal.get("orb_high"), signal.get("orb_low")
    if oh is not None and ol is not None and (signal.get("orb_range_pct") is None or signal.get("orb_range_pct") == 0):
        signal["orb_range_pct"] = compute_orb_range_pct(oh, ol)
    if (signal.get("current_price") is None or signal.get("current_price") == 0) and signal.get("price") is not None:
        signal["current_price"] = signal["price"]
    return signal


def _infer_market_regime(signal: Dict[str, Any], orb_range_min_pct: float) -> str:
    market_regime = signal.get("market_regime")
    if market_regime is not None:
        return str(market_regime)
    direction = _signal_side(signal)
    vwap_distance = float(signal.get("vwap_distance_pct") or 0)
    orb_range_pct = float(signal.get("orb_range_pct") or 0)
    current_price = float(signal.get("current_price") or signal.get("price") or 0)
    orb_high = signal.get("orb_high")
    orb_low = signal.get("orb_low")
    regime_vwap_abs = _env_float("0DTE_CONVEX_REGIME_VWAP_ABS", 1.0)
    regime_orb_mult = _env_float("0DTE_CONVEX_REGIME_ORB_MULT", 1.0)
    if abs(vwap_distance) > regime_vwap_abs:
        return "trend"
    if orb_range_pct >= orb_range_min_pct * regime_orb_mult:
        return "trend"
    if direction == "LONG" and orb_high and current_price > float(orb_high):
        return "trend"
    if direction == "SHORT" and orb_low and current_price > 0 and current_price < float(orb_low):
        return "trend"
    return "rotation"


def _orb_range_or_atr_pass(
    signal: Dict[str, Any],
    range_threshold: float,
) -> Tuple[bool, str, bool, bool, bool]:
    """Returns (pass, message, orb_range_pass, atr_pass, breakdown_pass)."""
    orb_range_pct = float(signal.get("orb_range_pct") or 0.0)
    current_price = float(signal.get("current_price") or signal.get("price") or 0.0)
    atr_5min = signal.get("atr_5min")
    atr_threshold_pct = float(
        signal.get("atr_threshold_pct") or _env_float("0DTE_CONVEX_ATR_THRESHOLD_PCT_DEFAULT", 0.25)
    )
    direction = _signal_side(signal)
    orb_low = signal.get("orb_low")
    is_leveraged = is_leveraged_0dte_symbol(str(signal.get("symbol") or ""))

    orb_range_pass = orb_range_pct >= range_threshold
    atr_pass = False
    if not orb_range_pass and atr_5min is not None and current_price > 0:
        atr_threshold = current_price * (atr_threshold_pct / 100.0)
        atr_pass = float(atr_5min) >= atr_threshold

    breakdown_pass = False
    breakdown_pct = 0.0
    if (
        not orb_range_pass
        and not atr_pass
        and direction == "SHORT"
        and orb_low
        and float(orb_low) > 0
        and current_price > 0
        and current_price < float(orb_low)
    ):
        breakdown_pct = (float(orb_low) - current_price) / float(orb_low) * 100.0
        breakdown_pass = breakdown_pct >= range_threshold

    tag = "leveraged ETF" if is_leveraged else "standard"
    if orb_range_pass:
        return True, f"ORB range {orb_range_pct:.2f}% ≥ {range_threshold:.2f}% ({tag})", True, False, False
    if atr_pass:
        return True, f"5-min ATR ${float(atr_5min):.2f} ≥ threshold (alternative to ORB range)", False, True, False
    if breakdown_pass:
        return (
            True,
            f"SHORT breakdown {breakdown_pct:.2f}% below ORB low (ORB width {orb_range_pct:.2f}% unreliable)",
            False,
            False,
            True,
        )
    return (
        False,
        f"ORB range {orb_range_pct:.2f}% < {range_threshold:.2f}% AND 5-min ATR insufficient",
        False,
        False,
        False,
    )


def _orb_break_pass(signal: Dict[str, Any]) -> Tuple[bool, str]:
    direction = _signal_side(signal)
    orb_high = signal.get("orb_high")
    orb_low = signal.get("orb_low")
    current_price = float(signal.get("current_price") or signal.get("price") or 0.0)
    min_pct = _env_float("BREAKOUT_BREAK_MIN", 0.08)
    dist = _orb_break_distance_pct(signal)

    if direction == "LONG" and orb_high is not None and current_price > 0:
        if dist is None or dist < float(min_pct):
            shown = 0.0 if dist is None else dist
            return (
                False,
                f"LONG: break {shown:.2f}% < {min_pct:.2f}% beyond ORB High ${float(orb_high):.2f}",
            )
        return True, f"LONG: Price ${current_price:.2f} > ORB High ${float(orb_high):.2f} (+{dist:.2f}%)"
    if direction == "SHORT" and orb_low is not None and current_price > 0:
        if dist is None or dist < float(min_pct):
            shown = 0.0 if dist is None else dist
            return (
                False,
                f"SHORT: break {shown:.2f}% < {min_pct:.2f}% beyond ORB Low ${float(orb_low):.2f}",
            )
        return True, f"SHORT: Price ${current_price:.2f} < ORB Low ${float(orb_low):.2f} (+{dist:.2f}%)"
    return True, "ORB break check skipped (missing ORB data)"


def _macd_noise_quality_pass(
    signal: Dict[str, Any],
    *,
    direction: str,
    hist_val: float,
    eps: float,
    noise_orb_max_pct: float,
    noise_macd_abs_min: float,
    noise_vol_max: float,
) -> Tuple[bool, str]:
    """Quality filter for MACD-deadband (weak opposing hist) VWAP-aligned names.

    MACD eps unlocks near-zero hist noise (Aug 5 GLD/IBIT class) but also admits a
    large loser basket. When hist is only weakly opposing within ±eps, require:
      1) tight ORB (``noise_orb_max_pct``), and
      2) either strong MACD *line* continuation (|macd| ≥ min) OR thin early tape
         (volume_ratio < max) — IBIT-class.

    Does **not** apply to true MACD-disagree rejects (|hist| > eps), below-VWAP
    CALL E11 paths, or MACD-aligned HOOD-class (hist same sign as side).
    """
    orb_range_pct = float(signal.get("orb_range_pct") or 0.0)
    vol = signal.get("volume_ratio")
    try:
        vol_f = float(vol) if vol is not None and str(vol).strip() != "" else None
    except (TypeError, ValueError):
        vol_f = None
    raw_line = signal.get("macd")
    try:
        macd_line = (
            float(raw_line)
            if raw_line is not None and str(raw_line).strip() != ""
            else None
        )
    except (TypeError, ValueError):
        macd_line = None

    if noise_orb_max_pct > 0 and orb_range_pct > noise_orb_max_pct:
        return False, (
            f"MACD-noise ORB {orb_range_pct:.2f}% > {noise_orb_max_pct:.2f}% "
            f"(deadband hist={hist_val:.2f} within ±{eps:.2f})"
        )

    if direction == "LONG":
        strong = macd_line is not None and macd_line >= noise_macd_abs_min
    else:
        strong = macd_line is not None and macd_line <= (-noise_macd_abs_min)
    thin = vol_f is not None and vol_f < noise_vol_max
    if strong or thin:
        why = "strong MACD line" if strong else f"thin vol {vol_f:.3f}"
        return True, (
            f"v2.5 MACD-noise quality pass ({why}, orb={orb_range_pct:.2f}%, "
            f"hist={hist_val:.2f} within ±{eps:.2f})"
        )
    return False, (
        f"MACD-noise quality fail (need |macd|≥{noise_macd_abs_min:.2f} or "
        f"vol<{noise_vol_max:.2f}; macd={macd_line}, vol={vol_f}, "
        f"hist={hist_val:.2f} within ±{eps:.2f})"
    )


def _v25_symmetric_gates_pass(
    signal: Dict[str, Any],
    *,
    vwap_extension_reject_pct: float,
    orb_range_max_pct: float,
    macd_disagree_eps: float = 99.0,
    macd_noise_quality_enabled: bool = False,
    macd_noise_orb_max_pct: float = 1.0,
    macd_noise_macd_abs_min: float = 0.60,
    macd_noise_vol_max: float = 0.20,
    macd_noise_band: float = 0.50,
) -> Tuple[bool, str]:
    """Side-symmetric v2.5 hard gates: VWAP extension, momentum disagreement, wide ORB.

    MACD disagreement uses an absolute deadband (``macd_disagree_eps``): near-zero
    histogram noise at the 7:30 open (e.g. −0.01…−0.25) must not block CALL-above-VWAP
    or PUT-below-VWAP. Aug 5 RESEARCH: 15/18 Convex rejects were tiny negative MACD
    with no meaningful disagreement.

    **E16:** Mild soft-neg quality (``macd_noise_*``) applies only inside
    ``±macd_noise_band`` (default **0.50**), independent of a large observe eps
    (e.g. eps=99). That blocks Aug 5 SLV/DAL/ETN fades while allowing Aug 6 deep
    soft-neg continuation (ASML/MU/SNDK hist ≪ −0.50). Quality uses MACD **line**
    ≥ abs min (GLD 1.16) or thin vol (IBIT), plus tight ORB ≤ max.
    """
    direction = _signal_side(signal)
    vwap_missing = bool(signal.get("vwap_missing"))
    if vwap_missing or signal.get("vwap_distance_pct") is None:
        return True, "v2.5 soft-skip (vwap_distance missing)"
    vwap_distance = float(signal.get("vwap_distance_pct") or 0.0)
    raw_macd = signal.get("macd_histogram")
    has_macd = (
        raw_macd is not None
        and str(raw_macd).strip() != ""
        and not bool(signal.get("macd_missing"))
    )
    macd_val = float(raw_macd) if has_macd else 0.0
    eps = max(0.0, float(macd_disagree_eps or 0.0))
    # Quality band is capped by eps but never forced to follow observe-scale eps.
    noise_band = max(0.0, float(macd_noise_band or 0.0))
    if eps > 0:
        noise_band = min(noise_band, eps) if noise_band > 0 else eps
    orb_range_pct = float(signal.get("orb_range_pct") or 0.0)

    if orb_range_max_pct > 0 and orb_range_pct >= orb_range_max_pct:
        return False, (
            f"ORB range {orb_range_pct:.2f}% ≥ {orb_range_max_pct:.2f}% "
            f"(wide-ORB chop/fade reject)"
        )

    def _deadband_quality() -> Tuple[bool, str]:
        if not macd_noise_quality_enabled:
            return True, (
                f"v2.5 MACD disagree soft-skip "
                f"(hist={macd_val:.2f} within ±{noise_band:.2f}; noise quality OFF)"
            )
        return _macd_noise_quality_pass(
            signal,
            direction=direction,
            hist_val=macd_val,
            eps=noise_band if noise_band > 0 else eps,
            noise_orb_max_pct=float(macd_noise_orb_max_pct or 0.0),
            noise_macd_abs_min=float(macd_noise_macd_abs_min or 0.0),
            noise_vol_max=float(macd_noise_vol_max or 0.0),
        )

    if direction == "LONG":
        if vwap_extension_reject_pct > 0 and vwap_distance > vwap_extension_reject_pct:
            return False, (
                f"CALL vwap extension {vwap_distance:.2f}% > {vwap_extension_reject_pct:.2f}%"
            )
        if has_macd and vwap_distance > 0 and macd_val < 0:
            # Mild soft-neg → quality (Aug 5 fade kill). Deep soft-neg → observe when
            # disagree eps is wide (E15/E16), else hard reject below -eps.
            band = noise_band if noise_band > 0 else eps
            if macd_val >= (-band):
                return _deadband_quality()
            if macd_val < (-eps):
                return False, (
                    f"CALL above VWAP ({vwap_distance:.2f}%) with negative MACD ({macd_val:.2f})"
                )
            return True, (
                f"v2.5 deep soft-neg observe "
                f"(hist={macd_val:.2f} < -{band:.2f}; disagree eps={eps:.2f})"
            )
    else:
        if vwap_extension_reject_pct > 0 and vwap_distance < -vwap_extension_reject_pct:
            return False, (
                f"PUT vwap extension {vwap_distance:.2f}% < -{vwap_extension_reject_pct:.2f}%"
            )
        if has_macd and vwap_distance < 0 and macd_val > 0:
            band = noise_band if noise_band > 0 else eps
            if macd_val <= band:
                return _deadband_quality()
            if macd_val > eps:
                return False, (
                    f"PUT below VWAP ({vwap_distance:.2f}%) with positive MACD ({macd_val:.2f})"
                )
            return True, (
                f"v2.5 deep soft-pos observe "
                f"(hist={macd_val:.2f} > {band:.2f}; disagree eps={eps:.2f})"
            )

    return True, "v2.5 symmetric side gates pass"


# Index symbols for E7 grind skip (guide: SPY/QQQ/IWM/RUT/MAGS).
_INDEX_GRIND_SYMBOLS = frozenset(
    s.strip().upper()
    for s in str(
        os.getenv("0DTE_CONVEX_E7_INDEX_SYMBOLS", "SPY,QQQ,IWM,RUT,MAGS") or ""
    ).split(",")
    if s.strip()
)


def _is_protection_day(signal: Dict[str, Any]) -> bool:
    if bool(signal.get("protection_day") or signal.get("is_protection_day")):
        return True
    for key in ("pp_trigger_id", "red_day_trigger_id", "trigger_id", "pp_regime"):
        tid = str(signal.get(key) or "").strip().lower()
        if tid in ("protection_day", "protection_day_split_tape"):
            return True
        if "protection_day" in tid:
            return True
    return False


def _is_index_grind_symbol(symbol: str) -> bool:
    return str(symbol or "").strip().upper() in _INDEX_GRIND_SYMBOLS


def _orb_break_distance_pct(signal: Dict[str, Any]) -> Optional[float]:
    """Signed break %: CALL above ORB high (+), PUT below ORB low (+ favorable)."""
    direction = _signal_side(signal)
    price = float(signal.get("current_price") or signal.get("price") or 0.0)
    if price <= 0:
        return None
    if direction == "LONG":
        orb_high = signal.get("orb_high")
        if orb_high is None or float(orb_high) <= 0:
            return None
        return (price - float(orb_high)) / float(orb_high) * 100.0
    orb_low = signal.get("orb_low")
    if orb_low is None or float(orb_low) <= 0:
        return None
    return (float(orb_low) - price) / float(orb_low) * 100.0


def _volume_ratio(signal: Dict[str, Any]) -> Optional[float]:
    raw = signal.get("volume_ratio")
    if raw is None:
        raw = signal.get("orb_volume_ratio")
    if raw is None or bool(signal.get("volume_ratio_missing")):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def e11_protection_call_carveout_pass(
    signal: Dict[str, Any],
    *,
    min_break_pct: float = 0.45,
    max_rsi: float = 54.0,
    min_orb_range_pct: float = 0.65,
) -> Tuple[bool, str]:
    """Jun 26 MELI/MAGS/META/TSLA — below-VWAP CALL continuation on protection day."""
    if _signal_side(signal) != "LONG":
        return False, "E11 N/A (not CALL)"
    break_pct = _orb_break_distance_pct(signal)
    if break_pct is None or break_pct <= float(min_break_pct):
        return False, f"E11 fail: break {break_pct} <= {min_break_pct}"
    if signal.get("vwap_distance_pct") is None or bool(signal.get("vwap_missing")):
        return False, "E11 fail: vwap missing"
    vwap = float(signal.get("vwap_distance_pct") or 0.0)
    if vwap >= 0:
        return False, f"E11 fail: vwap {vwap:.2f}% not below"
    rsi_raw = signal.get("rsi")
    if rsi_raw is None or bool(signal.get("rsi_missing")):
        return False, "E11 fail: rsi missing"
    if float(rsi_raw) >= float(max_rsi):
        return False, f"E11 fail: rsi {float(rsi_raw):.1f} >= {max_rsi}"
    pc_ok, pc_msg = pc_oi_confirmation_pass(signal, "LONG")
    if not pc_ok:
        return False, f"E11 fail: {pc_msg}"
    orb_range = float(signal.get("orb_range_pct") or 0.0)
    if orb_range < float(min_orb_range_pct):
        return False, f"E11 fail: orb_range {orb_range:.2f}% < {min_orb_range_pct}"
    return True, (
        f"E11 carve-out: break +{break_pct:.2f}% · vwap {vwap:.2f}% · "
        f"rsi {float(rsi_raw):.1f} · pc_oi ok · orb {orb_range:.2f}%"
    )


def e10_protection_call_gate_pass(
    signal: Dict[str, Any],
    *,
    enabled: Optional[bool] = None,
) -> Tuple[bool, str]:
    """E10: protection_day → block CALL unless E11 carve-out. PUT lane always open."""
    if enabled is None:
        enabled = _env_bool("0DTE_CONVEX_E10_ENABLED", True)
    if not enabled:
        return True, "E10 disabled"
    if _signal_side(signal) != "LONG":
        return True, "E10 N/A (PUT lane open)"
    if not _is_protection_day(signal):
        return True, "E10 N/A (not protection_day)"
    if not _env_bool("0DTE_CONVEX_E11_ENABLED", True):
        return False, "E10 protection_day CALL block (E11 disabled)"
    e11_ok, e11_msg = e11_protection_call_carveout_pass(signal)
    if e11_ok:
        return True, e11_msg
    return False, f"E10 protection_day CALL block ({e11_msg})"


def e7_index_grind_skip_pass(
    signal: Dict[str, Any],
    *,
    enabled: Optional[bool] = None,
    vol_max: float = 0.30,
    pc_oi_min: float = 1.5,
    break_max_pct: float = 0.75,
) -> Tuple[bool, str]:
    """E7: skip weak index CALL grind (vol<0.30 · pc_oi>1.5 · break<0.75%)."""
    if enabled is None:
        enabled = _env_bool("0DTE_CONVEX_E7_ENABLED", True)
    if not enabled:
        return True, "E7 disabled"
    if _signal_side(signal) != "LONG":
        return True, "E7 N/A (PUT)"
    sym = str(signal.get("symbol") or "")
    if not _is_index_grind_symbol(sym):
        return True, "E7 N/A (not index grind set)"
    vol = _volume_ratio(signal)
    if vol is None:
        return True, "E7 soft-skip (volume_ratio missing)"
    try:
        pc = float(signal.get("put_call_oi_ratio"))
    except (TypeError, ValueError):
        return True, "E7 soft-skip (pc_oi missing)"
    break_pct = _orb_break_distance_pct(signal)
    if break_pct is None:
        return True, "E7 soft-skip (break missing)"
    if vol < float(vol_max) and pc > float(pc_oi_min) and break_pct < float(break_max_pct):
        return False, (
            f"E7 index grind skip: {sym} vol={vol:.2f}<{vol_max} "
            f"pc_oi={pc:.2f}>{pc_oi_min} break={break_pct:.2f}%<{break_max_pct}"
        )
    return True, "E7 pass"


def compound_vwap_chase_skip_pass(
    signal: Dict[str, Any],
    *,
    enabled: Optional[bool] = None,
    vol_max: float = 0.30,
    vwap_abs_min: float = 6.0,
    pc_oi_adverse: float = 1.2,
) -> Tuple[bool, str]:
    """Mirrored compound chase: weak vol + extended VWAP + adverse pc_oi (or index).

    CALL: vwap > +6 AND vol < 0.30 AND (pc_oi > 1.2 OR index)
    PUT:  vwap < −6 AND vol < 0.30 AND (pc_oi < 0.8 OR index)  # bullish skew adverse to PUT
    """
    if enabled is None:
        enabled = _env_bool("0DTE_CONVEX_CHASE_SKIP_ENABLED", True)
    if not enabled:
        return True, "chase skip disabled"
    if signal.get("vwap_distance_pct") is None or bool(signal.get("vwap_missing")):
        return True, "chase soft-skip (vwap missing)"
    vwap = float(signal.get("vwap_distance_pct") or 0.0)
    vol = _volume_ratio(signal)
    if vol is None:
        return True, "chase soft-skip (volume_ratio missing)"
    direction = _signal_side(signal)
    sym = str(signal.get("symbol") or "")
    is_index = _is_index_grind_symbol(sym)
    try:
        pc = float(signal.get("put_call_oi_ratio")) if signal.get("put_call_oi_ratio") is not None else None
    except (TypeError, ValueError):
        pc = None
    if direction == "LONG":
        adverse = (pc is not None and pc > float(pc_oi_adverse)) or is_index
        if vol < float(vol_max) and vwap > float(vwap_abs_min) and adverse:
            return False, (
                f"CALL chase skip: vol={vol:.2f}<{vol_max} vwap={vwap:.1f}%>{vwap_abs_min} "
                f"pc_oi={pc} index={is_index}"
            )
    else:
        # Adverse for PUT = bullish chain (low pc_oi) or index ETF grind.
        put_adverse_ceil = 1.0 / float(pc_oi_adverse) if float(pc_oi_adverse) > 0 else 0.8
        adverse = (pc is not None and pc < put_adverse_ceil) or is_index
        if vol < float(vol_max) and vwap < -float(vwap_abs_min) and adverse:
            return False, (
                f"PUT chase skip: vol={vol:.2f}<{vol_max} vwap={vwap:.1f}%<-{vwap_abs_min} "
                f"pc_oi={pc} index={is_index}"
            )
    return True, "chase skip pass"


def e14_shallow_gates_pass(
    signal: Dict[str, Any],
    *,
    enabled: Optional[bool] = None,
    call_macd_min: Optional[float] = None,
    put_macd_max: Optional[float] = None,
) -> Tuple[bool, str]:
    """
    E14 shallow — historically rejected weak momentum vs VWAP side.

    CALL: vwap < 0 AND macd_histogram < call_macd_min (default 2.0)
    PUT:  vwap > 0 AND macd_histogram > put_macd_max (default −2.0)

    Default OFF for admit (0DTE_SHALLOW_E14_ENABLED=false): conflicts with Jun 26
    E11 protection CALL winners (below VWAP + negative MACD). When disabled, returns
    pass so callers can still log telemetry separately.

    When MACD is missing (<26 session bars), soft-skip — do not invent 0.0.
    """
    if enabled is None:
        enabled = _env_bool("0DTE_SHALLOW_E14_ENABLED", False)
    if not enabled:
        return True, "E14 shallow disabled (telemetry only)"
    cm = _env_float("0DTE_SHALLOW_CALL_MACD_MIN", 2.0) if call_macd_min is None else float(call_macd_min)
    pm = _env_float("0DTE_SHALLOW_PUT_MACD_MAX", -2.0) if put_macd_max is None else float(put_macd_max)
    direction = _signal_side(signal)
    vwap = float(signal.get("vwap_distance_pct") or 0.0)
    raw_macd = signal.get("macd_histogram")
    has_macd = raw_macd is not None and str(raw_macd).strip() != ""
    if not has_macd or bool(signal.get("macd_missing")):
        return True, "E14 shallow soft-skip (macd_histogram missing)"
    macd = float(raw_macd)
    if direction == "LONG" and vwap < 0 and macd < cm:
        return False, f"E14 shallow CALL: vwap {vwap:.1f}% < 0 and MACD {macd:.2f} < {cm}"
    if direction == "SHORT" and vwap > 0 and macd > pm:
        return False, f"E14 shallow PUT: vwap {vwap:.1f}% > 0 and MACD {macd:.2f} > {pm}"
    return True, "E14 shallow pass"


@dataclass
class ConvexEligibilityResult:
    signal: Dict[str, Any]
    eligibility_score: float
    is_eligible: bool
    eligibility_reasons: List[str]
    rejection_reasons: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "signal": self.signal,
            "eligibility_score": self.eligibility_score,
            "is_eligible": self.is_eligible,
            "eligibility_reasons": self.eligibility_reasons,
            "rejection_reasons": self.rejection_reasons,
        }


class ConvexEligibilityFilter:
    """
    ORB Quality Gate shim — score for ranking; admit via ``admit_pre_exec(PATH_ORB)``.

    Helpers on this module remain the shared kernels used by quality_gate_stack.
    """

    def __init__(
        self,
        orb_range_min_pct: Optional[float] = None,
        trend_day_required: bool = True,
        min_score: Optional[float] = None,
        v25_symmetric_enabled: Optional[bool] = None,
        vwap_extension_reject_pct: Optional[float] = None,
        macd_disagree_eps: Optional[float] = None,
        macd_deadband_quality_enabled: Optional[bool] = None,
        macd_deadband_orb_max_pct: Optional[float] = None,
        macd_deadband_macd_abs_min: Optional[float] = None,
        macd_deadband_vol_max: Optional[float] = None,
        macd_deadband_noise_band: Optional[float] = None,
        orb_range_max_pct: Optional[float] = None,
        shallow_e14_enabled: Optional[bool] = None,
        shallow_call_macd_min: Optional[float] = None,
        shallow_put_macd_max: Optional[float] = None,
        dir_volume_gate_enabled: Optional[bool] = None,
        dir_volume_min_pct: Optional[float] = None,
    ):
        self.orb_range_min_pct = (
            float(orb_range_min_pct)
            if orb_range_min_pct is not None
            else _env_float("0DTE_CONVEX_ORB_RANGE_MIN", 0.25)
        )
        self.trend_day_required = trend_day_required
        # Kept for constructor / log compat. Not an admit floor.
        self.min_score = (
            float(min_score) if min_score is not None else _env_float("0DTE_CONVEX_MIN_SCORE", 0.0)
        )
        self.dir_volume_gate_enabled = (
            dir_volume_gate_enabled
            if dir_volume_gate_enabled is not None
            else _env_bool("0DTE_DIR_VOLUME_GATE_ENABLED", True)
        )
        self.dir_volume_min_pct = (
            float(dir_volume_min_pct)
            if dir_volume_min_pct is not None
            else _env_float("0DTE_DIR_VOLUME_MIN_PCT", 0.54)
        )
        self.v25_symmetric_enabled = (
            v25_symmetric_enabled
            if v25_symmetric_enabled is not None
            else _env_bool("0DTE_CONVEX_V25_SYMMETRIC_ENABLED", True)
        )
        self.vwap_extension_reject_pct = (
            float(vwap_extension_reject_pct)
            if vwap_extension_reject_pct is not None
            else _env_float("0DTE_CONVEX_VWAP_EXTENSION_REJECT_PCT", 15.0)
        )
        self.macd_disagree_eps = (
            float(macd_disagree_eps)
            if macd_disagree_eps is not None
            else _env_float("0DTE_CONVEX_MACD_DISAGREE_EPS", 99.0)
        )
        self.macd_deadband_quality_enabled = (
            macd_deadband_quality_enabled
            if macd_deadband_quality_enabled is not None
            else _env_bool("0DTE_QG_MACD_DEADBAND_QUALITY_ENABLED", True)
        )
        self.macd_deadband_orb_max_pct = (
            float(macd_deadband_orb_max_pct)
            if macd_deadband_orb_max_pct is not None
            else _env_float("0DTE_QG_MACD_DEADBAND_ORB_MAX_PCT", 1.0)
        )
        self.macd_deadband_macd_abs_min = (
            float(macd_deadband_macd_abs_min)
            if macd_deadband_macd_abs_min is not None
            else _env_float("0DTE_QG_MACD_DEADBAND_MACD_ABS_MIN", 0.60)
        )
        self.macd_deadband_vol_max = (
            float(macd_deadband_vol_max)
            if macd_deadband_vol_max is not None
            else _env_float("0DTE_QG_MACD_DEADBAND_VOL_MAX", 0.20)
        )
        self.macd_deadband_noise_band = (
            float(macd_deadband_noise_band)
            if macd_deadband_noise_band is not None
            else _env_float("0DTE_QG_MACD_DEADBAND_NOISE_BAND", 0.50)
        )
        self.orb_range_max_pct = (
            float(orb_range_max_pct)
            if orb_range_max_pct is not None
            else _env_float("0DTE_CONVEX_ORB_RANGE_MAX_PCT", 8.0)
        )
        self.shallow_e14_enabled = (
            shallow_e14_enabled
            if shallow_e14_enabled is not None
            else _env_bool("0DTE_SHALLOW_E14_ENABLED", False)
        )
        self.shallow_call_macd_min = (
            float(shallow_call_macd_min)
            if shallow_call_macd_min is not None
            else _env_float("0DTE_SHALLOW_CALL_MACD_MIN", 2.0)
        )
        self.shallow_put_macd_max = (
            float(shallow_put_macd_max)
            if shallow_put_macd_max is not None
            else _env_float("0DTE_SHALLOW_PUT_MACD_MAX", -2.0)
        )
        self._last_full_results: List[ConvexEligibilityResult] = []
        log.info(
            "Quality Gate Filter (ORB shim): admit_pre_exec + helpers "
            "(dir_vol>%.2f, ORB %.2f–%.2f%%, min score %.2f, v25=%s, e14=%s)",
            self.dir_volume_min_pct,
            self.orb_range_min_pct,
            self.orb_range_max_pct,
            self.min_score,
            self.v25_symmetric_enabled,
            self.shallow_e14_enabled,
        )

    def convex_rejection_rows_for_alert(
        self, limit: int = 20, prefer_short: bool = False
    ) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        results = list(self._last_full_results or [])
        if prefer_short:
            results = sorted(
                results,
                key=lambda r: (0 if _signal_side(r.signal) == "SHORT" else 1),
            )
        for r in results:
            if r.is_eligible:
                continue
            sym = str(r.signal.get("symbol") or "UNKNOWN")
            side = _signal_side(r.signal)
            top = r.rejection_reasons[0] if r.rejection_reasons else "Convex rejected"
            rows.append(
                {
                    "symbol": sym,
                    "direction": side,
                    "reason_code": "convex_filter",
                    "reason": f"score={r.eligibility_score:.2f} | {top}",
                }
            )
            if len(rows) >= limit:
                break
        return rows

    def calculate_eligibility_score(
        self,
        signal: Dict[str, Any],
        all_signals: Optional[List[Dict[str, Any]]] = None,
    ) -> float:
        """0–1 extension confidence (shared ORB1/ORB2 ranker). Not an admit floor."""
        del all_signals
        signal = _merge_orb_into_signal(signal)
        from easy0DTE.modules.breakout_extension_score import extension_score_from_mapping

        score100, _fields = extension_score_from_mapping(signal)
        return float(score100) / 100.0

    def is_eligible(
        self,
        signal: Dict[str, Any],
        all_signals: Optional[List[Dict[str, Any]]] = None,
        min_score: Optional[float] = None,
    ) -> Tuple[bool, List[str], List[str]]:
        """Admit via singular Quality Gate Stack — no dual check loop."""
        del all_signals
        original = signal
        signal = _merge_orb_into_signal(signal)
        del min_score
        score = self.calculate_eligibility_score(signal)
        signal["eligibility_score"] = float(score)
        # Propagate shim instance knobs into GateContext (tests / overrides).
        signal["v25_symmetric_enabled"] = bool(self.v25_symmetric_enabled)
        signal["vwap_extension_reject_pct"] = float(self.vwap_extension_reject_pct)
        signal["orb_range_max_pct"] = float(self.orb_range_max_pct)
        signal["macd_disagree_eps"] = float(self.macd_disagree_eps)
        signal["macd_deadband_quality_enabled"] = bool(self.macd_deadband_quality_enabled)
        signal["macd_deadband_orb_max_pct"] = float(self.macd_deadband_orb_max_pct)
        signal["macd_deadband_macd_abs_min"] = float(self.macd_deadband_macd_abs_min)
        signal["macd_deadband_vol_max"] = float(self.macd_deadband_vol_max)
        signal["macd_deadband_noise_band"] = float(self.macd_deadband_noise_band)
        signal["shallow_e14_enabled"] = bool(self.shallow_e14_enabled)
        signal["shallow_call_macd_min"] = float(self.shallow_call_macd_min)
        signal["shallow_put_macd_max"] = float(self.shallow_put_macd_max)
        signal["dir_volume_gate_enabled"] = bool(self.dir_volume_gate_enabled)
        signal["dir_volume_min_pct"] = float(self.dir_volume_min_pct)
        signal["trend_day_required"] = bool(self.trend_day_required)
        signal["orb_range_min_pct"] = float(self.orb_range_min_pct)

        from easy0DTE.modules.quality_gate_stack import (
            PATH_ORB,
            admit_pre_exec,
            gate_report_to_reason_lists,
        )

        report = admit_pre_exec(
            PATH_ORB,
            signal,
            quality_score=float(score) * 100.0,
            quality_score_min=None,
            include_contract=False,
            stop_on_reject=False,
            stamp_target=signal,
        )
        qgr = signal.get("quality_gate_report")
        if isinstance(original, dict) and qgr is not None:
            original["quality_gate_report"] = qgr
            original["eligibility_score"] = float(score)
        eligibility_reasons, rejection_reasons = gate_report_to_reason_lists(report)
        is_ok = bool(report.admitted)
        eligibility_reasons.append(f"extension_score={score * 100.0:.0f}")
        return is_ok, eligibility_reasons, rejection_reasons

    def filter_signals(
        self,
        signals: List[Dict[str, Any]],
        min_score: Optional[float] = None,
        max_signals: Optional[int] = None,
        min_score_by_side: Optional[Dict[str, float]] = None,
    ) -> List[ConvexEligibilityResult]:
        if not signals:
            log.warning("No signals provided to filter")
            self._last_full_results = []
            return []

        del min_score, min_score_by_side  # rank by extension; admit is stack-only
        log.info(
            "🔍 Quality Gate (ORB): %d signals (admit_pre_exec STACKS[orb_0dte]; rank=extension)",
            len(signals),
        )
        log.info(
            "  0DTE_QUALITY_GATE_STAGE | stage=filter_start | "
            "grep=QUALITY_GATE_PASS|QUALITY_GATE_REJECT|CONVEX_PASS|CONVEX_REJECT|CONVEX_0_eligible"
        )

        results: List[ConvexEligibilityResult] = []
        for signal in signals:
            is_eligible, eligibility_reasons, rejection_reasons = self.is_eligible(
                signal, signals
            )
            score = self.calculate_eligibility_score(signal, signals)
            results.append(
                ConvexEligibilityResult(
                    signal=signal,
                    eligibility_score=score,
                    is_eligible=is_eligible,
                    eligibility_reasons=eligibility_reasons,
                    rejection_reasons=rejection_reasons,
                )
            )

        results.sort(key=lambda x: x.eligibility_score, reverse=True)
        eligible_results = [r for r in results if r.is_eligible]
        if max_signals and len(eligible_results) > max_signals:
            eligible_results = eligible_results[:max_signals]

        input_long = sum(1 for s in signals if _signal_side(s) == "LONG")
        input_short = sum(1 for s in signals if _signal_side(s) == "SHORT")
        log.info("Quality Gate Filter Results:")
        log.info("  - Total signals: %d (LONG: %d, SHORT: %d)", len(signals), input_long, input_short)
        log.info("  - Eligible signals: %d", len(eligible_results))
        log.info("  - Rejected signals: %d", len(signals) - len(eligible_results))

        for r in results:
            symbol = r.signal.get("symbol", "UNKNOWN")
            opt_type = "CALL" if _signal_side(r.signal) == "LONG" else "PUT"
            if r.is_eligible:
                log.info(
                    "  QUALITY_GATE_PASS | %s %s score=%.2f",
                    symbol,
                    opt_type,
                    r.eligibility_score,
                )
                log.info(
                    "  CONVEX_PASS | %s %s score=%.2f",
                    symbol,
                    opt_type,
                    r.eligibility_score,
                )
            else:
                first_reason = r.rejection_reasons[0] if r.rejection_reasons else "Unknown"
                top_reason = (
                    (first_reason[:80] + "...")
                    if isinstance(first_reason, str) and len(first_reason) > 80
                    else first_reason
                )
                log.info(
                    "  QUALITY_GATE_REJECT | %s %s score=%.2f reason=%s",
                    symbol,
                    opt_type,
                    r.eligibility_score,
                    top_reason,
                )
                log.info(
                    "  CONVEX_REJECT | %s %s score=%.2f reason=%s",
                    symbol,
                    opt_type,
                    r.eligibility_score,
                    top_reason,
                )

        if not eligible_results:
            near_miss = [r for r in results if self._near_miss_low <= r.eligibility_score < base_min]
            if near_miss:
                top5 = [
                    f"{r.signal.get('symbol','?')}={r.eligibility_score:.2f}"
                    for r in sorted(near_miss, key=lambda x: x.eligibility_score, reverse=True)[:5]
                ]
                log.info(
                    "  QUALITY_GATE_near_miss | count=%d (score %.2f-%.2f) | top5: %s",
                    len(near_miss),
                    self._near_miss_low,
                    base_min,
                    ", ".join(top5),
                )
                log.info(
                    "  CONVEX_near_miss | count=%d (score %.2f-%.2f) | top5: %s",
                    len(near_miss),
                    self._near_miss_low,
                    base_min,
                    ", ".join(top5),
                )

        log.info(
            "  0DTE_QUALITY_GATE_STAGE | stage=filter_done | in=%d eligible=%d rejected=%d",
            len(signals),
            len(eligible_results),
            len(signals) - len(eligible_results),
        )
        log.info(
            "  0DTE_CONVEX_STAGE | stage=filter_done | in=%d eligible=%d rejected=%d",
            len(signals),
            len(eligible_results),
            len(signals) - len(eligible_results),
        )
        self._last_full_results = results
        return eligible_results

    def get_filter_stats(self, results: List[ConvexEligibilityResult]) -> Dict[str, Any]:
        if not results:
            return {
                "total_signals": 0,
                "eligible_count": 0,
                "rejected_count": 0,
                "eligibility_rate": 0.0,
                "avg_eligibility_score": 0.0,
                "top_rejection_reasons": [],
            }
        eligible = [r for r in results if r.is_eligible]
        rejected = [r for r in results if not r.is_eligible]
        rejection_reason_counts: Dict[str, int] = {}
        for result in rejected:
            for reason in result.rejection_reasons:
                rejection_reason_counts[reason] = rejection_reason_counts.get(reason, 0) + 1
        top_rejection_reasons = sorted(
            rejection_reason_counts.items(), key=lambda x: x[1], reverse=True
        )[:5]
        return {
            "total_signals": len(results),
            "eligible_count": len(eligible),
            "rejected_count": len(rejected),
            "eligibility_rate": len(eligible) / len(results) if results else 0.0,
            "avg_eligibility_score": sum(r.eligibility_score for r in results) / len(results) if results else 0.0,
            "top_rejection_reasons": [{"reason": r[0], "count": r[1]} for r in top_rejection_reasons],
        }


# Public alias — docs/alerts say Quality Gate; class file keeps Convex* for one release.
QualityGateFilter = ConvexEligibilityFilter
