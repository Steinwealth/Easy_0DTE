#!/usr/bin/env python3
"""
Structured execution telemetry (ORB SO, ORB1 Breakout, Trendline 0DTE).

Single-line INFO logs with grep-friendly tokens; payload is JSON for downstream calibration.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

# Same logger name as prime_trading_system pipeline logs (Cloud Run grep parity with entries).
_option_lifecycle_log = logging.getLogger("prime_trading_system_optimized")


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_execution_payload(
    *,
    symbol: str,
    trade_id: str = "",
    strategy: str = "",
    signal_ts: Optional[str] = None,
    submit_ts: Optional[str] = None,
    fill_ts: Optional[str] = None,
    order_type: str = "",
    reprice_count: int = 0,
    quote_bid: Optional[float] = None,
    quote_ask: Optional[float] = None,
    quote_mid: Optional[float] = None,
    submitted_limit: Optional[float] = None,
    fill_price: Optional[float] = None,
    slippage_vs_mid: Optional[float] = None,
    spread_width_pct: Optional[float] = None,
    exit_reason: str = "",
    exit_urgency: str = "",
    exit_execution_style: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    lat_ms: Optional[float] = None
    if signal_ts and fill_ts:
        try:
            t0 = datetime.fromisoformat(signal_ts.replace("Z", "+00:00"))
            t1 = datetime.fromisoformat(fill_ts.replace("Z", "+00:00"))
            lat_ms = max(0.0, (t1 - t0).total_seconds() * 1000.0)
        except Exception:
            lat_ms = None
    if lat_ms is None and signal_ts:
        try:
            t0 = datetime.fromisoformat(signal_ts.replace("Z", "+00:00"))
            t1 = datetime.now(timezone.utc)
            lat_ms = max(0.0, (t1 - t0).total_seconds() * 1000.0)
        except Exception:
            lat_ms = None

    out: Dict[str, Any] = {
        "symbol": symbol,
        "trade_id": trade_id or "",
        "strategy": strategy or "",
        "signal_ts": signal_ts or "",
        "submit_ts": submit_ts or "",
        "fill_ts": fill_ts or "",
        "latency_ms": round(lat_ms, 1) if isinstance(lat_ms, (int, float)) else None,
        "order_type": order_type or "",
        "reprice_count": int(reprice_count or 0),
        "quote_bid": quote_bid,
        "quote_ask": quote_ask,
        "quote_mid": quote_mid,
        "submitted_limit": submitted_limit,
        "fill_price": fill_price,
        "slippage_vs_mid": slippage_vs_mid,
        "spread_width_pct": spread_width_pct,
        "exit_reason": exit_reason or "",
        "exit_urgency": exit_urgency or "",
        "exit_execution_style": exit_execution_style or "",
    }
    if extra:
        out["extra"] = extra
    return out


def log_option_exit_diagnosis(
    *,
    stage: str,
    strategy: str,
    trade_id: str = "",
    symbol: str = "",
    reason: str = "",
    pnl_pct: Optional[float] = None,
    pnl_dollar: Optional[float] = None,
    exit_premium: Optional[float] = None,
    underlying: Optional[float] = None,
    trigger: str = "",
    alert_sent: Optional[bool] = None,
    close_ok: Optional[bool] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """
    One grep token for full options exit lifecycle (stealth → executor → Telegram).

    Cloud Logging: textPayload:"OPTION_EXIT_DIAGNOSIS" AND textPayload:"trade_id=TL_..."
  """
    parts = [
        "OPTION_EXIT_DIAGNOSIS",
        f"stage={str(stage or 'unknown').strip()}",
        f"strategy={str(strategy or 'unknown').strip()}",
        f"trade_id={str(trade_id or '').strip()}",
        f"symbol={str(symbol or '').strip()}",
        f"reason={str(reason or '').strip()}",
    ]
    if trigger:
        parts.append(f"trigger={str(trigger).strip()}")
    if pnl_pct is not None:
        parts.append(f"pnl_pct={float(pnl_pct):.6f}")
    if pnl_dollar is not None:
        parts.append(f"pnl_dollar={float(pnl_dollar):.2f}")
    if exit_premium is not None:
        parts.append(f"exit_premium={float(exit_premium):.6f}")
    if underlying is not None:
        parts.append(f"underlying={float(underlying):.4f}")
    if alert_sent is not None:
        parts.append(f"alert_sent={str(bool(alert_sent)).lower()}")
    if close_ok is not None:
        parts.append(f"close_ok={str(bool(close_ok)).lower()}")
    if extra:
        try:
            parts.append(f"extra={json.dumps(extra, default=str, separators=(',', ':'))}")
        except Exception:
            parts.append("extra={\"error\":\"serialization_failed\"}")
    _option_lifecycle_log.info(" | ".join(parts))


def log_execution_event(event: str, strategy: str, payload: Dict[str, Any]) -> None:
    """Emit one structured INFO line: EXECUTION_<EVENT> | strategy=... | {...}"""
    token = str(event or "").strip().upper()
    if not token.startswith("EXECUTION_"):
        token = f"EXECUTION_{token}"
    strat = str(strategy or "unknown")
    try:
        body = json.dumps(payload, default=str, separators=(",", ":"))
    except Exception:
        body = json.dumps({"error": "serialization_failed", "symbol": payload.get("symbol")})
    log.info("%s | strategy=%s | %s", token, strat, body)


def slippage_vs_mid(side: str, mid: Optional[float], fill: Optional[float]) -> Optional[float]:
    if mid is None or fill is None or mid <= 0:
        return None
    s = str(side or "").upper()
    if s == "BUY":
        return round((float(fill) - float(mid)) / float(mid) * 10000.0, 2)  # bps
    if s == "SELL":
        return round((float(mid) - float(fill)) / float(mid) * 10000.0, 2)
    return None


def _optional_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def entry_spread_pct(
    bid: Optional[float],
    ask: Optional[float],
    mid: Optional[float] = None,
) -> Optional[float]:
    bid_f = _optional_float(bid)
    ask_f = _optional_float(ask)
    if bid_f is None or ask_f is None:
        return None
    mid_f = _optional_float(mid)
    if mid_f is None or mid_f <= 0:
        mid_f = (bid_f + ask_f) / 2.0 if (bid_f + ask_f) > 0 else None
    if mid_f is None or mid_f <= 0:
        return None
    return max(0.0, ask_f - bid_f) / mid_f


def entry_slippage_vs_mid_pct(
    side: str,
    mid: Optional[float],
    fill: Optional[float],
) -> Optional[float]:
    """Fractional slippage vs mid (matches profit_optimizer entry_quality)."""
    mid_f = _optional_float(mid)
    fill_f = _optional_float(fill)
    if mid_f is None or fill_f is None or mid_f <= 0:
        return None
    s = str(side or "BUY").upper()
    if s in ("SELL", "SHORT"):
        return (mid_f - fill_f) / mid_f
    return (fill_f - mid_f) / mid_f


def exit_slippage_vs_bid_pct(
    bid: Optional[float],
    fill: Optional[float],
) -> Optional[float]:
    """Fractional STC slippage vs bid (positive = received less than bid)."""
    bid_f = _optional_float(bid)
    fill_f = _optional_float(fill)
    if bid_f is None or fill_f is None or bid_f <= 0:
        return None
    return (bid_f - fill_f) / bid_f


def exit_slippage_vs_ask_pct(
    ask: Optional[float],
    fill: Optional[float],
) -> Optional[float]:
    """Fractional BTC slippage vs ask (positive = paid more than ask)."""
    ask_f = _optional_float(ask)
    fill_f = _optional_float(fill)
    if ask_f is None or fill_f is None or ask_f <= 0:
        return None
    return (fill_f - ask_f) / ask_f


def resolve_exit_max_slippage_vs_bid_pct(strategy_type: str) -> float:
    """Strategy-specific STC slippage cap vs bid (fraction: 0.08 = 8%)."""
    st = str(strategy_type or "").strip().lower()
    try:
        from modules.config_loader import get_config_value
    except ImportError:
        get_config_value = None  # type: ignore
    if "trendline" in st:
        default = "0.30"
        key = "TRENDLINE_0DTE_EXIT_MAX_SLIPPAGE_VS_BID_PCT"
    else:
        default = "0.08"
        key = "ORB_0DTE_EXIT_MAX_SLIPPAGE_VS_BID_PCT"
    try:
        if get_config_value is not None:
            return max(0.0, float(get_config_value(key, default)))
    except (TypeError, ValueError):
        pass
    return float(default)


def build_orb_entry_execution_payload(
    *,
    quote_bid: Optional[float] = None,
    quote_ask: Optional[float] = None,
    quote_mid: Optional[float] = None,
    fill_price: Optional[float] = None,
    delta_at_entry: Optional[float] = None,
    submit_ts: Optional[str] = None,
    fill_ts: Optional[str] = None,
    submitted_limit: Optional[float] = None,
    side: str = "BUY",
) -> Dict[str, Any]:
    bid = _optional_float(quote_bid)
    ask = _optional_float(quote_ask)
    mid = _optional_float(quote_mid)
    if mid is None and bid is not None and ask is not None and (bid + ask) > 0:
        mid = (bid + ask) / 2.0
    fill = _optional_float(fill_price)
    spread_pct = entry_spread_pct(bid, ask, mid)
    slip_pct = entry_slippage_vs_mid_pct(side, mid, fill)
    slip_bid = entry_slippage_vs_mid_pct("BUY", bid, fill) if bid is not None else None
    return {
        "quote_bid": bid,
        "quote_ask": ask,
        "quote_mid": mid,
        "fill_price": fill,
        "delta_at_entry": _optional_float(delta_at_entry),
        "submit_ts": str(submit_ts or ""),
        "fill_ts": str(fill_ts or ""),
        "submitted_limit": _optional_float(submitted_limit),
        "spread_width_pct": spread_pct,
        "slippage_vs_mid": slip_pct,
        "slippage_vs_mid_pct": slip_pct,
        "slippage_vs_ask_pct": slip_bid,
    }


def emit_orb_entry_fill_summary(
    *,
    trade_id: str,
    symbol: str,
    option_symbol: str = "",
    strategy_type: str = "ORB_0DTE",
    quote_bid: Optional[float] = None,
    quote_ask: Optional[float] = None,
    quote_mid: Optional[float] = None,
    fill_price: Optional[float] = None,
    delta_at_entry: Optional[float] = None,
    submit_ts: Optional[str] = None,
    fill_ts: Optional[str] = None,
    submitted_limit: Optional[float] = None,
    side: str = "BUY",
    order_type: str = "LIMIT",
) -> None:
    """Log one EXECUTION_FILL_SUMMARY for ORB entry; never raises."""
    try:
        bid = _optional_float(quote_bid)
        ask = _optional_float(quote_ask)
        mid = _optional_float(quote_mid)
        if mid is None and bid is not None and ask is not None and (bid + ask) > 0:
            mid = (bid + ask) / 2.0
        fill = _optional_float(fill_price)
        spread_pct = entry_spread_pct(bid, ask, mid)
        slip_pct = entry_slippage_vs_mid_pct(side, mid, fill)
        payload = build_execution_payload(
            symbol=str(symbol or ""),
            trade_id=str(trade_id or ""),
            strategy=str(strategy_type or "ORB_0DTE"),
            submit_ts=submit_ts,
            fill_ts=fill_ts or _iso_now(),
            order_type=str(order_type or "LIMIT"),
            quote_bid=bid,
            quote_ask=ask,
            quote_mid=mid,
            submitted_limit=_optional_float(submitted_limit),
            fill_price=fill,
            slippage_vs_mid=slip_pct,
            spread_width_pct=spread_pct,
            extra={
                "path": "orb_single_leg_entry",
                "option_symbol": str(option_symbol or ""),
                "delta_at_entry": _optional_float(delta_at_entry),
                "spread_pct": spread_pct,
                "slippage_vs_mid_pct": slip_pct,
            },
        )
        log_execution_event("EXECUTION_FILL_SUMMARY", str(strategy_type or "ORB_0DTE"), payload)
    except Exception as exc:
        log.debug("emit_orb_entry_fill_summary skipped | trade_id=%s | %s", trade_id, exc)


def attach_orb_entry_execution_telemetry(
    metadata: Dict[str, Any],
    *,
    contract: Any = None,
    quote_bid: Optional[float] = None,
    quote_ask: Optional[float] = None,
    quote_mid: Optional[float] = None,
    fill_price: Optional[float] = None,
    delta_at_entry: Optional[float] = None,
    submit_ts: Optional[str] = None,
    fill_ts: Optional[str] = None,
    submitted_limit: Optional[float] = None,
    trade_id: str = "",
    symbol: str = "",
    option_symbol: str = "",
    strategy_type: str = "ORB_0DTE",
    side: str = "BUY",
    order_type: str = "LIMIT",
    emit_summary: bool = True,
) -> Dict[str, Any]:
    """Persist ORB entry execution_payload on position metadata; optional fill summary log."""
    if contract is not None:
        quote_bid = quote_bid if quote_bid is not None else getattr(contract, "bid", None)
        quote_ask = quote_ask if quote_ask is not None else getattr(contract, "ask", None)
        quote_mid = quote_mid if quote_mid is not None else getattr(contract, "mid_price", None)
        fill_price = fill_price if fill_price is not None else quote_mid
        delta_at_entry = (
            delta_at_entry
            if delta_at_entry is not None
            else getattr(contract, "delta", None)
        )
        symbol = symbol or str(getattr(contract, "symbol", "") or "")
        if not option_symbol:
            exp = str(getattr(contract, "expiry", "") or "").replace("-", "")
            opt = str(getattr(contract, "option_type", "") or "").lower()
            strike = getattr(contract, "strike", None)
            if symbol and strike is not None and opt:
                option_symbol = (
                    f"{symbol}_{exp}_{int(float(strike) * 1000):08d}{opt[0]}"
                    if exp
                    else f"{symbol}_{strike}_{opt}"
                )
    payload = build_orb_entry_execution_payload(
        quote_bid=quote_bid,
        quote_ask=quote_ask,
        quote_mid=quote_mid,
        fill_price=fill_price,
        delta_at_entry=delta_at_entry,
        submit_ts=submit_ts,
        fill_ts=fill_ts or _iso_now(),
        submitted_limit=submitted_limit,
        side=side,
    )
    metadata["execution_payload"] = payload
    if emit_summary:
        emit_orb_entry_fill_summary(
            trade_id=trade_id,
            symbol=symbol,
            option_symbol=option_symbol,
            strategy_type=strategy_type,
            quote_bid=quote_bid,
            quote_ask=quote_ask,
            quote_mid=quote_mid,
            fill_price=fill_price,
            delta_at_entry=delta_at_entry,
            submit_ts=submit_ts,
            fill_ts=fill_ts or payload.get("fill_ts"),
            submitted_limit=submitted_limit,
            side=side,
            order_type=order_type,
        )
    return payload


def monotonic_ms() -> float:
    return time.perf_counter() * 1000.0
