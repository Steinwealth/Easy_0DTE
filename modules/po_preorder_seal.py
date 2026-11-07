"""Exact selected-contract Trade Profile seal, frozen before the broker order.

Rows 13–18 are the contract that was selected, not the representative chain
pair and not the fill. A later fill must not replace this seal.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

PROFILE_KEYS = ("delta", "otm_pct", "iv", "theta", "gamma", "vega")
QUOTE_KEYS = ("bid", "ask", "mid", "spread_pct", "quote_asof")

_SEALS: Dict[Tuple[str, str, str], Dict[str, Any]] = {}


def reset_preorder_seals_for_tests() -> None:
    _SEALS.clear()


def _num(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def _read(contract: Any, *names: str) -> Any:
    if isinstance(contract, dict):
        for name in names:
            if contract.get(name) not in (None, ""):
                return contract.get(name)
        return None
    for name in names:
        value = getattr(contract, name, None)
        if value not in (None, ""):
            return value
    return None


def _identity(symbol: Any, strike: Any, side: Any) -> Tuple[str, str, str]:
    side_text = str(side or "").strip().lower()
    if "put" in side_text:
        side_text = "put"
    elif "call" in side_text:
        side_text = "call"
    strike_num = _num(strike)
    strike_text = "" if strike_num is None else f"{strike_num:.4f}"
    return str(symbol or "").strip().upper(), strike_text, side_text


def _otm_pct(contract: Any) -> Optional[float]:
    existing = _num(_read(contract, "otm_pct"))
    if existing is not None:
        return existing
    strike = _num(_read(contract, "strike"))
    spot = _num(_read(contract, "underlying_price", "underlying", "spot"))
    if strike is None or spot is None or spot <= 0:
        return None
    side = str(_read(contract, "option_type", "option_side", "side") or "").lower()
    if "put" in side:
        return (spot - strike) / spot  # signed (Oct 13)
    if "call" in side:
        return (strike - spot) / spot  # signed (Oct 13)
    return None


def seal_selected_contract_before_order(contract: Any) -> Dict[str, Any]:
    """Read the already selected contract. Does not change selection or the order."""
    bid = _num(_read(contract, "bid"))
    ask = _num(_read(contract, "ask"))
    mid = _num(_read(contract, "mid", "mid_price"))
    spread_pct = None
    if bid is not None and ask is not None and mid is not None and mid > 0 and ask >= bid:
        spread_pct = (ask - bid) / mid * 100.0
    quote_asof = _read(contract, "quote_asof", "quote_timestamp", "quote_time")
    seal: Dict[str, Any] = {
        "delta": _num(_read(contract, "delta")),
        "otm_pct": _otm_pct(contract),
        "iv": _num(_read(contract, "iv", "implied_volatility")),
        "theta": _num(_read(contract, "theta")),
        "gamma": _num(_read(contract, "gamma")),
        "vega": _num(_read(contract, "vega")),
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "spread_pct": spread_pct,
        "quote_asof": None if quote_asof in (None, "") else str(quote_asof),
        "po_preorder_sealed": True,
    }
    key = _identity(
        _read(contract, "symbol", "underlying_symbol"),
        _read(contract, "strike"),
        _read(contract, "option_type", "option_side", "side"),
    )
    if key[0] and key[1] and key[2]:
        _SEALS[key] = dict(seal)
    try:
        bag = getattr(contract, "metadata", None)
        if isinstance(bag, dict):
            bag["po_preorder_trade_profile"] = dict(seal)
        elif isinstance(contract, dict):
            contract["po_preorder_trade_profile"] = dict(seal)
    except Exception:
        pass
    return seal


def _seal_for_row(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    direct = row.get("po_preorder_trade_profile")
    if isinstance(direct, dict) and direct.get("po_preorder_sealed"):
        return direct
    key = _identity(
        row.get("symbol"),
        row.get("strike") or row.get("po_options_owned_strike"),
        row.get("option_side") or row.get("option_type") or row.get("side"),
    )
    found = _SEALS.get(key)
    return found if isinstance(found, dict) else None


def apply_preorder_seal_to_row(row: Optional[Dict[str, Any]]) -> None:
    """Put the pre-order seal on the row. A fill value does not replace it."""
    if not isinstance(row, dict):
        return
    seal = _seal_for_row(row)
    if not isinstance(seal, dict):
        return
    row["po_preorder_trade_profile"] = dict(seal)
    row["po_preorder_sealed"] = True
    for key in PROFILE_KEYS:
        if seal.get(key) is not None:
            row[key] = seal[key]
            row[f"po_preorder_{key}"] = seal[key]
    for key in QUOTE_KEYS:
        if seal.get(key) is not None:
            row[f"po_preorder_{key}"] = seal[key]
