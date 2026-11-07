#!/usr/bin/env python3
"""
True ORB-range breakout confirmation (not spike / retest).

CALL: first print ≥ min-break beyond ORB high → READY → QG.
PUT:  first print ≥ min-break beyond ORB low → READY → QG.
Continuation / follow-through / chop are deleted as admit knives (TL parity).
Immediate reclaim back into the ORB range invalidates the attempt.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple

from .breakout_models import BreakoutCandidate, BreakoutCandidateState, BreakoutConfig


@dataclass
class BreakoutConfirmResult:
    ready: bool
    option_side: Optional[str]
    break_distance_pct: float
    continuation_pct: float
    follow_through_bars: int
    post_break_range_pct: Optional[float]
    failed_reasons: List[str]
    state: BreakoutCandidateState
    immediate_reclaim: bool = False


def _break_levels(cand: BreakoutCandidate, cfg: BreakoutConfig) -> Tuple[float, float]:
    """Return (call_trigger, put_trigger) absolute prices."""
    oh = float(cand.orb_high)
    ol = float(cand.orb_low)
    call_trig = oh * (1.0 + float(cfg.min_break_distance_pct))
    put_trig = ol * (1.0 - float(cfg.min_break_distance_pct))
    return call_trig, put_trig


def break_distance_pct(price: float, cand: BreakoutCandidate, option_side: str) -> float:
    side = str(option_side or "").lower()
    if side == "call":
        oh = float(cand.orb_high)
        if oh <= 0:
            return 0.0
        return max(0.0, (float(price) - oh) / oh)
    ol = float(cand.orb_low)
    if ol <= 0:
        return 0.0
    return max(0.0, (ol - float(price)) / ol)


def price_inside_orb(price: float, cand: BreakoutCandidate) -> bool:
    return float(cand.orb_low) < float(price) < float(cand.orb_high)


def detect_break_side(
    price: float, cand: BreakoutCandidate, cfg: BreakoutConfig
) -> Optional[str]:
    call_trig, put_trig = _break_levels(cand, cfg)
    if float(price) >= call_trig:
        return "call"
    if float(price) <= put_trig:
        return "put"
    return None


def compute_continuation_pct(cand: BreakoutCandidate, price: float) -> float:
    mat = cand.material_break_close
    if mat is None or float(mat) <= 0:
        return 0.0
    side = str(cand.option_side or "").lower()
    if side == "call":
        return (float(price) - float(mat)) / float(mat)
    if side == "put":
        return (float(mat) - float(price)) / float(mat)
    return 0.0


def compute_post_break_range_pct(cand: BreakoutCandidate) -> Optional[float]:
    mat = cand.material_break_close
    hi = cand.post_break_high
    lo = cand.post_break_low
    if mat is None or hi is None or lo is None:
        return None
    try:
        ref = float(mat)
        if ref <= 0:
            return None
        return (float(hi) - float(lo)) / ref
    except (TypeError, ValueError):
        return None


def process_breakout_tick(
    cand: BreakoutCandidate,
    price: float,
    ts: datetime,
    cfg: BreakoutConfig,
) -> BreakoutConfirmResult:
    """
    Advance candidate state on a new quote/price tick.

    Mutates ``cand`` in place. Returns confirmation status for the caller.
    """
    failed: List[str] = []
    price = float(price)
    cand.last_price = price
    cand.last_quote_ts = ts

    if cand.state in (
        BreakoutCandidateState.EXECUTED,
        BreakoutCandidateState.INVALIDATED,
        BreakoutCandidateState.EXPIRED,
    ):
        return BreakoutConfirmResult(
            ready=False,
            option_side=cand.option_side,
            break_distance_pct=0.0,
            continuation_pct=0.0,
            follow_through_bars=int(cand.follow_through_bars or 0),
            post_break_range_pct=compute_post_break_range_pct(cand),
            failed_reasons=["terminal_state"],
            state=cand.state,
        )

    # First material break
    if cand.state == BreakoutCandidateState.WAITING_FOR_BREAK:
        side = detect_break_side(price, cand, cfg)
        if side is None:
            if price_inside_orb(price, cand):
                meta = cand.metadata if isinstance(getattr(cand, "metadata", None), dict) else {}
                if meta.get("reentry_required_sides"):
                    meta["reentry_required_sides"] = []
                    cand.metadata = meta
            return BreakoutConfirmResult(
                ready=False,
                option_side=None,
                break_distance_pct=0.0,
                continuation_pct=0.0,
                follow_through_bars=0,
                post_break_range_pct=None,
                failed_reasons=[],
                state=cand.state,
            )
        # Structural post-executor reject cools this side for a bounded window.
        meta = cand.metadata if isinstance(getattr(cand, "metadata", None), dict) else {}
        blocked_until = None
        until_map = meta.get("terminal_reject_until_by_side") or {}
        raw_until = until_map.get(side) if isinstance(until_map, dict) else None
        if raw_until:
            try:
                text = str(raw_until).strip()
                if text.endswith("Z"):
                    text = text[:-1] + "+00:00"
                blocked_until = datetime.fromisoformat(text)
            except Exception:
                blocked_until = None
        if blocked_until and blocked_until.tzinfo is None:
            blocked_until = blocked_until.replace(tzinfo=ts.tzinfo)
        if blocked_until and blocked_until > ts:
            return BreakoutConfirmResult(
                ready=False,
                option_side=None,
                break_distance_pct=0.0,
                continuation_pct=0.0,
                follow_through_bars=0,
                post_break_range_pct=None,
                failed_reasons=[f"side_cooldown_rejected:{side}"],
                state=cand.state,
            )
        if blocked_until and blocked_until <= ts:
            until_map.pop(side, None)
            meta["terminal_reject_until_by_side"] = until_map
            meta["terminal_reject_sides"] = [
                s for s in (meta.get("terminal_reject_sides") or []) if str(s).strip().lower() != side
            ]
            cand.metadata = meta
        reentry_required = {
            str(s).strip().lower() for s in (meta.get("reentry_required_sides") or [])
        }
        if side in reentry_required:
            return BreakoutConfirmResult(
                ready=False,
                option_side=None,
                break_distance_pct=0.0,
                continuation_pct=0.0,
                follow_through_bars=0,
                post_break_range_pct=None,
                failed_reasons=[f"side_reentry_required:{side}"],
                state=cand.state,
            )
        cand.option_side = side
        cand.break_price = price
        cand.break_ts = ts
        cand.material_break_close = price
        cand.post_break_high = price
        cand.post_break_low = price
        cand.follow_through_bars = 1
        # Soft entry window anchor (mirror TL entry_window_at) — expires CONFIRM/READY.
        meta = cand.metadata if isinstance(cand.metadata, dict) else {}
        meta["entry_window_at"] = ts
        cand.metadata = meta
        dist = break_distance_pct(price, cand, side)
        cand.state = BreakoutCandidateState.READY_TO_EXECUTE
        return BreakoutConfirmResult(
            ready=True,
            option_side=side,
            break_distance_pct=dist,
            continuation_pct=0.0,
            follow_through_bars=1,
            post_break_range_pct=0.0,
            failed_reasons=[],
            state=cand.state,
        )

    # Confirmation phase
    side = str(cand.option_side or "").lower()
    if not side:
        cand.state = BreakoutCandidateState.WAITING_FOR_BREAK
        return BreakoutConfirmResult(
            ready=False,
            option_side=None,
            break_distance_pct=0.0,
            continuation_pct=0.0,
            follow_through_bars=0,
            post_break_range_pct=None,
            failed_reasons=["missing_option_side"],
            state=cand.state,
        )

    # Spike / retest: reclaim back inside ORB range → invalidate this attempt and re-arm
    if bool(cfg.reclaim_invalidates) and price_inside_orb(price, cand):
        cand.state = BreakoutCandidateState.WAITING_FOR_BREAK
        cand.option_side = None
        cand.break_price = None
        cand.break_ts = None
        cand.material_break_close = None
        cand.post_break_high = None
        cand.post_break_low = None
        cand.follow_through_bars = 0
        cand.failed_reasons = ["immediate_reclaim_into_orb"]
        meta = cand.metadata if isinstance(cand.metadata, dict) else {}
        meta.pop("entry_window_at", None)
        meta.pop("exec_ready_at", None)
        cand.metadata = meta
        return BreakoutConfirmResult(
            ready=False,
            option_side=None,
            break_distance_pct=0.0,
            continuation_pct=0.0,
            follow_through_bars=0,
            post_break_range_pct=None,
            failed_reasons=["immediate_reclaim_into_orb"],
            state=cand.state,
            immediate_reclaim=True,
        )

    # Still on break side?
    call_trig, put_trig = _break_levels(cand, cfg)
    on_side = (side == "call" and price >= call_trig) or (side == "put" and price <= put_trig)
    if on_side:
        cand.follow_through_bars = int(cand.follow_through_bars or 0) + 1
    else:
        # Hover outside ORB but not past material trigger — count as weak / reset FT
        cand.follow_through_bars = 0
        failed.append("follow_through_reset_not_beyond_trigger")

    if cand.post_break_high is None:
        cand.post_break_high = price
    else:
        cand.post_break_high = max(float(cand.post_break_high), price)
    if cand.post_break_low is None:
        cand.post_break_low = price
    else:
        cand.post_break_low = min(float(cand.post_break_low), price)

    dist = break_distance_pct(price, cand, side)
    cont = compute_continuation_pct(cand, price)
    pb_range = compute_post_break_range_pct(cand)
    ft = int(cand.follow_through_bars or 0)

    if dist < float(cfg.min_break_distance_pct):
        failed.append(
            f"break_distance_below_min:{dist:.6f}<{float(cfg.min_break_distance_pct):.6f}"
        )

    cand.failed_reasons = list(failed)
    ready = not failed
    if ready:
        cand.state = BreakoutCandidateState.READY_TO_EXECUTE
        # exec_ready_at is stamped in breakout_watch only after quality admit
            # (session-tech soft-defer must not burn the exec-ready TTL).
        meta = cand.metadata if isinstance(cand.metadata, dict) else {}
        cand.metadata = meta
    return BreakoutConfirmResult(
        ready=ready,
        option_side=side,
        break_distance_pct=dist,
        continuation_pct=cont,
        follow_through_bars=ft,
        post_break_range_pct=pb_range,
        failed_reasons=failed,
        state=cand.state,
    )


def should_soft_expire_entry_window(
    *,
    age_sec: Optional[float],
    window_sec: float,
    confirm_ready: bool,
) -> bool:
    """True → rearm WAIT_BREAK (no cooldown). False → keep the candidate.

    CONFIRM past ``window_sec`` expires and retries. A tick that is already
    READY must not expire — fall through to Quality Gate. QG reject uses the
    1h same-side cooldown (``BREAKOUT_SIDE_REJECT_COOLDOWN_SECONDS``).
    """
    try:
        window = float(window_sec or 0.0)
    except (TypeError, ValueError):
        window = 0.0
    if window <= 0 or age_sec is None:
        return False
    try:
        age = float(age_sec)
    except (TypeError, ValueError):
        return False
    if age <= window:
        return False
    if bool(confirm_ready):
        return False
    return True
