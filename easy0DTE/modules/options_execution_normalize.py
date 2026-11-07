#!/usr/bin/env python3
"""
Execution-layer normalization for Easy 0DTE ORB options.

Maps strategy labels to structural position types and builds a shared metadata
schema for monitoring / exits. ETF path does not use this module.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING, Union

if TYPE_CHECKING:
    from .options_chain_manager import CreditSpread, DebitSpread, OptionContract

log = logging.getLogger(__name__)

_CP_WIRE_MISMATCH_LOGGED: set = set()
_CP_WIRE_MISMATCH_LOG_CAP = 400

# Canonical structural types consumed by shared options monitoring.
SINGLE_LEG_LONG_CALL = "single_leg_long_call"
SINGLE_LEG_LONG_PUT = "single_leg_long_put"
DEBIT_SPREAD = "debit_spread"
CREDIT_SPREAD = "credit_spread"

STRUCTURAL_POSITION_TYPES = frozenset(
    {SINGLE_LEG_LONG_CALL, SINGLE_LEG_LONG_PUT, DEBIT_SPREAD, CREDIT_SPREAD}
)


def _wire_side(option_side: str) -> str:
    side = str(option_side or "").strip().lower()
    if side.startswith("p"):
        return "put"
    if side.startswith("c"):
        return "call"
    return side


def _wire_cp_ok(quote_req: str, side: str) -> bool:
    want = "C" if side == "call" else "P"
    compact = quote_req.replace(" ", "").upper()
    m = re.search(r"(\d{6})([CP])(\d{8})$", compact)
    if m and m.group(2) != want:
        return False
    upper = quote_req.upper()
    if ":CALL:" in upper and side == "put":
        return False
    if ":PUT:" in upper and side == "call":
        return False
    return True


def _repair_quote_wire_cp(quote_req: str, side: str) -> str:
    """Flip OSI / colon C-P letter to match the trade side. Keep strike and expiry."""
    want = "C" if side == "call" else "P"
    repaired = re.sub(r"(\d{6})[CPcp](\d{8})$", rf"\1{want}\2", quote_req)
    if side == "call":
        repaired = re.sub(r":PUT:", ":CALL:", repaired, flags=re.I)
    else:
        repaired = re.sub(r":CALL:", ":PUT:", repaired, flags=re.I)
    return repaired


def resolve_0dte_quote_wire_for_side(
    *,
    trade_id: str,
    option_side: str,
    osi_key: str,
) -> str:
    """Shared ORB + Trendline 0DTE guard: quote wire C/P must match side.

    Wrong-letter wire (CALL trade with ``…P…`` / ``:PUT:``) is repaired to the
    trade side so Peak monitor still gets a quote. Empty only when repair fails.
    """
    quote_req = str(osi_key or "").strip()
    side = _wire_side(option_side)
    if not quote_req or side not in ("call", "put"):
        return "" if side not in ("call", "put") else quote_req
    if _wire_cp_ok(quote_req, side):
        return quote_req

    repaired = _repair_quote_wire_cp(quote_req, side)
    if repaired and _wire_cp_ok(repaired, side):
        key = (str(trade_id or ""), side, quote_req)
        if key not in _CP_WIRE_MISMATCH_LOGGED:
            if len(_CP_WIRE_MISMATCH_LOGGED) >= _CP_WIRE_MISMATCH_LOG_CAP:
                _CP_WIRE_MISMATCH_LOGGED.clear()
            _CP_WIRE_MISMATCH_LOGGED.add(key)
            log.warning(
                "OPTIONS_EXECUTOR | stage=cp_wire_mismatch | trade_id=%s | side=%s | "
                "osi=%s | repaired=%s | path=0dte_unified",
                trade_id,
                side,
                quote_req,
                repaired,
            )
        return repaired

    key = (str(trade_id or ""), side, quote_req)
    if key not in _CP_WIRE_MISMATCH_LOGGED:
        if len(_CP_WIRE_MISMATCH_LOGGED) >= _CP_WIRE_MISMATCH_LOG_CAP:
            _CP_WIRE_MISMATCH_LOGGED.clear()
        _CP_WIRE_MISMATCH_LOGGED.add(key)
        log.error(
            "OPTIONS_EXECUTOR | stage=cp_wire_mismatch | trade_id=%s | side=%s | "
            "osi=%s | clearing_wire | path=0dte_unified",
            trade_id,
            side,
            quote_req,
        )
    return ""


def validate_normalized_options_for_stealth(norm: Any) -> Tuple[bool, List[str]]:
    """
    Minimum field checks before using metadata['normalized_options'] as stealth/monitoring source of truth.

    Returns (is_valid, missing_or_invalid_tags). Never raises.
    """
    missing: List[str] = []
    if not isinstance(norm, dict):
        return False, ["not_a_dict"]

    pt = str(norm.get("position_type") or "").strip().lower()
    if pt not in STRUCTURAL_POSITION_TYPES:
        missing.append("position_type_invalid")

    und = str(norm.get("underlying_symbol") or "").strip()
    if not und:
        missing.append("underlying_symbol")

    exp = str(norm.get("expiration_ymd") or "").strip()
    if not exp:
        missing.append("expiration_ymd")

    legs = norm.get("legs")
    leg_dicts: List[Dict[str, Any]] = []
    if isinstance(legs, list):
        leg_dicts = [x for x in legs if isinstance(x, dict)]
    else:
        missing.append("legs_not_a_list")

    def _fnum(v: Any) -> float:
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    ev = _fnum(norm.get("entry_value"))
    ed_raw = norm.get("entry_debit")
    ec_raw = norm.get("entry_credit")
    ed = _fnum(ed_raw) if ed_raw is not None else 0.0
    ec = _fnum(ec_raw) if ec_raw is not None else 0.0

    if pt in STRUCTURAL_POSITION_TYPES:
        if pt in (SINGLE_LEG_LONG_CALL, SINGLE_LEG_LONG_PUT):
            if len(leg_dicts) < 1:
                missing.append("legs_min_1")
            if ev <= 0 and ed <= 0:
                missing.append("entry_value_or_entry_debit")
        elif pt == DEBIT_SPREAD:
            if len(leg_dicts) < 2:
                missing.append("legs_min_2")
            if ev <= 0 and ed <= 0:
                missing.append("entry_value_or_entry_debit")
        elif pt == CREDIT_SPREAD:
            if len(leg_dicts) < 2:
                missing.append("legs_min_2")
            if ev <= 0 and ec <= 0:
                missing.append("entry_value_or_entry_credit")

    return (len(missing) == 0), missing


def normalize_execution_result(norm: Dict[str, Any]) -> Dict[str, Any]:
    """Canonicalize execution metadata before stealth registration/monitoring."""
    if not isinstance(norm, dict):
        return {}
    out = dict(norm)
    out["position_type"] = str(out.get("position_type") or "").strip().lower()
    out["underlying_symbol"] = str(out.get("underlying_symbol") or "").strip().upper()
    out["expiration_ymd"] = expiry_to_ymd(str(out.get("expiration_ymd") or ""))
    out["strategy_type"] = str(out.get("strategy_type") or "").strip().lower()
    out.setdefault("legs", [])
    return out


def expiry_to_ymd(expiry: str) -> str:
    """Normalize expiry to YYYYMMDD when possible."""
    if not expiry:
        return ""
    s = str(expiry).strip().replace("-", "")
    if len(s) >= 8:
        return s[:8]
    return s


def _attach_calendar_expiry_fields(
    out: Dict[str, Any],
    *,
    expiration_ymd: str = "",
    expiry_selection: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Stamp calendar DTE / selection metadata (never hardcode dte=0 / is_0dte=True)."""
    try:
        from modules.calendar_expiry_metadata import (
            copy_expiry_fields,
            merge_expiry_trade_fields,
            us_equity_session_date_iso,
        )
    except Exception:
        return out
    sel = expiry_selection if isinstance(expiry_selection, dict) else {}
    if sel:
        copy_expiry_fields(sel, out)
    merge_expiry_trade_fields(
        out,
        selected_expiry=out.get("selected_expiry_ymd")
        or out.get("expiry_ymd")
        or expiration_ymd
        or out.get("expiration_ymd"),
        requested_expiry=out.get("requested_expiry_ymd") or sel.get("requested_expiry_ymd"),
        session_date=out.get("session_date") or sel.get("session_date") or us_equity_session_date_iso(),
        expiry_selection_mode=out.get("expiry_selection_mode") or sel.get("expiry_selection_mode"),
        fallback_reason=out.get("fallback_reason") if out.get("fallback_reason") is not None else sel.get("fallback_reason"),
        fallback_days=out.get("fallback_days") if out.get("fallback_days") is not None else sel.get("fallback_days"),
        prefer_existing=True,
    )
    return out


def orb_strategy_to_structural_execution_type(
    strategy_type: str,
    direction: str,
    *,
    spread_type: str = "debit",
) -> str:
    """
    Map Level-2 ORB strategy label + execution context to structural position_type.

    spread_type: 'single_leg' | 'debit' | 'credit' from signal.
    """
    st = (strategy_type or "").strip().lower()
    sp = (spread_type or "debit").strip().lower()
    d = (direction or "LONG").strip().upper()

    if sp == "credit":
        return CREDIT_SPREAD
    if sp == "single_leg":
        if st == "long_put":
            return SINGLE_LEG_LONG_PUT
        if st == "long_call":
            return SINGLE_LEG_LONG_CALL
        if st in ("lotto", "single_leg"):
            return SINGLE_LEG_LONG_PUT if d == "SHORT" else SINGLE_LEG_LONG_CALL

    if st == "long_call":
        return SINGLE_LEG_LONG_CALL
    if st == "long_put":
        return SINGLE_LEG_LONG_PUT
    if st in ("lotto", "single_leg"):
        return SINGLE_LEG_LONG_PUT if d == "SHORT" else SINGLE_LEG_LONG_CALL
    if st in ("momentum_scalper", "itm_probability_spread", "debit_spread"):
        return DEBIT_SPREAD
    if st in ("no_trade", ""):
        return DEBIT_SPREAD
    return DEBIT_SPREAD


def _leg_from_contract(
    contract: Any,
    *,
    long_or_short: str,
    quantity: int,
    leg_id_suffix: str,
) -> Dict[str, Any]:
    sym = getattr(contract, "symbol", None) or ""
    bid = _positive_contract_float(getattr(contract, "bid", None))
    ask = _positive_contract_float(getattr(contract, "ask", None))
    return {
        "leg_id": f"{sym}_{leg_id_suffix}".strip("_") or leg_id_suffix,
        "symbol": sym or None,
        "long_or_short": long_or_short,
        "option_side": str(getattr(contract, "option_type", "") or "").lower(),
        "strike": float(getattr(contract, "strike", 0.0) or 0.0),
        "quantity": int(max(1, quantity)),
        "entry_price": float(getattr(contract, "mid_price", 0.0) or 0.0),
        "delta_at_entry": float(getattr(contract, "delta", 0.0) or 0.0),
        "bid": bid,
        "ask": ask,
        "entry_bid": bid,
        "entry_ask": ask,
    }


def _positive_contract_float(value: Any) -> Optional[float]:
    try:
        v = float(value)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def build_normalized_metadata_debit_spread(
    *,
    trade_id: str,
    spread: Union["DebitSpread", Dict[str, Any]],
    quantity: int,
    strategy_type: str = "",
    direction: str = "",
    spread_type: str = "debit",
    strategy_name: str = "Easy ORB1 Breakout",
    source_path: str = "easy0DTE",
    setup_type: str = "",
    trigger_direction: str = "",
) -> Dict[str, Any]:
    if hasattr(spread, "symbol"):
        sym = spread.symbol
        exp = expiry_to_ymd(spread.expiry)
        opt_t = str(spread.option_type or "").lower()
        legs: List[Dict[str, Any]] = [
            _leg_from_contract(
                spread.long_contract,
                long_or_short="long",
                quantity=quantity,
                leg_id_suffix=f"{exp}_L{int(spread.long_strike)}_{opt_t}",
            ),
            _leg_from_contract(
                spread.short_contract,
                long_or_short="short",
                quantity=quantity,
                leg_id_suffix=f"{exp}_S{int(spread.short_strike)}_{opt_t}",
            ),
        ]
        entry_debit = float(spread.debit_cost)
    else:
        d = spread if isinstance(spread, dict) else {}
        sym = str(d.get("symbol") or "")
        exp = expiry_to_ymd(str(d.get("expiry") or ""))
        opt_t = str(d.get("option_type") or "").lower()
        lc = d.get("long_contract") or {}
        sc = d.get("short_contract") or {}
        entry_debit = float(d.get("debit_cost") or 0.0)
        long_mid = float(lc.get("mid_price") or 0.0)
        short_mid = float(sc.get("mid_price") or 0.0)
        if long_mid <= 0 and entry_debit > 0:
            long_mid = max(entry_debit * 0.65, 0.01)
        if short_mid <= 0 and entry_debit > 0:
            short_mid = max(entry_debit - long_mid, 0.01)
        legs = [
            {
                "leg_id": f"{sym}_{exp}_L{d.get('long_strike')}_{opt_t}",
                "symbol": lc.get("symbol"),
                "long_or_short": "long",
                "option_side": opt_t,
                "strike": float(d.get("long_strike") or 0.0),
                "quantity": int(max(1, quantity)),
                "entry_price": long_mid,
                "delta_at_entry": float(lc.get("delta") or 0.0),
            },
            {
                "leg_id": f"{sym}_{exp}_S{d.get('short_strike')}_{opt_t}",
                "symbol": sc.get("symbol"),
                "long_or_short": "short",
                "option_side": opt_t,
                "strike": float(d.get("short_strike") or 0.0),
                "quantity": int(max(1, quantity)),
                "entry_price": short_mid,
                "delta_at_entry": float(sc.get("delta") or 0.0),
            },
        ]

    structural = orb_strategy_to_structural_execution_type(
        strategy_type, direction, spread_type=spread_type
    )
    entry_value = entry_debit
    return {
        "position_type": structural,
        "legacy_position_type": "debit_spread",
        "strategy_name": strategy_name,
        "strategy_type": "orb_0dte",
        "orb_strategy_type": strategy_type or "",
        "underlying_symbol": sym,
        "expiration_ymd": exp,
        "entry_value": entry_value,
        "entry_debit": entry_debit,
        "entry_credit": None,
        "current_value": entry_value,
        "legs": legs,
        "delta_at_entry": float(legs[0].get("delta_at_entry") or 0.0) if legs else 0.0,
        "trigger_direction": trigger_direction or direction,
        "setup_type": setup_type or strategy_type,
        "source_path": source_path,
        "trade_id": trade_id,
    }


def build_normalized_metadata_credit_spread(
    *,
    trade_id: str,
    spread: Union["CreditSpread", Dict[str, Any]],
    quantity: int,
    strategy_type: str = "",
    direction: str = "",
    spread_type: str = "credit",
    strategy_name: str = "Easy ORB1 Breakout",
    source_path: str = "easy0DTE",
    setup_type: str = "",
    trigger_direction: str = "",
) -> Dict[str, Any]:
    if hasattr(spread, "symbol"):
        sym = spread.symbol
        exp = expiry_to_ymd(spread.expiry)
        opt_t = str(spread.option_type or "").lower()
        legs = [
            _leg_from_contract(
                spread.short_contract,
                long_or_short="short",
                quantity=quantity,
                leg_id_suffix=f"{exp}_Sh{int(spread.short_strike)}_{opt_t}",
            ),
            _leg_from_contract(
                spread.long_contract,
                long_or_short="long",
                quantity=quantity,
                leg_id_suffix=f"{exp}_Lg{int(spread.long_strike)}_{opt_t}",
            ),
        ]
        entry_credit = float(spread.credit_received)
    else:
        d = spread if isinstance(spread, dict) else {}
        sym = str(d.get("symbol") or "")
        exp = expiry_to_ymd(str(d.get("expiry") or ""))
        opt_t = str(d.get("option_type") or "").lower()
        sc = d.get("short_contract") or {}
        lc = d.get("long_contract") or {}
        legs = [
            {
                "leg_id": f"{sym}_{exp}_Sh{d.get('short_strike')}_{opt_t}",
                "symbol": sc.get("symbol"),
                "long_or_short": "short",
                "option_side": opt_t,
                "strike": float(d.get("short_strike") or 0.0),
                "quantity": int(max(1, quantity)),
                "entry_price": float(sc.get("mid_price") or 0.0),
                "delta_at_entry": float(sc.get("delta") or 0.0),
            },
            {
                "leg_id": f"{sym}_{exp}_Lg{d.get('long_strike')}_{opt_t}",
                "symbol": lc.get("symbol"),
                "long_or_short": "long",
                "option_side": opt_t,
                "strike": float(d.get("long_strike") or 0.0),
                "quantity": int(max(1, quantity)),
                "entry_price": float(lc.get("mid_price") or 0.0),
                "delta_at_entry": float(lc.get("delta") or 0.0),
            },
        ]
        entry_credit = float(d.get("credit_received") or 0.0)

    structural = orb_strategy_to_structural_execution_type(
        strategy_type, direction, spread_type=spread_type
    )
    entry_value = entry_credit
    return {
        "position_type": structural,
        "legacy_position_type": "credit_spread",
        "strategy_name": strategy_name,
        "strategy_type": "orb_0dte",
        "orb_strategy_type": strategy_type or "",
        "underlying_symbol": sym,
        "expiration_ymd": exp,
        "entry_value": entry_value,
        "entry_debit": None,
        "entry_credit": entry_credit,
        "current_value": entry_value,
        "legs": legs,
        "delta_at_entry": float(legs[0].get("delta_at_entry") or 0.0) if legs else 0.0,
        "trigger_direction": trigger_direction or direction,
        "setup_type": setup_type or strategy_type,
        "source_path": source_path,
        "trade_id": trade_id,
    }


def _executable_entry_premium_from_contract(contract: Any) -> float:
    """BTO fill = ask (true-to-live); never use mid for cost basis metadata."""
    try:
        from modules.option_executable_pricing import from_option_contract

        px = float(from_option_contract(contract, side="entry"))
        if px > 0:
            return px
    except Exception:
        pass
    if hasattr(contract, "ask"):
        try:
            ask = float(getattr(contract, "ask", 0.0) or 0.0)
            if ask > 0:
                return ask
        except (TypeError, ValueError):
            pass
    if isinstance(contract, dict):
        try:
            ask = float(contract.get("ask") or 0.0)
            if ask > 0:
                return ask
        except (TypeError, ValueError):
            pass
    if hasattr(contract, "mid_price"):
        return float(getattr(contract, "mid_price", 0.0) or 0.0)
    if isinstance(contract, dict):
        return float(contract.get("mid_price") or 0.0)
    return 0.0


def build_normalized_metadata_single_leg(
    *,
    trade_id: str,
    contract: Union["OptionContract", Dict[str, Any]],
    quantity: int,
    strategy_type: str = "",
    direction: str = "",
    spread_type: str = "single_leg",
    strategy_name: str = "Easy ORB1 Breakout",
    source_path: str = "easy0DTE",
    setup_type: str = "",
    trigger_direction: str = "",
    entry_fill_price: Optional[float] = None,
    expiry_selection: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if hasattr(contract, "symbol"):
        sym = contract.symbol
        exp = expiry_to_ymd(contract.expiry)
        opt_side = str(contract.option_type or "").lower()
        strike = float(contract.strike or 0.0)
        delta = float(contract.delta or 0.0)
    else:
        c = contract if isinstance(contract, dict) else {}
        sym = str(c.get("symbol") or "")
        exp = expiry_to_ymd(str(c.get("expiry") or ""))
        opt_side = str(c.get("option_type") or c.get("option_side") or "").lower()
        strike = float(c.get("strike") or 0.0)
        delta = float(c.get("delta") or 0.0)

    # Fail-closed C/P identity: never invent "call" for OCC when side is unknown.
    if opt_side not in ("call", "put"):
        tid = str(trade_id or "").lower()
        if re.search(r"\d+(\.\d+)?p[-_]?\d*$", tid) or tid.rstrip("0123456789").endswith("p"):
            opt_side = "put"
        elif re.search(r"\d+(\.\d+)?c[-_]?\d*$", tid) or tid.rstrip("0123456789").endswith("c"):
            opt_side = "call"
        else:
            structural_guess = orb_strategy_to_structural_execution_type(
                strategy_type, direction, spread_type=spread_type
            )
            if structural_guess == SINGLE_LEG_LONG_PUT:
                opt_side = "put"
            elif structural_guess == SINGLE_LEG_LONG_CALL:
                opt_side = "call"
    if opt_side not in ("call", "put"):
        log.error(
            "OPTIONS_EXECUTOR | stage=cp_identity_missing | trade_id=%s | symbol=%s | "
            "refusing_default_call_occ",
            trade_id,
            sym,
        )
        opt_side = ""

    if entry_fill_price is not None and float(entry_fill_price) > 0:
        prem = float(entry_fill_price)
    else:
        prem = _executable_entry_premium_from_contract(contract)

    if hasattr(contract, "symbol"):
        entry_bid = _positive_contract_float(getattr(contract, "bid", None))
        entry_ask = _positive_contract_float(getattr(contract, "ask", None))
    else:
        c = contract if isinstance(contract, dict) else {}
        entry_bid = _positive_contract_float(c.get("bid"))
        entry_ask = _positive_contract_float(c.get("ask"))

    structural = orb_strategy_to_structural_execution_type(
        strategy_type, direction, spread_type=spread_type
    )
    if structural not in (SINGLE_LEG_LONG_CALL, SINGLE_LEG_LONG_PUT):
        if opt_side == "put":
            structural = SINGLE_LEG_LONG_PUT
        elif opt_side == "call":
            structural = SINGLE_LEG_LONG_CALL

    leg = {
        "leg_id": f"{sym}_{exp}_{strike}_{opt_side or 'unknown'}",
        "symbol": sym or None,
        "long_or_short": "long",
        "option_side": opt_side,
        "strike": strike,
        "quantity": int(max(1, quantity)),
        "entry_price": prem,
        "delta_at_entry": delta,
        "bid": entry_bid,
        "ask": entry_ask,
        "entry_bid": entry_bid,
        "entry_ask": entry_ask,
        "expiration": exp,
        "expiry": exp,
    }
    # Prefer chain-returned Market Quote wire symbol (osiKey) over reconstructed forms.
    # Shared 0DTE guard: clear wire when OSI C/P letter ≠ resolved option_side.
    raw_osi = ""
    if hasattr(contract, "osi_key"):
        raw_osi = str(getattr(contract, "osi_key", "") or "").strip()
    elif isinstance(contract, dict):
        raw_osi = str(contract.get("osi_key") or contract.get("osiKey") or "").strip()
    osi_key = resolve_0dte_quote_wire_for_side(
        trade_id=str(trade_id or ""),
        option_side=opt_side,
        osi_key=raw_osi,
    )
    if osi_key:
        leg["osi_key"] = osi_key
        leg["osi_symbol"] = osi_key
        # Market Quote wire: prefer colon form; keep OSI only on osi_key/osi_symbol.
        quote_wire = osi_key
        try:
            from modules.option_contract_identity import (
                identity_from_any_option_symbol,
                identity_to_etrade_quote_symbol,
            )

            ident = identity_from_any_option_symbol(osi_key)
            if ident is not None and ":" not in osi_key:
                quote_wire = identity_to_etrade_quote_symbol(ident)
        except Exception:
            quote_wire = osi_key
        leg["broker_quote_request_symbol"] = quote_wire
    occ = ""
    if opt_side in ("call", "put") and sym and exp and strike > 0:
        try:
            from easy0DTE.modules.etrade_options_api import format_occ_option_symbol

            occ = format_occ_option_symbol(sym, exp, opt_side, strike) or ""
            if occ:
                leg["occ_symbol"] = occ
                leg["option_symbol"] = occ
        except Exception:
            occ = ""
    st_low = (strategy_type or "").lower()
    legacy = "single_leg" if st_low in ("lotto", "single_leg") else ("long_call" if st_low == "long_call" else ("long_put" if st_low == "long_put" else "single_leg"))
    out = {
        "position_type": structural,
        "legacy_position_type": legacy,
        "strategy_name": strategy_name,
        "strategy_type": "orb_0dte",
        "orb_strategy_type": strategy_type or "",
        "underlying_symbol": sym,
        "expiration_ymd": exp,
        "entry_value": prem,
        "entry_debit": prem,
        "entry_credit": None,
        "entry_bid": entry_bid,
        "entry_ask": entry_ask,
        "current_value": prem,
        "legs": [leg],
        "delta_at_entry": delta,
        "trigger_direction": trigger_direction or direction,
        "setup_type": setup_type or strategy_type,
        "source_path": source_path,
        "trade_id": trade_id,
    }
    if occ:
        out["occ_symbol"] = occ
        out["option_symbol"] = occ
    if osi_key:
        out["osi_key"] = osi_key
        out["osi_symbol"] = osi_key
        out["broker_quote_request_symbol"] = str(
            leg.get("broker_quote_request_symbol") or osi_key
        )
    sel = expiry_selection
    if not isinstance(sel, dict):
        if isinstance(contract, dict) and isinstance(contract.get("expiry_selection"), dict):
            sel = contract.get("expiry_selection")
        else:
            raw_sel = getattr(contract, "expiry_selection", None)
            sel = raw_sel if isinstance(raw_sel, dict) else None
    _attach_calendar_expiry_fields(
        out, expiration_ymd=exp, expiry_selection=sel if isinstance(sel, dict) else None
    )
    return out


def log_metadata_normalized(trade_id: str, position_type: str, symbol: str) -> None:
    log.info(
        "OPTIONS_EXECUTOR | stage=metadata_normalized | trade_id=%s | symbol=%s | position_type=%s",
        trade_id,
        symbol,
        position_type,
    )


def log_position_type_normalized(trade_id: str, structural: str, legacy: str) -> None:
    log.info(
        "OPTIONS_EXECUTOR | stage=position_type_normalized | trade_id=%s | structural=%s | legacy=%s",
        trade_id,
        structural,
        legacy,
    )


def build_trendline_normalized_metadata(
    *,
    trade_id: str,
    underlying_symbol: str,
    expiration_ymd: str,
    option_side: str,
    strike: float,
    quantity: int,
    entry_premium_per_contract: float,
    delta_at_entry: float,
    setup_type: Optional[str] = None,
    trigger_direction: Optional[str] = None,
    strategy_name: str = "easyTrendline_0DTE",
    strategy_type: str = "trendline_0dte",
    source_path: str = "easyTrendline",
    osi_key: str = "",
    expiry_selection: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Shared schema for Trendline 0DTE single-leg executions (aligned with ORB field names)."""
    side = (option_side or "").lower()
    if side not in ("call", "put"):
        tid = str(trade_id or "").lower()
        if "p-" in tid or tid.rstrip("0123456789").endswith("p"):
            side = "put"
        elif "c-" in tid or tid.rstrip("0123456789").endswith("c"):
            side = "call"
    if side not in ("call", "put"):
        log.error(
            "OPTIONS_EXECUTOR | stage=cp_identity_missing | trade_id=%s | symbol=%s | "
            "refusing_default_call_occ",
            trade_id,
            underlying_symbol,
        )
        side = ""
    structural = (
        SINGLE_LEG_LONG_PUT
        if side == "put"
        else SINGLE_LEG_LONG_CALL
        if side == "call"
        else ""
    )
    exp = expiry_to_ymd(expiration_ymd)
    qty = int(max(1, quantity))
    quote_req = resolve_0dte_quote_wire_for_side(
        trade_id=str(trade_id or ""),
        option_side=side,
        osi_key=str(osi_key or ""),
    )
    leg = {
        "leg_id": f"{underlying_symbol}_{exp}_{strike}_{side or 'unknown'}",
        "symbol": underlying_symbol,
        "long_or_short": "long",
        "option_side": side,
        "strike": float(strike),
        "quantity": qty,
        "entry_price": float(entry_premium_per_contract),
        "delta_at_entry": float(delta_at_entry),
        "expiration": exp,
        "expiry": exp,
    }
    if quote_req:
        leg["osi_key"] = quote_req
        leg["osi_symbol"] = quote_req
        quote_wire = quote_req
        try:
            from modules.option_contract_identity import (
                identity_from_any_option_symbol,
                identity_to_etrade_quote_symbol,
            )

            ident = identity_from_any_option_symbol(quote_req)
            if ident is not None and ":" not in quote_req:
                quote_wire = identity_to_etrade_quote_symbol(ident)
        except Exception:
            quote_wire = quote_req
        leg["broker_quote_request_symbol"] = quote_wire
    occ = ""
    if side in ("call", "put") and underlying_symbol and exp and float(strike) > 0:
        try:
            from easy0DTE.modules.etrade_options_api import format_occ_option_symbol

            occ = format_occ_option_symbol(underlying_symbol, exp, side, float(strike)) or ""
            if occ:
                leg["occ_symbol"] = occ
                leg["option_symbol"] = occ
        except Exception:
            occ = ""
    out = {
        "position_type": structural,
        "legacy_position_type": "trendline_single_leg",
        "strategy_name": strategy_name,
        "strategy_type": strategy_type,
        "orb_strategy_type": "",
        "underlying_symbol": underlying_symbol,
        "expiration_ymd": exp,
        "entry_value": float(entry_premium_per_contract),
        "entry_debit": float(entry_premium_per_contract),
        "entry_credit": None,
        "current_value": float(entry_premium_per_contract),
        "legs": [leg],
        "delta_at_entry": float(delta_at_entry),
        "trigger_direction": trigger_direction or "",
        "setup_type": setup_type or "",
        "source_path": source_path,
        "trade_id": trade_id,
    }
    if occ:
        out["occ_symbol"] = occ
        out["option_symbol"] = occ
    if quote_req:
        out["osi_key"] = quote_req
        out["osi_symbol"] = quote_req
        out["broker_quote_request_symbol"] = str(
            leg.get("broker_quote_request_symbol") or quote_req
        )
    _attach_calendar_expiry_fields(
        out,
        expiration_ymd=exp,
        expiry_selection=expiry_selection if isinstance(expiry_selection, dict) else None,
    )
    return out
