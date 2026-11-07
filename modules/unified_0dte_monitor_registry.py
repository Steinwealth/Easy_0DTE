"""
Unified 0DTE open-position monitor registry helpers (ORB + Trendline).

Keep monitor identity bookkeeping strategy-agnostic. Path-specific exit/sizing
logic stays in prime_trading_system ORB / Trendline monitors.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

log = logging.getLogger(__name__)

_LOG_THROTTLE_MONO: Dict[str, float] = {}


def log_info_throttled(
    logger: logging.Logger,
    key: str,
    msg: str,
    *args: Any,
    interval_sec: float = 15.0,
) -> None:
    """INFO at most once per key per interval (per-position monitor heartbeats)."""
    now = time.monotonic()
    last = float(_LOG_THROTTLE_MONO.get(key, -1e9))
    if now - last < max(0.0, float(interval_sec or 0.0)):
        return
    _LOG_THROTTLE_MONO[key] = now
    logger.info(msg, *args)


def resolve_enrollment_failed_sec(system: Any = None, *, default: float = 60.0) -> float:
    """Do not mark enroll FAILED while a slow tick can still catch the fill.

    Floor 60s; also 2× last unified ``total_cycle_ms`` so a 16–106s tick
    cannot false-fail SMH-style fills.
    """
    cycle_sec = 0.0
    if system is not None:
        cadence = getattr(system, "_unified_0dte_monitor_cadence", None) or {}
        if isinstance(cadence, dict):
            try:
                cycle_sec = float(cadence.get("total_cycle_ms") or 0.0) / 1000.0
            except (TypeError, ValueError):
                cycle_sec = 0.0
    try:
        floor = max(0.0, float(default if default is not None else 60.0))
    except (TypeError, ValueError):
        floor = 60.0
    return max(floor, 2.0 * float(cycle_sec or 0.0))


EVAL_BUDGET_DEADLINE_ATTR = "_unified_0dte_eval_deadline_mono"
EVAL_BUDGET_HIT_ATTR = "_unified_0dte_eval_budget_hit"
EVAL_SKIPPED_IDS_ATTR = "_unified_0dte_eval_skipped_ids"
TRAIL_PRIORITY_ATTR = "_unified_0dte_trail_priority_ids"
DEFAULT_EVAL_BUDGET_MS = 1200.0
DEFAULT_EVAL_BUDGET_MS_OPEN20 = 1800.0
EVAL_BUDGET_MS_CAP = 2200.0
# ARM_TRAIL is +30%. Prioritize names already giving back ≥10pp so the 1200ms
# eval budget cannot skip a winner sitting on the 20pp band.
_TRAIL_ARM_PCT = 0.30
_TRAIL_NEAR_GIVEBACK_PCT = 0.10


def resolve_monitor_eval_budget_ms(*, open_count: int = 0) -> float:
    """1200ms default; 1800ms when 20+ opens so fair-rotate can finish more names."""
    try:
        base = float(
            os.getenv("0DTE_MONITOR_EVAL_BUDGET_MS", str(DEFAULT_EVAL_BUDGET_MS))
            or DEFAULT_EVAL_BUDGET_MS
        )
    except (TypeError, ValueError):
        base = DEFAULT_EVAL_BUDGET_MS
    try:
        bump = float(
            os.getenv(
                "0DTE_MONITOR_EVAL_BUDGET_MS_OPEN20",
                str(DEFAULT_EVAL_BUDGET_MS_OPEN20),
            )
            or DEFAULT_EVAL_BUDGET_MS_OPEN20
        )
    except (TypeError, ValueError):
        bump = DEFAULT_EVAL_BUDGET_MS_OPEN20
    base = max(250.0, float(base))
    bump = max(base, float(bump))
    if int(open_count or 0) >= 20:
        return min(EVAL_BUDGET_MS_CAP, bump)
    return min(EVAL_BUDGET_MS_CAP, base)


def begin_monitor_eval_budget(
    system: Any,
    *,
    budget_ms: Optional[float] = None,
    open_count: int = 0,
) -> None:
    """Start a wall-clock evaluate budget for this unified tick."""
    if system is None:
        return
    ms = budget_ms
    if ms is None:
        ms = resolve_monitor_eval_budget_ms(open_count=open_count)
    try:
        ms = max(250.0, float(ms or DEFAULT_EVAL_BUDGET_MS))
    except (TypeError, ValueError):
        ms = DEFAULT_EVAL_BUDGET_MS
    setattr(system, EVAL_BUDGET_DEADLINE_ATTR, time.monotonic() + (ms / 1000.0))
    setattr(system, EVAL_BUDGET_HIT_ATTR, False)
    prev = getattr(system, EVAL_SKIPPED_IDS_ATTR, None)
    setattr(system, "_unified_0dte_eval_priority_ids", list(prev or []))
    setattr(system, EVAL_SKIPPED_IDS_ATTR, [])


def collect_armed_trail_priority_ids(positions: Iterable[Any]) -> List[str]:
    """Trade ids already on the PX2/PX3 trail that must evaluate inside the budget."""
    out: List[str] = []
    seen = set()
    for pos in positions or []:
        tid = str(getattr(pos, "position_id", "") or "").strip()
        if not tid or tid in seen:
            continue
        meta = getattr(pos, "metadata", None)
        osnap = meta.get("option_stealth") if isinstance(meta, dict) else None
        if not isinstance(osnap, dict):
            continue
        try:
            hwm = float(
                osnap.get("px2_prem_hwm_pct")
                or osnap.get("max_pnl_pct")
                or 0.0
            )
            cur = float(osnap.get("current_pnl_pct") or 0.0)
        except (TypeError, ValueError):
            continue
        giveback = hwm - cur
        px3_mode = str(osnap.get("px3_mode") or "").strip()
        if hwm + 1e-12 < _TRAIL_ARM_PCT:
            continue
        if giveback + 1e-12 < _TRAIL_NEAR_GIVEBACK_PCT and not px3_mode:
            continue
        seen.add(tid)
        out.append(tid)
    return out


def should_defer_eval_for_budget(
    system: Any,
    trade_id: str,
    *,
    is_pending: bool = False,
) -> bool:
    """Skip non-pending stealth evaluate when the tick budget is exhausted."""
    if is_pending or system is None:
        return False
    tid = str(trade_id or "").strip()
    trail = getattr(system, TRAIL_PRIORITY_ATTR, None) or []
    if tid and tid in trail:
        return False
    deadline = float(getattr(system, EVAL_BUDGET_DEADLINE_ATTR, 0.0) or 0.0)
    if deadline <= 0.0 or time.monotonic() < deadline:
        return False
    setattr(system, EVAL_BUDGET_HIT_ATTR, True)
    if tid:
        skipped = getattr(system, EVAL_SKIPPED_IDS_ATTR, None)
        if not isinstance(skipped, list):
            skipped = []
            setattr(system, EVAL_SKIPPED_IDS_ATTR, skipped)
        skipped.append(tid)
    return True


def pending_first_positions(
    positions: Iterable[Any],
    pending: Optional[Dict[str, Any]],
    *,
    extra_priority_ids: Optional[Iterable[str]] = None,
    system: Any = None,
) -> List[Any]:
    """Evaluate unenrolled fills (then last-tick skips) before the rest of the book.

    First-tick ``price_stop`` / stealth attach must not sit behind a long
    sequential ORB-then-TL pass when a new fill is still pending.
    Armed-trail / PX3 runners evaluate next so a 1200ms budget cannot skip
    a name sitting on the 20pp giveback band.
    """
    rows = list(positions or [])
    pending_ids = {
        str(tid).strip()
        for tid in (pending or {})
        if str(tid or "").strip()
    }
    trail_ids = collect_armed_trail_priority_ids(rows)
    if system is not None:
        setattr(system, TRAIL_PRIORITY_ATTR, list(trail_ids))
    extra_ids = {
        str(tid).strip()
        for tid in (extra_priority_ids or [])
        if str(tid or "").strip()
    }
    extra_ids.update(trail_ids)
    if (not pending_ids and not extra_ids) or not rows:
        return rows
    first: List[Any] = []
    second: List[Any] = []
    rest: List[Any] = []
    for pos in rows:
        tid = str(getattr(pos, "position_id", "") or "").strip()
        if tid in pending_ids:
            first.append(pos)
        elif tid in extra_ids:
            second.append(pos)
        else:
            rest.append(pos)
    return first + second + rest


def enqueue_0dte_pending_monitor(
    pending: Dict[str, Dict[str, Any]],
    *,
    trade_id: str,
    symbol: str,
    strategy_type: str,
    registered_ts: Optional[float] = None,
) -> None:
    """Record fill/registration until the first successful monitor tick."""
    tid = str(trade_id or "").strip()
    if not tid or not isinstance(pending, dict):
        return
    pending[tid] = {
        "symbol": str(symbol or "").strip(),
        "strategy_type": str(strategy_type or "0dte").strip().lower() or "0dte",
        "registered_ts": float(registered_ts if registered_ts is not None else time.time()),
    }


def clear_0dte_monitor_identity(
    *,
    first_seen: Dict[str, float],
    pending: Dict[str, Dict[str, Any]],
    not_monitored_logged: Set[str],
    trade_id: str,
    heartbeat_ts: Optional[Dict[str, float]] = None,
) -> None:
    """Drop monitor identity for a closed / removed 0DTE trade (ORB or Trendline)."""
    tid = str(trade_id or "").strip()
    if not tid:
        return
    if isinstance(first_seen, dict):
        first_seen.pop(tid, None)
    if isinstance(pending, dict):
        pending.pop(tid, None)
    if isinstance(not_monitored_logged, set):
        not_monitored_logged.discard(tid)
    if isinstance(heartbeat_ts, dict):
        heartbeat_ts.pop(f"0dte_mfe_hb:{tid}", None)


def stamp_0dte_first_seen_on_monitor_tick(
    *,
    first_seen: Dict[str, float],
    pending: Dict[str, Dict[str, Any]],
    not_monitored_logged: Set[str],
    trade_id: str,
    now_ts: Optional[float] = None,
) -> bool:
    """
    Mark first monitor tick for a trade.

    Returns True when this is the first stamp (caller should emit MONITOR_START).
    """
    tid = str(trade_id or "").strip()
    if not tid:
        return False
    if tid in first_seen:
        pending.pop(tid, None)
        not_monitored_logged.discard(tid)
        return False
    first_seen[tid] = float(now_ts if now_ts is not None else time.time())
    pending.pop(tid, None)
    not_monitored_logged.discard(tid)
    return True


def audit_demo_dual_book_drift(executor: Any) -> Dict[str, Any]:
    """Detect ghost rows in executor.positions while Demo mock book is truth.

    Demo open truth is ``mock_executor.active_positions``. Non-closed rows left in
    ``executor.positions`` are ghosts that can confuse Live-shaped helpers.
    Never constructs a cold MockOptionsExecutor.
    """
    out: Dict[str, Any] = {
        "ok": True,
        "skipped": True,
        "ghost_executor_ids": [],
        "mock_open_count": 0,
        "executor_open_count": 0,
    }
    if executor is None:
        return out
    use_mock = getattr(executor, "_use_mock_fills", None)
    if not callable(use_mock) or not bool(use_mock()):
        return out
    mock = getattr(executor, "mock_executor", None)
    if mock is None:
        out["skipped"] = False
        out["ok"] = False
        out["reason"] = "missing_mock_executor"
        log.warning(
            "DEMO_REGISTRY_DRIFT | reason=missing_mock_executor | "
            "action=skip_cold_construct"
        )
        return out

    out["skipped"] = False
    mock_ids = set()
    for pid in getattr(mock, "active_positions", {}) or {}:
        tid = str(pid or "").strip()
        if tid:
            mock_ids.add(tid)
    out["mock_open_count"] = len(mock_ids)

    ghosts: List[str] = []
    exec_open = 0
    for pid, pos in (getattr(executor, "positions", None) or {}).items():
        tid = str(pid or "").strip()
        if not tid:
            continue
        st = str(getattr(pos, "status", "") or "").strip().lower()
        if st == "closed":
            continue
        exec_open += 1
        if tid not in mock_ids:
            ghosts.append(tid)
    out["executor_open_count"] = exec_open
    out["ghost_executor_ids"] = ghosts
    if ghosts:
        out["ok"] = False
        log.warning(
            "DEMO_REGISTRY_DRIFT | ghost_executor_positions=%s | mock_open=%d | "
            "action=mock_is_truth",
            ",".join(ghosts[:12]),
            len(mock_ids),
        )
    return out


def collect_stale_0dte_trade_ids(
    *,
    active_ids: Iterable[str],
    lifecycle_by_id: Dict[str, Dict[str, Any]],
    first_seen: Dict[str, float],
    now_ts: float,
    stale_sec: float = 45.0,
) -> List[str]:
    """Trade ids with no recent ``last_monitor_tick_ts`` (or never stamped first_seen)."""
    out: List[str] = []
    threshold = max(5.0, float(stale_sec or 45.0))
    for pid in active_ids:
        tid = str(pid or "").strip()
        if not tid:
            continue
        life = lifecycle_by_id.get(tid) if isinstance(lifecycle_by_id, dict) else None
        if not isinstance(life, dict):
            life = {}
        last_tick = float(life.get("last_monitor_tick_ts") or 0.0)
        seen = float(first_seen.get(tid) or 0.0)
        age_ref = last_tick if last_tick > 0 else seen
        if age_ref <= 0.0 or (float(now_ts) - age_ref) >= threshold:
            out.append(tid)
    return out


def should_emit_0dte_mfe_heartbeat(
    hb_ts: Dict[str, float],
    *,
    trade_id: str,
    min_interval_sec: float = 60.0,
    now_ts: Optional[float] = None,
) -> bool:
    tid = str(trade_id or "").strip()
    if not tid or not isinstance(hb_ts, dict):
        return False
    now = float(now_ts if now_ts is not None else time.time())
    key = f"0dte_mfe_hb:{tid}"
    last = float(hb_ts.get(key) or 0.0)
    # last<=0 means never emitted — always allow first heartbeat.
    if last > 0.0 and (now - last) < float(min_interval_sec or 60.0):
        return False
    hb_ts[key] = now
    return True


def emit_0dte_mfe_heartbeat(
    *,
    strategy_type: str,
    trade_id: str,
    symbol: str,
    current_pnl_pct: float,
    max_pnl_pct: float,
    had_opportunity: bool,
    stealth_mode: str,
    trigger_source: str,
) -> None:
    """Single WARNING heartbeat usable for ORB and Trendline 0DTE."""
    st = str(strategy_type or "0dte").strip().lower() or "0dte"
    log.warning(
        "0DTE_MFE_HEARTBEAT | strategy_type=%s | trade_id=%s | symbol=%s | current_pnl_pct=%.4f | "
        "max_pnl_pct=%.4f | had_opportunity=%s | stealth_mode=%s | trigger=%s",
        st,
        str(trade_id or ""),
        str(symbol or ""),
        float(current_pnl_pct or 0.0),
        float(max_pnl_pct or 0.0),
        str(bool(had_opportunity)).lower(),
        str(stealth_mode or "inactive"),
        str(trigger_source or ""),
    )


def log_0dte_monitor_empty_open_set(
    *,
    strategy_type: str,
    trigger_source: str,
    executor_rows: int,
    pending_monitor: int,
    reason: str = "unified_open_filter_empty",
) -> None:
    st = str(strategy_type or "0dte").strip().lower() or "0dte"
    # WARNING: cross-path pending noise was flooding ERROR during BO-only opens.
    log.warning(
        "0DTE_MONITOR_EMPTY_OPEN_SET | strategy_type=%s | trigger=%s | executor_rows=%d | "
        "pending_monitor=%d | reason=%s",
        st,
        str(trigger_source or ""),
        int(executor_rows),
        int(pending_monitor),
        str(reason or ""),
    )


def log_0dte_monitor_filtered_empty(
    *,
    strategy_type: str,
    trigger_source: str,
    active_before_filter: int,
    pending_monitor: int,
    reason: str = "demo_ghost_or_closed_filter",
) -> None:
    st = str(strategy_type or "0dte").strip().lower() or "0dte"
    log.error(
        "0DTE_MONITOR_FILTERED_EMPTY | strategy_type=%s | trigger=%s | active_before_filter=%d | "
        "pending_monitor=%d | reason=%s",
        st,
        str(trigger_source or ""),
        int(active_before_filter),
        int(pending_monitor),
        str(reason or ""),
    )


def log_0dte_monitor_stale(
    *,
    strategy_type: str,
    open_count: int,
    stale_ids: List[str],
    stale_sec: float,
) -> None:
    st = str(strategy_type or "0dte").strip().lower() or "0dte"
    log.error(
        "0DTE_MONITOR_STALE | strategy_type=%s | open=%d | stale=%d | trade_ids=%s | "
        "force_immediate=true | stale_sec=%.1f",
        st,
        int(open_count),
        len(stale_ids),
        ",".join(stale_ids[:12]),
        float(stale_sec),
    )


def sweep_0dte_pending_not_monitored(
    *,
    pending: Dict[str, Dict[str, Any]],
    not_monitored_logged: Set[str],
    active_trade_ids: Set[str],
    strategy_substring: str,
    grace_sec: float,
    now_ts: Optional[float] = None,
    delayed_sec: Optional[float] = None,
    failed_sec: Optional[float] = None,
    roster_trade_ids: Optional[Set[str]] = None,
) -> None:
    """Bounded enrollment telemetry for filled positions awaiting first monitor tick.

    Lifecycle (Commit E):
      FILLED → PENDING_MONITOR_ENROLLMENT (within grace; INFO, not an alarm)
            → ENROLLMENT_DELAYED (after delayed_sec; WARNING once)
            → ENROLLMENT_FAILED (after failed_sec; ERROR once)

    A trade id on this tick's monitor roster is already being watched. Clear it
    here. The sweep runs before the per-name stamp, and an eval skip used to
    leave the fill pending until the age clock logged FAILED on the same tick
    the monitor listed it (HIMS-28.5c-197, 2026-10-30).

    Legacy ``0DTE_POSITION_NOT_MONITORED`` is emitted only at the FAILED stage
    (same event as ``0DTE_MONITOR_ENROLLMENT_FAILED``) for grep continuity.
    """
    now = float(now_ts if now_ts is not None else time.time())
    needle = str(strategy_substring or "").strip().lower()
    grace = max(5.0, float(grace_sec or 5.0))
    delayed_after = float(delayed_sec if delayed_sec is not None else max(grace, grace * 2.0))
    failed_after = float(
        failed_sec if failed_sec is not None else max(delayed_after + 15.0, 60.0)
    )
    roster = {
        str(tid).strip()
        for tid in (roster_trade_ids or set())
        if str(tid or "").strip()
    }
    # Per-trade one-shot flags stored on pending info + not_monitored_logged for FAILED.
    for pending_trade_id, pending_info in list((pending or {}).items()):
        info = pending_info if isinstance(pending_info, dict) else {}
        st = str(info.get("strategy_type") or "").strip().lower()
        if needle and needle not in st:
            continue
        if pending_trade_id not in active_trade_ids:
            pending.pop(pending_trade_id, None)
            not_monitored_logged.discard(pending_trade_id)
            continue
        if pending_trade_id in roster:
            pending.pop(pending_trade_id, None)
            not_monitored_logged.discard(pending_trade_id)
            continue
        reg_ts = float(info.get("registered_ts") or 0.0)
        if reg_ts <= 0:
            continue
        age = now - reg_ts
        sym = str(info.get("symbol") or "")
        st_label = st or needle or "0dte"

        if age < grace:
            if not info.get("_enrollment_pending_logged"):
                info["_enrollment_pending_logged"] = True
                pending[pending_trade_id] = info
                log.info(
                    "0DTE_MONITOR_PENDING_ENROLLMENT | strategy_type=%s | trade_id=%s | "
                    "symbol=%s | age_sec=%.1f | grace_sec=%.1f",
                    st_label,
                    pending_trade_id,
                    sym,
                    age,
                    grace,
                )
            continue

        if age < delayed_after:
            continue

        if age < failed_after:
            if not info.get("_enrollment_delayed_logged"):
                info["_enrollment_delayed_logged"] = True
                pending[pending_trade_id] = info
                log.warning(
                    "0DTE_MONITOR_ENROLLMENT_DELAYED | strategy_type=%s | trade_id=%s | "
                    "symbol=%s | age_sec=%.1f | delayed_after_sec=%.1f",
                    st_label,
                    pending_trade_id,
                    sym,
                    age,
                    delayed_after,
                )
            continue

        if pending_trade_id in not_monitored_logged:
            continue
        not_monitored_logged.add(pending_trade_id)
        info["_enrollment_failed_logged"] = True
        pending[pending_trade_id] = info
        log.error(
            "0DTE_MONITOR_ENROLLMENT_FAILED | strategy_type=%s | trade_id=%s | symbol=%s | "
            "age_sec=%.1f | failed_after_sec=%.1f",
            st_label,
            pending_trade_id,
            sym,
            age,
            failed_after,
        )
        # Legacy alias for existing greps / dashboards.
        log.error(
            "0DTE_POSITION_NOT_MONITORED | strategy_type=%s | trade_id=%s | symbol=%s | "
            "reason=enrollment_failed | age_sec=%.1f",
            st_label,
            pending_trade_id,
            sym,
            age,
        )
        not_monitored_logged.add(pending_trade_id)


def count_pending_monitor_for_strategy(
    pending: Optional[Dict[str, Dict[str, Any]]],
    *,
    strategy_substring: str,
    exclude_substring: str = "",
) -> int:
    """Count pending enrollments for one path (avoid cross-strategy EMPTY_OPEN_SET spam).

    Jul 31: Trendline logged ERROR with pending_monitor=1 while only a Breakout
    fill was pending on the shared registry.
    """
    needle = str(strategy_substring or "").strip().lower()
    exclude = str(exclude_substring or "").strip().lower()
    n = 0
    for _tid, info in (pending or {}).items():
        st = str((info or {}).get("strategy_type") or "").strip().lower()
        if exclude and exclude in st:
            continue
        if needle:
            if needle in st:
                n += 1
        else:
            # empty needle ≈ ORB lane (non-trendline), including breakout_0dte
            if "trendline" not in st:
                n += 1
    return n


def count_executor_rows_for_strategy(
    positions: Iterable[Any],
    *,
    strategy_substring: str,
) -> int:
    """Count executor rows whose strategy_type contains substring (attr or metadata)."""
    from modules.unified_0dte_account import _position_strategy_type

    needle = str(strategy_substring or "").strip().lower()
    n = 0
    for pos in positions or []:
        st = _position_strategy_type(pos)
        if not st:
            st = str(getattr(pos, "strategy_type", "") or "").strip().lower()
        if needle and needle in st:
            n += 1
        elif not needle and "trendline" not in st:
            # empty needle ≈ ORB/non-trendline
            n += 1
    return n
