#!/usr/bin/env python3
"""
Unified Portfolio Protection — Regime Protection @ 7:30 PT.

Two families (display taxonomy):

  Family A — Regime Protection (true Portfolio Protection)
    Red Day Patterns 1–3, Protection Day / split tape, composite risk (3b).
    Controlled by PORTFOLIO_PROTECTION_BLOCK_EXECUTION (observe vs block).

  Family B — Path Selection (NOT Regime PP)
    SO/0DTE PS floor, SO winner profile, SO adaptive expense, signal-level Red Day.
    Selection quality filters — never send Regime PP SKIP TRADING Telegram.

Layer 3b (composite risk) lives in prime_enhanced_red_day_detector.py.
Post-fill BAD DAY emergency exit remains in prime_stealth_trailing_tp.py.
"""

from __future__ import annotations

import logging
import os
import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _env_bool_first(*names: str, default: bool = False) -> bool:
    for name in names:
        raw = os.getenv(name)
        if raw is None or str(raw).strip() == "":
            continue
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


@dataclass
class PortfolioProtectionAssessment:
    """Result of unified 7:30 portfolio protection evaluation."""

    block_so_execution: bool = False
    trigger_id: Optional[str] = None  # red_day_pattern_* | protection_day | protection_day_split_tape
    primary_reason: Optional[str] = None
    metrics: Dict[str, Any] = field(default_factory=dict)
    triggers_fired: List[str] = field(default_factory=list)

    @property
    def is_red_day_pattern(self) -> bool:
        return bool(self.trigger_id and self.trigger_id.startswith("red_day_"))

    @property
    def is_protection_day(self) -> bool:
        return self.trigger_id in ("protection_day", "protection_day_split_tape")


# Canonical Gate 3a/3b trigger_ids — only these may set _red_day_filter_blocked (LIVE app).
PORTFOLIO_PROTECTION_TRIGGER_IDS = frozenset(
    {
        "red_day_pattern_1",
        "red_day_pattern_2",
        "red_day_pattern_3",
        "protection_day",
        "protection_day_split_tape",
        "enhanced_red_day_skip_execution",
    }
)

# Bearish / selloff / put-leaning PP regimes — disable Hold defer on CALL sides
# (Jul 29 Pattern 3: Hold@0.55 on CALL converted winners→losses). PUT Hold still allowed.
# Includes all Gate 3a Red Day patterns + Protection Day + Gate 3b enhanced skip.
PP_BEARISH_CALL_HOLD_DISABLE_TRIGGERS = frozenset(PORTFOLIO_PROTECTION_TRIGGER_IDS)

# In-process session stamp so exit monitor can see PP even when observe-only
# (observe-only does not set _red_day_filter_blocked).
_PP_SESSION: Dict[str, Any] = {
    "trigger_id": None,
    "primary_reason": None,
    "triggers_fired": [],
    "block_so_execution": False,
    "observe_only": False,
}


def clear_portfolio_protection_session() -> None:
    _PP_SESSION["trigger_id"] = None
    _PP_SESSION["primary_reason"] = None
    _PP_SESSION["triggers_fired"] = []
    _PP_SESSION["block_so_execution"] = False
    _PP_SESSION["observe_only"] = False


def publish_portfolio_protection_session(
    *,
    trigger_id: Optional[str] = None,
    primary_reason: Optional[str] = None,
    triggers_fired: Optional[List[str]] = None,
    block_so_execution: bool = False,
    observe_only: Optional[bool] = None,
) -> None:
    """Publish today's PP Gate 3a/3b outcome for Hold / exit consumers."""
    _PP_SESSION["trigger_id"] = str(trigger_id).strip() if trigger_id else None
    _PP_SESSION["primary_reason"] = primary_reason
    _PP_SESSION["triggers_fired"] = list(triggers_fired or [])
    _PP_SESSION["block_so_execution"] = bool(block_so_execution)
    if observe_only is None:
        _PP_SESSION["observe_only"] = portfolio_protection_observe_only_enabled()
    else:
        _PP_SESSION["observe_only"] = bool(observe_only)


def publish_portfolio_protection_session_from_assessment(
    assessment: Optional["PortfolioProtectionAssessment"],
) -> None:
    if assessment is None:
        return
    publish_portfolio_protection_session(
        trigger_id=assessment.trigger_id,
        primary_reason=assessment.primary_reason,
        triggers_fired=list(assessment.triggers_fired or []),
        block_so_execution=bool(assessment.block_so_execution),
    )


def get_portfolio_protection_session() -> Dict[str, Any]:
    return dict(_PP_SESSION)


def get_portfolio_protection_session_trigger() -> Optional[str]:
    tid = _PP_SESSION.get("trigger_id")
    if tid:
        return str(tid)
    return None


def _normalize_option_side_token(side: Any) -> str:
    s = str(side or "").strip().lower()
    if s in {"put", "p", "short"} or s.endswith("put") or "long_put" in s:
        return "put"
    if s in {"call", "c", "long"} or s.endswith("call") or "long_call" in s:
        return "call"
    return s


def pp_trigger_is_bearish_for_call_hold(trigger_id: Optional[str]) -> bool:
    tid = str(trigger_id or "").strip()
    if not tid:
        return False
    allowed = set(PP_BEARISH_CALL_HOLD_DISABLE_TRIGGERS)
    raw = os.getenv("OPTION_STEALTH_HOLD_EXIT_PP_CALL_DISABLE_TRIGGERS", "").strip()
    if raw:
        allowed = {x.strip() for x in raw.split(",") if x.strip()}
    return tid in allowed


def pp_regime_disables_call_hold(
    *,
    option_side: Any = "",
    trigger_id: Optional[str] = None,
) -> Tuple[bool, str]:
    """Hold Exit family removed — PP CALL Hold suppress gate permanently off."""
    return False, "pp_call_hold_gate_off"


def build_live_protection_pool(
    *,
    so_signals: Optional[List[Dict[str, Any]]] = None,
    dte_signals: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    LIVE 0DTE-only: union pool for Gate 3a/3b @ 7:30.

    One row per underlying — 0DTE collection rows first, then SO breakout rows
    overwrite (typically richer technical enrichment from rank path).
    """
    by_symbol: Dict[str, Dict[str, Any]] = {}

    for sig in dte_signals or []:
        if not isinstance(sig, dict):
            continue
        sym = str(sig.get("original_symbol") or sig.get("symbol") or "").strip().upper()
        if not sym:
            continue
        row = dict(sig)
        row["symbol"] = sym
        by_symbol[sym] = row

    for sig in so_signals or []:
        if not isinstance(sig, dict):
            continue
        sym = str(sig.get("symbol") or "").strip().upper()
        if not sym:
            continue
        row = dict(sig)
        row["symbol"] = sym
        by_symbol[sym] = row

    return list(by_symbol.values())


def raw_volume_ratio(sig: Dict[str, Any]) -> Optional[float]:
    """Real volume_ratio only — never default missing/zero to 1.0."""
    if bool(sig.get("volume_ratio_missing")):
        return None
    md = sig.get("metadata")
    if isinstance(md, dict) and bool(md.get("volume_ratio_missing")):
        return None
    vol = sig.get("volume_ratio", sig.get("orb_volume_ratio"))
    if vol is None:
        return None
    try:
        v = float(vol)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return v


def protection_pool_volume_coverage_pct(signals: List[Dict[str, Any]]) -> float:
    n = len(signals or [])
    if n <= 0:
        return 0.0
    with_vol = sum(1 for s in signals if raw_volume_ratio(s) is not None)
    return (with_vol / n) * 100.0


def protection_pool_enrichment_sufficient(
    signals: List[Dict[str, Any]],
) -> Tuple[bool, float]:
    """Require ≥80% of pool rows to carry real volume_ratio before PP evaluate."""
    pct = protection_pool_volume_coverage_pct(signals)
    min_pct = _env_float("PORTFOLIO_PROTECTION_VOLUME_COVERAGE_MIN_PCT", 80.0)
    return pct >= min_pct, pct


def protection_pool_metrics_valid(metrics: Optional[Dict[str, Any]]) -> bool:
    """Do not block when enrichment produced zero RSI/volume or sparse volume coverage."""
    if not metrics:
        return False
    avg_rsi = float(metrics.get("avg_rsi") or 0)
    avg_volume = float(metrics.get("avg_volume") or 0)
    pct_vol_cov = float(metrics.get("pct_volume_coverage") or 0)
    min_cov = _env_float("PORTFOLIO_PROTECTION_VOLUME_COVERAGE_MIN_PCT", 80.0)
    return avg_rsi > 0.0 and avg_volume > 0.0 and pct_vol_cov >= min_cov


def portfolio_protection_block_execution_enabled() -> bool:
    """When false, PP still evaluates and logs triggers but does not block execution."""
    return _env_bool("PORTFOLIO_PROTECTION_BLOCK_EXECUTION", True)


def portfolio_protection_observe_only_enabled() -> bool:
    """PP evaluation on; execution blocks and tier-policy arming off."""
    return _env_bool("ENABLE_PORTFOLIO_PROTECTION", True) and not portfolio_protection_block_execution_enabled()


def portfolio_protection_enabled() -> bool:
    """Family A Regime PP evaluation master switch."""
    return _env_bool("ENABLE_PORTFOLIO_PROTECTION", True)


def red_day_tier_gate_enabled() -> bool:
    """Execution-time Red Day CALL tier filter (ORB / TL / BO).

    Default follows ENABLE_PORTFOLIO_PROTECTION. Explicit
    ``ENABLE_RED_DAY_TIER_GATE=false`` pauses the tier gate even if a stale
    session flag exists (Aug 7: SC paused → no reliable PP pool).
    """
    if not _env_bool("ENABLE_PORTFOLIO_PROTECTION", True):
        return False
    return _env_bool("ENABLE_RED_DAY_TIER_GATE", True)


def would_portfolio_protection_trigger_block(
    assessment: Optional[PortfolioProtectionAssessment],
) -> bool:
    """True when Gate 3a would block on metrics alone (ignores observe-only override)."""
    if not assessment or not assessment.block_so_execution:
        return False
    trigger = str(assessment.trigger_id or "").strip()
    if trigger and trigger not in PORTFOLIO_PROTECTION_TRIGGER_IDS:
        return False
    return protection_pool_metrics_valid(assessment.metrics)


def should_apply_portfolio_protection_block(
    assessment: PortfolioProtectionAssessment,
) -> bool:
    """True when Gate 3a assessment should set _red_day_filter_blocked."""
    if not portfolio_protection_block_execution_enabled():
        return False
    return would_portfolio_protection_trigger_block(assessment)


def should_arm_0dte_tier_policy_for_pp(
    *,
    zero_outcome: Optional["SOZeroExecutionOutcome"] = None,
    assessment: Optional[PortfolioProtectionAssessment] = None,
) -> bool:
    """
    True when ORB1 Breakout + Trendline should inherit _red_day_filter_blocked (LIVE / blocking Demo).

    Arms on canonical Gate 3a/3b blocks or when Gates 3c–3f zero the pool and PP
    SKIP TRADING is sent (symbol gates path).

    Observe-only (PORTFOLIO_PROTECTION_BLOCK_EXECUTION=false): never arms tier policy —
    PP still evaluates/alerts, but CALL/LONG paths must not be cut for RESEARCH Demo.
    """
    if portfolio_protection_observe_only_enabled():
        return False
    if assessment and should_apply_portfolio_protection_block(assessment):
        return True
    if zero_outcome and zero_outcome.send_portfolio_protection_alert:
        return zero_outcome.rejection_category == "portfolio_protection"
    return False


def _valid_rsi(sig: Dict[str, Any]) -> Optional[float]:
    """Trusted RSI only — invent 50 / rsi_missing must not enter pool averages."""
    if bool(sig.get("rsi_missing")):
        return None
    md = sig.get("metadata")
    if isinstance(md, dict) and bool(md.get("rsi_missing")):
        return None
    rsi = sig.get("rsi", sig.get("rsi_14"))
    if rsi is None:
        return None
    try:
        v = float(rsi)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    return v


def _valid_volume(sig: Dict[str, Any]) -> Optional[float]:
    return raw_volume_ratio(sig)


def _valid_macd_histogram(sig: Dict[str, Any]) -> Optional[float]:
    if bool(sig.get("macd_missing")):
        return None
    md = sig.get("metadata")
    if isinstance(md, dict) and bool(md.get("macd_missing")):
        return None
    raw = sig.get("macd_histogram")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _valid_rs(sig: Dict[str, Any]) -> Optional[float]:
    rs = sig.get("rs_vs_spy")
    if rs is None:
        return None
    try:
        return float(rs)
    except (TypeError, ValueError):
        return None


def _valid_vwap(sig: Dict[str, Any]) -> Optional[float]:
    vd = sig.get("vwap_distance_pct")
    if vd is None:
        return None
    try:
        return float(vd)
    except (TypeError, ValueError):
        return None


def _compute_pool_metrics(signals: List[Dict[str, Any]]) -> Dict[str, Any]:
    num_signals = len(signals or [])
    if num_signals <= 0:
        return {"num_signals": 0}

    rsi_values = [r for r in (_valid_rsi(sig) for sig in signals) if r is not None]
    volume_values = [v for v in (_valid_volume(sig) for sig in signals) if v is not None]
    macd_values = [m for m in (_valid_macd_histogram(sig) for sig in signals) if m is not None]

    signals_with_low_rsi = sum(1 for r in rsi_values if r < 40)
    signals_with_high_rsi = sum(1 for r in rsi_values if r > 80)
    signals_with_weak_volume = sum(1 for v in volume_values if v < 1.0)
    signals_below_vwap = sum(
        1 for sig in signals if (_valid_vwap(sig) is not None and _valid_vwap(sig) < 0)
    )

    rsi_den = len(rsi_values) if rsi_values else 0
    vol_den = len(volume_values) if volume_values else 0
    # Weakness % over trusted samples only; 0 when no trusted RSI/volume (not invent).
    pct_low_rsi = (signals_with_low_rsi / rsi_den) * 100.0 if rsi_den else 0.0
    pct_high_rsi = (signals_with_high_rsi / rsi_den) * 100.0 if rsi_den else 0.0
    pct_weak_volume = (signals_with_weak_volume / vol_den) * 100.0 if vol_den else 0.0
    pct_below_vwap = (signals_below_vwap / num_signals) * 100.0

    avg_rsi = sum(rsi_values) / len(rsi_values) if rsi_values else 0.0
    avg_volume = sum(volume_values) / len(volume_values) if volume_values else 0.0
    avg_macd_histogram = (
        sum(macd_values) / len(macd_values) if macd_values else 0.0
    )
    rs_values = [v for v in (_valid_rs(sig) for sig in signals) if v is not None]
    vwap_dist_values = [v for v in (_valid_vwap(sig) for sig in signals) if v is not None]
    vol_ratios_for_median = list(volume_values)
    median_volume_ratio = (
        float(statistics.median(vol_ratios_for_median))
        if vol_ratios_for_median
        else 0.0
    )
    median_vwap_distance = (
        float(statistics.median(vwap_dist_values)) if vwap_dist_values else 0.0
    )
    pct_vwap_coverage = (
        (len(vwap_dist_values) / num_signals) * 100.0 if num_signals else 0.0
    )
    pct_volume_coverage = (
        (len(volume_values) / num_signals) * 100.0 if num_signals else 0.0
    )
    pct_rsi_coverage = (
        (len(rsi_values) / num_signals) * 100.0 if num_signals else 0.0
    )
    pct_macd_coverage = (
        (len(macd_values) / num_signals) * 100.0 if num_signals else 0.0
    )

    return {
        "num_signals": num_signals,
        "signals_analyzed": num_signals,
        "signals_with_low_rsi": signals_with_low_rsi,
        "signals_with_high_rsi": signals_with_high_rsi,
        "signals_with_weak_volume": signals_with_weak_volume,
        "signals_below_vwap": signals_below_vwap,
        "pct_low_rsi": pct_low_rsi,
        "pct_high_rsi": pct_high_rsi,
        "pct_weak_volume": pct_weak_volume,
        "pct_below_vwap": pct_below_vwap,
        "avg_rsi": avg_rsi,
        "avg_volume": avg_volume,
        "median_volume_ratio": median_volume_ratio,
        "avg_macd_histogram": avg_macd_histogram,
        "avg_rs_vs_spy": sum(rs_values) / len(rs_values) if rs_values else 0.0,
        "avg_vwap_distance": (
            sum(vwap_dist_values) / len(vwap_dist_values) if vwap_dist_values else 0.0
        ),
        "median_vwap_distance": median_vwap_distance,
        "pct_vwap_coverage": pct_vwap_coverage,
        "pct_volume_coverage": pct_volume_coverage,
        "pct_rsi_coverage": pct_rsi_coverage,
        "pct_macd_coverage": pct_macd_coverage,
        "rs_coverage": len(rs_values),
        "vwap_coverage": len(vwap_dist_values),
        "volume_coverage": len(volume_values),
        "rsi_coverage": len(rsi_values),
        "macd_coverage": len(macd_values),
    }


def _evaluate_red_day_patterns(metrics: Dict[str, Any]) -> Tuple[bool, Optional[str], Optional[str], Dict[str, bool]]:
    """
    Legacy Red Day Patterns 1–3 with momentum overrides.
    Returns (blocked, trigger_id, reason, pattern_flags).
    """
    num_signals = int(metrics.get("num_signals") or 0)
    if num_signals <= 0:
        return False, None, None, {}

    avg_rsi = float(metrics.get("avg_rsi") or 0)
    avg_volume = float(metrics.get("avg_volume") or 0)
    if avg_rsi == 0.0 or avg_volume == 0.0:
        return False, None, None, {}

    pct_low_rsi = float(metrics.get("pct_low_rsi") or 0)
    pct_high_rsi = float(metrics.get("pct_high_rsi") or 0)
    pct_weak_volume = float(metrics.get("pct_weak_volume") or 0)
    avg_macd_histogram = float(metrics.get("avg_macd_histogram") or 0)
    avg_rs_vs_spy = float(metrics.get("avg_rs_vs_spy") or 0)
    avg_vwap_distance = float(metrics.get("avg_vwap_distance") or 0)

    RED_DAY_RSI_LOW_THRESHOLD = 70.0
    RED_DAY_RSI_HIGH_THRESHOLD = 80.0
    RED_DAY_VOLUME_THRESHOLD = 80.0
    RED_DAY_PATTERN3_BREADTH_CONFIRM = 50.0
    MIN_MACD_HISTOGRAM = 0.0
    MIN_RS_VS_SPY = 2.0
    MIN_MACD_FOR_SOLO_OVERRIDE = 10.0
    MIN_VWAP_DISTANCE = 1.0

    pattern1_oversold = (
        pct_low_rsi >= RED_DAY_RSI_LOW_THRESHOLD
        and pct_weak_volume >= RED_DAY_VOLUME_THRESHOLD
    )
    if pattern1_oversold:
        if (avg_rsi < 10 and avg_macd_histogram > 0) or avg_macd_histogram > 0.5:
            pattern1_oversold = False
        elif avg_rsi > 20 and avg_volume > 0.2:
            pattern1_oversold = False

    pattern2_overbought = (
        pct_high_rsi >= RED_DAY_RSI_HIGH_THRESHOLD
        and pct_weak_volume >= RED_DAY_VOLUME_THRESHOLD
    )
    if pattern2_overbought:
        if avg_macd_histogram > MIN_MACD_HISTOGRAM and avg_rs_vs_spy > MIN_RS_VS_SPY:
            pattern2_overbought = False
        elif avg_macd_histogram > MIN_MACD_FOR_SOLO_OVERRIDE and (
            avg_rs_vs_spy == 0 or avg_rs_vs_spy is None
        ):
            pattern2_overbought = False
        elif avg_vwap_distance > MIN_VWAP_DISTANCE and avg_macd_histogram > MIN_MACD_HISTOGRAM:
            pattern2_overbought = False

    pattern3_weak_volume_only = (
        pct_weak_volume >= RED_DAY_VOLUME_THRESHOLD
        and (
            pct_low_rsi >= RED_DAY_PATTERN3_BREADTH_CONFIRM
            or pct_high_rsi >= RED_DAY_PATTERN3_BREADTH_CONFIRM
        )
    )
    if pattern3_weak_volume_only:
        if avg_macd_histogram > MIN_MACD_HISTOGRAM and avg_rs_vs_spy > MIN_RS_VS_SPY:
            pattern3_weak_volume_only = False
        elif avg_macd_histogram > MIN_MACD_FOR_SOLO_OVERRIDE and (
            avg_rs_vs_spy == 0 or avg_rs_vs_spy is None
        ):
            pattern3_weak_volume_only = False
        elif avg_vwap_distance > MIN_VWAP_DISTANCE and avg_macd_histogram > MIN_MACD_HISTOGRAM:
            pattern3_weak_volume_only = False

    flags = {
        "pattern1_oversold": pattern1_oversold,
        "pattern2_overbought": pattern2_overbought,
        "pattern3_weak_volume_only": pattern3_weak_volume_only,
    }

    if pattern1_oversold:
        reason = (
            f"Red Day Pattern 1: {pct_low_rsi:.0f}% oversold (RSI <40) + "
            f"{pct_weak_volume:.0f}% weak volume"
        )
        return True, "red_day_pattern_1", reason, flags
    if pattern2_overbought:
        reason = (
            f"Red Day Pattern 2: {pct_high_rsi:.0f}% overbought (RSI >80) + "
            f"{pct_weak_volume:.0f}% weak volume"
        )
        return True, "red_day_pattern_2", reason, flags
    if pattern3_weak_volume_only:
        reason = (
            f"Red Day Pattern 3: {pct_weak_volume:.0f}% weak volume "
            f"(≥{RED_DAY_VOLUME_THRESHOLD:.0f}%)"
        )
        return True, "red_day_pattern_3", reason, flags

    return False, None, None, flags


def _vwap_coverage_sufficient(metrics: Dict[str, Any]) -> bool:
    """Suppress split-tape trigger when most rows lack populated VWAP distance."""
    min_coverage = _env_float("PORTFOLIO_PROTECTION_VWAP_COVERAGE_MIN_PCT", 80.0)
    pct_cov = float(metrics.get("pct_vwap_coverage") or 0)
    return pct_cov >= min_coverage


def _evaluate_protection_day(
    metrics: Dict[str, Any],
) -> Tuple[bool, Optional[str], Optional[str], List[str]]:
    """
    Protection Day batch skip (Gate 3a complement to Red Day Patterns 1–3).

    Fires when either:
      A) below-VWAP breadth + weak median volume (legacy Rev 00369), or
      B) split/chop tape: median VWAP distance below floor + weak median volume
         (Rev 00375 — May 26 class).

    Returns (blocked, trigger_id, primary_reason, triggers_fired).
    """
    if not _env_bool("ENABLE_PORTFOLIO_PROTECTION_DAY", True):
        return False, None, None, []

    num_signals = int(metrics.get("num_signals") or 0)
    if num_signals <= 0:
        return False, None, None, []

    vwap_threshold = _env_float("PORTFOLIO_PROTECTION_VWAP_BELOW_PCT", 50.0)
    median_vol_max = _env_float("PORTFOLIO_PROTECTION_MEDIAN_VOLUME_MAX", 0.45)
    median_vwap_min = _env_float("PORTFOLIO_PROTECTION_MEDIAN_VWAP_MIN_PCT", 2.0)

    pct_below_vwap = float(metrics.get("pct_below_vwap") or 0)
    median_vol = float(metrics.get("median_volume_ratio") or 0)
    median_vwap = float(metrics.get("median_vwap_distance") or 0)

    fired: List[str] = []
    reasons: List[str] = []

    if pct_below_vwap >= vwap_threshold and median_vol < median_vol_max:
        fired.append("protection_day")
        reasons.append(
            f"{pct_below_vwap:.0f}% of pool below VWAP and "
            f"median volume_ratio {median_vol:.2f} < {median_vol_max:.2f}"
        )

    if (
        _env_bool("ENABLE_PORTFOLIO_PROTECTION_SPLIT_TAPE", True)
        and _vwap_coverage_sufficient(metrics)
        and median_vol < median_vol_max
        and median_vwap < median_vwap_min
    ):
        fired.append("protection_day_split_tape")
        reasons.append(
            f"median VWAP distance {median_vwap:.2f}% < {median_vwap_min:.2f}% and "
            f"median volume_ratio {median_vol:.2f} < {median_vol_max:.2f}"
        )

    if not fired:
        return False, None, None, []

    trigger_id = fired[0]
    primary_reason = "Protection Day: " + "; ".join(reasons)
    return True, trigger_id, primary_reason, fired


def evaluate_portfolio_protection(
    signals: List[Dict[str, Any]],
) -> PortfolioProtectionAssessment:
    """
    Unified 7:30 gate. Blocks ORB SO when Red Day OR Protection Day triggers.
    Red Day is evaluated first (legacy priority); Protection Day if Red Day clear.
    """
    if not _env_bool("ENABLE_PORTFOLIO_PROTECTION", True):
        return PortfolioProtectionAssessment(block_so_execution=False)

    metrics = _compute_pool_metrics(signals)
    assessment = PortfolioProtectionAssessment(metrics=dict(metrics))

    red_blocked, red_id, red_reason, pattern_flags = _evaluate_red_day_patterns(metrics)
    assessment.metrics.update(pattern_flags)

    if red_blocked:
        assessment.block_so_execution = True
        assessment.trigger_id = red_id
        assessment.primary_reason = red_reason
        assessment.triggers_fired.append(red_id or "red_day")
        publish_portfolio_protection_session_from_assessment(assessment)
        return assessment

    prot_blocked, prot_id, prot_reason, prot_fired = _evaluate_protection_day(metrics)
    if prot_blocked:
        assessment.block_so_execution = True
        assessment.trigger_id = prot_id
        assessment.primary_reason = prot_reason
        for tid in prot_fired:
            if tid not in assessment.triggers_fired:
                assessment.triggers_fired.append(tid)
        publish_portfolio_protection_session_from_assessment(assessment)
        return assessment

    # Clear prior session stamp when today's evaluate finds no Gate 3a fire.
    clear_portfolio_protection_session()
    return assessment


def assessment_to_legacy_metrics(assessment: PortfolioProtectionAssessment) -> Dict[str, Any]:
    """Unpack assessment for downstream code that expects inline Red Day locals."""
    m = dict(assessment.metrics or {})
    m.setdefault("pattern1_oversold", False)
    m.setdefault("pattern2_overbought", False)
    m.setdefault("pattern3_weak_volume_only", False)
    m["momentum_override"] = not (
        m.get("pattern1_oversold")
        or m.get("pattern2_overbought")
        or m.get("pattern3_weak_volume_only")
    ) and assessment.block_so_execution is False
    return m


# ---------------------------------------------------------------------------
# Family A / Family B taxonomy (display + alert routing)
# ---------------------------------------------------------------------------

# Family A — Regime Protection (canonical code → human label)
PP_REGIME_TRIGGER_LABELS: Dict[str, Tuple[str, str]] = {
    "red_day_pattern_1": ("pp.regime.red_day_1", "Red Day — Oversold"),
    "red_day_pattern_2": ("pp.regime.red_day_2", "Red Day — Overbought"),
    "red_day_pattern_3": ("pp.regime.red_day_3", "Red Day — Weak Volume"),
    "protection_day": ("pp.regime.protection_day", "Protection Day — Below VWAP"),
    "protection_day_split_tape": ("pp.regime.split_tape", "Protection Day — Split Tape"),
    "enhanced_red_day_skip_execution": ("pp.regime.composite_skip", "Composite Risk — Skip"),
    "enhanced_red_day": ("pp.regime.composite_skip", "Composite Risk — Skip"),
    "red_day_portfolio": ("pp.regime.batch_block", "Regime — Batch Block"),
    "portfolio_protection": ("pp.regime", "Portfolio Protection — Regime"),
}

# Family B — Path Selection (reject_stage → canonical code, human label)
SELECTION_STAGE_LABELS: Dict[str, Tuple[str, str]] = {
    "zero_priority_score": ("sel.so.ps_floor", "SO Priority Score Floor"),
    "winner_profile_filter": ("sel.so.winner_profile", "SO Winner Profile"),
    "adaptive_filter": ("sel.so.adaptive", "SO Adaptive Expense"),
    "signal_level_red_day": ("sel.so.signal_red_day", "SO Signal-Level Red Day"),
    "priority_score_floor": ("sel.0dte.ps_floor", "0DTE Priority Score Floor"),
}

# Regime reject stages on SO terminal rows (Family A)
REGIME_REJECT_STAGES = frozenset({"red_day_portfolio", "enhanced_red_day"})

# Path selection reject stages (Family B — never Regime PP SKIP TRADING)
SELECTION_REJECT_STAGES = frozenset(
    {
        "zero_priority_score",
        "signal_level_red_day",
        "winner_profile_filter",
        "adaptive_filter",
    }
)

# Back-compat union (logs / older callers)
PP_GATE_REJECT_STAGES = frozenset(REGIME_REJECT_STAGES | SELECTION_REJECT_STAGES)

CAPITAL_REJECT_STAGES = frozenset(
    {
        "batch_sizing",
        "demo_execution",
    }
)


def canonical_pp_trigger_code(trigger_id: Optional[str]) -> str:
    """Map legacy / raw trigger_id to Family A canonical code."""
    raw = str(trigger_id or "").strip()
    if not raw:
        return "pp.regime"
    if raw in PP_REGIME_TRIGGER_LABELS:
        return PP_REGIME_TRIGGER_LABELS[raw][0]
    if raw.startswith("pp.regime"):
        return raw
    if raw.startswith("sel."):
        return raw
    return PP_REGIME_TRIGGER_LABELS.get(raw, ("pp.regime", ""))[0] or f"pp.regime.{raw}"


def human_pp_trigger_label(trigger_id: Optional[str]) -> str:
    """Human label for Regime or Selection trigger ids."""
    raw = str(trigger_id or "").strip()
    if not raw:
        return "Portfolio Protection — Regime"
    if raw in PP_REGIME_TRIGGER_LABELS:
        return PP_REGIME_TRIGGER_LABELS[raw][1]
    for _stage, (code, label) in SELECTION_STAGE_LABELS.items():
        if raw == code or raw == _stage:
            return label
    if raw in SELECTION_STAGE_LABELS:
        return SELECTION_STAGE_LABELS[raw][1]
    # Title-case fallback without noisy "Portfolio Protection Symbol Gates"
    return raw.replace("_", " ").replace(".", " — ")


def pp_path_impact_text(*, observe_only: bool) -> str:
    """Path-impact block for Regime PP Telegram (observe vs blocking)."""
    if observe_only:
        return (
            "🛤 <b>Path impact:</b>\n"
            "          • ORB SO: <b>continue</b>\n"
            "          • ORB1 Breakout: <b>continue</b> (no tier gate)\n"
            "          • Trendline / Breakout: <b>continue</b>"
        )
    return (
        "🛤 <b>Path impact:</b>\n"
        "          • ORB SO: <b>blocked</b> (batch)\n"
        "          • ORB1 Breakout CALLs: non–Tier 1 <b>blocked</b>; Tier 1 + PUTs continue\n"
        "          • Trendline: same tier gate\n"
        "          • Breakout: continue / inherit as configured"
    )


def format_so_execution_skip_reason(
    trigger_id: Optional[str],
    primary_reason: Optional[str],
) -> str:
    """Human-readable reason for 7:30 SO execution alert when Regime PP blocked SO."""
    label = human_pp_trigger_label(trigger_id)
    if primary_reason:
        return f"Blocked by Portfolio Protection (Regime) — {label}: {primary_reason}"
    return "Blocked by Portfolio Protection (Regime) at 7:30 PT (no SO fills)."


def format_0dte_execution_skip_reason(
    trigger_id: Optional[str],
    primary_reason: Optional[str],
) -> str:
    """Human-readable reason for 7:30 ORB1 Breakout execution alert when Regime PP blocked."""
    label = human_pp_trigger_label(trigger_id)
    if primary_reason:
        return f"Blocked by Portfolio Protection (Regime) — {label}: {primary_reason}"
    return "Blocked by Portfolio Protection (Regime) at 7:30 PT (no fills)."


def format_selection_reject_reason(
    trigger_id: Optional[str],
    primary_reason: Optional[str],
) -> str:
    """SO/0DTE zero-fill reason for Family B path selection (not Regime PP)."""
    label = human_pp_trigger_label(trigger_id) if trigger_id else "Path Selection"
    if primary_reason:
        return f"Selection rejects (not Regime PP) — {label}: {primary_reason}"
    return "Selection rejects (not Regime PP): execution pool empty after path filters."


@dataclass
class ZeroExecutionOutcome:
    """Classification for 0-fill batch @ 7:30 PT (SO or 0DTE alert routing)."""

    # portfolio_protection | selection_filter | insufficient_capital | priority_score_gate
    rejection_category: Optional[str] = None
    trigger_id: Optional[str] = None
    primary_reason: Optional[str] = None
    execution_alert_reason: Optional[str] = None
    send_portfolio_protection_alert: bool = False

    @property
    def so_alert_reason(self) -> Optional[str]:
        """Back-compat alias used by SO Step-5 alert routing and legacy tests."""
        return self.execution_alert_reason


# Back-compat alias (DEMO SO path)
SOZeroExecutionOutcome = ZeroExecutionOutcome


def _reject_stage(signal: Dict[str, Any]) -> str:
    return str(signal.get("reject_stage") or signal.get("stage") or "").strip().lower()


def summarize_selection_rejects(
    rejected_signals: List[Dict[str, Any]],
) -> Tuple[str, str, List[str]]:
    """
    Family B summary for SO execution alert.

    Returns (canonical_trigger_id, primary_reason, per-stage lines with symbols).
    """
    by_stage: Dict[str, List[str]] = {}
    for sig in rejected_signals or []:
        stage = _reject_stage(sig)
        if stage not in SELECTION_REJECT_STAGES:
            continue
        sym = str(sig.get("symbol") or "?").strip().upper() or "?"
        by_stage.setdefault(stage, []).append(sym)

    if not by_stage:
        return "sel.so", "No path-selection rejects recorded.", []

    lines: List[str] = []
    parts: List[str] = []
    for stage in (
        "zero_priority_score",
        "winner_profile_filter",
        "adaptive_filter",
        "signal_level_red_day",
    ):
        syms = by_stage.get(stage) or []
        if not syms:
            continue
        _code, label = SELECTION_STAGE_LABELS[stage]
        short = label.replace("SO ", "")
        lines.append(f"{short}: {len(syms)}  — {', '.join(syms)}")
        parts.append(f"{short}: {len(syms)} symbol(s)")

    if len(by_stage) == 1:
        only = next(iter(by_stage))
        trigger_id = SELECTION_STAGE_LABELS[only][0]
    else:
        trigger_id = "sel.so.multi"

    reason = "; ".join(parts) if parts else "Path selection cleared the SO execution pool."
    return trigger_id, reason, lines


def summarize_symbol_gate_block_reason(rejected_signals: List[Dict[str, Any]]) -> Tuple[str, str]:
    """
    Build trigger_id + reason for SO terminal rejects.

    Prefer Family B selection wording. Regime stages only if present alone.
    """
    stage_counts: Dict[str, int] = {}
    for sig in rejected_signals or []:
        stage = _reject_stage(sig)
        if stage:
            stage_counts[stage] = stage_counts.get(stage, 0) + 1

    selection_counts = {k: v for k, v in stage_counts.items() if k in SELECTION_REJECT_STAGES}
    regime_counts = {k: v for k, v in stage_counts.items() if k in REGIME_REJECT_STAGES}

    if selection_counts and not regime_counts:
        trigger_id, reason, _lines = summarize_selection_rejects(rejected_signals)
        return trigger_id, reason

    parts: List[str] = []
    if regime_counts.get("enhanced_red_day"):
        parts.append("Composite Risk — Skip")
    if regime_counts.get("red_day_portfolio"):
        parts.append("Regime — Batch Block")
    if selection_counts:
        _tid, sel_reason, _ = summarize_selection_rejects(rejected_signals)
        parts.append(sel_reason)

    if regime_counts and not selection_counts:
        if regime_counts.get("enhanced_red_day"):
            return "pp.regime.composite_skip", parts[0] if parts else "Composite Risk — Skip"
        return "pp.regime.batch_block", parts[0] if parts else "Regime — Batch Block"

    trigger_id = "sel.so.multi" if selection_counts else "pp.regime"
    reason = "; ".join(parts) if parts else "Execution pool cleared before sizing."
    return trigger_id, reason


def classify_so_zero_execution_outcome(
    *,
    executed_count: int,
    rejected_signals: Optional[List[Dict[str, Any]]],
    red_day_filter_blocked: bool = False,
    red_day_trigger_id: Optional[str] = None,
    red_day_primary_reason: Optional[str] = None,
) -> ZeroExecutionOutcome:
    """SO batch — Regime PP vs path selection vs capital.

    Family B (PS floor / winner / adaptive) never sets ``send_portfolio_protection_alert``.
    Regime SKIP TRADING only when ``red_day_filter_blocked`` and not observe-only.
    """
    if executed_count > 0:
        return ZeroExecutionOutcome()

    rejected = list(rejected_signals or [])
    selection_stages = {_reject_stage(s) for s in rejected} & SELECTION_REJECT_STAGES
    regime_stages = {_reject_stage(s) for s in rejected} & REGIME_REJECT_STAGES
    capital_stages = {_reject_stage(s) for s in rejected} & CAPITAL_REJECT_STAGES

    def _selection_outcome() -> ZeroExecutionOutcome:
        trigger, reason, _lines = summarize_selection_rejects(rejected)
        return ZeroExecutionOutcome(
            rejection_category="selection_filter",
            trigger_id=trigger,
            primary_reason=reason,
            execution_alert_reason=format_selection_reject_reason(trigger, reason),
            send_portfolio_protection_alert=False,
        )

    # Observe-only: never claim Regime SO block; surface selection / capital only.
    if portfolio_protection_observe_only_enabled():
        if rejected and selection_stages:
            return _selection_outcome()
        if rejected and capital_stages and not selection_stages:
            return ZeroExecutionOutcome(rejection_category="insufficient_capital")
        return ZeroExecutionOutcome()

    if red_day_filter_blocked:
        trigger = red_day_trigger_id or "portfolio_protection"
        reason = red_day_primary_reason or "Portfolio Protection blocked ORB SO batch @ 7:30 PT."
        return ZeroExecutionOutcome(
            rejection_category="portfolio_protection",
            trigger_id=trigger,
            primary_reason=reason,
            execution_alert_reason=format_so_execution_skip_reason(trigger, reason),
            send_portfolio_protection_alert=True,
        )

    # Family B only — selection filter, no Regime PP Telegram
    if rejected and selection_stages and not regime_stages:
        return _selection_outcome()

    if rejected and capital_stages and not selection_stages and not regime_stages:
        return ZeroExecutionOutcome(rejection_category="insufficient_capital")

    # Regime stages on rows without flag (rare) — still Regime family, but only alert if blocking
    if rejected and regime_stages and not selection_stages:
        trigger, reason = summarize_symbol_gate_block_reason(rejected)
        return ZeroExecutionOutcome(
            rejection_category="portfolio_protection",
            trigger_id=trigger,
            primary_reason=reason,
            execution_alert_reason=format_so_execution_skip_reason(trigger, reason),
            send_portfolio_protection_alert=True,
        )

    # Mixed selection + capital → prefer selection messaging (not PP)
    if rejected and selection_stages:
        return _selection_outcome()

    return ZeroExecutionOutcome()


def classify_0dte_zero_execution_outcome(
    *,
    executed_count: int,
    red_day_filter_blocked: bool = False,
    red_day_trigger_id: Optional[str] = None,
    red_day_primary_reason: Optional[str] = None,
    zero_ps_dropped_count: int = 0,
    hard_gate_passed_count: int = 0,
    collection_count: int = 0,
) -> ZeroExecutionOutcome:
    """
    ORB1 Breakout batch @ 7:30 — PP (3a/3b) vs selection PS floor vs pipeline filters.

    Portfolio Protection alerts only for true PP batch blocks (3a/3b).
    Priority-score floor clears are selection pipeline (not PP).
    Convex / Hard Gate / chain rejects alone → not Portfolio Protection.

    Observe-only (``PORTFOLIO_PROTECTION_BLOCK_EXECUTION=false``): never emit PP
    SKIP-TRADING for 0DTE — Gate 3a/3b observe alerts already fire from Step 5.
    """
    if executed_count > 0:
        return ZeroExecutionOutcome()

    if portfolio_protection_observe_only_enabled():
        # Selection / PS-floor outcomes only — never brand as PP block while observe-only.
        if zero_ps_dropped_count > 0 and hard_gate_passed_count > 0:
            trigger = "priority_score_floor"
            reason = (
                f"PS floor: {zero_ps_dropped_count} 0DTE row(s) at or below priority score floor; "
                f"{hard_gate_passed_count} passed Hard Gate but none remained for execution sizing."
            )
            return ZeroExecutionOutcome(
                rejection_category="priority_score_gate",
                trigger_id=trigger,
                primary_reason=reason,
                execution_alert_reason=format_0dte_execution_skip_reason(trigger, reason),
                send_portfolio_protection_alert=False,
            )
        if zero_ps_dropped_count > 0 and collection_count > 0 and hard_gate_passed_count == 0:
            trigger = "priority_score_floor"
            reason = (
                f"PS floor: {zero_ps_dropped_count} of {collection_count} 0DTE collection row(s) "
                "at or below priority score floor (execution pool empty)."
            )
            return ZeroExecutionOutcome(
                rejection_category="priority_score_gate",
                trigger_id=trigger,
                primary_reason=reason,
                execution_alert_reason=format_0dte_execution_skip_reason(trigger, reason),
                send_portfolio_protection_alert=False,
            )
        return ZeroExecutionOutcome()

    if red_day_filter_blocked:
        # PP armed allows Tier 1 CALL + all PUT through to execution. A non-empty Hard
        # Gate queue means zero fills came from Convex/Hard Gate/execution — not a batch block.
        if hard_gate_passed_count > 0:
            return ZeroExecutionOutcome()
        trigger = red_day_trigger_id or "portfolio_protection"
        reason = red_day_primary_reason or (
            "Portfolio Protection blocked 0DTE batch policy @ 7:30 PT "
            "(Tier 1 CALL + all PUT may continue when configured)."
        )
        return ZeroExecutionOutcome(
            rejection_category="portfolio_protection",
            trigger_id=trigger,
            primary_reason=reason,
            execution_alert_reason=format_0dte_execution_skip_reason(trigger, reason),
            send_portfolio_protection_alert=True,
        )

    if zero_ps_dropped_count > 0 and hard_gate_passed_count > 0:
        trigger = "priority_score_floor"
        reason = (
            f"PS floor: {zero_ps_dropped_count} 0DTE row(s) at or below priority score floor; "
            f"{hard_gate_passed_count} passed Hard Gate but none remained for execution sizing."
        )
        return ZeroExecutionOutcome(
            rejection_category="priority_score_gate",
            trigger_id=trigger,
            primary_reason=reason,
            execution_alert_reason=format_0dte_execution_skip_reason(trigger, reason),
            send_portfolio_protection_alert=False,
        )

    if zero_ps_dropped_count > 0 and collection_count > 0 and hard_gate_passed_count == 0:
        trigger = "priority_score_floor"
        reason = (
            f"PS floor: {zero_ps_dropped_count} of {collection_count} 0DTE collection row(s) "
            "at or below priority score floor (execution pool empty)."
        )
        return ZeroExecutionOutcome(
            rejection_category="priority_score_gate",
            trigger_id=trigger,
            primary_reason=reason,
            execution_alert_reason=format_0dte_execution_skip_reason(trigger, reason),
            send_portfolio_protection_alert=False,
        )

    return ZeroExecutionOutcome()


_TIER1_TOKENS = frozenset({"1", "TIER 1", "T1"})


def load_0dte_tier1_symbols(dte_list_path: Optional[str] = None) -> set:
    """Tier 1 symbols from ``0dte_list.csv`` (ORB1 Breakout + Trendline Red Day gate)."""
    tier1: set = set()
    if dte_list_path:
        paths = [dte_list_path]
    else:
        extra = os.getenv("0DTE_WATCHLIST_CSV_EXTRA_PATHS", "").strip()
        paths = [p.strip() for p in extra.split(",") if p.strip()] if extra else []
        env_path = (
            os.getenv("0DTE_WATCHLIST_PATH", "").strip()
            or os.getenv("0DTE_LIST_PATH", "").strip()
        )
        if env_path:
            paths.append(env_path)
        paths.extend(
            [
                "/app/data/watchlist/0dte_list.csv",
                "data/watchlist/0dte_list.csv",
            ]
        )
    try:
        import pandas as pd

        df = None
        resolved_path = ""
        for path in paths:
            if os.path.exists(path):
                df = pd.read_csv(path, comment="#")
                resolved_path = path
                break
        if df is None:
            log.warning("0DTE_TIER1 | action=load_skipped | reason=missing_path | paths=%s", paths[:3])
            return tier1
        if "symbol" in df.columns and "tier" in df.columns:
            symbol_col, tier_col = "symbol", "tier"
        else:
            symbol_col, tier_col = df.columns[0], df.columns[1]
        for _, row in df.iterrows():
            symbol_u = str(row.get(symbol_col, "")).strip().upper()
            tier_raw = row.get(tier_col, "")
            try:
                tier_n = str(int(float(tier_raw))).strip()
            except (TypeError, ValueError):
                tier_n = str(tier_raw or "").strip().upper()
            if symbol_u and (tier_n == "1" or tier_n in _TIER1_TOKENS):
                tier1.add(symbol_u)
        log.debug("0DTE_TIER1 | action=loaded | path=%s | count=%d", resolved_path, len(tier1))
    except Exception as err:
        log.warning("0DTE_TIER1 | action=load_failed | detail=%s", err)
    return tier1


def passes_red_day_0dte_style_gate(
    *,
    symbol: str,
    option_side_or_type: str = "",
    direction: str = "",
    tier1_symbols: Optional[set] = None,
    red_day_active: bool = True,
    allow_tier1_call: bool = True,
    allow_put: bool = True,
) -> Tuple[bool, str]:
    """
    Canonical Red Day tier check for ORB1 Breakout + Trendline + UAR.

    On Red Day: allow Tier 1 CALLs, all PUTs/SHORT; block other CALLs.
    Returns ``(allowed, reason_code)``.
    """
    if not red_day_active:
        return True, ""
    if not red_day_tier_gate_enabled():
        return True, "red_day_tier_gate_paused"
    sym = str(symbol or "").strip().upper()
    opt = str(option_side_or_type or "").strip().lower()
    dir_u = str(direction or "").strip().upper()
    tier1 = tier1_symbols if tier1_symbols is not None else load_0dte_tier1_symbols()
    if allow_tier1_call and sym in tier1:
        return True, "tier1_override"
    if allow_put and (dir_u == "SHORT" or opt == "put"):
        return True, "put_or_short_lane"
    if opt == "call" or dir_u in ("LONG", "BULL"):
        return False, "red_day_call_non_tier1"
    return True, ""


def _signal_option_side_for_red_day_gate(signal: Any) -> str:
    for attr in ("option_type", "option_side", "option_type_label"):
        raw = getattr(signal, attr, None)
        if raw is None and isinstance(signal, dict):
            raw = signal.get(attr)
        text = str(raw or "").strip().lower()
        if not text:
            continue
        if "put" in text:
            return "put"
        if "call" in text:
            return "call"
        return text
    return ""


def filter_signals_by_red_day_0dte_style_gate(
    signals: List[Any],
    *,
    red_day_active: bool,
    tier1_symbols: Optional[set] = None,
    allow_tier1_call: bool = True,
    allow_put: bool = True,
) -> Tuple[List[Any], int, int]:
    """
    Single ORB pre-execute Red Day trim path (uses ``passes_red_day_0dte_style_gate``).

    Returns ``(kept, dropped_non_tier1_call_count, kept_tier1_call_count)``.
    """
    if not red_day_active or not red_day_tier_gate_enabled():
        return list(signals or []), 0, 0
    tier1 = tier1_symbols if tier1_symbols is not None else load_0dte_tier1_symbols()
    kept: List[Any] = []
    dropped = 0
    kept_tier1_long = 0
    for signal in signals or []:
        if isinstance(signal, dict):
            sym = str(signal.get("symbol", "") or "").upper()
            direction = str(signal.get("direction", "") or "")
        else:
            sym = str(getattr(signal, "symbol", "") or "").upper()
            direction = str(getattr(signal, "direction", "") or "")
        option_side = _signal_option_side_for_red_day_gate(signal)
        allowed, reason = passes_red_day_0dte_style_gate(
            symbol=sym,
            option_side_or_type=option_side,
            direction=direction,
            tier1_symbols=tier1,
            red_day_active=True,
            allow_tier1_call=allow_tier1_call,
            allow_put=allow_put,
        )
        if not allowed:
            dropped += 1
            continue
        kept.append(signal)
        if reason == "tier1_override" and option_side != "put" and str(direction).upper() != "SHORT":
            kept_tier1_long += 1
    return kept, dropped, kept_tier1_long


# Aliases for optional signal-level filter in prime_trading_system.py
valid_rsi = _valid_rsi
valid_volume = _valid_volume
valid_macd_histogram = _valid_macd_histogram
valid_rs_vs_spy = _valid_rs
valid_vwap_distance = _valid_vwap


def _skip_zero_priority_score_enabled(path: str = "so") -> bool:
    path_u = str(path or "so").strip().lower()
    if path_u in ("0dte", "dte", "orb_0dte"):
        if os.getenv("0DTE_SKIP_ZERO_PRIORITY_SCORE") is not None:
            return _env_bool("0DTE_SKIP_ZERO_PRIORITY_SCORE", True)
    return _env_bool("SO_SKIP_ZERO_PRIORITY_SCORE", True)


def _min_priority_score_floor(path: str = "so") -> float:
    path_u = str(path or "so").strip().lower()
    if path_u in ("0dte", "dte", "orb_0dte"):
        if os.getenv("0DTE_MIN_PRIORITY_SCORE_FOR_EXECUTION") is not None:
            return _env_float("0DTE_MIN_PRIORITY_SCORE_FOR_EXECUTION", 0.30)
    return _env_float("SO_MIN_PRIORITY_SCORE_FOR_EXECUTION", 0.001)


@dataclass
class PriorityScoreExecutionFilterResult:
    """Symbol-level execution filter: drop PS at/below floor; preserve PS rank order."""

    kept: List[Dict[str, Any]] = field(default_factory=list)
    dropped: List[Dict[str, Any]] = field(default_factory=list)
    min_priority_score: float = 0.001
    enabled: bool = True

    @property
    def dropped_symbols(self) -> List[str]:
        return [
            str(s.get("symbol", "")).strip()
            for s in self.dropped
            if s.get("symbol")
        ]


def get_signal_priority_score(signal: Dict[str, Any]) -> float:
    for key in ("priority_score", "so_priority_score"):
        try:
            val = signal.get(key)
            if val is not None:
                return float(val)
        except (TypeError, ValueError):
            continue
    return 0.0


def get_dte0_signal_field(signal: Any, key: str, default: float = 0.0) -> float:
    """Read enrichment field from DTE0Signal.orb_signal or dict row."""
    orb = getattr(signal, "orb_signal", None)
    if isinstance(orb, dict) and key in orb:
        try:
            return float(orb.get(key) if orb.get(key) is not None else default)
        except (TypeError, ValueError):
            pass
    if isinstance(signal, dict):
        try:
            return float(signal.get(key) if signal.get(key) is not None else default)
        except (TypeError, ValueError):
            pass
    try:
        val = getattr(signal, key, default)
        return float(val if val is not None else default)
    except (TypeError, ValueError):
        return default


def _shallow_e14_enabled() -> bool:
    # Match Quality Gate / ORB0DTE.env lock — admit OFF (Jun 26 E11 conflict).
    return _env_bool("0DTE_SHALLOW_E14_ENABLED", False)


def _signal_to_convex_dict(signal: Any) -> Dict[str, Any]:
    """Build ORB dict for Convex E14 check from DTE0Signal or dict."""
    if isinstance(signal, dict):
        row = dict(signal)
    else:
        row = {}
        orb = getattr(signal, "orb_signal", None)
        if isinstance(orb, dict):
            row.update(orb)
        row["symbol"] = getattr(signal, "symbol", row.get("symbol"))
        direction = str(getattr(signal, "direction", "") or row.get("side") or "LONG").upper()
        row["side"] = direction
        row["direction"] = direction
    return row


def is_dte0_shallow_e14_eligible(
    signal: Any,
    enabled: Optional[bool] = None,
    call_macd_min: Optional[float] = None,
    put_macd_max: Optional[float] = None,
) -> Tuple[bool, Optional[str]]:
    """Deprecated wrapper — canonical: convex_eligibility_filter.e14_shallow_gates_pass."""
    from easy0DTE.modules.convex_eligibility_filter import e14_shallow_gates_pass

    ok, msg = e14_shallow_gates_pass(
        _signal_to_convex_dict(signal),
        enabled=enabled,
        call_macd_min=call_macd_min,
        put_macd_max=put_macd_max,
    )
    return ok, None if ok else msg


@dataclass
class DTE0ShallowE14FilterResult:
    kept: List[Any] = field(default_factory=list)
    dropped: List[Any] = field(default_factory=list)
    enabled: bool = True

    @property
    def dropped_labels(self) -> List[str]:
        out: List[str] = []
        for s in self.dropped:
            sym = str(getattr(s, "symbol", "") or "").strip()
            side = str(getattr(s, "direction", "") or "").upper()
            if not sym:
                continue
            out.append(f"{sym}_{'PUT' if side == 'SHORT' else 'CALL'}")
        return out


def filter_dte0_signals_shallow_e14(
    signals: List[Any],
    enabled: Optional[bool] = None,
) -> DTE0ShallowE14FilterResult:
    """Deprecated — E14 shallow runs in Quality Gate (`e14`, default OFF)."""
    if enabled is None:
        enabled = _shallow_e14_enabled()
    if not enabled or not signals:
        return DTE0ShallowE14FilterResult(kept=list(signals or []), dropped=[], enabled=enabled)

    kept: List[Any] = []
    dropped: List[Any] = []
    for sig in signals:
        ok, _ = is_dte0_shallow_e14_eligible(sig, enabled=True)
        if ok:
            kept.append(sig)
        else:
            dropped.append(sig)
    return DTE0ShallowE14FilterResult(kept=kept, dropped=dropped, enabled=True)


def log_dte0_shallow_e14_filter(result: DTE0ShallowE14FilterResult) -> None:
    log.info(
        "0DTE_SHALLOW_E14 | deprecated_wrapper | kept=%d | dropped=%d | use Convex layer 4",
        len(result.kept),
        len(result.dropped),
    )


from easy0DTE.modules.dte0_execution_filters import (  # noqa: E402
    DTE0PriorityScoreExecutionFilterResult as DTE0PriorityScoreExecutionFilterResult,
    filter_dte0_signals_below_min_priority_score,
    get_dte0_signal_priority_score,
    is_dte0_priority_score_eligible_for_execution,
    log_dte0_priority_score_execution_filter,
    rerank_dte0_signals_by_priority,
)


def is_priority_score_eligible_for_execution(
    signal: Dict[str, Any],
    min_priority_score: Optional[float] = None,
    enabled: Optional[bool] = None,
) -> bool:
    """True when signal may proceed to ORB SO execution (ranking is unchanged upstream)."""
    if enabled is None:
        enabled = _skip_zero_priority_score_enabled("so")
    if not enabled:
        return True
    floor = (
        _min_priority_score_floor("so")
        if min_priority_score is None
        else float(min_priority_score)
    )
    return get_signal_priority_score(signal) > floor


def filter_signals_below_min_priority_score(
    signals: List[Dict[str, Any]],
    min_priority_score: Optional[float] = None,
    enabled: Optional[bool] = None,
) -> PriorityScoreExecutionFilterResult:
    """
    Drop symbols with priority_score <= floor from the execution pool.

    Runs after portfolio-level protection passes. Full PS-ranked pool remains in
    execution_window_input snapshot; this only trims the list sent to fills.
    """
    if enabled is None:
        enabled = _skip_zero_priority_score_enabled("so")
    floor = (
        _min_priority_score_floor("so")
        if min_priority_score is None
        else float(min_priority_score)
    )
    if not enabled or not signals:
        return PriorityScoreExecutionFilterResult(
            kept=list(signals or []),
            dropped=[],
            min_priority_score=floor,
            enabled=enabled,
        )

    kept: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []
    for sig in signals:
        if is_priority_score_eligible_for_execution(sig, min_priority_score=floor, enabled=True):
            kept.append(sig)
        else:
            dropped.append(sig)

    return PriorityScoreExecutionFilterResult(
        kept=kept,
        dropped=dropped,
        min_priority_score=floor,
        enabled=True,
    )


def log_priority_score_execution_filter(result: PriorityScoreExecutionFilterResult) -> None:
    if not result.enabled:
        log.info("PORTFOLIO_PROTECTION_ZERO_PS | disabled | SO_SKIP_ZERO_PRIORITY_SCORE=false")
        return
    if not result.dropped:
        log.info(
            "PORTFOLIO_PROTECTION_ZERO_PS | pass | floor=%.4f | pool=%d",
            result.min_priority_score,
            len(result.kept),
        )
        return
    log.warning(
        "PORTFOLIO_PROTECTION_ZERO_PS | dropped=%d | kept=%d | floor=%.4f | symbols=%s",
        len(result.dropped),
        len(result.kept),
        result.min_priority_score,
        ",".join(result.dropped_symbols),
    )
    for sig in result.dropped:
        sym = str(sig.get("symbol", "?"))
        ps = get_signal_priority_score(sig)
        log.info(
            "SO_PIPELINE stage=zero_priority_score | symbol=%s | reason=priority_score_at_or_below_floor | "
            "ps=%.4f | floor=%.4f | path=SO",
            sym,
            ps,
            result.min_priority_score,
        )


def log_portfolio_protection_check(
    assessment: PortfolioProtectionAssessment,
    num_signals: int,
) -> None:
    m = assessment.metrics or {}
    log.info("=" * 80)
    log.info("🛡️ PORTFOLIO PROTECTION CHECK (unified — Rev 00369 + split-tape 00375)")
    log.info("=" * 80)
    log.info("   Signals analyzed: %s", num_signals)
    log.info(
        "   Pool: %.0f%% below VWAP | median vol %.3f | median VWAP %.2f%% | "
        "%.0f%% weak vol | oversold %.0f%% | overbought %.0f%%",
        m.get("pct_below_vwap", 0),
        m.get("median_volume_ratio", 0),
        m.get("median_vwap_distance", 0),
        m.get("pct_weak_volume", 0),
        m.get("pct_low_rsi", 0),
        m.get("pct_high_rsi", 0),
    )
    log.info(
        "   Avg RSI %.1f | Avg vol %.2fx | Avg MACD %.3f | Avg VWAP dist %.2f%% | "
        "VWAP field coverage %.0f%% | volume field coverage %.0f%%",
        m.get("avg_rsi", 0),
        m.get("avg_volume", 0),
        m.get("avg_macd_histogram", 0),
        m.get("avg_vwap_distance", 0),
        m.get("pct_vwap_coverage", 0),
        m.get("pct_volume_coverage", 0),
    )
    if would_portfolio_protection_trigger_block(assessment):
        if portfolio_protection_observe_only_enabled():
            log.warning(
                "   🔬 PORTFOLIO PROTECTION OBSERVE ONLY | trigger=%s | reason=%s | execution=continue",
                assessment.trigger_id,
                assessment.primary_reason,
            )
            log.info(
                "   grep=PORTFOLIO_PROTECTION_OBSERVE|portfolio_protection_triggered|%s",
                assessment.trigger_id or "",
            )
        else:
            log.warning(
                "   🚨 PORTFOLIO PROTECTION BLOCK | trigger=%s | reason=%s",
                assessment.trigger_id,
                assessment.primary_reason,
            )
            log.info(
                "   grep=PORTFOLIO_PROTECTION_BLOCK|blocked_portfolio_protection|%s",
                assessment.trigger_id or "",
            )
    else:
        log.info("   ✅ Portfolio protection: trading allowed (no pre-execution block)")
    log.info("=" * 80)
