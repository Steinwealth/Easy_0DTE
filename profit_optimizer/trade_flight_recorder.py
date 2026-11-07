#!/usr/bin/env python3
"""Session-scoped flight recorder and post-exit shadow tracker for executed ORB trades.

Records live + shadow premium paths for post-session path review (not an offline replay engine).
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from profit_optimizer.cohort_export import export_cohort_csv, export_cohort_csv_by_path
from profit_optimizer.path_classifier import path_class_for_strategy
from profit_optimizer.premium_tracker import PremiumTracker
from profit_optimizer.profit_optimizer_models import (
    STRATEGY_BREAKOUT_0DTE,
    STRATEGY_ORB_0DTE,
    STRATEGY_ORB_SO,
    STRATEGY_TRENDLINE_0DTE,
    DailySummary,
    ExitObservation,
    PremiumPathSample,
    TradeEntrySnapshot,
    TradeFlightRecord,
    UnderlyingPathSample,
    breakout_profit_optimizer_enabled,
    trendline_profit_optimizer_enabled,
)
from profit_optimizer.so_premium_shadow import so_premium_shadow_enabled
from profit_optimizer.profit_optimizer_storage import (
    checkpoint_interval_sec,
    load_daily_dataset_durable,
    persist_lag_warn_sec,
    jsonl_persist_min_write_gap_sec,
    persist_min_write_gap_sec,
    save_daily_dataset,
    sync_daily_to_gcs,
)
from profit_optimizer.time_utils import normalize_timestamp_iso
from profit_optimizer.audit import (
    classify_premium_source_bucket,
    parse_timestamp_utc,
    quote_quality_pct,
    validate_trade_record,
)
from profit_optimizer.path_analysis import compute_session_extension_metrics
from profit_optimizer.shadow_path import shadow_path_after_exit_enabled
from profit_optimizer.shadow_continuity import (
    apply_peak_trust_to_metadata,
    enrich_path_audit_export,
    evaluate_first_shadow_jump,
    first_shadow_jump_ratio_threshold,
    freeze_contract_identity,
    initial_path_audit_from_frozen,
    is_exact_shadow_quote_fields,
    last_live_premium_from_path,
    quarantine_shadow_path,
    resolve_last_live_for_shadow,
    shadow_sample_interval_sec,
)
from profit_optimizer.recording_window import (
    exit_is_recorded_loss,
    loss_exit_shadow_deadline_iso,
    max_shadow_continuations,
    recording_deadline_iso,
    recording_window_active,
    recording_window_seconds,
    should_shadow_after_exit,
)
from profit_optimizer.sampling_policy import SamplingPolicy, load_sampling_config

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _option_l1_imbalance(bid_size: Any, ask_size: Any) -> Optional[float]:
    """(bid size − ask size) / (bid size + ask size). Blank when either size is missing."""
    try:
        bid = float(bid_size)
        ask = float(ask_size)
    except (TypeError, ValueError):
        return None
    denom = bid + ask
    if denom <= 0:
        return None
    return (bid - ask) / denom


def _first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _resolve_contract_symbol(rec: Any, fields: Dict[str, Any]) -> str:
    """OCC on every live and shadow tick. Live ticks often omit the field."""
    entry = getattr(rec, "entry", None)
    audit = getattr(rec, "path_audit", None)
    audit = audit if isinstance(audit, dict) else {}
    identity = getattr(entry, "contract_identity", None)
    identity = identity if isinstance(identity, dict) else {}
    legs = identity.get("legs") if isinstance(identity.get("legs"), list) else []
    leg0 = legs[0] if legs and isinstance(legs[0], dict) else {}
    return _first_text(
        fields.get("contract_symbol"),
        fields.get("shadow_option_symbol"),
        getattr(entry, "option_symbol", ""),
        audit.get("entry_option_symbol"),
        audit.get("normalized_occ_symbol"),
        audit.get("broker_occ_symbol"),
        identity.get("broker_occ_symbol"),
        identity.get("option_symbol"),
        leg0.get("broker_occ_symbol"),
        leg0.get("option_symbol"),
    )


def _avg(vals: List[float]) -> Optional[float]:
    clean = [float(v) for v in vals if v is not None]
    if not clean:
        return None
    return sum(clean) / len(clean)


def _row_epoch(row: Dict[str, Any]) -> Optional[float]:
    if not isinstance(row, dict):
        return None
    raw = row.get("observation_timestamp") or row.get("timestamp")
    if not raw:
        return None
    dt = parse_timestamp_utc(str(raw))
    if dt is None:
        return None
    return float(dt.timestamp())


def _temporal_path_value(
    path: List[Dict[str, Any]],
    field: str,
    *,
    now_epoch: Optional[float],
    lookback_sec: float,
    max_staleness_sec: float,
) -> Optional[float]:
    if now_epoch is None:
        return None
    target = float(now_epoch) - float(lookback_sec)
    best_ts: Optional[float] = None
    best_val: Optional[float] = None
    for row in reversed(path or []):
        row_ts = _row_epoch(row)
        if row_ts is None or row_ts > target:
            continue
        if (target - row_ts) > float(max_staleness_sec):
            break
        val = _f(row.get(field))
        if val is None:
            continue
        best_ts = row_ts
        best_val = val
        break
    _ = best_ts
    return best_val


def _stamp_participation_trajectory(
    row: Dict[str, Any],
    *,
    prior_path: List[Dict[str, Any]],
    fields: Dict[str, Any],
) -> None:
    obs_ts = str(
        row.get("observation_timestamp")
        or row.get("timestamp")
        or fields.get("timestamp")
        or ""
    )
    now_dt = parse_timestamp_utc(obs_ts) if obs_ts else None
    now_epoch = float(now_dt.timestamp()) if now_dt is not None else None

    current_map = {
        "call_chain_volume": _f(fields.get("call_chain_volume")),
        "put_chain_volume": _f(fields.get("put_chain_volume")),
        "selected_contract_volume": _f(
            fields.get("selected_contract_volume")
            if fields.get("selected_contract_volume") is not None
            else fields.get("cumulative_volume")
        ),
        "bid_size": _f(fields.get("bid_size")),
        "ask_size": _f(fields.get("ask_size")),
    }
    for key, val in current_map.items():
        row[f"{key}_now"] = val
        row[f"{key}_30s_ago"] = _temporal_path_value(
            prior_path,
            f"{key}_now",
            now_epoch=now_epoch,
            lookback_sec=30.0,
            max_staleness_sec=45.0,
        )
        row[f"{key}_5m_ago"] = _temporal_path_value(
            prior_path,
            f"{key}_now",
            now_epoch=now_epoch,
            lookback_sec=300.0,
            max_staleness_sec=180.0,
        )
    side = str(fields.get("option_side") or fields.get("option_type") or "").strip().lower()
    selected_key = ""
    if "call" in side:
        selected_key = "selected_call_contract_volume"
    elif "put" in side:
        selected_key = "selected_put_contract_volume"
    if selected_key:
        for suffix in ("now", "30s_ago", "5m_ago"):
            val = row.get(f"selected_contract_volume_{suffix}")
            if val is not None:
                row[f"{selected_key}_{suffix}"] = val


class ThrottledProfitOptimizerLogger:
    def __init__(self, min_interval_sec: float = 30.0) -> None:
        self._min = max(0.0, float(min_interval_sec))
        self._last: Dict[str, float] = {}

    def allow(self, key: str) -> bool:
        if self._min <= 0:
            return True
        now = time.time()
        prev = self._last.get(key, 0.0)
        if now - prev >= self._min:
            self._last[key] = now
            return True
        return False


class TradeFlightRecorder:
    def __init__(
        self,
        *,
        session_date: str,
        log_min_interval_sec: float = 30.0,
        early_sample_sec: Optional[float] = None,
        early_window_sec: Optional[float] = None,
        late_sample_sec: Optional[float] = None,
    ) -> None:
        self.session_date = session_date
        es, ew, ls = load_sampling_config()
        self._early_sample_sec = float(early_sample_sec if early_sample_sec is not None else es)
        self._early_window_sec = float(early_window_sec if early_window_sec is not None else ew)
        self._late_sample_sec = float(late_sample_sec if late_sample_sec is not None else ls)
        self._log = ThrottledProfitOptimizerLogger(log_min_interval_sec)
        self._trackers: Dict[str, PremiumTracker] = {}
        self._policies: Dict[str, SamplingPolicy] = {}
        self._records: Dict[str, TradeFlightRecord] = {}
        self._session_quote_counts: Dict[str, int] = {
            "exact": 0,
            "nearest": 0,
            "cached": 0,
            "synthetic": 0,
        }
        self._session_shadow_quote_counts: Dict[str, int] = {
            "exact": 0,
            "nearest": 0,
            "cached": 0,
            "synthetic": 0,
        }
        self._last_checkpoint_mono: float = 0.0
        self._live_sample_logged: set[str] = set()
        self._shadow_sample_logged: set[str] = set()
        self._shadow_last_sample_mono: Dict[str, float] = {}
        self._pending_shadow_finalize: Set[str] = set()
        self._persist_lock = threading.RLock()
        self._gcs_lock = threading.Lock()
        self._gcs_pending = False
        self._gcs_prune_pending = False
        self._gcs_thread: Optional[threading.Thread] = None
        # Background local JSON persist (same coalescing worker as mock ledger / GCS).
        # Observe/evaluate never waits on save_daily_dataset or GCS.
        self._persist_thread_lock = threading.Lock()
        self._persist_thread: Optional[threading.Thread] = None
        self._persist_dirty = False
        self._persist_dirty_seq = 0
        self._persist_flushed_seq = 0
        self._persist_note = ""
        self._persist_dirty_since_mono = 0.0
        self._last_persist_success_mono = 0.0
        self._last_jsonl_success_mono = 0.0
        self._last_enqueue_ms = 0.0
        self._last_lag_warn_mono = 0.0
        self._hydrate_incomplete = False
        self._gcs_upload_daily_json = True

    def _should_sample_shadow(self, trade_id: str, now_mono: float) -> bool:
        # Unit tests construct the recorder with late_sample_sec=0 to disable throttling.
        if float(getattr(self, "_late_sample_sec", 30.0) or 0.0) <= 0.0:
            return True
        aligned = getattr(self, "_monitor_aligned_sample_sec", None)
        try:
            interval = float(aligned) if aligned is not None else shadow_sample_interval_sec()
        except (TypeError, ValueError):
            interval = shadow_sample_interval_sec()
        last = float(self._shadow_last_sample_mono.get(trade_id, 0.0))
        if last <= 0:
            return True
        return (now_mono - last) >= interval


    def trim_in_memory_paths(self, *, max_n: int | None = None) -> int:
        """Do not stride 2s research paths. Ceiling is record-time only."""
        del max_n
        return 0

    def hydrate_from_storage(self) -> int:
        """Reload trades from local disk + GCS checkpoints (scale-to-zero safe)."""
        data = load_daily_dataset_durable(self.session_date)
        if data.get("_hydrate_skipped_oversize"):
            self._hydrate_incomplete = True
            log.warning(
                "PROFIT_OPTIMIZER_STORAGE | action=recorder_hydrate_skipped | date=%s | "
                "bytes=%s | source=%s",
                self.session_date,
                data.get("_hydrate_skipped_bytes"),
                data.get("_hydrate_skipped_source"),
            )
            return 0
        try:
            from profit_optimizer.path_jsonl import download_session_jsonl_from_gcs
            from profit_optimizer.profit_optimizer_storage import _get_gcs_persistence_client

            gcs = _get_gcs_persistence_client()
            n_jsonl = download_session_jsonl_from_gcs(gcs, self.session_date)
            if n_jsonl:
                log.info(
                    "PROFIT_OPTIMIZER_STORAGE | action=jsonl_hydrated | date=%s | files=%d",
                    self.session_date,
                    n_jsonl,
                )
        except Exception as exc:
            log.debug("PROFIT_OPTIMIZER_STORAGE | jsonl_hydrate_skipped | error=%s", exc)
        from profit_optimizer.path_jsonl import attach_jsonl_paths

        trades = data.get("trades") if isinstance(data.get("trades"), list) else []
        restored = 0
        for trade in trades:
            if not isinstance(trade, dict):
                continue
            restored_trade = dict(trade)
            try:
                attach_jsonl_paths(self.session_date, restored_trade)
            except Exception:
                pass
            if self._restore_trade_from_dict(restored_trade):
                restored += 1
        self._hydrate_incomplete = False
        if restored:
            log.info(
                "PROFIT_OPTIMIZER_STORAGE | action=recorder_hydrated | date=%s | trades=%d",
                self.session_date,
                restored,
            )
        return restored

    def _restore_trade_from_dict(self, trade: Dict[str, Any]) -> bool:
        entry_d = trade.get("entry") if isinstance(trade.get("entry"), dict) else {}
        tid = str(entry_d.get("trade_id") or trade.get("trade_id") or "").strip()
        if not tid:
            return False
        existing = self._records.get(tid)
        if existing is not None:
            prem = list(trade.get("premium_path") or [])
            und = list(trade.get("underlying_path") or [])
            if len(prem) > len(list(existing.premium_path or [])):
                existing.premium_path = prem
            if len(und) > len(list(existing.underlying_path or [])):
                existing.underlying_path = und
            if tid not in self._trackers:
                self._ensure_restored_tracker(existing)
            return True
        entry_time = normalize_timestamp_iso(entry_d.get("entry_time"))
        entry = TradeEntrySnapshot(
            trade_id=tid,
            date=str(entry_d.get("date") or self.session_date),
            symbol=str(entry_d.get("symbol") or "").upper(),
            strategy_path=str(entry_d.get("strategy_path") or STRATEGY_ORB_0DTE),
            direction=str(entry_d.get("direction") or "LONG").upper(),
            entry_time=entry_time,
            underlying_entry_price=_f(entry_d.get("underlying_entry_price")),
            strike=_f(entry_d.get("strike")),
            expiration=str(entry_d.get("expiration") or ""),
            entry_bid=_f(entry_d.get("entry_bid")),
            entry_ask=_f(entry_d.get("entry_ask")),
            entry_mid=_f(entry_d.get("entry_mid")),
            entry_premium=_f(entry_d.get("entry_premium")),
            contracts=int(entry_d.get("contracts") or 0),
            position_size=_f(entry_d.get("position_size")),
            contract_identity=dict(entry_d.get("contract_identity") or {}),
            selector_metadata=dict(entry_d.get("selector_metadata") or {}),
            entry_execution_quality=dict(entry_d.get("entry_execution_quality") or {}),
            extra=dict(entry_d.get("extra") or {}),
        )
        entry.extra.setdefault("recording_deadline_at", recording_deadline_iso(entry.entry_time))
        entry.extra.setdefault("recording_window_seconds", recording_window_seconds())
        rec = TradeFlightRecord(entry=entry)
        rec.premium_path = list(trade.get("premium_path") or [])
        rec.underlying_path = list(trade.get("underlying_path") or [])
        rec.premium_metrics = dict(trade.get("premium_metrics") or {})
        rec.premium_metrics_at_exit = dict(trade.get("premium_metrics_at_exit") or {})
        rec.underlying_metrics = dict(trade.get("underlying_metrics") or {})
        rec.peak_quote_metadata = dict(trade.get("peak_quote_metadata") or {})
        rec.exit_observation = trade.get("exit_observation")
        rec.status = str(trade.get("status") or "open")
        rec.closed_at = str(trade.get("closed_at") or "")
        rec.shadow_tracking = bool(trade.get("shadow_tracking"))
        rec.path_audit = dict(trade.get("path_audit") or {})
        self._records[tid] = rec
        path = str(entry.strategy_path or "").strip().lower()
        if path in (STRATEGY_ORB_0DTE, STRATEGY_TRENDLINE_0DTE, STRATEGY_BREAKOUT_0DTE):
            tr = PremiumTracker(
                strategy_path=path,
                direction=entry.direction,
                entry_premium=float(entry.entry_premium or 0.0),
                entry_underlying=float(entry.underlying_entry_price or 0.0),
                entry_time=entry.entry_time,
            )
            tr.rehydrate_from_saved(rec.premium_path, rec.underlying_path)
            self._stamp_restored_entry_mono(tr, entry.entry_time)
            self._trackers[tid] = tr
            self._policies[tid] = SamplingPolicy(
                entry_time_iso=entry.entry_time,
                early_interval_sec=self._early_sample_sec,
                early_window_sec=self._early_window_sec,
                late_interval_sec=self._late_sample_sec,
            )
        return True

    @staticmethod
    def _stamp_restored_entry_mono(tracker: Any, entry_time: str) -> None:
        parsed = parse_timestamp_utc(entry_time)
        if parsed is None:
            tracker.set_entry_mono(time.monotonic())
            return
        age_s = max(0.0, time.time() - parsed.timestamp())
        tracker.set_entry_mono(time.monotonic() - age_s)

    def _ensure_restored_tracker(self, rec: Any) -> None:
        entry = getattr(rec, "entry", None)
        if entry is None:
            return
        tid = str(getattr(entry, "trade_id", "") or "").strip()
        path = str(getattr(entry, "strategy_path", "") or "").strip().lower()
        if not tid or path not in (
            STRATEGY_ORB_0DTE,
            STRATEGY_TRENDLINE_0DTE,
            STRATEGY_BREAKOUT_0DTE,
        ):
            return
        tr = PremiumTracker(
            strategy_path=path,
            direction=str(getattr(entry, "direction", "LONG") or "LONG"),
            entry_premium=float(getattr(entry, "entry_premium", 0.0) or 0.0),
            entry_underlying=float(getattr(entry, "underlying_entry_price", 0.0) or 0.0),
            entry_time=str(getattr(entry, "entry_time", "") or ""),
        )
        tr.rehydrate_from_saved(
            list(getattr(rec, "premium_path", None) or []),
            list(getattr(rec, "underlying_path", None) or []),
        )
        self._stamp_restored_entry_mono(tr, entry.entry_time)
        self._trackers[tid] = tr
        self._policies[tid] = SamplingPolicy(
            entry_time_iso=entry.entry_time,
            early_interval_sec=self._early_sample_sec,
            early_window_sec=self._early_window_sec,
            late_interval_sec=self._late_sample_sec,
        )

    def _checkpoint(self, note: str, *, force: bool = False) -> float:
        """Mark PO state dirty and enqueue background persist. Never writes disk/GCS.

        Returns enqueue wall-clock milliseconds. Owned-position evaluate must not
        wait on save_daily_dataset or GCS — those run on the persist worker
        (same coalescing pattern as mock_options_executor._schedule_persist).
        """
        t0 = time.monotonic()
        if self._persist_dirty_since_mono <= 0.0:
            self._persist_dirty_since_mono = time.monotonic()
        interval = checkpoint_interval_sec()
        now = time.time()
        if not force and interval > 0 and (now - self._last_checkpoint_mono) < interval:
            self._last_enqueue_ms = (time.monotonic() - t0) * 1000.0
            return self._last_enqueue_ms
        self._last_checkpoint_mono = now
        if not self._records:
            self._last_enqueue_ms = (time.monotonic() - t0) * 1000.0
            return self._last_enqueue_ms
        self._schedule_persist(note=note)
        self._last_enqueue_ms = (time.monotonic() - t0) * 1000.0
        return self._last_enqueue_ms

    def persist_telemetry(self) -> Dict[str, Any]:
        now = time.monotonic()
        dirty_since = float(self._persist_dirty_since_mono or 0.0)
        last_ok = float(self._last_persist_success_mono or 0.0)
        oldest = 0.0
        if dirty_since > 0.0:
            oldest = max(0.0, (now - dirty_since) * 1000.0)
        last_age = 0.0 if last_ok <= 0.0 else max(0.0, (now - last_ok) * 1000.0)
        pending = int(self._persist_dirty_seq) - int(self._persist_flushed_seq)
        thread = self._persist_thread
        depth = 1 if (self._persist_dirty or (thread is not None and thread.is_alive())) else 0
        return {
            "queue_depth": int(depth),
            "coalesced_pending": max(0, pending),
            "oldest_unpersisted_age_ms": float(oldest),
            "last_successful_persist_age_ms": float(last_age),
            "last_enqueue_ms": float(self._last_enqueue_ms or 0.0),
            "dirty": bool(self._persist_dirty),
        }

    def _schedule_persist(self, *, note: str = "") -> None:
        """Coalesce a dirty flag and ensure one persist worker is running."""
        self._persist_dirty = True
        self._persist_dirty_seq = int(self._persist_dirty_seq) + 1
        self._persist_note = str(note or self._persist_note or "checkpoint")
        if self._persist_dirty_since_mono <= 0.0:
            self._persist_dirty_since_mono = time.monotonic()
        lock = self._persist_thread_lock
        with lock:
            thread = self._persist_thread
            if thread is not None and thread.is_alive():
                return
            self._persist_thread = threading.Thread(
                target=self._persist_loop,
                name="po-persist",
                daemon=True,
            )
            self._persist_thread.start()

    def _persist_loop(self) -> None:
        """Single worker: export current in-memory records, local save, then GCS.

        Always exports the latest memory state (never a stale queued snapshot).
        If observe() dirties state during a flush, loop until clean.
        """
        while True:
            with self._persist_thread_lock:
                if not self._persist_dirty:
                    self._persist_thread = None
                    return
                self._persist_dirty = False
                note = str(self._persist_note or "checkpoint")
                seq_at_start = int(self._persist_dirty_seq)
                coalesced = max(1, seq_at_start - int(self._persist_flushed_seq))
                dirty_since = float(self._persist_dirty_since_mono or 0.0)
            force_notes = (
                "shutdown",
                "flush",
                "track_start",
                "track_start_resume",
                "exit_observed",
                "shadow_finalized",
                "shadow_finalized_batch",
                "so_premium_shadow_attach",
            )
            if str(note) not in force_notes:
                last_jsonl = float(self._last_jsonl_success_mono or 0.0)
                if last_jsonl > 0.0:
                    try:
                        jgap = float(jsonl_persist_min_write_gap_sec() or 0.0)
                    except Exception:
                        jgap = 5.0
                    if jgap > 0.0:
                        wait_s = jgap - (time.monotonic() - last_jsonl)
                        if wait_s > 0.05:
                            time.sleep(min(wait_s, jgap))
            queue_age_ms = 0.0
            if dirty_since > 0.0:
                queue_age_ms = max(0.0, (time.monotonic() - dirty_since) * 1000.0)
            t_all = time.monotonic()
            local_ms = 0.0
            gcs_ms = 0.0
            success = False
            dirty_trades = 0
            wrote_daily = False
            try:
                acquired = self._persist_lock.acquire(blocking=True, timeout=60.0)
                if not acquired:
                    log.warning(
                        "PO_PERSIST | trigger=%s | success=false | reason=persist_lock_timeout | "
                        "queue_age_ms=%.1f | will_retry=true",
                        note,
                        queue_age_ms,
                    )
                    self._persist_dirty = True
                    time.sleep(1.0)
                    continue
                try:
                    t_local = time.monotonic()
                    trades = self.export_trades()
                    dirty_trades = len(trades)
                    from profit_optimizer.path_jsonl import flush_trade_paths_jsonl

                    for trade in trades:
                        if isinstance(trade, dict):
                            flush_trade_paths_jsonl(self.session_date, trade)
                    self._last_jsonl_success_mono = time.monotonic()
                    last_daily = float(self._last_persist_success_mono or 0.0)
                    try:
                        dgap = float(persist_min_write_gap_sec() or 0.0)
                    except Exception:
                        dgap = 45.0
                    need_daily = str(note) in force_notes or last_daily <= 0.0
                    if not need_daily:
                        if dgap <= 0.0:
                            need_daily = True
                        else:
                            need_daily = (time.monotonic() - last_daily) >= dgap
                    if need_daily:
                        summary = self.build_summary().to_dict()
                        save_daily_dataset(
                            self.session_date,
                            trades,
                            summary,
                            run_note=note,
                            sync_gcs=False,
                            merge_existing=False,
                            include_trades_by_path=False,
                        )
                        wrote_daily = True
                    local_ms = (time.monotonic() - t_local) * 1000.0
                finally:
                    self._persist_lock.release()
                t_gcs = time.monotonic()
                if wrote_daily:
                    upload_daily = not bool(getattr(self, "_hydrate_incomplete", False))
                    self._gcs_upload_daily_json = upload_daily
                    self._schedule_gcs_sync(prune=False)
                    if str(note) in (
                        "shutdown",
                        "flush",
                        "exit_observed",
                        "shadow_finalized",
                        "shadow_finalized_batch",
                        "so_premium_shadow_attach",
                    ):
                        self.flush_gcs_sync(timeout_sec=30.0)
                    if not upload_daily:
                        log.warning(
                            "PO_PERSIST | trigger=%s | gcs_daily_json_skipped | "
                            "reason=hydrate_incomplete_oversize | jsonl_upload=true",
                            note,
                        )
                gcs_ms = (time.monotonic() - t_gcs) * 1000.0
                success = True
                self._persist_flushed_seq = seq_at_start
                if wrote_daily:
                    self._last_persist_success_mono = time.monotonic()
                if not self._persist_dirty:
                    self._persist_dirty_since_mono = 0.0
            except Exception as exc:
                self._persist_dirty = True
                log.warning(
                    "PO_PERSIST | trigger=%s | success=false | error=%s | queue_age_ms=%.1f | "
                    "will_retry=true",
                    note,
                    exc,
                    queue_age_ms,
                )
            total_ms = (time.monotonic() - t_all) * 1000.0
            pending_after = 1 if self._persist_dirty else 0
            log.info(
                "PO_PERSIST | trigger=%s | dirty_trades=%d | queue_age_ms=%.1f | "
                "local_save_ms=%.1f | gcs_upload_ms=%.1f | total_ms=%.1f | success=%s | "
                "coalesced_updates=%d | pending_after_flush=%d",
                note,
                dirty_trades,
                queue_age_ms,
                local_ms,
                gcs_ms,
                total_ms,
                str(bool(success)).lower(),
                coalesced,
                pending_after,
            )
            if success:
                try:
                    warn_sec = float(persist_lag_warn_sec() or 90.0)
                except Exception:
                    warn_sec = 90.0
                if queue_age_ms >= warn_sec * 1000.0:
                    now_m = time.monotonic()
                    if now_m - float(self._last_lag_warn_mono or 0.0) >= 30.0:
                        self._last_lag_warn_mono = now_m
                        log.warning(
                            "PO_PERSIST | lag_ms=%.1f | threshold_sec=%.0f | "
                            "dirty_trades=%d | coalesced_updates=%d",
                            queue_age_ms,
                            warn_sec,
                            dirty_trades,
                            coalesced,
                        )
            else:
                # Retry off the eval path. Never spin-loop on a failing GCS/disk write.
                time.sleep(1.0)

    def flush_persist_sync(self, timeout_sec: float = 60.0) -> None:
        """Wait for the background persist worker (shutdown / EOD). Does not run on eval."""
        if self._persist_dirty:
            self._schedule_persist(note="flush")
        thread = self._persist_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, float(timeout_sec)))

    def _schedule_gcs_sync(self, *, prune: bool = False) -> None:
        """Upload the local checkpoint off the trading loop. Prune only on EOD persist."""
        with self._gcs_lock:
            self._gcs_pending = True
            self._gcs_prune_pending = self._gcs_prune_pending or bool(prune)
            thread = self._gcs_thread
            if thread is not None and thread.is_alive():
                return
            self._gcs_thread = threading.Thread(
                target=self._gcs_sync_worker,
                name="po-gcs-sync",
                daemon=True,
            )
            self._gcs_thread.start()

    def _gcs_sync_worker(self) -> None:
        while True:
            with self._gcs_lock:
                if not self._gcs_pending:
                    self._gcs_thread = None
                    return
                prune = self._gcs_prune_pending
                self._gcs_pending = False
                self._gcs_prune_pending = False
            try:
                # Local JSON uses atomic replace — do not hold persist_lock
                # across the network upload (blocks the trading event loop).
                sync_daily_to_gcs(
                    self.session_date,
                    prune=prune,
                    upload_daily_json=bool(getattr(self, "_gcs_upload_daily_json", True)),
                )
            except Exception as exc:
                log.warning(
                    "PROFIT_OPTIMIZER_STORAGE | action=gcs_sync_background_error | error=%s",
                    exc,
                )

    def flush_gcs_sync(self, timeout_sec: float = 60.0) -> None:
        thread = self._gcs_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, float(timeout_sec)))

    def start_trade(self, entry: TradeEntrySnapshot) -> bool:
        from profit_optimizer.app_identity import stamp_entry_snapshot

        stamp_entry_snapshot(entry)
        entry.entry_time = normalize_timestamp_iso(entry.entry_time)
        entry.extra = dict(entry.extra or {})
        entry.extra["recording_deadline_at"] = recording_deadline_iso(entry.entry_time)
        entry.extra["recording_window_seconds"] = recording_window_seconds()
        path = str(entry.strategy_path or "").strip().lower()
        if path == STRATEGY_TRENDLINE_0DTE and not trendline_profit_optimizer_enabled():
            return False
        if path == STRATEGY_BREAKOUT_0DTE and not breakout_profit_optimizer_enabled():
            return False
        if path not in (
            STRATEGY_ORB_SO,
            STRATEGY_ORB_0DTE,
            STRATEGY_TRENDLINE_0DTE,
            STRATEGY_BREAKOUT_0DTE,
        ):
            return False
        tid = str(entry.trade_id or "").strip()
        if not tid:
            return False
        if tid in self._records:
            existing = self._records[tid]
            existing.entry = entry
            if existing.status in ("", "open"):
                existing.status = "open"
            log.info(
                "PROFIT_OPTIMIZER_TRACK_START | trade_id=%s | symbol=%s | strategy=%s | resumed=true",
                tid,
                entry.symbol,
                path,
            )
            self._checkpoint("track_start_resume", force=True)
            return True
        if path in (STRATEGY_ORB_0DTE, STRATEGY_TRENDLINE_0DTE, STRATEGY_BREAKOUT_0DTE):
            frozen = freeze_contract_identity(entry.contract_identity, entry=entry)
            entry.contract_identity = frozen
        elif path == STRATEGY_ORB_SO and so_premium_shadow_enabled(entry):
            frozen = freeze_contract_identity(entry.contract_identity, entry=entry)
            entry.contract_identity = frozen
        rec = TradeFlightRecord(entry=entry)
        rec.status = "open"
        if path in (STRATEGY_ORB_0DTE, STRATEGY_TRENDLINE_0DTE, STRATEGY_BREAKOUT_0DTE):
            rec.path_audit = initial_path_audit_from_frozen(entry.contract_identity)
        elif path == STRATEGY_ORB_SO and so_premium_shadow_enabled(entry):
            rec.path_audit = initial_path_audit_from_frozen(entry.contract_identity)
            rec.shadow_tracking = True
            pa = rec.path_audit if isinstance(rec.path_audit, dict) else {}
            pa["so_premium_shadow"] = True
            pa["recording_mode"] = "0dte_premium_shadow"
            pa["last_live_premium"] = float(entry.entry_premium or 0.0)
            pa["so_premium_shadow_skip_jump_check"] = True
            rec.path_audit = pa
        self._records[tid] = rec
        entry_prem = float(entry.entry_premium or 0.0)
        entry_und = float(entry.underlying_entry_price or 0.0)
        policy = SamplingPolicy(
            entry_time_iso=entry.entry_time,
            early_interval_sec=self._early_sample_sec,
            early_window_sec=self._early_window_sec,
            late_interval_sec=self._late_sample_sec,
        )
        self._policies[tid] = policy
        entry_mono = time.time()
        if path in (STRATEGY_ORB_0DTE, STRATEGY_TRENDLINE_0DTE, STRATEGY_BREAKOUT_0DTE):
            tr = PremiumTracker(
                strategy_path=path,
                direction=entry.direction,
                entry_premium=entry_prem,
                entry_underlying=entry_und,
                entry_time=entry.entry_time,
            )
            tr.set_entry_mono(entry_mono)
            self._trackers[tid] = tr
        elif path == STRATEGY_ORB_SO and so_premium_shadow_enabled(entry):
            tr = PremiumTracker(
                strategy_path=path,
                direction=entry.direction,
                entry_premium=float(entry.entry_premium or 0.0),
                entry_underlying=entry_und,
                entry_time=entry.entry_time,
            )
            tr.set_entry_mono(entry_mono)
            self._trackers[tid] = tr
        else:
            tr = PremiumTracker(
                strategy_path=path,
                direction=entry.direction,
                entry_premium=0.0,
                entry_underlying=entry_und,
                entry_time=entry.entry_time,
            )
            tr.set_entry_mono(entry_mono)
            self._trackers[tid] = tr
        log.info(
            "PROFIT_OPTIMIZER_TRACK_START | trade_id=%s | symbol=%s | strategy=%s | path_class=%s | direction=%s | "
            "option_symbol=%s | leg_count=%s | entry_premium=%s | premium_shadow=%s",
            tid,
            entry.symbol,
            path,
            path_class_for_strategy(path),
            entry.direction,
            (entry.contract_identity or {}).get("option_symbol", ""),
            (entry.contract_identity or {}).get("leg_count", ""),
            entry.entry_premium,
            str(so_premium_shadow_enabled(entry)).lower(),
        )
        self._checkpoint("track_start", force=True)
        return True

    def _stamped_recording_deadline(self, rec: TradeFlightRecord) -> Optional[str]:
        extra = rec.entry.extra if rec.entry is not None and isinstance(rec.entry.extra, dict) else {}
        raw = extra.get("recording_deadline_at")
        text = str(raw or "").strip()
        return text or None

    def _recording_active_for(self, rec: TradeFlightRecord, now: Optional[Any] = None) -> bool:
        return recording_window_active(
            rec.entry.entry_time,
            now=now,
            deadline_at=self._stamped_recording_deadline(rec),
        )

    def _shadow_window_stop_reason(self, rec: TradeFlightRecord) -> str:
        extra = rec.entry.extra if rec.entry is not None and isinstance(rec.entry.extra, dict) else {}
        if extra.get("loss_shadow_window"):
            return "loss_shadow_elapsed"
        pa = rec.path_audit if isinstance(rec.path_audit, dict) else {}
        if pa.get("shadow_path_trusted") is False:
            return "shadow_quarantined"
        if extra.get("emit_shadow"):
            return "emit_shadow_skipped"
        # Winner leftover runs to session EOD flatten (12:50 PT), not a 3h entry window.
        return "eod"

    def _shadow_continuation_rank(self, rec: TradeFlightRecord) -> Tuple[float, float]:
        """Highest leftover-research value first: exit MFE, then realized dollars."""
        mfe = None
        for bag in (rec.premium_metrics_at_exit, rec.premium_metrics):
            if isinstance(bag, dict) and bag.get("premium_mfe_pct") is not None:
                try:
                    mfe = float(bag.get("premium_mfe_pct"))
                    break
                except (TypeError, ValueError):
                    mfe = None
        pnl = None
        xo = rec.exit_observation if isinstance(rec.exit_observation, dict) else {}
        extra = xo.get("extra") if isinstance(xo.get("extra"), dict) else {}
        for src in (xo, extra):
            if not isinstance(src, dict):
                continue
            raw = src.get("realized_pnl")
            if raw is None:
                raw = src.get("realized_pnl_pct")
            if raw is None:
                continue
            try:
                pnl = float(raw)
                break
            except (TypeError, ValueError):
                continue
        return (float(mfe or 0.0), float(pnl or 0.0))

    def has_pending_shadow_work(self) -> bool:
        return bool(self._pending_shadow_finalize)

    def _queue_shadow_finalize(
        self,
        tid: str,
        rec: TradeFlightRecord,
        stop_reason: str,
    ) -> None:
        """Stop quoting immediately. Metrics + one GCS write happen in finalize_expired_shadows."""
        pa = rec.path_audit if isinstance(rec.path_audit, dict) else {}
        pa["shadow_stop_reason"] = str(stop_reason or "eod")
        rec.path_audit = pa
        rec.shadow_tracking = False
        rec.status = "closed"
        if not rec.closed_at:
            rec.closed_at = _now_iso()
        self._pending_shadow_finalize.add(str(tid))

    def finalize_expired_shadows(self) -> int:
        """Compute session metrics for queued shadows and persist once."""
        with self._persist_lock:
            tids = sorted(self._pending_shadow_finalize)
            self._pending_shadow_finalize.clear()
            n = 0
            for tid in tids:
                rec = self._records.get(tid)
                if rec is None:
                    continue
                tracker = self._trackers.get(tid)
                stop_reason = str(
                    (rec.path_audit or {}).get("shadow_stop_reason")
                    or self._shadow_window_stop_reason(rec)
                )
                if tracker is not None:
                    self._finalize_single_shadow_trade(
                        tid,
                        rec,
                        tracker,
                        stop_reason=stop_reason,
                        checkpoint=False,
                        queued=True,
                    )
                n += 1
            if n:
                self._checkpoint("shadow_finalized_batch", force=True)
        return n

    def list_shadow_trades(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for tid in sorted(self._records.keys()):
            rec = self._records[tid]
            if rec.shadow_tracking or (
                str(rec.entry.strategy_path) == STRATEGY_ORB_SO and so_premium_shadow_enabled(rec.entry)
            ):
                out.append(rec.to_dict())
        return out

    def list_unified_monitor_continuations(self, *, now: Optional[Any] = None) -> List[Dict[str, Any]]:
        """Return post-exit contracts that still require exact quotes."""
        ranked: List[Tuple[Tuple[float, float], Dict[str, Any]]] = []
        for tid in sorted(self._records.keys()):
            rec = self._records[tid]
            if not rec.shadow_tracking and rec.status != "shadow":
                continue
            extra = rec.entry.extra if rec.entry is not None and isinstance(rec.entry.extra, dict) else {}
            pa = rec.path_audit if isinstance(rec.path_audit, dict) else {}
            if extra.get("emit_shadow"):
                self._queue_shadow_finalize(tid, rec, "emit_shadow_skipped")
                continue
            if pa.get("shadow_path_trusted") is False:
                self._queue_shadow_finalize(tid, rec, "shadow_quarantined")
                continue
            stop_reason = self._shadow_window_stop_reason(rec)
            if not self._recording_active_for(rec, now):
                self._queue_shadow_finalize(tid, rec, stop_reason)
                continue

            contract = (
                rec.entry.contract_identity
                if isinstance(rec.entry.contract_identity, dict)
                else {}
            )
            legs_in = contract.get("legs") if isinstance(contract.get("legs"), list) else []
            legs: List[Dict[str, Any]] = []
            for leg in legs_in:
                if not isinstance(leg, dict):
                    continue
                option_symbol = str(
                    leg.get("broker_occ_symbol")
                    or leg.get("normalized_occ_symbol")
                    or leg.get("broker_quote_request_symbol")
                    or leg.get("option_symbol")
                    or leg.get("contract_id")
                    or ""
                ).strip()
                if not option_symbol:
                    continue
                legs.append(
                    {
                        "option_symbol": option_symbol,
                        "broker_occ_symbol": str(leg.get("broker_occ_symbol") or option_symbol),
                        "normalized_occ_symbol": str(
                            leg.get("normalized_occ_symbol") or option_symbol
                        ),
                        "broker_quote_request_symbol": str(
                            leg.get("broker_quote_request_symbol") or ""
                        ),
                        "osi_symbol": str(leg.get("osi_symbol") or ""),
                        "display_label": str(leg.get("display_label") or ""),
                        "long_or_short": str(leg.get("long_or_short") or "long").lower(),
                        "strike": leg.get("strike"),
                        "expiration": leg.get("expiration"),
                        "option_type": leg.get("option_type") or leg.get("side"),
                    }
                )
            if not legs:
                option_symbol = str(
                    contract.get("broker_occ_symbol")
                    or contract.get("normalized_occ_symbol")
                    or contract.get("broker_quote_request_symbol")
                    or contract.get("option_symbol")
                    or contract.get("contract_id")
                    or ""
                ).strip()
                if option_symbol:
                    legs.append(
                        {
                            "option_symbol": option_symbol,
                            "broker_occ_symbol": str(
                                contract.get("broker_occ_symbol") or option_symbol
                            ),
                            "normalized_occ_symbol": str(
                                contract.get("normalized_occ_symbol") or option_symbol
                            ),
                            "broker_quote_request_symbol": str(
                                contract.get("broker_quote_request_symbol") or ""
                            ),
                            "osi_symbol": str(contract.get("osi_symbol") or ""),
                            "display_label": str(contract.get("display_label") or ""),
                            "long_or_short": "long",
                            "strike": contract.get("strike"),
                            "expiration": contract.get("expiration"),
                            "option_type": contract.get("option_type")
                            or contract.get("option_side"),
                        }
                    )
            if not legs:
                log.warning(
                    "PROFIT_OPTIMIZER_CONTINUATION | trade_id=%s | action=finalize | "
                    "reason=missing_exact_contract_identity",
                    tid,
                )
                self._queue_shadow_finalize(tid, rec, "missing_exact_contract_identity")
                continue
            ranked.append(
                (
                    self._shadow_continuation_rank(rec),
                    {
                        "trade_id": tid,
                        "symbol": str(rec.entry.symbol or "").upper(),
                        "entry_time": rec.entry.entry_time,
                        "recording_deadline_at": rec.entry.extra.get("recording_deadline_at")
                        or recording_deadline_iso(rec.entry.entry_time),
                        "legs": legs,
                        "option_symbols": [leg["option_symbol"] for leg in legs],
                    },
                )
            )
        ranked.sort(key=lambda row: row[0], reverse=True)
        cap = max_shadow_continuations()
        if cap > 0 and len(ranked) > cap:
            dropped = len(ranked) - int(cap)
            if dropped > 0 and self._log.allow("shadow_continuation_cap"):
                log.warning(
                    "PROFIT_OPTIMIZER_CONTINUATION | action=cap | "
                    "cap=%d | active=%d | dropped=%d | "
                    "note=lower_mfe_winners_get_zero_shadow_until_cap_frees",
                    int(cap),
                    len(ranked),
                    dropped,
                )
            ranked = ranked[:cap]
        return [row for _rank, row in ranked]

    def rewrite_contract_option_symbols(
        self,
        trade_id: str,
        symbol_map: Dict[str, str],
    ) -> bool:
        """Rewrite frozen shadow contract identity option symbols (listed expiry repair)."""
        tid = str(trade_id or "").strip()
        rec = self._records.get(tid)
        if not rec or not symbol_map:
            return False
        contract = (
            rec.entry.contract_identity
            if isinstance(rec.entry.contract_identity, dict)
            else None
        )
        if not isinstance(contract, dict):
            return False
        changed = False
        legs = contract.get("legs") if isinstance(contract.get("legs"), list) else []
        for leg in legs:
            if not isinstance(leg, dict):
                continue
            old = str(leg.get("option_symbol") or leg.get("contract_id") or "").strip()
            new = str(symbol_map.get(old) or "").strip()
            if not old or not new or new == old:
                continue
            leg["option_symbol"] = new
            if "contract_id" in leg:
                leg["contract_id"] = new
            changed = True
        for key in ("option_symbol", "contract_id"):
            old = str(contract.get(key) or "").strip()
            new = str(symbol_map.get(old) or "").strip()
            if old and new and new != old:
                contract[key] = new
                changed = True
        if changed:
            rec.entry.contract_identity = contract
            pa = rec.path_audit if isinstance(rec.path_audit, dict) else {}
            pa["occ_expiry_repaired"] = True
            pa["occ_expiry_symbol_map"] = {
                str(k): str(v) for k, v in list(symbol_map.items())[:8]
            }
            rec.path_audit = pa
            self._checkpoint("occ_expiry_repair", force=False)
        return changed

    def last_reusable_exact_quote(
        self,
        trade_id: str,
        *,
        max_age_sec: float = 8.0,
        now_ts: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """Last trusted exact-contract premium sample usable when broker age rejects.

        Keeps post-exit shadow on Market Quote economics instead of flickering to
        chain bridge (AMD 2026-07-23 shadow was ~25% exact / ~75% fresh_chain).
        """
        tid = str(trade_id or "").strip()
        rec = self._records.get(tid)
        if not rec:
            return None
        now = float(now_ts if now_ts is not None else time.time())
        max_age = max(0.5, float(max_age_sec or 8.0))
        for row in reversed(list(rec.premium_path or [])):
            if not isinstance(row, dict):
                continue
            if str(row.get("sample_type") or "") == "gap":
                continue
            src = str(row.get("premium_source") or row.get("quote_source") or "").strip().lower()
            trust = str(row.get("shadow_trust") or "").strip().lower()
            if "exact_contract" not in src and src not in ("exact", "exit_grade") and trust != "trusted_exact":
                continue
            if "fresh_chain" in src or src.startswith("chain_strike"):
                continue
            mark = row.get("mark") if row.get("mark") is not None else row.get("bid")
            try:
                mark_f = float(mark) if mark is not None else None
            except (TypeError, ValueError):
                mark_f = None
            if mark_f is None or mark_f <= 0:
                continue
            age_sec: Optional[float] = None
            raw_ts = row.get("timestamp")
            try:
                if raw_ts:
                    from datetime import datetime

                    age_sec = max(
                        0.0,
                        now
                        - datetime.fromisoformat(str(raw_ts).replace("Z", "+00:00")).timestamp(),
                    )
            except Exception:
                age_sec = None
            if age_sec is None:
                try:
                    age_sec = float(row.get("quote_age_seconds"))
                except (TypeError, ValueError):
                    age_sec = None
            if age_sec is not None and age_sec > max_age:
                continue
            bid = row.get("bid") if row.get("bid") is not None else mark_f
            ask = row.get("ask")
            return {
                "bid": bid,
                "ask": ask,
                "mark": mark_f,
                "mid": row.get("mid") or mark_f,
                "source": "exact_quote",
                "quote_source": "exact_quote",
                "premium_source": "exact_quote",
                "quote_age_seconds": float(age_sec if age_sec is not None else 0.01),
                "freshness_status": "reused_last_exact",
                "contract_identity_matched": True,
                "contract_matched": True,
                "shadow_trust": "trusted_exact",
                "reused_last_exact": True,
                "option_symbol": row.get("shadow_option_symbol") or row.get("option_symbol"),
            }
        return None

    def note_shadow_quote_miss(
        self,
        trade_id: str,
        *,
        reason: str = "",
        requested_occ: str = "",
        requested_aliases: Optional[List[str]] = None,
        last_known_underlying: Optional[float] = None,
        exact_failure_reason: str = "",
        chain_failure_reason: str = "",
        broker_response_status: str = "",
    ) -> None:
        tid = str(trade_id or "").strip()
        rec = self._records.get(tid)
        if not rec or not rec.shadow_tracking:
            return
        from modules.monitor_premium_snapshot import open_or_extend_gap_interval

        pa = rec.path_audit if isinstance(rec.path_audit, dict) else {}
        last_valid = ""
        for row in reversed(list(rec.premium_path or [])):
            if not isinstance(row, dict):
                continue
            if str(row.get("sample_type") or "") == "gap":
                continue
            if row.get("mark") is not None:
                last_valid = str(row.get("timestamp") or "")
                break
        pa = open_or_extend_gap_interval(
            pa,
            trade_id=tid,
            reason=reason,
            requested_occ=requested_occ
            or str(pa.get("broker_occ_symbol") or pa.get("entry_option_symbol") or ""),
            requested_aliases=requested_aliases,
            last_known_underlying=last_known_underlying,
            exact_failure_reason=exact_failure_reason or reason,
            chain_failure_reason=chain_failure_reason,
            broker_response_status=broker_response_status or reason,
            last_valid_quote_timestamp=last_valid,
        )
        # Keep a compact rolling diagnostic list (not per-tick path spam).
        gaps = pa.get("shadow_gaps") if isinstance(pa.get("shadow_gaps"), list) else []
        intervals = pa.get("shadow_gap_intervals") if isinstance(pa.get("shadow_gap_intervals"), list) else []
        if intervals:
            latest = intervals[-1]
            gaps = [
                {
                    "timestamp": latest.get("timestamp_end") or latest.get("timestamp_start"),
                    "path_phase": "shadow",
                    "sample_type": "gap_interval",
                    "gap_reason": str(latest.get("exact_failure_reason") or reason or "no_shadow_quote")[:120],
                    "attempt_count": latest.get("attempt_count"),
                    "timestamp_start": latest.get("timestamp_start"),
                    "timestamp_end": latest.get("timestamp_end"),
                }
            ]
        pa["shadow_gaps"] = gaps[-20:]
        rec.path_audit = pa
        log.info(
            "PROFIT_OPTIMIZER_SHADOW_GAP | trade_id=%s | reason=%s | gap_count=%s | attempts=%s",
            tid,
            reason or "no_quote",
            pa.get("shadow_gap_count"),
            (intervals[-1].get("attempt_count") if intervals else 0),
        )
        self._checkpoint("shadow_gap", force=False)

    def _stamp_compact_path_state(
        self,
        row: Dict[str, Any],
        *,
        tracker: PremiumTracker,
        rec: TradeFlightRecord,
        fields: Dict[str, Any],
        path_phase: str,
        trade_id: str,
    ) -> None:
        """Stamp shared live/shadow exit-research fields onto one premium_path row."""
        entry = float(getattr(tracker, "entry_premium", 0.0) or 0.0)
        hwm = float(getattr(tracker, "_hwm_premium", 0.0) or 0.0)
        hwm_ts = str(getattr(tracker, "_peak_ts", "") or "") or None
        mark = _f(row.get("mark"))
        obs_ts = str(
            row.get("observation_timestamp")
            or row.get("timestamp")
            or fields.get("timestamp")
            or ""
        )
        row["trade_id"] = str(fields.get("trade_id") or trade_id or "")
        if not row.get("contract_symbol"):
            row["contract_symbol"] = _resolve_contract_symbol(rec, fields)
        l1 = _option_l1_imbalance(row.get("bid_size"), row.get("ask_size"))
        if l1 is None:
            l1 = _option_l1_imbalance(fields.get("bid_size"), fields.get("ask_size"))
        if l1 is not None:
            row["option_l1_imbalance"] = l1
        row["observation_timestamp"] = obs_ts or None
        if fields.get("broker_quote_timestamp"):
            row["broker_quote_timestamp"] = str(fields.get("broker_quote_timestamp"))
        if fields.get("last") is not None:
            row["last"] = _f(fields.get("last"))
        if fields.get("bid_size") is not None:
            row["bid_size"] = _f(fields.get("bid_size"))
        if fields.get("ask_size") is not None:
            row["ask_size"] = _f(fields.get("ask_size"))
        cum = _f(fields.get("cumulative_volume"))
        if cum is None:
            cum = _f(fields.get("volume"))
        row["cumulative_volume"] = cum
        if cum is not None:
            row["volume"] = cum
        if fields.get("monitor_tick_id"):
            row["monitor_tick_id"] = str(fields.get("monitor_tick_id"))
        if fields.get("market_snapshot_id") or fields.get("monitor_tick_id"):
            row["market_snapshot_id"] = str(
                fields.get("market_snapshot_id") or fields.get("monitor_tick_id") or ""
            )
        q_age_ms = _f(fields.get("quote_age_ms"))
        if q_age_ms is None:
            q_age_sec = _f(fields.get("quote_age_seconds") or row.get("quote_age_seconds"))
            if q_age_sec is not None:
                q_age_ms = float(q_age_sec) * 1000.0
        if q_age_ms is not None:
            row["quote_age_ms"] = q_age_ms
        if fields.get("evaluated") is not None:
            row["evaluated"] = bool(fields.get("evaluated"))
        else:
            row["evaluated"] = True
        if fields.get("eval_now"):
            row["eval_now"] = str(fields.get("eval_now"))
        if fields.get("eval_age_s") is not None:
            row["eval_age_s"] = _f(fields.get("eval_age_s"))
        if fields.get("exit_reason"):
            row["exit_reason"] = str(fields.get("exit_reason"))
        if fields.get("defer_reason") or row.get("evaluated") is False:
            row["defer_reason"] = str(fields.get("defer_reason") or "")
        if fields.get("underlying_source"):
            row["underlying_source"] = str(fields.get("underlying_source"))
        if fields.get("underlying_last") is not None:
            row["underlying_last"] = _f(fields.get("underlying_last"))
        if fields.get("underlying_age_ms") is not None:
            row["underlying_age_ms"] = _f(fields.get("underlying_age_ms"))
        if fields.get("underlying_observation_timestamp"):
            row["underlying_observation_timestamp"] = str(fields.get("underlying_observation_timestamp"))
        if fields.get("underlying_reused") is not None:
            row["underlying_reused"] = bool(fields.get("underlying_reused"))
        if fields.get("underlying_stale") is not None:
            row["underlying_stale"] = bool(fields.get("underlying_stale"))
        if fields.get("last_successful_eval_ts") is not None:
            row["last_successful_eval_ts"] = fields.get("last_successful_eval_ts")
        if fields.get("seconds_since_last_successful_eval") is not None:
            row["seconds_since_last_successful_eval"] = _f(
                fields.get("seconds_since_last_successful_eval")
            )
        row["reused_last_exact"] = bool(fields.get("reused_last_exact"))
        row["path_phase"] = path_phase
        if path_phase == "shadow":
            pa = rec.path_audit if isinstance(rec.path_audit, dict) else {}
            shadow_symbol = (
                fields.get("shadow_option_symbol")
                or pa.get("shadow_option_symbol")
                or pa.get("entry_option_symbol")
                or pa.get("broker_occ_symbol")
                or row.get("contract_symbol")
            )
            if shadow_symbol:
                row["shadow_option_symbol"] = str(shadow_symbol)
        row["entry_premium"] = entry if entry > 0 else None
        row["hwm_premium"] = hwm if hwm > 0 else None
        row["hwm_timestamp"] = hwm_ts
        row["strike"] = _f(getattr(rec.entry, "strike", None))
        row["direction"] = str(getattr(rec.entry, "direction", "") or "")
        option_side = ""
        try:
            ci = getattr(rec.entry, "contract_identity", None)
            if isinstance(ci, dict):
                option_side = str(ci.get("option_side") or ci.get("option_type") or "")
                if row.get("strike") is None:
                    row["strike"] = _f(ci.get("strike"))
        except Exception:
            option_side = ""
        if not option_side:
            option_side = str(fields.get("option_side") or fields.get("option_type") or "")
        row["option_side"] = option_side
        # Equity spot densify: live und + frozen entry und for asset-stop research.
        try:
            und_live = _f(row.get("underlying_price") if row.get("underlying_price") is not None else fields.get("underlying_price"))
            if und_live is None or und_live <= 0:
                und_live = _f(fields.get("underlying_last"))
            entry_und = _f(getattr(rec.entry, "underlying_entry_price", None))
            if und_live is not None and und_live > 0:
                row["underlying_price"] = und_live
                row["underlying_price_missing"] = False
            else:
                row["underlying_price_missing"] = True
            if entry_und is not None and entry_und > 0:
                row["entry_underlying"] = entry_und
                row["entry_underlying_price"] = entry_und
                if und_live is not None and und_live > 0:
                    raw = (und_live - entry_und) / entry_und
                    row["underlying_vs_entry_pct"] = raw
                    side = str(option_side or "")
                    if "put" in side.lower():
                        row["underlying_move_pct"] = -raw
                    else:
                        row["underlying_move_pct"] = raw
        except Exception:
            row.setdefault("underlying_price_missing", True)
        mfe_pct = ((hwm - entry) / entry) if entry > 0 and hwm > 0 else None
        row["mfe_pct"] = mfe_pct
        # Live mark vs entry — path consumers expect pnl_pct (not only mfe_pct).
        if entry > 0 and mark is not None:
            try:
                row["pnl_pct"] = (float(mark) - entry) / entry
            except (TypeError, ValueError):
                row["pnl_pct"] = None
        else:
            row["pnl_pct"] = None
        row["drawdown_from_hwm_pct"] = ((hwm - mark) / hwm) if hwm > 0 and mark is not None else None
        trigger = 0.10
        exit_dd = 0.10
        try:
            from modules.config_loader import get_config_value

            trigger = float(
                get_config_value("OPTION_SINGLE_MFE_TRAIL_TRIGGER", 0.10) or 0.10
            )
            exit_dd = float(
                get_config_value("OPTION_SINGLE_MFE_TRAIL_EXIT", 0.10) or 0.10
            )
        except Exception:
            pass
        armed = bool(mfe_pct is not None and float(mfe_pct) + 1e-12 >= float(trigger))
        row["mfe_giveback_armed"] = armed
        row["giveback_floor_premium"] = (hwm * (1.0 - float(exit_dd))) if armed and hwm > 0 else None
        try:
            from profit_optimizer.audit import seconds_between_timestamps

            entry_ts = str(getattr(tracker, "entry_time", "") or getattr(rec.entry, "entry_time", "") or "")
            row["seconds_since_entry"] = (
                seconds_between_timestamps(entry_ts, obs_ts) if entry_ts and obs_ts else None
            )
            row["seconds_since_hwm"] = (
                seconds_between_timestamps(hwm_ts, obs_ts) if hwm_ts and obs_ts else None
            )
        except Exception:
            row["seconds_since_entry"] = None
            row["seconds_since_hwm"] = None

        xo = rec.exit_observation if isinstance(rec.exit_observation, dict) else {}
        if path_phase == "shadow" and xo:
            row["actual_exit_timestamp"] = str(xo.get("exit_time") or "") or None
            row["actual_exit_premium"] = _f(xo.get("exit_premium"))
            row["actual_exit_bid"] = _f(xo.get("exit_bid") or xo.get("exit_premium"))
            row["actual_exit_reason"] = str(xo.get("exit_reason") or "")
            try:
                from profit_optimizer.audit import seconds_between_timestamps

                row["seconds_since_actual_exit"] = (
                    seconds_between_timestamps(str(xo.get("exit_time") or ""), obs_ts)
                    if xo.get("exit_time") and obs_ts
                    else None
                )
            except Exception:
                row["seconds_since_actual_exit"] = None
            pa = rec.path_audit if isinstance(rec.path_audit, dict) else {}
            sh = _f(pa.get("shadow_hwm_since_exit"))
            if hwm > 0 and (sh is None or hwm > float(sh)):
                pa["shadow_hwm_since_exit"] = hwm
                rec.path_audit = pa
            row["shadow_hwm_since_exit"] = _f(pa.get("shadow_hwm_since_exit"))
            row["live_hwm_at_exit"] = _f(pa.get("live_hwm_at_exit"))
        else:
            row["actual_exit_timestamp"] = None
            row["actual_exit_bid"] = None
            row["actual_exit_premium"] = None
            row["actual_exit_reason"] = ""
            row["seconds_since_actual_exit"] = None
        _keep_observed_path_fields(row, fields)

    def observe_premium_tick(self, trade_id: str, fields: Dict[str, Any]) -> None:
        tid = str(trade_id or "").strip()
        tracker = self._trackers.get(tid)
        rec = self._records.get(tid)
        policy = self._policies.get(tid)
        if not tracker or not rec or not policy:
            return
        if rec.status not in ("open", "shadow"):
            return
        try:
            from profit_optimizer.source_rings import (
                attach_causal_chain_volume,
                note_underlying_sample,
                underlying_asof,
            )

            attach_causal_chain_volume(fields, symbol=str(rec.entry.symbol or ""))
            if fields.get("underlying_price") not in (None, ""):
                note_underlying_sample(
                    rec.entry.symbol,
                    fields.get("timestamp"),
                    fields.get("underlying_price"),
                )
            elif fields.get("underlying_price") in (None, ""):
                shared = underlying_asof(rec.entry.symbol, fields.get("timestamp"))
                if shared is not None:
                    fields["underlying_price"] = shared
        except Exception:
            pass
        observed_at = fields.get("timestamp")
        if observed_at and not self._recording_active_for(rec, observed_at):
            stop_reason = self._shadow_window_stop_reason(rec)
            pa = rec.path_audit if isinstance(rec.path_audit, dict) else {}
            pa["recording_stop_reason"] = stop_reason
            pa["recording_completed_at"] = normalize_timestamp_iso(observed_at)
            rec.path_audit = pa
            if rec.status == "shadow":
                self._queue_shadow_finalize(tid, rec, stop_reason)
            return
        if str(rec.entry.strategy_path) == STRATEGY_ORB_SO and not so_premium_shadow_enabled(rec.entry):
            return
        now_mono = time.time()
        path_phase = "shadow" if rec.status == "shadow" or (
            str(rec.entry.strategy_path) == STRATEGY_ORB_SO and so_premium_shadow_enabled(rec.entry)
        ) else "live"
        evaluated_flag = fields.get("evaluated")
        freeze_latches = evaluated_flag is False or str(evaluated_flag).strip().lower() in ("false", "0")
        evaluated_true = evaluated_flag is True or str(evaluated_flag).strip().lower() in ("true", "1")
        # Every PATH_V2 evaluation tick is recorded (not the 2s sample throttle).
        force_sample = freeze_latches or bool(fields.get("force_path_sample")) or evaluated_true
        if path_phase == "shadow":
            if not force_sample and not self._should_sample_shadow(tid, now_mono):
                return
        elif not force_sample and not tracker.should_sample(policy, now_mono):
            return
        pa = rec.path_audit if isinstance(rec.path_audit, dict) else {}
        if path_phase == "shadow":
            if pa.get("shadow_path_trusted") is False:
                self.note_shadow_quote_miss(
                    tid, reason=str(pa.get("shadow_quarantine_reason") or "shadow_quarantined")
                )
                self._queue_shadow_finalize(tid, rec, "shadow_quarantined")
                return
            if not is_exact_shadow_quote_fields(fields):
                self.note_shadow_quote_miss(
                    tid,
                    reason="non_recordable_shadow_quote",
                    exact_failure_reason=str(fields.get("exact_failure_reason") or ""),
                    chain_failure_reason=str(fields.get("chain_failure_reason") or ""),
                    broker_response_status=str(
                        fields.get("shadow_trust") or fields.get("premium_source") or "invalid"
                    ),
                    requested_occ=str(fields.get("shadow_option_symbol") or ""),
                )
                return
            try:
                from modules.monitor_premium_snapshot import close_active_gap_interval

                rec.path_audit = close_active_gap_interval(pa)
                pa = rec.path_audit if isinstance(rec.path_audit, dict) else pa
            except Exception:
                pass
            mark = _f(fields.get("mark"))
            skip_jump = bool(pa.get("so_premium_shadow_skip_jump_check"))
            # Jump quarantine only for trusted_exact first marks; chain may gap vs last live.
            trust = str(fields.get("shadow_trust") or "")
            apply_jump = trust in ("", "trusted_exact") or bool(fields.get("shadow_quote_exact"))
            if pa.get("first_shadow_premium") is None and mark is not None:
                if skip_jump or not apply_jump:
                    pa["first_shadow_premium"] = mark
                    pa["shadow_option_symbol"] = str(
                        fields.get("shadow_option_symbol") or pa.get("entry_option_symbol") or ""
                    )
                    pa["contract_identity_match"] = True
                    pa["shadow_quote_exact"] = bool(fields.get("shadow_quote_exact"))
                    pa["shadow_trust"] = trust or (
                        "trusted_exact" if fields.get("shadow_quote_exact") else "trusted_chain"
                    )
                    rec.path_audit = pa
                else:
                    last_live = resolve_last_live_for_shadow(
                        path_audit=pa,
                        premium_path=rec.premium_path,
                        exit_premium=_f((rec.exit_observation or {}).get("exit_premium"))
                        if isinstance(rec.exit_observation, dict)
                        else None,
                        peak_quote_metadata=rec.peak_quote_metadata,
                    )
                    if last_live is None:
                        last_live = pa.get("last_live_premium")
                    if last_live is None:
                        last_live = last_live_premium_from_path(rec.premium_path)
                    ok_jump, ratio = evaluate_first_shadow_jump(last_live, mark)
                    pa["first_shadow_premium"] = mark
                    pa["first_shadow_jump_ratio"] = ratio
                    pa["shadow_option_symbol"] = str(
                        fields.get("shadow_option_symbol") or pa.get("entry_option_symbol") or ""
                    )
                    pa["contract_identity_match"] = True
                    pa["shadow_quote_exact"] = True
                    if not ok_jump:
                        pa = quarantine_shadow_path(
                            pa,
                            "first_shadow_jump",
                            first_shadow_premium=mark,
                            jump_ratio=ratio,
                        )
                        rec.path_audit = pa
                        self.note_shadow_quote_miss(
                            tid,
                            reason=f"first_shadow_jump_ratio={ratio:.4f}" if ratio is not None else "first_shadow_jump",
                        )
                        log.warning(
                            "PROFIT_OPTIMIZER_SHADOW_QUARANTINE | trade_id=%s | symbol=%s | reason=first_shadow_jump | "
                            "last_live=%s | first_shadow=%s | ratio=%s | threshold=%s",
                            tid,
                            rec.entry.symbol,
                            last_live,
                            mark,
                            ratio,
                            first_shadow_jump_ratio_threshold(),
                        )
                        self._queue_shadow_finalize(tid, rec, "shadow_quarantined")
                        return
                    rec.path_audit = pa
        prem_src = str(fields.get("premium_source") or "")
        bucket = classify_premium_source_bucket(prem_src)
        self._session_quote_counts[bucket] = int(self._session_quote_counts.get(bucket, 0)) + 1
        if path_phase == "shadow":
            self._shadow_last_sample_mono[tid] = now_mono
            self._session_shadow_quote_counts[bucket] = (
                int(self._session_shadow_quote_counts.get(bucket, 0)) + 1
            )
        pa = rec.path_audit if isinstance(rec.path_audit, dict) else {}
        q_age = _f(fields.get("quote_age_seconds"))
        stale_thresh = 30.0
        try:
            from modules.config_loader import get_config_value

            stale_thresh = float(get_config_value("PROFIT_OPTIMIZER_STALE_QUOTE_SECONDS", "30") or 30)
        except Exception:
            pass
        if q_age is not None and q_age > stale_thresh:
            pa["stale_quote_count"] = int(pa.get("stale_quote_count") or 0) + 1
        if not prem_src or bucket == "synthetic":
            pa["missing_quote_count"] = int(pa.get("missing_quote_count") or 0) + 1
        rec.path_audit = pa
        cum_vol = _f(fields.get("cumulative_volume"))
        if cum_vol is None:
            cum_vol = _f(fields.get("volume"))
        trajectory_row: Dict[str, Any] = {}
        _stamp_participation_trajectory(
            trajectory_row,
            prior_path=rec.premium_path,
            fields={**fields, "cumulative_volume": cum_vol},
        )
        sample = PremiumPathSample(
            timestamp=normalize_timestamp_iso(fields.get("timestamp")),
            underlying_price=_f(fields.get("underlying_price")),
            bid=_f(fields.get("bid")),
            ask=_f(fields.get("ask")),
            mid=_f(fields.get("mid")),
            # Prefer executable STC bid when provided (true-to-live path economics).
            mark=_f(fields.get("executable_bid_mark")) or _f(fields.get("mark")),
            spread_width=_f(fields.get("spread_width")),
            spread_pct=_f(fields.get("spread_pct")),
            volume=cum_vol,
            cumulative_volume=cum_vol,
            open_interest=_f(fields.get("open_interest")),
            delta=_f(fields.get("delta")),
            gamma=_f(fields.get("gamma")),
            theta=_f(fields.get("theta")),
            iv=_f(fields.get("iv")),
            call_chain_volume=_f(fields.get("call_chain_volume")),
            put_chain_volume=_f(fields.get("put_chain_volume")),
            call_chain_volume_now=_f(trajectory_row.get("call_chain_volume_now")),
            call_chain_volume_30s_ago=_f(trajectory_row.get("call_chain_volume_30s_ago")),
            call_chain_volume_5m_ago=_f(trajectory_row.get("call_chain_volume_5m_ago")),
            put_chain_volume_now=_f(trajectory_row.get("put_chain_volume_now")),
            put_chain_volume_30s_ago=_f(trajectory_row.get("put_chain_volume_30s_ago")),
            put_chain_volume_5m_ago=_f(trajectory_row.get("put_chain_volume_5m_ago")),
            selected_contract_volume_now=_f(trajectory_row.get("selected_contract_volume_now")),
            selected_contract_volume_30s_ago=_f(trajectory_row.get("selected_contract_volume_30s_ago")),
            selected_contract_volume_5m_ago=_f(trajectory_row.get("selected_contract_volume_5m_ago")),
            selected_call_contract_volume_now=_f(trajectory_row.get("selected_call_contract_volume_now")),
            selected_call_contract_volume_30s_ago=_f(trajectory_row.get("selected_call_contract_volume_30s_ago")),
            selected_call_contract_volume_5m_ago=_f(trajectory_row.get("selected_call_contract_volume_5m_ago")),
            selected_put_contract_volume_now=_f(trajectory_row.get("selected_put_contract_volume_now")),
            selected_put_contract_volume_30s_ago=_f(trajectory_row.get("selected_put_contract_volume_30s_ago")),
            selected_put_contract_volume_5m_ago=_f(trajectory_row.get("selected_put_contract_volume_5m_ago")),
            bid_size_now=_f(trajectory_row.get("bid_size_now")),
            bid_size_30s_ago=_f(trajectory_row.get("bid_size_30s_ago")),
            bid_size_5m_ago=_f(trajectory_row.get("bid_size_5m_ago")),
            ask_size_now=_f(trajectory_row.get("ask_size_now")),
            ask_size_30s_ago=_f(trajectory_row.get("ask_size_30s_ago")),
            ask_size_5m_ago=_f(trajectory_row.get("ask_size_5m_ago")),
            put_call_volume_ratio=_f(fields.get("put_call_volume_ratio")),
            put_call_oi_ratio=_f(fields.get("put_call_oi_ratio")),
            direction_volume_pct=_f(fields.get("direction_volume_pct")),
            call_chain_open_interest=_f(fields.get("call_chain_open_interest")),
            put_chain_open_interest=_f(fields.get("put_chain_open_interest")),
            total_chain_volume=_f(fields.get("total_chain_volume")),
            total_chain_open_interest=_f(fields.get("total_chain_open_interest")),
            representative_volume=_f(fields.get("representative_volume")),
            representative_open_interest=_f(fields.get("representative_open_interest")),
            representative_delta=_f(fields.get("representative_delta")),
            representative_bid=_f(fields.get("representative_bid")),
            representative_ask=_f(fields.get("representative_ask")),
            macd=_f(fields.get("macd")),
            macd_signal=_f(fields.get("macd_signal")),
            macd_histogram=_f(fields.get("macd_histogram")),
            prior_macd_histogram=_f(fields.get("prior_macd_histogram")),
            macd_above_zero=(
                bool(fields.get("macd_above_zero"))
                if fields.get("macd_above_zero") is not None
                else None
            ),
            macd_cross=str(fields.get("macd_cross") or ""),
            macd_hold_ok_call=(
                bool(fields.get("macd_hold_ok_call"))
                if fields.get("macd_hold_ok_call") is not None
                else None
            ),
            macd_exit_signal_call=(
                bool(fields.get("macd_exit_signal_call"))
                if fields.get("macd_exit_signal_call") is not None
                else None
            ),
            macd_hist_sign=(
                int(fields.get("macd_hist_sign"))
                if fields.get("macd_hist_sign") is not None
                else None
            ),
            sma_20=_f(fields.get("sma_20")),
            sma_50=_f(fields.get("sma_50")),
            ema_12=_f(fields.get("ema_12")),
            ema_26=_f(fields.get("ema_26")),
            rsi=_f(fields.get("rsi")),
            vwap=_f(fields.get("vwap")),
            vwap_distance_pct=_f(fields.get("vwap_distance_pct")),
            volume_ratio=_f(fields.get("volume_ratio")),
            orb_volume_ratio=_f(
                fields.get("orb_volume_ratio")
                if fields.get("orb_volume_ratio") is not None
                else fields.get("volume_ratio")
            ),
            volume_ratio_missing=(
                bool(fields.get("volume_ratio_missing"))
                if fields.get("volume_ratio_missing") is not None
                else None
            ),
            volume_ratio_source=str(fields.get("volume_ratio_source") or ""),
            tech_source=str(fields.get("tech_source") or ""),
            session_bar_count=(
                int(fields.get("session_bar_count"))
                if fields.get("session_bar_count") is not None
                else None
            ),
            vwap_missing=(
                bool(fields.get("vwap_missing")) if fields.get("vwap_missing") is not None else None
            ),
            rsi_missing=(
                bool(fields.get("rsi_missing")) if fields.get("rsi_missing") is not None else None
            ),
            session_tech_bar_at=str(fields.get("session_tech_bar_at") or "") or None,
            volume_1m=_f(fields.get("volume_1m")),
            macd_missing=(
                bool(fields.get("macd_missing")) if fields.get("macd_missing") is not None else None
            ),
            atr=_f(fields.get("atr")),
            momentum_10=_f(fields.get("momentum_10")),
            bollinger_upper=_f(fields.get("bollinger_upper")),
            bollinger_middle=_f(fields.get("bollinger_middle")),
            bollinger_lower=_f(fields.get("bollinger_lower")),
            bollinger_width=_f(fields.get("bollinger_width")),
            rs_vs_spy=_f(fields.get("rs_vs_spy")),
            hold_exit_dir_pct=_f(
                fields.get("hold_exit_dir_pct")
                if fields.get("hold_exit_dir_pct") is not None
                else fields.get("hold_dir_pct")
            ),
            quote_age_seconds=_f(fields.get("quote_age_seconds")),
            quote_quality=str(fields.get("quote_quality") or ""),
            premium_source=str(fields.get("premium_source") or ""),
            mark_quality=str(fields.get("mark_quality") or ""),
            mark_is_exit_grade=bool(fields.get("mark_is_exit_grade")),
            price_source_telemetry=str(fields.get("price_source_telemetry") or ""),
            trade_id=str(fields.get("trade_id") or tid),
            contract_symbol=_resolve_contract_symbol(rec, fields),
            observation_timestamp=str(
                fields.get("observation_timestamp") or fields.get("timestamp") or ""
            )
            or None,
            broker_quote_timestamp=str(fields.get("broker_quote_timestamp") or "") or None,
            last=_f(fields.get("last")),
            bid_size=_f(fields.get("bid_size")),
            ask_size=_f(fields.get("ask_size")),
            option_l1_imbalance=_option_l1_imbalance(
                fields.get("bid_size"), fields.get("ask_size")
            ),
            shadow_trust=str(fields.get("shadow_trust") or ""),
            contract_match_status=str(fields.get("contract_match_status") or ""),
            freshness_status=str(fields.get("freshness_status") or ""),
            monitor_tick_id=str(fields.get("monitor_tick_id") or ""),
            prem_vel_10=_f(fields.get("prem_vel_10")),
            reused_last_exact=bool(fields.get("reused_last_exact")),
            path_phase=path_phase,
        )
        mfe_up, mae_up, peak_meta = tracker.record_premium_sample(
            sample, now_mono=now_mono, freeze_latches=bool(freeze_latches)
        )
        rec.premium_path = list(tracker.premium_path)
        self._record_paired_underlying(tracker, rec, fields, path_phase)
        # Keep premium_metrics live so daily summary MFE/PnL is not forever None on open trades.
        try:
            rec.premium_metrics = tracker.build_premium_metrics().to_dict()
        except Exception:
            pass
        if rec.premium_path:
            from profit_optimizer.app_identity import stamp_path_sample

            last = rec.premium_path[-1]
            last["path_phase"] = path_phase
            last["quote_source"] = str(sample.premium_source or "")
            last["quote_age"] = sample.quote_age_seconds
            self._stamp_compact_path_state(
                last,
                tracker=tracker,
                rec=rec,
                fields=fields,
                path_phase=path_phase,
                trade_id=tid,
            )
            _stamp_participation_trajectory(
                last,
                prior_path=rec.premium_path[:-1],
                fields={**fields, "cumulative_volume": cum_vol},
            )
            if path_phase == "shadow":
                last["shadow_quote_exact"] = bool(fields.get("shadow_quote_exact"))
                if not last.get("shadow_option_symbol"):
                    pa = rec.path_audit if isinstance(rec.path_audit, dict) else {}
                    shadow_symbol = (
                        fields.get("shadow_option_symbol")
                        or pa.get("shadow_option_symbol")
                        or pa.get("entry_option_symbol")
                        or pa.get("broker_occ_symbol")
                        or last.get("contract_symbol")
                    )
                    if shadow_symbol:
                        last["shadow_option_symbol"] = str(shadow_symbol)
                if fields.get("shadow_trust"):
                    last["shadow_trust"] = str(fields.get("shadow_trust"))
                if fields.get("quote_valid") is not None:
                    last["quote_valid"] = bool(fields.get("quote_valid"))
                if fields.get("freshness_status"):
                    last["freshness_status"] = str(fields.get("freshness_status"))
                if fields.get("contract_match_status"):
                    last["contract_match_status"] = str(fields.get("contract_match_status"))
            else:
                # Live path also carries trust labels for shared-snapshot parity.
                src = str(sample.premium_source or "")
                if fields.get("shadow_trust"):
                    last["shadow_trust"] = str(fields.get("shadow_trust"))
                elif "exact" in src:
                    try:
                        from modules.exit_grade_durability import quote_source_is_exact_contract

                        if quote_source_is_exact_contract(src):
                            last["shadow_trust"] = "trusted_exact"
                        elif src.strip().lower() in ("exact", "chain_strike_match") or src.startswith("chain"):
                            last["shadow_trust"] = "trusted_chain"
                    except Exception:
                        if "exact_contract" in src:
                            last["shadow_trust"] = "trusted_exact"
                        elif src.startswith("chain") or src.strip().lower() == "exact":
                            last["shadow_trust"] = "trusted_chain"
                elif "fresh_chain" in src or src.startswith("chain"):
                    last["shadow_trust"] = "trusted_chain"
                if fields.get("freshness_status"):
                    last["freshness_status"] = str(fields.get("freshness_status"))
                if fields.get("contract_match_status"):
                    last["contract_match_status"] = str(fields.get("contract_match_status"))
                last["quote_valid"] = sample.mark is not None and float(sample.mark or 0) > 0
            stamped = stamp_path_sample(last)
            last.update(stamped)
        if peak_meta:
            last_row = rec.premium_path[-1] if rec.premium_path else {}
            rec.peak_quote_metadata = apply_peak_trust_to_metadata(
                peak_meta,
                path_phase=path_phase,
                path_audit=pa,
                sample={**last_row, **fields, "path_phase": path_phase},
            )
        if path_phase == "shadow":
            if tid not in self._shadow_sample_logged:
                self._shadow_sample_logged.add(tid)
                log.info(
                    "PROFIT_OPTIMIZER_SHADOW_SAMPLE | trade_id=%s | app=%s | mark=%s | quote_source=%s",
                    tid,
                    (rec.premium_path[-1] if rec.premium_path else {}).get("source_app"),
                    sample.mark,
                    sample.premium_source or "unknown",
                )
            elif self._log.allow(f"shadow_sample:{tid}"):
                log.info(
                    "PROFIT_OPTIMIZER_SHADOW_SAMPLE | trade_id=%s | app=%s | mark=%s | quote_source=%s",
                    tid,
                    (rec.premium_path[-1] if rec.premium_path else {}).get("source_app"),
                    sample.mark,
                    sample.premium_source or "unknown",
                )
        else:
            if tid not in self._live_sample_logged:
                self._live_sample_logged.add(tid)
                log.info(
                    "PROFIT_OPTIMIZER_LIVE_SAMPLE | trade_id=%s | mark=%s | quote_source=%s",
                    tid,
                    sample.mark,
                    sample.premium_source or "unknown",
                )
            elif self._log.allow(f"live_sample:{tid}"):
                log.info(
                    "PROFIT_OPTIMIZER_LIVE_SAMPLE | trade_id=%s | mark=%s | quote_source=%s",
                    tid,
                    sample.mark,
                    sample.premium_source or "unknown",
                )
        self._checkpoint("premium_tick", force=False)
        if mfe_up and self._log.allow(f"mfe:{tid}"):
            log.info(
                "PROFIT_OPTIMIZER_MFE_UPDATE | trade_id=%s | hwm=%.6f | peak_trusted=%s",
                tid,
                tracker._hwm_premium,
                str((peak_meta or {}).get("peak_trusted", False)).lower(),
            )
        if mae_up and self._log.allow(f"mae:{tid}"):
            log.info(
                "PROFIT_OPTIMIZER_MAE_UPDATE | trade_id=%s | lwm=%.6f",
                tid,
                tracker._lwm_premium,
            )
    def _record_paired_underlying(self, tracker: Any, rec: Any, fields: Dict[str, Any], path_phase: str) -> None:
        """One underlying row for each accepted premium row, from the shared symbol print."""
        px = _f(fields.get("underlying_price"))
        if px is None or px <= 0:
            px = _f(fields.get("underlying_last"))
        if px is None or px <= 0:
            return
        ts = str(fields.get("timestamp") or _now_iso())
        last = tracker.underlying_path[-1] if tracker.underlying_path else None
        tick = str(fields.get("monitor_tick_id") or "")
        if (
            isinstance(last, dict)
            and str(last.get("path_phase") or "") == path_phase
            and (
                (tick and str(last.get("monitor_tick_id") or "") == tick)
                or (not tick and str(last.get("timestamp") or "") == ts)
            )
        ):
            return
        sample = UnderlyingPathSample(
            timestamp=ts,
            underlying_price=float(px),
            volume=_f(fields.get("underlying_volume")),
        )
        tracker.record_underlying_sample(
            sample,
            now_mono=time.time(),
            policy=self._policies.get(str(getattr(rec.entry, "trade_id", "") or "")),
            force=True,
            path_phase=path_phase,
        )
        rec.underlying_path = list(tracker.underlying_path)
        if rec.underlying_path and tick:
            rec.underlying_path[-1]["monitor_tick_id"] = tick
            tracker.underlying_path[-1]["monitor_tick_id"] = tick

    def observe_underlying_tick(self, trade_id: str, fields: Dict[str, Any]) -> None:
        tid = str(trade_id or "").strip()
        tracker = self._trackers.get(tid)
        rec = self._records.get(tid)
        policy = self._policies.get(tid)
        if not tracker or not rec or not policy:
            return
        if rec.status not in ("open", "shadow"):
            return
        px = _f(fields.get("underlying_price"))
        if px is None or px <= 0:
            px = _f(fields.get("underlying_last"))
        if px is None or px <= 0:
            return
        now_mono = time.time()
        path_phase = "shadow" if rec.status == "shadow" else "live"
        ts = str(fields.get("timestamp") or "")
        tick = str(fields.get("monitor_tick_id") or "")
        last = tracker.underlying_path[-1] if tracker.underlying_path else None
        if (
            isinstance(last, dict)
            and str(last.get("path_phase") or "") == path_phase
            and (
                (tick and str(last.get("monitor_tick_id") or "") == tick)
                or (not tick and ts and str(last.get("timestamp") or "") == ts)
            )
        ):
            return
        sample = UnderlyingPathSample(
            timestamp=str(fields.get("timestamp") or _now_iso()),
            underlying_price=float(px),
            volume=_f(fields.get("volume")),
        )
        mfe_up, mae_up = tracker.record_underlying_sample(
            sample,
            now_mono=now_mono,
            policy=policy,
            force=rec.status == "shadow",
            path_phase=path_phase,
        )
        rec.underlying_path = list(tracker.underlying_path)
        if str(rec.entry.strategy_path) == STRATEGY_ORB_SO:
            if mfe_up and self._log.allow(f"mfe_u:{tid}"):
                log.info(
                    "PROFIT_OPTIMIZER_MFE_UPDATE | trade_id=%s | underlying_hwm=%.4f",
                    tid,
                    tracker._hwm_underlying,
                )
            if mae_up and self._log.allow(f"mae_u:{tid}"):
                log.info(
                    "PROFIT_OPTIMIZER_MAE_UPDATE | trade_id=%s | underlying_lwm=%.4f",
                    tid,
                    tracker._lwm_underlying,
                )

    def _record_exit_bid_path(
        self,
        tid: str,
        exit_prem: Optional[float],
        exit_und: Optional[float],
        exit_time: Any,
    ) -> None:
        """Keep the exit bid on the live path when the hold is shorter than the sample interval."""
        rec = self._records.get(tid)
        if rec is None or exit_prem is None or float(exit_prem) <= 0:
            return
        if rec.status not in ("open", "shadow"):
            return
        exit_ts = str(exit_time or "")
        last = rec.premium_path[-1] if rec.premium_path else None
        if isinstance(last, dict) and last.get("bid") not in (None, ""):
            try:
                same_px = abs(float(last.get("bid")) - float(exit_prem)) <= 1e-6
            except (TypeError, ValueError):
                same_px = False
            if same_px and str(last.get("timestamp") or "") == exit_ts:
                return
        self.observe_premium_tick(
            tid,
            {
                "timestamp": exit_time or _now_iso(),
                "mark": float(exit_prem),
                "bid": float(exit_prem),
                "underlying_price": exit_und,
                "premium_source": "exit_bid",
                "quote_source": "exit_bid",
                "quote_quality": "exit_bid",
                "mark_is_exit_grade": True,
                "force_path_sample": True,
                "trade_id": tid,
            },
        )

    def capture_exit(self, trade_id: str, exit_fields: Dict[str, Any]) -> None:
        tid = str(trade_id or "").strip()
        rec = self._records.get(tid)
        tracker = self._trackers.get(tid)
        if not rec:
            return
        pa_pre = rec.path_audit if isinstance(rec.path_audit, dict) else {}
        frozen_sym = str(
            pa_pre.get("entry_option_symbol")
            or pa_pre.get("broker_occ_symbol")
            or ""
        ).strip().upper()
        last_path_bid = None
        for row in rec.premium_path or []:
            if not isinstance(row, dict):
                continue
            if str(row.get("path_phase") or "") == "shadow":
                continue
            row_sym = str(row.get("contract_symbol") or "").strip().upper()
            if frozen_sym and row_sym and row_sym != frozen_sym:
                continue
            # Bid only — mark/HWM is not the contract's exit bid (TSLA 12.70 vs 2.46).
            bid_v = _f(row.get("bid"))
            if bid_v is not None and bid_v > 0:
                last_path_bid = bid_v
        from modules.path_v2_exit import resolve_frozen_contract_exit_bid

        extra_bag = exit_fields.get("extra") if isinstance(exit_fields.get("extra"), dict) else {}
        deciding_tick = str(
            extra_bag.get("monitor_tick_id")
            or extra_bag.get("deciding_monitor_tick_id")
            or exit_fields.get("monitor_tick_id")
            or ""
        )
        deciding_eval_now = (
            extra_bag.get("eval_now")
            or extra_bag.get("deciding_eval_now")
            or exit_fields.get("eval_now")
        )
        if not deciding_tick:
            for row in reversed(rec.premium_path or []):
                if not isinstance(row, dict) or not row.get("evaluated"):
                    continue
                deciding_tick = str(row.get("monitor_tick_id") or "")
                deciding_eval_now = deciding_eval_now or row.get("eval_now")
                if row.get("exit_reason"):
                    break
        if deciding_tick:
            pa_pre["deciding_monitor_tick_id"] = deciding_tick
        if deciding_eval_now:
            pa_pre["deciding_eval_now"] = str(deciding_eval_now)
        frozen_quote_bid = _f(
            exit_fields.get("frozen_symbol_bid")
            or extra_bag.get("frozen_symbol_bid")
            or exit_fields.get("entry_option_bid")
            or extra_bag.get("entry_option_bid")
        )
        if frozen_quote_bid is None:
            oq = extra_bag.get("option_quote")
            if isinstance(oq, dict):
                frozen_quote_bid = _f(oq.get("bid"))
        claimed = _f(exit_fields.get("exit_premium") or exit_fields.get("exit_bid"))
        live_hwm_pre = None
        if tracker is not None:
            live_hwm_pre = _f(getattr(tracker, "_hwm_premium", None)) or _f(
                getattr(tracker, "_peak_premium", None)
            )
        last_live = last_live_premium_from_path(rec.premium_path or [])
        claimed_stale = bool(
            exit_fields.get("claimed_stale") or extra_bag.get("claimed_stale")
        )
        exit_prem, overridden = resolve_frozen_contract_exit_bid(
            claimed_exit_bid=claimed,
            last_path_bid=last_path_bid,
            frozen_symbol_bid=frozen_quote_bid,
            last_live_premium=last_live,
            peak_premium=live_hwm_pre,
            claimed_stale=claimed_stale,
        )
        pa_pre["exit_bid_claimed"] = claimed
        pa_pre["exit_bid_path"] = last_path_bid
        pa_pre["exit_bid_frozen"] = frozen_quote_bid
        if overridden:
            pa_pre["exit_bid_mismatch"] = True
            pa_pre["exit_bid_overridden"] = True
            rec.path_audit = pa_pre
            exit_fields["exit_premium"] = exit_prem
            exit_fields["exit_bid"] = exit_prem
        else:
            rec.path_audit = pa_pre
        exit_und = _f(exit_fields.get("exit_underlying"))
        self._record_exit_bid_path(
            tid,
            exit_prem,
            exit_und,
            exit_fields.get("exit_time"),
        )
        path = str(rec.entry.strategy_path)
        so_shadow = path == STRATEGY_ORB_SO and so_premium_shadow_enabled(rec.entry)
        pm_dict: Dict[str, Any] = {}
        use_shadow = shadow_path_after_exit_enabled() and (
            path in (STRATEGY_ORB_0DTE, STRATEGY_TRENDLINE_0DTE, STRATEGY_BREAKOUT_0DTE) or so_shadow
        )
        exit_time_raw = exit_fields.get("exit_time")
        pa_existing = rec.path_audit if isinstance(rec.path_audit, dict) else {}
        window_closed = bool(
            str(pa_existing.get("recording_stop_reason") or "") in ("entry_window_elapsed", "eod")
            or (
                exit_time_raw
                and not recording_window_active(
                    rec.entry.entry_time,
                    now=exit_time_raw,
                    deadline_at=self._stamped_recording_deadline(rec),
                )
            )
        )
        if window_closed:
            use_shadow = False
            pa_existing["recording_stop_reason"] = "eod"
            pa_existing["recording_completed_at"] = normalize_timestamp_iso(
                exit_time_raw or _now_iso()
            )
            rec.path_audit = pa_existing
        extra_bag = exit_fields.get("extra") if isinstance(exit_fields.get("extra"), dict) else {}
        if deciding_tick:
            extra_bag["deciding_monitor_tick_id"] = deciding_tick
        if deciding_eval_now:
            extra_bag["deciding_eval_now"] = str(deciding_eval_now)
        exit_fields["extra"] = extra_bag
        realized_pnl = exit_fields.get("realized_pnl")
        if realized_pnl is None:
            realized_pnl = extra_bag.get("realized_pnl")
        if use_shadow and not should_shadow_after_exit(
            entry_premium=getattr(rec.entry, "entry_premium", None),
            exit_premium=exit_prem,
            realized_pnl=realized_pnl,
            realized_pnl_pct=exit_fields.get("realized_pnl_pct"),
            extra={**(rec.entry.extra or {}), **extra_bag},
        ):
            use_shadow = False
            pa_skip = rec.path_audit if isinstance(rec.path_audit, dict) else {}
            pa_skip["shadow_skip_reason"] = "not_profitable"
            rec.path_audit = pa_skip
        if tracker and (path in (STRATEGY_ORB_0DTE, STRATEGY_TRENDLINE_0DTE, STRATEGY_BREAKOUT_0DTE) or so_shadow):
            try:
                pm_exit = tracker.build_premium_metrics(exit_prem, session_scope=False)
                pm_dict = pm_exit.to_dict()
                rec.premium_metrics_at_exit = dict(pm_dict)
                rec.peak_quote_metadata = dict(tracker.peak_quote_metadata or rec.peak_quote_metadata)
                if not use_shadow:
                    rec.premium_metrics = dict(pm_dict)
            except Exception as exc:
                log.warning(
                    "PROFIT_OPTIMIZER_EXIT_OBSERVED | trade_id=%s | metrics_partial=true | error=%s",
                    tid,
                    exc,
                )
        if tracker:
            try:
                rec.underlying_metrics = tracker.build_underlying_metrics(exit_und).to_dict()
            except Exception as exc:
                log.warning(
                    "PROFIT_OPTIMIZER_EXIT_OBSERVED | trade_id=%s | underlying_metrics_failed | error=%s",
                    tid,
                    exc,
                )

        exit_time_iso = normalize_timestamp_iso(exit_fields.get("exit_time"))
        obs = ExitObservation(
            exit_time=exit_time_iso,
            exit_premium=exit_prem,
            exit_underlying=exit_und,
            exit_reason=str(exit_fields.get("exit_reason") or ""),
            realized_pnl_pct=_f(exit_fields.get("realized_pnl_pct")),
            premium_mfe_pct=pm_dict.get("premium_mfe_pct"),
            premium_mae_pct=pm_dict.get("premium_mae_pct"),
            capture_pct=pm_dict.get("capture_pct"),
            capture_of_net_opportunity_pct=pm_dict.get("capture_of_net_opportunity_pct"),
            missed_premium_pct=pm_dict.get("missed_premium_pct"),
            missed_opportunity_pct=pm_dict.get("missed_opportunity_pct"),
            extra=dict(exit_fields.get("extra") or {}) if isinstance(exit_fields.get("extra"), dict) else {},
        )
        rec.exit_observation = obs.to_dict()
        exit_reason_l = str(exit_fields.get("exit_reason") or "").lower()
        pa = rec.path_audit if isinstance(rec.path_audit, dict) else {}
        pa["last_live_premium"] = resolve_last_live_for_shadow(
            path_audit=pa,
            premium_path=rec.premium_path,
            exit_premium=exit_prem,
            peak_quote_metadata=rec.peak_quote_metadata,
        )
        if pa.get("last_live_premium") is None and exit_prem is not None:
            pa["last_live_premium"] = exit_prem
        # Trade-summary anchors for exit research (not repeated as high-churn invent).
        live_hwm = None
        if tracker is not None:
            live_hwm = _f(getattr(tracker, "_hwm_premium", None))
        if live_hwm is None:
            live_hwm = _f(pm_dict.get("premium_hwm"))
        if live_hwm is not None:
            pa["live_hwm_at_exit"] = live_hwm
            pa["shadow_hwm_since_exit"] = live_hwm
            xo = rec.exit_observation if isinstance(rec.exit_observation, dict) else {}
            xo["live_hwm_at_exit"] = live_hwm
            xo["exit_bid"] = exit_prem
            rec.exit_observation = xo
        # Stamp call/put volume, skew, owned-contract size onto exit for review.
        try:
            from profit_optimizer.path_chain_skew import (
                last_nonnull_path_chain_skew,
                merge_review_fields_onto_dict,
            )

            xo = rec.exit_observation if isinstance(rec.exit_observation, dict) else {}
            path_skew = last_nonnull_path_chain_skew(rec.premium_path, prefer_phase="live")
            extra_bag = exit_fields.get("extra") if isinstance(exit_fields.get("extra"), dict) else {}
            merge_review_fields_onto_dict(
                xo,
                path_skew,
                pa.get("path_chain_skew") if isinstance(pa.get("path_chain_skew"), dict) else {},
                extra_bag,
                exit_fields if isinstance(exit_fields, dict) else {},
                option_side=str(
                    getattr(getattr(rec, "entry", None), "direction", None)
                    or (getattr(rec.entry, "extra", {}) or {}).get("option_type")
                    or ""
                ),
                overwrite=False,
            )
            # Depth at exit when provided by monitor.
            for dk in ("bid_size", "ask_size", "cumulative_volume", "last"):
                if xo.get(dk) is None and exit_fields.get(dk) is not None:
                    xo[dk] = exit_fields.get(dk)
                if xo.get(dk) is None and isinstance(extra_bag, dict) and extra_bag.get(dk) is not None:
                    xo[dk] = extra_bag.get(dk)
            rec.exit_observation = xo
            if path_skew:
                pa["path_chain_skew"] = dict(path_skew)
                rec.path_audit = pa
        except Exception:
            pass
        if "no_progress" in exit_reason_l:
            pa["no_progress_exit_shadow"] = True
            pa["no_progress_exit_reason"] = str(exit_fields.get("exit_reason") or "")[:120]
        extra_bag = exit_fields.get("extra") if isinstance(exit_fields.get("extra"), dict) else {}
        realized_pnl = exit_fields.get("realized_pnl")
        if realized_pnl is None:
            realized_pnl = extra_bag.get("realized_pnl")
        if use_shadow and exit_is_recorded_loss(
            entry_premium=getattr(rec.entry, "entry_premium", None),
            exit_premium=exit_prem,
            realized_pnl=realized_pnl,
            realized_pnl_pct=exit_fields.get("realized_pnl_pct"),
        ):
            rec.entry.extra = dict(rec.entry.extra or {})
            loss_deadline = loss_exit_shadow_deadline_iso(
                rec.entry.entry_time,
                exit_time_iso or _now_iso(),
            )
            rec.entry.extra["recording_deadline_at"] = loss_deadline
            rec.entry.extra["loss_shadow_window"] = True
            pa["loss_shadow_window"] = True
            pa["recording_deadline_at"] = loss_deadline
        rec.path_audit = pa
        log.info(
            "PROFIT_OPTIMIZER_EXIT_OBSERVED | trade_id=%s | symbol=%s | reason=%s | exit_premium=%s | "
            "capture_pct=%s | shadow=%s",
            tid,
            rec.entry.symbol,
            obs.exit_reason,
            exit_prem,
            pm_dict.get("capture_pct"),
            str(use_shadow).lower(),
        )
        if use_shadow:
            rec.status = "shadow"
            rec.shadow_tracking = True
            log.info(
                "PROFIT_OPTIMIZER_SHADOW_PATH_START | trade_id=%s | symbol=%s | exit_premium=%s | "
                "no_progress_shadow=%s | so_premium_shadow=%s | loss_shadow=%s | shadow_until=%s",
                tid,
                rec.entry.symbol,
                exit_prem,
                str("no_progress" in exit_reason_l).lower(),
                str(so_shadow).lower(),
                str(bool((rec.entry.extra or {}).get("loss_shadow_window"))).lower(),
                str((rec.entry.extra or {}).get("recording_deadline_at") or ""),
            )
        else:
            rec.status = "closed"
            rec.closed_at = obs.exit_time
        self._checkpoint("exit_observed", force=True)

    def finalize_shadow_trades(self) -> None:
        """Compute full-session premium metrics for shadow trades at EOD."""
        pending = set(self._pending_shadow_finalize)
        self._pending_shadow_finalize.clear()
        for tid, rec in list(self._records.items()):
            if tid not in pending and not rec.shadow_tracking and rec.status != "shadow":
                continue
            tracker = self._trackers.get(tid)
            if not tracker:
                rec.status = "closed"
                rec.shadow_tracking = False
                continue
            stop_reason = str(
                (rec.path_audit or {}).get("shadow_stop_reason")
                or ("eod" if tid not in pending else self._shadow_window_stop_reason(rec))
            )
            self._finalize_single_shadow_trade(
                tid,
                rec,
                tracker,
                stop_reason=stop_reason,
                checkpoint=False,
                queued=tid in pending,
            )

    def _finalize_single_shadow_trade(
        self,
        tid: str,
        rec: TradeFlightRecord,
        tracker: PremiumTracker,
        *,
        stop_reason: str = "eod",
        checkpoint: bool = False,
        queued: bool = False,
    ) -> None:
        if not queued and not rec.shadow_tracking and rec.status != "shadow":
            return
        exit_obs = rec.exit_observation if isinstance(rec.exit_observation, dict) else {}
        exit_prem = _f(exit_obs.get("exit_premium"))
        pa = rec.path_audit if isinstance(rec.path_audit, dict) else {}
        quarantined = pa.get("shadow_path_trusted") is False
        if quarantined:
            live_only = [
                r
                for r in rec.premium_path
                if str(r.get("path_phase") or "") != "shadow"
                and str(r.get("sample_type") or "") != "gap"
            ]
            tr_live = PremiumTracker(
                strategy_path=str(rec.entry.strategy_path),
                direction=rec.entry.direction,
                entry_premium=float(rec.entry.entry_premium or 0.0),
                entry_underlying=float(rec.entry.underlying_entry_price or 0.0),
                entry_time=rec.entry.entry_time,
            )
            tr_live.rehydrate_from_saved(live_only, rec.underlying_path)
            pm_sess = tr_live.build_premium_metrics(exit_prem, session_scope=True)
            rec.peak_quote_metadata = dict(tr_live.peak_quote_metadata or rec.peak_quote_metadata)
            if rec.peak_quote_metadata:
                rec.peak_quote_metadata["peak_trusted"] = not quarantined
        else:
            pm_sess = tracker.build_premium_metrics(exit_prem, session_scope=True)
        sess_dict = pm_sess.to_dict()
        peak = sess_dict.get("peak_premium")
        eod_p = sess_dict.get("premium_at_eod")
        if peak and eod_p is not None and float(peak) > 0:
            sess_dict["capture_pct_at_eod"] = float(eod_p) / float(peak)
        sess_dict["capture_pct_at_exit"] = (
            rec.premium_metrics_at_exit.get("capture_pct")
            if isinstance(rec.premium_metrics_at_exit, dict)
            else None
        )
        if peak and exit_prem is not None and float(peak) > 0:
            sess_dict["capture_vs_session_peak_at_exit"] = float(exit_prem) / float(peak)
            sess_dict["left_on_table_at_exit_pct"] = max(
                0.0, (float(peak) - float(exit_prem)) / float(peak)
            )
        rec.premium_metrics = sess_dict
        ext = compute_session_extension_metrics(
            {
                "entry": rec.entry.to_dict(),
                "exit_observation": exit_obs,
                "premium_path": rec.premium_path,
                "premium_metrics_at_exit": rec.premium_metrics_at_exit,
                "premium_metrics": sess_dict,
                "path_audit": pa,
            }
        )
        for key, val in ext.items():
            if val is not None:
                sess_dict[key] = val
        rec.premium_metrics = sess_dict
        if ext:
            pa["post_exit_shadow_quote_count"] = ext.get("post_exit_shadow_quote_count")
            pa["post_exit_shadow_gap_count"] = ext.get("post_exit_shadow_gap_count")
            pa["post_exit_shadow_trusted"] = ext.get("post_exit_shadow_trusted")
            pa["post_exit_continued_higher"] = ext.get("post_exit_continued_higher")
        if not quarantined:
            rec.peak_quote_metadata = dict(tracker.peak_quote_metadata or rec.peak_quote_metadata)
        if quarantined and rec.peak_quote_metadata:
            rec.peak_quote_metadata["peak_trusted"] = False
        pa["shadow_path_trusted"] = not quarantined
        if quarantined:
            pa["shadow_quote_exact"] = False
        pa["shadow_stop_reason"] = str(stop_reason or "eod")
        if pa.get("no_progress_exit_shadow"):
            exit_prem_f = float(exit_prem) if exit_prem is not None else 0.0
            peak_f = float(peak) if peak is not None else 0.0
            would_win = (
                bool(peak_f > exit_prem_f * 1.05)
                if (not quarantined and exit_prem_f > 0 and peak_f > 0)
                else False
            )
            left_pct = (
                max(0.0, (peak_f - exit_prem_f) / exit_prem_f)
                if (not quarantined and exit_prem_f > 0 and peak_f > exit_prem_f)
                else 0.0
            )
            pa["no_progress_would_have_been_winner"] = would_win
            pa["no_progress_left_on_table_pct"] = left_pct
        rec.path_audit = enrich_path_audit_export(pa)
        rec.status = "closed"
        rec.shadow_tracking = False
        if not rec.closed_at:
            rec.closed_at = str(exit_obs.get("exit_time") or _now_iso())
        if self._log.allow(f"shadow_done:{tid}"):
            log.info(
                "PROFIT_OPTIMIZER_SHADOW_PATH_DONE | trade_id=%s | session_peak=%s | eod_premium=%s | "
                "capture_at_exit=%s | capture_at_eod=%s | stop_reason=%s",
                tid,
                peak,
                eod_p,
                sess_dict.get("capture_pct_at_exit"),
                sess_dict.get("capture_pct_at_eod"),
                pa.get("shadow_stop_reason", stop_reason),
            )
        if self._log.allow(f"post_exit_session:{tid}"):
            log.info(
                "PROFIT_OPTIMIZER_POST_EXIT_SESSION | trade_id=%s | symbol=%s | "
                "peak_while_open_pct=%s | post_exit_peak_pct=%s | full_session_peak_pct=%s | "
                "while_open_upside_pts=%s | post_exit_upside_pts=%s | full_session_upside_pts=%s | "
                "post_exit_continued_higher=%s | post_exit_shadow_trusted=%s | "
                "post_exit_shadow_quotes=%s | shadow_stop_reason=%s",
                tid,
                rec.entry.symbol,
                sess_dict.get("peak_while_open_pct"),
                sess_dict.get("post_exit_peak_pct"),
                sess_dict.get("full_session_peak_pct"),
                sess_dict.get("while_open_upside_pts"),
                sess_dict.get("post_exit_upside_pts"),
                sess_dict.get("full_session_upside_pts"),
                str(sess_dict.get("post_exit_continued_higher")).lower(),
                str(sess_dict.get("post_exit_shadow_trusted")).lower(),
                sess_dict.get("post_exit_shadow_quote_count"),
                pa.get("shadow_stop_reason", stop_reason),
            )
        pa = rec.path_audit if isinstance(rec.path_audit, dict) else {}
        if pa.get("no_progress_exit_shadow"):
            exit_prem_f = float(exit_prem) if exit_prem is not None else 0.0
            peak_f = float(peak) if peak is not None else 0.0
            would_win = bool(pa.get("no_progress_would_have_been_winner"))
            left_pct = float(pa.get("no_progress_left_on_table_pct") or 0.0)
            log.info(
                "NO_PROGRESS_EXIT_SHADOW_REVIEW | trade_id=%s | symbol=%s | exit_reason=%s | "
                "exit_premium=%.4f | session_peak_premium=%.4f | eod_premium=%s | "
                "shadow_path_trusted=%s | would_have_been_winner=%s | left_on_table_pct=%.4f | "
                "shadow_stop_reason=%s",
                tid,
                rec.entry.symbol,
                pa.get("no_progress_exit_reason", exit_obs.get("exit_reason", "")),
                exit_prem_f,
                peak_f,
                eod_p,
                str(not quarantined).lower(),
                str(would_win).lower(),
                left_pct,
                pa.get("shadow_stop_reason", stop_reason),
            )
        if rec.entry and str(rec.entry.strategy_path) == STRATEGY_TRENDLINE_0DTE:
            entry_prem = float(rec.entry.entry_premium or 0.0)
            peak_ret = None
            if entry_prem > 0 and peak is not None:
                peak_ret = (float(peak) - entry_prem) / entry_prem
            would_tl = bool(peak_ret is not None and peak_ret > 0.05)
            left_tl = "n/a"
            if peak_ret is not None and exit_prem is not None and entry_prem > 0:
                left_tl = f"{max(0.0, peak_ret - float(exit_prem - entry_prem) / entry_prem):.4f}"
            log.warning(
                "TRENDLINE_SHADOW_REVIEW | trade_id=%s | symbol=%s | emit_shadow=%s | "
                "peak_option_return_pct=%s | would_have_been_winner=%s | left_on_table_pct=%s | "
                "shadow_stop_reason=%s",
                tid,
                rec.entry.symbol,
                str(bool((rec.entry.extra or {}).get("emit_shadow"))).lower(),
                f"{peak_ret:.4f}" if peak_ret is not None else "n/a",
                str(would_tl).lower(),
                left_tl,
                pa.get("shadow_stop_reason", stop_reason),
            )
        if checkpoint:
            self._checkpoint("shadow_finalized", force=True)

    def finalize_open_at_eod(self) -> None:
        for tid, rec in self._records.items():
            if rec.status != "open":
                continue
            tracker = self._trackers.get(tid)
            if not tracker:
                continue
            path = str(rec.entry.strategy_path)
            so_shadow = path == STRATEGY_ORB_SO and so_premium_shadow_enabled(rec.entry)
            if path in (STRATEGY_ORB_0DTE, STRATEGY_TRENDLINE_0DTE, STRATEGY_BREAKOUT_0DTE) or so_shadow:
                rec.premium_metrics = tracker.build_premium_metrics().to_dict()
                rec.peak_quote_metadata = dict(tracker.peak_quote_metadata or rec.peak_quote_metadata)
            rec.underlying_metrics = tracker.build_underlying_metrics().to_dict()
            rec.status = "eod_open"

    def build_summary(self) -> DailySummary:
        prem_mfes: List[float] = []
        prem_maes: List[float] = []
        captures: List[float] = []
        t_mfe: List[float] = []
        t_mae: List[float] = []
        theta_pcts: List[float] = []
        by_symbol: Dict[str, List[float]] = {}
        by_strike: Dict[str, List[float]] = {}
        by_direction: Dict[str, List[float]] = {}
        by_strategy: Dict[str, List[float]] = {}
        largest_opp = 0.0
        largest_miss = 0.0

        unknown_mfe = 0
        for rec in list(self._records.values()):
            pm = rec.premium_metrics if isinstance(rec.premium_metrics, dict) else {}
            entry = rec.entry
            mfe_pct = pm.get("premium_mfe_pct") or pm.get("max_favorable_premium_pct")
            # Fallbacks when metrics were not stamped (legacy / shadow finalize gaps).
            if mfe_pct is None:
                xo = rec.exit_observation if isinstance(rec.exit_observation, dict) else {}
                mfe_pct = xo.get("premium_mfe_pct")
            if mfe_pct is None and isinstance(rec.premium_path, list) and rec.premium_path:
                path_mfes = [
                    float(r["mfe_pct"])
                    for r in rec.premium_path
                    if isinstance(r, dict) and r.get("mfe_pct") is not None
                ]
                if path_mfes:
                    mfe_pct = max(path_mfes)
            if mfe_pct is not None:
                prem_mfes.append(float(mfe_pct))
                largest_opp = max(largest_opp, float(mfe_pct))
            else:
                unknown_mfe += 1
            mae_pct = pm.get("premium_mae_pct") or pm.get("max_adverse_premium_pct")
            if mae_pct is not None:
                prem_maes.append(float(mae_pct))
            cap = pm.get("capture_pct")
            if cap is not None:
                captures.append(float(cap))
            miss = pm.get("missed_opportunity_pct")
            if miss is not None:
                largest_miss = max(largest_miss, float(miss))
            if pm.get("time_to_mfe_seconds") is not None:
                t_mfe.append(float(pm["time_to_mfe_seconds"]))
            if pm.get("time_to_mae_seconds") is not None:
                t_mae.append(float(pm["time_to_mae_seconds"]))
            if pm.get("theta_decay_pct") is not None:
                theta_pcts.append(float(pm["theta_decay_pct"]))

            sym = entry.symbol
            # Unknown MFE must not silently become 0.0 in rollups.
            if mfe_pct is not None:
                by_symbol.setdefault(sym, []).append(float(mfe_pct))
                sk = str(entry.strike or (entry.contract_identity or {}).get("strike") or "na")
                by_strike.setdefault(sk, []).append(float(mfe_pct))
                by_direction.setdefault(entry.direction, []).append(float(mfe_pct))
                by_strategy.setdefault(entry.strategy_path, []).append(float(mfe_pct))
            else:
                by_symbol.setdefault(sym, [])
                sk = str(entry.strike or (entry.contract_identity or {}).get("strike") or "na")
                by_strike.setdefault(sk, [])
                by_direction.setdefault(entry.direction, [])
                by_strategy.setdefault(entry.strategy_path, [])

        def _rollup(bucket: Dict[str, List[float]]) -> Dict[str, Any]:
            return {k: {"avg_mfe_pct": _avg(v) if v else None, "count": len(v), "unknown_mfe_count": 0} for k, v in bucket.items()}

        shadow_pcts = quote_quality_pct(self._session_shadow_quote_counts)
        gap_total = sum(
            int((r.path_audit or {}).get("shadow_gap_count") or 0) for r in self._records.values()
        )
        gap_intervals = sum(
            len((r.path_audit or {}).get("shadow_gap_intervals") or [])
            for r in self._records.values()
        )

        summary_extra = {
            **shadow_pcts,
            "shadow_gap_count_total": gap_total,
            "shadow_gap_interval_count": gap_intervals,
            "shadow_sample_count": sum(int(v) for v in self._session_shadow_quote_counts.values()),
            "session_sample_count": sum(int(v) for v in self._session_quote_counts.values()),
            "unknown_mfe_count": unknown_mfe,
        }

        ds = DailySummary(
            trade_count=len(self._records),
            avg_premium_mfe=_avg(prem_mfes),
            avg_premium_mae=_avg(prem_maes),
            avg_capture_pct=_avg(captures),
            avg_time_to_mfe_seconds=_avg(t_mfe),
            avg_time_to_mae_seconds=_avg(t_mae),
            largest_opportunity=largest_opp if largest_opp > 0 else None,
            largest_missed_opportunity=largest_miss if largest_miss > 0 else None,
            theta_decay_stats={
                "avg_theta_decay_pct": _avg(theta_pcts),
                "samples": len(theta_pcts),
                **summary_extra,
            },
            by_symbol=_rollup(by_symbol),
            by_strike=_rollup(by_strike),
            by_direction=_rollup(by_direction),
            by_strategy=_rollup(by_strategy),
        )
        return ds

    def export_trades(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        # Snapshot keys so a concurrent start_trade/observe cannot raise
        # "dictionary changed size during iteration" on the persist worker.
        for rec in list(self._records.values()):
            rec.path_audit = enrich_path_audit_export(
                rec.path_audit if isinstance(rec.path_audit, dict) else {}
            )
            out.append(rec.to_dict())
        return out

    def build_summary_by_path(self) -> Dict[str, Any]:
        grouped: Dict[str, List[Any]] = {p: [] for p in (STRATEGY_ORB_SO, STRATEGY_ORB_0DTE, STRATEGY_TRENDLINE_0DTE, STRATEGY_BREAKOUT_0DTE)}
        for rec in self._records.values():
            path = str(rec.entry.strategy_path)
            grouped.setdefault(path, []).append(rec)
        out: Dict[str, Any] = {}
        for path_key, records in grouped.items():
            if not records:
                continue
            prem_mfes: List[float] = []
            captures: List[float] = []
            calendar_0dte = 0
            calendar_fallback = 0
            for rec in records:
                pm = rec.premium_metrics if isinstance(rec.premium_metrics, dict) else {}
                mfe = pm.get("premium_mfe_pct") or pm.get("max_favorable_premium_pct")
                if mfe is not None:
                    prem_mfes.append(float(mfe))
                cap = pm.get("capture_pct")
                if cap is not None:
                    captures.append(float(cap))
                try:
                    ed = rec.entry.to_dict() if hasattr(rec.entry, "to_dict") else {}
                    is_0 = ed.get("is_0dte")
                    dte = ed.get("calendar_dte")
                    if is_0 is True or dte == 0:
                        calendar_0dte += 1
                    elif dte is not None and int(dte) > 0:
                        calendar_fallback += 1
                except Exception:
                    pass
            out[path_key] = {
                "path_class": path_class_for_strategy(path_key),
                "trade_count": len(records),
                "avg_premium_mfe_pct": _avg(prem_mfes),
                "avg_capture_pct": _avg(captures),
                "calendar_0dte_count": calendar_0dte,
                "calendar_fallback_count": calendar_fallback,
            }
        return out

    def attach_so_premium_shadow(
        self,
        trade_id: str,
        *,
        contract_identity: Dict[str, Any],
        entry_premium: float,
        expiration: str = "",
        strike: Optional[float] = None,
        selector_metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Late-bind paired 0DTE contract on an existing ORB SO record."""
        tid = str(trade_id or "").strip()
        rec = self._records.get(tid)
        if not rec or str(rec.entry.strategy_path) != STRATEGY_ORB_SO:
            return False
        from profit_optimizer.shadow_continuity import freeze_contract_identity, initial_path_audit_from_frozen

        frozen = freeze_contract_identity(contract_identity, entry=rec.entry)
        rec.entry.contract_identity = frozen
        rec.entry.entry_premium = float(entry_premium)
        if strike is not None:
            rec.entry.strike = float(strike)
        if expiration:
            rec.entry.expiration = str(expiration)
        extra = dict(rec.entry.extra or {})
        extra["premium_shadow_enabled"] = True
        extra["paired_0dte"] = True
        rec.entry.extra = extra
        if isinstance(selector_metadata, dict) and selector_metadata:
            merged = dict(rec.entry.selector_metadata or {})
            merged.update(selector_metadata)
            rec.entry.selector_metadata = merged
        rec.shadow_tracking = True
        rec.path_audit = initial_path_audit_from_frozen(frozen)
        pa = rec.path_audit if isinstance(rec.path_audit, dict) else {}
        pa["so_premium_shadow"] = True
        pa["recording_mode"] = "0dte_premium_shadow"
        pa["last_live_premium"] = float(entry_premium)
        pa["so_premium_shadow_skip_jump_check"] = True
        rec.path_audit = pa
        if tid not in self._trackers:
            policy = self._policies.get(tid) or SamplingPolicy(
                entry_time_iso=rec.entry.entry_time,
                early_interval_sec=self._early_sample_sec,
                early_window_sec=self._early_window_sec,
                late_interval_sec=self._late_sample_sec,
            )
            self._policies[tid] = policy
            tr = PremiumTracker(
                strategy_path=STRATEGY_ORB_SO,
                direction=rec.entry.direction,
                entry_premium=float(entry_premium),
                entry_underlying=float(rec.entry.underlying_entry_price or 0.0),
                entry_time=rec.entry.entry_time,
            )
            tr.set_entry_mono(time.time())
            self._trackers[tid] = tr
        log.info(
            "PROFIT_OPTIMIZER_SO_PREMIUM_SHADOW_ATTACHED | trade_id=%s | symbol=%s | entry_premium=%s",
            tid,
            rec.entry.symbol,
            entry_premium,
        )
        self._checkpoint("so_premium_shadow_attach", force=True)
        return True

    def persist_daily(self, *, run_note: str = "") -> None:
        self.flush_persist_sync(timeout_sec=60.0)
        self.flush_gcs_sync()
        with self._persist_lock:
            self.finalize_open_at_eod()
            self.finalize_shadow_trades()
            trades = self.export_trades()
            for trade in trades:
                ok, missing = validate_trade_record(trade)
                tid = (trade.get("entry") or {}).get("trade_id", "?")
                if not ok:
                    log.warning(
                        "PROFIT_OPTIMIZER_RECORD_VALIDATION_FAILED | trade_id=%s | missing=%s",
                        tid,
                        ",".join(missing),
                    )
            summary = self.build_summary()
            summary_by_path = self.build_summary_by_path()
            save_daily_dataset(
                self.session_date,
                trades,
                summary.to_dict(),
                run_note=run_note,
                summary_by_path=summary_by_path,
                sync_gcs=False,
                prune_gcs=False,
                merge_existing=True,
                include_trades_by_path=True,
            )
        export_cohort_csv(self.session_date, trades)
        export_cohort_csv_by_path(self.session_date, trades)
        self._schedule_gcs_sync(prune=True)
        log.info(
            "PROFIT_OPTIMIZER_DAILY_FINALIZED | date=%s | trades=%d | avg_capture_pct=%s | "
            "orb_so=%d | orb_0dte=%d | trendline_0dte=%d | note=%s",
            self.session_date,
            summary.trade_count,
            summary.avg_capture_pct,
            int((summary_by_path.get(STRATEGY_ORB_SO) or {}).get("trade_count") or 0),
            int((summary_by_path.get(STRATEGY_ORB_0DTE) or {}).get("trade_count") or 0),
            int((summary_by_path.get(STRATEGY_TRENDLINE_0DTE) or {}).get("trade_count") or 0),
            run_note or "",
        )


_EVAL_RECORD_COPY_KEYS = (
    "path_v2_peak",
    "path_pret",
    "path_v2_reason",
    "contracts",
    "quantity",
    "opening_fees",
    "closing_fees",
    "round_trip_fees",
    "quote_age_ms",
    "broker_quote_timestamp",
    "entry_premium",
    "actual_exit_premium",
    "actual_exit_bid",
    "actual_exit_reason",
)


def _keep_observed_path_fields(row: Dict[str, Any], fields: Dict[str, Any]) -> None:
    """Keep chain, contract, and session fields on the in-memory path row.

    Compact JSONL storage omits several of these keys. Exit review and shadow
    inheritance read the path row, including explicit zeros.
    """
    from profit_optimizer.path_chain_skew import _PATH_COPY_KEYS

    for key in _PATH_COPY_KEYS:
        if fields.get(key) is not None:
            row[key] = fields.get(key)
    for key in _EVAL_RECORD_COPY_KEYS:
        if fields.get(key) is not None and row.get(key) in (None, ""):
            row[key] = fields.get(key)


def _f(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None
