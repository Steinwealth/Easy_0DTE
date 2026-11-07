"""PATH_V2 Profit Capture for 0DTE singles: FLAT engine (Oct 6, 2026, includes V3A, SF4 and R2X).

Current Profit Capture system in the Research app. One decision function,
evaluate_path_v2_tick, reads top to bottom in true evaluation order:

  STATE S0-S10 run on every tick (inputs, favorable underlying, premium peak
  and velocity samples, Contract Fade quote memory, V3A shallow-floor
  bookkeeping, running MFE / proof / runner arm, clocks, V3A runner timing,
  V3A stall-floor bookkeeping, STEP 29-30 confirmed peak). Then each STEP is checked in order and the
  first exit returns. A HOLD does not return: it only skips the steps of its
  own region, and STEP 26-28 still run on that tick. The one exception is
  STEP 1 (R2X hold mode): once a runner is in hold mode it owns the exit and
  STEP 2-30 are skipped.

  STEP  0  No executable mark ............ none (hold)
  STEP  1  Runner Extension (R2X) ........ runner_extended_capture / RUNNER_EXT_TARGET, RUNNER_EXT_ULREV;
                                           runner_extension_floor / RUNNER_EXT_FLOOR  (hold mode only)
  STEP  2  20% Save (stall save) ......... twenty_percent_save / P20_STALL_SAVE
           + UL-retrace ext (code default off; env on) twenty_percent_save / P20_UL_RETRACE_SAVE
  STEP  3  Shallow proof stop ............ shallow_proof_stop / SHALLOW_PROOF_10_20
  STEP  4  Catastrophic premium stop ..... catastrophic_premium_stop / CAT_PREM_STOP   (mandatory)
  STEP  5  Afternoon MFE trail ........... mfe_trail / MFE_TRAIL_1230                  (mandatory)
  STEP  6  Absorption exit ............... absorption_exit / ABSORPTION_EXIT           (mandatory)
  STEP  7  350% runner timed capture ..... runner_timed_capture / RUNNER_TIMED_CAPTURE_350[_A30]
                                           (R2X: enters hold mode here instead when the gap guard passes)
  STEP  8  350% runner profit floor ...... runner_profit_floor / RUNNER_PROFIT_FLOOR_350[_G900]
  STEP  9  UL retrace capture ............ guarded_retrace_exit / GBUL_RETRACE_05_65;
                                           ul_giveback_ratio_exit / UFR_GIVEBACK_30_70
  STEP 10  Hybrid trail stage 1 (off) .... premium_trail_>50%_25trail / PREMIUM_TRAIL_GT50_25TRAIL
  STEP 11  Hybrid trail stage 2 (off) .... premium_trail_>30%_10trail / PREMIUM_TRAIL_GT30_10TRAIL
  STEP 12  Spike fail / UL MFE capture ... premium_spike_fail / SPIKE_FAIL;
                                           underlying_mfe_capture / UL_MFE_CAPTURE_175_250
  STEP 13  Premium stale high fail ....... premium_stale_fail / STALE_HIGH_FAIL
  STEP 14  Theta exit (off) .............. theta_exit / THETA_EXIT
  STEP 15  Contract Fade Exit (off) ...... contract_fade_exit / CONTRACT_FADE_EXIT
  STEP 16  120s hold + region split ...... none (state / hold)
  STEP 17  CALL shallow-green exit ....... shallow_green_exit / SHALLOW_GREEN_EXIT (SGUL hold)
  STEP 18  Shallow time stop ............. shallow_time_stop / SHALLOW_15M_MFE7P5
  STEP 19  Bounded high-MFE capture ...... bounded_mfe_capture / BOUNDED_MFE_CAPTURE_290_305
  STEP 20  Early premium trail (off) ..... premium_trail / PATH_V2_EARLY_PREM_TRAIL
  STEP 21  Premium trail 100% (off) ...... premium_trail / PATH_V2_PREMIUM_TRAIL
  STEP 22  V3A shallow floor stop ........ shallow_floor_stop / SHALLOW_FLOOR_5_10_18
  STEP 23  Immature stress + AE warning .. immature_stress / IMMATURE_STRESS (hold AE_WARNING_HOLD)
  STEP 24  30-50% giveback ............... gb30_giveback / GB30_24PP, gated 38pp extension
  STEP 25  Mature high-water retrace ..... hwm_rtc / HWM_RTC
  STEP 26  32-40% timed giveback ......... gb32_timed_giveback / GB32_40_TIMED_15PP
  STEP 27  Deep green stop ............... deep_green_stop / DEEP_STOP_10_30_65
  STEP 28  V3A stall floor stop .......... stall_floor_stop / STALL_FLOOR_15_40_4
  STEP 29  ATR loss cut .................. atr_loss_cut / ATR_LOSS_CUT_30_2ATR
  STEP 30  VWAP cross exit ............... vwap_cross_exit / VWAP_CROSS_50_025ATR

  Exit catalog (single source): easy0DTE/modules/options_exit_reason_catalog.py
  PATH_V2_ENGINE_EXITS lists every exit above once as (step, reason, details,
  kill switch, Shared.env on/off); tests/test_path_v2_exit_catalog.py asserts it
  equals every REASON_/DETAIL_ constant and every _decide() call in this file.

Outside this tick: no_data and degraded_data fire in the monitor before
PATH_V2; end_of_day_exit runs on the 12:50 PT EOD flatten scheduler. These
still apply while R2X holds.

Mark: pret = bid / entry - 1 (executable bid). All knobs are Shared.env §2b
OPTION_PATH_V2_* keys read by load_path_v2_config(). Kill switches:
OPTION_PC_ARCHITECTURE=current or
OPTION_PATH_V2_ENABLED=false (whole engine), plus one *_ENABLED per feature.
V3A switches: OPTION_PATH_V2_SHALLOW_FLOOR_STOP_ENABLED,
OPTION_PATH_V2_RUNNER_TIMING_V3A_ENABLED, OPTION_PATH_V2_STALL_FLOOR_STOP_ENABLED
(dataclass default off, Shared.env on). R2X switch:
OPTION_PATH_V2_RUNNER_EXTENSION_ENABLED (dataclass default off, Shared.env on;
off = the V3A + SF4 engine exactly). Step numbers moved up by one on 10/6 when
R2X became STEP 1 (the old STEP 1-24 are now STEP 2-25). On 10/26 every STEP and STATE was renumbered
in one integer sequence with no lettered sub-steps (old -> new: 1A->2, 2-7->3-8, 7A->9,
8-16->10-18, 17A->19, 17-25->20-28; STATE S1A->S2, S2-S8->S3-S9). Comments only: logic,
order, reasons, details and state keys are unchanged.

STEP 9 UL retrace capture (added Oct 22 as "STEP 7A"): two flat peak-capture rules for 30-200% MFE winners, each
with its own kill switch (dataclass default off, Shared.env on; both off = the prior engine
exactly): OPTION_PATH_V2_GBUL_ENABLED (guarded retrace) and OPTION_PATH_V2_UFR_ENABLED
(underlying giveback ratio). Both read their own favorable-UL tracker (STATE S2: anchored on
the first valid UL price, not the S1 entry-underlying anchor; a UL quote older than 10s, or a
lost anchor, holds and resets the 2-tick count) and add no hub or chain reads.

STEP 29-30 (Oct 27): ATR loss cut and VWAP cross exit. Both run only after STEP 0-28 returned
no exit, with no runner extension active, past the shared 120s checkpoint
(OPTION_PATH_V2_MIN_HOLD_SECONDS, the same value STEP 16 uses), on a fresh session-tech bar
(osnap session_tech_bar_at <= 180s old; a missing bar time never fires). They read atr and
vwap_distance_pct from the live session-tech overlay on osnap (SESSION_TECH_PATH_KEYS).
Kill switches OPTION_PATH_V2_ATR_LOSS_CUT_ENABLED / OPTION_PATH_V2_VWAP_CROSS_EXIT_ENABLED
(dataclass default off, Shared.env on; off = prior engine exactly). STATE S10 tracks their
2-tick confirmed premium peak.

STEP 17 SGUL hold (Oct 26): shallow_green_exit does not fire while the favorable UL move since
entry is >= +0.5% and the UL has given back <= 0.1% from its best favorable level (STATE S1
tracker: ul_dir / ul_dd, fresh UL quote only). Re-checked every tick. Kill switch
OPTION_PATH_V2_SG_UL_HOLD_ENABLED (dataclass default off, Shared.env on; off = prior engine exactly).

Flattened from the nested V3A engine (sha 261df582...ae47). The 8/19-10/5
replay is identical trade by trade and tick by tick (decision, detail,
telemetry and state).
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

log = logging.getLogger("path_v2_exit")

REASON_IMMATURE = "immature_stress"
REASON_HWM_RTC = "hwm_rtc"
REASON_CATASTROPHIC = "catastrophic_premium_stop"
REASON_SHALLOW_PROOF = "shallow_proof_stop"
REASON_MFE_TRAIL = "mfe_trail"
REASON_PREMIUM_TRAIL = "premium_trail"
REASON_SHALLOW_TIME = "shallow_time_stop"
REASON_GB30 = "gb30_giveback"
REASON_ABSORPTION = "absorption_exit"
REASON_THETA = "theta_exit"
REASON_SHALLOW_GREEN = "shallow_green_exit"
REASON_CONTRACT_FADE = "contract_fade_exit"
REASON_PREMIUM_TRAIL_50_25 = "premium_trail_>50%_25trail"
REASON_PREMIUM_TRAIL_30_10 = "premium_trail_>30%_10trail"
REASON_SPIKE_FAIL = "premium_spike_fail"
REASON_UNDERLYING_MFE_CAPTURE = "underlying_mfe_capture"
REASON_BOUNDED_MFE_CAPTURE = "bounded_mfe_capture"
REASON_TWENTY_PERCENT_SAVE = "twenty_percent_save"
REASON_STALE_FAIL = "premium_stale_fail"
REASON_RUNNER_TIMED_CAPTURE = "runner_timed_capture"
REASON_RUNNER_PROFIT_FLOOR = "runner_profit_floor"
REASON_GB32_TIMED = "gb32_timed_giveback"
REASON_DEEP_STOP = "deep_green_stop"
REASON_SHALLOW_FLOOR = "shallow_floor_stop"  # Oct 6 V3A SF
REASON_STALL_FLOOR = "stall_floor_stop"  # Oct 6 V3A SBE v2-A
REASON_GUARDED_RETRACE = "guarded_retrace_exit"  # Oct 22 STEP 9 GBUL
REASON_UL_GIVEBACK = "ul_giveback_ratio_exit"  # Oct 22 STEP 9 UFR
REASON_ATR_LOSS_CUT = "atr_loss_cut"  # Oct 27 STEP 29
REASON_VWAP_CROSS_EXIT = "vwap_cross_exit"  # Oct 27 STEP 30
DETAIL_ATR_LOSS_CUT = "ATR_LOSS_CUT_30_2ATR"
DETAIL_VWAP_CROSS_EXIT = "VWAP_CROSS_50_025ATR"
DETAIL_IMMATURE = "IMMATURE_STRESS"
DETAIL_HWM_RTC = "HWM_RTC"
DETAIL_SHALLOW_TIME = "SHALLOW_15M_MFE7P5"
DETAIL_GB30 = "GB30_24PP"
DETAIL_ABSORPTION = "ABSORPTION_EXIT"
DETAIL_THETA = "THETA_EXIT"
DETAIL_SHALLOW_GREEN = "SHALLOW_GREEN_EXIT"
DETAIL_CONTRACT_FADE = "CONTRACT_FADE_EXIT"
DETAIL_PREMIUM_TRAIL_50_25 = "PREMIUM_TRAIL_GT50_25TRAIL"
DETAIL_PREMIUM_TRAIL_30_10 = "PREMIUM_TRAIL_GT30_10TRAIL"
DETAIL_SPIKE_FAIL = "SPIKE_FAIL"
DETAIL_UNDERLYING_MFE_CAPTURE = "UL_MFE_CAPTURE_175_250"
DETAIL_BOUNDED_MFE_CAPTURE = "BOUNDED_MFE_CAPTURE_290_305"
DETAIL_TWENTY_PERCENT_SAVE = "P20_STALL_SAVE"
DETAIL_STALE_FAIL = "STALE_HIGH_FAIL"
DETAIL_RUNNER_TIMED_CAPTURE = "RUNNER_TIMED_CAPTURE_350"
DETAIL_RUNNER_PROFIT_FLOOR = "RUNNER_PROFIT_FLOOR_350"
DETAIL_CAT = "CAT_PREM_STOP"
DETAIL_SHALLOW_PROOF = "SHALLOW_PROOF_10_20"
DETAIL_MFE_TRAIL = "MFE_TRAIL_1230"
DETAIL_AE_WARNING = "AE_WARNING_HOLD"
DETAIL_PREMIUM_TRAIL = "PATH_V2_PREMIUM_TRAIL"
DETAIL_EARLY_PREMIUM_TRAIL = "PATH_V2_EARLY_PREM_TRAIL"
DETAIL_GB32_TIMED = "GB32_40_TIMED_15PP"
DETAIL_DEEP_STOP = "DEEP_STOP_10_30_65"
DETAIL_SHALLOW_FLOOR = "SHALLOW_FLOOR_5_10_18"
DETAIL_STALL_FLOOR = "STALL_FLOOR_15_40_4"
DETAIL_GUARDED_RETRACE = "GBUL_RETRACE_05_65"
DETAIL_UL_GIVEBACK = "UFR_GIVEBACK_30_70"
DETAIL_RUNNER_TIMED_CAPTURE_V3A = "RUNNER_TIMED_CAPTURE_350_A30"
DETAIL_RUNNER_PROFIT_FLOOR_V3A = "RUNNER_PROFIT_FLOOR_350_G900"
# Oct 6 R2X runner extension (STEP 1 / STEP 7). No id or detail contains "profit"
# (the router sends "profit" ids to the passive ladder by substring).
REASON_RUNNER_EXTENDED_CAPTURE = "runner_extended_capture"
REASON_RUNNER_EXTENSION_FLOOR = "runner_extension_floor"
DETAIL_RUNNER_EXT_TARGET = "RUNNER_EXT_TARGET"
DETAIL_RUNNER_EXT_ULREV = "RUNNER_EXT_ULREV"
DETAIL_RUNNER_EXT_FLOOR = "RUNNER_EXT_FLOOR"
DETAIL_RUNNER_EXT_START = "RUNNER_EXT_START"  # hold detail (STEP 7 enters hold mode), never an exit
DETAIL_RUNNER_EXT_HOLD = "RUNNER_EXT_HOLD"  # hold detail (STEP 1 holds), never an exit


_STATE_KEY = "path_v2"
_EPS = 1e-12
_ULR_MIN_UMAX = 0.0005  # S2: UFR retrace fraction needs a >0.05% favorable UL move
_PACIFIC_TZ = ZoneInfo("America/Los_Angeles")


@dataclass(frozen=True)
class PathV2Config:
    min_hold_seconds: float = 120.0
    early_cut: float = 0.15
    stress_threshold: float = 0.40
    proof: float = 0.50
    rtc_retrace: float = 0.20
    rtc_material_seconds: float = 300.0
    rtc_vel_threshold: float = -0.0005
    rtc_vel_window_seconds: float = 10.0
    material_hwm_pp: float = 0.05
    catastrophic_prem_stop_pct: float = -0.80
    catastrophic_min_age_seconds: float = 60.0
    # After a +10% to +20% peak, sell if the bid falls to -30%.
    # Profile book 19 Aug–29 Sep: cuts the 29 Sep IWM catastrophic and no winner.
    shallow_proof_stop_enabled: bool = True
    shallow_proof_stop_min_age_seconds: float = 60.0
    shallow_proof_stop_min_mfe: float = 0.10
    shallow_proof_stop_max_mfe: float = 0.20
    shallow_proof_stop_pret: float = -0.30
    # STEP 2 | 20% Save (Nov 3 2026 replacement: +20% stall save, CALL only).
    # Latch at the first tick pret >= MIN_MFE (+20%, exact). Exit a slow (t20 >= 90s),
    # sub-50% peak call that stalled 60s and gave back 15pp while still >= +11%.
    twenty_percent_save_enabled: bool = False
    twenty_percent_save_min_mfe: float = 0.20
    twenty_percent_save_sides: str = "call"
    twenty_percent_save_min_t20_s: float = 90.0
    twenty_percent_save_max_peak: float = 0.50
    twenty_percent_save_min_since_touch_s: float = 60.0
    twenty_percent_save_min_sec_hwm: float = 60.0
    twenty_percent_save_giveback: float = 0.15
    twenty_percent_save_min_pret: float = 0.11
    twenty_percent_save_max_newhigh_frac: float = 0.05
    twenty_percent_save_shadow_only: bool = False
    # STEP 2 extension (default OFF): UL-retrace stop armed at the +20% touch.
    twenty_percent_save_ulr_enabled: bool = False
    twenty_percent_save_ulr_frac: float = 0.90
    twenty_percent_save_ulr_max_entry_vwap_atr: float = 0.50
    # RETIRED from the 20% Save decision (old PVE3 latch). Kept only because the
    # caller's +20% first-touch recorder snapshot (po_mfe20_snapshot) still reads
    # them. Evaluate hardcodes allow_sync_refresh=False; Shared.env must stay false.
    twenty_percent_save_max_pve3_age_seconds: float = 75.0
    twenty_percent_save_sync_refresh: bool = False
    twenty_percent_save_sync_timeout_s: float = 3.0
    stale_max_seconds: float = 10.0
    ae_exp_speed_min: float = 1e-4
    ae_sec_hwm_max: float = 120.0
    ae_warning_enabled: bool = True
    ae_warning_window_seconds: float = 90.0
    ae_warning_stall_seconds: float = 45.0
    ae_warning_material_hwm_pp: float = 0.02
    ae_warning_max_start_return: float = 0.0
    premium_trail_enabled: bool = False
    premium_trail_trigger_pct: float = 1.00
    premium_trail_exit_pct: float = 0.30
    premium_trail_min_sec_since_hwm: float = 900.0
    early_premium_trail_enabled: bool = False
    early_premium_trail_trigger_pct: float = 1.75
    early_premium_trail_exit_pct: float = 0.05
    early_premium_trail_min_current_pct: float = 0.75
    bounded_mfe_capture_enabled: bool = False
    bounded_mfe_capture_min_mfe: float = 2.90
    bounded_mfe_capture_max_mfe: float = 3.05
    bounded_mfe_capture_giveback: float = 0.10
    bounded_mfe_capture_min_pret: float = 1.00
    bounded_mfe_capture_start_pt_hour: float = 10.75
    mfe_trail_trigger_pct: float = 0.10
    mfe_trail_exit_pct: float = 0.10
    shallow_time_stop_enabled: bool = True
    shallow_time_stop_seconds: float = 900.0
    shallow_time_stop_max_mfe: float = 0.075
    gb30_enabled: bool = True
    gb30_min_mfe: float = 0.30
    gb30_giveback: float = 0.24
    gb30_extension_enabled: bool = True
    gb30_extension_giveback: float = 0.38
    gb30_extension_max_ul_dd: float = 0.0009
    gb30_extension_min_pret: float = 0.05
    gb30_extension_max_pret: float = 0.10
    gb32_timed_enabled: bool = True
    gb32_timed_min_mfe: float = 0.32
    gb32_timed_max_mfe: float = 0.40
    gb32_timed_giveback: float = 0.15
    gb32_timed_min_sec_hwm: float = 120.0
    gb32_timed_min_pret: float = 0.05
    deep_stop_enabled: bool = True
    deep_stop_min_age_seconds: float = 60.0
    deep_stop_min_mfe: float = 0.10
    deep_stop_max_mfe: float = 0.30
    deep_stop_pret: float = -0.65
    absorption_exit_enabled: bool = True
    absorption_min_age_seconds: float = 450.0
    absorption_max_speed: float = 5e-05
    absorption_min_pret: float = 0.02
    theta_exit_enabled: bool = False
    theta_exit_min_age_seconds: float = 300.0
    theta_exit_min: float = -0.50
    shallow_green_exit_enabled: bool = True
    shallow_green_min_age_seconds: float = 120.0
    shallow_green_min_mfe: float = 0.02
    shallow_green_max_mfe: float = 0.20
    shallow_green_min_sec_hwm: float = 90.0
    shallow_green_max_vel: float = 0.0
    shallow_green_min_pret: float = 0.0
    # Oct 26 STEP 17 SGUL hold. Dataclass default OFF so a bare PathV2Config() is the prior engine.
    shallow_green_ul_hold_enabled: bool = False
    shallow_green_ul_hold_min_fav: float = 0.005
    shallow_green_ul_hold_max_giveback: float = 0.001
    contract_fade_exit_enabled: bool = True
    contract_fade_max_valid_bid_ask_contracts: float = 13.0
    contract_fade_min_mfe: float = 0.0
    contract_fade_max_mfe: float = 0.20
    contract_fade_min_drawdown: float = 0.02
    contract_fade_min_sec_hwm: float = 15.0
    contract_fade_quote_window_seconds: float = 30.0
    hybrid_premium_trail_enabled: bool = False
    hybrid_aggressive_arm_mfe: float = 0.50
    hybrid_aggressive_giveback: float = 0.25
    hybrid_aggressive_min_age_seconds: float = 60.0
    hybrid_aggressive_min_current_return: float = 0.20
    hybrid_aggressive_max_ul_retrace: float = 0.50
    hybrid_aggressive_min_ul_drawdown: float = 0.001
    hybrid_salvage_arm_mfe: float = 0.30
    hybrid_salvage_giveback: float = 0.10
    hybrid_salvage_min_age_seconds: float = 60.0
    hybrid_salvage_min_current_return: float = 0.05
    hybrid_salvage_max_ul_retrace: float = 0.75
    hybrid_salvage_min_ul_drawdown: float = 0.0
    runner_capture_enabled: bool = True
    runner_capture_arm_mfe: float = 3.50
    runner_capture_timed_min_current_return: float = 4.00
    runner_capture_timed_min_peak_ratio: float = 0.50
    runner_capture_timed_min_arm_age_seconds: float = 60.0
    runner_capture_floor_min_age_seconds: float = 600.0
    runner_capture_floor_peak_ratio: float = 0.80
    spike_fail_enabled: bool = True
    spike_fail_arm_mfe: float = 0.30
    spike_fail_max_mfe: float = 0.60
    spike_fail_giveback: float = 0.15
    spike_fail_min_pret: float = 0.10
    spike_fail_min_age_seconds: float = 60.0
    spike_fail_max_sec_hwm: float = 16.0
    # Mature 175-250% MFE capture, confirmed by an adverse fresh UL retrace.
    # Default off; Shared.env enables the replay-selected band.
    underlying_mfe_capture_enabled: bool = False
    underlying_mfe_capture_min_mfe: float = 1.75
    underlying_mfe_capture_max_mfe: float = 2.50
    underlying_mfe_capture_giveback: float = 0.25
    underlying_mfe_capture_min_pret: float = 0.50
    underlying_mfe_capture_min_sec_hwm: float = 120.0
    underlying_mfe_capture_min_ul_retr: float = 0.50
    underlying_mfe_capture_min_ul_dd: float = 0.003
    stale_fail_enabled: bool = True
    stale_fail_band_min_mfe: float = 0.70
    stale_fail_band_max_mfe: float = 0.90
    stale_fail_band_min_pret: float = 0.02
    stale_fail_band_max_pret: float = 0.08
    stale_fail_band_min_sec_hwm: float = 1000.0
    stale_fail_giveback_min_mfe: float = 0.70
    stale_fail_giveback_max_mfe: float = 0.90
    stale_fail_giveback: float = 0.50
    stale_fail_giveback_min_pret: float = 0.25
    stale_fail_giveback_min_sec_hwm: float = 300.0
    stale_fail_upper_min_mfe: float = 0.80
    stale_fail_upper_max_mfe: float = 0.82
    stale_fail_upper_min_pret: float = 0.02
    stale_fail_upper_max_pret: float = 0.40
    stale_fail_upper_min_sec_hwm: float = 180.0
    stale_fail_upper_min_ul_dd: float = 0.003
    stale_fail_reversal_min_mfe: float = 0.50
    stale_fail_reversal_max_mfe: float = 0.70
    stale_fail_reversal_min_pret: float = 0.02
    stale_fail_reversal_max_pret: float = 0.20
    stale_fail_reversal_min_sec_hwm: float = 200.0
    stale_fail_reversal_min_ul_retr: float = 1.15
    stale_fail_reversal_min_ul_dd: float = 0.004
    stale_fail_gap_min_mfe: float = 0.90
    stale_fail_gap_max_mfe: float = 0.94
    stale_fail_gap_min_pret: float = 0.02
    stale_fail_gap_max_pret: float = 0.20
    stale_fail_gap_min_sec_hwm: float = 800.0
    stale_fail_gap_min_ul_retr: float = 0.55
    stale_fail_high_min_mfe: float = 1.00
    stale_fail_high_min_pret: float = 0.02
    stale_fail_high_max_pret: float = 0.25
    stale_fail_high_min_sec_hwm: float = 1800.0
    stale_fail_runner_min_mfe: float = 1.50
    stale_fail_runner_min_pret: float = 0.02
    stale_fail_runner_max_pret: float = 0.20
    stale_fail_runner_min_ul_retr: float = 0.50
    stale_fail_runner_min_age_seconds: float = 60.0
    # Oct 6 V3A. Dataclass defaults are OFF so a bare PathV2Config() is the
    # package B engine; Shared.env / load_path_v2_config turn them on.
    # shallow_floor_stop (SF)
    shallow_floor_stop_enabled: bool = False
    shallow_floor_min_mfe: float = 0.05
    shallow_floor_max_mfe: float = 0.10
    shallow_floor_min_age_seconds: float = 120.0
    shallow_floor_pret: float = -0.18
    shallow_floor_min_peak_ticks: int = 2
    shallow_floor_require_fresh_cross: bool = True
    # runner timing V3A (RT)
    runner_timing_v3a_enabled: bool = False
    runner_timing_v3a_timed_min_arm_age_seconds: float = 30.0
    runner_timing_v3a_floor_green_min_age_seconds: float = 900.0
    runner_timing_v3a_floor_green_min_pret: float = 0.50
    # stall_floor_stop (SBE v2-A)
    stall_floor_stop_enabled: bool = False
    stall_floor_min_mfe: float = 0.15
    stall_floor_max_mfe: float = 0.40
    stall_floor_min_age_seconds: float = 60.0
    stall_floor_max_age_seconds: float = 3600.0
    stall_floor_min_sec_hwm: float = 1500.0
    stall_floor_floor: float = 0.04
    stall_floor_max_slip: float = 0.03
    stall_floor_arm_above_seconds: float = 30.0
    stall_floor_confirm_ticks: int = 2
    # Oct 6 runner extension (R2X). Default off = V3A + SF4 engine exactly;
    # Shared.env / load_path_v2_config turn it on.
    runner_extension_enabled: bool = False
    runner_extension_target_mult: float = 1.25
    runner_extension_ul_retrace: float = 0.04
    runner_extension_ul_min_mult: float = 1.045
    runner_extension_floor_pret: float = 0.50
    runner_extension_min_ticks: int = 2
    runner_extension_jump_guard: float = 0.5
    # Oct 22 UL retrace capture (STEP 9). Dataclass defaults are OFF so a bare
    # PathV2Config() is the prior engine; Shared.env / load_path_v2_config turn them on.
    # guarded_retrace (GBUL): 30-200% peak, favorable UL fell >= 0.5% off its high, bid >= 0.65 x peak.
    guarded_retrace_enabled: bool = False
    guarded_retrace_min_mfe: float = 0.30
    guarded_retrace_max_mfe: float = 2.00
    guarded_retrace_min_ul_dd: float = 0.005
    guarded_retrace_min_peak_ratio: float = 0.65
    guarded_retrace_confirm_ticks: int = 2
    # ul_giveback_ratio (UFR): 30-200% peak, favorable UL moved >= 0.5% then gave back >= 30% of that
    # move, bid >= 0.70 x peak and >= +3%.
    ul_giveback_enabled: bool = False
    ul_giveback_min_mfe: float = 0.30
    ul_giveback_max_mfe: float = 2.00
    ul_giveback_min_ul_mfe: float = 0.005
    ul_giveback_min_ul_retrace: float = 0.30
    ul_giveback_min_peak_ratio: float = 0.70
    ul_giveback_min_pret: float = 0.03
    ul_giveback_confirm_ticks: int = 2
    # Oct 27 STEP 29 ATR loss cut: bid <= -30%, side-aware UL move since entry <= -2.0 ATR,
    # confirmed peak < +10%, age >= 600s, price on the wrong side of VWAP, fresh bar (<= 180s).
    atr_loss_cut_enabled: bool = False
    atr_loss_cut_max_pret: float = -0.30
    atr_loss_cut_atr_mult: float = 2.0
    atr_loss_cut_max_peak: float = 0.10
    atr_loss_cut_min_age_s: float = 600.0
    atr_loss_cut_require_vwap_against: bool = True
    atr_loss_cut_bar_max_age_s: float = 180.0
    # Oct 27 STEP 30 VWAP cross exit: after the price was on the right side of VWAP and the
    # confirmed peak reached +50%, exit when it is >= 0.25 ATR on the wrong side for 1 bar and
    # bid >= +10%. Age gate = the shared 120s checkpoint (min_hold_seconds, STEP 16).
    vwap_cross_exit_enabled: bool = False
    vwap_cross_min_peak: float = 0.50
    vwap_cross_atr_buffer: float = 0.25
    vwap_cross_confirm_bars: int = 1
    vwap_cross_min_pret: float = 0.10
    vwap_cross_bar_max_age_s: float = 180.0
@dataclass
class PathV2Decision:
    exit_reason: Optional[str]
    detail: str
    telemetry: Dict[str, Any]


def _config_raw(key: str, default: Any) -> Any:
    if key in os.environ:
        return os.environ[key]
    try:
        from modules.config_loader import get_config_value

        return get_config_value(key, default)
    except Exception:
        return default


def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off", ""):
        return False
    return default


def _as_float(value: Any, default: float) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float(default)
    if out != out:
        return float(default)
    return out


def _priority_optimizer_session_tech_max_age_sec() -> float:
    """Read the one authoritative session-tech freshness cap.

    PATH_V2 reads the same 1m-bar cache as the Priority Optimizer. Its 20% Save
    exit therefore follows the decision-freeze policy, not a separate knob.
    """
    try:
        from central_tech.decision_freeze import session_tech_max_age_sec

        return float(session_tech_max_age_sec())
    except Exception:
        return 90.0


def load_path_v2_config() -> PathV2Config:
    """Frozen PATH_V2 parameters. Defaults match the research walker."""
    return PathV2Config(
        min_hold_seconds=_as_float(_config_raw("OPTION_PATH_V2_MIN_HOLD_SECONDS", 120), 120.0),
        early_cut=_as_float(_config_raw("OPTION_PATH_V2_EARLY_CUT", 0.15), 0.15),
        stress_threshold=_as_float(_config_raw("OPTION_PATH_V2_STRESS_THR", 0.40), 0.40),
        proof=_as_float(_config_raw("OPTION_PATH_V2_PROOF", 0.50), 0.50),
        rtc_retrace=_as_float(_config_raw("OPTION_PATH_V2_RTC_RETRACE", 0.20), 0.20),
        rtc_material_seconds=_as_float(_config_raw("OPTION_PATH_V2_RTC_MATERIAL_SEC", 300), 300.0),
        rtc_vel_threshold=_as_float(_config_raw("OPTION_PATH_V2_RTC_VEL", -0.0005), -0.0005),
        rtc_vel_window_seconds=_as_float(_config_raw("OPTION_PATH_V2_RTC_VEL_WINDOW", 10), 10.0),
        material_hwm_pp=_as_float(_config_raw("OPTION_PATH_V2_MATERIAL_HWM_PP", 0.05), 0.05),
        catastrophic_prem_stop_pct=_as_float(
            _config_raw("OPTION_PATH_V2_CAT_FLOOR", -0.80), -0.80
        ),
        catastrophic_min_age_seconds=_as_float(
            _config_raw("OPTION_PATH_V2_CAT_MIN_AGE_SECONDS", 60), 60.0
        ),
        shallow_proof_stop_enabled=_as_bool(
            _config_raw("OPTION_PATH_V2_SHALLOW_PROOF_STOP_ENABLED", True), True
        ),
        shallow_proof_stop_min_age_seconds=_as_float(
            _config_raw("OPTION_PATH_V2_SHALLOW_PROOF_MIN_AGE_SECONDS", 60), 60.0
        ),
        shallow_proof_stop_min_mfe=_as_float(
            _config_raw("OPTION_PATH_V2_SHALLOW_PROOF_MIN_MFE", 0.10), 0.10
        ),
        shallow_proof_stop_max_mfe=_as_float(
            _config_raw("OPTION_PATH_V2_SHALLOW_PROOF_MAX_MFE", 0.20), 0.20
        ),
        shallow_proof_stop_pret=_as_float(
            _config_raw("OPTION_PATH_V2_SHALLOW_PROOF_PRET", -0.30), -0.30
        ),
        twenty_percent_save_enabled=_as_bool(
            _config_raw("OPTION_PATH_V2_20P_SAVE_ENABLED", False), False
        ),
        twenty_percent_save_min_mfe=_as_float(
            _config_raw("OPTION_PATH_V2_20P_SAVE_MIN_MFE", 0.20), 0.20
        ),
        twenty_percent_save_sides=str(
            _config_raw("OPTION_PATH_V2_20P_SAVE_SIDES", "call") or "call"
        ).strip().lower(),
        twenty_percent_save_min_t20_s=_as_float(
            _config_raw("OPTION_PATH_V2_20P_SAVE_MIN_T20_S", 90.0), 90.0
        ),
        twenty_percent_save_max_peak=_as_float(
            _config_raw("OPTION_PATH_V2_20P_SAVE_MAX_PEAK", 0.50), 0.50
        ),
        twenty_percent_save_min_since_touch_s=_as_float(
            _config_raw("OPTION_PATH_V2_20P_SAVE_MIN_SINCE_TOUCH_S", 60.0), 60.0
        ),
        twenty_percent_save_min_sec_hwm=_as_float(
            _config_raw("OPTION_PATH_V2_20P_SAVE_MIN_SEC_HWM", 60.0), 60.0
        ),
        twenty_percent_save_giveback=_as_float(
            _config_raw("OPTION_PATH_V2_20P_SAVE_GIVEBACK", 0.15), 0.15
        ),
        twenty_percent_save_min_pret=_as_float(
            _config_raw("OPTION_PATH_V2_20P_SAVE_MIN_PRET", 0.11), 0.11
        ),
        twenty_percent_save_max_newhigh_frac=_as_float(
            _config_raw("OPTION_PATH_V2_20P_SAVE_MAX_NEWHIGH_FRAC", 0.05), 0.05
        ),
        twenty_percent_save_shadow_only=_as_bool(
            _config_raw("OPTION_PATH_V2_20P_SAVE_SHADOW_ONLY", False), False
        ),
        twenty_percent_save_ulr_enabled=_as_bool(
            _config_raw("OPTION_PATH_V2_20P_SAVE_ULR_ENABLED", False), False
        ),
        twenty_percent_save_ulr_frac=_as_float(
            _config_raw("OPTION_PATH_V2_20P_SAVE_ULR_FRAC", 0.90), 0.90
        ),
        twenty_percent_save_ulr_max_entry_vwap_atr=_as_float(
            _config_raw("OPTION_PATH_V2_20P_SAVE_ULR_MAX_ENTRY_VWAP_ATR", 0.50), 0.50
        ),
        twenty_percent_save_max_pve3_age_seconds=_as_float(
            _config_raw("OPTION_PATH_V2_20P_SAVE_MAX_PVE3_AGE_SEC", 75.0), 75.0
        ),
        twenty_percent_save_sync_refresh=_as_bool(
            _config_raw("OPTION_PATH_V2_20P_SAVE_SYNC_REFRESH", False), False
        ),
        twenty_percent_save_sync_timeout_s=_as_float(
            _config_raw("OPTION_PATH_V2_20P_SAVE_SYNC_TIMEOUT_SEC", 3.0), 3.0
        ),
        stale_max_seconds=_as_float(_config_raw("OPTION_PATH_V2_STALE_MAX_SECONDS", 10), 10.0),
        ae_exp_speed_min=_as_float(_config_raw("OPTION_PATH_V2_AE_EXP_SPEED_MIN", 1e-4), 1e-4),
        ae_sec_hwm_max=_as_float(_config_raw("OPTION_PATH_V2_AE_SEC_HWM_MAX", 120), 120.0),
        ae_warning_enabled=_as_bool(
            _config_raw("OPTION_PATH_V2_AE_WARNING_ENABLED", True), True
        ),
        ae_warning_window_seconds=_as_float(
            _config_raw("OPTION_PATH_V2_AE_WARNING_WINDOW_SECONDS", 90), 90.0
        ),
        ae_warning_stall_seconds=_as_float(
            _config_raw("OPTION_PATH_V2_AE_WARNING_STALL_SECONDS", 45), 45.0
        ),
        ae_warning_material_hwm_pp=_as_float(
            _config_raw("OPTION_PATH_V2_AE_WARNING_MATERIAL_HWM_PP", 0.02), 0.02
        ),
        ae_warning_max_start_return=_as_float(
            _config_raw("OPTION_PATH_V2_AE_WARNING_MAX_START_RETURN", 0.0), 0.0
        ),
        premium_trail_enabled=_as_bool(
            _config_raw("OPTION_PATH_V2_PREMIUM_TRAIL_ENABLED", False), False
        ),
        premium_trail_trigger_pct=_as_float(
            _config_raw("OPTION_PATH_V2_PREMIUM_TRAIL_TRIGGER", 1.00), 1.00
        ),
        premium_trail_exit_pct=_as_float(
            _config_raw("OPTION_PATH_V2_PREMIUM_TRAIL_EXIT", 0.30), 0.30
        ),
        premium_trail_min_sec_since_hwm=_as_float(
            _config_raw("OPTION_PATH_V2_PREMIUM_TRAIL_MIN_SEC_HWM", 900), 900.0
        ),
        early_premium_trail_enabled=_as_bool(
            _config_raw("OPTION_PATH_V2_EARLY_PREM_TRAIL_ENABLED", False), False
        ),
        early_premium_trail_trigger_pct=_as_float(
            _config_raw("OPTION_PATH_V2_EARLY_PREM_TRAIL_TRIGGER", 1.75), 1.75
        ),
        early_premium_trail_exit_pct=_as_float(
            _config_raw("OPTION_PATH_V2_EARLY_PREM_TRAIL_EXIT", 0.05), 0.05
        ),
        early_premium_trail_min_current_pct=_as_float(
            _config_raw("OPTION_PATH_V2_EARLY_PREM_TRAIL_MIN_CURRENT", 0.75), 0.75
        ),
        bounded_mfe_capture_enabled=_as_bool(
            _config_raw("OPTION_PATH_V2_BOUNDED_MFE_CAPTURE_ENABLED", False), False
        ),
        bounded_mfe_capture_min_mfe=_as_float(
            _config_raw("OPTION_PATH_V2_BOUNDED_MFE_CAPTURE_MIN_MFE", 2.90), 2.90
        ),
        bounded_mfe_capture_max_mfe=_as_float(
            _config_raw("OPTION_PATH_V2_BOUNDED_MFE_CAPTURE_MAX_MFE", 3.05), 3.05
        ),
        bounded_mfe_capture_giveback=_as_float(
            _config_raw("OPTION_PATH_V2_BOUNDED_MFE_CAPTURE_GIVEBACK", 0.10), 0.10
        ),
        bounded_mfe_capture_min_pret=_as_float(
            _config_raw("OPTION_PATH_V2_BOUNDED_MFE_CAPTURE_MIN_PRET", 1.00), 1.00
        ),
        bounded_mfe_capture_start_pt_hour=_as_float(
            _config_raw("OPTION_PATH_V2_BOUNDED_MFE_CAPTURE_START_PT_HOUR", 10.75), 10.75
        ),
        mfe_trail_trigger_pct=_as_float(_config_raw("OPTION_SINGLE_MFE_TRAIL_TRIGGER", 0.10), 0.10),
        mfe_trail_exit_pct=_as_float(_config_raw("OPTION_SINGLE_MFE_TRAIL_EXIT", 0.10), 0.10),
        shallow_time_stop_enabled=_as_bool(
            _config_raw("OPTION_PATH_V2_SHALLOW_TIME_STOP_ENABLED", True), True
        ),
        shallow_time_stop_seconds=_as_float(
            _config_raw("OPTION_PATH_V2_SHALLOW_TIME_STOP_SECONDS", 900), 900.0
        ),
        shallow_time_stop_max_mfe=_as_float(
            _config_raw("OPTION_PATH_V2_SHALLOW_TIME_STOP_MAX_MFE", 0.075), 0.075
        ),
        gb30_enabled=_as_bool(_config_raw("OPTION_PATH_V2_GB30_ENABLED", True), True),
        gb30_min_mfe=_as_float(_config_raw("OPTION_PATH_V2_GB30_MIN_MFE", 0.30), 0.30),
        gb30_giveback=_as_float(_config_raw("OPTION_PATH_V2_GB30_GIVEBACK", 0.24), 0.24),
        gb30_extension_enabled=_as_bool(
            _config_raw("OPTION_PATH_V2_GB30_EXTENSION_ENABLED", True), True
        ),
        gb30_extension_giveback=_as_float(
            _config_raw("OPTION_PATH_V2_GB30_EXTENSION_GIVEBACK", 0.38), 0.38
        ),
        gb30_extension_max_ul_dd=_as_float(
            _config_raw("OPTION_PATH_V2_GB30_EXTENSION_MAX_UL_DD", 0.0009), 0.0009
        ),
        gb30_extension_min_pret=_as_float(
            _config_raw("OPTION_PATH_V2_GB30_EXTENSION_MIN_PRET", 0.05), 0.05
        ),
        gb30_extension_max_pret=_as_float(
            _config_raw("OPTION_PATH_V2_GB30_EXTENSION_MAX_PRET", 0.10), 0.10
        ),
        gb32_timed_enabled=_as_bool(_config_raw("OPTION_PATH_V2_GB32_TIMED_ENABLED", True), True),
        gb32_timed_min_mfe=_as_float(_config_raw("OPTION_PATH_V2_GB32_TIMED_MIN_MFE", 0.32), 0.32),
        gb32_timed_max_mfe=_as_float(_config_raw("OPTION_PATH_V2_GB32_TIMED_MAX_MFE", 0.40), 0.40),
        gb32_timed_giveback=_as_float(
            _config_raw("OPTION_PATH_V2_GB32_TIMED_GIVEBACK", 0.15), 0.15
        ),
        gb32_timed_min_sec_hwm=_as_float(
            _config_raw("OPTION_PATH_V2_GB32_TIMED_MIN_SEC_HWM", 120), 120.0
        ),
        gb32_timed_min_pret=_as_float(
            _config_raw("OPTION_PATH_V2_GB32_TIMED_MIN_PRET", 0.05), 0.05
        ),
        deep_stop_enabled=_as_bool(_config_raw("OPTION_PATH_V2_DEEP_STOP_ENABLED", True), True),
        deep_stop_min_age_seconds=_as_float(
            _config_raw("OPTION_PATH_V2_DEEP_STOP_MIN_AGE_SECONDS", 60), 60.0
        ),
        deep_stop_min_mfe=_as_float(_config_raw("OPTION_PATH_V2_DEEP_STOP_MIN_MFE", 0.10), 0.10),
        deep_stop_max_mfe=_as_float(_config_raw("OPTION_PATH_V2_DEEP_STOP_MAX_MFE", 0.30), 0.30),
        deep_stop_pret=_as_float(_config_raw("OPTION_PATH_V2_DEEP_STOP_PRET", -0.65), -0.65),
        absorption_exit_enabled=_as_bool(
            _config_raw("OPTION_PATH_V2_ABSORPTION_EXIT_ENABLED", True), True
        ),
        absorption_min_age_seconds=_as_float(
            _config_raw("OPTION_PATH_V2_ABSORPTION_MIN_AGE_SECONDS", 450), 450.0
        ),
        absorption_max_speed=_as_float(
            _config_raw("OPTION_PATH_V2_ABSORPTION_MAX_SPEED", 5e-05), 5e-05
        ),
        absorption_min_pret=_as_float(
            _config_raw("OPTION_PATH_V2_ABSORPTION_MIN_PRET", 0.02), 0.02
        ),
        theta_exit_enabled=_as_bool(
            _config_raw("OPTION_PATH_V2_THETA_EXIT_ENABLED", False), False
        ),
        theta_exit_min_age_seconds=_as_float(
            _config_raw("OPTION_PATH_V2_THETA_EXIT_MIN_AGE_SECONDS", 300), 300.0
        ),
        theta_exit_min=_as_float(_config_raw("OPTION_PATH_V2_THETA_EXIT_MIN", -0.50), -0.50),
        shallow_green_exit_enabled=_as_bool(
            _config_raw("OPTION_PATH_V2_SHALLOW_GREEN_EXIT_ENABLED", True), True
        ),
        shallow_green_min_age_seconds=_as_float(
            _config_raw("OPTION_PATH_V2_SHALLOW_GREEN_MIN_AGE_SECONDS", 120), 120.0
        ),
        shallow_green_min_mfe=_as_float(
            _config_raw("OPTION_PATH_V2_SHALLOW_GREEN_MIN_MFE", 0.02), 0.02
        ),
        shallow_green_max_mfe=_as_float(
            _config_raw("OPTION_PATH_V2_SHALLOW_GREEN_MAX_MFE", 0.20), 0.20
        ),
        shallow_green_min_sec_hwm=_as_float(
            _config_raw("OPTION_PATH_V2_SHALLOW_GREEN_MIN_SEC_HWM", 90), 90.0
        ),
        shallow_green_max_vel=_as_float(
            _config_raw("OPTION_PATH_V2_SHALLOW_GREEN_MAX_VEL", 0), 0.0
        ),
        shallow_green_min_pret=_as_float(
            _config_raw("OPTION_PATH_V2_SHALLOW_GREEN_MIN_PRET", 0), 0.0
        ),
        shallow_green_ul_hold_enabled=_as_bool(
            _config_raw("OPTION_PATH_V2_SG_UL_HOLD_ENABLED", False), False
        ),
        shallow_green_ul_hold_min_fav=_as_float(
            _config_raw("OPTION_PATH_V2_SG_UL_HOLD_MIN_FAV", 0.005), 0.005
        ),
        shallow_green_ul_hold_max_giveback=_as_float(
            _config_raw("OPTION_PATH_V2_SG_UL_HOLD_MAX_GIVEBACK", 0.001), 0.001
        ),
        contract_fade_exit_enabled=_as_bool(
            _config_raw("OPTION_PATH_V2_CONTRACT_FADE_EXIT_ENABLED", True), True
        ),
        contract_fade_max_valid_bid_ask_contracts=_as_float(
            _config_raw("OPTION_PATH_V2_CONTRACT_FADE_MAX_VALID_BID_ASK_CONTRACTS", 13), 13.0
        ),
        contract_fade_min_mfe=_as_float(
            _config_raw("OPTION_PATH_V2_CONTRACT_FADE_MIN_MFE", 0), 0.0
        ),
        contract_fade_max_mfe=_as_float(
            _config_raw("OPTION_PATH_V2_CONTRACT_FADE_MAX_MFE", 0.20), 0.20
        ),
        contract_fade_min_drawdown=_as_float(
            _config_raw("OPTION_PATH_V2_CONTRACT_FADE_MIN_DRAWDOWN", 0.02), 0.02
        ),
        contract_fade_min_sec_hwm=_as_float(
            _config_raw("OPTION_PATH_V2_CONTRACT_FADE_MIN_SEC_HWM", 15), 15.0
        ),
        contract_fade_quote_window_seconds=_as_float(
            _config_raw("OPTION_PATH_V2_CONTRACT_FADE_QUOTE_WINDOW_SECONDS", 30), 30.0
        ),
        hybrid_premium_trail_enabled=_as_bool(
            _config_raw("OPTION_PATH_V2_HYBRID_PREMIUM_TRAIL_ENABLED", False), False
        ),
        hybrid_aggressive_arm_mfe=_as_float(
            _config_raw("OPTION_PATH_V2_PREMIUM_TRAIL_GT50_ARM_MFE", 0.50), 0.50
        ),
        hybrid_aggressive_giveback=_as_float(
            _config_raw("OPTION_PATH_V2_PREMIUM_TRAIL_GT50_GIVEBACK", 0.25), 0.25
        ),
        hybrid_aggressive_min_age_seconds=_as_float(
            _config_raw("OPTION_PATH_V2_PREMIUM_TRAIL_GT50_MIN_AGE_SECONDS", 60), 60.0
        ),
        hybrid_aggressive_min_current_return=_as_float(
            _config_raw("OPTION_PATH_V2_PREMIUM_TRAIL_GT50_MIN_CURRENT_RETURN", 0.20), 0.20
        ),
        hybrid_aggressive_max_ul_retrace=_as_float(
            _config_raw("OPTION_PATH_V2_PREMIUM_TRAIL_GT50_MAX_UL_RETRACE", 0.50), 0.50
        ),
        hybrid_aggressive_min_ul_drawdown=_as_float(
            _config_raw("OPTION_PATH_V2_PREMIUM_TRAIL_GT50_MIN_UL_DRAWDOWN", 0.001), 0.001
        ),
        hybrid_salvage_arm_mfe=_as_float(
            _config_raw("OPTION_PATH_V2_PREMIUM_TRAIL_GT30_ARM_MFE", 0.30), 0.30
        ),
        hybrid_salvage_giveback=_as_float(
            _config_raw("OPTION_PATH_V2_PREMIUM_TRAIL_GT30_GIVEBACK", 0.10), 0.10
        ),
        hybrid_salvage_min_age_seconds=_as_float(
            _config_raw("OPTION_PATH_V2_PREMIUM_TRAIL_GT30_MIN_AGE_SECONDS", 60), 60.0
        ),
        hybrid_salvage_min_current_return=_as_float(
            _config_raw("OPTION_PATH_V2_PREMIUM_TRAIL_GT30_MIN_CURRENT_RETURN", 0.05), 0.05
        ),
        hybrid_salvage_max_ul_retrace=_as_float(
            _config_raw("OPTION_PATH_V2_PREMIUM_TRAIL_GT30_MAX_UL_RETRACE", 0.75), 0.75
        ),
        hybrid_salvage_min_ul_drawdown=_as_float(
            _config_raw("OPTION_PATH_V2_PREMIUM_TRAIL_GT30_MIN_UL_DRAWDOWN", 0.0), 0.0
        ),
        runner_capture_enabled=_as_bool(
            _config_raw("OPTION_PATH_V2_RUNNER_CAPTURE_ENABLED", True), True
        ),
        runner_capture_arm_mfe=_as_float(
            _config_raw("OPTION_PATH_V2_RUNNER_CAPTURE_ARM_MFE", 3.50), 3.50
        ),
        runner_capture_timed_min_current_return=_as_float(
            _config_raw("OPTION_PATH_V2_RUNNER_CAPTURE_TIMED_MIN_CURRENT_RETURN", 4.00), 4.00
        ),
        runner_capture_timed_min_peak_ratio=_as_float(
            _config_raw("OPTION_PATH_V2_RUNNER_CAPTURE_TIMED_MIN_PEAK_RATIO", 0.50), 0.50
        ),
        runner_capture_timed_min_arm_age_seconds=_as_float(
            _config_raw("OPTION_PATH_V2_RUNNER_CAPTURE_TIMED_MIN_ARM_AGE_SECONDS", 60), 60.0
        ),
        runner_capture_floor_min_age_seconds=_as_float(
            _config_raw("OPTION_PATH_V2_RUNNER_CAPTURE_FLOOR_MIN_AGE_SECONDS", 600), 600.0
        ),
        runner_capture_floor_peak_ratio=_as_float(
            _config_raw("OPTION_PATH_V2_RUNNER_CAPTURE_FLOOR_PEAK_RATIO", 0.80), 0.80
        ),
        spike_fail_enabled=_as_bool(
            _config_raw("OPTION_PATH_V2_SPIKE_FAIL_ENABLED", True), True
        ),
        spike_fail_arm_mfe=_as_float(
            _config_raw("OPTION_PATH_V2_SPIKE_FAIL_ARM_MFE", 0.30), 0.30
        ),
        spike_fail_max_mfe=_as_float(
            _config_raw("OPTION_PATH_V2_SPIKE_FAIL_MAX_MFE", 0.60), 0.60
        ),
        spike_fail_giveback=_as_float(
            _config_raw("OPTION_PATH_V2_SPIKE_FAIL_GIVEBACK", 0.15), 0.15
        ),
        spike_fail_min_pret=_as_float(
            _config_raw("OPTION_PATH_V2_SPIKE_FAIL_MIN_PRET", 0.10), 0.10
        ),
        spike_fail_min_age_seconds=_as_float(
            _config_raw("OPTION_PATH_V2_SPIKE_FAIL_MIN_AGE_SECONDS", 60), 60.0
        ),
        spike_fail_max_sec_hwm=_as_float(
            _config_raw("OPTION_PATH_V2_SPIKE_FAIL_MAX_SEC_HWM", 16), 16.0
        ),
        underlying_mfe_capture_enabled=_as_bool(
            _config_raw("OPTION_PATH_V2_UNDERLYING_MFE_CAPTURE_ENABLED", False), False
        ),
        underlying_mfe_capture_min_mfe=_as_float(
            _config_raw("OPTION_PATH_V2_UNDERLYING_MFE_CAPTURE_MIN_MFE", 1.75), 1.75
        ),
        underlying_mfe_capture_max_mfe=_as_float(
            _config_raw("OPTION_PATH_V2_UNDERLYING_MFE_CAPTURE_MAX_MFE", 2.50), 2.50
        ),
        underlying_mfe_capture_giveback=_as_float(
            _config_raw("OPTION_PATH_V2_UNDERLYING_MFE_CAPTURE_GIVEBACK", 0.25), 0.25
        ),
        underlying_mfe_capture_min_pret=_as_float(
            _config_raw("OPTION_PATH_V2_UNDERLYING_MFE_CAPTURE_MIN_PRET", 0.50), 0.50
        ),
        underlying_mfe_capture_min_sec_hwm=_as_float(
            _config_raw("OPTION_PATH_V2_UNDERLYING_MFE_CAPTURE_MIN_SEC_HWM", 120), 120.0
        ),
        underlying_mfe_capture_min_ul_retr=_as_float(
            _config_raw("OPTION_PATH_V2_UNDERLYING_MFE_CAPTURE_MIN_UL_RETRACE", 0.50), 0.50
        ),
        underlying_mfe_capture_min_ul_dd=_as_float(
            _config_raw("OPTION_PATH_V2_UNDERLYING_MFE_CAPTURE_MIN_UL_DD", 0.003), 0.003
        ),
        stale_fail_enabled=_as_bool(
            _config_raw("OPTION_PATH_V2_STALE_FAIL_ENABLED", True), True
        ),
        stale_fail_band_min_mfe=_as_float(
            _config_raw("OPTION_PATH_V2_STALE_FAIL_BAND_MIN_MFE", 0.70), 0.70
        ),
        stale_fail_band_max_mfe=_as_float(
            _config_raw("OPTION_PATH_V2_STALE_FAIL_BAND_MAX_MFE", 0.90), 0.90
        ),
        stale_fail_band_min_pret=_as_float(
            _config_raw("OPTION_PATH_V2_STALE_FAIL_BAND_MIN_PRET", 0.02), 0.02
        ),
        stale_fail_band_max_pret=_as_float(
            _config_raw("OPTION_PATH_V2_STALE_FAIL_BAND_MAX_PRET", 0.08), 0.08
        ),
        stale_fail_band_min_sec_hwm=_as_float(
            _config_raw("OPTION_PATH_V2_STALE_FAIL_BAND_MIN_SEC_HWM", 1000), 1000.0
        ),
        stale_fail_giveback_min_mfe=_as_float(
            _config_raw("OPTION_PATH_V2_STALE_FAIL_GIVEBACK_MIN_MFE", 0.70), 0.70
        ),
        stale_fail_giveback_max_mfe=_as_float(
            _config_raw("OPTION_PATH_V2_STALE_FAIL_GIVEBACK_MAX_MFE", 0.90), 0.90
        ),
        stale_fail_giveback=_as_float(
            _config_raw("OPTION_PATH_V2_STALE_FAIL_GIVEBACK", 0.50), 0.50
        ),
        stale_fail_giveback_min_pret=_as_float(
            _config_raw("OPTION_PATH_V2_STALE_FAIL_GIVEBACK_MIN_PRET", 0.25), 0.25
        ),
        stale_fail_giveback_min_sec_hwm=_as_float(
            _config_raw("OPTION_PATH_V2_STALE_FAIL_GIVEBACK_MIN_SEC_HWM", 300), 300.0
        ),
        stale_fail_upper_min_mfe=_as_float(
            _config_raw("OPTION_PATH_V2_STALE_FAIL_UPPER_MIN_MFE", 0.80), 0.80
        ),
        stale_fail_upper_max_mfe=_as_float(
            _config_raw("OPTION_PATH_V2_STALE_FAIL_UPPER_MAX_MFE", 0.82), 0.82
        ),
        stale_fail_upper_min_pret=_as_float(
            _config_raw("OPTION_PATH_V2_STALE_FAIL_UPPER_MIN_PRET", 0.02), 0.02
        ),
        stale_fail_upper_max_pret=_as_float(
            _config_raw("OPTION_PATH_V2_STALE_FAIL_UPPER_MAX_PRET", 0.40), 0.40
        ),
        stale_fail_upper_min_sec_hwm=_as_float(
            _config_raw("OPTION_PATH_V2_STALE_FAIL_UPPER_MIN_SEC_HWM", 180), 180.0
        ),
        stale_fail_upper_min_ul_dd=_as_float(
            _config_raw("OPTION_PATH_V2_STALE_FAIL_UPPER_MIN_UL_DD", 0.003), 0.003
        ),
        stale_fail_reversal_min_mfe=_as_float(
            _config_raw("OPTION_PATH_V2_STALE_FAIL_REVERSAL_MIN_MFE", 0.50), 0.50
        ),
        stale_fail_reversal_max_mfe=_as_float(
            _config_raw("OPTION_PATH_V2_STALE_FAIL_REVERSAL_MAX_MFE", 0.70), 0.70
        ),
        stale_fail_reversal_min_pret=_as_float(
            _config_raw("OPTION_PATH_V2_STALE_FAIL_REVERSAL_MIN_PRET", 0.02), 0.02
        ),
        stale_fail_reversal_max_pret=_as_float(
            _config_raw("OPTION_PATH_V2_STALE_FAIL_REVERSAL_MAX_PRET", 0.20), 0.20
        ),
        stale_fail_reversal_min_sec_hwm=_as_float(
            _config_raw("OPTION_PATH_V2_STALE_FAIL_REVERSAL_MIN_SEC_HWM", 200), 200.0
        ),
        stale_fail_reversal_min_ul_retr=_as_float(
            _config_raw("OPTION_PATH_V2_STALE_FAIL_REVERSAL_MIN_UL_RETRACE", 1.15), 1.15
        ),
        stale_fail_reversal_min_ul_dd=_as_float(
            _config_raw("OPTION_PATH_V2_STALE_FAIL_REVERSAL_MIN_UL_DD", 0.004), 0.004
        ),
        stale_fail_gap_min_mfe=_as_float(
            _config_raw("OPTION_PATH_V2_STALE_FAIL_GAP_MIN_MFE", 0.90), 0.90
        ),
        stale_fail_gap_max_mfe=_as_float(
            _config_raw("OPTION_PATH_V2_STALE_FAIL_GAP_MAX_MFE", 0.94), 0.94
        ),
        stale_fail_gap_min_pret=_as_float(
            _config_raw("OPTION_PATH_V2_STALE_FAIL_GAP_MIN_PRET", 0.02), 0.02
        ),
        stale_fail_gap_max_pret=_as_float(
            _config_raw("OPTION_PATH_V2_STALE_FAIL_GAP_MAX_PRET", 0.20), 0.20
        ),
        stale_fail_gap_min_sec_hwm=_as_float(
            _config_raw("OPTION_PATH_V2_STALE_FAIL_GAP_MIN_SEC_HWM", 800), 800.0
        ),
        stale_fail_gap_min_ul_retr=_as_float(
            _config_raw("OPTION_PATH_V2_STALE_FAIL_GAP_MIN_UL_RETRACE", 0.55), 0.55
        ),
        stale_fail_high_min_mfe=_as_float(
            _config_raw("OPTION_PATH_V2_STALE_FAIL_HIGH_MIN_MFE", 1.00), 1.00
        ),
        stale_fail_high_min_pret=_as_float(
            _config_raw("OPTION_PATH_V2_STALE_FAIL_HIGH_MIN_PRET", 0.02), 0.02
        ),
        stale_fail_high_max_pret=_as_float(
            _config_raw("OPTION_PATH_V2_STALE_FAIL_HIGH_MAX_PRET", 0.25), 0.25
        ),
        stale_fail_high_min_sec_hwm=_as_float(
            _config_raw("OPTION_PATH_V2_STALE_FAIL_HIGH_MIN_SEC_HWM", 1800), 1800.0
        ),
        stale_fail_runner_min_mfe=_as_float(
            _config_raw("OPTION_PATH_V2_STALE_FAIL_RUNNER_MIN_MFE", 1.50), 1.50
        ),
        stale_fail_runner_min_pret=_as_float(
            _config_raw("OPTION_PATH_V2_STALE_FAIL_RUNNER_MIN_PRET", 0.02), 0.02
        ),
        stale_fail_runner_max_pret=_as_float(
            _config_raw("OPTION_PATH_V2_STALE_FAIL_RUNNER_MAX_PRET", 0.20), 0.20
        ),
        stale_fail_runner_min_ul_retr=_as_float(
            _config_raw("OPTION_PATH_V2_STALE_FAIL_RUNNER_MIN_UL_RETRACE", 0.50), 0.50
        ),
        stale_fail_runner_min_age_seconds=_as_float(
            _config_raw("OPTION_PATH_V2_STALE_FAIL_RUNNER_MIN_AGE_SECONDS", 60), 60.0
        ),
        # Oct 6 V3A (Shared.env §2b). Code default off; Shared.env turns each on.
        shallow_floor_stop_enabled=_as_bool(
            _config_raw("OPTION_PATH_V2_SHALLOW_FLOOR_STOP_ENABLED", False), False
        ),
        shallow_floor_min_mfe=_as_float(
            _config_raw("OPTION_PATH_V2_SHALLOW_FLOOR_MIN_MFE", 0.05), 0.05
        ),
        shallow_floor_max_mfe=_as_float(
            _config_raw("OPTION_PATH_V2_SHALLOW_FLOOR_MAX_MFE", 0.10), 0.10
        ),
        shallow_floor_min_age_seconds=_as_float(
            _config_raw("OPTION_PATH_V2_SHALLOW_FLOOR_MIN_AGE_SECONDS", 120), 120.0
        ),
        shallow_floor_pret=_as_float(
            _config_raw("OPTION_PATH_V2_SHALLOW_FLOOR_PRET", -0.18), -0.18
        ),
        shallow_floor_min_peak_ticks=int(
            _as_float(_config_raw("OPTION_PATH_V2_SHALLOW_FLOOR_MIN_PEAK_TICKS", 2), 2.0)
        ),
        shallow_floor_require_fresh_cross=_as_bool(
            _config_raw("OPTION_PATH_V2_SHALLOW_FLOOR_REQUIRE_FRESH_CROSS", True), True
        ),
        runner_timing_v3a_enabled=_as_bool(
            _config_raw("OPTION_PATH_V2_RUNNER_TIMING_V3A_ENABLED", False), False
        ),
        runner_timing_v3a_timed_min_arm_age_seconds=_as_float(
            _config_raw("OPTION_PATH_V2_RUNNER_TIMING_V3A_TIMED_MIN_ARM_AGE_SECONDS", 30), 30.0
        ),
        runner_timing_v3a_floor_green_min_age_seconds=_as_float(
            _config_raw("OPTION_PATH_V2_RUNNER_TIMING_V3A_FLOOR_GREEN_MIN_AGE_SECONDS", 900), 900.0
        ),
        runner_timing_v3a_floor_green_min_pret=_as_float(
            _config_raw("OPTION_PATH_V2_RUNNER_TIMING_V3A_FLOOR_GREEN_MIN_PRET", 0.50), 0.50
        ),
        stall_floor_stop_enabled=_as_bool(
            _config_raw("OPTION_PATH_V2_STALL_FLOOR_STOP_ENABLED", False), False
        ),
        stall_floor_min_mfe=_as_float(
            _config_raw("OPTION_PATH_V2_STALL_FLOOR_MIN_MFE", 0.15), 0.15
        ),
        stall_floor_max_mfe=_as_float(
            _config_raw("OPTION_PATH_V2_STALL_FLOOR_MAX_MFE", 0.40), 0.40
        ),
        stall_floor_min_age_seconds=_as_float(
            _config_raw("OPTION_PATH_V2_STALL_FLOOR_MIN_AGE_SECONDS", 60), 60.0
        ),
        stall_floor_max_age_seconds=_as_float(
            _config_raw("OPTION_PATH_V2_STALL_FLOOR_MAX_AGE_SECONDS", 3600), 3600.0
        ),
        stall_floor_min_sec_hwm=_as_float(
            _config_raw("OPTION_PATH_V2_STALL_FLOOR_MIN_SEC_HWM", 1500), 1500.0
        ),
        stall_floor_floor=_as_float(
            _config_raw("OPTION_PATH_V2_STALL_FLOOR_FLOOR", 0.04), 0.04
        ),
        stall_floor_max_slip=_as_float(
            _config_raw("OPTION_PATH_V2_STALL_FLOOR_MAX_SLIP", 0.03), 0.03
        ),
        stall_floor_arm_above_seconds=_as_float(
            _config_raw("OPTION_PATH_V2_STALL_FLOOR_ARM_ABOVE_SECONDS", 30), 30.0
        ),
        stall_floor_confirm_ticks=int(
            _as_float(_config_raw("OPTION_PATH_V2_STALL_FLOOR_CONFIRM_TICKS", 2), 2.0)
        ),
        runner_extension_enabled=_as_bool(
            _config_raw("OPTION_PATH_V2_RUNNER_EXTENSION_ENABLED", False), False
        ),
        runner_extension_target_mult=_as_float(
            _config_raw("OPTION_PATH_V2_RUNNER_EXTENSION_TARGET_MULT", 1.25), 1.25
        ),
        runner_extension_ul_retrace=_as_float(
            _config_raw("OPTION_PATH_V2_RUNNER_EXTENSION_UL_RETRACE", 0.04), 0.04
        ),
        runner_extension_ul_min_mult=_as_float(
            _config_raw("OPTION_PATH_V2_RUNNER_EXTENSION_UL_MIN_MULT", 1.045), 1.045
        ),
        runner_extension_floor_pret=_as_float(
            _config_raw("OPTION_PATH_V2_RUNNER_EXTENSION_FLOOR_PRET", 0.50), 0.50
        ),
        runner_extension_min_ticks=int(
            _as_float(_config_raw("OPTION_PATH_V2_RUNNER_EXTENSION_MIN_TICKS", 2), 2.0)
        ),
        runner_extension_jump_guard=_as_float(
            _config_raw("OPTION_PATH_V2_RUNNER_EXTENSION_JUMP_GUARD", 0.5), 0.5
        ),
        guarded_retrace_enabled=_as_bool(
            _config_raw("OPTION_PATH_V2_GBUL_ENABLED", False), False
        ),
        guarded_retrace_min_mfe=_as_float(
            _config_raw("OPTION_PATH_V2_GBUL_MIN_MFE", 0.30), 0.30
        ),
        guarded_retrace_max_mfe=_as_float(
            _config_raw("OPTION_PATH_V2_GBUL_MAX_MFE", 2.00), 2.00
        ),
        guarded_retrace_min_ul_dd=_as_float(
            _config_raw("OPTION_PATH_V2_GBUL_MIN_UL_DD", 0.005), 0.005
        ),
        guarded_retrace_min_peak_ratio=_as_float(
            _config_raw("OPTION_PATH_V2_GBUL_MIN_PEAK_RATIO", 0.65), 0.65
        ),
        guarded_retrace_confirm_ticks=int(
            _as_float(_config_raw("OPTION_PATH_V2_GBUL_CONFIRM_TICKS", 2), 2.0)
        ),
        ul_giveback_enabled=_as_bool(
            _config_raw("OPTION_PATH_V2_UFR_ENABLED", False), False
        ),
        ul_giveback_min_mfe=_as_float(
            _config_raw("OPTION_PATH_V2_UFR_MIN_MFE", 0.30), 0.30
        ),
        ul_giveback_max_mfe=_as_float(
            _config_raw("OPTION_PATH_V2_UFR_MAX_MFE", 2.00), 2.00
        ),
        ul_giveback_min_ul_mfe=_as_float(
            _config_raw("OPTION_PATH_V2_UFR_MIN_UL_MFE", 0.005), 0.005
        ),
        ul_giveback_min_ul_retrace=_as_float(
            _config_raw("OPTION_PATH_V2_UFR_MIN_UL_RETRACE", 0.30), 0.30
        ),
        ul_giveback_min_peak_ratio=_as_float(
            _config_raw("OPTION_PATH_V2_UFR_MIN_PEAK_RATIO", 0.70), 0.70
        ),
        ul_giveback_min_pret=_as_float(
            _config_raw("OPTION_PATH_V2_UFR_MIN_PRET", 0.03), 0.03
        ),
        ul_giveback_confirm_ticks=int(
            _as_float(_config_raw("OPTION_PATH_V2_UFR_CONFIRM_TICKS", 2), 2.0)
        ),
        atr_loss_cut_enabled=_as_bool(_config_raw("OPTION_PATH_V2_ATR_LOSS_CUT_ENABLED", False), False),
        atr_loss_cut_max_pret=_as_float(_config_raw("OPTION_PATH_V2_ATR_LOSS_CUT_MAX_PRET", -0.30), -0.30),
        atr_loss_cut_atr_mult=_as_float(_config_raw("OPTION_PATH_V2_ATR_LOSS_CUT_ATR_MULT", 2.0), 2.0),
        atr_loss_cut_max_peak=_as_float(_config_raw("OPTION_PATH_V2_ATR_LOSS_CUT_MAX_PEAK", 0.10), 0.10),
        atr_loss_cut_min_age_s=_as_float(_config_raw("OPTION_PATH_V2_ATR_LOSS_CUT_MIN_AGE_S", 600), 600.0),
        atr_loss_cut_require_vwap_against=_as_bool(
            _config_raw("OPTION_PATH_V2_ATR_LOSS_CUT_REQUIRE_VWAP_AGAINST", True), True
        ),
        atr_loss_cut_bar_max_age_s=_as_float(_config_raw("OPTION_PATH_V2_ATR_LOSS_CUT_BAR_MAX_AGE_S", 180), 180.0),
        vwap_cross_exit_enabled=_as_bool(_config_raw("OPTION_PATH_V2_VWAP_CROSS_EXIT_ENABLED", False), False),
        vwap_cross_min_peak=_as_float(_config_raw("OPTION_PATH_V2_VWAP_CROSS_MIN_PEAK", 0.50), 0.50),
        vwap_cross_atr_buffer=_as_float(_config_raw("OPTION_PATH_V2_VWAP_CROSS_ATR_BUFFER", 0.25), 0.25),
        vwap_cross_confirm_bars=int(_as_float(_config_raw("OPTION_PATH_V2_VWAP_CROSS_CONFIRM_BARS", 1), 1.0)),
        vwap_cross_min_pret=_as_float(_config_raw("OPTION_PATH_V2_VWAP_CROSS_MIN_PRET", 0.10), 0.10),
        vwap_cross_bar_max_age_s=_as_float(_config_raw("OPTION_PATH_V2_VWAP_CROSS_BAR_MAX_AGE_S", 180), 180.0),
    )


def path_v2_architecture_active() -> bool:
    """True only when both gates request the PATH_V2 replace path."""
    arch = str(_config_raw("OPTION_PC_ARCHITECTURE", "current") or "current").strip().lower()
    enabled = _as_bool(_config_raw("OPTION_PATH_V2_ENABLED", False), False)
    return arch == "path_v2" and enabled


# ---------------------------------------------------------------------------
# Utilities: pure formulas and parsing used by the STATE block. None of them
# makes an exit decision.
# ---------------------------------------------------------------------------


def _as_utc(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _finite(value: Any) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(out):
        return None
    return out


def _positive(value: Any) -> Optional[float]:
    out = _finite(value)
    if out is None or out <= 0.0:
        return None
    return out


def live_path_v2_pret_from_tick(
    *,
    option_bid: Any,
    entry_premium: Any,
    osnap_option_bid: Any = None,
    current_pnl_pct: Any = None,
    mark: Any = None,
) -> Optional[float]:
    """Live PATH_V2 pret: this-tick executable bid / entry − 1.

    Missing, non-finite, or ≤0 bid → None (STEP 0 HOLD, peak unchanged).
    ``current_pnl_pct``, liquidation/chain ``mark``, and a prior-tick
    ``osnap_option_bid`` are accepted only so callers cannot accidentally
    substitute them; they are never used.
    """
    del osnap_option_bid, current_pnl_pct, mark
    entry = _positive(entry_premium)
    bid = _positive(option_bid)
    if entry is None or bid is None:
        return None
    return bid / entry - 1.0


EXIT_BID_MISMATCH_FRAC = 0.25
_EXIT_BID_EQUAL_ABS = 0.005
_EXIT_BID_EQUAL_REL = 0.001


def _premiums_equal(left: float, right: float) -> bool:
    return abs(left - right) <= max(_EXIT_BID_EQUAL_ABS, _EXIT_BID_EQUAL_REL * max(abs(left), abs(right)))


def pacific_clock_hour(now: Any) -> Optional[float]:
    """PT hour as a float (12:30 PT = 12.5). Uses America/Los_Angeles, not UTC−7."""
    dt = _as_utc(now)
    if dt is None:
        return None
    local_pt = dt.astimezone(_PACIFIC_TZ)
    return (
        float(local_pt.hour)
        + float(local_pt.minute) / 60.0
        + float(local_pt.second) / 3600.0
        + float(local_pt.microsecond) / 3.6e9
    )


def resolve_frozen_contract_exit_bid(
    *,
    claimed_exit_bid: Any,
    last_path_bid: Any,
    frozen_symbol_bid: Any = None,
    last_live_premium: Any = None,
    peak_premium: Any = None,
    claimed_stale: bool = False,
) -> Tuple[Optional[float], bool]:
    """Recorded exit bid is the frozen entry_option_symbol quote bid.

    Recording-only: trading fills are unchanged. Override the claimed print
    only when it equals last_live_premium / peak (HWM-as-exit) or is stale.
    A >25% mismatch alone is not enough. Always logs claimed and bid.
    Returns (recorded_bid, overridden).
    """
    path_b = _positive(last_path_bid)
    frozen_b = _positive(frozen_symbol_bid)
    claimed = _positive(claimed_exit_bid)
    live = _positive(last_live_premium)
    peak = _positive(peak_premium)
    true_bid = frozen_b or path_b
    chosen = claimed or true_bid
    log.info(
        "PATH_V2_EXIT_BID | claimed=%s | bid=%s | frozen_symbol_bid=%s | last_path_bid=%s",
        None if claimed is None else round(claimed, 4),
        None if true_bid is None else round(true_bid, 4),
        None if frozen_b is None else round(frozen_b, 4),
        None if path_b is None else round(path_b, 4),
    )
    matches_peak = False
    if claimed is not None:
        if live is not None and _premiums_equal(claimed, live):
            matches_peak = True
        if peak is not None and _premiums_equal(claimed, peak):
            matches_peak = True
        if matches_peak and true_bid is not None and _premiums_equal(claimed, true_bid):
            matches_peak = False
    overridden = bool(
        true_bid is not None
        and (bool(claimed_stale) or matches_peak)
        and (claimed is None or not _premiums_equal(claimed, true_bid))
    )
    if overridden:
        chosen = true_bid
        log.warning(
            "PATH_V2_EXIT_BID_OVERRIDE | claimed=%.4f | bid=%.4f | reason=%s",
            float(claimed or 0.0),
            true_bid,
            "stale" if claimed_stale else "claimed_equals_peak_or_last_live",
        )
    if (
        claimed is not None
        and true_bid is not None
        and true_bid > 0
        and abs(claimed / true_bid - 1.0) > EXIT_BID_MISMATCH_FRAC
        and not overridden
    ):
        log.warning(
            "PATH_V2_EXIT_BID_MISMATCH | claimed=%.4f | bid=%.4f | frozen_symbol_bid=%s | "
            "frac=%.3f | override=false",
            claimed,
            true_bid,
            frozen_b,
            claimed / true_bid - 1.0,
        )
    return chosen, overridden


def _side(option_side: str) -> Optional[str]:
    text = str(option_side or "").strip().lower()
    if "put" in text:
        return "put"
    if "call" in text:
        return "call"
    return None


def _clip(value: float, lo: float, hi: float) -> float:
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def _state(osnap: Dict[str, Any]) -> Dict[str, Any]:
    raw = osnap.get(_STATE_KEY)
    if not isinstance(raw, dict):
        raw = {}
        osnap[_STATE_KEY] = raw
    return raw


_SAVE20_STATE_KEY = "save20_stall"


def _save20_stall_step(
    cfg: "PathV2Config",
    state: Dict[str, Any],
    *,
    side: Optional[str],
    pret_f: float,
    now_dt: datetime,
    t20_base: datetime,
) -> Tuple[bool, Dict[str, Any]]:
    """STEP 2 20% Save: update the stall latch and return (fire, latch).

    Latch at the first tick pret >= MIN_MFE: t0, t20 (seconds open->touch), peak,
    hwm_ts, n (ticks after touch), nh (new-high ticks after touch).
    """
    tsn = now_dt.timestamp()
    st = state.get(_SAVE20_STATE_KEY)
    if not isinstance(st, dict):
        if pret_f < float(cfg.twenty_percent_save_min_mfe) - 1e-9:
            return False, {}
        st = {
            "t0": tsn,
            "t20": tsn - t20_base.timestamp(),
            "peak": pret_f,
            "hwm_ts": tsn,
            "n": 0,
            "nh": 0,
        }
        state[_SAVE20_STATE_KEY] = st
    else:
        st["n"] = int(st.get("n") or 0) + 1
        if pret_f > float(st["peak"]) + 1e-12:
            st["peak"] = pret_f
            st["hwm_ts"] = tsn
            st["nh"] = int(st.get("nh") or 0) + 1
    sides = str(cfg.twenty_percent_save_sides or "call").strip().lower()
    if not (sides == "both" or side == sides):
        return False, st
    nhf = st["nh"] / st["n"] if st["n"] else 1.0
    st["nh_frac"] = nhf
    fire = (
        st["t20"] >= float(cfg.twenty_percent_save_min_t20_s) - 1e-9
        and st["peak"] < float(cfg.twenty_percent_save_max_peak) - 1e-12
        and tsn - st["t0"] >= float(cfg.twenty_percent_save_min_since_touch_s) - 1e-9
        and tsn - st["hwm_ts"] >= float(cfg.twenty_percent_save_min_sec_hwm) - 1e-9
        and st["peak"] - pret_f >= float(cfg.twenty_percent_save_giveback) - 1e-9
        and pret_f >= float(cfg.twenty_percent_save_min_pret) - 1e-9
        and nhf <= float(cfg.twenty_percent_save_max_newhigh_frac) + 1e-12
    )
    if fire:
        st["fired_at"] = now_dt.isoformat()
    return bool(fire), st


_SAVE20_ULR_KEY = "save20_ulr"
DETAIL_TWENTY_PERCENT_SAVE_ULR = "P20_UL_RETRACE_SAVE"


def _save20_ulr_step(
    cfg: "PathV2Config",
    state: Dict[str, Any],
    osnap: Dict[str, Any],
    *,
    side: Optional[str],
    pret_f: Optional[float],
    now_dt: datetime,
    entry_underlying: Any,
    current_underlying: Any,
    ul_quote_ts: Any,
) -> bool:
    """STEP 2 extension: exit when the fresh UL gives back >= FRAC of its
    entry->touch move, armed at the first pret >= MIN_MFE. Fresh UL = a tick
    whose ul_quote_ts changed."""
    st = state.setdefault(_SAVE20_ULR_KEY, {})
    # UL <= 0 or missing is not a tick: no refresh, no arm, no fire.
    cu = _positive(current_underlying)
    qk = str(ul_quote_ts) if ul_quote_ts is not None else None
    if cu is not None and qk is not None and qk != st.get("q"):
        st["q"] = qk
        st["ul"] = cu
    sgn = 1.0 if side == "call" else -1.0 if side == "put" else 0.0
    if "t0" not in st:
        if pret_f is None or pret_f < float(cfg.twenty_percent_save_min_mfe) - 1e-9:
            return False
        if cu is None:
            return False
        st["t0"] = now_dt.timestamp()
        st["ul0"] = st.get("ul")
        eul = _positive(entry_underlying)
        ul0 = _positive(st.get("ul0"))
        dv = _finite(osnap.get("entry_distance_from_vwap_atr"))
        mv = sgn * (ul0 - eul) if (ul0 is not None and eul is not None) else None
        st["armed"] = bool(
            sgn != 0.0
            and ul0 is not None
            and dv is not None
            and abs(dv) < float(cfg.twenty_percent_save_ulr_max_entry_vwap_atr) - 1e-12
            and mv is not None
            and mv > 0
        )
        st["mv"] = mv
        return False
    if not st.get("armed"):
        return False
    if cu is None:
        return False
    ul = _positive(st.get("ul"))
    if ul is None:
        return False
    back = sgn * (float(st["ul0"]) - ul)
    fire = back >= float(cfg.twenty_percent_save_ulr_frac) * float(st["mv"]) - 1e-12
    if fire:
        st["fired_at"] = now_dt.isoformat()
    return bool(fire)


def _quote_age_seconds(
    now: datetime,
    *,
    ul_quote_ts: Any,
    ul_age_ms: Any,
    ul_source: Any,
) -> Optional[float]:
    source = str(ul_source or "").strip().lower()
    if source == "unavailable":
        return None
    age_ms = _finite(ul_age_ms)
    if age_ms is not None:
        return max(0.0, age_ms / 1000.0)
    ts = _as_utc(ul_quote_ts)
    if ts is None:
        return None
    return max(0.0, (now - ts).total_seconds())


def _fav_ul(side: str, entry_underlying: float, current_underlying: float) -> float:
    """Research-loader favorable return. Not a side branch."""
    if side == "put":
        return (entry_underlying - current_underlying) / entry_underlying
    return (current_underlying - entry_underlying) / entry_underlying


def live_stress_score(live: Dict[str, Any]) -> float:
    """Exact immature stress scalar. Higher means more exit-worthy."""
    score = 0.0
    n = 0
    v10 = _finite(live.get("prem_vel_10"))
    if v10 is not None:
        score += _clip((-v10 - 0.0005) / 0.002, -1.0, 1.0)
        n += 1
    fr = live.get("frac_retrace")
    if fr is not None:
        fr_f = _finite(fr)
        if fr_f is not None:
            score += _clip((fr_f - 0.3) / 0.4, -1.0, 1.0)
            n += 1
    ur = _finite(live.get("fav_ul_retr"))
    if ur is not None:
        score += _clip((ur - 0.5) / 0.5, -1.0, 1.0)
        n += 1
    udd = _finite(live.get("fav_ul_dd"))
    if udd is not None:
        score += _clip((udd - 0.002) / 0.005, -1.0, 1.0)
        n += 1
    cr = _finite(live.get("current_return"))
    pk = _finite(live.get("prem_mfe"))
    if cr is not None and pk is not None and pk > _EPS:
        score += _clip((-cr) / 0.15, -1.0, 1.0) * 0.5
        n += 1
    age = _finite(live.get("trade_age_sec"))
    if age is not None:
        score += _clip((age - 180.0) / 300.0, -1.0, 1.0) * 0.3
        n += 1
    if n == 0:
        return 0.0
    return float(score / n)


def _samples(state: Dict[str, Any]) -> List[List[float]]:
    raw = state.get("prem_samples")
    if not isinstance(raw, list):
        return []
    out: List[List[float]] = []
    for row in raw:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        sse = _finite(row[0])
        pret = _finite(row[1])
        if sse is None or pret is None:
            continue
        out.append([sse, pret])
    return out


def _trim_samples(
    samples: Sequence[Sequence[float]],
    age: float,
    window: float,
) -> List[List[float]]:
    """Keep points inside the velocity window. Older points are dropped."""
    t0 = float(age) - float(window)
    rows = [[float(sse), float(pret)] for sse, pret in samples]
    return [row for row in rows if row[0] >= t0 - _EPS]


def _prem_vel(samples: Sequence[Sequence[float]], age: float, pret: float, window: float) -> float:
    measured = _prem_vel_measured(samples, age, pret, window)
    return 0.0 if measured is None else measured


def _prem_vel_measured(
    samples: Sequence[Sequence[float]], age: float, pret: float, window: float
) -> Optional[float]:
    """Premium velocity vs the oldest in-window sample.

    Default (live): None when this tick is the only point inside the window
    (a >10s gap). When S3 kept a pre-window sparse anchor, that earlier point
    is still in ``samples[0]`` and velocity is measured over dt that may exceed
    the window.
    """
    if not samples:
        return None
    t0 = float(age) - float(window)
    j = len(samples) - 1
    while j > 0 and float(samples[j - 1][0]) >= t0 - _EPS:
        j -= 1
    dt = float(age) - float(samples[j][0])
    if dt <= 1e-9 and j > 0:
        j -= 1
        dt = float(age) - float(samples[j][0])
    if dt <= 1e-9:
        return None
    return (float(pret) - float(samples[j][1])) / dt


def freeze_entry_valid_bid_ask_contracts(
    osnap: Dict[str, Any],
    *sources: Any,
) -> Optional[float]:
    """Keep the first entry-time chain breadth. A later source does not replace it.

    Missing breadth stays missing. Contract Fade then holds.
    """
    if not isinstance(osnap, dict):
        return None
    existing = _finite(osnap.get("valid_bid_ask_contracts"))
    if existing is not None:
        return existing
    nests = ("signal_snapshot", "execution_payload", "orb_signal", "metadata")
    for source in sources:
        if not isinstance(source, dict):
            continue
        found = _finite(source.get("valid_bid_ask_contracts"))
        if found is None:
            for nest_name in nests:
                nest = source.get(nest_name)
                if isinstance(nest, dict):
                    found = _finite(nest.get("valid_bid_ask_contracts"))
                elif isinstance(nest, list):
                    for item in nest:
                        if isinstance(item, dict):
                            found = _finite(item.get("valid_bid_ask_contracts"))
                            if found is not None:
                                break
                if found is not None:
                    break
        if found is not None:
            osnap["valid_bid_ask_contracts"] = found
            return found
    return None


def freeze_entry_distance_from_vwap_atr(
    osnap: Dict[str, Any],
    *sources: Any,
    symbol: Any = None,
) -> Optional[float]:
    """Keep the first entry-time VWAP distance (ATR units). A later source does not replace it.

    Missing distance stays missing. The UL-retrace save then never arms.
    """
    if not isinstance(osnap, dict):
        return None
    if symbol:
        osnap.setdefault("symbol", symbol)
    existing = _finite(osnap.get("entry_distance_from_vwap_atr"))
    if existing is not None:
        return existing
    existing_atr = _finite(osnap.get("entry_atr"))
    nests = ("signal_snapshot", "execution_payload", "orb_signal", "metadata", "path_session_tech")
    found_dv = None
    found_atr = existing_atr
    for source in sources:
        if not isinstance(source, dict):
            continue
        bags = [source]
        for nest_name in nests:
            nest = source.get(nest_name)
            if isinstance(nest, dict):
                bags.append(nest)
        for bag in bags:
            if found_dv is None:
                found_dv = _finite(bag.get("entry_distance_from_vwap_atr"))
                if found_dv is None:
                    found_dv = _finite(bag.get("distance_from_vwap_atr"))
            if found_atr is None:
                found_atr = _finite(bag.get("entry_atr"))
                if found_atr is None:
                    found_atr = _finite(bag.get("atr"))
        if found_dv is not None and found_atr is not None:
            break
    if found_atr is not None and existing_atr is None:
        osnap["entry_atr"] = found_atr
    if found_dv is not None:
        osnap["entry_distance_from_vwap_atr"] = found_dv
        return found_dv
    return None


def _quote_samples(state: Dict[str, Any]) -> List[List[float]]:
    raw = state.get("contract_fade_quote_samples")
    if not isinstance(raw, list):
        return []
    out: List[List[float]] = []
    for row in raw:
        if not isinstance(row, (list, tuple)) or len(row) < 3:
            continue
        age = _finite(row[0])
        bid = _finite(row[1])
        ask = _finite(row[2])
        if age is None or bid is None or ask is None:
            continue
        out.append([age, bid, ask])
    return out


def _trim_quote_samples(
    samples: Sequence[Sequence[float]], age: float, window: float
) -> List[List[float]]:
    # Keep a little history before the comparison window so nearest-past lookup
    # can still find the quote at or before age-window on sparse tapes.
    floor = float(age) - max(float(window) * 2.0, float(window) + 10.0)
    return [
        [float(sse), float(bid), float(ask)]
        for sse, bid, ask in samples
        if float(sse) >= floor - _EPS
    ]


# AE warning bookkeeping (state only).
def _clear_ae_warning(state: Dict[str, Any]) -> None:
    for key in (
        "ae_warning_active",
        "ae_warning_start_sse",
        "ae_warning_start_peak",
        "ae_warning_last_detail",
    ):
        state.pop(key, None)


# ---------------------------------------------------------------------------
# The decision: one flat function, STATE block then STEP 0-28 in order.
# ---------------------------------------------------------------------------

def trade_past_120s_checkpoint(age_seconds: float, cfg: PathV2Config) -> bool:
    """Shared 120s checkpoint: True once the trade is at least min_hold_seconds old.

    Same value as the STEP 16 minimum hold (OPTION_PATH_V2_MIN_HOLD_SECONDS, 120).
    Used by STEP 29-30 (Oct 27); the older steps keep their own checks for now.
    """
    return float(age_seconds) >= float(cfg.min_hold_seconds) - _EPS


def _fresh_session_tech_bar(
    osnap: Dict[str, Any], now_dt: datetime, max_age_s: float
) -> Optional[datetime]:
    """Bar time of the live session-tech overlay when it is <= max_age_s old, else None.

    A missing or future bar time is not fresh (STEP 29-30 never fire on it).
    """
    bar_at = _as_utc(osnap.get("session_tech_bar_at")) if isinstance(osnap, dict) else None
    if bar_at is None:
        return None
    bar_age = (now_dt - bar_at).total_seconds()
    if bar_age < 0 or bar_age > float(max_age_s) + _EPS:
        return None
    return bar_at


def evaluate_path_v2_tick(
    *,
    osnap: Dict[str, Any],
    option_side: str,
    entry_underlying: Any,
    current_underlying: Any,
    pret: Any,
    opened_at: Any,
    now: Any,
    ul_quote_ts: Any = None,
    ul_age_ms: Any = None,
    ul_source: Any = None,
    mfe_trail_active: bool = False,
    mfe_trail_trigger_pct: Optional[float] = None,
    mfe_trail_exit_pct: Optional[float] = None,
    theta: Any = None,
    option_bid: Any = None,
    option_ask: Any = None,
    valid_bid_ask_contracts: Any = None,
    cfg: Optional[PathV2Config] = None,
) -> PathV2Decision:
    """One monitor tick. Mutates osnap['path_v2']. Does not send an order.

    Read top to bottom: the STATE block runs on every tick, then each STEP is
    checked in evaluation order and the first exit returns. A HOLD step does
    not return; it only skips the steps of its own region, and the last
    checks (STEP 26-28) still run on that tick. STEP 1 (R2X hold mode) is the
    exception: while a runner is held it returns on every tick and owns the exit.
    """
    # =====================================================================
    # STATE S0 | Tick inputs | Exit Reason: none (state)
    # Knobs: OPTION_PATH_V2_* (load_path_v2_config) unless a cfg is passed.
    # Rule: age = seconds since open; pret = bid / entry - 1; mfe_trail
    #       trigger / giveback come from the caller (afternoon trail). The
    #       bounded MFE capture computes its own Pacific session clock.
    #       runner_ext_ticks counts every call on this position, including a
    #       tick with no mark (R2X gap guard, STEP 7).
    # =====================================================================
    cfg = cfg or load_path_v2_config()
    now_dt = _as_utc(now) or datetime.now(timezone.utc)
    opened = _as_utc(opened_at) or now_dt
    age = max(0.0, (now_dt - opened).total_seconds())
    side = _side(option_side)
    state = _state(osnap)
    state["runner_ext_ticks"] = int(state.get("runner_ext_ticks") or 0) + 1
    pret_f = _finite(pret)
    trigger = (
        float(mfe_trail_trigger_pct)
        if mfe_trail_trigger_pct is not None
        else float(cfg.mfe_trail_trigger_pct)
    )
    trail_exit = (
        float(mfe_trail_exit_pct)
        if mfe_trail_exit_pct is not None
        else float(cfg.mfe_trail_exit_pct)
    )
    local_pt_hour = pacific_clock_hour(now_dt) or 0.0
    try:
        from modules.session_clock import orb2_tl_end_hour

        _orb2_end_hour = float(orb2_tl_end_hour())
    except Exception:
        _orb2_end_hour = 12.5
    bounded_mfe_capture_active = (
        local_pt_hour >= float(cfg.bounded_mfe_capture_start_pt_hour) - _EPS
        # Same clock as STEP 5 / DISCOVERY_ORB2_TL_END_PT (default 12:30).
        and local_pt_hour < _orb2_end_hour - _EPS
        and not bool(mfe_trail_active)
    )

    # =====================================================================
    # STATE S1 | Favorable underlying (UL) | Exit Reason: none (state)
    # Knobs: OPTION_PATH_V2_STALE_MAX_SECONDS (10)
    # Rule: favorable UL return, its running max, drawdown and retrace. A UL
    #       quote older than 10s, or source=unavailable, leaves all three as
    #       None. Age is the monitor's broker/receive clock (underlying_age_ms),
    #       never the tick timestamp.
    # =====================================================================
    entry = _positive(entry_underlying)
    if entry is not None and _positive(state.get("ul_entry")) is None:
        state["ul_entry"] = entry
    elif entry is None:
        entry = _positive(state.get("ul_entry"))
    quote_age = _quote_age_seconds(
        now_dt, ul_quote_ts=ul_quote_ts, ul_age_ms=ul_age_ms, ul_source=ul_source
    )
    price = _positive(current_underlying)
    ul_fresh = (
        side is not None
        and entry is not None
        and price is not None
        and quote_age is not None
        and quote_age <= float(cfg.stale_max_seconds)
    )
    ul_dir: Optional[float] = None
    ul_dd: Optional[float] = None
    ul_retr: Optional[float] = None
    if ul_fresh and side is not None and entry is not None and price is not None:
        ul_dir = _fav_ul(side, entry, price)
        prev_mfe = _finite(state.get("ul_mfe"))
        ul_mfe = ul_dir if prev_mfe is None else max(prev_mfe, ul_dir)
        ul_dd = max(0.0, ul_mfe - ul_dir)
        ul_retr = (ul_dd / ul_mfe) if ul_mfe > _EPS else 0.0
        state["ul_mfe"] = ul_mfe
        state["ul_dir"] = ul_dir
        state["ul_dd"] = ul_dd
        state["ul_retr"] = ul_retr

    # =====================================================================
    # STATE S2 | UL retrace tracker (first-UL anchor) for STEP 9 | Exit Reason: none (state)
    # Knobs: OPTION_PATH_V2_GBUL_ENABLED, OPTION_PATH_V2_UFR_ENABLED, OPTION_PATH_V2_STALE_MAX_SECONDS (10)
    # Rule: the study-faithful UL path, deliberately NOT the S1 definition. Anchor = the first valid
    #       UL price seen on this position (S1 anchors on entry_underlying; the first monitored tick
    #       is usually a few seconds after entry, so the two differ). ulr_umax = running max of the
    #       favorable move off that anchor (starts at 0). ulr_dd = (extreme - fav) / |extreme| on the
    #       favorable price (call: price, put: -price), i.e. retrace relative to the extreme price,
    #       not to the anchor. ulr_retr = (ulr_umax - move) / ulr_umax once ulr_umax > 0.05%.
    #       Hold (all three None, nothing stored): UL missing, unavailable, or older than 10s; the
    #       tracker was not armed on the first call for this position (restart, re-enroll or a snapshot
    #       that predates this state); or the anchor / max / extreme is missing or invalid. Never
    #       re-anchor mid-trade. Reads only the tick inputs already passed in (no hub or chain read).
    # =====================================================================
    ulr_on = bool(cfg.guarded_retrace_enabled) or bool(cfg.ul_giveback_enabled)
    if ulr_on and "ulr_armed" not in state:
        state["ulr_armed"] = int(state.get("runner_ext_ticks") or 0) <= 1
    ulr_fresh = (
        ulr_on
        and state.get("ulr_armed") is True
        and side is not None
        and price is not None
        and quote_age is not None
        and quote_age <= float(cfg.stale_max_seconds)
    )
    ulr_sg = 1.0 if side == "call" else -1.0
    ulr_fav = (ulr_sg * price) if ulr_fresh and price is not None else None
    if ulr_fresh and state.get("ulr_u0") is None:
        state["ulr_u0"] = price
        state["ulr_umax"] = 0.0
        state["ulr_ext"] = ulr_fav
    ulr_u0 = _positive(state.get("ulr_u0")) if ulr_fresh else None
    ulr_prev_max = _finite(state.get("ulr_umax")) if ulr_fresh else None
    ulr_prev_ext = _finite(state.get("ulr_ext")) if ulr_fresh else None
    ulr_dd: Optional[float] = None
    ulr_retr: Optional[float] = None
    ulr_umax: Optional[float] = None
    if (
        ulr_u0 is not None
        and ulr_prev_max is not None
        and ulr_prev_ext is not None
        and ulr_fav is not None
        and price is not None
    ):
        ulr_move = ulr_sg * (price - ulr_u0) / ulr_u0
        ulr_umax = max(ulr_prev_max, ulr_move)
        ulr_ext = max(ulr_prev_ext, ulr_fav)
        state["ulr_umax"] = ulr_umax
        state["ulr_ext"] = ulr_ext
        ulr_dd = (ulr_ext - ulr_fav) / abs(ulr_ext) if abs(ulr_ext) > _EPS else None
        ulr_retr = (ulr_umax - ulr_move) / ulr_umax if ulr_umax > _ULR_MIN_UMAX else None
    if ulr_on:
        state["ulr_dd"] = ulr_dd
        state["ulr_retr"] = ulr_retr

    # =====================================================================
    # STATE S3 | Premium peak, material high, velocity samples | Exit Reason: none (state)
    # Knobs: OPTION_PATH_V2_MATERIAL_HWM_PP (0.05), OPTION_PATH_V2_RTC_VEL_WINDOW (10)
    # Rule: a new bid high moves peak and the high clock (abs_hwm_sse); a new
    #       high at least +5pp above the old one also resets the material
    #       clock and the hwm_rtc velocity timer. Keep the last 10s of pret.
    #       runner_ext_prev_pret = the previous tick's pret (R2X jump guard).
    # =====================================================================
    if pret_f is not None:
        prev_peak = _finite(state.get("peak"))
        if prev_peak is None or pret_f > prev_peak + _EPS:
            is_material = prev_peak is None or pret_f >= prev_peak + float(cfg.material_hwm_pp) - _EPS
            if is_material:
                state["material_sse"] = age
                state["vel_bad_sse"] = None
            state["peak"] = pret_f
            state["abs_hwm_sse"] = age
        samples = _samples(state)
        state["runner_ext_prev_pret"] = samples[-1][1] if samples else None
        samples.append([age, pret_f])
        samples = _trim_samples(
            samples,
            age,
            cfg.rtc_vel_window_seconds,
        )
        state["prem_samples"] = samples
    else:
        samples = _samples(state)

    # =====================================================================
    # STATE S4 | Contract Fade quote memory | Exit Reason: none (state)
    # Knobs: OPTION_PATH_V2_CONTRACT_FADE_QUOTE_WINDOW_SECONDS (30)
    # Rule: with a valid bid and ask, compare to the nearest quote at least
    #       30s earlier (bid/ask change), then store this quote.
    # =====================================================================
    cf_window = float(cfg.contract_fade_quote_window_seconds)
    cf_bid_f = _finite(option_bid)
    cf_ask_f = _finite(option_ask)
    cf_samples = _quote_samples(state)
    cf_prior_bid = cf_prior_ask = cf_bid_change = cf_ask_change = None
    if cf_bid_f is not None and cf_ask_f is not None and cf_bid_f > 0.0 and cf_ask_f > 0.0:
        cf_ages = [float(row[0]) for row in cf_samples]
        k = len(cf_ages) - 1
        cf_target_age = float(age) - float(cf_window)
        while k >= 0 and cf_ages[k] > cf_target_age + _EPS:
            k -= 1
        if k >= 0:
            cf_prior_bid = float(cf_samples[k][1])
            cf_prior_ask = float(cf_samples[k][2])
            cf_bid_change = cf_bid_f - cf_prior_bid
            cf_ask_change = cf_ask_f - cf_prior_ask
        cf_samples.append([float(age), cf_bid_f, cf_ask_f])
        cf_samples = _trim_quote_samples(cf_samples, age, cf_window)
    state["contract_fade_quote_samples"] = cf_samples

    # =====================================================================
    # STATE S5 | V3A shallow_floor_stop bookkeeping | Exit Reason: none (state)
    # Knobs: OPTION_PATH_V2_SHALLOW_FLOOR_STOP_ENABLED (true),
    #        OPTION_PATH_V2_SHALLOW_FLOOR_PRET (-0.18), _MIN_AGE_SECONDS (120)
    # Rule: count the ticks printed at the running peak bid (sf_peak_ticks);
    #       remember if the bid was above -18% at/after age 120s (fresh cross).
    # =====================================================================
    if bool(cfg.shallow_floor_stop_enabled) and pret_f is not None:
        if pret_f > float(cfg.shallow_floor_pret) + _EPS:
            if age >= float(cfg.shallow_floor_min_age_seconds) - _EPS:
                state["sf_seen_above_gate"] = True
        sf_pk_prev = _finite(state.get("sf_pk"))
        if sf_pk_prev is None or pret_f > sf_pk_prev + _EPS:
            state["sf_pk"] = pret_f
            state["sf_peak_ticks"] = 1
        elif abs(pret_f - sf_pk_prev) <= _EPS:
            state["sf_peak_ticks"] = int(state.get("sf_peak_ticks") or 0) + 1

    # =====================================================================
    # STATE S6 | Running MFE, proof flag, 350% runner arm | Exit Reason: none (state)
    # Knobs: OPTION_PATH_V2_PROOF (0.50), OPTION_PATH_V2_RUNNER_CAPTURE_ENABLED (true),
    #        OPTION_PATH_V2_RUNNER_CAPTURE_ARM_MFE (3.50)
    # Rule: running MFE = peak bid return (0 before the first pret); stamp the
    #       first tick the peak reaches +350% (runner arm).
    # =====================================================================
    peak = _finite(state.get("peak"))
    running_mfe = peak if peak is not None else 0.0
    if peak is not None and peak >= float(cfg.proof) - _EPS:
        state["proven"] = True
    if (
        bool(cfg.runner_capture_enabled)
        and peak is not None
        and peak >= float(cfg.runner_capture_arm_mfe) - _EPS
        and _finite(state.get("runner_capture_arm_sse")) is None
    ):
        state["runner_capture_arm_sse"] = age

    # =====================================================================
    # STATE S7 | Clocks: seconds since the high, runner arm age | Exit Reason: none (state)
    # =====================================================================
    abs_sse = _finite(state.get("abs_hwm_sse"))
    sec_since_hwm = (age - abs_sse) if abs_sse is not None else None
    runner_arm_sse = _finite(state.get("runner_capture_arm_sse"))
    runner_arm_age = (age - runner_arm_sse) if runner_arm_sse is not None else None

    # =====================================================================
    # STATE S8 | V3A runner timing (RT) | Exit Reason: none (state; used by STEP 7-8)
    # Knobs: OPTION_PATH_V2_RUNNER_TIMING_V3A_ENABLED (true),
    #        _TIMED_MIN_ARM_AGE_SECONDS (30), _FLOOR_GREEN_MIN_AGE_SECONDS (900),
    #        _FLOOR_GREEN_MIN_PRET (0.50); base OPTION_PATH_V2_RUNNER_CAPTURE_
    #        TIMED_MIN_ARM_AGE_SECONDS (60) / FLOOR_MIN_AGE_SECONDS (600)
    # Rule: timed capture waits 30s after the arm (base 60s). The profit floor
    #       waits 900s only while bid >= +50%, else the base 600s.
    # =====================================================================
    rt_timed_age = float(cfg.runner_capture_timed_min_arm_age_seconds)
    rt_floor_age = float(cfg.runner_capture_floor_min_age_seconds)
    if bool(cfg.runner_timing_v3a_enabled):
        rt_timed_age = float(cfg.runner_timing_v3a_timed_min_arm_age_seconds)
        if pret_f is not None and pret_f >= float(cfg.runner_timing_v3a_floor_green_min_pret) - _EPS:
            rt_floor_age = float(cfg.runner_timing_v3a_floor_green_min_age_seconds)

    # =====================================================================
    # STATE S9 | V3A stall_floor_stop bookkeeping | Exit Reason: none (state; used by STEP 28)
    # Knobs: OPTION_PATH_V2_STALL_FLOOR_STOP_ENABLED (true), _MIN_MFE (0.15),
    #        _MAX_MFE (0.40), _MIN_AGE_SECONDS (60), _MAX_AGE_SECONDS (3600),
    #        _MIN_SEC_HWM (1500), _FLOOR (0.04), _ARM_ABOVE_SECONDS (30)
    # Rule: eligible = 15-40% peak, age 60-3600s, high >= 1500s old. Count
    #       consecutive ticks with bid <= +4%; arm once the bid has held above
    #       +4% for 30s while eligible. Runs on every tick, exit or not.
    # =====================================================================
    sbf_armed = False
    sbf_floor = float(cfg.stall_floor_floor)
    if bool(cfg.stall_floor_stop_enabled) and pret_f is not None:
        sbf_ssh = sec_since_hwm if sec_since_hwm is not None else 0.0
        sbf_armed = (
            running_mfe >= float(cfg.stall_floor_min_mfe) - _EPS
            and running_mfe < float(cfg.stall_floor_max_mfe) - _EPS
            and age >= float(cfg.stall_floor_min_age_seconds) - _EPS
            and sbf_ssh >= float(cfg.stall_floor_min_sec_hwm) - _EPS
            and age <= float(cfg.stall_floor_max_age_seconds) + _EPS
        )
        if pret_f <= sbf_floor + _EPS:
            state["sbf_below_n"] = int(state.get("sbf_below_n") or 0) + 1
            state["sbf_above_since"] = None
        else:
            state["sbf_below_n"] = 0
            if _finite(state.get("sbf_above_since")) is None:
                state["sbf_above_since"] = age
            if sbf_armed and (age - float(state["sbf_above_since"])) >= float(
                cfg.stall_floor_arm_above_seconds
            ) - _EPS:
                state["sbf_seen_above_armed"] = True

    # Per-tick values filled by the 120s-hold steps (telemetry shows None
    # when a step is not reached on this tick).
    stress: Optional[float] = None
    vel: Optional[float] = None
    frac: Optional[float] = None
    hold_detail = ""

    # =====================================================================
    # OUTPUT | Decision + telemetry | builds the PathV2Decision for any return
    # =====================================================================
    def _decide(exit_reason: Optional[str], detail: str) -> PathV2Decision:
        material_sse = _finite(state.get("material_sse"))
        sec_since_material = (age - material_sse) if material_sse is not None else None
        telemetry = {
            "pc_architecture": "path_v2",
            "side": side,
            "pret": pret_f,
            "age_seconds": age,
            "running_mfe": running_mfe,
            "frac_retrace": frac,
            "prem_vel_10": vel,
            "sec_since_material_hwm": sec_since_material,
            "sec_since_hwm": sec_since_hwm,
            "live_stress": stress,
            "ul_dir": ul_dir,
            "ul_mfe": _finite(state.get("ul_mfe")),
            "ul_dd": ul_dd,
            "ul_retr": ul_retr,
            "quote_age": quote_age,
            "ul_fresh": ul_fresh,
            "mfe_trail_active": bool(mfe_trail_active),
            "bounded_mfe_capture_active": bool(bounded_mfe_capture_active),
            "bounded_mfe_capture_min_mfe": float(cfg.bounded_mfe_capture_min_mfe),
            "bounded_mfe_capture_max_mfe": float(cfg.bounded_mfe_capture_max_mfe),
            "bounded_mfe_capture_giveback": float(cfg.bounded_mfe_capture_giveback),
            "bounded_mfe_capture_min_pret": float(cfg.bounded_mfe_capture_min_pret),
            "bounded_mfe_capture_start_pt_hour": float(cfg.bounded_mfe_capture_start_pt_hour),
            "twenty_percent_save_enabled": bool(cfg.twenty_percent_save_enabled),
            "save20_stall": state.get(_SAVE20_STATE_KEY),
            "twenty_percent_save_min_mfe": float(cfg.twenty_percent_save_min_mfe),
            "twenty_percent_save_sides": str(cfg.twenty_percent_save_sides),
            "twenty_percent_save_min_t20_s": float(cfg.twenty_percent_save_min_t20_s),
            "twenty_percent_save_max_peak": float(cfg.twenty_percent_save_max_peak),
            "twenty_percent_save_giveback": float(cfg.twenty_percent_save_giveback),
            "twenty_percent_save_min_pret": float(cfg.twenty_percent_save_min_pret),
            "ae_shadow_would_spare": state.get("ae_shadow_would_spare"),
            "ae_shadow_exp_speed": state.get("ae_shadow_exp_speed"),
            "ae_shadow_sec_since_hwm": state.get("ae_shadow_sec_since_hwm"),
            "ae_warning_enabled": bool(cfg.ae_warning_enabled),
            "ae_warning_max_start_return": float(cfg.ae_warning_max_start_return),
            "ae_warning_active": bool(state.get("ae_warning_active")),
            "ae_warning_start_sse": _finite(state.get("ae_warning_start_sse")),
            "ae_warning_start_peak": _finite(state.get("ae_warning_start_peak")),
            "ae_warning_last_detail": state.get("ae_warning_last_detail"),
            "premium_trail_enabled": bool(cfg.premium_trail_enabled),
            "premium_trail_trigger_pct": float(cfg.premium_trail_trigger_pct),
            "premium_trail_exit_pct": float(cfg.premium_trail_exit_pct),
            "premium_trail_min_sec_since_hwm": float(cfg.premium_trail_min_sec_since_hwm),
            "early_premium_trail_enabled": bool(cfg.early_premium_trail_enabled),
            "hybrid_premium_trail_enabled": bool(cfg.hybrid_premium_trail_enabled),
            "hybrid_premium_trail_gt50_arm_mfe": float(cfg.hybrid_aggressive_arm_mfe),
            "hybrid_premium_trail_gt50_giveback": float(cfg.hybrid_aggressive_giveback),
            "hybrid_premium_trail_gt50_min_age_seconds": float(cfg.hybrid_aggressive_min_age_seconds),
            "hybrid_premium_trail_gt50_min_current_return": float(cfg.hybrid_aggressive_min_current_return),
            "hybrid_premium_trail_gt50_max_ul_retrace": float(cfg.hybrid_aggressive_max_ul_retrace),
            "hybrid_premium_trail_gt50_min_ul_drawdown": float(cfg.hybrid_aggressive_min_ul_drawdown),
            "hybrid_premium_trail_gt30_arm_mfe": float(cfg.hybrid_salvage_arm_mfe),
            "hybrid_premium_trail_gt30_giveback": float(cfg.hybrid_salvage_giveback),
            "hybrid_premium_trail_gt30_min_age_seconds": float(cfg.hybrid_salvage_min_age_seconds),
            "hybrid_premium_trail_gt30_min_current_return": float(cfg.hybrid_salvage_min_current_return),
            "hybrid_premium_trail_gt30_max_ul_retrace": float(cfg.hybrid_salvage_max_ul_retrace),
            "hybrid_premium_trail_gt30_min_ul_drawdown": float(cfg.hybrid_salvage_min_ul_drawdown),
            "underlying_mfe_capture_enabled": bool(cfg.underlying_mfe_capture_enabled),
            "underlying_mfe_capture_min_mfe": float(cfg.underlying_mfe_capture_min_mfe),
            "underlying_mfe_capture_max_mfe": float(cfg.underlying_mfe_capture_max_mfe),
            "underlying_mfe_capture_giveback": float(cfg.underlying_mfe_capture_giveback),
            "underlying_mfe_capture_min_pret": float(cfg.underlying_mfe_capture_min_pret),
            "underlying_mfe_capture_min_sec_hwm": float(cfg.underlying_mfe_capture_min_sec_hwm),
            "underlying_mfe_capture_min_ul_retr": float(cfg.underlying_mfe_capture_min_ul_retr),
            "underlying_mfe_capture_min_ul_dd": float(cfg.underlying_mfe_capture_min_ul_dd),
            "runner_capture_enabled": bool(cfg.runner_capture_enabled),
            "runner_capture_arm_mfe": float(cfg.runner_capture_arm_mfe),
            "runner_capture_arm_sse": runner_arm_sse,
            "runner_capture_arm_age_seconds": runner_arm_age,
            "runner_capture_timed_min_current_return": float(
                cfg.runner_capture_timed_min_current_return
            ),
            "runner_capture_timed_min_peak_ratio": float(
                cfg.runner_capture_timed_min_peak_ratio
            ),
            "runner_capture_timed_min_arm_age_seconds": float(
                cfg.runner_capture_timed_min_arm_age_seconds
            ),
            "runner_capture_floor_min_age_seconds": float(
                cfg.runner_capture_floor_min_age_seconds
            ),
            "runner_capture_floor_peak_ratio": float(cfg.runner_capture_floor_peak_ratio),
            "contract_fade_exit_enabled": bool(cfg.contract_fade_exit_enabled),
            "contract_fade_valid_bid_ask_contracts": _finite(valid_bid_ask_contracts),
            "contract_fade_bid": _finite(option_bid),
            "contract_fade_ask": _finite(option_ask),
            "contract_fade_prior_bid": cf_prior_bid,
            "contract_fade_prior_ask": cf_prior_ask,
            "contract_fade_bid_change": cf_bid_change,
            "contract_fade_ask_change": cf_ask_change,
            "contract_fade_quote_window_seconds": float(cfg.contract_fade_quote_window_seconds),
            "proven": bool(state.get("proven")),
            "gb32_timed_enabled": bool(cfg.gb32_timed_enabled),
            "gb30_extension_enabled": bool(cfg.gb30_extension_enabled),
            "gb30_extension_giveback": float(cfg.gb30_extension_giveback),
            "gb30_extension_max_ul_dd": float(cfg.gb30_extension_max_ul_dd),
            "gb30_extension_min_pret": float(cfg.gb30_extension_min_pret),
            "gb30_extension_max_pret": float(cfg.gb30_extension_max_pret),
            "gb30_extension_armed": bool(state.get("gb30_extension_armed")),
            "deep_stop_enabled": bool(cfg.deep_stop_enabled),
            "shallow_floor_stop_enabled": bool(cfg.shallow_floor_stop_enabled),
            "shallow_floor_peak_ticks": state.get("sf_peak_ticks"),
            "shallow_floor_seen_above_gate": bool(state.get("sf_seen_above_gate")),
            "runner_timing_v3a_enabled": bool(cfg.runner_timing_v3a_enabled),
            "runner_capture_timed_arm_age_used": rt_timed_age,
            "runner_capture_floor_age_used": rt_floor_age,
            "stall_floor_stop_enabled": bool(cfg.stall_floor_stop_enabled),
            "stall_floor_armed": bool(sbf_armed),
            "stall_floor_seen_above_armed": bool(state.get("sbf_seen_above_armed")),
            "stall_floor_below_ticks": state.get("sbf_below_n"),
            "runner_extension_enabled": bool(cfg.runner_extension_enabled),
            "runner_ext_l0": _finite(state.get("runner_ext_l0")),
            "runner_ext_start_age": _finite(state.get("runner_ext_start_age")),
            "runner_ext_high": _finite(state.get("runner_ext_high")),
            "runner_ext_ticks": state.get("runner_ext_ticks"),
            "runner_ext_prev_pret": _finite(state.get("runner_ext_prev_pret")),
            "guarded_retrace_enabled": bool(cfg.guarded_retrace_enabled),
            "guarded_retrace_ticks": state.get("gbul_ticks"),
            "ulr_armed": state.get("ulr_armed"),
            "ulr_umax": _finite(state.get("ulr_umax")),
            "ulr_dd": state.get("ulr_dd"),
            "ulr_retr": state.get("ulr_retr"),
            "ul_giveback_enabled": bool(cfg.ul_giveback_enabled),
            "ul_giveback_ticks": state.get("ufr_ticks"),
            "detail": detail,
        }
        osnap["pc_architecture"] = "path_v2"
        return PathV2Decision(exit_reason=exit_reason, detail=detail, telemetry=telemetry)

    # =====================================================================
    # STATE S10 | Confirmed premium peak for STEP 29-30 | Exit Reason: none (state)
    # Knobs: OPTION_PATH_V2_ATR_LOSS_CUT_ENABLED / OPTION_PATH_V2_VWAP_CROSS_EXIT_ENABLED
    # Rule: confirmed peak = the highest min(bid pret, previous tick bid pret), so a
    #       one-tick print does not count. Runs on every priced tick while either is on.
    # =====================================================================
    if pret_f is not None and (bool(cfg.atr_loss_cut_enabled) or bool(cfg.vwap_cross_exit_enabled)):
        tv_prev = _finite(state.get("tv_prev_pret"))
        tv_c2 = pret_f if tv_prev is None else min(pret_f, tv_prev)
        state["tv_prev_pret"] = pret_f
        tv_cpk_prev = _finite(state.get("tv_confirmed_peak"))
        if tv_cpk_prev is None or tv_c2 > tv_cpk_prev:
            state["tv_confirmed_peak"] = tv_c2

    # =====================================================================
    # STEP 0 | No executable mark | Exit Reason: none (hold)
    # Rule: a tick with no pret does not update the peak and never exits.
    # =====================================================================
    if pret_f is None:
        return _decide(None, "")

    # =====================================================================
    # STEP 1 | Runner Extension (R2X) | Exit Reason: runner_extended_capture / RUNNER_EXT_TARGET, RUNNER_EXT_ULREV; runner_extension_floor / RUNNER_EXT_FLOOR
    # Knobs: OPTION_PATH_V2_RUNNER_EXTENSION_ENABLED (true; dataclass default false),
    #        _TARGET_MULT (1.25), _UL_RETRACE (0.04), _UL_MIN_MULT (1.045), _FLOOR_PRET (0.50);
    #        hold mode is entered in STEP 7 (_MIN_TICKS 2, _JUMP_GUARD 0.5)
    # Rule: in hold mode (L0 = bid return at the timed-capture touch) only this step exits:
    #       bid < +50% -> floor; bid >= 1.25 x L0 -> target; bid >= 1.045 x L0 and
    #       UL retrace >= 0.04 (fresh UL) -> UL reversal; else HOLD and skip STEP 2-28.
    # =====================================================================
    r2x_l0 = _finite(state.get("runner_ext_l0"))
    if bool(cfg.runner_extension_enabled) and r2x_l0 is not None:
        r2x_high = _finite(state.get("runner_ext_high"))
        if r2x_high is None or pret_f > r2x_high + _EPS:
            state["runner_ext_high"] = pret_f  # telemetry only
        if pret_f < float(cfg.runner_extension_floor_pret) - _EPS:
            return _decide(REASON_RUNNER_EXTENSION_FLOOR, DETAIL_RUNNER_EXT_FLOOR)
        if pret_f >= r2x_l0 * float(cfg.runner_extension_target_mult) - _EPS:
            return _decide(REASON_RUNNER_EXTENDED_CAPTURE, DETAIL_RUNNER_EXT_TARGET)
        r2x_ul_retr = _finite(ul_retr)
        if (
            float(cfg.runner_extension_ul_retrace) > 0
            and r2x_ul_retr is not None
            and pret_f >= r2x_l0 * float(cfg.runner_extension_ul_min_mult) - _EPS
            and r2x_ul_retr >= float(cfg.runner_extension_ul_retrace) - _EPS
        ):
            return _decide(REASON_RUNNER_EXTENDED_CAPTURE, DETAIL_RUNNER_EXT_ULREV)
        return _decide(None, DETAIL_RUNNER_EXT_HOLD)

    # =====================================================================
    # STEP 2 | 20% Save | Exit Reason: twenty_percent_save / P20_STALL_SAVE
    # Knobs: OPTION_PATH_V2_20P_SAVE_ENABLED (kill switch), _MIN_MFE (0.20 touch),
    #   _SIDES (call), _MIN_T20_S (90), _MAX_PEAK (0.50), _MIN_SINCE_TOUCH_S (60),
    #   _MIN_SEC_HWM (60), _GIVEBACK (0.15), _MIN_PRET (0.11), _MAX_NEWHIGH_FRAC (0.05),
    #   _SHADOW_ONLY (false).
    # Latch at the first tick bid pret >= +20% (exact): t20 = seconds open->touch.
    # EXIT a CALL when t20 >= 90s, peak < +50%, >= 60s since touch, >= 60s since
    # the peak, peak - pret >= 15pp, pret >= +11% and new-high ticks / ticks since
    # touch <= 0.05. Premium path only. Same-tick result as running it after every
    # other step (an exit here and a later step's exit are at the same bid).
    # =====================================================================
    if bool(cfg.twenty_percent_save_enabled):
        _s20_fire, _s20_latch = _save20_stall_step(
            cfg,
            state,
            side=side,
            pret_f=pret_f,
            now_dt=now_dt,
            t20_base=opened,
        )
        if _s20_fire and not bool(cfg.twenty_percent_save_shadow_only):
            return _decide(REASON_TWENTY_PERCENT_SAVE, DETAIL_TWENTY_PERCENT_SAVE)
    # STEP 2 extension | UL-retrace save (code default OFF; Shared.env ON) | twenty_percent_save / P20_UL_RETRACE_SAVE
    # Knobs: OPTION_PATH_V2_20P_SAVE_ENABLED (master) AND OPTION_PATH_V2_20P_SAVE_ULR_ENABLED.
    #   UL <= 0 or None is unavailable (no arm, no fire). _FRAC (0.90),
    #   _MAX_ENTRY_VWAP_ATR (0.50). Shared.env sets ULR ENABLED=true.
    #   Fails safe (never arms) if osnap lacks entry_distance_from_vwap_atr.
    # Arm at the first pret >= +20%; record the last fresh UL. Only when the entry
    # |distance_from_vwap_atr| < 0.50. EXIT when the fresh UL gives back >= FRAC of
    # its entry->touch move (side-aware).
    if bool(cfg.twenty_percent_save_enabled) and bool(cfg.twenty_percent_save_ulr_enabled):
        if _save20_ulr_step(
            cfg, state, osnap, side=side, pret_f=pret_f, now_dt=now_dt,
            entry_underlying=entry_underlying, current_underlying=current_underlying,
            ul_quote_ts=ul_quote_ts,
        ) and not bool(cfg.twenty_percent_save_shadow_only):
            return _decide(REASON_TWENTY_PERCENT_SAVE, DETAIL_TWENTY_PERCENT_SAVE_ULR)

    # =====================================================================
    # STEP 3 | Shallow proof stop | Exit Reason: shallow_proof_stop / SHALLOW_PROOF_10_20
    # Knobs: OPTION_PATH_V2_SHALLOW_PROOF_STOP_ENABLED (true), _MIN_AGE_SECONDS (60),
    #        _MIN_MFE (0.10), _MAX_MFE (0.20), _PRET (-0.30)
    # Rule: age >= 60s, peak 10% up to 20%, bid <= -30%. Beats the 120s hold.
    # =====================================================================
    if (
        bool(cfg.shallow_proof_stop_enabled)
        and age >= float(cfg.shallow_proof_stop_min_age_seconds) - _EPS
        and running_mfe >= float(cfg.shallow_proof_stop_min_mfe) - _EPS
        and running_mfe < float(cfg.shallow_proof_stop_max_mfe) - _EPS
        and pret_f <= float(cfg.shallow_proof_stop_pret) + _EPS
    ):
        _clear_ae_warning(state)
        return _decide(REASON_SHALLOW_PROOF, DETAIL_SHALLOW_PROOF)

    # =====================================================================
    # STEP 4 | Catastrophic premium stop (mandatory) | Exit Reason: catastrophic_premium_stop / CAT_PREM_STOP
    # Knobs: OPTION_PATH_V2_CAT_FLOOR (-0.80), OPTION_PATH_V2_CAT_MIN_AGE_SECONDS (60)
    # Rule: bid <= -80% and age >= 60s. Beats the 120s hold.
    # =====================================================================
    if pret_f <= float(cfg.catastrophic_prem_stop_pct) and age >= float(
        cfg.catastrophic_min_age_seconds
    ) - _EPS:
        return _decide(REASON_CATASTROPHIC, DETAIL_CAT)

    # =====================================================================
    # STEP 5 | Afternoon MFE trail (mandatory) | Exit Reason: mfe_trail / MFE_TRAIL_1230
    # Knobs: caller (after 12:30 PT): mfe_trail_active, trigger 0.10, giveback 0.10
    # Rule: after 12:30 PT, peak >= 10% and giveback >= 10pp. Beats the 120s hold.
    # =====================================================================
    if (
        bool(mfe_trail_active)
        and running_mfe >= trigger - _EPS
        and (running_mfe - pret_f) >= trail_exit - _EPS
    ):
        return _decide(REASON_MFE_TRAIL, DETAIL_MFE_TRAIL)

    # =====================================================================
    # STEP 6 | Absorption exit (mandatory) | Exit Reason: absorption_exit / ABSORPTION_EXIT
    # Knobs: OPTION_PATH_V2_ABSORPTION_EXIT_ENABLED (true), _MIN_AGE_SECONDS (450),
    #        _MAX_SPEED (0.00005), _MIN_PRET (0.02)
    # Rule: age >= 450s, peak / age < 0.00005 (not expanding), bid >= +2%.
    # =====================================================================
    if (
        bool(cfg.absorption_exit_enabled)
        and age >= float(cfg.absorption_min_age_seconds) - _EPS
        and (running_mfe / age if age > 1.0 else 0.0) < float(cfg.absorption_max_speed)
        and pret_f >= float(cfg.absorption_min_pret) - _EPS
    ):
        return _decide(REASON_ABSORPTION, DETAIL_ABSORPTION)

    # =====================================================================
    # STEP 7 | 350% runner timed capture | Exit Reason: runner_timed_capture /
    #          RUNNER_TIMED_CAPTURE_350 (RUNNER_TIMED_CAPTURE_350_A30 when only the V3A 30s arm allowed it)
    # Knobs: OPTION_PATH_V2_RUNNER_CAPTURE_ENABLED (true), _ARM_MFE (3.50),
    #        _TIMED_MIN_CURRENT_RETURN (4.00), _TIMED_MIN_PEAK_RATIO (0.50), arm age from S8;
    #        R2X entry: OPTION_PATH_V2_RUNNER_EXTENSION_ENABLED (true), _MIN_TICKS (2), _JUMP_GUARD (0.5)
    # Rule: armed >= 30s (V3A; base 60s), peak >= 350%, bid >= +400% and >= 50% of peak.
    #       R2X on: if >= 2 ticks came before this one and the previous tick's bid return
    #       is >= 0.5 x this one (gap guard), do not sell: store L0 = this bid return and
    #       HOLD (STEP 1 owns the exit from the next tick). Guard fails -> sell as before.
    # =====================================================================
    if (
        bool(cfg.runner_capture_enabled)
        and runner_arm_age is not None
        and runner_arm_age >= rt_timed_age - _EPS
        and running_mfe >= float(cfg.runner_capture_arm_mfe) - _EPS
        and pret_f >= float(cfg.runner_capture_timed_min_current_return) - _EPS
        and (pret_f / running_mfe if running_mfe > _EPS else 0.0)
        >= float(cfg.runner_capture_timed_min_peak_ratio) - _EPS
    ):
        if bool(cfg.runner_extension_enabled):
            r2x_prev = _finite(state.get("runner_ext_prev_pret"))
            if (
                int(state.get("runner_ext_ticks") or 0) - 1 >= int(cfg.runner_extension_min_ticks)
                and r2x_prev is not None
                and r2x_prev >= float(cfg.runner_extension_jump_guard) * pret_f - 1e-9
            ):
                state["runner_ext_l0"] = pret_f
                state["runner_ext_start_age"] = age
                state["runner_ext_high"] = pret_f
                return _decide(None, DETAIL_RUNNER_EXT_START)
        _clear_ae_warning(state)
        if runner_arm_age < float(cfg.runner_capture_timed_min_arm_age_seconds) - _EPS:
            return _decide(REASON_RUNNER_TIMED_CAPTURE, DETAIL_RUNNER_TIMED_CAPTURE_V3A)
        return _decide(REASON_RUNNER_TIMED_CAPTURE, DETAIL_RUNNER_TIMED_CAPTURE)

    # =====================================================================
    # STEP 8 | 350% runner profit floor | Exit Reason: runner_profit_floor /
    #          RUNNER_PROFIT_FLOOR_350 (RUNNER_PROFIT_FLOOR_350_G900 when the V3A 900s green wait applied)
    # Knobs: OPTION_PATH_V2_RUNNER_CAPTURE_ENABLED (true), _ARM_MFE (3.50),
    #        _FLOOR_PEAK_RATIO (0.80), floor wait from S8
    # Rule: peak >= 350%, age >= wait (900s while bid >= +50%, else 600s), bid <= 80% of peak.
    # =====================================================================
    if (
        bool(cfg.runner_capture_enabled)
        and running_mfe >= float(cfg.runner_capture_arm_mfe) - _EPS
        and age >= rt_floor_age - _EPS
        and pret_f <= running_mfe * float(cfg.runner_capture_floor_peak_ratio) + _EPS
    ):
        _clear_ae_warning(state)
        if rt_floor_age > float(cfg.runner_capture_floor_min_age_seconds) + _EPS:
            return _decide(REASON_RUNNER_PROFIT_FLOOR, DETAIL_RUNNER_PROFIT_FLOOR_V3A)
        return _decide(REASON_RUNNER_PROFIT_FLOOR, DETAIL_RUNNER_PROFIT_FLOOR)

    # =====================================================================
    # STEP 9 | UL retrace capture (GBUL + UFR) | Exit Reason: guarded_retrace_exit / GBUL_RETRACE_05_65; ul_giveback_ratio_exit / UFR_GIVEBACK_30_70
    # Knobs: OPTION_PATH_V2_GBUL_ENABLED (true; dataclass default false), _GBUL_MIN_MFE (0.30),
    #        _GBUL_MAX_MFE (2.00), _GBUL_MIN_UL_DD (0.005), _GBUL_MIN_PEAK_RATIO (0.65), _GBUL_CONFIRM_TICKS (2);
    #        OPTION_PATH_V2_UFR_ENABLED (true; dataclass default false), _UFR_MIN_MFE (0.30), _UFR_MAX_MFE (2.00),
    #        _UFR_MIN_UL_MFE (0.005), _UFR_MIN_UL_RETRACE (0.30), _UFR_MIN_PEAK_RATIO (0.70), _UFR_MIN_PRET (0.03),
    #        _UFR_CONFIRM_TICKS (2)
    # Rule: both read the S2 tracker (ulr_dd, ulr_umax, ulr_retr; None = hold, count resets) and need a
    #       peak of 30% up to (not including) 200%, so the 350% runner path is untouched.
    #       GBUL: favorable UL is >= 0.5% off its extreme and the bid is still >= 0.65 x the peak.
    #       UFR: the favorable UL moved >= 0.5% from its anchor and has given back >= 30% of that
    #       move, the bid is >= 0.70 x the peak and >= +3%. Each rule must hold on 2 consecutive
    #       ticks (own counter). Runs after the mandatory exits and the runner steps, before every
    #       giveback / stale exit it pre-empts. GBUL wins a same-tick tie.
    #       8/19-10/22 replay vs the prior engine: Total +$4,593, TP +$1,566, 0 flips, 0 worsened losses.
    # =====================================================================
    gbul_hit = False
    if bool(cfg.guarded_retrace_enabled):
        gbul_ok = (
            ulr_dd is not None
            and running_mfe >= float(cfg.guarded_retrace_min_mfe) - _EPS
            and running_mfe < float(cfg.guarded_retrace_max_mfe) - _EPS
            and ulr_dd >= float(cfg.guarded_retrace_min_ul_dd) - _EPS
            and pret_f >= running_mfe * float(cfg.guarded_retrace_min_peak_ratio) - _EPS
        )
        state["gbul_ticks"] = (int(state.get("gbul_ticks") or 0) + 1) if gbul_ok else 0
        gbul_hit = state["gbul_ticks"] >= max(1, int(cfg.guarded_retrace_confirm_ticks))
    ufr_hit = False
    if bool(cfg.ul_giveback_enabled):
        ufr_ok = (
            ulr_retr is not None
            and ulr_umax is not None
            and running_mfe >= float(cfg.ul_giveback_min_mfe) - _EPS
            and running_mfe < float(cfg.ul_giveback_max_mfe) - _EPS
            and ulr_umax >= float(cfg.ul_giveback_min_ul_mfe) - _EPS
            and ulr_retr >= float(cfg.ul_giveback_min_ul_retrace) - _EPS
            and pret_f >= running_mfe * float(cfg.ul_giveback_min_peak_ratio) - _EPS
            and pret_f >= float(cfg.ul_giveback_min_pret) - _EPS
        )
        state["ufr_ticks"] = (int(state.get("ufr_ticks") or 0) + 1) if ufr_ok else 0
        ufr_hit = state["ufr_ticks"] >= max(1, int(cfg.ul_giveback_confirm_ticks))
    if gbul_hit:
        _clear_ae_warning(state)
        return _decide(REASON_GUARDED_RETRACE, DETAIL_GUARDED_RETRACE)
    if ufr_hit:
        _clear_ae_warning(state)
        return _decide(REASON_UL_GIVEBACK, DETAIL_UL_GIVEBACK)

    # =====================================================================
    # STEP 10 | Hybrid premium trail stage 1 (OFF) | Exit Reason: premium_trail_>50%_25trail / PREMIUM_TRAIL_GT50_25TRAIL
    # Knobs: OPTION_PATH_V2_HYBRID_PREMIUM_TRAIL_ENABLED (false), OPTION_PATH_V2_PREMIUM_TRAIL_GT50_*
    #        (arm 0.50, giveback 0.25, age 60, min return 0.20, max UL retrace 0.50, min UL dd 0.001)
    # Rule: needs a fresh UL quote. Peak >= 50%, giveback >= 25pp, age >= 60s, bid >= +20%,
    #       UL retrace <= 0.50 and UL drawdown >= 0.001.
    # =====================================================================
    if (
        bool(cfg.hybrid_premium_trail_enabled)
        and ul_retr is not None
        and ul_dd is not None
        and age >= float(cfg.hybrid_aggressive_min_age_seconds) - _EPS
        and running_mfe >= float(cfg.hybrid_aggressive_arm_mfe) - _EPS
        and (running_mfe - pret_f) >= float(cfg.hybrid_aggressive_giveback) - _EPS
        and pret_f >= float(cfg.hybrid_aggressive_min_current_return) - _EPS
        and ul_retr <= float(cfg.hybrid_aggressive_max_ul_retrace) + _EPS
        and ul_dd >= float(cfg.hybrid_aggressive_min_ul_drawdown) - _EPS
    ):
        _clear_ae_warning(state)
        return _decide(REASON_PREMIUM_TRAIL_50_25, DETAIL_PREMIUM_TRAIL_50_25)

    # =====================================================================
    # STEP 11 | Hybrid premium trail stage 2 (OFF) | Exit Reason: premium_trail_>30%_10trail / PREMIUM_TRAIL_GT30_10TRAIL
    # Knobs: OPTION_PATH_V2_HYBRID_PREMIUM_TRAIL_ENABLED (false), OPTION_PATH_V2_PREMIUM_TRAIL_GT30_*
    #        (arm 0.30, giveback 0.10, age 60, min return 0.05, max UL retrace 0.75, min UL dd 0.0)
    # Rule: same shape as stage 1 with the GT30 knobs.
    # =====================================================================
    if (
        bool(cfg.hybrid_premium_trail_enabled)
        and ul_retr is not None
        and ul_dd is not None
        and age >= float(cfg.hybrid_salvage_min_age_seconds) - _EPS
        and running_mfe >= float(cfg.hybrid_salvage_arm_mfe) - _EPS
        and (running_mfe - pret_f) >= float(cfg.hybrid_salvage_giveback) - _EPS
        and pret_f >= float(cfg.hybrid_salvage_min_current_return) - _EPS
        and ul_retr <= float(cfg.hybrid_salvage_max_ul_retrace) + _EPS
        and ul_dd >= float(cfg.hybrid_salvage_min_ul_drawdown) - _EPS
    ):
        _clear_ae_warning(state)
        return _decide(REASON_PREMIUM_TRAIL_30_10, DETAIL_PREMIUM_TRAIL_30_10)

    # =====================================================================
    # STEP 12 | Premium spike fail / Underlying-confirmed MFE capture | Exit Reason: premium_spike_fail / SPIKE_FAIL; underlying_mfe_capture / UL_MFE_CAPTURE_175_250
    # Knobs: OPTION_PATH_V2_SPIKE_FAIL_ENABLED (true), _ARM_MFE (0.30), _MAX_MFE (0.60),
    #        _GIVEBACK (0.15), _MIN_PRET (0.10), _MIN_AGE_SECONDS (60), _MAX_SEC_HWM (16)
    # Rule: peak 30-60%, giveback >= 15pp, bid >= +10%, age >= 60s, high no older than 16s.
    # =====================================================================
    if (
        bool(cfg.spike_fail_enabled)
        and age >= float(cfg.spike_fail_min_age_seconds) - _EPS
        and running_mfe >= float(cfg.spike_fail_arm_mfe) - _EPS
        and running_mfe <= float(cfg.spike_fail_max_mfe) + _EPS
        and (running_mfe - pret_f) >= float(cfg.spike_fail_giveback) - _EPS
        and pret_f >= float(cfg.spike_fail_min_pret) - _EPS
        and sec_since_hwm is not None
        and sec_since_hwm <= float(cfg.spike_fail_max_sec_hwm) + _EPS
    ):
        _clear_ae_warning(state)
        return _decide(REASON_SPIKE_FAIL, DETAIL_SPIKE_FAIL)

    # The narrow MFE band prevents this from clipping the 350% runner path.
    # It sells only after a fresh, meaningful underlying reversal confirms a
    # mature premium high has retraced while the contract remains strongly green.
    if (
        bool(cfg.underlying_mfe_capture_enabled)
        and ul_retr is not None
        and ul_dd is not None
        and sec_since_hwm is not None
        and running_mfe >= float(cfg.underlying_mfe_capture_min_mfe) - _EPS
        and running_mfe < float(cfg.underlying_mfe_capture_max_mfe) - _EPS
        and (running_mfe - pret_f) >= float(cfg.underlying_mfe_capture_giveback) - _EPS
        and pret_f >= float(cfg.underlying_mfe_capture_min_pret) - _EPS
        and sec_since_hwm >= float(cfg.underlying_mfe_capture_min_sec_hwm) - _EPS
        and ul_retr >= float(cfg.underlying_mfe_capture_min_ul_retr) - _EPS
        and ul_dd >= float(cfg.underlying_mfe_capture_min_ul_dd) - _EPS
    ):
        _clear_ae_warning(state)
        return _decide(REASON_UNDERLYING_MFE_CAPTURE, DETAIL_UNDERLYING_MFE_CAPTURE)

    # =====================================================================
    # STEP 13 | Premium stale high fail (7 shapes) | Exit Reason: premium_stale_fail / STALE_HIGH_FAIL
    # Knobs: OPTION_PATH_V2_STALE_FAIL_ENABLED (true), OPTION_PATH_V2_STALE_FAIL_* (per shape below)
    # Rule: any one shape fires. Needs a premium high (sec_since_hwm).
    #   band     70-90% peak, bid +2% to +8%, high >= 1000s old
    #   early    70-90% peak, giveback >= 50pp, bid >= +25%, high >= 300s old
    #   upper    80-82% peak, bid +2% to +40%, high >= 180s old, UL drawdown >= 0.003 (fresh UL)
    #   reversal 50% up to 70% peak, bid +2% to +20%, high >= 200s old, UL against the trade,
    #            UL retrace >= 1.15, UL drawdown >= 0.004 (fresh UL)
    #   gap      90% up to 94% peak, bid +2% to +20%, high >= 800s old, UL retrace >= 0.55 (fresh UL)
    #   high     peak >= 100%, bid +2% to +25%, high >= 1800s old
    #   runner   peak >= 150%, bid +2% to +20%, UL retrace >= 0.50, age >= 60s (fresh UL)
    # =====================================================================
    if bool(cfg.stale_fail_enabled) and sec_since_hwm is not None:
        sh_band = (
            running_mfe >= float(cfg.stale_fail_band_min_mfe) - _EPS
            and running_mfe <= float(cfg.stale_fail_band_max_mfe) + _EPS
            and pret_f >= float(cfg.stale_fail_band_min_pret) - _EPS
            and pret_f <= float(cfg.stale_fail_band_max_pret) + _EPS
            and sec_since_hwm >= float(cfg.stale_fail_band_min_sec_hwm) - _EPS
        )
        sh_early = (
            running_mfe >= float(cfg.stale_fail_giveback_min_mfe) - _EPS
            and running_mfe <= float(cfg.stale_fail_giveback_max_mfe) + _EPS
            and (running_mfe - pret_f) >= float(cfg.stale_fail_giveback) - _EPS
            and pret_f >= float(cfg.stale_fail_giveback_min_pret) - _EPS
            and sec_since_hwm >= float(cfg.stale_fail_giveback_min_sec_hwm) - _EPS
        )
        sh_upper = (
            ul_dd is not None
            and running_mfe >= float(cfg.stale_fail_upper_min_mfe) - _EPS
            and running_mfe <= float(cfg.stale_fail_upper_max_mfe) + _EPS
            and pret_f >= float(cfg.stale_fail_upper_min_pret) - _EPS
            and pret_f <= float(cfg.stale_fail_upper_max_pret) + _EPS
            and sec_since_hwm >= float(cfg.stale_fail_upper_min_sec_hwm) - _EPS
            and ul_dd >= float(cfg.stale_fail_upper_min_ul_dd) - _EPS
        )
        sh_reversal = (
            ul_retr is not None
            and ul_dir is not None
            and ul_dd is not None
            and ul_dir < 0.0
            and running_mfe >= float(cfg.stale_fail_reversal_min_mfe) - _EPS
            and running_mfe < float(cfg.stale_fail_reversal_max_mfe)
            and pret_f >= float(cfg.stale_fail_reversal_min_pret) - _EPS
            and pret_f <= float(cfg.stale_fail_reversal_max_pret) + _EPS
            and sec_since_hwm >= float(cfg.stale_fail_reversal_min_sec_hwm) - _EPS
            and ul_retr >= float(cfg.stale_fail_reversal_min_ul_retr) - _EPS
            and ul_dd >= float(cfg.stale_fail_reversal_min_ul_dd) - _EPS
        )
        sh_gap = (
            ul_retr is not None
            and running_mfe >= float(cfg.stale_fail_gap_min_mfe) - _EPS
            and running_mfe < float(cfg.stale_fail_gap_max_mfe)
            and pret_f >= float(cfg.stale_fail_gap_min_pret) - _EPS
            and pret_f <= float(cfg.stale_fail_gap_max_pret) + _EPS
            and sec_since_hwm >= float(cfg.stale_fail_gap_min_sec_hwm) - _EPS
            and ul_retr >= float(cfg.stale_fail_gap_min_ul_retr) - _EPS
        )
        sh_high = (
            running_mfe >= float(cfg.stale_fail_high_min_mfe) - _EPS
            and pret_f >= float(cfg.stale_fail_high_min_pret) - _EPS
            and pret_f <= float(cfg.stale_fail_high_max_pret) + _EPS
            and sec_since_hwm >= float(cfg.stale_fail_high_min_sec_hwm) - _EPS
        )
        sh_runner = (
            ul_retr is not None
            and running_mfe >= float(cfg.stale_fail_runner_min_mfe) - _EPS
            and pret_f >= float(cfg.stale_fail_runner_min_pret) - _EPS
            and pret_f <= float(cfg.stale_fail_runner_max_pret) + _EPS
            and ul_retr >= float(cfg.stale_fail_runner_min_ul_retr) - _EPS
            and age >= float(cfg.stale_fail_runner_min_age_seconds) - _EPS
        )
        if sh_band or sh_early or sh_upper or sh_reversal or sh_gap or sh_high or sh_runner:
            _clear_ae_warning(state)
            return _decide(REASON_STALE_FAIL, DETAIL_STALE_FAIL)

    # =====================================================================
    # STEP 14 | Theta exit (OFF, experimental) | Exit Reason: theta_exit / THETA_EXIT
    # Knobs: OPTION_PATH_V2_THETA_EXIT_ENABLED (false), _MIN_AGE_SECONDS (300), _MIN (-0.50)
    # Rule: never green (peak < 0), age >= 300s, theta >= -0.50.
    # =====================================================================
    if (
        bool(cfg.theta_exit_enabled)
        and _finite(theta) is not None
        and float(_finite(theta)) >= float(cfg.theta_exit_min) - _EPS
        and age >= float(cfg.theta_exit_min_age_seconds) - _EPS
        and running_mfe < 0.0
    ):
        return _decide(REASON_THETA, DETAIL_THETA)

    # =====================================================================
    # STEP 15 | Contract Fade Exit | Exit Reason: contract_fade_exit / CONTRACT_FADE_EXIT
    # Knobs: OPTION_PATH_V2_CONTRACT_FADE_EXIT_ENABLED (true), _MAX_VALID_BID_ASK_CONTRACTS (13),
    #        _MIN_MFE (0), _MAX_MFE (0.20), _MIN_DRAWDOWN (0.02), _MIN_SEC_HWM (15); quotes from S4
    # Rule: thin entry chain, peak above 0 and under 20%, >= 2pp off the high, high >= 15s
    #       old, and bid and ask both lower than the quote >= 30s earlier. Beats the 120s hold.
    # =====================================================================
    if (
        bool(cfg.contract_fade_exit_enabled)
        and _finite(valid_bid_ask_contracts) is not None
        and float(_finite(valid_bid_ask_contracts)) <= float(cfg.contract_fade_max_valid_bid_ask_contracts) + _EPS
        and running_mfe > float(cfg.contract_fade_min_mfe) + _EPS
        and running_mfe < float(cfg.contract_fade_max_mfe) - _EPS
        and (running_mfe - pret_f) >= float(cfg.contract_fade_min_drawdown) - _EPS
        and sec_since_hwm is not None
        and sec_since_hwm >= float(cfg.contract_fade_min_sec_hwm) - _EPS
        and cf_bid_change is not None
        and cf_ask_change is not None
        and cf_bid_change < -_EPS
        and cf_ask_change < -_EPS
    ):
        _clear_ae_warning(state)
        return _decide(REASON_CONTRACT_FADE, DETAIL_CONTRACT_FADE)

    # =====================================================================
    # STEP 16 | 120s minimum hold + region split | Exit Reason: none (state / hold)
    # Knobs: OPTION_PATH_V2_MIN_HOLD_SECONDS (120), OPTION_PATH_V2_EARLY_CUT (0.15),
    #        OPTION_PATH_V2_PROOF (0.50), OPTION_PATH_V2_RTC_VEL_WINDOW (10)
    # Rule: under 120s, STEP 17-25 are skipped (HOLD) and only STEP 26-28 can
    #       exit. From 120s: retrace fraction and 10s premium velocity are
    #       computed, and the peak picks one region: immature (< 15%),
    #       middle (15% up to 50%), or mature (>= 50%).
    # =====================================================================
    past_hold = age >= float(cfg.min_hold_seconds) - _EPS
    measured_vel: Optional[float] = None
    if past_hold:
        if peak is not None and peak > _EPS:
            frac = (peak - pret_f) / peak
        else:
            frac = 0.0
        vel = _prem_vel(samples, age, pret_f, cfg.rtc_vel_window_seconds)
        measured_vel = _prem_vel_measured(samples, age, pret_f, cfg.rtc_vel_window_seconds)

    # =====================================================================
    # STEP 17 | CALL shallow-green exit | Exit Reason: shallow_green_exit / SHALLOW_GREEN_EXIT
    # Knobs: OPTION_PATH_V2_SHALLOW_GREEN_EXIT_ENABLED (true), OPTION_PATH_V2_SHALLOW_GREEN_*
    #        (age 120, MFE 0.02-0.20, high age 90, max vel 0, min pret 0);
    #        Oct 26 SGUL hold: OPTION_PATH_V2_SG_UL_HOLD_ENABLED (true; dataclass default false),
    #        _SG_UL_HOLD_MIN_FAV (0.005), _SG_UL_HOLD_MAX_GIVEBACK (0.001)
    # Rule: CALL only. Peak 2% up to 20%, high >= 90s old, measured 10s velocity <= 0, bid >= 0.
    #       SGUL hold: when all of that passes, HOLD (no exit this tick) while the STATE S1
    #       favorable UL move since entry (ul_dir) >= +0.5% AND its giveback from the best
    #       favorable level (ul_dd) <= 0.1%. Both need a fresh UL quote this tick; a stale UL
    #       or either condition failing lets the exit fire as before. Re-checked every tick.
    # =====================================================================
    if (
        past_hold
        and bool(cfg.shallow_green_exit_enabled)
        and side == "call"
        and age >= float(cfg.shallow_green_min_age_seconds) - _EPS
        and running_mfe >= float(cfg.shallow_green_min_mfe) - _EPS
        and running_mfe < float(cfg.shallow_green_max_mfe) - _EPS
        and sec_since_hwm is not None
        and sec_since_hwm >= float(cfg.shallow_green_min_sec_hwm) - _EPS
        and measured_vel is not None
        and measured_vel <= float(cfg.shallow_green_max_vel) + _EPS
        and pret_f >= float(cfg.shallow_green_min_pret) - _EPS
    ):
        sgul_hold = (
            bool(getattr(cfg, "shallow_green_ul_hold_enabled", False))
            and ul_dir is not None
            and ul_dd is not None
            and ul_dir >= float(cfg.shallow_green_ul_hold_min_fav)
            and ul_dd <= float(cfg.shallow_green_ul_hold_max_giveback)
        )
        if not sgul_hold:
            _clear_ae_warning(state)
            return _decide(REASON_SHALLOW_GREEN, DETAIL_SHALLOW_GREEN)

    # =====================================================================
    # STEP 18 | Shallow time stop | Exit Reason: shallow_time_stop / SHALLOW_15M_MFE7P5
    # Knobs: OPTION_PATH_V2_SHALLOW_TIME_STOP_ENABLED (true), _SECONDS (900), _MAX_MFE (0.075)
    # Rule: age >= 15 minutes and the peak is still under 7.5%.
    # =====================================================================
    if (
        past_hold
        and bool(cfg.shallow_time_stop_enabled)
        and age >= float(cfg.shallow_time_stop_seconds) - _EPS
        and running_mfe < float(cfg.shallow_time_stop_max_mfe) - _EPS
    ):
        _clear_ae_warning(state)
        return _decide(REASON_SHALLOW_TIME, DETAIL_SHALLOW_TIME)

    # =====================================================================
    # STEP 19 | Bounded high-MFE capture | Exit Reason: bounded_mfe_capture / BOUNDED_MFE_CAPTURE_290_305
    # Knobs: OPTION_PATH_V2_BOUNDED_MFE_CAPTURE_ENABLED (false), _MIN_MFE (2.90), _MAX_MFE (3.05),
    #        _GIVEBACK (0.10), _MIN_PRET (1.00), _START_PT_HOUR (10.75)
    # Rule: after 10:45 PT and before the mandatory afternoon trail owns the tick, peak is 290-305%,
    #       bid is still at least +100%, and premium has given back at least 10pp. The 305% ceiling
    #       preserves the 350% runner regime.
    # =====================================================================
    if (
        past_hold
        and bool(cfg.bounded_mfe_capture_enabled)
        and bool(bounded_mfe_capture_active)
        and running_mfe >= float(cfg.bounded_mfe_capture_min_mfe) - _EPS
        and running_mfe < float(cfg.bounded_mfe_capture_max_mfe) - _EPS
        and pret_f >= float(cfg.bounded_mfe_capture_min_pret) - _EPS
        and (running_mfe - pret_f) >= float(cfg.bounded_mfe_capture_giveback) - _EPS
    ):
        _clear_ae_warning(state)
        return _decide(REASON_BOUNDED_MFE_CAPTURE, DETAIL_BOUNDED_MFE_CAPTURE)

    # =====================================================================
    # STEP 20 | Early premium trail, 175% guard (OFF) | Exit Reason: premium_trail / PATH_V2_EARLY_PREM_TRAIL
    # Knobs: OPTION_PATH_V2_EARLY_PREM_TRAIL_ENABLED (false), _TRIGGER (1.75), _EXIT (0.05), _MIN_CURRENT (0.75)
    # Rule: peak >= 175%, bid >= +75%, giveback >= 5pp.
    # =====================================================================
    if (
        past_hold
        and bool(cfg.early_premium_trail_enabled)
        and running_mfe >= float(cfg.early_premium_trail_trigger_pct) - _EPS
        and pret_f >= float(cfg.early_premium_trail_min_current_pct) - _EPS
        and (running_mfe - pret_f) >= float(cfg.early_premium_trail_exit_pct) - _EPS
    ):
        _clear_ae_warning(state)
        return _decide(REASON_PREMIUM_TRAIL, DETAIL_EARLY_PREMIUM_TRAIL)

    # =====================================================================
    # STEP 21 | Premium trail, 100% guard (OFF) | Exit Reason: premium_trail / PATH_V2_PREMIUM_TRAIL
    # Knobs: OPTION_PATH_V2_PREMIUM_TRAIL_ENABLED (false), _TRIGGER (1.00), _EXIT (0.30), _MIN_SEC_HWM (900)
    # Rule: peak >= 100%, giveback >= 30pp, high >= 900s old.
    # =====================================================================
    if (
        past_hold
        and bool(cfg.premium_trail_enabled)
        and running_mfe >= float(cfg.premium_trail_trigger_pct) - _EPS
        and (running_mfe - pret_f) >= float(cfg.premium_trail_exit_pct) - _EPS
        and sec_since_hwm is not None
        and sec_since_hwm >= float(cfg.premium_trail_min_sec_since_hwm) - _EPS
    ):
        _clear_ae_warning(state)
        return _decide(REASON_PREMIUM_TRAIL, DETAIL_PREMIUM_TRAIL)

    # =====================================================================
    # STEP 22 | V3A shallow floor stop (SF) | Exit Reason: shallow_floor_stop / SHALLOW_FLOOR_5_10_18
    # Knobs: OPTION_PATH_V2_SHALLOW_FLOOR_STOP_ENABLED (true), _MIN_MFE (0.05), _MAX_MFE (0.10),
    #        _MIN_AGE_SECONDS (120), _PRET (-0.18), _MIN_PEAK_TICKS (2), _REQUIRE_FRESH_CROSS (true)
    # Rule: peak 5% up to 10% printed on >= 2 ticks, age >= 120s, bid <= -18%, and the bid
    #       was above -18% at/after 120s (S5). Sells at the bid ahead of immature_stress.
    # =====================================================================
    if (
        past_hold
        and bool(cfg.shallow_floor_stop_enabled)
        and running_mfe >= float(cfg.shallow_floor_min_mfe) - _EPS
        and running_mfe < float(cfg.shallow_floor_max_mfe) - _EPS
        and age >= float(cfg.shallow_floor_min_age_seconds) - _EPS
        and pret_f <= float(cfg.shallow_floor_pret) + _EPS
        and int(state.get("sf_peak_ticks") or 0) >= int(cfg.shallow_floor_min_peak_ticks)
        and (
            not bool(cfg.shallow_floor_require_fresh_cross)
            or bool(state.get("sf_seen_above_gate"))
        )
    ):
        _clear_ae_warning(state)
        return _decide(REASON_SHALLOW_FLOOR, DETAIL_SHALLOW_FLOOR)

    in_immature = past_hold and running_mfe < float(cfg.early_cut) - _EPS
    in_middle = past_hold and not in_immature and running_mfe < float(cfg.proof) - _EPS
    in_mature = past_hold and not in_immature and not in_middle and peak is not None and peak > _EPS

    # =====================================================================
    # STEP 23 | Immature stress + active-expansion warning | Exit Reason: immature_stress /
    #           IMMATURE_STRESS (hold detail AE_WARNING_HOLD)
    # Knobs: OPTION_PATH_V2_STRESS_THR (0.40), OPTION_PATH_V2_AE_WARNING_ENABLED (true),
    #        _AE_WARNING_WINDOW_SECONDS (90), _AE_WARNING_STALL_SECONDS (45),
    #        _AE_WARNING_MATERIAL_HWM_PP (0.02), _AE_WARNING_MAX_START_RETURN (0.00),
    #        OPTION_PATH_V2_AE_EXP_SPEED_MIN (0.0001), OPTION_PATH_V2_AE_SEC_HWM_MAX (120)
    # Rule (immature region, peak < 15%):
    #   19a live stress < 0.40 -> cancel any warning, HOLD.
    #   19b stress >= 0.40: record the first-fire shadow (expansion speed, high age).
    #   19c a new +2pp high since the warning started -> cancel the warning, HOLD (AE_WARNING_HOLD).
    #   19d active expansion (peak/age >= 0.0001 and high <= 120s old) and bid <= 0 ->
    #       warning HOLD for up to 90s; after 90s with the high >= 45s old -> EXIT immature_stress.
    #   19e otherwise -> EXIT immature_stress.
    # =====================================================================
    if in_immature:
        stress = live_stress_score(
            {
                "current_return": pret_f,
                "prem_mfe": running_mfe,
                "frac_retrace": frac,
                "prem_vel_10": vel,
                "fav_ul_retr": ul_retr,
                "fav_ul_dd": ul_dd,
                "trade_age_sec": age,
            }
        )
    stress_hot = in_immature and stress is not None and stress >= float(cfg.stress_threshold) - _EPS

    # 19a
    if in_immature and not stress_hot:
        _clear_ae_warning(state)

    # 19b
    ae_exp_speed: Optional[float] = None
    would_spare = False
    material_since_warning = False
    warning_active = False
    warning_start: Optional[float] = None
    if stress_hot:
        if age > 1e-9:
            ae_exp_speed = running_mfe / age
        would_spare = bool(
            (ae_exp_speed is not None and ae_exp_speed >= float(cfg.ae_exp_speed_min) - 1e-15)
            and (sec_since_hwm is not None and sec_since_hwm <= float(cfg.ae_sec_hwm_max) + _EPS)
        )
        if state.get("stress_fire_logged") is not True:
            state["stress_fire_logged"] = True
            state["ae_shadow_would_spare"] = would_spare
            state["ae_shadow_exp_speed"] = ae_exp_speed
            state["ae_shadow_sec_since_hwm"] = sec_since_hwm
        warning_active = bool(state.get("ae_warning_active"))
        warning_start = _finite(state.get("ae_warning_start_sse"))
        warning_peak = _finite(state.get("ae_warning_start_peak"))
        material_since_warning = bool(
            warning_active
            and warning_peak is not None
            and running_mfe >= warning_peak + float(cfg.ae_warning_material_hwm_pp) - _EPS
        )

    # 19c
    if stress_hot and material_since_warning:
        _clear_ae_warning(state)
        hold_detail = DETAIL_AE_WARNING

    # 19d
    ae_hold = (
        stress_hot
        and not material_since_warning
        and bool(cfg.ae_warning_enabled)
        and would_spare
        and pret_f <= float(cfg.ae_warning_max_start_return) + _EPS
    )
    if ae_hold:
        if not warning_active or warning_start is None:
            state["ae_warning_active"] = True
            state["ae_warning_start_sse"] = age
            state["ae_warning_start_peak"] = running_mfe
            warning_start = age
        warning_age = age - float(warning_start)
        if (
            warning_age >= float(cfg.ae_warning_window_seconds) - _EPS
            and sec_since_hwm is not None
            and sec_since_hwm >= float(cfg.ae_warning_stall_seconds) - _EPS
        ):
            _clear_ae_warning(state)
            return _decide(REASON_IMMATURE, DETAIL_IMMATURE)
        state["ae_warning_last_detail"] = DETAIL_AE_WARNING
        hold_detail = DETAIL_AE_WARNING

    # 19e
    if stress_hot and not material_since_warning and not ae_hold:
        _clear_ae_warning(state)
        return _decide(REASON_IMMATURE, DETAIL_IMMATURE)

    # =====================================================================
    # STEP 24 | 30-50% giveback | Exit Reason: gb30_giveback / GB30_24PP
    # Knobs: OPTION_PATH_V2_GB30_ENABLED (true), _MIN_MFE (0.30), _GIVEBACK (0.24)
    #        Gated extension: _EXTENSION_ENABLED, _EXTENSION_GIVEBACK (0.38),
    #        _EXTENSION_MAX_UL_DD (0.0009), _EXTENSION_MIN/MAX_PRET (0.05/0.10).
    # Rule (middle region, peak 15% up to 50%): cancel any warning; peak >= 30% and
    #       giveback >= 24pp -> EXIT. If the extension gate is true at the 24pp touch,
    #       latch extension mode and hold until giveback reaches 38pp or a later exit
    #       captures the trade.
    #       Peak 15% up to 30% is a HOLD.
    # =====================================================================
    if in_middle:
        _clear_ae_warning(state)
    gb30_giveback_now = running_mfe - pret_f
    gb30_extension_gate = (
        bool(cfg.gb30_extension_enabled)
        and ul_dd is not None
        and ul_dd <= float(cfg.gb30_extension_max_ul_dd) + _EPS
        and pret_f >= float(cfg.gb30_extension_min_pret) - _EPS
        and pret_f <= float(cfg.gb30_extension_max_pret) + _EPS
    )
    if (
        in_middle
        and bool(cfg.gb30_enabled)
        and running_mfe >= float(cfg.gb30_min_mfe) - _EPS
        and gb30_giveback_now >= float(cfg.gb30_giveback) - _EPS
    ):
        if gb30_extension_gate:
            state["gb30_extension_armed"] = True
        if (
            bool(state.get("gb30_extension_armed"))
            and gb30_giveback_now < float(cfg.gb30_extension_giveback) - _EPS
        ):
            return _decide(None, "GB30_38PP_EXTENSION_HOLD")
        return _decide(REASON_GB30, DETAIL_GB30)

    # =====================================================================
    # STEP 25 | Mature high-water retrace | Exit Reason: hwm_rtc / HWM_RTC
    # Knobs: OPTION_PATH_V2_RTC_RETRACE (0.20), OPTION_PATH_V2_RTC_MATERIAL_SEC (300),
    #        OPTION_PATH_V2_RTC_VEL (-0.0005)
    # Rule (mature region, peak >= 50%): cancel any warning. Retrace < 20% clears the
    #       velocity timer (HOLD). Retrace >= 20%, material high >= 300s old and 10s
    #       velocity <= -0.0005 -> EXIT. Velocity above that clears the timer (HOLD).
    # =====================================================================
    if in_mature:
        _clear_ae_warning(state)
        material_sse_m = _finite(state.get("material_sse"))
        material_age = (age - material_sse_m) if material_sse_m is not None else 0.0
        rtc_wait = frac < float(cfg.rtc_retrace) - _EPS or material_age < float(cfg.rtc_material_seconds) - _EPS
        if rtc_wait and frac < float(cfg.rtc_retrace) - _EPS:
            state["vel_bad_sse"] = None
        if not rtc_wait and not (vel <= float(cfg.rtc_vel_threshold)):
            state["vel_bad_sse"] = None
        if not rtc_wait and vel <= float(cfg.rtc_vel_threshold):
            if _finite(state.get("vel_bad_sse")) is None:
                state["vel_bad_sse"] = age
            vel_bad = _finite(state.get("vel_bad_sse")) or age
            if age - vel_bad >= -_EPS:
                return _decide(REASON_HWM_RTC, DETAIL_HWM_RTC)

    # =====================================================================
    # STEP 26 | 32-40% timed giveback (package B) | Exit Reason: gb32_timed_giveback / GB32_40_TIMED_15PP
    # Knobs: OPTION_PATH_V2_GB32_TIMED_ENABLED (true), _MIN_MFE (0.32), _MAX_MFE (0.40),
    #        _GIVEBACK (0.15), _MIN_SEC_HWM (120), _MIN_PRET (0.05)
    # Rule: runs on any tick that reached here (also held ticks). Peak 32% up to 40%,
    #       giveback >= 15pp, high >= 120s old, bid >= +5%.
    # =====================================================================
    if (
        bool(cfg.gb32_timed_enabled)
        and running_mfe >= float(cfg.gb32_timed_min_mfe) - _EPS
        and running_mfe < float(cfg.gb32_timed_max_mfe) - _EPS
        and (running_mfe - pret_f) >= float(cfg.gb32_timed_giveback) - _EPS
        and sec_since_hwm is not None
        and sec_since_hwm >= float(cfg.gb32_timed_min_sec_hwm) - _EPS
        and pret_f >= float(cfg.gb32_timed_min_pret) - _EPS
    ):
        _clear_ae_warning(state)
        return _decide(REASON_GB32_TIMED, DETAIL_GB32_TIMED)

    # =====================================================================
    # STEP 27 | Deep green stop (package B) | Exit Reason: deep_green_stop / DEEP_STOP_10_30_65
    # Knobs: OPTION_PATH_V2_DEEP_STOP_ENABLED (true), _MIN_AGE_SECONDS (60), _MIN_MFE (0.10),
    #        _MAX_MFE (0.30), _PRET (-0.65)
    # Rule: age >= 60s, peak 10% up to 30%, bid <= -65%. Not held by the 120s hold.
    # =====================================================================
    if (
        bool(cfg.deep_stop_enabled)
        and age >= float(cfg.deep_stop_min_age_seconds) - _EPS
        and running_mfe >= float(cfg.deep_stop_min_mfe) - _EPS
        and running_mfe < float(cfg.deep_stop_max_mfe) - _EPS
        and pret_f <= float(cfg.deep_stop_pret) + _EPS
    ):
        _clear_ae_warning(state)
        return _decide(REASON_DEEP_STOP, DETAIL_DEEP_STOP)

    # =====================================================================
    # STEP 28 | V3A stall floor stop (SBE v2-A) | Exit Reason: stall_floor_stop / STALL_FLOOR_15_40_4
    # Knobs: OPTION_PATH_V2_STALL_FLOOR_STOP_ENABLED (true), _MAX_SLIP (0.03),
    #        _CONFIRM_TICKS (2); eligibility and arming from S9
    # Rule: eligible and armed (S9), bid <= +4% on the 2nd consecutive tick, and bid still
    #       >= +1% (floor - slip). A deeper print does not fire; the base exits keep it.
    # =====================================================================
    if (
        sbf_armed
        and pret_f <= sbf_floor + _EPS
        and pret_f >= sbf_floor - float(cfg.stall_floor_max_slip) - _EPS
        and bool(state.get("sbf_seen_above_armed"))
        and int(state.get("sbf_below_n") or 0) >= int(cfg.stall_floor_confirm_ticks)
    ):
        _clear_ae_warning(state)
        return _decide(REASON_STALL_FLOOR, DETAIL_STALL_FLOOR)

    # =====================================================================
    # STEP 29 | ATR loss cut | Exit Reason: atr_loss_cut / ATR_LOSS_CUT_30_2ATR
    # Knobs: OPTION_PATH_V2_ATR_LOSS_CUT_ENABLED (true; dataclass default false), _MAX_PRET (-0.30),
    #        _ATR_MULT (2.0), _MAX_PEAK (0.10), _MIN_AGE_S (600), _REQUIRE_VWAP_AGAINST (true),
    #        _BAR_MAX_AGE_S (180); shared 120s checkpoint OPTION_PATH_V2_MIN_HOLD_SECONDS (120)
    # Rule: only when STEP 0-28 gave no exit and no runner extension is active. Fresh
    #       session-tech bar (session_tech_bar_at <= 180s; missing = no fire), age past the
    #       120s checkpoint and >= 600s, bid <= -30%, confirmed peak (S10) < +10%, side-aware UL
    #       move since entry <= -2.0 x atr, and (when required) side-aware vwap_distance_pct < 0.
    # =====================================================================
    tv_runner_active = _finite(state.get("runner_ext_l0")) is not None
    tv_cpk = _finite(state.get("tv_confirmed_peak"))
    tv_sign = 1.0 if side == "call" else (-1.0 if side == "put" else 0.0)
    tv_atr = _positive(osnap.get("atr")) if isinstance(osnap, dict) else None
    tv_vwd_raw = _finite(osnap.get("vwap_distance_pct")) if isinstance(osnap, dict) else None
    tv_svwd = tv_sign * tv_vwd_raw if tv_vwd_raw is not None and tv_sign != 0.0 else None
    tv_ul = price if price is not None else _positive(state.get("tv_last_ul"))
    if price is not None:
        state["tv_last_ul"] = price
    tv_base_ok = (
        tv_sign != 0.0
        and not tv_runner_active
        and tv_cpk is not None
        and trade_past_120s_checkpoint(age, cfg)
    )
    if (
        bool(cfg.atr_loss_cut_enabled)
        and tv_base_ok
        and age >= float(cfg.atr_loss_cut_min_age_s) - _EPS
        and _fresh_session_tech_bar(osnap, now_dt, cfg.atr_loss_cut_bar_max_age_s) is not None
        and tv_atr is not None
        and entry is not None
        and tv_ul is not None
        and pret_f <= float(cfg.atr_loss_cut_max_pret) + _EPS
        and tv_cpk < float(cfg.atr_loss_cut_max_peak)
        and tv_sign * (tv_ul - entry) / tv_atr <= -float(cfg.atr_loss_cut_atr_mult) + _EPS
        and (
            not bool(cfg.atr_loss_cut_require_vwap_against)
            or (tv_svwd is not None and tv_svwd < 0.0)
        )
    ):
        _clear_ae_warning(state)
        return _decide(REASON_ATR_LOSS_CUT, DETAIL_ATR_LOSS_CUT)

    # =====================================================================
    # STEP 30 | VWAP cross exit | Exit Reason: vwap_cross_exit / VWAP_CROSS_50_025ATR
    # Knobs: OPTION_PATH_V2_VWAP_CROSS_EXIT_ENABLED (true; dataclass default false), _MIN_PEAK (0.50),
    #        _ATR_BUFFER (0.25), _CONFIRM_BARS (1), _MIN_PRET (0.10), _BAR_MAX_AGE_S (180);
    #        age gate = shared 120s checkpoint OPTION_PATH_V2_MIN_HOLD_SECONDS (120, STEP 16)
    # Rule: same gates as STEP 29 (no exit above, no runner extension, past 120s, fresh bar).
    #       Side-aware vwap_distance_pct > 0 on a fresh bar marks "was on the right side".
    #       Armed once that was seen and the confirmed peak (S10) >= +50%. Below = side-aware
    #       distance <= -(0.25 x atr / UL x 100)%. Count distinct bars (session_tech_bar_at)
    #       that are below while armed (reset otherwise); exit at >= 1 bar with bid >= +10%.
    # =====================================================================
    tv_vw_bar = (
        _fresh_session_tech_bar(osnap, now_dt, cfg.vwap_cross_bar_max_age_s)
        if bool(cfg.vwap_cross_exit_enabled) and tv_base_ok
        else None
    )
    if tv_vw_bar is not None and tv_svwd is not None and tv_atr is not None and tv_ul is not None:
        if tv_svwd > 0.0:
            state["tv_vw_seen_above"] = True
        tv_buf = float(cfg.vwap_cross_atr_buffer) * tv_atr / tv_ul * 100.0
        tv_armed = bool(state.get("tv_vw_seen_above")) and tv_cpk >= float(cfg.vwap_cross_min_peak) - _EPS
        tv_below = tv_svwd <= -tv_buf + _EPS
        if tv_armed and tv_below:
            tv_bar_key = tv_vw_bar.isoformat()
            if state.get("tv_vw_below_bar") != tv_bar_key:
                state["tv_vw_below_n"] = int(state.get("tv_vw_below_n") or 0) + 1
                state["tv_vw_below_bar"] = tv_bar_key
        else:
            state["tv_vw_below_n"] = 0
        if (
            tv_armed
            and tv_below
            and int(state.get("tv_vw_below_n") or 0) >= int(cfg.vwap_cross_confirm_bars)
            and pret_f >= float(cfg.vwap_cross_min_pret) - _EPS
        ):
            _clear_ae_warning(state)
            return _decide(REASON_VWAP_CROSS_EXIT, DETAIL_VWAP_CROSS_EXIT)

    # =====================================================================
    # END | No exit this tick | Exit Reason: none (hold; detail AE_WARNING_HOLD when the
    #       active-expansion warning is holding, else "")
    # =====================================================================
    return _decide(None, hold_detail)
