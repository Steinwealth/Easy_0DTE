#!/usr/bin/env python3
"""
Breakout path adapter for unified session tech.

Math + bar hydrate live in ``modules.session_tech_indicators``. This module only
adds Breakout-local stamps (minutes from 7:30, velocity) on top of the shared
``get_session_tech`` stamp.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

log = logging.getLogger(__name__)

SESSION_TECH_SOURCE = "session_1m_rth"
QUOTE_TECH_SOURCE = "quote"
CAND_DEFAULT_SOURCE = "cand_default"


def minutes_from_730_pt(ts: Optional[datetime] = None) -> Optional[float]:
    """Minutes since 07:30 America/Los_Angeles (negative before open)."""
    try:
        from zoneinfo import ZoneInfo

        pt = ZoneInfo("America/Los_Angeles")
    except Exception:
        return None
    if ts is None:
        now = datetime.now(pt)
    else:
        if ts.tzinfo is None:
            now = ts.replace(tzinfo=timezone.utc).astimezone(pt)
        else:
            now = ts.astimezone(pt)
    open_730 = now.replace(hour=7, minute=30, second=0, microsecond=0)
    return float((now - open_730).total_seconds() / 60.0)


def velocity_pct_from_continuation(
    continuation_pct: Optional[float],
    *,
    minutes_elapsed: Optional[float] = None,
) -> Optional[float]:
    if continuation_pct is None:
        return None
    try:
        return float(continuation_pct)
    except (TypeError, ValueError):
        return None


def _tech_from_quote_and_cand(quote: Dict[str, Any], cand: Any) -> Dict[str, Any]:
    """
    Missing-tech marker only — not an alternate VWAP/vol method.

    Real stamps come solely from ``modules.session_tech_indicators``
    (broker quotes → minute bars → get_session_tech). Soft-defer uses these
    missing flags until the shared hub has bars.
    """
    _ = quote, cand  # retained for call-site compatibility
    return {
        "volume_ratio": None,
        "vwap": None,
        "vwap_distance_pct": None,
        "vwap_position": None,
        "rsi": None,
        "macd_histogram": None,
        "atr": None,
        "bollinger_upper": None,
        "bollinger_middle": None,
        "bollinger_lower": None,
        "bollinger_width": None,
        "bollinger_position": None,
        "volume_ratio_source": CAND_DEFAULT_SOURCE,
        "vwap_source": None,
        "tech_source": CAND_DEFAULT_SOURCE,
        "volume_ratio_missing": True,
        "vwap_missing": True,
        "rsi_missing": True,
        "macd_missing": True,
        "minutes_from_730": None,
        "minutes_from_730_to_break": None,
    }


def volume_ratio_is_stub(tech: Dict[str, Any]) -> bool:
    """True when volume_ratio is the placeholder default without a real source."""
    if bool(tech.get("volume_ratio_missing")):
        return True
    src = str(tech.get("volume_ratio_source") or tech.get("tech_source") or "").strip().lower()
    if src in (SESSION_TECH_SOURCE, QUOTE_TECH_SOURCE, "broker", "comprehensive"):
        return False
    try:
        vol = float(tech.get("volume_ratio") if tech.get("volume_ratio") is not None else 1.0)
    except (TypeError, ValueError):
        return True
    return abs(vol - 1.0) < 1e-9 and src in ("", CAND_DEFAULT_SOURCE, "unknown", "stub")


def vwap_is_missing(tech: Dict[str, Any]) -> bool:
    if bool(tech.get("vwap_missing")):
        return True
    vwap = tech.get("vwap")
    try:
        if vwap is None or float(vwap) <= 0:
            return True
    except (TypeError, ValueError):
        return True
    return False


async def hydrate_breakout_session_bars(
    system: Any,
    symbols: Sequence[str],
    *,
    quotes: Optional[Dict[str, Any]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Ensure today's 1m RTH bars via canonical volume_data / session-tech hub."""
    from modules.volume_data import hydrate_equity_session_tech

    return await hydrate_equity_session_tech(system, symbols, quotes=quotes)


def session_tech_for_symbol(
    system: Any,
    symbol: str,
    *,
    price: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    from modules.volume_data import request_equity_session_tech

    return request_equity_session_tech(system, symbol, price=price)


def enrich_breakout_tech(
    system: Any,
    cand: Any,
    quote: Dict[str, Any],
    *,
    price: float,
    continuation_pct: Optional[float] = None,
    break_ts: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Build decide/execute tech stamp from shared session hub + BO-local fields."""
    from modules.volume_data import apply_equity_session_tech, request_equity_session_tech

    # Same path as Trendline: hydrate first (caller), then read shared hub.
    # Do not invent VWAP=last here — that bypasses unified session tech.
    session = request_equity_session_tech(system, getattr(cand, "symbol", ""), price=price)
    if session:
        tech = dict(session)
        apply_equity_session_tech(cand, session)
    else:
        tech = _tech_from_quote_and_cand(quote if isinstance(quote, dict) else {}, cand)

    if tech.get("vwap") is not None and tech.get("vwap_distance_pct") is None and price > 0:
        try:
            vwap = float(tech["vwap"])
            if vwap > 0:
                tech["vwap_distance_pct"] = ((float(price) - vwap) / vwap) * 100.0
        except (TypeError, ValueError):
            pass

    mins = minutes_from_730_pt(
        break_ts or getattr(cand, "break_ts", None) or datetime.now(timezone.utc)
    )
    tech["minutes_from_730"] = mins
    tech["minutes_from_730_to_break"] = mins
    vel = velocity_pct_from_continuation(continuation_pct)
    if vel is None:
        try:
            bp = float(getattr(cand, "break_price", None) or 0.0)
            if bp > 0 and price > 0:
                vel = abs(float(price) - bp) / bp
        except (TypeError, ValueError):
            vel = None
    tech["velocity_pct"] = vel
    if vel is not None and mins is not None and mins > 0:
        tech["velocity_per_min_pct"] = float(vel) / float(mins)

    tech["vwap_position"] = tech.get("vwap_position") or _vwap_position_label(
        float(price), tech.get("vwap")
    )
    tech["volume_ratio_missing"] = volume_ratio_is_stub(tech)
    tech["vwap_missing"] = vwap_is_missing(tech)
    return tech


def _vwap_position_label(price: float, vwap: Optional[float]) -> str:
    if vwap is None:
        return "unknown"
    try:
        v = float(vwap)
    except (TypeError, ValueError):
        return "unknown"
    if v <= 0:
        return "unknown"
    if float(price) > v * 1.0005:
        return "above"
    if float(price) < v * 0.9995:
        return "below"
    return "at"


# Keys path-opt QG knives need on cand.metadata / GateContext extras.
SESSION_TECH_QG_KEYS: tuple = (
    "tech_source",
    "volume_ratio_source",
    "vwap_source",
    "vwap_position",
    "vwap_distance_pct",
    "volume_ratio",
    "rsi",
    "rsi_missing",
    "macd_histogram",
    "macd_missing",
    "volume_ratio_missing",
    "vwap_missing",
    "atr",
    "bollinger_upper",
    "bollinger_middle",
    "bollinger_lower",
    "bollinger_width",
    "bollinger_position",
    "minutes_from_730",
    "minutes_from_730_to_break",
    "velocity_pct",
    "session_bar_count",
    "ema_12",
    "ema_26",
)


def stamp_session_tech_qg_keys(
    meta: Dict[str, Any], tech: Dict[str, Any]
) -> Dict[str, Any]:
    """Copy QG-required session-tech fields onto candidate metadata (incl. False flags)."""
    out = meta if isinstance(meta, dict) else {}
    if not isinstance(tech, dict):
        return out
    for k in SESSION_TECH_QG_KEYS:
        if k not in tech:
            continue
        val = tech.get(k)
        # Keep explicit False / 0 — only skip absent keys.
        if val is None and k not in (
            "rsi_missing",
            "macd_missing",
            "volume_ratio_missing",
            "vwap_missing",
        ):
            continue
        out[k] = val
    return out
