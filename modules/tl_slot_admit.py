"""Trendline slot claim + cash reservation, after Trade Profile admit (SINGLE_TP_ADMIT).

Oct 13 2026. When SINGLE_TP_ADMIT is on, the TL pipeline no longer claims a GCS slot
before the Trade Profile check. It calls ``claim_slot_and_reserve`` only after the
TP admits, as one step:

    1. refresh capital (balance - cash buffer - open entry cost)
    2. subtract in-flight reservations (other TL signals between claim and fill)
    3. size: entry_budget = min(slot_capital, effective_cash); reject if <= 0
       (no GCS call is made when cash is short)
    4. claim the GCS slot marker (existing claim fn, fast path kept)
    5. record the reservation

``release`` is idempotent and is the single release helper for every non-fill path.
``settle_fill`` drops only the in-flight cash (the fill is then counted in the open
entry cost) and keeps the GCS marker, which the position close path releases.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

_LOCK = threading.Lock()


@dataclass
class SlotReservation:
    symbol: str
    marker_path: Optional[str]
    reserved_cash: float
    slot_capital: float
    entry_budget: float
    available_cash: float
    buffer_reserve: float
    key: str = ""
    released: bool = field(default=False)
    settled: bool = field(default=False)


def _inflight(system: Any) -> Dict[str, float]:
    d = getattr(system, "_tl_inflight_reservations", None)
    if not isinstance(d, dict):
        d = {}
        setattr(system, "_tl_inflight_reservations", d)
    return d


def inflight_total(system: Any) -> float:
    return float(sum(_inflight(system).values()))


def compute_sizing(slot_capital: float, available_cash: float, inflight: float) -> Dict[str, float]:
    """available_cash already excludes the cash buffer and open entry cost."""
    eff = max(0.0, float(available_cash or 0.0) - max(0.0, float(inflight or 0.0)))
    budget = max(0.0, min(float(slot_capital or 0.0), eff))
    return {"effective_cash": eff, "entry_budget": budget}


def claim_slot_and_reserve(system: Any, symbol: str, local_open: int):
    """Return (SlotReservation, None) or (None, reason)."""
    with _LOCK:
        try:
            system._refresh_trendline_slot_capital()
        except Exception as e:  # keep last snapshot
            log.warning("TL_SLOT_ADMIT | stage=refresh_error | symbol=%s | error=%s", symbol, e)
        slot_capital = float(getattr(system, "_trendline_slot_capital", 0.0) or 0.0)
        available_cash = float(getattr(system, "_trendline_available_cash", 0.0) or 0.0)
        buffer_reserve = float(getattr(system, "_trendline_buffer_reserve", 0.0) or 0.0)
        infl = inflight_total(system)
        sz = compute_sizing(slot_capital, available_cash, infl)
        if sz["entry_budget"] <= 0.0:
            log.warning(
                "TL_SLOT_ADMIT | stage=cash_reject | symbol=%s | available_cash=%.2f | inflight=%.2f | "
                "buffer_reserve=%.2f | slot_capital=%.2f | gcs_ops=0",
                symbol, available_cash, infl, buffer_reserve, slot_capital,
            )
            return None, "buffer_reserve_exhausted"
        ok, marker = system._trendline_try_claim_global_daily_slot(
            signal_symbol=symbol, local_open=local_open
        )
        if not ok:
            return None, "global_daily_cap"
        key = marker or f"local:{symbol}:{id(object())}"
        _inflight(system)[key] = sz["entry_budget"]
        res = SlotReservation(
            symbol=symbol,
            marker_path=marker,
            reserved_cash=sz["entry_budget"],
            slot_capital=slot_capital,
            entry_budget=sz["entry_budget"],
            available_cash=sz["effective_cash"],
            buffer_reserve=buffer_reserve,
            key=key,
        )
        log.info(
            "TL_SLOT_ADMIT | stage=claimed | symbol=%s | marker=%s | reserved=%.2f | effective_cash=%.2f",
            symbol, marker, res.reserved_cash, res.available_cash,
        )
        return res, None


def release(system: Any, res: Optional[SlotReservation], reason: str = "") -> None:
    """Non-fill path: drop the cash hold and the GCS marker. Safe to call twice."""
    if res is None or res.released or res.settled:
        return
    with _LOCK:
        if res.released or res.settled:
            return
        res.released = True
        _inflight(system).pop(res.key, None)
    if res.marker_path:
        system._trendline_release_global_daily_slot(res.marker_path)
    log.info("TL_SLOT_ADMIT | stage=released | symbol=%s | reason=%s", res.symbol, reason)


def settle_fill(system: Any, res: Optional[SlotReservation]) -> None:
    """Fill: keep the marker (released on close), drop the in-flight cash hold."""
    if res is None or res.released or res.settled:
        return
    with _LOCK:
        res.settled = True
        _inflight(system).pop(res.key, None)
