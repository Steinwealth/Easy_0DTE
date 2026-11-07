"""Durable identity for one Priority Optimizer research event.

The object name is the event. A retry of the same sealed payload is the same
object. A different payload for that identity is a conflict and is not written
over the first one.

Process memory is a cache of this store. The store survives a restart when the
bytes were uploaded. Quote caches are not in here.
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

_LOCK = threading.Lock()
_NAME_LOCKS_GUARD = threading.Lock()
_NAME_LOCKS: Dict[str, threading.Lock] = {}
_BODIES: Dict[str, str] = {}
_STATS: Dict[str, int] = {
    "attempts": 0,
    "created": 0,
    "identical": 0,
    "conflict": 0,
    "failed": 0,
}


def canonical_family(stage: str) -> str:
    text = str(stage or "")
    try:
        from modules.po_lifecycle_moments import family_from_stage

        family = family_from_stage(text)
        if family:
            return family
    except Exception:
        if "options_50p" in text:
            return "mfe_50p"
        if "20p" in text:
            return "mfe_20p"
    if "exited" in text or text.endswith("_exit"):
        return "exit"
    if "options_executed" in text:
        return "entry_executed"
    if "handoff" in text:
        return "entry_handoff"
    return ""


def _safe(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(value or ""))[:96]


def event_identity(payload: Dict[str, Any]) -> str:
    extra = payload.get("extra") if isinstance(payload.get("extra"), dict) else {}
    selected = payload.get("features_selected") or []
    row = selected[0] if selected and isinstance(selected[0], dict) else {}
    # Oct 23, 2026: a pullback ladder shot is its own object. ``po_event_identity``
    # (``<trade_id>__pullback_s{n}``) names it while extra.trade_id stays the real id.
    explicit = str(extra.get("po_event_identity") or "").strip()
    if explicit:
        return _safe(explicit)
    trade_id = str(extra.get("trade_id") or row.get("trade_id") or payload.get("trade_id") or "").strip()
    if trade_id:
        return _safe(trade_id)
    symbol = str(row.get("symbol") or payload.get("symbol") or "NA").upper()
    side = str(row.get("option_side") or row.get("option_type") or "na").lower()
    strategy = str(payload.get("snapshot_strategy") or "na")
    freeze = str(
        payload.get("po_decision_freeze_at")
        or row.get("po_decision_freeze_at")
        or "nofreeze"
    )
    return _safe(f"{symbol}_{strategy}_{side}_{freeze}")


def canonical_object_name(payload: Dict[str, Any]) -> Optional[str]:
    family = canonical_family(str(payload.get("stage") or ""))
    if not family:
        return None
    date_key = str(payload.get("date") or "unknown")
    return f"{date_key}__{family}__{event_identity(payload)}.json"


def body_sha256(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def classify(name: str, body: str) -> str:
    """absent, identical, or conflict. Does not reserve the name."""
    with _LOCK:
        _STATS["attempts"] += 1
        current = _BODIES.get(name)
        if current is None:
            return "absent"
        if body_sha256(current) == body_sha256(body):
            _STATS["identical"] += 1
            return "identical"
        _STATS["conflict"] += 1
        return "conflict"


def remember(name: str, body: str, *, created: bool) -> str:
    """Record a write that succeeded, or bytes already stored remotely."""
    with _LOCK:
        current = _BODIES.get(name)
        if current is None:
            _BODIES[name] = body
            if created:
                _STATS["created"] += 1
            return "created"
        if body_sha256(current) == body_sha256(body):
            return "identical"
        return "conflict"


def has_name(name: str) -> bool:
    with _LOCK:
        return name in _BODIES


def canonical_name_for(date_key: str, family: str, trade_id: str) -> str:
    return f"{date_key}__{family}__{_safe(trade_id)}.json"


def seal_object_name(canonical_filename: str) -> str:
    """Sibling object written before the canonical snapshot.

    A crash after this write and before the canonical upload still has the
    exact sealed bytes. Local disk is not used.
    """
    parts = str(canonical_filename or "").split("__")
    if len(parts) < 3:
        return ""
    parts[1] = "seal_" + parts[1]
    return "__".join(parts)


def read_body(name: str) -> Optional[str]:
    """Return stored bytes from this process or from GCS. Does not create."""
    if not name:
        return None
    with _LOCK:
        current = _BODIES.get(name)
    if current:
        return current
    try:
        from .gcs_persistence import get_gcs_persistence
        from .runtime_mode import gcs_execution_snapshot_object

        gcs = get_gcs_persistence()
        if not gcs.enabled:
            return None
        remote = gcs.read_string(gcs_execution_snapshot_object(name))
    except Exception:
        return None
    if not remote:
        return None
    remember(name, remote, created=False)
    return remote


def _lock_for(name: str) -> threading.Lock:
    with _NAME_LOCKS_GUARD:
        lock = _NAME_LOCKS.get(name)
        if lock is None:
            lock = threading.Lock()
            _NAME_LOCKS[name] = lock
        return lock


def commit_bytes(name: str, body: str) -> str:
    """Create-if-absent. Identical bytes are a retry. A different body conflicts.

    The snapshot writer runs on a side thread per exit tick. Two threads used
    to both see an empty cache and both POST create-if-absent; the loser got
    GCS 412. One lock per object name makes the second thread see the first
    body and return identical or conflict without a second upload.
    """
    if not name or not body:
        return "failed"
    with _lock_for(name):
        return _commit_bytes_locked(name, body)


def _commit_bytes_locked(name: str, body: str) -> str:
    status = classify(name, body)
    if status in ("identical", "conflict"):
        return status
    uploaded = False
    gcs_enabled = False
    try:
        from .gcs_persistence import get_gcs_persistence
        from .runtime_mode import gcs_execution_snapshot_object

        gcs = get_gcs_persistence()
        gcs_enabled = bool(gcs.enabled)
        if gcs_enabled:
            gcs_path = gcs_execution_snapshot_object(name)
            uploaded = bool(
                gcs.upload_string(gcs_path, body, if_generation_match=0)
            )
            if not uploaded:
                remote = gcs.read_string(gcs_path)
                if remote and body_sha256(remote) == body_sha256(body):
                    uploaded = True
                    body = remote
                elif remote:
                    remember(name, remote, created=False)
                    return "conflict"
    except Exception:
        gcs_enabled = False
    if uploaded or not gcs_enabled:
        remembered = remember(name, body, created=True)
        return "created" if remembered == "created" else remembered
    _bump("failed")
    return "failed"


def _bump(key: str) -> None:
    with _LOCK:
        _STATS[key] = int(_STATS.get(key) or 0) + 1


def load_sealed_payload(date_key: str, family: str, trade_id: str) -> Optional[Dict[str, Any]]:
    """Exact sealed snapshot, if the seal object exists and the canonical one does not yet."""
    if not str(trade_id or "").strip():
        return None
    canon = canonical_name_for(date_key, family, trade_id)
    if read_body(canon):
        return None
    raw = read_body(seal_object_name(canon))
    if not raw:
        return None
    try:
        import json

        payload = json.loads(raw)
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def load_handoff_row(
    date_key: str,
    *,
    trade_id: str = "",
    symbol: str = "",
    snapshot_strategy: str = "",
    option_side: str = "",
    freeze_at: str = "",
) -> Optional[Dict[str, Any]]:
    """Load one sealed handoff. No market read. Unknown identity returns nothing."""
    names = []
    if str(trade_id or "").strip():
        names.append(canonical_name_for(date_key, "entry_handoff", trade_id))
    if freeze_at and symbol and snapshot_strategy:
        alias = f"{symbol}_{snapshot_strategy}_{option_side}_{freeze_at}"
        names.append(canonical_name_for(date_key, "entry_handoff", alias))
    for name in names:
        raw = read_body(name)
        if not raw:
            continue
        try:
            import json

            payload = json.loads(raw)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        selected = payload.get("features_selected") or []
        row = dict(selected[0]) if selected and isinstance(selected[0], dict) else {}
        for key in (
            "po_decision_freeze_at",
            "po_decision_freeze_mode",
            "po_snapshot_event_at",
            "po_chain_asof_at",
            "po_session_tech_asof_at",
            "po_quote_asof_at",
            "po_spy_asof_at",
            "po_premium_path_asof_at",
            "po_clock_gaps",
            "po_tech_asof",
            "po_entry_sealed",
            "po_entry_training_eligible",
        ):
            if payload.get(key) not in (None, "") and row.get(key) in (None, ""):
                row[key] = payload.get(key)
        row["po_snapshot_stage"] = str(payload.get("stage") or row.get("stage") or "entry_handoff")
        return row
    # Contract trade_id rarely matches the symbol-slot handoff name. Scan the
    # same-day handoff bodies by freeze clock + symbol + side.
    if freeze_at and symbol:
        prefix = f"{date_key}__entry_handoff__"
        names = _handoff_scan_names(date_key, prefix)
        want_sym = str(symbol).upper()
        want_side = str(option_side or "").lower()
        for name in names:
            raw = read_body(name) or _read_local_snapshot(date_key, name)
            if not raw:
                continue
            try:
                import json

                payload = json.loads(raw)
            except Exception:
                continue
            if not isinstance(payload, dict):
                continue
            selected = payload.get("features_selected") or []
            row = dict(selected[0]) if selected and isinstance(selected[0], dict) else {}
            row_freeze = str(
                payload.get("po_decision_freeze_at") or row.get("po_decision_freeze_at") or ""
            )
            if row_freeze != str(freeze_at):
                continue
            row_sym = str(row.get("symbol") or payload.get("symbol") or "").upper()
            if row_sym and row_sym != want_sym:
                continue
            row_side = str(row.get("option_side") or row.get("option_type") or "").lower()
            if want_side and row_side and row_side != want_side:
                continue
            for key in (
                "po_decision_freeze_at",
                "po_decision_freeze_mode",
                "po_snapshot_event_at",
                "po_chain_asof_at",
                "po_session_tech_asof_at",
                "po_quote_asof_at",
                "po_spy_asof_at",
                "po_premium_path_asof_at",
                "po_clock_gaps",
                "po_tech_asof",
                "po_entry_sealed",
                "po_entry_training_eligible",
            ):
                if payload.get(key) not in (None, "") and row.get(key) in (None, ""):
                    row[key] = payload.get(key)
            row["po_snapshot_stage"] = str(payload.get("stage") or row.get("stage") or "entry_handoff")
            return row
    return None


def _local_snapshot_dirs(date_key: str) -> List[Path]:
    root = Path(__file__).resolve().parents[1] / "priority_optimizer"
    return [
        root / "execution_snapshots",
        root / "execution_snapshots" / str(date_key),
        root / "retrieved_data" / "execution_snapshots" / str(date_key),
    ]


def _read_local_snapshot(date_key: str, name: str) -> Optional[str]:
    for folder in _local_snapshot_dirs(date_key):
        path = folder / name
        if path.is_file():
            try:
                return path.read_text()
            except OSError:
                return None
    return None


def _handoff_scan_names(date_key: str, prefix: str) -> List[str]:
    names: List[str] = []
    with _LOCK:
        names.extend(name for name in _BODIES if name.startswith(prefix))
    for folder in _local_snapshot_dirs(date_key):
        try:
            names.extend(path.name for path in folder.glob(f"{prefix}*.json"))
        except OSError:
            continue
    return list(dict.fromkeys(names))


def durable_event_present(date_key: str, family: str, trade_id: str) -> bool:
    """True when this process or GCS already holds the canonical object."""
    if not str(trade_id or "").strip():
        return False
    name = canonical_name_for(date_key, family, trade_id)
    if has_name(name):
        return True
    try:
        from .gcs_persistence import get_gcs_persistence
        from .runtime_mode import gcs_execution_snapshot_object

        gcs = get_gcs_persistence()
        if not gcs.enabled:
            return False
        remote = gcs.read_string(gcs_execution_snapshot_object(name))
    except Exception:
        return False
    if not remote:
        return False
    remember(name, remote, created=False)
    return True


def stats() -> Dict[str, int]:
    with _LOCK:
        return dict(_STATS)


def stored_names() -> list:
    with _LOCK:
        return sorted(_BODIES)


def reset_canonical_store_for_tests() -> None:
    with _LOCK:
        _BODIES.clear()
        for key in _STATS:
            _STATS[key] = 0
