#!/usr/bin/env python3
"""Startup validation for unified 0DTE portfolio risk."""

from __future__ import annotations

import logging
import os
from typing import Any, List, Optional

from modules.broker_router import load_broker_account_manifest, parse_broker_account_manifest
from modules.unified_0dte_portfolio_risk_manager import Unified0DTERiskConfig

log = logging.getLogger(__name__)


def _is_live_mode() -> bool:
    try:
        from modules.runtime_mode import is_live_mode

        return bool(is_live_mode())
    except ImportError:
        return str(os.getenv("MODE", "")).strip().lower() == "live"


def validate_unified_0dte_startup(
    *,
    system: Any,
    options_executor: Any,
    broker_router: Any,
    broker_adapter: Any,
    demo_mode: Optional[bool] = None,
) -> None:
    """
    Validate unified 0DTE wiring at startup.

    Raises RuntimeError when UNIFIED_0DTE_RISK_ENABLED=true and requirements fail.
    """
    cfg = Unified0DTERiskConfig.from_env()
    live = _is_live_mode() if demo_mode is None else not bool(demo_mode)
    errors: List[str] = []

    if not cfg.enabled:
        log.info(
            "UNIFIED_0DTE_STARTUP_AUDIT | enabled=false | skipped=true | live=%s",
            str(live).lower(),
        )
        return

    if getattr(options_executor, "trading_system", None) is None:
        errors.append("trading_system_not_wired")

    if broker_router is None:
        errors.append("broker_router_missing")

    raw_manifest = os.getenv("BROKER_ACCOUNTS_JSON", "").strip()
    manifest = []
    if raw_manifest:
        try:
            manifest = parse_broker_account_manifest(raw_manifest)
        except ValueError as exc:
            errors.append(f"broker_manifest_invalid:{exc}")
    else:
        manifest = load_broker_account_manifest(None)

    if cfg.orb_max_positions <= 0:
        errors.append("orb_cap_invalid")
    if cfg.trendline_max_positions <= 0:
        errors.append("trendline_cap_invalid")
    if cfg.max_total_positions < cfg.orb_max_positions:
        errors.append("combined_cap_below_orb_cap")
    if cfg.max_total_positions < cfg.trendline_max_positions:
        errors.append("combined_cap_below_trendline_cap")
    max_path = max(cfg.orb_max_positions, cfg.trendline_max_positions)
    if cfg.max_total_positions < max_path:
        errors.append("combined_cap_below_max_path_cap")

    if live and cfg.fail_closed:
        if broker_adapter is None or not getattr(
            broker_adapter, "is_available", lambda: False
        )():
            errors.append("broker_adapter_unavailable")

    if not manifest and not raw_manifest:
        errors.append("broker_manifest_empty_after_load")

    audit_fields = (
        f"enabled=true | live={str(live).lower()} | "
        f"orb_cap={cfg.orb_max_positions} | trendline_cap={cfg.trendline_max_positions} | "
        f"combined_cap={cfg.max_total_positions} | manifest_accounts={len(manifest)} | "
        f"trading_system_wired={str(getattr(options_executor, 'trading_system', None) is not None).lower()} | "
        f"router_wired={str(broker_router is not None).lower()} | "
        f"broker_available={str(broker_adapter is not None and getattr(broker_adapter, 'is_available', lambda: False)()).lower()}"
    )

    if errors:
        log.error(
            "UNIFIED_0DTE_STARTUP_AUDIT | status=fail | %s | errors=%s",
            audit_fields,
            ",".join(errors),
        )
        raise RuntimeError(
            "Unified 0DTE startup validation failed: " + "; ".join(errors)
        )

    log.info("UNIFIED_0DTE_STARTUP_AUDIT | status=pass | %s", audit_fields)
    if system is not None:
        system._unified_0dte_startup_validated = True
        try:
            from modules.unified_0dte_account import hydrate_unified_0dte_risk_from_account

            pct = hydrate_unified_0dte_risk_from_account(system)
            log.info(
                "UNIFIED_0DTE_ACCOUNT | action=startup_day_pnl_sync | day_pnl_pct=%.4f | "
                "halt_cleared_pt=%s",
                pct,
                str(getattr(system, "_unified_0dte_risk_halt_cleared_pt_date", "") or ""),
            )
        except Exception as sync_err:
            log.warning("UNIFIED_0DTE_ACCOUNT | startup_day_pnl_sync_failed | error=%s", sync_err)
