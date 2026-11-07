"""Tiny public discovery scan feed for the desk marquee.

Writes the current 24-name rotate window (WAIT_BREAK / CONFIRM / READY) plus any
other in-memory CONFIRM/READY names. Never includes EXECUTED / open-book fills.
Off-loop GCS upload, same pattern as desk_action_pulse.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

CACHE_CONTROL = "public, max-age=0, no-cache"
QG_KEYS = (
    "session_vs_avg_volume",
    "session_vs_avg",
    "direction_volume_pct",
    "rsi",
    "vwap_distance_pct",
    "break_distance_atr",
)

_LOCK = threading.Lock()
_WORKER: Optional[threading.Thread] = None
_DIRTY = False
_PAYLOAD: Dict[str, Any] = {}
_SEQ = 0


def desk_discovery_gcs_path() -> str:
    try:
        from modules.runtime_mode import gcs_namespace

        ns = gcs_namespace()
    except Exception:
        ns = "research_app"
    return f"{ns}/desk_public/discovery.json"


def _num(raw: Any) -> Optional[float]:
    try:
        n = float(raw)
    except (TypeError, ValueError):
        return None
    return n if n == n else None  # noqa: PLR0124


def _state_name(raw: Any) -> str:
    return str(getattr(raw, "name", None) or raw or "").strip()


def _is_filled(state: str) -> bool:
    u = str(state or "").upper()
    return "EXECUTED" in u or u in {"FILLED", "FILL", "OPEN"}


def _is_inactive_scan_state(state: str) -> bool:
    """ORB1 EXPIRED / INVALIDATED must not occupy the live 24-name rotate."""
    u = str(state or "").upper()
    return "EXPIRED" in u or "INVALIDATED" in u


def _compact_qg(cand: Any) -> Dict[str, Any]:
    src: Dict[str, Any] = {}
    meta = getattr(cand, "metadata", None)
    if isinstance(meta, dict):
        src.update(meta)
        qg = meta.get("qg_admit")
        if isinstance(qg, dict):
            src.update(qg)
    setup = getattr(cand, "setup_payload", None)
    if isinstance(setup, dict):
        src.update(setup)
        qg = setup.get("qg_admit")
        if isinstance(qg, dict):
            src.update(qg)
    try:
        from modules.qg_admit_snapshot import collect_qg_admit_snapshot

        src.update(collect_qg_admit_snapshot(src) or {})
    except Exception:
        pass
    out: Dict[str, Any] = {}
    for k in QG_KEYS:
        n = _num(src.get(k))
        if n is not None:
            out[k] = n
    slot = src.get("trendline_slot") or getattr(cand, "slot", None)
    if slot:
        out["trendline_slot"] = slot
    return out


def _quote_last(quotes: Dict[str, Any], symbol: str) -> Optional[float]:
    if not isinstance(quotes, dict):
        return None
    raw = quotes.get(symbol) or quotes.get(symbol.lower())
    if raw is None:
        return None
    if isinstance(raw, dict):
        for k in ("lastPrice", "last_price", "last", "close", "regularMarketLastPrice"):
            n = _num(raw.get(k))
            if n is not None:
                return n
        return None
    for k in ("last_price", "lastPrice", "last", "close"):
        n = _num(getattr(raw, k, None))
        if n is not None:
            return n
    return _num(raw)


def compact_scan_row(
    *,
    symbol: str,
    path: str,
    state: str,
    cand: Any = None,
    last_price: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    st = _state_name(state) or "WAIT_BREAK"
    if _is_filled(st) or _is_inactive_scan_state(st):
        return None
    sym = str(symbol or "").strip().upper()
    if not sym:
        return None
    px = last_price
    if px is None and cand is not None:
        px = _num(getattr(cand, "last_price", None))
    side = ""
    slot = None
    if cand is not None:
        side = str(getattr(cand, "option_side", None) or "")
        setup = getattr(cand, "setup_payload", None)
        if isinstance(setup, dict):
            side = side or str(setup.get("option_side") or "")
            slot = setup.get("trendline_slot") or setup.get("slot")
        slot = slot or getattr(cand, "slot", None)
    row: Dict[str, Any] = {
        "symbol": sym,
        "path": path,
        "state": st,
        "option_side": side or None,
        "last_price": px,
        "slot": slot,
    }
    qg = _compact_qg(cand) if cand is not None else {}
    if qg:
        row["qg_admit"] = qg
        if not row.get("slot") and qg.get("trendline_slot"):
            row["slot"] = qg.get("trendline_slot")
    return row


def _window_symbols(system: Any) -> List[str]:
    try:
        from modules.discovery_watch_batch import (
            merged_discovery_window_symbols,
            take_forced_discovery_symbols,
        )

        # Desk emit runs after ORB1/TL watch, often >45s after the quote batch.
        # Age-out here emptied `window` and dumped the first 40 WAIT_BREAK names.
        forced = take_forced_discovery_symbols(system, max_age_sec=0)
        if forced:
            return [str(s).strip().upper() for s in forced if str(s).strip()]
        extra = merged_discovery_window_symbols(system, max_age_sec=0)
        if extra:
            return [str(s).strip().upper() for s in extra if str(s).strip()]
    except Exception:
        pass
    return []


def _quotes(system: Any) -> Dict[str, Any]:
    try:
        from modules.open_position_quote_orchestration import MERGED_DISCOVERY_CACHE_ATTR

        cache = getattr(system, MERGED_DISCOVERY_CACHE_ATTR, None)
        if isinstance(cache, dict) and isinstance(cache.get("quotes"), dict):
            return cache["quotes"]
    except Exception:
        pass
    return {}


def _is_hot_state(state: str) -> bool:
    u = str(state or "").upper()
    return "CONFIRM" in u or "READY" in u


def collect_scan_items(system: Any) -> List[Dict[str, Any]]:
    """Current 24-name rotate + in-memory CONFIRM/READY. No filled/open book."""
    quotes = _quotes(system)
    window = _window_symbols(system)
    hot_items: List[Dict[str, Any]] = []
    wait_items: List[Dict[str, Any]] = []
    seen_hot: set = set()
    seen_wait: set = set()

    def add(row: Optional[Dict[str, Any]], *, hot: bool) -> None:
        if not row:
            return
        sym = str(row.get("symbol") or "")
        if hot:
            key = f"{row.get('path')}|{row.get('slot') or ''}|{sym}"
            if key in seen_hot:
                return
            seen_hot.add(key)
            hot_items.append(row)
            return
        if not sym or sym in seen_wait:
            return
        seen_wait.add(sym)
        wait_items.append(row)

    want = set(window)
    filled_syms: set = set()
    orb1 = getattr(system, "_orb1_0dte_watch_candidates", None) or {}
    orb2 = getattr(system, "_breakout_watch_candidates", None) or {}
    eng = getattr(system, "trendline_signal_engine", None)
    tl_cands = getattr(eng, "candidates", None) or {} if eng is not None else {}

    def ingest(cands: Any, path: str) -> None:
        for cand in (cands or {}).values():
            sym = str(getattr(cand, "symbol", "") or "").strip().upper()
            st = _state_name(getattr(cand, "state", None))
            if _is_filled(st):
                filled_syms.add(sym)
                continue
            if _is_inactive_scan_state(st):
                continue
            hot = _is_hot_state(st)
            if not hot and sym not in want:
                continue
            add(
                compact_scan_row(
                    symbol=sym,
                    path=path,
                    state=st or "WAIT_BREAK",
                    cand=cand,
                    last_price=_quote_last(quotes, sym),
                ),
                hot=hot,
            )

    ingest(orb1, "orb1")
    ingest(orb2, "orb2")
    ingest(tl_cands, "tl")

    have_wait = set(seen_wait)
    for sym in window:
        if sym in have_wait or sym in filled_syms:
            continue
        add(
            compact_scan_row(
                symbol=sym,
                path="scan",
                state="WAIT_BREAK",
                last_price=_quote_last(quotes, sym),
            ),
            hot=False,
        )
    wait_by = {str(r.get("symbol") or ""): r for r in wait_items}
    ordered_wait = [wait_by[s] for s in window if s in wait_by]
    return (hot_items + ordered_wait)[:40]


def desk_discovery_marker_path() -> str:
    try:
        from modules.runtime_mode import gcs_daily_markers_path

        return gcs_daily_markers_path("discovery_scan.json")
    except Exception:
        return "research_app/daily_markers/discovery_scan.json"


def _flush_sync() -> None:
    global _DIRTY
    with _LOCK:
        payload = dict(_PAYLOAD)
        _DIRTY = False
    if not payload:
        return
    try:
        from modules.desk_rejects import rejects_block

        payload["rejects"] = rejects_block()  # off-loop; today's PT block or None
    except Exception as rej_err:
        log.debug("DESK_DISCOVERY_PULSE | rejects skipped | %s", rej_err)
    body = json.dumps(payload, separators=(",", ":"), default=str)
    try:
        from modules.gcs_persistence import get_gcs_persistence

        gcs = get_gcs_persistence()
        if not gcs or not gcs.enabled:
            return
        marker_ok = False
        try:
            marker_ok = bool(
                gcs.upload_string(
                    desk_discovery_marker_path(),
                    body,
                )
            )
        except Exception as marker_err:
            log.debug("DESK_DISCOVERY_PULSE | marker upload skipped | %s", marker_err)
        ok = gcs.upload_string(
            desk_discovery_gcs_path(),
            body,
            cache_control=CACHE_CONTROL,
            content_type="application/json; charset=utf-8",
            public_read=True,
        )
        if ok or marker_ok:
            log.info(
                "DESK_DISCOVERY_PULSE | wrote %d names | seq=%s | public=%s | marker=%s",
                len(payload.get("items") or []),
                payload.get("seq"),
                str(bool(ok)).lower(),
                str(bool(marker_ok)).lower(),
            )
    except Exception as err:
        log.warning("DESK_DISCOVERY_PULSE | upload failed | %s", err)


def _worker() -> None:
    global _WORKER
    while True:
        with _LOCK:
            if not _DIRTY:
                _WORKER = None
                return
        _flush_sync()


def _rotate_meta(system: Any, window: List[str]) -> Dict[str, Any]:
    universe = len(window or [])
    interval = 10.0
    cycle = 60.0
    offset = None
    try:
        from modules.discovery_watch_batch import (
            SHARED_LIST_ROTATE_OFFSET_ATTR,
            discovery_full_list_cycle_sec,
            discovery_interval_sec,
            full_0dte_list_symbols,
        )

        universe = len(full_0dte_list_symbols(system) or []) or universe
        interval = float(discovery_interval_sec())
        cycle = float(discovery_full_list_cycle_sec(universe or len(window or [])))
        nxt = int(getattr(system, SHARED_LIST_ROTATE_OFFSET_ATTR, 0) or 0)
        if window and universe:
            offset = (nxt - len(window)) % max(1, universe)
        else:
            offset = nxt
    except Exception:
        pass
    return {
        "universe": universe or None,
        "interval_sec": interval,
        "cycle_sec": cycle,
        "offset": offset,
    }


def collect_process_risk(system: Any) -> Dict[str, Any]:
    """Live ORB1 / TL / ORB2 arming + ADV liquidity guard. Never invented."""
    names: List[str] = []
    if bool(getattr(system, "_orb1_0dte_watch_active", False)):
        names.append("ORB1")
    if bool(getattr(system, "_trendline_watch_active", False)):
        names.append("TL")
    if bool(getattr(system, "_breakout_watch_active", False)):
        names.append("ORB2")
    paths_armed = "+".join(names) if names else "OFF"
    liq_guard = None
    try:
        from modules.adv_data_manager import get_adv_manager

        adv = get_adv_manager()
        n = len(getattr(adv, "adv_cache", {}) or {})
        stale = bool(adv.is_data_stale()) if hasattr(adv, "is_data_stale") else False
        if n <= 0:
            liq_guard = "MISSING"
        elif stale:
            liq_guard = f"STALE · {n}"
        else:
            liq_guard = f"ON · {n}"
    except Exception:
        liq_guard = None
    return {"paths_armed": paths_armed, "liq_guard": liq_guard}


def emit_desk_discovery(system: Any) -> None:
    """Queue the current discovery window onto the public desk pulse. Never blocks."""
    global _SEQ, _DIRTY, _WORKER, _PAYLOAD
    if system is None:
        return
    try:
        items = collect_scan_items(system)
        window = _window_symbols(system)
        meta = _rotate_meta(system, window)
        process = collect_process_risk(system)
    except Exception as err:
        log.debug("desk_discovery_pulse collect failed | %s", err)
        return
    with _LOCK:
        _SEQ += 1
        _PAYLOAD = {
            "seq": _SEQ,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "window": window,
            "batch": len(window),
            "items": items,
            "universe": meta.get("universe"),
            "interval_sec": meta.get("interval_sec"),
            "cycle_sec": meta.get("cycle_sec"),
            "offset": meta.get("offset"),
            "paths_armed": process.get("paths_armed"),
            "liq_guard": process.get("liq_guard"),
        }
        _DIRTY = True
        if _WORKER is not None and _WORKER.is_alive():
            return
        _WORKER = threading.Thread(target=_worker, name="desk-discovery-pulse", daemon=True)
        _WORKER.start()
