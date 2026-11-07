#!/usr/bin/env python3
"""Extract contract identity and selector metadata from execution context (read-only)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from modules.canonical_contract_identity import build_canonical_identity_fields


def _f(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _first_leg(norm: Dict[str, Any]) -> Dict[str, Any]:
    legs = norm.get("legs")
    if isinstance(legs, list) and legs and isinstance(legs[0], dict):
        return legs[0]
    return {}


def _meta_occ_candidates(
    *,
    position: Any,
    norm: Dict[str, Any],
    exec_pl: Dict[str, Any],
    leg0: Dict[str, Any],
) -> List[str]:
    meta = getattr(position, "metadata", None)
    if not isinstance(meta, dict):
        meta = {}
    out: List[str] = []
    for raw in (
        meta.get("occ_symbol"),
        meta.get("broker_occ_symbol"),
        meta.get("option_symbol"),
        getattr(position, "occ_symbol", None),
        getattr(position, "option_symbol", None),
        exec_pl.get("occ_symbol"),
        exec_pl.get("contract_symbol"),
        exec_pl.get("option_symbol"),
        norm.get("occ_symbol"),
        leg0.get("occ_symbol"),
        leg0.get("broker_occ_symbol"),
        leg0.get("option_symbol"),
        # leg_id last — descriptive only; resolver skips it as primary.
        leg0.get("leg_id"),
    ):
        s = str(raw or "").strip()
        if s and s not in out:
            out.append(s)
    return out


def build_contract_identity(
    *,
    position: Any = None,
    norm: Optional[Dict[str, Any]] = None,
    exec_pl: Optional[Dict[str, Any]] = None,
    signal: Any = None,
) -> Dict[str, Any]:
    norm = norm if isinstance(norm, dict) else {}
    exec_pl = exec_pl if isinstance(exec_pl, dict) else {}
    leg0 = _first_leg(norm)
    und = str(
        norm.get("underlying_symbol")
        or getattr(position, "symbol", "")
        or exec_pl.get("symbol")
        or ""
    ).upper()
    opt_side = str(
        leg0.get("option_side")
        or leg0.get("option_type")
        or getattr(position, "option_side", "")
        or getattr(signal, "option_type", "")
        or ""
    ).lower()
    if opt_side.startswith("p"):
        opt_side = "put"
    elif opt_side.startswith("c"):
        opt_side = "call"
    strike = _f(leg0.get("strike") or getattr(position, "strike", None))
    expiration = str(
        norm.get("expiration_ymd")
        or getattr(position, "expiration", "")
        or exec_pl.get("expiration_ymd")
        or exec_pl.get("expiry_ymd")
        or leg0.get("expiration_ymd")
        or ""
    )
    display_label = str(leg0.get("leg_id") or "")
    candidates = _meta_occ_candidates(
        position=position, norm=norm, exec_pl=exec_pl, leg0=leg0
    )
    canonical = build_canonical_identity_fields(
        broker_occ_symbol=candidates[0] if candidates else "",
        osi_symbol=str(
            leg0.get("osi_symbol")
            or leg0.get("osiKey")
            or exec_pl.get("osi_symbol")
            or ""
        ),
        underlier=und,
        expiration=expiration,
        option_type=opt_side,
        strike=strike,
        display_label=display_label,
        broker_quote_request_symbol=str(
            exec_pl.get("broker_quote_request_symbol")
            or leg0.get("broker_quote_request_symbol")
            or ""
        ),
    )
    # If first candidate was descriptive, rebuild with full candidate list preference.
    if candidates:
        from modules.canonical_contract_identity import resolve_broker_occ_candidate

        broker_occ = resolve_broker_occ_candidate(
            *candidates,
            underlier=und,
            expiration=expiration,
            strike=strike,
            option_type=opt_side,
        )
        if broker_occ:
            canonical = build_canonical_identity_fields(
                broker_occ_symbol=broker_occ,
                osi_symbol=canonical.get("osi_symbol") or "",
                underlier=und,
                expiration=expiration,
                option_type=opt_side,
                strike=strike,
                display_label=display_label or canonical.get("display_label") or "",
                broker_quote_request_symbol=canonical.get("broker_quote_request_symbol") or "",
            )

    legs_out: List[Dict[str, Any]] = []
    raw_legs = norm.get("legs")
    if isinstance(raw_legs, list):
        for leg in raw_legs:
            if not isinstance(leg, dict):
                continue
            leg_side = str(
                leg.get("option_type")
                or leg.get("option_side")
                or getattr(position, "option_side", "")
                or opt_side
                or "call"
            ).lower()
            if leg_side.startswith("p"):
                leg_side = "put"
            elif leg_side.startswith("c"):
                leg_side = "call"
            leg_canon = build_canonical_identity_fields(
                broker_occ_symbol=str(
                    leg.get("occ_symbol")
                    or leg.get("broker_occ_symbol")
                    or leg.get("option_symbol")
                    or canonical.get("broker_occ_symbol")
                    or ""
                ),
                osi_symbol=str(leg.get("osi_symbol") or leg.get("osiKey") or ""),
                underlier=und,
                expiration=str(
                    leg.get("expiration_ymd") or norm.get("expiration_ymd") or expiration
                ),
                option_type=leg_side,
                strike=_f(leg.get("strike") or strike),
                display_label=str(leg.get("leg_id") or ""),
            )
            legs_out.append(
                {
                    **leg_canon,
                    "contract_id": str(leg.get("contract_id") or leg.get("leg_id") or ""),
                    "side": str(leg.get("long_or_short") or leg.get("side") or "long"),
                    "long_or_short": str(leg.get("long_or_short") or leg.get("side") or "long"),
                    "bid": _f(leg.get("bid")),
                    "ask": _f(leg.get("ask")),
                    "mid": _f(leg.get("mid") or leg.get("entry_price")),
                    "fill_price": _f(leg.get("entry_price") or leg.get("fill_price")),
                    "quantity": int(leg.get("quantity") or 0) or None,
                    "multiplier": int(leg.get("multiplier") or norm.get("multiplier") or 100),
                }
            )
    if not legs_out:
        legs_out.append(
            {
                **canonical,
                "contract_id": str(norm.get("trade_id") or getattr(position, "position_id", "") or ""),
                "side": "long",
                "long_or_short": "long",
                "multiplier": int(norm.get("multiplier") or 100),
            }
        )

    return {
        **canonical,
        "contract_id": str(norm.get("trade_id") or getattr(position, "position_id", "") or ""),
        "root_symbol": und,
        "underlying_symbol": und,
        "option_side": opt_side,
        "strategy_type": str(
            norm.get("orb_strategy_type")
            or norm.get("strategy_type")
            or getattr(position, "strategy_type", "")
            or ""
        ),
        "position_type": str(norm.get("position_type") or getattr(position, "position_type", "") or ""),
        "leg_count": len(legs_out),
        "legs": legs_out,
        "multiplier": int(norm.get("multiplier") or 100),
    }


def build_selector_metadata_0dte(
    *,
    signal: Any = None,
    norm: Optional[Dict[str, Any]] = None,
    pos_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    meta = {}
    if signal is not None:
        meta = getattr(signal, "metadata", None) if not isinstance(getattr(signal, "metadata", None), dict) else signal.metadata
    if not isinstance(meta, dict):
        meta = {}
    if isinstance(pos_meta, dict):
        snap = pos_meta.get("signal_snapshot")
        if isinstance(snap, dict):
            meta = {**snap, **meta}
    return {
        "priority_rank": _int_or_none(getattr(signal, "priority_rank", None) or meta.get("priority_rank")),
        "priority_score": _f(getattr(signal, "priority_score", None) or meta.get("priority_score")),
        "convex_score": _f(getattr(signal, "convex_score", None) or meta.get("convex_score")),
        "target_delta": _f(
            getattr(signal, "target_delta", None)
            or meta.get("target_delta")
            or (norm or {}).get("target_delta")
        ),
        "fill_delta": _f(
            meta.get("fill_delta")
            or meta.get("delta_at_entry")
            or meta.get("delta")
            or (norm or {}).get("fill_delta")
            or (norm or {}).get("delta_at_entry")
        ),
        "hard_gate_passed": _bool_or_none(meta.get("hard_gate_passed")),
        "pipeline_decision_stage": str(meta.get("pipeline_decision_stage") or meta.get("stage") or ""),
        "pipeline_decision_reason": str(meta.get("pipeline_decision_reason") or meta.get("reject_reason") or "")[:200],
        "selector_rank": _int_or_none(meta.get("selector_rank")),
        "selector_score": _f(meta.get("selector_score")),
        "strategy_type": str(
            (norm or {}).get("orb_strategy_type")
            or (norm or {}).get("strategy_type")
            or getattr(signal, "strategy_type", "")
            or ""
        ),
        "entry_archetype": str(meta.get("entry_archetype") or meta.get("break_archetype") or "opening_impulse"),
    }


def build_selector_metadata_so(*, signal: Any = None, market_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    md = market_data if isinstance(market_data, dict) else {}
    sig = signal if signal is not None else {}
    if hasattr(sig, "metadata") and isinstance(sig.metadata, dict):
        md = {**sig.metadata, **md}
    if isinstance(sig, dict):
        md = {**sig, **md}
    return {
        "priority_rank": _int_or_none(md.get("priority_rank") or getattr(sig, "priority_rank", None)),
        "priority_score": _f(md.get("priority_score") or getattr(sig, "priority_score", None)),
        "rsi": _f(md.get("rsi")),
        "vwap_distance_pct": _f(md.get("vwap_distance_pct")),
        "rs_vs_spy": _f(md.get("rs_vs_spy")),
        "orb_volume_ratio": _f(md.get("orb_volume_ratio") or md.get("volume_ratio")),
        "volume_ratio_missing": _bool_or_none(md.get("volume_ratio_missing")),
        "vwap_missing": _bool_or_none(md.get("vwap_missing")),
        "rsi_missing": _bool_or_none(md.get("rsi_missing")),
        "macd_missing": _bool_or_none(md.get("macd_missing")),
        "volume_ratio_source": _str_or_none(md.get("volume_ratio_source") or md.get("tech_source")),
        "vwap_source": _str_or_none(md.get("vwap_source") or md.get("tech_source")),
        "tech_source": _str_or_none(md.get("tech_source")),
        "session_bar_count": _int_or_none(md.get("session_bar_count")),
        "orb_range_pct": _f(md.get("orb_range_pct")),
        "strategy_type": "orb_so",
        "entry_archetype": str(md.get("entry_archetype") or "orb_so"),
    }


def _int_or_none(x: Any) -> Optional[int]:
    try:
        if x is None:
            return None
        return int(x)
    except (TypeError, ValueError):
        return None


def _bool_or_none(x: Any) -> Optional[bool]:
    if x is None:
        return None
    if isinstance(x, bool):
        return x
    return str(x).strip().lower() in ("1", "true", "yes", "on")


def _str_or_none(x: Any) -> Optional[str]:
    s = str(x or "").strip()
    return s or None
