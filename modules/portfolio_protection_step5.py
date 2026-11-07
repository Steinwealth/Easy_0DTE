"""
Portfolio Protection @ scheduler Step 5 (Gate 3a + 3b) — RESEARCH app.

GCS persistence, enrichment guards, and mandatory audit logging for the
easy-etrade-strategy Cloud Run service.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, List, Optional
from zoneinfo import ZoneInfo

from .portfolio_protection import (
    assessment_to_legacy_metrics,
    evaluate_portfolio_protection,
    log_portfolio_protection_check,
    portfolio_protection_observe_only_enabled,
    protection_pool_enrichment_sufficient,
    publish_portfolio_protection_session,
    should_apply_portfolio_protection_block,
    would_portfolio_protection_trigger_block,
)
from .portfolio_protection_gcs import (
    assessment_to_marker,
    log_pp_audit,
    persist_pp_step5_marker,
    try_hydrate_pp_from_gcs,
)

log = logging.getLogger(__name__)


async def force_enrich_protection_pool(system: Any, pool: List[dict]) -> None:
    """Second-pass enrichment so PP volume coverage is measured on real ratios."""
    from .portfolio_protection import raw_volume_ratio

    # Broker quote-delta hydrate first (same hub TL/BO watch uses) so coverage
    # is not stuck at 0% on pad/estimate-only bars from a cold Cloud Run tick.
    try:
        from modules.session_tech_indicators import apply_session_tech, get_session_tech
        from modules.volume_data import hydrate_equity_session_tech

        symbols: List[str] = []
        seen = set()
        for sig in pool or []:
            if not isinstance(sig, dict):
                continue
            if raw_volume_ratio(sig) is not None:
                continue
            sym = str(sig.get("symbol") or "").strip().upper()
            if sym and sym not in seen:
                seen.add(sym)
                symbols.append(sym)
        if symbols:
            broker_quotes: dict = {}
            dm = getattr(system, "data_manager", None)
            if dm is not None and hasattr(dm, "get_batch_quotes"):
                try:
                    broker_quotes = await dm.get_batch_quotes(symbols, skip_cache=True) or {}
                except TypeError:
                    broker_quotes = await dm.get_batch_quotes(symbols) or {}
                except Exception as qex:
                    log.debug("PORTFOLIO_PROTECTION | enrich_quotes_failed | %s", qex)
                    broker_quotes = {}
            # Dual-write session-tech hub + Liquidity Guard ADV$ from the same poll.
            ingest = getattr(system, "_ingest_broker_quotes_into_session_tech", None)
            if callable(ingest) and broker_quotes:
                try:
                    ingest(broker_quotes)
                except Exception as ingest_err:
                    log.debug(
                        "PORTFOLIO_PROTECTION | enrich_ingest_failed | %s",
                        ingest_err,
                    )
            await hydrate_equity_session_tech(
                system,
                symbols,
                quotes=broker_quotes if broker_quotes else None,
            )
            for sig in pool or []:
                if not isinstance(sig, dict) or raw_volume_ratio(sig) is not None:
                    continue
                sym = str(sig.get("symbol") or "").strip().upper()
                if not sym:
                    continue
                price = sig.get("current_price", sig.get("price"))
                try:
                    price_f = float(price) if price is not None else None
                except (TypeError, ValueError):
                    price_f = None
                tech = get_session_tech(system, sym, price=price_f)
                if tech:
                    apply_session_tech(sig, tech)
    except Exception as hub_err:
        log.debug("PORTFOLIO_PROTECTION | enrich_hub_failed | %s", hub_err)

    for sig in pool or []:
        if not isinstance(sig, dict):
            continue
        if raw_volume_ratio(sig) is not None:
            continue
        try:
            await system._backfill_so_signal_tech_fields(sig)
        except Exception as err:
            log.debug("PORTFOLIO_PROTECTION | enrich_backfill_failed | %s", err)
        if raw_volume_ratio(sig) is None:
            try:
                system._lightweight_enrich_signal_for_red_day(sig)
            except Exception as err:
                log.debug("PORTFOLIO_PROTECTION | enrich_lightweight_failed | %s", err)


async def run_portfolio_protection_gates_at_step5(
    system: Any,
    rank_pool: Optional[List[dict]] = None,
) -> None:
    """
    Run Gate 3a + 3b once per PT day on the post-Convex 0DTE execution queue.
    Persists results to GCS when evaluation completes.
    """
    from .orb_rank_for_0dte import _build_0dte_execution_protection_pool_enriched

    _ = rank_pool
    pt_tz = ZoneInfo("America/Los_Angeles")
    today_pt = datetime.now(pt_tz).strftime("%Y-%m-%d")
    instance = os.getenv("K_REVISION", "local")

    log_pp_audit(
        "step5_enter",
        reason="evaluate_or_hydrate",
        date_pt=today_pt,
        instance=instance,
    )

    if getattr(system, "_pp_gates_complete_at_step5_pt_date", None) == today_pt:
        log_pp_audit(
            "skipped",
            reason="in_memory_complete",
            date_pt=today_pt,
            instance=instance,
            blocked=str(getattr(system, "_red_day_filter_blocked", False)).lower(),
        )
        return

    if try_hydrate_pp_from_gcs(system, today_pt):
        log_pp_audit(
            "skipped",
            reason="gcs_marker_hydrated",
            date_pt=today_pt,
            instance=instance,
            blocked=str(getattr(system, "_red_day_filter_blocked", False)).lower(),
        )
        return

    pool = await _build_0dte_execution_protection_pool_enriched(system)
    await force_enrich_protection_pool(system, pool)
    system._last_pp_protection_pool = list(pool)
    pool_n = len(pool)

    if pool_n == 0:
        log.info(
            "PORTFOLIO_PROTECTION | stage=step5_pre_execution | action=complete | "
            "reason=empty_0dte_execution_queue | note=no_op_eval"
        )
        log_pp_audit(
            "complete",
            reason="empty_0dte_execution_queue",
            date_pt=today_pt,
            blocked="false",
        )
        system._pp_gates_complete_at_step5_pt_date = today_pt
        system._pp_step5_gate_synced_pt_date = today_pt
        system._gate_3b_step5_evaluated_pt_date = today_pt
        return

    sufficient, vol_cov = protection_pool_enrichment_sufficient(pool)
    if not sufficient:
        log.warning(
            "PORTFOLIO_PROTECTION | stage=step5_pre_execution | action=defer_gates | "
            "reason=volume_coverage_insufficient | coverage_pct=%.1f | pool=%d",
            vol_cov,
            pool_n,
        )
        log_pp_audit(
            "deferred",
            reason="volume_coverage_insufficient",
            date_pt=today_pt,
            coverage_pct=f"{vol_cov:.1f}",
            pool=pool_n,
        )
        return

    log.info(
        "PORTFOLIO_PROTECTION | path=0dte_execution_queue | pool=%d | volume_coverage_pct=%.1f | symbols=%s",
        pool_n,
        vol_cov,
        ",".join(str(s.get("symbol", "")).strip().upper() for s in pool[:12] if s.get("symbol")),
    )

    assess = evaluate_portfolio_protection(pool)
    system._portfolio_protection_assessment = assess
    log_portfolio_protection_check(assess, pool_n)

    if should_apply_portfolio_protection_block(assess):
        await system._apply_portfolio_protection_gate_block(
            trigger_id=assess.trigger_id or "portfolio_protection",
            reason=assess.primary_reason or "",
            metrics=assessment_to_legacy_metrics(assess),
            pool_count=pool_n,
            alert_path="step5_pre_execution_gate_3a",
        )
        if hasattr(system, "_refresh_0dte_pipeline_for_portfolio_red_day"):
            try:
                so_ranked = list(
                    getattr(system, "_step5_so_signals_ranked", None)
                    or getattr(system, "_pending_so_signals", None)
                    or []
                )
                await system._refresh_0dte_pipeline_for_portfolio_red_day(
                    so_signals_ranked=so_ranked
                )
            except Exception as refresh_err:
                log.error(
                    "0DTE_CONVEX_RED_DAY_REFRESH | after_pre_execution_3a | %s",
                    refresh_err,
                )
        marker = assessment_to_marker(
            assess,
            date_pt=today_pt,
            pool_count=pool_n,
            red_day_filter_blocked=True,
            volume_coverage_pct=vol_cov,
        )
        persist_pp_step5_marker(marker)
        system._pp_gates_complete_at_step5_pt_date = today_pt
        system._pp_step5_gate_synced_pt_date = today_pt
        system._gate_3b_step5_evaluated_pt_date = today_pt
        log_pp_audit(
            "complete",
            reason="gate_3a_block",
            date_pt=today_pt,
            trigger_id=assess.trigger_id,
            blocked="true",
        )
        return

    if would_portfolio_protection_trigger_block(assess) and portfolio_protection_observe_only_enabled():
        _legacy = assessment_to_legacy_metrics(assess)
        system._portfolio_protection_observe_trigger = assess.trigger_id
        system._portfolio_protection_observe_reason = assess.primary_reason
        system._portfolio_protection_observe_metrics = {
            **dict(_legacy or {}),
            "portfolio_protection_trigger": assess.trigger_id,
        }
        log.warning(
            "PORTFOLIO_PROTECTION | action=observe_only | trigger=%s | pool=%d | execution=continue",
            assess.trigger_id,
            pool_n,
        )
        await system._send_portfolio_protection_informational_alert(
            trigger_id=assess.trigger_id or "portfolio_protection",
            reason=assess.primary_reason or "",
            protection_pool_count=pool_n,
            alert_path="step5_pre_execution_gate_3a_observe",
            observe_only=True,
        )
        marker = assessment_to_marker(
            assess,
            date_pt=today_pt,
            pool_count=pool_n,
            red_day_filter_blocked=False,
            volume_coverage_pct=vol_cov,
            observe_only=True,
        )
        persist_pp_step5_marker(marker)
        system._pp_gates_complete_at_step5_pt_date = today_pt
        system._pp_step5_gate_synced_pt_date = today_pt
        system._gate_3b_step5_evaluated_pt_date = today_pt
        log_pp_audit(
            "complete",
            reason="gate_3a_observe_only",
            date_pt=today_pt,
            trigger_id=assess.trigger_id,
            blocked="false",
        )
        return

    system._pp_step5_gate_synced_pt_date = today_pt
    system._gate_3b_step5_evaluated_pt_date = today_pt
    log.info("🔍 PORTFOLIO PROTECTION GATE — composite risk analysis (pre-execution)")

    gate_3b_blocked = False
    try:
        gate_3b = await system._run_gate_3b_composite_risk(pool)
        if gate_3b:
            trigger_id, reason, metrics = gate_3b
            publish_portfolio_protection_session(
                trigger_id=trigger_id,
                primary_reason=reason,
                triggers_fired=[trigger_id],
                block_so_execution=True,
                observe_only=portfolio_protection_observe_only_enabled(),
            )
            gate_3b_blocked = True
            if portfolio_protection_observe_only_enabled():
                system._portfolio_protection_observe_trigger = trigger_id
                system._portfolio_protection_observe_reason = reason
                system._portfolio_protection_observe_metrics = dict(metrics or {})
                log.warning(
                    "PORTFOLIO PROTECTION GATE (composite risk) — OBSERVE ONLY | %s | execution=continue",
                    reason,
                )
                await system._send_portfolio_protection_informational_alert(
                    trigger_id=trigger_id,
                    reason=reason,
                    protection_pool_count=pool_n,
                    alert_path="step5_pre_execution_gate_3b_observe",
                    observe_only=True,
                )
                gate_3b_blocked = False
            else:
                system._red_day_filter_blocked = True
                system._red_day_reason = reason
                system._red_day_metrics = metrics
                log.warning(
                    "🚨 PORTFOLIO PROTECTION GATE (composite risk) — EXECUTION SKIPPED | %s",
                    reason,
                )
                await system._send_portfolio_protection_informational_alert(
                    trigger_id=trigger_id,
                    reason=reason,
                    protection_pool_count=pool_n,
                    alert_path="step5_pre_execution_gate_3b",
                )
                if hasattr(system, "_refresh_0dte_pipeline_for_portfolio_red_day"):
                    try:
                        so_ranked = list(
                            getattr(system, "_step5_so_signals_ranked", None)
                            or getattr(system, "_pending_so_signals", None)
                            or []
                        )
                        await system._refresh_0dte_pipeline_for_portfolio_red_day(
                            so_signals_ranked=so_ranked
                        )
                    except Exception as refresh_err:
                        log.error(
                            "0DTE_CONVEX_RED_DAY_REFRESH | after_pre_execution_3b | %s",
                            refresh_err,
                        )
        else:
            _assess = getattr(system, "_last_enhanced_red_day_assessment", None)
            _rec = str(getattr(_assess, "recommendation", "") or "")
            if _rec and not _rec.startswith("REDUCE_POSITION_SIZE"):
                log.info("✅ Enhanced analysis: Normal execution approved")
    except Exception as gate_3b_err:
        log.error("Enhanced red day detection failed: %s", gate_3b_err)
        log.info("⚠️ Continuing with portfolio protection results")

    marker = assessment_to_marker(
        assess,
        date_pt=today_pt,
        pool_count=pool_n,
        red_day_filter_blocked=gate_3b_blocked,
        volume_coverage_pct=vol_cov,
        observe_only=portfolio_protection_observe_only_enabled()
        and bool(getattr(system, "_portfolio_protection_observe_trigger", None)),
    )
    if gate_3b_blocked:
        _rdm = getattr(system, "_red_day_metrics", None) or {}
        marker["trigger_id"] = _rdm.get("portfolio_protection_trigger") or "enhanced_red_day_skip_execution"
        marker["primary_reason"] = getattr(system, "_red_day_reason", None)
        marker["block_so_execution"] = True
        marker["metrics"] = dict(_rdm)

    persist_pp_step5_marker(marker)
    system._pp_gates_complete_at_step5_pt_date = today_pt
    log_pp_audit(
        "complete",
        reason="gate_3b_pass" if not gate_3b_blocked else "gate_3b_block",
        date_pt=today_pt,
        blocked=str(gate_3b_blocked).lower(),
    )


async def ensure_pp_ready_before_execution(system: Any, *, context: str = "pre_execution") -> bool:
    """
    Guarantee PP was evaluated (or hydrated from GCS) before trading.

    Returns True when Step-5 PP is complete for today; False when execution must wait.
    """
    from .portfolio_protection import portfolio_protection_enabled

    pt_tz = ZoneInfo("America/Los_Angeles")
    today_pt = datetime.now(pt_tz).strftime("%Y-%m-%d")

    if not portfolio_protection_enabled():
        log_pp_audit(
            "ready",
            reason="pp_disabled",
            context=context,
            date_pt=today_pt,
        )
        system._pp_gates_complete_at_step5_pt_date = today_pt
        system._red_day_filter_blocked = False
        return True

    # Warm Liquidity Guard ADV$ (and session-tech deltas) before PP / fills.
    ensure_adv = getattr(system, "_ensure_liquidity_guard_adv_ready", None)
    if callable(ensure_adv):
        try:
            await ensure_adv(force=False)
        except Exception as adv_err:
            log.debug(
                "PORTFOLIO_PROTECTION | adv_ensure_failed | context=%s | err=%s",
                context,
                adv_err,
            )

    if getattr(system, "_pp_gates_complete_at_step5_pt_date", None) == today_pt:
        log_pp_audit("ready", reason="in_memory_complete", context=context, date_pt=today_pt)
        return True

    if try_hydrate_pp_from_gcs(system, today_pt):
        log_pp_audit("ready", reason="gcs_hydrated", context=context, date_pt=today_pt)
        return True

    log_pp_audit("run_missing", reason="pp_not_complete", context=context, date_pt=today_pt)
    await run_portfolio_protection_gates_at_step5(system)

    if getattr(system, "_pp_gates_complete_at_step5_pt_date", None) == today_pt:
        log_pp_audit("ready", reason="evaluated_now", context=context, date_pt=today_pt)
        return True

    log_pp_audit("not_ready", reason="pp_deferred_or_failed", context=context, date_pt=today_pt)
    return False
