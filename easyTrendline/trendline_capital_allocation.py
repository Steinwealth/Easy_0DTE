#!/usr/bin/env python3
"""
Unified 0DTE capital buckets: sacred buffer reserve + equal N-slot pool.

Shared by ORB batch sizing and Trendline entries. Buffer (default 10% of equity)
is set at the session floor on first refresh, never shrinks intraday on losses,
and grows when equity rises from profits. Each slot targets tradable_equity / N.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ZeroDTECapitalSnapshot:
    equity_basis: float
    cash_balance: float
    reserved_open_entry_cost: float
    buffer_reserve: float
    buffer_reserve_floor: float
    tradable_equity: float
    available_cash: float
    slot_capital: float
    entry_budget: float
    allocation_pct: float
    slot_count: int


# Back-compat alias (Trendline call sites / older tests).
TrendlineCapitalSnapshot = ZeroDTECapitalSnapshot


def buffer_pct_from_allocation(allocation_pct: float) -> float:
    """Tradable allocation 90% → 10% sacred buffer."""
    return max(0.0, min(100.0, 100.0 - float(allocation_pct)))


def compute_buffer_reserve(
    equity_basis: float,
    *,
    buffer_pct: float,
    buffer_floor: float,
) -> float:
    """
    Intraday buffer: max(session floor, current equity × buffer%).
    Floor never decreases until next session reset.
    """
    equity_basis = max(0.0, float(equity_basis))
    floor = max(0.0, float(buffer_floor))
    growth = equity_basis * max(0.0, float(buffer_pct)) / 100.0
    return max(floor, growth)


def compute_session_buffer_floor(equity_basis: float, *, buffer_pct: float) -> float:
    """One-time session floor at 7:30 (or first refresh of the trading day)."""
    return max(0.0, float(equity_basis)) * max(0.0, float(buffer_pct)) / 100.0


def compute_entry_budget(slot_capital: float, available_cash: float) -> float:
    """
    Deploy up to the 1/N slot target from cash outside the buffer.

    entry_budget = min(target, available_cash): use full target when cash allows;
    after a partial-loss close, enter at released proceeds rather than skip.
    """
    target = max(0.0, float(slot_capital))
    available = max(0.0, float(available_cash))
    if target <= 0.0 or available <= 0.0:
        return 0.0
    return min(target, available)


def compute_0dte_capital_snapshot(
    *,
    cash_balance: float,
    open_entry_costs: float,
    allocation_pct: float,
    slot_count: int,
    buffer_floor: float,
) -> ZeroDTECapitalSnapshot:
    cash_balance = max(0.0, float(cash_balance))
    open_entry_costs = max(0.0, float(open_entry_costs))
    slot_count = max(1, int(slot_count))
    allocation_pct = max(0.0, min(100.0, float(allocation_pct)))
    buf_pct = buffer_pct_from_allocation(allocation_pct)

    equity_basis = cash_balance + open_entry_costs
    buffer_reserve = compute_buffer_reserve(
        equity_basis, buffer_pct=buf_pct, buffer_floor=buffer_floor
    )
    tradable_equity = max(0.0, equity_basis - buffer_reserve)
    available_cash = max(0.0, cash_balance - buffer_reserve)
    # Per-slot target: 1/N of tradable pool. equity_basis = cash + open entry costs,
    # so the target stays flat while slots fill (not available_cash / N).
    slot_capital = tradable_equity / float(slot_count)
    entry_budget = compute_entry_budget(slot_capital, available_cash)

    return ZeroDTECapitalSnapshot(
        equity_basis=equity_basis,
        cash_balance=cash_balance,
        reserved_open_entry_cost=open_entry_costs,
        buffer_reserve=buffer_reserve,
        buffer_reserve_floor=max(0.0, float(buffer_floor)),
        tradable_equity=tradable_equity,
        available_cash=available_cash,
        slot_capital=slot_capital,
        entry_budget=entry_budget,
        allocation_pct=allocation_pct,
        slot_count=slot_count,
    )


# Back-compat name used across Trendline modules/tests.
compute_trendline_capital_snapshot = compute_0dte_capital_snapshot


def slot_affordable_quantity(*, budget: float, unit_cost: float) -> int:
    """Whole contracts/spreads that fit in the slot budget. 0 = too expensive (never force 1)."""
    b = max(0.0, float(budget or 0.0))
    c = max(0.0, float(unit_cost or 0.0))
    if b <= 0.0 or c <= 0.0:
        return 0
    return int(b // c)


def slot_unit_exceeds_budget(*, budget: float, unit_cost: float) -> bool:
    """True when one contract/spread costs more than the slot budget."""
    b = max(0.0, float(budget or 0.0))
    c = max(0.0, float(unit_cost or 0.0))
    if c <= 0.0:
        return True
    if b <= 0.0:
        return True
    return c > b + 1e-9


def can_debit_outside_buffer(entry_cost: float, cash_balance: float, buffer_reserve: float) -> bool:
    if entry_cost <= 0:
        return False
    return float(entry_cost) <= max(0.0, float(cash_balance) - float(buffer_reserve))
