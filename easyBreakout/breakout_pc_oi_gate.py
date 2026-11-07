"""
Breakout handoff before the order.

Hydrates this signal's chain and bars, then accepts or rejects with the
Trade Profile. Directional percent and open interest are profile keys.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def _env_bool(key: str, default: bool = True) -> bool:
    import os

    raw = os.getenv(key, str(default)).strip().lower()
    return raw in ("1", "true", "yes", "on")


def build_breakout_pc_oi_signal_row(signal: Any) -> Dict[str, Any]:
    sym = str(getattr(signal, "symbol", "") or "").strip().upper()
    option_side = str(getattr(signal, "option_side", "") or "").strip().lower()
    side = "CALL" if option_side == "call" else "PUT" if option_side == "put" else "CALL"
    spot = 0.0
    for attr in ("entry_underlying_price", "break_price"):
        try:
            spot = float(getattr(signal, attr, 0.0) or 0.0)
        except (TypeError, ValueError):
            spot = 0.0
        if spot > 0:
            break
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
        "source_path": "breakout_0dte",
    }
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
            "implied_volatility",
            "iv",
            "representative_iv",
            "direction_volume_pct",
            "l1_delta_5s_with",
            "close_at_break",
            "break_timestamp",
        ):
            if row.get(key) is None and meta.get(key) is not None:
                row[key] = meta[key]
        from easy0DTE.modules.trade_profile import copy_profile_fields

        copy_profile_fields(meta, row)
    return row


def _stamp_layer_c_on_signal(signal: Any, skew_out: Dict[str, Any], *, chain_warmed: bool) -> None:
    meta = getattr(signal, "metadata", None)
    if not isinstance(meta, dict):
        return
    for key, val in (skew_out or {}).items():
        if val is not None:
            meta[key] = val
    meta["pc_oi_chain_warmed"] = bool(chain_warmed)
    meta["pc_oi_confirmed"] = True
    meta["dir_pct_confirmed"] = True


async def confirm_breakout_dir_pct_before_execution(
    system: Any,
    signal: Any,
    *,
    enabled: Optional[bool] = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """Hydrate the chain once, then accept or reject with the Trade Profile."""
    if enabled is None:
        from easy0DTE.modules.quality_gate_stack import PATH_BO, resolve_gate_mode

        enabled = resolve_gate_mode(PATH_BO, "trade_profile") != "off"

    from easy0DTE.modules.quality_gate_stack import (
        PATH_BO,
        confirm_dir_pct_before_execution,
    )

    row = build_breakout_pc_oi_signal_row(signal)
    ok, msg, skew_out = await confirm_dir_pct_before_execution(
        PATH_BO,
        system,
        signal,
        row=row,
        log_tag="BREAKOUT_DIR_PCT",
        enabled=bool(enabled),
    )
    if ok and skew_out:
        _stamp_layer_c_on_signal(
            signal,
            skew_out,
            chain_warmed=bool(skew_out.get("pc_oi_chain_warmed")),
        )
    return ok, msg, skew_out


async def confirm_breakout_pc_oi_before_execution(
    system: Any,
    signal: Any,
    *,
    enabled: Optional[bool] = None,
) -> Tuple[bool, str, Dict[str, Any]]:
    """Legacy alias — same as ``confirm_breakout_dir_pct_before_execution``."""
    return await confirm_breakout_dir_pct_before_execution(
        system, signal, enabled=enabled
    )
