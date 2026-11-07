#!/usr/bin/env python3
"""
Trendline 0DTE measurement & attribution (Phase Next — no strategy logic changes).

Persists emit/execution cohorts, funnel counters, score buckets, opening-bar splits,
capital competition, and unified selector diagnostics. Exports daily JSON artifacts.
"""

from __future__ import annotations

import json
import os
import logging
import statistics
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, time as dt_time, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

PT = ZoneInfo("America/Los_Angeles")

SCORE_BUCKETS = (
    ("80+", 80.0, 999.0),
    ("75+", 75.0, 79.999),
    ("70+", 70.0, 74.999),
    ("65+", 65.0, 69.999),
    ("55+", 55.0, 64.999),
)

MOONSHOT_THRESHOLDS = (
    ("moonshot_500", 5.0),
    ("moonshot_300", 3.0),
    ("moonshot_200", 2.0),
    ("moonshot_100", 1.0),
)

FUNNEL_KEYS = (
    "breaks",
    "confirmed",
    "structure_accepted",
    "ready_to_execute",
    "signal_emit",
    "contract_selected",
    "broker_submitted",
    "durable_executed",
    "closed",
)


def trendline_measurement_enabled() -> bool:
    try:
        from modules.config_loader import get_config_value
    except Exception:
        return True
    master = str(get_config_value("ENABLE_EXECUTION_FEATURE_SNAPSHOT", "true")).strip().lower()
    if master not in ("1", "true", "yes", "on"):
        return False
    raw = str(get_config_value("ENABLE_TRENDLINE_MEASUREMENT", "true")).strip().lower()
    return raw in ("1", "true", "yes", "on")


def _session_date_pt(now: Optional[datetime] = None) -> str:
    ts = now or datetime.now(PT)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=PT)
    return ts.astimezone(PT).strftime("%Y-%m-%d")


def _parse_ts(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        s = str(value).strip().replace(" ", "T")
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def _opening_bar_emit_pt(emit_ts: Optional[datetime], session_date: str) -> bool:
    """True when emit is on the first 7:30 PT opening candle (≤90s after 7:30)."""
    if emit_ts is None:
        return False
    emit_pt = emit_ts.astimezone(PT)
    try:
        y, m, d = (int(x) for x in session_date.split("-"))
        open_pt = datetime(y, m, d, 7, 30, tzinfo=PT)
    except (TypeError, ValueError):
        return False
    delta = (emit_pt - open_pt).total_seconds()
    return 0.0 <= delta <= 90.0


def classify_outcome(
    *,
    peak_option_return_pct: Optional[float],
    close_option_return_pct: Optional[float],
    executed: bool,
) -> str:
    peak = peak_option_return_pct
    if peak is None and close_option_return_pct is not None:
        peak = close_option_return_pct
    if peak is None:
        return "unknown"
    for label, thr in MOONSHOT_THRESHOLDS:
        if peak >= thr:
            return label
    close = close_option_return_pct if close_option_return_pct is not None else peak
    if close > 0.02:
        return "winner"
    if close < -0.02:
        return "loser"
    return "flat"


def _score_bucket(score: float) -> str:
    for label, lo, hi in SCORE_BUCKETS:
        if lo <= score <= hi:
            return label
    if score >= 55.0:
        return "55+"
    return "below_55"


@dataclass
class TrendlineEmitRecord:
    session_date: str
    symbol: str
    trade_id: str
    side: str
    setup_type: str = ""
    score: float = 0.0
    quality_score: float = 0.0
    orb_zone: str = ""
    break_distance_pct: float = 0.0
    selected_delta: float = 0.0
    selected_delta_est: float = 0.0
    strike: Optional[float] = None
    premium_at_entry: float = 0.0
    underlying_at_entry: float = 0.0
    emit_timestamp: str = ""
    volume_ratio: float = 0.0
    opening_bar_emit: bool = False
    executed: bool = False
    execution_reject_reason: str = ""
    peak_underlying_move_pct: float = 0.0
    peak_option_return_pct: Optional[float] = None
    close_option_return_pct: Optional[float] = None
    mfe_pct: Optional[float] = None
    mae_pct: Optional[float] = None
    outcome_class: str = "unknown"
    capital_audit: Dict[str, Any] = field(default_factory=dict)
    selector_audit: Dict[str, Any] = field(default_factory=dict)
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_emit_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_executed_dict(self) -> Optional[Dict[str, Any]]:
        if not self.executed:
            return None
        row = self.to_emit_dict()
        row["status"] = "executed"
        return row


@dataclass
class TrendlineMeasurementDay:
    session_date: str
    funnel: Dict[str, int] = field(default_factory=lambda: {k: 0 for k in FUNNEL_KEYS})
    emits: Dict[str, TrendlineEmitRecord] = field(default_factory=dict)
    _funnel_logged: bool = False

    def inc_funnel(self, key: str, delta: int = 1) -> None:
        if key not in self.funnel:
            self.funnel[key] = 0
        self.funnel[key] = int(self.funnel.get(key, 0)) + int(delta)

    def register_emit(self, record: TrendlineEmitRecord) -> None:
        tid = str(record.trade_id or "").strip()
        if not tid:
            return
        self.emits[tid] = record
        self.inc_funnel("signal_emit")
        log.info(
            "TRENDLINE_SCORE_AUDIT | session_date=%s | symbol=%s | trade_id=%s | score=%.2f | "
            "quality=%.2f | volume_ratio=%.4f | orb_zone=%s | setup_type=%s | side=%s | "
            "selected_delta=%.4f | selected_delta_est=%.4f | selected_premium=%.4f | opening_bar_emit=%s",
            record.session_date,
            record.symbol,
            record.trade_id,
            float(record.score),
            float(record.quality_score),
            float(record.volume_ratio),
            record.orb_zone,
            record.setup_type,
            record.side,
            float(record.selected_delta),
            float(record.selected_delta_est),
            float(record.premium_at_entry),
            record.opening_bar_emit,
        )

    def update_execution(
        self,
        trade_id: str,
        *,
        executed: bool,
        reject_reason: str = "",
        broker_submitted: bool = False,
        durable_executed: bool = False,
    ) -> None:
        rec = self.emits.get(str(trade_id or "").strip())
        if not rec:
            return
        rec.executed = bool(executed)
        rec.execution_reject_reason = str(reject_reason or "")[:200]
        if broker_submitted:
            self.inc_funnel("broker_submitted")
        if durable_executed:
            self.inc_funnel("durable_executed")
            rec.outcome_class = classify_outcome(
                peak_option_return_pct=rec.peak_option_return_pct,
                close_option_return_pct=rec.close_option_return_pct,
                executed=True,
            )

    def note_closed(self, trade_id: str, *, close_option_return_pct: Optional[float]) -> None:
        rec = self.emits.get(str(trade_id or "").strip())
        if not rec:
            return
        self.inc_funnel("closed")
        if close_option_return_pct is not None:
            rec.close_option_return_pct = float(close_option_return_pct)
        rec.outcome_class = classify_outcome(
            peak_option_return_pct=rec.peak_option_return_pct,
            close_option_return_pct=rec.close_option_return_pct,
            executed=rec.executed,
        )

    def update_peaks(
        self,
        trade_id: str,
        *,
        underlying_price: Optional[float] = None,
        option_mark: Optional[float] = None,
    ) -> None:
        rec = self.emits.get(str(trade_id or "").strip())
        if not rec or rec.underlying_at_entry <= 0:
            return
        base_u = float(rec.underlying_at_entry)
        if underlying_price is not None and underlying_price > 0:
            move = abs(float(underlying_price) - base_u) / base_u
            rec.peak_underlying_move_pct = max(rec.peak_underlying_move_pct, move)
        entry_prem = float(rec.premium_at_entry or 0.0)
        if option_mark is not None and entry_prem > 0:
            ret = (float(option_mark) - entry_prem) / entry_prem
            prev_peak = rec.peak_option_return_pct
            rec.peak_option_return_pct = ret if prev_peak is None else max(prev_peak, ret)
            rec.mfe_pct = rec.peak_option_return_pct
            if rec.mae_pct is None:
                rec.mae_pct = ret
            else:
                rec.mae_pct = min(float(rec.mae_pct), ret)

    def log_funnel_audit(self, *, force: bool = False) -> None:
        if self._funnel_logged and not force:
            return
        self._funnel_logged = True
        parts = " | ".join(f"{k}={int(self.funnel.get(k, 0))}" for k in FUNNEL_KEYS)
        log.warning(
            "TRENDLINE_FUNNEL_AUDIT | session_date=%s | %s | emits=%d | executed=%d",
            self.session_date,
            parts,
            len(self.emits),
            sum(1 for e in self.emits.values() if e.executed),
        )

    def build_opening_bar_analysis(self) -> Dict[str, Any]:
        opening = [e for e in self.emits.values() if e.opening_bar_emit]
        later = [e for e in self.emits.values() if not e.opening_bar_emit]

        def _stats(rows: List[TrendlineEmitRecord]) -> Dict[str, Any]:
            peaks = [r.peak_option_return_pct for r in rows if r.peak_option_return_pct is not None]
            wins = [r for r in rows if r.outcome_class in ("winner",) + tuple(m[0] for m in MOONSHOT_THRESHOLDS)]
            return {
                "count": len(rows),
                "win_rate": (len(wins) / len(rows)) if rows else None,
                "avg_peak_return": (sum(peaks) / len(peaks)) if peaks else None,
                "median_peak_return": statistics.median(peaks) if peaks else None,
            }

        return {
            "schema_version": 1,
            "session_date": self.session_date,
            "stage": "OPENING_BAR_ANALYSIS",
            "opening_bar_count": len(opening),
            "later_bar_count": len(later),
            "opening_bar_win_rate": _stats(opening).get("win_rate"),
            "later_bar_win_rate": _stats(later).get("win_rate"),
            "opening_bar_avg_peak": _stats(opening).get("avg_peak_return"),
            "later_bar_avg_peak": _stats(later).get("avg_peak_return"),
            "opening_bar_median_peak": _stats(opening).get("median_peak_return"),
            "later_bar_median_peak": _stats(later).get("median_peak_return"),
            "opening_bar": _stats(opening),
            "later_bar": _stats(later),
        }

    def build_score_summary(self) -> Dict[str, Any]:
        buckets: Dict[str, Dict[str, Any]] = {}
        for label, _, _ in SCORE_BUCKETS:
            buckets[label] = {
                "count": 0,
                "win_rate": None,
                "avg_peak_return": None,
                "median_peak_return": None,
                "moonshot_rate": None,
                "_peaks": [],
                "_wins": 0,
                "_moonshots": 0,
            }
        for rec in self.emits.values():
            b = _score_bucket(float(rec.score))
            if b not in buckets:
                buckets[b] = {
                    "count": 0,
                    "win_rate": None,
                    "avg_peak_return": None,
                    "median_peak_return": None,
                    "moonshot_rate": None,
                    "_peaks": [],
                    "_wins": 0,
                    "_moonshots": 0,
                }
            buckets[b]["count"] = int(buckets[b]["count"]) + 1
            if rec.peak_option_return_pct is not None:
                buckets[b]["_peaks"].append(float(rec.peak_option_return_pct))
            if rec.outcome_class == "winner" or rec.outcome_class.startswith("moonshot"):
                buckets[b]["_wins"] = int(buckets[b]["_wins"]) + 1
            if rec.outcome_class.startswith("moonshot"):
                buckets[b]["_moonshots"] = int(buckets[b]["_moonshots"]) + 1
        for label, agg in buckets.items():
            peaks = agg.pop("_peaks", [])
            wins = int(agg.pop("_wins", 0))
            moon = int(agg.pop("_moonshots", 0))
            cnt = int(agg.get("count", 0))
            agg["win_rate"] = (wins / cnt) if cnt else None
            agg["moonshot_rate"] = (moon / cnt) if cnt else None
            agg["avg_peak_return"] = (sum(peaks) / len(peaks)) if peaks else None
            agg["median_peak_return"] = statistics.median(peaks) if peaks else None
        return {
            "schema_version": 1,
            "session_date": self.session_date,
            "stage": "trendline_score_summary",
            "buckets": buckets,
        }

    def build_funnel_summary(self) -> Dict[str, Any]:
        return {
            "schema_version": 1,
            "session_date": self.session_date,
            "stage": "trendline_funnel_summary",
            "funnel": dict(self.funnel),
            "emit_count": len(self.emits),
            "executed_count": sum(1 for e in self.emits.values() if e.executed),
        }


_day: Optional[TrendlineMeasurementDay] = None


def get_trendline_measurement_day(session_date: Optional[str] = None) -> TrendlineMeasurementDay:
    global _day
    date_key = session_date or _session_date_pt()
    if _day is None or _day.session_date != date_key:
        _day = TrendlineMeasurementDay(session_date=date_key)
    return _day


def reset_trendline_measurement_singleton() -> None:
    global _day
    _day = None


def _export_root(override: Optional[Path] = None) -> Path:
    if override is not None:
        root = Path(override)
    elif os.getenv("TRENDLINE_MEASUREMENT_EXPORT_DIR", "").strip():
        root = Path(os.environ["TRENDLINE_MEASUREMENT_EXPORT_DIR"].strip())
    else:
        root = Path(__file__).resolve().parent.parent / "priority_optimizer" / "trendline_measurement"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _snapshot_copy_dir(date_key: str, override: Optional[Path] = None) -> Optional[Path]:
    """Where the per-session copy goes, or None to skip it.

    The default target is ``priority_optimizer/retrieved_data/execution_snapshots/<date>/``,
    which on a workstation is the durable research archive. It is only used inside
    a Cloud Run container; locally (and always under pytest) the copy is skipped
    unless an explicit non-archive directory is given.
    """
    from .durable_archive_guard import guard_archive_mutation

    if override is not None:
        base = Path(override)
    elif os.getenv("TRENDLINE_MEASUREMENT_SNAPSHOT_DIR", "").strip():
        base = Path(os.environ["TRENDLINE_MEASUREMENT_SNAPSHOT_DIR"].strip())
    else:
        base = (
            Path(__file__).resolve().parent.parent
            / "priority_optimizer"
            / "retrieved_data"
            / "execution_snapshots"
        )
    snap_dir = base / date_key
    if not guard_archive_mutation(snap_dir, action="trendline_daily_export"):
        return None
    return snap_dir


def export_daily_artifacts(
    day: TrendlineMeasurementDay,
    *,
    export_root: Optional[Path] = None,
    snapshot_root: Optional[Path] = None,
) -> Dict[str, str]:
    """Write four JSON deliverables; return paths written.

    ``export_root`` / ``snapshot_root`` (or env ``TRENDLINE_MEASUREMENT_EXPORT_DIR`` /
    ``TRENDLINE_MEASUREMENT_SNAPSHOT_DIR``) redirect output. The snapshot copy never
    lands in the durable ``retrieved_data`` archive outside a Cloud Run container.
    """
    root = _export_root(export_root)
    date_key = day.session_date
    paths: Dict[str, str] = {}

    emit_rows = [e.to_emit_dict() for e in day.emits.values()]
    executed_rows = [r for r in (e.to_executed_dict() for e in day.emits.values()) if r]

    files = {
        f"trendline_emit_outcomes_{date_key}.json": {
            "schema_version": 1,
            "session_date": date_key,
            "count": len(emit_rows),
            "emits": emit_rows,
        },
        f"trendline_executed_outcomes_{date_key}.json": {
            "schema_version": 1,
            "session_date": date_key,
            "count": len(executed_rows),
            "executed": executed_rows,
        },
        f"trendline_funnel_summary_{date_key}.json": day.build_funnel_summary(),
        f"trendline_score_summary_{date_key}.json": day.build_score_summary(),
    }
    opening = day.build_opening_bar_analysis()
    files[f"trendline_opening_bar_analysis_{date_key}.json"] = opening

    for fname, payload in files.items():
        path = root / fname
        path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        paths[fname] = str(path)

    snap_dir = _snapshot_copy_dir(date_key, snapshot_root)
    if snap_dir is not None:
        snap_dir.mkdir(parents=True, exist_ok=True)
        for fname, payload in files.items():
            dest = snap_dir / fname
            dest.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
            paths[f"snapshot/{fname}"] = str(dest)

    log.warning(
        "TRENDLINE_MEASUREMENT | action=daily_export | session_date=%s | emit=%d | executed=%d | files=%d",
        date_key,
        len(emit_rows),
        len(executed_rows),
        len(files),
    )
    return paths


def log_unified_selector_audit(
    *,
    symbol: str,
    path: str,
    selected_delta: float,
    selected_delta_est: float,
    premium: float,
    expected_move_pct: Optional[float] = None,
    viability_score: Optional[float] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    if not trendline_measurement_enabled():
        return
    log.info(
        "UNIFIED_SELECTOR_AUDIT | path=%s | symbol=%s | selected_delta=%.4f | selected_delta_est=%.4f | "
        "premium=%.4f | expected_move_pct=%s | viability_score=%s",
        path,
        str(symbol).upper(),
        float(selected_delta),
        float(selected_delta_est),
        float(premium),
        f"{float(expected_move_pct):.6f}" if expected_move_pct is not None else "n/a",
        f"{float(viability_score):.4f}" if viability_score is not None else "n/a",
    )
    day = get_trendline_measurement_day()
    if extra and isinstance(extra, dict):
        _ = extra


def record_contract_selection(
    trade_id: str,
    *,
    pick: Any,
    reject_reason: Optional[str] = None,
    selector_extra: Optional[Dict[str, Any]] = None,
) -> None:
    if not trendline_measurement_enabled():
        return
    day = get_trendline_measurement_day()
    rec = day.emits.get(str(trade_id))
    if not rec:
        return
    if reject_reason:
        rec.execution_reject_reason = str(reject_reason)
        return
    day.inc_funnel("contract_selected")
    try:
        from easy0DTE.modules.dte0_entry_selection import abs_contract_delta
    except ImportError:
        abs_contract_delta = lambda c: abs(float(getattr(c, "delta_est", 0) or getattr(c, "delta", 0) or 0))  # noqa: E731

    dest = abs_contract_delta(pick)
    rec.selected_delta = float(dest)
    rec.selected_delta_est = float(getattr(pick, "delta_est", dest) or dest)
    rec.strike = float(getattr(pick, "strike", 0) or 0) or None
    rec.premium_at_entry = float(getattr(pick, "premium", 0) or 0)
    if selector_extra:
        rec.selector_audit = dict(selector_extra)
    log_unified_selector_audit(
        symbol=rec.symbol,
        path="TRENDLINE_0DTE",
        selected_delta=rec.selected_delta,
        selected_delta_est=rec.selected_delta_est,
        premium=rec.premium_at_entry,
        expected_move_pct=selector_extra.get("expected_move_pct") if selector_extra else None,
        viability_score=selector_extra.get("viability_score") if selector_extra else None,
    )


def _log_shadow_review_emit(rec: TrendlineEmitRecord) -> None:
    peak = rec.peak_option_return_pct
    would_win = peak is not None and peak > 0.05
    left = None
    if peak is not None and rec.close_option_return_pct is not None:
        left = max(0.0, float(peak) - float(rec.close_option_return_pct))
    log.warning(
        "TRENDLINE_SHADOW_REVIEW | session_date=%s | trade_id=%s | symbol=%s | executed=false | "
        "reject_reason=%s | peak_option_return_pct=%s | would_have_been_winner=%s | left_on_table_pct=%s | "
        "opening_bar_emit=%s | score=%.2f",
        rec.session_date,
        rec.trade_id,
        rec.symbol,
        rec.execution_reject_reason or "n/a",
        f"{peak:.4f}" if peak is not None else "n/a",
        str(bool(would_win)).lower(),
        f"{left:.4f}" if left is not None else "n/a",
        str(rec.opening_bar_emit).lower(),
        float(rec.score),
    )
