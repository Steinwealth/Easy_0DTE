"""
Rank ORB breakout signals for the shared 7:30 ORB1 Breakout pipeline.

Replaces the retired ORB SO ETF execution path — enrichment + priority rank +
Convex/Hard Gate/rank/Gate 3c + Signal Collection alert. Gate 3a Portfolio
Protection runs once @ Step 5 on the post-Convex execution queue via
ensure_pp_ready_before_execution (see portfolio_protection_step5).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


async def _enrich_protection_pool_rows(system: Any, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    enriched: List[Dict[str, Any]] = []
    for sig in rows or []:
        row = dict(sig)
        try:
            await system._backfill_so_signal_tech_fields(row)
            system._lightweight_enrich_signal_for_red_day(row)
        except Exception as enrich_err:
            log.debug("ORB_RANK | protection_pool_enrich_failed | %s", enrich_err)
        enriched.append(row)
    return enriched


async def _build_0dte_protection_pool_enriched(system: Any) -> List[Dict[str, Any]]:
    """
    Priority-ranked 0DTE collection for Gate 3a @ Step 5 (CALL + PUT rows).

    Matches the Signal Collection 0DTE list — no ORB SO union and no secondary
    re-check pass.
    """
    from .orb_shared_730_pipeline import build_ranked_dte_collection_list

    pending = [
        dict(s)
        for s in (getattr(system, "_pending_dte_signals", None) or [])
        if isinstance(s, dict)
    ]
    pool = build_ranked_dte_collection_list(pending)

    if len(pool) < 3:
        try:
            from easy0DTE.modules.prime_0dte_strategy_manager import load_0dte_symbols

            existing = {str(s.get("symbol", "")).strip().upper() for s in pool}
            orb_mgr = getattr(system, "orb_strategy_manager", None)
            orb_data = getattr(orb_mgr, "orb_data", None) if orb_mgr else None
            for sym in load_0dte_symbols():
                sym_u = str(sym or "").strip().upper()
                if not sym_u or sym_u in existing:
                    continue
                row: Dict[str, Any] = {"symbol": sym_u, "side": "LONG", "signal_type": "WATCHLIST"}
                if orb_data and sym_u in orb_data:
                    _orb = orb_data[sym_u]
                    row["orb_high"] = getattr(_orb, "orb_high", None)
                    row["orb_low"] = getattr(_orb, "orb_low", None)
                pool.append(row)
                existing.add(sym_u)
                if len(pool) >= 15:
                    break
        except Exception as wl_err:
            log.warning("ORB_RANK | watchlist_protection_pool_fallback_failed | %s", wl_err)

    return await _enrich_protection_pool_rows(system, pool)


def _pending_dte0_signal_to_pp_row(sig: Any) -> Dict[str, Any]:
    """Normalize a post-Convex DTE0Signal (or dict) into a PP pool row."""
    if isinstance(sig, dict):
        row = dict(sig)
    else:
        row = {}
        if hasattr(sig, "to_dict"):
            try:
                row = dict(sig.to_dict())
            except Exception:
                row = {}
    orb_src: Dict[str, Any] = {}
    if isinstance(row.get("orb_signal"), dict):
        orb_src = row["orb_signal"]
    elif hasattr(sig, "orb_signal") and isinstance(getattr(sig, "orb_signal", None), dict):
        orb_src = sig.orb_signal
    for key in (
        "symbol",
        "side",
        "direction",
        "rsi",
        "volume_ratio",
        "vwap_distance_pct",
        "vwap",
        "current_price",
        "price",
        "orb_high",
        "orb_low",
        "orb_range_pct",
        "confidence",
        "rs_vs_spy",
        "priority_score",
        "priority_rank",
    ):
        if row.get(key) is None and orb_src.get(key) is not None:
            row[key] = orb_src.get(key)
    sym = str(row.get("symbol") or getattr(sig, "symbol", "") or "").strip().upper()
    if sym:
        row["symbol"] = sym
    direction = str(
        row.get("direction") or row.get("side") or getattr(sig, "direction", "LONG") or "LONG"
    ).upper()
    row["direction"] = direction
    row["side"] = direction
    return row


async def _build_0dte_execution_protection_pool_enriched(system: Any) -> List[Dict[str, Any]]:
    """
    Post-Convex execution queue for Gate 3a/3b @ Step 5.

    Uses ``_pending_dte0_signals`` after Convex → Hard Gate → rank → Gate 3c.
    """
    pending_exec = list(getattr(system, "_pending_dte0_signals", None) or [])
    rows = [_pending_dte0_signal_to_pp_row(sig) for sig in pending_exec]
    rows.sort(
        key=lambda r: (int(r.get("priority_rank") or 9999), str(r.get("symbol") or "")),
    )
    return await _enrich_protection_pool_rows(system, rows)


async def _apply_portfolio_protection_block(
    system: Any,
    *,
    trigger_id: str,
    reason: str,
    metrics: Dict[str, Any],
    pool_count: int,
) -> None:
    """Set canonical PP flags and send SKIP TRADING Telegram (delegates to PrimeTradingSystem)."""
    if hasattr(system, "_apply_portfolio_protection_gate_block"):
        await system._apply_portfolio_protection_gate_block(
            trigger_id=trigger_id,
            reason=reason,
            metrics=metrics,
            pool_count=pool_count,
            alert_path="informational_730_rank",
        )
        return

    system._red_day_filter_blocked = True
    system._red_day_reason = reason
    system._red_day_metrics = {
        **dict(metrics or {}),
        "portfolio_protection_trigger": trigger_id,
    }
    log.warning(
        "PORTFOLIO PROTECTION — 0DTE POLICY FLAG | trigger=%s | pool=%d | %s",
        trigger_id,
        pool_count,
        reason,
    )
    if hasattr(system, "_send_portfolio_protection_informational_alert"):
        await system._send_portfolio_protection_informational_alert(
            trigger_id=trigger_id,
            reason=reason,
            protection_pool_count=pool_count,
        )


async def _run_gates_3a_3b(
    system: Any,
    protection_pool: List[Dict[str, Any]],
    ranked_so: List[Dict[str, Any]],
) -> bool:
    """Gate 3a/3b deferred — single PP evaluation @ Step 5 on execution queue."""
    _ = protection_pool, ranked_so
    if getattr(system, "_pp_gates_complete_at_step5_pt_date", None):
        return bool(getattr(system, "_red_day_filter_blocked", False))
    log.info(
        "ORB_RANK | gate_3a_3b=deferred | reason=pp_runs_once_at_step5_on_execution_queue"
    )
    return bool(getattr(system, "_red_day_filter_blocked", False))


async def _send_deferred_signal_collection_alert(
    system: Any,
    *,
    so_signals_ranked: List[Dict[str, Any]],
) -> None:
    deferred_payload = getattr(system, "_deferred_so_signal_collection_payload", None)
    deferred_sent = getattr(system, "_deferred_so_collection_alert_sent", False)
    if (
        not deferred_payload
        or deferred_sent
        or getattr(system, "_so_collection_alert_sent_today", False)
        or not getattr(system, "alert_manager", None)
    ):
        return
    try:
        mode_u = str(deferred_payload.get("mode", "") or "").upper()
        alert_so = [] if mode_u == "LIVE" else so_signals_ranked
        await system.alert_manager.send_so_signal_collection(
            so_signals=alert_so,
            total_scanned=deferred_payload.get("total_scanned", 0),
            mode=deferred_payload.get("mode", "DEMO"),
            spx_orb_data=deferred_payload.get("spx_orb_data"),
            qqq_orb_data=deferred_payload.get("qqq_orb_data"),
            spy_orb_data=deferred_payload.get("spy_orb_data"),
            dte0_signals_qualified=deferred_payload.get("dte0_signals_qualified"),
            dte0_signals_list=deferred_payload.get("dte0_signals_list"),
            dte_symbols_list=deferred_payload.get("dte_symbols_list"),
            hard_gated_symbols=deferred_payload.get("hard_gated_symbols"),
            dte0_collection_candidates=deferred_payload.get("dte0_collection_candidates"),
            dte0_convex_passed=deferred_payload.get("dte0_convex_passed"),
            dte0_hard_gate_passed=deferred_payload.get("dte0_hard_gate_passed"),
            dte0_pending_execution=deferred_payload.get("dte0_pending_execution"),
            red_day_blocked=bool(getattr(system, "_red_day_filter_blocked", False)),
            red_day_reason=getattr(system, "_red_day_reason", None)
            or deferred_payload.get("red_day_reason"),
            zero_signals_reason=deferred_payload.get("zero_signals_reason"),
            breakout_candidates_list=deferred_payload.get("breakout_candidates_list"),
        )
        system._so_collection_alert_sent_today = True
        system._trigger_late_scan_alert = False
        system._deferred_so_collection_alert_sent = True
        system._deferred_so_signal_collection_payload = None
        if hasattr(system, "_register_so_collection_allowlist") and mode_u != "LIVE":
            system._register_so_collection_allowlist(
                so_signals_ranked, source="deferred_signal_collection_ranked"
            )
        if hasattr(system, "daily_run_tracker") and system.daily_run_tracker:
            try:
                system.daily_run_tracker.record_signal_collection(
                    signals=so_signals_ranked,
                    total_scanned=deferred_payload.get("total_scanned", 0),
                    mode=deferred_payload.get("mode", "DEMO"),
                    metadata={
                        "window": "SO",
                        "service": os.getenv("K_SERVICE"),
                        "revision": os.getenv("K_REVISION"),
                        "collection_alert_sent": True,
                    },
                )
            except Exception as tracker_error:
                log.warning("ORB_RANK | deferred_alert_marker_failed | %s", tracker_error)
    except Exception as alert_send_error:
        log.error("ORB_RANK | deferred_alert_failed | %s", alert_send_error)


async def rank_orb_signals_for_0dte(system: Any, orb_signals: List[Dict[str, Any]]) -> bool:
    """Enrich for Convex, run 0DTE pipeline, send Signal Collection alert (PP @ pre-execute)."""
    so_signals = [
        s for s in (orb_signals or []) if isinstance(s, dict) and s.get("signal_type") == "SO"
    ]
    if not so_signals:
        # LIVE 0DTE-only: rank pool is LONG rows from dte_signals (may omit signal_type filter).
        so_signals = [
            s
            for s in (orb_signals or [])
            if isinstance(s, dict) and str(s.get("side", "LONG") or "LONG").upper() == "LONG"
        ]
    dte_n = len(getattr(system, "_pending_dte_signals", None) or [])
    log.info(
        "ORB_ENRICH_FOR_0DTE | action=start | so_mirror=%d | dte_pending=%d | total_in=%d",
        len(so_signals),
        dte_n,
        len(orb_signals or []),
    )

    so_priority_ordered: List[Dict[str, Any]] = []
    if so_signals:
        enriched: List[Dict[str, Any]] = []
        for sig in so_signals:
            row = dict(sig)
            await system._backfill_so_signal_tech_fields(row)
            system._lightweight_enrich_signal_for_red_day(row)
            enriched.append(row)

        from .orb_priority_score import calculate_so_priority_score

        for row in enriched:
            row["priority_score"] = calculate_so_priority_score(row)
        so_priority_ordered = sorted(
            enriched,
            key=lambda s: (-float(s.get("priority_score") or 0), str(s.get("symbol") or "")),
        )
        log.info(
            "ORB_ENRICH_FOR_0DTE | stage=so_mirror_priority | count=%d | note=not_execution_rank",
            len(so_priority_ordered),
        )
        system._step5_so_signals_ranked = list(so_priority_ordered)

    if getattr(system, "_defer_0dte_pipeline_until_so_rank", False):
        try:
            from .orb_shared_730_pipeline import (
                merge_dte_result_into_deferred_alert,
                stamp_so_priority_for_0dte,
            )

            stamp_so_priority_for_0dte(so_priority_ordered)
            dte_pipe = await system._run_orb_0dte_pipeline_at_730(
                pending_so_signals=so_priority_ordered,
                pending_dte_signals=list(getattr(system, "_pending_dte_signals", None) or []),
            )
            merge_dte_result_into_deferred_alert(system, dte_pipe)
            log.info(
                "0DTE_PIPELINE | stage=convex_complete | convex_queue=%d | hard_gate_queue=%d | "
                "note=priority_rank_runs_once_post_hard_gate",
                int(dte_pipe.get("dte0_convex_passed") or 0),
                int(dte_pipe.get("dte0_hard_gate_passed") or 0),
            )
        except Exception as dte_defer_err:
            log.error("0DTE_PIPELINE | stage=convex_failed | error=%s", dte_defer_err)
        finally:
            system._defer_0dte_pipeline_until_so_rank = False

    await _send_deferred_signal_collection_alert(system, so_signals_ranked=so_priority_ordered)

    log.info(
        "ORB_ENRICH_FOR_0DTE | action=complete | so_mirror=%d | pp=deferred_step5_execution_queue",
        len(so_priority_ordered),
    )
    return True
