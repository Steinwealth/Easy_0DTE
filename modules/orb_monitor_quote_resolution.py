"""ORB1 Breakout monitor: resolve stored entry quotes before synthetic fallback."""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def _positive_float(value: Any) -> Optional[float]:
    try:
        v = float(value)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def resolve_stored_entry_bid_ask(
    pos: Any,
    *,
    leg: Optional[Dict[str, Any]] = None,
    osnap: Optional[Dict[str, Any]] = None,
) -> Tuple[Optional[float], Optional[float]]:
    """
    Prefer live entry-time bid/ask from position metadata before synthetic inference.

    Resolution order: leg dict → option_stealth snapshot → normalized_options →
    execution_payload chain_hint → lotto_contract on position.
    """
    bid: Optional[float] = None
    ask: Optional[float] = None

    if isinstance(leg, dict):
        bid = _positive_float(leg.get("entry_bid")) or _positive_float(leg.get("bid"))
        ask = _positive_float(leg.get("entry_ask")) or _positive_float(leg.get("ask"))

    osnap = osnap if isinstance(osnap, dict) else {}
    bid = bid or _positive_float(osnap.get("entry_bid")) or _positive_float(osnap.get("option_bid_at_entry"))
    ask = ask or _positive_float(osnap.get("entry_ask")) or _positive_float(osnap.get("option_ask_at_entry"))
    bid = bid or _positive_float(osnap.get("option_bid"))
    ask = ask or _positive_float(osnap.get("option_ask"))

    meta = getattr(pos, "metadata", None) if pos is not None else None
    if isinstance(meta, dict):
        os2 = meta.get("option_stealth") if isinstance(meta.get("option_stealth"), dict) else {}
        bid = bid or _positive_float(os2.get("entry_bid")) or _positive_float(os2.get("option_bid"))
        ask = ask or _positive_float(os2.get("entry_ask")) or _positive_float(os2.get("option_ask"))
        norm = meta.get("normalized_options") if isinstance(meta.get("normalized_options"), dict) else {}
        bid = bid or _positive_float(norm.get("entry_bid"))
        ask = ask or _positive_float(norm.get("entry_ask"))
        ep = meta.get("execution_payload") if isinstance(meta.get("execution_payload"), dict) else {}
        ch = ep.get("chain_hint") if isinstance(ep.get("chain_hint"), dict) else {}
        bid = bid or _positive_float(ch.get("bid"))
        ask = ask or _positive_float(ch.get("ask"))

    ptype = str(getattr(pos, "position_type", "") or "").lower()
    if ptype == "lotto" and getattr(pos, "lotto_contract", None):
        lc = pos.lotto_contract
        bid = bid or _positive_float(getattr(lc, "bid", None))
        ask = ask or _positive_float(getattr(lc, "ask", None))

    return bid, ask


def synthetic_bid_ask_from_premium(
    premium: float,
    *,
    half_spread_frac: float = 0.05,
) -> Tuple[float, float]:
    """Last-resort demo monitor quotes (~10% width). Not for entry spread gates."""
    p = max(0.01, float(premium))
    h = max(0.0, min(0.25, float(half_spread_frac)))
    return p * (1.0 - h), p * (1.0 + h)
