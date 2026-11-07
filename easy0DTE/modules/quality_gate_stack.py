#!/usr/bin/env python3
"""
Quality Gate Stack — singular admit brain for ORB / Trendline / Breakout.

Trade Profile (`trade_profile`) is the signal accept/reject. Call SS and Put SS
stay on the stack as skip signals and default off. Entry, Delta Moon, chain-share
dir%, put/call OI ratio, and the old equity and contract gates are not the admit.

One catalog + per-path STACKS + GateReport. Path discovery fills GateContext;
``admit_pre_exec`` owns pre-exec admit. ConvexEligibilityFilter is a thin ORB
shim that maps GateReport → legacy (ok, reasons, rejects).

Vocabulary:
  equity_volume_ratio   — stock tape vs average (Layer B)
  direction_volume_pct  — same-side chain call/put share (Layer C)
  contract_volume / contract_oi — single-strike liquidity (Layer D)

Env modes (optional; default hard):
  GATE_<gate_id>=hard|observe|off
  GATE_ORB_<gate_id>=... / GATE_TL_<gate_id>=... / GATE_BO_<gate_id>=...
  QUALITY_GATE_STACK_SHADOW=true  — log GATE_STACK_REPORT
  QUALITY_GATE_STACK_CUTOVER=true — TL/BO Layer A/B admit driven by stack
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)

PATH_ORB = "orb_0dte"
PATH_TL = "trendline_0dte"
PATH_BO = "breakout_0dte"

ORB_EQUITY_VOLUME_RATIO_MIN_DEFAULT = 0.012


@dataclass
class GateResult:
    gate_id: str
    layer: str
    status: str  # pass | reject | skip | observe
    reason: str
    fields: Dict[str, Any] = field(default_factory=dict)

    @property
    def hard_reject(self) -> bool:
        return self.status == "reject"


@dataclass
class GateReport:
    path: str
    results: List[GateResult] = field(default_factory=list)
    admitted: bool = True
    deferred: bool = False
    symbol: str = ""
    side: str = ""

    def first_reject(self) -> Optional[GateResult]:
        for r in self.results:
            if r.status == "reject":
                return r
        return None

    def to_dict(self) -> Dict[str, Any]:
        fr = self.first_reject()
        return {
            "path": self.path,
            "symbol": self.symbol,
            "side": self.side,
            "admitted": self.admitted,
            "deferred": self.deferred,
            "first_reject": (
                None
                if fr is None
                else {
                    "gate_id": canonicalize_gate_id(fr.gate_id),
                    "reason": fr.reason,
                    "layer": fr.layer,
                }
            ),
            "results": [
                {
                    "gate_id": canonicalize_gate_id(r.gate_id),
                    "layer": r.layer,
                    "status": r.status,
                    "reason": r.reason,
                    "fields": r.fields,
                }
                for r in self.results
            ],
        }


@dataclass
class GateContext:
    """Unified evaluation context — singular stamp for ORB / TL / BO."""

    path: str
    symbol: str = ""
    side: str = ""  # CALL | PUT
    signal: Dict[str, Any] = field(default_factory=dict)
    # Session tech (first-class — never invent defaults)
    equity_volume_ratio: Optional[float] = None
    equity_volume_ratio_min: Optional[float] = None
    vwap_distance_pct: Optional[float] = None
    vwap_position: Optional[str] = None
    rsi: Optional[float] = None
    macd_histogram: Optional[float] = None
    ema_12: Optional[float] = None
    ema_26: Optional[float] = None
    quality_score: Optional[float] = None
    quality_score_min: Optional[float] = None
    quality_failed_reasons: List[str] = field(default_factory=list)
    put_call_oi_ratio: Optional[float] = None
    call_chain_volume: Optional[float] = None
    put_chain_volume: Optional[float] = None
    direction_volume_pct: Optional[float] = None
    # Geometry / confirmation
    break_distance_pct: Optional[float] = None
    range_pct: Optional[float] = None
    follow_through_bars: Optional[int] = None
    continuation_pct: Optional[float] = None
    post_break_chop_pct: Optional[float] = None
    geom_ready: Optional[bool] = None
    geom_reason: str = ""
    followthrough_ok: Optional[bool] = None
    followthrough_reason: str = ""
    # Contract / rank / regime
    delta: Optional[float] = None
    contract_volume: Optional[float] = None
    contract_oi: Optional[float] = None
    premium: Optional[float] = None
    spread_pct: Optional[float] = None
    otm_pct: Optional[float] = None
    contract_qty: Optional[int] = None
    priority_score: Optional[float] = None
    protection_day: Optional[bool] = None
    red_day_blocked: Optional[bool] = None
    red_day_reason: str = ""
    extras: Dict[str, Any] = field(default_factory=dict)

    def as_chain_signal(self) -> Dict[str, Any]:
        row = dict(self.signal or {})
        side = (self.side or row.get("side") or row.get("direction") or "CALL").upper()
        if side in ("C", "LONG", "BULLISH"):
            side = "CALL"
        elif side in ("P", "SHORT", "BEARISH"):
            side = "PUT"
        row.setdefault("symbol", self.symbol)
        row["side"] = side
        row["direction"] = side
        row["option_type"] = side.lower()
        if self.put_call_oi_ratio is not None:
            row["put_call_oi_ratio"] = self.put_call_oi_ratio
        if self.call_chain_volume is not None:
            row["call_chain_volume"] = self.call_chain_volume
        if self.put_chain_volume is not None:
            row["put_chain_volume"] = self.put_chain_volume
        if self.direction_volume_pct is not None:
            row["direction_volume_pct"] = self.direction_volume_pct
        if self.equity_volume_ratio is not None and row.get("volume_ratio") is None:
            row["volume_ratio"] = self.equity_volume_ratio
        if self.vwap_distance_pct is not None and row.get("vwap_distance_pct") is None:
            row["vwap_distance_pct"] = self.vwap_distance_pct
        if self.macd_histogram is not None and row.get("macd_histogram") is None:
            row["macd_histogram"] = self.macd_histogram
        if self.ema_12 is not None and row.get("ema_12") is None:
            row["ema_12"] = self.ema_12
        if self.ema_26 is not None and row.get("ema_26") is None:
            row["ema_26"] = self.ema_26
        if self.rsi is not None and row.get("rsi") is None:
            row["rsi"] = self.rsi
        if self.priority_score is not None and row.get("priority_score") is None:
            row["priority_score"] = self.priority_score
        if self.protection_day is not None:
            row["protection_day"] = bool(self.protection_day)
        if self.break_distance_pct is not None and row.get("break_distance_pct") is None:
            row["break_distance_pct"] = self.break_distance_pct
        if self.range_pct is not None and row.get("orb_range_pct") is None:
            row["orb_range_pct"] = self.range_pct
        return row


# Path names. The pre-chain gate list is SINGLE_TP_PRE_STACKS.
STACKS: Dict[str, List[str]] = {
    PATH_ORB: ["trade_profile"],
    PATH_TL: ["trade_profile"],
    PATH_BO: ["trade_profile"],
}


# Pre-chain admit keeps the signal definition (geometry) and ranking.
# Quality is the one Trade Profile. Contract feasibility is the one picker.
# Skip Signals run at chain time.
SINGLE_TP_PRE_STACKS: Dict[str, List[str]] = {
    PATH_ORB: ["orb_break", "orb_range", "regime", "PS", "top_n"],
    PATH_TL: ["tl_break"],
    PATH_BO: ["bo_confirm"],
}
# TL/BO Layer A/B: geometry + the CALL Skip Signal early-out (no chain needed,
# saves the chain fetch). PUT SS needs chain volume and runs at chain time.
SINGLE_TP_EQUITY_GEOM_GATES: Dict[str, List[str]] = {
    PATH_TL: ["tl_break", "call_ss"],
    PATH_BO: ["bo_confirm", "call_ss"],
}


def active_stacks() -> Dict[str, List[str]]:
    return SINGLE_TP_PRE_STACKS

CONTRACT_GATES = frozenset(
    {
        "delta",
        "liq",
        "spread",
        "OTM",
        "cheap_lotto",
    }
)

# Post-admit / post-rank only — SO may stamp priority_score before 0DTE re-rank.
RANK_GATES = frozenset({"PS", "top_n"})

# Long catalog ids (pre Aug 13 short-key cutover) → sole GATE_* / stack ids.
GATE_ID_ALIASES: Dict[str, str] = {
    "geom.tl_break": "tl_break",
    "geom.tl_followthrough": "tl_ft",
    "geom.orb_break": "orb_break",
    "geom.bo_confirm": "bo_confirm",
    "equity.score_floor": "score",
    "equity.volume_ratio": "Vol_Ratio",
    "equity.vwap_align": "VWAP",
    "equity.rsi_band": "RSI_band",
    "equity.rsi_anti_chase": "RSI",
    "equity.bb_anti_stretch": "BB",
    "equity.bb_mid_stretch": "BB_mid",
    "equity.rsi_stamp": "RSI_stamp",
    "equity.macd_hist": "MACD",
    "equity.bollinger_upper": "BB_upper",
    "equity.bollinger_middle": "BB_middle",
    "equity.bollinger_lower": "BB_lower",
    "equity.bollinger_width": "BB_width",
    "equity.bollinger_position": "BB_pctB",
    "chain.pc_oi": "pc_oi",
    "chain.dir_pct": "dir_pct",
    "chain.call_volume": "call_vol",
    "chain.put_volume": "put_vol",
    "policy.red_day": "red_day",
    "contract.cheap_lotto": "cheap_lotto",
    "contract.delta_band": "delta",
    "contract.delta_abs_max": "delta_abs",
    "contract.iv_max": "IV",
    "contract.liquidity": "liq",
    "contract.otm": "OTM",
    "contract.spread": "spread",
    "chase": "break_max",
    "geom.bo_chase_max": "break_max",
    # Display name for the v2.5 MACD histogram deadband (not the observe MACD stamp).
    "v25": "MACD_noise",
}


def canonicalize_gate_id(gate_id: str) -> str:
    g = str(gate_id or "").strip()
    return GATE_ID_ALIASES.get(g, g)


def canonicalize_quality_gate_report(report: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Rewrite nested gate_id fields to short stack ids for PO snapshots."""
    if not isinstance(report, dict):
        return report
    out = dict(report)
    fr = out.get("first_reject")
    if isinstance(fr, dict) and fr.get("gate_id"):
        fr = dict(fr)
        fr["gate_id"] = canonicalize_gate_id(str(fr.get("gate_id")))
        out["first_reject"] = fr
    rows = []
    for r in out.get("results") or []:
        if not isinstance(r, dict):
            rows.append(r)
            continue
        rr = dict(r)
        if rr.get("gate_id"):
            rr["gate_id"] = canonicalize_gate_id(str(rr.get("gate_id")))
        rows.append(rr)
    if out.get("results") is not None:
        out["results"] = rows
    return out


LAYER_BY_GATE: Dict[str, str] = {
    "orb_break": "A",
    "orb_range": "A",
    "regime": "A",
    "v25": "A",
    "MACD_noise": "A",
    "e14": "A",
    "e10_e11": "A",
    "e7": "A",
    "chase_skip": "A",
    "tl_break": "A",
    "tl_ft": "A",
    "bo_confirm": "A",
    "Vol_Ratio": "B",
    "VWAP": "B",
    "RSI_band": "B",
    "RSI": "B",
    "BB": "B",
    "BB_mid": "B",
    "RSI_stamp": "B",
    "MACD": "B",
    "score": "B",
    "break_max": "A",
    "chase": "A",
    "BB_upper": "C",
    "BB_middle": "C",
    "BB_lower": "C",
    "BB_width": "C",
    "BB_pctB": "C",
    "pc_oi": "C",
    "dir_pct": "C",
    "trade_profile": "SS",
    "entry_part": "SS",
    "call_ss": "SS",
    "put_ss": "SS",
    "path_ss": "SS",
    "call_vol": "C",
    "put_vol": "C",
    "delta": "D",
    "liq": "D",
    "spread": "D",
    "OTM": "D",
    "cheap_lotto": "D",
    "PS": "rank",
    "top_n": "rank",
    "red_day": "policy",
}

# Research stamps — default observe until profitable levels are locked (env can promote to hard).
STAMP_OBSERVE_GATES = frozenset(
    {
        "RSI_stamp",
        "MACD",
        "BB_upper",
        "BB_middle",
        "BB_lower",
        "BB_width",
        "BB_pctB",
        "call_vol",
        "put_vol",
    }
)

# TL-QG-LS1 + Aug 13 collect: missing GATE_TL_* must not resurrect dual RSI / hard VWAP / hard RSI / hard BB.
# Env still wins. See 0DTEQualityGates.md §5.
_TL_QG_DEFAULT_MODES = {
    "VWAP": "observe",
    "RSI": "observe",
    "BB": "observe",
    "Vol_Ratio": "off",
    "dir_pct": "off",
    "trade_profile": "hard",
    "entry_part": "off",
    "call_ss": "off",
    "put_ss": "off",
    "path_ss": "off",
}

# ORB2 BO: VWAP observe. Score observe (extension ranker, not a floor).
# BB_mid observe Aug 26 — stamp CALL≥50 / PUT≤50; do not hard-block.
# ATR / IV / soft-break / extra |δ| cap removed Aug 17 — not on the stack.
_BO_QG_DEFAULT_MODES = {
    "VWAP": "observe",
    "score": "observe",
    "BB_mid": "observe",
    "trade_profile": "hard",
    "entry_part": "off",
    "call_ss": "off",
    "put_ss": "off",
    "path_ss": "off",
}

# ORB1: VWAP/RSI observe (Aug 14 collection). Score observe (same extension ranker).
# Missing GATE_ORB_* must not resurrect LS1 hard. Env still wins.
_ORB_QG_DEFAULT_MODES = {
    "VWAP": "observe",
    "RSI": "observe",
    "score": "observe",
    "trade_profile": "hard",
    "entry_part": "off",
    "call_ss": "off",
    "put_ss": "off",
    "path_ss": "off",
}

PATH_POLICY = {
    PATH_ORB: {"on_layer_c_reject": "drop_from_pool", "on_layer_b_reject": "drop"},
    PATH_TL: {"on_layer_c_reject": "slot_side_cooldown", "on_layer_b_reject": "slot_side_cooldown"},
    PATH_BO: {"on_layer_c_reject": "side_day_block", "on_layer_b_reject": "side_day_block"},
}


def _env_bool(key: str, default: bool = False) -> bool:
    raw = os.getenv(key)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _env_float(key: str, default: float) -> float:
    raw = os.getenv(key)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _env_float_opt(key: str) -> Optional[float]:
    """Config-only float. Missing / blank → None (do not invent a Quality Gate level)."""
    raw = os.getenv(key)
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _env_str_opt(key: str) -> Optional[str]:
    raw = os.getenv(key)
    if raw is None or str(raw).strip() == "":
        return None
    return str(raw).strip()


def _env_float_first(keys: Sequence[str], default: float) -> float:
    for key in keys:
        raw = os.getenv(key)
        if raw is None or str(raw).strip() == "":
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return default


def _path_prefix(path: str) -> str:
    if path == PATH_ORB:
        return "ORB"
    if path == PATH_TL:
        return "TL"
    if path == PATH_BO:
        return "BO"
    return "ORB"


def _mode_from_raw(raw: Optional[str]) -> Optional[str]:
    if raw is None or str(raw).strip() == "":
        return None
    mode = str(raw).strip().lower()
    if mode in ("skip", "disabled"):
        return "off"
    if mode in ("hard", "observe", "off"):
        return mode
    return None


# Symbol-metric and contract bands the Trade Profile owns. They do not run a
# second accept/reject. The profile reads same-side chain volume, same-side
# open interest, and dir_pct_change_5m. The old chain-share dir% gate and the
# put/call OI ratio gate are not on the stack.
_GATES_OWNED_BY_TRADE_PROFILE = frozenset(
    {
        "dir_pct",
        "pc_oi",
        "Vol_Ratio",
        "orb_break",
        "orb_range",
        "regime",
        "tl_break",
        "bo_confirm",
        "break_max",
        "chase",
        "RSI",
        "RSI_band",
        "VWAP",
        "BB",
        "BB_mid",
        "delta",
        "liq",
        "spread",
        "OTM",
        "cheap_lotto",
        "entry_part",
        "path_ss",
    }
)


def resolve_gate_mode(path: str, gate_id: str) -> str:
    """hard | observe | off — GATE_{ORB|TL|BO}_{id} then GATE_{id}."""
    prefix = _path_prefix(path)
    ids = [gate_id]
    if gate_id == "break_max":
        ids.append("chase")
    elif gate_id == "chase":
        ids.append("break_max")
    elif gate_id == "MACD_noise":
        ids.append("v25")
    elif gate_id == "v25":
        ids.append("MACD_noise")
    for gid in ids:
        for key in (f"GATE_{prefix}_{gid}", f"GATE_{gid}"):
            parsed = _mode_from_raw(os.getenv(key))
            if parsed is not None:
                return parsed
    if gate_id in _GATES_OWNED_BY_TRADE_PROFILE:
        return "off"
    if gate_id in STAMP_OBSERVE_GATES:
        return "observe"
    if path == PATH_TL and gate_id in _TL_QG_DEFAULT_MODES:
        return _TL_QG_DEFAULT_MODES[gate_id]
    if path == PATH_BO and gate_id in _BO_QG_DEFAULT_MODES:
        return _BO_QG_DEFAULT_MODES[gate_id]
    if path == PATH_ORB and gate_id in _ORB_QG_DEFAULT_MODES:
        return _ORB_QG_DEFAULT_MODES[gate_id]
    return "hard"


def tl_dir_pct_handoff_enabled() -> bool:
    """Pre-exec hydrate + Trade Profile. Stays on unless the profile gate is off."""
    return resolve_gate_mode(PATH_TL, "trade_profile") != "off"


def _dir_min_for_path(path: str) -> float:
    if path == PATH_TL:
        # Live TL0–TL3: TRENDLINE_DIR_VOLUME_PCT=0.10. Default 0.54 if env missing.
        return _env_float("TRENDLINE_DIR_VOLUME_PCT", 0.54)
    if path == PATH_BO:
        return _env_float("BREAKOUT_DIR_VOLUME_MIN_PCT", 0.54)
    return _env_float("0DTE_DIR_VOLUME_MIN_PCT", 0.54)


def _equity_vol_min(ctx: GateContext) -> float:
    if ctx.equity_volume_ratio_min is not None:
        return float(ctx.equity_volume_ratio_min)
    if ctx.path == PATH_ORB:
        return orb_equity_volume_ratio_min(ctx.side)
    if ctx.path == PATH_TL:
        return _env_float("GATE_TL_Vol_Ratio_MIN", 0.012)
        return _env_float("BREAKOUT_MIN_VOLUME_RATIO", 0.012)


def orb_equity_volume_ratio_min(side: Optional[str] = None) -> float:
    normalized = str(side or "CALL").strip().upper()
    if normalized in ("PUT", "P", "SHORT", "BEARISH"):
        return _env_float(
            "0DTE_HARD_GATE_BREAKDOWN_VOL_FLOOR",
            ORB_EQUITY_VOLUME_RATIO_MIN_DEFAULT,
        )
    return _env_float(
        "0DTE_HARD_GATE_CONTINUATION_VOL_FLOOR",
        ORB_EQUITY_VOLUME_RATIO_MIN_DEFAULT,
    )


def _reason_num(value: Any, nd: int) -> str:
    """Compact numeric token with no extra colons (Telegram Reason)."""
    s = f"{float(value):.{int(nd)}f}"
    if nd > 0:
        s = s.rstrip("0").rstrip(".")
    return s


def _ok(gate_id: str, reason: str, **fields: Any) -> GateResult:
    return GateResult(gate_id, LAYER_BY_GATE.get(gate_id, "?"), "pass", reason, dict(fields))


def _rej(gate_id: str, reason: str, **fields: Any) -> GateResult:
    return GateResult(gate_id, LAYER_BY_GATE.get(gate_id, "?"), "reject", reason, dict(fields))


def _skip(gate_id: str, reason: str, **fields: Any) -> GateResult:
    return GateResult(gate_id, LAYER_BY_GATE.get(gate_id, "?"), "skip", reason, dict(fields))


def _defer(gate_id: str, reason: str, **fields: Any) -> GateResult:
    """Block handoff without a hard reject. Callers must not cooldown the break."""
    return GateResult(gate_id, LAYER_BY_GATE.get(gate_id, "?"), "defer", reason, dict(fields))


def _eval_chain_pc_oi(ctx: GateContext) -> GateResult:
    from easy0DTE.modules.convex_eligibility_filter import pc_oi_confirmation_pass

    row = ctx.as_chain_signal()
    ok, msg = pc_oi_confirmation_pass(row)
    fields = {"put_call_oi_ratio": row.get("put_call_oi_ratio"), "side": row.get("side")}
    return _ok("pc_oi", msg, **fields) if ok else _rej("pc_oi", msg, **fields)


def _eval_chain_dir_pct(ctx: GateContext) -> GateResult:
    from easy0DTE.modules.convex_eligibility_filter import (
        direction_volume_confirmation_pass,
        same_side_direction_volume_pct,
    )

    row = ctx.as_chain_signal()
    floor = _signal_float(
        row,
        "dir_volume_min_pct",
        _dir_min_for_path(ctx.path),
    )
    if ctx.path == PATH_TL:
        enabled = True  # GATE_TL_dir_pct mode already applied in run_stack
    elif ctx.path == PATH_BO:
        enabled = _signal_bool(
            row,
            "dir_volume_gate_enabled",
            _env_bool("BREAKOUT_DIR_VOLUME_GATE_ENABLED", True),
        )
    else:
        enabled = _signal_bool(
            row,
            "dir_volume_gate_enabled",
            _env_bool("0DTE_DIR_VOLUME_GATE_ENABLED", True),
        )
    ok, msg = direction_volume_confirmation_pass(row, min_pct=floor, enabled=enabled)
    dir_pct = same_side_direction_volume_pct(row)
    fields = {
        "direction_volume_pct": dir_pct,
        "call_chain_volume": row.get("call_chain_volume"),
        "put_chain_volume": row.get("put_chain_volume"),
        "dir_pct_min": floor,
    }
    if enabled and _dir_pct_defer_on_missing():
        # Oct 13 2026: missing / skipped / queued / stale chain is a data wait,
        # never a reject (and never a cooldown). Only a real value < floor rejects.
        attempt = str(row.get("chain_attempt") or "")
        stale = _dir_pct_chain_stale(row)
        if dir_pct is None or attempt.startswith("skipped") or stale:
            fields["chain_attempt"] = attempt or None
            fields["chain_unavailable_reason"] = row.get("chain_unavailable_reason") or (
                "chain_stale" if stale else (attempt or "chain_missing")
            )
            return _defer("dir_pct", "required_data_not_ready", **fields)
    return _ok("dir_pct", msg, **fields) if ok else _rej("dir_pct", msg, **fields)


def _dir_pct_defer_on_missing() -> bool:
    try:
        from central_tech.chain_fetch_manager import manager_enabled

        return bool(manager_enabled())
    except Exception:
        return False


def _dir_pct_chain_stale(row: Dict[str, Any]) -> bool:
    """True when the chain behind dir%% is older than the TP chain cap."""
    try:
        age = row.get("tp_chain_age_sec")
        if age is None:
            return False
        from central_tech.decision_freeze import chain_max_age_sec

        return float(age) > float(chain_max_age_sec())
    except Exception:
        return False


def _vol_missing_fail_closed(ctx: GateContext) -> bool:
    """ORB/TL first-hour: skip missing ratio. BO stays fail-closed."""
    extra = (ctx.extras or {}).get("volume_missing_fail_closed")
    if extra is True:
        return True
    if extra is False:
        return False
    if ctx.path == PATH_BO:
        return _env_bool("BREAKOUT_VOLUME_MISSING_FAIL_CLOSED", True)
    if ctx.path == PATH_TL:
        return _env_bool("TL_VOLUME_MISSING_FAIL_CLOSED", False)
    return _env_bool("ORB_VOLUME_MISSING_FAIL_CLOSED", False)


def _volume_proxy_fields(ctx: GateContext) -> Dict[str, Any]:
    """Stamp whatever tape we have when equity ratio is not trusted."""
    fields: Dict[str, Any] = {"volume_ratio_missing": True}
    sig = ctx.signal or {}
    extras = ctx.extras or {}
    for key in (
        "session_bar_count",
        "session_volume",
        "volume_1m",
        "volume_5m",
        "session_vs_avg_volume",
        "volume_ratio_source",
        "tech_source",
    ):
        val = extras.get(key)
        if val is None:
            val = sig.get(key)
        if val is not None:
            fields[key] = val
    if ctx.call_chain_volume is not None:
        fields["call_chain_volume"] = ctx.call_chain_volume
    if ctx.put_chain_volume is not None:
        fields["put_chain_volume"] = ctx.put_chain_volume
    if ctx.direction_volume_pct is not None:
        fields["direction_volume_pct"] = ctx.direction_volume_pct
    return fields


def _eval_equity_volume_ratio(ctx: GateContext) -> GateResult:
    # Trust flag first — invent 1.0 must not pass/fail as real tape.
    missing = bool(ctx.extras.get("volume_ratio_missing"))
    if not missing and ctx.signal:
        missing = bool(ctx.signal.get("volume_ratio_missing"))
    if missing:
        fields = _volume_proxy_fields(ctx)
        from easy0DTE.modules.decision_required import (
            VOLUME_RATIO_MIN_BARS,
            clock_ready,
            session_bar_count,
        )

        if clock_ready(session_bar_count(ctx), VOLUME_RATIO_MIN_BARS) is True:
            return _rej("Vol_Ratio", "required_session_data_error", **fields)
        if _vol_missing_fail_closed(ctx):
            return _rej("Vol_Ratio", "missing", **fields)
        # ORB1 / TL0: first minutes often lack ≥2 usable 1m volumes. Skip the
        # ratio knife so geometry + side gates (dir%, skip signals) can admit.
        # PO entry snaps still try to hydrate session/chain volume.
        return _skip("Vol_Ratio", "missing", **fields)
    vol = ctx.equity_volume_ratio
    if vol is None and ctx.signal:
        raw = ctx.signal.get("volume_ratio") or ctx.signal.get("orb_volume_ratio")
        try:
            vol = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            vol = None
    if vol is None:
        from easy0DTE.modules.decision_required import (
            VOLUME_RATIO_MIN_BARS,
            clock_ready,
            session_bar_count,
        )

        if clock_ready(session_bar_count(ctx), VOLUME_RATIO_MIN_BARS) is True:
            return _rej("Vol_Ratio", "required_session_data_error")
        return _skip("Vol_Ratio", "absent")
    floor = _equity_vol_min(ctx)
    fields = {"equity_volume_ratio": vol, "equity_volume_ratio_min": floor}
    if float(vol) >= floor:
        return _ok(
            "Vol_Ratio",
            f"{float(vol):.3f}>={float(floor):.3f}",
            **fields,
        )
    return _rej(
        "Vol_Ratio",
        f"{float(vol):.3f}<{float(floor):.3f}",
        **fields,
    )


def _eval_equity_vwap(ctx: GateContext) -> GateResult:
    side = (ctx.side or "").upper()
    if side in ("LONG", "C"):
        side = "CALL"
    elif side in ("SHORT", "P"):
        side = "PUT"
    pos = (ctx.vwap_position or "").lower()
    vd = ctx.vwap_distance_pct
    if vd is None and ctx.signal:
        try:
            vd = float(ctx.signal.get("vwap_distance_pct"))
        except (TypeError, ValueError):
            vd = None
    fields: Dict[str, Any] = {
        "vwap_position": pos or None,
        "vwap_distance_pct": vd,
        "side": side,
    }
    if not pos and vd is None:
        if ctx.extras.get("vwap_missing_fail_closed"):
            return _rej("VWAP", "missing", **fields)
        return _skip("VWAP", "absent")
    if not pos and vd is not None:
        pos = "above" if vd >= 0 else "below"
        fields["vwap_position"] = pos
    if side == "CALL" and pos == "below":
        return _rej("VWAP", "call_below", **fields)
    if side == "PUT" and pos == "above":
        return _rej("VWAP", "put_above", **fields)
    # TL-QG-LS1: reject VWAP "at" (not clearly above/below).
    reject_at = bool(ctx.extras.get("vwap_reject_at")) or (
        ctx.path == PATH_TL and _env_bool("QG_TL_VWAP_REJECT_AT", True)
    )
    if reject_at and pos == "at":
        return _rej("VWAP", "at", **fields)
    ext = ctx.extras.get("vwap_extension_reject")
    if ext is None:
        # Path defaults — ORB1-QG-LS1 |vwap|≤1.0; BO/TL use path env when set.
        if ctx.path == PATH_ORB:
            ext = _env_float("ORB_VWAP_EXTENSION_REJECT_PCT", 1.0)
        elif ctx.path == PATH_BO:
            ext = _env_float("BREAKOUT_VWAP_EXTENSION_REJECT_PCT", 0.15)
        elif ctx.path == PATH_TL:
            ext = _env_float("TRENDLINE_VWAP_EXTENSION_REJECT_PCT", 0.0) or None
    if ext is not None and vd is not None:
        lim = float(ext)
        vd_pct = float(vd)
        # ORB1-LS1: env/extra is percent points (1.0 = 1%). BO legacy: fraction 0.15 = 15%.
        if ctx.path == PATH_ORB or bool(ctx.extras.get("vwap_extension_is_percent")):
            lim_pct = lim
        else:
            lim_pct = lim * 100.0 if lim <= 1.0 else lim
            if abs(vd_pct) <= 2.0 and lim_pct >= 5.0:
                vd_pct = vd_pct * 100.0
        fields["vwap_extension_limit_pct"] = lim_pct
        # ORB1-LS1: absolute extension both sides.
        if ctx.path == PATH_ORB and abs(vd_pct) > lim_pct + 1e-9:
            return _rej(
                "VWAP",
                f"ext_{_reason_num(abs(vd_pct), 2)}>{_reason_num(lim_pct, 2)}",
                **fields,
            )
        if ctx.path != PATH_ORB:
            if side == "CALL" and vd_pct > lim_pct:
                return _rej(
                    "VWAP",
                    f"ext_{_reason_num(vd_pct, 2)}>{_reason_num(lim_pct, 2)}",
                    **fields,
                )
            if side == "PUT" and vd_pct < -lim_pct:
                return _rej(
                    "VWAP",
                    f"ext_{_reason_num(abs(vd_pct), 2)}>{_reason_num(lim_pct, 2)}",
                    **fields,
                )
    return _ok("VWAP", f"aligned_{side}_{pos}", **fields)


def _eval_equity_rsi(ctx: GateContext) -> GateResult:
    if ctx.extras.get("rsi_missing") or (ctx.signal and ctx.signal.get("rsi_missing")):
        return _skip("RSI_band", "rsi_missing")
    rsi = ctx.rsi
    if rsi is None and ctx.signal:
        try:
            rsi = float(ctx.signal.get("rsi"))
        except (TypeError, ValueError):
            rsi = None
    if rsi is None:
        return _skip("RSI_band", "absent")
    side = (ctx.side or "").upper()
    if side in ("LONG", "C"):
        side = "CALL"
    elif side in ("SHORT", "P"):
        side = "PUT"
    call_min = _env_float("BREAKOUT_CALL_MIN_RSI", 0.0)
    put_max = _env_float("BREAKOUT_PUT_MAX_RSI", 100.0)
    fields = {"rsi": rsi, "side": side, "call_min": call_min, "put_max": put_max}
    if side == "CALL" and float(rsi) < call_min:
        return _rej(
            "RSI_band",
            f"call_{_reason_num(rsi, 1)}<{_reason_num(call_min, 1)}",
            **fields,
        )
    if side == "PUT" and float(rsi) > put_max:
        return _rej(
            "RSI_band",
            f"put_{_reason_num(rsi, 1)}>{_reason_num(put_max, 1)}",
            **fields,
        )
    return _ok("RSI_band", f"ok_{_reason_num(rsi, 1)}", **fields)


def _normalize_side(side: Optional[str]) -> str:
    s = (side or "").upper()
    if s in ("LONG", "C", "BULLISH"):
        return "CALL"
    if s in ("SHORT", "P", "BEARISH"):
        return "PUT"
    return s


def _bb_position(ctx: GateContext) -> Optional[float]:
    raw = ctx.extras.get("bollinger_position")
    if raw is None and ctx.signal:
        raw = ctx.signal.get("bollinger_position") or ctx.signal.get("bb_percent_b")
    if raw is None:
        raw = _row_float(ctx, "bollinger_position", "bb_percent_b", "percent_b")
    try:
        return float(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _break_pct_points(ctx: GateContext) -> Optional[float]:
    """Normalize break distance to percent points (2.50 = 2.5%)."""
    bd = ctx.break_distance_pct
    if bd is None and ctx.signal:
        try:
            bd = float(ctx.signal.get("break_distance_pct"))
        except (TypeError, ValueError):
            bd = None
    if bd is None:
        try:
            bd = float(ctx.extras.get("break_distance_pct"))
        except (TypeError, ValueError):
            bd = None
    if bd is None:
        return None
    v = float(bd)
    if abs(v) <= 0.15:
        return v * 100.0
    return v


def _eval_equity_rsi_anti_chase(ctx: GateContext) -> GateResult:
    """Dual-side RSI anti-chase (CALL max / PUT min). Soft-skip when RSI missing."""
    if ctx.extras.get("rsi_missing") or (ctx.signal and ctx.signal.get("rsi_missing")):
        return _skip("RSI", "rsi_missing")
    rsi = ctx.rsi
    if rsi is None and ctx.signal:
        try:
            rsi = float(ctx.signal.get("rsi"))
        except (TypeError, ValueError):
            rsi = None
    if rsi is None:
        return _skip("RSI", "absent")
    side = _normalize_side(ctx.side)
    if ctx.path == PATH_ORB:
        call_max = _env_float("QG_ORB_RSI_CALL_MAX", 60.0)
        put_min = _env_float("QG_ORB_RSI_PUT_MIN", 40.0)
    elif ctx.path == PATH_TL:
        call_max = _env_float("QG_TL_RSI_CALL_MAX", 80.0)
        put_min = _env_float("QG_TL_RSI_PUT_MIN", 20.0)
    else:
        call_max = _env_float("QG_BO_RSI_CALL_MAX", 55.0)
        put_min = _env_float("QG_BO_RSI_PUT_MIN", 45.0)
    fields = {
        "rsi": float(rsi),
        "side": side,
        "call_max": call_max,
        "put_min": put_min,
        "path": ctx.path,
    }
    if side == "CALL" and float(rsi) > float(call_max) + 1e-9:
        return _rej(
            "RSI",
            f"call_{_reason_num(rsi, 1)}>{_reason_num(call_max, 1)}",
            **fields,
        )
    if side == "PUT" and float(rsi) + 1e-9 < float(put_min):
        return _rej(
            "RSI",
            f"put_{_reason_num(rsi, 1)}<{_reason_num(put_min, 1)}",
            **fields,
        )
    return _ok("RSI", f"ok_{_reason_num(rsi, 1)}", **fields)


def _eval_bb_anti_stretch(ctx: GateContext) -> GateResult:
    """TL-QG-LS1: CALL bb≤70 · PUT bb≥30. Soft-skip missing."""
    bb = _bb_position(ctx)
    if bb is None:
        return _skip("BB", "absent")
    side = _normalize_side(ctx.side)
    call_max = _env_float("QG_TL_BB_CALL_MAX", 70.0)
    put_min = _env_float("QG_TL_BB_PUT_MIN", 30.0)
    fields = {"bollinger_position": bb, "side": side, "call_max": call_max, "put_min": put_min}
    if side == "CALL" and float(bb) > float(call_max) + 1e-9:
        return _rej(
            "BB",
            f"call_{_reason_num(bb, 1)}>{_reason_num(call_max, 1)}",
            **fields,
        )
    if side == "PUT" and float(bb) + 1e-9 < float(put_min):
        return _rej(
            "BB",
            f"put_{_reason_num(bb, 1)}<{_reason_num(put_min, 1)}",
            **fields,
        )
    return _ok("BB", f"ok_{_reason_num(bb, 1)}", **fields)


def _eval_bb_mid_stretch(ctx: GateContext) -> GateResult:
    """BO-QG-A7: CALL bb≥50 · PUT bb≤50. Soft-skip missing."""
    bb = _bb_position(ctx)
    if bb is None:
        return _skip("BB_mid", "absent")
    side = _normalize_side(ctx.side)
    call_min = _env_float("QG_BO_BB_MID_CALL_MIN", 50.0)
    put_max = _env_float("QG_BO_BB_MID_PUT_MAX", 50.0)
    fields = {"bollinger_position": bb, "side": side, "call_min": call_min, "put_max": put_max}
    if side == "CALL" and float(bb) + 1e-9 < float(call_min):
        return _rej(
            "BB_mid",
            f"call_{_reason_num(bb, 1)}<{_reason_num(call_min, 1)}",
            **fields,
        )
    if side == "PUT" and float(bb) > float(put_max) + 1e-9:
        return _rej(
            "BB_mid",
            f"put_{_reason_num(bb, 1)}>{_reason_num(put_max, 1)}",
            **fields,
        )
    return _ok("BB_mid", f"ok_{_reason_num(bb, 1)}", **fields)


def _eval_break_max(ctx: GateContext) -> GateResult:
    """Shared ORB1/ORB2 chase cap: break% ≤ 2.50%. Alias: chase."""
    bp = _break_pct_points(ctx)
    if bp is None:
        return _skip("break_max", "absent")
    max_pct = _env_float_first(
        (
            "QG_BREAK_MAX",
            "QG_BO_BREAK_MAX",
            "QG_BO_BREAK_MAX_PCT",
            "QG_BO_BREAK_CHASE_MAX_PCT",
        ),
        2.50,
    )
    legacy = _env_float_first(
        ("BREAKOUT_BREAK_MAX", "BREAKOUT_MAX_BREAK_DISTANCE_PCT"),
        0.0,
    )
    if legacy > 0:
        legacy_pct = legacy * 100.0 if legacy <= 0.15 else legacy
        max_pct = min(max_pct, legacy_pct) if max_pct > 0 else legacy_pct
    fields = {"break_pct": bp, "max_break_pct": max_pct}
    if float(bp) > float(max_pct) + 1e-9:
        return _rej(
            "break_max",
            f"{_reason_num(bp, 2)}>{_reason_num(max_pct, 2)}",
            **fields,
        )
    return _ok("break_max", f"ok_{_reason_num(bp, 2)}", **fields)


def _eval_equity_score(ctx: GateContext) -> GateResult:
    """Extension-confidence stamp. Never hard-rejects (rank / observe only)."""
    from easy0DTE.modules.breakout_extension_score import (
        compute_breakout_extension_score,
    )

    row = dict(ctx.signal or {})
    extras = ctx.extras or {}
    atr = extras.get("atr") or row.get("atr")
    score, fields = compute_breakout_extension_score(
        atr=atr,
        atr_5min=row.get("atr_5min"),
        price=row.get("current_price") or row.get("price"),
        volume_ratio=ctx.equity_volume_ratio or row.get("volume_ratio"),
        direction_volume_pct=ctx.direction_volume_pct or row.get("direction_volume_pct"),
        break_distance_pct=ctx.break_distance_pct or row.get("break_distance_pct"),
    )
    ctx.quality_score = float(score)
    fields["quality_score"] = float(score)
    return _ok("score", f"ext_{score:.0f}", **fields)


def _eval_geom_precomputed(
    ctx: GateContext, gate_id: str, flag: Optional[bool], reason: str
) -> GateResult:
    if flag is None:
        return _skip(gate_id, f"{gate_id}_not_in_context")
    if flag:
        return _ok(gate_id, reason or f"{gate_id}_pass")
    return _rej(gate_id, reason or f"{gate_id}_reject")


def _eval_orb_break(ctx: GateContext) -> GateResult:
    from easy0DTE.modules.convex_eligibility_filter import (
        _merge_orb_into_signal,
        _orb_break_pass,
    )

    row = _merge_orb_into_signal(ctx.as_chain_signal())
    ok, msg = _orb_break_pass(row)
    return _ok("orb_break", msg) if ok else _rej("orb_break", msg)


def _eval_orb_range(ctx: GateContext) -> GateResult:
    from easy0DTE.modules.convex_eligibility_filter import (
        _merge_orb_into_signal,
        _orb_range_or_atr_pass,
    )

    row = _merge_orb_into_signal(ctx.as_chain_signal())
    thr = _signal_float(
        row,
        "orb_range_min_pct",
        _env_float("0DTE_CONVEX_ORB_RANGE_MIN", 0.25),
    )
    ok, msg, *_ = _orb_range_or_atr_pass(row, thr)
    return _ok("orb_range", msg) if ok else _rej("orb_range", msg)


def _eval_orb_regime(ctx: GateContext) -> GateResult:
    from easy0DTE.modules.convex_eligibility_filter import (
        _infer_market_regime,
        _merge_orb_into_signal,
    )

    row = _merge_orb_into_signal(ctx.as_chain_signal())
    thr = _env_float("0DTE_CONVEX_ORB_RANGE_MIN", 0.25)
    regime = _infer_market_regime(row, thr)
    required = _signal_bool(row, "trend_day_required", True)
    ok = (not required) or regime in ("trend", "impulse", "BULL", "BEAR")
    msg = f"Market regime: {regime}"
    if not required:
        return _ok("regime", f"{msg} (check disabled)", market_regime=regime)
    if ok:
        return _ok("regime", msg, market_regime=regime)
    return _rej("regime", f"{msg} (not trend/impulse)", market_regime=regime)


def _signal_bool(row: Dict[str, Any], key: str, default: bool) -> bool:
    if key not in row or row.get(key) is None:
        return default
    val = row.get(key)
    if isinstance(val, bool):
        return val
    return str(val).strip().lower() in ("1", "true", "yes", "on")


def _signal_float(row: Dict[str, Any], key: str, default: float) -> float:
    if key not in row or row.get(key) is None:
        return default
    try:
        return float(row[key])
    except (TypeError, ValueError):
        return default


def _macd_noise_reason_token(msg: str) -> str:
    """Compact space-free token for Telegram ``MACD_noise:{reason}``.

    The v2.5 knife is a MACD-histogram deadband (weak opposing hist inside
    ±0.50). Long prose used to display as ``v25:MACD-noise`` because the first
    space truncated the kv field.
    """
    m = str(msg or "").strip()
    if not m:
        return "weak_histogram"
    if " " not in m:
        return m[:90]
    orb = re.search(
        r"MACD-noise ORB ([0-9.]+)%\s*>\s*([0-9.]+)%",
        m,
    )
    if orb:
        return f"hist_deadband_ORB_{orb.group(1)}>{orb.group(2)}"
    if "quality fail" in m:
        need = re.search(r"\|macd\|[≥>=]+([0-9.]+)", m)
        hist = re.search(r"hist=([0-9.+-]+)", m)
        macd = re.search(r"; macd=([^,)]+)", m)
        floor = need.group(1) if need else "0.60"
        parts = [f"weak_hist_need_abs_macd>={floor}"]
        macd_s = (macd.group(1).strip() if macd else "")
        if macd_s and macd_s not in ("None",):
            parts.append(f"macd_{macd_s}")
        if hist:
            parts.append(f"hist_{hist.group(1)}")
        return "_".join(parts)[:90]
    call_neg = re.search(
        r"CALL above VWAP \(([0-9.+-]+)%\) with negative MACD \(([0-9.+-]+)\)",
        m,
    )
    if call_neg:
        return f"CALL_above_VWAP_{call_neg.group(1)}_neg_MACD_{call_neg.group(2)}"
    put_pos = re.search(
        r"PUT below VWAP \(([0-9.+-]+)%\) with positive MACD \(([0-9.+-]+)\)",
        m,
    )
    if put_pos:
        return f"PUT_below_VWAP_{put_pos.group(1)}_pos_MACD_{put_pos.group(2)}"
    ext = re.search(
        r"(CALL|PUT) vwap extension ([0-9.+-]+)%\s*[<>]\s*-?([0-9.]+)%",
        m,
    )
    if ext:
        return f"{ext.group(1)}_vwap_ext_{ext.group(2)}>{ext.group(3)}"
    wide = re.search(
        r"ORB range ([0-9.]+)%\s*≥\s*([0-9.]+)%",
        m,
    )
    if wide:
        return f"wide_ORB_{wide.group(1)}>={wide.group(2)}"
    return re.sub(r"[^\w.|>=<+-]+", "_", m).strip("_")[:90]


def _eval_v25(ctx: GateContext) -> GateResult:
    from easy0DTE.modules.convex_eligibility_filter import (
        _merge_orb_into_signal,
        _v25_symmetric_gates_pass,
    )

    row = _merge_orb_into_signal(ctx.as_chain_signal())
    enabled = _signal_bool(
        row,
        "v25_symmetric_enabled",
        _env_bool("0DTE_CONVEX_V25_SYMMETRIC_ENABLED", True),
    )
    if not enabled:
        return _skip("MACD_noise", "disabled")
    ok, msg = _v25_symmetric_gates_pass(
        row,
        vwap_extension_reject_pct=_signal_float(
            row,
            "vwap_extension_reject_pct",
            _env_float("0DTE_CONVEX_VWAP_EXTENSION_REJECT_PCT", 15.0),
        ),
        orb_range_max_pct=_signal_float(
            row,
            "orb_range_max_pct",
            _env_float("0DTE_CONVEX_ORB_RANGE_MAX_PCT", 8.0),
        ),
        macd_disagree_eps=_signal_float(
            row,
            "macd_disagree_eps",
            _env_float("0DTE_CONVEX_MACD_DISAGREE_EPS", 99.0),
        ),
        macd_noise_quality_enabled=_signal_bool(
            row,
            "macd_deadband_quality_enabled",
            _env_bool("0DTE_QG_MACD_DEADBAND_QUALITY_ENABLED", True),
        ),
        macd_noise_orb_max_pct=_signal_float(
            row,
            "macd_deadband_orb_max_pct",
            _env_float("0DTE_QG_MACD_DEADBAND_ORB_MAX_PCT", 1.0),
        ),
        macd_noise_macd_abs_min=_signal_float(
            row,
            "macd_deadband_macd_abs_min",
            _env_float("0DTE_QG_MACD_DEADBAND_MACD_ABS_MIN", 0.60),
        ),
        macd_noise_vol_max=_signal_float(
            row,
            "macd_deadband_vol_max",
            _env_float("0DTE_QG_MACD_DEADBAND_VOL_MAX", 0.20),
        ),
        macd_noise_band=_signal_float(
            row,
            "macd_deadband_noise_band",
            _env_float("0DTE_QG_MACD_DEADBAND_NOISE_BAND", 0.50),
        ),
    )
    token = _macd_noise_reason_token(msg)
    if ok:
        return _ok("MACD_noise", token, detail=msg)
    return _rej("MACD_noise", token, detail=msg)


def _eval_e14(ctx: GateContext) -> GateResult:
    from easy0DTE.modules.convex_eligibility_filter import (
        _merge_orb_into_signal,
        e14_shallow_gates_pass,
    )

    row = _merge_orb_into_signal(ctx.as_chain_signal())
    # Default OFF — telemetry/observe only unless explicitly re-enabled.
    enabled = _signal_bool(
        row,
        "shallow_e14_enabled",
        _env_bool("0DTE_SHALLOW_E14_ENABLED", False),
    )
    ok, msg = e14_shallow_gates_pass(
        row,
        enabled=enabled,
        call_macd_min=_signal_float(
            row,
            "shallow_call_macd_min",
            _env_float("0DTE_SHALLOW_CALL_MACD_MIN", 2.0),
        ),
        put_macd_max=_signal_float(
            row,
            "shallow_put_macd_max",
            _env_float("0DTE_SHALLOW_PUT_MACD_MAX", -2.0),
        ),
    )
    if not enabled:
        return _skip("e14", msg)
    return _ok("e14", msg) if ok else _rej("e14", msg)


def _eval_e10_e11(ctx: GateContext) -> GateResult:
    from easy0DTE.modules.convex_eligibility_filter import (
        _merge_orb_into_signal,
        e10_protection_call_gate_pass,
    )

    row = _merge_orb_into_signal(ctx.as_chain_signal())
    ok, msg = e10_protection_call_gate_pass(row)
    return _ok("e10_e11", msg) if ok else _rej("e10_e11", msg)


def _eval_e7(ctx: GateContext) -> GateResult:
    from easy0DTE.modules.convex_eligibility_filter import (
        _merge_orb_into_signal,
        e7_index_grind_skip_pass,
    )

    row = _merge_orb_into_signal(ctx.as_chain_signal())
    ok, msg = e7_index_grind_skip_pass(row)
    return _ok("e7", msg) if ok else _rej("e7", msg)


def _eval_chase_skip(ctx: GateContext) -> GateResult:
    from easy0DTE.modules.convex_eligibility_filter import (
        _merge_orb_into_signal,
        compound_vwap_chase_skip_pass,
    )

    row = _merge_orb_into_signal(ctx.as_chain_signal())
    ok, msg = compound_vwap_chase_skip_pass(row)
    return _ok("chase_skip", msg) if ok else _rej("chase_skip", msg)


def _ctx_lookup_float(ctx: GateContext, *keys: str) -> Optional[float]:
    """First-class ctx → extras → signal. Never invent."""
    first_class = {
        "direction_volume_pct": ctx.direction_volume_pct,
        "vwap_distance_pct": ctx.vwap_distance_pct,
        "rsi": ctx.rsi,
        "quality_score": ctx.quality_score,
        "delta": ctx.delta,
        "otm_pct": ctx.otm_pct,
        "call_chain_volume": ctx.call_chain_volume,
        "put_chain_volume": ctx.put_chain_volume,
        "break_distance_pct": ctx.break_distance_pct,
        "range_pct": ctx.range_pct,
    }
    sources: List[Dict[str, Any]] = []
    if ctx.extras:
        sources.append(ctx.extras)
    if ctx.signal:
        sources.append(ctx.signal)
    for key in keys:
        if key in first_class and first_class[key] is not None:
            try:
                return float(first_class[key])
            except (TypeError, ValueError):
                pass
        for src in sources:
            if src.get(key) is None:
                continue
            try:
                return float(src[key])
            except (TypeError, ValueError):
                continue
    return None


def _ss_side(ctx: GateContext) -> str:
    side = str(ctx.side or "").strip().upper()
    if side in ("C", "LONG", "BULLISH", "CALL"):
        return "CALL"
    if side in ("P", "SHORT", "BEARISH", "PUT"):
        return "PUT"
    return side


def _ss_iv(ctx: GateContext) -> Optional[float]:
    iv = _ctx_lookup_float(
        ctx,
        "implied_volatility",
        "iv",
        "representative_iv",
        "chosen_iv",
        "contract_iv",
    )
    if iv is not None and iv > 5.0:
        iv = iv / 100.0
    return iv


def _ss_dir(ctx: GateContext) -> Optional[float]:
    d = _ctx_lookup_float(ctx, "direction_volume_pct")
    if d is not None and d > 1.5:
        d = d / 100.0
    return d


def _ss_sva(ctx: GateContext) -> Optional[float]:
    return _ctx_lookup_float(ctx, "session_vs_avg_volume", "session_vs_avg")


def _ss_vwap_abs(ctx: GateContext) -> Optional[float]:
    named = _ctx_lookup_float(ctx, "abs_vwap_distance_pct")
    if named is not None:
        return abs(named)
    v = _ctx_lookup_float(ctx, "vwap_distance_pct")
    return abs(v) if v is not None else None


def _ss_rsi(ctx: GateContext) -> Optional[float]:
    return _ctx_lookup_float(ctx, "rsi", "rsi_14")


def _ss_vol(ctx: GateContext) -> Optional[float]:
    if ctx.equity_volume_ratio is not None:
        try:
            return float(ctx.equity_volume_ratio)
        except (TypeError, ValueError):
            pass
    return _ctx_lookup_float(ctx, "volume_ratio", "equity_volume_ratio")


def _ss_total_chain_vol(ctx: GateContext) -> Optional[float]:
    """Total chain volume (same_side + opposite_side)."""
    call_vol = ctx.call_chain_volume
    put_vol = ctx.put_chain_volume
    if call_vol is not None and put_vol is not None:
        return call_vol + put_vol
    # Fallback to signal
    call_v = _ctx_lookup_float(ctx, "call_chain_volume", "call_volume")
    put_v = _ctx_lookup_float(ctx, "put_chain_volume", "put_volume")
    if call_v is not None and put_v is not None:
        return call_v + put_v
    return _ctx_lookup_float(ctx, "total_chain_volume")


def _ss_same_side_vol(ctx: GateContext) -> Optional[float]:
    """Same-side chain volume (side-aware)."""
    side = _ss_side(ctx)
    if side == "CALL":
        vol = ctx.call_chain_volume
        if vol is None:
            vol = _ctx_lookup_float(ctx, "call_chain_volume", "call_volume")
        return vol
    elif side == "PUT":
        vol = ctx.put_chain_volume
        if vol is None:
            vol = _ctx_lookup_float(ctx, "put_chain_volume", "put_volume")
        return vol
    # Fallback
    return _ctx_lookup_float(
        ctx, "same_side_chain_volume", "chain_sanity_best_same_side_volume"
    )


def _ss_cumulative_volume(ctx: GateContext) -> Optional[float]:
    """Cumulative volume (from signal extras)."""
    return _ctx_lookup_float(ctx, "cumulative_volume", "cum_vol")


def _ss_spread_pct(ctx: GateContext) -> Optional[float]:
    """Representative spread percentage."""
    return _ctx_lookup_float(
        ctx, "representative_spread_pct", "avg_bid_ask_spread_pct", "spread_pct"
    )


def _moon_field(ctx: GateContext, key: str) -> Optional[float]:
    for src in (ctx.signal, ctx.extras):
        if not isinstance(src, dict):
            continue
        raw = src.get(key)
        if raw is None or raw == "":
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value == value:
            return value
    return None


def _moon_representative_delta(ctx: GateContext) -> Optional[float]:
    """Gate-time near-spot delta for this side only.

    CALL reads representative_call_delta. PUT reads representative_put_delta.
    representative_delta, chosen_delta, fill_delta, and the generic delta key
    are different measurements and are not this input.
    """
    side = _ss_side(ctx)
    if side == "CALL":
        return _moon_field(ctx, "representative_call_delta")
    if side == "PUT":
        return _moon_field(ctx, "representative_put_delta")
    return None


def _delta_moon_override(ctx: GateContext) -> Optional[float]:
    """Delta Moon. A hit clears a measured chain-floor miss.

    Disabled unless that side's floor is set. Omitting QG_CALL_DELTA_MOON_MIN
    or QG_PUT_DELTA_MOON_MIN does not fall back to a built-in floor.
    CALL, when enabled: representative_call_delta >= QG_CALL_DELTA_MOON_MIN.
    If QG_CALL_GAMMA_MOON_MIN is also set, representative_call_gamma must clear it.
    PUT, when enabled: representative_put_delta >= QG_PUT_DELTA_MOON_MIN.
    Puts do not read gamma. Both readings are the gate-time near-spot contract.
    A matched skip still rejects. A missing delta or a missing required call
    gamma does not override. A negative value does not override.
    representative_delta does not override.
    """
    side = _ss_side(ctx)
    if side == "CALL":
        floor = _env_float_opt("QG_CALL_DELTA_MOON_MIN")
        if floor is None:
            return None
        gamma_floor = _env_float_opt("QG_CALL_GAMMA_MOON_MIN")
    elif side == "PUT":
        floor = _env_float_opt("QG_PUT_DELTA_MOON_MIN")
        if floor is None:
            return None
        gamma_floor = None
    else:
        return None
    delta = _moon_representative_delta(ctx)
    if delta is None or float(delta) < float(floor):
        return None
    if gamma_floor is not None:
        gamma = _moon_field(ctx, "representative_call_gamma")
        if gamma is None or float(gamma) < float(gamma_floor):
            return None
    return float(delta)


def _chain_volume_measurement(ctx: GateContext, *keys: str):
    """VALID number (including zero), or a missing/invalid classification."""
    from easy0DTE.modules.decision_required import DATA_ERROR, finite_float

    for key in keys:
        raw = None
        present = False
        if key == "call_chain_volume" and ctx.call_chain_volume is not None:
            raw = ctx.call_chain_volume
            present = True
        elif key == "put_chain_volume" and ctx.put_chain_volume is not None:
            raw = ctx.put_chain_volume
            present = True
        else:
            for src in (ctx.extras, ctx.signal):
                if isinstance(src, dict) and key in src and src.get(key) not in (None, ""):
                    raw = src.get(key)
                    present = True
                    break
        if not present:
            continue
        state, number = finite_float(raw)
        if state == DATA_ERROR:
            return DATA_ERROR, None
        return state, number
    return "MISSING", None


def _eval_entry_part(ctx: GateContext) -> GateResult:
    """Participation admission.

    CALL requires call_chain_volume >= QG_CALL_ENTRY_CHAIN_MIN (110000).
    PUT requires put_chain_volume >= QG_PUT_ENTRY_CHAIN_MIN (11000).
    A missing or non-finite chain volume is a data error and rejects.
    A yielded chain fetch (monitor busy / live budget) with no usable cache
    defers on the same break instead of rejecting. Zero is a real volume and
    is compared to the floor. Delta Moon can clear a measured floor failure
    only when that side's moon floor is set in the environment. It cannot
    clear a missing chain.
    """
    from easy0DTE.modules.decision_required import DATA_ERROR, VALID, chain_attempt

    gate_id = "entry_part"
    side = _ss_side(ctx)
    call_chain_min = _env_float_opt("QG_CALL_ENTRY_CHAIN_MIN")
    if call_chain_min is None:
        call_chain_min = 110000.0
    put_chain_min = _env_float_opt("QG_PUT_ENTRY_CHAIN_MIN")
    if put_chain_min is None:
        put_chain_min = 11000.0
    if side == "CALL":
        state, chain = _chain_volume_measurement(ctx, "call_chain_volume", "call_volume")
        floor = call_chain_min
        name = "call_chain"
    elif side == "PUT":
        state, chain = _chain_volume_measurement(ctx, "put_chain_volume", "put_volume")
        floor = put_chain_min
        name = "put_chain"
    else:
        return _ok(gate_id, "entry_part_na", side=side)
    fields = {
        f"{name}_volume": chain,
        "chain_min": floor,
        "side": side,
        "chain_state": state,
    }
    if state != VALID:
        attempt = chain_attempt(ctx)
        if attempt == "skipped" or attempt.startswith("skipped_"):
            return _defer(gate_id, "required_data_not_ready", **fields)
        if state == DATA_ERROR:
            return _rej(gate_id, "invalid_numeric_data", **fields)
        return _rej(gate_id, "chain_volume_data_error", **fields)
    moon = _delta_moon_override(ctx)
    if chain < float(floor):
        if moon is not None:
            return _ok(
                gate_id,
                f"delta_moon_{_reason_num(moon, 2)}",
                delta=moon,
                **fields,
            )
        return _rej(
            gate_id,
            f"{name}_{_reason_num(chain, 0)}_lt_{_reason_num(floor, 0)}",
            **fields,
        )
    if moon is not None:
        return _ok(
            gate_id,
            f"delta_moon_{_reason_num(moon, 2)}",
            delta=moon,
            **fields,
        )
    return _ok(gate_id, f"{name}_{_reason_num(chain, 0)}", **fields)


def _fz_suffix_safe(freeze: Any) -> str:
    try:
        from central_tech.decision_freeze import freeze_log_suffix

        return freeze_log_suffix(freeze)
    except Exception:
        return ""


def _eval_trade_profile(ctx: GateContext) -> GateResult:
    """Accept or reject from the path/side Trade Profile.

    A base key the profile expects starts as a reject when it is missing.
    A later allow can still admit when its own values are present. A missing
    overlay or mined comparison does not fire.
    A chain fetch that was not run (scan budget used, no cache) does not
    reject. The same break is held until a chain is measured. Zero volume
    after a completed fetch is a real number and is scored.
    """
    from easy0DTE.modules.decision_required import chain_attempt
    from easy0DTE.modules.trade_profile import (
        VERSION_10_1,
        VERSION_10_2,
        VERSION_10_5,
        VERSION_10_16,
        decision_value_keys,
        decision_values_line,
        evaluate_trade_profile,
        field_name,
    )

    side = _ss_side(ctx)
    slot = None
    if ctx.extras:
        slot = ctx.extras.get("trendline_slot")
    if slot is None and ctx.signal:
        slot = ctx.signal.get("trendline_slot")
    # The 20 canonical keys plus the 10.2/10.3 ratio and 5-min flow keys that
    # central_tech already stamped on this signal (a superset of the 10.5 23-key
    # templates). Read only, never computed here.
    try:
        from modules.session_tech_indicators import ensure_bollinger_complete

        if isinstance(ctx.signal, dict):
            ensure_bollinger_complete(ctx.signal)
        if isinstance(ctx.extras, dict):
            ensure_bollinger_complete(ctx.extras)
    except Exception:
        pass
    values: Dict[str, Any] = {}
    for key in decision_value_keys():
        values[key] = _ctx_lookup_float(ctx, key)
    attempt = chain_attempt(ctx)
    vol_key = field_name("vol", side)
    sig = ctx.signal if isinstance(ctx.signal, dict) else {}
    trade_id = sig.get("trade_id") or sig.get("signal_id") or sig.get("handoff_id")
    # Oct 7 2026 decision freeze: the gate measured every source age right
    # before this call (tp_freeze_ctx). One freeze object holds the exact
    # scored values + ages + gate clock; the handoff snapshot reads it back.
    fz_ctx = sig.get("tp_freeze_ctx") if isinstance(sig.get("tp_freeze_ctx"), dict) else None
    gate_at = datetime.now(timezone.utc).isoformat()

    def _freeze_and_record(admitted, reason, *, profile="", version="", missing=(), stage="tp"):
        fz = None
        try:
            from central_tech.decision_freeze import build_decision_freeze

            fz = build_decision_freeze(
                values=values,
                path=ctx.path,
                side=side,
                slot=slot,
                symbol=ctx.symbol,
                trade_id=trade_id,
                profile=profile,
                profile_version=version,
                admitted=admitted,
                reason=reason,
                stage=stage,
                missing=missing,
                context=fz_ctx,
                gate_at=gate_at,
            )
            if ctx.extras is None:
                ctx.extras = {}
            ctx.extras["po_decision_freeze"] = fz
        except Exception as exc:
            log.debug("TP_FREEZE | build_error=%s", exc)
        try:
            from modules.tp_decision_record_store import append_decision_record

            if fz is not None:
                append_decision_record(fz, kind=stage)
        except Exception as exc:
            log.debug("TP_FREEZE | record_error=%s", exc)
        return fz

    if (
        (attempt == "skipped" or str(attempt).startswith("skipped_"))
        and values.get(vol_key) is None
    ):
        try:
            from central_tech.decision_freeze import stale_policy as _fz_policy

            _policy = _fz_policy()
        except Exception:
            _policy = "defer"
        if attempt == "skipped_tp_freeze_budget" and _policy == "reject":
            _freeze_and_record(False, "tp_freeze_budget_exhausted", stage="tp_budget_reject")
            log.warning(
                "TP_FREEZE_STALE | %s %s %s slot=%s trade_id=%s | action=reject | "
                "reason=chain_refresh_budget_exhausted",
                ctx.symbol or "?", ctx.path, side or "?", slot, trade_id,
            )
            return _rej(
                "trade_profile",
                "tp_freeze_budget_exhausted",
                chain_attempt=attempt,
                trendline_slot=slot,
            )
        _freeze_and_record(None, "required_data_not_ready", stage="tp_defer")
        return _defer(
            "trade_profile",
            "required_data_not_ready",
            chain_attempt=attempt,
            missing=vol_key,
            trendline_slot=slot,
        )
    if fz_ctx is not None:
        try:
            from central_tech.decision_freeze import (
                cell_keys as _fz_cell_keys,
                stale_gated as _fz_stale_gated,
                stale_policy as _fz_policy,
            )

            _stale = _fz_stale_gated(
                _fz_cell_keys(ctx.path, side, slot), fz_ctx.get("ages_sec") or {}
            )
        except Exception:
            _stale = []
        if _stale:
            _policy = _fz_policy()
            _fz = _freeze_and_record(
                None if _policy == "defer" else False,
                f"tp_freeze_stale_{_policy}",
                stage=f"tp_stale_{_policy}",
            )
            log.warning(
                "TP_FREEZE_STALE | %s %s %s slot=%s trade_id=%s | action=%s | stale=[%s] | %s",
                ctx.symbol or "?",
                ctx.path,
                side or "?",
                slot,
                trade_id,
                _policy,
                ",".join(_stale),
                _fz_suffix_safe(_fz),
            )
            if _policy == "reject":
                return _rej(
                    "trade_profile",
                    "tp_freeze_stale_source",
                    tp_freeze_stale=",".join(_stale),
                    trendline_slot=slot,
                )
            return _defer(
                "trade_profile",
                "required_data_not_ready",
                tp_freeze_stale=",".join(_stale),
                trendline_slot=slot,
            )
    try:
        decision = evaluate_trade_profile(ctx.path, side, values, slot=slot)
    except Exception as exc:
        # A bad value or a broken profile must not stop the trading loop.
        # The signal is rejected and the reason names the error.
        log.exception(
            "TP_DECISION_VALUES | %s %s %s slot=%s trade_id=%s | admit=0 "
            "reason=trade_profile_eval_error_%s | error=%s",
            ctx.symbol or "?",
            ctx.path,
            side or "?",
            slot,
            trade_id,
            type(exc).__name__,
            exc,
        )
        _freeze_and_record(False, f"trade_profile_eval_error_{type(exc).__name__}")
        return _rej(
            "trade_profile",
            f"trade_profile_eval_error_{type(exc).__name__}",
            trendline_slot=slot,
        )
    freeze = _freeze_and_record(
        bool(decision.admitted),
        decision.reason,
        profile=getattr(decision, "profile", "") or "",
        version=getattr(decision, "version", "") or "",
        missing=tuple(getattr(decision, "missing", ()) or ()),
    )
    if isinstance(freeze, dict) and freeze.get("missing_gated"):
        log.warning(
            "TP_FREEZE_MISSING_GATED | %s %s %s slot=%s trade_id=%s | admit=%s | "
            "reason=%s | missing_gated=[%s]",
            ctx.symbol or "?",
            ctx.path,
            side or "?",
            slot,
            trade_id,
            int(bool(decision.admitted)),
            decision.reason,
            ",".join(freeze.get("missing_gated") or []),
        )
    shadow = None
    if _env_bool("TRADE_PROFILE_SHADOW_LOG", True):
        try:
            # 10.5 logs the 10.2nh verdict (what ran through Oct 5); 10.2nh logs
            # 10.1; 10.1 logs 10.2nh.
            if decision.version in (VERSION_10_5, VERSION_10_16):
                other = VERSION_10_2
            elif decision.version != VERSION_10_1:
                other = VERSION_10_1
            else:
                other = VERSION_10_2
            shadow = evaluate_trade_profile(ctx.path, side, values, slot=slot, version=other)
        except Exception:
            shadow = None
    try:
        log.info(
            "%s",
            decision_values_line(
                decision,
                values,
                symbol=ctx.symbol,
                path=ctx.path,
                slot=slot,
                trade_id=trade_id,
                shadow=shadow,
                freeze_suffix=_fz_suffix_safe(freeze),
            ),
        )
    except Exception as exc:
        log.warning("TP_DECISION_VALUES | log_error=%s", exc)
    fields = decision.fields()
    fields["trendline_slot"] = slot
    if decision.admitted:
        return _ok("trade_profile", decision.reason, **fields)
    return _rej("trade_profile", decision.reason, **fields)


def _measured(ctx: GateContext, *keys: str):
    """(state, number) from the first present key. Zero is VALID."""
    from easy0DTE.modules.decision_required import DATA_ERROR, VALID, finite_float

    first_class = {
        "call_chain_volume": ctx.call_chain_volume,
        "put_chain_volume": ctx.put_chain_volume,
        "break_distance_pct": ctx.break_distance_pct,
        "vwap_distance_pct": ctx.vwap_distance_pct,
        "rsi": ctx.rsi,
        "direction_volume_pct": ctx.direction_volume_pct,
    }
    for key in keys:
        raw = None
        present = False
        if key in first_class and first_class[key] is not None:
            raw = first_class[key]
            present = True
        else:
            for src in (ctx.extras, ctx.signal):
                if isinstance(src, dict) and key in src and src.get(key) not in (None, ""):
                    raw = src.get(key)
                    present = True
                    break
        if not present:
            continue
        state, number = finite_float(raw)
        if state == DATA_ERROR:
            return DATA_ERROR, None
        return VALID, number
    return "MISSING", None


def _value_state(ctx: GateContext, value: Optional[float], *, clock_bars: Optional[int] = None):
    """Classify an already-read number. clock_bars None means not clock-based."""
    from easy0DTE.modules.decision_required import (
        DATA_ERROR,
        NOT_READY,
        VALID,
        missing_after_clock,
        session_bar_count,
    )

    if value is not None:
        state, _number = _measured_literal(value)
        return state
    if clock_bars is None:
        return DATA_ERROR
    return missing_after_clock(session_bar_count(ctx), clock_bars)


def _measured_literal(value: Any):
    from easy0DTE.modules.decision_required import finite_float

    return finite_float(value)


def _finish_required_pockets(gate_id: str, moon: Optional[float], outcomes: list, fields: Dict[str, Any]) -> GateResult:
    from easy0DTE.modules.decision_required import POCKET_ERROR, POCKET_TRUE, POCKET_UNKNOWN

    if POCKET_TRUE in outcomes:
        return _rej(gate_id, fields.get("_match_reason") or "skip_pocket", **fields)
    if POCKET_ERROR in outcomes:
        return _rej(gate_id, "required_indicator_data_error", **fields)
    if moon is not None:
        return _ok(gate_id, f"delta_moon_{_reason_num(moon, 2)}", delta=moon, **fields)
    if POCKET_UNKNOWN in outcomes:
        return _defer(gate_id, "required_data_not_ready", **fields)
    return _ok(gate_id, "no_dump_pocket", **fields)


def _call_ss_required_result(ctx: GateContext, gate_id: str, moon, fields: Dict[str, Any], **kw) -> Optional[GateResult]:
    """Post-hydration CALL skip. None means the legacy pre-chain pass."""
    from easy0DTE.modules.decision_required import (
        DATA_ERROR,
        NOT_READY,
        POCKET_FALSE,
        PVE_MIN_BARS,
        VALID,
        and_pocket,
        decision_hydrated,
        single_pocket,
    )

    if not decision_hydrated(ctx):
        return None
    outcomes = []
    bars_state = None

    def _clock_missing(value, minimum):
        if value is None:
            return _value_state(ctx, None, clock_bars=minimum)
        state, _n = _measured_literal(value)
        return state

    if kw["rsi_max"] is not None and kw["cum_vol_max"] is not None:
        rsi_state = _clock_missing(kw["rsi"], 14)
        vol_state = DATA_ERROR if kw["cum_vol"] is None else _measured_literal(kw["cum_vol"])[0]
        matched_rsi = rsi_state == VALID and kw["rsi"] is not None and kw["rsi"] <= kw["rsi_max"]
        matched_vol = vol_state == VALID and kw["cum_vol"] is not None and kw["cum_vol"] <= kw["cum_vol_max"]
        outcomes.append(and_pocket([rsi_state, vol_state], [matched_rsi, matched_vol]))
    if kw["spread_max"] is not None and kw["chain_max_b"] is not None:
        sp = DATA_ERROR if kw["spread_pct"] is None else _measured_literal(kw["spread_pct"])[0]
        ch = DATA_ERROR if kw["total_chain_vol"] is None else _measured_literal(kw["total_chain_vol"])[0]
        outcomes.append(and_pocket(
            [sp, ch],
            [
                sp == VALID and kw["spread_pct"] <= kw["spread_max"],
                ch == VALID and kw["total_chain_vol"] <= kw["chain_max_b"],
            ],
        ))
    if kw["break_dist_max"] is not None:
        # 2026-09-27 C2: a missing break distance does not skip and does not reject.
        if kw["break_dist"] is None:
            outcomes.append(POCKET_FALSE)
        else:
            state = _measured_literal(kw["break_dist"])[0]
            matched = state == VALID and kw["break_dist"] <= kw["break_dist_max"]
            if matched:
                fields["_match_reason"] = (
                    f"p88_brk_{_reason_num(kw['break_dist'], 6)}_le_{_reason_num(kw['break_dist_max'], 6)}"
                )
            outcomes.append(single_pocket(state, matched))
    if kw["pve_3m_min"] is not None:
        state = _clock_missing(kw["pve_3m"], PVE_MIN_BARS)
        matched = state == VALID and kw["pve_3m"] is not None and kw["pve_3m"] >= kw["pve_3m_min"]
        if matched:
            fields["_match_reason"] = (
                f"p924_pve3m_{_reason_num(kw['pve_3m'], 3)}_ge_{_reason_num(kw['pve_3m_min'], 2)}"
            )
        outcomes.append(single_pocket(state, matched))
    for label, limit, value, op in (
        ("put_chain", kw["put_chain_max"], kw["put_chain"], "le"),
        ("volume_5m", kw["volume_5m_max"], kw["volume_5m"], "le"),
        ("dir5", kw["dir_pct_5m_min"], kw["dir_pct_5m"], "ge"),
        ("rel", kw["rel_vol_max"], kw["rel_vol"], "le"),
        ("ask", kw["ask_max"], kw["ask"], "le"),
    ):
        if limit is None:
            continue
        if value is None:
            state = NOT_READY if label == "dir5" else DATA_ERROR
        else:
            state = _measured_literal(value)[0]
        if op == "le":
            matched = state == VALID and value is not None and value <= limit
        else:
            matched = state == VALID and value is not None and value >= limit
        outcomes.append(single_pocket(state, matched))
    if not outcomes:
        return _ok(gate_id, "no_dump_pocket", **fields)
    return _finish_required_pockets(gate_id, moon, outcomes, fields)


def _has_break(ctx: GateContext) -> bool:
    for src in (ctx.extras, ctx.signal):
        if not isinstance(src, dict):
            continue
        for key in ("break_timestamp", "break_ts", "break_time", "material_break_at"):
            if src.get(key) not in (None, ""):
                return True
    return False


def _ss_break_dist_ge_reject(ctx: GateContext, gate_id: str, threshold: Optional[float], fields: Dict[str, Any]) -> Optional[GateResult]:
    """2026-10-15 zero-win deep skip: reject when break_distance_pct >= threshold.

    Key unset / blank -> None (off). A missing, NaN or non-numeric
    break_distance_pct never skips. Runs before the pocket logic so the pre-chain
    (unhydrated) and chain-time (hydrated) evaluations give the same answer.
    """
    if threshold is None:
        return None
    bd = _ctx_lookup_float(ctx, "break_distance_pct")
    if bd is None or bd != bd or bd in (float("inf"), float("-inf")):
        return None
    if bd >= threshold:
        return _rej(
            gate_id,
            f"break_dist_ge_{_reason_num(bd, 6)}_ge_{_reason_num(threshold, 6)}",
            **dict(fields, break_distance_pct=bd),
        )
    return None


def _eval_call_ss(ctx: GateContext) -> GateResult:
    """Absolute CALL skip. Levels from QG_CALL_SS_* in Shared.env.

    2026-09-24: the four pockets that repeat the 7,000 entry rule are off
    because their env keys are omitted (RSI+cum volume, spread+small chain,
    put chain <= 435, volume_5m <= 24183). A pocket runs only when its keys
    are set. Missing stamps do not skip. Wrong side → n/a / pass.
    """
    gate_id = "call_ss"
    side = _ss_side(ctx)
    if side != "CALL":
        return _ok(gate_id, "call_ss_na", side=side)
    moon = _delta_moon_override(ctx)

    rsi_max = _env_float_opt("QG_CALL_SS_RSI_MAX")
    cum_vol_max = _env_float_opt("QG_CALL_SS_CUM_VOL_MAX")
    spread_max = _env_float_opt("QG_CALL_SS_SPREAD_MAX")
    chain_max_b = _env_float_opt("QG_CALL_SS_CHAIN_MAX_B")
    put_chain_max = _env_float_opt("QG_CALL_SS_PUT_CHAIN_MAX")
    break_dist_max = _env_float_opt("QG_CALL_SS_BREAK_DIST_MAX")
    volume_5m_max = _env_float_opt("QG_CALL_SS_VOLUME_5M_MAX")
    dir_pct_5m_min = _env_float_opt("QG_CALL_SS_DIR_PCT_5M_MIN")
    rel_vol_max = _env_float_opt("QG_CALL_SS_REL_VOL_TIME_ADJ_MAX")
    ask_max = _env_float_opt("QG_CALL_SS_ASK_MAX")
    pve_3m_min = _env_float_opt("QG_CALL_SS_PVE_3M_MIN")
    break_dist_ge = _env_float_opt("QG_CALL_SS_BREAK_DIST_GE")

    configured = any(
        v is not None
        for v in (
            rsi_max, cum_vol_max, spread_max, chain_max_b, put_chain_max,
            break_dist_max, volume_5m_max, dir_pct_5m_min, rel_vol_max, ask_max,
            pve_3m_min, break_dist_ge,
        )
    )
    if not configured:
        return _skip(gate_id, "unconfigured")

    total_chain_vol = _ss_total_chain_vol(ctx)
    rsi = _ss_rsi(ctx)
    cum_vol = _ss_cumulative_volume(ctx)
    spread_pct = _ss_spread_pct(ctx)
    put_chain = _ctx_lookup_float(ctx, "put_chain_volume")
    if put_chain is None and ctx.put_chain_volume is not None:
        try:
            put_chain = float(ctx.put_chain_volume)
        except (TypeError, ValueError):
            put_chain = None
    break_dist = _ctx_lookup_float(ctx, "break_distance_pct")
    if break_dist is None and ctx.break_distance_pct is not None:
        try:
            break_dist = float(ctx.break_distance_pct)
        except (TypeError, ValueError):
            break_dist = None
    volume_5m = _ctx_lookup_float(ctx, "volume_5m")
    dir_pct_5m = _ctx_lookup_float(ctx, "dir_pct_change_5m")
    rel_vol = _ctx_lookup_float(ctx, "relative_volume_time_adjusted")
    ask = _ctx_lookup_float(ctx, "representative_ask", "option_ask", "ask")
    pve_3m = _ctx_lookup_float(ctx, "price_volume_efficiency_3m")

    fields: Dict[str, Any] = {
        "total_chain_volume": total_chain_vol,
        "rsi": rsi,
        "cumulative_volume": cum_vol,
        "spread_pct": spread_pct,
        "put_chain_volume": put_chain,
        "break_distance_pct": break_dist,
        "volume_5m": volume_5m,
        "dir_pct_change_5m": dir_pct_5m,
        "relative_volume_time_adjusted": rel_vol,
        "representative_ask": ask,
        "price_volume_efficiency_3m": pve_3m,
    }

    deep = _ss_break_dist_ge_reject(ctx, gate_id, break_dist_ge, fields)
    if deep is not None:
        return deep

    hydrated = _call_ss_required_result(
        ctx,
        gate_id,
        moon,
        fields,
        rsi_max=rsi_max,
        cum_vol_max=cum_vol_max,
        spread_max=spread_max,
        chain_max_b=chain_max_b,
        put_chain_max=put_chain_max,
        break_dist_max=break_dist_max,
        volume_5m_max=volume_5m_max,
        dir_pct_5m_min=dir_pct_5m_min,
        rel_vol_max=rel_vol_max,
        ask_max=ask_max,
        pve_3m_min=pve_3m_min,
        rsi=rsi,
        cum_vol=cum_vol,
        spread_pct=spread_pct,
        total_chain_vol=total_chain_vol,
        put_chain=put_chain,
        break_dist=break_dist,
        volume_5m=volume_5m,
        dir_pct_5m=dir_pct_5m,
        rel_vol=rel_vol,
        ask=ask,
        pve_3m=pve_3m,
    )
    if hydrated is not None:
        return hydrated
    if moon is not None:
        return _ok(gate_id, f"delta_moon_{_reason_num(moon, 2)}", delta=moon, side=side)

    # c3: RSI <= max AND cum_vol <= max
    if rsi_max is not None and cum_vol_max is not None:
        if (rsi is not None and cum_vol is not None
                and rsi <= rsi_max and cum_vol <= cum_vol_max):
            return _rej(
                gate_id,
                f"p88_c3_rsi_{_reason_num(rsi, 0)}_le_{_reason_num(rsi_max, 0)}_cvol_{_reason_num(cum_vol, 0)}_le_{_reason_num(cum_vol_max, 0)}",
                **fields,
            )

    # c4: spread% <= max AND total_chain_vol <= max_b
    if spread_max is not None and chain_max_b is not None:
        if (spread_pct is not None and total_chain_vol is not None
                and spread_pct <= spread_max and total_chain_vol <= chain_max_b):
            return _rej(
                gate_id,
                f"p88_c4_spread_{_reason_num(spread_pct, 3)}_le_{_reason_num(spread_max, 1)}_chain_{_reason_num(total_chain_vol, 0)}_le_{_reason_num(chain_max_b, 0)}",
                **fields,
            )

    if put_chain_max is not None and put_chain is not None and put_chain <= put_chain_max:
        return _rej(
            gate_id,
            f"p88_put_chain_{_reason_num(put_chain, 0)}_le_{_reason_num(put_chain_max, 0)}",
            **fields,
        )
    if break_dist_max is not None and break_dist is not None and break_dist <= break_dist_max:
        return _rej(
            gate_id,
            f"p88_brk_{_reason_num(break_dist, 6)}_le_{_reason_num(break_dist_max, 6)}",
            **fields,
        )
    if volume_5m_max is not None and volume_5m is not None and volume_5m <= volume_5m_max:
        return _rej(
            gate_id,
            f"p88_vol5m_{_reason_num(volume_5m, 0)}_le_{_reason_num(volume_5m_max, 0)}",
            **fields,
        )
    if dir_pct_5m_min is not None and dir_pct_5m is not None and dir_pct_5m >= dir_pct_5m_min:
        return _rej(
            gate_id,
            f"p88_dir5m_{_reason_num(dir_pct_5m, 5)}_ge_{_reason_num(dir_pct_5m_min, 5)}",
            **fields,
        )
    if rel_vol_max is not None and rel_vol is not None and rel_vol <= rel_vol_max:
        return _rej(
            gate_id,
            f"p88_relvol_{_reason_num(rel_vol, 5)}_le_{_reason_num(rel_vol_max, 5)}",
            **fields,
        )
    if ask_max is not None and ask is not None and ask <= ask_max:
        return _rej(
            gate_id,
            f"p88_ask_{_reason_num(ask, 2)}_le_{_reason_num(ask_max, 2)}",
            **fields,
        )
    if pve_3m_min is not None and pve_3m is not None and pve_3m >= pve_3m_min:
        return _rej(
            gate_id,
            f"p924_pve3m_{_reason_num(pve_3m, 3)}_ge_{_reason_num(pve_3m_min, 2)}",
            **fields,
        )

    return _ok(gate_id, "no_dump_pocket", **fields)


def _put_leg_state(ctx: GateContext, value: Optional[float], *, clock_bars: Optional[int], five_minute: bool = False) -> str:
    from easy0DTE.modules.decision_required import DATA_ERROR, NOT_READY, VALID

    if value is not None:
        state, _number = _measured_literal(value)
        return state
    if five_minute:
        return NOT_READY
    if clock_bars is None:
        return DATA_ERROR
    return _value_state(ctx, None, clock_bars=clock_bars)


def _put_ss_required_result(ctx: GateContext, gate_id: str, moon, fields: Dict[str, Any], **kw) -> Optional[GateResult]:
    """Post-hydration PUT skip. Policy C. None means the legacy pre-chain pass."""
    from easy0DTE.modules.decision_required import (
        ATR_MIN_BARS,
        DATA_ERROR,
        NOT_READY,
        POCKET_FALSE,
        RSI_MIN_BARS,
        VALID,
        VWAP_MIN_BARS,
        and_pocket,
        decision_hydrated,
        single_pocket,
    )

    if not decision_hydrated(ctx):
        return None
    outcomes = []

    def _match_reason(text: str, outcome: str) -> str:
        if outcome == "TRUE":
            fields["_match_reason"] = text
        return outcome

    if kw["rsi_max_a"] is not None and kw["chain_max_b"] is not None:
        rsi_state = _put_leg_state(ctx, kw["rsi"], clock_bars=RSI_MIN_BARS)
        vol_state = _put_leg_state(ctx, kw["total_chain_vol"], clock_bars=None)
        outcomes.append(and_pocket(
            [rsi_state, vol_state],
            [
                rsi_state == VALID and kw["rsi"] <= kw["rsi_max_a"],
                vol_state == VALID and kw["total_chain_vol"] <= kw["chain_max_b"],
            ],
        ))
    if kw["rsi_max_b"] is not None and kw["iv_min_b"] is not None:
        rsi_state = _put_leg_state(ctx, kw["rsi"], clock_bars=RSI_MIN_BARS)
        iv_state = _put_leg_state(ctx, kw["iv"], clock_bars=None)
        outcomes.append(and_pocket(
            [rsi_state, iv_state],
            [
                rsi_state == VALID and kw["rsi"] <= kw["rsi_max_b"],
                iv_state == VALID and kw["iv"] > kw["iv_min_b"],
            ],
        ))
    if kw["spy_max"] is not None:
        spy_state = _put_leg_state(ctx, kw["spy"], clock_bars=None)
        matched = spy_state == VALID and kw["spy"] <= kw["spy_max"]
        outcomes.append(_match_reason(
            f"p88_spy_le_{_reason_num(kw['spy'], 3)}_le_{_reason_num(kw['spy_max'], 3)}" if matched else "",
            single_pocket(spy_state, matched),
        ))
    if kw["theta_min"] is not None and kw["spy_min"] is not None:
        theta_state = _put_leg_state(ctx, kw["theta"], clock_bars=None)
        spy_state = _put_leg_state(ctx, kw["spy"], clock_bars=None)
        outcomes.append(and_pocket(
            [theta_state, spy_state],
            [
                theta_state == VALID and kw["theta"] >= kw["theta_min"],
                spy_state == VALID and kw["spy"] >= kw["spy_min"],
            ],
        ))
    if kw["call_d5m_max"] is not None and kw["theta_min"] is not None:
        d5_state = _put_leg_state(ctx, kw["call_d5m"], clock_bars=None, five_minute=True)
        theta_state = _put_leg_state(ctx, kw["theta"], clock_bars=None)
        d5_outcome = and_pocket(
            [d5_state, theta_state],
            [
                d5_state == VALID and kw["call_d5m"] <= kw["call_d5m_max"],
                theta_state == VALID and kw["theta"] >= kw["theta_min"],
            ],
        )
        if d5_outcome == "TRUE":
            fields["_match_reason"] = (
                f"p88_cd5m_{_reason_num(kw['call_d5m'], 0)}_le_{_reason_num(kw['call_d5m_max'], 0)}"
                f"_theta_{_reason_num(kw['theta'], 5)}_ge_{_reason_num(kw['theta_min'], 5)}"
            )
        outcomes.append(d5_outcome)
    if kw["dir_pct_5m_min"] is not None and kw["theta_min"] is not None:
        d5_state = _put_leg_state(ctx, kw["dir_pct_5m"], clock_bars=None, five_minute=True)
        theta_state = _put_leg_state(ctx, kw["theta"], clock_bars=None)
        outcomes.append(and_pocket(
            [d5_state, theta_state],
            [
                d5_state == VALID and kw["dir_pct_5m"] >= kw["dir_pct_5m_min"],
                theta_state == VALID and kw["theta"] >= kw["theta_min"],
            ],
        ))
    if kw["abs_vwap_min"] is not None:
        state = _put_leg_state(ctx, kw["abs_vwap"], clock_bars=VWAP_MIN_BARS)
        matched = state == VALID and kw["abs_vwap"] >= kw["abs_vwap_min"]
        outcomes.append(single_pocket(state, matched))
    if kw["chain_oi_max"] is not None:
        state = _put_leg_state(ctx, kw["chain_oi"], clock_bars=None)
        matched = state == VALID and kw["chain_oi"] <= kw["chain_oi_max"]
        outcomes.append(single_pocket(state, matched))
    if kw["vwap_atr_max"] is not None:
        state = _put_leg_state(ctx, kw["vwap_atr"], clock_bars=ATR_MIN_BARS)
        matched = state == VALID and kw["vwap_atr"] <= kw["vwap_atr_max"]
        outcomes.append(single_pocket(state, matched))
    if kw["call_chain_min"] is not None and kw["break_orb_ratio_max"] is not None:
        # 2026-09-27 R-PUT: a missing leg does not skip and does not reject.
        if kw["call_chain"] is None or kw["break_orb_ratio"] is None:
            outcomes.append(POCKET_FALSE)
        else:
            call_state = _put_leg_state(ctx, kw["call_chain"], clock_bars=None)
            bor_state = _put_leg_state(ctx, kw["break_orb_ratio"], clock_bars=None)
            rput_outcome = and_pocket(
                [call_state, bor_state],
                [
                    call_state == VALID and kw["call_chain"] >= kw["call_chain_min"],
                    bor_state == VALID and kw["break_orb_ratio"] <= kw["break_orb_ratio_max"],
                ],
            )
            if rput_outcome == "TRUE":
                fields["_match_reason"] = (
                    f"rput_call_chain_{_reason_num(kw['call_chain'], 0)}_ge_{_reason_num(kw['call_chain_min'], 0)}"
                    f"_bor_{_reason_num(kw['break_orb_ratio'], 3)}_le_{_reason_num(kw['break_orb_ratio_max'], 3)}"
                )
            outcomes.append(rput_outcome)
    if kw["vol_1m_max"] is not None and kw["call_oi_max"] is not None:
        vol_state = _put_leg_state(ctx, kw["vol_1m"], clock_bars=1)
        oi_state = _put_leg_state(ctx, kw["call_oi"], clock_bars=None)
        outcomes.append(and_pocket(
            [vol_state, oi_state],
            [
                vol_state == VALID and kw["vol_1m"] <= kw["vol_1m_max"],
                oi_state == VALID and kw["call_oi"] <= kw["call_oi_max"],
            ],
        ))
    if not outcomes:
        return _ok(gate_id, "no_dump_pocket", **fields)
    return _finish_required_pockets(gate_id, moon, outcomes, fields)


def _eval_put_ss(ctx: GateContext) -> GateResult:
    """Absolute PUT skip.

    Levels from QG_PUT_SS_* in Shared.env / Configs.md. The 2026-09-27
    live-local config uses the R-PUT remine pocket:
    call_chain_volume >= min AND break_distance_orb_ratio <= max.
    Older pocket keys are still recognized when explicitly configured.
    Skip PUT if any pocket matches (OR of AND / single-atom conditions).
    Missing stamps do not skip. Wrong side → n/a / pass.
    """
    gate_id = "put_ss"
    side = _ss_side(ctx)
    if side != "PUT":
        return _ok(gate_id, "put_ss_na", side=side)
    moon = _delta_moon_override(ctx)

    rsi_max_a = _env_float_opt("QG_PUT_SS_RSI_MAX_A")
    chain_max_b = _env_float_opt("QG_PUT_SS_CHAIN_MAX_B")
    rsi_max_b = _env_float_opt("QG_PUT_SS_RSI_MAX_B")
    iv_min_b = _env_float_opt("QG_PUT_SS_IV_MIN_B")
    spy_max = _env_float_opt("QG_PUT_SS_SPY_CHANGE_MAX")
    vol_1m_max = _env_float_opt("QG_PUT_SS_VOLUME_1M_MAX")
    call_oi_max = _env_float_opt("QG_PUT_SS_CALL_OI_MAX")
    theta_min = _env_float_opt("QG_PUT_SS_THETA_MIN")
    spy_min = _env_float_opt("QG_PUT_SS_SPY_CHANGE_MIN")
    call_d5m_max = _env_float_opt("QG_PUT_SS_CALL_D5M_MAX")
    abs_vwap_min = _env_float_opt("QG_PUT_SS_ABS_VWAP_MIN")
    dir_pct_5m_min = _env_float_opt("QG_PUT_SS_DIR_PCT_5M_MIN")
    chain_oi_max = _env_float_opt("QG_PUT_SS_TOTAL_CHAIN_OI_MAX")
    vwap_atr_max = _env_float_opt("QG_PUT_SS_VWAP_ATR_MAX")
    call_chain_min = _env_float_opt("QG_PUT_SS_CALL_CHAIN_MIN")
    break_orb_ratio_max = _env_float_opt("QG_PUT_SS_BREAK_ORB_RATIO_MAX")
    break_dist_ge = _env_float_opt("QG_PUT_SS_BREAK_DIST_GE")

    configured = any(
        v is not None
        for v in (
            rsi_max_a, chain_max_b, rsi_max_b, iv_min_b, spy_max,
            vol_1m_max, call_oi_max, theta_min, spy_min, call_d5m_max,
            abs_vwap_min, dir_pct_5m_min, chain_oi_max, vwap_atr_max,
            call_chain_min, break_orb_ratio_max, break_dist_ge,
        )
    )
    if not configured:
        return _skip(gate_id, "unconfigured")

    total_chain_vol = _ss_total_chain_vol(ctx)
    rsi = _ss_rsi(ctx)
    iv = _ss_iv(ctx)
    spy = _ctx_lookup_float(ctx, "spy_change_pct")
    vol_1m = _ctx_lookup_float(ctx, "volume_1m")
    call_oi = _ctx_lookup_float(ctx, "call_chain_open_interest")
    theta = _ctx_lookup_float(ctx, "theta")
    call_d5m = _ctx_lookup_float(ctx, "call_chain_volume_delta_5m")
    abs_vwap = _ss_vwap_abs(ctx)
    dir_pct_5m = _ctx_lookup_float(ctx, "dir_pct_change_5m")
    chain_oi = _ctx_lookup_float(ctx, "total_chain_open_interest")
    vwap_atr = _ctx_lookup_float(ctx, "distance_from_vwap_atr")
    call_chain = _ctx_lookup_float(ctx, "call_chain_volume", "call_volume")
    if call_chain is None and ctx.call_chain_volume is not None:
        try:
            call_chain = float(ctx.call_chain_volume)
        except (TypeError, ValueError):
            call_chain = None
    break_orb_ratio = _ctx_lookup_float(ctx, "break_distance_orb_ratio")

    fields: Dict[str, Any] = {
        "total_chain_volume": total_chain_vol,
        "rsi": rsi,
        "implied_volatility": iv,
        "spy_change_pct": spy,
        "volume_1m": vol_1m,
        "call_chain_open_interest": call_oi,
        "theta": theta,
        "call_chain_volume_delta_5m": call_d5m,
        "abs_vwap_distance_pct": abs_vwap,
        "dir_pct_change_5m": dir_pct_5m,
        "total_chain_open_interest": chain_oi,
        "distance_from_vwap_atr": vwap_atr,
        "call_chain_volume": call_chain,
        "break_distance_orb_ratio": break_orb_ratio,
    }

    deep = _ss_break_dist_ge_reject(ctx, gate_id, break_dist_ge, fields)
    if deep is not None:
        return deep

    hydrated_put = _put_ss_required_result(
        ctx,
        gate_id,
        moon,
        fields,
        rsi_max_a=rsi_max_a,
        chain_max_b=chain_max_b,
        rsi_max_b=rsi_max_b,
        iv_min_b=iv_min_b,
        spy_max=spy_max,
        vol_1m_max=vol_1m_max,
        call_oi_max=call_oi_max,
        theta_min=theta_min,
        spy_min=spy_min,
        call_d5m_max=call_d5m_max,
        abs_vwap_min=abs_vwap_min,
        dir_pct_5m_min=dir_pct_5m_min,
        chain_oi_max=chain_oi_max,
        vwap_atr_max=vwap_atr_max,
        call_chain_min=call_chain_min,
        break_orb_ratio_max=break_orb_ratio_max,
        total_chain_vol=total_chain_vol,
        rsi=rsi,
        iv=iv,
        spy=spy,
        vol_1m=vol_1m,
        call_oi=call_oi,
        theta=theta,
        call_d5m=call_d5m,
        abs_vwap=abs_vwap,
        dir_pct_5m=dir_pct_5m,
        chain_oi=chain_oi,
        vwap_atr=vwap_atr,
        call_chain=call_chain,
        break_orb_ratio=break_orb_ratio,
    )
    if hydrated_put is not None:
        return hydrated_put
    if moon is not None:
        return _ok(gate_id, f"delta_moon_{_reason_num(moon, 2)}", delta=moon, side=side)

    # p2: RSI <= max_a AND total_chain_vol <= max_b
    if rsi_max_a is not None and chain_max_b is not None:
        if (rsi is not None and total_chain_vol is not None
                and rsi <= rsi_max_a and total_chain_vol <= chain_max_b):
            return _rej(
                gate_id,
                f"p88_p2_rsi_{_reason_num(rsi, 0)}_le_{_reason_num(rsi_max_a, 0)}_chain_{_reason_num(total_chain_vol, 0)}_le_{_reason_num(chain_max_b, 0)}",
                **fields,
            )

    # p4: RSI <= max_b AND IV > min_b
    if rsi_max_b is not None and iv_min_b is not None:
        if (rsi is not None and iv is not None
                and rsi <= rsi_max_b and iv > iv_min_b):
            return _rej(
                gate_id,
                f"p88_p4_rsi_{_reason_num(rsi, 0)}_le_{_reason_num(rsi_max_b, 0)}_iv_{_reason_num(iv, 2)}_gt_{_reason_num(iv_min_b, 1)}",
                **fields,
            )

    if spy_max is not None and spy is not None and spy <= spy_max:
        return _rej(
            gate_id,
            f"p88_spy_le_{_reason_num(spy, 3)}_le_{_reason_num(spy_max, 3)}",
            **fields,
        )

    # vol_1m <= max AND call_OI <= max
    if vol_1m_max is not None and call_oi_max is not None:
        if (vol_1m is not None and call_oi is not None
                and vol_1m <= vol_1m_max and call_oi <= call_oi_max):
            return _rej(
                gate_id,
                f"p88_vol1m_{_reason_num(vol_1m, 0)}_le_{_reason_num(vol_1m_max, 0)}_coi_{_reason_num(call_oi, 0)}_le_{_reason_num(call_oi_max, 0)}",
                **fields,
            )

    # theta >= min AND spy >= min
    if theta_min is not None and spy_min is not None:
        if (theta is not None and spy is not None
                and theta >= theta_min and spy >= spy_min):
            return _rej(
                gate_id,
                f"p88_theta_{_reason_num(theta, 5)}_ge_{_reason_num(theta_min, 5)}_spy_{_reason_num(spy, 3)}_ge_{_reason_num(spy_min, 3)}",
                **fields,
            )

    # call_d5m <= max AND theta >= min
    if call_d5m_max is not None and theta_min is not None:
        if (call_d5m is not None and theta is not None
                and call_d5m <= call_d5m_max and theta >= theta_min):
            return _rej(
                gate_id,
                f"p88_cd5m_{_reason_num(call_d5m, 0)}_le_{_reason_num(call_d5m_max, 0)}_theta_{_reason_num(theta, 5)}_ge_{_reason_num(theta_min, 5)}",
                **fields,
            )

    if abs_vwap_min is not None and abs_vwap is not None and abs_vwap >= abs_vwap_min:
        return _rej(
            gate_id,
            f"p88_absvwap_{_reason_num(abs_vwap, 5)}_ge_{_reason_num(abs_vwap_min, 5)}",
            **fields,
        )

    # dir_pct_5m >= min AND theta >= min
    if dir_pct_5m_min is not None and theta_min is not None:
        if (dir_pct_5m is not None and theta is not None
                and dir_pct_5m >= dir_pct_5m_min and theta >= theta_min):
            return _rej(
                gate_id,
                f"p88_dir5m_{_reason_num(dir_pct_5m, 5)}_ge_{_reason_num(dir_pct_5m_min, 5)}_theta_{_reason_num(theta, 5)}_ge_{_reason_num(theta_min, 5)}",
                **fields,
            )

    if chain_oi_max is not None and chain_oi is not None and chain_oi <= chain_oi_max:
        return _rej(
            gate_id,
            f"p924_chain_oi_{_reason_num(chain_oi, 0)}_le_{_reason_num(chain_oi_max, 0)}",
            **fields,
        )

    # 2026-09-24 tail-safe PUT VWAP. Missing distance_from_vwap_atr admits.
    # PUT only. OR with the pockets above. Does not fire on CALL.
    if vwap_atr_max is not None and vwap_atr is not None and vwap_atr <= vwap_atr_max:
        return _rej(
            gate_id,
            f"p924_vwap_atr_{_reason_num(vwap_atr, 4)}_le_{_reason_num(vwap_atr_max, 4)}",
            **fields,
        )

    if call_chain_min is not None and break_orb_ratio_max is not None:
        if (
            call_chain is not None
            and break_orb_ratio is not None
            and call_chain >= call_chain_min
            and break_orb_ratio <= break_orb_ratio_max
        ):
            return _rej(
                gate_id,
                f"rput_call_chain_{_reason_num(call_chain, 0)}_ge_{_reason_num(call_chain_min, 0)}"
                f"_bor_{_reason_num(break_orb_ratio, 3)}_le_{_reason_num(break_orb_ratio_max, 3)}",
                **fields,
            )

    return _ok(gate_id, "no_dump_pocket", **fields)


# Strategy/side 0-MFE Skip Signals (0MFESkipSignal.md). Pocket = AND; knife = OR.
# One live set per path × side. Future pockets append to that set. Missing stamps
# do not skip. Optional QG_PATH_SS_{BOOK}_{SIDE} replaces the whole set.
_PATH_SS_CLAUSE_RE = re.compile(
    r"^([A-Za-z][A-Za-z0-9_]*)\s*"
    r"(<=|>=|<|>|==|=)\s*"
    r"(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)$",
    re.IGNORECASE,
)
_PATH_SS_OPS = {
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "==": lambda a, b: abs(a - b) <= 1e-9,
    "=": lambda a, b: abs(a - b) <= 1e-9,
}
_PATH_SS_IDS: Dict[str, Dict[str, str]] = {
    "ORB1": {"CALL": "SSORB1CALL", "PUT": "SSORB1PUT"},
    "TL0": {"CALL": "SSTL0CALL", "PUT": "SSTL0PUT"},
    "ORB2": {"CALL": "SSORB2CALL", "PUT": "SSORB2PUT"},
    "TL1": {"CALL": "SSTL1CALL", "PUT": "SSTL1PUT"},
    "TL2PLUS": {"CALL": "SSTL2CALL", "PUT": "SSTL2PUT"},
}

_PathSsPocket = Tuple[Tuple[str, str, float], ...]
# Accepted 9.21 Path SS (PC-verified 9.10–9.21 bid-aware book).
# Champions: ORB1/TL0/ORB2/TL1/TL2+ × CALL/PUT.
# ORB2 CALL and TL2+ PUT are t40; all others t80.
# Pocket = AND; knife = OR. Missing stamps do not skip.
# Artifact: docs/0.ProfitabilityThresholds/9.21/ProfitThresholds9.21.md
_PATH_SS_KNIVES: Dict[str, Dict[str, Tuple[_PathSsPocket, ...]]] = {
    "ORB1": {
        # ORB1-CALL-SS-PC921-t80
        "CALL": (
            (('owned_contract_volume_delta_5m', '<=', 1238.0),),
            (('volume_pct_of_day_5m', '<=', 0.012692510440207527),),
            (('total_chain_volume', '<=', 2377.0),),
            (('price_change_1m_pct', '<=', -0.25355303810503355),),
            (('macd_signal', '<=', 0.02839888774181784),),
            (('representative_spread_pct', '<=', 0.012903225806451623),),
            (('rs_vs_spy', '<=', -1.5390717593781835),),
            (('relative_volume_time_adjusted', '<=', 0.667027532965384),),
            (('underlying_bid_size', '<=', 1.0),),
            (('price_change_1m_pct', '<=', -0.2448904145761757),),
            (('atr', '>=', 2.8950014822823675),),
            (('volume_vs_prior_5m', '>=', 50.64106971742899),),
            (('distance_from_vwap_atr', '>=', 22.726567221012296),),
            (('put_chain_volume', '<=', 4669.0), ('cumulative_volume', '>=', 5644.0),),
        ),
        # ORB1-PUT-SS-PC921-t80
        "PUT": (
            (('quality_score', '<=', 42.00898968820868),),
            (('underlying_l1_imbalance_delta_15s', '<=', -1.0127591706539074),),
            (('price_change_3m_pct', '<=', -0.7636342672662252),),
            (('volume_3m', '<=', 333713.0),),
            (('session_bar_count', '<=', 4.0),),
            (('avg_bid_ask_spread_pct', '<=', 4.97737993561017),),
            (('total_chain_volume', '<=', 1621.0),),
        ),
    },
    "TL0": {
        # TL0-CALL-SS-PC921-t80
        "CALL": (
            (('total_chain_volume', '<=', 5443.0),),
            (('same_side_chain_volume_5m_ago', '<=', 6089.0),),
            (('price_volume_efficiency_1m', '<=', 0.1348550746765691),),
            (('owned_contract_volume_delta_5m', '<=', -13299.0),),
            (('atr', '<=', 0.1146428571428554),),
            (('put_chain_open_interest', '<=', 6295.0),),
            (('spy_change_pct', '>=', 1.3863907889036151),),
            (('representative_delta', '>=', 0.40007),),
        ),
        # TL0-PUT-SS-PC921-t80
        "PUT": (
            (('underlying_bid_size', '>=', 193.0),),
            (('underlying_l1_imbalance_delta_15s', '<=', -0.8807215800114043),),
            (('break_distance_atr', '<=', 0.31244863566006725),),
            (('open_interest', '<=', 344.0),),
            (('gamma', '<=', 0.0169),),
            (('direction_volume_pct', '<=', 0.21461026070845649),),
            (('session_bar_count', '>=', 29.0),),
            (('orb_range_pct', '>=', 4.7225501770956395),),
        ),
    },
    "ORB2": {
        # ORB2-CALL-SS-PC921-t40
        "CALL": (
            (('put_chain_volume', '<=', 2184.0),),
            (('cumulative_volume', '<=', 255.0),),
            (('same_side_chain_volume_5m_ago', '<=', 1150.0),),
            (('volume_vs_prior_1m', '<=', 0.23816827586206896),),
            (('underlying_bid_size', '>=', 5539.0),),
            (('session_vs_avg_volume', '<=', 0.2275817143917931),),
            (('break_distance_pct', '<=', 0.0008318083513558003),),
            (('distance_from_orb_edge_atr', '>=', 11.594545454544724),),
            (('vega', '>=', 0.1041),),
            (('underlying_l1_imbalance_delta_15s', '<=', -1.5238160012695823),),
            (('vwap_distance_pct', '<=', 0.27062014031678905),),
            (('underlying_l1_imbalance_delta_15s', '>=', 0.8880616174582798),),
            (('underlying_ask_size', '<=', 5.0),),
            (('underlying_spread_pct', '>=', 0.0018808040437288212),),
            (('break_distance_pct', '>=', 0.009862778730703277),),
            (('macd', '<=', -0.010337591820311687),),
            (('seconds_since_break', '<=', 0.403184),),
            (('spy_change_pct', '>=', 1.1259200318281295),),
            (('volume_rate_1m', '>=', 8155.266666666666),),
            (('total_chain_open_interest', '>=', 366288.0),),
            (('entry_bar_range_pct', '>=', 0.0031532021311296296),),
            (('total_chain_volume', '<=', 67487.0), ('call_chain_volume', '>=', 67941.0),),
            (('put_chain_volume', '<=', 14634.0), ('opposite_side_chain_volume_delta_5m', '>=', 11562.0),),
            (('put_chain_volume', '<=', 2971.0), ('theta', '>=', -0.0449),),
            (('total_chain_volume', '>=', 11331.0), ('call_chain_volume', '<=', 11331.0),),
            (('total_chain_volume', '>=', 17160.0), ('call_chain_volume', '<=', 17160.0),),
            (('put_chain_volume', '<=', 10386.0), ('same_side_chain_volume_5m_ago', '>=', 31792.0),),
            (('put_chain_volume', '<=', 33172.0), ('option_l1_imbalance', '<=', -0.9148936170212766),),
            (('put_chain_volume', '<=', 37771.0), ('opposite_side_chain_volume_delta_5m', '>=', 30546.0),),
        ),
        # ORB2-PUT-SS-PC921-t80
        "PUT": (
            (('recent_higher_high_count', '<=', 0.0),),
            (('underlying_spread_pct', '>=', 0.0010632642211589982),),
            (('option_bid_size', '>=', 771.0),),
            (('same_side_chain_volume_5m_ago', '<=', 389.0),),
            (('spy_change_pct', '>=', 0.33932320204914906),),
            (('minutes_from_730_to_break', '>=', 292.59557041666665),),
            (('seconds_since_break', '>=', 32.918276),),
            (('atr', '<=', 0.011742857142858196),),
            (('session_vs_avg_volume', '<=', 0.3350066738023232),),
            (('break_distance_pct', '>=', 0.0133754896324916),),
            (('quality_score', '<=', 23.79526516017971),),
            (('rsi', '<=', 11.127308066083131),),
            (('chain_sanity_best_same_side_spread_pct', '>=', 20.895522388059707),),
            (('theta', '<=', -1.63526),),
            (('momentum_10', '>=', 0.08224984973584856),),
            (('macd', '>=', 0.04821281919038256),),
            (('session_bar_count', '>=', 215.0),),
            (('implied_volatility', '<=', 0.24098),),
            (('call_chain_open_interest', '>=', 209291.0),),
            (('representative_spread_pct', '>=', 0.28571428571428575),),
            (('minutes_from_730_to_break', '>=', 270.57726328333337), ('session_bar_count', '<=', 179.0),),
            (('minutes_from_730_to_break', '<=', 149.69340543333334), ('orb_volume_ratio', '>=', 11.181067339023796),),
            (('minutes_from_730_to_break', '>=', 232.18209213333333), ('session_bar_count', '<=', 159.0),),
            (('total_chain_volume', '<=', 8605.0), ('cumulative_volume', '>=', 2344.0),),
        ),
    },
    "TL1": {
        # TL1-CALL-SS-PC921-t80
        "CALL": (
            (('volume_3m', '<=', 14244.0),),
            (('price_volume_efficiency_5m', '<=', 0.0),),
            (('break_distance_atr', '<=', 0.6410783260690615),),
            (('option_ask_size', '>=', 1155.0),),
            (('seconds_since_break', '>=', 196.091162),),
            (('vega', '>=', 0.09214),),
            (('volume_acceleration_1m_vs_5m', '>=', 2.660081840656999),),
            (('dir_pct_change_5m', '>=', 0.27037222122950977),),
            (('representative_bid', '>=', 2.28),),
            (('volume_vs_prior_5m', '<=', 0.4324505574578118),),
            (('put_call_volume_ratio', '<=', 0.08069341781063007),),
            (('cumulative_volume', '<=', 67.0),),
            (('orb_volume_ratio', '>=', 2.419120992089047),),
            (('volume_pct_of_day_5m', '>=', 0.214803455342905),),
            (('price_volume_efficiency_3m', '>=', 8.06165839841615),),
            (('break_distance_pct', '<=', 0.0009067490047480808),),
            (('otm_pct', '>=', 0.0062148564664102485),),
            (('total_chain_open_interest', '>=', 441045.0),),
            (('macd', '>=', 2.9610551541532004),),
            (('spy_change_pct', '>=', 1.1047012797559899),),
            (('representative_bid', '<=', 0.11),),
            (('delta', '<=', 0.18724),),
            (('macd_signal', '<=', -1.4809966432714434),),
            (('vwap_distance_pct', '<=', -2.263766081677913),),
            (('vwap_distance_pct', '>=', 3.370664076053414),),
            (('atr', '<=', 0.015364285714285688),),
            (('put_chain_volume', '<=', 435.0),),
            (('total_chain_volume', '>=', 36949.0), ('call_chain_volume', '<=', 36949.0),),
            (('total_chain_volume', '>=', 11414.0), ('call_chain_volume', '<=', 11414.0),),
            (('total_chain_volume', '<=', 13343.0), ('call_chain_volume', '>=', 15065.0),),
            (('total_chain_volume', '<=', 2430.0), ('cumulative_volume', '>=', 686.0),),
            (('put_chain_volume', '>=', 163484.0), ('spread_candidate_count', '<=', 10.0),),
            (('total_chain_volume', '>=', 2858.0), ('call_chain_volume', '<=', 2858.0),),
            (('put_chain_volume', '>=', 3622.0), ('total_chain_volume', '<=', 2488.0),),
            (('put_chain_volume', '<=', 3147.0), ('spread_candidate_count', '>=', 60.0),),
            (('put_chain_volume', '>=', 5986.0), ('relative_volume_time_adjusted', '<=', 0.6179577232962636),),
            (('put_chain_volume', '>=', 896.0), ('chain_sanity_best_same_side_volume', '<=', 246.0),),
            (('total_chain_volume', '<=', 6994.0), ('same_side_chain_volume_5m_ago', '>=', 5804.0),),
            (('put_chain_volume', '>=', 6124.0), ('total_chain_volume', '<=', 6717.0),),
            (('total_chain_volume', '<=', 22520.0), ('call_chain_volume', '>=', 22520.0),),
            (('put_chain_volume', '>=', 2598.0), ('price_volume_efficiency_5m', '>=', 4.836518039763911),),
            (('total_chain_volume', '<=', 16239.0), ('same_side_chain_volume_5m_ago', '>=', 15129.0),),
            (('total_chain_volume', '<=', 3866.0), ('call_chain_volume', '>=', 3866.0),),
            (('put_chain_volume', '<=', 1564.0), ('same_side_chain_volume_5m_ago', '>=', 6906.0),),
            (('total_chain_volume', '<=', 6075.0), ('spread_candidate_count', '>=', 24.0),),
        ),
        # TL1-PUT-SS-PC921-t80
        "PUT": (
            (('volume_1m', '<=', 13943.0),),
            (('chain_sanity_best_same_side_volume', '<=', 250.0),),
            (('underlying_ask_size', '<=', 1.0),),
            (('session_volume', '<=', 1252937.0),),
            (('total_chain_volume', '<=', 868.0),),
            (('session_bar_count', '<=', 38.0),),
            (('put_call_volume_ratio', '>=', 2.6533030352215548),),
            (('avg_bid_ask_spread_pct', '>=', 32.023837816684185),),
            (('session_bar_count', '>=', 77.0),),
            (('recent_higher_high_count', '>=', 6.0),),
            (('relative_volume_time_adjusted', '>=', 2.8409309725354657),),
            (('representative_bid', '<=', 0.05),),
            (('spy_change_pct', '<=', -0.7994347695246586),),
            (('consecutive_directional_bars', '<=', 0.0),),
            (('price_change_5m_pct', '<=', -0.5442498816848065),),
            (('quality_score', '<=', 54.03057735037072),),
            (('underlying_ask_size', '>=', 26130.0),),
            (('relative_volume_time_adjusted', '<=', 0.8515870037344799),),
            (('session_volume', '>=', 38168994.0),),
            (('call_chain_open_interest', '>=', 474851.0),),
            (('spy_change_pct', '>=', 1.023871096081164),),
            (('atr', '<=', 0.00981428571428568),),
            (('underlying_quote_age_sec', '>=', 108.6389811038971),),
            (('cumulative_volume', '<=', 77.0),),
            (('representative_spread_pct', '>=', 0.23809523809523805),),
            (('price_volume_efficiency_1m', '>=', 10.390939950111054),),
            (('vega', '>=', 0.10141),),
            (('rs_vs_spy', '>=', 5.210119326069258),),
            (('total_chain_volume', '<=', 3333.0), ('put_chain_volume', '>=', 3340.0),),
            (('total_chain_volume', '<=', 3163.0), ('put_chain_volume', '>=', 3163.0),),
            (('chain_sanity_best_same_side_volume', '>=', 3071.0), ('avg_bid_ask_spread_pct', '>=', 26.548575928159117),),
        ),
    },
    "TL2PLUS": {
        # TL2P-CALL-SS-PC921-t80
        "CALL": (
            (('underlying_l1_imbalance_delta_15s', '<=', -0.04366387355545848),),
            (('distance_from_orb_edge_atr', '<=', -3.170806188925149),),
            (('entry_bar_range_pct', '<=', 0.0003188902618886774),),
            (('chain_sanity_best_same_side_spread_pct', '<=', 2.222222222222224),),
            (('call_chain_volume', '<=', 6237.0),),
            (('chain_sanity_best_same_side_spread_pct', '>=', 22.222222222222225),),
            (('theta', '>=', -0.06489),),
            (('cumulative_volume', '<=', 293.0),),
            (('otm_pct', '>=', 0.005490507375802161),),
            (('macd', '>=', 0.1319471320015282),),
            (('underlying_spread_pct', '>=', 0.0018064927474629588),),
            (('price_volume_efficiency_3m', '<=', 0.06488701706795887),),
        ),
        # TL2P-PUT-SS-PC921-t40
        "PUT": (
            (('call_chain_volume_delta_5m', '<=', 813.0),),
            (('underlying_quote_age_sec', '<=', 3.708765983581543),),
            (('call_chain_open_interest', '<=', 5295.0),),
            (('session_volume', '<=', 1852796.0),),
            (('price_volume_efficiency_1m', '<=', 0.0),),
            (('session_vs_avg_volume', '<=', 0.19721755551192652),),
            (('option_ask_size', '<=', 1.0),),
            (('underlying_quote_age_sec', '>=', 41.16478610038757),),
            (('option_ask_size', '>=', 197.0),),
            (('session_bar_count', '>=', 219.0),),
            (('put_chain_open_interest', '>=', 99073.0),),
            (('session_bar_count', '<=', 64.0),),
            (('representative_spread_pct', '>=', 0.2758620689655172),),
            (('avg_bid_ask_spread_pct', '>=', 47.29662229945539),),
            (('total_chain_volume', '>=', 459569.0),),
            (('theta', '<=', -1.61239),),
            (('option_bid_size', '>=', 341.0),),
            (('otm_pct', '>=', 0.007248660573589698),),
            (('seconds_since_break', '>=', 56.512455),),
            (('spy_change_pct', '>=', 1.232771862568748),),
        ),
    },
}

_PATH_SS_FEATURE_KEYS: Tuple[str, ...] = (
    # Oct 7 2026: 10.5 chain vol/OI ratio keys ride the same admit spine.
    "total_chain_vol_oi",
    "call_chain_vol_oi",
    "put_chain_vol_oi",
    "atr",
    "avg_bid_ask_spread_pct",
    "break_distance_atr",
    "break_distance_pct",
    "bollinger_position",
    "bollinger_width",
    "call_chain_open_interest",
    "call_chain_volume",
    "call_chain_volume_delta_5m",
    "chain_sanity_best_same_side_spread_pct",
    "chain_sanity_best_same_side_volume",
    "consecutive_directional_bars",
    "cumulative_volume",
    "delta",
    "dir_pct_change_5m",
    "direction_volume_pct",
    "distance_from_orb_edge_atr",
    "distance_from_vwap_atr",
    "entry_bar_range_pct",
    "gamma",
    "implied_volatility",
    "macd",
    "macd_signal",
    "minutes_from_730_to_break",
    "momentum_10",
    "open_interest",
    "opposite_side_chain_volume_delta_5m",
    "option_ask_size",
    "option_bid_size",
    "option_l1_imbalance",
    "orb_range_pct",
    "orb_volume_ratio",
    "otm_pct",
    "owned_contract_volume_delta_5m",
    "price_change_1m_pct",
    "price_change_3m_pct",
    "price_change_5m_pct",
    "price_volume_efficiency_1m",
    "price_volume_efficiency_3m",
    "price_volume_efficiency_5m",
    "put_call_volume_ratio",
    "put_chain_open_interest",
    "put_chain_volume",
    "quality_score",
    "recent_higher_high_count",
    "relative_volume_time_adjusted",
    "representative_ask",
    "representative_bid",
    "representative_delta",
    "representative_spread_pct",
    "rs_vs_spy",
    "rsi",
    "same_side_chain_volume_5m_ago",
    "seconds_since_break",
    "session_bar_count",
    "session_volume",
    "session_vs_avg_volume",
    "spread_candidate_count",
    "spy_change_pct",
    "theta",
    "total_chain_open_interest",
    "total_chain_volume",
    "underlying_ask_size",
    "underlying_bid_size",
    "underlying_l1_imbalance",
    "underlying_l1_imbalance_delta_15s",
    "underlying_quote_age_sec",
    "underlying_spread_pct",
    "vega",
    "volume_1m",
    "volume_3m",
    "volume_5m",
    "volume_acceleration_1m_vs_5m",
    "volume_pct_of_day_5m",
    "volume_rate_1m",
    "volume_vs_prior_1m",
    "volume_vs_prior_5m",
    "vwap_distance_pct",
    "abs_vwap_distance_pct",
)

# PRUNED-8/8 Absolute CALL/PUT SS — must be on the admit row (or aliases) before evaluate.
_ABS_SS_HYDRATE_KEYS: Tuple[str, ...] = (
    "rsi",
    "cumulative_volume",
    "representative_spread_pct",
    "total_chain_volume",
    "put_chain_volume",
    "break_distance_pct",
    "volume_5m",
    "dir_pct_change_5m",
    "relative_volume_time_adjusted",
    "representative_ask",
    "same_side_chain_volume",
    "same_side_chain_volume_5m_ago",
    "price_volume_efficiency_3m",
    "total_chain_open_interest",
    "implied_volatility",
    "spy_change_pct",
    "volume_1m",
    "call_chain_open_interest",
    "theta",
    "call_chain_volume_delta_5m",
    "vwap_distance_pct",
    "abs_vwap_distance_pct",
    "distance_from_vwap_atr",
)

_PATH_SS_PC921_IDS: Dict[str, Dict[str, str]] = {
    "ORB1": {"CALL": "ORB1-CALL-SS-PC921-t80", "PUT": "ORB1-PUT-SS-PC921-t80"},
    "TL0": {"CALL": "TL0-CALL-SS-PC921-t80", "PUT": "TL0-PUT-SS-PC921-t80"},
    "ORB2": {"CALL": "ORB2-CALL-SS-PC921-t40", "PUT": "ORB2-PUT-SS-PC921-t80"},
    "TL1": {"CALL": "TL1-CALL-SS-PC921-t80", "PUT": "TL1-PUT-SS-PC921-t80"},
    "TL2PLUS": {"CALL": "TL2P-CALL-SS-PC921-t80", "PUT": "TL2P-PUT-SS-PC921-t40"},
}

def _ss_book(ctx: GateContext) -> Optional[str]:
    """ORB1 / ORB2 / TL0 / TL1 / TL2PLUS (TL2–TL10). Unknown TL slot → None (do not skip)."""
    if ctx.path == PATH_ORB:
        return "ORB1"
    if ctx.path == PATH_BO:
        return "ORB2"
    if ctx.path != PATH_TL:
        return None
    setup = (ctx.signal or {}).get("setup_payload")
    raw = (
        (ctx.extras or {}).get("trendline_slot")
        or (ctx.signal or {}).get("trendline_slot")
        or (setup.get("trendline_slot") if isinstance(setup, dict) else None)
    )
    slot = str(raw or "").strip().lower().replace(" ", "")
    if not slot:
        return None
    if slot in ("trendline0", "tl0"):
        return "TL0"
    if slot in ("trendline1", "tl1"):
        return "TL1"
    n: Optional[int] = None
    if slot.startswith("trendline") and slot[len("trendline") :].isdigit():
        n = int(slot[len("trendline") :])
    elif slot.startswith("tl") and slot[2:].isdigit():
        n = int(slot[2:])
    elif slot.isdigit():
        n = int(slot)
    if n == 0:
        return "TL0"
    if n == 1:
        return "TL1"
    if n is not None and n >= 2:
        return "TL2PLUS"
    return None


def _ss_bb(ctx: GateContext) -> Optional[float]:
    bb = _bb_position(ctx)
    if bb is None:
        return None
    if 0.0 <= float(bb) <= 1.5:
        return float(bb) * 100.0
    return float(bb)


def _ss_vwap_with(ctx: GateContext) -> Optional[float]:
    signed = _ctx_lookup_float(ctx, "vwap_with")
    if signed is not None:
        return signed
    dist = _ctx_lookup_float(ctx, "vwap_distance_pct")
    if dist is None:
        return None
    return float(dist) if _ss_side(ctx) == "CALL" else -float(dist)


def _ss_l1d5(ctx: GateContext) -> Optional[float]:
    with_side = _ctx_lookup_float(ctx, "l1_delta_5s_with")
    if with_side is not None:
        return with_side
    imb = _ctx_lookup_float(
        ctx,
        "underlying_l1_imbalance_delta_5s",
        "l1_imbalance_delta_5s",
    )
    if imb is None:
        return None
    return float(imb) if _ss_side(ctx) == "CALL" else -float(imb)


def _ss_l1raw(ctx: GateContext) -> Optional[float]:
    return _ctx_lookup_float(
        ctx,
        "underlying_l1_imbalance_delta_5s",
        "l1_imbalance_delta_5s",
        "l1raw",
    )


def _ss_seconds(ctx: GateContext) -> Optional[float]:
    """Break age at evaluate time. Refresh stale 0.0 from break clocks when present."""
    sec = _ctx_lookup_float(ctx, "seconds_since_break")
    # Same-bar / break-detect stamps leave 0.0; Path SS seconds<=N then false-fires.
    if sec is not None and abs(float(sec)) > 1e-12:
        return float(sec)
    raw_break = None
    for src in (ctx.extras or {}, ctx.signal or {}):
        if not isinstance(src, dict):
            continue
        for key in (
            "break_timestamp",
            "break_ts",
            "break_time",
            "structure_break_ts",
            "material_break_at",
        ):
            if src.get(key) is not None and src.get(key) != "":
                raw_break = src.get(key)
                break
        if raw_break is not None:
            break
    if raw_break is None:
        # Stale exact 0.0 without a break clock → treat as missing (do not skip).
        return None if sec is not None and abs(float(sec)) <= 1e-12 else sec
    try:
        from modules.session_tech_admit import refresh_seconds_since_break

        row: Dict[str, Any] = {}
        if isinstance(ctx.extras, dict):
            row.update(ctx.extras)
        if isinstance(ctx.signal, dict):
            for k, v in ctx.signal.items():
                row.setdefault(k, v)
        refresh_seconds_since_break(row)
        refreshed = row.get("seconds_since_break")
        if refreshed is not None:
            try:
                out = float(refreshed)
            except (TypeError, ValueError):
                return None
            if isinstance(ctx.extras, dict):
                ctx.extras["seconds_since_break"] = out
            return out
    except Exception:
        pass
    return None if sec is not None and abs(float(sec)) <= 1e-12 else sec


def _ss_break_atr(ctx: GateContext) -> Optional[float]:
    return _ctx_lookup_float(ctx, "break_distance_atr", "break_atr")


def _ss_pve1(ctx: GateContext) -> Optional[float]:
    return _ctx_lookup_float(ctx, "price_volume_efficiency_1m", "pve1")


def _ss_pve5(ctx: GateContext) -> Optional[float]:
    return _ctx_lookup_float(ctx, "price_volume_efficiency_5m", "pve5")


def _ss_delta_abs(ctx: GateContext) -> Optional[float]:
    if ctx.delta is not None:
        try:
            return abs(float(ctx.delta))
        except (TypeError, ValueError):
            pass
    v = _ctx_lookup_float(
        ctx, "delta", "chosen_delta", "fill_delta", "representative_delta"
    )
    return abs(v) if v is not None else None


def _ss_otm(ctx: GateContext) -> Optional[float]:
    if ctx.otm_pct is not None:
        try:
            return float(ctx.otm_pct)
        except (TypeError, ValueError):
            pass
    return _ctx_lookup_float(ctx, "otm_pct", "otm")


def _ss_body(ctx: GateContext) -> Optional[float]:
    return _ctx_lookup_float(ctx, "entry_bar_body_pct", "body_pct", "body")


def _ss_wick(ctx: GateContext) -> Optional[float]:
    """Wick against continuation: CALL upper wick, PUT lower wick."""
    named = _ctx_lookup_float(ctx, "wick_against", "wick")
    if named is not None:
        return named
    if _ss_side(ctx) == "PUT":
        return _ctx_lookup_float(ctx, "entry_bar_lower_wick_pct")
    return _ctx_lookup_float(ctx, "entry_bar_upper_wick_pct")


def _ss_close(ctx: GateContext) -> Optional[float]:
    """Close-with: CALL close position, PUT 1 - close position."""
    named = _ctx_lookup_float(ctx, "close_with")
    if named is not None:
        return named
    pos = _ctx_lookup_float(ctx, "entry_bar_close_position")
    if pos is None:
        return None
    return float(pos) if _ss_side(ctx) == "CALL" else (1.0 - float(pos))


def _path_ss_lookup(ctx: GateContext, field: str) -> Optional[float]:
    """Resolve a Path SS knife field from GateContext. Never invent."""
    key = str(field or "").strip()
    if not key:
        return None
    aliases = {
        "vol": _ss_vol,
        "seconds": _ss_seconds,
        "l1d5": _ss_l1d5,
        "l1raw": _ss_l1raw,
        "dir": _ss_dir,
        "iv": _ss_iv,
        "bb": _ss_bb,
        "vwap_abs": _ss_vwap_abs,
        "sva": _ss_sva,
        "vwap_with": _ss_vwap_with,
        "rsi": _ss_rsi,
        "break_atr": _ss_break_atr,
        "pve1": _ss_pve1,
        "pve5": _ss_pve5,
        "otm": _ss_otm,
        "wick": _ss_wick,
        "body": _ss_body,
        "close": _ss_close,
    }
    getter = aliases.get(key)
    if getter is not None:
        return getter(ctx)
    if key in ("seconds_since_break",):
        return _ss_seconds(ctx)
    if key in ("implied_volatility",):
        return _ss_iv(ctx)
    if key in ("session_vs_avg_volume", "session_vs_avg"):
        return _ss_sva(ctx)
    if key in ("volume_ratio",):
        return _ss_vol(ctx)
    if key in ("rsi_14",):
        return _ss_rsi(ctx)
    if key in ("direction_volume_pct",):
        return _ss_dir(ctx)
    if key in ("total_chain_volume",):
        named = _ctx_lookup_float(ctx, "total_chain_volume")
        if named is not None:
            return named
        return _ss_total_chain_vol(ctx)
    if key in ("cumulative_volume", "cum_vol"):
        return _ss_cumulative_volume(ctx)
    if key in ("representative_spread_pct", "avg_bid_ask_spread_pct", "spread_pct"):
        if key == "representative_spread_pct":
            named = _ctx_lookup_float(ctx, "representative_spread_pct")
            if named is not None:
                return named
        if key == "avg_bid_ask_spread_pct":
            named = _ctx_lookup_float(ctx, "avg_bid_ask_spread_pct")
            if named is not None:
                return named
        return _ss_spread_pct(ctx)
    if key == "quality_score":
        if ctx.quality_score is not None:
            try:
                return float(ctx.quality_score)
            except (TypeError, ValueError):
                pass
        return _ctx_lookup_float(
            ctx, "quality_score", "eligibility_score", "convex_score", "score"
        )
    if key in ("delta", "representative_delta", "chosen_delta"):
        if key == "delta" and ctx.delta is not None:
            try:
                return float(ctx.delta)
            except (TypeError, ValueError):
                pass
        return _ctx_lookup_float(
            ctx, key, "delta", "representative_delta", "chosen_delta", "fill_delta"
        )
    if key == "otm_pct":
        return _ss_otm(ctx)
    if key == "open_interest":
        if ctx.contract_oi is not None:
            try:
                return float(ctx.contract_oi)
            except (TypeError, ValueError):
                pass
        return _ctx_lookup_float(
            ctx, "open_interest", "contract_oi", "representative_open_interest"
        )
    if key == "orb_volume_ratio":
        named = _ctx_lookup_float(ctx, "orb_volume_ratio")
        if named is not None:
            return named
        return _ss_vol(ctx)
    if key == "orb_range_pct":
        if ctx.range_pct is not None:
            try:
                return float(ctx.range_pct)
            except (TypeError, ValueError):
                pass
        return _ctx_lookup_float(ctx, "orb_range_pct", "range_pct")
    if key == "call_chain_volume":
        if ctx.call_chain_volume is not None:
            try:
                return float(ctx.call_chain_volume)
            except (TypeError, ValueError):
                pass
        return _ctx_lookup_float(ctx, "call_chain_volume", "call_volume")
    if key == "put_chain_volume":
        if ctx.put_chain_volume is not None:
            try:
                return float(ctx.put_chain_volume)
            except (TypeError, ValueError):
                pass
        return _ctx_lookup_float(ctx, "put_chain_volume", "put_volume")
    if key == "put_call_volume_ratio":
        named = _ctx_lookup_float(ctx, "put_call_volume_ratio")
        if named is not None:
            return named
        call_v = _path_ss_lookup(ctx, "call_chain_volume")
        put_v = _path_ss_lookup(ctx, "put_chain_volume")
        if call_v is not None and put_v is not None and float(call_v) > 0:
            return float(put_v) / float(call_v)
        return None
    return _ctx_lookup_float(ctx, key)


def _path_ss_fields(ctx: GateContext) -> Dict[str, Optional[float]]:
    values: Dict[str, Optional[float]] = {
        "vol": _ss_vol(ctx),
        "seconds": _ss_seconds(ctx),
        "l1d5": _ss_l1d5(ctx),
        "l1raw": _ss_l1raw(ctx),
        "dir": _ss_dir(ctx),
        "iv": _ss_iv(ctx),
        "bb": _ss_bb(ctx),
        "vwap_abs": _ss_vwap_abs(ctx),
        "sva": _ss_sva(ctx),
        "vwap_with": _ss_vwap_with(ctx),
        "rsi": _ss_rsi(ctx),
        "break_atr": _ss_break_atr(ctx),
        "pve1": _ss_pve1(ctx),
        "pve5": _ss_pve5(ctx),
        "delta": _ss_delta_abs(ctx),
        "otm": _ss_otm(ctx),
        "wick": _ss_wick(ctx),
        "body": _ss_body(ctx),
        "close": _ss_close(ctx),
    }
    for key in _PATH_SS_FEATURE_KEYS:
        values[key] = _path_ss_lookup(ctx, key)
    return values


def _path_ss_stamp(book: Optional[str], side: str) -> str:
    return (_PATH_SS_IDS.get(book or "") or {}).get(side, "path_ss")


def _parse_path_ss_formula(
    raw: str,
) -> Optional[Tuple[_PathSsPocket, ...]]:
    pockets: List[_PathSsPocket] = []
    for pocket_raw in str(raw or "").split("|"):
        pocket_raw = pocket_raw.strip()
        if not pocket_raw:
            continue
        clauses: List[Tuple[str, str, float]] = []
        for part in pocket_raw.split(","):
            part = part.strip()
            if not part:
                continue
            m = _PATH_SS_CLAUSE_RE.match(part)
            if not m:
                return None
            field, op, num = m.group(1).lower(), m.group(2), float(m.group(3))
            clauses.append((field, op, num))
        if clauses:
            pockets.append(tuple(clauses))
    return tuple(pockets) if pockets else None


def _path_ss_pockets(
    book: str, side: str
) -> Optional[Tuple[_PathSsPocket, ...]]:
    override = _env_str_opt(f"QG_PATH_SS_{book}_{side}")
    if override:
        parsed = _parse_path_ss_formula(override)
        if parsed:
            return parsed
    return (_PATH_SS_KNIVES.get(book) or {}).get(side)


def _eval_path_ss(ctx: GateContext) -> GateResult:
    """Per-strategy, per-side 0-MFE Skip Signal. Same step as CALL/PUT SS.

    Reject if this path/side knife matches. Absolute CALL/PUT SS is a separate
    gate — either reject blocks admit. Missing stamps do not skip.
    """
    side = _ss_side(ctx)
    book = _ss_book(ctx)
    stamp_id = _path_ss_stamp(book, side)
    if book is None or side not in ("CALL", "PUT"):
        return _skip("path_ss", "path_ss_unknown_book", side=side, book=book)
    pockets = _path_ss_pockets(book, side)
    if not pockets:
        return _skip("path_ss", "unconfigured", side=side, book=book, stamp_id=stamp_id)

    values = _path_ss_fields(ctx)
    pc921 = (_PATH_SS_PC921_IDS.get(book) or {}).get(side, stamp_id)
    fields: Dict[str, Any] = {
        "book": book,
        "side": side,
        "stamp_id": stamp_id,
        "pc921_stamp": pc921,
        **values,
    }
    for idx, pocket in enumerate(pockets, start=1):
        hit = True
        for field, op, thresh in pocket:
            val = values.get(field)
            cmp = _PATH_SS_OPS.get(op)
            if val is None or cmp is None or not cmp(float(val), float(thresh)):
                hit = False
                break
        if hit:
            bits = "_".join(
                f"{field}_{op}{_reason_num(thresh, 3)}" for field, op, thresh in pocket
            )
            return _rej(
                "path_ss",
                f"{stamp_id}_p{idx}_{bits}",
                **fields,
            )
    return _ok("path_ss", f"{stamp_id}_no_dump_pocket", **fields)


def _eval_ps_floor(ctx: GateContext) -> GateResult:
    ps = ctx.priority_score
    if ps is None and ctx.signal:
        try:
            ps = float(ctx.signal.get("priority_score"))
        except (TypeError, ValueError):
            ps = None
    floor = _env_float("0DTE_MIN_PRIORITY_SCORE_FOR_EXECUTION", 0.30)
    if not _env_bool("0DTE_SKIP_ZERO_PRIORITY_SCORE", True):
        return _skip("PS", "ps_floor_disabled")
    if ps is None:
        return _skip("PS", "priority_score_absent")
    fields = {"priority_score": ps, "ps_min": floor}
    if float(ps) >= floor:
        return _ok("PS", f"ps_{ps:.3f}_ge_{floor:.2f}", **fields)
    return _rej("PS", f"ps_{ps:.3f}_lt_{floor:.2f}", **fields)


def _eval_top_n(ctx: GateContext) -> GateResult:
    pos = ctx.extras.get("rank_position")
    cap = int(_env_float("ORB_0DTE_PRIORITY_RANK_FINAL_BATCH_MAX", 10))
    if pos is None:
        return _skip("top_n", "rank_position_absent")
    try:
        pos_i = int(pos)
    except (TypeError, ValueError):
        return _skip("top_n", "rank_position_invalid")
    fields = {"rank_position": pos_i, "top_n": cap}
    if pos_i <= cap:
        return _ok("top_n", f"rank_{pos_i}_le_{cap}", **fields)
    return _rej("top_n", f"rank_{pos_i}_gt_{cap}", **fields)


def _eval_red_day(ctx: GateContext) -> GateResult:
    if ctx.red_day_blocked is None:
        return _skip("red_day", "red_day_not_armed")
    if ctx.red_day_blocked:
        return _rej("red_day", ctx.red_day_reason or "red_day_blocked")
    return _ok("red_day", ctx.red_day_reason or "red_day_pass")


def _eval_contract_delta(ctx: GateContext) -> GateResult:
    moon = _delta_moon_override(ctx)
    if moon is not None:
        return _ok("delta", f"delta_moon_{_reason_num(moon, 2)}", delta=moon)
    if ctx.delta is None:
        return _skip("delta", "delta_absent")
    dmin = _env_float("0DTE_ENTRY_DELTA_MIN", 0.18)
    dmax = _env_float("0DTE_ENTRY_DELTA_MAX", 0.42)
    ad = abs(float(ctx.delta))
    fields = {"delta": ctx.delta, "delta_min": dmin, "delta_max": dmax}
    if dmin <= ad <= dmax:
        return _ok("delta", f"delta_{ad:.3f}_in_band", **fields)
    return _rej("delta", f"delta_{ad:.3f}_out_of_band", **fields)


def _eval_contract_liq(ctx: GateContext) -> GateResult:
    if ctx.contract_volume is None and ctx.contract_oi is None and ctx.premium is None:
        return _skip("liq", "contract_liquidity_absent")
    min_vol = int(_env_float("0DTE_ENTRY_MIN_VOLUME", 50))
    min_oi = int(_env_float("0DTE_ENTRY_MIN_OPEN_INTEREST", 100))
    min_prem = _env_float("0DTE_MIN_ENTRY_PREMIUM", _env_float("0DTE_ENTRY_MIN_PREMIUM", 0.03))
    max_prem = _env_float("0DTE_MAX_ENTRY_PREMIUM", 0.0)
    fields = {
        "contract_volume": ctx.contract_volume,
        "contract_oi": ctx.contract_oi,
        "premium": ctx.premium,
        "min_volume": min_vol,
        "min_oi": min_oi,
        "min_premium": min_prem,
        "max_premium": max_prem,
    }
    reasons = []
    if ctx.contract_volume is not None and float(ctx.contract_volume) < min_vol:
        reasons.append(f"contract_volume_{ctx.contract_volume}<{min_vol}")
    if ctx.contract_oi is not None and float(ctx.contract_oi) < min_oi:
        reasons.append(f"contract_oi_{ctx.contract_oi}<{min_oi}")
    if ctx.premium is not None and float(ctx.premium) < min_prem:
        reasons.append(f"premium_{ctx.premium}<{min_prem}")
    if max_prem > 0 and ctx.premium is not None and float(ctx.premium) > max_prem:
        reasons.append(f"premium_{ctx.premium}>{max_prem}")
    if reasons:
        return _rej("liq", ";".join(reasons), **fields)
    return _ok("liq", "contract_liquidity_ok", **fields)


def _eval_contract_spread(ctx: GateContext) -> GateResult:
    if ctx.spread_pct is None:
        return _skip("spread", "spread_absent")
    max_sp = _env_float("0DTE_MAX_BID_ASK_SPREAD_PCT", 8.0)
    fields = {"spread_pct": ctx.spread_pct, "max_spread_pct": max_sp}
    if float(ctx.spread_pct) <= max_sp:
        return _ok("spread", f"spread_{ctx.spread_pct:.2f}_le_{max_sp:.2f}", **fields)
    return _rej("spread", f"spread_{ctx.spread_pct:.2f}_gt_{max_sp:.2f}", **fields)


def _resolve_otm_limits(path: str) -> Tuple[float, float, bool]:
    """Shared Layer D OTM — Jul 28 lock ≤5.5% on ORB / TL / BO."""
    path_u = str(path or "").lower()
    default = 0.055
    if "breakout" in path_u or path_u == PATH_BO:
        prefer = _env_float(
            "BREAKOUT_PREFER_MAX_OTM_PCT",
            _env_float("0DTE_ENTRY_OTM_PCT_PREFER", default),
        )
        research = _env_float(
            "BREAKOUT_RESEARCH_MAX_OTM_PCT",
            _env_float("0DTE_ENTRY_OTM_PCT_MAX", default),
        )
        enabled = _env_bool(
            "BREAKOUT_OTM_GATE_ENABLED",
            _env_bool("0DTE_OTM_GATE_ENABLED", True),
        )
    elif "trendline" in path_u or path_u == PATH_TL:
        prefer = _env_float(
            "TRENDLINE_PREFER_MAX_OTM_PCT",
            _env_float("0DTE_ENTRY_OTM_PCT_PREFER", default),
        )
        research = _env_float(
            "TRENDLINE_RESEARCH_MAX_OTM_PCT",
            _env_float("0DTE_ENTRY_OTM_PCT_MAX", default),
        )
        enabled = _env_bool(
            "TRENDLINE_OTM_GATE_ENABLED",
            _env_bool("0DTE_OTM_GATE_ENABLED", True),
        )
    else:
        prefer = _env_float("0DTE_ENTRY_OTM_PCT_PREFER", default)
        research = _env_float("0DTE_ENTRY_OTM_PCT_MAX", default)
        enabled = _env_bool("0DTE_OTM_GATE_ENABLED", True)
    if research < prefer:
        research = prefer
    return prefer, research, enabled


def _eval_otm(ctx: GateContext) -> GateResult:
    if ctx.otm_pct is None:
        return _skip("OTM", "otm_pct_absent")
    prefer, research, enabled = _resolve_otm_limits(ctx.path or "")
    try:
        from easyBreakout.breakout_contract_gates import evaluate_otm_gate

        dec = evaluate_otm_gate(
            float(ctx.otm_pct),
            enabled=enabled,
            prefer_max=prefer,
            research_max=research,
        )
        fields = {
            "otm_pct": dec.otm_pct,
            "prefer_max": prefer,
            "research_max": research,
            "research_exception": dec.research_exception,
            "path": ctx.path,
        }
        return (
            _ok("OTM", dec.reason, **fields)
            if dec.ok
            else _rej("OTM", dec.reason, **fields)
        )
    except Exception:
        otm = float(ctx.otm_pct)
        fields = {"otm_pct": otm, "prefer_max": prefer, "research_max": research, "path": ctx.path}
        if not enabled:
            return _skip("OTM", "otm_gate_disabled", **fields)
        if otm > research:
            return _rej("OTM", f"otm_{otm:.4f}_gt_{research:.4f}", **fields)
        return _ok("OTM", f"otm_{otm:.4f}_ok", **fields)


def _eval_cheap_lotto(ctx: GateContext) -> GateResult:
    if ctx.premium is None or ctx.contract_qty is None:
        return _skip("cheap_lotto", "cheap_lotto_inputs_absent")
    if not _env_bool("BREAKOUT_CHEAP_LOTTO_GUARD_ENABLED", True):
        return _skip("cheap_lotto", "cheap_lotto_disabled")
    max_prem = _env_float("BREAKOUT_CHEAP_LOTTO_MAX_PREMIUM", 0.15)
    max_qty = int(_env_float("BREAKOUT_CHEAP_LOTTO_MAX_QTY", 8))
    near = _env_float("BREAKOUT_CHEAP_LOTTO_NEAR_ATM_OTM_PCT", 0.05)
    prem = float(ctx.premium)
    qty = int(ctx.contract_qty)
    otm = float(ctx.otm_pct or 0.0)
    fields = {"premium": prem, "qty": qty, "otm_pct": otm}
    try:
        from easyBreakout.breakout_contract_gates import evaluate_cheap_lotto_guard

        dec = evaluate_cheap_lotto_guard(premium=prem, quantity=qty, otm_pct=otm)
        ok = bool(getattr(dec, "ok", True))
        reason = str(getattr(dec, "reason", "cheap_lotto"))
        return _ok("cheap_lotto", reason, **fields) if ok else _rej(
            "cheap_lotto", reason, **fields
        )
    except Exception:
        if prem < max_prem and qty >= max_qty and otm > near:
            return _rej("cheap_lotto", "cheap_lotto_reject", **fields)
        return _ok("cheap_lotto", "cheap_lotto_ok", **fields)


def _row_float(ctx: GateContext, *keys: str) -> Optional[float]:
    row = ctx.signal or {}
    for key in keys:
        if row.get(key) is None:
            continue
        try:
            return float(row[key])
        except (TypeError, ValueError):
            continue
    return None


def _eval_stamp_float(
    ctx: GateContext,
    gate_id: str,
    field_name: str,
    *keys: str,
) -> GateResult:
    """Stamp-only gate — records value when present; soft-skip when absent (never invent)."""
    val = _row_float(ctx, *keys)
    fields: Dict[str, Any] = {field_name: val}
    if val is None:
        return _skip(gate_id, "absent", **fields)
    return _ok(gate_id, f"stamped_{_reason_num(val, 4)}", **fields)


def _eval_rsi_stamp(ctx: GateContext) -> GateResult:
    if ctx.extras.get("rsi_missing") or (ctx.signal and ctx.signal.get("rsi_missing")):
        return _skip("RSI_stamp", "missing", rsi=None)
    rsi = ctx.rsi
    if rsi is None:
        rsi = _row_float(ctx, "rsi", "rsi_14")
    fields = {"rsi": rsi}
    if rsi is None:
        return _skip("RSI_stamp", "absent", **fields)
    return _ok("RSI_stamp", f"stamped_{_reason_num(rsi, 2)}", **fields)


def _eval_macd_hist(ctx: GateContext) -> GateResult:
    if ctx.extras.get("macd_missing") or (ctx.signal and ctx.signal.get("macd_missing")):
        return _skip("MACD", "missing", macd_histogram=None)
    macd = ctx.macd_histogram
    if macd is None:
        macd = _row_float(ctx, "macd_histogram", "macd_hist")
    fields = {
        "macd_histogram": macd,
        "ema_12": ctx.ema_12 if ctx.ema_12 is not None else _row_float(ctx, "ema_12"),
        "ema_26": ctx.ema_26 if ctx.ema_26 is not None else _row_float(ctx, "ema_26"),
    }
    if macd is None:
        return _skip("MACD", "absent", **fields)
    return _ok("MACD", f"stamped_{_reason_num(macd, 4)}", **fields)


def _eval_bollinger_upper(ctx: GateContext) -> GateResult:
    return _eval_stamp_float(ctx, "BB_upper", "bollinger_upper", "bollinger_upper", "bb_upper")


def _eval_bollinger_middle(ctx: GateContext) -> GateResult:
    return _eval_stamp_float(
        ctx, "BB_middle", "bollinger_middle", "bollinger_middle", "bb_middle"
    )


def _eval_bollinger_lower(ctx: GateContext) -> GateResult:
    return _eval_stamp_float(ctx, "BB_lower", "bollinger_lower", "bollinger_lower", "bb_lower")


def _eval_bollinger_width(ctx: GateContext) -> GateResult:
    return _eval_stamp_float(ctx, "BB_width", "bollinger_width", "bollinger_width", "bb_width")


def _eval_bollinger_position(ctx: GateContext) -> GateResult:
    return _eval_stamp_float(
        ctx,
        "BB_pctB",
        "bollinger_position",
        "bollinger_position",
        "bb_percent_b",
        "percent_b",
    )


def _eval_call_volume(ctx: GateContext) -> GateResult:
    return _eval_stamp_float(
        ctx,
        "call_vol",
        "call_volume",
        "call_volume",
        "call_chain_volume",
    )


def _eval_put_volume(ctx: GateContext) -> GateResult:
    return _eval_stamp_float(
        ctx,
        "put_vol",
        "put_volume",
        "put_volume",
        "put_chain_volume",
    )


GATE_EVALUATORS: Dict[str, Callable[[GateContext], GateResult]] = {
    "pc_oi": _eval_chain_pc_oi,
    "dir_pct": _eval_chain_dir_pct,
    "call_vol": _eval_call_volume,
    "put_vol": _eval_put_volume,
    "Vol_Ratio": _eval_equity_volume_ratio,
    "VWAP": _eval_equity_vwap,
    "RSI_band": _eval_equity_rsi,
    "RSI": _eval_equity_rsi_anti_chase,
    "BB": _eval_bb_anti_stretch,
    "BB_mid": _eval_bb_mid_stretch,
    "break_max": _eval_break_max,
    "chase": _eval_break_max,
    "RSI_stamp": _eval_rsi_stamp,
    "MACD": _eval_macd_hist,
    "BB_upper": _eval_bollinger_upper,
    "BB_middle": _eval_bollinger_middle,
    "BB_lower": _eval_bollinger_lower,
    "BB_width": _eval_bollinger_width,
    "BB_pctB": _eval_bollinger_position,
    "score": _eval_equity_score,
    "orb_break": _eval_orb_break,
    "orb_range": _eval_orb_range,
    "regime": _eval_orb_regime,
    "MACD_noise": _eval_v25,
    "v25": _eval_v25,
    "e14": _eval_e14,
    "e10_e11": _eval_e10_e11,
    "e7": _eval_e7,
    "chase_skip": _eval_chase_skip,
    "trade_profile": _eval_trade_profile,
    "entry_part": _eval_entry_part,
    "call_ss": _eval_call_ss,
    "put_ss": _eval_put_ss,
    "path_ss": _eval_path_ss,
    "tl_break": lambda ctx: _eval_geom_precomputed(
        ctx, "tl_break", ctx.geom_ready, ctx.geom_reason or "tl_break"
    ),
    "tl_ft": lambda ctx: _eval_geom_precomputed(
        ctx,
        "tl_ft",
        ctx.followthrough_ok,
        ctx.followthrough_reason or "tl_followthrough",
    ),
    "bo_confirm": lambda ctx: _eval_geom_precomputed(
        ctx, "bo_confirm", ctx.geom_ready, ctx.geom_reason or "bo_confirm"
    ),
    "PS": _eval_ps_floor,
    "top_n": _eval_top_n,
    "red_day": _eval_red_day,
    "delta": _eval_contract_delta,
    "liq": _eval_contract_liq,
    "spread": _eval_contract_spread,
    "OTM": _eval_otm,
    "cheap_lotto": _eval_cheap_lotto,
}


def run_stack(
    path: str,
    ctx: GateContext,
    *,
    gate_ids: Optional[Sequence[str]] = None,
    stop_on_reject: bool = True,
    include_contract: bool = False,
) -> GateReport:
    """Evaluate ordered stack for path. Skips contract gates unless ctx present."""
    path = path if path in STACKS else PATH_ORB
    ctx.path = path
    ids = list(gate_ids) if gate_ids is not None else list(active_stacks().get(path, []))
    report = GateReport(path=path, symbol=ctx.symbol, side=ctx.side, admitted=True)

    for raw_gid in ids:
        gid = canonicalize_gate_id(raw_gid)
        mode = resolve_gate_mode(path, gid)
        if mode == "off":
            report.results.append(_skip(gid, "off"))
            continue

        if gid in CONTRACT_GATES and not include_contract:
            has_contract = any(
                x is not None
                for x in (
                    ctx.delta,
                    ctx.contract_volume,
                    ctx.contract_oi,
                    ctx.premium,
                    ctx.spread_pct,
                    ctx.otm_pct,
                )
            )
            if not has_contract:
                report.results.append(_skip(gid, "contract_ctx_absent"))
                continue

        evaluator = GATE_EVALUATORS.get(gid)
        if evaluator is None:
            report.results.append(_skip(gid, "gate_evaluator_missing"))
            continue

        try:
            result = evaluator(ctx)
        except Exception as exc:  # pragma: no cover
            log.exception("GATE_STACK | gate=%s | evaluate_error=%s", gid, exc)
            result = _rej(gid, f"eval_error_{type(exc).__name__}")

        if mode == "observe" and (
            result.status == "reject"
            or (result.status == "defer" and gid in ("trade_profile", "dir_pct"))
        ):
            result = GateResult(
                gate_id=result.gate_id,
                layer=result.layer,
                status="observe",
                reason=f"observe:{result.reason}",
                fields=result.fields,
            )

        report.results.append(result)
        if result.status == "reject":
            report.admitted = False
            report.deferred = False
            if stop_on_reject:
                break
        elif result.status == "defer":
            report.admitted = False
            if stop_on_reject:
                break

    report.deferred = (not report.admitted) and not any(
        r.status == "reject" for r in report.results
    ) and any(r.status == "defer" for r in report.results)
    return report


def run_chain_skew_stack(path: str, ctx: GateContext) -> GateReport:
    """Pre-exec admit: Skip Signals, one contract pick, one stamp, one Trade Profile."""
    return single_tp_decide(path, ctx)


def _ctx_summary_bits(ctx: Optional[GateContext]) -> str:
    if ctx is None:
        return ""
    parts = []
    extras = ctx.extras or {}
    if extras.get("volume_ratio_missing"):
        parts.append("vol=missing")
    elif ctx.equity_volume_ratio is not None:
        parts.append(f"vol={ctx.equity_volume_ratio:.3f}")
    if ctx.vwap_distance_pct is not None:
        parts.append(f"vwap={ctx.vwap_distance_pct:.2f}")
    if ctx.put_call_oi_ratio is not None:
        parts.append(f"pc_oi={ctx.put_call_oi_ratio:.3f}")
    if ctx.direction_volume_pct is not None:
        parts.append(f"dir%={ctx.direction_volume_pct:.3f}")
    if ctx.macd_histogram is not None:
        parts.append(f"macd={ctx.macd_histogram:.2f}")
    if extras.get("rsi_missing"):
        parts.append("rsi=missing")
    elif ctx.rsi is not None:
        parts.append(f"rsi={ctx.rsi:.1f}")
    if ctx.quality_score is not None:
        parts.append(f"score={ctx.quality_score:.2f}")
    return (" | " + " ".join(parts)) if parts else ""


def shadow_log_report(
    report: GateReport,
    *,
    legacy_admitted: Optional[bool] = None,
    ctx: Optional[GateContext] = None,
) -> None:
    if not _env_bool("QUALITY_GATE_STACK_SHADOW", True):
        return
    parity = ""
    if legacy_admitted is not None:
        match = bool(legacy_admitted) == bool(report.admitted)
        parity = (
            f" | parity={'OK' if match else 'MISMATCH'} | legacy_admitted={legacy_admitted}"
        )
    fr = report.first_reject()
    log.info(
        "GATE_STACK_REPORT | path=%s | symbol=%s | side=%s | admitted=%s | "
        "first_reject=%s | n=%d%s%s | grep=GATE_STACK_REPORT",
        report.path,
        report.symbol,
        report.side,
        str(report.admitted).lower(),
        (fr.gate_id if fr else None),
        len(report.results),
        parity,
        _ctx_summary_bits(ctx),
    )


def admit_pre_exec(
    path: str,
    row_or_ctx: Any,
    *,
    quality_score: Optional[float] = None,
    quality_score_min: Optional[float] = None,
    include_contract: bool = False,
    include_rank: bool = False,
    stop_on_reject: bool = False,
    stamp_target: Optional[Dict[str, Any]] = None,
    legacy_admitted: Optional[bool] = None,
) -> GateReport:
    """
    Singular pre-exec admit — runs STACKS[path].

    Contract gates run when ``include_contract`` is True **or** contract fields
    are present on the context (δ / OTM / premium / spread / vol / OI).
    Rank gates skipped by default — SO ``priority_score`` must not veto before
    0DTE re-rank / Gate 3c (``include_rank`` when rank ctx is armed).

    ``row_or_ctx`` may be a GateContext or a signal/dict row.
    """
    if isinstance(row_or_ctx, GateContext):
        ctx = row_or_ctx
        ctx.path = path if path in STACKS else ctx.path
    else:
        ctx = context_from_signal_row(path, dict(row_or_ctx or {}))
    if quality_score is not None:
        ctx.quality_score = float(quality_score)
    if quality_score_min is not None:
        ctx.quality_score_min = float(quality_score_min)
    path_key = path if path in STACKS else PATH_ORB
    gate_ids = list(active_stacks().get(path_key, []))
    if not include_rank:
        gate_ids = [g for g in gate_ids if g not in RANK_GATES]
    has_contract = any(
        x is not None
        for x in (
            ctx.delta,
            ctx.contract_volume,
            ctx.contract_oi,
            ctx.premium,
            ctx.spread_pct,
            ctx.otm_pct,
        )
    )
    # Contract feasibility is the shared picker's job (once, at chain time).
    include_contract, has_contract = False, False
    report = run_stack(
        path,
        ctx,
        gate_ids=gate_ids,
        stop_on_reject=stop_on_reject,
        include_contract=bool(include_contract or has_contract),
    )
    shadow_log_report(report, legacy_admitted=legacy_admitted, ctx=ctx)
    if stamp_target is not None:
        stamp_target["quality_gate_report"] = report.to_dict()
    elif isinstance(row_or_ctx, dict):
        row_or_ctx["quality_gate_report"] = report.to_dict()
    return report


def append_research_stamp_and_contract_gates(
    path: str,
    row: Dict[str, Any],
    *,
    prior_report: Optional[Any] = None,
    stamp_target: Optional[Dict[str, Any]] = None,
) -> GateReport:
    """
    Evaluate observe research stamps + Layer D contract gates and merge into
    ``quality_gate_report`` for PO Entry/Exit desk review.

    Used after TL/BO Layer C confirm (which only runs pc_oi + dir%) so stamp
    gate IDs and contract rows land on the same report as equity/geom gates.
    """
    ctx = context_from_signal_row(path, dict(row or {}))
    gate_ids = list(STAMP_OBSERVE_GATES) + sorted(CONTRACT_GATES)
    has_contract = any(
        x is not None
        for x in (
            ctx.delta,
            ctx.contract_volume,
            ctx.contract_oi,
            ctx.premium,
            ctx.spread_pct,
            ctx.otm_pct,
        )
    )
    stamp_report = run_stack(
        path,
        ctx,
        gate_ids=gate_ids,
        stop_on_reject=False,
        include_contract=has_contract,
    )
    prior = prior_report
    if prior is None and stamp_target is not None:
        prior = stamp_target.get("quality_gate_report")
    if prior is None:
        prior = row.get("quality_gate_report") if isinstance(row, dict) else None
    prior_parsed = report_from_dict(prior) if isinstance(prior, dict) else prior
    if isinstance(prior_parsed, GateReport):
        merged = merge_gate_reports(prior_parsed, stamp_report)
    else:
        merged = stamp_report
    out = merged.to_dict()
    if stamp_target is not None:
        stamp_target["quality_gate_report"] = out
    if isinstance(row, dict):
        row["quality_gate_report"] = out
    return merged


def gate_report_to_reason_lists(
    report: GateReport,
) -> Tuple[List[str], List[str]]:
    """Map GateReport → (eligibility_reasons, rejection_reasons) for ORB shim."""
    eligibility: List[str] = []
    rejection: List[str] = []
    for r in report.results:
        if r.status == "pass":
            eligibility.append(r.reason)
        elif r.status == "reject":
            rejection.append(f"{r.gate_id}:{r.reason}")
        elif r.status == "observe":
            eligibility.append(f"observe:{r.gate_id}:{r.reason}")
    return eligibility, rejection


# 5-minute chain deltas stamped by central_tech.chain_ring. R6: measured once,
# on the chain the gate just fetched.
CHAIN_TRAJECTORY_KEYS: Tuple[str, ...] = (
    "call_chain_volume_delta_5m",
    "put_chain_volume_delta_5m",
    "same_side_chain_volume_delta_5m",
    "opposite_side_chain_volume_delta_5m",
    "dir_pct_change_5m",
    "same_side_chain_volume",
    "same_side_chain_volume_5m_ago",
    "direction_volume_pct_5m_ago",
)
OPTION_L1_KEYS: Tuple[str, ...] = (
    "option_bid_size",
    "option_ask_size",
    "option_l1_imbalance",
)


def clear_chain_trajectory_stamps(row: Dict[str, Any], *, signal: Any = None) -> int:
    """Drop stale 5m chain deltas from the admit row and every nested carry.

    ``stamp_admit_tech`` fills only missing keys and ``promote_qg_admit_fields``
    re-lifts nested ``qg_admit`` / metadata values, so a delta stamped before
    the chain fetch (often an exact 0.0) otherwise survives the restamp.
    """
    if not isinstance(row, dict):
        return 0
    containers: List[Dict[str, Any]] = [row]
    md = row.get("metadata") if isinstance(row.get("metadata"), dict) else None
    setup = row.get("setup_payload") if isinstance(row.get("setup_payload"), dict) else None
    sig_md = getattr(signal, "metadata", None) if signal is not None else None
    for c in (md, setup, sig_md):
        if isinstance(c, dict):
            containers.append(c)
            nested_setup = c.get("setup_payload")
            if isinstance(nested_setup, dict):
                containers.append(nested_setup)
    for c in list(containers):
        q = c.get("qg_admit")
        if isinstance(q, dict):
            containers.append(q)
    n = 0
    seen: set = set()
    for c in containers:
        if id(c) in seen:
            continue
        seen.add(id(c))
        for key in CHAIN_TRAJECTORY_KEYS:
            if c.get(key) is not None:
                n += 1
            if key in c:
                c.pop(key, None)
    return n


async def confirm_chain_skew_before_execution(
    path: str,
    system: Any,
    signal: Any,
    *,
    row: Dict[str, Any],
    log_tag: str,
    enabled: bool = True,
) -> Tuple[bool, str, Dict[str, Any]]:
    """
    One handoff: stamp chain volume and bar tech once, then the Trade Profile.

    Dir% share, equity volume ratio, break geometry, and contract delta / OTM /
    liquidity / spread do not score again here. The executor still last-looks
    the contract spread at order time.
    Path wrappers build ``row`` and stamp metadata on pass.
    """
    if not enabled:
        log.info(
            "%s | stage=skipped | symbol=%s | reason=gate_disabled",
            log_tag,
            getattr(signal, "symbol", row.get("symbol") or "?"),
        )
        return True, "dir_pct_handoff_disabled", {}

    # Oct 13 2026: the open-position cap is checked first (no API). A capped
    # signal never pulls a chain or scores dir%, and is labelled with its true
    # reason (no dir% / trade_profile cooldown).
    _cap_hit = None
    try:
        from modules.unified_0dte_portfolio_risk_manager import max_open_positions_precheck

        _cap_hit = max_open_positions_precheck(system, path)
    except Exception:
        _cap_hit = None
    if _cap_hit:
        return _max_open_positions_outcome(path, signal, row, _cap_hit, log_tag=log_tag)

    import time

    from easy0DTE.modules.convex_chain_skew import (
        enrich_signal_with_chain_skew,
        resolve_options_chain_manager,
    )

    def _persist_qg_admit(
        snap: Dict[str, Any],
        *,
        overwrite_keys: Tuple[str, ...] = (),
    ) -> None:
        if not snap:
            return
        row["qg_admit"] = snap
        try:
            from modules.qg_admit_snapshot import apply_qg_admit_snapshot

            targets: List[Dict[str, Any]] = []
            meta = getattr(signal, "metadata", None)
            if isinstance(meta, dict):
                targets.append(meta)
            md = row.get("metadata") if isinstance(row.get("metadata"), dict) else None
            if isinstance(md, dict) and md is not meta:
                targets.append(md)
            setup = row.get("setup_payload") if isinstance(row.get("setup_payload"), dict) else None
            if isinstance(setup, dict):
                targets.append(setup)
            for target in targets:
                apply_qg_admit_snapshot(target, snap, overwrite=False)
                # Post-fetch measurements replace any earlier (pre-fetch /
                # discovery) value already copied onto metadata.
                for key in overwrite_keys:
                    if snap.get(key) is not None:
                        target[key] = snap[key]
                    elif key in target:
                        target[key] = None
                target["qg_admit"] = snap
        except Exception:
            pass

    sym = str(row.get("symbol") or "?")
    option_side = str(row.get("option_side") or row.get("side") or "?")
    chain_mgr = resolve_options_chain_manager(system)
    skew: Dict[str, Any] = {}
    chain_warmed = False
    fetch_started = time.perf_counter()
    # Oct 7 2026 decision freeze (A1-A8): every TP-gated key is re-read from
    # central_tech at decision time with a per-source age check. Values carried
    # from signal creation (rsi, pve_3m + inputs, dir%, l1, SPY) are cleared so
    # stamp_admit_tech (fill-only-if-missing) re-reads them; a source over its
    # cap gets ONE targeted, budgeted refresh for this symbol.
    _dfz = None
    _fz_on = False
    _fz_carry: Dict[str, Any] = {}
    _fz_carry_used: Dict[str, Any] = {}
    _fz_quote: Dict[str, Any] = {}
    _fz_chain_plan: Dict[str, Any] = {}
    _fz_force_live = False
    _fz_budget_denied = False
    _fz_chain_mgr = chain_mgr
    _fz_slot = row.get("trendline_slot")
    if _fz_slot is None and isinstance(row.get("metadata"), dict):
        _fz_slot = row["metadata"].get("trendline_slot")
    try:
        from central_tech import decision_freeze as _dfz

        _fz_on = bool(_dfz.freeze_enabled()) and system is not None
    except Exception:
        _dfz = None
        _fz_on = False
    if _fz_on:
        _fz_side = "PUT" if str(row.get("side") or option_side).upper() in ("PUT", "P", "SHORT", "BEAR", "BEARISH") else "CALL"
        try:
            _fz_quote = await _dfz.ensure_fresh_quote_sources(
                system,
                sym,
                sources=_dfz.cell_sources(path, _fz_side, _fz_slot),
                log_tag=log_tag,
            )
        except Exception as fz_exc:
            log.debug("%s | stage=tp_freeze_quote | symbol=%s | err=%s", log_tag, sym, fz_exc)
        _fz_carry = _dfz.clear_keys(
            row,
            _dfz.SESSION_REFRESH_KEYS + _dfz.QUOTE_REFRESH_KEYS + _dfz.SPY_REFRESH_KEYS,
        )
    try:
        from modules.session_tech_admit import stamp_admit_tech as stamp_qg_admit_fields
        from modules.qg_admit_snapshot import collect_qg_admit_snapshot

        # R6: no 5m chain deltas before the chain fetch (see stamp_admit_tech).
        stamp_qg_admit_fields(system, row, symbol=sym, chain_trajectory=False)
        # 9.21 Path SS needs local bar tech (sva/rsi/bb/vwap/atr/macd/PVE/volume windows).
        # Prefer cached 1m bars already on the hot path; only hydrate equity
        # session tech when those stamps are still missing (one symbol, no chain).
        if _qg_path_ss_needs_local_hydrate(row) and system is not None:
            cache = getattr(system, "_session_tech_bar_cache", None)
            bars = []
            if isinstance(cache, dict):
                bars = cache.get(str(sym).upper()) or []
            if bars:
                stamp_qg_admit_fields(system, row, symbol=sym, chain_trajectory=False)
            if _qg_path_ss_needs_local_hydrate(row):
                try:
                    from modules.volume_data import hydrate_equity_session_tech

                    await hydrate_equity_session_tech(system, [sym])
                    stamp_qg_admit_fields(
                        system, row, symbol=sym, chain_trajectory=False
                    )
                    missing_after = [
                        k
                        for k in _PATH_SS_LOCAL_HYDRATE_KEYS
                        if row.get(k) is None
                    ]
                    log.info(
                        "%s | stage=qg_admit_rebuild | symbol=%s | "
                        "sva=%s | still_missing=%s | grep=QG_ADMIT_REBUILD",
                        log_tag,
                        sym,
                        row.get("session_vs_avg_volume"),
                        ",".join(missing_after) if missing_after else "-",
                    )
                except Exception as rebuild_exc:
                    log.debug(
                        "%s | stage=qg_admit_rebuild | symbol=%s | skipped=%s",
                        log_tag,
                        sym,
                        rebuild_exc,
                    )
        snap = collect_qg_admit_snapshot(row)
        _persist_qg_admit(snap)
    except Exception as hydrate_exc:
        log.debug(
            "%s | stage=qg_admit_hydrate | symbol=%s | skipped=%s",
            log_tag,
            sym,
            hydrate_exc,
        )
    allow_stale = 0.0
    skip_chain_reason = ""
    will_live = False
    # Oct 13 2026: shared, prioritized chain budget (central_tech.chain_fetch_manager).
    _cfm = None
    _cfm_granted = False
    try:
        from central_tech.chain_fetch_manager import get_manager as _cfm_get

        _cfm = _cfm_get(system)
    except Exception:
        _cfm = None
    _cfm_spot = None
    for _k in ("current_price", "underlying_price", "price", "entry_price"):
        try:
            if row.get(_k) is not None and float(row.get(_k)) > 0:
                _cfm_spot = float(row.get(_k))
                break
        except (TypeError, ValueError):
            continue
    if _cfm is not None:
        _cfm.note_candidate(sym, spot=_cfm_spot, side=row.get("side") or option_side)
        _cfm.ensure_sampler()
    if chain_mgr is not None:
        will_live = True
        cache_age = None
        try:
            from modules.open_position_quote_orchestration import (
                consume_qg_live_chain_fetch,
                qg_chain_allow_stale_sec,
                qg_live_chain_fetch_allowed,
                yield_to_open_position_monitor,
            )

            # One event-loop turn, then the entry chain. A busy exit
            # monitor does not hold this break for a later scan.
            monitor_idle = await yield_to_open_position_monitor(system, max_wait_s=0)
            allow_stale = float(qg_chain_allow_stale_sec(system) or 0.0)
            try:
                cache_age = chain_mgr.cached_chain_age_seconds(sym)
            except Exception:
                cache_age = None
            ttl = float(getattr(chain_mgr, "cache_ttl", 300) or 300)
            if _fz_on:
                # A1: the decision chain must be <= TP_FREEZE_CHAIN_MAX_AGE_SEC
                # (60 s), not the 300/600 s reuse window.
                _fz_spot = None
                for _k in ("current_price", "underlying_price", "price", "entry_price"):
                    try:
                        if row.get(_k) is not None and float(row.get(_k)) > 0:
                            _fz_spot = float(row.get(_k))
                            break
                    except (TypeError, ValueError):
                        continue
                _fz_chain_plan = _dfz.plan_chain_refresh(
                    chain_mgr, sym, spot=_fz_spot, legacy_reuse_sec=max(ttl, allow_stale)
                )
                _fz_chain_plan["spot"] = _fz_spot
                _reuse = _fz_chain_plan.get("action") == "cache"
            else:
                _reuse = cache_age is not None and cache_age <= max(ttl, allow_stale)
            if _reuse:
                will_live = False
            elif will_live and _cfm is not None:
                # Manager on: one shared token bucket replaces the per-scan cap
                # (CHAIN_FETCH_SCAN_CEILING stays as a high per-scan ceiling).
                if qg_live_chain_fetch_allowed(system):
                    _ok_cfm, _why_cfm = _cfm.try_acquire(1, sym, reason="gate")
                else:
                    _ok_cfm, _why_cfm = False, "scan_ceiling"
                if not _ok_cfm:
                    # The break is in hand. A denied bucket used to queue the
                    # chain and leave Trade Profile for a later scan.
                    wait_s = 0.0
                    try:
                        wait_s = float(_cfm.seconds_until_token(1))
                    except Exception:
                        wait_s = 0.0
                    if 0.0 < wait_s <= 2.0:
                        import asyncio

                        await asyncio.sleep(wait_s)
                        _ok_cfm, _why_cfm = _cfm.try_acquire(1, sym, reason="gate_entry")
                if _ok_cfm:
                    _cfm_granted = True
                    consume_qg_live_chain_fetch(system)
                    if not monitor_idle:
                        log.info(
                            "%s | stage=chain_skew | action=fetch_after_yield | symbol=%s | "
                            "reason=monitor_busy | grep=QG_CHAIN_YIELD",
                            log_tag,
                            sym,
                        )
                else:
                    log.info(
                        "%s | stage=chain_skew | action=entry_chain_now | symbol=%s | "
                        "reason=chain_budget_%s | queued=%d | grep=QG_CHAIN_YIELD|CHAIN_FETCH_MGR",
                        log_tag,
                        sym,
                        _why_cfm,
                        len(_cfm),
                    )
            elif will_live and not qg_live_chain_fetch_allowed(system):
                # Scan ceiling is for background chains. This break is fetched now.
                log.info(
                    "%s | stage=chain_skew | action=entry_chain_now | symbol=%s | "
                    "reason=qg_live_budget | grep=QG_CHAIN_YIELD",
                    log_tag,
                    sym,
                )
                consume_qg_live_chain_fetch(system)
            elif will_live:
                # The yield already gave the exit monitor a turn. A missing
                # chain is still fetched. Busy is not a reason to skip it.
                if not monitor_idle:
                    log.info(
                        "%s | stage=chain_skew | action=fetch_after_yield | symbol=%s | "
                        "reason=monitor_busy | grep=QG_CHAIN_YIELD",
                        log_tag,
                        sym,
                    )
                consume_qg_live_chain_fetch(system)
        except Exception as yield_exc:
            log.debug(
                "%s | stage=chain_skew | action=yield_skip | symbol=%s | err=%s",
                log_tag,
                sym,
                yield_exc,
            )

    if (
        chain_mgr is not None
        and _fz_on
        and will_live
        and _fz_chain_plan.get("reason") == "tp_age_cap"
    ):
        # The chain exists and is older than the freshness cap. One live
        # refresh per discovery task. The rest of this window uses the cache
        # and still runs Trade Profile. A missing chain is not this branch.
        try:
            _age_cap_n = int(getattr(system, "_discovery_task_age_cap_chains", 0) or 0)
        except (TypeError, ValueError):
            _age_cap_n = 0
        if _age_cap_n >= 1:
            log.info(
                "%s | stage=tp_freeze_refresh | kind=chain | symbol=%s | "
                "action=entry_use_cache | reason=scan_chain_cap | chain_age_before=%s | "
                "cap=%s | grep=TP_FREEZE_REFRESH",
                log_tag,
                sym,
                _fz_chain_plan.get("age_sec"),
                _fz_chain_plan.get("cap_sec"),
            )
            will_live = False
            _fz_chain_plan = dict(_fz_chain_plan, action="cache", reason="scan_chain_cap")
            if _cfm_granted:
                try:
                    _cfm.refund()
                except Exception:
                    pass
                _cfm_granted = False
    if (
        chain_mgr is not None
        and _fz_on
        and will_live
        and _fz_chain_plan.get("reason") == "tp_age_cap"
    ):
        _fz_budget = _dfz.get_refresh_budget(system)
        async with _fz_budget.lock("chain", sym):
            _re = _dfz.plan_chain_refresh(
                chain_mgr,
                sym,
                spot=_fz_chain_plan.get("spot"),
                legacy_reuse_sec=max(
                    float(getattr(chain_mgr, "cache_ttl", 300) or 300), allow_stale
                ),
            )
            if _re.get("action") == "cache":
                # Another signal on this symbol refreshed it moments ago.
                _fz_budget.note_dedupe("chain")
                _fz_chain_plan = dict(_re, reason="deduped", action="cache")
                will_live = False
                if _cfm_granted:
                    _cfm.refund()
                    _cfm_granted = False
            elif _cfm_granted:
                # Charged once to the shared chain bucket (no second budget).
                _fz_chain_plan = dict(_fz_chain_plan, budget="chain_fetch_manager")
                _fz_force_live = True
                try:
                    setattr(
                        system,
                        "_discovery_task_age_cap_chains",
                        int(getattr(system, "_discovery_task_age_cap_chains", 0) or 0) + 1,
                    )
                except Exception:
                    pass
            else:
                _fz_chain_plan = _dfz.acquire_chain_refresh(
                    system, sym, _fz_chain_plan, log_tag=log_tag
                )
                if _fz_chain_plan.get("action") == "defer":
                    # One refresh was denied. Do not score the stale cache
                    # (empty keys would missing-pass). The entry waits.
                    log.info(
                        "%s | stage=tp_freeze_refresh | kind=chain | symbol=%s | "
                        "action=wait | reason=%s | grep=TP_FREEZE_REFRESH",
                        log_tag,
                        sym,
                        _fz_chain_plan.get("reason"),
                    )
                    will_live = False
                    _fz_budget_denied = True
                    _dfz.clear_keys(row, _dfz.CHAIN_REFRESH_KEYS)
                    for _k in ("dir_pct_change_5m_prior_age_sec", "chain_volume_delta_5m_prior_age_sec"):
                        row.pop(_k, None)
                else:
                    _fz_force_live = True
                    try:
                        setattr(
                            system,
                            "_discovery_task_age_cap_chains",
                            int(getattr(system, "_discovery_task_age_cap_chains", 0) or 0) + 1,
                        )
                    except Exception:
                        pass
            if chain_mgr is not None and not _fz_budget_denied:
                _dfz.clear_keys(row, _dfz.CHAIN_REFRESH_KEYS)
                for _k in ("dir_pct_change_5m_prior_age_sec", "chain_volume_delta_5m_prior_age_sec"):
                    row.pop(_k, None)
                skew = await enrich_signal_with_chain_skew(
                    chain_mgr,
                    row,
                    system=system,
                    allow_stale_cache_sec=None,
                    force_live=_fz_force_live,
                ) or {}
                log.info(
                    "%s | stage=tp_freeze_refresh | kind=chain | symbol=%s | action=%s | "
                    "chain_age_before=%s | cap=%s | grep=TP_FREEZE_REFRESH",
                    log_tag,
                    sym,
                    "live" if _fz_force_live else _fz_chain_plan.get("reason"),
                    _fz_chain_plan.get("age_sec"),
                    _fz_chain_plan.get("cap_sec"),
                )
    elif chain_mgr is not None:
        if _fz_on:
            # A3: chain-derived keys are recomputed from this chain, not kept
            # from signal creation when already present.
            _dfz.clear_keys(row, _dfz.CHAIN_REFRESH_KEYS)
            for _k in ("dir_pct_change_5m_prior_age_sec", "chain_volume_delta_5m_prior_age_sec"):
                row.pop(_k, None)
        _elif_live = bool(_fz_on and _fz_chain_plan.get("action") == "live")
        if _elif_live:
            _fz_force_live = True
        skew = await enrich_signal_with_chain_skew(
            chain_mgr,
            row,
            system=system,
            allow_stale_cache_sec=(
                None if (_fz_on or _elif_live) else (allow_stale if allow_stale > 0 else None)
            ),
            force_live=_elif_live,
        ) or {}
    if chain_mgr is not None:
        chain_warmed = row.get("put_call_oi_ratio") is not None
        fetch_ms = (time.perf_counter() - fetch_started) * 1000.0
        log_fn = log.debug if fetch_ms < 50.0 else log.info
        log_fn(
            "%s | stage=chain_skew_enriched | symbol=%s | option_side=%s | "
            "chain_fetch=single | warmed=%s | elapsed_ms=%.1f | put_call_oi_ratio=%s | "
            "call_chain_volume=%s | put_chain_volume=%s | representative_iv=%s",
            log_tag,
            sym,
            option_side,
            str(chain_warmed).lower(),
            fetch_ms,
            row.get("put_call_oi_ratio"),
            row.get("call_chain_volume"),
            row.get("put_chain_volume"),
            row.get("representative_iv") or row.get("implied_volatility"),
        )
    else:
        if _fz_on and _fz_chain_mgr is not None:
            # No decision-time chain: do not score chain keys carried from
            # signal creation. vol missing + skipped_* holds the break.
            _dfz.clear_keys(row, _dfz.CHAIN_REFRESH_KEYS)
        if _cfm is not None and _fz_chain_mgr is not None:
            # Never stamp an over-age / carried chain as current: dir% and the
            # chain keys are None with a reason code until the queued fetch lands.
            for _k in _CFM_CHAIN_STAMP_KEYS:
                row.pop(_k, None)
            row["chain_unavailable_reason"] = skip_chain_reason or "chain_unavailable"
        log.info(
            "%s | stage=chain_skew | symbol=%s | action=skip_fetch | reason=%s",
            log_tag,
            sym,
            skip_chain_reason or "no_chain_manager",
        )

    chain_fetched = bool(skew) and (
        row.get("call_chain_volume") is not None or row.get("put_chain_volume") is not None
    )
    overwrite_keys: Tuple[str, ...] = ()
    if _cfm is not None and chain_mgr is None and _fz_chain_mgr is not None:
        # Clear carried chain stamps on metadata / setup too (time-correct parity).
        overwrite_keys = _CFM_CHAIN_STAMP_KEYS
    if chain_fetched:
        # R6: deltas already on the row came from before this fetch (discovery
        # carry, an earlier attempt, a stale seed). Clear them everywhere they
        # can be re-promoted from, then restamp on the fetched chain.
        cleared = clear_chain_trajectory_stamps(row, signal=signal)
        overwrite_keys = CHAIN_TRAJECTORY_KEYS + OPTION_L1_KEYS
        if cleared:
            log.debug(
                "%s | stage=chain_trajectory_restamp | symbol=%s | cleared=%d",
                log_tag,
                sym,
                cleared,
            )
    try:
        from modules.session_tech_admit import stamp_admit_tech as stamp_qg_admit_fields
        from modules.qg_admit_snapshot import collect_qg_admit_snapshot

        stamp_qg_admit_fields(system, row, symbol=sym)
        if _fz_on and _fz_carry:
            # A8: a key central_tech could not re-read keeps its carried value
            # only when the carry is young enough (break age <= session cap).
            _carry_age = None
            try:
                _carry_age = float(row.get("seconds_since_break"))
            except (TypeError, ValueError):
                _carry_age = None
            _cap = _dfz.session_tech_max_age_sec()
            _dropped = []
            for _k in _dfz.CARRY_RESTORABLE_KEYS:
                if row.get(_k) is None and _fz_carry.get(_k) is not None:
                    if _carry_age is not None and _carry_age <= _cap:
                        row[_k] = _fz_carry[_k]
                        _fz_carry_used[_k] = _carry_age
                    else:
                        _dropped.append(_k)
            if _dropped:
                log.info(
                    "%s | stage=tp_freeze_carry | symbol=%s | action=drop_stale_carry | "
                    "carry_age=%s | cap=%s | keys=%s | grep=TP_FREEZE_CARRY",
                    log_tag,
                    sym,
                    _carry_age,
                    _cap,
                    ",".join(_dropped),
                )
        _persist_qg_admit(collect_qg_admit_snapshot(row), overwrite_keys=overwrite_keys)
    except Exception as restamp_exc:
        log.debug(
            "%s | stage=qg_admit_restamp | symbol=%s | skipped=%s",
            log_tag,
            sym,
            restamp_exc,
        )

    # decision_hydrated: this attempt finished its hydration sequence.
    # Individual keys are still VALID / NOT_READY / DATA_ERROR after this.
    row["decision_hydrated"] = True
    side_vol_present = (
        row.get("call_chain_volume") is not None or row.get("put_chain_volume") is not None
    )
    if _fz_budget_denied:
        row["chain_attempt"] = "skipped_tp_freeze_budget"
    elif chain_mgr is None and skip_chain_reason:
        # Intentional defer: monitor busy or live-request budget. Not a failed fetch.
        row["chain_attempt"] = f"skipped_{skip_chain_reason}"
    elif _fz_on and chain_mgr is not None and not side_vol_present:
        # Carried chain keys were cleared; a failed fetch must hold the break,
        # not score (missing passes) on an empty chain.
        row["chain_attempt"] = "skipped_chain_unavailable"
    elif will_live:
        # A live fetch ran. Empty volumes are a failed measurement, not a defer.
        row["chain_attempt"] = "fetched" if side_vol_present else "empty"
    elif chain_mgr is not None:
        row["chain_attempt"] = "cached" if side_vol_present else "empty"
    else:
        row["chain_attempt"] = "empty"
    _stamp_direction_volume_pct_row(row)
    _log_qg_ss_field_gap(log_tag, row)
    if _fz_on:
        try:
            row["tp_freeze_ctx"] = _dfz.collect_freeze_context(
                system,
                row,
                symbol=sym,
                chain_mgr=_fz_chain_mgr,
                chain_plan=dict(_fz_chain_plan, action=("live" if _fz_force_live else _fz_chain_plan.get("action"))) if _fz_chain_plan else {"action": "defer" if skip_chain_reason else "NA", "reason": skip_chain_reason or "NA"},
                quote_refresh=_fz_quote,
                carry=_fz_carry_used,
            )
        except Exception as fz_exc:
            log.debug("%s | stage=tp_freeze_ctx | symbol=%s | err=%s", log_tag, sym, fz_exc)
    stack_ok, stack_msg, stack_report = evaluate_chain_skew_from_row(
        path, row, symbol=sym, side=str(row.get("side") or "")
    )
    row.pop("tp_freeze_ctx", None)
    if _cfm is not None:
        stack_msg = _cfm_bound_dir_pct_defer(
            _cfm, path, sym, row, stack_ok, stack_msg, stack_report, log_tag=log_tag
        )
    decision = row.get("po_profile_decision") if isinstance(row, dict) else None
    if isinstance(decision, dict):
        meta = getattr(signal, "metadata", None)
        if isinstance(meta, dict):
            meta["po_profile_decision"] = decision
        if isinstance(row.get("metadata"), dict):
            row["metadata"]["po_profile_decision"] = decision
    shadow_log_report(stack_report, legacy_admitted=stack_ok)

    skew_keys = [
        "put_call_oi_ratio",
        "put_call_volume_ratio",
        "call_chain_open_interest",
        "put_chain_open_interest",
        "call_chain_volume",
        "put_chain_volume",
        "representative_iv",
        "implied_volatility",
        "iv",
        "session_vs_avg_volume",
        "seconds_since_break",
        "l1_delta_5s_with",
        "volume_vs_prior_1m",
        "vwap_distance_pct",
        "vwap_with",
        "volume_ratio",
        "rsi",
        "bollinger_position",
        "direction_volume_pct",
        "break_distance_atr",
        "price_volume_efficiency_1m",
        "price_volume_efficiency_5m",
        "entry_bar_body_pct",
        "entry_bar_upper_wick_pct",
        "entry_bar_lower_wick_pct",
        "entry_bar_close_position",
        "wick_against",
        "close_with",
        "otm_pct",
        "delta",
        "chosen_delta",
        "representative_delta",
        # Near-spot Moon inputs. Not representative_delta.
        "representative_call_delta",
        "representative_put_delta",
        "representative_call_gamma",
        "representative_put_gamma",
        "gamma",
        "representative_strike",
        *list(_PATH_SS_FEATURE_KEYS),
    ]
    skew_out = {k: row.get(k) for k in dict.fromkeys(skew_keys) if row.get(k) is not None}
    if isinstance(row.get("qg_admit"), dict) and row["qg_admit"]:
        skew_out["qg_admit"] = row["qg_admit"]
    if skew and not skew_out:
        skew_out = {k: v for k, v in skew.items() if v is not None}
    skew_out["pc_oi_chain_warmed"] = bool(chain_warmed)
    # A6: the handoff snapshot reads back this exact freeze (metadata carry).
    if isinstance(row.get("po_decision_freeze"), dict):
        skew_out["po_decision_freeze"] = row["po_decision_freeze"]
        for _k in ("total_chain_vol_oi", "call_chain_vol_oi", "put_chain_vol_oi"):
            if row.get(_k) is not None:
                skew_out[_k] = row.get(_k)

    prior = None
    meta = getattr(signal, "metadata", None)
    if isinstance(meta, dict):
        prior = meta.get("quality_gate_report")
    stamp_merged_quality_gate_report(skew_out, stack_report, prior=prior)

    # Append observe research stamps + contract gates so TL/BO PO snaps carry
    # the full QG matrix (not only equity/geom + Layer C).
    try:
        append_research_stamp_and_contract_gates(
            path,
            row,
            prior_report=skew_out.get("quality_gate_report"),
            stamp_target=skew_out,
        )
        if isinstance(meta, dict) and isinstance(skew_out.get("quality_gate_report"), dict):
            meta["quality_gate_report"] = skew_out["quality_gate_report"]
        if isinstance(row, dict) and isinstance(skew_out.get("quality_gate_report"), dict):
            row["quality_gate_report"] = skew_out["quality_gate_report"]
    except Exception as stamp_exc:
        log.debug(
            "%s | research_stamp_merge_skipped | symbol=%s | err=%s",
            log_tag,
            sym,
            stamp_exc,
        )

    if stack_ok:
        log.info(
            "%s_PASS | symbol=%s | option_side=%s | put_call_oi_ratio=%s | %s",
            log_tag,
            sym,
            option_side,
            row.get("put_call_oi_ratio"),
            stack_msg,
        )
        return True, stack_msg, skew_out

    log.warning(
        "%s_REJECT | symbol=%s | option_side=%s | put_call_oi_ratio=%s | reason=%s | "
        "grep=%s_REJECT|pc_oi_filter_reject|dir_vol_reject|GATE_STACK_REPORT",
        log_tag,
        sym,
        option_side,
        row.get("put_call_oi_ratio"),
        stack_msg,
        log_tag,
    )
    return False, stack_msg, skew_out


MAX_OPEN_POSITIONS_REASON = "max_open_positions_reached"


def _max_open_positions_outcome(
    path: str,
    signal: Any,
    row: Dict[str, Any],
    info: Dict[str, Any],
    *,
    log_tag: str,
) -> Tuple[bool, str, Dict[str, Any]]:
    """Gate report + reject log for a capped signal (no chain, no dir%, no cooldown)."""
    sym = str(row.get("symbol") or getattr(signal, "symbol", "") or "?")
    side = str(row.get("side") or row.get("option_side") or "")
    fields = {
        "open_n": info.get("open_n"),
        "cap": info.get("cap"),
        "cap_kind": info.get("cap_kind"),
    }
    report = GateReport(path=path, symbol=sym, side=side.upper())
    report.results.append(
        GateResult(
            gate_id="max_open_positions",
            layer="0",
            status="reject",
            reason=MAX_OPEN_POSITIONS_REASON,
            fields=fields,
        )
    )
    report.admitted = False
    report.deferred = False
    skew_out: Dict[str, Any] = {"max_open_positions": dict(info)}
    try:
        stamp_merged_quality_gate_report(skew_out, report, prior=None)
    except Exception:
        pass
    meta = getattr(signal, "metadata", None)
    if isinstance(meta, dict):
        meta["max_open_positions"] = dict(info)
        if isinstance(skew_out.get("quality_gate_report"), dict):
            meta["quality_gate_report"] = skew_out["quality_gate_report"]
    row["max_open_positions"] = dict(info)
    log.info(
        "%s_REJECT | symbol=%s | option_side=%s | reason=%s | cap_kind=%s | open_n=%s | cap=%s | "
        "chain_fetch=none | dir_pct=not_evaluated | cooldown=none | "
        "grep=%s_REJECT|MAX_OPEN_POSITIONS_REACHED|GATE_STACK_REPORT",
        log_tag, sym, side.lower(), MAX_OPEN_POSITIONS_REASON, info.get("cap_kind"),
        info.get("open_n"), info.get("cap"), log_tag,
    )
    log.info(
        "GATE_STACK_REPORT | path=%s | symbol=%s | side=%s | admitted=false | "
        "first_reject=max_open_positions | reason=%s | open_n=%s | cap=%s | grep=GATE_STACK_REPORT",
        path, sym, side.upper(), MAX_OPEN_POSITIONS_REASON, info.get("open_n"), info.get("cap"),
    )
    return False, MAX_OPEN_POSITIONS_REASON, skew_out


_CFM_CHAIN_STAMP_KEYS: Tuple[str, ...] = (
    "call_chain_volume",
    "put_chain_volume",
    "call_volume",
    "put_volume",
    "total_chain_volume",
    "same_side_chain_volume",
    "direction_volume_pct",
    "call_chain_open_interest",
    "put_chain_open_interest",
    "total_chain_open_interest",
    "put_call_volume_ratio",
    "put_call_oi_ratio",
)

DIR_PCT_DATA_UNAVAILABLE = "dir_pct_data_unavailable"


def _cfm_bound_dir_pct_defer(
    cfm: Any,
    path: str,
    sym: str,
    row: Dict[str, Any],
    stack_ok: bool,
    stack_msg: str,
    stack_report: Any,
    *,
    log_tag: str,
) -> str:
    """Bound a dir%% data defer: after DIR_PCT_DEFER_MAX_WAIT_SEC the outcome is
    ``dir_pct_data_unavailable`` (reason-coded, still no cooldown, signal kept)."""
    side = row.get("side") or row.get("option_side")
    dir_defer = False
    try:
        dir_defer = (not stack_ok) and bool(getattr(stack_report, "deferred", False)) and any(
            getattr(r, "gate_id", "") == "dir_pct" and getattr(r, "status", "") == "defer"
            for r in (getattr(stack_report, "results", None) or [])
        )
    except Exception:
        dir_defer = False
    if not dir_defer:
        cfm.clear_defer(path, sym, side)
        return stack_msg
    try:
        from central_tech.chain_fetch_manager import dir_pct_defer_max_wait_sec

        max_wait = float(dir_pct_defer_max_wait_sec())
    except Exception:
        max_wait = 90.0
    waited = cfm.note_defer(path, sym, side, restart_after_sec=max_wait)
    # Make sure the chain this signal waits on is queued at gate priority.
    try:
        cfm.enqueue(sym, priority=1, side=side, reason="dir_pct_defer")
    except Exception:
        pass
    row["dir_pct_defer_waited_sec"] = round(waited, 1)
    if waited >= max_wait > 0:
        log.info(
            "%s | stage=dir_pct_defer | symbol=%s | side=%s | action=data_unavailable | "
            "waited_sec=%.1f | max_wait_sec=%.0f | chain_attempt=%s | cooldown=none | "
            "grep=DIR_PCT_DATA_UNAVAILABLE",
            log_tag, sym, side, waited, max_wait, row.get("chain_attempt"),
        )
        # The clock is from the first miss. Clear it so a later scan with a
        # new chain gets its own 90s, instead of staying unavailable all day.
        cfm.clear_defer(path, sym, side)
        return DIR_PCT_DATA_UNAVAILABLE
    log.info(
        "%s | stage=dir_pct_defer | symbol=%s | side=%s | action=hold_reevaluate | "
        "waited_sec=%.1f | max_wait_sec=%.0f | chain_attempt=%s | cooldown=none | grep=DIR_PCT_DEFER",
        log_tag, sym, side, waited, max_wait, row.get("chain_attempt"),
    )
    return stack_msg


async def confirm_dir_pct_before_execution(
    path: str,
    system: Any,
    signal: Any,
    *,
    row: Dict[str, Any],
    log_tag: str = "LAYER_C_DIR_PCT",
    enabled: bool = True,
) -> Tuple[bool, str, Dict[str, Any]]:
    """Canonical Layer C name: hydrate chain vols → QG ``dir_pct`` hard."""
    return await confirm_chain_skew_before_execution(
        path,
        system,
        signal,
        row=row,
        log_tag=log_tag,
        enabled=enabled,
    )


def _stamp_direction_volume_pct_row(row: Dict[str, Any]) -> None:
    """Stamp same-side dir% and IV aliases after chain hydrate."""
    if not isinstance(row, dict):
        return
    if row.get("implied_volatility") is None:
        iv = row.get("iv")
        if iv is None:
            iv = row.get("representative_iv")
        if iv is not None:
            try:
                iv_f = float(iv)
                if iv_f > 5.0:
                    iv_f = iv_f / 100.0
                row["implied_volatility"] = iv_f
            except (TypeError, ValueError):
                pass
    if row.get("direction_volume_pct") is not None:
        return
    if row.get("call_chain_volume") is None and row.get("put_chain_volume") is None:
        return
    try:
        from easy0DTE.modules.convex_eligibility_filter import same_side_direction_volume_pct

        dp = same_side_direction_volume_pct(row)
        if dp is not None:
            row["direction_volume_pct"] = float(dp)
    except Exception:
        pass


# Local (non-chain) stamps LS100 Path SS knives lean on. Prefer cached 1m bars /
# L1 ring / strike math; only rebuild equity session tech when these are empty.
_PATH_SS_LOCAL_HYDRATE_KEYS: Tuple[str, ...] = (
    "session_vs_avg_volume",
    "rsi",
    "bollinger_position",
    "vwap_distance_pct",
    "break_distance_atr",
    "price_volume_efficiency_1m",
    "entry_bar_body_pct",
    "atr",
    "macd",
    "macd_signal",
    "session_bar_count",
    "volume_1m",
    "volume_3m",
    "volume_5m",
    "volume_pct_of_day_5m",
    "relative_volume_time_adjusted",
    "price_change_1m_pct",
    "entry_bar_range_pct",
    "distance_from_vwap_atr",
    "session_volume",
    "price_volume_efficiency_3m",
)


def _qg_path_ss_needs_local_hydrate(row: Dict[str, Any]) -> bool:
    """True when Path SS local bar tech is still incomplete after stamp."""
    if not isinstance(row, dict):
        return False
    for key in _PATH_SS_LOCAL_HYDRATE_KEYS:
        if key == "session_vs_avg_volume":
            if row.get("session_vs_avg_volume") is None and row.get("session_vs_avg") is None:
                return True
            continue
        if row.get(key) is None:
            return True
    return False


def _abs_ss_key_present(row: Dict[str, Any], key: str) -> bool:
    """True when a PRUNED-8/8 Absolute SS field (or alias) is stamped."""
    if not isinstance(row, dict):
        return False
    if key == "total_chain_volume":
        return row.get("total_chain_volume") is not None or (
            row.get("call_chain_volume") is not None and row.get("put_chain_volume") is not None
        )
    if key == "implied_volatility":
        return (
            row.get("implied_volatility") is not None
            or row.get("iv") is not None
            or row.get("representative_iv") is not None
        )
    if key == "representative_ask":
        return row.get("representative_ask") is not None or row.get("option_ask") is not None
    if key == "vwap_distance_pct":
        return row.get("vwap_distance_pct") is not None or row.get("abs_vwap_distance_pct") is not None
    if key == "abs_vwap_distance_pct":
        return row.get("abs_vwap_distance_pct") is not None or row.get("vwap_distance_pct") is not None
    if key == "rsi":
        return row.get("rsi") is not None or row.get("rsi_14") is not None
    return row.get(key) is not None


def _log_qg_ss_field_gap(log_tag: str, row: Dict[str, Any]) -> None:
    iv = row.get("implied_volatility")
    if iv is None:
        iv = row.get("iv") or row.get("representative_iv")
    # Prefer stamped dir%; else treat as present when volumes allow compute
    # (avoids false gaps when stamp ran but GateContext still reads volumes).
    dir_pct_present = row.get("direction_volume_pct") is not None
    if not dir_pct_present and (
        row.get("call_chain_volume") is not None
        and row.get("put_chain_volume") is not None
    ):
        try:
            from easy0DTE.modules.convex_eligibility_filter import same_side_direction_volume_pct

            dir_pct_present = same_side_direction_volume_pct(row) is not None
        except Exception:
            dir_pct_present = False
    missing = [
        k
        for k, present in (
            ("session_vs_avg_volume", row.get("session_vs_avg_volume") is not None),
            ("implied_volatility", iv is not None),
            ("seconds_since_break", row.get("seconds_since_break") is not None),
            ("direction_volume_pct", dir_pct_present),
        )
        if not present
    ]
    if not missing:
        path_ss_missing = [
            k
            for k, present in (
                ("break_distance_atr", row.get("break_distance_atr") is not None),
                (
                    "price_volume_efficiency_1m",
                    row.get("price_volume_efficiency_1m") is not None,
                ),
                ("entry_bar_body_pct", row.get("entry_bar_body_pct") is not None),
                ("otm_pct", row.get("otm_pct") is not None),
                (
                    "delta",
                    (
                        row.get("delta")
                        or row.get("chosen_delta")
                        or row.get("representative_delta")
                    )
                    is not None,
                ),
                ("rsi", row.get("rsi") is not None),
                ("bollinger_position", row.get("bollinger_position") is not None),
                ("vwap_distance_pct", row.get("vwap_distance_pct") is not None),
                (
                    "l1_delta_5s_with",
                    row.get("l1_delta_5s_with") is not None
                    or row.get("underlying_l1_imbalance_delta_5s") is not None,
                ),
                (
                    "volume_ratio",
                    row.get("volume_ratio") is not None
                    or row.get("equity_volume_ratio") is not None,
                ),
                ("atr", row.get("atr") is not None),
                ("macd_signal", row.get("macd_signal") is not None),
                ("session_bar_count", row.get("session_bar_count") is not None),
                (
                    "price_volume_efficiency_3m",
                    row.get("price_volume_efficiency_3m") is not None,
                ),
                ("session_volume", row.get("session_volume") is not None),
                (
                    "volume_pct_of_day_5m",
                    row.get("volume_pct_of_day_5m") is not None,
                ),
                (
                    "underlying_l1_imbalance_delta_15s",
                    row.get("underlying_l1_imbalance_delta_15s") is not None,
                ),
                (
                    "total_chain_volume",
                    row.get("total_chain_volume") is not None
                    or (
                        row.get("call_chain_volume") is not None
                        and row.get("put_chain_volume") is not None
                    ),
                ),
                ("volume_5m", row.get("volume_5m") is not None),
                (
                    "representative_ask",
                    row.get("representative_ask") is not None
                    or row.get("option_ask") is not None,
                ),
                (
                    "same_side_chain_volume_5m_ago",
                    row.get("same_side_chain_volume_5m_ago") is not None,
                ),
                ("spy_change_pct", row.get("spy_change_pct") is not None),
                ("break_distance_pct", row.get("break_distance_pct") is not None),
                ("cumulative_volume", row.get("cumulative_volume") is not None),
            )
            if not present
        ]
        if path_ss_missing:
            log.info(
                "%s | stage=qg_path_ss_field_gap | symbol=%s | missing=%s | "
                "grep=QG_PATH_SS_FIELD_GAP",
                log_tag,
                str(row.get("symbol") or "?"),
                ",".join(path_ss_missing),
            )
        abs_ss_missing = [k for k in _ABS_SS_HYDRATE_KEYS if not _abs_ss_key_present(row, k)]
        if abs_ss_missing:
            log.info(
                "%s | stage=qg_abs_ss_field_gap | symbol=%s | missing=%s | "
                "grep=QG_ABS_SS_FIELD_GAP",
                log_tag,
                str(row.get("symbol") or "?"),
                ",".join(abs_ss_missing),
            )
        return
    log.info(
        "%s | stage=qg_ss_field_gap | symbol=%s | missing=%s | grep=QG_SS_FIELD_GAP",
        log_tag,
        str(row.get("symbol") or "?"),
        ",".join(missing),
    )


def context_from_signal_row(
    path: str,
    row: Dict[str, Any],
    *,
    symbol: str = "",
    side: str = "",
) -> GateContext:
    try:
        from modules.qg_admit_snapshot import promote_qg_admit_fields

        promote_qg_admit_fields(row)
    except Exception:
        pass
    sym = symbol or str(row.get("symbol") or "").upper()
    sd = side or str(
        row.get("side")
        or row.get("option_side")
        or row.get("expected_option_side")
        or row.get("direction")
        or row.get("option_type")
        or row.get("option_type_label")
        or ""
    )
    sd_u = sd.upper()
    if sd_u in ("C", "LONG", "BULLISH", "CALL"):
        sd_u = "CALL"
    elif sd_u in ("P", "SHORT", "BEARISH", "PUT"):
        sd_u = "PUT"
    vol = row.get("volume_ratio")
    if vol is None:
        vol = row.get("orb_volume_ratio")
    try:
        vol_f = float(vol) if vol is not None else None
    except (TypeError, ValueError):
        vol_f = None
    try:
        vd = float(row["vwap_distance_pct"]) if row.get("vwap_distance_pct") is not None else None
    except (TypeError, ValueError):
        vd = None
    try:
        rsi = float(row["rsi"]) if row.get("rsi") is not None else None
    except (TypeError, ValueError):
        rsi = None

    def _opt_float(key: str) -> Optional[float]:
        if row.get(key) is None:
            return None
        try:
            return float(row[key])
        except (TypeError, ValueError):
            return None

    macd_h = _opt_float("macd_histogram")
    ema12 = _opt_float("ema_12")
    ema26 = _opt_float("ema_26")
    break_d = _opt_float("break_distance_pct")
    range_pct = _opt_float("orb_range_pct")
    if range_pct is None:
        range_pct = _opt_float("range_pct")
    cont = _opt_float("continuation_pct")
    chop = _opt_float("post_break_chop_pct")
    dir_vol = _opt_float("direction_volume_pct")
    ft_bars = None
    if row.get("follow_through_bars") is not None:
        try:
            ft_bars = int(row["follow_through_bars"])
        except (TypeError, ValueError):
            ft_bars = None

    protection = row.get("protection_day")
    if protection is None:
        protection = row.get("is_protection_day")
    if isinstance(protection, str):
        protection = protection.strip().lower() in ("1", "true", "yes", "on")
    elif protection is not None:
        protection = bool(protection)

    score = None
    for k in ("eligibility_score", "quality_score", "convex_score", "score"):
        if row.get(k) is not None:
            try:
                score = float(row[k])
                break
            except (TypeError, ValueError):
                pass
    ps = None
    if row.get("priority_score") is not None:
        try:
            ps = float(row["priority_score"])
        except (TypeError, ValueError):
            ps = None

    # Contract Layer D — pull from strike selection / representative_* aliases
    delta = _opt_float("delta")
    if delta is None:
        delta = _opt_float("representative_delta")
    if delta is None:
        delta = _opt_float("chosen_delta")
    if delta is None:
        delta = _opt_float("fill_delta")
    otm = _opt_float("otm_pct")
    prem = _opt_float("premium")
    if prem is None:
        prem = _opt_float("representative_price")
    if prem is None:
        prem = _opt_float("entry_premium")
    spread = _opt_float("spread_pct")
    if spread is None:
        spread = _opt_float("representative_spread_pct")
    if spread is None:
        spread = _opt_float("avg_bid_ask_spread_pct")
    cvol = _opt_float("contract_volume")
    if cvol is None:
        cvol = _opt_float("representative_volume")
    coi = _opt_float("contract_oi")
    if coi is None:
        coi = _opt_float("representative_open_interest")
    if coi is None:
        coi = _opt_float("open_interest")
    qty = None
    if row.get("contract_qty") is not None or row.get("quantity") is not None:
        try:
            qty = int(row.get("contract_qty") if row.get("contract_qty") is not None else row.get("quantity"))
        except (TypeError, ValueError):
            qty = None

    # Desk aliases for stamp gates (call_volume ↔ call_chain_volume)
    call_vol = row.get("call_chain_volume")
    if call_vol is None:
        call_vol = row.get("call_volume")
    put_vol = row.get("put_chain_volume")
    if put_vol is None:
        put_vol = row.get("put_volume")

    ctx = GateContext(
        path=path,
        symbol=sym,
        side=sd_u,
        signal=dict(row),
        equity_volume_ratio=vol_f,
        vwap_distance_pct=vd,
        vwap_position=str(row.get("vwap_position") or "") or None,
        rsi=rsi,
        macd_histogram=macd_h,
        ema_12=ema12,
        ema_26=ema26,
        quality_score=score,
        put_call_oi_ratio=row.get("put_call_oi_ratio"),
        call_chain_volume=call_vol,
        put_chain_volume=put_vol,
        direction_volume_pct=dir_vol,
        break_distance_pct=break_d,
        range_pct=range_pct,
        follow_through_bars=ft_bars,
        continuation_pct=cont,
        post_break_chop_pct=chop,
        delta=delta,
        contract_volume=cvol,
        contract_oi=coi,
        premium=prem,
        spread_pct=spread,
        otm_pct=otm,
        contract_qty=qty,
        priority_score=ps,
        protection_day=protection if isinstance(protection, bool) else None,
        geom_ready=row.get("_geom_ready") if isinstance(row.get("_geom_ready"), bool) else None,
        geom_reason=str(row.get("_geom_reason") or ""),
        followthrough_ok=(
            row.get("_followthrough_ok")
            if isinstance(row.get("_followthrough_ok"), bool)
            else None
        ),
        followthrough_reason=str(row.get("_followthrough_reason") or ""),
        extras={
            "volume_ratio_missing": bool(row.get("volume_ratio_missing")),
            "vwap_missing": bool(row.get("vwap_missing")),
            "rsi_missing": bool(row.get("rsi_missing")),
            "macd_missing": bool(
                row.get("macd_missing")
                if row.get("macd_missing") is not None
                else row.get("macd_histogram") is None
            ),
            "volume_missing_fail_closed": (
                bool(row.get("volume_missing_fail_closed"))
                if "volume_missing_fail_closed" in row
                else None
            ),
            "vwap_extension_reject": row.get("vwap_extension_reject"),
            "session_vs_avg_volume": row.get("session_vs_avg_volume")
            if row.get("session_vs_avg_volume") is not None
            else row.get("session_vs_avg"),
            "session_bar_count": row.get("session_bar_count"),
            "session_volume": row.get("session_volume"),
            "volume_1m": row.get("volume_1m"),
            "volume_5m": row.get("volume_5m"),
            "representative_ask": row.get("representative_ask")
            if row.get("representative_ask") is not None
            else row.get("option_ask"),
            "volume_ratio_source": row.get("volume_ratio_source"),
            "tech_source": row.get("tech_source"),
            "implied_volatility": row.get("implied_volatility")
            if row.get("implied_volatility") is not None
            else (
                row.get("iv")
                if row.get("iv") is not None
                else (
                    row.get("representative_iv")
                    if row.get("representative_iv") is not None
                    else row.get("chosen_iv")
                )
            ),
            "iv": row.get("iv")
            if row.get("iv") is not None
            else row.get("implied_volatility"),
            "trendline_slot": row.get("trendline_slot"),
            "seconds_since_break": row.get("seconds_since_break"),
            "l1_delta_5s_with": row.get("l1_delta_5s_with"),
            "underlying_l1_imbalance_delta_5s": row.get(
                "underlying_l1_imbalance_delta_5s"
            ),
            "vwap_with": row.get("vwap_with"),
            "bollinger_position": row.get("bollinger_position")
            if row.get("bollinger_position") is not None
            else row.get("bb_percent_b"),
            "break_distance_atr": row.get("break_distance_atr"),
            "price_volume_efficiency_1m": row.get("price_volume_efficiency_1m"),
            "price_volume_efficiency_5m": row.get("price_volume_efficiency_5m"),
            "entry_bar_body_pct": row.get("entry_bar_body_pct"),
            "entry_bar_upper_wick_pct": row.get("entry_bar_upper_wick_pct"),
            "entry_bar_lower_wick_pct": row.get("entry_bar_lower_wick_pct"),
            "entry_bar_close_position": row.get("entry_bar_close_position"),
            "wick_against": row.get("wick_against"),
            "close_with": row.get("close_with"),
            "otm_pct": row.get("otm_pct"),
            "chosen_delta": row.get("chosen_delta"),
            "representative_delta": row.get("representative_delta"),
            "representative_strike": row.get("representative_strike"),
        },
    )
    for key in _PATH_SS_FEATURE_KEYS:
        if ctx.extras.get(key) is None and row.get(key) is not None:
            ctx.extras[key] = row.get(key)
    return ctx


def capture_profile_decision(ctx: GateContext, report: GateReport) -> Dict[str, Any]:
    """The one scored-key record: freeze.values (post-pick stamp), plus digest.

    Do not re-read the row. Pre-pick representative otm/delta/L1 disagree with
    the contract the gate scored.
    """
    from datetime import datetime, timezone

    from easy0DTE.modules.trade_profile import (
        decision_value_keys,
        profile_name,
        resolve_profile_version,
    )

    side = _ss_side(ctx)
    slot = None
    if ctx.extras:
        slot = ctx.extras.get("trendline_slot")
    if slot is None and isinstance(ctx.signal, dict):
        slot = ctx.signal.get("trendline_slot")
    extras = ctx.extras if isinstance(ctx.extras, dict) else {}
    fz = extras.get("po_decision_freeze")
    digest = extras.get("po_decision_digest")
    keys: Dict[str, Any] = {}
    if isinstance(fz, dict) and isinstance(fz.get("values"), dict):
        keys = dict(fz.get("values") or {})
        digest = digest or fz.get("decision_digest")
    else:
        for key in decision_value_keys():
            keys[key] = _ctx_lookup_float(ctx, key)
    reason = ""
    if report.admitted:
        reason = "|".join(r.reason for r in report.results if r.status == "pass")
    else:
        first = report.first_reject()
        reason = first.reason if first else ""
    return {
        "profile": (fz.get("profile") if isinstance(fz, dict) else None) or profile_name(ctx.path, slot),
        "side": side,
        "admitted": bool(report.admitted),
        "reason": reason,
        "profile_version": (
            (fz.get("profile_version") if isinstance(fz, dict) else None)
            or resolve_profile_version()
        ),
        "scored_at": (fz.get("gate_at") if isinstance(fz, dict) else None)
        or datetime.now(timezone.utc).isoformat(),
        "keys": keys,
        "decision_digest": digest,
    }


def evaluate_chain_skew_from_row(
    path: str,
    row: Dict[str, Any],
    *,
    symbol: str = "",
    side: str = "",
) -> Tuple[bool, str, GateReport]:
    """Convenience for TL/BO/ORB cutover: (admitted, reason, report)."""
    ctx = context_from_signal_row(path, row, symbol=symbol, side=side)
    report = run_chain_skew_stack(path, ctx)
    if isinstance(row, dict):
        row["po_profile_decision"] = capture_profile_decision(ctx, report)
        # Oct 7 2026: the one decision freeze (ctx.signal is a copy of row).
        _fz = (ctx.extras or {}).get("po_decision_freeze")
        if isinstance(_fz, dict):
            row["po_decision_freeze"] = _fz
    if report.admitted:
        reasons = [r.reason for r in report.results if r.status == "pass"]
        return True, "|".join(reasons) or "chain_skew_pass", report
    if report.deferred:
        return False, "required_data_not_ready", report
    fr = report.first_reject()
    return False, (fr.reason if fr else "chain_skew_reject"), report


def path_policy_for(path: str) -> Dict[str, str]:
    return dict(PATH_POLICY.get(path, PATH_POLICY[PATH_ORB]))


def cutover_enabled() -> bool:
    """TL/BO Layer A/B cutover — default ON after Convex absorb (env can force false)."""
    return _env_bool("QUALITY_GATE_STACK_CUTOVER", True)


def shadow_equity_geom_report(
    path: str,
    ctx: GateContext,
    *,
    legacy_admitted: Optional[bool] = None,
    stamp_target: Optional[Dict[str, Any]] = None,
) -> GateReport:
    """
    Layer A/B for TL/BO. Always logs GATE_STACK_REPORT.
    When ``QUALITY_GATE_STACK_CUTOVER`` is on, callers should treat
    ``report.admitted`` as authoritative for quality admit.
    """
    geom = SINGLE_TP_EQUITY_GEOM_GATES
    gate_ids = geom.get(path) or geom[PATH_BO]
    report = run_stack(path, ctx, gate_ids=gate_ids, stop_on_reject=False)
    shadow_log_report(report, legacy_admitted=legacy_admitted, ctx=ctx)
    if stamp_target is not None:
        stamp_target["quality_gate_report"] = report.to_dict()
    return report


# Bare legacy equity / chase tokens retired from path scorers (list B / sole GATE_*).
# Cutover strips these so Telegram Reason stays gate_stack:{id}:{reason} only.
_LEGACY_EQUITY_REASON_PREFIXES = (
    "call_below_vwap",
    "put_above_vwap",
    "vwap_missing",
    "vwap_extension_reject",
    "vwap_at_reject",
    "volume_ratio_below_min",
    "volume_ratio_missing",
    "equity_volume_ratio_",
    "rsi_below_call_floor",
    "rsi_above_put_ceiling",
    "rsi_anti_chase_",
    "bb_anti_stretch_",
    "bb_mid_stretch_",
    "equity.rsi_anti_chase",
    "equity.vwap_align",
    "equity.volume_ratio",
    "equity.bb_anti_stretch",
    "equity.bb_mid_stretch",
    "equity.rsi_band",
    "equity.score_floor",
    "break_distance_chase",
    "entry_distance_extended",
    "reclaim_persisted",
    "opening_bar_range_chaos",
    "continuation_insufficient",
    "follow_through_insufficient",
    "material_break_score_below_min",
    "quality_score_below_min",
)


def _is_legacy_equity_reason(reason: str) -> bool:
    r = str(reason or "").strip().lower()
    if not r or r.startswith("gate_stack:"):
        return False
    return any(r == p or r.startswith(f"{p}:") or r.startswith(p) for p in _LEGACY_EQUITY_REASON_PREFIXES)


def apply_equity_geom_cutover(
    report: GateReport,
    *,
    legacy_passed: bool,
    failed_reasons: Optional[List[str]] = None,
) -> Tuple[bool, List[str]]:
    """
    Layer A/B cutover — when ``QUALITY_GATE_STACK_CUTOVER`` is on, GATE_* is sole
    equity admit (0DTEQualityGates). Legacy scorer may only keep geometry / confirm
    reasons; bare VWAP/vol/RSI/chase tokens are stripped as duplicates.

    When cutover off, returns legacy unchanged.
    """
    reasons = list(failed_reasons or [])
    if not cutover_enabled():
        return bool(legacy_passed), reasons

    # Drop retired list-B / duplicate equity tokens so they cannot AND-block admit.
    geom_reasons = [r for r in reasons if not _is_legacy_equity_reason(r)]

    if not report.admitted:
        fr = report.first_reject()
        if fr is not None:
            tag = f"gate_stack:{fr.gate_id}:{fr.reason}"
            if tag not in geom_reasons:
                geom_reasons.append(tag)
        elif not geom_reasons:
            geom_reasons.append("gate_stack:equity_geom_reject")
        return False, geom_reasons

    # Stack admitted — QG owns Layer B; remaining geom_reasons are Layer A only.
    if geom_reasons:
        return False, geom_reasons
    return True, []


def merge_gate_reports(*reports: GateReport) -> GateReport:
    """Combine Layer A/B + Layer C reports into one PO stamp (last path/symbol wins)."""
    if not reports:
        return GateReport(path=PATH_ORB, admitted=True)
    base = reports[0]
    merged = GateReport(
        path=base.path,
        symbol=base.symbol,
        side=base.side,
        admitted=all(r.admitted for r in reports),
        deferred=any(getattr(r, "deferred", False) for r in reports) and all(r.admitted or getattr(r, "deferred", False) for r in reports),
        results=[],
    )
    seen: set = set()
    index_by_id: Dict[str, int] = {}
    ss_reval = frozenset({"trade_profile", "entry_part", "call_ss", "put_ss", "path_ss"})
    for r in reports:
        for gr in r.results:
            if gr.gate_id in seen:
                # Later Layer C re-eval with IV/sva/seconds may reject a dump
                # pocket that A/B passed because those stamps were missing.
                # Also replace a prior pass so the stamp carries real fields.
                if gr.gate_id in ss_reval:
                    idx = index_by_id[gr.gate_id]
                    prior = merged.results[idx]
                    if gr.status == "reject" or prior.status != "reject":
                        merged.results[idx] = gr
                continue
            seen.add(gr.gate_id)
            index_by_id[gr.gate_id] = len(merged.results)
            merged.results.append(gr)
    merged.admitted = not any(gr.status in ("reject", "defer") for gr in merged.results)
    merged.deferred = (not merged.admitted) and not any(
        gr.status == "reject" for gr in merged.results
    ) and any(gr.status == "defer" for gr in merged.results)
    return merged


def report_from_dict(d: Optional[Dict[str, Any]]) -> Optional[GateReport]:
    if not isinstance(d, dict):
        return None
    report = GateReport(
        path=str(d.get("path") or PATH_ORB),
        symbol=str(d.get("symbol") or ""),
        side=str(d.get("side") or ""),
        admitted=bool(d.get("admitted", True)),
        results=[],
    )
    for r in d.get("results") or []:
        if not isinstance(r, dict):
            continue
        report.results.append(
            GateResult(
                gate_id=str(r.get("gate_id") or ""),
                layer=str(r.get("layer") or ""),
                status=str(r.get("status") or ""),
                reason=str(r.get("reason") or ""),
                fields=dict(r.get("fields") or {}),
            )
        )
    return report


def stamp_merged_quality_gate_report(
    stamp_target: Dict[str, Any],
    stack_report: GateReport,
    *,
    prior: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Write merged quality_gate_report onto stamp_target; return the dict stamped."""
    prior_report = report_from_dict(prior if prior is not None else stamp_target.get("quality_gate_report"))
    if prior_report is not None:
        merged = merge_gate_reports(prior_report, stack_report)
    else:
        merged = stack_report
    out = merged.to_dict()
    stamp_target["quality_gate_report"] = out
    return out


def single_tp_decide(path: str, ctx: GateContext) -> GateReport:
    """Must Execute (data / Skip Signals / contract pick) -> one stamp -> one TP.

    ORB1, Trendline, and Breakout share this step. Returns a GateReport so the
    chain-fetch defer, the PO handoff freeze, and the report stamp stay the same.
    The freeze (with ``decision_digest``) goes to ctx.extras["po_decision_freeze"].
    """
    from datetime import datetime, timezone

    from central_tech.decision_stamp import build_decision_stamp
    from easy0DTE.modules import single_tp_admit as stp
    from easy0DTE.modules.decision_required import chain_attempt
    from easy0DTE.modules.trade_profile import decision_value_keys, profile_name

    side = _ss_side(ctx)
    if ctx.extras is None:
        ctx.extras = {}
    row = ctx.signal if isinstance(ctx.signal, dict) else {}
    sym = str(ctx.symbol or row.get("symbol") or "?").upper()
    slot = ctx.extras.get("trendline_slot")
    if slot is None:
        slot = row.get("trendline_slot")
    report = GateReport(path=path, symbol=sym, side=side)
    ver = stp.active_version()
    trade_id = row.get("trade_id") or row.get("signal_id") or row.get("handoff_id")
    fz_ctx = row.get("tp_freeze_ctx") if isinstance(row.get("tp_freeze_ctx"), dict) else None
    gate_at = datetime.now(timezone.utc).isoformat()
    values = {k: _ctx_lookup_float(ctx, k) for k in decision_value_keys()}
    ages = {}
    if isinstance(fz_ctx, dict):
        ages = dict(fz_ctx.get("ages_sec") or {})
    if not ages:
        for src, key in (
            ("chain", "tp_age_chain_sec"),
            ("quote", "tp_age_quote_sec"),
            ("spy", "tp_age_spy_sec"),
            ("session_tech", "tp_age_session_tech_sec"),
        ):
            try:
                if row.get(key) is not None:
                    ages[src] = float(row.get(key))
            except (TypeError, ValueError):
                pass
    state: Dict[str, Any] = {"stamp": None}

    def _finish(admitted: Optional[bool], reason: str, stage: str, decision=None, pick=None) -> GateReport:
        stamp = state["stamp"]
        if stamp is None:
            stamp = build_decision_stamp(
                path, side, slot, symbol=sym, base_values=values, row=row, pick=pick,
                ages=ages,
            )
        try:
            from central_tech.decision_freeze import build_decision_freeze

            fz = build_decision_freeze(
                values=dict(stamp.keys), path=path, side=side, slot=slot, symbol=sym,
                trade_id=trade_id, profile=profile_name(path, slot) or "",
                profile_version=f"single_tp:{ver}", admitted=admitted, reason=reason,
                stage=stage, context=fz_ctx, gate_at=gate_at,
            )
            fz["values"] = dict(stamp.keys)
            fz["decision_digest"] = stamp.digest
            fz["single_tp"] = {
                "version": ver,
                "evaluated_digest": (decision or {}).get("evaluated_digest"),
                "extensions": {"equity_volume_ratio": stamp.keys.get("equity_volume_ratio")},
                "pick": {k: v for k, v in dict(stamp.pick).items() if k not in ("limits", "eliminated")},
                "failed": (decision or {}).get("failed") or [],
                "legacy_reason": (decision or {}).get("legacy_reason"),
            }
            shadow = stp.shadow_version()
            if shadow != "off" and decision is not None:
                sd = stp.evaluate(stamp, shadow)
                fz["single_tp"]["shadow"] = {k: sd.get(k) for k in ("version", "admitted", "reason")}
            ctx.extras["po_decision_freeze"] = fz
            ctx.extras["po_decision_digest"] = stamp.digest
            ctx.extras["po_profile_decision"] = {
                "profile": fz.get("profile"),
                "side": side,
                "admitted": admitted,
                "reason": reason,
                "profile_version": fz.get("profile_version"),
                "scored_at": fz.get("gate_at"),
                "keys": dict(fz.get("values") or stamp.keys),
                "decision_digest": stamp.digest,
            }
            from modules.tp_decision_record_store import append_decision_record

            append_decision_record(fz, kind=f"single_tp_{stage}")
        except Exception as exc:
            log.debug("SINGLE_TP | freeze_error=%s", exc)
        status = "defer" if admitted is None else ("pass" if admitted else "reject")
        # A data wait is reported as gate dir_pct/defer so the chain-fetch
        # manager bounds it (-> dir_pct_data_unavailable, no cooldown).
        gid = "dir_pct" if status == "defer" else ("skip_signal" if stage == "skip_signal" else "trade_profile")
        report.results.append(GateResult(
            gate_id=gid, layer="C", status=status, reason=reason,
            fields={"stage": stage, "decision_digest": stamp.digest, "single_tp_version": ver},
        ))
        report.admitted = admitted is True
        report.deferred = admitted is None
        log.info(
            "TP_DECISION | %s %s %s slot=%s | version=%s | admitted=%s | reason=%s | stage=%s | "
            "digest=%s | grep=TP_DECISION",
            sym, path, side, slot, ver, admitted, reason, stage, stamp.digest[:12],
        )
        return report

    # M5 data readiness: no measured chain -> hold the break (bounded by the CFM defer).
    attempt = chain_attempt(ctx)
    try:
        from central_tech.decision_freeze import (
            stale_gated as _fz_stale_gated,
            stale_policy as _fz_policy,
        )

        _policy = _fz_policy()
    except Exception:
        _policy = "defer"
        _fz_stale_gated = None
    if attempt == "skipped_tp_freeze_budget":
        if _policy == "reject":
            return _finish(False, "tp_freeze_budget_exhausted", "tp_budget_reject")
        return _finish(None, "required_data_not_ready", "tp_stale_defer")
    if (attempt == "skipped" or str(attempt).startswith("skipped_")) and (
        values.get("call_chain_volume") is None and values.get("put_chain_volume") is None
    ):
        return _finish(None, "data_wait:chain", "data_wait")
    if _fz_stale_gated is not None:
        try:
            # All age-capped families, not only this cell's gated keys.
            # Emptying a missing-pass key used to admit the trade.
            _stale = _fz_stale_gated(None, ages)
        except Exception:
            _stale = []
        if _stale:
            log.warning(
                "TP_FREEZE_STALE | %s %s %s slot=%s | action=%s | stale=[%s]",
                sym, path, side, slot, _policy, ",".join(_stale),
            )
            if _policy == "reject":
                return _finish(False, "tp_freeze_stale_source", "tp_stale_reject")
            return _finish(None, "required_data_not_ready", "tp_stale_defer")
    # M6 Skip Signals: separate hard checks, outside the Trade Profile.
    prior_ss = _prior_gate_status(row)
    for gid, fn, tag in (("call_ss", _eval_call_ss, "call"), ("put_ss", _eval_put_ss, "put")):
        try:
            if resolve_gate_mode(path, gid) != "hard":
                continue
            if gid == "call_ss" and prior_ss.get("call_ss") == "pass":
                continue  # already decided pre-chain on the same break (no chain input)
            res = fn(ctx)
        except Exception as exc:
            log.debug("SINGLE_TP | %s error=%s", gid, exc)
            continue
        if res.status == "reject":
            ctx.extras["skip_signal_detail"] = res.reason
            # 2026-10-15: the zero-win deep skip carries its rule in the reason code.
            sub = ":break_dist_ge" if str(res.reason or "").startswith("break_dist_ge_") else ""
            return _finish(False, f"skip_signal:{tag}{sub}", "skip_signal")
    # M7 one contract pick (before the TP: the TP scores the contract we buy).
    pick: Dict[str, Any] = {"feasible": None, "reason": "no_chain_manager"}
    chain_mgr = None
    spot = 0.0
    try:
        from easy0DTE.modules.convex_chain_skew import (
            peek_cached_options_chain,
            resolve_underlying_price_from_signal,
        )

        chain_mgr = stp.registered_chain_manager()
        spot = float(resolve_underlying_price_from_signal(row) or 0.0)
        chain = peek_cached_options_chain(chain_mgr, sym, spot or None) if chain_mgr is not None else None
        pick = stp.pick_contract(chain, path=path, side=side, spot=spot)
    except Exception as exc:
        log.debug("SINGLE_TP | pick_error=%s", exc)
    ctx.extras["single_tp_pick"] = {k: v for k, v in pick.items() if k != "limits"}
    if pick.get("feasible") is False:
        why = str(pick.get("reason") or "unavailable")
        if why == "no_quote":
            age = None
            try:
                from central_tech.decision_freeze import chain_age_sec

                age = chain_age_sec(chain_mgr, sym, spot or None)
            except Exception:
                age = None
            if age is None:
                try:
                    age = float(row.get("tp_chain_age_sec"))
                except (TypeError, ValueError):
                    age = None
            just_live = age is not None and float(age) <= 3.0
            if not just_live:
                log.info(
                    "TP_CHAIN_QUOTE_HYDRATE | %s %s %s | age=%s | action=data_wait | "
                    "grep=TP_CHAIN_QUOTE_HYDRATE",
                    sym,
                    path,
                    side,
                    age,
                )
                return _finish(None, "data_wait:chain_quotes", "data_wait", pick=pick)
        return _finish(False, f"contract_unavailable:{why}", "contract_unavailable", pick=pick)
    stamp = build_decision_stamp(
        path, side, slot, symbol=sym, base_values=values, row=row, pick=pick, ages=ages,
    )
    state["stamp"] = stamp
    decision = stp.evaluate(stamp, ver)
    if decision.get("evaluated_digest") != stamp.digest:
        log.error("TP_STAMP_PARITY_FAIL | %s %s %s", sym, path, side)
        return _finish(False, "data_unavailable:stamp_parity", "data_unavailable", decision)
    stage = decision.get("stage") or "tp"
    if stage == "data_wait":
        return _finish(None, decision["reason"], "data_wait", decision)
    return _finish(decision.get("admitted") is True, decision["reason"], stage, decision)


def _prior_gate_status(row: Dict[str, Any]) -> Dict[str, str]:
    """gate_id -> status from the pre-chain report carried on the row."""
    out: Dict[str, str] = {}
    for src in (row, row.get("metadata") if isinstance(row.get("metadata"), dict) else None):
        rep = src.get("quality_gate_report") if isinstance(src, dict) else None
        if isinstance(rep, dict):
            for r in rep.get("results") or ():
                if isinstance(r, dict) and r.get("gate_id"):
                    out.setdefault(str(r["gate_id"]), str(r.get("status") or ""))
    return out
