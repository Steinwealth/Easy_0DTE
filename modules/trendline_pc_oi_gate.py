"""
Trendline handoff before the order.

Hydrates this signal's chain and bars, then accepts or rejects with the
Trade Profile. Directional percent and open interest are profile keys.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def build_trendline_pc_oi_signal_row(signal: Any) -> Dict[str, Any]:
    """Map TrendlineTradeSignal to a Quality Gate row (side = CALL|PUT)."""
    sym = str(getattr(signal, "symbol", "") or "").strip().upper()
    option_side = str(getattr(signal, "option_side", "") or "").strip().lower()
    side = "CALL" if option_side == "call" else "PUT" if option_side == "put" else "CALL"
    spot = 0.0
    be = getattr(signal, "break_event", None)
    if be is not None:
        try:
            spot = float(getattr(be, "close_price", 0.0) or 0.0)
        except (TypeError, ValueError):
            spot = 0.0
    meta = getattr(signal, "metadata", None)
    if spot <= 0.0 and isinstance(meta, dict):
        for key in ("entry_underlying_price", "close_at_break", "current_price", "price"):
            try:
                spot = float(meta.get(key) or 0.0)
            except (TypeError, ValueError):
                continue
            if spot > 0.0:
                break
    row: Dict[str, Any] = {
        "symbol": sym,
        "side": side,
        "direction": side,
        "option_side": option_side,
        "option_type": option_side or side.lower(),
        "option_type_label": side,
        "current_price": spot,
        "price": spot,
        "source_path": "trendline_0dte",
    }
    if be is not None:
        try:
            raw_dist = getattr(be, "break_distance_pct", None)
            if raw_dist is not None:
                row["break_distance_pct"] = float(raw_dist)
        except (TypeError, ValueError):
            pass
        try:
            raw_line = getattr(be, "trendline_price", None)
            line_px = float(raw_line) if raw_line is not None else 0.0
        except (TypeError, ValueError):
            line_px = 0.0
        if line_px > 0.0:
            row["line_price_at_break"] = line_px
            row["break_level"] = line_px
    if isinstance(meta, dict):
        row["metadata"] = dict(meta)
        for key in (
            "volume_ratio",
            "rsi",
            "vwap_distance_pct",
            "vwap_position",
            "bollinger_position",
            "session_vs_avg_volume",
            "seconds_since_break",
            "seconds_from_material_break",
            "implied_volatility",
            "iv",
            "representative_iv",
            "direction_volume_pct",
            "l1_delta_5s_with",
            "trendline_slot",
            "close_at_break",
            "break_timestamp",
            "material_break_at",
            "break_distance_pct",
            "line_price_at_break",
            "break_level",
            "price_volume_efficiency_3m",
            "price_change_3m_pct",
            "volume_3m",
            "setup_payload",
            "qg_admit",
        ):
            if row.get(key) is None and meta.get(key) is not None:
                row[key] = meta[key]
        setup = meta.get("setup_payload") if isinstance(meta.get("setup_payload"), dict) else {}
        from easy0DTE.modules.trade_profile import copy_profile_fields

        copy_profile_fields(meta, row)
        copy_profile_fields(setup, row)
        if row.get("trendline_slot") is None and setup.get("trendline_slot") is not None:
            row["trendline_slot"] = setup.get("trendline_slot")
        if row.get("seconds_since_break") is None and meta.get("seconds_from_material_break") is not None:
            row["seconds_since_break"] = meta.get("seconds_from_material_break")
    return row


def _stamp_layer_c_on_signal(signal: Any, skew_out: Dict[str, Any], *, chain_warmed: bool) -> None:
    """Persist skew on signal metadata so executor reuses warmed chain cache."""
    meta = getattr(signal, "metadata", None)
    if not isinstance(meta, dict):
        return
    for key, val in (skew_out or {}).items():
        if val is not None:
            meta[key] = val
    meta["pc_oi_chain_warmed"] = bool(chain_warmed)
    meta["pc_oi_confirmed"] = True
    meta["dir_pct_confirmed"] = True


async def confirm_trendline_dir_pct_before_execution(
    system: Any,
    signal: Any,
    *,
    enabled: Optional[bool] = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """Layer C via QG — one chain fetch + ``dir_pct`` hard."""
    from easy0DTE.modules.quality_gate_stack import (
        PATH_TL,
        confirm_dir_pct_before_execution,
        tl_dir_pct_handoff_enabled,
    )

    if enabled is None:
        enabled = tl_dir_pct_handoff_enabled()

    row = build_trendline_pc_oi_signal_row(signal)
    ok, msg, skew_out = await confirm_dir_pct_before_execution(
        PATH_TL,
        system,
        signal,
        row=row,
        log_tag="TRENDLINE_DIR_PCT",
        enabled=bool(enabled),
    )
    if ok and skew_out:
        _stamp_layer_c_on_signal(
            signal,
            skew_out,
            chain_warmed=bool(skew_out.get("pc_oi_chain_warmed")),
        )
    return ok, msg, skew_out

