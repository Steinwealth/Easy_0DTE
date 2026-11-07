#!/usr/bin/env python3
"""
Wire E*TRADE options API + broker adapter for LIVE soak readiness.

When ENABLE_0DTE_STRATEGY=false (execution paused), Live mode still needs
balance, option chains, and broker certification for /api/ready and Good Morning.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional, Tuple

log = logging.getLogger(__name__)


def _resolve_dte_account_id() -> Optional[str]:
    from modules.broker_config_manager import get_broker_config_manager

    dte_account_id = (
        os.getenv("ETRADE_0DTE_ACCOUNT_ID")
        or os.getenv("0DTE_ETRADE_ACCOUNT_ID")
        or None
    )
    if dte_account_id:
        return str(dte_account_id).strip()
    broker_config = get_broker_config_manager()
    dte_account_config = broker_config.get_dte_account_config()
    if dte_account_config and dte_account_config.broker_type.value == "etrade":
        return str(dte_account_config.account_id).strip()
    return None


def build_etrade_options_api(etrade_trader: Any) -> Optional[Any]:
    """Initialize ETradeOptionsAPI for the configured 0DTE account."""
    if etrade_trader is None:
        log.warning("LIVE_BROKER_SOAK | etrade_trader missing — options API skipped")
        return None
    try:
        from easy0DTE.modules.etrade_options_api import ETradeOptionsAPI
    except ImportError as exc:
        log.error("LIVE_BROKER_SOAK | ETradeOptionsAPI import failed: %s", exc)
        return None

    dte_account_id = _resolve_dte_account_id()
    if dte_account_id:
        log.info(
            "LIVE_BROKER_SOAK | ETrade Options API account=%s (execution may be disabled)",
            dte_account_id,
        )
        return ETradeOptionsAPI(
            etrade_trading=etrade_trader,
            environment="prod",
            account_id=dte_account_id,
        )
    log.info("LIVE_BROKER_SOAK | ETrade Options API shared account (no ETRADE_0DTE_ACCOUNT_ID)")
    return ETradeOptionsAPI(etrade_trading=etrade_trader, environment="prod")


async def wire_live_broker_stack(
    system: Any,
    *,
    etrade_trader: Any = None,
) -> Tuple[Optional[Any], Optional[Any]]:
    """
    Attach broker adapter + options API to system for readiness probes.

    Idempotent — skips if system._broker_adapter is already available.
    """
    from modules.broker_adapter import create_broker_adapter
    from modules.runtime_mode import audit_production_startup, is_live_mode

    existing = getattr(system, "_broker_adapter", None)
    if existing is not None and getattr(existing, "is_available", lambda: False)():
        api = getattr(system, "_etrade_options_api", None)
        log.info("LIVE_BROKER_SOAK | broker stack already wired")
        return api, existing

    if not is_live_mode():
        return None, None

    etrade_options_api = build_etrade_options_api(etrade_trader)
    if etrade_options_api is None:
        return None, None

    broker_adapter = create_broker_adapter(
        etrade_options_api=etrade_options_api,
        live_mode=True,
    )
    system._broker_adapter = broker_adapter
    system._etrade_options_api = etrade_options_api

    log.info(
        "LIVE_BROKER_SOAK | wired | available=%s | account_env=%s",
        broker_adapter.is_available(),
        _resolve_dte_account_id(),
    )

    audit_production_startup(broker_adapter=broker_adapter, demo_mode=False)

    try:
        from modules.options_level_audit import audit_options_level

        option_level_raw = None
        if broker_adapter.is_available():
            bal = await broker_adapter.get_account_balance()
            if bal:
                raw = getattr(bal, "raw", {}) or {}
                option_level_raw = raw.get("optionLevel")
        if option_level_raw is None and etrade_options_api.etrade:
            bal_obj = etrade_options_api.etrade.get_account_balance()
            if bal_obj:
                option_level_raw = getattr(bal_obj, "option_level", None)
        audit_options_level(
            option_level_raw,
            account_id=os.getenv("ETRADE_0DTE_ACCOUNT_ID"),
        )
    except Exception as level_err:
        log.warning("LIVE_BROKER_SOAK | OPTIONS_LEVEL_AUDIT skipped: %s", level_err)

    return etrade_options_api, broker_adapter
