"""Compact booked-equity series: seed + exits + withdrawals.

Fat closed-trade JSON stays on the 2-day / count cap. This book is O(events),
never stores execution_payload, and is the durable source for the desk line.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional


def iso_ts(raw: Any = None) -> str:
    if raw is None:
        return datetime.now(timezone.utc).isoformat()
    if isinstance(raw, datetime):
        ts = raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
        return ts.isoformat()
    text = str(raw).strip()
    return text or datetime.now(timezone.utc).isoformat()


def point(*, t: Any, equity: float, delta: float = 0.0, kind: str = "exit") -> Dict[str, Any]:
    return {
        "t": iso_ts(t),
        "equity": round(float(equity or 0.0), 2),
        "delta": round(float(delta or 0.0), 2),
        "kind": str(kind or "exit"),
    }


def empty_book(seed: float, t: Any = None) -> Dict[str, Any]:
    seed_eq = round(float(seed or 0.0), 2)
    return {
        "booked_equity": seed_eq,
        "peak_equity": seed_eq,
        "withdrawals_total": 0.0,
        "equity_curve": [point(t=t, equity=seed_eq, delta=0.0, kind="seed")],
    }


def apply_event(book: Optional[Dict[str, Any]], *, delta: float, t: Any, kind: str) -> Dict[str, Any]:
    src = dict(book or {})
    curve: List[Dict[str, Any]] = list(src.get("equity_curve") or [])
    delta_n = round(float(delta or 0.0), 2)
    kind_s = str(kind or "exit")
    if not curve:
        src = empty_book(float(src.get("booked_equity") or 0.0), t)
        curve = list(src["equity_curve"])
    running = round(float(src.get("booked_equity") or 0.0) + delta_n, 2)
    src["booked_equity"] = running
    peak = float(src.get("peak_equity") or running)
    src["peak_equity"] = round(max(peak, running), 2)
    if kind_s in ("withdraw", "withdrawal"):
        src["withdrawals_total"] = round(float(src.get("withdrawals_total") or 0.0) + abs(delta_n), 2)
    curve.append(point(t=t, equity=running, delta=delta_n, kind=kind_s))
    src["equity_curve"] = curve
    src["withdrawals_total"] = round(float(src.get("withdrawals_total") or 0.0), 2)
    return src


def curve_has_seed_adjust(curve: Any) -> bool:
    if not isinstance(curve, list):
        return False
    for raw in curve:
        if isinstance(raw, dict) and str(raw.get("kind") or "").strip().lower() == "seed_adjust":
            return True
    return False


def rebase_without_seed_adjusts(seed: float, curve: Any, t0: Any = None) -> Optional[Dict[str, Any]]:
    """Replay exits and withdrawals onto the seed.

    ``seed_adjust`` points are not closed-trade P&L. Leaving them in the series
    makes booked equity miss the seed, so the desk prints seed-sized fake losses.
    """
    if not isinstance(curve, list) or not curve_has_seed_adjust(curve):
        return None
    start = t0
    for raw in curve:
        if isinstance(raw, dict) and raw.get("t"):
            start = raw.get("t")
            break
    book = empty_book(float(seed or 0.0), start)
    for raw in curve:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind") or "exit").strip().lower()
        if kind in ("seed", "seed_adjust"):
            continue
        delta = raw.get("delta")
        if delta is None:
            continue
        book = apply_event(book, delta=float(delta), t=raw.get("t"), kind=kind or "exit")
    return book


def curve_is_seed_only(curve: Any) -> bool:
    """True when every point is a seed or seed_adjust (no booked close or withdrawal)."""
    if not isinstance(curve, list) or not curve:
        return False
    saw = False
    for raw in curve:
        if not isinstance(raw, dict):
            continue
        saw = True
        kind = str(raw.get("kind") or "exit").strip().lower()
        if kind not in ("seed", "seed_adjust"):
            return False
    return saw


def _row_pnl(row: Any) -> float:
    if isinstance(row, dict):
        return float(row.get("realized_pnl") or 0.0)
    return float(getattr(row, "realized_pnl", 0.0) or 0.0)


def _row_t(row: Any) -> Any:
    for key in ("closed_at", "exit_time", "entry_time", "opened_at"):
        if isinstance(row, dict):
            val = row.get(key)
        else:
            val = getattr(row, key, None)
        if val:
            return val
    return None


def _row_close_t(row: Any) -> Any:
    for key in ("closed_at", "exit_time"):
        if isinstance(row, dict):
            val = row.get(key)
        else:
            val = getattr(row, key, None)
        if val:
            return val
    return None


def backfill_from_closes(seed: float, rows: Iterable[Any], t0: Any = None) -> Dict[str, Any]:
    items = [r for r in (rows or []) if r is not None and _row_close_t(r)]
    items.sort(key=lambda r: iso_ts(_row_close_t(r)))
    start = t0
    if items:
        first_close = _row_close_t(items[0])
        first_any = _row_t(items[0])
        start = first_any or first_close or t0
    book = empty_book(seed, start)
    for row in items:
        book = apply_event(book, delta=_row_pnl(row), t=_row_close_t(row), kind="exit")
    return book


def restore_or_backfill(
    data: Optional[Dict[str, Any]],
    seed: float,
    closes: Iterable[Any],
) -> Dict[str, Any]:
    payload = data if isinstance(data, dict) else {}
    curve = payload.get("equity_curve")
    if isinstance(curve, list) and curve:
        out = {
            "booked_equity": 0.0,
            "peak_equity": 0.0,
            "withdrawals_total": round(float(payload.get("withdrawals_total") or 0.0), 2),
            "equity_curve": [],
        }
        for raw in curve:
            if not isinstance(raw, dict):
                continue
            if raw.get("t") is None or raw.get("equity") is None:
                continue
            out["equity_curve"].append(
                point(
                    t=raw.get("t"),
                    equity=raw.get("equity"),
                    delta=raw.get("delta") or 0.0,
                    kind=raw.get("kind") or "exit",
                )
            )
        if out["equity_curve"]:
            last_eq = out["equity_curve"][-1]["equity"]
            booked = payload.get("booked_equity")
            if booked is None:
                booked = last_eq
            out["booked_equity"] = round(float(booked), 2)
            peak = payload.get("peak_equity")
            if peak is None:
                peak = max(p["equity"] for p in out["equity_curve"])
            out["peak_equity"] = round(float(peak), 2)
            return out
    return backfill_from_closes(seed, closes)
