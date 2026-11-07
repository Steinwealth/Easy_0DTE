#!/usr/bin/env python3
"""Strategy-path labels for profit_optimizer cohort grouping."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from profit_optimizer.profit_optimizer_models import (
    STRATEGY_BREAKOUT_0DTE,
    STRATEGY_ORB_0DTE,
    STRATEGY_ORB_SO,
    STRATEGY_TRENDLINE_0DTE,
)

# Canonical classifier strings (stable for CSV / analytics).
PATH_CLASS_ORB_SO = "SO"
PATH_CLASS_ORB_0DTE = "ORB0DTE"
PATH_CLASS_TRENDLINE_0DTE = "Trendline0DTE"
PATH_CLASS_BREAKOUT_0DTE = "Breakout0DTE"

STRATEGY_PATHS = (
    STRATEGY_ORB_SO,
    STRATEGY_ORB_0DTE,
    STRATEGY_TRENDLINE_0DTE,
    STRATEGY_BREAKOUT_0DTE,
)

_PATH_TO_CLASS = {
    STRATEGY_ORB_SO: PATH_CLASS_ORB_SO,
    STRATEGY_ORB_0DTE: PATH_CLASS_ORB_0DTE,
    STRATEGY_TRENDLINE_0DTE: PATH_CLASS_TRENDLINE_0DTE,
    STRATEGY_BREAKOUT_0DTE: PATH_CLASS_BREAKOUT_0DTE,
}


def normalize_strategy_path(strategy_path: Optional[str]) -> str:
    return str(strategy_path or "").strip().lower()


def path_class_for_strategy(strategy_path: Optional[str]) -> str:
    return _PATH_TO_CLASS.get(normalize_strategy_path(strategy_path), "")


def path_class_for_trade(trade: Dict[str, Any]) -> str:
    entry = trade.get("entry") if isinstance(trade.get("entry"), dict) else {}
    explicit = str(trade.get("path_class") or entry.get("path_class") or "").strip()
    if explicit:
        return explicit
    return path_class_for_strategy(
        trade.get("strategy_path") or entry.get("strategy_path")
    )


def group_trades_by_path(trades: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {p: [] for p in STRATEGY_PATHS}
    for trade in trades:
        entry = trade.get("entry") if isinstance(trade.get("entry"), dict) else {}
        path = normalize_strategy_path(trade.get("strategy_path") or entry.get("strategy_path"))
        if path not in grouped:
            grouped[path] = []
        grouped[path].append(trade)
    return grouped


def enrich_trade_path_class(trade: Dict[str, Any]) -> Dict[str, Any]:
    """Attach top-level path_class for export / retrieve."""
    out = dict(trade)
    pc = path_class_for_trade(out)
    if pc:
        out["path_class"] = pc
    entry = out.get("entry") if isinstance(out.get("entry"), dict) else {}
    if pc and isinstance(entry, dict):
        entry = dict(entry)
        entry["path_class"] = pc
        out["entry"] = entry
    return out


def calendar_dte_bucket(trade: Dict[str, Any]) -> str:
    """Group label from calendar DTE (true 0DTE vs nearest-forward)."""
    entry = trade.get("entry") if isinstance(trade.get("entry"), dict) else {}
    extra = entry.get("extra") if isinstance(entry.get("extra"), dict) else {}
    selector = entry.get("selector_metadata") if isinstance(entry.get("selector_metadata"), dict) else {}
    dte = entry.get("calendar_dte")
    if dte is None:
        dte = selector.get("calendar_dte")
    if dte is None:
        dte = extra.get("calendar_dte")
    if dte is None:
        dte = entry.get("dte")
    try:
        dte_i = int(dte) if dte is not None else None
    except (TypeError, ValueError):
        dte_i = None
    is_0 = entry.get("is_0dte")
    if is_0 is None:
        is_0 = selector.get("is_0dte")
    if is_0 is None:
        is_0 = extra.get("is_0dte")
    if is_0 is True or dte_i == 0:
        return "calendar_0dte"
    if dte_i == 1:
        return "calendar_1dte"
    if dte_i is not None and dte_i > 1:
        return f"calendar_{dte_i}dte"
    return "calendar_dte_unknown"


def group_trades_by_calendar_dte(trades: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for trade in trades:
        key = calendar_dte_bucket(trade)
        grouped.setdefault(key, []).append(trade)
    return grouped
