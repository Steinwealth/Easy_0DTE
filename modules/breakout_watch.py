#!/usr/bin/env python3
"""
Breakout 0DTE watch orchestration (seed @ scan / freeze @ 7:30 / watch+execute).

Universe: full 0dte_list (fair-rotate). Levels: 6:30–7:30 PT high/low frozen at
activate. When price breaks above first-hour high → CALL; below low → PUT — only
after continuation confirmation + quality gates (volume/VWAP/RSI/score) + pc_oi.
Paths discover independently: an ORB (or Trendline) fill does **not** remove a
symbol from Breakout candidacy. Same-path BO executed-today / open BO only.

Also records Priority Optimizer execution snapshots + profit_optimizer shadow fills.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Set, Tuple

log = logging.getLogger(__name__)


def _cfg(system: Any):
    cfg = getattr(system, "_breakout_config", None)
    if cfg is not None:
        return cfg
    from easyBreakout.breakout_config_loader import load_breakout_config_from_env

    try:
        from modules.config_loader import get_config_value

        cfg = load_breakout_config_from_env(get_config_value)
    except Exception:
        cfg = load_breakout_config_from_env()
    system._breakout_config = cfg
    return cfg


def seed_breakout_candidate(
    system: Any,
    *,
    symbol: str,
    orb_high: float,
    orb_low: float,
    price: float,
    volume_ratio: float = 1.0,
    vwap: Optional[float] = None,
    vwap_distance_pct: Optional[float] = None,
    rsi: Optional[float] = None,
    extra_meta: Optional[Dict[str, Any]] = None,
) -> bool:
    """Record scan-time tech for a 0DTE symbol (optional metadata for activate).

    Watch universe is no longer Mid-only — activate builds from full 0dte_list
    with 6:30–7:30 levels. Seed still stores volume/VWAP/RSI when available.
    """
    if not getattr(system, "_enable_breakout_strategy", False):
        return False
    from easyBreakout.breakout_models import BreakoutCandidate, BreakoutCandidateState

    sym = str(symbol or "").strip().upper()
    if not sym:
        return False
    # Placeholder levels until activate freezes first-hour H/L (orb_* kept for compat).
    oh = float(orb_high) if float(orb_high or 0) > 0 else 0.0
    ol = float(orb_low) if float(orb_low or 0) > 0 else 0.0

    seeds: Dict[str, Any] = getattr(system, "_breakout_seed_candidates", None) or {}
    now = datetime.now(timezone.utc)
    existing = seeds.get(sym)
    if existing is not None:
        existing.price_at_seed = float(price)
        existing.last_price = float(price)
        existing.volume_ratio = float(volume_ratio or existing.volume_ratio or 1.0)
        if oh > 0 and ol > 0:
            existing.orb_high = oh
            existing.orb_low = ol
        if vwap is not None:
            existing.vwap = float(vwap)
        if vwap_distance_pct is not None:
            existing.vwap_distance_pct = float(vwap_distance_pct)
        if rsi is not None:
            existing.rsi = float(rsi)
        seeds[sym] = existing
    else:
        seeds[sym] = BreakoutCandidate(
            symbol=sym,
            orb_high=oh if oh > 0 else float(price or 0.0),
            orb_low=ol if ol > 0 else float(price or 0.0),
            seeded_at=now,
            price_at_seed=float(price),
            state=BreakoutCandidateState.WAITING_FOR_BREAK,
            volume_ratio=float(volume_ratio or 1.0),
            vwap=float(vwap) if vwap is not None else None,
            vwap_distance_pct=float(vwap_distance_pct) if vwap_distance_pct is not None else None,
            rsi=float(rsi) if rsi is not None else None,
            metadata=dict(extra_meta or {}),
        )
    system._breakout_seed_candidates = seeds
    return True


def resolve_breakout_universe(system: Any) -> List[str]:
    """Full 0dte_list in stable watchlist order (same sources as Trendline).

    Universe is the scrubbed watchlist (ETFs/equities only — cash indexes like
    SPX/RUT are omitted from ``0dte_list.csv``, not filtered here).
    """
    source_symbols: List[str] = []
    if hasattr(system, "dte0_manager") and system.dte0_manager and getattr(
        system.dte0_manager, "target_symbols", None
    ):
        source_symbols = [
            str(s).strip().upper() for s in (system.dte0_manager.target_symbols or []) if s
        ]
    if not source_symbols:
        try:
            from easy0DTE.modules.prime_0dte_strategy_manager import load_0dte_symbols

            loaded = load_0dte_symbols()
            source_symbols = [str(s).strip().upper() for s in (loaded or []) if s]
        except Exception as e:
            log.warning(
                "BREAKOUT_PIPELINE | stage=universe | action=load_0dte_symbols_failed | detail=%s",
                e,
            )
    if not source_symbols:
        try:
            import pandas as pd

            for dte_list_path in (
                "/app/data/watchlist/0dte_list.csv",
                "data/watchlist/0dte_list.csv",
            ):
                if os.path.exists(dte_list_path):
                    df = pd.read_csv(dte_list_path, comment="#")
                    col = "symbol" if "symbol" in df.columns else df.columns[0]
                    source_symbols = [
                        str(s).strip().upper() for s in df[col].tolist() if s
                    ]
                    break
        except Exception as e2:
            log.warning(
                "BREAKOUT_PIPELINE | stage=universe | action=csv_load_failed | detail=%s",
                e2,
            )
    return list(dict.fromkeys([s for s in source_symbols if s]))


def list_breakout_seed_symbols(
    system: Any,
    *,
    exclude: Optional[Set[str]] = None,
    order_map: Optional[Dict[str, int]] = None,
) -> List[str]:
    """Scan-seeded symbols (tech cache), watchlist-ordered.

    Excludes names that already qualified for ORB1 Breakout CALL/PUT collection.
    """
    if not getattr(system, "_enable_breakout_strategy", False):
        return []
    seeds: Dict[str, Any] = getattr(system, "_breakout_seed_candidates", None) or {}
    if not seeds:
        return []
    blocked = {str(s or "").strip().upper() for s in (exclude or set()) if str(s or "").strip()}
    syms = [
        str(sym).strip().upper()
        for sym in seeds.keys()
        if str(sym or "").strip() and str(sym).strip().upper() not in blocked
    ]
    if order_map:
        syms.sort(key=lambda s: (0, order_map[s]) if s in order_map else (1, s))
    else:
        syms.sort()
    return syms


def breakout_candidates_for_collection_alert(
    system: Any,
    *,
    dte0_signals_list: Optional[List[Any]] = None,
    dte_symbols_list: Optional[List[Any]] = None,
    order_map: Optional[Dict[str, int]] = None,
) -> List[str]:
    """Preview symbols for Trade Signal Collection Telegram (full-list intent).

    Prefers resolved 0dte_list (paths discover independently — ORB-ready names stay);
    falls back to scan seeds.
    """
    # Cross-path: do not strip ORB / Trendline candidates from Breakout preview.
    _ = (dte0_signals_list, dte_symbols_list)
    exclude: Set[str] = set()

    universe = resolve_breakout_universe(system)
    if universe:
        syms = list(universe)
        if order_map:
            syms.sort(key=lambda s: (0, order_map[s]) if s in order_map else (1, s))
        return syms
    return list_breakout_seed_symbols(system, exclude=exclude, order_map=order_map)


def _breakout_same_path_blocked_symbols(system: Any) -> Set[str]:
    """Breakout no longer day-locks a symbol after fill or while a BO is open.

    Same-side replay is owned by the 1h cooldown + ``reentry_required_sides``.
    Opposite ORB2 H/L stays scannable even while a CALL or PUT fill is open.
    Same-side stacking while a BO is still open is blocked by
    ``_breakout_has_open_same_side`` (Trendline parity).
    """
    _ = system
    return set()


_BO_POSITION_ID_SIDE_RE = re.compile(
    r"-(\d+(?:\.\d+)?)([cCpP])-(?:[A-Za-z0-9]+)$"
)


def _breakout_position_option_side(pos: Any) -> str:
    """CALL/PUT for an open Breakout row (attr, metadata, then trade-id suffix)."""
    try:
        from modules.unified_0dte_account import trendline_position_option_side

        side = str(trendline_position_option_side(pos) or "").strip().lower()
    except Exception:
        side = str(getattr(pos, "option_side", "") or "").strip().lower()
    if side in ("c", "call"):
        return "call"
    if side in ("p", "put"):
        return "put"
    ptype = str(getattr(pos, "position_type", "") or "").strip().lower()
    if "long_call" in ptype or ptype.endswith("_call"):
        return "call"
    if "long_put" in ptype or ptype.endswith("_put"):
        return "put"
    pid = str(getattr(pos, "position_id", "") or getattr(pos, "trade_id", "") or "")
    m = _BO_POSITION_ID_SIDE_RE.search(pid)
    if m:
        return "call" if m.group(2).lower() == "c" else "put"
    return ""


def _breakout_has_open_same_side(system: Any, symbol: str, option_side: str) -> bool:
    """True when an open Breakout position exists for same symbol + CALL/PUT."""
    sym = str(symbol or "").strip().upper()
    side = str(option_side or "").strip().lower()
    if side in ("c", "call"):
        side = "call"
    elif side in ("p", "put"):
        side = "put"
    if not sym or side not in ("call", "put"):
        return False
    try:
        from modules.unified_0dte_account import list_open_positions_by_strategy

        for pos in list_open_positions_by_strategy(system, strategy_substring="breakout"):
            pos_sym = str(getattr(pos, "symbol", "") or "").strip().upper()
            if pos_sym != sym:
                continue
            if _breakout_position_option_side(pos) == side:
                return True
    except Exception as exc:
        log.warning(
            "BREAKOUT_PIPELINE | stage=dedupe_guard | action=open_side_check_failed | "
            "symbol=%s | side=%s | error=%s",
            sym,
            side,
            exc,
        )
    return False


def _orb_executed_symbols(system: Any) -> Set[str]:
    """Deprecated alias — retained for callers; same-path BO block only."""
    return _breakout_same_path_blocked_symbols(system)


def _frozen_orb2_levels(system: Any) -> Dict[str, Tuple[float, float]]:
    """Read frozen ORB #2 levels from strategy manager (preferred) or system alias."""
    mgr = getattr(system, "orb_strategy_manager", None)
    if mgr is not None and hasattr(mgr, "orb2_levels_map"):
        try:
            levels = mgr.orb2_levels_map()
            if levels:
                return levels
        except Exception:
            pass
    existing = getattr(system, "_orb2_data", None) or {}
    out: Dict[str, Tuple[float, float]] = {}
    for sym, row in existing.items():
        try:
            if hasattr(row, "as_hl"):
                out[str(sym).upper()] = row.as_hl
            elif isinstance(row, dict):
                hi = float(row.get("orb_high") or 0)
                lo = float(row.get("orb_low") or 0)
                if hi > 0 and lo > 0:
                    out[str(sym).upper()] = (hi, lo)
            else:
                hi = float(getattr(row, "orb_high", 0) or 0)
                lo = float(getattr(row, "orb_low", 0) or 0)
                if hi > 0 and lo > 0:
                    out[str(sym).upper()] = (hi, lo)
        except Exception:
            continue
    return out


# E*TRADE session OHLC for the ORB#2 box is only complete at/after capture end (Shared CAPTURE_ORB2_END_PT).


def _orb2_capture_ready_pt():
    from modules.session_clock import orb2_capture_window_pt

    return orb2_capture_window_pt()[1]


def _orb2_window_label() -> str:
    from modules.session_clock import orb2_capture_label

    return orb2_capture_label()


def _orb2_now_pt() -> datetime:
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("America/Los_Angeles"))


def orb2_session_ohlc_ready(now_pt: Optional[datetime] = None) -> bool:
    """True when PT clock is at or after 07:30 so session H/L covers 06:30–07:30."""
    now = now_pt or _orb2_now_pt()
    try:
        from zoneinfo import ZoneInfo

        if getattr(now, "tzinfo", None) is not None:
            now = now.astimezone(ZoneInfo("America/Los_Angeles"))
    except Exception:
        pass
    return now.time() >= _orb2_capture_ready_pt()


def orb2_post_0dte_capture_permitted(system: Any) -> bool:
    """ORB#2 may capture only after Step 5 ORB1 Breakout handoff (or recovery after that gate)."""
    return bool(getattr(system, "_orb2_capture_permitted_post_0dte", False))


def _orb2_row_capture_time_pt(row: Any) -> Optional[datetime]:
    ct = getattr(row, "capture_time", None)
    if ct is None and isinstance(row, dict):
        ct = row.get("capture_time")
    if ct is None:
        return None
    try:
        from zoneinfo import ZoneInfo

        if getattr(ct, "tzinfo", None) is not None:
            return ct.astimezone(ZoneInfo("America/Los_Angeles"))
        return ct.replace(tzinfo=ZoneInfo("America/Los_Angeles"))
    except Exception:
        return ct if isinstance(ct, datetime) else None


def orb2_freeze_is_premature(system: Any) -> bool:
    """True if today's ORB#2 stamp exists but was frozen before 07:30 PT (incomplete hour)."""
    mgr = getattr(system, "orb_strategy_manager", None)
    rows = getattr(mgr, "orb2_data", None) if mgr is not None else None
    if not rows:
        rows = getattr(system, "_orb2_data", None) or {}
    if not rows:
        return False
    stamped = False
    try:
        if mgr is not None and hasattr(mgr, "orb2_already_captured_today"):
            stamped = bool(mgr.orb2_already_captured_today())
    except Exception:
        stamped = bool(rows)
    if not stamped and not rows:
        return False
    for row in (rows or {}).values():
        ct = _orb2_row_capture_time_pt(row)
        if ct is None:
            return True
        if ct.time() < _orb2_capture_ready_pt():
            return True
    return False


def orb2_freeze_valid_for_tl_bo(system: Any, *, require_session_ready: bool = True) -> bool:
    """True when ORB#2 is stamped today, not premature, and (optionally) clock ≥07:30 PT.

    TL1 build + BO/TL discovery unlock must use this — never a raw
    ``orb2_already_captured_today()`` stamp alone (that can be a 06:30 incomplete freeze).
    """
    if require_session_ready and not orb2_session_ohlc_ready():
        return False
    if orb2_freeze_is_premature(system):
        return False
    mgr = getattr(system, "orb_strategy_manager", None)
    n = 0
    try:
        n = len(getattr(mgr, "orb2_data", None) or {}) or len(
            getattr(system, "_orb2_data", None) or {}
        )
    except Exception:
        n = 0
    if n <= 0:
        return False
    try:
        if mgr is not None and hasattr(mgr, "orb2_already_captured_today"):
            return bool(mgr.orb2_already_captured_today())
    except Exception:
        pass
    try:
        from zoneinfo import ZoneInfo

        today = datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
    except Exception:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return str(getattr(system, "_orb2_captured_pt_date", None) or "") == today


def persist_orb2_freeze_marker(system: Any, *, force: bool = False) -> bool:
    """Write today's valid ORB#2 freeze to daily markers (GCS) — ORB#1 parity.

    Only persists post-07:30 stamps. ``force=True`` replaces a premature marker
    after a real ≥07:30 recapture; otherwise a valid day freeze is never overwritten.
    """
    tracker = getattr(system, "daily_run_tracker", None)
    mgr = getattr(system, "orb_strategy_manager", None)
    if tracker is None or mgr is None or not hasattr(tracker, "record_orb2_capture"):
        return False
    rows = getattr(mgr, "orb2_data", None) or {}
    if not rows:
        return False
    if orb2_freeze_is_premature(system):
        log.warning(
            "ORB_CAPTURE | orb_label=%s | action=skip_persist | reason=premature_freeze",
            "ORB#2",
        )
        return False
    if not orb2_session_ohlc_ready():
        # Allow backfill persist of an already-valid in-memory freeze after restart
        # only when freeze itself is stamped ≥07:30 (checked above via premature).
        if not orb2_freeze_valid_for_tl_bo(system, require_session_ready=False):
            return False
    try:
        snapshot = {
            str(sym).upper(): (
                row.to_dict() if hasattr(row, "to_dict") else dict(row)
            )
            for sym, row in rows.items()
        }
    except Exception as snap_err:
        log.warning(
            "ORB_CAPTURE | orb_label=%s | action=persist_failed | reason=snapshot_error | err=%s",
            "ORB#2",
            snap_err,
        )
        return False
    meta: Dict[str, Any] = {
        "service": os.getenv("K_SERVICE"),
        "revision": os.getenv("K_REVISION"),
        "source": "etrade_session_ohlc",
        "window_pt": "06:30-07:30",
    }
    try:
        from modules.runtime_mode import mode_label

        meta["mode"] = mode_label()
    except Exception:
        pass
    try:
        ok = bool(
            tracker.record_orb2_capture(
                orb2_snapshot=snapshot,
                captured_count=len(snapshot),
                total_symbols=len(snapshot),
                metadata=meta,
                force=force,
            )
        )
        if ok:
            log.info(
                "ORB_CAPTURE | orb_label=%s | action=persisted_marker | captured=%d | force=%s",
                "ORB#2",
                len(snapshot),
                str(force).lower(),
            )
        return ok
    except Exception as persist_err:
        log.warning(
            "ORB_CAPTURE | orb_label=%s | action=persist_failed | reason=%s",
            "ORB#2",
            persist_err,
        )
        return False


def clear_orb2_freeze(system: Any, *, reason: str) -> None:
    """Drop premature / invalid ORB#2 freeze so a post-07:30 capture can run."""
    from easyBreakout.breakout_first_hour import ORB2_LABEL

    mgr = getattr(system, "orb_strategy_manager", None)
    n = 0
    try:
        n = len(getattr(mgr, "orb2_data", None) or {}) or len(
            getattr(system, "_orb2_data", None) or {}
        )
    except Exception:
        n = 0
    if mgr is not None:
        try:
            if hasattr(mgr, "orb2_data"):
                mgr.orb2_data.clear()
            if hasattr(mgr, "_orb2_captured_pt_date"):
                mgr._orb2_captured_pt_date = None
        except Exception:
            pass
    system._orb2_data = {}
    system._orb2_captured_pt_date = None
    log.warning(
        "ORB_CAPTURE | orb_label=%s | action=clear_freeze | reason=%s | prior_symbols=%d",
        ORB2_LABEL,
        reason,
        n,
    )


async def capture_orb2_breakout_ranges(
    system: Any,
    *,
    force: bool = False,
    permitted: bool = False,
) -> Dict[str, Tuple[float, float]]:
    """ORB #2 capture via ETrade batch quotes → ``orb_strategy_manager.orb2_data``.

    Hard rules:
    - PT clock **at or after 07:30** (E*TRADE session H/L = 06:30–07:30).
    - Only after Step 5 ORB1 Breakout handoff (``permitted=True`` or
      ``_orb2_capture_permitted_post_0dte``). No early BO/main-loop capture.
    Does **not** overwrite ORB #1. Premature freezes (captured before 07:30) are cleared.
    """
    from easyBreakout.breakout_first_hour import ORB2_LABEL

    mgr = getattr(system, "orb_strategy_manager", None)
    if mgr is None:
        log.error(
            "ORB_CAPTURE | orb_label=%s | action=abort | reason=no_orb_strategy_manager",
            ORB2_LABEL,
        )
        return _frozen_orb2_levels(system)

    now_pt = _orb2_now_pt()
    if orb2_freeze_is_premature(system):
        clear_orb2_freeze(system, reason="premature_capture_before_0730_pt")
        force = True

    if not (permitted or orb2_post_0dte_capture_permitted(system)):
        log.warning(
            "ORB_CAPTURE | orb_label=%s | action=block | reason=await_post_0dte_step5 | "
            "now_pt=%s",
            ORB2_LABEL,
            now_pt.strftime("%H:%M:%S"),
        )
        return _frozen_orb2_levels(system)

    if not orb2_session_ohlc_ready(now_pt):
        log.warning(
            "ORB_CAPTURE | orb_label=%s | action=wait | reason=before_0730_pt | "
            "now_pt=%s | need=at_or_after_07:30 | window=%s",
            ORB2_LABEL,
            now_pt.strftime("%H:%M:%S"),
            _orb2_window_label(),
        )
        return _frozen_orb2_levels(system)

    # Day-locked: a valid post-07:30 freeze is never overwritten (ignore force).
    # Cloud restarts must reuse memory/GCS levels — never re-pull live session OHLC.
    if (
        hasattr(mgr, "orb2_already_captured_today")
        and mgr.orb2_already_captured_today()
        and not orb2_freeze_is_premature(system)
    ):
        levels = mgr.orb2_levels_map()
        log.info(
            "ORB_CAPTURE | orb_label=%s | action=reuse_frozen | captured=%d | window=%s",
            ORB2_LABEL,
            len(levels),
            _orb2_window_label(),
        )
        system._orb2_data = mgr.orb2_data
        system._orb2_captured_pt_date = getattr(mgr, "_orb2_captured_pt_date", None)
        # Backfill GCS marker if an older revision captured in-memory only.
        try:
            persist_orb2_freeze_marker(system, force=False)
        except Exception:
            pass
        # If freeze already existed but Telegram never fired (restart), send once.
        try:
            send_alert = getattr(system, "_send_orb2_capture_alert_if_needed", None)
            if callable(send_alert):
                await send_alert(capture_time_seconds=0.0)
        except Exception as alert_err:
            log.debug(
                "ORB_CAPTURE | orb_label=%s | reuse_alert_skip | reason=%s",
                ORB2_LABEL,
                alert_err,
            )
        return levels

    universe = resolve_breakout_universe(system)
    if not universe:
        log.warning(
            "ORB_CAPTURE | orb_label=%s | window=%s | action=skip | reason=empty_universe",
            ORB2_LABEL,
            _orb2_window_label(),
        )
        return _frozen_orb2_levels(system)

    dm = getattr(system, "data_manager", None)
    if dm is None or not hasattr(dm, "get_batch_quotes"):
        log.error(
            "ORB_CAPTURE | orb_label=%s | action=abort | reason=no_data_manager",
            ORB2_LABEL,
        )
        return _frozen_orb2_levels(system)

    if hasattr(dm, "ensure_valid_token_before_orb_capture"):
        try:
            if not dm.ensure_valid_token_before_orb_capture():
                log.error(
                    "ORB_CAPTURE | orb_label=%s | action=abort | reason=invalid_etrade_token",
                    ORB2_LABEL,
                )
                return _frozen_orb2_levels(system)
        except Exception as tok_err:
            log.warning(
                "ORB_CAPTURE | orb_label=%s | token_check_error=%s | continuing",
                ORB2_LABEL,
                tok_err,
            )

    log.info(
        "ORB_CAPTURE | orb_label=%s | window=%s PT | START | symbols=%d | source=etrade_batch_quotes",
        ORB2_LABEL,
        _orb2_window_label(),
        len(universe),
    )
    import time as _time_mod

    _orb2_t0 = _time_mod.monotonic()
    try:
        batch_quotes = await dm.get_batch_quotes(universe, skip_cache=True)
    except TypeError:
        batch_quotes = await dm.get_batch_quotes(universe)
    except Exception as e:
        log.error(
            "ORB_CAPTURE | orb_label=%s | action=fetch_failed | reason=%s | keeping_prior=%d",
            ORB2_LABEL,
            e,
            len(getattr(mgr, "orb2_data", {}) or {}),
        )
        return _frozen_orb2_levels(system)
    capture_duration_sec = max(0.0, _time_mod.monotonic() - _orb2_t0)

    ingest = getattr(system, "_ingest_broker_quotes_into_session_tech", None)
    if callable(ingest) and batch_quotes:
        try:
            ingest(batch_quotes)
        except Exception as ingest_err:
            log.debug(
                "ORB_CAPTURE | orb_label=%s | session_tech_ingest_failed | err=%s",
                ORB2_LABEL,
                ingest_err,
            )

    if not batch_quotes:
        log.error(
            "ORB_CAPTURE | orb_label=%s | action=empty_quotes | keeping_prior=%d",
            ORB2_LABEL,
            len(getattr(mgr, "orb2_data", {}) or {}),
        )
        return _frozen_orb2_levels(system)

    now_pt = _orb2_now_pt()
    captured = 0
    failed: List[str] = []
    for sym in universe:
        q = batch_quotes.get(sym) or batch_quotes.get(sym.upper())
        row = mgr.ingest_session_ohlc_orb2(sym, q or {}, capture_time=now_pt, overwrite=force)
        if row is None:
            failed.append(sym)
        else:
            captured += 1

    if captured > 0:
        mgr._orb2_captured_pt_date = now_pt.strftime("%Y-%m-%d")
        system._orb2_data = mgr.orb2_data
        system._orb2_captured_pt_date = mgr._orb2_captured_pt_date
        log.info(
            "ORB_CAPTURE | orb_label=%s | window=%s PT | DONE | captured=%d | failed=%d | "
            "store=orb_strategy_manager.orb2_data | kept_separate_from=ORB#1",
            ORB2_LABEL,
            _orb2_window_label(),
            captured,
            len(failed),
        )
        # Persist day freeze for Cloud Run restarts (same durability as ORB#1).
        try:
            persist_orb2_freeze_marker(system, force=True)
        except Exception as persist_err:
            log.warning(
                "ORB_CAPTURE | orb_label=%s | action=persist_error | reason=%s",
                ORB2_LABEL,
                persist_err,
            )
        # Telegram: one ORB2 Capture Complete alert per day (ORB1 parity).
        try:
            send_alert = getattr(system, "_send_orb2_capture_alert_if_needed", None)
            if callable(send_alert):
                await send_alert(capture_time_seconds=capture_duration_sec)
        except Exception as alert_err:
            log.warning(
                "ORB_CAPTURE | orb_label=%s | action=alert_error | reason=%s",
                ORB2_LABEL,
                alert_err,
            )
    else:
        log.error(
            "ORB_CAPTURE | orb_label=%s | action=zero_captured | failed=%d | keeping_prior=%d",
            ORB2_LABEL,
            len(failed),
            len(getattr(mgr, "orb2_data", {}) or {}),
        )

    if failed and len(failed) <= 20:
        log.warning(
            "ORB_CAPTURE | orb_label=%s | failed_symbols=%s",
            ORB2_LABEL,
            ",".join(sorted(failed)),
        )
    elif failed:
        log.warning(
            "ORB_CAPTURE | orb_label=%s | failed_count=%d | sample=%s",
            ORB2_LABEL,
            len(failed),
            ",".join(sorted(failed)[:12]),
        )
    return _frozen_orb2_levels(system)


async def activate_breakout_watch_async(system: Any) -> int:
    """Freeze Breakout watch from **already captured** valid ORB #2.

    Never captures ORB#2 here — capture is only post Step 5 ORB1 Breakout
    (``_ensure_orb2_captured_for_tl_bo``). Activate waits for a valid freeze.
    """
    if not getattr(system, "_enable_breakout_strategy", False):
        return 0

    universe = resolve_breakout_universe(system)
    # Prefer day-freeze markers when memory empty (cold start / restart).
    hydrated = 0
    try:
        hydrate_fn = getattr(system, "_hydrate_orb2_from_daily_markers", None)
        if callable(hydrate_fn):
            hydrated = int(hydrate_fn() or 0)
    except Exception as hydrate_err:
        log.warning(
            "BREAKOUT_PIPELINE | stage=activate | action=hydrate_failed | reason=%s",
            hydrate_err,
        )
    if orb2_freeze_is_premature(system):
        clear_orb2_freeze(system, reason="activate_rejected_premature_orb2")

    levels = _frozen_orb2_levels(system)
    if not levels:
        log.warning(
            "BREAKOUT_PIPELINE | stage=activate | action=wait | "
            "reason=orb2_missing_await_post_0dte_capture | hydrate_attempted=true | "
            "hydrated=%d",
            hydrated,
        )
        return 0

    return activate_breakout_watch(
        system, levels_by_symbol=levels, universe=universe
    )

def activate_breakout_watch(
    system: Any,
    *,
    levels_by_symbol: Optional[Dict[str, Tuple[float, float]]] = None,
    universe: Optional[List[str]] = None,
) -> int:
    """Freeze Breakout watchlist after ORB 7:30 batch.

    Uses full 0dte_list + ORB #2 (6:30–7:30) H/L from day-freeze / ETrade.
    No Mid-only filter. Pass ``levels_by_symbol`` from async activate (or tests);
    symbols without levels are skipped. When levels are omitted/empty, hydrate
    ORB#2 from daily markers before reading memory.
    """
    if not getattr(system, "_enable_breakout_strategy", False):
        return 0
    from easyBreakout.breakout_models import BreakoutCandidate, BreakoutCandidateState

    if not levels_by_symbol:
        try:
            hydrate_fn = getattr(system, "_hydrate_orb2_from_daily_markers", None)
            if callable(hydrate_fn):
                hydrate_fn()
        except Exception:
            pass
        levels_by_symbol = levels_by_symbol or _frozen_orb2_levels(system)

    levels = {
        str(k).strip().upper(): (float(v[0]), float(v[1]))
        for k, v in (levels_by_symbol or {}).items()
        if v and float(v[0]) > 0 and float(v[1]) > 0 and float(v[0]) >= float(v[1])
    }
    symbols = [str(s).strip().upper() for s in (universe or []) if str(s or "").strip()]
    if not symbols:
        symbols = resolve_breakout_universe(system)
    if not symbols and levels:
        symbols = sorted(levels.keys())

    seeds: Dict[str, Any] = dict(getattr(system, "_breakout_seed_candidates", None) or {})
    blocked = _breakout_same_path_blocked_symbols(system)
    watch: Dict[str, Any] = {}
    now = datetime.now(timezone.utc)
    missing_levels = 0

    for sym in symbols:
        if sym in blocked:
            continue
        hl = levels.get(sym)
        if not hl:
            missing_levels += 1
            continue
        hi, lo = float(hl[0]), float(hl[1])
        seed = seeds.get(sym)
        px = float(getattr(seed, "last_price", 0) or getattr(seed, "price_at_seed", 0) or 0.0)
        box_meta = {
            "box_source": "orb2_630_730",
            "orb_label": "ORB#2",
            "window_pt": "06:30-07:30",
            "data_source": "etrade_session_ohlc",
        }
        if seed is not None:
            seed.orb_high = hi
            seed.orb_low = lo
            seed.state = BreakoutCandidateState.WAITING_FOR_BREAK
            meta = dict(getattr(seed, "metadata", None) or {})
            meta.update(box_meta)
            seed.metadata = meta
            watch[sym] = seed
        else:
            watch[sym] = BreakoutCandidate(
                symbol=sym,
                orb_high=hi,
                orb_low=lo,
                seeded_at=now,
                price_at_seed=px if px > 0 else lo,
                state=BreakoutCandidateState.WAITING_FOR_BREAK,
                last_price=px if px > 0 else None,
                metadata=dict(box_meta),
            )

    system._breakout_watch_candidates = watch
    system._breakout_watch_active = bool(watch)
    system._breakout_activated_pt_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # Overlay CONFIRM/READY from day marker after freeze stamp (activate replaces memory).
    try:
        from modules.discovery_watch_fsm_persist import hydrate_watch_fsm_from_markers

        hydrate_watch_fsm_from_markers(system)
    except Exception:
        pass
    watch = getattr(system, "_breakout_watch_candidates", None) or watch
    log.info(
        "BREAKOUT_PIPELINE | stage=activate | watch_count=%d | universe=%d | "
        "levels=%d | missing_levels=%d | seeds=%d | same_path_blocked=%d | "
        "orb_label=ORB#2 | box_source=orb2_630_730 | active=%s",
        len(watch),
        len(symbols),
        len(levels),
        missing_levels,
        len(seeds),
        len(blocked),
        str(bool(watch)).lower(),
    )
    if watch:
        sample = sorted(watch.keys())[:12]
        log.info(
            "BREAKOUT_PIPELINE | stage=activate_sample | symbols=%s%s",
            ",".join(sample),
            "..." if len(watch) > 12 else "",
        )
    return len(watch)


def _quote_price(quote: Any) -> float:
    if not isinstance(quote, dict):
        return 0.0
    for key in ("last", "last_price", "close", "price", "mark"):
        try:
            px = float(quote.get(key) or 0.0)
        except (TypeError, ValueError):
            continue
        if px > 0:
            return px
    return 0.0


def _breakout_state_priority(cand: Any) -> int:
    st = str(getattr(cand, "state", "") or "").upper()
    name = str(getattr(getattr(cand, "state", None), "name", "") or "").upper()
    blob = f"{st} {name}"
    if "READY_TO_EXECUTE" in blob:
        return 0
    if "WAITING_FOR_CONFIRMATION" in blob:
        return 1
    if "WAITING_FOR_BREAK" in blob:
        return 2
    return 3


def _rearm_breakout_to_wait_break(cand: Any, *, reason: str, system: Any = None) -> None:
    """Drop a failed attempt and return symbol to base rotating scan (WAIT_BREAK).

    Clears side/break tracking so a later opposite-edge break (e.g. CALL rejected
    by pc_oi, then PUT break) can confirm cleanly. Does not day-kill the symbol.
    Production single-side rejects use ``_side_day_block_and_rearm_breakout``.
    """
    from easyBreakout.breakout_models import BreakoutCandidateState

    cand.state = BreakoutCandidateState.WAITING_FOR_BREAK
    cand.option_side = None
    cand.break_price = None
    cand.break_ts = None
    cand.material_break_close = None
    cand.post_break_high = None
    cand.post_break_low = None
    cand.follow_through_bars = 0
    cand.failed_reasons = list(cand.failed_reasons or []) + [str(reason or "rearm_wait_break")]
    meta = cand.metadata if isinstance(cand.metadata, dict) else {}
    meta.pop("pc_oi_blocked", None)
    meta.pop("quality_hard_reject_count", None)
    meta.pop("exec_ready_at", None)
    meta.pop("entry_window_at", None)
    meta["last_rearm_reason"] = str(reason or "")
    cand.metadata = meta


# Post-executor reasons that will not heal on immediate retry (chain/liquidity/slot).
# Canonical set lives in path_exec_day_exclude; keep alias for existing imports/tests.
def _breakout_terminal_reasons() -> frozenset:
    from modules.path_exec_day_exclude import TERMINAL_PATH_EXEC_REJECT_REASONS

    return TERMINAL_PATH_EXEC_REJECT_REASONS


_BREAKOUT_TERMINAL_POST_EXEC_REASONS = None  # resolved lazily via helpers


def _breakout_post_exec_reason_code(err: str) -> str:
    from modules.path_exec_day_exclude import normalize_exec_reject_reason

    return normalize_exec_reject_reason(err)


def _is_breakout_terminal_post_exec_reject(err: str) -> bool:
    from modules.path_exec_day_exclude import is_terminal_path_exec_reject

    return bool(is_terminal_path_exec_reject(err))


def _breakout_terminal_reject_side_key(symbol: str, side: str) -> str:
    return f"{str(symbol or '').strip().upper()}:{str(side or '').strip().lower()}"


def _breakout_side_reject_cooldown_seconds(system: Any = None) -> float:
    try:
        from modules.config_loader import get_config_value

        raw = get_config_value("BREAKOUT_SIDE_REJECT_COOLDOWN_SECONDS", 3600)
    except Exception:
        raw = os.getenv("BREAKOUT_SIDE_REJECT_COOLDOWN_SECONDS", "3600")
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 3600.0


def _env_bool_breakout(key: str, default: bool = True) -> bool:
    try:
        from modules.config_loader import get_config_value

        raw = get_config_value(key, default)
    except Exception:
        raw = os.getenv(key, str(default))
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _env_float_breakout(key: str, default: float) -> float:
    try:
        from modules.config_loader import get_config_value

        raw = get_config_value(key, default)
    except Exception:
        raw = os.getenv(key, str(default))
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


def maybe_release_breakout_after_profitable_exit(
    system: Any,
    *,
    symbol: str,
    option_side: Optional[str] = None,
    realized_pnl_pct: Optional[float] = None,
) -> bool:
    """After any filled BO exit, re-arm WAIT_BREAK so the opposite ORB2 edge can trade.

    Profit and loss both release the symbol-level day-executed lock. The exited
    side keeps the 1h cooldown and must reclaim inside ORB2 before the same
    edge can qualify again. Opposite CALL/PUT stays eligible on the next signal.
    ``realized_pnl_pct`` is accepted for call-site compatibility.
    """
    del realized_pnl_pct
    return arm_breakout_side_cooldown_after_exit(
        system, symbol=symbol, option_side=option_side, reason="post_exit"
    )


def arm_breakout_side_cooldown_after_exit(
    system: Any,
    *,
    symbol: str,
    option_side: Optional[str] = None,
    reason: str = "post_exit",
) -> bool:
    """Arm 1h same-side BO cooldown after any filled exit (profit or loss).

    Opposite CALL/PUT remains eligible. Clears the symbol-level day-executed
    lock and rearms ``WAIT_BREAK`` so the other ORB#2 edge can still qualify.
    The exited side must reclaim inside ORB2 after the 1h timer (same-side
    chase guard). Uses the same store / duration as reject cooldowns
    (``BREAKOUT_SIDE_REJECT_COOLDOWN_SECONDS``).
    """
    from modules.path_exec_day_exclude import (
        POST_EXIT_COOLDOWN_REASON,
        normalize_option_side,
    )

    sym = str(symbol or "").strip().upper()
    side_l = normalize_option_side(option_side)
    if not sym or side_l not in ("call", "put"):
        return False
    reason_s = str(reason or POST_EXIT_COOLDOWN_REASON).strip() or POST_EXIT_COOLDOWN_REASON

    watch = getattr(system, "_breakout_watch_candidates", None) or {}
    cand = watch.get(sym) if isinstance(watch, dict) else None
    if cand is None and isinstance(watch, dict):
        for _k, c in watch.items():
            if str(getattr(c, "symbol", "") or "").strip().upper() == sym:
                cand = c
                break
    if cand is None:
        # Minimal stub so cooldown map + logs still arm without a live candidate.
        cand = SimpleNamespace(symbol=sym, metadata={}, failed_reasons=[])
    _mark_breakout_side_terminal_reject(system, cand, side=side_l, reason=reason_s)
    executed = set(getattr(system, "_breakout_signals_executed_today", None) or set())
    if sym in executed:
        executed.discard(sym)
        system._breakout_signals_executed_today = executed
    if getattr(cand, "state", None) is not None:
        _rearm_breakout_to_wait_break(
            cand, reason="post_exit_keep_opposite_open", system=system
        )
        meta = cand.metadata if isinstance(getattr(cand, "metadata", None), dict) else {}
        meta["reentry_required_sides"] = [side_l]
        cand.metadata = meta
    return True


def _breakout_side_reject_until(cand: Any, side: str) -> Optional[datetime]:
    meta = cand.metadata if isinstance(getattr(cand, "metadata", None), dict) else {}
    side_l = str(side or "").strip().lower()
    raw_map = meta.get("terminal_reject_until_by_side") or {}
    raw = raw_map.get(side_l) if isinstance(raw_map, dict) else None
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    try:
        text = str(raw).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _breakout_side_is_terminal_rejected(system: Any, cand: Any, side: str) -> bool:
    from modules.path_exec_day_exclude import (
        PATH_BREAKOUT,
        is_symbol_exec_rejected_today,
    )

    sym = str(getattr(cand, "symbol", "") or "").strip().upper()
    if is_symbol_exec_rejected_today(system, PATH_BREAKOUT, sym):
        return True
    side_l = str(side or "").strip().lower()
    if not side_l:
        return False
    key = _breakout_terminal_reject_side_key(sym, side_l)
    meta = cand.metadata if isinstance(getattr(cand, "metadata", None), dict) else {}
    now = datetime.now(timezone.utc)
    blocked = getattr(system, "_breakout_side_terminal_rejects", None) or {}
    until: Optional[datetime] = None
    if isinstance(blocked, dict):
        raw_until = blocked.get(key)
        if isinstance(raw_until, datetime):
            until = raw_until if raw_until.tzinfo else raw_until.replace(tzinfo=timezone.utc)
        elif raw_until:
            try:
                until = datetime.fromisoformat(str(raw_until).replace("Z", "+00:00"))
            except Exception:
                until = None
    elif key in blocked:
        until = _breakout_side_reject_until(cand, side_l) or (now + timedelta(seconds=_breakout_side_reject_cooldown_seconds(system)))
    until = until or _breakout_side_reject_until(cand, side_l)
    if until and until > now:
        return True
    if until and until <= now:
        if isinstance(blocked, dict):
            blocked.pop(key, None)
        sides = [s for s in (meta.get("terminal_reject_sides") or []) if str(s).strip().lower() != side_l]
        meta["terminal_reject_sides"] = sides
        until_map = meta.get("terminal_reject_until_by_side") or {}
        if isinstance(until_map, dict):
            until_map.pop(side_l, None)
            meta["terminal_reject_until_by_side"] = until_map
        cand.metadata = meta
    return False


def _mark_breakout_side_terminal_reject(
    system: Any, cand: Any, *, side: str, reason: str
) -> None:
    """Cooldown one ORB#2 edge (call=high / put=low), leaving the opposite side open."""
    side_l = str(side or "").strip().lower()
    if side_l in ("c", "call"):
        side_l = "call"
    elif side_l in ("p", "put"):
        side_l = "put"
    sym = str(getattr(cand, "symbol", "") or "").strip().upper()
    if not side_l or not sym:
        return
    key = _breakout_terminal_reject_side_key(sym, side_l)
    blocked = getattr(system, "_breakout_side_terminal_rejects", None)
    if not isinstance(blocked, dict):
        blocked = {}
        system._breakout_side_terminal_rejects = blocked
    cooldown_sec = _breakout_side_reject_cooldown_seconds(system)
    now = datetime.now(timezone.utc)
    until = now + timedelta(seconds=cooldown_sec)
    existing = blocked.get(key)
    existing_dt = None
    if isinstance(existing, datetime):
        existing_dt = existing if existing.tzinfo else existing.replace(tzinfo=timezone.utc)
    elif existing:
        try:
            existing_dt = datetime.fromisoformat(str(existing).replace("Z", "+00:00"))
            if existing_dt.tzinfo is None:
                existing_dt = existing_dt.replace(tzinfo=timezone.utc)
        except Exception:
            existing_dt = None
    if existing_dt is not None and existing_dt > now:
        until = existing_dt
    else:
        blocked[key] = until.isoformat()
    meta = cand.metadata if isinstance(getattr(cand, "metadata", None), dict) else {}
    sides = list(meta.get("terminal_reject_sides") or [])
    if side_l not in {str(s).strip().lower() for s in sides}:
        sides.append(side_l)
    meta["terminal_reject_sides"] = sides
    until_map = meta.get("terminal_reject_until_by_side") or {}
    if not isinstance(until_map, dict):
        until_map = {}
    until_map[side_l] = until.isoformat()
    meta["terminal_reject_until_by_side"] = until_map
    reentry_sides = list(meta.get("reentry_required_sides") or [])
    if side_l not in {str(s).strip().lower() for s in reentry_sides}:
        reentry_sides.append(side_l)
    meta["reentry_required_sides"] = reentry_sides
    meta["last_terminal_reject_reason"] = str(reason or "")[:200]
    cand.metadata = meta
    log.warning(
        "BREAKOUT_SIDE_COOLDOWN_BLOCK | symbol=%s | side=%s | reason=%s | "
        "cooldown_sec=%.0f | blocked_until=%s | action=block_side_keep_opposite_open | blocked_sides=%s",
        sym,
        side_l,
        str(reason or "")[:120],
        cooldown_sec,
        until.isoformat(),
        ",".join(sorted(str(s).lower() for s in sides)),
    )


def _breakout_both_sides_blocked(cand: Any, system: Any) -> bool:
    meta = cand.metadata if isinstance(getattr(cand, "metadata", None), dict) else {}
    sides = {str(s).strip().lower() for s in (meta.get("terminal_reject_sides") or [])}
    if "call" in sides and "put" in sides:
        return True
    sym = str(getattr(cand, "symbol", "") or "").strip().upper()
    blocked = getattr(system, "_breakout_side_terminal_rejects", None) or set()
    return (
        _breakout_terminal_reject_side_key(sym, "call") in blocked
        and _breakout_terminal_reject_side_key(sym, "put") in blocked
    )


def _side_day_block_and_rearm_breakout(
    system: Any, cand: Any, *, side: str, reason: str
) -> None:
    """Fail one BO side: cooldown that edge, re-arm WAIT_BREAK for opposite/future breaks."""
    _mark_breakout_side_terminal_reject(system, cand, side=side, reason=reason)
    _rearm_breakout_to_wait_break(cand, reason=reason, system=system)


def _day_exclude_breakout_symbol(system: Any, cand: Any, *, reason: str) -> None:
    """Remove symbol from Breakout discovery (both sides dead or explicit full exclude)."""
    from easyBreakout.breakout_models import BreakoutCandidateState
    from modules.path_exec_day_exclude import (
        PATH_BREAKOUT,
        mark_symbol_exec_rejected_today,
    )

    sym = str(getattr(cand, "symbol", "") or "").strip().upper()
    mark_symbol_exec_rejected_today(
        system, PATH_BREAKOUT, sym, reason=reason
    )
    cand.state = BreakoutCandidateState.INVALIDATED
    cand.option_side = None
    cand.failed_reasons = list(cand.failed_reasons or []) + [
        f"exec_reject_day:{reason}"
    ]
    meta = cand.metadata if isinstance(getattr(cand, "metadata", None), dict) else {}
    meta["exec_reject_day"] = True
    meta["last_terminal_reject_reason"] = str(reason or "")[:200]
    cand.metadata = meta
    watch = getattr(system, "_breakout_watch_candidates", None)
    if isinstance(watch, dict) and sym in watch:
        # Keep record for telemetry but state is INVALIDATED so scan skips it.
        watch[sym] = cand



def _breakout_execution_proximity_key(cand: Any, cfg: Optional[Any] = None) -> tuple:
    """Rank key fragment: closest to execute wins (lower sorts first).

    Order (best → worst):
      past ORB #2 edge (confirmation / extension) → at edge → close to edge → middle of box.

    Returns ``(band, secondary, tertiary)`` where band 0 = past material break edge.
    """
    from easyBreakout.breakout_confirmation import _break_levels
    from easyBreakout.breakout_models import BreakoutCandidateState, BreakoutConfig

    if cfg is None:
        cfg = BreakoutConfig()

    px = float(getattr(cand, "last_price", None) or getattr(cand, "price_at_seed", 0.0) or 0.0)
    orb_high = float(getattr(cand, "orb_high", 0.0) or 0.0)
    orb_low = float(getattr(cand, "orb_low", 0.0) or 0.0)
    if px <= 0 or orb_high <= 0 or orb_low <= 0 or orb_high <= orb_low:
        return (9, 999.0, 999.0)

    call_trig, put_trig = _break_levels(cand, cfg)
    mid = (orb_high + orb_low) / 2.0
    rng = orb_high - orb_low
    st = getattr(cand, "state", None)
    side = str(getattr(cand, "option_side", "") or "").lower()

    def _call_extension() -> float:
        if px >= call_trig:
            return max(0.0, (px - call_trig) / orb_high)
        return 0.0

    def _put_extension() -> float:
        if px <= put_trig:
            return max(0.0, (put_trig - px) / orb_low)
        return 0.0

    # Past edge: confirmation / ready, or price beyond material trigger.
    if st in (BreakoutCandidateState.WAITING_FOR_CONFIRMATION, BreakoutCandidateState.READY_TO_EXECUTE):
        if side == "call":
            ext = _call_extension()
            if ext > 0:
                return (0, -ext, 0.0)
        elif side == "put":
            ext = _put_extension()
            if ext > 0:
                return (0, -ext, 0.0)
        # Side unknown but triggers breached — still prefer over inside-box names.
        if px >= call_trig:
            return (0, -_call_extension(), 0.0)
        if px <= put_trig:
            return (0, -_put_extension(), 0.0)

    if px >= call_trig:
        return (0, -_call_extension(), 0.0)
    if px <= put_trig:
        return (0, -_put_extension(), 0.0)

    # Inside ORB #2: middle worst, then close to edge, then at edge (lower secondary/tertiary).
    dist_high = (orb_high - px) / orb_high
    dist_low = (px - orb_low) / orb_low
    min_edge = min(dist_high, dist_low)
    center_frac = abs(px - mid) / (rng / 2.0) if rng > 0 else 0.0  # 0=mid, 1=at edge
    return (1, 1.0 - center_frac, min_edge)


def _breakout_rank_key(cand: Any) -> tuple:
    """Diagnostic opportunity rank vs frozen ORB H/L — NOT used for discovery scan.

    Discovery quotes WAIT_BREAK via CSV tier→row fair rotate only
    (``select_tier_rotated_batch``). Proximity ranking historically clogged the
    scan with near-execute names; keep this helper for unit tests / diagnostics.
    Break/entry uses frozen ``orb_high``/``orb_low`` inside ``process_breakout_tick``.
    """
    px = float(getattr(cand, "last_price", None) or getattr(cand, "price_at_seed", 0.0) or 0.0)
    orb_high = float(getattr(cand, "orb_high", 0.0) or 0.0)
    orb_low = float(getattr(cand, "orb_low", 0.0) or 0.0)
    exec_key = _breakout_execution_proximity_key(cand)
    try:
        quality = float(getattr(cand, "quality_score", 0.0) or 0.0)
    except Exception:
        quality = 0.0
    try:
        volume = float(getattr(cand, "volume_ratio", 1.0) or 1.0)
    except Exception:
        volume = 1.0
    # Soft momentum / extension signals already stamped on the candidate.
    try:
        vwap_dist = abs(float(getattr(cand, "vwap_distance_pct", 0.0) or 0.0))
        if vwap_dist > 1.0:
            vwap_dist = vwap_dist / 100.0
    except Exception:
        vwap_dist = 0.0
    # Prefer names not already rejected by Convex-style pc_oi for this side.
    pc_blocked = 1 if bool((getattr(cand, "metadata", None) or {}).get("pc_oi_blocked")) else 0
    last_ts = getattr(cand, "last_quote_ts", None)
    try:
        last_seen = float(last_ts.timestamp()) if last_ts is not None else 0.0
    except Exception:
        last_seen = 0.0
    return (
        _breakout_state_priority(cand),
        int(pc_blocked),
        exec_key[0],
        exec_key[1],
        exec_key[2],
        -volume,
        -quality,
        vwap_dist,  # less extreme extension first (Convex-style)
        last_seen,
        str(getattr(cand, "symbol", "") or ""),
    )


def _tech_from_quote(quote: Dict[str, Any], cand: Any) -> Dict[str, Any]:
    """Legacy quote-only stamp; prefer enrich_breakout_tech when system is available."""
    from easyBreakout.breakout_session_tech import _tech_from_quote_and_cand

    return _tech_from_quote_and_cand(quote if isinstance(quote, dict) else {}, cand)


def select_breakout_watch_batch(
    system: Any,
    *,
    discovery_cap_override: Optional[int] = None,
    advance_rotation: bool = True,
) -> List[Any]:
    """Fair-rotate the shared 24-name list window. Status does not pin slots.

    CONFIRM/READY are evaluated when they appear in this scan's rotate window,
    not as extras that hold a name past one scan.
    """
    cfg = _cfg(system)
    from easyBreakout.breakout_models import BreakoutCandidateState
    from modules.discovery_watch_batch import (
        select_tier_rotated_batch,
        take_forced_discovery_symbols,
    )

    watch: Dict[str, Any] = getattr(system, "_breakout_watch_candidates", None) or {}
    executed: Set[str] = set(getattr(system, "_breakout_signals_executed_today", None) or set())
    from modules.path_exec_day_exclude import PATH_BREAKOUT, path_exec_rejects_today

    day_excluded = path_exec_rejects_today(system, PATH_BREAKOUT)
    active = [
        c
        for c in watch.values()
        if c.state
        in (
            BreakoutCandidateState.WAITING_FOR_BREAK,
            BreakoutCandidateState.WAITING_FOR_CONFIRMATION,
            BreakoutCandidateState.READY_TO_EXECUTE,
        )
        and str(getattr(c, "symbol", "") or "").strip().upper() not in day_excluded
    ]
    if not active:
        return []

    per_tick = int(
        discovery_cap_override
        if discovery_cap_override is not None
        else cfg.watch_symbols_per_tick or 24
    )
    forced = take_forced_discovery_symbols(system)
    offset = int(getattr(system, "_breakout_watch_rotate_idx", 0) or 0)
    batch, new_offset, meta = select_tier_rotated_batch(
        active,
        per_tick=per_tick,
        offset=offset,
        forced_symbols=forced,
    )
    if advance_rotation and meta.get("mode") in ("tier_rotate", "path_fallback_rotate"):
        system._breakout_watch_rotate_idx = int(new_offset)
    if advance_rotation and len(active) > len(batch):
        log.info(
            "BREAKOUT_PIPELINE | stage=watch_batch | total=%d | this_tick=%d | "
            "mode=%s | offset=%d | next_offset=%d | ready=%s | cap=%d | rotation=tier_list",
            len(active),
            len(batch),
            meta.get("mode"),
            int(meta.get("offset", offset) or offset),
            int(meta.get("new_offset", new_offset) or new_offset),
            meta.get("ready", 0),
            per_tick,
        )
    return list(batch)


async def run_breakout_watch_cycle(system: Any) -> None:
    """One Breakout watch tick: quote → confirm → quality → pc_oi → execute + PO/snapshots."""
    if not getattr(system, "_enable_breakout_strategy", False):
        return
    if not getattr(system, "_breakout_watch_active", False):
        return
    if not getattr(system, "breakout_options_executor", None):
        return
    if not getattr(system, "_orb_0dte_batch_complete_for_trendline", False):
        log.debug(
            "BREAKOUT_PIPELINE | stage=watch | action=blocked | reason=await_orb_0dte_batch"
        )
        return

    from datetime import time as time_class

    from easyBreakout.breakout_confirm_shell import process_breakout_tick
    from easyBreakout.breakout_models import BreakoutCandidateState
    from easyBreakout.breakout_quality import compute_breakout_quality_score
    from easyBreakout.breakout_signal_adapter import (
        build_breakout_trade_signal,
        to_trendline_compatible_signal,
    )
    from modules.unified_0dte_account import (
        breakout_0dte_max_positions,
        classify_0dte_path,
        count_unified_open_positions,
        get_options_executor,
        total_0dte_max_positions,
    )

    cfg = _cfg(system)
    osm = getattr(system, "orb_strategy_manager", None)
    if osm is None:
        return
    try:
        pt_now = osm._get_current_time_pt()
    except Exception:
        return
    if pt_now < time_class(7, 30):
        return

    cutoff_hhmm = str(cfg.no_new_entries_after_pt or "12:30")
    try:
        _h, _m = cutoff_hhmm.split(":")
        no_trade_after = time_class(int(_h), int(_m))
    except Exception:
        no_trade_after = time_class(11, 30)
    if pt_now > no_trade_after:
        system._breakout_watch_active = False
        log.warning(
            "BREAKOUT_PIPELINE | stage=expired | reason=no_trade_zone_after_cutoff | cutoff_pt=%s",
            cutoff_hhmm,
        )
        return

    discovery_cap_override: Optional[int] = None
    try:
        from modules.discovery_watch_batch import discovery_batch_size

        discovery_cap_override = discovery_batch_size()
    except Exception:
        pass

    batch = select_breakout_watch_batch(system, discovery_cap_override=discovery_cap_override)
    if not batch:
        return

    symbols = [c.symbol for c in batch]
    quotes: Dict[str, Any] = {}
    try:
        from modules.open_position_quote_orchestration import (
            take_discovery_quotes_from_merged_cache,
        )

        cached = take_discovery_quotes_from_merged_cache(system, symbols)
        if cached is not None:
            quotes = cached
            log.info(
                "BREAKOUT_PIPELINE | stage=watch | action=use_merged_discovery_quotes | symbols=%d",
                len(symbols),
            )
    except Exception:
        cached = None
    if not quotes:
        try:
            dm = getattr(system, "data_manager", None)
            if dm is not None and hasattr(dm, "get_batch_quotes"):
                quotes = await dm.get_batch_quotes(symbols, skip_cache=True) or {}
            else:
                tm = getattr(system, "trade_manager", None)
                et = getattr(tm, "etrade_trading", None) if tm else None
                if et is not None and hasattr(et, "get_quotes"):
                    raw = et.get_quotes(symbols) or []
                    for q in raw:
                        sym = str(getattr(q, "symbol", "") or "").upper()
                        if not sym:
                            continue
                        quotes[sym] = {
                            "last": getattr(q, "last_price", None) or getattr(q, "last", None),
                            "price": getattr(q, "last_price", None),
                            "volume": getattr(q, "volume", None),
                        }
            try:
                from modules.broker_api_pacing import (
                    LANE_BREAKOUT_WATCH_EQUITY,
                    record_broker_lane_request,
                )

                record_broker_lane_request(LANE_BREAKOUT_WATCH_EQUITY)
            except Exception:
                pass
        except Exception as quote_err:
            log.warning("BREAKOUT_PIPELINE | stage=watch | quote_error=%s", quote_err)
            return
    if not quotes:
        return

    ingest = getattr(system, "_ingest_broker_quotes_into_session_tech", None)
    if callable(ingest):
        try:
            ingest(quotes)
        except Exception as ingest_err:
            log.debug(
                "BREAKOUT_PIPELINE | stage=watch | session_tech_ingest_failed | err=%s",
                ingest_err,
            )

    try:
        max_total = int(total_0dte_max_positions())
        max_breakout = int(breakout_0dte_max_positions())
    except Exception:
        max_total = 10
        max_breakout = 10
    # Must live in this scope — fill path updates it before Telegram dispatch.
    # (Jul 31: NameError `executed` aborted after fill → no execution alert.)
    executed: Set[str] = set(getattr(system, "_breakout_signals_executed_today", None) or set())
    open_count = 0
    breakout_open_count = 0
    try:
        open_count = int(count_unified_open_positions(system) or 0)
        ex = get_options_executor(system)
        positions = list(ex.get_open_positions() or []) if ex else []
        breakout_open_count = sum(1 for p in positions if classify_0dte_path(p) == "breakout_0dte")
    except Exception:
        open_count = 0
        breakout_open_count = 0

    # Hydrate session 1m bars for this tick's batch so VWAP/vol are real at decide.
    enrich_breakout_tech = None
    try:
        from easyBreakout.breakout_session_tech import (
            enrich_breakout_tech as _enrich_breakout_tech,
            hydrate_breakout_session_bars,
        )

        await hydrate_breakout_session_bars(
            system,
            [str(getattr(c, "symbol", "") or "") for c in batch],
            quotes=quotes if isinstance(quotes, dict) else None,
        )
        enrich_breakout_tech = _enrich_breakout_tech
    except Exception as hydrate_err:
        log.warning(
            "BREAKOUT_SESSION_TECH | stage=hydrate | err=%s",
            hydrate_err,
        )
    now = datetime.now(timezone.utc)
    for cand in batch:
        quote = quotes.get(cand.symbol, {}) if isinstance(quotes, dict) else {}
        if not isinstance(quote, dict):
            quote = {}
        price = _quote_price(quote)
        if price <= 0:
            continue

        # Stamp equity tech every scan so WAIT_BREAK rotation can prefer
        # volume / VWAP / quality the way Trendline priority_score does.
        if enrich_breakout_tech is not None:
            tech_stamp = enrich_breakout_tech(
                system,
                cand,
                quote,
                price=price,
                continuation_pct=None,
                break_ts=getattr(cand, "break_ts", None),
            )
        else:
            tech_stamp = _tech_from_quote(quote, cand)
        try:
            if tech_stamp.get("volume_ratio") is not None:
                cand.volume_ratio = float(tech_stamp["volume_ratio"])
        except (TypeError, ValueError):
            pass
        if tech_stamp.get("vwap") is not None:
            try:
                cand.vwap = float(tech_stamp["vwap"])
            except (TypeError, ValueError):
                pass
        if tech_stamp.get("vwap_distance_pct") is not None:
            try:
                cand.vwap_distance_pct = float(tech_stamp["vwap_distance_pct"])
            except (TypeError, ValueError):
                pass
        if tech_stamp.get("rsi") is not None:
            try:
                cand.rsi = float(tech_stamp["rsi"])
            except (TypeError, ValueError):
                pass
        meta_cand = cand.metadata if isinstance(cand.metadata, dict) else {}
        try:
            from easyBreakout.breakout_session_tech import stamp_session_tech_qg_keys

            meta_cand = stamp_session_tech_qg_keys(meta_cand, tech_stamp)
        except Exception:
            for k in (
                "tech_source",
                "volume_ratio_source",
                "vwap_source",
                "vwap_position",
                "minutes_from_730",
                "minutes_from_730_to_break",
                "velocity_pct",
                "session_bar_count",
                "bollinger_position",
                "atr",
                "rsi_missing",
            ):
                if tech_stamp.get(k) is not None:
                    meta_cand[k] = tech_stamp.get(k)
        cand.metadata = meta_cand
        cand.last_price = price
        cand.last_quote_ts = now

        confirm = process_breakout_tick(cand, price, now, cfg)
        if confirm.immediate_reclaim:
            log.info(
                "BREAKOUT_PIPELINE | stage=reclaim | symbol=%s | action=rearm | reason=spike_reclaim_into_orb",
                cand.symbol,
            )
            continue

        # Soft entry window from first material break.
        # CONFIRM past the window rearms WAIT_BREAK (no cooldown).
        # READY this tick does not expire — fall through to QG. QG reject
        # applies BREAKOUT_SIDE_REJECT_COOLDOWN_SECONDS (1h).
        from easyBreakout.breakout_confirmation import should_soft_expire_entry_window

        try:
            window_sec = float(getattr(cfg, "simple_entry_window_sec", 300.0) or 0.0)
        except (TypeError, ValueError):
            window_sec = 300.0
        if window_sec > 0 and cand.state in (
            BreakoutCandidateState.WAITING_FOR_CONFIRMATION,
            BreakoutCandidateState.READY_TO_EXECUTE,
        ):
            meta_w = cand.metadata if isinstance(cand.metadata, dict) else {}
            anchor = meta_w.get("entry_window_at") or getattr(cand, "break_ts", None)
            age_sec: Optional[float] = None
            if anchor is not None:
                try:
                    if isinstance(anchor, datetime):
                        a = anchor if anchor.tzinfo else anchor.replace(tzinfo=timezone.utc)
                        age_sec = float((now - a).total_seconds())
                    else:
                        a = datetime.fromisoformat(str(anchor).replace("Z", "+00:00"))
                        if a.tzinfo is None:
                            a = a.replace(tzinfo=timezone.utc)
                        age_sec = float((now - a).total_seconds())
                except Exception:
                    age_sec = None
            ready_now = bool(getattr(confirm, "ready", False))
            if should_soft_expire_entry_window(
                age_sec=age_sec, window_sec=window_sec, confirm_ready=ready_now
            ):
                log.info(
                    "BREAKOUT_PIPELINE | stage=expired | symbol=%s | side=%s | "
                    "reason=entry_window_expired | age_sec=%.1f | window_sec=%.1f | action=rearm_wait_break",
                    cand.symbol,
                    getattr(cand, "option_side", None) or confirm.option_side,
                    age_sec,
                    window_sec,
                )
                _rearm_breakout_to_wait_break(
                    cand, reason="entry_window_expired", system=system
                )
                continue
            if (
                age_sec is not None
                and age_sec > window_sec
                and ready_now
            ):
                log.info(
                    "BREAKOUT_PIPELINE | stage=window_ready_continue | symbol=%s | side=%s | "
                    "age_sec=%.1f | window_sec=%.1f | action=quality_decide",
                    cand.symbol,
                    getattr(cand, "option_side", None) or confirm.option_side,
                    age_sec,
                    window_sec,
                )

        if not confirm.ready:
            continue

            # Structural post-executor reject cooldown on this side — do not re-enter
            # CONFIRM→execute until the cooldown expires.
        confirm_side = str(confirm.option_side or cand.option_side or "").lower()
        if confirm_side and _breakout_side_is_terminal_rejected(system, cand, confirm_side):
            from modules.path_exec_day_exclude import (
                PATH_BREAKOUT,
                is_symbol_exec_rejected_today,
            )

            if is_symbol_exec_rejected_today(system, PATH_BREAKOUT, cand.symbol):
                log.warning(
                    "BREAKOUT_PIPELINE | stage=symbol_day_excluded | symbol=%s | side=%s | "
                    "action=skip_discovery",
                    cand.symbol,
                    confirm_side,
                )
                continue
            log.warning(
                "BREAKOUT_PIPELINE | stage=side_terminal_blocked | symbol=%s | side=%s | "
                    "action=rearm_wait_break_until_cooldown_expires",
                cand.symbol,
                confirm_side,
            )
            _rearm_breakout_to_wait_break(
                cand,
                reason=f"side_terminal_blocked:{confirm_side}",
                system=system,
            )
            continue

        # Re-enrich at decide with continuation / break_ts for velocity + minutes.
        if enrich_breakout_tech is not None:
            tech = enrich_breakout_tech(
                system,
                cand,
                quote,
                price=price,
                continuation_pct=confirm.continuation_pct,
                break_ts=getattr(cand, "break_ts", None) or now,
            )
        else:
            tech = tech_stamp
        quality = compute_breakout_quality_score(
            cand,
            confirm,
            cfg,
            price=price,
            volume_ratio=tech.get("volume_ratio"),
            vwap=tech.get("vwap"),
            vwap_distance_pct=tech.get("vwap_distance_pct"),
            rsi=tech.get("rsi"),
            macd_histogram=tech.get("macd_histogram"),
            ema_12=tech.get("ema_12"),
            ema_26=tech.get("ema_26"),
            volume_ratio_missing=bool(tech.get("volume_ratio_missing")),
            vwap_missing=bool(tech.get("vwap_missing")),
            rsi_missing=bool(tech.get("rsi_missing")),
            macd_missing=bool(tech.get("macd_missing")),
        )
        # Quality Gate Stack Layer A/B — cutover when QUALITY_GATE_STACK_CUTOVER
        try:
            from easy0DTE.modules.quality_gate_stack import (
                PATH_BO,
                GateContext,
                apply_equity_geom_cutover,
                shadow_equity_geom_report,
            )

            side_u = str(confirm.option_side or cand.option_side or "call").upper()
            if side_u in ("C", "CALL"):
                side_u = "CALL"
            elif side_u in ("P", "PUT"):
                side_u = "PUT"
            reasons = list(quality.failed_reasons or [])
            geom_ok = not any(
                str(r).startswith("confirm") or "box" in str(r).lower() for r in reasons
            )
            # Layer A geom only — do not poison geom_ready with Layer B equity scorer fails
            # (bare put_above_vwap / volume_ratio_*). GATE_BO owns equity admit.
            geom_ready = bool(geom_ok and bool(confirm.ready))
            meta_bo = cand.metadata if isinstance(cand.metadata, dict) else {}

            def _tech_or_meta(key: str) -> Any:
                if tech.get(key) is not None:
                    return tech.get(key)
                return meta_bo.get(key)

            mins_730 = _tech_or_meta("minutes_from_730")
            mins_brk = _tech_or_meta("minutes_from_730_to_break")
            if mins_730 is None and mins_brk is None:
                try:
                    from easyBreakout.breakout_session_tech import minutes_from_730_pt

                    mins_730 = minutes_from_730_pt(
                        getattr(cand, "break_ts", None) or now
                    )
                    mins_brk = mins_730
                except Exception:
                    mins_730 = mins_brk = None

            ctx_ab = GateContext(
                path=PATH_BO,
                symbol=str(cand.symbol or "").upper(),
                side=side_u,
                signal=dict(tech or {}),
                equity_volume_ratio=(
                    float(quality.volume_ratio)
                    if quality.volume_ratio is not None
                    else None
                ),
                vwap_distance_pct=(
                    float(quality.vwap_distance_pct)
                    if quality.vwap_distance_pct is not None
                    else None
                ),
                vwap_position=str(quality.vwap_position or "") or None,
                rsi=float(quality.rsi) if quality.rsi is not None else None,
                macd_histogram=(
                    float(quality.macd_histogram)
                    if quality.macd_histogram is not None
                    else None
                ),
                ema_12=float(quality.ema_12) if quality.ema_12 is not None else None,
                ema_26=float(quality.ema_26) if quality.ema_26 is not None else None,
                quality_score=float(quality.score),
                quality_failed_reasons=[],
                geom_ready=geom_ready,
                geom_reason=(
                    "bo_confirm_ok"
                    if geom_ready
                    else (reasons[0] if reasons else "bo_quality_reject")
                ),
                break_distance_pct=(
                    float(confirm.break_distance_pct)
                    if confirm.break_distance_pct is not None
                    else None
                ),
                extras={
                    "volume_ratio_missing": bool(tech.get("volume_ratio_missing")),
                    "vwap_missing_fail_closed": bool(tech.get("vwap_missing")),
                    "rsi_missing": bool(tech.get("rsi_missing")),
                    "macd_missing": bool(tech.get("macd_missing")),
                    "bollinger_position": _tech_or_meta("bollinger_position"),
                    "atr": _tech_or_meta("atr"),
                    "iv": tech.get("implied_volatility") or tech.get("iv"),
                    "implied_volatility": tech.get("implied_volatility")
                    or tech.get("iv")
                    or tech.get("representative_iv"),
                    "session_vs_avg_volume": _tech_or_meta("session_vs_avg_volume")
                    or _tech_or_meta("session_vs_avg"),
                    "seconds_since_break": (
                        (
                            max(
                                0.0,
                                (
                                    now
                                    - (
                                        getattr(cand, "break_ts", None)
                                        or getattr(confirm, "break_ts", None)
                                        or now
                                    )
                                ).total_seconds(),
                            )
                            if getattr(cand, "break_ts", None) is not None
                            or getattr(confirm, "break_ts", None) is not None
                            else _tech_or_meta("seconds_since_break")
                        )
                    ),
                    "break_timestamp": (
                        getattr(cand, "break_ts", None)
                        or getattr(confirm, "break_ts", None)
                    ),
                    "l1_delta_5s_with": _tech_or_meta("l1_delta_5s_with"),
                    "minutes_from_730": mins_730,
                    "minutes_from_730_to_break": mins_brk,
                    "break_distance_pct": (
                        float(confirm.break_distance_pct)
                        if confirm.break_distance_pct is not None
                        else None
                    ),
                },
            )
            meta_ab = meta_bo
            ab_report = shadow_equity_geom_report(
                PATH_BO, ctx_ab, legacy_admitted=bool(quality.passed), stamp_target=meta_ab
            )
            cand.metadata = meta_ab
            try:
                from modules.qg_admit_snapshot import collect_qg_admit_snapshot

                snap = collect_qg_admit_snapshot(dict(meta_ab))
                if snap:
                    meta_ab["qg_admit"] = snap
                    cand.metadata = meta_ab
            except Exception:
                pass
            # Readiness log — soft-skip knives should be rare after hub hydrate.
            _soft = []
            for _k in (
                "bollinger_position",
                "atr",
                "minutes_from_730",
                "minutes_from_730_to_break",
                "break_distance_pct",
            ):
                if ctx_ab.extras.get(_k) is None and (
                    _k != "break_distance_pct" or ctx_ab.break_distance_pct is None
                ):
                    _soft.append(_k)
            if _soft:
                log.info(
                    "BREAKOUT_QG_FIELD_GAP | symbol=%s | side=%s | missing=%s | "
                    "tech_source=%s | grep=BREAKOUT_QG_FIELD_GAP",
                    cand.symbol,
                    side_u,
                    ",".join(_soft),
                    tech.get("tech_source"),
                )
            cut_pass, cut_reasons = apply_equity_geom_cutover(
                ab_report,
                legacy_passed=bool(quality.passed),
                failed_reasons=list(quality.failed_reasons or []),
            )
            if cut_pass != bool(quality.passed):
                quality.passed = cut_pass
                quality.failed_reasons = cut_reasons
        except Exception as _qgs_exc:
            log.debug("GATE_STACK BO quality shadow skipped: %s", _qgs_exc)
        if not quality.passed:
            fail_side = str(confirm.option_side or cand.option_side or "").lower()
            reasons_list = list(quality.failed_reasons or [])
            reasons = ",".join(reasons_list[:6])
            # Data gaps (missing VWAP/vol) = soft-skip; never day-block. Retry next tick.
            # Also clear any confirm-time exec_ready stamp so TTL cannot burn during defer
            # (Jul 30 cascade: quality_defer for hours must not burn slot-wait TTL).
            try:
                from modules.session_tech_indicators import is_session_tech_data_gap

                if is_session_tech_data_gap(reasons_list):
                    meta_gap = cand.metadata if isinstance(cand.metadata, dict) else {}
                    meta_gap.pop("exec_ready_at", None)
                    cand.metadata = meta_gap
                    log.warning(
                        "BREAKOUT_PIPELINE | stage=quality_defer | symbol=%s | side=%s | "
                        "score=%.1f | reasons=%s | action=skip_await_session_tech",
                        cand.symbol,
                        fail_side or "n/a",
                        quality.score,
                        reasons,
                    )
                    continue
            except Exception:
                pass
            log.info(
                "BREAKOUT_PIPELINE | stage=quality_reject | symbol=%s | side=%s | score=%.1f | "
                "reasons=%s | action=side_cooldown_rearm_wait_break",
                cand.symbol,
                fail_side or "n/a",
                quality.score,
                reasons,
            )
            q_reason = f"quality_reject:{reasons[:160]}"
            # Persist path-SS reject-time features (entry-comparable densified schema).
            try:
                _rs = ",".join(str(r) for r in reasons_list)
                if "path_ss" in _rs or "gate_stack:path_ss:" in str(q_reason):
                    from modules.path_ss_reject_snapshot_store import (
                        capture_path_ss_reject_snapshot,
                    )

                    meta_rj = cand.metadata if isinstance(cand.metadata, dict) else {}
                    qg_admit = (
                        meta_rj.get("qg_admit")
                        if isinstance(meta_rj.get("qg_admit"), dict)
                        else None
                    )
                    tech_row = dict(tech) if isinstance(tech, dict) else {}
                    for _k, _v in meta_rj.items():
                        if _k == "qg_admit":
                            continue
                        if _v is not None and tech_row.get(_k) is None:
                            tech_row[_k] = _v
                    side_lbl = (
                        "CALL"
                        if "call" in str(fail_side or "").lower()
                        else "PUT"
                        if "put" in str(fail_side or "").lower()
                        else str(fail_side or "").upper()
                    )
                    capture_path_ss_reject_snapshot(
                        system,
                        source="BREAKOUT_PIPELINE_quality_reject",
                        symbol=str(cand.symbol or ""),
                        side=side_lbl,
                        reason_code=str(q_reason),
                        qg_admit=qg_admit,
                        tech_row=tech_row,
                        gate_report=(
                            meta_rj.get("quality_gate_report")
                            if isinstance(meta_rj.get("quality_gate_report"), dict)
                            else ab_report
                        ),
                        extra={"reasons": reasons_list[:12], "score": quality.score},
                    )
            except Exception:
                pass
            _side_day_block_and_rearm_breakout(
                system,
                cand,
                side=fail_side,
                reason=q_reason,
            )
            # Desk Telegram — same compact Reason as TL quality rejects (QG §3.2).
            try:
                am = getattr(system, "alert_manager", None)
                if am is not None and hasattr(am, "send_breakout_options_execution_alert"):
                    try:
                        from modules.prime_alert_manager import PrimeAlertManager

                        reason_disp = PrimeAlertManager._execution_reject_display_code(
                            q_reason
                        )
                    except Exception:
                        reason_disp = reasons[:120] or "quality_reject"
                    mode_label = "DEMO"
                    try:
                        mode_label = (
                            "LIVE"
                            if str(getattr(system, "mode", "") or "").lower() == "live"
                            else "DEMO"
                        )
                    except Exception:
                        pass
                    await am.send_breakout_options_execution_alert(
                        executed_rows=[],
                        failed_rows=[
                            {
                                "symbol": str(cand.symbol or ""),
                                "option_side": str(fail_side or "").upper(),
                                "setup_type": "breakout",
                                "trade_id": "n/a",
                                "diagnostic_stage": "quality_reject",
                                "failure_stage": "quality_reject",
                                "failure_reason": q_reason,
                                "failure_reason_code": str(reason_disp)[:160],
                                "execution_attempted": True,
                                "status": "failed",
                            }
                        ],
                        mode=mode_label,
                    )
            except Exception as q_alert_err:
                log.debug(
                    "BREAKOUT_PIPELINE | stage=quality_reject_alert | symbol=%s | error=%s",
                    cand.symbol,
                    q_alert_err,
                )
            continue

        if open_count >= max_total or breakout_open_count >= max_breakout:
            cap_side = str(confirm.option_side or cand.option_side or "").lower()
            log.warning(
                "BREAKOUT_PIPELINE | stage=slot_cap | classification=skip_reject | "
                "reason=max_open_positions_reached | "
                "symbol=%s | side=%s | open=%d | max=%d | breakout_open=%d | breakout_max=%d | "
                "action=retry_until_exec_ready_ttl | cooldown=none",
                cand.symbol,
                cap_side or "n/a",
                open_count,
                max_total,
                breakout_open_count,
                max_breakout,
            )
            continue

        bo_signal = build_breakout_trade_signal(
            cand,
            price=price,
            confirm_break_distance_pct=confirm.break_distance_pct,
            confirm_continuation_pct=confirm.continuation_pct,
            confirm_follow_through=confirm.follow_through_bars,
            quality_score=quality.score,
            extra_meta={
                "vwap_position": quality.vwap_position,
                "volume_ratio": quality.volume_ratio,
                "volume_ratio_missing": bool(quality.volume_ratio_missing),
                "rsi": quality.rsi,
                "rsi_missing": bool(quality.rsi_missing),
                "macd_histogram": quality.macd_histogram,
                "macd_missing": bool(quality.macd_missing),
                "ema_12": quality.ema_12,
                "ema_26": quality.ema_26,
                "vwap": tech.get("vwap"),
                "vwap_distance_pct": quality.vwap_distance_pct,
                "break_distance_pct": confirm.break_distance_pct,
                "continuation_pct": confirm.continuation_pct,
                "follow_through_bars": confirm.follow_through_bars,
                "quality_score": quality.score,
                "velocity_pct": tech.get("velocity_pct"),
                "minutes_from_730": tech.get("minutes_from_730"),
                "minutes_from_730_to_break": tech.get("minutes_from_730_to_break"),
                "tech_source": tech.get("tech_source"),
                "volume_ratio_source": tech.get("volume_ratio_source"),
                "vwap_source": tech.get("vwap_source"),
                "session_bar_count": tech.get("session_bar_count"),
                "break_ts": (
                    cand.break_ts.isoformat()
                    if getattr(cand, "break_ts", None) is not None
                    and hasattr(cand.break_ts, "isoformat")
                    else getattr(cand, "break_ts", None)
                ),
                "break_timestamp": (
                    cand.break_ts.isoformat()
                    if getattr(cand, "break_ts", None) is not None
                    and hasattr(cand.break_ts, "isoformat")
                    else getattr(cand, "break_ts", None)
                ),
                "close_at_break": (
                    getattr(cand, "material_break_close", None)
                    or getattr(cand, "break_price", None)
                ),
                **(
                    {"quality_gate_report": cand.metadata.get("quality_gate_report")}
                    if isinstance(cand.metadata, dict)
                    and isinstance(cand.metadata.get("quality_gate_report"), dict)
                    else {}
                ),
            },
        )

        from easyBreakout.breakout_pc_oi_gate import confirm_breakout_pc_oi_before_execution

        pc_ok, pc_reason, _pc_skew = await confirm_breakout_pc_oi_before_execution(
            system, bo_signal
        )
        if not pc_ok:
            if str(pc_reason or "") == "max_open_positions_reached":
                log.info(
                    "BREAKOUT_EXECUTION_BLOCKED | symbol=%s | stage=max_open_positions_reached | "
                    "action=retry_until_exec_ready_ttl | cooldown=none",
                    cand.symbol,
                )
                continue
            if str(pc_reason or "") == "dir_pct_data_unavailable":
                # Oct 13 2026: bounded data wait ran out -> retire this break, no side block.
                log.info(
                    "BREAKOUT_EXECUTION_BLOCKED | symbol=%s | stage=dir_pct_data_unavailable | "
                    "action=retire_break | cooldown=none",
                    cand.symbol,
                )
                try:
                    _rearm_breakout_to_wait_break(cand, reason="dir_pct_data_unavailable", system=system)
                except Exception:
                    pass
                continue
            if str(pc_reason or "") in ("required_data_not_ready",):
                log.info(
                    "BREAKOUT_EXECUTION_BLOCKED | symbol=%s | stage=%s | "
                    "action=hold_same_break | cooldown=none",
                    cand.symbol,
                    str(pc_reason or ""),
                )
                continue
            log.warning(
                "BREAKOUT_EXECUTION_BLOCKED | symbol=%s | stage=trade_profile | reason=%s | "
                "action=side_day_block_keep_opposite_open",
                cand.symbol,
                pc_reason,
            )
            meta = cand.metadata if isinstance(cand.metadata, dict) else {}
            meta["trade_profile_reject_reason"] = str(pc_reason or "")
            meta["last_trade_profile_reject_side"] = str(
                confirm.option_side or cand.option_side or ""
            )
            cand.metadata = meta
            fail_side = str(confirm.option_side or cand.option_side or "").lower()
            q_reason = f"trade_profile_reject:{pc_reason}"
            if str(pc_reason or "").startswith(("dir_vol_", "dir_pct")):
                q_reason = f"dir_pct_filter_reject:{pc_reason}"
            _side_day_block_and_rearm_breakout(
                system,
                cand,
                side=fail_side,
                reason=q_reason,
            )
            try:
                am = getattr(system, "alert_manager", None)
                if am is not None and hasattr(am, "send_breakout_options_execution_alert"):
                    try:
                        from modules.prime_alert_manager import PrimeAlertManager

                        reason_disp = PrimeAlertManager._execution_reject_display_code(
                            q_reason
                        )
                    except Exception:
                        reason_disp = str(pc_reason or "trade_profile")[:120]
                    mode_label = "DEMO"
                    try:
                        mode_label = (
                            "LIVE"
                            if str(getattr(system, "mode", "") or "").lower() == "live"
                            else "DEMO"
                        )
                    except Exception:
                        pass
                    await am.send_breakout_options_execution_alert(
                        executed_rows=[],
                        failed_rows=[
                            {
                                "symbol": str(cand.symbol or ""),
                                "option_side": str(fail_side or "").upper(),
                                "setup_type": "breakout",
                                "trade_id": "n/a",
                                "diagnostic_stage": "trade_profile",
                                "failure_stage": "trade_profile",
                                "failure_reason": q_reason,
                                "failure_reason_code": str(reason_disp)[:160],
                                "execution_attempted": True,
                                "status": "failed",
                            }
                        ],
                        mode=mode_label,
                    )
            except Exception as tp_alert_err:
                log.debug(
                    "BREAKOUT_PIPELINE | stage=trade_profile_alert | symbol=%s | error=%s",
                    cand.symbol,
                    tp_alert_err,
                )
            continue

        exec_side = str(
            getattr(bo_signal, "option_side", "")
            or confirm.option_side
            or cand.option_side
            or ""
        ).lower()
        if exec_side in ("c", "call"):
            exec_side = "call"
        elif exec_side in ("p", "put"):
            exec_side = "put"
        if exec_side in ("call", "put") and _breakout_has_open_same_side(
            system, cand.symbol, exec_side
        ):
            log.warning(
                "BREAKOUT_EXECUTION_BLOCKED | symbol=%s | stage=dedupe_guard | "
                "reason=open_same_side_symbol_strategy | option_side=%s | "
                "action=skip_keep_opposite_open",
                cand.symbol,
                exec_side,
            )
            _rearm_breakout_to_wait_break(
                cand, reason=f"open_same_side_dedupe:{exec_side}", system=system
            )
            continue

        tl_signal = to_trendline_compatible_signal(bo_signal)
        # Stamp pc_oi on TL-compatible metadata for snapshot/shadow
        if isinstance(getattr(tl_signal, "metadata", None), dict) and isinstance(
            getattr(bo_signal, "metadata", None), dict
        ):
            for k, v in bo_signal.metadata.items():
                if v is not None:
                    tl_signal.metadata.setdefault(k, v)

        slot_capital = float(getattr(system, "_trendline_slot_capital", 0.0) or 0.0)
        if slot_capital <= 0:
            try:
                if hasattr(system, "_refresh_trendline_slot_capital"):
                    system._refresh_trendline_slot_capital()
                    slot_capital = float(getattr(system, "_trendline_slot_capital", 0.0) or 0.0)
            except Exception:
                pass
        if slot_capital <= 0:
            try:
                from modules.unified_0dte_account import unified_demo_starting_balance

                slot_capital = float(unified_demo_starting_balance()) / max(max_total, 1)
            except Exception:
                slot_capital = 500.0

        log.warning(
            "BREAKOUT_EXECUTION_HANDOFF | symbol=%s | side=%s | price=%.4f | orb_h=%.4f | orb_l=%.4f | "
            "break_dist=%.4f | cont=%.4f | ft=%d | score=%.1f",
            cand.symbol,
            bo_signal.option_side,
            price,
            cand.orb_high,
            cand.orb_low,
            confirm.break_distance_pct,
            confirm.continuation_pct,
            confirm.follow_through_bars,
            quality.score,
        )
        if hasattr(system, "_save_breakout_execution_priority_snapshot"):
            try:
                system._save_breakout_execution_priority_snapshot(
                    signal=tl_signal,
                    stage="breakout_emit_handoff",
                    order_payload={},
                    broker_success=None,
                    candidate=cand,
                )
            except Exception as snap_err:
                log.debug("BREAKOUT handoff snapshot skipped: %s", snap_err)

        # Enter Trade — exec-ready TTL for capital/slot wait.
        try:
            from modules.path_exec_ready_ttl import (
                EXEC_READY_TTL_REASON,
                exec_ready_age_sec,
                exec_signal_expire_minutes,
                is_exec_ready_expired,
                log_exec_ready_ttl_expired,
                stamp_exec_ready_at,
            )

            meta_ttl = cand.metadata if isinstance(cand.metadata, dict) else {}
            stamp_exec_ready_at(meta_ttl, now=now)
            cand.metadata = meta_ttl
            expire_min = exec_signal_expire_minutes("breakout")
            if is_exec_ready_expired(
                meta_ttl, path="breakout", now=now, expire_minutes=expire_min
            ):
                ttl_side = str(
                    getattr(tl_signal, "option_side", None)
                    or getattr(cand, "option_side", None)
                    or ""
                ).lower()
                log_exec_ready_ttl_expired(
                    path="breakout",
                    symbol=str(cand.symbol or ""),
                    age_sec=exec_ready_age_sec(meta_ttl, now=now),
                    expire_minutes=expire_min,
                    extra=f"side={ttl_side or 'n/a'}",
                )
                from modules.path_exec_ready_ttl import (
                    EXEC_READY_TTL_RETIRE_REASON,
                    ttl_retire_no_cooldown_enabled,
                )

                if ttl_retire_no_cooldown_enabled():
                    # Oct 13 2026: retire this break, wait for the next one, no side block.
                    _rearm_breakout_to_wait_break(cand, reason=EXEC_READY_TTL_RETIRE_REASON, system=system)
                    continue
                _side_day_block_and_rearm_breakout(
                    system,
                    cand,
                    side=ttl_side,
                    reason=EXEC_READY_TTL_REASON,
                )
                continue
        except Exception as ttl_exc:
            log.debug("BREAKOUT exec_ready TTL check skipped: %s", ttl_exc)

        if open_count >= max_total or breakout_open_count >= max_breakout:
            cap_side = str(
                getattr(tl_signal, "option_side", None) or getattr(cand, "option_side", None) or ""
            ).lower()
            log.warning(
                "BREAKOUT_PIPELINE | stage=slot_cap | classification=skip_reject | "
                "reason=max_open_positions_reached | "
                "symbol=%s | side=%s | open=%d | max=%d | breakout_open=%d | breakout_max=%d | "
                "action=retry_until_exec_ready_ttl | cooldown=none",
                cand.symbol,
                cap_side or "n/a",
                open_count,
                max_total,
                breakout_open_count,
                max_breakout,
            )
            continue

        slot_capital = float(getattr(system, "_trendline_slot_capital", 0.0) or 0.0)
        if slot_capital <= 0:
            try:
                if hasattr(system, "_refresh_trendline_slot_capital"):
                    system._refresh_trendline_slot_capital()
                    slot_capital = float(getattr(system, "_trendline_slot_capital", 0.0) or 0.0)
            except Exception:
                pass
        if slot_capital <= 0:
            try:
                from modules.unified_0dte_account import unified_demo_starting_balance

                slot_capital = float(unified_demo_starting_balance()) / max(max_total, 1)
            except Exception:
                slot_capital = 500.0

        result = await system.breakout_options_executor.execute(
            tl_signal,
            slot_index=open_count,
            slot_capital=slot_capital,
            entry_budget=slot_capital,
            available_cash=slot_capital,
        )
        _p = getattr(result, "order_payload", {}) or {}
        try:
            from modules.runtime_mode import mode_label as runtime_mode_label

            mode_label = runtime_mode_label()
        except Exception:
            mode_label = "DEMO"

        def _breakout_exec_alert_row(*, filled: bool, err: str = "") -> Dict[str, Any]:
            meta = getattr(tl_signal, "metadata", None) or {}
            if not isinstance(meta, dict):
                meta = {}
            row: Dict[str, Any] = {
                "symbol": str(getattr(tl_signal, "symbol", "") or cand.symbol or "UNKNOWN"),
                "setup_type": str(meta.get("setup_type") or "orb_range_breakout"),
                "trigger_direction": str(meta.get("trigger_direction") or ""),
                "option_side": str(getattr(tl_signal, "option_side", "") or "").upper(),
                "trade_id": str(getattr(result, "position_id", "") or "") or "n/a",
                "quantity": int((_p.get("quantity") if isinstance(_p, dict) else 1) or 1),
                "strike": float((_p.get("strike") if isinstance(_p, dict) else 0.0) or 0.0),
                "delta_at_entry": float(
                    (_p.get("delta_at_entry") if isinstance(_p, dict) else 0.0) or 0.0
                ),
                "entry_premium": float(
                    (_p.get("estimated_contract_price") if isinstance(_p, dict) else 0.0) or 0.0
                ),
                "estimated_total_cost": float(
                    (_p.get("estimated_total_cost") if isinstance(_p, dict) else 0.0) or 0.0
                ),
                "diagnostic_stage": "executed" if filled else "post_executor",
                "execution_attempted": True,
            }
            if isinstance(_p, dict):
                _exp = str(
                    _p.get("expiry_ymd")
                    or _p.get("selected_expiry_ymd")
                    or _p.get("expiration_ymd")
                    or _p.get("expiry")
                    or ""
                ).strip()
                if _exp:
                    row["expiry_ymd"] = _exp
            if not filled:
                row["failure_stage"] = "post_executor"
                row["failure_reason"] = str(err or "execute_failed")[:500]
                row["failure_reason_code"] = str(err or "execute_failed").split("|")[0].strip() or "unknown"
            try:
                from modules.unified_0dte_account import sync_unified_account_balance

                row["account_equity_at_entry"] = float(sync_unified_account_balance(system))
            except Exception:
                pass
            if hasattr(system, "_trendline_order_payload_health"):
                try:
                    row.update(system._trendline_order_payload_health(_p))
                except Exception:
                    pass
            return row

        async def _dispatch_breakout_exec_alert(*, filled: bool, err: str = "") -> None:
            am = getattr(system, "alert_manager", None)
            if am is None or not hasattr(am, "send_breakout_options_execution_alert"):
                log.warning(
                    "BREAKOUT_PIPELINE | stage=execution_alert_dispatch | action=skip | "
                    "reason=no_alert_manager | symbol=%s | filled=%s",
                    cand.symbol,
                    str(filled).lower(),
                )
                return
            row = _breakout_exec_alert_row(filled=filled, err=err)
            try:
                sent = await am.send_breakout_options_execution_alert(
                    executed_rows=[row] if filled else [],
                    failed_rows=[] if filled else [row],
                    mode=mode_label,
                    single_emit=True,
                )
                if not sent:
                    log.warning(
                        "BREAKOUT_PIPELINE | stage=execution_alert_dispatch | action=failed | "
                        "symbol=%s | filled=%s | reason=telegram_send_false",
                        cand.symbol,
                        str(filled).lower(),
                    )
            except Exception as alert_err:
                log.warning(
                    "BREAKOUT_PIPELINE | stage=execution_alert_dispatch | alert_error=%s",
                    alert_err,
                )

        if result.success:
            fill_side = str(getattr(bo_signal, "option_side", "") or getattr(cand, "option_side", "") or "").lower()
            if fill_side in ("c", "call"):
                fill_side = "call"
            elif fill_side in ("p", "put"):
                fill_side = "put"
            if fill_side in ("call", "put"):
                _mark_breakout_side_terminal_reject(
                    system, cand, side=fill_side, reason="filled"
                )
            _rearm_breakout_to_wait_break(
                cand, reason="fill_keep_opposite_open", system=system
            )
            executed.discard(cand.symbol)
            system._breakout_signals_executed_today = executed
            open_count += 1
            breakout_open_count += 1
            log.warning(
                "BREAKOUT_EXECUTION_ALLOWED | symbol=%s | position_id=%s | side=%s | strategy_type=breakout_0dte",
                cand.symbol,
                str(getattr(result, "position_id", "") or ""),
                bo_signal.option_side,
            )
            if hasattr(system, "_save_breakout_execution_priority_snapshot"):
                try:
                    system._save_breakout_execution_priority_snapshot(
                        signal=tl_signal,
                        stage="breakout_options_executed",
                        order_payload=dict(_p) if isinstance(_p, dict) else {},
                        broker_success=True,
                        candidate=cand,
                    )
                except Exception as snap_err:
                    log.debug("BREAKOUT fill snapshot skipped: %s", snap_err)
            # Profit optimizer shadow flight recorder
            try:
                if hasattr(system, "_profit_optimizer_track_breakout_fill"):
                    system._profit_optimizer_track_breakout_fill(
                        signal=tl_signal,
                        result=result,
                    )
                else:
                    from profit_optimizer.trading_hooks import track_breakout_fill

                    track_breakout_fill(system, signal=tl_signal, result=result)
            except Exception as po_err:
                log.debug("BREAKOUT PO track skipped: %s", po_err)
            await _dispatch_breakout_exec_alert(filled=True)
        else:
            err = str(getattr(result, "error", "") or "execute_failed")
            reason_code = _breakout_post_exec_reason_code(err)
            terminal = _is_breakout_terminal_post_exec_reject(err)
            fail_side = str(
                getattr(bo_signal, "option_side", None)
                or getattr(cand, "option_side", None)
                or ""
            ).lower()
            log.warning(
                "BREAKOUT_PIPELINE | stage=execute_fail | symbol=%s | reason=%s | "
                "terminal=%s | side=%s",
                cand.symbol,
                err,
                str(terminal).lower(),
                fail_side or "n/a",
            )
            if hasattr(system, "_save_breakout_execution_priority_snapshot"):
                try:
                    system._save_breakout_execution_priority_snapshot(
                        signal=tl_signal,
                        stage="breakout_options_executed",
                        order_payload=dict(_p) if isinstance(_p, dict) else {},
                        broker_success=False,
                        broker_error=err,
                        candidate=cand,
                    )
                except Exception:
                    pass
            # One alert per attempt — then leave CONFIRM so we do not spam / hog slots.
            await _dispatch_breakout_exec_alert(filled=False, err=err)
            if terminal:
                _side_day_block_and_rearm_breakout(
                    system,
                    cand,
                    side=fail_side,
                    reason=reason_code,
                )
                log.warning(
                    "BREAKOUT_PIPELINE | stage=execute_fail_terminal | symbol=%s | "
                    "side=%s | reason=%s | action=side_cooldown_keep_opposite_open",
                    cand.symbol,
                    fail_side or "n/a",
                    reason_code,
                )
            else:
                # Transient broker/API miss: free must-run slot; opposite/retry later OK.
                _rearm_breakout_to_wait_break(
                    cand, reason=f"post_exec_transient:{reason_code}", system=system
                )

    try:
        from modules.discovery_watch_fsm_persist import sync_watch_fsm_after_cycle

        sync_watch_fsm_after_cycle(system)
    except Exception:
        pass


def reset_breakout_daily_state(system: Any) -> None:
    system._breakout_seed_candidates = {}
    system._breakout_watch_candidates = {}
    system._breakout_watch_active = False
    system._breakout_signals_executed_today = set()
    system._breakout_side_terminal_rejects = {}
    try:
        from modules.path_exec_day_exclude import PATH_BREAKOUT, clear_path_exec_rejects_today

        clear_path_exec_rejects_today(system, PATH_BREAKOUT)
    except Exception:
        system._breakout_exec_rejects_today = set()
    system._breakout_watch_rotate_idx = 0
    system._breakout_activated_pt_date = None
    system._breakout_po_entry_features_by_symbol = {}
    system._orb2_data = {}
    system._orb2_captured_pt_date = None
    system._orb2_capture_permitted_post_0dte = False
    system._orb_0dte_filled_symbols_today = set()
    system._dte0_executed_symbols_today = set()
    system._step5_0dte_pp_deferred = False
    system._breakout_activate_retry_ts = 0.0
    mgr = getattr(system, "orb_strategy_manager", None)
    if mgr is not None:
        try:
            if hasattr(mgr, "orb2_data"):
                mgr.orb2_data.clear()
            if hasattr(mgr, "_orb2_captured_pt_date"):
                mgr._orb2_captured_pt_date = None
        except Exception:
            pass
