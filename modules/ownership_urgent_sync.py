"""Urgent ownership-gate recovery + SLA telemetry for Live Mode latched exits.

Safety gate is preserved: never submit STC while ownership is uncertain.
When latched + gated, trigger a bounded non-overlapping urgent sync.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Set

log = logging.getLogger(__name__)

# Dedup in-flight urgent syncs by trade_id
_urgent_inflight: Set[str] = set()


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None or str(raw).strip() == "":
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


@dataclass
class OwnershipSlaConfig:
    sync_target_sec: float = 2.0
    block_warning_sec: float = 3.0
    block_critical_sec: float = 5.0
    urgent_sync_timeout_sec: float = 2.0
    cert_failure_sec: float = 10.0


def resolve_ownership_sla_config() -> OwnershipSlaConfig:
    return OwnershipSlaConfig(
        sync_target_sec=max(0.1, _env_float("0DTE_OWNERSHIP_SYNC_TARGET_SEC", 2.0)),
        block_warning_sec=max(0.1, _env_float("0DTE_OWNERSHIP_BLOCK_WARNING_SEC", 3.0)),
        block_critical_sec=max(0.1, _env_float("0DTE_OWNERSHIP_BLOCK_CRITICAL_SEC", 5.0)),
        urgent_sync_timeout_sec=max(0.2, _env_float("0DTE_OWNERSHIP_URGENT_SYNC_TIMEOUT_SEC", 2.0)),
        cert_failure_sec=10.0,
    )


def classify_gate_duration(duration_sec: float, cfg: Optional[OwnershipSlaConfig] = None) -> str:
    cfg = cfg or resolve_ownership_sla_config()
    d = float(duration_sec or 0.0)
    if d > cfg.cert_failure_sec + 1e-9:
        return "certification_failure"
    if d > cfg.block_critical_sec + 1e-9:
        return "critical"
    if d > cfg.block_warning_sec + 1e-9:
        return "warning"
    if d <= cfg.sync_target_sec + 1e-9:
        return "expected"
    return "elevated"


def _meta(position: Any) -> Dict[str, Any]:
    m = getattr(position, "metadata", None)
    if not isinstance(m, dict):
        try:
            position.metadata = {}
            m = position.metadata
        except Exception:
            return {}
    return m


def _ownership_state(position: Any) -> Dict[str, Any]:
    meta = _meta(position)
    st = meta.get("ownership_recovery")
    if not isinstance(st, dict):
        st = {}
        meta["ownership_recovery"] = st
    return st


def ownership_event_fields(
    position: Any,
    *,
    app: str = "",
    mode: str = "",
    strategy: str = "",
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    from modules.exit_latch import load_exit_latch

    latch = load_exit_latch(position)
    st = _ownership_state(position)
    meta = _meta(position)
    pc = meta.get("pending_close") if isinstance(meta.get("pending_close"), dict) else {}
    now = time.time()
    started = float(st.get("ownership_gate_started_at") or latch.sync_block_started_at or 0.0)
    gate_ms = max(0.0, (now - started) * 1000.0) if started > 0 else float(latch.sync_block_duration_ms or 0.0)
    fields = {
        "app": app or str(os.getenv("APP_NAME") or os.getenv("K_SERVICE") or ""),
        "mode": mode or str(os.getenv("MODE") or ""),
        "strategy": strategy or str(getattr(position, "strategy_type", "") or ""),
        "trade_id": str(getattr(position, "position_id", "") or ""),
        "option_symbol": str(
            getattr(position, "option_symbol", None)
            or (meta.get("option_symbol") if isinstance(meta, dict) else None)
            or getattr(position, "symbol", "")
            or ""
        ),
        "exit_reason": latch.exit_latched_reason,
        "exit_latched_at": latch.exit_latched_at,
        "exit_trigger_bid": latch.exit_trigger_bid,
        "last_confirmed_quantity": st.get("last_confirmed_quantity", latch.last_confirmed_quantity),
        "sync_age_ms": st.get("last_successful_sync_age_ms"),
        "gate_duration_ms": gate_ms,
        "urgent_sync_attempt_count": int(st.get("urgent_sync_attempt_count") or 0),
        "broker_order_id": str(pc.get("broker_order_id") or ""),
    }
    if extra:
        fields.update(extra)
    return fields


def _log_event(token: str, fields: Dict[str, Any], *, level: int = logging.INFO) -> None:
    parts = [f"{k}={fields.get(k)}" for k in (
        "app", "mode", "strategy", "trade_id", "option_symbol", "exit_reason",
        "exit_latched_at", "exit_trigger_bid", "last_confirmed_quantity",
        "sync_age_ms", "gate_duration_ms", "urgent_sync_attempt_count", "broker_order_id",
    )]
    log.log(level, "%s | %s", token, " | ".join(parts))


def mark_ownership_blocked(
    position: Any,
    *,
    last_confirmed_quantity: int = 0,
    sync_age_sec: Optional[float] = None,
    app: str = "",
    mode: str = "",
    strategy: str = "",
) -> Dict[str, Any]:
    """Extend latch ownership gate + SLA classification (does not submit STC)."""
    from modules.exit_latch import mark_dispatch_ownership_gated, load_exit_latch

    latch = mark_dispatch_ownership_gated(
        position,
        last_confirmed_quantity=last_confirmed_quantity,
        last_successful_sync_age_sec=sync_age_sec,
    )
    st = _ownership_state(position)
    now = time.time()
    if not st.get("ownership_gate_started_at"):
        st["ownership_gate_started_at"] = float(latch.sync_block_started_at or now)
    st["ownership_gate_last_checked_at"] = now
    st["last_confirmed_quantity"] = int(last_confirmed_quantity or 0)
    if sync_age_sec is not None:
        st["last_successful_sync_age_ms"] = float(sync_age_sec) * 1000.0
    fields = ownership_event_fields(position, app=app, mode=mode, strategy=strategy)
    _log_event("EXIT_LATCHED_BUT_SYNC_BLOCKED", fields, level=logging.WARNING)

    cfg = resolve_ownership_sla_config()
    dur_sec = float(fields.get("gate_duration_ms") or 0.0) / 1000.0
    level = classify_gate_duration(dur_sec, cfg)
    st["sla_class"] = level
    if level == "warning":
        _log_event("OWNERSHIP_GATE_WARNING", fields, level=logging.WARNING)
    elif level in ("critical", "certification_failure"):
        _log_event("OWNERSHIP_GATE_CRITICAL", {**fields, "sla_class": level}, level=logging.ERROR)
    return st


def mark_ownership_cleared(
    position: Any,
    *,
    app: str = "",
    mode: str = "",
    strategy: str = "",
) -> Dict[str, Any]:
    from modules.exit_latch import clear_ownership_gate, load_exit_latch

    latch_before = load_exit_latch(position)
    was_gated = bool(latch_before.dispatch_ownership_gated)
    started = float((_ownership_state(position).get("ownership_gate_started_at") or 0.0))
    clear_ownership_gate(position)
    st = _ownership_state(position)
    now = time.time()
    st["ownership_gate_cleared_at"] = now
    st["urgent_sync_in_flight"] = False
    st["last_successful_sync_at"] = now
    st["last_successful_sync_age_ms"] = 0.0
    if was_gated and started > 0:
        st["dispatch_after_gate_ms"] = max(0.0, (now - started) * 1000.0)
    fields = ownership_event_fields(position, app=app, mode=mode, strategy=strategy)
    _log_event("OWNERSHIP_GATE_CLEARED", fields)
    if was_gated:
        _log_event(
            "EXIT_DISPATCHED_AFTER_OWNERSHIP_RECOVERY",
            {**fields, "dispatch_after_gate_ms": st.get("dispatch_after_gate_ms")},
        )
    return st


async def request_urgent_ownership_sync(
    system: Any,
    position: Any,
    *,
    app: str = "",
    mode: str = "",
    strategy: str = "",
) -> Dict[str, Any]:
    """Non-overlapping, timeout-bounded urgent broker synchronize for one latched trade."""
    trade_id = str(getattr(position, "position_id", "") or "")
    st = _ownership_state(position)
    cfg = resolve_ownership_sla_config()
    fields = ownership_event_fields(position, app=app, mode=mode, strategy=strategy)

    if trade_id and trade_id in _urgent_inflight:
        st["last_urgent_sync_result"] = "deduped_in_flight"
        return {"ok": False, "reason": "deduped_in_flight", "state": st}

    if bool(st.get("urgent_sync_in_flight")):
        st["last_urgent_sync_result"] = "already_in_flight"
        return {"ok": False, "reason": "already_in_flight", "state": st}

    if trade_id:
        _urgent_inflight.add(trade_id)
    st["urgent_sync_in_flight"] = True
    st["urgent_sync_attempt_count"] = int(st.get("urgent_sync_attempt_count") or 0) + 1
    st["ownership_gate_last_checked_at"] = time.time()
    _log_event("OWNERSHIP_URGENT_SYNC_STARTED", fields)

    result: Dict[str, Any] = {"ok": False, "reason": "unknown", "state": st}
    try:
        from modules.broker_position_synchronizer import (
            get_or_create_synchronizer,
            should_skip_normal_exits_for_stale_registry,
        )

        sync = get_or_create_synchronizer(system)

        async def _run():
            return await sync.synchronize()

        sync_result = await asyncio.wait_for(_run(), timeout=float(cfg.urgent_sync_timeout_sec))
        stale = bool(
            getattr(sync_result, "registry_stale", False)
            or getattr(sync_result, "sync_unavailable", False)
            or should_skip_normal_exits_for_stale_registry(system)
        )
        qty = int(getattr(position, "quantity", 0) or 0)
        st["last_confirmed_quantity"] = qty
        st["last_urgent_sync_result"] = "stale" if stale else "ok"
        if not stale:
            mark_ownership_cleared(position, app=app, mode=mode, strategy=strategy)
            _log_event(
                "OWNERSHIP_URGENT_SYNC_SUCCEEDED",
                ownership_event_fields(position, app=app, mode=mode, strategy=strategy),
            )
            result = {"ok": True, "reason": "synced", "state": st, "quantity": qty}
        else:
            _log_event(
                "OWNERSHIP_URGENT_SYNC_FAILED",
                {**ownership_event_fields(position, app=app, mode=mode, strategy=strategy),
                 "reason": getattr(sync_result, "reason", "stale")},
                level=logging.WARNING,
            )
            result = {"ok": False, "reason": "still_stale", "state": st}
    except asyncio.TimeoutError:
        st["last_urgent_sync_result"] = "timeout"
        _log_event(
            "OWNERSHIP_URGENT_SYNC_FAILED",
            {**fields, "reason": "timeout"},
            level=logging.WARNING,
        )
        result = {"ok": False, "reason": "timeout", "state": st}
    except Exception as exc:
        st["last_urgent_sync_result"] = f"error:{exc}"
        _log_event(
            "OWNERSHIP_URGENT_SYNC_FAILED",
            {**fields, "reason": str(exc)},
            level=logging.WARNING,
        )
        result = {"ok": False, "reason": str(exc), "state": st}
    finally:
        st["urgent_sync_in_flight"] = False
        if trade_id:
            _urgent_inflight.discard(trade_id)
    return result


def clear_urgent_inflight_for_tests() -> None:
    _urgent_inflight.clear()
