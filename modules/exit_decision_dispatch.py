"""Exit decision dispatch + shadow parity helpers (monitor orchestrator).

Stealth returns ExitDecision; close submission stays in the monitor/executor path.
Shadow compare never mutates registry, submits, or alerts.
"""

from __future__ import annotations

import logging
import os
from copy import deepcopy
from typing import Any, Dict, Optional, Tuple

from modules.position_registry_contract import (
    EXIT_ACTION_HOLD,
    ExitDecision,
    premium_snapshot_from_mapping,
)

log = logging.getLogger(__name__)


def stealth_evaluate_api_enabled() -> bool:
    """Primary path: monitor uses stealth.evaluate (default on)."""
    return str(os.getenv("OPTION_STEALTH_USE_EVALUATE_API", "true") or "true").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def stealth_evaluate_shadow_enabled() -> bool:
    """Parity-only: run legacy process_position on a metadata copy and compare."""
    return str(os.getenv("OPTION_STEALTH_EVALUATE_SHADOW", "false") or "false").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def dispatch_exit_decision(decision: Optional[ExitDecision]) -> Optional[Tuple[float, str, Dict[str, Any]]]:
    """Adapt ExitDecision → legacy (prem, reason, telem) for existing close loops."""
    if decision is None or str(decision.action or "").upper() == EXIT_ACTION_HOLD:
        return None
    telem = dict(decision.diagnostics or {})
    telem["exit_action"] = decision.action
    telem["threshold_name"] = decision.threshold_name
    telem["current_pnl_pct"] = decision.current_pnl_pct
    telem["mfe_pct"] = decision.mfe_pct
    thr = decision.threshold_value if decision.threshold_value is not None else decision.threshold
    if thr is not None:
        telem.setdefault("trail_stop", thr)
        telem.setdefault("policy_threshold", thr)
    prem = decision.triggered_value
    if prem is None:
        prem = decision.trigger_value
    return (
        float(prem or 0.0),
        str(decision.reason or ""),
        telem,
    )


def load_premium_snapshot_from_position(position: Any) -> Any:
    md = getattr(position, "metadata", None) if not isinstance(position, dict) else position.get("metadata")
    if not isinstance(md, dict):
        return None
    return premium_snapshot_from_mapping(md.get("premium_snapshot"))


def compare_exit_decisions(
    *,
    trade_id: str,
    symbol: str,
    decision: ExitDecision,
    legacy_out: Optional[Tuple[Any, Any, Any]],
) -> None:
    """Log-only parity compare. Never submits or mutates."""
    if legacy_out is None:
        legacy_action = EXIT_ACTION_HOLD
        legacy_reason = None
        legacy_prem = None
    else:
        legacy_prem, legacy_reason, _telem = legacy_out
        from modules.position_registry_contract import exit_action_from_reason

        legacy_action = exit_action_from_reason(str(legacy_reason or ""), sell=True)

    action_match = str(decision.action).upper() == str(legacy_action).upper()
    reason_match = str(decision.reason or "") == str(legacy_reason or "")
    prem_delta = None
    try:
        d_prem = decision.triggered_value if decision.triggered_value is not None else decision.trigger_value
        if d_prem is not None and legacy_prem is not None:
            prem_delta = abs(float(d_prem) - float(legacy_prem))
    except (TypeError, ValueError):
        prem_delta = None

    level = log.info if action_match and reason_match else log.warning
    level(
        "STEALTH_EVALUATE_SHADOW | trade_id=%s | symbol=%s | action_match=%s | reason_match=%s | "
        "evaluate_action=%s | evaluate_reason=%s | legacy_action=%s | legacy_reason=%s | prem_delta=%s",
        trade_id,
        symbol,
        str(action_match).lower(),
        str(reason_match).lower(),
        decision.action,
        decision.reason,
        legacy_action,
        legacy_reason,
        f"{prem_delta:.6f}" if prem_delta is not None else "n/a",
    )


def run_legacy_process_position_shadow(
    stealth: Any,
    *,
    position_id: str,
    position_symbol: str,
    position_metadata: Dict[str, Any],
    current_underlying: float,
    now: Any,
    option_quote: Optional[Dict[str, Any]],
) -> Optional[Tuple[Any, Any, Any]]:
    """Call process_position on a deep-copied metadata so registry state is untouched."""
    md_copy = deepcopy(position_metadata) if isinstance(position_metadata, dict) else {}
    try:
        return stealth.process_position(
            position_id,
            position_symbol=position_symbol,
            position_metadata=md_copy,
            current_underlying=current_underlying,
            now=now,
            option_quote=option_quote,
            premium=None,
            mutate_premium_state=True,
        )
    except TypeError:
        # Older signature without premium kwargs
        return stealth.process_position(
            position_id,
            position_symbol=position_symbol,
            position_metadata=md_copy,
            current_underlying=current_underlying,
            now=now,
            option_quote=option_quote,
        )
    except Exception as err:
        log.warning(
            "STEALTH_EVALUATE_SHADOW | legacy_failed | trade_id=%s | error=%s",
            position_id,
            err,
        )
        return None
