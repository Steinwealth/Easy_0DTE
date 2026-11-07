"""Bounded Telegram *notification* flush. Not the execution ledger.

Queue rows are exit-alert retries only. Bound / timeout / max-attempt drops
discard notifications. They must never delete or rewrite fills, closed
positions, GCS objects, or other authoritative execution records.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

LEDGER_FLUSH_MAX_PENDING = 32
LEDGER_FLUSH_MAX_ATTEMPTS = 3
LEDGER_FLUSH_TIMEOUT_S = 8.0
KIND_LEDGER_CLOSED_EXIT = "ledger_closed_exit"

_CLAIMED_ATTR = "_ledger_alert_events_claimed"
_SENT_ATTR = "_ledger_alert_events_sent"


def alert_event_id(item: Optional[Dict[str, Any]]) -> str:
    """Stable id for one notification. Same pos_id + different event stays distinct."""
    row = item if isinstance(item, dict) else {}
    explicit = str(row.get("event_id") or "").strip()
    if explicit:
        return explicit
    kind = str(row.get("kind") or KIND_LEDGER_CLOSED_EXIT).strip() or KIND_LEDGER_CLOSED_EXIT
    pid = str(row.get("pos_id") or "").strip()
    extra = str(row.get("event_key") or row.get("closed_at") or "").strip()
    if extra:
        return f"{kind}:{pid}:{extra}"
    return f"{kind}:{pid}"


def _claimed(owner: Any) -> set:
    got = getattr(owner, _CLAIMED_ATTR, None)
    if not isinstance(got, set):
        got = set()
        setattr(owner, _CLAIMED_ATTR, got)
    return got


def _sent(owner: Any) -> set:
    got = getattr(owner, _SENT_ATTR, None)
    if not isinstance(got, set):
        got = set()
        setattr(owner, _SENT_ATTR, got)
    return got


def acquire_send_claim(owner: Any, event_id: str) -> str:
    """Return sent / claimed / acquired. Claimed after timeout must not resend."""
    eid = str(event_id or "").strip()
    if not eid:
        return "sent"
    if eid in _sent(owner):
        return "sent"
    claimed = _claimed(owner)
    if eid in claimed:
        return "claimed"
    claimed.add(eid)
    return "acquired"


def mark_event_sent(owner: Any, event_id: str) -> None:
    eid = str(event_id or "").strip()
    if not eid:
        return
    _sent(owner).add(eid)
    _claimed(owner).discard(eid)


def release_send_claim(owner: Any, event_id: str) -> None:
    """Failed before a confirmed send — allow a later retry."""
    _claimed(owner).discard(str(event_id or "").strip())


def enqueue_ledger_closed_alert(
    pending: Optional[List[Dict[str, Any]]],
    item: Dict[str, Any],
    *,
    max_pending: int = LEDGER_FLUSH_MAX_PENDING,
) -> List[Dict[str, Any]]:
    """FIFO notification enqueue. Drops the oldest *alert*, never a ledger row."""
    queue: List[Dict[str, Any]] = pending if isinstance(pending, list) else []
    row = dict(item or {})
    pid = str(row.get("pos_id") or "").strip()
    if not pid:
        return queue
    row["pos_id"] = pid
    row.setdefault("kind", KIND_LEDGER_CLOSED_EXIT)
    row.setdefault("attempts", 0)
    row["event_id"] = alert_event_id(row)
    eid = row["event_id"]
    for existing in queue:
        if alert_event_id(existing) == eid:
            return queue
    queue.append(row)
    while len(queue) > int(max_pending):
        dropped = queue.pop(0)
        log.warning(
            "LEDGER_CLOSED_EXIT_ALERT_DROP | reason=queue_bound | "
            "notification_only=true | trade_id=%s | event_id=%s | pending=%d",
            dropped.get("pos_id"),
            dropped.get("event_id"),
            len(queue),
        )
    return queue


def take_flush_batch(pending: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """Drain FIFO snapshot. Callers requeue failures after the batch."""
    if not isinstance(pending, list) or not pending:
        return []
    batch = list(pending)
    pending.clear()
    return batch


def requeue_failed(
    pending: Optional[List[Dict[str, Any]]],
    failed: List[Dict[str, Any]],
    *,
    max_attempts: int = LEDGER_FLUSH_MAX_ATTEMPTS,
    max_pending: int = LEDGER_FLUSH_MAX_PENDING,
) -> List[Dict[str, Any]]:
    """Put failed notifications back at the tail if attempts remain. Drop otherwise."""
    queue: List[Dict[str, Any]] = pending if isinstance(pending, list) else []
    for item in failed or []:
        if not isinstance(item, dict):
            continue
        attempts = int(item.get("attempts") or 0) + 1
        item["attempts"] = attempts
        eid = alert_event_id(item)
        if attempts >= int(max_attempts):
            log.warning(
                "LEDGER_CLOSED_EXIT_ALERT_DROP | reason=max_attempts | "
                "notification_only=true | trade_id=%s | event_id=%s | attempts=%d",
                item.get("pos_id"),
                eid,
                attempts,
            )
            continue
        enqueue_ledger_closed_alert(queue, item, max_pending=max_pending)
    return queue


def _drop_remaining(remaining: List[Dict[str, Any]], item: Any) -> None:
    if remaining and remaining[0] is item:
        remaining.pop(0)
    elif item in remaining:
        remaining.remove(item)


async def flush_notification_batch(
    owner: Any,
    rows: List[Dict[str, Any]],
    *,
    pending: List[Dict[str, Any]],
    send: Callable[[Dict[str, Any]], Awaitable[bool]],
) -> int:
    """Send each notification once. Timeout keeps the claim so retry cannot duplicate."""
    remaining = list(rows)
    sent_n = 0
    failed: List[Dict[str, Any]] = []
    try:
        for item in rows:
            if not isinstance(item, dict):
                _drop_remaining(remaining, item)
                continue
            eid = alert_event_id(item)
            if not eid or eid.endswith(":"):
                _drop_remaining(remaining, item)
                continue
            gate = acquire_send_claim(owner, eid)
            if gate != "acquired":
                log.info(
                    "LEDGER_CLOSED_EXIT_ALERT_SKIP | reason=%s | notification_only=true | event_id=%s",
                    gate,
                    eid,
                )
                _drop_remaining(remaining, item)
                continue
            try:
                ok = await send(item)
            except asyncio.CancelledError:
                log.warning(
                    "LEDGER_CLOSED_EXIT_ALERT_DROP | reason=timeout_claimed | "
                    "notification_only=true | trade_id=%s | event_id=%s | retry_send=false",
                    item.get("pos_id"),
                    eid,
                )
                _drop_remaining(remaining, item)
                raise
            except Exception as exc:
                release_send_claim(owner, eid)
                failed.append(item)
                log.warning(
                    "LEDGER_CLOSED_EXIT_ALERT_FLUSH_FAILED | trade_id=%s | event_id=%s | error=%s",
                    item.get("pos_id"),
                    eid,
                    exc,
                )
            else:
                if ok:
                    mark_event_sent(owner, eid)
                    sent_n += 1
                else:
                    release_send_claim(owner, eid)
                    failed.append(item)
            _drop_remaining(remaining, item)
        requeue_failed(pending, failed)
        if rows:
            log.info(
                "LEDGER_CLOSED_EXIT_ALERT_FLUSH | queued=%d | recovered=%d | requeued=%d",
                len(rows),
                sent_n,
                len(pending),
            )
        return sent_n
    except asyncio.CancelledError:
        for item in remaining:
            enqueue_ledger_closed_alert(pending, item)
        raise


def offload_flush_task(
    owner: Any,
    flush: Callable[[], Awaitable[Any]],
    *,
    timeout_s: float = LEDGER_FLUSH_TIMEOUT_S,
) -> Optional[asyncio.Task]:
    """Single-flight create_task. Failures log; a done task does not block the next."""
    task = getattr(owner, "_ledger_closed_alert_flush_task", None)
    if task is not None and not task.done():
        return task

    async def _run() -> Any:
        try:
            return await asyncio.wait_for(flush(), timeout=float(timeout_s))
        except asyncio.TimeoutError:
            log.warning(
                "LEDGER_CLOSED_EXIT_ALERT_FLUSH_FAILED | reason=timeout | "
                "notification_only=true | timeout_s=%.1f",
                float(timeout_s),
            )
            return 0
        except Exception as exc:
            log.warning("LEDGER_CLOSED_EXIT_ALERT_FLUSH_FAILED | reason=task | error=%s", exc)
            return 0

    spawned = asyncio.create_task(_run())

    def _done(done: asyncio.Task) -> None:
        if done.cancelled():
            return
        try:
            exc = done.exception()
        except Exception:
            return
        if exc is not None:
            log.warning("LEDGER_CLOSED_EXIT_ALERT_FLUSH_FAILED | reason=done | error=%s", exc)

    spawned.add_done_callback(_done)
    owner._ledger_closed_alert_flush_task = spawned
    return spawned
