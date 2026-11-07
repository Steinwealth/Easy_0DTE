#!/usr/bin/env python3
"""
Path-owned spread caps — one limit per strategy path (no cross-path MIN).

Hierarchy (each path owns selection + broker entry gate):
  ORB1 Breakout       → 0DTE_SINGLE_LEG_DIRECTIONAL_MAX_SPREAD_PCT (default 8% points)
  Trendline 0DTE → TRENDLINE_OPTION_MAX_SPREAD_PCT (default 0.30 fraction = 30%)

Legacy LIVE_MAX_SPREAD_PCT is ignored unless explicitly set (deprecated global broker gate).
"""

from __future__ import annotations

from typing import Optional


def _cfg_float(key: str, default: float) -> float:
    try:
        from modules.config_loader import get_config_value

        raw = get_config_value(key, default, convert_type=False)
    except ImportError:
        import os

        raw = os.getenv(key, str(default))
    if raw is None:
        return default
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return default


def spread_fraction_from_env(raw: float) -> float:
    """Normalize env value to fraction (0.10 = 10%). Values > 1 treated as percent points."""
    if raw <= 1.0:
        return float(raw)
    return float(raw) / 100.0


def spread_points_from_env(raw: float) -> float:
    """Normalize env value to percent points for bid_ask_spread_pct comparisons (8.0 = 8%)."""
    if raw <= 1.0:
        return float(raw) * 100.0
    return float(raw)


def orb_chain_manager_spread_points() -> float:
    """ORB chain manager filter. Env: 0DTE_MAX_BID_ASK_SPREAD_PCT (canonical)."""
    legacy = _cfg_float("0DTE_OPTIONS_MAX_BID_ASK_SPREAD_PCT", -1.0)
    if legacy >= 0:
        return spread_points_from_env(legacy)
    return spread_points_from_env(_cfg_float("0DTE_MAX_BID_ASK_SPREAD_PCT", 8.0))


def orb_entry_max_spread_points() -> float:
    """ORB1 selection + entry gate (% points, default 8.0)."""
    return path_spread_cap_points("orb1")


def orb_entry_max_spread_fraction() -> float:
    return orb_entry_max_spread_points() / 100.0


def trendline_path_spread_fraction() -> float:
    """Trendline path cap. Env: TRENDLINE_OPTION_MAX_SPREAD_PCT (default 0.30)."""
    return spread_fraction_from_env(_cfg_float("TRENDLINE_OPTION_MAX_SPREAD_PCT", 0.30))


def trendline_entry_max_spread_fraction(
    *,
    relax_spread_check: bool = False,
    path_cap_fraction: Optional[float] = None,
) -> float:
    """Trendline selection + entry gate (fraction). Per-path cap, default 30%."""
    _ = (relax_spread_check, path_cap_fraction)
    return path_spread_cap_points("trendline") / 100.0


def trendline_entry_max_spread_points(
    *,
    relax_spread_check: bool = False,
    path_cap_fraction: Optional[float] = None,
) -> float:
    frac = trendline_entry_max_spread_fraction(
        relax_spread_check=relax_spread_check,
        path_cap_fraction=path_cap_fraction,
    )
    return frac * 100.0


def trendline_pick_spread_cap_fraction(
    symbol: str = "",
    *,
    relax_spread_check: bool = False,
    path_cap_fraction: Optional[float] = None,
) -> float:
    """Alias for Trendline contract-pick cap (symbol retained for call-site compatibility)."""
    _ = symbol
    return trendline_entry_max_spread_fraction(
        relax_spread_check=relax_spread_check,
        path_cap_fraction=path_cap_fraction,
    )


def _normalize_strategy_type(strategy_type: str) -> str:
    return str(strategy_type or "").strip().lower()


def is_trendline_strategy(strategy_type: str) -> bool:
    return "trendline" in _normalize_strategy_type(strategy_type)


def entry_max_spread_points_for_strategy(strategy_type: str) -> float:
    """Broker entry market-quality cap for the given path."""
    return path_spread_cap_points(strategy_type)


def entry_max_spread_fraction_for_strategy(strategy_type: str) -> float:
    return entry_max_spread_points_for_strategy(strategy_type) / 100.0


# --- Deprecated: global broker gate (do not use for new code) ---


def live_entry_max_spread_fraction() -> float:
    """Deprecated global gate → ORB path cap. LIVE_MAX_SPREAD_PCT only if explicitly set."""
    legacy = _cfg_float("LIVE_MAX_SPREAD_PCT", -1.0)
    if legacy >= 0:
        return spread_fraction_from_env(legacy)
    return orb_entry_max_spread_fraction()


def live_entry_max_spread_points() -> float:
    legacy = _cfg_float("LIVE_MAX_SPREAD_PCT", -1.0)
    if legacy >= 0:
        return spread_points_from_env(legacy)
    return orb_entry_max_spread_points()


# One spread rule. Unit: percent points of mid, (ask - bid) / ((ask + bid) / 2) * 100.
# Per path: 0DTE_MAX_SPREAD_PCT_ORB1 (8) / _BREAKOUT (30) / _TRENDLINE (30).
# A quote whose dollar spread is <= 0DTE_SPREAD_CHEAP_MAX_USD (default 0.01)
# passes regardless of %, so one-tick cheap contracts are not blocked.
# Enforced in the shared picker and re-checked at submit.


def spread_path_key(path: str) -> str:
    p = _normalize_strategy_type(path)
    if "trendline" in p or p in ("tl", "tl0", "tl1", "tl2", "tl3", "tl4"):
        return "TRENDLINE"
    if "breakout" in p or p in ("bo", "orb2"):
        return "BREAKOUT"
    return "ORB1"


_PATH_SPREAD_DEFAULTS = {"ORB1": 8.0, "BREAKOUT": 30.0, "TRENDLINE": 30.0}


def path_spread_cap_points(path: str) -> float:
    """Per path (Eric 10/13): ORB1 8 · Breakout 30 · Trendline 30 (% of mid).

    Path key wins; else the path default. ``0DTE_MAX_SPREAD_PCT`` is not a
    cross-path override (it would clamp BO/TL to 8)."""
    key = spread_path_key(path)
    return spread_points_from_env(_cfg_float(f"0DTE_MAX_SPREAD_PCT_{key}", _PATH_SPREAD_DEFAULTS[key]))


def cheap_spread_max_usd() -> float:
    return max(0.0, _cfg_float("0DTE_SPREAD_CHEAP_MAX_USD", 0.01))


def cheap_allowance_applies(bid: float, ask: float) -> bool:
    """True when the dollar spread is one tick or less (<= allowance)."""
    try:
        b, a = float(bid or 0.0), float(ask or 0.0)
    except (TypeError, ValueError):
        return False
    if b <= 0 or a <= 0 or a < b:
        return False
    return (a - b) <= cheap_spread_max_usd() + 1e-9


def spread_check(bid: float, ask: float, path: str) -> dict:
    """{ok, spread_pct (points of mid), spread_usd, cap_pct, cheap_allow_usd, rule}."""
    cap = path_spread_cap_points(path)
    allow = cheap_spread_max_usd()
    out = {"ok": False, "spread_pct": None, "spread_usd": None, "cap_pct": cap,
           "cheap_allow_usd": allow, "rule": "no_quote", "path_key": spread_path_key(path)}
    try:
        b, a = float(bid or 0.0), float(ask or 0.0)
    except (TypeError, ValueError):
        return out
    if b <= 0 or a <= 0 or a < b:
        return out
    mid = (a + b) / 2.0
    usd = a - b
    sp = usd / mid * 100.0
    out.update(spread_pct=sp, spread_usd=usd)
    if sp <= cap + 1e-9:
        out.update(ok=True, rule="pct")
    elif usd <= allow + 1e-9:
        out.update(ok=True, rule="cheap_usd")
    else:
        out.update(rule="spread_too_wide")
    return out
