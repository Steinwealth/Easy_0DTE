#!/usr/bin/env python3
"""
One live-data gate for the running process.

Broker selection stays on BROKER. Authorization for that broker (E*TRADE OAuth
in Secret Manager today; IB, Schwab, and TastyTrade when those planes exist)
is restored here. Quotes, the option chain, Trendline, and Breakout all wait
on this step. A renewal does not require a new deploy or a process restart.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

log = logging.getLogger("improved_main")

_STRATEGY_ATTRS = (
    ("ENABLE_0DTE_STRATEGY", "dte0_manager"),
    ("ENABLE_TRENDLINE_STRATEGY", "trendline_signal_engine"),
    ("ENABLE_BREAKOUT_STRATEGY", "breakout_options_executor"),
)


def _flag_on(name: str) -> bool:
    return os.getenv(name, "false").strip().lower() in ("1", "true", "yes", "on")


def strategies_requiring_live_data() -> List[str]:
    labels = {
        "ENABLE_0DTE_STRATEGY": "0dte",
        "ENABLE_TRENDLINE_STRATEGY": "trendline",
        "ENABLE_BREAKOUT_STRATEGY": "breakout",
    }
    return [labels[name] for name, _attr in _STRATEGY_ATTRS if _flag_on(name)]


def engines_missing(system: Any) -> List[str]:
    """Enabled strategy engines that were never attached to this process."""
    missing: List[str] = []
    labels = {
        "ENABLE_0DTE_STRATEGY": "0dte",
        "ENABLE_TRENDLINE_STRATEGY": "trendline",
        "ENABLE_BREAKOUT_STRATEGY": "breakout",
    }
    for name, attr in _STRATEGY_ATTRS:
        if _flag_on(name) and getattr(system, attr, None) is None:
            missing.append(labels[name])
    return missing


def _reload_quote_tokens(system: Any) -> bool:
    dm = getattr(system, "data_manager", None)
    if dm is None or not hasattr(dm, "reload_broker_tokens"):
        return False
    try:
        return bool(dm.reload_broker_tokens())
    except Exception as exc:
        log.warning("LIVE_DATA_ACCESS | quote token reload failed: %s", exc)
        return False


def _probe_live_api(system: Any) -> bool:
    dm = getattr(system, "data_manager", None)
    if dm is None or not hasattr(dm, "ensure_valid_token_before_orb_capture"):
        return False
    try:
        return bool(dm.ensure_valid_token_before_orb_capture())
    except Exception as exc:
        log.warning("LIVE_DATA_ACCESS | live probe failed: %s", exc)
        return False


def ensure_broker_session(system: Any) -> Optional[Any]:
    """
    Return a broker session that can serve live data.

    E*TRADE reads the current Secret Manager token. Other brokers stay
    fail-closed until their live-data plane is implemented.
    """
    broker = os.getenv("BROKER") or os.getenv("BROKER_TYPE") or "etrade"
    broker = str(broker).strip().lower()
    if broker in ("e*trade",):
        broker = "etrade"
    if broker != "etrade":
        log.error(
            "LIVE_DATA_ACCESS | broker=%s | live data plane not implemented",
            broker,
        )
        return None

    trader = getattr(system, "etrade_trader", None)
    if trader is not None and hasattr(trader, "reload_tokens"):
        if trader.reload_tokens() and hasattr(trader, "is_token_valid") and trader.is_token_valid():
            return trader
        log.warning("LIVE_DATA_ACCESS | existing E*TRADE session rejected — opening a new one")

    from modules.prime_etrade_trading import PrimeETradeTrading

    candidate = PrimeETradeTrading(environment="prod")
    if not candidate.initialize():
        log.error("LIVE_DATA_ACCESS | E*TRADE session init failed after token reload")
        return None
    system.etrade_trader = candidate
    log.info("LIVE_DATA_ACCESS | E*TRADE session opened from Secret Manager")
    return candidate


async def restore_live_data_access(system: Any) -> Dict[str, Any]:
    """
    Reload the broker credential and, if strategy engines were skipped at boot,
    attach them now. Engines that are already running are left in place.
    """
    reloaded = _reload_quote_tokens(system)
    live_ok = _probe_live_api(system)
    missing_before = engines_missing(system)
    result: Dict[str, Any] = {
        "reloaded": reloaded,
        "live_api_ok": live_ok,
        "engines_missing_before": missing_before,
        "engines_restored": False,
        "engines_missing_after": missing_before,
    }
    if not live_ok:
        log.warning(
            "LIVE_DATA_ACCESS | live_api_ok=false | engines_deferred=%s",
            ",".join(missing_before) or "none",
        )
        return result
    if not missing_before:
        system._live_data_deferred = False
        log.info("LIVE_DATA_ACCESS | live_api_ok=true | engines=already_attached")
        return result

    trader = ensure_broker_session(system)
    if trader is None:
        log.error("LIVE_DATA_ACCESS | live quote ok but broker session did not open")
        return result

    from modules.attach_live_strategies import attach_live_strategies

    await attach_live_strategies(system, trader)
    missing_after = engines_missing(system)
    restored = not missing_after
    result["engines_restored"] = restored
    result["engines_missing_after"] = missing_after
    if restored:
        system._live_data_deferred = False
        log.info(
            "LIVE_DATA_ACCESS | restored=true | engines=%s",
            ",".join(strategies_requiring_live_data()) or "none",
        )
    else:
        system._live_data_deferred = True
        log.error(
            "LIVE_DATA_ACCESS | restored=false | still_missing=%s",
            ",".join(missing_after),
        )
    return result
