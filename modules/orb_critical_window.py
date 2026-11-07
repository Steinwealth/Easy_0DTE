"""
ORB 7:30 critical execution window helpers (Jun 5 P0).

Priority window: 7:29:45–7:31:00 America/Los_Angeles for ORB SO + ORB1 Breakout submission.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, time
from typing import Optional

log = logging.getLogger(__name__)

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    from pytz import timezone as ZoneInfo  # type: ignore

PT = ZoneInfo("America/Los_Angeles")


def _parse_hhmmss(key: str, default: str) -> time:
    raw = os.getenv(key, default)
    try:
        parts = str(raw).strip().split(":")
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        s = int(parts[2]) if len(parts) > 2 else 0
        return time(h, m, s)
    except Exception:
        parts = default.split(":")
        return time(int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) > 2 else 0)


def is_orb_critical_execution_window(now_pt: Optional[datetime] = None) -> bool:
    """True during configured ORB SO / 0DTE execution priority window."""
    now = now_pt or datetime.now(PT)
    if now.tzinfo is None:
        now = now.replace(tzinfo=PT)
    else:
        now = now.astimezone(PT)
    start = _parse_hhmmss("ORB_CRITICAL_WINDOW_START_PT", "7:29:45")
    end = _parse_hhmmss("ORB_CRITICAL_WINDOW_END_PT", "7:31:00")
    t = now.time()
    return start <= t <= end


def max_chain_contracts_during_critical_window() -> int:
    try:
        return max(0, int(os.getenv("ORB_CRITICAL_WINDOW_MAX_CHAIN_CONTRACTS", "24")))
    except (TypeError, ValueError):
        return 24


def log_critical_window_telemetry(
    *,
    critical_window_active: bool,
    chain_scan_deferred: bool = False,
    chain_scan_contract_budget_used: int = 0,
    symbol: str = "",
    detail: str = "",
) -> None:
    tail = f" | symbol={symbol}" if symbol else ""
    if detail:
        tail = f"{tail} | detail={detail}"
    log.info(
        "ORB_CRITICAL_WINDOW | critical_window_active=%s | chain_scan_deferred=%s | "
        "chain_scan_contract_budget_used=%d%s",
        str(critical_window_active).lower(),
        str(chain_scan_deferred).lower(),
        int(chain_scan_contract_budget_used),
        tail,
    )
