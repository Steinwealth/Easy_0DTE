"""Pre-session and post-session checks for research-critical Entry fields.

Warmup-dependent keys may be null. Required keys that are unexpectedly
absent are reported as errors. This module does not fill values.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

REQUIRED_CORE = "REQUIRED_CORE"
WARMUP_DEPENDENT = "WARMUP_DEPENDENT"
PATH_DEPENDENT = "PATH_DEPENDENT"
OPTIONAL = "OPTIONAL"
NOT_APPLICABLE = "NOT_APPLICABLE"

# The research-critical Entry list. Classification is about whether a null
# is a defect, not about whether the key is useful.
RESEARCH_CRITICAL_CLASS = {
    "call_chain_volume": REQUIRED_CORE,
    "put_chain_volume": REQUIRED_CORE,
    "representative_call_delta": REQUIRED_CORE,
    "representative_put_delta": REQUIRED_CORE,
    "price_volume_efficiency_3m": WARMUP_DEPENDENT,
    "price_volume_efficiency_5m": WARMUP_DEPENDENT,
    "break_distance_pct": REQUIRED_CORE,
    "call_chain_volume_delta_5m": WARMUP_DEPENDENT,
    "put_chain_volume_delta_5m": WARMUP_DEPENDENT,
    "same_side_chain_volume_delta_5m": WARMUP_DEPENDENT,
    "same_side_chain_volume_5m_ago": WARMUP_DEPENDENT,
    "total_chain_open_interest": REQUIRED_CORE,
    "call_chain_open_interest": REQUIRED_CORE,
    "put_chain_open_interest": REQUIRED_CORE,
    "rsi": WARMUP_DEPENDENT,
    "implied_volatility": REQUIRED_CORE,
    "theta": REQUIRED_CORE,
    "delta": REQUIRED_CORE,
    "gamma": REQUIRED_CORE,
    "vega": REQUIRED_CORE,
    "spy_change_pct": REQUIRED_CORE,
    "distance_from_vwap_atr": PATH_DEPENDENT,
    "vwap_distance_pct": REQUIRED_CORE,
    "dir_pct_change_5m": WARMUP_DEPENDENT,
    "relative_volume_time_adjusted": WARMUP_DEPENDENT,
    "quality_score": PATH_DEPENDENT,
    "atr": REQUIRED_CORE,
    "underlying_volume_1m": REQUIRED_CORE,
    "volume_1m": REQUIRED_CORE,
    "break_level": PATH_DEPENDENT,
    "break_timestamp": PATH_DEPENDENT,
    "underlying_price": REQUIRED_CORE,
    "vwap": REQUIRED_CORE,
    "macd": WARMUP_DEPENDENT,
    "macd_signal": WARMUP_DEPENDENT,
    "macd_histogram": WARMUP_DEPENDENT,
}


def _blank(value: Any) -> bool:
    return value is None or value == ""


def moon_null_is_expected(row: Dict[str, Any]) -> bool:
    """A moon delta may be null when the decision chain was not in hand.

    When both side volumes are present, the chain was available and a null
    moon delta is unexpected.
    """
    if not isinstance(row, dict):
        return True
    return _blank(row.get("call_chain_volume")) or _blank(row.get("put_chain_volume"))


def audit_research_critical(row: Optional[Dict[str, Any]]) -> Dict[str, List[str]]:
    """Split the research-critical list into populated and null buckets."""
    src = row if isinstance(row, dict) else {}
    out: Dict[str, List[str]] = {
        "populated": [],
        "required_missing": [],
        "warmup_null": [],
        "path_null": [],
        "optional_null": [],
        "moon_null_expected": [],
    }
    for key, kind in RESEARCH_CRITICAL_CLASS.items():
        if not _blank(src.get(key)):
            out["populated"].append(key)
            continue
        if key in ("representative_call_delta", "representative_put_delta") and moon_null_is_expected(src):
            out["moon_null_expected"].append(key)
            continue
        if kind == REQUIRED_CORE:
            out["required_missing"].append(key)
        elif kind == WARMUP_DEPENDENT:
            out["warmup_null"].append(key)
        elif kind == PATH_DEPENDENT:
            out["path_null"].append(key)
        else:
            out["optional_null"].append(key)
    return out
