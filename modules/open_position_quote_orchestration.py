"""Prioritized open-position exact-quote orchestration for the unified 0DTE monitor.

One monitor tick:
  1. Collect open 0DTE position contracts first.
  2. Add shadow continuations second.
  3. Canonicalize / dedupe by structured contract identity.
  4. Run one ``fetch_exact_contract_quotes`` batch.
  5. Share the owned snapshot with exit monitoring.
  6. If any owned open-position contract is still missing, run one isolated
     single-contract recovery task (not a duplicate full-batch refresh).
  7. After owned evaluate, quote winner leftover OCC on a single-flight
     task off the owned lock (exit-tuning tape; not in ``total_cycle_ms``).

When any 0DTE position is open, owned exact OCC quotes win over Trendline
universe polling, full-chain bridge, shadow-only hydration, and non-critical scans.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

log = logging.getLogger(__name__)

# System attribute: open-position exact quotes currently own the broker quote budget.
OPEN_QUOTE_PRIORITY_ATTR = "_0dte_open_exact_quote_priority"
OPEN_QUOTE_PRIORITY_UNTIL_ATTR = "_0dte_open_exact_quote_priority_until_mono"
PRIMARY_QUOTE_TICK_ATTR = "_0dte_primary_exact_quote_tick_id"
PRIMARY_QUOTE_SYMBOLS_ATTR = "_0dte_primary_exact_quote_symbols"
OPEN_QUOTE_HEALTH_ATTR = "_0dte_open_exact_quote_health"
QG_LIVE_CHAIN_BUDGET_ATTR = "_qg_live_chain_budget_remaining"
QG_LIVE_CHAIN_CAP_ATTR = "_qg_live_chain_budget_cap"


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


@dataclass
class PrioritizedExactQuotePlan:
    """Ordered, identity-deduped symbols for one tick's exact-quote batch."""

    symbols: List[str] = field(default_factory=list)
    open_symbols: List[str] = field(default_factory=list)
    shadow_symbols: List[str] = field(default_factory=list)
    dropped_duplicates: int = 0
    open_identity_keys: List[str] = field(default_factory=list)


def structured_identity_key(symbol: str) -> Optional[str]:
    """Stable key for canonicalize/dedupe (underlier|exp|cp|strike)."""
    try:
        from modules.option_contract_identity import identity_from_any_option_symbol

        ident = identity_from_any_option_symbol(str(symbol or "").strip())
    except Exception:
        ident = None
    if ident is None:
        return None
    return (
        f"{ident.underlying}|{ident.expiration}|{ident.call_put}|"
        f"{round(float(ident.strike), 6)}"
    )


def _dedupe_key(symbol: str) -> str:
    s = str(symbol or "").strip()
    if not s:
        return ""
    return structured_identity_key(s) or s.upper()


def collect_open_position_quote_symbols(contracts: Sequence[Any]) -> List[str]:
    """Preferred wire/OCC symbols for owned open positions (order preserved)."""
    out: List[str] = []
    seen: Set[str] = set()
    for c in contracts or []:
        extras = getattr(c, "extras", None)
        extras_d = extras if isinstance(extras, dict) else {}
        sym = str(
            extras_d.get("broker_quote_request_symbol")
            or extras_d.get("osi_key")
            or getattr(c, "option_symbol", "")
            or ""
        ).strip()
        if not sym:
            continue
        key = _dedupe_key(sym)
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(sym)
    return out


def collect_shadow_continuation_symbols(continuations: Sequence[Dict[str, Any]]) -> List[str]:
    """Shadow / PO continuation contract symbols (order preserved)."""
    out: List[str] = []
    seen: Set[str] = set()
    for continuation in continuations or []:
        if not isinstance(continuation, dict):
            continue
        candidates: List[str] = []
        for option_symbol in continuation.get("option_symbols") or []:
            candidates.append(str(option_symbol or "").strip())
        legs = continuation.get("legs") if isinstance(continuation.get("legs"), list) else []
        for leg in legs:
            if not isinstance(leg, dict):
                continue
            candidates.append(
                str(
                    leg.get("broker_quote_request_symbol")
                    or leg.get("osi_key")
                    or leg.get("broker_occ_symbol")
                    or leg.get("option_symbol")
                    or ""
                ).strip()
            )
        for sym in candidates:
            if not sym:
                continue
            key = _dedupe_key(sym)
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(sym)
    return out


# Default: do not mix closed-trade PO shadow OCC into the 2s exit quote batch.
DEFAULT_MAX_SHADOW_WHILE_OPEN = 0
SHADOW_ROTATION_ATTR = "_0dte_shadow_quote_rotation_offset"
OPEN_COUNT_ATTR = "_0dte_open_position_count_for_quote_priority"


def max_shadow_quotes_while_open() -> int:
    """Shadow OCC slots allowed in the exact batch while any position is open."""
    return max(0, _env_int("0DTE_MAX_SHADOW_QUOTES_WHILE_OPEN", DEFAULT_MAX_SHADOW_WHILE_OPEN))


def cap_shadow_symbols_while_open(
    shadow_option_symbols: Sequence[str],
    *,
    open_count: int,
    max_shadow: int = DEFAULT_MAX_SHADOW_WHILE_OPEN,
    rotation_offset: int = 0,
) -> Tuple[List[str], int, int]:
    """Fair-rotate and cap shadow OCC when open positions own the quote budget.

    Returns (capped_symbols, deferred_count, next_rotation_offset).
    """
    shadows = [str(s).strip() for s in (shadow_option_symbols or []) if str(s or "").strip()]
    if int(open_count) <= 0:
        return shadows, 0, int(rotation_offset or 0)
    if int(max_shadow) <= 0:
        return [], len(shadows), int(rotation_offset or 0)
    if len(shadows) <= int(max_shadow):
        return shadows, 0, int(rotation_offset or 0)
    n = len(shadows)
    start = int(rotation_offset or 0) % n
    rotated = shadows[start:] + shadows[:start]
    capped = rotated[: int(max_shadow)]
    deferred = n - len(capped)
    next_off = (start + int(max_shadow)) % n
    return capped, deferred, next_off


def leftover_shadow_quote_symbols(
    shadow_option_symbols: Sequence[str],
    *,
    already_quoted: Sequence[str],
    max_shadow: int = 0,
    rotation_offset: int = 0,
) -> Tuple[List[str], int, int]:
    """Winner leftover OCC to quote after owned evaluate, off the owned lock.

    Owned symbols stay off this list. ``max_shadow<=0`` means quote every
    remaining leftover (recorder already lists winners only).
    """
    quoted_keys = {
        _dedupe_key(str(s or "").strip())
        for s in (already_quoted or [])
        if str(s or "").strip()
    }
    remaining: List[str] = []
    seen: Set[str] = set()
    for raw in shadow_option_symbols or []:
        sym = str(raw or "").strip()
        if not sym:
            continue
        key = _dedupe_key(sym)
        if not key or key in seen:
            continue
        seen.add(key)
        if key in quoted_keys:
            continue
        remaining.append(sym)
    if int(max_shadow) <= 0:
        return remaining, 0, int(rotation_offset or 0)
    if not remaining:
        return [], 0, int(rotation_offset or 0)
    return cap_shadow_symbols_while_open(
        remaining,
        open_count=1,
        max_shadow=int(max_shadow),
        rotation_offset=rotation_offset,
    )


def leftover_underlying_symbols(
    continuations: Sequence[Dict[str, Any]],
    *,
    owned_symbols: Sequence[str] = (),
) -> List[str]:
    """Equity tickers for winner leftover that are not still on the open book.

    Owned spots stay on the live critical refresh. Leftover names are fetched
    on the leftover OCC task so stall-bank has moving und + premium after exit.
    """
    owned = {
        str(s or "").strip().upper()
        for s in (owned_symbols or [])
        if str(s or "").strip()
    }
    out: List[str] = []
    seen: Set[str] = set()
    for continuation in continuations or []:
        if not isinstance(continuation, dict):
            continue
        sym = str(continuation.get("symbol") or "").strip().upper()
        if not sym or sym in owned or sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
    return out


def should_defer_po_continuation_observe(
    *,
    trigger_source: str = "",
    open_count: int = 0,
    max_open: Optional[int] = None,
) -> bool:
    """Skip leftover observe only on fill-enroll ticks or an explicit open cap.

    ``0DTE_PO_CONTINUATION_MAX_OPEN<=0`` (default) never skips for book size.
    A positive cap is a safety valve only — Aug 31 used 8 and dropped every
    winner leftover while the book was large.
    """
    if str(trigger_source or "").startswith("fill_enroll"):
        return True
    cap = (
        int(max_open)
        if max_open is not None
        else _env_int("0DTE_PO_CONTINUATION_MAX_OPEN", 0)
    )
    if cap <= 0:
        return False
    return int(open_count) >= int(cap)


def leftover_shadow_max_per_tick() -> int:
    """Winner leftover OCC after owned eval. 0 = quote every leftover (default).

    Fair-rotate only when an operator sets a positive cap. Exit-tuning review
    needs the full winner book at ~2s, not an 8-name subset.
    """
    return max(0, _env_int("0DTE_MAX_SHADOW_LEFTOVER_PER_TICK", 0))


def leftover_skip_when_owned_slow() -> bool:
    """Default on so leftover OCC cannot occupy the Market lane while names are open.

    Leftover is the same ``occ_fetch`` inflight as owned exact quotes. A 4-name
    leftover at 13s makes the next owned ticks ``exact_ok=0``. This flag only
    skips leftover winner OCC. It does not pause owned OCC, UL, PATH_V2
    evaluate, or the 10s Discovery equity rotate. Set
    ``0DTE_LEFTOVER_SHADOW_SKIP_WHEN_OWNED_SLOW=false`` to restore leftover
    while the book is open.
    """
    raw = str(os.getenv("0DTE_LEFTOVER_SHADOW_SKIP_WHEN_OWNED_SLOW") or "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def should_skip_leftover_quotes_this_tick(
    *,
    last_cycle_ms: float = 0.0,
    eval_budget_hit: bool = False,
    skip_cycle_ms: Optional[float] = None,
    open_count: int = 0,
) -> bool:
    """Skip leftover OCC while the live book needs the Market lane.

    Default: skip whenever any owned name is open, or the last owned cycle
    already missed the 2s spine. Leftover still runs when the book is empty.
    """
    if not leftover_skip_when_owned_slow():
        return False
    if eval_budget_hit:
        return True
    if int(open_count or 0) > 0:
        return True
    limit = (
        float(skip_cycle_ms)
        if skip_cycle_ms is not None
        else _env_float("0DTE_LEFTOVER_SHADOW_SKIP_CYCLE_MS", 2500.0)
    )
    return float(last_cycle_ms or 0.0) > float(limit)


def leftover_occ_may_fetch(
    *,
    skip_leftover_quotes: bool,
    leftover_busy: bool,
    leftover_syms: Optional[Sequence[str]] = None,
) -> bool:
    """Owned book skip must not start leftover OCC (same occ_fetch inflight)."""
    if skip_leftover_quotes or leftover_busy:
        return False
    return bool(leftover_syms)


def build_prioritized_exact_quote_plan(
    *,
    open_option_symbols: Sequence[str],
    shadow_option_symbols: Sequence[str],
    max_shadow_while_open: Optional[int] = None,
    shadow_rotation_offset: int = 0,
) -> PrioritizedExactQuotePlan:
    """Open positions first, shadow second; dedupe by structured identity.

    When any open symbol is present, shadow symbols are fair-rotated and capped
    so post-exit PO hydration cannot crowd out live exit marks.
    """
    plan = PrioritizedExactQuotePlan()
    seen_keys: Set[str] = set()

    def _add(raw: str, *, open_lane: bool) -> None:
        sym = str(raw or "").strip()
        if not sym:
            return
        key = _dedupe_key(sym)
        if not key:
            return
        if key in seen_keys:
            plan.dropped_duplicates += 1
            return
        seen_keys.add(key)
        if open_lane:
            plan.open_symbols.append(sym)
            plan.open_identity_keys.append(key)
        else:
            plan.shadow_symbols.append(sym)

    for s in open_option_symbols or []:
        _add(s, open_lane=True)

    shadow_in = list(shadow_option_symbols or [])
    deferred = 0
    next_off = int(shadow_rotation_offset or 0)
    if plan.open_symbols:
        cap = (
            max_shadow_quotes_while_open()
            if max_shadow_while_open is None
            else max(0, int(max_shadow_while_open))
        )
        shadow_in, deferred, next_off = cap_shadow_symbols_while_open(
            shadow_in,
            open_count=len(plan.open_symbols),
            max_shadow=cap,
            rotation_offset=shadow_rotation_offset,
        )
        plan.dropped_duplicates += int(deferred)

    for s in shadow_in:
        _add(s, open_lane=False)

    plan.symbols = list(plan.open_symbols) + list(plan.shadow_symbols)
    # Stash rotation for callers via extras-compatible attribute on plan.
    setattr(plan, "shadow_deferred", int(deferred))
    setattr(plan, "shadow_rotation_next", int(next_off))
    return plan


def mark_open_quote_priority(
    system: Any,
    *,
    active: bool,
    hold_sec: float = 0.0,
    tick_id: str = "",
    open_count: int = 0,
) -> None:
    """Advertise that owned exact OCC quotes own the broker quote budget.

    While ``open_count > 0``, priority stays active for the full open duration
    (``hold_sec<=0`` / until cleared). A positive ``hold_sec`` is only a
    fallback TTL if the flag cannot be refreshed by the next healthy tick.
    """
    if system is None:
        return
    try:
        n_open = int(open_count)
        setattr(system, OPEN_COUNT_ATTR, n_open)
        on = bool(active) and n_open > 0
        setattr(system, OPEN_QUOTE_PRIORITY_ATTR, on)
        if on:
            # hold_sec<=0 → indefinite while ATTR true (full open duration).
            if float(hold_sec or 0.0) > 0:
                setattr(
                    system,
                    OPEN_QUOTE_PRIORITY_UNTIL_ATTR,
                    time.monotonic() + max(0.5, float(hold_sec)),
                )
            else:
                setattr(system, OPEN_QUOTE_PRIORITY_UNTIL_ATTR, 0.0)
            if tick_id:
                setattr(system, PRIMARY_QUOTE_TICK_ATTR, str(tick_id))
        else:
            setattr(system, OPEN_QUOTE_PRIORITY_UNTIL_ATTR, 0.0)
    except Exception:
        pass


def _system_reports_open_0dte(system: Any) -> bool:
    try:
        n = int(getattr(system, OPEN_COUNT_ATTR, 0) or 0)
        if n > 0:
            return True
    except Exception:
        pass
    try:
        mon = getattr(system, "_unified_0dte_position_monitor", None)
        if mon is not None and hasattr(mon, "_collect_open_positions"):
            return bool(mon._collect_open_positions(system))
    except Exception:
        pass
    return False


def open_quote_priority_active(system: Any) -> bool:
    """True when non-critical quote lanes should yield to open-position OCC."""
    if system is None:
        return False
    try:
        if bool(getattr(system, OPEN_QUOTE_PRIORITY_ATTR, False)):
            until = float(getattr(system, OPEN_QUOTE_PRIORITY_UNTIL_ATTR, 0.0) or 0.0)
            # until<=0 means hold for full open duration (cleared explicitly).
            if until <= 0 or time.monotonic() < until:
                return True
        # If the fast monitor stalled and the TTL expired, still defer while opens exist.
        return _system_reports_open_0dte(system)
    except Exception:
        return False


def record_primary_batch_symbols(system: Any, symbols: Sequence[str], *, tick_id: str) -> None:
    if system is None:
        return
    try:
        setattr(system, PRIMARY_QUOTE_TICK_ATTR, str(tick_id or ""))
        setattr(
            system,
            PRIMARY_QUOTE_SYMBOLS_ATTR,
            {str(s).strip() for s in (symbols or []) if str(s or "").strip()},
        )
    except Exception:
        pass


def owned_symbols_missing_quotes(
    *,
    open_symbols: Sequence[str],
    quote_result_quotes: Dict[str, Any],
    exact_by_occ: Dict[str, Any],
) -> List[str]:
    """Owned open-position symbols still without an accepted exact quote."""
    missing: List[str] = []
    seen: Set[str] = set()
    quotes = quote_result_quotes if isinstance(quote_result_quotes, dict) else {}
    by_occ = exact_by_occ if isinstance(exact_by_occ, dict) else {}

    try:
        from modules.canonical_contract_identity import lookup_quote_by_aliases
    except Exception:
        lookup_quote_by_aliases = None  # type: ignore
    try:
        from modules.exact_contract_option_quotes import exact_quote_hard_fail_active
    except Exception:
        exact_quote_hard_fail_active = None  # type: ignore

    for sym in open_symbols or []:
        s = str(sym or "").strip()
        if not s:
            continue
        key = _dedupe_key(s)
        if key in seen:
            continue
        seen.add(key)
        if exact_quote_hard_fail_active is not None and exact_quote_hard_fail_active(s):
            continue
        q = quotes.get(s)
        if q is not None and not getattr(q, "rejected", False) and getattr(q, "bid", None) is not None:
            continue
        if s in by_occ and isinstance(by_occ.get(s), dict) and by_occ[s].get("bid") is not None:
            continue
        if lookup_quote_by_aliases is not None:
            hit = lookup_quote_by_aliases(by_occ, s)
            if isinstance(hit, dict) and hit.get("bid") is not None:
                continue
        missing.append(s)
    return missing


def should_defer_chain_bridge(
    *,
    open_position_count: int,
    owned_missing_count: int,
    continuation_missing_count: int,
) -> Tuple[bool, str]:
    """When open positions exist, owned exact OCC wins over full-chain bridge.

    Returns (defer, reason).
    """
    if int(open_position_count) <= 0:
        return False, ""
    if int(owned_missing_count) > 0:
        return True, "owned_exact_incomplete"
    if int(continuation_missing_count) > 0:
        # Shadow-only hydration yields while any 0DTE position is open.
        return True, "shadow_only_while_open"
    return False, ""


def record_open_quote_health(
    system: Any,
    *,
    tick_id: str,
    open_count: int,
    owned_missing_count: int,
    recovery_attempted: int = 0,
    recovery_deferred: int = 0,
    quote_latency_ms: float = 0.0,
    quote_batch_count: int = 0,
) -> None:
    """Publish the latest P0 exact-quote health for budget-aware discovery lanes."""
    if system is None:
        return
    try:
        health = {
            "tick_id": str(tick_id or ""),
            "open_count": int(open_count or 0),
            "owned_missing_count": int(owned_missing_count or 0),
            "recovery_attempted": int(recovery_attempted or 0),
            "recovery_deferred": int(recovery_deferred or 0),
            "quote_latency_ms": float(quote_latency_ms or 0.0),
            "quote_batch_count": int(quote_batch_count or 0),
            "recorded_mono": time.monotonic(),
        }
        health["healthy"] = bool(
            health["open_count"] <= 0
            or (health["owned_missing_count"] <= 0 and health["recovery_deferred"] <= 0)
        )
        hist = getattr(system, "_0dte_open_quote_latency_hist", None)
        if not isinstance(hist, list):
            hist = []
        hist.append(float(quote_latency_ms or 0.0))
        hist = hist[-5:]
        setattr(system, "_0dte_open_quote_latency_hist", hist)
        ordered = sorted(hist)
        health["quote_latency_median_ms"] = float(ordered[len(ordered) // 2])
        setattr(system, OPEN_QUOTE_HEALTH_ATTR, health)
    except Exception:
        pass


def owned_miss_blocks_discovery(
    open_count: int,
    missing: int,
    deferred: int = 0,
) -> bool:
    """True when owned OCC health is bad enough that discovery must yield.

    A single miss on a 3+ name book (ghost / one reject) must not starve entries.
    Tiny books (1–2 opens) still block on any owned miss.
    """
    gap = max(int(missing or 0), int(deferred or 0))
    if gap <= 0:
        return False
    n = max(0, int(open_count or 0))
    if n <= 2:
        return True
    min_block = max(2, _env_int("0DTE_DISCOVERY_OWNED_MISS_BLOCK_MIN", 2))
    return gap >= min_block


def open_quote_budget_allows_discovery(system: Any) -> Tuple[bool, str]:
    """P0 exact-quote health for option-chain lanes.

    The 24-name equity scan and the ORB1, Trendline, and ORB2 watches do not
    call this. A slow exit batch must not skip a break check.
    """
    if not open_quote_priority_active(system):
        return True, "no_open_priority"
    health = getattr(system, OPEN_QUOTE_HEALTH_ATTR, None)
    if not isinstance(health, dict):
        return False, "open_quote_health_unknown"
    try:
        open_count = int(health.get("open_count") or 0)
        missing = int(health.get("owned_missing_count") or 0)
        deferred = int(health.get("recovery_deferred") or 0)
        latency_ms = float(health.get("quote_latency_ms") or 0.0)
    except Exception:
        return False, "open_quote_health_invalid"
    if open_count <= 0:
        return True, "no_open_positions"
    if owned_miss_blocks_discovery(open_count, missing, deferred):
        return False, "owned_exact_incomplete"
    max_latency = _env_float("0DTE_DISCOVERY_MAX_OPEN_QUOTE_LATENCY_MS", 4000.0)
    latency_for_gate = float(health.get("quote_latency_median_ms") or latency_ms or 0.0)
    if max_latency > 0 and latency_for_gate > max_latency:
        return False, "open_quote_latency_high"
    return True, "open_exact_healthy"


def open_0dte_count(system: Any) -> int:
    """Best-effort open 0DTE count from the last monitor health stamp."""
    if system is None:
        return 0
    health = getattr(system, OPEN_QUOTE_HEALTH_ATTR, None)
    if isinstance(health, dict):
        try:
            n = int(health.get("open_count") or 0)
            if n > 0:
                return n
        except Exception:
            pass
    try:
        if _system_reports_open_0dte(system):
            return 1
    except Exception:
        pass
    return 0


def qg_chain_allow_stale_sec(system: Any = None) -> float:
    """QG dir% may reuse a cached chain this long while positions are open.

    Exact OCC exits need tick-fresh bids. Layer C dir% (floor 0.10) does not.
    """
    if system is not None and open_0dte_count(system) <= 0 and not open_quote_priority_active(system):
        return 0.0
    return max(0.0, _env_float("0DTE_QG_CHAIN_CACHE_WHILE_OPEN_SEC", 600.0))


def qg_live_chain_cap(system: Any) -> int:
    """Max live (non-cache) chain fetches per discovery scan.

    Manager on: ``CHAIN_FETCH_SCAN_CEILING`` only. Manager off: below
    ``0DTE_QG_LIVE_CHAIN_TIGHT_AT_OPEN`` opens (7) use the flat cap; at or above it
    the tight WHILE_OPEN cap so exit quotes stay first.
    """
    try:
        from central_tech.chain_fetch_manager import manager_enabled

        if manager_enabled():
            # Oct 13 2026: the shared chain bucket paces live chains. The per-scan
            # number is only a high ceiling (WHILE_OPEN applies when the manager is off).
            return max(1, _env_int("CHAIN_FETCH_SCAN_CEILING", 24))
    except Exception:
        pass
    open_n = open_0dte_count(system)
    flat_cap = max(1, _env_int("0DTE_QG_LIVE_CHAIN_MAX_PER_SCAN", 8))
    if open_n <= 0 and not open_quote_priority_active(system):
        return flat_cap
    # Oct 13 2026: was a hard-coded ``open_n >= 7``. Legacy path only (the chain
    # manager returns above); threshold now a config key.
    if open_n >= max(1, _env_int("0DTE_QG_LIVE_CHAIN_TIGHT_AT_OPEN", 7)):
        return max(0, _env_int("0DTE_QG_LIVE_CHAIN_MAX_PER_SCAN_WHILE_OPEN", 2))
    return flat_cap


def reset_qg_live_chain_budget(system: Any) -> int:
    """Reset the per-discovery-scan live QG chain budget. Returns the cap."""
    cap = qg_live_chain_cap(system)
    if system is None:
        return cap
    try:
        setattr(system, QG_LIVE_CHAIN_BUDGET_ATTR, int(cap))
        setattr(system, QG_LIVE_CHAIN_CAP_ATTR, int(cap))
    except Exception:
        pass
    return cap


def qg_live_chain_fetch_allowed(system: Any) -> bool:
    if system is None:
        return True
    remaining = getattr(system, QG_LIVE_CHAIN_BUDGET_ATTR, None)
    if remaining is None:
        return True
    try:
        return int(remaining) > 0
    except Exception:
        return True


def consume_qg_live_chain_fetch(system: Any) -> None:
    if system is None:
        return
    remaining = getattr(system, QG_LIVE_CHAIN_BUDGET_ATTR, None)
    if remaining is None:
        return
    try:
        setattr(system, QG_LIVE_CHAIN_BUDGET_ATTR, max(0, int(remaining) - 1))
    except Exception:
        pass


async def yield_to_open_position_monitor(
    system: Any,
    *,
    max_wait_s: float = 0.35,
) -> bool:
    """Yield the trading loop so the 2s exit spine can run before a chain fetch.

    Returns True when the unified monitor is idle (or unknown). False means the
    monitor was still busy after the wait. Callers with no cached chain still
    fetch. The yield is a turn for the exit spine, not a reason to skip.
    """
    busy_fn = getattr(system, "_unified_0dte_monitor_is_busy", None) if system is not None else None
    deadline = time.monotonic() + max(0.0, float(max_wait_s or 0.0))
    while True:
        busy = False
        try:
            busy = bool(busy_fn()) if callable(busy_fn) else False
        except Exception:
            busy = False
        if not busy:
            await asyncio.sleep(0)
            return True
        if time.monotonic() >= deadline:
            await asyncio.sleep(0)
            return False
        await asyncio.sleep(0.05)


def discovery_symbols_per_tick(system: Any, *, default: int = 24, lane: str = "") -> int:
    """Single discovery batch size (no while-open shrink; lane arg kept for compat)."""
    del system, lane
    try:
        from modules.discovery_watch_batch import discovery_batch_size

        return int(discovery_batch_size())
    except Exception:
        return max(1, int(default or 24))


MERGED_DISCOVERY_CACHE_ATTR = "_0dte_merged_discovery_equity_cache"


def discovery_merged_max_while_open() -> int:
    """Alias of unified discovery batch size (≤ ETrade batch hard cap)."""
    try:
        from modules.discovery_watch_batch import discovery_batch_size

        return int(discovery_batch_size())
    except Exception:
        return 24


def merge_discovery_symbol_lists(
    primary: Sequence[str],
    secondary: Sequence[str],
    *,
    max_total: Optional[int] = None,
) -> List[str]:
    """Legacy helper — prefer unified list scan. Still order-preserving unique merge."""
    cap = int(max_total) if max_total is not None else discovery_merged_max_while_open()
    out: List[str] = []
    seen: Set[str] = set()

    def _add(raw: str) -> None:
        sym = str(raw or "").strip().upper()
        if not sym or sym in seen:
            return
        if len(out) >= cap:
            return
        seen.add(sym)
        out.append(sym)

    for s in primary or []:
        _add(s)
    for s in secondary or []:
        _add(s)
    return out


def clear_merged_discovery_quotes(system: Any) -> None:
    if system is None:
        return
    try:
        setattr(system, MERGED_DISCOVERY_CACHE_ATTR, None)
    except Exception:
        pass


def store_merged_discovery_quotes(
    system: Any,
    quotes: Dict[str, Any],
    symbols: Sequence[str],
) -> None:
    if system is None:
        return
    try:
        sym_set = {str(s or "").strip().upper() for s in (symbols or []) if str(s or "").strip()}
        q = quotes if isinstance(quotes, dict) else {}
        setattr(
            system,
            MERGED_DISCOVERY_CACHE_ATTR,
            {
                "quotes": q,
                "symbols": sym_set,
                "mono": time.monotonic(),
            },
        )
    except Exception:
        pass


def take_discovery_quotes_from_merged_cache(
    system: Any,
    symbols: Sequence[str],
    *,
    max_age_sec: float = 45.0,
) -> Optional[Dict[str, Any]]:
    """Return the cached quote subset for requested symbols (partial hits allowed)."""
    if system is None:
        return None
    want = [str(s or "").strip().upper() for s in (symbols or []) if str(s or "").strip()]
    if not want:
        return {}
    try:
        cache = getattr(system, MERGED_DISCOVERY_CACHE_ATTR, None)
    except Exception:
        cache = None
    if not isinstance(cache, dict):
        return None
    try:
        age = time.monotonic() - float(cache.get("mono") or 0.0)
        if max_age_sec > 0 and age > float(max_age_sec):
            return None
        have = cache.get("symbols") or set()
        quotes = cache.get("quotes") if isinstance(cache.get("quotes"), dict) else {}
        if not isinstance(have, set):
            have = set(have or [])
        # Partial hit is enough: path eval skips names without a mark.
        # All-or-nothing used to force a second equity fetch while the 2s
        # monitor owned the quote budget (Aug 28 extras-first / 24-cap).
        # Prefer exact key match; fall back to uppercase key.
        out: Dict[str, Any] = {}
        for sym in want:
            if sym in quotes:
                out[sym] = quotes[sym]
            else:
                for k, v in quotes.items():
                    if str(k or "").strip().upper() == sym:
                        out[sym] = v
                        break
        return out or None
    except Exception:
        return None


def broker_budget_allows_heavy_enrichment(system: Any) -> Tuple[bool, str]:
    """Heavy PO chain/comprehensive enrich yields while open-position quote pressure exists."""
    if not open_quote_priority_active(system):
        return True, "no_open_priority"
    allow_when_open = str(os.getenv("0DTE_PO_HEAVY_ENRICH_WHILE_OPEN", "false") or "false").lower()
    if allow_when_open in ("1", "true", "yes", "on"):
        return open_quote_budget_allows_discovery(system)
    return False, "open_position_priority"


def po_snapshot_heavy_enrich_allowed(system: Any, *, force: bool = False) -> Tuple[bool, str]:
    """Entry/exit PO snaps pass ``force=True`` so they never defer under open-quote P0."""
    if force:
        return True, "forced_entry_or_exit"
    return broker_budget_allows_heavy_enrichment(system)


def should_defer_non_critical_scans(system: Any) -> bool:
    """Full universe / non-critical scans yield while open priority holds."""
    return open_quote_priority_active(system)


async def recover_missing_owned_exact_quotes(
    missing_symbols: Sequence[str],
    *,
    data_manager: Any = None,
    etrade_trader: Any = None,
    timeout_sec: float = 1.5,
    max_age_sec: float = 3.0,
    tick_id: str = "",
    primary_batch_symbols: Optional[Set[str]] = None,
    max_attempts: Optional[int] = None,
    max_wall_sec: Optional[float] = None,
    soft_stale_ok: bool = False,
) -> Any:
    """One isolated single-contract ``fetch_exact_contract_quotes`` per missing owned symbol.

    Does not re-issue the tick's full primary batch. Each recovery is a separate
    one-symbol task so a prior batch omission is not duplicated as a multi-symbol
    refresh of the same failed request set.
    """
    from modules.exact_contract_option_quotes import (
        ExactQuoteBatchResult,
        fetch_exact_contract_quotes,
        occ_fetch_in_flight,
    )

    result = ExactQuoteBatchResult(quotes={})
    if occ_fetch_in_flight():
        result.limitation = "occ_fetch_inflight"
        setattr(result, "symbols_deferred", len([s for s in (missing_symbols or []) if str(s or "").strip()]))
        log.info(
            "OPEN_POSITION_QUOTE_RECOVERY | tick_id=%s | action=skip_primary_inflight | "
            "missing=%d",
            tick_id,
            int(getattr(result, "symbols_deferred", 0) or 0),
        )
        return result
    primary = set(primary_batch_symbols or set())
    recovered = 0
    attempted = 0
    deferred = 0
    started_m = time.monotonic()
    max_n = max(1, int(max_attempts if max_attempts is not None else _env_int("0DTE_OPEN_QUOTE_RECOVERY_MAX_ATTEMPTS_PER_TICK", 2)))
    wall_budget = max(0.1, float(max_wall_sec if max_wall_sec is not None else _env_float("0DTE_OPEN_QUOTE_RECOVERY_MAX_WALL_SEC", 0.9)))
    missing_list = list(missing_symbols or [])

    for raw in missing_list:
        sym = str(raw or "").strip()
        if not sym:
            continue
        try:
            from modules.exact_contract_option_quotes import exact_quote_hard_fail_active

            if exact_quote_hard_fail_active(sym):
                deferred += 1
                log.info(
                    "OPEN_POSITION_QUOTE_RECOVERY | tick_id=%s | symbol=%s | "
                    "action=skip_hard_fail_quarantine",
                    tick_id,
                    sym,
                )
                continue
        except Exception:
            pass
        if attempted >= max_n or (time.monotonic() - started_m) >= wall_budget:
            deferred += 1
            continue
        attempted += 1
        # Isolated: always one symbol — never the full primary set.
        if primary and len(primary) > 1 and set([sym]) == primary:
            # Defensive: never treat "recover all primary symbols" as one task.
            pass
        try:
            one = await fetch_exact_contract_quotes(
                [sym],
                data_manager=data_manager,
                etrade_trader=etrade_trader,
                timeout_sec=float(timeout_sec),
                max_age_sec=float(max_age_sec),
                concurrency=1,
                broker_lane="owned_recovery",
                soft_stale_ok=bool(soft_stale_ok),
            )
        except Exception as exc:
            log.warning(
                "OPEN_POSITION_QUOTE_RECOVERY | tick_id=%s | symbol=%s | error=%s",
                tick_id,
                sym,
                exc,
            )
            continue
        result.request_count += int(getattr(one, "request_count", 0) or 0)
        result.batch_count += int(getattr(one, "batch_count", 0) or 0)
        for sample in getattr(one, "rtt_ms_samples", None) or []:
            result.rtt_ms_samples.append(sample)
        for k, q in (getattr(one, "quotes", None) or {}).items():
            result.quotes[k] = q
            if q is not None and not getattr(q, "rejected", False) and getattr(q, "bid", None) is not None:
                recovered += 1
        log.info(
            "OPEN_POSITION_QUOTE_RECOVERY | tick_id=%s | symbol=%s | "
            "attempted=1 | hit=%s | isolated=true",
            tick_id,
            sym,
            "true"
            if (one.quotes.get(sym) is not None and not getattr(one.quotes.get(sym), "rejected", True))
            else "false",
        )

    result.symbols_requested = attempted
    setattr(result, "symbols_deferred", int(deferred))
    setattr(result, "max_attempts_per_tick", int(max_n))
    setattr(result, "max_wall_sec", float(wall_budget))
    result.limitation = (
        ""
        if recovered >= attempted and attempted > 0
        else ("owned_recovery_partial" if recovered else "owned_recovery_miss")
    )
    log.info(
        "OPEN_POSITION_QUOTE_RECOVERY | tick_id=%s | attempted=%d | recovered=%d | "
        "deferred=%d | primary_batch_size=%d | mode=isolated_single_contract",
        tick_id,
        attempted,
        recovered,
        deferred,
        len(primary),
    )
    return result
