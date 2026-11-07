"""Live Mode close-attempt telemetry and safe order escalation helpers."""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


@dataclass
class LiveCloseAttemptRecord:
    trade_id: str = ""
    close_lifecycle_id: str = ""
    attempt_number: int = 0
    attempt_started_at: float = 0.0
    order_type: str = ""
    limit_price: Optional[float] = None
    decision_bid: Optional[float] = None
    current_bid: Optional[float] = None
    current_ask: Optional[float] = None
    spread: Optional[float] = None
    requested_quantity: int = 0
    last_confirmed_owned_quantity: int = 0
    previously_filled_quantity: int = 0
    remaining_quantity: int = 0
    broker_preview_result: str = ""
    broker_submit_result: str = ""
    broker_order_id: str = ""
    broker_order_status: str = ""
    fill_quantity: int = 0
    fill_price: Optional[float] = None
    elapsed_since_latch_ms: Optional[float] = None
    exit_reason: str = ""
    extras: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# Broker order status classes for safe escalation
WORKING = frozenset({"WORKING", "OPEN", "PENDING", "PARTIAL", "PARTIALLY_FILLED", "QUEUED"})
TERMINAL_OK = frozenset({"FILLED", "EXECUTED", "COMPLETE"})
RETRYABLE = frozenset({"REJECTED", "CANCELLED", "CANCELED", "EXPIRED"})
UNKNOWN = frozenset({"UNKNOWN", "STATUS_UNKNOWN", "TIMEOUT", ""})


def classify_broker_order_status(status: str) -> str:
    s = str(status or "").strip().upper()
    if s in TERMINAL_OK or "FILL" in s and "PARTIAL" not in s:
        return "filled"
    if s in WORKING or "PARTIAL" in s:
        return "working"
    if s in RETRYABLE:
        return "retryable"
    if s in UNKNOWN or s == "TIMEOUT":
        return "unknown"
    return "unknown"


def remaining_close_quantity(
    *,
    owned_quantity: int,
    previously_filled: int,
    requested: int,
) -> int:
    owned = max(0, int(owned_quantity or 0))
    filled = max(0, int(previously_filled or 0))
    req = max(0, int(requested or 0))
    rem = max(0, owned - filled)
    if req > 0:
        rem = min(rem, req)
    return rem


def should_submit_close_attempt(
    *,
    prior_order_status: str,
    remaining_qty: int,
    ownership_confirmed: bool,
) -> tuple:
    """Return (allow_submit, reason). Never submit when unknown or working."""
    if not ownership_confirmed:
        return False, "ownership_unconfirmed"
    if remaining_qty <= 0:
        return False, "nothing_remaining"
    if not str(prior_order_status or "").strip():
        return True, "first_attempt"
    klass = classify_broker_order_status(prior_order_status)
    if klass == "working":
        return False, "prior_order_working"
    if klass == "filled":
        return False, "prior_order_filled"
    if klass == "unknown":
        return False, "prior_order_unknown_reconcile_first"
    if klass == "retryable":
        return True, "prior_retryable"
    return False, f"unclassified:{klass}"


def may_escalate_to_market(
    *,
    attempt_number: int,
    prior_order_status: str,
    ownership_confirmed: bool,
    remaining_qty: int,
    policy_allows_market: bool,
) -> bool:
    """MARKET only after reconciling prior order and confirming remaining qty."""
    if not policy_allows_market:
        return False
    if attempt_number < 2:
        return False
    if not ownership_confirmed or remaining_qty <= 0:
        return False
    klass = classify_broker_order_status(prior_order_status)
    # Must not escalate while working or unknown
    if klass in ("working", "unknown", "filled"):
        return False
    return klass in ("retryable",) or not str(prior_order_status or "").strip()


def record_live_close_attempt(position: Any, record: LiveCloseAttemptRecord) -> None:
    meta = getattr(position, "metadata", None)
    if not isinstance(meta, dict):
        try:
            position.metadata = {}
            meta = position.metadata
        except Exception:
            return
    attempts: List[Dict[str, Any]] = list(meta.get("live_close_attempts") or [])
    attempts.append(record.to_dict())
    meta["live_close_attempts"] = attempts
    meta["last_live_close_attempt"] = record.to_dict()
    log.info(
        "LIVE_CLOSE_ATTEMPT_DETAIL | trade_id=%s | lifecycle=%s | attempt=%s | order_type=%s | "
        "limit=%s | decision_bid=%s | bid=%s | ask=%s | spread=%s | req_qty=%s | owned=%s | "
        "filled=%s | remaining=%s | submit=%s | order_id=%s | status=%s | fill_qty=%s | "
        "fill_px=%s | elapsed_latch_ms=%s | exit_reason=%s",
        record.trade_id,
        record.close_lifecycle_id,
        record.attempt_number,
        record.order_type,
        record.limit_price,
        record.decision_bid,
        record.current_bid,
        record.current_ask,
        record.spread,
        record.requested_quantity,
        record.last_confirmed_owned_quantity,
        record.previously_filled_quantity,
        record.remaining_quantity,
        record.broker_submit_result,
        record.broker_order_id,
        record.broker_order_status,
        record.fill_quantity,
        record.fill_price,
        record.elapsed_since_latch_ms,
        record.exit_reason,
    )


def mark_close_status_unknown(position: Any, *, reason: str = "submit_timeout") -> None:
    meta = getattr(position, "metadata", None)
    if not isinstance(meta, dict):
        try:
            position.metadata = {}
            meta = position.metadata
        except Exception:
            return
    meta["close_status"] = "STATUS_UNKNOWN"
    meta["close_status_unknown_reason"] = reason
    meta["close_status_unknown_at"] = time.time()
    log.warning(
        "LIVE_CLOSE_STATUS_UNKNOWN | trade_id=%s | reason=%s | reconcile_before_retry=true",
        getattr(position, "position_id", ""),
        reason,
    )
