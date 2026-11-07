#!/usr/bin/env python3
"""
ORB1 Breakout opening-window execution helpers (May 28 performance pass).

Adaptive opening selector, convex-preservation degraded tier, liquidity classes,
chain precheck snapshots, and vol-exotic routing — without new env surface area.
"""

from __future__ import annotations

import logging
import os
import random
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple, TYPE_CHECKING

from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from easy0DTE.modules.options_chain_manager import OptionContract

log = logging.getLogger(__name__)

_OPENING_ENTRY_MOD = None


def _dte0_entry_selection():
    global _OPENING_ENTRY_MOD
    if _OPENING_ENTRY_MOD is not None:
        return _OPENING_ENTRY_MOD
    import importlib.util
    import os

    path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "easy0DTE",
        "modules",
        "dte0_entry_selection.py",
    )
    path = os.path.normpath(path)
    spec = importlib.util.spec_from_file_location("dte0_entry_selection_opening", path)
    if spec is None or spec.loader is None:
        raise ImportError("dte0_entry_selection module not found")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _OPENING_ENTRY_MOD = mod
    return mod


PT = ZoneInfo("America/Los_Angeles")
OPENING_WINDOW_MINUTES = 10.0  # legacy default; live window from ORB_0DTE_DISCOVERY_*_PT

ULTRA_LIQUID_ETF_INDEX = frozenset(
    {
        "SPY",
        "QQQ",
        "IWM",
        "DIA",
        "TQQQ",
        "GLD",
        "SLV",
    }
)
MID_LIQUID_MOMENTUM = frozenset(
    {
        "SOFI",
        "OPEN",
        "NET",
        "IONQ",
        "HIMS",
        "COIN",
        "CRWD",
        "APP",
        "ARM",
        "RGTI",
        "QBTS",
    }
)
VOL_EXOTIC = frozenset({"VIX", "UVXY", "VXX"})
THIN_OR_SPECULATIVE = frozenset({"MAGS"})

CONVEX_PRESERVATION_MIN = 0.95
CONVEX_DEFERRED_RETRY_MIN = 0.90
HARD_MAX_SPREAD_PCT = 20.0


def read_orb_0dte_execution_mode(
    get_config_value: Any = None,
) -> Dict[str, bool]:
    """ORB1 Breakout execution routing flags (defaults: lotto-first, spreads off)."""
    if get_config_value is None:
        try:
            from modules.config_loader import get_config_value as _gcv
        except Exception:
            _gcv = None
        get_config_value = _gcv

    def _truthy(key: str, default: str) -> bool:
        raw = os.getenv(key)
        if raw is None or str(raw).strip() == "":
            if get_config_value is not None:
                try:
                    raw = get_config_value(key, default)
                except Exception:
                    raw = default
            else:
                raw = default
        return str(raw).strip().lower() in ("1", "true", "yes", "on")

    lotto_first = _truthy("0DTE_ORB_LOTTO_FIRST", "true")
    spread_primary = _truthy("0DTE_ORB_SPREAD_PRIMARY_ENABLED", "false")
    spread_fallback = _truthy("0DTE_ORB_SPREAD_FALLBACK_ENABLED", "false")
    if lotto_first:
        spread_primary = False
    return {
        "lotto_first": bool(lotto_first),
        "spread_primary_enabled": bool(spread_primary),
        "spread_fallback_enabled": bool(spread_fallback),
    }


def orb_0dte_execution_diag_context(
    *,
    strategy_type_at_730: str,
    mode: Optional[Dict[str, bool]] = None,
    selector_result: str = "",
    reject_reason: str = "",
) -> Dict[str, Any]:
    """Telegram / reject payload fields for ORB1 Breakout execution clarity."""
    flags = mode if isinstance(mode, dict) else read_orb_0dte_execution_mode()
    lotto_first = bool(flags.get("lotto_first"))
    return {
        "strategy_type_at_730": str(strategy_type_at_730 or "unknown"),
        "execution_structure": "lotto_first" if lotto_first else "legacy_spread_capable",
        "spread_primary_enabled": bool(flags.get("spread_primary_enabled")),
        "spread_fallback_enabled": bool(flags.get("spread_fallback_enabled")),
        "selector_result": str(selector_result or ""),
        "reject_reason": str(reject_reason or "")[:240],
    }


def summarize_single_leg_selector_reject(diag: Optional[Dict[str, Any]]) -> str:
    """
    Human-readable Premium / Delta / Spread reject line for ORB1 Breakout Telegram alerts.

    Built from ``options_chain_manager.last_selector_diagnostics`` after
    ``select_single_leg_contract`` returns None.
    """
    d = diag if isinstance(diag, dict) else {}
    if not d:
        return ""

    buckets = d.get("reject_bucket_counts")
    if not isinstance(buckets, dict):
        buckets = {}
    delta_band_buckets = d.get("delta_band_reject_bucket_counts")
    if not isinstance(delta_band_buckets, dict):
        delta_band_buckets = {}
    try:
        delta_band_n = int(d.get("delta_band_contracts") or 0)
    except (TypeError, ValueError):
        delta_band_n = 0
    # Prefer delta-band buckets when present — full-chain premium junk mislabels weekly near-misses.
    report_buckets = (
        delta_band_buckets
        if delta_band_n > 0 and any(int(delta_band_buckets.get(k, 0) or 0) > 0 for k in delta_band_buckets)
        else buckets
    )
    report_n = delta_band_n if report_buckets is delta_band_buckets else 0

    failure_reason = str(d.get("failure_reason") or "").strip().lower()
    side_candidates = int(d.get("side_candidates") or 0)
    contracts_scanned = int(d.get("contracts_this_side") or 0)
    if report_n <= 0:
        report_n = contracts_scanned

    try:
        pmin = float(d.get("premium_min", 0.0) or 0.0)
    except (TypeError, ValueError):
        pmin = 0.0
    try:
        pmax = float(d.get("premium_max", 0.0) or 0.0)
    except (TypeError, ValueError):
        pmax = 0.0
    try:
        max_spread_pct = float(d.get("max_spread_pct", 0.0) or 0.0)
    except (TypeError, ValueError):
        max_spread_pct = 0.0

    delta_window = d.get("delta_window")
    dmin = dmax = 0.0
    if isinstance(delta_window, (list, tuple)) and len(delta_window) >= 2:
        try:
            dmin = float(delta_window[0])
            dmax = float(delta_window[1])
        except (TypeError, ValueError):
            dmin = dmax = 0.0

    dominant = str(d.get("dominant_reject") or "").strip().lower()
    if (not dominant or dominant == "none") and report_buckets:
        dominant = max(report_buckets, key=lambda k: int(report_buckets.get(k, 0) or 0))

    def _bucket_pct(key: str) -> Optional[int]:
        cnt = int(report_buckets.get(key, 0) or 0)
        if report_n <= 0 or cnt <= 0:
            return None
        return int(round(100.0 * cnt / report_n))

    premium_range = ""
    if pmax > 0:
        premium_range = f"${pmin:.2f}–${pmax:.2f}" if pmin > 0 else f"≤${pmax:.2f}"

    delta_range = ""
    if dmax > dmin:
        delta_range = f"δ {dmin:.2f}–{dmax:.2f}"

    filter_labels = {
        "premium": "Premium filter",
        "spread": "Spread filter",
        "volume": "Volume filter",
        "oi": "Open interest filter",
        "side": "Side filter",
    }

    selected_expiry = str(d.get("selected_expiry") or "").strip()
    session_day = str(d.get("session_day") or "").strip()
    expiry_note = ""
    if selected_expiry and session_day and selected_expiry != session_day:
        expiry_note = f"weekly/nearest expiry {selected_expiry}"

    if failure_reason == "delta_band_empty":
        if side_candidates > 0:
            detail = (
                f"{side_candidates} passed premium/liquidity but none in {delta_range or 'delta band'}"
            )
        else:
            detail = f"no strikes in {delta_range or 'delta band'}"
        prem_pct = _bucket_pct("premium")
        if prem_pct and side_candidates == 0:
            return f"Premium filter: {prem_pct}% rejected outside {premium_range or 'premium band'}"
        return f"Delta filter: {detail}"

    if failure_reason == "penny_name_no_in_band_liquid_contract":
        return f"Delta filter: penny-name spot — no liquid contract in {delta_range or 'delta band'}"

    if failure_reason in ("liquidity_or_premium_filters", "single_leg_no_match") or side_candidates == 0:
        label = filter_labels.get(dominant, "Selector filter")
        parts: List[str] = []
        if dominant == "premium" and premium_range:
            parts.append(f"allowed {premium_range}")
        if dominant == "spread" and max_spread_pct > 0:
            parts.append(f"entry max ≤{max_spread_pct:.2f}%")
        pct = _bucket_pct(dominant) if dominant else None
        scope = "delta-band" if report_buckets is delta_band_buckets else "chain"
        if pct is not None:
            parts.append(f"{pct}% of {scope} rejected on {dominant}")
        elif dominant:
            parts.append(f"dominant reject={dominant}")
        if expiry_note:
            parts.append(expiry_note)
        if not parts:
            parts.append("no contract passed selector filters")
        return f"{label}: {' — '.join(parts)}"

    if dominant and dominant != "none":
        base = f"{filter_labels.get(dominant, dominant.title())} ({failure_reason or 'single_leg_no_match'})"
        return f"{base} — {expiry_note}" if expiry_note else base
    if failure_reason:
        return failure_reason.replace("_", " ")
    return ""


def _flatten_post_selector_spread_diagnostics(
    selector_diagnostics: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Merge nested execution-risk audit into a flat diag map (execution metrics win)."""
    d = dict(selector_diagnostics) if isinstance(selector_diagnostics, dict) else {}
    nested = d.get("execution_risk")
    if isinstance(nested, dict):
        for key, value in nested.items():
            if value is not None:
                d[key] = value
    return d


def _positive_diag_float(value: Any) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return 0.0
    return num if num > 0.0 else 0.0


def resolve_orb_0dte_post_selector_reject(
    *,
    block_reason: str,
    selector_diagnostics: Optional[Dict[str, Any]] = None,
    contract: Any = None,
) -> Tuple[str, str, str]:
    """
    Map post-selector execution failures (contract chosen, no fill) to alert reason codes.

    Returns (reason_code, human_summary, selector_result).

    Prefer execution-risk diagnostics (refreshed quote / reject audit) over the
    selector-time ``contract.bid_ask_spread_pct``, which can still show a passing
    spread after a later market-quality reject.
    """
    d = _flatten_post_selector_spread_diagnostics(selector_diagnostics)
    br = str(block_reason or "").strip().lower()

    # Execution audit first; contract attribute is selector-era and may be stale.
    spread_pct = _positive_diag_float(d.get("spread_pct"))
    if spread_pct <= 0.0 and contract is not None:
        spread_pct = _positive_diag_float(getattr(contract, "bid_ask_spread_pct", None))
    if spread_pct <= 0.0:
        bid = _positive_diag_float(d.get("bid"))
        ask = _positive_diag_float(d.get("ask"))
        if bid > 0.0 and ask > 0.0:
            mid = (bid + ask) / 2.0
            if mid > 0.0:
                spread_pct = (ask - bid) / mid * 100.0

    max_spread = _positive_diag_float(d.get("max_spread_pct"))

    if br in ("spread_too_wide",) or ("spread" in br and "wide" in br):
        reason_text = str(
            d.get("reject_reason_text") or d.get("reason_text") or ""
        ).strip()
        cap_txt = f"{max_spread:.2f}%" if max_spread > 0 else "entry cap"
        if spread_pct > 0.0:
            summary = (
                f"Spread filter at entry: {spread_pct:.2f}% bid/ask "
                f"(entry max ≤{cap_txt})"
            )
        elif reason_text:
            summary = f"Spread filter at entry: {reason_text}"
        else:
            summary = f"Spread filter at entry: too wide (entry max ≤{cap_txt})"
        return "spread_too_wide", summary, "entry_spread_reject"

    if br in ("order_submit_failed",) or "submit" in br or "broker" in br:
        detail = block_reason or "broker rejected order"
        return "order_submit_failed", f"Broker submit failed: {detail}", "broker_reject"

    if br in ("execution_risk_blocked", "insufficient_buying_power_or_clamps"):
        return br or "execution_risk_blocked", f"Execution risk blocked: {block_reason}", "risk_reject"

    if br in (
        "contract_too_expensive_for_slot",
        "option_too_expensive_for_budget",
        "no_affordable_contract_for_slot",
        "contract_too_expensive",
    ):
        unit = _positive_diag_float(d.get("unit_cost"))
        budget = _positive_diag_float(d.get("slot_budget") or d.get("entry_budget"))
        if unit > 0.0 and budget > 0.0:
            summary = (
                f"1 contract ${unit:.2f} exceeds slot budget ${budget:.2f}"
            )
        elif block_reason:
            summary = str(block_reason)
        else:
            summary = "Contract too expensive for slot budget"
        return "contract_too_expensive_for_slot", summary, "execution_reject"

    # Same LIVE_ENTRY_MARKET_QUALITY step as ask/bid size floors in
    # dte0_execution_risk_manager — keep alert prefix consistent for both sides.
    if br in (
        "missing_bid_or_ask",
        "ask_size_too_small",
        "bid_size_too_small",
        "ask_size_missing",
        "bid_size_missing",
        "quote_stale",
    ):
        return br, f"Market quality reject: {block_reason}", "market_quality_reject"

    if br:
        return "execution_failed_no_position", f"Execution blocked: {block_reason}", "execution_reject"

    return (
        "execution_failed_no_position",
        "Execution returned no position after contract selected",
        "execution_reject",
    )


def _diag_premium_bounds(diag: Optional[Dict[str, Any]]) -> Tuple[float, float]:
    d = diag if isinstance(diag, dict) else {}
    audit = d.get("audit") if isinstance(d.get("audit"), dict) else {}
    try:
        pmin = float(audit.get("premium_min", d.get("premium_min", 0.0)) or 0.0)
    except (TypeError, ValueError):
        pmin = 0.0
    try:
        pmax = float(audit.get("premium_max", d.get("premium_max", 0.0)) or 0.0)
    except (TypeError, ValueError):
        pmax = 0.0
    return pmin, pmax


def log_0dte_selector_reject_detail(
    *,
    symbol: str,
    direction: str,
    priority_score: float,
    strategy_type_at_730: str,
    execution_structure: str,
    lotto_candidate_found: bool,
    lotto_reject_reason: str,
    chain_contract_count: int,
    target_delta: float,
    premium_min: float,
    premium_max: float,
    final_reject_reason: str,
    spread_primary_enabled: bool = False,
    spread_fallback_enabled: bool = False,
    selector_result: str = "",
    # Legacy aliases (spread probe fields)
    initial_strategy_type: str = "",
    final_strategy_type: str = "",
    spread_candidate_found: bool = False,
    spread_reject_reason: str = "",
    option_type: str = "call",
) -> None:
    """Structured reject line — lotto vs spread visibility (grep 0DTE_SELECTOR_REJECT_DETAIL)."""
    st730 = str(strategy_type_at_730 or initial_strategy_type or "unknown")
    final_rc = str(final_reject_reason or final_strategy_type or "unknown")
    log.info(
        "0DTE_SELECTOR_REJECT_DETAIL | symbol=%s | direction=%s | priority_score=%.4f | "
        "strategy_type_at_730=%s | execution_structure=%s | spread_primary_enabled=%s | "
        "spread_fallback_enabled=%s | selector_result=%s | lotto_candidate_found=%s | "
        "lotto_reject_reason=%s | chain_contract_count=%d | target_delta=%.4f | "
        "premium_min=%.4f | premium_max=%.4f | final_reject_reason=%s | "
        "spread_candidate_found=%s | spread_reject_reason=%s | option_type=%s",
        str(symbol or "").upper(),
        str(direction or "UNKNOWN").upper(),
        float(priority_score or 0.0),
        st730,
        str(execution_structure or "unknown"),
        str(bool(spread_primary_enabled)).lower(),
        str(bool(spread_fallback_enabled)).lower(),
        str(selector_result or "")[:120],
        str(bool(lotto_candidate_found)).lower(),
        str(lotto_reject_reason or "")[:240],
        int(chain_contract_count or 0),
        float(target_delta or 0.0),
        float(premium_min or 0.0),
        float(premium_max or 0.0),
        final_rc[:240],
        str(bool(spread_candidate_found)).lower(),
        str(spread_reject_reason or "")[:240],
        str(option_type or "call").lower(),
    )


def emit_0dte_selector_reject_detail(
    options_chain_manager: Any,
    *,
    symbol: str,
    direction: str,
    priority_score: float,
    strategy_type_at_730: str,
    final_reject_reason: str,
    chain: Optional[Dict[str, Any]],
    option_type: str,
    target_delta: float,
    current_price: float,
    momentum_score: float = 0.0,
    single_leg_selector: str = "",
    selector_result: str = "",
    lotto_reject_reason: str = "",
    spread_fallback_attempted: bool = False,
    spread_width: float = 1.0,
    execution_mode: Optional[Dict[str, bool]] = None,
    # Legacy kwargs
    initial_strategy_type: str = "",
    final_strategy_type: str = "",
) -> None:
    """Probe lotto (and optional spread) and emit 0DTE_SELECTOR_REJECT_DETAIL."""
    mode = execution_mode if isinstance(execution_mode, dict) else read_orb_0dte_execution_mode()
    st730 = str(strategy_type_at_730 or initial_strategy_type or "unknown")
    final_rc = str(final_reject_reason or final_strategy_type or "single_leg_selection_failed")
    exec_struct = "lotto_first" if mode.get("lotto_first") else "legacy_spread_capable"
    opt = str(option_type or "call").lower()
    contracts = (chain or {}).get(opt + "s", []) or []
    lotto_candidate = None
    spread_candidate = None
    lotto_reason = str(lotto_reject_reason or "")
    spread_reason = ""
    if not lotto_reason:
        try:
            lotto_candidate = options_chain_manager.select_lotto_strike(
                chain=chain or {},
                option_type=opt,
                current_price=float(current_price),
                target_delta=float(target_delta or 0.15),
                symbol=str(symbol),
                momentum_score=float(momentum_score or 0.0),
            )
        except Exception as exc:
            lotto_reason = f"lotto_probe_error:{exc}"
        if lotto_candidate is None and not lotto_reason:
            _ld = getattr(options_chain_manager, "last_selector_diagnostics", {}) or {}
            _audit = _ld.get("audit") if isinstance(_ld.get("audit"), dict) else {}
            lotto_reason = str(
                _audit.get("reject_reason")
                or _ld.get("failure_reason")
                or single_leg_selector
                or "no_lotto_contract"
            )
    else:
        lotto_candidate = None
    if spread_fallback_attempted and mode.get("spread_fallback_enabled"):
        try:
            spread_candidate = options_chain_manager.select_debit_spread_strikes(
                chain=chain or {},
                option_type=opt,
                target_delta=float(target_delta or 0.15),
                spread_width=float(spread_width or 1.0),
                current_price=float(current_price),
                momentum_score=float(momentum_score or 0.0),
            )
        except Exception as exc:
            spread_reason = f"spread_probe_error:{exc}"
        if spread_candidate is None and not spread_reason:
            _sd = getattr(options_chain_manager, "last_selector_diagnostics", {}) or {}
            spread_reason = str(_sd.get("failure_reason") or "no_debit_spread_pair")
    pmin, pmax = _diag_premium_bounds(getattr(options_chain_manager, "last_selector_diagnostics", {}))
    log_0dte_selector_reject_detail(
        symbol=symbol,
        direction=direction,
        priority_score=priority_score,
        strategy_type_at_730=st730,
        execution_structure=exec_struct,
        spread_primary_enabled=bool(mode.get("spread_primary_enabled")),
        spread_fallback_enabled=bool(mode.get("spread_fallback_enabled")),
        selector_result=str(selector_result or "no_contract"),
        lotto_candidate_found=bool(lotto_candidate),
        lotto_reject_reason=lotto_reason,
        chain_contract_count=len(contracts),
        target_delta=float(target_delta or 0.0),
        premium_min=pmin,
        premium_max=pmax,
        final_reject_reason=final_rc,
        spread_candidate_found=bool(spread_candidate),
        spread_reject_reason=spread_reason,
        option_type=opt,
    )
STANDARD_MAX_SPREAD_PCT = 8.0

DEGRADED_CAPITAL_PCT = 0.375  # ~37.5% of normal ORB1 Breakout slot

# Hard exitability floors (not env — discipline guardrails)
ABSOLUTE_MICRO_PREMIUM = 0.03
PENNY_EXITABILITY_PREMIUM = 0.12


@dataclass
class OpeningSelectorParams:
    delta_min: float
    delta_max: float
    premium_min: float
    premium_max: float
    max_spread_pct: float
    min_volume: int
    min_open_interest: int
    allow_high_delta_liquid: bool = False


def classify_liquidity_class(symbol: str) -> str:
    sym = str(symbol or "").upper().strip()
    if sym in VOL_EXOTIC:
        return "vol_exotic"
    if sym in THIN_OR_SPECULATIVE:
        return "thin_or_speculative"
    if sym in ULTRA_LIQUID_ETF_INDEX:
        return "ultra_liquid_etf_index"
    if sym in MID_LIQUID_MOMENTUM:
        return "mid_liquid_momentum_equity"
    return "standard_equity"


def is_orb_0dte_opening_window(now_pt: Optional[datetime] = None) -> bool:
    """True during ORB1/TL0 discovery window (Shared.env DISCOVERY_ORB1_TL0_*)."""
    from modules.session_clock import orb1_tl0_session_open, now_pt_time

    clock = now_pt.time() if now_pt is not None else now_pt_time()
    return orb1_tl0_session_open(clock)


def opening_selector_params(
    *,
    liquidity_class: str,
    underlying_price: float,
    convex_score: float,
    mode: str,
) -> OpeningSelectorParams:
    """Relaxed thresholds for opening / convex-preservation modes."""
    liq = str(liquidity_class or "standard_equity")
    spot = float(underlying_price or 0.0)
    convex = float(convex_score or 0.0)
    is_opening = str(mode).lower() in {"opening", "opening_degraded", "convex_preservation"}

    _es = _dte0_entry_selection()
    delta_min, delta_max = _es.entry_delta_bounds()
    premium_min = _es.directional_entry_premium_min()
    premium_max = _es.directional_entry_premium_max() or 1.20
    min_vol, min_oi = 100, 200

    if liq == "ultra_liquid_etf_index":
        min_vol, min_oi = 80, 150
    elif liq == "mid_liquid_momentum_equity":
        min_vol, min_oi = 50, 100
    elif liq == "thin_or_speculative":
        # Stay strict on liquidity; delta band still follows unified entry config.
        premium_min = max(premium_min, 0.12)
        min_vol, min_oi = 200, 500
    elif liq == "vol_exotic":
        min_vol, min_oi = 150, 300

    if is_opening and convex >= CONVEX_PRESERVATION_MIN and liq != "thin_or_speculative":
        if spot > 0 and spot < 25.0:
            premium_min = 0.12

    from modules.spread_policy import path_spread_cap_points

    max_spread = float(path_spread_cap_points("orb1"))

    # Unified entry band caps at 0.42 — do not admit 0.45–0.55 preservation ITM overrides.
    allow_high_delta = False

    return OpeningSelectorParams(
        delta_min=delta_min,
        delta_max=delta_max,
        premium_min=premium_min,
        premium_max=premium_max,
        max_spread_pct=max_spread,
        min_volume=min_vol,
        min_open_interest=min_oi,
        allow_high_delta_liquid=allow_high_delta,
    )


def _contract_passes_hard_guards(c: "OptionContract") -> Tuple[bool, str]:
    """Minimal quote presence — spread/premium discipline handled in exitability scoring."""
    if float(c.bid or 0.0) <= 0.0 or float(c.ask or 0.0) <= 0.0:
        return False, "missing_or_zero_bid_ask"
    return True, ""


def _liquidity_relaxed_ok(c: "OptionContract", min_vol: int, min_oi: int) -> Tuple[bool, str]:
    vol = int(c.volume or 0)
    oi = int(c.open_interest or 0)
    spread = float(c.bid_ask_spread_pct or 0.0)
    if vol >= min_vol and oi >= min_oi:
        return True, ""
    if vol >= 500 or oi >= 1000:
        if spread <= 5.0:
            return True, "excellent_liquidity_one_leg"
    if vol < min_vol and oi < min_oi:
        return False, "weak_volume_and_oi"
    if vol < min_vol and oi < min_oi * 2:
        return False, "weak_volume"
    if oi < min_oi and vol < min_vol * 2:
        return False, "weak_oi"
    return True, "relaxed_liquidity_pass"


def _is_same_side_otm(c: "OptionContract", option_type: str, spot: float) -> bool:
    if option_type == "call":
        return float(c.strike) > float(spot)
    return float(c.strike) < float(spot)


def _opening_strike_in_play(c: "OptionContract", option_type: str, spot: float) -> bool:
    """Slight ITM through moderate OTM — executable opening band, not wings-only."""
    if spot <= 0:
        return False
    strike = float(c.strike)
    moneyness = strike / spot
    if option_type == "call":
        return 0.88 <= moneyness <= 1.20
    return 0.80 <= moneyness <= 1.12


def _distance_from_atm_pct(c: "OptionContract", spot: float) -> float:
    if spot <= 0:
        return 999.0
    return abs(float(c.strike) - float(spot)) / float(spot) * 100.0


def compute_exitability_score(
    c: "OptionContract",
    *,
    max_spread_pct: float = STANDARD_MAX_SPREAD_PCT,
) -> Tuple[float, Dict[str, Any], Optional[str]]:
    """
    Lightweight expected exitability (premium-based exits).
    Returns (score 0–100, detail dict, hard_reject_reason).
    """
    prem = float(c.mid_price)
    spread = float(c.bid_ask_spread_pct or 0.0)
    bid = float(c.bid or 0.0)
    ask = float(c.ask or 0.0)
    bid_size = int(getattr(c, "bid_size", 0) or 0)
    ask_size = int(getattr(c, "ask_size", 0) or 0)
    quote_age = getattr(c, "quote_age_sec", None)

    detail: Dict[str, Any] = {
        "spread_pct": round(spread, 4),
        "bid_size": bid_size,
        "ask_size": ask_size,
        "premium": round(prem, 4),
        "quote_age": quote_age,
        "stable_bid": False,
        "estimated_exit_quality": 0.0,
        "score": 0.0,
        "reject_reason_if_any": None,
    }

    if bid <= 0.0 or ask <= 0.0:
        detail["reject_reason_if_any"] = "zero_bid"
        return 0.0, detail, "zero_bid"
    try:
        from modules.spread_policy import cheap_allowance_applies as _sp_cheap

        _cheap_ok = _sp_cheap(bid, ask)
    except Exception:
        _cheap_ok = False
    if spread > HARD_MAX_SPREAD_PCT and not _cheap_ok:
        detail["reject_reason_if_any"] = "spread_gt_20pct"
        return 0.0, detail, "spread_gt_20pct"
    if prem < ABSOLUTE_MICRO_PREMIUM:
        detail["reject_reason_if_any"] = "premium_microscopic"
        return 0.0, detail, "premium_microscopic"

    score = 100.0
    score -= min(spread * 2.2, 55.0)
    if prem < PENNY_EXITABILITY_PREMIUM:
        score -= 28.0
    elif prem < 0.18:
        score -= 12.0
    elif prem < 0.25:
        score -= 5.0

    bid_frac = bid / prem if prem > 0 else 0.0
    detail["stable_bid"] = bid_frac >= 0.38
    if bid_frac < 0.30:
        score -= 22.0
    elif bid_frac < 0.38:
        score -= 10.0

    vol = int(c.volume or 0)
    oi = int(c.open_interest or 0)
    if vol < 30 and oi < 100:
        score -= 18.0
    elif vol < 80 and oi < 250:
        score -= 8.0

    if spread > max(max_spread_pct, 8.0) and prem < 0.22:
        score -= 15.0
    if spread > 12.0 and prem < 0.30:
        score -= 10.0

    score = max(0.0, min(100.0, score))
    detail["score"] = round(score, 2)
    detail["estimated_exit_quality"] = round(score / 100.0, 4)

    if score < 20.0 and spread > max_spread_pct:
        detail["reject_reason_if_any"] = f"spread_gt_{max_spread_pct:.2f}"
        return score, detail, detail["reject_reason_if_any"]

    return score, detail, None


def _liquidity_component_score(c: "OptionContract") -> float:
    vol = int(c.volume or 0)
    oi = int(c.open_interest or 0)
    spread = float(c.bid_ask_spread_pct or 0.0)
    vol_s = min(vol / 800.0, 1.0)
    oi_s = min(oi / 3000.0, 1.0)
    spread_s = max(0.0, 1.0 - spread / 15.0)
    return (vol_s * 0.4 + oi_s * 0.35 + spread_s * 0.25) * 100.0


def score_executable_contract(
    c: "OptionContract",
    *,
    option_type: str,
    spot: float,
    params: OpeningSelectorParams,
    convex_score: float,
) -> Tuple[float, Dict[str, Any], Optional[str]]:
    """
    Weighted executable quality (higher is better). Delta is a minor tie-break only.
    Returns (total_score, breakdown, hard_reject_reason).
    """
    exit_sc, exit_det, exit_reject = compute_exitability_score(
        c, max_spread_pct=float(params.max_spread_pct)
    )
    if exit_reject:
        flat = {
            "strike": round(float(c.strike), 2),
            "delta": round(abs(float(c.delta or 0.0)), 4),
            "premium": round(float(c.mid_price), 4),
            "spread_pct": round(float(c.bid_ask_spread_pct or 0.0), 4),
            "volume": int(c.volume or 0),
            "oi": int(c.open_interest or 0),
            "distance_from_atm": round(_distance_from_atm_pct(c, spot), 3),
            "exitability_score": round(exit_sc, 2),
            "liquidity_score": 0.0,
            "total_score": -1.0,
        }
        return -1.0, {**flat, "exitability": exit_det, "hard_reject": exit_reject}, exit_reject

    spread = float(c.bid_ask_spread_pct or 0.0)
    prem = float(c.mid_price)
    dist_atm = _distance_from_atm_pct(c, spot)

    spread_q = max(0.0, 1.0 - spread / max(float(params.max_spread_pct), 1.0))
    if prem >= 0.25:
        prem_q = min(1.0, prem / 0.50)
    elif prem >= PENNY_EXITABILITY_PREMIUM:
        prem_q = 0.55 + (prem - PENNY_EXITABILITY_PREMIUM) / 0.13 * 0.35
    else:
        prem_q = prem / PENNY_EXITABILITY_PREMIUM * 0.35

    atm_q = max(0.0, 1.0 - dist_atm / 18.0)
    liq_sc = _liquidity_component_score(c)
    exit_q = exit_sc / 100.0
    delta = abs(float(c.delta or 0.0))
    _es = _dte0_entry_selection()
    dmin, dmax = _es.entry_delta_bounds()
    span = max(0.01, dmax - dmin)
    if _es.prefer_lowest_entry_delta():
        delta_q = max(0.0, 1.0 - (delta - dmin) / span)
    else:
        delta_q = max(0.0, 1.0 - min(abs(delta - 0.42) / span, 1.0))

    # Slight ITM / near-ATM bonus for low-priced underlyings
    moneyness = float(c.strike) / spot if spot > 0 else 1.0
    itm_bonus = 0.0
    if spot < 25.0 and option_type == "call" and moneyness < 1.0 and moneyness >= 0.92:
        itm_bonus = 0.08
    elif spot < 25.0 and option_type == "put" and moneyness > 1.0 and moneyness <= 1.08:
        itm_bonus = 0.08

    total = (
        spread_q * 0.30
        + prem_q * 0.20
        + atm_q * 0.22
        + (liq_sc / 100.0) * 0.16
        + exit_q * 0.24
        + delta_q * 0.05
        + itm_bonus
    ) * 100.0

    breakdown = {
        "strike": round(float(c.strike), 2),
        "delta": round(delta, 4),
        "premium": round(prem, 4),
        "spread_pct": round(spread, 4),
        "volume": int(c.volume or 0),
        "oi": int(c.open_interest or 0),
        "distance_from_atm": round(dist_atm, 3),
        "exitability_score": round(exit_sc, 2),
        "liquidity_score": round(liq_sc, 2),
        "spread_quality": round(spread_q * 100, 2),
        "premium_quality": round(prem_q * 100, 2),
        "atm_proximity": round(atm_q * 100, 2),
        "delta_suitability": round(delta_q * 100, 2),
        "total_score": round(total, 2),
        "convex_score": round(float(convex_score), 4),
    }
    return total, breakdown, None


def _contract_passes_selector_gates(
    c: "OptionContract",
    *,
    params: OpeningSelectorParams,
    convex_score: float,
) -> Tuple[bool, str]:
    prem = float(c.mid_price)
    # Allowed mid = [premium_min, premium_max] from shared 0DTE entry policy.
    # Prefer side-specific codes over legacy premium_out_of_band (ambiguous which bound failed).
    if prem < params.premium_min:
        return False, "option_premium_too_low"
    if prem > params.premium_max:
        return False, "option_premium_too_high"
    spread = float(c.bid_ask_spread_pct or 0.0)
    if spread > params.max_spread_pct:
        try:
            from modules.spread_policy import cheap_allowance_applies as _sp_cheap

            _cheap = _sp_cheap(float(c.bid or 0.0), float(c.ask or 0.0))
        except Exception:
            _cheap = False
        if not _cheap:
            return False, f"spread_gt_{params.max_spread_pct:.2f}"
    liq_ok, liq_reason = _liquidity_relaxed_ok(c, params.min_volume, params.min_open_interest)
    if not liq_ok:
        return False, liq_reason
    ad = abs(float(c.delta or 0.0))
    in_band = params.delta_min <= ad <= params.delta_max
    high_delta_ok = (
        params.allow_high_delta_liquid
        and convex_score >= CONVEX_PRESERVATION_MIN
        and 0.45 <= ad <= 0.55
        and spread <= 6.0
        and int(c.volume or 0) >= 200
    )
    if not in_band and not high_delta_ok:
        return False, "delta_out_of_band"
    return True, "accepted"


def _missed_contract_payload(
    breakdown: Dict[str, Any],
    *,
    reason_not_selected: str,
    selected: bool = False,
) -> Dict[str, Any]:
    return {
        "strike": breakdown.get("strike"),
        "delta": breakdown.get("delta"),
        "premium": breakdown.get("premium"),
        "spread_pct": breakdown.get("spread_pct"),
        "volume": breakdown.get("volume"),
        "oi": breakdown.get("oi"),
        "distance_from_atm": breakdown.get("distance_from_atm"),
        "exitability_score": breakdown.get("exitability_score"),
        "nearest_liquid_score": breakdown.get("total_score"),
        "liquidity_score": breakdown.get("liquidity_score"),
        "reason_not_selected": reason_not_selected,
        "selected": selected,
    }


def log_best_executable_missed_contract(
    *,
    symbol: str,
    liquidity_class: str,
    rejection_stage: str,
    opening_selector_active: bool,
    preservation_eligible: bool,
    convex_score: float,
    momentum_score: float,
    missed: Optional[Dict[str, Any]],
) -> None:
    if not missed:
        return
    log_opening_telemetry(
        "ORB_0DTE_BEST_EXECUTABLE_MISSED_CONTRACT",
        symbol=str(symbol).upper(),
        liquidity_class=liquidity_class,
        rejection_stage=rejection_stage,
        opening_selector_active=str(opening_selector_active).lower(),
        preservation_eligible=str(preservation_eligible).lower(),
        convex_score=round(float(convex_score), 4),
        momentum_score=round(float(momentum_score), 2),
        strike=missed.get("strike"),
        delta=missed.get("delta"),
        premium=missed.get("premium"),
        spread_pct=missed.get("spread_pct"),
        volume=missed.get("volume"),
        oi=missed.get("oi"),
        exitability_score=missed.get("exitability_score"),
        nearest_liquid_score=missed.get("nearest_liquid_score"),
        reason_not_selected=missed.get("reason_not_selected"),
    )


def log_best_executable_missed_from_audit(
    audit: Dict[str, Any],
    *,
    symbol: str,
    liquidity_class: str,
    rejection_stage: str,
    opening_selector_active: bool,
    preservation_eligible: bool,
    convex_score: float,
    momentum_score: float = 0.0,
) -> None:
    missed = audit.get("best_executable_missed")
    if isinstance(missed, dict):
        log_best_executable_missed_contract(
            symbol=symbol,
            liquidity_class=liquidity_class,
            rejection_stage=rejection_stage,
            opening_selector_active=opening_selector_active,
            preservation_eligible=preservation_eligible,
            convex_score=convex_score,
            momentum_score=momentum_score,
            missed=missed,
        )


def select_nearest_liquid_opening_contract(
    contracts: List["OptionContract"],
    *,
    option_type: str,
    current_price: float,
    params: OpeningSelectorParams,
    convex_score: float,
    liquidity_class: str,
) -> Tuple[Optional["OptionContract"], Dict[str, Any]]:
    """
    Executable-quality ranked opening / preservation selector.
    Prefers spread, exitability, and ATM proximity over delta alignment.
    Returns (contract, audit_dict).
    """
    audit: Dict[str, Any] = {
        "opening_mode": True,
        "liquidity_class": liquidity_class,
        "convex_score": float(convex_score),
        "params": {
            "delta_min": params.delta_min,
            "delta_max": params.delta_max,
            "premium_min": params.premium_min,
            "max_spread_pct": params.max_spread_pct,
        },
        "reject_reason": None,
        "best_executable_missed": None,
        "scoreboard": [],
    }
    if not contracts:
        audit["reject_reason"] = "no_contracts"
        return None, audit

    spot = float(current_price)
    scored: List[Tuple["OptionContract", float, Dict[str, Any], str]] = []
    passing: List[Tuple["OptionContract", float, Dict[str, Any]]] = []
    best_missed: Optional[Tuple[float, Dict[str, Any], str]] = None

    for c in contracts:
        ok_guard, guard_reason = _contract_passes_hard_guards(c)
        if not ok_guard:
            continue
        if not _opening_strike_in_play(c, option_type, spot):
            continue

        total, breakdown, hard_reject = score_executable_contract(
            c,
            option_type=option_type,
            spot=spot,
            params=params,
            convex_score=convex_score,
        )
        if hard_reject:
            cand = _missed_contract_payload(
                breakdown, reason_not_selected=hard_reject, selected=False
            )
            rank = float(breakdown.get("exitability_score") or 0)
            if rank <= 0:
                rank = -float(breakdown.get("spread_pct") or 999.0)
            if best_missed is None or rank > best_missed[0]:
                best_missed = (rank, cand, hard_reject)
            continue

        gate_ok, gate_reason = _contract_passes_selector_gates(
            c, params=params, convex_score=convex_score
        )
        scored.append((c, total, breakdown, gate_reason))
        if gate_ok:
            passing.append((c, total, breakdown))
        else:
            missed = _missed_contract_payload(
                breakdown, reason_not_selected=gate_reason, selected=False
            )
            if best_missed is None or total > best_missed[0]:
                best_missed = (total, missed, gate_reason)

    scored.sort(key=lambda x: x[1], reverse=True)
    for _c, _tot, _br, _gr in scored[:8]:
        log_opening_telemetry(
            "ORB_0DTE_NEAREST_LIQUID_SCORE",
            strike=_br.get("strike"),
            delta=_br.get("delta"),
            premium=_br.get("premium"),
            spread_pct=_br.get("spread_pct"),
            volume=_br.get("volume"),
            oi=_br.get("oi"),
            distance_from_atm=_br.get("distance_from_atm"),
            exitability_score=_br.get("exitability_score"),
            liquidity_score=_br.get("liquidity_score"),
            total_score=_br.get("total_score"),
            selected="false",
            gate_reason=_gr,
        )
    audit["scoreboard"] = [s[2] for s in scored[:5]]

    if not passing:
        audit["reject_reason"] = (
            best_missed[2] if best_missed else (scored[-1][3] if scored else "no_passing_contract")
        )
        if best_missed:
            audit["best_executable_missed"] = best_missed[1]
        elif scored:
            _c, _t, _br, _gr = scored[0]
            audit["best_executable_missed"] = _missed_contract_payload(
                _br, reason_not_selected=_gr, selected=False
            )
        return None, audit

    _prefer_low = _dte0_entry_selection().prefer_lowest_entry_delta()
    if _prefer_low:
        passing.sort(
            key=lambda x: (
                abs(float(getattr(x[0], "delta", 0.0) or 0.0)),
                -x[1],
            )
        )
    else:
        passing.sort(key=lambda x: x[1], reverse=True)
    selected, sel_score, sel_breakdown = passing[0][0], passing[0][1], passing[0][2]
    sel_payload = _missed_contract_payload(sel_breakdown, reason_not_selected="accepted", selected=True)
    audit["selected_contract"] = sel_payload
    audit["best_executable_missed"] = sel_payload

    log_opening_telemetry(
        "ORB_0DTE_NEAREST_LIQUID_SCORE",
        strike=sel_breakdown.get("strike"),
        delta=sel_breakdown.get("delta"),
        premium=sel_breakdown.get("premium"),
        spread_pct=sel_breakdown.get("spread_pct"),
        volume=sel_breakdown.get("volume"),
        oi=sel_breakdown.get("oi"),
        distance_from_atm=sel_breakdown.get("distance_from_atm"),
        exitability_score=sel_breakdown.get("exitability_score"),
        liquidity_score=sel_breakdown.get("liquidity_score"),
        total_score=sel_breakdown.get("total_score"),
        selected="true",
        gate_reason="accepted",
    )
    _, exit_det, _ = compute_exitability_score(
        selected, max_spread_pct=float(params.max_spread_pct)
    )
    log_opening_telemetry(
        "ORB_0DTE_EXITABILITY_SCORE",
        strike=sel_breakdown.get("strike"),
        spread_pct=exit_det.get("spread_pct"),
        bid_size=exit_det.get("bid_size"),
        ask_size=exit_det.get("ask_size"),
        premium=exit_det.get("premium"),
        quote_age=exit_det.get("quote_age"),
        stable_bid=str(exit_det.get("stable_bid", False)).lower(),
        estimated_exit_quality=exit_det.get("estimated_exit_quality"),
        score=exit_det.get("score"),
        reject_reason_if_any="",
    )
    return selected, audit


def _contract_snapshot(c: "OptionContract", reason: str) -> Dict[str, Any]:
    return {
        "strike": float(c.strike),
        "delta": float(c.delta or 0.0),
        "premium": float(c.mid_price),
        "spread_pct": float(c.bid_ask_spread_pct),
        "volume": int(c.volume or 0),
        "open_interest": int(c.open_interest or 0),
        "reason": reason,
    }


def build_chain_sanity_snapshot(
    chain: Dict[str, List["OptionContract"]],
    option_type: str,
    underlying_price: float,
    symbol: str,
) -> Dict[str, Any]:
    """Best same-side liquidity metrics for precheck vs execution compare."""
    liq_class = classify_liquidity_class(symbol)
    contracts = list(chain.get(option_type + "s", []) or [])
    same_side = [c for c in contracts if _is_same_side_otm(c, option_type, underlying_price)]
    best_spread = 999.0
    best_vol = 0
    best_oi = 0
    atm_bid_ask = False
    for c in same_side:
        if float(c.bid or 0) > 0 and float(c.ask or 0) > 0:
            gap = abs(float(c.strike) - float(underlying_price))
            if gap <= max(1.0, float(underlying_price) * 0.02):
                atm_bid_ask = True
        sp = float(c.bid_ask_spread_pct or 999.0)
        if sp < best_spread:
            best_spread = sp
        best_vol = max(best_vol, int(c.volume or 0))
        best_oi = max(best_oi, int(c.open_interest or 0))
    if best_spread >= 999.0:
        best_spread = 0.0
    return {
        "symbol": str(symbol).upper(),
        "liquidity_class": liq_class,
        "best_same_side_spread_pct": float(best_spread),
        "best_same_side_volume": int(best_vol),
        "best_same_side_oi": int(best_oi),
        "atm_near_bid_ask": bool(atm_bid_ask),
        "underlying_price": float(underlying_price),
    }


def compare_chain_snapshots(
    precheck: Dict[str, Any],
    execution: Dict[str, Any],
) -> Dict[str, Any]:
    pre_s = float(precheck.get("best_same_side_spread_pct", 0) or 0)
    ex_s = float(execution.get("best_same_side_spread_pct", 0) or 0)
    pre_v = int(precheck.get("best_same_side_volume", 0) or 0)
    ex_v = int(execution.get("best_same_side_volume", 0) or 0)
    materially_worse = ex_s > pre_s * 1.35 + 2.0 if pre_s > 0 else ex_s > 15.0
    improved = ex_s < pre_s * 0.85 if pre_s > 0 else False
    vol_degraded = pre_v > 50 and ex_v < pre_v * 0.5
    return {
        "precheck_spread_pct": pre_s,
        "execution_spread_pct": ex_s,
        "precheck_volume": pre_v,
        "execution_volume": ex_v,
        "materially_worse": bool(materially_worse),
        "execution_improved": bool(improved),
        "volume_degraded": bool(vol_degraded),
        "divergence": bool(materially_worse or vol_degraded),
    }


def should_defer_vol_exotic(
    symbol: str,
    liquidity_class: str,
    opening_window: bool,
    chain_snapshot: Dict[str, Any],
) -> Tuple[bool, str]:
    if liquidity_class != "vol_exotic" and str(symbol).upper() not in VOL_EXOTIC:
        return False, ""
    if not opening_window:
        return False, ""
    spread = float(chain_snapshot.get("best_same_side_spread_pct", 0) or 0)
    vol = int(chain_snapshot.get("best_same_side_volume", 0) or 0)
    atm = bool(chain_snapshot.get("atm_near_bid_ask", False))
    if atm and spread <= 10.0 and vol >= 100:
        return False, "chain_quality_excellent"
    return True, "opening_liquidity_unstable"


def deferred_retry_delay_seconds() -> float:
    return float(random.uniform(15.0, 25.0))


def convex_score_from_signal(signal: Any) -> float:
    try:
        er = getattr(signal, "eligibility_result", None)
        if er is not None:
            return float(getattr(er, "eligibility_score", 0.0) or 0.0)
    except Exception:
        pass
    try:
        return float(getattr(signal, "convex_score", 0.0) or 0.0)
    except Exception:
        return 0.0


def log_opening_telemetry(event: str, **fields: Any) -> None:
    parts = [f"{k}={fields[k]}" for k in sorted(fields.keys())]
    log.info("%s | %s", event, " | ".join(parts))


def log_selector_failure_audit(
    *,
    symbol: str,
    strategy_direction: str,
    selector_diagnostics: Optional[Dict[str, Any]] = None,
    option_type: str = "",
    spot: float = 0.0,
    chain: Optional[Dict[str, Any]] = None,
    reason: str = "",
) -> None:
    """Structured telemetry when spread/single-leg selection fails after queue admission."""
    diag = dict(selector_diagnostics) if isinstance(selector_diagnostics, dict) else {}
    opt_side = str(option_type or diag.get("option_type") or "").lower()
    contracts = list((chain or {}).get(opt_side + "s", []) or []) if opt_side else []
    candidate_count = int(
        diag.get("candidate_spreads_built")
        or diag.get("contracts_this_side")
        or diag.get("total_contracts")
        or len(contracts)
    )
    rejection_breakdown: Dict[str, Any] = {
        "failure_stage": str(diag.get("failure_stage") or "unknown"),
        "failure_reason": str(diag.get("failure_reason") or reason or "unknown"),
        "long_liquidity_fail_count": int(diag.get("long_liquidity_fail_count") or 0),
        "short_liquidity_fail_count": int(diag.get("short_liquidity_fail_count") or 0),
        "short_leg_missing_count": int(diag.get("short_leg_missing_count") or 0),
        "itm_contracts": int(diag.get("itm_contracts") or 0),
        "total_contracts": int(diag.get("total_contracts") or len(contracts)),
    }
    long_examples = diag.get("long_liquidity_fail_examples")
    if isinstance(long_examples, list) and long_examples:
        rejection_breakdown["long_liquidity_fail_examples"] = long_examples[:3]
    short_examples = diag.get("short_liquidity_fail_examples")
    if isinstance(short_examples, list) and short_examples:
        rejection_breakdown["short_liquidity_fail_examples"] = short_examples[:3]
    best_candidate_summary: Dict[str, Any] = {}
    for key in (
        "long_strike",
        "short_strike",
        "long_delta",
        "short_delta",
        "long_premium",
        "short_premium",
        "spread_width",
        "debit",
        "band_label",
    ):
        if key in diag and diag.get(key) is not None:
            best_candidate_summary[key] = diag.get(key)
    if not best_candidate_summary and contracts:
        nearest = sorted(
            contracts,
            key=lambda c: abs(float(getattr(c, "strike", 0.0) or 0.0) - float(spot or 0.0)),
        )[:1]
        if nearest:
            c0 = nearest[0]
            best_candidate_summary = {
                "nearest_strike": float(getattr(c0, "strike", 0.0) or 0.0),
                "nearest_delta": getattr(c0, "delta", None),
                "nearest_bid": float(getattr(c0, "bid", 0.0) or 0.0),
                "nearest_ask": float(getattr(c0, "ask", 0.0) or 0.0),
                "nearest_spread_pct": getattr(c0, "spread_pct", None),
            }
    log_opening_telemetry(
        "ORB_0DTE_SELECTOR_FAILURE_AUDIT",
        symbol=str(symbol).upper(),
        strategy_direction=str(strategy_direction or "unknown"),
        option_type=opt_side or "unknown",
        spot=round(float(spot or diag.get("spot") or 0.0), 4),
        selector=str(diag.get("selector") or "unknown"),
        candidate_count=candidate_count,
        rejection_breakdown=rejection_breakdown,
        best_candidate_summary=best_candidate_summary,
        final_reason=str(reason or diag.get("failure_reason") or "spread_selection_failed"),
    )


# Borderline spread audit band (Priority 5 — measure opportunity near cap without changing limits)
BORDERLINE_SPREAD_MIN_PCT = 8.5
BORDERLINE_SPREAD_MAX_PCT = 10.5
_BORDERLINE_SPREAD_LOG_TS: Dict[str, float] = {}
_BORDERLINE_SPREAD_LOG_SEC = 30.0

# Minimum convex to attempt ITM opening fallback outside thin chains when not in opening window
ITM_OPENING_FALLBACK_MIN_CONVEX = 0.85


def log_borderline_spread_rejection(
    *,
    symbol: str,
    option_type: str,
    strike: float,
    spread_pct: float,
    cap_pct: float,
    route_evaluated: str,
    oi: int = 0,
    volume: int = 0,
    delta: Optional[float] = None,
) -> None:
    sp = float(spread_pct)
    if sp < BORDERLINE_SPREAD_MIN_PCT or sp > BORDERLINE_SPREAD_MAX_PCT:
        return
    key = (
        f"{str(symbol or '').upper()}|{str(option_type or '').upper()}|"
        f"{round(float(strike), 4)}"
    )
    now = time.time()
    last = float(_BORDERLINE_SPREAD_LOG_TS.get(key) or 0.0)
    if last > 0 and (now - last) < _BORDERLINE_SPREAD_LOG_SEC:
        return
    _BORDERLINE_SPREAD_LOG_TS[key] = now
    log_opening_telemetry(
        "ORB_0DTE_BORDERLINE_SPREAD_REJECTION",
        symbol=str(symbol).upper(),
        side=str(option_type or "").upper(),
        strike=round(float(strike), 4),
        spread_pct=round(sp, 4),
        cap_pct=round(float(cap_pct), 4),
        route_evaluated=str(route_evaluated or "unknown"),
        oi=int(oi or 0),
        volume=int(volume or 0),
        delta=round(float(delta), 5) if delta is not None else None,
        band=f"{BORDERLINE_SPREAD_MIN_PCT}-{BORDERLINE_SPREAD_MAX_PCT}",
    )


def _is_same_side_otm_contract(c: "OptionContract", option_type: str, spot: float) -> bool:
    if option_type == "call":
        return float(c.strike) > float(spot)
    return float(c.strike) < float(spot)


def compute_best_rejected_alternative(
    chain: Optional[Dict[str, Any]],
    *,
    option_type: str,
    spot: float,
    symbol: str,
    route_evaluated: str,
    final_reject_reason: str,
) -> Optional[Dict[str, Any]]:
    """
    Best same-side OTM contract with valid quotes before final rejection (Priority 2).
    Ranked by lowest spread, then delta proximity to 0.35.
    """
    opt = str(option_type or "").lower()
    if not chain or opt not in {"call", "put"}:
        return None
    contracts = list(chain.get(opt + "s", []) or [])
    if not contracts or float(spot) <= 0:
        return None
    target_delta = 0.35
    best: Optional[Tuple[float, float, "OptionContract"]] = None
    for c in contracts:
        if float(c.bid or 0) <= 0 or float(c.ask or 0) <= 0:
            continue
        if not _is_same_side_otm_contract(c, opt, float(spot)):
            continue
        prem = float(c.mid_price or 0)
        if prem < ABSOLUTE_MICRO_PREMIUM:
            continue
        sp = float(c.bid_ask_spread_pct or 999.0)
        ad = abs(float(c.delta or 0.0))
        delta_dist = abs(ad - target_delta)
        rank = (sp, delta_dist)
        if best is None or rank < (best[0], best[1]):
            best = (sp, delta_dist, c)
    if best is None:
        return None
    c = best[2]
    side_label = "C" if opt == "call" else "P"
    return {
        "symbol": str(symbol).upper(),
        "side": opt.upper(),
        "best_alt": f"{float(c.strike):g}{side_label}",
        "strike": float(c.strike),
        "delta": round(float(c.delta or 0.0), 5),
        "premium": round(float(c.mid_price), 4),
        "spread_pct": round(float(c.bid_ask_spread_pct or 0.0), 4),
        "volume": int(c.volume or 0),
        "oi": int(c.open_interest or 0),
        "route_evaluated": str(route_evaluated or "unknown"),
        "final_reject_reason": str(final_reject_reason or "unknown"),
    }


def log_best_rejected_alternative(
    chain: Optional[Dict[str, Any]],
    *,
    option_type: str,
    spot: float,
    symbol: str,
    route_evaluated: str,
    final_reject_reason: str,
) -> None:
    alt = compute_best_rejected_alternative(
        chain,
        option_type=option_type,
        spot=spot,
        symbol=symbol,
        route_evaluated=route_evaluated,
        final_reject_reason=final_reject_reason,
    )
    if not alt:
        return
    log_opening_telemetry("ORB_0DTE_BEST_REJECTED_ALTERNATIVE", **alt)


def log_opening_fallback_attempt(
    *,
    symbol: str,
    strategy_type: str,
    original_reject_reason: str,
    liquidity_class: str,
    opening_active: bool,
    convex_score: float,
) -> None:
    log_opening_telemetry(
        "ORB_0DTE_OPENING_FALLBACK_ATTEMPT",
        symbol=str(symbol).upper(),
        strategy_type=str(strategy_type or "unknown"),
        original_reject_reason=str(original_reject_reason or "unknown")[:200],
        liquidity_class=str(liquidity_class or "standard_equity"),
        opening_active=str(bool(opening_active)).lower(),
        convex_score=round(float(convex_score), 4),
    )


def log_opening_fallback_accepted(
    *,
    symbol: str,
    strategy_type: str,
    route: str,
    original_reject_reason: str,
    strike: float,
    delta: float,
    premium: float,
    spread_pct: float,
    quantity: int,
    degraded_capital: bool,
) -> None:
    log_opening_telemetry(
        "ORB_0DTE_OPENING_FALLBACK_ACCEPTED",
        symbol=str(symbol).upper(),
        strategy_type=str(strategy_type or "unknown"),
        route=str(route or "unknown"),
        original_reject_reason=str(original_reject_reason or "unknown")[:200],
        strike=round(float(strike), 4),
        delta=round(float(delta), 5),
        premium=round(float(premium), 4),
        spread_pct=round(float(spread_pct), 4),
        quantity=int(quantity),
        degraded_capital=str(bool(degraded_capital)).lower(),
    )


def log_opening_fallback_rejected(
    *,
    symbol: str,
    strategy_type: str,
    original_reject_reason: str,
    reason: str,
    liquidity_class: str,
) -> None:
    log_opening_telemetry(
        "ORB_0DTE_OPENING_FALLBACK_REJECTED",
        symbol=str(symbol).upper(),
        strategy_type=str(strategy_type or "unknown"),
        original_reject_reason=str(original_reject_reason or "unknown")[:200],
        reason=str(reason or "unknown")[:200],
        liquidity_class=str(liquidity_class or "standard_equity"),
    )


async def try_itm_opening_preservation_fallback(
    *,
    options_chain_manager: Any,
    options_executor: Any,
    chain: Dict[str, Any],
    option_type: str,
    current_price: float,
    symbol: str,
    direction: str,
    strategy_type: str,
    capital_allocated: float,
    momentum_score: float,
    convex_score: float,
    liquidity_class: str,
    opening_active: bool,
    original_reject_reason: str,
    orb_range_pct: float = 0.0,
    degraded_capital_pct: float = DEGRADED_CAPITAL_PCT,
) -> Tuple[Optional[Any], Optional[Any], str]:
    """
  Priority 1: After ITM spread ladder fails, try opening preservation then directional single-leg.
  Uses degraded capital sizing; does not enable global lotto sleeve.
  Returns (position, contract, route_label).
    """
    sym = str(symbol).upper()
    liq = str(liquidity_class or "standard_equity")
    if liq == "thin_or_speculative":
        log_opening_fallback_rejected(
            symbol=sym,
            strategy_type=strategy_type,
            original_reject_reason=original_reject_reason,
            reason="thin_or_speculative_skip",
            liquidity_class=liq,
        )
        return None, None, ""

    if not opening_active and float(convex_score) < ITM_OPENING_FALLBACK_MIN_CONVEX:
        log_opening_fallback_rejected(
            symbol=sym,
            strategy_type=strategy_type,
            original_reject_reason=original_reject_reason,
            reason="outside_opening_window_and_convex_below_min",
            liquidity_class=liq,
        )
        return None, None, ""

    log_opening_fallback_attempt(
        symbol=sym,
        strategy_type=strategy_type,
        original_reject_reason=original_reject_reason,
        liquidity_class=liq,
        opening_active=opening_active,
        convex_score=convex_score,
    )

    opt = str(option_type or "").lower()
    spot = float(current_price)
    selected = None
    route = ""

    pres = options_chain_manager.select_opening_preservation_contract(
        chain=chain,
        option_type=opt,
        current_price=spot,
        symbol=sym,
        mode="opening",
        convex_score=float(convex_score),
        liquidity_class=liq,
    )
    if pres:
        selected, route = pres, "opening_preservation"
    else:
        selected = options_chain_manager.select_single_leg_contract(
            chain=chain,
            option_type=opt,
            current_price=spot,
            profile="directional",
            symbol=sym,
            adaptive_open_window=True,
            momentum_score=float(momentum_score or 0.0),
            orb_range_pct=float(orb_range_pct or 0.0),
        )
        if selected:
            route = "directional_single_leg_opening"

    if not selected:
        _diag = getattr(options_chain_manager, "last_selector_diagnostics", {}) or {}
        log_opening_fallback_rejected(
            symbol=sym,
            strategy_type=strategy_type,
            original_reject_reason=original_reject_reason,
            reason=str((_diag.get("audit") or {}).get("reject_reason", _diag.get("failure_reason", "no_contract"))),
            liquidity_class=liq,
        )
        return None, None, ""

    cap = float(capital_allocated) * float(degraded_capital_pct)
    option_cost = float(selected.mid_price) * 100.0
    qty = max(1, int(cap / option_cost)) if option_cost > 0 else 1
    position = await options_executor.execute_by_strategy(
        strategy_type="lotto",
        direction=str(direction),
        quantity=qty,
        single_leg_contract=selected,
        send_open_alert=False,
    )
    if position:
        log_opening_fallback_accepted(
            symbol=sym,
            strategy_type=strategy_type,
            route=route,
            original_reject_reason=original_reject_reason,
            strike=float(selected.strike),
            delta=float(selected.delta or 0.0),
            premium=float(selected.mid_price),
            spread_pct=float(selected.bid_ask_spread_pct or 0.0),
            quantity=qty,
            degraded_capital=True,
        )
        return position, selected, route

    log_opening_fallback_rejected(
        symbol=sym,
        strategy_type=strategy_type,
        original_reject_reason=original_reject_reason,
        reason="broker_execution_failed",
        liquidity_class=liq,
    )
    return None, None, route
