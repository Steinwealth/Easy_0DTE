"""Observation-layer telemetry for exact quotes, exit cadence, and MFE decisions.

Strategy thresholds are intentionally untouched — this module only records and logs.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)

# Canonical miss classes for exact-contract quote audits.
MISS_COLON_WIRE = "colon_wire_request"
MISS_RESPONSE_MATCH = "response_matching"
MISS_BATCH_OMISSION = "batch_omission"
MISS_FROZEN_IDENTITY = "stale_or_incorrect_frozen_identity"
MISS_ETRADE_OMIT = "etrade_intermittent_omit"
MISS_RETRY_BLOCKING = "retries_blocking_unified_loop"
MISS_UNKNOWN = "unknown"


def classify_exact_quote_miss(
    *,
    request_parameter_sent: Optional[str] = None,
    rejection_reason: Optional[str] = None,
    response_contract_count: Optional[int] = None,
    match_method: Optional[str] = None,
    recovery_attempted: bool = False,
    recovery_succeeded: bool = False,
    frozen_wire: Optional[str] = None,
) -> str:
    """Map a miss into an audit bucket (DELL-class diagnosis)."""
    reason = str(rejection_reason or "").strip().lower()
    req = str(request_parameter_sent or "").strip()
    wire = str(frozen_wire or req).strip()
    count = response_contract_count
    method = str(match_method or "").strip().lower()

    if reason in {"symbol_not_in_response", "missing_exact_quote", "missing"}:
        if count is not None and int(count) == 0:
            return MISS_ETRADE_OMIT
        if count is not None and int(count) > 0:
            # Broker returned other contracts but not ours → batch omission.
            return MISS_BATCH_OMISSION
        if ":" in wire and wire.count(":") >= 5:
            return MISS_COLON_WIRE
        return MISS_ETRADE_OMIT

    if reason in {"identity_mismatch", "response_missing_structured_identity"} or method in {
        "identity_mismatch",
        "response_missing_structured_identity",
        "none",
    }:
        return MISS_RESPONSE_MATCH

    if reason in {"stale_quote", "quote_too_old", "max_age"}:
        return MISS_FROZEN_IDENTITY

    if recovery_attempted and not recovery_succeeded:
        return MISS_RETRY_BLOCKING

    return MISS_UNKNOWN


def _pctile(sorted_vals: Sequence[float], p: float) -> Optional[float]:
    if not sorted_vals:
        return None
    n = len(sorted_vals)
    if n == 1:
        return float(sorted_vals[0])
    idx = min(n - 1, max(0, int(round(float(p) * (n - 1)))))
    return float(sorted_vals[idx])


def _median(vals: Sequence[float]) -> Optional[float]:
    if not vals:
        return None
    s = sorted(float(v) for v in vals)
    n = len(s)
    if n % 2 == 1:
        return float(s[n // 2])
    return 0.5 * (s[n // 2 - 1] + s[n // 2])


@dataclass
class ExactQuoteReliabilityStats:
    """Per-position exact-contract quote counters (open-position lifetime)."""

    trade_id: str = ""
    exact_quote_requests: int = 0
    exact_quote_successes: int = 0
    exact_quote_misses: int = 0
    fallback_source_counts: Dict[str, int] = field(default_factory=dict)
    recovery_candidate_used: int = 0
    winning_symbol_format_counts: Dict[str, int] = field(default_factory=dict)
    miss_class_counts: Dict[str, int] = field(default_factory=dict)
    trusted_bid_ages_sec: List[float] = field(default_factory=list)
    _gap_open_mono: Optional[float] = None
    _gap_lengths_sec: List[float] = field(default_factory=list)
    longest_continuous_gap_sec: float = 0.0
    last_request_symbol: str = ""
    last_miss_class: str = ""
    last_winning_symbol_format: str = ""
    max_age_samples: int = 240

    def record_request(self, *, request_symbol: str = "") -> None:
        self.exact_quote_requests += 1
        if request_symbol:
            self.last_request_symbol = str(request_symbol)

    def record_success(
        self,
        *,
        bid_age_sec: Optional[float] = None,
        winning_symbol_format: str = "",
        recovery_used: bool = False,
        now_mono: Optional[float] = None,
    ) -> None:
        self.exact_quote_successes += 1
        if winning_symbol_format:
            fmt = str(winning_symbol_format)
            self.winning_symbol_format_counts[fmt] = int(
                self.winning_symbol_format_counts.get(fmt, 0)
            ) + 1
            self.last_winning_symbol_format = fmt
        if recovery_used:
            self.recovery_candidate_used += 1
        if bid_age_sec is not None:
            try:
                age = max(0.0, float(bid_age_sec))
                self.trusted_bid_ages_sec.append(age)
                if len(self.trusted_bid_ages_sec) > self.max_age_samples:
                    self.trusted_bid_ages_sec = self.trusted_bid_ages_sec[-self.max_age_samples :]
            except (TypeError, ValueError):
                pass
        mono = float(now_mono if now_mono is not None else time.monotonic())
        if self._gap_open_mono is not None:
            gap = max(0.0, mono - float(self._gap_open_mono))
            self._gap_lengths_sec.append(gap)
            self.longest_continuous_gap_sec = max(self.longest_continuous_gap_sec, gap)
            self._gap_open_mono = None

    def record_miss(
        self,
        *,
        miss_class: str,
        fallback_source: str = "",
        now_mono: Optional[float] = None,
    ) -> None:
        self.exact_quote_misses += 1
        cls = str(miss_class or MISS_UNKNOWN)
        self.miss_class_counts[cls] = int(self.miss_class_counts.get(cls, 0)) + 1
        self.last_miss_class = cls
        if fallback_source:
            src = str(fallback_source)
            self.fallback_source_counts[src] = int(self.fallback_source_counts.get(src, 0)) + 1
        mono = float(now_mono if now_mono is not None else time.monotonic())
        if self._gap_open_mono is None:
            self._gap_open_mono = mono

    @property
    def exact_success_rate(self) -> Optional[float]:
        if self.exact_quote_requests <= 0:
            return None
        return float(self.exact_quote_successes) / float(self.exact_quote_requests)

    def summary(self) -> Dict[str, Any]:
        ages = sorted(float(a) for a in self.trusted_bid_ages_sec)
        # Include an open gap in longest if still missing.
        longest = float(self.longest_continuous_gap_sec or 0.0)
        if self._gap_open_mono is not None:
            longest = max(longest, max(0.0, time.monotonic() - float(self._gap_open_mono)))
        top_fmt = ""
        if self.winning_symbol_format_counts:
            top_fmt = max(self.winning_symbol_format_counts.items(), key=lambda kv: kv[1])[0]
        top_miss = ""
        if self.miss_class_counts:
            top_miss = max(self.miss_class_counts.items(), key=lambda kv: kv[1])[0]
        return {
            "trade_id": self.trade_id,
            "exact_quote_requests": int(self.exact_quote_requests),
            "exact_quote_successes": int(self.exact_quote_successes),
            "exact_quote_misses": int(self.exact_quote_misses),
            "exact_success_rate": (
                round(float(self.exact_success_rate), 4)
                if self.exact_success_rate is not None
                else None
            ),
            "longest_continuous_gap_sec": round(longest, 3),
            "trusted_bid_age_p50": (
                round(float(_pctile(ages, 0.50)), 3) if ages else None
            ),
            "trusted_bid_age_p95": (
                round(float(_pctile(ages, 0.95)), 3) if ages else None
            ),
            "trusted_bid_age_max": round(float(ages[-1]), 3) if ages else None,
            "fallback_source_counts": dict(self.fallback_source_counts),
            "recovery_candidate_used": int(self.recovery_candidate_used),
            "winning_symbol_format": top_fmt or self.last_winning_symbol_format or None,
            "winning_symbol_format_counts": dict(self.winning_symbol_format_counts),
            "miss_class_counts": dict(self.miss_class_counts),
            "dominant_miss_class": top_miss or self.last_miss_class or None,
            "last_request_symbol": self.last_request_symbol or None,
        }


@dataclass
class ExitEvalCadenceSample:
    evaluation_interval_ms: float = 0.0
    quote_wait_ms: float = 0.0
    monitor_runtime_ms: float = 0.0
    decision_runtime_ms: float = 0.0
    total_cycle_ms: float = 0.0


@dataclass
class ExitEvalCadenceStats:
    """Authoritative exit-evaluation cadence (evaluate() to evaluate())."""

    trade_id: str = ""
    samples: List[ExitEvalCadenceSample] = field(default_factory=list)
    last_eval_mono: float = 0.0
    max_samples: int = 180

    def record(
        self,
        *,
        now_mono: float,
        quote_wait_ms: float = 0.0,
        monitor_runtime_ms: float = 0.0,
        decision_runtime_ms: float = 0.0,
        total_cycle_ms: float = 0.0,
    ) -> Optional[float]:
        interval_ms: Optional[float] = None
        if self.last_eval_mono > 0:
            interval_ms = max(0.0, (float(now_mono) - float(self.last_eval_mono)) * 1000.0)
            self.samples.append(
                ExitEvalCadenceSample(
                    evaluation_interval_ms=float(interval_ms),
                    quote_wait_ms=float(quote_wait_ms or 0.0),
                    monitor_runtime_ms=float(monitor_runtime_ms or 0.0),
                    decision_runtime_ms=float(decision_runtime_ms or 0.0),
                    total_cycle_ms=float(total_cycle_ms or 0.0),
                )
            )
            if len(self.samples) > self.max_samples:
                self.samples = self.samples[-self.max_samples :]
        self.last_eval_mono = float(now_mono)
        return interval_ms

    def summary(self) -> Dict[str, Any]:
        intervals = [float(s.evaluation_interval_ms) for s in self.samples]
        quote_waits = [float(s.quote_wait_ms) for s in self.samples]
        monitor_rts = [float(s.monitor_runtime_ms) for s in self.samples]
        decision_rts = [float(s.decision_runtime_ms) for s in self.samples]
        totals = [float(s.total_cycle_ms) for s in self.samples]
        s_int = sorted(intervals)

        def _bucket(vals: Sequence[float]) -> Dict[str, Any]:
            if not vals:
                return {
                    "median": None,
                    "p95": None,
                    "maximum": None,
                    "count_gt_2_5s": 0,
                    "count_gt_3_5s": 0,
                    "count_gt_5s": 0,
                    "count_gt_10s": 0,
                }
            ss = sorted(float(v) for v in vals)
            return {
                "median": round(float(_median(ss) or 0.0), 1),
                "p95": round(float(_pctile(ss, 0.95) or 0.0), 1),
                "maximum": round(float(ss[-1]), 1),
                "count_gt_2_5s": sum(1 for v in ss if v > 2500.0),
                "count_gt_3_5s": sum(1 for v in ss if v > 3500.0),
                "count_gt_5s": sum(1 for v in ss if v > 5000.0),
                "count_gt_10s": sum(1 for v in ss if v > 10000.0),
            }

        return {
            "trade_id": self.trade_id,
            "samples": len(self.samples),
            "evaluation_interval_ms": _bucket(s_int),
            "quote_wait_ms": {
                "median": round(float(_median(quote_waits) or 0.0), 1) if quote_waits else None,
                "p95": round(float(_pctile(sorted(quote_waits), 0.95) or 0.0), 1)
                if quote_waits
                else None,
                "maximum": round(max(quote_waits), 1) if quote_waits else None,
            },
            "monitor_runtime_ms": {
                "median": round(float(_median(monitor_rts) or 0.0), 1) if monitor_rts else None,
                "p95": round(float(_pctile(sorted(monitor_rts), 0.95) or 0.0), 1)
                if monitor_rts
                else None,
                "maximum": round(max(monitor_rts), 1) if monitor_rts else None,
            },
            "decision_runtime_ms": {
                "median": round(float(_median(decision_rts) or 0.0), 1) if decision_rts else None,
                "p95": round(float(_pctile(sorted(decision_rts), 0.95) or 0.0), 1)
                if decision_rts
                else None,
                "maximum": round(max(decision_rts), 1) if decision_rts else None,
            },
            "total_cycle_ms": {
                "median": round(float(_median(totals) or 0.0), 1) if totals else None,
                "p95": round(float(_pctile(sorted(totals), 0.95) or 0.0), 1) if totals else None,
                "maximum": round(max(totals), 1) if totals else None,
            },
        }


def infer_symbol_format(symbol: str) -> str:
    s = str(symbol or "").strip()
    if not s:
        return "empty"
    if ":" in s and s.count(":") >= 5:
        return "colon_wire"
    if "--" in s or (s.count("-") >= 3 and any(ch.isdigit() for ch in s)):
        return "osi_dash"
    if " " in s and any(ch.isdigit() for ch in s):
        return "occ_spaced"
    if "_" in s:
        return "underscore_leg"
    return "other"


def mfe_decision_fields(
    *,
    trade_id: str,
    current_bid: Optional[float],
    entry_premium: Optional[float],
    hwm_bid: Optional[float],
    hwm_pnl_pct: Optional[float],
    giveback_exit_pct: Optional[float] = None,
    current_drawdown_pp: Optional[float] = None,
    quote_age: Optional[float] = None,
    quote_source: Optional[str] = None,
    confirmation_count: Optional[int] = None,
) -> Dict[str, Any]:
    """Shared payload for MFE_* / EXIT_* INFO logs and PO exit stamps."""
    entry = float(entry_premium or 0.0)
    hwm = float(hwm_bid or 0.0)
    hwm_pnl = float(hwm_pnl_pct or 0.0)
    dd_exit = float(giveback_exit_pct if giveback_exit_pct is not None else 0.10)
    clean_floor = None
    if entry > 0 and hwm_pnl > 0:
        # Clean floor = entry * (1 + hwm_pnl - giveback_exit_pp)
        clean_floor = entry * (1.0 + (hwm_pnl - dd_exit))
    elif hwm > 0:
        clean_floor = hwm * (1.0 - dd_exit)
    return {
        "trade_id": str(trade_id or ""),
        "current_bid": current_bid,
        "entry_premium": entry_premium,
        "hwm_bid": hwm_bid,
        "hwm_pnl_pct": hwm_pnl_pct,
        "clean_floor_bid": clean_floor,
        "current_drawdown_pp": current_drawdown_pp,
        "quote_age": quote_age,
        "quote_source": quote_source,
        "confirmation_count": confirmation_count,
    }


def format_mfe_decision_log(event: str, fields: Dict[str, Any]) -> str:
    """Stable pipe-delimited INFO line for exit-decision telemetry."""

    def _f(key: str, fmt: str = "%.6f") -> str:
        v = fields.get(key)
        if v is None:
            return "n/a"
        try:
            return fmt % float(v)
        except (TypeError, ValueError):
            return str(v)

    return (
        f"{event} | trade_id={fields.get('trade_id') or '-'} | "
        f"current_bid={_f('current_bid')} | entry_premium={_f('entry_premium')} | "
        f"hwm_bid={_f('hwm_bid')} | hwm_pnl_pct={_f('hwm_pnl_pct', '%.4f')} | "
        f"clean_floor_bid={_f('clean_floor_bid')} | "
        f"current_drawdown_pp={_f('current_drawdown_pp', '%.4f')} | "
        f"quote_age={_f('quote_age', '%.3f')} | quote_source={fields.get('quote_source') or 'n/a'} | "
        f"confirmation_count={fields.get('confirmation_count') if fields.get('confirmation_count') is not None else 'n/a'}"
    )


def log_mfe_decision(event: str, fields: Dict[str, Any], *, level: int = logging.INFO) -> None:
    log.log(level, "%s", format_mfe_decision_log(event, fields))


def build_po_exit_observation_stamp(
    *,
    position: Any = None,
    osnap: Optional[Dict[str, Any]] = None,
    exit_diagnostics: Optional[Dict[str, Any]] = None,
    exit_premium: Optional[float] = None,
    exit_reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Authoritative HWM / giveback / gap-guard fields for Priority Optimizer exit rows."""
    meta: Dict[str, Any] = {}
    if position is not None and isinstance(getattr(position, "metadata", None), dict):
        meta = dict(position.metadata or {})
    snap = osnap if isinstance(osnap, dict) else {}
    if not snap:
        stealth = meta.get("option_stealth")
        if isinstance(stealth, dict):
            snap = stealth
    diag = exit_diagnostics if isinstance(exit_diagnostics, dict) else {}
    if not diag:
        raw = snap.get("exit_diagnostics")
        if isinstance(raw, dict):
            diag = raw
    latch = meta.get("exit_latch") if isinstance(meta.get("exit_latch"), dict) else {}

    entry_prem = (
        diag.get("entry_premium")
        or snap.get("entry_premium")
        or meta.get("entry_premium")
        or getattr(position, "entry_price", None)
    )
    hwm_bid = (
        latch.get("exit_trigger_hwm_bid")
        or diag.get("premium_high_water_mark")
        or snap.get("premium_hwm")
        or snap.get("liquidatable_hwm_bid")
    )
    hwm_pnl = diag.get("max_pnl_pct")
    if hwm_pnl is None:
        hwm_pnl = snap.get("max_pnl_pct")
    decision_bid = (
        latch.get("exit_trigger_bid")
        or diag.get("option_bid_at_exit")
        or diag.get("effective_exit_premium")
        or exit_premium
    )
    fill_bid = exit_premium if exit_premium is not None else diag.get("exit_price")
    giveback_exit = snap.get("mfe_giveback_exit_pct")
    if giveback_exit is None:
        giveback_exit = 0.10
    fields = mfe_decision_fields(
        trade_id=str(
            getattr(position, "position_id", None)
            or diag.get("trade_id")
            or meta.get("trade_id")
            or ""
        ),
        current_bid=decision_bid,
        entry_premium=float(entry_prem) if entry_prem is not None else None,
        hwm_bid=float(hwm_bid) if hwm_bid is not None else None,
        hwm_pnl_pct=float(hwm_pnl) if hwm_pnl is not None else None,
        giveback_exit_pct=float(giveback_exit),
        current_drawdown_pp=diag.get("drawdown_from_premium_hwm_pct"),
        quote_age=(diag.get("data_quality") or {}).get("quote_age_seconds")
        if isinstance(diag.get("data_quality"), dict)
        else snap.get("quote_age_seconds"),
        quote_source=diag.get("premium_source_at_exit") or snap.get("last_premium_source"),
        confirmation_count=snap.get("mfe_giveback_gap_confirm_count"),
    )
    decision_to_fill = None
    try:
        if decision_bid is not None and fill_bid is not None:
            decision_to_fill = float(fill_bid) - float(decision_bid)
    except (TypeError, ValueError):
        decision_to_fill = None

    gap_state = "inactive"
    if snap.get("mfe_giveback_gap_pending"):
        gap_state = "pending"
    elif snap.get("mfe_giveback_gap_confirm_count"):
        gap_state = "confirmed_or_cleared"
    if snap.get("mfe_giveback_defer_timeout_forced"):
        gap_state = "defer_timeout_forced"

    quote_cont = "unknown"
    if meta.get("awaiting_market_data"):
        quote_cont = "gap_open"
    elif meta.get("exact_quote") or meta.get("exact_contract_quote"):
        quote_cont = "exact_present"
    reliability = meta.get("exact_quote_reliability")
    if isinstance(reliability, dict):
        if float(reliability.get("longest_continuous_gap_sec") or 0) > 0:
            quote_cont = "had_gaps"

    stamp = {
        "armed_at": snap.get("had_opportunity_armed_at") or snap.get("mfe_armed_at"),
        "hwm_timestamp": snap.get("hwm_timestamp") or snap.get("premium_hwm_at"),
        "hwm_bid": fields.get("hwm_bid"),
        "hwm_pnl_pct": fields.get("hwm_pnl_pct"),
        "giveback_floor_bid": fields.get("clean_floor_bid"),
        "exit_decision_bid": decision_bid,
        "exit_fill_bid": fill_bid,
        "decision_to_fill_slippage": decision_to_fill,
        "gap_guard_state": gap_state,
        "gap_guard_confirm_count": snap.get("mfe_giveback_gap_confirm_count"),
        "quote_continuity_status": quote_cont,
        "exit_reason": exit_reason or diag.get("exit_reason"),
        "peak_pct": fields.get("hwm_pnl_pct"),
        "peak_price": fields.get("hwm_bid"),
        "exact_quote_reliability": reliability if isinstance(reliability, dict) else None,
    }
    # Flatten core fields onto row-friendly keys as well.
    stamp.update({k: v for k, v in fields.items() if k not in stamp})
    try:
        from profit_optimizer.path_chain_skew import stamp_review_microstructure

        side = ""
        if position is not None:
            side = str(getattr(position, "option_side", None) or "")
        if not side:
            side = str(snap.get("option_side") or meta.get("option_side") or "")
        stamp_review_microstructure(
            stamp,
            option_side=side,
            overwrite=False,
            sources=(
                snap,
                meta.get("path_chain_skew") if isinstance(meta.get("path_chain_skew"), dict) else {},
                diag,
            ),
        )
    except Exception:
        pass
    return stamp


def persist_reliability_on_meta(meta: Dict[str, Any], stats: ExactQuoteReliabilityStats) -> None:
    if not isinstance(meta, dict):
        return
    meta["exact_quote_reliability"] = stats.summary()


def persist_cadence_on_meta(meta: Dict[str, Any], stats: ExitEvalCadenceStats) -> None:
    if not isinstance(meta, dict):
        return
    meta["exit_eval_cadence"] = stats.summary()
