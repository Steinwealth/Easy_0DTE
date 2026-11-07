#!/usr/bin/env python3
"""
Live trading safety audits — broker truth, account routing, payload certification,
post-close flatness, orphan watchdog, and EOD reconciliation.

Read-only certification audits integrate with broker_certification_probe.py.
Runtime audits hook into execute_exit, monitor loop, and EOD flatten.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from modules.broker_adapter import BrokerCloseRequest, OptionOrderRequest

log = logging.getLogger(__name__)

EXPECTED_ORB_ACCOUNT_ID = "YOUR_ORB_ACCOUNT_ID"
EXPECTED_OPTIONS_ACCOUNT_ID = "YOUR_0DTE_ACCOUNT_ID"

STRATEGY_ACCOUNT_ROUTING: Dict[str, str] = {
    "ORB_SO": EXPECTED_ORB_ACCOUNT_ID,
    "ORB_0DTE": EXPECTED_OPTIONS_ACCOUNT_ID,
    "Trendline_0DTE": EXPECTED_OPTIONS_ACCOUNT_ID,
}

def _session_0dte_expiry_ymd() -> str:
    """Use today's US/Eastern calendar date as the 0DTE OCC expiry (YYYYMMDD)."""
    try:
        from zoneinfo import ZoneInfo

        et = datetime.now(ZoneInfo("America/New_York")).date()
    except Exception:
        et = date.today()
    return et.strftime("%Y%m%d")


def _default_winner_scenarios() -> List[Dict[str, Any]]:
    """Desk-shaped lotto payloads with **current-session** OCC expiry (not stale hardcoded dates)."""
    expiry = _session_0dte_expiry_ymd()
    return [
        {
            "label": "MSTR PUT lotto",
            "symbol": "MSTR",
            "option_type": "put",
            "strike": 115.0,
            "expiry": expiry,
            "premium": 0.79,
            "quantity": 1,
        },
        {
            "label": "COIN PUT lotto",
            "symbol": "COIN",
            "option_type": "put",
            "strike": 165.0,
            "expiry": expiry,
            "premium": 2.51,
            "quantity": 1,
        },
        {
            "label": "SLV PUT lotto",
            "symbol": "SLV",
            "option_type": "put",
            "strike": 66.0,
            "expiry": expiry,
            "premium": 0.35,
            "quantity": 1,
        },
        {
            "label": "GOOGL CALL lotto",
            "symbol": "GOOGL",
            "option_type": "call",
            "strike": 382.5,
            "expiry": expiry,
            "premium": 0.265,
            "quantity": 1,
        },
    ]


# Backward-compatible name — always resolves to current-session expiry.
_DEFAULT_WINNER_SCENARIOS: List[Dict[str, Any]] = []  # populated lazily via _default_winner_scenarios()


def _expected_orb_account_id() -> str:
    return str(
        os.getenv("BROKER_CERT_ORB_ACCOUNT_ID")
        or os.getenv("ETRADE_ORB_ACCOUNT_ID")
        or EXPECTED_ORB_ACCOUNT_ID
    ).strip()


def _expected_options_account_id() -> str:
    return str(
        os.getenv("BROKER_CERT_0DTE_ACCOUNT_ID")
        or os.getenv("ETRADE_0DTE_ACCOUNT_ID")
        or os.getenv("0DTE_ETRADE_ACCOUNT_ID")
        or EXPECTED_OPTIONS_ACCOUNT_ID
    ).strip()


def _load_winner_scenarios() -> List[Dict[str, Any]]:
    raw = os.getenv("BROKER_CERT_WINNER_SCENARIOS")
    if not raw or not str(raw).strip():
        return _default_winner_scenarios()
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list) and parsed:
            return parsed
    except json.JSONDecodeError:
        pass
    return _default_winner_scenarios()


def _log_audit(token: str, audit: Dict[str, Any]) -> None:
    parts = [f"{k}={v}" for k, v in audit.items() if k != "cases" and k != "routing"]
    if "routing" in audit:
        parts.append(f"routing={audit['routing']}")
    if "cases" in audit:
        parts.append(f"cases={len(audit['cases'])}")
    log.info("%s | %s", token, " | ".join(parts))


def _normalize_expiry(expiry: str) -> str:
    return str(expiry or "").replace("-", "").strip()


def _build_occ_symbol(*, symbol: str, expiry: str, option_type: str, strike: float) -> str:
    expiry_ymd = _normalize_expiry(expiry)
    expiry_short = expiry_ymd[2:] if len(expiry_ymd) >= 8 else expiry_ymd
    code = "C" if str(option_type).lower() == "call" else "P"
    strike_milli = int(round(float(strike) * 1000))
    return f"{symbol} {expiry_short}{code}{strike_milli:08d}"


def _expiry_valid(expiry: str) -> bool:
    token = _normalize_expiry(expiry)
    if len(token) != 8 or not token.isdigit():
        return False
    try:
        datetime.strptime(token, "%Y%m%d").date()
        return True
    except ValueError:
        return False


def _strike_valid(strike: float) -> bool:
    return float(strike) > 0


def _quantity_valid(quantity: int) -> bool:
    return int(quantity) > 0


def _position_contract_key(position: Any) -> Tuple[str, str]:
    """Return (underlying_symbol, occ_contract) for flat/orphan matching."""
    symbol = str(getattr(position, "symbol", "") or "").strip().upper()
    lc = getattr(position, "lotto_contract", None)
    if lc is not None:
        expiry = _normalize_expiry(
            str(getattr(lc, "expiry", "") or getattr(lc, "expiration", "") or "")
        )
        otype = str(getattr(lc, "option_type", "") or "call").lower()
        strike = float(getattr(lc, "strike", 0.0) or 0.0)
        if symbol and expiry and strike > 0:
            return symbol, _build_occ_symbol(
                symbol=symbol, expiry=expiry, option_type=otype, strike=strike
            )
    meta = dict(getattr(position, "metadata", None) or {})
    occ = str(meta.get("option_symbol") or meta.get("occ_symbol") or "").strip()
    if occ:
        return symbol, occ
    return symbol, symbol


def _broker_position_keys(positions: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Map normalized contract key → broker row."""
    out: Dict[str, Dict[str, Any]] = {}
    for row in positions or []:
        sym = str(row.get("symbol") or row.get("Symbol") or "").strip()
        if not sym:
            continue
        qty = float(row.get("quantity") or row.get("qty") or row.get("Quantity") or 0.0)
        key = sym.upper()
        out[key] = {"symbol": sym, "quantity": qty, "raw": row}
        # Also index by underlying if OCC-style "SYM YYMMDD..."
        parts = sym.split()
        if parts:
            out.setdefault(parts[0].upper(), out[key])
    return out


def audit_broker_account_routing(
    *,
    etrade_trader: Any = None,
    etrade_options_api: Any = None,
) -> Dict[str, Any]:
    """
    Verify ORB vs 0DTE account IDs and per-strategy routing table.
    """
    from modules.broker_config_manager import get_broker_config_manager

    orb_expected = _expected_orb_account_id()
    dte_expected = _expected_options_account_id()

    mgr = get_broker_config_manager()
    orb_cfg = mgr.get_orb_account_config()
    dte_cfg = mgr.get_dte_account_config()

    orb_resolved = str(getattr(orb_cfg, "account_id", "") or "").strip() if orb_cfg else ""
    dte_resolved = str(getattr(dte_cfg, "account_id", "") or "").strip() if dte_cfg else ""

    routing = {
        "ORB_SO": orb_expected,
        "ORB_0DTE": dte_expected,
        "Trendline_0DTE": dte_expected,
    }
    ids_ok = orb_resolved == orb_expected and dte_resolved == dte_expected
    canonical_ok = orb_expected == EXPECTED_ORB_ACCOUNT_ID and dte_expected == EXPECTED_OPTIONS_ACCOUNT_ID
    routing_ok = routing == STRATEGY_ACCOUNT_ROUTING

    dte_selected_id = None
    dte_selected_status = None
    et = getattr(etrade_options_api, "etrade", None) if etrade_options_api else None
    if et is None and etrade_trader is not None:
        et = etrade_trader
    selected = getattr(et, "selected_account", None) if et else None
    if selected:
        dte_selected_id = str(getattr(selected, "account_id", "") or "")
        dte_selected_status = str(getattr(selected, "account_status", "") or "")

    dte_selected_ok = (
        dte_selected_id is None or str(dte_selected_id) == dte_expected
    )
    pass_ok = bool(ids_ok and canonical_ok and routing_ok and dte_selected_ok)

    audit = {
        "pass": pass_ok,
        "orb_account": orb_expected,
        "options_account": dte_expected,
        "orb_resolved": orb_resolved,
        "options_resolved": dte_resolved,
        "orb_account_expected": EXPECTED_ORB_ACCOUNT_ID,
        "options_account_expected": EXPECTED_OPTIONS_ACCOUNT_ID,
        "routing": routing,
        "routing_ok": routing_ok,
        "dte_selected_account_id": dte_selected_id,
        "dte_account_status": dte_selected_status,
        "dte_selected_ok": dte_selected_ok,
    }
    _log_audit("BROKER_ACCOUNT_ROUTING_AUDIT", audit)
    return audit


def _build_option_order_from_scenario(spec: Dict[str, Any]) -> OptionOrderRequest:
    return OptionOrderRequest(
        symbol=str(spec["symbol"]).upper(),
        expiry=_normalize_expiry(str(spec.get("expiry") or "")),
        option_type=str(spec.get("option_type") or "call").lower(),
        strike=float(spec.get("strike") or 0.0),
        quantity=int(spec.get("quantity") or 1),
        side="BUY",
        bid=float(spec.get("premium") or 0.0) * 0.98,
        ask=float(spec.get("premium") or 0.0) * 1.02,
        mid=float(spec.get("premium") or 0.0),
        strategy="ORB_0DTE",
        trade_id=f"CERT_{spec.get('symbol')}_{spec.get('option_type')}",
    )


async def audit_broker_strategy_compatibility(
    *,
    broker_adapter: Any = None,
    etrade_options_api: Any = None,
    options_level: Any = None,
    account_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Construct ORB1 Breakout winner OptionOrderRequest payloads and validate readiness.
    Never submits orders.
    """
    from modules.broker_certification_probe import _build_option_preview_payload, _preview_option_order
    from modules.options_level_audit import audit_options_level, required_options_level_launch_profile

    target_account = account_id or _expected_options_account_id()
    et = getattr(etrade_options_api, "etrade", None) if etrade_options_api else None
    if et is None and broker_adapter is not None:
        et = getattr(broker_adapter, "etrade_trading", None)

    selected = getattr(et, "selected_account", None) if et else None
    account_authorized = bool(
        selected and str(getattr(selected, "account_id", "")) == str(target_account)
    )
    if et is not None and target_account and hasattr(et, "select_account"):
        try:
            et.select_account(target_account)
            selected = getattr(et, "selected_account", None)
            account_authorized = bool(
                selected and str(getattr(selected, "account_id", "")) == str(target_account)
            )
        except Exception as exc:
            log.warning("BROKER_STRATEGY_COMPATIBILITY_AUDIT | account_select_error=%s", exc)

    level_audit = audit_options_level(options_level, account_id=target_account)
    options_level_ok = bool(level_audit.get("pass"))
    preview_supported = et is not None and bool(getattr(et, "_make_etrade_api_call", None))
    adapter_ok = bool(
        broker_adapter and getattr(broker_adapter, "is_available", lambda: False)()
    )

    cases: List[Dict[str, Any]] = []
    all_pass = True

    for spec in _load_winner_scenarios():
        case: Dict[str, Any] = {"label": spec.get("label") or spec.get("symbol"), "symbol": spec.get("symbol")}
        errors: List[str] = []
        try:
            order = _build_option_order_from_scenario(spec)
            case["payload_built"] = True
            case["trade_id"] = order.trade_id
        except Exception as exc:
            case["payload_built"] = False
            errors.append(f"payload_build:{exc}")
            all_pass = False
            case["pass"] = False
            case["errors"] = errors
            cases.append(case)
            log.info(
                "BROKER_STRATEGY_COMPATIBILITY_AUDIT | symbol=%s | pass=false | errors=%s",
                spec.get("symbol"),
                errors,
            )
            continue

        qty_ok = _quantity_valid(order.quantity)
        strike_ok = _strike_valid(order.strike)
        expiry_ok = _expiry_valid(order.expiry)
        if not qty_ok:
            errors.append("quantity_invalid")
        if not strike_ok:
            errors.append("strike_invalid")
        if not expiry_ok:
            errors.append("expiration_invalid")
        if not account_authorized:
            errors.append("account_not_authorized")
        if not options_level_ok:
            errors.append("options_level_insufficient")

        preview_ok = None
        if preview_supported and qty_ok and strike_ok and expiry_ok:
            payload = _build_option_preview_payload(
                symbol=order.symbol,
                expiry_ymd=order.expiry,
                option_type=order.option_type,
                strike=order.strike,
                order_action="BUY_OPEN",
                quantity=order.quantity,
            )
            ok, detail = await _preview_option_order(et, payload)
            preview_ok = ok
            case["preview"] = detail
            if not ok:
                errors.append("preview_rejected")
        elif not preview_supported:
            preview_ok = None
            case["preview"] = {"note": "preview_unavailable"}

        case_ok = (
            case["payload_built"]
            and qty_ok
            and strike_ok
            and expiry_ok
            and account_authorized
            and options_level_ok
            and (preview_ok is not False)
        )
        if not case_ok:
            all_pass = False
        case.update(
            {
                "quantity_valid": qty_ok,
                "strike_valid": strike_ok,
                "expiration_valid": expiry_ok,
                "account_authorized": account_authorized,
                "options_level_ok": options_level_ok,
                "preview_ok": preview_ok,
                "pass": case_ok,
                "errors": errors or None,
            }
        )
        cases.append(case)
        log.info(
            "BROKER_STRATEGY_COMPATIBILITY_AUDIT | symbol=%s | option_type=%s | "
            "payload_built=%s | preview_ok=%s | pass=%s",
            order.symbol,
            order.option_type,
            str(case["payload_built"]).lower(),
            preview_ok,
            str(case_ok).lower(),
        )

    audit = {
        "pass": all_pass,
        "account_id": target_account,
        "account_authorized": account_authorized,
        "options_level_ok": options_level_ok,
        "required_level": required_options_level_launch_profile(),
        "preview_supported": preview_supported,
        "adapter_available": adapter_ok,
        "cases": cases,
    }
    _log_audit("BROKER_STRATEGY_COMPATIBILITY_AUDIT", audit)
    return audit


async def audit_broker_close_compatibility(
    *,
    broker_adapter: Any = None,
    etrade_options_api: Any = None,
    account_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Validate SELL_TO_CLOSE payload construction and preview for winner contracts.
    Never submits orders.
    """
    from modules.broker_certification_probe import _build_option_preview_payload, _preview_option_order

    target_account = account_id or _expected_options_account_id()
    et = getattr(etrade_options_api, "etrade", None) if etrade_options_api else None
    if et is None and broker_adapter is not None:
        et = getattr(broker_adapter, "etrade_trading", None)

    selected = getattr(et, "selected_account", None) if et else None
    account_authorized = bool(
        selected and str(getattr(selected, "account_id", "")) == str(target_account)
    )
    if et is not None and target_account and hasattr(et, "select_account"):
        try:
            et.select_account(target_account)
            selected = getattr(et, "selected_account", None)
            account_authorized = bool(
                selected and str(getattr(selected, "account_id", "")) == str(target_account)
            )
        except Exception as exc:
            log.warning("BROKER_CLOSE_COMPATIBILITY_AUDIT | account_select_error=%s", exc)

    has_close = bool(getattr(broker_adapter, "submit_close_order", None)) if broker_adapter else False
    preview_supported = et is not None and bool(getattr(et, "_make_etrade_api_call", None))

    cases: List[Dict[str, Any]] = []
    all_pass = True

    for spec in _load_winner_scenarios():
        label = spec.get("label") or spec.get("symbol")
        symbol = str(spec.get("symbol") or "").upper()
        otype = str(spec.get("option_type") or "call").lower()
        strike = float(spec.get("strike") or 0.0)
        expiry = _normalize_expiry(str(spec.get("expiry") or ""))
        qty = int(spec.get("quantity") or 1)
        contract = _build_occ_symbol(symbol=symbol, expiry=expiry, option_type=otype, strike=strike)
        errors: List[str] = []

        try:
            close_req = BrokerCloseRequest(
                position_id=f"CERT_CLOSE_{symbol}",
                symbol=symbol,
                quantity=qty,
                exit_price=None,
                order_type="MARKET",
                strategy="ORB_0DTE",
                position=None,
            )
            payload_built = True
        except Exception as exc:
            payload_built = False
            errors.append(f"payload_build:{exc}")
            all_pass = False
            cases.append(
                {
                    "label": label,
                    "symbol": symbol,
                    "contract": contract,
                    "payload_built": False,
                    "pass": False,
                    "errors": errors,
                }
            )
            continue

        contract_valid = _strike_valid(strike) and _expiry_valid(expiry) and bool(symbol)
        if not contract_valid:
            errors.append("contract_invalid")
        if not account_authorized:
            errors.append("account_not_authorized")
        if not has_close:
            errors.append("adapter_close_missing")

        preview_ok = None
        if preview_supported and contract_valid:
            preview_payload = _build_option_preview_payload(
                symbol=symbol,
                expiry_ymd=expiry,
                option_type=otype,
                strike=strike,
                order_action="SELL_CLOSE",
                quantity=qty,
            )
            ok, detail = await _preview_option_order(et, preview_payload)
            preview_ok = ok
            if not ok:
                errors.append("preview_rejected")
        elif not preview_supported:
            preview_ok = None

        case_ok = (
            payload_built
            and contract_valid
            and account_authorized
            and has_close
            and (preview_ok is not False)
        )
        if not case_ok:
            all_pass = False

        row = {
            "label": label,
            "symbol": symbol,
            "contract": contract,
            "quantity": qty,
            "payload_built": payload_built,
            "contract_valid": contract_valid,
            "account_authorized": account_authorized,
            "adapter_close_present": has_close,
            "preview_ok": preview_ok,
            "pass": case_ok,
            "errors": errors or None,
        }
        cases.append(row)
        log.info(
            "BROKER_CLOSE_COMPATIBILITY_AUDIT | symbol=%s | contract=%s | "
            "payload_built=%s | preview_ok=%s | pass=%s",
            symbol,
            contract,
            str(payload_built).lower(),
            preview_ok,
            str(case_ok).lower(),
        )

    audit = {
        "pass": all_pass,
        "account_id": target_account,
        "account_authorized": account_authorized,
        "adapter_close_present": has_close,
        "preview_supported": preview_supported,
        "cases": cases,
    }
    _log_audit("BROKER_CLOSE_COMPATIBILITY_AUDIT", audit)
    return audit


async def audit_broker_position_flat(
    *,
    broker_adapter: Any,
    position: Any,
    symbol: Optional[str] = None,
) -> Dict[str, Any]:
    """
    After a successful Live close, verify broker shows flat for the contract.
    """
    sym = symbol or str(getattr(position, "symbol", "") or "").strip().upper()
    _, contract = _position_contract_key(position)
    broker_qty: Optional[float] = None
    flat = False
    error = None

    if broker_adapter is None or not getattr(broker_adapter, "is_available", lambda: False)():
        error = "broker_adapter_unavailable"
    else:
        try:
            broker_positions = await broker_adapter.get_positions()
            keys = _broker_position_keys(broker_positions or [])
            row = keys.get(contract.upper()) or keys.get(sym)
            if row is None:
                flat = True
                broker_qty = 0.0
            else:
                broker_qty = float(row.get("quantity") or 0.0)
                flat = abs(broker_qty) < 1e-9
        except Exception as exc:
            error = str(exc)

    audit = {
        "symbol": sym,
        "contract": contract,
        "broker_qty": broker_qty,
        "flat": flat if error is None else False,
        "pass": flat if error is None else False,
        "error": error,
    }
    _log_audit("BROKER_POSITION_FLAT_AUDIT", audit)
    if error is None and not flat:
        log.error(
            "LIVE_POSITION_NOT_FLAT | severity=critical | symbol=%s | contract=%s | broker_qty=%s",
            sym,
            contract,
            broker_qty,
        )
    return audit


def _local_position_registry(executor: Any) -> Dict[str, Any]:
    return dict(getattr(executor, "positions", None) or {})


async def audit_live_orphan_positions(
    *,
    broker_adapter: Any,
    options_executor: Any,
    lifecycle: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Compare local executor registry vs broker get_positions().
    Emits LIVE_ORPHAN_POSITION_AUDIT and LIVE_POSITION_MISMATCH when drift detected.
    """
    if options_executor is None:
        audit = {"pass": True, "skipped": True, "reason": "no_executor"}
        _log_audit("LIVE_ORPHAN_POSITION_AUDIT", audit)
        return audit

    if getattr(options_executor, "demo_mode", True):
        audit = {"pass": True, "skipped": True, "reason": "demo_mode"}
        _log_audit("LIVE_ORPHAN_POSITION_AUDIT", audit)
        return audit

    if broker_adapter is None or not getattr(broker_adapter, "is_available", lambda: False)():
        audit = {"pass": False, "error": "broker_adapter_unavailable"}
        _log_audit("LIVE_ORPHAN_POSITION_AUDIT", audit)
        return audit

    local_registry = _local_position_registry(options_executor)
    broker_rows = await broker_adapter.get_positions()
    broker_keys = _broker_position_keys(broker_rows or [])

    local_by_contract: Dict[str, Dict[str, Any]] = {}
    for pid, pos in local_registry.items():
        sym, contract = _position_contract_key(pos)
        local_by_contract[contract.upper()] = {
            "position_id": pid,
            "symbol": sym,
            "contract": contract,
            "quantity": int(getattr(pos, "quantity", 0) or 0),
            "strike": float(getattr(getattr(pos, "lotto_contract", None), "strike", 0.0) or 0.0),
        }

    broker_only: List[str] = []
    local_only: List[str] = []
    qty_mismatch: List[Dict[str, Any]] = []
    strike_mismatch: List[Dict[str, Any]] = []

    for contract_key, local in local_by_contract.items():
        broker_row = broker_keys.get(contract_key)
        if broker_row is None:
            # Try OCC full symbol match
            broker_row = broker_keys.get(local["contract"].upper())
        if broker_row is None:
            local_only.append(local["position_id"])
            continue
        bqty = float(broker_row.get("quantity") or 0.0)
        lqty = float(local["quantity"])
        if abs(bqty - lqty) > 1e-9:
            qty_mismatch.append(
                {
                    "position_id": local["position_id"],
                    "contract": contract_key,
                    "local_qty": lqty,
                    "broker_qty": bqty,
                }
            )

    seen_local_contracts = {v["contract"].upper() for v in local_by_contract.values()}
    for bkey, brow in broker_keys.items():
        bqty = float(brow.get("quantity") or 0.0)
        if bqty == 0:
            continue
        sym = str(brow.get("symbol") or bkey)
        if " " in sym:
            occ = sym.upper()
        else:
            occ = sym.upper()
        if occ not in seen_local_contracts and sym.upper() not in seen_local_contracts:
            # Avoid counting underlying-only index entries twice
            if " " in sym or re.search(r"[CP]\d{8}$", sym.replace(" ", "")):
                broker_only.append(sym)

    mismatches = bool(broker_only or local_only or qty_mismatch or strike_mismatch)
    pass_ok = not mismatches

    audit = {
        "pass": pass_ok,
        "local_count": len(local_registry),
        "broker_count": len(broker_rows or []),
        "broker_only": broker_only,
        "local_only": local_only,
        "qty_mismatch": qty_mismatch,
        "strike_mismatch": strike_mismatch,
        "lifecycle_open": len(
            [
                tid
                for tid, rec in (lifecycle or {}).items()
                if isinstance(rec, dict) and not bool(rec.get("close_confirmed"))
            ]
        ),
    }
    _log_audit("LIVE_ORPHAN_POSITION_AUDIT", audit)

    if mismatches:
        log.error(
            "LIVE_POSITION_MISMATCH | broker_only=%s | local_only=%s | qty_mismatch=%s | strike_mismatch=%s",
            broker_only,
            local_only,
            qty_mismatch,
            strike_mismatch,
        )
    return audit


@dataclass
class _LifecycleClosedRow:
    realized_pnl: float
    entry_time: datetime
    closed_at: Optional[datetime] = None
    position_id: str = ""
    trade_id: str = ""
    strategy_type: str = ""


def _parse_iso_ts(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        text = str(raw).replace("Z", "+00:00")
        return datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return None


def lifecycle_closed_positions(lifecycle: Dict[str, Any]) -> List[_LifecycleClosedRow]:
    """Build EOD-compatible closed rows from ORB1 Breakout lifecycle registry."""
    rows: List[_LifecycleClosedRow] = []
    for _tid, rec in (lifecycle or {}).items():
        if not isinstance(rec, dict) or not bool(rec.get("close_confirmed")):
            continue
        cc = dict(rec.get("close_confirmation") or {})
        pnl = float(cc.get("realized_pnl_dollar") or 0.0)
        closed_at = _parse_iso_ts(cc.get("close_confirmed_timestamp"))
        entry_time = closed_at or datetime.now(timezone.utc)
        trade_id = str(rec.get("trade_id") or _tid or "").strip()
        position_id = str(rec.get("position_id") or trade_id or "").strip()
        strategy_type = str(rec.get("strategy_type") or "").strip()
        rows.append(
            _LifecycleClosedRow(
                realized_pnl=pnl,
                entry_time=entry_time,
                closed_at=closed_at,
                position_id=position_id,
                trade_id=trade_id,
                strategy_type=strategy_type,
            )
        )
    return rows


def payload_from_live_broker_truth(
    system: Any,
    options_executor: Any,
) -> Dict[str, Any]:
    """
    ORB1 Breakout EOD payload from durable ledger + lifecycle close_confirmation + broker balance.
    DEMO: GCS mock ledger + warm mock executor + lifecycle.
    LIVE: GCS live ledger + lifecycle (via live_options_ledger when available).
    """
    from modules.eod_report_helpers import (
        empty_trade_stats,
        filter_positions_by_pt_date,
        reconcile_options_eod_stats,
        stats_dict_from_positions,
    )
    from modules.runtime_mode import is_demo_mode, mode_label

    demo_mode = bool(getattr(options_executor, "demo_mode", is_demo_mode()))

    if demo_mode:
        from modules.eod_report_helpers import collect_demo_eod_closed_rows

        closed, source_tag = collect_demo_eod_closed_rows(system)
    else:
        lifecycle_rows = lifecycle_closed_positions(dict(getattr(system, "_orb_0dte_lifecycle", None) or {}))
        if lifecycle_rows:
            closed = lifecycle_rows
            source_tag = "broker_lifecycle"
        else:
            try:
                from modules.live_options_ledger import collect_live_eod_closed_rows

                closed, source_tag = collect_live_eod_closed_rows(system)
            except ImportError:
                closed = []
                source_tag = "broker_lifecycle"

    daily_stats, weekly_stats, _recovered = reconcile_options_eod_stats(
        empty_trade_stats(),
        empty_trade_stats(),
        closed,
    )
    if closed:
        today_positions, week_positions, _ = filter_positions_by_pt_date(closed)
        daily_stats = stats_dict_from_positions(today_positions or closed)
        weekly_stats = stats_dict_from_positions(week_positions)

    all_time_stats = stats_dict_from_positions(closed) if closed else None

    account_balance = 0.0
    starting_balance = float(
        os.getenv("0DTE_LIVE_STARTING_BALANCE", os.getenv("0DTE_DEMO_STARTING_BALANCE", "5000"))
    )
    adapter = getattr(options_executor, "broker_adapter", None)
    et = getattr(adapter, "etrade_trading", None) if adapter else None
    if et is not None and hasattr(et, "get_account_balance"):
        try:
            bal_obj = et.get_account_balance()
            if bal_obj:
                account_balance = float(getattr(bal_obj, "account_value", 0.0) or 0.0)
        except Exception as exc:
            log.warning("payload_from_live_broker_truth | balance_read_failed | %s", exc)

    if demo_mode and account_balance <= 0:
        live_mock = getattr(options_executor, "mock_executor", None)
        if live_mock is not None:
            account_balance = float(getattr(live_mock, "account_balance", 0.0) or 0.0)
        if account_balance <= 0:
            gcs_payload = None
            try:
                from modules.eod_report_helpers import load_0dte_demo_stats_from_gcs

                gcs_payload = load_0dte_demo_stats_from_gcs()
            except Exception:
                gcs_payload = None
            if gcs_payload:
                account_balance = float(gcs_payload.get("account_balance", 0.0) or 0.0)
                if not all_time_stats and gcs_payload.get("all_time_stats"):
                    all_time_stats = gcs_payload.get("all_time_stats")

    if all_time_stats is None and account_balance > 0 and starting_balance > 0:
        equity_pnl = account_balance - starting_balance
        if abs(equity_pnl) > 1e-6:
            all_time_stats = {
                "positions_closed": 0,
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "total_pnl": equity_pnl,
                "best_trade": 0.0,
                "worst_trade": 0.0,
                "total_wins_sum": max(0.0, equity_pnl),
                "total_losses_sum": abs(min(0.0, equity_pnl)),
            }

    return {
        "daily_stats": daily_stats,
        "weekly_stats": weekly_stats,
        "all_time_stats": all_time_stats,
        "account_balance": account_balance,
        "starting_balance": starting_balance,
        "mode": mode_label(),
        "source": source_tag,
        "closed_count": len(closed),
    }


async def payload_from_live_broker_truth_async(
    system: Any,
    options_executor: Any,
) -> Dict[str, Any]:
    """Async ORB1 Breakout Live EOD payload with broker balance."""
    from modules.eod_report_helpers import empty_trade_stats, filter_positions_by_pt_date, reconcile_options_eod_stats, stats_dict_from_positions
    from modules.runtime_mode import mode_label

    lifecycle = dict(getattr(system, "_orb_0dte_lifecycle", None) or {})
    closed = lifecycle_closed_positions(lifecycle)
    daily_stats, weekly_stats, _recovered = reconcile_options_eod_stats(
        empty_trade_stats(),
        empty_trade_stats(),
        closed,
    )
    if closed:
        daily_stats = stats_dict_from_positions(closed)
        _, week_positions, _ = filter_positions_by_pt_date(closed)
        weekly_stats = stats_dict_from_positions(week_positions)

    account_balance = 0.0
    starting_balance = float(
        os.getenv("0DTE_LIVE_STARTING_BALANCE", os.getenv("0DTE_DEMO_STARTING_BALANCE", "5000"))
    )
    adapter = getattr(options_executor, "broker_adapter", None)
    if adapter and getattr(adapter, "is_available", lambda: False)():
        try:
            bal = await adapter.get_account_balance()
            if bal:
                account_balance = float(getattr(bal, "account_value", 0.0) or 0.0)
        except Exception as exc:
            log.warning("payload_from_live_broker_truth_async | balance_read_failed | %s", exc)

    return {
        "daily_stats": daily_stats,
        "weekly_stats": weekly_stats,
        "all_time_stats": None,
        "account_balance": account_balance,
        "starting_balance": starting_balance,
        "mode": mode_label(),
        "source": "broker_lifecycle",
        "closed_count": len(closed),
    }


async def audit_broker_eod_reconciliation(
    *,
    system: Any,
    options_executor: Any,
    broker_adapter: Any = None,
    flatten_status: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    EOD broker truth: registry vs executor vs broker positions; entry/exit fill counts.
    """
    lifecycle = dict(getattr(system, "_orb_0dte_lifecycle", None) or {})
    registry_open = [
        tid
        for tid, rec in lifecycle.items()
        if isinstance(rec, dict) and not bool(rec.get("close_confirmed"))
    ]
    executor_open = options_executor.get_open_positions() if options_executor else []
    executor_open_count = len(executor_open or [])

    entry_fills = sum(
        1
        for rec in lifecycle.values()
        if isinstance(rec, dict) and "entry_confirmed" in (rec.get("state_history") or set())
        or str(rec.get("state", "")).lower() in ("entry_confirmed", "monitoring", "exit_confirmed", "exit_signalled")
    )
    exit_fills = sum(
        1 for rec in lifecycle.values() if isinstance(rec, dict) and bool(rec.get("close_confirmed"))
    )
    realized_pnl = sum(
        float((rec.get("close_confirmation") or {}).get("realized_pnl_dollar") or 0.0)
        for rec in lifecycle.values()
        if isinstance(rec, dict) and bool(rec.get("close_confirmed"))
    )

    broker_open_count = 0
    broker_positions: List[Dict[str, Any]] = []
    adapter = broker_adapter or getattr(options_executor, "broker_adapter", None)
    if adapter and getattr(adapter, "is_available", lambda: False)():
        try:
            broker_positions = await adapter.get_positions()
            broker_open_count = sum(
                1
                for p in (broker_positions or [])
                if abs(float(p.get("quantity") or p.get("qty") or 0.0)) > 0
            )
        except Exception as exc:
            log.warning("BROKER_EOD_RECONCILIATION_AUDIT | broker_positions_error=%s", exc)

    fs = flatten_status or {}
    closed = int(fs.get("positions_closed", 0) or 0)
    remaining_executor = int(fs.get("positions_missing", executor_open_count) or executor_open_count)
    orphan_registry = len(registry_open)
    orphan_broker = broker_open_count

    pass_ok = (
        remaining_executor == 0
        and broker_open_count == 0
        and len(registry_open) == 0
    )

    audit = {
        "pass": pass_ok,
        "registry_open": len(registry_open),
        "executor_open": executor_open_count,
        "broker_open": broker_open_count,
        "entry_fills": entry_fills,
        "exit_fills": exit_fills,
        "realized_pnl": round(realized_pnl, 2),
        "contracts_closed": closed,
        "remaining_executor": remaining_executor,
        "remaining_broker": broker_open_count,
        "orphan_registry": orphan_registry,
        "orphan_broker": orphan_broker,
    }
    _log_audit("BROKER_EOD_RECONCILIATION_AUDIT", audit)
    return audit
