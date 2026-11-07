"""
Day-freeze serialize/deserialize for TL0–TL10 A1/A2 + slope.

ORB2 H/L prices alone do not pin envelope geometry. Persist each drawn line
(``TrendlineDefinition``) and ORB2 break/inject state under daily_markers key
``trendline_geometry`` so mid-session restarts keep the same trade lines.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

try:
    from easyTrendline.trendline_models import (
        MAX_TRENDLINE_SLOT as _MAX_TL_SLOT,
        ORB2_EXTENSION_BUILT_FLAGS as _EXT_BUILT,
        ORB2_EXTENSION_PENDING_FLAGS as _EXT_PENDING,
        ORB2_EXTENSION_SLOT_IDS as _EXT_SLOTS,
        TRENDLINE_SLOT_RANK as _SLOT_RANK,
    )
except Exception:
    _MAX_TL_SLOT = 10
    _SLOT_RANK = {f"trendline{i}": i for i in range(_MAX_TL_SLOT + 1)}
    _EXT_SLOTS = tuple(f"trendline{i}" for i in range(2, _MAX_TL_SLOT + 1))
    _EXT_BUILT = tuple(f"tl{i}_built" for i in range(2, _MAX_TL_SLOT + 1))
    _EXT_PENDING = tuple(f"tl{i}_pending" for i in range(3, _MAX_TL_SLOT + 1))

log = logging.getLogger("trendline_geometry_persist")


def _iso(ts: Any) -> Optional[str]:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.isoformat()
    return str(ts)


def _parse_dt(raw: Any) -> Optional[datetime]:
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def _is_minute_aligned_ts(raw: Any) -> bool:
    dt = _parse_dt(raw)
    if dt is None:
        return False
    return dt.second == 0 and int(getattr(dt, "microsecond", 0) or 0) == 0


def tl24_anchors_are_stub(a1: Dict[str, Any], a2: Dict[str, Any]) -> bool:
    """True when TL2–TL10 A1/A2 would plot the synthetic / quote-tick envelope."""
    src = str(a2.get("source") or "").lower()
    if "synthetic" in src:
        return True
    if not _is_minute_aligned_ts(a1.get("ts")) or not _is_minute_aligned_ts(a2.get("ts")):
        return True
    try:
        p1 = float(a1.get("price") or 0)
        p2 = float(a2.get("price") or 0)
        t1 = _parse_dt(a1.get("ts"))
        t2 = _parse_dt(a2.get("ts"))
        if p1 > 0 and t1 is not None and t2 is not None and abs(p2 / p1 - 1.001) < 1e-6:
            if abs((t2 - t1).total_seconds() - 60.0) < 2.0:
                return True
    except (TypeError, ValueError, ZeroDivisionError):
        return True
    return False


def serialize_anchor(anchor: Any) -> Optional[Dict[str, Any]]:
    if anchor is None:
        return None
    try:
        price = float(getattr(anchor, "price", None))
    except (TypeError, ValueError):
        return None
    ts = _iso(getattr(anchor, "ts", None))
    if not ts or price <= 0:
        return None
    out: Dict[str, Any] = {
        "ts": ts,
        "price": price,
        "source": str(getattr(anchor, "source", "") or ""),
    }
    ci = getattr(anchor, "candle_index", None)
    if ci is not None:
        try:
            out["candle_index"] = int(ci)
        except (TypeError, ValueError):
            pass
    return out


def serialize_candidate(candidate: Any) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Return (key, payload) for a TrendlineCandidate with a drawn line."""
    if candidate is None:
        return None
    tl = getattr(candidate, "trendline", None)
    if tl is None:
        return None
    try:
        from easyTrendline.trendline_models import (
            make_trendline_candidate_key,
            normalize_trendline_slot,
            trendline_slot_from_payload,
        )
    except Exception:
        return None

    symbol = str(getattr(candidate, "symbol", "") or "").strip().upper()
    direction = getattr(candidate, "direction", None)
    dir_s = getattr(direction, "value", None) or str(direction or "").strip().lower()
    payload = getattr(candidate, "setup_payload", None)
    if not isinstance(payload, dict):
        payload = {}
    try:
        slot = trendline_slot_from_payload(payload)
    except Exception:
        slot = normalize_trendline_slot(payload.get("trendline_slot") or "trendline1")
    key = make_trendline_candidate_key(symbol, direction, slot)
    a1 = serialize_anchor(getattr(tl, "anchor_one", None))
    a2 = serialize_anchor(getattr(tl, "anchor_two", None))
    if not symbol or not a1 or not a2:
        return None
    slot_s = str(slot)
    if slot_s in _EXT_SLOTS and tl24_anchors_are_stub(a1, a2):
        log.warning(
            "TRENDLINE_GEOMETRY_PERSIST | action=skip_stub | symbol=%s | slot=%s | "
            "a1_ts=%s | a2_src=%s",
            symbol,
            slot_s,
            a1.get("ts"),
            a2.get("source"),
        )
        return None
    try:
        slope = float(getattr(tl, "slope_per_second"))
        intercept = float(getattr(tl, "intercept"))
    except (TypeError, ValueError):
        return None

    # Compact setup payload — drop bulky nests.
    setup_compact = {
        k: v
        for k, v in payload.items()
        if not isinstance(v, (dict, list))
        or k
        in (
            "quality_gate_report",
        )
    }
    # Never persist nested QG report blobs in geometry freeze.
    setup_compact.pop("quality_gate_report", None)

    row = {
        "symbol": symbol,
        "direction": str(dir_s),
        "slot": str(slot),
        "anchor_one": a1,
        "anchor_two": a2,
        "slope_per_second": slope,
        "intercept": intercept,
        "built_at": _iso(getattr(tl, "built_at", None)),
        "trendline_metadata": dict(getattr(tl, "metadata", None) or {}),
        "setup_payload": setup_compact,
        "priority_score": float(getattr(candidate, "priority_score", 0.0) or 0.0),
        "confidence": float(getattr(candidate, "confidence", 0.0) or 0.0),
        "qualified_at": _iso(getattr(candidate, "qualified_at", None)),
        "candidate_state": str(
            getattr(getattr(candidate, "state", None), "value", getattr(candidate, "state", "") or "")
            or ""
        ).strip().lower(),
        "state_reason": str(getattr(candidate, "state_reason", "") or ""),
        "attempt_consumed": bool(setup_compact.get("attempt_consumed")),
    }
    try:
        from easyTrendline.trendline_simple_mode import get_simple_state

        simple = get_simple_state(candidate)
        row["simple_state"] = str(getattr(simple, "value", simple) or "")
    except Exception:
        pass
    return key, row


def serialize_candidates(candidates: Any) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    if not candidates:
        return out
    seq = candidates.values() if isinstance(candidates, dict) else candidates
    for cand in seq or []:
        pair = serialize_candidate(cand)
        if not pair:
            continue
        key, row = pair
        out[key] = row
    return out


def deserialize_candidate(row: Dict[str, Any]) -> Optional[Any]:
    """Rebuild TrendlineCandidate + TrendlineDefinition from a frozen row."""
    if not isinstance(row, dict):
        return None
    try:
        from easyTrendline.trendline_models import (
            TrendlineAnchor,
            TrendlineCandidate,
            TrendlineCandidateState,
            TrendlineDefinition,
            TrendlineDirection,
            normalize_trendline_slot,
        )
    except Exception:
        return None

    symbol = str(row.get("symbol") or "").strip().upper()
    dir_s = str(row.get("direction") or "").strip().lower()
    if dir_s not in ("bull", "bear") or not symbol:
        return None
    direction = TrendlineDirection.BULL if dir_s == "bull" else TrendlineDirection.BEAR
    a1r = row.get("anchor_one") if isinstance(row.get("anchor_one"), dict) else None
    a2r = row.get("anchor_two") if isinstance(row.get("anchor_two"), dict) else None
    if not a1r or not a2r:
        return None
    a1_ts = _parse_dt(a1r.get("ts"))
    a2_ts = _parse_dt(a2r.get("ts"))
    try:
        a1_px = float(a1r.get("price"))
        a2_px = float(a2r.get("price"))
        slope = float(row.get("slope_per_second"))
        intercept = float(row.get("intercept"))
    except (TypeError, ValueError):
        return None
    if a1_ts is None or a2_ts is None or a1_px <= 0 or a2_px <= 0:
        return None

    a1 = TrendlineAnchor(
        ts=a1_ts,
        price=a1_px,
        source=str(a1r.get("source") or ""),
        candle_index=a1r.get("candle_index"),
    )
    a2 = TrendlineAnchor(
        ts=a2_ts,
        price=a2_px,
        source=str(a2r.get("source") or ""),
        candle_index=a2r.get("candle_index"),
    )
    built_at = _parse_dt(row.get("built_at")) or datetime.now(timezone.utc)
    meta = row.get("trendline_metadata") if isinstance(row.get("trendline_metadata"), dict) else {}
    tl = TrendlineDefinition(
        symbol=symbol,
        direction=direction,
        anchor_one=a1,
        anchor_two=a2,
        slope_per_second=slope,
        intercept=intercept,
        built_at=built_at,
        metadata=dict(meta),
    )
    setup = row.get("setup_payload") if isinstance(row.get("setup_payload"), dict) else {}
    setup = dict(setup)
    slot = normalize_trendline_slot(row.get("slot") or setup.get("trendline_slot") or "trendline1")
    setup["trendline_slot"] = slot
    setup.setdefault("line_geometry", dir_s)
    setup.setdefault("trendline_structure_source", "day_freeze_hydrate")
    setup.setdefault("structure_selection_reason", "day_freeze_hydrate")
    q_at = _parse_dt(row.get("qualified_at")) or built_at
    cand = TrendlineCandidate(
        symbol=symbol,
        direction=direction,
        qualified_at=q_at,
        priority_score=float(row.get("priority_score") or 0.0),
        confidence=float(row.get("confidence") or 0.0),
        trendline=tl,
        setup_payload=setup,
        state=TrendlineCandidateState.WAITING_FOR_BREAK,
    )
    apply_persisted_envelope_state(cand, row)
    return cand


def apply_persisted_envelope_state(candidate: Any, row: Dict[str, Any]) -> None:
    """Restore EXPIRED/EXECUTED after hydrate so one-attempt envelopes do not re-arm.

    A1/A2 stay on the candidate for plots. Superseded prior slots stay expired.
    """
    if candidate is None or not isinstance(row, dict):
        return
    setup = row.get("setup_payload") if isinstance(row.get("setup_payload"), dict) else {}
    superseded = bool(setup.get("geometry_superseded"))
    consumed = bool(row.get("attempt_consumed") or setup.get("attempt_consumed"))
    state_s = str(row.get("candidate_state") or "").strip().lower()
    reason = str(row.get("state_reason") or "")[:180]
    simple_s = str(row.get("simple_state") or "").strip().lower()
    terminal = superseded or consumed or state_s in {
        "expired",
        "executed",
        "invalidated",
    }
    if not terminal:
        return
    try:
        from easyTrendline.trendline_models import TrendlineCandidateState
        from easyTrendline.trendline_simple_mode import (
            SimpleTrendlineState,
            set_simple_state,
        )

        if state_s == "executed" or simple_s == "executed":
            candidate.state = TrendlineCandidateState.EXECUTED
            set_simple_state(candidate, SimpleTrendlineState.EXECUTED, reason or "executor_ack")
        elif state_s == "invalidated":
            candidate.state = TrendlineCandidateState.INVALIDATED
            set_simple_state(candidate, SimpleTrendlineState.REJECTED, reason or "invalidated")
        else:
            candidate.state = TrendlineCandidateState.EXPIRED
            set_simple_state(
                candidate,
                SimpleTrendlineState.EXPIRED,
                reason or ("superseded_by_newer_slot" if superseded else "attempt_consumed"),
            )
        if reason:
            candidate.state_reason = reason
        if isinstance(candidate.setup_payload, dict):
            if superseded:
                candidate.setup_payload["geometry_superseded"] = True
            if consumed:
                candidate.setup_payload["attempt_consumed"] = True
    except Exception:
        pass


def candidate_row_is_valid(row: Any) -> bool:
    if not isinstance(row, dict):
        return False
    setup = row.get("setup_payload") if isinstance(row.get("setup_payload"), dict) else {}
    a1 = row.get("anchor_one") if isinstance(row.get("anchor_one"), dict) else None
    a2 = row.get("anchor_two") if isinstance(row.get("anchor_two"), dict) else None
    if not a1 or not a2:
        return False
    try:
        if float(a1.get("price") or 0) <= 0 or float(a2.get("price") or 0) <= 0:
            return False
        float(row.get("slope_per_second"))
        float(row.get("intercept"))
    except (TypeError, ValueError):
        return False
    if not _parse_dt(a1.get("ts")) or not _parse_dt(a2.get("ts")):
        return False
    slot = str(row.get("slot") or "").strip().lower()
    if slot not in _SLOT_RANK:
        return False
    if slot in _EXT_SLOTS and tl24_anchors_are_stub(a1, a2):
        return False
    return True


def prune_prior_slots_from_candidates(
    candidates: Dict[str, Dict[str, Any]],
    *,
    symbol: str,
    keep_slot: str,
) -> Tuple[Dict[str, Dict[str, Any]], int]:
    """Drop freeze rows for ``symbol`` on slots older than ``keep_slot`` (not TL0)."""
    try:
        from easyTrendline.trendline_models import TRENDLINE_SLOT_RANK, normalize_trendline_slot
    except Exception:
        def normalize_trendline_slot(raw: Any = None) -> str:  # type: ignore
            return str(raw or "trendline1").strip().lower() or "trendline1"

        TRENDLINE_SLOT_RANK = {  # type: ignore
            f"trendline{i}": i for i in range(int(_MAX_TL_SLOT) + 1)
        }

    sym = str(symbol or "").strip().upper()
    keep = normalize_trendline_slot(keep_slot)
    keep_r = TRENDLINE_SLOT_RANK.get(keep)
    if not sym or keep_r is None or keep_r < 2:
        return dict(candidates or {}), 0
    out: Dict[str, Dict[str, Any]] = {}
    pruned = 0
    for key, row in (candidates or {}).items():
        if not isinstance(row, dict):
            continue
        row_sym = str(row.get("symbol") or "").strip().upper()
        if row_sym != sym:
            out[str(key)] = dict(row)
            continue
        slot = str(row.get("slot") or "").strip().lower()
        slot_r = TRENDLINE_SLOT_RANK.get(normalize_trendline_slot(slot), -1)
        if slot_r >= 1 and slot_r < keep_r:
            pruned += 1
            continue
        out[str(key)] = dict(row)
    return out, pruned


def marker_has_valid_geometry(info: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(info, dict):
        return False
    cands = info.get("candidates") if isinstance(info.get("candidates"), dict) else {}
    if any(candidate_row_is_valid(v) for v in cands.values()):
        return True
    events = info.get("orb2_break_events") if isinstance(info.get("orb2_break_events"), dict) else {}
    for st in events.values():
        if isinstance(st, dict) and any(st.get(flag) for flag in _EXT_BUILT):
            return True
    return False


def count_slots(info: Optional[Dict[str, Any]]) -> Dict[str, int]:
    out = {slot: 0 for slot in _SLOT_RANK}
    out["total"] = 0
    if not isinstance(info, dict):
        return out
    cands = info.get("candidates") if isinstance(info.get("candidates"), dict) else {}
    for row in cands.values():
        if not candidate_row_is_valid(row):
            continue
        slot = str(row.get("slot") or "").strip().lower()
        if slot in out:
            out[slot] += 1
        out["total"] += 1
    return out


def _overlay_candidate_trade_state(
    existing: Dict[str, Any], incoming: Dict[str, Any]
) -> Dict[str, Any]:
    """Keep frozen A1/A2/slope; overlay expire/fill/supersede flags."""
    out = dict(existing or {})
    inc = incoming if isinstance(incoming, dict) else {}
    setup_e = dict(out.get("setup_payload") or {}) if isinstance(out.get("setup_payload"), dict) else {}
    setup_i = inc.get("setup_payload") if isinstance(inc.get("setup_payload"), dict) else {}
    for k in (
        "geometry_superseded",
        "superseded_by_slot",
        "supersede_reason",
        "attempt_consumed",
        "attempt_consumed_reason",
    ):
        if setup_i.get(k) not in (None, "", False):
            setup_e[k] = setup_i[k]
    out["setup_payload"] = setup_e
    for k in ("candidate_state", "state_reason", "simple_state", "attempt_consumed"):
        if inc.get(k) not in (None, ""):
            out[k] = inc.get(k)
    return out


def merge_geometry_payload(
    existing: Optional[Dict[str, Any]],
    *,
    candidates: Optional[Dict[str, Dict[str, Any]]] = None,
    orb2_break_events: Optional[Dict[str, Any]] = None,
    orb2_extension_built: Optional[Dict[str, Any]] = None,
    orb2_extreme_ts: Optional[Dict[str, Any]] = None,
    prune_symbol: Optional[str] = None,
    prune_keep_slot: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Merge new geometry into existing freeze.

    Never overwrite an existing valid candidate's A1/A2/slope. Overlay expire /
    fill / superseded flags so one-attempt state survives restart. Prior-slot
    rows stay for plots (``prune_symbol`` is accepted but does not drop rows).
    """
    _ = (prune_symbol, prune_keep_slot)
    base = dict(existing or {}) if isinstance(existing, dict) else {}
    dest_cands = dict(base.get("candidates") or {}) if isinstance(base.get("candidates"), dict) else {}
    pruned = 0
    added = 0
    skipped = 0
    for key, row in (candidates or {}).items():
        if not candidate_row_is_valid(row):
            continue
        k = str(key)
        if k in dest_cands and candidate_row_is_valid(dest_cands.get(k)):
            dest_cands[k] = _overlay_candidate_trade_state(dest_cands[k], row)
            skipped += 1
            continue
        dest_cands[k] = dict(row)
        added += 1

    dest_events = (
        dict(base.get("orb2_break_events") or {})
        if isinstance(base.get("orb2_break_events"), dict)
        else {}
    )
    for sym, st in (orb2_break_events or {}).items():
        if not isinstance(st, dict):
            continue
        u = str(sym or "").strip().upper()
        if not u:
            continue
        cur = dict(dest_events.get(u) or {})
        for flag in _EXT_BUILT:
            if st.get(flag):
                cur[flag] = True
        if st.get("break_count") is not None:
            try:
                cur["break_count"] = max(int(cur.get("break_count") or 0), int(st.get("break_count") or 0))
            except (TypeError, ValueError):
                pass
        if st.get("armed_side"):
            cur["armed_side"] = st.get("armed_side")
        # Preserve episode bookkeeping when present.
        for k in (
            "in_extension",
            "cleared",
            "seen_inside_post_730",
            "await_reclaim_before_tl2",
        ) + tuple(_EXT_PENDING):
            if k in st:
                cur[k] = st.get(k)
        dest_events[u] = cur

    dest_built = (
        dict(base.get("orb2_extension_built") or {})
        if isinstance(base.get("orb2_extension_built"), dict)
        else {}
    )
    for k, v in (orb2_extension_built or {}).items():
        if k not in dest_built and v is not None:
            dest_built[str(k)] = v

    dest_ext = (
        dict(base.get("orb2_extreme_ts") or {})
        if isinstance(base.get("orb2_extreme_ts"), dict)
        else {}
    )
    for sym, row in (orb2_extreme_ts or {}).items():
        if not isinstance(row, dict):
            continue
        u = str(sym or "").strip().upper()
        if not u:
            continue
        # First write wins for extreme timestamps (A1 time freeze).
        if u in dest_ext and isinstance(dest_ext.get(u), dict):
            continue
        dest_ext[u] = dict(row)

    now = datetime.now(timezone.utc).isoformat()
    return {
        "frozen": True,
        "frozen_at": base.get("frozen_at") or now,
        "updated_at": now,
        "candidates": dest_cands,
        "orb2_break_events": dest_events,
        "orb2_extension_built": dest_built,
        "orb2_extreme_ts": dest_ext,
        "last_merge_added": added,
        "last_merge_skipped": skipped,
        "last_merge_pruned": pruned,
    }


def _orb_row_get(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)


def _is_placeholder_orb_extreme(row: Any, ts_key: str) -> bool:
    """True when the in-memory extreme ts is still the 06:30 ingest stub."""
    src_key = "orb_high_ts_source" if "high" in ts_key else "orb_low_ts_source"
    src = str(_orb_row_get(row, src_key) or "").strip().lower()
    if src == "structure_1m_match":
        return False
    ts = _orb_row_get(row, ts_key)
    parsed = ts if isinstance(ts, datetime) else _parse_dt(ts)
    if parsed is None:
        return True
    loc = parsed
    try:
        if loc.tzinfo is not None:
            from zoneinfo import ZoneInfo

            loc = loc.astimezone(ZoneInfo("America/Los_Angeles"))
    except Exception:
        pass
    return loc.hour == 6 and loc.minute == 30


def _orb_row_set(row: Any, key: str, value: Any, *, overwrite: bool = False) -> bool:
    """Write an extreme-ts field onto an ORBData object or dict row."""
    if row is None or value is None:
        return False
    current = _orb_row_get(row, key)
    if current is not None and not overwrite:
        return False
    write_val = value
    if key.endswith("_ts") and not isinstance(value, datetime):
        parsed = _parse_dt(value)
        if parsed is not None:
            write_val = parsed
    if isinstance(row, dict):
        row[key] = write_val
        return True
    try:
        setattr(row, key, write_val)
        return True
    except Exception:
        return False


def collect_orb2_extreme_ts(system: Any) -> Dict[str, Dict[str, Any]]:
    """Snapshot refined ORB2 extreme timestamps from in-memory orb2_data.

    Runtime stores ``{symbol: ORBData}`` objects (and occasionally dict rows).
    Both shapes must persist A1 timestamps after Yahoo 1m refine.
    """
    out: Dict[str, Dict[str, Any]] = {}
    mgr = getattr(system, "orb_strategy_manager", None)
    data = getattr(mgr, "orb2_data", None) if mgr is not None else None
    if not isinstance(data, dict):
        data = getattr(system, "_orb2_data", None)
    if not isinstance(data, dict):
        return out
    for sym, row in data.items():
        if row is None:
            continue
        u = str(sym or "").strip().upper()
        hi_ts = _orb_row_get(row, "orb_high_extreme_ts")
        lo_ts = _orb_row_get(row, "orb_low_extreme_ts")
        if not hi_ts and not lo_ts:
            continue
        out[u] = {
            "orb_high_extreme_ts": _iso(hi_ts) if not isinstance(hi_ts, str) else hi_ts,
            "orb_low_extreme_ts": _iso(lo_ts) if not isinstance(lo_ts, str) else lo_ts,
            "orb_high_ts_source": _orb_row_get(row, "orb_high_ts_source"),
            "orb_low_ts_source": _orb_row_get(row, "orb_low_ts_source"),
        }
    return out


def apply_orb2_extreme_ts(system: Any, extreme_map: Dict[str, Any]) -> int:
    if not isinstance(extreme_map, dict) or not extreme_map:
        return 0
    mgr = getattr(system, "orb_strategy_manager", None)
    targets = []
    if mgr is not None and isinstance(getattr(mgr, "orb2_data", None), dict):
        targets.append(mgr.orb2_data)
    mem = getattr(system, "_orb2_data", None)
    if isinstance(mem, dict):
        targets.append(mem)
    n = 0
    for data in targets:
        for sym, row in extreme_map.items():
            if not isinstance(row, dict):
                continue
            u = str(sym or "").strip().upper()
            dest = data.get(u)
            if dest is None:
                continue
            wrote = False
            for ts_key, src_key in (
                ("orb_high_extreme_ts", "orb_high_ts_source"),
                ("orb_low_extreme_ts", "orb_low_ts_source"),
            ):
                overwrite = _is_placeholder_orb_extreme(dest, ts_key)
                if _orb_row_set(dest, ts_key, row.get(ts_key), overwrite=overwrite):
                    wrote = True
                if _orb_row_set(dest, src_key, row.get(src_key), overwrite=overwrite):
                    wrote = True
            if wrote or _orb_row_get(dest, "orb_high_extreme_ts") or _orb_row_get(
                dest, "orb_low_extreme_ts"
            ):
                n += 1
    return n
