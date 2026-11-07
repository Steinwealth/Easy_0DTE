"""Profit-retention telemetry — measurement only; does not change MFE policy."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional


def liquidatable_pnl_to_fraction(
    raw: Any,
    *,
    engine_fraction: Optional[float] = None,
) -> Optional[float]:
    """Normalize mixed fraction / percent-point PnL to a 0–1 fraction.

    Stealth ``max_pnl_pct`` is a fraction (ADBE HWM +1.37% → ``0.013699``).
    Profit-retention stores percent points (``1.3699``). Telegram used to
    treat ``1.3699`` as a fraction and print **+136.99%**. Values in
    ``(1.0, 1.5]`` are ambiguous without the engine fraction: +1.37% points
    vs a +137% moon. When ``engine_fraction`` is the stealth HWM, prefer it
    if ``raw`` is the same MFE in percent points (≈100×).
    """
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value != value:
        return None

    engine: Optional[float] = None
    if engine_fraction is not None:
        try:
            engine = float(engine_fraction)
        except (TypeError, ValueError):
            engine = None
        if engine is not None and engine != engine:
            engine = None

    if engine is not None:
        same_unit = abs(value - engine) <= max(1e-4, abs(engine) * 0.02)
        if same_unit:
            return engine
        as_fraction = value / 100.0
        if abs(as_fraction - engine) <= max(1e-4, abs(engine) * 0.05):
            return engine

    if abs(value) > 1.5:
        return value / 100.0
    return value


def premium_hwm_from_exit_metadata(metadata: Any) -> Optional[float]:
    """Best live bid (or spread value) stamped on close metadata."""
    blobs: List[Any] = []
    if isinstance(metadata, dict):
        blobs.extend(
            [
                metadata.get("option_stealth"),
                metadata.get("premium_snapshot"),
                metadata,
            ]
        )
    for blob in blobs:
        if not isinstance(blob, dict):
            continue
        raw = blob.get("premium_hwm")
        if raw is None:
            raw = blob.get("hwm_premium")
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 1e-12:
            return value
    return None


def display_max_bid_pnl_fraction(
    max_pnl_pct: Any,
    *,
    engine_fraction: Optional[float] = None,
    entry_premium: Optional[float] = None,
    premium_hwm: Optional[float] = None,
) -> float:
    """Telegram Max P&L (Bid): peak bid vs fill, not favorable-only MFE.

    Engine ``max_pnl_pct`` starts at 0 and only ratchets up, so a never-green
    trade prints +0.00% even when the best live bid was red vs fill. When that
    clamped 0 is paired with a live ``premium_hwm`` below entry, return
    ``(hwm - entry) / entry`` so Max matches the peak bid.
    """
    converted = liquidatable_pnl_to_fraction(
        max_pnl_pct, engine_fraction=engine_fraction
    )
    max_v = float(converted) if converted is not None else 0.0
    try:
        entry = float(entry_premium) if entry_premium is not None else 0.0
    except (TypeError, ValueError):
        entry = 0.0
    try:
        hwm = float(premium_hwm) if premium_hwm is not None else 0.0
    except (TypeError, ValueError):
        hwm = 0.0
    if entry > 1e-12 and hwm > 1e-12:
        peak = (hwm - entry) / entry
        if abs(max_v) < 1e-9 and peak < -1e-9:
            return peak
    return max_v


def first_present_number(*values: Any) -> Optional[float]:
    """First numeric value, including 0.0 (unlike ``a or b``)."""
    for raw in values:
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value == value:
            return value
    return None


def fill_premium_pnl_fraction(
    entry_premium: Optional[float],
    exit_premium: Optional[float],
) -> Optional[float]:
    try:
        entry = float(entry_premium) if entry_premium is not None else 0.0
        exit_px = float(exit_premium) if exit_premium is not None else 0.0
    except (TypeError, ValueError):
        return None
    if entry <= 1e-12 or exit_px <= 1e-12:
        return None
    return (exit_px - entry) / entry


def reconcile_exit_liq_fraction(
    raw_liq: Any,
    *,
    entry_premium: Optional[float] = None,
    exit_premium: Optional[float] = None,
    engine_fraction: Optional[float] = None,
) -> Optional[float]:
    """Exit P&L (liq) must match the printed exit fill, not decision-bid or 100× points.

    Retention stores percent points (VST +0.93% → ``0.9259``). ``liquidatable_pnl_to_fraction``
    treats ``|x|<1.5`` as a fraction, so that prints **+92.59%**. Decision-bid osnap can
    also disagree with the fill (QQQ bid $0.18 vs demo fill $0.47). Prefer fill vs entry
    when the converted liq disagrees by more than 5pp, or when converted liq is ~0 and
    fill is live.
    """
    fill_frac = fill_premium_pnl_fraction(entry_premium, exit_premium)
    engine = engine_fraction if engine_fraction is not None else fill_frac
    converted = liquidatable_pnl_to_fraction(raw_liq, engine_fraction=engine)
    if fill_frac is None:
        return converted
    if converted is None:
        return fill_frac
    if abs(float(converted)) < 1e-9 and abs(fill_frac) >= 1e-9:
        return fill_frac
    if abs(float(converted) - fill_frac) >= 0.05:
        return fill_frac
    return float(converted)


def profit_retention_telemetry_enabled() -> bool:
    raw = os.getenv("0DTE_PROFIT_RETENTION_TELEMETRY_ENABLED", "true")
    return str(raw or "true").strip().lower() in ("1", "true", "yes", "on")


def compute_profit_retention(
    *,
    max_liquidatable_pnl_pct: Optional[float],
    exit_liquidatable_pnl_pct: Optional[float],
    exit_reason: str = "",
    mfe_armed: bool = False,
    mfe_triggered: bool = False,
    time_from_mfe_arm_to_exit_sec: Optional[float] = None,
    time_from_bid_hwm_to_exit_sec: Optional[float] = None,
    quote_rejections_after_hwm: int = 0,
    ownership_gate_duration_ms: Optional[float] = None,
) -> Dict[str, Any]:
    """Only compute ratio when max liquidatable PnL is strictly positive."""
    out: Dict[str, Any] = {
        "max_liquidatable_pnl_pct": max_liquidatable_pnl_pct,
        "exit_liquidatable_pnl_pct": exit_liquidatable_pnl_pct,
        "profit_retention_ratio": None,
        "profit_given_back_pct_points": None,
        "exit_reason": str(exit_reason or ""),
        "mfe_armed": bool(mfe_armed),
        "mfe_triggered": bool(mfe_triggered),
        "time_from_mfe_arm_to_exit_sec": time_from_mfe_arm_to_exit_sec,
        "time_from_bid_hwm_to_exit_sec": time_from_bid_hwm_to_exit_sec,
        "quote_rejections_after_hwm": int(quote_rejections_after_hwm or 0),
        "ownership_gate_duration_ms": ownership_gate_duration_ms,
    }
    try:
        mx = float(max_liquidatable_pnl_pct) if max_liquidatable_pnl_pct is not None else None
        ex = float(exit_liquidatable_pnl_pct) if exit_liquidatable_pnl_pct is not None else None
    except (TypeError, ValueError):
        return out
    if mx is None or mx <= 0:
        return out
    if ex is None:
        return out
    out["profit_retention_ratio"] = round(ex / mx, 6)
    out["profit_given_back_pct_points"] = round(mx - ex, 6)
    return out


def attach_profit_retention_to_position(position: Any, retention: Dict[str, Any]) -> None:
    meta = getattr(position, "metadata", None)
    if not isinstance(meta, dict):
        try:
            position.metadata = {}
            meta = position.metadata
        except Exception:
            return
    meta["profit_retention"] = dict(retention)


def group_retention_by_exit_reason(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """EOD aggregation by exit reason."""
    buckets: Dict[str, List[float]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        reason = str(row.get("exit_reason") or "unknown")
        ratio = row.get("profit_retention_ratio")
        if ratio is None:
            continue
        try:
            buckets.setdefault(reason, []).append(float(ratio))
        except (TypeError, ValueError):
            continue
    out: Dict[str, Dict[str, Any]] = {}
    for reason, vals in buckets.items():
        if not vals:
            continue
        out[reason] = {
            "count": len(vals),
            "avg_retention": round(sum(vals) / len(vals), 6),
            "min_retention": round(min(vals), 6),
            "max_retention": round(max(vals), 6),
        }
    return out
