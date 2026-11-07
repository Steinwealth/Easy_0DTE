#!/usr/bin/env python3
"""Models for the Breakout 0DTE path (full 0dte_list @ 7:30 → later first-hour high/low break)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class BreakoutCandidateState(str, Enum):
    WAITING_FOR_BREAK = "waiting_for_break"
    WAITING_FOR_CONFIRMATION = "waiting_for_confirmation"
    READY_TO_EXECUTE = "ready_to_execute"
    EXECUTED = "executed"
    INVALIDATED = "invalidated"
    EXPIRED = "expired"


@dataclass(frozen=True)
class BreakoutConfig:
    """Tunable Breakout gates (loaded from Breakout0DTE.env)."""

    # Sole Layer A floor (TL parity): first print ≥0.08% beyond the box → QG.
    # Continuation / FT / chop keys stay on the dataclass for stamps; not admit.
    min_break_distance_pct: float = 0.0008  # break_min 0.08%
    # break_max at QG (fraction). Desk cap 2.50%. None/0 disables scorer stamp.
    max_break_distance_pct: float = 0.025
    min_follow_through_bars: int = 1  # deleted as admit; stamp only
    min_continuation_pct: float = 0.0  # deleted as admit; stamp only
    post_break_chop_max_range_pct: float = 0.0  # deleted as admit; stamp only
    reclaim_invalidates: bool = True

    # Quality (aligned with Trendline / ORB Convex spirit)
    min_volume_ratio: float = 0.12
    # Sole hard VWAP/vol = GATE_BO_* (0DTEQualityGates). Scorer flags default off.
    volume_ratio_gate_enabled: bool = False
    require_vwap_alignment: bool = False
    vwap_extension_reject_pct: float = 0.15  # ±15% like Convex (legacy scorer only)
    call_min_rsi: float = 0.0
    put_max_rsi: float = 100.0
    min_quality_score: float = 0.0  # unused as admit; extension ranker is observe-only
    pc_oi_confirmation_enabled: bool = True
    # Fail closed when VWAP/vol missing if those gates are on (Jul 27 stubs).
    vwap_missing_fail_open: bool = False
    volume_missing_fail_closed: bool = True

    # Soft entry window from first material break (mirror TRENDLINE_SIMPLE_ENTRY_WINDOW_SEC).
    # Expire CONFIRM/READY → re-arm WAIT_BREAK (no 2h side cooldown).
    simple_entry_window_sec: float = 300.0

    # Contract OTM gate @ execute (Jul 27: winners ~8% vs never-armed ~18%)
    otm_gate_enabled: bool = True
    prefer_max_otm_pct: float = 0.055  # Jul 28 lock — ≤5.5%
    research_max_otm_pct: float = 0.055  # hard reject above prefer (prefer==hard)

    # Cheap-lotto size guard (entry < $0.15 + large qty unless near ATM)
    cheap_lotto_guard_enabled: bool = True
    cheap_lotto_max_premium: float = 0.15
    cheap_lotto_max_qty: int = 8
    cheap_lotto_near_atm_otm_pct: float = 0.05
    cheap_lotto_mode: str = "reject"  # reject | downsize
    cheap_lotto_downsize_to: int = 1

    # Session
    no_new_entries_after_pt: str = "12:30"
    watch_interval_sec: float = 30.0
    opening_watch_interval_sec: float = 15.0
    watch_symbols_per_tick: int = 24
    # Legacy (unused): CONFIRM no longer starves WAIT_BREAK under tier-list rotate.
    wait_break_reserve: int = 0

    # Legacy seed band knobs (kept for config compat; activate no longer Mid-filters)
    seed_above_high_mult: float = 1.001
    seed_below_low_mult: float = 0.999


@dataclass
class BreakoutCandidate:
    """Symbol on Breakout watch — levels are first-hour (6:30–7:30) high/low."""

    symbol: str
    orb_high: float  # first-hour high (field name kept for adapter/compat)
    orb_low: float  # first-hour low
    seeded_at: datetime
    price_at_seed: float
    state: BreakoutCandidateState = BreakoutCandidateState.WAITING_FOR_BREAK
    option_side: Optional[str] = None  # call / put once break direction known
    break_price: Optional[float] = None
    break_ts: Optional[datetime] = None
    material_break_close: Optional[float] = None
    post_break_high: Optional[float] = None
    post_break_low: Optional[float] = None
    follow_through_bars: int = 0
    last_price: Optional[float] = None
    last_quote_ts: Optional[datetime] = None
    volume_ratio: float = 1.0
    vwap: Optional[float] = None
    vwap_distance_pct: Optional[float] = None
    rsi: Optional[float] = None
    failed_reasons: List[str] = field(default_factory=list)
    quality_score: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def orb_mid(self) -> float:
        return (float(self.orb_high) + float(self.orb_low)) / 2.0


@dataclass(frozen=True)
class BreakoutTradeSignal:
    """Trade-ready Breakout signal (CALL above first-hour high, PUT below low)."""

    symbol: str
    option_side: str  # call | put
    emitted_at: datetime
    orb_high: float
    orb_low: float
    break_price: float
    entry_underlying_price: float
    confidence: float
    priority_score: float
    quality_score: float
    break_distance_pct: float
    continuation_pct: float
    follow_through_bars: int
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def direction_label(self) -> str:
        return "CALL" if str(self.option_side).lower() == "call" else "PUT"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
