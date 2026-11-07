"""Canonical underlying spot stamps for Priority Optimizer snaps + Profit Optimizer path ticks.

Asset-stop research needs entry vs live equity on every entry/exit snap and every
live/shadow premium_path sample. Keep field names stable:

- ``underlying_price`` — fresh live equity spot (authoritative for path ticks)
- ``entry_underlying`` / ``entry_underlying_price`` — frozen fill-time equity
- ``exit_underlying`` — exit-time equity (exit snaps only)
- ``current_price`` / ``close`` — aliases of live spot on Priority Optimizer snaps
- ``underlying_move_pct`` — side-aligned move from entry (CALL up+, PUT down+)
- ``underlying_price_missing`` — invent flag when live spot unavailable
"""

from __future__ import annotations

import inspect
import logging
import math
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

log = logging.getLogger(__name__)


def coerce_underlying_price(value: Any) -> Optional[float]:
    """Return a finite equity price > 0, else None."""
    try:
        if value is None or value == "":
            return None
        px = float(value)
        if math.isfinite(px) and px > 0.0:
            return px
    except (TypeError, ValueError):
        return None
    return None


def extract_underlying_from_quote(quote: Any) -> Optional[float]:
    """Pull last (preferred) or live bid/ask mid from a broker equity quote dict."""
    if not isinstance(quote, Mapping):
        return None
    for key in (
        "last",
        "lastPrice",
        "last_price",
        "close",
        "price",
        "mark",
        "underlying_price",
        "current_price",
    ):
        px = coerce_underlying_price(quote.get(key))
        if px is not None:
            return px
    bid = coerce_underlying_price(quote.get("bid"))
    ask = coerce_underlying_price(quote.get("ask"))
    if bid is not None and ask is not None:
        mid = (bid + ask) / 2.0
        if mid > 0:
            return mid
    # Nested All quote shapes
    for nest_key in ("All", "all", "quote", "Quote"):
        nest = quote.get(nest_key)
        if isinstance(nest, Mapping):
            px = extract_underlying_from_quote(nest)
            if px is not None:
                return px
    return None


def _side_is_put(option_side: Any) -> bool:
    s = str(option_side or "").strip().lower()
    return "put" in s or s in {"short", "p"}


def stamp_underlying_research_fields(
    row: Optional[Dict[str, Any]],
    *,
    live_underlying: Any = None,
    entry_underlying: Any = None,
    exit_underlying: Any = None,
    option_side: Any = None,
    at_exit: bool = False,
    quote_age_sec: Any = None,
    source: str = "",
) -> Dict[str, Any]:
    """Stamp live + entry (+ exit) underlying fields onto a feature / path row.

    Always sets ``underlying_price_missing``. Prefer explicit args; fall back to
    existing row keys when args are empty.
    """
    if not isinstance(row, dict):
        return {}

    live = coerce_underlying_price(live_underlying)
    if live is None:
        live = coerce_underlying_price(
            row.get("underlying_price")
            or row.get("current_price")
            or row.get("close")
            or row.get("price")
            or row.get("last")
        )

    entry = coerce_underlying_price(entry_underlying)
    if entry is None:
        entry = coerce_underlying_price(
            row.get("entry_underlying")
            or row.get("entry_underlying_price")
            or row.get("underlying_entry_price")
        )

    exit_px = coerce_underlying_price(exit_underlying)
    if exit_px is None and at_exit:
        exit_px = live

    # At entry fill, freeze entry to live when not already set.
    if entry is None and live is not None and not at_exit:
        entry = live

    if live is not None:
        row["underlying_price"] = live
        row["current_price"] = live
        row["close"] = live
        row["underlying_price_missing"] = False
    else:
        row["underlying_price_missing"] = True

    if entry is not None:
        row["entry_underlying"] = entry
        row["entry_underlying_price"] = entry

    if at_exit and exit_px is not None:
        row["exit_underlying"] = exit_px
        # Live spot on exit snaps is the exit equity.
        row["underlying_price"] = exit_px
        row["current_price"] = exit_px
        row["close"] = exit_px
        row["underlying_price_missing"] = False
        live = exit_px

    if live is not None and entry is not None and entry > 0:
        raw = (live - entry) / entry
        if _side_is_put(option_side or row.get("option_side") or row.get("side") or row.get("option_type")):
            row["underlying_move_pct"] = -raw  # favorable when underlying falls
        else:
            row["underlying_move_pct"] = raw
        row["underlying_vs_entry_pct"] = raw  # signed CALL convention (up +)

    age = coerce_underlying_price(quote_age_sec)  # reuse float coerce loosely
    if age is not None:
        try:
            row["underlying_quote_age_sec"] = float(quote_age_sec)
        except (TypeError, ValueError):
            pass

    if source:
        row["underlying_price_source"] = str(source)[:80]

    # The snapshot write is not the quote time. Keep a clock only when the
    # quote cache already recorded one.
    observed = (
        row.get("quote_observed_at")
        or row.get("underlying_observation_timestamp")
        or row.get("po_quote_asof_at")
    )
    if observed not in (None, ""):
        row["underlying_stamped_at"] = observed
    return row


def refresh_underlying_spot_cache(
    system: Any,
    symbols: Iterable[str],
    *,
    quotes: Optional[Mapping[str, Any]] = None,
) -> Dict[str, float]:
    """Update ``system._orb_0dte_last_underlying_by_symbol`` for the given underlyings.

    Prefer an already-fetched ``quotes`` map. Sync callers must **not** invoke
    async ``data_manager.get_batch_quotes`` (Aug 4 bug: un-awaited coroutine →
    sparse monitor / stale asset-stop spots). Use
    ``refresh_underlying_spot_cache_async`` from async monitor ticks instead.
    Returns the refreshed symbol→price map (only successes).
    """
    syms = sorted({str(s or "").strip().upper() for s in symbols if str(s or "").strip()})
    if not syms:
        return {}

    cache = getattr(system, "_orb_0dte_last_underlying_by_symbol", None)
    if not isinstance(cache, dict):
        cache = {}
        try:
            system._orb_0dte_last_underlying_by_symbol = cache
        except Exception:
            pass

    quote_map: Dict[str, Any] = {}
    if isinstance(quotes, Mapping):
        quote_map = {str(k).strip().upper(): v for k, v in quotes.items()}
    else:
        # Sync path: only use a sync getter. Never call async get_batch_quotes here
        # (Aug 4: un-awaited coroutine → sparse Position Monitoring / stale asset-stop).
        dm = getattr(system, "data_manager", None)
        getter = getattr(dm, "get_batch_quotes_sync", None) if dm is not None else None
        if callable(getter):
            try:
                fetched = getter(syms)
                if inspect.isawaitable(fetched):
                    try:
                        fetched.close()  # type: ignore[attr-defined]
                    except Exception:
                        pass
                    fetched = None
                if isinstance(fetched, Mapping):
                    quote_map = {str(k).strip().upper(): v for k, v in fetched.items()}
            except Exception:
                quote_map = {}

    return _apply_underlying_quotes_to_cache(cache, syms, quote_map)


async def refresh_underlying_spot_cache_async(
    system: Any,
    symbols: Iterable[str],
    *,
    quotes: Optional[Mapping[str, Any]] = None,
    skip_cache: bool = True,
    owned_only: bool = True,
    mark_fresh: Optional[bool] = None,
) -> Dict[str, float]:
    """Async refresh of the shared underlying cache.

    Evaluate never calls get_quote. This shared pass fetches a live broker last
    for stale owned names (skip_cache=True, oldest first, one batch by default).
    Passing an already-built ``quotes`` map only seeds the price cache — it does
    **not** stamp fresh_batch (that was the 10/30 frozen-UL hole: discovery/last
    cache restamped as now). A failed/slow fetch falls through to last_good /
    unavailable.
    """
    import asyncio

    from modules.underlying_freshness import (
        record_fresh_batch_prices,
        select_symbols_for_owned_refresh,
        symbols_needing_fresh_refresh,
    )

    requested = sorted({str(s or "").strip().upper() for s in symbols if str(s or "").strip()})
    if not requested:
        return {}

    cache = getattr(system, "_orb_0dte_last_underlying_by_symbol", None)
    if not isinstance(cache, dict):
        cache = {}
        try:
            system._orb_0dte_last_underlying_by_symbol = cache
        except Exception:
            pass

    quote_map: Dict[str, Any] = {}
    fetched_live = False
    if isinstance(quotes, Mapping):
        quote_map = {str(k).strip().upper(): v for k, v in quotes.items()}
    elif getattr(system, "_orb_0dte_ul_refresh_inflight", False):
        # Previous tick's broker call is still running. Do not stack another.
        return {s: px for s in requested if (px := coerce_underlying_price(cache.get(s)))}
    else:
        if owned_only:
            # One owned refresh per tick: oldest first, then the rest, cap 25.
            # The 4s skip left most names as last_good / unavailable.
            from modules.underlying_freshness import owned_underlying_refresh_max

            stale = select_symbols_for_owned_refresh(system, requested)
            rest = [s for s in requested if s not in set(stale)]
            ordered = list(stale) + rest
            cap = owned_underlying_refresh_max()
            syms = ordered[:cap] if cap > 0 else ordered
        else:
            need = symbols_needing_fresh_refresh(system, requested)
            from modules.underlying_freshness import owned_underlying_refresh_max

            cap = owned_underlying_refresh_max()
            syms = need[:cap] if cap > 0 else need
        if not syms:
            return {s: px for s in requested if (px := coerce_underlying_price(cache.get(s)))}
        dm = getattr(system, "data_manager", None)
        getter = getattr(dm, "get_batch_quotes", None) if dm is not None else None
        if callable(getter):
            try:
                try:
                    gen = int(getattr(system, "_orb_0dte_ul_refresh_generation", 0) or 0) + 1
                    system._orb_0dte_ul_refresh_generation = gen
                    system._orb_0dte_ul_refresh_inflight = True
                except Exception:
                    gen = 1
                try:
                    try:
                        fetched = getter(syms, skip_cache=bool(skip_cache), fail_fast=True)
                    except TypeError:
                        fetched = getter(syms, skip_cache=bool(skip_cache)) if skip_cache else getter(syms)
                    if inspect.isawaitable(fetched):
                        # Do not wait_for-cancel. Equity last shares the market
                        # lane with OCC; 5–8s is common. Cancelling discards a
                        # completed to_thread result and can start a second
                        # to_thread while the first HTTP is still alive.
                        fetched = await fetched
                    if int(getattr(system, "_orb_0dte_ul_refresh_generation", 0) or 0) != int(gen):
                        # Superseded generation: ignore a late result so a
                        # cancelled/orphaned worker cannot restamp fresh_batch.
                        return {
                            s: px
                            for s in requested
                            if (px := coerce_underlying_price(cache.get(s)))
                        }
                    if isinstance(fetched, Mapping):
                        quote_map = {str(k).strip().upper(): v for k, v in fetched.items()}
                        fetched_live = True
                    try:
                        from modules.option_api_accounting import record_real_broker_request

                        n_batches = max(1, (len(syms) + 24) // 25)
                        record_real_broker_request(system, lane="equity", count=n_batches)
                        stats = getattr(system, "_unified_und_tick_stats", None)
                        if isinstance(stats, dict):
                            stats["refresh_real_calls"] = int(stats.get("refresh_real_calls") or 0) + n_batches
                            stats["refresh_requested"] = int(stats.get("refresh_requested") or 0) + len(syms)
                    except Exception:
                        pass
                finally:
                    try:
                        system._orb_0dte_ul_refresh_inflight = False
                    except Exception:
                        pass
            except Exception as exc:
                log.debug("underlying_spot_refresh_failed | n=%d | err=%s", len(syms), exc)
                quote_map = {}

    updated = _apply_underlying_quotes_to_cache(
        cache, list(quote_map.keys()), quote_map, recycle_prior=False
    )
    stamp_fresh = bool(fetched_live) if mark_fresh is None else bool(mark_fresh)
    if updated and stamp_fresh:
        cache_clocks = _CACHE_OBSERVED_AT.get(id(cache), {})
        by_clock: Dict[str, Dict[str, float]] = {}
        for sym, px in updated.items():
            clock = str(cache_clocks.get(sym) or "")
            by_clock.setdefault(clock, {})[sym] = px
        for clock, prices in by_clock.items():
            record_fresh_batch_prices(
                system, prices, observed_iso=clock or None
            )
    return updated


# Observation time stored beside the price cache. Keyed by the cache object's
# identity so a later snapshot can copy the time the quote was received.
_CACHE_OBSERVED_AT: Dict[int, Dict[str, str]] = {}


def _quote_source_time(quote: Any) -> str:
    """Broker timestamp on the quote, if the payload has one.

    Prefer the exchange/broker clock (date_time_utc) over a local receive
    ``timestamp`` that get_batch_quotes always sets to utcnow.
    """
    if not isinstance(quote, Mapping):
        return ""
    for key in (
        "date_time_utc",
        "dateTimeUTC",
        "quoteTime",
        "date_time",
        "dateTime",
        "datetime",
        "asOf",
        "asof",
        "timestamp",
        "time",
    ):
        raw = quote.get(key)
        if raw not in (None, ""):
            return str(raw)
    for nest_key in ("All", "all", "quote", "Quote"):
        nest = quote.get(nest_key)
        if isinstance(nest, Mapping):
            found = _quote_source_time(nest)
            if found:
                return found
    return ""


def _apply_underlying_quotes_to_cache(
    cache: Dict[str, Any],
    syms: Sequence[str],
    quote_map: Mapping[str, Any],
    *,
    recycle_prior: bool = False,
) -> Dict[str, float]:
    """Write extracted lasts into the price cache.

    A missing last is not a refresh. Recycling the prior price as ``updated``
    made record_fresh_batch_prices stamp now onto a frozen last (10/30).
    """
    updated: Dict[str, float] = {}
    clocks = _CACHE_OBSERVED_AT.setdefault(id(cache), {})
    for sym in syms:
        raw_quote = quote_map.get(sym)
        px = extract_underlying_from_quote(raw_quote)
        if px is None:
            if recycle_prior:
                prior = coerce_underlying_price(cache.get(sym))
                if prior is not None:
                    updated[sym] = prior
            continue
        cache[sym] = px
        updated[sym] = px
        source_time = _quote_source_time(raw_quote)
        clocks[sym] = source_time or datetime.now(timezone.utc).isoformat()
    return updated


def lookup_underlying_observed_at(system: Any, symbol: str) -> Optional[str]:
    """Return the clock stored when this symbol's quote entered the cache."""
    sym = str(symbol or "").strip().upper()
    if not sym or system is None:
        return None
    cache = getattr(system, "_orb_0dte_last_underlying_by_symbol", None)
    if not isinstance(cache, dict):
        return None
    observed = _CACHE_OBSERVED_AT.get(id(cache), {}).get(sym)
    return str(observed) if observed not in (None, "") else None


def resolve_cached_underlying(system: Any, symbol: str) -> Optional[float]:
    """Read last known und from the system cache."""
    sym = str(symbol or "").strip().upper()
    if not sym:
        return None
    cache = getattr(system, "_orb_0dte_last_underlying_by_symbol", None)
    if isinstance(cache, dict):
        return coerce_underlying_price(cache.get(sym))
    return None


def resolve_entry_underlying(
    *,
    row: Optional[Mapping[str, Any]] = None,
    signal_snapshot: Optional[Mapping[str, Any]] = None,
    position: Any = None,
    order_payload: Optional[Mapping[str, Any]] = None,
) -> Optional[float]:
    """Best-effort entry equity from common containers."""
    sources: Sequence[Any] = (
        (row or {}).get("entry_underlying") if isinstance(row, Mapping) else None,
        (row or {}).get("entry_underlying_price") if isinstance(row, Mapping) else None,
        (signal_snapshot or {}).get("entry_underlying_price") if isinstance(signal_snapshot, Mapping) else None,
        (signal_snapshot or {}).get("entry_underlying") if isinstance(signal_snapshot, Mapping) else None,
        (order_payload or {}).get("entry_underlying_price") if isinstance(order_payload, Mapping) else None,
        getattr(position, "entry_underlying", None) if position is not None else None,
        getattr(position, "underlying_entry_price", None) if position is not None else None,
    )
    for raw in sources:
        px = coerce_underlying_price(raw)
        if px is not None:
            return px
    if position is not None:
        meta = getattr(position, "metadata", None) or {}
        if isinstance(meta, dict):
            sig = meta.get("signal_snapshot") if isinstance(meta.get("signal_snapshot"), dict) else {}
            px = coerce_underlying_price(
                sig.get("entry_underlying_price") or meta.get("entry_underlying_price")
            )
            if px is not None:
                return px
    return None
