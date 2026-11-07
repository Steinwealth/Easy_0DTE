#!/usr/bin/env python3
"""Entry execution quality metrics (record-only, no execution influence)."""

from __future__ import annotations

from typing import Any, Dict, Optional


def _f(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def build_entry_execution_quality(
    *,
    entry_bid: Optional[float] = None,
    entry_ask: Optional[float] = None,
    entry_mid: Optional[float] = None,
    entry_mark: Optional[float] = None,
    fill_price: Optional[float] = None,
    entry_quote_age_seconds: Optional[float] = None,
    entry_mark_quality: str = "",
    entry_premium_source: str = "",
    entry_is_exit_grade: Optional[bool] = None,
    is_long_premium: bool = True,
) -> Dict[str, Any]:
    bid = _f(entry_bid)
    ask = _f(entry_ask)
    mid = _f(entry_mid)
    mark = _f(entry_mark)
    fill = _f(fill_price)
    if mid is None and bid is not None and ask is not None and (bid + ask) > 0:
        mid = (bid + ask) / 2.0
    if fill is None:
        fill = mark or mid

    spread_w = None
    spread_pct = None
    if bid is not None and ask is not None:
        spread_w = max(0.0, ask - bid)
        if mid and mid > 0:
            spread_pct = spread_w / mid

    slip_mid = None
    slip_bid = None
    if fill is not None and fill > 0:
        if mid and mid > 0:
            slip_mid = (fill - mid) / mid if is_long_premium else (mid - fill) / mid
        if bid and bid > 0:
            slip_bid = (fill - bid) / bid if is_long_premium else (bid - fill) / bid

    return {
        "entry_bid": bid,
        "entry_ask": ask,
        "entry_mid": mid,
        "entry_mark": mark,
        "fill_price": fill,
        "entry_spread_width": spread_w,
        "entry_spread_pct": spread_pct,
        "entry_slippage_vs_mid_pct": slip_mid,
        "entry_slippage_vs_bid_pct": slip_bid,
        "entry_quote_age_seconds": _f(entry_quote_age_seconds),
        "entry_mark_quality": str(entry_mark_quality or ""),
        "entry_premium_source": str(entry_premium_source or ""),
        "entry_is_exit_grade": bool(entry_is_exit_grade) if entry_is_exit_grade is not None else None,
    }
