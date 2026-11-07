#!/usr/bin/env python3
"""Refine ORB2 high/low timestamps from structure bars without changing prices."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Optional, Tuple


def _f(val: Any) -> Optional[float]:
    try:
        out = float(val)
    except (TypeError, ValueError):
        return None
    return out if out > 0 else None


def _ts(val: Any) -> Optional[datetime]:
    raw = getattr(val, "ts", None) or (val.get("ts") if isinstance(val, dict) else None)
    if isinstance(raw, datetime):
        return raw
    try:
        s = str(raw or "").strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except Exception:
        return None


def _bar_val(bar: Any, key: str) -> Optional[float]:
    return _f(getattr(bar, key, None) if not isinstance(bar, dict) else bar.get(key))


def _in_window(ts: datetime, start: datetime, end: datetime) -> bool:
    t = ts
    s = start
    e = end
    try:
        if t.tzinfo is None and s.tzinfo is not None:
            t = t.replace(tzinfo=s.tzinfo)
        if s.tzinfo is None and t.tzinfo is not None:
            s = s.replace(tzinfo=t.tzinfo)
        if e.tzinfo is None and t.tzinfo is not None:
            e = e.replace(tzinfo=t.tzinfo)
        if t.tzinfo is not None:
            if s.tzinfo is not None:
                s = s.astimezone(t.tzinfo)
            if e.tzinfo is not None:
                e = e.astimezone(t.tzinfo)
    except Exception:
        pass
    return s <= t <= e


def _best_match(
    bars: Iterable[Any],
    *,
    field: str,
    target: float,
    start: datetime,
    end: datetime,
    tolerance: float,
) -> Optional[Tuple[datetime, float]]:
    best: Optional[Tuple[float, datetime, float]] = None
    for bar in bars or []:
        ts = _ts(bar)
        if ts is None or not _in_window(ts, start, end):
            continue
        px = _bar_val(bar, field)
        if px is None:
            continue
        diff = abs(float(px) - float(target))
        if diff > tolerance:
            continue
        if best is None or diff < best[0] or (diff == best[0] and ts < best[1]):
            best = (diff, ts, px)
    if best is None:
        return None
    return best[1], best[2]


def refine_orb2_extreme_timestamps_from_bars(
    orb_obj: Any,
    bars: Iterable[Any],
    *,
    window_start: datetime,
    window_end: datetime,
    abs_tolerance: float = 0.05,
    pct_tolerance: float = 0.0002,
) -> dict:
    """
    Stamp ORB2 high/low timestamps from 1m structure bars while keeping broker prices.

    E*TRADE session OHLC owns ORB2 high/low prices. Yahoo/broker 1m bars may only
    locate the timestamp of the matching high/low inside 06:30-07:30. If no bar is
    close enough to the broker level, existing timestamps remain unchanged.
    """
    if orb_obj is None:
        return {"high_refined": False, "low_refined": False}
    high = _f(getattr(orb_obj, "orb_high", None) if not isinstance(orb_obj, dict) else orb_obj.get("orb_high"))
    low = _f(getattr(orb_obj, "orb_low", None) if not isinstance(orb_obj, dict) else orb_obj.get("orb_low"))
    if high is None or low is None:
        return {"high_refined": False, "low_refined": False}

    rows = list(bars or [])
    out = {"high_refined": False, "low_refined": False}

    high_tol = max(float(abs_tolerance), abs(float(high)) * float(pct_tolerance))
    low_tol = max(float(abs_tolerance), abs(float(low)) * float(pct_tolerance))
    high_match = _best_match(
        rows, field="high", target=high, start=window_start, end=window_end, tolerance=high_tol
    )
    low_match = _best_match(
        rows, field="low", target=low, start=window_start, end=window_end, tolerance=low_tol
    )

    if high_match is not None:
        if isinstance(orb_obj, dict):
            orb_obj["orb_high_extreme_ts"] = high_match[0]
            orb_obj["orb_high_ts_source"] = "structure_1m_match"
        else:
            setattr(orb_obj, "orb_high_extreme_ts", high_match[0])
            setattr(orb_obj, "orb_high_ts_source", "structure_1m_match")
        out.update({"high_refined": True, "high_match_px": high_match[1], "high_ts": high_match[0]})
    if low_match is not None:
        if isinstance(orb_obj, dict):
            orb_obj["orb_low_extreme_ts"] = low_match[0]
            orb_obj["orb_low_ts_source"] = "structure_1m_match"
        else:
            setattr(orb_obj, "orb_low_extreme_ts", low_match[0])
            setattr(orb_obj, "orb_low_ts_source", "structure_1m_match")
        out.update({"low_refined": True, "low_match_px": low_match[1], "low_ts": low_match[0]})
    return out
