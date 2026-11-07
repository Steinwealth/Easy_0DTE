"""Startup hygiene for local/GCS-adjacent telemetry (disk + log sanity)."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

from .telemetry_jsonl_utils import trim_oversized_telemetry_on_startup

log = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        return max(0, int(float(str(raw).strip())))
    except ValueError:
        return default


def prune_local_execution_snapshots() -> int:
    """Drop local snapshot files beyond EXECUTION_SNAPSHOT_KEEP_SESSIONS session dates."""
    keep = _env_int("EXECUTION_SNAPSHOT_KEEP_SESSIONS", 2)
    if keep <= 0:
        return 0
    keep = min(2, keep)
    root = Path(__file__).resolve().parent.parent
    snap_dir = root / "priority_optimizer" / "execution_snapshots"
    if not snap_dir.is_dir():
        return 0
    import re

    date_re = re.compile(r"^(\d{4}-\d{2}-\d{2})_")
    date_to_files: dict[str, list[Path]] = {}
    for p in snap_dir.glob("*.json"):
        m = date_re.match(p.name)
        if not m:
            continue
        date_to_files.setdefault(m.group(1), []).append(p)
    if len(date_to_files) <= keep:
        return 0
    keep_dates = set(sorted(date_to_files.keys(), reverse=True)[:keep])
    removed = 0
    for date_key, paths in date_to_files.items():
        if date_key in keep_dates:
            continue
        for p in paths:
            try:
                p.unlink(missing_ok=True)
                removed += 1
            except OSError as exc:
                log.debug("Local snapshot prune failed %s: %s", p, exc)
    if removed:
        log.info("Telemetry startup: pruned %s local execution snapshot file(s)", removed)
    return removed


def report_local_telemetry_disk_usage() -> None:
    root = Path(__file__).resolve().parent.parent
    paths = [
        root / "data" / "trendline_optimizer" / "trendline_features.jsonl",
        root / "priority_optimizer" / "execution_snapshots",
    ]
    total = 0
    for p in paths:
        if p.is_file():
            total += p.stat().st_size
        elif p.is_dir():
            total += sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
    if total > 0:
        log.info("Telemetry startup: local telemetry ~%.1f MiB", total / (1024 * 1024))
    warn_mb = _env_int("TELEMETRY_DISK_WARN_MB", 64)
    if warn_mb > 0 and total > warn_mb * 1024 * 1024:
        log.warning(
            "Telemetry startup: local telemetry %.1f MiB exceeds warn threshold %d MiB",
            total / (1024 * 1024),
            warn_mb,
        )


def run_telemetry_startup_hygiene() -> None:
    """Call once on Cloud Run / cloud-mode boot."""
    trim_oversized_telemetry_on_startup()
    prune_local_execution_snapshots()
    try:
        from .gcs_session_retention import run_session_retention_hygiene

        run_session_retention_hygiene()
    except Exception as exc:
        log.debug("Telemetry startup: GCS session retention skipped: %s", exc)
    if os.getenv("K_SERVICE") and os.getenv("PRIORITY_OPTIMIZER_CLEAR_RETRIEVED_ON_STARTUP", "true").lower() in (
        "1",
        "true",
        "yes",
    ):
        retrieved = Path(__file__).resolve().parent.parent / "priority_optimizer" / "retrieved_data"
        from .durable_archive_guard import guard_archive_mutation

        if retrieved.is_dir() and guard_archive_mutation(retrieved, action="startup_clear_retrieved"):
            try:
                shutil.rmtree(retrieved)
                retrieved.mkdir(parents=True, exist_ok=True)
                log.info("Telemetry startup: cleared priority_optimizer/retrieved_data cache")
            except OSError as exc:
                log.debug("Could not clear retrieved_data: %s", exc)
    report_local_telemetry_disk_usage()
