#!/usr/bin/env python3
"""
Shared helpers for scheduled end-of-day Telegram reports (ORB SO, ORB1 Breakout, Trendline 0DTE).

Cloud Scheduler job ``end-of-day-report`` POST /api/end-of-day-report (~12:55 PM PT /
3:55 PM ET) is the only sender of ``🏦 END-OF-DAY 0DTE``. The trading loop does not
send this Telegram. GCS markers still skip scheduler retries after a successful send.
Trading-day boundaries use America/Los_Angeles for consistency with the desk session.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from html import escape as html_escape
from typing import Any, Dict, List, Optional, Tuple

from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

PT_TZ = ZoneInfo("America/Los_Angeles")
ET_TZ = ZoneInfo("America/New_York")

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def orb_so_eod_enabled() -> bool:
    """
    ORB SO ETF EOD Telegram (`🛂 END-OF-DAY REPORT`) — only when the SO path is on.

    When ``ENABLE_ORB_STRATEGY=false`` (RESEARCH Demo desk: SO paused), Cloud Scheduler
    ``POST /api/end-of-day-report`` skips SO demo/live summaries and sends only unified
    ``🏦 END-OF-DAY 0DTE``. Marker token in results: ``skipped:so_etf_retired``.
    """
    return os.getenv("ENABLE_ORB_STRATEGY", "false").strip().lower() in _TRUTHY


def fmt_usd(amount: float, decimals: int = 2) -> str:
    """Dollar amount with thousands separators (e.g. $12,345.67)."""
    return f"${float(amount):,.{decimals}f}"


def fmt_usd_signed(amount: float, decimals: int = 2) -> str:
    """Signed dollar amount (e.g. +$12,345.67 or -$12,345.67)."""
    val = float(amount)
    sign = "+" if val >= 0 else "-"
    return f"{sign}${abs(val):,.{decimals}f}"


def fmt_pct(pct: float, decimals: int = 2) -> str:
    """Signed percent with thousands separators (e.g. +2,281.71)."""
    val = float(pct)
    sign = "+" if val >= 0 else "-"
    return f"{sign}{abs(val):,.{decimals}f}"


def pt_trading_date() -> date:
    """Calendar date in Pacific time (canonical trading session day)."""
    return datetime.now(PT_TZ).date()


def pt_trading_date_iso() -> str:
    return pt_trading_date().isoformat()


def pt_et_display_times() -> Tuple[str, str]:
    """Formatted clock strings for EOD headers."""
    now_pt = datetime.now(PT_TZ)
    now_et = datetime.now(ET_TZ)
    return now_pt.strftime("%I:%M %p PT"), now_et.strftime("%I:%M %p ET")


def empty_trade_stats() -> Dict[str, Any]:
    return {
        "positions_closed": 0,
        "winning_trades": 0,
        "losing_trades": 0,
        "total_pnl": 0.0,
        "best_trade": 0.0,
        "worst_trade": 0.0,
        "total_wins_sum": 0.0,
        "total_losses_sum": 0.0,
    }


def try_claim_eod_marker(gcs, marker_key: str) -> bool:
    """
    Return True if this instance should send the EOD (marker claimed).
    Return False if marker already exists (duplicate — skip send).
    On GCS failure, returns True (proceed; may duplicate).
    """
    from .runtime_mode import gcs_eod_marker_path

    gcs_marker_path = gcs_eod_marker_path(marker_key)
    try:
        if gcs.file_exists(gcs_marker_path):
            return False
        gcs.upload_string(gcs_marker_path, datetime.now().isoformat())
        return True
    except Exception as exc:
        log.warning("EOD marker claim failed for %s: %s — proceeding without dedupe", marker_key, exc)
        return True


def eod_marker_exists(gcs, marker_key: str) -> bool:
    """True when today's EOD Telegram marker is already on GCS (sent, not merely started)."""
    if not gcs:
        return False
    try:
        from .runtime_mode import gcs_eod_marker_path

        return bool(gcs.file_exists(gcs_eod_marker_path(marker_key)))
    except Exception as exc:
        log.warning("EOD marker exists-check failed for %s: %s", marker_key, exc)
        return False


def _sum_wins_losses(positions: List[Any]) -> Tuple[float, float]:
    wins_sum = sum(float(p.realized_pnl) for p in positions if float(p.realized_pnl) > 0)
    losses_sum = abs(sum(float(p.realized_pnl) for p in positions if float(p.realized_pnl) < 0))
    return wins_sum, losses_sum


def profit_factor(positions: List[Any]) -> float:
    wins_sum, losses_sum = _sum_wins_losses(positions)
    if losses_sum > 0:
        return wins_sum / losses_sum
    return float("inf") if wins_sum > 0 else 0.0


def pf_str(value: float) -> str:
    if value == float("inf"):
        return "∞"
    return f"{float(value):,.2f}"


def pct(pnl_value: float, base: float) -> float:
    return (pnl_value / base * 100.0) if base > 0 else 0.0


def period_start_balance(account_balance: float, period_pnl: float) -> float:
    """Equity at period open (start of day or start of week) before period trades."""
    return float(account_balance) - float(period_pnl)


def period_pnl_pct(
    period_pnl: float,
    account_balance: float,
    *,
    fallback_base: float = 0.0,
) -> float:
    """
    Period return % vs balance at period start (not vs original demo seed).

    Example: EOD balance $23,499.50 with +$5,214 today → base $18,285.50 → ~28.54%.
    """
    base = period_start_balance(account_balance, period_pnl)
    if base > 0:
        return pct(period_pnl, base)
    if fallback_base > 0:
        return pct(period_pnl, fallback_base)
    return 0.0


def all_time_pnl_pct(all_time_pnl: float, starting_balance: float) -> float:
    """Lifetime return % vs original account starting balance (e.g. $5,000 demo seed)."""
    return pct(all_time_pnl, starting_balance)


def resolve_eod_account_balance(
    account_balance: float,
    starting_balance: float,
    all_time_pnl: float = 0.0,
) -> float:
    """Best-effort EOD equity for period-base math when callers pass partial payloads."""
    bal = float(account_balance or 0.0)
    if bal > 0:
        return bal
    seed = float(starting_balance or 0.0)
    return seed + float(all_time_pnl or 0.0)


def position_closed_date_pt(p: Any) -> Optional[date]:
    """
    Best-effort PT calendar date when a closed position belongs in EOD buckets.

    Prefers ``closed_at``; falls back to ``entry_time`` for legacy ORB1 Breakout rows
    (0DTE positions open and close same session).
    """
    closed_at = getattr(p, "closed_at", None)
    if closed_at:
        if hasattr(closed_at, "tzinfo") and closed_at.tzinfo is not None:
            return closed_at.astimezone(PT_TZ).date()
        if hasattr(closed_at, "date"):
            return closed_at.date()
        return closed_at if isinstance(closed_at, date) else None

    entry_time = getattr(p, "entry_time", None)
    if entry_time:
        if hasattr(entry_time, "tzinfo") and entry_time.tzinfo is not None:
            return entry_time.astimezone(PT_TZ).date()
        if hasattr(entry_time, "date"):
            return entry_time.date()

    opened_at = getattr(p, "opened_at", None)
    if opened_at:
        if hasattr(opened_at, "tzinfo") and opened_at.tzinfo is not None:
            return opened_at.astimezone(PT_TZ).date()
        if hasattr(opened_at, "date"):
            return opened_at.date()
    return None


def filter_positions_by_pt_date(
    positions: List[Any],
    *,
    on_date: Optional[date] = None,
    week_through: Optional[date] = None,
) -> Tuple[List[Any], List[Any], List[Any]]:
    """
    Partition closed positions into today (PT), week-to-date (Mon–on_date), and all-time.
    """
    on_date = on_date or pt_trading_date()
    week_through = week_through or on_date
    week_start = week_through - timedelta(days=week_through.weekday())

    today_positions: List[Any] = []
    week_positions: List[Any] = []
    for p in positions:
        closed_date = position_closed_date_pt(p)
        if not closed_date:
            continue
        if closed_date == on_date:
            today_positions.append(p)
        if week_start <= closed_date <= week_through:
            week_positions.append(p)
    return today_positions, week_positions, list(positions)


def stats_dict_from_positions(positions: List[Any]) -> Dict[str, Any]:
    """Build daily/weekly stats dict from closed ledger rows (SO-style recovery)."""
    if not positions:
        return empty_trade_stats()

    pnl_values = [float(getattr(p, "realized_pnl", 0.0) or 0.0) for p in positions]
    wins = [v for v in pnl_values if v > 0]
    losses = [v for v in pnl_values if v < 0]
    wins_sum = sum(wins)
    losses_sum = abs(sum(losses))

    closed = len(positions)
    return {
        "positions_closed": closed,
        "total_trades": closed,
        "winning_trades": len(wins),
        "losing_trades": len(losses),
        "total_pnl": sum(pnl_values),
        "best_trade": max(pnl_values) if pnl_values else 0.0,
        "worst_trade": min(pnl_values) if pnl_values else 0.0,
        "total_wins_sum": wins_sum,
        "total_losses_sum": losses_sum,
    }


def reconcile_options_eod_stats(
    daily_stats: Dict[str, Any],
    weekly_stats: Dict[str, Any],
    closed_positions: List[Any],
) -> Tuple[Dict[str, Any], Dict[str, Any], bool]:
    """
    Recover daily/weekly counters from persisted closed_positions when in-memory
    stats were wiped (e.g. post-deploy cold start before EOD).
    """
    today_positions, week_positions, _ = filter_positions_by_pt_date(closed_positions)

    daily = dict(daily_stats or empty_trade_stats())
    weekly = dict(weekly_stats or empty_trade_stats())
    recovered = False

    mem_daily = int(daily.get("positions_closed", 0) or 0)
    ledger_daily = len(today_positions)
    if ledger_daily > 0 and mem_daily < ledger_daily:
        log.warning(
            "EOD_ORB_0DTE | daily_stats recovery | memory=%s ledger=%s (likely post-deploy wipe)",
            mem_daily,
            ledger_daily,
        )
        daily = stats_dict_from_positions(today_positions)
        recovered = True

    mem_weekly = int(weekly.get("positions_closed", 0) or 0)
    ledger_weekly = len(week_positions)
    if ledger_weekly > 0 and mem_weekly < ledger_weekly:
        log.warning(
            "EOD_ORB_0DTE | weekly_stats recovery | memory=%s ledger=%s",
            mem_weekly,
            ledger_weekly,
        )
        weekly = stats_dict_from_positions(week_positions)
        recovered = True

    if recovered:
        log.info(
            "EOD_ORB_0DTE | recovered stats from closed_positions | today=%s trades $%.2f | week=%s trades $%.2f",
            daily.get("positions_closed", 0),
            float(daily.get("total_pnl", 0.0) or 0.0),
            weekly.get("positions_closed", 0),
            float(weekly.get("total_pnl", 0.0) or 0.0),
        )

    return daily, weekly, recovered


def is_trendline_closed_position(p: Any) -> bool:
    """True when a closed ledger row belongs to the Trendline 0DTE path."""
    st = str(getattr(p, "strategy_type", "") or "").lower()
    if isinstance(p, dict):
        st = st or str(p.get("strategy_type") or "").lower()
    return "trendline" in st


def build_today_path_breakdown(closed_positions: List[Any]) -> Dict[str, Dict[str, Any]]:
    """Split today's closed trades by execution path on the unified options ledger."""
    today_positions, _, _ = filter_positions_by_pt_date(closed_positions)
    orb = [p for p in today_positions if not is_trendline_closed_position(p)]
    tl = [p for p in today_positions if is_trendline_closed_position(p)]
    return {
        "orb_0dte": stats_dict_from_positions(orb),
        "trendline_0dte": stats_dict_from_positions(tl),
    }


def payload_from_mock_options_executor(mock: Any, source: str) -> Dict[str, Any]:
    """Unified 0DTE demo payload (ORB + Trendline) with ledger recovery for daily/weekly stats."""
    closed_positions = list(getattr(mock, "closed_positions", None) or [])
    daily_stats, weekly_stats, recovered = reconcile_options_eod_stats(
        mock.get_daily_stats(),
        mock.get_weekly_stats(),
        closed_positions,
    )
    if recovered and hasattr(mock, "daily_stats"):
        mock.daily_stats.update(daily_stats)
    if recovered and hasattr(mock, "weekly_stats"):
        mock.weekly_stats.update({k: v for k, v in weekly_stats.items() if k != "week_start_date"})

    resolved_source = f"{source}_ledger_recovery" if recovered else source
    return {
        "daily_stats": daily_stats,
        "weekly_stats": weekly_stats,
        "all_time_stats": mock.get_all_time_stats(),
        "account_balance": mock.account_balance,
        "starting_balance": mock.starting_balance,
        "mode": "DEMO",
        "source": resolved_source,
        "path_breakdown": build_today_path_breakdown(closed_positions),
    }


def build_ledger_eod_bundle(
    closed_positions: List[Any],
    *,
    starting_balance: float,
    account_balance: Optional[float] = None,
) -> Dict[str, Any]:
    """Stats bundle for options-style / trendline EOD Telegram bodies."""
    today_positions, week_positions, all_positions = filter_positions_by_pt_date(closed_positions)
    seed = float(starting_balance)
    ledger_all_time_pnl = sum(
        float(getattr(p, "realized_pnl", 0.0) or 0.0) for p in all_positions
    )
    acct_raw = float(account_balance if account_balance is not None else 0.0)
    if acct_raw > seed:
        acct = acct_raw
        all_time_pnl = acct - seed
        if all_positions and abs(all_time_pnl - ledger_all_time_pnl) > 0.01:
            all_time_pnl = ledger_all_time_pnl
    else:
        all_time_pnl = ledger_all_time_pnl
        acct = seed + ledger_all_time_pnl

    today_pnl = sum(float(p.realized_pnl) for p in today_positions)
    week_pnl = sum(float(p.realized_pnl) for p in week_positions)
    today_wins = sum(1 for p in today_positions if float(p.realized_pnl) > 0)
    today_losses = sum(1 for p in today_positions if float(p.realized_pnl) < 0)
    week_wins = sum(1 for p in week_positions if float(p.realized_pnl) > 0)
    week_losses = sum(1 for p in week_positions if float(p.realized_pnl) < 0)
    all_time_wins = sum(1 for p in all_positions if float(p.realized_pnl) > 0)
    all_time_losses = sum(1 for p in all_positions if float(p.realized_pnl) < 0)

    today_closed = len(today_positions)
    week_closed = len(week_positions)
    all_time_closed = len(all_positions)

    today_best = max((float(p.realized_pnl) for p in today_positions), default=0.0)
    today_worst = min((float(p.realized_pnl) for p in today_positions), default=0.0)
    today_wins_sum, today_losses_sum = _sum_wins_losses(today_positions)
    avg_win = (today_wins_sum / today_wins) if today_wins > 0 else 0.0
    avg_loss = -(today_losses_sum / today_losses) if today_losses > 0 else 0.0

    return {
        "starting_balance": float(starting_balance),
        "account_balance": acct,
        "report_date": pt_trading_date_iso(),
        "today_pnl": today_pnl,
        "week_pnl": week_pnl,
        "all_time_pnl": all_time_pnl,
        "today_wins": today_wins,
        "today_losses": today_losses,
        "week_wins": week_wins,
        "week_losses": week_losses,
        "all_time_wins": all_time_wins,
        "all_time_losses": all_time_losses,
        "today_closed": today_closed,
        "week_closed": week_closed,
        "all_time_closed": all_time_closed,
        "today_win_rate": (today_wins / today_closed * 100.0) if today_closed > 0 else 0.0,
        "week_win_rate": (week_wins / week_closed * 100.0) if week_closed > 0 else 0.0,
        "all_time_win_rate": (all_time_wins / all_time_closed * 100.0) if all_time_closed > 0 else 0.0,
        "today_pf": profit_factor(today_positions),
        "week_pf": profit_factor(week_positions),
        "all_time_pf": profit_factor(all_positions),
        "today_best": today_best,
        "today_worst": today_worst,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
    }



def load_0dte_demo_stats_from_gcs() -> Optional[Dict[str, Any]]:
    """
    Load persisted ORB1 Breakout demo ledger from GCS when dte0_manager is unavailable
    (cold start / init failure at EOD).
    """
    try:
        from easy0DTE.modules.mock_options_executor import MockOptionsExecutor

        mock = MockOptionsExecutor()
        return payload_from_mock_options_executor(mock, "gcs_ledger")
    except Exception as exc:
        log.warning("EOD 0DTE GCS ledger load failed: %s", exc)
        return None


def payload_from_demo_closed_rows(
    closed_rows: List[Any],
    source: str,
    *,
    starting_balance: Optional[float] = None,
    account_balance: Optional[float] = None,
) -> Dict[str, Any]:
    """Build a consistent DEMO 0DTE EOD payload from deduped closed ledger rows."""
    import os

    seed = float(
        starting_balance
        if starting_balance is not None
        else os.getenv("0DTE_DEMO_STARTING_BALANCE", "5000")
    )
    today_rows, week_rows, all_rows = filter_positions_by_pt_date(closed_rows)
    daily_stats = stats_dict_from_positions(today_rows)
    weekly_stats = stats_dict_from_positions(week_rows)
    all_time_stats = stats_dict_from_positions(all_rows) if all_rows else None
    ledger_pnl = float((all_time_stats or {}).get("total_pnl", 0.0) or 0.0)
    bal = float(account_balance if account_balance is not None else 0.0)
    if bal <= seed:
        bal = seed + ledger_pnl
    return {
        "daily_stats": daily_stats,
        "weekly_stats": weekly_stats,
        "all_time_stats": all_time_stats,
        "account_balance": bal,
        "starting_balance": seed,
        "mode": "DEMO",
        "source": source,
        "path_breakdown": build_today_path_breakdown(closed_rows),
        "closed_count": len(closed_rows),
    }


def _demo_eod_balance_from_sources(system: Any) -> Tuple[float, float]:
    """Best-effort demo account balance and seed for unified EOD."""
    import os

    starting_balance = float(os.getenv("0DTE_DEMO_STARTING_BALANCE", "5000"))
    account_balance = 0.0
    options_executor = _options_executor_for_eod(system)
    live_mock = getattr(options_executor, "mock_executor", None) if options_executor else None
    if live_mock is not None:
        account_balance = float(getattr(live_mock, "account_balance", 0.0) or 0.0)
        starting_balance = float(
            getattr(live_mock, "starting_balance", 0.0) or starting_balance
        )
    if account_balance <= starting_balance:
        gcs_payload = load_0dte_demo_stats_from_gcs()
        if gcs_payload:
            gcs_bal = float(gcs_payload.get("account_balance", 0.0) or 0.0)
            if gcs_bal > account_balance:
                account_balance = gcs_bal
            gcs_seed = float(gcs_payload.get("starting_balance", 0.0) or 0.0)
            if gcs_seed > 0:
                starting_balance = gcs_seed
    return account_balance, starting_balance


def _options_executor_for_eod(system) -> Any:
    dte0_mgr = getattr(system, "dte0_manager", None)
    if dte0_mgr and getattr(dte0_mgr, "options_executor", None):
        return dte0_mgr.options_executor
    return None


def _daily_closed_from_payload(payload: Dict[str, Any]) -> int:
    stats = payload.get("daily_stats") or {}
    return int(stats.get("positions_closed", 0) or stats.get("total_trades", 0) or 0)


def _closed_row_dedupe_key(row: Any) -> str:
    pid = str(getattr(row, "position_id", "") or "").strip()
    if pid:
        return f"id:{pid}"
    trade_id = str(getattr(row, "trade_id", "") or "").strip()
    if trade_id:
        return f"trade:{trade_id}"
    entry_time = getattr(row, "entry_time", None)
    pnl = getattr(row, "realized_pnl", None)
    return f"anon:{entry_time}:{pnl}"


def collect_demo_eod_closed_rows(system: Any) -> Tuple[List[Any], str]:
    """
    Merge persisted GCS mock ledger, warm-instance mock executor, and in-memory
    lifecycle closes for DEMO EOD (survives Cloud Run cold start at 1:05 PM PT).
    """
    from modules.broker_live_audits import lifecycle_closed_positions

    rows: List[Any] = []
    seen: set = set()
    sources: List[str] = []

    def _add(row: Any) -> None:
        key = _closed_row_dedupe_key(row)
        if key in seen:
            return
        seen.add(key)
        rows.append(row)

    try:
        from easy0DTE.modules.mock_options_executor import MockOptionsExecutor

        gcs_mock = MockOptionsExecutor()
        for pos in getattr(gcs_mock, "closed_positions", None) or []:
            _add(pos)
        if getattr(gcs_mock, "closed_positions", None):
            sources.append("gcs_ledger")
    except Exception as exc:
        log.warning("collect_demo_eod_closed_rows | gcs_load_failed | %s", exc)

    options_executor = _options_executor_for_eod(system)
    live_mock = getattr(options_executor, "mock_executor", None) if options_executor else None
    if live_mock is not None:
        before = len(rows)
        for pos in getattr(live_mock, "closed_positions", None) or []:
            _add(pos)
        if len(rows) > before:
            sources.append("executor")

    lifecycle = dict(getattr(system, "_orb_0dte_lifecycle", None) or {})
    lifecycle_rows = lifecycle_closed_positions(lifecycle)
    if lifecycle_rows:
        before = len(rows)
        for row in lifecycle_rows:
            _add(row)
        if len(rows) > before:
            sources.append("broker_lifecycle")

    if not sources:
        return [], "demo_empty"
    if len(sources) == 1:
        return rows, sources[0]
    return rows, "+".join(sources)


def _all_time_trades_from_stats(stats: Optional[Dict[str, Any]]) -> int:
    if not stats:
        return 0
    return int(stats.get("total_trades", 0) or stats.get("positions_closed", 0) or 0)


def normalize_0dte_eod_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure display balance and all-time stats match persisted ledger totals."""
    out = dict(payload)
    starting = float(out.get("starting_balance", 0) or 0)
    all_time = dict(out.get("all_time_stats") or {})
    if all_time and not all_time.get("total_trades") and all_time.get("positions_closed"):
        all_time["total_trades"] = int(all_time["positions_closed"])
    at_pnl = float(all_time.get("total_pnl", 0) or 0)
    out["all_time_stats"] = all_time or None
    out["account_balance"] = resolve_eod_account_balance(
        float(out.get("account_balance", 0) or 0),
        starting,
        at_pnl,
    )
    return out


def _select_best_0dte_eod_payload(candidates: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Pick the payload with the richest today trade count; merge balance and all-time stats."""
    if not candidates:
        return None

    def _rank(payload: Dict[str, Any]) -> Tuple[int, int, int, float]:
        daily = _daily_closed_from_payload(payload)
        weekly = int((payload.get("weekly_stats") or {}).get("positions_closed", 0) or 0)
        source = str(payload.get("source") or "")
        if "gcs_ledger" in source or source == "executor":
            source_pref = 3
        elif "broker_lifecycle_gcs" in source:
            source_pref = 2
        elif "executor" in source:
            source_pref = 2
        else:
            source_pref = 1
        balance = float(payload.get("account_balance", 0) or 0)
        return (daily, weekly, source_pref, balance)

    best = max(candidates, key=_rank)
    merged = dict(best)
    import os

    merge_candidate_balances = os.getenv("MODE", "demo").strip().lower() not in (
        "demo",
        "paper",
        "sim",
    )
    for candidate in candidates:
        if merge_candidate_balances:
            bal = float(candidate.get("account_balance", 0) or 0)
            if bal > float(merged.get("account_balance", 0) or 0):
                merged["account_balance"] = bal
        seed = float(candidate.get("starting_balance", 0) or 0)
        if seed > 0 and float(merged.get("starting_balance", 0) or 0) <= 0:
            merged["starting_balance"] = seed
        cand_at = candidate.get("all_time_stats") or {}
        merged_at = merged.get("all_time_stats") or {}
        cand_trades = _all_time_trades_from_stats(cand_at)
        merged_trades = _all_time_trades_from_stats(merged_at)
        cand_pnl = abs(float(cand_at.get("total_pnl", 0) or 0))
        merged_pnl = abs(float(merged_at.get("total_pnl", 0) or 0))
        if cand_trades > merged_trades or (
            cand_trades == merged_trades and cand_pnl > merged_pnl
        ):
            merged["all_time_stats"] = cand_at
        if not merged.get("path_breakdown") and candidate.get("path_breakdown"):
            merged["path_breakdown"] = candidate["path_breakdown"]
    return merged


def _zero_0dte_eod_payload() -> Dict[str, Any]:
    import os

    from modules.runtime_mode import mode_label as _runtime_mode_label

    mode = _runtime_mode_label()
    from modules.unified_0dte_account import unified_demo_starting_balance

    starting_balance = float(unified_demo_starting_balance())
    empty = empty_trade_stats()
    return {
        "daily_stats": empty,
        "weekly_stats": dict(empty),
        "all_time_stats": None,
        "account_balance": starting_balance,
        "starting_balance": starting_balance,
        "mode": mode,
        "source": "zero_fallback",
    }


def _live_gcs_eod_payload_from_rows(rows: List[Any]) -> Dict[str, Any]:
    import os

    from modules.runtime_mode import mode_label as _runtime_mode_label

    today_rows, week_rows, all_rows = filter_positions_by_pt_date(rows)
    starting_balance = float(
        os.getenv("0DTE_LIVE_STARTING_BALANCE", os.getenv("0DTE_DEMO_STARTING_BALANCE", "5000"))
    )
    all_time_stats = stats_dict_from_positions(all_rows)
    at_pnl = float(all_time_stats.get("total_pnl", 0) or 0)
    return {
        "daily_stats": stats_dict_from_positions(today_rows),
        "weekly_stats": stats_dict_from_positions(week_rows),
        "all_time_stats": all_time_stats,
        "account_balance": resolve_eod_account_balance(0.0, starting_balance, at_pnl),
        "starting_balance": starting_balance,
        "mode": _runtime_mode_label(),
        "source": "broker_lifecycle_gcs",
        "closed_count": len(rows),
    }


def resolve_orb_0dte_eod_payload(system) -> Dict[str, Any]:
    """
    Resolve stats for unified 0DTE EOD from all durable sources (executor memory, GCS
    mock_options_history.json, broker lifecycle) and pick the richest today trade set.

    Includes ORB1 Breakout and Trendline 0DTE closes on the shared demo ledger.
    """
    from modules.runtime_mode import is_demo_mode, is_live_mode

    if is_demo_mode():
        closed_rows, source_tag = collect_demo_eod_closed_rows(system)
        if closed_rows:
            account_balance, starting_balance = _demo_eod_balance_from_sources(system)
            payload = payload_from_demo_closed_rows(
                closed_rows,
                source_tag,
                starting_balance=starting_balance,
                account_balance=account_balance,
            )
            payload = normalize_0dte_eod_payload(payload)
            log.info(
                "EOD_ORB_0DTE | resolved source=%s | daily_trades=%s | balance=$%.2f | demo_ledger_rows=%s",
                payload.get("source"),
                _daily_closed_from_payload(payload),
                float(payload.get("account_balance", 0) or 0),
                len(closed_rows),
            )
            return payload

    candidates: List[Dict[str, Any]] = []
    options_executor = _options_executor_for_eod(system)

    if (
        options_executor
        and getattr(options_executor, "demo_mode", False)
        and getattr(options_executor, "mock_executor", None)
    ):
        candidates.append(
            payload_from_mock_options_executor(options_executor.mock_executor, "executor")
        )

    if is_demo_mode():
        gcs_payload = load_0dte_demo_stats_from_gcs()
        if gcs_payload:
            gcs_payload = dict(gcs_payload)
            gcs_payload["source"] = gcs_payload.get("source", "gcs_ledger")
            candidates.append(gcs_payload)

    if options_executor:
        from modules.broker_live_audits import payload_from_live_broker_truth

        broker_payload = payload_from_live_broker_truth(system, options_executor)
        if broker_payload.get("closed_count"):
            closed_rows, _ = collect_demo_eod_closed_rows(system) if is_demo_mode() else ([], "")
            if closed_rows and not broker_payload.get("path_breakdown"):
                broker_payload = dict(broker_payload)
                broker_payload["path_breakdown"] = build_today_path_breakdown(closed_rows)
        candidates.append(broker_payload)

    if is_live_mode() and not any(_daily_closed_from_payload(c) > 0 for c in candidates):
        try:
            from modules.live_options_ledger import load_live_ledger_for_eod_fallback

            rows = load_live_ledger_for_eod_fallback()
            if rows:
                candidates.append(_live_gcs_eod_payload_from_rows(rows))
        except ImportError:
            pass

    best = _select_best_0dte_eod_payload(candidates)
    if best:
        if _all_time_trades_from_stats(best.get("all_time_stats")) == 0:
            enrich: List[Dict[str, Any]] = []
            if is_demo_mode():
                gcs_payload = load_0dte_demo_stats_from_gcs()
                if gcs_payload:
                    enrich.append(gcs_payload)
            elif is_live_mode():
                try:
                    from modules.live_options_ledger import load_live_ledger_for_eod_fallback

                    rows = load_live_ledger_for_eod_fallback()
                    if rows:
                        enrich.append(_live_gcs_eod_payload_from_rows(rows))
                except ImportError:
                    pass
            if enrich:
                best = _select_best_0dte_eod_payload([best] + enrich) or best
        best = normalize_0dte_eod_payload(best)
        log.info(
            "EOD_ORB_0DTE | resolved source=%s | daily_trades=%s | balance=$%.2f | candidates=%s",
            best.get("source"),
            _daily_closed_from_payload(best),
            float(best.get("account_balance", 0) or 0),
            [c.get("source") for c in candidates],
        )
        return best

    return normalize_0dte_eod_payload(_zero_0dte_eod_payload())


def resolve_unified_0dte_eod_payload(system) -> Dict[str, Any]:
    """Combined ORB + Trendline 0DTE EOD stats from the unified options ledger."""
    return resolve_orb_0dte_eod_payload(system)
