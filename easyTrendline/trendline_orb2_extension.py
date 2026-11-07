#!/usr/bin/env python3
"""Trendline2–Trendline10 — sequential ORB2 material-break injection.

TL0–TL10 share one envelope fitter and product mapping
(descending resistance → CALL, ascending support → PUT). A1 policy:
  TL1 — ORB2 extreme @ post-ORB2 build
  TL2 — ORB2 opposite edge @ 1st material ORB2 break (farthest box edge from the break)
  TL3+ — Yahoo 1m session extreme @ 2nd+ material ORB2 break (often beyond ORB2 H/L;
         A2 is the hull through confirm, often shallower after a pullback)

A2 is always the next structure touch (lower high / higher low), same hull as TL0/TL1.

Sequencing (one line per **ORB2 breakout episode**):
  1) TL1 envelopes finish first (discovery held until primary TL1 completes).
     Names already outside the ORB2 box at/after 07:30 stay on **TL1** — do not
     inject TL2 from that already-extended print (KTOS 2026-08-25).
  2) First **post-freeze** material ORB2 break that starts from **inside** the
     box → **TL2** (expire TL1; Yahoo 1m backfill; ORB2-edge A1). If the name
     was already through the box at freeze, require the same reclaim 1m close
     as TL3+ (``max(0.08%, min_break)`` into the box from the **broken edge**),
     then a later break. A close already at that depth **is** the reclaim —
     do not force another exit/re-enter. A nick inside the H/L is not.
  3) After a **1m close ≥ max(0.08%, min_break)** into the ORB2 box from the
     broken edge, next material break → **TL3**
  4) Each later inward 1m close at that same floor, then next material break →
     **TL4 / TL5 / … / TL10** (same session-extreme family as TL3). Cap is
     ``MAX_TRENDLINE_SLOT`` (currently 10). After the newest slot is built,
     the next reclaim+break still runs the injector — it does not silently
     drop the episode. Raise the constant if a name needs a later line.

A bounce that stays outside the box (including the 0.15% material buffer) is the
same episode — not a later slot. A wick into the box, or a 1m close that only nicks
the H/L (AMD 11:05: +3.5¢ / 0.007% inside ORB2 low; IBIT 2026-08-24: 4–5¢ /
0.08–0.11% inside ORB2 high), is a retest — not a new breakout. Effective
reclaim is ``max(TRENDLINE_ORB2_RECLAIM_INSIDE_PCT, min_break_pct)`` so a cycle
around H/L cannot clear with a shallower move than the break itself.

Active discovery per symbol is the newest non-expired slot.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .trendline_models import (
    OHLCVBar,
    MAX_TRENDLINE_SLOT,
    ORB2_EXTENSION_BUILT_FLAGS,
    ORB2_EXTENSION_PENDING_FLAGS,
    TrendlineCandidate,
    TrendlineCandidateState,
    TrendlineDirection,
    TrendlineSetupType,
    TRENDLINE_SLOT_RANK,
    make_trendline_candidate_key,
)

log = logging.getLogger(__name__)

EXT_ABOVE = "above_orb2_high"
EXT_BELOW = "below_orb2_low"


def _orb2_levels(system: Any) -> Dict[str, Tuple[float, float]]:
    try:
        from modules.breakout_watch import _frozen_orb2_levels

        return dict(_frozen_orb2_levels(system) or {})
    except Exception:
        return {}


def _min_break_distance_pct(system: Any) -> float:
    cfg = getattr(getattr(system, "trendline_signal_engine", None), "config", None)
    try:
        v = float(getattr(cfg, "orb2_envelope_min_break_distance_pct", 0.0015) or 0.0015)
        if v > 0:
            return v
    except Exception:
        pass
    try:
        from easyBreakout.breakout_config_loader import load_breakout_config_from_env
        from modules.config_loader import get_config_value

        bo = load_breakout_config_from_env(get_config_value)
        return float(getattr(bo, "min_break_distance_pct", 0.0015) or 0.0015)
    except Exception:
        return 0.0015


def _material_triggers(orb_high: float, orb_low: float, min_break_pct: float) -> Tuple[float, float]:
    call_trig = float(orb_high) * (1.0 + float(min_break_pct))
    put_trig = float(orb_low) * (1.0 - float(min_break_pct))
    return call_trig, put_trig


def _extension_side_for_price(
    price: float, orb_high: float, orb_low: float, min_break_pct: float
) -> Optional[str]:
    if price <= 0 or orb_high <= 0 or orb_low <= 0 or orb_high <= orb_low:
        return None
    call_trig, put_trig = _material_triggers(orb_high, orb_low, min_break_pct)
    if float(price) >= call_trig:
        return EXT_ABOVE
    if float(price) <= put_trig:
        return EXT_BELOW
    return None


def _price_inside_orb2(price: float, orb_high: float, orb_low: float) -> bool:
    return float(orb_low) < float(price) < float(orb_high)


def _latest_minute_close(bars: List[OHLCVBar]) -> Optional[float]:
    aligned = _minute_aligned_structure_bars(bars)
    if not aligned:
        return None
    try:
        px = float(getattr(aligned[-1], "close", 0.0) or 0.0)
    except (TypeError, ValueError):
        return None
    return px if px > 0 else None


def _reclaim_inside_pct(system: Any) -> float:
    """Inward 1m-close floor before the next extension episode can arm.

    Used for TL3+ after a built line, and for the *first* TL2 when the name
    was already outside the ORB2 box at the 07:30 freeze (TL1 keeps that
    envelope until this reclaim). Config default is 0.08% (AMD nick guard).
    Effective floor is ``max(configured, min_break_pct)`` so a cycle around
    ORB2 H/L cannot clear with a shallower print than the 0.15% material
    break (IBIT 8/24: 45.080 vs high 45.1187 = 0.086% — same episode).
    """
    configured = 0.0008
    cfg = getattr(getattr(system, "trendline_signal_engine", None), "config", None)
    try:
        v = float(getattr(cfg, "orb2_reclaim_inside_pct", 0.0008) or 0.0008)
        if v > 0:
            configured = v
    except Exception:
        pass
    try:
        min_break = float(_min_break_distance_pct(system) or 0.0)
    except Exception:
        min_break = 0.0
    if min_break > configured:
        return min_break
    return configured


def _inward_pct_from_broken_edge(
    price: float, orb_high: float, orb_low: float, armed_side: Optional[str]
) -> Optional[float]:
    px = float(price)
    if armed_side == EXT_BELOW and float(orb_low) > 0:
        return (px - float(orb_low)) / float(orb_low)
    if armed_side == EXT_ABOVE and float(orb_high) > 0:
        return (float(orb_high) - px) / float(orb_high)
    return None


def _close_reclaimed_from_side(
    price: float,
    orb_high: float,
    orb_low: float,
    armed_side: Optional[str],
    reclaim_pct: float,
) -> bool:
    """True when ``price`` is ≥ reclaim_pct into the box from the broken edge.

    With ``armed_side``, measure from that edge only. With no side, require
    that depth from **both** edges so a hug of either wall is not a reclaim.
    """
    px = float(price)
    if not _price_inside_orb2(px, orb_high, orb_low):
        return False
    need = float(reclaim_pct) if reclaim_pct and reclaim_pct > 0 else 0.0008
    if armed_side == EXT_BELOW:
        return px >= float(orb_low) * (1.0 + need)
    if armed_side == EXT_ABOVE:
        return px <= float(orb_high) * (1.0 - need)
    return px >= float(orb_low) * (1.0 + need) and px <= float(orb_high) * (1.0 - need)


def _reclaim_inside_orb2(
    price: float,
    orb_high: float,
    orb_low: float,
    bars: List[OHLCVBar],
    *,
    armed_side: Optional[str] = None,
    reclaim_pct: float = 0.0008,
) -> bool:
    """True only after a real reclaim into the ORB2 box.

    The gap between the box edge and the 0.15% material trigger is still the
    same breakout. A 1m close that only nicks the H/L (inside by pennies, or
    IBIT-style 0.08–0.11% while min_break is 0.15%) is also the same episode.
    Require ≥ ``reclaim_pct`` in from the broken edge. Prefer the latest Yahoo
    1m close so a wick through the edge does not start a new TL3+ episode.
    """
    close_px = _latest_minute_close(bars)
    px = close_px if close_px is not None else float(price)
    return _close_reclaimed_from_side(px, orb_high, orb_low, armed_side, reclaim_pct)


def _event_state(system: Any) -> Dict[str, Dict[str, Any]]:
    """Per-symbol ORB2 break episode tracker for TL2→TL10 sequencing."""
    m = getattr(system, "_trendline_orb2_break_events_today", None)
    if not isinstance(m, dict):
        m = {}
        system._trendline_orb2_break_events_today = m
    return m


def _orb2_extension_steps() -> Tuple[Tuple[str, str, int, str, Optional[str]], ...]:
    """(slot, envelope_role, break_index, built_key, pending_key).

    break_index 1 = TL2 (primary). 2..N-1 = session-extreme TL3..TL{MAX}.
    """
    steps: List[Tuple[str, str, int, str, Optional[str]]] = [
        ("trendline2", "primary", 1, "tl2_built", None),
    ]
    for n in range(3, int(MAX_TRENDLINE_SLOT) + 1):
        steps.append(
            (f"trendline{n}", "session_extreme", n - 1, f"tl{n}_built", f"tl{n}_pending")
        )
    return tuple(steps)


_ORB2_EXTENSION_STEPS = _orb2_extension_steps()
_PENDING_BY_BREAK = {
    idx: pending for _, _, idx, _, pending in _ORB2_EXTENSION_STEPS if pending
}
_BUILT_BY_BREAK = {idx: built for _, _, idx, built, _ in _ORB2_EXTENSION_STEPS}

_SYM_STATE_DEFAULTS: Dict[str, Any] = {
    "break_count": 0,
    "armed_side": None,
    "in_extension": False,
    "cleared": True,
    # First TL2 may not fire from an already-extended 07:30 print.
    "seen_inside_post_730": False,
    "await_reclaim_before_tl2": False,
}
for _built in ORB2_EXTENSION_BUILT_FLAGS:
    _SYM_STATE_DEFAULTS[_built] = False
for _pending in ORB2_EXTENSION_PENDING_FLAGS:
    _SYM_STATE_DEFAULTS[_pending] = False


def _sym_state(system: Any, symbol: str) -> Dict[str, Any]:
    store = _event_state(system)
    st = store.get(symbol)
    if not isinstance(st, dict):
        st = dict(_SYM_STATE_DEFAULTS)
        store[symbol] = st
    else:
        for key, val in _SYM_STATE_DEFAULTS.items():
            st.setdefault(key, val)
    return st


def _extension_pending_retry(st: Dict[str, Any]) -> bool:
    prev_built = True
    for _slot, _role, _idx, built_key, pending_key in _ORB2_EXTENSION_STEPS:
        if pending_key is None:
            prev_built = bool(st.get(built_key))
            continue
        if prev_built and not st.get(built_key) and st.get(pending_key):
            return True
        prev_built = bool(st.get(built_key))
    return False


def _same_episode_hold(st: Dict[str, Any], side: str) -> bool:
    return bool(
        st.get("in_extension")
        and st.get("armed_side") == side
        and not st.get("cleared")
        and not _extension_pending_retry(st)
    )


def _new_break_qualifies(st: Dict[str, Any], side: str, pending_key: str) -> bool:
    if st.get(pending_key) and st.get("armed_side") == side:
        return True
    if st.get("cleared"):
        return True
    if st.get("armed_side") and st.get("armed_side") != side:
        return True
    return False


def _next_extension_spec(st: Dict[str, Any], side: str) -> Optional[Tuple[str, str, int]]:
    """(slot, envelope_role, break_index) when this tick should build a line."""
    if not st.get("tl2_built"):
        return ("trendline2", "primary", 1)
    if _same_episode_hold(st, side):
        return None
    for slot, role, idx, built_key, pending_key in _ORB2_EXTENSION_STEPS[1:]:
        if st.get(built_key):
            continue
        if pending_key and _new_break_qualifies(st, side, pending_key):
            return (slot, role, idx)
        return None
    return None


def _built_map(system: Any) -> Dict[str, str]:
    """Compat map for logs / older probes (slot stamps)."""
    m = getattr(system, "_trendline_orb2_extension_lines_built_today", None)
    if not isinstance(m, dict):
        m = {}
        system._trendline_orb2_extension_lines_built_today = m
    return m


def _last_price(system: Any, symbol: str, bars: List[OHLCVBar]) -> float:
    for attr in ("_discovery_merged_quote_cache", "_trendline_last_quotes", "_breakout_last_quotes"):
        cache = getattr(system, attr, None)
        if not isinstance(cache, dict):
            continue
        q = cache.get(symbol) or cache.get(symbol.upper())
        if not isinstance(q, dict):
            continue
        for k in ("last", "last_price", "close", "price", "mark"):
            try:
                px = float(q.get(k) or 0.0)
            except (TypeError, ValueError):
                continue
            if px > 0:
                return px
    if bars:
        try:
            px = float(bars[-1].close or 0.0)
            if px > 0:
                return px
        except Exception:
            pass
    return 0.0


def _minute_aligned_structure_bars(bars: List[OHLCVBar]) -> List[OHLCVBar]:
    """Yahoo-grade 1m bars only. Quote ticks must not bind TL2–TL10 A1/A2."""
    out: List[OHLCVBar] = []
    for b in bars or []:
        ts = getattr(b, "ts", None)
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts.second == 0 and int(getattr(ts, "microsecond", 0) or 0) == 0:
            out.append(b)
    return out


def _bars_from_cache(system: Any, symbol: str) -> List[OHLCVBar]:
    """Real cached bars only — never synth (TL2/TL3 geometry requires Yahoo path)."""
    engine = getattr(system, "trendline_signal_engine", None)
    if engine is not None:
        cache = getattr(engine, "_bar_cache", None) or {}
        bars = cache.get(symbol) or cache.get(str(symbol).upper())
        if isinstance(bars, list) and bars:
            return list(bars)
    return []


def _bars_for_symbol(system: Any, symbol: str) -> List[OHLCVBar]:
    """Cache bars for inject; empty when thin (caller must Yahoo-hydrate first).

    Synth is retained only for non-geometry callers / tests via
    ``_synthesize_bars_for_orb2_inject`` — inject must not use it.
    """
    return _bars_from_cache(system, symbol)


def _inject_min_bars() -> int:
    try:
        return int(os.getenv("TRENDLINE_WATCH_YF_1M_MIN_BARS", "15") or 15)
    except (TypeError, ValueError):
        return 15


def _rth_730_cutoff_utc(bars: List[OHLCVBar]) -> Optional[datetime]:
    """07:30 PT cutoff in UTC from the latest bar's session date."""
    if not bars:
        return None
    try:
        from zoneinfo import ZoneInfo

        pt = ZoneInfo("America/Los_Angeles")
        last = None
        for b in reversed(bars):
            ts = getattr(b, "ts", None)
            if ts is None:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            last = ts
            break
        if last is None:
            return None
        day = last.astimezone(pt).date()
        return datetime(day.year, day.month, day.day, 7, 30, tzinfo=pt).astimezone(
            timezone.utc
        )
    except Exception:
        return None


def _bar_ts_utc(bar: OHLCVBar) -> Optional[datetime]:
    ts = getattr(bar, "ts", None)
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts


def _bars_cover_post_730(bars: List[OHLCVBar]) -> bool:
    """True when cache has at least one bar after the session 07:30 PT cutoff.

    Uses the date of the latest bar (not wall-clock today) so day-freeze /
    unit-test fixtures with historical timestamps still qualify.
    """
    if not bars:
        return False
    cutoff = _rth_730_cutoff_utc(bars)
    if cutoff is None:
        return len(bars) >= _inject_min_bars()
    for b in reversed(bars):
        ts = _bar_ts_utc(b)
        if ts is None:
            continue
        if ts > cutoff:
            return True
    return False


def _iter_post_730_closes(bars: List[OHLCVBar]) -> List[float]:
    """≥07:30 PT 1m closes in bar order. Freeze bar counts; pre-07:30 does not."""
    aligned = _minute_aligned_structure_bars(bars)
    cutoff = _rth_730_cutoff_utc(aligned or bars)
    if cutoff is None:
        return []
    out: List[float] = []
    for b in aligned:
        ts = _bar_ts_utc(b)
        if ts is None or ts < cutoff:
            continue
        try:
            close_px = float(getattr(b, "close", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        if close_px > 0:
            out.append(close_px)
    return out


def _post_730_started_from_inside(
    bars: List[OHLCVBar],
    orb_high: float,
    orb_low: float,
    min_break_pct: float,
) -> bool:
    """True when a post-730 inside close printed *before* the first material outside.

    Names already through the box at freeze fail this — they must reclaim from
    the broken edge. A later nick after that outside print must not count as
    start-from-inside.
    """
    saw_inside = False
    for close_px in _iter_post_730_closes(bars):
        if _price_inside_orb2(close_px, orb_high, orb_low):
            saw_inside = True
        side = _extension_side_for_price(close_px, orb_high, orb_low, min_break_pct)
        if side is not None:
            return saw_inside
    return saw_inside


def _post_730_first_extension_side(
    bars: List[OHLCVBar],
    orb_high: float,
    orb_low: float,
    min_break_pct: float,
) -> Optional[str]:
    """First post-730 material-outside side, or None if never left the box."""
    for close_px in _iter_post_730_closes(bars):
        side = _extension_side_for_price(close_px, orb_high, orb_low, min_break_pct)
        if side is not None:
            return side
    return None


def _post_730_has_reclaim_from_side(
    bars: List[OHLCVBar],
    orb_high: float,
    orb_low: float,
    armed_side: Optional[str],
    reclaim_pct: float,
) -> bool:
    return any(
        _close_reclaimed_from_side(
            close_px, orb_high, orb_low, armed_side, reclaim_pct
        )
        for close_px in _iter_post_730_closes(bars)
    )


def _extension_inject_unlocked(system: Any) -> bool:
    """TL2+ inject waits until primary TL1 is done (or tests with no sequencer)."""
    if bool(getattr(system, "_trendline_discovery_hold_for_tl1", False)):
        return False
    if bool(getattr(system, "_trendline_watch_tl0_only", False)):
        return False
    completed = getattr(system, "_trendline_tl1_primary_completed_today", None)
    has_tl1 = getattr(system, "_trendline_engine_has_tl1_candidates", None)
    if not callable(completed) and not callable(has_tl1):
        return True
    try:
        if callable(completed) and completed():
            return True
    except Exception:
        pass
    try:
        if callable(has_tl1) and has_tl1():
            return True
    except Exception:
        pass
    return False


def _seed_seen_inside_post_730(
    st: Dict[str, Any],
    bars: List[OHLCVBar],
    orb_high: float,
    orb_low: float,
    *,
    reclaim_pct: float,
    min_break_pct: float,
) -> Optional[str]:
    """Stamp start-from-inside / already-reclaimed. Returns the arm reason or None."""
    if st.get("seen_inside_post_730"):
        return None
    # Already-extended names must reclaim at the TL3+ floor — a later H/L nick
    # in the Yahoo cache must not count as "started from inside."
    if st.get("await_reclaim_before_tl2"):
        return None
    if _post_730_started_from_inside(bars, orb_high, orb_low, min_break_pct):
        st["seen_inside_post_730"] = True
        st["await_reclaim_before_tl2"] = False
        return "started_inside"
    last_side = st.get("armed_side") or _post_730_first_extension_side(
        bars, orb_high, orb_low, min_break_pct
    )
    if not last_side:
        return None
    if _post_730_has_reclaim_from_side(
        bars, orb_high, orb_low, last_side, reclaim_pct
    ):
        st["seen_inside_post_730"] = True
        st["await_reclaim_before_tl2"] = False
        st["armed_side"] = last_side
        return "reclaimed"
    return None


def _log_await_reclaim_before_tl2(
    symbol: str,
    st: Dict[str, Any],
    *,
    side: str,
    px: float,
    oh_f: float,
    ol_f: float,
    reclaim_pct: float,
) -> None:
    prev = st.get("_last_skip_tl2_px")
    if prev is not None and abs(float(prev) - float(px)) <= 1e-6:
        return
    st["_last_skip_tl2_px"] = float(px)
    log.info(
        "TRENDLINE_ORB2_ENVELOPE | symbol=%s | action=skip | "
        "reason=await_reclaim_inside_orb2_before_tl2 | side=%s | "
        "price=%.4f | orb_high=%.4f | orb_low=%.4f | need_pct=%.6f | "
        "note=tl1_owns_already_extended_at_730",
        symbol,
        side,
        px,
        oh_f,
        ol_f,
        reclaim_pct,
    )


def _arm_inside_orb2_for_tl2(
    symbol: str,
    st: Dict[str, Any],
    *,
    px: float,
    check_px: float,
    oh_f: float,
    ol_f: float,
    reclaim_pct: float,
) -> None:
    st["await_reclaim_before_tl2"] = False
    st["seen_inside_post_730"] = True
    st.pop("_last_skip_clear_close", None)
    st.pop("_last_skip_tl2_px", None)
    inward = _inward_pct_from_broken_edge(
        check_px, oh_f, ol_f, st.get("armed_side")
    )
    log.info(
        "TRENDLINE_ORB2_ENVELOPE | symbol=%s | action=armed_inside_orb2_for_tl2 | "
        "reason=inside_orb2_1m_close | break_count=%d | last_side=%s | "
        "price=%.4f | close=%.4f | inward_pct=%.6f | need_pct=%.6f",
        symbol,
        int(st.get("break_count") or 0),
        st.get("armed_side"),
        px,
        check_px,
        float(inward) if inward is not None else -1.0,
        reclaim_pct,
    )


def pending_orb2_inject_symbols(system: Any) -> List[str]:
    """Symbols that would inject TL2–TL10 on this tick (for forced Yahoo hydrate)."""
    engine = getattr(system, "trendline_signal_engine", None)
    if engine is None:
        return []
    cfg = getattr(engine, "config", None)
    if cfg is not None and not bool(getattr(cfg, "orb2_extension_envelope_enabled", True)):
        return []
    levels = _orb2_levels(system)
    if not levels:
        return []
    if not _extension_inject_unlocked(system):
        return []
    min_break = _min_break_distance_pct(system)
    reclaim_pct = _reclaim_inside_pct(system)
    out: List[str] = []
    for symbol in sorted({str(s).upper() for s in levels.keys()}):
        try:
            oh, ol = levels[symbol]
            oh_f, ol_f = float(oh), float(ol)
        except Exception:
            continue
        if oh_f <= 0 or ol_f <= 0 or oh_f <= ol_f:
            continue
        bars = _bars_from_cache(system, symbol)
        px = _last_price(system, symbol, bars)
        if px <= 0:
            continue
        st = _sym_state(system, symbol)
        _seed_seen_inside_post_730(
            st,
            bars,
            oh_f,
            ol_f,
            reclaim_pct=reclaim_pct,
            min_break_pct=min_break,
        )
        side = _extension_side_for_price(px, oh_f, ol_f, min_break)
        if side is None:
            continue
        if not st.get("tl2_built") and not st.get("seen_inside_post_730"):
            continue
        if _next_extension_spec(st, side) is not None:
            out.append(symbol)
    return out


def _synthesize_bars_for_orb2_inject(system: Any, symbol: str) -> List[OHLCVBar]:
    """Last-resort RTH skeleton when engine cache is still empty after hydrate.

    Do **not** bake ORB2 high/low into every bar's high/low — that makes envelope
    A2 impossible (bear needs high < ORB2 high; bull needs low > ORB2 low).
    Prefer real Yahoo/broker path bars from watch hydrate; this only supplies a
    sparse open→now trail around traded prices.
    """
    try:
        from zoneinfo import ZoneInfo

        pt = ZoneInfo("America/Los_Angeles")
        now = datetime.now(timezone.utc).astimezone(pt)
        day = now.date()
        t630 = datetime(day.year, day.month, day.day, 6, 30, tzinfo=pt)
        t700 = datetime(day.year, day.month, day.day, 7, 0, tzinfo=pt)
        t730 = datetime(day.year, day.month, day.day, 7, 30, tzinfo=pt)
    except Exception:
        return []

    oh = ol = op = cl = 0.0
    try:
        mgr = getattr(system, "orb_strategy_manager", None)
        row = None
        if mgr is not None and isinstance(getattr(mgr, "orb2_data", None), dict):
            row = mgr.orb2_data.get(symbol) or mgr.orb2_data.get(symbol.upper())
        if row is None and isinstance(getattr(system, "_orb2_data", None), dict):
            row = system._orb2_data.get(symbol)
        if row is not None:
            if isinstance(row, dict):
                oh = float(row.get("orb_high") or 0.0)
                ol = float(row.get("orb_low") or 0.0)
                op = float(row.get("orb_open") or 0.0)
                cl = float(row.get("orb_close") or 0.0)
            else:
                oh = float(getattr(row, "orb_high", 0.0) or 0.0)
                ol = float(getattr(row, "orb_low", 0.0) or 0.0)
                op = float(getattr(row, "orb_open", 0.0) or 0.0)
                cl = float(getattr(row, "orb_close", 0.0) or 0.0)
    except Exception:
        return []
    if oh <= 0 or ol <= 0 or oh < ol:
        return []

    px = _last_price(system, symbol, [])
    if px <= 0:
        px = cl if cl > 0 else (oh + ol) / 2.0
    if op <= 0:
        op = (oh + ol) / 2.0
    if cl <= 0:
        cl = px
    mid = (oh + ol) / 2.0

    def _pt_bar(ts: datetime, o: float, h: float, l: float, c: float) -> OHLCVBar:
        hi = max(float(h), float(o), float(c))
        lo = min(float(l), float(o), float(c))
        # Keep path local — never expand to full ORB2 box on every stamp.
        return OHLCVBar(
            ts=ts.astimezone(timezone.utc),
            open=float(o),
            high=hi,
            low=lo,
            close=float(c),
            volume=0.0,
            atr=None,
        )

    # Sparse trail: open → mid → 7:30 close → live (no ORB H/L embedded).
    bars = [
        _pt_bar(t630, op, op, op, op),
        _pt_bar(t700, mid, mid, mid, mid),
        _pt_bar(t730, cl, cl, cl, cl),
    ]
    if now > t730:
        bars.append(_pt_bar(now, px, px, px, px))
    return bars


def _orb2_context(system: Any, symbol: str, oh_f: float, ol_f: float) -> Dict[str, Any]:
    orb2_ctx: Dict[str, Any] = {
        "orb_high": oh_f,
        "orb_low": ol_f,
        "orb_high_ts": None,
        "orb_low_ts": None,
    }
    try:
        mgr = getattr(system, "orb_strategy_manager", None)
        row = None
        if mgr is not None and isinstance(getattr(mgr, "orb2_data", None), dict):
            row = mgr.orb2_data.get(symbol) or mgr.orb2_data.get(symbol.upper())
        if row is None and isinstance(getattr(system, "_orb2_data", None), dict):
            row = system._orb2_data.get(symbol)
        if row is not None:
            if isinstance(row, dict):
                orb2_ctx["orb_high_ts"] = (
                    row.get("orb_high_extreme_ts")
                    or row.get("orb_high_ts")
                    or row.get("high_ts")
                )
                orb2_ctx["orb_low_ts"] = (
                    row.get("orb_low_extreme_ts")
                    or row.get("orb_low_ts")
                    or row.get("low_ts")
                )
            else:
                orb2_ctx["orb_high_ts"] = getattr(row, "orb_high_extreme_ts", None) or getattr(
                    row, "orb_high_ts", None
                )
                orb2_ctx["orb_low_ts"] = getattr(row, "orb_low_extreme_ts", None) or getattr(
                    row, "orb_low_ts", None
                )
        # Session-OHLC ORB2 without extremes: window open, not capture_time.
        if orb2_ctx["orb_high_ts"] is None or orb2_ctx["orb_low_ts"] is None:
            from zoneinfo import ZoneInfo

            pt = ZoneInfo("America/Los_Angeles")
            now = datetime.now(timezone.utc).astimezone(pt)
            win_open = now.replace(hour=6, minute=30, second=0, microsecond=0)
            if orb2_ctx["orb_high_ts"] is None:
                orb2_ctx["orb_high_ts"] = win_open
            if orb2_ctx["orb_low_ts"] is None:
                orb2_ctx["orb_low_ts"] = win_open
    except Exception:
        pass
    return orb2_ctx


def _direction_for_side(side: str) -> Tuple[TrendlineDirection, TrendlineSetupType, str, str]:
    if side == EXT_BELOW:
        return (
            TrendlineDirection.BEAR,
            TrendlineSetupType.DESCENDING_RESISTANCE,
            "call",
            "breakout_up",
        )
    return (
        TrendlineDirection.BULL,
        TrendlineSetupType.ASCENDING_SUPPORT,
        "put",
        "breakdown_down",
    )


def _inject_envelope(
    system: Any,
    *,
    symbol: str,
    bars: List[OHLCVBar],
    orb2_ctx: Dict[str, Any],
    side: str,
    role: str,
    slot: str,
    confirmed_at: datetime,
    oh_f: float,
    ol_f: float,
    price: float,
    break_index: int,
) -> Optional[str]:
    engine = getattr(system, "trendline_signal_engine", None)
    builder = getattr(engine, "builder", None) if engine is not None else None
    if engine is None or builder is None:
        return None

    tl_def = builder.build_orb2_extension_envelope_line(
        symbol,
        bars,
        orb2_ctx,
        side,
        confirmed_at=confirmed_at,
        envelope_role=role,
    )
    if tl_def is None:
        return None
    try:
        a1_px = float(tl_def.anchor_one.price)
        a2_px = float(tl_def.anchor_two.price)
    except Exception:
        a1_px = a2_px = 0.0
    if a1_px <= 0.0 or a2_px <= 0.0:
        log.info(
            "TRENDLINE_ORB2_ENVELOPE | symbol=%s | action=skip | reason=invalid_anchor_price | "
            "slot=%s | a1=%.4f | a2=%.4f",
            symbol,
            slot,
            a1_px,
            a2_px,
        )
        return None

    direction, setup_type, expected_side, trigger_direction = _direction_for_side(side)
    if tl_def.direction != direction:
        log.warning(
            "TRENDLINE_ORB2_ENVELOPE | symbol=%s | action=skip | reason=direction_mismatch | "
            "slot=%s | role=%s | expected=%s | got=%s",
            symbol,
            slot,
            role,
            direction.value,
            tl_def.direction.value,
        )
        return None

    # Shared already-broken telemetry (same helper as TL1 contest). Do not flip side —
    # keep correct-side envelope; Phase 2 decides emit vs chase block.
    try:
        from .trendline_geometry_selection import line_already_broken_at_cutoff

        if line_already_broken_at_cutoff(tl_def, confirmed_at, float(price)):
            log.info(
                "TRENDLINE_ORB2_ENVELOPE | symbol=%s | action=arm_already_broken_same_side | "
                "slot=%s | role=%s | direction=%s | option_side=%s | price=%.4f",
                symbol,
                slot,
                role,
                direction.value,
                expected_side,
                float(price),
            )
    except Exception:
        pass

    key = make_trendline_candidate_key(symbol, direction, slot)
    if key in (getattr(engine, "candidates", {}) or {}):
        return None

    now = datetime.now(timezone.utc)
    payload = {
        "signal_type": "TRENDLINE_ORB2_EXTENSION",
        "strategy_name": "easyTrendline_0DTE",
        "entry_type": "trendline_break",
        "strategy_type": "trendline_0dte",
        "source_path": "easyTrendline",
        "candidate_source": "orb2_material_extension",
        "trendline_slot": slot,
        "envelope_role": role,
        "orb2_break_index": int(break_index),
        "orb_box_source": "orb2_630_730",
        "orb_high": oh_f,
        "orb_low": ol_f,
        "extension_side": side,
        "setup_type": setup_type.value,
        "trigger_direction": trigger_direction,
        "expected_option_side": expected_side,
        "line_geometry": direction.value,
        "structure_selection_reason": "orb2_extension_envelope",
        "trendline_structure_source": "orb2_extension_envelope",
        "dual_lane_armed": False,
        "dual_lane_pair_id": "",
        "dual_lane_role": "",
    }
    cand = TrendlineCandidate(
        symbol=symbol,
        direction=direction,
        qualified_at=now,
        priority_score=0.0,
        confidence=0.0,
        trendline=tl_def,
        setup_payload=payload,
        state=TrendlineCandidateState.WAITING_FOR_BUILD,
    )
    injected_key = engine.add_runtime_candidate(cand, bars=bars, now_utc=now)
    if not injected_key:
        return None

    # Expire prior-slot geometry so discovery/entry uses this slot only.
    try:
        n_exp = int(
            engine.expire_prior_slots_for_symbol(
                symbol,
                keep_slot=slot,
                reason="superseded_by_orb2_extension",
            )
            or 0
        )
        if n_exp:
            log.warning(
                "TRENDLINE_ORB2_ENVELOPE | symbol=%s | action=expire_prior_slots | "
                "keep_slot=%s | expired=%d",
                symbol,
                slot,
                n_exp,
            )
    except Exception as exp_err:
        log.debug(
            "TRENDLINE_ORB2_ENVELOPE | symbol=%s | action=expire_prior_failed | err=%s",
            symbol,
            exp_err,
        )

    pending = getattr(system, "_pending_trendline_candidates", None)
    if isinstance(pending, list):
        # Drop superseded prior-slot candidates from pending watch list.
        try:
            from .trendline_models import TRENDLINE_SLOT_RANK, normalize_trendline_slot, trendline_slot_from_payload

            keep_r = TRENDLINE_SLOT_RANK.get(normalize_trendline_slot(slot), 9)
            kept_pending = []
            for p in pending:
                if str(getattr(p, "symbol", "") or "").strip().upper() != symbol:
                    kept_pending.append(p)
                    continue
                try:
                    ps = trendline_slot_from_payload(getattr(p, "setup_payload", None) or {})
                except Exception:
                    ps = "trendline1"
                pr = TRENDLINE_SLOT_RANK.get(normalize_trendline_slot(ps), -1)
                if pr >= 1 and pr < keep_r:
                    continue
                kept_pending.append(p)
            system._pending_trendline_candidates = kept_pending
        except Exception:
            pass
        pending = getattr(system, "_pending_trendline_candidates", None)
        if isinstance(pending, list):
            pending.append(cand)
    system._trendline_watch_active = True
    # Day-freeze TL2/TL3 A1/A2/slope + inject state; prune superseded lower slots.
    try:
        persist = getattr(system, "_persist_trendline_geometry_now", None)
        if callable(persist):
            persist(
                [cand],
                include_break_state=True,
                include_extreme_ts=True,
                metadata={"source": f"inject_{slot}", "symbol": symbol},
                prune_prior_slots_for_symbol=symbol,
                prune_keep_slot=slot,
            )
    except Exception:
        pass
    log.warning(
        "TRENDLINE_ORB2_ENVELOPE | symbol=%s | action=injected | slot=%s | role=%s | "
        "break_index=%d | side=%s | direction=%s | option_side=%s | price=%.4f | "
        "orb2_high=%.4f | orb2_low=%.4f | a1=%.4f | a2=%.4f | key=%s",
        symbol,
        slot,
        role,
        int(break_index),
        side,
        direction.value,
        expected_side,
        price,
        oh_f,
        ol_f,
        float(tl_def.anchor_one.price),
        float(tl_def.anchor_two.price),
        injected_key,
    )
    return injected_key


def maybe_inject_orb2_extension_trendlines(system: Any) -> int:
    """Inject TL2–TL10 from live price vs the frozen ORB2 box.

    Intentionally ignores BO/TL trade cooldowns. A rejected 7:45 PUT/CALL must
    not block Yahoo backfill or the next line (TL3+ after reclaim). Cooldowns
    only skip *entries* in watch/execute.
    """
    engine = getattr(system, "trendline_signal_engine", None)
    if engine is None:
        return 0
    cfg = getattr(engine, "config", None)
    if cfg is not None and not bool(getattr(cfg, "orb2_extension_envelope_enabled", True)):
        return 0
    if not _extension_inject_unlocked(system):
        return 0

    levels = _orb2_levels(system)
    if not levels:
        return 0

    min_break = _min_break_distance_pct(system)
    reclaim_pct = _reclaim_inside_pct(system)
    built = _built_map(system)
    injected = 0
    now = datetime.now(timezone.utc)

    for symbol in sorted({str(s).upper() for s in levels.keys()}):
        try:
            oh, ol = levels[symbol]
        except Exception:
            continue
        oh_f, ol_f = float(oh), float(ol)
        if oh_f <= 0 or ol_f <= 0 or oh_f <= ol_f:
            continue

        bars = _bars_for_symbol(system, symbol)
        px = _last_price(system, symbol, bars)
        if px <= 0:
            continue

        st = _sym_state(system, symbol)
        seeded = _seed_seen_inside_post_730(
            st,
            bars,
            oh_f,
            ol_f,
            reclaim_pct=reclaim_pct,
            min_break_pct=min_break,
        )
        side = _extension_side_for_price(px, oh_f, ol_f, min_break)

        # Same episode until a 1m close is ≥ reclaim_pct into the ORB2 box.
        # Pre-TL2: the same reclaim arms the first extension after an already-
        # extended 07:30 print (KTOS) so TL1 keeps the envelope until then.
        # A close already at that depth is the reclaim — do not require another
        # exit/re-enter. A nick inside the H/L is not.
        if side is None:
            close_px = _latest_minute_close(bars)
            check_px = close_px if close_px is not None else px
            if seeded == "reclaimed":
                _arm_inside_orb2_for_tl2(
                    symbol,
                    st,
                    px=px,
                    check_px=check_px,
                    oh_f=oh_f,
                    ol_f=ol_f,
                    reclaim_pct=reclaim_pct,
                )
                continue
            awaiting_tl2 = bool(st.get("await_reclaim_before_tl2")) and not st.get(
                "tl2_built"
            )
            last_ext_side = st.get("armed_side") or _post_730_first_extension_side(
                bars, oh_f, ol_f, min_break
            )
            if st.get("in_extension") or awaiting_tl2:
                if _reclaim_inside_orb2(
                    px,
                    oh_f,
                    ol_f,
                    bars,
                    armed_side=st.get("armed_side"),
                    reclaim_pct=reclaim_pct,
                ):
                    if st.get("in_extension"):
                        st["in_extension"] = False
                        st["cleared"] = True
                    if awaiting_tl2:
                        _arm_inside_orb2_for_tl2(
                            symbol,
                            st,
                            px=px,
                            check_px=check_px,
                            oh_f=oh_f,
                            ol_f=ol_f,
                            reclaim_pct=reclaim_pct,
                        )
                    else:
                        st.pop("_last_skip_clear_close", None)
                        st.pop("_last_skip_tl2_px", None)
                        inward = _inward_pct_from_broken_edge(
                            check_px, oh_f, ol_f, st.get("armed_side")
                        )
                        log.info(
                            "TRENDLINE_ORB2_ENVELOPE | symbol=%s | action=cleared_extension | "
                            "reason=inside_orb2_1m_close | break_count=%d | last_side=%s | "
                            "price=%.4f | close=%.4f | inward_pct=%.6f | need_pct=%.6f",
                            symbol,
                            int(st.get("break_count") or 0),
                            st.get("armed_side"),
                            px,
                            check_px,
                            float(inward) if inward is not None else -1.0,
                            reclaim_pct,
                        )
                elif _price_inside_orb2(check_px, oh_f, ol_f):
                    prev = st.get("_last_skip_clear_close")
                    if prev is None or abs(float(prev) - float(check_px)) > 1e-6:
                        st["_last_skip_clear_close"] = float(check_px)
                        inward = _inward_pct_from_broken_edge(
                            check_px, oh_f, ol_f, st.get("armed_side")
                        )
                        log.info(
                            "TRENDLINE_ORB2_ENVELOPE | symbol=%s | action=skip_clear | "
                            "reason=inside_orb2_lt_reclaim_pct | break_count=%d | "
                            "last_side=%s | close=%.4f | orb_high=%.4f | orb_low=%.4f | "
                            "inward_pct=%.6f | need_pct=%.6f",
                            symbol,
                            int(st.get("break_count") or 0),
                            st.get("armed_side"),
                            check_px,
                            oh_f,
                            ol_f,
                            float(inward) if inward is not None else -1.0,
                            reclaim_pct,
                        )
            elif not st.get("seen_inside_post_730") and _price_inside_orb2(
                check_px, oh_f, ol_f
            ):
                if last_ext_side:
                    st["armed_side"] = last_ext_side
                    if _close_reclaimed_from_side(
                        check_px, oh_f, ol_f, last_ext_side, reclaim_pct
                    ):
                        _arm_inside_orb2_for_tl2(
                            symbol,
                            st,
                            px=px,
                            check_px=check_px,
                            oh_f=oh_f,
                            ol_f=ol_f,
                            reclaim_pct=reclaim_pct,
                        )
                    else:
                        st["await_reclaim_before_tl2"] = True
                        prev = st.get("_last_skip_clear_close")
                        if prev is None or abs(float(prev) - float(check_px)) > 1e-6:
                            st["_last_skip_clear_close"] = float(check_px)
                            inward = _inward_pct_from_broken_edge(
                                check_px, oh_f, ol_f, last_ext_side
                            )
                            log.info(
                                "TRENDLINE_ORB2_ENVELOPE | symbol=%s | action=skip_clear | "
                                "reason=inside_orb2_lt_reclaim_pct | break_count=%d | "
                                "last_side=%s | close=%.4f | orb_high=%.4f | orb_low=%.4f | "
                                "inward_pct=%.6f | need_pct=%.6f",
                                symbol,
                                int(st.get("break_count") or 0),
                                last_ext_side,
                                check_px,
                                oh_f,
                                ol_f,
                                float(inward) if inward is not None else -1.0,
                                reclaim_pct,
                            )
                else:
                    st["seen_inside_post_730"] = True
            continue

        if not st.get("tl2_built") and not st.get("seen_inside_post_730"):
            st["await_reclaim_before_tl2"] = True
            st["armed_side"] = side
            _log_await_reclaim_before_tl2(
                symbol,
                st,
                side=side,
                px=px,
                oh_f=oh_f,
                ol_f=ol_f,
                reclaim_pct=reclaim_pct,
            )
            continue

        spec = _next_extension_spec(st, side)
        if spec is None:
            if st.get(f"tl{int(MAX_TRENDLINE_SLOT)}_built") and not _same_episode_hold(
                st, side
            ):
                log.info(
                    "TRENDLINE_ORB2_ENVELOPE | symbol=%s | action=skip | "
                    "reason=max_slot_reached | last_slot=trendline%d | "
                    "note=reclaim_break_had_no_next_line",
                    symbol,
                    int(MAX_TRENDLINE_SLOT),
                )
            continue
        slot, role, break_index = spec

        bars = _minute_aligned_structure_bars(_bars_from_cache(system, symbol))
        min_bars = _inject_min_bars()
        if len(bars) < min_bars or not _bars_cover_post_730(bars):
            log.info(
                "TRENDLINE_ORB2_ENVELOPE | symbol=%s | action=skip | reason=insufficient_yahoo_1m | "
                "bars=%d | min_bars=%d | post_730=%s | side=%s | slot=%s | hint=force_hydrate_before_inject",
                symbol,
                len(bars),
                min_bars,
                str(_bars_cover_post_730(bars)).lower(),
                side,
                slot,
            )
            continue

        confirmed_at = None
        try:
            aligned_n = []
            for b in bars:
                ts = getattr(b, "ts", None)
                if ts is None:
                    continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts.second == 0 and int(getattr(ts, "microsecond", 0) or 0) == 0:
                    aligned_n.append(ts)
            if aligned_n:
                confirmed_at = max(aligned_n)
        except Exception:
            confirmed_at = None
        if confirmed_at is None:
            log.info(
                "TRENDLINE_ORB2_ENVELOPE | symbol=%s | action=skip | "
                "reason=no_minute_aligned_confirm | side=%s | slot=%s",
                symbol,
                side,
                slot,
            )
            continue

        orb2_ctx = _orb2_context(system, symbol, oh_f, ol_f)
        key = _inject_envelope(
            system,
            symbol=symbol,
            bars=bars,
            orb2_ctx=orb2_ctx,
            side=side,
            role=role,
            slot=slot,
            confirmed_at=confirmed_at,
            oh_f=oh_f,
            ol_f=ol_f,
            price=px,
            break_index=break_index,
        )
        pending_key = _PENDING_BY_BREAK.get(int(break_index))
        built_key = _BUILT_BY_BREAK.get(int(break_index))
        if not built_key:
            continue
        if not key:
            if pending_key:
                st[pending_key] = True
                st["armed_side"] = side
                st["in_extension"] = True
                st["cleared"] = False
                log.info(
                    "TRENDLINE_ORB2_ENVELOPE | symbol=%s | action=%s | "
                    "reason=no_session_extreme_envelope_yet | side=%s | slot=%s",
                    symbol,
                    pending_key,
                    side,
                    slot,
                )
            continue

        st[built_key] = True
        if pending_key:
            st[pending_key] = False
        st["break_count"] = int(break_index)
        st["armed_side"] = side
        st["in_extension"] = True
        st["cleared"] = False
        built[f"{symbol}:break{int(break_index)}"] = side
        built[f"{symbol}:{slot}"] = slot
        injected += 1
        try:
            persist = getattr(system, "_persist_trendline_geometry_now", None)
            if callable(persist):
                persist(
                    include_break_state=True,
                    include_extreme_ts=False,
                    metadata={"source": f"inject_{slot}_state", "symbol": symbol},
                )
        except Exception:
            pass

    return injected
