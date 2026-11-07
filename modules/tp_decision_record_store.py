"""One lightweight decision record per Trade Profile evaluation (Oct 7 2026).

Every evaluation - admit, reject, freeze-stale defer, and post-TP contract-band
block - appends one compact JSON line (the decision freeze: values, per-source
ages, clocks, verdict) to a local daily JSONL file:

    tmp/tp_decision_records/<YYYY-MM-DD>.jsonl

Why not one GCS object per reject: 10/7 had 716 evaluations (697 non-admits).
One object per evaluation would be ~700 extra GCS writes per session on the
trading process. Instead the local file is appended in-process (a few hundred
microseconds, no network) and the *whole daily file* is uploaded through the
existing GCS persistence client (``modules.gcs_persistence``) on a daemon
thread at most every ``TP_DECISION_RECORD_UPLOAD_SEC`` (default 300 s, ~80
small uploads per RTH session) plus once at process exit. Object:

    <namespace>/profit_optimizer/tp_decision_records/<YYYY-MM-DD>.jsonl

GCS upload follows the same switches as the persistence layer: nothing is
uploaded when the persistence client is disabled (``GCS_PERSISTENCE_ENABLED``
false) or ``TP_DECISION_RECORD_GCS`` is false. Local writes can be turned off
with ``TP_DECISION_RECORD_ENABLED=false``.
"""
from __future__ import annotations

import atexit
import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

ROOT_APP = Path(__file__).resolve().parents[1]
DEFAULT_GCS_PREFIX = "profit_optimizer/tp_decision_records"

_LOCK = threading.Lock()
_STATE: Dict[str, Any] = {"last_upload": 0.0, "dirty": set(), "count": 0}
# One GCS object per process per day: a Cloud Run restart (ephemeral /tmp)
# must not overwrite the records an earlier instance already uploaded.
_PROC_TAG = f"{datetime.utcnow().strftime('%H%M%S')}_{os.getpid()}"


def _truthy(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def records_enabled() -> bool:
    return _truthy("TP_DECISION_RECORD_ENABLED", True)


def out_dir() -> Path:
    raw = os.environ.get("TP_DECISION_RECORD_DIR")
    if raw:
        return Path(raw)
    if os.environ.get("PYTEST_CURRENT_TEST"):
        # Unit tests never write into the app tree.
        import tempfile

        return Path(tempfile.gettempdir()) / "tp_decision_records_pytest"
    return ROOT_APP / "tmp" / "tp_decision_records"


def _pt_day() -> str:
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
    except Exception:
        return datetime.utcnow().strftime("%Y-%m-%d")


def daily_path(day: Optional[str] = None) -> Path:
    return out_dir() / f"{day or _pt_day()}.jsonl"


def append_decision_record(record: Dict[str, Any], *, kind: str = "tp_evaluation") -> Optional[Path]:
    """Append one record. Never raises into the trading loop."""
    if not records_enabled() or not isinstance(record, dict):
        return None
    try:
        day = _pt_day()
        path = daily_path(day)
        line = dict(record)
        line.setdefault("record_kind", kind)
        line.setdefault("recorded_at", datetime.utcnow().isoformat() + "Z")
        body = json.dumps(line, default=str, separators=(",", ":")) + "\n"
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(body)
            _STATE["dirty"].add(day)
            _STATE["count"] = int(_STATE.get("count") or 0) + 1
        _maybe_upload()
        return path
    except Exception as exc:  # pragma: no cover - defensive
        log.debug("TP_DECISION_RECORD | append_failed | err=%s", exc)
        return None


def _upload_interval() -> float:
    try:
        return max(30.0, float(os.environ.get("TP_DECISION_RECORD_UPLOAD_SEC", "300") or 300))
    except (TypeError, ValueError):
        return 300.0


def _upload_day(day: str) -> bool:
    if not _truthy("TP_DECISION_RECORD_GCS", True):
        return False
    if not _truthy("GCS_PERSISTENCE_ENABLED", True):
        return False
    try:
        from modules.gcs_persistence import get_gcs_persistence
        from modules.runtime_mode import gcs_namespace

        client = get_gcs_persistence()
        if not getattr(client, "enabled", False):
            return False
        ns = str(gcs_namespace() or "research_app").strip().strip("/") or "research_app"
        path = daily_path(day)
        if not path.exists():
            return False
        client.upload_file(str(path), f"{ns}/{DEFAULT_GCS_PREFIX}/{day}/{_PROC_TAG}.jsonl")
        return True
    except Exception as exc:
        log.debug("TP_DECISION_RECORD | upload_failed | day=%s | err=%s", day, exc)
        return False


def _maybe_upload(force: bool = False) -> None:
    now = time.time()
    with _LOCK:
        if not _STATE["dirty"]:
            return
        if not force and now - float(_STATE.get("last_upload") or 0.0) < _upload_interval():
            return
        days = sorted(_STATE["dirty"])
        _STATE["dirty"] = set()
        _STATE["last_upload"] = now

    def _run() -> None:
        for day in days:
            _upload_day(day)

    if force:
        _run()
        return
    try:
        threading.Thread(target=_run, name="tp-decision-record-gcs", daemon=True).start()
    except Exception:
        _run()


def flush_decision_records() -> None:
    """Upload pending days now (EOD / shutdown)."""
    _maybe_upload(force=True)


atexit.register(flush_decision_records)
