"""Unified 0DTE discovery equity scan for ORB1 Breakout + Trendline + ORB2 Breakout.

One scan owns broker quotes:
  - Universe = ``0dte_list`` (already unique; 118 names)
  - Batch = next **24** symbols by CSV tier → row (shared symbol window)
  - Cadence = **10s**, and the cursor **always advances** this window
  - Full-list coverage: ``ceil(N/24) * 10s`` → **118 names ≤ 50s**
  - Quotes feed **TL0 (≤07:25)**, **ORB1 (06:45–07:25)**, then **TL1–6 + ORB2 BO (≥07:30)**

No status/rank queue. A symbol is quoted only while it sits in this scan's
24-name window; CONFIRM / READY / body-beyond **do not** steal slots, pin a
name across scans, or block the next 24. Path eval uses this same window.
Do **not** reorder by proximity to ORB H/L or TL lines.

Active paths evaluate the **same** forced window each discovery tick.
TL0 discovery ends at **07:25**; 07:25–07:30 is the ORB2 capture gap; TL1+ / BO gate at ≥07:30 via
``_tl_bo_discovery_session_open``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Callable, List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)

FORCED_DISCOVERY_SYMBOLS_ATTR = "_0dte_forced_discovery_symbols"
FORCED_DISCOVERY_MONO_ATTR = "_0dte_forced_discovery_symbols_mono"
SHARED_LIST_ROTATE_OFFSET_ATTR = "_0dte_discovery_list_rotate_offset"
UNIFIED_SCAN_LAST_MONO_ATTR = "_0dte_unified_discovery_last_mono"


def unified_discovery_scan_slot_free(scan_task: Any) -> bool:
    """10s scan slot is free. A running watch task must not block this."""
    return scan_task is None or bool(getattr(scan_task, "done", lambda: True)())


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, str(default)) or default))
    except Exception:
        return int(default)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or default)
    except Exception:
        return float(default)


def discovery_batch_size() -> int:
    """Single batch size for the unified list scan (default 24, ≤ ETrade hard cap)."""
    try:
        from modules.broker_api_pacing import etrade_batch_quote_size

        hard = int(etrade_batch_quote_size())
    except Exception:
        hard = 25
    raw = 24
    try:
        from modules.config_loader import get_config_value

        raw = int(
            float(
                get_config_value(
                    "0DTE_DISCOVERY_SYMBOLS_PER_TICK",
                    get_config_value(
                        "0DTE_DISCOVERY_MERGED_MAX_WHILE_OPEN",
                        get_config_value("0DTE_DISCOVERY_SYMBOLS_PER_TICK_WHILE_OPEN", 24),
                    ),
                )
            )
        )
    except Exception:
        raw = _env_int(
            "0DTE_DISCOVERY_SYMBOLS_PER_TICK",
            _env_int(
                "0DTE_DISCOVERY_MERGED_MAX_WHILE_OPEN",
                _env_int("0DTE_DISCOVERY_SYMBOLS_PER_TICK_WHILE_OPEN", 24),
            ),
        )
    return max(1, min(int(raw), int(hard)))


def discovery_max_quotes_while_open() -> int:
    """Equity quotes per scan while any 0DTE is open — same 24 rotate, never extras-first."""
    cap = discovery_batch_size()
    try:
        from modules.config_loader import get_config_value

        raw = int(
            float(get_config_value("0DTE_DISCOVERY_MAX_QUOTES_WHILE_OPEN", cap) or cap)
        )
    except Exception:
        raw = _env_int("0DTE_DISCOVERY_MAX_QUOTES_WHILE_OPEN", cap)
    return max(1, min(int(raw), int(cap)))


def discovery_full_list_cycle_sec(universe_n: int) -> float:
    """Seconds to visit every symbol at least once (118 @ 24/10s → 50s)."""
    n = max(0, int(universe_n or 0))
    per = max(1, int(discovery_batch_size()))
    interval = float(discovery_interval_sec())
    if n <= 0:
        return 0.0
    ticks = (n + per - 1) // per
    return float(ticks) * interval


def discovery_interval_sec() -> float:
    """Single discovery cadence (default 10s; 118 names @ 24/tick → full list in ≤50s)."""
    try:
        from modules.config_loader import get_config_value

        return max(10.0, float(get_config_value("0DTE_DISCOVERY_INTERVAL_SEC", 10)))
    except Exception:
        return max(
            10.0,
            _env_float(
                "0DTE_DISCOVERY_INTERVAL_SEC",
                _env_float("TRENDLINE_WATCH_INTERVAL_SEC", 10.0),
            ),
        )


def _sym(c: Any) -> str:
    return str(getattr(c, "symbol", "") or "").strip().upper()


def _state_text(c: Any) -> str:
    """Name + value, uppercased.

    Live TL/BO/ORB1 states are ``str`` Enums (``waiting_for_confirmation``).
    Older checks looked for ``WAITING_FOR_CONFIRMATION`` in the raw ``str(state)``
    and missed every CONFIRM/READY extra.
    """
    st = getattr(c, "state", None)
    bits = [str(st or "")]
    name = getattr(st, "name", None)
    if name:
        bits.append(str(name))
    return " ".join(bits).upper()


def _is_ready_state(c: Any) -> bool:
    return "READY_TO_EXECUTE" in _state_text(c)


def _tl_body_beyond_watch(c: Any) -> bool:
    """First open/close beyond the line — poll every tick until emit or expire."""
    setup = getattr(c, "setup_payload", None) or {}
    if not isinstance(setup, dict):
        return False
    if setup.get("body_beyond_watch"):
        return True
    simple = setup.get("simple_mode")
    return bool(isinstance(simple, dict) and simple.get("body_beyond_watch"))


def _is_must_run_state(c: Any) -> bool:
    """CONFIRM / READY / body-beyond — diagnostic only. Does not pin scan slots."""
    st = _state_text(c)
    if "READY_TO_EXECUTE" in st or "WAITING_FOR_CONFIRMATION" in st:
        return True
    return _tl_body_beyond_watch(c)


def tier_sorted_candidates(candidates: Sequence[Any]) -> List[Any]:
    try:
        from modules.dte_list_order import rotation_tier_key
    except Exception:

        def rotation_tier_key(symbol: str) -> Tuple[int, int, str]:
            return (99, 10_000, str(symbol or "").upper())

    return sorted(list(candidates or []), key=lambda c: rotation_tier_key(_sym(c)))


def tier_sorted_symbols(symbols: Sequence[str]) -> List[str]:
    try:
        from modules.dte_list_order import rotation_tier_key
    except Exception:

        def rotation_tier_key(symbol: str) -> Tuple[int, int, str]:
            return (99, 10_000, str(symbol or "").upper())

    uniq: List[str] = []
    seen = set()
    for raw in symbols or []:
        s = str(raw or "").strip().upper()
        if not s or s in seen:
            continue
        seen.add(s)
        uniq.append(s)
    return sorted(uniq, key=rotation_tier_key)


def rotate_window(
    ordered: Sequence[Any],
    *,
    per_tick: int,
    offset: int,
) -> Tuple[List[Any], int]:
    n = len(ordered)
    if n <= 0 or per_tick <= 0:
        return [], int(offset or 0)
    if n <= per_tick:
        return list(ordered), 0
    start = int(offset or 0) % n
    out = [ordered[(start + i) % n] for i in range(per_tick)]
    return out, (start + per_tick) % n


def full_0dte_list_symbols(system: Any = None) -> List[str]:
    """Canonical unique 0dte_list order (tier → row)."""
    syms: List[str] = []
    if system is not None:
        for attr in ("symbol_list", "_0dte_symbol_list", "daily_watchlist"):
            raw = getattr(system, attr, None)
            if isinstance(raw, (list, tuple)) and raw:
                syms = [str(s).strip().upper() for s in raw if str(s).strip()]
                break
    if not syms:
        try:
            from modules.dte_list_order import load_0dte_symbol_tier_order

            mapping = load_0dte_symbol_tier_order() or {}
            syms = list(mapping.keys())
        except Exception:
            syms = []
    return tier_sorted_symbols(syms)


def collect_ready_discovery_symbols(system: Any) -> List[str]:
    """CONFIRM/READY names for logs and cooldown filters. Never consumes rotate slots."""
    out: List[str] = []
    seen = set()

    def _add(sym: str) -> None:
        s = str(sym or "").strip().upper()
        if not s or s in seen:
            return
        seen.add(s)
        out.append(s)

    try:
        from modules.path_exec_day_exclude import (
            is_trendline_candidate_on_slot_side_cooldown,
        )
    except Exception:
        def is_trendline_candidate_on_slot_side_cooldown(system: Any, candidate: Any) -> bool:
            return False

    try:
        eng = getattr(system, "trendline_signal_engine", None)
        cands = getattr(eng, "candidates", None) or {}
        for c in cands.values():
            if not _is_must_run_state(c):
                continue
            if is_trendline_candidate_on_slot_side_cooldown(system, c):
                continue
            _add(_sym(c))
    except Exception:
        pass
    try:
        from modules.breakout_watch import _breakout_side_is_terminal_rejected
    except Exception:
        def _breakout_side_is_terminal_rejected(system: Any, cand: Any, side: str) -> bool:
            return False

    try:
        watch = getattr(system, "_breakout_watch_candidates", None) or {}
        for c in watch.values():
            if not _is_must_run_state(c):
                continue
            side = str(getattr(c, "option_side", "") or "").strip().lower()
            if side in ("call", "put") and _breakout_side_is_terminal_rejected(system, c, side):
                continue
            _add(_sym(c))
    except Exception:
        pass
    try:
        from modules.orb1_0dte_watch import is_orb1_side_on_cooldown
    except Exception:
        def is_orb1_side_on_cooldown(system: Any, symbol: str, side: Any) -> bool:
            return False

    try:
        orb1 = getattr(system, "_orb1_0dte_watch_candidates", None) or {}
        for c in orb1.values() if isinstance(orb1, dict) else list(orb1 or []):
            if not _is_must_run_state(c):
                continue
            side = str(getattr(c, "option_side", "") or "").strip().lower()
            if side in ("call", "put") and is_orb1_side_on_cooldown(system, _sym(c), side):
                continue
            _add(_sym(c))
    except Exception:
        pass
    return out


# Alias — diagnostic CONFIRM/READY set; does not pin discovery slots.
collect_must_run_discovery_symbols = collect_ready_discovery_symbols


def select_tier_rotated_batch(
    candidates: Sequence[Any],
    *,
    per_tick: int,
    offset: int,
    is_ready: Optional[Callable[[Any], bool]] = None,
    forced_symbols: Optional[Sequence[str]] = None,
) -> Tuple[List[Any], int, dict]:
    """Filter path candidates to this scan's 24-name list window.

    Status/rank extras are not appended. A CONFIRM name is evaluated only when
    it is in the current rotate window (or the unified forced quote set).
    """
    del is_ready
    ordered = tier_sorted_candidates(candidates)
    if not ordered:
        return [], int(offset or 0), {"mode": "empty"}

    by_sym: dict = {}
    for c in ordered:
        s = _sym(c)
        if not s:
            continue
        by_sym.setdefault(s, []).append(c)

    if forced_symbols is not None:
        forced = [str(s or "").strip().upper() for s in forced_symbols if str(s or "").strip()]
        batch: List[Any] = []
        seen: set = set()

        def _append(c: Any) -> None:
            key = id(c)
            if key in seen:
                return
            seen.add(key)
            batch.append(c)

        for s in forced:
            for c in by_sym.get(s, []):
                _append(c)
        return (
            batch,
            int(offset or 0),
            {
                "mode": "unified_list_window",
                "forced": len(forced),
                "batch": len(batch),
                "universe": len(ordered),
            },
        )

    cap = max(1, int(per_tick or 1))
    rotating, new_offset = rotate_window(ordered, per_tick=cap, offset=int(offset or 0))
    return (
        list(rotating),
        int(new_offset),
        {
            "mode": "path_fallback_rotate",
            "batch": len(rotating),
            "universe": len(ordered),
            "offset": int(offset or 0),
            "new_offset": int(new_offset),
            "per_tick": cap,
        },
    )


def store_forced_discovery_symbols(system: Any, symbols: Sequence[str]) -> None:
    if system is None:
        return
    syms = [str(s or "").strip().upper() for s in (symbols or []) if str(s or "").strip()]
    try:
        setattr(system, FORCED_DISCOVERY_SYMBOLS_ATTR, syms)
        setattr(system, FORCED_DISCOVERY_MONO_ATTR, time.monotonic())
    except Exception:
        pass


def take_forced_discovery_symbols(
    system: Any,
    *,
    max_age_sec: float = 45.0,
) -> Optional[List[str]]:
    if system is None:
        return None
    try:
        syms = getattr(system, FORCED_DISCOVERY_SYMBOLS_ATTR, None)
        mono = float(getattr(system, FORCED_DISCOVERY_MONO_ATTR, 0.0) or 0.0)
    except Exception:
        return None
    if not isinstance(syms, list) or not syms:
        return None
    if max_age_sec > 0 and (time.monotonic() - mono) > float(max_age_sec):
        return None
    return [str(s).strip().upper() for s in syms if str(s).strip()]


def merged_discovery_window_symbols(
    system: Any,
    *,
    max_age_sec: float = 45.0,
) -> Optional[List[str]]:
    """Symbols from this tick's unified quote batch if forced-window attr aged out."""
    if system is None:
        return None
    try:
        from modules.open_position_quote_orchestration import MERGED_DISCOVERY_CACHE_ATTR

        cache = getattr(system, MERGED_DISCOVERY_CACHE_ATTR, None)
    except Exception:
        return None
    if not isinstance(cache, dict):
        return None
    try:
        age = time.monotonic() - float(cache.get("mono") or 0.0)
        if max_age_sec > 0 and age > float(max_age_sec):
            return None
        have = cache.get("symbols") or set()
        out = [str(s).strip().upper() for s in have if str(s).strip()]
        return out or None
    except Exception:
        return None


def clear_forced_discovery_symbols(system: Any) -> None:
    if system is None:
        return
    try:
        setattr(system, FORCED_DISCOVERY_SYMBOLS_ATTR, None)
        setattr(system, FORCED_DISCOVERY_MONO_ATTR, 0.0)
    except Exception:
        pass


def next_shared_list_window(
    system: Any,
    symbols: Sequence[str],
    *,
    per_tick: int,
    advance: bool = True,
) -> List[str]:
    ordered = tier_sorted_symbols(symbols)
    if not ordered:
        return []
    offset = int(getattr(system, SHARED_LIST_ROTATE_OFFSET_ATTR, 0) or 0)
    window, new_offset = rotate_window(ordered, per_tick=per_tick, offset=offset)
    if advance:
        try:
            setattr(system, SHARED_LIST_ROTATE_OFFSET_ATTR, int(new_offset))
        except Exception:
            pass
        log.info(
            "DISCOVERY_LIST_ROTATE | universe=%d | this_tick=%d | offset=%d | next_offset=%d | "
            "per_tick=%d | head=%s",
            len(ordered),
            len(window),
            offset,
            new_offset,
            int(per_tick),
            ",".join(str(x).strip().upper() for x in window[:8]),
        )
    return [str(x).strip().upper() for x in window]


async def run_unified_0dte_discovery_scan(system: Any) -> List[str]:
    """One ETrade equity batch: next 24 of 0dte_list, then advance.

    CONFIRM/READY status never consumes a slot or holds a name past this scan.
    118 symbols @ 24 / 10s → every name at least once in ≤50s.
    """
    from modules.open_position_quote_orchestration import (
        clear_merged_discovery_quotes,
        open_quote_priority_active,
        store_merged_discovery_quotes,
        yield_to_open_position_monitor,
    )

    clear_merged_discovery_quotes(system)
    clear_forced_discovery_symbols(system)
    # Open-quote latency does not sit on this path. A slow exit batch used to
    # log budget_soft here and then the watches returned before Trade Profile
    # (IREN TL2, 2026-10-29, 9:20 cross, first stamp 10:34).
    reason = "rotate"

    if not getattr(system, "data_manager", None):
        log.info("DISCOVERY_UNIFIED | stage=skip | reason=no_data_manager")
        return []

    per_tick = discovery_batch_size()
    universe = full_0dte_list_symbols(system)
    if not universe:
        log.warning("DISCOVERY_UNIFIED | stage=skip | reason=empty_0dte_list")
        return []

    if bool(open_quote_priority_active(system)):
        per_tick = min(int(per_tick), int(discovery_max_quotes_while_open()))
    # Always the next 24 list names. Advance even if some quotes miss —
    # a symbol must not stay in this window past one scan.
    window = next_shared_list_window(
        system,
        universe,
        per_tick=per_tick,
        advance=True,
    )
    from modules.spy_session_quote import with_spy

    # SPY is pinned on every scan. It is not one of the 24 rotate slots.
    quote_syms = with_spy(window)

    # Forced names stay the rotate window. SPY is only on the quote batch.
    # Up to half a second for the exit monitor, then the quote. This does not
    # skip the scan.
    store_forced_discovery_symbols(system, window)
    try:
        await yield_to_open_position_monitor(system, max_wait_s=0.5)
    except Exception:
        await asyncio.sleep(0)
    quotes = {}
    try:
        from modules.broker_api_pacing import LANE_TRENDLINE_WATCH_EQUITY

        try:
            quotes = await system.data_manager.get_batch_quotes(
                quote_syms,
                skip_cache=True,
                market_lane=LANE_TRENDLINE_WATCH_EQUITY,
            ) or {}
        except TypeError:
            quotes = await system.data_manager.get_batch_quotes(
                quote_syms, skip_cache=True
            ) or {}
    except Exception:
        quotes = await system.data_manager.get_batch_quotes(quote_syms, skip_cache=True) or {}
    store_merged_discovery_quotes(
        system,
        quotes if isinstance(quotes, dict) else {},
        quote_syms,
    )
    try:
        from modules.spy_session_quote import record_quote_batch

        record_quote_batch(quotes)
    except Exception:
        pass
    try:
        ingest = getattr(system, "_ingest_broker_quotes_into_session_tech", None)
        if callable(ingest) and isinstance(quotes, dict) and quotes:
            ingest(quotes)
    except Exception:
        pass
    try:
        from modules.broker_api_pacing import (
            LANE_TRENDLINE_WATCH_EQUITY,
            record_broker_lane_request,
        )

        record_broker_lane_request(LANE_TRENDLINE_WATCH_EQUITY)
    except Exception:
        pass

    try:
        setattr(system, UNIFIED_SCAN_LAST_MONO_ATTR, time.monotonic())
    except Exception:
        pass
    # t168u: ONE path-agnostic 5m chain pre-sampler for dir_pct_change_5m.
    # Every path's live candidates (ORB1, TL0-TL6, ORB2/Breakout; CALL/PUT) are
    # read through one level interface; no API of its own, capped P2 queue via
    # the chain fetch manager. DIR5M_APPROACH_SAMPLER_ENABLED=false -> no-op.
    try:
        from central_tech.dir5m_presample import on_discovery_quotes

        on_discovery_quotes(system, quotes if isinstance(quotes, dict) else {})
    except Exception:
        pass
    # t166u: debounced persist of the 5m chain ring (no broker call; the GCS
    # write runs off the event loop). Discovery quotes carry no chain volume,
    # so discovery does not record ring samples itself.
    try:
        from central_tech.chain_ring_persist import maybe_flush_chain_ring

        maybe_flush_chain_ring(system)
    except Exception:
        pass

    hot = {
        str(s).strip().upper()
        for s in collect_ready_discovery_symbols(system)
        if str(s).strip()
    }
    in_window_hot = len([s for s in window if s in hot])
    start_offset = (int(getattr(system, SHARED_LIST_ROTATE_OFFSET_ATTR, 0) or 0) - len(window)) % max(
        1, len(universe)
    )
    log.info(
        "DISCOVERY_UNIFIED | stage=scan | universe=%d | window=%d | quote=%d | "
        "must_run_extra=0 | in_window_hot=%d | quotes=%d | reason=%s | offset=%d | "
        "head=%s | window_syms=%s | cycle_sec=%.0f",
        len(universe),
        len(window),
        len(quote_syms),
        in_window_hot,
        len(quotes) if isinstance(quotes, dict) else 0,
        reason,
        start_offset,
        ",".join(window[:8]),
        ",".join(window),
        discovery_full_list_cycle_sec(len(universe)),
    )
    return list(window)
