#!/usr/bin/env python3
"""Hard-cap dated GCS/local session artifacts at 2 calendar days.

Keeps today + yesterday. Deletes older session files and future-dated test
junk (e.g. 2099-*). Never touches current ledgers (mock_options_history.json).
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)

_DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")
_COMPACT_DATE_RE = re.compile(r"(20\d{6})")
_MAX_RETENTION_DAYS = 2
_DEFAULT_RETENTION_DAYS = 2

_DATED_PREFIXES: Tuple[str, ...] = (
    "research_app/profit_optimizer/daily/",
    "research_app/profit_optimizer/cohorts/",
    "research_app/profit_optimizer/paths/",
    "final_app/profit_optimizer/daily/",
    "final_app/profit_optimizer/cohorts/",
    "final_app/profit_optimizer/paths/",
    "profit_optimizer/daily/",
    "profit_optimizer/cohorts/",
    "profit_optimizer/paths/",
    "research_app/priority_optimizer/execution_snapshots/",
    "final_app/priority_optimizer/execution_snapshots/",
    "priority_optimizer/execution_snapshots/",
    "research_app/priority_optimizer/trendline_break_snapshots/",
    "final_app/priority_optimizer/trendline_break_snapshots/",
    "priority_optimizer/trendline_break_snapshots/",
    "research_app/priority_optimizer/trendline_outcomes/",
    "final_app/priority_optimizer/trendline_outcomes/",
    "priority_optimizer/trendline_outcomes/",
    "research_app/priority_optimizer/daily_signals/",
    "final_app/priority_optimizer/daily_signals/",
    "priority_optimizer/daily_signals/",
    "research_app/daily_markers/",
    "final_app/daily_markers/",
    "daily_markers/",
    "research_app/trendline_daily_cap/",
    "final_app/trendline_daily_cap/",
    "research_app/backups/",
    "final_app/backups/",
)

_KEEP_BASENAMES = frozenset(
    {
        "mock_options_history.json",
        "mock_trading_history.json",
    }
)


def session_retention_days() -> int:
    """Calendar days of dated session files to keep (hard-capped at 2)."""
    try:
        from modules.config_loader import get_config_value

        raw = get_config_value("GCS_SESSION_RETENTION_DAYS", str(_DEFAULT_RETENTION_DAYS))
        days = int(float(str(raw).strip()))
    except Exception:
        days = _DEFAULT_RETENTION_DAYS
    return max(1, min(_MAX_RETENTION_DAYS, days))


def parse_session_date_from_name(name: str) -> Optional[date]:
    text = str(name or "")
    m = _DATE_RE.search(text)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y-%m-%d").date()
        except ValueError:
            pass
    m2 = _COMPACT_DATE_RE.search(text.replace("-", ""))
    if m2:
        try:
            return datetime.strptime(m2.group(1), "%Y%m%d").date()
        except ValueError:
            pass
    return None


def should_prune_session_date(
    file_date: date,
    *,
    today: Optional[date] = None,
    retention_days: Optional[int] = None,
) -> bool:
    """True when the dated artifact is outside today+yesterday (or is a future test date)."""
    today = today or date.today()
    max_age = session_retention_days() if retention_days is None else max(1, min(_MAX_RETENTION_DAYS, int(retention_days)))
    if file_date > today:
        return True
    return (today - file_date).days >= max_age


def prune_dated_gcs_prefixes(
    gcs: Any,
    *,
    prefixes: Sequence[str] = _DATED_PREFIXES,
    today: Optional[date] = None,
) -> Tuple[int, int]:
    """Delete dated GCS objects past the 2-day window. Returns (examined, deleted)."""
    if not gcs or not getattr(gcs, "enabled", False):
        return 0, 0
    today = today or date.today()
    examined = 0
    to_delete: List[str] = []
    seen: set[str] = set()
    for prefix in prefixes:
        try:
            paths: Iterable[str] = gcs.list_files(prefix=prefix) or []
        except Exception as exc:
            log.debug("GCS_SESSION_RETENTION | list_failed | prefix=%s | err=%s", prefix, exc)
            continue
        for path in paths:
            if path in seen:
                continue
            seen.add(path)
            examined += 1
            base = str(path).split("/")[-1]
            if base in _KEEP_BASENAMES:
                continue
            file_date = parse_session_date_from_name(base) or parse_session_date_from_name(path)
            if file_date is None:
                continue
            if not should_prune_session_date(file_date, today=today):
                continue
            to_delete.append(path)

    deleted = 0
    if to_delete:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        bucket = getattr(gcs, "bucket", None)

        def _delete_one(path: str) -> bool:
            try:
                if bucket is not None:
                    bucket.blob(path).delete()
                    return True
                return bool(gcs.delete_file(path))
            except Exception:
                return False

        workers = min(16, max(4, len(to_delete) // 50 or 4))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = [pool.submit(_delete_one, p) for p in to_delete]
            deleted = sum(1 for fut in as_completed(futs) if fut.result())
    if deleted:
        log.warning(
            "GCS_SESSION_RETENTION | action=gcs_pruned | examined=%d | deleted=%d | "
            "keep_days=%d | today=%s",
            examined,
            deleted,
            session_retention_days(),
            today.isoformat(),
        )
    return examined, deleted


def prune_local_dated_files(directories: Sequence[Path], *, today: Optional[date] = None) -> int:
    """Delete local dated JSON/CSV files outside the 2-day window."""
    from modules.durable_archive_guard import guard_archive_mutation

    today = today or date.today()
    removed = 0
    for directory in directories:
        if not directory or not directory.is_dir():
            continue
        # Workstation retrieved_data/ is the long-term archive (GCS keeps 2 days).
        if not guard_archive_mutation(directory, action="local_dated_prune"):
            continue
        for path in directory.rglob("*"):
            if not path.is_file():
                continue
            if path.name in _KEEP_BASENAMES:
                continue
            file_date = parse_session_date_from_name(path.name) or parse_session_date_from_name(
                str(path)
            )
            if file_date is None or not should_prune_session_date(file_date, today=today):
                continue
            try:
                path.unlink(missing_ok=True)
                removed += 1
            except OSError as exc:
                log.debug("GCS_SESSION_RETENTION | local_delete_failed | path=%s | err=%s", path, exc)
    if removed:
        log.warning("GCS_SESSION_RETENTION | action=local_pruned | deleted=%d", removed)
    return removed


def run_session_retention_hygiene(gcs: Any = None) -> Tuple[int, int]:
    """Startup/EOD: prune GCS + local dated research artifacts."""
    examined = deleted = 0
    try:
        if gcs is None:
            from modules.gcs_persistence import get_gcs_persistence

            gcs = get_gcs_persistence()
        examined, deleted = prune_dated_gcs_prefixes(gcs)
    except Exception as exc:
        log.warning("GCS_SESSION_RETENTION | gcs_prune_skipped | err=%s", exc)
    try:
        root = Path(__file__).resolve().parent.parent
        prune_local_dated_files(
            [
                root / "analytics" / "profit_optimizer",
                root / "analytics" / "profit_optimizer" / "cohorts",
                root / "analytics" / "profit_optimizer" / "paths",
                root / "priority_optimizer" / "execution_snapshots",
                root / "priority_optimizer" / "trendline_break_snapshots",
                root / "priority_optimizer" / "retrieved_data",
            ]
        )
    except Exception as exc:
        log.debug("GCS_SESSION_RETENTION | local_prune_skipped | err=%s", exc)
    return examined, deleted
