"""Fair owned-position evaluation scheduler (ORB + Trendline).

Priority (locked cadence contract):
  P0 pending enrollment / pending fill / exit-submitted
  P1 trail-armed / PX3 / exit-critical protection
  P2 oldest seconds_since_last_successful_eval
  P3 ordinary

Within a priority, rotate fairly across ORB and Trendline. Strategy type must
not permanently determine order.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from modules.unified_0dte_monitor_registry import collect_armed_trail_priority_ids

log = logging.getLogger(__name__)

LANE_ORB = "orb"
LANE_TRENDLINE = "trendline"

LAST_EVAL_ATTR = "_0dte_last_successful_eval_ts"


@dataclass
class EvalSlot:
    position: Any
    trade_id: str
    symbol: str
    lane: str
    priority: int
    seconds_since_last_eval: float
    pending: bool = False
    trail_armed: bool = False
    exit_submitted: bool = False


def _lane_for(pos: Any) -> str:
    try:
        from modules.unified_0dte_account import _position_strategy_type

        st = str(_position_strategy_type(pos) or "").lower()
    except Exception:
        st = str(getattr(pos, "strategy_type", "") or "").lower()
        meta = getattr(pos, "metadata", None)
        if isinstance(meta, dict) and not st:
            st = str(meta.get("strategy_type") or "").lower()
    if "trendline" in st:
        return LANE_TRENDLINE
    return LANE_ORB


def _position_still_open(system: Any, trade_id: str, pos: Any = None) -> bool:
    """False after EOD flatten / close so a yielded tick does not evaluate twice."""
    tid = str(trade_id or "").strip()
    if not tid:
        return False
    if pos is not None:
        status = str(getattr(pos, "status", "") or "").lower()
        if status in ("closed", "closing", "flat"):
            return False
        try:
            qty = getattr(pos, "quantity", None)
            if qty is not None and float(qty) <= 0:
                return False
        except (TypeError, ValueError):
            pass
        md = getattr(pos, "metadata", None)
        if isinstance(md, dict):
            if md.get("closed") or md.get("eod_flattened") or md.get("flatten_submitted"):
                return False
            st = str(md.get("lifecycle_state") or md.get("state") or "").lower()
            if st in ("closed", "pending_close", "pending_close_retry", "exit_submitted"):
                return False
    if _close_pending(system, tid):
        return False
    try:
        dte0 = getattr(system, "dte0_manager", None)
        ex = getattr(dte0, "options_executor", None) if dte0 is not None else None
        opens = list(ex.get_open_positions() or []) if ex is not None else []
        if opens and not any(str(getattr(p, "position_id", "") or "") == tid for p in opens):
            return False
    except Exception:
        pass
    return True


def _close_pending(system: Any, trade_id: str) -> bool:
    tid = str(trade_id or "").strip()
    if not tid or system is None:
        return False
    for attr in ("_orb_0dte_lifecycle", "_trendline_lifecycle"):
        life = getattr(system, attr, None)
        if not isinstance(life, dict):
            continue
        rec = life.get(tid)
        if not isinstance(rec, dict):
            continue
        if float(rec.get("exit_signalled_ts", 0.0) or 0.0) > 0 and not bool(rec.get("close_confirmed")):
            return True
        if str(rec.get("state") or "") in ("pending_close", "pending_close_retry", "exit_submitted"):
            return True
    return False


def last_successful_eval_ts(system: Any, trade_id: str) -> float:
    bag = getattr(system, LAST_EVAL_ATTR, None)
    if not isinstance(bag, dict):
        return 0.0
    try:
        return float(bag.get(str(trade_id or "").strip()) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def mark_successful_eval(system: Any, trade_id: str, *, now_ts: Optional[float] = None) -> None:
    import time

    tid = str(trade_id or "").strip()
    if not tid or system is None:
        return
    bag = getattr(system, LAST_EVAL_ATTR, None)
    if not isinstance(bag, dict):
        bag = {}
        setattr(system, LAST_EVAL_ATTR, bag)
    bag[tid] = float(now_ts if now_ts is not None else time.time())


def seconds_since_last_successful_eval(system: Any, trade_id: str, *, now_ts: Optional[float] = None) -> float:
    import time

    last = last_successful_eval_ts(system, trade_id)
    if last <= 0:
        return 1e9
    now = float(now_ts if now_ts is not None else time.time())
    return max(0.0, now - last)


def _fair_rotate(slots: Sequence[EvalSlot], *, start_tl: bool) -> List[EvalSlot]:
    orb = [s for s in slots if s.lane == LANE_ORB]
    tl = [s for s in slots if s.lane == LANE_TRENDLINE]
    out: List[EvalSlot] = []
    i = j = 0
    take_tl = bool(start_tl)
    while i < len(orb) or j < len(tl):
        if take_tl:
            if j < len(tl):
                out.append(tl[j])
                j += 1
            elif i < len(orb):
                out.append(orb[i])
                i += 1
        else:
            if i < len(orb):
                out.append(orb[i])
                i += 1
            elif j < len(tl):
                out.append(tl[j])
                j += 1
        take_tl = not take_tl
    return out


def schedule_owned_eval_order(
    positions: Iterable[Any],
    *,
    system: Any = None,
    pending: Optional[Dict[str, Any]] = None,
    now_ts: Optional[float] = None,
    rotate_start_tl: bool = False,
) -> List[EvalSlot]:
    """Return owned positions in locked P0–P3 order with ORB/TL fair rotate."""
    import time

    rows = [p for p in (positions or []) if p is not None]
    pending_ids = {str(tid).strip() for tid in (pending or {}) if str(tid or "").strip()}
    trail_ids = set(collect_armed_trail_priority_ids(rows))
    if system is not None:
        from modules.unified_0dte_monitor_registry import TRAIL_PRIORITY_ATTR

        setattr(system, TRAIL_PRIORITY_ATTR, list(trail_ids))
    now = float(now_ts if now_ts is not None else time.time())

    slots: List[EvalSlot] = []
    for pos in rows:
        tid = str(getattr(pos, "position_id", "") or "").strip()
        if not tid:
            continue
        pending_flag = tid in pending_ids
        exit_sub = _close_pending(system, tid)
        trail = tid in trail_ids
        age = seconds_since_last_successful_eval(system, tid, now_ts=now)
        if pending_flag or exit_sub:
            pri = 0
        elif trail:
            pri = 1
        else:
            pri = 3
        slots.append(
            EvalSlot(
                position=pos,
                trade_id=tid,
                symbol=str(getattr(pos, "symbol", "") or "").strip().upper(),
                lane=_lane_for(pos),
                priority=pri,
                seconds_since_last_eval=age,
                pending=pending_flag,
                trail_armed=trail,
                exit_submitted=exit_sub,
            )
        )

    # P2: among ordinary (pri 3), those with a last-eval age become P2 when we sort
    # by (-priority is wrong). Assign P2 to non-P0/P1 ordered by oldest age.
    p0 = [s for s in slots if s.priority == 0]
    p1 = [s for s in slots if s.priority == 1]
    rest = [s for s in slots if s.priority not in (0, 1)]
    rest_sorted = sorted(rest, key=lambda s: -float(s.seconds_since_last_eval or 0.0))
    for s in rest_sorted:
        s.priority = 2 if s.seconds_since_last_eval >= 0 else 3

    def _prio_block(block: List[EvalSlot]) -> List[EvalSlot]:
        return _fair_rotate(block, start_tl=rotate_start_tl)

    return _prio_block(p0) + _prio_block(p1) + _prio_block(rest_sorted)


def collect_owned_open_positions(system: Any) -> List[Any]:
    """ORB + Trendline open lots, de-duplicated by trade_id."""
    executor = getattr(getattr(system, "dte0_manager", None), "options_executor", None)
    if executor is None:
        return []
    raw = executor.get_open_positions() or []
    try:
        raw = system._orb_0dte_filter_demo_open_positions(executor, raw)
    except Exception:
        pass
    orb: List[Any] = []
    try:
        orb = list(system._filter_orb_only_open_positions(raw) or [])
    except Exception:
        orb = list(raw)
    tl: List[Any] = []
    try:
        tl_map = system._trendline_unified_open_positions() or {}
        tl = list(tl_map.values()) if isinstance(tl_map, dict) else []
        tl = system._orb_0dte_filter_demo_open_positions(executor, tl)
    except Exception:
        tl = []
    seen = set()
    out: List[Any] = []
    for pos in list(orb) + list(tl):
        tid = str(getattr(pos, "position_id", "") or "").strip()
        if not tid or tid in seen:
            continue
        seen.add(tid)
        out.append(pos)
    return out


async def run_fair_owned_eval_tick(system: Any, *, tick_id: str = "", trigger_source: str = "") -> None:
    """Evaluate every owned lot in P0–P3 order and write its flight-recorder row.

    A long exit check notes the eval budget and still finishes the book. Skipping
    a name drops both the exit decision and the matching path sample.
    """
    import asyncio
    import time
    from datetime import datetime, timezone

    from modules.owned_tick_observe import (
        log_eval_interval_percentiles,
        observe_deferred_owned_tick,
    )
    from modules.unified_0dte_monitor_registry import (
        EVAL_BUDGET_DEADLINE_ATTR,
        EVAL_BUDGET_HIT_ATTR,
    )
    from modules.underlying_freshness import (
        note_observation_stat,
        resolve_underlying_observation,
        stamp_underlying_provenance,
        STATS_ATTR,
    )

    tick = str(
        tick_id
        or getattr(system, "_unified_0dte_monitor_tick_id", "")
        or getattr(system, "_unified_0dte_tick_id", "")
        or ""
    )
    positions = collect_owned_open_positions(system)
    pending = getattr(system, "_0dte_pending_monitor_registrations", None)
    rotate_tl = bool(abs(hash(tick)) % 2 == 1) if tick else False
    schedule = schedule_owned_eval_order(
        positions,
        system=system,
        pending=pending if isinstance(pending, dict) else {},
        rotate_start_tl=rotate_tl,
    )
    system._owned_eval_schedule = schedule
    system._owned_eval_order_ids = [s.trade_id for s in schedule]
    if not schedule:
        return

    stealth = None
    try:
        stealth = system._ensure_options_stealth_engine()
    except Exception:
        stealth = None
    now = datetime.now(timezone.utc)
    coverage = {
        "evaluated": 0,
        "deferred": 0,
        "orb_evaluated": 0,
        "tl_evaluated": 0,
        "pending_skipped": 0,
        "trail_evaluated": 0,
    }
    for slot in schedule:
        t0 = time.monotonic()
        pos = slot.position
        md = getattr(pos, "metadata", None)
        if not isinstance(md, dict):
            md = {}
            try:
                pos.metadata = md
            except Exception:
                pass
        if slot.exit_submitted or not _position_still_open(
            system, slot.trade_id, slot.position
        ):
            coverage["pending_skipped"] += 1
            await asyncio.sleep(0)
            continue
        deadline = float(getattr(system, EVAL_BUDGET_DEADLINE_ATTR, 0.0) or 0.0)
        if deadline > 0.0 and time.monotonic() >= deadline:
            setattr(system, EVAL_BUDGET_HIT_ATTR, True)
        obs = resolve_underlying_observation(system, slot.symbol)
        note_observation_stat(system, obs)
        osnap = md.get("option_stealth") if isinstance(md.get("option_stealth"), dict) else {}
        if not isinstance(osnap, dict):
            osnap = {}
            md["option_stealth"] = osnap
        stamp_underlying_provenance(osnap, obs)
        oq = md.get("exact_quote") if isinstance(md.get("exact_quote"), dict) else None
        if not isinstance(oq, dict):
            oq = md.get("option_quote") if isinstance(md.get("option_quote"), dict) else {}
        out = None
        t_strategy = time.monotonic()
        if stealth is not None:
            try:
                out = system._0dte_run_stealth_exit_decision(
                    stealth=stealth,
                    pos=pos,
                    position_metadata=md,
                    current_underlying=float(obs.price_or_zero or 0.0),
                    now=now,
                    option_quote=oq if oq else None,
                    monitor_tick_id=tick,
                )
            except Exception as exc:
                log.warning(
                    "OWNED_FAIR_EVAL_FAIL | trade_id=%s | symbol=%s | err=%s",
                    slot.trade_id,
                    slot.symbol,
                    exc,
                )
                observe_deferred_owned_tick(
                    system, pos, tick_id=tick, defer_reason="eval_error"
                )
                coverage["deferred"] += 1
                await asyncio.sleep(0)
                continue
        strategy_eval_ms = (time.monotonic() - t_strategy) * 1000.0
        mark_successful_eval(system, slot.trade_id)
        t_obs = time.monotonic()
        po_observe_ms = (time.monotonic() - t_obs) * 1000.0
        po_enqueue_ms = 0.0
        try:
            po = getattr(system, "profit_optimizer", None) or getattr(
                system, "_profit_optimizer", None
            )
            rec = getattr(po, "recorder", None) if po is not None else None
            po_enqueue_ms = float(getattr(rec, "_last_enqueue_ms", 0.0) or 0.0)
        except Exception:
            po_enqueue_ms = 0.0
        eval_ms = (time.monotonic() - t0) * 1000.0
        coverage["evaluated"] += 1
        if slot.lane == LANE_TRENDLINE:
            coverage["tl_evaluated"] += 1
        else:
            coverage["orb_evaluated"] += 1
        if slot.trail_armed:
            coverage["trail_evaluated"] += 1
        log.info(
            "POSITION_EVAL_MS | trade_id=%s | symbol=%s | lane=%s | priority=%s | "
            "eval_ms=%.1f | strategy_eval_ms=%.1f | po_observe_ms=%.1f | po_enqueue_ms=%.1f | "
            "evaluated=true | underlying_source=%s | tick_id=%s | trigger=%s",
            slot.trade_id,
            slot.symbol,
            slot.lane,
            slot.priority,
            float(eval_ms),
            float(strategy_eval_ms),
            float(po_observe_ms),
            float(po_enqueue_ms),
            str(obs.source),
            tick,
            str(trigger_source or ""),
        )
        await asyncio.sleep(0)

    stats = getattr(system, STATS_ATTR, None) if STATS_ATTR else None
    if not isinstance(stats, dict):
        stats = {}
    log.info(
        "MONITOR_COVERAGE | tick_id=%s | trigger=%s | owned=%d | evaluated=%d | deferred=%d | "
        "orb_evaluated=%d | tl_evaluated=%d | trail_evaluated=%d | pending_skipped=%d",
        tick,
        str(trigger_source or ""),
        len(schedule),
        coverage["evaluated"],
        coverage["deferred"],
        coverage["orb_evaluated"],
        coverage["tl_evaluated"],
        coverage["trail_evaluated"],
        coverage["pending_skipped"],
    )
    log_eval_interval_percentiles(system, tick_id=tick)
    log.info(
        "UNDERLYING_CACHE | tick_id=%s | fresh_batch=%d | shared_cache=%d | last_good=%d | "
        "unavailable=%d",
        tick,
        int(stats.get("fresh_batch") or 0),
        int(stats.get("shared_cache") or 0),
        int(stats.get("last_good") or 0),
        int(stats.get("unavailable") or 0),
    )
    try:
        po = getattr(system, "profit_optimizer", None) or getattr(
            system, "_profit_optimizer", None
        )
        rec = getattr(po, "recorder", None) if po is not None else None
        tel = rec.persist_telemetry() if rec is not None and hasattr(rec, "persist_telemetry") else {}
        log.info(
            "PO_PERSIST_QUEUE | tick_id=%s | po_persist_queue_depth=%d | "
            "oldest_unpersisted_age_ms=%.1f | last_successful_persist_age_ms=%.1f | "
            "coalesced_pending=%d | dirty=%s",
            tick,
            int(tel.get("queue_depth") or 0),
            float(tel.get("oldest_unpersisted_age_ms") or 0.0),
            float(tel.get("last_successful_persist_age_ms") or 0.0),
            int(tel.get("coalesced_pending") or 0),
            str(bool(tel.get("dirty"))).lower(),
        )
    except Exception:
        pass
