#!/usr/bin/env python3
"""Shadow-path contract continuity, exact-quote gates, and sanity quarantine."""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

from profit_optimizer.audit import classify_premium_source_bucket
from profit_optimizer.peak_quote import is_trusted_peak_quote, is_trusted_shadow_peak_quote


def live_hwm_jump_ratio_threshold() -> float:
    """Reject single-tick live HWM updates above this ratio vs last mark (phantom quote guard)."""
    try:
        from modules.config_loader import get_config_value

        raw = get_config_value(
            "PROFIT_OPTIMIZER_LIVE_HWM_JUMP_RATIO",
            get_config_value("PROFIT_OPTIMIZER_SHADOW_FIRST_JUMP_RATIO", "3.0"),
        )
        return max(1.5, float(str(raw).strip()))
    except Exception:
        return 3.0


def shadow_sample_interval_sec() -> float:
    """Shadow leftover samples on the same cadence as open-position monitoring.

    Exit-tuning leftover must line up with live ``premium_path`` (2s). Dedicated
    keys override; otherwise inherit ``0DTE_POSITION_MONITOR_INTERVAL_SEC``.
    """
    try:
        from modules.config_loader import get_config_value

        raw = get_config_value(
            "PROFIT_OPTIMIZER_SHADOW_SAMPLE_SEC",
            get_config_value("PROFIT_OPTIMIZER_SHADOW_SAMPLE_SECONDS", ""),
        )
        text = str(raw or "").strip()
        if text:
            return max(0.5, float(text))
    except Exception:
        pass
    try:
        from modules.dte0_exit_monitor_config import resolve_0dte_position_monitor_interval_sec

        return max(0.5, float(resolve_0dte_position_monitor_interval_sec()))
    except Exception:
        return 2.0


def first_shadow_jump_ratio_threshold() -> float:
    try:
        from modules.config_loader import get_config_value

        raw = get_config_value("PROFIT_OPTIMIZER_SHADOW_FIRST_JUMP_RATIO", "3.0")
        return max(1.5, float(str(raw).strip()))
    except Exception:
        return 3.0


def freeze_contract_identity(
    contract_identity: Optional[Dict[str, Any]],
    *,
    entry: Optional[Any] = None,
) -> Dict[str, Any]:
    """Deep-freeze contract identity at TRACK_START for shadow lookup.

    Canonical broker OCC fields are immutable after this call. Descriptive
    ``display_label`` may remain for storage/UI but is never the quote key.
    """
    from modules.canonical_contract_identity import build_canonical_identity_fields

    src = dict(contract_identity or {})
    frozen = copy.deepcopy(src)
    und = str(
        frozen.get("underlier")
        or frozen.get("underlying_symbol")
        or frozen.get("root_symbol")
        or (getattr(entry, "symbol", "") if entry is not None else "")
        or ""
    ).upper()
    opt_side = str(
        frozen.get("option_type") or frozen.get("option_side") or "call"
    ).lower()
    if opt_side.startswith("p"):
        opt_side = "put"
    elif opt_side.startswith("c"):
        opt_side = "call"
    strike = frozen.get("strike")
    if strike is None and entry is not None:
        strike = getattr(entry, "strike", None)
    expiration = str(
        frozen.get("expiration")
        or (getattr(entry, "expiration", "") if entry is not None else "")
        or ""
    )
    canon = build_canonical_identity_fields(
        broker_occ_symbol=str(
            frozen.get("broker_occ_symbol")
            or frozen.get("normalized_occ_symbol")
            or frozen.get("option_symbol")
            or ""
        ),
        osi_symbol=str(frozen.get("osi_symbol") or ""),
        underlier=und,
        expiration=expiration,
        option_type=opt_side,
        strike=strike,
        display_label=str(frozen.get("display_label") or ""),
        broker_quote_request_symbol=str(frozen.get("broker_quote_request_symbol") or ""),
    )
    frozen.update(canon)
    frozen["underlier"] = canon["underlier"]
    frozen["underlying_symbol"] = canon["underlier"]
    frozen["root_symbol"] = canon["underlier"]
    frozen["option_side"] = canon["option_type"]
    frozen["option_type"] = canon["option_type"]

    legs_in = frozen.get("legs") if isinstance(frozen.get("legs"), list) else []
    legs_out: List[Dict[str, Any]] = []
    for leg in legs_in:
        if not isinstance(leg, dict):
            continue
        leg_opt = str(
            leg.get("option_type")
            or leg.get("side")
            or frozen.get("option_side")
            or frozen.get("option_type")
            or "call"
        ).lower()
        if leg_opt.startswith("p"):
            leg_opt = "put"
        elif leg_opt.startswith("c"):
            leg_opt = "call"
        leg_canon = build_canonical_identity_fields(
            broker_occ_symbol=str(
                leg.get("broker_occ_symbol")
                or leg.get("normalized_occ_symbol")
                or leg.get("option_symbol")
                or canon.get("broker_occ_symbol")
                or ""
            ),
            osi_symbol=str(leg.get("osi_symbol") or canon.get("osi_symbol") or ""),
            underlier=und,
            expiration=str(
                leg.get("expiration")
                or leg.get("expiration_ymd")
                or frozen.get("expiration")
                or ""
            ),
            option_type=leg_opt,
            strike=leg.get("strike") if leg.get("strike") is not None else strike,
            display_label=str(leg.get("display_label") or leg.get("leg_id") or ""),
            broker_quote_request_symbol=str(
                leg.get("broker_quote_request_symbol")
                or canon.get("broker_quote_request_symbol")
                or ""
            ),
        )
        legs_out.append(
            {
                **leg_canon,
                "contract_id": str(leg.get("contract_id") or leg.get("leg_id") or ""),
                "side": leg_opt,
                "long_or_short": str(leg.get("long_or_short") or leg.get("side") or "long").lower(),
                "multiplier": int(leg.get("multiplier") or frozen.get("multiplier") or 100),
            }
        )
    if not legs_out:
        legs_out.append(
            {
                **canon,
                "contract_id": str(frozen.get("contract_id") or ""),
                "side": opt_side,
                "long_or_short": "long",
                "multiplier": int(frozen.get("multiplier") or 100),
            }
        )
    frozen["legs"] = legs_out
    if not frozen.get("contract_id") and legs_out:
        frozen["contract_id"] = str(legs_out[0].get("contract_id") or "")
    frozen["contract_identity_frozen"] = True
    frozen["multiplier"] = int(frozen.get("multiplier") or 100)
    return frozen


def initial_path_audit_from_frozen(frozen: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "contract_identity_frozen": True,
        "entry_option_symbol": str(
            frozen.get("broker_occ_symbol") or frozen.get("option_symbol") or ""
        ),
        "broker_occ_symbol": str(frozen.get("broker_occ_symbol") or ""),
        "normalized_occ_symbol": str(frozen.get("normalized_occ_symbol") or ""),
        "broker_quote_request_symbol": str(frozen.get("broker_quote_request_symbol") or ""),
        "display_label": str(frozen.get("display_label") or ""),
        "shadow_option_symbol": "",
        "contract_identity_match": None,
        "shadow_quote_exact": None,
        "shadow_path_trusted": True,
        "shadow_quarantine_reason": "",
        "first_shadow_premium": None,
        "last_live_premium": None,
        "first_shadow_jump_ratio": None,
        "shadow_gap_count": 0,
        "shadow_gaps": [],
        "shadow_gap_intervals": [],
    }


def last_live_premium_from_path(premium_path: List[Dict[str, Any]]) -> Optional[float]:
    last: Optional[float] = None
    for row in premium_path:
        if str(row.get("path_phase") or "") == "shadow":
            continue
        if str(row.get("sample_type") or "") == "gap":
            continue
        for key in ("mark", "mid", "bid", "ask"):
            v = row.get(key)
            if v is not None and float(v) > 0:
                last = float(v)
    return last


def resolve_last_live_for_shadow(
    *,
    path_audit: Dict[str, Any],
    premium_path: List[Dict[str, Any]],
    exit_premium: Optional[float],
    peak_quote_metadata: Optional[Dict[str, Any]] = None,
) -> Optional[float]:
    """
    Anchor for first-shadow jump check. Uses exit premium when live path ends on
    an untrusted or implausible peak (phantom HWM before exit).
    """
    last = last_live_premium_from_path(premium_path)
    if last is None and exit_premium is not None:
        return float(exit_premium)
    peak_meta = peak_quote_metadata if isinstance(peak_quote_metadata, dict) else {}
    peak_trusted = peak_meta.get("peak_trusted")
    thr = first_shadow_jump_ratio_threshold()
    if exit_premium is not None and last is not None and float(last) > 0:
        exit_f = float(exit_premium)
        ratio = float(last) / exit_f if exit_f > 0 else 0.0
        if peak_trusted is False or ratio > thr:
            return exit_f
    return last


def is_exact_shadow_quote_fields(fields: Dict[str, Any]) -> bool:
    """Legacy name: recordable research quote (exact or matched live chain)."""
    src = str(fields.get("premium_source") or fields.get("quote_source") or "")
    mq = str(fields.get("mark_quality") or fields.get("quote_quality") or "").lower()
    src_l = src.lower()
    if "cached" in src_l or "cached" in mq or "stale" in src_l or "stale" in mq:
        return False
    try:
        from modules.monitor_premium_snapshot import is_recordable_research_quote

        if is_recordable_research_quote(fields):
            return True
    except Exception:
        pass
    # Strict exact-only fallback for older callers.
    if not bool(fields.get("shadow_quote_exact")):
        return False
    bucket = classify_premium_source_bucket(src)
    if bucket != "exact":
        return False
    mark = fields.get("mark")
    bid = fields.get("bid")
    ask = fields.get("ask")
    if mark is None or float(mark) <= 0:
        return False
    if bid is not None and ask is not None:
        if float(bid) <= 0 or float(ask) <= 0:
            return False
    return True


def evaluate_first_shadow_jump(
    last_live: Optional[float],
    first_shadow: Optional[float],
    *,
    threshold: Optional[float] = None,
) -> Tuple[bool, Optional[float]]:
    """Return (ok, ratio). ratio = first_shadow / last_live when last_live > 0."""
    if last_live is None or last_live <= 0 or first_shadow is None or first_shadow <= 0:
        return True, None
    thr = float(threshold if threshold is not None else first_shadow_jump_ratio_threshold())
    ratio = float(first_shadow) / float(last_live)
    if ratio > thr:
        return False, ratio
    if last_live > 0 and float(first_shadow) < float(last_live) / thr:
        return False, ratio
    return True, ratio


def quarantine_shadow_path(
    path_audit: Dict[str, Any],
    reason: str,
    *,
    first_shadow_premium: Optional[float] = None,
    jump_ratio: Optional[float] = None,
) -> Dict[str, Any]:
    pa = dict(path_audit)
    pa["shadow_path_trusted"] = False
    pa["shadow_quarantine_reason"] = str(reason or "quarantine")[:120]
    pa["shadow_quote_exact"] = False
    if first_shadow_premium is not None:
        pa["first_shadow_premium"] = first_shadow_premium
    if jump_ratio is not None:
        pa["first_shadow_jump_ratio"] = jump_ratio
    return pa


def shadow_peak_trusted_from_sample(sample: Dict[str, Any], path_audit: Dict[str, Any]) -> bool:
    if path_audit.get("shadow_path_trusted") is False:
        return False
    return is_trusted_shadow_peak_quote(
        premium_source=str(sample.get("premium_source") or sample.get("quote_source") or ""),
        quote_quality=str(sample.get("quote_quality") or ""),
        mark_quality=str(sample.get("mark_quality") or ""),
        mark_is_exit_grade=bool(sample.get("mark_is_exit_grade")),
        shadow_quote_exact=bool(sample.get("shadow_quote_exact")),
        path_phase=str(sample.get("path_phase") or ""),
        shadow_trust=str(sample.get("shadow_trust") or path_audit.get("shadow_trust") or ""),
    )


def apply_peak_trust_to_metadata(
    peak_meta: Dict[str, Any],
    *,
    path_phase: str,
    path_audit: Dict[str, Any],
    sample: Dict[str, Any],
) -> Dict[str, Any]:
    meta = dict(peak_meta)
    if path_phase == "shadow":
        meta["peak_trusted"] = shadow_peak_trusted_from_sample(sample, path_audit)
    else:
        meta["peak_trusted"] = is_trusted_peak_quote(
            premium_source=str(sample.get("premium_source") or ""),
            quote_quality=str(sample.get("quote_quality") or ""),
            mark_quality=str(sample.get("mark_quality") or ""),
            mark_is_exit_grade=bool(sample.get("mark_is_exit_grade")),
        )
    meta["peak_quote_quality"] = str(sample.get("quote_quality") or sample.get("mark_quality") or "")
    meta["peak_premium_source"] = str(sample.get("premium_source") or "")
    meta["peak_mark_quality"] = str(sample.get("mark_quality") or "")
    return meta


def enrich_path_audit_export(path_audit: Dict[str, Any]) -> Dict[str, Any]:
    """Ensure shadow diagnostics are present on export."""
    pa = dict(path_audit or {})
    if "shadow_path_trusted" not in pa:
        pa["shadow_path_trusted"] = True
    if pa.get("shadow_path_trusted") is False:
        pa["shadow_quote_exact"] = False
    return pa
