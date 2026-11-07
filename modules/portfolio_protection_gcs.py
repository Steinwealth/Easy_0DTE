"""
GCS persistence for Portfolio Protection @ scheduler Step 5.

Cross-instance parity: the instance that evaluates PP writes a daily marker;
every instance reads it before SO / 0DTE execution so block state cannot be lost.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from .portfolio_protection import (
    PortfolioProtectionAssessment,
    portfolio_protection_enabled,
    portfolio_protection_observe_only_enabled,
    publish_portfolio_protection_session_from_assessment,
)

log = logging.getLogger(__name__)

PP_STEP5_MARKER_PREFIX = "portfolio_protection_step5"


def pp_step5_marker_relative(date_pt: str) -> str:
    return f"{PP_STEP5_MARKER_PREFIX}/{date_pt}.json"


def log_pp_audit(action: str, *, reason: str = "", **fields: Any) -> None:
    """Mandatory PP audit line — grep ``PORTFOLIO_PROTECTION | audit``."""
    parts = [f"action={action}"]
    if reason:
        parts.append(f"reason={reason}")
    for key, val in sorted(fields.items()):
        if val is not None and val != "":
            parts.append(f"{key}={val}")
    log.info("PORTFOLIO_PROTECTION | audit | %s", " | ".join(parts))


def assessment_to_marker(
    assessment: PortfolioProtectionAssessment,
    *,
    date_pt: str,
    pool_count: int,
    red_day_filter_blocked: bool,
    volume_coverage_pct: float,
    observe_only: bool = False,
) -> Dict[str, Any]:
    return {
        "date": date_pt,
        "complete": True,
        "evaluated_at_utc": datetime.now(timezone.utc).isoformat(),
        "service": os.getenv("K_SERVICE", ""),
        "revision": os.getenv("K_REVISION", ""),
        "pool_count": int(pool_count),
        "volume_coverage_pct": round(float(volume_coverage_pct), 2),
        "block_so_execution": bool(assessment.block_so_execution),
        "observe_only": bool(observe_only),
        "trigger_id": assessment.trigger_id,
        "primary_reason": assessment.primary_reason,
        "triggers_fired": list(assessment.triggers_fired or []),
        "metrics": dict(assessment.metrics or {}),
        "red_day_filter_blocked": bool(red_day_filter_blocked),
        "red_day_reason": assessment.primary_reason if red_day_filter_blocked else None,
    }


def persist_pp_step5_marker(marker: Dict[str, Any]) -> bool:
    try:
        from .gcs_persistence import get_gcs_persistence
        from .runtime_mode import gcs_daily_markers_path

        gcs = get_gcs_persistence()
        if not gcs or not getattr(gcs, "enabled", False):
            log_pp_audit("persist_skipped", reason="gcs_disabled")
            return False
        date_pt = str(marker.get("date") or "").strip()
        if not date_pt:
            return False
        rel = pp_step5_marker_relative(date_pt)
        path = gcs_daily_markers_path(rel)
        gcs.upload_string(path, json.dumps(marker, default=str))
        log_pp_audit(
            "persisted",
            reason="gcs_write_ok",
            date_pt=date_pt,
            path=path,
            blocked=str(marker.get("red_day_filter_blocked", False)).lower(),
            trigger_id=marker.get("trigger_id") or "none",
        )
        return True
    except Exception as err:
        log.warning("PORTFOLIO_PROTECTION | persist_failed | %s", err)
        log_pp_audit("persist_failed", reason=str(err)[:120])
        return False


def load_pp_step5_marker(date_pt: str) -> Optional[Dict[str, Any]]:
    try:
        from .gcs_persistence import get_gcs_persistence
        from .runtime_mode import gcs_read_daily_markers_string

        gcs = get_gcs_persistence()
        if not gcs or not getattr(gcs, "enabled", False):
            return None
        raw = gcs_read_daily_markers_string(gcs, pp_step5_marker_relative(date_pt))
        if not raw:
            return None
        data = json.loads(raw)
        if str(data.get("date") or "") != date_pt:
            return None
        if not data.get("complete"):
            return None
        return data
    except Exception as err:
        log.debug("PORTFOLIO_PROTECTION | load_marker_failed | %s", err)
        return None


def apply_pp_marker_to_system(system: Any, marker: Dict[str, Any]) -> None:
    """Hydrate in-memory PP flags from a GCS Step-5 marker."""
    metrics = dict(marker.get("metrics") or {})
    trigger_id = marker.get("trigger_id")
    if trigger_id:
        metrics["portfolio_protection_trigger"] = trigger_id

    assessment = PortfolioProtectionAssessment(
        block_so_execution=bool(marker.get("block_so_execution")),
        trigger_id=trigger_id,
        primary_reason=marker.get("primary_reason"),
        metrics=metrics,
        triggers_fired=list(marker.get("triggers_fired") or []),
    )
    system._portfolio_protection_assessment = assessment
    system._last_pp_protection_pool = list(getattr(system, "_last_pp_protection_pool", None) or [])
    publish_portfolio_protection_session_from_assessment(assessment)

    blocked = bool(marker.get("red_day_filter_blocked"))
    # Paused PP or observe-only: never arm tier gate from GCS (Aug 7 SC-off / RESEARCH).
    if (not portfolio_protection_enabled()) or portfolio_protection_observe_only_enabled():
        blocked = False
        if marker.get("block_so_execution") or marker.get("observe_only"):
            system._portfolio_protection_observe_trigger = marker.get("trigger_id")
            system._portfolio_protection_observe_reason = marker.get("primary_reason")
            system._portfolio_protection_observe_metrics = dict(metrics)
            # Observe-only still publishes trigger for CALL Hold suppress.
            publish_portfolio_protection_session_from_assessment(assessment)
    system._red_day_filter_blocked = blocked
    if blocked:
        system._red_day_reason = marker.get("red_day_reason") or marker.get("primary_reason")
        system._red_day_metrics = metrics
    else:
        if not getattr(system, "_red_day_filter_blocked", False):
            system._red_day_reason = None
            system._red_day_metrics = None

    date_pt = str(marker.get("date") or "")
    if date_pt:
        system._pp_gates_complete_at_step5_pt_date = date_pt
        system._pp_step5_gate_synced_pt_date = date_pt
        system._gate_3b_step5_evaluated_pt_date = date_pt

    log_pp_audit(
        "hydrated",
        reason="gcs_marker_applied",
        date_pt=date_pt,
        blocked=str(blocked).lower(),
        trigger_id=trigger_id or "none",
        pool_count=marker.get("pool_count"),
        volume_coverage_pct=marker.get("volume_coverage_pct"),
        pp_enabled=str(portfolio_protection_enabled()).lower(),
    )


def try_hydrate_pp_from_gcs(system: Any, date_pt: str) -> bool:
    """Load GCS marker into system if present. Returns True when marker applied."""
    marker = load_pp_step5_marker(date_pt)
    if not marker:
        return False
    apply_pp_marker_to_system(system, marker)
    return True
