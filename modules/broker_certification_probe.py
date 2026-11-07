#!/usr/bin/env python3
"""
Read-only Broker Certification Probe for ORB1 Breakout + Trendline 0DTE Live cutover.

Validates E*TRADE account wiring and BrokerAdapter read paths without submitting
orders or modifying account state. Safe to run with MODE=demo (data/audit only).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from modules.broker_adapter import OptionOrderRequest

log = logging.getLogger(__name__)

_CHAIN_SYMBOLS = tuple(
    s.strip().upper()
    for s in os.getenv("BROKER_CERT_CHAIN_SYMBOLS", "SPY,QQQ").split(",")
    if s.strip()
) or ("SPY", "QQQ")

_RESOLUTION_SYMBOL = os.getenv("BROKER_CERT_RESOLUTION_SYMBOL", "SPY").strip().upper() or "SPY"

_BLOCKER_KEYS = frozenset(
    {
        "oauth",
        "account",
        "account_routing",
        "buying_power",
        "options_level",
        "market_hours",
        "buying_power_simulation",
        "option_chains",
        "contract_resolution",
        "strategy_compatibility",
        "close_compatibility",
    }
)


def _target_0dte_account_id() -> str:
    raw = (
        os.getenv("ETRADE_0DTE_ACCOUNT_ID")
        or os.getenv("0DTE_ETRADE_ACCOUNT_ID")
        or os.getenv("BROKER_CERT_ACCOUNT_ID")
        or ""
    )
    return str(raw).strip()


def _contract_fields(c: Any) -> Dict[str, Any]:
    if c is None:
        return {}
    return {
        "symbol": getattr(c, "symbol", None) or _RESOLUTION_SYMBOL,
        "strike": float(getattr(c, "strike", 0.0) or 0.0),
        "expiration": str(getattr(c, "expiry", "") or ""),
        "option_type": str(getattr(c, "option_type", "") or "").lower(),
        "bid": float(getattr(c, "bid", 0.0) or 0.0),
        "ask": float(getattr(c, "ask", 0.0) or 0.0),
        "delta": getattr(c, "delta", None),
    }


def _mid(c: Any) -> float:
    bid = float(getattr(c, "bid", 0.0) or 0.0)
    ask = float(getattr(c, "ask", 0.0) or 0.0)
    if bid > 0 and ask > 0:
        return (bid + ask) / 2.0
    last = float(getattr(c, "last", 0.0) or 0.0)
    return last if last > 0 else max(bid, ask)


def _estimate_spot(calls: List[Any], puts: List[Any]) -> Optional[float]:
    strikes = sorted(
        {
            float(getattr(c, "strike", 0.0) or 0.0)
            for c in (calls or []) + (puts or [])
            if float(getattr(c, "strike", 0.0) or 0.0) > 0
        }
    )
    if not strikes:
        return None
    return strikes[len(strikes) // 2]


def _pick_closest(contracts: List[Any], target_strike: float, option_type: str) -> Optional[Any]:
    typed = [
        c
        for c in contracts
        if str(getattr(c, "option_type", "") or "").upper().startswith(option_type[0].upper())
        or str(getattr(c, "option_type", "") or "").lower() == option_type.lower()
    ]
    if not typed:
        typed = list(contracts)
    if not typed:
        return None
    return min(typed, key=lambda c: abs(float(getattr(c, "strike", 0.0) or 0.0) - target_strike))


def _pick_lotto_otm(contracts: List[Any], spot: float, option_type: str) -> Optional[Any]:
    typed = [c for c in contracts if float(getattr(c, "strike", 0.0) or 0.0) > 0]
    if option_type.lower() == "call":
        otm = [c for c in typed if float(c.strike) > spot * 1.001]
        otm.sort(key=lambda c: float(c.strike))
    else:
        otm = [c for c in typed if float(c.strike) < spot * 0.999]
        otm.sort(key=lambda c: float(c.strike), reverse=True)
    for c in otm[:12]:
        if _mid(c) > 0:
            return c
    return otm[0] if otm else None


def _build_option_preview_payload(
    *,
    symbol: str,
    expiry_ymd: str,
    option_type: str,
    strike: float,
    order_action: str,
    quantity: int = 1,
    order_type: str = "MARKET",
    limit_price: Optional[float] = None,
) -> Dict[str, Any]:
    expiry_short = expiry_ymd.replace("-", "")[2:]
    code = "C" if option_type.lower() == "call" else "P"
    option_symbol = f"{symbol} {expiry_short}{code}{int(strike * 1000):08d}"
    instrument: Dict[str, Any] = {
        "Product": {"securityType": "OPTION", "symbol": option_symbol},
        "orderAction": order_action,
        "quantity": quantity,
    }
    payload: Dict[str, Any] = {
        "orderType": order_type,
        "clientOrderId": f"CERT_{int(time.time())}",
        "orderTerm": "GOOD_FOR_DAY",
        "priceType": order_type,
        "Order": [{"Instrument": [instrument]}],
    }
    if order_type == "LIMIT" and limit_price is not None:
        payload["Order"][0]["limitPrice"] = float(limit_price)
    return payload


async def _preview_option_order(et: Any, payload: Dict[str, Any]) -> Tuple[bool, Dict[str, Any]]:
    if et is None or not getattr(et, "selected_account", None):
        return False, {"error": "no_selected_account"}
    try:
        preview_url = (
            f"{et.config['base_url']}/v1/accounts/"
            f"{et.selected_account.account_id_key}/orders/preview"
        )
        response = await asyncio.to_thread(
            et._make_etrade_api_call,
            "POST",
            preview_url,
            payload,
        )
        if isinstance(response, dict) and "PreviewOrderResponse" in response:
            block = response["PreviewOrderResponse"] or {}
            est = block.get("estimatedTotalAmount") or block.get("totalOrderValue")
            commission = block.get("estimatedCommission") or block.get("commission")
            return True, {
                "preview_id": block.get("previewId"),
                "estimated_cost": est,
                "commission": commission,
                "raw_keys": sorted(block.keys()),
            }
        return False, {"error": "preview_response_missing", "response_type": type(response).__name__}
    except Exception as exc:
        return False, {"error": str(exc)}


async def run_broker_certification_probe(
    *,
    broker_adapter: Any = None,
    etrade_oauth: Any = None,
    etrade_options_api: Any = None,
    market_manager: Any = None,
) -> Dict[str, Any]:
    """
    Read-only broker certification. Never calls submit_option_order, submit_close_order,
    or orders/place.
    """
    from modules.runtime_mode import mode_label, resolve_broker_kind

    audits: Dict[str, Any] = {}
    blockers: List[str] = []
    errors: List[str] = []
    target_account = _target_0dte_account_id()

    et = getattr(etrade_options_api, "etrade", None) if etrade_options_api else None
    if et is None and broker_adapter is not None:
        et = getattr(broker_adapter, "etrade_trading", None)

    # --- 1. OAuth / Authentication ---
    broker = resolve_broker_kind()
    auth_status: Dict[str, Any] = {}
    authenticated = False
    token_expiration: Any = None
    account_count = 0
    api_reachable = False
    refresh_supported = bool(getattr(etrade_oauth, "oauth_manager", None)) if etrade_oauth else False
    refresh_ok = None

    if etrade_oauth is not None:
        try:
            auth_status = dict(etrade_oauth.get_auth_status() or {})
            authenticated = bool(auth_status.get("authenticated"))
            token_expiration = auth_status.get("expired_at") or auth_status.get("expires_at")
            if authenticated and hasattr(etrade_oauth, "needs_renewal"):
                refresh_ok = not bool(etrade_oauth.needs_renewal())
        except Exception as exc:
            auth_status = {"error": str(exc)}
            errors.append(f"oauth_check_failed:{exc}")

    if et is not None:
        try:
            if hasattr(et, "accounts") and et.accounts:
                account_count = len(et.accounts)
            elif hasattr(et, "load_accounts"):
                await asyncio.to_thread(et.load_accounts)
                account_count = len(getattr(et, "accounts", []) or [])
            api_reachable = account_count > 0 or authenticated
        except Exception as exc:
            errors.append(f"etrade_api_reachability:{exc}")

    auth_ok = authenticated and api_reachable
    auth_audit = {
        "broker": broker,
        "authenticated": authenticated,
        "token_expiration": token_expiration,
        "account_count": account_count,
        "api_reachable": api_reachable,
        "refresh_path_supported": refresh_supported,
        "refresh_path_ok": refresh_ok,
        "auth_status": auth_status.get("status"),
    }
    audits["BROKER_AUTH_AUDIT"] = auth_audit
    log.info("BROKER_AUTH_AUDIT | %s", auth_audit)
    if not auth_ok:
        blockers.append("oauth")
        errors.append("broker_certification_failed:oauth")

    # --- 2. Account Discovery ---
    account_id = target_account or getattr(getattr(et, "selected_account", None), "account_id", None)
    account_type = None
    account_status = None
    margin_enabled = None
    options_level = None
    account_ok = False

    if et is not None and target_account:
        try:
            if hasattr(et, "select_account"):
                et.select_account(target_account)
        except Exception as exc:
            errors.append(f"account_select:{exc}")

    selected = getattr(et, "selected_account", None) if et else None
    if selected:
        account_id = getattr(selected, "account_id", account_id)
        account_type = getattr(selected, "account_type", None)
        account_status = getattr(selected, "account_status", None)

    if target_account and selected and str(getattr(selected, "account_id", "")) == str(target_account):
        account_ok = str(getattr(selected, "account_status", "")).upper() == "ACTIVE"
    elif target_account and selected:
        account_ok = False
    elif not target_account:
        account_ok = False

    if et is not None and account_ok:
        try:
            bal_obj = await asyncio.to_thread(et.get_account_balance)
            if bal_obj:
                options_level = getattr(bal_obj, "option_level", None)
                margin_enabled = options_level is not None and str(options_level) not in ("", "0", "NONE")
        except Exception as exc:
            errors.append(f"account_balance_meta:{exc}")

    account_audit = {
        "account_id": account_id,
        "target_account_id": target_account,
        "account_type": account_type,
        "account_status": account_status,
        "margin_enabled": margin_enabled,
        "options_level": options_level,
        "selected": bool(selected),
    }
    audits["BROKER_ACCOUNT_AUDIT"] = account_audit
    log.info("BROKER_ACCOUNT_AUDIT | %s", account_audit)
    if not account_ok or not target_account:
        blockers.append("account")
        errors.append("broker_certification_failed:account")

    # --- 3. Buying Power ---
    cash_balance = None
    buying_power = None
    net_liquidation = None
    account_value = None
    balance_ok = False

    adapter_available = bool(
        broker_adapter and getattr(broker_adapter, "is_available", lambda: False)()
    )
    if adapter_available:
        try:
            bal = await broker_adapter.get_account_balance()
            if bal:
                cash_balance = float(bal.cash_available)
                buying_power = float(bal.buying_power)
                account_value = float(bal.account_value)
                net_liquidation = account_value
                balance_ok = cash_balance is not None and cash_balance >= 0
                raw = getattr(bal, "raw", {}) or {}
                options_level = options_level or raw.get("optionLevel")
        except Exception as exc:
            errors.append(f"balance_read:{exc}")

    balance_audit = {
        "cash_balance": cash_balance,
        "buying_power": buying_power,
        "net_liquidation": net_liquidation,
        "account_value": account_value,
    }
    audits["BROKER_BALANCE_AUDIT"] = balance_audit
    log.info("BROKER_BALANCE_AUDIT | %s", balance_audit)
    if not balance_ok:
        blockers.append("buying_power")
        errors.append("broker_certification_failed:buying_power")

    # --- 3b. Options approval level ---
    from modules.options_level_audit import audit_options_level

    options_level_audit = audit_options_level(
        options_level,
        account_id=target_account or account_id,
    )
    audits["OPTIONS_LEVEL_AUDIT"] = options_level_audit
    if not options_level_audit.get("pass"):
        blockers.append("options_level")
        errors.append("broker_certification_failed:options_level")

    # --- 3c. Buying power simulation (lotto sizing vs broker budget) ---
    from modules.broker_buying_power_simulation import run_broker_buying_power_simulation

    buying_power_sim = run_broker_buying_power_simulation(
        cash_available=cash_balance,
        buying_power=buying_power,
        account_value=account_value,
    )
    audits["BROKER_BUYING_POWER_SIMULATION"] = buying_power_sim
    if not buying_power_sim.get("all_pass"):
        blockers.append("buying_power_simulation")
        errors.append("broker_certification_failed:buying_power_simulation")

    # --- 3d. Market hours (ORB1 Breakout time sensitivity) ---
    from modules.broker_market_hours_audit import run_broker_market_hours_audit

    market_hours_audit = await run_broker_market_hours_audit(
        etrade_trading=et,
        market_manager=market_manager,
    )
    audits["BROKER_MARKET_HOURS_AUDIT"] = market_hours_audit
    if not market_hours_audit.get("can_determine_hours"):
        blockers.append("market_hours")
        errors.append("broker_certification_failed:market_hours")

    # --- 4. Option Chain Retrieval ---
    chain_audits: List[Dict[str, Any]] = []
    chains_ok = True
    today = datetime.now(timezone.utc).strftime("%Y%m%d")

    if adapter_available:
        for sym in _CHAIN_SYMBOLS:
            t0 = time.monotonic()
            chain_ok = False
            detail: Dict[str, Any] = {"symbol": sym, "error": None}
            try:
                chain = await broker_adapter.get_option_chain(sym, today, strike_count=10)
                calls = list((chain or {}).get("calls") or [])
                puts = list((chain or {}).get("puts") or [])
                strikes = {
                    float(getattr(c, "strike", 0.0) or 0.0)
                    for c in calls + puts
                    if float(getattr(c, "strike", 0.0) or 0.0) > 0
                }
                expirations = {
                    str(getattr(c, "expiry", "") or "")
                    for c in calls + puts
                    if getattr(c, "expiry", None)
                }
                chain_ok = len(calls) > 0 and len(puts) > 0
                detail.update(
                    {
                        "expirations_found": len(expirations) or (1 if calls or puts else 0),
                        "strikes_found": len(strikes),
                        "calls_found": len(calls),
                        "puts_found": len(puts),
                        "latency_ms": round((time.monotonic() - t0) * 1000, 1),
                    }
                )
            except Exception as exc:
                detail["error"] = str(exc)
                chain_ok = False
            if not chain_ok:
                chains_ok = False
            chain_audits.append(detail)
            log.info("BROKER_OPTION_CHAIN_AUDIT | %s", detail)
    else:
        chains_ok = False
        errors.append("broker_adapter_unavailable_for_chains")

    audits["BROKER_OPTION_CHAIN_AUDIT"] = chain_audits
    if not chains_ok:
        blockers.append("option_chains")
        errors.append("broker_certification_failed:option_chains")

    # --- 5. Contract Resolution (SPY) ---
    resolution_audit: Dict[str, Any] = {"symbol": _RESOLUTION_SYMBOL, "contracts": {}}
    resolution_ok = False
    spy_calls: List[Any] = []
    spy_puts: List[Any] = []
    expiry_for_preview = today

    if adapter_available:
        try:
            chain = await broker_adapter.get_option_chain(
                _RESOLUTION_SYMBOL, today, strike_count=15
            )
            spy_calls = list((chain or {}).get("calls") or [])
            spy_puts = list((chain or {}).get("puts") or [])
            spot = _estimate_spot(spy_calls, spy_puts) or 0.0
            if spy_calls:
                expiry_for_preview = str(getattr(spy_calls[0], "expiry", today) or today).replace("-", "")
            atm_call = _pick_closest(spy_calls, spot, "call")
            atm_put = _pick_closest(spy_puts, spot, "put")
            lotto_call = _pick_lotto_otm(spy_calls, spot, "call")
            lotto_put = _pick_lotto_otm(spy_puts, spot, "put")
            resolved = {
                "atm_call": _contract_fields(atm_call),
                "atm_put": _contract_fields(atm_put),
                "otm_lotto_call": _contract_fields(lotto_call),
                "otm_lotto_put": _contract_fields(lotto_put),
                "spot_estimate": spot,
            }
            resolution_audit["contracts"] = resolved
            resolution_ok = all(
                resolved[k].get("strike")
                for k in ("atm_call", "atm_put", "otm_lotto_call", "otm_lotto_put")
            )
        except Exception as exc:
            resolution_audit["error"] = str(exc)
            errors.append(f"contract_resolution:{exc}")

    audits["BROKER_CONTRACT_RESOLUTION_AUDIT"] = resolution_audit
    log.info("BROKER_CONTRACT_RESOLUTION_AUDIT | %s", resolution_audit)
    if not resolution_ok:
        blockers.append("contract_resolution")
        errors.append("broker_certification_failed:contract_resolution")

    # --- 6. Order Preview Capability (preview only) ---
    preview_supported = et is not None and bool(getattr(et, "_make_etrade_api_call", None))
    preview_results: Dict[str, Any] = {}
    preview_success = False
    estimated_cost = None
    commission = None

    if preview_supported and resolution_ok and et is not None:
        preview_cases = []
        contracts_map = resolution_audit.get("contracts") or {}
        for label, action in (
            ("buy_call", "BUY_OPEN"),
            ("buy_put", "BUY_OPEN"),
            ("sell_close_call", "SELL_CLOSE"),
            ("sell_close_put", "SELL_CLOSE"),
        ):
            key = {
                "buy_call": "atm_call",
                "buy_put": "atm_put",
                "sell_close_call": "otm_lotto_call",
                "sell_close_put": "otm_lotto_put",
            }[label]
            c = contracts_map.get(key) or {}
            if not c.get("strike"):
                preview_results[label] = {"ok": False, "error": "contract_missing"}
                continue
            otype = "call" if "call" in label else "put"
            payload = _build_option_preview_payload(
                symbol=_RESOLUTION_SYMBOL,
                expiry_ymd=expiry_for_preview,
                option_type=otype,
                strike=float(c["strike"]),
                order_action=action,
            )
            ok, detail = await _preview_option_order(et, payload)
            preview_results[label] = {"ok": ok, **detail}
            if ok:
                preview_success = True
                estimated_cost = detail.get("estimated_cost")
                commission = detail.get("commission")

    preview_audit = {
        "preview_supported": preview_supported,
        "preview_success": preview_success,
        "estimated_cost": estimated_cost,
        "commission": commission,
        "cases": preview_results,
    }
    audits["BROKER_ORDER_PREVIEW_AUDIT"] = preview_audit
    log.info("BROKER_ORDER_PREVIEW_AUDIT | %s", preview_audit)

    # --- 7. Order Submission Readiness (construct only) ---
    model_ok = True
    sample_contract = (resolution_audit.get("contracts") or {}).get("atm_call") or {}
    try:
        OptionOrderRequest(
            symbol=_RESOLUTION_SYMBOL,
            expiry=expiry_for_preview,
            option_type="call",
            strike=float(sample_contract.get("strike") or 1.0),
            quantity=1,
            side="BUY",
            order_type="MARKET",
        )
        OptionOrderRequest(
            symbol=_RESOLUTION_SYMBOL,
            expiry=expiry_for_preview,
            option_type="put",
            strike=float(sample_contract.get("strike") or 1.0),
            quantity=1,
            side="SELL",
            order_type="LIMIT",
            limit_price=0.5,
        )
    except Exception as exc:
        model_ok = False
        errors.append(f"order_model:{exc}")

    has_submit = bool(getattr(broker_adapter, "submit_option_order", None)) if broker_adapter else False
    has_close = bool(getattr(broker_adapter, "submit_close_order", None)) if broker_adapter else False
    order_model_audit = {
        "buy_to_open_supported": model_ok and has_submit,
        "sell_to_close_supported": model_ok and has_close,
        "market_supported": model_ok,
        "limit_supported": model_ok,
        "adapter_submit_present": has_submit,
        "adapter_close_present": has_close,
    }
    audits["BROKER_ORDER_MODEL_AUDIT"] = order_model_audit
    log.info("BROKER_ORDER_MODEL_AUDIT | %s", order_model_audit)

    # --- 8. Position Retrieval ---
    positions_count = 0
    positions_ok = False
    if adapter_available:
        try:
            positions = await broker_adapter.get_positions()
            if not positions and et is not None and hasattr(et, "get_portfolio"):
                portfolio = await asyncio.to_thread(et.get_portfolio)
                positions = [
                    {
                        "symbol": getattr(p, "symbol", ""),
                        "quantity": getattr(p, "quantity", 0),
                    }
                    for p in (portfolio or [])
                ]
            positions_count = len(positions or [])
            positions_ok = positions is not None
        except Exception as exc:
            errors.append(f"positions_query:{exc}")

    position_audit = {"success": positions_ok, "positions_count": positions_count}
    audits["BROKER_POSITION_QUERY_AUDIT"] = position_audit
    log.info("BROKER_POSITION_QUERY_AUDIT | %s", position_audit)

    # --- 9. Order Status Query ---
    order_status_ok = False
    order_status_detail: Dict[str, Any] = {}
    sample_order_id = None
    if et is not None and hasattr(et, "get_orders"):
        try:
            for status_filter in ("EXECUTED", "OPEN", "CANCELLED"):
                orders = await asyncio.to_thread(et.get_orders, status_filter)
                if orders:
                    sample_order_id = orders[0].get("order_id")
                    break
            if sample_order_id and adapter_available:
                order_status_detail = await broker_adapter.get_order_status(str(sample_order_id))
                order_status_ok = "error" not in (order_status_detail or {})
            elif sample_order_id is None:
                order_status_ok = True
                order_status_detail = {"note": "no_recent_orders_to_query"}
            else:
                order_status_detail = {"error": "adapter_unavailable"}
        except Exception as exc:
            order_status_detail = {"error": str(exc)}
            errors.append(f"order_status:{exc}")
    else:
        order_status_detail = {"note": "get_orders_unavailable"}
        order_status_ok = False

    order_status_audit = {
        "success": order_status_ok,
        "sample_order_id": sample_order_id,
        "detail": order_status_detail,
    }
    audits["BROKER_ORDER_STATUS_AUDIT"] = order_status_audit
    log.info("BROKER_ORDER_STATUS_AUDIT | %s", order_status_audit)

    # --- 9b. Account routing (ORB SO vs 0DTE accounts) ---
    from modules.broker_live_audits import (
        audit_broker_account_routing,
        audit_broker_close_compatibility,
        audit_broker_strategy_compatibility,
    )

    routing_audit = audit_broker_account_routing(
        etrade_options_api=etrade_options_api,
    )
    audits["BROKER_ACCOUNT_ROUTING_AUDIT"] = routing_audit
    if not routing_audit.get("pass"):
        blockers.append("account_routing")
        errors.append("broker_certification_failed:account_routing")

    # --- 9c. Strategy payload certification (recent ORB1 Breakout winners) ---
    strategy_compat_audit = await audit_broker_strategy_compatibility(
        broker_adapter=broker_adapter,
        etrade_options_api=etrade_options_api,
        options_level=options_level,
        account_id=target_account,
    )
    audits["BROKER_STRATEGY_COMPATIBILITY_AUDIT"] = strategy_compat_audit
    if not strategy_compat_audit.get("pass"):
        blockers.append("strategy_compatibility")
        errors.append("broker_certification_failed:strategy_compatibility")

    # --- 9d. Close payload certification (SELL_TO_CLOSE winners) ---
    close_compat_audit = await audit_broker_close_compatibility(
        broker_adapter=broker_adapter,
        etrade_options_api=etrade_options_api,
        account_id=target_account,
    )
    audits["BROKER_CLOSE_COMPATIBILITY_AUDIT"] = close_compat_audit
    if not close_compat_audit.get("pass"):
        blockers.append("close_compatibility")
        errors.append("broker_certification_failed:close_compatibility")

    # --- 10. Capability Matrix ---
    def _status(passed: bool, *, na: bool = False) -> str:
        if na:
            return "NA"
        return "PASS" if passed else "FAIL"

    preview_matrix = "PASS" if preview_success else ("NA" if not preview_supported else "FAIL")

    capability_matrix: List[Dict[str, str]] = [
        {"capability": "OAuth", "status": _status(auth_ok)},
        {"capability": "Account", "status": _status(account_ok and bool(target_account))},
        {
            "capability": "Account Routing",
            "status": _status(bool(routing_audit.get("pass"))),
        },
        {
            "capability": "Strategy Payload Certification",
            "status": _status(bool(strategy_compat_audit.get("pass"))),
        },
        {
            "capability": "Close Payload Certification",
            "status": _status(bool(close_compat_audit.get("pass"))),
        },
        {"capability": "Buying Power", "status": _status(balance_ok)},
        {
            "capability": "Options Level",
            "status": _status(bool(options_level_audit.get("pass"))),
        },
        {
            "capability": "Market Hours",
            "status": _status(bool(market_hours_audit.get("can_determine_hours"))),
        },
        {
            "capability": "Buying Power Simulation",
            "status": _status(bool(buying_power_sim.get("all_pass"))),
        },
        {"capability": "Option Chains", "status": _status(chains_ok)},
        {"capability": "Contract Resolution", "status": _status(resolution_ok)},
        {"capability": "Position Query", "status": _status(positions_ok)},
        {"capability": "Order Status", "status": _status(order_status_ok)},
        {"capability": "Order Preview", "status": preview_matrix},
        {
            "capability": "Buy To Open",
            "status": "READY" if order_model_audit.get("buy_to_open_supported") else "NOT_READY",
        },
        {
            "capability": "Sell To Close",
            "status": "READY" if order_model_audit.get("sell_to_close_supported") else "NOT_READY",
        },
    ]

    certified = not any(b in _BLOCKER_KEYS for b in blockers)
    status = "broker_certification_passed" if certified else "broker_certification_failed"

    result = {
        "certified": certified,
        "status": status,
        "probe": "broker_certification",
        "mode": mode_label(),
        "broker": broker,
        "target_account_id": target_account,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "audits": audits,
        "capability_matrix": capability_matrix,
        "blockers": blockers,
        "errors": errors,
        "read_only": True,
        "orders_submitted": False,
    }
    log.info(
        "BROKER_CERTIFICATION_PROBE | status=%s | certified=%s | blockers=%s | mode=%s | account=%s",
        status,
        certified,
        blockers or "none",
        mode_label(),
        target_account,
    )
    return result
