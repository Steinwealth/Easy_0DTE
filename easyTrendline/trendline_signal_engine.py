#!/usr/bin/env python3
"""
Main orchestration engine for Easy Trendline 0DTE signal lifecycle.

FINAL TRENDLINE FLOW:

1. Selector builds trendline (structure-first)
2. Engine monitors price action
3. Break detected
4. Survival window allows structure to form
5. Retest OR continuation triggers entry
6. Immediate break only for strong impulse
7. Execution → stealth management
"""

from __future__ import annotations

from dataclasses import replace
import logging
import math
import time as time_module
from statistics import mean
from datetime import datetime, timedelta, time, timezone
from typing import Any, Dict, Iterable, List, NamedTuple, Optional
from zoneinfo import ZoneInfo


from .trendline_builder import OrbTestFailureClassification, TrendlineBuilder
from .trendline_intent_adapter import build_execution_intent_from_trendline_signal
from .trendline_simple_mode import (
    SimpleTrendlineProcessor,
    SimpleTrendlineState,
    get_simple_state,
    set_simple_state,
)
from .trendline_models import (
    BreakStatus,
    MomentumStatus,
    OHLCVBar,
    TrendlineCandidate,
    TrendlineCandidateState,
    TrendlineConfig,
    TrendlineBreakEvent,
    TrendlineDefinition,
    TrendlineDirection,
    TrendlineMomentumConfirmation,
    TrendlineReasonCode,
    TrendlineSetupType,
    TrendlineTradeSignal,
    make_trendline_candidate_key,
    trendline_slot_from_payload,
    trendline_slot_short_label,
)


# Minimal internal knobs (prefer over new env scatter for archetype tuning).
_BREAK_ARCHETYPE_EXHAUSTION_BD_MULT = 0.26
_BREAK_ARCHETYPE_MICRO_FLOOR_SCALE = 2.6
_REANCHOR_NEAR_LINE_FREEZE_FRAC = 0.00048
_REANCHOR_ERASURE_SHIFT_FRAC = 0.0001
_DELAYED_CONTINUATION_TTL_MIN = 12.0
_DELAYED_CONTINUATION_MAX_BARS = 8
_DELAYED_EXTENSION_REL = 6.5e-5
_DELAYED_EXPECTED_MOVE_BOOST_MULT = 1.08
_STRUCTURE_FRAGILITY_TOUCH_COUNT = 8

# Canonical entry archetypes (stored on break_event.metadata["break_archetype"]).
ENTRY_ARCHETYPE_IMPULSE = "IMPULSE_BREAK"
ENTRY_ARCHETYPE_DRIFT = "CONTINUATION_DRIFT"
ENTRY_ARCHETYPE_EXHAUSTION = "EXHAUSTION_REVERSAL"

log = logging.getLogger(__name__)


class TrendlineSignalEngine:
    """
    State-machine driven trendline signal engine.

    Lifecycle:
      WAITING_FOR_BUILD -> WAITING_FOR_BREAK -> WAITING_FOR_CONFIRMATION
      -> READY_TO_EXECUTE -> EXECUTED / INVALIDATED / EXPIRED
    """

    def __init__(self, config: Optional[TrendlineConfig] = None) -> None:
        self.config = config or TrendlineConfig()
        self.builder = TrendlineBuilder(self.config)
        self._tz_pt = ZoneInfo("America/Los_Angeles")
        self._session_730_cutoff_utc: Optional[datetime] = None

        self._candidates: Dict[str, TrendlineCandidate] = {}
        self._bar_cache: Dict[str, List[OHLCVBar]] = {}
        self._watch_last_session_volume: Dict[str, float] = {}
        self._emitted_candidate_ids: set[str] = set()
        self._confirm_pending_last_log_ts: Dict[str, float] = {}

        self.on_first_break_snapshot = None
        self.on_priority_dataset_capture = None
        self._simple_processor: Optional[SimpleTrendlineProcessor] = SimpleTrendlineProcessor(
            self.config,
            on_dataset_capture=self._handle_simple_priority_dataset_capture,
            on_first_break_snapshot=self._handle_simple_first_break_snapshot,
            trade_id_fn=self._trade_id,
            emitted_ids=self._emitted_candidate_ids,
        )
        self._session_tech_system = None

        self._session_summary_logged = False
        self._decision_stats: Dict[str, int] = {
            "break_detected": 0,
            "entry_emitted": 0,
            "filtered_small_break": 0,
            "filtered_weak_break": 0,
            "filtered_anti_chop": 0,
            "missed_opportunity": 0,
            "executed": 0,
        }
        self._entry_tracking: Dict[str, Dict[str, float]] = {}
        self._market_regime = "TRENDING"
        self._market_regime_metrics: Dict[str, float] = {}
        self._regime_choppy_overlap_min = 0.55
        self._regime_trend_directional_min = 0.58
        self._regime_min_avg_range_pct = 0.0008

        self._continuation_max_bars = 6
        self._emit_skip_watch: Dict[str, Dict[str, Any]] = {}
        self._post_break_no_emit_diag_ts: Dict[str, float] = {}
        self._consecutive_skips_session = 0
        self._skip_streak_alert_armed = True

        self._funnel_counts: Dict[str, int] = {
            "candidates_seen": 0,
            "break_detected": 0,
            "structure_accepted": 0,
            "pre_execute_passed": 0,
            "selector_reached": 0,
            "submitted": 0,
            "durable_confirmed": 0,
        }
        self._funnel_last_log_ts = 0.0
        self._funnel_interval_sec = max(
            60.0, float(getattr(self.config, "trendline_funnel_summary_interval_sec", 180.0))
        )

        self._max_active_minutes = max(1.0, float(self.config.max_active_minutes))
        self._bad_entry_max_favorable_pct = 0.03
        self._bad_entry_drawdown_pct = 0.05
        self._missed_win_move_pct = 0.015

        self._log_trendline_config_summary()

    def bind_session_tech_system(self, system: Any) -> None:
        """Attach trading system so simple quality uses shared session-tech hub."""
        self._session_tech_system = system
        proc = getattr(self, "_simple_processor", None)
        if proc is not None and hasattr(proc, "bind_session_tech_system"):
            proc.bind_session_tech_system(system)

    def _is_simple_break_mode(self) -> bool:
        return True

    def _log_trendline_config_summary(self) -> None:
        c = self.config
        log.warning(
            "TRENDLINE_CONFIG_SUMMARY | path=simple_break_mode | simple_min_score=%.2f | "
            "simple_min_break_pct=%.6f | simple_entry_window_sec=%.1f | control_line_v3=%s | "
            "primary_lane_enforce=%s | orb_bias_mse_soft_ratio=%.2f",
            float(c.simple_min_score),
            float(c.simple_min_break_distance_pct),
            float(c.simple_entry_window_sec),
            str(bool(c.control_line_v3_enabled)).lower(),
            str(bool(c.primary_lane_enforce_geometry_alignment)).lower(),
            float(c.orb_bias_mse_soft_ratio),
        )

    def _trade_id(self, candidate: Optional[TrendlineCandidate]) -> str:
        if not candidate:
            return "unknown_na"
        ts = None
        if candidate.break_event and candidate.break_event.candle_ts:
            ts = self._normalize_dt(candidate.break_event.candle_ts).strftime("%Y%m%dT%H%M%S")
        if not ts:
            ts = "na"
        try:
            slot_label = trendline_slot_short_label(
                (candidate.setup_payload or {}).get("trendline_slot")
            )
        except Exception:
            slot_label = "TL1"
        return f"{candidate.symbol}_{slot_label}_{ts}"

    @property
    def candidates(self) -> Dict[str, TrendlineCandidate]:
        return self._candidates

    def _log_trendline_flow_stage(self, candidate: TrendlineCandidate, stage: str) -> None:
        log.info(
            "TRENDLINE_FLOW_STAGE | symbol=%s | trade_id=%s | stage=%s",
            candidate.symbol,
            self._trade_id(candidate),
            stage,
        )

    def _in_survival_window(self, candidate: TrendlineCandidate, current_bar_index: int) -> bool:
        bbi = getattr(candidate, "break_bar_index", None)
        if bbi is None:
            return False
        w = int(self._post_break_survival_bars)
        if w <= 0:
            return False
        return (int(current_bar_index) - int(bbi)) < w

    def _in_post_break_survival_window(self, candidate: TrendlineCandidate, bars: List[OHLCVBar]) -> bool:
        if not bars:
            return False
        return self._in_survival_window(candidate, max(0, len(bars) - 1))

    def _survival_blocks_negative_verdict(self, candidate: TrendlineCandidate) -> bool:
        fb = self._bar_cache.get(candidate.symbol, [])
        if not fb:
            return False
        cur_idx = max(0, len(fb) - 1)
        if self._in_survival_window(candidate, cur_idx):
            self._log_trendline_flow_stage(candidate, "survival_window_blocked_invalidation")
            return True
        return False

    def _infer_entry_path(self, candidate: TrendlineCandidate) -> str:
        sr = str(candidate.state_reason or "").lower()
        meta = (candidate.break_event.metadata or {}) if candidate.break_event else {}
        if meta.get("retest_mode") or "retest" in sr:
            return "retest"
        if "continuation" in sr:
            return "continuation"
        return "immediate_break"

    def _compute_break_quality_metrics(
        self,
        candidate: TrendlineCandidate,
        bars: List[OHLCVBar],
        *,
        post_break: Optional[List[OHLCVBar]] = None,
        distance_hint: Optional[bool] = None,
    ) -> Dict[str, Any]:
        if not candidate.break_event or not bars:
            return {
                "break_distance_pct": 0.0, "body_ratio_break": 0.0, "close_position_ratio": None,
                "range_expansion_ratio": 0.0, "direction_commitment_score": 0.0, "expected_move_pct": 0.0,
                "distance_increasing": False, "strong_close": False, "weak_reversal_risk": False,
            }
        br, bd = self._break_body_ratio_vs_prev(candidate, bars)
        br_avg, _ = self._break_bar_body_ratio(candidate, bars)
        close_pos = self._break_bar_close_position_in_candle(candidate, bars)
        ts_n = self._normalize_dt(candidate.break_event.candle_ts)
        bbar = next((b for b in reversed(bars) if self._normalize_dt(b.ts) == ts_n), bars[-1])
        c_range = abs(float(bbar.high)-float(bbar.low))
        sample = bars[-4:-1] if len(bars) >= 4 else bars[:-1]
        avg_range = (sum(abs(float(x.high)-float(x.low)) for x in sample)/max(1,len(sample))) if sample else 0.0
        range_exp = (c_range/avg_range) if avg_range>0 else 1.0
        pb = post_break if post_break is not None else [b for b in bars if self._normalize_dt(b.ts) >= ts_n]
        expected = self._compute_expected_move_pct(candidate, pb) if pb else 0.0
        dist_inc = bool(distance_hint) if distance_hint is not None else bool((candidate.break_event.metadata or {}).get('distance_increasing'))
        if len(bars)>=2 and candidate.trendline is not None:
            prev=bars[-2]; cur=bars[-1]
            lp=float(candidate.trendline.value_at(prev.ts)); lc=float(candidate.trendline.value_at(cur.ts))
            if abs(lp)>1e-9 and abs(lc)>1e-9:
                dp=abs(float(prev.close)-lp)/abs(lp); dc=abs(float(cur.close)-lc)/abs(lc)
                dist_inc = dc>dp
        commit = (close_pos if close_pos is not None else 0.0) if candidate.direction == TrendlineDirection.BEAR else ((1.0-close_pos) if close_pos is not None else 0.0)
        strong_close = bool(commit >= 0.70)
        weak_reversal = self._has_large_opposite_candle(candidate, self._bars_for_break_event_window(candidate, bars))
        metrics = {
            "break_distance_pct": float(bd),
            "body_ratio_break": float(br),
            "body_ratio_vs_prev": float(br),
            "body_ratio_vs_avg": float(br_avg),
            "close_position_ratio": float(close_pos) if close_pos is not None else None,
            "range_expansion_ratio": float(range_exp),
            "direction_commitment_score": float(max(0.0, min(1.0, commit))),
            "expected_move_pct": float(expected),
            "distance_increasing": bool(dist_inc),
            "strong_close": bool(strong_close),
            "weak_reversal_risk": bool(weak_reversal),
        }
        log.info(
            "TRENDLINE_BREAK_QUALITY_METRICS | symbol=%s | trade_id=%s | break_distance_pct=%.6f | body_ratio_break=%.3f | close_position_ratio=%s | range_expansion_ratio=%.3f | direction_commitment_score=%.3f | expected_move_pct=%.6f | distance_increasing=%s | strong_close=%s | weak_reversal_risk=%s",
            candidate.symbol,
            self._trade_id(candidate),
            float(metrics["break_distance_pct"]),
            float(metrics["body_ratio_break"]),
            f"{float(metrics['close_position_ratio']):.3f}" if metrics["close_position_ratio"] is not None else "none",
            float(metrics["range_expansion_ratio"]),
            float(metrics["direction_commitment_score"]),
            float(metrics["expected_move_pct"]),
            str(bool(metrics["distance_increasing"])).lower(),
            str(bool(metrics["strong_close"])).lower(),
            str(bool(metrics["weak_reversal_risk"])).lower(),
        )
        return metrics

    def _is_slow_trend_acceptance(self, candidate: TrendlineCandidate, post_break: List[OHLCVBar]) -> tuple[bool, Dict[str, Any]]:
        if len(post_break) < 3 or candidate.trendline is None:
            return False, {}
        look = post_break[-min(6, len(post_break)):]
        directional = 0
        overlap_hits = 0
        for i in range(1, len(look)):
            d = float(look[i].close) - float(look[i-1].close)
            if (candidate.direction == TrendlineDirection.BEAR and d > 0) or (candidate.direction == TrendlineDirection.BULL and d < 0):
                directional += 1
            if max(float(look[i].high), float(look[i-1].high)) > min(float(look[i].low), float(look[i-1].low)):
                overlap_hits += 1
        consistency = directional / max(1, len(look)-1)
        start = float(look[0].close); end = float(look[-1].close)
        disp = abs(end-start) / max(abs(start), 1e-9)
        ok = consistency >= max(0.60, float(self._slow_trend_consistency_min)-0.07) and disp >= float(self._min_expected_move_pct_default) and overlap_hits <= len(look)-1
        return ok, {"consistency": consistency, "cumulative_displacement": disp, "overlap_hits": overlap_hits, "bars": len(look)}

    def _has_structural_acceptance(self, candidate: TrendlineCandidate, post_break: List[OHLCVBar]) -> tuple[bool, str]:
        if len(post_break) < 3 or candidate.break_event is None:
            return False, "insufficient_post_break_bars"
        break_level = float(candidate.break_event.close_price or 0.0)
        if break_level <= 0:
            return False, "invalid_break_level"
        closes = [float(b.close) for b in post_break[-6:]]
        beyond = 0
        reclaim = 0
        for c in closes:
            if candidate.direction == TrendlineDirection.BEAR:
                beyond += 1 if c > break_level else 0
                reclaim += 1 if c <= break_level else 0
            else:
                beyond += 1 if c < break_level else 0
                reclaim += 1 if c >= break_level else 0
        if beyond < int(self._acceptance_min_beyond_closes):
            return False, "acceptance_beyond_line_insufficient"
        if reclaim > int(self._acceptance_max_reclaims):
            return False, "repeated_reclaim_failures"
        slow_ok, slow_meta = self._is_slow_trend_acceptance(candidate, post_break)
        if slow_ok:
            log.info("TRENDLINE_SLOW_TREND_ACCEPT | symbol=%s | trade_id=%s | consistency=%.3f | displacement=%.6f | overlap_hits=%d", candidate.symbol, self._trade_id(candidate), float(slow_meta.get('consistency',0.0)), float(slow_meta.get('cumulative_displacement',0.0)), int(slow_meta.get('overlap_hits',0)))
            return True, "slow_trend_acceptance"
        log.info("TRENDLINE_SLOW_TREND_REJECT | symbol=%s | trade_id=%s | reason=slow_structure_not_directional", candidate.symbol, self._trade_id(candidate))
        return True, "multi_close_acceptance"

    def _break_body_ratio_vs_prev(self, candidate: TrendlineCandidate, bars: List[OHLCVBar]) -> tuple[float, float]:
        """Body ratio vs prior bar (detector-aligned). Used for break qualification and impulse gates."""
        if not candidate.break_event:
            return 0.0, 0.0
        meta = candidate.break_event.metadata or {}
        if meta.get("body_ratio_vs_prev") is not None:
            return float(meta["body_ratio_vs_prev"]), float(candidate.break_event.break_distance_pct or 0.0)
        if meta.get("body_ratio") is not None:
            return float(meta["body_ratio"]), float(candidate.break_event.break_distance_pct or 0.0)
        ts_n = self._normalize_dt(candidate.break_event.candle_ts)
        idx = -1
        for i in range(len(bars) - 1, -1, -1):
            if self._normalize_dt(bars[i].ts) == ts_n:
                idx = i
                break
        if idx <= 0:
            return 0.0, float(candidate.break_event.break_distance_pct or 0.0)
        bbar = bars[idx]
        prev = bars[idx - 1]
        body = abs(float(bbar.close) - float(bbar.open))
        prev_body = abs(float(prev.close) - float(prev.open))
        ratio = (body / max(prev_body, 1e-6)) if prev_body > 0 else 0.0
        return float(ratio), float(candidate.break_event.break_distance_pct or 0.0)

    def _persist_break_body_ratios(
        self,
        event: TrendlineBreakEvent,
        *,
        body_ratio_vs_avg: float,
        prev_bar: Optional[OHLCVBar],
        break_bar: OHLCVBar,
    ) -> None:
        if event.metadata is None:
            event.metadata = {}
        br_prev = float((event.metadata or {}).get("body_ratio_vs_prev") or (event.metadata or {}).get("body_ratio") or 0.0)
        if br_prev <= 0.0 and prev_bar is not None:
            body = abs(float(break_bar.close) - float(break_bar.open))
            prev_body = abs(float(prev_bar.close) - float(prev_bar.open))
            br_prev = (body / max(prev_body, 1e-6)) if prev_body > 0 else 0.0
        event.metadata["body_ratio_vs_prev"] = float(br_prev)
        event.metadata["body_ratio_vs_avg"] = float(body_ratio_vs_avg)
        event.metadata["body_ratio"] = float(br_prev)

    def _is_impulse_fast_emit_entry(self, candidate: TrendlineCandidate, break_meta: Dict[str, Any]) -> bool:
        if (
            self._counter_orb_bool("block_impulse_fast_path", True)
            and self._is_below_orb_countertrend_call(candidate)
        ):
            return False
        if (
            self._counter_orb_bool("block_impulse_fast_path", True)
            and self._is_above_orb_countertrend_put(candidate)
        ):
            return False
        sr = str(candidate.state_reason or "").lower()
        return bool(
            break_meta.get("impulse_mode")
            or break_meta.get("early_entry_mode")
            or "immediate_break" in sr
            or "first_move" in sr
            or str(break_meta.get("trendline_mode") or "").upper() in {"IMPULSE", "EARLY_ENTRY"}
        )

    def _count_beyond_line_closes(self, candidate: TrendlineCandidate, post_break: List[OHLCVBar]) -> int:
        if not candidate.break_event or not post_break:
            return 0
        break_level = float(candidate.break_event.close_price or 0.0)
        if break_level <= 0:
            return 0
        beyond = 0
        for b in post_break[-6:]:
            c = float(b.close)
            if candidate.direction == TrendlineDirection.BEAR:
                beyond += 1 if c > break_level else 0
            else:
                beyond += 1 if c < break_level else 0
        return beyond

    def _line_separation_persistent(self, candidate: TrendlineCandidate, post_break: List[OHLCVBar]) -> tuple[bool, float]:
        if not post_break or candidate.trendline is None:
            return False, 0.0
        last = post_break[-1]
        line_px = float(candidate.trendline.value_at(last.ts))
        if line_px <= 1e-9:
            return False, 0.0
        dist_pct = abs(float(last.close) - line_px) / abs(line_px)
        min_sep = max(float(self.config.break_distance_min) * 0.35, 0.0004)
        return dist_pct >= min_sep, float(dist_pct)

    def _post_break_acceptance_met(
        self,
        candidate: TrendlineCandidate,
        post_break: List[OHLCVBar],
        *,
        continuation_dist: float = 0.0,
        reclaimed: bool = False,
    ) -> tuple[bool, str, Dict[str, Any]]:
        struct_ok, struct_reason = self._has_structural_acceptance(candidate, post_break)
        slow_ok, slow_meta = self._is_slow_trend_acceptance(candidate, post_break)
        beyond = self._count_beyond_line_closes(candidate, post_break)
        sep_ok, sep_pct = self._line_separation_persistent(candidate, post_break)
        min_cont = self._effective_min_continuation_distance_pct(candidate)
        cont_ok = float(continuation_dist) >= min_cont and not reclaimed
        meta: Dict[str, Any] = {
            "structural_acceptance": bool(struct_ok),
            "slow_trend_acceptance": bool(slow_ok),
            "continuation_dist": float(continuation_dist),
            "reclaimed": bool(reclaimed),
            "beyond_line_closes": int(beyond),
            "line_separation_pct": float(sep_pct),
            "line_separation_ok": bool(sep_ok),
            "min_continuation_distance_pct": float(min_cont),
        }
        meta.update(slow_meta)
        if struct_ok:
            return True, str(struct_reason), meta
        if slow_ok:
            return True, "slow_trend_acceptance", meta
        if cont_ok:
            if (
                self._counter_orb_bool("disallow_continuation_only_acceptance", True)
                and self._is_below_orb_countertrend_call(candidate)
                and not struct_ok
                and not slow_ok
            ):
                return False, "below_orb_requires_structural_acceptance", meta
            if (
                self._counter_orb_bool("disallow_continuation_only_acceptance", True)
                and self._is_above_orb_countertrend_put(candidate)
                and not struct_ok
                and not slow_ok
            ):
                return False, "above_orb_requires_structural_acceptance", meta
            return True, "continuation_dist_no_reclaim", meta
        if beyond >= int(self._acceptance_min_beyond_closes) and not reclaimed and sep_ok:
            return True, "beyond_line_closes_separation", meta
        return False, "post_break_acceptance_missing", meta

    def _log_post_break_acceptance(
        self,
        candidate: TrendlineCandidate,
        *,
        accepted: bool,
        via: str,
        meta: Dict[str, Any],
        expansion_state: str = "",
    ) -> None:
        log.warning(
            "TRENDLINE_POST_BREAK_ACCEPTANCE | symbol=%s | trade_id=%s | accepted=%s | via=%s | "
            "structural_acceptance=%s | slow_trend_acceptance=%s | continuation_dist=%.6f | reclaimed=%s | "
            "beyond_line_closes=%d | line_separation_pct=%.6f | expansion_quality_state=%s | persistence_state=%s",
            candidate.symbol,
            self._trade_id(candidate),
            str(bool(accepted)).lower(),
            via or "none",
            str(bool(meta.get("structural_acceptance"))).lower(),
            str(bool(meta.get("slow_trend_acceptance"))).lower(),
            float(meta.get("continuation_dist", 0.0)),
            str(bool(meta.get("reclaimed"))).lower(),
            int(meta.get("beyond_line_closes", 0)),
            float(meta.get("line_separation_pct", 0.0)),
            expansion_state or "n/a",
            via or "pending",
        )

    def _momentum_persistence_agrees(self, candidate: TrendlineCandidate, post_break: List[OHLCVBar]) -> bool:
        slow_ok, _ = self._is_slow_trend_acceptance(candidate, post_break)
        if slow_ok:
            return True
        opt = self._expected_option_side_for_candidate(candidate)
        seg = post_break[-4:] if len(post_break) >= 4 else post_break
        if len(seg) < 2:
            return True
        directional = 0
        pairs = 0
        for i in range(1, len(seg)):
            pairs += 1
            c0 = float(seg[i - 1].close)
            c1 = float(seg[i].close)
            if opt == "PUT":
                if c1 < c0:
                    directional += 1
            else:
                if c1 > c0:
                    directional += 1
        required = 2 if pairs >= 2 else 1
        return directional >= required

    def _maybe_reanchor_candidate(self, candidate: TrendlineCandidate, bars: List[OHLCVBar], now: datetime) -> None:
        if not self._reanchor_enabled or candidate.state != TrendlineCandidateState.WAITING_FOR_BREAK:
            return
        if getattr(candidate, "reanchor_attempted", False):
            return
        started = self._normalize_dt(getattr(candidate, "start_time", now))
        elapsed = max(0.0, (self._normalize_dt(now) - started).total_seconds() / 60.0)
        if elapsed < float(self._reanchor_minutes):
            return
        if not bars:
            return
        last = bars[-1]
        near = self._near_price_to_line_frac(candidate, last)
        touch_ct = int(getattr(candidate, "touch_count", 0) or 0)
        fragile = touch_ct >= int(_STRUCTURE_FRAGILITY_TOUCH_COUNT)
        if fragile:
            log.warning(
                "TRENDLINE_STRUCTURE_FRAGILITY_ACTIVE | symbol=%s | trade_id=%s | touch_count=%d | threshold=%d",
                candidate.symbol,
                self._trade_id(candidate),
                touch_ct,
                int(_STRUCTURE_FRAGILITY_TOUCH_COUNT),
            )
        if near is not None and (
            float(near) < float(_REANCHOR_NEAR_LINE_FREEZE_FRAC) or fragile
        ):
            log.warning(
                "TRENDLINE_REANCHOR_FREEZE | symbol=%s | trade_id=%s | line_proximity_frac=%s | touch_count=%d "
                "| fragile=%s",
                candidate.symbol,
                self._trade_id(candidate),
                "none" if near is None else f"{float(near):.8f}",
                touch_ct,
                str(bool(fragile)).lower(),
            )
            candidate.reanchor_attempted = True
            return
        log.info(
            "TRENDLINE_REANCHOR_BUILD | symbol=%s | trade_id=%s | elapsed_minutes=%.2f | min_touches=%d",
            candidate.symbol,
            self._trade_id(candidate),
            elapsed,
            int(self._reanchor_min_touches),
        )
        rebuilt = self.builder.build_reanchored_from_recent_bars(
            candidate.symbol,
            candidate.direction,
            bars[-min(len(bars), int(self._reanchor_lookback_bars)) :],
            min_touches=int(self._reanchor_min_touches),
        )
        if rebuilt is None:
            candidate.reanchor_attempted = True
            log.info(
                "TRENDLINE_REANCHOR_REJECTED | symbol=%s | trade_id=%s | reason=insufficient_structure_quality",
                candidate.symbol,
                self._trade_id(candidate),
            )
            return
        if self._reanchor_break_geometry_would_erase(candidate, bars, rebuilt):
            candidate.reanchor_attempted = True
            log.warning(
                "TRENDLINE_REANCHOR_REJECT_BREAK_ERASURE | symbol=%s | trade_id=%s | reason=recent_break_context_preserved",
                candidate.symbol,
                self._trade_id(candidate),
            )
            return
        candidate.reanchor_attempted = True
        candidate.trendline = rebuilt
        candidate.reanchor_anchor_count = int((rebuilt.metadata or {}).get("touch_count", 0))
        setattr(candidate, "trendline_line_source", "reanchor")
        setattr(candidate, "structure_maturity_bar_index", max(0, len(bars) - 1))
        log.warning(
            "TRENDLINE_REANCHOR_ACCEPTED | symbol=%s | trade_id=%s | touch_count=%d | anchor_spacing_min=%s",
            candidate.symbol,
            self._trade_id(candidate),
            int(candidate.reanchor_anchor_count),
            str((rebuilt.metadata or {}).get("anchor_spacing_min", "none")),
        )

    def _maybe_log_missed_win_early(self, candidate: TrendlineCandidate, bar: OHLCVBar) -> None:
        st = getattr(candidate, "skip_timestamp", None)
        sp = getattr(candidate, "skip_price", None)
        if st is None or sp is None or float(sp) <= 0:
            return
        if getattr(candidate, "missed_win_early_logged", False):
            return
        now_ts = self._normalize_dt(bar.ts)
        dt_min = (now_ts - self._normalize_dt(st)).total_seconds() / 60.0
        if dt_min > 15.0:
            return
        close = float(bar.close)
        want_up = candidate.direction == TrendlineDirection.BEAR
        move = (close - float(sp)) / float(sp) if want_up else (float(sp) - close) / float(sp)
        if move >= 0.004:
            log.info(
                "TRENDLINE_MISSED_WIN_EARLY | symbol=%s | trade_id=%s | skip_ts=%s | minutes_since_skip=%.2f | "
                "move_pct=%.4f | expected_direction=%s",
                candidate.symbol,
                self._trade_id(candidate),
                self._normalize_dt(st).isoformat(),
                dt_min,
                float(move) * 100.0,
                "up" if want_up else "down",
            )
            candidate.missed_win_early_logged = True

    def _warn_confirm_pending(self, candidate: TrendlineCandidate, phase: str, detail: str) -> None:
        """Sampled WARNING so Cloud Logging shows break registered but entry gates not yet satisfied."""
        key = candidate.candidate_id or candidate.symbol
        now = time_module.time()
        if now - float(self._confirm_pending_last_log_ts.get(key, 0.0)) < float(
            self._confirm_pending_log_sec
        ):
            return
        self._confirm_pending_last_log_ts[key] = now
        log.warning(
            "TRENDLINE_PIPELINE | stage=confirm_pending | symbol=%s | trade_id=%s | geometry=%s | phase=%s | %s",
            candidate.symbol,
            self._trade_id(candidate),
            candidate.direction.value,
            phase,
            detail,
        )

    def _rearm_for_next_break(self, candidate: TrendlineCandidate, reason: str) -> None:
        """
        Reset break-specific state so symbol can keep watching for a fresh break.

        Used when a specific break attempt is invalidated, but the symbol should remain
        eligible for a later profitable setup.
        """
        candidate.break_event = None
        candidate.momentum_confirmation = None
        candidate.has_broken_once = False
        candidate.break_attempt_count = 0
        candidate.hold_success_at = None
        candidate.continuation_break_at = None
        candidate.local_continuation_level = None
        candidate.hold_bars_achieved = 0
        candidate.hold_end_bar_index = 0
        candidate.last_hold_duration_seconds = 0.0
        candidate.continuation_pending = False
        candidate.continuation_start_index = 0
        candidate.continuation_reason = ""
        candidate.continuation_max_bars = int(self._continuation_max_bars)
        candidate.rearm_pending = False
        candidate.rearm_origin_reason = ""
        candidate.rearm_started_at = None
        candidate.rearm_checks_done = 0
        candidate.rearm_break_level = None
        candidate.rearm_break_candle_low = None
        candidate.rearm_break_candle_high = None
        candidate.retest_pending = False
        candidate.retest_started_at = None
        candidate.retest_break_level = None
        candidate.retest_direction = ""
        candidate.retest_checks_done = 0
        candidate.mfe_window_logged = {}
        candidate.impulse_pending_confirmation = False
        candidate.impulse_break_high = None
        candidate.impulse_break_low = None
        candidate.break_bar_index = None
        candidate.break_timestamp = None
        candidate.decision_logged = False
        candidate.skip_timestamp = None
        candidate.skip_price = None
        candidate.missed_win_early_logged = False
        self._clear_delayed_continuation_flags(candidate)
        self._set_state(candidate, TrendlineCandidateState.WAITING_FOR_BREAK, reason)

    @staticmethod
    def _clear_delayed_continuation_flags(candidate: TrendlineCandidate) -> None:
        candidate.delayed_continuation_armed = False
        candidate.delayed_continuation_origin = ""
        candidate.delayed_continuation_started_at = None
        candidate.delayed_continuation_deadline_ts = None
        candidate.delayed_continuation_bars_seen = 0

    def _compute_market_regime(
        self,
        intraday_by_symbol: Dict[str, List[OHLCVBar]],
    ) -> tuple[str, Dict[str, float]]:
        sym_metrics: List[Dict[str, float]] = []
        from modules.session_clock import orb2_capture_window_pt

        start_t, end_t = orb2_capture_window_pt()
        for _, bars in intraday_by_symbol.items():
            if not bars or len(bars) < 4:
                continue
            window = []
            for b in bars:
                ts_pt = self._normalize_dt(b.ts).astimezone(self._tz_pt)
                if start_t <= ts_pt.time() < end_t:
                    window.append(b)
            if len(window) < 4:
                continue
            ranges = []
            overlaps = []
            directions = []
            for i, b in enumerate(window):
                c = float(b.close)
                if abs(c) > 1e-9:
                    ranges.append(abs(float(b.high) - float(b.low)) / abs(c))
                if i >= 1:
                    prev = window[i - 1]
                    d = float(b.close) - float(prev.close)
                    if abs(d) > 1e-12:
                        directions.append(1.0 if d > 0 else -1.0)
                    inter = max(
                        0.0,
                        min(float(prev.high), float(b.high)) - max(float(prev.low), float(b.low)),
                    )
                    union = max(float(prev.high), float(b.high)) - min(float(prev.low), float(b.low))
                    if union > 1e-12:
                        overlaps.append(inter / union)
            if not ranges or len(directions) < 2 or not overlaps:
                continue
            up = sum(1 for d in directions if d > 0)
            down = sum(1 for d in directions if d < 0)
            directional_ratio = max(up, down) / max(1, len(directions))
            sym_metrics.append(
                {
                    "avg_range_pct": float(mean(ranges)),
                    "directional_ratio": float(directional_ratio),
                    "overlap_ratio": float(mean(overlaps)),
                }
            )
        if not sym_metrics:
            return "TRENDING", {"avg_range_pct": 0.0, "directional_ratio": 0.0, "overlap_ratio": 0.0, "symbols_used": 0.0}
        avg_range_pct = float(mean(m["avg_range_pct"] for m in sym_metrics))
        directional_ratio = float(mean(m["directional_ratio"] for m in sym_metrics))
        overlap_ratio = float(mean(m["overlap_ratio"] for m in sym_metrics))
        is_choppy = (
            overlap_ratio >= float(self._regime_choppy_overlap_min)
            and (
                directional_ratio < float(self._regime_trend_directional_min)
                or avg_range_pct < float(self._regime_min_avg_range_pct)
            )
        )
        regime = "CHOPPY" if is_choppy else "TRENDING"
        return regime, {
            "avg_range_pct": avg_range_pct,
            "directional_ratio": directional_ratio,
            "overlap_ratio": overlap_ratio,
            "symbols_used": float(len(sym_metrics)),
        }

    def _log_trendline_skip_reason(self, symbol: str, reason: str, **kwargs: Any) -> None:
        extra = " | ".join(f"{k}={v}" for k, v in kwargs.items()) if kwargs else ""
        log.info(
            "TRENDLINE_SKIP_REASON | symbol=%s | reason=%s%s",
            symbol,
            reason,
            f" | {extra}" if extra else "",
        )

    def _expected_option_side_for_candidate(self, candidate: TrendlineCandidate) -> str:
        if candidate.direction == TrendlineDirection.BEAR:
            return "call"
        return "put"

    def _structure_display_label(self, candidate: TrendlineCandidate) -> str:
        pl = candidate.setup_payload or {}
        return str(pl.get("structure_display_label") or pl.get("setup_type") or "")

    def _fails_primary_lane_gate(self, candidate: TrendlineCandidate) -> tuple[bool, str]:
        enforce = bool(getattr(self.config, "primary_lane_enforce_geometry_alignment", True))
        if not enforce:
            return False, ""
        setup = candidate.setup_payload or {}
        setup_type = str(setup.get("setup_type") or "").strip().lower()
        opt_side = self._expected_option_side_for_candidate(candidate)
        if setup_type == "descending_resistance":
            if opt_side != "call" or candidate.direction != TrendlineDirection.BEAR:
                return True, "geometry_descending_resistance_requires_bear_call"
        elif setup_type == "ascending_support":
            if opt_side != "put" or candidate.direction != TrendlineDirection.BULL:
                return True, "geometry_ascending_support_requires_bull_put"
        elif candidate.direction == TrendlineDirection.BEAR and opt_side != "call":
            return True, "geometry_bear_line_requires_call"
        elif candidate.direction == TrendlineDirection.BULL and opt_side != "put":
            return True, "geometry_bull_line_requires_put"
        return False, ""

    def _bar_index_at_or_after(self, bars: List[OHLCVBar], cutoff_utc: datetime) -> int:
        cut = self._normalize_dt(cutoff_utc)
        for i, b in enumerate(bars):
            if self._normalize_dt(b.ts) >= cut:
                return i
        return max(0, len(bars) - 1)

    def _funnel_inc(self, name: str, delta: int = 1) -> None:
        self._funnel_counts[name] = int(self._funnel_counts.get(name, 0)) + int(delta)

    def _maybe_log_trendline_funnel_summary(self) -> None:
        now = time_module.time()
        if now - float(self._funnel_last_log_ts) < float(self._funnel_interval_sec):
            return
        self._funnel_last_log_ts = now
        log.info(
            "TRENDLINE_FUNNEL_SUMMARY | candidates_seen=%d | break_detected=%d | submitted=%d | durable_confirmed=%d",
            int(self._funnel_counts.get("candidates_seen", 0)),
            int(self._funnel_counts.get("break_detected", 0)),
            int(self._funnel_counts.get("submitted", 0)),
            int(self._funnel_counts.get("durable_confirmed", 0)),
        )
        try:
            from modules.trendline_measurement import get_trendline_measurement_day

            day = get_trendline_measurement_day()
            day.funnel["breaks"] = max(
                int(day.funnel.get("breaks", 0)),
                int(self._funnel_counts.get("break_detected", 0)),
            )
            day.log_funnel_audit()
        except Exception:
            pass

    def _maybe_log_session_summary(self, now: Optional[datetime] = None) -> None:
        if self._session_summary_logged:
            return
        if any(
            c.state
            not in {
                TrendlineCandidateState.EXECUTED,
                TrendlineCandidateState.INVALIDATED,
                TrendlineCandidateState.EXPIRED,
            }
            for c in self._candidates.values()
        ):
            return
        log.info(
            "TRENDLINE_SESSION_SUMMARY | symbols=%d | break_detected=%d | entry_emitted=%d | executed=%d",
            len(self._candidates),
            int(self._decision_stats.get("break_detected", 0)),
            int(self._decision_stats.get("entry_emitted", 0)),
            int(self._decision_stats.get("executed", 0)),
        )
        self._session_summary_logged = True

    def _update_missed_win_watchers(self, symbol: str, bar: OHLCVBar) -> None:
        return

    def seed_bar_cache_from_intraday(
        self,
        intraday_by_symbol: Optional[Dict[str, List[Any]]] = None,
    ) -> int:
        """Seed/merge TL bar cache for ORB2 universe — including symbols with empty TL1.

        Empty TL1 previously left ``_bar_cache`` empty → TL2/TL3 synthesize-only
        path could not form a valid A2 hull. Prefer denser series; never wipe a
        longer cache with a thinner seed.
        """
        if not isinstance(intraday_by_symbol, dict) or not intraday_by_symbol:
            return 0
        seeded = 0
        for raw_sym, bars in intraday_by_symbol.items():
            sym = str(raw_sym or "").strip().upper()
            if not sym or not isinstance(bars, list) or not bars:
                continue
            prior = self._bar_cache.get(sym) or []
            if len(prior) > len(bars):
                continue
            self._bar_cache[sym] = list(bars)
            seeded += 1
        return seeded

    def initialize_candidates(
        self,
        candidates: Iterable[TrendlineCandidate],
        intraday_by_symbol: Dict[str, List[OHLCVBar]],
        orb_context_by_symbol: Dict[str, Dict[str, object]],
        now_utc: Optional[datetime] = None,
        session_cutoff_utc: Optional[datetime] = None,
    ) -> None:
        """
        Accept 7:30 candidate list for post-7:30 monitoring.

        Trendlines must be supplied by ``trendline_setup_selector`` (selector-built lines only).
        """
        self._confirm_pending_last_log_ts.clear()
        self._session_summary_logged = False
        # Preserve TL0 emit tracking across TL1 morning init (keys differ by slot).
        preserved_tracking = {
            k: v
            for k, v in (self._entry_tracking or {}).items()
            if ":trendline0" in str(k)
        }
        preserved_skip = {
            k: v
            for k, v in (self._emit_skip_watch or {}).items()
            if ":trendline0" in str(k)
        }
        self._decision_stats = {
            "break_detected": 0,
            "entry_emitted": 0,
            "filtered_small_break": 0,
            "filtered_weak_break": 0,
            "filtered_anti_chop": 0,
            "missed_opportunity": 0,
            "executed": 0,
        }
        self._entry_tracking.clear()
        self._entry_tracking.update(preserved_tracking)
        self._emit_skip_watch.clear()
        self._emit_skip_watch.update(preserved_skip)
        self._consecutive_skips_session = 0
        self._skip_streak_alert_armed = True
        self._market_regime, self._market_regime_metrics = self._compute_market_regime(intraday_by_symbol)
        log.warning(
            "TRENDLINE_MARKET_REGIME | regime=%s | avg_range_pct=%.6f | directional_ratio=%.3f | overlap_ratio=%.3f | symbols_used=%d",
            self._market_regime,
            float(self._market_regime_metrics.get("avg_range_pct", 0.0)),
            float(self._market_regime_metrics.get("directional_ratio", 0.0)),
            float(self._market_regime_metrics.get("overlap_ratio", 0.0)),
            int(self._market_regime_metrics.get("symbols_used", 0.0)),
        )
        now = self._normalize_dt(now_utc or datetime.now(timezone.utc))
        if session_cutoff_utc is not None:
            self._session_730_cutoff_utc = self._normalize_dt(session_cutoff_utc)
        elif self._session_730_cutoff_utc is None:
            loc = now.astimezone(self._tz_pt)
            from modules.session_clock import orb2_capture_window_pt

            _, orb2_end = orb2_capture_window_pt()
            self._session_730_cutoff_utc = self._normalize_dt(
                datetime.combine(loc.date(), orb2_end, self._tz_pt)
            )
        struct_cache: Dict[str, OrbTestFailureClassification] = {}
        for candidate in candidates:
            symbol = candidate.symbol
            self._bar_cache[symbol] = list(intraday_by_symbol.get(symbol, []))
            if symbol not in struct_cache:
                struct_cache[symbol] = self.builder.classify_orb_test_failure(
                    symbol,
                    self._bar_cache.get(symbol, []),
                    orb_context_by_symbol.get(symbol, {}),
                )
            orb_cls = struct_cache[symbol]
            setattr(candidate, "orb_test_failure", orb_cls)
            setattr(candidate, "touch_count", 0)
            setattr(candidate, "pressure_score", 0.0)
            setattr(candidate, "last_touch_ts", None)
            setattr(candidate, "last_touch_bar_index", None)
            setattr(candidate, "entry_mode", "")
            setattr(candidate, "awaiting_line_retest", False)
            setattr(candidate, "line_retest_ok", False)
            setattr(candidate, "structure_hard_gate_passed", False)
            setattr(candidate, "_maturity_fail_logged", False)
            setattr(candidate, "_maturity_pass_logged", False)
            setattr(candidate, "low_confidence", False)

            # NOTE:
            # Trendlines must be built by trendline_setup_selector.
            # Engine-side construction is deprecated and disabled in production flow.
            if candidate.trendline is None:
                self._log_trendline_skip_reason(
                    symbol,
                    "missing_selector_trendline",
                    failure_type=str(getattr(orb_cls, "failure_type", "") or ""),
                )
                log.info(
                    "TRENDLINE_LEGACY_PATH_BLOCKED | symbol=%s | detail=engine_internal_build_disabled",
                    symbol,
                )
                continue

            setattr(candidate, "structure_hard_gate_passed", True)
            setup_pl = candidate.setup_payload or {}
            tl_pb = candidate.trendline
            meta_pb = (tl_pb.metadata or {}) if tl_pb else {}
            log.info(
                "TRENDLINE_SELECTOR_STRUCTURE_ACTIVE | symbol=%s | direction=%s | setup_type=%s | "
                "failure_type=%s | selected_line_reason=%s | anchor_one_source=%s | anchor_two_source=%s",
                symbol,
                candidate.direction.value,
                setup_pl.get("setup_type", ""),
                orb_cls.failure_type or "none",
                orb_cls.selected_line_reason or "none",
                meta_pb.get("anchor_one_source", "none"),
                meta_pb.get("anchor_two_source", "none"),
            )
            ft_pb = orb_cls.failure_type or ""
            if (not ft_pb) or ft_pb not in ("failed_downside", "failed_upside"):
                setattr(candidate, "low_confidence", True)
                log.info(
                    "TRENDLINE_SELECTOR_LOW_CONFIDENCE | symbol=%s | direction=%s | failure_type=%s | "
                    "note=structure_classification_not_failed_side_pair",
                    symbol,
                    candidate.direction.value,
                    ft_pb or "none",
                )
            log.info(
                "TRENDLINE_LINE_SELECTED | symbol=%s | direction=%s | reason=selector_built_trendline | setup_type=%s | "
                "structure_failure_type=%s | note=structure_gate_selector_built",
                symbol,
                candidate.direction.value,
                setup_pl.get("setup_type", ""),
                orb_cls.failure_type,
            )

            if not isinstance(candidate.setup_payload, dict):
                candidate.setup_payload = {}
            if not candidate.setup_payload.get("trendline_slot"):
                candidate.setup_payload["trendline_slot"] = "trendline1"
            slot = trendline_slot_from_payload(candidate.setup_payload)
            candidate_key = make_trendline_candidate_key(symbol, candidate.direction, slot)
            candidate.candidate_id = candidate_key
            candidate.continuation_pending = False
            candidate.continuation_start_index = 0
            candidate.continuation_reason = ""
            candidate.continuation_max_bars = int(self._continuation_max_bars)
            candidate.rearm_pending = False
            candidate.rearm_origin_reason = ""
            candidate.rearm_started_at = None
            candidate.rearm_checks_done = 0
            candidate.rearm_break_level = None
            candidate.rearm_break_candle_low = None
            candidate.rearm_break_candle_high = None
            candidate.retest_pending = False
            candidate.retest_started_at = None
            candidate.retest_break_level = None
            candidate.retest_direction = ""
            candidate.retest_checks_done = 0
            candidate.mfe_window_logged = {}
            candidate.impulse_pending_confirmation = False
            candidate.impulse_break_high = None
            candidate.impulse_break_low = None
            candidate.break_bar_index = None
            candidate.break_timestamp = None
            candidate.decision_logged = False
            candidate.skip_timestamp = None
            candidate.skip_price = None
            candidate.missed_win_early_logged = False
            self._candidates[candidate_key] = candidate
            self._set_state(candidate, TrendlineCandidateState.WAITING_FOR_BUILD, "initialized")

            lane_fail, lane_reason = self._fails_primary_lane_gate(candidate)
            if lane_fail:
                self._set_state(candidate, TrendlineCandidateState.INVALIDATED, lane_reason)
                log.warning(
                    "TRENDLINE_PIPELINE | stage=invalidated | symbol=%s | trade_id=%s | reason=%s | "
                    "detail=primary_lane_excluded_at_build",
                    symbol,
                    self._trade_id(candidate),
                    lane_reason,
                )
                continue

            if self._is_expired(now, candidate):
                self._set_state(candidate, TrendlineCandidateState.EXPIRED, TrendlineReasonCode.EXPIRED_CUTOFF.value)
                log.info(
                    "TRENDLINE_CANDIDATE_EXPIRED | symbol=%s | trade_id=%s | reason=%s | phase=initialize",
                    symbol,
                    f"{symbol}_na",
                    TrendlineReasonCode.EXPIRED_CUTOFF.value,
                )
                log.info(
                    "TRENDLINE_PIPELINE | stage=expired | symbol=%s | trade_id=%s | reason=%s",
                    symbol,
                    f"{symbol}_na",
                    TrendlineReasonCode.EXPIRED_CUTOFF.value,
                )
                self._maybe_log_session_summary(now)
                continue

            self._set_state(candidate, TrendlineCandidateState.WAITING_FOR_BREAK, "trendline_selector_built")
            if self._is_simple_break_mode():
                set_simple_state(candidate, SimpleTrendlineState.ARMED, "trendline_selector_built")
            setattr(candidate, "start_time", now)
            bc = self._bar_cache.get(symbol, [])
            cut_ix = self._bar_index_at_or_after(bc, self._session_730_cutoff_utc or now)
            sm_idx = max(cut_ix, len(bc) - 1) if bc else 0
            setattr(candidate, "structure_maturity_bar_index", sm_idx)
            setattr(
                candidate,
                "structure_shift_ts",
                self._normalize_dt(bc[cut_ix].ts) if bc else (self._session_730_cutoff_utc or now),
            )
            setattr(candidate, "trendline_line_source", "selector_built")
            tls_chk = str(getattr(candidate, "trendline_line_source", "") or "")
            if tls_chk != "selector_built":
                log.error(
                    "TRENDLINE_ERROR | type=unexpected_trendline_source | symbol=%s | source=%s",
                    symbol,
                    tls_chk,
                )
            assert tls_chk == "selector_built"
            self._log_trendline_flow_stage(candidate, "selector_built")
            self._log_trendline_flow_stage(candidate, "monitoring")
            log.info(
                "TRENDLINE_PIPELINE | stage=build | symbol=%s | trade_id=%s | action=use_selector_built | line_geometry=%s | setup_type=%s",
                symbol,
                self._trade_id(candidate),
                candidate.direction.value,
                (candidate.setup_payload or {}).get("setup_type", ""),
            )
            self._catchup_intraday_bars_on_activate(candidate)
            if candidate.state == TrendlineCandidateState.READY_TO_EXECUTE:
                log.warning(
                    "TRENDLINE_CATCHUP_READY | symbol=%s | trade_id=%s | detail=replay_reached_ready_live_watch_will_emit",
                    symbol,
                    self._trade_id(candidate),
                )
            elif candidate.state == TrendlineCandidateState.WAITING_FOR_CONFIRMATION:
                log.info(
                    "TRENDLINE_CATCHUP_CONFIRM | symbol=%s | trade_id=%s | detail=replay_awaiting_confirmation",
                    symbol,
                    self._trade_id(candidate),
                )

    def add_runtime_candidate(
        self,
        candidate: TrendlineCandidate,
        *,
        bars: Optional[List[OHLCVBar]] = None,
        now_utc: Optional[datetime] = None,
    ) -> Optional[str]:
        """Inject Trendline2/3 after 7:30. Caller expires prior slots after success.

        Returns candidate_id on success, None if rejected / duplicate.
        """
        if candidate is None or candidate.trendline is None:
            return None
        symbol = str(candidate.symbol or "").strip().upper()
        if not symbol:
            return None
        if not isinstance(candidate.setup_payload, dict):
            candidate.setup_payload = {}
        slot = trendline_slot_from_payload(candidate.setup_payload)
        if slot == "trendline1" and not candidate.setup_payload.get("trendline_slot"):
            candidate.setup_payload["trendline_slot"] = "trendline1"
            slot = "trendline1"
        candidate.setup_payload["trendline_slot"] = slot
        key = make_trendline_candidate_key(symbol, candidate.direction, slot)
        if key in self._candidates:
            existing = self._candidates[key]
            st = getattr(existing, "state", None)
            if st not in {
                TrendlineCandidateState.INVALIDATED,
                TrendlineCandidateState.EXPIRED,
            }:
                log.info(
                    "TRENDLINE_ORB2_ENVELOPE | symbol=%s | action=skip_inject | reason=duplicate_key | key=%s",
                    symbol,
                    key,
                )
                return None

        now = self._normalize_dt(now_utc or datetime.now(timezone.utc))
        if bars is not None:
            # Merge inject path into cache — never wipe denser TL1/Yahoo history.
            try:
                self.merge_provider_bars_into_cache(symbol, list(bars))
            except Exception:
                prior = self._bar_cache.get(symbol) or []
                if len(list(bars)) >= len(prior):
                    self._bar_cache[symbol] = list(bars)
        elif symbol not in self._bar_cache:
            self._bar_cache[symbol] = []

        candidate.candidate_id = key
        candidate.symbol = symbol
        candidate.continuation_pending = False
        candidate.continuation_start_index = 0
        candidate.continuation_reason = ""
        candidate.continuation_max_bars = int(self._continuation_max_bars)
        candidate.rearm_pending = False
        candidate.mfe_window_logged = {}
        candidate.decision_logged = False
        setattr(candidate, "structure_hard_gate_passed", True)
        payload = candidate.setup_payload or {}
        is_day_freeze = (
            str(payload.get("structure_selection_reason") or "") == "day_freeze_hydrate"
            or str(payload.get("trendline_structure_source") or "") == "day_freeze_hydrate"
        )
        line_source = "day_freeze_hydrate" if is_day_freeze else "orb2_extension_envelope"
        setattr(candidate, "trendline_line_source", line_source)
        setattr(candidate, "start_time", now)
        bc = self._bar_cache.get(symbol, [])
        cut_ix = self._bar_index_at_or_after(bc, self._session_730_cutoff_utc or now)
        sm_idx = max(cut_ix, len(bc) - 1) if bc else 0
        setattr(candidate, "structure_maturity_bar_index", sm_idx)
        setattr(
            candidate,
            "structure_shift_ts",
            self._normalize_dt(bc[cut_ix].ts) if bc else (self._session_730_cutoff_utc or now),
        )

        lane_fail, lane_reason = self._fails_primary_lane_gate(candidate)
        if lane_fail:
            log.warning(
                "TRENDLINE_ORB2_ENVELOPE | symbol=%s | action=reject_inject | reason=%s | slot=%s",
                symbol,
                lane_reason,
                slot,
            )
            return None

        self._candidates[key] = candidate
        self._set_state(candidate, TrendlineCandidateState.WAITING_FOR_BREAK, line_source)
        if self._is_simple_break_mode():
            set_simple_state(candidate, SimpleTrendlineState.ARMED, line_source)
        flow_stage = "day_freeze_restored" if is_day_freeze else "orb2_extension_injected"
        self._log_trendline_flow_stage(candidate, flow_stage)
        inject_action = "day_freeze_restore" if is_day_freeze else "runtime_inject"
        log.info(
            "TRENDLINE_PIPELINE | stage=build | symbol=%s | trade_id=%s | action=%s | "
            "slot=%s | line_geometry=%s | setup_type=%s | key=%s",
            symbol,
            self._trade_id(candidate),
            inject_action,
            slot,
            candidate.direction.value,
            (candidate.setup_payload or {}).get("setup_type", ""),
            key,
        )
        return key

    def replace_runtime_trendline(
        self,
        candidate_id: str,
        tl_def: "TrendlineDefinition",
    ) -> bool:
        """Update TL2/TL3 geometry while still waiting for break (session-extreme refresh)."""
        key = str(candidate_id or "").strip()
        if not key or tl_def is None:
            return False
        candidate = self._candidates.get(key)
        if candidate is None:
            return False
        st = getattr(candidate, "state", None)
        if st not in {
            TrendlineCandidateState.WAITING_FOR_BREAK,
            TrendlineCandidateState.WAITING_FOR_BUILD,
        }:
            return False
        if self._is_simple_break_mode():
            sst = get_simple_state(candidate)
            if sst not in (SimpleTrendlineState.ARMED, None):
                return False
        if candidate.direction != tl_def.direction:
            return False
        candidate.trendline = tl_def
        setattr(candidate, "trendline_line_source", "orb2_extension_envelope")
        log.info(
            "TRENDLINE_PIPELINE | stage=build | symbol=%s | trade_id=%s | action=runtime_refresh | "
            "slot=%s | line_geometry=%s | a1=%.4f | a2=%.4f | key=%s",
            candidate.symbol,
            self._trade_id(candidate),
            trendline_slot_from_payload(candidate.setup_payload),
            tl_def.direction.value,
            float(tl_def.anchor_one.price),
            float(tl_def.anchor_two.price),
            key,
        )
        return True

    def _catchup_intraday_bars_on_activate(
        self, candidate: TrendlineCandidate
    ) -> Optional[TrendlineTradeSignal]:
        """
        Replay 1m bars already in cache (7:30 → now) so breaks at 7:32–7:38 are not missed
        when the 7:30 build finishes late. Live watch only appends new minutes via quotes.
        """
        bars = self._bar_cache.get(candidate.symbol, [])
        if len(bars) < 2:
            return None
        shift_ts = getattr(candidate, "structure_shift_ts", None)
        start_ix = self._bar_index_at_or_after(bars, self._session_730_cutoff_utc or shift_ts or bars[0].ts)
        end_ix = len(bars) - 1
        if end_ix <= start_ix:
            return None
        log.info(
            "TRENDLINE_CATCHUP_SCAN | symbol=%s | trade_id=%s | bars=%d..%d | shift_ts=%s | "
            "reason=replay_intraday_since_730",
            candidate.symbol,
            self._trade_id(candidate),
            start_ix,
            end_ix,
            shift_ts,
        )
        setattr(candidate, "_catchup_replay", True)
        try:
            for i in range(start_ix, end_ix + 1):
                bar = bars[i]
                if shift_ts and self._normalize_dt(bar.ts) < self._normalize_dt(shift_ts):
                    continue
                candidate.decision_logged = False
                self._advance_candidate_on_bar(candidate, bar)
        finally:
            setattr(candidate, "_catchup_replay", False)
        return None

    def upsert_watch_quote_bar(
        self,
        symbol: str,
        price: float,
        ts: datetime,
        *,
        session_volume: Optional[float] = None,
    ) -> OHLCVBar:
        """
        Aggregate live quotes into one OHLCV bar per clock minute (chart-aligned breaks).
        Replaces per-tick micro-bars that miss AMD/RKLB/NFLX-style 1m structure breaks.

        When ``session_volume`` (broker cumulative day volume) is supplied, minute-bar
        volume is incremented by the positive delta vs the last seen session total.
        """
        from zoneinfo import ZoneInfo

        tz_pt = ZoneInfo("America/Los_Angeles")
        loc = self._normalize_dt(ts).astimezone(tz_pt)
        key = (loc.date(), loc.hour, loc.minute)
        cache = self._bar_cache.setdefault(symbol, [])
        vol_delta = 0.0
        if session_volume is not None:
            try:
                sess = float(session_volume)
            except (TypeError, ValueError):
                sess = 0.0
            if math.isfinite(sess) and sess > 0:
                last_sess = float(self._watch_last_session_volume.get(symbol, 0.0) or 0.0)
                if last_sess > 0 and sess >= last_sess:
                    vol_delta = max(0.0, sess - last_sess)
                elif last_sess <= 0:
                    vol_delta = max(1.0, sess * 0.001)
                self._watch_last_session_volume[symbol] = sess
        if cache:
            last = cache[-1]
            last_loc = self._normalize_dt(last.ts).astimezone(tz_pt)
            last_key = (last_loc.date(), last_loc.hour, last_loc.minute)
            if last_key == key:
                new_vol = float(last.volume or 0.0)
                if vol_delta > 0:
                    new_vol = max(new_vol, 0.0) + vol_delta
                updated = replace(
                    last,
                    high=max(float(last.high), price),
                    low=min(float(last.low), price),
                    close=price,
                    volume=new_vol,
                )
                cache[-1] = updated
                return updated
        prev = float(cache[-1].close) if cache else price
        bar = OHLCVBar(
            ts=self._normalize_dt(ts),
            open=prev,
            high=max(prev, price),
            low=min(prev, price),
            close=price,
            volume=vol_delta if vol_delta > 0 else 0.0,
            atr=max(1e-9, abs(price - prev)),
        )
        cache.append(bar)
        return bar

    def merge_provider_bars_into_cache(self, symbol: str, provider_bars: List[Any]) -> int:
        """
        Merge Yahoo / provider 1m OHLC into the watch cache.

        Provider OHLC always wins for that minute so quote-synth session volume
        cannot block Yahoo structure. Prefer minute-aligned timestamps.
        Returns count of bars merged/updated.
        """
        if not provider_bars:
            return 0
        merged = 0
        cache = self._bar_cache.setdefault(symbol, [])
        index: Dict[tuple, int] = {}
        for i, existing in enumerate(cache):
            loc = self._normalize_dt(existing.ts).astimezone(self._tz_pt)
            index[(loc.date(), loc.hour, loc.minute)] = i

        for raw in provider_bars:
            bar: Optional[OHLCVBar]
            if isinstance(raw, OHLCVBar):
                bar = raw
            elif isinstance(raw, dict):
                try:
                    ts_raw = raw.get("timestamp") or raw.get("datetime") or raw.get("time")
                    ts = self._normalize_dt(ts_raw) if ts_raw is not None else None
                    if ts is None:
                        continue
                    bar = OHLCVBar(
                        ts=ts,
                        open=float(raw.get("open", 0.0) or 0.0),
                        high=float(raw.get("high", 0.0) or 0.0),
                        low=float(raw.get("low", 0.0) or 0.0),
                        close=float(raw.get("close", 0.0) or raw.get("last", 0.0) or 0.0),
                        volume=float(raw.get("volume", 0.0) or 0.0),
                        atr=float(raw.get("atr", 0.0) or 0.0) if raw.get("atr") is not None else None,
                    )
                except (TypeError, ValueError):
                    continue
            else:
                continue
            loc = self._normalize_dt(bar.ts).astimezone(self._tz_pt)
            mk = (loc.date(), loc.hour, loc.minute)
            aligned_ts = loc.replace(second=0, microsecond=0)
            if bar.ts != aligned_ts:
                bar = replace(bar, ts=aligned_ts)
            try:
                vol = float(bar.volume or 0.0)
            except (TypeError, ValueError):
                vol = 0.0
            if mk in index:
                ix = index[mk]
                prev = cache[ix]
                cache[ix] = replace(
                    prev,
                    ts=aligned_ts,
                    open=float(bar.open),
                    high=float(bar.high),
                    low=float(bar.low),
                    close=float(bar.close),
                    volume=max(vol, float(prev.volume or 0.0)),
                    atr=bar.atr if bar.atr is not None else prev.atr,
                )
                merged += 1
            else:
                cache.append(bar)
                index[mk] = len(cache) - 1
                merged += 1
        cache.sort(key=lambda b: self._normalize_dt(b.ts))
        return merged

    def _handle_simple_priority_dataset_capture(self, **kwargs: Any) -> None:
        if callable(self.on_priority_dataset_capture):
            self.on_priority_dataset_capture(**kwargs)
        try:
            from modules.config_loader import get_config_value

            legacy_on = str(
                get_config_value("ENABLE_TRENDLINE_LEGACY_JSONL_DATASET", "false")
            ).strip().lower() in ("1", "true", "yes")
        except Exception:
            legacy_on = False
        if not legacy_on:
            return
        try:
            from modules.trendline_priority_optimizer_dataset import (
                build_priority_optimizer_row,
                get_trendline_priority_optimizer_dataset,
            )

            row = build_priority_optimizer_row(
                candidate=kwargs.get("candidate"),
                bar=kwargs.get("bar"),
                bars=kwargs.get("bars") or [],
                trade_id=kwargs.get("trade_id", ""),
                decision=kwargs.get("decision", ""),
                decision_reason=kwargs.get("decision_reason", ""),
                quality=kwargs.get("quality"),
                signal=kwargs.get("signal"),
                vwap=kwargs.get("vwap", 0.0),
                vwap_position=kwargs.get("vwap_position", ""),
                rsi=kwargs.get("rsi"),
                volume_ratio=kwargs.get("volume_ratio"),
                velocity_pct=kwargs.get("velocity_pct"),
                orb_zone=kwargs.get("orb_zone"),
            )
            get_trendline_priority_optimizer_dataset().record_event(row)
        except Exception:
            pass

    def _handle_simple_first_break_snapshot(self, **kwargs: Any) -> None:
        hook = getattr(self, "on_first_break_snapshot", None)
        if callable(hook):
            hook(**kwargs)

    def _advance_simple_candidate_on_bar(
        self, candidate: TrendlineCandidate, bar: OHLCVBar, now: datetime
    ) -> Optional[TrendlineTradeSignal]:
        processor = self._simple_processor
        if processor is None:
            processor = SimpleTrendlineProcessor(
                self.config,
                on_dataset_capture=self._handle_simple_priority_dataset_capture,
                on_first_break_snapshot=self._handle_simple_first_break_snapshot,
                trade_id_fn=self._trade_id,
                emitted_ids=self._emitted_candidate_ids,
                session_tech_system=getattr(self, "_session_tech_system", None),
            )
            self._simple_processor = processor
        elif getattr(self, "_session_tech_system", None) is not None:
            processor.bind_session_tech_system(self._session_tech_system)
        bars = self._bar_cache.get(candidate.symbol, [])
        return processor.advance(candidate, bar, bars, now)

    def _advance_candidate_on_bar(
        self, candidate: TrendlineCandidate, bar: OHLCVBar
    ) -> Optional[TrendlineTradeSignal]:
        """Single-bar state machine step (simple break → quality → emit)."""
        now = self._normalize_dt(bar.ts)
        candidate.decision_logged = False
        return self._advance_simple_candidate_on_bar(candidate, bar, now)

    def process_new_bar(
        self,
        symbol: str,
        bar: OHLCVBar,
        *,
        candidate_id: Optional[str] = None,
    ) -> Optional[TrendlineTradeSignal]:
        """
        Process a new post-7:30 candle and emit trade signal once fully confirmed.

        When ``candidate_id`` is set (Prime watch loop), only that envelope
        advances so a TL0/TL1 bull PUT and bear CALL can each emit independently.
        """
        self._maybe_log_trendline_funnel_summary()
        # TL0 geometry hard-stops at 07:10 PT — never advance sibling TL0 slots when
        # Prime watches TL1+ for the same symbol after cutoff.
        tl0_open = True
        try:
            from datetime import datetime as _dt
            from zoneinfo import ZoneInfo

            from modules.session_clock import orb1_tl0_session_open

            # Prefer bar clock (replay/tests); fall back to wall clock.
            try:
                bar_dt = self._normalize_dt(bar.ts)
                if bar_dt.tzinfo is None:
                    bar_dt = bar_dt.replace(tzinfo=ZoneInfo("UTC"))
                now_pt = bar_dt.astimezone(ZoneInfo("America/Los_Angeles")).time()
            except Exception:
                now_pt = _dt.now(ZoneInfo("America/Los_Angeles")).time()
            tl0_open = orb1_tl0_session_open(now_pt)
        except Exception:
            tl0_open = True

        cid_filter = str(candidate_id or "").strip()
        candidates = []
        for c in self._candidates.values():
            if c.symbol != symbol:
                continue
            if cid_filter and str(getattr(c, "candidate_id", "") or "") != cid_filter:
                continue
            if c.state in {
                TrendlineCandidateState.EXECUTED,
                TrendlineCandidateState.INVALIDATED,
                TrendlineCandidateState.EXPIRED,
            }:
                continue
            try:
                slot = trendline_slot_from_payload(getattr(c, "setup_payload", None) or {})
            except Exception:
                slot = "trendline1"
            if slot == "trendline0" and not tl0_open:
                # Expire in place so later ticks skip cleanly.
                self._set_state(
                    c, TrendlineCandidateState.EXPIRED, "tl0_geometry_cutoff_0710"
                )
                if self._is_simple_break_mode():
                    set_simple_state(c, SimpleTrendlineState.EXPIRED, "tl0_geometry_cutoff_0710")
                continue
            candidates.append(c)
        if not candidates:
            return None
        self._funnel_inc("candidates_seen", len(candidates))

        cache = self._bar_cache.setdefault(symbol, [])
        if cache:
            last = cache[-1]
            same_tick = last is bar
            if not same_tick:
                try:
                    same_tick = self._normalize_dt(last.ts) == self._normalize_dt(bar.ts)
                except Exception:
                    same_tick = False
            if not same_tick:
                cache.append(bar)
        else:
            cache.append(bar)
        now = self._normalize_dt(bar.ts)
        self._update_missed_win_watchers(symbol, bar)

        signal: Optional[TrendlineTradeSignal] = None
        for candidate in candidates:
            sig = self._advance_candidate_on_bar(candidate, bar)
            if sig is not None:
                signal = sig

        self._maybe_log_session_summary(now)
        post_break_active = [
            c
            for c in candidates
            if c.state
            in (
                TrendlineCandidateState.WAITING_FOR_CONFIRMATION,
                TrendlineCandidateState.REVERSAL_WATCH,
                TrendlineCandidateState.READY_TO_EXECUTE,
            )
        ]
        if post_break_active:
            tnow = time_module.time()
            last_pe = float(self._post_break_no_emit_diag_ts.get(symbol, 0.0) or 0.0)
            if tnow - last_pe >= 180.0:
                self._post_break_no_emit_diag_ts[symbol] = tnow
                parts = [
                    f"{self._trade_id(c)}:{c.state.value}:state_reason={getattr(c, 'state_reason', '') or ''}"
                    for c in post_break_active[:5]
                ]
                log.warning(
                    "TRENDLINE_NO_EMIT_THIS_BAR | symbol=%s | n_post_break=%d | summary=%s | "
                    "hint=pair_logs_TRENDLINE_DECISION_SNAPSHOT|TRENDLINE_SKIP_REASON|TRENDLINE_PIPELINE|stage=entry_blocked",
                    symbol,
                    len(post_break_active),
                    " | ".join(parts),
                )
        if signal is not None:
            log.warning(
                "TRENDLINE_SIGNAL_RETURNED_TO_PRIME | symbol=%s | option_side=%s | path=%s",
                signal.symbol,
                signal.option_side,
                "simple_break_mode" if self._is_simple_break_mode() else "legacy",
            )
        return signal

    def mark_executed(
        self,
        symbol: str,
        *,
        option_side: Optional[str] = None,
        candidate_id: Optional[str] = None,
    ) -> None:
        """Mark the filled candidate executed; leave opposite-side TL slots armed."""
        sym = str(symbol or "").strip().upper()
        side = str(option_side or "").strip().lower()
        cid = str(candidate_id or "").strip()
        if not sym:
            return
        for c in self._candidates.values():
            if str(getattr(c, "symbol", "") or "").strip().upper() != sym:
                continue
            if cid:
                if str(getattr(c, "candidate_id", "") or "") != cid:
                    continue
            elif side in ("call", "put"):
                if self._expected_option_side_for_candidate(c) != side:
                    continue
            else:
                # Legacy callers: mark ready/executed rows for the symbol only.
                pass
            if c.state not in {
                TrendlineCandidateState.READY_TO_EXECUTE,
                TrendlineCandidateState.EXECUTED,
            }:
                st_name = str(getattr(c, "state", "") or "")
                if cid and str(getattr(c, "candidate_id", "") or "") == cid:
                    pass  # always mark the exact filled candidate
                elif "READY_TO_EXECUTE" not in st_name and "EXECUTED" not in st_name:
                    continue
            self._set_state(c, TrendlineCandidateState.EXECUTED, "executor_ack")
            if self._is_simple_break_mode():
                set_simple_state(c, SimpleTrendlineState.EXECUTED, "executor_ack")
            self._decision_stats["executed"] += 1
            self._funnel_inc("durable_confirmed")
            track = self._entry_tracking.get(c.symbol) or {}
            track["entry_ts"] = float(track.get("entry_ts", time_module.time()))
            track["max_pnl_pct"] = float(track.get("max_pnl_pct", 0.0))
            track["entry_mode"] = str(getattr(c, "entry_mode", "") or "")
            track["pressure_score"] = float(getattr(c, "pressure_score", 0.0) or 0.0)
            if c.break_event:
                track["break_distance_pct"] = float(
                    getattr(c.break_event, "break_distance_pct", 0.0) or 0.0
                )
            orb_c = getattr(c, "orb_test_failure", None)
            track["structure_type"] = getattr(orb_c, "failure_type", "") if orb_c else ""
            self._entry_tracking[c.symbol] = track
        self._maybe_log_session_summary()

    def mark_execution_rejected_for_session(
        self,
        symbol: str,
        reason: str = "exec_reject_day",
        *,
        option_side: Optional[str] = None,
        candidate_id: Optional[str] = None,
    ) -> int:
        """Invalidate only the matching envelope. Dual envelopes stay independent.

        Without ``candidate_id`` or ``option_side`` this is a no-op so a leftover
        symbol-level caller cannot kill the opposite envelope.
        """
        sym = str(symbol or "").strip().upper()
        if not sym:
            return 0
        cid = str(candidate_id or "").strip()
        side = str(option_side or "").strip().lower()
        if not cid and side not in ("call", "put"):
            log.warning(
                "TRENDLINE_PIPELINE | stage=exec_reject_keep_peer_envelopes | symbol=%s | "
                "reason=%s | action=skip_symbol_invalidate",
                sym,
                str(reason or "")[:120],
            )
            return 0
        n = 0
        reason_s = f"exec_reject_day:{str(reason or 'execute_failed')[:120]}"
        watchable = (
            TrendlineCandidateState.WAITING_FOR_BREAK,
            TrendlineCandidateState.WAITING_FOR_CONFIRMATION,
            TrendlineCandidateState.READY_TO_EXECUTE,
        )
        for c in list(self._candidates.values()):
            if str(getattr(c, "symbol", "") or "").strip().upper() != sym:
                continue
            if cid:
                if str(getattr(c, "candidate_id", "") or "") != cid:
                    continue
            elif self._expected_option_side_for_candidate(c) != side:
                continue
            st = getattr(c, "state", None)
            if st not in watchable:
                # Dual-lane / simple mode may keep EMITTED under READY_TO_EXECUTE already.
                st_name = str(st or "")
                if not any(
                    tok in st_name
                    for tok in ("WAITING_FOR_BREAK", "WAITING_FOR_CONFIRMATION", "READY_TO_EXECUTE")
                ):
                    continue
            self._set_state(c, TrendlineCandidateState.INVALIDATED, reason_s)
            if self._is_simple_break_mode():
                try:
                    set_simple_state(c, SimpleTrendlineState.REJECTED, reason_s)
                except Exception:
                    pass
            n += 1
            log.warning(
                "TRENDLINE_PIPELINE | stage=exec_reject_envelope_only | symbol=%s | trade_id=%s | reason=%s",
                sym,
                self._trade_id(c) if hasattr(self, "_trade_id") else getattr(c, "candidate_id", ""),
                reason_s,
            )
        return n

    def rearm_deferred_execution(
        self,
        symbol: str,
        reason: str = "execution_deferred",
        *,
        candidate_id: Optional[str] = None,
    ) -> bool:
        """
        Return a simple-mode candidate from EMITTED to QUALITY_CONFIRMED so a deferred
        entry can re-emit when buffer cash or an open slot becomes available.

        When ``candidate_id`` is set, only that envelope rearms — the opposite
        TL0/TL1 envelope stays on its own emit/cooldown clock.
        """
        sym = str(symbol or "").strip().upper()
        if not sym:
            return False
        cid_filter = str(candidate_id or "").strip()
        rearmed = False
        for c in self._candidates.values():
            if c.symbol != sym:
                continue
            if cid_filter and str(getattr(c, "candidate_id", "") or "") != cid_filter:
                continue
            if not self._is_simple_break_mode():
                continue
            if get_simple_state(c) != SimpleTrendlineState.EMITTED:
                continue
            cid = str(getattr(c, "candidate_id", "") or "")
            if cid:
                self._emitted_candidate_ids.discard(cid)
            set_simple_state(c, SimpleTrendlineState.QUALITY_CONFIRMED, reason)
            c.state_reason = reason
            # A causal-data hold must not spend the 60s capital clock. The next
            # quality pass stamps a fresh exec-ready time. Discovery end still
            # retires the break.
            setup = getattr(c, "setup_payload", None)
            simple = setup.get("simple_mode") if isinstance(setup, dict) else None
            if isinstance(simple, dict):
                if str(reason or "") == "required_data_not_ready":
                    simple["data_readiness_hold"] = True
                    simple.pop("exec_ready_at", None)
                else:
                    simple.pop("data_readiness_hold", None)
            rearmed = True
            log.warning(
                "TRENDLINE_PIPELINE | stage=execution_rearm | symbol=%s | candidate_id=%s | reason=%s",
                sym,
                cid or cid_filter,
                reason,
            )
        return rearmed

    def track_trade_pnl(self, symbol: str, pnl_pct: float) -> None:
        track = self._entry_tracking.get(symbol) or {}
        prev = float(track.get("max_pnl_pct", float("-inf")))
        track["max_pnl_pct"] = max(prev, float(pnl_pct))
        self._entry_tracking[symbol] = track

    def log_trade_close(
        self,
        symbol: str,
        entry_price: float,
        exit_price: float,
        pnl_pct: float,
        max_pnl_pct: float,
        bars_held: int,
        exit_type: str,
    ) -> None:
        track = self._entry_tracking.get(symbol) or {}
        tracked_max = float(track.get("max_pnl_pct", float(max_pnl_pct)))
        final_max_pnl_pct = max(float(max_pnl_pct), tracked_max)
        efficiency = (float(pnl_pct) / final_max_pnl_pct) if abs(final_max_pnl_pct) > 1e-9 else 0.0
        trade_id = f"{symbol}_{self._normalize_dt(datetime.now(timezone.utc)).strftime('%Y%m%dT%H%M%S')}"
        for c in self._candidates.values():
            if c.symbol == symbol:
                trade_id = self._trade_id(c)
                break
        log.info(
            "TRENDLINE_EXIT_SUMMARY | symbol=%s | trade_id=%s | entry_price=%.4f | exit_price=%.4f | pnl_pct=%.4f | max_pnl_pct=%.4f | bars_held=%d | exit_type=%s | efficiency=%.6f",
            symbol,
            trade_id,
            float(entry_price),
            float(exit_price),
            float(pnl_pct),
            final_max_pnl_pct,
            int(bars_held),
            str(exit_type),
            efficiency,
        )
        em = str(track.get("entry_mode", "") or "")
        ps = float(track.get("pressure_score", 0.0) or 0.0)
        bdp = float(track.get("break_distance_pct", 0.0) or 0.0)
        st = str(track.get("structure_type", "") or "")
        if final_max_pnl_pct < float(self._bad_entry_max_favorable_pct) or float(pnl_pct) <= -float(
            self._bad_entry_drawdown_pct
        ):
            log.info(
                "TRENDLINE_BAD_ENTRY | symbol=%s | trade_id=%s | entry_mode=%s | pressure_score=%.2f | "
                "break_distance_pct=%.6f | structure_type=%s | max_favorable_pct=%.4f | pnl_at_close_pct=%.4f",
                symbol,
                trade_id,
                em,
                ps,
                bdp,
                st,
                final_max_pnl_pct,
                float(pnl_pct),
            )

    def _is_expired(self, now: datetime, candidate: TrendlineCandidate) -> bool:
        local = self._normalize_dt(now).astimezone(self._tz_pt)
        hh, mm = self._parse_hhmm(self.config.expiration_time_pt)
        expiry_local = datetime.combine(local.date(), time(hh, mm), self._tz_pt)
        start = getattr(candidate, "start_time", None)
        if start is not None:
            start_local = self._normalize_dt(start).astimezone(self._tz_pt)
            elapsed_min = (local - start_local).total_seconds() / 60.0
            if elapsed_min < float(self._max_active_minutes):
                return False
        return local >= expiry_local

    @staticmethod
    def _parse_hhmm(value: str) -> tuple[int, int]:
        parts = value.split(":")
        return int(parts[0]), int(parts[1])

    @staticmethod
    def _normalize_dt(value: datetime) -> datetime:
        """Return timezone-aware UTC datetime for safe comparisons."""
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def _dual_lane_invalidate_opposite(
        self,
        winner: TrendlineCandidate,
        *,
        winner_reason: str = "dual_lane_winner",
    ) -> None:
        """Keep the opposite envelope armed.

        Dual envelopes (bull PUT + bear CALL) on the same TL0/TL1 slot are
        independent. Reject, fill, or close of one side only arms a 1h
        ``symbol:slot:side`` cooldown on that envelope.
        """
        pl = winner.setup_payload or {}
        pair_id = str(pl.get("dual_lane_pair_id") or "")
        if not pair_id:
            return
        log.info(
            "TRENDLINE_DUAL_ENVELOPE_KEEP_PEER | symbol=%s | winner=%s|dir=%s | "
            "reason=%s | action=keep_opposite_armed | slot=%s",
            winner.symbol,
            str(pl.get("dual_lane_role") or "primary"),
            winner.direction.value,
            winner_reason,
            trendline_slot_from_payload(pl),
        )

    @staticmethod
    def _set_state(candidate: TrendlineCandidate, state: TrendlineCandidateState, reason: str) -> None:
        candidate.state = state
        candidate.state_reason = reason
        candidate.updated_at = datetime.now(timezone.utc)

    def expire_prior_slots_for_symbol(
        self,
        symbol: str,
        *,
        keep_slot: str,
        reason: str = "superseded_by_newer_slot",
    ) -> int:
        """Expire all candidates for ``symbol`` on slots older than ``keep_slot``.

        On TL2 inject expire TL1; on TL3 expire TL1+TL2; on TL10 expire TL1–TL9.
        TL0 is left alone (already hard-cuts at 07:10). Returns count expired.
        """
        from .trendline_models import TRENDLINE_SLOT_RANK, normalize_trendline_slot, trendline_slot_from_payload

        sym = str(symbol or "").strip().upper()
        keep = normalize_trendline_slot(keep_slot)
        keep_r = TRENDLINE_SLOT_RANK.get(keep)
        if not sym or keep_r is None or keep_r <= 1:
            # keep_slot must be TL2+ to supersede anything
            if keep_r is None or keep_r < 2:
                return 0
        expired = 0
        for key, cand in list((self._candidates or {}).items()):
            if str(getattr(cand, "symbol", "") or "").strip().upper() != sym:
                continue
            try:
                slot = trendline_slot_from_payload(getattr(cand, "setup_payload", None) or {})
            except Exception:
                slot = "trendline1"
            slot_r = TRENDLINE_SLOT_RANK.get(normalize_trendline_slot(slot), -1)
            # Never expire TL0 here; expire TL1..keep-1 only.
            if slot_r < 1 or slot_r >= keep_r:
                continue
            st = getattr(cand, "state", None)
            if st in {
                TrendlineCandidateState.INVALIDATED,
                TrendlineCandidateState.EXPIRED,
                TrendlineCandidateState.EXECUTED,
            }:
                continue
            reason_tag = f"{reason}:{keep}"
            self._set_state(cand, TrendlineCandidateState.EXPIRED, reason_tag)
            if isinstance(cand.setup_payload, dict):
                cand.setup_payload["geometry_superseded"] = True
                cand.setup_payload["superseded_by_slot"] = keep
                cand.setup_payload["supersede_reason"] = reason_tag
            if self._is_simple_break_mode():
                try:
                    set_simple_state(cand, SimpleTrendlineState.EXPIRED, reason_tag)
                except Exception:
                    pass
            expired += 1
            log.warning(
                "TRENDLINE_PIPELINE | stage=expired | symbol=%s | key=%s | "
                "reason=%s | prior_slot=%s | keep_slot=%s",
                sym,
                key,
                reason_tag,
                slot,
                keep,
            )
        return expired


