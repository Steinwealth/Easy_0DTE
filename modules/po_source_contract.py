"""Where each Priority Optimizer family may come from, and how fresh it must be.

Entry hydration may copy a blank from one of these sources only when that
source was already in the process and its observation time is at or before
the decision freeze. A missing timestamp does not make a cache fresh. Values
the live decision already placed on the signal are the authority for that key.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

from .po_temporal_contract import (
    ENTRY_DECISION_FROZEN,
    ENTRY_FILL_IDENTITY,
    EXIT_OUTCOME,
    IMMUTABLE_IDENTITY,
    MOMENT_RESTAMP,
    PATH_HISTORY_ASOF_MOMENT,
    temporal_class,
)

# Coverage the pre-deploy verifier compares. Not a demand that every key be full.
REQUIRED = "REQUIRED"
WARMUP_DEPENDENT = "WARMUP_DEPENDENT"
PATH_DEPENDENT = "PATH_DEPENDENT"
OPTIONAL = "OPTIONAL"
NOT_APPLICABLE = "NOT_APPLICABLE"

SOURCE_NOT_AVAILABLE = "source_not_available"
HISTORY_NOT_WARM = "history_not_warm"
STALE_AT_FREEZE = "stale_at_freeze"
NOT_APPLICABLE_REASON = "not_applicable"
UPSTREAM_MISSING = "upstream_missing"
DECISION_PATH_DID_NOT_COLLECT = "decision_path_did_not_collect"
BROKER_BUDGET_PROTECTED = "broker_budget_protected"
TEMPORAL_REJECT_FUTURE = "temporal_reject_future_observation"
CLOCK_NOT_RECORDED = "clock_not_recorded"

# family -> preferred, fallback, max age seconds, broker fallback, coverage, clock field
FAMILY_POLICY: Dict[str, Dict[str, object]] = {
    "identity": {
        "preferred": "signal / candidate labels",
        "fallback": "none",
        "max_age_sec": None,
        "broker": False,
        "coverage": NOT_APPLICABLE,
        "clock": "po_decision_freeze_at",
    },
    "fill": {
        "preferred": "broker fill result",
        "fallback": "order payload",
        "max_age_sec": None,
        "broker": False,
        "coverage": REQUIRED,
        "clock": "po_snapshot_event_at",
    },
    "decision": {
        "preferred": "live QG / signal values already used to admit",
        "fallback": "candidate and break record",
        "max_age_sec": None,
        "broker": False,
        "coverage": REQUIRED,
        "clock": "po_decision_freeze_at",
    },
    "session_ta": {
        "preferred": "central session-tech hub, last closed 1-minute bar",
        "fallback": "values already on the signal",
        "max_age_sec": 90,
        "broker": False,
        "coverage": REQUIRED,
        "clock": "po_session_tech_asof_at",
    },
    "underlying_quote": {
        "preferred": "discovery / path quote cache",
        "fallback": "signal spot already used by the decision",
        "max_age_sec": 45,
        "broker": False,
        "coverage": REQUIRED,
        "clock": "po_quote_asof_at",
    },
    "spy": {
        "preferred": "SPY quote already in the quote cache",
        "fallback": "none; do not invent 0",
        "max_age_sec": 45,
        "broker": False,
        "coverage": REQUIRED,
        "clock": "po_spy_asof_at",
    },
    "chain": {
        "preferred": "Layer C / QG chain cache on the signal",
        "fallback": "in-process chain TTL",
        "max_age_sec": 300,
        "broker": True,
        "coverage": REQUIRED,
        "clock": "po_chain_asof_at",
    },
    "moon": {
        "preferred": "near-spot deltas from that same Layer C chain",
        "fallback": "none; do not copy representative_delta or fill_delta",
        "max_age_sec": 300,
        "broker": False,
        "coverage": REQUIRED,
        "clock": "po_chain_asof_at",
    },
    "greeks": {
        "preferred": "selected-contract greek on the Layer C chain",
        "fallback": "none from fill_delta",
        "max_age_sec": 300,
        "broker": False,
        "coverage": REQUIRED,
        "clock": "po_chain_asof_at",
    },
    "l1": {
        "preferred": "quote cache sizes or trajectory L1 ring",
        "fallback": "none",
        "max_age_sec": 15,
        "broker": False,
        "coverage": OPTIONAL,
        "clock": "po_quote_asof_at",
    },
    "trajectory": {
        "preferred": "closed 1-minute bars and the chain ring already sampled",
        "fallback": "none",
        "max_age_sec": 300,
        "broker": False,
        "coverage": WARMUP_DEPENDENT,
        "clock": "po_session_tech_asof_at",
    },
    "premium_path": {
        "preferred": "open-trade premium path, ticks at or before the event",
        "fallback": "current owned quote on the row",
        "max_age_sec": 15,
        "broker": False,
        "coverage": WARMUP_DEPENDENT,
        "clock": "po_premium_path_asof_at",
    },
    "exit": {
        "preferred": "exit decision stamp",
        "fallback": "exit fill economics on fill-class fields only",
        "max_age_sec": None,
        "broker": False,
        "coverage": NOT_APPLICABLE,
        "clock": "po_exit_event_at",
    },
}

_SESSION_TA = {
    "sma_20",
    "sma_50",
    "ema_12",
    "ema_26",
    "rsi",
    "rsi_14",
    "macd",
    "macd_signal",
    "macd_histogram",
    "prior_macd_histogram",
    "macd_above_zero",
    "macd_cross",
    "macd_hold_ok_call",
    "macd_exit_signal_call",
    "macd_hist_sign",
    "atr",
    "momentum_10",
    "bollinger_upper",
    "bollinger_middle",
    "bollinger_lower",
    "bollinger_width",
    "bollinger_position",
    "vwap",
    "vwap_distance_pct",
    "vwap_position",
    "volume_ratio",
    "orb_volume_ratio",
    "session_bar_count",
}
_QUOTE = {
    "underlying_price",
    "current_price",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "underlying_quote_age_sec",
}
_SPY = {"spy_price", "spy_change_pct", "rs_vs_spy"}
_MOON = {
    "representative_call_delta",
    "representative_put_delta",
    "representative_call_gamma",
    "representative_put_gamma",
}
_GREEKS = {
    "delta",
    "gamma",
    "theta",
    "vega",
    "implied_volatility",
    "iv",
    "representative_delta",
}
_L1 = {
    "underlying_bid_size",
    "underlying_ask_size",
    "underlying_l1_imbalance",
    "underlying_l1_imbalance_delta_5s",
    "underlying_l1_imbalance_delta_15s",
    "underlying_spread_pct",
    "option_bid_size",
    "option_ask_size",
    "option_l1_imbalance",
    "l1_delta_5s_with",
}


def family_for_key(key: str) -> str:
    cls = temporal_class(key)
    if cls == IMMUTABLE_IDENTITY:
        return "identity"
    if cls == ENTRY_FILL_IDENTITY:
        return "fill"
    if cls == EXIT_OUTCOME or key in ("exit_underlying", "hold_exit_dir_pct"):
        return "exit"
    if key in _MOON:
        return "moon"
    if key in _GREEKS:
        return "greeks"
    if key in _SPY:
        return "spy"
    if key in _SESSION_TA:
        return "session_ta"
    if key in _QUOTE:
        return "underlying_quote"
    if key in _L1:
        return "l1"
    if key == "underlying_volume_1m":
        return "trajectory"
    if cls == PATH_HISTORY_ASOF_MOMENT:
        if key.startswith("premium") or key.startswith("option_") or key.startswith("delta_"):
            return "premium_path"
        return "trajectory"
    if cls == MOMENT_RESTAMP:
        return "chain"
    if cls == ENTRY_DECISION_FROZEN:
        return "decision"
    return "decision"


def policy_for_key(key: str) -> Dict[str, object]:
    return FAMILY_POLICY[family_for_key(key)]


def coverage_for_key(key: str) -> str:
    family = family_for_key(key)
    if family == "trajectory":
        return WARMUP_DEPENDENT
    if family == "premium_path":
        return WARMUP_DEPENDENT
    if family == "l1":
        return OPTIONAL
    if family in ("exit",):
        return NOT_APPLICABLE
    if family == "identity":
        return REQUIRED
    if family == "decision" and key in (
        "convex_score",
        "convex_eligible",
        "convex_top_reason",
        "convex_all_reasons_short",
        "eligibility_score",
        "spread_width",
        "spread_type",
        "selector_reject_summary",
    ):
        return OPTIONAL
    if family == "decision" and key in (
        "line_price_at_break",
        "close_at_break",
        "structure_fit_mse",
        "break_archetype",
        "pressure_score",
        "body_ratio_vs_avg",
        "minutes_from_730_to_break",
    ):
        return PATH_DEPENDENT
    return str(FAMILY_POLICY[family]["coverage"])


def observation_is_usable(
    source_asof: Optional[str],
    event_at: Optional[str],
) -> Tuple[bool, str]:
    """True when the source time is known and not after the snapshot event."""
    if event_at in (None, ""):
        return False, CLOCK_NOT_RECORDED
    if source_asof in (None, ""):
        return False, CLOCK_NOT_RECORDED
    try:
        from datetime import datetime

        src = datetime.fromisoformat(str(source_asof).replace("Z", "+00:00"))
        evt = datetime.fromisoformat(str(event_at).replace("Z", "+00:00"))
    except Exception:
        return False, CLOCK_NOT_RECORDED
    if src > evt:
        return False, TEMPORAL_REJECT_FUTURE
    return True, ""


def observation_age_s(
    source_asof: Optional[str],
    event_at: Optional[str],
) -> Optional[float]:
    """Seconds the source clock is behind the event. None when either is bad."""
    if event_at in (None, "") or source_asof in (None, ""):
        return None
    try:
        from datetime import datetime

        src = datetime.fromisoformat(str(source_asof).replace("Z", "+00:00"))
        evt = datetime.fromisoformat(str(event_at).replace("Z", "+00:00"))
        return (evt - src).total_seconds()
    except Exception:
        return None


STALE_CHAIN_REASON = "stale_chain_over_60s"


def stale_chain_ineligibility(payload: Optional[dict]) -> Optional[dict]:
    """Oct 13 2026 (Eric, approved): a handoff/entry whose gate decision used a
    stale chain (age > the freeze cap, default 60 s) or was a stale defer is kept
    for audit but is not an Entry training row. Reads only the decision freeze
    already on the payload. Returns the audit fields, or None when fresh."""
    if not isinstance(payload, dict):
        return None
    fz = payload.get("po_decision_freeze")
    if not isinstance(fz, dict):
        return None
    ages = fz.get("ages_sec") if isinstance(fz.get("ages_sec"), dict) else {}
    caps = fz.get("max_age_sec") if isinstance(fz.get("max_age_sec"), dict) else {}
    try:
        age = float(ages.get("chain")) if ages.get("chain") is not None else None
    except (TypeError, ValueError):
        age = None
    if age is None:
        for key in ("chain_age_sec", "tp_chain_age_sec", "tp_age_chain_sec"):
            raw = fz.get(key)
            if raw is None:
                raw = payload.get(key)
            try:
                if raw is not None:
                    age = float(raw)
                    break
            except (TypeError, ValueError):
                continue
    try:
        cap = float(caps.get("chain") if caps.get("chain") is not None else 60.0)
    except (TypeError, ValueError):
        cap = 60.0
    reason = str(fz.get("reason") or "")
    # Chain staleness only: chain age over the cap, or a stale defer whose
    # stale keys are chain keys (quote/SPY-only stale defers are not this rule).
    stale = (age is not None and age > cap) or any(
        ":chain:" in str(k) for k in (fz.get("stale_gated") or [])
    )
    if not stale:
        return None
    asof = fz.get("asof") if isinstance(fz.get("asof"), dict) else {}
    return {
        "training_ineligible_reason": STALE_CHAIN_REASON,
        "chain_age_sec": age,
        "chain_asof": asof.get("chain") or fz.get("chain_asof") or payload.get("chain_asof"),
        "gate_reason": reason or None,
    }


def stamp_chain_age_fields(payload: Optional[dict]) -> None:
    """Always expose chain age on the snapshot row, not only when stale.

    Reviews look for ``chain_age_sec``. The freeze already has ``ages_sec.chain``;
    flatten it so 9/10+ artifacts can apply ``stale_chain_over_60s``.
    """
    if not isinstance(payload, dict):
        return
    fz = payload.get("po_decision_freeze")
    ages = fz.get("ages_sec") if isinstance(fz, dict) and isinstance(fz.get("ages_sec"), dict) else {}
    asof = fz.get("asof") if isinstance(fz, dict) and isinstance(fz.get("asof"), dict) else {}
    age = None
    for raw in (
        ages.get("chain"),
        fz.get("chain_age_sec") if isinstance(fz, dict) else None,
        payload.get("chain_age_sec"),
        payload.get("tp_chain_age_sec"),
        payload.get("tp_age_chain_sec"),
    ):
        try:
            if raw is not None:
                age = float(raw)
                break
        except (TypeError, ValueError):
            continue
    if age is not None:
        payload["chain_age_sec"] = age
        payload.setdefault("tp_chain_age_sec", age)
        payload.setdefault("tp_age_chain_sec", age)
    chain_asof = asof.get("chain") or (fz.get("chain_asof") if isinstance(fz, dict) else None)
    if chain_asof:
        payload.setdefault("chain_asof", chain_asof)


QUALITY_GATE_REJECT_REASON = "quality_gate_reject"


def _handoff_feature_row(payload: dict) -> Optional[dict]:
    for pool in (payload.get("features_selected"), payload.get("features_selected_dense")):
        if isinstance(pool, list) and pool and isinstance(pool[0], dict):
            return pool[0]
    return None


def freeze_admitted(payload: Optional[dict]) -> bool:
    """True when the Trade Profile freeze on this snapshot admitted the trade.

    An observe-stack quality_gate_report can say admitted false (delta, spread,
    liq) on that same handoff. The freeze is the decision that was scored.
    """
    if not isinstance(payload, dict):
        return False
    row = _handoff_feature_row(payload)
    for src in (payload, row):
        if not isinstance(src, dict):
            continue
        fz = src.get("po_decision_freeze")
        if isinstance(fz, dict) and fz.get("admitted") is True:
            return True
    return False


def quality_gate_reject_ineligibility(payload: Optional[dict]) -> Optional[dict]:
    """Handoff the quality gate blocked before an order.

    A Trade Profile freeze that admitted the trade stays an Entry training row.
    The observe stack's quality_gate_report often says admitted false on those
    same handoffs, and broker_success is still blank because the fill has not
    returned. That report is not a block. A row with no admitting freeze and a
    real quality-gate reject stays ineligible.
    """
    if not isinstance(payload, dict):
        return None
    if freeze_admitted(payload):
        return None
    extra = payload.get("extra") if isinstance(payload.get("extra"), dict) else {}
    row = _handoff_feature_row(payload)
    broker = extra.get("broker_success")
    if broker is None:
        broker = payload.get("broker_success")
    if broker is None and isinstance(row, dict):
        broker = row.get("broker_success")
    if broker is True:
        return None
    qg = None
    if isinstance(row, dict) and isinstance(row.get("quality_gate_report"), dict):
        qg = row.get("quality_gate_report")
    elif isinstance(payload.get("quality_gate_report"), dict):
        qg = payload.get("quality_gate_report")
    first = qg.get("first_reject") if isinstance(qg, dict) else None
    hard = payload.get("hard_gate_passed")
    if hard is None and isinstance(row, dict):
        hard = row.get("hard_gate_passed")
    rejected = bool(isinstance(qg, dict) and qg.get("admitted") is False and first) or hard is False
    if not rejected:
        return None
    gate_id = None
    reason = None
    if isinstance(first, dict):
        gate_id = first.get("gate_id")
        reason = first.get("reason")
    elif first not in (None, ""):
        reason = str(first)
    return {
        "training_ineligible_reason": QUALITY_GATE_REJECT_REASON,
        "qg_admitted": False if isinstance(qg, dict) else None,
        "hard_gate_passed": hard,
        "qg_reject_gate": gate_id,
        "qg_first_reject": reason,
    }


def mark_handoff_quality_gate_training(row: Optional[dict]) -> Optional[dict]:
    """Stamp a hydrated handoff row before persist. Returns the reject info."""
    if not isinstance(row, dict):
        return None
    info = quality_gate_reject_ineligibility(row)
    if info is None:
        return None
    row["po_entry_training_eligible"] = False
    for key, value in info.items():
        if row.get(key) in (None, ""):
            row[key] = value
    return info


def apply_training_ineligibility(payload: Optional[dict], info: Optional[dict]) -> None:
    if not isinstance(payload, dict) or not isinstance(info, dict):
        return
    payload["po_entry_training_eligible"] = False
    for key, value in info.items():
        if payload.get(key) in (None, ""):
            payload[key] = value
    row = _handoff_feature_row(payload)
    if not isinstance(row, dict):
        return
    row["po_entry_training_eligible"] = False
    for key, value in info.items():
        if row.get(key) in (None, ""):
            row[key] = value


def entry_training_eligible(payload: Optional[dict]) -> bool:
    """Executed rows that missed the handoff seal are not Entry training rows.

    A freeze-admitted handoff stays eligible even if an earlier stamp copied
    ``po_entry_training_eligible = false`` from an observe-stack reject.
    A stale decision chain still removes it.
    """
    if not isinstance(payload, dict):
        return False
    if str(payload.get("po_tech_asof") or "") == "executed_unsealed":
        return False
    if payload.get("po_handoff_link") == "missing":
        return False
    if stale_chain_ineligibility(payload) is not None:
        return False
    if freeze_admitted(payload):
        return True
    if payload.get("po_entry_training_eligible") is False:
        return False
    if quality_gate_reject_ineligibility(payload) is not None:
        return False
    return True


def clear_observe_stack_ineligibility(payload: Optional[dict]) -> None:
    """Drop an observe-stack reject label once the freeze admit is the record."""
    if not isinstance(payload, dict) or not freeze_admitted(payload):
        return
    if stale_chain_ineligibility(payload) is not None:
        return
    payload["po_entry_training_eligible"] = True
    if payload.get("training_ineligible_reason") == QUALITY_GATE_REJECT_REASON:
        payload.pop("training_ineligible_reason", None)
    row = _handoff_feature_row(payload)
    if not isinstance(row, dict):
        return
    row["po_entry_training_eligible"] = True
    if row.get("training_ineligible_reason") == QUALITY_GATE_REJECT_REASON:
        row.pop("training_ineligible_reason", None)
