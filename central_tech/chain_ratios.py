"""Same-snapshot chain volume and open-interest ratios.

These six keys are computed from the chain totals already on the signal.
They are not the rolling Dynamic Volume averages. A missing input or a
non-positive denominator leaves the key unset.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

_RATIO_FIELDS = (
    "call_chain_volume_share",
    "call_put_chain_volume_ratio",
    "put_call_chain_volume_ratio",
    "call_chain_vol_oi",
    "put_chain_vol_oi",
    "total_chain_vol_oi",
)


def _f(val: Any) -> Optional[float]:
    if val is None or val == "":
        return None
    try:
        out = float(val)
    except (TypeError, ValueError):
        return None
    if out != out:
        return None
    return out


def _div(num: Optional[float], den: Optional[float]) -> Optional[float]:
    if num is None or den is None or den <= 0:
        return None
    return float(num) / float(den)


def chain_ratio_fields(row: Dict[str, Any]) -> Dict[str, float]:
    """Return the six ratios that can be computed from this row."""
    if not isinstance(row, dict):
        return {}
    cv = _f(row.get("call_chain_volume"))
    pv = _f(row.get("put_chain_volume"))
    coi = _f(row.get("call_chain_open_interest"))
    poi = _f(row.get("put_chain_open_interest"))
    total_vol = None if cv is None or pv is None else cv + pv
    total_oi = None if coi is None or poi is None else coi + poi
    values = {
        "call_chain_volume_share": _div(cv, total_vol),
        "call_put_chain_volume_ratio": _div(cv, pv),
        "put_call_chain_volume_ratio": _div(pv, cv),
        "call_chain_vol_oi": _div(cv, coi),
        "put_chain_vol_oi": _div(pv, poi),
        "total_chain_vol_oi": _div(total_vol, total_oi),
    }
    return {k: v for k, v in values.items() if v is not None and k in _RATIO_FIELDS}


def stamp_chain_ratios(row: Dict[str, Any]) -> int:
    """Write the six ratios onto the row from the current chain totals."""
    fields = chain_ratio_fields(row)
    if not fields:
        return 0
    n = 0
    for key, value in fields.items():
        if row.get(key) != value:
            row[key] = value
            n += 1
    return n
