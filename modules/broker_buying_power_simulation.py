#!/usr/bin/env python3
"""BROKER_BUYING_POWER_SIMULATION — verify sizing math vs broker buying power (read-only)."""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

_DEFAULT_SCENARIOS: List[Dict[str, Any]] = [
    {
        "label": "MSTR PUT lotto",
        "symbol": "MSTR",
        "option_type": "put",
        "premium": 0.80,
        "quantity": 10,
    },
    {
        "label": "COIN PUT lotto",
        "symbol": "COIN",
        "option_type": "put",
        "premium": 2.50,
        "quantity": 5,
    },
]


def _load_scenarios() -> List[Dict[str, Any]]:
    raw = os.getenv("BROKER_CERT_BUYING_POWER_SIM")
    if not raw or not str(raw).strip():
        return list(_DEFAULT_SCENARIOS)
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list) and parsed:
            return parsed
    except json.JSONDecodeError:
        pass
    return list(_DEFAULT_SCENARIOS)


def _option_cost(premium: float, quantity: int) -> float:
    """Single-leg buy-to-open cost (premium × contracts × 100)."""
    return round(float(premium) * int(quantity) * 100.0, 2)


def run_broker_buying_power_simulation(
    *,
    cash_available: Optional[float],
    buying_power: Optional[float],
    account_value: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Simulate representative ORB1 Breakout lotto deployments against broker balances.

    Uses buying_power when present, else cash_available.
    """
    budget = buying_power if buying_power is not None else cash_available
    scenarios_out: List[Dict[str, Any]] = []
    all_pass = budget is not None and budget >= 0

    for spec in _load_scenarios():
        premium = float(spec.get("premium") or 0.0)
        qty = int(spec.get("quantity") or 0)
        cost = _option_cost(premium, qty)
        ok = budget is not None and cost <= float(budget)
        if not ok:
            all_pass = False
        row = {
            "label": spec.get("label") or spec.get("symbol"),
            "symbol": spec.get("symbol"),
            "option_type": spec.get("option_type"),
            "premium": premium,
            "quantity": qty,
            "cost": cost,
            "formula": f"${premium:.2f} x {qty} x 100 = ${cost:.2f}",
            "pass": ok,
        }
        scenarios_out.append(row)
        log.info(
            "BROKER_BUYING_POWER_SIMULATION | %s %s @ $%.2f x %d | cost=$%.2f | pass=%s",
            row["symbol"],
            str(row["option_type"]).upper(),
            premium,
            qty,
            cost,
            str(ok).lower(),
        )

    audit = {
        "cash_available": cash_available,
        "buying_power": buying_power,
        "account_value": account_value,
        "budget_used": budget,
        "all_pass": all_pass,
        "scenarios": scenarios_out,
    }
    log.info(
        "BROKER_BUYING_POWER_SIMULATION | budget=%s | all_pass=%s | scenarios=%d",
        budget,
        str(all_pass).lower(),
        len(scenarios_out),
    )
    return audit
