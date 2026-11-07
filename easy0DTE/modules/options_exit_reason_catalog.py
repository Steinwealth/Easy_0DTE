"""
0DTE profit-capture exit reason taxonomy (ORB1 Breakout + Trendline 0DTE).

Single canonical ID set — emitted by stealth, logs, Telegram, and Profit Optimizer.
LEGACY_EXIT_REASON_ALIASES maps pre-rename / pre-2026-06 strings for historical log replay.
"""

from __future__ import annotations

from enum import Enum
from typing import Dict, FrozenSet, Optional


class OptionsStealthExitReason(str, Enum):
    """Canonical stealth exit IDs (primary + disabled tiers)."""

    # Primary — config-gated where noted
    MFE_TAKE_PROFIT = "mfe_take_profit"  # +30% full close when und-runner off / no-arm bank (was take_profit)
    PRICE_STOP = "price_stop"  # −1.8% ORB/TL · −0.8% BO under/over entry; only before ARM
    PREMIUM_STOP = "premium_stop"  # −25% premium when MFE < 15%; never-armed only (Aug 14)
    PRICE_TRAIL = "price_trail"  # und HWM trail after +30% gate (was und_trail)
    PREM_TRAIL = "premium_trail"  # PC-PX2 20pp ratchet after ARM; lock@200% same width (was prem_trail/prem_giveback)
    PREM_GIVEBACK = "premium_trail"  # alias — historical member name
    TAKE_PROFIT = "take_profit"  # +1% favorable und from entry once runner armed (was und_take_profit)
    MFE_TRAIL = "mfe_trail"  # premium giveback from HWM (was mfe_giveback)
    MFE_GIVEBACK = "mfe_trail"  # alias — historical member name
    HOLD_EXIT = "hold_exit"  # config-off Jul 29
    MOON_HOLD = "moon_hold"  # Aug 12 H2 — removed Aug 17; historical logs only
    BREAKEVEN = "breakeven"
    SCRATCH = "scratch"
    TIME_EXIT = "time_exit"
    NO_PROGRESS = "no_progress"
    NO_DATA = "no_data"
    DEGRADED_DATA = "degraded_data"
    END_OF_DAY_EXIT = "end_of_day_exit"  # was eod_close
    UL_TRAIL = "ul_trail"  # historical PC8 CALL trail — engine removed Sep 23
    UL_BE = "ul_be"  # historical PC8 PUT breakeven — engine removed Sep 23
    CATASTROPHIC_PREMIUM_STOP = "catastrophic_premium_stop"  # PATH_V2 −80% premium after 60s
    SHALLOW_PROOF_STOP = "shallow_proof_stop"  # PATH_V2: MFE 10–20%, bid ≤ −30% after 60s
    IMMATURE_STRESS = "immature_stress"  # PATH_V2 running MFE < 15% and live_stress ≥ 0.40
    SHALLOW_TIME_STOP = "shallow_time_stop"  # PATH_V2 age ≥ 15m and running MFE still < 7.5%
    GB30_GIVEBACK = "gb30_giveback"  # PATH_V2 running MFE 30% up to 50%, giveback ≥ 24pp (25pp before Oct 1)
    GB32_TIMED_GIVEBACK = "gb32_timed_giveback"  # PATH_V2 Oct 1 B: MFE 32–40%, giveback ≥ 15pp, high ≥ 120s old, pret ≥ +5%
    DEEP_GREEN_STOP = "deep_green_stop"  # PATH_V2 Oct 1 B: age ≥ 60s, MFE 10–30%, bid return ≤ −65%
    SHALLOW_FLOOR_STOP = "shallow_floor_stop"  # PATH_V2 Oct 6 V3A SF: MFE 5–10% (peak ≥2 ticks), age ≥ 120s, fresh cross to bid ≤ −18%
    STALL_FLOOR_STOP = "stall_floor_stop"  # PATH_V2 Oct 6 V3A SBE v2-A: MFE 15–40%, high ≥ 1500s old, 2 ticks ≤ +4%, bid ≥ +1%
    HWM_RTC = "hwm_rtc"  # PATH_V2 mature retrace + material-HWM age + premium velocity
    ABSORPTION_EXIT = "absorption_exit"  # mandatory: age ≥ 450s, weak expansion, pret ≥ +2%
    THETA_EXIT = "theta_exit"  # experimental, off: age ≥ 300s, never green, theta ≥ −0.50
    SHALLOW_GREEN_EXIT = "shallow_green_exit"  # CALL: age ≥ 120s, MFE 2–20%, HWM stale ≥ 90s, vel ≤ 0, pret ≥ 0
    CONTRACT_FADE_EXIT = "contract_fade_exit"  # PATH_V2: thin contract breadth, <20% MFE, bid+ask fade. OFF Oct 6 (pending calibration)
    PREMIUM_TRAIL_GT50_25TRAIL = "premium_trail_>50%_25trail"  # PATH_V2 hybrid stage 1: MFE ≥ 50%, giveback ≥ 25pp. OFF (Sep 28)
    PREMIUM_TRAIL_GT30_10TRAIL = "premium_trail_>30%_10trail"  # PATH_V2 hybrid stage 2: MFE ≥ 30%, giveback ≥ 10pp. OFF (Sep 28)
    PREMIUM_SPIKE_FAIL = "premium_spike_fail"  # PATH_V2: 30–60% MFE, 15pp giveback, high still ≤ 16s old
    UNDERLYING_MFE_CAPTURE = "underlying_mfe_capture"  # PATH_V2: MFE 175-250%, green premium and adverse fresh UL retrace
    GUARDED_RETRACE_EXIT = "guarded_retrace_exit"  # PATH_V2 STEP 9 (Oct 22 GBUL): MFE 30-200%, UL ≥ 0.5% off its extreme, bid ≥ 0.65× peak, 2 ticks
    ATR_LOSS_CUT = "atr_loss_cut"  # PATH_V2 STEP 29 (Oct 27): bid ≤ −30%, UL ≥ 2.0 ATR against since entry, confirmed peak < +10%, age ≥ 600s, wrong side of VWAP, fresh bar
    VWAP_CROSS_EXIT = "vwap_cross_exit"  # PATH_V2 STEP 30 (Oct 27): was above VWAP, confirmed peak ≥ +50%, now ≥ 0.25 ATR on the wrong side for 1 bar, bid ≥ +10%, fresh bar
    UL_GIVEBACK_RATIO_EXIT = "ul_giveback_ratio_exit"  # PATH_V2 STEP 9 (Oct 22 UFR): MFE 30-200%, UL gave back ≥ 30% of a ≥ 0.5% move, bid ≥ 0.70× peak and ≥ +3%, 2 ticks
    BOUNDED_MFE_CAPTURE = "bounded_mfe_capture"  # PATH_V2: 290-305% MFE, +10pp giveback after 10:45 PT
    TWENTY_PERCENT_SAVE = "twenty_percent_save"  # PATH_V2 STEP 2: +20% stall save (Nov 3 2026, CALL, t20>=90s, 15pp giveback)
    PREMIUM_STALE_FAIL = "premium_stale_fail"  # PATH_V2: proved premium faded back to a small win after a stale high
    RUNNER_TIMED_CAPTURE = "runner_timed_capture"  # PATH_V2: 350%+ runner, +400% current after 60s armed (30s with V3A RT)
    RUNNER_PROFIT_FLOOR = "runner_profit_floor"  # PATH_V2: 350%+ runner, 80% peak floor after 600s (900s while bid ≥ +50% with V3A RT)
    RUNNER_EXTENDED_CAPTURE = "runner_extended_capture"  # PATH_V2 R2X (Oct 6): held runner hit 1.25× L0, or ≥ 1.045× L0 with UL retrace ≥ 0.04
    RUNNER_EXTENSION_FLOOR = "runner_extension_floor"  # PATH_V2 R2X (Oct 6): held runner fell below +50%
    # Disabled exits (retained for log normalization of old sessions only)
    MFE_TRAILING_STOP = "mfe_trailing_stop"  # was trailing_stop


class OptionsEmergencyExitReason(str, Enum):
    """Emergency-only exits when stealth monitor fails (not normal profit capture)."""

    FAIL_SAFE = "fail_safe"
    HEALTH_EMERGENCY = "health_emergency"


PRIMARY_ENABLED_EXITS: FrozenSet[str] = frozenset(
    {
        OptionsStealthExitReason.MFE_TAKE_PROFIT.value,
        OptionsStealthExitReason.PRICE_STOP.value,
        OptionsStealthExitReason.PREMIUM_STOP.value,
        OptionsStealthExitReason.PRICE_TRAIL.value,
        OptionsStealthExitReason.PREM_TRAIL.value,
        OptionsStealthExitReason.TAKE_PROFIT.value,
        OptionsStealthExitReason.MFE_TRAIL.value,
        OptionsStealthExitReason.HOLD_EXIT.value,
        OptionsStealthExitReason.BREAKEVEN.value,
        OptionsStealthExitReason.SCRATCH.value,
        OptionsStealthExitReason.TIME_EXIT.value,
        OptionsStealthExitReason.NO_PROGRESS.value,
        OptionsStealthExitReason.NO_DATA.value,
        OptionsStealthExitReason.DEGRADED_DATA.value,
        OptionsStealthExitReason.END_OF_DAY_EXIT.value,
        OptionsStealthExitReason.CATASTROPHIC_PREMIUM_STOP.value,
        OptionsStealthExitReason.SHALLOW_PROOF_STOP.value,
        OptionsStealthExitReason.IMMATURE_STRESS.value,
        OptionsStealthExitReason.SHALLOW_TIME_STOP.value,
        OptionsStealthExitReason.GB30_GIVEBACK.value,
        OptionsStealthExitReason.HWM_RTC.value,
        OptionsStealthExitReason.ABSORPTION_EXIT.value,
        OptionsStealthExitReason.SHALLOW_GREEN_EXIT.value,
        OptionsStealthExitReason.PREMIUM_SPIKE_FAIL.value,
        OptionsStealthExitReason.UNDERLYING_MFE_CAPTURE.value,
        OptionsStealthExitReason.GUARDED_RETRACE_EXIT.value,  # STEP 9 GBUL (Oct 22, was 7A)
        OptionsStealthExitReason.UL_GIVEBACK_RATIO_EXIT.value,  # STEP 9 UFR (Oct 22, was 7A)
        OptionsStealthExitReason.BOUNDED_MFE_CAPTURE.value,
        OptionsStealthExitReason.PREMIUM_STALE_FAIL.value,
        OptionsStealthExitReason.RUNNER_TIMED_CAPTURE.value,
        OptionsStealthExitReason.RUNNER_PROFIT_FLOOR.value,
        OptionsStealthExitReason.RUNNER_EXTENDED_CAPTURE.value,  # Oct 6 R2X
        OptionsStealthExitReason.RUNNER_EXTENSION_FLOOR.value,  # Oct 6 R2X
        OptionsStealthExitReason.GB32_TIMED_GIVEBACK.value,
        OptionsStealthExitReason.DEEP_GREEN_STOP.value,
        OptionsStealthExitReason.SHALLOW_FLOOR_STOP.value,
        OptionsStealthExitReason.STALL_FLOOR_STOP.value,
        OptionsStealthExitReason.ATR_LOSS_CUT.value,  # STEP 29 (Oct 27)
        OptionsStealthExitReason.VWAP_CROSS_EXIT.value,  # STEP 30 (Oct 27)
        OptionsStealthExitReason.TWENTY_PERCENT_SAVE.value,  # STEP 2 (Nov 3: +20% stall save, ON)
    }
)

# Oct 6 SHIP (exit catalog): implemented PATH_V2 exits that are OFF in Shared.env §2b.
# Ids still normalize (CANONICAL_EXIT_REASON_VALUES) so logs and replays stay readable.
PRIMARY_DISABLED_EXITS: FrozenSet[str] = frozenset(
    {
        OptionsStealthExitReason.THETA_EXIT.value,  # OPTION_PATH_V2_THETA_EXIT_ENABLED=false
        OptionsStealthExitReason.CONTRACT_FADE_EXIT.value,  # Oct 6: OPTION_PATH_V2_CONTRACT_FADE_EXIT_ENABLED=false
        OptionsStealthExitReason.PREMIUM_TRAIL_GT50_25TRAIL.value,  # OPTION_PATH_V2_HYBRID_PREMIUM_TRAIL_ENABLED=false
        OptionsStealthExitReason.PREMIUM_TRAIL_GT30_10TRAIL.value,  # OPTION_PATH_V2_HYBRID_PREMIUM_TRAIL_ENABLED=false
    }
)

DISABLED_EXITS: FrozenSet[str] = frozenset(
    {
        OptionsStealthExitReason.MFE_TRAILING_STOP.value,
        OptionsStealthExitReason.MOON_HOLD.value,  # removed Aug 17
        OptionsStealthExitReason.UL_TRAIL.value,  # historical PC8 CALL trail, engine removed Sep 23
        OptionsStealthExitReason.UL_BE.value,  # historical PC8 PUT breakeven, engine removed Sep 23
    }
)

EMERGENCY_EXITS: FrozenSet[str] = frozenset(
    e.value for e in OptionsEmergencyExitReason
)

CANONICAL_EXIT_REASON_VALUES: FrozenSet[str] = frozenset(
    PRIMARY_ENABLED_EXITS | PRIMARY_DISABLED_EXITS | DISABLED_EXITS | EMERGENCY_EXITS
)

# Former canonical IDs (Aug 9, 2026 rename) → current IDs.
# Note: bare historical ``take_profit`` (old MFE TP) is ambiguous with new und ``take_profit``;
# emitters must use ``mfe_take_profit`` going forward. Old MFE alerts may still say take_profit.
RENAMED_EXIT_REASON_IDS: Dict[str, str] = {
    "asset_stop": OptionsStealthExitReason.PRICE_STOP.value,
    "und_trail": OptionsStealthExitReason.PRICE_TRAIL.value,
    "und_take_profit": OptionsStealthExitReason.TAKE_PROFIT.value,
    "mfe_giveback": OptionsStealthExitReason.MFE_TRAIL.value,
    "eod_close": OptionsStealthExitReason.END_OF_DAY_EXIT.value,
    "prem_trail": OptionsStealthExitReason.PREM_TRAIL.value,
    "prem_giveback": OptionsStealthExitReason.PREM_TRAIL.value,
    "trailing_stop": OptionsStealthExitReason.MFE_TRAILING_STOP.value,
}

# Pre-2026-06 / deleted-floor strings → canonical ID (read-only normalization).
LEGACY_EXIT_REASON_ALIASES: Dict[str, str] = {
    "breakeven_stop": OptionsStealthExitReason.BREAKEVEN.value,
    "options_breakeven_exit": OptionsStealthExitReason.BREAKEVEN.value,
    "options_stealth:profit_floor_exit": OptionsStealthExitReason.BREAKEVEN.value,
    "profit_floor_exit": OptionsStealthExitReason.BREAKEVEN.value,
    "profit_floor": OptionsStealthExitReason.BREAKEVEN.value,
    "impulse_profit_lock": OptionsStealthExitReason.BREAKEVEN.value,
    "micro_lock": OptionsStealthExitReason.BREAKEVEN.value,
    "impulse_micro_lock": OptionsStealthExitReason.BREAKEVEN.value,
    "options_profit_lock_exit": OptionsStealthExitReason.BREAKEVEN.value,
    "options_micro_lock_exit": OptionsStealthExitReason.BREAKEVEN.value,
    # hold_exit
    "hold_floor_break": OptionsStealthExitReason.HOLD_EXIT.value,
    "hold_reclaim_fail": OptionsStealthExitReason.HOLD_EXIT.value,
    "hold_retrail_giveback": OptionsStealthExitReason.HOLD_EXIT.value,
    "hold_max": OptionsStealthExitReason.HOLD_EXIT.value,
    # moon_hold (Aug 12 H2 — removed Aug 17; normalize historical closes only)
    "moon_hold_trail": OptionsStealthExitReason.MOON_HOLD.value,
    "moon_hold_cancel_dir": OptionsStealthExitReason.MOON_HOLD.value,
    "moon_hold_cancel_max": OptionsStealthExitReason.MOON_HOLD.value,
    "moon_hold_entered": OptionsStealthExitReason.MOON_HOLD.value,
    # mfe_giveback
    "options_stealth:max_pnl_drawdown_exit": OptionsStealthExitReason.MFE_GIVEBACK.value,
    "reversal_exit": OptionsStealthExitReason.MFE_GIVEBACK.value,
    "trendline_reversal_exit": OptionsStealthExitReason.MFE_GIVEBACK.value,
    "max_pnl_drawdown_exit": OptionsStealthExitReason.MFE_GIVEBACK.value,
    "options_peak_giveback_exit": OptionsStealthExitReason.MFE_GIVEBACK.value,
    # no_progress
    "no_progress_timeout": OptionsStealthExitReason.NO_PROGRESS.value,
    "options_no_progress_exit": OptionsStealthExitReason.NO_PROGRESS.value,
    # no_data / degraded_data
    "option_force_exit_no_data": OptionsStealthExitReason.NO_DATA.value,
    "options_no_data_exit": OptionsStealthExitReason.NO_DATA.value,
    "degraded_data_no_fallback": OptionsStealthExitReason.DEGRADED_DATA.value,
    "degraded_data_outage": OptionsStealthExitReason.DEGRADED_DATA.value,
    # end_of_day_exit (was eod_close)
    "eod_close": OptionsStealthExitReason.END_OF_DAY_EXIT.value,
    "EOD_CLOSE": OptionsStealthExitReason.END_OF_DAY_EXIT.value,
    "end_of_day_close": OptionsStealthExitReason.END_OF_DAY_EXIT.value,
    "options_eod_exit": OptionsStealthExitReason.END_OF_DAY_EXIT.value,
    "CAT_PREM_STOP": OptionsStealthExitReason.CATASTROPHIC_PREMIUM_STOP.value,
    "SHALLOW_PROOF_10_20": OptionsStealthExitReason.SHALLOW_PROOF_STOP.value,
    "SHALLOW_15M_MFE7P5": OptionsStealthExitReason.SHALLOW_TIME_STOP.value,
    "SHALLOW_20M": OptionsStealthExitReason.SHALLOW_TIME_STOP.value,
    "MFE_TRAIL_1230": OptionsStealthExitReason.MFE_TRAIL.value,
    "PATH_V2_EARLY_PREM_TRAIL": OptionsStealthExitReason.PREM_TRAIL.value,
    "PATH_V2_PREMIUM_TRAIL": OptionsStealthExitReason.PREM_TRAIL.value,
    "CONTRACT_FADE_EXIT": OptionsStealthExitReason.CONTRACT_FADE_EXIT.value,
    "UL_TRAIL_EXIT": OptionsStealthExitReason.UL_TRAIL.value,
    "UL_BE_EXIT": OptionsStealthExitReason.UL_BE.value,
    "IMMATURE_STRESS": OptionsStealthExitReason.IMMATURE_STRESS.value,
    "SHALLOW_TIME_STOP": OptionsStealthExitReason.SHALLOW_TIME_STOP.value,
    "GB30_25PP": OptionsStealthExitReason.GB30_GIVEBACK.value,
    "GB30_20PP": OptionsStealthExitReason.GB30_GIVEBACK.value,
    "GB30_24PP": OptionsStealthExitReason.GB30_GIVEBACK.value,
    "GB32_40_TIMED_15PP": OptionsStealthExitReason.GB32_TIMED_GIVEBACK.value,
    "GB32_TIMED_GIVEBACK": OptionsStealthExitReason.GB32_TIMED_GIVEBACK.value,
    "DEEP_STOP_10_30_65": OptionsStealthExitReason.DEEP_GREEN_STOP.value,
    "DEEP_GREEN_STOP": OptionsStealthExitReason.DEEP_GREEN_STOP.value,
    "SHALLOW_FLOOR_5_10_18": OptionsStealthExitReason.SHALLOW_FLOOR_STOP.value,
    "SHALLOW_FLOOR_STOP": OptionsStealthExitReason.SHALLOW_FLOOR_STOP.value,
    "STALL_FLOOR_15_40_4": OptionsStealthExitReason.STALL_FLOOR_STOP.value,
    "STALL_FLOOR_STOP": OptionsStealthExitReason.STALL_FLOOR_STOP.value,
    "HWM_RTC": OptionsStealthExitReason.HWM_RTC.value,
    "ABSORPTION_EXIT": OptionsStealthExitReason.ABSORPTION_EXIT.value,
    "THETA_EXIT": OptionsStealthExitReason.THETA_EXIT.value,
    "SHALLOW_GREEN_EXIT": OptionsStealthExitReason.SHALLOW_GREEN_EXIT.value,
    "PREMIUM_TRAIL_GT50_25TRAIL": OptionsStealthExitReason.PREMIUM_TRAIL_GT50_25TRAIL.value,
    "PREMIUM_TRAIL_GT30_10TRAIL": OptionsStealthExitReason.PREMIUM_TRAIL_GT30_10TRAIL.value,
    "SPIKE_FAIL": OptionsStealthExitReason.PREMIUM_SPIKE_FAIL.value,
    "PREMIUM_SPIKE_FAIL": OptionsStealthExitReason.PREMIUM_SPIKE_FAIL.value,
    "UL_MFE_CAPTURE_175_250": OptionsStealthExitReason.UNDERLYING_MFE_CAPTURE.value,
    "UNDERLYING_MFE_CAPTURE": OptionsStealthExitReason.UNDERLYING_MFE_CAPTURE.value,
    "GBUL_RETRACE_05_65": OptionsStealthExitReason.GUARDED_RETRACE_EXIT.value,
    "GUARDED_RETRACE_EXIT": OptionsStealthExitReason.GUARDED_RETRACE_EXIT.value,
    "UFR_GIVEBACK_30_70": OptionsStealthExitReason.UL_GIVEBACK_RATIO_EXIT.value,
    "UL_GIVEBACK_RATIO_EXIT": OptionsStealthExitReason.UL_GIVEBACK_RATIO_EXIT.value,
    "ATR_LOSS_CUT_30_2ATR": OptionsStealthExitReason.ATR_LOSS_CUT.value,  # Oct 27 STEP 29
    "ATR_LOSS_CUT": OptionsStealthExitReason.ATR_LOSS_CUT.value,
    "VWAP_CROSS_50_025ATR": OptionsStealthExitReason.VWAP_CROSS_EXIT.value,  # Oct 27 STEP 30
    "VWAP_CROSS_EXIT": OptionsStealthExitReason.VWAP_CROSS_EXIT.value,
    "BOUNDED_MFE_CAPTURE_290_305": OptionsStealthExitReason.BOUNDED_MFE_CAPTURE.value,
    "BOUNDED_MFE_CAPTURE": OptionsStealthExitReason.BOUNDED_MFE_CAPTURE.value,
    "P20_STALL_SAVE": OptionsStealthExitReason.TWENTY_PERCENT_SAVE.value,  # Nov 3 STEP 2
    "P20_UL_RETRACE_SAVE": OptionsStealthExitReason.TWENTY_PERCENT_SAVE.value,  # Nov 3 STEP 2 ext (default off)
    "PVE_20P_SAVE": OptionsStealthExitReason.TWENTY_PERCENT_SAVE.value,  # retired Oct 22 detail (old logs)
    "TWENTY_PERCENT_SAVE": OptionsStealthExitReason.TWENTY_PERCENT_SAVE.value,
    "STALE_HIGH_FAIL": OptionsStealthExitReason.PREMIUM_STALE_FAIL.value,
    "PREMIUM_STALE_FAIL": OptionsStealthExitReason.PREMIUM_STALE_FAIL.value,
    "RUNNER_TIMED_CAPTURE_350": OptionsStealthExitReason.RUNNER_TIMED_CAPTURE.value,
    "RUNNER_PROFIT_FLOOR_350": OptionsStealthExitReason.RUNNER_PROFIT_FLOOR.value,
    "RUNNER_TIMED_CAPTURE_300": OptionsStealthExitReason.RUNNER_TIMED_CAPTURE.value,
    "RUNNER_PROFIT_FLOOR_300": OptionsStealthExitReason.RUNNER_PROFIT_FLOOR.value,
    "RUNNER_TIMED_CAPTURE_350_A30": OptionsStealthExitReason.RUNNER_TIMED_CAPTURE.value,  # Oct 6 V3A RT
    "RUNNER_PROFIT_FLOOR_350_G900": OptionsStealthExitReason.RUNNER_PROFIT_FLOOR.value,  # Oct 6 V3A RT
    "RUNNER_EXT_TARGET": OptionsStealthExitReason.RUNNER_EXTENDED_CAPTURE.value,  # Oct 6 R2X
    "RUNNER_EXT_ULREV": OptionsStealthExitReason.RUNNER_EXTENDED_CAPTURE.value,  # Oct 6 R2X
    "RUNNER_EXT_FLOOR": OptionsStealthExitReason.RUNNER_EXTENSION_FLOOR.value,  # Oct 6 R2X
    "premium_trail_aggressive": OptionsStealthExitReason.PREMIUM_TRAIL_GT50_25TRAIL.value,
    "premium_trail_hybrid_salvage": OptionsStealthExitReason.PREMIUM_TRAIL_GT30_10TRAIL.value,
    # mfe_trailing_stop (was trailing_stop)
    "trailing_stop": OptionsStealthExitReason.MFE_TRAILING_STOP.value,
    "options_trailing_exit": OptionsStealthExitReason.MFE_TRAILING_STOP.value,
    # price_stop (was asset_stop)
    "asset_stop": OptionsStealthExitReason.PRICE_STOP.value,
    "stop_loss": OptionsStealthExitReason.PRICE_STOP.value,
    "STOP_LOSS": OptionsStealthExitReason.PRICE_STOP.value,
    "underlying_stop": OptionsStealthExitReason.PRICE_STOP.value,
    "asset_stop_loss": OptionsStealthExitReason.PRICE_STOP.value,
    # price_trail (was und_trail)
    "und_trail": OptionsStealthExitReason.PRICE_TRAIL.value,
    "price_runner_trail": OptionsStealthExitReason.PRICE_TRAIL.value,
    "underlying_trail": OptionsStealthExitReason.PRICE_TRAIL.value,
    # take_profit (was und_take_profit) — und runner bank
    "und_take_profit": OptionsStealthExitReason.TAKE_PROFIT.value,
    "price_tp": OptionsStealthExitReason.TAKE_PROFIT.value,
    "price_runner_take_profit": OptionsStealthExitReason.TAKE_PROFIT.value,
    "underlying_take_profit": OptionsStealthExitReason.TAKE_PROFIT.value,
    # mfe_take_profit (was take_profit) — option premium +30% bank / PX2 stall-bank
    "mfe_tp": OptionsStealthExitReason.MFE_TAKE_PROFIT.value,
    "options_take_profit": OptionsStealthExitReason.MFE_TAKE_PROFIT.value,
    "options_stealth:take_profit": OptionsStealthExitReason.MFE_TAKE_PROFIT.value,
    "stall_bank": OptionsStealthExitReason.MFE_TAKE_PROFIT.value,
    # PC-PX2 20pp ratchet
    "prem_trail": OptionsStealthExitReason.PREM_TRAIL.value,
    "prem_giveback": OptionsStealthExitReason.PREM_TRAIL.value,
    "moon_giveback": OptionsStealthExitReason.PREM_TRAIL.value,
    # time_exit
    "options_time_exit": OptionsStealthExitReason.TIME_EXIT.value,
    # emergency
    "options_fail_safe_exit": OptionsEmergencyExitReason.FAIL_SAFE.value,
    "options_health_emergency_exit": OptionsEmergencyExitReason.HEALTH_EMERGENCY.value,
}

# Backward-compatible name for code that imported the old map.
EXIT_REASON_CANONICAL: Dict[str, str] = dict(LEGACY_EXIT_REASON_ALIASES)


def _lookup_exit_reason_alias(key: str) -> Optional[str]:
    """Exact then case-insensitive alias / rename / canonical lookup."""
    if key in LEGACY_EXIT_REASON_ALIASES:
        return LEGACY_EXIT_REASON_ALIASES[key]
    if key in RENAMED_EXIT_REASON_IDS:
        return RENAMED_EXIT_REASON_IDS[key]
    if key in CANONICAL_EXIT_REASON_VALUES:
        return key
    folded = key.lower()
    if folded != key:
        if folded in LEGACY_EXIT_REASON_ALIASES:
            return LEGACY_EXIT_REASON_ALIASES[folded]
        if folded in RENAMED_EXIT_REASON_IDS:
            return RENAMED_EXIT_REASON_IDS[folded]
        if folded in CANONICAL_EXIT_REASON_VALUES:
            return folded
    return None


def _strip_stealth_prefix(raw: str) -> str:
    key = raw.strip()
    mapped = _lookup_exit_reason_alias(key)
    if mapped:
        return mapped
    for prefix in ("orb_options_stealth:", "trendline_options_stealth:", "options_stealth:", "failsafe:"):
        if key.startswith(prefix) or key.lower().startswith(prefix):
            key = key.split(":", 1)[-1].strip()
            break
    mapped = _lookup_exit_reason_alias(key)
    if mapped:
        return mapped
    if ":" in key:
        head, tail = key.split(":", 1)
        mapped = _lookup_exit_reason_alias(head) or _lookup_exit_reason_alias(tail)
        if mapped:
            return mapped
    return key


def normalize_exit_reason(reason: Optional[str]) -> str:
    """Map any stealth / legacy string to a canonical short exit id."""
    if not reason:
        return ""
    key = _strip_stealth_prefix(str(reason))
    mapped = _lookup_exit_reason_alias(key)
    if mapped:
        return mapped
    if key.startswith("options_legacy:"):
        inner = key.split(":", 1)[-1]
        return normalize_exit_reason(inner)
    return key


def canonical_exit_reason(legacy_reason: Optional[str]) -> str:
    """Alias for normalize_exit_reason (trading system + profit optimizer hooks)."""
    return normalize_exit_reason(legacy_reason)


def is_primary_enabled_exit(reason: Optional[str]) -> bool:
    return normalize_exit_reason(reason) in PRIMARY_ENABLED_EXITS


def is_emergency_exit(reason: Optional[str]) -> bool:
    return normalize_exit_reason(reason) in EMERGENCY_EXITS


# ---------------------------------------------------------------------------
# Oct 6 SHIP: PATH_V2 FLAT engine exit registry (modules/path_v2_exit.py).
# One row per exit STEP in true evaluation order, plus the monitor-level exits.
# (step, reason id, engine detail strings, Shared.env §2b kill switch or None,
#  on in Shared.env as shipped Oct 6). tests/test_path_v2_exit_catalog.py keeps
# this in sync with the engine constants, Shared.env, alert labels and stats.
# STEP 0 (no mark) and STEP 16 (120s hold) never exit. AE_WARNING_HOLD is a
# STEP 23 hold detail, not an exit; RUNNER_EXT_START / RUNNER_EXT_HOLD are
# R2X hold details (STEP 7 entry / STEP 1 hold mode), not exits. STEP 1 (R2X)
# has two reasons. Step numbers moved up by one on Oct 6 when R2X became
# STEP 1. PATH_END is the replay's end-of-tape label; live, that trade is
# closed by end_of_day_exit.
# Oct 26: one integer STEP sequence 0-28, no lettered sub-steps (old 1A->2,
# 2-7->3-8, 7A->9, 8-16->10-18, 17A->19, 17-25->20-28). Ids and details unchanged.
# Oct 27: STEP 29 atr_loss_cut and STEP 30 vwap_cross_exit appended after STEP 28 (no renumbering).
# ---------------------------------------------------------------------------
PATH_V2_ENGINE_EXITS = (
    (1, "runner_extended_capture", ("RUNNER_EXT_TARGET", "RUNNER_EXT_ULREV"), "OPTION_PATH_V2_RUNNER_EXTENSION_ENABLED", True),  # Oct 6 R2X
    (1, "runner_extension_floor", ("RUNNER_EXT_FLOOR",), "OPTION_PATH_V2_RUNNER_EXTENSION_ENABLED", True),  # Oct 6 R2X
    (2, "twenty_percent_save", ("P20_STALL_SAVE", "P20_UL_RETRACE_SAVE"), "OPTION_PATH_V2_20P_SAVE_ENABLED", True),  # Nov 3 stall save + UL-retrace ext
    (3, "shallow_proof_stop", ("SHALLOW_PROOF_10_20",), "OPTION_PATH_V2_SHALLOW_PROOF_STOP_ENABLED", True),
    (4, "catastrophic_premium_stop", ("CAT_PREM_STOP",), None, True),  # mandatory
    (5, "mfe_trail", ("MFE_TRAIL_1230",), None, True),  # mandatory
    (6, "absorption_exit", ("ABSORPTION_EXIT",), "OPTION_PATH_V2_ABSORPTION_EXIT_ENABLED", True),  # mandatory
    (7, "runner_timed_capture", ("RUNNER_TIMED_CAPTURE_350", "RUNNER_TIMED_CAPTURE_350_A30"), "OPTION_PATH_V2_RUNNER_CAPTURE_ENABLED", True),
    (8, "runner_profit_floor", ("RUNNER_PROFIT_FLOOR_350", "RUNNER_PROFIT_FLOOR_350_G900"), "OPTION_PATH_V2_RUNNER_CAPTURE_ENABLED", True),
    (9, "guarded_retrace_exit", ("GBUL_RETRACE_05_65",), "OPTION_PATH_V2_GBUL_ENABLED", True),  # Oct 22 GBUL
    (9, "ul_giveback_ratio_exit", ("UFR_GIVEBACK_30_70",), "OPTION_PATH_V2_UFR_ENABLED", True),  # Oct 22 UFR
    (10, "premium_trail_>50%_25trail", ("PREMIUM_TRAIL_GT50_25TRAIL",), "OPTION_PATH_V2_HYBRID_PREMIUM_TRAIL_ENABLED", False),
    (11, "premium_trail_>30%_10trail", ("PREMIUM_TRAIL_GT30_10TRAIL",), "OPTION_PATH_V2_HYBRID_PREMIUM_TRAIL_ENABLED", False),
    (12, "premium_spike_fail", ("SPIKE_FAIL",), "OPTION_PATH_V2_SPIKE_FAIL_ENABLED", True),
    (12, "underlying_mfe_capture", ("UL_MFE_CAPTURE_175_250",), "OPTION_PATH_V2_UNDERLYING_MFE_CAPTURE_ENABLED", True),
    (13, "premium_stale_fail", ("STALE_HIGH_FAIL",), "OPTION_PATH_V2_STALE_FAIL_ENABLED", True),
    (14, "theta_exit", ("THETA_EXIT",), "OPTION_PATH_V2_THETA_EXIT_ENABLED", False),
    (15, "contract_fade_exit", ("CONTRACT_FADE_EXIT",), "OPTION_PATH_V2_CONTRACT_FADE_EXIT_ENABLED", False),  # Oct 6 off
    (17, "shallow_green_exit", ("SHALLOW_GREEN_EXIT",), "OPTION_PATH_V2_SHALLOW_GREEN_EXIT_ENABLED", True),
    (18, "shallow_time_stop", ("SHALLOW_15M_MFE7P5",), "OPTION_PATH_V2_SHALLOW_TIME_STOP_ENABLED", True),
    (19, "bounded_mfe_capture", ("BOUNDED_MFE_CAPTURE_290_305",), "OPTION_PATH_V2_BOUNDED_MFE_CAPTURE_ENABLED", True),
    (20, "premium_trail", ("PATH_V2_EARLY_PREM_TRAIL",), "OPTION_PATH_V2_EARLY_PREM_TRAIL_ENABLED", False),
    (21, "premium_trail", ("PATH_V2_PREMIUM_TRAIL",), "OPTION_PATH_V2_PREMIUM_TRAIL_ENABLED", False),
    (22, "shallow_floor_stop", ("SHALLOW_FLOOR_5_10_18",), "OPTION_PATH_V2_SHALLOW_FLOOR_STOP_ENABLED", True),  # V3A SF
    (23, "immature_stress", ("IMMATURE_STRESS",), None, True),
    (24, "gb30_giveback", ("GB30_24PP",), "OPTION_PATH_V2_GB30_ENABLED", True),
    (25, "hwm_rtc", ("HWM_RTC",), None, True),
    (26, "gb32_timed_giveback", ("GB32_40_TIMED_15PP",), "OPTION_PATH_V2_GB32_TIMED_ENABLED", True),
    (27, "deep_green_stop", ("DEEP_STOP_10_30_65",), "OPTION_PATH_V2_DEEP_STOP_ENABLED", True),
    (28, "stall_floor_stop", ("STALL_FLOOR_15_40_4",), "OPTION_PATH_V2_STALL_FLOOR_STOP_ENABLED", True),  # V3A SBE v2-A
    (29, "atr_loss_cut", ("ATR_LOSS_CUT_30_2ATR",), "OPTION_PATH_V2_ATR_LOSS_CUT_ENABLED", True),  # Oct 27
    (30, "vwap_cross_exit", ("VWAP_CROSS_50_025ATR",), "OPTION_PATH_V2_VWAP_CROSS_EXIT_ENABLED", True),  # Oct 27
)

# Mandatory monitor-level exits that run outside the PATH_V2 tick.
PATH_V2_MONITOR_EXITS: FrozenSet[str] = frozenset(
    {
        OptionsStealthExitReason.NO_DATA.value,
        OptionsStealthExitReason.DEGRADED_DATA.value,
        OptionsStealthExitReason.END_OF_DAY_EXIT.value,  # 12:50 PT EOD flatten
    }
)

PATH_V2_ENABLED_EXITS: FrozenSet[str] = frozenset(
    r[1] for r in PATH_V2_ENGINE_EXITS if r[4]
) | PATH_V2_MONITOR_EXITS

PATH_V2_DISABLED_EXITS: FrozenSet[str] = frozenset(
    r[1] for r in PATH_V2_ENGINE_EXITS if not r[4]
) - PATH_V2_ENABLED_EXITS
