"""0DTE monitor premium-source bucketing (health / kill-switch ratios).

Shared by ORB1 Breakout and Trendline 0DTE post-fill monitors. Exit-grade durable
quotes must not inflate modeled_proxy / missing_or_invalid.

Import: ``modules.unified_0dte_premium_bucket`` (file cannot be ``0dte_*`` — leading digit).
Public API: ``classify_0dte_monitor_premium_bucket``.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def _positive_quote(value: Any) -> Optional[float]:
    try:
        v = float(value)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def classify_0dte_monitor_premium_bucket(
    *,
    source: str,
    osnap: Dict[str, Any],
    oq_orb: Dict[str, Any],
    durability: Optional[Dict[str, Any]] = None,
    trusted_quote_hold: bool = False,
) -> str:
    """
    Map a monitor tick to premium_sources bucket keys used by degraded_ratio.

    Healthy: mark_or_mid, last
    Degraded: modeled_proxy, current_position_value_fallback, missing_or_invalid
    """
    if trusted_quote_hold:
        return "mark_or_mid"

    osnap = osnap if isinstance(osnap, dict) else {}
    oq_orb = oq_orb if isinstance(oq_orb, dict) else {}
    dur = durability if isinstance(durability, dict) else {}

    mark_exit = bool(osnap.get("mark_is_exit_grade"))
    good_ticks = int(osnap.get("good_mark_tick_count") or 0)
    min_ticks = 3
    try:
        from modules.config_loader import get_config_value

        min_ticks = int(get_config_value("ORB_0DTE_EXIT_GRADE_MIN_GOOD_TICKS", "3") or 3)
    except Exception:
        pass
    mark_quality = str(osnap.get("mark_quality") or "").strip().lower()
    live_mark = mark_quality in ("live_single_leg", "live_two_leg", "live_chain_monitor", "")
    exit_grade_live = bool(
        (mark_exit and good_ticks >= min_ticks)
        or dur.get("exit_grade_durable_confirmed")
        or (live_mark and mark_exit)
    )
    if exit_grade_live:
        return "mark_or_mid"

    source = str(source or "").strip()
    try:
        from modules.exit_grade_durability import (
            canonicalize_quote_source,
            premium_source_is_live_contract_quote,
            quote_source_is_exact_contract,
        )

        source = canonicalize_quote_source(source)
        source_l = source.lower()
        live_contract = bool(
            premium_source_is_live_contract_quote(source_l)
            or quote_source_is_exact_contract(source_l)
        )
    except Exception:
        source_l = source.lower()
        live_contract = source_l in (
            "exact_quote",
            "nearest",
            "cached_quote",
            "chain_strike_match",
            "fresh_chain_refresh",
            "fresh_chain_force_refresh",  # legacy
            "live_chain_monitor",
            "bid_executable",
        ) or source_l.startswith("exact_contract")
    if live_contract:
        bid = _positive_quote(oq_orb.get("bid"))
        ask = _positive_quote(oq_orb.get("ask"))
        if bid is not None:
            return "mark_or_mid"
        if bid is not None and ask is not None:
            return "mark_or_mid"
        if exit_grade_live or mark_exit:
            return "mark_or_mid"
    if source in ("option_mark", "option_mid", "spread_mtm_mid_last"):
        return "mark_or_mid"
    if source == "option_last":
        return "last"
    if source == "modeled_proxy":
        return "modeled_proxy"
    if source in (
        "spread_mtm_incomplete_quotes",
        "spread_mtm_no_legs",
        "spread_mtm_missing_leg_role",
    ):
        return "missing_or_invalid"
    if source and ("position" in source or "current_value" in source):
        if exit_grade_live or mark_exit:
            return "mark_or_mid"
        return "current_position_value_fallback"
    if oq_orb.get("has_position_value"):
        if exit_grade_live or mark_exit:
            return "mark_or_mid"
        return "current_position_value_fallback"
    if exit_grade_live:
        return "mark_or_mid"
    return "missing_or_invalid"


# Backward-compatible alias (ORB-named call sites / tests).
classify_orb_monitor_premium_bucket = classify_0dte_monitor_premium_bucket
