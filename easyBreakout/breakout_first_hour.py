#!/usr/bin/env python3
"""ORB #2 helpers — thin wrappers around PrimeORBStrategyManager.orb2_data (ETrade only)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional, Tuple

# Labels kept for logs / metadata (canonical store is orb_strategy_manager).
# Window strings come from Shared.env §5 via session_clock (do not hardcode).
ORB1_LABEL = "ORB#1"
ORB2_LABEL = "ORB#2"


def _orb1_window_label() -> str:
    from modules.session_clock import orb1_capture_label

    return orb1_capture_label()


def _orb2_window_label() -> str:
    from modules.session_clock import orb2_capture_label

    return orb2_capture_label()


# Back-compat for log formatters / tests that import the names.
def __getattr__(name: str):
    if name == "ORB1_WINDOW":
        return _orb1_window_label()
    if name == "ORB2_WINDOW":
        return _orb2_window_label()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def orb2_hl_from_quote(quote: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    """Parse ETrade session high/low from one quote dict."""
    if not isinstance(quote, dict):
        return None
    try:
        hi = float(quote.get("high") or 0.0)
        lo = float(quote.get("low") or 0.0)
    except (TypeError, ValueError):
        return None
    if hi <= 0 or lo <= 0 or hi < lo:
        return None
    return hi, lo


def orb2_from_etrade_quote(
    symbol: str,
    quote: Dict[str, Any],
    *,
    capture_time: Optional[datetime] = None,
):
    """Build an ORBData-like row for tests / callers without a live manager.

    Production capture uses ``PrimeORBStrategyManager.ingest_session_ohlc_orb2``.
    """
    from modules.prime_orb_strategy_manager import ORBData, compute_orb_range_pct, PT_TZ

    hl = orb2_hl_from_quote(quote)
    if hl is None:
        return None
    hi, lo = hl
    try:
        op = float(quote.get("open") or 0.0)
    except (TypeError, ValueError):
        op = 0.0
    try:
        cl = float(quote.get("last") or quote.get("close") or quote.get("last_price") or 0.0)
    except (TypeError, ValueError):
        cl = 0.0
    try:
        vol = float(quote.get("volume") or 0.0)
    except (TypeError, ValueError):
        vol = 0.0
    now = capture_time or datetime.now(PT_TZ)
    pct = compute_orb_range_pct(hi, lo)
    row = ORBData(
        symbol=str(symbol or "").strip().upper(),
        orb_high=hi,
        orb_low=lo,
        orb_open=op,
        orb_close=cl,
        orb_volume=vol,
        orb_range=hi - lo,
        orb_range_pct=pct,
        orb_is_green=cl > op if op > 0 else True,
        capture_time=now,
        orb_label=ORB2_LABEL,
        window_pt=_orb2_window_label(),
        source="etrade_session_ohlc",
    )
    return row
