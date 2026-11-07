#!/usr/bin/env python3
"""Daily cohort CSV export for profit_optimizer."""

from __future__ import annotations

import csv
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from profit_optimizer.path_analysis import analyze_trade_path
from profit_optimizer.path_classifier import STRATEGY_PATHS, path_class_for_trade
from profit_optimizer.profit_optimizer_storage import analytics_dir

log = logging.getLogger(__name__)

COHORT_COLUMNS = [
    "date",
    "source_app",
    "app",
    "gcs_namespace",
    "trade_id",
    "symbol",
    "path_class",
    "path",
    "direction",
    "strategy_type",
    "option_symbol",
    "expiration",
    "session_date",
    "expiry_ymd",
    "calendar_dte",
    "is_0dte",
    "expiry_selection_mode",
    "requested_expiry_ymd",
    "selected_expiry_ymd",
    "fallback_days",
    "fallback_reason",
    "strike",
    "option_side",
    "entry_time",
    "exit_time",
    "entry_premium",
    "exit_premium",
    "peak_premium",
    "low_premium",
    "delta",
    "target_delta",
    "mfe_pct",
    "mae_pct",
    "mfe_pct_at_exit",
    "mae_pct_at_exit",
    "realized_pnl_pct",
    "pre_mfe_mae_pct",
    "path_pre_mfe_mae_pct",
    "mae_before_peak_pct",
    "mae_before_5pct_mfe_pct",
    "time_to_5pct_mfe_sec",
    "time_to_10pct_mfe_sec",
    "time_to_20pct_mfe_sec",
    "time_to_30pct_mfe_sec",
    "mfe_5pct_bypass_hit",
    "capture_vs_session_peak_at_exit",
    "left_on_table_at_exit_pct",
    "peak_while_open_pct",
    "post_exit_peak_pct",
    "post_exit_eod_pct",
    "full_session_peak_pct",
    "while_open_upside_pts",
    "post_exit_upside_pts",
    "full_session_upside_pts",
    "post_exit_left_on_table_pct",
    "capture_vs_full_session_peak",
    "post_exit_continued_higher",
    "post_exit_exceeded_open_peak",
    "post_exit_shadow_trusted",
    "post_exit_shadow_quote_count",
    "post_exit_shadow_gap_count",
    "live_tick_count",
    "shadow_tick_count",
    "path_quality_sparse",
    "capture_pct",
    "capture_pct_at_exit",
    "capture_pct_at_eod",
    "premium_at_eod",
    "capture_of_net_opportunity_pct",
    "missed_premium_pct",
    "time_to_mfe_sec",
    "time_to_mae_sec",
    "theta_decay_5m_after_peak_pct",
    "theta_decay_15m_after_peak_pct",
    "theta_decay_30m_after_peak_pct",
    "priority_rank",
    "priority_score",
    "convex_score",
    "selector_score",
    "entry_spread_pct",
    "entry_slippage_vs_mid_pct",
    "peak_quote_quality",
    "peak_premium_source",
    "peak_trusted",
    "shadow_path_trusted",
    "shadow_quarantine_reason",
    "shadow_quote_exact",
    "contract_identity_match",
    "first_shadow_jump_ratio",
    "no_progress_exit_shadow",
    "shadow_stop_reason",
    "exit_reason",
]


def cohort_file_path(session_date: str, base_dir: Optional[Path] = None) -> Path:
    root = (base_dir or analytics_dir()) / "cohorts"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{session_date}_profit_optimizer_cohort.csv"


def _row_from_trade(trade: Dict[str, Any], session_date: str) -> Dict[str, Any]:
    from profit_optimizer.app_identity import trade_app_label

    entry = trade.get("entry") if isinstance(trade.get("entry"), dict) else {}
    pm = trade.get("premium_metrics") if isinstance(trade.get("premium_metrics"), dict) else {}
    pm_exit = (
        trade.get("premium_metrics_at_exit")
        if isinstance(trade.get("premium_metrics_at_exit"), dict)
        else {}
    )
    exit_obs = trade.get("exit_observation") if isinstance(trade.get("exit_observation"), dict) else {}
    peak = trade.get("peak_quote_metadata") if isinstance(trade.get("peak_quote_metadata"), dict) else {}
    pa = trade.get("path_audit") if isinstance(trade.get("path_audit"), dict) else {}
    contract = entry.get("contract_identity") if isinstance(entry.get("contract_identity"), dict) else {}
    selector = entry.get("selector_metadata") if isinstance(entry.get("selector_metadata"), dict) else {}
    eq = entry.get("entry_execution_quality") if isinstance(entry.get("entry_execution_quality"), dict) else {}
    extra = entry.get("extra") if isinstance(entry.get("extra"), dict) else {}
    app_label = trade_app_label(trade) or str(trade.get("source_app") or entry.get("source_app") or "")

    def _theta_offset_pct(minutes: int) -> Optional[float]:
        peak_p = pm.get("premium_at_peak") or pm.get("peak_premium")
        key = f"premium_{minutes}m_after_peak"
        if minutes == 5:
            key = "premium_5m_after_peak"
        elif minutes == 15:
            key = "premium_15m_after_peak"
        elif minutes == 30:
            key = "premium_30m_after_peak"
        after = pm.get(key)
        if peak_p and after and float(peak_p) > 0:
            return (float(peak_p) - float(after)) / float(peak_p)
        return pm.get(f"theta_decay_{minutes}m_after_peak_pct")

    path_fields = analyze_trade_path(trade)

    return {
        "date": session_date,
        "source_app": app_label,
        "app": app_label or trade.get("app") or entry.get("app"),
        "gcs_namespace": trade.get("gcs_namespace") or entry.get("gcs_namespace") or "",
        "trade_id": entry.get("trade_id", ""),
        "symbol": entry.get("symbol", ""),
        "path_class": path_class_for_trade(trade),
        "path": entry.get("strategy_path", ""),
        "direction": entry.get("direction", ""),
        "strategy_type": contract.get("strategy_type") or selector.get("strategy_type", ""),
        "option_symbol": contract.get("option_symbol", ""),
        "expiration": contract.get("expiration") or entry.get("expiration", ""),
        "session_date": entry.get("session_date") or selector.get("session_date") or extra.get("session_date"),
        "expiry_ymd": entry.get("expiry_ymd") or selector.get("expiry_ymd") or extra.get("expiry_ymd"),
        "calendar_dte": entry.get("calendar_dte")
        if entry.get("calendar_dte") is not None
        else selector.get("calendar_dte")
        if selector.get("calendar_dte") is not None
        else extra.get("calendar_dte"),
        "is_0dte": entry.get("is_0dte")
        if entry.get("is_0dte") is not None
        else selector.get("is_0dte")
        if selector.get("is_0dte") is not None
        else extra.get("is_0dte"),
        "expiry_selection_mode": entry.get("expiry_selection_mode")
        or selector.get("expiry_selection_mode")
        or extra.get("expiry_selection_mode"),
        "requested_expiry_ymd": entry.get("requested_expiry_ymd")
        or selector.get("requested_expiry_ymd")
        or extra.get("requested_expiry_ymd"),
        "selected_expiry_ymd": entry.get("selected_expiry_ymd")
        or selector.get("selected_expiry_ymd")
        or extra.get("selected_expiry_ymd"),
        "fallback_days": entry.get("fallback_days")
        if entry.get("fallback_days") is not None
        else selector.get("fallback_days")
        if selector.get("fallback_days") is not None
        else extra.get("fallback_days"),
        "fallback_reason": entry.get("fallback_reason")
        or selector.get("fallback_reason")
        or extra.get("fallback_reason"),
        "strike": contract.get("strike") or entry.get("strike", ""),
        "option_side": contract.get("option_side", ""),
        "entry_time": entry.get("entry_time", ""),
        "exit_time": exit_obs.get("exit_time", trade.get("closed_at", "")),
        "entry_premium": entry.get("entry_premium"),
        "exit_premium": exit_obs.get("exit_premium"),
        "peak_premium": pm.get("peak_premium") or peak.get("peak_premium"),
        "low_premium": pm.get("premium_lwm"),
        "delta": path_fields.get("delta"),
        "target_delta": path_fields.get("target_delta"),
        "mfe_pct": pm.get("premium_mfe_pct") or pm.get("max_favorable_premium_pct"),
        "mae_pct": pm.get("premium_mae_pct") or pm.get("max_adverse_premium_pct"),
        "mfe_pct_at_exit": path_fields.get("mfe_pct_at_exit") or pm_exit.get("premium_mfe_pct"),
        "mae_pct_at_exit": path_fields.get("mae_pct_at_exit") or pm_exit.get("premium_mae_pct"),
        "realized_pnl_pct": path_fields.get("realized_pnl_pct"),
        "pre_mfe_mae_pct": path_fields.get("pre_mfe_mae_pct"),
        "path_pre_mfe_mae_pct": path_fields.get("path_pre_mfe_mae_pct"),
        "mae_before_peak_pct": path_fields.get("mae_before_peak_pct"),
        "mae_before_5pct_mfe_pct": path_fields.get("mae_before_5pct_mfe_pct"),
        "time_to_5pct_mfe_sec": path_fields.get("time_to_5pct_mfe_sec"),
        "time_to_10pct_mfe_sec": path_fields.get("time_to_10pct_mfe_sec"),
        "time_to_20pct_mfe_sec": path_fields.get("time_to_20pct_mfe_sec"),
        "time_to_30pct_mfe_sec": path_fields.get("time_to_30pct_mfe_sec"),
        "mfe_5pct_bypass_hit": path_fields.get("mfe_5pct_bypass_hit"),
        "capture_vs_session_peak_at_exit": path_fields.get("capture_vs_session_peak_at_exit"),
        "left_on_table_at_exit_pct": path_fields.get("left_on_table_at_exit_pct"),
        "peak_while_open_pct": path_fields.get("peak_while_open_pct"),
        "post_exit_peak_pct": path_fields.get("post_exit_peak_pct"),
        "post_exit_eod_pct": path_fields.get("post_exit_eod_pct"),
        "full_session_peak_pct": path_fields.get("full_session_peak_pct"),
        "while_open_upside_pts": path_fields.get("while_open_upside_pts"),
        "post_exit_upside_pts": path_fields.get("post_exit_upside_pts"),
        "full_session_upside_pts": path_fields.get("full_session_upside_pts"),
        "post_exit_left_on_table_pct": path_fields.get("post_exit_left_on_table_pct"),
        "capture_vs_full_session_peak": path_fields.get("capture_vs_full_session_peak"),
        "post_exit_continued_higher": path_fields.get("post_exit_continued_higher"),
        "post_exit_exceeded_open_peak": path_fields.get("post_exit_exceeded_open_peak"),
        "post_exit_shadow_trusted": path_fields.get("post_exit_shadow_trusted"),
        "post_exit_shadow_quote_count": path_fields.get("post_exit_shadow_quote_count"),
        "post_exit_shadow_gap_count": path_fields.get("post_exit_shadow_gap_count"),
        "live_tick_count": path_fields.get("live_tick_count"),
        "shadow_tick_count": path_fields.get("shadow_tick_count"),
        "path_quality_sparse": path_fields.get("path_quality_sparse"),
        "capture_pct": pm.get("capture_pct") or pm.get("capture_pct_at_eod"),
        "capture_pct_at_exit": pm.get("capture_pct_at_exit") or pm_exit.get("capture_pct"),
        "capture_pct_at_eod": pm.get("capture_pct_at_eod"),
        "premium_at_eod": pm.get("premium_at_eod"),
        "capture_of_net_opportunity_pct": pm.get("capture_of_net_opportunity_pct"),
        "missed_premium_pct": pm.get("missed_premium_pct"),
        "time_to_mfe_sec": pm.get("time_to_mfe_seconds"),
        "time_to_mae_sec": pm.get("time_to_mae_seconds"),
        "theta_decay_5m_after_peak_pct": _theta_offset_pct(5),
        "theta_decay_15m_after_peak_pct": _theta_offset_pct(15),
        "theta_decay_30m_after_peak_pct": _theta_offset_pct(30),
        "priority_rank": selector.get("priority_rank"),
        "priority_score": selector.get("priority_score"),
        "convex_score": selector.get("convex_score"),
        "selector_score": selector.get("selector_score"),
        "entry_spread_pct": eq.get("entry_spread_pct"),
        "entry_slippage_vs_mid_pct": eq.get("entry_slippage_vs_mid_pct"),
        "peak_quote_quality": peak.get("peak_quote_quality", ""),
        "peak_premium_source": peak.get("peak_premium_source", ""),
        "peak_trusted": peak.get("peak_trusted"),
        "shadow_path_trusted": pa.get("shadow_path_trusted"),
        "shadow_quarantine_reason": pa.get("shadow_quarantine_reason", ""),
        "shadow_quote_exact": pa.get("shadow_quote_exact"),
        "contract_identity_match": pa.get("contract_identity_match"),
        "first_shadow_jump_ratio": pa.get("first_shadow_jump_ratio"),
        "no_progress_exit_shadow": pa.get("no_progress_exit_shadow"),
        "shadow_stop_reason": pa.get("shadow_stop_reason", ""),
        "exit_reason": exit_obs.get("exit_reason", ""),
    }


def export_cohort_csv(
    session_date: str,
    trades: List[Dict[str, Any]],
    *,
    base_dir: Optional[Path] = None,
) -> Path:
    path = cohort_file_path(session_date, base_dir)
    rows = [_row_from_trade(t, session_date) for t in trades]
    with path.open("w", newline="", encoding="utf-8") as fp:
        writer = csv.DictWriter(fp, fieldnames=COHORT_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    log.info(
        "PROFIT_OPTIMIZER_COHORT_EXPORTED | path=%s | rows=%d",
        path,
        len(rows),
    )
    return path


def export_cohort_csv_by_path(
    session_date: str,
    trades: List[Dict[str, Any]],
    *,
    base_dir: Optional[Path] = None,
) -> Dict[str, Path]:
    """Write one cohort CSV per strategy path (SO / ORB0DTE / Trendline0DTE)."""
    root = (base_dir or analytics_dir()) / "cohorts"
    root.mkdir(parents=True, exist_ok=True)
    out: Dict[str, Path] = {}
    by_path: Dict[str, List[Dict[str, Any]]] = {p: [] for p in STRATEGY_PATHS}
    for trade in trades:
        entry = trade.get("entry") if isinstance(trade.get("entry"), dict) else {}
        path = str(entry.get("strategy_path") or trade.get("strategy_path") or "").strip().lower()
        if path not in by_path:
            by_path[path] = []
        by_path[path].append(trade)
    for path_key, bucket in by_path.items():
        if not bucket:
            continue
        file_path = root / f"{session_date}_{path_key}_profit_optimizer_cohort.csv"
        rows = [_row_from_trade(t, session_date) for t in bucket]
        with file_path.open("w", newline="", encoding="utf-8") as fp:
            writer = csv.DictWriter(fp, fieldnames=COHORT_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow(row)
        out[path_key] = file_path
        log.info(
            "PROFIT_OPTIMIZER_COHORT_EXPORTED | path=%s | strategy=%s | rows=%d",
            file_path,
            path_key,
            len(rows),
        )
    return out
