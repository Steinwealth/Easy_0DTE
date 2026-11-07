#!/usr/bin/env python3
"""ORB SO paired 0DTE premium shadow helpers (read-only research path)."""

from __future__ import annotations

from typing import Any, Dict, Optional


def so_premium_shadow_enabled(entry: Any) -> bool:
    extra = getattr(entry, "extra", None) if not isinstance(entry, dict) else entry.get("extra")
    if not isinstance(extra, dict):
        extra = {}
    flag = extra.get("premium_shadow_enabled")
    if isinstance(entry, dict):
        prem = entry.get("entry_premium")
    else:
        prem = getattr(entry, "entry_premium", None)
    return bool(flag) and float(prem or 0.0) > 0.0


def index_dte0_signal(signal: Any) -> Optional[Dict[str, Any]]:
    """Normalize DTE0Signal or dict into a lookup payload."""
    if signal is None:
        return None
    if isinstance(signal, dict):
        sym = str(signal.get("symbol") or "").upper()
        if not sym:
            return None
        direction = str(signal.get("direction") or "LONG").upper()
        option_type = str(signal.get("option_type") or ("call" if direction == "LONG" else "put")).lower()
        return {
            "symbol": sym,
            "direction": direction,
            "option_type": option_type,
            "target_delta": float(signal.get("target_delta") or 0.15),
            "strategy_type": str(signal.get("strategy_type") or "lotto"),
            "priority_score": signal.get("priority_score"),
            "priority_rank": signal.get("priority_rank"),
            "momentum_score": float(signal.get("momentum_score") or 0.0),
        }
    sym = str(getattr(signal, "symbol", "") or "").upper()
    if not sym:
        return None
    direction = str(getattr(signal, "direction", "LONG") or "LONG").upper()
    option_type = str(getattr(signal, "option_type", "call") if hasattr(signal, "option_type") else "call").lower()
    return {
        "symbol": sym,
        "direction": direction,
        "option_type": option_type,
        "target_delta": float(getattr(signal, "target_delta", 0.15) or 0.15),
        "strategy_type": str(getattr(signal, "strategy_type", "lotto") or "lotto"),
        "priority_score": getattr(signal, "priority_score", None),
        "priority_rank": getattr(signal, "priority_rank", None),
        "momentum_score": float(getattr(signal, "momentum_score", 0.0) or 0.0),
    }


def build_paired_contract_identity(
    *,
    symbol: str,
    option_type: str,
    strike: float,
    expiration: str,
    option_symbol: str = "",
    entry_premium: float = 0.0,
) -> Dict[str, Any]:
    side = str(option_type or "call").lower()
    return {
        "underlying_symbol": str(symbol).upper(),
        "expiration": str(expiration or ""),
        "strike": float(strike),
        "option_side": side,
        "option_symbol": str(option_symbol or ""),
        "legs": [
            {
                "strike": float(strike),
                "option_type": side,
                "long_or_short": "long",
                "option_symbol": str(option_symbol or ""),
                "expiration": str(expiration or ""),
            }
        ],
        "leg_count": 1,
        "multiplier": 100,
    }
