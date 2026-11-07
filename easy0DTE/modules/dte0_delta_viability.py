"""
Lowest *viable* delta selection for 0DTE single-leg entry.

Prefers the lowest delta in band when breakeven distance, expected move (ORB),
spread, premium, and momentum support the strike. When the lowest delta is too far
OTM for the session expected move, steps up within 0.15–0.35 (configurable).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

_STEP_UP_MIN_KEY = "0DTE_DELTA_VIABILITY_STEP_UP_MIN"
_STEP_UP_MAX_KEY = "0DTE_DELTA_VIABILITY_STEP_UP_MAX"
_BREAKEVEN_CUSHION_KEY = "0DTE_DELTA_VIABILITY_BREAKEVEN_CUSHION"
_ORB_FRAC_KEY = "0DTE_DELTA_VIABILITY_ORB_RANGE_FRACTION"
_MIN_EXPECTED_MOVE_KEY = "0DTE_DELTA_VIABILITY_MIN_EXPECTED_MOVE_PCT"
_MOONSHOT_MOM_KEY = "0DTE_DELTA_VIABILITY_MOONSHOT_MOMENTUM"
_MOONSHOT_ORB_KEY = "0DTE_DELTA_VIABILITY_MOONSHOT_ORB_RANGE_PCT"


def _float_env(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None or str(raw).strip() == "":
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


def step_up_delta_bounds() -> Tuple[float, float]:
    lo = max(0.05, _float_env(_STEP_UP_MIN_KEY, 0.15))
    hi = max(lo, _float_env(_STEP_UP_MAX_KEY, 0.35))
    return lo, hi


def breakeven_cushion() -> float:
    return max(1.0, _float_env(_BREAKEVEN_CUSHION_KEY, 1.20))


def expected_move_pct(*, orb_range_pct: float, momentum_score: float) -> float:
    """Session expected underlying move (percent) from ORB width and momentum."""
    orb_frac = max(0.25, min(1.0, _float_env(_ORB_FRAC_KEY, 0.65)))
    floor = max(0.10, _float_env(_MIN_EXPECTED_MOVE_KEY, 0.25))
    base = max(floor, float(orb_range_pct or 0.0) * orb_frac)
    mom = float(momentum_score or 0.0)
    if mom >= 85.0:
        base *= 1.35
    elif mom >= 72.0:
        base *= 1.20
    elif mom >= 60.0:
        base *= 1.08
    return base


def breakeven_move_pct(
    *,
    spot: float,
    strike: float,
    premium: float,
    option_type: str,
) -> float:
    """Percent underlying move from spot required to reach option breakeven."""
    s = float(spot or 0.0)
    if s <= 0.0:
        return 999.0
    prem = max(0.0, float(premium or 0.0))
    k = float(strike or 0.0)
    opt = str(option_type or "call").lower()
    if opt.startswith("p"):
        be_px = k - prem
        return max(0.0, (s - be_px) / s * 100.0)
    be_px = k + prem
    return max(0.0, (be_px - s) / s * 100.0)


def moonshot_waiver(*, orb_range_pct: float, momentum_score: float) -> bool:
    """Allow very low delta when ORB/momentum support convex moonshots (e.g. SLV)."""
    mom_thr = _float_env(_MOONSHOT_MOM_KEY, 70.0)
    orb_thr = _float_env(_MOONSHOT_ORB_KEY, 0.90)
    return float(momentum_score or 0.0) >= mom_thr and float(orb_range_pct or 0.0) >= orb_thr


class ViabilityAssessment:
    def __init__(
        self,
        *,
        viable: bool,
        breakeven_move_pct: float,
        expected_move_pct: float,
        cushion: float,
        spread_pct: float,
        premium: float,
        abs_delta: float,
        moonshot_waiver: bool,
        reject_reason: str = "",
    ) -> None:
        self.viable = viable
        self.breakeven_move_pct = breakeven_move_pct
        self.expected_move_pct = expected_move_pct
        self.cushion = cushion
        self.spread_pct = spread_pct
        self.premium = premium
        self.abs_delta = abs_delta
        self.moonshot_waiver = moonshot_waiver
        self.reject_reason = reject_reason

    def to_audit_fields(self) -> Dict[str, Any]:
        return {
            "viable": self.viable,
            "breakeven_move_pct": round(self.breakeven_move_pct, 4),
            "expected_move_pct": round(self.expected_move_pct, 4),
            "breakeven_cushion": round(self.cushion, 3),
            "spread_pct": round(self.spread_pct, 3),
            "premium": round(self.premium, 4),
            "abs_delta": round(self.abs_delta, 4),
            "moonshot_waiver": self.moonshot_waiver,
            "reject_reason": self.reject_reason or "",
        }


def _abs_contract_delta(contract: Any) -> float:
    try:
        raw = getattr(contract, "delta_est", None)
        if raw is None:
            raw = getattr(contract, "delta", 0.0)
        return abs(float(raw or 0.0))
    except (TypeError, ValueError):
        return 0.0


def assess_contract_viability(
    contract: Any,
    *,
    spot: float,
    option_type: str,
    orb_range_pct: float,
    momentum_score: float,
    max_spread_pct: float,
    premium_min: float,
) -> ViabilityAssessment:
    abs_delta = _abs_contract_delta(contract)
    spread = float(getattr(contract, "bid_ask_spread_pct", 0.0) or 999.0)
    prem = float(getattr(contract, "mid_price", 0.0) or 0.0)
    be_move = breakeven_move_pct(
        spot=float(spot),
        strike=float(getattr(contract, "strike", 0.0) or 0.0),
        premium=prem,
        option_type=option_type,
    )
    exp_move = expected_move_pct(orb_range_pct=float(orb_range_pct), momentum_score=float(momentum_score))
    cushion = breakeven_cushion()
    waiver = moonshot_waiver(orb_range_pct=float(orb_range_pct), momentum_score=float(momentum_score))

    reject = ""
    if spread > float(max_spread_pct) + 1e-6:
        reject = "spread_above_cap"
    elif prem < float(premium_min) - 1e-6:
        reject = "premium_below_min"
    elif not waiver and be_move > exp_move * cushion + 1e-6:
        reject = "breakeven_beyond_expected_move"
    elif waiver:
        reject = ""

    viable = not reject or (waiver and reject == "breakeven_beyond_expected_move")
    if waiver:
        viable = spread <= float(max_spread_pct) + 1e-6 and prem >= float(premium_min) - 1e-6
    else:
        viable = not reject

    return ViabilityAssessment(
        viable=bool(viable),
        breakeven_move_pct=be_move,
        expected_move_pct=exp_move,
        cushion=cushion,
        spread_pct=spread,
        premium=prem,
        abs_delta=abs_delta,
        moonshot_waiver=waiver,
        reject_reason=reject if not viable else "",
    )


def log_delta_viability_audit(
    *,
    symbol: str,
    profile: str,
    action: str,
    selected_delta: float,
    lowest_delta: float,
    orb_range_pct: float,
    momentum_score: float,
    assessment: ViabilityAssessment,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    fields = assessment.to_audit_fields()
    if extra:
        fields.update(extra)
    log.info(
        "DELTA_VIABILITY_AUDIT | symbol=%s | profile=%s | action=%s | selected_delta=%.4f | "
        "lowest_delta=%.4f | orb_range_pct=%.3f | momentum=%.1f | %s",
        str(symbol),
        str(profile),
        str(action),
        float(selected_delta),
        float(lowest_delta),
        float(orb_range_pct),
        float(momentum_score),
        " | ".join(f"{k}={v}" for k, v in fields.items()),
    )


def pick_lowest_viable_single_leg(
    candidates: List[Any],
    *,
    spot: float,
    option_type: str,
    symbol: str,
    profile: str,
    orb_range_pct: float,
    momentum_score: float,
    max_spread_pct: float,
    premium_min: float,
    prefer_lowest: bool,
) -> Tuple[Optional[Any], Dict[str, Any]]:
    """
    Choose contract: lowest viable delta, else step-up in [0.15, 0.35], else lowest in band.
    """
    audit: Dict[str, Any] = {
        "viability_enabled": True,
        "orb_range_pct": float(orb_range_pct),
        "momentum_score": float(momentum_score),
    }
    if not candidates:
        audit["action"] = "no_candidates"
        return None, audit

    sorted_c = sorted(candidates, key=_abs_contract_delta)

    def _abs_d(c: Any) -> float:
        return _abs_contract_delta(c)

    lowest = sorted_c[0]
    lowest_d = _abs_d(lowest)
    lowest_assess = assess_contract_viability(
        lowest,
        spot=spot,
        option_type=option_type,
        orb_range_pct=orb_range_pct,
        momentum_score=momentum_score,
        max_spread_pct=max_spread_pct,
        premium_min=premium_min,
    )
    audit["lowest_assessment"] = lowest_assess.to_audit_fields()

    if not prefer_lowest:
        audit["action"] = "legacy_sort_only"
        return sorted_c[-1] if sorted_c else None, audit

    if lowest_assess.viable:
        audit["action"] = "lowest_viable"
        log_delta_viability_audit(
            symbol=symbol,
            profile=profile,
            action="lowest_viable",
            selected_delta=lowest_d,
            lowest_delta=lowest_d,
            orb_range_pct=orb_range_pct,
            momentum_score=momentum_score,
            assessment=lowest_assess,
        )
        return lowest, audit

    step_min, step_max = step_up_delta_bounds()
    step_pool = [c for c in sorted_c if step_min - 1e-6 <= _abs_d(c) <= step_max + 1e-6]
    audit["step_up_band"] = [step_min, step_max]
    for cand in step_pool:
        assess = assess_contract_viability(
            cand,
            spot=spot,
            option_type=option_type,
            orb_range_pct=orb_range_pct,
            momentum_score=momentum_score,
            max_spread_pct=max_spread_pct,
            premium_min=premium_min,
        )
        if assess.viable:
            sel_d = _abs_d(cand)
            audit["action"] = "step_up_viable"
            audit["step_up_from_delta"] = lowest_d
            audit["selected_assessment"] = assess.to_audit_fields()
            log_delta_viability_audit(
                symbol=symbol,
                profile=profile,
                action="step_up_viable",
                selected_delta=sel_d,
                lowest_delta=lowest_d,
                orb_range_pct=orb_range_pct,
                momentum_score=momentum_score,
                assessment=assess,
                extra={"step_up_from_delta": lowest_d, "lowest_reject": lowest_assess.reject_reason},
            )
            return cand, audit

    if step_pool:
        fallback = step_pool[0]
        audit["action"] = "step_up_best_effort"
        log_delta_viability_audit(
            symbol=symbol,
            profile=profile,
            action="step_up_best_effort",
            selected_delta=_abs_d(fallback),
            lowest_delta=lowest_d,
            orb_range_pct=orb_range_pct,
            momentum_score=momentum_score,
            assessment=assess_contract_viability(
                fallback,
                spot=spot,
                option_type=option_type,
                orb_range_pct=orb_range_pct,
                momentum_score=momentum_score,
                max_spread_pct=max_spread_pct,
                premium_min=premium_min,
            ),
            extra={"lowest_reject": lowest_assess.reject_reason},
        )
        return fallback, audit

    audit["action"] = "retain_lowest_in_band"
    log_delta_viability_audit(
        symbol=symbol,
        profile=profile,
        action="retain_lowest_in_band",
        selected_delta=lowest_d,
        lowest_delta=lowest_d,
        orb_range_pct=orb_range_pct,
        momentum_score=momentum_score,
        assessment=lowest_assess,
    )
    return lowest, audit
