#!/usr/bin/env python3
"""
Mock Options Trading Executor for Demo Mode
===========================================

Simulates options trade execution, P&L tracking, and position management
for Easy 0DTE in Demo Mode. Mirrors MockTradingExecutor pattern from ORB Strategy.

Author: Easy ORB Strategy Development Team
Last Updated: January 6, 2026 (Rev 00231)
Version: 2.31.0
"""

import asyncio
import logging
import sys
import threading
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any
import json
import os
from dataclasses import dataclass, asdict, fields

from zoneinfo import ZoneInfo

from .options_trading_executor import OptionsPosition, DebitSpread, OptionContract
from .options_chain_manager import CreditSpread
from .options_chain_manager import OptionsChainManager
from .options_execution_normalize import (
    build_normalized_metadata_credit_spread,
    build_normalized_metadata_debit_spread,
    build_normalized_metadata_single_leg,
    log_metadata_normalized,
    log_position_type_normalized,
)
from .options_trade_id import build_options_position_id

log = logging.getLogger(__name__)


def _mock_ledger_io_isolated() -> bool:
    """True when this process is a unit test and must not touch the Demo ledger.

    A live MockOptionsExecutor() loads research_app/mock_options_history.json and
    execute_lotto_sleeve saves it. Tests that fill a contract were publishing that
    fill, and a $10,000 seed, onto the Demo account and the desk snapshot.
    """
    if os.getenv("EASY_0DTE_ALLOW_MOCK_LEDGER_WRITE", "").strip().lower() in {"1", "true", "yes"}:
        return False
    if os.getenv("PYTEST_CURRENT_TEST"):
        return True
    argv = " ".join(sys.argv)
    return "pytest" in argv or "unittest" in argv


def _pulse_desk(kind: str, position: Any, extra: Optional[Dict[str, Any]] = None, executor: Any = None) -> None:
    """Tiny public desk feed — never waits on the 33MB ledger persist."""
    if _mock_ledger_io_isolated():
        return
    try:
        from modules.desk_action_pulse import emit_desk_action

        emit_desk_action(kind, position, extra)
    except Exception as err:
        log.debug("desk_action_pulse skip | %s", err)
    if executor is None:
        return
    try:
        from modules.desk_snapshot_pulse import queue_desk_snapshot

        queue_desk_snapshot(executor)
    except Exception as err:
        log.debug("desk_snapshot_pulse skip | %s", err)

_PT_TZ = ZoneInfo("America/Los_Angeles")


def _pt_trading_date_iso() -> str:
    return datetime.now(_PT_TZ).date().isoformat()

# Aligns with modules.prime_alert_manager.ALERT_LABEL_EASY_ORB_0DTE_OPTIONS (avoid import cycle from easy0DTE)
_EASY_ORB_0DTE_ALERT = "Easy ORB1 Breakout"


def _mock_ledger_max_closed_trades() -> int:
    raw = os.getenv("MOCK_OPTIONS_LEDGER_MAX_CLOSED_TRADES", "200")
    try:
        return int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return 0


def _mock_ledger_max_age_days() -> int:
    raw = os.getenv("MOCK_OPTIONS_LEDGER_MAX_AGE_DAYS", "2")
    try:
        return max(1, min(2, int(float(str(raw).strip()))))
    except (TypeError, ValueError):
        return 2


def _apply_mock_closed_positions_retention(positions: List[Any]) -> List[Any]:
    """Keep recent closed trades (2-day age cap + count cap)."""
    max_age = _mock_ledger_max_age_days()
    cutoff = datetime.now(_PT_TZ) - timedelta(days=max_age)
    kept: List[Any] = []
    dropped_age = 0
    for pos in positions:
        closed_at = getattr(pos, "closed_at", None)
        if closed_at is None:
            kept.append(pos)
            continue
        try:
            ts = closed_at if closed_at.tzinfo else closed_at.replace(tzinfo=_PT_TZ)
            if ts < cutoff:
                dropped_age += 1
                continue
        except Exception:
            pass
        kept.append(pos)
    max_trades = _mock_ledger_max_closed_trades()
    dropped_cap = 0
    if max_trades > 0 and len(kept) > max_trades:
        dropped_cap = len(kept) - max_trades
        kept = kept[-max_trades:]
    if dropped_age or dropped_cap:
        log.info(
            "MOCK_OPTIONS_LEDGER | pruned | kept=%d | dropped_age=%d | dropped_cap=%d | "
            "cap=%d | max_age_days=%d",
            len(kept),
            dropped_age,
            dropped_cap,
            max_trades,
            max_age,
        )
    return kept


def _mock_position_expired_for_today(position: Any) -> bool:
    """True when a persisted open option cannot be a valid current 0DTE open."""
    meta = getattr(position, "metadata", None)
    norm = meta.get("normalized_options") if isinstance(meta, dict) and isinstance(meta.get("normalized_options"), dict) else {}

    expiry_raw = None
    for key in ("expiry_ymd", "selected_expiry_ymd", "requested_expiry_ymd"):
        expiry_raw = norm.get(key) if isinstance(norm, dict) else None
        if expiry_raw:
            break
    if not expiry_raw:
        try:
            lc = getattr(position, "lotto_contract", None) or {}
            expiry_raw = lc.get("expiry") if isinstance(lc, dict) else getattr(lc, "expiry", None)
        except Exception:
            expiry_raw = None

    expiry_s = str(expiry_raw or "").strip().replace("-", "")
    if len(expiry_s) == 8 and expiry_s.isdigit():
        try:
            expiry_date = datetime.strptime(expiry_s, "%Y%m%d").date()
            if expiry_date < datetime.now(_PT_TZ).date():
                return True
        except Exception:
            pass

    for source in (meta, norm):
        if not isinstance(source, dict):
            continue
        try:
            dte_raw = source.get("calendar_dte")
            if dte_raw is not None and int(float(dte_raw)) < 0:
                return True
        except (TypeError, ValueError):
            pass
    return False


@dataclass
class MockOptionsPosition:
    """Mock options position for Demo Mode"""
    position_id: str
    symbol: str
    position_type: str  # 'debit_spread', 'credit_spread', or 'lotto'
    entry_price: float
    entry_time: datetime
    closed_at: Optional[datetime] = None
    quantity: int = 1
    current_value: float = 0.0
    unrealized_pnl: float = 0.0
    realized_pnl: float = 0.0
    status: str = 'open'  # 'open', 'partial', 'closed'
    debit_spread: Optional[Dict[str, Any]] = None
    credit_spread: Optional[Dict[str, Any]] = None
    lotto_contract: Optional[Dict[str, Any]] = None
    # Shared normalized schema (additive); full blob under normalized_options.
    metadata: Optional[Dict[str, Any]] = None
    # Selector route (momentum_scalper, debit_spread, …); mirrors OptionsPosition.strategy_type.
    strategy_type: str = ""
    # Per-share tradable value at open (matches entry_price for ORB demo).
    entry_value: float = 0.0
    entry_debit: Optional[float] = None
    entry_credit: Optional[float] = None
    # Demo credit spread: margin encumbered while position is open.
    reserved_margin: float = 0.0
    
    # Demo-specific tracking
    max_favorable: float = 0.0
    max_unfavorable: float = 0.0

    @property
    def normalized_options(self) -> Dict[str, Any]:
        meta = self.metadata if isinstance(self.metadata, dict) else {}
        norm = meta.get("normalized_options")
        return norm if isinstance(norm, dict) else {}

    @property
    def normalized_position_type(self) -> str:
        structural = str(self.normalized_options.get("position_type", "") or "").strip()
        return structural if structural else str(self.position_type or "")
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        status_l = str(self.status or "").strip().lower()
        exit_price = None
        if status_l == "closed" and float(self.current_value or 0.0) > 0:
            exit_price = float(self.current_value)
        meta = self.metadata if isinstance(self.metadata, dict) else {}
        norm = meta.get("normalized_options") if isinstance(meta.get("normalized_options"), dict) else {}
        out = {
            'position_id': self.position_id,
            'symbol': self.symbol,
            'position_type': self.position_type,
            'debit_spread': self.debit_spread,
            'credit_spread': self.credit_spread,
            'lotto_contract': self.lotto_contract,
            'entry_price': self.entry_price,
            'exit_price': exit_price,
            'entry_time': self.entry_time.isoformat(),
            'closed_at': self.closed_at.isoformat() if self.closed_at else None,
            'exit_time': self.closed_at.isoformat() if self.closed_at else None,
            'quantity': self.quantity,
            'current_value': self.current_value,
            'unrealized_pnl': self.unrealized_pnl,
            'realized_pnl': self.realized_pnl,
            'status': self.status,
            'max_favorable': self.max_favorable,
            'max_unfavorable': self.max_unfavorable,
            'metadata': self.metadata,
            'entry_value': self.entry_value,
            'entry_debit': self.entry_debit,
            'entry_credit': self.entry_credit,
            'reserved_margin': self.reserved_margin,
            'strategy_type': self.strategy_type,
            'account_equity_at_entry': (
                (self.metadata or {}).get('account_equity_at_entry')
                if isinstance(self.metadata, dict)
                else None
            ),
        }
        for _ek in (
            "session_date",
            "expiry_ymd",
            "calendar_dte",
            "is_0dte",
            "expiry_selection_mode",
            "requested_expiry_ymd",
            "selected_expiry_ymd",
            "fallback_days",
            "fallback_reason",
            "dte",
        ):
            val = meta.get(_ek)
            if val is None:
                val = norm.get(_ek)
            if val is not None:
                out[_ek] = val
        return out


def _deserialize_mock_position_row(pos_data: Dict[str, Any]) -> MockOptionsPosition:
    """Build MockOptionsPosition from persisted dict; drops unknown keys."""
    row = dict(pos_data)
    if "entry_time" in row and isinstance(row["entry_time"], str):
        row["entry_time"] = datetime.fromisoformat(row["entry_time"])
    if "closed_at" in row and isinstance(row["closed_at"], str):
        row["closed_at"] = datetime.fromisoformat(row["closed_at"].replace("Z", "+00:00"))
    allowed = {f.name for f in fields(MockOptionsPosition)}
    clean = {k: v for k, v in row.items() if k in allowed}
    return MockOptionsPosition(**clean)


class MockOptionsExecutor:
    """
    Mock Options Trading Executor for Demo Mode
    
    Simulates options trade execution, P&L tracking, and position management
    for Easy 0DTE. Similar to MockTradingExecutor for ETF trading.
    """
    
    def __init__(self, alert_manager=None):
        """
        Initialize Mock Options Executor
        
        Args:
            alert_manager: Optional alert manager for notifications
        """
        self.alert_manager = alert_manager
        self.active_positions: Dict[str, MockOptionsPosition] = {}
        self.closed_positions: List[MockOptionsPosition] = []
        # EOD flatten closes many names; defer the full GCS merge-write until the pass ends
        # so Cloud Run does not recycle the instance while the event loop is blocked.
        self._persist_deferred = False
        self._persist_dirty = False
        self._persist_thread = None
        self._persist_thread_lock = threading.Lock()
        
        # Demo account balance — seed from 0DTE_DEMO_STARTING_BALANCE (Account0DTE.env).
        # Grows/shrinks with closed P&L; persists in GCS mock_options_history.json.
        _seed = self._configured_starting_balance()
        self.account_balance = _seed
        self.starting_balance = _seed
        # Demo: cash includes collected option premium; encumbered margin for credit spreads.
        self.reserved_margin_total = 0.0
        self.booked_equity = _seed
        self.peak_equity = _seed
        self.withdrawals_total = 0.0
        self.equity_curve: List[Dict[str, Any]] = []
        
        # Daily stats tracking
        self.daily_stats = {
            'positions_opened': 0,
            'positions_closed': 0,
            'total_pnl': 0.0,
            'winning_trades': 0,
            'losing_trades': 0,
            'best_trade': 0.0,
            'worst_trade': 0.0,
            'total_wins_sum': 0.0,
            'total_losses_sum': 0.0,
        }
        self.current_trading_date = datetime.utcnow().date()
        self.stats_trading_date: Optional[str] = _pt_trading_date_iso()
        self.risk_halt_cleared_pt_date: Optional[str] = None
        
        # Weekly stats tracking
        self.weekly_stats = {
            'positions_opened': 0,
            'positions_closed': 0,
            'total_pnl': 0.0,
            'winning_trades': 0,
            'losing_trades': 0,
            'best_trade': 0.0,
            'worst_trade': 0.0,
            'total_wins_sum': 0.0,
            'total_losses_sum': 0.0,
            'week_start_date': self._get_week_start_date()
        }
        self._check_and_reset_weekly_stats()
        
        # Mock data file (local backup)
        self.mock_data_file = "data/mock_options_history.json"
        # Rev 00217: GCS path for persistence (namespace-scoped — research_app on RESEARCH service)
        self.gcs_path = self._resolve_gcs_path()
        self._load_mock_data()
        if self.active_positions:
            try:
                from modules.desk_snapshot_pulse import queue_desk_snapshot

                queue_desk_snapshot(self)
            except Exception as pulse_err:
                log.debug("desk_snapshot_pulse startup skip | %s", pulse_err)
        log.info("🎮 Mock Options Executor initialized (%s):", _EASY_ORB_0DTE_ALERT)
        log.info("  - Starting balance: $%.2f", self.starting_balance)
        log.info("  - Current balance: $%.2f", self.account_balance)
        log.info("  - Booked equity: $%.2f", float(getattr(self, "booked_equity", self.starting_balance) or 0.0))
        log.info("  - Active positions: %d", len(self.active_positions))

    @staticmethod
    def _configured_starting_balance() -> float:
        """Canonical demo seed from Account0DTE.env / Cloud Run env."""
        try:
            from modules.unified_0dte_account import unified_demo_starting_balance

            return float(unified_demo_starting_balance())
        except Exception:
            try:
                return float(os.getenv("0DTE_DEMO_STARTING_BALANCE", "5000.0"))
            except (TypeError, ValueError):
                return 5000.0

    @staticmethod
    def _explicit_demo_starting_balance() -> Optional[float]:
        """Configured seed only. Missing config must not look like the $5,000 fallback."""
        try:
            from modules.config_loader import get_config_value

            raw = get_config_value("0DTE_DEMO_STARTING_BALANCE", None)
        except Exception:
            return None
        if raw is None:
            return None
        token = str(raw).strip()
        if not token:
            return None
        try:
            return float(token)
        except (TypeError, ValueError):
            return None

    def _apply_starting_balance_migration(self) -> None:
        """
        When 0DTE_DEMO_STARTING_BALANCE changes (e.g. $5k -> $100k), bump the live demo
        cash by the same delta so closed-trade history is preserved and sizing sees new capital.
        """
        configured = self._explicit_demo_starting_balance()
        if configured is None:
            log.warning(
                "0DTE_DEMO_BALANCE_MIGRATION | skipped | reason=seed_unconfigured | "
                "starting_balance=%.2f",
                float(self.starting_balance or 0.0),
            )
            return
        old_start = float(self.starting_balance or 0.0)
        if abs(old_start - configured) < 0.01:
            return
        delta = configured - old_start
        self.account_balance = float(self.account_balance or 0.0) + delta
        self.starting_balance = configured
        self._book_equity_event(delta, datetime.now(timezone.utc), kind="seed_adjust")
        log.info(
            "0DTE_DEMO_BALANCE_MIGRATION | old_start=%.2f | new_start=%.2f | delta=%.2f | "
            "account_balance=%.2f",
            old_start,
            configured,
            delta,
            self.account_balance,
        )
        try:
            self._save_mock_data()
        except Exception as save_err:
            log.warning("0DTE_DEMO_BALANCE_MIGRATION | persist_failed | %s", save_err)

    @staticmethod
    def _get_orb_modules_path() -> Optional[str]:
        """
        Resolve ORB Strategy modules path for shared helpers (e.g., gcs_persistence).
        Avoids duplicating a brittle path join that can break in Cloud Run/container layouts.
        """
        try:
            strategy_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            modules_path = os.path.join(strategy_root, "modules")
            if os.path.isdir(modules_path):
                return modules_path
        except Exception:
            pass
        return None

    @staticmethod
    def _resolve_gcs_path() -> str:
        try:
            from modules.runtime_mode import gcs_mock_options_history_path

            return gcs_mock_options_history_path()
        except Exception:
            return "research_app/mock_options_history.json"

    def _gcs_load_candidates(self) -> List[str]:
        return [self._resolve_gcs_path()]

    async def _send_demo_system_alert(self, title: str, message: str) -> None:
        """
        Best-effort demo alert adapter.

        PrimeAlertManager does not expose send_alert(); use send_system_alert when available,
        and gracefully fall back to send_telegram_alert or logging only.
        """
        if not self.alert_manager:
            return
        try:
            from modules.prime_alert_manager import (
                AlertLevel,
                _alert_mode_header_line,
                _telegram_html_escape,
            )

            title_esc = _telegram_html_escape(title)
            body = f"<b>{title_esc}</b>\n{_alert_mode_header_line(None)}\n\n{message}"
            send_fn = getattr(self.alert_manager, "_send_telegram_message", None)
            if callable(send_fn):
                await send_fn(body, AlertLevel.INFO)
                return
        except Exception:
            pass
        try:
            if hasattr(self.alert_manager, 'send_system_alert'):
                await self.alert_manager.send_system_alert(title=title, message=message)
            elif hasattr(self.alert_manager, 'send_telegram_alert'):
                await self.alert_manager.send_telegram_alert(f"{title}\n{message}")
            else:
                log.debug(f"Demo alert skipped (unsupported manager): {title}")
        except Exception as e:
            log.warning(f"Demo alert dispatch failed ({title}): {e}")

    def available_balance(self) -> float:
        """Demo cash available for new debits after reserved credit-spread margin."""
        return max(0.0, float(self.account_balance) - float(self.reserved_margin_total))

    def account_equity(self) -> float:
        """Cash plus capital at risk in open positions (denominator for deployed % alerts)."""
        deployed = 0.0
        for pos in self.active_positions.values():
            if str(getattr(pos, "status", "") or "").lower() == "closed":
                continue
            if _mock_position_expired_for_today(pos):
                continue
            if pos.position_type == "credit_spread":
                deployed += float(getattr(pos, "reserved_margin", 0.0) or 0.0)
            else:
                deployed += float(pos.entry_price or 0.0) * max(1, int(pos.quantity or 1)) * 100.0
        return float(self.account_balance) + deployed

    def _stamp_account_equity_at_entry(self, position: MockOptionsPosition) -> None:
        meta = position.metadata if isinstance(position.metadata, dict) else {}
        meta = dict(meta)
        meta["account_equity_at_entry"] = float(self.account_equity())
        position.metadata = meta

    def net_liquidation_value(self) -> float:
        """Cash ledger (includes premium collected; margin still reserved until close)."""
        return float(self.account_balance)
    
    def _get_week_start_date(self) -> datetime.date:
        """Get Monday of current week"""
        today = datetime.utcnow().date()
        days_since_monday = today.weekday()
        return today - timedelta(days=days_since_monday)
    
    def _check_and_reset_weekly_stats(self):
        """Reset weekly stats if new week"""
        today = datetime.utcnow().date()
        if today > self.weekly_stats['week_start_date']:
            log.info("📅 New week detected - resetting weekly stats")
            self.weekly_stats = {
                'positions_opened': 0,
                'positions_closed': 0,
                'total_pnl': 0.0,
                'winning_trades': 0,
                'losing_trades': 0,
                'best_trade': 0.0,
                'worst_trade': 0.0,
                'total_wins_sum': 0.0,
                'total_losses_sum': 0.0,
                'week_start_date': self._get_week_start_date()
            }
    
    def _load_mock_data(self):
        """
        Load existing mock trading data from GCS (primary) or local file (fallback)
        Rev 00217: Added GCS persistence similar to ORB Strategy
        """
        if _mock_ledger_io_isolated():
            log.info("ORB_0DTE_MOCK_LEDGER_LOAD_SKIPPED | reason=test_process")
            return
        data = None
        loaded_from = None
        
        # Try GCS first (persists across Cloud Run redeployments)
        try:
            # Import here to avoid circular dependencies
            import sys
            modules_path = self._get_orb_modules_path()
            if modules_path:
                sys.path.insert(0, modules_path)
                from gcs_persistence import get_gcs_persistence
                gcs = get_gcs_persistence()
                
                if gcs.enabled:
                    for candidate_path in self._gcs_load_candidates():
                        if not gcs.file_exists(candidate_path):
                            continue
                        json_content = gcs.read_string(candidate_path)
                        if json_content:
                            data = json.loads(json_content)
                            loaded_from = "GCS"
                            self.gcs_path = candidate_path
                            log.info(
                                "ORB_0DTE_MOCK_LEDGER_LOADED | path=%s | balance=$%.2f",
                                self.gcs_path,
                                float(data.get("account_balance", 0.0) or 0.0),
                            )
                            break
        except Exception as gcs_error:
            log.debug(f"GCS load failed (will try local): {gcs_error}")
        
        # Fallback to local file if GCS not available or failed
        if data is None:
            try:
                if os.path.exists(self.mock_data_file):
                    with open(self.mock_data_file, 'r') as f:
                        data = json.load(f)
                    loaded_from = "local file"
                    log.info(f"✅ Loaded 0DTE mock trading data from local file: {self.mock_data_file}")
            except Exception as local_error:
                log.debug(f"Local file load failed: {local_error}")
        
        # Process loaded data
        if data:
            try:
                _seed = self._configured_starting_balance()
                self.account_balance = float(data.get('account_balance', _seed) or _seed)
                self.starting_balance = float(data.get('starting_balance', _seed) or _seed)
                self.reserved_margin_total = float(data.get("reserved_margin_total", 0.0) or 0.0)
                
                # Load closed positions
                loaded_position_ids = set()
                for pos_data in data.get('closed_positions', []):
                    position_id = pos_data.get('position_id', '')
                    # Skip duplicates
                    if position_id and position_id in loaded_position_ids:
                        log.debug(f"Skipping duplicate position: {position_id}")
                        continue
                    
                    try:
                        position = _deserialize_mock_position_row(pos_data)
                        self.closed_positions.append(position)
                        if position_id:
                            loaded_position_ids.add(position_id)
                    except Exception as load_error:
                        log.warning(f"⚠️ Could not load position {position_id}: {load_error}")

                # Load open positions (persisted across Cloud Run restarts)
                for pos_data in data.get("active_positions", []):
                    position_id = str(pos_data.get("position_id", "") or "").strip()
                    if not position_id or position_id in self.active_positions:
                        continue
                    try:
                        position = _deserialize_mock_position_row(pos_data)
                        if _mock_position_expired_for_today(position):
                            log.info(
                                "MOCK_OPTIONS_LEDGER | skip_expired_open | position_id=%s | symbol=%s",
                                position_id,
                                getattr(position, "symbol", ""),
                            )
                            continue
                        if str(getattr(position, "status", "") or "").lower() != "closed":
                            self.active_positions[position_id] = position
                    except Exception as load_error:
                        log.warning(
                            "⚠️ Could not load open position %s: %s",
                            position_id,
                            load_error,
                        )
                
                log.info(
                    "✅ Loaded 0DTE mock trading data from %s: $%.2f balance, %d closed, %d open",
                    loaded_from,
                    float(self.account_balance),
                    len(self.closed_positions),
                    len(self.active_positions),
                )

                stats_date = data.get("stats_trading_date")
                self.stats_trading_date = str(stats_date) if stats_date else None
                raw_halt_cleared = data.get("risk_halt_cleared_pt_date")
                self.risk_halt_cleared_pt_date = (
                    str(raw_halt_cleared).strip() if raw_halt_cleared else None
                )
                if stats_date and stats_date == _pt_trading_date_iso():
                    if isinstance(data.get("daily_stats"), dict):
                        self.daily_stats = data["daily_stats"]
                    if isinstance(data.get("weekly_stats"), dict):
                        loaded_weekly = dict(data["weekly_stats"])
                        week_start = loaded_weekly.pop("week_start_date", None)
                        self.weekly_stats.update(loaded_weekly)
                        if week_start:
                            if isinstance(week_start, str):
                                self.weekly_stats["week_start_date"] = datetime.fromisoformat(week_start).date()
                            else:
                                self.weekly_stats["week_start_date"] = week_start
                    log.info(
                        "✅ Restored in-memory daily/weekly stats for %s from persisted ledger",
                        stats_date,
                    )

                self._hydrate_equity_book(data)
                self._apply_starting_balance_migration()
                self._drop_seed_adjusts_from_equity_book()
                self._rebase_seed_only_equity_book()
                
                # If loaded from GCS, also save to local for faster future loads
                if loaded_from == "GCS":
                    try:
                        data["booked_equity"] = self.booked_equity
                        data["peak_equity"] = self.peak_equity
                        data["withdrawals_total"] = self.withdrawals_total
                        data["equity_curve"] = list(self.equity_curve or [])
                        data["starting_balance"] = self.starting_balance
                        data["account_balance"] = self.account_balance
                        os.makedirs(os.path.dirname(self.mock_data_file), exist_ok=True)
                        with open(self.mock_data_file, 'w') as f:
                            json.dump(data, f, indent=2, default=str)
                        log.debug(f"✅ Synced GCS data to local file for faster future loads")
                    except Exception as sync_error:
                        log.debug(f"Could not sync GCS data to local: {sync_error}")
                        
            except json.JSONDecodeError as e:
                log.warning(f"Invalid JSON in 0DTE mock trading data ({loaded_from}): {e}")
                # Reset to defaults
                _seed = self._configured_starting_balance()
                self.account_balance = _seed
                self.starting_balance = _seed
                self.reserved_margin_total = 0.0
                self.closed_positions = []
                self._reset_equity_book(_seed)
            except Exception as e:
                log.warning(f"Failed to process 0DTE mock trading data from {loaded_from}: {e}")
                # Reset to defaults
                _seed = self._configured_starting_balance()
                self.account_balance = _seed
                self.starting_balance = _seed
                self.reserved_margin_total = 0.0
                self.closed_positions = []
                self._reset_equity_book(_seed)
        else:
            _seed = self._configured_starting_balance()
            log.info(
                "📭 No existing 0DTE mock trading data found (GCS or local) - starting fresh with $%.0f",
                _seed,
            )
            self.account_balance = _seed
            self.starting_balance = _seed
            self.reserved_margin_total = 0.0
            self.closed_positions = []
            self._reset_equity_book(_seed)

    def defer_persist(self, deferred: bool) -> None:
        """Coalesce ledger writes during a multi-close flatten pass.

        Caller must ``_save_mock_data()`` after releasing the deferral.
        GCS I/O runs on a background thread so the trading event loop stays free.
        """
        self._persist_deferred = bool(deferred)

    def _save_mock_data(self):
        """Queue a ledger persist. Do not run GCS read/write on the trading event loop.

        Cloud Run liveness hits HTTP ``/health`` on the same asyncio loop. A sync
        GCS merge-write on every fill blocked ``/health`` past 5s and killed the
        instance mid-session (Aug 24 TL0, Aug 25 afternoon, Aug 26 06:53 and 07:39 PT).
        """
        if _mock_ledger_io_isolated():
            log.info("MockOptionsExecutor | persist skipped | reason=test_process")
            return
        if getattr(self, "_persist_deferred", False):
            self._persist_dirty = True
            log.debug("MockOptionsExecutor | persist deferred until flatten pass ends")
            return
        self._schedule_persist()

    def _schedule_persist(self) -> None:
        self._persist_dirty = True
        lock = getattr(self, "_persist_thread_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._persist_thread_lock = lock
        with lock:
            t = getattr(self, "_persist_thread", None)
            if t is not None and t.is_alive():
                return
            t = threading.Thread(
                target=self._persist_loop,
                name="mock-ledger-gcs",
                daemon=True,
            )
            self._persist_thread = t
            t.start()

    def _persist_loop(self) -> None:
        while True:
            lock = getattr(self, "_persist_thread_lock", None)
            if lock is None:
                return
            with lock:
                if not getattr(self, "_persist_dirty", False):
                    self._persist_thread = None
                    return
                self._persist_dirty = False
            try:
                self._save_mock_data_sync()
            except Exception as e:
                log.error("❌ Failed to save 0DTE mock trading data: %s", e)

    def _save_mock_data_sync(self):
        """
        Save mock trading data to GCS (primary) and local file (backup)
        Rev 00217: Added GCS persistence similar to ORB Strategy with bidirectional merging
        """
        if _mock_ledger_io_isolated():
            log.info("MockOptionsExecutor | persist skipped | reason=test_process")
            return
        try:
            # Rev 00217: SAFEGUARD - Before saving, check if GCS has more positions than we have in memory
            # This prevents data loss if closed_positions was accidentally cleared
            gcs_position_count = 0
            gcs_closed_positions = []
            gcs_active_positions = []
            try:
                import sys
                modules_path = self._get_orb_modules_path()
                if modules_path:
                    sys.path.insert(0, modules_path)
                    from gcs_persistence import get_gcs_persistence
                    gcs = get_gcs_persistence()
                    
                    if gcs.enabled and gcs.file_exists(self.gcs_path):
                        json_content = gcs.read_string(self.gcs_path)
                        if json_content:
                            gcs_data = json.loads(json_content)
                            gcs_closed_positions = gcs_data.get('closed_positions', [])
                            gcs_active_positions = gcs_data.get('active_positions', [])
                            gcs_position_count = len(gcs_closed_positions)
                            log.info(
                                "📊 GCS ledger | closed=%d | open=%d | memory_closed=%d | memory_open=%d",
                                gcs_position_count,
                                len(gcs_active_positions),
                                len(self.closed_positions),
                                len(self.active_positions),
                            )
            except Exception as gcs_check_error:
                log.debug(f"Could not check GCS before save (non-critical): {gcs_check_error}")
            
            # Rev 00217: ALWAYS merge GCS positions with memory positions (both directions)
            # This ensures we NEVER lose historical data, even if one source is reset
            all_closed_positions = list(self.closed_positions)  # Start with current positions
            existing_position_ids = {pos.position_id for pos in self.closed_positions}
            
            # Always merge GCS positions (if any exist) to prevent data loss
            if gcs_closed_positions:
                positions_recovered = 0
                for pos_data in gcs_closed_positions:
                    position_id = pos_data.get('position_id', '')
                    if position_id and position_id not in existing_position_ids:
                        try:
                            position = _deserialize_mock_position_row(pos_data)
                            all_closed_positions.append(position)
                            existing_position_ids.add(position_id)
                            positions_recovered += 1
                            log.info(f"✅ Recovered historical position: {position.symbol} ({position_id})")
                        except Exception as merge_error:
                            log.warning(f"⚠️ Could not merge position {position_id}: {merge_error}")
                
                if positions_recovered > 0:
                    # Update self.closed_positions with merged list
                    original_count = len(self.closed_positions)
                    self.closed_positions = all_closed_positions
                    log.info(f"✅ Merged positions: Now have {len(self.closed_positions)} total positions (was {original_count}, recovered {positions_recovered} from GCS)")

            # Drop ghost actives before merge/save (closed row still present in GCS active list).
            self._purge_closed_ids_from_active()

            closed_position_ids = self._closed_position_id_set()

            # Merge GCS open positions so redeploys never drop active mock registry rows
            if gcs_active_positions:
                active_recovered = 0
                for pos_data in gcs_active_positions:
                    position_id = str(pos_data.get("position_id", "") or "").strip()
                    if not position_id or position_id in self.active_positions:
                        continue
                    if position_id in closed_position_ids:
                        continue
                    try:
                        position = _deserialize_mock_position_row(pos_data)
                        if _mock_position_expired_for_today(position):
                            log.info(
                                "MOCK_OPTIONS_LEDGER | skip_expired_open_merge | position_id=%s | symbol=%s",
                                position_id,
                                getattr(position, "symbol", ""),
                            )
                            continue
                        if str(getattr(position, "status", "") or "").lower() != "closed":
                            self.active_positions[position_id] = position
                            active_recovered += 1
                    except Exception as merge_error:
                        log.warning(
                            "⚠️ Could not merge open position %s: %s",
                            position_id,
                            merge_error,
                        )
                if active_recovered > 0:
                    log.info(
                        "✅ Merged open positions from GCS: now have %d active (recovered %d)",
                        len(self.active_positions),
                        active_recovered,
                    )
            
            # Rev 00217: CRITICAL SAFEGUARD - Never save if we would lose historical positions
            if gcs_position_count > 0 and len(self.closed_positions) < gcs_position_count:
                log.error(f"🚨 CRITICAL: About to save {len(self.closed_positions)} positions, but GCS had {gcs_position_count} positions - DATA LOSS RISK!")
                log.error(f"   Attempting to recover all GCS positions before save...")
                
                # Force merge all GCS positions
                for pos_data in gcs_closed_positions:
                    position_id = pos_data.get('position_id', '')
                    # Check if we already have this position
                    already_exists = False
                    for existing_pos in self.closed_positions:
                        if (existing_pos.position_id == position_id or
                            (existing_pos.symbol == pos_data.get('symbol') and
                             abs(existing_pos.realized_pnl - pos_data.get('realized_pnl', 0)) < 0.01 and
                             existing_pos.entry_time.isoformat()[:10] == pos_data.get('entry_time', '')[:10])):
                            already_exists = True
                            break
                    
                    if not already_exists:
                        try:
                            position = _deserialize_mock_position_row(pos_data)
                            self.closed_positions.append(position)
                            log.info(f"✅ Force-recovered position: {position.symbol} ({position_id})")
                        except Exception as merge_error:
                            log.warning(f"⚠️ Could not force-merge position {position_id}: {merge_error}")
                
                log.info(f"✅ After force-merge: {len(self.closed_positions)} positions")
            
            self.closed_positions = _apply_mock_closed_positions_retention(self.closed_positions)

            # Prepare data structure — snapshot mutable dicts/lists so concurrent
            # fills/closes cannot raise "dictionary changed size during iteration"
            # while json.dump / GCS serialize runs on the persist worker.
            import copy

            weekly_save = copy.deepcopy(self.weekly_stats) if isinstance(self.weekly_stats, dict) else {}
            week_start = weekly_save.get("week_start_date")
            if hasattr(week_start, "isoformat"):
                weekly_save["week_start_date"] = week_start.isoformat()
            daily_save = copy.deepcopy(self.daily_stats) if isinstance(self.daily_stats, dict) else {}
            closed_snap = list(self.closed_positions)
            active_snap = list(self.active_positions.values())

            data = {
                'account_balance': self.account_balance,
                'starting_balance': self.starting_balance,
                'booked_equity': round(float(getattr(self, "booked_equity", self.starting_balance) or 0.0), 2),
                'peak_equity': round(float(getattr(self, "peak_equity", self.starting_balance) or 0.0), 2),
                'withdrawals_total': round(float(getattr(self, "withdrawals_total", 0.0) or 0.0), 2),
                'equity_curve': list(getattr(self, "equity_curve", None) or []),
                'reserved_margin_total': self.reserved_margin_total,
                'daily_stats': daily_save,
                'weekly_stats': weekly_save,
                'stats_trading_date': _pt_trading_date_iso(),
                'risk_halt_cleared_pt_date': getattr(self, "risk_halt_cleared_pt_date", None),
                'closed_positions': [pos.to_dict() for pos in closed_snap],
                'active_positions': [
                    pos.to_dict()
                    for pos in active_snap
                    if str(getattr(pos, "status", "") or "").lower() != "closed"
                ],
            }
            
            log.info(
                "💾 Saving 0DTE mock ledger | closed=%d | open=%d | balance=$%.2f",
                len(data["closed_positions"]),
                len(data["active_positions"]),
                float(data["account_balance"]),
            )
            
            # Save to local file first (for backup)
            try:
                os.makedirs(os.path.dirname(self.mock_data_file), exist_ok=True)
                with open(self.mock_data_file, 'w') as f:
                    json.dump(data, f, indent=2, default=str)
                log.debug(f"✅ Saved 0DTE mock trading data to local file: {self.mock_data_file}")
            except Exception as local_error:
                log.warning(f"⚠️ Failed to save to local file: {local_error}")
            
            # Save to GCS (primary persistence for Cloud Run)
            try:
                import sys
                modules_path = self._get_orb_modules_path()
                if modules_path:
                    sys.path.insert(0, modules_path)
                    from gcs_persistence import get_gcs_persistence
                    gcs = get_gcs_persistence()
                    
                    if gcs.enabled:
                        # Upload JSON string directly to GCS
                        json_content = json.dumps(data, indent=2, default=str)
                        if gcs.upload_string(self.gcs_path, json_content):
                            log.info(f"✅ Saved 0DTE mock trading data to GCS: {self.gcs_path} ({len(data['closed_positions'])} positions, ${data['account_balance']:.2f} balance, persists across redeployments)")
                        else:
                            log.warning(f"⚠️ Failed to upload to GCS (will retry on next save)")
                    else:
                        log.debug(f"GCS persistence disabled - only saved locally")
            except Exception as gcs_error:
                log.warning(f"⚠️ Failed to save to GCS: {gcs_error} (local file saved as backup)")
                
        except Exception as e:
            log.error(f"❌ Failed to save 0DTE mock trading data: {e}")
    
    async def execute_debit_spread(
        self,
        spread: DebitSpread,
        quantity: int = 1,
        send_open_alert: bool = True,
        strategy_type: str = "",
        direction: str = "",
        spread_type: str = "debit",
    ) -> Optional[MockOptionsPosition]:
        """
        Execute debit spread in Demo Mode
        
        Args:
            spread: DebitSpread object
            quantity: Number of spreads
            
        Returns:
            MockOptionsPosition or None if execution failed
        """
        # Calculate total cost
        total_cost = spread.debit_cost * quantity * 100  # Options are per 100 shares
        
        # Check account balance
        if total_cost > self.available_balance():
            log.warning(f"🎮 DEMO: Insufficient balance for {spread.symbol} debit spread")
            log.warning(
                f"  Required: ${total_cost:.2f}, Available: ${self.available_balance():.2f} "
                f"(cash=${self.account_balance:.2f}, reserved_margin=${self.reserved_margin_total:.2f})"
            )
            return None
        
        # Simulate execution
        log.info(f"🎮 DEMO: Executing debit spread: {spread.symbol} {spread.option_type} {spread.long_strike}/{spread.short_strike}")
        log.info(f"  Quantity: {quantity}, Debit: ${spread.debit_cost:.2f}, Total Cost: ${total_cost:.2f}")
        
        # Create mock position (Rev 00232: Shortened format for alerts - symbol_date_strikes_type_microseconds)
        now = datetime.now()
        position_id = build_options_position_id(
            live=False,
            symbol=spread.symbol,
            option_type=spread.option_type,
            expiry=spread.expiry,
            long_strike=spread.long_strike,
            short_strike=spread.short_strike,
            now=now,
        )
        
        position = MockOptionsPosition(
            position_id=position_id,
            symbol=spread.symbol,
            position_type='debit_spread',
            debit_spread=spread.to_dict(),
            entry_price=spread.debit_cost,
            entry_time=datetime.now(),
            quantity=quantity,
            current_value=spread.debit_cost,
            unrealized_pnl=0.0,
            entry_value=float(spread.debit_cost),
            entry_debit=float(spread.debit_cost),
            entry_credit=None,
        )
        norm = build_normalized_metadata_debit_spread(
            trade_id=position_id,
            spread=spread,
            quantity=quantity,
            strategy_type=strategy_type,
            direction=str(direction or "LONG"),
            spread_type=str(spread_type or "debit"),
        )
        position.metadata = {"normalized_options": norm}
        position.strategy_type = str(norm.get("orb_strategy_type") or strategy_type or "").strip()
        log_position_type_normalized(position_id, norm["position_type"], "debit_spread")
        log_metadata_normalized(position_id, norm["position_type"], spread.symbol)
        
        # Deduct cost from account
        self.account_balance -= total_cost
        
        # Track position
        self.active_positions[position_id] = position
        self._stamp_account_equity_at_entry(position)
        self.daily_stats['positions_opened'] += 1
        self.weekly_stats['positions_opened'] += 1
        
        if send_open_alert:
            await self._send_demo_system_alert(
                title=f"{_EASY_ORB_0DTE_ALERT} | Debit Spread Opened",
                message=f"Strategy: {_EASY_ORB_0DTE_ALERT} (options)\n"
                        f"{spread.symbol} {spread.option_type.upper()} {spread.long_strike}/{spread.short_strike}\n"
                        f"Quantity: {quantity}\n"
                        f"Debit: ${spread.debit_cost:.2f}\n"
                        f"Total Cost: ${total_cost:.2f}\n"
                        f"Account Balance: ${self.account_balance:.2f}"
            )
        
        log.info(f"🎮 DEMO: Debit spread executed: Position ID {position_id}")
        log.info(f"  Account balance: ${self.account_balance:.2f}")
        _pulse_desk("entry", position, executor=self)
        self._save_mock_data()
        return position
    
    async def execute_lotto_sleeve(
        self,
        contract: OptionContract,
        quantity: int = 1,
        send_open_alert: bool = True,
        strategy_type: str = "",
        direction: str = "",
        spread_type: str = "single_leg",
        expiry_selection: Optional[Dict[str, Any]] = None,
    ) -> Optional[MockOptionsPosition]:
        """
        Execute lotto sleeve (single-leg option) in Demo Mode
        
        Args:
            contract: OptionContract object
            quantity: Number of contracts
            
        Returns:
            MockOptionsPosition or None if execution failed
        """
        from modules.option_executable_pricing import from_option_contract
        from modules.options_trade_economics import build_entry_economics

        fill_px = from_option_contract(contract, side="entry")
        entry_econ = build_entry_economics(
            fill_per_share=float(fill_px),
            quantity=int(quantity),
            position_type="lotto",
            quote_bid=float(getattr(contract, "bid", 0.0) or 0.0) or None,
            quote_ask=float(getattr(contract, "ask", 0.0) or 0.0) or None,
            quote_mid=float(getattr(contract, "mid_price", 0.0) or 0.0) or None,
            fill_mode="demo",
            execution_source="mock_options_executor",
        )
        total_cost = float(entry_econ.get("total_cost_basis") or (fill_px * quantity * 100))
        
        # Check account balance
        if total_cost > self.available_balance():
            log.warning(f"🎮 DEMO: Insufficient balance for {contract.symbol} lotto")
            log.warning(
                f"  Required: ${total_cost:.2f}, Available: ${self.available_balance():.2f} "
                f"(cash=${self.account_balance:.2f}, reserved_margin=${self.reserved_margin_total:.2f})"
            )
            return None
        
        # Simulate execution
        log.info(
            "🎮 DEMO: Executing lotto sleeve: %s %s %s | BTO @ ask=$%.2f | premium=$%.2f | opening_fees=$%.2f | total_cost=$%.2f",
            contract.symbol,
            contract.option_type,
            contract.strike,
            fill_px,
            float(entry_econ.get("premium") or 0.0),
            float(entry_econ.get("opening_fees") or 0.0),
            total_cost,
        )
        
        # Create mock position (Rev 00232: Shortened format for alerts - symbol_date_strike_type_microseconds)
        now = datetime.now()
        position_id = build_options_position_id(
            live=False,
            symbol=contract.symbol,
            option_type=contract.option_type,
            expiry=contract.expiry,
            strike=contract.strike,
            now=now,
        )
        
        position = MockOptionsPosition(
            position_id=position_id,
            symbol=contract.symbol,
            position_type='lotto',
            lotto_contract=contract.to_dict(),
            entry_price=fill_px,
            entry_time=datetime.now(),
            quantity=quantity,
            current_value=fill_px,
            unrealized_pnl=0.0,
            entry_value=float(fill_px),
            entry_debit=float(fill_px),
            entry_credit=None,
        )
        try:
            from modules.calendar_expiry_metadata import resolve_expiry_selection_from_context

            _expiry_sel = dict(expiry_selection or {}) if isinstance(expiry_selection, dict) else {}
            if not _expiry_sel:
                _expiry_sel = resolve_expiry_selection_from_context(
                    contract=contract,
                    trading_system=getattr(self, "trading_system", None),
                )
        except Exception:
            _expiry_sel = {}
        norm = build_normalized_metadata_single_leg(
            trade_id=position_id,
            contract=contract,
            quantity=quantity,
            strategy_type=strategy_type or "lotto",
            direction=str(direction or "LONG"),
            spread_type=str(spread_type or "single_leg"),
            entry_fill_price=float(fill_px),
            expiry_selection=_expiry_sel or None,
        )
        position.metadata = {
            "normalized_options": norm,
            "trade_economics_entry": entry_econ,
            "simulated_fill_confirmed": True,
            "fill_path": "demo_simulated_ledger",
        }
        # Prefer post-guard wire from normalize — required for Stage B enrollment.
        _osi = str(norm.get("broker_quote_request_symbol") or norm.get("osi_key") or "").strip()
        if _osi:
            position.metadata["osi_key"] = _osi
            position.metadata["osi_symbol"] = _osi
            position.metadata["broker_quote_request_symbol"] = _osi
            position.metadata["exit_grade_enrollment_eligible"] = True
        else:
            position.metadata["exit_grade_enrollment_eligible"] = False
            position.metadata["exit_grade_enrollment_blocker"] = "missing_frozen_quote_wire"
        _occ = str(norm.get("occ_symbol") or "").strip()
        if _occ:
            position.metadata["occ_symbol"] = _occ
            position.metadata["option_symbol"] = _occ
            position.metadata["contract_symbol"] = _occ
        for _ek in (
            "session_date",
            "expiry_ymd",
            "calendar_dte",
            "is_0dte",
            "expiry_selection_mode",
            "requested_expiry_ymd",
            "selected_expiry_ymd",
            "fallback_days",
            "fallback_reason",
            "dte",
        ):
            if _ek in norm:
                position.metadata[_ek] = norm.get(_ek)
        position.strategy_type = str(norm.get("orb_strategy_type") or strategy_type or "lotto").strip()
        try:
            from modules.execution_telemetry import attach_orb_entry_execution_telemetry

            fill_ts = datetime.now(timezone.utc).isoformat()
            attach_orb_entry_execution_telemetry(
                position.metadata,
                contract=contract,
                fill_price=float(fill_px),
                submit_ts=fill_ts,
                fill_ts=fill_ts,
                submitted_limit=float(contract.ask or fill_px or 0.0) or None,
                trade_id=position_id,
                symbol=contract.symbol,
                strategy_type=position.strategy_type or "ORB_0DTE",
                order_type="DEMO_SIM",
            )
        except Exception as telem_err:
            log.debug(
                "ORB demo entry telemetry attach skipped | trade_id=%s | %s",
                position_id,
                telem_err,
            )
        log_position_type_normalized(position_id, norm["position_type"], "lotto")
        log_metadata_normalized(position_id, norm["position_type"], contract.symbol)
        
        # Deduct cost from account
        self.account_balance -= total_cost
        
        # Track position
        self.active_positions[position_id] = position
        self._stamp_account_equity_at_entry(position)
        self.daily_stats['positions_opened'] += 1
        self.weekly_stats['positions_opened'] += 1
        
        st_lower = str(strategy_type or "").strip().lower()
        if send_open_alert and "trendline" not in st_lower:
            await self._send_demo_system_alert(
                title=f"{_EASY_ORB_0DTE_ALERT} | Lotto Sleeve Opened",
                message=f"Strategy: {_EASY_ORB_0DTE_ALERT} (options)\n"
                        f"{contract.symbol} {contract.option_type.upper()} {contract.strike}\n"
                        f"Quantity: {quantity}\n"
                        f"BTO @ ask: ${fill_px:.2f}\n"
                        f"Total Cost: ${total_cost:.2f}\n"
                        f"Account Balance: ${self.account_balance:.2f}"
            )
        
        log.info(f"🎮 DEMO: Lotto sleeve executed: Position ID {position_id}")
        log.info(f"  Account balance: ${self.account_balance:.2f}")
        _pulse_desk("entry", position, executor=self)
        self._save_mock_data()
        return position
    
    async def execute_credit_spread(
        self,
        spread: CreditSpread,
        quantity: int = 1,
        send_open_alert: bool = True,
        strategy_type: str = "",
        direction: str = "",
        spread_type: str = "credit",
    ) -> Optional[MockOptionsPosition]:
        """
        Execute credit spread in Demo Mode
        
        Args:
            spread: CreditSpread object
            quantity: Number of spreads
            
        Returns:
            MockOptionsPosition or None if execution failed
        """
        # Calculate margin requirement (max_loss = spread_width - credit_received)
        margin_requirement = spread.max_loss * quantity * 100  # Options are per 100 shares
        
        # Require unencumbered cash to cover margin reservation
        if margin_requirement > self.available_balance():
            log.warning(f"🎮 DEMO: Insufficient balance for {spread.symbol} credit spread")
            log.warning(
                f"  Required Margin: ${margin_requirement:.2f}, Available: ${self.available_balance():.2f} "
                f"(cash=${self.account_balance:.2f}, reserved_margin=${self.reserved_margin_total:.2f})"
            )
            return None
        
        # Simulate execution
        credit_received = spread.credit_received * quantity * 100
        log.info(f"🎮 DEMO: Executing credit spread: {spread.symbol} {spread.option_type} {spread.short_strike}/{spread.long_strike}")
        log.info(f"  Quantity: {quantity}, Credit: ${spread.credit_received:.2f}, Total Credit: ${credit_received:.2f}")
        log.info(f"  Margin Required: ${margin_requirement:.2f}")
        
        # Create mock position (Rev 00232: Shortened format for alerts - symbol_date_strikes_type_microseconds)
        now = datetime.now()
        position_id = build_options_position_id(
            live=False,
            symbol=spread.symbol,
            option_type=spread.option_type,
            expiry=spread.expiry,
            long_strike=spread.short_strike,
            short_strike=spread.long_strike,
            now=now,
        )
        
        position = MockOptionsPosition(
            position_id=position_id,
            symbol=spread.symbol,
            position_type='credit_spread',
            credit_spread=spread.to_dict(),
            entry_price=spread.credit_received,  # Entry price = credit received
            entry_time=datetime.now(),
            quantity=quantity,
            current_value=spread.credit_received,
            unrealized_pnl=0.0,
            entry_value=float(spread.credit_received),
            entry_debit=None,
            entry_credit=float(spread.credit_received),
            reserved_margin=float(margin_requirement),
        )
        norm = build_normalized_metadata_credit_spread(
            trade_id=position_id,
            spread=spread,
            quantity=quantity,
            strategy_type=strategy_type or "debit_spread",
            direction=str(direction or "SHORT"),
            spread_type=str(spread_type or "credit"),
        )
        position.metadata = {"normalized_options": norm}
        position.strategy_type = str(norm.get("orb_strategy_type") or strategy_type or "").strip()
        log_position_type_normalized(position_id, norm["position_type"], "credit_spread")
        log_metadata_normalized(position_id, norm["position_type"], spread.symbol)
        
        # Receive premium; encumber margin so available_balance reflects risk capital
        self.account_balance += credit_received
        self.reserved_margin_total += float(margin_requirement)
        log.info(
            "OPTIONS_EXECUTOR | stage=credit_margin_reserved | trade_id=%s | symbol=%s | reserved=%.2f | total_reserved=%.2f | available=%.2f",
            position_id,
            spread.symbol,
            float(margin_requirement),
            self.reserved_margin_total,
            self.available_balance(),
        )
        
        # Track position
        self.active_positions[position_id] = position
        self._stamp_account_equity_at_entry(position)
        self.daily_stats['positions_opened'] += 1
        self.weekly_stats['positions_opened'] += 1
        
        if send_open_alert:
            await self._send_demo_system_alert(
                title=f"{_EASY_ORB_0DTE_ALERT} | Credit Spread Opened",
                message=f"Strategy: {_EASY_ORB_0DTE_ALERT} (options)\n"
                        f"{spread.symbol} {spread.option_type.upper()} {spread.short_strike}/{spread.long_strike}\n"
                        f"Quantity: {quantity}\n"
                        f"Credit: ${spread.credit_received:.2f}\n"
                        f"Total Credit: ${credit_received:.2f}\n"
                        f"Margin Required: ${margin_requirement:.2f}\n"
                        f"Account Balance: ${self.account_balance:.2f}"
            )
        
        log.info(f"🎮 DEMO: Credit spread executed: Position ID {position_id}")
        log.info(f"  Account balance: ${self.account_balance:.2f}")
        _pulse_desk("entry", position, executor=self)
        self._save_mock_data()
        return position
    
    async def update_position_value(
        self,
        position_id: str,
        current_value: float
    ) -> Optional[MockOptionsPosition]:
        """
        Update position current value and P&L
        
        Args:
            position_id: Position ID
            current_value: Current position value
            
        Returns:
            Updated MockOptionsPosition or None if not found
        """
        if position_id not in self.active_positions:
            return None
        
        position = self.active_positions[position_id]
        position.current_value = current_value
        meta = position.metadata if isinstance(position.metadata, dict) else {}
        norm = meta.get("normalized_options")
        if isinstance(norm, dict):
            norm["current_value"] = float(current_value)

        from modules.options_trade_economics import net_result_pnl_dollars

        pt = str(position.normalized_position_type or position.position_type or "lotto")
        position.unrealized_pnl = net_result_pnl_dollars(
            entry_fill=float(position.entry_price or 0.0),
            exit_fill=float(current_value or 0.0),
            quantity=int(position.quantity or 0),
            is_credit_spread="credit" in pt.lower(),
            position_type=pt,
        )
        
        # Update max favorable/unfavorable
        if position.unrealized_pnl > position.max_favorable:
            position.max_favorable = position.unrealized_pnl
        if position.unrealized_pnl < position.max_unfavorable:
            position.max_unfavorable = position.unrealized_pnl
        
        return position
    
    async def close_position(
        self,
        position_id: str,
        exit_price: float,
        reason: str = "EOD"
    ) -> Optional[MockOptionsPosition]:
        """
        Close position in Demo Mode
        
        Args:
            position_id: Position ID
            exit_price: Exit price
            reason: Exit reason
            
        Returns:
            Closed MockOptionsPosition or None if not found
        """
        if position_id not in self.active_positions:
            log.warning(f"🎮 DEMO: Position {position_id} not found")
            return None

        position = self.active_positions[position_id]
        closed = self._finalize_mock_close(
            position,
            exit_price=float(exit_price),
            reason=str(reason or "EOD"),
        )
        if closed is None:
            return None

        log.info(f"🎮 DEMO: Position closed: {position_id}")
        log.info(
            f"  P&L: ${float(getattr(closed, 'realized_pnl', 0.0) or 0.0):+.2f}, Account balance: ${self.account_balance:.2f}"
        )

        return closed

    def _apply_closed_stats(self, final_pnl: float) -> None:
        self.daily_stats["positions_closed"] += 1
        self.daily_stats["total_pnl"] += final_pnl
        self.weekly_stats["positions_closed"] += 1
        self.weekly_stats["total_pnl"] += final_pnl
        if final_pnl > 0:
            self.daily_stats["winning_trades"] += 1
            self.weekly_stats["winning_trades"] += 1
            self.daily_stats["total_wins_sum"] = float(self.daily_stats.get("total_wins_sum") or 0.0) + final_pnl
            self.weekly_stats["total_wins_sum"] = float(self.weekly_stats.get("total_wins_sum") or 0.0) + final_pnl
            if final_pnl > self.daily_stats["best_trade"]:
                self.daily_stats["best_trade"] = final_pnl
            if final_pnl > self.weekly_stats["best_trade"]:
                self.weekly_stats["best_trade"] = final_pnl
        elif final_pnl < 0:
            self.daily_stats["losing_trades"] += 1
            self.weekly_stats["losing_trades"] += 1
            self.daily_stats["total_losses_sum"] = float(self.daily_stats.get("total_losses_sum") or 0.0) + abs(final_pnl)
            self.weekly_stats["total_losses_sum"] = float(self.weekly_stats.get("total_losses_sum") or 0.0) + abs(final_pnl)
            if final_pnl < self.daily_stats["worst_trade"]:
                self.daily_stats["worst_trade"] = final_pnl
            if final_pnl < self.weekly_stats["worst_trade"]:
                self.weekly_stats["worst_trade"] = final_pnl

    def record_external_closed_position(
        self,
        position: Any,
        *,
        exit_price: float,
        exit_reason: str = "",
    ) -> bool:
        """
        Persist a demo ledger close when the open row was not in mock active_positions.
        """
        position_id = str(getattr(position, "position_id", "") or "").strip()
        if not position_id:
            log.warning("MockOptionsExecutor | record_external_closed | missing position_id")
            return False

        if position_id in self._closed_position_id_set():
            log.debug("MockOptionsExecutor | record_external_closed | duplicate %s", position_id)
            return True

        if position_id in self.active_positions:
            active_pos = self.active_positions[position_id]
            closed_row = self._finalize_mock_close(
                active_pos,
                exit_price=float(exit_price or getattr(position, "current_value", 0.0) or 0.0),
                reason=str(exit_reason or "external_close"),
            )
            if closed_row is None:
                return False
            log.info(
                "MockOptionsExecutor | record_external_closed | promoted active close | trade_id=%s | symbol=%s | pnl=%.2f",
                position_id,
                closed_row.symbol,
                float(getattr(closed_row, "realized_pnl", 0.0) or 0.0),
            )
            return True

        final_pnl = float(getattr(position, "realized_pnl", 0.0) or 0.0)
        qty = int(getattr(position, "quantity", 1) or 1)
        entry_price = float(getattr(position, "entry_price", 0.0) or 0.0)
        exit_px = float(exit_price or getattr(position, "current_value", 0.0) or 0.0)
        pos_type = str(getattr(position, "position_type", "lotto") or "lotto")
        if abs(final_pnl) < 1e-9 and exit_px > 0.0 and entry_price > 0.0:
            mult = 100 * qty
            if pos_type == "credit_spread":
                final_pnl = (entry_price - exit_px) * mult
            else:
                final_pnl = (exit_px - entry_price) * mult
            position.realized_pnl = final_pnl
        entry_time = getattr(position, "entry_time", None) or datetime.now(timezone.utc)
        if hasattr(entry_time, "tzinfo") and entry_time.tzinfo is None:
            entry_time = entry_time.replace(tzinfo=timezone.utc)

        strategy_type = str(getattr(position, "strategy_type", "") or "")
        meta = dict(getattr(position, "metadata", None) or {})
        if strategy_type and "strategy_type" not in meta:
            meta["strategy_type"] = strategy_type
        if exit_reason:
            meta["exit_reason"] = exit_reason

        closed = MockOptionsPosition(
            position_id=position_id,
            symbol=str(getattr(position, "symbol", "") or ""),
            position_type=pos_type,
            entry_price=entry_price,
            entry_time=entry_time,
            closed_at=datetime.now(timezone.utc),
            quantity=qty,
            current_value=float(exit_price),
            unrealized_pnl=0.0,
            realized_pnl=final_pnl,
            status="closed",
            strategy_type=strategy_type,
            metadata=meta or None,
            entry_value=entry_price,
        )

        lotto = getattr(position, "lotto_contract", None)
        if lotto is not None:
            closed.lotto_contract = (
                lotto.to_dict() if hasattr(lotto, "to_dict") else lotto
            )

        self.account_balance += final_pnl
        self._apply_closed_stats(final_pnl)
        self.closed_positions.append(closed)
        self._book_equity_event(final_pnl, closed.closed_at, kind="exit")
        _pulse_desk("exit", closed, {"pnl": final_pnl}, executor=self)
        self._save_mock_data()
        log.info(
            "MockOptionsExecutor | record_external_closed | trade_id=%s | symbol=%s | pnl=%.2f | balance=%.2f | path=%s",
            position_id,
            closed.symbol,
            final_pnl,
            self.account_balance,
            self.gcs_path,
        )
        return True
    
    def _closed_position_id_set(self) -> set:
        out: set = set()
        for pos in self.closed_positions:
            pid = str(getattr(pos, "position_id", "") or "").strip()
            if pid:
                out.add(pid)
        return out

    def _purge_closed_ids_from_active(self) -> int:
        closed_ids = self._closed_position_id_set()
        purged = 0
        for pid in list(self.active_positions.keys()):
            if pid in closed_ids:
                del self.active_positions[pid]
                purged += 1
        if purged:
            log.info(
                "MockOptionsExecutor | purged ghost active rows | count=%d",
                purged,
            )
        return purged

    def is_position_closed(self, position_id: str) -> bool:
        pid = str(position_id or "").strip()
        if not pid:
            return False
        return pid in self._closed_position_id_set()

    def _reset_equity_book(self, seed: float) -> None:
        from modules.equity_curve_book import empty_book

        book = empty_book(seed)
        self.booked_equity = book["booked_equity"]
        self.peak_equity = book["peak_equity"]
        self.withdrawals_total = book["withdrawals_total"]
        self.equity_curve = book["equity_curve"]

    def _apply_equity_book(self, book: Dict[str, Any]) -> None:
        self.booked_equity = float(book.get("booked_equity") or 0.0)
        self.peak_equity = float(book.get("peak_equity") or self.booked_equity)
        self.withdrawals_total = float(book.get("withdrawals_total") or 0.0)
        self.equity_curve = list(book.get("equity_curve") or [])

    def _drop_seed_adjusts_from_equity_book(self) -> None:
        """Anchor booked equity on the seed plus real closes. Seed adjusts are not P&L."""
        from modules.equity_curve_book import rebase_without_seed_adjusts

        repaired = rebase_without_seed_adjusts(
            float(self.starting_balance or 0.0),
            getattr(self, "equity_curve", None),
        )
        if repaired is None:
            return
        old = float(getattr(self, "booked_equity", 0.0) or 0.0)
        self._apply_equity_book(repaired)
        log.warning(
            "MOCK_OPTIONS_LEDGER | equity_book_seed_adjusts_dropped | booked %.2f -> %.2f | seed=%.2f",
            old,
            float(self.booked_equity),
            float(self.starting_balance or 0.0),
        )
        try:
            self._save_mock_data()
        except Exception as save_err:
            log.warning("MOCK_OPTIONS_LEDGER | equity_book_persist_failed | %s", save_err)
        try:
            from modules.desk_snapshot_pulse import queue_desk_snapshot

            queue_desk_snapshot(self)
        except Exception as pulse_err:
            log.warning("MOCK_OPTIONS_LEDGER | desk_snapshot_after_rebase_failed | %s", pulse_err)

    def _rebase_seed_only_equity_book(self) -> None:
        """A seed-only curve must land on the seed. Repeated seed_adjusts are not P&L."""
        from modules.equity_curve_book import curve_is_seed_only

        if not curve_is_seed_only(getattr(self, "equity_curve", None)):
            return
        seed = float(self.starting_balance or 0.0)
        booked = float(getattr(self, "booked_equity", seed) or 0.0)
        if abs(booked - seed) <= 0.01:
            return
        log.warning(
            "MOCK_OPTIONS_LEDGER | equity_book_rebased | booked=%.2f | seed=%.2f | "
            "reason=seed_adjust_without_closes",
            booked,
            seed,
        )
        self._reset_equity_book(seed)

    def _hydrate_equity_book(self, data: Optional[Dict[str, Any]]) -> None:
        from modules.equity_curve_book import restore_or_backfill

        had_curve = isinstance(data, dict) and isinstance(data.get("equity_curve"), list) and bool(data.get("equity_curve"))
        book = restore_or_backfill(data, float(self.starting_balance or 0.0), self.closed_positions)
        self._apply_equity_book(book)
        log.info(
            "MOCK_OPTIONS_LEDGER | equity_book | booked=%.2f | peak=%.2f | curve=%d | backfill=%s",
            self.booked_equity,
            self.peak_equity,
            len(self.equity_curve),
            "no" if had_curve else "yes",
        )
        if not had_curve:
            try:
                self._save_mock_data()
            except Exception as save_err:
                log.warning("MOCK_OPTIONS_LEDGER | equity_book_persist_failed | %s", save_err)

    def _book_equity_event(self, delta: float, t: Any, kind: str = "exit") -> None:
        from modules.equity_curve_book import apply_event

        book = apply_event(
            {
                "booked_equity": float(getattr(self, "booked_equity", self.starting_balance) or 0.0),
                "peak_equity": float(getattr(self, "peak_equity", self.starting_balance) or 0.0),
                "withdrawals_total": float(getattr(self, "withdrawals_total", 0.0) or 0.0),
                "equity_curve": list(getattr(self, "equity_curve", None) or []),
            },
            delta=float(delta or 0.0),
            t=t,
            kind=kind,
        )
        self._apply_equity_book(book)

    def _finalize_mock_close(
        self,
        position: MockOptionsPosition,
        *,
        exit_price: float,
        reason: str,
    ) -> Optional[MockOptionsPosition]:
        position_id = str(getattr(position, "position_id", "") or "").strip()
        if not position_id:
            return None
        qty = int(position.quantity)
        meta = dict(getattr(position, "metadata", None) or {})
        from modules.options_trade_economics import resolve_true_to_live_exit_fill

        exit_px = resolve_true_to_live_exit_fill(
            exit_fill=float(exit_price),
            metadata=meta,
            mode="DEMO",
        )
        if exit_px <= 0:
            exit_px = float(exit_price)
        lotto = position.lotto_contract if isinstance(position.lotto_contract, dict) else {}
        quote_bid = float(lotto.get("bid") or 0.0) or None
        quote_ask = float(lotto.get("ask") or 0.0) or None
        quote_mid = float(lotto.get("mid_price") or lotto.get("mid") or 0.0) or None
        is_credit = position.normalized_position_type == "credit_spread"
        try:
            from modules.options_trade_economics import finalize_close_economics

            _, final_pnl = finalize_close_economics(
                position=position,
                exit_fill_per_share=exit_px,
                quantity=qty,
                position_type=str(position.normalized_position_type or position.position_type or "lotto"),
                close_reason=str(reason or ""),
                quote_bid=quote_bid,
                quote_ask=quote_ask,
                quote_mid=quote_mid,
                exit_price_source=str(meta.get("exit_price_source") or "BID_EXECUTABLE"),
                is_credit_spread=is_credit,
                execution_source="mock_options_executor",
            )
            meta = dict(getattr(position, "metadata", None) or meta)
        except Exception as econ_err:
            log.warning(
                "MockOptionsExecutor | close_economics_fallback | trade_id=%s | %s",
                position_id,
                econ_err,
            )
            mult = 100 * qty
            if is_credit:
                final_pnl = (position.entry_price - exit_px) * mult
            else:
                final_pnl = (exit_px - position.entry_price) * mult

        if is_credit:
            mult = 100 * qty
            exit_block = (meta.get("economic_summary") or {}).get("exit") or {}
            net_proceeds = float(exit_block.get("net_proceeds") or (exit_px * mult))
            self.account_balance -= net_proceeds
            rm = float(getattr(position, "reserved_margin", 0.0) or 0.0)
            self.reserved_margin_total = max(0.0, float(self.reserved_margin_total) - rm)
            log.info(
                "OPTIONS_EXECUTOR | stage=credit_margin_released | trade_id=%s | symbol=%s | released=%.2f | total_reserved=%.2f | available=%.2f",
                position_id,
                position.symbol,
                rm,
                self.reserved_margin_total,
                self.available_balance(),
            )
        else:
            exit_block = (meta.get("economic_summary") or {}).get("exit") or {}
            net_proceeds = float(exit_block.get("net_proceeds") or (exit_px * qty * 100))
            self.account_balance += net_proceeds

        position.realized_pnl = final_pnl
        position.unrealized_pnl = 0.0
        position.status = "closed"
        position.current_value = exit_px
        position.closed_at = datetime.now(timezone.utc)
        if reason:
            meta["exit_reason"] = reason
        position.metadata = meta or None

        self._apply_closed_stats(final_pnl)
        self.closed_positions.append(position)
        self.active_positions.pop(position_id, None)
        self._book_equity_event(final_pnl, position.closed_at, kind="exit")
        _pulse_desk("exit", position, {"pnl": final_pnl}, executor=self)
        self._save_mock_data()
        return position

    def get_open_positions(self) -> List[MockOptionsPosition]:
        """Get all open positions (excludes ledger-closed ghosts)."""
        self._purge_closed_ids_from_active()
        closed_ids = self._closed_position_id_set()
        out: List[MockOptionsPosition] = []
        for pid, pos in self.active_positions.items():
            if pid in closed_ids:
                continue
            if str(getattr(pos, "status", "") or "").lower() == "closed":
                continue
            if _mock_position_expired_for_today(pos):
                continue
            out.append(pos)
        return out
    
    def get_position(self, position_id: str) -> Optional[MockOptionsPosition]:
        """Get position by ID"""
        return self.active_positions.get(position_id)
    
    def get_daily_stats(self) -> Dict[str, Any]:
        """Get daily statistics"""
        return self.daily_stats.copy()
    
    def get_weekly_stats(self) -> Dict[str, Any]:
        """Get weekly statistics"""
        return self.weekly_stats.copy()
    
    def get_all_time_stats(self) -> Dict[str, Any]:
        """Get all-time statistics from closed positions"""
        if not self.closed_positions:
            return {
                'total_trades': 0,
                'winning_trades': 0,
                'losing_trades': 0,
                'total_pnl': 0.0,
                'total_wins_sum': 0.0,
                'total_losses_sum': 0.0
            }
        
        total_trades = len(self.closed_positions)
        winning_trades = sum(1 for p in self.closed_positions if p.realized_pnl > 0)
        losing_trades = total_trades - winning_trades
        total_pnl = sum(p.realized_pnl for p in self.closed_positions)
        total_wins_sum = sum(p.realized_pnl for p in self.closed_positions if p.realized_pnl > 0)
        total_losses_sum = abs(sum(p.realized_pnl for p in self.closed_positions if p.realized_pnl < 0))
        
        return {
            'total_trades': total_trades,
            'winning_trades': winning_trades,
            'losing_trades': losing_trades,
            'total_pnl': total_pnl,
            'total_wins_sum': total_wins_sum,
            'total_losses_sum': total_losses_sum
        }
    
    def reset_daily(self):
        """Reset daily state"""
        self.daily_stats = {
            'positions_opened': 0,
            'positions_closed': 0,
            'total_pnl': 0.0,
            'winning_trades': 0,
            'losing_trades': 0,
            'best_trade': 0.0,
            'worst_trade': 0.0,
            'total_wins_sum': 0.0,
            'total_losses_sum': 0.0,
        }
        self.current_trading_date = datetime.utcnow().date()
        self.stats_trading_date = _pt_trading_date_iso()
        self.risk_halt_cleared_pt_date = None
        log.info("🎮 DEMO: Mock Options Executor daily reset complete")
        if not _mock_ledger_io_isolated():
            try:
                from modules.desk_snapshot_pulse import queue_desk_snapshot

                queue_desk_snapshot(self)  # desk snapshot/head carry the new PT day
            except Exception as pulse_err:
                log.debug("desk_snapshot_pulse skip after reset_daily | %s", pulse_err)
