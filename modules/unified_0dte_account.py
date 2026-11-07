#!/usr/bin/env python3
"""
Unified 0DTE account — ORB1 Breakout + Trendline 0DTE share one balance and position registry.

Demo: MockOptionsExecutor ($5k simulated ledger in mock_options_history.json).
Live: BrokerAdapter + OptionsTradingExecutor.positions (fill-confirmed).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional, Tuple

log = logging.getLogger(__name__)


def get_options_executor(system: Any) -> Any:
    dte0 = getattr(system, "dte0_manager", None)
    if dte0 and getattr(dte0, "options_executor", None):
        return dte0.options_executor
    return None


_CFG_MISSING = object()


def _int_cfg_aliases(
    keys: tuple,
    fallback: int,
    get_config_value=None,
) -> int:
    """Resolve first present key from canonical → legacy alias chain."""
    if get_config_value is None:
        try:
            from modules.config_loader import get_config_value as _g

            get_config_value = _g
        except ImportError:
            return int(fallback)
    for key in keys:
        try:
            raw = get_config_value(key, _CFG_MISSING, convert_type=False)
        except TypeError:
            try:
                raw = get_config_value(key, _CFG_MISSING)
            except TypeError:
                raw = get_config_value(key, None)
        if raw is _CFG_MISSING or raw is None:
            continue
        try:
            return int(float(str(raw).strip()))
        except (TypeError, ValueError):
            continue
    return int(fallback)


def unified_0dte_slot_count(get_config_value=None) -> int:
    """Equal-slot sizing divisor (tradable equity / N). Must equal TOTAL_0DTE_MAX_POSITIONS.

    Canonical: Account0DTE.env ``0DTE_SLOT_COUNT``. If it differs from TOTAL, TOTAL wins
    so 1/N sizing matches the combined open-count cap.
    """
    n = _int_cfg_aliases(
        ("0DTE_SLOT_COUNT", "TRENDLINE_SLOT_COUNT"),
        0,
        get_config_value,
    )
    total_n = _int_cfg_aliases(
        (
            "TOTAL_0DTE_MAX_POSITIONS",
            "UNIFIED_0DTE_MAX_TOTAL_POSITIONS",
            "MAX_TOTAL_OPTION_POSITIONS",
        ),
        0,
        get_config_value,
    )
    if n > 0:
        if total_n > 0 and n != total_n:
            log.warning(
                "0DTE_SLOT_COUNT=%d differs from TOTAL_0DTE_MAX_POSITIONS=%d — "
                "using TOTAL so slot sizing matches the combined book cap",
                n,
                total_n,
            )
            return total_n
        return n
    if total_n > 0:
        return total_n
    log.warning(
        "0DTE_SLOT_COUNT missing from Account0DTE.env / env — fail-closed slot_count=1"
    )
    return 1


def orb_0dte_max_positions(get_config_value=None) -> int:
    """ORB1 Breakout open-count cap. Canonical: ``ORB_0DTE_MAX_POSITIONS``."""
    return _int_cfg_aliases(
        ("ORB_0DTE_MAX_POSITIONS", "UNIFIED_0DTE_ORB_MAX_POSITIONS"),
        unified_0dte_slot_count(get_config_value),
        get_config_value,
    )


def breakout_0dte_max_positions(get_config_value=None) -> int:
    """ORB2 Breakout open-count cap. Canonical: ``BREAKOUT_0DTE_MAX_POSITIONS``."""
    return _int_cfg_aliases(
        (
            "BREAKOUT_0DTE_MAX_POSITIONS",
            "ORB2_0DTE_MAX_POSITIONS",
            "UNIFIED_0DTE_BREAKOUT_MAX_POSITIONS",
        ),
        orb_0dte_max_positions(get_config_value),
        get_config_value,
    )


def trendline_0dte_max_positions(get_config_value=None) -> int:
    """Trendline 0DTE open-count cap. Canonical: ``TRENDLINE_0DTE_MAX_POSITIONS``.

    Not ``0DTE_SLOT_COUNT`` — slot count sizes dollars; this gates how many TL names
    may be open.
    """
    return _int_cfg_aliases(
        (
            "TRENDLINE_0DTE_MAX_POSITIONS",
            "TRENDLINE_MAX_OPEN_POSITIONS",
            "UNIFIED_0DTE_TRENDLINE_MAX_POSITIONS",
        ),
        unified_0dte_slot_count(get_config_value),
        get_config_value,
    )


def total_0dte_max_positions(get_config_value=None) -> int:
    """Combined 0DTE open-count cap (ORB1 + ORB2 + Trendline). One variable.

    Canonical: Account0DTE.env ``TOTAL_0DTE_MAX_POSITIONS``.
    ``UNIFIED_0DTE_MAX_TOTAL_POSITIONS`` / ``MAX_TOTAL_OPTION_POSITIONS`` are leftover
    aliases only — do not set them differently.
    """
    n = _int_cfg_aliases(
        (
            "TOTAL_0DTE_MAX_POSITIONS",
            "UNIFIED_0DTE_MAX_TOTAL_POSITIONS",
            "MAX_TOTAL_OPTION_POSITIONS",
        ),
        0,
        get_config_value,
    )
    if n > 0:
        return n
    log.warning(
        "TOTAL_0DTE_MAX_POSITIONS missing from Account0DTE.env — fail-closed total=1"
    )
    return 1


def orb_0dte_priority_rank_final_batch_max(get_config_value=None) -> int:
    """Legacy 7:30 ORB1 top-N funnel. Rolling ORB1/ORB2 do not use this as a book cap.

    If the old key is still in env, honor it. Otherwise use ``ORB_0DTE_MAX_POSITIONS``.
    """
    n = _int_cfg_aliases(
        (
            "ORB_0DTE_PRIORITY_RANK_FINAL_BATCH_MAX",
            "ORB_0DTE_PRIORITY_RANK_BATCH_MAX_POSITIONS",
            "ORB_0DTE_BATCH_MAX_POSITIONS",
            "0DTE_MAX_POSITIONS",
        ),
        0,
        get_config_value,
    )
    if n > 0:
        return n
    return orb_0dte_max_positions(get_config_value)


def orb_0dte_priority_rank_batch_max_positions(get_config_value=None) -> int:
    """Deprecated alias — use orb_0dte_max_positions()."""
    return orb_0dte_priority_rank_final_batch_max(get_config_value)


def orb_0dte_batch_max_positions(get_config_value=None) -> int:
    """Deprecated alias — use orb_0dte_max_positions()."""
    return orb_0dte_priority_rank_final_batch_max(get_config_value)


def _float_cfg(key: str, fallback: float, get_config_value=None) -> float:
    if get_config_value is None:
        try:
            from modules.config_loader import get_config_value as _g

            get_config_value = _g
        except ImportError:
            return float(fallback)
    try:
        return float(get_config_value(key, str(fallback)))
    except (TypeError, ValueError):
        return float(fallback)


def unified_trading_capital_pct(get_config_value=None) -> float:
    return _float_cfg("0DTE_TRADING_CAPITAL_PCT", 90.0, get_config_value)


def unified_max_position_size_pct(get_config_value=None) -> float:
    return _float_cfg("0DTE_MAX_POSITION_SIZE_PCT", 35.0, get_config_value)


def unified_max_position_size_sum(get_config_value=None) -> float:
    """
    Optional absolute per-position cap (USD). When > 0, each new 0DTE open is capped at
    min(pct cap, sum cap). Set 0 / off / NA to use pct cap only (default 35%).
    """
    if get_config_value is None:
        try:
            from modules.config_loader import get_config_value as _g

            get_config_value = _g
        except ImportError:
            return 0.0
    raw = get_config_value("0DTE_MAX_POSITION_SIZE_SUM", "0")
    if raw is None:
        return 0.0
    token = str(raw).strip().lower()
    if token in ("", "0", "off", "na", "n/a", "none", "false"):
        return 0.0
    try:
        return max(0.0, float(token))
    except (TypeError, ValueError):
        return 0.0


def effective_max_single_position_usd(
    account_balance: float,
    *,
    max_position_pct: float,
    max_position_sum_usd: float = 0.0,
) -> float:
    """Pct cap × optional absolute sum cap (tighter of the two when sum > 0)."""
    balance = max(0.0, float(account_balance))
    pct_cap = balance * (float(max_position_pct) / 100.0)
    sum_cap = max(0.0, float(max_position_sum_usd or 0.0))
    if sum_cap > 0.0:
        return min(pct_cap, sum_cap)
    return pct_cap


def unified_max_total_positions(get_config_value=None) -> int:
    """Alias for total_0dte_max_positions (combined ORB + Trendline book)."""
    return total_0dte_max_positions(get_config_value)


def unified_demo_starting_balance(get_config_value=None) -> float:
    return _float_cfg("0DTE_DEMO_STARTING_BALANCE", 5000.0, get_config_value)


def unified_trendline_slot_count(get_config_value=None) -> int:
    """Back-compat alias for ``unified_0dte_slot_count``."""
    return unified_0dte_slot_count(get_config_value)


def _float_cfg_aliases(keys: tuple, fallback: float, get_config_value=None) -> float:
    if get_config_value is None:
        try:
            from modules.config_loader import get_config_value as _g

            get_config_value = _g
        except ImportError:
            return float(fallback)
    for key in keys:
        try:
            raw = get_config_value(key, None)
        except Exception:
            raw = None
        if raw is None:
            continue
        token = str(raw).strip()
        if not token:
            continue
        try:
            return float(token)
        except (TypeError, ValueError):
            continue
    return float(fallback)


def _mock_executor(executor: Any) -> Any:
    return getattr(executor, "mock_executor", None) if executor else None


def _runtime_is_demo(executor: Any = None) -> bool:
    try:
        from modules.runtime_mode import is_demo_mode

        runtime_demo = is_demo_mode()
    except ImportError:
        runtime_demo = False
    if executor is not None and getattr(executor, "demo_mode", False):
        return True
    return bool(runtime_demo)


def _read_cached_cash_balance(system: Any) -> Optional[float]:
    for attr in ("_unified_0dte_cash_balance", "_unified_0dte_cash_available"):
        raw = getattr(system, attr, None)
        if raw is None:
            continue
        try:
            val = float(raw)
            if val >= 0.0:
                return val
        except (TypeError, ValueError):
            continue
    return None


def _mark_live_account_state_valid(system: Any, valid: bool) -> None:
    system._unified_0dte_account_state_valid = bool(valid)


def _write_live_cash_cache(system: Any, bal: float, *, broker_ok: bool) -> float:
    cash = max(0.0, float(bal))
    system._unified_0dte_cash_balance = cash
    system._unified_0dte_cash_available = cash
    if broker_ok:
        system._unified_0dte_broker_balance_cached_at = time.time()
        _mark_live_account_state_valid(system, True)
    else:
        _mark_live_account_state_valid(system, False)
    return cash


def _pt_trading_date_iso() -> str:
    try:
        from modules.eod_report_helpers import pt_trading_date_iso

        return pt_trading_date_iso()
    except ImportError:
        import datetime
        from zoneinfo import ZoneInfo

        return datetime.datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()


def _mock_ledger_stats_trading_date(mock: Any) -> str:
    raw = getattr(mock, "stats_trading_date", None)
    if raw is not None and str(raw).strip():
        return str(raw).strip()
    cur = getattr(mock, "current_trading_date", None)
    if hasattr(cur, "isoformat"):
        return cur.isoformat()
    return ""


def _day_pnl_pct_from_realized(
    day_pnl: float,
    *,
    account_balance: Optional[float] = None,
    starting_balance: Optional[float] = None,
) -> float:
    day = float(day_pnl or 0.0)
    acct = float(account_balance) if account_balance is not None else 0.0
    start = float(starting_balance) if starting_balance is not None else unified_demo_starting_balance()
    base = acct - day if acct > day else start
    if base <= 0.0:
        base = start if start > 0.0 else unified_demo_starting_balance()
    return (day / base * 100.0) if base > 0.0 else 0.0


def _hydrate_risk_halt_cleared_from_mock(system: Any) -> None:
    """Restore EOD halt-clear marker from persisted demo ledger (survives Cloud Run restart)."""
    if getattr(system, "_unified_0dte_risk_halt_cleared_pt_date", None):
        return
    executor = get_options_executor(system)
    if not executor or not _runtime_is_demo(executor):
        return
    mock = _mock_executor(executor)
    if mock is None:
        return
    raw = getattr(mock, "risk_halt_cleared_pt_date", None)
    if raw is not None and str(raw).strip():
        system._unified_0dte_risk_halt_cleared_pt_date = str(raw).strip()


def _persist_risk_halt_cleared_to_mock(system: Any, cleared_pt: Optional[str]) -> None:
    """Persist EOD halt-clear marker on the demo account ledger (GCS-backed)."""
    executor = get_options_executor(system)
    if not executor or not _runtime_is_demo(executor):
        return
    mock = _mock_executor(executor)
    if mock is None:
        return
    mock.risk_halt_cleared_pt_date = str(cleared_pt).strip() if cleared_pt else None
    try:
        mock._save_mock_data()
    except Exception as exc:
        log.debug("UNIFIED_0DTE_ACCOUNT | risk_halt_persist_failed | error=%s", exc)


def sync_unified_0dte_day_pnl_from_account(system: Any) -> float:
    """
    Account-scoped day P&L % for unified risk (ORB + Trendline on one ledger).

    Demo: mock ledger daily_stats.total_pnl when stats_trading_date matches PT today.
    Live: in-memory session tracker updated on confirmed closes.
    """
    _hydrate_risk_halt_cleared_from_mock(system)
    today = _pt_trading_date_iso()
    cleared_pt = str(getattr(system, "_unified_0dte_risk_halt_cleared_pt_date", "") or "")
    if cleared_pt == today:
        system._unified_0dte_day_realized_pnl = 0.0
        system._unified_0dte_day_pnl_pct = 0.0
        return 0.0

    executor = get_options_executor(system)
    if executor and _runtime_is_demo(executor):
        mock = _mock_executor(executor)
        if mock is not None:
            stats_date = _mock_ledger_stats_trading_date(mock)
            if stats_date != today:
                day_pnl = 0.0
            else:
                stats = getattr(mock, "daily_stats", None) or {}
                day_pnl = float(stats.get("total_pnl", 0.0) or 0.0)
            acct = float(getattr(mock, "account_balance", 0.0) or 0.0)
            start = float(getattr(mock, "starting_balance", 0.0) or unified_demo_starting_balance())
            pct = _day_pnl_pct_from_realized(
                day_pnl,
                account_balance=acct,
                starting_balance=start,
            )
            system._unified_0dte_day_realized_pnl = day_pnl
            system._unified_0dte_day_pnl_pct = pct
            return pct

    return float(getattr(system, "_unified_0dte_day_pnl_pct", 0.0) or 0.0)


def get_unified_0dte_day_pnl_pct(system: Any) -> float:
    """Refresh and return account-scoped intraday realized P&L % for risk hooks."""
    return sync_unified_0dte_day_pnl_from_account(system)


def hydrate_unified_0dte_risk_from_account(system: Any) -> float:
    """Startup: bind UAR P4 halt state to the traded account ledger."""
    return sync_unified_0dte_day_pnl_from_account(system)


def reset_unified_0dte_day_pnl_session(system: Any, *, reason: str = "session_open") -> None:
    """Reset intraday realized P&L halt trackers (session open or EOD)."""
    prev_pct = float(getattr(system, "_unified_0dte_day_pnl_pct", 0.0) or 0.0)
    prev_pnl = float(getattr(system, "_unified_0dte_day_realized_pnl", 0.0) or 0.0)
    system._unified_0dte_day_realized_pnl = 0.0
    system._unified_0dte_day_pnl_pct = 0.0
    today = _pt_trading_date_iso()
    reason_norm = str(reason or "").strip().lower()
    if reason_norm in ("eod", "end_of_day_exit", "end_of_day"):
        system._unified_0dte_risk_halt_cleared_pt_date = today
        _persist_risk_halt_cleared_to_mock(system, today)
    elif reason_norm in ("session_open", "daily_open", "daily_reset"):
        system._unified_0dte_risk_halt_cleared_pt_date = None
        _persist_risk_halt_cleared_to_mock(system, None)
    if prev_pct != 0.0 or prev_pnl != 0.0 or reason != "session_open":
        log.info(
            "UNIFIED_0DTE_ACCOUNT | action=day_pnl_session_reset | reason=%s | pt_date=%s | "
            "prev_day_pnl=%.2f | prev_day_pnl_pct=%.4f | halt_cleared=%s",
            reason,
            today,
            prev_pnl,
            prev_pct,
            str(getattr(system, "_unified_0dte_risk_halt_cleared_pt_date", "") or ""),
        )


def record_unified_0dte_realized_close(
    system: Any,
    realized_pnl: float,
    *,
    account_balance: Optional[float] = None,
) -> float:
    """
    Bump session day P&L % after a confirmed 0DTE close (demo or live).

    Used by Unified0DTEPortfolioRiskManager P4 daily-loss halt hook.
    """
    try:
        executor = get_options_executor(system)
        if executor and _runtime_is_demo(executor):
            return sync_unified_0dte_day_pnl_from_account(system)

        day_pnl = float(getattr(system, "_unified_0dte_day_realized_pnl", 0.0) or 0.0)
        day_pnl += float(realized_pnl or 0.0)
        acct = (
            float(account_balance)
            if account_balance is not None
            else float(sync_unified_account_balance(system))
        )
        pct = _day_pnl_pct_from_realized(day_pnl, account_balance=acct)
        system._unified_0dte_day_realized_pnl = day_pnl
        system._unified_0dte_day_pnl_pct = float(pct)
        return float(pct)
    except Exception as exc:
        log.debug("UNIFIED_0DTE_ACCOUNT | day_pnl_update_failed | error=%s", exc)
        return float(getattr(system, "_unified_0dte_day_pnl_pct", 0.0) or 0.0)


def sync_unified_account_balance(system: Any) -> float:
    """
    Read cash balance from the unified ledger (demo mock or live broker sync).

    Caches on system._unified_0dte_cash_balance for Trendline slot refresh between async broker reads.
    """
    executor = get_options_executor(system)
    demo_default = unified_demo_starting_balance()
    if not executor:
        if _runtime_is_demo():
            system._unified_0dte_cash_balance = demo_default
            return demo_default
        cached = _read_cached_cash_balance(system)
        if cached is not None:
            return cached
        _mark_live_account_state_valid(system, False)
        return 0.0

    if _runtime_is_demo(executor):
        mock = _mock_executor(executor)
        bal = demo_default
        if mock is not None:
            bal = float(getattr(mock, "account_balance", bal) or bal)
        system._unified_0dte_cash_balance = bal
        return bal

    cached = _read_cached_cash_balance(system)
    bal: Optional[float] = None
    api = getattr(executor, "etrade_options_api", None)
    if api and getattr(api, "is_available", lambda: False)() and getattr(api, "etrade", None):
        try:
            obj = api.etrade.get_account_balance()
            if obj:
                read = float(obj.cash_available_for_investment or 0.0) or float(
                    obj.account_value or 0.0
                )
                if read > 0.0:
                    bal = read
        except Exception as exc:
            log.warning("UNIFIED_0DTE_ACCOUNT | balance_read_failed | error=%s", exc)

    if bal is not None and bal > 0.0:
        return _write_live_cash_cache(system, bal, broker_ok=True)
    if cached is not None:
        log.warning(
            "UNIFIED_0DTE_ACCOUNT | balance_stale | mode=live | action=use_cache | cash=%.2f",
            cached,
        )
        _mark_live_account_state_valid(system, False)
        return cached
    log.warning("UNIFIED_0DTE_ACCOUNT | balance_unavailable | mode=live | action=return_zero")
    _mark_live_account_state_valid(system, False)
    return 0.0


def sync_unified_cash_available(system: Any) -> float:
    """Cash available for new debits (demo: mock available_balance; live: cached broker cash)."""
    executor = get_options_executor(system)
    try:
        from modules.runtime_mode import is_demo_mode

        runtime_demo = is_demo_mode()
    except ImportError:
        runtime_demo = False
    if executor and (runtime_demo or getattr(executor, "demo_mode", False)):
        mock = _mock_executor(executor)
        if mock is not None:
            fn = getattr(mock, "available_balance", None)
            if callable(fn):
                cash = float(fn())
            else:
                cash = float(getattr(mock, "account_balance", 0.0) or 0.0)
            system._unified_0dte_cash_available = cash
            return cash
    cash = float(getattr(system, "_unified_0dte_cash_balance", None) or sync_unified_account_balance(system))
    system._unified_0dte_cash_available = cash
    return cash


async def refresh_unified_account_from_broker(system: Any) -> float:
    """Live: pull fresh broker cash after ORB batch so Trendline uses remainder."""
    executor = get_options_executor(system)
    demo_default = unified_demo_starting_balance()
    if not executor:
        if _runtime_is_demo():
            system._unified_0dte_cash_balance = demo_default
            system._unified_0dte_cash_available = demo_default
            return demo_default
        cached = _read_cached_cash_balance(system)
        if cached is not None:
            _mark_live_account_state_valid(system, False)
            return cached
        _mark_live_account_state_valid(system, False)
        return 0.0

    if _runtime_is_demo(executor):
        bal = sync_unified_cash_available(system)
        log.info(
            "UNIFIED_0DTE_ACCOUNT | action=refresh | mode=demo | cash_balance=%.2f | cash_available=%.2f",
            sync_unified_account_balance(system),
            bal,
        )
        return bal

    cached = _read_cached_cash_balance(system)
    bal: Optional[float] = None
    broker_ok = False

    adapter = getattr(executor, "broker_adapter", None)
    if adapter and getattr(adapter, "is_available", lambda: False)():
        try:
            bobj = await adapter.get_account_balance()
            if bobj is not None:
                ca = float(getattr(bobj, "cash_available", 0.0) or 0.0)
                av = float(getattr(bobj, "account_value", 0.0) or 0.0)
                bp = float(getattr(bobj, "buying_power", 0.0) or 0.0)
                read = ca if ca > 0.0 else (bp if bp > 0.0 else av)
                if read > 0.0:
                    bal = read
                    broker_ok = True
        except Exception as exc:
            log.warning("UNIFIED_0DTE_ACCOUNT | broker_refresh_failed | error=%s", exc)
    if bal is None:
        api = getattr(executor, "etrade_options_api", None)
        if api and getattr(api, "is_available", lambda: False)() and getattr(api, "etrade", None):
            try:
                obj = api.etrade.get_account_balance()
                if obj:
                    read = float(obj.cash_available_for_investment or 0.0) or float(
                        obj.account_value or 0.0
                    )
                    if read > 0.0:
                        bal = read
                        broker_ok = True
            except Exception as exc:
                log.warning("UNIFIED_0DTE_ACCOUNT | etrade_refresh_failed | error=%s", exc)

    if broker_ok and bal is not None:
        out = _write_live_cash_cache(system, bal, broker_ok=True)
        log.info(
            "UNIFIED_0DTE_ACCOUNT | action=refresh | mode=live | cash_available=%.2f | open_positions=%d",
            out,
            count_unified_open_positions(system),
        )
        return out

    if cached is not None:
        log.warning(
            "UNIFIED_0DTE_ACCOUNT | action=refresh | mode=live | status=stale_cache | "
            "cash_available=%.2f | open_positions=%d",
            cached,
            count_unified_open_positions(system),
        )
        _mark_live_account_state_valid(system, False)
        return cached

    log.warning(
        "UNIFIED_0DTE_ACCOUNT | action=refresh | mode=live | status=unavailable | cash_available=0.00 | "
        "open_positions=%d",
        count_unified_open_positions(system),
    )
    _mark_live_account_state_valid(system, False)
    return 0.0


def _open_positions_from_executor(system: Any) -> list:
    executor = get_options_executor(system)
    if not executor:
        return []
    try:
        return list(executor.get_open_positions() or [])
    except Exception:
        return []


def list_unified_open_positions(system: Any) -> list:
    """Single registry: OptionsTradingExecutor (demo mock + live book)."""
    return _open_positions_from_executor(system)


def count_unified_open_positions(system: Any) -> int:
    """Single registry: OptionsTradingExecutor (demo mock + live book)."""
    return len(_open_positions_from_executor(system))


def _deployed_entry_costs(system: Any) -> float:
    total = 0.0
    for pos in _open_positions_from_executor(system):
        try:
            qty = max(1, int(getattr(pos, "quantity", 1) or 1))
            ep = float(getattr(pos, "entry_price", 0.0) or 0.0)
            if ep <= 0 and getattr(pos, "lotto_contract", None):
                ep = float(getattr(pos.lotto_contract, "mid_price", 0.0) or 0.0)
            total += ep * qty * 100.0
        except (TypeError, ValueError):
            continue
    return total


def compute_unified_0dte_capital_snapshot(
    system: Any,
    *,
    buffer_floor: float,
    get_config_value=None,
) -> Dict[str, float]:
    """
    Shared ORB + Trendline slot snapshot: sacred buffer + equal 1/N of tradable equity.

    entry_budget = min(slot_capital, available_cash) — deploy released proceeds when below
    full 1/N target; skip only when available_cash <= 0.
    """
    from easyTrendline.trendline_capital_allocation import (
        buffer_pct_from_allocation,
        compute_0dte_capital_snapshot,
        compute_session_buffer_floor,
    )

    cash = sync_unified_cash_available(system)
    cash_balance = sync_unified_account_balance(system)
    reserved = _deployed_entry_costs(system)
    alloc_pct = unified_trading_capital_pct(get_config_value)
    slot_count = unified_0dte_slot_count(get_config_value)
    buf_pct = buffer_pct_from_allocation(alloc_pct)

    if buffer_floor <= 0.0:
        buffer_floor = compute_session_buffer_floor(
            max(cash_balance + reserved, cash_balance),
            buffer_pct=buf_pct,
        )

    snap = compute_0dte_capital_snapshot(
        cash_balance=cash_balance,
        open_entry_costs=reserved,
        allocation_pct=alloc_pct,
        slot_count=slot_count,
        buffer_floor=buffer_floor,
    )
    slot_capital = float(snap.slot_capital)
    entry_budget = float(snap.entry_budget)
    sum_cap = unified_max_position_size_sum(get_config_value)
    if sum_cap > 0.0 and slot_capital > sum_cap:
        from easyTrendline.trendline_capital_allocation import compute_entry_budget

        slot_capital = sum_cap
        entry_budget = compute_entry_budget(slot_capital, float(snap.available_cash))
        log.info(
            "0DTE_POSITION_SIZE_SUM_CAP | path=0dte | slot_cap_before=%.2f | sum_cap=%.2f | "
            "slot_cap_after=%.2f | entry_budget=%.2f",
            float(snap.slot_capital),
            sum_cap,
            slot_capital,
            entry_budget,
        )
    return {
        "equity_basis": float(snap.equity_basis),
        "cash_balance": float(snap.cash_balance),
        "cash_available": cash,
        "reserved_open_entry_cost": float(snap.reserved_open_entry_cost),
        "buffer_reserve": float(snap.buffer_reserve),
        "buffer_reserve_floor": float(snap.buffer_reserve_floor),
        "tradable_equity": float(snap.tradable_equity),
        "available_cash": float(snap.available_cash),
        "slot_capital": slot_capital,
        "entry_budget": entry_budget,
        "allocation_pct": alloc_pct,
        "slot_count": float(slot_count),
    }


# Back-compat alias — Trendline call sites.
compute_unified_trendline_capital_snapshot = compute_unified_0dte_capital_snapshot


def apply_unified_0dte_capital_to_system(system: Any, snap: Dict[str, float]) -> None:
    system._trendline_buffer_reserve = float(snap.get("buffer_reserve", 0.0))
    system._trendline_available_cash = float(snap.get("available_cash", 0.0))
    system._trendline_slot_capital = float(snap.get("slot_capital", 0.0))
    system._trendline_entry_budget = float(snap.get("entry_budget", 0.0))
    # Keep GCS daily-slot claims on the same N as Account0DTE.env (never a hardcoded 10).
    try:
        slot_n = int(float(snap.get("slot_count", 0) or 0))
    except (TypeError, ValueError):
        slot_n = 0
    if slot_n <= 0:
        slot_n = int(unified_0dte_slot_count())
    slot_n = max(1, slot_n)
    try:
        max_open = max(1, int(trendline_0dte_max_positions()))
    except Exception:
        max_open = slot_n
    prev = int(getattr(system, "_trendline_max_open_positions", 0) or 0)
    system._trendline_slot_count = slot_n
    system._trendline_max_open_positions = max_open
    if prev and prev != max_open:
        log.warning(
            "0DTE_PIPELINE | stage=open_caps_synced | was_max_open=%d | now_max_open=%d | slot_count=%d",
            prev,
            max_open,
            slot_n,
        )


apply_unified_trendline_capital_to_system = apply_unified_0dte_capital_to_system


def unified_account_equity(system: Any) -> float:
    """Cash plus capital deployed in open unified 0DTE positions (ORB + Trendline)."""
    return float(sync_unified_account_balance(system)) + float(_deployed_entry_costs(system))


def _position_strategy_type(pos: Any) -> str:
    """Best-effort strategy_type from position / metadata blocks.

    Prefer Breakout when any block says breakout — Jul 27 fills mistagged
    ``normalized_options.strategy_type=trendline_0dte`` while ``source_path=breakout_0dte``,
    which wrongly parked Breakout rows on the Trendline open-set.
    """
    st_attr = str(getattr(pos, "strategy_type", "") or "").strip().lower()
    if "breakout" in st_attr:
        return st_attr or "breakout_0dte"
    md = getattr(pos, "metadata", None)
    if isinstance(md, dict):
        for block_key in ("normalized_options", "execution_payload", "signal_snapshot"):
            block = md.get(block_key)
            if not isinstance(block, dict):
                continue
            for key in ("strategy_type", "source_path", "orb_strategy_type"):
                raw = str(block.get(key) or "").strip().lower()
                if "breakout" in raw:
                    return "breakout_0dte"
        if "breakout" in str(md.get("source_path") or "").strip().lower():
            return "breakout_0dte"
        norm = md.get("normalized_options")
        if isinstance(norm, dict):
            st = str(
                norm.get("strategy_type") or norm.get("orb_strategy_type") or ""
            ).strip().lower()
            if st:
                return st
        ep = md.get("execution_payload")
        if isinstance(ep, dict):
            st = str(ep.get("strategy_type") or "").strip().lower()
            if st:
                return st
    return st_attr


def list_open_positions_by_strategy(
    system: Any,
    *,
    strategy_substring: str,
    exclude_substring: Optional[str] = None,
) -> list:
    """
    Open 0DTE rows from the unified executor registry filtered by strategy_type.

    Used for both ORB (exclude trendline / match orb) and Trendline paths so
    monitor open-sets and watch counts stay consistent.
    """
    executor = get_options_executor(system)
    mock = getattr(executor, "mock_executor", None) if executor else None
    is_closed = getattr(mock, "is_position_closed", None) if mock else None
    needle = str(strategy_substring or "").strip().lower()
    exclude = str(exclude_substring or "").strip().lower()
    out = []
    for pos in _open_positions_from_executor(system):
        st = _position_strategy_type(pos)
        if needle:
            if needle not in st:
                continue
        elif exclude and exclude in st:
            continue
        pid = str(getattr(pos, "position_id", "") or "").strip()
        if pid and callable(is_closed) and is_closed(pid):
            continue
        out.append(pos)
    return out


def classify_0dte_path(pos_or_strategy: Any) -> str:
    """Map a position or strategy string to orb_0dte / trendline_0dte / breakout_0dte."""
    if pos_or_strategy is not None and not isinstance(pos_or_strategy, str):
        st = _position_strategy_type(pos_or_strategy)
    else:
        st = str(pos_or_strategy or "").strip().lower()
    if "breakout" in st or st in ("orb2", "orb2_breakout"):
        return "breakout_0dte"
    if "trendline" in st:
        return "trendline_0dte"
    return "orb_0dte"


def list_trendline_open_positions(system: Any) -> list:
    """Open Trendline 0DTE rows from the unified options executor registry."""
    return [
        pos
        for pos in _open_positions_from_executor(system)
        if classify_0dte_path(pos) == "trendline_0dte"
    ]


def list_orb_open_positions(system: Any) -> list:
    """Open ORB1 0DTE rows (excludes Trendline and ORB2/Breakout)."""
    return [
        pos
        for pos in _open_positions_from_executor(system)
        if classify_0dte_path(pos) == "orb_0dte"
    ]


def list_breakout_open_positions(system: Any) -> list:
    """Open ORB2 Breakout 0DTE rows from the unified options executor registry."""
    return [
        pos
        for pos in _open_positions_from_executor(system)
        if classify_0dte_path(pos) == "breakout_0dte"
    ]


def get_unified_position(system: Any, position_id: str) -> Any:
    executor = get_options_executor(system)
    if not executor or not position_id:
        return None
    try:
        return executor.get_position(str(position_id))
    except Exception:
        return None


def trendline_position_entry_cost(pos: Any) -> float:
    ec = float(getattr(pos, "entry_cost", 0.0) or 0.0)
    if ec > 0:
        return ec
    md = getattr(pos, "metadata", None)
    if isinstance(md, dict):
        ep = md.get("execution_payload")
        if isinstance(ep, dict):
            etc = float(ep.get("estimated_total_cost", 0.0) or 0.0)
            if etc > 0:
                return etc
    ep = float(getattr(pos, "entry_price", 0.0) or 0.0)
    qty = max(1, int(getattr(pos, "quantity", 1) or 1))
    return ep * qty * 100.0


def trendline_position_option_side(pos: Any) -> str:
    side = str(getattr(pos, "option_side", "") or "").strip().lower()
    if side:
        return side
    md = getattr(pos, "metadata", None)
    if isinstance(md, dict):
        norm = md.get("normalized_options")
        if isinstance(norm, dict):
            legs = norm.get("legs")
            if isinstance(legs, list) and legs and isinstance(legs[0], dict):
                return str(legs[0].get("option_side") or "").strip().lower()
        snap = md.get("signal_snapshot")
        if isinstance(snap, dict):
            return str(snap.get("option_side") or "").strip().lower()
        lotto = getattr(pos, "lotto_contract", None)
        if isinstance(lotto, dict):
            return str(lotto.get("option_type") or "").strip().lower()
    return ""


def normalize_trendline_position_attrs(pos: Any) -> Any:
    """Attach Trendline monitor/EOD compat fields on unified OptionsPosition rows."""
    if pos is None:
        return pos
    if not getattr(pos, "entry_cost", None):
        try:
            pos.entry_cost = trendline_position_entry_cost(pos)
        except Exception:
            pass
    if not getattr(pos, "option_side", None):
        try:
            pos.option_side = trendline_position_option_side(pos)
        except Exception:
            pass
    return pos
