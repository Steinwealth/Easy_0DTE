#!/usr/bin/env python3
"""
Easy Trendline 0DTE strategy models.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class TrendlineDirection(str, Enum):
    """
    Trendline *geometry* (line type), not ORB signal side.

    BULL  = ascending support (ORB low + higher lows): valid trigger is breakdown
            below the line → long PUT.
    BEAR  = descending resistance (ORB high + lower highs): valid trigger is
            breakout above the line → long CALL.

    Do not infer this from SO/0DTE collection side or a fixed long bias; it must
    come from pre-7:30 structure selection.
    """

    BULL = "bull"
    BEAR = "bear"


class TrendlineSetupType(str, Enum):
    """User-facing setup semantics (maps 1:1 to TrendlineDirection geometry)."""

    ASCENDING_SUPPORT = "ascending_support"
    DESCENDING_RESISTANCE = "descending_resistance"

    @classmethod
    def from_direction(cls, direction: "TrendlineDirection") -> "TrendlineSetupType":
        if direction == TrendlineDirection.BULL:
            return cls.ASCENDING_SUPPORT
        return cls.DESCENDING_RESISTANCE


class BreakStatus(str, Enum):
    """Break detection status."""

    NONE = "none"
    DETECTED = "detected"
    REJECTED = "rejected"


class MomentumStatus(str, Enum):
    """Post-break momentum confirmation status."""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    FAILED = "failed"


class TrendlineCandidateState(str, Enum):
    """State machine status for each trendline candidate."""

    WAITING_FOR_BUILD = "waiting_for_build"
    WAITING_FOR_BREAK = "waiting_for_break"
    WAITING_FOR_CONFIRMATION = "waiting_for_confirmation"
    REVERSAL_WATCH = "reversal_watch"
    READY_TO_EXECUTE = "ready_to_execute"
    EXECUTED = "executed"
    INVALIDATED = "invalidated"
    EXPIRED = "expired"


class TrendlineReasonCode(str, Enum):
    """Canonical reason codes for trendline lifecycle diagnostics."""

    BUILD_FAILED = "build_failed"
    MISSING_ORB_CONTEXT = "missing_orb_context"
    INSUFFICIENT_PRE730_BARS = "insufficient_pre730_bars"
    MICRO_BREAK_REJECTED = "micro_break_rejected"
    FAILED_HOLD = "failed_hold"
    NO_STRUCTURE_HOLD = "no_structure_hold"
    NO_LOCAL_CONTINUATION = "no_local_continuation"
    NO_POST_BREAK_STRUCTURE = "no_post_break_structure"
    POST_CONTINUE_CHOP_GATE = "post_continue_chop_gate"
    MOMENTUM_TOO_WEAK = "momentum_too_weak"
    LATE_CONFIRMATION = "late_confirmation"
    RETRACE_VIOLATION = "retrace_violation"
    SECOND_BREAK_NOT_ALLOWED = "second_break_not_allowed"
    EXPIRED_CUTOFF = "expired_cutoff"
    DEDUPE_BLOCK = "dedupe_block"
    EXECUTED_VALID_CONFIRMATION = "executed_valid_confirmation"
    NO_VALID_TRENDLINE_STRUCTURE = "no_valid_trendline_structure"
    AMBIGUOUS_STRUCTURE = "ambiguous_structure"


@dataclass(frozen=True)
class TrendlineConfig:
    """
    Trendline 0DTE configuration (simple-only surface).

    Pre-draw selector/build, watch/session ops, and nine ``TRENDLINE_SIMPLE_*`` post-draw knobs
    are loaded from env via ``trendline_config_loader.load_trendline_config_from_env``.
    """

    # --- Session / build ---
    build_time_pt: str = "07:30"
    expiration_time_pt: str = "12:30"
    min_break_pct: float = 0.001
    use_atr_break: bool = False  # Aug 12 prune Wave A
    atr_break_multiplier: float = 0.25
    min_anchor_distance_minutes: int = 3
    min_structure_bars: int = 3
    min_structure_seconds: float = 90.0
    max_active_minutes: float = 180.0
    # Max age from line draw/inject (built_at) during which a first break may activate a trade.
    # After this, ARMED TL1–TL10 lines expire — late-day intersections do not enter.
    # TL0 ignores this clock and hard-expires at 07:10 PT.
    trade_activate_max_age_sec: float = 10800.0
    no_new_entries_after_pt: str = "12:30"
    min_expected_move_pct: float = 0.0015

    # --- Pre-draw selector v3 (kept for dual-lane scoring helpers; not a TL1 winner) ---
    control_line_v3_enabled: bool = False  # Jul 30: TL1 winner is structure envelope only
    control_line_min_score: float = 25.0
    control_line_max_candidates_per_side: int = 24
    dual_lane_min_score_ratio: float = 0.70
    compression_call_band_low: float = 0.20
    compression_call_band_high: float = 0.55
    compression_strength_threshold: float = 40.0
    compression_bonus_points: float = 15.0
    compression_persist_min_strength: float = 35.0
    corridor_pct_floor: float = 0.0008
    use_corridor_break_detection: bool = True
    orb_bias_mse_soft_ratio: float = 8.0
    primary_lane_enforce_geometry_alignment: bool = True
    false_break_bypass_counter_orb_only: bool = False
    extreme_orb_upper_min_pct_from_low: float = 0.65
    extreme_orb_lower_max_pct_from_low: float = 0.35
    within_orb_prefer_descending_call_on_unclear: bool = False  # Aug 12 prune Wave A
    within_orb_prefer_descending_call_min_pct: float = 0.20
    within_orb_prefer_descending_call_max_pct: float = 0.55

    # --- Geometry Phase 1 (7:30 draw path) ---
    geometry_phase1_enabled: bool = True
    max_line_distance_at_cutoff_pct: float = 0.02
    line_relevance_retry_alternates: bool = False  # Aug 12 prune Wave A
    max_line_distance_volatility_adjust: bool = False
    mse_override_block_ratio: float = 3.0
    mse_override_requires_relevance_pass: bool = True
    mse_dominance_expansion_max_line_dist_mult: float = 2.5
    # Dual-side TL1 watch — no mission side contest.
    geometry_skip_trend_continuation: bool = True
    cutoff_control_line_enabled: bool = False  # Jul 30: TL1 uses structure envelope (TL2/3 method), not close-pin
    cutoff_control_near_cutoff_minutes: float = 45.0
    # TL1 @ 7:30 — same envelope family as TL2/3 (A1 ORB extreme → A2 structure high/low; never cutoff close)
    pre730_structure_envelope_enabled: bool = True
    pre730_envelope_wick_buffer_pct: float = 0.0005
    pre730_envelope_min_anchor_seconds: float = 60.0

    # --- Legacy telemetry (simple mode does not use hold gates; kept for PO feature rows) ---
    hold_mode: str = "time_based"

    # --- Watch / logging ---
    confirm_pending_log_sec: float = 120.0
    false_break_gate_log_throttle_sec: float = 45.0
    trendline_funnel_summary_interval_sec: float = 180.0

    # --- Post-draw break mode ---
    simple_break_mode: bool = True
    simple_min_score: float = 55.0
    simple_min_break_distance_pct: float = 0.0008
    simple_min_volume_ratio: float = 0.012
    simple_max_entry_distance_pct: float = 0.05
    simple_entry_window_sec: float = 300.0
    simple_enforce_geometry_side: bool = True
    # ORB2 extension envelopes (Trendline2 / Trendline3) — does not alter Trendline1
    orb2_extension_envelope_enabled: bool = True
    orb2_envelope_wick_buffer_pct: float = 0.0005
    orb2_envelope_min_break_distance_pct: float = 0.0015
    orb2_envelope_min_anchor_seconds: float = 60.0
    # 1m close must be this far into the ORB2 box from the broken edge
    # before the next material break can inject TL3+.
    orb2_reclaim_inside_pct: float = 0.0008


# Sequencer / persist / watch priority follow this. TL2PLUS Path-SS covers TL2–TL10.
MAX_TRENDLINE_SLOT = 10
TRENDLINE_SLOT_RANK: Dict[str, int] = {
    f"trendline{i}": i for i in range(MAX_TRENDLINE_SLOT + 1)
}
_TRENDLINE_SLOT_IDS = tuple(TRENDLINE_SLOT_RANK.keys())
ORB2_EXTENSION_SLOT_IDS = tuple(
    f"trendline{i}" for i in range(2, MAX_TRENDLINE_SLOT + 1)
)
ORB2_EXTENSION_BUILT_FLAGS = tuple(
    f"tl{i}_built" for i in range(2, MAX_TRENDLINE_SLOT + 1)
)
ORB2_EXTENSION_PENDING_FLAGS = tuple(
    f"tl{i}_pending" for i in range(3, MAX_TRENDLINE_SLOT + 1)
)


def newest_trendline_slot_rank() -> int:
    return int(max(TRENDLINE_SLOT_RANK.values()))


def normalize_trendline_slot(raw: Any) -> str:
    """Canonical slot id: trendline0 .. trendline{MAX_TRENDLINE_SLOT}."""
    slot = str(raw or "trendline1").strip().lower().replace(" ", "")
    if slot in _TRENDLINE_SLOT_IDS:
        return slot
    n: Optional[int] = None
    if slot.startswith("tl") and slot[2:].isdigit():
        n = int(slot[2:])
    elif slot.startswith("trendline_") and slot[10:].isdigit():
        n = int(slot[10:])
    elif slot.isdigit():
        n = int(slot)
    if n is not None:
        key = f"trendline{n}"
        if key in TRENDLINE_SLOT_RANK:
            return key
    return "trendline1"


def trendline_slot_rank(raw: Any = None) -> int:
    """Build order: TL0 < TL1 < … < newest slot. Unknown → -1."""
    return int(TRENDLINE_SLOT_RANK.get(normalize_trendline_slot(raw), -1))


def trendline_slot_watch_priority(raw: Any = None) -> int:
    """Lower = evaluate/execute first. Newest active slot first (TL10→…→TL0)."""
    rank = trendline_slot_rank(raw)
    if rank < 0:
        return 99
    return int(newest_trendline_slot_rank() - rank)


def trendline_slot_from_payload(payload: Optional[Dict[str, Any]]) -> str:
    return normalize_trendline_slot((payload or {}).get("trendline_slot"))


def trendline_slot_short_label(raw: Any = None) -> str:
    """Telegram / UI short tag: TL0 | TL1 | … | TL10."""
    slot = normalize_trendline_slot(raw)
    rank = TRENDLINE_SLOT_RANK.get(slot)
    if rank is None:
        return "TL1"
    return f"TL{rank}"


def make_trendline_candidate_key(
    symbol: str,
    direction: Any,
    trendline_slot: Any = "trendline1",
) -> str:
    """Unique engine key so TL2/TL3 cannot overwrite Trendline1."""
    sym = str(symbol or "").strip().upper()
    if hasattr(direction, "value"):
        dir_s = str(direction.value)
    else:
        dir_s = str(direction or "").strip().lower()
    return f"{sym}:{dir_s}:{normalize_trendline_slot(trendline_slot)}"


@dataclass(frozen=True)
class TrendlineOptionSelectionConfig:
    """0DTE contract selection knobs (Trendline path only)."""

    delta_min: float = 0.18
    delta_max: float = 0.42
    strike_mode: str = "otm_1_to_2"
    lotto_mode: bool = True
    max_bid_ask_spread_pct: float = 0.40
    min_open_interest: int = 0
    min_volume: int = 0


@dataclass(frozen=True)
class TrendlineAnchor:
    """An anchor point used to define a trendline."""

    ts: datetime
    price: float
    source: str
    candle_index: Optional[int] = None


@dataclass(frozen=True)
class TrendlineDefinition:
    """Projected trendline built from two anchors."""

    symbol: str
    direction: TrendlineDirection
    anchor_one: TrendlineAnchor
    anchor_two: TrendlineAnchor
    slope_per_second: float
    intercept: float
    built_at: datetime
    metadata: Dict[str, Any] = field(default_factory=dict)

    def value_at(self, ts: datetime) -> float:
        """Return projected line value at a timestamp."""
        return (self.slope_per_second * ts.timestamp()) + self.intercept


@dataclass
class TrendlineCandidate:
    """Pending candidate carried through post-7:30 lifecycle."""

    symbol: str
    direction: TrendlineDirection
    qualified_at: datetime
    priority_score: float = 0.0
    confidence: float = 0.0
    setup_payload: Dict[str, Any] = field(default_factory=dict)
    trendline: Optional[TrendlineDefinition] = None
    state: TrendlineCandidateState = TrendlineCandidateState.WAITING_FOR_BUILD
    state_reason: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    break_event: Optional["TrendlineBreakEvent"] = None
    momentum_confirmation: Optional["TrendlineMomentumConfirmation"] = None
    candidate_id: str = ""
    executed_trade_id: Optional[str] = None
    has_broken_once: bool = False
    first_break_at: Optional[datetime] = None
    break_attempt_count: int = 0
    hold_success_at: Optional[datetime] = None
    continuation_break_at: Optional[datetime] = None
    local_continuation_level: Optional[float] = None
    hold_bars_achieved: int = 0
    hold_end_bar_index: int = 0
    last_hold_duration_seconds: float = 0.0
    continuation_pending: bool = False
    continuation_start_index: int = 0
    continuation_max_bars: int = 8
    continuation_reason: str = ""
    rearm_pending: bool = False
    rearm_origin_reason: str = ""
    rearm_started_at: Optional[datetime] = None
    rearm_checks_done: int = 0
    rearm_break_level: Optional[float] = None
    rearm_break_candle_low: Optional[float] = None
    rearm_break_candle_high: Optional[float] = None
    retest_pending: bool = False
    retest_started_at: Optional[datetime] = None
    retest_break_level: Optional[float] = None
    retest_direction: str = ""
    retest_checks_done: int = 0
    break_bar_index: Optional[int] = None
    mfe_window_logged: Dict[str, bool] = field(default_factory=dict)
    impulse_pending_confirmation: bool = False
    impulse_break_high: Optional[float] = None
    impulse_break_low: Optional[float] = None
    reversal_watch_active: bool = False
    reversal_watch_started_at: Optional[datetime] = None
    reversal_watch_start_index: int = 0
    reversal_original_break_direction: str = ""
    reversal_rejection_high: Optional[float] = None
    reversal_rejection_low: Optional[float] = None
    break_timestamp: Optional[datetime] = None
    # Dedupes TRENDLINE_DECISION_SNAPSHOT within the current process_new_bar pass; reset each bar.
    decision_logged: bool = False
    skip_timestamp: Optional[datetime] = None
    skip_price: Optional[float] = None
    missed_win_early_logged: bool = False
    reanchor_attempted: bool = False
    reanchor_anchor_count: int = 0
    # Deferred entry after strict pre-emit soft-fail (e.g. reversal candle on micro-break path).
    delayed_continuation_armed: bool = False
    delayed_continuation_origin: str = ""
    delayed_continuation_started_at: Optional[datetime] = None
    delayed_continuation_deadline_ts: Optional[datetime] = None
    delayed_continuation_bars_seen: int = 0
    delayed_continuation_break_low: Optional[float] = None
    delayed_continuation_break_high: Optional[float] = None
    delayed_continuation_ref_em_pct: Optional[float] = None


@dataclass(frozen=True)
class TrendlineBreakEvent:
    """A detected trendline break event."""

    symbol: str
    direction: TrendlineDirection
    status: BreakStatus
    candle_ts: datetime
    close_price: float
    trendline_price: float
    break_distance: float
    break_distance_pct: float
    threshold_used: float
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TrendlineMomentumConfirmation:
    """Momentum verdict for a break event."""

    symbol: str
    direction: TrendlineDirection
    status: MomentumStatus
    confirmed_at: datetime
    bars_used: int
    velocity_pct: float
    range_expansion_ratio: float
    pullback_ratio: float
    reason: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TrendlineTradeSignal:
    """Trade-ready signal emitted by trendline signal engine."""

    symbol: str
    direction: TrendlineDirection
    emitted_at: datetime
    trendline: TrendlineDefinition
    break_event: TrendlineBreakEvent
    momentum_confirmation: TrendlineMomentumConfirmation
    confidence: float
    priority_score: float
    option_side: str  # call or put
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TrendlineTradeResult:
    """Execution result for a trendline signal."""

    symbol: str
    direction: TrendlineDirection
    success: bool
    executed_at: datetime
    signal_time: datetime
    account_mode: str
    position_id: Optional[str] = None
    order_payload: Dict[str, Any] = field(default_factory=dict)
    details: Dict[str, Any] = field(default_factory=dict)
    error: str = ""


@dataclass(frozen=True)
class OHLCVBar:
    """Minimal candle representation for trendline engines."""

    ts: datetime
    open: float
    high: float
    low: float
    close: float
    volume: Optional[float] = None
    atr: Optional[float] = None

