"""One sealed research payload per 20p event and per exit event.

A later tick may retry the write. It may not rebuild the market state.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

_SEALED_20P: Dict[str, Dict[str, Any]] = {}
_SEALED_EXIT: Dict[str, Dict[str, Any]] = {}
_PERSISTED_20P: set = set()
_PERSISTED_EXIT: set = set()
# Clocks created in this process. A clock that arrives from saved position
# state after a restart is not in these sets, so it cannot be paired with a
# later market rebuild.
_BORN_20P: set = set()
_BORN_EXIT: set = set()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def note_20p_event(osnap: Dict[str, Any]) -> str:
    """Record the first +20% instant. Later ticks keep this clock."""
    if not isinstance(osnap, dict):
        return _now()
    if osnap.get("po_20p_event_at") in (None, ""):
        osnap["po_20p_event_at"] = _now()
        _BORN_20P.add(str(osnap["po_20p_event_at"]))
    return str(osnap["po_20p_event_at"])


def clock_born_this_process_20p(event_at: Optional[str]) -> bool:
    return str(event_at or "") in _BORN_20P


def clock_born_this_process_exit(event_at: Optional[str]) -> bool:
    return str(event_at or "") in _BORN_EXIT


def remember_exit_clock(event_at: Optional[str]) -> None:
    if event_at not in (None, ""):
        _BORN_EXIT.add(str(event_at))


def sealed_20p(trade_id: str) -> Optional[Dict[str, Any]]:
    payload = _SEALED_20P.get(str(trade_id or "").strip())
    return payload if isinstance(payload, dict) else None


def remember_20p(trade_id: str, payload: Optional[Dict[str, Any]]) -> None:
    tid = str(trade_id or "").strip()
    if tid and isinstance(payload, dict):
        _SEALED_20P[tid] = payload


def persisted_20p(trade_id: str) -> bool:
    return str(trade_id or "").strip() in _PERSISTED_20P


def mark_persisted_20p(trade_id: str) -> None:
    tid = str(trade_id or "").strip()
    if tid:
        _PERSISTED_20P.add(tid)
        # The durable object is the record. Drop the in-memory copy.
        _SEALED_20P.pop(tid, None)


def note_exit_event(holder: Dict[str, Any]) -> str:
    if not isinstance(holder, dict):
        return _now()
    if holder.get("po_exit_event_at") in (None, ""):
        holder["po_exit_event_at"] = _now()
    return str(holder["po_exit_event_at"])


def sealed_exit(trade_id: str) -> Optional[Dict[str, Any]]:
    payload = _SEALED_EXIT.get(str(trade_id or "").strip())
    return payload if isinstance(payload, dict) else None


def remember_exit(trade_id: str, payload: Optional[Dict[str, Any]]) -> None:
    tid = str(trade_id or "").strip()
    if tid and isinstance(payload, dict):
        _SEALED_EXIT[tid] = payload


def persisted_exit(trade_id: str) -> bool:
    return str(trade_id or "").strip() in _PERSISTED_EXIT


def mark_persisted_exit(trade_id: str) -> None:
    tid = str(trade_id or "").strip()
    if tid:
        _PERSISTED_EXIT.add(tid)
        _SEALED_EXIT.pop(tid, None)


_SEALED_EVENT: Dict[str, Dict[str, Dict[str, Any]]] = {}
_PERSISTED_EVENT: Dict[str, set] = {}
_BORN_EVENT: Dict[str, set] = {}


def note_event(osnap: Dict[str, Any], snap_type: str, clock_key: Optional[str] = None) -> str:
    """Record the first instant for a mid-trade snapshot type. Later ticks keep it."""
    key = str(clock_key or f"po_{snap_type}_event_at")
    if not isinstance(osnap, dict):
        return _now()
    if osnap.get(key) in (None, ""):
        osnap[key] = _now()
        _BORN_EVENT.setdefault(str(snap_type), set()).add(str(osnap[key]))
    return str(osnap[key])


def clock_born_this_process_event(snap_type: str, event_at: Optional[str]) -> bool:
    return str(event_at or "") in _BORN_EVENT.get(str(snap_type), set())


def sealed_event(snap_type: str, identity: str) -> Optional[Dict[str, Any]]:
    payload = _SEALED_EVENT.get(str(snap_type), {}).get(str(identity or "").strip())
    return payload if isinstance(payload, dict) else None


def remember_event(snap_type: str, identity: str, payload: Optional[Dict[str, Any]]) -> None:
    tid = str(identity or "").strip()
    if tid and isinstance(payload, dict):
        _SEALED_EVENT.setdefault(str(snap_type), {})[tid] = payload


def persisted_event(snap_type: str, identity: str) -> bool:
    return str(identity or "").strip() in _PERSISTED_EVENT.get(str(snap_type), set())


def mark_persisted_event(snap_type: str, identity: str) -> None:
    tid = str(identity or "").strip()
    if tid:
        _PERSISTED_EVENT.setdefault(str(snap_type), set()).add(tid)
        _SEALED_EVENT.get(str(snap_type), {}).pop(tid, None)


def reset_event_seals_for_tests() -> None:
    _SEALED_20P.clear()
    _SEALED_EXIT.clear()
    _PERSISTED_20P.clear()
    _PERSISTED_EXIT.clear()
    _BORN_20P.clear()
    _BORN_EXIT.clear()
    _SEALED_EVENT.clear()
    _PERSISTED_EVENT.clear()
    _BORN_EVENT.clear()
