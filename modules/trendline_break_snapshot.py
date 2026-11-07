"""
Trendline break snapshots for Priority Optimizer (one daily session file).

Captures per-symbol technical + structure context at **first trendline break**
each session (analogous to ORB SO / ORB1 Breakout `signal_collection_730` at 7:30).

Writes:
- GCS: {research_app|final_app}/priority_optimizer/trendline_break_snapshots/YYYY-MM-DD_breaks.json
- Local: priority_optimizer/trendline_break_snapshots/YYYY-MM-DD_breaks.json

Controlled by ENABLE_EXECUTION_FEATURE_SNAPSHOT (same master switch as ORB snapshots).
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .execution_feature_snapshot import (
    _safe_json_value,
    extract_path_features,
    extract_signal_features,
    extract_trendline_pool_features,
    merge_comprehensive_market_data,
)
from .po_feature_schema import SCHEMA_VERSION as PO_SCHEMA_VERSION, schema_for_strategy

try:
    from easyTrendline.trendline_models import normalize_trendline_slot
except Exception:  # pragma: no cover

    def normalize_trendline_slot(raw: Any) -> str:  # type: ignore[misc]
        return str(raw or "trendline1").strip().lower() or "trendline1"
from .trendline_telemetry import derive_orb_zone, enrich_signal_row_for_features

log = logging.getLogger(__name__)

SCHEMA_VERSION = PO_SCHEMA_VERSION
GCS_PREFIX = "priority_optimizer/trendline_break_snapshots/"
STAGE = "trendline_break_snapshot"
SNAPSHOT_STRATEGY = "easy_trendline_0dte"


def _skip_local_write() -> bool:
    explicit = os.getenv("EXECUTION_SNAPSHOT_LOCAL", "").strip().lower()
    if explicit in ("1", "true", "yes"):
        return False
    if explicit in ("0", "false", "no"):
        return True
    return bool(os.getenv("K_SERVICE"))


def _snapshots_enabled() -> bool:
    try:
        from .config_loader import get_config_value
    except Exception:
        return True
    master = str(get_config_value("ENABLE_EXECUTION_FEATURE_SNAPSHOT", "true")).strip().lower()
    if master not in ("1", "true", "yes"):
        return False
    tl = str(get_config_value("ENABLE_TRENDLINE_BREAK_SNAPSHOT", "true")).strip().lower()
    return tl in ("1", "true", "yes")


def _date_key_pt(now: Optional[datetime] = None) -> str:
    from zoneinfo import ZoneInfo

    ts = now or datetime.now(ZoneInfo("America/Los_Angeles"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=ZoneInfo("America/Los_Angeles"))
    return ts.astimezone(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")


def _candidate_state_value(candidate: Any) -> Optional[str]:
    state = getattr(candidate, "state", None)
    if state is None:
        return None
    return getattr(state, "value", str(state))


def build_break_snapshot_record(
    *,
    candidate: Any,
    break_event: Any,
    break_bar: Any,
    trade_id: str,
    body_ratio_now: float = 0.0,
    expected_move_pct: float = 0.0,
    velocity: float = 0.0,
    close_position_in_candle: Optional[float] = None,
    market_context: Optional[Dict[str, Any]] = None,
    rsi: Optional[float] = None,
    vwap_position: Optional[str] = None,
    volume_ratio: Optional[float] = None,
    vwap: Optional[float] = None,
    entry_distance_pct: Optional[float] = None,
    score: Optional[float] = None,
    orb_zone: Optional[str] = None,
    comprehensive_data: Optional[Dict[str, Any]] = None,
    options_features: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Flat JSON row for one symbol at first break (Priority Optimizer / cohort review)."""
    setup = dict(getattr(candidate, "setup_payload", None) or {})
    be_meta = dict(getattr(break_event, "metadata", None) or {})
    tl = getattr(candidate, "trendline", None)
    direction = getattr(candidate, "direction", None)
    direction_val = getattr(direction, "value", direction) if direction else None

    close_px = float(getattr(break_event, "close_price", 0.0) or 0.0)
    line_px = float(getattr(break_event, "trendline_price", 0.0) or 0.0)
    orb_high = setup.get("orb_high")
    orb_low = setup.get("orb_low")
    orb_range = None
    try:
        if orb_high is not None and orb_low is not None:
            orb_range = float(orb_high) - float(orb_low)
    except (TypeError, ValueError):
        orb_range = None

    orb_pct_from_low = None
    try:
        if orb_high is not None and orb_low is not None and float(orb_high) > float(orb_low):
            orb_pct_from_low = (close_px - float(orb_low)) / (float(orb_high) - float(orb_low))
    except (TypeError, ValueError):
        orb_pct_from_low = None

    zone = orb_zone or derive_orb_zone(
        close=close_px,
        orb_low=orb_low,
        orb_high=orb_high,
        orb_pct_from_low=orb_pct_from_low,
    )
    if zone and setup.get("orb_zone") is None:
        setup["orb_zone"] = zone

    # Trade side = CALL|PUT from option mapping (bear line → call, bull line → put).
    # Never stamp LONG/SHORT; keep bear/bull only as line_geometry on the record.
    expected_opt = str(setup.get("expected_option_side") or "").strip().lower()
    if expected_opt not in ("call", "put"):
        expected_opt = "put" if str(direction_val or "").lower() == "bull" else "call"
    trade_side = expected_opt.upper()
    sig_row: Dict[str, Any] = {
        "symbol": getattr(candidate, "symbol", None),
        "side": trade_side,
        "direction": trade_side,
        "option_type": expected_opt,
        "option_type_label": trade_side,
        "expected_option_side": expected_opt,
        "current_price": close_px,
        "confidence": getattr(candidate, "confidence_score", None),
        "orb_high": orb_high,
        "orb_low": orb_low,
        "orb_range": orb_range,
        "orb_range_pct": setup.get("orb_range_pct"),
        "metadata": {
            "setup_type": setup.get("setup_type"),
            "expected_option_side": expected_opt,
            "trade_id": trade_id,
            "line_geometry": str(direction_val or "").lower() or None,
        },
    }
    if sig_row.get("orb_range_pct") is None and orb_high is not None and orb_low is not None:
        try:
            low_f = float(orb_low)
            if low_f > 0:
                sig_row["orb_range_pct"] = ((float(orb_high) - low_f) / low_f) * 100.0
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    enrich_signal_row_for_features(
        sig_row,
        rsi=rsi,
        volume_ratio=volume_ratio,
        vwap=vwap,
        vwap_position=vwap_position,
        entry_distance_pct=entry_distance_pct,
        score=score,
        orb_zone=zone,
    )
    sig_row["open"] = float(getattr(break_bar, "open", 0.0) or 0.0)
    sig_row["high"] = float(getattr(break_bar, "high", 0.0) or 0.0)
    sig_row["low"] = float(getattr(break_bar, "low", 0.0) or 0.0)
    sig_row["close"] = close_px
    sig_row["volume"] = getattr(break_bar, "volume", None)
    sig_row["orb_open"] = setup.get("orb_open")
    sig_row["orb_close"] = setup.get("orb_close")
    sig_row["orb_volume"] = setup.get("orb_volume")
    sig_row["entry_price"] = close_px
    sig_row["priority_score"] = score
    if isinstance(sig_row.get("metadata"), dict):
        sig_row["metadata"].update(
            {
                "structure_fit_mse": setup.get("structure_fit_mse"),
                "structure_selection_reason": setup.get("structure_selection_reason"),
                "trendline_structure_source": setup.get("trendline_structure_source"),
                "line_geometry": setup.get("line_geometry") or setup.get("setup_type"),
                "break_distance_pct": float(getattr(break_event, "break_distance_pct", 0.0) or 0.0),
                "entry_distance_pct": entry_distance_pct,
                "orb_zone": zone,
                "vwap_position": vwap_position,
            }
        )
    merge_comprehensive_market_data(
        sig_row,
        comprehensive_data,
        snapshot_strategy="easy_trendline_0dte",
    )
    if options_features:
        for key, val in options_features.items():
            if val is not None:
                sig_row[key] = val

    break_ts = getattr(break_event, "candle_ts", None)
    first_break_at = getattr(candidate, "first_break_at", None)
    built_at = getattr(tl, "built_at", None) if tl else None

    record: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "trade_id": trade_id,
        "symbol": (getattr(candidate, "symbol", None) or "").strip().upper(),
        "captured_at_utc": datetime.utcnow().isoformat(),
        "break_timestamp": _safe_json_value(break_ts),
        "first_break_timestamp": _safe_json_value(first_break_at),
        "direction": trade_side,
        "line_geometry_direction": direction_val,
        "expected_option_side": expected_opt,
        "setup_type": setup.get("setup_type"),
        "trigger_direction": setup.get("trigger_direction"),
        "candidate_source": setup.get("candidate_source", "full_0dte_list"),
        "trendline_structure_source": setup.get("trendline_structure_source"),
        "trendline_slot": normalize_trendline_slot(setup.get("trendline_slot")),
        "envelope_role": setup.get("envelope_role"),
        "structure_selection_reason": setup.get("structure_selection_reason"),
        "structure_fit_mse": setup.get("structure_fit_mse"),
        "structure_failure_type": setup.get("structure_failure_type"),
        "construction_rule": setup.get("construction_rule"),
        "line_geometry": setup.get("line_geometry")
        or str(direction_val or "").lower()
        or setup.get("setup_type"),
        "line_price_at_break": line_px,
        "close_at_break": close_px,
        "break_distance_pct": float(getattr(break_event, "break_distance_pct", 0.0) or 0.0),
        "break_reason": getattr(break_event, "reason", None),
        "body_ratio_vs_avg": float(body_ratio_now),
        "expected_move_pct": float(expected_move_pct),
        "velocity_pct": float(velocity),
        "close_position_in_candle": close_position_in_candle,
        "distance_increasing": be_meta.get("distance_increasing"),
        "body_ratio_vs_prev": be_meta.get("body_ratio_vs_prev"),
        "break_archetype": be_meta.get("break_archetype"),
        "orb_high": orb_high,
        "orb_low": orb_low,
        "orb_range": orb_range,
        "orb_range_pct": sig_row.get("orb_range_pct") or setup.get("orb_range_pct"),
        "orb_pct_from_low": orb_pct_from_low,
        "orb_zone": zone,
        "rsi": rsi,
        "vwap_position": vwap_position,
        "volume_ratio": volume_ratio,
        "entry_distance_pct": entry_distance_pct,
        "score": score,
        "priority_score": score,
        # The engine initialises pressure_score=0.0 / entry_mode="" and never
        # updates them (retired). A constant is not a measurement: stamp None
        # unless a real non-zero / non-empty value exists.
        "pressure_score": (
            float(getattr(candidate, "pressure_score"))
            if isinstance(getattr(candidate, "pressure_score", None), (int, float))
            and float(getattr(candidate, "pressure_score")) != 0.0
            else None
        ),
        "entry_mode": getattr(candidate, "entry_mode", None) or None,
        "candidate_state": _candidate_state_value(candidate),
        "candidate_state_reason": getattr(candidate, "state_reason", None),
        "break_attempt_count": int(getattr(candidate, "break_attempt_count", 0) or 0),
        "trendline_slope_per_second": getattr(tl, "slope_per_second", None) if tl else None,
        "trendline_built_at": _safe_json_value(built_at),
        "minutes_from_730_to_break": (
            max(0.0, (break_ts - built_at).total_seconds() / 60.0)
            if break_ts and built_at
            else None
        ),
        "break_bar": {
            "open": float(getattr(break_bar, "open", 0.0) or 0.0),
            "high": float(getattr(break_bar, "high", 0.0) or 0.0),
            "low": float(getattr(break_bar, "low", 0.0) or 0.0),
            "close": float(getattr(break_bar, "close", 0.0) or 0.0),
            "volume": getattr(break_bar, "volume", None),
            "ts": _safe_json_value(getattr(break_bar, "ts", None)),
        },
        "features_priority_optimizer": extract_trendline_pool_features(
            sig_row, include_nulls=True
        ),
        "features_dense": extract_trendline_pool_features(sig_row, include_nulls=True),
        "features_89": extract_trendline_pool_features(sig_row, include_nulls=True),
        "feature_schema_keys": list(schema_for_strategy("easy_trendline_0dte")),
        "feature_schema_89_keys": list(schema_for_strategy("easy_trendline_0dte")),
        "outcome_labels": {
            "desk_review": None,
            "should_emit": None,
            "executed": False,
            "final_gate_skip_reason": None,
        },
    }
    if market_context:
        record["market_context"] = dict(market_context)
    return record


class TrendlineBreakSnapshotCollector:
    """Append-first-break rows into one JSON file per PT session date."""

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        root = Path(__file__).resolve().parent.parent
        self._local_dir = base_dir or (root / "priority_optimizer" / "trendline_break_snapshots")
        self._local_dir.mkdir(parents=True, exist_ok=True)
        self._seen_trade_ids: Dict[str, set] = {}
        # In-memory session so Cloud Run (no local file) does not GCS-read
        # on every first-break. Sync GCS on this path froze /health Aug 24.
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._gcs_lock = threading.Lock()
        self._gcs_pending: Dict[str, str] = {}
        self._gcs_thread: Optional[threading.Thread] = None

    def _file_name(self, date_key: str) -> str:
        return f"{date_key}_breaks.json"

    def _gcs_path(self, date_key: str) -> str:
        try:
            from .runtime_mode import gcs_trendline_break_snapshot_object

            return gcs_trendline_break_snapshot_object(self._file_name(date_key))
        except Exception:
            return f"{GCS_PREFIX}{self._file_name(date_key)}"

    def _gcs_read_candidates(self, date_key: str) -> list:
        fname = self._file_name(date_key)
        paths = [self._gcs_path(date_key)]
        try:
            from .runtime_mode import gcs_trendline_break_snapshot_list_prefixes

            for prefix in gcs_trendline_break_snapshot_list_prefixes():
                cand = f"{prefix.rstrip('/')}/{fname}"
                if cand not in paths:
                    paths.append(cand)
        except Exception:
            legacy = f"{GCS_PREFIX}{fname}"
            if legacy not in paths:
                paths.append(legacy)
        return paths

    def _local_path(self, date_key: str) -> Path:
        return self._local_dir / self._file_name(date_key)

    def _load_session(self, date_key: str) -> Dict[str, Any]:
        from zoneinfo import ZoneInfo

        cached = self._sessions.get(date_key)
        if isinstance(cached, dict) and cached.get("date") == date_key:
            return cached

        path = self._local_path(date_key)
        if path.exists():
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and data.get("date") == date_key:
                    self._sessions[date_key] = data
                    return data
            except Exception as exc:
                log.debug("Trendline break snapshot local read failed: %s", exc)
        try:
            from .gcs_persistence import get_gcs_persistence

            gcs = get_gcs_persistence()
            if gcs.enabled:
                for gpath in self._gcs_read_candidates(date_key):
                    raw = gcs.read_string(gpath)
                    if not raw:
                        continue
                    data = json.loads(raw)
                    if isinstance(data, dict) and data.get("date") == date_key:
                        self._sessions[date_key] = data
                        return data
        except Exception as exc:
            log.debug("Trendline break snapshot GCS read failed: %s", exc)

        now_pt = datetime.now(ZoneInfo("America/Los_Angeles"))
        try:
            from .runtime_mode import gcs_app_label, gcs_namespace
            _app, _ns = gcs_app_label(), gcs_namespace()
        except Exception:
            _app, _ns = "RESEARCH", "research_app"
        session = {
            "schema_version": SCHEMA_VERSION,
            "date": date_key,
            "stage": STAGE,
            "source_app": _app,
            "app": _app,
            "gcs_namespace": _ns,
            "snapshot_strategy": SNAPSHOT_STRATEGY,
            "captured_at_pt": now_pt.isoformat(),
            "break_count": 0,
            "breaks": [],
        }
        self._sessions[date_key] = session
        return session

    def record_first_break(
        self,
        record: Dict[str, Any],
        *,
        date_key: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Append one break row (deduped by trade_id). Returns (gcs_path, local_path).
        """
        if not _snapshots_enabled():
            return None, None

        tid = str(record.get("trade_id") or "").strip()
        sym = str(record.get("symbol") or "").strip().upper()
        if not tid or not sym:
            return None, None

        dk = date_key or _date_key_pt()
        seen = self._seen_trade_ids.setdefault(dk, set())
        if tid in seen:
            return None, None

        session = self._load_session(dk)
        breaks: List[Dict[str, Any]] = list(session.get("breaks") or [])
        existing_ids = {str(r.get("trade_id") or "") for r in breaks if isinstance(r, dict)}
        if tid in existing_ids:
            seen.add(tid)
            return None, None

        breaks.append(record)
        session["breaks"] = breaks
        session["break_count"] = len(breaks)
        try:
            from .runtime_mode import gcs_app_label, gcs_namespace
            session["source_app"] = gcs_app_label()
            session["app"] = session["source_app"]
            session["gcs_namespace"] = gcs_namespace()
        except Exception:
            pass
        from zoneinfo import ZoneInfo

        session["last_updated_pt"] = datetime.now(ZoneInfo("America/Los_Angeles")).isoformat()
        seen.add(tid)

        self._sessions[dk] = session
        body = json.dumps(session, separators=(",", ":"), default=str)

        local_written: Optional[str] = None
        if not _skip_local_write():
            try:
                lp = self._local_path(dk)
                lp.write_text(body, encoding="utf-8")
                local_written = str(lp)
            except Exception as exc:
                log.warning("Trendline break snapshot local write failed: %s", exc)

        gcs_written = self._schedule_gcs_upload(dk, body)

        log.warning(
            "TRENDLINE_BREAK_SNAPSHOT | date=%s | symbol=%s | trade_id=%s | orb_zone=%s | velocity_pct=%.6f | break_count=%s | gcs=%s | local=%s",
            dk,
            sym,
            tid,
            record.get("orb_zone") or "-",
            float(record.get("velocity_pct") or 0.0),
            session["break_count"],
            gcs_written or "-",
            local_written or "-",
        )
        return gcs_written, local_written

    def _schedule_gcs_upload(self, date_key: str, body: str) -> Optional[str]:
        """Queue GCS write off the asyncio loop. Latest body for the date wins."""
        try:
            from .gcs_persistence import get_gcs_persistence

            gcs = get_gcs_persistence()
            if not gcs.enabled:
                return None
        except Exception:
            return None
        gpath = self._gcs_path(date_key)
        with self._gcs_lock:
            self._gcs_pending[date_key] = body
            thread = self._gcs_thread
            if thread is None or not thread.is_alive():
                self._gcs_thread = threading.Thread(
                    target=self._gcs_upload_worker,
                    name="tl-break-snapshot-gcs",
                    daemon=True,
                )
                self._gcs_thread.start()
        return gpath

    def _gcs_upload_worker(self) -> None:
        while True:
            with self._gcs_lock:
                if not self._gcs_pending:
                    self._gcs_thread = None
                    return
                items = list(self._gcs_pending.items())
                self._gcs_pending.clear()
            try:
                from .gcs_persistence import get_gcs_persistence

                gcs = get_gcs_persistence()
            except Exception as exc:
                log.warning("Trendline break snapshot GCS upload skipped: %s", exc)
                return
            if not gcs.enabled:
                return
            for date_key, body in items:
                try:
                    gcs.upload_string(self._gcs_path(date_key), body)
                except Exception as exc:
                    log.warning(
                        "Trendline break snapshot GCS upload skipped: date=%s | err=%s",
                        date_key,
                        exc,
                    )

    def find_break_for_symbol(
        self,
        symbol: str,
        *,
        date_key: Optional[str] = None,
        trendline_slot: Optional[str] = None,
        trade_id: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Return break row for ``symbol`` (prefer trade_id, then slot, then first match)."""
        sym = str(symbol or "").strip().upper()
        if not sym:
            return None
        dk = date_key or _date_key_pt()
        session = self._load_session(dk)
        rows = [r for r in (session.get("breaks") or []) if isinstance(r, dict)]
        tid = str(trade_id or "").strip()
        if tid:
            for row in rows:
                if str(row.get("trade_id") or "").strip() == tid:
                    return row
        slot = None
        if trendline_slot is not None:
            try:
                slot = normalize_trendline_slot(trendline_slot)
            except Exception:
                slot = str(trendline_slot or "").strip().lower() or None
        symbol_rows = [
            r for r in rows if str(r.get("symbol") or "").strip().upper() == sym
        ]
        if not symbol_rows:
            return None
        if slot:
            for row in reversed(symbol_rows):
                try:
                    row_slot = normalize_trendline_slot(row.get("trendline_slot"))
                except Exception:
                    row_slot = str(row.get("trendline_slot") or "trendline1").strip().lower()
                if row_slot == slot:
                    return row
            # Legacy rows without slot: only fall back for TL1.
            if slot == "trendline1":
                for row in symbol_rows:
                    if not row.get("trendline_slot"):
                        return row
            return None
        return symbol_rows[0]


_collector_singleton: Optional[TrendlineBreakSnapshotCollector] = None


def get_trendline_break_snapshot_collector() -> TrendlineBreakSnapshotCollector:
    global _collector_singleton
    if _collector_singleton is None:
        _collector_singleton = TrendlineBreakSnapshotCollector()
    return _collector_singleton
