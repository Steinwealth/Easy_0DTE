#!/usr/bin/env python3
"""Shared ORB1 / ORB2 extension-confidence ranker (not a hard admit floor).

Predicts how far the underlying is likely to close beyond this trade's ORB
high (CALL) or low (PUT). Higher ATR is fuel — the old ATR≤0.15 knife
inverted that and killed the biggest Aug 17 held breaks.

Returns 0–100. Stamp / rank only. Do not hard-reject on this number.
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Tuple


def _f(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v:  # NaN
        return None
    return v


def _clip01(value: float) -> float:
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def break_pct_points(raw: Any) -> Optional[float]:
    """Normalize break distance to percent points (2.50 = 2.5%)."""
    v = _f(raw)
    if v is None:
        return None
    if abs(v) <= 0.15:
        return v * 100.0
    return v


def compute_breakout_extension_score(
    *,
    atr: Any = None,
    atr_5min: Any = None,
    price: Any = None,
    volume_ratio: Any = None,
    direction_volume_pct: Any = None,
    break_distance_pct: Any = None,
    min_break_pct: float = 0.08,
    max_break_pct: float = 2.50,
) -> Tuple[float, Dict[str, Any]]:
    """0–100 extension confidence.

    Weights (sum 100): ATR fuel 40 · volume 25 · dir% 20 · unused chase room 15.
    Missing inputs contribute 0 for that term (fail-open rank, not a veto).
    """
    atr_v = _f(atr)
    if atr_v is None:
        a5 = _f(atr_5min)
        px = _f(price)
        if a5 is not None and px is not None and px > 0:
            # 5-min dollar ATR → percent of price (0.40 = 0.40%)
            atr_v = (a5 / px) * 100.0
    vol_v = _f(volume_ratio)
    dir_v = _f(direction_volume_pct)
    brk = break_pct_points(break_distance_pct)

    atr_term = 0.0
    if atr_v is not None and atr_v > 0:
        # Aug 17 held-beyond names sat ~0.20–1.00. 0.40 ATR → full fuel.
        atr_term = _clip01(float(atr_v) / 0.40) * 40.0

    vol_term = 0.0
    if vol_v is not None and vol_v > 0:
        vol_term = _clip01(float(vol_v) / 1.0) * 25.0

    dir_term = 0.0
    if dir_v is not None and dir_v > 0:
        dir_term = _clip01(float(dir_v) / 0.55) * 20.0

    room_term = 0.0
    span = max(float(max_break_pct) - float(min_break_pct), 1e-6)
    if brk is not None:
        room = (float(max_break_pct) - float(brk)) / span
        room_term = _clip01(room) * 15.0

    score = max(0.0, min(100.0, atr_term + vol_term + dir_term + room_term))
    fields: Dict[str, Any] = {
        "extension_score": round(score, 2),
        "atr_term": round(atr_term, 2),
        "vol_term": round(vol_term, 2),
        "dir_term": round(dir_term, 2),
        "room_term": round(room_term, 2),
        "atr": atr_v,
        "volume_ratio": vol_v,
        "direction_volume_pct": dir_v,
        "break_pct": brk,
    }
    return score, fields


def extension_score_from_mapping(row: Mapping[str, Any]) -> Tuple[float, Dict[str, Any]]:
    extras = row.get("extras") if isinstance(row.get("extras"), dict) else {}
    atr = row.get("atr")
    if atr is None:
        atr = extras.get("atr") if extras else None
    return compute_breakout_extension_score(
        atr=atr,
        atr_5min=row.get("atr_5min"),
        price=row.get("current_price") or row.get("price"),
        volume_ratio=row.get("volume_ratio") or row.get("equity_volume_ratio"),
        direction_volume_pct=row.get("direction_volume_pct"),
        break_distance_pct=row.get("break_distance_pct") or row.get("break_pct"),
    )
