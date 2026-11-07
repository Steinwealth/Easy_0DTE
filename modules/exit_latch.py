"""Exit latch — persist breached protective exits across sync/quote failures.

A latched exit must not be reevaluated back into HOLD. Dispatch retries follow
the existing close state machine; duplicate broker orders are prevented.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)


@dataclass
class ExitLatchState:
    exit_latched: bool = False
    exit_latched_reason: str = ""
    exit_latched_at: float = 0.0
    exit_trigger_bid: Optional[float] = None
    exit_trigger_ask: Optional[float] = None
    exit_trigger_hwm_bid: Optional[float] = None
    exit_trigger_quote_timestamp: Optional[str] = None
    exit_trigger_premium: Optional[float] = None
    dispatch_ownership_gated: bool = False
    sync_block_started_at: float = 0.0
    sync_block_duration_ms: float = 0.0
    last_confirmed_quantity: int = 0
    last_successful_sync_age_sec: Optional[float] = None
    # Distinct counters — never conflate monitor re-observation with broker submits.
    latch_observation_count: int = 0
    dispatch_attempt_count: int = 0
    broker_submit_attempt_count: int = 0
    ownership_sync_attempt_count: int = 0
    # Deprecated alias of broker_submit_attempt_count (kept for older readers).
    pending_close_retry_count: int = 0
    dispatch_started_at: float = 0.0
    decision_to_latch_ms: float = 0.0
    threshold_breached_at: float = 0.0
    extras: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        # Keep alias aligned for any consumer still reading the old key.
        self.pending_close_retry_count = int(self.broker_submit_attempt_count or 0)
        return asdict(self)

    @classmethod
    def from_mapping(cls, raw: Any) -> "ExitLatchState":
        if isinstance(raw, ExitLatchState):
            return raw
        d = raw if isinstance(raw, dict) else {}
        legacy_retry = int(d.get("pending_close_retry_count") or 0)
        broker_submits = int(d.get("broker_submit_attempt_count") or 0)
        if broker_submits <= 0 and legacy_retry > 0:
            broker_submits = legacy_retry
        return cls(
            exit_latched=bool(d.get("exit_latched")),
            exit_latched_reason=str(d.get("exit_latched_reason") or ""),
            exit_latched_at=float(d.get("exit_latched_at") or 0.0),
            exit_trigger_bid=_opt_float(d.get("exit_trigger_bid")),
            exit_trigger_ask=_opt_float(d.get("exit_trigger_ask")),
            exit_trigger_hwm_bid=_opt_float(d.get("exit_trigger_hwm_bid")),
            exit_trigger_quote_timestamp=(
                str(d.get("exit_trigger_quote_timestamp"))
                if d.get("exit_trigger_quote_timestamp") is not None
                else None
            ),
            exit_trigger_premium=_opt_float(d.get("exit_trigger_premium")),
            dispatch_ownership_gated=bool(d.get("dispatch_ownership_gated")),
            sync_block_started_at=float(d.get("sync_block_started_at") or 0.0),
            sync_block_duration_ms=float(d.get("sync_block_duration_ms") or 0.0),
            last_confirmed_quantity=int(d.get("last_confirmed_quantity") or 0),
            last_successful_sync_age_sec=_opt_float(d.get("last_successful_sync_age_sec")),
            latch_observation_count=int(d.get("latch_observation_count") or 0),
            dispatch_attempt_count=int(d.get("dispatch_attempt_count") or 0),
            broker_submit_attempt_count=broker_submits,
            ownership_sync_attempt_count=int(d.get("ownership_sync_attempt_count") or 0),
            pending_close_retry_count=broker_submits,
            dispatch_started_at=float(d.get("dispatch_started_at") or 0.0),
            decision_to_latch_ms=float(d.get("decision_to_latch_ms") or 0.0),
            threshold_breached_at=float(d.get("threshold_breached_at") or 0.0),
            extras=dict(d.get("extras") or {}) if isinstance(d.get("extras"), dict) else {},
        )


def _opt_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _legacy_pending_close(meta: Dict[str, Any]) -> Dict[str, Any]:
    pc = meta.get("pending_close")
    return pc if isinstance(pc, dict) else {}


def _reconstruct_latch_from_legacy(meta: Dict[str, Any]) -> Optional[ExitLatchState]:
    """Rebuild modern latch from top-level / pending_close keys after restart."""
    top_latched = bool(meta.get("exit_latched"))
    reason = str(meta.get("pending_exit_reason") or "").strip()
    premium = _opt_float(meta.get("pending_exit_premium"))
    pc = _legacy_pending_close(meta)
    pc_reason = str(
        pc.get("close_reason") or pc.get("exit_reason") or pc.get("reason") or ""
    ).strip()
    pc_id = str(pc.get("close_lifecycle_id") or pc.get("lifecycle_id") or "").strip()
    broker_oid = str(pc.get("broker_order_id") or "").strip()
    triggered_raw = (
        pc.get("triggered_at")
        or pc.get("latched_at")
        or meta.get("exit_latched_at")
        or meta.get("threshold_breached_at")
    )

    if not (
        top_latched
        or reason
        or pc_reason
        or pc_id
        or broker_oid
        or bool(pc.get("fill_confirmed") is False and pc)
    ):
        # pending_close with any close intent still counts
        if not pc:
            return None
        if not (pc_id or pc_reason or broker_oid or top_latched or reason):
            return None

    now = time.time()
    breached = _opt_float(triggered_raw)
    if breached is None or breached <= 0:
        breached = now
    resolved_reason = reason or pc_reason or ("pending_close" if pc else "exit_latched")
    extras: Dict[str, Any] = {"reconstructed_from": "legacy_pending_close"}
    if pc_id:
        extras["close_lifecycle_id"] = pc_id
    if broker_oid:
        extras["broker_order_id"] = broker_oid

    return ExitLatchState(
        exit_latched=True,
        exit_latched_reason=resolved_reason,
        exit_latched_at=float(breached),
        exit_trigger_premium=premium,
        exit_trigger_bid=premium,
        threshold_breached_at=float(breached),
        decision_to_latch_ms=0.0,
        extras=extras,
    )


def load_exit_latch(position: Any) -> ExitLatchState:
    meta = getattr(position, "metadata", None)
    if not isinstance(meta, dict):
        return ExitLatchState()
    raw = meta.get("exit_latch")
    if isinstance(raw, dict) and raw.get("exit_latched"):
        return ExitLatchState.from_mapping(raw)

    reconstructed = _reconstruct_latch_from_legacy(meta)
    if reconstructed is None:
        return ExitLatchState()

    # Prefer any partial modern fields already present.
    if isinstance(raw, dict):
        partial = ExitLatchState.from_mapping(raw)
        reconstructed.latch_observation_count = int(partial.latch_observation_count or 0)
        reconstructed.dispatch_attempt_count = int(partial.dispatch_attempt_count or 0)
        reconstructed.broker_submit_attempt_count = int(
            partial.broker_submit_attempt_count or partial.pending_close_retry_count or 0
        )
        reconstructed.ownership_sync_attempt_count = int(
            partial.ownership_sync_attempt_count or 0
        )
        reconstructed.pending_close_retry_count = reconstructed.broker_submit_attempt_count
        if partial.dispatch_ownership_gated:
            reconstructed.dispatch_ownership_gated = True
            reconstructed.sync_block_started_at = float(partial.sync_block_started_at or 0.0)
            reconstructed.sync_block_duration_ms = float(partial.sync_block_duration_ms or 0.0)
            reconstructed.last_confirmed_quantity = int(partial.last_confirmed_quantity or 0)

    persist_exit_latch(position, reconstructed)
    log.info(
        "EXIT_LATCH_RECONSTRUCTED | trade_id=%s | reason=%s | premium=%s",
        getattr(position, "position_id", ""),
        reconstructed.exit_latched_reason,
        reconstructed.exit_trigger_premium,
    )
    return reconstructed


def persist_exit_latch(position: Any, latch: ExitLatchState) -> None:
    meta = getattr(position, "metadata", None)
    if not isinstance(meta, dict):
        try:
            position.metadata = {}
            meta = position.metadata
        except Exception:
            return
    meta["exit_latch"] = latch.to_dict()
    # Mirror into lifecycle-friendly keys for restart recovery / pending retry
    if latch.exit_latched:
        meta["exit_latched"] = True
        meta["pending_exit_reason"] = latch.exit_latched_reason
        if latch.exit_trigger_premium is not None:
            meta["pending_exit_premium"] = latch.exit_trigger_premium


def latch_exit_decision(
    position: Any,
    *,
    reason: str,
    bid: Optional[float] = None,
    ask: Optional[float] = None,
    hwm_bid: Optional[float] = None,
    quote_timestamp: Optional[str] = None,
    premium: Optional[float] = None,
    threshold_breached_at: Optional[float] = None,
) -> ExitLatchState:
    """Idempotent latch: first breach wins; later prices recorded separately."""
    existing = load_exit_latch(position)
    now = time.time()
    if existing.exit_latched:
        existing.extras["later_bid"] = bid
        existing.extras["later_ask"] = ask
        existing.extras["later_seen_at"] = now
        # Re-observation only — not a broker retry.
        existing.latch_observation_count = int(existing.latch_observation_count or 0) + 1
        persist_exit_latch(position, existing)
        return existing

    breached = float(threshold_breached_at or now)
    latch = ExitLatchState(
        exit_latched=True,
        exit_latched_reason=str(reason or "exit"),
        exit_latched_at=now,
        exit_trigger_bid=bid,
        exit_trigger_ask=ask,
        exit_trigger_hwm_bid=hwm_bid,
        exit_trigger_quote_timestamp=quote_timestamp,
        exit_trigger_premium=premium if premium is not None else bid,
        threshold_breached_at=breached,
        decision_to_latch_ms=max(0.0, (now - breached) * 1000.0),
        latch_observation_count=1,
    )
    persist_exit_latch(position, latch)
    log.info(
        "EXIT_LATCHED | trade_id=%s | reason=%s | bid=%s | hwm_bid=%s | decision_to_latch_ms=%.1f",
        getattr(position, "position_id", ""),
        latch.exit_latched_reason,
        latch.exit_trigger_bid,
        latch.exit_trigger_hwm_bid,
        latch.decision_to_latch_ms,
    )
    try:
        from modules.exit_observation_telemetry import log_mfe_decision, mfe_decision_fields

        meta = getattr(position, "metadata", None)
        osnap = meta.get("option_stealth") if isinstance(meta, dict) else None
        osnap = osnap if isinstance(osnap, dict) else {}
        entry = None
        try:
            entry = float(
                (osnap.get("engine") or {}).get("entry_premium")
                or getattr(position, "entry_price", None)
                or 0.0
            )
        except Exception:
            entry = None
        log_mfe_decision(
            "EXIT_LATCHED",
            mfe_decision_fields(
                trade_id=str(getattr(position, "position_id", "") or ""),
                current_bid=bid,
                entry_premium=entry,
                hwm_bid=hwm_bid,
                hwm_pnl_pct=osnap.get("max_pnl_pct"),
                current_drawdown_pp=None,
                quote_age=None,
                quote_source=str(osnap.get("last_premium_source") or ""),
                confirmation_count=osnap.get("mfe_giveback_gap_confirm_count"),
            ),
        )
    except Exception:
        pass
    return latch


def mark_dispatch_ownership_gated(
    position: Any,
    *,
    last_confirmed_quantity: int = 0,
    last_successful_sync_age_sec: Optional[float] = None,
) -> ExitLatchState:
    latch = load_exit_latch(position)
    if not latch.exit_latched:
        return latch
    now = time.time()
    if latch.sync_block_started_at <= 0:
        latch.sync_block_started_at = now
    latch.dispatch_ownership_gated = True
    latch.sync_block_duration_ms = max(0.0, (now - latch.sync_block_started_at) * 1000.0)
    latch.last_confirmed_quantity = int(last_confirmed_quantity or 0)
    latch.last_successful_sync_age_sec = last_successful_sync_age_sec
    latch.ownership_sync_attempt_count = int(latch.ownership_sync_attempt_count or 0) + 1
    persist_exit_latch(position, latch)
    log.warning(
        "EXIT_LATCHED_BUT_SYNC_BLOCKED | trade_id=%s | sync_block_duration_ms=%.1f | qty=%s | "
        "sync_age_sec=%s | ownership_sync_attempt_count=%s",
        getattr(position, "position_id", ""),
        latch.sync_block_duration_ms,
        latch.last_confirmed_quantity,
        latch.last_successful_sync_age_sec,
        latch.ownership_sync_attempt_count,
    )
    return latch


def clear_ownership_gate(position: Any) -> ExitLatchState:
    """Clear ownership gate after successful broker sync so latched exits may dispatch."""
    latch = load_exit_latch(position)
    if not latch.exit_latched:
        return latch
    if latch.dispatch_ownership_gated:
        latch.dispatch_ownership_gated = False
        latch.extras["ownership_gate_cleared_at"] = time.time()
        persist_exit_latch(position, latch)
        log.info(
            "EXIT_OWNERSHIP_GATE_CLEARED | trade_id=%s | reason=%s",
            getattr(position, "position_id", ""),
            latch.exit_latched_reason,
        )
    return latch


def should_defer_broker_dispatch(position: Any, *, ownership_gate_active: bool) -> bool:
    """True when Live STC must wait (ownership unknown or open close order in-flight)."""
    latch = load_exit_latch(position)
    if ownership_gate_active and latch.exit_latched:
        return True
    if latch.dispatch_ownership_gated and latch.exit_latched:
        return True
    meta = getattr(position, "metadata", None)
    if isinstance(meta, dict):
        pc = meta.get("pending_close")
        if isinstance(pc, dict) and str(pc.get("broker_order_id") or "").strip():
            if not bool(pc.get("fill_confirmed") or meta.get("close_fill_confirmed")):
                return True
    return False


def mark_dispatch_started(position: Any) -> ExitLatchState:
    latch = load_exit_latch(position)
    now = time.time()
    latch.dispatch_started_at = now
    latch.dispatch_ownership_gated = False
    latch.dispatch_attempt_count = int(latch.dispatch_attempt_count or 0) + 1
    persist_exit_latch(position, latch)
    try:
        from modules.exit_observation_telemetry import log_mfe_decision, mfe_decision_fields

        meta = getattr(position, "metadata", None)
        osnap = meta.get("option_stealth") if isinstance(meta, dict) else None
        osnap = osnap if isinstance(osnap, dict) else {}
        log_mfe_decision(
            "EXIT_DISPATCHED",
            mfe_decision_fields(
                trade_id=str(getattr(position, "position_id", "") or ""),
                current_bid=latch.exit_trigger_bid,
                entry_premium=None,
                hwm_bid=latch.exit_trigger_hwm_bid,
                hwm_pnl_pct=osnap.get("max_pnl_pct"),
                quote_source=str(osnap.get("last_premium_source") or ""),
                confirmation_count=osnap.get("mfe_giveback_gap_confirm_count"),
            ),
        )
    except Exception:
        pass
    return latch


def mark_close_order_submitted(position: Any, *, broker_order_id: str = "") -> None:
    """Record in-flight Live STC so retries do not place a duplicate order.

    Increments broker_submit_attempt_count only when a real broker order id is recorded.
    """
    meta = getattr(position, "metadata", None)
    if not isinstance(meta, dict):
        try:
            position.metadata = {}
            meta = position.metadata
        except Exception:
            return
    oid = str(broker_order_id or "").strip()
    if not oid:
        return
    pc = meta.get("pending_close") if isinstance(meta.get("pending_close"), dict) else {}
    pc["broker_order_id"] = oid
    pc["submitted_at"] = time.time()
    pc["fill_confirmed"] = False
    meta["pending_close"] = pc
    meta["close_dispatch_inflight"] = True
    latch = load_exit_latch(position)
    if latch.exit_latched:
        latch.dispatch_started_at = float(pc["submitted_at"])
        latch.broker_submit_attempt_count = int(latch.broker_submit_attempt_count or 0) + 1
        latch.pending_close_retry_count = latch.broker_submit_attempt_count
        persist_exit_latch(position, latch)


def clear_exit_latch(position: Any) -> None:
    meta = getattr(position, "metadata", None)
    if isinstance(meta, dict):
        meta.pop("exit_latch", None)
        meta.pop("exit_latched", None)
        meta.pop("close_dispatch_inflight", None)
