#!/usr/bin/env python3
"""
Executable bid/ask pricing for 0DTE options (CALL + PUT, single-leg + spreads).

Long option lifecycle (true-to-live):
  Enter  — Buy to Open  @ ask
  Exit   — Sell to Close @ bid

Short leg lifecycle (spread close):
  Exit   — Buy to Close @ ask

Used by mock fills, monitor marks, stealth PnL, and sizing so demo sim matches live broker economics.
"""

from __future__ import annotations

from typing import Any, Literal, Optional


def _positive_float(value: Any) -> Optional[float]:
    try:
        v = float(value)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def long_option_buy_to_open_price(
    *,
    bid: Optional[float] = None,
    ask: Optional[float] = None,
    mid: Optional[float] = None,
    last: Optional[float] = None,
) -> float:
    """BTO long call/put — pay the ask (fallback: mid → last → bid)."""
    a = _positive_float(ask)
    if a is not None:
        return a
    m = _positive_float(mid)
    if m is not None:
        return m
    l = _positive_float(last)
    if l is not None:
        return l
    b = _positive_float(bid)
    if b is not None:
        return b
    return 0.01


def long_option_sell_to_close_price(
    *,
    bid: Optional[float] = None,
    ask: Optional[float] = None,
    mid: Optional[float] = None,
    last: Optional[float] = None,
    bid_only: Optional[bool] = None,
) -> float:
    """STC long call/put — receive the bid.

    When synthetic exit quotes are prohibited (default), mid/last/ask fallbacks
    are disabled so fabricated prices cannot drive liquidation valuation.
    """
    b = _positive_float(bid)
    if b is not None:
        return b
    strict = bid_only
    if strict is None:
        try:
            from modules.dte0_exit_monitor_config import synthetic_exit_quotes_allowed

            strict = not synthetic_exit_quotes_allowed()
        except Exception:
            strict = True
    if strict:
        return 0.0
    m = _positive_float(mid)
    if m is not None:
        return m
    l = _positive_float(last)
    if l is not None:
        return l
    a = _positive_float(ask)
    if a is not None:
        return a
    return 0.01


def short_option_sell_to_open_price(
    *,
    bid: Optional[float] = None,
    ask: Optional[float] = None,
    mid: Optional[float] = None,
    last: Optional[float] = None,
) -> float:
    """STO short leg — receive the bid (fallback: mid → last → ask)."""
    return long_option_sell_to_close_price(bid=bid, ask=ask, mid=mid, last=last)


def short_option_buy_to_close_price(
    *,
    bid: Optional[float] = None,
    ask: Optional[float] = None,
    mid: Optional[float] = None,
    last: Optional[float] = None,
) -> float:
    """BTC short leg — pay the ask (fallback: mid → last → bid)."""
    return long_option_buy_to_open_price(bid=bid, ask=ask, mid=mid, last=last)


def spread_debit_executable_close_value(
    *,
    long_bid: Optional[float] = None,
    short_ask: Optional[float] = None,
) -> float:
    """Debit spread STC economics: sell long @ bid, buy short @ ask."""
    lb = _positive_float(long_bid)
    sa = _positive_float(short_ask)
    if lb is not None and sa is not None:
        return max(0.0, lb - sa)
    if lb is not None:
        return lb
    return 0.0


def spread_credit_executable_close_value(
    *,
    short_ask: Optional[float] = None,
    long_bid: Optional[float] = None,
) -> float:
    """Credit spread close cost (positive = debit to close)."""
    sa = _positive_float(short_ask)
    lb = _positive_float(long_bid)
    if sa is not None and lb is not None:
        return max(0.0, sa - lb)
    if sa is not None:
        return sa
    return 0.0


def spread_debit_executable_entry_cost(
    *,
    long_ask: Optional[float] = None,
    short_bid: Optional[float] = None,
) -> float:
    """Debit spread BTO economics: buy long @ ask, sell short @ bid."""
    la = _positive_float(long_ask)
    sb = _positive_float(short_bid)
    if la is not None and sb is not None:
        return max(0.01, la - sb)
    if la is not None:
        return la
    return 0.01


def leg_executable_mark_price(
    *,
    leg_role: str,
    bid: Optional[float] = None,
    ask: Optional[float] = None,
    mid: Optional[float] = None,
    last: Optional[float] = None,
    true_to_live: bool = True,
) -> float:
    """Per-leg mark for monitoring / close when true-to-live flags are on."""
    role = str(leg_role or "").lower()
    if true_to_live and true_to_live_executable_marks_enabled():
        if role == "long":
            return long_option_sell_to_close_price(bid=bid, ask=ask, mid=mid, last=last)
        if role == "short":
            return short_option_buy_to_close_price(bid=bid, ask=ask, mid=mid, last=last)
    m = _positive_float(mid)
    if m is not None:
        px = float(m)
        if role == "long":
            return px * 0.98
        if role == "short":
            return px * 1.02
        return px
    l = _positive_float(last)
    if l is not None:
        return float(l)
    b = _positive_float(bid)
    a = _positive_float(ask)
    if b is not None and a is not None and (b + a) > 0:
        px = (b + a) / 2.0
        if role == "long":
            return px * 0.98
        if role == "short":
            return px * 1.02
        return px
    return 0.0


def contract_executable_entry_premium(contract: Any) -> float:
    """Per-share BTO premium for sizing (ask-first)."""
    return from_option_contract(contract, side="entry")


def from_quote_dict(
    quote: Optional[dict],
    *,
    side: Literal["entry", "exit"],
) -> float:
    if not isinstance(quote, dict):
        return 0.01
    bid = _positive_float(quote.get("bid"))
    ask = _positive_float(quote.get("ask"))
    mid = _positive_float(quote.get("mid_price") or quote.get("mark") or quote.get("mid"))
    last = _positive_float(quote.get("last"))
    if side == "entry":
        return long_option_buy_to_open_price(bid=bid, ask=ask, mid=mid, last=last)
    return long_option_sell_to_close_price(bid=bid, ask=ask, mid=mid, last=last)


def from_option_contract(contract: Any, *, side: Literal["entry", "exit"]) -> float:
    bid = _positive_float(getattr(contract, "bid", None))
    ask = _positive_float(getattr(contract, "ask", None))
    mid = _positive_float(getattr(contract, "mid_price", None))
    if mid is None and bid is not None and ask is not None:
        mid = (bid + ask) / 2.0
    last = _positive_float(getattr(contract, "last", None))
    if side == "entry":
        return long_option_buy_to_open_price(bid=bid, ask=ask, mid=mid, last=last)
    return long_option_sell_to_close_price(bid=bid, ask=ask, mid=mid, last=last)


def true_to_live_executable_marks_enabled() -> bool:
    import os

    try:
        from modules.config_loader import get_config_value

        raw = get_config_value("OPTION_TRUE_TO_LIVE_EXECUTABLE_MARKS", None, convert_type=False)
        if raw is None or (isinstance(raw, str) and not str(raw).strip()):
            raw = get_config_value(
                "OPTION_STEALTH_0DTE_TRUE_TO_LIVE_EXECUTABLE_MARKS", True, convert_type=False
            )
    except ImportError:
        raw = os.getenv("OPTION_TRUE_TO_LIVE_EXECUTABLE_MARKS")
        if raw is None or not str(raw).strip():
            raw = os.getenv("OPTION_STEALTH_0DTE_TRUE_TO_LIVE_EXECUTABLE_MARKS", "true")
    if isinstance(raw, bool):
        return raw
    return str(raw or "true").strip().lower() in ("1", "true", "yes", "on")


def is_long_single_leg_option_side(option_side: str) -> bool:
    return str(option_side or "").strip().lower() in ("call", "put")
