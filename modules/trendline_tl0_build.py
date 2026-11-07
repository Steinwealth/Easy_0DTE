"""TL0 dual-side envelope build after ORB1 ≥06:45 secure.

Uses ORB1 (06:30–06:45) H/L as A1 and the same A2 hull fitter as TL1.
Candidate keys: SYM:bull|bear:trendline0 — never overwrites TL1/2/3.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, time as time_cls, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

PT = ZoneInfo("America/Los_Angeles")


def _parse_bar_dicts(rows: List[Dict[str, Any]], OHLCVBar: Any) -> List[Any]:
    out: List[Any] = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        try:
            ts = r.get("timestamp") or r.get("ts") or r.get("datetime")
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            if not isinstance(ts, datetime):
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=PT)
            out.append(
                OHLCVBar(
                    ts=ts.astimezone(timezone.utc),
                    open=float(r.get("open") or 0),
                    high=float(r.get("high") or 0),
                    low=float(r.get("low") or 0),
                    close=float(r.get("close") or r.get("last") or 0),
                    volume=float(r.get("volume") or 0),
                    atr=float(r.get("atr") or 0) or abs(float(r.get("high") or 0) - float(r.get("low") or 0)),
                )
            )
        except Exception:
            continue
    return sorted(out, key=lambda b: b.ts)


def _orb1_obj_for_symbol(system: Any, symbol: str) -> Any:
    mgr = getattr(system, "orb_strategy_manager", None)
    rows = getattr(mgr, "orb_data", None) if mgr is not None else None
    if not isinstance(rows, dict):
        return None
    return rows.get(str(symbol).upper()) or rows.get(symbol)


def _source_symbols(system: Any) -> List[str]:
    syms: List[str] = []
    dm = getattr(system, "dte0_manager", None)
    if dm and getattr(dm, "target_symbols", None):
        syms = [str(s).strip().upper() for s in (dm.target_symbols or []) if s]
    if not syms:
        try:
            from easy0DTE.modules.prime_0dte_strategy_manager import load_0dte_symbols

            syms = [str(s).strip().upper() for s in (load_0dte_symbols() or []) if s]
        except Exception:
            pass
    if not syms and getattr(system, "symbol_list", None):
        syms = [str(s).strip().upper() for s in (system.symbol_list or []) if s]
    return list(dict.fromkeys([s for s in syms if s]))


async def build_tl0_candidates_after_orb1(system: Any) -> int:
    """Build TL0 dual-side envelopes when ORB1 freeze is secure. Returns candidate count."""
    if not getattr(system, "_enable_trendline_strategy", False):
        return 0
    eng = getattr(system, "trendline_signal_engine", None)
    if eng is None:
        log.warning("TRENDLINE_PIPELINE | stage=tl0_build | action=skip | reason=engine_not_initialized")
        return 0
    if not getattr(system, "_orb1_memory_freeze_valid", lambda: False)():
        log.info("TRENDLINE_PIPELINE | stage=tl0_build | action=hold | reason=orb1_not_secure")
        return 0

    # Day-freeze: hydrate TL0 keys only — do not rebuild over valid rows.
    try:
        from modules.trendline_geometry_persist import candidate_row_is_valid

        tracker = getattr(system, "daily_run_tracker", None)
        geo = None
        if tracker is not None and hasattr(tracker, "get_trendline_geometry"):
            geo = tracker.get_trendline_geometry()
        cands_blob = (geo or {}).get("candidates") if isinstance(geo, dict) else {}
        existing_tl0 = 0
        if isinstance(cands_blob, dict):
            for k, row in cands_blob.items():
                if ":trendline0" in str(k) and candidate_row_is_valid(row):
                    existing_tl0 += 1
        if existing_tl0 > 0:
            n_hyd = int(system._hydrate_trendline_geometry_from_daily_markers() or 0)
            eng_cands = getattr(getattr(system, "trendline_signal_engine", None), "candidates", None) or {}
            tl0_in_engine = any(":trendline0" in str(k).lower() for k in eng_cands)
            if tl0_in_engine:
                system._trendline0_ready = True
                system._trendline_watch_active = True
                log.warning(
                    "TRENDLINE_PIPELINE | stage=tl0_build | action=skip_rebuild_use_day_freeze | "
                    "tl0_keys=%d | hydrated=%d",
                    existing_tl0,
                    n_hyd,
                )
                apply_caps = getattr(system, "_apply_trendline_open_caps_from_config", None)
                if callable(apply_caps):
                    apply_caps()
                return existing_tl0
            log.warning(
                "TRENDLINE_PIPELINE | stage=tl0_build | action=freeze_tl0_missing_after_hydrate | "
                "tl0_keys=%d | hydrated=%d | note=fall_through_yahoo_dual_envelope",
                existing_tl0,
                n_hyd,
            )
    except Exception as freeze_err:
        log.debug("TRENDLINE_PIPELINE | stage=tl0_build | freeze_check_failed=%s", freeze_err)

    if getattr(system, "_tl0_build_in_flight", False):
        return 0
    system._tl0_build_in_flight = True
    built = 0
    try:
        from easyTrendline import OHLCVBar, TrendlineCandidate, TrendlineCandidateState
        from easyTrendline.trendline_setup_selector import select_tl0_structure_setup
        from modules.prime_data_manager import _yf_tl0_structure_1m_bars

        symbols = _source_symbols(system)
        with_orb: List[str] = []
        for sym in symbols:
            od = _orb1_obj_for_symbol(system, sym)
            if od is None:
                continue
            try:
                hi = float(getattr(od, "orb_high", None) or (od.get("orb_high") if isinstance(od, dict) else 0) or 0)
                lo = float(getattr(od, "orb_low", None) or (od.get("orb_low") if isinstance(od, dict) else 0) or 0)
            except Exception:
                continue
            if hi > 0 and lo > 0 and hi >= lo:
                with_orb.append(sym)
        if not with_orb:
            log.warning("TRENDLINE_PIPELINE | stage=tl0_build | action=empty | reason=no_orb1_context")
            return 0

        try:
            max_yf = int(os.getenv("TRENDLINE_BUILD_YF_1M_MAX_SYMBOLS", "150") or 150)
        except (TypeError, ValueError):
            max_yf = 150
        try:
            yf_batch = int(os.getenv("TRENDLINE_BUILD_YF_1M_BATCH_SIZE", "20") or 20)
        except (TypeError, ValueError):
            yf_batch = 20
        try:
            yf_sleep = float(os.getenv("TRENDLINE_BUILD_YF_1M_BATCH_SLEEP_SEC", "0.5") or 0.5)
        except (TypeError, ValueError):
            yf_sleep = 0.5

        structure_syms = with_orb[: max(0, max_yf)]
        t0 = time.perf_counter()
        yf_map = await _yf_tl0_structure_1m_bars(
            structure_syms,
            batch_size=yf_batch,
            inter_batch_sleep_sec=yf_sleep,
        )
        log.info(
            "TRENDLINE_PIPELINE | stage=tl0_build_fetch | action=tl0_structure_yf_1m | "
            "requested=%d | hits=%d | duration_ms=%.1f",
            len(structure_syms),
            sum(1 for v in (yf_map or {}).values() if v),
            (time.perf_counter() - t0) * 1000.0,
        )
        # Same real 06:30-06:45 1m bars → shared session tech, so RSI-14 / 3m PVE /
        # ATR / VWAP exist at the first ORB1 / TL0 gate (Oct 5 2026 warm-up fix).
        try:
            from modules.session_tech_warmup import seed_today_bars_from_tl0_fetch

            seed_today_bars_from_tl0_fetch(system, yf_map)
        except Exception as seed_err:
            log.debug("SESSION_TECH_WARMUP | tl0_bar_seed_skip | err=%s", seed_err)

        now_utc = datetime.now(timezone.utc)
        now_pt = datetime.now(PT)
        ts_630 = datetime.combine(now_pt.date(), time_cls(6, 30), PT)
        ts_645 = datetime.combine(now_pt.date(), time_cls(6, 45), PT)
        tl_config = getattr(eng, "config", None)
        candidate_objects: List[Any] = []
        intraday_by_symbol: Dict[str, List[Any]] = {}
        orb_context_by_symbol: Dict[str, Dict[str, Any]] = {}

        for sym in structure_syms:
            od = _orb1_obj_for_symbol(system, sym)
            bars = _parse_bar_dicts((yf_map or {}).get(sym) or [], OHLCVBar)
            # Keep only through 06:45
            bars = [b for b in bars if b.ts <= ts_645.astimezone(timezone.utc)]
            if len(bars) < 2:
                continue
            intraday_by_symbol[sym] = bars

            hi_ts = getattr(od, "orb_high_extreme_ts", None) if od else None
            lo_ts = getattr(od, "orb_low_extreme_ts", None) if od else None
            if hi_ts is None:
                hi_ts = ts_630
            if lo_ts is None:
                lo_ts = ts_630
            # Refine A1 timestamps against 1m path when possible
            try:
                from modules.trendline_orb2_timestamp_refinement import (
                    refine_orb2_extreme_timestamps_from_bars,
                )

                refine_orb2_extreme_timestamps_from_bars(
                    od,
                    bars,
                    window_start=ts_630,
                    window_end=ts_645,
                    abs_tolerance=float(os.getenv("TRENDLINE_ORB2_TS_REFINE_ABS_TOL", "0.05") or 0.05),
                    pct_tolerance=float(os.getenv("TRENDLINE_ORB2_TS_REFINE_PCT_TOL", "0.0002") or 0.0002),
                )
                hi_ts = getattr(od, "orb_high_extreme_ts", None) or hi_ts
                lo_ts = getattr(od, "orb_low_extreme_ts", None) or lo_ts
            except Exception:
                pass

            orb_ctx = {
                "orb_high": getattr(od, "orb_high", None),
                "orb_low": getattr(od, "orb_low", None),
                "orb_ts": getattr(od, "capture_time", None),
                "orb_high_ts": hi_ts,
                "orb_low_ts": lo_ts,
                "orb_box_source": "orb1_630_645",
                "a1_policy": "tl0",
                "envelope_cutoff_hm": (6, 45),
            }
            orb_context_by_symbol[sym] = orb_ctx
            selections = select_tl0_structure_setup(sym, bars, orb_ctx, config=tl_config)
            if not selections:
                continue
            dual_lane_id = f"{sym}_TL0_{now_utc.strftime('%Y%m%d')}"
            for selection in selections:
                peer_role = (
                    "peer_bear_call"
                    if selection.direction.value == "bear"
                    else "peer_bull_put"
                )
                setup_base = {
                    "signal_type": "TRENDLINE_UNIVERSE",
                    "strategy_name": "easyTrendline_0DTE",
                    "entry_type": "trendline_break",
                    "strategy_type": "trendline_0dte",
                    "source_path": "easyTrendline",
                    "trendline_slot": "trendline0",
                    "orb_box_source": "orb1_630_645",
                    "a1_policy": "tl0",
                    "orb_high": orb_ctx.get("orb_high"),
                    "orb_low": orb_ctx.get("orb_low"),
                    "candidate_source": "full_0dte_list",
                    "setup_type": selection.setup_type.value,
                    "trigger_direction": selection.trigger_direction,
                    "expected_option_side": selection.expected_option_side,
                    "trendline_structure_source": "tl0_structure_envelope",
                    "structure_fit_mse": selection.fit_mse,
                    "structure_alternate_fit_mse": selection.alternate_fit_mse,
                    "structure_selection_reason": selection.selection_reason,
                    "structure_display_label": getattr(selection, "structure_display_label", "") or "",
                    "structure_explainability": dict(getattr(selection, "explainability", None) or {}),
                    "line_distance_at_cutoff_pct": (
                        (getattr(selection, "explainability", None) or {}).get("line_distance_pct")
                    ),
                    "line_geometry": selection.direction.value,
                    "dual_lane_pair_id": dual_lane_id,
                    "dual_lane_role": peer_role,
                    "dual_lane_armed": False,
                    "geometry_dual_side_watch": True,
                }
                candidate_objects.append(
                    TrendlineCandidate(
                        symbol=sym,
                        direction=selection.direction,
                        qualified_at=now_utc,
                        priority_score=0.0,
                        confidence=0.0,
                        trendline=selection.trendline,
                        setup_payload=setup_base,
                        state=TrendlineCandidateState.WAITING_FOR_BUILD,
                    )
                )

        if not candidate_objects:
            log.warning(
                "TRENDLINE_PIPELINE | stage=tl0_build | action=empty | symbols=%d | reason=no_structure",
                len(structure_syms),
            )
            system._trendline0_ready = True  # attempt finished; allow retry hydrate later
            return 0

        # Inject via runtime path so TL1 morning init cannot wipe TL0 (keys differ).
        pending = list(getattr(system, "_pending_trendline_candidates", None) or [])
        pending_keys = {str(getattr(c, "candidate_id", "") or "") for c in pending}
        for cand in candidate_objects:
            bars = intraday_by_symbol.get(cand.symbol)
            key = eng.add_runtime_candidate(cand, bars=bars, now_utc=now_utc)
            if key:
                built += 1
                if key not in pending_keys:
                    pending.append(cand)
                    pending_keys.add(key)
        system._pending_trendline_candidates = pending
        system._trendline0_ready = True
        system._trendline_watch_active = True
        try:
            system._persist_trendline_geometry_now(
                candidate_objects,
                include_break_state=False,
                include_extreme_ts=False,
                metadata={
                    "source": "tl0_build",
                    "built_count": built,
                    "tl0_frozen_at": now_utc.isoformat(),
                },
            )
        except Exception as perr:
            log.warning("TRENDLINE_PIPELINE | stage=tl0_build | persist_failed=%s", perr)
        log.info(
            "TRENDLINE_PIPELINE | stage=tl0_build | action=complete | candidates=%d | symbols=%d",
            built,
            len(structure_syms),
        )
        return built
    finally:
        system._tl0_build_in_flight = False


async def maybe_run_tl0_build(system: Any) -> int:
    """Single-flight TL0 build when ORB1 ≥06:45 secure and not yet ready today."""
    if not getattr(system, "_enable_trendline_strategy", False):
        return 0
    if getattr(system, "_trendline0_ready", False) and (
        getattr(system, "trendline_signal_engine", None)
        and any(
            ":trendline0" in str(k)
            for k in (getattr(system.trendline_signal_engine, "candidates", None) or {})
        )
    ):
        return 0
    if getattr(system, "_tl0_build_in_flight", False):
        return 0
    if not getattr(system, "_orb1_memory_freeze_valid", lambda: False)():
        return 0
    # Clock gate: only at/after 06:45 PT
    now_pt = datetime.now(PT).time()
    if now_pt < time_cls(6, 45):
        return 0
    return await build_tl0_candidates_after_orb1(system)
