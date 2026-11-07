"""Population check for a Priority Optimizer feature row.

Required keys are allowed to be null when the source was genuinely absent.
A collapse versus the session baseline is a warning, not a fabricated fill.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from .po_feature_schema import FEATURE_KEYS_0DTE_UNIFIED
from .po_source_contract import (
    NOT_APPLICABLE,
    REQUIRED,
    coverage_for_key,
    family_for_key,
)

# Handoff population on 2026-09-25 is the current baseline for required keys
# that actually appeared. The verifier warns when a new batch falls 20 points
# below that rate. Keys with no historical fills stay off this map.
ENTRY_BASELINE_PCT = {
    "rsi": 1.0,
    "macd": 1.0,
    "underlying_price": 1.0,
    "break_distance_pct": 0.95,
    "call_chain_volume": 0.90,
    "implied_volatility": 0.90,
    "vwap": 0.90,
    "vwap_distance_pct": 0.90,
    "spy_change_pct": 0.85,
}


def _blank(value: Any) -> bool:
    return value is None or value == ""


def summarize_rows(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    materialized = [row for row in rows if isinstance(row, dict)]
    n = len(materialized)
    families: Dict[str, Dict[str, int]] = {}
    keys: List[Dict[str, Any]] = []
    warnings: List[str] = []
    for key in FEATURE_KEYS_0DTE_UNIFIED:
        family = family_for_key(key)
        coverage = coverage_for_key(key)
        populated = sum(1 for row in materialized if not _blank(row.get(key)))
        pct = (populated / n) if n else 0.0
        bucket = families.setdefault(
            family, {"keys": 0, "populated": 0, "eligible": 0}
        )
        bucket["keys"] += 1
        if coverage != NOT_APPLICABLE:
            bucket["eligible"] += n
            bucket["populated"] += populated
        keys.append(
            {
                "key": key,
                "family": family,
                "coverage": coverage,
                "rows": n,
                "populated": populated,
                "populated_pct": round(pct, 4),
                "nulls": n - populated,
            }
        )
        floor = ENTRY_BASELINE_PCT.get(key)
        if floor is not None and n and coverage == REQUIRED and pct + 0.20 < floor:
            warnings.append(
                f"{key} population {pct:.0%} is more than 20 points under baseline {floor:.0%}"
            )
    for bucket in families.values():
        eligible = bucket["eligible"]
        bucket["populated_pct"] = round(bucket["populated"] / eligible, 4) if eligible else 0.0
    return {
        "rows": n,
        "families": families,
        "keys": keys,
        "warnings": warnings,
        "ok": not warnings,
    }


def null_reasons_for_row(row: Dict[str, Any]) -> Dict[str, str]:
    """Compact reasons for blank required and warmup keys. Filled keys are omitted."""
    from .po_source_contract import HISTORY_NOT_WARM, UPSTREAM_MISSING, WARMUP_DEPENDENT

    out: Dict[str, str] = {}
    if not isinstance(row, dict):
        return out
    for key in FEATURE_KEYS_0DTE_UNIFIED:
        if not _blank(row.get(key)):
            continue
        coverage = coverage_for_key(key)
        if coverage == REQUIRED:
            out[key] = UPSTREAM_MISSING
        elif coverage == WARMUP_DEPENDENT:
            out[key] = HISTORY_NOT_WARM
    return out
