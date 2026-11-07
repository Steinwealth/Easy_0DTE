"""Shared 0DTE position registry contract (Demo + Live).

Demo and Live use the same field meanings; only the truth provider differs:
  Demo  → mock fills hydrate PositionRecord
  Live  → BrokerPositionSynchronizer hydrates PositionRecord from broker

This module freezes the schema and repair-policy vocabulary before the
synchronizer becomes the sole Live registry writer.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class EntrySource(str, Enum):
    LIVE_BROKER_FILL = "live_broker_fill"
    DEMO_MOCK_FILL = "demo_mock_fill"
    BROKER_RECOVERY = "broker_recovery"
    MANUAL_IMPORT = "manual_import"


class ManagementPolicy(str, Enum):
    STRATEGY_OWNED = "strategy_owned"  # ORB / Trendline known
    EMERGENCY_SAFE = "emergency_safe"  # recovered / unknown mapping
    UNMANAGED = "unmanaged"  # explicit no auto-exit
    QUARANTINED = "quarantined"  # mismatch / unsafe


class ReconciliationStatus(str, Enum):
    IN_SYNC = "in_sync"
    REPAIRED = "repaired"
    MISMATCH_OPEN = "mismatch_open"
    QUARANTINED = "quarantined"
    EXTERNALLY_CLOSED = "externally_closed"
    UNKNOWN = "unknown"


class MismatchKind(str, Enum):
    BROKER_ONLY = "broker_only"
    LOCAL_ONLY = "local_only"
    QTY_MISMATCH = "qty_mismatch"
    AVG_FILL_MISMATCH = "avg_fill_mismatch"
    CONTRACT_SYMBOL_MISMATCH = "contract_symbol_mismatch"
    LOCAL_CLOSED_BROKER_OPEN = "local_closed_broker_open"
    BROKER_FLAT_LOCAL_OPEN = "broker_flat_local_open"
    UNSUPPORTED_MANUAL = "unsupported_manual"


# Explicit repair policy (Live). Broker wins quantity/avg; never invent strategy.
REPAIR_POLICY: Dict[MismatchKind, str] = {
    MismatchKind.BROKER_ONLY: "import_classify_register_monitor_alert",
    MismatchKind.LOCAL_ONLY: "quarantine_or_remove_local_alert_critical",
    MismatchKind.QTY_MISMATCH: "replace_local_qty_with_broker",
    MismatchKind.AVG_FILL_MISMATCH: "broker_avg_wins",
    MismatchKind.CONTRACT_SYMBOL_MISMATCH: "quarantine_do_not_infer",
    MismatchKind.LOCAL_CLOSED_BROKER_OPEN: "restore_open_escalate",
    MismatchKind.BROKER_FLAT_LOCAL_OPEN: "close_local_as_externally_closed",
    MismatchKind.UNSUPPORTED_MANUAL: "register_unmanaged_or_emergency_safe",
}


STRATEGY_RECOVERED_UNKNOWN = "recovered_unknown"


@dataclass(frozen=True)
class PremiumSnapshot:
    """Immutable premium / PnL / MFE-MAE snapshot for exit-engine consumption.

    Long STC actionable premium = executable_exit_price (bid). Ask/mark are diagnostic.
    """

    position_id: str
    contract_symbol: str
    quantity: int

    entry_fill_price: float
    executable_exit_price: Optional[float]

    bid: Optional[float]
    ask: Optional[float]
    mark: Optional[float]

    current_value: Optional[float]
    unrealized_pnl_dollars: Optional[float]
    unrealized_pnl_pct: Optional[float]

    premium_hwm: Optional[float]
    premium_lwm: Optional[float]
    mfe_pct: Optional[float]
    mae_pct: Optional[float]

    quote_source: str
    quote_timestamp: Optional[datetime]
    quote_age_seconds: Optional[float]
    degraded: bool
    degradation_reason: Optional[str]

    # Extended fields (defaults preserve older constructors / tests)
    entry_notional: Optional[float] = None
    last: Optional[float] = None
    current_position_value: Optional[float] = None
    premium_bucket: Optional[str] = None
    data_quality: Optional[str] = None
    diagnostics: Dict[str, Any] = field(default_factory=dict)
    # True-to-live liquidatable (bid) series — only these may control long-option exits
    current_bid: Optional[float] = None
    current_ask: Optional[float] = None
    current_mark: Optional[float] = None
    liquidatable_pnl_pct: Optional[float] = None
    liquidatable_hwm_bid: Optional[float] = None
    liquidatable_mfe_pct: Optional[float] = None
    giveback_from_hwm_pct: Optional[float] = None
    analytical_mark_mfe_pct: Optional[float] = None
    mfe_giveback_armed: bool = False
    mfe_giveback_triggered: bool = False
    exit_grade: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        ts = self.quote_timestamp
        d["quote_timestamp"] = ts.isoformat() if isinstance(ts, datetime) else None
        return d


@dataclass(frozen=True)
class PremiumStateUpdate:
    """Mutable registry fields to apply after a premium snapshot (sole-writer)."""

    position_id: str
    premium_hwm: Optional[float] = None
    premium_lwm: Optional[float] = None
    mfe_pct: Optional[float] = None
    mae_pct: Optional[float] = None
    last_reliable_premium: Optional[float] = None
    last_quote_timestamp: Optional[datetime] = None
    premium_health: Optional[str] = None
    degraded_since: Optional[datetime] = None
    current_position_value: Optional[float] = None
    unrealized_pnl_dollars: Optional[float] = None
    unrealized_pnl_pct: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        for key in ("last_quote_timestamp", "degraded_since"):
            ts = d.get(key)
            if isinstance(ts, datetime):
                d[key] = ts.isoformat()
        return d


@dataclass(frozen=True)
class PremiumApplyResult:
    """Truthful outcome of applying PremiumStateUpdate to the authoritative registry."""

    applied: bool
    position_id: str
    registry: str
    reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# Quote sources allowed to raise HWM / favorable MFE (Demo + Live).
HWM_RATCHET_TRUSTED_SOURCES = frozenset(
    {
        "exact_quote",
        "exact_contract_quote",  # legacy alias during rename window
        "exit_grade",
        "nearest",
        "cached_quote",
        "bid_executable",
        "nearest_exchange_strike",
        "nearest_listed",
        "chain_strike_match",
        "fresh_cache",
        "fresh_cached_quote",
        "fresh_chain_refresh",
        "fresh_chain_force_refresh",  # legacy
        "live_chain_monitor",
    }
)


@dataclass(frozen=True)
class QuoteContext:
    """Wraps existing chain / option-quote structures without a data-provider rewrite."""

    option_quote: Optional[Dict[str, Any]] = None
    chain: Optional[Dict[str, Any]] = None
    target_strike: Optional[float] = None
    option_type: Optional[str] = None  # call | put
    position_type: str = "lotto"  # lotto | debit_spread | credit_spread
    prior_hwm: Optional[float] = None
    prior_lwm: Optional[float] = None
    prior_mfe_pct: Optional[float] = None
    prior_mae_pct: Optional[float] = None
    allow_hwm_ratchet: bool = True
    last_reliable_premium: Optional[float] = None
    degraded_since: Optional[datetime] = None
    quote_timestamp: Optional[datetime] = None


@dataclass(frozen=True)
class PremiumEvaluationRequest:
    position: Any  # PositionRecord or OptionsPosition duck-type
    quote_context: QuoteContext
    evaluated_at: datetime


@dataclass(frozen=True)
class ExitDecision:
    """Frozen exit evaluation result — stealth must not submit or mutate registry."""

    action: str  # HOLD | SELL | FORCE_SELL
    reason: Optional[str] = None
    current_pnl_pct: Optional[float] = None
    mfe_pct: Optional[float] = None
    threshold_name: Optional[str] = None
    threshold_value: Optional[float] = None
    triggered_value: Optional[float] = None
    # Backward-compatible aliases used by transitional call sites
    trigger_value: Optional[float] = None
    threshold: Optional[float] = None
    diagnostics: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


EXIT_ACTION_HOLD = "HOLD"
EXIT_ACTION_SELL = "SELL"
EXIT_ACTION_FORCE_SELL = "FORCE_SELL"

FORCE_SELL_REASON_TOKENS = (
    "no_data",
    "degraded_data",
    "force_eod",
    "eod",
    "forced_degraded",
    "time_exit",
    "mandatory",
)


def exit_action_from_reason(reason: Optional[str], *, sell: bool) -> str:
    if not sell:
        return EXIT_ACTION_HOLD
    r = str(reason or "").strip().lower()
    if any(tok in r for tok in FORCE_SELL_REASON_TOKENS):
        return EXIT_ACTION_FORCE_SELL
    return EXIT_ACTION_SELL


def premium_snapshot_from_mapping(data: Any) -> Optional[PremiumSnapshot]:
    """Rehydrate PremiumSnapshot from registry metadata dict or pass-through."""
    if data is None:
        return None
    if isinstance(data, PremiumSnapshot):
        return data
    if not isinstance(data, dict):
        return None
    try:
        payload = dict(data)
        ts = payload.get("quote_timestamp")
        if isinstance(ts, str) and ts.strip():
            text = ts.replace("Z", "+00:00")
            payload["quote_timestamp"] = datetime.fromisoformat(text)
        # Drop unknown keys so older/newer metadata still constructs
        allowed = {f.name for f in PremiumSnapshot.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        filtered = {k: v for k, v in payload.items() if k in allowed}
        return PremiumSnapshot(**filtered)
    except Exception:
        return None


@dataclass
class PositionRecord:
    """Canonical logical position — same meaning in Demo and Live."""

    position_id: str
    broker_position_id: Optional[str] = None
    broker_order_ids: List[str] = field(default_factory=list)
    occ_contract_symbol: str = ""
    underlying: str = ""
    expiration: str = ""
    strike: Optional[float] = None
    call_put: str = ""  # call | put
    quantity: int = 0
    average_fill: float = 0.0
    strategy: str = ""
    entry_source: str = EntrySource.DEMO_MOCK_FILL.value
    entry_timestamp: Optional[datetime] = None
    monitor_identity: Optional[str] = None
    management_policy: str = ManagementPolicy.STRATEGY_OWNED.value
    quote_health_state: Dict[str, Any] = field(default_factory=dict)
    premium_snapshot: Optional[PremiumSnapshot] = None
    premium_hwm: Optional[float] = None
    premium_lwm: Optional[float] = None
    mfe_pct: Optional[float] = None
    mae_pct: Optional[float] = None
    exit_engine_state: Dict[str, Any] = field(default_factory=dict)
    close_lifecycle: Dict[str, Any] = field(default_factory=dict)
    reconciliation_status: str = ReconciliationStatus.UNKNOWN.value
    status: str = "open"  # open | quarantined | closed

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if self.entry_timestamp is not None:
            d["entry_timestamp"] = self.entry_timestamp.isoformat()
        if self.premium_snapshot is not None:
            d["premium_snapshot"] = self.premium_snapshot.to_dict()
        return d


def assert_position_record_invariants(rec: PositionRecord) -> List[str]:
    """Return list of invariant violations (empty = ok)."""
    errors: List[str] = []
    if not str(rec.position_id or "").strip():
        errors.append("missing_position_id")
    if rec.status == "open" and int(rec.quantity) <= 0:
        errors.append("open_with_nonpositive_qty")
    if rec.entry_source == EntrySource.BROKER_RECOVERY.value:
        if rec.strategy not in ("", STRATEGY_RECOVERED_UNKNOWN) and rec.strategy not in (
            "recovered_unknown",
            "unknown_0dte",
        ):
            # Recovery may keep a confident strategy; unknown must be labeled.
            pass
        if rec.management_policy not in (
            ManagementPolicy.EMERGENCY_SAFE.value,
            ManagementPolicy.UNMANAGED.value,
            ManagementPolicy.STRATEGY_OWNED.value,
            ManagementPolicy.QUARANTINED.value,
        ):
            errors.append("invalid_management_policy_on_recovery")
    if rec.strategy in (STRATEGY_RECOVERED_UNKNOWN, "unknown_0dte"):
        if rec.management_policy == ManagementPolicy.STRATEGY_OWNED.value:
            errors.append("unknown_strategy_must_not_be_strategy_owned")
    return errors


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def recovered_unknown_meta(*, occ_symbol: str, broker_qty: int) -> Dict[str, Any]:
    """Metadata for broker-only imports that cannot map to ORB/Trendline confidently."""
    return {
        "strategy": STRATEGY_RECOVERED_UNKNOWN,
        "management_policy": ManagementPolicy.EMERGENCY_SAFE.value,
        "entry_source": EntrySource.BROKER_RECOVERY.value,
        "occ_symbol": occ_symbol,
        "broker_quantity": int(broker_qty),
        "rehydrated_from_broker": True,
        "reconciliation_status": ReconciliationStatus.REPAIRED.value,
    }


@dataclass(frozen=True)
class ConfirmedOpenFact:
    """Execution fact from Live entry — executor returns this; synchronizer creates the record."""

    broker_order_id: str
    broker_execution_id: Optional[str]
    contract_symbol: str  # OCC preferred
    underlying: str
    expiration: str
    strike: float
    call_put: str
    filled_quantity: int
    fill_price: float
    strategy: str
    signal_id: Optional[str]
    trade_id: str
    submitted_at: datetime
    confirmed_at: datetime
    lotto_contract: Any = None
    metadata_seed: Dict[str, Any] = field(default_factory=dict)
    direction: str = "LONG"
    spread_type: str = "single_leg"


@dataclass(frozen=True)
class ConfirmedCloseFact:
    """Close fill fact — does not authorize registry deletion; broker flat does."""

    position_id: str
    broker_order_id: str
    broker_execution_id: Optional[str]
    contract_symbol: str
    filled_quantity: int
    fill_price: float
    exit_reason: str
    confirmed_at: datetime
    realized_pnl_dollars: Optional[float] = None
    realized_pnl_pct: Optional[float] = None


# Sync-failure policy vocabulary (pricing unavailable ≠ ownership unknown).
SYNC_UNAVAILABLE_PREFIXES = (
    "broker_adapter_unavailable",
    "get_positions_failed",
    "broker_positions_unavailable",
)
SYNC_FAIL_STREAK_CRITICAL = 3
PENDING_BROKER_VISIBILITY_GRACE_SEC = 120.0


class StrategyRecoverySource(str, Enum):
    PERSISTED_LIFECYCLE = "persisted_lifecycle"
    BROKER_ORDER_METADATA = "broker_order_metadata"
    LOCAL_REGISTRY = "local_registry"
    SIGNAL_EXECUTION_LEDGER = "signal_execution_ledger"
    RECOVERED_UNKNOWN = "recovered_unknown"


def parse_occ_symbol(occ: str) -> Dict[str, Any]:
    """Parse OCC-style ``SYM YYMMDDC########`` (space optional) into contract fields."""
    raw = str(occ or "").strip().upper()
    out: Dict[str, Any] = {
        "occ_symbol": raw,
        "underlying": "",
        "expiration": "",
        "strike": None,
        "call_put": "",
    }
    if not raw:
        return out
    # Prefer spaced E*TRADE form; fall back to compact.
    m = re.match(
        r"^([A-Z0-9.\-]+)\s+(\d{6})([CP])(\d{8})$",
        raw,
    )
    if not m:
        m = re.match(r"^([A-Z0-9.\-]+?)(\d{6})([CP])(\d{8})$", raw.replace(" ", ""))
    if not m:
        parts = raw.split()
        out["underlying"] = parts[0] if parts else raw[:6]
        return out
    underlying, yymmdd, cp, strike_milli = m.groups()
    # OSI roots are dash-padded to 6 chars (QQQ---, COIN--, QBTS--). Never keep
    # those dashes in the underlier — Market Quote treats QQQ--- as invalid.
    underlying = str(underlying or "").rstrip("-")
    try:
        # YYMMDD → YYYY-MM-DD (20xx)
        expiration = f"20{yymmdd[0:2]}-{yymmdd[2:4]}-{yymmdd[4:6]}"
        strike = int(strike_milli) / 1000.0
    except (TypeError, ValueError):
        return out
    out["underlying"] = underlying
    out["expiration"] = expiration
    out["strike"] = strike
    out["call_put"] = "call" if cp == "C" else "put"
    return out


def is_sync_unavailable_reason(reason: str) -> bool:
    r = str(reason or "")
    return any(r == p or r.startswith(f"{p}:") for p in SYNC_UNAVAILABLE_PREFIXES)
