#!/usr/bin/env python3
"""Flatten trade flight records for daily JSON / cohort review."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from profit_optimizer.path_analysis import analyze_trade_path
from profit_optimizer.path_classifier import enrich_trade_path_class, path_class_for_trade


def _f(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _peak_mark_from_path(samples: List[Dict[str, Any]], phase: str) -> Optional[float]:
    peak: Optional[float] = None
    for row in samples:
        row_phase = str(row.get("path_phase") or "")
        if phase == "live" and row_phase == "shadow":
            continue
        if phase == "shadow" and row_phase != "shadow":
            continue
        if str(row.get("sample_type") or "") == "gap":
            continue
        for key in ("mark", "mid", "bid", "ask"):
            v = _f(row.get(key))
            if v is not None and v > 0:
                if peak is None or v > peak:
                    peak = v
                break
    return peak


def enrich_trade_export_dict(trade: Dict[str, Any]) -> Dict[str, Any]:
    """Add top-level review fields required for 0DTE Profit Capture research."""
    from profit_optimizer.app_identity import stamp_trade_dict, trade_app_label

    trade = stamp_trade_dict(trade)
    entry = trade.get("entry") if isinstance(trade.get("entry"), dict) else {}
    exit_obs = trade.get("exit_observation") if isinstance(trade.get("exit_observation"), dict) else {}
    pm = trade.get("premium_metrics") if isinstance(trade.get("premium_metrics"), dict) else {}
    pm_exit = (
        trade.get("premium_metrics_at_exit")
        if isinstance(trade.get("premium_metrics_at_exit"), dict)
        else {}
    )
    pp = trade.get("premium_path") if isinstance(trade.get("premium_path"), list) else []
    contract = entry.get("contract_identity") if isinstance(entry.get("contract_identity"), dict) else {}
    legs = contract.get("legs") if isinstance(contract.get("legs"), list) else []
    option_side = ""
    if legs and isinstance(legs[0], dict):
        option_side = str(legs[0].get("option_type") or legs[0].get("side") or "")
    if not option_side:
        option_side = str((entry.get("extra") or {}).get("option_type") or "")

    pa = trade.get("path_audit") if isinstance(trade.get("path_audit"), dict) else {}
    peak_meta = trade.get("peak_quote_metadata") if isinstance(trade.get("peak_quote_metadata"), dict) else {}

    peak_live = _peak_mark_from_path(pp, "live")
    peak_shadow = _peak_mark_from_path(pp, "shadow")
    if peak_live is None:
        peak_live = _f(
            pm_exit.get("peak_premium")
            or pm_exit.get("premium_hwm")
            or pm_exit.get("premium_at_peak")
        )
    if peak_shadow is None and pa.get("shadow_path_trusted") is not False:
        peak_shadow = _f(pm.get("peak_premium"))

    out = dict(trade)
    path_class = path_class_for_trade(trade)
    path_fields = analyze_trade_path(trade)
    app_label = trade_app_label(out) or str(out.get("source_app") or "")
    out.update(
        {
            "source_app": app_label or out.get("source_app"),
            "app": app_label or out.get("app"),
            "gcs_namespace": out.get("gcs_namespace") or entry.get("gcs_namespace"),
            "trade_id": str(entry.get("trade_id") or ""),
            "symbol": str(entry.get("symbol") or ""),
            "strategy_path": str(entry.get("strategy_path") or ""),
            "path_class": path_class,
            "option_side": option_side,
            "strike": entry.get("strike") or contract.get("strike"),
            "expiration": str(entry.get("expiration") or contract.get("expiration") or ""),
            "session_date": entry.get("session_date"),
            "expiry_ymd": entry.get("expiry_ymd"),
            "calendar_dte": entry.get("calendar_dte"),
            "is_0dte": entry.get("is_0dte"),
            "expiry_selection_mode": entry.get("expiry_selection_mode"),
            "requested_expiry_ymd": entry.get("requested_expiry_ymd"),
            "selected_expiry_ymd": entry.get("selected_expiry_ymd"),
            "fallback_days": entry.get("fallback_days"),
            "fallback_reason": entry.get("fallback_reason"),
            "entry_time": entry.get("entry_time"),
            "entry_premium": entry.get("entry_premium"),
            "entry_underlying": entry.get("underlying_entry_price"),
            "peak_live_premium": peak_live,
            "peak_shadow_premium": peak_shadow,
            "premium_at_eod": pm.get("premium_at_eod"),
            "capture_vs_session_peak_at_exit": pm.get("capture_vs_session_peak_at_exit"),
            "left_on_table_at_exit_pct": pm.get("left_on_table_at_exit_pct"),
            "contract_identity_frozen": bool(
                contract.get("contract_identity_frozen") or pa.get("contract_identity_frozen")
            ),
            "entry_option_symbol": str(
                pa.get("entry_option_symbol") or contract.get("option_symbol") or ""
            ),
            "shadow_option_symbol": str(pa.get("shadow_option_symbol") or ""),
            "contract_identity_match": pa.get("contract_identity_match"),
            "shadow_quote_exact": pa.get("shadow_quote_exact"),
            "shadow_path_trusted": pa.get("shadow_path_trusted"),
            "shadow_quarantine_reason": str(pa.get("shadow_quarantine_reason") or ""),
            "first_shadow_premium": pa.get("first_shadow_premium"),
            "last_live_premium": pa.get("last_live_premium"),
            "first_shadow_jump_ratio": pa.get("first_shadow_jump_ratio"),
            "peak_quote_quality": str(
                peak_meta.get("peak_quote_quality") or peak_meta.get("peak_mark_quality") or ""
            ),
            "peak_trusted": peak_meta.get("peak_trusted"),
            "path_audit": pa,
            **path_fields,
        }
    )
    # Promote nested exit/MFE AFTER path_fields so null path keys cannot blank real exits.
    exit_prem = _f(exit_obs.get("exit_premium"))
    if exit_prem is None:
        exit_prem = _f(out.get("exit_premium"))
    out["exit_time"] = exit_obs.get("exit_time") or out.get("exit_time")
    out["exit_reason"] = exit_obs.get("exit_reason") or out.get("exit_reason")
    out["exit_premium"] = exit_prem
    out["exit_underlying"] = _f(exit_obs.get("exit_underlying")) or out.get("exit_underlying")
    out["realized_pnl_pct"] = _f(exit_obs.get("realized_pnl_pct")) or out.get("realized_pnl_pct")
    mfe_at_exit = _f(pm_exit.get("premium_mfe_pct"))
    if mfe_at_exit is None:
        mfe_at_exit = _f(exit_obs.get("premium_mfe_pct"))
    if mfe_at_exit is None:
        mfe_at_exit = _f(out.get("mfe_pct_at_exit"))
    out["mfe_pct_at_exit"] = mfe_at_exit
    mae_at_exit = _f(pm_exit.get("premium_mae_pct"))
    if mae_at_exit is None:
        mae_at_exit = _f(exit_obs.get("premium_mae_pct"))
    if mae_at_exit is None:
        mae_at_exit = _f(out.get("mae_pct_at_exit"))
    out["mae_pct_at_exit"] = mae_at_exit
    if out.get("peak_shadow_premium") is None:
        out["peak_shadow_premium"] = peak_shadow
    if out.get("peak_live_premium") is None:
        out["peak_live_premium"] = peak_live
    return enrich_trade_path_class(out)
