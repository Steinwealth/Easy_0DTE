#!/usr/bin/env python3
"""Peak quote metadata and trusted-source classification (read-only)."""

from __future__ import annotations

from typing import Any, Dict, Optional

TRUSTED_PEAK_PREMIUM_SOURCES = frozenset(
    {
        "exact_quote",
        "exit_grade",
        "option_mark",
        "option_mid",
        "spread_mtm_mid_last",
        "live_chain_monitor",
        "fresh_chain_refresh",
        "fresh_chain_force_refresh",  # legacy
        "fresh_chain_bridge",
        "chain_strike_match",
    }
)

TRUSTED_SHADOW_PREMIUM_SOURCES = frozenset(
    {
        "exact_quote",
        "exit_grade",
        "live_chain_monitor",
        "fresh_chain_refresh",
        "fresh_chain_force_refresh",  # legacy
        "fresh_chain_bridge",
    }
)

TRUSTED_PEAK_TELEMETRY = frozenset({"live"})

TRUSTED_MARK_QUALITIES = frozenset(
    {
        "live_single_leg",
        "live_two_leg_spread",
        "exit_grade",
    }
)


def is_trusted_peak_quote(
    *,
    premium_source: str,
    quote_quality: str = "",
    mark_quality: str = "",
    price_source_telemetry: str = "",
    mark_is_exit_grade: bool = False,
) -> bool:
    src = str(premium_source or "").strip().lower()
    mq = str(mark_quality or quote_quality or "").strip().lower()
    if "cached" in mq or "synthetic" in mq or "partial" in mq:
        return False
    if src in ("nearest", "option_last", "cached_quote", "cached_recent"):
        return False
    if mark_is_exit_grade and src in TRUSTED_PEAK_PREMIUM_SOURCES:
        return True
    if src in TRUSTED_PEAK_PREMIUM_SOURCES:
        return True
    telem = str(price_source_telemetry or "").strip().lower()
    if telem in TRUSTED_PEAK_TELEMETRY and src in TRUSTED_PEAK_PREMIUM_SOURCES:
        return True
    if mq in TRUSTED_MARK_QUALITIES and src in TRUSTED_PEAK_PREMIUM_SOURCES:
        return True
    return False


def is_trusted_shadow_peak_quote(
    *,
    premium_source: str,
    quote_quality: str = "",
    mark_quality: str = "",
    mark_is_exit_grade: bool = False,
    shadow_quote_exact: bool = False,
    path_phase: str = "",
    shadow_trust: str = "",
) -> bool:
    if str(path_phase or "").lower() != "shadow":
        return is_trusted_peak_quote(
            premium_source=premium_source,
            quote_quality=quote_quality,
            mark_quality=mark_quality,
            mark_is_exit_grade=mark_is_exit_grade,
        )
    trust = str(shadow_trust or "").strip().lower()
    src = str(premium_source or "").strip().lower()
    mq = str(mark_quality or quote_quality or "").strip().lower()
    if "cached" in mq or "synthetic" in mq or "partial" in mq:
        return False
    if trust == "trusted_exact" or shadow_quote_exact:
        try:
            from modules.exit_grade_durability import (
                CANONICAL_EXACT_CONTRACT_SOURCE,
                quote_source_is_exact_contract,
            )

            return (
                src in TRUSTED_SHADOW_PREMIUM_SOURCES
                or quote_source_is_exact_contract(src)
                or src == CANONICAL_EXACT_CONTRACT_SOURCE
            )
        except Exception:
            return src in TRUSTED_SHADOW_PREMIUM_SOURCES or "exact_contract" in src
    if trust in ("trusted_chain", "degraded_chain"):
        # Chain peaks are retained for research; degraded stays False for HWM trust.
        return trust == "trusted_chain" and src in TRUSTED_SHADOW_PREMIUM_SOURCES
    if not shadow_quote_exact:
        return False
    if src not in TRUSTED_SHADOW_PREMIUM_SOURCES:
        return False
    return True


def build_peak_quote_metadata(
    *,
    peak_time: str,
    peak_premium: float,
    peak_underlying_price: Optional[float] = None,
    peak_bid: Optional[float] = None,
    peak_ask: Optional[float] = None,
    peak_mid: Optional[float] = None,
    peak_spread_pct: Optional[float] = None,
    peak_quote_age_seconds: Optional[float] = None,
    peak_quote_quality: str = "",
    peak_premium_source: str = "",
    peak_mark_quality: str = "",
    peak_is_exit_grade: bool = False,
    price_source_telemetry: str = "",
) -> Dict[str, Any]:
    trusted = is_trusted_peak_quote(
        premium_source=peak_premium_source,
        quote_quality=peak_quote_quality,
        mark_quality=peak_mark_quality,
        price_source_telemetry=price_source_telemetry,
        mark_is_exit_grade=peak_is_exit_grade,
    )
    return {
        "peak_time": peak_time,
        "peak_premium": peak_premium,
        "peak_underlying_price": peak_underlying_price,
        "peak_bid": peak_bid,
        "peak_ask": peak_ask,
        "peak_mid": peak_mid,
        "peak_spread_pct": peak_spread_pct,
        "peak_quote_age_seconds": peak_quote_age_seconds,
        "peak_quote_quality": peak_quote_quality,
        "peak_premium_source": peak_premium_source,
        "peak_mark_quality": peak_mark_quality,
        "peak_is_exit_grade": bool(peak_is_exit_grade),
        "peak_trusted": trusted,
    }
