#!/usr/bin/env python3
"""BROKER_MARKET_HOURS_AUDIT — E*TRADE + internal market manager read-only checks."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")


def _walk_times(obj: Any, found: List[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = str(k).lower()
            if any(t in key for t in ("time", "hour", "open", "close", "start", "end")):
                if v is not None and str(v).strip():
                    found.append(f"{k}={v}")
            _walk_times(v, found)
    elif isinstance(obj, list):
        for item in obj:
            _walk_times(item, found)


def _extract_session_block(response: Any) -> Dict[str, Any]:
    """Best-effort parse of E*TRADE /v1/market/hours response."""
    out: Dict[str, Any] = {
        "market_open": None,
        "market_close": None,
        "option_trading_open": None,
        "option_trading_close": None,
        "early_close": False,
        "raw_keys": [],
    }
    if not response:
        return out

    if isinstance(response, dict):
        out["raw_keys"] = sorted(response.keys())
        block = response.get("MarketHoursResponse") or response
        if isinstance(block, dict):
            for product_key in ("Equity", "EQ", "OPTION", "OPTN", "Product"):
                product = block.get(product_key)
                if isinstance(product, dict):
                    for session_key in ("Open", "open", "Regular", "regular"):
                        session = product.get(session_key)
                        if isinstance(session, dict):
                            start = session.get("start") or session.get("open") or session.get("openTime")
                            end = session.get("end") or session.get("close") or session.get("closeTime")
                            if start and out["market_open"] is None:
                                out["market_open"] = str(start)
                            if end and out["market_close"] is None:
                                out["market_close"] = str(end)
                    if product_key in ("OPTION", "OPTN") and out["option_trading_open"] is None:
                        out["option_trading_open"] = out["market_open"]
                        out["option_trading_close"] = out["market_close"]

        times: List[str] = []
        _walk_times(response, times)
        if times and out["market_open"] is None:
            out["market_open"] = times[0]
        if len(times) > 1 and out["market_close"] is None:
            out["market_close"] = times[1]

    return out


async def run_broker_market_hours_audit(
    *,
    etrade_trading: Any = None,
    market_manager: Any = None,
) -> Dict[str, Any]:
    """
    Verify runtime can determine market open/close, option window, and early close.
    Read-only — no trading actions.
    """
    etrade_block: Dict[str, Any] = {}
    if etrade_trading is not None and hasattr(etrade_trading, "get_market_hours"):
        try:
            raw = await asyncio.to_thread(etrade_trading.get_market_hours)
            etrade_block = _extract_session_block(raw)
            etrade_block["etrade_api_ok"] = bool(raw)
        except Exception as exc:
            etrade_block = {"etrade_api_ok": False, "error": str(exc)}

    mgr_block: Dict[str, Any] = {}
    if market_manager is not None:
        try:
            info = (
                market_manager.get_market_hours_info()
                if hasattr(market_manager, "get_market_hours_info")
                else {}
            )
            mgr_block = {
                "is_market_open": info.get("is_market_open"),
                "is_early_close_day": info.get("is_early_close_day"),
                "early_close_time": info.get("early_close_time"),
                "next_market_open": info.get("next_market_open"),
                "next_market_close": info.get("next_market_close"),
                "market_status": info.get("market_status"),
            }
        except Exception as exc:
            mgr_block = {"error": str(exc)}

    # Standard US options regular session fallback (ET)
    default_open = "09:30"
    default_close = "16:00"
    market_open = etrade_block.get("market_open") or default_open
    market_close = etrade_block.get("market_close") or default_close
    option_open = etrade_block.get("option_trading_open") or market_open
    option_close = etrade_block.get("option_trading_close") or market_close
    early_close = bool(mgr_block.get("is_early_close_day"))
    if mgr_block.get("early_close_time"):
        option_close = str(mgr_block.get("early_close_time"))
        early_close = True

    can_determine = bool(market_open and market_close and option_open and option_close)
    now_et = datetime.now(_ET).isoformat()

    audit = {
        "market_open": market_open,
        "market_close": market_close,
        "option_trading_window": f"{option_open}-{option_close} ET",
        "option_trading_open": option_open,
        "option_trading_close": option_close,
        "early_close": early_close,
        "can_determine_hours": can_determine,
        "checked_at_et": now_et,
        "etrade": etrade_block,
        "market_manager": mgr_block,
    }
    log.info(
        "BROKER_MARKET_HOURS_AUDIT | market_open=%s | market_close=%s | "
        "option_trading_window=%s | early_close=%s | can_determine=%s",
        market_open,
        market_close,
        audit["option_trading_window"],
        str(early_close).lower(),
        str(can_determine).lower(),
    )
    return audit
