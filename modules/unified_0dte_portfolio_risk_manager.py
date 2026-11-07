#!/usr/bin/env python3
"""
Unified 0DTE portfolio risk — ORB1 Breakout + Trendline 0DTE account-level gates.

Path-specific sizing (ORB rank pack, Trendline sacred buffer) stays upstream.
This module owns combined caps, Red Day delegation, buying power, pending
reservations, and execution clamps (via dte0_execution_risk_manager).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

log = logging.getLogger(__name__)

PATH_ORB_0DTE = "orb_0dte"
PATH_TRENDLINE_0DTE = "trendline_0dte"
PATH_BREAKOUT_0DTE = "breakout_0dte"


def _cfg_bool(key: str, default: bool) -> bool:
    try:
        from modules.config_loader import get_config_value

        raw = get_config_value(key, "true" if default else "false", convert_type=False)
    except ImportError:
        import os

        raw = os.getenv(key, "true" if default else "false")
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _cfg_int(key: str, default: int) -> int:
    try:
        from modules.config_loader import get_config_value

        raw = get_config_value(key, default, convert_type=False)
    except ImportError:
        import os

        raw = os.getenv(key, str(default))
    try:
        return int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return default


def _cfg_float(key: str, default: float) -> float:
    try:
        from modules.config_loader import get_config_value

        raw = get_config_value(key, default, convert_type=False)
    except ImportError:
        import os

        raw = os.getenv(key, str(default))
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return default


@dataclass
class Unified0DTERiskConfig:
    enabled: bool = True
    fail_closed: bool = True
    log_audit: bool = True
    orb_max_positions: int = 10
    trendline_max_positions: int = 10
    breakout_max_positions: int = 10
    max_total_positions: int = 10
    demo_audit_only_clamps: bool = True
    # P4 hooks (not enforced unless wired)
    daily_loss_halt_pct: float = 0.0
    kill_switch_active: bool = False

    @classmethod
    def from_env(cls) -> "Unified0DTERiskConfig":
        from modules.unified_0dte_account import (
            breakout_0dte_max_positions,
            orb_0dte_max_positions,
            total_0dte_max_positions,
            trendline_0dte_max_positions,
        )

        return cls(
            enabled=_cfg_bool("UNIFIED_0DTE_RISK_ENABLED", True),
            fail_closed=_cfg_bool("UNIFIED_0DTE_FAIL_CLOSED", True),
            log_audit=_cfg_bool("UNIFIED_0DTE_LOG_AUDIT", True),
            orb_max_positions=orb_0dte_max_positions(),
            trendline_max_positions=trendline_0dte_max_positions(),
            breakout_max_positions=breakout_0dte_max_positions(),
            max_total_positions=total_0dte_max_positions(),
            demo_audit_only_clamps=not _cfg_bool("LIVE_SIZE_CAP_ENFORCE_IN_DEMO", False),
            daily_loss_halt_pct=_cfg_float("MAX_DAILY_LOSS_PCT", 0.0),
            kill_switch_active=_cfg_bool("UNIFIED_0DTE_KILL_SWITCH", False),
        )


@dataclass
class Unified0DTEOpenPositionView:
    position_id: str
    symbol: str
    strategy_type: str
    path: str
    entry_cost: float
    option_side: str = ""


@dataclass
class Unified0DTEAccountState:
    cash_available: float
    buying_power: float
    account_value: float
    premium_deployed: float
    pending_reserved: float
    orb_open_count: int
    trendline_open_count: int
    breakout_open_count: int
    combined_open_count: int
    day_realized_pnl_pct: float = 0.0
    refreshed_at: float = field(default_factory=time.time)
    account_state_valid: bool = True
    balance_source: str = ""


@dataclass
class Unified0DTERiskDecision:
    approved: bool
    approved_quantity: int
    approved_notional: float
    block_reason: Optional[str]
    risk_path: str
    audit: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Unified0DTEBrokerRoute:
    account_id: str
    broker_kind: str
    paths: Tuple[str, ...] = (PATH_ORB_0DTE, PATH_TRENDLINE_0DTE)


def _position_path(strategy_type: str) -> str:
    try:
        from modules.unified_0dte_account import classify_0dte_path

        return classify_0dte_path(strategy_type)
    except ImportError:
        st = str(strategy_type or "").strip().lower()
        if "breakout" in st:
            return PATH_BREAKOUT_0DTE
        if "trendline" in st:
            return PATH_TRENDLINE_0DTE
        return PATH_ORB_0DTE


def _position_entry_cost(pos: Any) -> float:
    ec = float(getattr(pos, "entry_cost", 0.0) or 0.0)
    if ec > 0:
        return ec
    ep = float(getattr(pos, "entry_price", 0.0) or 0.0)
    qty = max(1, int(getattr(pos, "quantity", 1) or 1))
    if ep > 0:
        return ep * qty * 100.0
    return 0.0


class Unified0DTEReservationGuard:
    """Single-owner reservation lifecycle for execute_lotto_sleeve."""

    def __init__(
        self,
        mgr: Optional["Unified0DTEPortfolioRiskManager"],
        reservation_id: str,
    ) -> None:
        self._mgr = mgr
        self._rid = str(reservation_id or "")
        self._released = False

    def release(self, reason: str) -> bool:
        if self._released or not self._mgr or not self._rid:
            return False
        released = self._mgr.release_pending_capital(self._rid, reason=reason)
        self._released = True
        return released


class Unified0DTEPortfolioRiskManager:
    """Broker-agnostic portfolio risk for combined 0DTE book."""

    def __init__(self, config: Optional[Unified0DTERiskConfig] = None) -> None:
        self.config = config or Unified0DTERiskConfig.from_env()
        self._pending_reservations: Dict[str, float] = {}
        self._pending_reservation_paths: Dict[str, str] = {}
        self._released_reservation_ids: Set[str] = set()
        self._last_state: Optional[Unified0DTEAccountState] = None
        self._tier1_cache: Optional[Set[str]] = None

    def reload_config(self) -> None:
        self.config = Unified0DTERiskConfig.from_env()

    def count_positions_by_path(self, system: Any) -> Tuple[int, int, int, int]:
        orb_n, tl_n, bo_n = 0, 0, 0
        for view in self._iter_open_positions(system):
            if view.path == PATH_TRENDLINE_0DTE:
                tl_n += 1
            elif view.path == PATH_BREAKOUT_0DTE:
                bo_n += 1
            else:
                orb_n += 1
        return orb_n, tl_n, bo_n, orb_n + tl_n + bo_n

    def _iter_open_positions(self, system: Any) -> List[Unified0DTEOpenPositionView]:
        from modules.unified_0dte_account import classify_0dte_path, get_options_executor

        executor = get_options_executor(system)
        if not executor:
            return []
        views: List[Unified0DTEOpenPositionView] = []
        try:
            positions = list(executor.get_open_positions() or [])
        except Exception:
            positions = []
        for pos in positions:
            st = str(getattr(pos, "strategy_type", "") or "")
            path = classify_0dte_path(pos)
            views.append(
                Unified0DTEOpenPositionView(
                    position_id=str(getattr(pos, "position_id", "") or ""),
                    symbol=str(getattr(pos, "symbol", "") or ""),
                    strategy_type=st,
                    path=path,
                    entry_cost=_position_entry_cost(pos),
                    option_side=str(getattr(pos, "option_side", "") or ""),
                )
            )
        return views

    def total_pending_reserved(self) -> float:
        return sum(max(0.0, float(v)) for v in self._pending_reservations.values())

    def has_reservation(self, reservation_id: str) -> bool:
        return str(reservation_id or "") in self._pending_reservations

    def _broker_balance_cache_max_age_sec(self) -> float:
        return max(0.0, _cfg_float("UNIFIED_0DTE_BROKER_BALANCE_CACHE_MAX_AGE_SEC", 120.0))

    def _live_cached_balance_valid(self, system: Any) -> Tuple[bool, float]:
        cached = getattr(system, "_unified_0dte_cash_balance", None)
        cached_at = float(getattr(system, "_unified_0dte_broker_balance_cached_at", 0.0) or 0.0)
        if cached is None or float(cached) <= 0:
            return False, 0.0
        age = time.time() - cached_at
        if cached_at <= 0 or age > self._broker_balance_cache_max_age_sec():
            return False, 0.0
        return True, float(cached)

    async def refresh_account_state(
        self,
        system: Any,
        *,
        broker_adapter: Any = None,
        demo_mode: Optional[bool] = None,
    ) -> Unified0DTEAccountState:
        from modules.unified_0dte_account import (
            refresh_unified_account_from_broker,
            sync_unified_account_balance,
            sync_unified_cash_available,
        )

        if demo_mode is None:
            try:
                from modules.runtime_mode import is_demo_mode

                demo_mode = is_demo_mode()
            except ImportError:
                demo_mode = True

        account_state_valid = True
        balance_source = "demo_ledger" if demo_mode else "unknown"
        cash = 0.0
        balance = 0.0
        buying_power = 0.0

        if demo_mode:
            try:
                await refresh_unified_account_from_broker(system)
            except Exception as exc:
                log.warning("UNIFIED_0DTE_RISK | refresh_broker_failed | error=%s", exc)
            cash = float(sync_unified_cash_available(system))
            balance = float(sync_unified_account_balance(system))
            buying_power = cash
        else:
            broker_ok = False
            if broker_adapter and getattr(broker_adapter, "is_available", lambda: False)():
                try:
                    bal_obj = await broker_adapter.get_account_balance()
                    if bal_obj is not None:
                        ca = float(getattr(bal_obj, "cash_available", 0.0) or 0.0)
                        bp = float(getattr(bal_obj, "buying_power", 0.0) or 0.0)
                        av = float(getattr(bal_obj, "account_value", 0.0) or 0.0)
                        cash_val = ca if ca > 0 else bp
                        if cash_val > 0:
                            cash = cash_val
                            balance = av if av > 0 else cash_val
                            buying_power = bp if bp > 0 else cash_val
                            broker_ok = True
                            balance_source = "broker_live"
                            system._unified_0dte_cash_balance = cash
                            system._unified_0dte_cash_available = cash
                            system._unified_0dte_broker_balance_cached_at = time.time()
                except Exception as exc:
                    log.warning("UNIFIED_0DTE_RISK | broker_balance_failed | error=%s", exc)

            if not broker_ok:
                cache_valid, cached_cash = self._live_cached_balance_valid(system)
                if cache_valid:
                    cash = cached_cash
                    balance = cached_cash
                    buying_power = cached_cash
                    balance_source = "broker_cache"
                elif self.config.fail_closed:
                    account_state_valid = False
                    balance_source = "unavailable"
                    cash = 0.0
                    balance = 0.0
                    buying_power = 0.0
                else:
                    try:
                        await refresh_unified_account_from_broker(system)
                    except Exception as exc:
                        log.warning("UNIFIED_0DTE_RISK | refresh_broker_failed | error=%s", exc)
                    cash = float(sync_unified_cash_available(system))
                    balance = float(sync_unified_account_balance(system))
                    buying_power = cash
                    balance_source = "legacy_fallback"

        views = self._iter_open_positions(system)
        premium_deployed = sum(v.entry_cost for v in views)
        pending = self.total_pending_reserved()
        orb_n, tl_n, bo_n, combined = self.count_positions_by_path(system)
        buying_power = max(0.0, buying_power - pending)

        state = Unified0DTEAccountState(
            cash_available=cash,
            buying_power=buying_power,
            account_value=max(balance, cash + premium_deployed),
            premium_deployed=premium_deployed,
            pending_reserved=pending,
            orb_open_count=orb_n,
            trendline_open_count=tl_n,
            breakout_open_count=bo_n,
            combined_open_count=combined,
            day_realized_pnl_pct=self._day_pnl_pct_hook(system),
            account_state_valid=account_state_valid,
            balance_source=balance_source,
        )
        self._last_state = state
        return state

    def _day_pnl_pct_hook(self, system: Any) -> float:
        """P4: daily loss halt hook — account-scoped day P&L % (demo ledger or live tracker)."""
        try:
            from modules.unified_0dte_account import get_unified_0dte_day_pnl_pct

            return float(get_unified_0dte_day_pnl_pct(system))
        except ImportError:
            return float(getattr(system, "_unified_0dte_day_pnl_pct", 0.0) or 0.0)

    def reserve_pending_capital(
        self,
        reservation_id: str,
        amount: float,
        path: Optional[str] = None,
    ) -> None:
        if not reservation_id:
            return
        rid = str(reservation_id)
        amt = max(0.0, float(amount or 0.0))
        path_norm = _position_path(path) if path else ""
        if amt <= 0.0 and not path_norm and rid not in self._pending_reservations:
            return
        self._pending_reservations[rid] = amt
        if path_norm:
            self._pending_reservation_paths[rid] = path_norm
        log.info(
            "UNIFIED_0DTE_RESERVATION_CREATED | id=%s | amount=%.2f | path=%s | pending_total=%.2f",
            rid,
            amt,
            path_norm,
            self.total_pending_reserved(),
        )

    def release_pending_capital(self, reservation_id: str, *, reason: str = "unspecified") -> bool:
        rid = str(reservation_id or "")
        if not rid:
            return False
        if rid in self._released_reservation_ids:
            log.warning(
                "UNIFIED_0DTE_RESERVATION_LEAK_DETECTED | id=%s | reason=double_release | release_reason=%s",
                rid,
                reason,
            )
            return False
        amount = self._pending_reservations.pop(rid, None)
        self._pending_reservation_paths.pop(rid, None)
        if amount is None:
            return False
        amount = max(0.0, float(amount))
        self._released_reservation_ids.add(rid)
        log.info(
            "UNIFIED_0DTE_RESERVATION_RELEASED | id=%s | amount=%.2f | reason=%s | pending_total=%.2f",
            rid,
            amount,
            reason,
            self.total_pending_reserved(),
        )
        return True

    def _reject(
        self,
        *,
        risk_path: str,
        reason: str,
        requested_qty: int,
        audit_extra: Optional[Dict[str, Any]] = None,
    ) -> Unified0DTERiskDecision:
        audit = {"requested_quantity": requested_qty, "risk_path": risk_path}
        if audit_extra:
            audit.update(audit_extra)
        decision = Unified0DTERiskDecision(
            approved=False,
            approved_quantity=0,
            approved_notional=0.0,
            block_reason=reason,
            risk_path=risk_path,
            audit=audit,
        )
        self.audit_decision(decision)
        return decision

    def _approve(
        self,
        *,
        risk_path: str,
        qty: int,
        notional: float,
        audit_extra: Optional[Dict[str, Any]] = None,
    ) -> Unified0DTERiskDecision:
        audit = {
            "approved_quantity": qty,
            "approved_notional": notional,
            "risk_path": risk_path,
        }
        if audit_extra:
            audit.update(audit_extra)
        decision = Unified0DTERiskDecision(
            approved=True,
            approved_quantity=qty,
            approved_notional=notional,
            block_reason=None,
            risk_path=risk_path,
            audit=audit,
        )
        self.audit_decision(decision)
        return decision

    def audit_decision(self, decision: Unified0DTERiskDecision) -> None:
        if not self.config.log_audit:
            return
        a = decision.audit or {}
        log.info(
            "UNIFIED_0DTE_RISK_AUDIT | approved=%s | risk_path=%s | symbol=%s | "
            "path=%s | requested_qty=%s | approved_qty=%d | notional=%.2f | "
            "combined_open=%s | orb_open=%s | trendline_open=%s | breakout_open=%s | reason=%s",
            str(decision.approved).lower(),
            decision.risk_path,
            a.get("symbol", ""),
            a.get("path", ""),
            a.get("requested_quantity", a.get("approved_quantity", "")),
            decision.approved_quantity,
            decision.approved_notional,
            a.get("combined_open_count", ""),
            a.get("orb_open_count", ""),
            a.get("trendline_open_count", ""),
            a.get("breakout_open_count", ""),
            decision.block_reason or "",
        )

    def _check_p4_hooks(self, state: Unified0DTEAccountState) -> Optional[str]:
        if self.config.kill_switch_active:
            return "kill_switch_active"
        if self.config.daily_loss_halt_pct > 0:
            if state.day_realized_pnl_pct <= -abs(self.config.daily_loss_halt_pct):
                log.warning(
                    "UNIFIED_0DTE_RISK | action=daily_loss_halt | day_realized_pnl_pct=%.4f | "
                    "halt_threshold_pct=%.4f",
                    state.day_realized_pnl_pct,
                    self.config.daily_loss_halt_pct,
                )
                return "daily_loss_halt"
        return None

    def _pending_path_counts(self) -> Tuple[int, int, int, int]:
        """In-flight approvals that are not open positions yet. Each holds one slot."""
        orb_n = tl_n = bo_n = other = 0
        for path in self._pending_reservation_paths.values():
            if path == PATH_TRENDLINE_0DTE:
                tl_n += 1
            elif path == PATH_BREAKOUT_0DTE:
                bo_n += 1
            elif path == PATH_ORB_0DTE:
                orb_n += 1
            else:
                other += 1
        return orb_n, tl_n, bo_n, orb_n + tl_n + bo_n + other

    def _spendable_cash(self, state: Unified0DTEAccountState) -> float:
        """Cash that can back a new debit after the sacred buffer and earlier reservations.

        ``buying_power`` is already net of pending dollar reservations. The buffer is
        ``(100 - 0DTE_TRADING_CAPITAL_PCT)`` of equity, so a 90% allocation keeps 10%.
        """
        try:
            from modules.unified_0dte_account import unified_trading_capital_pct

            alloc = float(unified_trading_capital_pct())
        except Exception:
            alloc = 90.0
        buf_pct = max(0.0, min(100.0, 100.0 - alloc)) / 100.0
        equity = max(
            float(state.account_value or 0.0),
            float(state.cash_available or 0.0) + float(state.premium_deployed or 0.0),
            float(state.buying_power or 0.0)
            + float(state.pending_reserved or 0.0)
            + float(state.premium_deployed or 0.0),
        )
        return max(0.0, float(state.buying_power or 0.0) - equity * buf_pct)

    def _check_path_caps(
        self,
        path: str,
        state: Unified0DTEAccountState,
    ) -> Optional[str]:
        cfg = self.config
        pend_orb, pend_tl, pend_bo, pend_combined = self._pending_path_counts()
        if state.combined_open_count + pend_combined >= cfg.max_total_positions:
            return "combined_cap_reached"
        if path == PATH_ORB_0DTE and state.orb_open_count + pend_orb >= cfg.orb_max_positions:
            return "orb_cap_reached"
        if path == PATH_TRENDLINE_0DTE and state.trendline_open_count + pend_tl >= cfg.trendline_max_positions:
            return "trendline_cap_reached"
        if path == PATH_BREAKOUT_0DTE and state.breakout_open_count + pend_bo >= cfg.breakout_max_positions:
            return "breakout_cap_reached"
        return None

    def precheck_open_capacity(self, system: Any, path: str) -> Optional[Dict[str, Any]]:
        """Cheap (no API) open-count cap check before chain fetch / dir% (Oct 13 2026).

        Same counts as ``_check_path_caps``: in-process open positions plus in-flight
        reservations. Returns None when a slot is free, else a reason dict with
        ``reason=max_open_positions_reached``, ``cap_kind``, ``open_n`` and ``cap``.
        """
        path_norm = _position_path(path)
        cfg = self.config
        orb_n, tl_n, bo_n, total_n = self.count_positions_by_path(system)
        pend_orb, pend_tl, pend_bo, pend_total = self._pending_path_counts()
        checks = [("combined", total_n + pend_total, int(cfg.max_total_positions))]
        if path_norm == PATH_ORB_0DTE:
            checks.append(("orb", orb_n + pend_orb, int(cfg.orb_max_positions)))
        elif path_norm == PATH_TRENDLINE_0DTE:
            checks.append(("trendline", tl_n + pend_tl, int(cfg.trendline_max_positions)))
        elif path_norm == PATH_BREAKOUT_0DTE:
            checks.append(("breakout", bo_n + pend_bo, int(cfg.breakout_max_positions)))
        for kind, open_n, cap in checks:
            if cap > 0 and open_n >= cap:
                return {
                    "reason": MAX_OPEN_POSITIONS_REASON,
                    "cap_kind": kind,
                    "open_n": int(open_n),
                    "cap": int(cap),
                    "path": path_norm,
                }
        return None

    def _check_red_day(
        self,
        *,
        symbol: str,
        option_side: str,
        red_day_active: bool,
    ) -> Optional[str]:
        if not red_day_active:
            return None
        try:
            from modules.portfolio_protection import (
                load_0dte_tier1_symbols,
                passes_red_day_0dte_style_gate,
            )
            from modules.config_loader import get_config_value
        except ImportError:
            return None

        if self._tier1_cache is None:
            self._tier1_cache = load_0dte_tier1_symbols()
        allowed, reason = passes_red_day_0dte_style_gate(
            symbol=symbol,
            option_side_or_type=option_side,
            tier1_symbols=self._tier1_cache,
            red_day_active=True,
            allow_tier1_call=bool(
                get_config_value("TRENDLINE_RED_DAY_ALLOW_TIER1_CALL", True)
            ),
            allow_put=bool(get_config_value("TRENDLINE_RED_DAY_ALLOW_PUT", True)),
        )
        if not allowed:
            return reason or "red_day_tier_gate"
        return None

    async def evaluate_entry(
        self,
        *,
        system: Any,
        path: str,
        symbol: str,
        option_side: str,
        quantity: int,
        contract: Any,
        demo_mode: bool,
        broker_adapter: Any = None,
        red_day_active: bool = False,
        proposed_notional: Optional[float] = None,
        reservation_id: Optional[str] = None,
        max_spread_pct: Optional[float] = None,
        warm_chain: Any = None,
        warm_chain_age_sec: Optional[float] = None,
        options_chain_manager: Any = None,
    ) -> Unified0DTERiskDecision:
        requested_qty = max(0, int(quantity or 0))
        path_norm = _position_path(path)
        sym = str(symbol or getattr(contract, "symbol", "") or "")

        if not self.config.enabled:
            ask = float(getattr(contract, "ask", 0.0) or 0.0)
            prem = ask if ask > 0 else float(getattr(contract, "mid_price", 0.0) or 0.0)
            notional = prem * requested_qty * 100.0
            return self._approve(
                risk_path="disabled_pass_through",
                qty=requested_qty,
                notional=notional,
                audit_extra={"symbol": sym, "path": path_norm, "requested_quantity": requested_qty},
            )

        state = await self.refresh_account_state(
            system, broker_adapter=broker_adapter, demo_mode=demo_mode
        )
        audit_base = {
            "symbol": sym,
            "path": path_norm,
            "requested_quantity": requested_qty,
            "combined_open_count": state.combined_open_count,
            "orb_open_count": state.orb_open_count,
            "trendline_open_count": state.trendline_open_count,
            "breakout_open_count": state.breakout_open_count,
            "buying_power": state.buying_power,
            "pending_reserved": state.pending_reserved,
            "demo_mode": demo_mode,
            "balance_source": state.balance_source,
            "account_state_valid": state.account_state_valid,
        }

        if (
            not demo_mode
            and self.config.fail_closed
            and not state.account_state_valid
        ):
            return self._reject(
                risk_path="account_state_unavailable",
                reason="account_state_unavailable",
                requested_qty=requested_qty,
                audit_extra=audit_base,
            )

        p4_reason = self._check_p4_hooks(state)
        if p4_reason:
            return self._reject(
                risk_path="p4_halt",
                reason=p4_reason,
                requested_qty=requested_qty,
                audit_extra=audit_base,
            )

        if not demo_mode and self.config.fail_closed:
            if broker_adapter is None or not getattr(
                broker_adapter, "is_available", lambda: False
            )():
                return self._reject(
                    risk_path="broker_fail_closed",
                    reason="broker_adapter_unavailable",
                    requested_qty=requested_qty,
                    audit_extra=audit_base,
                )

        cap_reason = self._check_path_caps(path_norm, state)
        if cap_reason:
            audit_base = dict(audit_base, cap_reason=cap_reason)
            return self._reject(
                risk_path="portfolio_cap",
                reason=cap_reason,
                requested_qty=requested_qty,
                audit_extra=audit_base,
            )

        ask0 = float(getattr(contract, "ask", 0.0) or 0.0)
        prem0 = ask0 if ask0 > 0 else float(getattr(contract, "mid_price", 0.0) or 0.0)
        provisional = prem0 * requested_qty * 100.0
        if proposed_notional and proposed_notional > 0:
            provisional = max(provisional, float(proposed_notional))
        enforce_clamps = (not demo_mode) or not self.config.demo_audit_only_clamps
        if enforce_clamps and provisional > self._spendable_cash(state) + 0.01:
            return self._reject(
                risk_path="insufficient_buying_power",
                reason="insufficient_buying_power",
                requested_qty=requested_qty,
                audit_extra=audit_base,
            )

        keep_reservation = False
        if reservation_id:
            self.reserve_pending_capital(reservation_id, provisional, path=path_norm)
        try:
            decision = await self._finish_reserved_entry(
                state=state,
                audit_base=audit_base,
                system=system,
                path_norm=path_norm,
                sym=sym,
                option_side=option_side,
                requested_qty=requested_qty,
                contract=contract,
                demo_mode=demo_mode,
                broker_adapter=broker_adapter,
                red_day_active=red_day_active,
                proposed_notional=proposed_notional,
                reservation_id=reservation_id,
                max_spread_pct=max_spread_pct,
                warm_chain=warm_chain,
                warm_chain_age_sec=warm_chain_age_sec,
                options_chain_manager=options_chain_manager,
                enforce_clamps=enforce_clamps,
            )
            keep_reservation = bool(decision.approved)
            return decision
        finally:
            if (
                reservation_id
                and not keep_reservation
                and self.has_reservation(reservation_id)
            ):
                self.release_pending_capital(reservation_id, reason="entry_rejected")

    async def _finish_reserved_entry(
        self,
        *,
        state: Unified0DTEAccountState,
        audit_base: Dict[str, Any],
        system: Any,
        path_norm: str,
        sym: str,
        option_side: str,
        requested_qty: int,
        contract: Any,
        demo_mode: bool,
        broker_adapter: Any,
        red_day_active: bool,
        proposed_notional: Optional[float],
        reservation_id: Optional[str],
        max_spread_pct: Optional[float],
        warm_chain: Any,
        warm_chain_age_sec: Optional[float],
        options_chain_manager: Any,
        enforce_clamps: bool,
    ) -> Unified0DTERiskDecision:
        red_reason = self._check_red_day(
            symbol=sym, option_side=option_side, red_day_active=red_day_active
        )
        if red_reason:
            return self._reject(
                risk_path="red_day_tier_gate",
                reason=red_reason,
                requested_qty=requested_qty,
                audit_extra=audit_base,
            )

        from modules.dte0_execution_risk_manager import (
            apply_live_execution_risk_limits,
            refresh_contract_quotes_if_enabled,
        )
        from modules.broker_adapter import BrokerAccountBalance

        chain_mgr = options_chain_manager
        if chain_mgr is None:
            chain_mgr = getattr(system, "options_chain_manager", None)

        contract_for_exec = contract
        if broker_adapter and getattr(broker_adapter, "is_available", lambda: False)():
            contract_for_exec = await refresh_contract_quotes_if_enabled(
                contract,
                broker_adapter,
                warm_chain=warm_chain if isinstance(warm_chain, dict) else None,
                warm_chain_age_sec=warm_chain_age_sec,
                options_chain_manager=chain_mgr,
            )
        elif warm_chain is not None or chain_mgr is not None:
            contract_for_exec = await refresh_contract_quotes_if_enabled(
                contract,
                None,
                warm_chain=warm_chain if isinstance(warm_chain, dict) else None,
                warm_chain_age_sec=warm_chain_age_sec,
                options_chain_manager=chain_mgr,
            )

        account_balance = None
        if broker_adapter and getattr(broker_adapter, "is_available", lambda: False)():
            try:
                bal_obj = await broker_adapter.get_account_balance()
                if bal_obj is not None:
                    account_balance = bal_obj
            except Exception:
                account_balance = None
        if account_balance is None:
            account_balance = BrokerAccountBalance(
                cash_available=state.buying_power,
                account_value=state.account_value,
                buying_power=state.buying_power,
            )

        exec_result = apply_live_execution_risk_limits(
            quantity=requested_qty,
            contract=contract_for_exec,
            strategy_type=path_norm,
            demo_mode=bool(demo_mode),
            broker_adapter=broker_adapter,
            account_balance=account_balance,
            max_spread_pct=max_spread_pct,
        )
        audit_base["execution_risk"] = exec_result.audit_data

        exec_audit = exec_result.audit_data if isinstance(exec_result.audit_data, dict) else {}
        market_quality_failed = exec_audit.get("market_quality_passed") is False

        if exec_result.blocked and (market_quality_failed or enforce_clamps):
            return self._reject(
                risk_path="execution_clamp",
                reason=exec_result.block_reason or "execution_risk_blocked",
                requested_qty=requested_qty,
                audit_extra=audit_base,
            )

        approved_qty = int(exec_result.approved_quantity)
        if approved_qty < 1 and enforce_clamps:
            return self._reject(
                risk_path="execution_clamp",
                reason="quantity_below_minimum",
                requested_qty=requested_qty,
                audit_extra=audit_base,
            )
        ask = float(getattr(contract_for_exec, "ask", 0.0) or 0.0)
        prem = ask if ask > 0 else float(
            getattr(contract_for_exec, "mid_price", 0.0) or 0.0
        )
        notional = prem * approved_qty * 100.0

        if proposed_notional and proposed_notional > 0:
            notional = float(proposed_notional)

        if enforce_clamps and notional > self._spendable_cash(state) + 0.01:
            return self._reject(
                risk_path="insufficient_buying_power",
                reason="insufficient_buying_power",
                requested_qty=requested_qty,
                audit_extra=audit_base,
            )

        if reservation_id:
            self._pending_reservations[str(reservation_id)] = float(notional)

        return self._approve(
            risk_path="approved",
            qty=approved_qty,
            notional=notional,
            audit_extra=audit_base,
        )

    async def evaluate_orb_entry(self, **kwargs: Any) -> Unified0DTERiskDecision:
        kwargs["path"] = PATH_ORB_0DTE
        return await self.evaluate_entry(**kwargs)

    async def evaluate_trendline_entry(self, **kwargs: Any) -> Unified0DTERiskDecision:
        kwargs["path"] = PATH_TRENDLINE_0DTE
        return await self.evaluate_entry(**kwargs)

    async def evaluate_breakout_entry(self, **kwargs: Any) -> Unified0DTERiskDecision:
        kwargs["path"] = PATH_BREAKOUT_0DTE
        return await self.evaluate_entry(**kwargs)


_manager_singleton: Optional[Unified0DTEPortfolioRiskManager] = None

MAX_OPEN_POSITIONS_REASON = "max_open_positions_reached"


def max_open_positions_precheck(system: Any, path: str) -> Optional[Dict[str, Any]]:
    """Gate-flow cap check (TL / BO / ORB) before chain fetch and dir%.

    Flag ``MAX_OPEN_POSITIONS_PRECHECK_ENABLED`` (true). Only when the unified
    risk manager is on (it owns the same cap at execution).
    """
    if system is None:
        return None
    if not _cfg_bool("MAX_OPEN_POSITIONS_PRECHECK_ENABLED", True):
        return None
    if not _cfg_bool("UNIFIED_0DTE_RISK_ENABLED", True):
        return None
    try:
        return get_unified_0dte_portfolio_risk_manager(system).precheck_open_capacity(system, path)
    except Exception as exc:  # never block a signal on a precheck error
        log.debug("MAX_OPEN_POSITIONS_PRECHECK | error=%s", exc)
        return None


def format_max_open_positions(info: Dict[str, Any]) -> str:
    return (
        f"{MAX_OPEN_POSITIONS_REASON}:{info.get('cap_kind')}_open_n={info.get('open_n')}"
        f"_cap={info.get('cap')}"
    )


def get_unified_0dte_portfolio_risk_manager(
    system: Any = None,
) -> Unified0DTEPortfolioRiskManager:
    global _manager_singleton
    if system is not None:
        mgr = getattr(system, "_unified_0dte_portfolio_risk_manager", None)
        if mgr is not None:
            return mgr
    if _manager_singleton is None:
        _manager_singleton = Unified0DTEPortfolioRiskManager()
    if system is not None:
        system._unified_0dte_portfolio_risk_manager = _manager_singleton
    return _manager_singleton
