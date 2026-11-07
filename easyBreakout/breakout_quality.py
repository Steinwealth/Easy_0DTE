#!/usr/bin/env python3
"""
Breakout quality gates — aligned with Trendline simple quality + ORB Convex spirit.

Gates:
  - volume_ratio floor (fail closed when missing if configured)
  - VWAP side alignment (CALL not below VWAP; PUT not above; fail closed when missing)
  - VWAP extension reject (±15% default, Convex-style)
  - RSI floors/ceilings
  - extension-confidence ranker (observe / rank only — not a hard floor)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import List, Optional

from .breakout_confirmation import BreakoutConfirmResult
from .breakout_models import BreakoutCandidate, BreakoutConfig


@dataclass
class BreakoutQualityResult:
    score: float
    passed: bool
    failed_reasons: List[str] = field(default_factory=list)
    vwap_position: str = ""
    vwap_distance_pct: Optional[float] = None
    volume_ratio: Optional[float] = None
    rsi: Optional[float] = None
    macd_histogram: Optional[float] = None
    ema_12: Optional[float] = None
    ema_26: Optional[float] = None
    volume_ratio_missing: bool = False
    rsi_missing: bool = False
    macd_missing: bool = False


def _vwap_position(price: float, vwap: Optional[float]) -> str:
    if vwap is None or float(vwap) <= 0:
        return "unknown"
    if float(price) > float(vwap) * 1.0005:
        return "above"
    if float(price) < float(vwap) * 0.9995:
        return "below"
    return "at"


def _env_fail_open_vwap(cfg: BreakoutConfig) -> bool:
    """Prefer config; env override kept for ops toggles."""
    raw = os.getenv("BREAKOUT_VWAP_MISSING_FAIL_OPEN")
    if raw is not None and str(raw).strip() != "":
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    return bool(getattr(cfg, "vwap_missing_fail_open", False))


def compute_breakout_quality_score(
    cand: BreakoutCandidate,
    confirm: BreakoutConfirmResult,
    cfg: BreakoutConfig,
    *,
    price: float,
    volume_ratio: Optional[float] = None,
    vwap: Optional[float] = None,
    vwap_distance_pct: Optional[float] = None,
    rsi: Optional[float] = None,
    macd_histogram: Optional[float] = None,
    ema_12: Optional[float] = None,
    ema_26: Optional[float] = None,
    volume_ratio_missing: bool = False,
    vwap_missing: bool = False,
    rsi_missing: bool = False,
    macd_missing: bool = False,
) -> BreakoutQualityResult:
    failed: List[str] = []
    side = str(confirm.option_side or cand.option_side or "").lower()
    if volume_ratio is None:
        volume_ratio_missing = True if volume_ratio_missing else bool(
            isinstance(getattr(cand, "metadata", None), dict)
            and getattr(cand, "metadata", {}).get("volume_ratio_missing")
        )
    try:
        vol = float(volume_ratio if volume_ratio is not None else (cand.volume_ratio or 0.0))
    except (TypeError, ValueError):
        volume_ratio_missing = True
        vol = 0.0
    vwap_val = vwap if vwap is not None else cand.vwap
    rsi_val = rsi if rsi is not None else cand.rsi
    vd = vwap_distance_pct if vwap_distance_pct is not None else cand.vwap_distance_pct
    vwap_pos = _vwap_position(price, vwap_val)
    if vwap_missing or vwap_pos == "unknown":
        vwap_pos = "unknown"

    # Volume / VWAP / RSI / chase — score scaffolding only.
    # Sole hard admit is GATE_BO_* via quality_gate_stack (0DTEQualityGates §3.2).
    # Legacy scorer knives stay behind explicit env flags for replay only.
    min_vol = float(cfg.min_volume_ratio or 0.12)
    vol_gate_on = bool(getattr(cfg, "volume_ratio_gate_enabled", False))
    env_vol_gate = os.getenv("BREAKOUT_VOLUME_RATIO_GATE_ENABLED")
    if env_vol_gate is not None and str(env_vol_gate).strip() != "":
        vol_gate_on = str(env_vol_gate).strip().lower() in ("1", "true", "yes", "on")
    vol_fail_closed = bool(getattr(cfg, "volume_missing_fail_closed", True))
    if vol_gate_on:
        if vol_fail_closed and volume_ratio_missing:
            failed.append("volume_ratio_missing")
        elif vol < min_vol:
            failed.append(f"volume_ratio_below_min:{vol:.2f}<{min_vol:.2f}")

    # Aug 6 BO QG: side-aware MACD hold (CALL hist>0 · PUT hist<0). Fail closed when missing.
    macd_hold_raw = os.getenv("BREAKOUT_MACD_HOLD_GATE_ENABLED", "false")
    macd_hold_on = str(macd_hold_raw or "").strip().lower() in ("1", "true", "yes", "on")
    if macd_hold_on:
        if macd_missing or macd_histogram is None:
            failed.append("macd_hold_missing")
        else:
            try:
                hist = float(macd_histogram)
            except (TypeError, ValueError):
                hist = 0.0
                failed.append("macd_hold_missing")
            else:
                side_l = str(side or "").strip().lower()
                if side_l in ("put", "p"):
                    if hist >= 0:
                        failed.append(f"macd_hold_put_hist_nonneg:{hist:.4f}")
                else:
                    if hist <= 0:
                        failed.append(f"macd_hold_call_hist_nonpos:{hist:.4f}")

    # VWAP alignment — GATE_BO_VWAP owns admit (default scorer off).
    require_vwap = bool(cfg.require_vwap_alignment)
    env_vwap = os.getenv("BREAKOUT_REQUIRE_VWAP_ALIGNMENT")
    if env_vwap is not None and str(env_vwap).strip() != "":
        require_vwap = str(env_vwap).strip().lower() in ("1", "true", "yes", "on")
    if require_vwap:
        if vwap_pos == "unknown":
            if not _env_fail_open_vwap(cfg):
                failed.append("vwap_missing")
        elif side == "call" and vwap_pos == "below":
            failed.append("call_below_vwap")
        elif side == "put" and vwap_pos == "above":
            failed.append("put_above_vwap")

        # Extension only with legacy VWAP knife on (GATE owns extension otherwise).
        # Hub stamps ``vwap_distance_pct`` in **percent** (e.g. 0.38 = 0.38%).
        # Env lim is a **fraction** (0.15 = 15%).
        ext_lim = float(cfg.vwap_extension_reject_pct or 0.15)
        if ext_lim > 1.0:
            ext_lim = ext_lim / 100.0
        if vd is not None:
            try:
                vd_pct = float(vd)
                vd_frac = vd_pct / 100.0  # always percent → fraction
                if side == "call" and vd_frac > ext_lim:
                    failed.append(f"vwap_extension_reject:{vd_pct:.4f}>{ext_lim * 100.0:.4f}")
                if side == "put" and vd_frac < -ext_lim:
                    failed.append(f"vwap_extension_reject:{vd_pct:.4f}<-{ext_lim * 100.0:.4f}")
            except (TypeError, ValueError):
                pass

    # RSI momentum floors — GATE_BO_RSI_band=off; anti knives are path-opt on TL/ORB.
    rsi_missing_flag = bool(rsi_missing) or bool(getattr(cand, "rsi_missing", False))
    md = getattr(cand, "metadata", None)
    if isinstance(md, dict) and md.get("rsi_missing"):
        rsi_missing_flag = True
    if rsi_val is not None and not rsi_missing_flag:
        try:
            rsi_f = float(rsi_val)
            call_floor = float(cfg.call_min_rsi)
            put_ceil = float(cfg.put_max_rsi)
            # Floors 0/100 = scaffolding only (A7 momentum RSI OFF).
            if call_floor > 0 and side == "call" and rsi_f < call_floor:
                failed.append(f"rsi_below_call_floor:{rsi_f:.1f}<{call_floor:.1f}")
            if put_ceil < 100 and side == "put" and rsi_f > put_ceil:
                failed.append(f"rsi_above_put_ceiling:{rsi_f:.1f}>{put_ceil:.1f}")
        except (TypeError, ValueError):
            pass

    # Chase cap — GATE_BO_break_max owns hard reject (scorer stamp only).
    # Legacy hard chase remains available only when BREAKOUT_QUALITY_CHASE_GATE=true.
    chase_scorer = str(os.getenv("BREAKOUT_QUALITY_CHASE_GATE", "false") or "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    max_dist = float(getattr(cfg, "max_break_distance_pct", 0.0) or 0.0)
    dist_now = float(confirm.break_distance_pct or 0.0)
    if chase_scorer and max_dist > 0 and dist_now > max_dist:
        failed.append(f"break_distance_chase:{dist_now:.4f}>{max_dist:.4f}")

    # Score composition — shared extension-confidence ranker (0–100).
    # Not a hard floor. ATR is fuel (higher = more likely to hold beyond the box).
    from easy0DTE.modules.breakout_extension_score import compute_breakout_extension_score

    dist = float(confirm.break_distance_pct or 0.0)
    score, ext_fields = compute_breakout_extension_score(
        volume_ratio=None if volume_ratio_missing else vol,
        break_distance_pct=dist,
        direction_volume_pct=(
            cand.metadata.get("direction_volume_pct")
            if isinstance(cand.metadata, dict)
            else None
        ),
        atr=(cand.metadata.get("atr") if isinstance(cand.metadata, dict) else None),
        price=price,
    )
    if confirm.immediate_reclaim:
        failed.append("immediate_reclaim")
        score = min(score, 40.0)
    score = max(0.0, min(100.0, score))

    # Confirmation / reclaim failures also block (Layer A geometry — not equity QG).
    for reason in confirm.failed_reasons or []:
        if reason not in failed and reason not in (
            "awaiting_follow_through",
            "follow_through_reset_not_beyond_trigger",
        ):
            failed.append(reason)

    passed = not failed and bool(confirm.ready)
    cand.quality_score = score
    if not volume_ratio_missing:
        cand.volume_ratio = vol
    cand.vwap = vwap_val
    cand.rsi = rsi_val
    if isinstance(cand.metadata, dict):
        cand.metadata["macd_histogram"] = None if macd_missing else macd_histogram
        cand.metadata["macd_missing"] = bool(macd_missing or macd_histogram is None)
        cand.metadata["ema_12"] = ema_12
        cand.metadata["ema_26"] = ema_26
        cand.metadata["volume_ratio_missing"] = bool(volume_ratio_missing)
        cand.metadata["rsi_missing"] = bool(rsi_missing_flag)
        cand.metadata["extension_score"] = float(score)
        cand.metadata.update({k: v for k, v in ext_fields.items() if k != "extension_score"})
    cand.failed_reasons = list(failed)
    return BreakoutQualityResult(
        score=score,
        passed=passed,
        failed_reasons=failed,
        vwap_position=vwap_pos,
        vwap_distance_pct=vd,
        volume_ratio=None if volume_ratio_missing else vol,
        rsi=rsi_val,
        macd_histogram=None if macd_missing else macd_histogram,
        ema_12=ema_12,
        ema_26=ema_26,
        volume_ratio_missing=bool(volume_ratio_missing),
        rsi_missing=bool(rsi_missing_flag),
        macd_missing=bool(macd_missing or macd_histogram is None),
    )
