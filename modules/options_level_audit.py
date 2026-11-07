#!/usr/bin/env python3
"""
E*TRADE options approval level requirements and OPTIONS_LEVEL_AUDIT.

E*TRADE option levels (typical retail mapping):
  0 — options not enabled
  1 — covered calls / cash-secured puts
  2 — long calls, long puts (ORB1 Breakout / Trendline lotto Live launch)
  3 — debit spreads, credit spreads
  4 — naked / advanced strategies
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

# Documented minimum E*TRADE option levels per strategy instrument type
ETRADE_OPTIONS_LEVEL_REQUIREMENTS: Dict[str, int] = {
    "long_calls": 2,
    "long_puts": 2,
    "debit_spreads": 3,  # future — LIVE_SPREADS_ENABLED
    "credit_spreads": 3,  # future — LIVE_SPREADS_ENABLED
}


def _env_int(key: str, default: int) -> int:
    raw = os.getenv(key)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(str(raw).strip())
    except ValueError:
        return default


def required_options_level_for(strategy_key: str) -> int:
    """Minimum E*TRADE option level for a strategy instrument class."""
    env_key = f"REQUIRED_OPTIONS_LEVEL_{strategy_key.upper()}"
    default = ETRADE_OPTIONS_LEVEL_REQUIREMENTS.get(strategy_key, 2)
    return _env_int(env_key, _env_int("REQUIRED_OPTIONS_LEVEL", default))


def required_options_level_launch_profile() -> int:
    """
    Minimum level for current Live launch profile.

    Lotto-only (LIVE_SPREADS_ENABLED=false): max(long_call, long_put) → level 2.
    Spreads enabled: max across debit/credit spread requirements → level 3.

    Uses LIVE_SPREADS_ENABLED env directly (not runtime_mode.live_spreads_enabled,
    which is true in Demo to exercise spread code paths).
    """
    spreads = os.getenv("LIVE_SPREADS_ENABLED", "false").strip().lower() == "true"

    if spreads:
        return max(
            required_options_level_for("long_calls"),
            required_options_level_for("long_puts"),
            required_options_level_for("debit_spreads"),
            required_options_level_for("credit_spreads"),
        )
    return max(
        required_options_level_for("long_calls"),
        required_options_level_for("long_puts"),
    )


def parse_etrade_option_level(raw: Any) -> Optional[int]:
    """Normalize E*TRADE optionLevel tokens ('3', 'Level 3', etc.) to int."""
    if raw is None:
        return None
    text = str(raw).strip().upper()
    if not text or text in ("NONE", "N/A", "NULL"):
        return None
    if text.isdigit():
        return int(text)
    match = re.search(r"(\d+)", text)
    if match:
        return int(match.group(1))
    return None


def options_level_requirements_snapshot() -> Dict[str, int]:
    """Return documented + env-overridden requirements for audits and docs."""
    return {
        "long_calls": required_options_level_for("long_calls"),
        "long_puts": required_options_level_for("long_puts"),
        "debit_spreads": required_options_level_for("debit_spreads"),
        "credit_spreads": required_options_level_for("credit_spreads"),
        "launch_profile": required_options_level_launch_profile(),
    }


def audit_options_level(
    actual_raw: Any,
    *,
    required: Optional[int] = None,
    strict: bool = False,
    account_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Emit OPTIONS_LEVEL_AUDIT startup / certification line.

    Returns audit dict with pass=true when actual >= required.
    """
    req = required if required is not None else required_options_level_launch_profile()
    actual = parse_etrade_option_level(actual_raw)
    passed = actual is not None and actual >= req
    requirements = options_level_requirements_snapshot()

    audit = {
        "required": req,
        "actual": actual,
        "actual_raw": actual_raw,
        "pass": passed,
        "account_id": account_id,
        "requirements": requirements,
        "launch_profile_spreads": req >= 3,
    }
    log.info(
        "OPTIONS_LEVEL_AUDIT | required=%s | actual=%s | pass=%s | account_id=%s | "
        "long_calls=%s | long_puts=%s | debit_spreads=%s | credit_spreads=%s",
        req,
        actual if actual is not None else actual_raw,
        str(passed).lower(),
        account_id or "-",
        requirements["long_calls"],
        requirements["long_puts"],
        requirements["debit_spreads"],
        requirements["credit_spreads"],
    )
    if strict and not passed:
        raise ValueError(
            f"OPTIONS_LEVEL_AUDIT_FAILED | required={req} | actual={actual_raw!r}"
        )
    return audit
