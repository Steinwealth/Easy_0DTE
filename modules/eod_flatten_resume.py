"""EOD flatten resume helpers — survive Cloud Run instance replacement.

The 12:50–12:51 PT window is only the *scheduled start*. Flatten must keep
running until the unified 0DTE book is empty so the 12:55 EOD report is not
sent over leftover opens. Done-flags live in GCS as well as process memory.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any, Dict, Iterable, List, Optional, Sequence
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

PT_TZ = ZoneInfo("America/Los_Angeles")

EOD_FLATTEN_COMPLETE_PREFIX = "eod_flatten_complete"
EOD_FLATTEN_BATCH_ALERT_PREFIX = "eod_flatten_batch_alert"
_EOD_FLATTEN_REASONS = frozenset({"end_of_day_exit", "eod_close"})


def flatten_complete_marker_key(pt_date: date) -> str:
    return f"{EOD_FLATTEN_COMPLETE_PREFIX}_{pt_date.isoformat()}"


def flatten_batch_alert_marker_key(pt_date: date) -> str:
    return f"{EOD_FLATTEN_BATCH_ALERT_PREFIX}_{pt_date.isoformat()}"


def should_skip_eod_flatten(
    *,
    remaining_open: int,
    same_process_done: bool,
    gcs_complete: bool,
    skip_if_already: bool,
) -> bool:
    """Skip only when the book is already empty and flatten finished this PT day."""
    if not skip_if_already:
        return False
    if int(remaining_open or 0) > 0:
        return False
    return bool(same_process_done or gcs_complete)


def is_eod_flatten_reason(reason: str) -> bool:
    raw = str(reason or "").strip().lower().replace(" ", "_")
    return raw in _EOD_FLATTEN_REASONS


def _exit_reason_of(row: Any) -> str:
    if isinstance(row, dict):
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        return str(meta.get("exit_reason") or row.get("exit_reason") or "")
    meta = getattr(row, "metadata", None)
    if isinstance(meta, dict):
        return str(meta.get("exit_reason") or "")
    return str(getattr(row, "exit_reason", "") or "")


def _parse_pt_date(raw: Any) -> Optional[date]:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        ts = raw if raw.tzinfo is not None else raw.replace(tzinfo=PT_TZ)
        return ts.astimezone(PT_TZ).date()
    if isinstance(raw, date):
        return raw
    if isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=PT_TZ)
        return parsed.astimezone(PT_TZ).date()
    return None


def _row_closed_date_pt(row: Any) -> Optional[date]:
    if isinstance(row, dict):
        return (
            _parse_pt_date(row.get("closed_at"))
            or _parse_pt_date(row.get("exit_time"))
            or _parse_pt_date(row.get("entry_time"))
        )
    try:
        from modules.eod_report_helpers import position_closed_date_pt

        return position_closed_date_pt(row)
    except Exception:
        return _parse_pt_date(getattr(row, "closed_at", None)) or _parse_pt_date(
            getattr(row, "entry_time", None)
        )


def _row_as_dict(row: Any) -> Optional[Dict[str, Any]]:
    if isinstance(row, dict):
        return row
    to_dict = getattr(row, "to_dict", None)
    if callable(to_dict):
        try:
            payload = to_dict()
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None
    return None


def collect_today_eod_flatten_close_dicts(
    rows: Optional[Iterable[Any]],
    *,
    today: Optional[date] = None,
) -> List[Dict[str, Any]]:
    """Today's closed rows whose exit reason is the EOD flatten (not mfe_trail)."""
    if today is None:
        today = datetime.now(PT_TZ).date()
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows or []:
        if not is_eod_flatten_reason(_exit_reason_of(row)):
            continue
        closed_day = _row_closed_date_pt(row)
        if closed_day is not None and closed_day != today:
            continue
        payload = _row_as_dict(row)
        if not isinstance(payload, dict):
            continue
        pid = str(payload.get("position_id") or "").strip()
        if pid and pid in seen:
            continue
        if pid:
            seen.add(pid)
        out.append(payload)
    return out


def merge_eod_close_dicts(*groups: Sequence[Any]) -> List[Dict[str, Any]]:
    """Dedupe close dicts by position_id, keeping first occurrence."""
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for group in groups:
        for row in group or []:
            if not isinstance(row, dict):
                continue
            pid = str(row.get("position_id") or "").strip()
            if pid and pid in seen:
                continue
            if pid:
                seen.add(pid)
            out.append(row)
    return out


def _gcs_client() -> Any:
    try:
        from modules.gcs_persistence import get_gcs_persistence

        return get_gcs_persistence()
    except Exception:
        return None


def gcs_eod_flatten_complete(pt_date: Optional[date] = None, gcs: Any = None) -> bool:
    if pt_date is None:
        pt_date = datetime.now(PT_TZ).date()
    client = gcs if gcs is not None else _gcs_client()
    if not client or not getattr(client, "enabled", False):
        return False
    try:
        from modules.runtime_mode import gcs_eod_marker_path

        return bool(client.file_exists(gcs_eod_marker_path(flatten_complete_marker_key(pt_date))))
    except Exception as exc:
        log.warning("EOD_FLATTEN_GCS | complete_check_failed | err=%s", exc)
        return False


def mark_gcs_eod_flatten_complete(pt_date: Optional[date] = None, gcs: Any = None) -> None:
    if pt_date is None:
        pt_date = datetime.now(PT_TZ).date()
    client = gcs if gcs is not None else _gcs_client()
    if not client:
        return
    try:
        from modules.eod_report_helpers import try_claim_eod_marker

        try_claim_eod_marker(client, flatten_complete_marker_key(pt_date))
    except Exception as exc:
        log.warning("EOD_FLATTEN_GCS | complete_mark_failed | err=%s", exc)


def gcs_eod_flatten_batch_alert_sent(pt_date: Optional[date] = None, gcs: Any = None) -> bool:
    """True when Batch Order Closed already sent today (GCS marker present)."""
    if pt_date is None:
        pt_date = datetime.now(PT_TZ).date()
    client = gcs if gcs is not None else _gcs_client()
    if not client:
        return False
    try:
        from modules.eod_report_helpers import eod_marker_exists

        return bool(eod_marker_exists(client, flatten_batch_alert_marker_key(pt_date)))
    except Exception as exc:
        log.warning("EOD_FLATTEN_GCS | batch_sent_check_failed | err=%s", exc)
        return False


def claim_gcs_eod_flatten_batch_alert(pt_date: Optional[date] = None, gcs: Any = None) -> bool:
    """Record that Batch Order Closed Telegram succeeded. Call only after send."""
    if pt_date is None:
        pt_date = datetime.now(PT_TZ).date()
    client = gcs if gcs is not None else _gcs_client()
    if not client:
        return True
    try:
        from modules.eod_report_helpers import try_claim_eod_marker

        return bool(try_claim_eod_marker(client, flatten_batch_alert_marker_key(pt_date)))
    except Exception as exc:
        log.warning("EOD_FLATTEN_GCS | batch_claim_failed | err=%s", exc)
        return True


def collect_today_eod_flatten_closes_from_system(system: Any) -> List[Dict[str, Any]]:
    rows: List[Any] = []
    try:
        from modules.unified_0dte_account import get_options_executor

        executor = get_options_executor(system)
    except Exception:
        executor = getattr(getattr(system, "dte0_manager", None), "options_executor", None)
    if executor is not None:
        rows.extend(getattr(executor, "closed_positions", None) or [])
        mock = getattr(executor, "mock_executor", None)
        if mock is not None:
            rows.extend(getattr(mock, "closed_positions", None) or [])
    return collect_today_eod_flatten_close_dicts(rows)
