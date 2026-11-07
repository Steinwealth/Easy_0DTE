"""
Trendline Priority Optimizer telemetry helpers (research-only).

Derives orb_zone, break velocity, and feature enrichment for snapshots/events.
Does not affect trading gates, scores, or execution.
"""

from __future__ import annotations

import logging
from typing import Any, List, Optional, Union

log = logging.getLogger(__name__)

ORB_ZONE_BELOW = "below_orb"
ORB_ZONE_INSIDE = "inside_orb"
ORB_ZONE_ABOVE = "above_orb"


def derive_orb_zone(
    *,
    close: Optional[float],
    orb_low: Optional[float],
    orb_high: Optional[float],
    orb_pct_from_low: Optional[float] = None,
) -> Optional[str]:
    """
    Classify price vs ORB at break time.

    Uses strict ORB bounds when close and range are valid; otherwise falls back to pct bands.
    """
    try:
        c = float(close) if close is not None else None
        lo = float(orb_low) if orb_low is not None else None
        hi = float(orb_high) if orb_high is not None else None
    except (TypeError, ValueError):
        c, lo, hi = None, None, None

    if c is not None and lo is not None and hi is not None and hi > lo:
        if c < lo:
            return ORB_ZONE_BELOW
        if c > hi:
            return ORB_ZONE_ABOVE
        return ORB_ZONE_INSIDE

    if orb_pct_from_low is not None:
        try:
            pct = float(orb_pct_from_low)
        except (TypeError, ValueError):
            return None
        if pct < 0.0:
            return ORB_ZONE_BELOW
        if pct > 1.0:
            return ORB_ZONE_ABOVE
        return ORB_ZONE_INSIDE

    return None


def compute_break_velocity_pct(
    bar: Any,
    bars: List[Any],
    direction: Any,
) -> float:
    """
    Bar-over-bar price change in the break direction (non-negative %).

    Telemetry-only proxy for expansion speed at the break bar; not used in gates.
    """
    if not bars or len(bars) < 2:
        return 0.0
    try:
        close = float(getattr(bar, "close", 0.0) or 0.0)
        prev_close = float(getattr(bars[-2], "close", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0
    if prev_close <= 0 or close <= 0:
        return 0.0

    raw_pct = (close - prev_close) / prev_close
    dir_val = getattr(direction, "value", direction)
    dir_s = str(dir_val or "").lower()

    # Bear / descending resistance break = upward through line → favorable velocity when price rises
    if dir_s == "bear":
        return float(max(0.0, raw_pct))
    # Bull / ascending support break = downward through line
    if dir_s == "bull":
        return float(max(0.0, -raw_pct))
    return float(abs(raw_pct))


def log_velocity_capture_audit(symbol: str, velocity_pct: float) -> None:
    log.warning(
        "TRENDLINE_VELOCITY_CAPTURE_AUDIT | symbol=%s | velocity_pct=%.6f",
        (symbol or "").strip().upper(),
        float(velocity_pct or 0.0),
    )


def enrich_signal_row_for_features(
    sig_row: dict,
    *,
    rsi: Optional[float] = None,
    volume_ratio: Optional[float] = None,
    vwap: Optional[float] = None,
    vwap_position: Optional[str] = None,
    entry_distance_pct: Optional[float] = None,
    score: Optional[float] = None,
    orb_zone: Optional[str] = None,
) -> dict:
    """Merge telemetry fields into sig_row metadata for extract_signal_features*."""
    md = dict(sig_row.get("metadata") or {})
    if rsi is not None:
        sig_row["rsi"] = float(rsi)
        md["rsi"] = float(rsi)
    if volume_ratio is not None:
        sig_row["volume_ratio"] = float(volume_ratio)
        md["volume_ratio"] = float(volume_ratio)
    if vwap is not None and float(vwap) > 0:
        sig_row["vwap"] = float(vwap)
        md["vwap"] = float(vwap)
    if vwap_position:
        md["vwap_position"] = str(vwap_position)
    if entry_distance_pct is not None:
        md["entry_distance_pct"] = float(entry_distance_pct)
    if score is not None:
        sig_row["priority_score"] = float(score)
        md["simple_quality_score"] = float(score)
    if orb_zone:
        md["orb_zone"] = str(orb_zone)
    sig_row["metadata"] = md
    return sig_row


def _first_present(*values: Any) -> Any:
    for val in values:
        if val is None:
            continue
        if isinstance(val, str) and not val.strip():
            continue
        return val
    return None


def _as_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


# Decision-time keys taken from the first-break record / break event when the
# signal metadata does not already carry them. ``pressure_score`` and
# ``entry_mode`` are not here: the engine only ever initialises them (0.0 / "")
# and never updates them, so carrying them would stamp a constant, not data.
_BREAK_STRUCTURE_CARRY_KEYS = (
    "body_ratio_vs_avg",
    "break_archetype",
    "candidate_state",
    "structure_fit_mse",
    "structure_selection_reason",
    "trendline_structure_source",
    "minutes_from_730_to_break",
    "velocity_pct",
)


def _carry_break_structure_keys(
    sig_row: dict,
    *,
    meta: dict,
    setup: dict,
    break_row: dict,
    break_event: Any,
) -> int:
    if not isinstance(sig_row, dict):
        return 0
    be_meta: dict = {}
    if break_event is not None:
        raw = getattr(break_event, "metadata", None)
        if isinstance(raw, dict):
            be_meta = raw
    flat: dict = {}
    if isinstance(break_row, dict) and break_row:
        try:
            from modules.po_snapshot_hydration import features_from_break_record

            flat = features_from_break_record(break_row) or {}
        except Exception:
            flat = {}
    n = 0
    for key in _BREAK_STRUCTURE_CARRY_KEYS:
        if sig_row.get(key) not in (None, ""):
            continue
        val = _first_present(
            meta.get(key),
            be_meta.get(key),
            (break_row or {}).get(key) if isinstance(break_row, dict) else None,
            flat.get(key),
            setup.get(key),
        )
        if val in (None, ""):
            continue
        sig_row[key] = val
        md = sig_row.get("metadata")
        if isinstance(md, dict) and md.get(key) in (None, ""):
            md[key] = val
        n += 1
    return n


def build_trendline_execution_snapshot_signal(
    signal: Any,
    *,
    candidate: Any = None,
    order_payload: Optional[dict] = None,
    break_snapshot_row: Optional[dict] = None,
) -> dict:
    """
    Build a PO execution snapshot row for a filled Trendline 0DTE trade.

    Merges emit-time metadata, break-time quality, first-break snapshot rows, and fill payload
    so rsi / volume / vwap / ORB / 89-key exports are populated at execution capture.
    """
    meta = dict(getattr(signal, "metadata", None) or {})
    setup = dict(meta.get("setup_payload") or {})
    if candidate is not None:
        setup = {**dict(getattr(candidate, "setup_payload", None) or {}), **setup}
    bs = dict(break_snapshot_row or {})
    pl = dict(order_payload or {})

    be = getattr(signal, "break_event", None)
    if be is None and candidate is not None:
        be = getattr(candidate, "break_event", None)

    close_px = _first_present(
        pl.get("entry_underlying_price"),
        getattr(be, "close_price", None) if be is not None else None,
        bs.get("close_at_break"),
        meta.get("close_at_break"),
    )
    close_f = _as_float(close_px)

    orb_high = _first_present(setup.get("orb_high"), bs.get("orb_high"), meta.get("orb_high"))
    orb_low = _first_present(setup.get("orb_low"), bs.get("orb_low"), meta.get("orb_low"))
    orb_range = _first_present(setup.get("orb_range"), bs.get("orb_range"), meta.get("orb_range"))
    orb_range_pct = _first_present(
        setup.get("orb_range_pct"),
        bs.get("orb_range_pct"),
        meta.get("orb_range_pct"),
    )
    if orb_range is None and orb_high is not None and orb_low is not None:
        try:
            orb_range = float(orb_high) - float(orb_low)
        except (TypeError, ValueError):
            orb_range = None
    if orb_range_pct is None and orb_high is not None and orb_low is not None:
        try:
            low_f = float(orb_low)
            if low_f > 0:
                orb_range_pct = ((float(orb_high) - low_f) / low_f) * 100.0
        except (TypeError, ValueError, ZeroDivisionError):
            orb_range_pct = None

    break_bar = bs.get("break_bar") if isinstance(bs.get("break_bar"), dict) else {}
    bar_volume = _first_present(break_bar.get("volume"), meta.get("volume"))

    rsi = _as_float(
        _first_present(meta.get("rsi"), bs.get("rsi"), setup.get("rsi"))
    )
    volume_ratio = _as_float(
        _first_present(meta.get("volume_ratio"), bs.get("volume_ratio"), setup.get("volume_ratio"))
    )
    vwap = _as_float(_first_present(meta.get("vwap"), bs.get("vwap"), setup.get("vwap")))
    entry_distance_pct = _as_float(
        _first_present(
            meta.get("vwap_distance_pct"),
            meta.get("entry_distance_pct"),
            bs.get("entry_distance_pct"),
        )
    )
    vwap_position = _first_present(meta.get("vwap_position"), bs.get("vwap_position"))
    orb_zone = _first_present(meta.get("orb_zone"), bs.get("orb_zone"))
    if orb_zone is None and close_f is not None:
        orb_zone = derive_orb_zone(
            close=close_f,
            orb_low=_as_float(orb_low),
            orb_high=_as_float(orb_high),
            orb_pct_from_low=_as_float(bs.get("orb_pct_from_low")),
        )

    fill_trade_id = pl.get("trade_id") or meta.get("trade_id") or bs.get("trade_id")
    trade_side = str(getattr(signal, "option_side", "") or "").upper()
    if trade_side not in ("CALL", "PUT"):
        trade_side = "CALL"
    # Only stamp a real Trendline slot. Empty/missing must stay null so shared
    # callers (ORB2 Breakout reuses this builder) do not get filler trendline1.
    raw_slot = _first_present(
        meta.get("trendline_slot"),
        setup.get("trendline_slot"),
        bs.get("trendline_slot"),
    )
    tl_slot = None
    if raw_slot is not None:
        try:
            from easyTrendline.trendline_models import normalize_trendline_slot

            tl_slot = normalize_trendline_slot(raw_slot)
        except Exception:
            tl_slot = str(raw_slot).strip().lower() or None
    envelope_role = _first_present(
        meta.get("envelope_role"),
        setup.get("envelope_role"),
        bs.get("envelope_role"),
    )
    priority_score = _as_float(
        _first_present(
            getattr(signal, "priority_score", None),
            meta.get("priority_score"),
            meta.get("simple_quality_score"),
            bs.get("priority_score"),
            bs.get("score"),
        )
    )
    sig_row: dict = {
        "symbol": getattr(signal, "symbol", None),
        "side": trade_side,
        "direction": trade_side,
        "option_type": trade_side.lower(),
        "option_type_label": trade_side,
        "expected_option_side": trade_side.lower(),
        "trade_id": fill_trade_id,
        "trendline_slot": tl_slot,
        "envelope_role": envelope_role,
        "current_price": close_f,
        "price": close_f,
        "close": close_f,
        "confidence": getattr(signal, "confidence", None),
        "priority_score": priority_score,
        "simple_quality_score": _as_float(
            _first_present(meta.get("simple_quality_score"), priority_score)
        ),
        "priority_rank": meta.get("priority_rank"),
        "quantity": pl.get("quantity"),
        "position_size_pct": None,
        "orb_high": orb_high,
        "orb_low": orb_low,
        "orb_range": orb_range,
        "orb_range_pct": orb_range_pct,
        "volume": bar_volume,
        "velocity_pct": _as_float(_first_present(meta.get("velocity_pct"), bs.get("velocity_pct"))),
        "break_distance_pct": _as_float(
            _first_present(meta.get("break_distance_pct"), bs.get("break_distance_pct"))
        ),
        "setup_type": meta.get("setup_type") or bs.get("setup_type"),
        "trigger_direction": meta.get("trigger_direction") or bs.get("trigger_direction"),
        "metadata": {
            **meta,
            "strategy_name": "easyTrendline_0DTE",
            "strategy_type": "trendline_0dte",
            "entry_type": "trendline_break",
            "stage": "executed",
            "trade_id": fill_trade_id,
            "trendline_slot": tl_slot,
            "envelope_role": envelope_role,
            "setup_type": meta.get("setup_type"),
            "structure_display_label": meta.get("structure_display_label"),
            "trigger_direction": meta.get("trigger_direction"),
            "line_geometry": getattr(getattr(signal, "direction", None), "value", None),
            "option_side": getattr(signal, "option_side", None),
            "strike": pl.get("strike"),
            "delta_at_entry": pl.get("delta_at_entry"),
            "entry_premium_per_contract": pl.get("estimated_contract_price"),
            "estimated_total_cost": pl.get("estimated_total_cost"),
            "expiry_ymd": pl.get("expiry_ymd"),
            "candidate_source": meta.get("candidate_source"),
            "trendline_structure_source": meta.get("trendline_structure_source"),
            "structure_explainability": meta.get("structure_explainability")
            if isinstance(meta.get("structure_explainability"), dict)
            else None,
            "break_trade_id": bs.get("trade_id"),
            "break_snapshot_joined": bool(bs),
            "priority_score": priority_score,
            "simple_quality_score": _as_float(
                _first_present(meta.get("simple_quality_score"), priority_score)
            ),
        },
    }

    enrich_signal_row_for_features(
        sig_row,
        rsi=rsi,
        volume_ratio=volume_ratio,
        vwap=vwap,
        vwap_position=str(vwap_position) if vwap_position else None,
        entry_distance_pct=entry_distance_pct,
        score=_as_float(
            _first_present(meta.get("simple_quality_score"), priority_score, bs.get("score"))
        ),
        orb_zone=str(orb_zone) if orb_zone else None,
    )
    if entry_distance_pct is not None:
        sig_row["vwap_distance_pct"] = entry_distance_pct

    # Carry QG report + FT / OTM for PO densify (Entry/Exit desk matrix)
    qgr = _first_present(
        meta.get("quality_gate_report"),
        bs.get("quality_gate_report"),
        setup.get("quality_gate_report"),
    )
    if isinstance(qgr, dict):
        sig_row["quality_gate_report"] = qgr
    for k in (
        "continuation_pct",
        "follow_through_bars",
        "post_break_chop_pct",
        "otm_pct",
        "chosen_delta",
        "fill_delta",
        "quality_score",
    ):
        v = _first_present(meta.get(k), bs.get(k), setup.get(k), pl.get(k))
        if v is not None and sig_row.get(k) is None:
            sig_row[k] = v

    # Break clock provenance for PO trajectory (terminate at po_decision_freeze_at).
    break_ts = _first_present(
        bs.get("break_timestamp"),
        bs.get("first_break_timestamp"),
        meta.get("break_timestamp"),
        meta.get("break_ts"),
        getattr(be, "candle_ts", None) if be is not None else None,
    )
    if break_ts is not None:
        if hasattr(break_ts, "isoformat"):
            try:
                break_ts = break_ts.isoformat()
            except Exception:
                pass
        sig_row.setdefault("break_timestamp", break_ts)
        sig_row.setdefault("structure_break_ts", break_ts)
        sig_row.setdefault("break_ts", break_ts)
        if isinstance(sig_row.get("metadata"), dict):
            sig_row["metadata"].setdefault("break_timestamp", break_ts)
            sig_row["metadata"].setdefault("break_ts", break_ts)

    close_at_break = _as_float(
        _first_present(
            bs.get("close_at_break"),
            meta.get("close_at_break"),
            getattr(be, "close_price", None) if be is not None else None,
            close_f,
        )
    )
    if close_at_break is not None:
        sig_row.setdefault("close_at_break", close_at_break)
        if isinstance(sig_row.get("metadata"), dict):
            sig_row["metadata"].setdefault("close_at_break", close_at_break)

    line_at_break = _as_float(
        _first_present(
            bs.get("line_price_at_break"),
            meta.get("line_price_at_break"),
            getattr(be, "line_price", None) if be is not None else None,
        )
    )
    if line_at_break is not None:
        sig_row.setdefault("line_price_at_break", line_at_break)

    # Break-structure research keys. They are observed at the break, before the
    # handoff, and the +20%/exit snaps already join them from the same break
    # record (features_from_break_record). The handoff builder never copied
    # them, so Oct 5 TL handoffs had body_ratio_vs_avg / break_archetype /
    # candidate_state 100% null while mfe/exit filled them for the same trade.
    _carry_break_structure_keys(sig_row, meta=meta, setup=setup, break_row=bs, break_event=be)

    # Selected/owned OCC book sizes from fill payload (exact contract, not representative).
    for src_key, dst_key in (
        ("bid_size", "option_bid_size"),
        ("ask_size", "option_ask_size"),
        ("option_bid_size", "option_bid_size"),
        ("option_ask_size", "option_ask_size"),
    ):
        if pl.get(src_key) is not None and sig_row.get(dst_key) is None:
            try:
                sig_row[dst_key] = int(float(pl.get(src_key)))
            except (TypeError, ValueError):
                pass
    if pl.get("strike") is not None and sig_row.get("strike") is None:
        sig_row["strike"] = pl.get("strike")
    if pl.get("listed_exchange_strike") is not None and sig_row.get("strike") is None:
        sig_row["strike"] = pl.get("listed_exchange_strike")

    features_89 = bs.get("features_89") or bs.get("features_dense")
    if isinstance(features_89, dict):
        for key, val in features_89.items():
            if val is not None and sig_row.get(key) is None:
                sig_row[key] = val

    from modules.po_snapshot_hydration import DECISION_MOON_DELTA_KEYS

    # Decision metadata wins. A missing Moon input stays absent (densify null).
    for moon_key in DECISION_MOON_DELTA_KEYS:
        if meta.get(moon_key) not in (None, ""):
            sig_row[moon_key] = meta.get(moon_key)
        else:
            sig_row.pop(moon_key, None)

    return sig_row
