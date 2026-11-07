"""PC-PX2.5 exit extension — no re-entry, no new trades.

PX2.5 is a narrow profit-capture overlay. It only evaluates when an existing
winner is about to exit from PX2 / PX3 style profit capture. If the trade still
has an intact side-aligned price trend, PX2.5 holds the same open position for a
short extension and exits on a 1% price trail, a 900s max-extension guard, or a
configured premium floor.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

try:
    from modules.price_runner_exit import underlying_favorable_move_pct, update_price_hwm
    from modules.px3_exit import px3_capture_ratio, px3_und_trail_broken
except Exception:
    from price_runner_exit import underlying_favorable_move_pct, update_price_hwm  # type: ignore
    from px3_exit import px3_capture_ratio, px3_und_trail_broken  # type: ignore


PX25_DETAIL_KEEP_EXIT = "keep_exit"
PX25_DETAIL_HANDOFF = "px25_handoff"
PX25_DETAIL_PRICE_TRAIL = "px25_price_trail"
PX25_DETAIL_TIME = "px25_max_extension"
PX25_DETAIL_PREM_FLOOR = "px25_prem_floor"
PX25_DETAIL_THIN_UND_MFE_BANK = "px25_thin_und_mfe_bank"
PX25_DETAIL_CONTINUE = "continue"

PX25_ALLOWED_REASONS = {"premium_trail", "price_trail"}
PX25_BLOCKED_REASONS = {
    "price_stop",
    "premium_stop",
    "breakeven",
    "scratch",
    "no_progress",
    "time_exit",
    "end_of_day_exit",
    "no_data",
    "degraded_data",
}


@dataclass(frozen=True)
class Px25Config:
    enabled: bool = False
    min_prem_hwm_pct: float = 1.50
    exclude_ultra_clean_pnl_pct: float = 7.50
    exclude_ultra_clean_capture_ratio: float = 0.85
    price_trail_pct: float = 0.01
    max_extension_seconds: float = 900.0
    prem_floor_pct: float = 0.00
    mfe_thin_und_bank_enabled: bool = True
    mfe_thin_und_fav_hwm_pct: float = 0.003
    mfe_thin_und_min_prem_hwm_pct: float = 1.50


DEFAULT_PX25_CONFIG = Px25Config()


@dataclass(frozen=True)
class Px25Decision:
    action: str  # continue | exit | keep_exit
    detail: str = PX25_DETAIL_CONTINUE
    reason: str = ""
    state_updates: Optional[Dict[str, Any]] = None
    log_event: str = ""


def _parse_ts(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def px25_elapsed_seconds(start_ts: Any, now_ts: Any) -> Optional[float]:
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


def px25_active(osnap: Dict[str, Any]) -> bool:
    return bool(isinstance(osnap, dict) and osnap.get("px25_active"))


def px25_und_hwm_price_from_current_or_existing(
    *,
    osnap: Dict[str, Any],
    option_side: str,
    current_underlying: float,
) -> Optional[float]:
    try:
        cur = float(current_underlying or 0.0)
    except (TypeError, ValueError):
        cur = 0.0
    existing = None
    for key in ("px25_und_hwm_price", "px3_und_hwm_price", "price_runner_hwm"):
        try:
            v = osnap.get(key) if isinstance(osnap, dict) else None
            if v is not None and float(v) > 0:
                existing = float(v)
                break
        except (TypeError, ValueError):
            continue
    if existing is None:
        return cur if cur > 0 else None
    return update_price_hwm(
        option_side=option_side,
        hwm=existing,
        current_underlying=cur,
    )


def _is_ultra_clean(*, current_pnl_pct: float, prem_hwm_pct: float, cfg: Px25Config) -> bool:
    ratio = px3_capture_ratio(realized_pnl_pct=current_pnl_pct, prem_hwm_pct=prem_hwm_pct)
    if ratio is None:
        return False
    return (
        float(current_pnl_pct or 0.0) + 1e-12 >= float(cfg.exclude_ultra_clean_pnl_pct)
        and float(ratio) + 1e-12 >= float(cfg.exclude_ultra_clean_capture_ratio)
    )


def _entry_underlying_from_osnap(osnap: Dict[str, Any]) -> Optional[float]:
    if not isinstance(osnap, dict):
        return None
    for key in ("entry_underlying", "entry_underlying_price", "underlying_entry_price"):
        try:
            value = osnap.get(key)
            if value is not None and float(value) > 0:
                return float(value)
        except (TypeError, ValueError):
            continue
    engine = osnap.get("engine")
    if isinstance(engine, dict):
        for key in ("entry_underlying", "entry_underlying_price", "underlying_entry_price"):
            try:
                value = engine.get(key)
                if value is not None and float(value) > 0:
                    return float(value)
            except (TypeError, ValueError):
                continue
    return None


def _side_aligned_hwm_fav_pct(
    *,
    osnap: Dict[str, Any],
    option_side: str,
    und_hwm_price: Optional[float],
) -> Optional[float]:
    entry_underlying = _entry_underlying_from_osnap(osnap)
    if entry_underlying is None or und_hwm_price is None:
        return None
    fav = underlying_favorable_move_pct(
        option_side=option_side,
        entry_underlying=float(entry_underlying),
        current_underlying=float(und_hwm_price),
    )
    if fav is None:
        return None
    return max(0.0, float(fav))


def _thin_und_mfe_bank(
    *,
    osnap: Dict[str, Any],
    reason: str,
    prem_hwm_pct: float,
    option_side: str,
    und_hwm_price: Optional[float],
    cfg: Px25Config,
) -> bool:
    if not cfg.mfe_thin_und_bank_enabled:
        return False
    if reason not in {"mfe_take_profit", "take_profit"}:
        return False
    if float(prem_hwm_pct or 0.0) + 1e-12 < float(cfg.mfe_thin_und_min_prem_hwm_pct):
        return False
    fav = _side_aligned_hwm_fav_pct(
        osnap=osnap,
        option_side=option_side,
        und_hwm_price=und_hwm_price,
    )
    if fav is None:
        return False
    return float(fav) + 1e-12 < float(cfg.mfe_thin_und_fav_hwm_pct)


def evaluate_px25_at_exit(
    *,
    osnap: Dict[str, Any],
    candidate_reason: str,
    current_pnl_pct: float,
    prem_hwm_pct: float,
    option_side: str,
    und_hwm_price: Optional[float],
    current_underlying: float,
    now_ts: Any = None,
    cfg: Px25Config = DEFAULT_PX25_CONFIG,
) -> Px25Decision:
    """Decide whether a winning exit should be extended.

    This is intentionally fail-closed. It never overrides red or defensive exits,
    never creates a new entry, and never extends ultra-clean captures where PX2/PX3
    already banked at least the configured capture ratio.
    """
    if not cfg.enabled:
        return Px25Decision(action="keep_exit", detail=PX25_DETAIL_KEEP_EXIT, reason=candidate_reason)
    reason = str(candidate_reason or "").strip()
    if reason in {"mfe_take_profit", "take_profit"}:
        if _thin_und_mfe_bank(
            osnap=osnap,
            reason=reason,
            prem_hwm_pct=prem_hwm_pct,
            option_side=option_side,
            und_hwm_price=und_hwm_price,
            cfg=cfg,
        ):
            return Px25Decision(
                action="keep_exit",
                detail=PX25_DETAIL_THIN_UND_MFE_BANK,
                reason=reason,
            )
        return Px25Decision(action="keep_exit", detail=PX25_DETAIL_KEEP_EXIT, reason=reason)
    if reason in PX25_BLOCKED_REASONS or reason not in PX25_ALLOWED_REASONS:
        return Px25Decision(action="keep_exit", detail=PX25_DETAIL_KEEP_EXIT, reason=reason)
    try:
        pnl = float(current_pnl_pct or 0.0)
        hwm = float(prem_hwm_pct or 0.0)
    except (TypeError, ValueError):
        pnl = 0.0
        hwm = 0.0
    if pnl <= 0 or hwm + 1e-12 < float(cfg.min_prem_hwm_pct):
        return Px25Decision(action="keep_exit", detail=PX25_DETAIL_KEEP_EXIT, reason=reason)
    if _is_ultra_clean(current_pnl_pct=pnl, prem_hwm_pct=hwm, cfg=cfg):
        return Px25Decision(action="keep_exit", detail=PX25_DETAIL_KEEP_EXIT, reason=reason)
    if _thin_und_mfe_bank(
        osnap=osnap,
        reason=reason,
        prem_hwm_pct=hwm,
        option_side=option_side,
        und_hwm_price=und_hwm_price,
        cfg=cfg,
    ):
        return Px25Decision(
            action="keep_exit",
            detail=PX25_DETAIL_THIN_UND_MFE_BANK,
            reason=reason,
        )

    broken = px3_und_trail_broken(
        option_side=option_side,
        und_hwm_price=und_hwm_price,
        current_underlying=current_underlying,
        trail_pct=cfg.price_trail_pct,
    )
    if broken is not False:
        return Px25Decision(action="keep_exit", detail=PX25_DETAIL_KEEP_EXIT, reason=reason)

    return Px25Decision(
        action="continue",
        detail=PX25_DETAIL_HANDOFF,
        reason=reason,
        state_updates={
            "px25_active": True,
            "px25_handoff_ts": now_ts,
            "px25_original_reason": reason,
            "px25_prem_floor_pct": float(cfg.prem_floor_pct),
            "px25_price_trail_pct": float(cfg.price_trail_pct),
            "px25_und_hwm_price": und_hwm_price,
            "px25_start_pnl_pct": float(pnl),
            "px25_start_prem_hwm_pct": float(hwm),
        },
        log_event="PX25_HANDOFF",
    )


def evaluate_px25_extension_tick(
    *,
    osnap: Dict[str, Any],
    option_side: str,
    current_underlying: float,
    current_pnl_pct: float,
    now_ts: Any = None,
    cfg: Px25Config = DEFAULT_PX25_CONFIG,
) -> Px25Decision:
    if not cfg.enabled or not px25_active(osnap):
        return Px25Decision(action="continue", detail=PX25_DETAIL_CONTINUE)

    reason = str(osnap.get("px25_original_reason") or "premium_trail")
    und_hwm = update_price_hwm(
        option_side=option_side,
        hwm=osnap.get("px25_und_hwm_price"),
        current_underlying=current_underlying,
    )
    updates: Dict[str, Any] = {"px25_active": True, "px25_und_hwm_price": und_hwm}

    try:
        pnl = float(current_pnl_pct or 0.0)
    except (TypeError, ValueError):
        pnl = 0.0
    try:
        floor = float(osnap.get("px25_prem_floor_pct") or cfg.prem_floor_pct)
    except (TypeError, ValueError):
        floor = float(cfg.prem_floor_pct)
    if pnl <= floor + 1e-12:
        updates["px25_active"] = False
        updates["px25_last_detail"] = PX25_DETAIL_PREM_FLOOR
        return Px25Decision(
            action="exit",
            detail=PX25_DETAIL_PREM_FLOOR,
            reason=reason,
            state_updates=updates,
            log_event="PX25_PREM_FLOOR",
        )

    broken = px3_und_trail_broken(
        option_side=option_side,
        und_hwm_price=und_hwm,
        current_underlying=current_underlying,
        trail_pct=float(osnap.get("px25_price_trail_pct") or cfg.price_trail_pct),
    )
    if broken is True:
        updates["px25_active"] = False
        updates["px25_last_detail"] = PX25_DETAIL_PRICE_TRAIL
        return Px25Decision(
            action="exit",
            detail=PX25_DETAIL_PRICE_TRAIL,
            reason=reason,
            state_updates=updates,
            log_event="PX25_PRICE_TRAIL",
        )

    held = px25_elapsed_seconds(osnap.get("px25_handoff_ts"), now_ts)
    if held is not None and float(cfg.max_extension_seconds or 0.0) > 0:
        if held + 1e-12 >= float(cfg.max_extension_seconds):
            updates["px25_active"] = False
            updates["px25_last_detail"] = PX25_DETAIL_TIME
            return Px25Decision(
                action="exit",
                detail=PX25_DETAIL_TIME,
                reason=reason,
                state_updates=updates,
                log_event="PX25_MAX_EXTENSION",
            )

    updates["px25_last_detail"] = PX25_DETAIL_CONTINUE
    return Px25Decision(
        action="continue",
        detail=PX25_DETAIL_CONTINUE,
        reason=reason,
        state_updates=updates,
    )


def apply_px25_state_updates(osnap: Dict[str, Any], updates: Optional[Dict[str, Any]]) -> None:
    if not updates or not isinstance(osnap, dict):
        return
    for k, v in updates.items():
        osnap[k] = v


def px25_config_from_engine(config: Any) -> Px25Config:
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

    return Px25Config(
        enabled=_b("dte_px25_enabled", False),
        min_prem_hwm_pct=_f("dte_px25_min_prem_hwm_pct", 1.50),
        exclude_ultra_clean_pnl_pct=_f("dte_px25_exclude_ultra_clean_pnl_pct", 7.50),
        exclude_ultra_clean_capture_ratio=_f("dte_px25_exclude_ultra_clean_capture_ratio", 0.85),
        price_trail_pct=_f("dte_px25_price_trail_pct", 0.01),
        max_extension_seconds=_f("dte_px25_max_extension_seconds", 900.0),
        prem_floor_pct=_f("dte_px25_prem_floor_pct", 0.00),
        mfe_thin_und_bank_enabled=_b("dte_px25_mfe_thin_und_bank_enabled", True),
        mfe_thin_und_fav_hwm_pct=_f("dte_px25_mfe_thin_und_fav_hwm_pct", 0.003),
        mfe_thin_und_min_prem_hwm_pct=_f("dte_px25_mfe_thin_und_min_prem_hwm_pct", 1.50),
    )
