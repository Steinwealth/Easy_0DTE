"""
Daily Run Tracker
=================

Provides persistence for daily ORB capture and SO signal collection/execution
markers so Cloud Run instance restarts do not repeat critical tasks or alerts.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, date, time
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python <3.9 fallback
    from pytz import timezone as ZoneInfo  # type: ignore

from .gcs_persistence import get_gcs_persistence
from .runtime_mode import (
    gcs_daily_markers_path,
    gcs_read_daily_markers_string,
)

log = logging.getLogger("daily_run_tracker")


def _json_safe(value: Any) -> Any:
    """Datetime-safe JSON walk so WATCH_FSM GCS writes cannot fail mid-session."""
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


PT_TZ = ZoneInfo("America/Los_Angeles")

# ORB #1 opening range is only complete at/after ORB_WINDOW_END (Shared.env §5).
# ORB #2 session OHLC is only complete at/after CAPTURE_ORB2_END_PT.


def orb1_capture_ready_pt() -> time:
    from modules.session_clock import orb1_capture_window_pt

    return orb1_capture_window_pt()[1]


def orb2_capture_ready_pt() -> time:
    from modules.session_clock import orb2_capture_window_pt

    return orb2_capture_window_pt()[1]


def __getattr__(name: str):
    if name == "ORB1_CAPTURE_AT_OR_AFTER_PT":
        return orb1_capture_ready_pt()
    if name == "ORB2_CAPTURE_AT_OR_AFTER_PT":
        return orb2_capture_ready_pt()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def so_marker_indicates_real_batch_completed(signals_info: Optional[Dict[str, Any]]) -> bool:
    """
    True only when persisted state proves ORB SO batch execution actually completed
    (i.e. _process_orb_signals ran for the 7:30 batch), not the historic Step-5 empty
    path that wrote execution_completed with zero trades (May 12, 2026 incident).
    """
    if not signals_info:
        return False
    if signals_info.get("so_execution_batch_completed") is True:
        return True
    if signals_info.get("execution_completed") is not True:
        return False
    meta = signals_info.get("metadata") or {}
    executed_ct = int(signals_info.get("executed_count") or 0)
    rejected_ct = int(signals_info.get("rejected_count") or 0)
    if executed_ct > 0 or rejected_ct > 0:
        return True
    if meta.get("batch_process_completed") or meta.get("process_orb_signals_ran"):
        return True
    # Poisoned legacy marker: Step 5 called record_signal_execution with no pending SO.
    if meta.get("note") == "No signals collected":
        return False
    # Ambiguous legacy zero/zero without proof — do not arm dedupe (prefer one re-run over silent skip).
    return False


def _current_trading_date() -> date:
    """Return the current trading date in Pacific Time."""
    return datetime.now(PT_TZ).date()


def _marker_snapshot_is_valid_day_freeze(
    info: Optional[Dict[str, Any]],
    *,
    min_capture_time: time,
) -> bool:
    """Shared validator for ORB1/ORB2 day freezes in daily markers."""
    if not info or not info.get("captured"):
        return False
    snapshot = info.get("snapshot") or {}
    if not snapshot:
        return False
    today = _current_trading_date()
    captured_at_str = info.get("captured_at") or ""
    try:
        if captured_at_str:
            captured_at = datetime.fromisoformat(str(captured_at_str).replace("Z", "+00:00"))
            if captured_at.tzinfo is None:
                captured_at = captured_at.replace(tzinfo=PT_TZ)
            else:
                captured_at = captured_at.astimezone(PT_TZ)
            if captured_at.date() != today:
                return False
    except Exception:
        return False

    for row in snapshot.values():
        if not isinstance(row, dict):
            return False
        ct_raw = row.get("capture_time")
        if not ct_raw:
            return False
        try:
            ct = datetime.fromisoformat(str(ct_raw).replace("Z", "+00:00"))
            if ct.tzinfo is None:
                ct = ct.replace(tzinfo=PT_TZ)
            else:
                ct = ct.astimezone(PT_TZ)
        except Exception:
            return False
        if ct.date() != today:
            return False
        if ct.time() < min_capture_time:
            return False
        try:
            hi = float(row.get("orb_high") or 0.0)
            lo = float(row.get("orb_low") or 0.0)
        except (TypeError, ValueError):
            return False
        if hi <= 0 or lo <= 0 or hi < lo:
            return False
    return True


def _snapshot_capture_datetime(snapshot: Dict[str, Dict[str, Any]]) -> datetime:
    """Return the latest capture_time carried by a frozen ORB snapshot."""
    latest: Optional[datetime] = None
    for row in snapshot.values():
        if not isinstance(row, dict):
            continue
        ct_raw = row.get("capture_time")
        if not ct_raw:
            continue
        try:
            ct = datetime.fromisoformat(str(ct_raw).replace("Z", "+00:00"))
            if ct.tzinfo is None:
                ct = ct.replace(tzinfo=PT_TZ)
            else:
                ct = ct.astimezone(PT_TZ)
        except Exception:
            continue
        if latest is None or ct > latest:
            latest = ct
    return latest or datetime.now(PT_TZ)


def orb1_marker_is_valid_day_freeze(orb_info: Optional[Dict[str, Any]]) -> bool:
    """True when marker holds a post-06:45 PT ORB#1 freeze for the current PT day."""
    return _marker_snapshot_is_valid_day_freeze(
        orb_info, min_capture_time=orb1_capture_ready_pt()
    )


def orb2_marker_is_valid_day_freeze(orb2_info: Optional[Dict[str, Any]]) -> bool:
    """True when marker holds a post-07:30 PT ORB#2 freeze for the current PT day."""
    return _marker_snapshot_is_valid_day_freeze(
        orb2_info, min_capture_time=orb2_capture_ready_pt()
    )


def trendline_geometry_marker_is_valid(info: Optional[Dict[str, Any]]) -> bool:
    """True when marker holds at least one frozen TL line or TL2/TL3 inject state."""
    try:
        from modules.trendline_geometry_persist import marker_has_valid_geometry

        return bool(marker_has_valid_geometry(info))
    except Exception:
        return False


class DailyRunTracker:
    """Persist daily run markers for ORB capture and SO processing."""

    def __init__(self) -> None:
        self._gcs = get_gcs_persistence()
        base_dir = os.getenv("DAILY_MARKER_DIR", "/tmp/easy_etrade_markers")
        self._base_path = Path(base_dir)
        self._base_path.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def get_today_state(self) -> Dict[str, Any]:
        """Load today's marker information."""
        date_key = self._date_key()
        marker = self._load_marker(date_key)
        return marker or {}

    def record_orb_capture(
        self,
        orb_snapshot: Dict[str, Dict[str, Any]],
        captured_count: int,
        total_symbols: int,
        metadata: Optional[Dict[str, Any]] = None,
        *,
        force: bool = False,
    ) -> bool:
        """Persist ORB #1 (06:30–06:45) freeze for Cloud Run restarts / redeploys.

        Day-locked like ORB #2: does not overwrite a valid post-06:45 freeze unless
        ``force=True``. Rejects premature / invalid snapshots.
        """
        if not orb_snapshot:
            return False

        date_key = self._date_key()
        marker = self._load_marker(date_key) or {}
        existing = marker.get("orb") or {}
        if not force and orb1_marker_is_valid_day_freeze(existing):
            log.info(
                "ORB1_MARKER | action=skip_overwrite | reason=valid_day_freeze_exists | "
                "date=%s | captured=%s",
                date_key,
                existing.get("captured_count"),
            )
            return False

        meta = dict(metadata or {})
        captured_dt = _snapshot_capture_datetime(orb_snapshot)
        candidate = {
            "captured": True,
            "captured_at": captured_dt.isoformat(),
            "captured_epoch": captured_dt.timestamp(),
            "captured_count": captured_count,
            "total_symbols": total_symbols,
            "snapshot": orb_snapshot,
            "metadata": meta,
            "alert_sent": bool(meta.get("alert_sent", False)),
            "window_pt": "06:30-06:45",
            "orb_label": "ORB#1",
        }
        if not orb1_marker_is_valid_day_freeze(candidate):
            log.warning(
                "ORB1_MARKER | action=reject_persist | reason=premature_or_invalid_snapshot | "
                "date=%s | captured=%s",
                date_key,
                captured_count,
            )
            return False

        marker["orb"] = candidate
        self._save_marker(date_key, marker)
        log.info(
            "ORB1_MARKER | action=recorded | date=%s | captured=%s/%s | window=06:30-06:45",
            date_key,
            captured_count,
            total_symbols,
        )
        return True

    def record_orb2_capture(
        self,
        orb2_snapshot: Dict[str, Dict[str, Any]],
        captured_count: int,
        total_symbols: int,
        metadata: Optional[Dict[str, Any]] = None,
        *,
        force: bool = False,
    ) -> bool:
        """Persist ORB #2 (06:30–07:30) freeze for Cloud Run restarts / redeploys.

        Mirrors ORB #1 ``record_orb_capture`` under marker key ``orb2``. Does not
        overwrite a valid post-07:30 freeze unless ``force=True`` (e.g. replacing a
        premature stamp after a real ≥07:30 recapture).
        """
        if not orb2_snapshot:
            return False

        date_key = self._date_key()
        marker = self._load_marker(date_key) or {}
        existing = marker.get("orb2") or {}
        if (
            not force
            and orb2_marker_is_valid_day_freeze(existing)
        ):
            log.info(
                "ORB2_MARKER | action=skip_overwrite | reason=valid_day_freeze_exists | "
                "date=%s | captured=%s",
                date_key,
                existing.get("captured_count"),
            )
            return False

        # Refuse to persist a premature / incomplete-hour snapshot.
        captured_dt = _snapshot_capture_datetime(orb2_snapshot)
        candidate = {
            "captured": True,
            "captured_at": captured_dt.isoformat(),
            "captured_epoch": captured_dt.timestamp(),
            "captured_count": captured_count,
            "total_symbols": total_symbols,
            "snapshot": orb2_snapshot,
            "metadata": metadata or {},
            "alert_sent": bool((metadata or {}).get("alert_sent", False)),
            "window_pt": "06:30-07:30",
            "orb_label": "ORB#2",
        }
        if not orb2_marker_is_valid_day_freeze(candidate):
            log.warning(
                "ORB2_MARKER | action=reject_persist | reason=premature_or_invalid_snapshot | "
                "date=%s | captured=%s",
                date_key,
                captured_count,
            )
            return False

        marker["orb2"] = candidate
        self._save_marker(date_key, marker)
        log.info(
            "ORB2_MARKER | action=recorded | date=%s | captured=%s/%s | window=06:30-07:30",
            date_key,
            captured_count,
            total_symbols,
        )
        return True

    def record_trendline_geometry_merge(
        self,
        *,
        candidates: Optional[Dict[str, Dict[str, Any]]] = None,
        orb2_break_events: Optional[Dict[str, Any]] = None,
        orb2_extension_built: Optional[Dict[str, Any]] = None,
        orb2_extreme_ts: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        prune_symbol: Optional[str] = None,
        prune_keep_slot: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Merge TL1/TL2/TL3 A1/A2/slope (+ inject state) into today's marker.

        Never overwrites an existing valid candidate key unless pruned for a
        newer slot (TL2 supersedes TL1; TL3 supersedes TL1+TL2).
        Returns merge diagnostics.
        """
        from modules.trendline_geometry_persist import merge_geometry_payload

        date_key = self._date_key()
        marker = self._load_marker(date_key) or {}
        existing = marker.get("trendline_geometry") if isinstance(marker.get("trendline_geometry"), dict) else {}
        merged = merge_geometry_payload(
            existing,
            candidates=candidates,
            orb2_break_events=orb2_break_events,
            orb2_extension_built=orb2_extension_built,
            orb2_extreme_ts=orb2_extreme_ts,
            prune_symbol=prune_symbol,
            prune_keep_slot=prune_keep_slot,
        )
        if isinstance(metadata, dict) and metadata:
            meta = dict(merged.get("metadata") or {})
            meta.update(metadata)
            merged["metadata"] = meta
        marker["trendline_geometry"] = merged
        self._save_marker(date_key, marker)
        added = int(merged.get("last_merge_added") or 0)
        skipped = int(merged.get("last_merge_skipped") or 0)
        total = len(merged.get("candidates") or {})
        log.info(
            "TL_GEOMETRY_MARKER | action=merged | date=%s | candidates=%d | added=%d | "
            "skipped_overwrite=%d | break_syms=%d",
            date_key,
            total,
            added,
            skipped,
            len(merged.get("orb2_break_events") or {}),
        )
        return {
            "ok": True,
            "added": added,
            "skipped": skipped,
            "candidates": total,
        }

    def get_trendline_geometry(self) -> Dict[str, Any]:
        """Return today's trendline_geometry freeze blob (may be empty)."""
        state = self.get_today_state() or {}
        geo = state.get("trendline_geometry")
        return dict(geo) if isinstance(geo, dict) else {}

    def record_tl1_build_pending(self, *, armed: bool) -> None:
        """Persist TL1-pending so a Cloud Run kill cannot drop the 07:30 arm.

        In-memory ``_trendline_build_pending_at_730`` is lost on replacement.
        Recover used to stamp TL1 complete after 08:00 when pending was gone.
        """
        date_key = self._date_key()
        marker = self._load_marker(date_key) or {}
        if armed:
            marker["tl1_build_pending"] = {
                "armed": True,
                "pt_date": date_key,
                "updated_at": datetime.now(PT_TZ).isoformat(),
            }
        else:
            marker.pop("tl1_build_pending", None)
        self._save_marker(date_key, marker)

    def get_tl1_build_pending(self) -> bool:
        """True when today's marker says TL1 was armed and not yet finished."""
        state = self.get_today_state() or {}
        blob = state.get("tl1_build_pending")
        return bool(isinstance(blob, dict) and blob.get("armed"))

    def record_watch_fsm_merge(self, paths: Dict[str, Dict[str, Any]]) -> bool:
        """Overwrite today's ``watch_fsm`` hot set (CONFIRM/READY/EXECUTED only).

        Called by debounced discovery_watch_fsm_persist flushes — not every scan tick.
        """
        if not isinstance(paths, dict):
            return False
        date_key = self._date_key()
        marker = self._load_marker(date_key) or {}
        clean: Dict[str, Dict[str, Any]] = {}
        for path_key in ("orb1", "orb2", "tl"):
            block = paths.get(path_key) if isinstance(paths.get(path_key), dict) else {}
            out_block: Dict[str, Any] = {}
            for k, row in (block or {}).items():
                if not isinstance(row, dict):
                    continue
                sym = str(row.get("symbol") or k or "").strip().upper()
                if not sym and path_key != "tl":
                    continue
                out_block[str(k)] = _json_safe(dict(row))
            clean[path_key] = out_block
        marker["watch_fsm"] = {
            "updated_at": datetime.now(PT_TZ).isoformat(),
            "paths": clean,
        }
        self._save_marker(date_key, marker)
        n = sum(len(v or {}) for v in clean.values())
        log.info(
            "WATCH_FSM_MARKER | action=recorded | date=%s | hot_count=%d",
            date_key,
            n,
        )
        return True

    def mark_orb_alert_sent(self) -> None:
        """Persist that the ORB#1 Capture Telegram alert was delivered for today."""
        date_key = self._date_key()
        marker = self._load_marker(date_key) or {}
        orb = marker.get("orb") or {}
        if not orb.get("captured"):
            return
        orb["alert_sent"] = True
        meta = orb.get("metadata") or {}
        meta["alert_sent"] = True
        orb["metadata"] = meta
        marker["orb"] = orb
        self._save_marker(date_key, marker)
        log.info("📝 ORB#1 capture alert marked sent for %s", date_key)

    def mark_orb2_alert_sent(self) -> None:
        """Persist that the ORB#2 Capture Telegram alert was delivered for today."""
        date_key = self._date_key()
        marker = self._load_marker(date_key) or {}
        orb2 = marker.get("orb2") or {}
        if not orb2.get("captured"):
            return
        orb2["alert_sent"] = True
        meta = orb2.get("metadata") or {}
        meta["alert_sent"] = True
        orb2["metadata"] = meta
        marker["orb2"] = orb2
        self._save_marker(date_key, marker)
        log.info("📝 ORB#2 capture alert marked sent for %s", date_key)

    def record_signal_collection(
        self,
        signals: List[Dict[str, Any]],
        total_scanned: int,
        mode: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Persist SO signal collection summary for the trading day."""
        date_key = self._date_key()
        marker = self._load_marker(date_key) or {}

        sanitized_signals = self._sanitize_signals(signals)
        now_pt = datetime.now(PT_TZ)

        signals_entry = marker.get("signals", {})
        signals_entry.update(
            {
                "collected": True,
                "collection_time": now_pt.isoformat(),
                "collection_epoch": now_pt.timestamp(),
                "signal_count": len(sanitized_signals),
                "total_scanned": total_scanned,
                "mode": mode,
                "signals": sanitized_signals,
                "metadata": metadata or {},
            }
        )
        marker["signals"] = signals_entry

        self._save_marker(date_key, marker)
        log.info(
            "📝 Signal collection marker recorded for %s (%s signals)",
            date_key,
            len(sanitized_signals),
        )

    def record_signal_execution(
        self,
        executed_signals: List[Dict[str, Any]],
        rejected_signals: List[Dict[str, Any]],
        mode: str,
        metadata: Optional[Dict[str, Any]] = None,
        *,
        collected_signal_count: Optional[int] = None,
        terminal_reject_complete: Optional[bool] = None,
    ) -> None:
        """Persist SO execution results for the trading day."""
        date_key = self._date_key()
        marker = self._load_marker(date_key) or {}

        sanitized_executed = self._sanitize_signals(executed_signals)
        sanitized_rejected = self._sanitize_signals(rejected_signals)
        now_pt = datetime.now(PT_TZ)

        meta = dict(metadata or {})
        if collected_signal_count is None:
            collected_signal_count = int(marker.get("signals", {}).get("signal_count") or 0)
        if terminal_reject_complete is None:
            terminal_reject_complete = bool(
                collected_signal_count <= 0
                or len(sanitized_executed) > 0
                or (
                    len(sanitized_rejected) >= collected_signal_count
                    and collected_signal_count > 0
                )
            )

        from .so_terminal_rejects import batch_completion_allowed

        batch_ok = batch_completion_allowed(
            collected_count=int(collected_signal_count or 0),
            executed_count=len(sanitized_executed),
            rejected_count=len(sanitized_rejected),
            terminal_complete=bool(terminal_reject_complete),
        )
        meta["collected_signal_count"] = int(collected_signal_count or 0)
        meta["terminal_reject_complete"] = bool(terminal_reject_complete)
        meta["batch_completion_allowed"] = bool(batch_ok)
        if not batch_ok:
            meta["batch_incomplete_reason"] = (
                "signal_count>0 with executed_count=0 and incomplete terminal rejects"
            )

        signals_entry = marker.get("signals", {})
        update_payload: Dict[str, Any] = {
            "execution_time": now_pt.isoformat(),
            "execution_epoch": now_pt.timestamp(),
            "executed_count": len(sanitized_executed),
            "rejected_count": len(sanitized_rejected),
            "executed_signals": sanitized_executed,
            "rejected_signals": sanitized_rejected,
            "mode": mode,
            "metadata": meta,
        }
        if batch_ok:
            update_payload["so_execution_batch_completed"] = True
            update_payload["execution_completed"] = True
        else:
            update_payload["so_execution_batch_incomplete"] = True
            update_payload.pop("so_execution_batch_completed", None)
            signals_entry.pop("execution_completed", None)
            log.warning(
                "SO_EXECUTION_MARKER_INCOMPLETE | date=%s | collected=%s | executed=%s | rejected=%s | "
                "terminal_reject_complete=%s",
                date_key,
                collected_signal_count,
                len(sanitized_executed),
                len(sanitized_rejected),
                terminal_reject_complete,
            )
        signals_entry.update(update_payload)
        marker["signals"] = signals_entry

        self._save_marker(date_key, marker)
        kind = "orb_so_batch_completed" if batch_ok else "orb_so_batch_incomplete"
        log.info(
            "SO_EXECUTION_MARKER_WRITTEN | kind=%s | date=%s | executed=%s | rejected=%s",
            kind,
            date_key,
            len(sanitized_executed),
            len(sanitized_rejected),
        )
        log.info(
            "📝 Signal execution marker recorded for %s (executed=%s, rejected=%s)",
            date_key,
            len(sanitized_executed),
            len(sanitized_rejected),
        )

    def record_so_step5_empty_pending(self, mode: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        """
        Step 5 ran with zero pending SO symbols. Does NOT set execution_completed / batch dedupe flags
        (those are reserved for real _process_orb_signals completion — Rev May12).
        Clears poisoned legacy fields from the historic empty Step-5 path.
        """
        date_key = self._date_key()
        marker = self._load_marker(date_key) or {}
        now_pt = datetime.now(PT_TZ)
        signals_entry = marker.get("signals", {})
        meta_in = signals_entry.get("metadata") or {}
        # Strip false execution_completed from old bug (empty Step 5 wrote execution_completed).
        if signals_entry.get("execution_completed") and int(signals_entry.get("executed_count") or 0) == 0:
            if int(signals_entry.get("rejected_count") or 0) == 0 and meta_in.get("note") == "No signals collected":
                signals_entry.pop("execution_completed", None)
                signals_entry.pop("execution_time", None)
                signals_entry.pop("execution_epoch", None)
                signals_entry.pop("so_execution_batch_completed", None)
        signals_entry["so_step5_evaluated_empty_pending"] = True
        signals_entry["so_step5_evaluated_empty_pending_at"] = now_pt.isoformat()
        signals_entry["so_step5_empty_pending_metadata"] = dict(metadata or {})
        signals_entry["mode"] = mode
        marker["signals"] = signals_entry
        self._save_marker(date_key, marker)
        log.info(
            "SO_EXECUTION_MARKER_WRITTEN | kind=step5_empty_pending | date=%s | note=%s",
            date_key,
            (metadata or {}).get("note", ""),
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _date_key(self, trading_date: Optional[date] = None) -> str:
        trading_date = trading_date or _current_trading_date()
        return trading_date.isoformat()

    def _marker_path(self, date_key: str) -> Path:
        return self._base_path / f"{date_key}.json"

    def _load_marker(self, date_key: str) -> Optional[Dict[str, Any]]:
        path = self._marker_path(date_key)
        if path.exists():
            try:
                with path.open("r", encoding="utf-8") as handle:
                    return json.load(handle)
            except Exception as exc:  # pragma: no cover - corrupted file edge case
                log.warning("⚠️ Failed to read local marker %s: %s", path, exc)

        gcs_content = gcs_read_daily_markers_string(self._gcs, f"{date_key}.json")
        if gcs_content:
            try:
                data = json.loads(gcs_content)
                # Cache locally for faster subsequent reads
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("w", encoding="utf-8") as handle:
                    json.dump(data, handle, indent=2, sort_keys=True)
                return data
            except Exception as exc:  # pragma: no cover - corrupted GCS content
                log.warning("⚠️ Failed to parse GCS marker %s: %s", date_key, exc)

        return None

    def _save_marker(self, date_key: str, marker: Dict[str, Any]) -> None:
        if date_key:
            marker.setdefault("ymd", date_key)
            marker.setdefault("date", date_key)
        path = self._marker_path(date_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(marker, handle, indent=2, sort_keys=True)

        if self._gcs.enabled:
            self._gcs.upload_string(
                gcs_daily_markers_path(f"{date_key}.json"),
                json.dumps(marker, sort_keys=True),
            )

        # Also sync a Priority Optimizer-style daily signals file (and enforce retention)
        # so we always have the SO signal list for Red Day analysis and P&L studies.
        try:
            self._sync_priority_optimizer_signals(date_key, marker)
        except Exception as exc:  # pragma: no cover - defensive logging only
            log.warning("⚠️ Failed to sync Priority Optimizer signals for %s: %s", date_key, exc)

    def _sync_priority_optimizer_signals(self, date_key: str, marker: Dict[str, Any]) -> None:
        """
        Create / update a Priority Optimizer daily signals file in GCS and
        enforce a short retention window (default: last 2 calendar days ≈ 48h).

        This ensures that:
        - Every SO signal collection (even on Red Days where execution is skipped)
          is persisted in a consistent format under:
              {research_app|final_app}/priority_optimizer/daily_signals/YYYY-MM-DD_signals.json
        - Only recent daily lists are kept (see PRIORITY_OPTIMIZER_DAILY_SIGNALS_RETENTION_DAYS).
        """
        if not self._gcs.enabled:
            return

        from modules.runtime_mode import gcs_daily_markers_path, gcs_daily_signals_object

        signals_entry = marker.get("signals") or {}
        raw_signals = signals_entry.get("signals") or []
        total_scanned = signals_entry.get("total_scanned", 0)
        mode = signals_entry.get("mode")
        metadata = dict(signals_entry.get("metadata") or {})

        # Rev 00298: Canonical SO list for the day is merged in signal_collection_730.
        # Marker `signals.signals` can be a subset (e.g. execution-only); prefer 730 when present.
        try:
            p730 = gcs_daily_markers_path(f"signal_collection_730/{date_key}.json")
            raw730 = self._gcs.read_string(p730)
            if raw730:
                d730 = json.loads(raw730)
                if d730.get("date") == date_key:
                    pend = d730.get("pending_so_signals") or []
                    if pend:
                        raw_signals = pend
                        allow = d730.get("allowed_so_symbols") or []
                        if allow:
                            total_scanned = len(allow)
                        metadata.setdefault("synced_from", "signal_collection_730")
        except Exception as exc:
            log.debug("730 read for Priority Optimizer sync skipped: %s", exc)

        if not raw_signals:
            # Nothing to persist for this day
            return

        # Sanitize signals to a compact, stable structure (reuses existing helper)
        sanitized_signals = self._sanitize_signals(raw_signals)

        payload: Dict[str, Any] = {
            "date": date_key,
            "mode": mode,
            "total_scanned": total_scanned,
            "signal_count": len(sanitized_signals),
            "signals": sanitized_signals,
            "metadata": metadata,
        }

        gcs_path = gcs_daily_signals_object(date_key)
        ok = self._gcs.upload_string(gcs_path, json.dumps(payload, sort_keys=True))
        if ok:
            log.info(
                "☁️ Priority Optimizer signal list saved for %s (%s signals) → %s",
                date_key,
                len(sanitized_signals),
                gcs_path,
            )
        else:
            log.warning(
                "⚠️ Failed to upload Priority Optimizer signal list for %s to %s",
                date_key,
                gcs_path,
            )
            return

        from modules.priority_optimizer_daily_signals_retention import (
            prune_priority_optimizer_daily_signals,
        )

        prune_priority_optimizer_daily_signals(self._gcs)

    @staticmethod
    def _sanitize_signals(signals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        sanitized: List[Dict[str, Any]] = []
        for signal in signals or []:
            if not isinstance(signal, dict):
                continue
            sanitized.append(
                {
                    "symbol": signal.get("symbol"),
                    "original_symbol": signal.get("original_symbol"),
                    "signal_type": signal.get("signal_type"),
                    "side": signal.get("side"),
                    "price": signal.get("price"),
                    "confidence": signal.get("confidence"),
                    "position_size_pct": signal.get("position_size_pct"),
                    "reasoning": signal.get("reasoning"),
                    "inverse_symbol": signal.get("inverse_symbol"),
                    # Priority Ranking Formula v2.1 factors (Rev 00106, Nov 6, 2025)
                    "priority_score": signal.get("priority_score"),
                    "rank": signal.get("rank"),
                    "vwap_distance_pct": signal.get("vwap_distance_pct"),
                    "rs_vs_spy": signal.get("rs_vs_spy"),
                    "orb_volume_ratio": signal.get("orb_volume_ratio"),
                    "orb_range_pct": signal.get("orb_range_pct"),
                    "rsi": signal.get("rsi"),
                }
            )
        return sanitized


_tracker_instance: Optional[DailyRunTracker] = None


def get_daily_run_tracker() -> DailyRunTracker:
    """Return singleton instance of DailyRunTracker."""
    global _tracker_instance
    if _tracker_instance is None:
        _tracker_instance = DailyRunTracker()
    return _tracker_instance
