#!/usr/bin/env python3
"""
Fail-closed aggregate Live preflight — individual gate reasons, no orders.

Runs (or stubs) the key Live gates and returns ready=false unless every
required gate passes. Never submits broker orders.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

EXPECTED_QUOTE_ACCOUNT = "YOUR_ORB_ACCOUNT_ID"
EXPECTED_OPTIONS_ACCOUNT = "YOUR_0DTE_ACCOUNT_ID"


async def run_live_preflight(
    *,
    broker_adapter: Any = None,
    etrade_oauth: Any = None,
    etrade_options_api: Any = None,
    market_manager: Any = None,
    etrade_trader: Any = None,
    run_broker_cert: bool = True,
) -> Dict[str, Any]:
    """
    Aggregate Live readiness gates. Fail-closed: ready=true only if all
    required gates pass. Each gate includes ok + reason.
    """
    from modules.runtime_mode import is_live_mode, mode_label, resolve_broker_kind

    gates: Dict[str, Any] = {}
    errors: List[str] = []

    # --- MODE ---
    live = is_live_mode()
    gates["mode"] = {
        "ok": live,
        "value": mode_label(),
        "reason": "ok" if live else "MODE must be live",
    }
    if not live:
        errors.append("mode_not_live")

    # --- BROKER ---
    broker = resolve_broker_kind()
    gates["broker"] = {
        "ok": broker == "etrade",
        "value": broker,
        "reason": "ok" if broker == "etrade" else f"broker_not_etrade:{broker}",
    }
    if broker != "etrade":
        errors.append(f"broker_not_etrade:{broker}")

    # --- SPREADS / LOTTO ---
    spreads = str(os.getenv("LIVE_SPREADS_ENABLED", "false") or "false").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    gates["live_spreads_disabled"] = {
        "ok": not spreads,
        "value": spreads,
        "reason": "ok" if not spreads else "LIVE_SPREADS_ENABLED must be false",
    }
    if spreads:
        errors.append("live_spreads_enabled")

    def _env_float(name: str, default: float) -> float:
        try:
            return float(os.getenv(name, str(default)) or default)
        except (TypeError, ValueError):
            return default

    def _env_int(name: str, default: int) -> int:
        try:
            return int(float(os.getenv(name, str(default)) or default))
        except (TypeError, ValueError):
            return default

    def _env_bool(name: str, default: bool) -> bool:
        raw = os.getenv(name, "true" if default else "false")
        return str(raw).strip().lower() in ("1", "true", "yes", "on")

    max_contracts = _env_int("LIVE_MAX_CONTRACTS_PER_TRADE", 0)
    gates["live_max_contracts"] = {
        "ok": max_contracts == 0,
        "value": max_contracts,
        "reason": "ok" if max_contracts == 0 else "LIVE_MAX_CONTRACTS_PER_TRADE must be 0 for liquidity-native sizing",
    }
    if max_contracts != 0:
        errors.append(f"live_max_contracts:{max_contracts}")

    volume_pct = _env_float("LIVE_OPTION_VOLUME_PARTICIPATION_PCT", 0.0)
    oi_pct = _env_float("LIVE_OPTION_OI_PARTICIPATION_PCT", 0.0)
    depth_pct = _env_float("LIVE_OPTION_BOOK_DEPTH_PARTICIPATION_PCT", 2.0)
    exit_depth_pct = _env_float("LIVE_OPTION_EXIT_BOOK_DEPTH_PARTICIPATION_PCT", 0.0)
    adv_pct = _env_float("LIVE_UNDERLYING_ADV_PARTICIPATION_PCT", 0.02)
    min_ask = _env_int("LIVE_MIN_ASK_SIZE", 1)
    min_bid = _env_int("LIVE_MIN_BID_SIZE", 1)
    refresh_quotes = _env_bool("LIVE_REFRESH_QUOTES_BEFORE_SUBMIT", True)
    # v1.6: volume and OI qty caps may be 0 (disabled). Ask-depth, ADV, min sizes, refresh required.
    liquidity_ok = (
        volume_pct >= 0
        and depth_pct > 0
        and adv_pct > 0
        and min_ask > 0
        and min_bid > 0
        and refresh_quotes
        and oi_pct >= 0
        and exit_depth_pct >= 0
    )
    gates["live_liquidity_governors"] = {
        "ok": liquidity_ok,
        "value": {
            "volume_pct": volume_pct,
            "oi_pct": oi_pct,
            "book_depth_pct": depth_pct,
            "exit_book_depth_pct": exit_depth_pct,
            "underlying_adv_pct": adv_pct,
            "min_ask_size": min_ask,
            "min_bid_size": min_bid,
            "refresh_quotes": refresh_quotes,
        },
        "reason": "ok" if liquidity_ok else "liquidity-native sizing requires ask-depth/ADV/min-size guards and quote refresh (vol/OI may be 0)",
    }
    if not liquidity_ok:
        errors.append("live_liquidity_governors_invalid")

    # --- ACCOUNT IDS ---
    quote_acct = str(
        os.getenv("ETRADE_ORB_ACCOUNT_ID")
        or os.getenv("ETRADE_DEFAULT_ACCOUNT_ID")
        or ""
    ).strip()
    options_acct = str(
        os.getenv("ETRADE_0DTE_ACCOUNT_ID")
        or os.getenv("0DTE_ETRADE_ACCOUNT_ID")
        or ""
    ).strip()
    try:
        from modules.live_readiness_probe import resolve_options_account_id

        resolved = resolve_options_account_id(
            etrade_options_api, etrade_trader=etrade_trader
        )
        if resolved:
            options_acct = str(resolved).strip()
    except Exception as exc:
        log.warning("LIVE_PREFLIGHT | resolve_options_account_id failed: %s", exc)

    gates["quote_account"] = {
        "ok": quote_acct == EXPECTED_QUOTE_ACCOUNT,
        "value": quote_acct or None,
        "expected": EXPECTED_QUOTE_ACCOUNT,
        "reason": (
            "ok"
            if quote_acct == EXPECTED_QUOTE_ACCOUNT
            else f"quote_account_mismatch:{quote_acct or 'missing'}"
        ),
    }
    if quote_acct != EXPECTED_QUOTE_ACCOUNT:
        errors.append(gates["quote_account"]["reason"])

    gates["options_account"] = {
        "ok": options_acct == EXPECTED_OPTIONS_ACCOUNT,
        "value": options_acct or None,
        "expected": EXPECTED_OPTIONS_ACCOUNT,
        "reason": (
            "ok"
            if options_acct == EXPECTED_OPTIONS_ACCOUNT
            else f"options_account_mismatch:{options_acct or 'missing'}"
        ),
    }
    if options_acct != EXPECTED_OPTIONS_ACCOUNT:
        errors.append(gates["options_account"]["reason"])

    # --- READINESS PROBE (no trade) ---
    try:
        from modules.live_readiness_probe import run_live_readiness_probe

        probe = await run_live_readiness_probe(
            broker_adapter=broker_adapter,
            etrade_oauth=etrade_oauth,
            etrade_options_api=etrade_options_api,
            market_manager=market_manager,
        )
        probe_ok = bool(probe.get("ready") or probe.get("ok"))
        gates["spy_probe"] = {
            "ok": probe_ok,
            "value": probe,
            "reason": "ok" if probe_ok else "live_readiness_probe_failed",
        }
        if not probe_ok:
            errors.append("spy_probe_failed")
            for e in probe.get("errors") or []:
                errors.append(f"probe:{e}")
    except Exception as exc:
        gates["spy_probe"] = {"ok": False, "reason": f"spy_probe_exception:{exc}"}
        errors.append(f"spy_probe_exception:{exc}")

    # --- BROKER CERT (read-only / current expiry) ---
    if run_broker_cert:
        try:
            from modules.broker_certification_probe import run_broker_certification_probe

            cert = await run_broker_certification_probe(
                broker_adapter=broker_adapter,
                etrade_oauth=etrade_oauth,
                etrade_options_api=etrade_options_api,
            )
            cert_ok = bool(cert.get("passed") or cert.get("ok") or cert.get("ready"))
            gates["broker_certification"] = {
                "ok": cert_ok,
                "value": {k: cert.get(k) for k in ("passed", "ok", "ready", "errors", "summary") if k in cert},
                "reason": "ok" if cert_ok else "broker_certification_failed",
            }
            if not cert_ok:
                errors.append("broker_certification_failed")
        except Exception as exc:
            gates["broker_certification"] = {
                "ok": False,
                "reason": f"broker_cert_exception:{exc}",
            }
            errors.append(f"broker_cert_exception:{exc}")
    else:
        gates["broker_certification"] = {
            "ok": False,
            "reason": "skipped",
            "skipped": True,
        }

    ready = len(errors) == 0 and all(
        bool(g.get("ok")) for g in gates.values() if not g.get("skipped")
    )
    out = {
        "ready": ready,
        "ok": ready,
        "fail_closed": True,
        "errors": errors,
        "gates": gates,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    log.info(
        "LIVE_PREFLIGHT | ready=%s | errors=%s | gates=%s",
        str(ready).lower(),
        errors,
        {k: v.get("ok") for k, v in gates.items()},
    )
    return out
