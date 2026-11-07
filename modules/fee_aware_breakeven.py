"""0DTE single-leg breakeven / scratch exits (premium PnL bands after hold window).

Never-armed trades (max MFE < ARM_TRAIL) exit on premium bands:

  breakeven  →  ≥60m · −0.05% ≤ premium PnL < +2%
  scratch    →  ≥62m · premium PnL < −0.05%

Cash-flat economics remain in ``fee_aware_snapshot`` for telemetry only —
they do not gate the exit. ARM_TRAIL-armed winners are never scratched via
this path.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, Optional, Tuple


EXIT_REASON_BREAKEVEN = "breakeven"
EXIT_REASON_SCRATCH = "scratch"


def _dec(value: Any) -> Optional[Decimal]:
    """Parse bid without truthiness — valid 0.00 stays 0, not None."""
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def resolve_breakeven_exit_premium(position: Any) -> Optional[float]:
    """Prefer stored entry economics; else recompute from fees config (telemetry)."""
    from modules.options_trade_economics import (
        breakeven_exit_premium_per_share,
        compute_closing_fees,
        compute_opening_fees,
        entry_fill_from_position,
        extract_entry_economics_from_metadata,
    )

    meta = getattr(position, "metadata", None) or {}
    if not isinstance(meta, dict):
        meta = {}
    entry = extract_entry_economics_from_metadata(meta)
    if isinstance(entry, dict):
        be = entry.get("breakeven_exit_premium")
        if be is not None:
            try:
                return float(be)
            except (TypeError, ValueError):
                pass
        fill = float(entry.get("fill") or 0.0)
        qty = max(1, int(entry.get("contracts") or getattr(position, "quantity", 1) or 1))
        open_fees = float(entry.get("opening_fees") or 0.0)
        close_fees = compute_closing_fees(
            quantity=qty,
            position_type=str(getattr(position, "position_type", "lotto") or "lotto"),
        )
        if fill > 0:
            return breakeven_exit_premium_per_share(
                entry_fill=fill,
                quantity=qty,
                opening_fees=open_fees,
                closing_fees=close_fees,
            )

    fill = entry_fill_from_position(position)
    qty = max(1, int(getattr(position, "quantity", 1) or 1))
    pt = str(getattr(position, "position_type", "lotto") or "lotto")
    if fill <= 0:
        return None
    return breakeven_exit_premium_per_share(
        entry_fill=fill,
        quantity=qty,
        opening_fees=compute_opening_fees(quantity=qty, position_type=pt),
        closing_fees=compute_closing_fees(quantity=qty, position_type=pt),
    )


def fee_aware_snapshot(
    *,
    position: Any,
    current_executable_bid: Optional[float],
    entry_fill: Optional[float] = None,
) -> Dict[str, Any]:
    """Separate gross premium PnL from cash (fee-aware) breakeven — telemetry only."""
    from modules.options_trade_economics import (
        compute_closing_fees,
        compute_opening_fees,
        entry_fill_from_position,
    )

    entry = float(entry_fill) if entry_fill is not None else float(entry_fill_from_position(position) or 0.0)
    qty = max(1, int(getattr(position, "quantity", 1) or 1))
    pt = str(getattr(position, "position_type", "lotto") or "lotto")
    open_fees = compute_opening_fees(quantity=qty, position_type=pt)
    close_fees = compute_closing_fees(quantity=qty, position_type=pt)
    be = resolve_breakeven_exit_premium(position)
    bid_d = _dec(current_executable_bid)
    bid = float(bid_d) if bid_d is not None else None

    raw_premium_pnl_pct = None
    if entry > 0 and bid is not None:
        raw_premium_pnl_pct = (bid / entry) - 1.0

    gross_pnl = None
    if entry > 0 and bid is not None:
        gross_pnl = round((bid - entry) * qty * 100.0, 2)

    total_fees = round(open_fees + close_fees, 2)
    net_pnl = None
    if bid is not None and entry > 0:
        gross_exit = bid * qty * 100.0
        entry_cost = entry * qty * 100.0 + open_fees
        net_pnl = round(gross_exit - close_fees - entry_cost, 2)

    dist = None
    dist_pct = None
    reached = False
    if be is not None and bid is not None:
        dist = round(float(be) - bid, 6)
        if float(be) > 0:
            dist_pct = round(dist / float(be), 6)
        reached = bid + 1e-12 >= float(be)

    return {
        "raw_premium_pnl_pct": raw_premium_pnl_pct,
        "gross_pnl": gross_pnl,
        "estimated_round_trip_fees": total_fees,
        "net_pnl_after_fees": net_pnl,
        "breakeven_exit_premium": be,
        "distance_to_cash_breakeven": dist,
        "distance_to_cash_breakeven_pct": dist_pct,
        "fee_aware_breakeven_reached": bool(reached),
        "current_executable_bid": bid,
        "entry_fill": entry,
        "opening_fees": open_fees,
        "closing_fees_est": close_fees,
    }


def classify_breakeven_or_scratch(
    *,
    is_0dte_single: bool,
    held_minutes: float,
    current_pnl_pct: float,
    max_pnl_pct: float = 0.0,
    mfe_giveback_trigger_pct: float = 0.10,
    breakeven_minutes: float = 60.0,
    scratch_minutes: Optional[float] = None,
    breakeven_enabled: bool = True,
    exit_below_pnl_pct: float = 0.02,
    scratch_below_pnl_pct: float = -0.0005,
    position: Any = None,
    current_executable_bid: Optional[float] = None,
) -> Tuple[Optional[str], Dict[str, Any]]:
    """Return (``breakeven`` | ``scratch`` | None, snapshot).

    Never-armed singles:

    - ``breakeven`` when hold ≥ ``breakeven_minutes`` (60) and
      ``scratch_below_pnl_pct`` ≤ premium PnL < ``exit_below_pnl_pct``
      (−0.05% … &lt; +2%)
    - ``scratch`` when hold ≥ ``scratch_minutes`` (62) and
      premium PnL < ``scratch_below_pnl_pct`` (−0.05%)
    """
    snap: Dict[str, Any] = {}
    if position is not None:
        try:
            snap = fee_aware_snapshot(
                position=position,
                current_executable_bid=current_executable_bid,
            )
        except Exception:
            snap = {}

    if not breakeven_enabled or not is_0dte_single:
        return None, snap
    if float(max_pnl_pct or 0.0) + 1e-12 >= float(mfe_giveback_trigger_pct or 0.10):
        return None, snap

    pnl = float(current_pnl_pct or 0.0)
    held = float(held_minutes or 0.0)
    ceiling = float(exit_below_pnl_pct if exit_below_pnl_pct is not None else 0.02)
    scratch_floor = float(
        scratch_below_pnl_pct if scratch_below_pnl_pct is not None else -0.0005
    )
    be_min = float(breakeven_minutes or 60.0)
    sc_min = float(scratch_minutes if scratch_minutes is not None else 62.0)

    out = dict(snap)
    out["premium_pnl_pct"] = pnl
    out["exit_below_pnl_pct"] = ceiling
    out["scratch_below_pnl_pct"] = scratch_floor
    out["breakeven_minutes"] = be_min
    out["scratch_minutes"] = sc_min

    if pnl + 1e-12 >= ceiling:
        return None, out

    if pnl + 1e-12 < scratch_floor:
        if held + 1e-12 < sc_min:
            return None, out
        out["exit_band"] = EXIT_REASON_SCRATCH
        return EXIT_REASON_SCRATCH, out

    # BE band: scratch_floor ≤ pnl < ceiling
    if held + 1e-12 < be_min:
        return None, out
    out["exit_band"] = EXIT_REASON_BREAKEVEN
    return EXIT_REASON_BREAKEVEN, out


def should_exit_fee_aware_breakeven(
    *,
    is_0dte_single: bool,
    held_minutes: float,
    current_executable_bid: Optional[float],
    position: Any,
    breakeven_minutes: float = 60.0,
    scratch_minutes: Optional[float] = None,
    breakeven_enabled: bool = True,
    max_pnl_pct: float = 0.0,
    mfe_giveback_trigger_pct: float = 0.10,
    current_pnl_pct: Optional[float] = None,
    exit_below_pnl_pct: float = 0.02,
    scratch_below_pnl_pct: float = -0.0005,
) -> Tuple[bool, Dict[str, Any]]:
    """True when ``breakeven`` or ``scratch`` would fire (test / compat helper)."""
    snap = fee_aware_snapshot(
        position=position,
        current_executable_bid=current_executable_bid,
    )
    if current_pnl_pct is not None:
        pnl = float(current_pnl_pct)
    elif snap.get("raw_premium_pnl_pct") is not None:
        pnl = float(snap["raw_premium_pnl_pct"])
    else:
        pnl = 0.0
    reason, out = classify_breakeven_or_scratch(
        is_0dte_single=is_0dte_single,
        held_minutes=held_minutes,
        current_pnl_pct=pnl,
        max_pnl_pct=max_pnl_pct,
        mfe_giveback_trigger_pct=mfe_giveback_trigger_pct,
        breakeven_minutes=breakeven_minutes,
        scratch_minutes=scratch_minutes,
        breakeven_enabled=breakeven_enabled,
        exit_below_pnl_pct=float(exit_below_pnl_pct if exit_below_pnl_pct is not None else 0.02),
        scratch_below_pnl_pct=scratch_below_pnl_pct,
        position=position,
        current_executable_bid=current_executable_bid,
    )
    return reason is not None, out
