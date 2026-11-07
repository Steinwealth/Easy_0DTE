"""Protect the local durable research archives from automated writes/pruning.

``priority_optimizer/retrieved_data/`` and ``profit_optimizer/retrieved_data/``
hold the daily post-session pulls. GCS keeps only ~2 session days
(``modules/gcs_session_retention``), so on a workstation these folders are the
only long-term copy. Only the retrieve scripts (and explicit manual tools)
should write there.

App code that may touch these folders (trendline daily export, startup
hygiene, dated-file retention) asks this module first:

* Inside a Cloud Run container (``K_SERVICE`` / ``CLOUD_RUN_JOB``) the folder is
  an ephemeral image copy, so the old behavior is kept.
* Anywhere else (a Mac checkout, a local ``--cloud-mode`` run, pytest) it is
  refused.
* Under pytest it is always refused, even when a test sets ``K_SERVICE``.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Iterable, Tuple

log = logging.getLogger(__name__)

APP_ROOT = Path(__file__).resolve().parent.parent


def durable_archive_roots() -> Tuple[Path, ...]:
    return (
        (APP_ROOT / "priority_optimizer" / "retrieved_data").resolve(),
        (APP_ROOT / "profit_optimizer" / "retrieved_data").resolve(),
    )


def running_under_pytest() -> bool:
    return bool(os.getenv("PYTEST_CURRENT_TEST")) or "pytest" in sys.modules


def in_cloud_container() -> bool:
    return bool((os.getenv("K_SERVICE") or "").strip() or (os.getenv("CLOUD_RUN_JOB") or "").strip())


def is_durable_archive_path(path: Path | str, roots: Iterable[Path] | None = None) -> bool:
    """True when ``path`` is a durable archive root or anything inside one."""
    try:
        target = Path(path).resolve()
    except Exception:
        return False
    for root in roots if roots is not None else durable_archive_roots():
        root = Path(root).resolve()
        if target == root or root in target.parents:
            return True
    return False


def archive_mutation_allowed() -> bool:
    """Automated app code may write/prune the archive only in a cloud container, never in tests."""
    if running_under_pytest():
        return False
    return in_cloud_container()


def guard_archive_mutation(path: Path | str, *, action: str) -> bool:
    """Return True if ``action`` on ``path`` may proceed; log and return False otherwise."""
    if not is_durable_archive_path(path):
        return True
    if archive_mutation_allowed():
        return True
    log.warning(
        "DURABLE_ARCHIVE_GUARD | action=%s | path=%s | blocked=true | reason=%s",
        action,
        path,
        "pytest" if running_under_pytest() else "not_cloud_container",
    )
    return False
