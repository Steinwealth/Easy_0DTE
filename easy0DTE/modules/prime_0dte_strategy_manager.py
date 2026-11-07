#!/usr/bin/env python3
"""
Prime 0DTE Strategy Manager
===========================

Listens to ORB signals and applies the Quality Gate (ORB admit_pre_exec shim)
to determine which signals deserve options exposure. Manages 0DTE options trading.

Key Responsibilities:
1. Listen to ORB signals from PrimeORBStrategyManager
2. Apply Quality Gate (ConvexEligibilityFilter → admit_pre_exec)
3. Generate 0DTE signals for QQQ & SPY
4. Coordinate with Options Trading Executor

Author: Easy ORB Strategy Development Team
Last Updated: January 6, 2026 (Rev 00231)
Version: 2.31.0
"""

import asyncio
import logging
import math
import os
from datetime import datetime, time, timedelta
from typing import Dict, List, Optional, Any, Callable, Tuple, Set
from dataclasses import dataclass, field
from enum import Enum
import pytz

from .convex_eligibility_filter import ConvexEligibilityFilter, ConvexEligibilityResult
from .options_priority_data_collector import OptionsPriorityDataCollector
from modules.execution_intent import ExecutionIntent

log = logging.getLogger(__name__)


def _float_env(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _bool_env(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _parse_et_hhmm(env_key: str, default: str) -> Tuple[int, int]:
    """Parse HH:MM (24h) Eastern time window boundary from env."""
    raw = str(os.getenv(env_key, default) or default).strip()
    parts = raw.split(":")
    try:
        h = int(parts[0])
        m = int(parts[1]) if len(parts) > 1 else 0
        return (h, m)
    except (TypeError, ValueError):
        dparts = default.split(":")
        return (int(dparts[0]), int(dparts[1]) if len(dparts) > 1 else 0)


def _parse_csv_floats(env_key: str, default_csv: str) -> List[float]:
    """Parse comma-separated floats; empty or invalid tokens skipped. Uses default_csv if result empty."""
    raw = os.getenv(env_key)
    src = str(raw).strip() if raw is not None and str(raw).strip() != "" else default_csv

    def _parse_line(line: str) -> List[float]:
        out: List[float] = []
        for part in line.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                out.append(float(part))
            except (TypeError, ValueError):
                continue
        return out

    parsed = _parse_line(src)
    if not parsed:
        parsed = _parse_line(default_csv)
    return parsed


def _priority_subscore_from_desc_bounds(value: float, bounds_key: str, scores_key: str, default_bounds: str, default_scores: str) -> float:
    """
    Subscore when value is compared to descending bounds (e.g. breakout % or ORB range %).
    scores[i] applies when value >= bounds_sorted_desc[i]; else scores[-1].
    """
    bounds = sorted(_parse_csv_floats(bounds_key, default_bounds), reverse=True)
    scores = _parse_csv_floats(scores_key, default_scores)
    if len(scores) != len(bounds) + 1:
        bounds = sorted([float(x.strip()) for x in default_bounds.split(",") if x.strip()], reverse=True)
        scores = [float(x.strip()) for x in default_scores.split(",") if x.strip()]
    for i, b in enumerate(bounds):
        if value >= b:
            return scores[i]
    return scores[-1]


def _0dte_breakout_continuation_score(breakout_pct: float) -> float:
    """
    Peak continuation rank at ~1.5–2.5% above ORB (DAL #1 May 20), not ultra-tight 0.1% (NOW-class).
    """
    brk = max(0.0, float(breakout_pct or 0.0))
    tiny_max = _float_env("0DTE_PRIORITY_BREAKOUT_TINY_MAX", 0.35)
    peak_lo = _float_env("0DTE_PRIORITY_BREAKOUT_PEAK_LOW", 1.2)
    peak_hi = _float_env("0DTE_PRIORITY_BREAKOUT_PEAK_HIGH", 2.8)
    if brk < tiny_max:
        return 0.52 + (brk / max(tiny_max, 1e-9)) * 0.22
    if brk <= peak_hi:
        span = max(peak_hi - peak_lo, 1e-9)
        if brk < peak_lo:
            return 0.74 + ((brk - tiny_max) / max(peak_lo - tiny_max, 1e-9)) * 0.22
        return 0.96 + min(1.0, (brk - peak_lo) / span) * 0.04
    tail = _float_env("0DTE_PRIORITY_BREAKOUT_TAIL_DECAY", 0.07)
    return max(0.55, 1.0 - (brk - peak_hi) * tail)


def _0dte_vwap_continuation_score(vwap_distance: float) -> float:
    """
    SO-aligned VWAP continuation curve (May 20): moderate separation above VWAP ranks best;
    extreme extension is not automatic quality.
    """
    vd = float(vwap_distance or 0.0)
    chase_pct = _float_env("0DTE_PRIORITY_LONG_VWAP_CHASE_PCT", 35.0)
    extreme_pct = _float_env("0DTE_PRIORITY_LONG_VWAP_EXTREME_PCT", 50.0)
    if vd <= -10.0:
        return 0.38
    if vd < 0.0:
        return 0.45 + 0.025 * ((vd + 10.0) / 10.0)
    if vd <= 10.0:
        return 0.58 + 0.038 * vd
    if vd <= 22.0:
        return 0.96 - 0.020 * (vd - 10.0)
    if vd <= chase_pct:
        return max(0.38, 0.74 - 0.016 * (vd - 22.0))
    if vd <= extreme_pct:
        return max(0.22, 0.38 - 0.006 * (vd - chase_pct))
    return 0.22


def _0dte_priority_execution_penalty(
    direction: str,
    vwap_distance: float,
    macd_histogram: float,
    volume_ratio: float,
    breakout_pct: float,
    so_align: Optional[float] = None,
) -> float:
    """
    Multiplicative penalties for May 20 loser patterns (NET/QCOM/NVDA/PLTR) while keeping
    DAL/IONQ/CRWD/CRDO/OKLO/NOW winners in the top ranks.
    """
    pen = 1.0
    vd = float(vwap_distance or 0.0)
    macd = float(macd_histogram or 0.0)
    volr = float(volume_ratio or 0.0)
    brk = max(0.0, float(breakout_pct or 0.0))
    if direction != "LONG":
        return pen

    if vd > 25.0 and brk >= 1.0:
        # Jun 1 SNOW: SO vwap_extreme_momentum_winner — do not apply 0.45× when SO align + MACD confirm.
        so_relief_align = _float_env("0DTE_PRIORITY_SO_VWAP_RELIEF_ALIGN_MIN", 0.75)
        so_relief_macd = _float_env("0DTE_PRIORITY_SO_VWAP_RELIEF_MACD_MIN", 0.0)
        if (
            so_align is not None
            and float(so_align) >= so_relief_align
            and macd > so_relief_macd
        ):
            pen *= _float_env("0DTE_PRIORITY_PEN_VWAP_EXTREME_SO_RELIEF_MULT", 1.0)
        else:
            pen *= _float_env("0DTE_PRIORITY_PEN_VWAP_EXTREME_BREAK_HIGH", 0.45)
    elif vd > 25.0 and brk < 1.0 and macd > _float_env("0DTE_PRIORITY_CRWD_MACD_MIN", 0.5):
        pen *= _float_env("0DTE_PRIORITY_PEN_VWAP_EXTREME_TIGHT_MACD", 0.90)
    elif vd > 25.0 and brk < 1.0 and volr >= _float_env("0DTE_PRIORITY_HIGH_VOL_TIGHT_BRK_MIN", 0.38):
        if so_align is not None and float(so_align) >= _float_env("0DTE_PRIORITY_CRDO_SO_ALIGN_MIN", 0.82):
            pen *= _float_env("0DTE_PRIORITY_BOOST_CRDO_HIGH_VOL", 1.12)
        else:
            pen *= _float_env("0DTE_PRIORITY_PEN_VWAP_EXTREME_HIGH_VOL", 0.86)
    elif vd > 25.0:
        pen *= _float_env("0DTE_PRIORITY_PEN_VWAP_EXTREME_TIGHT", 0.68)
    elif vd > 20.0 and brk >= 1.0:
        pen *= _float_env("0DTE_PRIORITY_PEN_VWAP_HIGH_BREAK", 0.55)
    elif vd > 15.0 and brk >= 1.0 and macd <= 0.0:
        pen *= _float_env("0DTE_PRIORITY_PEN_VWAP_CHASE_NEG_MACD", 0.62)
    elif vd > 15.0 and brk < 1.0 and abs(macd) < _float_env("0DTE_PRIORITY_STALE_MACD_ABS_MAX", 0.3):
        pen *= _float_env("0DTE_PRIORITY_PEN_VWAP_STALE", 0.58)
    elif vd > 15.0:
        pen *= _float_env("0DTE_PRIORITY_PEN_VWAP_MODERATE", 0.78)

    weak_vol = _float_env("0DTE_PRIORITY_WEAK_VOL_MAX", 0.18)
    weak_brk = _float_env("0DTE_PRIORITY_WEAK_BREAKOUT_MAX", 0.45)
    if volr < weak_vol and brk < weak_brk:
        pen *= _float_env("0DTE_PRIORITY_PEN_WEAK_TAPE", 0.48)

    below_vwap = _float_env("0DTE_PRIORITY_BELOW_VWAP_PEN_THRESHOLD", -8.0)
    if vd < below_vwap and macd <= _float_env("0DTE_PRIORITY_BELOW_VWAP_MACD_MAX", 0.2) and brk < 1.0:
        pen *= _float_env("0DTE_PRIORITY_PEN_BELOW_VWAP_WEAK", 0.52)
    # VST-class: below VWAP + bearish MACD + weak SO should not outrank CRDO/CRWD
    if (
        vd < _float_env("0DTE_PRIORITY_BELOW_VWAP_DEEP", -10.0)
        and macd < _float_env("0DTE_PRIORITY_BELOW_VWAP_MACD_DEEP", -0.5)
        and so_align is not None
        and float(so_align) < _float_env("0DTE_PRIORITY_BELOW_VWAP_SO_MAX", 0.65)
    ):
        pen *= _float_env("0DTE_PRIORITY_PEN_BELOW_VWAP_WEAK_SO", 0.48)

    recovery_vd = _float_env("0DTE_PRIORITY_RECOVERY_VWAP_MAX", -25.0)
    so_floor = _float_env("0DTE_PRIORITY_RECOVERY_SO_ALIGN_MIN", 0.75)
    if (
        vd < recovery_vd
        and volr >= _float_env("0DTE_PRIORITY_RECOVERY_VOL_MIN", 0.22)
        and brk >= _float_env("0DTE_PRIORITY_RECOVERY_BREAKOUT_MIN", 1.5)
        and so_align is not None
        and float(so_align) >= so_floor
    ):
        pen *= _float_env("0DTE_PRIORITY_BOOST_GAP_RECOVERY", 1.06)

    sweet_vwap = _float_env("0DTE_PRIORITY_SWEET_VWAP_MAX", 12.0)
    sweet_brk_lo = _float_env("0DTE_PRIORITY_SWEET_BREAKOUT_MIN", 1.2)
    sweet_brk_hi = _float_env("0DTE_PRIORITY_SWEET_BREAKOUT_MAX", 3.2)
    if (
        0.0 < vd <= sweet_vwap
        and sweet_brk_lo <= brk <= sweet_brk_hi
        and volr >= _float_env("0DTE_PRIORITY_SWEET_VOL_MIN", 0.18)
    ):
        pen *= _float_env("0DTE_PRIORITY_BOOST_CONTINUATION_SWEET", 1.14)

    # Desk #1 pattern: top SO continuation score + sweet VWAP/break band (DAL / IONQ)
    so_norm = _float_env("0DTE_PRIORITY_SO_SCORE_NORM", 0.22)
    if (
        volr >= _float_env("0DTE_PRIORITY_DESK_TOP_VOL_MIN", 0.20)
        and sweet_brk_lo <= brk <= sweet_brk_hi
        and 0.0 < vd <= sweet_vwap
    ):
        pen *= _float_env("0DTE_PRIORITY_BOOST_DESK_CONTINUATION", 1.08)

    # Ultra-tight break without SO backing (NOW-class): do not outrank DAL
    if brk < _float_env("0DTE_PRIORITY_ULTRA_TIGHT_BREAK_MAX", 0.45):
        pen *= _float_env("0DTE_PRIORITY_PEN_ULTRA_TIGHT_BREAK", 0.88)

    # CRWD-class: tight break + bullish MACD + real volume
    if (
        brk < _float_env("0DTE_PRIORITY_CRWD_BREAKOUT_MAX", 1.2)
        and macd > _float_env("0DTE_PRIORITY_CRWD_MACD_MIN", 0.5)
        and volr >= _float_env("0DTE_PRIORITY_CRWD_VOL_MIN", 0.28)
    ):
        pen *= _float_env("0DTE_PRIORITY_BOOST_CRWD_MOMENTUM", 1.10)

    # SMCI-class: weak SO + flat VWAP should not outrank desk leaders
    if so_align is not None and float(so_align) < _float_env("0DTE_PRIORITY_WEAK_SO_ALIGN_MAX", 0.72):
        if abs(vd) < _float_env("0DTE_PRIORITY_FLAT_VWAP_ABS_MAX", 5.0):
            pen *= _float_env("0DTE_PRIORITY_PEN_WEAK_SO_FLAT", 0.68)

    # Low SO-alignment + marginal VWAP (SMCI-class) should not outrank DAL/IONQ/CRWD
    if (
        _float_env("0DTE_PRIORITY_FLAT_VWAP_LOW", -5.0)
        < vd
        < _float_env("0DTE_PRIORITY_FLAT_VWAP_HIGH", 3.0)
        and brk < _float_env("0DTE_PRIORITY_FLAT_BREAKOUT_MAX", 1.5)
    ):
        pen *= _float_env("0DTE_PRIORITY_PEN_FLAT_CONTINUATION", 0.84)

    return pen


def _delta_spread_for_underlying(target_symbol: str, orb_range_pct: float) -> Tuple[float, float]:
    """
    Target delta and spread width ($) from ORB range tiers — all knobs in env (0DTE_DELTA_*).
    Buckets: SPX, QQQ, SPY, OTHER.
    """
    high_p = _float_env("0DTE_DELTA_ORB_PCT_HIGH", 0.5)
    med_p = _float_env("0DTE_DELTA_ORB_PCT_MED", 0.35)
    if orb_range_pct >= high_p:
        tier = "HIGH"
    elif orb_range_pct >= med_p:
        tier = "MED"
    else:
        tier = "LOW"

    sym = str(target_symbol or "").strip().upper()
    if sym == "SPX":
        bucket = "SPX"
    elif sym == "QQQ":
        bucket = "QQQ"
    elif sym == "SPY":
        bucket = "SPY"
    else:
        bucket = "OTHER"

    defaults: Dict[Tuple[str, str], Tuple[float, float]] = {
        ("SPX", "HIGH"): (0.30, 10.0),
        ("SPX", "MED"): (0.25, 5.0),
        ("SPX", "LOW"): (0.15, 5.0),
        ("QQQ", "HIGH"): (0.30, 2.0),
        ("QQQ", "MED"): (0.25, 1.0),
        ("QQQ", "LOW"): (0.15, 1.0),
        ("SPY", "HIGH"): (0.30, 2.0),
        ("SPY", "MED"): (0.25, 1.0),
        ("SPY", "LOW"): (0.15, 1.0),
        ("OTHER", "HIGH"): (0.35, 2.0),
        ("OTHER", "MED"): (0.25, 1.0),
        ("OTHER", "LOW"): (0.15, 1.0),
    }
    d_def, w_def = defaults[(bucket, tier)]
    delta = _float_env(f"0DTE_DELTA_{bucket}_{tier}_DELTA", d_def)
    width = _float_env(f"0DTE_DELTA_{bucket}_{tier}_WIDTH", w_def)
    return delta, width


def _rank_position_multiplier(rank: int) -> float:
    """Greedy sizing multiplier by priority rank — env 0DTE_RANK_MULT_*."""
    r = int(rank)
    if r == 1:
        return _float_env("0DTE_RANK_MULT_1", 3.0)
    if r == 2:
        return _float_env("0DTE_RANK_MULT_2", 2.5)
    if r == 3:
        return _float_env("0DTE_RANK_MULT_3", 2.0)
    if r <= 5:
        return _float_env("0DTE_RANK_MULT_4_5", 1.71)
    if r <= 10:
        return _float_env("0DTE_RANK_MULT_6_10", 1.5)
    if r <= 15:
        return _float_env("0DTE_RANK_MULT_11_15", 1.2)
    return _float_env("0DTE_RANK_MULT_16_PLUS", 1.0)


def _momentum_vwap_long_points(vwap_distance: float) -> float:
    bounds = sorted(_parse_csv_floats("0DTE_MOMENTUM_VWAP_LONG_BOUNDS", "1.0,0.5,0.0"), reverse=True)
    pts = _parse_csv_floats("0DTE_MOMENTUM_VWAP_LONG_POINTS", "20,15,10,5")
    if len(pts) != len(bounds) + 1:
        bounds = sorted([1.0, 0.5, 0.0], reverse=True)
        pts = [20.0, 15.0, 10.0, 5.0]
    for i, b in enumerate(bounds):
        if vwap_distance >= b:
            return pts[i]
    return pts[-1]


def _hard_gate_base_index_set() -> set:
    raw = os.getenv("0DTE_HARD_GATE_BASE_INDICES", "SPY,QQQ,IWM")
    return {p.strip().upper() for p in str(raw).split(",") if p.strip()}


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _effective_hard_gate_volume_multiplier(
    signal: "DTE0Signal",
    volume_multiplier: Optional[float] = None,
) -> float:
    """
    Rev 00368: Do not apply Hard Gate volume stricter than continuation vol floor (May 27 desk).
    """
    if volume_multiplier is not None:
        base = float(volume_multiplier)
    else:
        base = float(os.getenv("0DTE_HARD_GATE_VOLUME_MULTIPLIER", "1.0"))
    if not _env_bool("0DTE_HARD_GATE_ALIGN_CONTINUATION_VOL", True):
        return base
    direction = str(getattr(signal, "direction", "LONG") or "LONG").upper()
    from easy0DTE.modules.quality_gate_stack import orb_equity_volume_ratio_min

    cont_floor = orb_equity_volume_ratio_min("PUT" if direction == "SHORT" else "CALL")
    return min(base, cont_floor)


def _momentum_vwap_short_points(vwap_distance: float) -> float:
    """Stronger bearish = more negative; thresholds ascending toward 0."""
    bounds = sorted(_parse_csv_floats("0DTE_MOMENTUM_VWAP_SHORT_BOUNDS", "-1.0,-0.5,0.0"))
    pts = _parse_csv_floats("0DTE_MOMENTUM_VWAP_SHORT_POINTS", "20,15,10,5")
    if len(pts) != len(bounds) + 1:
        bounds = sorted([-1.0, -0.5, 0.0])
        pts = [20.0, 15.0, 10.0, 5.0]
    for i, b in enumerate(bounds):
        if vwap_distance <= b:
            return pts[i]
    return pts[-1]


# Timezone constants
ET_TZ = pytz.timezone('America/New_York')
PT_TZ = pytz.timezone('America/Los_Angeles')

# 0DTE Symbols - Loaded from 0dte_list.csv (Rev 00209); CSV missing → 0DTE_SYMBOLS_FALLBACK env
def _default_0dte_symbol_fallback() -> List[str]:
    raw = os.getenv("0DTE_SYMBOLS_FALLBACK", "SPY,QQQ,IWM")
    parts = [p.strip().upper() for p in str(raw).split(",") if p.strip()]
    return parts if parts else ["SPY", "QQQ", "IWM"]


DTE_SYMBOLS = _default_0dte_symbol_fallback()

_0DTE_WATCHLIST_SEARCH_PATHS = [
    "/app/data/watchlist/0dte_list.csv",
    "data/watchlist/0dte_list.csv",
    "../1. The Easy ORB Strategy/data/watchlist/0dte_list.csv",
    "../../1. The Easy ORB Strategy/data/watchlist/0dte_list.csv",
    "/app/1. The Easy ORB Strategy/data/watchlist/0dte_list.csv",
]


def _0dte_watchlist_search_paths() -> List[str]:
    paths: List[str] = []
    env_path = os.getenv("0DTE_WATCHLIST_PATH", "").strip()
    if env_path:
        paths.append(env_path)
    extra = os.getenv("0DTE_WATCHLIST_CSV_EXTRA_PATHS", "").strip()
    if extra:
        for p in extra.split(","):
            p = p.strip()
            if p:
                paths.append(p)
    paths.extend(_0DTE_WATCHLIST_SEARCH_PATHS)
    seen = set()
    ordered: List[str] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            ordered.append(path)
    return ordered


def _read_0dte_watchlist_df(path: str):
    """Read 0dte_list.csv whether or not the file includes a header row."""
    import pandas as pd

    df = pd.read_csv(path, comment="#")
    if "symbol" not in df.columns:
        df = pd.read_csv(
            path,
            comment="#",
            header=None,
            names=["symbol", "tier", "category", "notes"],
        )
    return df


def resolve_0dte_watchlist_path() -> Optional[str]:
    for path in _0dte_watchlist_search_paths():
        if os.path.exists(path):
            return path
    return None


def load_0dte_watchlist_df():
    """Load the first found 0dte_list.csv as a DataFrame."""
    path = resolve_0dte_watchlist_path()
    if not path:
        return None
    return _read_0dte_watchlist_df(path)


def load_0dte_symbols() -> List[str]:
    """
    Load 0DTE symbols from 0dte_list.csv file

    Returns:
        Symbols in CSV row order. Cash indexes (SPX/RUT) are omitted from the
        CSV — not filtered in code. The ``tier`` column is metadata only.
    """
    try:
        path = resolve_0dte_watchlist_path()
        if not path:
            log.warning("⚠️ 0DTE symbol list not found, using 0DTE_SYMBOLS_FALLBACK")
            return _default_0dte_symbol_fallback()

        df = _read_0dte_watchlist_df(path)
        symbols = [str(s).strip().upper() for s in df["symbol"].tolist() if str(s).strip()]
        log.info(f"✅ Loaded {len(symbols)} 0DTE symbols from {path}")
        return symbols
    except Exception as e:
        log.warning(f"⚠️ Failed to load 0DTE symbol list: {e}, using default")
        return _default_0dte_symbol_fallback()


@dataclass
class DTE0Signal:
    """0DTE Signal structure"""
    symbol: str  # equity/ETF underlying (SPY/QQQ/IWM/…)
    direction: str  # 'LONG' or 'SHORT'
    orb_signal: Dict[str, Any]  # Original ORB signal
    eligibility_result: ConvexEligibilityResult
    target_delta: float  # Target delta for long leg (0.30-0.45)
    spread_width: float  # $1 or $2
    spread_type: str = 'debit'  # 'single_leg', 'debit', or 'credit'
    strategy_type: str = 'debit_spread'  # Rev 00229: 'long_call', 'long_put', 'debit_spread', 'momentum_scalper', 'itm_probability_spread', 'lotto', 'no_trade'
    created_at: datetime = field(default_factory=datetime.now)
    priority_score: float = 0.0  # Priority score (0.0-1.0) - calculated during ranking
    priority_rank: int = 0  # Priority rank (1 = highest) - assigned during ranking
    capital_allocated: float = 0.0  # Capital allocated based on priority tier
    momentum_score: float = 0.0  # Rev 00228: Momentum Strength Score (0-100)
    hard_gate_prevalidated: bool = False  # Set in Signal Collection prevalidation
    hard_gate_reason: Optional[str] = None  # If prevalidation failed, store reason
    
    @property
    def option_type(self) -> str:
        """
        Convert direction to option type
        
        Returns:
            'call' for LONG direction (bullish)
            'put' for SHORT direction (bearish)
        """
        return 'call' if self.direction == 'LONG' else 'put'
    
    @property
    def option_type_label(self) -> str:
        """Human-readable option type label"""
        return 'CALL' if self.direction == 'LONG' else 'PUT'
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary"""
        return {
            'symbol': self.symbol,
            'direction': self.direction,
            'option_type': self.option_type,  # 'call' or 'put'
            'option_type_label': self.option_type_label,  # 'CALL' or 'PUT'
            'orb_signal': self.orb_signal,
            'eligibility_score': self.eligibility_result.eligibility_score,
            'target_delta': self.target_delta,
            'spread_width': self.spread_width,
            'spread_type': self.spread_type,  # 'single_leg', 'debit', or 'credit'
            'strategy_type': self.strategy_type,  # Rev 00227: Level 2 strategy type
            'created_at': self.created_at.isoformat(),
            'priority_score': self.priority_score,
            'priority_rank': self.priority_rank,
            'capital_allocated': self.capital_allocated,
            'hard_gate_prevalidated': self.hard_gate_prevalidated,
            'hard_gate_reason': self.hard_gate_reason,
        }


class Prime0DTEStrategyManager:
    """
    Prime 0DTE Strategy Manager
    
    Listens to ORB signals and applies Convex Eligibility Filter to determine
    which signals deserve options exposure.
    """
    
    def __init__(
        self,
        convex_filter: Optional[ConvexEligibilityFilter] = None,
        target_symbols: List[str] = None,
        max_positions: int = 5,
        enable_lotto_sleeve: bool = False,
        priority_collector: Optional[OptionsPriorityDataCollector] = None,
        alert_manager=None
    ):
        """
        Initialize 0DTE Strategy Manager
        
        Args:
            convex_filter: Convex Eligibility Filter instance (creates default if None)
            target_symbols: Target symbols for options (default: ['QQQ', 'SPY'])
            max_positions: Maximum number of 0DTE positions
            enable_lotto_sleeve: Enable lotto sleeve (single-leg options)
            priority_collector: Options Priority Data Collector for trade history
        """
        self.convex_filter = convex_filter or ConvexEligibilityFilter()
        # Rev 00209: Load 0DTE symbols from file, or use provided list, or use default
        # Rev 00346: Uppercase/strip so ORB dict keys (e.g. vix vs VIX) still match 0dte_list.csv
        if target_symbols:
            self.target_symbols = [str(s).strip().upper() for s in target_symbols]
        else:
            self.target_symbols = [str(s).strip().upper() for s in load_0dte_symbols()]
        self.max_positions = max_positions
        self.enable_lotto_sleeve = enable_lotto_sleeve
        
        # Priority Data Collector for trade history
        self.priority_collector = priority_collector
        
        # Alert Manager for notifications
        self.alert_manager = alert_manager
        
        # Signal storage
        self.orb_signals: List[Dict[str, Any]] = []
        self.eligible_signals: List[ConvexEligibilityResult] = []
        self.dte0_signals: List[DTE0Signal] = []
        
        # Callbacks
        self.on_signal_callback: Optional[Callable] = None
        
        log.info(f"Prime 0DTE Strategy Manager initialized:")
        log.info(f"  - Target symbols: {self.target_symbols}")
        log.info(f"  - Max positions: {self.max_positions}")
        log.info(f"  - Lotto sleeve enabled: {self.enable_lotto_sleeve}")
        log.info(f"  - Priority collector: {'Enabled' if self.priority_collector else 'Disabled'}")
        self._validate_structure_mapping()
        # Pipeline audit keys for Signal Collection merge / Cloud Logging (Rev 00347)
        self._last_convex_audit_by_key: Dict[str, Dict[str, Any]] = {}
        self._last_priority_dropped_keys: Set[str] = set()
        self._last_zero_ps_dropped_keys: Set[str] = set()
        self._last_shallow_dropped_keys: Set[str] = set()
        self._last_structure_skip_keys: Set[str] = set()
        self._last_convex_qualified_count: int = 0
        self._last_convex_pass_keys: Set[str] = set()
        self._last_hard_gate_passed_count: int = 0
        self._last_hard_gate_passed_keys: Set[str] = set()
        self._last_hard_gate_failed_keys: Set[str] = set()
        self._last_hard_gate_failed_reasons: Dict[str, str] = {}

    @staticmethod
    def _orb_signal_key(sym: Any, side: Any) -> str:
        su = str(sym or "").strip().upper()
        sd = str(side or "LONG").strip().upper()
        return f"{su}_{sd}"

    def _refresh_convex_audit_from_filter(self) -> None:
        """Rebuild `_last_convex_audit_by_key` from `convex_filter._last_full_results` and emit INFO audit lines."""
        self._last_convex_audit_by_key = {}
        raw = getattr(self.convex_filter, "_last_full_results", None) or []
        for r in raw:
            sig = getattr(r, "signal", None) or {}
            sym = str(sig.get("symbol", "?")).strip().upper()
            side = str(sig.get("side", "LONG")).strip().upper()
            key = f"{sym}_{side}"
            reasons = list(getattr(r, "rejection_reasons", None) or [])
            joined = " || ".join(str(x) for x in reasons[:15])
            if len(joined) > 480:
                joined = joined[:477] + "..."
            eligible = bool(getattr(r, "is_eligible", False))
            score_f = float(getattr(r, "eligibility_score", 0.0) or 0.0)
            self._last_convex_audit_by_key[key] = {
                "symbol": sym,
                "side": side,
                "convex_eligible": eligible,
                "convex_score": round(score_f, 4),
                "top_reason": (reasons[0] if reasons else ""),
                "all_reasons_short": joined,
                "quality_gate_report": (
                    sig.get("quality_gate_report")
                    if isinstance(sig.get("quality_gate_report"), dict)
                    else None
                ),
            }
            top = (reasons[0] if reasons else "")[:220]
            log.info(
                "0DTE_CONVEX_AUDIT | symbol=%s | side=%s | eligible=%s | score=%.4f | top_reason=%s",
                sym,
                side,
                str(eligible).lower(),
                score_f,
                top,
            )
            if joined and not eligible:
                log.info(
                    "0DTE_CONVEX_AUDIT_DETAIL | symbol=%s | side=%s | all_reasons=%s",
                    sym,
                    side,
                    joined,
                )

    def _get_market_alignment_data(self) -> Dict[str, Any]:
        """
        Lightweight market alignment snapshot for momentum scoring.

        Rev 00300: Added to prevent AttributeError during Convex processing
        when called from listen_to_orb_signals. Uses available SPY/QQQ data
        if present via a data manager; otherwise returns an empty dict and
        momentum scoring falls back to signal-local data.
        """
        market_data: Dict[str, Any] = {}
        try:
            data_manager = None

            # Prefer an attached data_manager on this manager (if wired)
            if hasattr(self, "data_manager") and self.data_manager:
                data_manager = self.data_manager

            # Fallback: options_executor may expose a data_manager
            if not data_manager and hasattr(self, "options_executor") and self.options_executor:
                if hasattr(self.options_executor, "data_manager"):
                    data_manager = self.options_executor.data_manager

            if not data_manager:
                return market_data

            symbols = ["SPY", "QQQ"]
            quotes = None
            if hasattr(data_manager, "get_cached_quotes"):
                quotes = data_manager.get_cached_quotes(symbols)
            elif hasattr(data_manager, "get_batch_quotes"):
                quotes = data_manager.get_batch_quotes(symbols)

            if isinstance(quotes, dict):
                for sym in symbols:
                    q = quotes.get(sym) or {}
                    if q:
                        market_data[sym] = {
                            "last": q.get("last") or q.get("price"),
                            "change_pct": q.get("change_pct"),
                            "open": q.get("open"),
                            "high": q.get("high"),
                            "low": q.get("low"),
                            "volume": q.get("volume"),
                        }
                # Build direction keys expected by calculate_momentum_score()
                # so alignment contributes correctly instead of silently defaulting to NONE.
                def _direction_from_quote(q: Dict[str, Any]) -> str:
                    try:
                        change_pct = q.get("change_pct")
                        if change_pct is not None:
                            cp = float(change_pct)
                            if cp > 0:
                                return "UP"
                            if cp < 0:
                                return "DOWN"
                            return "NONE"
                        last = q.get("last")
                        open_px = q.get("open")
                        if last is not None and open_px is not None:
                            l = float(last)
                            o = float(open_px)
                            if l > o:
                                return "UP"
                            if l < o:
                                return "DOWN"
                    except Exception:
                        pass
                    return "NONE"

                market_data["spy_direction"] = _direction_from_quote(market_data.get("SPY") or {})
                market_data["qqq_direction"] = _direction_from_quote(market_data.get("QQQ") or {})
        except Exception:
            # Alignment data is an enhancement; failures should not break signal generation
            return {}
        return market_data
    
    def set_signal_callback(self, callback: Callable):
        """Set callback for when 0DTE signals are generated"""
        self.on_signal_callback = callback

    def _dedupe_signals_keep_strongest(self, signals: List[DTE0Signal]) -> List[DTE0Signal]:
        """
        Deduplicate by (symbol, direction), keeping the strongest candidate.

        Strength heuristic favors higher Convex eligibility first, then higher
        confidence from the originating ORB signal.
        """
        if not signals:
            return []

        best_by_key: Dict[Tuple[str, str], DTE0Signal] = {}
        for signal in signals:
            key = (signal.symbol, signal.direction)
            current_best = best_by_key.get(key)
            if current_best is None:
                best_by_key[key] = signal
                continue

            new_score = float(getattr(signal.eligibility_result, "eligibility_score", 0.0) or 0.0)
            old_score = float(getattr(current_best.eligibility_result, "eligibility_score", 0.0) or 0.0)
            if new_score > old_score:
                best_by_key[key] = signal
                continue
            if new_score == old_score:
                new_conf = float(signal.orb_signal.get("confidence", 0.0) or 0.0)
                old_conf = float(current_best.orb_signal.get("confidence", 0.0) or 0.0)
                if new_conf > old_conf:
                    best_by_key[key] = signal

        deduped = list(best_by_key.values())
        removed = len(signals) - len(deduped)
        if removed > 0:
            log.warning(
                f"⚠️ 0DTE dedupe removed {removed} duplicate candidates "
                f"(by symbol+direction); remaining={len(deduped)}"
            )
            log.info(
                f"  0DTE_DEDUPE | removed={removed} | remaining={len(deduped)} | "
                f"keeps_highest_eligibility_then_confidence"
            )
        return deduped
    
    async def listen_to_orb_signals(
        self,
        orb_signals: List[Dict[str, Any]],
        symbol_mapping: Optional[Dict[str, str]] = None,
        orb_strategy_manager: Optional[Any] = None,
        portfolio_red_day: bool = False,
    ) -> List[DTE0Signal]:
        """
        Listen to ORB signals and generate 0DTE signals
        
        Args:
            orb_signals: List of ORB signals from PrimeORBStrategyManager
            symbol_mapping: Mapping from ORB symbols to 0DTE symbols (e.g., {'TQQQ': 'QQQ'})
            
        Returns:
            List of 0DTE signals
        """
        self._last_priority_dropped_keys = set()
        self._last_structure_skip_keys = set()
        self._last_convex_audit_by_key = {}
        self._last_convex_qualified_count = 0
        self._last_convex_pass_keys = set()
        self._last_hard_gate_passed_count = 0
        self._last_hard_gate_passed_keys = set()
        self._last_hard_gate_failed_keys = set()
        self._last_hard_gate_failed_reasons = {}

        if not orb_signals:
            log.info("No ORB signals received")
            return []

        log.info(
            f"  0DTE_PIPELINE | stage=listen_start | orb_in={len(orb_signals)} | "
            f"targets_loaded={len(self.target_symbols or [])} | "
            f"grep=0DTE_PIPELINE|0DTE_TARGET_FILTER|0DTE_DEDUPE|0DTE_PRIORITY_DROP|0DTE_qualified_for_execution"
        )

        # Process only symbols that are in 0DTE target list.
        # This keeps Convex pass/reject counts aligned with executable 0DTE universe.
        target_set = set(self.target_symbols or [])

        def _norm_sym(x: Any) -> str:
            return str(x).strip().upper() if x is not None else ""

        target_orb_signals = [
            s for s in orb_signals
            if (_norm_sym(s.get("symbol")) in target_set)
            or (_norm_sym(s.get("original_symbol")) in target_set)
        ]

        input_long = sum(1 for s in target_orb_signals if s.get('side', 'LONG') == 'LONG')
        input_short = sum(1 for s in target_orb_signals if s.get('side', 'LONG') == 'SHORT')
        dropped_non_target = len(orb_signals) - len(target_orb_signals)

        log.info(f"📥 0DTE Strategy: Received {len(target_orb_signals)} target signals for processing (LONG: {input_long}, SHORT: {input_short})")
        log.info(f"   Target symbols list: {len(self.target_symbols)} symbols from 0dte_list.csv")
        if dropped_non_target > 0:
            not_in_target: List[str] = []
            seen_nt = set()
            for s in orb_signals:
                sym = s.get("symbol")
                orig = s.get("original_symbol")
                if (_norm_sym(sym) in target_set) or (_norm_sym(orig) in target_set):
                    continue
                label = sym or orig or "?"
                if label not in seen_nt:
                    seen_nt.add(label)
                    not_in_target.append(label)
            sample = ",".join(not_in_target[:45])
            suffix = "..." if len(not_in_target) > 45 else ""
            log.info(
                f"  0DTE_TARGET_FILTER | dropped_signals={dropped_non_target} | "
                f"unique_symbols_not_in_0dte_list={len(not_in_target)} | sample={sample}{suffix}"
            )
            log.info(f"   Dropped non-target symbols before Convex: {dropped_non_target}")
            for s in orb_signals:
                sym = _norm_sym(s.get("symbol")) or _norm_sym(s.get("original_symbol"))
                if not sym or sym in target_set:
                    continue
                log.info(
                    "0DTE_COLLECTION_AUDIT | symbol=%s | side=%s | stage=target_list | outcome=dropped | reason=not_in_0dte_list_csv",
                    sym,
                    str(s.get("side") or "LONG"),
                )
        
        if not target_orb_signals:
            log.warning("⚠️ 0DTE Strategy: 0 target symbols in input after target filter")
            return []
        
        # Store ORB signals
        self.orb_signals = target_orb_signals
        
        # Rev 00246: Log ORB signals summary
        orb_symbols = [s.get('symbol', 'UNKNOWN') for s in target_orb_signals]
        dte_targets_in_signals = [s for s in orb_symbols if str(s).strip().upper() in target_set]
        log.info(f"   ORB signals matching 0DTE targets: {len(dte_targets_in_signals)}/{len(target_orb_signals)}")
        if dte_targets_in_signals:
            log.info(f"   Matching symbols: {', '.join(dte_targets_in_signals[:10])}{'...' if len(dte_targets_in_signals) > 10 else ''}")
        
        # Stamp regime for Convex E10/E11 (protection_day CALL block + carve-out).
        # Prefer explicit trigger on signals / strategy manager; fall back to portfolio_red_day.
        _prot = bool(portfolio_red_day)
        _tid = None
        for src in (orb_strategy_manager, self):
            if src is None:
                continue
            metrics = getattr(src, "_red_day_metrics", None) or {}
            if isinstance(metrics, dict):
                _tid = metrics.get("trigger_id") or metrics.get("pp_trigger_id") or _tid
            assess = getattr(src, "_pp_assessment", None) or getattr(src, "pp_assessment", None)
            if assess is not None:
                _tid = getattr(assess, "trigger_id", None) or _tid
                if getattr(assess, "is_protection_day", None):
                    try:
                        _prot = bool(assess.is_protection_day) or _prot
                    except Exception:
                        pass
        if _tid and "protection_day" in str(_tid).lower():
            _prot = True
        if _prot or _tid:
            for s in target_orb_signals:
                if not isinstance(s, dict):
                    continue
                if _prot:
                    s["protection_day"] = True
                    s["is_protection_day"] = True
                if _tid and not s.get("pp_trigger_id"):
                    s["pp_trigger_id"] = _tid
                    s["red_day_trigger_id"] = _tid
            log.info(
                "0DTE_CONVEX_REGIME_STAMP | protection_day=%s | trigger=%s | n=%d",
                str(_prot).lower(),
                _tid,
                len(target_orb_signals),
            )

        # Apply Quality Gate (ORB admit_pre_exec via ConvexEligibilityFilter shim)
        log.info(
            "🔍 Applying Quality Gate (admit_pre_exec STACKS[orb_0dte])..."
        )
        # Rev 00310: Do not cap here — Convex sorts by eligibility only; 0DTE priority
        # (breakout/range/volume) is applied later. Capping at max_signals could drop
        # names that would rank in the top max_positions after _rank_signals_by_priority.
        eligible_results = self.convex_filter.filter_signals(
            target_orb_signals,
            max_signals=None,
        )
        self._refresh_convex_audit_from_filter()

        self.eligible_signals = eligible_results
        
        if not eligible_results:
            log.warning(f"⚠️ 0DTE Strategy: No signals passed Quality Gate (0/{len(target_orb_signals)})")
            log.warning(
                f"   Input: {input_long} LONG (CALL), {input_short} SHORT (PUT) → Quality Gate filtered ALL out"
            )
            log.warning(
                "   Check QUALITY_GATE_REJECT / CONVEX_REJECT logs (pc_oi, ORB range, v25, Layer 5, etc.)"
            )
            log.info(
                f"  0DTE_QUALITY_GATE_0_eligible | input={len(target_orb_signals)} "
                f"LONG={input_long} SHORT={input_short} | grep=QUALITY_GATE_REJECT|CONVEX_REJECT|CONVEX_0_eligible"
            )
            log.info(
                f"  0DTE_CONVEX_0_eligible | input={len(target_orb_signals)} LONG={input_long} SHORT={input_short} | grep CONVEX_REJECT for per-symbol; grep CONVEX_0_eligible for summary"
            )
            return []
        
        log.info(f"✅ {len(eligible_results)}/{len(target_orb_signals)} signals passed Quality Gate")
        passed_convex_symbols = [f"{r.signal.get('symbol', '?')}_{'CALL' if r.signal.get('side', 'LONG') == 'LONG' else 'PUT'}" for r in eligible_results]
        log.info(f"  0DTE_QUALITY_GATE_PASSED | count={len(eligible_results)} | symbols={','.join(passed_convex_symbols[:30])}{'...' if len(passed_convex_symbols) > 30 else ''}")
        log.info(f"  0DTE_CONVEX_PASSED | count={len(eligible_results)} | symbols={','.join(passed_convex_symbols[:30])}{'...' if len(passed_convex_symbols) > 30 else ''}")
        
        # Rev 00246: Log detailed eligibility breakdown
        for i, result in enumerate(eligible_results[:5], 1):  # Show top 5
            signal = result.signal
            symbol = signal.get('symbol', 'UNKNOWN')
            score = result.eligibility_score
            log.info(f"   {i}. {symbol}: Score {score:.3f} - Eligible: {result.is_eligible}, "
                    f"Reasons: {len(result.eligibility_reasons)} pass, {len(result.rejection_reasons)} fail")
            if result.rejection_reasons:
                log.debug(f"      Rejection reasons: {', '.join(result.rejection_reasons[:3])}")
        
        # Rev 00211: Separate LONG and SHORT signals for processing (0DTE produces both; ORB SO is Long-only)
        long_results = [r for r in eligible_results if r.signal.get('side', 'LONG') == 'LONG']
        short_results = [r for r in eligible_results if r.signal.get('side', 'LONG') == 'SHORT']
        
        log.info(f"   • LONG signals (CALL): {len(long_results)}")
        log.info(f"   • SHORT signals (PUT): {len(short_results)}")
        if long_results and short_results:
            log.info(f"   📋 0DTE Signal Collection: Both LONG and SHORT qualified — combined list will be ranked and top N executed as CALL + PUT options")
        
        # Map ORB signals to 0DTE symbols (Rev 00209: All symbols from 0dte_list.csv)
        # Rev 00211: Process both LONG (CALL) and SHORT (PUT) signals
        dte0_signals = []
        
        # Process LONG signals (CALL options)
        log.info(f"🔵 Processing {len(long_results)} LONG signals for CALL options...")
        for result in long_results:
            orb_signal = result.signal
            orb_symbol = str(orb_signal.get('symbol', '')).strip().upper()
            direction = orb_signal.get('side', 'LONG')
            
            # ONLY process direct signals for symbols in target_symbols (loaded from 0dte_list.csv)
            if orb_symbol not in self.target_symbols:
                log.debug(f"⏭️ Skipping {orb_symbol} - not in 0DTE target symbols list")
                continue
            
            target_symbol = orb_symbol
            log.debug(f"📋 0DTE Mapping: {orb_symbol} ({direction}) -> {target_symbol} (CALL options)")
            
            # Rev 00212: Updated delta range to 0.10-0.30 (10-30 delta) for gamma explosion
            # Premium target: $0.20-$0.60, 1-3 strikes OTM
            orb_range_pct = orb_signal.get('orb_range_pct', 0.0)
            
            target_delta, spread_width = _delta_spread_for_underlying(
                target_symbol, float(orb_range_pct or 0.0)
            )

            log.debug(f"   {target_symbol} {direction}: Delta {target_delta:.2f}, Width ${spread_width:.0f} "
                     f"(ORB Range: {orb_range_pct:.2f}%)")
            
            # Temporary signal for momentum scoring only.
            temp_signal = DTE0Signal(
                symbol=target_symbol,
                direction=direction,
                orb_signal=orb_signal,
                eligibility_result=result,
                target_delta=target_delta,
                spread_width=spread_width,
                spread_type='single_leg',
                strategy_type='long_call'
            )
            momentum_score = self.calculate_momentum_score(temp_signal)
            
            # Rev 00227: Determine Level 2 strategy type for CALL (LONG direction)
            # Rev 00228: Enhanced with momentum score
            strategy_type = self._select_strategy_type(
                direction='LONG',
                orb_signal=orb_signal,
                eligibility_result=result,
                orb_range_pct=orb_range_pct,
                momentum_score=momentum_score  # Rev 00228: Pass momentum score
            )
            
            spread_type = self._spread_type_for_strategy(strategy_type)
            if strategy_type == "no_trade":
                self._last_structure_skip_keys.add(self._orb_signal_key(target_symbol, direction))
                log.info(
                    "0DTE_COLLECTION_AUDIT | symbol=%s | side=%s | stage=structure_selection | outcome=skip | reason=strategy_type_no_trade",
                    target_symbol,
                    direction,
                )
                log.info(
                    "0DTE_STRUCTURE_SELECTION | symbol=%s | strategy_type=%s | momentum=%.1f | confidence=%.2f | volume_ratio=%.2f | spread_type=%s | action=skip",
                    target_symbol,
                    strategy_type,
                    momentum_score,
                    float(orb_signal.get('confidence', 0.0) or 0.0),
                    float(orb_signal.get('volume_ratio', 0.0) or 0.0),
                    spread_type,
                )
                continue
            
            # Create 0DTE CALL signal
            dte0_signal = DTE0Signal(
                symbol=target_symbol,
                direction=direction,  # LONG
                orb_signal=orb_signal,
                eligibility_result=result,
                target_delta=target_delta,
                spread_width=spread_width,
                spread_type=spread_type,
                strategy_type=strategy_type,  # Rev 00227: Level 2 strategy selection
                momentum_score=momentum_score  # Rev 00228: Momentum Strength Score
            )
            
            dte0_signals.append(dte0_signal)
        
        # Rev 00211: Process SHORT signals (PUT options)
        log.info(f"🔴 Processing {len(short_results)} SHORT signals for PUT options...")
        for result in short_results:
            orb_signal = result.signal
            orb_symbol = str(orb_signal.get('symbol', '')).strip().upper()
            direction = orb_signal.get('side', 'SHORT')
            
            # ONLY process direct signals for symbols in target_symbols
            if orb_symbol not in self.target_symbols:
                log.debug(f"⏭️ Skipping {orb_symbol} - not in 0DTE target symbols list")
                continue
            
            target_symbol = orb_symbol
            log.debug(f"📋 0DTE Mapping: {orb_symbol} ({direction}) -> {target_symbol} (PUT options)")
            
            # Determine target delta and spread width based on symbol and volatility
            orb_range_pct = orb_signal.get('orb_range_pct', 0.0)
            
            target_delta, spread_width = _delta_spread_for_underlying(
                target_symbol, float(orb_range_pct or 0.0)
            )

            log.debug(f"   {target_symbol} {direction}: Delta {target_delta:.2f}, Width ${spread_width:.0f} "
                     f"(ORB Range: {orb_range_pct:.2f}%)")
            
            # Temporary signal for momentum scoring only.
            temp_signal = DTE0Signal(
                symbol=target_symbol,
                direction=direction,
                orb_signal=orb_signal,
                eligibility_result=result,
                target_delta=target_delta,
                spread_width=spread_width,
                spread_type='single_leg',
                strategy_type='long_put'
            )
            momentum_score = self.calculate_momentum_score(temp_signal)
            
            # Rev 00227: Determine Level 2 strategy type for PUT (SHORT direction)
            # Rev 00228: Enhanced with momentum score
            strategy_type = self._select_strategy_type(
                direction='SHORT',
                orb_signal=orb_signal,
                eligibility_result=result,
                orb_range_pct=orb_range_pct,
                momentum_score=momentum_score  # Rev 00228: Pass momentum score
            )
            
            spread_type = self._spread_type_for_strategy(strategy_type)
            if strategy_type == "no_trade":
                self._last_structure_skip_keys.add(self._orb_signal_key(target_symbol, direction))
                log.info(
                    "0DTE_COLLECTION_AUDIT | symbol=%s | side=%s | stage=structure_selection | outcome=skip | reason=strategy_type_no_trade",
                    target_symbol,
                    direction,
                )
                log.info(
                    "0DTE_STRUCTURE_SELECTION | symbol=%s | strategy_type=%s | momentum=%.1f | confidence=%.2f | volume_ratio=%.2f | spread_type=%s | action=skip",
                    target_symbol,
                    strategy_type,
                    momentum_score,
                    float(orb_signal.get('confidence', 0.0) or 0.0),
                    float(orb_signal.get('volume_ratio', 0.0) or 0.0),
                    spread_type,
                )
                continue
            
            # Create 0DTE PUT signal
            dte0_signal = DTE0Signal(
                symbol=target_symbol,
                direction=direction,  # SHORT
                orb_signal=orb_signal,
                eligibility_result=result,
                target_delta=target_delta,
                spread_width=spread_width,
                spread_type=spread_type,
                strategy_type=strategy_type,  # Rev 00227: Level 2 strategy selection
                momentum_score=momentum_score  # Rev 00228: Momentum Strength Score
            )
            
            dte0_signals.append(dte0_signal)
        
        # Remove duplicate symbol+direction entries before scoring/ranking.
        # This avoids repeated names crowding out distinct candidates in top-N execution.
        dte0_signals = self._dedupe_signals_keep_strongest(dte0_signals)

        # Rev 00228: Final momentum (includes SPY/QQQ alignment when market_data present)
        if dte0_signals:
            market_data = self._get_market_alignment_data()
            for signal in dte0_signals:
                momentum_score = self.calculate_momentum_score(signal, market_data)
                log.debug(f"   {signal.symbol} {signal.direction}: Momentum Score = {momentum_score:.1f}/100")
            # Rev 00310: Strategy was chosen using pre-alignment temp_signal momentum; re-select
            # after final score so no_trade / ITM / debit / scalper match execution reality.
            for signal in dte0_signals:
                orb_range_pct = float(signal.orb_signal.get('orb_range_pct', 0.0) or 0.0)
                signal.strategy_type = self._select_strategy_type(
                    direction=signal.direction,
                    orb_signal=signal.orb_signal,
                    eligibility_result=signal.eligibility_result,
                    orb_range_pct=orb_range_pct,
                    momentum_score=signal.momentum_score,
                )
                signal.spread_type = self._spread_type_for_strategy(signal.strategy_type)
            # non-executable strategy rows should not be queued for execution
            dte0_signals = [s for s in dte0_signals if str(s.strategy_type).lower() != "no_trade"]
        
        # Hard Gate before priority rank so top-N cap backfills from Hard Gate survivors.
        if dte0_signals:
            log.info(
                "0DTE_PIPELINE | stage=hard_gate_input | count=%d | next=priority_rank_then_cap",
                len(dte0_signals),
            )
            dte0_signals = self._apply_hard_gate_before_rank(dte0_signals)
            log.info(
                "0DTE_PIPELINE | stage=hard_gate_passed | count=%d | convex_in=%d",
                len(dte0_signals),
                self._last_convex_qualified_count,
            )

        # Calculate priority scores and rank signals (Rev 00374: SO mirror / legacy 00352)
        if dte0_signals:
            log.info(f"📊 Ranking {len(dte0_signals)} 0DTE signals by priority...")
            dte0_signals = self._rank_signals_by_priority(dte0_signals)
            dte0_signals = self._apply_dte0_priority_execution_filters(dte0_signals)
            log.info(f"✅ Ranked {len(dte0_signals)} 0DTE signals by priority (post PS floor + cap)")
        
        # Record signal collection for Priority Optimizer
        if self.priority_collector and dte0_signals:
            try:
                self.priority_collector.record_signal_collection(dte0_signals)
            except Exception as e:
                log.error(f"Failed to record signal collection: {e}")
        
        self.dte0_signals = dte0_signals

        # Validation-only ExecutionIntent generation (non-breaking; logging only).
        for signal in dte0_signals:
            sig_meta = signal.orb_signal if isinstance(signal.orb_signal, dict) else {}
            norm = (
                signal.metadata.get("normalized_options", {})
                if hasattr(signal, "metadata") and isinstance(getattr(signal, "metadata", None), dict)
                else {}
            )
            intent = ExecutionIntent(
                symbol=signal.symbol,
                side="LONG",
                strategy_type=signal.strategy_type,
                asset_type="option",
                structure_type=norm.get("position_type", signal.spread_type),
                confidence=float(sig_meta.get("confidence", 0.0) or 0.0),
                entry_price=None,
                stop_loss=None,
                take_profit=None,
                metadata={
                    "delta": getattr(signal, "target_delta", None),
                    "orb_range": sig_meta.get("orb_range_pct"),
                    "volume_ratio": sig_meta.get("volume_ratio"),
                },
            )
            log.info(
                f"EXECUTION_INTENT_0DTE | {intent.symbol} | {intent.strategy_type} | "
                f"{intent.structure_type} | conf={intent.confidence:.2f}"
            )
        
        dte0_call_count = sum(1 for s in dte0_signals if s.direction == 'LONG')
        dte0_put_count = sum(1 for s in dte0_signals if s.direction == 'SHORT')
        qual_symbols = [f"{s.symbol}_{s.option_type_label}" for s in dte0_signals]
        log.info(f"  0DTE_qualified_for_execution | count={len(dte0_signals)} CALL={dte0_call_count} PUT={dte0_put_count} | symbols={','.join(qual_symbols[:25])}{'...' if len(qual_symbols) > 25 else ''}")
        log.info(f"✅ Generated {len(dte0_signals)} 0DTE signals (CALL: {dte0_call_count}, PUT: {dte0_put_count}) — ranked by priority:")
        for signal in dte0_signals:
            log.info(f"  - Rank {signal.priority_rank}: {signal.symbol} {signal.direction} ({signal.option_type_label}) "
                    f"- Priority: {signal.priority_score:.3f}, Eligibility: {signal.eligibility_result.eligibility_score:.2f}, "
                    f"Delta: {signal.target_delta:.2f}, Width: ${signal.spread_width:.0f}, "
                    f"Strategy: {signal.strategy_type}, Momentum: {signal.momentum_score:.1f}")
        log.info(
            f"  0DTE_PIPELINE | stage=listen_done | qualified={len(dte0_signals)} | "
            f"CALL={dte0_call_count} PUT={dte0_put_count} | next=execution"
        )
        
        # Send Options Signal Collection alert (Rev 00206)
        if self.alert_manager:
            try:
                # Determine mode (DEMO or LIVE)
                from modules.runtime_mode import mode_label

                mode = mode_label()
                
                # Format qualified signals for alert (include direction for Long/Short clarity)
                qualified_signals = []
                for signal in dte0_signals:
                    qualified_signals.append({
                        'symbol': signal.symbol,
                        'direction': signal.direction,  # LONG or SHORT (0DTE produces both; ORB SO is Long-only)
                        'option_type': signal.option_type,
                        'option_type_label': signal.option_type_label,
                        'eligibility_score': signal.eligibility_result.eligibility_score,
                        'target_delta': signal.target_delta,
                        'spread_width': signal.spread_width
                    })
                
                # 0DTE signal collection is now integrated into the SO Signal Collection alert
                # No separate 0DTE alert sent - information included in main ORB alert
                log.info("✅ Options Signal Collection alert sent")
            except Exception as alert_error:
                log.error(f"Failed to send Options Signal Collection alert: {alert_error}")
        
        # Call callback if set
        if self.on_signal_callback:
            await self.on_signal_callback(dte0_signals)
        
        return dte0_signals
    
    def record_execution_results(
        self,
        executed_positions: List[Any],
        filtered_signals: List[Any] = None
    ):
        """
        Record execution results for Priority Optimizer
        
        Args:
            executed_positions: List of OptionsPosition objects that were executed
            filtered_signals: List of signals that were filtered out
        """
        if self.priority_collector:
            try:
                self.priority_collector.record_execution_results(executed_positions, filtered_signals)
            except Exception as e:
                log.error(f"Failed to record execution results: {e}")
    
    async def save_priority_data(self):
        """
        Save Priority Optimizer data at EOD
        """
        if self.priority_collector:
            try:
                await self.priority_collector.save_daily_data(format='json')
            except Exception as e:
                log.error(f"Failed to save Priority Optimizer data: {e}")
    
    async def generate_0dte_signals(
        self,
        eligible_signals: List[ConvexEligibilityResult]
    ) -> List[DTE0Signal]:
        """
        Generate 0DTE signals from eligible ORB signals
        
        NOTE: This method is deprecated. Use listen_to_orb_signals() instead,
        which includes complete symbol mapping logic.
        
        Args:
            eligible_signals: List of eligible signals from Convex Eligibility Filter
            
        Returns:
            List of 0DTE signals
        """
        # This method is kept for backward compatibility but should not be used.
        # The actual implementation is in listen_to_orb_signals() which includes
        # complete symbol mapping, inverse ETF handling, and delta/spread selection.
        log.warning("generate_0dte_signals() is deprecated. Use listen_to_orb_signals() instead.")
        return []
    
    def get_strategy_stats(self) -> Dict[str, Any]:
        """Get strategy statistics"""
        return {
            'orb_signals_received': len(self.orb_signals),
            'eligible_signals': len(self.eligible_signals),
            'dte0_signals_generated': len(self.dte0_signals),
            'target_symbols': self.target_symbols,
            'max_positions': self.max_positions,
            'lotto_sleeve_enabled': self.enable_lotto_sleeve
        }
    
    def get_qqq_spy_orb_data(self, orb_strategy_manager) -> Dict[str, Any]:
        """
        Extract QQQ and SPY ORB data from ORB Strategy Manager
        
        Args:
            orb_strategy_manager: PrimeORBStrategyManager instance from ORB Strategy
            
        Returns:
            Dictionary with QQQ and SPY ORB data:
            {
                'QQQ': ORBData or None,
                'SPY': ORBData or None
            }
        """
        qqq_spy_orb = {'QQQ': None, 'SPY': None}
        
        if not orb_strategy_manager or not hasattr(orb_strategy_manager, 'orb_data'):
            log.warning("ORB Strategy Manager not available or orb_data not found")
            return qqq_spy_orb
        
        # Extract QQQ ORB data
        if 'QQQ' in orb_strategy_manager.orb_data:
            qqq_spy_orb['QQQ'] = orb_strategy_manager.orb_data['QQQ']
            # Calculate orb_range_pct from orb_range and orb_low
            qqq_orb_range_pct = float(getattr(qqq_spy_orb['QQQ'], 'orb_range_pct', 0) or 0)
            log.info(f"✅ QQQ ORB data found: High=${qqq_spy_orb['QQQ'].orb_high:.2f}, Low=${qqq_spy_orb['QQQ'].orb_low:.2f}, Range={qqq_orb_range_pct:.2f}%")
        else:
            log.warning("⚠️ QQQ ORB data not found in ORB Strategy")
        
        # Extract SPY ORB data
        if 'SPY' in orb_strategy_manager.orb_data:
            qqq_spy_orb['SPY'] = orb_strategy_manager.orb_data['SPY']
            # Calculate orb_range_pct from orb_range and orb_low
            spy_orb_range_pct = float(getattr(qqq_spy_orb['SPY'], 'orb_range_pct', 0) or 0)
            log.info(f"✅ SPY ORB data found: High=${qqq_spy_orb['SPY'].orb_high:.2f}, Low=${qqq_spy_orb['SPY'].orb_low:.2f}, Range={spy_orb_range_pct:.2f}%")
        else:
            log.warning("⚠️ SPY ORB data not found in ORB Strategy")
        
        return qqq_spy_orb
    
    def get_spx_qqq_spy_orb_data(self, orb_strategy_manager) -> Dict[str, Any]:
        """
        Extract SPX, QQQ, and SPY ORB data from ORB Strategy Manager
        
        Args:
            orb_strategy_manager: PrimeORBStrategyManager instance from ORB Strategy
            
        Returns:
            Dictionary with SPX, QQQ, and SPY ORB data:
            {
                'SPX': ORBData or None,
                'QQQ': ORBData or None,
                'SPY': ORBData or None
            }
        """
        orb_data = {'SPX': None, 'QQQ': None, 'SPY': None}
        
        if not orb_strategy_manager or not hasattr(orb_strategy_manager, 'orb_data'):
            log.warning("ORB Strategy Manager not available or orb_data not found")
            return orb_data
        
        # Extract SPX ORB data (Priority 1)
        if 'SPX' in orb_strategy_manager.orb_data:
            orb_data['SPX'] = orb_strategy_manager.orb_data['SPX']
            # Calculate orb_range_pct from orb_range and orb_low
            spx_orb_range_pct = float(getattr(orb_data['SPX'], 'orb_range_pct', 0) or 0)
            log.info(f"✅ SPX ORB data found: High=${orb_data['SPX'].orb_high:.2f}, Low=${orb_data['SPX'].orb_low:.2f}, Range={spx_orb_range_pct:.2f}%")
        elif 'SPY' in orb_strategy_manager.orb_data:
            # SPX is not in the ETF capture universe; proxy index ORB from SPY for macro gates
            orb_data['SPX'] = orb_strategy_manager.orb_data['SPY']
            spx_orb_range_pct = float(getattr(orb_data['SPX'], 'orb_range_pct', 0) or 0)
            log.info(
                "SPX ORB proxied from SPY (SPX not in capture universe): "
                f"High=${orb_data['SPX'].orb_high:.2f}, Low=${orb_data['SPX'].orb_low:.2f}, Range={spx_orb_range_pct:.2f}%"
            )
        else:
            log.warning("⚠️ SPX ORB data not found in ORB Strategy (SPY also missing)")
        
        # Extract QQQ ORB data (Priority 2)
        if 'QQQ' in orb_strategy_manager.orb_data:
            orb_data['QQQ'] = orb_strategy_manager.orb_data['QQQ']
            # Calculate orb_range_pct from orb_range and orb_low
            qqq_orb_range_pct = float(getattr(orb_data['QQQ'], 'orb_range_pct', 0) or 0)
            log.info(f"✅ QQQ ORB data found: High=${orb_data['QQQ'].orb_high:.2f}, Low=${orb_data['QQQ'].orb_low:.2f}, Range={qqq_orb_range_pct:.2f}%")
        else:
            log.warning("⚠️ QQQ ORB data not found in ORB Strategy")
        
        # Extract SPY ORB data (Priority 3)
        if 'SPY' in orb_strategy_manager.orb_data:
            orb_data['SPY'] = orb_strategy_manager.orb_data['SPY']
            # Calculate orb_range_pct from orb_range and orb_low
            spy_orb_range_pct = float(getattr(orb_data['SPY'], 'orb_range_pct', 0) or 0)
            log.info(f"✅ SPY ORB data found: High=${orb_data['SPY'].orb_high:.2f}, Low=${orb_data['SPY'].orb_low:.2f}, Range={spy_orb_range_pct:.2f}%")
        else:
            log.warning("⚠️ SPY ORB data not found in ORB Strategy")
        
        return orb_data
    
    def _calculate_priority_score(self, signal: DTE0Signal) -> float:
        """
        Calculate priority score for 0DTE signal (Rev 00246: Optimized for Primary Targets)
        
        Similar to ORB Strategy priority ranking, but optimized for options trading.
        Uses multi-factor scoring to identify highest-probability setups.
        
        Rev 00246 (Jan 19, 2026): Removed RS vs SPY from ALL 0DTE signals.
        0DTE underlyings are SPY/QQQ/SPX — RS vs SPY would be SPY vs SPY (no trade signal).
        
        Formula v1.1 (ALL Symbols):
        - ORB Breakout %: 35% (↑ from 30% - strong breakout = higher probability)
        - ORB Range %: 30% (↑ from 25% - wider range = better options opportunity;
          scores max out at ≥0.50% ORB width — still monotonic vs thin ranges)
        - Volume Score: 20% (same - high volume = stronger move)
        - Eligibility Score: 15% (same - already calculated by Convex Filter)
        - RS vs SPY: REMOVED (not relevant for 0DTE options)
        - Momentum: REMOVED (redundant with breakout score)
        
        Args:
            signal: DTE0Signal object
        
        Returns:
            Priority score (0.0-1.0, higher = better)
        """
        try:
            orb_signal = signal.orb_signal
            orb_high = orb_signal.get('orb_high', 0.0)
            orb_low = orb_signal.get('orb_low', 0.0)
            current_price = orb_signal.get('current_price', 0.0)
            orb_range_pct = float(orb_signal.get('orb_range_pct') or 0)
            if orb_range_pct <= 0 and orb_low > 0 and orb_high >= orb_low:
                orb_range_pct = (orb_high - orb_low) / orb_low * 100.0  # same as morning ORB capture
            # Catalog resolve — never treat invent-1.0 / stub as trusted participation.
            from modules.volume_data import resolve_equity_volume_for_gate

            vol_res = resolve_equity_volume_for_gate(orb_signal)
            volume_ratio = float(vol_res.get("ratio") or 1.0)
            volume_ratio_missing = bool(vol_res.get("missing"))
            
            # Factor 1: ORB Breakout % (30% weight)
            if signal.direction == 'LONG':
                # CALL: How far above ORB high
                if orb_high > 0:
                    breakout_pct = ((current_price - orb_high) / orb_high) * 100
                else:
                    breakout_pct = 0.0
            else:
                # PUT: How far below ORB low
                if orb_low > 0:
                    breakout_pct = ((orb_low - current_price) / orb_low) * 100
                else:
                    breakout_pct = 0.0
            
            breakout_score = _priority_subscore_from_desc_bounds(
                breakout_pct,
                "0DTE_PRIORITY_BREAKOUT_BOUNDS",
                "0DTE_PRIORITY_BREAKOUT_SCORES",
                "5,3,1,0.5,0.2",
                "1,0.85,0.7,0.5,0.3,0.15",
            )
            range_score = _priority_subscore_from_desc_bounds(
                orb_range_pct,
                "0DTE_PRIORITY_ORB_RANGE_BOUNDS",
                "0DTE_PRIORITY_ORB_RANGE_SCORES",
                "0.5,0.35,0.25,0.15",
                "1,0.85,0.7,0.5,0.3",
            )
            volume_score = _priority_subscore_from_desc_bounds(
                float(volume_ratio),
                "0DTE_PRIORITY_VOLUME_RATIO_BOUNDS",
                "0DTE_PRIORITY_VOLUME_RATIO_SCORES",
                "3,2,1.5,1.2",
                "1,0.85,0.7,0.5,0.25",
            )
            # Soft-skip invent: missing tape keeps lowest volume subscore (same as ratio≈1.0 bucket).
            # Thresholds unchanged — avoids ranking on fake participation.
            if volume_ratio_missing:
                volume_score = min(float(volume_score), 0.25)

            # Factor 4: Eligibility Score (15% weight)
            # Already calculated by Convex Eligibility Filter (0.75+ passes)
            eligibility_score = signal.eligibility_result.eligibility_score
            # Normalize to 0.0-1.0 (eligibility_score is already 0.0-1.0)
            eligibility_normalized = min(1.0, max(0.0, eligibility_score))
            
            # Rev 00246 (Jan 19, 2026): Removed RS vs SPY from ALL 0DTE signals
            # RS vs SPY is NOT used for 0DTE options ranking (not relevant for options)
            # Weights redistributed to focus on breakout, range, and volume
            
            wb = _float_env("0DTE_PRIORITY_LEGACY_W_BREAKOUT", 0.22)
            wr = _float_env("0DTE_PRIORITY_LEGACY_W_RANGE", 0.22)
            wv = _float_env("0DTE_PRIORITY_LEGACY_W_VOLUME", 0.38)
            we = _float_env("0DTE_PRIORITY_LEGACY_W_ELIGIBILITY", 0.18)
            priority_score = (
                breakout_score * wb
                + range_score * wr
                + volume_score * wv
                + eligibility_normalized * we
            )
            
            log.debug(f"   {signal.symbol} {signal.direction}: Priority Score = {priority_score:.3f} "
                     f"(Breakout: {breakout_score:.2f}, Range: {range_score:.2f}, Volume: {volume_score:.2f}, "
                     f"Eligibility: {eligibility_normalized:.2f})")
            
            return min(1.0, max(0.0, priority_score))
            
        except Exception as e:
            log.error(f"Error calculating priority score for {signal.symbol}: {e}")
            return 0.0
    
    def _rank_signals_by_priority(self, signals: List[DTE0Signal]) -> List[DTE0Signal]:
        """
        Rank 0DTE signals by priority.

        Prefer ``0DTE_PRIORITY_USE_PS_V2=true`` (default): options early-continuation
        scorer ``calculate_0dte_priority_score_v2`` (CALL + PUT). Else when
        ``0DTE_PRIORITY_USE_SO_RANK=true``, mirror ORB SO for CALL / reversed SO for PUT.
        Legacy Rev 00352 VWAP blend when both are off.
        """
        try:
            use_ps_v2 = _bool_env("0DTE_PRIORITY_USE_PS_V2", True)
            use_so_rank = _bool_env("0DTE_PRIORITY_USE_SO_RANK", True)

            for signal in signals:
                orb_signal = signal.orb_signal or {}
                rank_ctx = dict(orb_signal) if isinstance(orb_signal, dict) else {}
                convex_score = min(
                    1.0,
                    max(0.0, float(getattr(signal.eligibility_result, "eligibility_score", 0.0) or 0.0)),
                )
                signal.convex_score = convex_score

                if use_ps_v2:
                    from modules.orb_priority_score import calculate_0dte_priority_score_v2

                    # Ensure side reaches the scorer (orb_signal may lack option_type).
                    if "side" not in rank_ctx and "direction" not in rank_ctx:
                        rank_ctx["side"] = (
                            "CALL"
                            if str(signal.direction or "").upper() == "LONG"
                            else "PUT"
                        )
                    elif str(signal.direction or "").upper() == "LONG":
                        rank_ctx["side"] = "CALL"
                    elif str(signal.direction or "").upper() == "SHORT":
                        rank_ctx["side"] = "PUT"
                    if not rank_ctx.get("symbol"):
                        rank_ctx["symbol"] = signal.symbol
                    ps_val = float(calculate_0dte_priority_score_v2(rank_ctx))
                    signal.priority_score = max(0.0, ps_val)
                    signal._so_align_score = float(ps_val)
                    signal._rank_source = "0dte_ps_v2"
                    signal._continuation_tiebreak = float(
                        rank_ctx.get("continuation_quality_score") or 0.0
                    )
                    log.info(
                        "0DTE_PRIORITY_PS_V2 | symbol=%s | side=%s | score=%.4f | convex=%.3f",
                        signal.symbol,
                        signal.direction,
                        float(signal.priority_score or 0.0),
                        convex_score,
                    )
                    continue

                if use_so_rank:
                    if str(signal.direction or "").upper() == "LONG":
                        from modules.orb_priority_score import calculate_so_priority_score

                        rank_source = "so_stamped"
                        ps_val: Optional[float] = None
                        for key in ("so_priority_score", "priority_score"):
                            raw = rank_ctx.get(key)
                            if raw is None:
                                continue
                            try:
                                candidate = float(raw)
                            except (TypeError, ValueError):
                                continue
                            if candidate > 0.0:
                                ps_val = candidate
                                break
                        if ps_val is None:
                            rank_source = "so_calculated"
                            ps_val = float(calculate_so_priority_score(rank_ctx))
                        signal.priority_score = max(0.0, float(ps_val))
                        signal._so_align_score = float(ps_val)
                        signal._rank_source = rank_source
                        signal._continuation_tiebreak = float(
                            rank_ctx.get("continuation_quality_score") or 0.0
                        )
                    else:
                        from modules.orb_priority_score import calculate_so_put_priority_score

                        ps_val = float(calculate_so_put_priority_score(rank_ctx))
                        signal.priority_score = max(0.0, ps_val)
                        signal._so_align_score = float(ps_val)
                        signal._rank_source = "so_put_mirror"
                        signal._continuation_tiebreak = float(
                            rank_ctx.get("continuation_quality_score") or 0.0
                        )
                    log.info(
                        "0DTE_PRIORITY_SO_MIRROR | symbol=%s | side=%s | source=%s | score=%.4f | convex=%.3f",
                        signal.symbol,
                        signal.direction,
                        getattr(signal, "_rank_source", ""),
                        float(signal.priority_score or 0.0),
                        convex_score,
                    )
                    continue

                # Legacy Rev 00352 blend (disabled by default — VWAP penalty matrix).
                w_brk = _float_env("0DTE_PRIORITY_RANK_W_BREAKOUT_CONT", 0.30)
                w_vw = _float_env("0DTE_PRIORITY_RANK_W_VWAP_CONT", 0.24)
                w_so = _float_env("0DTE_PRIORITY_RANK_W_SO_ALIGN", 0.24)
                w_vo = _float_env("0DTE_PRIORITY_RANK_W_VOLUME", 0.18)
                w_cf = _float_env("0DTE_PRIORITY_RANK_W_CONFIDENCE", 0.08)
                vol_norm = max(0.01, _float_env("0DTE_PRIORITY_VOL_NORM", 0.35))
                so_norm = max(0.01, _float_env("0DTE_PRIORITY_SO_SCORE_NORM", 0.22))
                so_top_tier = _float_env("0DTE_PRIORITY_SO_TOP_TIER", 0.98)

                orb_high = float(orb_signal.get('orb_high', 0.0) or 0.0)
                orb_low = float(orb_signal.get('orb_low', 0.0) or 0.0)
                current_price = float(orb_signal.get('current_price', 0.0) or orb_signal.get('price', 0.0) or 0.0)
                orb_range_pct = float(orb_signal.get('orb_range_pct') or 0.0)
                if orb_range_pct <= 0 and orb_low > 0 and orb_high >= orb_low:
                    orb_range_pct = ((orb_high - orb_low) / orb_low) * 100.0
                from modules.volume_data import resolve_equity_volume_for_gate

                vol_res = resolve_equity_volume_for_gate(orb_signal)
                volume_ratio_missing = bool(vol_res.get("missing"))
                volume_ratio = (
                    0.0
                    if volume_ratio_missing
                    else float(vol_res.get("ratio") or 0.0)
                )
                macd_missing = bool(orb_signal.get("macd_missing"))
                try:
                    macd_hist = (
                        0.0
                        if macd_missing or orb_signal.get("macd_histogram") is None
                        else float(orb_signal.get("macd_histogram"))
                    )
                except (TypeError, ValueError):
                    macd_hist = 0.0
                vwap_missing = bool(orb_signal.get("vwap_missing"))
                try:
                    vwap_dist = (
                        0.0
                        if vwap_missing or orb_signal.get("vwap_distance_pct") is None
                        else float(orb_signal.get("vwap_distance_pct"))
                    )
                except (TypeError, ValueError):
                    vwap_dist = 0.0

                if signal.direction == 'LONG':
                    breakout_pct = ((current_price - orb_high) / orb_high) * 100.0 if orb_high > 0 else 0.0
                else:
                    breakout_pct = ((orb_low - current_price) / orb_low) * 100.0 if orb_low > 0 else 0.0

                breakout_score = _priority_subscore_from_desc_bounds(
                    breakout_pct,
                    "0DTE_PRIORITY_BREAKOUT_BOUNDS",
                    "0DTE_PRIORITY_BREAKOUT_SCORES",
                    "5,3,1,0.5,0.2",
                    "1,0.85,0.7,0.5,0.3,0.15",
                )
                orb_range_score = _priority_subscore_from_desc_bounds(
                    orb_range_pct,
                    "0DTE_PRIORITY_ORB_RANGE_BOUNDS",
                    "0DTE_PRIORITY_ORB_RANGE_SCORES",
                    "0.5,0.35,0.25,0.15",
                    "1,0.85,0.7,0.5,0.3",
                )
                volume_score = min(1.0, volume_ratio / vol_norm) if not volume_ratio_missing else 0.0
                vwap_cont_score = (
                    0.5 if vwap_missing else _0dte_vwap_continuation_score(vwap_dist)
                )
                so_pri_raw = orb_signal.get('priority_score') or orb_signal.get('so_priority_score')
                try:
                    so_align = min(1.0, float(so_pri_raw or 0.0) / so_norm) if so_pri_raw is not None else 0.5
                except (TypeError, ValueError):
                    so_align = 0.5
                conf_raw = float(orb_signal.get('confidence', 0.0) or 0.0)
                conf_score = min(1.0, conf_raw)

                breakout_cont = _0dte_breakout_continuation_score(breakout_pct)
                exec_penalty = _0dte_priority_execution_penalty(
                    signal.direction,
                    vwap_dist,
                    macd_hist,
                    volume_ratio,
                    breakout_pct,
                    so_align=so_align,
                )

                signal.breakout_score = breakout_score
                signal.orb_range_score = orb_range_score
                signal.volume_score = volume_score

                so_tier_boost = 1.0
                if so_align >= so_top_tier:
                    so_tier_boost = _float_env("0DTE_PRIORITY_SO_TOP_TIER_MULT", 1.10)

                base_score = (
                    breakout_cont * w_brk
                    + vwap_cont_score * w_vw
                    + min(1.0, so_align * so_tier_boost) * w_so
                    + volume_score * w_vo
                    + conf_score * w_cf
                )
                try:
                    from modules.orb_priority_geometry import is_tier_a_geometry, is_tier_b_geometry

                    orb_rank_ctx = orb_signal if isinstance(orb_signal, dict) else {}
                    if is_tier_a_geometry(orb_rank_ctx):
                        base_score *= _float_env("0DTE_PRIORITY_TIER_A_GEOMETRY_MULT", 1.12)
                    elif is_tier_b_geometry(orb_rank_ctx):
                        base_score *= _float_env("0DTE_PRIORITY_TIER_B_GEOMETRY_MULT", 0.88)
                    chase_brk = _float_env("0DTE_PRIORITY_CHASE_CONF_BREAK_PCT", 4.0)
                    if conf_score >= 0.90 and breakout_pct >= chase_brk:
                        base_score *= _float_env("0DTE_PRIORITY_HIGH_CONF_CHASE_MULT", 0.85)
                except Exception as geom_rank_err:
                    log.debug("0DTE geometry priority adjustment skipped: %s", geom_rank_err)
                signal.priority_score = max(0.0, min(1.0, base_score * exec_penalty))
                signal._continuation_tiebreak = float(breakout_cont)
                signal._vwap_continuation_score = float(vwap_cont_score)
                signal._so_align_score = float(so_align)
                signal._rank_source = "legacy_00352"

                log.debug(
                    f"0DTE_PRIORITY | {signal.symbol}_{signal.option_type_label} | "
                    f"score={signal.priority_score:.3f} | brk_cont={breakout_cont:.2f} | "
                    f"vwap_cont={vwap_cont_score:.2f} | vol={volume_score:.2f} | "
                    f"so_align={so_align:.2f} | pen={exec_penalty:.2f} | brk%={breakout_pct:.2f}"
                )
            
            # Sort by priority score (highest first). Tie-break prefers **higher early_momentum**
            # (less extended breakout path), then volume, then stable symbol/direction ordering.
            ranked_signals = sorted(
                signals,
                key=lambda s: (
                    -(s.priority_score or 0.0),
                    -(float(getattr(s, "_continuation_tiebreak", 0.0) or 0.0)),
                    -(float(getattr(s, "_so_align_score", 0.0) or 0.0)),
                    -(float(getattr(s, "_vwap_continuation_score", 0.0) or 0.0)),
                    -(getattr(s, "volume_score", 0.0) or 0.0),
                    (s.symbol or ""),
                    s.direction or "",
                ),
            )
            
            # Assign priority rank (1 = highest)
            for rank, signal in enumerate(ranked_signals, 1):
                signal.priority_rank = rank
            
            log.info(f"✅ Ranked {len(ranked_signals)} signals by priority:")
            for i, signal in enumerate(ranked_signals[:5], 1):  # Log top 5
                log.info(f"   {i}. {signal.symbol} {signal.direction} - Score: {signal.priority_score:.3f}, Eligibility: {signal.eligibility_result.eligibility_score:.2f}")
            
            return ranked_signals
            
        except Exception as e:
            log.error(f"Error ranking signals by priority: {e}")
            return signals  # Return original list if ranking fails

    def _apply_dte0_priority_execution_filters(
        self, dte0_signals: List[DTE0Signal]
    ) -> List[DTE0Signal]:
        """
        ORB1 Breakout post-rank execution filters (PS floor + cap).

        E14 shallow ★ runs in Convex (layer 4) — not here.
        Portfolio Protection 3a/3b is observe-only; these filters always enforce.
        """
        from easy0DTE.modules.dte0_execution_filters import (
            filter_dte0_signals_below_min_priority_score,
            get_dte0_signal_priority_score,
            log_dte0_priority_score_execution_filter,
            rerank_dte0_signals_by_priority,
        )

        if not dte0_signals:
            self._last_zero_ps_dropped_keys = set()
            self._last_shallow_dropped_keys = set()
            self._last_priority_dropped_keys = set()
            return []

        ps_filter = filter_dte0_signals_below_min_priority_score(dte0_signals)
        log_dte0_priority_score_execution_filter(ps_filter)
        self._last_zero_ps_dropped_keys = {
            self._orb_signal_key(s.symbol, s.direction) for s in ps_filter.dropped
        }
        self._last_shallow_dropped_keys = set()

        reranked = rerank_dte0_signals_by_priority(ps_filter.kept)
        if len(reranked) > self.max_positions:
            before = len(reranked)
            dropped_ranked = reranked[self.max_positions :]
            self._last_priority_dropped_keys = {
                self._orb_signal_key(s.symbol, s.direction) for s in dropped_ranked
            }
            for s in dropped_ranked:
                log.info(
                    "0DTE_COLLECTION_AUDIT | symbol=%s | side=%s | stage=priority_rank_cap | outcome=dropped | "
                    "reason=max_positions_%s_exceeded | rank=%s | priority_score=%.4f",
                    s.symbol,
                    s.direction,
                    self.max_positions,
                    getattr(s, "priority_rank", 0),
                    float(getattr(s, "priority_score", 0.0) or 0.0),
                )
            drop_labels = [
                f"{s.symbol}_{s.option_type_label}(r{s.priority_rank},p{s.priority_score:.3f})"
                for s in dropped_ranked[:40]
            ]
            drop_more = "..." if len(dropped_ranked) > 40 else ""
            log.info(
                "📊 0DTE cap: keeping top %s by priority (was %s after zero_ps_filter)",
                self.max_positions,
                before,
            )
            log.info(
                "  0DTE_PRIORITY_DROP | dropped=%s | max_positions=%s | not_selected=%s%s",
                len(dropped_ranked),
                self.max_positions,
                ",".join(drop_labels),
                drop_more,
            )
            reranked = reranked[: self.max_positions]
        else:
            self._last_priority_dropped_keys = set()

        log.info(
            "0DTE_PIPELINE | stage=dte0_execution_filters | kept=%d | ps_dropped=%d | cap=%d | grep=0DTE_PIPELINE",
            len(reranked),
            len(ps_filter.dropped),
            self.max_positions,
        )
        for s in reranked:
            if isinstance(s.orb_signal, dict):
                s.orb_signal["priority_score"] = float(getattr(s, "priority_score", 0.0) or 0.0)
                s.orb_signal["priority_rank"] = int(getattr(s, "priority_rank", 0) or 0)
            log.debug(
                "0DTE_EXEC_QUEUE_ROW | symbol=%s | side=%s | rank=%s | ps=%.4f",
                s.symbol,
                s.direction,
                getattr(s, "priority_rank", 0),
                get_dte0_signal_priority_score(s),
            )
        return reranked
    
    def calculate_position_sizing(
        self,
        signals: List[DTE0Signal],
        account_balance: float,
        trading_capital_pct: float = 90.0,
        max_position_pct: float = 35.0,
        max_concurrent_positions: int = 15,
        max_position_sum_usd: float = 0.0,
        open_entry_costs: float = 0.0,
        buffer_floor: float = 0.0,
        slot_count: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """
        Equal-slot 0DTE sizing shared with Trendline (sacred buffer + tradable / N).

        Ranking still selects who gets a slot (top-K). Dollars are equal 1/N of tradable
        equity unless ``0DTE_RANK_SIZING_ENABLED=true`` restores optional rank multipliers.
        """
        try:
            if not signals:
                return []

            from easyTrendline.trendline_capital_allocation import (
                buffer_pct_from_allocation,
                compute_0dte_capital_snapshot,
                compute_entry_budget,
                compute_session_buffer_floor,
            )
            from modules.unified_0dte_account import (
                effective_max_single_position_usd,
                unified_0dte_slot_count,
            )

            n_slots = max(1, int(slot_count) if slot_count is not None else unified_0dte_slot_count())
            max_single_position = effective_max_single_position_usd(
                account_balance,
                max_position_pct=max_position_pct,
                max_position_sum_usd=max_position_sum_usd,
            )

            signals_to_size = signals[:max_concurrent_positions]
            num_signals = len(signals_to_size)
            if num_signals == 0:
                return []

            buf_pct = buffer_pct_from_allocation(trading_capital_pct)
            floor = float(buffer_floor or 0.0)
            if floor <= 0.0:
                equity_guess = max(0.0, float(account_balance)) + max(0.0, float(open_entry_costs))
                floor = compute_session_buffer_floor(equity_guess, buffer_pct=buf_pct)

            snap = compute_0dte_capital_snapshot(
                cash_balance=float(account_balance),
                open_entry_costs=float(open_entry_costs or 0.0),
                allocation_pct=float(trading_capital_pct),
                slot_count=n_slots,
                buffer_floor=floor,
            )
            slot_capital = float(snap.slot_capital)
            if max_position_sum_usd and max_position_sum_usd > 0.0:
                slot_capital = min(slot_capital, float(max_position_sum_usd))
            slot_capital = min(slot_capital, max_single_position)
            available_cash = float(snap.available_cash)
            equal_budget = compute_entry_budget(slot_capital, available_cash)
            rank_sizing = _bool_env("0DTE_RANK_SIZING_ENABLED", False)

            log.info(
                "0DTE_SLOT_SIZING | signals=%d | equity_basis=%.2f | buffer=%.2f | "
                "tradable=%.2f | available_cash=%.2f | slot_count=%d | slot_capital=%.2f | "
                "entry_budget=%.2f | rank_sizing=%s | max_single=%.2f",
                num_signals,
                float(snap.equity_basis),
                float(snap.buffer_reserve),
                float(snap.tradable_equity),
                available_cash,
                n_slots,
                slot_capital,
                equal_budget,
                str(rank_sizing).lower(),
                max_single_position,
            )

            position_sizes = []
            if rank_sizing:
                # Optional overlay: greedy rank multipliers, then normalize into available cash.
                for signal in signals_to_size:
                    multiplier = _rank_position_multiplier(int(getattr(signal, "priority_rank", 0) or 0))
                    raw_value = equal_budget * multiplier
                    capped_value = min(raw_value, max_single_position)
                    position_sizes.append(
                        {
                            "signal": signal,
                            "capped_value": capped_value,
                            "multiplier": multiplier,
                        }
                    )
                total_after_caps = sum(p["capped_value"] for p in position_sizes)
                pool = min(available_cash, float(snap.tradable_equity))
                if total_after_caps > pool > 0.0:
                    norm_factor = pool / total_after_caps
                    log.info(
                        "0DTE_SLOT_SIZING | action=normalize_rank | before=%.2f | pool=%.2f | factor=%.3f",
                        total_after_caps,
                        pool,
                        norm_factor,
                    )
                    for p in position_sizes:
                        p["normalized_value"] = p["capped_value"] * norm_factor
                else:
                    for p in position_sizes:
                        p["normalized_value"] = p["capped_value"]
            else:
                remaining = available_cash
                for signal in signals_to_size:
                    budget = compute_entry_budget(slot_capital, remaining)
                    position_sizes.append(
                        {
                            "signal": signal,
                            "normalized_value": budget,
                            "multiplier": 1.0,
                        }
                    )
                    remaining = max(0.0, remaining - budget)

            sized_positions = []
            for p in position_sizes:
                signal = p["signal"]
                capital_allocated = float(p["normalized_value"])
                signal.capital_allocated = capital_allocated
                sized_positions.append(
                    {
                        "signal": signal,
                        "capital_allocated": capital_allocated,
                        "quantity": 0,
                        "position_value": capital_allocated,
                        "multiplier": p["multiplier"],
                    }
                )

            total_allocated = sum(p["capital_allocated"] for p in sized_positions)
            deployment_pct = (total_allocated / account_balance * 100) if account_balance > 0 else 0.0
            log.info(
                "0DTE_SLOT_SIZING | status=complete | total_allocated=%.2f | deployment_pct=%.1f | top3=%.2f",
                total_allocated,
                deployment_pct,
                sum(p["capital_allocated"] for p in sized_positions[:3]),
            )
            return sized_positions

        except Exception as e:
            log.error(f"Error calculating position sizes: {e}", exc_info=True)
            from easyTrendline.trendline_capital_allocation import (
                buffer_pct_from_allocation,
                compute_0dte_capital_snapshot,
                compute_session_buffer_floor,
            )
            from modules.unified_0dte_account import unified_0dte_slot_count

            n_slots = max(1, unified_0dte_slot_count())
            buf_pct = buffer_pct_from_allocation(trading_capital_pct)
            floor = compute_session_buffer_floor(float(account_balance), buffer_pct=buf_pct)
            snap = compute_0dte_capital_snapshot(
                cash_balance=float(account_balance),
                open_entry_costs=0.0,
                allocation_pct=float(trading_capital_pct),
                slot_count=n_slots,
                buffer_floor=floor,
            )
            equal = float(snap.entry_budget)
            return [
                {
                    "signal": signal,
                    "capital_allocated": equal,
                    "quantity": 0,
                    "position_value": equal,
                    "multiplier": 1.0,
                }
                for signal in signals[:max_concurrent_positions]
            ]
    
    def _calculate_capital_allocation(
        self,
        priority_rank: int,
        total_signals: int,
        available_capital: float
    ) -> float:
        """
        DEPRECATED: Use calculate_position_sizing() instead (Rev 00226)
        
        This method is kept for backward compatibility but should not be used.
        The new calculate_position_sizing() method provides full ORB Strategy-style
        position sizing with rank multipliers, normalization, and caps.
        """
        log.warning("_calculate_capital_allocation() is deprecated. Use calculate_position_sizing() instead.")
        # Fallback: Equal allocation
        return available_capital / max(1, total_signals)
    
    def calculate_momentum_score(
        self,
        signal: DTE0Signal,
        market_data: Optional[Dict[str, Any]] = None
    ) -> float:
        """
        Calculate Momentum Strength Score (0-100) (Rev 00228: Momentum Score)
        
        Combines multiple factors to determine momentum strength:
        - ORB % range: 20 points
        - Relative volume (RVOL): 20 points
        - 1m/5m candle body strength: 20 points
        - VWAP slope & distance: 20 points
        - Market alignment (SPY ↔ QQQ): 20 points
        
        Args:
            signal: DTE0Signal object
            market_data: Optional market data (SPY/QQQ prices, etc.)
        
        Returns:
            Momentum score (0.0-100.0, higher = stronger momentum)
        """
        try:
            orb_signal = signal.orb_signal
            orb_range_pct = orb_signal.get('orb_range_pct', 0.0)
            from modules.volume_data import resolve_equity_volume_for_gate

            vol_res = resolve_equity_volume_for_gate(orb_signal)
            volume_ratio = float(vol_res.get("ratio") or 1.0)
            volume_ratio_missing = bool(vol_res.get("missing"))
            current_price = orb_signal.get('current_price', 0.0)
            vwap_distance = orb_signal.get('vwap_distance', 0.0)
            
            score = 0.0

            orb_score = _priority_subscore_from_desc_bounds(
                float(orb_range_pct or 0.0),
                "0DTE_MOMENTUM_ORB_BOUNDS",
                "0DTE_MOMENTUM_ORB_POINTS",
                "0.5,0.35,0.25,0.15",
                "20,15,10,5,0",
            )
            score += orb_score

            volume_score = _priority_subscore_from_desc_bounds(
                float(volume_ratio or 0.0),
                "0DTE_MOMENTUM_VOLUME_BOUNDS",
                "0DTE_MOMENTUM_VOLUME_POINTS",
                "3,2,1.5,1.2",
                "20,15,10,5,0",
            )
            # Missing tape → 0 volume points (do not invent RVOL score).
            if volume_ratio_missing:
                volume_score = 0.0
            score += volume_score

            confidence = float(orb_signal.get('confidence', 0.0) or 0.0)
            candle_score = _priority_subscore_from_desc_bounds(
                confidence,
                "0DTE_MOMENTUM_CONF_BOUNDS",
                "0DTE_MOMENTUM_CONF_POINTS",
                "0.9,0.75,0.6,0.45",
                "20,15,10,5,0",
            )
            score += candle_score

            vd = float(vwap_distance or 0.0)
            if signal.direction == 'LONG':
                vwap_score = _momentum_vwap_long_points(vd)
            else:
                vwap_score = _momentum_vwap_short_points(vd)
            score += vwap_score

            ap = _float_env("0DTE_MOMENTUM_ALIGN_PARTIAL", 10.0)
            perf = _float_env("0DTE_MOMENTUM_ALIGN_PERFECT", 20.0)
            none_pts = _float_env("0DTE_MOMENTUM_ALIGN_NONE", 0.0)
            neutral_nd = _float_env("0DTE_MOMENTUM_ALIGN_NEUTRAL_NO_MARKETDATA", 10.0)
            if market_data:
                spy_direction = market_data.get('spy_direction', 'NONE')
                qqq_direction = market_data.get('qqq_direction', 'NONE')

                if signal.direction == 'LONG':
                    if spy_direction == 'UP' and qqq_direction == 'UP':
                        alignment_score = perf
                    elif (spy_direction == 'UP' and qqq_direction == 'NONE') or (
                        spy_direction == 'NONE' and qqq_direction == 'UP'
                    ):
                        alignment_score = ap
                    else:
                        alignment_score = none_pts
                else:
                    if spy_direction == 'DOWN' and qqq_direction == 'DOWN':
                        alignment_score = perf
                    elif (spy_direction == 'DOWN' and qqq_direction == 'NONE') or (
                        spy_direction == 'NONE' and qqq_direction == 'DOWN'
                    ):
                        alignment_score = ap
                    else:
                        alignment_score = none_pts
            else:
                alignment_score = neutral_nd
            score += alignment_score
            
            # Store momentum score in signal
            signal.momentum_score = score
            
            return score
            
        except Exception as e:
            log.error(f"Error calculating momentum score: {e}")
            return 0.0
    
    def _apply_hard_gate_before_rank(self, dte0_signals: List[DTE0Signal]) -> List[DTE0Signal]:
        """
        Run Hard Gate on the post-Convex pool before priority rank + Gate 3c cap.

        Ensures the top-N execution batch is filled from symbols that pass Hard Gate,
        with lower-priority Convex passers backfilling slots dropped by Hard Gate.
        """
        from zoneinfo import ZoneInfo

        self._last_convex_qualified_count = len(dte0_signals)
        self._last_convex_pass_keys = {
            self._orb_signal_key(s.symbol, s.direction) for s in dte0_signals
        }
        self._last_hard_gate_passed_keys = set()
        self._last_hard_gate_failed_keys = set()
        self._last_hard_gate_failed_reasons = {}

        if not dte0_signals:
            self._last_hard_gate_passed_count = 0
            return []

        hard_gate_current_time = datetime.now(ZoneInfo("America/Los_Angeles"))
        max_allowed_spread_pct = _float_env("0DTE_HARD_GATE_MAX_SPREAD_PCT", 5.0)
        survivors: List[DTE0Signal] = []
        passed_labels: List[str] = []

        for signal in dte0_signals:
            key = self._orb_signal_key(signal.symbol, signal.direction)
            is_valid, reason = self.validate_hard_gate(
                signal=signal,
                current_time=hard_gate_current_time,
                max_allowed_spread_pct=max_allowed_spread_pct,
            )
            signal.hard_gate_prevalidated = bool(is_valid)
            signal.hard_gate_reason = None if is_valid else str(reason)
            if is_valid:
                self._last_hard_gate_passed_keys.add(key)
                survivors.append(signal)
                passed_labels.append(f"{signal.symbol} {signal.option_type_label}")
            else:
                self._last_hard_gate_failed_keys.add(key)
                self._last_hard_gate_failed_reasons[key] = str(reason)

        n_failed = len(self._last_hard_gate_failed_keys)
        self._last_hard_gate_passed_count = len(survivors)
        log.info(
            "HARD_GATE_SUMMARY | passed=%d failed=%d total=%d | passed_symbols=%s",
            len(survivors),
            n_failed,
            len(dte0_signals),
            ",".join(passed_labels[:20]),
        )
        return survivors

    @staticmethod
    def _log_hard_gate_reject(signal: DTE0Signal, gate: str, reason: str) -> None:
        """Grep-friendly single line: 0DTE_HARD_GATE_REJECT | gate=symbol_list|time_window|volume|exception"""
        msg = (reason or "").replace("\n", " ").strip()
        if len(msg) > 240:
            msg = msg[:237] + "..."
        log.info(
            f"  0DTE_HARD_GATE_REJECT | symbol={signal.symbol} | opt={signal.option_type_label} "
            f"| gate={gate} | reason={msg}"
        )
    
    def validate_hard_gate(
        self,
        signal: DTE0Signal,
        current_time: datetime,
        max_allowed_spread_pct: float = 5.0,
        volume_multiplier: Optional[float] = None,
        session_avg_volume: Optional[float] = None
    ) -> Tuple[bool, str]:
        """
        Hard Gate validation before 0DTE execution (Rev 00228: Hard Gate System)
        
        If ANY of these fail → NO 0DTE TRADE:
        - Symbol in 0DTE target list (or index fallback SPY/QQQ/IWM/SPX)
        - Time: watch mode uses Shared DISCOVERY_ORB1_TL0_*_PT; batch SC uses 0DTE_HARD_GATE_ET_*
        - Volume ≥ session_avg * volume_multiplier (or volume_ratio vs multiplier)
        
        ORB width is not hard-rejected here: Convex already gates minimum ORB range (pc_oi mode); priority
        rewards wider opening ranges; very wide ORB only logs a warning below (proxy for spread risk).
        Options execution applies bid/ask and OI guardrails separately.
        
        Args:
            signal: DTE0Signal object
            current_time: Current datetime
            max_allowed_spread_pct: Reserved API (not used in gate logic; spreads enforced when building orders)
            volume_multiplier: Volume multiplier for validation (default 1.0)
            session_avg_volume: Session average volume (optional)
        
        Returns:
            Tuple of (is_valid, reason)
        """
        try:
            # Gate 1: Symbol Validation
            # Rev 00304: Use full 0DTE target list for symbol validation instead of hard-coded indexes only.
            # This ensures we can trade all symbols configured in 0dte_list.csv (e.g., NEBX, GOOGL, ASML),
            # while still defaulting to the index set if the target list is unavailable.
            target_list = set(getattr(self, "target_symbols", []) or [])
            base_allowed = _hard_gate_base_index_set()
            allowed_symbols = target_list or base_allowed
            if signal.symbol not in allowed_symbols:
                msg = f"Symbol {signal.symbol} not in allowed 0DTE target list {allowed_symbols}"
                self._log_hard_gate_reject(signal, "symbol_list", msg)
                return False, msg
            
            # Gate 2: Time window — watch mode shares DISCOVERY_ORB1_TL0_* (Shared.env §5).
            # Batch SC rollback still uses 0DTE_HARD_GATE_ET_* (default 10:30–10:40 ET).
            from pytz import timezone
            et_tz = timezone('US/Eastern')
            pt_tz = timezone('US/Pacific')
            
            if current_time.tzinfo is None:
                # Assume PT if no timezone
                current_time = pt_tz.localize(current_time)
            
            current_et = current_time.astimezone(et_tz)
            et_hour = current_et.hour
            et_minute = current_et.minute
            pt_clock = current_time.astimezone(pt_tz).time()

            from modules.session_clock import (
                discovery_mode_is_watch,
                orb1_tl0_session_open,
                orb1_tl0_window_pt,
                pt_bound_as_et_hhmm,
            )

            if discovery_mode_is_watch():
                if not orb1_tl0_session_open(pt_clock):
                    start_pt, end_pt = orb1_tl0_window_pt()
                    ws = pt_bound_as_et_hhmm(start_pt, current_time)
                    we = pt_bound_as_et_hhmm(end_pt, current_time)
                    msg = (
                        f"Time {et_hour:02d}:{et_minute:02d} ET outside discovery window "
                        f"({ws[0]:02d}:{ws[1]:02d}-{we[0]:02d}:{we[1]:02d} ET / "
                        f"{start_pt.strftime('%H:%M')}-{end_pt.strftime('%H:%M')} PT)"
                    )
                    self._log_hard_gate_reject(signal, "time_window", msg)
                    return False, msg
            else:
                window_start = _parse_et_hhmm("0DTE_HARD_GATE_ET_START", "10:30")
                window_end = _parse_et_hhmm("0DTE_HARD_GATE_ET_END", "10:40")
                current_time_tuple = (et_hour, et_minute)
                if current_time_tuple < window_start or current_time_tuple > window_end:
                    msg = (
                        f"Time {et_hour:02d}:{et_minute:02d} ET outside execution window "
                        f"({window_start[0]:02d}:{window_start[1]:02d}-"
                        f"{window_end[0]:02d}:{window_end[1]:02d} ET)"
                    )
                    self._log_hard_gate_reject(signal, "time_window", msg)
                    return False, msg
            
            # Gate 3: ORB width diagnostic only (no reject). Options spread/OI enforced at execution.
            orb_signal = signal.orb_signal
            orb_range_pct = orb_signal.get('orb_range_pct', 0.0)
            wide_orb_warn = float(os.getenv("0DTE_HARD_GATE_WIDE_ORB_WARN_PCT", "2.0"))
            if orb_range_pct > wide_orb_warn:
                log.warning(
                    f"⚠️ Wide ORB range {orb_range_pct:.2f}% for {signal.symbol} — "
                    f"may indicate wide option spreads; chain guardrails still apply at execution"
                )
            
            # Gate 4: Volume Validation (equity session ratio only — via volume_data catalog)
            from modules.volume_data import (
                VOLUME_KIND_EQUITY_SESSION_RATIO,
                resolve_equity_volume_for_gate,
            )

            vol_res = resolve_equity_volume_for_gate(signal)
            volume_ratio = float(vol_res.get("ratio") or 1.0)
            volume_ratio_missing = bool(vol_res.get("missing"))
            effective_mult = _effective_hard_gate_volume_multiplier(signal, volume_multiplier)
            base_mult = float(
                volume_multiplier
                if volume_multiplier is not None
                else os.getenv("0DTE_HARD_GATE_VOLUME_MULTIPLIER", "1.0")
            )
            if volume_ratio_missing:
                # ORB1/TL0 first hour: ratio needs ≥2 usable 1m bars. Do not
                # 1h-block. Side gates (dir%, skip signals, measured vol floor)
                # still apply. PO entry snaps hydrate session/chain volume.
                log.info(
                    "  0DTE_HARD_GATE_VOLUME_DATA_GAP | symbol=%s | opt=%s | "
                    "volume_ratio=missing | volume_kind=%s | missing=true | "
                    "action=skip_missing_admit | grep=0DTE_HARD_GATE|SESSION_TECH",
                    signal.symbol,
                    signal.option_type_label,
                    vol_res.get("kind") or VOLUME_KIND_EQUITY_SESSION_RATIO,
                )
            elif (
                effective_mult < base_mult
                and volume_ratio >= effective_mult
            ):
                log.info(
                    "  0DTE_HARD_GATE_CONTINUATION_VOL_ALIGN | symbol=%s | opt=%s | "
                    "volume_ratio=%.2f | volume_kind=%s | base_threshold=%.2f | "
                    "effective_threshold=%.2f",
                    signal.symbol,
                    signal.option_type_label,
                    volume_ratio,
                    VOLUME_KIND_EQUITY_SESSION_RATIO,
                    base_mult,
                    effective_mult,
                )
            if session_avg_volume and not volume_ratio_missing:
                # Compare to session average
                current_volume = orb_signal.get('volume', 0)
                if current_volume < (session_avg_volume * effective_mult):
                    msg = (
                        f"Volume {current_volume} below threshold "
                        f"{session_avg_volume * effective_mult}"
                    )
                    self._log_hard_gate_reject(signal, "volume", msg)
                    return False, msg
            elif not volume_ratio_missing:
                # Use volume ratio as proxy
                if volume_ratio < effective_mult:
                    msg = (
                        f"Volume ratio {volume_ratio:.2f} below threshold {effective_mult:.2f}"
                    )
                    self._log_hard_gate_reject(signal, "volume", msg)
                    return False, msg
            
            # All gates passed
            return True, "All hard gate checks passed"
            
        except Exception as e:
            log.error(f"Error in hard gate validation: {e}")
            msg = f"Hard gate validation error: {e}"
            try:
                self._log_hard_gate_reject(signal, "exception", msg)
            except Exception:
                pass
            return False, msg

    @staticmethod
    def _spread_type_for_strategy(strategy_type: str) -> str:
        """Map strategy label to execution structure type."""
        st = str(strategy_type or "").strip().lower()
        if st in {"lotto", "long_call", "long_put"}:
            return "single_leg"
        if st in {"momentum_scalper", "itm_probability_spread", "debit_spread"}:
            return "debit"
        if st == "credit_spread":
            return "credit"
        return "debit"

    def _validate_structure_mapping(self) -> None:
        """Guardrail log to catch drift between strategy labels and structure mapping."""
        expected = {
            "lotto": "single_leg",
            "long_call": "single_leg",
            "long_put": "single_leg",
            "momentum_scalper": "debit",
            "itm_probability_spread": "debit",
            "debit_spread": "debit",
            "credit_spread": "credit",
        }
        drift = []
        for strategy, spread_type in expected.items():
            actual = self._spread_type_for_strategy(strategy)
            if actual != spread_type:
                drift.append((strategy, spread_type, actual))
        if drift:
            log.error("0DTE_STRUCTURE_MAPPING_DRIFT | mismatches=%s", drift)
        else:
            log.info("0DTE_STRUCTURE_MAPPING_OK | mapping=%s", expected)

    @staticmethod
    def _log_strategy_to_execution_map(
        strategy_type: str, direction: str, spread_type: str = "debit"
    ) -> None:
        """Log deterministic mapping from Level-2 strategy label to structural execution type."""
        from .options_execution_normalize import orb_strategy_to_structural_execution_type

        structural = orb_strategy_to_structural_execution_type(
            strategy_type, direction, spread_type=spread_type
        )
        log.info(
            "OPTIONS_EXECUTOR | stage=strategy_execution_map | strategy_type=%s | direction=%s | "
            "spread_type=%s | structural_execution_type=%s",
            strategy_type,
            direction,
            spread_type,
            structural,
        )
    
    def _select_strategy_type(
        self,
        direction: str,
        orb_signal: Dict[str, Any],
        eligibility_result: Any,
        orb_range_pct: float,
        momentum_score: float
    ) -> str:
        """
        Select strategy type based on market conditions and momentum (Rev 00227: Level 2 Options Strategies)
        
        Strategy Selection Logic (single-leg-first):
        1) lotto: adjusted_momentum>=88, confidence>=0.92, volume_ratio>=1.6, orb_range_pct>=0.35
        2) long_call/long_put: adjusted_momentum>=76, confidence>=0.86, volume_ratio>=1.3, orb_range_pct>=0.30
        3) momentum_scalper: adjusted_momentum>=66
        4) itm_probability_spread: adjusted_momentum>=48
        5) debit_spread: fallback when not weak/chop
        6) no_trade: weak/chop conditions
        
        Args:
            direction: 'LONG' or 'SHORT'
            orb_signal: Original ORB signal dictionary
            eligibility_result: ConvexEligibilityResult object
            orb_range_pct: ORB range percentage
            momentum_score: Momentum Strength Score (0-100)
        
        Returns:
            Strategy type string: 'debit_spread', 'momentum_scalper', 'itm_probability_spread', 
                                  'long_call', 'long_put', 'lotto', or 'no_trade'
        """
        try:
            # Get additional market conditions from ORB signal
            from modules.volume_data import resolve_equity_volume_for_gate

            vol_res = resolve_equity_volume_for_gate(orb_signal)
            volume_ratio_missing = bool(vol_res.get("missing"))
            # Never use invent 1.0 for structure floors when tape is missing.
            volume_ratio = (
                0.0
                if volume_ratio_missing
                else float(vol_res.get("ratio") or 0.0)
            )
            confidence = orb_signal.get('confidence', 0.0)
            vwap_distance = float(orb_signal.get('vwap_distance', 0.0) or 0.0)
            eligibility_score = eligibility_result.eligibility_score if hasattr(eligibility_result, 'eligibility_score') else 0.75
            direction_u = str(direction or "").upper()

            # Rev 00349: Directional-strength momentum boost for strategy selection only.
            # Keeps collection scoring unchanged while helping strong trend names choose long-option/lotto paths.
            # CRITICAL: do not apply volume boost when equity volume_ratio is missing/stub.
            directional_strength_boost = 0.0
            if (not volume_ratio_missing) and volume_ratio >= _float_env(
                "0DTE_STRATEGY_BOOST_MIN_VOLUME_RATIO", 1.35
            ):
                directional_strength_boost += _float_env("0DTE_STRATEGY_BOOST_VOLUME_POINTS", 6.0)
            if confidence >= _float_env("0DTE_STRATEGY_BOOST_MIN_CONFIDENCE", 0.88):
                directional_strength_boost += _float_env("0DTE_STRATEGY_BOOST_CONFIDENCE_POINTS", 6.0)
            if orb_range_pct >= _float_env("0DTE_STRATEGY_BOOST_MIN_ORB_RANGE_PCT", 0.35):
                directional_strength_boost += _float_env("0DTE_STRATEGY_BOOST_ORB_POINTS", 4.0)
            if (direction_u == "LONG" and vwap_distance >= _float_env("0DTE_STRATEGY_BOOST_MIN_VWAP_DIST", 0.30)) or (
                direction_u == "SHORT" and vwap_distance <= -_float_env("0DTE_STRATEGY_BOOST_MIN_VWAP_DIST", 0.30)
            ):
                directional_strength_boost += _float_env("0DTE_STRATEGY_BOOST_VWAP_POINTS", 4.0)
            adjusted_momentum = min(100.0, float(momentum_score) + float(directional_strength_boost))
            log.debug(
                "0DTE_STRATEGY_SELECTOR | direction=%s | momentum_raw=%.1f | momentum_adj=%.1f | "
                "boost=%.1f | vol=%.2f | conf=%.2f | vwap_dist=%.2f | orb_range=%.2f",
                direction_u,
                momentum_score,
                adjusted_momentum,
                directional_strength_boost,
                volume_ratio,
                confidence,
                vwap_distance,
                orb_range_pct,
            )
            
            symbol = str(orb_signal.get("symbol", ""))
            current_price = float(orb_signal.get("price") or orb_signal.get("current_price") or 0.0)
            orb_high = float(orb_signal.get("orb_high") or orb_signal.get("opening_range_high") or 0.0)
            orb_low = float(orb_signal.get("orb_low") or orb_signal.get("opening_range_low") or 0.0)
            orb_width = max(abs(orb_high - orb_low), 1e-6)

            # Distance from ORB boundary in units of ORB width.
            if direction_u == "LONG":
                breakout_distance = max(0.0, current_price - orb_high) if orb_high > 0 else 0.0
            else:
                breakout_distance = max(0.0, orb_low - current_price) if orb_low > 0 else 0.0
            breakout_distance_ratio = breakout_distance / orb_width if orb_width > 0 else 0.0

            strong_momentum_min = _float_env("0DTE_STRONG_MOMENTUM_MIN", 70.0)
            strong_distance_min = _float_env("0DTE_STRONG_BREAKOUT_DISTANCE_RATIO_MIN", 0.08)
            strong_conf_min = _float_env("0DTE_STRONG_MIN_CONFIDENCE", 0.72)
            strong_vol_min = _float_env("0DTE_STRONG_MIN_VOLUME_RATIO", 1.05)
            strong_directional_signal = (
                breakout_distance_ratio >= strong_distance_min
                and adjusted_momentum >= strong_momentum_min
                and confidence >= strong_conf_min
                and volume_ratio >= strong_vol_min
            )

            moderate_momentum_min = _float_env("0DTE_MODERATE_MOMENTUM_MIN", 55.0)
            moderate_distance_min = _float_env("0DTE_MODERATE_BREAKOUT_DISTANCE_RATIO_MIN", 0.02)
            moderate_conf_min = _float_env("0DTE_MODERATE_MIN_CONFIDENCE", 0.58)
            moderate_vol_min = _float_env("0DTE_MODERATE_MIN_VOLUME_RATIO", 0.90)
            moderate_directional_signal = (
                breakout_distance_ratio >= moderate_distance_min
                and adjusted_momentum >= moderate_momentum_min
                and confidence >= moderate_conf_min
                and volume_ratio >= moderate_vol_min
            )

            weak_momentum_max = _float_env("0DTE_WEAK_MOMENTUM_MAX", 50.0)
            weak_distance_max = _float_env("0DTE_WEAK_BREAKOUT_DISTANCE_RATIO_MAX", 0.015)
            weak_conf_min = _float_env("0DTE_WEAK_MIN_CONFIDENCE", 0.55)
            weak_signal = (
                adjusted_momentum <= weak_momentum_max
                or breakout_distance_ratio <= weak_distance_max
                or confidence < weak_conf_min
                or eligibility_score < _float_env("0DTE_WEAK_MIN_ELIGIBILITY", 0.72)
            )
            log.info(
                "0DTE_SIGNAL_METRICS | symbol=%s | momentum=%.2f | distance=%.4f | conf=%.2f | vol=%.2f",
                symbol,
                adjusted_momentum,
                breakout_distance_ratio,
                confidence,
                volume_ratio,
            )

            so_ps_raw = orb_signal.get("so_priority_score")
            if so_ps_raw is None:
                so_ps_raw = orb_signal.get("priority_score")
            try:
                so_ps_val = float(so_ps_raw or 0.0)
            except (TypeError, ValueError):
                so_ps_val = 0.0

            lotto_first = _bool_env("0DTE_ORB_LOTTO_FIRST", True)
            spread_primary = _bool_env("0DTE_ORB_SPREAD_PRIMARY_ENABLED", False)
            if lotto_first:
                spread_primary = False
            so_debit_min = _float_env("0DTE_STRATEGY_SO_DEBIT_SPREAD_MIN_PS", 0.17)
            if (
                spread_primary
                and not lotto_first
                and direction_u == "LONG"
                and so_ps_val >= so_debit_min
            ):
                strategy_type = "debit_spread"
                log.info(
                    "0DTE_STRUCTURE_SELECTION | symbol=%s | strategy_type=%s | reason=so_priority_debit_spread | "
                    "so_ps=%.4f | min=%.4f | spread_primary=true",
                    symbol,
                    strategy_type,
                    so_ps_val,
                    so_debit_min,
                )
                self._log_strategy_to_execution_map(
                    strategy_type,
                    direction,
                    self._spread_type_for_strategy(strategy_type),
                )
                return strategy_type

            if lotto_first:
                strategy_type = "single_leg"
                log.info(
                    "0DTE_STRUCTURE_SELECTION | symbol=%s | strategy_type=%s | reason=orb_single_leg_first_default | "
                    "so_ps=%.4f | direction=%s | momentum_adj=%.1f | weak_signal=%s",
                    symbol,
                    strategy_type,
                    so_ps_val,
                    direction_u,
                    adjusted_momentum,
                    str(bool(weak_signal)).lower(),
                )
                log.info(
                    "0DTE_STRATEGY_SELECTED | symbol=%s | strategy=%s",
                    symbol,
                    strategy_type,
                )
                self._log_strategy_to_execution_map(
                    strategy_type,
                    direction,
                    "single_leg",
                )
                return strategy_type

            if strong_directional_signal:
                strategy_type = "long_call" if direction_u == "LONG" else "long_put"
            elif moderate_directional_signal and self.enable_lotto_sleeve:
                lotto_min_ps = _float_env("0DTE_LOTTO_MIN_SO_PRIORITY_SCORE", 0.22)
                if so_ps_val >= lotto_min_ps:
                    strategy_type = "single_leg"
                else:
                    strategy_type = "momentum_scalper"
                    log.info(
                        "0DTE_STRUCTURE_SELECTION | symbol=%s | strategy_type=%s | reason=lotto_ps_floor | "
                        "so_ps=%.4f | min=%.4f",
                        symbol,
                        strategy_type,
                        so_ps_val,
                        lotto_min_ps,
                    )
            elif weak_signal:
                strategy_type = "momentum_scalper"  # spread fallback only
            else:
                strategy_type = "itm_probability_spread"  # spread fallback only

            log.info(
                "0DTE_STRUCTURE_SELECTION | symbol=%s | strategy_type=%s | momentum=%.1f | confidence=%.2f | volume_ratio=%.2f | breakout_dist_ratio=%.3f",
                symbol,
                strategy_type,
                adjusted_momentum,
                confidence,
                volume_ratio,
                breakout_distance_ratio,
            )
            log.info(
                "0DTE_STRATEGY_SELECTED | symbol=%s | strategy=%s",
                symbol,
                strategy_type,
            )
            self._log_strategy_to_execution_map(
                strategy_type,
                direction,
                "single_leg" if strategy_type in {"long_call", "long_put", "lotto"} else "debit",
            )
            return strategy_type
            
        except Exception as e:
            log.error(f"Error selecting strategy type: {e}")
            # Keep single-leg as safe default path on selector errors.
            fallback_strategy = "long_call" if str(direction or "").upper() == "LONG" else "long_put"
            log.info(
                "0DTE_STRATEGY_SELECTED | symbol=%s | strategy=%s",
                str(orb_signal.get("symbol", "")),
                fallback_strategy,
            )
            self._log_strategy_to_execution_map(fallback_strategy, direction, "single_leg")
            return fallback_strategy
    
    def reset_daily(self):
        """Reset daily state"""
        self.orb_signals = []
        self.eligible_signals = []
        self.dte0_signals = []
        log.info("0DTE Strategy Manager daily reset complete")
