#!/usr/bin/env python3
"""
No-trade Live readiness probe for ORB1 Breakout + Trendline 0DTE.

Validates broker wiring, OAuth, account, and chain access without submitting orders.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

_PROBE_SYMBOL = os.getenv("LIVE_READINESS_PROBE_SYMBOL", "SPY")


def resolve_options_account_id(
    etrade_options_api: Any = None,
    *,
    etrade_trader: Any = None,
) -> Optional[str]:
    """
    Resolve the configured 0DTE options account id for readiness checks.

    Prefers the live E*TRADE selected account, then Cloud Run env aliases.
    """
    et = etrade_trader
    if et is None and etrade_options_api is not None:
        et = getattr(etrade_options_api, "etrade", None)
    if et is not None:
        selected = getattr(et, "selected_account", None)
        if selected is not None:
            aid = getattr(selected, "account_id", None)
            if aid is not None and str(aid).strip():
                return str(aid).strip()
        for attr in ("selected_account_id", "account_id"):
            raw = getattr(et, attr, None)
            if raw is not None and str(raw).strip():
                return str(raw).strip()
    for env_key in (
        "ETRADE_0DTE_ACCOUNT_ID",
        "0DTE_ETRADE_ACCOUNT_ID",
        "ETRADE_ACCOUNT_ID",
    ):
        raw = os.getenv(env_key)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    return None


async def run_live_readiness_probe(
    *,
    broker_adapter: Any = None,
    etrade_oauth: Any = None,
    etrade_options_api: Any = None,
    market_manager: Any = None,
) -> Dict[str, Any]:
    """
    Dry-run Live checks. Never calls submit_option_order or submit_close_order.
    """
    from modules.runtime_mode import is_live_mode, mode_label, resolve_broker_kind

    checks: Dict[str, Any] = {}
    errors: List[str] = []

    checks["mode"] = {
        "ok": is_live_mode(),
        "value": mode_label(),
        "detail": "MODE must be live for probe",
    }
    if not is_live_mode():
        errors.append("mode_not_live")

    broker = resolve_broker_kind()
    checks["broker"] = {"ok": broker == "etrade", "value": broker}
    if broker != "etrade":
        errors.append(f"broker_not_etrade:{broker}")

    oauth_ok = False
    oauth_detail = "not_checked"
    if etrade_oauth is not None:
        try:
            oauth_ok = bool(etrade_oauth.is_authenticated())
            oauth_detail = str(etrade_oauth.get_auth_status() if hasattr(etrade_oauth, "get_auth_status") else oauth_ok)
        except Exception as exc:
            oauth_detail = str(exc)
    checks["oauth"] = {"ok": oauth_ok, "detail": oauth_detail}
    if not oauth_ok:
        errors.append("oauth_not_authenticated")

    adapter_loaded = broker_adapter is not None
    adapter_available = bool(
        adapter_loaded and getattr(broker_adapter, "is_available", lambda: False)()
    )
    checks["broker_adapter"] = {
        "ok": adapter_available,
        "loaded": adapter_loaded,
        "kind": getattr(getattr(broker_adapter, "kind", None), "value", None),
    }
    if not adapter_available:
        errors.append("broker_adapter_unavailable")

    account_id = resolve_options_account_id(etrade_options_api=etrade_options_api)
    checks["account_id"] = {"ok": bool(account_id), "value": account_id}
    if not account_id:
        errors.append("account_id_unresolved")

    balance_ok = False
    balance_detail: Any = None
    if adapter_available:
        try:
            bal = await broker_adapter.get_account_balance()
            if bal and float(bal.cash_available) >= 0:
                balance_ok = True
                balance_detail = {
                    "cash_available": float(bal.cash_available),
                    "buying_power": float(bal.buying_power),
                    "account_value": float(bal.account_value),
                }
        except Exception as exc:
            balance_detail = str(exc)
    checks["account_balance"] = {"ok": balance_ok, "detail": balance_detail}
    if not balance_ok:
        errors.append("account_balance_unreadable")

    chain_ok = False
    chain_detail: Any = None
    if adapter_available:
        try:
            today = datetime.now(timezone.utc).strftime("%Y%m%d")
            chain = await broker_adapter.get_option_chain(
                _PROBE_SYMBOL,
                today,
                strike_count=5,
            )
            calls = list((chain or {}).get("calls") or [])
            puts = list((chain or {}).get("puts") or [])
            chain_ok = len(calls) > 0 or len(puts) > 0
            chain_detail = {"calls": len(calls), "puts": len(puts), "symbol": _PROBE_SYMBOL}
        except Exception as exc:
            chain_detail = str(exc)
    checks["option_chain"] = {"ok": chain_ok, "detail": chain_detail}
    if not chain_ok:
        errors.append("option_chain_unavailable")

    market_ok = True
    market_detail: Any = "not_checked"
    if market_manager is not None:
        try:
            session = market_manager.get_current_session() if hasattr(market_manager, "get_current_session") else None
            market_detail = str(session)
        except Exception as exc:
            market_ok = False
            market_detail = str(exc)
    checks["market_session"] = {"ok": market_ok, "detail": market_detail}

    expiry_ok = True
    try:
        exp = datetime.now(timezone.utc).strftime("%Y%m%d")
        expiry_ok = len(exp) == 8
        checks["option_expiration"] = {"ok": expiry_ok, "today_ymd": exp}
    except Exception as exc:
        expiry_ok = False
        checks["option_expiration"] = {"ok": False, "error": str(exc)}

    checks["submit_order_called"] = {"ok": True, "value": False}
    checks["close_order_called"] = {"ok": True, "value": False}

    ready = len(errors) == 0
    result = {
        "ready": ready,
        "probe": "live_readiness",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "errors": errors,
    }
    log.info(
        "LIVE_READINESS_PROBE | ready=%s | errors=%s | symbol=%s",
        ready,
        errors or "none",
        _PROBE_SYMBOL,
    )
    return result
