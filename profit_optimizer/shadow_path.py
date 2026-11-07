#!/usr/bin/env python3
"""Post-exit shadow premium path (read-only) until session EOD."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

STRATEGY_ORB_0DTE = "orb_0dte"
STRATEGY_TRENDLINE_0DTE = "trendline_0dte"


def shadow_path_after_exit_enabled() -> bool:
    try:
        from modules.config_loader import get_config_value

        raw = get_config_value("PROFIT_OPTIMIZER_SHADOW_PATH_AFTER_EXIT", "true")
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    except Exception:
        return True


def contract_legs_for_quote(entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    contract = entry.get("contract_identity") if isinstance(entry.get("contract_identity"), dict) else {}
    legs = contract.get("legs")
    if isinstance(legs, list) and legs:
        out: List[Dict[str, Any]] = []
        for leg in legs:
            if not isinstance(leg, dict):
                continue
            opt_type = str(
                leg.get("option_type")
                or leg.get("side")
                or contract.get("option_side")
                or contract.get("option_type")
                or "call"
            ).lower()
            out.append(
                {
                    "strike": leg.get("strike"),
                    "option_type": opt_type,
                    "long_or_short": str(leg.get("long_or_short") or "long").lower(),
                    "leg_id": str(leg.get("option_symbol") or leg.get("leg_id") or contract.get("option_symbol") or ""),
                    "option_symbol": str(leg.get("option_symbol") or leg.get("leg_id") or ""),
                    "contract_id": str(leg.get("contract_id") or ""),
                    "expiration": str(leg.get("expiration") or contract.get("expiration") or ""),
                }
            )
        return out
    strike = contract.get("strike") or entry.get("strike")
    side = str(contract.get("option_side") or contract.get("option_type") or "call").lower()
    if strike is None:
        return []
    return [
        {
            "strike": strike,
            "option_type": side,
            "long_or_short": "long",
            "leg_id": str(contract.get("option_symbol") or ""),
            "option_symbol": str(contract.get("option_symbol") or ""),
            "contract_id": str(contract.get("contract_id") or ""),
            "expiration": str(contract.get("expiration") or entry.get("expiration") or ""),
        }
    ]


def shadow_hydration_targets(trades: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Unique symbol+expiration pairs for chain fetch."""
    out: List[Dict[str, str]] = []
    seen: set[Tuple[str, str]] = set()
    for trade in trades:
        entry = trade.get("entry") if isinstance(trade.get("entry"), dict) else {}
        sym = str(entry.get("symbol") or "").strip().upper()
        contract = entry.get("contract_identity") if isinstance(entry.get("contract_identity"), dict) else {}
        exp = str(contract.get("expiration") or entry.get("expiration") or "").strip()
        if not sym:
            continue
        key = (sym, exp)
        if key in seen:
            continue
        seen.add(key)
        out.append({"symbol": sym, "expiration": exp})
    return out


def build_shadow_leg_quotes_strict(
    chain_data: Optional[Dict[str, Any]],
    legs_for_quotes: List[Dict[str, Any]],
    *,
    symbol: str = "",
    hydration_started_ts: Optional[float] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """
    Build leg quotes for shadow path using exact strike + option_symbol match only.
    No nearest strike, no synthetic mid from entry_price, no cached unrelated contracts.
    """
    import time

    from profit_optimizer.audit import find_contract_at_strike_strict

    audit: Dict[str, Any] = {
        "long_leg_found": False,
        "short_leg_found": False,
        "spread_quote_quality": "missing",
        "chain_used": bool(isinstance(chain_data, dict) and (chain_data.get("calls") or chain_data.get("puts"))),
        "strict_strike_match": True,
        "shadow_quote_exact": False,
    }
    leg_quotes: List[Dict[str, Any]] = []
    if not isinstance(chain_data, dict):
        return leg_quotes, audit

    calls = chain_data.get("calls") if isinstance(chain_data.get("calls"), list) else []
    puts = chain_data.get("puts") if isinstance(chain_data.get("puts"), list) else []
    started = float(hydration_started_ts or time.time())

    for lq in legs_for_quotes:
        if not isinstance(lq, dict):
            continue
        try:
            strike = float(lq.get("strike") or 0.0)
        except (TypeError, ValueError):
            continue
        if strike <= 0.0:
            continue
        opt_type = str(lq.get("option_type") or lq.get("side") or "call").lower()
        contracts = calls if opt_type.startswith("c") or opt_type == "call" else puts
        exp_sym = str(lq.get("option_symbol") or lq.get("leg_id") or "").strip()
        contract = find_contract_at_strike_strict(
            contracts,
            strike,
            expected_option_symbol=exp_sym,
            expected_option_type=opt_type,
        )
        if contract is None:
            continue
        bid_v = float(getattr(contract, "bid", 0.0) or 0.0)
        ask_v = float(getattr(contract, "ask", 0.0) or 0.0)
        if bid_v <= 0.0 or ask_v <= 0.0:
            continue
        mid_v = float(getattr(contract, "mid_price", 0.0) or 0.0)
        if mid_v <= 0.0:
            mid_v = (bid_v + ask_v) / 2.0
        if mid_v <= 0.0:
            continue
        role = str(lq.get("long_or_short") or "long").lower()
        fetch_ts = time.time()
        leg_age_sec = max(0.01, fetch_ts - started)
        resolved_sym = exp_sym or _contract_symbol_from_obj(contract)
        leg_quotes.append(
            {
                "leg_id": resolved_sym or str(lq.get("leg_id") or symbol or ""),
                "symbol": str(lq.get("symbol") or symbol or ""),
                "option_symbol": resolved_sym,
                "strike": strike,
                "option_type": opt_type,
                "long_or_short": role,
                "bid": bid_v,
                "ask": ask_v,
                "last": float(getattr(contract, "last", 0.0) or 0.0) or mid_v,
                "mid_price": mid_v,
                "mark": bid_v if bid_v and bid_v > 0 else mid_v,
                "quote_source": "chain_strike_match",
                "premium_source": "chain_strike_match",
                "quote_fetched_at": fetch_ts,
                "quote_age_seconds": leg_age_sec,
                "mark_quality": "live_single_leg",
                "shadow_quote_exact": True,
            }
        )
        if role == "long":
            audit["long_leg_found"] = True
        elif role == "short":
            audit["short_leg_found"] = True

    if len(leg_quotes) >= 2 and audit["long_leg_found"] and audit["short_leg_found"]:
        audit["spread_quote_quality"] = "live_two_leg"
        audit["shadow_quote_exact"] = True
    elif len(leg_quotes) == 1:
        lq0 = leg_quotes[0]
        if lq0.get("bid") and lq0.get("ask"):
            audit["spread_quote_quality"] = "live_single_leg"
            audit["shadow_quote_exact"] = True
    elif leg_quotes:
        audit["spread_quote_quality"] = "partial_leg"
        audit["shadow_quote_exact"] = False
        leg_quotes = []

    audit["hydration_age_seconds"] = max(0.0, time.time() - started)
    return leg_quotes, audit


def _contract_symbol_from_obj(contract: Any) -> str:
    for attr in ("option_symbol", "symbol", "occ_symbol"):
        val = getattr(contract, attr, None)
        if val:
            return str(val).strip().upper()
    return ""


def build_shadow_option_quote_from_legs(
    leg_quotes: List[Dict[str, Any]],
    *,
    underlying_price: float,
) -> Optional[Dict[str, Any]]:
    """Single-leg or spread net mark from exact chain legs only (no modeled price)."""
    if not leg_quotes or not all(lq.get("shadow_quote_exact") for lq in leg_quotes):
        return None
    if len(leg_quotes) == 1:
        lq0 = leg_quotes[0]
        # STC economics: shadow mark = bid (sell-to-close), not mid
        try:
            from modules.option_executable_pricing import long_option_sell_to_close_price

            mark = float(
                long_option_sell_to_close_price(
                    bid=lq0.get("bid"),
                    ask=lq0.get("ask"),
                    mid=lq0.get("mid_price") or lq0.get("mark"),
                    last=lq0.get("last"),
                )
                or 0.0
            )
        except Exception:
            mark = float(lq0.get("bid") or lq0.get("mark") or lq0.get("mid_price") or 0.0)
        if mark <= 0:
            return None
        return {
            "bid": lq0.get("bid"),
            "ask": lq0.get("ask"),
            "mark": mark,
            "mid_price": float(lq0.get("mid_price") or lq0.get("mark") or mark),
            "current_position_value": mark,
            "has_position_value": True,
            "leg_quotes": leg_quotes,
            "quote_source": "chain_strike_match",
            "shadow_quote_exact": True,
        }
    net = 0.0
    for lq in leg_quotes:
        px = float(lq.get("mark") or lq.get("mid_price") or 0.0)
        if px <= 0:
            return None
        role = str(lq.get("long_or_short") or "long").lower()
        net += px if role == "long" else -px
    if net <= 0:
        return None
    return {
        "mark": net,
        "mid_price": net,
        "current_position_value": net,
        "has_position_value": True,
        "leg_quotes": leg_quotes,
        "quote_source": "chain_strike_match",
        "shadow_quote_exact": True,
        "underlying_price": underlying_price,
    }
