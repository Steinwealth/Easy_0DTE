#!/usr/bin/env python3
"""
Breakout contract gates — OTM moneyness + cheap-lotto size guard.

Jul 28 desk lock: prefer/hard OTM ≤ **5.5%** (shared with ORB + Trendline Layer D).
Prior Jul 27 research used prefer 12% / soft 15%; book replay favored 5.5%.
Cheap lotto (premium < $0.15 + large qty) only allowed near ATM.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Tuple


def compute_otm_pct(
    underlying_spot: float,
    strike: float,
    option_side: str,
) -> Optional[float]:
    """Signed OTM fraction vs spot (0.10 = 10% OTM; negative = ITM)."""
    try:
        spot = float(underlying_spot or 0.0)
        k = float(strike or 0.0)
    except (TypeError, ValueError):
        return None
    if spot <= 0 or k <= 0:
        return None
    side = str(option_side or "").strip().lower()
    if side == "call":
        raw = (k - spot) / spot
    elif side == "put":
        raw = (spot - k) / spot
    else:
        raw = abs(k - spot) / spot
    return float(raw)


@dataclass(frozen=True)
class OtmGateDecision:
    ok: bool
    reason: str = ""
    otm_pct: Optional[float] = None
    prefer_max: float = 0.055
    research_max: float = 0.055
    research_exception: bool = False  # prefer_max < otm ≤ research_max (empty when prefer==hard)


def evaluate_otm_gate(
    otm_pct: Optional[float],
    *,
    enabled: bool = True,
    prefer_max: float = 0.055,
    research_max: float = 0.055,
) -> OtmGateDecision:
    """
    Reject deep lotto above research_max.

    - otm ≤ prefer_max → pass
    - prefer_max < otm ≤ research_max → pass + research_exception stamp
    - otm > research_max → reject
    Jul 28: prefer==research==0.055 (hard ≤5.5%).
    """
    pref = float(prefer_max if prefer_max is not None else 0.055)
    res = float(research_max if research_max is not None else 0.055)
    if res < pref:
        res = pref
    if not enabled:
        return OtmGateDecision(
            ok=True,
            reason="otm_gate_disabled",
            otm_pct=otm_pct,
            prefer_max=pref,
            research_max=res,
        )
    if otm_pct is None:
        return OtmGateDecision(
            ok=False,
            reason="otm_pct_unknown",
            otm_pct=None,
            prefer_max=pref,
            research_max=res,
        )
    otm = float(otm_pct)
    if otm > res + 1e-12:
        return OtmGateDecision(
            ok=False,
            reason=f"contract_otm_too_deep:{otm:.4f}>{res:.4f}",
            otm_pct=otm,
            prefer_max=pref,
            research_max=res,
        )
    research_exc = otm > pref + 1e-12
    return OtmGateDecision(
        ok=True,
        reason="otm_research_exception" if research_exc else "otm_ok",
        otm_pct=otm,
        prefer_max=pref,
        research_max=res,
        research_exception=research_exc,
    )


@dataclass(frozen=True)
class CheapLottoDecision:
    ok: bool
    reason: str = ""
    action: str = "allow"  # allow | reject | downsize
    quantity: int = 0


def evaluate_cheap_lotto_guard(
    *,
    premium: float,
    quantity: int,
    otm_pct: Optional[float],
    enabled: bool = True,
    max_premium: float = 0.15,
    max_qty: int = 8,
    near_atm_otm_pct: float = 0.05,
    downsize_to: int = 1,
    mode: str = "reject",  # reject | downsize
) -> CheapLottoDecision:
    """
    Reject/downsize when entry is cheap-lotto priced and size is large unless
    contract is near ATM (OTM ≤ near_atm_otm_pct).
    """
    qty = int(quantity or 0)
    if not enabled:
        return CheapLottoDecision(ok=True, reason="cheap_lotto_disabled", action="allow", quantity=qty)
    try:
        prem = float(premium or 0.0)
    except (TypeError, ValueError):
        prem = 0.0
    if prem <= 0 or qty < 1:
        return CheapLottoDecision(ok=True, reason="not_applicable", action="allow", quantity=qty)
    if prem >= float(max_premium) - 1e-12:
        return CheapLottoDecision(ok=True, reason="premium_ok", action="allow", quantity=qty)
    if qty < int(max_qty):
        return CheapLottoDecision(ok=True, reason="qty_ok", action="allow", quantity=qty)
    otm = float(otm_pct) if otm_pct is not None else None
    if otm is not None and otm <= float(near_atm_otm_pct) + 1e-12:
        return CheapLottoDecision(
            ok=True,
            reason="near_atm_exception",
            action="allow",
            quantity=qty,
        )
    mode_l = str(mode or "reject").strip().lower()
    if mode_l == "downsize":
        new_qty = max(0, int(downsize_to))
        if new_qty < 1:
            return CheapLottoDecision(
                ok=False,
                reason=f"cheap_lotto_reject:prem={prem:.4f}<{float(max_premium):.2f}|qty={qty}",
                action="reject",
                quantity=0,
            )
        return CheapLottoDecision(
            ok=True,
            reason=f"cheap_lotto_downsize:{qty}->{new_qty}",
            action="downsize",
            quantity=new_qty,
        )
    return CheapLottoDecision(
        ok=False,
        reason=f"cheap_lotto_reject:prem={prem:.4f}<{float(max_premium):.2f}|qty={qty}|otm={otm}",
        action="reject",
        quantity=0,
    )


def stamp_contract_fields(
    target: Any,
    *,
    otm_pct: Optional[float],
    chosen_delta: Optional[float],
    research_exception: bool = False,
) -> None:
    """Stamp otm_pct / chosen_delta onto a dict or object with metadata."""
    fields = {
        "otm_pct": otm_pct,
        "chosen_delta": chosen_delta,
        "fill_delta": chosen_delta,
        "otm_research_exception": bool(research_exception),
    }
    if isinstance(target, dict):
        for k, v in fields.items():
            if v is not None or k == "otm_research_exception":
                target[k] = v
        return
    md = getattr(target, "metadata", None)
    if isinstance(md, dict):
        for k, v in fields.items():
            if v is not None or k == "otm_research_exception":
                md[k] = v
