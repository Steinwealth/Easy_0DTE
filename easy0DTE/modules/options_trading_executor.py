#!/usr/bin/env python3
"""
Options Trading Executor
========================

Executes options trades (debit spreads and lotto sleeve) for Easy 0DTE.
Handles order execution, position management, and profit-taking.

**Two production paths (ORB1 Breakout + Trendline 0DTE):**

1. **DEMO** (``MODE=demo``): Live broker **market data** (chains/quotes) + **simulated ledger**
   only (``MockOptionsExecutor`` / GCS ``mock_options_history.json``). No broker order submit.

2. **LIVE** (``MODE=live``): Broker open + broker close + ``live_options_ledger`` end-to-end.
"""

import asyncio
import logging
import os
import time
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timezone
from dataclasses import dataclass, field

from .options_chain_manager import DebitSpread, CreditSpread, OptionContract
from .options_exit_manager import OptionsExitManager, ExitReason, ExitSignal
from .options_types import OptionsPosition
from .options_execution_normalize import (
    build_normalized_metadata_credit_spread,
    build_normalized_metadata_debit_spread,
    build_normalized_metadata_single_leg,
    log_metadata_normalized,
    log_position_type_normalized,
)
from .options_trade_id import build_options_position_id

try:
    from modules.config_loader import get_config_value
except ImportError:  # pragma: no cover
    def get_config_value(key: str, default: Any = None) -> Any:  # type: ignore
        return default

try:
    from modules.execution_routing import (
        smart_execution_enabled,
        resolve_options_exit_plan,
        last_look_option_spread_ok,
        last_look_max_spread_pct_default,
    )
    from modules.execution_telemetry import (
        attach_orb_entry_execution_telemetry,
        build_execution_payload,
        entry_slippage_vs_mid_pct,
        log_execution_event,
    )
except ImportError:
    def smart_execution_enabled() -> bool:  # type: ignore
        return False

    def resolve_options_exit_plan(*args, **kwargs):  # type: ignore
        class _P:
            order_type = "MARKET"
            allow_market_fallback = True
            exit_urgency = "URGENT"
            exit_execution_style = "MARKET"

        return _P()

    def last_look_option_spread_ok(*args):  # type: ignore
        return True, 0.0

    def last_look_max_spread_pct_default() -> float:  # type: ignore
        return 2.5

    log_execution_event = None  # type: ignore
    build_execution_payload = None  # type: ignore
    attach_orb_entry_execution_telemetry = None  # type: ignore
    entry_slippage_vs_mid_pct = None  # type: ignore

# Import ETrade Options API
try:
    from .etrade_options_api import ETradeOptionsAPI

    ETRADE_OPTIONS_AVAILABLE = True
except ImportError:
    ETRADE_OPTIONS_AVAILABLE = False
    logging.warning("ETrade Options API not available")

log = logging.getLogger(__name__)

try:
    from modules.runtime_mode import (
        broker_execution_enabled,
        is_demo_mode as _runtime_is_demo,
        resolve_runtime_mode,
    )
except ImportError:
    resolve_runtime_mode = None  # type: ignore

    def _runtime_is_demo() -> bool:  # type: ignore
        return os.getenv("MODE", "demo").lower() != "live"

    def broker_execution_enabled() -> bool:  # type: ignore
        return os.getenv("MODE", "demo").lower() == "live"

try:
    from modules.broker_adapter import BrokerAdapter, BrokerCloseRequest, OptionOrderRequest
except ImportError:
    BrokerAdapter = None  # type: ignore
    BrokerCloseRequest = None  # type: ignore
    OptionOrderRequest = None  # type: ignore


def _live_fail_closed(
    reason: str, *, path: str, symbol: str = "", level: str = "error"
) -> None:
    """Log and reject Live execution — never create phantom positions.

    Lotto-sleeve policy blocks (slot cap, liquidity) are INFO. Broker/close
    fail-closed stays ERROR.
    """
    emit = log.info if str(level or "").lower() == "info" else log.error
    emit(
        "LIVE_EXECUTION_BLOCKED | path=%s | symbol=%s | reason=%s",
        path,
        symbol or "-",
        reason,
    )


def _lotto_sleeve_block_reason(reason: str) -> str:
    return str(reason or "execution_blocked").strip() or "execution_blocked"


def _live_spreads_guard(*, path: str, symbol: str = "") -> bool:
    """Return True when Live spread execution is allowed."""
    try:
        from modules.runtime_mode import live_spreads_enabled
    except ImportError:
        return os.getenv("LIVE_SPREADS_ENABLED", "false").lower() == "true"
    if live_spreads_enabled():
        return True
    _live_fail_closed("live_spreads_not_enabled", path=path, symbol=symbol, level="info")
    return False


def _execution_context_for_entry(
    *,
    confidence: float = 0.0,
    breakout_score: float = 0.0,
    seconds_from_signal: float = 45.0,
    continuation_distance: float = 0.0,
    velocity: float = 0.0,
) -> Dict[str, Any]:
    """Lightweight momentum context for opening aggression (no signal-engine changes)."""
    return {
        "confidence": float(confidence or 0.0),
        "breakout_score": float(breakout_score or 0.0),
        "seconds_from_signal": float(seconds_from_signal),
        "continuation_distance": float(continuation_distance or 0.0),
        "velocity": float(velocity or 0.0),
        "high_confidence_impulse": float(confidence or 0.0) >= 0.85,
    }


def _infer_nonzero_option_per_share_mark(position: Any) -> float:
    """Deprecated — historical-mark EOD settlement is prohibited.

    Kept as a no-op stub so accidental callers cannot invent a nonzero close.
    """
    return 0.0


class OptionsTradingExecutor:
    """
    Options Trading Executor for Easy 0DTE
    
    Handles:
    - Debit spread execution (Demo/Live)
    - Credit spread execution (Demo/Live)
    - Lotto sleeve execution (Demo/Live)
    - Position management
    - Auto-partial profits
    - Systematic runners
    
    Supports both Demo Mode (mock execution) and Live Mode (broker API).
    """
    
    def __init__(
        self,
        auto_partial_enabled: bool = False,
        partial_profit_pct: float = 0.50,  # Legacy: Take 50% profit at target
        runner_profit_pct: float = 2.0,  # Legacy: Let 50% run to 2x
        max_position_cost: float = 999999.0,  # Disabled - using only percentage-based limit (35%)
        max_position_size_pct: float = 0.35,  # Max position size as % of account equity (35% - matches ORB Strategy)
        demo_mode: Optional[bool] = None,  # None = auto-detect from env, True/False = explicit
        mock_executor=None,  # Mock executor for Demo mode
        alert_manager=None,  # Alert manager for notifications
        priority_collector=None  # Priority Data Collector for trade history
    ):
        """
        Initialize Options Trading Executor
        
        Args:
            auto_partial_enabled: Enable auto-partial profits
            partial_profit_pct: Percentage to take profit at (e.g., 0.50 = 50%)
            runner_profit_pct: Profit percentage for runners (e.g., 2.0 = 2x)
            max_position_cost: Maximum cost per position
        """
        self.auto_partial_enabled = auto_partial_enabled
        self.partial_profit_pct = partial_profit_pct  # Legacy, kept for compatibility
        self.runner_profit_pct = runner_profit_pct  # Legacy, kept for compatibility
        self.max_position_cost = max_position_cost
        self.max_position_size_pct = max_position_size_pct  # 35% max of account equity (matches ORB Strategy)
        
        # Detect mode from canonical MODE resolver when not explicitly set
        if demo_mode is None:
            demo_mode = _runtime_is_demo()

        self.demo_mode = demo_mode
        self.mock_executor = mock_executor
        self.alert_manager = alert_manager
        self.priority_collector = priority_collector  # Priority Data Collector
        # Optional callback(latency_ms: float, source: str) for external latency controllers.
        self.chain_latency_callback = None
        self.broker_adapter: Optional[Any] = None
        self.trading_system: Optional[Any] = None
        # Live sole-writer gate: only BrokerPositionSynchronizer may set this True.
        self._bps_write_token: bool = False

        # ETrade Options API for Live Mode — set via set_etrade_options_api() / set_broker_adapter()
        # from main.py (avoid blocking network init at import/instantiate time).
        self.etrade_options_api = None
        self._last_execution_block_reason: Optional[str] = None
        self._last_execution_block_diagnostics: Dict[str, Any] = {}

        # Position tracking
        self.positions: Dict[str, OptionsPosition] = {}
        
        # Emergency-only exit manager (stealth owns primary exits)
        self.exit_manager = OptionsExitManager(
            debit_spread_fail_safe_pct=float(os.getenv("0DTE_DEBIT_FAIL_SAFE_PCT", "-0.60")),
            credit_spread_fail_safe_pct=float(os.getenv("0DTE_CREDIT_FAIL_SAFE_PCT", "-0.60")),
            lotto_fail_safe_pct=float(os.getenv("0DTE_LOTTO_FAIL_SAFE_PCT", "-0.60")),
        )

        mode_label = "🎮 DEMO MODE" if demo_mode else "💰 LIVE MODE"
        log.info(f"Options Trading Executor initialized ({mode_label}):")
        log.info(f"  - Auto-partial enabled: {auto_partial_enabled} (partial ladder removed; take_profit is stealth-owned)")
        log.info(f"  - Max position cost: ${max_position_cost:.2f}")
        log.info(f"  - Max position size: {max_position_size_pct*100:.0f}% of account equity")
        log.info(f"  - Emergency Exit Manager: fail_safe + health_emergency only")
        if demo_mode:
            if broker_execution_enabled():
                log.info("  - Fill path: broker preview+place + fill_confirmed (E*TRADE sim)")
                log.info("  - Close path: LIMIT → MARKET retries (LIVE_CLOSE_MAX_ATTEMPTS)")
            elif mock_executor is not None:
                log.info("  - Fill path: MockOptionsExecutor (BTO @ ask / STC @ bid)")
            else:
                log.info("  - Fill path: pending broker adapter wiring (mock disabled)")
        else:
            log.info("  - ETrade API: set via set_etrade_options_api() / broker adapter")

    @staticmethod
    def _demo_available_balance(mock_executor) -> float:
        if mock_executor is None:
            return 0.0
        fn = getattr(mock_executor, "available_balance", None)
        if callable(fn):
            return float(fn())
        return float(getattr(mock_executor, "account_balance", 0.0) or 0.0)

    def _runtime_demo_mode(self) -> bool:
        """Canonical demo gate — instance flag plus runtime MODE resolver."""
        if self.demo_mode:
            return True
        try:
            if resolve_runtime_mode is not None:
                return bool(resolve_runtime_mode(force_refresh=True).is_demo)
            return bool(_runtime_is_demo())
        except Exception:
            return False

    def _use_broker_fills(self) -> bool:
        """Broker preview+place with fill_confirmed — LIVE mode only."""
        if self._runtime_demo_mode():
            return False
        try:
            if resolve_runtime_mode is not None:
                if not resolve_runtime_mode(force_refresh=True).is_live:
                    return False
            elif not broker_execution_enabled():
                return False
        except Exception:
            if not broker_execution_enabled():
                return False
        adapter = self.broker_adapter
        return bool(adapter and getattr(adapter, "is_available", lambda: False)())

    def _use_mock_fills(self) -> bool:
        """Simulated ledger entry+exit (``MODE=demo``)."""
        return bool(self._runtime_demo_mode() and self.mock_executor)

    def _should_use_mock_close(self, position_id: str, position: Optional["OptionsPosition"] = None) -> bool:
        """DEMO mode always settles on simulated ledger — never broker STC."""
        return self._runtime_demo_mode()

    def _ensure_lotto_contract_for_close(self, position: "OptionsPosition") -> bool:
        """Rebuild lotto_contract from normalized metadata when missing (post-restart / rehydrate)."""
        if getattr(position, "lotto_contract", None) is not None:
            return True
        if str(getattr(position, "position_type", "") or "").lower() != "lotto":
            return bool(position.debit_spread or position.credit_spread)
        meta = position.metadata if isinstance(getattr(position, "metadata", None), dict) else {}
        norm = meta.get("normalized_options")
        if not isinstance(norm, dict):
            return False
        legs = norm.get("legs") or []
        if not legs or not isinstance(legs[0], dict):
            return False
        leg = legs[0]
        try:
            from easy0DTE.modules.options_chain_manager import OptionContract
        except ImportError:
            from options_chain_manager import OptionContract  # type: ignore
        exp_raw = str(norm.get("expiration_ymd") or leg.get("expiry") or "")
        if len(exp_raw) == 8 and exp_raw.isdigit():
            expiry_fmt = f"{exp_raw[:4]}-{exp_raw[4:6]}-{exp_raw[6:8]}"
        else:
            expiry_fmt = exp_raw
        prem = float(leg.get("entry_price") or position.entry_price or 0.0)
        position.lotto_contract = OptionContract(
            symbol=str(norm.get("underlying_symbol") or position.symbol or ""),
            strike=float(leg.get("strike") or 0.0),
            expiry=expiry_fmt,
            option_type=str(leg.get("option_side") or "put").lower(),
            bid=prem,
            ask=prem,
            last=prem,
            volume=0,
            open_interest=0,
            delta=float(leg.get("delta_at_entry") or 0.0),
            gamma=0.0,
            theta=0.0,
            vega=0.0,
            implied_volatility=0.0,
        )
        return True

    async def _demo_ledger_close_external(
        self,
        position: "OptionsPosition",
        exit_signal: "ExitSignal",
        *,
        exit_reason: str,
    ) -> Optional["OptionsPosition"]:
        """
        Close a demo position missing from mock active_positions (registry drift / cold-start merge).
        """
        position_id = str(position.position_id)
        exit_px = float(exit_signal.exit_price)
        qty = int(max(1, int(position.quantity or 1)))
        entry_px = float(position.entry_price or 0.0)
        structural = str(
            getattr(position, "normalized_position_type", "") or position.position_type or ""
        ).strip().lower()
        if structural == "credit_spread" or position.position_type == "credit_spread":
            pnl_dollar = (entry_px - exit_px) * qty * 100.0
        else:
            pnl_dollar = (exit_px - entry_px) * qty * 100.0

        position.status = "closed"
        position.realized_pnl = float(pnl_dollar)
        position.unrealized_pnl = 0.0
        position.current_value = exit_px
        meta_raw = getattr(position, "metadata", None)
        meta = dict(meta_raw) if isinstance(meta_raw, dict) else {}
        try:
            from modules.options_trade_economics import finalize_close_economics

            lotto = getattr(position, "lotto_contract", None)
            lotto_d = lotto if isinstance(lotto, dict) else (lotto.to_dict() if hasattr(lotto, "to_dict") else {})
            _, net_pnl = finalize_close_economics(
                position=position,
                exit_fill_per_share=exit_px,
                quantity=qty,
                position_type=structural,
                close_reason=str(exit_reason or ""),
                quote_bid=float(lotto_d.get("bid") or 0.0) or None,
                quote_ask=float(lotto_d.get("ask") or 0.0) or None,
                quote_mid=float(lotto_d.get("mid_price") or lotto_d.get("mid") or 0.0) or None,
                exit_price_source=str(meta.get("exit_price_source") or "BID_EXECUTABLE"),
                is_credit_spread=(structural == "credit_spread"),
                execution_source="demo_simulated_ledger_close",
            )
            position.realized_pnl = float(net_pnl)
            _pos_meta = getattr(position, "metadata", None)
            meta = dict(_pos_meta) if isinstance(_pos_meta, dict) else dict(meta)
        except Exception as econ_err:
            log.debug(
                "DEMO_LEDGER_CLOSE | economics_skipped | trade_id=%s | %s",
                position_id,
                econ_err,
            )
        meta["demo_ledger_close"] = True
        meta.setdefault("fill_path", "demo_simulated_ledger_close")
        meta.pop("broker_fill_confirmed", None)
        meta["simulated_fill_confirmed"] = True
        meta["exit_reason"] = exit_reason
        position.metadata = meta

        mock = self.mock_executor
        if mock is not None and hasattr(mock, "record_external_closed_position"):
            mock.record_external_closed_position(
                position,
                exit_price=exit_px,
                exit_reason=exit_reason,
            )
        try:
            from modules.live_options_ledger import persist_options_close_from_position

            persist_options_close_from_position(
                position,
                exit_reason=exit_reason,
                filled_exit_price=exit_px,
                mock_executor=mock,
            )
        except Exception as exc:
            log.warning(
                "DEMO_LEDGER_CLOSE | persist_failed | trade_id=%s | %s",
                position_id,
                exc,
            )

        self._record_unified_day_pnl_after_close(float(pnl_dollar))

        self._remove_registry_position(position_id)

        log.info(
            "✅ Position %s closed via demo ledger (external) | reason=%s | pnl=%.2f",
            position_id,
            exit_reason,
            pnl_dollar,
        )
        return position

    @staticmethod
    def _holding_minutes_for_exit_alert(exit_time: Any, entry_time: Any) -> int:
        if not exit_time or not entry_time:
            return 0
        try:
            et = exit_time
            ent = entry_time
            if getattr(et, "tzinfo", None) is None and hasattr(et, "replace"):
                et = et.replace(tzinfo=timezone.utc)
            if getattr(ent, "tzinfo", None) is None and hasattr(ent, "replace"):
                ent = ent.replace(tzinfo=timezone.utc)
            return max(0, int((et - ent).total_seconds() / 60))
        except Exception:
            return 0

    def _record_unified_day_pnl_after_close(self, realized_pnl: float) -> None:
        ts = self.trading_system
        if ts is None:
            return
        try:
            from modules.unified_0dte_account import record_unified_0dte_realized_close

            record_unified_0dte_realized_close(ts, realized_pnl)
        except Exception as exc:
            log.debug("UNIFIED_0DTE_ACCOUNT | day_pnl_after_close_skipped | %s", exc)

    async def _send_position_exit_alert(
        self,
        *,
        closed_position: Any,
        position: "OptionsPosition",
        exit_signal: "ExitSignal",
        exit_reason: str,
        suppress_exit_alert: bool,
    ) -> None:
        if not self.alert_manager or suppress_exit_alert:
            return
        try:
            holding_time_minutes = self._holding_minutes_for_exit_alert(
                exit_signal.exit_time,
                position.entry_time,
            )
            position_dict = (
                closed_position.to_dict()
                if hasattr(closed_position, "to_dict")
                else position.to_dict()
            )
            position_dict["current_value"] = float(exit_signal.exit_price)
            mode = "DEMO" if self.demo_mode else "LIVE"
            entry_px = float(getattr(closed_position, "entry_price", position.entry_price) or 0.0)
            exit_px = float(exit_signal.exit_price)
            structural = str(
                getattr(closed_position, "normalized_position_type", None)
                or getattr(closed_position, "position_type", None)
                or position.position_type
                or ""
            ).lower()
            if structural == "credit_spread":
                pnl_frac = (entry_px - exit_px) / entry_px if entry_px > 0 else 0.0
            else:
                pnl_frac = (exit_px - entry_px) / entry_px if entry_px > 0 else 0.0
            acct_bal = self._snapshot_alert_account_balance()
            realized = float(
                getattr(closed_position, "realized_pnl", None)
                or position.realized_pnl
                or exit_signal.pnl_dollar
                or 0.0
            )
            await self.alert_manager.send_options_position_exit_alert(
                    position=position_dict,
                    exit_price=exit_signal.exit_price,
                    exit_reason=exit_reason,
                    holding_time_minutes=holding_time_minutes,
                    mode=mode,
                    realized_pnl_override=realized,
                    pnl_pct_fraction_override=float(pnl_frac),
                    account_balance_override=acct_bal,
                )
            log.info("✅ Options Position Exit alert sent for %s", position.position_id)
        except Exception as alert_error:
            log.error("Failed to send Options Position Exit alert: %s", alert_error)

    def _fail_lotto_sleeve(
        self,
        reason: str,
        *,
        symbol: str = "",
        diagnostics: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record terminal lotto-sleeve block reason for upstream ORB/Trendline alerts."""
        reason_s = _lotto_sleeve_block_reason(reason)
        self._last_execution_block_reason = reason_s
        self._last_execution_block_diagnostics = (
            dict(diagnostics) if isinstance(diagnostics, dict) else {}
        )
        _live_fail_closed(reason_s, path="execute_lotto_sleeve", symbol=symbol, level="info")

    def _snapshot_alert_account_balance(self) -> Optional[float]:
        """Best-effort cash/equity snapshot for exit Telegram footers (demo or live)."""
        if self._use_mock_fills():
            return float(getattr(self.mock_executor, "account_balance", 0.0) or 0.0)
        api = self.etrade_options_api
        if api is None or not getattr(api, "is_available", lambda: False)():
            return None
        et = getattr(api, "etrade", None)
        if et is None:
            return None
        try:
            account_balance_obj = et.get_account_balance()
            if not account_balance_obj:
                return None
            bal = (
                account_balance_obj.cash_available_for_investment
                or account_balance_obj.account_value
                or 0.0
            )
            return float(bal) if bal else None
        except Exception:
            return None

    def set_etrade_options_api(self, etrade_options_api):
        """
        Set ETrade Options API instance (called from main.py initialization)
        
        Args:
            etrade_options_api: ETradeOptionsAPI instance
        """
        self.etrade_options_api = etrade_options_api
        if etrade_options_api and etrade_options_api.is_available():
            log.info("✅ ETrade Options API set for Options Trading Executor")
        else:
            log.warning("⚠️ ETrade Options API not available")

    def set_trading_system(self, trading_system: Any) -> None:
        """Attach PrimeTradingSystem for unified portfolio risk context."""
        self.trading_system = trading_system

    def _assert_live_registry_write_allowed(self, *, op: str = "mutate") -> None:
        """Live ownership: registry membership changes require synchronizer write token."""
        if self.demo_mode or bool(getattr(self, "_bps_write_token", False)):
            return
        log.error(
            "SOLE_WRITER_VIOLATION | op=%s | Live registry mutations must go through "
            "BrokerPositionSynchronizer",
            op,
        )
        raise RuntimeError("SOLE_WRITER_VIOLATION")

    def _put_registry_position(self, position: OptionsPosition) -> None:
        """Demo-local put, or Live gated put (token required). Prefer synchronizer in Live."""
        if not self.demo_mode:
            self._assert_live_registry_write_allowed(op="put")
        self.positions[position.position_id] = position

    def _remove_registry_position(self, position_id: str) -> Optional[OptionsPosition]:
        if position_id not in self.positions:
            return None
        if not self.demo_mode:
            self._assert_live_registry_write_allowed(op="remove")
        return self.positions.pop(position_id, None)
    def set_broker_adapter(self, broker_adapter: Any) -> None:
        """Attach canonical broker adapter (Live order path)."""
        self.broker_adapter = broker_adapter
        if broker_adapter and getattr(broker_adapter, "is_available", lambda: False)():
            log.info(
                "✅ Broker adapter set: %s",
                getattr(getattr(broker_adapter, "kind", None), "value", broker_adapter),
            )
        else:
            log.warning("⚠️ Broker adapter unavailable or not implemented")

    async def execute_by_strategy(
        self,
        *,
        strategy_type: str,
        direction: str,
        quantity: int = 1,
        single_leg_contract: Optional[OptionContract] = None,
        debit_spread: Optional[DebitSpread] = None,
        credit_spread: Optional[CreditSpread] = None,
        send_open_alert: bool = True,
        entry_max_spread_pct: Optional[float] = None,
        entry_budget: Optional[float] = None,
        warm_chain: Optional[Dict[str, Any]] = None,
        warm_chain_age_sec: Optional[float] = None,
    ) -> Optional[OptionsPosition]:
        """
        Strategy-driven execution router.
        Routing is based on strategy_type, not spread_type.
        """
        st = str(strategy_type or "").strip().lower()
        d = str(direction or "LONG").strip().upper()
        qty = int(quantity or 0)
        if qty < 1:
            log.warning(
                "0DTE_SLOT_BUDGET_REJECT | strategy=%s | reason=quantity_below_one | qty=%s | entry_budget=%s",
                st,
                quantity,
                entry_budget,
            )
            self._last_execution_block_reason = "contract_too_expensive_for_slot"
            return None

        if st in {"lotto", "single_leg", "long_call", "long_put"}:
            if not single_leg_contract:
                log.warning("0DTE_SPREAD_FALLBACK | symbol=unknown | reason=missing_single_leg_contract")
                return None
            try:
                from easy0DTE.modules.dte0_entry_selection import (
                    PATH_ORB_0DTE,
                    validate_single_leg_before_execute,
                )
            except ImportError:
                from .dte0_entry_selection import PATH_ORB_0DTE, validate_single_leg_before_execute
            sym_gate = str(getattr(single_leg_contract, "symbol", "") or "UNKNOWN")
            passes, _, _ = validate_single_leg_before_execute(
                PATH_ORB_0DTE,
                sym_gate,
                single_leg_contract,
            )
            if not passes:
                return None
            from easy0DTE.modules.option_entry_liquidity_validator import (
                validate_option_entry_liquidity,
                validator_diagnostics_payload,
            )
            from modules.spread_policy import orb_entry_max_spread_points

            max_spread_pts = (
                float(entry_max_spread_pct)
                if entry_max_spread_pct is not None and float(entry_max_spread_pct) > 0
                else float(orb_entry_max_spread_points())
            )
            q_age = getattr(single_leg_contract, "quote_age_seconds", None)
            ts = self.trading_system
            mgr = getattr(ts, "options_chain_manager", None) if ts is not None else None
            if q_age is None and mgr is not None and hasattr(mgr, "get_last_chain_fetch_meta"):
                try:
                    meta = mgr.get_last_chain_fetch_meta(sym_gate) or {}
                    if meta.get("quote_age_seconds") is not None:
                        q_age = float(meta.get("quote_age_seconds"))
                except Exception:
                    pass
            require_live = str(os.getenv("REQUIRE_LIVE_OPTION_DATA", "true")).lower() in (
                "1",
                "true",
                "yes",
                "on",
            )
            synth_demo = str(os.getenv("0DTE_DEMO_SYNTHETIC_CHAIN", "false")).lower() in (
                "1",
                "true",
                "yes",
                "on",
            )
            chain_src = "live_chain" if not (self.demo_mode and synth_demo) else "demo_synthetic"
            liq_val = validate_option_entry_liquidity(
                single_leg_contract,
                path="orb",
                max_spread_pct_points=max_spread_pts,
                quote_age_seconds=q_age,
                chain_source=chain_src,
                require_live_chain=require_live,
                demo_mode=bool(self.demo_mode),
                synthetic_allowed=synth_demo and self.demo_mode,
                enforce_quote_age=None,
                require_quote_fields=True,
                budget=float(entry_budget) if entry_budget is not None else None,
            )
            if not liq_val.get("passed"):
                code = str(liq_val.get("reason_code") or "option_liquidity_failed")
                self._last_execution_block_reason = code
                diag = validator_diagnostics_payload(liq_val)
                log.warning(
                    "ORB_0DTE_ENTRY_LIQUIDITY_REJECT | symbol=%s | %s | %s",
                    sym_gate,
                    code,
                    str(liq_val.get("reason_text") or ""),
                )
                if diag:
                    log.warning(
                        "ORB_0DTE_ENTRY_LIQUIDITY_REJECT_DETAIL | symbol=%s | %s",
                        sym_gate,
                        " | ".join(f"{k}={v}" for k, v in diag.items()),
                    )
                return None
            # Prefer explicit warm_chain; else peek select-time manager cache.
            _warm = warm_chain
            _warm_age = warm_chain_age_sec
            if _warm is None and mgr is not None and hasattr(mgr, "peek_cached_chain"):
                try:
                    _max_age = float(
                        os.getenv("LIVE_QUOTE_REFRESH_CACHE_MAX_AGE_SEC", "5") or "5"
                    )
                except (TypeError, ValueError):
                    _max_age = 5.0
                try:
                    _warm, _warm_age = mgr.peek_cached_chain(
                        sym_gate,
                        getattr(single_leg_contract, "expiry", None),
                        max_age_sec=_max_age,
                    )
                except Exception:
                    _warm, _warm_age = None, None
            return await self.execute_lotto_sleeve(
                contract=single_leg_contract,
                quantity=qty,
                send_open_alert=send_open_alert,
                strategy_type=st,
                direction=d,
                spread_type="single_leg",
                entry_max_spread_pct=entry_max_spread_pct,
                warm_chain=_warm if isinstance(_warm, dict) else None,
                warm_chain_age_sec=_warm_age,
            )
        if st in {"momentum_scalper", "itm_probability_spread", "debit_spread"}:
            if not debit_spread:
                log.warning("0DTE_SPREAD_FALLBACK | symbol=unknown | reason=missing_debit_spread")
                return None
            if self._use_broker_fills() and not _live_spreads_guard(
                path="execute_by_strategy:debit_spread",
                symbol=str(getattr(debit_spread, "symbol", "") or ""),
            ):
                return None
            return await self.execute_debit_spread(
                spread=debit_spread,
                quantity=qty,
                send_open_alert=send_open_alert,
                strategy_type=st,
                direction=d,
                spread_type="debit",
            )
        if st == "credit_spread":
            if not credit_spread:
                log.warning("0DTE_SPREAD_FALLBACK | symbol=unknown | reason=missing_credit_spread")
                return None
            if self._use_broker_fills() and not _live_spreads_guard(
                path="execute_by_strategy:credit_spread",
                symbol=str(getattr(credit_spread, "symbol", "") or ""),
            ):
                return None
            return await self.execute_credit_spread(
                spread=credit_spread,
                quantity=qty,
                send_open_alert=send_open_alert,
                strategy_type=st,
                direction=d,
                spread_type="credit",
            )

        log.warning("Unsupported strategy_type for execution: %s", strategy_type)
        return None
    
    async def execute_debit_spread(
        self,
        spread: DebitSpread,
        quantity: int = 1,
        send_open_alert: bool = True,
        strategy_type: str = "",
        direction: str = "",
        spread_type: str = "debit",
    ) -> Optional[OptionsPosition]:
        """
        Execute debit spread order (Demo or Live)
        
        Args:
            spread: DebitSpread object
            quantity: Number of spreads
            
        Returns:
            OptionsPosition or None if execution failed
        """
        # Validate combined package cost for the full spread position.
        # Debit spreads are one combined position under the 35% cap.
        total_cost = spread.debit_cost * quantity * 100  # Options are per 100 shares
        
        # Check max position size as % of account equity (35% max - matches ORB Strategy)
        # Note: max_position_cost check disabled - using only percentage-based limit
        if self.mock_executor:
            account_balance = self._demo_available_balance(self.mock_executor)
            max_allowed = account_balance * self.max_position_size_pct
            if total_cost > max_allowed:
                log.warning(f"Position cost ${total_cost:.2f} exceeds {self.max_position_size_pct*100:.0f}% of account equity (${max_allowed:.2f})")
                return None
        
        # Demo Mode: Use mock executor
        if self._use_mock_fills():
            return await self.mock_executor.execute_debit_spread(
                spread,
                quantity,
                send_open_alert=send_open_alert,
                strategy_type=strategy_type,
                direction=str(direction or "LONG"),
                spread_type=str(spread_type or "debit"),
            )

        if self._use_broker_fills() and not _live_spreads_guard(
            path="execute_debit_spread",
            symbol=str(getattr(spread, "symbol", "") or ""),
        ):
            return None
        
        # Broker path: Use ETrade API (demo sim or live account)
        if self._use_broker_fills() and self.etrade_options_api:
            # Check max position size as % of account equity (33% max)
            try:
                # Get account balance from ETrade API
                if hasattr(self.etrade_options_api, 'etrade') and self.etrade_options_api.etrade:
                    account_balance_obj = self.etrade_options_api.etrade.get_account_balance()
                    if account_balance_obj:
                        # Use cash available for investment or account value
                        account_balance = (
                            account_balance_obj.cash_available_for_investment or
                            account_balance_obj.account_value or
                            0.0
                        )
                        
                        if account_balance > 0:
                            max_allowed = account_balance * self.max_position_size_pct
                            if total_cost > max_allowed:
                                log.warning(f"Position cost ${total_cost:.2f} exceeds {self.max_position_size_pct*100:.0f}% of account equity (${max_allowed:.2f} of ${account_balance:.2f})")
                                return None
                            log.debug(f"✅ Broker path: Position cost ${total_cost:.2f} within {self.max_position_size_pct*100:.0f}% limit (${max_allowed:.2f} of ${account_balance:.2f})")
                        else:
                            log.warning(f"⚠️ Broker path: Could not determine account balance, skipping 33% check")
            except Exception as e:
                log.warning(f"⚠️ Broker path: Error checking account balance for 33% limit: {e}")
                # Continue execution but log warning
            
            try:
                # Convert expiry format: YYYY-MM-DD -> YYYYMMDD
                expiry_etrade = spread.expiry.replace('-', '')
                max_debit = float(spread.debit_cost or 0.0)
                if max_debit <= 0:
                    try:
                        max_debit = float(spread.long_contract.mid_price) - float(spread.short_contract.mid_price)
                    except Exception:
                        max_debit = 0.0
                if max_debit <= 0:
                    log.error("Debit spread open blocked: invalid max_net_debit")
                    return None
                pending_tid = f"PENDING_{spread.symbol}_{int(datetime.now().timestamp())}"
                
                # Place order via ETrade API
                order_response = await self.etrade_options_api.place_debit_spread_order(
                    symbol=spread.symbol,
                    expiry=expiry_etrade,
                    option_type=spread.option_type,
                    long_strike=spread.long_strike,
                    short_strike=spread.short_strike,
                    quantity=quantity,
                    max_net_debit=max_debit,
                    quoted_mid_debit=float(spread.debit_cost or max_debit),
                    strategy=str(strategy_type or "ORB_0DTE"),
                    trade_id=pending_tid,
                    execution_context=_execution_context_for_entry(
                        confidence=0.8,
                        seconds_from_signal=35.0,
                        breakout_score=0.7,
                    ),
                )
                
                if order_response:
                    now = datetime.now()
                    position_id = build_options_position_id(
                        live=True,
                        symbol=spread.symbol,
                        option_type=spread.option_type,
                        expiry=spread.expiry,
                        long_strike=spread.long_strike,
                        short_strike=spread.short_strike,
                        now=now,
                    )
                    
                    norm = build_normalized_metadata_debit_spread(
                        trade_id=position_id,
                        spread=spread,
                        quantity=quantity,
                        strategy_type=strategy_type,
                        direction=str(direction or "LONG"),
                        spread_type=str(spread_type or "debit"),
                    )
                    _orb_st = str(norm.get("orb_strategy_type") or strategy_type or "").strip()
                    position = OptionsPosition(
                        position_id=position_id,
                        symbol=spread.symbol,
                        position_type='debit_spread',
                        debit_spread=spread,
                        entry_price=spread.debit_cost,
                        quantity=quantity,
                        current_value=spread.debit_cost,
                        unrealized_pnl=0.0,
                        strategy_type=_orb_st,
                        metadata={"normalized_options": norm},
                    )
                    log_position_type_normalized(position_id, norm["position_type"], "debit_spread")
                    log_metadata_normalized(position_id, norm["position_type"], spread.symbol)
                    
                    try:
                        from modules.broker_position_synchronizer import get_or_create_synchronizer

                        sync = get_or_create_synchronizer(self.trading_system)
                        position = await sync.register_local_position_from_executor(position)
                    except Exception as sync_err:
                        log.error(
                            "SOLE_WRITER | debit_spread_register_failed | trade_id=%s | %s",
                            position_id,
                            sync_err,
                        )
                        return None
                    
                    log.info(f"✅ Live debit spread executed: Position ID {position_id}")
                    return position
                else:
                    log.error("Failed to place debit spread order via ETrade API")
                    return None
                    
            except Exception as e:
                log.error(f"Error placing debit spread order via ETrade API: {e}")
                return None

        if self._use_broker_fills():
            _live_fail_closed(
                "broker_adapter_or_etrade_api_unavailable",
                path="execute_debit_spread",
                symbol=getattr(spread, "symbol", ""),
            )
        return None
    async def execute_lotto_sleeve(
        self,
        contract: OptionContract,
        quantity: int = 1,
        send_open_alert: bool = True,
        strategy_type: str = "",
        direction: str = "",
        spread_type: str = "single_leg",
        entry_max_spread_pct: Optional[float] = None,
        warm_chain: Optional[Dict[str, Any]] = None,
        warm_chain_age_sec: Optional[float] = None,
    ) -> Optional[OptionsPosition]:
        """
        Execute lotto sleeve (single-leg option) (Demo or Live)
        
        Args:
            contract: OptionContract object
            quantity: Number of contracts
            warm_chain: Optional select-time chain dict for quote-refresh reuse
            warm_chain_age_sec: Age of warm_chain in seconds (0 = just fetched)
            
        Returns:
            OptionsPosition or None if execution failed
        """
        adapter = self.broker_adapter
        ts = self.trading_system
        st_norm = str(strategy_type or "single_leg").strip().lower()
        if st_norm == "lotto":
            st_norm = "single_leg"
        path = (
            "trendline_0dte"
            if "trendline" in st_norm
            else (
                "breakout_0dte"
                if "breakout" in st_norm
                else "orb_0dte"
            )
        )
        chain_mgr = getattr(ts, "options_chain_manager", None) if ts is not None else None
        option_side = str(getattr(contract, "option_type", "") or "")
        reservation_id = f"{path}_{contract.symbol}_{int(time.time() * 1000)}"
        quantity = int(quantity or 1)
        mgr = None
        guard = None
        unified_import_ok = False
        self._last_execution_block_reason = None
        self._last_execution_block_diagnostics = {}

        try:
            try:
                from modules.unified_0dte_portfolio_risk_manager import (
                    Unified0DTEReservationGuard,
                    get_unified_0dte_portfolio_risk_manager,
                )

                unified_import_ok = True
            except ImportError:
                Unified0DTEReservationGuard = None  # type: ignore[misc, assignment]
                get_unified_0dte_portfolio_risk_manager = None  # type: ignore[assignment]

            if unified_import_ok and get_unified_0dte_portfolio_risk_manager is not None:
                mgr = get_unified_0dte_portfolio_risk_manager(ts)
                guard = Unified0DTEReservationGuard(mgr, reservation_id)

            use_unified_risk = bool(
                unified_import_ok
                and mgr is not None
                and mgr.config.enabled
                and ts is not None
            )
            liquidity_guard_audit: Dict[str, Any] = {}
            if use_unified_risk:
                red_day = bool(getattr(ts, "_red_day_filter_blocked", False))
                decision = await mgr.evaluate_entry(
                    system=ts,
                    path=path,
                    symbol=str(contract.symbol or ""),
                    option_side=option_side,
                    quantity=quantity,
                    contract=contract,
                    demo_mode=bool(self.demo_mode),
                    broker_adapter=adapter,
                    red_day_active=red_day,
                    reservation_id=reservation_id,
                    max_spread_pct=entry_max_spread_pct,
                    warm_chain=warm_chain if isinstance(warm_chain, dict) else None,
                    warm_chain_age_sec=warm_chain_age_sec,
                    options_chain_manager=chain_mgr,
                )
                if not decision.approved:
                    _audit = decision.audit if isinstance(decision.audit, dict) else {}
                    _exec_risk = (
                        _audit.get("execution_risk")
                        if isinstance(_audit.get("execution_risk"), dict)
                        else {}
                    )
                    _diag = dict(_exec_risk) if _exec_risk else dict(_audit)
                    self._fail_lotto_sleeve(
                        decision.block_reason or "unified_risk_rejected",
                        symbol=str(contract.symbol or ""),
                        diagnostics=_diag,
                    )
                    return None
                quantity = int(decision.approved_quantity)
                _ok_audit = decision.audit if isinstance(decision.audit, dict) else {}
                _ok_exec = _ok_audit.get("execution_risk")
                if isinstance(_ok_exec, dict):
                    liquidity_guard_audit = dict(_ok_exec)
                if quantity < 1:
                    self._fail_lotto_sleeve(
                        "quantity_below_minimum",
                        symbol=str(contract.symbol or ""),
                    )
                    return None
            else:
                if unified_import_ok and Unified0DTEReservationGuard is not None:
                    guard = Unified0DTEReservationGuard(None, "")
                from modules.dte0_execution_risk_manager import (
                    apply_live_execution_risk_limits,
                    refresh_contract_quotes_if_enabled,
                )

                try:
                    from modules.broker_adapter import BrokerAccountBalance
                except ImportError:
                    BrokerAccountBalance = None  # type: ignore

                contract_for_exec = contract
                if adapter and getattr(adapter, "is_available", lambda: False)():
                    contract_for_exec = await refresh_contract_quotes_if_enabled(
                        contract,
                        adapter,
                        warm_chain=warm_chain if isinstance(warm_chain, dict) else None,
                        warm_chain_age_sec=warm_chain_age_sec,
                        options_chain_manager=chain_mgr,
                    )
                elif warm_chain is not None or chain_mgr is not None:
                    # Demo / no adapter: still hydrate from warm/manager cache when fresh.
                    contract_for_exec = await refresh_contract_quotes_if_enabled(
                        contract,
                        None,
                        warm_chain=warm_chain if isinstance(warm_chain, dict) else None,
                        warm_chain_age_sec=warm_chain_age_sec,
                        options_chain_manager=chain_mgr,
                    )

                account_balance = None
                if adapter and getattr(adapter, "is_available", lambda: False)():
                    try:
                        account_balance = await adapter.get_account_balance()
                    except Exception as exc:
                        log.warning(
                            "LIVE_ENTRY_BALANCE_READ_FAILED | symbol=%s | error=%s",
                            contract.symbol,
                            exc,
                        )
                elif self._use_mock_fills() and BrokerAccountBalance is not None:
                    try:
                        cash = float(self._demo_available_balance(self.mock_executor))
                        acct = float(
                            getattr(self.mock_executor, "account_balance", cash) or cash
                        )
                        account_balance = BrokerAccountBalance(
                            cash_available=cash,
                            account_value=acct,
                            buying_power=cash,
                        )
                    except (TypeError, ValueError):
                        account_balance = None

                risk = apply_live_execution_risk_limits(
                    quantity=quantity,
                    contract=contract_for_exec,
                    strategy_type=str(strategy_type or "single_leg"),
                    demo_mode=bool(self.demo_mode),
                    broker_adapter=adapter,
                    account_balance=account_balance,
                    max_spread_pct=entry_max_spread_pct,
                )
                if isinstance(risk.audit_data, dict):
                    liquidity_guard_audit = dict(risk.audit_data)
                if risk.blocked:
                    self._fail_lotto_sleeve(
                        risk.block_reason or "execution_risk_blocked",
                        symbol=str(contract.symbol or ""),
                        diagnostics=(
                            risk.audit_data
                            if isinstance(risk.audit_data, dict)
                            else None
                        ),
                    )
                    return None
                quantity = int(risk.approved_quantity)
                contract = contract_for_exec
                guard = Unified0DTEReservationGuard(None, "")

            if self._use_mock_fills():
                try:
                    from modules.po_preorder_seal import seal_selected_contract_before_order

                    seal_selected_contract_before_order(contract)
                except Exception:
                    log.debug("preorder trade profile seal skipped", exc_info=True)
                try:
                    from modules.calendar_expiry_metadata import resolve_expiry_selection_from_context

                    _expiry_sel = resolve_expiry_selection_from_context(
                        contract=contract,
                        trading_system=self.trading_system,
                    )
                except Exception:
                    _expiry_sel = {}
                position = await self.mock_executor.execute_lotto_sleeve(
                    contract,
                    quantity,
                    send_open_alert=send_open_alert,
                    strategy_type=strategy_type,
                    direction=str(direction or "LONG"),
                    spread_type=str(spread_type or "single_leg"),
                    expiry_selection=_expiry_sel or None,
                )
                if position is None:
                    self._fail_lotto_sleeve("mock_lotto_open_failed", symbol=str(contract.symbol or ""))
                    return None
                if liquidity_guard_audit:
                    try:
                        meta = (
                            position.metadata
                            if isinstance(getattr(position, "metadata", None), dict)
                            else {}
                        )
                        meta = dict(meta)
                        meta["execution_risk"] = liquidity_guard_audit
                        if liquidity_guard_audit.get("underlying_adv_dollars") is not None:
                            meta["underlying_adv_dollars"] = (
                                liquidity_guard_audit.get("underlying_adv_dollars")
                            )
                        if liquidity_guard_audit.get("bid_size") is not None:
                            meta["bid_size"] = liquidity_guard_audit.get("bid_size")
                        if liquidity_guard_audit.get("ask_size") is not None:
                            meta["ask_size"] = liquidity_guard_audit.get("ask_size")
                        position.metadata = meta
                    except Exception as lg_stamp_err:
                        log.debug(
                            "LIQUIDITY_GUARD_ADV | stage=stamp_trade_failed | err=%s",
                            lg_stamp_err,
                        )
                return position

            if self._use_broker_fills():
                if not adapter or not getattr(adapter, "is_available", lambda: False)():
                    self._fail_lotto_sleeve(
                        "broker_adapter_unavailable",
                        symbol=str(getattr(contract, "symbol", "") or ""),
                    )
                    return None
                if OptionOrderRequest is None:
                    self._fail_lotto_sleeve(
                        "broker_adapter_fill_required",
                        symbol=str(getattr(contract, "symbol", "") or ""),
                    )
                    return None

                try:
                    from modules.po_preorder_seal import seal_selected_contract_before_order

                    seal_selected_contract_before_order(contract)
                except Exception:
                    log.debug("preorder trade profile seal skipped", exc_info=True)
                pending_tid = f"PENDING_{contract.symbol}_{int(datetime.now().timestamp())}"
                submit_ts = datetime.now(timezone.utc).isoformat()
                order_req = OptionOrderRequest(
                    symbol=contract.symbol,
                    expiry=contract.expiry.replace("-", ""),
                    option_type=contract.option_type,
                    strike=float(contract.strike),
                    quantity=quantity,
                    side="BUY",
                    bid=float(contract.bid or 0.0),
                    ask=float(contract.ask or 0.0),
                    mid=float(contract.mid_price or 0.0),
                    strategy=str(strategy_type or "ORB_0DTE"),
                    trade_id=pending_tid,
                    execution_context=_execution_context_for_entry(
                        confidence=0.82,
                        seconds_from_signal=35.0,
                        breakout_score=0.72,
                    ),
                )
                broker_result = await adapter.submit_option_order(order_req)
                if not broker_result.success:
                    _broker_err = str(broker_result.error or "broker_submit_failed")
                    self._fail_lotto_sleeve(
                        _broker_err,
                        symbol=str(contract.symbol or ""),
                    )
                    return None
                if not broker_result.fill_confirmed:
                    _fill_err = str(broker_result.error or "fill_not_confirmed")
                    self._fail_lotto_sleeve(
                        _fill_err,
                        symbol=str(contract.symbol or ""),
                    )
                    return None

                entry_px = float(broker_result.average_fill_price or 0.0)
                if entry_px <= 0.0:
                    entry_px = float(getattr(contract, "ask", 0.0) or 0.0)
                    log.warning(
                        "LIVE_ENTRY_FILL_PRICE | symbol=%s | strike=%s | source=ask | ask=%.4f | "
                        "note=broker fill price missing",
                        contract.symbol,
                        contract.strike,
                        entry_px,
                    )
                if entry_px <= 0.0:
                    entry_px = float(getattr(contract, "mid_price", 0.0) or 0.0)
                fill_ts = datetime.now(timezone.utc).isoformat()
                now = datetime.now()
                position_id = build_options_position_id(
                    live=True,
                    symbol=contract.symbol,
                    option_type=contract.option_type,
                    expiry=contract.expiry,
                    strike=contract.strike,
                    now=now,
                )
                try:
                    from modules.calendar_expiry_metadata import resolve_expiry_selection_from_context

                    _expiry_sel = resolve_expiry_selection_from_context(
                        contract=contract,
                        trading_system=self.trading_system,
                    )
                except Exception:
                    _expiry_sel = {}
                norm = build_normalized_metadata_single_leg(
                    trade_id=position_id,
                    contract=contract,
                    quantity=quantity,
                    strategy_type=strategy_type or "single_leg",
                    direction=str(direction or "LONG"),
                    spread_type=str(spread_type or "single_leg"),
                    entry_fill_price=float(entry_px),
                    expiry_selection=_expiry_sel or None,
                )
                norm["broker_order_id"] = broker_result.order_id
                norm["broker_fill_confirmed"] = True
                _orb_st = str(norm.get("orb_strategy_type") or strategy_type or "single_leg").strip()
                if _orb_st == "lotto":
                    _orb_st = "single_leg"
                try:
                    from modules.options_trade_economics import build_entry_economics

                    entry_econ = build_entry_economics(
                        fill_per_share=float(entry_px),
                        quantity=int(quantity),
                        position_type="lotto",
                        quote_bid=float(contract.bid or 0.0) or None,
                        quote_ask=float(contract.ask or 0.0) or None,
                        quote_mid=float(contract.mid_price or 0.0) or None,
                        fill_mode="live",
                        execution_source="broker_adapter",
                    )
                except Exception:
                    entry_econ = None
                position_metadata: Dict[str, Any] = {
                    "normalized_options": norm,
                    "broker_order_id": broker_result.order_id,
                    "broker_fill_confirmed": True,
                    "fill_path": "live_broker_confirmed",
                }
                if liquidity_guard_audit:
                    position_metadata["execution_risk"] = liquidity_guard_audit
                    if liquidity_guard_audit.get("underlying_adv_dollars") is not None:
                        position_metadata["underlying_adv_dollars"] = (
                            liquidity_guard_audit.get("underlying_adv_dollars")
                        )
                    if liquidity_guard_audit.get("bid_size") is not None:
                        position_metadata["bid_size"] = liquidity_guard_audit.get("bid_size")
                    if liquidity_guard_audit.get("ask_size") is not None:
                        position_metadata["ask_size"] = liquidity_guard_audit.get("ask_size")
                # Prefer post-guard wire from normalize only — never re-stamp raw
                # contract.osi_key (can reintroduce Call wire on a Put fill).
                _osi = str(norm.get("broker_quote_request_symbol") or norm.get("osi_key") or "").strip()
                if _osi:
                    position_metadata["osi_key"] = _osi
                    position_metadata["osi_symbol"] = _osi
                    position_metadata["broker_quote_request_symbol"] = _osi
                    position_metadata["exit_grade_enrollment_eligible"] = True
                else:
                    position_metadata["exit_grade_enrollment_eligible"] = False
                    position_metadata["exit_grade_enrollment_blocker"] = "missing_frozen_quote_wire"
                # Scrub contaminated wire off the live contract object so lotto/sync
                # cannot reintroduce a Call key on a Put (or vice versa).
                try:
                    if hasattr(contract, "osi_key"):
                        setattr(contract, "osi_key", _osi)
                except Exception:
                    pass
                _occ = str(norm.get("occ_symbol") or "").strip()
                if _occ:
                    position_metadata["occ_symbol"] = _occ
                    position_metadata["option_symbol"] = _occ
                    position_metadata["contract_symbol"] = _occ
                if isinstance(entry_econ, dict):
                    position_metadata["trade_economics_entry"] = entry_econ
                if attach_orb_entry_execution_telemetry:
                    try:
                        attach_orb_entry_execution_telemetry(
                            position_metadata,
                            contract=contract,
                            fill_price=entry_px,
                            submit_ts=submit_ts,
                            fill_ts=fill_ts,
                            submitted_limit=float(contract.mid_price or 0.0) or None,
                            trade_id=position_id,
                            symbol=contract.symbol,
                            strategy_type=_orb_st or "ORB_0DTE",
                            order_type="LIMIT",
                        )
                    except Exception as telem_err:
                        log.debug(
                            "ORB entry telemetry attach skipped | trade_id=%s | %s",
                            position_id,
                            telem_err,
                        )
                position = OptionsPosition(
                    position_id=position_id,
                    symbol=contract.symbol,
                    position_type="lotto",
                    lotto_contract=contract,
                    entry_price=entry_px,
                    quantity=quantity,
                    current_value=entry_px,
                    unrealized_pnl=0.0,
                    strategy_type=_orb_st,
                    metadata=position_metadata,
                )
                log_position_type_normalized(position_id, norm["position_type"], "lotto")
                log_metadata_normalized(position_id, norm["position_type"], contract.symbol)
                # Live sole-writer: return facts to synchronizer; do not write self.positions here.
                try:
                    from modules.broker_live_audits import _build_occ_symbol
                    from modules.broker_position_synchronizer import get_or_create_synchronizer
                    from modules.position_registry_contract import ConfirmedOpenFact

                    occ = _build_occ_symbol(
                        symbol=str(contract.symbol),
                        expiry=str(contract.expiry),
                        option_type=str(contract.option_type),
                        strike=float(contract.strike),
                    )
                    submitted_at = datetime.fromisoformat(submit_ts.replace("Z", "+00:00"))
                    confirmed_at = datetime.fromisoformat(fill_ts.replace("Z", "+00:00"))
                    open_fact = ConfirmedOpenFact(
                        broker_order_id=str(broker_result.order_id or ""),
                        broker_execution_id=str(
                            getattr(broker_result, "execution_id", None)
                            or getattr(broker_result, "fill_id", None)
                            or ""
                        )
                        or None,
                        contract_symbol=occ,
                        underlying=str(contract.symbol or "").upper(),
                        expiration=str(contract.expiry or ""),
                        strike=float(contract.strike),
                        call_put=str(contract.option_type or "").lower(),
                        filled_quantity=int(quantity),
                        fill_price=float(entry_px),
                        strategy=str(_orb_st or strategy_type or "ORB_0DTE"),
                        signal_id=str(pending_tid or "") or None,
                        trade_id=str(position_id),
                        submitted_at=submitted_at,
                        confirmed_at=confirmed_at,
                        lotto_contract=contract,
                        metadata_seed=position_metadata,
                        direction=str(direction or "LONG"),
                        spread_type=str(spread_type or "single_leg"),
                    )
                    sync = get_or_create_synchronizer(self.trading_system)
                    synced = await sync.register_confirmed_fill(open_fact)
                    log.info(
                        "✅ Live lotto sleeve broker-confirmed via synchronizer: %s order_id=%s",
                        position_id,
                        broker_result.order_id,
                    )
                    return synced if synced is not None else position
                except Exception as sync_open_err:
                    # Broker fill already confirmed. If put_position succeeded before the
                    # exception, return the registry row so stealth + capital accounting attach.
                    # Never treat a registered Live fill as a failed sleeve open.
                    registered = None
                    try:
                        registered = (getattr(self, "positions", None) or {}).get(str(position_id))
                    except Exception:
                        registered = None
                    if registered is not None:
                        log.error(
                            "SOLE_WRITER | register_confirmed_fill_partial | trade_id=%s | "
                            "error=%s | registry_present=true | returning_registered",
                            position_id,
                            sync_open_err,
                            exc_info=True,
                        )
                        return registered
                    log.error(
                        "SOLE_WRITER | register_confirmed_fill_failed | trade_id=%s | error=%s",
                        position_id,
                        sync_open_err,
                        exc_info=True,
                    )
                    self._fail_lotto_sleeve(
                        f"synchronizer_register_failed:{sync_open_err}",
                        symbol=str(contract.symbol or ""),
                    )
                    return None

            self._fail_lotto_sleeve("execution_path_unavailable", symbol=str(contract.symbol or ""))
            return None
        except asyncio.CancelledError:
            if guard and mgr and mgr.has_reservation(reservation_id):
                guard.release("cancelled")
            raise
        except Exception as exc:
            log.exception(
                "execute_lotto_sleeve failed | symbol=%s | error=%s",
                getattr(contract, "symbol", ""),
                exc,
            )
            self._fail_lotto_sleeve("execute_lotto_sleeve_exception", symbol=str(getattr(contract, "symbol", "") or ""))
            return None
        finally:
            if guard and mgr and mgr.has_reservation(reservation_id):
                guard.release("execution_complete")
        return None

    async def execute_credit_spread(
        self,
        spread: CreditSpread,
        quantity: int = 1,
        send_open_alert: bool = True,
        strategy_type: str = "",
        direction: str = "",
        spread_type: str = "credit",
    ) -> Optional[OptionsPosition]:
        """
        Execute credit spread order (Demo or Live)
        
        Args:
            spread: CreditSpread object
            quantity: Number of spreads
            
        Returns:
            OptionsPosition or None if execution failed
        """
        # Validate combined package margin for the full spread position.
        # Credit spreads are one combined position under the 35% cap.
        # Margin requirement = max_loss (spread_width - credit_received) * quantity * 100
        margin_requirement = spread.max_loss * quantity * 100  # Options are per 100 shares
        
        # Check max position size as % of account equity (35% max - matches ORB Strategy)
        # Note: max_position_cost check disabled - using only percentage-based limit
        if self.mock_executor:
            account_balance = self._demo_available_balance(self.mock_executor)
            max_allowed = account_balance * self.max_position_size_pct
            if margin_requirement > max_allowed:
                log.warning(f"Margin requirement ${margin_requirement:.2f} exceeds {self.max_position_size_pct*100:.0f}% of account equity (${max_allowed:.2f})")
                return None
        
        # Demo Mode: Use mock executor
        if self._use_mock_fills():
            return await self.mock_executor.execute_credit_spread(
                spread,
                quantity,
                send_open_alert=send_open_alert,
                strategy_type=strategy_type,
                direction=str(direction or "SHORT"),
                spread_type=str(spread_type or "credit"),
            )

        if self._use_broker_fills() and not _live_spreads_guard(
            path="execute_credit_spread",
            symbol=str(getattr(spread, "symbol", "") or ""),
        ):
            return None
        
        # Broker path: Use ETrade API
        if self._use_broker_fills() and self.etrade_options_api:
            # Check max position size as % of account equity (33% max)
            try:
                # Get account balance from ETrade API
                if hasattr(self.etrade_options_api, 'etrade') and self.etrade_options_api.etrade:
                    account_balance_obj = self.etrade_options_api.etrade.get_account_balance()
                    if account_balance_obj:
                        # Use cash available for investment or account value
                        account_balance = (
                            account_balance_obj.cash_available_for_investment or
                            account_balance_obj.account_value or
                            0.0
                        )
                        
                        if account_balance > 0:
                            max_allowed = account_balance * self.max_position_size_pct
                            if margin_requirement > max_allowed:
                                log.warning(f"Margin requirement ${margin_requirement:.2f} exceeds {self.max_position_size_pct*100:.0f}% of account equity (${max_allowed:.2f} of ${account_balance:.2f})")
                                return None
                            log.debug(f"✅ Broker path: Margin requirement ${margin_requirement:.2f} within {self.max_position_size_pct*100:.0f}% limit (${max_allowed:.2f} of ${account_balance:.2f})")
                        else:
                            log.warning(f"⚠️ Broker path: Could not determine account balance, skipping 33% check")
            except Exception as e:
                log.warning(f"⚠️ Broker path: Error checking account balance for 33% limit: {e}")
                # Continue execution but log warning
            
            try:
                # Convert expiry format: YYYY-MM-DD -> YYYYMMDD
                expiry_etrade = spread.expiry.replace('-', '')
                
                # Place order via ETrade API
                order_response = await self.etrade_options_api.place_credit_spread_order(
                    symbol=spread.symbol,
                    expiry=expiry_etrade,
                    option_type=spread.option_type,
                    short_strike=spread.short_strike,
                    long_strike=spread.long_strike,
                    quantity=quantity
                )
                
                if order_response:
                    now = datetime.now()
                    position_id = build_options_position_id(
                        live=True,
                        symbol=spread.symbol,
                        option_type=spread.option_type,
                        expiry=spread.expiry,
                        long_strike=spread.short_strike,
                        short_strike=spread.long_strike,
                        now=now,
                    )
                    
                    norm = build_normalized_metadata_credit_spread(
                        trade_id=position_id,
                        spread=spread,
                        quantity=quantity,
                        strategy_type=strategy_type or "debit_spread",
                        direction=str(direction or "SHORT"),
                        spread_type=str(spread_type or "credit"),
                    )
                    _orb_st = str(norm.get("orb_strategy_type") or strategy_type or "").strip()
                    position = OptionsPosition(
                        position_id=position_id,
                        symbol=spread.symbol,
                        position_type='credit_spread',
                        credit_spread=spread,
                        entry_price=spread.credit_received,  # Entry price = credit received
                        quantity=quantity,
                        current_value=spread.credit_received,
                        unrealized_pnl=0.0,
                        strategy_type=_orb_st,
                        metadata={"normalized_options": norm},
                    )
                    log_position_type_normalized(position_id, norm["position_type"], "credit_spread")
                    log_metadata_normalized(position_id, norm["position_type"], spread.symbol)
                    
                    try:
                        from modules.broker_position_synchronizer import get_or_create_synchronizer

                        sync = get_or_create_synchronizer(self.trading_system)
                        position = await sync.register_local_position_from_executor(position)
                    except Exception as sync_err:
                        log.error(
                            "SOLE_WRITER | credit_spread_register_failed | trade_id=%s | %s",
                            position_id,
                            sync_err,
                        )
                        return None
                    
                    log.info(f"✅ Live credit spread executed: Position ID {position_id}")
                    return position
                else:
                    log.error("Failed to place credit spread order via ETrade API")
                    return None
                    
            except Exception as e:
                log.error(f"Error placing credit spread order via ETrade API: {e}")
                return None

        if self._use_broker_fills():
            _live_fail_closed(
                "broker_adapter_or_etrade_api_unavailable",
                path="execute_credit_spread",
                symbol=getattr(spread, "symbol", ""),
            )
        return None
    
    async def update_position_value(
        self,
        position_id: str,
        current_value: float
    ) -> Optional[OptionsPosition]:
        """
        Update position current value and P&L
        
        Args:
            position_id: Position ID
            current_value: Current position value
            
        Returns:
            Updated OptionsPosition or None if not found
        """
        if self._use_mock_fills():
            updated = await self.mock_executor.update_position_value(position_id, current_value)
            if updated is not None:
                if position_id in self.positions:
                    self.positions[position_id].current_value = current_value
                    try:
                        self.positions[position_id].unrealized_pnl = float(updated.unrealized_pnl)
                    except Exception:
                        pass
                return self.positions.get(position_id) or updated
            if position_id not in self.positions:
                log.warning(f"Position {position_id} not found")
            return None

        if position_id not in self.positions:
            log.warning(f"Position {position_id} not found")
            return None

        position = self.positions[position_id]
        position.current_value = current_value
        meta = position.metadata if isinstance(getattr(position, "metadata", None), dict) else {}
        norm = meta.get("normalized_options")
        if isinstance(norm, dict):
            norm["current_value"] = float(current_value)

        from modules.options_trade_economics import net_result_pnl_dollars

        pt = str(getattr(position, "position_type", "") or "lotto")
        position.unrealized_pnl = net_result_pnl_dollars(
            entry_fill=float(position.entry_price or 0.0),
            exit_fill=float(current_value or 0.0),
            quantity=int(position.quantity or 0),
            is_credit_spread="credit" in pt.lower(),
            position_type=pt,
        )
        
        return position
    
    async def update_positions_with_real_prices(
        self,
        *,
        shared_chain_cache: Optional[Dict[Tuple[str, str, str], Dict[str, Any]]] = None,
        tick_id: Optional[str] = None,
    ) -> None:
        """
        Update all open positions with real-time options prices from E*TRADE API (Rev 00238)
        
        This ensures exit decisions are based on actual options P&L, not underlying price movement.
        For example: QQQ moves +0.86% but QQQ 628c moves from $0.19 to $0.97 (+410%)
        """
        open_positions = self.get_open_positions()

        if not open_positions:
            return

        log.debug(f"Updating {len(open_positions)} positions with real-time options prices...")

        tick_key = str(tick_id or "default")
        cache_ref = shared_chain_cache if isinstance(shared_chain_cache, dict) else {}
        use_etrade_chain = bool(self.etrade_options_api)

        def _usable_exact_oq(position: OptionsPosition) -> Optional[Dict[str, Any]]:
            md_ex = getattr(position, "metadata", None)
            if not isinstance(md_ex, dict):
                return None
            exact_oq = md_ex.get("exact_quote") or md_ex.get("exact_contract_quote") or md_ex.get("option_quote")
            if not isinstance(exact_oq, dict):
                return None
            src = str(exact_oq.get("source") or exact_oq.get("quote_source") or "")
            # Canonical Market Quote source is ``exact_quote`` (legacy: exact_contract*).
            try:
                from modules.exit_grade_durability import quote_source_is_exact_contract

                if not quote_source_is_exact_contract(src):
                    return None
            except Exception:
                src_l = src.strip().lower()
                if not (
                    src_l.startswith("exact_quote")
                    or src_l.startswith("exact_contract")
                    or src_l == "exit_grade"
                ):
                    return None
            # bid=0.00 is a valid live quote — do not use truthiness.
            if exact_oq.get("bid") is None:
                return None
            return exact_oq

        has_usable_exact = any(_usable_exact_oq(p) is not None for p in open_positions)
        if not use_etrade_chain and not cache_ref and not has_usable_exact:
            log.debug(
                "OPTION_CHAIN_UPDATE_SKIP | reason=no_etrade_api_and_empty_chain_cache | positions=%d",
                len(open_positions),
            )
            return

        def _normalize_expiry(raw_expiry: Any) -> str:
            exp_s = str(raw_expiry or "").strip()
            if "-" in exp_s:
                return exp_s.replace("-", "")
            return exp_s

        def _to_float(value: Any) -> Optional[float]:
            try:
                return float(value)
            except (TypeError, ValueError):
                return None

        def _extract_contract_quote(
            contracts: List[Any],
            target_strike: float,
            *,
            leg_role: str = "long",
        ) -> Optional[Dict[str, Any]]:
            """Return bid/ask/mid/last + executable STC price for premium engine."""
            exact_contract = None
            nearest_contract = None
            nearest_diff = None
            for contract in contracts:
                try:
                    if isinstance(contract, dict):
                        c_strike = float(contract.get("strike", 0.0) or 0.0)
                    else:
                        c_strike = float(getattr(contract, "strike", 0.0) or 0.0)
                except (TypeError, ValueError):
                    continue
                diff = abs(c_strike - float(target_strike))
                if diff < 0.01:
                    exact_contract = contract
                    break
                if nearest_contract is None or diff < float(nearest_diff):
                    nearest_contract = contract
                    nearest_diff = diff

            chosen = exact_contract
            source = "chain_strike_match"
            if chosen is None and nearest_contract is not None and float(nearest_diff or 999.0) <= 0.5:
                chosen = nearest_contract
                source = "nearest_strike"
            if chosen is None:
                return None

            bid = float(OptionsExitManager._nested_field(chosen, "bid", 0.0) or 0.0)
            ask = float(OptionsExitManager._nested_field(chosen, "ask", 0.0) or 0.0)
            mid = float(OptionsExitManager._nested_field(chosen, "mid_price", 0.0) or 0.0)
            if mid <= 0 and bid > 0 and ask > 0:
                mid = (bid + ask) / 2.0
            last = float(OptionsExitManager._nested_field(chosen, "last", 0.0) or 0.0)
            executable = _extract_contract_price(contracts, float(target_strike), leg_role=leg_role)
            return {
                "bid": bid if bid > 0 else None,
                "ask": ask if ask > 0 else None,
                "mid": mid if mid > 0 else None,
                "last": last if last > 0 else None,
                "source": source,
                "quote_source": source,
                "contract_identity_matched": False,
                "contract_matched": False,
                "executable": float(executable) if executable is not None else None,
            }

        def _extract_contract_price(
            contracts: List[Any],
            target_strike: float,
            *,
            leg_role: str = "long",
        ) -> Optional[float]:
            exact_contract = None
            nearest_contract = None
            nearest_diff = None
            for contract in contracts:
                try:
                    if isinstance(contract, dict):
                        c_strike = float(contract.get("strike", 0.0) or 0.0)
                    else:
                        c_strike = float(getattr(contract, "strike", 0.0) or 0.0)
                except (TypeError, ValueError):
                    continue
                diff = abs(c_strike - float(target_strike))
                if diff < 0.01:
                    exact_contract = contract
                    break
                if nearest_contract is None or diff < float(nearest_diff):
                    nearest_contract = contract
                    nearest_diff = diff

            chosen = exact_contract
            if chosen is None and nearest_contract is not None and float(nearest_diff or 999.0) <= 0.5:
                chosen = nearest_contract
            if chosen is None:
                return None

            bid = float(OptionsExitManager._nested_field(chosen, "bid", 0.0) or 0.0)
            ask = float(OptionsExitManager._nested_field(chosen, "ask", 0.0) or 0.0)
            mid = float(OptionsExitManager._nested_field(chosen, "mid_price", 0.0) or 0.0)
            if mid <= 0 and bid > 0 and ask > 0:
                mid = (bid + ask) / 2.0
            last = float(OptionsExitManager._nested_field(chosen, "last", 0.0) or 0.0)
            try:
                from modules.option_executable_pricing import (
                    leg_executable_mark_price,
                    long_option_sell_to_close_price,
                    true_to_live_executable_marks_enabled,
                )
            except ImportError:
                leg_executable_mark_price = None  # type: ignore
                long_option_sell_to_close_price = None  # type: ignore
                true_to_live_executable_marks_enabled = lambda: False  # type: ignore

            if true_to_live_executable_marks_enabled() and leg_executable_mark_price is not None:
                return float(
                    leg_executable_mark_price(
                        leg_role=str(leg_role or "long"),
                        bid=bid,
                        ask=ask,
                        mid=mid,
                        last=last,
                        true_to_live=True,
                    )
                )
            if str(leg_role).lower() == "long" and long_option_sell_to_close_price is not None:
                return float(
                    long_option_sell_to_close_price(bid=bid, ask=ask, mid=mid, last=last)  # type: ignore
                )
            if mid > 0:
                px = mid
            else:
                if last > 0:
                    px = last
                elif bid > 0 and ask > 0:
                    px = (bid + ask) / 2.0
                else:
                    return None
            # Conservative marking for spread monitoring (legacy when true-to-live off).
            if str(leg_role).lower() == "short":
                return float(px) * 1.02
            if str(leg_role).lower() == "long":
                return float(px) * 0.98
            return float(px)

        def _apply_premium_engine_update(
            position: OptionsPosition,
            *,
            option_quote: Dict[str, Any],
            option_type: Optional[str],
            strike: Optional[float],
            position_type: str,
            chain_blob: Optional[Dict[str, Any]],
        ) -> bool:
            """Hydrate premium via Premium Engine; Live patch applied by synchronizer."""
            try:
                from modules.broker_position_synchronizer import get_or_create_synchronizer
                from modules.premium_engine import evaluate as premium_evaluate
                from modules.position_registry_contract import (
                    PremiumEvaluationRequest,
                    QuoteContext,
                )

                ctx = QuoteContext(
                    option_quote=option_quote,
                    chain=chain_blob if isinstance(chain_blob, dict) else None,
                    target_strike=float(strike) if isinstance(strike, (int, float)) else None,
                    option_type=str(option_type or "").lower() or None,
                    position_type=str(position_type or "lotto"),
                )
                snap, patch = premium_evaluate(
                    PremiumEvaluationRequest(
                        position=position,
                        quote_context=ctx,
                        evaluated_at=datetime.now(timezone.utc),
                    )
                )
                sync = get_or_create_synchronizer(self.trading_system)
                apply_result = sync.apply_premium_state_update(self, snap, patch)
                if not getattr(apply_result, "applied", False):
                    log.error(
                        "PREMIUM_STATE_APPLY_FAILED | position_id=%s | reason=%s | "
                        "skip_legacy_valuation_fallback",
                        getattr(position, "position_id", ""),
                        getattr(apply_result, "reason", "unknown"),
                    )
                    # PE already evaluated — never fall through to a second economics path.
                    return True
                return True
            except Exception as prem_err:
                log.warning(
                    "PREMIUM_ENGINE | hydrate_failed | trade_id=%s | error=%s",
                    getattr(position, "position_id", ""),
                    prem_err,
                )
                return False

        # Group once per symbol per monitor cycle and fetch exactly one chain snapshot per symbol.
        positions_by_symbol: Dict[str, List[OptionsPosition]] = {}
        for position in open_positions:
            sym = str(getattr(position, "symbol", "") or "").strip().upper()
            if not sym:
                continue
            positions_by_symbol.setdefault(sym, []).append(position)

        for symbol, symbol_positions in positions_by_symbol.items():
            chain_expiry = ""
            distinct_expiries: set[str] = set()
            for position in symbol_positions:
                expiry = None
                if position.position_type == 'debit_spread' and position.debit_spread:
                    expiry = OptionsExitManager._nested_field(position.debit_spread, 'expiry')
                elif position.position_type == 'credit_spread' and position.credit_spread:
                    expiry = OptionsExitManager._nested_field(position.credit_spread, 'expiry')
                elif position.position_type == 'lotto' and position.lotto_contract:
                    expiry = OptionsExitManager._nested_field(position.lotto_contract, 'expiry')
                if not expiry:
                    md = getattr(position, "metadata", None)
                    if isinstance(md, dict):
                        norm = md.get("normalized_options")
                        if isinstance(norm, dict):
                            expiry = norm.get("expiration_ymd") or expiry
                exp_norm = _normalize_expiry(expiry)
                if exp_norm:
                    distinct_expiries.add(exp_norm)
                    if not chain_expiry:
                        chain_expiry = exp_norm

            if len(distinct_expiries) > 1:
                log.warning(
                    "OPTION_CHAIN_USAGE | symbol=%s | multiple_expiries=%s | action=use_first_expiry_only",
                    symbol,
                    ",".join(sorted(distinct_expiries)),
                )

            legs_expected = 0
            legs_resolved = 0
            quote_calls_saved = 0
            dedup_key = (str(symbol), str(chain_expiry or "0dte"), tick_key)
            reused_chain = dedup_key in cache_ref

            # When every open lotto on this underlying already has an exact OCC quote,
            # skip the full-chain fetch for this tick (unified monitor is primary).
            can_skip_chain_fetch = all(
                str(getattr(p, "position_type", "") or "") == "lotto"
                and _usable_exact_oq(p) is not None
                for p in symbol_positions
            )
            if can_skip_chain_fetch and not reused_chain:
                for position in symbol_positions:
                    try:
                        exact_oq = _usable_exact_oq(position)
                        if not isinstance(exact_oq, dict):
                            continue
                        option_type = None
                        strike = None
                        if position.lotto_contract:
                            _ot = OptionsExitManager._nested_field(
                                position.lotto_contract, "option_type", None
                            )
                            option_type = str(_ot).upper() if _ot is not None else None
                            strike = _to_float(
                                OptionsExitManager._nested_field(position.lotto_contract, "strike")
                            )
                        if option_type not in {"CALL", "PUT"} or not isinstance(strike, (int, float)):
                            md = getattr(position, "metadata", None)
                            norm = md.get("normalized_options") if isinstance(md, dict) else {}
                            legs = norm.get("legs") if isinstance(norm, dict) else None
                            if isinstance(legs, list) and legs and isinstance(legs[0], dict):
                                leg0 = legs[0]
                                osid = str(leg0.get("option_side") or "").lower()
                                option_type = "CALL" if osid == "call" else "PUT"
                                strike = _to_float(leg0.get("strike"))
                        if option_type not in {"CALL", "PUT"} or not isinstance(strike, (int, float)):
                            continue
                        applied = _apply_premium_engine_update(
                            position,
                            option_quote={
                                "bid": exact_oq.get("bid"),
                                "ask": exact_oq.get("ask"),
                                "mid": exact_oq.get("mid") or exact_oq.get("mark"),
                                "last": exact_oq.get("last"),
                                "source": "exact_quote",
                            },
                            option_type=option_type,
                            strike=float(strike),
                            position_type="lotto",
                            chain_blob=None,
                        )
                        if applied:
                            legs_resolved += 1
                            quote_calls_saved += 1
                    except Exception as exact_only_err:
                        log.debug(
                            "OPTION_EXACT_ONLY_APPLY_SKIP | symbol=%s | trade_id=%s | err=%s",
                            symbol,
                            getattr(position, "position_id", ""),
                            exact_only_err,
                        )
                log.info(
                    "OPTION_CHAIN_DEDUP | symbol=%s | reused=false | skipped_fetch=exact_quotes_complete | resolved=%d",
                    symbol,
                    legs_resolved,
                )
                continue

            if reused_chain:
                chain = cache_ref.get(dedup_key) or {}
            elif use_etrade_chain:
                chain_t0 = time.perf_counter()
                # Canonical same-day→nearest via OptionsChainManager (not raw SelectedED).
                chain = {}
                mgr = None
                try:
                    ts = getattr(self, "trading_system", None)
                    mgr = getattr(ts, "options_chain_manager", None) if ts is not None else None
                    if mgr is None:
                        dte0 = getattr(ts, "dte0_manager", None) if ts is not None else None
                        mgr = getattr(dte0, "options_chain_manager", None) if dte0 is not None else None
                except Exception:
                    mgr = None
                from easy0DTE.modules.options_chain_manager import fetch_0dte_intent_chain

                chain = await fetch_0dte_intent_chain(
                    mgr,
                    symbol,
                    system=ts,
                    expiry=chain_expiry or None,
                    use_cache=True,
                )
                if callable(getattr(self, "chain_latency_callback", None)):
                    try:
                        self.chain_latency_callback(
                            (time.perf_counter() - chain_t0) * 1000.0,
                            source="orb",
                        )
                    except Exception:
                        pass
                cache_ref[dedup_key] = chain or {}
            else:
                chain = cache_ref.get(dedup_key) or {}
                if not chain:
                    log.debug(
                        "OPTION_CHAIN_UPDATE_SKIP | symbol=%s | reason=hydrated_cache_miss",
                        symbol,
                    )
                    continue
            log.info(
                "OPTION_CHAIN_DEDUP | symbol=%s | reused=%s",
                symbol,
                str(bool(reused_chain)).lower(),
            )
            calls = chain.get("calls") if isinstance(chain, dict) else []
            puts = chain.get("puts") if isinstance(chain, dict) else []
            calls = calls if isinstance(calls, list) else []
            puts = puts if isinstance(puts, list) else []

            for position in symbol_positions:
                try:
                    current_value = None
                    option_type = None
                    strike = None
                    long_strike = None
                    short_strike = None

                    if position.position_type == 'debit_spread' and position.debit_spread:
                        ds = position.debit_spread
                        _ot = OptionsExitManager._nested_field(ds, 'option_type', None)
                        option_type = str(_ot).upper() if _ot is not None else None
                        long_strike = _to_float(OptionsExitManager._nested_field(ds, 'long_strike'))
                        short_strike = _to_float(OptionsExitManager._nested_field(ds, 'short_strike'))
                        legs_expected += 2
                        quote_calls_saved += 2
                    elif position.position_type == 'credit_spread' and position.credit_spread:
                        cs = position.credit_spread
                        _ot = OptionsExitManager._nested_field(cs, 'option_type', None)
                        option_type = str(_ot).upper() if _ot is not None else None
                        long_strike = _to_float(OptionsExitManager._nested_field(cs, 'long_strike'))
                        short_strike = _to_float(OptionsExitManager._nested_field(cs, 'short_strike'))
                        legs_expected += 2
                        quote_calls_saved += 2
                    elif position.position_type == 'lotto' and position.lotto_contract:
                        lc = position.lotto_contract
                        _ot = OptionsExitManager._nested_field(lc, 'option_type', None)
                        option_type = str(_ot).upper() if _ot is not None else None
                        strike = _to_float(OptionsExitManager._nested_field(lc, 'strike'))
                        legs_expected += 1
                        quote_calls_saved += 1
                    elif position.position_type == 'lotto':
                        md = getattr(position, "metadata", None)
                        norm = md.get("normalized_options") if isinstance(md, dict) else {}
                        legs = norm.get("legs") if isinstance(norm, dict) else None
                        if isinstance(legs, list) and legs and isinstance(legs[0], dict):
                            leg0 = legs[0]
                            osid = str(leg0.get("option_side") or "").lower()
                            option_type = "CALL" if osid == "call" else "PUT"
                            strike = _to_float(leg0.get("strike"))
                            legs_expected += 1
                            quote_calls_saved += 1

                    if option_type not in {"CALL", "PUT"}:
                        log.warning(f"   Position {position.position_id} missing option_type, skipping")
                        continue
                    contracts = calls if option_type == "CALL" else puts

                    if position.position_type == 'lotto' and isinstance(strike, (int, float)):
                        # Prefer exact-contract REST quote injected by unified monitor
                        exact_oq = _usable_exact_oq(position)
                        if isinstance(exact_oq, dict):
                            applied = _apply_premium_engine_update(
                                position,
                                option_quote={
                                    "bid": exact_oq.get("bid"),
                                    "ask": exact_oq.get("ask"),
                                    "mid": exact_oq.get("mid") or exact_oq.get("mark"),
                                    "last": exact_oq.get("last"),
                                    "source": "exact_quote",
                                },
                                option_type=option_type,
                                strike=float(strike),
                                position_type="lotto",
                                chain_blob=None,  # do not fall back to aged chain for HWM
                            )
                            if applied:
                                continue
                        quote = _extract_contract_quote(contracts, float(strike), leg_role="long")
                        if quote is not None and quote.get("executable") is not None:
                            current_value = float(quote["executable"])
                            legs_resolved += 1
                            src = str(quote.get("source") or "chain_strike_match")
                            # Force-refreshed chain matches are tagged for exit-grade bridge.
                            # Never emit bare ``exact`` — that alias meant chain strike match.
                            if src in ("exact", "chain_strike_match"):
                                src = "fresh_chain_refresh"
                            applied = _apply_premium_engine_update(
                                position,
                                option_quote={
                                    "bid": quote.get("bid"),
                                    "ask": quote.get("ask"),
                                    "mid": quote.get("mid"),
                                    "last": quote.get("last"),
                                    "source": src,
                                },
                                option_type=option_type,
                                strike=float(strike),
                                position_type="lotto",
                                chain_blob=chain if isinstance(chain, dict) else None,
                            )
                            if applied:
                                continue
                        leg_price = _extract_contract_price(contracts, float(strike), leg_role="long")
                        if leg_price is not None:
                            current_value = float(leg_price)
                            legs_resolved += 1

                    elif (
                        position.position_type == 'debit_spread'
                        and isinstance(long_strike, (int, float))
                        and isinstance(short_strike, (int, float))
                    ):
                        long_quote = _extract_contract_quote(contracts, float(long_strike), leg_role="long")
                        short_quote = _extract_contract_quote(contracts, float(short_strike), leg_role="short")
                        long_value = (long_quote or {}).get("executable")
                        short_value = (short_quote or {}).get("executable")
                        if long_value is None:
                            long_value = _extract_contract_price(contracts, float(long_strike), leg_role="long")
                        if short_value is None:
                            short_value = _extract_contract_price(contracts, float(short_strike), leg_role="short")
                        if long_value is not None:
                            legs_resolved += 1
                        if short_value is not None:
                            legs_resolved += 1
                        if long_value is not None and short_value is not None:
                            current_value = float(long_value - short_value)
                            # Executable net for debit close ≈ long bid − short ask
                            lb = (long_quote or {}).get("bid")
                            sa = (short_quote or {}).get("ask")
                            _apply_premium_engine_update(
                                position,
                                option_quote={
                                    "bid": lb if lb is not None else current_value,
                                    "ask": sa,
                                    "mid": current_value,
                                    "last": current_value,
                                    "source": "spread_mtm_mid_last",
                                },
                                option_type=option_type,
                                strike=float(long_strike),
                                position_type="debit_spread",
                                chain_blob=chain if isinstance(chain, dict) else None,
                            )
                            continue

                    elif (
                        position.position_type == 'credit_spread'
                        and isinstance(long_strike, (int, float))
                        and isinstance(short_strike, (int, float))
                    ):
                        short_quote = _extract_contract_quote(contracts, float(short_strike), leg_role="short")
                        long_quote = _extract_contract_quote(contracts, float(long_strike), leg_role="long")
                        short_value = (short_quote or {}).get("executable")
                        long_value = (long_quote or {}).get("executable")
                        if short_value is None:
                            short_value = _extract_contract_price(contracts, float(short_strike), leg_role="short")
                        if long_value is None:
                            long_value = _extract_contract_price(contracts, float(long_strike), leg_role="long")
                        if short_value is not None:
                            legs_resolved += 1
                        if long_value is not None:
                            legs_resolved += 1
                        if long_value is not None and short_value is not None:
                            current_value = float(short_value - long_value)
                            sa = (short_quote or {}).get("ask")
                            lb = (long_quote or {}).get("bid")
                            _apply_premium_engine_update(
                                position,
                                option_quote={
                                    "bid": lb,
                                    "ask": sa if sa is not None else current_value,
                                    "mid": current_value,
                                    "last": current_value,
                                    "source": "spread_mtm_mid_last",
                                },
                                option_type=option_type,
                                strike=float(short_strike),
                                position_type="credit_spread",
                                chain_blob=chain if isinstance(chain, dict) else None,
                            )
                            continue

                    if current_value is not None and current_value > 0:
                        await self.update_position_value(position.position_id, current_value)
                    else:
                        log.warning(f"   Could not resolve chain quote for position {position.position_id}, keeping existing value")
                except Exception as e:
                    log.error(f"Error updating position {position.position_id} with real prices: {e}")
                    continue

            legs_resolved_from_chain = bool(legs_expected > 0 and legs_resolved == legs_expected)
            log.info(
                "OPTION_CHAIN_USAGE | symbol=%s | legs_resolved_from_chain=%s | quote_calls_saved=%d",
                symbol,
                str(legs_resolved_from_chain).lower(),
                int(quote_calls_saved),
            )
            log.info(
                "OPTION_CHAIN_ONLY_MODE | symbol=%s | chain_used=true | quote_calls_blocked=true",
                symbol,
            )

        log.debug(f"✅ Updated positions with real-time options prices")
    
    async def auto_partial_profit(
        self,
        position_id: str,
        target_name: str = 'first_target_60pct'
    ) -> Optional[Dict[str, Any]]:
        """Removed — partial TP ladder deleted. Winner exit is stealth ``take_profit``."""
        return None
    
    def get_open_positions(self) -> List[OptionsPosition]:
        """Get all open positions"""
        # Mock fill path: source of truth is mock executor state.
        if self._use_mock_fills():
            return self.mock_executor.get_open_positions()
        # Include any non-closed book row so fresh LIVE fills stay visible to monitors
        # even if a broker adapter sets a non-canonical status string.
        out: List[OptionsPosition] = []
        for p in self.positions.values():
            st = str(getattr(p, "status", "") or "").strip().lower()
            if st and st != "closed":
                out.append(p)
        return out
    
    def get_position(self, position_id: str) -> Optional[OptionsPosition]:
        """Get position by ID"""
        if self._use_mock_fills():
            pos = self.mock_executor.get_position(position_id)
            if pos:
                return pos
        return self.positions.get(position_id)
    
    async def monitor_positions(
        self,
        market_data_provider: Optional[Any] = None,
        orb_data_provider: Optional[Any] = None
    ) -> List[ExitSignal]:
        """
        Monitor all open positions and check exit conditions
        
        Args:
            market_data_provider: Function/provider to get current market data
            orb_data_provider: Function/provider to get ORB data
            
        Returns:
            List of ExitSignal objects for positions that should be closed
        """
        exit_signals = []
        open_positions = self.get_open_positions()
        
        if not open_positions:
            return exit_signals
        
        log.warning(
            "OPTIONS_STEALTH_FAILSAFE | stage=legacy_monitor_positions_invoked | "
            "mode=fallback_only | open_positions=%d",
            len(open_positions),
        )
        
        for position in open_positions:
            try:
                # Get current market data
                if market_data_provider:
                    market_data = await market_data_provider(position.symbol)
                else:
                    # Fallback: Use position current_value
                    market_data = {
                        'current_price': position.current_value,
                        'current_value': position.current_value,
                        'vwap': None,
                        'bid_ask_spread_pct': 0.0,
                        'momentum': 0.0
                    }

                # Keep tracked position value current before evaluating exits.
                latest_value = market_data.get('current_value') if isinstance(market_data, dict) else None
                try:
                    latest_value = float(latest_value) if latest_value is not None else None
                except (TypeError, ValueError):
                    latest_value = None
                if latest_value is not None and latest_value > 0:
                    if self._use_mock_fills():
                        await self.mock_executor.update_position_value(position.position_id, latest_value)
                    else:
                        await self.update_position_value(position.position_id, latest_value)
                
                # Get ORB data
                orb_data = None
                if orb_data_provider:
                    orb_data = await orb_data_provider(position.symbol)
                
                exit_signal = self.exit_manager.evaluate_emergency_only(
                    position=position,
                    current_value=position.current_value,
                    market_data=market_data,
                    stale_quote=False,
                )
                
                if exit_signal:
                    exit_signals.append(exit_signal)
                    log.info(f"✅ Exit signal generated for position {position.position_id}: {exit_signal.reason}")
                    
                    # Record trade performance for Priority Optimizer
                    if self.priority_collector:
                        try:
                            # Calculate peak values (use current value as peak if higher)
                            peak_value = max(position.current_value, position.entry_price)
                            if position.position_type == 'credit_spread':
                                peak_pnl_pct = (position.entry_price - peak_value) / position.entry_price if position.entry_price > 0 else 0.0
                            else:
                                peak_pnl_pct = (peak_value - position.entry_price) / position.entry_price if position.entry_price > 0 else 0.0
                            
                            self.priority_collector.record_trade_performance(
                                position=position,
                                exit_signal=exit_signal,
                                peak_value=peak_value,
                                peak_pnl_pct=peak_pnl_pct
                            )
                        except Exception as e:
                            log.error(f"Failed to record trade performance: {e}")
                    
            except Exception as e:
                log.error(f"Error monitoring position {position.position_id}: {e}", exc_info=True)
        
        return exit_signals

    def _idempotent_closed_position_stub(self, position_id: str, *, symbol: str = "") -> Any:
        from types import SimpleNamespace

        stub = SimpleNamespace(
            position_id=str(position_id or ""),
            symbol=str(symbol or ""),
            status="closed",
            broker_order_id="",
            realized_pnl=0.0,
            metadata=None,
            _idempotent_replay=True,
        )

        def _to_dict() -> Dict[str, Any]:
            return {
                "position_id": stub.position_id,
                "symbol": stub.symbol,
                "status": "closed",
            }

        stub.to_dict = _to_dict  # type: ignore[attr-defined]
        return stub

    async def close_position_with_data(
        self,
        *,
        position_id: str,
        exit_price: float,
        exit_reason: str,
        exit_time: Optional[datetime] = None,
        details: Optional[Dict[str, Any]] = None,
        external_position_data: Optional[Dict[str, Any]] = None,
        suppress_exit_alert: bool = False,
    ) -> Optional[Any]:
        """Unified close path for both ORB1 Breakout and Trendline 0DTE."""
        pid = str(position_id or "").strip()
        if self._use_mock_fills() and self.mock_executor and pid:
            is_closed_fn = getattr(self.mock_executor, "is_position_closed", None)
            if callable(is_closed_fn) and is_closed_fn(pid):
                sym_idem = ""
                if isinstance(external_position_data, dict):
                    sym_idem = str(external_position_data.get("symbol") or "")
                log.info(
                    "OPTIONS_CLOSE_IDEMPOTENT | trade_id=%s | symbol=%s | reason=already_in_closed_ledger",
                    pid,
                    sym_idem,
                )
                return self._idempotent_closed_position_stub(pid, symbol=sym_idem)
        position = self.get_position(position_id)
        if not position and isinstance(external_position_data, dict) and self.mock_executor:
            position = self.mock_executor.get_position(position_id)
        if not position and not isinstance(external_position_data, dict):
            return None
        entry_price = float(
            (getattr(position, "entry_price", 0.0) if position else external_position_data.get("entry_price", 0.0)) or 0.0
        )
        qty = int(max(1, int((getattr(position, "quantity", 1) if position else external_position_data.get("quantity", 1)) or 1)))
        structural_type = None
        if position:
            structural_type = getattr(position, "normalized_position_type", None)
        if not structural_type and isinstance(external_position_data, dict):
            structural_type = external_position_data.get("position_type")
        structural_key = str(structural_type or "").strip().lower()
        ex = float(exit_price)
        if structural_key == "credit_spread":
            pnl_pct = ((entry_price - ex) / entry_price) if entry_price > 0 else 0.0
            pnl_dollar = (entry_price - ex) * qty * 100.0
        else:
            pnl_pct = ((ex - entry_price) / entry_price) if entry_price > 0 else 0.0
            pnl_dollar = (ex - entry_price) * qty * 100.0
        log.info(
            "OPTIONS_CLOSE_PNL_AUDIT | position_id=%s | position_type=%s | entry_price=%.6f | exit_price=%.6f | "
            "pnl_pct=%.6f | pnl_dollar=%.2f",
            position_id,
            structural_key or str(structural_type or "unknown"),
            entry_price,
            ex,
            float(pnl_pct),
            float(pnl_dollar),
        )
        try:
            from modules.execution_telemetry import log_option_exit_diagnosis
        except ImportError:
            from execution_telemetry import log_option_exit_diagnosis  # type: ignore[no-redef]
        strat = "unknown"
        if isinstance(external_position_data, dict):
            strat = str(external_position_data.get("strategy_type") or "unknown")
        elif position is not None:
            strat = str(getattr(position, "strategy_type", None) or getattr(position, "strategy_name", None) or "unknown")
        log_option_exit_diagnosis(
            stage="executor_close",
            strategy=strat,
            trade_id=str(position_id or ""),
            symbol=str(
                (getattr(position, "symbol", "") if position else external_position_data.get("symbol", ""))
                if isinstance(external_position_data, dict) or position
                else ""
            ),
            reason=str(exit_reason or ""),
            pnl_pct=float(pnl_pct),
            pnl_dollar=float(pnl_dollar),
            exit_premium=float(ex),
            close_ok=True,
        )
        signal_time = exit_time or datetime.now(timezone.utc)
        if not position and isinstance(external_position_data, dict):
            sym = str(external_position_data.get("symbol") or "")
            if not self._runtime_demo_mode():
                err = "live_close_missing_executor_position"
                log.error(
                    "LIVE_CLOSE_FAIL_CLOSED | external_only | trade_id=%s | symbol=%s",
                    position_id,
                    sym,
                )
                await self._emit_close_escalation_alert(
                    position_id=position_id,
                    symbol=sym,
                    exit_reason=str(exit_reason or ""),
                    error=err,
                )
                return None
            result = {
                "position_id": position_id,
                "exit_price": float(exit_price),
                "exit_reason": str(exit_reason),
                "exit_time": signal_time,
                "entry_price": entry_price,
                "quantity": qty,
                "pnl_pct": float(pnl_pct),
                "pnl_dollar": float(pnl_dollar),
                "details": details or {},
                "external": True,
            }
            if self._runtime_demo_mode() and self.mock_executor:
                mock = self.mock_executor
                active = getattr(mock, "active_positions", None) or {}
                if pid in active:
                    signal = ExitSignal(
                        position_id=pid,
                        reason=ExitReason.FAIL_SAFE.value,
                        exit_price=float(exit_price),
                        exit_time=signal_time,
                        pnl_pct=float(pnl_pct),
                        pnl_dollar=float(pnl_dollar),
                        details=details or {},
                    )
                    closed_ext = await self.execute_exit(
                        signal,
                        exit_reason_override=exit_reason,
                        suppress_exit_alert=bool(suppress_exit_alert),
                    )
                    if closed_ext:
                        return closed_ext
                try:
                    from types import SimpleNamespace

                    from modules.live_options_ledger import persist_options_close_from_position

                    ext_meta = external_position_data.get("metadata")
                    if not isinstance(ext_meta, dict):
                        ext_meta = {}
                    ext_meta = dict(ext_meta)
                    ext_meta["demo_ledger_close"] = True
                    stub = SimpleNamespace(
                        position_id=pid,
                        symbol=sym,
                        strategy_type=str(
                            external_position_data.get("strategy_type") or "trendline_0dte"
                        ),
                        realized_pnl=float(pnl_dollar),
                        quantity=qty,
                        entry_price=entry_price,
                        entry_time=signal_time,
                        position_type=str(external_position_data.get("position_type") or "lotto"),
                        metadata=ext_meta,
                        lotto_contract=external_position_data.get("lotto_contract"),
                    )
                    if hasattr(mock, "record_external_closed_position"):
                        mock.record_external_closed_position(
                            stub,
                            exit_price=float(ex),
                            exit_reason=str(exit_reason),
                        )
                    persist_options_close_from_position(
                        stub,
                        exit_reason=str(exit_reason),
                        filled_exit_price=float(ex),
                        mock_executor=mock,
                    )
                except Exception as ledger_err:
                    log.warning(
                        "DEMO_OPTIONS_LEDGER | external_mock_close_persist_failed | trade_id=%s | %s",
                        pid,
                        ledger_err,
                    )
            return result
        signal = ExitSignal(
            position_id=position_id,
            reason=ExitReason.FAIL_SAFE.value,
            exit_price=float(exit_price),
            exit_time=signal_time,
            pnl_pct=float(pnl_pct),
            pnl_dollar=float(pnl_dollar),
            details=details or {},
        )
        return await self.execute_exit(
            signal,
            exit_reason_override=exit_reason,
            suppress_exit_alert=bool(suppress_exit_alert),
        )

    async def _emit_close_escalation_alert(
        self,
        *,
        position_id: str,
        symbol: str,
        exit_reason: str,
        error: str,
    ) -> None:
        """Escalate when a close cannot be confirmed (demo ledger or live broker)."""
        if not self.alert_manager:
            return
        try:
            from modules.prime_alert_manager import (
                AlertLevel,
                _alert_mode_header_line,
                _telegram_html_escape,
            )
        except ImportError:
            from prime_alert_manager import (  # type: ignore
                AlertLevel,
                _alert_mode_header_line,
                _telegram_html_escape,
            )
        try:
            from modules.runtime_mode import is_demo_mode
        except ImportError:
            from runtime_mode import is_demo_mode  # type: ignore
        settlement = (
            "demo simulated ledger"
            if is_demo_mode()
            else "broker confirmation"
        )
        ts = datetime.now().strftime("%H:%M:%S")
        sym_esc = _telegram_html_escape(symbol)
        pid_esc = _telegram_html_escape(position_id)
        reason_esc = _telegram_html_escape(exit_reason)
        error_esc = _telegram_html_escape(error)
        message = (
            f"ℹ️ <b>CLOSE FAILED — {sym_esc}</b>\n"
            f"{_alert_mode_header_line(None)}\n"
            f"🕐 {ts}\n\n"
            f"Position {pid_esc} could not be closed with {settlement}.\n"
            f"Reason: {reason_esc}\n"
            f"Error: {error_esc}\n"
            f"Action: manual reconciliation required."
        )
        try:
            send_fn = getattr(self.alert_manager, "_send_telegram_message", None)
            if callable(send_fn):
                await send_fn(message, AlertLevel.INFO)
                return
            await self.alert_manager.send_system_alert(
                title=f"CLOSE FAILED — {symbol}",
                message=message,
            )
        except Exception as exc:
            log.error("Failed to send close escalation alert: %s", exc)
    
    async def execute_exit(
        self,
        exit_signal: ExitSignal,
        exit_reason_override: Optional[str] = None,
        *,
        suppress_exit_alert: bool = False,
    ) -> Optional[OptionsPosition]:
        """
        Execute exit for a position based on exit signal
        
        Args:
            exit_signal: ExitSignal object
            exit_reason_override: Override exit reason string
            
        Returns:
            Closed OptionsPosition or None if execution failed
        """
        position_id = exit_signal.position_id

        position = self.positions.get(position_id)
        if position is None and self.mock_executor:
            position = self.mock_executor.get_position(position_id)
        if position is None:
            log.warning(f"Position {position_id} not found for exit")
            return None

        use_mock_close = self._should_use_mock_close(position_id, position)
        exit_reason_str = exit_reason_override or exit_signal.reason

        # Close position
        if use_mock_close:
            active = getattr(self.mock_executor, "active_positions", None) or {}
            if position_id in active:
                closed_position = await self.mock_executor.close_position(
                    position_id=position_id,
                    exit_price=exit_signal.exit_price,
                    reason=exit_reason_str,
                )
            else:
                closed_position = await self._demo_ledger_close_external(
                    position,
                    exit_signal,
                    exit_reason=exit_reason_str,
                )

            if closed_position:
                self._remove_registry_position(position_id)

                log.info(
                    "✅ Position %s closed via demo ledger: %s",
                    position_id,
                    exit_reason_str,
                )
                log.info(
                    "   P&L: %s%% ($%.2f)",
                    exit_signal.pnl_pct * 100.0,
                    exit_signal.pnl_dollar,
                )
                await self._send_position_exit_alert(
                    closed_position=closed_position,
                    position=position,
                    exit_signal=exit_signal,
                    exit_reason=exit_reason_str,
                    suppress_exit_alert=suppress_exit_alert,
                )
                self._record_unified_day_pnl_after_close(
                    float(getattr(closed_position, "realized_pnl", exit_signal.pnl_dollar) or 0.0)
                )
                return closed_position

        elif self._use_broker_fills():
            if self._runtime_demo_mode():
                log.error(
                    "DEMO_MODE_BROKER_CLOSE_BLOCKED | position_id=%s | symbol=%s",
                    position_id,
                    position.symbol,
                )
                closed_position = await self._demo_ledger_close_external(
                    position,
                    exit_signal,
                    exit_reason=exit_reason_str,
                )
                if closed_position:
                    self._remove_registry_position(position_id)
                    await self._send_position_exit_alert(
                        closed_position=closed_position,
                        position=position,
                        exit_signal=exit_signal,
                        exit_reason=exit_reason_str,
                        suppress_exit_alert=suppress_exit_alert,
                    )
                    return closed_position
                await self._emit_close_escalation_alert(
                    position_id=position_id,
                    symbol=position.symbol,
                    exit_reason=exit_reason_str,
                    error="demo_ledger_close_failed",
                )
                return None
            # Live: refuse duplicate STC while a prior close order is still open.
            try:
                from modules.exit_latch import should_defer_broker_dispatch, mark_dispatch_started

                if should_defer_broker_dispatch(position, ownership_gate_active=False):
                    meta = getattr(position, "metadata", None) if isinstance(getattr(position, "metadata", None), dict) else {}
                    pc = meta.get("pending_close") if isinstance(meta.get("pending_close"), dict) else {}
                    log.warning(
                        "LIVE_CLOSE_DEDUPED | position_id=%s | symbol=%s | broker_order_id=%s | "
                        "reason=inflight_before_submit",
                        position_id,
                        position.symbol,
                        pc.get("broker_order_id"),
                    )
                    # Do not place a new order; return None so pending_close_retry keeps the latch.
                    return None
                mark_dispatch_started(position)
            except Exception:
                pass
            adapter = self.broker_adapter
            if adapter is None or not getattr(adapter, "is_available", lambda: False)() or BrokerCloseRequest is None:
                err = "broker_adapter_unavailable"
                _live_fail_closed(err, path="execute_exit", symbol=position.symbol)
                await self._emit_close_escalation_alert(
                    position_id=position_id,
                    symbol=position.symbol,
                    exit_reason=exit_reason_override or exit_signal.reason,
                    error=err,
                )
                return None
            try:
                if not self._ensure_lotto_contract_for_close(position):
                    err = "close_missing_option_contract"
                    _live_fail_closed(err, path="execute_exit", symbol=position.symbol)
                    await self._emit_close_escalation_alert(
                        position_id=position_id,
                        symbol=position.symbol,
                        exit_reason=exit_reason_str,
                        error=err,
                    )
                    return None
                log.info("💰 LIVE: Closing position %s via broker adapter", position_id)
                rs = exit_reason_str
                plan = resolve_options_exit_plan(
                    exit_reason_override,
                    exit_enum_value=exit_signal.reason,
                    details=exit_signal.details,
                )
                try:
                    max_attempts = max(1, int(get_config_value("LIVE_CLOSE_MAX_ATTEMPTS", 3)))
                except (TypeError, ValueError):
                    max_attempts = 3
                try:
                    retry_interval = max(0.1, float(get_config_value("LIVE_CLOSE_RETRY_INTERVAL_SEC", 0.75)))
                except (TypeError, ValueError):
                    retry_interval = 0.75
                try:
                    retry_backoff = max(1.0, float(get_config_value("LIVE_CLOSE_RETRY_BACKOFF_MULTIPLIER", 1.5)))
                except (TypeError, ValueError):
                    retry_backoff = 1.5

                last_err = "close_fill_not_confirmed"
                close_result = None
                broker_ot = "LIMIT"
                prior_status = ""
                previously_filled = 0
                owned_qty = int(position.quantity or 1)
                meta_live = getattr(position, "metadata", None) if isinstance(getattr(position, "metadata", None), dict) else {}
                if isinstance(meta_live.get("pending_close"), dict):
                    prior_status = str(meta_live["pending_close"].get("broker_order_status") or "")
                    previously_filled = int(meta_live["pending_close"].get("filled_quantity") or 0)
                if str(meta_live.get("close_status") or "").upper() == "STATUS_UNKNOWN":
                    log.warning(
                        "LIVE_CLOSE_RECONCILE_REQUIRED | position_id=%s | status=STATUS_UNKNOWN | "
                        "no_immediate_retry=true",
                        position_id,
                    )
                    # Ambiguous timeout: reconcile only — do not submit another STC yet.
                    return None
                for attempt in range(1, max_attempts + 1):
                    from modules.live_close_attempt import (
                        LiveCloseAttemptRecord,
                        may_escalate_to_market,
                        remaining_close_quantity,
                        record_live_close_attempt,
                        should_submit_close_attempt,
                        mark_close_status_unknown,
                    )
                    from modules.canonical_closed_trade import resolve_close_lifecycle_id

                    rem_qty = remaining_close_quantity(
                        owned_quantity=owned_qty,
                        previously_filled=previously_filled,
                        requested=owned_qty,
                    )
                    allow, allow_reason = should_submit_close_attempt(
                        prior_order_status=prior_status,
                        remaining_qty=rem_qty,
                        ownership_confirmed=True,
                    )
                    if not allow:
                        log.warning(
                            "LIVE_CLOSE_SUBMIT_BLOCKED | position_id=%s | attempt=%d | reason=%s | prior_status=%s",
                            position_id,
                            attempt,
                            allow_reason,
                            prior_status,
                        )
                        if allow_reason == "prior_order_working":
                            return None
                        if allow_reason == "prior_order_unknown_reconcile_first":
                            mark_close_status_unknown(position, reason="prior_unknown")
                            return None
                    if allow_reason in ("prior_order_filled", "nothing_remaining"):
                        break
                    ot = plan.order_type
                    ex_px = float(exit_signal.exit_price)
                    allow_market_fallback_now = bool(plan.allow_market_fallback) and (
                        str(plan.exit_urgency or "").upper() == "URGENT"
                        or attempt >= max_attempts
                    )
                    use_market = may_escalate_to_market(
                        attempt_number=attempt,
                        prior_order_status=prior_status,
                        ownership_confirmed=True,
                        remaining_qty=rem_qty,
                        policy_allows_market=allow_market_fallback_now,
                    )
                    if use_market:
                        ot = "MARKET"
                        ex_px = None
                    elif smart_execution_enabled() and position.position_type == "lotto" and position.lotto_contract:
                        lc = position.lotto_contract
                        ok_ll, sp_ll = last_look_option_spread_ok(
                            float(lc.bid or 0.0),
                            float(lc.ask or 0.0),
                            float(lc.mid_price or 0.0),
                        )
                        if not ok_ll and plan.allow_market_fallback and attempt >= 2 and prior_status:
                            # Only after reconciling a prior terminal/retryable status
                            if may_escalate_to_market(
                                attempt_number=attempt,
                                prior_order_status=prior_status or "REJECTED",
                                ownership_confirmed=True,
                                remaining_qty=rem_qty,
                                policy_allows_market=allow_market_fallback_now,
                            ):
                                ot = "MARKET"
                                ex_px = None
                    broker_ot = "MARKET" if str(ot).upper() == "MARKET" else "LIMIT"
                    broker_px = None if broker_ot == "MARKET" else ex_px
                    close_req = BrokerCloseRequest(
                        position_id=position_id,
                        symbol=position.symbol,
                        quantity=int(rem_qty or position.quantity or 1),
                        exit_price=broker_px,
                        order_type=broker_ot,
                        strategy=str(position.strategy_type or "ORB_0DTE"),
                        position=position,
                    )
                    decision_bid = float(exit_signal.exit_price or 0.0)
                    cur_bid = decision_bid
                    cur_ask = None
                    if position.lotto_contract:
                        cur_bid = float(getattr(position.lotto_contract, "bid", 0.0) or decision_bid or 0.0)
                        cur_ask = float(getattr(position.lotto_contract, "ask", 0.0) or 0.0) or None
                    latch_meta = meta_live.get("exit_latch") if isinstance(meta_live.get("exit_latch"), dict) else {}
                    elapsed_latch = None
                    if latch_meta.get("exit_latched_at"):
                        try:
                            elapsed_latch = max(0.0, (time.time() - float(latch_meta["exit_latched_at"])) * 1000.0)
                        except (TypeError, ValueError):
                            elapsed_latch = None
                    attempt_rec = LiveCloseAttemptRecord(
                        trade_id=str(position_id),
                        close_lifecycle_id=resolve_close_lifecycle_id(position),
                        attempt_number=attempt,
                        attempt_started_at=time.time(),
                        order_type=broker_ot,
                        limit_price=broker_px,
                        decision_bid=decision_bid,
                        current_bid=cur_bid,
                        current_ask=cur_ask,
                        spread=(float(cur_ask) - float(cur_bid)) if cur_ask is not None and cur_bid is not None else None,
                        requested_quantity=int(rem_qty or 1),
                        last_confirmed_owned_quantity=owned_qty,
                        previously_filled_quantity=previously_filled,
                        remaining_quantity=int(rem_qty or 0),
                        exit_reason=rs,
                    )
                    log.info(
                        "LIVE_CLOSE_ATTEMPT | position_id=%s | symbol=%s | attempt=%d/%d | order_type=%s | reason=%s | qty=%s",
                        position_id,
                        position.symbol,
                        attempt,
                        max_attempts,
                        broker_ot,
                        rs,
                        rem_qty,
                    )
                    try:
                        close_result = await adapter.submit_close_order(close_req)
                    except asyncio.TimeoutError:
                        mark_close_status_unknown(position, reason="submit_timeout")
                        attempt_rec.broker_submit_result = "timeout"
                        attempt_rec.broker_order_status = "STATUS_UNKNOWN"
                        record_live_close_attempt(position, attempt_rec)
                        return None
                    except Exception as submit_err:
                        err_s = str(submit_err).lower()
                        if "timeout" in err_s:
                            mark_close_status_unknown(position, reason="submit_timeout")
                            attempt_rec.broker_submit_result = "timeout"
                            attempt_rec.broker_order_status = "STATUS_UNKNOWN"
                            record_live_close_attempt(position, attempt_rec)
                            return None
                        raise
                    try:
                        from modules.exit_latch import mark_close_order_submitted

                        if close_result and getattr(close_result, "success", False):
                            mark_close_order_submitted(
                                position,
                                broker_order_id=str(getattr(close_result, "order_id", "") or ""),
                            )
                    except Exception:
                        pass
                    attempt_rec.broker_submit_result = "success" if getattr(close_result, "success", False) else "failed"
                    attempt_rec.broker_order_id = str(getattr(close_result, "order_id", "") or "")
                    attempt_rec.broker_order_status = str(getattr(close_result, "status", "") or "")
                    attempt_rec.fill_quantity = int(getattr(close_result, "filled_quantity", 0) or 0)
                    attempt_rec.fill_price = getattr(close_result, "average_fill_price", None)
                    attempt_rec.elapsed_since_latch_ms = elapsed_latch
                    record_live_close_attempt(position, attempt_rec)
                    prior_status = attempt_rec.broker_order_status or prior_status
                    if close_result.success and close_result.fill_confirmed:
                        break
                    last_err = close_result.error or "close_fill_not_confirmed"
                    log.warning(
                        "LIVE_CLOSE_ATTEMPT_FAILED | position_id=%s | symbol=%s | attempt=%d/%d | error=%s | status=%s",
                        position_id,
                        position.symbol,
                        attempt,
                        max_attempts,
                        last_err,
                        str(getattr(close_result, "status", "") or ""),
                    )
                    if attempt < max_attempts:
                        wait_sec = retry_interval * (retry_backoff ** (attempt - 1))
                        await asyncio.sleep(wait_sec)

                if not close_result or not close_result.success or not close_result.fill_confirmed:
                    err = last_err
                    _live_fail_closed(err, path="execute_exit", symbol=position.symbol)
                    await self._emit_close_escalation_alert(
                        position_id=position_id,
                        symbol=position.symbol,
                        exit_reason=rs,
                        error=err,
                    )
                    return None
                exit_fill_px = float(
                    close_result.average_fill_price or exit_signal.exit_price
                )
                exit_bid_signal = float(exit_signal.exit_price or 0.0)
                exit_mid = exit_bid_signal
                if exit_bid_signal <= 0.0 and position.lotto_contract:
                    lc = position.lotto_contract
                    exit_bid_signal = float(getattr(lc, "bid", 0.0) or getattr(lc, "mid_price", 0.0) or 0.0)
                    exit_mid = float(getattr(lc, "mid_price", 0.0) or 0.0)
                entry_px = float(position.entry_price or 0.0)
                qty_close = int(max(1, int(position.quantity or 1)))
                structural = str(
                    getattr(position, "normalized_position_type", "") or position.position_type or ""
                ).strip().lower()
                if structural == "credit_spread":
                    fill_pnl_dollar = (entry_px - exit_fill_px) * qty_close * 100.0
                    fill_pnl_pct = ((entry_px - exit_fill_px) / entry_px) if entry_px > 0 else 0.0
                else:
                    fill_pnl_dollar = (exit_fill_px - entry_px) * qty_close * 100.0
                    fill_pnl_pct = ((exit_fill_px - entry_px) / entry_px) if entry_px > 0 else 0.0
                exit_signal.pnl_dollar = float(fill_pnl_dollar)
                exit_signal.pnl_pct = float(fill_pnl_pct)
                exit_signal.exit_price = float(exit_fill_px)
                exit_slip_mid = None
                exit_slip_bid = None
                slip_cap = 0.08
                slip_cap_exceeded = False
                try:
                    from modules.execution_telemetry import (
                        entry_slippage_vs_mid_pct as _exit_slip_mid_fn,
                        exit_slippage_vs_bid_pct,
                        resolve_exit_max_slippage_vs_bid_pct,
                    )

                    exit_slip_mid = _exit_slip_mid_fn("SELL", exit_mid, exit_fill_px)
                    exit_slip_bid = exit_slippage_vs_bid_pct(
                        exit_bid_signal if exit_bid_signal > 0 else None,
                        exit_fill_px,
                    )
                    slip_cap = float(
                        resolve_exit_max_slippage_vs_bid_pct(str(position.strategy_type or "ORB_0DTE"))
                    )
                    slip_cap_exceeded = bool(
                        exit_slip_bid is not None and slip_cap > 0 and exit_slip_bid > slip_cap + 1e-9
                    )
                except ImportError:
                    if entry_slippage_vs_mid_pct:
                        exit_slip_mid = entry_slippage_vs_mid_pct("SELL", exit_mid, exit_fill_px)
                if slip_cap_exceeded and log_execution_event and build_execution_payload:
                    log_execution_event(
                        "EXECUTION_EXIT_SLIPPAGE_CAP_EXCEEDED",
                        str(position.strategy_type or "ORB_0DTE"),
                        build_execution_payload(
                            symbol=position.symbol,
                            trade_id=position_id,
                            strategy=str(position.strategy_type or "ORB_0DTE"),
                            fill_price=exit_fill_px,
                            quote_bid=exit_bid_signal if exit_bid_signal > 0 else None,
                            quote_mid=exit_mid if exit_mid > 0 else None,
                            extra={
                                "exit_slippage_vs_bid_pct": exit_slip_bid,
                                "exit_slippage_cap_pct": slip_cap,
                                "close_order_type": broker_ot,
                                "broker_order_id": close_result.order_id,
                            },
                        ),
                    )
                if log_execution_event and build_execution_payload:
                    log_execution_event(
                        "EXECUTION_FILL_SUMMARY",
                        str(position.strategy_type or "ORB_0DTE"),
                        build_execution_payload(
                            symbol=position.symbol,
                            trade_id=position_id,
                            strategy=str(position.strategy_type or "ORB_0DTE"),
                            order_type=str(broker_ot),
                            fill_ts=datetime.now(timezone.utc).isoformat(),
                            fill_price=exit_fill_px,
                            exit_reason=str(rs),
                            exit_urgency=plan.exit_urgency,
                            exit_execution_style=plan.exit_execution_style,
                            extra={
                                "path": "options_close_broker_confirmed",
                                "broker_order_id": close_result.order_id,
                                "exit_slippage_vs_bid_pct": exit_slip_bid,
                                "exit_slippage_cap_exceeded": slip_cap_exceeded,
                            },
                        ),
                    )
                meta = dict(getattr(position, "metadata", None) or {})
                meta["broker_close_confirmed"] = True
                meta["broker_close_order_id"] = close_result.order_id
                meta["exit_execution_quality"] = {
                    "exit_bid_signal": exit_bid_signal if exit_bid_signal > 0 else None,
                    "exit_mid": exit_mid if exit_mid > 0 else None,
                    "exit_fill_price": exit_fill_px,
                    "exit_slippage_vs_mid_pct": exit_slip_mid,
                    "exit_slippage_vs_bid_pct": exit_slip_bid,
                    "exit_slippage_cap_pct": slip_cap,
                    "exit_slippage_cap_exceeded": slip_cap_exceeded,
                    "close_order_type": broker_ot,
                    "exit_price_source": "LIVE_BID",
                }
                position.metadata = meta
                try:
                    from modules.options_trade_economics import finalize_close_economics

                    closing_fees_override = None
                    raw_close = getattr(close_result, "raw", None) or {}
                    if isinstance(raw_close, dict):
                        comm = raw_close.get("commission") or raw_close.get("estimatedCommission")
                        if comm is not None:
                            try:
                                closing_fees_override = abs(float(comm))
                            except (TypeError, ValueError):
                                closing_fees_override = None
                    lotto = getattr(position, "lotto_contract", None)
                    lotto_d = (
                        lotto
                        if isinstance(lotto, dict)
                        else (lotto.to_dict() if hasattr(lotto, "to_dict") else {})
                    )
                    _, net_pnl = finalize_close_economics(
                        position=position,
                        exit_fill_per_share=float(exit_fill_px),
                        quantity=qty_close,
                        position_type=str(
                            getattr(position, "normalized_position_type", "")
                            or position.position_type
                            or "lotto"
                        ),
                        close_reason=str(rs),
                        quote_bid=exit_bid_signal if exit_bid_signal > 0 else None,
                        quote_ask=None,
                        quote_mid=exit_mid if exit_mid > 0 else None,
                        exit_price_source="LIVE_BID",
                        closing_fees_override=closing_fees_override,
                        is_credit_spread=structural == "credit_spread",
                        execution_source="broker_adapter",
                    )
                    fill_pnl_dollar = float(net_pnl)
                    entry_cost = float(
                        ((getattr(position, "metadata", {}) or {}).get("economic_summary") or {})
                        .get("entry", {})
                        .get("total_cost_basis")
                        or 0.0
                    )
                    if entry_cost > 0:
                        fill_pnl_pct = fill_pnl_dollar / entry_cost
                    exit_signal.pnl_dollar = fill_pnl_dollar
                    exit_signal.pnl_pct = fill_pnl_pct
                except Exception as econ_err:
                    log.warning(
                        "LIVE_CLOSE | economics_finalize_skipped | trade_id=%s | %s",
                        position_id,
                        econ_err,
                    )
                position.realized_pnl = float(fill_pnl_dollar)
                position.unrealized_pnl = 0.0
                position.current_value = exit_fill_px
                # Live sole-writer: close fill alone must NOT delete from registry.
                # Synchronizer removes only after broker reports flat.
                try:
                    from modules.broker_live_audits import _position_contract_key
                    from modules.broker_position_synchronizer import get_or_create_synchronizer
                    from modules.position_registry_contract import ConfirmedCloseFact

                    _, occ = _position_contract_key(position)
                    close_fact = ConfirmedCloseFact(
                        position_id=str(position_id),
                        broker_order_id=str(close_result.order_id or ""),
                        broker_execution_id=str(
                            getattr(close_result, "execution_id", None)
                            or getattr(close_result, "fill_id", None)
                            or ""
                        )
                        or None,
                        contract_symbol=str(occ or ""),
                        filled_quantity=int(qty_close),
                        fill_price=float(exit_fill_px),
                        exit_reason=str(rs),
                        confirmed_at=datetime.now(timezone.utc),
                        realized_pnl_dollars=float(fill_pnl_dollar),
                        realized_pnl_pct=float(fill_pnl_pct),
                    )
                    sync = get_or_create_synchronizer(self.trading_system)
                    sync_result = await sync.reconcile_confirmed_close(close_fact)
                    if position_id in self.positions:
                        log.error(
                            "LIVE_CLOSE | fill_confirmed_but_broker_not_flat | trade_id=%s | "
                            "keeping registry open | sync_pass=%s",
                            position_id,
                            str(bool(sync_result.pass_ok)).lower(),
                        )
                        await self._emit_close_escalation_alert(
                            position_id=position_id,
                            symbol=position.symbol,
                            exit_reason=f"{rs}|awaiting_broker_flat",
                            error="close_fill_broker_not_flat",
                        )
                        return None
                except Exception as sync_close_err:
                    log.error(
                        "SOLE_WRITER | reconcile_confirmed_close_failed | trade_id=%s | error=%s",
                        position_id,
                        sync_close_err,
                        exc_info=True,
                    )
                    await self._emit_close_escalation_alert(
                        position_id=position_id,
                        symbol=position.symbol,
                        exit_reason=rs,
                        error=f"synchronizer_close_failed:{sync_close_err}",
                    )
                    return None
                # Mark closed only after synchronizer removed from open book.
                position.status = "closed"
                try:
                    from modules.broker_live_audits import audit_broker_position_flat

                    await audit_broker_position_flat(
                        broker_adapter=adapter,
                        position=position,
                        symbol=position.symbol,
                    )
                except Exception as flat_audit_err:
                    log.warning("BROKER_POSITION_FLAT_AUDIT skipped: %s", flat_audit_err)
                log.info(
                    "✅ Live position %s closed broker-flat-confirmed order_id=%s",
                    position_id,
                    close_result.order_id,
                )
                try:
                    from modules.live_options_ledger import persist_options_close_from_position

                    persist_options_close_from_position(
                        position,
                        exit_reason=rs,
                        broker_order_id=str(close_result.order_id or ""),
                        filled_exit_price=float(
                            close_result.average_fill_price or exit_signal.exit_price
                        ),
                        mock_executor=self.mock_executor,
                    )
                except Exception as ledger_err:
                    log.warning(
                        "LIVE_OPTIONS_LEDGER | persist_from_position_failed | trade_id=%s | %s",
                        position_id,
                        ledger_err,
                    )
                self._record_unified_day_pnl_after_close(float(fill_pnl_dollar))
                if self.alert_manager and not suppress_exit_alert:
                    try:
                        holding_time_minutes = self._holding_minutes_for_exit_alert(
                            exit_signal.exit_time,
                            position.entry_time,
                        )
                        position_dict = position.to_dict()
                        await self.alert_manager.send_options_position_exit_alert(
                            position=position_dict,
                            exit_price=exit_signal.exit_price,
                            exit_reason=rs,
                            holding_time_minutes=holding_time_minutes,
                            mode="DEMO" if self.demo_mode else "LIVE",
                            realized_pnl_override=float(exit_signal.pnl_dollar),
                            pnl_pct_fraction_override=float(exit_signal.pnl_pct),
                            account_balance_override=self._snapshot_alert_account_balance(),
                        )
                    except Exception as alert_error:
                        log.error("Failed to send Options Position Exit alert: %s", alert_error)
                return position
            except Exception as e:
                log.error("Error closing position via broker adapter: %s", e, exc_info=True)
                await self._emit_close_escalation_alert(
                    position_id=position_id,
                    symbol=position.symbol,
                    exit_reason=exit_reason_override or exit_signal.reason,
                    error=str(e),
                )
                return None

        if self._runtime_demo_mode():
            closed = await self._demo_ledger_close_external(
                position,
                exit_signal,
                exit_reason=exit_reason_override or exit_signal.reason,
            )
            if closed:
                await self._send_position_exit_alert(
                    closed_position=closed,
                    position=position,
                    exit_signal=exit_signal,
                    exit_reason=exit_reason_override or exit_signal.reason,
                    suppress_exit_alert=suppress_exit_alert,
                )
                return closed
        _live_fail_closed("close_not_confirmed", path="execute_exit", symbol=position.symbol)
        await self._emit_close_escalation_alert(
            position_id=position_id,
            symbol=position.symbol,
            exit_reason=exit_reason_override or exit_signal.reason,
            error="close_not_confirmed",
        )
        return None
    
    async def close_all_positions(
        self,
        reason: str = "end_of_day_exit",
        *,
        flatten_status: Optional[Dict[str, Any]] = None,
        positions: Optional[List[OptionsPosition]] = None,
        send_aggregated_alert: bool = True,
    ) -> List[OptionsPosition]:
        """
        Close open options positions (typically at EOD).

        Args:
            reason: Reason for closing positions (canonical: ``end_of_day_exit``)
            positions: Optional subset to close; defaults to all open positions
            send_aggregated_alert: When False, skip Telegram so a caller can send
                one combined Batch Order Closed alert across strategies.

        Returns:
            List of closed OptionsPosition objects
        """
        open_positions = positions if positions is not None else self.get_open_positions()
        n_open = len(open_positions or [])
        if flatten_status is not None:
            flatten_status["no_positions_found"] = bool(n_open == 0)
            flatten_status.setdefault("positions_closed", 0)
            flatten_status.setdefault("positions_failed", 0)
            flatten_status.setdefault("positions_missing", 0)
        if not open_positions:
            if flatten_status is not None:
                flatten_status["positions_closed"] = 0
                flatten_status["positions_failed"] = 0
                flatten_status["positions_missing"] = 0
                flatten_status["aggregated_alert_sent"] = False
            log.info("No open options positions to close")
            return []
        
        log.info(f"Closing {len(open_positions)} open options positions: {reason}")
        closed_positions = []
        failed_closes = 0
        
        for position in open_positions:
            try:
                # Exit mark: exact executable bid only — never invent from historical marks.
                current_value = float(getattr(position, "current_value", 0.0) or 0.0)
                meta_eod = getattr(position, "metadata", None)
                if current_value <= 0.0 and isinstance(meta_eod, dict):
                    exact_oq = meta_eod.get("exact_quote") or meta_eod.get("exact_contract_quote") or meta_eod.get("option_quote")
                    if isinstance(exact_oq, dict) and exact_oq.get("bid") is not None:
                        try:
                            current_value = float(exact_oq.get("bid"))
                        except (TypeError, ValueError):
                            current_value = 0.0
                if current_value < 0.0:
                    current_value = 0.0
                if current_value <= 0.0:
                    if isinstance(meta_eod, dict):
                        meta_eod["eod_quote_status"] = "no_executable_bid"
                        meta_eod["eod_close_non_certified"] = True
                    log.warning(
                        "EOD_NO_EXECUTABLE_BID | trade_id=%s | symbol=%s | "
                        "settlement=0.0 | reason=missing_exact_bid",
                        getattr(position, "position_id", ""),
                        getattr(position, "symbol", ""),
                    )
                    current_value = 0.0
                
                # Create exit signal
                from .options_exit_manager import ExitSignal
                from .options_exit_reason_catalog import OptionsStealthExitReason, canonical_exit_reason

                canonical_reason = canonical_exit_reason(reason) or OptionsStealthExitReason.END_OF_DAY_EXIT.value
                if canonical_reason != OptionsStealthExitReason.END_OF_DAY_EXIT.value:
                    canonical_reason = OptionsStealthExitReason.END_OF_DAY_EXIT.value
                
                # Calculate P&L based on position type
                if position.position_type == 'credit_spread':
                    # For credit spreads: profit = (entry_price - current_value) / entry_price
                    # Entry price = credit received, current_value = cost to close
                    pnl_pct = (position.entry_price - current_value) / position.entry_price if position.entry_price > 0 else 0.0
                    pnl_dollar = (position.entry_price - current_value) * position.quantity * 100
                else:
                    # For debit spreads and lottos: profit = (current_value - entry_price) / entry_price
                    pnl_pct = (current_value - position.entry_price) / position.entry_price if position.entry_price > 0 else 0.0
                    pnl_dollar = (current_value - position.entry_price) * position.quantity * 100
                
                exit_signal = ExitSignal(
                    position_id=position.position_id,
                    reason=canonical_reason,
                    exit_price=current_value,
                    exit_time=datetime.now(),
                    pnl_pct=pnl_pct,
                    pnl_dollar=pnl_dollar,
                    details={"reason": reason, "batch_flatten": True},
                )

                # Execute exit — canonical stealth id eod_close (single EOD path)
                closed_position = await self.execute_exit(
                    exit_signal,
                    exit_reason_override=canonical_reason,
                    suppress_exit_alert=True,
                )
                if closed_position:
                    closed_positions.append(closed_position)
                else:
                    failed_closes += 1
                    
            except Exception as e:
                failed_closes += 1
                log.error(f"Error closing position {position.position_id}: {e}", exc_info=True)
        
        log.info(f"✅ Closed {len(closed_positions)}/{len(open_positions)} options positions")

        remaining = len(self.get_open_positions() or [])
        if flatten_status is not None:
            flatten_status["positions_closed"] = int(len(closed_positions))
            flatten_status["positions_failed"] = int(failed_closes)
            flatten_status["positions_missing"] = int(remaining)

        # Send aggregated exit alert if positions were closed
        if closed_positions and self.alert_manager and send_aggregated_alert:
            try:
                mode = "DEMO" if self.demo_mode else "LIVE"
                closed_positions_dict = [p.to_dict() for p in closed_positions]
                batch_balance = self._snapshot_alert_account_balance()
                agg_ok = await self.alert_manager.send_options_aggregated_exit_alert(
                    closed_positions=closed_positions_dict,
                    exit_reason=reason,
                    mode=mode,
                    account_balance=batch_balance,
                    eod_flatten_status=flatten_status,
                )
                if flatten_status is not None:
                    flatten_status["aggregated_alert_sent"] = bool(agg_ok)
                log.info(
                    "OPTIONS_AGGREGATED_EXIT_ALERT_STATUS | exit_reason=%s | sent=%s | positions_closed=%d | positions_missing=%d | positions_failed=%d | no_positions_found=%s",
                    str(reason),
                    str(bool(agg_ok)).lower(),
                    int(flatten_status.get("positions_closed", len(closed_positions)) if flatten_status else len(closed_positions)),
                    int(flatten_status.get("positions_missing", remaining) if flatten_status else remaining),
                    int(flatten_status.get("positions_failed", failed_closes) if flatten_status else failed_closes),
                    str(bool(flatten_status.get("no_positions_found", False)) if flatten_status else False).lower(),
                )
                log.info(f"✅ Options aggregated exit alert sent for {len(closed_positions)} positions")
            except Exception as e:
                if flatten_status is not None:
                    flatten_status["aggregated_alert_sent"] = False
                log.error(f"Failed to send options aggregated exit alert: {e}")
        elif flatten_status is not None:
            flatten_status["aggregated_alert_sent"] = False
        
        return closed_positions
    
    def reset_daily(self):
        """Reset daily state"""
        self.positions = {}
        log.info("Options Trading Executor daily reset complete")
