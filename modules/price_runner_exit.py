"""Price-runner exit helpers for 0DTE singles (Jul 31 desk lock §5.10.15).

After premium +30% gate: confirm favorable underlying for N ticks, then:
  • trail from side-aligned price HWM
  • optional hard price take-profit from entry (OFF when take_profit_pct <= 0)
  • premium BE floor once armed

State lives on option_stealth osnap (same pattern as mfe_hold_exit).
Legacy osnap keys ``und_runner_*`` are migrated to ``price_runner_*`` on read.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional


PRICE_RUNNER_DETAIL_ARMED = "armed"
PRICE_RUNNER_DETAIL_CONFIRMING = "confirming"
PRICE_RUNNER_DETAIL_TRAIL = "price_trail"
PRICE_RUNNER_DETAIL_TAKE_PROFIT = "take_profit"
PRICE_RUNNER_DETAIL_PREM_FLOOR = "prem_floor"
PRICE_RUNNER_DETAIL_CONTINUE = "continue"


@dataclass(frozen=True)
class PriceRunnerDecision:
    action: str  # continue | arm | exit
    detail: str = PRICE_RUNNER_DETAIL_CONTINUE
    reason: str = ""
    state_updates: Optional[Dict[str, Any]] = None
    log_event: str = ""


def underlying_favorable_move_pct(
    *,
    option_side: str,
    entry_underlying: float,
    current_underlying: float,
) -> Optional[float]:
    """Side-aligned favorable move vs entry (CALL up / PUT down). None if invalid."""
    try:
        entry_u = float(entry_underlying or 0.0)
        cur_u = float(current_underlying or 0.0)
    except (TypeError, ValueError):
        return None
    if entry_u <= 0 or cur_u <= 0:
        return None
    side = str(option_side or "").strip().lower()
    if "put" in side or side == "short":
        return (entry_u - cur_u) / entry_u
    return (cur_u - entry_u) / entry_u


def arm_trail_price_is_thin(
    *,
    option_side: str,
    entry_underlying: float,
    current_underlying: float,
    thin_max_und_pct: float,
) -> tuple[bool, Optional[float]]:
    """True when und fav at ARM is thin/unknown — bank premium TP instead of trail.

    Missing/invalid underlying marks count as thin (premium-only spike class).
    Returns ``(is_thin, und_fav_pct)``.
    """
    fav = underlying_favorable_move_pct(
        option_side=option_side,
        entry_underlying=entry_underlying,
        current_underlying=current_underlying,
    )
    try:
        thin_max = float(thin_max_und_pct or 0.0)
    except (TypeError, ValueError):
        thin_max = 0.0
    if thin_max <= 0:
        return False, fav
    if fav is None:
        return True, None
    return float(fav) + 1e-12 < thin_max, float(fav)


def update_price_hwm(
    *,
    option_side: str,
    hwm: Optional[float],
    current_underlying: float,
) -> Optional[float]:
    """CALL tracks high; PUT tracks low."""
    try:
        cur_u = float(current_underlying or 0.0)
    except (TypeError, ValueError):
        return hwm
    if cur_u <= 0:
        return hwm
    if hwm is None or float(hwm) <= 0:
        return cur_u
    side = str(option_side or "").strip().lower()
    if "put" in side or side == "short":
        return cur_u if cur_u < float(hwm) else float(hwm)
    return cur_u if cur_u > float(hwm) else float(hwm)


def resolve_runner_prem_floor_pct(
    *,
    base_floor_pct: float,
    price_thin_max_pct: float,
    price_thin_floor_pct: float,
    option_side: str,
    entry_underlying: float,
    price_hwm: Optional[float],
    current_underlying: Optional[float] = None,
) -> tuple[float, bool, Optional[float]]:
    """Resolve effective prem floor; optional price-thin override.

    When ``price_thin_max_pct`` > 0 and peak favorable underlying move from entry
    (side-aligned price HWM) is still below that max, use ``price_thin_floor_pct``
    (at least ``base_floor_pct``). Otherwise keep the baseline floor.

    Returns ``(effective_floor_pct, used_price_thin, peak_fav_pct)``.
    """
    try:
        base = float(base_floor_pct or 0.0)
    except (TypeError, ValueError):
        base = 0.0
    try:
        thin_max = float(price_thin_max_pct or 0.0)
    except (TypeError, ValueError):
        thin_max = 0.0
    try:
        thin_floor = float(price_thin_floor_pct or 0.0)
    except (TypeError, ValueError):
        thin_floor = 0.0
    if thin_max <= 0:
        return base, False, None
    try:
        hwm_f = float(price_hwm) if price_hwm is not None else 0.0
    except (TypeError, ValueError):
        hwm_f = 0.0
    try:
        cur_f = float(current_underlying) if current_underlying is not None else 0.0
    except (TypeError, ValueError):
        cur_f = 0.0
    # Peak favorable = side-aligned HWM vs entry; include this tick's und.
    candidates = []
    for ref in (hwm_f, cur_f):
        if ref <= 0:
            continue
        fav = underlying_favorable_move_pct(
            option_side=str(option_side or ""),
            entry_underlying=float(entry_underlying or 0.0),
            current_underlying=float(ref),
        )
        if fav is not None:
            candidates.append(float(fav))
    if not candidates:
        return base, False, None
    peak_fav = max(candidates)
    if peak_fav + 1e-12 < thin_max:
        return max(base, thin_floor), True, float(peak_fav)
    return base, False, float(peak_fav)


def resolve_runner_trail_pct(
    *,
    base_trail_pct: float,
    wide_trail_pct: float = 0.0,
    wide_mfe_pct: float = 0.0,
    premium_mfe_pct: float = 0.0,
) -> tuple:
    """Widen und trail after premium MFE gate. Returns ``(trail_pct, used_wide)``.

    Aug 14 desk: after prem HWM ≥ +60%, trail 0.50% instead of 0.20%.
    ``wide_mfe_pct <= 0`` or ``wide_trail_pct <= base`` keeps the base trail.
    """
    try:
        base = float(base_trail_pct or 0.0)
    except (TypeError, ValueError):
        base = 0.0
    try:
        wide = float(wide_trail_pct or 0.0)
    except (TypeError, ValueError):
        wide = 0.0
    try:
        gate = float(wide_mfe_pct or 0.0)
    except (TypeError, ValueError):
        gate = 0.0
    try:
        mfe = float(premium_mfe_pct or 0.0)
    except (TypeError, ValueError):
        mfe = 0.0
    if gate > 0 and wide > base + 1e-12 and mfe + 1e-12 >= gate:
        return wide, True
    return base, False


def price_trail_hit(
    *,
    option_side: str,
    hwm: Optional[float],
    current_underlying: float,
    trail_pct: float,
) -> bool:
    """True when price has given back ≥ trail_pct from side-aligned HWM."""
    try:
        cur_u = float(current_underlying or 0.0)
        trail = float(trail_pct or 0.0)
        h = float(hwm) if hwm is not None else 0.0
    except (TypeError, ValueError):
        return False
    if cur_u <= 0 or h <= 0 or trail <= 0:
        return False
    side = str(option_side or "").strip().lower()
    if "put" in side or side == "short":
        return cur_u >= h * (1.0 + trail) - 1e-12
    return cur_u <= h * (1.0 - trail) + 1e-12


def start_price_runner_state(
    *,
    now_iso: str,
    price_hwm: float,
    confirm_ticks: int,
    arm_price_fav_pct: float,
) -> Dict[str, Any]:
    return {
        "price_runner_active": True,
        "price_runner_armed_iso": str(now_iso or ""),
        "price_runner_confirm_ticks": int(confirm_ticks),
        "price_runner_hwm": float(price_hwm),
        "price_runner_arm_price_fav_pct": float(arm_price_fav_pct),
        "price_runner_last_detail": PRICE_RUNNER_DETAIL_ARMED,
    }


_LEGACY_OSNAP_KEYS = (
    "und_runner_active",
    "und_runner_armed_iso",
    "und_runner_confirm_ticks",
    "und_runner_und_hwm",
    "und_runner_arm_und_fav_pct",
    "und_runner_last_detail",
)


def _migrate_legacy_price_runner_osnap(osnap: Dict[str, Any]) -> None:
    """Copy legacy und_runner_* osnap fields onto price_runner_* once."""
    if not isinstance(osnap, dict):
        return
    pairs = (
        ("price_runner_active", "und_runner_active"),
        ("price_runner_armed_iso", "und_runner_armed_iso"),
        ("price_runner_confirm_ticks", "und_runner_confirm_ticks"),
        ("price_runner_hwm", "und_runner_und_hwm"),
        ("price_runner_arm_price_fav_pct", "und_runner_arm_und_fav_pct"),
        ("price_runner_last_detail", "und_runner_last_detail"),
    )
    for new_k, old_k in pairs:
        if new_k not in osnap and old_k in osnap:
            osnap[new_k] = osnap[old_k]


def clear_price_runner_state(osnap: Dict[str, Any]) -> None:
    for k in (
        "price_runner_active",
        "price_runner_armed_iso",
        "price_runner_confirm_ticks",
        "price_runner_hwm",
        "price_runner_arm_price_fav_pct",
        "price_runner_last_detail",
        *_LEGACY_OSNAP_KEYS,
    ):
        osnap.pop(k, None)


def evaluate_price_runner_tick(
    *,
    osnap: Dict[str, Any],
    option_side: str,
    entry_underlying: float,
    current_underlying: float,
    current_pnl_pct: float,
    past_premium_gate: bool,
    arm_price_fav_pct: float = 0.0005,
    confirm_ticks_required: int = 3,
    trail_pct: float = 0.002,
    price_take_profit_pct: float = 0.01,
    prem_floor_pct: float = 0.0,
    now_iso: str = "",
) -> PriceRunnerDecision:
    """Advance price-runner state for one trusted protection tick.

    Caller invokes when price-runner is enabled for 0DTE singles.
    Tracks price HWM from first valid tick (open-aligned), arms after premium gate
    + consecutive favorable price confirms, then evaluates trail / price TP / prem floor.
    """
    updates: Dict[str, Any] = {}
    side = str(option_side or "")
    _migrate_legacy_price_runner_osnap(osnap)
    prev_hwm = osnap.get("price_runner_hwm")
    try:
        prev_hwm_f = float(prev_hwm) if prev_hwm is not None else None
    except (TypeError, ValueError):
        prev_hwm_f = None
    hwm = update_price_hwm(
        option_side=side,
        hwm=prev_hwm_f,
        current_underlying=float(current_underlying or 0.0),
    )
    if hwm is not None:
        updates["price_runner_hwm"] = float(hwm)

    fav = underlying_favorable_move_pct(
        option_side=side,
        entry_underlying=float(entry_underlying or 0.0),
        current_underlying=float(current_underlying or 0.0),
    )
    active = bool(osnap.get("price_runner_active"))

    if (not active) and past_premium_gate:
        try:
            count = int(osnap.get("price_runner_confirm_ticks") or 0)
        except (TypeError, ValueError):
            count = 0
        if fav is not None and fav + 1e-12 >= float(arm_price_fav_pct):
            count += 1
            updates["price_runner_confirm_ticks"] = int(count)
            updates["price_runner_last_detail"] = PRICE_RUNNER_DETAIL_CONFIRMING
            if count >= int(max(1, confirm_ticks_required)):
                arm_hwm = float(hwm) if hwm is not None else float(current_underlying or 0.0)
                arm_state = start_price_runner_state(
                    now_iso=now_iso,
                    price_hwm=arm_hwm,
                    confirm_ticks=count,
                    arm_price_fav_pct=float(arm_price_fav_pct),
                )
                updates.update(arm_state)
                active = True
                # Fall through — same tick may also hit price_tp / trail / prem floor.
        else:
            updates["price_runner_confirm_ticks"] = 0
            updates["price_runner_last_detail"] = PRICE_RUNNER_DETAIL_CONTINUE

    if not active:
        return PriceRunnerDecision(
            action="continue",
            detail=PRICE_RUNNER_DETAIL_CONTINUE,
            state_updates=updates,
        )

    # Armed runner exits (including the arming tick).
    if price_take_profit_pct is not None and float(price_take_profit_pct) > 0:
        if fav is not None and fav + 1e-12 >= float(price_take_profit_pct):
            updates["price_runner_last_detail"] = PRICE_RUNNER_DETAIL_TAKE_PROFIT
            return PriceRunnerDecision(
                action="exit",
                detail=PRICE_RUNNER_DETAIL_TAKE_PROFIT,
                reason="take_profit",
                state_updates=updates,
                log_event="PRICE_RUNNER_TAKE_PROFIT",
            )

    if prem_floor_pct is not None:
        if float(current_pnl_pct) <= float(prem_floor_pct) + 1e-12:
            updates["price_runner_last_detail"] = PRICE_RUNNER_DETAIL_PREM_FLOOR
            return PriceRunnerDecision(
                action="exit",
                detail=PRICE_RUNNER_DETAIL_PREM_FLOOR,
                reason="breakeven",
                state_updates=updates,
                log_event="PRICE_RUNNER_PREM_FLOOR",
            )

    if price_trail_hit(
        option_side=side,
        hwm=hwm,
        current_underlying=float(current_underlying or 0.0),
        trail_pct=float(trail_pct),
    ):
        updates["price_runner_last_detail"] = PRICE_RUNNER_DETAIL_TRAIL
        return PriceRunnerDecision(
            action="exit",
            detail=PRICE_RUNNER_DETAIL_TRAIL,
            reason="price_trail",
            state_updates=updates,
            log_event="PRICE_RUNNER_TRAIL",
        )

    updates["price_runner_last_detail"] = PRICE_RUNNER_DETAIL_CONTINUE
    return PriceRunnerDecision(
        action="continue",
        detail=PRICE_RUNNER_DETAIL_CONTINUE,
        state_updates=updates,
    )


def apply_price_runner_state_updates(osnap: Dict[str, Any], updates: Optional[Dict[str, Any]]) -> None:
    if not updates:
        return
    for k, v in updates.items():
        osnap[k] = v
