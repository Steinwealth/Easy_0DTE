"""Locked open-position underlying freshness contract (monitor cadence).

evaluate() consumes only cache-layer observations: fresh_batch | shared_cache |
last_good | unavailable. Never initiate a broker equity get_quote from here.
"""

from __future__ import annotations

import logging
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional, Set

log = logging.getLogger(__name__)

SOURCE_FRESH_BATCH = "fresh_batch"
SOURCE_SHARED_CACHE = "shared_cache"
SOURCE_LAST_GOOD = "last_good"
SOURCE_UNAVAILABLE = "unavailable"

DEFAULT_FRESH_MAX_MS = 4000.0
DEFAULT_LAST_GOOD_MAX_MS = 8000.0
# One E*TRADE equity batch. 10–20 open names refresh every stale tick;
# 200-wide research rotates oldest first so OCC still owns the 2s budget.
DEFAULT_OWNED_REFRESH_MAX = 25

META_ATTR = "_orb_0dte_underlying_meta_by_symbol"
PRICE_ATTR = "_orb_0dte_last_underlying_by_symbol"
TICK_FRESH_ATTR = "_unified_und_tick_fresh_symbols"
TICK_SHARED_ATTR = "_unified_und_tick_shared_symbols"
TICK_ID_ATTR = "_unified_und_tick_id"
STATS_ATTR = "_unified_und_tick_stats"


def underlying_fresh_max_ms() -> float:
    try:
        return max(0.0, float(os.getenv("0DTE_UNDERLYING_FRESH_MAX_MS", str(DEFAULT_FRESH_MAX_MS)) or DEFAULT_FRESH_MAX_MS))
    except (TypeError, ValueError):
        return DEFAULT_FRESH_MAX_MS


def underlying_last_good_max_ms() -> float:
    try:
        return max(
            0.0,
            float(os.getenv("0DTE_UNDERLYING_LAST_GOOD_MAX_MS", str(DEFAULT_LAST_GOOD_MAX_MS)) or DEFAULT_LAST_GOOD_MAX_MS),
        )
    except (TypeError, ValueError):
        return DEFAULT_LAST_GOOD_MAX_MS


def owned_underlying_refresh_max() -> int:
    """Max unique owned underlyings to skip_cache-refresh per tick. 0 = unlimited."""
    try:
        n = int(float(os.getenv("0DTE_UNDERLYING_OWNED_REFRESH_MAX", str(DEFAULT_OWNED_REFRESH_MAX)) or DEFAULT_OWNED_REFRESH_MAX))
    except (TypeError, ValueError):
        n = DEFAULT_OWNED_REFRESH_MAX
    return max(0, n)


@dataclass
class UnderlyingObservation:
    symbol: str
    price: Optional[float]
    source: str
    age_ms: Optional[float]
    observed_at_iso: Optional[str]
    reused: bool
    stale: bool

    @property
    def usable(self) -> bool:
        return self.source in (SOURCE_FRESH_BATCH, SOURCE_SHARED_CACHE, SOURCE_LAST_GOOD) and (
            self.price is not None and float(self.price) > 0
        )

    @property
    def price_or_zero(self) -> float:
        try:
            px = float(self.price or 0.0)
        except (TypeError, ValueError):
            return 0.0
        return px if px > 0 else 0.0


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso_epoch(raw: Any) -> Optional[float]:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _age_ms_from_stored_clock(
    row: Dict[str, Any],
    *,
    now_mono: float,
    now_wall: Optional[float] = None,
) -> Optional[float]:
    """Age from the clock stored with the price.

    Prefer ``observed_iso`` (the quote clock written beside the last) so age
    and stamp share one timeline. Fall back to ``observed_mono`` for legacy
    rows that only have a receive clock.
    """
    iso_epoch = _parse_iso_epoch(row.get("observed_iso") if isinstance(row, dict) else None)
    if iso_epoch is not None:
        wall = float(now_wall if now_wall is not None else datetime.now(timezone.utc).timestamp())
        return max(0.0, (wall - iso_epoch) * 1000.0)
    try:
        obs_mono = float(row.get("observed_mono")) if row.get("observed_mono") is not None else None
    except (TypeError, ValueError):
        obs_mono = None
    if obs_mono is None:
        return None
    return max(0.0, (float(now_mono) - obs_mono) * 1000.0)


def begin_underlying_tick(system: Any, tick_id: str) -> None:
    if system is None:
        return
    setattr(system, TICK_ID_ATTR, str(tick_id or ""))
    setattr(system, TICK_FRESH_ATTR, set())
    setattr(system, TICK_SHARED_ATTR, set())
    setattr(
        system,
        STATS_ATTR,
        {
            SOURCE_FRESH_BATCH: 0,
            SOURCE_SHARED_CACHE: 0,
            SOURCE_LAST_GOOD: 0,
            SOURCE_UNAVAILABLE: 0,
            "ages_ms": [],
            "refresh_requested": 0,
            "refresh_real_calls": 0,
        },
    )


def record_fresh_batch_prices(
    system: Any,
    prices: Dict[str, float],
    *,
    observed_mono: Optional[float] = None,
    observed_iso: Optional[str] = None,
) -> None:
    """Stamp symbols that this tick's shared equity refresh actually filled."""
    if system is None or not prices:
        return
    meta = getattr(system, META_ATTR, None)
    if not isinstance(meta, dict):
        meta = {}
        setattr(system, META_ATTR, meta)
    cache = getattr(system, PRICE_ATTR, None)
    if not isinstance(cache, dict):
        cache = {}
        setattr(system, PRICE_ATTR, cache)
    fresh: Set[str] = getattr(system, TICK_FRESH_ATTR, None) or set()
    now_m = float(observed_mono if observed_mono is not None else time.monotonic())
    now_iso = str(observed_iso or _iso_now())
    for raw, px in prices.items():
        sym = str(raw or "").strip().upper()
        try:
            price = float(px)
        except (TypeError, ValueError):
            continue
        if not sym or not math.isfinite(price) or price <= 0:
            continue
        cache[sym] = price
        meta[sym] = {
            "price": price,
            "observed_mono": now_m,
            "observed_iso": now_iso,
            "source": SOURCE_FRESH_BATCH,
        }
        fresh.add(sym)
    setattr(system, TICK_FRESH_ATTR, fresh)
    setattr(system, TICK_ID_ATTR, str(getattr(system, TICK_ID_ATTR, "") or ""))


def _meta_for(system: Any, symbol: str) -> Dict[str, Any]:
    meta = getattr(system, META_ATTR, None)
    if not isinstance(meta, dict):
        return {}
    row = meta.get(str(symbol or "").strip().upper())
    return row if isinstance(row, dict) else {}


def resolve_underlying_observation(
    system: Any,
    symbol: str,
    *,
    now_mono: Optional[float] = None,
) -> UnderlyingObservation:
    """Read-only cache resolve. Never calls the broker."""
    from modules.underlying_spot_stamp import coerce_underlying_price

    sym = str(symbol or "").strip().upper()
    now_m = float(now_mono if now_mono is not None else time.monotonic())
    unavailable = UnderlyingObservation(
        symbol=sym,
        price=None,
        source=SOURCE_UNAVAILABLE,
        age_ms=None,
        observed_at_iso=None,
        reused=False,
        stale=False,
    )
    if not sym or system is None:
        return unavailable

    fresh_set: Set[str] = getattr(system, TICK_FRESH_ATTR, None) or set()
    row = _meta_for(system, sym)
    px = coerce_underlying_price(row.get("price"))
    if px is None:
        cache = getattr(system, PRICE_ATTR, None)
        if isinstance(cache, dict):
            px = coerce_underlying_price(cache.get(sym))

    age_ms = _age_ms_from_stored_clock(row, now_mono=now_m) if row else None
    obs_iso = str(row.get("observed_iso") or "") or None

    fresh_max = underlying_fresh_max_ms()
    last_good_max = underlying_last_good_max_ms()

    if px is None:
        return unavailable

    # Age validity takes precedence over this-tick fresh_set membership.
    # A quote older than last_good_max is unavailable even if the symbol was
    # fetched on this tick (persist stalls can age a "fresh" stamp past 8s).
    if age_ms is not None and age_ms > last_good_max:
        return UnderlyingObservation(
            symbol=sym,
            price=None,
            source=SOURCE_UNAVAILABLE,
            age_ms=age_ms,
            observed_at_iso=obs_iso,
            reused=True,
            stale=True,
        )

    if age_ms is None:
        # Legacy float cache without a timestamp — treat as last_good if present,
        # but mark stale so research does not confuse it with a fresh batch.
        return UnderlyingObservation(
            symbol=sym,
            price=px,
            source=SOURCE_LAST_GOOD,
            age_ms=None,
            observed_at_iso=obs_iso,
            reused=True,
            stale=True,
        )

    stale = bool(age_ms > fresh_max)
    if stale:
        # 4000–8000ms: last_good + stale, usable. Membership does not keep
        # the fresh_batch label once the fresh TTL has elapsed.
        return UnderlyingObservation(
            symbol=sym,
            price=px,
            source=SOURCE_LAST_GOOD,
            age_ms=age_ms,
            observed_at_iso=obs_iso,
            reused=True,
            stale=True,
        )

    if sym in fresh_set:
        shared = getattr(system, TICK_SHARED_ATTR, None)
        if not isinstance(shared, set):
            shared = set()
            setattr(system, TICK_SHARED_ATTR, shared)
        reused = sym in shared
        if reused:
            source = SOURCE_SHARED_CACHE
        else:
            source = SOURCE_FRESH_BATCH
            shared.add(sym)
        return UnderlyingObservation(
            symbol=sym,
            price=px,
            source=source,
            age_ms=age_ms,
            observed_at_iso=obs_iso,
            reused=reused,
            stale=False,
        )

    # Within fresh window but not in this tick's batch → last_good, still usable.
    return UnderlyingObservation(
        symbol=sym,
        price=px,
        source=SOURCE_LAST_GOOD,
        age_ms=age_ms,
        observed_at_iso=obs_iso,
        reused=True,
        stale=False,
    )


def note_observation_stat(system: Any, obs: UnderlyingObservation) -> None:
    stats = getattr(system, STATS_ATTR, None)
    if not isinstance(stats, dict):
        return
    key = str(obs.source or SOURCE_UNAVAILABLE)
    stats[key] = int(stats.get(key, 0) or 0) + 1
    if obs.age_ms is not None:
        ages = stats.get("ages_ms")
        if isinstance(ages, list):
            ages.append(float(obs.age_ms))


def symbols_needing_fresh_refresh(system: Any, symbols: Iterable[str]) -> list:
    """Owned underlyings whose cache is older than the fresh TTL (or missing).

    Oldest first so a per-tick cap rotates the frozen names, not the ones just fetched.
    """
    from modules.underlying_spot_stamp import coerce_underlying_price

    now_m = time.monotonic()
    fresh_max = underlying_fresh_max_ms()
    ranked: list = []
    seen: Set[str] = set()
    meta = getattr(system, META_ATTR, None)
    cache = getattr(system, PRICE_ATTR, None)
    missing_age = 10**12
    for raw in symbols or []:
        sym = str(raw or "").strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        row = meta.get(sym) if isinstance(meta, dict) else None
        px = coerce_underlying_price((row or {}).get("price")) if isinstance(row, dict) else None
        if px is None and isinstance(cache, dict):
            px = coerce_underlying_price(cache.get(sym))
        if px is None:
            ranked.append((missing_age, sym))
            continue
        age_ms = _age_ms_from_stored_clock(row or {}, now_mono=now_m) if isinstance(row, dict) else None
        if age_ms is None:
            ranked.append((missing_age, sym))
            continue
        if age_ms > fresh_max:
            ranked.append((age_ms, sym))
    ranked.sort(key=lambda item: float(item[0]), reverse=True)
    return [sym for _age, sym in ranked]


def select_symbols_for_owned_refresh(system: Any, symbols: Iterable[str]) -> list:
    """Stale/missing owned names, oldest first, capped to one equity batch by default."""
    need = symbols_needing_fresh_refresh(system, symbols)
    cap = owned_underlying_refresh_max()
    if cap > 0 and len(need) > cap:
        return need[:cap]
    return need


def stamp_underlying_provenance(
    row: Optional[Dict[str, Any]],
    obs: UnderlyingObservation,
) -> Dict[str, Any]:
    """Add contract aliases without removing existing underlying_price keys."""
    if not isinstance(row, dict):
        return {}
    px = obs.price_or_zero
    missing = not obs.usable
    row["underlying_source"] = str(obs.source)
    row["underlying_last"] = px if px > 0 else None
    row["underlying_age_ms"] = obs.age_ms
    row["underlying_observation_timestamp"] = obs.observed_at_iso
    row["underlying_reused"] = bool(obs.reused)
    row["underlying_stale"] = bool(obs.stale)
    if obs.age_ms is not None:
        try:
            row["underlying_quote_age_sec"] = float(obs.age_ms) / 1000.0
        except (TypeError, ValueError):
            pass
    if px > 0:
        row["underlying_price"] = px
        row["current_underlying_price"] = px
        row["underlying_price_missing"] = False
    else:
        row["underlying_price_missing"] = True
    if missing:
        row["underlying_source"] = SOURCE_UNAVAILABLE
    return row


def underlying_source_unavailable(osnap: Optional[Dict[str, Any]]) -> bool:
    if not isinstance(osnap, dict):
        return False
    return str(osnap.get("underlying_source") or "").strip().lower() == SOURCE_UNAVAILABLE


def _coerce_positive_underlying(value: Any) -> Optional[float]:
    try:
        px = float(value)
    except (TypeError, ValueError):
        return None
    if math.isfinite(px) and px > 0.0:
        return px
    return None


def resolve_exit_underlying_price(
    *,
    live_underlying: Any = None,
    osnap: Optional[Dict[str, Any]] = None,
    position: Any = None,
    metadata: Optional[Dict[str, Any]] = None,
    entry_underlying: Any = None,
    symbol_cache: Optional[Dict[str, Any]] = None,
    symbol: Optional[str] = None,
) -> float:
    """Best available underlying for exit alerts / PO / certification.

    Never prefer a zero from an unavailable monitor tick when a prior good
    stamp (osnap / symbol cache / entry) exists — that produced Telegram
    ``Exit $: $0.00 (+0.00%)`` on D-SOFI-17.5p-392 while spot was ~$17.30.
    """
    candidates: list[Any] = [live_underlying]
    if isinstance(osnap, dict):
        candidates.extend(
            [
                osnap.get("current_underlying_price"),
                osnap.get("underlying_price"),
                osnap.get("underlying_last"),
                ((osnap.get("engine") or {}) if isinstance(osnap.get("engine"), dict) else {}).get(
                    "current_underlying"
                ),
                ((osnap.get("engine") or {}) if isinstance(osnap.get("engine"), dict) else {}).get(
                    "entry_underlying"
                ),
            ]
        )
    md = metadata if isinstance(metadata, dict) else None
    if md is None and position is not None:
        if isinstance(position, dict):
            md = position.get("metadata") if isinstance(position.get("metadata"), dict) else None
        else:
            raw_md = getattr(position, "metadata", None)
            md = raw_md if isinstance(raw_md, dict) else None
    if isinstance(md, dict):
        candidates.extend(
            [
                md.get("entry_underlying_price"),
                md.get("entry_underlying"),
                md.get("underlying_price"),
            ]
        )
        ps = md.get("premium_snapshot") if isinstance(md.get("premium_snapshot"), dict) else {}
        candidates.append(ps.get("underlying_price") or ps.get("entry_underlying"))
        sig = md.get("signal_snapshot") if isinstance(md.get("signal_snapshot"), dict) else {}
        candidates.append(
            sig.get("entry_underlying_price")
            or sig.get("entry_underlying")
            or sig.get("underlying_price")
        )
    if isinstance(position, dict):
        candidates.extend(
            [
                position.get("underlying_price"),
                position.get("current_underlying"),
                position.get("entry_underlying"),
            ]
        )
    else:
        for attr in ("underlying_price", "current_underlying", "entry_underlying"):
            if position is not None and hasattr(position, attr):
                candidates.append(getattr(position, attr, None))
    sym = str(symbol or "").strip().upper()
    if not sym and isinstance(position, dict):
        sym = str(position.get("symbol") or "").strip().upper()
    elif not sym and position is not None:
        sym = str(getattr(position, "symbol", "") or "").strip().upper()
    if isinstance(symbol_cache, dict) and sym:
        candidates.append(symbol_cache.get(sym))
    candidates.append(entry_underlying)

    for cand in candidates:
        px = _coerce_positive_underlying(cand)
        if px is not None:
            return float(px)
    return 0.0
