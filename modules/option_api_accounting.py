"""Real vs accounted Market API usage for the 0DTE monitor hard cap.

Only actual broker HTTP requests increment the cap. Cache hits, DEDUP reuse,
and monitor lookups are telemetry-only.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict

log = logging.getLogger(__name__)


def _bag(system: Any) -> Dict[str, Any]:
    bag = getattr(system, "_option_api_real_usage", None)
    if not isinstance(bag, dict):
        bag = {
            "window_start": time.time(),
            "broker_option_requests": 0,
            "broker_equity_requests": 0,
            "broker_chain_requests": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "monitor_quote_lookups": 0,
            "throttled_requests": 0,
            "real_broker_total": 0,
            "accounted_total": 0,
            "monitor_cycles": 0,
        }
        try:
            system._option_api_real_usage = bag
        except Exception:
            pass
    return bag


def record_monitor_lookup(system: Any, *, cache_hit: bool = False) -> None:
    if system is None:
        return
    bag = _bag(system)
    bag["monitor_quote_lookups"] = int(bag.get("monitor_quote_lookups") or 0) + 1
    if cache_hit:
        bag["cache_hits"] = int(bag.get("cache_hits") or 0) + 1
    else:
        bag["cache_misses"] = int(bag.get("cache_misses") or 0) + 1


def record_real_broker_request(
    system: Any,
    *,
    lane: str,
    count: int = 1,
    throttled: bool = False,
) -> None:
    """Count a real Market API HTTP request toward HARD_CAP."""
    if system is None or int(count or 0) <= 0:
        return
    n = int(count)
    bag = _bag(system)
    lane_l = str(lane or "").strip().lower()
    if "chain" in lane_l:
        bag["broker_chain_requests"] = int(bag.get("broker_chain_requests") or 0) + n
    elif "equity" in lane_l or lane_l in ("quote", "und", "underlying"):
        bag["broker_equity_requests"] = int(bag.get("broker_equity_requests") or 0) + n
    else:
        bag["broker_option_requests"] = int(bag.get("broker_option_requests") or 0) + n
    bag["real_broker_total"] = int(bag.get("real_broker_total") or 0) + n
    bag["accounted_total"] = int(bag.get("real_broker_total") or 0)
    if throttled:
        bag["throttled_requests"] = int(bag.get("throttled_requests") or 0) + n
    _maybe_roll_window(system, bag)


def record_monitor_cycle(system: Any) -> None:
    """Count one UNIFIED_0DTE_MONITOR tick toward cycles/min (telemetry only)."""
    if system is None:
        return
    bag = _bag(system)
    bag["monitor_cycles"] = int(bag.get("monitor_cycles") or 0) + 1
    _maybe_roll_window(system, bag)


def snapshot_usage(system: Any) -> Dict[str, int]:
    bag = _bag(system)
    real = int(bag.get("real_broker_total") or 0)
    return {
        "broker_option_requests": int(bag.get("broker_option_requests") or 0),
        "broker_equity_requests": int(bag.get("broker_equity_requests") or 0),
        "broker_chain_requests": int(bag.get("broker_chain_requests") or 0),
        "cache_hits": int(bag.get("cache_hits") or 0),
        "cache_misses": int(bag.get("cache_misses") or 0),
        "monitor_quote_lookups": int(bag.get("monitor_quote_lookups") or 0),
        "throttled_requests": int(bag.get("throttled_requests") or 0),
        "real_broker_total": real,
        "accounted_total": real,
        "monitor_cycles": int(bag.get("monitor_cycles") or 0),
    }


def _maybe_roll_window(system: Any, bag: Dict[str, Any]) -> None:
    now = time.time()
    start = float(bag.get("window_start") or now)
    if now - start < 60.0:
        return
    snap = snapshot_usage(system)
    log.info(
        "OPTION_API_USAGE | real_broker_total=%d | broker_option=%d | broker_equity=%d | "
        "broker_chain=%d | cache_hits=%d | cache_misses=%d | monitor_quote_lookups=%d | "
        "accounted_total=%d | throttled_requests=%d | monitor_cycles=%d",
        snap["real_broker_total"],
        snap["broker_option_requests"],
        snap["broker_equity_requests"],
        snap["broker_chain_requests"],
        snap["cache_hits"],
        snap["cache_misses"],
        snap["monitor_quote_lookups"],
        snap["accounted_total"],
        snap["throttled_requests"],
        snap.get("monitor_cycles") or 0,
    )
    hard_cap = int(getattr(system, "_option_api_hard_cap_per_min", 140) or 140)
    real = snap["real_broker_total"]
    if real > hard_cap:
        system._options_api_hard_cap_extra_sec = max(
            3.0,
            float(getattr(system, "_options_api_hard_cap_extra_sec", 0.0) or 0.0) + 3.0,
        )
        system._options_latency_symbol_backoff_level = max(
            1,
            int(getattr(system, "_options_latency_symbol_backoff_level", 0) or 0),
        )
        log.warning(
            "OPTION_API_HARD_CAP | calls_per_min=%d | action=throttle | basis=real_broker_total",
            real,
        )
    elif float(getattr(system, "_options_api_hard_cap_extra_sec", 0.0) or 0.0) > 0.0:
        system._options_api_hard_cap_extra_sec = max(
            0.0,
            float(getattr(system, "_options_api_hard_cap_extra_sec", 0.0) or 0.0) - 1.0,
        )
    # Oct 13 2026: feed the chain fetch manager's adaptive budget with this
    # minute's non-chain real calls (before the counters reset).
    try:
        mgr = getattr(system, "_chain_fetch_manager", None)
        if mgr is not None and hasattr(mgr, "observe_minute"):
            mgr.observe_minute(max(0, real - int(snap.get("broker_chain_requests") or 0)))
    except Exception:
        pass
    bag["window_start"] = now
    for k in (
        "broker_option_requests",
        "broker_equity_requests",
        "broker_chain_requests",
        "cache_hits",
        "cache_misses",
        "monitor_quote_lookups",
        "throttled_requests",
        "real_broker_total",
        "accounted_total",
        "monitor_cycles",
    ):
        bag[k] = 0
