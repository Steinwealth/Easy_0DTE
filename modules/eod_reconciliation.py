"""EOD ledger / balance / report reconciliation."""

from __future__ import annotations

import logging
import os
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


def eod_reconciliation_enabled() -> bool:
    raw = os.getenv("0DTE_EOD_RECONCILIATION_ENABLED", "true")
    return str(raw or "true").strip().lower() in ("1", "true", "yes", "on")


def eod_fail_on_unexplained_mismatch() -> bool:
    raw = os.getenv("0DTE_EOD_FAIL_ON_UNEXPLAINED_MISMATCH", "true")
    return str(raw or "true").strip().lower() in ("1", "true", "yes", "on")


@dataclass
class EodReconciliationRecord:
    app: str = ""
    mode: str = ""
    trade_date: str = ""
    starting_balance: float = 0.0
    ending_balance: float = 0.0
    balance_delta: float = 0.0
    closed_trade_count: int = 0
    closed_trade_net_pnl: float = 0.0
    ledger_realized_pnl: float = 0.0
    report_net_pnl: float = 0.0
    broker_realized_pnl: Optional[float] = None
    commission_adjustment: float = 0.0
    unresolved_open_positions: int = 0
    difference: float = 0.0
    status: str = "INCOMPLETE"  # PASS | PASS_WITH_BROKER_TIMING_DIFFERENCE | FAIL | INCOMPLETE
    extras: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d


def reconcile_demo_accounting(
    *,
    app: str,
    mode: str,
    trade_date: str,
    starting_balance: float,
    ending_balance: float,
    closed_trades: List[Dict[str, Any]],
    ledger_realized_pnl: Optional[float] = None,
    report_net_pnl: Optional[float] = None,
    unresolved_open_positions: int = 0,
    tolerance: float = 0.02,
) -> EodReconciliationRecord:
    closed_sum = 0.0
    for t in closed_trades:
        try:
            closed_sum += float((t or {}).get("net_pnl") or 0.0)
        except (TypeError, ValueError):
            pass
    closed_sum = round(closed_sum, 2)
    bal_delta = round(float(ending_balance) - float(starting_balance), 2)
    led = round(float(ledger_realized_pnl if ledger_realized_pnl is not None else closed_sum), 2)
    rep = round(float(report_net_pnl if report_net_pnl is not None else closed_sum), 2)

    diffs = [
        abs(closed_sum - led),
        abs(closed_sum - rep),
        abs(closed_sum - bal_delta),
    ]
    max_diff = max(diffs) if diffs else 0.0
    status = "PASS"
    if unresolved_open_positions > 0:
        status = "INCOMPLETE"
    elif max_diff > tolerance + 1e-9:
        status = "FAIL"

    rec = EodReconciliationRecord(
        app=app,
        mode=mode,
        trade_date=trade_date,
        starting_balance=float(starting_balance),
        ending_balance=float(ending_balance),
        balance_delta=bal_delta,
        closed_trade_count=len(closed_trades),
        closed_trade_net_pnl=closed_sum,
        ledger_realized_pnl=led,
        report_net_pnl=rep,
        broker_realized_pnl=None,
        commission_adjustment=0.0,
        unresolved_open_positions=int(unresolved_open_positions),
        difference=round(max_diff, 4),
        status=status,
    )
    log.info(
        "EOD_RECONCILIATION | app=%s | mode=%s | trade_date=%s | starting_balance=%s | "
        "ending_balance=%s | balance_delta=%s | closed_trade_count=%s | closed_trade_net_pnl=%s | "
        "ledger_realized_pnl=%s | report_net_pnl=%s | broker_realized_pnl=%s | "
        "commission_adjustment=%s | unresolved_open_positions=%s | difference=%s | status=%s",
        rec.app,
        rec.mode,
        rec.trade_date,
        rec.starting_balance,
        rec.ending_balance,
        rec.balance_delta,
        rec.closed_trade_count,
        rec.closed_trade_net_pnl,
        rec.ledger_realized_pnl,
        rec.report_net_pnl,
        rec.broker_realized_pnl,
        rec.commission_adjustment,
        rec.unresolved_open_positions,
        rec.difference,
        rec.status,
    )
    if status == "FAIL" and eod_fail_on_unexplained_mismatch():
        log.error(
            "EOD_RECONCILIATION_FAIL | unexplained_mismatch | difference=%s | "
            "closed=%s | ledger=%s | report=%s | balance_delta=%s",
            rec.difference,
            closed_sum,
            led,
            rep,
            bal_delta,
        )
    return rec


def reconcile_live_accounting(
    *,
    app: str,
    mode: str,
    trade_date: str,
    starting_balance: float,
    ending_balance: float,
    closed_trades: List[Dict[str, Any]],
    ledger_realized_pnl: Optional[float] = None,
    report_net_pnl: Optional[float] = None,
    broker_realized_pnl: Optional[float] = None,
    commission_adjustment: float = 0.0,
    unresolved_open_positions: int = 0,
    tolerance: float = 0.50,
) -> EodReconciliationRecord:
    closed_sum = round(
        sum(float((t or {}).get("net_pnl") or 0.0) for t in closed_trades),
        2,
    )
    bal_delta = round(float(ending_balance) - float(starting_balance), 2)
    led = round(float(ledger_realized_pnl if ledger_realized_pnl is not None else closed_sum), 2)
    rep = round(float(report_net_pnl if report_net_pnl is not None else closed_sum), 2)
    status = "PASS"
    difference = abs(closed_sum - led)
    if unresolved_open_positions > 0:
        status = "INCOMPLETE"
    elif broker_realized_pnl is not None:
        br = float(broker_realized_pnl)
        timing_diff = abs(closed_sum - br)
        if timing_diff > tolerance + 1e-9 and abs(closed_sum - led) <= tolerance:
            status = "PASS_WITH_BROKER_TIMING_DIFFERENCE"
            difference = timing_diff
        elif timing_diff > tolerance + 1e-9 and abs(closed_sum - led) > tolerance:
            status = "FAIL"
            difference = max(timing_diff, abs(closed_sum - led))
    elif difference > tolerance + 1e-9:
        status = "FAIL"

    rec = EodReconciliationRecord(
        app=app,
        mode=mode,
        trade_date=trade_date,
        starting_balance=float(starting_balance),
        ending_balance=float(ending_balance),
        balance_delta=bal_delta,
        closed_trade_count=len(closed_trades),
        closed_trade_net_pnl=closed_sum,
        ledger_realized_pnl=led,
        report_net_pnl=rep,
        broker_realized_pnl=broker_realized_pnl,
        commission_adjustment=float(commission_adjustment or 0.0),
        unresolved_open_positions=int(unresolved_open_positions),
        difference=round(difference, 4),
        status=status,
    )
    log.info(
        "EOD_RECONCILIATION | app=%s | mode=%s | trade_date=%s | starting_balance=%s | "
        "ending_balance=%s | balance_delta=%s | closed_trade_count=%s | closed_trade_net_pnl=%s | "
        "ledger_realized_pnl=%s | report_net_pnl=%s | broker_realized_pnl=%s | "
        "commission_adjustment=%s | unresolved_open_positions=%s | difference=%s | status=%s",
        rec.app,
        rec.mode,
        rec.trade_date,
        rec.starting_balance,
        rec.ending_balance,
        rec.balance_delta,
        rec.closed_trade_count,
        rec.closed_trade_net_pnl,
        rec.ledger_realized_pnl,
        rec.report_net_pnl,
        rec.broker_realized_pnl,
        rec.commission_adjustment,
        rec.unresolved_open_positions,
        rec.difference,
        rec.status,
    )
    return rec
