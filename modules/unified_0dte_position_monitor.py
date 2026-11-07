"""Unified 0DTE position monitor — single owner for ORB + Trendline exit ticks.

Guarantees:
  - One scheduler / one monotonic tick id
  - No overlapping monitor cycles
  - Exact-contract batched quotes (chain force-refresh only as bridge)
  - Same decision path for Demo and Live; only execution adapter differs
  - Profit Optimizer winner leftover is quoted after evaluate, off this lock
    (exit-tuning tape). Closed OCC never joins the owned exit batch.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

log = logging.getLogger(__name__)

UL_REFRESH_SLOW_S = 4.0
UL_REFRESH_HUNG_S = 30.0
UL_REFRESH_POLL_S = 2.0
# After a successful OCC apply, yield the shared Market lane to UL while last
# exact is still reusable. Two 5s equity waves + slack. Does not raise 4s/8s UL TTL.
OCC_DEFER_FOR_UL_MAX_S = 12.0


def _percentile_sorted(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    idx = min(len(values) - 1, max(0, int(round((pct / 100.0) * (len(values) - 1)))))
    return float(values[idx])


def resolve_force_refresh_underlyings(
    force_refresh_symbols: Optional[Set[str]],
    open_underlyings: Set[str],
) -> tuple:
    """Resolve chain-hydrate underlyings.

    Returns (force_set, exact_complete_no_bridge, full_chain_requests).

    Semantics:
      None      → caller did not decide; derive from open_underlyings (legacy)
      empty set → explicit zero hydration
      non-empty → hydrate only those underlyings
    """
    if force_refresh_symbols is None:
        force_set = {str(s).upper() for s in (open_underlyings or set()) if str(s or "").strip()}
        return force_set, False, len(force_set)
    force_set = {str(s).upper() for s in force_refresh_symbols if str(s or "").strip()}
    return force_set, True, len(force_set)


def bridge_underlyings_for_missing_exact(
    *,
    contracts: List[Any],
    exact_by_trade: Dict[str, Any],
    positions: List[Any],
) -> Set[str]:
    """Unique underlyings that still need chain bridge hydration."""
    missing_trade_ids = {
        str(getattr(c, "trade_id", "") or "")
        for c in (contracts or [])
        if str(getattr(c, "trade_id", "") or "") not in (exact_by_trade or {})
    }
    missing_trade_ids.discard("")
    out: Set[str] = set()
    for p in positions or []:
        pid = str(getattr(p, "position_id", "") or "")
        if pid not in missing_trade_ids:
            continue
        und = str(getattr(p, "symbol", "") or "").upper().strip()
        if und:
            out.add(und)
    for c in contracts or []:
        tid = str(getattr(c, "trade_id", "") or "")
        if tid and tid in (exact_by_trade or {}):
            continue
        und = str(
            getattr(c, "underlying", None) or getattr(c, "underlying_symbol", None) or ""
        ).upper().strip()
        if und:
            out.add(und)
    return out


def canonical_monitor_tick_id(raw: Any = None, *, now: Optional[float] = None) -> str:
    """One tick-id format for main, fallback, and watchdog: ``u0dte_<ms>``."""
    text = str(raw or "").strip()
    if text.startswith("u0dte_"):
        tail = text[6:]
        if tail.isdigit() and len(tail) >= 12:
            return text
    wall = float(now if now is not None else time.time())
    return f"u0dte_{int(wall * 1000)}"


def should_skip_duplicate_by_tick_id(
    *,
    trigger_source: str,
    tick_id: Optional[str],
    last_processed_tick_id: Optional[str],
    last_completed_ts: float = 0.0,
    now_ts: Optional[float] = None,
    legacy_min_interval_sec: float = 2.0,
) -> tuple:
    """Unified: skip only same tick_id. Legacy: wall-clock guard.

    Returns (should_skip, reason).
    """
    tid = str(tick_id or "").strip()
    trig = str(trigger_source or "")
    is_unified = trig.startswith("unified|") or tid.startswith("u0dte_")
    if is_unified and tid:
        if str(last_processed_tick_id or "") == tid:
            return True, "duplicate_same_tick"
        return False, ""
    now = float(now_ts if now_ts is not None else time.time())
    if float(last_completed_ts or 0.0) > 0 and (now - float(last_completed_ts)) < float(
        legacy_min_interval_sec
    ):
        return True, "duplicate_cycle_skip"
    return False, ""


@dataclass
class MonitorTickTelemetry:
    monitor_tick_id: str = ""
    scheduled_tick_at: float = 0.0
    actual_tick_started_at: float = 0.0
    scheduler_delay_ms: float = 0.0
    scheduler_interval_ms: float = 0.0
    open_position_count: int = 0
    unique_contract_count: int = 0
    quote_batch_count: int = 0
    quote_request_started_at: float = 0.0
    quote_response_received_at: float = 0.0
    quote_rtt_ms: float = 0.0
    quote_request_latency_ms: float = 0.0
    broker_quote_age_ms: float = 0.0
    quote_refresh_wait_ms: float = 0.0
    decision_runtime_ms: float = 0.0
    monitor_runtime_ms: float = 0.0
    total_cycle_ms: float = 0.0
    quote_parse_completed_at: float = 0.0
    monitor_cycle_completed_at: float = 0.0
    monitor_cycle_duration_ms: float = 0.0
    missed_deadline: bool = False
    overlapping_suppressed: bool = False
    used_chain_bridge: bool = False
    exact_quotes_ok: int = 0
    exact_quotes_rejected: int = 0
    exact_quotes_reused: int = 0
    sync_blocked_exits: int = 0
    extras: Dict[str, Any] = field(default_factory=dict)


@dataclass
class _PositionCadenceStats:
    """Rolling monitor-interval stats for one open trade."""

    intervals_ms: List[float] = field(default_factory=list)
    last_tick_mono: float = 0.0
    max_samples: int = 120

    def record_interval(self, interval_ms: float) -> None:
        self.intervals_ms.append(float(interval_ms))
        if len(self.intervals_ms) > self.max_samples:
            self.intervals_ms = self.intervals_ms[-self.max_samples :]

    def summary(self) -> Dict[str, Any]:
        vals = sorted(float(v) for v in self.intervals_ms if v is not None)
        if not vals:
            return {
                "samples": 0,
                "median_interval": None,
                "95th_percentile_interval": None,
                "maximum_interval": None,
                "intervals_over_2500ms": 0,
                "intervals_over_3500ms": 0,
                "intervals_over_5000ms": 0,
                "intervals_over_10000ms": 0,
            }
        n = len(vals)
        median = vals[n // 2] if n % 2 == 1 else 0.5 * (vals[n // 2 - 1] + vals[n // 2])
        p95 = vals[min(n - 1, max(0, int(round(0.95 * (n - 1)))))]
        return {
            "samples": n,
            "median_interval": round(median, 1),
            "95th_percentile_interval": round(p95, 1),
            "maximum_interval": round(vals[-1], 1),
            "intervals_over_2500ms": sum(1 for v in vals if v > 2500.0),
            "intervals_over_3500ms": sum(1 for v in vals if v > 3500.0),
            "intervals_over_5000ms": sum(1 for v in vals if v > 5000.0),
            "intervals_over_10000ms": sum(1 for v in vals if v > 10000.0),
        }


def persist_open_option_books(system: Any, *, min_interval_sec: float = 8.0) -> None:
    """Queue demo ledger persist so stealth HWM/PX3 survive Cloud Run kills.

    GCS write runs on the mock executor background thread (not this loop).
    Throttled so a 2s monitor cadence does not saturate GCS.
    """
    if system is None:
        return
    last = float(getattr(system, "_open_book_persist_ts", 0.0) or 0.0)
    now = time.time()
    if last > 0.0 and (now - last) < float(min_interval_sec or 0.0):
        return
    seen: Set[int] = set()
    executors = []
    dte0 = getattr(system, "dte0_manager", None)
    if dte0 is not None:
        executors.append(getattr(dte0, "options_executor", None))
    executors.append(getattr(system, "trendline_options_executor", None))
    wrote = False
    for ex in executors:
        if ex is None:
            continue
        mock = getattr(ex, "mock_executor", None)
        if mock is None and hasattr(ex, "_save_mock_data"):
            mock = ex
        ident = id(mock) if mock is not None else None
        if ident is None or ident in seen:
            continue
        seen.add(ident)
        if hasattr(mock, "_save_mock_data"):
            try:
                mock._save_mock_data()
                wrote = True
            except Exception:
                pass
    if wrote:
        system._open_book_persist_ts = now


class Unified0DTEPositionMonitor:
    """Single owner for open ORB + Trendline 0DTE premium monitoring."""

    def __init__(self, system: Any = None) -> None:
        self._system = system
        self._lock = asyncio.Lock()
        self._running = False
        self._next_tick_monotonic = 0.0
        self._last_completed_monotonic = 0.0
        self._last_tick_id: Optional[str] = None
        self._missed_deadlines = 0
        self._overlap_suppressions = 0
        self._overlap_streak = 0
        self._ticks = 0
        self._cycle_times: List[float] = []
        self._last_telemetry: Optional[MonitorTickTelemetry] = None
        # Consecutive overlap suppressions before WARNING (evaluate starved).
        self._overlap_warn_streak = 5
        # Heavy repair/bridge must not stretch the 2s open-position cadence.
        self._last_occ_repair_mono = 0.0
        self._quote_age_catchup = False
        self._quote_age_pull_mono = 0.0
        self._last_exact_incomplete = False
        self._occ_repair_task: Optional[asyncio.Task] = None
        self._bridge_refresh_task: Optional[asyncio.Task] = None
        self._last_bridge_refresh_mono = 0.0
        self._last_bridge_defer_log_mono = 0.0
        # Legacy duplicate full-batch refresh removed; owned recovery is in-tick only.
        self._quote_refresh_task: Optional[asyncio.Task] = None
        self._last_quote_refresh_mono = 0.0
        self._owned_recovery_task: Optional[asyncio.Task] = None
        self._last_owned_recovery_mono = 0.0
        self._po_shadow_persist_task: Optional[asyncio.Task] = None
        self._leftover_shadow_task: Optional[asyncio.Task] = None
        # Per-position cadence + bottleneck counters (debug summary).
        self._cadence_by_trade: Dict[str, _PositionCadenceStats] = {}
        self._last_cadence_summary_mono: float = 0.0
        self._bottleneck_counts: Dict[str, int] = {
            "scheduler_delay": 0,
            "sequential_monitor": 0,
            "quote_age_gate": 0,
            "chain_bridge": 0,
            "broker_request_latency": 0,
            "logging_overhead": 0,
            "profit_optimizer": 0,
            "other_blocking": 0,
            "reused_last_exact": 0,
        }
        # Observation layer: exact-quote reliability + authoritative evaluate cadence.
        self._quote_reliability_by_trade: Dict[str, Any] = {}
        self._exit_eval_cadence_by_trade: Dict[str, Any] = {}
        self._last_reliability_summary_mono: float = 0.0
        self._wake: Optional[asyncio.Event] = None
        self._loop_task: Optional[asyncio.Task] = None
        self._ul_refresh_task: Optional[asyncio.Task] = None
        self._ul_refresh_stalled: bool = False
        self._ul_last_ok_mono: float = 0.0
        self._occ_fetch_task: Optional[asyncio.Task] = None
        self._occ_last_apply_mono: float = 0.0
        self._occ_last_apply_quotes: Dict[str, Dict[str, Any]] = {}
        self._open_seen_mono: float = 0.0
        self._first_full_eval_mono: float = 0.0

    def bind_system(self, system: Any) -> None:
        self._system = system

    def _ensure_wake(self) -> asyncio.Event:
        ev = self._wake
        if ev is None:
            ev = asyncio.Event()
            self._wake = ev
        return ev

    def request_wake(self, *, reason: str = "") -> None:
        """Wake the dedicated maybe_tick loop. Fill enroll still only sets a flag."""
        sys = self._system
        if sys is not None:
            try:
                sys._0dte_force_monitor_tick = True
            except Exception:
                pass
        ev = self._wake
        if ev is not None:
            ev.set()
        if reason:
            log.debug("UNIFIED_0DTE_MONITOR | action=wake | reason=%s", reason)

    def loop_task_alive(self) -> bool:
        task = self._loop_task
        return task is not None and not task.done()

    def ul_refresh_task_alive(self) -> bool:
        task = self._ul_refresh_task
        return task is not None and not task.done()

    def ul_refresh_ok(self) -> bool:
        """True when the 2s UL loop is alive and not stalled.

        Evaluate skips the in-tick join whenever the loop task is alive
        (``ul_refresh_task_alive``), including while a slow batch is still
        in flight. This flag is stall telemetry, not the evaluate gate.
        """
        return self.ul_refresh_task_alive() and not bool(self._ul_refresh_stalled)

    def ensure_background_tasks(self) -> None:
        """Own timed maybe_tick + 2s UL refresh. Safe to call every start()."""
        if self._system is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._ensure_wake()
        if not self.loop_task_alive():
            self._loop_task = loop.create_task(
                self._monitor_loop(), name="unified-0dte-monitor"
            )
            log.info("UNIFIED_0DTE_MONITOR | action=loop_started")
        if not self.ul_refresh_task_alive():
            self._ul_refresh_task = loop.create_task(
                self._ul_refresh_loop(), name="unified-0dte-ul-refresh"
            )
            log.info("UNIFIED_0DTE_MONITOR | action=ul_refresh_started | interval_s=2.0")

    async def _monitor_loop(self) -> None:
        """Call existing maybe_tick on the configured interval or an immediate wake.

        Always pause after a tick. Idle ``maybe_tick`` does not advance
        ``_next_tick_monotonic``; without a pause this loop spins and starves
        health / discovery / entries.
        """
        ev = self._ensure_wake()
        min_pause = 0.2
        while True:
            try:
                sys = self._system
                if sys is None:
                    await asyncio.sleep(min_pause)
                    continue
                force = bool(getattr(sys, "_0dte_force_monitor_tick", False))
                now_m = time.monotonic()
                next_at = float(self._next_tick_monotonic or 0.0)
                if force or next_at <= 0.0 or now_m >= next_at:
                    await self.maybe_tick(sys, trigger_source="monitor_task")
                    ev.clear()
                    now_m = time.monotonic()
                    next_at = float(self._next_tick_monotonic or 0.0)
                    if next_at <= now_m:
                        try:
                            interval = float(
                                getattr(self._cfg(), "position_monitor_interval_sec", 2.0) or 2.0
                            )
                        except Exception:
                            interval = 2.0
                        self._next_tick_monotonic = now_m + max(min_pause, interval)
                    wait = max(min_pause, self._next_tick_monotonic - time.monotonic())
                    if bool(getattr(sys, "_0dte_force_monitor_tick", False)):
                        await asyncio.sleep(min_pause)
                        continue
                    try:
                        await asyncio.wait_for(ev.wait(), timeout=wait)
                    except asyncio.TimeoutError:
                        pass
                    ev.clear()
                    continue
                timeout = max(min_pause, next_at - now_m)
                try:
                    await asyncio.wait_for(ev.wait(), timeout=timeout)
                except asyncio.TimeoutError:
                    continue
                ev.clear()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("UNIFIED_0DTE_MONITOR | action=loop_error | err=%s", exc)
                await asyncio.sleep(0.5)

    async def _ul_refresh_loop(self) -> None:
        """Keep the owned UL cache fresh; evaluate only reads resolve_underlying_observation.

        Never timeout-cancel the broker batch. Equity last shares the market
        lane with OCC; cancelling discards an in-flight ``to_thread`` result and
        can start a second HTTP while the first thread is still alive. Leave
        the fetch running; evaluate stays cache-only while this loop is alive.
        A 4s miss logs ``ul_refresh_slow`` (cancel=false). A 30s hang logs
        ``ul_refresh_hung`` (cancel=false) and refuses a second fetch until
        the first task is done. When the inflight task completes, the next
        oldest-first batch starts immediately (no leftover 2s poll).
        """
        inflight: Optional[asyncio.Task] = None
        started_mono = 0.0
        n_symbols = 0
        logged_slow = False
        hung_logged = False
        slow_s = float(UL_REFRESH_SLOW_S)
        hung_s = float(UL_REFRESH_HUNG_S)
        poll_s = float(UL_REFRESH_POLL_S)
        while True:
            try:
                if inflight is not None and inflight.done():
                    try:
                        inflight.result()
                        self._ul_refresh_stalled = False
                        self._ul_last_ok_mono = time.monotonic()
                    except asyncio.CancelledError:
                        self._ul_refresh_stalled = True
                    except Exception as exc:
                        self._ul_refresh_stalled = True
                        log.warning(
                            "UNIFIED_0DTE_MONITOR | action=ul_refresh_error | err=%s",
                            exc,
                        )
                    inflight = None
                    logged_slow = False
                    hung_logged = False

                sys = self._system
                if inflight is None and sys is not None:
                    positions = self._collect_open_positions(sys)
                    if positions:
                        from modules.po_session_tech_tick import open_underlying_symbols
                        from modules.underlying_spot_stamp import (
                            refresh_underlying_spot_cache_async,
                        )

                        symbols = open_underlying_symbols(positions)
                        if symbols:
                            n_symbols = len(symbols)
                            inflight = asyncio.create_task(
                                refresh_underlying_spot_cache_async(
                                    sys,
                                    symbols,
                                    owned_only=True,
                                    skip_cache=True,
                                ),
                                name="unified-0dte-ul-refresh-batch",
                            )
                            started_mono = time.monotonic()
                            logged_slow = False
                            hung_logged = False
                if inflight is not None:
                    age = time.monotonic() - started_mono
                    if age >= hung_s:
                        self._ul_refresh_stalled = True
                        if not hung_logged:
                            hung_logged = True
                            log.warning(
                                "UNIFIED_0DTE_MONITOR | action=ul_refresh_hung | "
                                "elapsed_s=%.1f | symbols=%d | fallback=cache | cancel=false",
                                age,
                                n_symbols,
                            )
                    elif age >= slow_s:
                        self._ul_refresh_stalled = True
                        if not logged_slow:
                            logged_slow = True
                            log.warning(
                                "UNIFIED_0DTE_MONITOR | action=ul_refresh_slow | "
                                "elapsed_s=%.1f | symbols=%d | fallback=cache | cancel=false",
                                age,
                                n_symbols,
                            )
                    try:
                        # Wake as soon as the batch finishes so rotation does
                        # not wait out a leftover poll. Timeout does not cancel.
                        await asyncio.wait_for(asyncio.shield(inflight), timeout=poll_s)
                    except asyncio.TimeoutError:
                        pass
                else:
                    await asyncio.sleep(poll_s)
            except asyncio.CancelledError:
                if inflight is not None and not inflight.done():
                    inflight.cancel()
                raise
            except Exception as exc:
                self._ul_refresh_stalled = True
                log.warning("UNIFIED_0DTE_MONITOR | action=ul_refresh_error | err=%s", exc)
                await asyncio.sleep(poll_s)

    @property
    def last_telemetry(self) -> Optional[MonitorTickTelemetry]:
        return self._last_telemetry

    def _cfg(self):
        from modules.dte0_exit_monitor_config import resolve_exit_monitor_config

        return resolve_exit_monitor_config()

    @staticmethod
    def _last_exact_reusable(
        meta: Optional[Dict[str, Any]],
        *,
        max_age_sec: float,
        now_ts: Optional[float] = None,
        soft_stale_ok: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Return last trusted exact_quote if still usable for exit evaluation.

        Does not relax freshness for newly fetched marks — only reuses a previously
        accepted exact mark while an async refresh is in flight.

        When ``soft_stale_ok`` (Selected-ED / weekly), age alone does not discard a
        reusable bid — exits must still evaluate on the last correct contract mark.
        """
        if not isinstance(meta, dict):
            return None
        try:
            from modules.exit_grade_durability import meta_exact_quote, quote_source_is_exact_contract
        except Exception:
            meta_exact_quote = None  # type: ignore
            quote_source_is_exact_contract = None  # type: ignore
        if meta_exact_quote is not None:
            last = meta_exact_quote(meta)
        else:
            last = meta.get("exact_quote") or meta.get("exact_contract_quote")
        if not isinstance(last, dict) or last.get("bid") is None:
            return None
        src = str(last.get("source") or last.get("quote_source") or "").strip().lower()
        if quote_source_is_exact_contract is not None:
            if not quote_source_is_exact_contract(src) and src != "exit_grade":
                return None
        elif "exact_contract" not in src and src != "exact_quote" and src != "exit_grade":
            return None
        now = float(now_ts if now_ts is not None else time.time())
        age_sec: Optional[float] = None
        for key in ("broker_quote_timestamp", "quote_timestamp"):
            raw = last.get(key)
            if raw is None:
                continue
            try:
                if isinstance(raw, (int, float)):
                    ts = float(raw)
                    if ts > 1e12:
                        ts /= 1000.0
                else:
                    from datetime import datetime

                    ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
                age_sec = max(0.0, now - ts)
                break
            except Exception:
                continue
        if age_sec is None and last.get("quote_age_ms") is not None:
            try:
                base = float(last["quote_age_ms"]) / 1000.0
                stamped = last.get("parsed_at") or last.get("response_received_at")
                if stamped is not None:
                    try:
                        stamped_f = float(stamped)
                        if stamped_f > 1e12:
                            stamped_f /= 1000.0
                        base = base + max(0.0, now - stamped_f)
                    except Exception:
                        pass
                age_sec = base
            except Exception:
                age_sec = None
        soft_reused = False
        if age_sec is not None and age_sec > float(max_age_sec):
            if not soft_stale_ok:
                return None
            soft_reused = True
        out = dict(last)
        out["source"] = str(last.get("source") or "exact_quote")
        out["quote_source"] = "exact_quote"
        out["reused_last_exact"] = True
        if soft_reused:
            out["soft_stale_reused"] = True
        return out

    def _owned_last_exact_reusable_counts(
        self,
        positions: List[Any],
        *,
        max_age_sec: float,
        soft_stale_ok: bool,
        now_ts: Optional[float] = None,
    ) -> tuple:
        """Return (reusable, owned) last-exact marks still usable for evaluate."""
        owned = 0
        reusable = 0
        now = float(now_ts if now_ts is not None else time.time())
        for pos in positions or []:
            owned += 1
            tid = str(getattr(pos, "position_id", "") or "")
            if self._reusable_quote_for_trade(
                pos,
                tid,
                max_age_sec=float(max_age_sec),
                now_ts=now,
                soft_stale_ok=bool(soft_stale_ok),
            ) is not None:
                reusable += 1
        return reusable, owned

    def _reusable_quote_for_trade(
        self,
        pos: Any,
        trade_id: str,
        *,
        max_age_sec: float,
        now_ts: float,
        soft_stale_ok: bool,
    ) -> Optional[Dict[str, Any]]:
        """Last exact from meta, else the last successful OCC apply map."""
        meta = getattr(pos, "metadata", None) if pos is not None else None
        reused = self._last_exact_reusable(
            meta if isinstance(meta, dict) else None,
            max_age_sec=float(max_age_sec),
            now_ts=now_ts,
            soft_stale_ok=bool(soft_stale_ok),
        )
        if reused is not None:
            return reused
        blob = (
            self._occ_last_apply_quotes.get(str(trade_id))
            if trade_id and isinstance(self._occ_last_apply_quotes, dict)
            else None
        )
        if isinstance(blob, dict):
            return self._last_exact_reusable(
                {"exact_quote": blob},
                max_age_sec=float(max_age_sec),
                now_ts=now_ts,
                soft_stale_ok=bool(soft_stale_ok),
            )
        return None

    def _should_defer_occ_for_ul(
        self,
        positions: List[Any],
        *,
        max_age_sec: float,
        soft_stale_ok: bool,
    ) -> bool:
        """Yield a new OCC fetch only while last exact is still reusable.

        Evaluate already reuses last exact while a fetch is in flight. Starting a
        new OCC every maybe_tick at 5s RTT starves UL. Do not raise UL freshness
        limits. Once any owned last exact has expired, fetch OCC again — a 12s
        wall clock must not suppress fresh option bids.
        """
        if self._occ_fetch_task is not None:
            return False
        if not positions:
            return False
        last = float(self._occ_last_apply_mono or 0.0)
        if last <= 0.0:
            return False
        if (time.monotonic() - last) >= float(OCC_DEFER_FOR_UL_MAX_S):
            return False
        reusable, owned = self._owned_last_exact_reusable_counts(
            positions,
            max_age_sec=max_age_sec,
            soft_stale_ok=soft_stale_ok,
        )
        if owned > 0 and reusable < owned and last > 0.0:
            log.info(
                "UNIFIED_0DTE_MONITOR | action=occ_refresh_expired_last_exact | "
                "reusable=%d | owned=%d | yield_s=%.1f",
                reusable,
                owned,
                max(0.0, time.monotonic() - last),
            )
        return owned > 0 and reusable >= owned

    def _stamp_option_quote_eval_extras(
        self,
        tel: MonitorTickTelemetry,
        *,
        positions: List[Any],
        exact_by_trade: Dict[str, Any],
        max_age_sec: float,
        occ_deferred: bool,
    ) -> None:
        """Option-quote age + evaluate handoff, including OCC-yield ticks."""
        now = time.time()
        ages_ms: List[float] = []
        with_bid = 0
        missing = 0
        expired = 0
        reused = 0
        for pos in positions or []:
            tid = str(getattr(pos, "position_id", "") or "")
            blob = exact_by_trade.get(tid) if tid else None
            if not isinstance(blob, dict) or blob.get("bid") is None:
                missing += 1
                continue
            with_bid += 1
            if blob.get("reused_last_exact"):
                reused += 1
            age_sec = None
            for key in ("broker_quote_timestamp", "quote_timestamp"):
                raw = blob.get(key)
                if raw is None:
                    continue
                try:
                    if isinstance(raw, (int, float)):
                        ts = float(raw)
                        if ts > 1e12:
                            ts /= 1000.0
                    else:
                        from datetime import datetime

                        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
                    age_sec = max(0.0, now - ts)
                    break
                except Exception:
                    continue
            if age_sec is None and blob.get("quote_age_ms") is not None:
                try:
                    age_sec = float(blob["quote_age_ms"]) / 1000.0
                except (TypeError, ValueError):
                    age_sec = None
            if age_sec is not None:
                ages_ms.append(age_sec * 1000.0)
                if age_sec > float(max_age_sec):
                    expired += 1
        ages_s = sorted(ages_ms)
        tel.extras["positions_open"] = len(positions or [])
        tel.extras["positions_with_bid"] = with_bid
        tel.extras["positions_missing_bid"] = missing
        tel.extras["positions_handed_evaluate"] = with_bid
        tel.extras["positions_skipped_no_bid"] = missing
        tel.extras["positions_reused_last_exact"] = reused
        tel.extras["positions_expired_last_exact"] = expired
        tel.extras["option_quote_age_ms_p50"] = (
            ages_s[len(ages_s) // 2] if ages_s else None
        )
        tel.extras["option_quote_age_ms_p95"] = (
            ages_s[min(len(ages_s) - 1, max(0, int(round(0.95 * (len(ages_s) - 1)))))]
            if ages_s
            else None
        )
        tel.extras["occ_yield_window"] = bool(occ_deferred)
        inflight = bool(tel.extras.get("occ_evaluate_while_inflight"))
        if missing > 0 and inflight:
            skip_class = "occ_inflight"
        elif missing > 0 and occ_deferred:
            skip_class = "yield_no_bid"
        elif missing > 0:
            skip_class = "no_bid"
        elif occ_deferred:
            skip_class = "yield_eval"
        elif inflight:
            skip_class = "occ_inflight_eval"
        else:
            skip_class = "eval"
        tel.extras["eval_skip_class"] = skip_class
        tel.extras["positions_skipped_occ_inflight"] = missing if inflight else 0
        tel.extras["positions_skipped_all"] = missing
        owned = len(positions or [])
        if owned > 0 and self._open_seen_mono <= 0.0:
            self._open_seen_mono = time.monotonic()
        if owned > 0 and with_bid >= owned and self._first_full_eval_mono <= 0.0:
            self._first_full_eval_mono = time.monotonic()
        if self._open_seen_mono > 0.0:
            tel.extras["awaiting_first_eval"] = self._first_full_eval_mono <= 0.0
            tel.extras["fill_to_first_eval_s"] = (
                None
                if self._first_full_eval_mono <= 0.0
                else max(0.0, self._first_full_eval_mono - self._open_seen_mono)
            )
        else:
            tel.extras["awaiting_first_eval"] = False
            tel.extras["fill_to_first_eval_s"] = None

    async def _await_primary_occ_quotes(
        self,
        symbols: List[str],
        *,
        system: Any,
        cfg: Any,
        timeout_sec: float,
        max_age_sec: float,
        soft_stale_ok: bool,
        tel: Optional[MonitorTickTelemetry] = None,
    ) -> Any:
        """Join or start one OCC fetch. Never wait_for-cancel the worker.

        After the slow window, return so evaluate can reuse last exact bids.
        A finished batch is applied on this tick or the next. A second fetch
        is not started while the first task/thread is alive.
        """
        from modules.exact_contract_option_quotes import (
            ExactQuoteBatchResult,
            fetch_exact_contract_quotes,
        )

        extras = tel.extras if tel is not None else {}

        def _empty(limitation: str) -> ExactQuoteBatchResult:
            result = ExactQuoteBatchResult(quotes={})
            result.limitation = limitation
            return result

        def _mark_applied(result: Any) -> Any:
            limitation = str(getattr(result, "limitation", "") or "")
            if limitation not in (
                "occ_fetch_error",
                "occ_fetch_inflight",
                "occ_fetch_slow",
                "occ_deferred_for_ul",
            ):
                self._occ_last_apply_mono = time.monotonic()
            return result

        task = self._occ_fetch_task
        if task is not None and task.done():
            try:
                result = task.result()
            except Exception as exc:
                log.warning(
                    "UNIFIED_0DTE_MONITOR | action=occ_fetch_error | err=%s",
                    exc,
                )
                result = _empty("occ_fetch_error")
            self._occ_fetch_task = None
            extras["occ_applied_deferred"] = True
            extras["occ_evaluate_while_inflight"] = False
            return _mark_applied(result)

        if task is not None:
            extras["occ_evaluate_while_inflight"] = True
            extras["occ_second_fetch_refused"] = True
            try:
                result = await asyncio.wait_for(asyncio.shield(task), timeout=0.05)
                self._occ_fetch_task = None
                extras["occ_evaluate_while_inflight"] = False
                extras["occ_applied_deferred"] = True
                return _mark_applied(result)
            except asyncio.TimeoutError:
                return _empty("occ_fetch_inflight")
            except Exception as exc:
                self._occ_fetch_task = None
                log.warning(
                    "UNIFIED_0DTE_MONITOR | action=occ_fetch_error | err=%s",
                    exc,
                )
                return _empty("occ_fetch_error")

        self._occ_fetch_task = asyncio.create_task(
            fetch_exact_contract_quotes(
                symbols,
                data_manager=getattr(system, "data_manager", None),
                etrade_trader=getattr(
                    getattr(system, "data_manager", None), "etrade_trader", None
                ),
                timeout_sec=float(timeout_sec),
                max_age_sec=float(max_age_sec),
                batch_size=int(getattr(cfg, "open_exact_quote_batch_size", 25) or 25),
                concurrency=int(getattr(cfg, "quote_batch_concurrency", 4) or 4),
                soft_stale_ok=bool(soft_stale_ok),
            ),
            name="unified-0dte-occ-fetch",
        )
        try:
            result = await asyncio.wait_for(
                asyncio.shield(self._occ_fetch_task),
                timeout=max(0.05, float(timeout_sec)),
            )
            self._occ_fetch_task = None
            extras["occ_evaluate_while_inflight"] = False
            return _mark_applied(result)
        except asyncio.TimeoutError:
            extras["occ_evaluate_while_inflight"] = True
            log.warning(
                "UNIFIED_0DTE_MONITOR | action=exact_quote_slow | elapsed_s=%.1f | "
                "evaluate=cache | cancel=false",
                float(timeout_sec),
            )
            return _empty("occ_fetch_slow")
        except Exception as exc:
            self._occ_fetch_task = None
            log.warning(
                "UNIFIED_0DTE_MONITOR | action=occ_fetch_error | err=%s",
                exc,
            )
            return _empty("occ_fetch_error")

    def _prune_closed_trade_metrics(self, open_ids: Set[str]) -> None:
        """Drop in-memory counters for closed trades (no cross-position leak)."""
        for store_name in (
            "_cadence_by_trade",
            "_quote_reliability_by_trade",
            "_exit_eval_cadence_by_trade",
        ):
            store = getattr(self, store_name, None)
            if not isinstance(store, dict):
                continue
            for tid in list(store.keys()):
                if tid not in open_ids:
                    store.pop(tid, None)

    def _record_position_cadence(self, positions: List[Any], *, now_m: float) -> None:
        open_ids = {
            str(getattr(p, "position_id", "") or "").strip()
            for p in (positions or [])
            if str(getattr(p, "position_id", "") or "").strip()
        }
        self._prune_closed_trade_metrics(open_ids)
        for tid in open_ids:
            stats = self._cadence_by_trade.get(tid)
            if stats is None:
                self._cadence_by_trade[tid] = _PositionCadenceStats(last_tick_mono=now_m)
                continue
            if stats.last_tick_mono > 0:
                stats.record_interval((now_m - stats.last_tick_mono) * 1000.0)
            stats.last_tick_mono = now_m

    def _quote_reliability(self, trade_id: str):
        from modules.exit_observation_telemetry import ExactQuoteReliabilityStats

        tid = str(trade_id or "").strip()
        if not tid:
            return ExactQuoteReliabilityStats()
        stats = self._quote_reliability_by_trade.get(tid)
        if stats is None:
            stats = ExactQuoteReliabilityStats(trade_id=tid)
            self._quote_reliability_by_trade[tid] = stats
        return stats

    def _exit_eval_cadence(self, trade_id: str):
        from modules.exit_observation_telemetry import ExitEvalCadenceStats

        tid = str(trade_id or "").strip()
        if not tid:
            return ExitEvalCadenceStats()
        stats = self._exit_eval_cadence_by_trade.get(tid)
        if stats is None:
            stats = ExitEvalCadenceStats(trade_id=tid)
            self._exit_eval_cadence_by_trade[tid] = stats
        return stats

    def record_exit_evaluation(
        self,
        trade_id: str,
        *,
        quote_wait_ms: float = 0.0,
        monitor_runtime_ms: float = 0.0,
        decision_runtime_ms: float = 0.0,
        total_cycle_ms: float = 0.0,
        meta: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record one authoritative stealth.evaluate() completion for cadence proof."""
        from modules.exit_observation_telemetry import persist_cadence_on_meta

        tid = str(trade_id or "").strip()
        if not tid:
            return
        stats = self._exit_eval_cadence(tid)
        interval_ms = stats.record(
            now_mono=time.monotonic(),
            quote_wait_ms=float(quote_wait_ms or 0.0),
            monitor_runtime_ms=float(monitor_runtime_ms or 0.0),
            decision_runtime_ms=float(decision_runtime_ms or 0.0),
            total_cycle_ms=float(total_cycle_ms or 0.0),
        )
        if isinstance(meta, dict):
            persist_cadence_on_meta(meta, stats)
            if interval_ms is not None:
                meta["_last_exit_eval_interval_ms"] = float(interval_ms)
                meta["_last_exit_eval_quote_wait_ms"] = float(quote_wait_ms or 0.0)
                meta["_last_exit_eval_decision_runtime_ms"] = float(decision_runtime_ms or 0.0)

    def _maybe_log_cadence_summary(self, *, now_m: float, tel: MonitorTickTelemetry) -> None:
        # INFO when positions are open (prove evaluate cadence); else debug every ~3m.
        has_open = int(tel.open_position_count or 0) > 0
        min_gap = 30.0 if has_open else 180.0
        if self._last_cadence_summary_mono > 0 and (now_m - self._last_cadence_summary_mono) < min_gap:
            return
        self._last_cadence_summary_mono = now_m
        per_pos = {
            tid: stats.summary()
            for tid, stats in self._cadence_by_trade.items()
            if stats.intervals_ms
        }
        eval_pos = {
            tid: stats.summary()
            for tid, stats in self._exit_eval_cadence_by_trade.items()
            if getattr(stats, "samples", None)
        }
        if not per_pos and not eval_pos and not tel.open_position_count:
            return
        medians = [
            float(s["median_interval"])
            for s in per_pos.values()
            if s.get("median_interval") is not None
        ]
        p95s = [
            float(s["95th_percentile_interval"])
            for s in per_pos.values()
            if s.get("95th_percentile_interval") is not None
        ]
        maxes = [
            float(s["maximum_interval"])
            for s in per_pos.values()
            if s.get("maximum_interval") is not None
        ]
        eval_medians = []
        for s in eval_pos.values():
            bucket = s.get("evaluation_interval_ms") if isinstance(s, dict) else None
            if isinstance(bucket, dict) and bucket.get("median") is not None:
                eval_medians.append(float(bucket["median"]))
        logger = log.info if has_open else log.debug
        # Compact INFO payload — avoid dumping full per-position dicts on the hot path.
        eval_compact = {
            tid: {
                "eval_med_ms": (s.get("evaluation_interval_ms") or {}).get("median"),
                "eval_p95_ms": (s.get("evaluation_interval_ms") or {}).get("p95"),
                "eval_max_ms": (s.get("evaluation_interval_ms") or {}).get("maximum"),
                "gt_2_5s": (s.get("evaluation_interval_ms") or {}).get("count_gt_2_5s"),
                "gt_5s": (s.get("evaluation_interval_ms") or {}).get("count_gt_5s"),
                "quote_wait_med_ms": (s.get("quote_wait_ms") or {}).get("median"),
                "cycle_med_ms": (s.get("total_cycle_ms") or {}).get("median"),
                "samples": s.get("samples"),
            }
            for tid, s in list(eval_pos.items())[:8]
        }
        logger(
            "MONITOR_CADENCE_SUMMARY | positions=%d | tick_median_interval_ms=%s | "
            "tick_p95_interval_ms=%s | tick_max_interval_ms=%s | "
            "eval_median_interval_ms=%s | cycle_ms=%.1f | "
            "quote_wait_ms=%.1f | quote_latency_ms=%.1f | broker_quote_age_ms=%.1f | "
            "decision_ms=%.1f | monitor_ms=%.1f | bottlenecks=%s | "
            "per_position_eval=%s",
            max(len(per_pos), len(eval_pos), int(tel.open_position_count or 0)),
            round(sorted(medians)[len(medians) // 2], 1) if medians else None,
            round(max(p95s), 1) if p95s else None,
            round(max(maxes), 1) if maxes else None,
            round(sorted(eval_medians)[len(eval_medians) // 2], 1) if eval_medians else None,
            float(tel.total_cycle_ms or tel.monitor_cycle_duration_ms or 0.0),
            float(tel.quote_refresh_wait_ms or 0.0),
            float(tel.quote_request_latency_ms or 0.0),
            float(tel.broker_quote_age_ms or 0.0),
            float(tel.decision_runtime_ms or 0.0),
            float(tel.monitor_runtime_ms or 0.0),
            dict(self._bottleneck_counts),
            eval_compact,
        )
        if has_open:
            log.debug(
                "MONITOR_CADENCE_SUMMARY_DETAIL | per_position_tick=%s | per_position_eval_full=%s",
                {tid: per_pos[tid] for tid in list(per_pos)[:8]},
                {tid: eval_pos[tid] for tid in list(eval_pos)[:8]},
            )

    def _maybe_log_quote_reliability_summary(self, *, now_m: float, positions: List[Any]) -> None:
        open_ids = {
            str(getattr(p, "position_id", "") or "").strip()
            for p in (positions or [])
            if str(getattr(p, "position_id", "") or "").strip()
        }
        self._prune_closed_trade_metrics(open_ids)
        if not open_ids:
            return
        if self._last_reliability_summary_mono > 0 and (now_m - self._last_reliability_summary_mono) < 30.0:
            return
        self._last_reliability_summary_mono = now_m
        # Peak book: do not emit one INFO line per open trade every 30s.
        log_ids = list(open_ids)
        reliability_info = log.info
        if len(log_ids) >= 8:
            reliability_info = log.debug
            log_ids = log_ids[:3]
        for tid in log_ids:
            stats = self._quote_reliability_by_trade.get(tid)
            if stats is None or int(getattr(stats, "exact_quote_requests", 0) or 0) <= 0:
                continue
            summary = stats.summary()
            reliability_info(
                "EXACT_QUOTE_RELIABILITY | trade_id=%s | exact_quote_requests=%s | "
                "exact_quote_successes=%s | exact_quote_misses=%s | exact_success_rate=%s | "
                "longest_continuous_gap_sec=%s | trusted_bid_age_p50=%s | trusted_bid_age_p95=%s | "
                "trusted_bid_age_max=%s | fallback_source_counts=%s | recovery_candidate_used=%s | "
                "winning_symbol_format=%s | dominant_miss_class=%s | miss_class_counts=%s | "
                "last_request_symbol=%s",
                summary.get("trade_id"),
                summary.get("exact_quote_requests"),
                summary.get("exact_quote_successes"),
                summary.get("exact_quote_misses"),
                summary.get("exact_success_rate"),
                summary.get("longest_continuous_gap_sec"),
                summary.get("trusted_bid_age_p50"),
                summary.get("trusted_bid_age_p95"),
                summary.get("trusted_bid_age_max"),
                summary.get("fallback_source_counts"),
                summary.get("recovery_candidate_used"),
                summary.get("winning_symbol_format"),
                summary.get("dominant_miss_class"),
                summary.get("miss_class_counts"),
                summary.get("last_request_symbol"),
            )

    def _classify_bottlenecks(self, tel: MonitorTickTelemetry, *, interval: float) -> None:
        if float(tel.scheduler_delay_ms or 0.0) > 500.0:
            self._bottleneck_counts["scheduler_delay"] += 1
        if float(tel.quote_request_latency_ms or 0.0) > 800.0:
            self._bottleneck_counts["broker_request_latency"] += 1
        if int(tel.exact_quotes_rejected or 0) > 0:
            self._bottleneck_counts["quote_age_gate"] += 1
        if bool(tel.used_chain_bridge) or bool(tel.extras.get("chain_bridge_background")):
            self._bottleneck_counts["chain_bridge"] += 1
        if float(tel.decision_runtime_ms or 0.0) > float(interval) * 1000.0 * 0.6:
            self._bottleneck_counts["sequential_monitor"] += 1
        if bool(tel.extras.get("po_observe_ms")) and float(tel.extras.get("po_observe_ms") or 0) > 200.0:
            self._bottleneck_counts["profit_optimizer"] += 1
        # quote_refresh_wait_ms is the full exact-quote RTT, not a separate wait.
        # Count other_blocking only when wait exceeds request latency (owned recovery).
        extra_wait = float(tel.quote_refresh_wait_ms or 0.0) - float(
            tel.quote_request_latency_ms or 0.0
        )
        if extra_wait > 250.0:
            self._bottleneck_counts["other_blocking"] += 1
        if int(tel.exact_quotes_reused or 0) > 0:
            self._bottleneck_counts["reused_last_exact"] += int(tel.exact_quotes_reused)

    def _schedule_open_quote_refresh(
        self,
        system: Any,
        symbols: List[str],
        *,
        max_age_sec: float,
        now_m: float,
        interval: float,
    ) -> None:
        """Removed: duplicated the same tick's failed multi-symbol request.

        Owned misses are recovered in-tick via isolated single-contract fetches
        in ``_run_once`` (see ``recover_missing_owned_exact_quotes``). This stub
        must not schedule a second full-batch ``fetch_exact_contract_quotes``.
        """
        log.debug(
            "EXACT_QUOTE_ASYNC_REFRESH | skipped=replaced_by_owned_recovery | "
            "symbols=%d | interval=%.2f",
            len(symbols or []),
            float(interval),
        )
        return

    def _collect_open_positions(self, system: Any) -> List[Any]:
        positions: List[Any] = []
        seen: Set[str] = set()
        dte0 = getattr(system, "dte0_manager", None)
        executor = getattr(dte0, "options_executor", None) if dte0 else None
        if executor is not None:
            try:
                from modules.unified_0dte_monitor_registry import audit_demo_dual_book_drift

                audit_demo_dual_book_drift(executor)
            except Exception:
                pass
        if executor is not None and hasattr(executor, "get_open_positions"):
            for p in executor.get_open_positions() or []:
                tid = str(getattr(p, "position_id", "") or "")
                if tid and tid not in seen:
                    seen.add(tid)
                    positions.append(p)
        # Trendline unified open set (may alias same executor book)
        if hasattr(system, "_trendline_unified_open_positions"):
            try:
                tl_map = system._trendline_unified_open_positions() or {}
                for tid, p in tl_map.items():
                    key = str(tid or getattr(p, "position_id", "") or "")
                    if key and key not in seen:
                        seen.add(key)
                        positions.append(p)
            except Exception:
                pass
        if hasattr(system, "_orb_unified_open_positions"):
            try:
                orb_map = system._orb_unified_open_positions() or {}
                for tid, p in orb_map.items():
                    key = str(tid or getattr(p, "position_id", "") or "")
                    if key and key not in seen:
                        seen.add(key)
                        positions.append(p)
            except Exception:
                pass
        return positions

    def _mode_label(self, system: Any) -> str:
        try:
            from modules.runtime_mode import is_demo_mode

            return "demo" if is_demo_mode() else "live"
        except Exception:
            executor = getattr(getattr(system, "dte0_manager", None), "options_executor", None)
            return "demo" if getattr(executor, "demo_mode", True) else "live"

    def _collect_profit_optimizer_continuations(self, system: Any) -> List[Dict[str, Any]]:
        try:
            from profit_optimizer.trading_hooks import unified_monitor_continuations

            return unified_monitor_continuations(system)
        except Exception as exc:
            log.warning(
                "PROFIT_OPTIMIZER_CONTINUATION | action=collect_failed | error=%s",
                exc,
            )
            return []

    def _schedule_po_shadow_persist(self, system: Any, recorder: Any = None) -> bool:
        """Finalize expired shadows off the 2s tick (never await on the hot path)."""
        rec = recorder
        if rec is None:
            try:
                from profit_optimizer.trading_hooks import ensure_profit_optimizer

                po = ensure_profit_optimizer(system)
                rec = po.recorder if po else None
            except Exception:
                rec = None
        if rec is None:
            return False
        if not rec.has_pending_shadow_work():
            return True
        task = self._po_shadow_persist_task
        if task is not None and not task.done():
            return True

        async def _run() -> None:
            try:
                n = await asyncio.to_thread(rec.finalize_expired_shadows)
                if n:
                    log.info(
                        "PROFIT_OPTIMIZER | action=shadow_persist_background_done | finalized=%s",
                        int(n),
                    )
            except Exception as persist_err:
                log.warning(
                    "PROFIT_OPTIMIZER | action=shadow_persist_background_error | error=%s",
                    persist_err,
                )

        try:
            self._po_shadow_persist_task = asyncio.create_task(_run())
            return True
        except RuntimeError:
            return False

    def _deliver_po_samples_from_last_tick(
        self,
        system: Any,
        *,
        trigger_source: str,
        tick_id: str = "",
    ) -> None:
        """Keep Profit Optimizer on the 2s cadence when a heavy tick is still running.

        Reuses the last exact/chain quote batch — no second broker fetch.
        """
        sample_tick = str(tick_id or self._last_tick_id or f"overlap_{trigger_source}")
        try:
            from profit_optimizer.trading_hooks import (
                observe_0dte_tick,
                observe_unified_monitor_continuations,
            )

            exact_by_occ = getattr(system, "_unified_0dte_exact_quotes_by_occ", None)
            if not isinstance(exact_by_occ, dict):
                exact_by_occ = {}
            exact_by_trade = getattr(system, "_unified_0dte_exact_quotes", None)
            if not isinstance(exact_by_trade, dict):
                exact_by_trade = {}

            und_map = getattr(system, "_orb_0dte_last_underlying_by_symbol", None)
            if not isinstance(und_map, dict):
                und_map = {}

            # Overlap samples reuse the last owned last. Do not restamp a
            # discovery/cache last as fresh — that froze UL on 10/30.

            for p in self._collect_open_positions(system):
                tid = str(getattr(p, "position_id", "") or "").strip()
                if not tid:
                    continue
                oq = exact_by_trade.get(tid)
                if not isinstance(oq, dict):
                    continue
                sym = str(getattr(p, "symbol", "") or "").strip().upper()
                observe_0dte_tick(
                    system,
                    trade_id=tid,
                    underlying_price=float(und_map.get(sym, 0.0) or 0.0),
                    option_quote=oq,
                    osnap={},
                    quote_grade=str(oq.get("quote_source") or oq.get("premium_source") or ""),
                )

            continuations = self._collect_profit_optimizer_continuations(system)
            if continuations:
                observe_unified_monitor_continuations(
                    system,
                    continuations=continuations,
                    exact_quotes_by_occ=exact_by_occ,
                    tick_id=sample_tick,
                    premium_snapshots_by_trade=getattr(
                        system, "_unified_0dte_premium_snapshots_by_trade", None
                    ),
                    sample_at_now=True,
                )
        except Exception as exc:
            log.warning(
                "PROFIT_OPTIMIZER_CONTINUATION | action=overlap_sample_failed | "
                "trigger=%s | error=%s",
                trigger_source,
                exc,
            )

    def _occ_repair_due(self, *, now_m: float, interval: float) -> bool:
        """OCC chain repair is expensive — run sparsely, not every 2s tick."""
        min_gap = max(30.0, float(interval) * 15.0)
        if self._last_occ_repair_mono <= 0:
            return True
        # Retry sooner while *owned* exact quotes are incomplete, but never on every tick.
        if self._last_exact_incomplete:
            return (now_m - self._last_occ_repair_mono) >= max(10.0, float(interval) * 5.0)
        return (now_m - self._last_occ_repair_mono) >= min_gap

    def _schedule_occ_repair(
        self,
        system: Any,
        *,
        positions: List[Any],
        continuations: List[Dict[str, Any]],
        symbols: List[str],
        owned_only: bool,
    ) -> None:
        """Run OCC expiry repair off the open-position tick (never await on hot path).

        Multi-day chain walks for weekly names + shadow continuations were observed
        stretching ``cycle_ms`` to ~50–60s (Aug 10 RESEARCH Demo), starving the 2s
        exit cadence. Owned-only while opens exist; full repair when idle.
        """
        task = self._occ_repair_task
        if task is not None and not task.done():
            log.info(
                "OCC_EXPIRY_REPAIR | stage=background_skip | reason=already_running | "
                "owned_only=%s",
                str(bool(owned_only)).lower(),
            )
            return

        pos_snapshot = list(positions or [])
        cont_snapshot = list(continuations or [])
        sym_snapshot = list(symbols or [])

        async def _run() -> None:
            try:
                from modules.exact_contract_option_quotes import (
                    repair_continuation_option_symbols_from_chain,
                    repair_owned_contract_expiry_from_chain,
                    repair_quote_symbols_expiry_from_chain,
                )
                from modules.open_position_recovery import rebuild_system_position_registry

                n_repaired = await repair_owned_contract_expiry_from_chain(
                    system, pos_snapshot
                )
                n_cont = 0
                n_syms = 0
                if not owned_only:
                    n_cont = await repair_continuation_option_symbols_from_chain(
                        system, cont_snapshot
                    )
                    sym_map = await repair_quote_symbols_expiry_from_chain(
                        system, sym_snapshot
                    )
                    n_syms = len(sym_map or {})
                if n_repaired or n_cont:
                    try:
                        rebuild_system_position_registry(system)
                    except Exception:
                        pass
                log.info(
                    "OCC_EXPIRY_REPAIR | stage=background_done | owned_only=%s | "
                    "owned_repairs=%s | continuation_repairs=%s | symbol_rewrites=%s",
                    str(bool(owned_only)).lower(),
                    int(n_repaired or 0),
                    int(n_cont or 0),
                    int(n_syms or 0),
                )
            except Exception as repair_err:
                log.warning(
                    "OCC_EXPIRY_REPAIR | stage=background_error | error=%s",
                    repair_err,
                )

        self._occ_repair_task = asyncio.create_task(_run())
        log.info(
            "OCC_EXPIRY_REPAIR | stage=background_scheduled | owned_only=%s | "
            "positions=%d | continuations=%d | symbols=%d",
            str(bool(owned_only)).lower(),
            len(pos_snapshot),
            len(cont_snapshot),
            len(sym_snapshot),
        )

    def _schedule_bridge_refresh(
        self,
        system: Any,
        continuations: List[Dict[str, Any]],
        *,
        max_age_sec: float,
        now_m: float,
        interval: float,
    ) -> None:
        """Refresh chain-bridge quotes in the background; never block the 2s tick."""
        if not continuations:
            return
        min_gap = max(float(interval), 2.0)
        if (now_m - self._last_bridge_refresh_mono) < min_gap:
            return
        task = self._bridge_refresh_task
        if task is not None and not task.done():
            return

        async def _run() -> None:
            try:
                from modules.exact_contract_option_quotes import (
                    fetch_chain_bridge_quotes_for_continuations,
                )

                chain_by_trade = await fetch_chain_bridge_quotes_for_continuations(
                    system,
                    continuations,
                    max_age_sec=float(max_age_sec),
                )
                if isinstance(chain_by_trade, dict) and chain_by_trade:
                    prev = getattr(system, "_unified_0dte_chain_quotes_by_trade", None)
                    merged = dict(prev) if isinstance(prev, dict) else {}
                    merged.update(chain_by_trade)
                    system._unified_0dte_chain_quotes_by_trade = merged
            except Exception as chain_err:
                log.warning(
                    "CHAIN_BRIDGE_CONTINUATION | background_error=%s",
                    chain_err,
                )

        self._last_bridge_refresh_mono = now_m
        self._bridge_refresh_task = asyncio.create_task(_run())

    def _merge_leftover_exact_quotes(
        self,
        system: Any,
        result_quotes: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Upsert leftover OCC marks onto the current exact map (in place).

        Does not replace the owned dict. Observe uses the returned leftover
        updates so a concurrent owned tick cannot drop winner marks.
        """
        from modules.dte0_exit_monitor_config import exit_grade_source_allowed

        try:
            from modules.canonical_contract_identity import register_quote_aliases
        except Exception:
            register_quote_aliases = None  # type: ignore
        updates: Dict[str, Any] = {}
        for option_symbol, q in (result_quotes or {}).items():
            if (
                q is None
                or getattr(q, "rejected", False)
                or not exit_grade_source_allowed(getattr(q, "source", None))
                or getattr(q, "bid", None) is None
            ):
                continue
            try:
                q_dict = q.to_option_quote_dict()
            except Exception:
                continue
            if not isinstance(q_dict, dict):
                continue
            updates[str(option_symbol)] = q_dict
            if register_quote_aliases is not None:
                extra = [
                    str(getattr(q, "option_symbol", "") or ""),
                ]
                raw = getattr(q, "raw", None)
                if isinstance(raw, dict):
                    extra.append(str(raw.get("osiKey") or ""))
                try:
                    register_quote_aliases(
                        updates,
                        q_dict,
                        primary_key=str(option_symbol),
                        extra_aliases=extra,
                    )
                except Exception:
                    pass
        cur = getattr(system, "_unified_0dte_exact_quotes_by_occ", None)
        if isinstance(cur, dict):
            cur.update(updates)
        else:
            system._unified_0dte_exact_quotes_by_occ = dict(updates)
        return updates

    def _schedule_leftover_shadow_quotes(
        self,
        system: Any,
        *,
        leftover_syms: List[str],
        leftover_next: int,
        continuations: List[Dict[str, Any]],
        tick_id: str,
        timeout_sec: float,
        max_age_sec: float,
        concurrency: int,
        owned_underlyings: Optional[List[str]] = None,
    ) -> bool:
        """Quote winner leftover OCC off the owned lock; observe only after marks land.

        Owned evaluate stays first. Leftover wait must not land in ``total_cycle_ms``.
        Single-flight: if a leftover fetch is already running, the caller skips
        observe so missing quotes do not spam ``PROFIT_OPTIMIZER_SHADOW_GAP``.
        Leftover underlyings that left the open book are refreshed here, not on
        the owned critical equity pass.
        """
        if not leftover_syms:
            return False
        task = self._leftover_shadow_task
        if task is not None and not task.done():
            return False
        syms = list(leftover_syms or [])
        conts = list(continuations or [])
        next_off = int(leftover_next or 0)
        sample_tick = str(tick_id or "")
        owned_und = [
            str(s or "").strip().upper()
            for s in (owned_underlyings or [])
            if str(s or "").strip()
        ]

        async def _run() -> None:
            started = time.monotonic()
            hits = 0
            try:
                from modules.exact_contract_option_quotes import fetch_exact_contract_quotes
                from modules.monitor_premium_snapshot import build_premium_snapshot
                from modules.canonical_contract_identity import (
                    lookup_quote_by_aliases,
                    quote_lookup_aliases,
                )
                from modules.open_position_quote_orchestration import leftover_underlying_symbols
                from modules.underlying_spot_stamp import refresh_underlying_spot_cache_async
                from profit_optimizer.trading_hooks import observe_unified_monitor_continuations

                leftover_result = await fetch_exact_contract_quotes(
                    syms,
                    data_manager=getattr(system, "data_manager", None),
                    etrade_trader=getattr(
                        getattr(system, "data_manager", None), "etrade_trader", None
                    ),
                    timeout_sec=float(timeout_sec),
                    max_age_sec=float(max_age_sec),
                    concurrency=int(concurrency),
                    broker_lane="po_shadow_occ",
                    soft_stale_ok=True,
                )
                try:
                    from modules.option_api_accounting import record_real_broker_request

                    n_left = int(getattr(leftover_result, "request_count", 0) or 0)
                    if n_left > 0:
                        record_real_broker_request(system, lane="option", count=n_left)
                except Exception:
                    pass
                leftover_updates = self._merge_leftover_exact_quotes(
                    system, getattr(leftover_result, "quotes", None) or {}
                )
                hits = len(leftover_updates)
                try:
                    system._0dte_shadow_leftover_rotation_offset = next_off
                except Exception:
                    pass
                need_und = leftover_underlying_symbols(conts, owned_symbols=owned_und)
                if need_und and not owned_und:
                    try:
                        await refresh_underlying_spot_cache_async(
                            system, need_und, owned_only=False, skip_cache=True
                        )
                    except Exception as und_err:
                        log.warning(
                            "PO_LEFTOVER_SHADOW | action=underlying_refresh_error | "
                            "tick_id=%s | symbols=%d | error=%s",
                            sample_tick,
                            len(need_und),
                            und_err,
                        )
                exact_by_occ = dict(
                    getattr(system, "_unified_0dte_exact_quotes_by_occ", None) or {}
                )
                exact_by_occ.update(leftover_updates)
                chain_by_trade = getattr(system, "_unified_0dte_chain_quotes_by_trade", None)
                if not isinstance(chain_by_trade, dict):
                    chain_by_trade = {}
                snapshots: Dict[str, Any] = {}
                prev = getattr(system, "_unified_0dte_premium_snapshots_by_trade", None)
                if isinstance(prev, dict):
                    snapshots.update(prev)
                und_map = getattr(system, "_orb_0dte_last_underlying_by_symbol", None)
                if not isinstance(und_map, dict):
                    und_map = {}
                for continuation in conts:
                    tid = str(continuation.get("trade_id") or "").strip()
                    if not tid:
                        continue
                    legs = (
                        continuation.get("legs")
                        if isinstance(continuation.get("legs"), list)
                        else []
                    )
                    leg0 = legs[0] if legs and isinstance(legs[0], dict) else {}
                    aliases = quote_lookup_aliases(leg0) if leg0 else []
                    exact_q = lookup_quote_by_aliases(exact_by_occ, leg0 or aliases)
                    if not exact_q:
                        continue
                    chain_q = (
                        chain_by_trade.get(tid)
                        if isinstance(chain_by_trade.get(tid), dict)
                        else None
                    )
                    und_px = float(
                        und_map.get(str(continuation.get("symbol") or "").upper(), 0.0) or 0.0
                    )
                    snapshots[tid] = build_premium_snapshot(
                        trade_id=tid,
                        exact_quote=exact_q,
                        chain_quote=chain_q,
                        underlying_price=und_px if und_px > 0 else None,
                        requested_occ=str(
                            leg0.get("broker_occ_symbol") or leg0.get("option_symbol") or ""
                        ),
                        requested_aliases=aliases,
                        exact_failure_reason="",
                        chain_failure_reason="" if chain_q or exact_q else "chain_quote_missing",
                        broker_response_status="ok",
                        tick_id=sample_tick,
                    )
                system._unified_0dte_premium_snapshots_by_trade = snapshots
                observe_unified_monitor_continuations(
                    system,
                    continuations=conts,
                    exact_quotes_by_occ=exact_by_occ,
                    tick_id=sample_tick,
                    premium_snapshots_by_trade=snapshots,
                )
            except Exception as leftover_err:
                log.warning(
                    "PO_LEFTOVER_SHADOW | action=error | tick_id=%s | requested=%d | error=%s",
                    sample_tick,
                    len(syms),
                    leftover_err,
                )
            finally:
                log.info(
                    "PO_LEFTOVER_SHADOW | action=done | tick_id=%s | requested=%d | hits=%d | ms=%.0f",
                    sample_tick,
                    len(syms),
                    int(hits),
                    max(0.0, (time.monotonic() - started) * 1000.0),
                )

        self._leftover_shadow_task = asyncio.create_task(_run())
        log.info(
            "PO_LEFTOVER_SHADOW | action=scheduled | tick_id=%s | leftover=%d",
            sample_tick,
            len(syms),
        )
        return True

    async def maybe_tick(self, system: Any = None, *, trigger_source: str = "unified_fast") -> Optional[MonitorTickTelemetry]:
        """Monotonic gated tick — ORB and Trendline callers share this entry."""
        sys = system or self._system
        if sys is None:
            return None
        cfg = self._cfg()
        if not cfg.unified_monitor_enabled:
            return None

        positions = self._collect_open_positions(sys)
        continuations = self._collect_profit_optimizer_continuations(sys)
        if not positions and not continuations:
            try:
                from modules.open_position_quote_orchestration import mark_open_quote_priority

                mark_open_quote_priority(sys, active=False, open_count=0)
            except Exception:
                pass
            try:
                sys._0dte_force_monitor_tick = False
            except Exception:
                pass
            try:
                idle_iv = float(getattr(self._cfg(), "position_monitor_interval_sec", 2.0) or 2.0)
            except Exception:
                idle_iv = 2.0
            self._next_tick_monotonic = time.monotonic() + max(0.2, idle_iv)
            return None

        # Advertise open-quote priority as soon as we know opens exist (full duration).
        try:
            from modules.open_position_quote_orchestration import mark_open_quote_priority

            mark_open_quote_priority(
                sys,
                active=bool(positions),
                hold_sec=0.0,
                open_count=len(positions),
            )
        except Exception:
            pass

        now_m = time.monotonic()
        from modules.dte0_exit_monitor_config import effective_position_monitor_interval_sec

        interval = float(effective_position_monitor_interval_sec(len(positions)))
        try:
            from profit_optimizer.sampling_policy import align_recorder_to_monitor_interval

            align_recorder_to_monitor_interval(sys, interval)
        except Exception:
            pass

        if self._next_tick_monotonic <= 0:
            self._next_tick_monotonic = now_m

        force_immediate = bool(getattr(sys, "_0dte_force_monitor_tick", False))
        if now_m < self._next_tick_monotonic and not force_immediate:
            return None
        if force_immediate:
            try:
                sys._0dte_force_monitor_tick = False
            except Exception:
                pass
            pending_n = 0
            try:
                pending_n = len(getattr(sys, "_0dte_pending_monitor_registrations", None) or {})
            except Exception:
                pending_n = 0
            _fi_now = time.time()
            _fi_sec = 30.0
            if _fi_now - float(getattr(self, "_force_immediate_log_ts", 0.0) or 0.0) >= _fi_sec:
                self._force_immediate_log_ts = _fi_now
                log.info(
                    "UNIFIED_0DTE_MONITOR | action=force_immediate | trigger=%s | pending=%d",
                    trigger_source,
                    int(pending_n),
                )

        if self._lock.locked() or self._running:
            self._overlap_suppressions += 1
            self._overlap_streak += 1
            # Keep PO delivery on the monitor interval even while a heavy cycle runs.
            scheduled = self._next_tick_monotonic
            missed = now_m > scheduled + interval
            if missed:
                self._missed_deadlines += 1
                self._next_tick_monotonic = now_m + interval
            else:
                self._next_tick_monotonic = scheduled + interval
            self._deliver_po_samples_from_last_tick(
                sys,
                trigger_source=f"overlap|{trigger_source}",
                tick_id=str(self._last_tick_id or ""),
            )
            tel = MonitorTickTelemetry(
                overlapping_suppressed=True,
                monitor_tick_id=str(self._last_tick_id or ""),
                missed_deadline=missed,
                extras={
                    "po_overlap_sample": True,
                    "overlap_suppressions_total": self._overlap_suppressions,
                    "overlap_streak": self._overlap_streak,
                },
            )
            self._last_telemetry = tel
            warn = self._overlap_streak >= int(getattr(self, "_overlap_warn_streak", 5) or 5)
            log_fn = log.warning if warn else log.info
            log_fn(
                "UNIFIED_0DTE_MONITOR | overlapping_suppressed=true | count=%d | "
                "streak=%d | trigger=%s | po_overlap_sample=true%s",
                self._overlap_suppressions,
                self._overlap_streak,
                trigger_source,
                " | evaluate_starved=true" if warn else "",
            )
            return tel

        # Advance schedule by interval (processing time does not permanently stretch cadence)
        scheduled = self._next_tick_monotonic
        missed = now_m > scheduled + interval
        if missed:
            self._missed_deadlines += 1
            # Catch up: jump to now to avoid burst catch-up storms
            self._next_tick_monotonic = now_m + interval
        else:
            self._next_tick_monotonic = scheduled + interval

        async with self._lock:
            self._running = True
            self._overlap_streak = 0
            try:
                return await self._run_once(
                    sys,
                    positions=positions,
                    continuations=continuations,
                    trigger_source=trigger_source,
                    scheduled_at=scheduled,
                    missed_deadline=missed,
                    cfg=cfg,
                )
            finally:
                self._running = False
                self._pull_next_tick_after_quote_age_miss(interval)

    def _pull_next_tick_after_quote_age_miss(self, interval: float) -> None:
        """One sooner follow-up after quote_age_exceeded. Steady cadence stays 2s.

        A pulled tick cannot happen again until a full monitor interval has
        passed, so a stuck broker clock cannot turn the lane into a 1s poll.
        One extra batch per interval is about 1,800 extra Market quotes per
        hour at the ceiling, on top of the budgeted 1,800, still under 14,000.
        """
        if not getattr(self, "_quote_age_catchup", False):
            return
        self._quote_age_catchup = False
        now_m = time.monotonic()
        gap = max(1.0, float(interval or 2.0))
        last_pull = float(getattr(self, "_quote_age_pull_mono", 0.0) or 0.0)
        if last_pull > 0 and (now_m - last_pull) < gap:
            return
        sooner = now_m + min(1.0, gap)
        if sooner < float(self._next_tick_monotonic or 0.0):
            self._next_tick_monotonic = sooner
            self._quote_age_pull_mono = now_m
            log.info(
                "UNIFIED_0DTE_MONITOR | action=quote_age_catchup | next_in_s=%.2f | "
                "steady_interval_s=%.2f",
                min(1.0, gap),
                gap,
            )

    async def _run_once(
        self,
        system: Any,
        *,
        positions: List[Any],
        continuations: List[Dict[str, Any]],
        trigger_source: str,
        scheduled_at: float,
        missed_deadline: bool,
        cfg: Any,
    ) -> MonitorTickTelemetry:
        """One monitor cycle.

        Live order: broker sync → recollect authoritative positions → rebuild OCC
        → exact quotes → evaluate. Never evaluate the pre-sync object list.
        The inbound ``positions`` / ``continuations`` lists are wake-only hints.
        """
        started = time.time()
        started_m = time.monotonic()
        tick_id = canonical_monitor_tick_id(now=started)
        self._last_tick_id = tick_id
        self._ticks += 1
        await asyncio.sleep(0)  # Cloud Run /health shares this loop; yield before quote/eval work

        tel = MonitorTickTelemetry(
            monitor_tick_id=tick_id,
            scheduled_tick_at=scheduled_at,
            actual_tick_started_at=started_m,
            scheduler_delay_ms=max(0.0, (started_m - scheduled_at) * 1000.0),
            open_position_count=len(positions),
            missed_deadline=missed_deadline,
        )

        mode = self._mode_label(system)
        from modules.active_option_contract_registry import get_active_option_contract_registry
        from modules.dte0_exit_monitor_config import exit_grade_source_allowed
        from modules.open_position_recovery import rebuild_system_position_registry

        # --- Live pre-sync BEFORE final roster / quotes / evaluate ---
        system._unified_0dte_sync_gate_dispatch = False
        system._unified_0dte_skip_exit_eval = False
        executor = getattr(getattr(system, "dte0_manager", None), "options_executor", None)
        is_live = executor is not None and not getattr(executor, "demo_mode", True)
        if is_live:
            try:
                from modules.broker_position_synchronizer import (
                    get_or_create_synchronizer,
                    should_skip_normal_exits_for_stale_registry,
                )

                sync = get_or_create_synchronizer(system)
                sync_result = await sync.synchronize()
                stale = bool(
                    sync_result.registry_stale
                    or sync_result.sync_unavailable
                    or should_skip_normal_exits_for_stale_registry(system)
                )
                if stale:
                    system._unified_0dte_sync_gate_dispatch = True
                    log.warning(
                        "BROKER_POSITION_SYNC | stage=pre_monitor | ownership_gate_dispatch=true | "
                        "evaluate_exits=true | reason=%s",
                        sync_result.reason,
                    )
                else:
                    system._unified_0dte_sync_gate_dispatch = False
            except Exception as sync_err:
                system._unified_0dte_sync_gate_dispatch = True
                log.warning(
                    "BROKER_POSITION_SYNC | stage=pre_monitor | ownership_gate_dispatch=true | error=%s",
                    sync_err,
                )

        # Authoritative roster after sync (Demo + Live). Drop pre-sync object refs.
        rebuild_system_position_registry(system)
        positions = self._collect_open_positions(system)
        continuations = self._collect_profit_optimizer_continuations(system)
        tel.open_position_count = len(positions)
        tel.extras["wake_roster_discarded"] = True
        tel.extras["post_sync_recollect"] = True

        if is_live and not system._unified_0dte_sync_gate_dispatch:
            try:
                from modules.exit_latch import clear_ownership_gate

                for _p in positions:
                    clear_ownership_gate(_p)
            except Exception:
                pass

        if not positions and not continuations:
            tel.extras["idle_after_sync"] = True
            self._last_telemetry = tel
            return tel

        registry = get_active_option_contract_registry()
        contracts = registry.sync_from_positions(positions, mode=mode)
        from modules.open_position_quote_orchestration import (
            build_prioritized_exact_quote_plan,
            collect_open_position_quote_symbols,
            collect_shadow_continuation_symbols,
            leftover_shadow_max_per_tick,
            leftover_occ_may_fetch,
            leftover_shadow_quote_symbols,
            mark_open_quote_priority,
            record_open_quote_health,
            record_primary_batch_symbols,
            recover_missing_owned_exact_quotes,
            owned_symbols_missing_quotes,
            should_defer_chain_bridge,
            should_defer_po_continuation_observe,
            should_skip_leftover_quotes_this_tick,
        )

        open_syms = collect_open_position_quote_symbols(contracts)
        shadow_syms = collect_shadow_continuation_symbols(continuations)
        shadow_rot = int(getattr(system, "_0dte_shadow_quote_rotation_offset", 0) or 0)
        # Owned batch stays owned-only while anything is open. Winner leftover
        # OCC is quoted after evaluate on a single-flight task off this lock.
        quote_plan = build_prioritized_exact_quote_plan(
            open_option_symbols=open_syms,
            shadow_option_symbols=shadow_syms,
            max_shadow_while_open=0 if open_syms else None,
            shadow_rotation_offset=shadow_rot,
        )
        try:
            system._0dte_shadow_quote_rotation_offset = int(
                getattr(quote_plan, "shadow_rotation_next", shadow_rot) or shadow_rot
            )
        except Exception:
            pass
        symbols = list(quote_plan.symbols)
        tel.unique_contract_count = len(symbols)
        tel.extras["continuation_trade_count"] = len(continuations)
        tel.extras["quote_open_symbols"] = len(quote_plan.open_symbols)
        tel.extras["quote_shadow_symbols"] = len(quote_plan.shadow_symbols)
        tel.extras["quote_deduped_dropped"] = int(quote_plan.dropped_duplicates)
        tel.extras["quote_shadow_deferred"] = int(getattr(quote_plan, "shadow_deferred", 0) or 0)

        # Repair ledger OCC + shadow continuation symbols when stamped expiry
        # differs from listed chain expiry. Expensive multi-day chain walks — never
        # await on the open-position hot path (Aug 10: ~50–60s cycle_ms from inline repair).
        interval = float(cfg.position_monitor_interval_sec)
        if self._occ_repair_due(now_m=started_m, interval=interval):
            self._last_occ_repair_mono = started_m
            # While opens look healthy (prior tick), do not even background-repair —
            # chain walks still contend for the 2 rps Market API lane vs exact OCC.
            if positions and not self._last_exact_incomplete:
                tel.extras["occ_expiry_repair_skipped"] = True
                tel.extras["occ_expiry_repair_skip_reason"] = "open_exact_healthy"
                tel.extras["occ_expiry_repair_deferred_for_cadence"] = True
            else:
                owned_only = bool(positions)
                self._schedule_occ_repair(
                    system,
                    positions=list(positions),
                    continuations=list(continuations),
                    symbols=list(symbols),
                    owned_only=owned_only,
                )
                tel.extras["occ_expiry_repair_deferred_for_cadence"] = True
                tel.extras["occ_expiry_repair_mode"] = (
                    "background_owned_only" if owned_only else "background_full"
                )
        else:
            tel.extras["occ_expiry_repair_skipped"] = True
            tel.extras["occ_expiry_repair_deferred_for_cadence"] = True

        # Attach tick telemetry on system for downstream consumers
        system._unified_0dte_monitor_tick_id = tick_id
        system._unified_0dte_exact_quotes = {}
        system._unified_0dte_monitor_telemetry = tel
        from modules.po_session_tech_tick import (
            cached_underlying_quotes,
            open_underlying_symbols,
            prime_open_session_tech,
        )

        open_underlyings = open_underlying_symbols(positions)
        from modules.underlying_freshness import begin_underlying_tick
        from modules.underlying_spot_stamp import refresh_underlying_spot_cache_async

        und_task = None
        begin_underlying_tick(system, tick_id)
        if open_underlyings and not self.ul_refresh_task_alive():
            # Dedicated 2s UL loop owns skip_cache last when running, including
            # while a slow batch is still in flight (stalled, not cancelled).
            # In-tick join on stall restalls a 20–38 name book and ages
            # last_good past 8s. Tests / no-loop fallback still refresh here.
            und_task = asyncio.create_task(
                refresh_underlying_spot_cache_async(
                    system,
                    open_underlyings,
                    owned_only=True,
                    skip_cache=True,
                )
            )
        prime_started_m = time.monotonic()
        tech_task = asyncio.create_task(
            prime_open_session_tech(
                system,
                open_underlyings,
                quotes=cached_underlying_quotes(system, open_underlyings),
            )
        )

        # Open-position exact OCC owns broker quote budget for the full open duration.
        mark_open_quote_priority(
            system,
            active=bool(positions),
            hold_sec=0.0,
            tick_id=tick_id,
            open_count=len(positions),
        )
        record_primary_batch_symbols(system, symbols, tick_id=tick_id)

        # --- Exact-contract quotes (one prioritized batch per tick) ---
        tel.quote_request_started_at = time.time()
        from modules.dte0_exit_monitor_config import (
            effective_exit_quote_max_age_sec,
            exit_quote_soft_stale_ok,
        )

        _max_age = effective_exit_quote_max_age_sec(
            base=float(cfg.exit_quote_max_age_sec),
            positions=positions,
        )
        _soft_stale = bool(exit_quote_soft_stale_ok(positions=positions))
        tel.extras["exit_quote_max_age_sec"] = float(_max_age)
        tel.extras["exit_quote_soft_stale_ok"] = bool(_soft_stale)
        if self._should_defer_occ_for_ul(
            positions,
            max_age_sec=float(_max_age),
            soft_stale_ok=bool(_soft_stale),
        ):
            from modules.exact_contract_option_quotes import ExactQuoteBatchResult

            quote_result = ExactQuoteBatchResult(quotes={})
            quote_result.limitation = "occ_deferred_for_ul"
            tel.extras["occ_deferred_for_ul"] = True
            tel.extras["occ_evaluate_while_inflight"] = False
            log.info(
                "UNIFIED_0DTE_MONITOR | action=occ_deferred_for_ul | owned=%d | "
                "yield_s=%.1f | evaluate=last_exact",
                len(positions or []),
                max(0.0, time.monotonic() - float(self._occ_last_apply_mono or 0.0)),
            )
        else:
            quote_result = await self._await_primary_occ_quotes(
                symbols,
                system=system,
                cfg=cfg,
                timeout_sec=float(cfg.exit_quote_timeout_sec),
                max_age_sec=float(_max_age),
                soft_stale_ok=bool(_soft_stale),
                tel=tel,
            )
        try:
            from modules.option_api_accounting import record_real_broker_request

            n_real = int(getattr(quote_result, "request_count", 0) or 0)
            if n_real > 0:
                record_real_broker_request(system, lane="option", count=n_real)
        except Exception:
            pass
        await asyncio.sleep(0)
        tel.quote_response_received_at = time.time()
        tel.quote_batch_count = int(quote_result.batch_count)
        tel.quote_rtt_ms = float(quote_result.p95_rtt_ms() or 0.0)
        tel.quote_request_latency_ms = max(
            0.0, (tel.quote_response_received_at - tel.quote_request_started_at) * 1000.0
        )
        # Median broker quote age across accepted quotes (instrumentation only).
        ages: List[float] = []
        for _q in (quote_result.quotes or {}).values():
            if _q is None or getattr(_q, "rejected", False):
                continue
            age_ms = getattr(_q, "broker_quote_age_ms", None)
            if age_ms is None:
                age_ms = getattr(_q, "age_ms", None)
            if age_ms is not None:
                try:
                    ages.append(float(age_ms))
                except Exception:
                    pass
        if ages:
            ages_sorted = sorted(ages)
            tel.broker_quote_age_ms = ages_sorted[len(ages_sorted) // 2]
        tel.quote_parse_completed_at = time.time()
        tel.quote_refresh_wait_ms = max(
            0.0, (tel.quote_parse_completed_at - tel.quote_request_started_at) * 1000.0
        )

        exact_by_occ: Dict[str, Any] = {}
        exact_by_trade: Dict[str, Any] = {}
        try:
            from modules.canonical_contract_identity import register_quote_aliases
        except Exception:
            register_quote_aliases = None  # type: ignore

        # Map trade OCC ↔ wire quote symbol (osiKey / repaired colon).
        wire_by_occ: Dict[str, str] = {}
        for c in contracts:
            extras = c.extras if isinstance(getattr(c, "extras", None), dict) else {}
            wire = str(
                extras.get("broker_quote_request_symbol")
                or extras.get("osi_key")
                or c.option_symbol
                or ""
            ).strip()
            if c.option_symbol and wire:
                wire_by_occ[str(c.option_symbol)] = wire

        def _index_accepted_quotes(result_quotes: Dict[str, Any]) -> int:
            newly = 0
            for option_symbol, q in (result_quotes or {}).items():
                if q is None or q.rejected or not exit_grade_source_allowed(q.source) or q.bid is None:
                    continue
                q_dict = q.to_option_quote_dict()
                if option_symbol in exact_by_occ and isinstance(exact_by_occ.get(option_symbol), dict):
                    exact_by_occ[option_symbol] = q_dict
                    continue
                tel.exact_quotes_ok += 1
                newly += 1
                exact_by_occ[option_symbol] = q_dict
                for occ_sym, wire_sym in wire_by_occ.items():
                    if wire_sym == option_symbol and occ_sym:
                        exact_by_occ[occ_sym] = q_dict
                if register_quote_aliases is not None:
                    register_quote_aliases(
                        exact_by_occ,
                        q_dict,
                        primary_key=option_symbol,
                        extra_aliases=[
                            str(getattr(q, "option_symbol", "") or ""),
                            str((q.raw or {}).get("osiKey") or "")
                            if isinstance(getattr(q, "raw", None), dict)
                            else "",
                            *[occ for occ, wire in wire_by_occ.items() if wire == option_symbol],
                        ],
                    )
            return newly

        # Count primary rejects before indexing accepted rows.
        for option_symbol in symbols:
            q = quote_result.quotes.get(option_symbol)
            if q is None or q.rejected or not exit_grade_source_allowed(q.source) or q.bid is None:
                tel.exact_quotes_rejected += 1
        _index_accepted_quotes(quote_result.quotes)

        # Owned miss → one isolated single-contract recovery (not a duplicate full batch).
        owned_missing = owned_symbols_missing_quotes(
            open_symbols=quote_plan.open_symbols,
            quote_result_quotes=quote_result.quotes,
            exact_by_occ=exact_by_occ,
        )
        tel.extras["owned_exact_missing_pre_recovery"] = len(owned_missing)
        if (
            owned_missing
            and not tel.extras.get("occ_evaluate_while_inflight")
            and not tel.extras.get("occ_deferred_for_ul")
        ):
            recovery = await recover_missing_owned_exact_quotes(
                owned_missing,
                data_manager=getattr(system, "data_manager", None),
                etrade_trader=getattr(
                    getattr(system, "data_manager", None), "etrade_trader", None
                ),
                timeout_sec=float(cfg.exit_quote_timeout_sec),
                max_age_sec=float(_max_age),
                tick_id=tick_id,
                primary_batch_symbols=set(symbols),
                soft_stale_ok=bool(_soft_stale),
            )
            tel.extras["owned_recovery_attempted"] = len(owned_missing)
            tel.quote_batch_count = int(tel.quote_batch_count or 0) + int(
                getattr(recovery, "batch_count", 0) or 0
            )
            tel.extras["owned_recovery_hits"] = _index_accepted_quotes(
                getattr(recovery, "quotes", None) or {}
            )
            tel.extras["owned_recovery_deferred"] = int(
                getattr(recovery, "symbols_deferred", 0) or 0
            )
            # Merge recovery rows into primary result for reliability accounting.
            for k, q in (getattr(recovery, "quotes", None) or {}).items():
                quote_result.quotes[k] = q
            owned_missing = owned_symbols_missing_quotes(
                open_symbols=quote_plan.open_symbols,
                quote_result_quotes=quote_result.quotes,
                exact_by_occ=exact_by_occ,
            )
        tel.extras["owned_exact_missing_post_recovery"] = len(owned_missing)
        record_open_quote_health(
            system,
            tick_id=tick_id,
            open_count=len(positions),
            owned_missing_count=len(owned_missing),
            recovery_attempted=int(tel.extras.get("owned_recovery_attempted", 0) or 0),
            recovery_deferred=int(tel.extras.get("owned_recovery_deferred", 0) or 0),
            quote_latency_ms=float(tel.quote_request_latency_ms or 0.0),
            quote_batch_count=int(tel.quote_batch_count or 0),
        )

        # Apply quote observations through synchronizer (Demo + Live sole-writer path).
        try:
            from modules.broker_position_synchronizer import get_or_create_synchronizer

            _quote_sync = get_or_create_synchronizer(system)
        except Exception:
            _quote_sync = None

        for c in contracts:
            wire = wire_by_occ.get(str(c.option_symbol), str(c.option_symbol))
            q = quote_result.quotes.get(c.option_symbol) or quote_result.quotes.get(wire)
            oq = exact_by_occ.get(c.option_symbol) or exact_by_occ.get(wire)
            rel = self._quote_reliability(c.trade_id)
            req_sym = str(
                (getattr(q, "raw", None) or {}).get("request_parameter_sent")
                if isinstance(getattr(q, "raw", None), dict)
                else wire
                or c.option_symbol
                or ""
            )
            rel.record_request(request_symbol=req_sym or str(wire or c.option_symbol or ""))
            pos = next((p for p in positions if str(getattr(p, "position_id", "")) == c.trade_id), None)
            meta = getattr(pos, "metadata", None) if pos is not None else None
            if oq is None:
                rejection_reason = (
                    str(q.rejection_reason or q.source)
                    if q is not None
                    else "missing_exact_quote"
                )
                rejection_source = str(q.source) if q is not None else "missing"
                raw = getattr(q, "raw", None) if q is not None else None
                raw_d = raw if isinstance(raw, dict) else {}
                from modules.exit_observation_telemetry import (
                    classify_exact_quote_miss,
                    persist_reliability_on_meta,
                )

                miss_class = classify_exact_quote_miss(
                    request_parameter_sent=str(
                        raw_d.get("request_parameter_sent") or req_sym or wire or ""
                    ),
                    rejection_reason=rejection_reason,
                    response_contract_count=raw_d.get("response_contract_count"),
                    match_method=str(raw_d.get("match_method") or ""),
                    recovery_attempted=bool(raw_d.get("recovery_attempted")),
                    recovery_succeeded=bool(raw_d.get("recovery_succeeded")),
                    frozen_wire=str(wire or ""),
                )
                reused = self._reusable_quote_for_trade(
                    pos,
                    c.trade_id,
                    max_age_sec=float(_max_age),
                    now_ts=time.time(),
                    soft_stale_ok=bool(_soft_stale),
                )
                fallback_src = "reused_last_exact" if reused is not None else rejection_source
                rel.record_miss(
                    miss_class=miss_class,
                    fallback_source=fallback_src,
                    now_mono=time.monotonic(),
                )
                if isinstance(meta, dict):
                    persist_reliability_on_meta(meta, rel)
                if reused is not None:
                    # Evaluate exits on last trusted exact while refresh continues async.
                    exact_by_trade[c.trade_id] = reused
                    exact_by_occ.setdefault(str(c.option_symbol), reused)
                    tel.exact_quotes_reused += 1
                    if _quote_sync is not None and executor is not None:
                        _quote_sync.apply_quote_observation(
                            executor,
                            c.trade_id,
                            awaiting_market_data=True,
                            exact_quote=reused,
                            quote_rejection_reason=rejection_reason,
                            quote_source="exact_quote",
                            tick_id=tick_id,
                        )
                    elif isinstance(meta, dict):
                        meta["awaiting_market_data"] = True
                        meta["quote_rejection_reason"] = rejection_reason
                        meta["exact_quote"] = reused
                        meta["last_quote_source"] = "exact_quote"
                        meta["exact_quote_miss_class"] = miss_class
                        meta["_unified_0dte_monitor_tick_id"] = tick_id
                    log.debug(
                        "EXIT_QUOTE_REUSED_LAST_EXACT | trade_id=%s | occ=%s | reason=%s | "
                        "miss_class=%s | mode=%s",
                        c.trade_id,
                        c.option_symbol,
                        rejection_reason,
                        miss_class,
                        mode,
                    )
                    continue
                if _quote_sync is not None and executor is not None:
                    _quote_sync.apply_quote_observation(
                        executor,
                        c.trade_id,
                        awaiting_market_data=True,
                        quote_rejection_reason=rejection_reason,
                        quote_source=rejection_source,
                        tick_id=tick_id,
                    )
                elif isinstance(meta, dict):
                    meta["awaiting_market_data"] = True
                    meta["quote_rejection_reason"] = rejection_reason
                    meta["last_quote_source"] = rejection_source
                    meta["exact_quote_miss_class"] = miss_class
                # Rate-limit awaiting spam when no reusable mark.
                last_await_log = 0.0
                if isinstance(meta, dict):
                    try:
                        last_await_log = float(meta.get("_awaiting_md_log_mono") or 0.0)
                    except Exception:
                        last_await_log = 0.0
                now_mono = time.monotonic()
                if (now_mono - last_await_log) >= 15.0:
                    if isinstance(meta, dict):
                        meta["_awaiting_md_log_mono"] = now_mono
                    log.warning(
                        "EXIT_QUOTE_AWAITING_MARKET_DATA | trade_id=%s | occ=%s | reason=%s | "
                        "miss_class=%s | mode=%s",
                        c.trade_id,
                        c.option_symbol,
                        rejection_reason,
                        miss_class,
                        mode,
                    )
                if "quote_age_exceeded" in str(rejection_reason or ""):
                    self._quote_age_catchup = True
                continue
            # Success path
            from modules.exit_observation_telemetry import (
                infer_symbol_format,
                persist_reliability_on_meta,
            )

            bid_age_sec = None
            try:
                age_ms = oq.get("broker_quote_age_ms")
                if age_ms is None:
                    age_ms = oq.get("quote_age_ms")
                if age_ms is not None:
                    bid_age_sec = float(age_ms) / 1000.0
                elif oq.get("quote_age_seconds") is not None:
                    bid_age_sec = float(oq.get("quote_age_seconds"))
            except (TypeError, ValueError):
                bid_age_sec = None
            raw_ok = getattr(q, "raw", None) if q is not None else None
            raw_ok_d = raw_ok if isinstance(raw_ok, dict) else {}
            win_fmt = str(
                raw_ok_d.get("winning_symbol_format")
                or infer_symbol_format(
                    str(raw_ok_d.get("request_parameter_sent") or req_sym or wire or "")
                )
            )
            rel.record_success(
                bid_age_sec=bid_age_sec,
                winning_symbol_format=win_fmt,
                recovery_used=bool(raw_ok_d.get("recovery_succeeded")),
                now_mono=time.monotonic(),
            )
            if isinstance(meta, dict):
                persist_reliability_on_meta(meta, rel)
                meta["exact_quote_miss_class"] = None
                meta["_unified_0dte_monitor_tick_id"] = tick_id
                osnap_owned = meta.get("option_stealth")
                if isinstance(osnap_owned, dict) and isinstance(oq, dict):
                    try:
                        from modules.po_lifecycle_snapshots import record_owned_contract_sample

                        record_owned_contract_sample(osnap_owned, oq, meta)
                    except Exception:
                        pass
            exact_by_trade[c.trade_id] = oq
            if _quote_sync is not None and executor is not None:
                _quote_sync.apply_quote_observation(
                    executor,
                    c.trade_id,
                    awaiting_market_data=False,
                    exact_quote=oq,
                    quote_source="exact_quote",
                    tick_id=tick_id,
                )
            else:
                pos = next((p for p in positions if str(getattr(p, "position_id", "")) == c.trade_id), None)
                if pos is not None:
                    meta = getattr(pos, "metadata", None)
                    if isinstance(meta, dict):
                        meta["awaiting_market_data"] = False
                        meta["exact_quote"] = oq
                        meta["last_quote_source"] = "exact_quote"
                        meta["_unified_0dte_monitor_tick_id"] = tick_id

        system._unified_0dte_exact_quotes = exact_by_trade
        system._unified_0dte_exact_quotes_by_occ = exact_by_occ
        if int(tel.exact_quotes_ok or 0) > 0:
            stored: Dict[str, Dict[str, Any]] = {}
            for tid, q in exact_by_trade.items():
                if not tid or not isinstance(q, dict) or q.get("bid") is None:
                    continue
                blob = dict(q)
                if not blob.get("source"):
                    blob["source"] = "exact_quote"
                blob["quote_source"] = str(blob.get("quote_source") or "exact_quote")
                stored[str(tid)] = blob
            self._occ_last_apply_quotes = stored
        self._stamp_option_quote_eval_extras(
            tel,
            positions=positions,
            exact_by_trade=exact_by_trade,
            max_age_sec=float(_max_age),
            occ_deferred=bool(tel.extras.get("occ_deferred_for_ul")),
        )

        # Bridge: force-refresh chains when exact quotes incomplete for open
        # positions OR shadow continuations (shared snapshot for PO).
        from modules.canonical_contract_identity import lookup_quote_by_aliases

        open_exact_ok = sum(
            1
            for c in contracts
            if lookup_quote_by_aliases(exact_by_occ, c.option_symbol) is not None
            or c.option_symbol in exact_by_occ
        )
        cont_missing = []
        for continuation in continuations:
            tid = str(continuation.get("trade_id") or "")
            legs = continuation.get("legs") if isinstance(continuation.get("legs"), list) else []
            hit = False
            for leg in legs:
                if isinstance(leg, dict) and lookup_quote_by_aliases(exact_by_occ, leg) is not None:
                    hit = True
                    break
                sym = str((leg or {}).get("option_symbol") if isinstance(leg, dict) else "")
                if sym and sym in exact_by_occ:
                    hit = True
                    break
            if not hit and tid:
                cont_missing.append(continuation)
        owned_missing_n = max(0, len(contracts) - open_exact_ok) if contracts else 0
        defer_bridge, defer_bridge_reason = should_defer_chain_bridge(
            open_position_count=len(positions),
            owned_missing_count=owned_missing_n,
            continuation_missing_count=len(cont_missing),
        )
        need_bridge = bool(cfg.force_chain_refresh_bridge) and (
            (open_exact_ok < len(contracts) and len(contracts) > 0) or bool(cont_missing)
        )
        if need_bridge and defer_bridge:
            tel.extras["bridge_deferred_for_open_priority"] = True
            tel.extras["bridge_defer_reason"] = defer_bridge_reason
            need_bridge = False
            now_bridge_log = time.monotonic()
            last_bridge_log = float(getattr(self, "_last_bridge_defer_log_mono", 0.0) or 0.0)
            if (now_bridge_log - last_bridge_log) >= 15.0:
                self._last_bridge_defer_log_mono = now_bridge_log
                log.info(
                    "EXACT_CONTRACT_QUOTE_BRIDGE | deferred=open_position_priority | reason=%s | "
                    "ok=%d | total=%d | continuation_missing=%d",
                    defer_bridge_reason,
                    open_exact_ok,
                    len(contracts),
                    len(cont_missing),
                )
        tel.used_chain_bridge = need_bridge
        # Only owned open-position incompleteness drives aggressive OCC-repair retry.
        # Shadow continuation misses are expected while open (bridge deferred) and must
        # not force a 10s multi-day chain-walk schedule.
        self._last_exact_incomplete = bool(
            open_exact_ok < len(contracts) and len(contracts) > 0
        )
        # Never let chain hydration stretch past the open-position monitor interval.
        elapsed_pre_bridge = time.monotonic() - started_m
        bridge_budget_ok = elapsed_pre_bridge < max(0.25, float(interval) * 0.55)
        if need_bridge and not bridge_budget_ok:
            tel.extras["bridge_deferred_for_cadence"] = True
            tel.extras["bridge_elapsed_pre_sec"] = round(elapsed_pre_bridge, 3)
            need_bridge = False
        if need_bridge:
            log.warning(
                "EXACT_CONTRACT_QUOTE_BRIDGE | force_chain_refresh | ok=%d | total=%d | "
                "continuation_missing=%d | limitation=%s",
                open_exact_ok,
                len(contracts),
                len(cont_missing),
                quote_result.limitation or "partial_exact_quotes",
            )

        # Delegate evaluate + same-cycle dispatch to existing monitors with injected quotes.
        # exact_contract_batch: never force full-chain hydrate on the 2s tick — that path
        # was the 10–20s stretch. Missing marks use last exact / background bridge cache.
        shared_cache: Dict = {}
        force_syms: Set[str] = set()
        if cfg.exit_quote_mode != "exact_contract_batch":
            force_syms = {
                str(getattr(p, "symbol", "") or "").upper()
                for p in positions
                if str(getattr(p, "symbol", "") or "").strip()
            }
        elif open_exact_ok < len(contracts) and len(contracts) > 0:
            tel.extras["force_chain_hydrate_skipped_for_cadence"] = True

        # Shared premium snapshots for Profit Optimizer (exact + matched chain).
        # Prefer last bridge cache; shadow-only bridge yields while any 0DTE is open.
        chain_by_trade: Dict[str, Any] = {}
        prev_chain = getattr(system, "_unified_0dte_chain_quotes_by_trade", None)
        if isinstance(prev_chain, dict):
            chain_by_trade = dict(prev_chain)
        if (
            cont_missing
            and bool(cfg.force_chain_refresh_bridge)
            and not defer_bridge
        ):
            self._schedule_bridge_refresh(
                system,
                cont_missing,
                max_age_sec=float(_max_age),
                now_m=time.monotonic(),
                interval=interval,
            )
            tel.extras["chain_bridge_background"] = True
        elif cont_missing and defer_bridge:
            tel.extras["chain_bridge_background_deferred"] = True
            tel.extras["bridge_defer_reason"] = defer_bridge_reason
        # Do NOT re-schedule a duplicate full-batch refresh of this tick's symbols.
        tel.extras["exact_quote_async_refresh"] = False
        tel.extras["exact_quote_orchestration"] = "prioritized_batch_plus_owned_recovery"
        system._unified_0dte_chain_quotes_by_trade = chain_by_trade
        # Shared exact snapshot for exit monitor + Profit Optimizer (same tick).
        system._unified_0dte_exact_quotes_by_occ = dict(exact_by_occ)
        system._unified_0dte_exact_quote_plan = {
            "open_symbols": list(quote_plan.open_symbols),
            "shadow_symbols": list(quote_plan.shadow_symbols),
            "dropped_duplicates": int(quote_plan.dropped_duplicates),
            "tick_id": tick_id,
        }

        premium_snapshots_by_trade: Dict[str, Any] = {}
        try:
            from modules.monitor_premium_snapshot import build_premium_snapshot
            from modules.canonical_contract_identity import (
                lookup_quote_by_aliases,
                quote_lookup_aliases,
            )
            if und_task is not None:
                try:
                    await und_task
                except Exception as und_err:
                    log.debug(
                        "UNIFIED_0DTE_MONITOR | action=owned_underlying_refresh_failed | error=%s",
                        und_err,
                    )

            for continuation in continuations:
                tid = str(continuation.get("trade_id") or "").strip()
                if not tid:
                    continue
                legs = continuation.get("legs") if isinstance(continuation.get("legs"), list) else []
                leg0 = legs[0] if legs and isinstance(legs[0], dict) else {}
                aliases = quote_lookup_aliases(leg0) if leg0 else []
                exact_q = lookup_quote_by_aliases(exact_by_occ, leg0 or aliases)
                chain_q = chain_by_trade.get(tid) if isinstance(chain_by_trade.get(tid), dict) else None
                und_px = float(
                    getattr(system, "_orb_0dte_last_underlying_by_symbol", {}).get(
                        str(continuation.get("symbol") or "").upper(), 0.0
                    )
                    or 0.0
                )
                premium_snapshots_by_trade[tid] = build_premium_snapshot(
                    trade_id=tid,
                    exact_quote=exact_q,
                    chain_quote=chain_q,
                    underlying_price=und_px if und_px > 0 else None,
                    requested_occ=str(
                        leg0.get("broker_occ_symbol") or leg0.get("option_symbol") or ""
                    ),
                    requested_aliases=aliases,
                    exact_failure_reason=(
                        ""
                        if exact_q
                        else f"exact_quote_missing:{aliases[0] if aliases else 'unknown'}"
                    ),
                    chain_failure_reason="" if chain_q or exact_q else "chain_quote_missing",
                    broker_response_status="ok" if (exact_q or chain_q) else "miss",
                    tick_id=tick_id,
                )
        except Exception as snap_err:
            log.warning("MONITOR_PREMIUM_SNAPSHOT | error=%s | tick_id=%s", snap_err, tick_id)
        system._unified_0dte_premium_snapshots_by_trade = premium_snapshots_by_trade

        system._unified_0dte_quote_context = {
            "exact_quotes_complete": not need_bridge and cfg.exit_quote_mode == "exact_contract_batch",
            "missing_contracts": max(0, len(contracts) - open_exact_ok),
            "bridge_underlyings": sorted(force_syms),
            "source_trigger": trigger_source,
            "tick_id": tick_id,
        }

        eval_counts = {
            "orb_positions_collected": 0,
            "orb_positions_evaluated": 0,
            "trendline_positions_collected": 0,
            "trendline_positions_evaluated": 0,
            "duplicate_same_tick_suppressed": 0,
            "full_chain_requests": 0,
            "eval_budget_deferred": 0,
        }
        system._unified_0dte_eval_counts = eval_counts

        try:
            from modules.po_session_tech_tick import finish_prime_with_quote_wait

            await finish_prime_with_quote_wait(
                system,
                tech_task,
                started_mono=prime_started_m,
            )
            from modules.po_session_tech_tick import session_tech_tick_failed

            tel.extras["session_tech_tick_primed"] = True
            tel.extras["session_tech_tick_failed"] = bool(session_tech_tick_failed(system))
        except Exception as tech_exc:
            tel.extras["session_tech_tick_primed"] = False
            tel.extras["session_tech_tick_error"] = str(tech_exc)
        eval_started_m = time.monotonic()
        if positions and hasattr(system, "monitor_0dte_options_positions"):
            try:
                from modules.unified_0dte_monitor_registry import begin_monitor_eval_budget

                begin_monitor_eval_budget(system, open_count=len(positions))
            except Exception:
                pass
            await system.monitor_0dte_options_positions(
                trigger_source=f"unified|{trigger_source}",
                shared_chain_cache=shared_cache,
                tick_id=tick_id,
                force_refresh_symbols=force_syms,
                exact_contract_quotes=exact_by_trade,
                sync_already_done=True,
                ownership_gate_dispatch=bool(system._unified_0dte_sync_gate_dispatch),
            )
        eval_ms = max(0.0, (time.monotonic() - eval_started_m) * 1000.0)

        leftover_started = time.monotonic()
        leftover_syms: List[str] = []
        leftover_busy = (
            self._leftover_shadow_task is not None
            and not self._leftover_shadow_task.done()
        )
        skip_po_cont = should_defer_po_continuation_observe(
            trigger_source=str(trigger_source or ""),
            open_count=len(positions),
        )
        last_cycle_ms = 0.0
        try:
            last_cycle_ms = float(
                (getattr(system, "_unified_0dte_monitor_cadence", None) or {}).get(
                    "total_cycle_ms"
                )
                or 0.0
            )
        except Exception:
            last_cycle_ms = 0.0
        eval_budget_hit = bool(getattr(system, "_unified_0dte_eval_budget_hit", False))
        skip_leftover_quotes = should_skip_leftover_quotes_this_tick(
            last_cycle_ms=last_cycle_ms,
            eval_budget_hit=eval_budget_hit,
            open_count=len(positions),
        )
        observe_on_owned_tick = False
        if skip_leftover_quotes:
            if eval_budget_hit:
                tel.extras["quote_shadow_leftover_skipped"] = "eval_budget"
            elif positions:
                tel.extras["quote_shadow_leftover_skipped"] = "open_book"
            else:
                tel.extras["quote_shadow_leftover_skipped"] = "slow_monitor"
        if leftover_busy:
            tel.extras["quote_shadow_leftover_skipped"] = "leftover_busy"
            if continuations and not skip_po_cont and not skip_leftover_quotes:
                try:
                    from profit_optimizer.trading_hooks import (
                        observe_unified_monitor_continuations,
                    )

                    observe_unified_monitor_continuations(
                        system,
                        continuations=list(continuations),
                        exact_quotes_by_occ=dict(exact_by_occ),
                        tick_id=str(tick_id or ""),
                        premium_snapshots_by_trade=getattr(
                            system, "_unified_0dte_premium_snapshots_by_trade", None
                        ),
                        sample_at_now=True,
                    )
                    tel.extras["quote_shadow_leftover_sampled"] = "cached_while_busy"
                except Exception:
                    log.debug("cached shadow sample while leftover busy failed", exc_info=True)
        if continuations and shadow_syms and not skip_po_cont:
            leftover_rot = int(
                getattr(system, "_0dte_shadow_leftover_rotation_offset", 0) or 0
            )
            leftover_cap = leftover_shadow_max_per_tick()
            leftover_syms, leftover_deferred, leftover_next = leftover_shadow_quote_symbols(
                shadow_syms,
                already_quoted=list(exact_by_occ.keys()),
                max_shadow=int(leftover_cap),
                rotation_offset=leftover_rot,
            )
            tel.extras["quote_shadow_leftover_requested"] = len(leftover_syms)
            tel.extras["quote_shadow_leftover_deferred"] = int(leftover_deferred)
            if leftover_syms:
                if leftover_occ_may_fetch(
                    skip_leftover_quotes=skip_leftover_quotes,
                    leftover_busy=leftover_busy,
                    leftover_syms=leftover_syms,
                ):
                    scheduled = self._schedule_leftover_shadow_quotes(
                        system,
                        leftover_syms=leftover_syms,
                        leftover_next=leftover_next,
                        continuations=list(continuations),
                        tick_id=str(tick_id or ""),
                        timeout_sec=float(cfg.exit_quote_timeout_sec),
                        max_age_sec=float(_max_age),
                        concurrency=int(cfg.quote_batch_concurrency),
                        owned_underlyings=[
                            str(getattr(p, "symbol", "") or "").strip().upper()
                            for p in (positions or [])
                            if str(getattr(p, "symbol", "") or "").strip()
                        ],
                    )
                    tel.extras["quote_shadow_leftover_scheduled"] = bool(scheduled)
            else:
                observe_on_owned_tick = True
        elif continuations and not skip_po_cont:
            observe_on_owned_tick = True
        tel.extras["po_shadow_leftover_ms"] = round(
            (time.monotonic() - leftover_started) * 1000.0, 1
        )

        po_started = time.monotonic()
        if observe_on_owned_tick and continuations and not skip_po_cont:
            try:
                from profit_optimizer.trading_hooks import (
                    observe_unified_monitor_continuations,
                )

                observe_unified_monitor_continuations(
                    system,
                    continuations=continuations,
                    exact_quotes_by_occ=exact_by_occ,
                    tick_id=tick_id,
                    premium_snapshots_by_trade=getattr(
                        system, "_unified_0dte_premium_snapshots_by_trade", None
                    ),
                )
            except Exception as exc:
                log.warning(
                    "PROFIT_OPTIMIZER_CONTINUATION | action=observe_failed | "
                    "tick_id=%s | error=%s",
                    tick_id,
                    exc,
                )
        elif skip_po_cont and continuations:
            log.info(
                "PROFIT_OPTIMIZER_CONTINUATION | action=defer | reason=protect_exit_cadence | "
                "tick_id=%s | open=%d | leftover=%d | trigger=%s",
                tick_id,
                len(positions),
                len(leftover_syms),
                trigger_source,
            )
        tel.extras["po_observe_ms"] = round((time.monotonic() - po_started) * 1000.0, 1)
        tel.extras["po_deferred"] = bool(skip_po_cont and continuations)
        tel.extras["po_observe_on_owned_tick"] = bool(observe_on_owned_tick)
        tel.decision_runtime_ms = eval_ms

        ended = time.time()
        ended_m = time.monotonic()
        tel.monitor_cycle_completed_at = ended
        tel.monitor_cycle_duration_ms = max(0.0, (ended - started) * 1000.0)
        tel.monitor_runtime_ms = tel.monitor_cycle_duration_ms
        tel.total_cycle_ms = max(0.0, (ended_m - started_m) * 1000.0)
        tel.scheduler_interval_ms = float(interval) * 1000.0
        counts = getattr(system, "_unified_0dte_eval_counts", None) or eval_counts
        tel.extras["eval_counts"] = dict(counts)
        suppressed = int(counts.get("duplicate_same_tick_suppressed") or 0)
        evaluated = int(counts.get("orb_positions_evaluated") or 0) + int(
            counts.get("trendline_positions_evaluated") or 0
        )
        collected = int(counts.get("orb_positions_collected") or 0) + int(
            counts.get("trendline_positions_collected") or 0
        )
        tel.extras["evaluation_complete"] = suppressed == 0 or evaluated > 0
        tel.extras["eval_budget_hit"] = bool(getattr(system, "_unified_0dte_eval_budget_hit", False))
        tel.extras["eval_budget_deferred"] = int(counts.get("eval_budget_deferred") or 0)
        tel.extras["positions_evaluated"] = evaluated
        tel.extras["positions_eval_skipped"] = int(
            tel.extras.get("eval_budget_deferred") or 0
        ) + int(tel.extras.get("positions_skipped_no_bid") or 0)
        if evaluated <= 0:
            # Load-test / stub evaluate: handoff count is the bid set.
            tel.extras["positions_evaluated"] = int(
                tel.extras.get("positions_handed_evaluate") or 0
            )
        if tel.extras["eval_budget_hit"]:
            log.info(
                "UNIFIED_0DTE_MONITOR | eval_budget_hit=true | evaluated=%s | deferred=%s | "
                "trigger=%s | tick_id=%s",
                evaluated,
                tel.extras["eval_budget_deferred"],
                trigger_source,
                tick_id,
            )
        self._record_position_cadence(positions, now_m=ended_m)
        self._classify_bottlenecks(tel, interval=interval)
        self._maybe_log_cadence_summary(now_m=ended_m, tel=tel)
        self._maybe_log_quote_reliability_summary(now_m=ended_m, positions=positions)
        self._last_completed_monotonic = ended_m
        self._last_telemetry = tel
        system._orb_0dte_last_monitor_cycle_completed_ts = ended
        system._trendline_last_fast_monitor_ts = ended
        system._orb_0dte_last_fast_monitor_ts = ended
        system._unified_0dte_monitor_cadence = {
            "tick_id": tick_id,
            "scheduler_interval_ms": tel.scheduler_interval_ms,
            "monitor_runtime_ms": tel.monitor_runtime_ms,
            "quote_request_latency_ms": tel.quote_request_latency_ms,
            "broker_quote_age_ms": tel.broker_quote_age_ms,
            "quote_refresh_wait_ms": tel.quote_refresh_wait_ms,
            "decision_runtime_ms": tel.decision_runtime_ms,
            "total_cycle_ms": tel.total_cycle_ms,
            "exact_ok": tel.exact_quotes_ok,
            "exact_rej": tel.exact_quotes_rejected,
            "exact_reused": tel.exact_quotes_reused,
            "bottlenecks": dict(self._bottleneck_counts),
            "per_position": {
                tid: stats.summary()
                for tid, stats in self._cadence_by_trade.items()
                if stats.intervals_ms
            },
        }

        now_m = time.monotonic()
        times = getattr(self, "_cycle_times", None)
        if not isinstance(times, list):
            times = []
            self._cycle_times = times
        times.append(now_m)
        self._cycle_times = [t for t in times if now_m - t <= 60.0]
        cycles_per_min = len(self._cycle_times)
        interval_p50_ms = 0.0
        interval_p95_ms = 0.0
        interval_max_ms = 0.0
        if len(self._cycle_times) >= 2:
            ordered = list(self._cycle_times)
            diffs = sorted((ordered[i] - ordered[i - 1]) * 1000.0 for i in range(1, len(ordered)))
            n_i = len(diffs)
            interval_p50_ms = (
                diffs[n_i // 2]
                if n_i % 2 == 1
                else 0.5 * (diffs[n_i // 2 - 1] + diffs[n_i // 2])
            )
            interval_p95_ms = diffs[min(n_i - 1, max(0, int(round(0.95 * (n_i - 1)))))]
            interval_max_ms = diffs[-1]
        cadence_out = getattr(system, "_unified_0dte_monitor_cadence", None)
        if isinstance(cadence_out, dict):
            cadence_out["missed_deadline"] = bool(tel.missed_deadline)
            cadence_out["scheduler_delay_ms"] = float(tel.scheduler_delay_ms or 0.0)
            cadence_out["cycles_per_min"] = int(cycles_per_min)
            cadence_out["interval_p50_ms"] = float(interval_p50_ms)
        api_snap: Dict[str, Any] = {}
        try:
            from modules.option_api_accounting import snapshot_usage

            api_snap = snapshot_usage(self._system) or {}
        except Exception:
            api_snap = {}
        po_tel: Dict[str, Any] = {}
        try:
            po = getattr(self._system, "profit_optimizer", None) or getattr(
                self._system, "_profit_optimizer", None
            )
            rec = getattr(po, "recorder", None) if po is not None else None
            if rec is not None and hasattr(rec, "persist_telemetry"):
                po_tel = rec.persist_telemetry() or {}
        except Exception:
            po_tel = {}
        try:
            from modules.option_api_accounting import record_monitor_cycle

            record_monitor_cycle(self._system)
        except Exception:
            pass
        und_stats = getattr(system, "_unified_und_tick_stats", None) or {}
        ul_ages = sorted(float(a) for a in (und_stats.get("ages_ms") or []) if a is not None)
        ul_age_p50 = _percentile_sorted(ul_ages, 50.0)
        ul_age_p95 = _percentile_sorted(ul_ages, 95.0)
        ul_unavailable = int(und_stats.get("unavailable") or 0)

        log.info(
            "UNIFIED_0DTE_MONITOR | tick_id=%s | trigger=%s | open=%d | contracts=%d | "
            "exact_ok=%d | exact_rej=%d | exact_reused=%d | bridge=%s | full_chain_requests=%s | "
            "orb_collected=%s | orb_evaluated=%s | tl_collected=%s | tl_evaluated=%s | "
            "duplicate_same_tick_suppressed=%s | cycle_ms=%.1f | quote_lat_ms=%.1f | "
            "broker_age_ms=%.1f | decision_ms=%.1f | delay_ms=%.1f | missed=%s | "
            "cycles_per_min=%d | interval_p50_ms=%.1f | interval_p95_ms=%.1f | "
            "interval_max_ms=%.1f | occ_broker_win=%d | equity_broker_win=%d | "
            "real_broker_win=%d | ul_age_ms_p50=%.1f | ul_age_ms_p95=%.1f | "
            "ul_unavailable=%d | po_persist_queue_depth=%d | oldest_unpersisted_age_ms=%.1f | "
            "last_successful_persist_age_ms=%.1f | with_bid=%s | missing_bid=%s | "
            "eval_skip_class=%s | oq_age_ms_p95=%s",
            tick_id,
            trigger_source,
            tel.open_position_count,
            tel.unique_contract_count,
            tel.exact_quotes_ok,
            tel.exact_quotes_rejected,
            tel.exact_quotes_reused,
            str(tel.used_chain_bridge).lower(),
            counts.get("full_chain_requests", 0),
            counts.get("orb_positions_collected", 0),
            counts.get("orb_positions_evaluated", 0),
            counts.get("trendline_positions_collected", 0),
            counts.get("trendline_positions_evaluated", 0),
            counts.get("duplicate_same_tick_suppressed", 0),
            tel.monitor_cycle_duration_ms,
            tel.quote_request_latency_ms,
            tel.broker_quote_age_ms,
            tel.decision_runtime_ms,
            tel.scheduler_delay_ms,
            str(tel.missed_deadline).lower(),
            cycles_per_min,
            float(interval_p50_ms),
            float(interval_p95_ms),
            float(interval_max_ms),
            int(api_snap.get("broker_option_requests") or 0),
            int(api_snap.get("broker_equity_requests") or 0),
            int(api_snap.get("real_broker_total") or 0),
            ul_age_p50,
            ul_age_p95,
            ul_unavailable,
            int(po_tel.get("queue_depth") or 0),
            float(po_tel.get("oldest_unpersisted_age_ms") or 0.0),
            float(po_tel.get("last_successful_persist_age_ms") or 0.0),
            tel.extras.get("positions_with_bid"),
            tel.extras.get("positions_missing_bid"),
            tel.extras.get("eval_skip_class") or "",
            (
                f"{float(tel.extras.get('option_quote_age_ms_p95')):.1f}"
                if tel.extras.get("option_quote_age_ms_p95") is not None
                else "n/a"
            ),
        )
        if collected > 0 and evaluated == 0 and suppressed > 0:
            log.error(
                "UNIFIED_0DTE_MONITOR | evaluation_skipped_all | tick_id=%s | "
                "collected=%s | suppressed=%s | reason=duplicate_same_tick",
                tick_id,
                collected,
                suppressed,
            )
        try:
            persist_open_option_books(system)
        except Exception as persist_err:
            log.warning(
                "UNIFIED_0DTE_MONITOR | persist_open_books_error=%s | tick_id=%s",
                persist_err,
                tick_id,
            )
        return tel

    def watchdog_should_run(self, *, healthy_window_sec: float = 5.0) -> bool:
        """30s loop may evaluate only when the dedicated monitor task is not the owner.

        A live or just-dead ``_monitor_loop`` is woken / restarted. A second
        ``monitor_0dte_options_positions`` under a different tick id would
        advance velocity twice.
        """
        if self.loop_task_alive() or self._running:
            last = float(self._last_completed_monotonic or 0.0)
            if last > 0.0 and (time.monotonic() - last) > float(healthy_window_sec):
                self.request_wake(reason="watchdog_stale")
                log.warning(
                    "UNIFIED_0DTE_MONITOR | action=watchdog_wake | reason=stale_tick | "
                    "age_s=%.1f",
                    time.monotonic() - last,
                )
            return False
        task = self._loop_task
        if task is not None and task.done():
            log.warning("UNIFIED_0DTE_MONITOR | action=watchdog_restart_loop")
            self.ensure_background_tasks()
            self.request_wake(reason="watchdog_restart")
            return False
        if self._last_completed_monotonic <= 0:
            return True
        return (time.monotonic() - self._last_completed_monotonic) > float(healthy_window_sec)


_MONITOR: Optional[Unified0DTEPositionMonitor] = None


def get_unified_0dte_position_monitor(system: Any = None) -> Unified0DTEPositionMonitor:
    global _MONITOR
    if _MONITOR is None:
        _MONITOR = Unified0DTEPositionMonitor(system)
    elif system is not None:
        _MONITOR.bind_system(system)
    return _MONITOR
