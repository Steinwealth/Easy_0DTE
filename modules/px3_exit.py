"""PC-PX3 Protected Runner — post-PX2 ``premium_trail`` handoff (research / config-off).

PX2 remains the banker. PX3 only evaluates when PX2 would exit ``premium_trail``.
Guards that say keep PX2 return that original exit unchanged. When disabled,
callers must skip this module so PX2 behavior is identical.

Does not replace stall-bank, BE floor, 0.50% PX2 und trail, 12:30 ``mfe_trail``,
or flatten. Those stay with the caller.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

try:
    from modules.price_runner_exit import underlying_favorable_move_pct, update_price_hwm
except Exception:
    from price_runner_exit import underlying_favorable_move_pct, update_price_hwm  # type: ignore


PX3_MODE_TREND = "trend_runner"
PX3_MODE_MOON = "moon_probe"
PX3_MODE_EXCEPTIONAL = "exceptional_price_runner"
PX3_MODE_THIN_LEAD_EXCEPTIONAL = "thin_lead_exceptional_runner"
PX3_MODE_HIGH_HWM_EXCEPTIONAL = "high_hwm_exceptional_runner"
PX3_MODE_NEAR_TREND = "near_trend_runner"
PX3_MODE_MFE_CONFIRMATION = "mfe_take_profit_confirmation_runner"

PX3_DETAIL_KEEP_PX2 = "keep_px2"
PX3_DETAIL_HANDOFF_TREND = "handoff_trend"
PX3_DETAIL_HANDOFF_MOON = "handoff_moon"
PX3_DETAIL_HANDOFF_EXCEPTIONAL = "handoff_exceptional"
PX3_DETAIL_HANDOFF_THIN_LEAD_EXCEPTIONAL = "handoff_thin_lead_exceptional"
PX3_DETAIL_HANDOFF_HIGH_HWM_EXCEPTIONAL = "handoff_high_hwm_exceptional"
PX3_DETAIL_HANDOFF_NEAR_TREND = "handoff_near_trend"
PX3_DETAIL_MFE_CONFIRM_PENDING = "mfe_confirm_pending"
PX3_DETAIL_MFE_CONFIRM_HANDOFF = "mfe_confirm_handoff"
PX3_DETAIL_MFE_CONFIRM_FAIL_BANK = "mfe_confirm_fail_bank"
PX3_DETAIL_PRICE_TRAIL = "px3_price_trail"
PX3_DETAIL_PREM_FLOOR = "px3_prem_floor"
PX3_DETAIL_MOON_PATIENCE = "px3_moon_patience"
PX3_DETAIL_CONTINUE = "continue"
PX3_DETAIL_RESTART_GAP_HOLD = "px3_restart_gap_hold"
PX2_DEFAULT_GIVEBACK_PCT = 0.20

PX3_REASON_PRICE_TRAIL = "price_trail"
PX3_REASON_PREM_TRAIL = "premium_trail"
PX3_REASON_MFE_TAKE_PROFIT = "mfe_take_profit"


@dataclass(frozen=True)
class Px3Config:
    enabled: bool = False
    trend_mfe_pct: float = 1.00
    trend_und_hwm_pct: float = 0.004
    price_trail_pct: float = 0.004
    trend_prem_floor_pct: float = 0.50
    moon_mfe_pct: float = 2.00
    moon_patience_seconds: float = 300.0
    moon_prem_floor_pct: float = 0.75
    ultra_clean_pnl_pct: float = 7.50
    ultra_clean_capture_ratio: float = 0.85
    clean_trend_pnl_pct: float = 2.50
    clean_trend_capture_ratio: float = 0.90
    exceptional_enabled: bool = False
    exceptional_min_current_pnl_pct: float = 1.00
    exceptional_min_prem_hwm_pct: float = 0.60
    exceptional_min_und_hwm_pct: float = 0.005
    exceptional_price_trail_pct: float = 0.005
    exceptional_prem_floor_pct: float = 0.75
    thin_lead_exceptional_enabled: bool = False
    thin_lead_exceptional_min_current_pnl_pct: float = 1.00
    thin_lead_exceptional_min_prem_hwm_pct: float = 0.60
    thin_lead_exceptional_min_und_hwm_pct: float = 0.001
    thin_lead_exceptional_price_trail_pct: float = 0.004
    thin_lead_exceptional_prem_floor_pct: float = 0.25
    high_hwm_exceptional_enabled: bool = False
    high_hwm_exceptional_min_current_pnl_pct: float = 1.00
    high_hwm_exceptional_min_prem_hwm_pct: float = 1.25
    high_hwm_exceptional_min_und_hwm_pct: float = 0.004
    high_hwm_exceptional_price_trail_pct: float = 0.004
    high_hwm_exceptional_prem_floor_pct: float = 0.50
    near_trend_enabled: bool = False
    near_trend_min_current_pnl_pct: float = 0.70
    near_trend_min_prem_hwm_pct: float = 0.90
    near_trend_min_und_hwm_pct: float = 0.0009
    near_trend_price_trail_pct: float = 0.004
    near_trend_prem_floor_pct: float = 0.50
    mfe_confirm_enabled: bool = False
    mfe_confirm_min_current_pnl_pct: float = 0.20
    mfe_confirm_min_prem_hwm_pct: float = 0.30
    mfe_confirm_min_und_hwm_pct: float = 0.0025
    mfe_confirm_normal_min_entry_age_seconds: float = 1200.0
    mfe_confirm_normal_max_entry_age_seconds: float = 1800.0
    mfe_confirm_delta_pct: float = 0.15
    mfe_confirm_window_seconds: float = 120.0
    mfe_confirm_thin_recovery_enabled: bool = True
    mfe_confirm_thin_recovery_min_current_pnl_pct: float = 0.15
    mfe_confirm_thin_recovery_deadzone_min_pnl_pct: float = 0.20
    mfe_confirm_thin_recovery_deadzone_max_pnl_pct: float = 0.35
    mfe_confirm_thin_recovery_min_und_hwm_pct: float = 0.001
    mfe_confirm_thin_recovery_max_und_hwm_pct: float = 0.0025
    mfe_confirm_thin_recovery_max_prem_hwm_pct: float = 0.75
    mfe_confirm_thin_recovery_window_seconds: float = 600.0
    mfe_confirm_thin_recovery_floor_giveback_pct: float = 0.10
    mfe_confirm_thin_recovery_runner_giveback_pct: float = 0.50
    mfe_confirm_strong_recovery_min_current_pnl_pct: float = 0.29
    mfe_confirm_strong_und_hwm_pct: float = 0.010
    mfe_confirm_recovery_max_entry_age_seconds: float = 1200.0
    mfe_confirm_recovery_window_seconds: float = 900.0
    mfe_confirm_recovery_floor_giveback_pct: float = 0.20
    mfe_confirm_late_strong_recovery_enabled: bool = False
    mfe_confirm_late_strong_recovery_min_current_pnl_pct: float = 0.35
    mfe_confirm_late_strong_recovery_min_prem_hwm_pct: float = 0.60
    mfe_confirm_late_strong_recovery_max_prem_hwm_pct: float = 1.50
    mfe_confirm_late_strong_recovery_min_und_hwm_pct: float = 0.0075
    mfe_confirm_late_strong_recovery_max_entry_age_seconds: float = 3600.0
    mfe_confirm_price_trail_pct: float = 0.004
    mfe_confirm_prem_floor_pct: float = 0.30
    mfe_confirm_runner_giveback_pct: float = 0.50


DEFAULT_PX3_CONFIG = Px3Config()


@dataclass(frozen=True)
class Px3Decision:
    action: str  # continue | exit | keep_px2
    detail: str = PX3_DETAIL_CONTINUE
    reason: str = ""
    mode: str = ""
    state_updates: Optional[Dict[str, Any]] = None
    log_event: str = ""


def px3_capture_ratio(*, realized_pnl_pct: float, prem_hwm_pct: float) -> Optional[float]:
    try:
        hwm = float(prem_hwm_pct or 0.0)
        pnl = float(realized_pnl_pct or 0.0)
    except (TypeError, ValueError):
        return None
    if hwm <= 0:
        return None
    return pnl / hwm


def px3_keep_px2_prem_trail(
    *,
    realized_pnl_pct: float,
    prem_hwm_pct: float,
    cfg: Px3Config = DEFAULT_PX3_CONFIG,
) -> bool:
    """True when PX2 already captured a clean / ultra-clean print — do not override."""
    try:
        pnl = float(realized_pnl_pct or 0.0)
        hwm = float(prem_hwm_pct or 0.0)
    except (TypeError, ValueError):
        return False
    ratio = px3_capture_ratio(realized_pnl_pct=pnl, prem_hwm_pct=hwm)
    if ratio is None:
        return False
    if pnl + 1e-12 >= float(cfg.ultra_clean_pnl_pct) and ratio + 1e-12 >= float(
        cfg.ultra_clean_capture_ratio
    ):
        return True
    if pnl + 1e-12 >= float(cfg.clean_trend_pnl_pct) and ratio + 1e-12 >= float(
        cfg.clean_trend_capture_ratio
    ):
        return True
    return False


def px3_through_px2_giveback_floor(
    *,
    realized_pnl_pct: float,
    prem_hwm_pct: float,
    giveback_pct: float = PX2_DEFAULT_GIVEBACK_PCT,
) -> bool:
    """True when this tick is already below PX2's 20pp floor — bank PX2, do not start a runner.

    Cadence gaps (UBER 28 Aug: 100% HWM → 67% on a 38s tick) must not hand off
    trend_runner and then fill 33% on the next starve. Exceptional paths that
    require current ≥ +100% are evaluated before this guard.
    """
    try:
        pnl = float(realized_pnl_pct or 0.0)
        hwm = float(prem_hwm_pct or 0.0)
        gb = float(giveback_pct or 0.0)
    except (TypeError, ValueError):
        return False
    if gb <= 0.0:
        return False
    return pnl + 1e-12 < max(0.0, hwm - gb)


def px3_und_trail_broken(
    *,
    option_side: str,
    und_hwm_price: Optional[float],
    current_underlying: float,
    trail_pct: float,
) -> Optional[bool]:
    """True when current und has given back ``trail_pct`` from side-aligned HWM.

    None when the trail cannot be scored (missing und / HWM).
    """
    try:
        hwm = float(und_hwm_price) if und_hwm_price is not None else 0.0
        cur = float(current_underlying or 0.0)
        trail = float(trail_pct or 0.0)
    except (TypeError, ValueError):
        return None
    if hwm <= 0 or cur <= 0 or trail <= 0:
        return None
    side = str(option_side or "").strip().lower()
    if "put" in side or side == "short":
        return cur + 1e-12 >= hwm * (1.0 + trail)
    return cur <= hwm * (1.0 - trail) + 1e-12


def _parse_ts(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def px3_elapsed_seconds(start_ts: Any, now_ts: Any) -> Optional[float]:
    a = _parse_ts(start_ts)
    b = _parse_ts(now_ts)
    if a is None or b is None:
        return None
    try:
        if a.tzinfo is None and b.tzinfo is not None:
            b = b.replace(tzinfo=None)
        elif a.tzinfo is not None and b.tzinfo is None:
            a = a.replace(tzinfo=None)
        return (b - a).total_seconds()
    except Exception:
        return None


def evaluate_px3_at_prem_trail(
    *,
    realized_pnl_pct: float,
    prem_hwm_pct: float,
    und_hwm_fav_pct: Optional[float],
    option_side: str,
    und_hwm_price: Optional[float],
    current_underlying: float,
    entry_ts: Any = None,
    now_ts: Any = None,
    cfg: Px3Config = DEFAULT_PX3_CONFIG,
) -> Px3Decision:
    """Decide keep-PX2 vs trend-runner vs moon-probe at the PX2 ``premium_trail`` tick.

    Live-only inputs: realized pnl % and premium HWM **at this tick**, und HWM
    fav already stored on the open, and whether the current und has already
    broken the 0.4% trail. Session-peak / shadow leftover is not an input
    (that would be look-ahead).

    Call only when PX2 reason is ``premium_trail``. Disabled config still returns
    ``keep_px2`` so a mistaken call cannot change PX2.
    """
    if not cfg.enabled:
        return Px3Decision(action="keep_px2", detail=PX3_DETAIL_KEEP_PX2, reason="premium_trail")
    if px3_keep_px2_prem_trail(
        realized_pnl_pct=realized_pnl_pct, prem_hwm_pct=prem_hwm_pct, cfg=cfg
    ):
        return Px3Decision(
            action="keep_px2",
            detail=PX3_DETAIL_KEEP_PX2,
            reason="premium_trail",
            log_event="PX3_KEEP_PX2",
        )

    try:
        hwm = float(prem_hwm_pct or 0.0)
    except (TypeError, ValueError):
        hwm = 0.0
    try:
        pnl = float(realized_pnl_pct or 0.0)
    except (TypeError, ValueError):
        pnl = 0.0
    try:
        und_hwm = float(und_hwm_fav_pct) if und_hwm_fav_pct is not None else None
    except (TypeError, ValueError):
        und_hwm = None

    trail_broken = px3_und_trail_broken(
        option_side=option_side,
        und_hwm_price=und_hwm_price,
        current_underlying=current_underlying,
        trail_pct=cfg.price_trail_pct,
    )
    high_hwm_exceptional_trail_broken = px3_und_trail_broken(
        option_side=option_side,
        und_hwm_price=und_hwm_price,
        current_underlying=current_underlying,
        trail_pct=cfg.high_hwm_exceptional_price_trail_pct,
    )
    high_hwm_exceptional_clean = (
        bool(cfg.high_hwm_exceptional_enabled)
        and pnl + 1e-12 >= float(cfg.high_hwm_exceptional_min_current_pnl_pct)
        and hwm + 1e-12 >= float(cfg.high_hwm_exceptional_min_prem_hwm_pct)
        and und_hwm is not None
        and und_hwm + 1e-12 >= float(cfg.high_hwm_exceptional_min_und_hwm_pct)
        and high_hwm_exceptional_trail_broken is False
    )
    if high_hwm_exceptional_clean:
        return Px3Decision(
            action="continue",
            detail=PX3_DETAIL_HANDOFF_HIGH_HWM_EXCEPTIONAL,
            mode=PX3_MODE_HIGH_HWM_EXCEPTIONAL,
            state_updates={
                "px3_mode": PX3_MODE_HIGH_HWM_EXCEPTIONAL,
                "px3_prem_floor_pct": float(cfg.high_hwm_exceptional_prem_floor_pct),
                "px3_price_trail_pct": float(cfg.high_hwm_exceptional_price_trail_pct),
                "px3_handoff_ts": now_ts,
                "px3_und_hwm_price": und_hwm_price,
            },
            log_event="PX3_HANDOFF_HIGH_HWM_EXCEPTIONAL",
        )

    exceptional_trail_broken = px3_und_trail_broken(
        option_side=option_side,
        und_hwm_price=und_hwm_price,
        current_underlying=current_underlying,
        trail_pct=cfg.exceptional_price_trail_pct,
    )
    exceptional_clean = (
        bool(cfg.exceptional_enabled)
        and pnl + 1e-12 >= float(cfg.exceptional_min_current_pnl_pct)
        and hwm + 1e-12 >= float(cfg.exceptional_min_prem_hwm_pct)
        and und_hwm is not None
        and und_hwm + 1e-12 >= float(cfg.exceptional_min_und_hwm_pct)
        and exceptional_trail_broken is False
    )
    if exceptional_clean:
        return Px3Decision(
            action="continue",
            detail=PX3_DETAIL_HANDOFF_EXCEPTIONAL,
            mode=PX3_MODE_EXCEPTIONAL,
            state_updates={
                "px3_mode": PX3_MODE_EXCEPTIONAL,
                "px3_prem_floor_pct": float(cfg.exceptional_prem_floor_pct),
                "px3_price_trail_pct": float(cfg.exceptional_price_trail_pct),
                "px3_handoff_ts": now_ts,
                "px3_und_hwm_price": und_hwm_price,
            },
            log_event="PX3_HANDOFF_EXCEPTIONAL",
        )

    thin_lead_trail_broken = px3_und_trail_broken(
        option_side=option_side,
        und_hwm_price=und_hwm_price,
        current_underlying=current_underlying,
        trail_pct=cfg.thin_lead_exceptional_price_trail_pct,
    )
    thin_lead_exceptional_clean = (
        bool(cfg.thin_lead_exceptional_enabled)
        and pnl + 1e-12 >= float(cfg.thin_lead_exceptional_min_current_pnl_pct)
        and hwm + 1e-12 >= float(cfg.thin_lead_exceptional_min_prem_hwm_pct)
        and und_hwm is not None
        and und_hwm + 1e-12 >= float(cfg.thin_lead_exceptional_min_und_hwm_pct)
        and thin_lead_trail_broken is False
    )
    if thin_lead_exceptional_clean:
        return Px3Decision(
            action="continue",
            detail=PX3_DETAIL_HANDOFF_THIN_LEAD_EXCEPTIONAL,
            mode=PX3_MODE_THIN_LEAD_EXCEPTIONAL,
            state_updates={
                "px3_mode": PX3_MODE_THIN_LEAD_EXCEPTIONAL,
                "px3_prem_floor_pct": float(cfg.thin_lead_exceptional_prem_floor_pct),
                "px3_price_trail_pct": float(cfg.thin_lead_exceptional_price_trail_pct),
                "px3_handoff_ts": now_ts,
                "px3_und_hwm_price": und_hwm_price,
            },
            log_event="PX3_HANDOFF_THIN_LEAD_EXCEPTIONAL",
        )

    near_trend_trail_broken = px3_und_trail_broken(
        option_side=option_side,
        und_hwm_price=und_hwm_price,
        current_underlying=current_underlying,
        trail_pct=cfg.near_trend_price_trail_pct,
    )
    near_trend_clean = (
        bool(cfg.near_trend_enabled)
        and pnl + 1e-12 >= float(cfg.near_trend_min_current_pnl_pct)
        and hwm + 1e-12 >= float(cfg.near_trend_min_prem_hwm_pct)
        and und_hwm is not None
        and und_hwm + 1e-12 >= float(cfg.near_trend_min_und_hwm_pct)
        and near_trend_trail_broken is False
    )
    if near_trend_clean:
        return Px3Decision(
            action="continue",
            detail=PX3_DETAIL_HANDOFF_NEAR_TREND,
            mode=PX3_MODE_NEAR_TREND,
            state_updates={
                "px3_mode": PX3_MODE_NEAR_TREND,
                "px3_prem_floor_pct": float(cfg.near_trend_prem_floor_pct),
                "px3_price_trail_pct": float(cfg.near_trend_price_trail_pct),
                "px3_handoff_ts": now_ts,
                "px3_und_hwm_price": und_hwm_price,
            },
            log_event="PX3_HANDOFF_NEAR_TREND",
        )

    trend_clean = (
        hwm + 1e-12 >= float(cfg.trend_mfe_pct)
        and und_hwm is not None
        and und_hwm + 1e-12 >= float(cfg.trend_und_hwm_pct)
        and trail_broken is False
    )
    if trend_clean:
        if px3_through_px2_giveback_floor(realized_pnl_pct=pnl, prem_hwm_pct=hwm):
            return Px3Decision(
                action="keep_px2",
                detail=PX3_DETAIL_KEEP_PX2,
                reason="premium_trail",
                log_event="PX3_KEEP_PX2_THROUGH_FLOOR",
            )
        return Px3Decision(
            action="continue",
            detail=PX3_DETAIL_HANDOFF_TREND,
            mode=PX3_MODE_TREND,
            state_updates={
                "px3_mode": PX3_MODE_TREND,
                "px3_prem_floor_pct": float(cfg.trend_prem_floor_pct),
                "px3_handoff_ts": now_ts,
                "px3_und_hwm_price": und_hwm_price,
            },
            log_event="PX3_HANDOFF_TREND",
        )

    if hwm + 1e-12 >= float(cfg.moon_mfe_pct):
        return Px3Decision(
            action="continue",
            detail=PX3_DETAIL_HANDOFF_MOON,
            mode=PX3_MODE_MOON,
            state_updates={
                "px3_mode": PX3_MODE_MOON,
                "px3_prem_floor_pct": float(cfg.moon_prem_floor_pct),
                "px3_handoff_ts": now_ts,
                "px3_und_hwm_price": und_hwm_price,
            },
            log_event="PX3_HANDOFF_MOON",
        )

    return Px3Decision(action="keep_px2", detail=PX3_DETAIL_KEEP_PX2, reason="premium_trail")


def evaluate_px3_runner_tick(
    *,
    osnap: Dict[str, Any],
    option_side: str,
    current_underlying: float,
    current_pnl_pct: float,
    now_ts: Any = None,
    cfg: Px3Config = DEFAULT_PX3_CONFIG,
) -> Px3Decision:
    """Advance an already-handed-off PX3 runner. No-op unless ``px3_mode`` is set."""
    mode = str(osnap.get("px3_mode") or "")
    if mode not in (
        PX3_MODE_TREND,
        PX3_MODE_MOON,
        PX3_MODE_EXCEPTIONAL,
        PX3_MODE_THIN_LEAD_EXCEPTIONAL,
        PX3_MODE_HIGH_HWM_EXCEPTIONAL,
        PX3_MODE_NEAR_TREND,
        PX3_MODE_MFE_CONFIRMATION,
    ):
        return Px3Decision(action="continue", detail=PX3_DETAIL_CONTINUE)

    try:
        floor = float(osnap.get("px3_prem_floor_pct") or 0.0)
    except (TypeError, ValueError):
        if mode == PX3_MODE_MOON:
            floor = float(cfg.moon_prem_floor_pct)
        elif mode == PX3_MODE_EXCEPTIONAL:
            floor = float(cfg.exceptional_prem_floor_pct)
        elif mode == PX3_MODE_THIN_LEAD_EXCEPTIONAL:
            floor = float(cfg.thin_lead_exceptional_prem_floor_pct)
        elif mode == PX3_MODE_HIGH_HWM_EXCEPTIONAL:
            floor = float(cfg.high_hwm_exceptional_prem_floor_pct)
        elif mode == PX3_MODE_NEAR_TREND:
            floor = float(cfg.near_trend_prem_floor_pct)
        elif mode == PX3_MODE_MFE_CONFIRMATION:
            floor = float(cfg.mfe_confirm_prem_floor_pct)
        else:
            floor = float(cfg.trend_prem_floor_pct)
    try:
        pnl = float(current_pnl_pct or 0.0)
    except (TypeError, ValueError):
        pnl = 0.0

    und_hwm = update_price_hwm(
        option_side=option_side,
        hwm=osnap.get("px3_und_hwm_price"),
        current_underlying=current_underlying,
    )
    updates: Dict[str, Any] = {"px3_und_hwm_price": und_hwm, "px3_mode": mode}
    if mode == PX3_MODE_MFE_CONFIRMATION:
        try:
            prem_hwm = max(float(osnap.get("px3_prem_hwm_pct") or 0.0), pnl)
        except (TypeError, ValueError):
            prem_hwm = pnl
        try:
            giveback = float(
                osnap.get("px3_mfe_confirm_runner_giveback_pct")
                if osnap.get("px3_mfe_confirm_runner_giveback_pct") is not None
                else cfg.mfe_confirm_runner_giveback_pct
            )
        except (TypeError, ValueError):
            giveback = float(cfg.mfe_confirm_runner_giveback_pct)
        floor = max(floor, prem_hwm - giveback)
        updates["px3_prem_hwm_pct"] = prem_hwm

    if osnap.get("px3_skip_floor_once"):
        osnap["px3_skip_floor_once"] = False
        updates["px3_skip_floor_once"] = False
        updates["px3_last_detail"] = PX3_DETAIL_RESTART_GAP_HOLD
        return Px3Decision(
            action="continue",
            detail=PX3_DETAIL_RESTART_GAP_HOLD,
            mode=mode,
            state_updates=updates,
            log_event="PX3_RESTART_GAP_HOLD",
        )

    if pnl <= floor + 1e-12:
        updates["px3_last_detail"] = PX3_DETAIL_PREM_FLOOR
        return Px3Decision(
            action="exit",
            detail=PX3_DETAIL_PREM_FLOOR,
            reason=PX3_REASON_PREM_TRAIL,
            mode=mode,
            state_updates=updates,
            log_event="PX3_PREM_FLOOR",
        )

    if str(osnap.get("underlying_source") or "").strip().lower() == "unavailable":
        broken = None
    else:
        broken = px3_und_trail_broken(
            option_side=option_side,
            und_hwm_price=und_hwm,
            current_underlying=current_underlying,
            trail_pct=osnap.get("px3_price_trail_pct", cfg.price_trail_pct),
        )
    if broken is True:
        updates["px3_last_detail"] = PX3_DETAIL_PRICE_TRAIL
        return Px3Decision(
            action="exit",
            detail=PX3_DETAIL_PRICE_TRAIL,
            reason=PX3_REASON_PRICE_TRAIL,
            mode=mode,
            state_updates=updates,
            log_event="PX3_PRICE_TRAIL",
        )

    if mode == PX3_MODE_MOON:
        held = px3_elapsed_seconds(osnap.get("px3_handoff_ts"), now_ts)
        try:
            patience = float(cfg.moon_patience_seconds or 0.0)
        except (TypeError, ValueError):
            patience = 300.0
        if held is not None and patience > 0 and held + 1e-12 >= patience:
            updates["px3_last_detail"] = PX3_DETAIL_MOON_PATIENCE
            return Px3Decision(
                action="exit",
                detail=PX3_DETAIL_MOON_PATIENCE,
                reason=PX3_REASON_PREM_TRAIL,
                mode=mode,
                state_updates=updates,
                log_event="PX3_MOON_PATIENCE",
            )

    updates["px3_last_detail"] = PX3_DETAIL_CONTINUE
    return Px3Decision(
        action="continue",
        detail=PX3_DETAIL_CONTINUE,
        mode=mode,
        state_updates=updates,
    )


def apply_px3_state_updates(osnap: Dict[str, Any], updates: Optional[Dict[str, Any]]) -> None:
    if not updates or not isinstance(osnap, dict):
        return
    for k, v in updates.items():
        osnap[k] = v


def px3_mfe_confirm_pending(osnap: Dict[str, Any]) -> bool:
    if str((osnap or {}).get("px3_mode") or "") == PX3_MODE_MFE_CONFIRMATION:
        return False
    return bool((osnap or {}).get("px3_mfe_confirm_pending"))


def px3_active(osnap: Dict[str, Any]) -> bool:
    mode = str((osnap or {}).get("px3_mode") or "")
    return mode in (
        PX3_MODE_TREND,
        PX3_MODE_MOON,
        PX3_MODE_EXCEPTIONAL,
        PX3_MODE_THIN_LEAD_EXCEPTIONAL,
        PX3_MODE_HIGH_HWM_EXCEPTIONAL,
        PX3_MODE_NEAR_TREND,
        PX3_MODE_MFE_CONFIRMATION,
    )


def evaluate_px3_at_mfe_take_profit(
    *,
    realized_pnl_pct: float,
    prem_hwm_pct: float,
    und_hwm_fav_pct: Optional[float],
    option_side: str,
    und_hwm_price: Optional[float],
    current_underlying: float,
    entry_ts: Any = None,
    now_ts: Any = None,
    cfg: Px3Config = DEFAULT_PX3_CONFIG,
) -> Px3Decision:
    """Optionally defer a PX2 stall-bank for a confirmed runner.

    This is dual-sided. It only uses state available at the original
    ``mfe_take_profit`` tick; the next tick is evaluated by
    ``evaluate_px3_mfe_confirmation_tick``.
    """
    if not cfg.enabled or not bool(cfg.mfe_confirm_enabled):
        return Px3Decision(action="keep_px2", detail=PX3_DETAIL_KEEP_PX2, reason=PX3_REASON_MFE_TAKE_PROFIT)
    try:
        pnl = float(realized_pnl_pct or 0.0)
        hwm = float(prem_hwm_pct or 0.0)
        und_hwm = float(und_hwm_fav_pct) if und_hwm_fav_pct is not None else None
    except (TypeError, ValueError):
        return Px3Decision(action="keep_px2", detail=PX3_DETAIL_KEEP_PX2, reason=PX3_REASON_MFE_TAKE_PROFIT)
    broken = px3_und_trail_broken(
        option_side=option_side,
        und_hwm_price=und_hwm_price,
        current_underlying=current_underlying,
        trail_pct=cfg.mfe_confirm_price_trail_pct,
    )
    base_standard_confirm = (
        pnl + 1e-12 >= float(cfg.mfe_confirm_min_current_pnl_pct)
        and hwm + 1e-12 >= float(cfg.mfe_confirm_min_prem_hwm_pct)
        and und_hwm is not None
        and und_hwm + 1e-12 >= float(cfg.mfe_confirm_min_und_hwm_pct)
        and broken is False
    )
    entry_age = px3_elapsed_seconds(entry_ts, now_ts)
    normal_age_ok = (
        entry_age is not None
        and entry_age + 1e-12 >= float(cfg.mfe_confirm_normal_min_entry_age_seconds)
        and entry_age <= float(cfg.mfe_confirm_normal_max_entry_age_seconds) + 1e-12
    )
    standard_confirm = base_standard_confirm and normal_age_ok
    recovery_age_ok = (
        entry_age is not None
        and entry_age <= float(cfg.mfe_confirm_recovery_max_entry_age_seconds) + 1e-12
    )
    thin_recovery = (
        bool(cfg.mfe_confirm_thin_recovery_enabled)
        and pnl + 1e-12 >= float(cfg.mfe_confirm_thin_recovery_min_current_pnl_pct)
        and (
            pnl < float(cfg.mfe_confirm_thin_recovery_deadzone_min_pnl_pct)
            or pnl + 1e-12 >= float(cfg.mfe_confirm_thin_recovery_deadzone_max_pnl_pct)
        )
        and hwm + 1e-12 >= float(cfg.mfe_confirm_min_prem_hwm_pct)
        and hwm <= float(cfg.mfe_confirm_thin_recovery_max_prem_hwm_pct) + 1e-12
        and und_hwm is not None
        and und_hwm + 1e-12 >= float(cfg.mfe_confirm_thin_recovery_min_und_hwm_pct)
        and und_hwm < float(cfg.mfe_confirm_thin_recovery_max_und_hwm_pct) - 1e-12
        and broken is False
    )
    strong_und_recovery = (
        recovery_age_ok
        and base_standard_confirm
        and pnl + 1e-12 >= float(cfg.mfe_confirm_strong_recovery_min_current_pnl_pct)
        and und_hwm + 1e-12 >= float(cfg.mfe_confirm_strong_und_hwm_pct)
    )
    late_strong_age_ok = (
        entry_age is not None
        and entry_age > float(cfg.mfe_confirm_recovery_max_entry_age_seconds) + 1e-12
        and entry_age <= float(cfg.mfe_confirm_late_strong_recovery_max_entry_age_seconds) + 1e-12
    )
    late_strong_und_recovery = (
        bool(cfg.mfe_confirm_late_strong_recovery_enabled)
        and late_strong_age_ok
        and pnl + 1e-12 >= float(cfg.mfe_confirm_late_strong_recovery_min_current_pnl_pct)
        and hwm + 1e-12 >= float(cfg.mfe_confirm_late_strong_recovery_min_prem_hwm_pct)
        and hwm <= float(cfg.mfe_confirm_late_strong_recovery_max_prem_hwm_pct) + 1e-12
        and und_hwm is not None
        and und_hwm + 1e-12 >= float(cfg.mfe_confirm_late_strong_recovery_min_und_hwm_pct)
        and broken is False
    )
    if standard_confirm or strong_und_recovery or late_strong_und_recovery or (recovery_age_ok and thin_recovery):
        floor = float(pnl)
        window = float(cfg.mfe_confirm_window_seconds)
        recovery_kind = ""
        if strong_und_recovery or late_strong_und_recovery:
            floor = max(0.0, float(pnl) - float(cfg.mfe_confirm_recovery_floor_giveback_pct))
            window = float(cfg.mfe_confirm_recovery_window_seconds)
            recovery_kind = "late_strong_underlying" if late_strong_und_recovery else "strong_underlying"
        elif recovery_age_ok and thin_recovery and not standard_confirm:
            floor = max(
                0.0,
                float(pnl) - float(cfg.mfe_confirm_thin_recovery_floor_giveback_pct),
            )
            window = float(cfg.mfe_confirm_thin_recovery_window_seconds)
            recovery_kind = "thin_underlying"
        return Px3Decision(
            action="continue",
            detail=PX3_DETAIL_MFE_CONFIRM_PENDING,
            state_updates={
                "px3_mfe_confirm_pending": True,
                "px3_mfe_confirm_original_pnl_pct": float(pnl),
                "px3_mfe_confirm_original_prem_hwm_pct": float(hwm),
                "px3_mfe_confirm_ts": now_ts,
                "px3_mfe_confirm_floor_pct": floor,
                "px3_mfe_confirm_strong_und_recovery": strong_und_recovery,
                "px3_mfe_confirm_late_strong_und_recovery": late_strong_und_recovery,
                "px3_mfe_confirm_recovery_kind": recovery_kind,
                "px3_mfe_confirm_window_seconds": window,
                "px3_und_hwm_price": und_hwm_price,
                "px3_price_trail_pct": float(cfg.mfe_confirm_price_trail_pct),
                "px3_prem_floor_pct": max(float(cfg.mfe_confirm_prem_floor_pct), float(pnl)),
                "px3_mfe_confirm_runner_giveback_pct": float(cfg.mfe_confirm_runner_giveback_pct),
            },
            log_event="PX3_MFE_CONFIRM_PENDING",
        )
    return Px3Decision(action="keep_px2", detail=PX3_DETAIL_KEEP_PX2, reason=PX3_REASON_MFE_TAKE_PROFIT)


def evaluate_px3_mfe_confirmation_tick(
    *,
    osnap: Dict[str, Any],
    option_side: str,
    current_underlying: float,
    current_pnl_pct: float,
    now_ts: Any = None,
    cfg: Px3Config = DEFAULT_PX3_CONFIG,
) -> Px3Decision:
    """Resolve MFE confirmation: runner handoff, pending recovery, or fail-bank."""
    if not px3_mfe_confirm_pending(osnap):
        return Px3Decision(action="continue", detail=PX3_DETAIL_CONTINUE)
    try:
        original = float(osnap.get("px3_mfe_confirm_original_pnl_pct") or 0.0)
        pnl = float(current_pnl_pct or 0.0)
    except (TypeError, ValueError):
        original = 0.0
        pnl = 0.0

    und_hwm = update_price_hwm(
        option_side=option_side,
        hwm=osnap.get("px3_und_hwm_price"),
        current_underlying=current_underlying,
    )
    held = px3_elapsed_seconds(osnap.get("px3_mfe_confirm_ts"), now_ts)
    try:
        floor_raw = osnap.get("px3_mfe_confirm_floor_pct")
        floor = float(original if floor_raw is None else floor_raw)
    except (TypeError, ValueError):
        floor = original
    try:
        window_raw = osnap.get("px3_mfe_confirm_window_seconds")
        window = float(cfg.mfe_confirm_window_seconds if window_raw is None else window_raw)
    except (TypeError, ValueError):
        window = float(cfg.mfe_confirm_window_seconds)

    base_updates: Dict[str, Any] = {
        "px3_mfe_confirm_pending": True,
        "px3_und_hwm_price": und_hwm,
    }
    if pnl - original + 1e-12 >= float(cfg.mfe_confirm_delta_pct):
        recovery_kind = str(osnap.get("px3_mfe_confirm_recovery_kind") or "")
        if recovery_kind == "thin_underlying":
            runner_floor = original
            runner_giveback = float(cfg.mfe_confirm_thin_recovery_runner_giveback_pct)
        else:
            runner_floor = max(float(cfg.mfe_confirm_prem_floor_pct), original)
            runner_giveback = float(cfg.mfe_confirm_runner_giveback_pct)
        return Px3Decision(
            action="continue",
            detail=PX3_DETAIL_MFE_CONFIRM_HANDOFF,
            mode=PX3_MODE_MFE_CONFIRMATION,
            state_updates={
                **base_updates,
                "px3_mfe_confirm_pending": False,
                "px3_mode": PX3_MODE_MFE_CONFIRMATION,
                "px3_prem_floor_pct": runner_floor,
                "px3_prem_hwm_pct": max(original, pnl),
                "px3_mfe_confirm_runner_giveback_pct": runner_giveback,
                "px3_price_trail_pct": float(cfg.mfe_confirm_price_trail_pct),
                "px3_handoff_ts": now_ts,
                "px3_last_detail": PX3_DETAIL_MFE_CONFIRM_HANDOFF,
            },
            log_event="PX3_MFE_CONFIRM_HANDOFF",
        )
    if pnl + 1e-12 >= floor and (held is None or held + 1e-12 < window):
        return Px3Decision(
            action="continue",
            detail=PX3_DETAIL_MFE_CONFIRM_PENDING,
            state_updates={
                **base_updates,
                "px3_last_detail": PX3_DETAIL_MFE_CONFIRM_PENDING,
            },
            log_event="PX3_MFE_CONFIRM_PENDING",
        )

    return Px3Decision(
        action="exit",
        detail=PX3_DETAIL_MFE_CONFIRM_FAIL_BANK,
        reason=PX3_REASON_MFE_TAKE_PROFIT,
        state_updates={
            **base_updates,
            "px3_mfe_confirm_pending": False,
            "px3_last_detail": PX3_DETAIL_MFE_CONFIRM_FAIL_BANK,
        },
        log_event="PX3_MFE_CONFIRM_FAIL_BANK",
    )


# Silence unused import if a caller wants fav at handoff from current und.
def px3_und_hwm_price_from_fav(
    *,
    option_side: str,
    entry_underlying: float,
    und_hwm_fav_pct: Optional[float],
    current_underlying: float,
) -> Optional[float]:
    """Side-aligned und HWM price from stored fav %. Falls back to current und."""
    try:
        entry_u = float(entry_underlying or 0.0)
        cur = float(current_underlying or 0.0)
        fav = float(und_hwm_fav_pct) if und_hwm_fav_pct is not None else None
    except (TypeError, ValueError):
        return None
    if fav is not None and entry_u > 0:
        side = str(option_side or "").strip().lower()
        if "put" in side or side == "short":
            hwm = entry_u * (1.0 - fav)
        else:
            hwm = entry_u * (1.0 + fav)
        if hwm > 0:
            return hwm
    return cur if cur > 0 else None


def px3_und_fav_pct(
    *, option_side: str, entry_underlying: float, current_underlying: float
) -> Optional[float]:
    return underlying_favorable_move_pct(
        option_side=option_side,
        entry_underlying=entry_underlying,
        current_underlying=current_underlying,
    )


def px3_config_from_engine(config: Any) -> Px3Config:
    """Read PX3 knobs off stealth engine config. Missing attrs → defaults (OFF)."""
    def _b(name: str, default: bool) -> bool:
        try:
            return bool(getattr(config, name, default))
        except Exception:
            return default

    def _f(name: str, default: float) -> float:
        try:
            v = getattr(config, name, default)
            return float(v if v is not None else default)
        except (TypeError, ValueError):
            return float(default)

    return Px3Config(
        enabled=_b("dte_single_px3_enabled", False),
        trend_mfe_pct=_f("dte_single_px3_trend_mfe_pct", 1.00),
        trend_und_hwm_pct=_f("dte_single_px3_trend_und_hwm_pct", 0.004),
        price_trail_pct=_f("dte_single_px3_price_trail_pct", 0.004),
        trend_prem_floor_pct=_f("dte_single_px3_trend_prem_floor_pct", 0.50),
        moon_mfe_pct=_f("dte_single_px3_moon_mfe_pct", 2.00),
        moon_patience_seconds=_f("dte_single_px3_moon_patience_seconds", 300.0),
        moon_prem_floor_pct=_f("dte_single_px3_moon_prem_floor_pct", 0.75),
        ultra_clean_pnl_pct=_f("dte_single_px3_ultra_clean_pnl_pct", 7.50),
        ultra_clean_capture_ratio=_f("dte_single_px3_ultra_clean_capture_ratio", 0.85),
        clean_trend_pnl_pct=_f("dte_single_px3_clean_trend_pnl_pct", 2.50),
        clean_trend_capture_ratio=_f("dte_single_px3_clean_trend_capture_ratio", 0.90),
        exceptional_enabled=_b("dte_single_px3_exceptional_enabled", False),
        exceptional_min_current_pnl_pct=_f(
            "dte_single_px3_exceptional_min_current_pnl_pct", 1.00
        ),
        exceptional_min_prem_hwm_pct=_f("dte_single_px3_exceptional_min_prem_hwm_pct", 0.60),
        exceptional_min_und_hwm_pct=_f("dte_single_px3_exceptional_min_und_hwm_pct", 0.005),
        exceptional_price_trail_pct=_f("dte_single_px3_exceptional_price_trail_pct", 0.005),
        exceptional_prem_floor_pct=_f("dte_single_px3_exceptional_prem_floor_pct", 0.75),
        thin_lead_exceptional_enabled=_b("dte_single_px3_thin_lead_exceptional_enabled", False),
        thin_lead_exceptional_min_current_pnl_pct=_f(
            "dte_single_px3_thin_lead_exceptional_min_current_pnl_pct", 1.00
        ),
        thin_lead_exceptional_min_prem_hwm_pct=_f(
            "dte_single_px3_thin_lead_exceptional_min_prem_hwm_pct", 0.60
        ),
        thin_lead_exceptional_min_und_hwm_pct=_f(
            "dte_single_px3_thin_lead_exceptional_min_und_hwm_pct", 0.001
        ),
        thin_lead_exceptional_price_trail_pct=_f(
            "dte_single_px3_thin_lead_exceptional_price_trail_pct", 0.004
        ),
        thin_lead_exceptional_prem_floor_pct=_f(
            "dte_single_px3_thin_lead_exceptional_prem_floor_pct", 0.25
        ),
        high_hwm_exceptional_enabled=_b("dte_single_px3_high_hwm_exceptional_enabled", False),
        high_hwm_exceptional_min_current_pnl_pct=_f(
            "dte_single_px3_high_hwm_exceptional_min_current_pnl_pct", 1.00
        ),
        high_hwm_exceptional_min_prem_hwm_pct=_f(
            "dte_single_px3_high_hwm_exceptional_min_prem_hwm_pct", 1.25
        ),
        high_hwm_exceptional_min_und_hwm_pct=_f(
            "dte_single_px3_high_hwm_exceptional_min_und_hwm_pct", 0.004
        ),
        high_hwm_exceptional_price_trail_pct=_f(
            "dte_single_px3_high_hwm_exceptional_price_trail_pct", 0.004
        ),
        high_hwm_exceptional_prem_floor_pct=_f(
            "dte_single_px3_high_hwm_exceptional_prem_floor_pct", 0.50
        ),
        near_trend_enabled=_b("dte_single_px3_near_trend_enabled", False),
        near_trend_min_current_pnl_pct=_f(
            "dte_single_px3_near_trend_min_current_pnl_pct", 0.70
        ),
        near_trend_min_prem_hwm_pct=_f("dte_single_px3_near_trend_min_prem_hwm_pct", 0.90),
        near_trend_min_und_hwm_pct=_f("dte_single_px3_near_trend_min_und_hwm_pct", 0.0009),
        near_trend_price_trail_pct=_f("dte_single_px3_near_trend_price_trail_pct", 0.004),
        near_trend_prem_floor_pct=_f("dte_single_px3_near_trend_prem_floor_pct", 0.50),
        mfe_confirm_enabled=_b("dte_single_px3_mfe_confirm_enabled", False),
        mfe_confirm_min_current_pnl_pct=_f(
            "dte_single_px3_mfe_confirm_min_current_pnl_pct", 0.20
        ),
        mfe_confirm_min_prem_hwm_pct=_f(
            "dte_single_px3_mfe_confirm_min_prem_hwm_pct", 0.30
        ),
        mfe_confirm_min_und_hwm_pct=_f(
            "dte_single_px3_mfe_confirm_min_und_hwm_pct", 0.0025
        ),
        mfe_confirm_normal_min_entry_age_seconds=_f(
            "dte_single_px3_mfe_confirm_normal_min_entry_age_seconds", 1200.0
        ),
        mfe_confirm_normal_max_entry_age_seconds=_f(
            "dte_single_px3_mfe_confirm_normal_max_entry_age_seconds", 1800.0
        ),
        mfe_confirm_delta_pct=_f("dte_single_px3_mfe_confirm_delta_pct", 0.15),
        mfe_confirm_window_seconds=_f("dte_single_px3_mfe_confirm_window_seconds", 120.0),
        mfe_confirm_thin_recovery_enabled=_b(
            "dte_single_px3_mfe_confirm_thin_recovery_enabled", True
        ),
        mfe_confirm_thin_recovery_min_current_pnl_pct=_f(
            "dte_single_px3_mfe_confirm_thin_recovery_min_current_pnl_pct", 0.15
        ),
        mfe_confirm_thin_recovery_deadzone_min_pnl_pct=_f(
            "dte_single_px3_mfe_confirm_thin_recovery_deadzone_min_pnl_pct", 0.20
        ),
        mfe_confirm_thin_recovery_deadzone_max_pnl_pct=_f(
            "dte_single_px3_mfe_confirm_thin_recovery_deadzone_max_pnl_pct", 0.35
        ),
        mfe_confirm_thin_recovery_min_und_hwm_pct=_f(
            "dte_single_px3_mfe_confirm_thin_recovery_min_und_hwm_pct", 0.001
        ),
        mfe_confirm_thin_recovery_max_und_hwm_pct=_f(
            "dte_single_px3_mfe_confirm_thin_recovery_max_und_hwm_pct", 0.0025
        ),
        mfe_confirm_thin_recovery_max_prem_hwm_pct=_f(
            "dte_single_px3_mfe_confirm_thin_recovery_max_prem_hwm_pct", 0.75
        ),
        mfe_confirm_thin_recovery_window_seconds=_f(
            "dte_single_px3_mfe_confirm_thin_recovery_window_seconds", 600.0
        ),
        mfe_confirm_thin_recovery_floor_giveback_pct=_f(
            "dte_single_px3_mfe_confirm_thin_recovery_floor_giveback_pct", 0.10
        ),
        mfe_confirm_thin_recovery_runner_giveback_pct=_f(
            "dte_single_px3_mfe_confirm_thin_recovery_runner_giveback_pct", 0.50
        ),
        mfe_confirm_strong_recovery_min_current_pnl_pct=_f(
            "dte_single_px3_mfe_confirm_strong_recovery_min_current_pnl_pct", 0.29
        ),
        mfe_confirm_strong_und_hwm_pct=_f(
            "dte_single_px3_mfe_confirm_strong_und_hwm_pct", 0.010
        ),
        mfe_confirm_recovery_max_entry_age_seconds=_f(
            "dte_single_px3_mfe_confirm_recovery_max_entry_age_seconds", 1200.0
        ),
        mfe_confirm_recovery_window_seconds=_f(
            "dte_single_px3_mfe_confirm_recovery_window_seconds", 900.0
        ),
        mfe_confirm_recovery_floor_giveback_pct=_f(
            "dte_single_px3_mfe_confirm_recovery_floor_giveback_pct", 0.20
        ),
        mfe_confirm_late_strong_recovery_enabled=_b(
            "dte_single_px3_mfe_confirm_late_strong_recovery_enabled", False
        ),
        mfe_confirm_late_strong_recovery_min_current_pnl_pct=_f(
            "dte_single_px3_mfe_confirm_late_strong_recovery_min_current_pnl_pct", 0.35
        ),
        mfe_confirm_late_strong_recovery_min_prem_hwm_pct=_f(
            "dte_single_px3_mfe_confirm_late_strong_recovery_min_prem_hwm_pct", 0.60
        ),
        mfe_confirm_late_strong_recovery_max_prem_hwm_pct=_f(
            "dte_single_px3_mfe_confirm_late_strong_recovery_max_prem_hwm_pct", 1.50
        ),
        mfe_confirm_late_strong_recovery_min_und_hwm_pct=_f(
            "dte_single_px3_mfe_confirm_late_strong_recovery_min_und_hwm_pct", 0.0075
        ),
        mfe_confirm_late_strong_recovery_max_entry_age_seconds=_f(
            "dte_single_px3_mfe_confirm_late_strong_recovery_max_entry_age_seconds", 3600.0
        ),
        mfe_confirm_price_trail_pct=_f(
            "dte_single_px3_mfe_confirm_price_trail_pct", 0.004
        ),
        mfe_confirm_prem_floor_pct=_f(
            "dte_single_px3_mfe_confirm_prem_floor_pct", 0.30
        ),
        mfe_confirm_runner_giveback_pct=_f(
            "dte_single_px3_mfe_confirm_runner_giveback_pct", 0.50
        ),
    )
