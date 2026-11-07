#!/usr/bin/env python3
"""
ORB1 Breakout watch — BO-style rotating discovery on ORB#1 (06:30–06:45 H/L).

Aug 7 remodel: replace 7:15–7:30 signal collection + Step 5 one-shot with
event-driven discovery. Aug 12: discovery begins with TL0 right after ≥06:45
ORB1 capture → Yahoo 1m → TL0 inject (floor ``DISCOVERY_ORB1_TL0_START_PT`` in Shared.env;
gated on ``_trendline0_ready``). No new ORB1 Breakout entries after 07:25 cutoff.

Spine:
  - Quotes: ``run_unified_0dte_discovery_scan`` (main loop) → cache / forced window
  - Confirm: shared flat-box ``process_breakout_tick`` (ORB#1 levels)
  - Admit: ORB E16 QG including Layer C ``dir_pct`` (chain hydrate before admit)
  - Execute: ``system._execute_0dte_options_trades`` (shared ORB lotto spine)
"""

from __future__ import annotations

import logging
import os
from dataclasses import replace
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

log = logging.getLogger(__name__)

from modules.session_clock import (  # noqa: E402
    discovery_mode_is_watch,
    orb1_tl0_session_open as orb1_discovery_session_open,
    orb1_tl0_window_pt as orb1_discovery_window_pt,
    past_orb1_tl0_cutoff as past_orb1_entry_cutoff,
)


def validation_candle_enabled() -> bool:
    try:
        from modules.config_loader import get_config_value

        raw = get_config_value("ENABLE_VALIDATION_CANDLE", "false")
    except Exception:
        raw = os.getenv("ENABLE_VALIDATION_CANDLE", "false")
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def signal_collection_enabled() -> bool:
    try:
        from modules.config_loader import get_config_value

        raw = get_config_value("ENABLE_ORB_SIGNAL_COLLECTION", "false")
    except Exception:
        raw = os.getenv("ENABLE_ORB_SIGNAL_COLLECTION", "false")
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _orb1_same_path_blocked_symbols(system: Any) -> Set[str]:
    """Symbols ORB1 watch must not re-enter after a fill (one trade, 06:45–07:25).

    ``_orb1_0dte_executed_today`` is the in-flight fill lock. Opposite-side replay
    after a fill is not used — ORB1 does not re-enter the box. Reject still uses
    a 1h same-side cooldown and leaves the other H/L eligible until 07:25.
    """
    out: Set[str] = set()
    raw = getattr(system, "_orb1_0dte_executed_today", None)
    if isinstance(raw, (set, list, tuple)):
        out.update(str(s).upper() for s in raw if s)
    try:
        for pos in (getattr(system, "positions", None) or {}).values():
            psym = str(getattr(pos, "symbol", "") or "").upper()
            if not psym:
                continue
            st = str(getattr(pos, "strategy_type", "") or "").lower()
            md = getattr(pos, "metadata", None)
            src = ""
            if isinstance(md, dict):
                norm = md.get("normalized_options") if isinstance(md.get("normalized_options"), dict) else {}
                src = str(norm.get("source_path") or md.get("source_path") or "").lower()
                st = st or str(md.get("strategy_type") or "").lower()
            if (
                "breakout" in st
                or "trendline" in st
                or "breakout" in src
                or "trendline" in src
            ):
                continue
            out.add(psym)
    except Exception:
        pass
    try:
        dte0 = getattr(system, "dte0_manager", None)
        opt_ex = getattr(dte0, "options_executor", None) if dte0 else None
        mock = getattr(opt_ex, "mock_executor", None) if opt_ex else None
        for container in (
            getattr(opt_ex, "active_positions", None) if opt_ex else None,
            getattr(mock, "active_positions", None) if mock else None,
        ):
            if not isinstance(container, dict):
                continue
            for _tid, pos in container.items():
                if isinstance(pos, dict):
                    out.add(str(pos.get("symbol") or "").upper())
                else:
                    out.add(str(getattr(pos, "symbol", "") or "").upper())
    except Exception:
        pass
    out.discard("")
    return out


def reset_orb1_0dte_watch_daily_state(system: Any) -> None:
    """Clear ORB1 watch in-memory state on a new PT trading day."""
    system._orb1_0dte_watch_candidates = {}
    system._orb1_0dte_watch_active = False
    system._orb1_0dte_executed_today = set()
    system._orb1_0dte_side_reject_until = {}
    system._orb1_0dte_watch_rotate_idx = 0
    system._orb1_0dte_activated_pt_date = None
    system._orb1_0dte_pp_ready_pt_date = None
    system._orb_0dte_po_entry_features_by_symbol = {}


def _orb1_side_cooldown_key(symbol: str, side: str) -> str:
    return f"{str(symbol or '').strip().upper()}:{str(side or '').strip().lower()}"


def _orb1_normalize_side(side: Any) -> str:
    s = str(side or "").strip().lower()
    if s in ("c", "call", "long", "bullish", "bull"):
        return "call"
    if s in ("p", "put", "short", "bearish", "bear"):
        return "put"
    return s


def mark_orb1_side_cooldown(
    system: Any,
    symbol: str,
    side: Any,
    *,
    reason: str = "",
) -> Optional[datetime]:
    """Arm 1h ORB1 same-side cooldown (reject / post-exit parity with BO/TL)."""
    from modules.path_exec_day_exclude import (
        POST_EXIT_COOLDOWN_REASON,
        path_side_reject_cooldown_seconds,
    )

    sym = str(symbol or "").strip().upper()
    side_n = _orb1_normalize_side(side)
    if not sym or side_n not in ("call", "put"):
        return None
    cooldown_sec = path_side_reject_cooldown_seconds(system)
    now = datetime.now(timezone.utc)
    store = getattr(system, "_orb1_0dte_side_reject_until", None)
    if not isinstance(store, dict):
        store = {}
        system._orb1_0dte_side_reject_until = store
    key = _orb1_side_cooldown_key(sym, side_n)
    existing_dt = None
    raw_existing = store.get(key)
    if raw_existing is not None:
        try:
            if isinstance(raw_existing, datetime):
                existing_dt = (
                    raw_existing if raw_existing.tzinfo else raw_existing.replace(tzinfo=timezone.utc)
                )
            else:
                text = str(raw_existing).strip().replace("Z", "+00:00")
                existing_dt = datetime.fromisoformat(text)
                if existing_dt.tzinfo is None:
                    existing_dt = existing_dt.replace(tzinfo=timezone.utc)
        except Exception:
            existing_dt = None
    if existing_dt is not None and existing_dt > now:
        until = existing_dt
    else:
        until = now + timedelta(seconds=max(0.0, cooldown_sec))
        store[key] = until.isoformat()
    reason_s = str(reason or POST_EXIT_COOLDOWN_REASON).strip()[:120] or POST_EXIT_COOLDOWN_REASON
    log.warning(
        "ORB1_SIDE_COOLDOWN_BLOCK | symbol=%s | side=%s | reason=%s | "
        "cooldown_sec=%.0f | blocked_until=%s | action=block_side_keep_opposite_open",
        sym,
        side_n,
        reason_s,
        cooldown_sec,
        until.isoformat(),
    )
    return until


def is_orb1_side_on_cooldown(system: Any, symbol: str, side: Any) -> bool:
    """True while this ORB1 symbol+side is inside the 1h reject/exit cooldown."""
    sym = str(symbol or "").strip().upper()
    side_n = _orb1_normalize_side(side)
    if not sym or side_n not in ("call", "put"):
        return False
    store = getattr(system, "_orb1_0dte_side_reject_until", None)
    if not isinstance(store, dict):
        return False
    key = _orb1_side_cooldown_key(sym, side_n)
    raw = store.get(key)
    if raw is None:
        return False
    try:
        if isinstance(raw, datetime):
            until = raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
        else:
            text = str(raw).strip()
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            until = datetime.fromisoformat(text)
            if until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)
    except Exception:
        store.pop(key, None)
        return False
    now = datetime.now(timezone.utc)
    if until > now:
        return True
    store.pop(key, None)
    return False


def arm_orb1_side_cooldown_after_exit(
    system: Any,
    *,
    symbol: str,
    option_side: Optional[str] = None,
) -> bool:
    """After an ORB1 fill/exit: keep the one-trade lock. No re-entry before 07:25.

    Discovery ends at 07:25 with TL0. Same-side reject cooldown is separate.
    """
    until = mark_orb1_side_cooldown(
        system, symbol, option_side, reason="post_exit"
    )
    return until is not None


def morning_structure_ready_for_orb1(system: Any) -> bool:
    """True when ≥06:45 capture → Yahoo → TL0 inject chain is done (or TL off + ORB1 freeze)."""
    if getattr(system, "_enable_trendline_strategy", False):
        return bool(getattr(system, "_trendline0_ready", False))
    try:
        freeze_fn = getattr(system, "_orb1_memory_freeze_valid", None)
        if callable(freeze_fn):
            return bool(freeze_fn())
    except Exception:
        pass
    return bool(_orb1_levels_from_manager(system))


def expire_orb1_discovery_after_cutoff(
    system: Any,
    *,
    now_pt: Optional[time] = None,
    reason: str = "orb1_geometry_cutoff_0710",
) -> int:
    """
    Hard-stop ORB1 Breakout discovery + entries at 07:25 PT.

    Mirrors TL0 ``tl0_geometry_cutoff_0710``: deactivate watch, expire non-terminal
    candidates so READY/CONFIRM cannot submit after the morning window.
    """
    osm = getattr(system, "orb_strategy_manager", None)
    if now_pt is None:
        try:
            now_pt = osm._get_current_time_pt() if osm else None
        except Exception:
            now_pt = None
    if now_pt is None:
        try:
            from zoneinfo import ZoneInfo

            now_pt = datetime.now(ZoneInfo("America/Los_Angeles")).time()
        except Exception:
            return 0
    if not past_orb1_entry_cutoff(now_pt):
        return 0

    was_active = bool(getattr(system, "_orb1_0dte_watch_active", False))
    system._orb1_0dte_watch_active = False

    watch = getattr(system, "_orb1_0dte_watch_candidates", None) or {}
    if not watch:
        if was_active:
            log.info(
                "ORB1_0DTE_WATCH | stage=expired | reason=%s | expired=0 | "
                "note=no_orb1_trades_after_0710",
                reason,
            )
        return 0

    from easyBreakout.breakout_models import BreakoutCandidateState

    terminal = {
        BreakoutCandidateState.EXECUTED,
        BreakoutCandidateState.EXPIRED,
        BreakoutCandidateState.INVALIDATED,
    }
    expired_n = 0
    for cand in list(watch.values()):
        st = getattr(cand, "state", None)
        if st in terminal:
            continue
        try:
            cand.state = BreakoutCandidateState.EXPIRED
            meta = dict(getattr(cand, "metadata", None) or {})
            meta["expire_reason"] = reason
            cand.metadata = meta
            expired_n += 1
        except Exception:
            continue
    if expired_n or was_active:
        log.warning(
            "ORB1_0DTE_WATCH | stage=expired | action=orb1_geometry_cutoff | "
            "expired=%d | was_active=%s | reason=%s | note=no_orb1_trades_after_0710",
            expired_n,
            str(was_active).lower(),
            reason,
        )
    return expired_n


def is_orb1_watch_signal(signal: Any) -> bool:
    """True when a DTE0Signal originated from ORB1 morning breakout watch."""
    orb = getattr(signal, "orb_signal", None)
    if not isinstance(orb, dict):
        return False
    et = str(orb.get("entry_type") or "").strip().lower()
    md = orb.get("metadata") if isinstance(orb.get("metadata"), dict) else {}
    if not et:
        et = str(md.get("entry_type") or "").strip().lower()
    st = str(orb.get("setup_type") or "").strip().lower()
    return et == "orb1_watch_breakout" or st == "orb1_range_breakout"


def _orb1_confirm_cfg(system: Any):
    """BreakoutConfig clone with ORB1 Breakout cutoff / entry-window overrides."""
    from easyBreakout.breakout_config_loader import load_breakout_config_from_env
    from modules.config_loader import get_config_value

    base = load_breakout_config_from_env(get_config_value)
    _, end = orb1_discovery_window_pt()
    cutoff = f"{end.hour:02d}:{end.minute:02d}"
    try:
        window_sec = float(get_config_value("ORB_0DTE_SIMPLE_ENTRY_WINDOW_SEC", 300) or 300)
    except Exception:
        window_sec = 300.0
    try:
        from modules.discovery_watch_batch import discovery_batch_size

        per_tick = int(discovery_batch_size() or 24)
    except Exception:
        per_tick = 24
    return replace(
        base,
        no_new_entries_after_pt=cutoff,
        simple_entry_window_sec=window_sec,
        watch_symbols_per_tick=per_tick,
        # ORB E16 QG owns admit — BO quality scorer is observe-only for confirm path.
        volume_ratio_gate_enabled=False,
        require_vwap_alignment=False,
        pc_oi_confirmation_enabled=False,
        min_quality_score=0.0,
    )


def _orb1_levels_from_manager(system: Any) -> Dict[str, Tuple[float, float]]:
    osm = getattr(system, "orb_strategy_manager", None)
    if osm is None:
        return {}
    orb_data = getattr(osm, "orb_data", None) or {}
    out: Dict[str, Tuple[float, float]] = {}
    for sym, row in orb_data.items():
        try:
            if isinstance(row, dict):
                hi = float(row.get("high") or row.get("orb_high") or 0)
                lo = float(row.get("low") or row.get("orb_low") or 0)
            else:
                hi = float(getattr(row, "high", 0) or getattr(row, "orb_high", 0) or 0)
                lo = float(getattr(row, "low", 0) or getattr(row, "orb_low", 0) or 0)
            if hi > 0 and lo > 0 and hi >= lo:
                out[str(sym).strip().upper()] = (hi, lo)
        except Exception:
            continue
    return out


def resolve_orb1_universe(system: Any) -> List[str]:
    from modules.breakout_watch import resolve_breakout_universe

    return resolve_breakout_universe(system)


def activate_orb1_0dte_watch(system: Any) -> int:
    """Freeze ORB#1 watchlist once levels exist, window open, and morning structure ready."""
    if not getattr(system, "_enable_0dte_strategy", True):
        return 0
    if not discovery_mode_is_watch():
        return 0
    if getattr(system, "_orb1_0dte_watch_active", False) and (
        getattr(system, "_orb1_0dte_watch_candidates", None) or {}
    ):
        # Already armed. Return 0 so callers treat this as a no-op (do not re-log
        # SCHEDULER_ORDER activate_with_tl0 on every main-loop tick).
        return 0

    osm = getattr(system, "orb_strategy_manager", None)
    try:
        now_pt = osm._get_current_time_pt() if osm else None
    except Exception:
        now_pt = None
    if now_pt is None or not orb1_discovery_session_open(now_pt):
        return 0
    if past_orb1_entry_cutoff(now_pt):
        return 0
    if not morning_structure_ready_for_orb1(system):
        log.info(
            "ORB1_0DTE_WATCH | stage=activate | action=hold | "
            "reason=await_morning_structure_tl0_ready"
        )
        return 0

    # Prefer day-freeze markers over empty in-memory orb_data (cold start / restart).
    hydrated = 0
    try:
        hydrate_fn = getattr(system, "_hydrate_orb1_from_daily_markers", None)
        if callable(hydrate_fn):
            hydrated = int(hydrate_fn() or 0)
    except Exception as hydrate_err:
        log.warning(
            "ORB1_0DTE_WATCH | stage=activate | action=hydrate_failed | reason=%s",
            hydrate_err,
        )

    levels = _orb1_levels_from_manager(system)
    if not levels:
        log.info(
            "ORB1_0DTE_WATCH | stage=activate | action=skip | reason=orb1_levels_missing | "
            "hydrate_attempted=%s | hydrated=%d",
            str(callable(getattr(system, "_hydrate_orb1_from_daily_markers", None))).lower(),
            hydrated,
        )
        return 0

    from easyBreakout.breakout_models import BreakoutCandidate, BreakoutCandidateState

    universe = resolve_orb1_universe(system) or sorted(levels.keys())
    blocked = _orb1_same_path_blocked_symbols(system)
    watch: Dict[str, Any] = {}
    now = datetime.now(timezone.utc)
    missing = 0
    for sym in universe:
        if sym in blocked:
            continue
        hl = levels.get(sym)
        if not hl:
            missing += 1
            continue
        hi, lo = float(hl[0]), float(hl[1])
        watch[sym] = BreakoutCandidate(
            symbol=sym,
            orb_high=hi,
            orb_low=lo,
            seeded_at=now,
            price_at_seed=lo,
            state=BreakoutCandidateState.WAITING_FOR_BREAK,
            last_price=None,
            metadata={
                "box_source": "orb1_630_645",
                "orb_label": "ORB#1",
                "window_pt": "06:30-06:45",
                "source_path": "orb_0dte",
                "strategy_type": "orb_0dte",
            },
        )

    system._orb1_0dte_watch_candidates = watch
    system._orb1_0dte_watch_active = bool(watch)
    system._orb1_0dte_watch_rotate_idx = 0
    system._orb1_0dte_activated_pt_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # Overlay CONFIRM/READY from day marker after freeze stamp (activate replaces memory).
    try:
        from modules.discovery_watch_fsm_persist import hydrate_watch_fsm_from_markers

        hydrate_watch_fsm_from_markers(system)
    except Exception:
        pass
    log.info(
        "ORB1_0DTE_WATCH | stage=activate | watch_count=%d | universe=%d | "
        "levels=%d | missing_levels=%d | blocked=%d | orb_label=ORB#1 | active=%s",
        len(watch),
        len(universe),
        len(levels),
        missing,
        len(blocked),
        str(bool(watch)).lower(),
    )
    return len(getattr(system, "_orb1_0dte_watch_candidates", None) or watch)


def select_orb1_0dte_watch_batch(
    system: Any,
    *,
    discovery_cap_override: Optional[int] = None,
    advance_rotation: bool = True,
) -> List[Any]:
    from easyBreakout.breakout_models import BreakoutCandidateState
    from modules.discovery_watch_batch import (
        select_tier_rotated_batch,
        take_forced_discovery_symbols,
    )

    watch: Dict[str, Any] = getattr(system, "_orb1_0dte_watch_candidates", None) or {}
    blocked = _orb1_same_path_blocked_symbols(system)
    active = [
        c
        for c in watch.values()
        if c.state
        in (
            BreakoutCandidateState.WAITING_FOR_BREAK,
            BreakoutCandidateState.WAITING_FOR_CONFIRMATION,
            BreakoutCandidateState.READY_TO_EXECUTE,
        )
        and c.symbol not in blocked
    ]
    if not active:
        return []

    cfg = _orb1_confirm_cfg(system)
    per_tick = int(
        discovery_cap_override
        if discovery_cap_override is not None
        else cfg.watch_symbols_per_tick or 24
    )
    forced = take_forced_discovery_symbols(system)
    offset = int(getattr(system, "_orb1_0dte_watch_rotate_idx", 0) or 0)
    batch, new_offset, meta = select_tier_rotated_batch(
        active,
        per_tick=per_tick,
        offset=offset,
        forced_symbols=forced,
    )
    if advance_rotation and meta.get("mode") in ("tier_rotate", "path_fallback_rotate"):
        system._orb1_0dte_watch_rotate_idx = int(new_offset)
    if advance_rotation and len(active) > len(batch):
        log.info(
            "ORB1_0DTE_WATCH | stage=watch_batch | total=%d | this_tick=%d | "
            "mode=%s | offset=%d | next_offset=%d | cap=%d",
            len(active),
            len(batch),
            meta.get("mode"),
            int(meta.get("offset", offset) or offset),
            int(meta.get("new_offset", new_offset) or new_offset),
            per_tick,
        )
    return list(batch)


def _quote_price(quote: Any) -> float:
    if not isinstance(quote, dict):
        return 0.0
    for k in ("last", "price", "lastPrice", "close", "mark"):
        try:
            v = float(quote.get(k) or 0)
            if v > 0:
                return v
        except Exception:
            continue
    return 0.0


def _rearm_wait_break(cand: Any, *, reason: str, system: Any = None) -> None:
    from easyBreakout.breakout_models import BreakoutCandidateState

    cand.state = BreakoutCandidateState.WAITING_FOR_BREAK
    cand.option_side = None
    cand.break_price = None
    cand.break_ts = None
    meta = dict(getattr(cand, "metadata", None) or {})
    meta["rearm_reason"] = reason
    meta.pop("entry_window_at", None)
    meta.pop("exec_ready_at", None)
    cand.metadata = meta


def _orb_range_pct(cand: Any) -> Optional[float]:
    hi = float(getattr(cand, "orb_high", 0) or 0)
    lo = float(getattr(cand, "orb_low", 0) or 0)
    mid = (hi + lo) / 2.0 if hi > 0 and lo > 0 else 0.0
    if mid <= 0:
        return None
    return abs(hi - lo) / mid * 100.0


async def orb1_decision_chain(
    system: Any,
    chain_mgr: Any,
    skew_row: Dict[str, Any],
    tech: Dict[str, Any],
    meta: Optional[Dict[str, Any]],
    *,
    symbol: str,
    price: float,
) -> Dict[str, Any]:
    """Oct 7 2026 (A2/A3): ORB1 decision chain, age-checked like TL/BO.

    The cached chain is reused only when <= TP_FREEZE_CHAIN_MAX_AGE_SEC; an
    older cache gets ONE budgeted, per-symbol-deduped live refresh. Every
    chain-derived key (vol/OI/ratios, iv/delta/otm, greeks, spread) is
    recomputed by enrich (never carried from discovery), then copied to tech.
    """
    from central_tech import decision_freeze as dfz
    from easy0DTE.modules.convex_chain_skew import (
        CHAIN_SKEW_FIELDS,
        enrich_signal_with_chain_skew,
    )

    dfz.clear_keys(tech, dfz.CHAIN_REFRESH_KEYS)
    dfz.clear_keys(skew_row, dfz.CHAIN_REFRESH_KEYS)
    sym = str(symbol or "").upper()
    ttl = float(getattr(chain_mgr, "cache_ttl", 300) or 300)
    plan = dfz.plan_chain_refresh(chain_mgr, sym, spot=float(price), legacy_reuse_sec=ttl)
    force_live = False
    deferred = False
    if plan.get("reason") == "tp_age_cap":
        bud = dfz.get_refresh_budget(system)
        async with bud.lock("chain", sym):
            re_plan = dfz.plan_chain_refresh(chain_mgr, sym, spot=float(price), legacy_reuse_sec=ttl)
            if re_plan.get("action") == "cache":
                bud.note_dedupe("chain")
                plan = dict(re_plan, reason="deduped")
            else:
                plan = dfz.acquire_chain_refresh(system, sym, plan, log_tag="ORB1_0DTE_WATCH")
            if plan.get("action") == "defer":
                deferred = True
            else:
                force_live = plan.get("action") == "live"
                await enrich_signal_with_chain_skew(
                    chain_mgr, skew_row, system=system, force_live=force_live
                )
    elif plan.get("action") == "live":
        force_live = True
        await enrich_signal_with_chain_skew(
            chain_mgr, skew_row, system=system, force_live=True
        )
    else:
        await enrich_signal_with_chain_skew(chain_mgr, skew_row, system=system)
    for k in tuple(CHAIN_SKEW_FIELDS) + tuple(dfz.CHAIN_REFRESH_KEYS) + (
        "tp_chain_age_sec",
        "tp_chain_observed_epoch",
    ):
        if skew_row.get(k) is not None:
            tech[k] = skew_row[k]
            if isinstance(meta, dict):
                meta[k] = skew_row[k]
    log.info(
        "ORB1_0DTE_WATCH | stage=tp_freeze_chain | symbol=%s | action=%s | reason=%s | "
        "chain_age_before=%s | iv=%s | delta=%s | otm_pct=%s | grep=TP_FREEZE_REFRESH",
        sym,
        "defer" if deferred else ("live" if force_live else plan.get("action")),
        plan.get("reason"),
        plan.get("age_sec"),
        tech.get("iv"),
        tech.get("delta"),
        tech.get("otm_pct"),
    )
    return {"plan": plan, "force_live": force_live, "deferred": deferred}


def _build_dte0_signal(system: Any, cand: Any, *, price: float, tech: Dict[str, Any], side: str):
    from easy0DTE.modules.convex_eligibility_filter import ConvexEligibilityResult
    from easy0DTE.modules.prime_0dte_strategy_manager import DTE0Signal
    from easy0DTE.modules.quality_gate_stack import (
        PATH_ORB,
        GateContext,
        run_chain_skew_stack,
        shadow_log_report,
        _PATH_SS_FEATURE_KEYS,
    )

    side_u = "CALL" if side.lower() in ("call", "c", "long") else "PUT"
    direction = "LONG" if side_u == "CALL" else "SHORT"
    orb_range = _orb_range_pct(cand)
    dir_pct = tech.get("direction_volume_pct")
    vol = tech.get("volume_ratio")
    try:
        vol_f = float(vol) if vol is not None else float(getattr(cand, "volume_ratio", 0) or 0) or None
    except Exception:
        vol_f = None

    def _f(key: str) -> Optional[float]:
        raw = tech.get(key)
        if raw is None:
            return None
        try:
            return float(raw)
        except (TypeError, ValueError):
            return None

    ctx = GateContext(
        path=PATH_ORB,
        symbol=str(cand.symbol).upper(),
        side=side_u,
        signal=dict(tech or {}),
        equity_volume_ratio=vol_f,
        vwap_distance_pct=(
            float(tech["vwap_distance_pct"])
            if tech.get("vwap_distance_pct") is not None
            else getattr(cand, "vwap_distance_pct", None)
        ),
        vwap_position=str(tech.get("vwap_position") or "") or None,
        rsi=float(tech["rsi"]) if tech.get("rsi") is not None else getattr(cand, "rsi", None),
        macd_histogram=(
            float(tech["macd_histogram"]) if tech.get("macd_histogram") is not None else None
        ),
        ema_12=float(tech["ema_12"]) if tech.get("ema_12") is not None else None,
        ema_26=float(tech["ema_26"]) if tech.get("ema_26") is not None else None,
        quality_score=float(getattr(cand, "quality_score", 0) or 0) or None,
        geom_ready=True,
        geom_reason="orb1_break_confirmed",
        break_distance_pct=_f("break_distance_pct"),
        range_pct=orb_range,
        direction_volume_pct=float(dir_pct) if dir_pct is not None else None,
        put_call_oi_ratio=_f("put_call_oi_ratio"),
        call_chain_volume=_f("call_chain_volume"),
        put_chain_volume=_f("put_chain_volume"),
        extras={
            "entry_underlying": float(price),
            "orb_high": float(cand.orb_high),
            "orb_low": float(cand.orb_low),
            "orb_range_pct": orb_range,
            "source": "orb1_0dte_watch",
            "vwap_extension_reject": float(
                os.getenv("ORB_VWAP_EXTENSION_REJECT_PCT", "1.0") or "1.0"
            ),
            "vwap_extension_is_percent": True,
            "rsi_missing": bool(tech.get("rsi_missing")),
            "bollinger_position": tech.get("bollinger_position"),
            "session_vs_avg_volume": tech.get("session_vs_avg_volume")
            if tech.get("session_vs_avg_volume") is not None
            else tech.get("session_vs_avg"),
            "implied_volatility": tech.get("implied_volatility")
            if tech.get("implied_volatility") is not None
            else (tech.get("iv") if tech.get("iv") is not None else tech.get("representative_iv")),
            "iv": tech.get("iv") if tech.get("iv") is not None else tech.get("implied_volatility"),
            "seconds_since_break": tech.get("seconds_since_break"),
            "l1_delta_5s_with": tech.get("l1_delta_5s_with"),
            "volume_vs_prior_1m": tech.get("volume_vs_prior_1m"),
            "quality_score": float(getattr(cand, "quality_score", 0) or 0) or None,
        },
    )
    for key in _PATH_SS_FEATURE_KEYS:
        if ctx.extras.get(key) is None and tech.get(key) is not None:
            ctx.extras[key] = tech.get(key)
    report = run_chain_skew_stack(PATH_ORB, ctx)
    try:
        from easy0DTE.modules.quality_gate_stack import capture_profile_decision

        tech["po_profile_decision"] = capture_profile_decision(ctx, report)
    except Exception:
        pass
    # Oct 7 2026: the one decision freeze (gate == handoff snapshot).
    _fz = (ctx.extras or {}).get("po_decision_freeze")
    if isinstance(_fz, dict):
        tech["po_decision_freeze"] = _fz
    shadow_log_report(report, ctx=ctx)
    if not report.admitted:
        return None, report

    orb_signal = {
        "symbol": cand.symbol,
        "side": side_u,
        "direction": direction,
        "current_price": float(price),
        "underlying_price": float(price),
        "orb_high": float(cand.orb_high),
        "orb_low": float(cand.orb_low),
        "orb_range_pct": orb_range,
        "volume_ratio": vol_f,
        "direction_volume_pct": ctx.direction_volume_pct,
        "valid_bid_ask_contracts": tech.get("valid_bid_ask_contracts"),
        "put_call_oi_ratio": ctx.put_call_oi_ratio,
        "macd_histogram": ctx.macd_histogram,
        "rsi": ctx.rsi,
        "vwap_distance_pct": ctx.vwap_distance_pct,
        "vwap_position": ctx.vwap_position,
        "bollinger_position": tech.get("bollinger_position"),
        "atr": tech.get("atr"),
        "rsi_missing": bool(tech.get("rsi_missing")),
        "priority_score": float(getattr(cand, "quality_score", 0) or 0.5) / 100.0
        if float(getattr(cand, "quality_score", 0) or 0) > 1
        else float(getattr(cand, "quality_score", 0.5) or 0.5),
        "strategy_type": "orb_0dte",
        "source_path": "orb_0dte",
        "setup_type": "orb1_range_breakout",
        "entry_type": "orb1_watch_breakout",
        "quality_gate_report": report.to_dict() if hasattr(report, "to_dict") else None,
    }
    from easy0DTE.modules.trade_profile import copy_profile_fields

    copy_profile_fields(tech, orb_signal)
    if isinstance(tech.get("po_profile_decision"), dict):
        orb_signal["po_profile_decision"] = tech["po_profile_decision"]
    if isinstance(tech.get("po_decision_freeze"), dict):
        orb_signal["po_decision_freeze"] = tech["po_decision_freeze"]
    from modules.po_snapshot_hydration import carry_decision_moon_deltas

    carry_decision_moon_deltas(orb_signal, tech)
    # Prefer PS-like score from proximity / volume when available
    try:
        ps = float(orb_signal["priority_score"] or 0)
        if vol_f:
            ps = max(ps, min(0.95, 0.35 + 0.2 * min(vol_f, 2.0)))
        orb_signal["priority_score"] = ps
    except Exception:
        pass

    elig = ConvexEligibilityResult(
        signal=orb_signal,
        eligibility_score=1.0,
        is_eligible=True,
        eligibility_reasons=["orb1_watch_qg_admit"],
        rejection_reasons=[],
    )
    signal = DTE0Signal(
        symbol=str(cand.symbol).upper(),
        direction=direction,
        orb_signal=orb_signal,
        eligibility_result=elig,
        target_delta=0.35,
        spread_width=1.0,
        spread_type="single_leg",
        strategy_type="lotto",
        priority_score=float(orb_signal.get("priority_score") or 0.5),
        hard_gate_prevalidated=False,
    )
    return signal, report


async def run_orb1_0dte_watch_cycle(system: Any) -> None:
    """One ORB1 0DTE watch tick: rotate → confirm → QG → PO snap → execute."""
    if not discovery_mode_is_watch():
        return
    if not getattr(system, "_enable_0dte_strategy", True):
        return
    if not getattr(system, "_orb1_0dte_watch_active", False):
        return

    osm = getattr(system, "orb_strategy_manager", None)
    if osm is None:
        return
    try:
        pt_now = osm._get_current_time_pt()
    except Exception:
        return

    if past_orb1_entry_cutoff(pt_now):
        expire_orb1_discovery_after_cutoff(system, now_pt=pt_now)
        return
    if not orb1_discovery_session_open(pt_now):
        return

    cfg = _orb1_confirm_cfg(system)
    discovery_cap_override: Optional[int] = None
    try:
        from modules.discovery_watch_batch import discovery_batch_size

        discovery_cap_override = discovery_batch_size()
    except Exception:
        pass

    batch = select_orb1_0dte_watch_batch(
        system, discovery_cap_override=discovery_cap_override
    )
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
    except Exception:
        cached = None
    if not quotes:
        try:
            dm = getattr(system, "data_manager", None)
            if dm is not None and hasattr(dm, "get_batch_quotes"):
                quotes = await dm.get_batch_quotes(symbols, skip_cache=True) or {}
        except Exception as quote_err:
            log.warning("ORB1_0DTE_WATCH | stage=watch | quote_error=%s", quote_err)
            return
    if not quotes:
        return

    ingest = getattr(system, "_ingest_broker_quotes_into_session_tech", None)
    if callable(ingest):
        try:
            ingest(quotes)
        except Exception:
            pass

    enrich_breakout_tech = None
    try:
        from easyBreakout.breakout_session_tech import (
            enrich_breakout_tech as _enrich_breakout_tech,
            hydrate_breakout_session_bars,
        )

        await hydrate_breakout_session_bars(
            system,
            symbols,
            quotes=quotes if isinstance(quotes, dict) else None,
        )
        enrich_breakout_tech = _enrich_breakout_tech
    except Exception as hydrate_err:
        log.debug("ORB1_0DTE_WATCH | stage=hydrate | err=%s", hydrate_err)

    from easyBreakout.breakout_confirm_shell import process_breakout_tick
    from easyBreakout.breakout_models import BreakoutCandidateState

    now = datetime.now(timezone.utc)
    ready_signals = []
    blocked = _orb1_same_path_blocked_symbols(system)

    for cand in batch:
        if cand.symbol in blocked:
            continue
        quote = quotes.get(cand.symbol, {}) if isinstance(quotes, dict) else {}
        if not isinstance(quote, dict):
            quote = {}
        price = _quote_price(quote)
        if price <= 0:
            continue

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
            tech_stamp = {}
        try:
            if tech_stamp.get("volume_ratio") is not None:
                cand.volume_ratio = float(tech_stamp["volume_ratio"])
        except (TypeError, ValueError):
            pass
        for attr, key in (
            ("vwap", "vwap"),
            ("vwap_distance_pct", "vwap_distance_pct"),
            ("rsi", "rsi"),
        ):
            if tech_stamp.get(key) is not None:
                try:
                    setattr(cand, attr, float(tech_stamp[key]))
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
                "direction_volume_pct",
                "put_call_oi_ratio",
                "macd_histogram",
                "ema_12",
                "ema_26",
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
            continue

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
            if age_sec is not None and age_sec > window_sec:
                _rearm_wait_break(cand, reason="entry_window_expired", system=system)
                continue

        if not confirm.ready:
            continue

        side = str(confirm.option_side or cand.option_side or "").lower()
        if side not in ("call", "put"):
            continue

        if is_orb1_side_on_cooldown(system, cand.symbol, side):
            log.info(
                "ORB1_0DTE_WATCH | stage=side_cooldown_block | symbol=%s | side=%s | "
                "reason=reject_or_exit_cooldown_1h",
                cand.symbol,
                side,
            )
            _rearm_wait_break(cand, reason="side_cooldown_1h", system=system)
            continue

        # Oct 13 2026: open-position cap first (no API). Hold the same break with
        # its true reason; no chain pull, no dir%, no side cooldown.
        try:
            from modules.unified_0dte_portfolio_risk_manager import max_open_positions_precheck

            _cap_hit = max_open_positions_precheck(system, "orb_0dte")
        except Exception:
            _cap_hit = None
        if _cap_hit:
            log.info(
                "ORB1_0DTE_WATCH | stage=qg_reject | symbol=%s | side=%s | reason=max_open_positions_reached | "
                "cap_kind=%s | open_n=%s | cap=%s | action=hold_same_break | cooldown=none | "
                "grep=MAX_OPEN_POSITIONS_REACHED",
                cand.symbol,
                side,
                _cap_hit.get("cap_kind"),
                _cap_hit.get("open_n"),
                _cap_hit.get("cap"),
            )
            continue

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
            tech = dict(tech_stamp or {})
        # Merge chain direction stamps from metadata when hydrate lacks them
        meta = cand.metadata if isinstance(cand.metadata, dict) else {}
        for k in (
            "direction_volume_pct",
            "put_call_oi_ratio",
            "call_chain_volume",
            "put_chain_volume",
            "macd_histogram",
            "ema_12",
            "ema_26",
            "bollinger_position",
            "atr",
            "rsi",
            "rsi_missing",
            "vwap_position",
            "vwap_distance_pct",
            "volume_ratio",
        ):
            if tech.get(k) is None and meta.get(k) is not None:
                tech[k] = meta.get(k)
        try:
            tech["break_distance_pct"] = float(confirm.break_distance_pct)
        except (TypeError, ValueError):
            pass
        if side == "call":
            try:
                tech["break_level"] = float(cand.orb_high)
            except (TypeError, ValueError):
                pass
        elif side == "put":
            try:
                tech["break_level"] = float(cand.orb_low)
            except (TypeError, ValueError):
                pass

        # Oct 7 2026 decision freeze setup (see central_tech/decision_freeze.py).
        _dfz = None
        _orb_fz_on = False
        _orb_fz_chain_plan: Dict[str, Any] = {}
        _orb_fz_force_live = False
        _orb_fz_deferred = False
        _orb_fz_chain_mgr = None
        try:
            from central_tech import decision_freeze as _dfz

            _orb_fz_on = bool(_dfz.freeze_enabled()) and system is not None
        except Exception:
            _dfz = None
        # Layer C: hydrate chain vols + Path-SS micro once so admit_pre_exec
        # sees real stamps (same spine as TL/BO confirm_dir_pct_before_execution).
        # Always restamp from cache even when call/put vols are already present —
        # missing greeks/spread/totals would otherwise no-skip the 9.21 knives.
        try:
            from easy0DTE.modules.convex_chain_skew import (
                CHAIN_SKEW_FIELDS,
                enrich_signal_with_chain_skew,
                resolve_options_chain_manager,
                stamp_path_ss_chain_fields_from_cache,
            )

            side_u = "CALL" if side == "call" else "PUT"
            skew_row: Dict[str, Any] = {
                "symbol": str(cand.symbol).upper(),
                "side": side_u,
                "direction": side_u,
                "option_side": side,
                "option_type": side,
                "current_price": float(price),
                "price": float(price),
                "source_path": "orb_0dte",
            }
            chain_mgr = resolve_options_chain_manager(system)
            _orb_fz_chain_mgr = chain_mgr
            if _orb_fz_on and chain_mgr is not None:
                # A2/A3: ORB1 decision chain is age-checked (<= TP cap) and
                # every chain-derived key (vol/OI/ratios, iv/delta/otm, greeks,
                # spread, dir%) is recomputed from it, never carried.
                _fz_res = await orb1_decision_chain(
                    system,
                    chain_mgr,
                    skew_row,
                    tech,
                    meta if isinstance(meta, dict) else None,
                    symbol=str(cand.symbol).upper(),
                    price=float(price),
                )
                _orb_fz_chain_plan = _fz_res["plan"]
                _orb_fz_force_live = _fz_res["force_live"]
                _orb_fz_deferred = _fz_res["deferred"]
                cand.metadata = meta
            elif chain_mgr is not None:
                for k in CHAIN_SKEW_FIELDS:
                    if tech.get(k) is not None:
                        skew_row[k] = tech.get(k)
                if (
                    tech.get("call_chain_volume") is None
                    or tech.get("put_chain_volume") is None
                ):
                    await enrich_signal_with_chain_skew(
                        chain_mgr, skew_row, system=system
                    ) or {}
                else:
                    stamp_path_ss_chain_fields_from_cache(chain_mgr, skew_row)
                for k in CHAIN_SKEW_FIELDS:
                    if skew_row.get(k) is not None:
                        tech[k] = skew_row[k]
                        if isinstance(meta, dict):
                            meta[k] = skew_row[k]
                cand.metadata = meta
        except Exception as skew_err:
            log.warning(
                "ORB1_0DTE_WATCH | stage=layer_c_hydrate | symbol=%s | error=%s",
                cand.symbol,
                skew_err,
            )

        # Re-stamp ring from cached Layer C volumes even when fetch was skipped
        # so ORB CONFIRM names keep ≥60s priors into execute/handoff.
        # (Freeze on: enrich already recorded the sample at the chain's own clock.)
        if not _orb_fz_on and (
            tech.get("call_chain_volume") is not None or tech.get("put_chain_volume") is not None
        ):
            try:
                from central_tech.chain_ring import record_chain_skew_sample

                side_u = "CALL" if side == "call" else "PUT"
                record_chain_skew_sample(
                    system,
                    str(cand.symbol).upper(),
                    {
                        "side": side_u,
                        "direction": side_u,
                        "option_side": side,
                        "call_chain_volume": tech.get("call_chain_volume"),
                        "put_chain_volume": tech.get("put_chain_volume"),
                        "direction_volume_pct": tech.get("direction_volume_pct"),
                        "representative_volume": tech.get("representative_volume")
                        or meta.get("representative_volume"),
                        "cumulative_volume": tech.get("cumulative_volume")
                        or meta.get("cumulative_volume"),
                        "open_interest": tech.get("open_interest") or meta.get("open_interest"),
                    },
                )
            except Exception:
                pass

        # Stamp same-side dir% onto tech so GateContext + logs carry a real value.
        if tech.get("direction_volume_pct") is None and (
            tech.get("call_chain_volume") is not None
            or tech.get("put_chain_volume") is not None
        ):
            try:
                from easy0DTE.modules.convex_eligibility_filter import (
                    same_side_direction_volume_pct,
                )

                side_u = "CALL" if side == "call" else "PUT"
                dir_row = {
                    "side": side_u,
                    "call_chain_volume": tech.get("call_chain_volume"),
                    "put_chain_volume": tech.get("put_chain_volume"),
                }
                dp = same_side_direction_volume_pct(dir_row)
                if dp is not None:
                    tech["direction_volume_pct"] = float(dp)
                    if isinstance(meta, dict):
                        meta["direction_volume_pct"] = float(dp)
                        cand.metadata = meta
            except Exception:
                pass

        # Field readiness for ORB1-LS1 knives (soft-skip should be rare after hydrate).
        _gaps = []
        if tech.get("rsi") is None or bool(tech.get("rsi_missing")):
            _gaps.append("rsi")
        if tech.get("vwap_position") is None and tech.get("vwap_distance_pct") is None:
            _gaps.append("vwap")
        if tech.get("volume_ratio") is None or bool(tech.get("volume_ratio_missing")):
            _gaps.append("volume_ratio")
        if tech.get("call_chain_volume") is None or tech.get("put_chain_volume") is None:
            _gaps.append("chain_vol")
        if _gaps:
            log.info(
                "ORB1_QG_FIELD_GAP | symbol=%s | side=%s | missing=%s | "
                "tech_source=%s | grep=ORB1_QG_FIELD_GAP",
                cand.symbol,
                side,
                ",".join(_gaps),
                tech.get("tech_source"),
            )

        try:
            from modules.session_tech_admit import stamp_admit_tech as stamp_qg_admit_fields

            tech.setdefault("symbol", str(cand.symbol).upper())
            tech.setdefault("side", "CALL" if side == "call" else "PUT")
            tech.setdefault("current_price", float(price))
            tech.setdefault("underlying_price", float(price))
            try:
                if getattr(cand, "orb_high", None):
                    tech.setdefault("orb_high", float(cand.orb_high))
                if getattr(cand, "orb_low", None):
                    tech.setdefault("orb_low", float(cand.orb_low))
            except (TypeError, ValueError):
                pass
            # Always refresh break age (do not keep stale 0.0 from break-detect).
            brk = getattr(cand, "break_ts", None)
            if brk is not None:
                tech["break_timestamp"] = brk
                tech["break_ts"] = brk
                try:
                    b = brk if getattr(brk, "tzinfo", None) else brk.replace(tzinfo=timezone.utc)
                    tech["seconds_since_break"] = max(0.0, (now - b).total_seconds())
                except Exception:
                    pass
            _orb_fz_carry: Dict[str, Any] = {}
            _orb_fz_quote: Dict[str, Any] = {}
            if _orb_fz_on:
                _sym_u = str(cand.symbol).upper()
                try:
                    _orb_fz_quote = await _dfz.ensure_fresh_quote_sources(
                        system,
                        _sym_u,
                        sources=_dfz.cell_sources("orb_0dte", tech.get("side") or "CALL", None),
                        log_tag="ORB1_0DTE_WATCH",
                    )
                except Exception:
                    _orb_fz_quote = {}
                _orb_fz_carry = _dfz.clear_keys(
                    tech,
                    _dfz.SESSION_REFRESH_KEYS + _dfz.QUOTE_REFRESH_KEYS + _dfz.SPY_REFRESH_KEYS,
                )
                for _k in ("dir_pct_change_5m_prior_age_sec", "chain_volume_delta_5m_prior_age_sec"):
                    tech.pop(_k, None)
            stamp_qg_admit_fields(system, tech, symbol=str(cand.symbol).upper())
            if _orb_fz_on:
                _carry_used: Dict[str, Any] = {}
                _carry_age = tech.get("seconds_since_break")
                try:
                    _carry_age = float(_carry_age)
                except (TypeError, ValueError):
                    _carry_age = None
                for _k in _dfz.CARRY_RESTORABLE_KEYS:
                    if tech.get(_k) is None and _orb_fz_carry.get(_k) is not None:
                        if _carry_age is not None and _carry_age <= _dfz.session_tech_max_age_sec():
                            tech[_k] = _orb_fz_carry[_k]
                            _carry_used[_k] = _carry_age
                tech["tp_freeze_ctx"] = _dfz.collect_freeze_context(
                    system,
                    tech,
                    symbol=str(cand.symbol).upper(),
                    chain_mgr=_orb_fz_chain_mgr,
                    chain_plan=_orb_fz_chain_plan,
                    quote_refresh=_orb_fz_quote,
                    carry=_carry_used,
                )
            try:
                from modules.qg_admit_snapshot import collect_qg_admit_snapshot

                snap = collect_qg_admit_snapshot(tech)
                if snap:
                    if not isinstance(meta, dict):
                        meta = {}
                    meta["qg_admit"] = snap
                    cand.metadata = meta
            except Exception:
                pass
        except Exception:
            pass

        tech["decision_hydrated"] = True
        if tech.get("call_chain_volume") is None and tech.get("put_chain_volume") is None:
            if _orb_fz_on and _orb_fz_deferred:
                tech["chain_attempt"] = "skipped_tp_freeze_budget"
            elif _orb_fz_on and _orb_fz_chain_mgr is not None:
                tech["chain_attempt"] = "skipped_chain_unavailable"
            else:
                tech["chain_attempt"] = "empty"
        else:
            tech["chain_attempt"] = "fetched"
        signal, report = _build_dte0_signal(
            system, cand, price=price, tech=tech, side=side
        )
        tech.pop("tp_freeze_ctx", None)
        if (
            signal is None
            and report is not None
            and getattr(report, "deferred", False)
            and report.first_reject() is None
        ):
            log.info(
                "ORB1_0DTE_WATCH | stage=qg_not_ready | symbol=%s | side=%s | "
                "reason=required_data_not_ready | action=hold_same_break",
                cand.symbol,
                side,
            )
            continue
        if signal is None:
            first = None
            try:
                first = report.first_reject() if report is not None else None
            except Exception:
                first = None
            reason = None
            if first is not None:
                gid = getattr(first, "gate_id", None)
                rsn = getattr(first, "reason", None)
                if gid and rsn:
                    reason = f"gate_stack:{gid}:{rsn}"
                else:
                    reason = rsn or gid
            reason_tag = str(reason or "qg_reject")[:180]
            log.info(
                "ORB1_0DTE_WATCH | stage=qg_reject | symbol=%s | side=%s | reason=%s | "
                "action=side_cooldown_rearm_wait_break",
                cand.symbol,
                side,
                reason_tag,
            )
            # Persist path-SS reject-time admit features for later synthetic tuning.
            try:
                if "gate_stack:path_ss:" in str(reason_tag or ""):
                    from modules.path_ss_reject_snapshot_store import (
                        capture_path_ss_reject_snapshot,
                    )

                    meta = getattr(cand, "metadata", None)
                    qg_admit = (
                        meta.get("qg_admit")
                        if isinstance(meta, dict) and isinstance(meta.get("qg_admit"), dict)
                        else None
                    )
                    # Full tech row at reject — same fields stamped for GateContext /
                    # qg_admit — so reject snaps densify like PO entry payloads.
                    tech_row = dict(tech) if isinstance(tech, dict) else {}
                    if isinstance(meta, dict):
                        for _k, _v in meta.items():
                            if _k == "qg_admit":
                                continue
                            if _v is not None and tech_row.get(_k) is None:
                                tech_row[_k] = _v
                    side_lbl = "CALL" if side == "call" else "PUT"
                    capture_path_ss_reject_snapshot(
                        system,
                        source="ORB1_0DTE_WATCH",
                        symbol=str(getattr(cand, "symbol", "") or ""),
                        side=side_lbl,
                        reason_code=str(reason or reason_tag),
                        qg_admit=qg_admit,
                        tech_row=tech_row,
                        gate_report=report,
                        extra={"reason_tag": reason_tag},
                    )
            except Exception:
                pass
            # Hard QG reject: 1h same-side cooldown (parity with BO/TL + post-exec reject).
            if side in ("call", "put"):
                mark_orb1_side_cooldown(
                    system,
                    cand.symbol,
                    side,
                    reason=f"quality_reject:{reason_tag}",
                )
            _rearm_wait_break(cand, reason=f"qg_reject:{reason_tag}", system=system)
            # Desk Telegram — compact Reason matches 0DTEQualityGates §3.2.
            try:
                am = getattr(system, "alert_manager", None)
                if am is not None and hasattr(am, "send_strategy_options_execution_alert"):
                    try:
                        from modules.prime_alert_manager import PrimeAlertManager

                        reason_disp = PrimeAlertManager._execution_reject_display_code(
                            f"qg_reject:{reason_tag}"
                        )
                    except Exception:
                        reason_disp = reason_tag
                    mode_label = "DEMO"
                    try:
                        mode_label = (
                            "LIVE"
                            if str(getattr(system, "mode", "") or "").lower() == "live"
                            else "DEMO"
                        )
                    except Exception:
                        pass
                    await am.send_strategy_options_execution_alert(
                        "orb1",
                        executed_rows=[],
                        failed_rows=[
                            {
                                "symbol": str(cand.symbol or ""),
                                "option_side": str(side or "").upper(),
                                "setup_type": "orb1_range_breakout",
                                "trade_id": "n/a",
                                "diagnostic_stage": "quality_reject",
                                "failure_stage": "quality_reject",
                                "failure_reason": f"qg_reject:{reason_tag}",
                                "failure_reason_code": str(reason_disp)[:160],
                                "execution_attempted": True,
                                "status": "failed",
                            }
                        ],
                        mode=mode_label,
                    )
            except Exception as q_alert_err:
                log.debug(
                    "ORB1_0DTE_WATCH | stage=qg_reject_alert | symbol=%s | error=%s",
                    cand.symbol,
                    q_alert_err,
                )
            continue

        log.info(
            "ORB1_0DTE_WATCH | stage=ready | symbol=%s | side=%s | ps=%.3f | "
            "orb_high=%.4f | orb_low=%.4f | price=%.4f",
            cand.symbol,
            side,
            float(signal.priority_score or 0),
            float(cand.orb_high),
            float(cand.orb_low),
            price,
        )
        ready_signals.append(signal)
        cand.state = BreakoutCandidateState.READY_TO_EXECUTE

    if not ready_signals:
        try:
            from modules.discovery_watch_fsm_persist import sync_watch_fsm_after_cycle

            sync_watch_fsm_after_cycle(system)
        except Exception:
            pass
        return

    # Re-check cutoff before submit — no new entries after 07:25
    try:
        pt_now = osm._get_current_time_pt()
    except Exception:
        pt_now = None
    if pt_now is None or past_orb1_entry_cutoff(pt_now):
        expire_orb1_discovery_after_cutoff(system, now_pt=pt_now)
        log.info(
            "ORB1_0DTE_WATCH | stage=exec_skip | reason=past_cutoff | n_ready=%d",
            len(ready_signals),
        )
        try:
            from modules.discovery_watch_fsm_persist import sync_watch_fsm_after_cycle

            sync_watch_fsm_after_cycle(system)
        except Exception:
            pass
        return

    # Aug 7: PP + Red Day tier gate paused with Signal Collection
    # (ENABLE_PORTFOLIO_PROTECTION / ENABLE_RED_DAY_TIER_GATE=false). No SC pool
    # exists to evaluate Regime gates — do not invent a substitute pool @ 07:00.
    try:
        from modules.portfolio_protection import (
            portfolio_protection_enabled,
            red_day_tier_gate_enabled,
        )

        if not portfolio_protection_enabled():
            log.info(
                "ORB1_0DTE_WATCH | stage=pp | action=skip | reason=ENABLE_PORTFOLIO_PROTECTION=false"
            )
        if not red_day_tier_gate_enabled():
            log.info(
                "ORB1_0DTE_WATCH | stage=red_day_gate | action=skip | "
                "reason=ENABLE_RED_DAY_TIER_GATE=false_or_pp_off"
            )
        # Clear any stale flag so TL/BO later today also stay ungated while paused.
        if not red_day_tier_gate_enabled():
            system._red_day_filter_blocked = False
    except Exception:
        pass

    # Priority Optimizer: index admitted signals + handoff Priority Snapshots (entry)
    try:
        index_fn = getattr(system, "_profit_optimizer_index_dte0_collection", None)
        if callable(index_fn):
            index_fn(ready_signals)
    except Exception as idx_err:
        log.debug("ORB1_0DTE_WATCH | po_index_skip | error=%s", idx_err)

    snap_fn = getattr(system, "_save_orb_0dte_execution_priority_snapshot", None)
    if callable(snap_fn):
        for sig in ready_signals:
            try:
                cand = (getattr(system, "_orb1_0dte_watch_candidates", None) or {}).get(
                    str(sig.symbol).upper()
                )
                snap_fn(
                    signal=sig,
                    stage="orb_0dte_emit_handoff",
                    order_payload={},
                    broker_success=None,
                    candidate=cand,
                )
            except Exception as snap_err:
                log.debug(
                    "ORB1_0DTE_WATCH | handoff_snapshot_skip | symbol=%s | error=%s",
                    getattr(sig, "symbol", "?"),
                    snap_err,
                )

    exec_fn = getattr(system, "_execute_0dte_options_trades", None)
    if not callable(exec_fn):
        log.error("ORB1_0DTE_WATCH | stage=exec | error=missing__execute_0dte_options_trades")
        return

    log.info(
        "ORB1_0DTE_WATCH | stage=exec | action=submit | n=%d | symbols=%s",
        len(ready_signals),
        ",".join(s.symbol for s in ready_signals[:12]),
    )
    try:
        await exec_fn(ready_signals)
    except Exception as exec_err:
        log.error("ORB1_0DTE_WATCH | stage=exec | error=%s", exec_err)
        return

    # Day-exclude every submitted symbol (prevents double-submit on registry lag).
    # Prefer fill hygiene set when present for logging / EXECUTED state.
    filled = set(getattr(system, "_orb_0dte_filled_symbols_today", None) or set())
    try:
        for pos in (getattr(system, "positions", None) or {}).values():
            psym = str(getattr(pos, "symbol", "") or "").upper()
            if psym:
                filled.add(psym)
    except Exception:
        pass

    done = set(getattr(system, "_orb1_0dte_executed_today", None) or set())
    watch = getattr(system, "_orb1_0dte_watch_candidates", None) or {}
    from easyBreakout.breakout_models import BreakoutCandidateState

    for sig in ready_signals:
        sym = str(sig.symbol).upper()
        side = _orb1_normalize_side(
            getattr(sig, "option_type", None)
            or getattr(sig, "option_side", None)
            or getattr(sig, "option_type_label", None)
        )
        filled_ok = sym in filled
        cand = watch.get(sym)
        if cand is None:
            continue
        if filled_ok:
            # One ORB1 fill ends this symbol for the morning window (no re-entry).
            done.add(sym)
            cand.state = BreakoutCandidateState.EXECUTED
            meta = dict(getattr(cand, "metadata", None) or {})
            meta["exec_submitted"] = True
            meta["exec_filled"] = True
            cand.metadata = meta
        else:
            # Reject: 1h same-side cooldown. Opposite H/L can still fire until 07:25.
            if side in ("call", "put"):
                mark_orb1_side_cooldown(
                    system, sym, side, reason="execution_reject"
                )
            done.discard(sym)
            _rearm_wait_break(cand, reason="execution_reject_side_cooldown", system=system)
            meta = dict(getattr(cand, "metadata", None) or {})
            meta["exec_submitted"] = True
            meta["exec_filled"] = False
            cand.metadata = meta
    system._orb1_0dte_executed_today = done
    try:
        from modules.discovery_watch_fsm_persist import sync_watch_fsm_after_cycle

        sync_watch_fsm_after_cycle(system)
    except Exception:
        pass
