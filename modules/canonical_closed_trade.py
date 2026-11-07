"""Canonical closed-trade result + exactly-once finalization.

All Demo/Live ledgers, alerts, EOD, and analytics must consume this object
rather than independently recomputing economics.
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Set, Tuple
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")
_finalized_keys: Set[str] = set()


@dataclass
class CanonicalClosedTrade:
    trade_id: str = ""
    app: str = ""
    mode: str = ""
    strategy: str = ""
    underlying: str = ""
    option_symbol: str = ""
    option_type: str = ""
    strike: Optional[float] = None
    expiration: str = ""
    quantity: int = 0
    entry_timestamp: Optional[str] = None
    entry_fill_price: Optional[float] = None
    exit_decision_timestamp: Optional[str] = None
    exit_decision_bid: Optional[float] = None
    exit_fill_timestamp: Optional[str] = None
    exit_fill_price: Optional[float] = None
    gross_entry_cost: Optional[float] = None
    gross_exit_proceeds: Optional[float] = None
    gross_pnl: Optional[float] = None
    entry_fees: Optional[float] = None
    exit_fees: Optional[float] = None
    total_fees: Optional[float] = None
    net_pnl: Optional[float] = None
    net_pnl_pct: Optional[float] = None
    max_liquidatable_pnl_pct: Optional[float] = None
    max_analytical_pnl_pct: Optional[float] = None
    bid_hwm: Optional[float] = None
    exit_reason: str = ""
    broker_order_id: str = ""
    quote_source: str = ""
    quote_age_ms: Optional[float] = None
    decision_to_dispatch_ms: Optional[float] = None
    dispatch_to_fill_ms: Optional[float] = None
    close_lifecycle_id: str = ""
    idempotency_key: str = ""
    market_session_date: str = ""
    profit_retention: Dict[str, Any] = field(default_factory=dict)
    economic_summary: Dict[str, Any] = field(default_factory=dict)
    extras: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def us_market_session_date(now: Optional[datetime] = None) -> str:
    """U.S. equity session date (America/New_York), not UTC date boundary."""
    dt = now or datetime.now(_ET)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc).astimezone(_ET)
    else:
        dt = dt.astimezone(_ET)
    return dt.strftime("%Y-%m-%d")


def build_idempotency_key(*, app: str, mode: str, trade_id: str, close_lifecycle_id: str) -> str:
    return f"{app}|{mode}|{trade_id}|{close_lifecycle_id}"


def resolve_close_lifecycle_id(position: Any) -> str:
    meta = getattr(position, "metadata", None) or {}
    if not isinstance(meta, dict):
        meta = {}
    existing = str(meta.get("close_lifecycle_id") or "").strip()
    if existing:
        return existing
    lid = str(uuid.uuid4())
    try:
        meta["close_lifecycle_id"] = lid
        position.metadata = meta
    except Exception:
        pass
    return lid


def build_canonical_closed_trade(
    *,
    position: Any,
    economic_summary: Dict[str, Any],
    exit_reason: str = "",
    exit_fill_price: Optional[float] = None,
    exit_decision_bid: Optional[float] = None,
    mode: str = "",
    app: str = "",
    broker_order_id: str = "",
    quote_source: str = "",
    quote_age_ms: Optional[float] = None,
    profit_retention: Optional[Dict[str, Any]] = None,
) -> CanonicalClosedTrade:
    meta = getattr(position, "metadata", None) or {}
    if not isinstance(meta, dict):
        meta = {}
    entry = economic_summary.get("entry") or {}
    exit_b = economic_summary.get("exit") or {}
    econ = economic_summary.get("economics") or {}
    trade_id = str(getattr(position, "position_id", "") or "")
    app_name = app or str(os.getenv("APP_NAMESPACE") or os.getenv("K_SERVICE") or "")
    mode_name = mode or str(os.getenv("MODE") or ("demo" if getattr(position, "demo_mode", False) else "live"))
    lifecycle_id = resolve_close_lifecycle_id(position)
    key = build_idempotency_key(
        app=app_name, mode=mode_name, trade_id=trade_id, close_lifecycle_id=lifecycle_id
    )
    osnap = meta.get("option_stealth") if isinstance(meta.get("option_stealth"), dict) else {}
    latch = meta.get("exit_latch") if isinstance(meta.get("exit_latch"), dict) else {}
    retention = profit_retention or meta.get("profit_retention") or {}

    return CanonicalClosedTrade(
        trade_id=trade_id,
        app=app_name,
        mode=mode_name,
        strategy=str(getattr(position, "strategy_type", "") or ""),
        underlying=str(getattr(position, "symbol", "") or "").upper(),
        option_symbol=str(meta.get("option_symbol") or getattr(position, "option_symbol", "") or ""),
        option_type=str(meta.get("option_type") or getattr(position, "option_type", "") or ""),
        strike=_opt_float(meta.get("strike") or getattr(position, "strike", None)),
        expiration=str(meta.get("expiration") or getattr(position, "expiration", "") or ""),
        quantity=int(getattr(position, "quantity", 0) or entry.get("contracts") or 0),
        entry_timestamp=str(meta.get("entry_timestamp") or getattr(position, "entry_time", "") or ""),
        entry_fill_price=_opt_float(entry.get("fill")),
        exit_decision_timestamp=str(latch.get("exit_latched_at") or ""),
        exit_decision_bid=_opt_float(exit_decision_bid or latch.get("exit_trigger_bid")),
        exit_fill_timestamp=datetime.now(timezone.utc).isoformat(),
        exit_fill_price=_opt_float(exit_fill_price if exit_fill_price is not None else exit_b.get("fill")),
        gross_entry_cost=_opt_float(entry.get("premium")),
        gross_exit_proceeds=_opt_float(exit_b.get("gross_premium")),
        gross_pnl=_opt_float(econ.get("gross_pnl")),
        entry_fees=_opt_float(econ.get("opening_fees")),
        exit_fees=_opt_float(econ.get("closing_fees")),
        total_fees=_opt_float(econ.get("total_fees")),
        net_pnl=_opt_float(econ.get("net_pnl")),
        net_pnl_pct=_opt_float(econ.get("net_pnl_pct")),
        max_liquidatable_pnl_pct=_opt_float(
            retention.get("max_liquidatable_pnl_pct") or osnap.get("max_pnl_pct")
        ),
        max_analytical_pnl_pct=_opt_float(osnap.get("max_analytical_pnl_pct")),
        bid_hwm=_opt_float(osnap.get("hwm_bid") or latch.get("exit_trigger_hwm_bid")),
        exit_reason=str(exit_reason or ""),
        broker_order_id=str(broker_order_id or ""),
        quote_source=str(quote_source or exit_b.get("exit_price_source") or ""),
        quote_age_ms=quote_age_ms,
        decision_to_dispatch_ms=_opt_float(latch.get("decision_to_latch_ms")),
        dispatch_to_fill_ms=_opt_float(meta.get("dispatch_to_fill_ms")),
        close_lifecycle_id=lifecycle_id,
        idempotency_key=key,
        market_session_date=us_market_session_date(),
        profit_retention=dict(retention) if isinstance(retention, dict) else {},
        economic_summary=dict(economic_summary),
    )


def _opt_float(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def finalize_canonical_close_once(
    *,
    position: Any,
    economic_summary: Dict[str, Any],
    exit_reason: str = "",
    exit_fill_price: Optional[float] = None,
    mode: str = "",
    app: str = "",
    broker_order_id: str = "",
    is_partial: bool = False,
) -> Tuple[Optional[CanonicalClosedTrade], bool]:
    """Return (canonical, is_new). Partial fills never finalize.

    Exactly-once: duplicate callbacks with the same idempotency key return
    the prior object with is_new=False and do not re-credit balance.
    """
    if is_partial:
        meta = getattr(position, "metadata", None) or {}
        if isinstance(meta, dict):
            meta["pending_close_partial"] = True
            meta["close_status"] = "PARTIAL"
        return None, False

    ct = build_canonical_closed_trade(
        position=position,
        economic_summary=economic_summary,
        exit_reason=exit_reason,
        exit_fill_price=exit_fill_price,
        mode=mode,
        app=app,
        broker_order_id=broker_order_id,
    )
    key = ct.idempotency_key
    meta = getattr(position, "metadata", None) or {}
    if not isinstance(meta, dict):
        meta = {}
        try:
            position.metadata = meta
        except Exception:
            pass

    if key in _finalized_keys or meta.get("canonical_close_finalized"):
        prior = meta.get("canonical_closed_trade")
        if isinstance(prior, dict):
            log.info("CANONICAL_CLOSE_DEDUPED | key=%s | trade_id=%s", key, ct.trade_id)
            return CanonicalClosedTrade(**{k: prior.get(k) for k in CanonicalClosedTrade.__dataclass_fields__}), False
        return ct, False

    _finalized_keys.add(key)
    meta["canonical_close_finalized"] = True
    meta["canonical_closed_trade"] = ct.to_dict()
    meta["close_status"] = "FILLED"
    meta.pop("pending_close_partial", None)
    try:
        position.metadata = meta
    except Exception:
        pass
    log.info(
        "CANONICAL_CLOSE_FINALIZED | key=%s | trade_id=%s | net_pnl=%s | exit_reason=%s | session=%s",
        key,
        ct.trade_id,
        ct.net_pnl,
        ct.exit_reason,
        ct.market_session_date,
    )
    return ct, True


def clear_finalization_cache_for_tests() -> None:
    _finalized_keys.clear()
