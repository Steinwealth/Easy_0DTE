"""
Resolve the canonical SO (Standard Order) signal collection list for a trading day.

Rev 00298: The merged 7:30 PT payload at
    {gcs_namespace}/daily_markers/signal_collection_730/YYYY-MM-DD.json
holds `pending_so_signals` — the authoritative list used for execution allowlists.
The Priority Optimizer file under {ns}/priority_optimizer/daily_signals/ is derived from
the daily marker and can lag or hold a subset (e.g. only post-execution rows).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .runtime_mode import (
    gcs_daily_markers_path,
    gcs_daily_signals_list_prefixes,
    gcs_daily_signals_object,
    gcs_read_daily_markers_string,
    mode_label,
)

log = logging.getLogger(__name__)


def resolve_so_signal_collection_for_date(
    date_str: str,
    gcs: Any,
    *,
    retrieved_data_dir: Optional[Path] = None,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """
    Return (payload, source_description).

    payload keys match priority_optimizer daily_signals JSON: signals, total_scanned, mode, metadata, date.

    Resolution order:
    1. {namespace}/daily_markers/signal_collection_730/{date}.json → pending_so_signals
    2. {namespace}/priority_optimizer/daily_signals/{date}_signals.json (+ legacy flat)
    3. {namespace}/daily_markers/{date}.json → signals.signals (legacy flat fallback via gcs_read)
    """
    retrieved_data_dir = retrieved_data_dir or (
        Path(__file__).resolve().parent.parent / "priority_optimizer" / "retrieved_data"
    )
    runtime_mode = mode_label()

    def _read_gcs(relative: str) -> Optional[Dict[str, Any]]:
        raw = gcs_read_daily_markers_string(gcs, relative)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            log.warning("Invalid JSON from GCS path %s", gcs_daily_markers_path(relative))
            return None

    def _read_local(name: str) -> Optional[Dict[str, Any]]:
        p = retrieved_data_dir / name
        if not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception as exc:
            log.warning("Failed to read local %s: %s", p, exc)
            return None

    # 1) signal_collection_730 (authoritative merged SO list)
    path730_rel = f"signal_collection_730/{date_str}.json"
    path730 = gcs_daily_markers_path(path730_rel)
    data730 = _read_gcs(path730_rel) or _read_local(f"signal_collection_730_{date_str}.json")
    if data730 and data730.get("date") == date_str:
        pending = data730.get("pending_so_signals") or []
        if pending:
            allow = data730.get("allowed_so_symbols") or []
            total_scanned = len(allow) if allow else len(pending)
            payload: Dict[str, Any] = {
                "date": date_str,
                "signals": pending,
                "total_scanned": total_scanned,
                "mode": data730.get("mode") or runtime_mode,
                "signal_count": len(pending),
                "metadata": {
                    "resolver": "signal_collection_730",
                    "gcs_path": path730,
                },
            }
            log.info(
                "SO signal list: using %s (%s symbols)",
                path730,
                len(pending),
            )
            return payload, "signal_collection_730"

    # 2) priority_optimizer daily_signals (namespaced first, then legacy flat)
    path_po = gcs_daily_signals_object(date_str)
    po = None
    po_path_used = path_po
    if gcs and getattr(gcs, "enabled", False):
        candidates = [path_po]
        for prefix in gcs_daily_signals_list_prefixes():
            cand = f"{prefix.rstrip('/')}/{date_str}_signals.json"
            if cand not in candidates:
                candidates.append(cand)
        for cand in candidates:
            raw_po = gcs.read_string(cand)
            if not raw_po:
                continue
            try:
                po = json.loads(raw_po)
                po_path_used = cand
                break
            except json.JSONDecodeError:
                log.warning("Invalid JSON from GCS path %s", cand)
    if not po:
        po = _read_local(f"{date_str}_signals.json")
    if po and po.get("signals"):
        log.info(
            "SO signal list: using %s (%s symbols)",
            po_path_used,
            len(po["signals"]),
        )
        return po, "priority_optimizer/daily_signals"

    # 3) daily marker root
    path_dm_rel = f"{date_str}.json"
    path_dm = gcs_daily_markers_path(path_dm_rel)
    dm = _read_gcs(path_dm_rel)
    if dm and dm.get("signals"):
        se = dm["signals"]
        sigs = se.get("signals") or []
        if sigs:
            log.info(
                "SO signal list: using %s (%s symbols)",
                path_dm,
                len(sigs),
            )
            return (
                {
                    "date": date_str,
                    "signals": sigs,
                    "total_scanned": se.get("total_scanned", 0),
                    "mode": se.get("mode") or runtime_mode,
                    "signal_count": len(sigs),
                    "metadata": se.get("metadata") or {},
                },
                "daily_markers",
            )

    log.warning("No SO signal collection list found for %s", date_str)
    return None, ""
