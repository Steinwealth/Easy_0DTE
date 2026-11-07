"""Phase 2 Hold Exit — defer base MFE giveback when same-side dir% clears floor.

Canonical policy (Jul 28, 2026 desk lock — Hold@0.55 reclaim→re-trail):
  Baseline remains +10% arm / −10pp ``mfe_giveback``.
  At the first −10pp fire:
    1. If same-side session dir% ≥ floor (default 0.55) and quote trusted → enter Hold.
    2. Else → take normal ``mfe_giveback`` (fail-closed when dir% missing).
  While Hold:
    3. Abort if premium PnL ≤ hard floor (default −5%).
    4. Reclaim prior option HWM within reclaim window (default 20m; Shared.env); else exit reclaim_fail.
    5. After reclaim, re-trail new HWM with −10pp giveback (same math as baseline).
    6. Hard time cap from Hold start (default 45 minutes).

Jul 29: on Portfolio Protection bearish / Red Day / Protection Day triggers
(``red_day_pattern_1|2|3``, ``protection_day*``, ``enhanced_red_day_skip_execution``),
**CALL Hold defer is disabled** — take baseline ``mfe_giveback``. PUT Hold still allowed.
Observe-only PP days still publish the trigger for this gate.

Profit Optimizer remains observe-only. This module is the live decision helper.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple


HOLD_DETAIL_ENTERED = "hold_entered"
HOLD_DETAIL_FLOOR = "hold_floor_break"
HOLD_DETAIL_RECLAIM_FAIL = "hold_reclaim_fail"
HOLD_DETAIL_RETRAIL = "hold_retrail_giveback"
HOLD_DETAIL_MAX = "hold_max"
HOLD_DETAIL_CONTINUE = "hold_continue"
HOLD_DETAIL_DIR_REJECT = "hold_dir_reject"


@dataclass(frozen=True)
class HoldExitDecision:
    """Result of one Hold evaluation tick."""

    action: str  # "continue" | "exit"
    detail: str
    reason: str = "hold_exit"  # canonical exit id when action=exit
    state_updates: Optional[Dict[str, Any]] = None
    log_event: str = ""


def hold_exit_eligible(
    *,
    enabled: bool,
    is_0dte_single: bool,
    is_spread: bool,
) -> bool:
    return bool(enabled) and bool(is_0dte_single) and (not bool(is_spread))


def hold_trigger_giveback_pct(cfg: Any, base_exit_pct: float) -> float:
    """Giveback pp that *starts* Hold.

    Hold@0.55 locks this to the base mfe giveback (default 0.10) so Hold defers
    the normal fire rather than waiting for a wider −20pp band.
    """
    raw = getattr(cfg, "hold_exit_trigger_giveback_pct", None)
    try:
        v = float(raw) if raw is not None else float(base_exit_pct or 0.10)
    except (TypeError, ValueError):
        v = float(base_exit_pct or 0.10)
    if v <= 0:
        return float(base_exit_pct or 0.10)
    return v


def _safe_float(v: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if v is None:
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _dig_chain_fields(*blobs: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    keys = (
        "direction_volume_pct",
        "call_chain_volume",
        "put_chain_volume",
        "option_side",
        "option_type",
        "expected_option_side",
        "side",
    )
    nest_keys = (
        "signal_snapshot",
        "execution_payload",
        "entry_signal",
        "normalized_options",
        "engine",
        "option_stealth",
    )

    def walk(obj: Any, depth: int = 0) -> None:
        if depth > 3 or not isinstance(obj, Mapping):
            return
        for k in keys:
            if k not in out and obj.get(k) is not None:
                out[k] = obj.get(k)
        for nk in nest_keys:
            child = obj.get(nk)
            if isinstance(child, Mapping):
                walk(child, depth + 1)

    for b in blobs:
        walk(b)
    return out


def resolve_same_side_dir_pct(
    *,
    osnap: Optional[Mapping[str, Any]] = None,
    position_metadata: Optional[Mapping[str, Any]] = None,
    option_side: str = "",
) -> Tuple[Optional[float], str]:
    """Return (same-side dir%, source_note). None → fail-closed for Hold entry."""
    fields = _dig_chain_fields(osnap or {}, position_metadata or {})
    direct = _safe_float(fields.get("direction_volume_pct"))
    if direct is not None:
        return max(0.0, min(1.0, direct)), "direction_volume_pct"

    cv = _safe_float(fields.get("call_chain_volume"))
    pv = _safe_float(fields.get("put_chain_volume"))
    if cv is None or pv is None or (cv + pv) <= 0:
        return None, "missing_chain_volume"

    side = str(
        option_side
        or fields.get("expected_option_side")
        or fields.get("option_type")
        or fields.get("option_side")
        or fields.get("side")
        or ""
    ).strip().lower()
    is_put = side in {"put", "p", "short"} or side.endswith("put")
    is_call = side in {"call", "c", "long"} or side.endswith("call")
    if not is_put and not is_call:
        return None, "missing_option_side"
    same = (pv / (cv + pv)) if is_put else (cv / (cv + pv))
    return float(same), "derived_call_put_volume"


def hold_dir_volume_allows_defer(
    *,
    dir_pct: Optional[float],
    min_pct: float = 0.55,
    require_dir: bool = True,
) -> Tuple[bool, str]:
    """Fail-closed: missing dir% rejects Hold when require_dir is True."""
    floor = float(min_pct)
    if dir_pct is None:
        if require_dir:
            return False, "dir_missing_fail_closed"
        return True, "dir_optional_missing_allow"
    if float(dir_pct) + 1e-12 >= floor:
        return True, f"dir_pass:{float(dir_pct):.4f}>={floor:.4f}"
    return False, f"dir_reject:{float(dir_pct):.4f}<{floor:.4f}"


def hold_pp_allows_call_defer(
    *,
    option_side: str = "",
    trigger_id: Optional[str] = None,
) -> Tuple[bool, str]:
    """False → refuse Hold on CALL during bearish PP Red Day / Protection Day."""
    try:
        from modules.portfolio_protection import pp_regime_disables_call_hold
    except Exception:
        try:
            from portfolio_protection import pp_regime_disables_call_hold  # type: ignore
        except Exception:
            return True, "pp_gate_unavailable"
    disabled, note = pp_regime_disables_call_hold(
        option_side=option_side,
        trigger_id=trigger_id,
    )
    if disabled:
        return False, note
    return True, note


def start_hold_state(
    *,
    now_iso: str,
    trigger_bid: float,
    trigger_hwm: float,
    trigger_hwm_pnl: float,
    entry_premium: float,
    dir_pct: Optional[float] = None,
    dir_source: str = "",
) -> Dict[str, Any]:
    return {
        "hold_exit_active": True,
        "hold_exit_phase": "holding",  # holding | trailing
        "hold_exit_started_iso": str(now_iso or ""),
        "hold_exit_trigger_bid": float(trigger_bid),
        "hold_exit_trigger_hwm": float(trigger_hwm),
        "hold_exit_trigger_hwm_pnl": float(trigger_hwm_pnl),
        "hold_exit_entry_premium": float(entry_premium),
        "hold_exit_reclaimed": False,
        "hold_exit_post_hwm": float(trigger_bid),
        "hold_exit_reclaim_at_iso": "",
        "hold_exit_last_detail": HOLD_DETAIL_ENTERED,
        "hold_exit_dir_pct": (float(dir_pct) if dir_pct is not None else None),
        "hold_exit_dir_source": str(dir_source or ""),
    }


def clear_hold_state(osnap: Dict[str, Any]) -> None:
    for k in (
        "hold_exit_active",
        "hold_exit_phase",
        "hold_exit_started_iso",
        "hold_exit_trigger_bid",
        "hold_exit_trigger_hwm",
        "hold_exit_trigger_hwm_pnl",
        "hold_exit_entry_premium",
        "hold_exit_reclaimed",
        "hold_exit_post_hwm",
        "hold_exit_reclaim_at_iso",
        "hold_exit_last_detail",
        "hold_exit_dir_pct",
        "hold_exit_dir_source",
        "hold_exit_suppress_mfe_latch",
    ):
        osnap.pop(k, None)


def evaluate_hold_tick(
    *,
    osnap: Dict[str, Any],
    now_iso: str,
    elapsed_minutes: float,
    current_bid: float,
    current_pnl_pct: float,
    reclaim_minutes: float = 20.0,
    hold_max_minutes: float = 45.0,
    hard_floor_pnl_pct: float = -0.05,
    re_trail_giveback_pct: float = 0.10,
    require_trigger_bid_floor: bool = False,
    premium_jump_down: bool = False,
) -> HoldExitDecision:
    """Advance Hold state for one trusted monitor tick.

    Caller must only invoke when ``hold_exit_active`` and quote is protection-ok.

    After ``reclaim_minutes`` without reclaiming trigger HWM → exit reclaim_fail
    (no soft extend). Soft trigger-bid floor is unused in the locked policy but
    kept as a no-op parameter for config compatibility.
    """
    _ = require_trigger_bid_floor  # locked policy: hard reclaim timeout only
    trigger_hwm = _safe_float(osnap.get("hold_exit_trigger_hwm"), 0.0) or 0.0
    post_hwm = _safe_float(osnap.get("hold_exit_post_hwm"), trigger_hwm) or float(trigger_hwm)
    reclaimed = bool(osnap.get("hold_exit_reclaimed"))
    phase = str(osnap.get("hold_exit_phase") or "holding")
    bid = float(current_bid)
    pnl = float(current_pnl_pct)
    elapsed = float(elapsed_minutes)
    updates: Dict[str, Any] = {"hold_exit_last_detail": HOLD_DETAIL_CONTINUE}

    # 1) Hard floor — fail closed on collapse
    if pnl <= float(hard_floor_pnl_pct) + 1e-12:
        updates["hold_exit_last_detail"] = HOLD_DETAIL_FLOOR
        return HoldExitDecision(
            action="exit",
            detail=HOLD_DETAIL_FLOOR,
            reason="hold_exit",
            state_updates=updates,
            log_event="HOLD_EXIT_FLOOR_BREAK",
        )

    # 2) Hard time cap
    if elapsed + 1e-12 >= float(hold_max_minutes):
        updates["hold_exit_last_detail"] = HOLD_DETAIL_MAX
        return HoldExitDecision(
            action="exit",
            detail=HOLD_DETAIL_MAX,
            reason="hold_exit",
            state_updates=updates,
            log_event="HOLD_EXIT_MAX",
        )

    if not reclaimed or phase == "holding":
        # Reclaim prior HWM → switch to trailing
        if bid + 1e-12 >= trigger_hwm and trigger_hwm > 0:
            updates.update(
                {
                    "hold_exit_reclaimed": True,
                    "hold_exit_phase": "trailing",
                    "hold_exit_post_hwm": max(bid, trigger_hwm),
                    "hold_exit_reclaim_at_iso": str(now_iso or ""),
                    "hold_exit_last_detail": HOLD_DETAIL_CONTINUE,
                }
            )
            return HoldExitDecision(
                action="continue",
                detail=HOLD_DETAIL_CONTINUE,
                state_updates=updates,
                log_event="HOLD_EXIT_RECLAIMED",
            )

        # Reclaim window elapsed without reclaim → exit (AMAT-class protector)
        if elapsed + 1e-12 >= float(reclaim_minutes):
            updates["hold_exit_last_detail"] = HOLD_DETAIL_RECLAIM_FAIL
            return HoldExitDecision(
                action="exit",
                detail=HOLD_DETAIL_RECLAIM_FAIL,
                reason="hold_exit",
                state_updates=updates,
                log_event="HOLD_EXIT_RECLAIM_FAIL",
            )

        updates["hold_exit_last_detail"] = HOLD_DETAIL_CONTINUE
        return HoldExitDecision(
            action="continue",
            detail=HOLD_DETAIL_CONTINUE,
            state_updates=updates,
            log_event="",
        )

    # Trailing after reclaim — re-arm baseline giveback from new HWM
    if not premium_jump_down and bid > post_hwm:
        post_hwm = bid
        updates["hold_exit_post_hwm"] = float(post_hwm)

    if post_hwm > 0 and not premium_jump_down:
        entry = _safe_float(osnap.get("hold_exit_entry_premium"), 0.0) or 0.0
        if entry > 0:
            post_pnl = (post_hwm - entry) / entry
            dd = post_pnl - pnl
        else:
            dd = (post_hwm - bid) / post_hwm
        if dd + 1e-12 >= float(re_trail_giveback_pct):
            updates["hold_exit_last_detail"] = HOLD_DETAIL_RETRAIL
            updates["hold_exit_post_hwm"] = float(post_hwm)
            return HoldExitDecision(
                action="exit",
                detail=HOLD_DETAIL_RETRAIL,
                reason="hold_exit",
                state_updates=updates,
                log_event="HOLD_EXIT_RETRAIL",
            )

    updates.setdefault("hold_exit_post_hwm", float(post_hwm))
    updates["hold_exit_last_detail"] = HOLD_DETAIL_CONTINUE
    return HoldExitDecision(
        action="continue",
        detail=HOLD_DETAIL_CONTINUE,
        state_updates=updates,
        log_event="",
    )


def apply_hold_state_updates(osnap: Dict[str, Any], updates: Optional[Dict[str, Any]]) -> None:
    if not updates:
        return
    for k, v in updates.items():
        osnap[k] = v
