#!/usr/bin/env python3
"""Quote-quality helpers, strict contract lookup, and trade-record validation."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

STRIKE_MATCH_EPS = 0.01


def parse_timestamp_utc(ts: Any) -> Optional[datetime]:
    """Parse ISO timestamps; naive values are treated as UTC."""
    if ts is None:
        return None
    if isinstance(ts, datetime):
        dt = ts
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    raw = str(ts).strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            dt = dt.astimezone(timezone.utc)
        return dt
    except Exception:
        return None


def seconds_between_timestamps(t0: str, t1: str) -> Optional[float]:
    a, b = parse_timestamp_utc(t0), parse_timestamp_utc(t1)
    if not a or not b:
        return None
    return max(0.0, (b - a).total_seconds())


def _contract_option_symbol(contract: Any) -> str:
    for attr in ("option_symbol", "symbol", "occ_symbol", "contract_symbol"):
        val = getattr(contract, attr, None)
        if val:
            return str(val).strip().upper()
    if isinstance(contract, dict):
        for key in ("option_symbol", "symbol", "occ_symbol", "contract_symbol"):
            val = contract.get(key)
            if val:
                return str(val).strip().upper()
    return ""


def find_contract_at_strike_strict(
    contracts: List[Any],
    target_strike: float,
    *,
    expected_option_symbol: str = "",
    expected_option_type: str = "",
) -> Optional[Any]:
    """Exact strike match only — no nearest-strike fallback (shadow path)."""
    try:
        tgt = float(target_strike)
    except (TypeError, ValueError):
        return None
    if tgt <= 0:
        return None
    exp_sym = str(expected_option_symbol or "").strip().upper()
    exp_type = str(expected_option_type or "").strip().lower()
    at_strike: List[Any] = []
    for contract in contracts or []:
        try:
            c_strike = float(getattr(contract, "strike", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        if abs(c_strike - tgt) > STRIKE_MATCH_EPS:
            continue
        if exp_type:
            c_type = str(getattr(contract, "option_type", "") or "").lower()
            if not c_type and hasattr(contract, "option_type_label"):
                c_type = str(getattr(contract, "option_type_label", "") or "").lower()
            if c_type and not c_type.startswith(exp_type[:1]):
                continue
        at_strike.append(contract)
    if not at_strike:
        return None
    if exp_sym:
        sym_hits = [c for c in at_strike if _contract_option_symbol(c) == exp_sym]
        if len(sym_hits) == 1:
            return sym_hits[0]
        if len(sym_hits) > 1:
            return sym_hits[0]
    # Single strike+type match when internal vs OCC option_symbol differs (shadow path).
    if len(at_strike) == 1:
        return at_strike[0]
    if exp_sym:
        for contract in at_strike:
            if _contract_option_symbol(contract) == exp_sym:
                return contract
    return None


def classify_premium_source_bucket(source: str) -> str:
    from modules.exit_grade_durability import (
        PREMIUM_SOURCE_BUCKET_EXACT,
        canonicalize_quote_source,
        quote_source_is_exact_contract,
    )

    s = canonicalize_quote_source(source).strip().lower()
    if quote_source_is_exact_contract(s) or s in (
        "exit_grade",
        "option_mark",
        "option_mid",
        "spread_mtm_mid_last",
    ):
        return PREMIUM_SOURCE_BUCKET_EXACT
    if s in ("nearest", "option_last", "nearest_strike", "chain_strike_match"):
        return "nearest"
    if "cached" in s:
        return "cached"
    if s in ("partial_leg",):
        return "cached"
    if "synthetic" in s or "modeled" in s or s in (
        "spread_mtm_incomplete_quotes",
        "spread_mtm_no_legs",
        "spread_mtm_missing_leg_role",
        "unknown",
        "",
    ):
        return "synthetic"
    if "live_chain" in s or "bidask" in s or "fresh_chain" in s:
        return PREMIUM_SOURCE_BUCKET_EXACT
    return "nearest"


def quote_quality_pct(counts: Dict[str, int]) -> Dict[str, Optional[float]]:
    total = sum(int(v) for v in counts.values())
    if total <= 0:
        return {
            "shadow_exact_quote_pct": None,
            "shadow_cached_quote_pct": None,
            "shadow_synthetic_quote_pct": None,
            "shadow_nearest_quote_pct": None,
        }

    def _pct(key: str) -> float:
        return float(counts.get(key, 0)) / float(total)

    return {
        "shadow_exact_quote_pct": _pct("exact"),
        "shadow_cached_quote_pct": _pct("cached"),
        "shadow_synthetic_quote_pct": _pct("synthetic"),
        "shadow_nearest_quote_pct": _pct("nearest"),
    }


def validate_trade_record(trade: Dict[str, Any], *, require_0dte_path: bool = True) -> Tuple[bool, List[str]]:
    """Return (ok, missing_field_names) for research-ready records."""
    missing: List[str] = []
    entry = trade.get("entry") if isinstance(trade.get("entry"), dict) else {}
    path = str(entry.get("strategy_path") or "")
    if not entry.get("trade_id"):
        missing.append("entry.trade_id")
    if not entry.get("symbol"):
        missing.append("entry.symbol")
    if not entry.get("entry_time"):
        missing.append("entry.entry_time")
    if path in ("orb_0dte", "trendline_0dte"):
        if entry.get("entry_premium") is None:
            missing.append("entry.entry_premium")
        contract = entry.get("contract_identity")
        if not isinstance(contract, dict) or not contract:
            missing.append("entry.contract_identity")
        selector = entry.get("selector_metadata")
        if not isinstance(selector, dict):
            missing.append("entry.selector_metadata")
        eq = entry.get("entry_execution_quality")
        if not isinstance(eq, dict):
            missing.append("entry.entry_execution_quality")
        if require_0dte_path and not trade.get("premium_path"):
            missing.append("premium_path")
        pm = trade.get("premium_metrics")
        if not isinstance(pm, dict) or not pm:
            missing.append("premium_metrics")
        peak = trade.get("peak_quote_metadata")
        if not isinstance(peak, dict):
            missing.append("peak_quote_metadata")
    if path == "orb_so":
        if not trade.get("underlying_path") and trade.get("status") not in ("open", "shadow"):
            missing.append("underlying_path")
    status = str(trade.get("status") or "")
    if status in ("closed", "eod_open") and not trade.get("exit_observation"):
        if path in ("orb_0dte", "trendline_0dte"):
            missing.append("exit_observation")
    return len(missing) == 0, missing
