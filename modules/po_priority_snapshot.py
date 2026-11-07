"""Priority Optimizer snapshot capture.

Moved off PrimeTradingSystem so the trading core does not own row assembly,
session-tech hydrate, chain overlay, or the GCS write. Methods stay on the
instance via the mixin. Heavy imports stay inside the methods.
"""
from __future__ import annotations

import copy
import logging
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger("prime_trading_system_optimized")


def _live_chain_moment(is_exit: bool, moment: Any, stage: Any) -> bool:
    try:
        from modules.po_lifecycle_moments import is_live_chain_moment

        return is_live_chain_moment(
            is_exit=is_exit,
            stage=str(stage or ""),
            event=str(moment or ""),
            po_tech_asof=str(moment or ""),
        )
    except Exception:
        text = str(stage or "")
        moment_s = str(moment or "").lower()
        return bool(is_exit or moment_s == "mfe_20p" or "options_20p" in text)


def skip_failed_fill_executed_snapshot(
    stage: Any,
    broker_success: Optional[bool],
    *,
    symbol: Any = "",
    broker_error: Any = None,
) -> bool:
    """True → do not write an ``*_options_executed`` snapshot (or its seal).

    Oct 5: AMZN BO, MRVL/MSFT/SLV/TSLA TL wrote entry_executed + seal with
    ``broker_success=False`` (delta_out_of_band, premium/OI/cap rejects). No
    contract was bought, so the row is not an entry and must not feed PO.
    ``PO_WRITE_FAILED_EXECUTED_SNAPSHOT=true`` restores the old behavior.
    """
    if broker_success is not False:
        return False
    if "executed" not in str(stage or "").lower():
        return False
    raw = str(os.getenv("PO_WRITE_FAILED_EXECUTED_SNAPSHOT", "") or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return False
    log.info(
        "PO_SNAPSHOT_SKIP | reason=fill_failed | stage=%s | symbol=%s | broker_error=%s",
        stage,
        str(symbol or "").upper(),
        str(broker_error or "")[:160],
    )
    return True
_ENTRY_CACHE_LOCK = threading.Lock()
_ENTRY_CACHE_KEEP = 8


def _entry_cache_rows(cache: Any, symbol: str) -> List[Dict[str, Any]]:
    if not isinstance(cache, dict):
        return []
    slot = cache.get(symbol)
    if isinstance(slot, dict):
        return [slot]
    if isinstance(slot, list):
        return [row for row in slot if isinstance(row, dict)]
    return []


def _store_entry_cache_row(cache: Dict[str, Any], symbol: str, payload: Dict[str, Any]) -> None:
    rows = _entry_cache_rows(cache, symbol)
    # Deep copy. A later fill mutates nested reports on its own row. The
    # handoff cache must stay the bytes that were sealed at the freeze.
    rows.append(copy.deepcopy(payload))
    cache[symbol] = rows[-_ENTRY_CACHE_KEEP:]


def _load_durable_handoff(
    *,
    symbol: str,
    path: str,
    trade_id: Optional[str],
    freeze_at: str,
    option_side: str,
) -> Optional[Dict[str, Any]]:
    """Exact handoff object from GCS. Does not read the broker."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from modules.po_canonical_store import load_handoff_row

    p = str(path or "").strip().lower()
    if p in ("trendline", "tl", "easy_trendline_0dte"):
        strategy = "easy_trendline_0dte"
    elif p in ("breakout", "bo", "easy_breakout_0dte"):
        strategy = "easy_breakout_0dte"
    else:
        strategy = "easy_orb_0dte"
    day = datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
    return load_handoff_row(
        day,
        trade_id=str(trade_id or ""),
        symbol=str(symbol or "").upper(),
        snapshot_strategy=strategy,
        option_side=str(option_side or "").lower(),
        freeze_at=str(freeze_at or ""),
    )


def _attach_signal_freeze(row: Dict[str, Any], signal: Any) -> None:
    """Carry the gate's decision freeze from signal metadata onto the snapshot row."""
    if not isinstance(row, dict) or isinstance(row.get("po_decision_freeze"), dict):
        return
    try:
        from central_tech.decision_freeze import find_freeze

        meta = getattr(signal, "metadata", None)
        fz = find_freeze(meta if isinstance(meta, dict) else {})
        if fz is None:
            orb = getattr(signal, "orb_signal", None)
            fz = find_freeze(orb if isinstance(orb, dict) else {})
        if fz is None:
            fz = find_freeze(row)
    except Exception:
        fz = None
    if isinstance(fz, dict):
        row["po_decision_freeze"] = fz


def _link_freeze_clock_from_gate(row: Dict[str, Any]) -> None:
    """Executed rows link to their handoff by trade_id, then by the gate clock
    carried in the decision freeze (signal metadata). No restamp to now."""
    if not isinstance(row, dict) or row.get("po_decision_freeze_at") not in (None, ""):
        return
    try:
        from central_tech.decision_freeze import find_freeze

        fz = find_freeze(row)
    except Exception:
        fz = None
    if isinstance(fz, dict) and fz.get("gate_at"):
        row["po_decision_freeze_at"] = fz.get("gate_at")


def _is_handoff_row(row: Dict[str, Any]) -> bool:
    stage = str(row.get("po_snapshot_stage") or row.get("stage") or "")
    return "options_executed" not in stage and "exited" not in stage


def _select_entry_cache_row(
    rows: List[Dict[str, Any]],
    *,
    trade_id: str,
    freeze_at: str,
    option_side: str,
) -> Optional[Dict[str, Any]]:
    if trade_id:
        for row in reversed(rows):
            if not _is_handoff_row(row):
                continue
            if str(row.get("trade_id") or "").strip() == trade_id:
                return row
    if freeze_at:
        for row in reversed(rows):
            if not _is_handoff_row(row):
                continue
            if str(row.get("po_decision_freeze_at") or "") == freeze_at:
                return row
    side = option_side.lower()
    if side:
        matched = [
            row
            for row in rows
            if str(row.get("option_side") or row.get("option_type") or "").lower() == side
            and "options_executed" not in str(row.get("po_snapshot_stage") or row.get("stage") or "")
        ]
        if len(matched) == 1:
            return matched[0]
    handoffs = [
        row
        for row in rows
        if "options_executed" not in str(row.get("po_snapshot_stage") or row.get("stage") or "")
    ]
    if len(handoffs) == 1:
        return handoffs[0]
    return None


class PrioritySnapshotMixin:
    def _save_execution_feature_snapshot(
        self,
        *,
        stage: str,
        signals_full_pool: List[Dict[str, Any]],
        signals_selected_for_execution: List[Dict[str, Any]],
        date_key: str,
        portfolio_red_day_pattern: bool,
        total_scanned: int,
        snapshot_strategy: str = "easy_orb_etf_so",
        extra: Optional[Dict[str, Any]] = None,
        feature_extractor=None,
    ) -> Optional[Dict[str, Any]]:
        """Rev 00333: Persist execution-time features + gating context for priority_optimizer analysis."""
        self._po_last_persist_ok = False
        try:
            from .execution_feature_snapshot import (
                build_execution_snapshot_payload,
                persist_execution_snapshot,
            )

            if "options_executed" in str(stage or ""):
                self._relock_executed_rows_from_handoff(
                    list(signals_selected_for_execution or [])
                    + list(signals_full_pool or []),
                    snapshot_strategy=snapshot_strategy,
                    broker_success=bool((extra or {}).get("broker_success")),
                )
            stage_s = str(stage or "")
            if "emit_handoff" in stage_s or stage_s.endswith("_handoff"):
                try:
                    from modules.po_source_contract import mark_handoff_quality_gate_training

                    extra_ok = (extra or {}).get("broker_success") is True
                    if not extra_ok:
                        for row in list(signals_selected_for_execution or []) + list(
                            signals_full_pool or []
                        ):
                            mark_handoff_quality_gate_training(row)
                except Exception:
                    pass

            payload = build_execution_snapshot_payload(
                date_key=date_key,
                stage=stage,
                signals_full_pool=signals_full_pool,
                signals_selected_for_execution=signals_selected_for_execution,
                red_day_filter_blocked=bool(getattr(self, "_red_day_filter_blocked", False)),
                portfolio_red_day_pattern=portfolio_red_day_pattern,
                red_day_reason=getattr(self, "_red_day_reason", None),
                red_day_metrics=getattr(self, "_red_day_metrics", None),
                enhanced_assessment=getattr(self, "_last_enhanced_red_day_assessment", None),
                spy_momentum=float(getattr(self, "_execution_spy_momentum", 0.0)),
                vix_level=float(getattr(self, "_execution_vix_level", 15.0)),
                total_scanned=total_scanned,
                snapshot_strategy=snapshot_strategy,
                extra=extra,
                feature_extractor=feature_extractor,
            )
            gcs_path, local_path = persist_execution_snapshot(payload)
            self._po_last_persist_ok = bool(gcs_path or local_path)
            log.info(
                "EXECUTION_FEATURE_SNAPSHOT_ATTEMPT | stage=%s | strategy=%s | pool=%s | selected=%s | gcs=%s | local=%s",
                stage,
                snapshot_strategy,
                len(signals_full_pool or []),
                len(signals_selected_for_execution or []),
                gcs_path,
                local_path,
            )
            return payload
        except Exception as e:
            self._po_last_persist_ok = False
            log.warning("⚠️ Execution feature snapshot failed (non-fatal): %s", e)
            return None

    def _save_orb_0dte_pipeline_snapshot(
        self,
        *,
        pipeline_rows: List[Dict[str, Any]],
        date_key: str,
        total_scanned: int,
        pipeline_extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Rev 00350: Full 0DTE collection pool + Convex/funnel fields for priority_optimizer."""
        if not pipeline_rows:
            return
        try:
            from .execution_feature_snapshot import extract_orb_0dte_pipeline_features
            from .po_snapshot_hydration import backfill_chain_totals_from_sides

            # Cache entry features by symbol so ORB exit snaps can carry forward TA / OI-PC.
            entry_cache: Dict[str, Dict[str, Any]] = {}
            for row in pipeline_rows:
                if not isinstance(row, dict):
                    continue
                backfill_chain_totals_from_sides(row)
                # Re-stamp hub tech so invent RSI-50 / MACD-0 never lands in PO cache.
                try:
                    self._stamp_session_tech_on_po_row(
                        row,
                        symbol=str(row.get("symbol") or ""),
                        ensure_bars=True,
                        overwrite_session_authority=True,
                    )
                except Exception:
                    pass
                sym = str(row.get("symbol") or "").strip().upper()
                if sym:
                    entry_cache[sym] = dict(row)
            self._orb_0dte_po_entry_features_by_symbol = entry_cache
            self._orb_0dte_po_entry_features_date = str(date_key or "")

            selected = [
                r
                for r in pipeline_rows
                if isinstance(r, dict)
                and str(r.get("pipeline_decision_stage") or "") == "pending_execution_queue"
            ]
            extra = dict(pipeline_extra or {})
            extra.setdefault("note", "ORB1 Breakout pipeline at 7:30 after Convex + hard-gate funnel")
            self._save_execution_feature_snapshot(
                stage="orb_0dte_execution_window_input",
                signals_full_pool=list(pipeline_rows),
                signals_selected_for_execution=selected,
                date_key=date_key,
                portfolio_red_day_pattern=False,
                total_scanned=total_scanned,
                snapshot_strategy="easy_orb_0dte",
                extra=extra,
                feature_extractor=extract_orb_0dte_pipeline_features,
            )
            # Complete Signal Collection PO snapshot — every collected CALL/PUT with full
            # priority keys (pc_oi, Convex reasons, RSI/MACD/VWAP, chain) for ConvexImprovementGuide.
            sc_extra = dict(extra)
            sc_extra["note"] = (
                "Complete ORB1 Breakout signal-collection priority features "
                "(all collected symbols — Convex pass + reject) for missed-winner research"
            )
            sc_extra["signal_collection_complete"] = True
            self._save_execution_feature_snapshot(
                stage="orb_0dte_signal_collection",
                signals_full_pool=list(pipeline_rows),
                signals_selected_for_execution=list(pipeline_rows),
                date_key=date_key,
                portfolio_red_day_pattern=False,
                total_scanned=total_scanned,
                snapshot_strategy="easy_orb_0dte",
                extra=sc_extra,
                feature_extractor=extract_orb_0dte_pipeline_features,
            )
            try:
                self._persist_signal_collection_complete_features(
                    pipeline_rows=list(pipeline_rows),
                    date_key=date_key,
                    pipeline_extra=extra,
                )
            except Exception as sc_exc:
                log.warning(
                    "SIGNAL_COLLECTION_COMPLETE | persist_failed | reason=%s",
                    sc_exc,
                )
            for row in pipeline_rows:
                if isinstance(row, dict):
                    executed = str(row.get("pipeline_decision_stage") or "") == "pending_execution_queue"
                    self._record_options_priority_snapshot_row(
                        row=row,
                        executed=executed,
                        filter_reason=str(row.get("pipeline_decision_reason") or row.get("hard_gate_reason") or ""),
                    )
        except Exception as exc:
            log.debug("ORB1 Breakout pipeline snapshot skipped: %s", exc)


    def _fetch_comprehensive_for_trendline_snapshot(
        self,
        symbol: str,
        *,
        row: Optional[Dict[str, Any]] = None,
        prefer_cache: bool = True,
    ) -> Optional[Dict[str, Any]]:
        """Market data for PO snapshots — cache-first, broker only on miss.

        Discovery/path already hold fresh equity quotes; session 1m stamps MACD/RSI/VWAP.
        Prefer those over a new ``get_quotes([sym])`` (+ SPY) on every entry/exit snap.
        """
        sym = str(symbol or "").strip().upper()
        if not sym:
            return None
        if prefer_cache:
            try:
                from modules.po_snapshot_cache_hydrate import (
                    build_comprehensive_from_quote_cache,
                    session_ta_sufficient_for_po,
                )

                cached = build_comprehensive_from_quote_cache(self, sym, row=row)
                if isinstance(cached, dict) and cached:
                    # Enough for PO when session/carry TA already on the row or in the cache bag.
                    if session_ta_sufficient_for_po(row) or session_ta_sufficient_for_po(cached):
                        cached["po_market_enrichment_source"] = "quote_cache"
                        return cached
                    # Spot-only cache still useful as a hydrate seed; keep trying broker for TA.
                    if cached.get("current_price") is not None and row is not None:
                        if row.get("current_price") is None:
                            row["current_price"] = cached.get("current_price")
                        if row.get("close") is None:
                            row["close"] = cached.get("current_price")
            except Exception as cache_exc:
                log.debug("PO comprehensive cache seed skipped for %s: %s", sym, cache_exc)
        try:
            client = self._resolve_etrade_market_data_client()
            if client is None or not hasattr(client, "get_market_data_for_strategy"):
                return None
            data = client.get_market_data_for_strategy(sym)
            if not isinstance(data, dict) or not data:
                return None
            # E*TRADE often returns data_quality as a string ('excellent'|'good'|…).
            # Never int()-coerce — that raises and used to drop all TL TA stamps.
            dq = data.get("data_quality")
            quality_ok = False
            if isinstance(dq, (int, float)):
                quality_ok = float(dq) > 0
            elif isinstance(dq, str) and dq.strip():
                quality_ok = dq.strip().lower() not in {
                    "0",
                    "none",
                    "unknown",
                    "failed",
                    "bad",
                    "poor",
                    "error",
                }
            ta_keys = (
                "sma_20",
                "sma_50",
                "ema_12",
                "ema_26",
                "macd",
                "macd_histogram",
                "atr",
                "rsi",
                "vwap",
            )
            has_ta = any(data.get(k) is not None for k in ta_keys)
            if quality_ok or has_ta:
                data["po_market_enrichment_source"] = "broker"
                return data
        except Exception as exc:
            log.debug("Trendline comprehensive snapshot fetch skipped for %s: %s", sym, exc)
        return None


    def _stamp_session_tech_on_po_row(
        self,
        row: Dict[str, Any],
        *,
        symbol: Optional[str] = None,
        ensure_bars: bool = True,
        overwrite_session_authority: bool = False,
    ) -> bool:
        """
        Stamp session MACD/SMA/RSI/VWAP/volume_ratio from the shared 1m RTH bar cache.

        Fills gaps when broker hydrate fails; does not overwrite non-null fields
        already present on the row (broker daily SMA wins when hydrate succeeds)
        unless ``overwrite_session_authority=True`` — then session 1m wins for
        MACD / VWAP / RSI / volume_ratio (desk + path must match Convex hub).
        When the cache is empty, optionally sync-hydrates bars so TL/BO entry/exit
        snaps are not left with RSI-only / MACD-null (Jul 29 NFLX/AMZN).
        """
        if not isinstance(row, dict):
            return False
        sym = str(symbol or row.get("symbol") or "").strip().upper()
        if not sym:
            return False
        try:
            from modules.session_tech_indicators import (
                ensure_bollinger_complete,
                ensure_session_tech_bars_sync,
                get_session_tech,
                get_session_tech_asof,
                session_tech_enabled,
            )
            from modules.volume_data import apply_equity_session_tech

            if not session_tech_enabled():
                return False
            price = row.get("current_price") or row.get("close") or row.get("price")
            try:
                price_f = float(price) if price is not None else None
            except (TypeError, ValueError):
                price_f = None
            event_at = row.get("po_snapshot_event_at") or row.get("po_decision_freeze_at")
            if event_at not in (None, ""):
                tech = get_session_tech_asof(self, sym, event_at, price=price_f)
            else:
                tech = get_session_tech(self, sym, price=price_f)
            # Re-hydrate when MACD thin OR volume/RSI still untrusted (pad/estimate
            # bars look "warm" by count but leave volume_ratio_missing=true).
            _need_bars = (
                not tech
                or tech.get("macd") is None
                or bool(tech.get("volume_ratio_missing"))
                or bool(tech.get("rsi_missing"))
                or tech.get("bollinger_upper") is None
                or tech.get("bollinger_position") is None
            )
            if _need_bars and ensure_bars:
                from modules.po_session_tech_tick import (
                    allow_inline_session_tech_refresh,
                    session_tech_tick_primed,
                )

                if session_tech_tick_primed(self):
                    if event_at not in (None, ""):
                        tech = get_session_tech_asof(self, sym, event_at, price=price_f)
                    else:
                        tech = get_session_tech(self, sym, price=price_f)
                elif allow_inline_session_tech_refresh(self, handoff=True):
                    try:
                        ensure_session_tech_bars_sync(self, [sym], timeout_seconds=12.0)
                    except Exception as ens_exc:
                        log.debug("Session tech bar ensure skipped for %s: %s", sym, ens_exc)
                    if event_at not in (None, ""):
                        tech = get_session_tech_asof(self, sym, event_at, price=price_f)
                    else:
                        tech = get_session_tech(self, sym, price=price_f)
                else:
                    log.info(
                        "SESSION_TECH | stage=ensure_skipped | reason=eod_flatten | symbol=%s",
                        sym,
                    )
            if not tech:
                return False
            bar_at = tech.get("session_tech_bar_at")
            if bar_at not in (None, ""):
                row["session_tech_bar_at"] = bar_at
            pve_bar = tech.get("pve3_bar_at") or bar_at
            if pve_bar not in (None, ""):
                row["pve3_bar_at"] = pve_bar
            asof = tech.get("session_tech_asof_at") or pve_bar
            if asof not in (None, ""):
                row["session_tech_asof_at"] = asof
                if overwrite_session_authority or row.get("po_session_tech_asof_at") in (None, ""):
                    row["po_session_tech_asof_at"] = asof
                    if row.get("po_session_tech_source") in (None, ""):
                        row["po_session_tech_source"] = "session_tech_cache"
            try:
                from central_tech.decision_freeze import session_tech_state

                observed_at = session_tech_state(self, sym).get("observed_at")
                if observed_at not in (None, ""):
                    row["session_tech_observed_at"] = observed_at
            except Exception:
                pass
            # Only fill missing keys so broker hydrate remains authoritative when present.
            gap_keys = (
                "macd",
                "macd_signal",
                "macd_histogram",
                "prior_macd_histogram",
                "macd_above_zero",
                "macd_cross",
                "macd_hold_ok_call",
                "macd_exit_signal_call",
                "macd_hist_sign",
                "sma_20",
                "sma_50",
                "ema_12",
                "ema_26",
                "rsi",
                "rsi_14",
                "vwap",
                "vwap_distance_pct",
                "vwap_position",
                "volume_ratio",
                "price_volume_efficiency_3m",
                "price_change_3m_pct",
                "volume_3m",
                "atr",
                "momentum_10",
                "bollinger_upper",
                "bollinger_middle",
                "bollinger_lower",
                "bollinger_width",
                "bollinger_position",
                # Trust flags — must stamp so PO/path don't treat invent 1.0 / RSI-50 as real
                "volume_ratio_missing",
                "vwap_missing",
                "rsi_missing",
                "macd_missing",
                "session_bar_count",
                "volume_1m",
                "session_volume",
                "underlying_volume_1m",
                "open",
                "high",
                "low",
                "volume",
            )
            # Session-authoritative for 0DTE desk / path (match Convex session overwrite).
            # sma_20 / sma_50 on a PO row are the session 1-minute SMA, full window
            # only. A broker daily SMA is a different measurement and is not kept.
            session_authority_keys = (
                "sma_20",
                "sma_50",
                "macd",
                "macd_signal",
                "macd_histogram",
                "prior_macd_histogram",
                "macd_above_zero",
                "macd_cross",
                "macd_hold_ok_call",
                "macd_exit_signal_call",
                "macd_hist_sign",
                "ema_12",
                "ema_26",
                "rsi",
                "rsi_14",
                "vwap",
                "vwap_distance_pct",
                "volume_ratio",
                "price_volume_efficiency_3m",
                "price_change_3m_pct",
                "volume_3m",
                "volume_ratio_missing",
                "vwap_missing",
                "rsi_missing",
                "macd_missing",
                "session_bar_count",
                # BB is session-hub authority (same as Breakout) — overwrite broker quote H/L fakes.
                "bollinger_upper",
                "bollinger_middle",
                "bollinger_lower",
                "bollinger_width",
                "bollinger_position",
                "atr",
                "momentum_10",
                "volume_1m",
                "session_volume",
                "underlying_volume_1m",
                "open",
                "high",
                "low",
                "volume",
            )
            _trust_flags = (
                "volume_ratio_missing",
                "vwap_missing",
                "rsi_missing",
                "macd_missing",
            )
            needs = (
                any(row.get(k) is None for k in gap_keys)
                or bool(overwrite_session_authority)
                or bool(row.get("volume_ratio_missing"))
                or bool(row.get("rsi_missing"))
                or bool(row.get("macd_missing"))
            )
            if not needs:
                try:
                    from modules.po_observe_trajectory import stamp_po_observe_trajectory

                    stamp_po_observe_trajectory(self, row, symbol=sym)
                except Exception:
                    pass
                return False
            # Apply onto a scratch dict then merge only missing fields.
            scratch: Dict[str, Any] = {}
            apply_equity_session_tech(scratch, tech)
            stamped = 0
            for k in gap_keys:
                auth = overwrite_session_authority and k in session_authority_keys
                src_val = scratch.get(k) if scratch.get(k) is not None else tech.get(k)
                if k in ("sma_20", "sma_50"):
                    from modules.po_decision_hydrate import session_sma_for_po

                    sma_val = session_sma_for_po(tech, k)
                    if sma_val is None:
                        # Not a full 20 or 50 closed session minutes. Drop any
                        # carried or daily value so the key cannot change meaning.
                        row[k] = None
                    else:
                        row[k] = sma_val
                        stamped += 1
                    continue
                # Booleans: False is valid (volume trusted) — use `is not None`
                if src_val is None and k not in tech:
                    continue
                if k in _trust_flags:
                    src_val = bool(tech.get(k))
                if (row.get(k) is None or auth) and src_val is not None:
                    row[k] = src_val
                    stamped += 1
            if row.get("sma_20") is not None or row.get("sma_50") is not None:
                row["po_sma_horizon"] = "session_1m"
            else:
                row["po_sma_horizon"] = "session_1m_not_warm"
            if row.get("orb_volume_ratio") is None or (
                overwrite_session_authority and scratch.get("volume_ratio") is not None
            ):
                if scratch.get("volume_ratio") is not None:
                    row["orb_volume_ratio"] = scratch["volume_ratio"]
                elif tech.get("volume_ratio") is not None:
                    row["orb_volume_ratio"] = tech["volume_ratio"]
            if stamped:
                row["session_tech_po_stamp"] = True
                if overwrite_session_authority:
                    row["tech_source"] = scratch.get("tech_source") or tech.get("tech_source") or "session_1m_rth"
                else:
                    row.setdefault("tech_source", scratch.get("tech_source") or tech.get("tech_source"))
            # Never leave invent RSI-50 / MACD-0 on PO rows when hub flags missing.
            if bool(tech.get("rsi_missing")):
                row["rsi"] = None
                row["rsi_14"] = None
                row["rsi_missing"] = True
            if bool(tech.get("macd_missing")):
                row["macd_histogram"] = None
                row["prior_macd_histogram"] = None
                row["macd_signal"] = None
                row["macd"] = None
                row["macd_missing"] = True
            if bool(tech.get("volume_ratio_missing")):
                row["volume_ratio_missing"] = True
            vol_1m = tech.get("volume_1m")
            if vol_1m is not None and (
                overwrite_session_authority or row.get("underlying_volume_1m") is None
            ):
                row["volume_1m"] = vol_1m
                row["underlying_volume_1m"] = vol_1m
            # Complete BB %B / width from bands when hub or broker left position null.
            ensure_bollinger_complete(row, price=price_f)
            try:
                from modules.po_observe_trajectory import stamp_po_observe_trajectory

                stamp_po_observe_trajectory(self, row, symbol=sym)
            except Exception:
                pass
            return stamped > 0 or bool(tech.get("rsi_missing")) or bool(tech.get("macd_missing")) or bool(
                tech.get("volume_ratio_missing")
            ) or row.get("bollinger_position") is not None
        except Exception as exc:
            log.debug("Session tech PO stamp skipped for %s: %s", sym, exc)
            try:
                from modules.session_tech_indicators import ensure_bollinger_complete

                ensure_bollinger_complete(row)
            except Exception:
                pass
            try:
                from modules.po_observe_trajectory import stamp_po_observe_trajectory

                stamp_po_observe_trajectory(self, row, symbol=sym)
            except Exception:
                pass
            return False

    def _hydrate_po_snapshot_row_with_market_data(
        self,
        row: Dict[str, Any],
        *,
        symbol: Optional[str] = None,
        snapshot_strategy: str = "easy_trendline_0dte",
        force: bool = False,
        live_moment: bool = False,
        frozen_tech: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        Stamp SMA/EMA/MACD/ATR/RS onto a PO snapshot row using the same broker
        comprehensive path as ORB 7:30 (`_apply_comprehensive_data_to_so_signal`).
        Returns True when comprehensive data was applied.

        ``force=True`` (entry/exit Priority Optimizer snapshots) never defers for
        open-position quote budget — research snaps must succeed.

        Always attempts session-tech fill for MACD/SMA gaps (TL/BO often lack ORB
        7:30 TA). Prefer discovery quote cache + session TA; broker comprehensive
        only when cache/session cannot fill critical keys (avoids get_quotes+SPY
        on every snap as Path SS reject volume rises).
        """
        if not isinstance(row, dict):
            return False
        if row.get("po_entry_sealed"):
            row["po_market_enrichment_status"] = "sealed_handoff"
            row["po_market_enrichment_source"] = "decision_freeze"
            return True
        if "options_executed" in str(row.get("po_snapshot_stage") or row.get("stage") or ""):
            # Fill copies the handoff freeze. Do not restamp session tech or
            # pull comprehensive at fill time — that is not the gate row.
            row["po_market_enrichment_status"] = "sealed_handoff"
            row["po_market_enrichment_source"] = "decision_freeze"
            return True
        sym = str(symbol or row.get("symbol") or "").strip().upper()
        if not sym:
            return False
        skip_session_hub = isinstance(frozen_tech, dict)
        if skip_session_hub:
            try:
                from profit_optimizer.path_chain_skew import SESSION_TECH_PATH_KEYS

                keys = SESSION_TECH_PATH_KEYS
            except Exception:
                keys = tuple(frozen_tech.keys())
            for key in keys:
                if frozen_tech.get(key) is not None:
                    row[key] = frozen_tech.get(key)
            for key in ("open", "high", "low", "volume", "volume_1m"):
                if frozen_tech.get(key) is not None:
                    row[key] = frozen_tech.get(key)
            for key in (
                "hub_read_id",
                "hub_read_at",
                "pve3_age_s",
                "tech_age_s",
                "pve3_fresh",
                "tech_fresh",
                "refreshed_sync",
                "pve3_bar_at",
                "session_tech_asof_at",
                "price_change_3m_pct",
                "volume_3m",
                "refresh_skip",
            ):
                if frozen_tech.get(key) is not None:
                    row[key] = frozen_tech.get(key)
            row["session_tech_po_stamp"] = True
            row["po_session_tech_source"] = "event_frozen_hub"
            asof = (
                frozen_tech.get("session_tech_asof_at")
                or frozen_tech.get("pve3_bar_at")
                or frozen_tech.get("session_tech_bar_at")
            )
            if asof not in (None, ""):
                row["session_tech_asof_at"] = asof
                row["po_session_tech_asof_at"] = asof
            # Frozen hub can be empty of PVE/RSI at arm time. Overlay the
            # current 1m cache only — no broker bar fetch on this path.
            if live_moment and (
                row.get("price_volume_efficiency_3m") in (None, "")
                or row.get("rsi") in (None, "")
                or row.get("distance_from_vwap_atr") in (None, "")
            ):
                self._stamp_session_tech_on_po_row(
                    row, symbol=sym, ensure_bars=False, overwrite_session_authority=False
                )
        else:
            # Session 1m MACD/SMA/BB first so TL/BO snaps are never left null when bars exist.
            # A 20p or exit snap overwrites carried gate tech with the bars at that moment.
            self._stamp_session_tech_on_po_row(
                row, symbol=sym, overwrite_session_authority=bool(live_moment)
            )
            if row.get("bollinger_upper") is None or row.get("bollinger_position") is None:
                self._stamp_session_tech_on_po_row(
                    row, symbol=sym, overwrite_session_authority=bool(live_moment)
                )
        try:
            from modules.session_tech_indicators import ensure_bollinger_complete

            ensure_bollinger_complete(row)
        except Exception:
            pass
        try:
            from modules.po_snapshot_hydration import stamp_tp_moment_cache

            meta_now = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            osnap_now = meta_now.get("option_stealth") if isinstance(meta_now.get("option_stealth"), dict) else None
            stamp_tp_moment_cache(row, self if live_moment else None, osnap_now)
        except Exception:
            pass
        try:
            from .execution_feature_snapshot import merge_comprehensive_market_data
            from .po_snapshot_hydration import ensure_orb_range_pct

            if not force:
                try:
                    from modules.open_position_quote_orchestration import po_snapshot_heavy_enrich_allowed

                    _allow_hydrate, _hydrate_reason = po_snapshot_heavy_enrich_allowed(self, force=False)
                    if not _allow_hydrate:
                        row["po_market_enrichment_status"] = "deferred"
                        row["po_market_enrichment_deferred_reason"] = _hydrate_reason
                        row.setdefault("feature_observed_at", datetime.now(timezone.utc).isoformat())
                        ensure_orb_range_pct(row)
                        try:
                            from modules.session_tech_indicators import ensure_bollinger_complete

                            ensure_bollinger_complete(row)
                        except Exception:
                            pass
                        try:
                            from modules.volume_data import ensure_volume_research_fields

                            ensure_volume_research_fields(row)
                        except Exception:
                            pass
                        try:
                            from modules.po_observe_trajectory import stamp_po_observe_trajectory

                            stamp_po_observe_trajectory(self, row, symbol=sym)
                        except Exception:
                            pass
                        # Session stamp alone still counts as partial success for TA presence.
                        return bool(row.get("session_tech_po_stamp")) or row.get("bollinger_position") is not None
                except Exception:
                    pass
            else:
                row["po_market_enrichment_forced"] = True
            # Cache-first: when session/carry already filled critical TA, skip broker
            # comprehensive (still stamp RS from quote cache when available).
            try:
                from modules.po_snapshot_cache_hydrate import (
                    rs_vs_spy_from_quote_caches,
                    session_ta_sufficient_for_po,
                )

                if force and session_ta_sufficient_for_po(row):
                    if row.get("rs_vs_spy") is None or self._is_sentinel_missing_tech(
                        row.get("rs_vs_spy"), "rs_vs_spy"
                    ):
                        try:
                            from modules.po_snapshot_cache_hydrate import (
                                stamp_rs_vs_spy_from_quote_caches,
                            )

                            stamped = stamp_rs_vs_spy_from_quote_caches(row, self, sym)
                            if stamped.get("rs_vs_spy") is None:
                                self._stamp_live_rs_vs_spy_onto_row(row, sym)
                        except Exception:
                            try:
                                self._stamp_live_rs_vs_spy_onto_row(row, sym)
                            except Exception:
                                pass
                    ensure_orb_range_pct(row)
                    try:
                        from modules.po_snapshot_cache_hydrate import (
                            stamp_session_ohlv_from_quote_cache,
                        )

                        stamp_session_ohlv_from_quote_cache(
                            row, self, sym, overwrite=bool(live_moment)
                        )
                    except Exception:
                        pass
                    try:
                        from modules.volume_data import ensure_volume_research_fields

                        ensure_volume_research_fields(row)
                    except Exception:
                        pass
                    try:
                        from modules.po_observe_trajectory import stamp_po_observe_trajectory

                        stamp_po_observe_trajectory(self, row, symbol=sym)
                    except Exception:
                        pass
                    row["po_market_enrichment_status"] = "session_tech_cache"
                    row["po_market_enrichment_source"] = "session_tech+quote_cache"
                    return True
            except Exception:
                pass
            comprehensive = self._fetch_comprehensive_for_trendline_snapshot(sym, row=row, prefer_cache=True)
            if not comprehensive:
                ensure_orb_range_pct(row)
                try:
                    from modules.po_snapshot_cache_hydrate import (
                        stamp_session_ohlv_from_quote_cache,
                    )

                    stamp_session_ohlv_from_quote_cache(
                        row, self, sym, overwrite=bool(live_moment)
                    )
                except Exception:
                    pass
                try:
                    from modules.po_observe_trajectory import stamp_po_observe_trajectory

                    stamp_po_observe_trajectory(self, row, symbol=sym)
                except Exception:
                    pass
                # Prefer session-tech success over hard failure when bars stamped MACD/SMA.
                if row.get("macd") is not None or row.get("sma_20") is not None or row.get("macd_histogram") is not None:
                    if force:
                        row["po_market_enrichment_status"] = "session_tech"
                    return True
                if force:
                    row["po_market_enrichment_status"] = "failed"
                return False
            # Reuse ORB TA stamp (include RS for Trendline via non-ORB branch).
            self._apply_comprehensive_data_to_so_signal(
                row,
                comprehensive,
                symbol=sym,
                is_orb_symbol=False,
            )
            merge_comprehensive_market_data(
                row,
                comprehensive,
                snapshot_strategy=snapshot_strategy,
            )
            ensure_orb_range_pct(row)
            if force:
                src = str(comprehensive.get("po_market_enrichment_source") or "broker")
                row["po_market_enrichment_source"] = src
                row["po_market_enrichment_status"] = (
                    "cache" if src.startswith("quote_cache") else "ok"
                )
            # Session 1m wins after a broker overlay, including sma_20 / sma_50.
            # Those keys are the full-window session SMA, not the daily SMA.
            if not skip_session_hub:
                self._stamp_session_tech_on_po_row(
                    row, symbol=sym, overwrite_session_authority=True
                )
            try:
                from modules.session_tech_indicators import ensure_bollinger_complete

                ensure_bollinger_complete(row)
            except Exception:
                pass
            try:
                from modules.volume_data import ensure_volume_research_fields

                ensure_volume_research_fields(row)
            except Exception:
                pass
            try:
                from modules.po_observe_trajectory import stamp_po_observe_trajectory

                stamp_po_observe_trajectory(self, row, symbol=sym)
            except Exception:
                pass
            return True
        except Exception as exc:
            log.debug("PO snapshot market hydrate failed for %s: %s", sym, exc)
            try:
                from .po_snapshot_hydration import ensure_orb_range_pct

                ensure_orb_range_pct(row)
            except Exception:
                pass
            try:
                from modules.session_tech_indicators import ensure_bollinger_complete

                ensure_bollinger_complete(row)
            except Exception:
                pass
            try:
                from modules.po_observe_trajectory import stamp_po_observe_trajectory

                stamp_po_observe_trajectory(self, row, symbol=sym)
            except Exception:
                pass
            if row.get("macd") is not None or row.get("sma_20") is not None:
                if force:
                    row["po_market_enrichment_status"] = "session_tech"
                return True
            if force:
                row["po_market_enrichment_status"] = "failed"
            return False


    def _lookup_po_entry_features_cache(
        self,
        *,
        symbol: str,
        path: str,
        trade_id: Optional[str] = None,
        freeze_at: Optional[str] = None,
        option_side: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Return cached handoff/entry PO features for trajectory freeze carry-forward."""
        sym = str(symbol or "").strip().upper()
        if not sym:
            return None
        tid = str(trade_id or "").strip()
        p = str(path or "").strip().lower()
        freeze_at = str(freeze_at or "")
        side = str(option_side or "")
        with _ENTRY_CACHE_LOCK:
            if p in ("trendline", "tl", "easy_trendline_0dte"):
                if tid:
                    by_tid = getattr(self, "_trendline_po_entry_features_by_trade_id", None)
                    if isinstance(by_tid, dict) and isinstance(by_tid.get(tid), dict):
                        return by_tid.get(tid)
                cache = getattr(self, "_trendline_po_entry_features_by_symbol", None)
            elif p in ("breakout", "bo", "easy_breakout_0dte"):
                cache = getattr(self, "_breakout_po_entry_features_by_symbol", None)
            else:
                cache = getattr(self, "_orb_0dte_po_entry_features_by_symbol", None)
            return _select_entry_cache_row(
                _entry_cache_rows(cache, sym),
                trade_id=tid,
                freeze_at=freeze_at,
                option_side=side,
            )

    def _reuse_sealed_entry_handoff(
        self,
        row: Dict[str, Any],
        *,
        stage: str,
        symbol: str,
        path: str,
        trade_id: Optional[str] = None,
    ) -> bool:
        """On a fill, keep the handoff research row. Otherwise fill blanks in place.

        A fill that has no handoff still hydrates. It is not left as an empty row.
        """
        if not isinstance(row, dict):
            return False
        from modules.po_decision_hydrate import (
            absorb_in_process_decision_state,
            copy_sealed_research_state,
        )

        stage_s = str(stage or "")
        if "options_executed" not in stage_s:
            absorb_in_process_decision_state(row)
            return False
        gate = self._lookup_po_entry_features_cache(
            symbol=symbol,
            path=path,
            trade_id=trade_id,
            freeze_at=str(row.get("po_decision_freeze_at") or ""),
            option_side=str(row.get("option_side") or row.get("option_type") or ""),
        )
        gate_stage = ""
        if isinstance(gate, dict):
            gate_stage = str(gate.get("po_snapshot_stage") or gate.get("stage") or "")
        sealed = (
            isinstance(gate, dict)
            and gate.get("po_decision_freeze_at") not in (None, "")
            and "options_executed" not in gate_stage
        )
        if not sealed:
            loaded = _load_durable_handoff(
                symbol=symbol,
                path=path,
                trade_id=trade_id,
                freeze_at=str(row.get("po_decision_freeze_at") or ""),
                option_side=str(row.get("option_side") or row.get("option_type") or ""),
            )
            if isinstance(loaded, dict) and loaded.get("po_decision_freeze_at") not in (None, ""):
                copy_sealed_research_state(row, loaded)
                return True
            absorb_in_process_decision_state(row)
            return False
        copy_sealed_research_state(row, gate)
        return True

    def _clear_spot_stored_as_entry_premium(self, row: Dict[str, Any]) -> None:
        """Handoff entry_premium is the option fill. A stock print is not that fill."""
        try:
            premium = float(row.get("entry_premium"))
            spot = float(
                row.get("underlying_price")
                or row.get("current_price")
                or row.get("close")
                or 0.0
            )
        except (TypeError, ValueError):
            return
        if spot <= 1.0 or premium <= 0:
            return
        if abs(premium - spot) / spot < 0.002:
            row["entry_premium"] = None

    def _relock_executed_rows_from_handoff(
        self,
        rows: List[Dict[str, Any]],
        *,
        snapshot_strategy: str,
        broker_success: bool,
    ) -> None:
        """Last write before the fill file. The saved handoff is the lock source."""
        from modules.po_snapshot_hydration import apply_gate_tech_lock

        strategy = str(snapshot_strategy or "")
        if "trendline" in strategy:
            path = "trendline"
        elif "breakout" in strategy:
            path = "breakout"
        else:
            path = "orb"
        seen: set = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            ident = id(row)
            if ident in seen:
                continue
            seen.add(ident)
            if not broker_success:
                self._clear_spot_stored_as_entry_premium(row)
            loaded = _load_durable_handoff(
                symbol=str(row.get("symbol") or ""),
                path=path,
                trade_id=str(row.get("trade_id") or "") or None,
                freeze_at=str(row.get("po_decision_freeze_at") or ""),
                option_side=str(row.get("option_side") or row.get("option_type") or ""),
            )
            if not isinstance(loaded, dict) or loaded.get("po_decision_freeze_at") in (None, ""):
                loaded = self._lookup_po_entry_features_cache(
                    symbol=str(row.get("symbol") or ""),
                    path=path,
                    trade_id=str(row.get("trade_id") or "") or None,
                    freeze_at=str(row.get("po_decision_freeze_at") or ""),
                    option_side=str(row.get("option_side") or row.get("option_type") or ""),
                )
            gate_stage = ""
            if isinstance(loaded, dict):
                gate_stage = str(loaded.get("po_snapshot_stage") or loaded.get("stage") or "")
            if (
                isinstance(loaded, dict)
                and loaded.get("po_decision_freeze_at") not in (None, "")
                and "options_executed" not in gate_stage
            ):
                apply_gate_tech_lock(row, loaded)

    def _seal_entry_snapshot_tech(
        self,
        row: Dict[str, Any],
        *,
        stage: str,
        symbol: str,
        path: str,
        trade_id: Optional[str] = None,
    ) -> None:
        """Entry files keep the gate tech. A later owned-contract stamp does not replace it."""
        if not isinstance(row, dict):
            return
        stage_s = str(stage or "")
        if "options_executed" in stage_s:
            _link_freeze_clock_from_gate(row)
            gate = self._lookup_po_entry_features_cache(
                symbol=symbol,
                path=path,
                trade_id=trade_id,
                freeze_at=str(row.get("po_decision_freeze_at") or ""),
                option_side=str(row.get("option_side") or row.get("option_type") or ""),
            )
            gate_stage = ""
            if isinstance(gate, dict):
                gate_stage = str(gate.get("po_snapshot_stage") or gate.get("stage") or "")
            same_freeze = (
                isinstance(gate, dict)
                and gate.get("po_decision_freeze_at") not in (None, "")
                and str(row.get("po_decision_freeze_at") or "") == str(gate.get("po_decision_freeze_at"))
                and "options_executed" not in gate_stage
            )
            from modules.po_decision_hydrate import has_source_clocks, promote_entry_freeze_clocks

            if same_freeze:
                from modules.po_snapshot_hydration import apply_gate_tech_lock

                apply_gate_tech_lock(row, gate)
            else:
                # Do not label a row as gate tech when the handoff freeze was not restored.
                row["po_tech_asof"] = "executed_unsealed"
                row["po_entry_training_eligible"] = False
                if row.get("po_decision_freeze_at") not in (None, ""):
                    row["po_tech_asof_at"] = row.get("po_decision_freeze_at")
            promote_entry_freeze_clocks(row, stage=stage_s)
            if not has_source_clocks(row):
                row["po_tech_asof"] = "executed_unsealed"
                row["po_entry_training_eligible"] = False
            try:
                from modules.po_preorder_seal import apply_preorder_seal_to_row

                apply_preorder_seal_to_row(row)
            except Exception:
                pass
            return
        if "emit_handoff" in stage_s or stage_s.endswith("_handoff"):
            row["po_tech_asof"] = "gate"
            row["po_entry_training_eligible"] = True
            try:
                from modules.po_source_contract import mark_handoff_quality_gate_training

                mark_handoff_quality_gate_training(row)
            except Exception:
                pass
            self._clear_spot_stored_as_entry_premium(row)
            # B1 (Oct 7 2026): no option premium exists at handoff. The stock
            # price lives only in the underlying fields.
            row["entry_premium"] = None
            freeze = None
            try:
                from central_tech.decision_freeze import find_freeze

                freeze = find_freeze(row)
            except Exception:
                freeze = None
            try:
                from modules.po_snapshot_hydration import stamp_entry_mode

                stamp_entry_mode(row, path=path, source=row.get("metadata"))
            except Exception:
                pass
            if isinstance(freeze, dict) and freeze.get("gate_at"):
                row["po_decision_freeze_at"] = freeze.get("gate_at")
            if row.get("po_decision_freeze_at") not in (None, ""):
                row["po_tech_asof_at"] = row.get("po_decision_freeze_at")
                row["po_snapshot_event_at"] = row.get("po_decision_freeze_at")
            try:
                from easy0DTE.modules.trade_profile import apply_profile_decision

                apply_profile_decision(row)
            except Exception:
                pass
            try:
                from modules.po_decision_hydrate import (
                    attach_cached_source_clocks,
                    stamp_provenance_clocks,
                )
                attach_cached_source_clocks(row, system=self, symbol=symbol)
                if isinstance(freeze, dict):
                    # A6: the handoff carries exactly what the gate scored,
                    # with the gate's own source clocks (one freeze object).
                    from central_tech.decision_freeze import apply_freeze_to_row

                    diffs = apply_freeze_to_row(row, freeze)
                    row["po_freeze_parity_diffs"] = len(diffs)
                    log.info(
                        "TP_FREEZE_PARITY | stage=%s | symbol=%s | trade_id=%s | gate_at=%s | "
                        "replaced=%d | keys=%s",
                        stage_s,
                        symbol,
                        trade_id or row.get("trade_id"),
                        freeze.get("gate_at"),
                        len(diffs),
                        ",".join(diffs[:40]) or "-",
                    )
                else:
                    row["po_freeze_parity_diffs"] = None
                    log.warning(
                        "TP_FREEZE_PARITY | stage=%s | symbol=%s | trade_id=%s | "
                        "freeze=missing | grep=TP_FREEZE_MISSING",
                        stage_s,
                        symbol,
                        trade_id or row.get("trade_id"),
                    )
                stamp_provenance_clocks(row, event_at=row.get("po_decision_freeze_at"))
            except Exception as exc:
                log.debug("TP_FREEZE_PARITY | apply_error=%s", exc)

    def _prepare_po_trajectory_for_stage(
        self,
        row: Dict[str, Any],
        *,
        stage: str,
        symbol: str,
        path: str,
        trade_id: Optional[str] = None,
    ) -> None:
        """
        Decision-time trajectory freeze for entry research.

        - emit_handoff: mark freeze clock before hydrate/stamp (closed bars).
        - options_executed: carry trajectory from handoff cache; skip post-fill restamp.
        """
        if not isinstance(row, dict):
            return
        stage_s = str(stage or "")
        row["po_snapshot_stage"] = stage_s
        try:
            from modules.po_observe_trajectory import (
                carry_forward_trajectory_features,
                mark_decision_freeze,
            )
        except Exception:
            return
        if "emit_handoff" in stage_s or stage_s.endswith("_handoff"):
            # A6 (Oct 7 2026): the handoff freeze clock is the gate clock from
            # the decision freeze, not the (later) snapshot write time.
            when = None
            try:
                from central_tech.decision_freeze import find_freeze

                fz = find_freeze(row)
                if isinstance(fz, dict) and fz.get("gate_at"):
                    when = datetime.fromisoformat(str(fz["gate_at"]).replace("Z", "+00:00"))
            except Exception:
                when = None
            mark_decision_freeze(row, when=when)
            return
        if "options_executed" in stage_s:
            _link_freeze_clock_from_gate(row)
            cached = self._lookup_po_entry_features_cache(
                symbol=symbol,
                path=path,
                trade_id=trade_id,
                freeze_at=str(row.get("po_decision_freeze_at") or ""),
                option_side=str(row.get("option_side") or row.get("option_type") or ""),
            )
            if isinstance(cached, dict) and cached.get("po_decision_freeze_at") not in (None, ""):
                row["po_decision_freeze_at"] = cached.get("po_decision_freeze_at")
                if cached.get("po_observe_trajectory_stamp"):
                    carry_forward_trajectory_features(row, cached)
                return
            if row.get("po_entry_sealed") and row.get("po_decision_freeze_at"):
                # The handoff freeze is already on the row. Do not move it to now.
                return
            # B2 (Oct 7 2026): no handoff row for this trade_id / gate clock.
            # Never restamp the freeze to the fill time; mark the row unlinked.
            row["po_handoff_link"] = "missing"
            row["po_entry_training_eligible"] = False
            log.warning(
                "PO_HANDOFF_LINK_MISSING | stage=%s | symbol=%s | path=%s | trade_id=%s | "
                "freeze_at=%s | action=no_restamp",
                stage_s,
                symbol,
                path,
                trade_id,
                row.get("po_decision_freeze_at"),
            )

    def _cache_po_entry_features(
        self,
        *,
        symbol: str,
        row: Dict[str, Any],
        path: str,
        trade_id: Optional[str] = None,
    ) -> None:
        """Cache hydrated entry features so exits can carry forward TA if live fetch fails."""
        sym = str(symbol or "").strip().upper()
        if not sym or not isinstance(row, dict):
            return
        try:
            from modules.volume_data import ensure_volume_research_fields

            ensure_volume_research_fields(row)
        except Exception:
            pass
        try:
            from .underlying_spot_stamp import stamp_underlying_research_fields

            stamp_underlying_research_fields(
                row,
                live_underlying=row.get("underlying_price") or row.get("current_price") or row.get("close"),
                entry_underlying=(
                    row.get("entry_underlying")
                    or row.get("entry_underlying_price")
                    or row.get("underlying_price")
                    or row.get("current_price")
                ),
                option_side=row.get("option_side") or row.get("option_type") or row.get("side"),
                at_exit=False,
                source=f"cache_po_entry:{path}",
            )
        except Exception:
            pass
        # Last write on an entry row. Volume aliasing and the owned-contract
        # stamp must not replace the gate tech that gets stored and saved.
        self._seal_entry_snapshot_tech(
            row,
            stage=str(row.get("po_snapshot_stage") or row.get("stage") or ""),
            symbol=sym,
            path=path,
            trade_id=trade_id,
        )
        # Scalars plus the nested lock objects. A later fill must be able to
        # restore quality_gate_report from this cache, not only from the file.
        payload = {
            k: v
            for k, v in row.items()
            if not isinstance(v, (dict, list))
            or k in ("metadata", "quality_gate_report", "po_clock_gaps", "po_decision_freeze")
        }
        # Keep a compact chain-skew copy for premium_path forward-fill while open.
        skew = {
            k: payload.get(k)
            for k in (
                "call_chain_volume",
                "put_chain_volume",
                "put_call_volume_ratio",
                "put_call_oi_ratio",
                "call_chain_open_interest",
                "put_chain_open_interest",
                "direction_volume_pct",
                "total_chain_volume",
                "total_chain_open_interest",
                "representative_volume",
                "cumulative_volume",
                "representative_open_interest",
                "open_interest",
                "liquidity_class",
                "representative_delta",
                "representative_bid",
                "representative_ask",
                "delta",
                "iv",
                "underlying_price",
                "strike",
            )
            if payload.get(k) is not None
        }
        if skew:
            payload["_path_chain_skew"] = skew
            try:
                from modules.po_observe_trajectory import record_chain_skew_sample

                record_chain_skew_sample(self, sym, skew)
            except Exception:
                pass
        tid = str(trade_id or payload.get("trade_id") or "").strip()
        with _ENTRY_CACHE_LOCK:
            if path == "trendline":
                cache = getattr(self, "_trendline_po_entry_features_by_symbol", None)
                if not isinstance(cache, dict):
                    cache = {}
                _store_entry_cache_row(cache, sym, payload)
                self._trendline_po_entry_features_by_symbol = cache
                if tid:
                    by_tid = getattr(self, "_trendline_po_entry_features_by_trade_id", None)
                    if not isinstance(by_tid, dict):
                        by_tid = {}
                    by_tid[tid] = copy.deepcopy(payload)
                    self._trendline_po_entry_features_by_trade_id = by_tid
            elif path == "breakout":
                cache = getattr(self, "_breakout_po_entry_features_by_symbol", None)
                if not isinstance(cache, dict):
                    cache = {}
                _store_entry_cache_row(cache, sym, payload)
                self._breakout_po_entry_features_by_symbol = cache
            else:
                cache = getattr(self, "_orb_0dte_po_entry_features_by_symbol", None)
                if not isinstance(cache, dict):
                    cache = {}
                _store_entry_cache_row(cache, sym, payload)
                self._orb_0dte_po_entry_features_by_symbol = cache


    def _fetch_options_features_for_trendline_signal(
        self,
        signal: Any,
        *,
        order_payload: Optional[Dict[str, Any]] = None,
        force: bool = False,
        seed_row: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Options microstructure at Trendline emit/execution (sync-safe for hooks).

        ``force=True`` skips open-position heavy-enrich deferral so Priority Optimizer
        entry/exit snapshots always attempt a chain overlay. Prefers Layer C / row seed
        and the in-process chain TTL before a live E*TRADE pull.

        ``seed_row`` supplies hydrated spot / strike / side from the PO snapshot
        row. ORB1 fill payloads often lack ``entry_underlying_price``; using
        ``signal.entry_price`` (option premium) as spot would miss the chain.
        """
        if isinstance(seed_row, dict) and seed_row.get("po_entry_sealed"):
            return {
                "po_options_enrichment_status": "sealed_handoff",
                "chain_fetch_source": "sealed_handoff",
            }
        manager = self._options_chain_manager()
        if manager is None:
            return {
                "po_options_enrichment_status": "failed",
                "po_options_enrichment_deferred_reason": "no_options_chain_manager",
            }
        from .options_snapshot_fields import enrich_row_with_options_chain_sync

        pl = dict(order_payload or {})
        meta = getattr(signal, "metadata", None) or {}
        if not isinstance(meta, dict):
            meta = {}
        seed = seed_row if isinstance(seed_row, dict) else {}
        opt_side = (
            getattr(signal, "option_side", None)
            or getattr(signal, "option_type", None)
            or pl.get("option_side")
            or pl.get("option_type")
            or seed.get("option_type")
            or seed.get("option_side")
            or seed.get("side")
            or meta.get("expected_option_side")
        )
        spot = (
            pl.get("entry_underlying_price")
            or pl.get("current_price")
            or seed.get("current_price")
            or seed.get("underlying_price")
            or seed.get("close")
            or meta.get("entry_underlying_price")
            or getattr(signal, "current_price", None)
            or getattr(signal, "price", None)
        )
        row: Dict[str, Any] = {
            "symbol": str(
                getattr(signal, "symbol", "")
                or pl.get("symbol")
                or seed.get("symbol")
                or ""
            ).strip().upper(),
            "side": str(opt_side or seed.get("side") or "").upper(),
            "expected_option_side": meta.get("expected_option_side") or opt_side,
            "option_type": opt_side,
            "current_price": spot,
            "underlying_price": seed.get("underlying_price") or spot,
            "target_delta": pl.get("delta_at_entry")
            or pl.get("fill_delta")
            or meta.get("target_delta")
            or seed.get("target_delta"),
            "strike": pl.get("strike")
            or seed.get("strike")
            or seed.get("po_options_owned_strike")
            or meta.get("strike")
            or getattr(signal, "strike", None),
            "metadata": dict(meta),
        }
        # Seed Layer C / entry-carry / prior enrich onto the work row so we can
        # skip a live chain when admit already stamped skew.
        allow_stale = 300.0
        is_exit = False
        try:
            from modules.po_snapshot_cache_hydrate import (
                extract_options_features_from_row,
                options_microstructure_sufficient,
                po_entry_chain_allow_stale_sec,
                po_exit_chain_allow_stale_sec,
            )

            for src in (seed, meta):
                if not isinstance(src, dict):
                    continue
                seeded = extract_options_features_from_row(src)
                for k, v in seeded.items():
                    if v is not None and row.get(k) is None:
                        row[k] = v
            # Exit and the +20% MFE snap are their own clock. They must read
            # the chain then, not reuse the gate row.
            moment = str(seed.get("event") or seed.get("po_tech_asof") or "").lower()
            stage_s = str(seed.get("stage") or seed.get("po_snapshot_stage") or "")
            is_exit = bool(
                seed.get("exit_reason")
                or seed.get("exit_premium") is not None
                or moment == "exit"
                or "exited" in stage_s
                or (
                    row.get("strike") is not None
                    and (
                        seed.get("realized_pnl") is not None
                        or seed.get("exit_time") is not None
                    )
                )
            )
            try:
                from modules.po_lifecycle_moments import is_live_chain_moment

                is_live_moment = is_live_chain_moment(
                    is_exit=is_exit, stage=stage_s, event=moment, po_tech_asof=moment
                )
            except Exception:
                is_live_moment = bool(
                    is_exit or moment == "mfe_20p" or "options_20p" in stage_s
                )
            allow_stale = (
                po_exit_chain_allow_stale_sec() if is_live_moment else po_entry_chain_allow_stale_sec()
            )
            # Entry only: reuse admit Layer C fields — no second chain pull.
            # 20p and exit still prefer the chain cache, and must not skip the
            # overlay when the carried gate row already has dir%/OI.
            if force and (not is_live_moment) and options_microstructure_sufficient(row):
                features = extract_options_features_from_row(row)
                if selector_summary := (pl.get("selector_reject_summary") or pl.get("reject_reason")):
                    features["selector_reject_summary"] = str(selector_summary)
                features["po_options_enrichment_forced"] = True
                features["po_options_enrichment_status"] = "cache_row"
                features["chain_fetch_source"] = features.get("chain_fetch_source") or "row_seed"
                # Option L1 / representative / viability from the cached chain
                # (no broker call) when the gate row did not carry them.
                self._fill_entry_options_from_cached_chain(
                    features, row, is_live_moment=is_live_moment, stage=stage_s
                )
                try:
                    from modules.po_observe_trajectory import record_chain_skew_sample

                    record_chain_skew_sample(
                        self,
                        str(row.get("symbol") or "").strip().upper(),
                        features,
                    )
                except Exception:
                    pass
                return features
        except Exception:
            allow_stale = 300.0
            is_exit = False
        selector_summary = pl.get("selector_reject_summary") or pl.get("reject_reason")
        if not force:
            try:
                from modules.open_position_quote_orchestration import po_snapshot_heavy_enrich_allowed

                _allow_chain, _chain_reason = po_snapshot_heavy_enrich_allowed(self, force=False)
                if not _allow_chain:
                    # Still return seed fields when present so densify is not empty.
                    try:
                        from modules.po_snapshot_cache_hydrate import (
                            extract_options_features_from_row,
                            options_microstructure_sufficient,
                        )

                        seeded = extract_options_features_from_row(row)
                        if options_microstructure_sufficient(seeded):
                            seeded["po_options_enrichment_status"] = "deferred_cache_row"
                            seeded["po_options_enrichment_deferred_reason"] = _chain_reason
                            return seeded
                    except Exception:
                        pass
                    _deferred: Dict[str, Any] = {}
                    try:
                        # Keep partial seed values; a deferred heavy enrich must
                        # not drop what the gate row already carried.
                        for _k, _v in extract_options_features_from_row(row).items():
                            if _v is not None:
                                _deferred[_k] = _v
                    except Exception:
                        pass
                    _deferred.update(
                        {
                            "po_options_enrichment_status": "deferred",
                            "po_options_enrichment_deferred_reason": _chain_reason,
                            "feature_observed_at": datetime.now(timezone.utc).isoformat(),
                        }
                    )
                    try:
                        # Entry handoff: the cached chain costs no broker call.
                        _stage_d = str(seed.get("stage") or seed.get("po_snapshot_stage") or "")
                        _moment_d = str(seed.get("event") or seed.get("po_tech_asof") or "").lower()
                        self._fill_entry_options_from_cached_chain(
                            _deferred,
                            row,
                            is_live_moment=_live_chain_moment(
                                is_exit, _moment_d, _stage_d
                            ),
                            stage=_stage_d,
                        )
                    except Exception:
                        pass
                    return _deferred
            except Exception:
                pass
        # Prefer in-process chain TTL (Layer C / discovery) over a live pull.
        try:
            allow_stale_arg = float(allow_stale or 0.0)
        except Exception:
            allow_stale_arg = 300.0
        # While open exact-OCC owns the budget, never force a live chain for PO —
        # cache/row seed is enough; premium marks already come from the monitor.
        prefer_cache_only = False
        try:
            from modules.open_position_quote_orchestration import open_quote_priority_active

            prefer_cache_only = bool(force and open_quote_priority_active(self))
        except Exception:
            prefer_cache_only = False
        features = enrich_row_with_options_chain_sync(
            manager,
            row,
            allow_stale_cache_sec=allow_stale_arg if allow_stale_arg > 0 else None,
            prefer_cache_only=prefer_cache_only,
        )
        if not isinstance(features, dict):
            features = {}
        # If chain miss but seed had fields, keep seed fill.
        if not features or features.get("po_options_enrichment_status") == "failed":
            try:
                from modules.po_snapshot_cache_hydrate import (
                    extract_options_features_from_row,
                    options_microstructure_sufficient,
                )

                seeded = extract_options_features_from_row(row)
                if options_microstructure_sufficient(seeded):
                    features = seeded
                    features["po_options_enrichment_status"] = "cache_row"
                else:
                    # Oct 5: 7/33 TL handoffs (row_seed, no owned depth) lost
                    # the gate's chain fields here, incl. option L1. Keep every
                    # seeded value the failed enrich did not provide.
                    for _k, _v in seeded.items():
                        if _v is not None and features.get(_k) in (None, ""):
                            features[_k] = _v
            except Exception:
                pass
        try:
            _stage_main = str(seed.get("stage") or seed.get("po_snapshot_stage") or "")
            _moment_main = str(seed.get("event") or seed.get("po_tech_asof") or "").lower()
            _live_main = _live_chain_moment(is_exit, _moment_main, _stage_main)
            self._fill_entry_options_from_cached_chain(
                features, row, is_live_moment=_live_main, stage=_stage_main
            )
        except Exception:
            pass
        if selector_summary:
            features["selector_reject_summary"] = str(selector_summary)
        if force:
            features["po_options_enrichment_forced"] = True
        # Status: deferred already returned above; mark ok/failed/cache from content.
        if features.get("po_options_enrichment_status") is None:
            has_chain = any(
                features.get(k) is not None
                for k in (
                    "put_call_oi_ratio",
                    "representative_volume",
                    "call_chain_volume",
                    "put_chain_volume",
                )
            )
            src = str(features.get("chain_fetch_source") or "")
            if has_chain and src.startswith("cache"):
                features["po_options_enrichment_status"] = "cache"
            else:
                features["po_options_enrichment_status"] = "ok" if has_chain else "failed"
        # Observe-only chain trajectory ring — reuse this hydrate (no extra poll).
        try:
            from modules.po_observe_trajectory import record_chain_skew_sample

            record_chain_skew_sample(
                self,
                str(row.get("symbol") or "").strip().upper(),
                features,
            )
        except Exception:
            pass
        return features

    def _fill_entry_options_from_cached_chain(
        self,
        features: Dict[str, Any],
        row: Dict[str, Any],
        *,
        is_live_moment: bool,
        stage: str = "",
    ) -> int:
        """Entry handoff only: fill missing chain keys from the chain already cached.

        Covers option L1 (bid/ask size, imbalance), representative price /
        volume / source, viability, chain sanity and greeks for the selected
        strike. No broker call. The cached chain is the one the gate admitted on
        (Layer C may admit on a chain up to ~10 minutes old), so it is bounded by
        ``0DTE_PO_ENTRY_CHAIN_PEEK_MAX_AGE_SEC`` (default 600). Only missing keys
        are filled. 20p / exit and the executed stage never use this: their
        fields must come from their own moment.
        """
        if not isinstance(features, dict) or not isinstance(row, dict) or is_live_moment:
            return 0
        if "options_executed" in str(stage or "") or "exit" in str(stage or ""):
            return 0
        need_keys = (
            "option_l1_imbalance",
            "option_bid_size",
            "representative_price",
            "representative_volume",
            "viability_score",
        )
        if all(features.get(k) not in (None, "") for k in need_keys):
            return 0
        try:
            import os as _os

            from modules.options_snapshot_fields import (
                options_features_from_chain,
                peek_cached_chain_with_age,
                resolve_option_type_from_row,
                resolve_preferred_strike_from_row,
                resolve_target_delta_from_row,
                resolve_underlying_price_from_row,
            )

            manager = self._options_chain_manager()
            sym = str(row.get("symbol") or features.get("symbol") or "").strip().upper()
            chain, age, ts = peek_cached_chain_with_age(manager, sym)
            try:
                max_age = float(_os.getenv("0DTE_PO_ENTRY_CHAIN_PEEK_MAX_AGE_SEC", "600") or 600)
            except (TypeError, ValueError):
                max_age = 600.0
            if chain is None or age is None:
                features.setdefault("po_entry_chain_peek", "miss")
                return 0
            if age > max_age:
                features.setdefault("po_entry_chain_peek", "stale")
                features.setdefault("po_entry_chain_peek_age_sec", round(float(age), 1))
                return 0
            work = dict(row)
            for k, v in features.items():
                if v is not None and work.get(k) in (None, ""):
                    work[k] = v
            spot = resolve_underlying_price_from_row(work)
            if spot <= 0:
                return 0
            observed_iso = datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
            got = options_features_from_chain(
                manager,
                chain,
                symbol=sym,
                option_type=resolve_option_type_from_row(work),
                underlying_price=spot,
                target_delta=resolve_target_delta_from_row(work),
                preferred_strike=resolve_preferred_strike_from_row(work),
                chain_meta={"observed_at": observed_iso},
            )
            n = 0
            for k, v in (got or {}).items():
                if v is None or features.get(k) not in (None, ""):
                    continue
                features[k] = v
                n += 1
            if n:
                features["po_entry_chain_peek"] = "filled"
                features["po_entry_chain_peek_age_sec"] = round(float(age), 1)
                if str(features.get("chain_fetch_source") or "") in ("", "row_seed", "cache_miss", "None"):
                    features["chain_fetch_source"] = "cache_peek_entry"
                if features.get("po_options_enrichment_status") in (None, "failed"):
                    features["po_options_enrichment_status"] = "cache"
                features.setdefault("chain_observed_at", observed_iso)
            return n
        except Exception as exc:
            log.debug("PO entry chain peek fill skipped: %s", exc)
            return 0

    def _fetch_options_features_for_trendline_from_break(
        self,
        *,
        candidate: Any,
        break_bar: Any,
        expected_option_side: Optional[str] = None,
    ) -> Dict[str, Any]:
        manager = self._options_chain_manager()
        if manager is None:
            return {}
        from .options_snapshot_fields import enrich_row_with_options_chain_sync

        sym = str(getattr(candidate, "symbol", "") or "").strip().upper()
        try:
            spot = float(getattr(break_bar, "close", 0.0) or 0.0)
        except (TypeError, ValueError):
            spot = 0.0
        row: Dict[str, Any] = {
            "symbol": sym,
            "expected_option_side": expected_option_side,
            "option_type": expected_option_side,
            "current_price": spot,
            "close_at_break": spot,
        }
        return enrich_row_with_options_chain_sync(manager, row)

    def _record_options_priority_snapshot_row(
        self,
        *,
        row: Dict[str, Any],
        executed: bool,
        filter_reason: str = "",
    ) -> None:
        """Integrate OptionsPriorityDataCollector with automated PO snapshots (DEMO research)."""
        collector = getattr(self, "_options_priority_collector", None)
        if collector is None:
            return
        try:
            sym = (row.get("symbol") or "").strip().upper()
            if not sym:
                return
            payload = dict(row)
            payload["executed"] = bool(executed)
            payload["filtered"] = not executed
            if filter_reason:
                payload["filter_reason"] = filter_reason
            collector.record_signal_collection([payload])
        except Exception as exc:
            log.debug("Options priority collector snapshot row skipped: %s", exc)

    def _save_trendline_execution_priority_snapshot(
        self,
        *,
        signal: Any,
        candidate: Optional[Any],
        stage: str,
        order_payload: Optional[Dict[str, Any]] = None,
        broker_success: Optional[bool] = None,
        broker_error: Optional[str] = None,
    ) -> None:
        """Persist Trendline emit handoff or fill snapshot (ORB1 Breakout–parity PO artifact)."""
        if skip_failed_fill_executed_snapshot(
            stage,
            broker_success,
            symbol=getattr(signal, "symbol", ""),
            broker_error=broker_error,
        ):
            return
        try:
            from zoneinfo import ZoneInfo

            from .execution_feature_snapshot import (
                extract_trendline_pool_features,
            )
            from .trendline_break_snapshot import get_trendline_break_snapshot_collector
            from .trendline_telemetry import build_trendline_execution_snapshot_signal

            snap_date = datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
            collector = getattr(self, "_trendline_break_snapshot_collector", None)
            if collector is None:
                collector = get_trendline_break_snapshot_collector()
                self._trendline_break_snapshot_collector = collector
            sig_meta = signal.metadata if isinstance(getattr(signal, "metadata", None), dict) else {}
            setup_nested = (
                sig_meta.get("setup_payload")
                if isinstance(sig_meta.get("setup_payload"), dict)
                else {}
            )
            cand_setup = (
                getattr(candidate, "setup_payload", None) if candidate is not None else None
            )
            if not isinstance(cand_setup, dict):
                cand_setup = {}
            try:
                from easyTrendline.trendline_models import normalize_trendline_slot

                tl_slot = normalize_trendline_slot(
                    sig_meta.get("trendline_slot")
                    or setup_nested.get("trendline_slot")
                    or cand_setup.get("trendline_slot")
                )
            except Exception:
                tl_slot = "trendline1"
            break_trade_id = str(
                sig_meta.get("break_trade_id")
                or ""
            ).strip() or None
            break_row = collector.find_break_for_symbol(
                signal.symbol,
                date_key=snap_date,
                trendline_slot=tl_slot,
                trade_id=break_trade_id,
            )
            pl = dict(order_payload or {})
            trendline_snapshot_signal = build_trendline_execution_snapshot_signal(
                signal,
                candidate=candidate,
                order_payload=pl,
                break_snapshot_row=break_row,
            )
            trendline_snapshot_signal["stage"] = stage
            trendline_snapshot_signal["po_snapshot_stage"] = stage
            _attach_signal_freeze(trendline_snapshot_signal, signal)
            self._reuse_sealed_entry_handoff(
                trendline_snapshot_signal,
                stage=stage,
                symbol=str(getattr(signal, "symbol", "") or ""),
                path="trendline",
                trade_id=str(pl.get("trade_id") or (signal.metadata or {}).get("trade_id") or "")
                or None,
            )
            options_features = self._fetch_options_features_for_trendline_signal(
                signal,
                order_payload=pl,
                force=True,
                seed_row=trendline_snapshot_signal,
            )
            if options_features:
                from modules.po_snapshot_hydration import merge_gate_options_features

                merge_gate_options_features(trendline_snapshot_signal, options_features)
            try:
                from modules.po_observe_trajectory import (
                    apply_selected_contract_option_l1,
                    stamp_break_timing_fields,
                )

                stamp_break_timing_fields(
                    trendline_snapshot_signal,
                    break_ts=(
                        (break_row or {}).get("break_timestamp")
                        if isinstance(break_row, dict)
                        else None
                    )
                    or sig_meta.get("break_timestamp")
                    or sig_meta.get("break_ts"),
                    close_at_break=(
                        (break_row or {}).get("close_at_break")
                        if isinstance(break_row, dict)
                        else None
                    )
                    or sig_meta.get("close_at_break"),
                    line_price_at_break=(
                        (break_row or {}).get("line_price_at_break")
                        if isinstance(break_row, dict)
                        else None
                    )
                    or sig_meta.get("line_price_at_break"),
                )
                apply_selected_contract_option_l1(
                    trendline_snapshot_signal,
                    order_payload=pl,
                    prefer_owned=("options_executed" in str(stage or "")),
                )
            except Exception:
                pass
            _tl_sym = str(getattr(signal, "symbol", "") or "").strip().upper()
            self._prepare_po_trajectory_for_stage(
                trendline_snapshot_signal,
                stage=stage,
                symbol=_tl_sym,
                path="trendline",
                trade_id=str(
                    trendline_snapshot_signal.get("trade_id")
                    or pl.get("trade_id")
                    or ""
                ).strip()
                or None,
            )
            # ORB-parity TA stamp (SMA/EMA/MACD/ATR/RS) + orb_range_pct
            self._hydrate_po_snapshot_row_with_market_data(
                trendline_snapshot_signal,
                symbol=_tl_sym,
                snapshot_strategy="easy_trendline_0dte",
                force=True,
            )
            try:
                from modules.po_snapshot_hydration import stamp_owned_contract_microstructure

                stamp_owned_contract_microstructure(
                    trendline_snapshot_signal,
                    order_payload=pl,
                )
            except Exception:
                pass
            try:
                from easy0DTE.modules.quality_gate_stack import (
                    PATH_TL,
                    append_research_stamp_and_contract_gates,
                )

                append_research_stamp_and_contract_gates(
                    PATH_TL,
                    trendline_snapshot_signal,
                    prior_report=trendline_snapshot_signal.get("quality_gate_report"),
                    stamp_target=trendline_snapshot_signal,
                )
            except Exception:
                pass
            try:
                from .underlying_spot_stamp import (
                    resolve_entry_underlying,
                    stamp_underlying_research_fields,
                )

                live_u = (
                    trendline_snapshot_signal.get("underlying_price")
                    or trendline_snapshot_signal.get("current_price")
                    or trendline_snapshot_signal.get("close")
                    or pl.get("entry_underlying_price")
                )
                entry_u = resolve_entry_underlying(
                    row=trendline_snapshot_signal,
                    signal_snapshot=getattr(signal, "metadata", None)
                    if isinstance(getattr(signal, "metadata", None), dict)
                    else None,
                    order_payload=pl,
                ) or live_u
                stamp_underlying_research_fields(
                    trendline_snapshot_signal,
                    live_underlying=live_u,
                    entry_underlying=entry_u,
                    option_side=getattr(signal, "option_side", None),
                    at_exit=False,
                    source="trendline_execution_snap",
                )
            except Exception:
                pass
            fill_tid = pl.get("trade_id") or (signal.metadata or {}).get("trade_id")
            if fill_tid:
                trendline_snapshot_signal["trade_id"] = fill_tid
                md_tid = trendline_snapshot_signal.setdefault("metadata", {})
                if isinstance(md_tid, dict):
                    md_tid["trade_id"] = fill_tid
            trendline_snapshot_signal["trendline_slot"] = tl_slot
            meta = trendline_snapshot_signal.setdefault("metadata", {})
            if isinstance(meta, dict):
                meta["trendline_slot"] = tl_slot
                meta.setdefault("broker_success", broker_success)
                if broker_error:
                    meta["broker_error"] = broker_error
                if pl.get("reject_reason"):
                    meta["reject_reason"] = pl.get("reject_reason")
            # Cache hydrated entry features for exit carry-forward (fills + rejects)
            try:
                self._cache_po_entry_features(
                    symbol=str(getattr(signal, "symbol", "") or ""),
                    row=trendline_snapshot_signal,
                    path="trendline",
                    trade_id=str(fill_tid or "") or None,
                )
            except Exception:
                pass
            self._save_execution_feature_snapshot(
                stage=stage,
                signals_full_pool=[trendline_snapshot_signal],
                signals_selected_for_execution=[trendline_snapshot_signal],
                date_key=snap_date,
                portfolio_red_day_pattern=bool(getattr(self, "_red_day_filter_blocked", False)),
                total_scanned=int(getattr(self, "_trendline_candidate_universe_size", 0) or 0),
                snapshot_strategy="easy_trendline_0dte",
                extra={
                    "strategy_name": "easyTrendline_0DTE",
                    "source_path": "easyTrendline",
                    "trade_id": fill_tid,
                    "trendline_slot": tl_slot,
                    "setup_type": (signal.metadata or {}).get("setup_type"),
                    "structure_display_label": (signal.metadata or {}).get("structure_display_label"),
                    "trigger_direction": (signal.metadata or {}).get("trigger_direction"),
                    "line_geometry": signal.direction.value if getattr(signal, "direction", None) else None,
                    "option_side": signal.option_side,
                    "break_snapshot_joined": bool(break_row),
                    "broker_success": broker_success,
                    "broker_error": broker_error,
                },
                feature_extractor=extract_trendline_pool_features,
            )
            self._record_options_priority_snapshot_row(
                row=trendline_snapshot_signal,
                executed=bool(broker_success),
                filter_reason=str(broker_error or pl.get("reject_reason") or ""),
            )
        except Exception as exc:
            log.warning(
                "TRENDLINE_PIPELINE | stage=%s | snapshot_error=%s",
                stage,
                exc,
            )


    def _save_0dte_exit_priority_snapshot(
        self,
        *,
        stage: str,
        snapshot_strategy: str,
        symbol: str,
        trade_id: str,
        option_side: Optional[str] = None,
        exit_reason: Optional[str] = None,
        exit_premium: Optional[float] = None,
        entry_premium: Optional[float] = None,
        realized_pnl: Optional[float] = None,
        realized_pnl_pct: Optional[float] = None,
        exit_underlying: Optional[float] = None,
        position: Any = None,
        extra: Optional[Dict[str, Any]] = None,
        purpose: str = "exit",
        frozen_tech: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Persist ORB1 Breakout / Trendline exit technical snapshot for Priority Optimizer matrix.

        ``purpose="20p"`` is the one-shot snapshot at the first +20% MFE. It uses the
        same carry, session-tech, and chain hydration as entry/exit, but it is not an
        exit: no exit reason, no exit premium, and chain overlay stays on the entry
        cache window so a live broker pull is only the fallback.
        """
        try:
            from zoneinfo import ZoneInfo

            from .execution_feature_snapshot import (
                extract_0dte_unified_features,
            )
            from .po_snapshot_hydration import (
                carry_forward_entry_features,
                count_critical_populated,
                ensure_po_desk_aliases,
                features_from_break_record,
                overlay_options_features,
                resolve_underlying_symbol,
                stamp_exit_outcome_fields,
            )
            from modules.po_event_seal import (
                mark_persisted_20p,
                mark_persisted_exit,
                persisted_20p,
                persisted_exit,
                remember_20p,
                remember_exit,
                sealed_20p,
                sealed_exit,
            )

            from modules.po_lifecycle_moments import (
                event_clock_key,
                family_for_purpose,
                is_mid_trade_purpose,
                is_new_lifecycle_purpose,
                tech_asof_for_purpose,
            )

            purpose_s = str(purpose or "exit")
            is_20p_retry = purpose_s == "20p"
            is_mid_retry = is_mid_trade_purpose(purpose_s)
            tid_early = str(trade_id or "").strip()
            mid_identity = tid_early
            if is_new_lifecycle_purpose(purpose_s) and isinstance(extra, dict):
                # A pullback ladder shot names itself (``<trade_id>__pullback_s{n}``);
                # extra.trade_id stays the real trade id.
                mid_identity = str(
                    extra.get("po_event_identity") or extra.get("trade_id") or tid_early
                ).strip()
            if not is_mid_retry:
                from modules.po_canonical_store import durable_event_present
                from modules.po_event_seal import clock_born_this_process_exit

                exit_day = datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
                live_md = (
                    position.metadata
                    if position is not None and isinstance(getattr(position, "metadata", None), dict)
                    else None
                )
                if tid_early and durable_event_present(exit_day, "exit", tid_early):
                    mark_persisted_exit(tid_early)
                    return True
                from modules.po_canonical_store import load_sealed_payload

                sealed_exit_payload = load_sealed_payload(exit_day, "exit", tid_early)
                if isinstance(sealed_exit_payload, dict):
                    from modules.execution_feature_snapshot import persist_execution_snapshot

                    persist_execution_snapshot(sealed_exit_payload)
                    mark_persisted_exit(tid_early)
                    if isinstance(live_md, dict):
                        live_md["po_exit_seal_status"] = "recovered_seal"
                    return True
                existing_exit = live_md.get("po_exit_event_at") if isinstance(live_md, dict) else None
                if (
                    existing_exit not in (None, "")
                    and sealed_exit(tid_early) is None
                    and not clock_born_this_process_exit(existing_exit)
                ):
                    if isinstance(live_md, dict):
                        live_md["po_exit_seal_status"] = "recovery_unsealed"
                        live_md["po_exit_training_eligible"] = False
                    log.warning(
                        "PO_EXIT_SNAPSHOT | recovery_unsealed | trade_id=%s | event_at=%s",
                        tid_early,
                        existing_exit,
                    )
                    return False
            if is_20p_retry:
                prior_payload = sealed_20p(tid_early)
            elif is_new_lifecycle_purpose(purpose_s):
                from modules.po_event_seal import persisted_event, sealed_event

                prior_payload = sealed_event(purpose_s, mid_identity)
            else:
                prior_payload = sealed_exit(tid_early)
            if isinstance(prior_payload, dict):
                if is_20p_retry:
                    already = persisted_20p(tid_early)
                elif is_new_lifecycle_purpose(purpose_s):
                    already = persisted_event(purpose_s, mid_identity)
                else:
                    already = persisted_exit(tid_early)
                if already:
                    return True
                if not is_mid_retry:
                    selected = prior_payload.get("features_selected")
                    feat = selected[0] if isinstance(selected, list) and selected else None
                    if isinstance(feat, dict):
                        for key, val in (
                            ("exit_premium", exit_premium),
                            ("exit_price", exit_premium),
                            ("exit_reason", exit_reason),
                            ("realized_pnl", realized_pnl),
                            ("realized_pnl_pct", realized_pnl_pct),
                        ):
                            if val not in (None, "") and feat.get(key) in (None, ""):
                                feat[key] = val
                from .execution_feature_snapshot import persist_execution_snapshot

                gcs_retry, local_retry = persist_execution_snapshot(prior_payload)
                if gcs_retry or local_retry:
                    if is_20p_retry:
                        mark_persisted_20p(tid_early)
                    elif is_new_lifecycle_purpose(purpose_s):
                        from modules.po_event_seal import mark_persisted_event

                        mark_persisted_event(purpose_s, mid_identity)
                    else:
                        mark_persisted_exit(tid_early)
                    return True
                return False

            snap_date = datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
            sym = resolve_underlying_symbol(
                symbol=symbol,
                trade_id=trade_id,
                position=position,
            )
            meta: Dict[str, Any] = {}
            if position is not None and isinstance(getattr(position, "metadata", None), dict):
                meta = dict(position.metadata or {})
            sig_snap = meta.get("signal_snapshot") if isinstance(meta.get("signal_snapshot"), dict) else {}
            exec_pl = meta.get("execution_payload") if isinstance(meta.get("execution_payload"), dict) else {}
            opt_side = (
                option_side
                or sig_snap.get("option_side")
                or sig_snap.get("option_type")
                or getattr(position, "option_side", None)
                or meta.get("option_side")
            )
            exit_time_iso = datetime.now(timezone.utc).isoformat()
            entry_time_raw = (
                sig_snap.get("entry_time")
                or meta.get("entry_time")
                or getattr(position, "entry_time", None)
            )
            if hasattr(entry_time_raw, "isoformat"):
                entry_time_iso = entry_time_raw.isoformat()
            else:
                entry_time_iso = str(entry_time_raw) if entry_time_raw else None
            row: Dict[str, Any] = {
                "symbol": sym,
                "trade_id": str(trade_id or ""),
                "option_side": opt_side,
                "option_type": opt_side,
                "side": "LONG",
                "stage": str(stage or ""),
                "po_snapshot_stage": str(stage or ""),
                "po_trajectory_mode": "live",
                "setup_type": (exec_pl.get("meta") or {}).get("setup_type") if isinstance(exec_pl.get("meta"), dict) else meta.get("setup_type"),
                "current_price": exit_underlying or sig_snap.get("entry_underlying_price"),
                "close": exit_underlying or sig_snap.get("entry_underlying_price"),
                "entry_premium": entry_premium or sig_snap.get("entry_premium_per_contract") or getattr(position, "entry_price", None),
                "exit_premium": exit_premium,
                "exit_reason": exit_reason,
                "realized_pnl": realized_pnl,
                "realized_pnl_pct": realized_pnl_pct,
                "delta_at_entry": sig_snap.get("delta_at_entry") or meta.get("delta_at_entry"),
                "strike": sig_snap.get("strike"),
                "metadata": {
                    **meta,
                    "exit_reason": exit_reason,
                    "exit_premium": exit_premium,
                    "realized_pnl_pct": realized_pnl_pct,
                    "signal_snapshot": sig_snap,
                },
            }
            is_20p = purpose_s == "20p"
            is_mid = is_mid_trade_purpose(purpose_s)
            if is_mid:
                row.pop("exit_premium", None)
                row.pop("exit_reason", None)
                row.pop("realized_pnl", None)
                row.pop("realized_pnl_pct", None)
                row["event"] = tech_asof_for_purpose(purpose_s)
                if isinstance(extra, dict):
                    for _k in (
                        "mfe_pct",
                        "hwm_pnl_pct",
                        "peak_pct",
                        "current_pnl_pct",
                        "snapshot_premium",
                        "save20",
                        "save20_decision",
                        "save20_hold_reason",
                        "pve3",
                        "pve3_limit",
                        "pve3_bar_at",
                        "pve3_age_s",
                        "tech_age_s",
                        "hub_read_id",
                        "refreshed_sync",
                        "refresh_skip",
                        "arm_source",
                    ):
                        if extra.get(_k) is not None:
                            row[_k] = extra.get(_k)
                    if isinstance(extra.get("lifecycle_event"), dict):
                        row["lifecycle_event"] = dict(extra["lifecycle_event"])
                    try:
                        from modules.po_snapshot_hydration import fill_missing_tp_keys_from_osnap

                        fill_missing_tp_keys_from_osnap(row, extra)
                    except Exception:
                        pass
            else:
                stamp_exit_outcome_fields(
                    row,
                    exit_reason=exit_reason,
                    exit_premium=exit_premium,
                    entry_premium=row.get("entry_premium"),
                    realized_pnl=realized_pnl,
                    realized_pnl_pct=realized_pnl_pct,
                    exit_time=exit_time_iso,
                    entry_time=entry_time_iso,
                    live_underlying=exit_underlying or row.get("current_price"),
                    entry_underlying=(
                        sig_snap.get("entry_underlying_price")
                        or sig_snap.get("entry_underlying")
                        or exec_pl.get("entry_underlying_price")
                    ),
                    exit_underlying=exit_underlying,
                    option_side=opt_side,
                )
            if is_mid:
                clock_key = event_clock_key(purpose_s)
                event_at = None
                if isinstance(extra, dict):
                    event_at = extra.get(clock_key) or extra.get("po_snapshot_event_at")
                if event_at in (None, ""):
                    event_at = row.get(clock_key)
                if event_at in (None, ""):
                    log.warning(
                        "PO_%s_SNAPSHOT | missing_event_clock | trade_id=%s | "
                        "not inventing a write-time clock",
                        purpose_s.upper(),
                        trade_id,
                    )
                    return False
                row[clock_key] = event_at
                row["po_tech_asof"] = tech_asof_for_purpose(purpose_s)
            else:
                event_at = exit_time_iso
                row["po_exit_event_at"] = event_at
                row["po_tech_asof"] = "exit"
                from modules.po_event_seal import remember_exit_clock

                remember_exit_clock(event_at)
                if position is not None and isinstance(getattr(position, "metadata", None), dict):
                    if position.metadata.get("po_exit_event_at") in (None, ""):
                        position.metadata["po_exit_event_at"] = event_at
            row["po_snapshot_event_at"] = event_at
            row["po_tech_asof_at"] = event_at
            # 1) Carry ORB 7:30 entry features (TA + OI-PC + funnel) when available
            entry_cache = getattr(self, "_orb_0dte_po_entry_features_by_symbol", None) or {}
            if isinstance(entry_cache, dict) and sym:
                carry_forward_entry_features(
                    row,
                    _select_entry_cache_row(
                        _entry_cache_rows(entry_cache, sym),
                        trade_id=str(trade_id or ""),
                        freeze_at="",
                        option_side=str(opt_side or ""),
                    ),
                )
            # Entry carry may bring decision-freeze / skip-restamp — exit must restamp live.
            row["stage"] = str(stage or row.get("stage") or "")
            row["po_snapshot_stage"] = str(stage or row.get("po_snapshot_stage") or "")
            row["po_trajectory_mode"] = "live"
            row.pop("po_skip_trajectory_restamp", None)
            # 1b) Carry Trendline execute-time hydrated features (prefer trade_id, then symbol)
            tid_key = str(trade_id or "").strip()
            tl_by_tid = getattr(self, "_trendline_po_entry_features_by_trade_id", None) or {}
            tl_entry = None
            if isinstance(tl_by_tid, dict) and tid_key:
                tl_entry = tl_by_tid.get(tid_key)
            if tl_entry is None:
                tl_cache = getattr(self, "_trendline_po_entry_features_by_symbol", None) or {}
                if isinstance(tl_cache, dict) and sym:
                    tl_entry = _select_entry_cache_row(
                        _entry_cache_rows(tl_cache, sym),
                        trade_id=tid_key,
                        freeze_at="",
                        option_side=str(opt_side or ""),
                    )
            if tl_entry:
                carry_forward_entry_features(row, tl_entry)
            # 1c) Carry Breakout execute-time hydrated features
            bo_cache = getattr(self, "_breakout_po_entry_features_by_symbol", None) or {}
            if isinstance(bo_cache, dict) and sym:
                carry_forward_entry_features(
                    row,
                    _select_entry_cache_row(
                        _entry_cache_rows(bo_cache, sym),
                        trade_id=str(trade_id or ""),
                        freeze_at="",
                        option_side=str(opt_side or ""),
                    ),
                )
            try:
                from modules.po_snapshot_hydration import stamp_entry_mode

                stamp_entry_mode(row, path=str(snapshot_strategy or ""), source=sig_snap)
            except Exception:
                pass
            # Re-assert live exit mode after all entry carries.
            row["stage"] = str(stage or row.get("stage") or "")
            row["po_snapshot_stage"] = str(stage or row.get("po_snapshot_stage") or "")
            row["po_trajectory_mode"] = "live"
            row.pop("po_skip_trajectory_restamp", None)
            if row.get("po_decision_freeze_at") in (None, ""):
                freeze_src = tl_entry if isinstance(tl_entry, dict) else None
                if (
                    not isinstance(freeze_src, dict)
                    or freeze_src.get("po_decision_freeze_at") in (None, "")
                ):
                    for cache_name in (
                        "_orb_0dte_po_entry_features_by_symbol",
                        "_breakout_po_entry_features_by_symbol",
                        "_trendline_po_entry_features_by_symbol",
                    ):
                        picked = _select_entry_cache_row(
                            _entry_cache_rows(getattr(self, cache_name, None), sym),
                            trade_id=str(trade_id or ""),
                            freeze_at="",
                            option_side=str(opt_side or ""),
                        ) if sym else None
                        if (
                            isinstance(picked, dict)
                            and picked.get("po_decision_freeze_at") not in (None, "")
                        ):
                            freeze_src = picked
                            break
                if (
                    isinstance(freeze_src, dict)
                    and freeze_src.get("po_decision_freeze_at") not in (None, "")
                ):
                    row["po_decision_freeze_at"] = freeze_src.get("po_decision_freeze_at")
                    row["po_decision_freeze_mode"] = (
                        freeze_src.get("po_decision_freeze_mode") or "closed_bar"
                    )
            if is_20p:
                row["po_20p_seal_status"] = "persisted"
            # ORB1 / BO exits must not inherit trendline_slot from same-symbol TL
            # entry cache carry-forward (only Trendline snaps keep real slots).
            if "trendline" not in str(snapshot_strategy or "").lower():
                row["trendline_slot"] = None
                _exit_md = row.get("metadata")
                if isinstance(_exit_md, dict):
                    _exit_md["trendline_slot"] = None
            # 2) Trendline: join same-day break snapshot (structure + options + TA)
            break_joined = False
            if "trendline" in str(snapshot_strategy or "").lower() and sym:
                try:
                    from .trendline_break_snapshot import get_trendline_break_snapshot_collector

                    collector = getattr(self, "_trendline_break_snapshot_collector", None)
                    if collector is None:
                        collector = get_trendline_break_snapshot_collector()
                        self._trendline_break_snapshot_collector = collector
                    tl_slot_exit = (
                        sig_snap.get("trendline_slot")
                        or meta.get("trendline_slot")
                        or (
                            (exec_pl.get("meta") or {}).get("trendline_slot")
                            if isinstance(exec_pl.get("meta"), dict)
                            else None
                        )
                    )
                    break_tid = sig_snap.get("break_trade_id") or meta.get("break_trade_id") or None
                    break_row = collector.find_break_for_symbol(
                        sym,
                        date_key=snap_date,
                        trendline_slot=tl_slot_exit,
                        trade_id=break_tid,
                    )
                    if break_row:
                        carry_forward_entry_features(row, features_from_break_record(break_row))
                        break_joined = True
                        if break_row.get("trendline_slot") and row.get("trendline_slot") is None:
                            row["trendline_slot"] = break_row.get("trendline_slot")
                except Exception as break_exc:
                    log.debug("Exit snapshot break join skipped for %s: %s", sym, break_exc)
            # 3) Thin signal_snapshot / exec meta fill gaps
            for k in (
                "rsi", "vwap_distance_pct", "macd", "macd_signal", "macd_histogram",
                "sma_20", "sma_50", "ema_12", "ema_26", "momentum_10", "atr",
                "volume_ratio", "orb_volume_ratio", "vwap", "rs_vs_spy",
                "priority_score", "simple_quality_score", "break_distance_pct", "vwap_position",
                "orb_high", "orb_low", "orb_range_pct", "direction", "trigger_direction",
                "velocity_pct", "setup_type", "score", "put_call_oi_ratio",
                "call_chain_open_interest", "put_chain_open_interest",
                "quality_gate_report", "otm_pct", "chosen_delta", "fill_delta",
                "delta_at_entry", "representative_delta", "implied_volatility", "iv",
                "continuation_pct", "follow_through_bars", "quality_score",
                "bollinger_upper", "bollinger_middle", "bollinger_lower",
                "bollinger_width", "bollinger_position",
                "call_chain_volume", "put_chain_volume", "call_volume", "put_volume",
                "chain_observed_at",
                "direction_volume_pct", "hold_exit_dir_pct", "cumulative_volume",
            ):
                if row.get(k) is None and sig_snap.get(k) is not None:
                    row[k] = sig_snap.get(k)
                if row.get(k) is None and meta.get(k) is not None:
                    row[k] = meta.get(k)
                em = exec_pl.get("meta") if isinstance(exec_pl.get("meta"), dict) else {}
                if row.get(k) is None and em.get(k) is not None:
                    row[k] = em.get(k)
                # Common entry-contract aliases on TL/BO payloads
                if row.get(k) is None and k == "fill_delta":
                    for alt in ("delta_at_entry", "entry_delta", "selected_delta"):
                        if sig_snap.get(alt) is not None:
                            row[k] = sig_snap.get(alt)
                            break
                        if meta.get(alt) is not None:
                            row[k] = meta.get(alt)
                            break
                        if em.get(alt) is not None:
                            row[k] = em.get(alt)
                            break
            # Preserve exit spot after carry-forward (entry close must not win)
            if exit_underlying:
                row["current_price"] = exit_underlying
                row["close"] = exit_underlying
                row["underlying_price"] = exit_underlying
                if not is_mid:
                    row["exit_underlying"] = exit_underlying
            # 4) Live comprehensive TA (SMA/MACD/RSI) — ORB-parity hydrate (forced on exit)
            if not isinstance(frozen_tech, dict) or not frozen_tech:
                try:
                    live_md = (
                        position.metadata
                        if position is not None and isinstance(getattr(position, "metadata", None), dict)
                        else None
                    )
                    osnap = live_md.get("option_stealth") if isinstance(live_md, dict) else None
                    if isinstance(osnap, dict):
                        frozen_tech = osnap.get(f"po_{purpose_s}_tech") or osnap.get("po_20p_tech")
                    if not isinstance(frozen_tech, dict) or not frozen_tech:
                        from modules.session_tech_indicators import read_session_tech_at

                        from modules.po_lifecycle_moments import (
                            SESSION_TECH_EVENT_MAX_AGE_S,
                            session_tech_may_refresh,
                        )

                        frozen_tech = read_session_tech_at(
                            self,
                            str(sym or symbol or ""),
                            now_utc=event_at or exit_time_iso,
                            max_age_s=SESSION_TECH_EVENT_MAX_AGE_S,
                            allow_sync_refresh=session_tech_may_refresh(
                                "exit" if not is_mid else purpose_s
                            ),
                            refresh_timeout_s=3.0,
                        )
                        if isinstance(osnap, dict) and isinstance(frozen_tech, dict) and frozen_tech:
                            osnap[f"po_{purpose_s}_tech"] = frozen_tech
                except Exception:
                    pass
            if sym:
                self._hydrate_po_snapshot_row_with_market_data(
                    row,
                    symbol=sym,
                    snapshot_strategy=snapshot_strategy,
                    force=True,
                    live_moment=True,
                    frozen_tech=frozen_tech,
                )
            # Re-stamp exit outcomes after hydrate (do not let entry carry overwrite)
            entry_und_final = (
                row.get("entry_underlying")
                or row.get("entry_underlying_price")
                or sig_snap.get("entry_underlying_price")
                or sig_snap.get("entry_underlying")
                or exec_pl.get("entry_underlying_price")
            )
            if is_mid:
                try:
                    from .underlying_spot_stamp import stamp_underlying_research_fields

                    stamp_underlying_research_fields(
                        row,
                        live_underlying=exit_underlying or row.get("underlying_price") or row.get("current_price"),
                        entry_underlying=entry_und_final,
                        option_side=opt_side,
                        at_exit=False,
                        source=tech_asof_for_purpose(purpose_s),
                    )
                except Exception:
                    pass
            else:
                stamp_exit_outcome_fields(
                    row,
                    exit_reason=exit_reason,
                    exit_premium=exit_premium,
                    entry_premium=row.get("entry_premium"),
                    realized_pnl=realized_pnl,
                    realized_pnl_pct=realized_pnl_pct,
                    exit_time=exit_time_iso,
                    entry_time=entry_time_iso,
                    live_underlying=exit_underlying or row.get("underlying_price") or row.get("current_price"),
                    entry_underlying=entry_und_final,
                    exit_underlying=exit_underlying or row.get("underlying_price"),
                    option_side=opt_side,
                )
            # 5) Fresh chain at this snapshot. Drop carried contract greeks first
            # so a handoff delta cannot remain on a +20% or exit file.
            for _gk in (
                "delta",
                "gamma",
                "theta",
                "vega",
                "iv",
                "implied_volatility",
                "representative_delta",
            ):
                row[_gk] = None
            opts_count = 0
            opts_status = "failed"
            if sym:
                try:
                    # Prefer owned strike so representative_* / greeks are the traded contract
                    # at exit (Hold research), not only entry-carry or ATM viability proxy.
                    owned_strike = row.get("strike")
                    if owned_strike is None and position is not None:
                        owned_strike = getattr(position, "strike", None) or (
                            (getattr(position, "metadata", None) or {}).get("strike")
                            if isinstance(getattr(position, "metadata", None), dict)
                            else None
                        )
                    osnap_live = (
                        meta.get("option_stealth")
                        if isinstance(meta.get("option_stealth"), dict)
                        else {}
                    )
                    refresh_n = int(osnap_live.get("po_chain_refresh_n") or 0)
                    from modules.po_lifecycle_moments import (
                        CHAIN_REFRESH_CAP,
                        chain_should_live_refresh,
                    )

                    refresh_cap = CHAIN_REFRESH_CAP
                    force_chain = chain_should_live_refresh(
                        purpose_s if is_mid else "exit",
                        refresh_n,
                        refresh_cap,
                        row=row,
                        osnap=osnap_live,
                    )
                    if force_chain and osnap_live is not None and purpose_s != "exit":
                        osnap_live["po_chain_refresh_n"] = refresh_n + 1
                        if position is not None and isinstance(getattr(position, "metadata", None), dict):
                            stealth = position.metadata.get("option_stealth")
                            if isinstance(stealth, dict):
                                stealth["po_chain_refresh_n"] = osnap_live["po_chain_refresh_n"]
                    opts = None
                    if force_chain:
                        from modules.po_session_tech_tick import pace_eod_exit_chain

                        pace_eod_exit_chain(self)
                        opts = self._fetch_options_features_for_trendline_signal(
                            type(
                                "S",
                                (),
                                {
                                    "symbol": sym,
                                    "option_side": row.get("option_side"),
                                    "side": "LONG",
                                    "strike": owned_strike,
                                    "metadata": {
                                        **(row.get("metadata") if isinstance(row.get("metadata"), dict) else {}),
                                        "strike": owned_strike,
                                    },
                                    "current_price": row.get("current_price"),
                                },
                            )(),
                            order_payload={
                                "symbol": sym,
                                "option_side": row.get("option_side"),
                                "entry_underlying_price": row.get("current_price"),
                                "strike": owned_strike,
                                "delta_at_entry": row.get("delta_at_entry"),
                            },
                            force=True,
                            seed_row=row,
                        )
                        opts_status = str(
                            (opts or {}).get("po_options_enrichment_status") or "failed"
                        )
                        opts_count = overlay_options_features(row, opts)
                    else:
                        opts_status = "skipped_no_live_chain"
                        opts_count = 0
                        if row.get("po_chain_source") in (None, ""):
                            row["po_chain_source"] = "entry_carry"
                    try:
                        from modules.options_snapshot_fields import stamp_live_owned_greeks

                        for _gk in (
                            "delta",
                            "gamma",
                            "theta",
                            "vega",
                            "iv",
                            "implied_volatility",
                            "representative_delta",
                        ):
                            row[_gk] = None
                        exact_q = None
                        if isinstance(meta, dict):
                            cand = meta.get("exact_quote") or meta.get("last_exact_quote")
                            if isinstance(cand, dict):
                                exact_q = cand
                        if exact_q is None and isinstance(extra, dict):
                            cand = extra.get("exact_quote")
                            if isinstance(cand, dict):
                                exact_q = cand
                        stamp_live_owned_greeks(row, exact_q, overwrite=True)
                    except Exception:
                        pass
                    if owned_strike is not None and row.get("po_options_owned_strike") is None:
                        try:
                            row["po_options_owned_strike"] = float(owned_strike)
                        except (TypeError, ValueError):
                            pass
                except Exception as opts_exc:
                    log.debug("Exit options enrich skipped for %s: %s", sym, opts_exc)
                    opts_status = "failed"
            # Desk alias parity (iv/δ/hold_exit_dir) after fresh chain + entry carry
            try:
                _osnap_alias = meta.get("option_stealth") if isinstance(meta.get("option_stealth"), dict) else {}
                _diag_alias = (
                    _osnap_alias.get("exit_diagnostics")
                    if isinstance(_osnap_alias.get("exit_diagnostics"), dict)
                    else {}
                )
                ensure_po_desk_aliases(
                    row,
                    stealth_osnap=_osnap_alias,
                    exit_diagnostics=_diag_alias,
                )
                # Desk aliases may restore prior-event IV from stealth. OCC
                # on this tick is the only live greek source after entry.
                for _gk in (
                    "delta",
                    "gamma",
                    "theta",
                    "vega",
                    "iv",
                    "implied_volatility",
                    "representative_delta",
                ):
                    row[_gk] = None
                exact_q = None
                if isinstance(meta, dict):
                    cand = meta.get("exact_quote") or meta.get("last_exact_quote")
                    if isinstance(cand, dict):
                        exact_q = cand
                if exact_q is None and isinstance(extra, dict):
                    cand = extra.get("exact_quote")
                    if isinstance(cand, dict):
                        exact_q = cand
                from modules.options_snapshot_fields import stamp_live_owned_greeks

                stamp_live_owned_greeks(row, exact_q, overwrite=True)
            except Exception:
                pass
            if not is_mid and row.get("hold_exit_dir_pct") in (None, ""):
                from modules.po_snapshot_hydration import _exit_chain_share

                share = _exit_chain_share(row, opt_side)
                if share is not None:
                    row["hold_exit_dir_pct"] = share
                    row["hold_exit_dir_source"] = "exit_chain_share"
            xtra = {
                "event": tech_asof_for_purpose(purpose_s) if is_mid else "exit",
                "trade_id": mid_identity if is_new_lifecycle_purpose(purpose_s) else str(trade_id or ""),
                "entry_premium": row.get("entry_premium"),
            }
            if not is_mid:
                xtra.update({
                    "exit_reason": exit_reason,
                    "realized_pnl": realized_pnl,
                    "realized_pnl_pct": realized_pnl_pct,
                    "exit_premium": exit_premium,
                    "exit_time": exit_time_iso,
                })
            xtra.update({
                "symbol": sym,
                "break_snapshot_joined": break_joined,
                "options_fields_overlaid": int(opts_count),
                "po_options_enrichment_status": opts_status,
                "po_options_owned_strike": row.get("po_options_owned_strike") or row.get("strike"),
                "po_market_enrichment_status": row.get("po_market_enrichment_status"),
                "critical_keys_populated": count_critical_populated(row),
                "po_tech_asof": tech_asof_for_purpose(purpose_s) if is_mid else "exit",
                "po_tech_asof_at": event_at if is_new_lifecycle_purpose(purpose_s) else exit_time_iso,
            })
            row["po_tech_asof"] = tech_asof_for_purpose(purpose_s) if is_mid else "exit"
            row["po_tech_asof_at"] = event_at if is_new_lifecycle_purpose(purpose_s) else exit_time_iso
            if extra:
                xtra.update(extra)
            if not is_mid:
                # Pullback ladder: flag a trade whose bid high reached the arm level
                # but fired no shot before the exit tick.
                try:
                    from modules.po_lifecycle_snapshots import pullback_exit_fields

                    xtra.update(pullback_exit_fields(meta.get("option_stealth")))
                except Exception:
                    pass
            # Force CALL/PUT side from option_type / trade_id — never leave stale
            # option_type_label=PUT on CALL exits (Jul 28 OKLO/IBM).
            try:
                from .options_snapshot_fields import canonicalize_option_side_label

                _side = canonicalize_option_side_label(row)
                row["side"] = _side
                row["direction"] = _side
                row["option_type"] = _side.lower()
                row["option_type_label"] = _side
                row["expected_option_side"] = _side.lower()
                row["option_side"] = _side.lower()
            except Exception:
                pass
            # Exit-time chain trajectory (now + ~5m ago / Δ): after side canonicalize
            # so same-side dir%/volume keys match CALL vs PUT. Always restamp live.
            try:
                from modules.po_observe_trajectory import (
                    record_chain_skew_sample,
                    stamp_po_observe_trajectory,
                )

                row["po_snapshot_stage"] = str(stage or row.get("po_snapshot_stage") or "")
                row["po_trajectory_mode"] = "live"
                row.pop("po_skip_trajectory_restamp", None)
                record_chain_skew_sample(self, sym, row)
                stamp_po_observe_trajectory(
                    self,
                    row,
                    symbol=sym,
                    overwrite=True,
                    decision_freeze=False,
                )
                try:
                    from modules.po_lifecycle_snapshots import (
                        record_owned_contract_sample,
                        stamp_owned_contract_deltas,
                    )

                    _osnap_owned = (
                        meta.get("option_stealth")
                        if isinstance(meta.get("option_stealth"), dict)
                        else {}
                    )
                    exact_owned = None
                    if isinstance(meta, dict):
                        cand = meta.get("exact_quote") or meta.get("last_exact_quote")
                        if isinstance(cand, dict):
                            exact_owned = cand
                    record_owned_contract_sample(_osnap_owned, exact_owned, meta)
                    stamp_owned_contract_deltas(row, _osnap_owned)
                except Exception:
                    pass
            except Exception:
                pass
            try:
                from modules.po_snapshot_hydration import stamp_tp_moment_cache

                stamp_tp_moment_cache(
                    row,
                    self,
                    meta.get("option_stealth") if isinstance(meta.get("option_stealth"), dict) else None,
                )
            except Exception:
                pass
            try:
                from modules.exit_observation_telemetry import build_po_exit_observation_stamp

                osnap_src = None
                if isinstance(meta.get("option_stealth"), dict):
                    osnap_src = meta.get("option_stealth")
                diag_src = None
                if isinstance(osnap_src, dict) and isinstance(osnap_src.get("exit_diagnostics"), dict):
                    diag_src = osnap_src.get("exit_diagnostics")
                obs_premium = exit_premium
                if is_mid:
                    obs_premium = row.get("snapshot_premium")
                    if obs_premium is None and isinstance(extra, dict):
                        obs_premium = extra.get("snapshot_premium")
                stamp = build_po_exit_observation_stamp(
                    position=position,
                    osnap=osnap_src,
                    exit_diagnostics=diag_src,
                    exit_premium=obs_premium,
                    exit_reason=None if is_mid else exit_reason,
                )
                # Durability: keep stamp on position metadata through reconcile / ghost quarantine.
                # A mid-trade snap must not mark the open trade as exited.
                if (
                    not is_mid
                    and position is not None
                    and isinstance(getattr(position, "metadata", None), dict)
                ):
                    position.metadata["po_exit_observation"] = dict(stamp)
                    meta["po_exit_observation"] = dict(stamp)
                for k, v in stamp.items():
                    if v is None:
                        continue
                    xtra.setdefault(k, v)
                    if k in (
                        "hwm_bid",
                        "hwm_pnl_pct",
                        "peak_pct",
                        "peak_price",
                        "giveback_floor_bid",
                        "exit_decision_bid",
                        "exit_fill_bid",
                        "decision_to_fill_slippage",
                        "gap_guard_state",
                        "quote_continuity_status",
                        "armed_at",
                        "hwm_timestamp",
                        "hold_exit_dir_pct",
                        "direction_volume_pct",
                        "cumulative_volume",
                    ):
                        row[k] = v
                xtra["po_exit_observation"] = {
                    k: v for k, v in stamp.items() if v is not None
                }
                try:
                    ensure_po_desk_aliases(
                        row,
                        stealth_osnap=osnap_src if isinstance(osnap_src, dict) else None,
                        exit_diagnostics=diag_src if isinstance(diag_src, dict) else None,
                    )
                    # Keep desk aliases on extra for moon/hold matrices
                    for _alias_k in (
                        "iv",
                        "implied_volatility",
                        "hold_exit_dir_pct",
                        "fill_delta",
                        "chosen_delta",
                        "representative_delta",
                        "delta",
                        "cumulative_volume",
                        "atr",
                        "bollinger_position",
                        "call_chain_volume",
                        "put_chain_volume",
                        "representative_volume",
                        "direction_volume_pct",
                        "direction_volume_pct_5m_ago",
                        "same_side_chain_volume",
                        "same_side_chain_volume_5m_ago",
                        "dir_pct_change_5m",
                        "call_chain_volume_delta_5m",
                        "put_chain_volume_delta_5m",
                        "same_side_chain_volume_delta_5m",
                        "opposite_side_chain_volume_delta_5m",
                        "owned_contract_volume_delta_5m",
                        "owned_contract_oi_change",
                    ):
                        if row.get(_alias_k) is not None:
                            xtra.setdefault(_alias_k, row.get(_alias_k))
                    xtra["critical_keys_populated"] = count_critical_populated(row)
                except Exception:
                    pass
            except Exception as stamp_exc:
                log.debug("PO exit observation stamp skipped: %s", stamp_exc)
            try:
                from modules.po_decision_hydrate import (
                    attach_cached_source_clocks,
                    stamp_provenance_clocks,
                )

                if row.get("chain_observed_at") in (None, "") and (
                    opts_count > 0
                    or row.get("call_chain_volume") is not None
                    or row.get("put_chain_volume") is not None
                ):
                    row["chain_observed_at"] = exit_time_iso
                attach_cached_source_clocks(row, system=self, symbol=symbol)
                stamp_provenance_clocks(row, event_at=row.get("po_snapshot_event_at"))
                if is_new_lifecycle_purpose(purpose_s):
                    from modules.po_lifecycle_snapshots import stamp_lifecycle_row

                    stamp_lifecycle_row(row, extra)
            except Exception:
                pass
            payload = self._save_execution_feature_snapshot(
                stage=stage,
                signals_full_pool=[row],
                signals_selected_for_execution=[row],
                date_key=snap_date,
                portfolio_red_day_pattern=bool(getattr(self, "_red_day_filter_blocked", False)),
                total_scanned=1,
                snapshot_strategy=snapshot_strategy,
                extra=xtra,
                feature_extractor=lambda s: extract_0dte_unified_features(
                    s,
                    snapshot_strategy=snapshot_strategy,
                    include_nulls=True,
                    include_exit_fields=True,
                ),
            )
            if isinstance(payload, dict):
                if is_20p:
                    remember_20p(str(trade_id or ""), payload)
                    if getattr(self, "_po_last_persist_ok", False):
                        mark_persisted_20p(str(trade_id or ""))
                elif is_new_lifecycle_purpose(purpose_s):
                    from modules.po_event_seal import mark_persisted_event, remember_event

                    remember_event(purpose_s, mid_identity, payload)
                    if getattr(self, "_po_last_persist_ok", False):
                        mark_persisted_event(purpose_s, mid_identity)
                else:
                    remember_exit(str(trade_id or ""), payload)
                    if getattr(self, "_po_last_persist_ok", False):
                        mark_persisted_exit(str(trade_id or ""))
            return bool(getattr(self, "_po_last_persist_ok", False)) and isinstance(payload, dict)
        except Exception as exc:
            log.warning(
                "EXECUTION_FEATURE_SNAPSHOT | stage=%s | exit_snapshot_error=%s | trade_id=%s",
                stage,
                exc,
                trade_id,
            )
            return False

    def _save_orb_0dte_exit_priority_snapshot(self, **kwargs: Any) -> None:
        kwargs.setdefault("stage", "orb_0dte_options_exited")
        kwargs.setdefault("snapshot_strategy", "easy_orb_0dte")
        self._save_0dte_exit_priority_snapshot(**kwargs)
        try:
            from modules.orb1_0dte_watch import arm_orb1_side_cooldown_after_exit

            arm_orb1_side_cooldown_after_exit(
                self,
                symbol=str(kwargs.get("symbol") or ""),
                option_side=str(kwargs.get("option_side") or "") or None,
            )
        except Exception as orb1_cd_err:
            log.debug(
                "ORB1_0DTE_WATCH | stage=post_exit_cooldown | action=skip | error=%s",
                orb1_cd_err,
            )

    def _save_orb_0dte_execution_priority_snapshot(
        self,
        *,
        signal: Any,
        stage: str,
        order_payload: Optional[Dict[str, Any]] = None,
        broker_success: Optional[bool] = None,
        broker_error: Optional[str] = None,
        candidate: Optional[Any] = None,
        position: Optional[Any] = None,
    ) -> None:
        """Persist ORB1 Breakout handoff/fill Priority Snapshot (watch-mode + batch parity)."""
        if skip_failed_fill_executed_snapshot(
            stage,
            broker_success,
            symbol=getattr(signal, "symbol", ""),
            broker_error=broker_error,
        ):
            return
        try:
            from zoneinfo import ZoneInfo

            from .execution_feature_snapshot import extract_orb_0dte_pipeline_features

            snap_date = datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
            pl = dict(order_payload or {})
            orb = getattr(signal, "orb_signal", None) if signal is not None else None
            row: Dict[str, Any] = dict(orb) if isinstance(orb, dict) else {}
            sym = str(
                getattr(signal, "symbol", None)
                or row.get("symbol")
                or getattr(candidate, "symbol", None)
                or ""
            ).upper()
            row["symbol"] = sym
            if getattr(signal, "direction", None) is not None:
                row["direction"] = getattr(signal, "direction")
            if getattr(signal, "priority_score", None) is not None:
                row["priority_score"] = getattr(signal, "priority_score")
            if getattr(signal, "priority_rank", None) is not None:
                row["priority_rank"] = getattr(signal, "priority_rank")
            if getattr(signal, "option_type", None) or getattr(signal, "option_type_label", None):
                row["option_type"] = getattr(signal, "option_type", None) or getattr(
                    signal, "option_type_label", None
                )
            row["strategy_type"] = "orb_0dte"
            row["source_path"] = "orb_0dte"
            _attach_signal_freeze(row, signal)
            row.setdefault("setup_type", row.get("setup_type") or "orb1_range_breakout")
            if candidate is not None:
                for attr, key in (
                    ("orb_high", "orb_high"),
                    ("orb_low", "orb_low"),
                    ("quality_score", "quality_score"),
                    ("volume_ratio", "volume_ratio"),
                    ("vwap", "vwap"),
                    ("vwap_distance_pct", "vwap_distance_pct"),
                    ("rsi", "rsi"),
                ):
                    val = getattr(candidate, attr, None)
                    if val is not None and row.get(key) is None:
                        row[key] = val
                meta_c = getattr(candidate, "metadata", None)
                if isinstance(meta_c, dict):
                    for k in (
                        "orb_range_pct",
                        "break_distance_pct",
                        "continuation_pct",
                        "follow_through_bars",
                        "direction_volume_pct",
                        "put_call_oi_ratio",
                        "macd_histogram",
                        "representative_call_delta",
                        "representative_put_delta",
                        "representative_call_gamma",
                        "representative_put_gamma",
                    ):
                        if meta_c.get(k) is not None and row.get(k) is None:
                            row[k] = meta_c.get(k)
                try:
                    from modules.po_observe_trajectory import stamp_break_timing_fields

                    stamp_break_timing_fields(
                        row,
                        break_ts=getattr(candidate, "break_ts", None),
                        close_at_break=(
                            getattr(candidate, "material_break_close", None)
                            or getattr(candidate, "break_price", None)
                        ),
                    )
                except Exception:
                    pass
            for k in ("otm_pct", "chosen_delta", "fill_delta", "strike", "quantity", "entry_premium", "bid_size", "ask_size"):
                if pl.get(k) is not None:
                    row[k] = pl.get(k)
            if pl.get("bid_size") is not None and row.get("option_bid_size") is None:
                row["option_bid_size"] = pl.get("bid_size")
            if pl.get("ask_size") is not None and row.get("option_ask_size") is None:
                row["option_ask_size"] = pl.get("ask_size")
            if position is not None:
                for attr, key in (
                    ("strike", "strike"),
                    ("entry_price", "entry_premium"),
                    ("position_id", "trade_id"),
                    ("option_type", "option_type"),
                ):
                    val = getattr(position, attr, None)
                    if val is not None and row.get(key) is None:
                        row[key] = val
                pos_meta = getattr(position, "metadata", None)
                if isinstance(pos_meta, dict):
                    for k in ("bid_size", "ask_size"):
                        if pos_meta.get(k) is not None and row.get(k) is None:
                            row[k] = pos_meta.get(k)
                    if pos_meta.get("bid_size") is not None and row.get("option_bid_size") is None:
                        row["option_bid_size"] = pos_meta.get("bid_size")
                    if pos_meta.get("ask_size") is not None and row.get("option_ask_size") is None:
                        row["option_ask_size"] = pos_meta.get("ask_size")
                    er = pos_meta.get("execution_risk")
                    if isinstance(er, dict):
                        if er.get("bid_size") is not None and row.get("option_bid_size") is None:
                            row["option_bid_size"] = er.get("bid_size")
                        if er.get("ask_size") is not None and row.get("option_ask_size") is None:
                            row["option_ask_size"] = er.get("ask_size")
            fill_tid = (
                pl.get("trade_id")
                or row.get("trade_id")
                or (str(getattr(position, "position_id", "") or "").strip() or None)
            )
            if fill_tid:
                row["trade_id"] = fill_tid
            prepare_po_trajectory = getattr(self, "_prepare_po_trajectory_for_stage", None)
            if callable(prepare_po_trajectory):
                prepare_po_trajectory(
                    row,
                    stage=stage,
                    symbol=sym,
                    path="orb",
                    trade_id=fill_tid,
                )
            reuse_handoff = getattr(self, "_reuse_sealed_entry_handoff", None)
            if callable(reuse_handoff):
                reuse_handoff(
                    row,
                    stage=stage,
                    symbol=sym,
                    path="orb",
                    trade_id=fill_tid,
                )
            self._hydrate_po_snapshot_row_with_market_data(
                row,
                symbol=sym,
                snapshot_strategy="easy_orb_0dte",
                force=True,
            )
            # Contract/chain must land before Layer D stamps. Fetch options first,
            # then re-apply fill δ from the broker position (hydrate must not drop it).
            try:
                options_features = self._fetch_options_features_for_trendline_signal(
                    signal,
                    order_payload=pl,
                    force=True,
                    seed_row=row,
                )
                if options_features:
                    from modules.po_snapshot_hydration import merge_gate_options_features

                    merge_gate_options_features(row, options_features)
                try:
                    from modules.po_observe_trajectory import apply_selected_contract_option_l1

                    apply_selected_contract_option_l1(
                        row,
                        order_payload=pl,
                        prefer_owned=("options_executed" in str(stage or "")),
                    )
                except Exception:
                    pass
                try:
                    from modules.po_observe_trajectory import stamp_po_observe_trajectory

                    stamp_po_observe_trajectory(self, row, symbol=sym)
                except Exception:
                    pass
            except Exception:
                pass
            if "options_executed" not in str(stage or "") and (
                row.get("implied_volatility") is None
                or (row.get("representative_delta") is None and row.get("fill_delta") is None)
            ):
                try:
                    manager = self._options_chain_manager()
                    if manager is not None:
                        from .options_snapshot_fields import enrich_row_with_options_chain_sync

                        enrich_row_with_options_chain_sync(manager, row)
                except Exception:
                    pass
            for k in ("otm_pct", "chosen_delta", "fill_delta", "strike", "quantity", "entry_premium"):
                if pl.get(k) is not None and row.get(k) is None:
                    row[k] = pl.get(k)
            if position is not None:
                pos_delta = getattr(position, "delta", None)
                if pos_delta is not None:
                    if row.get("fill_delta") is None:
                        row["fill_delta"] = pos_delta
                    if row.get("chosen_delta") is None:
                        row["chosen_delta"] = pos_delta
            try:
                from modules.po_snapshot_hydration import stamp_owned_contract_microstructure

                stamp_owned_contract_microstructure(
                    row,
                    position=position,
                    order_payload=pl,
                )
            except Exception:
                pass
            try:
                from easy0DTE.modules.quality_gate_stack import (
                    PATH_ORB,
                    append_research_stamp_and_contract_gates,
                )

                append_research_stamp_and_contract_gates(
                    PATH_ORB,
                    row,
                    prior_report=row.get("quality_gate_report"),
                    stamp_target=row,
                )
            except Exception:
                pass
            meta = row.setdefault("metadata", {})
            if isinstance(meta, dict):
                meta.setdefault("broker_success", broker_success)
                if broker_error:
                    meta["broker_error"] = broker_error
                meta["source_path"] = "orb_0dte"
                meta.setdefault("discovery_mode", "watch")
            try:
                self._cache_po_entry_features(symbol=sym, row=row, path="orb")
            except Exception:
                pass
            watch_n = len(getattr(self, "_orb1_0dte_watch_candidates", None) or {})
            pool_n = watch_n or int(getattr(self, "_dte0_pipeline_collection_count", 0) or 0) or 1
            self._save_execution_feature_snapshot(
                stage=stage,
                signals_full_pool=[row],
                signals_selected_for_execution=[row],
                date_key=snap_date,
                portfolio_red_day_pattern=bool(getattr(self, "_red_day_filter_blocked", False)),
                total_scanned=int(pool_n),
                snapshot_strategy="easy_orb_0dte",
                extra={
                    "strategy_name": "easyORB_0DTE",
                    "source_path": "orb_0dte",
                    "trade_id": fill_tid,
                    "setup_type": row.get("setup_type"),
                    "option_side": row.get("option_type") or row.get("side"),
                    "broker_success": broker_success,
                    "broker_error": broker_error,
                    "discovery_mode": "watch",
                },
                feature_extractor=extract_orb_0dte_pipeline_features,
            )
            self._record_options_priority_snapshot_row(
                row=row,
                executed=bool(broker_success),
                filter_reason=str(broker_error or pl.get("reject_reason") or ""),
            )
        except Exception as exc:
            log.warning(
                "ORB_0DTE_PIPELINE | stage=%s | snapshot_error=%s",
                stage,
                exc,
            )

    def _save_trendline_exit_priority_snapshot(self, **kwargs: Any) -> None:
        kwargs.setdefault("stage", "trendline_options_exited")
        kwargs.setdefault("snapshot_strategy", "easy_trendline_0dte")
        self._save_0dte_exit_priority_snapshot(**kwargs)
        try:
            from modules.path_exec_day_exclude import arm_trendline_post_exit_cooldown

            pos = kwargs.get("position")
            meta = {}
            if pos is not None and isinstance(getattr(pos, "metadata", None), dict):
                meta = dict(pos.metadata or {})
            sig_snap = meta.get("signal_snapshot") if isinstance(meta.get("signal_snapshot"), dict) else {}
            exec_pl = meta.get("execution_payload") if isinstance(meta.get("execution_payload"), dict) else {}
            exec_meta = exec_pl.get("meta") if isinstance(exec_pl.get("meta"), dict) else {}
            slot = (
                sig_snap.get("trendline_slot")
                or meta.get("trendline_slot")
                or exec_meta.get("trendline_slot")
                or kwargs.get("trendline_slot")
            )
            arm_trendline_post_exit_cooldown(
                self,
                str(kwargs.get("symbol") or ""),
                kwargs.get("option_side"),
                slot=slot,
            )
            # Fill already consumed the envelope (EXECUTED). Do not re-arm for a
            # second same-slot/side trade later today. A1/A2 stay frozen.
        except Exception as tl_cd_err:
            log.debug(
                "TRENDLINE_PIPELINE | stage=post_exit_cooldown | action=skip | error=%s",
                tl_cd_err,
            )

    def _save_breakout_exit_priority_snapshot(self, **kwargs: Any) -> None:
        kwargs.setdefault("stage", "breakout_options_exited")
        kwargs.setdefault("snapshot_strategy", "easy_breakout_0dte")
        self._save_0dte_exit_priority_snapshot(**kwargs)
        try:
            from modules.breakout_watch import (
                arm_breakout_side_cooldown_after_exit,
            )

            # Always arm 1h same-side cooldown after any BO exit (profit or loss)
            # and re-arm WAIT_BREAK so the opposite ORB#2 edge can still qualify.
            arm_breakout_side_cooldown_after_exit(
                self,
                symbol=str(kwargs.get("symbol") or ""),
                option_side=str(kwargs.get("option_side") or "") or None,
                reason="post_exit",
            )
        except Exception as reentry_err:
            log.debug(
                "BREAKOUT_PIPELINE | stage=profit_reentry | action=skip | error=%s",
                reentry_err,
            )

    def _save_breakout_execution_priority_snapshot(
        self,
        *,
        signal: Any,
        stage: str,
        order_payload: Optional[Dict[str, Any]] = None,
        broker_success: Optional[bool] = None,
        broker_error: Optional[str] = None,
        candidate: Optional[Any] = None,
    ) -> None:
        """Persist Breakout handoff/fill snapshot for Priority Optimizer (ORB/TL parity)."""
        if skip_failed_fill_executed_snapshot(
            stage,
            broker_success,
            symbol=getattr(signal, "symbol", ""),
            broker_error=broker_error,
        ):
            return
        try:
            from zoneinfo import ZoneInfo

            from .execution_feature_snapshot import extract_breakout_pool_features
            from .trendline_telemetry import build_trendline_execution_snapshot_signal

            snap_date = datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
            pl = dict(order_payload or {})
            # Reuse Trendline row builder — Breakout executes via TL-compatible signal shape.
            breakout_snapshot_signal = build_trendline_execution_snapshot_signal(
                signal,
                candidate=candidate,
                order_payload=pl,
                break_snapshot_row=None,
            )
            meta_sig = getattr(signal, "metadata", None) if signal is not None else None
            if isinstance(meta_sig, dict):
                for k in (
                    "orb_high",
                    "orb_low",
                    "orb_range_pct",
                    "break_distance_pct",
                    "continuation_pct",
                    "follow_through_bars",
                    "quality_score",
                    "volume_ratio",
                    "vwap",
                    "vwap_distance_pct",
                    "vwap_position",
                    "rsi",
                    "put_call_oi_ratio",
                    "setup_type",
                    "trigger_direction",
                    "otm_pct",
                    "chosen_delta",
                    "fill_delta",
                    "otm_research_exception",
                    "velocity_pct",
                    "minutes_from_730",
                    "minutes_from_730_to_break",
                    "tech_source",
                    "volume_ratio_source",
                    "vwap_source",
                    "session_bar_count",
                    "quality_gate_report",
                    "continuation_pct",
                    "follow_through_bars",
                    "bollinger_position",
                    "call_volume",
                    "put_volume",
                    "call_chain_volume",
                    "put_chain_volume",
                    "direction_volume_pct",
                    "representative_call_delta",
                    "representative_put_delta",
                    "representative_call_gamma",
                    "representative_put_gamma",
                    "break_ts",
                    "break_timestamp",
                    "structure_break_ts",
                    "close_at_break",
                    "line_price_at_break",
                ):
                    if meta_sig.get(k) is not None and breakout_snapshot_signal.get(k) is None:
                        breakout_snapshot_signal[k] = meta_sig.get(k)
            # Prefer OTM/δ from fill payload when present (execute stamp).
            for k in ("otm_pct", "chosen_delta", "fill_delta", "otm_research_exception"):
                if pl.get(k) is not None:
                    breakout_snapshot_signal[k] = pl.get(k)
            if candidate is not None:
                for attr, key in (
                    ("orb_high", "orb_high"),
                    ("orb_low", "orb_low"),
                    ("quality_score", "quality_score"),
                    ("volume_ratio", "volume_ratio"),
                    ("vwap", "vwap"),
                    ("vwap_distance_pct", "vwap_distance_pct"),
                    ("rsi", "rsi"),
                ):
                    val = getattr(candidate, attr, None)
                    if val is not None and breakout_snapshot_signal.get(key) is None:
                        breakout_snapshot_signal[key] = val
                try:
                    from modules.po_observe_trajectory import stamp_break_timing_fields

                    stamp_break_timing_fields(
                        breakout_snapshot_signal,
                        break_ts=(
                            getattr(candidate, "break_ts", None)
                            or (meta_sig.get("break_ts") if isinstance(meta_sig, dict) else None)
                            or (meta_sig.get("break_timestamp") if isinstance(meta_sig, dict) else None)
                        ),
                        close_at_break=(
                            getattr(candidate, "material_break_close", None)
                            or getattr(candidate, "break_price", None)
                            or (meta_sig.get("close_at_break") if isinstance(meta_sig, dict) else None)
                        ),
                    )
                except Exception:
                    pass
            breakout_snapshot_signal["strategy_type"] = "breakout_0dte"
            breakout_snapshot_signal["source_path"] = "breakout_0dte"
            # BO is not a Trendline path — never carry filler trendline_slot from the
            # shared TL snapshot builder (normalize defaults empty → trendline1).
            breakout_snapshot_signal["trendline_slot"] = None
            _bo_md = breakout_snapshot_signal.get("metadata")
            if isinstance(_bo_md, dict):
                _bo_md["trendline_slot"] = None
                _bo_md["strategy_type"] = "breakout_0dte"
                _bo_md["strategy_name"] = "easyBreakout_0DTE"
                _bo_md["source_path"] = "breakout_0dte"
            breakout_snapshot_signal["setup_type"] = (
                breakout_snapshot_signal.get("setup_type") or "orb_range_breakout"
            )
            breakout_snapshot_signal["stage"] = stage
            breakout_snapshot_signal["po_snapshot_stage"] = stage
            _attach_signal_freeze(breakout_snapshot_signal, signal)
            self._reuse_sealed_entry_handoff(
                breakout_snapshot_signal,
                stage=stage,
                symbol=str(getattr(signal, "symbol", "") or ""),
                path="breakout",
                trade_id=str(
                    breakout_snapshot_signal.get("trade_id") or pl.get("trade_id") or ""
                )
                or None,
            )
            options_features = self._fetch_options_features_for_trendline_signal(
                signal,
                order_payload=pl,
                force=True,
                seed_row=breakout_snapshot_signal,
            )
            if options_features:
                from modules.po_snapshot_hydration import merge_gate_options_features

                merge_gate_options_features(breakout_snapshot_signal, options_features)
            try:
                from modules.po_observe_trajectory import apply_selected_contract_option_l1

                apply_selected_contract_option_l1(
                    breakout_snapshot_signal,
                    order_payload=pl,
                    prefer_owned=("options_executed" in str(stage or "")),
                )
            except Exception:
                pass
            _bo_sym = str(getattr(signal, "symbol", "") or "").strip().upper()
            self._prepare_po_trajectory_for_stage(
                breakout_snapshot_signal,
                stage=stage,
                symbol=_bo_sym,
                path="breakout",
                trade_id=str(
                    breakout_snapshot_signal.get("trade_id")
                    or pl.get("trade_id")
                    or ""
                ).strip()
                or None,
            )
            self._hydrate_po_snapshot_row_with_market_data(
                breakout_snapshot_signal,
                symbol=_bo_sym,
                snapshot_strategy="easy_breakout_0dte",
                force=True,
            )
            if "options_executed" not in str(stage or "") and (
                breakout_snapshot_signal.get("implied_volatility") is None
                or (
                    breakout_snapshot_signal.get("representative_delta") is None
                    and breakout_snapshot_signal.get("fill_delta") is None
                )
            ):
                try:
                    manager = self._options_chain_manager()
                    if manager is not None:
                        from .options_snapshot_fields import enrich_row_with_options_chain_sync

                        enrich_row_with_options_chain_sync(manager, breakout_snapshot_signal)
                except Exception:
                    pass
            try:
                from modules.po_snapshot_hydration import stamp_owned_contract_microstructure

                stamp_owned_contract_microstructure(
                    breakout_snapshot_signal,
                    order_payload=pl,
                )
            except Exception:
                pass
            try:
                from easy0DTE.modules.quality_gate_stack import (
                    PATH_BO,
                    append_research_stamp_and_contract_gates,
                )

                append_research_stamp_and_contract_gates(
                    PATH_BO,
                    breakout_snapshot_signal,
                    prior_report=breakout_snapshot_signal.get("quality_gate_report"),
                    stamp_target=breakout_snapshot_signal,
                )
            except Exception:
                pass
            try:
                from .underlying_spot_stamp import (
                    resolve_entry_underlying,
                    stamp_underlying_research_fields,
                )

                live_u = (
                    breakout_snapshot_signal.get("underlying_price")
                    or breakout_snapshot_signal.get("current_price")
                    or breakout_snapshot_signal.get("close")
                    or pl.get("entry_underlying_price")
                )
                entry_u = resolve_entry_underlying(
                    row=breakout_snapshot_signal,
                    signal_snapshot=meta_sig if isinstance(meta_sig, dict) else None,
                    order_payload=pl,
                ) or live_u
                stamp_underlying_research_fields(
                    breakout_snapshot_signal,
                    live_underlying=live_u,
                    entry_underlying=entry_u,
                    option_side=getattr(signal, "option_side", None),
                    at_exit=False,
                    source="breakout_execution_snap",
                )
            except Exception:
                pass
            fill_tid = pl.get("trade_id") or (
                (getattr(signal, "metadata", None) or {}).get("trade_id")
                if isinstance(getattr(signal, "metadata", None), dict)
                else None
            )
            if fill_tid:
                breakout_snapshot_signal["trade_id"] = fill_tid
                md_tid = breakout_snapshot_signal.setdefault("metadata", {})
                if isinstance(md_tid, dict):
                    md_tid["trade_id"] = fill_tid
            meta = breakout_snapshot_signal.setdefault("metadata", {})
            if isinstance(meta, dict):
                meta.setdefault("broker_success", broker_success)
                if broker_error:
                    meta["broker_error"] = broker_error
                if pl.get("reject_reason"):
                    meta["reject_reason"] = pl.get("reject_reason")
                meta["source_path"] = "breakout_0dte"
            try:
                self._cache_po_entry_features(
                    symbol=str(getattr(signal, "symbol", "") or ""),
                    row=breakout_snapshot_signal,
                    path="breakout",
                )
            except Exception:
                pass
            watch_n = len(getattr(self, "_breakout_watch_candidates", None) or {})
            _trig = None
            if isinstance(meta_sig, dict):
                _trig = meta_sig.get("trigger_direction")
            self._save_execution_feature_snapshot(
                stage=stage,
                signals_full_pool=[breakout_snapshot_signal],
                signals_selected_for_execution=[breakout_snapshot_signal],
                date_key=snap_date,
                portfolio_red_day_pattern=bool(getattr(self, "_red_day_filter_blocked", False)),
                total_scanned=int(watch_n),
                snapshot_strategy="easy_breakout_0dte",
                extra={
                    "strategy_name": "easyBreakout_0DTE",
                    "source_path": "breakout_0dte",
                    "trade_id": fill_tid,
                    "setup_type": breakout_snapshot_signal.get("setup_type"),
                    "trigger_direction": breakout_snapshot_signal.get("trigger_direction") or _trig,
                    "option_side": getattr(signal, "option_side", None),
                    "broker_success": broker_success,
                    "broker_error": broker_error,
                },
                feature_extractor=extract_breakout_pool_features,
            )
            self._record_options_priority_snapshot_row(
                row=breakout_snapshot_signal,
                executed=bool(broker_success),
                filter_reason=str(broker_error or pl.get("reject_reason") or ""),
            )
        except Exception as exc:
            log.warning(
                "BREAKOUT_PIPELINE | stage=%s | snapshot_error=%s",
                stage,
                exc,
            )

    def _save_breakout_watch_activation_snapshot(self) -> None:
        """Priority Optimizer artifact: Breakout watchlist freeze after ORB Step 5."""
        watch = getattr(self, "_breakout_watch_candidates", None) or {}
        if not watch:
            return
        try:
            from zoneinfo import ZoneInfo

            from .execution_feature_snapshot import extract_breakout_pool_features

            snap_date = datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
            # Ensure session 1m bars before densify so watch snaps are not tech-empty.
            try:
                from modules.session_tech_indicators import ensure_session_tech_bars_sync

                ensure_session_tech_bars_sync(
                    self,
                    [str(getattr(c, "symbol", "") or "").upper() for c in watch.values()],
                    timeout_seconds=12.0,
                )
            except Exception as ens_exc:
                log.debug("BREAKOUT_PIPELINE | watch_activated_bar_ensure | err=%s", ens_exc)

            rows: List[Dict[str, Any]] = []
            watch_event_at = datetime.now(timezone.utc).isoformat()
            for cand in watch.values():
                row: Dict[str, Any] = {
                    "symbol": str(getattr(cand, "symbol", "") or "").upper(),
                    "side": "LONG",
                    "option_side": None,
                    "orb_high": getattr(cand, "orb_high", None),
                    "orb_low": getattr(cand, "orb_low", None),
                    "current_price": getattr(cand, "price_at_seed", None)
                    or getattr(cand, "last_price", None),
                    "volume_ratio": getattr(cand, "volume_ratio", None),
                    "vwap": getattr(cand, "vwap", None),
                    "vwap_distance_pct": getattr(cand, "vwap_distance_pct", None),
                    "rsi": getattr(cand, "rsi", None),
                    "setup_type": "orb_range_breakout",
                    "strategy_type": "breakout_0dte",
                    "source_path": "breakout_0dte",
                    "pipeline_decision_stage": "breakout_watch_active",
                    "po_snapshot_stage": "breakout_watch_activated",
                    "po_snapshot_event_at": watch_event_at,
                    "po_training_fill_eligible": False,
                    "po_fill_pct_scope": "watch_activation",
                    "metadata": dict(getattr(cand, "metadata", None) or {}),
                }
                # Cache hub tech only. Do not pull chain/greeks on a 200-name watch.
                try:
                    self._stamp_session_tech_on_po_row(
                        row,
                        symbol=row.get("symbol"),
                        ensure_bars=False,
                        overwrite_session_authority=True,
                    )
                except Exception as stamp_exc:
                    log.debug(
                        "BREAKOUT_PIPELINE | watch_activated_tech_stamp | symbol=%s | err=%s",
                        row.get("symbol"),
                        stamp_exc,
                    )
                asof = row.get("session_tech_asof_at")
                if asof not in (None, ""):
                    row["po_session_tech_asof_at"] = asof
                try:
                    from .po_snapshot_hydration import ensure_orb_range_pct

                    ensure_orb_range_pct(row)
                except Exception:
                    pass
                rows.append(row)
            self._save_execution_feature_snapshot(
                stage="breakout_watch_activated",
                signals_full_pool=rows,
                signals_selected_for_execution=rows,
                date_key=snap_date,
                portfolio_red_day_pattern=bool(getattr(self, "_red_day_filter_blocked", False)),
                total_scanned=len(rows),
                snapshot_strategy="easy_breakout_0dte",
                extra={
                    "strategy_name": "easyBreakout_0DTE",
                    "source_path": "breakout_0dte",
                    "po_fill_pct_scope": "watch_activation",
                    "note": (
                        "In-range @ 7:30 names skipped by ORB — Breakout watch "
                        "activated. Cache session-tech + ORB box only; not a "
                        "267-key or 27-key training fill. Score "
                        "WATCH_ACTIVATION_SCORE_KEYS."
                    ),
                },
                feature_extractor=extract_breakout_pool_features,
            )
        except Exception as exc:
            log.warning("BREAKOUT_PIPELINE | stage=watch_activated_snapshot | error=%s", exc)

