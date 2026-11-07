"""Strict exit-grade source policy for 0DTE profit-capture exits.

Normal exit-grade (may update bid HWM / arm MFE / trigger BE / giveback):
  1. Fresh exact-contract E*TRADE quote with valid matching bid (incl. 0.00)
  2. Fresh force-refreshed matching chain-contract bid (bridge only)

Everything else is rejected for normal profit-capture state.

OPTION_SINGLE_EXIT_GRADE_BYPASS (legacy) may only escalate
timeout/no_progress safety exits to emergency_risk_grade — never HWM/MFE/BE.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, Optional, Tuple


def strict_exit_grade_enabled() -> bool:
    raw = os.getenv("0DTE_STRICT_EXIT_GRADE_ENABLED", "true")
    return str(raw or "true").strip().lower() in ("1", "true", "yes", "on")


def _dec_bid(raw_bid: Any) -> Optional[Decimal]:
    """Valid live bid of 0.00 is not missing."""
    if raw_bid is None:
        return None
    try:
        return Decimal(str(raw_bid))
    except Exception:
        return None


@dataclass
class ExitGradeVerdict:
    premium_source: str = ""
    source_grade: str = "rejected"  # normal_exit_grade | emergency_risk_grade | analytics_only | rejected
    exit_grade: bool = False
    emergency_grade: bool = False
    quote_rejection_reason: str = ""
    quote_age_ms: Optional[float] = None
    broker_quote_timestamp: Optional[str] = None
    local_response_timestamp: Optional[float] = None
    bid: Optional[float] = None
    extras: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "premium_source": self.premium_source,
            "source_grade": self.source_grade,
            "exit_grade": self.exit_grade,
            "emergency_grade": self.emergency_grade,
            "quote_rejection_reason": self.quote_rejection_reason,
            "quote_age_ms": self.quote_age_ms,
            "broker_quote_timestamp": self.broker_quote_timestamp,
            "local_response_timestamp": self.local_response_timestamp,
            "bid": self.bid,
            **self.extras,
        }


_NORMAL_ALLOWED = frozenset(
    {
        "exact_quote",
        "exact_contract",
        "fresh_chain_refresh",
        "fresh_chain_force_refresh",  # legacy
        "fresh_chain_bridge",
        "bid_executable",
    }
)

_PROHIBITED_TOKENS = (
    "synthetic",
    "theoretical",
    "modeled",
    "proxy",
    "underlying",
    "mark_only",
    "mark-only",
    "midpoint",
    "mid_only",
    "last_only",
    "last-trade",
    "stale",
    "cached",
    "cache",
    "parse_fallback",
    "unmatched",
    "wrong_",
    "missing_bid",
    "nearest",
    "chain_strike",
    "unverified",
)


def classify_exit_grade_source(
    *,
    premium_source: str,
    raw_bid: Any = None,
    quote_age_sec: Optional[float] = None,
    max_age_sec: Optional[float] = None,
    contract_matched: bool = True,
    skip_cache_bridge: bool = False,
    wrong_expiration: bool = False,
    wrong_strike: bool = False,
    wrong_cp: bool = False,
    broker_quote_timestamp: Optional[str] = None,
    soft_stale_ok: bool = False,
) -> ExitGradeVerdict:
    """Canonical resolver: may this snapshot control a normal exit?"""
    src = str(premium_source or "").strip().lower()
    now = time.time()
    bid_d = _dec_bid(raw_bid)
    bid_f = float(bid_d) if bid_d is not None else None
    age_ms = None if quote_age_sec is None else float(quote_age_sec) * 1000.0
    max_age = max_age_sec
    if max_age is None:
        try:
            max_age = float(os.getenv("0DTE_EXIT_QUOTE_MAX_AGE_SEC", "8") or 8)
        except (TypeError, ValueError):
            max_age = 3.0

    v = ExitGradeVerdict(
        premium_source=src,
        quote_age_ms=age_ms,
        broker_quote_timestamp=broker_quote_timestamp,
        local_response_timestamp=now,
        bid=bid_f,
    )

    if not strict_exit_grade_enabled():
        # Soft path still bans synthetics when allow_synthetic is false.
        from modules.dte0_exit_monitor_config import exit_grade_source_allowed

        ok = exit_grade_source_allowed(src) and bid_d is not None
        v.exit_grade = bool(ok)
        v.source_grade = "normal_exit_grade" if ok else "rejected"
        if not ok:
            v.quote_rejection_reason = "legacy_exit_grade_source_allowed_false"
        return v

    if wrong_expiration:
        v.quote_rejection_reason = "wrong_expiration"
        return v
    if wrong_strike:
        v.quote_rejection_reason = "wrong_strike"
        return v
    if wrong_cp:
        v.quote_rejection_reason = "wrong_call_put_side"
        return v
    if not contract_matched:
        v.quote_rejection_reason = "unmatched_option_symbol"
        return v
    if bid_d is None:
        v.quote_rejection_reason = "missing_bid"
        return v
    if quote_age_sec is not None and float(quote_age_sec) > float(max_age) + 1e-9:
        # Selected-ED / weekly: keep exit-grade when a usable bid is present.
        if not (soft_stale_ok and bid_d is not None):
            v.quote_rejection_reason = "stale_quote"
            v.source_grade = "rejected"
            return v

    # Prohibited tokens (cached chain without fresh bridge, synthetic, etc.)
    if any(tok in src for tok in _PROHIBITED_TOKENS):
        # Allow only explicit fresh bridge token even if it contains "chain"
        if "fresh_chain_refresh" not in src and "fresh_chain_force_refresh" not in src and "fresh_chain_bridge" not in src:
            if "cache" in src or "cached" in src:
                v.quote_rejection_reason = "cached_option_chain"
                return v
            if "synthetic" in src:
                v.quote_rejection_reason = "synthetic_option_quote"
                return v
            if "theoretical" in src or "modeled" in src:
                v.quote_rejection_reason = "theoretical_premium"
                return v
            if "underlying" in src:
                v.quote_rejection_reason = "underlying_derived_premium"
                return v
            if "mark" in src and "bid" not in src:
                v.quote_rejection_reason = "mark_only_value"
                return v
            if "mid" in src and "bid" not in src:
                v.quote_rejection_reason = "midpoint_only_value"
                return v
            if "last" in src and "bid" not in src:
                v.quote_rejection_reason = "last_trade_only_value"
                return v
            v.quote_rejection_reason = f"prohibited_source:{src}"
            return v

    # Bridge must be skip_cache fresh
    if "fresh_chain" in src or "force_refresh" in src:
        if not skip_cache_bridge and "force_refresh" in src:
            # Caller must assert skip_cache; if unknown, still allow when token is explicit
            pass
        v.exit_grade = True
        v.source_grade = "normal_exit_grade"
        return v

    try:
        from modules.exit_grade_durability import quote_source_is_exact_contract

        _exact_ok = quote_source_is_exact_contract(src)
    except Exception:
        _exact_ok = src.startswith("exact_quote") or src.startswith("exact_contract")
    if src in _NORMAL_ALLOWED or _exact_ok:
        v.exit_grade = True
        v.source_grade = "normal_exit_grade"
        return v

    # live_chain_monitor without fresh/force is analytics-only under strict mode
    if src in ("live_chain_monitor", "nearest_exchange_strike"):
        v.source_grade = "analytics_only"
        v.quote_rejection_reason = "not_exact_or_fresh_bridge"
        return v

    v.quote_rejection_reason = f"unknown_source:{src or 'empty'}"
    return v


def may_update_executable_hwm(verdict: ExitGradeVerdict) -> bool:
    return bool(verdict.exit_grade and verdict.source_grade == "normal_exit_grade")


def may_trigger_normal_profit_exit(verdict: ExitGradeVerdict) -> bool:
    return may_update_executable_hwm(verdict)


def emergency_bypass_allowed_for_timeout() -> bool:
    """Legacy EXIT_GRADE_BYPASS — emergency path only (timeout / no_progress)."""
    raw = os.getenv("OPTION_SINGLE_EXIT_GRADE_BYPASS")
    if raw is None or str(raw).strip() == "":
        raw = os.getenv("OPTION_STEALTH_0DTE_SINGLE_EXIT_GRADE_BYPASS", "true")
    return str(raw or "").strip().lower() in ("1", "true", "yes", "on")


def mark_emergency_risk_grade(verdict: ExitGradeVerdict, *, reason: str) -> ExitGradeVerdict:
    verdict.emergency_grade = True
    verdict.source_grade = "emergency_risk_grade"
    verdict.extras["emergency_reason"] = reason
    return verdict


# Back-compat shim used by dte0_exit_monitor_config
def exit_grade_source_allowed_strict(source: str) -> bool:
    return classify_exit_grade_source(premium_source=source, raw_bid=0.01).exit_grade
