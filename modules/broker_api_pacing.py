"""
E*TRADE Market API pacing for this deployment.

E*TRADE Market module limits: 4 requests/sec throttle, 14,000 requests/hour per OAuth user.
Defaults keep this Cloud Run service under the per-second throttle on batch quotes and chains.

Lane counters + wait/latency/deferral telemetry track Market API usage by purpose so
open-position pressure can be diagnosed without guessing which discovery/enrich path
is competing with exact OCC exits.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from contextlib import asynccontextmanager, contextmanager
from typing import Dict, Iterator, List, Optional, Tuple

log = logging.getLogger(__name__)

# E*TRADE hard limit without overrideSymbolCount (error 1023 above 25).
ETRADE_BATCH_QUOTE_SIZE = 25
# ~3.5 req/sec — headroom under the 4/sec Market API throttle for this service alone.
_DEFAULT_INTER_REQUEST_SEC = 0.28

# Canonical lane names for open-position / discovery / research request accounting.
LANE_OPEN_EXACT_OCC = "open_exact_occ"
LANE_OWNED_RECOVERY = "owned_recovery"
LANE_BROKER_POSITION_SYNC = "broker_position_sync"
LANE_TRENDLINE_WATCH_EQUITY = "trendline_watch_equity"
LANE_BREAKOUT_WATCH_EQUITY = "breakout_watch_equity"
LANE_TRENDLINE_BUILD = "trendline_build"
LANE_OPTION_CHAIN_PC_OI = "option_chain_pc_oi"
LANE_PO_SNAPSHOT_CHAIN = "po_snapshot_chain"
LANE_EQUITY_BATCH = "equity_batch"
LANE_OTHER_MARKET = "other_market"

# Lower number = higher priority (open exact must not wait behind discovery).
_LANE_PRIORITY: Dict[str, int] = {
    LANE_OPEN_EXACT_OCC: 0,
    LANE_OWNED_RECOVERY: 1,
    LANE_BROKER_POSITION_SYNC: 2,
    LANE_TRENDLINE_WATCH_EQUITY: 5,
    LANE_BREAKOUT_WATCH_EQUITY: 5,
    LANE_TRENDLINE_BUILD: 6,
    LANE_OPTION_CHAIN_PC_OI: 7,
    # Entry/exit PO snapshots must beat discovery (5); stay below open OCC (0–2).
    LANE_PO_SNAPSHOT_CHAIN: 3,
    LANE_EQUITY_BATCH: 6,
    LANE_OTHER_MARKET: 9,
}

_KNOWN_LANES = tuple(_LANE_PRIORITY.keys())

_lane_lock = threading.Lock()
_lane_counts: Dict[str, int] = {k: 0 for k in _KNOWN_LANES}
_lane_latency_ms_sum: Dict[str, float] = {k: 0.0 for k in _KNOWN_LANES}
_lane_latency_n: Dict[str, int] = {k: 0 for k in _KNOWN_LANES}
_lane_pacing_wait_ms_sum: Dict[str, float] = {k: 0.0 for k in _KNOWN_LANES}
_lane_queue_wait_ms_sum: Dict[str, float] = {k: 0.0 for k in _KNOWN_LANES}
_lane_deferrals: Dict[str, int] = {k: 0 for k in _KNOWN_LANES}
_lane_total = 0
_lane_last_log_total = 0
_request_timestamps: List[float] = []
_active_lane: Optional[str] = None
_active_priority: int = 99
_gate_cv = threading.Condition(_lane_lock)
_gate_holders = 0
# (priority, lane) stack — release must recompute active from remaining holders
# or a finished high-priority lane leaves a stale _active_priority and starves peers.
_gate_holder_stack: List[Tuple[int, str]] = []


def etrade_batch_quote_size() -> int:
    try:
        val = int(os.getenv("ETRADE_BATCH_QUOTE_SIZE", str(ETRADE_BATCH_QUOTE_SIZE)))
    except (TypeError, ValueError):
        val = ETRADE_BATCH_QUOTE_SIZE
    return max(1, min(val, ETRADE_BATCH_QUOTE_SIZE))


def market_api_inter_request_sec() -> float:
    try:
        val = float(os.getenv("ETRADE_MARKET_API_INTER_REQUEST_SEC", str(_DEFAULT_INTER_REQUEST_SEC)))
    except (TypeError, ValueError):
        val = _DEFAULT_INTER_REQUEST_SEC
    return max(0.0, val)


def chunk_symbols(symbols: List[str], batch_size: int | None = None) -> List[List[str]]:
    size = batch_size if batch_size is not None else etrade_batch_quote_size()
    if size <= 0:
        size = ETRADE_BATCH_QUOTE_SIZE
    return [symbols[i : i + size] for i in range(0, len(symbols), size)]


def _lane_log_every() -> int:
    try:
        return max(0, int(os.getenv("0DTE_BROKER_LANE_COUNTER_LOG_EVERY", "50") or "50"))
    except (TypeError, ValueError):
        return 50


def lane_priority(lane: str) -> int:
    return int(_LANE_PRIORITY.get(str(lane or LANE_OTHER_MARKET), 9))


def _resolve_lane(lane: Optional[str], reason: str) -> str:
    if lane:
        key = str(lane).strip()
        return key if key in _lane_counts else LANE_OTHER_MARKET
    r = str(reason or "").lower()
    if "recovery" in r:
        return LANE_OWNED_RECOVERY
    if "position_sync" in r or "broker_sync" in r:
        return LANE_BROKER_POSITION_SYNC
    if "exact" in r or "occ" in r:
        return LANE_OPEN_EXACT_OCC
    if "trendline" in r and "build" in r:
        return LANE_TRENDLINE_BUILD
    if "trendline" in r:
        return LANE_TRENDLINE_WATCH_EQUITY
    if "breakout" in r:
        return LANE_BREAKOUT_WATCH_EQUITY
    if "pc_oi" in r or ("chain" in r and "po" not in r):
        return LANE_OPTION_CHAIN_PC_OI
    if "po" in r or "snapshot" in r:
        return LANE_PO_SNAPSHOT_CHAIN
    if "batch_quotes" in r or "equity" in r:
        return LANE_EQUITY_BATCH
    return LANE_OTHER_MARKET


def _trim_request_timestamps(now: float) -> None:
    cutoff = now - 3600.0
    while _request_timestamps and _request_timestamps[0] < cutoff:
        _request_timestamps.pop(0)


def record_broker_lane_request(lane: str, *, n: int = 1) -> None:
    """Increment a Market API lane counter and optionally emit a summary log."""
    global _lane_total, _lane_last_log_total
    key = str(lane or LANE_OTHER_MARKET).strip() or LANE_OTHER_MARKET
    if key not in _lane_counts:
        key = LANE_OTHER_MARKET
    add = max(0, int(n or 0))
    if add <= 0:
        return
    now = time.time()
    with _lane_lock:
        _lane_counts[key] = int(_lane_counts.get(key, 0) or 0) + add
        _lane_total += add
        for _ in range(add):
            _request_timestamps.append(now)
        _trim_request_timestamps(now)
        total = _lane_total
        every = _lane_log_every()
        should_log = every > 0 and (total - _lane_last_log_total) >= every
        snapshot = broker_lane_telemetry_snapshot_unlocked() if should_log else None
        if should_log:
            _lane_last_log_total = total
    if snapshot is not None:
        parts = " ".join(
            f"{k}={v}"
            for k, v in snapshot.get("counts", {}).items()
            if v and k != "total"
        )
        log.info(
            "BROKER_LANE_COUNTERS | total=%d | rps=%.2f | rph=%.0f | deferrals=%d | %s",
            int(snapshot.get("total") or 0),
            float(snapshot.get("requests_per_sec") or 0.0),
            float(snapshot.get("requests_per_hour") or 0.0),
            int(snapshot.get("deferrals_total") or 0),
            parts,
        )


def record_broker_lane_timing(
    lane: str,
    *,
    pacing_wait_ms: float = 0.0,
    queue_wait_ms: float = 0.0,
    latency_ms: float = 0.0,
) -> None:
    """Accumulate wait/latency samples for a lane."""
    key = str(lane or LANE_OTHER_MARKET).strip() or LANE_OTHER_MARKET
    if key not in _lane_counts:
        key = LANE_OTHER_MARKET
    with _lane_lock:
        if pacing_wait_ms > 0:
            _lane_pacing_wait_ms_sum[key] = float(_lane_pacing_wait_ms_sum.get(key, 0.0)) + float(
                pacing_wait_ms
            )
        if queue_wait_ms > 0:
            _lane_queue_wait_ms_sum[key] = float(_lane_queue_wait_ms_sum.get(key, 0.0)) + float(
                queue_wait_ms
            )
        if latency_ms > 0:
            _lane_latency_ms_sum[key] = float(_lane_latency_ms_sum.get(key, 0.0)) + float(latency_ms)
            _lane_latency_n[key] = int(_lane_latency_n.get(key, 0) or 0) + 1


def record_broker_lane_deferral(lane: str, *, n: int = 1, reason: str = "") -> None:
    """Count times a lower-priority lane yielded to open exact OCC."""
    key = str(lane or LANE_OTHER_MARKET).strip() or LANE_OTHER_MARKET
    if key not in _lane_counts:
        key = LANE_OTHER_MARKET
    add = max(0, int(n or 0))
    if add <= 0:
        return
    with _lane_lock:
        _lane_deferrals[key] = int(_lane_deferrals.get(key, 0) or 0) + add
    if reason:
        log.info(
            "BROKER_LANE_DEFERRAL | lane=%s | n=%d | reason=%s | active_lane=%s",
            key,
            add,
            reason,
            _active_lane or "",
        )


def broker_lane_counts_snapshot() -> Dict[str, int]:
    with _lane_lock:
        out = dict(_lane_counts)
        out["total"] = int(_lane_total)
        return out


def broker_lane_telemetry_snapshot_unlocked() -> Dict[str, object]:
    """Caller must hold ``_lane_lock``."""
    now = time.time()
    _trim_request_timestamps(now)
    recent_1s = sum(1 for t in _request_timestamps if t >= now - 1.0)
    recent_hr = len(_request_timestamps)
    counts = dict(_lane_counts)
    counts["total"] = int(_lane_total)
    latency_avg = {
        k: (
            round(float(_lane_latency_ms_sum.get(k, 0.0)) / n, 2)
            if (n := int(_lane_latency_n.get(k, 0) or 0)) > 0
            else 0.0
        )
        for k in _KNOWN_LANES
    }
    return {
        "counts": counts,
        "total": int(_lane_total),
        "pacing_wait_ms_sum": dict(_lane_pacing_wait_ms_sum),
        "queue_wait_ms_sum": dict(_lane_queue_wait_ms_sum),
        "latency_ms_avg": latency_avg,
        "deferrals": dict(_lane_deferrals),
        "deferrals_total": int(sum(_lane_deferrals.values())),
        "requests_per_sec": float(recent_1s),
        "requests_per_hour": float(recent_hr),
        "active_lane": _active_lane,
        "active_priority": int(_active_priority),
        "gate_holders": int(_gate_holders),
    }


def broker_lane_telemetry_snapshot() -> Dict[str, object]:
    with _lane_lock:
        return broker_lane_telemetry_snapshot_unlocked()


def market_lane_active() -> Optional[str]:
    with _lane_lock:
        return _active_lane


async def yield_lane_to_equity_batch(
    *,
    wait_start_s: float = 0.35,
    hold_s: float = 30.0,
) -> bool:
    """After releasing OCC, let a waiting equity_batch run to completion.

    Does not start a second HTTP while equity_batch holds. Returns True if
    equity_batch became active during the wait. Times out if no UL waiter
    acquires within ``wait_start_s``.
    """
    start_deadline = time.monotonic() + max(0.0, float(wait_start_s))
    got = False
    while time.monotonic() < start_deadline:
        if market_lane_active() == LANE_EQUITY_BATCH:
            got = True
            break
        await asyncio.sleep(0.02)
    if not got:
        return False
    hold_deadline = time.monotonic() + max(0.0, float(hold_s))
    while time.monotonic() < hold_deadline:
        if market_lane_active() != LANE_EQUITY_BATCH:
            return True
        await asyncio.sleep(0.05)
    return True


def _refresh_active_from_stack_unlocked() -> None:
    """Caller must hold ``_gate_cv`` / ``_lane_lock``."""
    global _active_lane, _active_priority, _gate_holders
    _gate_holders = len(_gate_holder_stack)
    if not _gate_holder_stack:
        _active_lane = None
        _active_priority = 99
        return
    best_pri = min(p for p, _ in _gate_holder_stack)
    best_lane = next(l for p, l in _gate_holder_stack if p == best_pri)
    _active_priority = int(best_pri)
    _active_lane = str(best_lane)


def reset_broker_lane_telemetry_for_tests() -> None:
    """Test-only reset of counters and gate state."""
    global _lane_total, _lane_last_log_total, _active_lane, _active_priority, _gate_holders
    with _lane_lock:
        for k in _KNOWN_LANES:
            _lane_counts[k] = 0
            _lane_latency_ms_sum[k] = 0.0
            _lane_latency_n[k] = 0
            _lane_pacing_wait_ms_sum[k] = 0.0
            _lane_queue_wait_ms_sum[k] = 0.0
            _lane_deferrals[k] = 0
        _lane_total = 0
        _lane_last_log_total = 0
        _request_timestamps.clear()
        _gate_holder_stack.clear()
        _active_lane = None
        _active_priority = 99
        _gate_holders = 0
        _gate_cv.notify_all()


def _acquire_gate(lane: str, *, timeout_sec: float = 30.0) -> Tuple[float, int]:
    """Block until this lane may run; return (queue_wait_ms, deferral_count)."""
    pri = lane_priority(lane)
    started = time.monotonic()
    deferrals = 0
    deadline = started + max(0.1, float(timeout_sec))
    with _gate_cv:
        while True:
            top_pri = min(p for p, _ in _gate_holder_stack) if _gate_holder_stack else 99
            if _gate_holder_stack and pri > top_pri:
                deferrals += 1
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                _gate_cv.wait(timeout=min(0.05, remaining))
                continue
            _gate_holder_stack.append((pri, lane))
            _refresh_active_from_stack_unlocked()
            queue_ms = max(0.0, (time.monotonic() - started) * 1000.0)
            return queue_ms, deferrals
    # Timeout fallback: still take the slot to avoid deadlock.
    with _gate_cv:
        _gate_holder_stack.append((pri, lane))
        _refresh_active_from_stack_unlocked()
        return max(0.0, (time.monotonic() - started) * 1000.0), deferrals


def _release_gate(lane: str) -> None:
    with _gate_cv:
        for i in range(len(_gate_holder_stack) - 1, -1, -1):
            if _gate_holder_stack[i][1] == lane:
                _gate_holder_stack.pop(i)
                break
        _refresh_active_from_stack_unlocked()
        _gate_cv.notify_all()


@contextmanager
def market_api_request_slot(
    *,
    reason: str = "market",
    lane: Optional[str] = None,
) -> Iterator[str]:
    """Sync context: priority gate + pacing sleep after the request body."""
    key = _resolve_lane(lane, reason)
    queue_ms, deferrals = _acquire_gate(key)
    if deferrals > 0:
        record_broker_lane_deferral(key, n=deferrals, reason="yielded_to_higher_priority")
    if queue_ms > 0:
        record_broker_lane_timing(key, queue_wait_ms=queue_ms)
    started = time.monotonic()
    try:
        yield key
    finally:
        latency_ms = max(0.0, (time.monotonic() - started) * 1000.0)
        record_broker_lane_timing(key, latency_ms=latency_ms)
        record_broker_lane_request(key)
        # Release before pace sleep so lower-priority lanes are not starved by throttle.
        _release_gate(key)
        delay = market_api_inter_request_sec()
        if delay > 0:
            pace_started = time.monotonic()
            time.sleep(delay)
            record_broker_lane_timing(
                key, pacing_wait_ms=max(0.0, (time.monotonic() - pace_started) * 1000.0)
            )


@asynccontextmanager
async def amarket_api_request_slot(
    *,
    reason: str = "market",
    lane: Optional[str] = None,
):
    """Async context: priority gate + pacing sleep after the request body."""
    key = _resolve_lane(lane, reason)
    queue_ms, deferrals = await asyncio.to_thread(_acquire_gate, key)
    if deferrals > 0:
        record_broker_lane_deferral(key, n=deferrals, reason="yielded_to_higher_priority")
    if queue_ms > 0:
        record_broker_lane_timing(key, queue_wait_ms=queue_ms)
    started = time.monotonic()
    try:
        yield key
    finally:
        latency_ms = max(0.0, (time.monotonic() - started) * 1000.0)
        record_broker_lane_timing(key, latency_ms=latency_ms)
        record_broker_lane_request(key)
        # Release before pace sleep so lower-priority lanes are not starved by throttle.
        await asyncio.to_thread(_release_gate, key)
        delay = market_api_inter_request_sec()
        if delay > 0:
            pace_started = time.monotonic()
            await asyncio.sleep(delay)
            record_broker_lane_timing(
                key, pacing_wait_ms=max(0.0, (time.monotonic() - pace_started) * 1000.0)
            )


def pace_market_api_after_request(*, reason: str = "market", lane: Optional[str] = None) -> None:
    """Legacy post-request pace (counts + sleep). Prefer ``market_api_request_slot``."""
    key = _resolve_lane(lane, reason)
    record_broker_lane_request(key)
    delay = market_api_inter_request_sec()
    if delay <= 0:
        return
    started = time.monotonic()
    time.sleep(delay)
    record_broker_lane_timing(
        key, pacing_wait_ms=max(0.0, (time.monotonic() - started) * 1000.0)
    )


async def apace_market_api_after_request(*, reason: str = "market", lane: Optional[str] = None) -> None:
    """Legacy async post-request pace. Prefer ``amarket_api_request_slot``."""
    key = _resolve_lane(lane, reason)
    record_broker_lane_request(key)
    delay = market_api_inter_request_sec()
    if delay <= 0:
        return
    started = time.monotonic()
    await asyncio.sleep(delay)
    record_broker_lane_timing(
        key, pacing_wait_ms=max(0.0, (time.monotonic() - started) * 1000.0)
    )
