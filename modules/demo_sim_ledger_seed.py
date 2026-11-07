"""
Seed demo sim ledgers to GCS at research_app/ or final_app/ namespace paths.

Set DEMO_SIM_LEDGER_SEED_ON_STARTUP=true on a one-time migration deploy to reset
mock options + mock SO ledgers to configured seed balances ($0 trade history).
Remove the env var after that deploy so ledgers persist normally.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)


def demo_sim_ledger_seed_on_startup_enabled() -> bool:
    return os.getenv("DEMO_SIM_LEDGER_SEED_ON_STARTUP", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )


def _pt_trading_date_iso() -> str:
    return datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()


def _week_start_iso() -> str:
    today = datetime.now(ZoneInfo("America/Los_Angeles")).date()
    monday = today - timedelta(days=today.weekday())
    return monday.isoformat()


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def get_0dte_demo_starting_balance() -> float:
    try:
        from modules.config_loader import get_config_value

        return float(get_config_value("0DTE_DEMO_STARTING_BALANCE", 5000.0))
    except Exception:
        return _float_env("0DTE_DEMO_STARTING_BALANCE", 5000.0)


def get_so_demo_starting_balance() -> float:
    try:
        from modules.mock_trading_executor import get_so_demo_starting_balance as _so

        return float(_so())
    except Exception:
        return _float_env("SO_DEMO_STARTING_BALANCE", 5000.0)


def build_seed_mock_options_history() -> Dict[str, Any]:
    balance = get_0dte_demo_starting_balance()
    return {
        "account_balance": balance,
        "starting_balance": balance,
        "reserved_margin_total": 0.0,
        "daily_stats": {
            "positions_opened": 0,
            "positions_closed": 0,
            "total_pnl": 0.0,
            "winning_trades": 0,
            "losing_trades": 0,
            "best_trade": 0.0,
            "worst_trade": 0.0,
        },
        "weekly_stats": {
            "positions_opened": 0,
            "positions_closed": 0,
            "total_pnl": 0.0,
            "winning_trades": 0,
            "losing_trades": 0,
            "best_trade": 0.0,
            "worst_trade": 0.0,
            "week_start_date": _week_start_iso(),
        },
        "stats_trading_date": _pt_trading_date_iso(),
        "risk_halt_cleared_pt_date": None,
        "closed_positions": [],
        "active_positions": [],
        "booked_equity": balance,
        "peak_equity": balance,
        "withdrawals_total": 0.0,
        "equity_curve": [
            {
                "t": datetime.now(timezone.utc).isoformat(),
                "equity": round(float(balance), 2),
                "delta": 0.0,
                "kind": "seed",
            }
        ],
    }


def build_seed_mock_trading_history() -> Dict[str, Any]:
    balance = get_so_demo_starting_balance()
    return {
        "total_pnl": 0.0,
        "total_trades": 0,
        "winning_trades": 0,
        "losing_trades": 0,
        "account_balance": balance,
        "starting_balance": balance,
        "weekly_stats": {
            "positions_opened": 0,
            "positions_closed": 0,
            "total_pnl": 0.0,
            "winning_trades": 0,
            "losing_trades": 0,
            "best_trade": 0.0,
            "worst_trade": 0.0,
            "week_start_date": _week_start_iso(),
        },
        "closed_trades": [],
    }


def _mock_trading_gcs_path() -> str:
    from modules.runtime_mode import gcs_mock_trading_history_path

    return gcs_mock_trading_history_path()


def seed_demo_sim_ledgers_to_gcs() -> List[str]:
    """
    Overwrite namespace-scoped demo sim ledgers in GCS with fresh seed balances.
    Returns list of GCS object paths written.
    """
    from modules.gcs_persistence import get_gcs_persistence
    from modules.runtime_mode import gcs_mock_options_history_path, gcs_namespace

    gcs = get_gcs_persistence()
    if not gcs or not getattr(gcs, "enabled", False):
        log.warning("DEMO_SIM_LEDGER_SEED | gcs_disabled — skipped")
        return []

    written: List[str] = []
    options_payload = build_seed_mock_options_history()
    payloads = (
        (gcs_mock_options_history_path(), options_payload, False),
        (_mock_trading_gcs_path(), build_seed_mock_trading_history(), False),
        (f"{gcs_namespace()}/desk_public/mock_options_history.json", options_payload, True),
    )
    for path, payload, public in payloads:
        kwargs = {}
        if public:
            kwargs = {
                "cache_control": "public, max-age=0, no-cache",
                "content_type": "application/json; charset=utf-8",
                "public_read": True,
            }
        ok = gcs.upload_string(path, json.dumps(payload, indent=2, default=str), **kwargs)
        if ok:
            written.append(path)
            log.info(
                "DEMO_SIM_LEDGER_SEED | wrote | path=%s | balance=$%.2f | trades=0",
                path,
                float(payload.get("account_balance", payload.get("starting_balance", 0.0))),
            )
        else:
            log.error("DEMO_SIM_LEDGER_SEED | upload_failed | path=%s", path)

    try:
        from modules.desk_action_pulse import reset_desk_actions_for_seed

        actions_path = reset_desk_actions_for_seed()
        if actions_path:
            written.append(actions_path)
    except Exception as err:
        log.warning("DEMO_SIM_LEDGER_SEED | desk_public actions reset failed | %s", err)

    if written:
        log.info(
            "DEMO_SIM_LEDGER_SEED | complete | namespace=%s | paths=%s",
            gcs_namespace(),
            written,
        )
    return written


def maybe_seed_demo_sim_ledgers_on_startup() -> Optional[List[str]]:
    if not demo_sim_ledger_seed_on_startup_enabled():
        return None
    from modules.runtime_mode import is_demo_mode

    if not is_demo_mode():
        log.info("DEMO_SIM_LEDGER_SEED | skipped — MODE is not demo")
        return None
    return seed_demo_sim_ledgers_to_gcs()
