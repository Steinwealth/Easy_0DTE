"""
Unified TL + Breakout contract selection (Jul 27 desk).

Policy:
  - Rank nearest OTM / higher δ inside the OTM gate (not lowest-δ first).
  - Floor ask via entry_min_premium(); if ask < floor → walk closer (toward ATM).
  - If δ < min → walk closer until δ ≥ min.
  - If δ > max → walk one step further OTM; else reject.
  - Only if no contract satisfies band + floor + OTM gate → no pick.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)

# (prefer_rank, otm, -delta, dist, contract)
RankedRow = Tuple[float, float, float, float, Any]


def contract_ask(contract: Any) -> float:
    bid = float(getattr(contract, "bid", 0.0) or 0.0)
    ask = float(getattr(contract, "ask", 0.0) or 0.0)
    mid = float(getattr(contract, "mid_price", 0.0) or 0.0)
    if ask > 0:
        return ask
    if mid > 0:
        return mid
    return max(0.01, bid) if bid > 0 else 0.0


def near_atm_sort_key(row: RankedRow) -> Tuple[float, float, float, float]:
    """Prefer ≤prefer band (rank 0), then lower OTM, then higher δ, then tighter dist."""
    return (row[0], row[1], row[2], row[3])


def build_ranked_row(
    contract: Any,
    *,
    underlying_spot: float,
    option_side: str,
    prefer_max_otm: float,
    abs_delta_fn: Callable[[Any], float],
    otm_fn: Callable[[float, float, str], Optional[float]],
) -> Optional[RankedRow]:
    try:
        strike = float(getattr(contract, "strike", 0.0) or 0.0)
    except (TypeError, ValueError):
        return None
    if strike <= 0:
        return None
    delta = float(abs_delta_fn(contract) or 0.0)
    if delta <= 0:
        return None
    otm = otm_fn(underlying_spot, strike, option_side)
    if otm is None:
        return None
    dist = abs(strike - underlying_spot) / max(underlying_spot, 1e-9)
    prefer_rank = 0.0 if float(otm) <= float(prefer_max_otm) + 1e-12 else 1.0
    return (prefer_rank, float(otm), -float(delta), float(dist), contract)


def pick_with_premium_delta_walk(
    ranked: Sequence[RankedRow],
    *,
    delta_min: float,
    delta_max: float,
    min_premium: float,
    abs_delta_fn: Callable[[Any], float],
    symbol: str = "",
    path: str = "",
) -> Tuple[Optional[Any], Optional[str], dict]:
    """
    Walk ranked near-ATM list until ask ≥ floor and δ in [min, max].

    If the nearest qualifying attempt has δ > max only, try one further-OTM step.
    Returns (contract, reject_reason, diagnostics).
    """
    diag: dict = {
        "n_ranked": len(ranked),
        "walk_steps": 0,
        "selected_ask": None,
        "selected_delta": None,
        "selected_otm": None,
    }
    if not ranked:
        return None, "no_contract_in_band", diag

    dmin = float(delta_min)
    dmax = float(delta_max)
    floor = float(min_premium)

    def _ok(c: Any) -> Tuple[bool, float, float]:
        ask = contract_ask(c)
        delta = float(abs_delta_fn(c) or 0.0)
        if ask + 1e-12 < floor:
            return False, ask, delta
        if delta + 1e-12 < dmin:
            return False, ask, delta
        if delta > dmax + 1e-12:
            return False, ask, delta
        return True, ask, delta

    # Primary: walk toward ATM order (already near-ATM sorted) until band+floor.
    for i, row in enumerate(ranked):
        c = row[-1]
        ok, ask, delta = _ok(c)
        diag["walk_steps"] = i + 1
        if ok:
            diag["selected_ask"] = ask
            diag["selected_delta"] = delta
            diag["selected_otm"] = row[1]
            if i > 0:
                log.info(
                    "UNIFIED_CONTRACT_WALK | path=%s | symbol=%s | steps=%d | "
                    "ask=%.4f | delta=%.4f | otm=%.4f | reason=prem_or_delta_floor",
                    path,
                    symbol,
                    i + 1,
                    ask,
                    delta,
                    float(row[1]),
                )
            return c, None, diag

    # δ > max recovery: from the nearest-OTM row, take one step further OTM
    # (next row in sort — higher OTM / lower δ).
    nearest = ranked[0][-1]
    nearest_delta = float(abs_delta_fn(nearest) or 0.0)
    if nearest_delta > dmax + 1e-12 and len(ranked) >= 2:
        alt = ranked[1][-1]
        ok, ask, delta = _ok(alt)
        diag["walk_steps"] = max(int(diag["walk_steps"]), 2)
        if ok:
            diag["selected_ask"] = ask
            diag["selected_delta"] = delta
            diag["selected_otm"] = ranked[1][1]
            log.info(
                "UNIFIED_CONTRACT_WALK | path=%s | symbol=%s | steps=2 | "
                "ask=%.4f | delta=%.4f | otm=%.4f | reason=delta_above_max_one_step_otm",
                path,
                symbol,
                ask,
                delta,
                float(ranked[1][1]),
            )
            return alt, None, diag

    # Classify reject
    asks = [contract_ask(r[-1]) for r in ranked]
    deltas = [float(abs_delta_fn(r[-1]) or 0.0) for r in ranked]
    if asks and max(asks) + 1e-12 < floor:
        return None, "option_premium_too_low", diag
    if deltas and max(deltas) + 1e-12 < dmin:
        return None, "delta_out_of_band", diag
    if deltas and min(deltas) > dmax + 1e-12:
        return None, "delta_out_of_band", diag
    return None, "no_contract_in_band", diag


def missing_or_zero_delta(abs_delta: Any) -> bool:
    try:
        d = abs(float(abs_delta))
    except (TypeError, ValueError):
        return True
    return d < 0.005
