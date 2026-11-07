"""
Unified 0DTE entry delta band and lowest-delta preference (ORB1 Breakout + Trendline 0DTE).

Profit capture remains path-specific only via OPTION_STEALTH_* profiles; this module
covers contract selection at entry only.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional, Tuple

log = logging.getLogger(__name__)

PATH_ORB_0DTE = "ORB_0DTE"
PATH_TRENDLINE_0DTE = "TRENDLINE_0DTE"
_PENNY_SPOT_MAX_KEY = "0DTE_PENNY_NAME_SPOT_MAX"
_TRENDLINE_TOLERANCE_KEY = "TRENDLINE_DELTA_TOLERANCE"

# Canonical env keys (shared 0DTE entry policy).
_ENTRY_DELTA_MIN_KEY = "0DTE_ENTRY_DELTA_MIN"
_ENTRY_DELTA_MAX_KEY = "0DTE_ENTRY_DELTA_MAX"
_PREFER_LOWEST_KEY = "0DTE_ENTRY_PREFER_LOWEST_DELTA"
_LEGACY_MIN_KEY = "0DTE_SINGLE_LEG_DIRECTIONAL_DELTA_MIN"
_LEGACY_MAX_KEY = "0DTE_SINGLE_LEG_DIRECTIONAL_DELTA_MAX"
_TRENDLINE_MIN_KEY = "TRENDLINE_OPTION_DELTA_MIN"
_TRENDLINE_MAX_KEY = "TRENDLINE_OPTION_DELTA_MAX"
_PREMIUM_MIN_KEY = "0DTE_MIN_ENTRY_PREMIUM"
_LEGACY_PREMIUM_MIN_KEY = "0DTE_ENTRY_MIN_PREMIUM"
_PREMIUM_MAX_KEY = "0DTE_MAX_ENTRY_PREMIUM"
# Jul 28 desk lock: OTM ≤ 5.5% maximizes 18-trade book (7W/1L · +$472). Shared Layer D.
_ENTRY_OTM_PREFER_KEY = "0DTE_ENTRY_OTM_PCT_PREFER"
_ENTRY_OTM_MAX_KEY = "0DTE_ENTRY_OTM_PCT_MAX"
_DEFAULT_OTM_MAX = 0.055


def _float_env(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None or str(raw).strip() == "":
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


def _bool_env(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def entry_delta_min() -> float:
    """Allowed minimum absolute delta at entry (default 0.18 — near-ATM band)."""
    for key in (_ENTRY_DELTA_MIN_KEY, _LEGACY_MIN_KEY, _TRENDLINE_MIN_KEY):
        raw = os.getenv(key)
        if raw is not None and str(raw).strip() != "":
            try:
                return max(0.01, float(raw))
            except (TypeError, ValueError):
                pass
    return 0.18


def entry_delta_max() -> float:
    """Allowed maximum absolute delta at entry (default 0.42)."""
    for key in (_ENTRY_DELTA_MAX_KEY, _LEGACY_MAX_KEY, _TRENDLINE_MAX_KEY):
        raw = os.getenv(key)
        if raw is not None and str(raw).strip() != "":
            try:
                return min(0.95, float(raw))
            except (TypeError, ValueError):
                pass
    return 0.42


def entry_delta_bounds() -> Tuple[float, float]:
    dmin = entry_delta_min()
    dmax = entry_delta_max()
    if dmin > dmax:
        return dmin, dmin
    return dmin, dmax


def prefer_lowest_entry_delta() -> bool:
    """Jul 27+: default False — prefer near-ATM / higher δ (shared TL+BO)."""
    return _bool_env(_PREFER_LOWEST_KEY, False)


def directional_entry_premium_min() -> float:
    """Per-share floor for single-leg entries."""
    for key in (_PREMIUM_MIN_KEY, _LEGACY_PREMIUM_MIN_KEY, "0DTE_SINGLE_LEG_DIRECTIONAL_PREMIUM_MIN", "TRENDLINE_OPTION_MIN_PREMIUM"):
        raw = os.getenv(key)
        if raw is not None and str(raw).strip() != "":
            try:
                return max(0.03, float(raw))
            except (TypeError, ValueError):
                pass
    return 0.03


def directional_entry_premium_max() -> float:
    """Per-share cap for all 0DTE single-leg entries. 0 disables the cap."""
    return max(0.0, _float_env(_PREMIUM_MAX_KEY, 0.0))


def entry_otm_prefer_max() -> float:
    """Prefer band ceiling (≤ this ranks first). Default 5.5% — Jul 28 book lock."""
    return max(0.0, _float_env(_ENTRY_OTM_PREFER_KEY, _DEFAULT_OTM_MAX))


def entry_otm_research_max() -> float:
    """Hard reject above this OTM fraction. Default 5.5% (prefer==hard on lock)."""
    pref = entry_otm_prefer_max()
    hard = max(0.0, _float_env(_ENTRY_OTM_MAX_KEY, _DEFAULT_OTM_MAX))
    return max(pref, hard)


def entry_otm_bounds() -> Tuple[float, float]:
    """(prefer_max, research/hard_max) for unified OTM gate — all 0DTE paths."""
    return entry_otm_prefer_max(), entry_otm_research_max()


def otm_gate_enabled() -> bool:
    return _bool_env("0DTE_OTM_GATE_ENABLED", True)


def abs_contract_delta(contract: Any) -> float:
    try:
        raw = getattr(contract, "delta_est", None)
        if raw is None:
            raw = getattr(contract, "delta", 0.0)
        return abs(float(raw or 0.0))
    except (TypeError, ValueError):
        return 0.0


def contract_delta_in_entry_band(contract: Any) -> bool:
    d = abs_contract_delta(contract)
    dmin, dmax = entry_delta_bounds()
    return dmin - 1e-6 <= d <= dmax + 1e-6


def trendline_delta_tolerance() -> float:
    return max(0.0, _float_env(_TRENDLINE_TOLERANCE_KEY, 0.02))


def penny_name_spot_max() -> float:
    return max(1.0, _float_env(_PENNY_SPOT_MAX_KEY, 25.0))


def is_penny_name_spot(spot: float) -> bool:
    s = float(spot or 0.0)
    return s > 0.0 and s < penny_name_spot_max()


def effective_delta_max(path: str) -> float:
    _, dmax = entry_delta_bounds()
    path_u = str(path or PATH_ORB_0DTE).upper()
    if path_u.startswith("TRENDLINE"):
        return min(0.95, dmax + trendline_delta_tolerance())
    return dmax


def delta_band_label(path: str) -> str:
    dmin, dmax = entry_delta_bounds()
    eff_max = effective_delta_max(path)
    if eff_max > dmax + 1e-6:
        return f"{dmin:.2f}-{eff_max:.2f}"
    return f"{dmin:.2f}-{dmax:.2f}"


def single_leg_delta_passes_band(abs_delta: float, path: str) -> bool:
    dmin, _ = entry_delta_bounds()
    eff_max = effective_delta_max(path)
    return dmin - 1e-6 <= float(abs_delta) <= eff_max + 1e-6


def log_delta_band_audit(
    *,
    path: str,
    symbol: str,
    selected_delta: float,
    band: str,
    passes: bool,
    action: str,
) -> None:
    log.info(
        "DELTA_BAND_AUDIT | path=%s | symbol=%s | selected_delta=%.4f | band=%s | pass=%s | action=%s",
        str(path),
        str(symbol),
        float(selected_delta),
        str(band),
        str(bool(passes)).lower(),
        str(action),
    )


def validate_single_leg_before_execute(
    path: str,
    symbol: str,
    contract: Any,
) -> Tuple[bool, float, str]:
    """
    Final hard gate before single-leg execution (delta band + spread cap).
    Returns (passes, abs_delta, band_label).
    """
    from modules.spread_policy import entry_max_spread_points_for_strategy

    delta = abs_contract_delta(contract)
    band = delta_band_label(path)
    passes = single_leg_delta_passes_band(delta, path)
    max_spread_pts = float(entry_max_spread_points_for_strategy(path))
    spread_pts = float(getattr(contract, "bid_ask_spread_pct", 0.0) or 0.0)
    bid = float(getattr(contract, "bid", 0.0) or 0.0)
    ask = float(getattr(contract, "ask", 0.0) or 0.0)
    if bid > 0 and ask > 0:
        mid = (bid + ask) / 2.0
        if mid > 0:
            spread_pts = (ask - bid) / mid * 100.0
    if max_spread_pts > 0 and spread_pts > max_spread_pts + 1e-9:
        from modules.spread_policy import cheap_allowance_applies

        if not cheap_allowance_applies(bid, ask):
            passes = False
    action = "execute" if passes else "reject_before_execute"
    log_delta_band_audit(
        path=path,
        symbol=str(symbol or getattr(contract, "symbol", "UNKNOWN")),
        selected_delta=delta,
        band=band,
        passes=passes,
        action=action,
    )
    return passes, delta, band


def validate_trendline_delta_before_execute(symbol: str, abs_delta: float) -> Tuple[bool, str]:
    """Trendline path: allow configured tolerance above base max."""
    band = delta_band_label(PATH_TRENDLINE_0DTE)
    passes = single_leg_delta_passes_band(abs_delta, PATH_TRENDLINE_0DTE)
    action = "execute" if passes else "reject_before_execute"
    log_delta_band_audit(
        path=PATH_TRENDLINE_0DTE,
        symbol=str(symbol),
        selected_delta=float(abs_delta),
        band=band,
        passes=passes,
        action=action,
    )
    return passes, band


def delta_above_configured_band(abs_delta: float, path: str = PATH_ORB_0DTE) -> bool:
    """True when abs delta exceeds the base entry max (ignoring Trendline tolerance)."""
    _, dmax = entry_delta_bounds()
    return float(abs_delta) > dmax + 1e-6


def single_leg_sort_key(
    contract: Any,
    *,
    option_type: str,
    current_price: float,
    liquidity_score: float = 0.0,
) -> Tuple[float, ...]:
    """
    Sort key for single-leg candidates.
    Default (prefer_lowest=False): nearer ATM / higher δ first, then tighter spread.
    Legacy (prefer_lowest=True): lowest δ first.
    """
    delta = abs_contract_delta(contract)
    spread = float(getattr(contract, "bid_ask_spread_pct", 0.0) or 999.0)
    vol = -int(getattr(contract, "volume", 0) or 0)
    dist = abs(float(getattr(contract, "strike", 0.0) or 0.0) - float(current_price or 0.0))
    if prefer_lowest_entry_delta():
        return (delta, spread, vol, -float(liquidity_score))
    # Near-ATM / higher δ (shared with TL+BO unified select).
    return (dist, -delta, spread, vol, -float(liquidity_score))
