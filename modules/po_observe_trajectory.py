"""
Priority Optimizer observe-only trajectory / L1 / participation features.

These fields approximate what a desk sees on L1, session volume trajectory, and
candle structure using data already on the hot path:

- Equity L1 sizes from existing ``get_batch_quotes`` polls (ring buffer)
- Session 1m OHLCV from ``_session_tech_bar_cache`` (participation proxies)
- Option L1 sizes already on exact-quote / path depth stamps
- Chain volume trajectory from prior hydrate stamps only (no new chain polls)

Not order flow: no aggressor buy/sell, no L2 depth, no Time & Sales.
Volume_* fields are participation proxies. Never promote to live hard gates
without multi-session PO evidence (same rule as Quality Gate promotion).

Decision-time freeze (entry research):
- ``*_emit_handoff`` stamps with closed 1m bars + ``po_decision_freeze_at``.
- ``*_options_executed`` carries bar/L1/participation from the handoff cache
  but restamps chain now / ~5m-ago at execution wall clock (ring + options enrich).
- Exit snaps may use live/forming-bar mode.
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Soft quote-age threshold for underlying_quote_stale (seconds).
_UNDERLYING_QUOTE_STALE_SEC = 15.0
_L1_RING_MAX = 120
_CHAIN_RING_MAX = 64
_RTH_MINUTES = 390.0  # 06:30–13:00 PT

# Non-schema diagnostic keys (not part of FEATURE_KEYS_0DTE_UNIFIED densify).
_DECISION_FREEZE_AT = "po_decision_freeze_at"
_DECISION_FREEZE_MODE = "po_decision_freeze_mode"
_TRAJECTORY_MODE = "po_trajectory_mode"
_SKIP_RESTAMP = "po_skip_trajectory_restamp"

# Chain trajectory keys stamped for QG/SS remine (now + ~5m history).
_CHAIN_TRAJECTORY_KEYS = (
    "call_chain_volume_delta_5m",
    "put_chain_volume_delta_5m",
    "same_side_chain_volume_delta_5m",
    "opposite_side_chain_volume_delta_5m",
    "dir_pct_change_5m",
    "owned_contract_volume_delta_5m",
    "owned_contract_oi_change",
    "same_side_chain_volume",
    "same_side_chain_volume_5m_ago",
    "direction_volume_pct_5m_ago",
)
# Min spacing between ring appends for the same symbol (seconds). Prevents
# discovery spam while still allowing ≥60s priors before handoff/execute.
_CHAIN_RING_MIN_SPACING_SEC = 15.0
_OPTION_L1_SOURCE = "option_l1_source"


def _f(val: Any) -> Optional[float]:
    if val is None or val == "":
        return None
    try:
        out = float(val)
    except (TypeError, ValueError):
        return None
    if out != out or math.isinf(out):
        return None
    return out


def _i(val: Any) -> Optional[int]:
    f = _f(val)
    if f is None:
        return None
    try:
        return int(f)
    except (TypeError, ValueError):
        return None


def l1_imbalance(*args, **kwargs):
    """Runtime owner: central_tech.option_l1.l1_imbalance."""
    from central_tech.option_l1 import l1_imbalance as _impl
    return _impl(*args, **kwargs)


def _ensure_equity_l1_ring(*args, **kwargs):
    """Runtime owner: modules.session_tech_admit._ensure_equity_l1_ring."""
    from modules.session_tech_admit import _ensure_equity_l1_ring as _impl
    return _impl(*args, **kwargs)


def _ensure_chain_skew_ring(*args, **kwargs):
    """Runtime owner: central_tech.chain_ring._ensure_chain_skew_ring."""
    from central_tech.chain_ring import _ensure_chain_skew_ring as _impl
    return _impl(*args, **kwargs)


def record_equity_l1_sample(*args, **kwargs):
    """Runtime owner: modules.session_tech_admit.record_equity_l1_sample."""
    from modules.session_tech_admit import record_equity_l1_sample as _impl
    return _impl(*args, **kwargs)


def note_chain_skew_warm_candidate(*args, **kwargs):
    """Runtime owner: central_tech.chain_ring.note_chain_skew_warm_candidate."""
    from central_tech.chain_ring import note_chain_skew_warm_candidate as _impl
    return _impl(*args, **kwargs)


def record_chain_skew_sample(*args, **kwargs):
    """Runtime owner: central_tech.chain_ring.record_chain_skew_sample."""
    from central_tech.chain_ring import record_chain_skew_sample as _impl
    return _impl(*args, **kwargs)


def _dir5m_merge_group(row: Dict[str, Any], blob: Dict[str, Any], *, overwrite: bool):
    """t217u: atomic dir5m group merge (flag DIR5M_TELEMETRY_ATOMIC, default on)."""
    try:
        from central_tech.chain_ring import dir5m_telemetry_atomic_enabled, merge_dir5m_group

        if not dir5m_telemetry_atomic_enabled():
            return 0, frozenset()
        return merge_dir5m_group(row, blob, overwrite=overwrite)
    except Exception:
        return 0, frozenset()


def chain_ring_premium_samples(*args, **kwargs):
    """Runtime owner: central_tech.chain_ring.chain_ring_premium_samples."""
    from central_tech.chain_ring import chain_ring_premium_samples as _impl
    return _impl(*args, **kwargs)


def _option_side_token(*args, **kwargs):
    """Runtime owner: central_tech.chain_ring._option_side_token."""
    from central_tech.chain_ring import _option_side_token as _impl
    return _impl(*args, **kwargs)


def _same_side_volume_share(*args, **kwargs):
    """Runtime owner: central_tech.chain_ring._same_side_volume_share."""
    from central_tech.chain_ring import _same_side_volume_share as _impl
    return _impl(*args, **kwargs)


def _same_side_share_from_sample(*args, **kwargs):
    """Runtime owner: central_tech.chain_ring._same_side_share_from_sample."""
    from central_tech.chain_ring import _same_side_share_from_sample as _impl
    return _impl(*args, **kwargs)


def _sample_owned_vol(*args, **kwargs):
    """Runtime owner: central_tech.chain_ring._sample_owned_vol."""
    from central_tech.chain_ring import _sample_owned_vol as _impl
    return _impl(*args, **kwargs)


def _sample_oi(*args, **kwargs):
    """Runtime owner: central_tech.chain_ring._sample_oi."""
    from central_tech.chain_ring import _sample_oi as _impl
    return _impl(*args, **kwargs)


def _pick_chain_prior_sample(*args, **kwargs):
    """Runtime owner: central_tech.chain_ring._pick_chain_prior_sample."""
    from central_tech.chain_ring import _pick_chain_prior_sample as _impl
    return _impl(*args, **kwargs)


def record_equity_l1_from_quotes(*args, **kwargs):
    """Runtime owner: modules.session_tech_admit.record_equity_l1_from_quotes."""
    from modules.session_tech_admit import record_equity_l1_from_quotes as _impl
    return _impl(*args, **kwargs)


def _sample_near(*args, **kwargs):
    """Runtime owner: modules.session_tech_admit._sample_near."""
    from modules.session_tech_admit import _sample_near as _impl
    return _impl(*args, **kwargs)


def _pt_minutes_since_rth_open(*args, **kwargs):
    """Runtime owner: modules.session_tech_admit._pt_minutes_since_rth_open."""
    from modules.session_tech_admit import _pt_minutes_since_rth_open as _impl
    return _impl(*args, **kwargs)


def _expected_session_fraction(*args, **kwargs):
    """Runtime owner: modules.session_tech_admit._expected_session_fraction."""
    from modules.session_tech_admit import _expected_session_fraction as _impl
    return _impl(*args, **kwargs)


def _bars_for_symbol(*args, **kwargs):
    """Runtime owner: modules.session_tech_admit._bars_for_symbol."""
    from modules.session_tech_admit import _bars_for_symbol as _impl
    return _impl(*args, **kwargs)


def _parse_ts(*args, **kwargs):
    """Runtime owner: modules.session_tech_admit._parse_ts."""
    from modules.session_tech_admit import _parse_ts as _impl
    return _impl(*args, **kwargs)


def mark_decision_freeze(row: Dict[str, Any], *, when: Optional[datetime] = None) -> datetime:
    """Stamp non-schema decision freeze clock used by entry trajectory derives."""
    freeze = when or datetime.now(timezone.utc)
    if freeze.tzinfo is None:
        freeze = freeze.replace(tzinfo=timezone.utc)
    row[_DECISION_FREEZE_AT] = freeze.isoformat()
    row[_DECISION_FREEZE_MODE] = "closed_bar"
    row[_TRAJECTORY_MODE] = "decision"
    return freeze


def carry_forward_trajectory_features(
    dest: Dict[str, Any],
    source: Optional[Dict[str, Any]],
) -> int:
    """Copy trajectory observe keys from handoff cache onto executed row (no restamp)."""
    if not isinstance(dest, dict) or not isinstance(source, dict):
        return 0
    try:
        from modules.po_feature_schema import FEATURE_KEYS_TRAJECTORY_OBSERVE

        keys = FEATURE_KEYS_TRAJECTORY_OBSERVE
    except Exception:
        keys = ()
    n = 0
    for k in keys:
        if dest.get(k) is None and source.get(k) is not None:
            dest[k] = source.get(k)
            n += 1
    # Preserve freeze diagnostics / quote age already on core schema.
    for k in (
        _DECISION_FREEZE_AT,
        _DECISION_FREEZE_MODE,
        _TRAJECTORY_MODE,
        _OPTION_L1_SOURCE,
        "underlying_quote_age_sec",
        "po_observe_trajectory_stamp",
        "break_timestamp",
        "structure_break_ts",
        "break_ts",
        "close_at_break",
        "line_price_at_break",
    ):
        if dest.get(k) is None and source.get(k) is not None:
            dest[k] = source.get(k)
    if n or source.get("po_observe_trajectory_stamp"):
        dest[_SKIP_RESTAMP] = True
        dest["po_observe_trajectory_stamp"] = True
        dest.setdefault(_TRAJECTORY_MODE, "decision_carry")
        dest.setdefault(_DECISION_FREEZE_MODE, "closed_bar")
    return n


def _iso_ts(val: Any) -> Optional[str]:
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        dt = val if val.tzinfo else val.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    if isinstance(val, (int, float)):
        try:
            return datetime.fromtimestamp(float(val), tz=timezone.utc).isoformat()
        except (TypeError, ValueError, OSError, OverflowError):
            return None
    if isinstance(val, str) and val.strip():
        return val.strip()
    return None


def stamp_break_timing_fields(
    row: Dict[str, Any],
    *,
    break_ts: Any = None,
    close_at_break: Any = None,
    line_price_at_break: Any = None,
) -> None:
    """
    Normalize break clock inputs onto a PO row.

    Extension / retracement clocks must terminate at ``po_decision_freeze_at``
    (never fill time); this only supplies the break origin timestamp + refs.
    """
    if not isinstance(row, dict):
        return
    md = row.get("metadata") if isinstance(row.get("metadata"), dict) else None
    ts_iso = _iso_ts(
        break_ts
        if break_ts is not None
        else row.get("break_timestamp")
        or row.get("structure_break_ts")
        or row.get("break_ts")
        or row.get("break_time")
        or (md.get("break_timestamp") if isinstance(md, dict) else None)
        or (md.get("structure_break_ts") if isinstance(md, dict) else None)
        or (md.get("break_ts") if isinstance(md, dict) else None)
        or (md.get("break_time") if isinstance(md, dict) else None)
    )
    if ts_iso:
        row.setdefault("break_timestamp", ts_iso)
        row.setdefault("structure_break_ts", ts_iso)
        row.setdefault("break_ts", ts_iso)
        if isinstance(md, dict):
            md.setdefault("break_timestamp", ts_iso)
            md.setdefault("structure_break_ts", ts_iso)
            md.setdefault("break_ts", ts_iso)

    close_v = _f(
        close_at_break
        if close_at_break is not None
        else row.get("close_at_break")
        if row.get("close_at_break") is not None
        else (md.get("close_at_break") if isinstance(md, dict) else None)
    )
    if close_v is not None:
        row.setdefault("close_at_break", close_v)
        if isinstance(md, dict):
            md.setdefault("close_at_break", close_v)

    line_v = _f(
        line_price_at_break
        if line_price_at_break is not None
        else row.get("line_price_at_break")
        if row.get("line_price_at_break") is not None
        else (md.get("line_price_at_break") if isinstance(md, dict) else None)
    )
    if line_v is not None:
        row.setdefault("line_price_at_break", line_v)
        if isinstance(md, dict):
            md.setdefault("line_price_at_break", line_v)


def _size_pair_from_mapping(*args, **kwargs):
    """Runtime owner: central_tech.option_l1._size_pair_from_mapping."""
    from central_tech.option_l1 import _size_pair_from_mapping as _impl
    return _impl(*args, **kwargs)


def _size_pair_from_contract(*args, **kwargs):
    """Runtime owner: central_tech.option_l1._size_pair_from_contract."""
    from central_tech.option_l1 import _size_pair_from_contract as _impl
    return _impl(*args, **kwargs)


def apply_selected_contract_option_l1(*args, **kwargs):
    """Runtime owner: central_tech.option_l1.apply_selected_contract_option_l1."""
    from central_tech.option_l1 import apply_selected_contract_option_l1 as _impl
    return _impl(*args, **kwargs)


def closed_session_bars(*args, **kwargs):
    """Runtime owner: modules.session_tech_admit.closed_session_bars."""
    from modules.session_tech_admit import closed_session_bars as _impl
    return _impl(*args, **kwargs)


def _sum_volume(*args, **kwargs):
    """Runtime owner: modules.session_tech_admit._sum_volume."""
    from modules.session_tech_admit import _sum_volume as _impl
    return _impl(*args, **kwargs)


def _close_n_ago(*args, **kwargs):
    """Runtime owner: modules.session_tech_admit._close_n_ago."""
    from modules.session_tech_admit import _close_n_ago as _impl
    return _impl(*args, **kwargs)


def _bar_structure(*args, **kwargs):
    """Runtime owner: modules.session_tech_admit._bar_structure."""
    from modules.session_tech_admit import _bar_structure as _impl
    return _impl(*args, **kwargs)


def _consecutive_directional_bars(*args, **kwargs):
    """Runtime owner: modules.session_tech_admit._consecutive_directional_bars."""
    from modules.session_tech_admit import _consecutive_directional_bars as _impl
    return _impl(*args, **kwargs)


def _hh_ll_counts(*args, **kwargs):
    """Runtime owner: modules.session_tech_admit._hh_ll_counts."""
    from modules.session_tech_admit import _hh_ll_counts as _impl
    return _impl(*args, **kwargs)


def derive_participation_features(*args, **kwargs):
    """Runtime owner: modules.session_tech_admit.derive_participation_features."""
    from modules.session_tech_admit import derive_participation_features as _impl
    return _impl(*args, **kwargs)


def derive_candle_structure_features(*args, **kwargs):
    """Runtime owner: modules.session_tech_admit.derive_candle_structure_features."""
    from modules.session_tech_admit import derive_candle_structure_features as _impl
    return _impl(*args, **kwargs)


def derive_break_geometry_features(*args, **kwargs):
    """Runtime owner: modules.session_tech_admit.derive_break_geometry_features."""
    from modules.session_tech_admit import derive_break_geometry_features as _impl
    return _impl(*args, **kwargs)


def derive_underlying_l1_features(*args, **kwargs):
    """Runtime owner: modules.session_tech_admit.derive_underlying_l1_features."""
    from modules.session_tech_admit import derive_underlying_l1_features as _impl
    return _impl(*args, **kwargs)


def _sample_near_at(*args, **kwargs):
    """Runtime owner: modules.session_tech_admit._sample_near_at."""
    from modules.session_tech_admit import _sample_near_at as _impl
    return _impl(*args, **kwargs)


def derive_option_l1_features(*args, **kwargs):
    """Runtime owner: central_tech.option_l1.derive_option_l1_features."""
    from central_tech.option_l1 import derive_option_l1_features as _impl
    return _impl(*args, **kwargs)


def _path_samples_for_symbol(*args, **kwargs):
    """Runtime owner: central_tech.chain_ring._path_samples_for_symbol."""
    from central_tech.chain_ring import _path_samples_for_symbol as _impl
    return _impl(*args, **kwargs)


def derive_chain_trajectory_features(*args, **kwargs):
    """Runtime owner: central_tech.chain_ring.derive_chain_trajectory_features."""
    from central_tech.chain_ring import derive_chain_trajectory_features as _impl
    return _impl(*args, **kwargs)


def _latest_quote_from_system(*args, **kwargs):
    """Runtime owner: modules.session_tech_admit._latest_quote_from_system."""
    from modules.session_tech_admit import _latest_quote_from_system as _impl
    return _impl(*args, **kwargs)


# Keys Skip Signals / suggested QGs need at admit. Filled from local bars,
# cached quotes, and the Layer C chain fetch — not a full PO snapshot.
QG_ADMIT_KEYS: tuple[str, ...] = (
    # Oct 7 2026: 10.5 chain vol/OI ratio keys (kept in sync with qg_admit_snapshot).
    "total_chain_vol_oi",
    "call_chain_vol_oi",
    "put_chain_vol_oi",
    "session_vs_avg_volume",
    "session_vs_avg",
    "implied_volatility",
    "iv",
    "representative_iv",
    "seconds_since_break",
    "direction_volume_pct",
    "call_chain_volume",
    "put_chain_volume",
    "call_chain_volume_delta_5m",
    "put_chain_volume_delta_5m",
    "same_side_chain_volume_delta_5m",
    "opposite_side_chain_volume_delta_5m",
    "dir_pct_change_5m",
    "owned_contract_volume_delta_5m",
    "owned_contract_oi_change",
    "same_side_chain_volume",
    "same_side_chain_volume_5m_ago",
    "direction_volume_pct_5m_ago",
    "rs_vs_spy",
    "spy_change_pct",
    "spy_price",
    "vwap_distance_pct",
    "abs_vwap_distance_pct",
    "vwap_position",
    "vwap_with",
    "volume_ratio",
    "rsi",
    "bollinger_position",
    "bollinger_width",
    "l1_delta_5s_with",
    "underlying_l1_imbalance",
    "underlying_l1_imbalance_delta_5s",
    "volume_vs_prior_1m",
    "average_volume",
    "trendline_slot",
    "break_timestamp",
    "close_at_break",
    "material_break_at",
    "break_distance_atr",
    "price_volume_efficiency_1m",
    "price_volume_efficiency_5m",
    "entry_bar_body_pct",
    "entry_bar_upper_wick_pct",
    "entry_bar_lower_wick_pct",
    "entry_bar_close_position",
    "otm_pct",
    "delta",
    "chosen_delta",
    "representative_delta",
    "representative_strike",
    "wick_against",
    "close_with",
    # 9.21 Path SS admit keys (PC-verified knives)
    "atr",
    "avg_bid_ask_spread_pct",
    "break_distance_pct",
    "call_chain_open_interest",
    "chain_sanity_best_same_side_spread_pct",
    "chain_sanity_best_same_side_volume",
    "consecutive_directional_bars",
    "cumulative_volume",
    "distance_from_orb_edge_atr",
    "distance_from_vwap_atr",
    "entry_bar_range_pct",
    "gamma",
    "macd",
    "macd_signal",
    "minutes_from_730_to_break",
    "momentum_10",
    "open_interest",
    "option_ask_size",
    "option_bid_size",
    "option_l1_imbalance",
    "orb_range_pct",
    "orb_volume_ratio",
    "price_change_1m_pct",
    "price_change_3m_pct",
    "price_change_5m_pct",
    "price_volume_efficiency_3m",
    "put_call_volume_ratio",
    "put_chain_open_interest",
    "quality_score",
    "recent_higher_high_count",
    "relative_volume_time_adjusted",
    "representative_bid",
    "representative_ask",
    "representative_spread_pct",
    "session_bar_count",
    "session_volume",
    "spread_candidate_count",
    "theta",
    "total_chain_open_interest",
    "total_chain_volume",
    "underlying_ask_size",
    "underlying_bid_size",
    "underlying_l1_imbalance_delta_15s",
    "underlying_quote_age_sec",
    "underlying_spread_pct",
    "vega",
    "volume_1m",
    "volume_3m",
    "volume_5m",
    "volume_acceleration_1m_vs_5m",
    "volume_pct_of_day_5m",
    "volume_rate_1m",
    "volume_vs_prior_5m",
)


def _qg_admit_json_value(*args, **kwargs):
    """Runtime owner: modules.qg_admit_snapshot._qg_admit_json_value."""
    from modules.qg_admit_snapshot import _qg_admit_json_value as _impl
    return _impl(*args, **kwargs)


def collect_qg_admit_snapshot(*args, **kwargs):
    """Runtime owner: modules.qg_admit_snapshot.collect_qg_admit_snapshot."""
    from modules.qg_admit_snapshot import collect_qg_admit_snapshot as _impl
    return _impl(*args, **kwargs)


def apply_qg_admit_snapshot(*args, **kwargs):
    """Runtime owner: modules.qg_admit_snapshot.apply_qg_admit_snapshot."""
    from modules.qg_admit_snapshot import apply_qg_admit_snapshot as _impl
    return _impl(*args, **kwargs)


def promote_qg_admit_fields(*args, **kwargs):
    """Runtime owner: modules.qg_admit_snapshot.promote_qg_admit_fields."""
    from modules.qg_admit_snapshot import promote_qg_admit_fields as _impl
    return _impl(*args, **kwargs)


def _qg_admit_is_put_side(*args, **kwargs):
    """Runtime owner: modules.session_tech_admit._qg_admit_is_put_side."""
    from modules.session_tech_admit import _qg_admit_is_put_side as _impl
    return _impl(*args, **kwargs)


def _stamp_path_ss_local_aliases(*args, **kwargs):
    """Runtime owner: modules.session_tech_admit._stamp_path_ss_local_aliases."""
    from modules.session_tech_admit import _stamp_path_ss_local_aliases as _impl
    return _impl(*args, **kwargs)


def _minutes_from_730_to_break(*args, **kwargs):
    """Runtime owner: modules.session_tech_admit._minutes_from_730_to_break."""
    from modules.session_tech_admit import _minutes_from_730_to_break as _impl
    return _impl(*args, **kwargs)


def _stamp_path_ss_derived_fields(*args, **kwargs):
    """Runtime owner: modules.session_tech_admit._stamp_path_ss_derived_fields."""
    from modules.session_tech_admit import _stamp_path_ss_derived_fields as _impl
    return _impl(*args, **kwargs)


def _stamp_break_distance_pct_fraction(*args, **kwargs):
    """Runtime owner: modules.session_tech_admit._stamp_break_distance_pct_fraction."""
    from modules.session_tech_admit import _stamp_break_distance_pct_fraction as _impl
    return _impl(*args, **kwargs)


def _stamp_pve_3m_from_row(*args, **kwargs):
    """Runtime owner: modules.session_tech_admit._stamp_pve_3m_from_row."""
    from modules.session_tech_admit import _stamp_pve_3m_from_row as _impl
    return _impl(*args, **kwargs)


def _stamp_spy_rs_from_quote_cache(*args, **kwargs):
    """Runtime owner: modules.session_tech_admit._stamp_spy_rs_from_quote_cache."""
    from modules.session_tech_admit import _stamp_spy_rs_from_quote_cache as _impl
    return _impl(*args, **kwargs)


def _stamp_path_ss_from_cached_chain(*args, **kwargs):
    """Runtime owner: modules.session_tech_admit._stamp_path_ss_from_cached_chain."""
    from modules.session_tech_admit import _stamp_path_ss_from_cached_chain as _impl
    return _impl(*args, **kwargs)


def _fill_missing_session_tech_from_cached_bars(*args, **kwargs):
    """Runtime owner: modules.session_tech_admit._fill_missing_session_tech_from_cached_bars."""
    from modules.session_tech_admit import _fill_missing_session_tech_from_cached_bars as _impl
    return _impl(*args, **kwargs)


def _restamp_break_geometry_if_needed(*args, **kwargs):
    """Runtime owner: modules.session_tech_admit._restamp_break_geometry_if_needed."""
    from modules.session_tech_admit import _restamp_break_geometry_if_needed as _impl
    return _impl(*args, **kwargs)


def _break_ts_candidates(*args, **kwargs):
    """Runtime owner: modules.session_tech_admit._break_ts_candidates."""
    from modules.session_tech_admit import _break_ts_candidates as _impl
    return _impl(*args, **kwargs)


def refresh_seconds_since_break(*args, **kwargs):
    """Runtime owner: modules.session_tech_admit.refresh_seconds_since_break."""
    from modules.session_tech_admit import refresh_seconds_since_break as _impl
    return _impl(*args, **kwargs)


def stamp_qg_admit_fields(*args, **kwargs):
    """Runtime owner: modules.session_tech_admit.stamp_admit_tech."""
    from modules.session_tech_admit import stamp_admit_tech as _impl
    return _impl(*args, **kwargs)


def _stamp_premium_trajectory_v5(
    system: Any,
    row: Dict[str, Any],
    *,
    now_epoch: float,
    overwrite: bool,
) -> int:
    """Owned-contract 30s/5m premium trajectory. Null when the path has no clean history."""
    try:
        from .premium_trajectory_v5 import apply_premium_trajectory_v5
    except Exception:
        return 0
    try:
        return int(
            apply_premium_trajectory_v5(
                system,
                row,
                now_epoch=float(now_epoch),
                overwrite=bool(overwrite),
            )
            or 0
        )
    except Exception:
        return 0


def _snapshot_event_epoch(row: Dict[str, Any]) -> float:
    """t172u: a live snapshot's own observation clock (event time, else now)."""
    for k in ("po_snapshot_event_at", "po_20p_event_at", "po_exit_event_at", "po_tech_asof_at"):
        raw = row.get(k)
        if raw in (None, ""):
            continue
        try:
            dt_ = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if dt_.tzinfo is None:
                dt_ = dt_.replace(tzinfo=timezone.utc)
            return dt_.timestamp()
        except Exception:
            continue
    return time.time()


def stamp_po_observe_trajectory(
    system: Any,
    row: Dict[str, Any],
    *,
    symbol: Optional[str] = None,
    overwrite: bool = False,
    decision_freeze: Optional[bool] = None,
) -> int:
    """
    Stamp observe-only trajectory / L1 / participation fields onto a PO row.

    Entry (decision) mode uses closed 1m bars and ``po_decision_freeze_at``.
    Executed rows that carried handoff trajectory set ``po_skip_trajectory_restamp``
    and only fill option-L1 / missing chain-Δ gaps (decision freeze preserved).
    Exit snaps always restamp live (including chain now / ~5m-ago keys) so exit
    research is not frozen to entry decision deltas.
    Never triggers broker chain fetches.
    """
    if not isinstance(row, dict) or system is None:
        return 0
    sym = str(symbol or row.get("symbol") or "").strip().upper()
    if not sym:
        return 0

    stage = str(row.get("stage") or row.get("po_snapshot_stage") or "")
    is_exit_stage = "exited" in stage or str(row.get(_TRAJECTORY_MODE) or "") == "live"

    # Exit / live: always recompute (do not honor entry handoff skip-restamp).
    if is_exit_stage:
        row.pop(_SKIP_RESTAMP, None)
        row[_TRAJECTORY_MODE] = "live"
        decision_freeze = False
        overwrite = True

    # Handoff → executed carry-forward. Bar clocks stay at the gate.
    # Chain trajectory already on the row is the gate measurement and stays.
    # Only a missing key is filled, and that fill uses the decision freeze
    # when the row has one.
    elif row.get(_SKIP_RESTAMP) and row.get("po_observe_trajectory_stamp") and not overwrite:
        n = 0
        freeze_ts = _parse_ts(row.get(_DECISION_FREEZE_AT))
        try:
            record_chain_skew_sample(system, sym, row)
        except Exception:
            pass
        try:
            chain_blob = derive_chain_trajectory_features(
                system,
                row,
                symbol=sym,
                freeze_ts=freeze_ts,
            )
        except Exception:
            chain_blob = {}
        _owned = _dir5m_merge_group(row, chain_blob, overwrite=False)
        n += _owned[0]
        for k in _CHAIN_TRAJECTORY_KEYS:
            v = chain_blob.get(k)
            if k in _owned[1] or v is None or row.get(k) is not None:
                continue
            row[k] = v
            n += 1
        for k, v in chain_blob.items():
            if k in _CHAIN_TRAJECTORY_KEYS or k in _owned[1] or v is None or row.get(k) is not None:
                continue
            row[k] = v
            n += 1
        opt = derive_option_l1_features(row)
        for k, v in opt.items():
            if v is not None and row.get(k) is None:
                row[k] = v
                n += 1
        n += _stamp_side_signed_aliases(row)
        n += _stamp_path_ss_local_aliases(row)
        # Carried entry trajectory stays on the decision clock. A fill-time
        # premium path must not replace anchors already frozen at handoff.
        prem_now = freeze_ts.timestamp() if freeze_ts is not None else None
        if prem_now is not None:
            wrote = _stamp_premium_trajectory_v5(
                system, row, now_epoch=prem_now, overwrite=False
            )
            n += wrote
            if wrote and row.get("po_premium_path_asof_at") in (None, ""):
                row["po_premium_path_asof_at"] = freeze_ts.isoformat()
        if n:
            try:
                snap = collect_qg_admit_snapshot(row)
                if snap:
                    row["qg_admit"] = snap
            except Exception:
                pass
        return n

    if decision_freeze is None:
        decision_freeze = (
            str(row.get(_TRAJECTORY_MODE) or "") == "decision"
            or bool(row.get(_DECISION_FREEZE_AT))
            or "emit_handoff" in stage
            or stage.endswith("_handoff")
            or "options_executed" in stage
        )
    # Exit observations stay live (forming bar OK).
    if is_exit_stage:
        decision_freeze = False

    freeze_ts = _parse_ts(row.get(_DECISION_FREEZE_AT))
    if decision_freeze and freeze_ts is None:
        freeze_ts = mark_decision_freeze(row)
    elif decision_freeze and freeze_ts is not None:
        row.setdefault(_TRAJECTORY_MODE, "decision")

    # Gate-accurate volume / same-side dir% before trajectory + qg_admit collect.
    try:
        from modules.volume_data import ensure_volume_research_fields

        ensure_volume_research_fields(row)
    except Exception:
        pass

    quote = None if decision_freeze else _latest_quote_from_system(system, sym)
    if quote is None and not decision_freeze:
        quote = _latest_quote_from_system(system, sym)

    raw_bars = _bars_for_symbol(system, sym)
    bars = closed_session_bars(raw_bars, as_of=freeze_ts) if decision_freeze else list(raw_bars)

    vol_map = getattr(system, "_session_tech_last_session_volume", None)
    session_volume = None
    if isinstance(vol_map, dict):
        session_volume = _f(vol_map.get(sym))
    if session_volume is None:
        q = quote or _latest_quote_from_system(system, sym)
        if isinstance(q, dict):
            session_volume = _f(
                q.get("totalVolume") if q.get("totalVolume") is not None else q.get("volume")
            )
    average_volume = _f(quote.get("average_volume")) if isinstance(quote, dict) else None
    if average_volume is None:
        q2 = _latest_quote_from_system(system, sym)
        average_volume = _f(q2.get("average_volume")) if isinstance(q2, dict) else None
    if average_volume is None:
        average_volume = _f(row.get("average_volume"))

    blobs = [
        derive_underlying_l1_features(
            system, sym, quote=quote, freeze_ts=freeze_ts if decision_freeze else None
        ),
        derive_option_l1_features(row),
        derive_participation_features(
            bars,
            session_volume=session_volume,
            average_volume=average_volume,
            now=freeze_ts if decision_freeze else None,
        ),
        derive_candle_structure_features(bars),
        derive_break_geometry_features(
            row, bars, freeze_ts=freeze_ts if decision_freeze else None
        ),
        derive_chain_trajectory_features(
            system,
            row,
            symbol=sym,
            freeze_ts=freeze_ts if decision_freeze else None,
            asof_ts=None if decision_freeze else _snapshot_event_epoch(row),
        ),
    ]

    stamped = 0
    # t217u: the chain blob (last) carries the dir5m group; merge it atomically
    # unless DIR5M_TELEMETRY_ATOMIC=false (default on since 10/17).
    _w, _owned = _dir5m_merge_group(row, blobs[-1], overwrite=overwrite)
    stamped += _w
    for blob in blobs:
        for k, v in blob.items():
            if v is None or k in _owned:
                continue
            if overwrite or row.get(k) is None:
                row[k] = v
                stamped += 1
    # Side-signed Path SS / QG aliases must land on entry densify (not only admit stamp).
    stamped += _stamp_side_signed_aliases(row)
    stamped += _stamp_path_ss_local_aliases(row)
    stamped += _stamp_path_ss_derived_fields(row)
    stamped += _stamp_spy_rs_from_quote_cache(system, row, sym)
    stamped += _stamp_path_ss_from_cached_chain(system, row, sym)
    stamped += _stamp_path_ss_derived_fields(row)
    now_epoch = freeze_ts.timestamp() if decision_freeze and freeze_ts is not None else time.time()
    premium_asof = None
    if not decision_freeze:
        event_raw = (
            row.get("po_snapshot_event_at")
            or row.get("po_20p_event_at")
            or row.get("po_exit_event_at")
        )
        if event_raw not in (None, ""):
            try:
                event_dt = datetime.fromisoformat(str(event_raw).replace("Z", "+00:00"))
                now_epoch = event_dt.timestamp()
                premium_asof = event_dt.isoformat()
            except Exception:
                pass
    elif freeze_ts is not None:
        premium_asof = freeze_ts.isoformat()
    prem_wrote = _stamp_premium_trajectory_v5(
        system,
        row,
        now_epoch=now_epoch,
        overwrite=bool(overwrite or is_exit_stage),
    )
    stamped += prem_wrote
    if prem_wrote and premium_asof and row.get("po_premium_path_asof_at") in (None, ""):
        row["po_premium_path_asof_at"] = premium_asof
    if stamped:
        row["po_observe_trajectory_stamp"] = True
        try:
            snap = collect_qg_admit_snapshot(row)
            if snap:
                row["qg_admit"] = snap
        except Exception:
            pass
    try:
        from modules.po_snapshot_hydration import ensure_path_signal_stamps

        ensure_path_signal_stamps(row)
    except Exception:
        try:
            from .po_snapshot_hydration import ensure_path_signal_stamps

            ensure_path_signal_stamps(row)
        except Exception:
            pass
    return stamped


def _stamp_side_signed_aliases(*args, **kwargs):
    """Runtime owner: modules.session_tech_admit._stamp_side_signed_aliases."""
    from modules.session_tech_admit import _stamp_side_signed_aliases as _impl
    return _impl(*args, **kwargs)
