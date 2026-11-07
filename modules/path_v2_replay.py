"""PATH_V2 replay harness: PT clock and live-exit parity scoring.

Use this instead of (hour - 7). Pacific Daylight vs Pacific Standard is
America/Los_Angeles. After 11/1 the UTC−7 shortcut fires MFE trail and EOD
an hour early.

Replay clock: prefer eval_now / eval_age_s (the exact now passed to
evaluate_path_v2_tick). Historical rows without eval_now keep timestamp.

Replay inputs match the live evaluate_path_v2_tick call: theta,
valid_bid_ask_contracts, option bid/ask, session-tech overlay, and
unavailable UL when the row has no UL fields. MFE-trail knobs come from
cfg (OPTION_SINGLE_MFE_TRAIL_*). Session hours come from session_clock.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from modules.path_v2_exit import (
    PathV2Config,
    PathV2Decision,
    evaluate_path_v2_tick,
    pacific_clock_hour,
)

SHADOW_CONTINUATION = "SHADOW_CONTINUATION"


def mfe_trail_pt_hour() -> float:
    from modules.session_clock import orb2_tl_end_hour

    return float(orb2_tl_end_hour())


def eod_flatten_pt_hour() -> float:
    from modules.session_clock import eod_flatten_start_hour

    return float(eod_flatten_start_hour())


def __getattr__(name: str) -> float:
    if name == "MFE_TRAIL_PT_HOUR":
        return mfe_trail_pt_hour()
    if name == "EOD_PT_HOUR":
        return eod_flatten_pt_hour()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _parse_replay_dt(raw: Any) -> Optional[datetime]:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        if raw.tzinfo is None:
            return raw.replace(tzinfo=timezone.utc)
        return raw
    text = str(raw).strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def prep_replay_ticks(
    rows: List[Dict[str, Any]],
    *,
    opened_at: Optional[datetime] = None,
    entry_premium: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Build replay ticks from saved JSONL / recorder rows.

    Historical rows without eval_now keep timestamp as now (accepted book
    unchanged). When any row is evaluated=true, only those drive state.
    Order is parsed eval_now (or timestamp / now) epoch, then monitor_tick_id.
    Missing UL fields stay unavailable (never defaulted to entry or now).
    ``entry_premium`` joins the trade/book record onto rows that omitted it.
    It does not invent a value or write back onto historical jsonl.
    """
    raw = [r for r in (rows or []) if isinstance(r, dict)]
    if any(r.get("evaluated") is True for r in raw):
        raw = [r for r in raw if r.get("evaluated") is True]

    def _sort_key(r: Dict[str, Any]) -> tuple:
        now = _parse_replay_dt(r.get("eval_now") or r.get("timestamp") or r.get("now"))
        tick = str(r.get("monitor_tick_id") or "")
        epoch = now.timestamp() if now is not None else float("inf")
        return (epoch, tick)

    ticks: List[Dict[str, Any]] = []
    for r in sorted(raw, key=_sort_key):
        now = _parse_replay_dt(r.get("eval_now") or r.get("timestamp") or r.get("now"))
        if now is None:
            continue
        raw_bid = r.get("bid") if r.get("bid") is not None else r.get("option_bid")
        entry = r.get("entry_premium")
        if entry in (None, "", 0, 0.0) and entry_premium not in (None, "", 0, 0.0):
            entry = entry_premium
        from modules.path_v2_exit import _positive, live_path_v2_pret_from_tick

        # Same as live: this-tick executable bid only. Never mark / pnl_pct.
        pret = live_path_v2_pret_from_tick(
            option_bid=raw_bid,
            entry_premium=entry,
            mark=r.get("mark"),
            current_pnl_pct=r.get("pnl_pct") if r.get("pnl_pct") is not None else r.get("path_pret"),
        )
        option_bid = _positive(raw_bid)
        raw_ask = r.get("ask") if r.get("ask") is not None else r.get("option_ask")
        option_ask = _positive(raw_ask)
        ul = r.get("underlying_price")
        if ul is None:
            ul = r.get("underlying_last")
        if ul is None:
            ul = r.get("current_underlying")
        ul_ts = r.get("underlying_observation_timestamp")
        if ul_ts is None:
            ul_ts = r.get("ul_quote_ts")
        ul_age = r.get("underlying_age_ms")
        if ul_age is None:
            ul_age = r.get("ul_age_ms")
        ul_source = r.get("underlying_source")
        if ul_source is None:
            ul_source = r.get("ul_source")
        if ul is None and ul_ts is None and ul_age is None and not ul_source:
            ul_source = "unavailable"
        ticks.append(
            {
                "now": now,
                "eval_now": now,
                "eval_age_s": r.get("eval_age_s"),
                "monitor_tick_id": r.get("monitor_tick_id"),
                "pret": None if pret is None else float(pret),
                "option_bid": option_bid,
                "option_ask": option_ask,
                "current_underlying": ul,
                "ul_quote_ts": ul_ts,
                "ul_age_ms": ul_age,
                "ul_source": ul_source,
                "theta": r.get("theta"),
                "valid_bid_ask_contracts": r.get("valid_bid_ask_contracts"),
                "atr": r.get("atr"),
                "vwap_distance_pct": r.get("vwap_distance_pct"),
                "session_tech_bar_at": r.get("session_tech_bar_at"),
                "option_side": r.get("option_side"),
                "entry_underlying": r.get("entry_underlying"),
                "opened_at": opened_at,
                "evaluated": r.get("evaluated"),
            }
        )
    return ticks


def mfe_trail_active_at(now: Any) -> bool:
    hour = pacific_clock_hour(now)
    return hour is not None and hour >= mfe_trail_pt_hour() - 1e-12


def eod_flatten_due_at(now: Any) -> bool:
    hour = pacific_clock_hour(now)
    return hour is not None and hour >= eod_flatten_pt_hour() - 1e-12


@dataclass
class ReplayExit:
    reason: Optional[str]
    detail: str
    at: datetime
    exit_px: Optional[float]
    shadow_continuation: bool = False


def replay_ticks(
    ticks: List[Dict[str, Any]],
    *,
    cfg: Optional[PathV2Config] = None,
    osnap: Optional[Dict[str, Any]] = None,
    option_side: str = "CALL",
    entry_underlying: float = 100.0,
    opened_at: Optional[datetime] = None,
    live_exit_at: Optional[datetime] = None,
    stop_at_live_exit: bool = False,
) -> ReplayExit:
    """Walk ticks through evaluate_path_v2_tick.

    stop_at_live_exit: do not evaluate ticks after live_exit_at.
    A first engine exit after live_exit_at is tagged SHADOW_CONTINUATION.
    Live-parity: overlay session tech (keep last), pass theta / bid-ask /
    contract count, leave MFE-trail knobs to cfg, treat missing UL as unavailable.
    """
    state_osnap: Dict[str, Any] = osnap if isinstance(osnap, dict) else {}
    first_now = ticks[0]["now"] if ticks else opened_at
    opened = opened_at or first_now
    last_px = None
    last_now = opened
    last_tech: Dict[str, Any] = {}
    try:
        from profit_optimizer.path_chain_skew import overlay_live_session_tech
    except Exception:
        overlay_live_session_tech = None
    for tick in ticks:
        now = tick.get("eval_now") or tick["now"]
        last_now = now
        last_px = tick.get("option_bid", tick.get("exit_px"))
        if stop_at_live_exit and live_exit_at is not None and now > live_exit_at:
            return ReplayExit(
                reason=SHADOW_CONTINUATION,
                detail=SHADOW_CONTINUATION,
                at=now,
                exit_px=last_px,
                shadow_continuation=True,
            )
        if eod_flatten_due_at(now):
            return ReplayExit(reason="end_of_day_exit", detail="EOD", at=now, exit_px=last_px)
        tech = {
            key: tick[key]
            for key in ("atr", "vwap_distance_pct", "session_tech_bar_at")
            if tick.get(key) is not None
        }
        if tech:
            last_tech.update(tech)
        if last_tech:
            if overlay_live_session_tech is not None:
                overlay_live_session_tech(state_osnap, last_tech)
            else:
                state_osnap.update(last_tech)
        ul = tick.get("current_underlying")
        ul_source = tick.get("ul_source")
        ul_ts = tick.get("ul_quote_ts")
        ul_age = tick.get("ul_age_ms")
        if ul is None and ul_ts is None and ul_age is None and not ul_source:
            ul_source = "unavailable"
        decision: PathV2Decision = evaluate_path_v2_tick(
            osnap=state_osnap,
            option_side=tick.get("option_side") or option_side,
            entry_underlying=tick.get("entry_underlying", entry_underlying),
            current_underlying=ul,
            pret=tick["pret"],
            opened_at=opened,
            now=now,
            ul_quote_ts=ul_ts,
            ul_age_ms=ul_age,
            ul_source=ul_source,
            mfe_trail_active=mfe_trail_active_at(now),
            mfe_trail_trigger_pct=None,
            mfe_trail_exit_pct=None,
            theta=tick.get("theta"),
            option_bid=tick.get("option_bid"),
            option_ask=tick.get("option_ask"),
            valid_bid_ask_contracts=tick.get("valid_bid_ask_contracts"),
            cfg=cfg,
        )
        if decision.exit_reason:
            after_live = live_exit_at is not None and now > live_exit_at
            return ReplayExit(
                reason=decision.exit_reason,
                detail=decision.detail,
                at=now,
                exit_px=last_px,
                shadow_continuation=after_live,
            )
    return ReplayExit(reason=None, detail="PATH_END", at=last_now, exit_px=last_px)
