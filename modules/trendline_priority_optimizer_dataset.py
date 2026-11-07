#!/usr/bin/env python3
"""
Trendline Priority Optimizer helpers (legacy JSONL event builder).

Trendline capture is now two artifacts only:
  - Break:  priority_optimizer/trendline_break_snapshots/YYYY-MM-DD_breaks.json
  - Fill:   priority_optimizer/execution_snapshots/*_trendline_options_executed.json

This module retains ``build_priority_optimizer_row`` for tests and optional tooling.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2
GCS_PREFIX = "priority_optimizer/trendline_priority_optimizer_dataset/"
STAGE = "trendline_priority_optimizer_dataset"


def _skip_local_write() -> bool:
    explicit = os.getenv("EXECUTION_SNAPSHOT_LOCAL", "").strip().lower()
    if explicit in ("1", "true", "yes"):
        return False
    if explicit in ("0", "false", "no"):
        return True
    return bool(os.getenv("K_SERVICE"))


def _snapshots_enabled() -> bool:
    try:
        from modules.config_loader import get_config_value
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


def build_priority_optimizer_row(
    *,
    candidate: Any,
    bar: Any,
    bars: List[Any],
    trade_id: str,
    decision: str,
    decision_reason: str,
    quality: Optional[Any] = None,
    signal: Optional[Any] = None,
    vwap: float = 0.0,
    vwap_position: str = "",
    rsi: Optional[float] = None,
    volume_ratio: Optional[float] = None,
    velocity_pct: Optional[float] = None,
    orb_zone: Optional[str] = None,
) -> Dict[str, Any]:
    from .trendline_telemetry import derive_orb_zone
    setup = dict(getattr(candidate, "setup_payload", None) or {})
    be = getattr(candidate, "break_event", None)
    close_px = float(getattr(bar, "close", 0.0) or 0.0)
    line_px = float(getattr(be, "trendline_price", 0.0) or 0.0) if be else 0.0
    orb_high = setup.get("orb_high")
    orb_low = setup.get("orb_low")
    orb_pos = None
    try:
        if orb_high is not None and orb_low is not None and float(orb_high) > float(orb_low):
            orb_pos = (close_px - float(orb_low)) / (float(orb_high) - float(orb_low))
    except (TypeError, ValueError):
        orb_pos = None

    body = abs(float(getattr(bar, "close", 0.0)) - float(getattr(bar, "open", 0.0)))
    rng = float(getattr(bar, "high", 0.0)) - float(getattr(bar, "low", 0.0))
    body_pct = body / max(rng, 1e-9) if rng > 0 else 0.0
    range_pct = rng / max(close_px, 1e-9)

    vol = float(getattr(bar, "volume", 0.0) or 0.0)
    avg_vol = 0.0
    if bars:
        vols = [float(getattr(b, "volume", 0.0) or 0.0) for b in bars if float(getattr(b, "volume", 0.0) or 0.0) > 0]
        avg_vol = sum(vols) / len(vols) if vols else vol

    direction = getattr(getattr(candidate, "direction", None), "value", None)
    option_side = setup.get("expected_option_side") or (
        "call" if str(direction).lower() == "bear" else "put"
    )
    zone = orb_zone or derive_orb_zone(
        close=close_px,
        orb_low=orb_low,
        orb_high=orb_high,
        orb_pct_from_low=orb_pos,
    )
    vel = float(velocity_pct) if velocity_pct is not None else 0.0

    row: Dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "captured_at_utc": datetime.utcnow().isoformat(),
        "timestamp": getattr(bar, "ts", datetime.utcnow()).isoformat() if hasattr(bar, "ts") else datetime.utcnow().isoformat(),
        "trade_id": trade_id,
        "symbol": (getattr(candidate, "symbol", "") or "").strip().upper(),
        "setup_type": setup.get("setup_type"),
        "direction": direction,
        "expected_option_side": option_side,
        "line_price": line_px,
        "close_price": close_px,
        "break_distance_pct": float(getattr(be, "break_distance_pct", 0.0) or 0.0) if be else 0.0,
        "entry_distance_pct": float(getattr(quality, "entry_distance_pct", 0.0) or 0.0) if quality else 0.0,
        "orb_high": orb_high,
        "orb_low": orb_low,
        "orb_position": orb_pos,
        "orb_zone": zone,
        "velocity_pct": vel,
        "vwap": float(vwap or getattr(quality, "vwap", 0.0) or 0.0),
        "vwap_position": vwap_position or (getattr(quality, "vwap_position", "") if quality else ""),
        "vwap_distance_pct": (
            getattr(quality, "vwap_distance_pct", None) if quality is not None else None
        ),
        "rsi": (
            float(rsi)
            if rsi is not None
            else (
                float(getattr(quality, "rsi"))
                if quality is not None and getattr(quality, "rsi", None) is not None
                else None
            )
        ),
        "rsi_missing": bool(
            (rsi is None and (quality is None or getattr(quality, "rsi", None) is None))
            or (quality is not None and getattr(quality, "rsi_missing", False))
        ),
        "volume": vol,
        "average_volume": avg_vol,
        "volume_ratio": (
            float(volume_ratio)
            if volume_ratio is not None
            else (
                float(getattr(quality, "volume_ratio"))
                if quality is not None and getattr(quality, "volume_ratio", None) is not None
                else None
            )
        ),
        "volume_ratio_missing": bool(
            (
                volume_ratio is None
                and (quality is None or getattr(quality, "volume_ratio", None) is None)
            )
            or (quality is not None and getattr(quality, "volume_ratio_missing", False))
        ),
        "macd_histogram": (
            None
            if quality is not None and getattr(quality, "macd_missing", False)
            else (
                getattr(quality, "macd_histogram", None)
                if quality is not None
                else None
            )
        ),
        "ema_12": getattr(quality, "ema_12", None) if quality is not None else None,
        "ema_26": getattr(quality, "ema_26", None) if quality is not None else None,
        "macd_missing": bool(quality is not None and getattr(quality, "macd_missing", False)),
        "candle_body_pct": body_pct,
        "candle_range_pct": range_pct,
        "continuation_distance": float(getattr(quality, "break_distance_pct", 0.0) or 0.0) if quality else 0.0,
        "score": float(getattr(quality, "score", 0.0) or 0.0) if quality else None,
        "decision": decision,
        "decision_reason": decision_reason,
        "option_contract": None,
        "later_max_favorable_move": None,
        "later_max_adverse_move": None,
        "later_return_5m": None,
        "later_return_10m": None,
        "later_return_20m": None,
        "later_estimated_option_return": None,
        "simple_state": (setup.get("simple_mode") or {}).get("state"),
    }
    if signal is not None:
        meta = getattr(signal, "metadata", {}) or {}
        intent = meta.get("execution_intent") or {}
        if isinstance(intent, dict):
            row["option_contract"] = intent.get("contract_symbol") or intent.get("option_symbol")
    return row


class TrendlinePriorityOptimizerDataset:
    def __init__(self, base_dir: Optional[Path] = None) -> None:
        root = Path(__file__).resolve().parents[1]
        self._local_dir = base_dir or (root / "priority_optimizer" / "trendline_priority_optimizer_dataset")
        self._local_dir.mkdir(parents=True, exist_ok=True)
        self._seen_keys: set[str] = set()

    def record_event(self, row: Dict[str, Any]) -> None:
        if not _snapshots_enabled():
            return
        sym = str(row.get("symbol") or "")
        decision = str(row.get("decision") or "")
        ts = str(row.get("timestamp") or "")
        trade_id_key = str(row.get("trade_id") or "")
        dedupe_key = f"{sym}|{decision}|{trade_id_key}|{ts}"
        if dedupe_key in self._seen_keys:
            return
        self._seen_keys.add(dedupe_key)

        date_key = _date_key_pt()
        path = self._local_dir / f"{date_key}_events.jsonl"
        line = json.dumps(row, default=str) + "\n"
        if not _skip_local_write():
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line)
        log.warning(
            "TRENDLINE_PRIORITY_DATASET_CAPTURED | symbol=%s | decision=%s | score=%s | path=%s",
            sym,
            decision,
            row.get("score"),
            path.name,
        )
        self._sync_jsonl_to_gcs(date_key, line)
        self._maybe_register_outcome_tracker(row)

    def _maybe_register_outcome_tracker(self, row: Dict[str, Any]) -> None:
        decision = str(row.get("decision") or "")
        if decision not in ("break_detected", "break_promoted"):
            return
        try:
            from .trendline_break_outcome_tracker import get_trendline_break_outcome_tracker
            from datetime import datetime as _dt

            ts_raw = row.get("timestamp")
            break_ts = _dt.fromisoformat(str(ts_raw).replace("Z", "+00:00")) if ts_raw else _dt.utcnow()
            get_trendline_break_outcome_tracker().register_break(
                trade_id=str(row.get("trade_id") or ""),
                symbol=str(row.get("symbol") or ""),
                break_ts=break_ts,
                break_close=float(row.get("close_price") or 0.0),
                line_price=float(row.get("line_price") or 0.0),
                direction=str(
                    row.get("line_geometry_direction")
                    or row.get("line_geometry")
                    or row.get("direction")
                    or ""
                ),
                expected_option_side=str(row.get("expected_option_side") or ""),
            )
        except Exception as exc:
            log.debug("TRENDLINE_OUTCOME_TRACKER | register_skip | reason=%s", exc)

    def _sync_jsonl_to_gcs(self, date_key: str, new_line: str) -> None:
        """Upload full session JSONL to GCS (works on Cloud Run when local write is skipped)."""
        fname = f"{date_key}_events.jsonl"
        gcs_path = f"{GCS_PREFIX}{fname}"
        local_path = self._local_dir / fname
        try:
            if not _skip_local_write() and local_path.exists():
                content = local_path.read_text(encoding="utf-8")
            else:
                from modules.gcs_persistence import get_gcs_persistence

                gcs = get_gcs_persistence()
                existing = gcs.read_string(gcs_path) if gcs.enabled else ""
                content = (existing or "") + new_line
            from modules.gcs_persistence import get_gcs_persistence

            gcs = get_gcs_persistence()
            if gcs.enabled:
                gcs.upload_string(gcs_path, content)
        except Exception as exc:
            log.debug("TRENDLINE_PRIORITY_DATASET | gcs_upload_skipped | reason=%s", exc)


_collector: Optional[TrendlinePriorityOptimizerDataset] = None


def get_trendline_priority_optimizer_dataset() -> TrendlinePriorityOptimizerDataset:
    global _collector
    if _collector is None:
        _collector = TrendlinePriorityOptimizerDataset()
    return _collector
