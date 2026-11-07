#!/usr/bin/env python3
"""
QUARANTINED — Legacy Prime risk_manager pre-check for Trendline intents.

Live ORB / Trendline 0DTE does not use this path when Unified Account Risk is on.
Call sites only invoke ``evaluate_trendline_legacy_risk`` when a Demo risk_manager
exposes ``evaluate_intent``. Otherwise they log ``legacy_risk_gate_quarantined``
and defer to UAR / ``DTE0ExecutionRiskManager`` at broker submit.

Kept for Demo MODE + unit tests only — do not re-wire into Live 0DTE.
"""

from __future__ import annotations

import asyncio
from typing import Any, Tuple


async def evaluate_trendline_legacy_risk(
    risk_manager: Any, intent: Any
) -> Tuple[bool, str, bool]:
    """
    Returns (approved, risk_path, safe_mode_active).

    Only meaningful when ``risk_manager.evaluate_intent`` exists (Demo).
    """
    approved = True
    risk_path = "legacy_risk_gate_quarantined"
    safe_mode = False
    if risk_manager and hasattr(risk_manager, "evaluate_intent"):
        risk_path = "evaluate_intent"
        try:
            decision = risk_manager.evaluate_intent(intent)
            if asyncio.iscoroutine(decision):
                decision = await decision
            approved = bool(decision)
        except Exception:
            approved = False
        return approved, risk_path, safe_mode
    if risk_manager:
        risk_path = "fallback_no_evaluate_intent"
        safe_mode = bool(getattr(risk_manager, "safe_mode_active", False))
        if safe_mode:
            approved = False
    return approved, risk_path, safe_mode


def trendline_risk_rejection_hint(risk_path: str, safe_mode: bool) -> str:
    if risk_path == "evaluate_intent":
        return "evaluate_intent_rejected"
    if safe_mode:
        return "safe_mode_active"
    return "evaluate_intent_false_or_fallback"
