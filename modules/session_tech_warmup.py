"""Session-tech warm-up for the first ORB1 / TL0 gate (Oct 5 2026, Eric).

The 10.5 ORB1 and TL0 cells now reject when a warm-up key is missing
(``missing_reject_keys``: atr, price_volume_efficiency_3m, dir_pct_change_5m,
distance_from_vwap_atr, rsi). This module makes those keys present at the first
gate (~06:45 PT) with real observations only. Nothing here invents a value.

1. Prior-session bar warm-up (``seed_prior_session_warmup``)
   From 06:00 PT, one batched Yahoo 1m download (``period=2d``, RTH only,
   ``prepost=False``; the same Yahoo 1m source session tech already uses for
   RSI backfill) keeps the last ``SESSION_TECH_WARMUP_BARS`` (default 60) bars
   of the prior RTH session per watchlist symbol. They are stored apart from
   today's bar cache (``system._session_tech_warmup``) and are read only by the
   close-series indicators in ``compute_session_tech_from_bars`` (RSI-14,
   MACD 12/26/9, SMA-20/50, EMA-12/26, Bollinger-20, momentum-10), and only
   while today's closes are too few. VWAP, ATR, volume and bar structure stay
   today-only. E*TRADE has no intraday history API, so Yahoo is the source.

2. Today's 06:30-06:45 1m bars (``seed_today_bars_from_tl0_fetch``)
   The TL0 build already downloads Yahoo RTH 1m bars 06:30-06:45 for every
   ORB1 symbol before ORB1/TL0 discovery starts. Those bars are now merged into
   the shared session-tech cache (``bar_kind=minute``), so RSI-14 (15 closes),
   price_volume_efficiency_3m, ATR and VWAP have today's real minutes at the
   first gate.

3. Chain-ring pre-warm (``prewarm_chain_ring``)
   ``dir_pct_change_5m`` needs a distinct chain observation 4-7 min (strict) or
   3-10 min (fallback) older than the decision chain. At 06:40 PT (and at
   06:50 / 07:00 / 07:26 / 07:33 for symbols with no ring sample in the last
   4 min; t168u moved 06:36 / 06:53 so the 06:45-07:25 ORB1 / TL0 gates sit
   inside the window, and added 07:26 / 07:33 for the 07:30 TL1 + ORB2 build,
   whose lines do not exist before 07:30, so no approach can arm earlier) one live E*TRADE 0DTE chain per watchlist
   symbol is fetched and recorded in the central chain ring. The fetched chain
   is evicted from the chain manager cache so no decision reads it as "now",
   and the symbol is not added to the discovery warm-refresh list. Before
   06:30 the 0DTE chain has no session volume, so a pre-open sample cannot
   serve as the prior.

Switches: ``SESSION_TECH_WARMUP_PRIOR_SESSION`` (true), ``SESSION_TECH_WARMUP_BARS``
(60), ``SESSION_TECH_WARMUP_START_PT`` (06:00), ``SESSION_TECH_TL0_BAR_SEED`` (true),
``CHAIN_RING_PREWARM_ENABLED`` (true), ``CHAIN_RING_PREWARM_TIMES_PT``
(06:40,06:50,07:00,07:26,07:33 since t168u; was 06:36,06:53,07:00), ``CHAIN_RING_PREWARM_WINDOW_SEC`` (240),
``CHAIN_RING_PREWARM_CONCURRENCY`` (4).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, time as dt_time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence

log = logging.getLogger(__name__)

WARMUP_SOURCE = "yahoo_1m_prior_rth"
_PT = "America/Los_Angeles"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        val = int(float(os.getenv(name, str(default)) or default))
    except (TypeError, ValueError):
        val = default
    return max(lo, min(hi, val))


def _parse_hhmm(raw: str) -> Optional[dt_time]:
    try:
        hh, mm = str(raw).strip().split(":")[:2]
        return dt_time(int(hh), int(mm))
    except Exception:
        return None


def _now_pt(now: Optional[datetime] = None) -> datetime:
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(_PT)
    if now is None:
        return datetime.now(tz)
    if now.tzinfo is None:
        return now.replace(tzinfo=tz)
    return now.astimezone(tz)


def warmup_enabled() -> bool:
    return _env_bool("SESSION_TECH_WARMUP_PRIOR_SESSION", True)


def warmup_bar_count() -> int:
    # MACD 12/26/9 needs 34 closes, SMA-50 needs 50.
    return _env_int("SESSION_TECH_WARMUP_BARS", 60, 35, 240)


def tl0_bar_seed_enabled() -> bool:
    return _env_bool("SESSION_TECH_TL0_BAR_SEED", True)


def _store(system: Any) -> Dict[str, Any]:
    st = getattr(system, "_session_tech_warmup", None)
    if not isinstance(st, dict):
        st = {"date": "", "bars": {}, "source_date": {}, "attempts": 0, "last_attempt": 0.0}
        try:
            setattr(system, "_session_tech_warmup", st)
        except Exception:
            pass
    return st


def _roll_day(system: Any, today: str) -> Dict[str, Any]:
    st = _store(system)
    if st.get("date") != today:
        st.clear()
        st.update({"date": today, "bars": {}, "source_date": {}, "attempts": 0, "last_attempt": 0.0})
    return st


def warmup_closes(system: Any, symbol: str, *, now: Optional[datetime] = None) -> List[float]:
    """Prior-session closes (oldest first) seeded today for ``symbol``; [] otherwise."""
    if system is None or not warmup_enabled():
        return []
    st = getattr(system, "_session_tech_warmup", None)
    if not isinstance(st, dict):
        return []
    if st.get("date") != _now_pt(now).date().isoformat():
        return []
    bars = (st.get("bars") or {}).get(str(symbol or "").strip().upper()) or []
    out: List[float] = []
    for b in bars:
        try:
            c = float(b.get("close"))
        except (TypeError, ValueError, AttributeError):
            continue
        if c > 0:
            out.append(c)
    return out


def _last_prior_bars(bars: Sequence[Dict[str, Any]], today, n: int) -> tuple:
    """Keep the last ``n`` priced bars of the most recent PT date before ``today``."""
    dated = []
    for b in bars or ():
        if not isinstance(b, dict):
            continue
        ts = b.get("timestamp") or b.get("datetime")
        try:
            c = float(b.get("close") or 0.0)
        except (TypeError, ValueError):
            continue
        if c <= 0 or not hasattr(ts, "date"):
            continue
        d = _now_pt(ts).date()
        if d >= today:
            continue  # never use today's (or future) bars as warm-up history
        dated.append((d, ts, b))
    if not dated:
        return None, []
    src = max(d for d, _t, _b in dated)
    keep = sorted(((t, b) for d, t, b in dated if d == src), key=lambda x: _now_pt(x[0]))
    return src, [dict(b, bar_kind="warmup_prior") for _t, b in keep[-n:]]


async def _default_prior_fetch(symbols: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    from modules.prime_data_manager import _yf_batch_intraday_rth_bars_blocking
    from modules.session_clock import orb1_capture_window_pt

    return await asyncio.to_thread(
        _yf_batch_intraday_rth_bars_blocking,
        symbols,
        interval="1m",
        period="2d",
        rth_start=orb1_capture_window_pt()[0],
        rth_end_inclusive=dt_time(13, 0),
        batch_size=_env_int("SESSION_TECH_WARMUP_BATCH_SIZE", 20, 1, 50),
        inter_batch_sleep_sec=0.5,
        log_tag="SESSION_TECH_WARMUP_YF_1M",
        session="prior",
    )


async def seed_prior_session_warmup(
    system: Any,
    symbols: Sequence[str],
    *,
    fetcher: Optional[Callable[[List[str]], Awaitable[Dict[str, List[Dict[str, Any]]]]]] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Fetch and store prior-session 1m warm-up bars for symbols not yet seeded today."""
    now_pt = _now_pt(now)
    today = now_pt.date()
    st = _roll_day(system, today.isoformat())
    want = list(dict.fromkeys(str(s or "").strip().upper() for s in symbols or () if str(s or "").strip()))
    need = [s for s in want if not (st.get("bars") or {}).get(s)]
    summary: Dict[str, Any] = {"requested": len(want), "fetched": 0, "seeded": 0, "missing": []}
    if not warmup_enabled() or not need:
        summary["seeded_total"] = len(st.get("bars") or {})
        return summary
    st["attempts"] = int(st.get("attempts") or 0) + 1
    st["last_attempt"] = time.time()
    n = warmup_bar_count()
    try:
        fetched = await (fetcher or _default_prior_fetch)(need)
    except Exception as exc:
        log.warning("SESSION_TECH_WARMUP | stage=fetch_failed | n=%d | err=%s", len(need), exc)
        fetched = {}
    summary["fetched"] = len(fetched or {})
    seeded = 0
    src_dates = set()
    for sym in need:
        src, bars = _last_prior_bars((fetched or {}).get(sym) or [], today, n)
        if bars:
            st["bars"][sym] = bars
            st["source_date"][sym] = src.isoformat()
            src_dates.add(src.isoformat())
            seeded += 1
        else:
            summary["missing"].append(sym)
    summary["seeded"] = seeded
    summary["seeded_total"] = len(st.get("bars") or {})
    log.info(
        "SESSION_TECH_WARMUP | stage=seeded | source=%s | requested=%d | fetched=%d | seeded=%d | "
        "seeded_total=%d | missing=%d | bars_per_symbol=%d | source_dates=%s | attempt=%d | "
        "missing_symbols=%s | grep=SESSION_TECH_WARMUP",
        WARMUP_SOURCE,
        len(need),
        summary["fetched"],
        seeded,
        summary["seeded_total"],
        len(summary["missing"]),
        n,
        ",".join(sorted(src_dates)) or "none",
        st["attempts"],
        ",".join(summary["missing"][:24]) + ("..." if len(summary["missing"]) > 24 else ""),
    )
    return summary


def warmup_due(system: Any, *, now: Optional[datetime] = None) -> bool:
    """True when the prior-session seed should (re)run now: 06:00-12:30 PT, not
    complete today, at most 4 attempts spaced 120 s."""
    if system is None or not warmup_enabled():
        return False
    now_pt = _now_pt(now)
    start = _parse_hhmm(os.getenv("SESSION_TECH_WARMUP_START_PT", "06:00")) or dt_time(6, 0)
    if not (start <= now_pt.time() < dt_time(12, 30)):
        return False
    st = getattr(system, "_session_tech_warmup", None)
    if not isinstance(st, dict) or st.get("date") != now_pt.date().isoformat():
        return True
    if st.get("complete"):
        return False
    if int(st.get("attempts") or 0) >= 4:
        return False
    return (time.time() - float(st.get("last_attempt") or 0.0)) >= 120.0


def start_warmup_task(system: Any, symbols: Sequence[str], *, reason: str = "") -> bool:
    """Run ``seed_prior_session_warmup`` in the background (never blocks the loop)."""
    task = getattr(system, "_session_tech_warmup_task", None)
    if task is not None and not task.done():
        return False

    async def _run() -> None:
        try:
            summary = await seed_prior_session_warmup(system, list(symbols or ()))
            st = _store(system)
            if summary.get("requested") and not summary.get("missing"):
                st["complete"] = True
            elif summary.get("seeded_total", 0) >= max(1, int(summary.get("requested") or 0)) * 0.97:
                # A few symbols (new listings, halted names) have no prior session.
                st["complete"] = True
        except Exception as exc:
            log.warning("SESSION_TECH_WARMUP | stage=task_failed | reason=%s | err=%s", reason, exc)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    setattr(system, "_session_tech_warmup_task", loop.create_task(_run()))
    log.info(
        "SESSION_TECH_WARMUP | stage=task_started | reason=%s | symbols=%d | grep=SESSION_TECH_WARMUP",
        reason,
        len(list(symbols or ())),
    )
    return True


def seed_today_bars_from_tl0_fetch(system: Any, yf_map: Optional[Dict[str, Any]]) -> int:
    """Merge the TL0 build's real Yahoo 06:30-06:45 1m bars into session tech."""
    if system is None or not yf_map or not tl0_bar_seed_enabled():
        return 0
    try:
        from modules.session_tech_indicators import seed_session_tech_bars

        n = int(seed_session_tech_bars(system, {k: v for k, v in yf_map.items() if v}, bar_kind="minute") or 0)
    except Exception as exc:
        log.warning("SESSION_TECH_WARMUP | stage=tl0_bar_seed_failed | err=%s", exc)
        return 0
    log.info(
        "SESSION_TECH_WARMUP | stage=tl0_bar_seed | symbols=%d | seeded=%d | source=yahoo_1m_0630_0645 | "
        "grep=SESSION_TECH_WARMUP",
        sum(1 for v in yf_map.values() if v),
        n,
    )
    return n


# --- chain ring pre-warm ---------------------------------------------------


def chain_prewarm_enabled() -> bool:
    return _env_bool("CHAIN_RING_PREWARM_ENABLED", True)


def chain_prewarm_times() -> List[dt_time]:
    raw = os.getenv("CHAIN_RING_PREWARM_TIMES_PT", "06:40,06:50,07:00,07:15,07:26,07:33")
    out = [t for t in (_parse_hhmm(x) for x in str(raw).split(",")) if t is not None]
    return sorted(out)


def chain_prewarm_due_slot(system: Any, *, now: Optional[datetime] = None) -> Optional[str]:
    """The HH:MM slot to run now (inside its window, not yet run today), else None."""
    if system is None or not chain_prewarm_enabled():
        return None
    now_pt = _now_pt(now)
    window = _env_int("CHAIN_RING_PREWARM_WINDOW_SEC", 240, 30, 900)
    done = getattr(system, "_chain_ring_prewarm_done", None)
    if not isinstance(done, dict) or done.get("date") != now_pt.date().isoformat():
        done = {"date": now_pt.date().isoformat(), "slots": set()}
        setattr(system, "_chain_ring_prewarm_done", done)
    secs = now_pt.hour * 3600 + now_pt.minute * 60 + now_pt.second
    for t in chain_prewarm_times():
        start = t.hour * 3600 + t.minute * 60
        slot = t.strftime("%H:%M")
        if start <= secs < start + window and slot not in done["slots"]:
            return slot
    return None


def _latest_ring_t(system: Any, sym: str) -> float:
    try:
        from central_tech.chain_ring import _ensure_chain_skew_ring

        buf = _ensure_chain_skew_ring(system).get(sym) or []
        return max((float(s.get("t") or 0.0) for s in buf if isinstance(s, dict)), default=0.0)
    except Exception:
        return 0.0


def _evict_chain_cache(manager: Any, sym: str, since_epoch: float) -> int:
    cache = getattr(manager, "chain_cache", None)
    if not isinstance(cache, dict):
        return 0
    n = 0
    for key in [k for k in list(cache.keys()) if str(k).startswith(f"{sym}_")]:
        entry = cache.get(key)
        try:
            ts = float((entry or {}).get("timestamp") or 0.0)
        except (TypeError, ValueError, AttributeError):
            ts = 0.0
        if ts >= since_epoch - 1.0:
            cache.pop(key, None)
            n += 1
    return n


def _spot_from_quote(q: Any) -> float:
    for attr in ("last_price", "last", "current_price", "lastTrade", "mark", "price"):
        try:
            raw = q.get(attr) if isinstance(q, dict) else getattr(q, attr, None)
            v = float(raw or 0.0)
        except (TypeError, ValueError):
            continue
        if v > 0:
            return v
    return 0.0


async def prewarm_chain_ring(
    system: Any,
    symbols: Sequence[str],
    *,
    slot: str = "",
    deadline_epoch: Optional[float] = None,
    min_ring_age_sec: float = 0.0,
    manager: Any = None,
    quotes: Optional[Dict[str, Any]] = None,
    fetch_skew: Optional[Callable[..., Awaitable[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    """One live 0DTE chain per symbol into the central chain ring (real samples only).

    ``min_ring_age_sec`` > 0 skips symbols that already have a ring sample that
    recent. Stops at ``deadline_epoch``. Yields to the open-position monitor.
    """
    from central_tech.chain_ring import record_chain_skew_sample

    t0 = time.time()
    deadline = float(deadline_epoch) if deadline_epoch else t0 + 150.0
    syms = list(dict.fromkeys(str(s or "").strip().upper() for s in symbols or () if str(s or "").strip()))
    if min_ring_age_sec > 0:
        syms = [s for s in syms if (t0 - _latest_ring_t(system, s)) >= float(min_ring_age_sec)]
    out: Dict[str, Any] = {
        "slot": slot,
        "requested": len(syms),
        "recorded": 0,
        "no_volume": 0,
        "no_spot": 0,
        "errors": 0,
        "evicted": 0,
        "skipped_deadline": 0,
        "deferred_open_quotes": 0,
    }
    if not syms:
        return out
    if manager is None:
        try:
            from easy0DTE.modules.convex_chain_skew import resolve_options_chain_manager

            manager = resolve_options_chain_manager(system)
        except Exception:
            manager = None
    if manager is None:
        log.warning("CHAIN_RING_PREWARM | slot=%s | action=skip | reason=no_chain_manager", slot)
        return out
    if fetch_skew is None:
        from easy0DTE.modules.convex_chain_skew import fetch_chain_skew_for_symbol as fetch_skew
    if quotes is None:
        quotes = {}
        dm = getattr(system, "data_manager", None)
        if dm is not None and hasattr(dm, "get_batch_quotes"):
            try:
                quotes = await dm.get_batch_quotes(syms, skip_cache=True) or {}
            except TypeError:
                quotes = await dm.get_batch_quotes(syms) or {}
            except Exception as exc:
                log.warning("CHAIN_RING_PREWARM | slot=%s | quotes_failed | err=%s", slot, exc)
                quotes = {}
    warm = getattr(system, "_po_chain_skew_warm_symbols", None)
    sem = asyncio.Semaphore(_env_int("CHAIN_RING_PREWARM_CONCURRENCY", 4, 1, 6))

    async def _one(sym: str) -> None:
        async with sem:
            if time.time() >= deadline:
                out["skipped_deadline"] += 1
                return
            # Exact-OCC exits own the E*TRADE budget: yield to the 2s monitor and
            # skip (never queue) when open-position quotes are unhealthy.
            try:
                from modules.open_position_quote_orchestration import (
                    open_quote_budget_allows_discovery,
                    yield_to_open_position_monitor,
                )

                _cfm = None
                try:
                    from central_tech.chain_fetch_manager import P_PREWARM, get_manager

                    _cfm = get_manager(system)
                except Exception:
                    _cfm = None
                if _cfm is not None:
                    # Oct 13 2026: open positions no longer zero out the prewarm.
                    # It waits for a low-priority token (exits/gates keep their
                    # reserve) and stops at the slot deadline.
                    while True:
                        ok, _why = _cfm.try_acquire(P_PREWARM, sym, reason="prewarm")
                        if ok:
                            break
                        if time.time() >= deadline:
                            out["skipped_deadline"] += 1
                            out["deferred_budget"] = int(out.get("deferred_budget") or 0) + 1
                            return
                        await asyncio.sleep(min(5.0, max(0.5, _cfm.seconds_until_token(P_PREWARM))))
                else:
                    allow, _why = open_quote_budget_allows_discovery(system)
                    if not allow:
                        out["deferred_open_quotes"] += 1
                        return
                await yield_to_open_position_monitor(system)
            except Exception:
                pass
            q = (quotes or {}).get(sym)
            spot = _spot_from_quote(q) if q is not None else 0.0
            if spot <= 0:
                out["no_spot"] += 1
                return
            was_warm = isinstance(warm, dict) and sym in warm
            started = time.time()
            try:
                skew = await fetch_skew(manager, symbol=sym, underlying_price=spot, system=system)
            except Exception as exc:
                out["errors"] += 1
                log.debug("CHAIN_RING_PREWARM | symbol=%s | error=%s", sym, exc)
                return
            finally:
                out["evicted"] += _evict_chain_cache(manager, sym, started)
            if not isinstance(skew, dict) or not (skew.get("call_chain_volume") or skew.get("put_chain_volume")):
                out["no_volume"] += 1
                return
            sample = {k: skew.get(k) for k in ("call_chain_volume", "put_chain_volume",
                                                 "representative_volume", "cumulative_volume",
                                                 "open_interest", "representative_open_interest",
                                                 "representative_bid", "representative_ask",
                                                 "representative_strike", "iv", "delta")
                      if skew.get(k) is not None}
            sample["underlying_price"] = spot
            _seed_t = time.time()
            record_chain_skew_sample(system, sym, sample, ts=_seed_t)
            # t168u: the open seed of the unified dir5m sampler (trigger source /
            # lead-time telemetry; no API).
            try:
                from central_tech.dir5m_presample import note_open_seed

                note_open_seed(system, sym, now=_seed_t)
            except Exception:
                pass
            # Pre-warm must not join the all-day discovery warm-refresh rotation.
            w2 = getattr(system, "_po_chain_skew_warm_symbols", None)
            if not was_warm and isinstance(w2, dict):
                w2.pop(sym, None)
            out["recorded"] += 1

    await asyncio.gather(*(_one(s) for s in syms), return_exceptions=True)
    out["elapsed_s"] = round(time.time() - t0, 1)
    log.info(
        "CHAIN_RING_PREWARM | slot=%s | requested=%d | recorded=%d | no_volume=%d | no_spot=%d | "
        "errors=%d | skipped_deadline=%d | deferred_open_quotes=%d | cache_evicted=%d | elapsed_s=%.1f | "
        "grep=CHAIN_RING_PREWARM",
        slot, out["requested"], out["recorded"], out["no_volume"], out["no_spot"], out["errors"],
        out["skipped_deadline"], out["deferred_open_quotes"], out["evicted"], out["elapsed_s"],
    )
    return out


def start_chain_prewarm_task(system: Any, symbols: Sequence[str], *, now: Optional[datetime] = None) -> Optional[str]:
    """Start the due pre-warm slot in the background. Returns the slot or None."""
    slot = chain_prewarm_due_slot(system, now=now)
    if slot is None:
        return None
    task = getattr(system, "_chain_ring_prewarm_task", None)
    if task is not None and not task.done():
        return None
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return None
    system._chain_ring_prewarm_done["slots"].add(slot)
    times = chain_prewarm_times()
    first = times[0].strftime("%H:%M") if times else slot
    window = _env_int("CHAIN_RING_PREWARM_WINDOW_SEC", 240, 30, 900)
    now_pt = _now_pt(now)
    hh, mm = (int(x) for x in slot.split(":"))
    start_epoch = now_pt.replace(hour=hh, minute=mm, second=0, microsecond=0).timestamp()
    deadline = start_epoch + window + 30.0
    # First slot: every symbol. Later slots: only symbols with no ring sample in 4 min.
    min_age = 0.0 if slot == first else 240.0

    async def _run() -> None:
        try:
            await prewarm_chain_ring(system, list(symbols or ()), slot=slot,
                                     deadline_epoch=deadline, min_ring_age_sec=min_age)
        except Exception as exc:
            log.warning("CHAIN_RING_PREWARM | slot=%s | task_failed | err=%s", slot, exc)

    system._chain_ring_prewarm_task = loop.create_task(_run())
    return slot
