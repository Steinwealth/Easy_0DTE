"""Timestamped in-process samples shared by every contract on a symbol.

A shadow tick may use the newest sample at or before its own clock, inside
the freshness window. It may not use a later sample, and a missing sample
stays missing. This module does not call the broker.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

def _chain_sample_max_age_sec() -> float:
    try:
        from central_tech.decision_freeze import chain_max_age_sec

        return float(chain_max_age_sec())
    except Exception:
        return 60.0


def _underlying_sample_max_age_sec() -> float:
    try:
        from central_tech.decision_freeze import quote_max_age_sec

        return float(quote_max_age_sec())
    except Exception:
        return 120.0


# Callers may still pass an explicit window. The defaults follow the one age table.
CHAIN_SAMPLE_MAX_AGE_SEC = 60.0
UNDERLYING_SAMPLE_MAX_AGE_SEC = 120.0

_ChainSample = Tuple[float, Optional[float], Optional[float]]
_UnderlyingSample = Tuple[float, float]

_CHAIN: Dict[str, List[_ChainSample]] = {}
_UNDERLYING: Dict[str, List[_UnderlyingSample]] = {}


def reset_source_rings_for_tests() -> None:
    _CHAIN.clear()
    _UNDERLYING.clear()


def _epoch(timestamp: object) -> Optional[float]:
    if timestamp in (None, ""):
        return None
    if isinstance(timestamp, (int, float)):
        return float(timestamp)
    text = str(timestamp).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _finite(value: object) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def note_chain_sample(
    symbol: str,
    timestamp: object,
    call_volume: object,
    put_volume: object,
) -> None:
    """Record a chain print. None stays out of the ring. Zero is a real print."""
    sym = str(symbol or "").strip().upper()
    epoch = _epoch(timestamp)
    call = _finite(call_volume)
    put = _finite(put_volume)
    if not sym or epoch is None or (call is None and put is None):
        return
    _CHAIN.setdefault(sym, []).append((epoch, call, put))


def chain_asof(
    symbol: str,
    timestamp: object,
    *,
    max_age_sec: Optional[float] = None,
) -> Optional[Tuple[Optional[float], Optional[float]]]:
    """Newest chain sample at or before ``timestamp`` inside the freshness window."""
    sym = str(symbol or "").strip().upper()
    tick = _epoch(timestamp)
    if not sym or tick is None:
        return None
    window = float(max_age_sec if max_age_sec is not None else _chain_sample_max_age_sec())
    chosen: Optional[_ChainSample] = None
    for sample in _CHAIN.get(sym, []):
        if sample[0] > tick:
            continue
        if tick - sample[0] > window:
            continue
        if chosen is None or sample[0] >= chosen[0]:
            chosen = sample
    if chosen is None:
        return None
    return chosen[1], chosen[2]


def note_underlying_sample(symbol: str, timestamp: object, price: object) -> None:
    sym = str(symbol or "").strip().upper()
    epoch = _epoch(timestamp)
    px = _finite(price)
    if not sym or epoch is None or px is None or px <= 0:
        return
    _UNDERLYING.setdefault(sym, []).append((epoch, px))


def underlying_asof(
    symbol: str,
    timestamp: object,
    *,
    max_age_sec: Optional[float] = None,
) -> Optional[float]:
    """Newest underlying print at or before ``timestamp`` inside the freshness window."""
    sym = str(symbol or "").strip().upper()
    tick = _epoch(timestamp)
    if not sym or tick is None:
        return None
    window = float(max_age_sec if max_age_sec is not None else _underlying_sample_max_age_sec())
    chosen: Optional[_UnderlyingSample] = None
    for sample in _UNDERLYING.get(sym, []):
        if sample[0] > tick:
            continue
        if tick - sample[0] > window:
            continue
        if chosen is None or sample[0] >= chosen[0]:
            chosen = sample
    if chosen is None:
        return None
    return chosen[1]


def attach_causal_chain_volume(fields: Dict[str, object], *, symbol: str) -> None:
    """Fill missing chain volume from the ring. Never writes zero for a miss."""
    if not isinstance(fields, dict):
        return
    call = fields.get("call_chain_volume")
    put = fields.get("put_chain_volume")
    if call is not None or put is not None:
        note_chain_sample(symbol, fields.get("timestamp"), call, put)
        return
    sample = chain_asof(symbol, fields.get("timestamp"))
    if sample is None:
        return
    if fields.get("call_chain_volume") is None and sample[0] is not None:
        fields["call_chain_volume"] = sample[0]
    if fields.get("put_chain_volume") is None and sample[1] is not None:
        fields["put_chain_volume"] = sample[1]
