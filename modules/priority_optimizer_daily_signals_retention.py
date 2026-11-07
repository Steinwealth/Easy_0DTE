#!/usr/bin/env python3
"""GCS retention for priority_optimizer/daily_signals/ (session symbol lists)."""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import TYPE_CHECKING, Optional, Tuple

if TYPE_CHECKING:
    from modules.gcs_persistence import GCSPersistence

log = logging.getLogger(__name__)

_FILENAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})_signals\.json$")
_LEGACY_PREFIX = "priority_optimizer/daily_signals/"


def daily_signals_retention_days() -> int:
    """Session-date files older than this many calendar days are pruned (default 2 ≈ 48h)."""
    try:
        from modules.config_loader import get_config_value

        raw = get_config_value("PRIORITY_OPTIMIZER_DAILY_SIGNALS_RETENTION_DAYS", "2")
        days = int(float(str(raw).strip()))
        return max(1, days)
    except Exception:
        return 2


def parse_daily_signals_session_date(gcs_path: str) -> Optional[date]:
    """Extract YYYY-MM-DD from …/daily_signals/YYYY-MM-DD_signals.json (ns or legacy)."""
    name = str(gcs_path or "").split("/")[-1]
    m = _FILENAME_RE.match(name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d").date()
    except ValueError:
        return None


def should_prune_daily_signals_file(file_session_date: date, *, today: Optional[date] = None) -> bool:
    """
    True when the session file is at or past the retention window.

    retention_days=2: on calendar day D, keep D and D-1; delete D-2 and older.
    """
    today = today or date.today()
    max_age = daily_signals_retention_days()
    age_days = (today - file_session_date).days
    return age_days >= max_age


def _daily_signals_prefixes() -> Tuple[str, ...]:
    try:
        from modules.runtime_mode import gcs_daily_signals_list_prefixes

        return tuple(dict.fromkeys(gcs_daily_signals_list_prefixes()))
    except Exception:
        return (_LEGACY_PREFIX,)


def prune_priority_optimizer_daily_signals(
    gcs: "GCSPersistence",
    *,
    today: Optional[date] = None,
) -> Tuple[int, int]:
    """
    Delete GCS objects under namespaced + legacy daily_signals/ past retention.

    Returns (examined_count, deleted_count).
    """
    if not gcs or not gcs.enabled:
        return 0, 0

    today = today or date.today()
    examined = 0
    deleted = 0
    seen: set[str] = set()

    for prefix in _daily_signals_prefixes():
        paths = gcs.list_files(prefix=prefix) or []
        for path in paths:
            if path in seen:
                continue
            seen.add(path)
            examined += 1
            if "/daily_signals/" not in path:
                continue
            session_date = parse_daily_signals_session_date(path)
            if session_date is None:
                log.debug("PRIORITY_OPTIMIZER_RETENTION | skip_unrecognized | path=%s", path)
                continue
            if not should_prune_daily_signals_file(session_date, today=today):
                continue
            if gcs.delete_file(path):
                deleted += 1

    if deleted:
        retain = daily_signals_retention_days()
        log.info(
            "🧹 Priority Optimizer daily_signals retention: deleted %s file(s) "
            "(session age >= %s calendar day(s))",
            deleted,
            retain,
        )
    return examined, deleted
