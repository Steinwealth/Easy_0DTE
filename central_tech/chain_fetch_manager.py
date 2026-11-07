"""Chain fetch manager: one shared, prioritized broker budget for option chains.

Oct 13 2026. Replaces the per-discovery-scan live chain cap (2 while open)
that starved Trendline handoffs (103 dir_vol_missing rejects on 10/13).

* Token bucket ``CHAIN_FETCH_BUDGET_PER_MIN`` chain fetches/min (burst
  ``CHAIN_FETCH_BURST``). One E*TRADE chain = 2 real Market API calls.
* Priorities: P0 open position / exit, P1 signal gate / handoff, P2 5-minute
  prior resample, P3 prewarm. Low priority (P2/P3) keeps
  ``CHAIN_FETCH_LOW_PRIORITY_RESERVE`` tokens for P0/P1 and only runs while
  the live minute (real broker + 2 x chains) is under
  ``CHAIN_FETCH_LOW_PRIORITY_SAFE_PER_MIN``. P0/P1 stop at
  ``CHAIN_FETCH_HIGH_PRIORITY_SAFE_PER_MIN``.
* Queue dedupe per symbol/expiry: one chain carries both sides; sides
  requested are merged on the queued item. A chain fresh within the TP cap
  (``TP_FREEZE_CHAIN_MAX_AGE_SEC``) is reused, never refetched.
* Every manager fetch records a chain-ring sample, so the 5m prior exists.
* Light resample: candidates seen at a gate in the last
  ``CHAIN_RESAMPLE_CANDIDATE_TTL_SEC`` get one low-priority chain each
  ``CHAIN_RESAMPLE_INTERVAL_SEC`` when there is headroom.
* Defer registry: first-defer time per (path, symbol, side) so the dir% gate
  can bound its wait (``DIR_PCT_DEFER_MAX_WAIT_SEC``).

``CHAIN_FETCH_MANAGER_ENABLED=false`` restores the old behaviour everywhere.
Nothing here polls discovery, the position monitor, or the flight recorder.
"""

from __future__ import annotations

import asyncio
import heapq
import itertools
import logging
import os
import time
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

log = logging.getLogger(__name__)

P_POSITION = 0
P_GATE = 1
P_RESAMPLE = 2
P_PREWARM = 3
PRIORITY_NAMES = {P_POSITION: "position", P_GATE: "gate", P_RESAMPLE: "resample", P_PREWARM: "prewarm"}

SYSTEM_ATTR = "_chain_fetch_manager"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _env_float(name: str, default: float) -> float:
    try:
        raw = os.getenv(name)
        return float(raw) if raw not in (None, "") else float(default)
    except (TypeError, ValueError):
        return float(default)


def manager_enabled() -> bool:
    return _env_bool("CHAIN_FETCH_MANAGER_ENABLED", True)


def budget_per_min() -> float:
    return max(1.0, _env_float("CHAIN_FETCH_BUDGET_PER_MIN", 24.0))


def burst() -> float:
    return max(1.0, _env_float("CHAIN_FETCH_BURST", 8.0))


def low_priority_reserve() -> float:
    return max(0.0, _env_float("CHAIN_FETCH_LOW_PRIORITY_RESERVE", 6.0))


def real_calls_per_chain() -> float:
    return max(1.0, _env_float("CHAIN_FETCH_REAL_CALLS_PER_CHAIN", 2.0))


def chain_calls_count_toward_cap() -> bool:
    """Live chains are recorded as real broker calls (lane=chain) toward the hard cap."""
    return _env_bool("CHAIN_CALLS_COUNT_TOWARD_HARD_CAP", True)


def adaptive_enabled() -> bool:
    return _env_bool("CHAIN_FETCH_ADAPTIVE_BUDGET", True)


def hard_cap_per_min() -> float:
    return max(1.0, _env_float("CHAIN_FETCH_HARD_CAP_PER_MIN", 140.0))


def adaptive_cap_fraction() -> float:
    return min(1.0, max(0.1, _env_float("CHAIN_FETCH_ADAPTIVE_CAP_FRACTION", 0.85)))


def adaptive_floor_per_min() -> float:
    return max(0.0, _env_float("CHAIN_FETCH_ADAPTIVE_FLOOR_PER_MIN", 6.0))


def adaptive_ceiling_per_min() -> float:
    return max(1.0, _env_float("CHAIN_FETCH_ADAPTIVE_CEILING_PER_MIN", 40.0))


def adaptive_chains_per_min(non_chain_calls_last_min: float) -> float:
    """chains/min = (fraction*cap - last minute's non-chain real calls) / calls_per_chain, clamped."""
    raw = (adaptive_cap_fraction() * hard_cap_per_min() - max(0.0, float(non_chain_calls_last_min or 0.0))) / real_calls_per_chain()
    lo = adaptive_floor_per_min()
    hi = max(lo, adaptive_ceiling_per_min())
    return min(hi, max(lo, raw))


def low_priority_safe_per_min() -> float:
    return _env_float("CHAIN_FETCH_LOW_PRIORITY_SAFE_PER_MIN", 100.0)


def high_priority_safe_per_min() -> float:
    return _env_float("CHAIN_FETCH_HIGH_PRIORITY_SAFE_PER_MIN", 130.0)


def resample_enabled() -> bool:
    return _env_bool("CHAIN_RESAMPLE_ENABLED", True)


def resample_interval_sec() -> float:
    return max(60.0, _env_float("CHAIN_RESAMPLE_INTERVAL_SEC", 285.0))


def resample_tick_sec() -> float:
    return max(2.0, _env_float("CHAIN_RESAMPLE_TICK_SEC", 15.0))


def resample_max_per_tick() -> int:
    return max(0, int(_env_float("CHAIN_RESAMPLE_MAX_PER_TICK", 3.0)))


def resample_max_per_tick_when_open() -> int:
    """P2 drain cap while any 0DTE position is open so OCC keeps the 2s tape."""
    return max(1, int(_env_float("CHAIN_RESAMPLE_MAX_PER_TICK_WHEN_OPEN", 2.0)))


def dir5m_sample_open_positions() -> bool:
    """t194u: dir5m held-position sampling (exit / 20p priors + fresh now-chain)."""
    return _env_bool("DIR5M_SAMPLE_OPEN_POSITIONS", False)


def dir5m_open_position_resample_sec() -> float:
    return max(60.0, _env_float("DIR5M_OPEN_POSITION_RESAMPLE_SEC", 300.0))


def dir5m_keepalive_dedup_sec() -> float:
    """t202u: a dir5m keep-alive / 20p-prefetch item is dropped only if the ring got a sample this recently."""
    return max(5.0, _env_float("DIR5M_KEEPALIVE_DEDUP_SEC", 45.0))


DIR5M_KEEPALIVE_REASONS = ("dir5m_keepalive", "dir5m_20p_prefetch")


def dir5m_open_position_max_per_min() -> int:
    return max(0, int(_env_float("DIR5M_OPEN_POSITION_MAX_PER_MIN", 6.0)))


DIR5M_POSITION_REASON = "dir5m_position"


# t217u (Oct 17 2026): handoff-first order inside P2. Rank 0 = armed keep-alive
# (handoff seconds away), 1 = in-band keep-alive / 20p prefetch, 2 = held-position
# dir5m sample, 3 = approach, 4 = wide keep-alive, 5 = generic resample / other.
# Same priority, lower rank first; FIFO (original enqueue order) within a rank.
P2_RANK_ARMED = 0
P2_RANK_BAND = 1
P2_RANK_DEFAULT = 5
_RANK_BY_REASON = {
    "dir5m_20p_prefetch": 1,
    DIR5M_POSITION_REASON: 2,
    "dir5m_approach": 3,
    "dir5m_keepalive": 4,
}


def p2_rank_enabled() -> bool:
    """t217u: rank queued needs inside a priority (handoff keep-alive first)."""
    return _env_bool("CHAIN_FETCH_P2_RANK_ENABLED", True)


def requeue_keep_order() -> bool:
    """t217u: a need the budget could not serve keeps its place (old: back of the queue)."""
    return _env_bool("CHAIN_FETCH_REQUEUE_KEEP_ORDER", True)


def drain_concurrency() -> int:
    """t217u: chain fetches in flight per drain step (broker pacer still spaces calls)."""
    return max(1, min(4, int(_env_float("CHAIN_FETCH_DRAIN_CONCURRENCY", 2.0))))


def rank_for_reason(reason: str) -> int:
    return int(_RANK_BY_REASON.get(str(reason or ""), P2_RANK_DEFAULT))


def candidate_ttl_sec() -> float:
    return max(60.0, _env_float("CHAIN_RESAMPLE_CANDIDATE_TTL_SEC", 900.0))


def dir_pct_defer_max_wait_sec() -> float:
    return max(0.0, _env_float("DIR_PCT_DEFER_MAX_WAIT_SEC", 90.0))


def chain_fresh_cap_sec() -> float:
    try:
        from central_tech.decision_freeze import chain_max_age_sec

        return float(chain_max_age_sec())
    except Exception:
        return max(5.0, _env_float("TP_FREEZE_CHAIN_MAX_AGE_SEC", 60.0))


def _sym(symbol: Any) -> str:
    return str(symbol or "").strip().upper()


def _defer_span(rec: Any, now: float) -> Tuple[float, float]:
    """(first, last) from a defer record. A bare float is the legacy first-only stamp."""
    if isinstance(rec, tuple) and len(rec) == 2:
        return float(rec[0]), float(rec[1])
    if rec is None:
        return now, now
    try:
        stamped = float(rec)
    except (TypeError, ValueError):
        return now, now
    return stamped, stamped


def _side(side: Any) -> str:
    s = str(side or "").strip().upper()
    if s in ("C", "CALL", "LONG", "BULL", "BULLISH"):
        return "CALL"
    if s in ("P", "PUT", "SHORT", "BEAR", "BEARISH"):
        return "PUT"
    return ""


class ChainFetchManager:
    """Per-process manager. Clock is injectable for tests."""

    def __init__(self, system: Any = None, *, clock: Callable[[], float] = time.time) -> None:
        self.system = system
        self.clock = clock
        self.rate_per_sec = budget_per_min() / 60.0
        self.capacity = burst()
        self.tokens = self.capacity
        self._last_refill = clock()
        # queue: heap of (priority, rank, seq, key); _items[key] -> dict
        # (rank is 0 for every item when CHAIN_FETCH_P2_RANK_ENABLED=false).
        self._heap: List[Tuple[int, int, int, Tuple[str, str]]] = []
        self._items: Dict[Tuple[str, str], Dict[str, Any]] = {}
        self._seq = itertools.count()
        self._candidates: Dict[str, Dict[str, Any]] = {}
        self._defers: Dict[Tuple[str, str, str], Tuple[float, float]] = {}
        self._minute_start = clock()
        self._minute_granted = 0
        self.stats: Dict[str, int] = {
            "granted": 0, "denied_tokens": 0, "denied_reserve": 0, "denied_headroom": 0,
            "deduped_fresh": 0, "queued": 0, "queue_merged": 0, "fetched": 0, "fetch_failed": 0,
            "ring_recorded": 0,
        }
        # Adaptive budget (Oct 13 2026): rate follows last minute's non-chain calls.
        self.last_min_non_chain_calls: Optional[float] = None
        self.adaptive_rate_per_min: float = budget_per_min()
        self.max_granted_in_minute = 0
        self._sampler_task: Optional[asyncio.Task] = None
        # t194u: rolling 60 s enqueue times of dir5m held-position samples.
        self._held_q: List[float] = []

    # ------------------------------------------------------------------ budget
    def _refill(self, now: float) -> None:
        dt = max(0.0, now - self._last_refill)
        self.tokens = min(self.capacity, self.tokens + dt * self.rate_per_sec)
        self._last_refill = now
        if now - self._minute_start >= 60.0:
            self.max_granted_in_minute = max(self.max_granted_in_minute, self._minute_granted)
            self._minute_start = now
            self._minute_granted = 0

    def _current_minute_split(self) -> Tuple[float, float]:
        """(non_chain_real_calls, chain_real_calls) in the live accounting minute."""
        sys_ = self.system
        if sys_ is None:
            return 0.0, 0.0
        try:
            from modules.option_api_accounting import snapshot_usage

            snap = snapshot_usage(sys_)
        except Exception:
            snap = {}
        real = float(snap.get("real_broker_total") or 0)
        chain_lane = float(snap.get("broker_chain_requests") or 0)
        if chain_calls_count_toward_cap():
            return max(0.0, real - chain_lane), chain_lane
        try:
            chains = float(getattr(sys_, "_option_api_usage_chain_calls", 0) or 0)
        except Exception:
            chains = 0.0
        return max(0.0, real - chain_lane), chains * real_calls_per_chain()

    def observe_minute(self, non_chain_calls: float) -> float:
        """Feed last minute's non-chain real calls; returns the new chains/min rate."""
        self.last_min_non_chain_calls = max(0.0, float(non_chain_calls or 0.0))
        if adaptive_enabled():
            self.adaptive_rate_per_min = adaptive_chains_per_min(self.last_min_non_chain_calls)
        else:
            self.adaptive_rate_per_min = budget_per_min()
        self.rate_per_sec = self.adaptive_rate_per_min / 60.0
        log.info(
            "CHAIN_FETCH_MGR | action=adaptive_budget | non_chain_last_min=%.0f | chains_per_min=%.1f | "
            "grep=CHAIN_FETCH_MGR",
            self.last_min_non_chain_calls, self.adaptive_rate_per_min,
        )
        return self.adaptive_rate_per_min

    def _adapt_from_minute(self) -> None:
        if not adaptive_enabled() or self.system is None:
            return
        non_chain, _chain = self._current_minute_split()
        self.observe_minute(non_chain)

    def _live_minute_real_calls(self) -> float:
        """Real broker calls this minute incl. chains (chains are not in real_broker_total)."""
        sys_ = self.system
        if sys_ is None:
            return 0.0
        real = 0.0
        try:
            from modules.option_api_accounting import snapshot_usage

            real = float(snapshot_usage(sys_).get("real_broker_total") or 0)
        except Exception:
            real = 0.0
        if chain_calls_count_toward_cap():
            return real  # chains are already in real_broker_total (lane=chain)
        try:
            chains = float(getattr(sys_, "_option_api_usage_chain_calls", 0) or 0)
        except Exception:
            chains = 0.0
        return real + chains * real_calls_per_chain()

    def _hard_cap_throttling(self) -> bool:
        try:
            return float(getattr(self.system, "_options_api_hard_cap_extra_sec", 0.0) or 0.0) > 0.0
        except Exception:
            return False

    def _flat(self) -> bool:
        try:
            from modules.open_position_quote_orchestration import open_0dte_count

            return int(open_0dte_count(self.system) or 0) <= 0
        except Exception:
            return False

    def try_acquire(self, priority: int, symbol: str = "", *, reason: str = "") -> Tuple[bool, str]:
        now = self.clock()
        self._refill(now)
        live = self._live_minute_real_calls()
        per = real_calls_per_chain()
        if priority <= P_GATE:
            if live + per > high_priority_safe_per_min() or self._hard_cap_throttling():
                self.stats["denied_headroom"] += 1
                return False, "headroom"
            need = 1.0
        else:
            if live + per > low_priority_safe_per_min() or self._hard_cap_throttling():
                self.stats["denied_headroom"] += 1
                return False, "headroom"
            need = 1.0 + low_priority_reserve()
            if priority == P_PREWARM and self._flat() and _env_bool("CHAIN_FETCH_PREWARM_FLAT_BYPASS", True):
                # No open positions: prewarm keeps its pre-manager pace, bounded
                # only by the live-minute headroom check above (no token spend).
                self.stats["granted"] += 1
                return True, "granted_flat"
        if self.tokens < need:
            key = "denied_tokens" if priority <= P_GATE else "denied_reserve"
            self.stats[key] += 1
            return False, "tokens" if priority <= P_GATE else "reserve"
        self.tokens -= 1.0
        self._minute_granted += 1
        self.max_granted_in_minute = max(self.max_granted_in_minute, self._minute_granted)
        self.stats["granted"] += 1
        log.debug(
            "CHAIN_FETCH_MGR | action=grant | priority=%s | symbol=%s | reason=%s | tokens=%.2f",
            PRIORITY_NAMES.get(priority, priority), symbol, reason, self.tokens,
        )
        return True, "granted"

    def refund(self) -> None:
        """Return a granted token that was not used (deduped inside the lock)."""
        self.tokens = min(self.capacity, self.tokens + 1.0)
        self._minute_granted = max(0, self._minute_granted - 1)
        self.stats["granted"] = max(0, self.stats["granted"] - 1)

    def seconds_until_token(self, priority: int = P_GATE) -> float:
        need = 1.0 if priority <= P_GATE else 1.0 + low_priority_reserve()
        self._refill(self.clock())
        if self.tokens >= need:
            return 0.0
        return (need - self.tokens) / max(1e-9, self.rate_per_sec)

    # ------------------------------------------------------------------ queue
    def enqueue(
        self,
        symbol: str,
        *,
        priority: int,
        expiry: Optional[str] = None,
        side: Any = None,
        spot: Optional[float] = None,
        reason: str = "",
        rank: Optional[int] = None,
    ) -> bool:
        """Queue one chain need. Returns True when a new item was created.

        ``rank`` (t217u) orders needs inside one priority (lower first); default
        from the reason (see ``_RANK_BY_REASON``). A merge keeps the item's
        original place and only ever moves it forward.
        """
        sym = _sym(symbol)
        if not sym:
            return False
        key = (sym, str(expiry or "0dte"))
        side_n = _side(side)
        rk = int(rank) if rank is not None else rank_for_reason(reason)
        if not p2_rank_enabled():
            rk = 0
        item = self._items.get(key)
        if item is not None:
            self.stats["queue_merged"] += 1
            if side_n:
                item["sides"].add(side_n)
            if spot:
                item["spot"] = float(spot)
            item["reasons"].add(reason or PRIORITY_NAMES.get(priority, "?"))
            old_p, old_r = item["priority"], item.get("rank", 0)
            new_p = min(int(priority), old_p)
            if int(priority) < old_p:
                new_r = rk
            elif int(priority) == old_p:
                new_r = min(rk, old_r)
            else:
                new_r = old_r
            if (new_p, new_r) != (old_p, old_r):
                item["priority"], item["rank"] = new_p, new_r
                heapq.heappush(self._heap, (new_p, new_r, item["seq"], key))
            return False
        seq = next(self._seq)
        self._items[key] = {
            "symbol": sym, "expiry": expiry, "priority": int(priority), "rank": rk, "seq": seq,
            "sides": {side_n} if side_n else set(), "spot": float(spot) if spot else None,
            "reasons": {reason or PRIORITY_NAMES.get(priority, "?")}, "enq_t": self.clock(),
        }
        heapq.heappush(self._heap, (int(priority), rk, seq, key))
        self.stats["queued"] += 1
        return True

    def _live_entry(self, entry: Tuple[int, int, int, Tuple[str, str]]) -> bool:
        prio, rk, seq, key = entry
        item = self._items.get(key)
        return (item is not None and item["priority"] == prio and item.get("rank", 0) == rk
                and item.get("seq") == seq)

    def pop_next(self) -> Optional[Dict[str, Any]]:
        while self._heap:
            entry = heapq.heappop(self._heap)
            if not self._live_entry(entry):
                continue  # stale heap entry (priority / rank was raised)
            item = self._items.pop(entry[3])
            return item
        return None

    def _requeue_held(self, item: Dict[str, Any]) -> None:
        """t217u: put an unserved need back at its original place (same seq / rank)."""
        key = (item["symbol"], str(item.get("expiry") or "0dte"))
        cur = self._items.get(key)
        if cur is None:
            self._items[key] = item
            heapq.heappush(self._heap, (item["priority"], item.get("rank", 0), item["seq"], key))
            return
        # Re-queued meanwhile (concurrent drain): merge into the better of the two.
        cur["reasons"].update(item.get("reasons") or ())
        cur["sides"].update(item.get("sides") or ())
        if item.get("spot") and not cur.get("spot"):
            cur["spot"] = item["spot"]
        better = (item["priority"], item.get("rank", 0), item["seq"]) < (cur["priority"], cur.get("rank", 0), cur["seq"])
        if better:
            cur["priority"], cur["rank"], cur["seq"] = item["priority"], item.get("rank", 0), item["seq"]
            cur["enq_t"] = min(float(cur.get("enq_t") or 0.0), float(item.get("enq_t") or 0.0)) or cur.get("enq_t")
            heapq.heappush(self._heap, (cur["priority"], cur["rank"], cur["seq"], key))

    def peek_priority(self) -> Optional[int]:
        while self._heap:
            if not self._live_entry(self._heap[0]):
                heapq.heappop(self._heap)
                continue
            return self._heap[0][0]
        return None

    def queued(self, symbol: str, expiry: Optional[str] = None) -> bool:
        return (_sym(symbol), str(expiry or "0dte")) in self._items

    def __len__(self) -> int:
        return len(self._items)

    # ------------------------------------------------------------------ candidates / defers
    def note_candidate(self, symbol: str, *, spot: Optional[float] = None, side: Any = None) -> None:
        sym = _sym(symbol)
        if not sym:
            return
        c = self._candidates.setdefault(sym, {"sides": set()})
        c["t"] = self.clock()
        if spot:
            try:
                if float(spot) > 0:
                    c["spot"] = float(spot)
            except (TypeError, ValueError):
                pass
        s = _side(side)
        if s:
            c["sides"].add(s)

    def note_defer(
        self,
        path: str,
        symbol: str,
        side: Any,
        *,
        restart_after_sec: Optional[float] = None,
    ) -> float:
        """Seconds since the first defer of this key.

        A gap longer than ``restart_after_sec`` is the scanner being away, not
        90s of chain attempts. The clock starts again on this visit (CAKE
        2026-10-30: waited_sec=194.9 on the visit that finally fetched).
        """
        key = (str(path or ""), _sym(symbol), _side(side))
        now = self.clock()
        rec = self._defers.get(key)
        first, last = _defer_span(rec, now)
        if (
            restart_after_sec is not None
            and rec is not None
            and (now - last) > float(restart_after_sec)
        ):
            log.info(
                "CHAIN_FETCH_MGR | stage=dir_pct_defer | action=clock_restart | "
                "path=%s | symbol=%s | side=%s | gap_sec=%.1f | grep=DIR_PCT_DEFER",
                str(path or ""),
                _sym(symbol),
                _side(side),
                now - last,
            )
            first = now
        self._defers[key] = (first, now)
        return max(0.0, now - first)

    def clear_defer(self, path: str, symbol: str, side: Any) -> None:
        self._defers.pop((str(path or ""), _sym(symbol), _side(side)), None)

    def defer_elapsed(self, path: str, symbol: str, side: Any) -> Optional[float]:
        rec = self._defers.get((str(path or ""), _sym(symbol), _side(side)))
        if rec is None:
            return None
        first, _last = _defer_span(rec, self.clock())
        return max(0.0, self.clock() - first)

    # ------------------------------------------------------------------ freshness
    @staticmethod
    def chain_age(chain_mgr: Any, symbol: str, spot: Optional[float] = None) -> Optional[float]:
        if chain_mgr is None:
            return None
        try:
            from central_tech.decision_freeze import chain_age_sec

            return chain_age_sec(chain_mgr, symbol, spot)
        except Exception:
            pass
        try:
            return chain_mgr.cached_chain_age_seconds(symbol)
        except Exception:
            return None

    def ring_latest_age(self, symbol: str) -> Optional[float]:
        ring = getattr(self.system, "_po_chain_skew_ring", None) if self.system is not None else None
        if not isinstance(ring, dict):
            try:
                from central_tech.chain_ring import _ensure_chain_skew_ring

                ring = _ensure_chain_skew_ring(self.system)
            except Exception:
                ring = None
        buf = ring.get(_sym(symbol)) if isinstance(ring, dict) else None
        if not buf:
            return None
        try:
            return max(0.0, self.clock() - max(float(s.get("t") or 0.0) for s in buf if isinstance(s, dict)))
        except Exception:
            return None

    def _spot_for(self, sym: str, hint: Optional[float]) -> Optional[float]:
        if hint:
            return float(hint)
        c = self._candidates.get(sym) or {}
        if c.get("spot"):
            return float(c["spot"])
        cache = getattr(self.system, "_session_tech_bar_cache", None) if self.system is not None else None
        bars = cache.get(sym) if isinstance(cache, dict) else None
        if bars:
            last = bars[-1]
            for k in ("close", "c", "last"):
                try:
                    v = last.get(k) if isinstance(last, dict) else getattr(last, k, None)
                    if v is not None and float(v) > 0:
                        return float(v)
                except Exception:
                    continue
        return None

    def _p2_allow_stale_sec(self, reasons: Any) -> float:
        """How old a cached chain may be for a dir5m / resample (P2/P3) read."""
        reasons = set(reasons or ())
        if any(r in reasons for r in DIR5M_KEEPALIVE_REASONS):
            try:
                from central_tech.dir5m_presample import keepalive_sec

                return float(keepalive_sec())
            except Exception:
                return 210.0
        if DIR5M_POSITION_REASON in reasons:
            return float(dir5m_open_position_resample_sec())
        return float(resample_interval_sec())

    # ------------------------------------------------------------------ fetch
    async def fetch_item(self, chain_mgr: Any, item: Dict[str, Any], *, fetch_skew: Any = None) -> Dict[str, Any]:
        """Fetch one queued chain (budget must already be granted) and record the ring sample."""
        sym = item["symbol"]
        spot = self._spot_for(sym, item.get("spot"))
        if not spot or chain_mgr is None:
            self.stats["fetch_failed"] += 1
            return {}
        if fetch_skew is None:
            from easy0DTE.modules.convex_chain_skew import fetch_chain_skew_for_symbol as fetch_skew
        age = self.chain_age(chain_mgr, sym, spot)
        pri = int(item.get("priority") or P_RESAMPLE)
        # P0/P1 (exit / admit): refresh when older than the TP freeze cap.
        # P2/P3 (dir5m / resample / prewarm): reuse cache for the cadence —
        # force_live here was re-walking empty expiries every 60s and stalling OCC.
        quotes_ok = True
        if pri <= P_GATE:
            try:
                from easy0DTE.modules.convex_chain_skew import (
                    chain_has_two_sided_quotes,
                    peek_cached_options_chain,
                )

                cached = peek_cached_options_chain(chain_mgr, sym, spot)
                quotes_ok = chain_has_two_sided_quotes(cached) if cached is not None else False
            except Exception:
                quotes_ok = False
        force_live = bool(
            pri <= P_GATE
            and (
                age is None
                or age > chain_fresh_cap_sec()
                or not quotes_ok
            )
        )
        allow_stale = None if pri <= P_GATE else self._p2_allow_stale_sec(item.get("reasons"))
        try:
            try:
                skew = await fetch_skew(
                    chain_mgr,
                    symbol=sym,
                    underlying_price=float(spot),
                    system=self.system,
                    force_live=force_live,
                    allow_stale_cache_sec=allow_stale,
                )
            except TypeError:
                skew = await fetch_skew(
                    chain_mgr,
                    symbol=sym,
                    underlying_price=float(spot),
                    system=self.system,
                    force_live=force_live,
                )
        except Exception as exc:
            self.stats["fetch_failed"] += 1
            log.debug("CHAIN_FETCH_MGR | action=fetch_failed | symbol=%s | err=%s", sym, exc)
            return {}
        if not isinstance(skew, dict) or not (skew.get("call_chain_volume") or skew.get("put_chain_volume")):
            self.stats["fetch_failed"] += 1
            return {}
        self.stats["fetched"] += 1
        # t166u F1: stamp the ring sample at the chain's real fetch time (asof),
        # never the reuse clock. A cached P2 read older than 60s must still
        # carry the original observation or dir% priors look younger than they are.
        obs_ts = None
        try:
            age_after = self.chain_age(chain_mgr, sym, spot)
            if age_after is not None and float(age_after) >= 0:
                obs_ts = self.clock() - float(age_after)
                skew["tp_chain_observed_epoch"] = float(obs_ts)
                if float(age_after) > 0.5:
                    skew["chain_fetch_source"] = "cache"
        except Exception:
            obs_ts = None
        self.record_ring(sym, skew, spot=spot, sides=item.get("sides") or set(), obs_ts=obs_ts)
        return skew

    def record_ring(
        self,
        sym: str,
        skew: Dict[str, Any],
        *,
        spot: Optional[float],
        sides: Set[str],
        obs_ts: Optional[float] = None,
    ) -> None:
        if self.system is None:
            return
        try:
            from central_tech.chain_ring import record_chain_skew_sample

            sample = {k: skew.get(k) for k in (
                "call_chain_volume", "put_chain_volume", "representative_volume", "cumulative_volume",
                "open_interest", "representative_open_interest", "representative_bid",
                "representative_ask", "representative_strike", "iv", "delta") if skew.get(k) is not None}
            if spot:
                sample["underlying_price"] = float(spot)
            # Side-specific dir% only when exactly one side is known (no invented share).
            if len(sides) == 1:
                sample["option_side"] = next(iter(sides)).lower()
            for k in ("selected_expiry", "expiry", "expiry_ymd", "tp_chain_observed_epoch"):
                if skew.get(k) is not None:
                    sample[k] = skew.get(k)
            ts = self.clock()
            if obs_ts is not None:
                ts = min(float(ts), float(obs_ts))
                sample["tp_chain_observed_epoch"] = float(obs_ts)
            record_chain_skew_sample(self.system, sym, sample, ts=ts)
            self.stats["ring_recorded"] += 1
        except Exception as exc:
            log.debug("CHAIN_FETCH_MGR | action=ring_record_failed | symbol=%s | err=%s", sym, exc)

    async def drain(
        self,
        chain_mgr: Any,
        *,
        max_n: int,
        fetch_skew: Any = None,
        max_priority: Optional[int] = None,
    ) -> int:
        """Serve queued needs in priority order under the bucket. Returns fetches done.

        ``max_priority`` keeps worse priorities queued (no broker call). Used when
        exit quotes are already missing the 2s tape so discovery resamples wait.
        """
        done = 0
        held: List[Dict[str, Any]] = []
        deferred: List[Dict[str, Any]] = []
        # t217u / 2c06b26: two in flight, up to 6 per tick, handoff-first rank.
        # Do not serialize just because the Wide book is open — that is the 10/16
        # shortage. Yield after each batch so OCC still gets the 2s tape.
        conc = drain_concurrency()
        open_syms = self.open_position_symbols()
        stop = False
        while done < max_n and not stop:
            # t217u: up to CHAIN_FETCH_DRAIN_CONCURRENCY granted needs per step,
            # fetched together (each one still took its own token / headroom check).
            batch: List[Dict[str, Any]] = []
            while len(batch) < min(conc, max_n - done):
                item = self.pop_next()
                if item is None:
                    stop = True
                    break
                pri = item.get("priority")
                if pri is None:
                    pri = P_RESAMPLE
                if max_priority is not None and int(pri) > int(max_priority):
                    deferred.append(item)
                    continue
                sym = item["symbol"]
                spot = self._spot_for(sym, item.get("spot"))
                age = self.chain_age(chain_mgr, sym, spot)
                if age is not None and age <= chain_fresh_cap_sec() and item["priority"] <= P_GATE:
                    self.stats["deduped_fresh"] += 1
                    continue
                if item["priority"] >= P_RESAMPLE or item["reasons"] == {"position_5m"}:
                    ring_age = self.ring_latest_age(sym)
                    fresh_s = resample_interval_sec()
                    if DIR5M_POSITION_REASON in (item.get("reasons") or ()):
                        fresh_s = min(fresh_s, dir5m_open_position_resample_sec())
                    if any(r in (item.get("reasons") or ()) for r in DIR5M_KEEPALIVE_REASONS):
                        # t202u: the keep-alive cadence (210 s) is below the generic 285 s dedupe.
                        fresh_s = min(fresh_s, dir5m_keepalive_dedup_sec())
                    if ring_age is not None and ring_age < fresh_s:
                        self.stats["deduped_fresh"] += 1
                        continue
                ok, _why = self.try_acquire(item["priority"], sym, reason="drain")
                if not ok:
                    held.append(item)
                    stop = True
                    break
                batch.append(item)
            if not batch:
                break
            if len(batch) == 1:
                await self.fetch_item(chain_mgr, batch[0], fetch_skew=fetch_skew)
            else:
                self.stats["drain_parallel"] = self.stats.get("drain_parallel", 0) + 1
                await asyncio.gather(*(self.fetch_item(chain_mgr, it, fetch_skew=fetch_skew) for it in batch),
                                     return_exceptions=True)
            done += len(batch)
            if open_syms:
                try:
                    from modules.open_position_quote_orchestration import yield_to_open_position_monitor

                    await yield_to_open_position_monitor(self.system)
                except Exception:
                    await asyncio.sleep(0)
        for item in deferred:
            self._requeue_held(item)
        if requeue_keep_order():
            for item in held:
                self._requeue_held(item)
            return done
        for item in held:  # put back what the budget could not serve yet
            self.enqueue(item["symbol"], priority=item["priority"], expiry=item.get("expiry"),
                         spot=item.get("spot"), reason="requeue", rank=item.get("rank"))
            _rk = (item["symbol"], str(item.get("expiry") or "0dte"))
            if _rk in self._items:  # t194u: keep the original reasons (per-reason dedupe)
                self._items[_rk]["reasons"].update(item.get("reasons") or ())
            for s in item.get("sides") or ():
                self._items[(item["symbol"], str(item.get("expiry") or "0dte"))]["sides"].add(s)
        return done

    def open_position_symbols(self) -> Set[str]:
        """Underlyings of open 0DTE positions (read-only view of the monitor)."""
        out: Set[str] = set()
        mon = getattr(self.system, "_unified_0dte_position_monitor", None) if self.system is not None else None
        if mon is None and self.system is not None and dir5m_sample_open_positions():
            # t194u: nothing sets ``system._unified_0dte_position_monitor``; the live
            # monitor is the module singleton. Read it (never create one here).
            try:
                from modules import unified_0dte_position_monitor as _u0m

                mon = getattr(_u0m, "_MONITOR", None)
            except Exception:
                mon = None
        if mon is None or not hasattr(mon, "_collect_open_positions"):
            return out
        try:
            rows = mon._collect_open_positions(self.system) or []
        except Exception:
            return out
        for r in rows if isinstance(rows, (list, tuple)) else []:
            cand = r[1] if isinstance(r, tuple) and len(r) > 1 else r
            for k in ("underlying", "underlying_symbol", "symbol"):
                v = cand.get(k) if isinstance(cand, dict) else getattr(cand, k, None)
                v = _sym(v)
                if v and v.isalpha() and len(v) <= 6:
                    out.add(v)
                    break
        return out

    def enqueue_due_resamples(self) -> int:
        """Queue P2 resamples for recent candidates whose ring prior is about to age out."""
        if not resample_enabled():
            return 0
        now = self.clock()
        ttl = candidate_ttl_sec()
        interval = resample_interval_sec()
        n = 0
        # t194u: dir5m held-position sampling. P2 (behind the P0/P1 reserve, same
        # bucket), 1 per symbol per DIR5M_OPEN_POSITION_RESAMPLE_SEC, at most
        # DIR5M_OPEN_POSITION_MAX_PER_MIN per rolling 60 s so candidates are not starved.
        # Every chain read (gate / sampler / prewarm) already lands in the ring at
        # zero extra cost; this only fills the gap for symbols nobody else reads.
        if dir5m_sample_open_positions():
            n += self.enqueue_open_position_samples(now)
        # Held positions first (20p / exit snapshots read the 5m ring prior).
        elif _env_bool("CHAIN_RESAMPLE_OPEN_POSITIONS", True):
            for sym in sorted(self.open_position_symbols()):
                ring_age = self.ring_latest_age(sym)
                if ring_age is None or ring_age >= interval:
                    if self.enqueue(sym, priority=P_POSITION, reason="position_5m"):
                        n += 1
        for sym, c in list(self._candidates.items()):
            if now - float(c.get("t") or 0.0) > ttl:
                self._candidates.pop(sym, None)
                continue
            ring_age = self.ring_latest_age(sym)
            if ring_age is None or ring_age >= interval:
                sides = c.get("sides") or set()
                if self.enqueue(sym, priority=P_RESAMPLE, spot=c.get("spot"),
                                side=next(iter(sides)) if len(sides) == 1 else None,
                                reason="resample_5m"):
                    n += 1
        return n

    def enqueue_open_position_samples(self, now: Optional[float] = None) -> int:
        t = float(self.clock() if now is None else now)
        self._held_q = [x for x in self._held_q if t - x <= 60.0]
        cap = dir5m_open_position_max_per_min()
        interval = dir5m_open_position_resample_sec()
        due = []
        for sym in self.open_position_symbols():
            ring_age = self.ring_latest_age(sym)
            if ring_age is None or ring_age >= interval:
                due.append((-(ring_age if ring_age is not None else 1e9), sym))
        n = 0
        for _neg_age, sym in sorted(due):  # oldest ring sample first
            if len(self._held_q) >= cap:
                self.stats["dir5m_position_capped"] = self.stats.get("dir5m_position_capped", 0) + 1
                break
            if self._items.get((sym, "0dte")) is not None:
                self._items[(sym, "0dte")]["reasons"].add(DIR5M_POSITION_REASON)
                continue
            if self.enqueue(sym, priority=P_RESAMPLE, reason=DIR5M_POSITION_REASON):
                self._held_q.append(t)
                self.stats["dir5m_position_queued"] = self.stats.get("dir5m_position_queued", 0) + 1
                n += 1
        return n

    async def sampler_tick(self, chain_mgr: Any = None, *, fetch_skew: Any = None) -> int:
        if chain_mgr is None:
            try:
                from easy0DTE.modules.convex_chain_skew import resolve_options_chain_manager

                chain_mgr = resolve_options_chain_manager(self.system)
            except Exception:
                chain_mgr = None
        if chain_mgr is None:
            return 0
        self.enqueue_due_resamples()
        if not len(self):
            return 0
        # Exits own the broker: give the 2s monitor its turn before a chain call.
        try:
            from modules.open_position_quote_orchestration import yield_to_open_position_monitor

            await yield_to_open_position_monitor(self.system)
        except Exception:
            pass
        cap = max(0, resample_max_per_tick())
        max_priority: Optional[int] = None
        # Equity discovery is not deferred here. When the exit quote itself is
        # stale, cap this tick to one position chain so a queued discovery
        # resample cannot sit on the exit loop.
        if not self._flat():
            allow, reason = True, ""
            try:
                from modules.open_position_quote_orchestration import (
                    open_quote_budget_allows_discovery,
                )

                allow, reason = open_quote_budget_allows_discovery(self.system)
            except Exception:
                allow, reason = True, ""
            if not allow:
                cap = 1
                max_priority = P_POSITION
                now_wall = time.time()
                last_log = float(getattr(self, "_sampler_defer_log_ts", 0.0) or 0.0)
                if now_wall - last_log >= 30.0:
                    self._sampler_defer_log_ts = now_wall
                    log.info(
                        "CHAIN_FETCH_MGR | action=sampler_defer_discovery | reason=%s | "
                        "cap=%d | queued=%d | grep=CHAIN_FETCH_MGR",
                        reason or "open_quote_unhealthy",
                        cap,
                        len(self),
                    )
        if cap <= 0:
            return 0
        return await self.drain(
            chain_mgr,
            max_n=cap,
            fetch_skew=fetch_skew,
            max_priority=max_priority,
        )

    def ensure_sampler(self) -> None:
        """Start the low-priority sampler loop once (no-op without a running loop)."""
        if not resample_enabled() or not manager_enabled():
            return
        if self._sampler_task is not None and not self._sampler_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return

        async def _run() -> None:
            while manager_enabled():
                try:
                    n = await self.sampler_tick()
                    if n:
                        log.info(
                            "CHAIN_FETCH_MGR | action=sampler_tick | fetched=%d | queued=%d | tokens=%.1f | "
                            "candidates=%d | grep=CHAIN_FETCH_MGR",
                            n, len(self), self.tokens, len(self._candidates),
                        )
                except Exception as exc:
                    log.debug("CHAIN_FETCH_MGR | sampler_error=%s", exc)
                await asyncio.sleep(resample_tick_sec())

        self._sampler_task = loop.create_task(_run())


def get_manager(system: Any) -> Optional[ChainFetchManager]:
    """The process manager (None when disabled)."""
    if not manager_enabled():
        return None
    if system is None:
        return None
    mgr = getattr(system, SYSTEM_ATTR, None)
    if isinstance(mgr, ChainFetchManager):
        return mgr
    mgr = ChainFetchManager(system)
    try:
        setattr(system, SYSTEM_ATTR, mgr)
    except Exception:
        return mgr
    return mgr


def qg_live_chain_cap_ceiling() -> int:
    """With the manager on, the per-scan cap is only a ceiling (env override)."""
    return max(1, int(_env_float("0DTE_QG_LIVE_CHAIN_MAX_PER_SCAN_WHILE_OPEN", 24.0)))
