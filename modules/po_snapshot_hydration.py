"""
Priority Optimizer snapshot hydration helpers (Rev 00402).

Ensures execution/exit snapshots carry critical technicals + options chain
fields needed to review Call/Put entry and exit timing.
"""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .po_feature_schema import (
    FEATURE_KEYS_0DTE_UNIFIED,
    FEATURE_KEYS_ENTRY_EXIT_CRITICAL,
    FEATURE_KEYS_OPTIONS_MICROSTRUCTURE,
)

# Compact / legacy trade ids: GOOGL-347.5p-774 · D-NVDA-207.5c-451 · L-AMZN-250c-069 · D-260724-AAPL-335c-821
_TRADE_ID_UNDERLYING = re.compile(
    r"^(?:[DL]-)?(?:\d{6}-)?([A-Z]{1,10})(?:[-_/]|$)",
    re.IGNORECASE,
)

# Keys safe to carry from entry/break into exit (then overlay fresh spot/options).
def _carry_forward_key_list() -> Tuple[str, ...]:
    """Identity, decision-frozen research, and fill identity only.

    A 20p or exit row must not keep an Entry reading in a field that is
    supposed to be the market at that later event. Those fields stay null
    until the event stamp writes them.
    """
    from .po_temporal_contract import (
        ENTRY_DECISION_FROZEN,
        ENTRY_FILL_IDENTITY,
        IMMUTABLE_IDENTITY,
        temporal_class,
    )

    keep = {IMMUTABLE_IDENTITY, ENTRY_DECISION_FROZEN, ENTRY_FILL_IDENTITY}
    return tuple(k for k in FEATURE_KEYS_0DTE_UNIFIED if temporal_class(k) in keep)


_CARRY_FORWARD_KEYS: Tuple[str, ...] = _carry_forward_key_list()


def resolve_underlying_symbol(
    *,
    symbol: str = "",
    trade_id: str = "",
    position: Any = None,
) -> str:
    """Resolve equity underlying for PO enrich (never leave blank when trade_id known)."""
    candidates: list[str] = []
    for raw in (symbol, getattr(position, "symbol", None) if position is not None else None):
        cand = str(raw or "").strip().upper()
        if cand:
            candidates.append(cand)
    if position is not None:
        for attr in ("underlying", "underlying_symbol"):
            cand = str(getattr(position, attr, "") or "").strip().upper()
            if cand:
                candidates.append(cand)
        meta = getattr(position, "metadata", None) or {}
        if isinstance(meta, dict):
            for key in ("underlying", "underlying_symbol", "symbol"):
                cand = str(meta.get(key) or "").strip().upper()
                if cand:
                    candidates.append(cand)
            sig = meta.get("signal_snapshot") if isinstance(meta.get("signal_snapshot"), dict) else {}
            cand = str(sig.get("symbol") or "").strip().upper()
            if cand:
                candidates.append(cand)
    for cand in candidates:
        if cand and not _looks_like_occ_option(cand):
            return cand
    m = _TRADE_ID_UNDERLYING.match(str(trade_id or "").strip())
    if m:
        return m.group(1).upper()
    return candidates[0] if candidates else ""


def _looks_like_occ_option(sym: str) -> bool:
    """Heuristic: OCC roots are longer and embed digits; underlyings used in PO are short."""
    s = str(sym or "")
    if len(s) <= 6:
        return False
    return any(ch.isdigit() for ch in s)


def backfill_chain_totals_from_sides(row: Dict[str, Any]) -> None:
    """When Convex only stamped per-side OI/vol, derive totals for schema completeness."""
    if not isinstance(row, dict):
        return
    if row.get("total_chain_open_interest") is None:
        try:
            c = row.get("call_chain_open_interest")
            p = row.get("put_chain_open_interest")
            if c is not None and p is not None:
                row["total_chain_open_interest"] = int(c) + int(p)
        except (TypeError, ValueError):
            pass
    if row.get("total_chain_volume") is None:
        try:
            c = row.get("call_chain_volume")
            p = row.get("put_chain_volume")
            if c is not None and p is not None:
                row["total_chain_volume"] = int(c) + int(p)
        except (TypeError, ValueError):
            pass


def merge_features_into_row(
    dest: Dict[str, Any],
    src: Optional[Dict[str, Any]],
    keys: Optional[Sequence[str]] = None,
    *,
    only_if_missing: bool = True,
) -> int:
    """Copy feature keys from src → dest. Returns count of keys written."""
    if not isinstance(dest, dict) or not isinstance(src, dict):
        return 0
    written = 0
    use_keys: Iterable[str] = keys if keys is not None else src.keys()
    for key in use_keys:
        val = src.get(key)
        if val is None or val == "":
            continue
        if only_if_missing and dest.get(key) is not None and dest.get(key) != "":
            continue
        dest[key] = val
        written += 1
    return written


# Decision, moment, and path-history keys. A post-fill chain or owned-contract
# stamp must not replace these on an entry file. Fill identity
# (fill_delta, chosen_delta, strike, entry premium) is not in this set.
from .po_temporal_contract import ENTRY_LOCK_KEYS, TP_PROFILE_EXTRA_KEYS

GATE_TECH_KEYS = ENTRY_LOCK_KEYS


def stamp_entry_mode(row: Optional[Dict[str, Any]], *, path: str = "", source: Any = None) -> None:
    """Keep the admission entry_mode on later snapshots. Do not invent a new mode."""
    if not isinstance(row, dict):
        return
    if str(row.get("entry_mode") or "").strip():
        return
    pools: List[Any] = [source, row.get("metadata"), row.get("setup_payload")]
    for src in pools:
        if not isinstance(src, dict):
            continue
        em = src.get("entry_mode")
        if str(em or "").strip():
            row["entry_mode"] = em
            return
        if src.get("early_entry_mode") is True:
            row["entry_mode"] = "EARLY_ENTRY"
            return
    text = str(path or row.get("strategy_type") or row.get("setup_type") or "").lower()
    if "early" in text:
        row["entry_mode"] = "EARLY_ENTRY"
    elif "trendline" in text or text.startswith("tl"):
        row["entry_mode"] = "simple_break"
    elif "breakout" in text:
        row["entry_mode"] = "breakout"
    elif "orb" in text:
        row["entry_mode"] = "orb1"


# Moment restamp only. Never dir_pct_change_5m — that key stays phased and
# must come from the causal chain ring, not a tape ratio on the owned tick.
TP_MOMENT_CACHE_KEYS: Tuple[str, ...] = (
    "call_chain_volume",
    "put_chain_volume",
    "call_chain_open_interest",
    "put_chain_open_interest",
    "total_chain_vol_oi",
    "call_chain_vol_oi",
    "put_chain_vol_oi",
    "call_share",
    "put_share",
    "break_distance_pct",
    "price_volume_efficiency_3m",
    "atr",
    "distance_from_vwap_atr",
    "rsi",
    "spy_change_pct",
    "underlying_l1_imbalance",
    "option_l1_imbalance",
    "chain_sanity_best_same_side_spread_pct",
    "delta",
    "otm_pct",
    "iv",
    "theta",
    "gamma",
    "vega",
    "bollinger_position",
    "bollinger_width",
)


def fill_missing_tp_keys_from_osnap(
    dest: Optional[Dict[str, Any]],
    osnap: Optional[Dict[str, Any]],
) -> int:
    """Copy live owned-tick TP keys onto a snapshot row when the row is blank.

    The monitor already stamps these on option_stealth. Persist must reuse
    them. Does not copy dir_pct_change_5m.
    """
    if not isinstance(dest, dict) or not isinstance(osnap, dict):
        return 0
    n = 0
    for key in TP_MOMENT_CACHE_KEYS:
        if dest.get(key) not in (None, ""):
            continue
        val = osnap.get(key)
        if val not in (None, ""):
            dest[key] = val
            n += 1
    if dest.get("price_volume_efficiency_3m") in (None, "") and osnap.get("pve3") not in (None, ""):
        dest["price_volume_efficiency_3m"] = osnap.get("pve3")
        n += 1
    # The owned tick already has the bar and quote clocks for the values above.
    # Copy the clock with the value. Do not invent one, and do not replace a
    # clock the event hub already wrote.
    for key in (
        "session_tech_asof_at",
        "session_tech_bar_at",
        "pve3_bar_at",
        "session_tech_observed_at",
        "quote_observed_at",
        "spy_observed_at",
        "chain_observed_at",
    ):
        if dest.get(key) not in (None, ""):
            continue
        val = osnap.get(key)
        if val in (None, ""):
            continue
        dest[key] = val
        n += 1
    if (
        dest.get("po_session_tech_asof_at") in (None, "")
        and dest.get("po_session_tech_source") in (None, "")
        and (
            dest.get("session_tech_asof_at") not in (None, "")
            or dest.get("session_tech_bar_at") not in (None, "")
            or dest.get("pve3_bar_at") not in (None, "")
        )
    ):
        dest["po_session_tech_source"] = "owned_tick"
    return n


def stamp_tp_profile_derived_keys(row: Optional[Dict[str, Any]]) -> int:
    """Write share, vol/OI, OTM, VWAP-ATR, and option L1 from fields on this row.

    No broker call. Mid-trade and exit families already stamp volume and OI
    at 100% but drop share/vol-OI aliases. OTM and VWAP-ATR are arithmetic
    on spot + strike / vwap + atr already on the row.
    """
    if not isinstance(row, dict):
        return 0
    n = 0
    try:
        from central_tech.chain_ratios import stamp_chain_ratios

        n += int(stamp_chain_ratios(row) or 0)
    except Exception:
        pass
    try:
        from central_tech.decision_stamp import shares

        cs, ps = shares(row.get("call_chain_volume"), row.get("put_chain_volume"))
        if cs is not None and row.get("call_share") != cs:
            row["call_share"] = cs
            n += 1
        if ps is not None and row.get("put_share") != ps:
            row["put_share"] = ps
            n += 1
    except Exception:
        pass
    try:
        px = _as_float(row.get("current_price") or row.get("underlying_price") or row.get("close"))
        vwap = _as_float(row.get("vwap"))
        atr = _as_float(row.get("atr"))
        if row.get("distance_from_vwap_atr") in (None, "") and px is not None and vwap is not None and atr and atr > 0:
            row["distance_from_vwap_atr"] = (px - vwap) / atr
            n += 1
    except Exception:
        pass
    if row.get("otm_pct") in (None, ""):
        try:
            from easyBreakout.breakout_contract_gates import compute_otm_pct

            px = _as_float(row.get("current_price") or row.get("underlying_price") or row.get("close"))
            strike = _as_float(row.get("strike") or row.get("po_options_owned_strike"))
            side = str(row.get("option_side") or row.get("option_type") or row.get("direction") or "")
            if px is not None and strike is not None:
                row["otm_pct"] = compute_otm_pct(px, strike, side)
                n += 1
        except Exception:
            pass
    try:
        from central_tech.option_l1 import stamp_option_l1_imbalance_from_sizes

        n += int(stamp_option_l1_imbalance_from_sizes(row) or 0)
    except Exception:
        pass
    return n


def stamp_tp_moment_cache(
    row: Optional[Dict[str, Any]],
    system: Any = None,
    osnap: Optional[Dict[str, Any]] = None,
) -> int:
    """Fill remaining TP keys from owned tick, hub-derived fields, and quote caches."""
    if not isinstance(row, dict):
        return 0
    n = fill_missing_tp_keys_from_osnap(row, osnap)
    n += stamp_tp_profile_derived_keys(row)
    if system is not None:
        try:
            from modules.po_snapshot_cache_hydrate import stamp_underlying_l1_from_cache

            n += int(
                stamp_underlying_l1_from_cache(
                    row, system, str(row.get("symbol") or "").strip().upper()
                )
                or 0
            )
        except Exception:
            pass
        if row.get("spy_change_pct") in (None, ""):
            try:
                from central_tech.spy_change import cached_spy_change_pct

                spy = cached_spy_change_pct(system)
                if spy is not None:
                    row["spy_change_pct"] = spy
                    n += 1
            except Exception:
                pass
    return n


def attach_tp_profile_extras(dest: Optional[Dict[str, Any]], *sources: Any) -> None:
    """Copy the 27-key Trade Profile extras onto a densified row.

    These five keys are scored at the gate and stored on the freeze. They are
    not in the 267-key schema, so densify would otherwise drop them.
    """
    if not isinstance(dest, dict):
        return
    pools: List[Dict[str, Any]] = []
    for src in sources:
        if isinstance(src, dict):
            pools.append(src)
            fz = src.get("po_decision_freeze")
            if isinstance(fz, dict) and isinstance(fz.get("values"), dict):
                pools.append(fz["values"])
            elif isinstance(src.get("values"), dict) and src.get("schema"):
                pools.append(src["values"])
    for src in pools:
        for key in TP_PROFILE_EXTRA_KEYS:
            if dest.get(key) not in (None, ""):
                continue
            val = src.get(key)
            if val not in (None, ""):
                dest[key] = val


_GATE_ENVELOPE_CLOCKS = (
    "po_session_tech_asof_at",
    "session_tech_asof_at",
    "session_tech_bar_at",
    "pve3_bar_at",
    "po_quote_asof_at",
    "quote_observed_at",
    "po_spy_asof_at",
    "po_chain_asof_at",
    "po_premium_path_asof_at",
    "po_sma_horizon",
    "po_clock_gaps",
    "po_session_tech_source",
)


def apply_gate_tech_lock(row: Dict[str, Any], gate: Optional[Dict[str, Any]]) -> int:
    """Restore gate-time tech onto an entry snapshot after post-fill enrich."""
    if not isinstance(row, dict) or not isinstance(gate, dict):
        return 0
    n = 0
    for key in GATE_TECH_KEYS:
        value = gate.get(key)
        if value is None or value == "":
            continue
        shared = isinstance(value, (dict, list)) and row.get(key) is value
        copied = copy.deepcopy(value) if isinstance(value, (dict, list)) else value
        if shared or row.get(key) != copied:
            row[key] = copied
            n += 1
    freeze_at = gate.get("po_decision_freeze_at")
    if freeze_at not in (None, ""):
        row["po_decision_freeze_at"] = freeze_at
        row["po_decision_freeze_mode"] = gate.get("po_decision_freeze_mode") or "closed_bar"
    for clock in _GATE_ENVELOPE_CLOCKS:
        if clock in gate:
            row[clock] = gate.get(clock)
    attach_tp_profile_extras(row, gate)
    row["po_tech_asof"] = "gate"
    row["po_tech_asof_at"] = row.get("po_decision_freeze_at")
    return n


def merge_gate_options_features(row: Dict[str, Any], options: Optional[Dict[str, Any]]) -> None:
    """Merge a chain read onto an entry row without replacing the decision freeze.

    Handoff may fill a blank research key from the chain read taken at that
    freeze. An executed row may not. A later chain is not evidence that the
    metric was knowable at the decision.
    """
    if not isinstance(row, dict) or not isinstance(options, dict):
        return
    stage = str(row.get("po_snapshot_stage") or row.get("stage") or "")
    executed = "options_executed" in stage
    handoff = ("emit_handoff" in stage) or stage.endswith("_handoff")
    for key, val in options.items():
        if val is None:
            continue
        if key in (
            "representative_call_delta",
            "representative_put_delta",
            "representative_call_gamma",
            "representative_put_gamma",
        ) and row.get(key) not in (None, ""):
            continue
        if executed and key in GATE_TECH_KEYS:
            continue
        if handoff and key in GATE_TECH_KEYS and row.get(key) not in (None, ""):
            continue
        row[key] = val


def carry_forward_entry_features(
    exit_row: Dict[str, Any],
    entry_row: Optional[Dict[str, Any]],
) -> int:
    """Bring entry-time technicals into exit snapshot (options re-fetched separately)."""
    return merge_features_into_row(
        exit_row,
        entry_row,
        _CARRY_FORWARD_KEYS,
        only_if_missing=True,
    )


def features_from_break_record(break_row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Flatten a trendline break collector record into a feature dict."""
    if not isinstance(break_row, dict):
        return {}
    out: Dict[str, Any] = {}
    for nest_key in (
        "features_priority_optimizer",
        "features_dense",
        "features_89",
        "features",
    ):
        nest = break_row.get(nest_key)
        if isinstance(nest, dict) and nest:
            merge_features_into_row(out, nest, only_if_missing=True)
    # Top-level break fields often hold structure/TA directly
    merge_features_into_row(out, break_row, FEATURE_KEYS_0DTE_UNIFIED, only_if_missing=True)
    if out.get("break_timestamp") in (None, ""):
        if break_row.get("first_break_timestamp") not in (None, ""):
            out["break_timestamp"] = break_row.get("first_break_timestamp")
        else:
            bar = break_row.get("break_bar")
            if isinstance(bar, dict) and bar.get("ts") not in (None, ""):
                out["break_timestamp"] = bar.get("ts")
    if out.get("symbol") is None and break_row.get("symbol"):
        out["symbol"] = str(break_row.get("symbol")).strip().upper()
    out["break_snapshot_joined"] = True
    return out


def overlay_options_features(dest: Dict[str, Any], options: Optional[Dict[str, Any]]) -> int:
    """Always prefer fresh exit-time options microstructure when present."""
    n = merge_features_into_row(
        dest,
        options,
        FEATURE_KEYS_OPTIONS_MICROSTRUCTURE,
        only_if_missing=False,
    )
    backfill_chain_totals_from_sides(dest)
    if isinstance(options, dict) and options.get("chain_observed_at") not in (None, ""):
        dest["chain_observed_at"] = options.get("chain_observed_at")
    try:
        from profit_optimizer.path_chain_skew import stamp_review_microstructure

        side = str(
            dest.get("option_side")
            or dest.get("expected_option_side")
            or dest.get("direction")
            or dest.get("option_type")
            or ""
        )
        stamp_review_microstructure(dest, option_side=side, overwrite=False)
    except Exception:
        pass
    return n


def count_critical_populated(row: Optional[Dict[str, Any]]) -> int:
    if not isinstance(row, dict):
        return 0
    return sum(
        1
        for k in FEATURE_KEYS_ENTRY_EXIT_CRITICAL
        if row.get(k) is not None and row.get(k) != ""
    )


def ensure_orb_range_pct(row: Optional[Dict[str, Any]]) -> None:
    """Derive orb_range_pct from high/low when the explicit field is missing."""
    if not isinstance(row, dict):
        return
    if row.get("orb_range_pct") is not None:
        return
    try:
        high = row.get("orb_high")
        low = row.get("orb_low")
        if high is None or low is None:
            od = row.get("orb_data")
            if isinstance(od, dict):
                high = high if high is not None else od.get("orb_high")
                low = low if low is not None else od.get("orb_low")
                if row.get("orb_range_pct") is None and od.get("orb_range_pct") is not None:
                    row["orb_range_pct"] = od.get("orb_range_pct")
                    return
        if high is None or low is None:
            return
        h = float(high)
        l = float(low)
        if l > 0:
            row["orb_range_pct"] = ((h - l) / l) * 100.0
        elif row.get("orb_range") is None:
            row["orb_range"] = h - l
    except (TypeError, ValueError, ZeroDivisionError):
        return


def _exit_chain_share(row: Dict[str, Any], option_side: Any) -> Optional[float]:
    """Same-side chain share at the exit row. Not a copy of direction_volume_pct."""
    try:
        call_v = float(row.get("call_chain_volume"))
        put_v = float(row.get("put_chain_volume"))
    except (TypeError, ValueError):
        return None
    total = call_v + put_v
    if total <= 0:
        return None
    side = str(option_side or row.get("option_side") or row.get("option_type") or "").strip().lower()
    if side in {"put", "p"} or side.endswith("put"):
        return put_v / total
    if side in {"call", "c"} or side.endswith("call"):
        return call_v / total
    return None


def stamp_exit_outcome_fields(
    row: Dict[str, Any],
    *,
    exit_reason: Optional[str] = None,
    exit_premium: Optional[float] = None,
    entry_premium: Optional[float] = None,
    realized_pnl: Optional[float] = None,
    realized_pnl_pct: Optional[float] = None,
    exit_time: Optional[str] = None,
    entry_time: Optional[str] = None,
    live_underlying: Any = None,
    entry_underlying: Any = None,
    exit_underlying: Any = None,
    option_side: Any = None,
) -> None:
    """Normalize exit outcome aliases onto the feature row for unified schema export."""
    if not isinstance(row, dict):
        return
    if exit_reason is not None:
        row["exit_reason"] = exit_reason
    if exit_premium is not None:
        row["exit_premium"] = exit_premium
        row.setdefault("exit_price", exit_premium)
    if entry_premium is not None:
        row.setdefault("entry_premium", entry_premium)
    if realized_pnl is not None:
        row["realized_pnl"] = realized_pnl
        row.setdefault("pnl_dollars", realized_pnl)
    if realized_pnl_pct is not None:
        row["realized_pnl_pct"] = realized_pnl_pct
        row.setdefault("pnl_pct", realized_pnl_pct)
        try:
            row["win"] = bool(float(realized_pnl_pct) > 0.0)
        except (TypeError, ValueError):
            pass
    if exit_time:
        row["exit_time"] = exit_time
    if entry_time and exit_time and row.get("holding_minutes") is None:
        try:
            from datetime import datetime

            def _parse(ts: str) -> Optional[datetime]:
                raw = str(ts).replace("Z", "+00:00")
                try:
                    return datetime.fromisoformat(raw)
                except ValueError:
                    return None

            et = _parse(str(entry_time))
            xt = _parse(str(exit_time))
            if et is not None and xt is not None:
                row["holding_minutes"] = max(0.0, (xt - et).total_seconds() / 60.0)
        except Exception:
            pass
    # Always densify equity entry vs live/exit for asset-stop research.
    try:
        from .underlying_spot_stamp import stamp_underlying_research_fields

        stamp_underlying_research_fields(
            row,
            live_underlying=live_underlying,
            entry_underlying=entry_underlying,
            exit_underlying=exit_underlying,
            option_side=option_side or row.get("option_side") or row.get("option_type"),
            at_exit=True,
            source="exit_outcome",
        )
    except Exception:
        pass


def ensure_po_desk_aliases(
    row: Optional[Dict[str, Any]],
    *,
    stealth_osnap: Optional[Dict[str, Any]] = None,
    exit_diagnostics: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Desk parity aliases so entry/exit snaps expose the same research surface.

    - ``iv`` ↔ ``implied_volatility``
    - ``hold_exit_dir_pct`` only from an explicit exit/stealth value
    - δ family: fill/chosen/representative/delta_at_entry backfill
    - ``cumulative_volume`` from stealth when present
    """
    if not isinstance(row, dict):
        return
    osnap = stealth_osnap if isinstance(stealth_osnap, dict) else {}
    diag = exit_diagnostics if isinstance(exit_diagnostics, dict) else {}

    iv = row.get("implied_volatility")
    if iv is None:
        iv = row.get("iv")
    if iv is None:
        iv = osnap.get("implied_volatility") or osnap.get("iv") or diag.get("implied_volatility")
    if iv is not None:
        row["implied_volatility"] = iv
        row["iv"] = iv

    hold_dir = row.get("hold_exit_dir_pct")
    if hold_dir is None:
        hold_dir = osnap.get("hold_exit_dir_pct")
    if hold_dir is None:
        hold_dir = diag.get("hold_exit_dir_pct")
    # direction_volume_pct is a decision measurement. It is not the exit hold.
    if hold_dir is not None:
        try:
            row["hold_exit_dir_pct"] = float(hold_dir)
        except (TypeError, ValueError):
            pass

    # Fill identity may mirror chosen_delta. It must not be copied into
    # representative_delta or delta, and those research fields must not be
    # copied back into fill_delta. They are different measurements.
    fill = row.get("fill_delta")
    chosen = row.get("chosen_delta")
    rep = row.get("representative_delta")
    entry_d = row.get("delta_at_entry")
    if fill is None:
        fill = next((v for v in (chosen, entry_d) if v is not None), None)
    if chosen is None and fill is not None:
        chosen = fill
    if fill is not None:
        row["fill_delta"] = fill
    if chosen is not None:
        row["chosen_delta"] = chosen

    if row.get("quality_score") is None and row.get("score") is not None:
        row["quality_score"] = row.get("score")
    if row.get("hard_gate_passed") is None and row.get("broker_success") is True:
        row["hard_gate_passed"] = True
    if row.get("vwap_source") in (None, "") and row.get("tech_source") not in (None, ""):
        row["vwap_source"] = row.get("tech_source")

    if row.get("otm_pct") is None:
        strike = row.get("representative_strike") or row.get("po_options_owned_strike")
        spot = (
            row.get("underlying_price")
            or row.get("entry_underlying")
            or row.get("entry_underlying_price")
            or row.get("current_price")
        )
        try:
            s = float(strike) if strike is not None else None
            u = float(spot) if spot is not None else None
        except (TypeError, ValueError):
            s = u = None
        if s is not None and u is not None and u > 0:
            side_tok = str(row.get("side") or row.get("option_type") or "").strip().lower()
            if "put" in side_tok:
                row["otm_pct"] = (u - s) / u
            else:
                row["otm_pct"] = (s - u) / u

    if row.get("cumulative_volume") is None:
        cum = (
            osnap.get("cumulative_volume")
            or diag.get("cumulative_volume")
            or row.get("representative_volume")
        )
        if cum is not None:
            row["cumulative_volume"] = cum


def stamp_owned_contract_microstructure(
    row: Optional[Dict[str, Any]],
    *,
    position: Any = None,
    order_payload: Optional[Dict[str, Any]] = None,
) -> None:
    """Copy filled OCC δ / IV / spread onto a PO execute row.

    Admit-time Layer D often skips with ``contract_ctx_absent``. Execute snaps
    still need the owned quote for next-session desks (ORB1 / ORB2 / TL).
    """
    if not isinstance(row, dict):
        return
    pl = order_payload if isinstance(order_payload, dict) else {}
    stage = str(row.get("po_snapshot_stage") or row.get("stage") or "")
    executed = "options_executed" in stage

    def _first(*vals: Any) -> Any:
        for v in vals:
            if v not in (None, ""):
                return v
        return None

    def _pos(attr: str) -> Any:
        if position is None:
            return None
        if isinstance(position, dict):
            return position.get(attr)
        return getattr(position, attr, None)

    strike = _first(
        row.get("strike"),
        row.get("po_options_owned_strike"),
        row.get("representative_strike"),
        row.get("matched_strike"),
        row.get("requested_strike"),
        row.get("listed_exchange_strike"),
        pl.get("strike"),
        pl.get("representative_strike"),
        pl.get("matched_strike"),
        pl.get("requested_strike"),
        _pos("strike"),
    )
    try:
        strike_f = float(strike) if strike is not None else None
        if strike_f is not None and strike_f <= 0:
            strike_f = None
    except (TypeError, ValueError):
        strike_f = None
    if strike_f is not None:
        if row.get("strike") is None:
            row["strike"] = strike_f
        if row.get("po_options_owned_strike") is None:
            row["po_options_owned_strike"] = strike_f

    opt = _first(
        row.get("option_type"),
        row.get("option_side"),
        pl.get("option_type"),
        pl.get("option_side"),
        _pos("option_type"),
        _pos("option_side"),
    )
    if opt and row.get("option_type") in (None, ""):
        row["option_type"] = str(opt).strip().lower()

    prem = _first(row.get("entry_premium"), pl.get("entry_premium"), _pos("entry_price"))
    if prem is not None and row.get("entry_premium") is None:
        row["entry_premium"] = prem

    pos_delta = _first(pl.get("fill_delta"), pl.get("chosen_delta"), pl.get("delta"), _pos("delta"))
    if pos_delta is not None:
        if row.get("fill_delta") is None:
            row["fill_delta"] = pos_delta
        if row.get("chosen_delta") is None:
            row["chosen_delta"] = pos_delta
        # delta is the research greek. The fill value stays on fill_delta.
        if row.get("delta") is None and not executed:
            row["delta"] = pos_delta

    if row.get("representative_spread_pct") in (None, "") and not executed:
        spread = _first(
            row.get("spread_pct"),
            pl.get("representative_spread_pct"),
            pl.get("spread_pct"),
            row.get("chain_sanity_best_same_side_spread_pct"),
        )
        if spread is not None:
            row["representative_spread_pct"] = spread
    if row.get("spread_pct") in (None, "") and row.get("representative_spread_pct") not in (None, ""):
        row["spread_pct"] = row.get("representative_spread_pct")

    iv = _first(
        row.get("implied_volatility"),
        row.get("iv"),
        pl.get("implied_volatility"),
        pl.get("iv"),
        _pos("implied_volatility"),
    )
    # Gate IV stays. A missing gate IV is not backfilled from the fill on
    # the executed file. Handoff may still record the decision-chain IV.
    if iv is not None and row.get("implied_volatility") in (None, "") and not executed:
        row["implied_volatility"] = iv
        row["iv"] = iv

    # Owned contract volume / OI — needed for owned_contract_volume_delta_5m.
    # TL/BO select often has these on the payload even when Layer C skew does not.
    owned_vol = _first(
        row.get("representative_volume"),
        row.get("cumulative_volume"),
        pl.get("representative_volume"),
        pl.get("cumulative_volume"),
        pl.get("volume"),
        _pos("volume"),
        _pos("cumulative_volume"),
    )
    if owned_vol is not None and not executed:
        if row.get("representative_volume") is None:
            row["representative_volume"] = owned_vol
        if row.get("cumulative_volume") is None:
            row["cumulative_volume"] = owned_vol

    owned_oi = _first(
        row.get("open_interest"),
        row.get("representative_open_interest"),
        pl.get("open_interest"),
        pl.get("representative_open_interest"),
        _pos("open_interest"),
    )
    if owned_oi is not None and not executed:
        if row.get("open_interest") is None:
            row["open_interest"] = owned_oi
        if row.get("representative_open_interest") is None:
            row["representative_open_interest"] = owned_oi

    if row.get("liquidity_class") in (None, ""):
        liq = _first(pl.get("liquidity_class"), _pos("liquidity_class"))
        if liq not in (None, ""):
            row["liquidity_class"] = liq
        else:
            try:
                from modules.orb_0dte_opening_execution import classify_liquidity_class

                sym = str(row.get("symbol") or pl.get("symbol") or "").strip().upper()
                if sym:
                    row["liquidity_class"] = classify_liquidity_class(sym)
            except Exception:
                pass

    # Owned option L1 sizes from fill payload / position / exact quote.
    bid_sz = _first(
        row.get("option_bid_size"),
        pl.get("option_bid_size"),
        pl.get("bid_size"),
        _pos("bid_size"),
        _pos("option_bid_size"),
    )
    ask_sz = _first(
        row.get("option_ask_size"),
        pl.get("option_ask_size"),
        pl.get("ask_size"),
        _pos("ask_size"),
        _pos("option_ask_size"),
    )
    if bid_sz is not None and row.get("option_bid_size") is None and not executed:
        try:
            row["option_bid_size"] = int(float(bid_sz))
        except (TypeError, ValueError):
            pass
    if ask_sz is not None and row.get("option_ask_size") is None and not executed:
        try:
            row["option_ask_size"] = int(float(ask_sz))
        except (TypeError, ValueError):
            pass
    if (
        row.get("option_l1_imbalance") is None
        and row.get("option_bid_size") is not None
        and row.get("option_ask_size") is not None
    ):
        try:
            b = float(row["option_bid_size"])
            a = float(row["option_ask_size"])
            denom = b + a
            if denom > 0:
                row["option_l1_imbalance"] = (b - a) / denom
        except (TypeError, ValueError):
            pass
    try:
        from modules.po_observe_trajectory import apply_selected_contract_option_l1

        apply_selected_contract_option_l1(row, order_payload=pl, prefer_owned=True)
    except Exception:
        pass

    ensure_po_desk_aliases(row)


_QG_FIELD_COPY = (
    (("RSI", "equity.rsi_anti_chase"), "rsi", "rsi"),
    (("BB", "equity.bb_anti_stretch", "BB_pctB"), "bollinger_position", "bollinger_position"),
    (("Vol_Ratio", "equity.volume_ratio"), "equity_volume_ratio", "volume_ratio"),
    (("dir_pct", "chain.dir_pct"), "direction_volume_pct", "direction_volume_pct"),
    (("dir_pct", "chain.dir_pct"), "call_chain_volume", "call_chain_volume"),
    (("dir_pct", "chain.dir_pct"), "put_chain_volume", "put_chain_volume"),
    (("VWAP", "equity.vwap_align"), "vwap_position", "vwap_position"),
    (("VWAP", "equity.vwap_align"), "vwap_distance_pct", "vwap_distance_pct"),
    (("score", "equity.score_floor"), "quality_score", "quality_score"),
    (("OTM", "contract.otm"), "otm_pct", "otm_pct"),
    (("delta", "contract.delta"), "delta", "delta"),
    (("delta", "contract.delta"), "delta", "fill_delta"),
    (("delta", "contract.delta"), "delta", "chosen_delta"),
    (("spread", "contract.spread"), "spread_pct", "representative_spread_pct"),
    (("spread", "contract.spread"), "spread_pct", "spread_pct"),
    (("IV", "contract.iv"), "iv", "iv"),
    (("IV", "contract.iv"), "iv", "implied_volatility"),
)


def overlay_quality_gate_into_row(row: Optional[Dict[str, Any]]) -> None:
    """Copy QG report stamps onto schema keys and rewrite long gate_ids to short."""
    if not isinstance(row, dict):
        return
    raw = row.get("quality_gate_report")
    if not isinstance(raw, dict):
        return
    try:
        from easy0DTE.modules.quality_gate_stack import canonicalize_quality_gate_report
    except Exception:
        canonicalize_quality_gate_report = None  # type: ignore
    report = canonicalize_quality_gate_report(raw) if canonicalize_quality_gate_report else raw
    if isinstance(report, dict):
        row["quality_gate_report"] = report
    else:
        report = raw
    by_id: Dict[str, Dict[str, Any]] = {}
    for r in report.get("results") or []:
        if not isinstance(r, dict):
            continue
        gid = str(r.get("gate_id") or "")
        if gid:
            by_id[gid] = r
    for gids, field_name, row_key in _QG_FIELD_COPY:
        if row.get(row_key) not in (None, ""):
            continue
        for gid in gids:
            rec = by_id.get(gid)
            if not rec:
                continue
            fields = rec.get("fields") if isinstance(rec.get("fields"), dict) else {}
            val = fields.get(field_name)
            if val not in (None, ""):
                row[row_key] = val
                break
    if row.get("hard_gate_passed") is None:
        # Do not stamp False when first_reject is missing (cloud observe stacks
        # can report admitted=false on filled trades).
        if report.get("admitted") is True:
            row["hard_gate_passed"] = True
        elif report.get("first_reject"):
            row["hard_gate_passed"] = False
        elif row.get("broker_success") is True:
            row["hard_gate_passed"] = True
    if not row.get("strategy_type") and report.get("path"):
        row["strategy_type"] = report.get("path")
    if not row.get("side") and report.get("side"):
        row["side"] = str(report.get("side") or "").upper()
    # QG dir% often has chain vols before the feature row does — copy then total.
    try:
        backfill_chain_totals_from_sides(row)
    except Exception:
        pass
    try:
        from .volume_data import ensure_volume_research_fields

        ensure_volume_research_fields(row)
    except Exception:
        try:
            from modules.volume_data import ensure_volume_research_fields

            ensure_volume_research_fields(row)
        except Exception:
            pass


def merge_snapshot_extra_into_row(
    row: Optional[Dict[str, Any]],
    extra: Optional[Dict[str, Any]],
) -> None:
    """Copy PO extra envelope keys onto the feature row when the schema key is empty."""
    if not isinstance(row, dict) or not isinstance(extra, dict):
        return
    for k in (
        "trendline_slot",
        "a1_policy",
        "setup_type",
        "trigger_direction",
        "line_geometry",
        "option_side",
        "expected_option_side",
        "entry_premium",
        "trade_id",
        "broker_success",
        "broker_error",
        "break_snapshot_joined",
        "strategy_name",
    ):
        if row.get(k) in (None, "") and extra.get(k) not in (None, ""):
            row[k] = extra.get(k)
    if not row.get("strategy_type") and extra.get("strategy_name"):
        name = str(extra.get("strategy_name") or "")
        if "trendline" in name.lower():
            row["strategy_type"] = "trendline_0dte"
        elif "breakout" in name.lower():
            row["strategy_type"] = "breakout_0dte"
        elif "orb" in name.lower():
            row["strategy_type"] = "orb_0dte"


# Exact scalars Moon read. Never filled from representative_delta.
DECISION_MOON_DELTA_KEYS: Tuple[str, ...] = (
    "representative_call_delta",
    "representative_put_delta",
    "representative_call_gamma",
    "representative_put_gamma",
)


def carry_decision_moon_deltas(dest: Optional[Dict[str, Any]], *sources: Any) -> None:
    """Copy decision-time near-spot deltas onto a snapshot row.

    Leaves a key null when every source is missing it. Does not read
    representative_delta, the selected contract, or the chain cache.
    """
    if not isinstance(dest, dict):
        return
    for key in DECISION_MOON_DELTA_KEYS:
        if not _blank(dest.get(key)):
            continue
        for src in sources:
            if not isinstance(src, dict):
                continue
            val = src.get(key)
            if _blank(val):
                continue
            dest[key] = val
            break


def _blank(value: Any) -> bool:
    return value is None or value == ""


def _first_filled(sources: Iterable[Any], keys: Sequence[str]) -> Any:
    for src in sources:
        if not isinstance(src, dict):
            continue
        for key in keys:
            val = src.get(key)
            if not _blank(val):
                return val
    return None


def _as_float(value: Any) -> Optional[float]:
    if _blank(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def ensure_path_signal_stamps(row: Optional[Dict[str, Any]]) -> None:
    """Copy path-signal aliases onto the canonical densify keys.

    Entry, exit, and 20p all pass through densify. This runs before the schema
    emit so the ten path-signal fields survive when a source value exists and
    stay null when it does not.
    """
    if not isinstance(row, dict):
        return
    md = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    setup = row.get("setup_payload") if isinstance(row.get("setup_payload"), dict) else {}
    if not setup and isinstance(md, dict):
        setup = md.get("setup_payload") if isinstance(md.get("setup_payload"), dict) else {}
    qg = row.get("qg_admit") if isinstance(row.get("qg_admit"), dict) else {}
    if not qg and isinstance(md, dict):
        qg = md.get("qg_admit") if isinstance(md.get("qg_admit"), dict) else {}
    sources = (row, md, setup, qg)

    if _blank(row.get("underlying_volume_1m")):
        vol = _first_filled(sources, ("volume_1m", "volume_delta_1m"))
        if vol is not None:
            row["underlying_volume_1m"] = vol
    if _blank(row.get("volume_1m")) and not _blank(row.get("underlying_volume_1m")):
        row["volume_1m"] = row["underlying_volume_1m"]

    if _blank(row.get("open_interest")):
        oi = _first_filled(sources, ("representative_open_interest", "owned_contract_oi"))
        if oi is not None:
            row["open_interest"] = oi

    if _blank(row.get("break_timestamp")):
        ts = _first_filled(
            sources,
            (
                "break_timestamp",
                "structure_break_ts",
                "break_ts",
                "break_time",
                "first_break_timestamp",
                "material_break_at",
            ),
        )
        if _blank(ts):
            for src in sources:
                if not isinstance(src, dict):
                    continue
                bar = src.get("break_bar")
                if isinstance(bar, dict) and not _blank(bar.get("ts")):
                    ts = bar.get("ts")
                    break
        if not _blank(ts):
            row["break_timestamp"] = ts

    if _blank(row.get("break_level")):
        level = _as_float(_first_filled(sources, ("line_price_at_break",)))
        if level is None:
            strat = str(
                _first_filled(sources, ("strategy_type", "snapshot_strategy")) or ""
            ).lower()
            if "trend" not in strat:
                side = str(
                    _first_filled(
                        sources,
                        ("option_side", "option_type", "expected_option_side", "side"),
                    )
                    or ""
                ).upper()
                if "PUT" in side or side in ("SHORT", "DOWN", "BEAR"):
                    level = _as_float(_first_filled(sources, ("orb_low",)))
                elif "CALL" in side or side in ("LONG", "UP", "BULL"):
                    level = _as_float(_first_filled(sources, ("orb_high",)))
        if level is not None:
            row["break_level"] = level

    # Same key only. representative_delta is a different measurement.
    carry_decision_moon_deltas(row, md, setup, qg)


def densify_feature_map(
    features: Optional[Dict[str, Any]],
    *,
    snapshot_strategy: str,
    include_exit_fields: bool = False,
) -> Dict[str, Any]:
    """
    Emit a fixed-key map matching the path schema (null for missing values).

    Ensures ORB 7:30 / Trendline break / execute / exit payloads share the same key set.
    Runs volume research ensure so chain/dir%/equity ratio aliases are derived first.
    """
    from .po_feature_schema import (
        ENTRY_SNAPSHOT_EXCLUDED,
        FEATURE_KEYS_SNAPSHOT_DROPPED,
        is_entry_excluded_field,
        schema_for_strategy,
    )

    src = dict(features) if isinstance(features, dict) else {}
    try:
        from modules.volume_data import ensure_volume_research_fields

        ensure_volume_research_fields(src)
    except Exception:
        try:
            from .volume_data import ensure_volume_research_fields

            ensure_volume_research_fields(src)
        except Exception:
            pass
    try:
        from modules.session_tech_indicators import ensure_bollinger_complete

        ensure_bollinger_complete(src)
    except Exception:
        try:
            from .session_tech_indicators import ensure_bollinger_complete

            ensure_bollinger_complete(src)
        except Exception:
            pass
    try:
        backfill_chain_totals_from_sides(src)
    except Exception:
        pass
    try:
        overlay_quality_gate_into_row(src)
    except Exception:
        pass
    try:
        ensure_po_desk_aliases(src)
    except Exception:
        pass
    try:
        ensure_path_signal_stamps(src)
    except Exception:
        pass
    keys = list(schema_for_strategy(snapshot_strategy))
    if include_exit_fields:
        for k in ENTRY_SNAPSHOT_EXCLUDED:
            if k not in keys:
                keys.append(k)
    # Owned strike: ORB execute often has representative_strike but leaves
    # po_options_owned_strike null — stamp for desk join parity with TL.
    if src.get("po_options_owned_strike") in (None, ""):
        for _sk in (
            "strike",
            "representative_strike",
            "matched_strike",
            "requested_strike",
            "listed_exchange_strike",
        ):
            _sv = src.get(_sk)
            if _sv in (None, ""):
                continue
            try:
                _sf = float(_sv)
            except (TypeError, ValueError):
                continue
            if _sf > 0:
                src["po_options_owned_strike"] = _sf
                if src.get("strike") in (None, ""):
                    src["strike"] = _sf
                break
    # Invent sentinel zeros must densify as null (Sep 8 Demo: rs_vs_spy=0.0
    # stamped when broker omitted changePercent).
    for _zk in ("rs_vs_spy",):
        _zv = src.get(_zk)
        if _zv is None or _zv == "":
            continue
        try:
            if float(_zv) == 0.0:
                src[_zk] = None
        except (TypeError, ValueError):
            pass
    # CALL/PUT side must survive the second densify. The live row often has
    # side=CALL while option_side was only stamped after the first pass.
    if src.get("option_side") in (None, ""):
        tok = str(src.get("side") or src.get("option_type") or src.get("direction") or "").strip().lower()
        if tok in ("call", "put"):
            src["option_side"] = tok
    # B1 (Oct 7 2026): at handoff no option has been bought; entry_price there
    # is the stock price, so never alias it into entry_premium.
    _stage_s = str(src.get("po_snapshot_stage") or src.get("stage") or "")
    _is_handoff = "emit_handoff" in _stage_s or _stage_s.endswith("_handoff")
    if _is_handoff:
        src["entry_premium"] = None
    elif src.get("entry_premium") in (None, ""):
        alt = src.get("entry_price")
        try:
            if alt is not None and float(alt) > 0:
                src["entry_premium"] = float(alt)
        except (TypeError, ValueError):
            pass
    # Orphan MACD line without signal/hist (macd_missing) — clear for consistency.
    if bool(src.get("macd_missing")):
        src["macd_histogram"] = None
        src["prior_macd_histogram"] = None
        src["macd_signal"] = None
        src["macd"] = None
    out: Dict[str, Any] = {}
    dropped = set(FEATURE_KEYS_SNAPSHOT_DROPPED)
    for k in keys:
        if k in dropped:
            continue
        if not include_exit_fields and is_entry_excluded_field(k):
            continue
        val = src.get(k)
        out[k] = val if val != "" else None
    attach_tp_profile_extras(out, src)
    stamp_tp_profile_derived_keys(out)
    return out
