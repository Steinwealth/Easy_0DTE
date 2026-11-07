"""PC-PX2 post-ARM profit capture (Aug 17 lock · Pareto leftover ≤ $10).

After premium +30% ARM:
  • stall-bank when und and premium stop printing new highs (QQQ-class)
  • PX2.6: stall only near HWM when stall_min_retention_of_mfe>0
    (current_pnl >= R * open MFE / prem HWM; else hold for trail/BE/EOD)
  • PX2.6b: delay stall until open MFE >= stall_min_mfe_pct when set
  • PX2.6c: when prem HWM >= high_mfe_trail_trigger_pct, use high_mfe_giveback_pct
    (default 30pp) instead of base giveback for premium_trail floor
    (else hold for trail/BE/EOD; Env PX2_STALL_MIN_MFE_PCT)
  • leftover ≤ $10 is the same dumper class (INTC CALL ORB1 14) — never
    underrun that live fill; 2s stall-bank / 20pp banks it, $0 is not the fill
  • 20pp ratcheting premium giveback from HWM; lock@200% uses the same 20pp
    (SPY +470% / MSFT +312% — not an und trail from ARM)
  • never exit red (BE floor books entry / $0, not the red print)
  • 0.50% und trail only after und HWM ≥0.5% AND prem ≥60% (caller)

Does not change required post-cutoff mfe_trail −10pp or 12:50 flatten.
Do not skip post-cutoff mfe_trail (GDX). Do not drop und trail (hurts TSLA 14 / SLV).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

try:
    from modules.price_runner_exit import underlying_favorable_move_pct
except Exception:
    from price_runner_exit import underlying_favorable_move_pct  # type: ignore


PX2_DETAIL_STALL_BANK = "stall_bank"
PX2_DETAIL_PREM_GIVEBACK = "prem_giveback"
PX2_DETAIL_MOON_GIVEBACK = "moon_giveback"
PX2_DETAIL_BE_FLOOR = "post_arm_be_floor"
PX2_DETAIL_CONTINUE = "continue"

# Overlay / expected-book scoring: leftover ≤ this USD is stall-bank class
# (QQQ leftover $0 and INTC CALL ORB1 14 leftover $9). Live 2s tape uses
# dual-stall + 20pp; do not treat $0 clamp as the primary fill for these.
PX2_DUMPER_LEFT_USD = 10.0


def px2_is_stall_bank_dumper(*, leftover_usd: float, max_usd: float = PX2_DUMPER_LEFT_USD) -> bool:
    """True when leftover is the stall-bank dumper class (Pareto keep-live)."""
    try:
        left = float(leftover_usd or 0.0)
    except (TypeError, ValueError):
        left = 0.0
    try:
        cap = float(max_usd if max_usd is not None else PX2_DUMPER_LEFT_USD)
    except (TypeError, ValueError):
        cap = PX2_DUMPER_LEFT_USD
    return left <= cap + 1e-9


@dataclass(frozen=True)
class Px2Decision:
    action: str  # continue | exit
    detail: str = PX2_DETAIL_CONTINUE
    reason: str = ""
    state_updates: Optional[Dict[str, Any]] = None
    log_event: str = ""


def px2_be_floor_fill_premium(
    *,
    detail: str,
    entry_premium: float,
    current_premium: float,
) -> float:
    """Post-ARM BE clamp: book entry ($0), not the red print.

    After ``DISCOVERY_ORB2_TL_END_PT`` ``mfe_trail`` −10pp is a separate latch (caller). Do not use this
    helper for afternoon profit-taking — that path must stay green.
    """
    if str(detail or "") != PX2_DETAIL_BE_FLOOR:
        return float(current_premium or 0.0)
    try:
        entry = float(entry_premium or 0.0)
    except (TypeError, ValueError):
        entry = 0.0
    if entry > 0:
        return entry
    return float(current_premium or 0.0)


def px2_giveback_floor(*, prem_hwm_pct: float, giveback_pct: float) -> float:
    """Ratcheting floor: max(0, HWM − giveback). Never below breakeven."""
    try:
        hwm = float(prem_hwm_pct or 0.0)
    except (TypeError, ValueError):
        hwm = 0.0
    try:
        gb = float(giveback_pct or 0.0)
    except (TypeError, ValueError):
        gb = 0.0
    if gb <= 0:
        return 0.0
    return max(0.0, hwm - gb)


def evaluate_px2_tick(
    *,
    osnap: Dict[str, Any],
    option_side: str,
    entry_underlying: float,
    current_underlying: float,
    current_pnl_pct: float,
    prem_hwm_pct: float,
    stall_ticks: int = 8,
    giveback_pct: float = 0.20,
    lock_moon_pct: float = 2.0,
    post_arm_be_floor: bool = True,
    underlying_usable: bool = True,
    stall_min_retention_of_mfe: float = 0.0,
    stall_min_mfe_pct: float = 0.0,
    high_mfe_trail_trigger_pct: float = 0.0,
    high_mfe_giveback_pct: float = 0.0,
) -> Px2Decision:
    """Advance PX2 latches for one trusted tick after ARM.

    Stall-bank requires *both* und and premium to stop making new highs for
    ``stall_ticks`` (~16s at 2s cadence). A moon that is still printing new
    premium HWMs (SPY / MSFT) is never stall-banked even if und stays thin.

    PX2.6: when ``stall_min_retention_of_mfe`` > 0, stall-bank only if
    ``current_pnl_pct >= retention * prem_hwm_pct`` (near open MFE / HWM).
    PX2.6b: when ``stall_min_mfe_pct`` > 0, stall-bank only if open MFE
    (prem HWM) >= that floor. Otherwise hold; trail / BE / EOD own the exit.
    PX2.6c: when ``high_mfe_trail_trigger_pct`` > 0 and prem HWM >= trigger,
    premium_trail uses ``high_mfe_giveback_pct`` (e.g. 30pp at MFE≥100%).
    Env: ``PX2_STALL_MIN_RETENTION_OF_MFE``, ``PX2_STALL_MIN_MFE_PCT``,
    ``PX2_HIGH_MFE_TRAIL_TRIGGER_PCT``, ``PX2_HIGH_MFE_TRAIL_PCT``.
    """
    updates: Dict[str, Any] = {}
    fav = underlying_favorable_move_pct(
        option_side=str(option_side or ""),
        entry_underlying=float(entry_underlying or 0.0),
        current_underlying=float(current_underlying or 0.0),
    )
    try:
        prev_und = osnap.get("px2_und_hwm_fav")
        prev_und_f = float(prev_und) if prev_und is not None else None
    except (TypeError, ValueError):
        prev_und_f = None
    try:
        und_stall = int(osnap.get("px2_stall_und_ticks") or 0)
    except (TypeError, ValueError):
        und_stall = 0
    try:
        prem_stall = int(osnap.get("px2_stall_prem_ticks") or 0)
    except (TypeError, ValueError):
        prem_stall = 0

    if not underlying_usable or fav is None:
        # Missing/unavailable underlying is not a stall and is not favorable/adverse.
        if prev_und_f is not None:
            updates["px2_und_hwm_fav"] = float(prev_und_f)
        updates["px2_stall_und_ticks"] = int(und_stall)
    elif prev_und_f is None or float(fav) > prev_und_f + 1e-12:
        updates["px2_und_hwm_fav"] = float(fav)
        updates["px2_stall_und_ticks"] = 0
        und_stall = 0
    else:
        updates["px2_und_hwm_fav"] = float(prev_und_f)
        und_stall += 1
        updates["px2_stall_und_ticks"] = int(und_stall)

    try:
        hwm = float(prem_hwm_pct or 0.0)
    except (TypeError, ValueError):
        hwm = 0.0
    try:
        pnl = float(current_pnl_pct or 0.0)
    except (TypeError, ValueError):
        pnl = 0.0
    if pnl + 1e-12 >= hwm:
        prem_stall = 0
    else:
        prem_stall += 1
    updates["px2_stall_prem_ticks"] = int(prem_stall)
    updates["px2_prem_hwm_pct"] = float(max(hwm, pnl))

    need = int(max(1, stall_ticks or 1))
    if und_stall >= need and prem_stall >= need and pnl > 0:
        # PX2.6 / 6b: quality gates before stall-bank mfe_take_profit.
        # 1) optional min open-MFE floor (PX2_STALL_MIN_MFE_PCT)
        # 2) optional HWM retention (PX2_STALL_MIN_RETENTION_OF_MFE)
        # Else hold for premium_trail / price_trail / 12:30 mfe_trail / BE / EOD.
        try:
            retention = float(stall_min_retention_of_mfe or 0.0)
        except (TypeError, ValueError):
            retention = 0.0
        try:
            min_mfe = float(stall_min_mfe_pct or 0.0)
        except (TypeError, ValueError):
            min_mfe = 0.0
        open_mfe = float(max(hwm, 0.0))
        if min_mfe > 1e-12 and open_mfe + 1e-12 < min_mfe:
            updates["px2_stall_min_mfe_block"] = True
            updates["px2_stall_retention_block"] = False
            updates["px2_last_detail"] = PX2_DETAIL_CONTINUE
            # Fall through — do not stall-bank before open MFE floor.
        elif retention > 1e-12 and open_mfe > 1e-12 and pnl + 1e-12 < retention * open_mfe:
            updates["px2_stall_min_mfe_block"] = False
            updates["px2_stall_retention_block"] = True
            updates["px2_last_detail"] = PX2_DETAIL_CONTINUE
            # Fall through — do not emit early mfe_take_profit away from HWM.
        else:
            updates["px2_stall_min_mfe_block"] = False
            updates["px2_stall_retention_block"] = False
            updates["px2_last_detail"] = PX2_DETAIL_STALL_BANK
            return Px2Decision(
                action="exit",
                detail=PX2_DETAIL_STALL_BANK,
                reason="mfe_take_profit",
                state_updates=updates,
                log_event="PX2_STALL_BANK",
            )

    if post_arm_be_floor and pnl <= 0 + 1e-12:
        updates["px2_last_detail"] = PX2_DETAIL_BE_FLOOR
        return Px2Decision(
            action="exit",
            detail=PX2_DETAIL_BE_FLOOR,
            reason="breakeven",
            state_updates=updates,
            log_event="PX2_BE_FLOOR",
        )

    # PX2.6c: widen premium giveback after large open MFE (default MFE≥100% → 30pp).
    try:
        hi_trig = float(high_mfe_trail_trigger_pct or 0.0)
    except (TypeError, ValueError):
        hi_trig = 0.0
    try:
        hi_gb = float(high_mfe_giveback_pct or 0.0)
    except (TypeError, ValueError):
        hi_gb = 0.0
    try:
        base_gb = float(giveback_pct or 0.0)
    except (TypeError, ValueError):
        base_gb = 0.0
    eff_gb = base_gb
    if hi_trig > 1e-12 and hi_gb > 1e-12 and hwm + 1e-12 >= hi_trig:
        eff_gb = hi_gb
        updates["px2_high_mfe_trail"] = True
    else:
        updates["px2_high_mfe_trail"] = False
    floor = px2_giveback_floor(prem_hwm_pct=hwm, giveback_pct=eff_gb)
    if pnl <= floor + 1e-12:
        try:
            lock_at = float(lock_moon_pct or 0.0)
        except (TypeError, ValueError):
            lock_at = 0.0
        moon = lock_at > 0 and hwm + 1e-12 >= lock_at
        detail = PX2_DETAIL_MOON_GIVEBACK if moon else PX2_DETAIL_PREM_GIVEBACK
        if floor <= 0 + 1e-12:
            updates["px2_last_detail"] = PX2_DETAIL_BE_FLOOR
            return Px2Decision(
                action="exit",
                detail=PX2_DETAIL_BE_FLOOR,
                reason="breakeven",
                state_updates=updates,
                log_event="PX2_BE_FLOOR",
            )
        updates["px2_last_detail"] = detail
        return Px2Decision(
            action="exit",
            detail=detail,
            reason="premium_trail",
            state_updates=updates,
            log_event="PX2_MOON_GIVEBACK" if moon else "PX2_PREM_GIVEBACK",
        )

    updates["px2_last_detail"] = PX2_DETAIL_CONTINUE
    return Px2Decision(
        action="continue",
        detail=PX2_DETAIL_CONTINUE,
        state_updates=updates,
    )


def apply_px2_state_updates(osnap: Dict[str, Any], updates: Optional[Dict[str, Any]]) -> None:
    if not updates or not isinstance(osnap, dict):
        return
    for k, v in updates.items():
        osnap[k] = v


def px2_und_hwm_fav(osnap: Dict[str, Any]) -> Optional[float]:
    try:
        v = osnap.get("px2_und_hwm_fav") if isinstance(osnap, dict) else None
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None
