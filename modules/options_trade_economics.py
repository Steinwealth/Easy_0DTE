#!/usr/bin/env python3
"""
Options trade economics — fees, cost basis, net P&L, break-even, close certification.

Demo: BTO @ ask, STC @ bid + modeled E*TRADE-style fees (conservative, repeatable).
Live: actual broker fill prices + modeled fees unless broker commission is present in raw.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, Literal, Optional, Tuple

log = logging.getLogger(__name__)

FillMode = Literal["simulated", "demo", "live"]
ExitPriceSource = Literal[
    "LIVE_BID",
    "LIVE_ASK",
    "MODELED",
    "SYNTHETIC",
    "LAST",
    "MID",
    "BID_EXECUTABLE",
    "ASK_EXECUTABLE",
    "UNKNOWN",
]


def _float_cfg(key: str, fallback: float) -> float:
    try:
        from modules.config_loader import get_config_value

        return float(get_config_value(key, str(fallback)))
    except Exception:
        try:
            return float(os.getenv(key, str(fallback)))
        except (TypeError, ValueError):
            return float(fallback)


def opening_fee_per_contract() -> float:
    """Commission + regulatory per contract on open (USD)."""
    commission = _float_cfg("OPTIONS_OPEN_COMMISSION_PER_CONTRACT", 0.65)
    regulatory = _float_cfg("OPTIONS_OPEN_REGULATORY_FEE_PER_CONTRACT", 0.0)
    return max(0.0, commission + regulatory)


def closing_fee_per_contract() -> float:
    """Commission + regulatory per contract on close (USD)."""
    commission = _float_cfg("OPTIONS_CLOSE_COMMISSION_PER_CONTRACT", 0.65)
    regulatory = _float_cfg("OPTIONS_CLOSE_REGULATORY_FEE_PER_CONTRACT", 0.0)
    return max(0.0, commission + regulatory)


def leg_count_for_position_type(position_type: str) -> int:
    """Single-leg = 1 fee unit; spreads = 1 contract unit per spread (not per leg)."""
    pt = str(position_type or "").strip().lower()
    if pt in ("debit_spread", "credit_spread"):
        return 1
    return 1


def compute_opening_fees(*, quantity: int, position_type: str = "lotto") -> float:
    qty = max(0, int(quantity or 0))
    units = leg_count_for_position_type(position_type)
    return round(opening_fee_per_contract() * qty * units, 2)


def compute_closing_fees(*, quantity: int, position_type: str = "lotto") -> float:
    qty = max(0, int(quantity or 0))
    units = leg_count_for_position_type(position_type)
    return round(closing_fee_per_contract() * qty * units, 2)


def spread_metrics(
    *,
    bid: Optional[float] = None,
    ask: Optional[float] = None,
    mid: Optional[float] = None,
) -> Dict[str, Optional[float]]:
    b = _pos(bid)
    a = _pos(ask)
    m = _pos(mid)
    if m is None and b is not None and a is not None:
        m = (b + a) / 2.0
    spread = None
    spread_pct = None
    if b is not None and a is not None and a >= b:
        spread = round(a - b, 6)
        if m and m > 0:
            spread_pct = round(spread / m, 6)
    return {
        "bid": b,
        "ask": a,
        "mid": m,
        "spread_paid": spread,
        "spread_pct": spread_pct,
    }


def _pos(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        v = float(value)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def breakeven_exit_premium_per_share(
    *,
    entry_fill: float,
    quantity: int,
    opening_fees: float,
    closing_fees: float,
) -> float:
    """
    Per-share bid required to net zero after fees (long single-leg / debit-style).
    bid * 100 * qty - close_fees = entry * 100 * qty + open_fees
    """
    qty = max(1, int(quantity or 1))
    mult = 100.0 * qty
    entry = max(0.0, float(entry_fill or 0.0))
    if mult <= 0:
        return entry
    return round((entry * mult + float(opening_fees or 0.0) + float(closing_fees or 0.0)) / mult, 6)


def build_entry_economics(
    *,
    fill_per_share: float,
    quantity: int,
    position_type: str = "lotto",
    quote_bid: Optional[float] = None,
    quote_ask: Optional[float] = None,
    quote_mid: Optional[float] = None,
    fill_mode: FillMode = "demo",
    execution_source: str = "mock_options_executor",
) -> Dict[str, Any]:
    qty = max(1, int(quantity or 1))
    fill = max(0.0, float(fill_per_share or 0.0))
    premium = round(fill * qty * 100.0, 2)
    open_fees = compute_opening_fees(quantity=qty, position_type=position_type)
    total_cost = round(premium + open_fees, 2)
    sm = spread_metrics(bid=quote_bid, ask=quote_ask, mid=quote_mid)
    close_fees_est = compute_closing_fees(quantity=qty, position_type=position_type)
    be = breakeven_exit_premium_per_share(
        entry_fill=fill,
        quantity=qty,
        opening_fees=open_fees,
        closing_fees=close_fees_est,
    )
    return {
        "fill": round(fill, 6),
        "contracts": qty,
        "premium": premium,
        "opening_fees": open_fees,
        "total_cost_basis": total_cost,
        "quote_bid": sm.get("bid"),
        "quote_ask": sm.get("ask"),
        "quote_mid": sm.get("mid"),
        "spread_paid": sm.get("spread_paid"),
        "entry_spread_pct": sm.get("spread_pct"),
        "breakeven_exit_premium": be,
        "fill_mode": fill_mode,
        "execution_source": execution_source,
    }


def build_exit_economics(
    *,
    exit_fill_per_share: float,
    quantity: int,
    position_type: str = "lotto",
    quote_bid: Optional[float] = None,
    quote_ask: Optional[float] = None,
    quote_mid: Optional[float] = None,
    exit_price_source: str = "BID_EXECUTABLE",
    closing_fees_override: Optional[float] = None,
) -> Dict[str, Any]:
    qty = max(1, int(quantity or 1))
    fill = max(0.0, float(exit_fill_per_share or 0.0))
    gross_premium = round(fill * qty * 100.0, 2)
    close_fees = (
        round(float(closing_fees_override), 2)
        if closing_fees_override is not None
        else compute_closing_fees(quantity=qty, position_type=position_type)
    )
    net_proceeds = round(gross_premium - close_fees, 2)
    sm = spread_metrics(bid=quote_bid, ask=quote_ask, mid=quote_mid)
    return {
        "fill": round(fill, 6),
        "contracts": qty,
        "gross_premium": gross_premium,
        "closing_fees": close_fees,
        "net_proceeds": net_proceeds,
        "quote_bid": sm.get("bid"),
        "quote_ask": sm.get("ask"),
        "quote_mid": sm.get("mid"),
        "spread_at_exit": sm.get("spread_paid"),
        "exit_spread_pct": sm.get("spread_pct"),
        "exit_price_source": str(exit_price_source or "UNKNOWN").upper(),
    }


def gross_fill_pnl_dollars(
    *,
    entry_fill: float,
    exit_fill: float,
    quantity: int,
    is_credit_spread: bool = False,
) -> float:
    """Option fill P&L in dollars (premium delta × contracts × 100). Excludes fees."""
    entry = float(entry_fill or 0.0)
    exit_px = float(exit_fill or 0.0)
    qty = max(1, int(quantity or 1))
    delta = (entry - exit_px) if is_credit_spread else (exit_px - entry)
    return round(delta * qty * 100.0, 2)


def gross_fill_pnl_fraction(
    *,
    entry_fill: float,
    exit_fill: float,
    is_credit_spread: bool = False,
) -> Optional[float]:
    entry = float(entry_fill or 0.0)
    if entry <= 1e-12:
        return None
    exit_px = float(exit_fill or 0.0)
    if is_credit_spread:
        return (entry - exit_px) / entry
    return (exit_px - entry) / entry


def resolve_true_to_live_exit_fill(
    *,
    exit_fill: float,
    metadata: Optional[Dict[str, Any]] = None,
    mode: str = "",
) -> float:
    """Demo STC = executable bid. Live = broker fill (passed exit_fill)."""
    stamped = float(exit_fill or 0.0)
    md = metadata if isinstance(metadata, dict) else {}
    mode_s = str(mode or md.get("runtime_mode") or "").strip().upper()
    if mode_s == "LIVE" or md.get("broker_fill_confirmed"):
        return stamped
    snap = md.get("premium_snapshot") if isinstance(md.get("premium_snapshot"), dict) else {}
    exact = md.get("exact_quote") if isinstance(md.get("exact_quote"), dict) else {}
    oq = md.get("option_quote") if isinstance(md.get("option_quote"), dict) else {}
    osnap = md.get("option_stealth") if isinstance(md.get("option_stealth"), dict) else {}
    for raw in (
        snap.get("executable_exit_price"),
        snap.get("current_bid"),
        snap.get("bid"),
        exact.get("bid"),
        oq.get("bid"),
        osnap.get("last_effective_premium"),
    ):
        try:
            px = float(raw or 0.0)
        except (TypeError, ValueError):
            px = 0.0
        if px > 0:
            return px
    return stamped


def net_result_pnl_dollars(
    *,
    entry_fill: float,
    exit_fill: float,
    quantity: int,
    is_credit_spread: bool = False,
    opening_fees: Optional[float] = None,
    closing_fees: Optional[float] = None,
    position_type: str = "lotto",
) -> float:
    """True-to-live bookkeeping: fill P&L minus open+close fees."""
    qty = max(1, int(quantity or 1))
    gross = gross_fill_pnl_dollars(
        entry_fill=entry_fill,
        exit_fill=exit_fill,
        quantity=qty,
        is_credit_spread=is_credit_spread,
    )
    open_f = (
        round(float(opening_fees), 2)
        if opening_fees is not None
        else compute_opening_fees(quantity=qty, position_type=position_type)
    )
    close_f = (
        round(float(closing_fees), 2)
        if closing_fees is not None
        else compute_closing_fees(quantity=qty, position_type=position_type)
    )
    return round(float(gross) - float(open_f) - float(close_f), 2)


def net_result_pnl_fraction(
    *,
    entry_fill: float,
    exit_fill: float,
    quantity: int,
    is_credit_spread: bool = False,
    opening_fees: Optional[float] = None,
    closing_fees: Optional[float] = None,
    position_type: str = "lotto",
    cost_basis: Optional[float] = None,
) -> Optional[float]:
    qty = max(1, int(quantity or 1))
    net = net_result_pnl_dollars(
        entry_fill=entry_fill,
        exit_fill=exit_fill,
        quantity=qty,
        is_credit_spread=is_credit_spread,
        opening_fees=opening_fees,
        closing_fees=closing_fees,
        position_type=position_type,
    )
    if cost_basis is not None:
        try:
            cost = float(cost_basis or 0.0)
        except (TypeError, ValueError):
            cost = 0.0
        if cost > 1e-12:
            return net / cost
    entry = float(entry_fill or 0.0)
    if entry <= 1e-12:
        return None
    open_f = (
        float(opening_fees)
        if opening_fees is not None
        else compute_opening_fees(quantity=qty, position_type=position_type)
    )
    premium = entry * qty * 100.0
    cost = premium + float(open_f or 0.0)
    if is_credit_spread:
        cost = max(premium, 1e-12)
    if cost <= 1e-12:
        return None
    return net / cost


def build_trade_economic_summary(
    *,
    entry: Dict[str, Any],
    exit_block: Dict[str, Any],
    is_credit_spread: bool = False,
) -> Dict[str, Any]:
    entry_cost = float(entry.get("total_cost_basis") or 0.0)
    net_proceeds = float(exit_block.get("net_proceeds") or 0.0)
    open_fees = float(entry.get("opening_fees") or 0.0)
    close_fees = float(exit_block.get("closing_fees") or 0.0)
    total_fees = round(open_fees + close_fees, 2)
    gross_pnl = round(net_proceeds + close_fees - (entry_cost - open_fees), 2)
    if is_credit_spread:
        gross_pnl = round((entry_cost - open_fees) - (net_proceeds + close_fees), 2)
    net_pnl = round(net_proceeds - entry_cost, 2) if not is_credit_spread else round(entry_cost - net_proceeds, 2)
    entry_fill = float(entry.get("fill") or 0.0)
    qty = int(entry.get("contracts") or 1)
    be = breakeven_exit_premium_per_share(
        entry_fill=entry_fill,
        quantity=qty,
        opening_fees=open_fees,
        closing_fees=close_fees,
    )
    entry_premium_only = float(entry.get("premium") or 0.0)
    exit_gross = float(exit_block.get("gross_premium") or 0.0)
    if entry_premium_only > 0:
        gross_pnl_premium = round(exit_gross - entry_premium_only, 2)
        if is_credit_spread:
            gross_pnl_premium = round(entry_premium_only - exit_gross, 2)
    else:
        gross_pnl_premium = gross_pnl
    pnl_pct = (net_pnl / entry_cost) if entry_cost > 0 else 0.0
    return {
        "entry": dict(entry),
        "exit": dict(exit_block),
        "economics": {
            "gross_pnl": gross_pnl_premium,
            "total_fees": total_fees,
            "opening_fees": open_fees,
            "closing_fees": close_fees,
            "net_pnl": net_pnl,
            "net_pnl_pct": round(pnl_pct, 6),
            "breakeven_exit_premium": be,
        },
    }


def resolve_fill_mode_from_metadata(meta: Optional[Dict[str, Any]]) -> FillMode:
    if not isinstance(meta, dict):
        return "demo"
    if meta.get("broker_fill_confirmed"):
        return "live"
    if meta.get("simulated_fill_confirmed") or meta.get("demo_ledger_close"):
        return "demo"
    fill_path = str(meta.get("fill_path") or "").lower()
    if "live" in fill_path or "broker" in fill_path:
        return "live"
    if "sim" in fill_path or "demo" in fill_path or "mock" in fill_path:
        return "demo"
    return "simulated"


def normalize_exit_price_source(raw: Optional[str]) -> str:
    token = str(raw or "").strip().upper().replace("-", "_")
    aliases = {
        "BID": "LIVE_BID",
        "BID_EXECUTABLE": "LIVE_BID",
        "ASK": "LIVE_ASK",
        "ASK_EXECUTABLE": "LIVE_ASK",
        "MID": "MID",
        "LAST": "LAST",
        "MODELED": "MODELED",
        "SYNTHETIC": "SYNTHETIC",
        "SYNTHETIC_MODELED": "SYNTHETIC",
        "MODELED_PROXY": "MODELED",
        "UNDERLYING_PROXY": "MODELED",
    }
    return aliases.get(token, token or "UNKNOWN")


def entry_fill_from_position(position: Any) -> float:
    px = float(getattr(position, "entry_price", 0.0) or 0.0)
    if px > 0:
        return px
    meta = getattr(position, "metadata", None) or {}
    if isinstance(meta, dict):
        te = meta.get("trade_economics_entry")
        if isinstance(te, dict):
            f = _pos(te.get("fill"))
            if f is not None:
                return f
        exec_pl = meta.get("execution_payload")
        if isinstance(exec_pl, dict):
            f = _pos(exec_pl.get("fill_price"))
            if f is not None:
                return f
    return px


def extract_entry_economics_from_metadata(meta: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(meta, dict):
        return None
    block = meta.get("trade_economics_entry")
    return dict(block) if isinstance(block, dict) else None


def emit_trade_close_certification(
    *,
    trade_id: str,
    symbol: str,
    strategy_type: str,
    economic_summary: Dict[str, Any],
    close_reason: str = "",
    fill_mode: str = "demo",
    execution_source: str = "",
) -> None:
    """One grep-friendly line: TRADE_CLOSE_CERTIFICATION + JSON payload."""
    try:
        entry = economic_summary.get("entry") or {}
        exit_block = economic_summary.get("exit") or {}
        econ = economic_summary.get("economics") or {}
        payload = {
            "trade_id": str(trade_id or ""),
            "symbol": str(symbol or "").upper(),
            "strategy_type": str(strategy_type or ""),
            "close_reason": str(close_reason or ""),
            "fill_mode": str(fill_mode or ""),
            "execution_source": str(execution_source or ""),
            "entry": {
                "fill": entry.get("fill"),
                "ask": entry.get("quote_ask"),
                "bid": entry.get("quote_bid"),
                "contracts": entry.get("contracts"),
                "premium": entry.get("premium"),
                "opening_fees": entry.get("opening_fees"),
                "total_cost_basis": entry.get("total_cost_basis"),
                "spread_paid": entry.get("spread_paid"),
                "entry_spread_pct": entry.get("entry_spread_pct"),
            },
            "exit": {
                "fill": exit_block.get("fill"),
                "bid": exit_block.get("quote_bid"),
                "ask": exit_block.get("quote_ask"),
                "gross_premium": exit_block.get("gross_premium"),
                "closing_fees": exit_block.get("closing_fees"),
                "net_proceeds": exit_block.get("net_proceeds"),
                "spread_at_exit": exit_block.get("spread_at_exit"),
                "exit_spread_pct": exit_block.get("exit_spread_pct"),
                "exit_price_source": exit_block.get("exit_price_source"),
            },
            "economics": {
                "gross_pnl": econ.get("gross_pnl"),
                "total_fees": econ.get("total_fees"),
                "opening_fees": econ.get("opening_fees"),
                "closing_fees": econ.get("closing_fees"),
                "net_pnl": econ.get("net_pnl"),
                "net_pnl_pct": econ.get("net_pnl_pct"),
                "breakeven_exit_premium": econ.get("breakeven_exit_premium"),
            },
        }
        log.info(
            "TRADE_CLOSE_CERTIFICATION | trade_id=%s | symbol=%s | strategy=%s | fill_mode=%s | "
            "entry_fill=%s | exit_fill=%s | total_cost=%s | net_proceeds=%s | total_fees=%s | "
            "net_pnl=%s | breakeven=%s | exit_source=%s | payload=%s",
            trade_id,
            symbol,
            strategy_type,
            fill_mode,
            entry.get("fill"),
            exit_block.get("fill"),
            entry.get("total_cost_basis"),
            exit_block.get("net_proceeds"),
            econ.get("total_fees"),
            econ.get("net_pnl"),
            econ.get("breakeven_exit_premium"),
            exit_block.get("exit_price_source"),
            json.dumps(payload, default=str),
        )
    except Exception as exc:
        log.warning("TRADE_CLOSE_CERTIFICATION | emit_failed | trade_id=%s | %s", trade_id, exc)


def finalize_close_economics(
    *,
    position: Any,
    exit_fill_per_share: float,
    quantity: int,
    position_type: str,
    close_reason: str = "",
    quote_bid: Optional[float] = None,
    quote_ask: Optional[float] = None,
    quote_mid: Optional[float] = None,
    exit_price_source: str = "BID_EXECUTABLE",
    closing_fees_override: Optional[float] = None,
    is_credit_spread: bool = False,
    execution_source: str = "",
) -> Tuple[Dict[str, Any], float]:
    """
    Build economic summary, attach to position metadata, return (summary, net_realized_pnl).
    """
    meta = dict(getattr(position, "metadata", None) or {})
    entry_block = extract_entry_economics_from_metadata(meta)
    entry_fill = entry_fill_from_position(position)
    qty = max(1, int(quantity or 1))
    if entry_block is None:
        fill_mode = resolve_fill_mode_from_metadata(meta)
        entry_block = build_entry_economics(
            fill_per_share=entry_fill,
            quantity=qty,
            position_type=position_type,
            quote_bid=quote_bid,
            quote_ask=quote_ask,
            quote_mid=quote_mid,
            fill_mode=fill_mode,
            execution_source=execution_source or str(meta.get("fill_path") or "unknown"),
        )
    exit_block = build_exit_economics(
        exit_fill_per_share=exit_fill_per_share,
        quantity=qty,
        position_type=position_type,
        quote_bid=quote_bid,
        quote_ask=quote_ask,
        quote_mid=quote_mid,
        exit_price_source=exit_price_source,
        closing_fees_override=closing_fees_override,
    )
    summary = build_trade_economic_summary(
        entry=entry_block,
        exit_block=exit_block,
        is_credit_spread=is_credit_spread,
    )
    meta["trade_economics_entry"] = entry_block
    meta["economic_summary"] = summary
    try:
        position.metadata = meta
    except Exception:
        pass
    net_pnl = float(summary.get("economics", {}).get("net_pnl") or 0.0)
    fill_mode = resolve_fill_mode_from_metadata(meta)
    emit_trade_close_certification(
        trade_id=str(getattr(position, "position_id", "") or ""),
        symbol=str(getattr(position, "symbol", "") or ""),
        strategy_type=str(getattr(position, "strategy_type", "") or ""),
        economic_summary=summary,
        close_reason=close_reason,
        fill_mode=fill_mode,
        execution_source=execution_source or str(meta.get("fill_path") or ""),
    )
    # Canonical closed-trade + profit retention (exactly-once)
    try:
        from modules.profit_retention_telemetry import (
            attach_profit_retention_to_position,
            compute_profit_retention,
            profit_retention_telemetry_enabled,
        )

        if profit_retention_telemetry_enabled():
            osnap = meta.get("option_stealth") if isinstance(meta.get("option_stealth"), dict) else {}
            latch = meta.get("exit_latch") if isinstance(meta.get("exit_latch"), dict) else {}
            entry_px = float(entry_block.get("fill") or 0.0)
            exit_px = float(exit_fill_per_share or 0.0)
            max_liq = osnap.get("max_pnl_pct")
            exit_liq = ((exit_px / entry_px) - 1.0) if entry_px > 0 else None
            # Stealth max_pnl_pct is a fraction (MSFT moon 6.189 = +618.9%).
            # Retention stores percent points. Always convert both — |x|<2 skipped
            # moons and mixed units (max 6.19 vs exit 575.68).
            try:
                if max_liq is not None:
                    max_liq = float(max_liq) * 100.0
                if exit_liq is not None:
                    exit_liq = float(exit_liq) * 100.0
            except (TypeError, ValueError):
                pass
            retention = compute_profit_retention(
                max_liquidatable_pnl_pct=max_liq,
                exit_liquidatable_pnl_pct=exit_liq,
                exit_reason=close_reason,
                mfe_armed=bool(osnap.get("mfe_giveback_armed") or osnap.get("mfe_armed")),
                mfe_triggered=str(close_reason or "").lower() in ("mfe_trail", "mfe_giveback"),
                ownership_gate_duration_ms=latch.get("sync_block_duration_ms"),
            )
            attach_profit_retention_to_position(position, retention)
            summary.setdefault("profit_retention", retention)
    except Exception as ret_err:
        log.debug("profit_retention_attach_failed | %s", ret_err)
    try:
        from modules.canonical_closed_trade import finalize_canonical_close_once

        is_partial = bool(meta.get("pending_close_partial") or meta.get("close_status") == "PARTIAL")
        ct, is_new = finalize_canonical_close_once(
            position=position,
            economic_summary=summary,
            exit_reason=close_reason,
            exit_fill_price=exit_fill_per_share,
            mode=fill_mode if fill_mode in ("demo", "live") else ("live" if fill_mode == "live" else "demo"),
            broker_order_id=str((meta.get("pending_close") or {}).get("broker_order_id") or ""),
            is_partial=is_partial,
        )
        if ct is not None:
            summary["canonical_closed_trade"] = ct.to_dict()
            summary["canonical_close_is_new"] = bool(is_new)
    except Exception as canon_err:
        log.warning("canonical_close_finalize_failed | %s", canon_err)
    return summary, net_pnl
