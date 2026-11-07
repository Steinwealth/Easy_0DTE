#!/usr/bin/env python3
"""Early/late sampling throttle for profit_optimizer only (not monitor loop)."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from profit_optimizer.audit import parse_timestamp_utc


def _parse_ts(ts: str) -> Optional[datetime]:
    return parse_timestamp_utc(ts)


def align_recorder_to_monitor_interval(system: object, interval_sec: float) -> None:
    """Keep live and shadow sample clocks on the monitor interval for this book.

    Below 20 opens that is 2s. At 20 or more it is the wide interval (3s).
    Each evaluated monitor tick still forces one path row, so the sample and
    the exit check share that tick.
    """
    po = getattr(system, "profit_optimizer", None) or getattr(system, "_profit_optimizer", None)
    rec = getattr(po, "recorder", None) if po is not None else None
    if rec is None:
        return
    try:
        iv = max(0.0, float(interval_sec))
    except (TypeError, ValueError):
        return
    rec._early_sample_sec = iv
    rec._late_sample_sec = iv
    rec._monitor_aligned_sample_sec = iv
    policies = getattr(rec, "_policies", None)
    if not isinstance(policies, dict):
        return
    for policy in policies.values():
        try:
            policy.early_interval_sec = iv
            policy.late_interval_sec = iv
        except Exception:
            continue


def load_sampling_config() -> tuple[float, float, float]:
    """Early/late sample intervals follow open-position monitor cadence.

    Sole cadence source: ``0DTE_POSITION_MONITOR_INTERVAL_SEC`` (same knob as
    unified ``maybe_tick``). The monitor overwrites both intervals with
    ``effective_position_monitor_interval_sec`` once the book size is known.
    Early-window length stays a PO-only setting.
    """
    try:
        from modules.config_loader import get_config_value
        from modules.dte0_exit_monitor_config import resolve_0dte_position_monitor_interval_sec

        interval = float(resolve_0dte_position_monitor_interval_sec())
        window = float(get_config_value("PROFIT_OPTIMIZER_EARLY_WINDOW_SECONDS", "1800") or 1800)
        return max(0.0, interval), max(0.0, window), max(0.0, interval)
    except Exception:
        return 2.0, 1800.0, 2.0


class SamplingPolicy:
    """Throttle profit_optimizer writes based on seconds since entry."""

    def __init__(
        self,
        *,
        entry_time_iso: str,
        early_interval_sec: float = 2.0,
        early_window_sec: float = 1800.0,
        late_interval_sec: float = 2.0,
    ) -> None:
        from profit_optimizer.time_utils import normalize_timestamp_iso

        self._entry_time_iso = normalize_timestamp_iso(entry_time_iso)
        self._entry_dt = _parse_ts(self._entry_time_iso)
        self.early_interval_sec = max(0.0, float(early_interval_sec))
        self.early_window_sec = max(0.0, float(early_window_sec))
        self.late_interval_sec = max(0.0, float(late_interval_sec))

    def effective_interval_sec(self, now_mono: float, entry_mono: float) -> float:
        elapsed = 0.0
        if entry_mono > 0:
            elapsed = max(0.0, now_mono - entry_mono)
        elif self._entry_dt is not None:
            elapsed = max(
                0.0,
                (datetime.now(timezone.utc) - self._entry_dt).total_seconds(),
            )
        if elapsed <= self.early_window_sec:
            return self.early_interval_sec
        return self.late_interval_sec

    def should_sample(self, last_sample_mono: float, now_mono: float, entry_mono: float) -> bool:
        interval = self.effective_interval_sec(now_mono, entry_mono)
        if interval <= 0:
            return True
        if last_sample_mono <= 0:
            return True
        return (now_mono - last_sample_mono) >= interval
