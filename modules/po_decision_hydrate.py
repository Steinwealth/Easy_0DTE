"""Fill Entry blanks from state the decision already holds. No broker call.

The live signal, its metadata, the QG admit blob, and a Layer C skew already
copied onto that row are decision-time sources. A blank research key may take
the first of those. It may not take a later chain read.
"""

from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, Optional

from .po_snapshot_hydration import carry_decision_moon_deltas
from .po_source_contract import (
    CLOCK_NOT_RECORDED,
    DECISION_PATH_DID_NOT_COLLECT,
    observation_is_usable,
)
from .po_temporal_contract import ENTRY_LOCK_KEYS, TP_PROFILE_EXTRA_KEYS


def _dicts(*values: Any) -> Iterable[Dict[str, Any]]:
    for value in values:
        if isinstance(value, dict):
            yield value


def absorb_in_process_decision_state(row: Optional[Dict[str, Any]]) -> int:
    """Copy decision-time values already on the row onto blank research keys.

    Returns how many blanks were filled. Does not replace a present value and
    does not call the broker.
    """
    if not isinstance(row, dict):
        return 0
    md = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    setup = row.get("setup_payload") if isinstance(row.get("setup_payload"), dict) else {}
    if not setup and isinstance(md, dict):
        setup = md.get("setup_payload") if isinstance(md.get("setup_payload"), dict) else {}
    qg = row.get("qg_admit") if isinstance(row.get("qg_admit"), dict) else {}
    if not qg and isinstance(md, dict):
        qg = md.get("qg_admit") if isinstance(md.get("qg_admit"), dict) else {}
    carry_decision_moon_deltas(row, md, setup, qg)
    written = 0
    try:
        from .po_snapshot_cache_hydrate import extract_options_features_from_row
    except Exception:
        extract_options_features_from_row = None  # type: ignore
    sources = []
    if extract_options_features_from_row is not None:
        for src in _dicts(md, setup, qg):
            sources.append(extract_options_features_from_row(src))
    for src in _dicts(md, setup, qg, *sources):
        for key in ENTRY_LOCK_KEYS:
            if row.get(key) not in (None, ""):
                continue
            val = src.get(key)
            if val in (None, ""):
                continue
            row[key] = val
            written += 1
    return written


def has_source_clocks(row: Dict[str, Any]) -> bool:
    return any(
        row.get(clock) not in (None, "")
        for clock in (
            "po_chain_asof_at",
            "po_session_tech_asof_at",
            "po_quote_asof_at",
            "po_spy_asof_at",
        )
    )


def copy_sealed_research_state(dest: Dict[str, Any], gate: Dict[str, Any]) -> int:
    """Put the handoff research state onto an executed row. Fill identity stays."""
    n = 0
    for key in ENTRY_LOCK_KEYS:
        val = gate.get(key)
        shared = isinstance(val, (dict, list)) and dest.get(key) is val
        copied = copy.deepcopy(val) if isinstance(val, (dict, list)) else val
        if shared or dest.get(key) != copied:
            dest[key] = copied
            n += 1
    for outcome_key in ("exit_underlying", "hold_exit_dir_pct", "peak_price", "peak_pct"):
        if outcome_key not in ENTRY_LOCK_KEYS or gate.get(outcome_key) in (None, ""):
            dest[outcome_key] = gate.get(outcome_key)
    for clock in (
        "po_decision_freeze_at",
        "po_decision_freeze_mode",
        "po_snapshot_event_at",
        "po_chain_asof_at",
        "po_session_tech_asof_at",
        "po_quote_asof_at",
        "po_spy_asof_at",
        "po_premium_path_asof_at",
        "po_sma_horizon",
        "po_clock_gaps",
    ):
        if clock in gate:
            dest[clock] = gate.get(clock)
    try:
        from .po_snapshot_hydration import attach_tp_profile_extras

        attach_tp_profile_extras(dest, gate)
    except Exception:
        for key in TP_PROFILE_EXTRA_KEYS:
            if dest.get(key) in (None, "") and gate.get(key) not in (None, ""):
                dest[key] = gate.get(key)
    dest["po_tech_asof_at"] = dest.get("po_decision_freeze_at")
    dest["po_entry_sealed"] = True
    if has_source_clocks(dest):
        dest["po_tech_asof"] = "gate"
        dest["po_entry_training_eligible"] = True
    else:
        dest["po_tech_asof"] = "executed_unsealed"
        dest["po_entry_training_eligible"] = False
    return n


def reject_future_observation(
    source_asof: Optional[str],
    event_at: Optional[str],
) -> str:
    """Return a null reason when the source is after the event, else empty."""
    ok, reason = observation_is_usable(source_asof, event_at)
    if ok:
        return ""
    if reason == CLOCK_NOT_RECORDED:
        return CLOCK_NOT_RECORDED
    return reason or DECISION_PATH_DID_NOT_COLLECT


def session_sma_for_po(tech: Optional[Dict[str, Any]], key: str) -> Optional[float]:
    """Session 1-minute SMA only. A shorter average is not sma_20 or sma_50.

    Broker daily SMA is a different measurement and must not be returned here.
    """
    if not isinstance(tech, dict) or key not in ("sma_20", "sma_50"):
        return None
    need = 20 if key == "sma_20" else 50
    try:
        bars = int(tech.get("session_bar_count") or 0)
    except (TypeError, ValueError):
        return None
    if bars < need:
        return None
    raw = tech.get(key)
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


# Family clock on the snapshot, and the source fields that may already carry
# the observation time. Cache age is not a timestamp. A quote clock is not a
# SPY clock. The snapshot write time is not a source time.
_SOURCE_CLOCK_FIELDS = {
    "session_tech": (
        "po_session_tech_asof_at",
        ("session_tech_asof_at", "pve3_bar_at", "session_tech_bar_at", "session_bar_open_at"),
    ),
    "quote": (
        "po_quote_asof_at",
        ("quote_observed_at", "underlying_observation_timestamp"),
    ),
    "spy": (
        "po_spy_asof_at",
        ("spy_observed_at", "spy_quote_timestamp"),
    ),
    "chain": (
        "po_chain_asof_at",
        ("chain_observed_at",),
    ),
    "premium_path": (
        "po_premium_path_asof_at",
        (),
    ),
}


def _as_utc_iso(raw: Any) -> Optional[str]:
    if raw in (None, ""):
        return None
    try:
        from datetime import datetime, timezone

        text = str(raw).strip().replace("Z", "+00:00")
        parsed = datetime.fromisoformat(text)
    except Exception:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def attach_cached_source_clocks(
    row: Optional[Dict[str, Any]],
    *,
    system: Any = None,
    symbol: str = "",
) -> None:
    """Copy clocks already stored on the quote cache. Does not read the broker."""
    if not isinstance(row, dict) or system is None:
        return
    sym = str(symbol or row.get("symbol") or "").strip().upper()
    try:
        from .underlying_spot_stamp import lookup_underlying_observed_at
    except Exception:
        return
    if sym and row.get("quote_observed_at") in (None, ""):
        observed = lookup_underlying_observed_at(system, sym)
        if not observed:
            try:
                from .spy_session_quote import symbol_observed_at

                observed = symbol_observed_at(sym)
            except Exception:
                observed = None
        if observed:
            row["quote_observed_at"] = observed
    if row.get("spy_observed_at") in (None, ""):
        spy_observed = lookup_underlying_observed_at(system, "SPY")
        if not spy_observed:
            try:
                from .spy_session_quote import spy_snapshot

                pinned = spy_snapshot()
            except Exception:
                pinned = None
            if isinstance(pinned, dict):
                spy_observed = pinned.get("spy_observed_at")
                if row.get("spy_change_pct") in (None, "") and pinned.get("spy_change_pct") not in (None, ""):
                    row["spy_change_pct"] = pinned.get("spy_change_pct")
        if spy_observed:
            row["spy_observed_at"] = spy_observed


_FREEZE_CLOCKS = (
    ("chain", "po_chain_asof_at"),
    ("quote", "po_quote_asof_at"),
    ("spy", "po_spy_asof_at"),
    ("session_tech", "po_session_tech_asof_at"),
)

# Dropped with a future session-tech clock. Values after the event are not
# the measurement at the snapshot. Leave them missing instead of filled.
_SESSION_TECH_NULL_ON_FUTURE = (
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
    "ema_12",
    "ema_26",
    "sma_20",
    "sma_50",
    "vwap",
    "vwap_distance_pct",
    "volume_ratio",
    "price_volume_efficiency_3m",
    "price_change_3m_pct",
    "volume_3m",
    "bollinger_upper",
    "bollinger_middle",
    "bollinger_lower",
    "bollinger_width",
    "bollinger_position",
    "atr",
    "momentum_10",
    "volume_1m",
    "session_volume",
    "underlying_volume_1m",
    "orb_volume_ratio",
)


def _null_future_session_tech(row: Dict[str, Any]) -> None:
    for key in _SESSION_TECH_NULL_ON_FUTURE:
        row[key] = None
    row["rsi_missing"] = True
    row["macd_missing"] = True
    row["vwap_missing"] = True
    row["volume_ratio_missing"] = True
    row.pop("session_tech_asof_at", None)
    row.pop("session_tech_bar_at", None)
    row.pop("pve3_bar_at", None)
    row.pop("po_session_tech_asof_at", None)


# Mid-trade / exit chain older than 60s is not the event measurement.
# Leave the keys missing instead of repeating the previous event.
_CHAIN_NULL_ON_STALE = (
    "call_chain_volume",
    "put_chain_volume",
    "call_volume",
    "put_volume",
    "total_chain_volume",
    "total_chain_open_interest",
    "call_chain_open_interest",
    "put_chain_open_interest",
    "put_call_volume_ratio",
    "put_call_oi_ratio",
    "direction_volume_pct",
    "same_side_chain_volume",
    "same_side_chain_volume_5m_ago",
    "direction_volume_pct_5m_ago",
    "call_chain_volume_delta_5m",
    "put_chain_volume_delta_5m",
    "same_side_chain_volume_delta_5m",
    "opposite_side_chain_volume_delta_5m",
    "representative_volume",
    "representative_open_interest",
    "representative_bid",
    "representative_ask",
    "representative_delta",
    "representative_spread_pct",
    "call_chain_volume_now",
    "put_chain_volume_now",
    "selected_contract_volume_now",
)


def _null_stale_chain(row: Dict[str, Any], *, reason: str = "stale_over_60s") -> None:
    for key in _CHAIN_NULL_ON_STALE:
        row[key] = None
    row["po_chain_source"] = reason
    row.pop("po_chain_asof_at", None)
    row.pop("chain_observed_at", None)


def _mid_or_exit_row(row: Dict[str, Any]) -> bool:
    asof = str(row.get("po_tech_asof") or "")
    if asof in ("", "gate"):
        return False
    return True


def promote_entry_freeze_clocks(payload: Optional[Dict[str, Any]], *, stage: str = "") -> None:
    """Put the decision-freeze clocks on a handoff or executed envelope.

    The freeze already stores gate_at and each source as-of. Some fill files
    kept the freeze object and left the flat clocks blank, which marked the
    row executed_unsealed. Mid-trade families keep their own event clock.
    """
    if not isinstance(payload, dict):
        return
    stage_s = str(stage or payload.get("stage") or payload.get("po_snapshot_stage") or "")
    entry = (
        "emit_handoff" in stage_s
        or stage_s.endswith("_handoff")
        or "options_executed" in stage_s
    )
    if not entry:
        return
    fz = payload.get("po_decision_freeze")
    if not isinstance(fz, dict):
        selected = payload.get("features_selected")
        if isinstance(selected, list) and selected and isinstance(selected[0], dict):
            nested = selected[0].get("po_decision_freeze")
            if isinstance(nested, dict):
                fz = nested
                payload["po_decision_freeze"] = nested
    if not isinstance(fz, dict):
        return
    asof = fz.get("asof") if isinstance(fz.get("asof"), dict) else {}
    for src, clock in _FREEZE_CLOCKS:
        if payload.get(clock) in (None, "") and asof.get(src) not in (None, ""):
            payload[clock] = asof.get(src)
    gate_at = fz.get("gate_at") or payload.get("po_decision_freeze_at")
    if payload.get("po_decision_freeze_at") in (None, "") and gate_at not in (None, ""):
        payload["po_decision_freeze_at"] = gate_at
    if payload.get("po_snapshot_event_at") in (None, "") and gate_at not in (None, ""):
        payload["po_snapshot_event_at"] = gate_at
    if payload.get("po_chain_asof_at") not in (None, "") and payload.get("po_chain_source") == "entry_carry":
        payload.pop("po_chain_source", None)
    clocks_ok = any(payload.get(clock) not in (None, "") for _, clock in _FREEZE_CLOCKS)
    if payload.get("po_tech_asof") == "executed_unsealed" and clocks_ok:
        payload["po_tech_asof"] = "gate"
        if payload.get("po_tech_asof_at") in (None, "") and gate_at not in (None, ""):
            payload["po_tech_asof_at"] = gate_at


def stamp_provenance_clocks(row: Optional[Dict[str, Any]], *, event_at: Optional[str]) -> None:
    """Copy a source clock only when it is known and not after the event.

    A missing source clock stays missing. Cache age is not turned into a
    clock. The symbol quote clock is not copied onto SPY.
    """
    if not isinstance(row, dict) or event_at in (None, ""):
        return
    from .po_source_contract import observation_is_usable

    row["po_snapshot_event_at"] = event_at
    gaps = []
    rejects = []
    for family, (clock, sources) in _SOURCE_CLOCK_FIELDS.items():
        candidate = row.get(clock)
        if candidate in (None, ""):
            for source_key in sources:
                candidate = row.get(source_key)
                if candidate not in (None, ""):
                    break
        if candidate in (None, ""):
            gaps.append(family)
            continue
        normalized = _as_utc_iso(candidate)
        usable, reason = observation_is_usable(normalized, event_at)
        if not usable:
            if row.get(clock) not in (None, ""):
                row.pop(clock, None)
            if reason == "temporal_reject_future_observation":
                rejects.append(family)
                if family == "session_tech":
                    _null_future_session_tech(row)
                if family == "chain" and _mid_or_exit_row(row):
                    _null_stale_chain(row, reason="future_chain")
            gaps.append(family)
            continue
        row[clock] = normalized
        if family == "chain" and _mid_or_exit_row(row):
            try:
                from .po_source_contract import observation_age_s

                age_s = observation_age_s(normalized, event_at)
            except Exception:
                age_s = None
            try:
                from central_tech.decision_freeze import chain_max_age_sec

                chain_cap = float(chain_max_age_sec())
            except Exception:
                chain_cap = 60.0
            if age_s is not None and age_s > chain_cap:
                _null_stale_chain(row, reason="stale_over_60s")
                gaps.append(family)
    # ``underlying_stamped_at`` is the schema clock for the underlying quote.
    # stamp_underlying_research_fields only copies quote_observed_at, and on
    # entry/exit rows it runs before attach_cached_source_clocks, so the
    # schema key stayed 100% null on Oct 5 while po_quote_asof_at was set on
    # 25/33 handoffs and every exit. Use the accepted quote clock (never a
    # clock after the event, never cache age).
    quote_clock = row.get("po_quote_asof_at")
    if quote_clock not in (None, "") and row.get("underlying_stamped_at") in (None, ""):
        row["underlying_stamped_at"] = quote_clock
    if gaps:
        row["po_clock_gaps"] = gaps
    else:
        row.pop("po_clock_gaps", None)
    if rejects:
        row["po_clock_rejects"] = rejects
    else:
        row.pop("po_clock_rejects", None)
    # Age 120s and first red do not take a new chain. Copied volumes are
    # not the measurement at this event — leave them missing.
    already = str(row.get("po_chain_source") or "")
    if (
        _mid_or_exit_row(row)
        and row.get("po_chain_asof_at") in (None, "")
        and already not in ("stale_over_60s", "future_chain")
        and (
            row.get("call_chain_volume") not in (None, "")
            or row.get("put_chain_volume") not in (None, "")
            or already in ("", "entry_carry")
        )
    ):
        _null_stale_chain(row, reason="entry_carry")
