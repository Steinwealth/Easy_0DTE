"""One session-tech bar fetch per monitor tick.

Collect open underlyings, fetch once, then persist from that cache. Do not
stack a 3s refresh per position. Flatten primes remaining opens once, then
marks EOD so persist stays cache-only.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Dict, Iterable, List, Optional

EOD_FLAG = "_po_eod_flattening"
PRIMED_TICK = "_po_session_tech_tick_primed"
FAILED_TICK = "_po_session_tech_tick_failed"
CHAIN_PACE_MONO = "_po_eod_chain_last_mono"
EOD_CHAIN_GAP_S = 0.25
PRIME_TIMEOUT_S = 3.0


def mark_eod_flattening(system: Any, active: bool) -> None:
    if system is None:
        return
    setattr(system, EOD_FLAG, bool(active))


def eod_flattening(system: Any) -> bool:
    return bool(getattr(system, EOD_FLAG, False)) if system is not None else False


def session_tech_tick_primed(system: Any) -> bool:
    if system is None:
        return False
    tick = getattr(system, "_unified_0dte_monitor_tick_id", None)
    return tick not in (None, "") and getattr(system, PRIMED_TICK, None) == tick


def session_tech_tick_failed(system: Any) -> bool:
    if system is None:
        return False
    tick = getattr(system, "_unified_0dte_monitor_tick_id", None)
    return tick not in (None, "") and getattr(system, FAILED_TICK, None) == tick


def _close_prime(system: Any, *, failed: bool) -> None:
    tick = getattr(system, "_unified_0dte_monitor_tick_id", None)
    if tick in (None, ""):
        return
    setattr(system, PRIMED_TICK, tick)
    setattr(system, FAILED_TICK, tick if failed else None)


def allow_inline_session_tech_refresh(system: Any, *, handoff: bool = False) -> bool:
    """False when this tick already primed the hub, or during EOD flatten.

    Handoff still refreshes when the tick was not primed, even if the monitor
    is running. Blank handoff stamps are not allowed.
    """
    if eod_flattening(system):
        return False
    if session_tech_tick_primed(system):
        return False
    if handoff:
        return True
    return True


def open_underlying_symbols(positions: Iterable[Any]) -> List[str]:
    out: List[str] = []
    seen = set()
    for pos in positions or []:
        sym = str(getattr(pos, "symbol", "") or "").strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
    return out


def cached_underlying_quotes(system: Any, symbols: Iterable[str]) -> Dict[str, Any]:
    """Reuse discovery / last-underlying marks so the prime does not re-quote.

    Flight recorder and the monitor already share exact OCC. Session-tech bars
    ingest these equity marks instead of a second get_batch_quotes.

    A live owned last (fresh TTL) wins over a 45s discovery quote so VWAP
    distance is not computed from a frozen spot.
    """
    names = [str(s).strip().upper() for s in (symbols or []) if str(s).strip()]
    out: Dict[str, Any] = {}
    if system is None or not names:
        return out
    try:
        from modules.open_position_quote_orchestration import (
            take_discovery_quotes_from_merged_cache,
        )

        try:
            from central_tech.decision_freeze import quote_max_age_sec

            quote_reuse = float(quote_max_age_sec())
        except Exception:
            quote_reuse = 120.0
        cached = take_discovery_quotes_from_merged_cache(system, names, max_age_sec=quote_reuse)
        if isinstance(cached, dict):
            out.update(cached)
    except Exception:
        pass
    last = getattr(system, "_orb_0dte_last_underlying_by_symbol", None) or {}
    meta = getattr(system, "_orb_0dte_underlying_meta_by_symbol", None) or {}
    now_m = time.monotonic()
    try:
        from modules.underlying_freshness import underlying_fresh_max_ms

        fresh_max_s = float(underlying_fresh_max_ms()) / 1000.0
    except Exception:
        fresh_max_s = 4.0
    if isinstance(last, dict):
        for sym in names:
            px = last.get(sym)
            try:
                price = float(px)
            except (TypeError, ValueError):
                continue
            if price <= 0:
                continue
            row = meta.get(sym) if isinstance(meta, dict) else None
            obs_m = None
            if isinstance(row, dict):
                try:
                    obs_m = float(row.get("observed_mono")) if row.get("observed_mono") is not None else None
                except (TypeError, ValueError):
                    obs_m = None
            live = obs_m is not None and (now_m - obs_m) <= fresh_max_s
            if live:
                payload = {"last": price, "price": price}
                iso = (row or {}).get("observed_iso") if isinstance(row, dict) else None
                if iso:
                    payload["date_time_utc"] = iso
                out[sym] = payload
            elif sym not in out:
                out[sym] = {"last": price, "price": price}
    return out


async def prime_open_session_tech(
    system: Any,
    symbols: Iterable[str],
    *,
    quotes: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """One hub bar fetch for this tick. No internal timeout — the monitor joins it."""
    names = [str(s).strip().upper() for s in (symbols or []) if str(s).strip()]
    names = list(dict.fromkeys(names))
    if system is None or not names or eod_flattening(system):
        return []
    from modules.session_tech_indicators import ensure_session_tech_bars

    seed = quotes if isinstance(quotes, dict) else cached_underlying_quotes(system, names)
    try:
        await ensure_session_tech_bars(system, names, quotes=seed or None)
    except Exception:
        _close_prime(system, failed=True)
        return []
    _close_prime(system, failed=False)
    return names


async def finish_prime_with_quote_wait(
    system: Any,
    tech_task: Any,
    *,
    started_mono: float,
    cap_s: float = PRIME_TIMEOUT_S,
) -> List[str]:
    """Wait for the bar fetch until the 3s tick budget is spent.

    Quotes and bars start together. After quotes return, leftover budget (up to
    3s from start) is given to bars. refresh_failed only if bars are still
    running when that budget is gone — the same wall quotes already used.
    """
    remaining = float(cap_s) - (time.monotonic() - float(started_mono))
    if tech_task is None:
        return []
    try:
        if remaining <= 0:
            if getattr(tech_task, "done", lambda: False)():
                return list(tech_task.result() or [])
            _close_prime(system, failed=True)
            return []
        return list(await asyncio.wait_for(tech_task, timeout=remaining) or [])
    except asyncio.TimeoutError:
        _close_prime(system, failed=True)
        return []
    except Exception:
        _close_prime(system, failed=True)
        return []


def pace_eod_exit_chain(system: Any) -> None:
    """Space forced exit chains so flatten does not stamp 50 at once."""
    if not eod_flattening(system):
        return
    last = float(getattr(system, CHAIN_PACE_MONO, 0.0) or 0.0)
    now = time.monotonic()
    wait = EOD_CHAIN_GAP_S - (now - last)
    if wait > 0:
        time.sleep(wait)
    setattr(system, CHAIN_PACE_MONO, time.monotonic())
