#!/usr/bin/env python3
"""
Research-only post-break outcome tracker for Trendline Priority Optimizer.

Writes: {ns}/priority_optimizer/trendline_outcomes/YYYY-MM-DD_outcomes.jsonl
Controlled by ENABLE_TRENDLINE_OUTCOME_TRACKER (default false).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

GCS_PREFIX = "priority_optimizer/trendline_outcomes/"
HORIZONS_MIN = (5, 15, 30, 60)


def _outcome_tracker_enabled() -> bool:
    try:
        from .config_loader import get_config_value
    except Exception:
        return False
    master = str(get_config_value("ENABLE_EXECUTION_FEATURE_SNAPSHOT", "true")).strip().lower()
    if master not in ("1", "true", "yes"):
        return False
    flag = str(get_config_value("ENABLE_TRENDLINE_OUTCOME_TRACKER", "false")).strip().lower()
    return flag in ("1", "true", "yes")


def _skip_local_write() -> bool:
    explicit = os.getenv("EXECUTION_SNAPSHOT_LOCAL", "").strip().lower()
    if explicit in ("1", "true", "yes"):
        return False
    if explicit in ("0", "false", "no"):
        return True
    return bool(os.getenv("K_SERVICE"))


def _date_key_pt(now: Optional[datetime] = None) -> str:
    from zoneinfo import ZoneInfo

    ts = now or datetime.now(ZoneInfo("America/Los_Angeles"))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=ZoneInfo("America/Los_Angeles"))
    return ts.astimezone(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass
class _PendingBreakOutcome:
    trade_id: str
    symbol: str
    break_ts: datetime
    break_close: float
    line_price: float
    direction: str
    expected_option_side: str
    registered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    max_favorable_move: float = 0.0
    max_adverse_move: float = 0.0
    max_break_distance_pct: float = 0.0
    reclaim_distance_pct: float = 0.0
    horizon_returns: Dict[int, Optional[float]] = field(default_factory=dict)
    horizon_mfe: Dict[int, float] = field(default_factory=dict)
    horizon_mae: Dict[int, float] = field(default_factory=dict)
    last_close: float = 0.0
    finalized: bool = False

    def __post_init__(self) -> None:
        self.last_close = float(self.break_close)
        for m in HORIZONS_MIN:
            self.horizon_returns.setdefault(m, None)
            self.horizon_mfe.setdefault(m, 0.0)
            self.horizon_mae.setdefault(m, 0.0)


class TrendlineBreakOutcomeTracker:
    """Sidecar tracker — ticked from Trendline watch bars; no trading impact."""

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        root = Path(__file__).resolve().parent.parent
        self._local_dir = base_dir or (root / "priority_optimizer" / "trendline_outcomes")
        self._local_dir.mkdir(parents=True, exist_ok=True)
        self._pending: Dict[str, _PendingBreakOutcome] = {}
        self._written_ids: set[str] = set()

    def register_break(
        self,
        *,
        trade_id: str,
        symbol: str,
        break_ts: datetime,
        break_close: float,
        line_price: float,
        direction: str,
        expected_option_side: str,
    ) -> None:
        if not _outcome_tracker_enabled():
            return
        tid = str(trade_id or "").strip()
        sym = str(symbol or "").strip().upper()
        if not tid or not sym:
            return
        if tid in self._pending or tid in self._written_ids:
            return
        self._pending[tid] = _PendingBreakOutcome(
            trade_id=tid,
            symbol=sym,
            break_ts=_to_utc(break_ts),
            break_close=float(break_close),
            line_price=float(line_price),
            direction=str(direction or "").lower(),
            expected_option_side=str(expected_option_side or "").lower(),
        )

    def on_bar(self, symbol: str, bar: Any, *, now: Optional[datetime] = None) -> None:
        if not _outcome_tracker_enabled() or not self._pending:
            return
        sym = str(symbol or "").strip().upper()
        try:
            close = float(getattr(bar, "close", 0.0) or 0.0)
            bar_ts = getattr(bar, "ts", None)
        except (TypeError, ValueError):
            return
        if close <= 0 or bar_ts is None:
            return
        bar_dt = _to_utc(bar_ts if isinstance(bar_ts, datetime) else datetime.now(timezone.utc))
        clock = _to_utc(now or datetime.now(timezone.utc))

        done: List[str] = []
        for tid, p in list(self._pending.items()):
            if p.symbol != sym:
                continue
            self._update_pending(p, close=close, line_at_bar=self._line_distance_pct(p, close), bar_dt=bar_dt, clock=clock)
            if p.finalized:
                self._flush_outcome(p)
                done.append(tid)
        for tid in done:
            self._pending.pop(tid, None)

    @staticmethod
    def _line_distance_pct(p: _PendingBreakOutcome, close: float) -> float:
        line = max(abs(p.line_price), 1e-9)
        if p.direction == "bear":
            return max(0.0, (close - p.line_price) / line)
        if p.direction == "bull":
            return max(0.0, (p.line_price - close) / line)
        return 0.0

    def _favorable_move_pct(self, p: _PendingBreakOutcome, close: float) -> float:
        base = max(abs(p.break_close), 1e-9)
        if p.direction == "bear":
            return max(0.0, (close - p.break_close) / base)
        if p.direction == "bull":
            return max(0.0, (p.break_close - close) / base)
        return 0.0

    def _adverse_move_pct(self, p: _PendingBreakOutcome, close: float) -> float:
        base = max(abs(p.break_close), 1e-9)
        if p.direction == "bear":
            return max(0.0, (p.break_close - close) / base)
        if p.direction == "bull":
            return max(0.0, (close - p.break_close) / base)
        return 0.0

    def _reclaim_pct(self, p: _PendingBreakOutcome, close: float) -> float:
        line = p.line_price
        if p.direction == "bear" and close <= line:
            return max(0.0, (line - close) / max(abs(line), 1e-9))
        if p.direction == "bull" and close >= line:
            return max(0.0, (close - line) / max(abs(line), 1e-9))
        return 0.0

    def _update_pending(
        self,
        p: _PendingBreakOutcome,
        *,
        close: float,
        line_at_bar: float,
        bar_dt: datetime,
        clock: datetime,
    ) -> None:
        p.last_close = close
        fav = self._favorable_move_pct(p, close)
        adv = self._adverse_move_pct(p, close)
        p.max_favorable_move = max(p.max_favorable_move, fav)
        p.max_adverse_move = max(p.max_adverse_move, adv)
        p.max_break_distance_pct = max(p.max_break_distance_pct, line_at_bar)
        p.reclaim_distance_pct = max(p.reclaim_distance_pct, self._reclaim_pct(p, close))

        elapsed_min = (bar_dt - p.break_ts).total_seconds() / 60.0
        for m in HORIZONS_MIN:
            if elapsed_min >= float(m) and p.horizon_returns[m] is None:
                base = max(abs(p.break_close), 1e-9)
                p.horizon_returns[m] = (close - p.break_close) / base
            if elapsed_min <= float(m):
                p.horizon_mfe[m] = max(p.horizon_mfe[m], fav)
                p.horizon_mae[m] = max(p.horizon_mae[m], adv)

        if elapsed_min >= float(HORIZONS_MIN[-1]) or (clock - p.break_ts) >= timedelta(minutes=HORIZONS_MIN[-1] + 2):
            p.finalized = True

    def _build_row(self, p: _PendingBreakOutcome) -> Dict[str, Any]:
        base = max(abs(p.break_close), 1e-9)
        ret_60 = p.horizon_returns.get(60)
        if ret_60 is None and p.last_close:
            ret_60 = (p.last_close - p.break_close) / base
        if ret_60 is None:
            final_dir = "unknown"
        elif ret_60 > 0.0001:
            final_dir = "favorable"
        elif ret_60 < -0.0001:
            final_dir = "adverse"
        else:
            final_dir = "flat"

        return {
            "trade_id": p.trade_id,
            "symbol": p.symbol,
            "timestamp": _to_utc(p.break_ts).isoformat(),
            "return_5m": p.horizon_returns.get(5),
            "return_15m": p.horizon_returns.get(15),
            "return_30m": p.horizon_returns.get(30),
            "return_60m": ret_60,
            "mfe_5m": p.horizon_mfe.get(5, 0.0),
            "mfe_15m": p.horizon_mfe.get(15, 0.0),
            "mfe_30m": p.horizon_mfe.get(30, 0.0),
            "mfe_60m": p.max_favorable_move,
            "mae_5m": p.horizon_mae.get(5, 0.0),
            "mae_15m": p.horizon_mae.get(15, 0.0),
            "mae_30m": p.horizon_mae.get(30, 0.0),
            "mae_60m": p.max_adverse_move,
            "max_favorable_move": p.max_favorable_move,
            "max_adverse_move": p.max_adverse_move,
            "max_break_distance_pct": p.max_break_distance_pct,
            "reclaim_distance_pct": p.reclaim_distance_pct,
            "final_direction": final_dir,
        }

    def _flush_outcome(self, p: _PendingBreakOutcome) -> None:
        if p.trade_id in self._written_ids:
            return
        row = self._build_row(p)
        date_key = _date_key_pt(p.break_ts)
        line = json.dumps(row, default=str) + "\n"
        fname = f"{date_key}_outcomes.jsonl"
        local_path = self._local_dir / fname
        if not _skip_local_write():
            with local_path.open("a", encoding="utf-8") as fh:
                fh.write(line)
        try:
            from .gcs_persistence import get_gcs_persistence

            gcs = get_gcs_persistence()
            try:
                from .runtime_mode import gcs_trendline_outcomes_object

                gpath = gcs_trendline_outcomes_object(fname)
            except Exception:
                gpath = f"{GCS_PREFIX}{fname}"
            if gcs.enabled:
                existing = gcs.read_string(gpath) or ""
                gcs.upload_string(gpath, existing + line)
        except Exception as exc:
            log.debug("TRENDLINE_OUTCOME_TRACKER | gcs_skip | reason=%s", exc)
        self._written_ids.add(p.trade_id)
        log.info(
            "TRENDLINE_OUTCOME_TRACKER | trade_id=%s | symbol=%s | final_direction=%s",
            p.trade_id,
            p.symbol,
            row.get("final_direction"),
        )


_tracker: Optional[TrendlineBreakOutcomeTracker] = None


def get_trendline_break_outcome_tracker() -> TrendlineBreakOutcomeTracker:
    global _tracker
    if _tracker is None:
        _tracker = TrendlineBreakOutcomeTracker()
    return _tracker
