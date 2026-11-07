"""Session watch FSM persistence for CONFIRM / READY / EXECUTED restart safety.

Hot path stays in memory. GCS/local daily markers hold a debounced ``watch_fsm``
snapshot of the hot set only (never pure WAIT_BREAK). Discovery scan order is
unchanged (CSV tier→row fair rotate).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Set

log = logging.getLogger(__name__)

WATCH_FSM_MARKER_KEY = "watch_fsm"
PATH_ORB1 = "orb1"
PATH_ORB2 = "orb2"
PATH_TL = "tl"

HOT_STATES = frozenset(
    {
        "WAITING_FOR_CONFIRMATION",
        "READY_TO_EXECUTE",
        "EXECUTED",
        "waiting_for_confirmation",
        "ready_to_execute",
        "executed",
    }
)

DEBOUNCE_SEC_DEFAULT = 5.0
DIRTY_ATTR = "_watch_fsm_dirty"
LAST_HOT_ATTR = "_watch_fsm_last_hot"
LAST_FLUSH_MONO_ATTR = "_watch_fsm_last_flush_mono"
PENDING_TIMER_ATTR = "_watch_fsm_flush_deadline_mono"


def _state_name(raw: Any) -> str:
    return str(getattr(raw, "name", None) or raw or "").strip()


def _is_hot_state(raw: Any) -> bool:
    st = _state_name(raw)
    if not st:
        return False
    if st in HOT_STATES:
        return True
    u = st.upper()
    return (
        "WAITING_FOR_CONFIRMATION" in u
        or "READY_TO_EXECUTE" in u
        or u.endswith("EXECUTED")
        or u == "EXECUTED"
    )


def _iso(dt: Any) -> Optional[str]:
    if dt is None:
        return None
    if isinstance(dt, datetime):
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    try:
        return str(dt)
    except Exception:
        return None


def _parse_dt(raw: Any) -> Optional[datetime]:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    try:
        s = str(raw).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _qg_admit_from_cand(cand: Any) -> Dict[str, Any]:
    src: Dict[str, Any] = {}
    meta = getattr(cand, "metadata", None)
    if isinstance(meta, dict):
        src.update(meta)
    setup = getattr(cand, "setup_payload", None)
    if isinstance(setup, dict):
        for k, v in setup.items():
            src.setdefault(k, v)
    try:
        from modules.qg_admit_snapshot import collect_qg_admit_snapshot

        return collect_qg_admit_snapshot(src)
    except Exception:
        return {}


def _apply_qg_admit_to_mapping(target: Dict[str, Any], row: Dict[str, Any]) -> None:
    snap = row.get("qg_admit") if isinstance(row.get("qg_admit"), dict) else {}
    if not snap:
        return
    try:
        from modules.qg_admit_snapshot import apply_qg_admit_snapshot

        apply_qg_admit_snapshot(target, snap, overwrite=False)
    except Exception:
        pass


def _serialize_bo_cand(path: str, cand: Any) -> Optional[Dict[str, Any]]:
    if cand is None:
        return None
    st = _state_name(getattr(cand, "state", None))
    if not _is_hot_state(st):
        return None
    sym = str(getattr(cand, "symbol", "") or "").strip().upper()
    if not sym:
        return None
    meta = getattr(cand, "metadata", None)
    meta = dict(meta) if isinstance(meta, dict) else {}
    out = {
        "path": path,
        "symbol": sym,
        "state": st,
        "option_side": str(getattr(cand, "option_side", "") or "") or None,
        "break_price": getattr(cand, "break_price", None),
        "material_break_close": getattr(cand, "material_break_close", None),
        "follow_through_bars": int(getattr(cand, "follow_through_bars", 0) or 0),
        "last_price": getattr(cand, "last_price", None),
        "orb_high": float(getattr(cand, "orb_high", 0.0) or 0.0),
        "orb_low": float(getattr(cand, "orb_low", 0.0) or 0.0),
        "break_ts": _iso(getattr(cand, "break_ts", None)),
        "entry_window_at": _iso(meta.get("entry_window_at")),
        "box_source": meta.get("box_source"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    snap = _qg_admit_from_cand(cand)
    if snap:
        out["qg_admit"] = snap
    return out


def _serialize_tl_cand(cand: Any) -> Optional[Dict[str, Any]]:
    if cand is None:
        return None
    st = _state_name(getattr(cand, "state", None))
    if not _is_hot_state(st):
        return None
    sym = str(getattr(cand, "symbol", "") or "").strip().upper()
    if not sym:
        return None
    setup = getattr(cand, "setup_payload", None)
    setup = dict(setup) if isinstance(setup, dict) else {}
    slot = str(setup.get("trendline_slot") or getattr(cand, "slot", "") or "trendline1")
    out = {
        "path": PATH_TL,
        "symbol": sym,
        "state": st,
        "option_side": str(getattr(cand, "option_side", "") or setup.get("option_side") or "")
        or None,
        "slot": slot,
        "direction": str(getattr(getattr(cand, "direction", None), "value", None)
                         or getattr(cand, "direction", "")
                         or ""),
        "break_price": getattr(cand, "break_price", None),
        "last_price": getattr(cand, "last_price", None),
        "qualified_at": _iso(getattr(cand, "qualified_at", None)),
        "material_break_at": _iso(setup.get("material_break_at") or setup.get("break_timestamp")),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    snap = _qg_admit_from_cand(cand)
    if snap:
        out["qg_admit"] = snap
    return out


def collect_hot_watch_fsm(system: Any) -> Dict[str, Dict[str, Any]]:
    """Build ``{path: {SYM: row}}`` for CONFIRM/READY/EXECUTED only."""
    out: Dict[str, Dict[str, Any]] = {PATH_ORB1: {}, PATH_ORB2: {}, PATH_TL: {}}

    try:
        for cand in (getattr(system, "_orb1_0dte_watch_candidates", None) or {}).values():
            row = _serialize_bo_cand(PATH_ORB1, cand)
            if row:
                out[PATH_ORB1][row["symbol"]] = row
    except Exception:
        pass

    try:
        for cand in (getattr(system, "_breakout_watch_candidates", None) or {}).values():
            row = _serialize_bo_cand(PATH_ORB2, cand)
            if row:
                out[PATH_ORB2][row["symbol"]] = row
    except Exception:
        pass

    try:
        eng = getattr(system, "trendline_signal_engine", None)
        cands = getattr(eng, "candidates", None) or {} if eng is not None else {}
        for cand in cands.values():
            row = _serialize_tl_cand(cand)
            if row:
                # Key by slot+symbol so TL1/TL2/TL3 of same underlying coexist.
                key = f"{row.get('slot') or 'trendline1'}:{row['symbol']}"
                out[PATH_TL][key] = row
    except Exception:
        pass

    return out


def _hot_fingerprint(hot: Dict[str, Dict[str, Any]]) -> str:
    parts = []
    for path in (PATH_ORB1, PATH_ORB2, PATH_TL):
        block = hot.get(path) or {}
        for k in sorted(block.keys()):
            row = block[k] or {}
            progress = (
                row.get("state"),
                row.get("option_side"),
                row.get("break_price"),
                row.get("material_break_close"),
                row.get("follow_through_bars"),
                row.get("break_ts"),
                row.get("entry_window_at"),
                row.get("qualified_at"),
                row.get("material_break_at"),
                row.get("slot"),
            )
            parts.append(f"{path}|{k}|" + "|".join(str(v) for v in progress))
    return ";".join(parts)


def note_watch_fsm_dirty(system: Any, *, force: bool = False) -> None:
    """Compare live hot set to last snapshot; mark dirty when changed."""
    if system is None:
        return
    try:
        hot = collect_hot_watch_fsm(system)
        fp = _hot_fingerprint(hot)
        last = getattr(system, LAST_HOT_ATTR, None)
        if not force and last == fp:
            return
        setattr(system, LAST_HOT_ATTR, fp)
        setattr(system, DIRTY_ATTR, True)
        deadline = float(getattr(system, PENDING_TIMER_ATTR, 0.0) or 0.0)
        now_m = time.monotonic()
        if deadline <= 0 or now_m >= deadline:
            setattr(
                system,
                PENDING_TIMER_ATTR,
                now_m + float(DEBOUNCE_SEC_DEFAULT),
            )
    except Exception as exc:
        log.debug("WATCH_FSM | note_dirty_failed | %s", exc)


def maybe_flush_watch_fsm(system: Any, *, force: bool = False) -> bool:
    """Flush hot set into daily markers when dirty and debounce elapsed (or force)."""
    if system is None:
        return False
    if not force and not bool(getattr(system, DIRTY_ATTR, False)):
        return False
    now_m = time.monotonic()
    deadline = float(getattr(system, PENDING_TIMER_ATTR, 0.0) or 0.0)
    if not force and deadline > 0 and now_m < deadline:
        return False
    tracker = getattr(system, "daily_run_tracker", None)
    if tracker is None or not hasattr(tracker, "record_watch_fsm_merge"):
        setattr(system, DIRTY_ATTR, False)
        return False
    try:
        hot = collect_hot_watch_fsm(system)
        ok = bool(tracker.record_watch_fsm_merge(hot))
        setattr(system, DIRTY_ATTR, False)
        setattr(system, PENDING_TIMER_ATTR, 0.0)
        setattr(system, LAST_FLUSH_MONO_ATTR, now_m)
        setattr(system, LAST_HOT_ATTR, _hot_fingerprint(hot))
        if ok:
            n = sum(len(v or {}) for v in hot.values())
            log.info(
                "WATCH_FSM | action=flushed | hot_count=%d | orb1=%d | orb2=%d | tl=%d | force=%s",
                n,
                len(hot.get(PATH_ORB1) or {}),
                len(hot.get(PATH_ORB2) or {}),
                len(hot.get(PATH_TL) or {}),
                str(bool(force)).lower(),
            )
        return ok
    except Exception as exc:
        log.warning("WATCH_FSM | action=flush_failed | reason=%s", exc)
        return False


def sync_watch_fsm_after_cycle(system: Any) -> None:
    """Call at end of ORB1 / ORB2 / TL watch cycles."""
    note_watch_fsm_dirty(system)
    maybe_flush_watch_fsm(system, force=False)


def flush_watch_fsm_on_shutdown(system: Any) -> None:
    """Best-effort immediate flush (SIGTERM / EOD)."""
    note_watch_fsm_dirty(system, force=True)
    maybe_flush_watch_fsm(system, force=True)


def _apply_bo_row(cand: Any, row: Dict[str, Any], state_enum: Any) -> None:
    st_raw = str(row.get("state") or "")
    st_u = st_raw.upper()
    if "READY_TO_EXECUTE" in st_u:
        cand.state = state_enum.READY_TO_EXECUTE
    elif "WAITING_FOR_CONFIRMATION" in st_u:
        cand.state = state_enum.WAITING_FOR_CONFIRMATION
    elif "EXECUTED" in st_u:
        cand.state = state_enum.EXECUTED
    else:
        return
    if row.get("option_side"):
        cand.option_side = str(row.get("option_side")).lower()
    if row.get("break_price") is not None:
        try:
            cand.break_price = float(row["break_price"])
        except (TypeError, ValueError):
            pass
    if row.get("material_break_close") is not None:
        try:
            cand.material_break_close = float(row["material_break_close"])
        except (TypeError, ValueError):
            pass
    try:
        cand.follow_through_bars = int(row.get("follow_through_bars") or 0)
    except (TypeError, ValueError):
        pass
    if row.get("last_price") is not None:
        try:
            cand.last_price = float(row["last_price"])
        except (TypeError, ValueError):
            pass
    bt = _parse_dt(row.get("break_ts"))
    if bt is not None:
        cand.break_ts = bt
    meta = cand.metadata if isinstance(getattr(cand, "metadata", None), dict) else {}
    if row.get("entry_window_at"):
        meta["entry_window_at"] = row.get("entry_window_at")
    if row.get("box_source"):
        meta["box_source"] = row.get("box_source")
    _apply_qg_admit_to_mapping(meta, row)
    meta["watch_fsm_restored"] = True
    cand.metadata = meta


def _ensure_bo_candidate(
    watch: Dict[str, Any],
    row: Dict[str, Any],
    *,
    BreakoutCandidate: Any,
    BreakoutCandidateState: Any,
) -> Optional[Any]:
    sym = str(row.get("symbol") or "").strip().upper()
    if not sym:
        return None
    oh = float(row.get("orb_high") or 0.0)
    ol = float(row.get("orb_low") or 0.0)
    if oh <= 0 or ol <= 0:
        return None
    cand = watch.get(sym)
    if cand is None:
        cand = BreakoutCandidate(
            symbol=sym,
            orb_high=oh,
            orb_low=ol,
            seeded_at=datetime.now(timezone.utc),
            price_at_seed=ol,
            state=BreakoutCandidateState.WAITING_FOR_BREAK,
            last_price=None,
            metadata={},
        )
        watch[sym] = cand
    else:
        # Never invent levels — only restore FSM onto existing freeze geometry.
        if float(getattr(cand, "orb_high", 0) or 0) <= 0:
            cand.orb_high = oh
        if float(getattr(cand, "orb_low", 0) or 0) <= 0:
            cand.orb_low = ol
    _apply_bo_row(cand, row, BreakoutCandidateState)
    return cand


def _apply_tl_state(cand: Any, row: Dict[str, Any], TrendlineCandidateState: Any) -> None:
    st_u = str(row.get("state") or "").upper()
    if "READY_TO_EXECUTE" in st_u:
        cand.state = TrendlineCandidateState.READY_TO_EXECUTE
    elif "WAITING_FOR_CONFIRMATION" in st_u:
        cand.state = TrendlineCandidateState.WAITING_FOR_CONFIRMATION
    elif "EXECUTED" in st_u:
        try:
            cand.state = TrendlineCandidateState.EXECUTED
        except Exception:
            pass
    if row.get("option_side"):
        try:
            cand.option_side = str(row.get("option_side")).lower()
        except Exception:
            pass
    if row.get("last_price") is not None:
        try:
            cand.last_price = float(row["last_price"])
        except (TypeError, ValueError):
            pass
    setup = cand.setup_payload if isinstance(getattr(cand, "setup_payload", None), dict) else {}
    if row.get("material_break_at"):
        setup.setdefault("material_break_at", row.get("material_break_at"))
        setup.setdefault("break_timestamp", row.get("material_break_at"))
    _apply_qg_admit_to_mapping(setup, row)
    setup["watch_fsm_restored"] = True
    cand.setup_payload = setup


def hydrate_watch_fsm_from_markers(system: Any) -> int:
    """Restore CONFIRM/READY/EXECUTED onto in-memory candidates from day markers.

    Requires frozen geometry already present (ORB levels / TL lines). Returns
    number of symbols/slots restored.
    """
    if system is None:
        return 0
    tracker = getattr(system, "daily_run_tracker", None)
    if tracker is None or not hasattr(tracker, "get_today_state"):
        return 0
    try:
        state = tracker.get_today_state() or {}
    except Exception:
        return 0
    blob = state.get(WATCH_FSM_MARKER_KEY)
    if not isinstance(blob, dict):
        return 0
    paths = blob.get("paths") if isinstance(blob.get("paths"), dict) else blob
    if not isinstance(paths, dict):
        return 0

    restored = 0
    try:
        from easyBreakout.breakout_models import BreakoutCandidate, BreakoutCandidateState
    except Exception:
        BreakoutCandidate = None  # type: ignore
        BreakoutCandidateState = None  # type: ignore

    # ORB1
    orb1_rows = paths.get(PATH_ORB1) if isinstance(paths.get(PATH_ORB1), dict) else {}
    if orb1_rows and BreakoutCandidate is not None:
        watch = dict(getattr(system, "_orb1_0dte_watch_candidates", None) or {})
        for row in orb1_rows.values():
            if not isinstance(row, dict):
                continue
            # Require freeze levels from memory or row (row levels from prior stamp).
            if _ensure_bo_candidate(
                watch, row, BreakoutCandidate=BreakoutCandidate, BreakoutCandidateState=BreakoutCandidateState
            ):
                restored += 1
        if watch:
            system._orb1_0dte_watch_candidates = watch
            # Never re-arm ORB1 discovery after 07:25 (same hard stop as TL0).
            try:
                from modules.orb1_0dte_watch import (
                    expire_orb1_discovery_after_cutoff,
                    orb1_discovery_session_open,
                )

                if orb1_discovery_session_open():
                    system._orb1_0dte_watch_active = True
                else:
                    expire_orb1_discovery_after_cutoff(system)
            except Exception:
                system._orb1_0dte_watch_active = True

    # ORB2
    orb2_rows = paths.get(PATH_ORB2) if isinstance(paths.get(PATH_ORB2), dict) else {}
    if orb2_rows and BreakoutCandidate is not None:
        watch2 = dict(getattr(system, "_breakout_watch_candidates", None) or {})
        for row in orb2_rows.values():
            if not isinstance(row, dict):
                continue
            if _ensure_bo_candidate(
                watch2, row, BreakoutCandidate=BreakoutCandidate, BreakoutCandidateState=BreakoutCandidateState
            ):
                restored += 1
        if watch2:
            system._breakout_watch_candidates = watch2
            system._breakout_watch_active = True

    # TL — overlay state onto geometry-hydrated candidates only (no invent without line).
    tl_rows = paths.get(PATH_TL) if isinstance(paths.get(PATH_TL), dict) else {}
    eng = getattr(system, "trendline_signal_engine", None)
    if tl_rows and eng is not None:
        try:
            from easyTrendline import TrendlineCandidateState
        except Exception:
            TrendlineCandidateState = None  # type: ignore
        cands = getattr(eng, "candidates", None) or {}
        if TrendlineCandidateState is not None and cands:
            by_sym_slot: Dict[str, Any] = {}
            for c in cands.values():
                sym = str(getattr(c, "symbol", "") or "").strip().upper()
                setup = getattr(c, "setup_payload", None)
                setup = setup if isinstance(setup, dict) else {}
                slot = str(setup.get("trendline_slot") or "trendline1")
                by_sym_slot[f"{slot}:{sym}"] = c
                by_sym_slot[sym] = c  # last-wins fallback
            for key, row in tl_rows.items():
                if not isinstance(row, dict):
                    continue
                cand = by_sym_slot.get(str(key))
                if cand is None:
                    slot = str(row.get("slot") or "trendline1")
                    sym = str(row.get("symbol") or "").strip().upper()
                    cand = by_sym_slot.get(f"{slot}:{sym}") or by_sym_slot.get(sym)
                if cand is None:
                    # Do not invent TL without frozen A1/A2/slope.
                    continue
                _apply_tl_state(cand, row, TrendlineCandidateState)
                restored += 1

    setattr(system, LAST_HOT_ATTR, _hot_fingerprint(collect_hot_watch_fsm(system)))
    setattr(system, DIRTY_ATTR, False)
    if restored:
        log.info("WATCH_FSM | action=hydrated | restored=%d", restored)
    return restored
