"""
Shared 7:30 PT ORB pipeline helpers — SO + 0DTE alignment (Rev 00373).

Runs 0DTE Convex (pc_oi + ORB range) → Hard Gate → priority rank (once) → Gate 3c cap @ 7:30.
Pre-Convex: enrich + SO-mirror priority stamp only (not execution rank).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)


def build_ranked_dte_collection_list(dte_candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Stable collection list for Convex input and alert context (no execution priority_rank)."""
    if not dte_candidates:
        return []

    def _sort_key(item: Tuple[int, Dict[str, Any]]):
        idx, sig = item
        pr = sig.get("priority_rank")
        if pr is not None:
            try:
                return (0, float(pr))
            except (TypeError, ValueError):
                pass
        ps = sig.get("priority_score")
        if ps is not None:
            try:
                return (1, -float(ps))
            except (TypeError, ValueError):
                pass
        try:
            from modules.orb_priority_geometry import collection_geometry_rank_score

            _geom = -float(collection_geometry_rank_score(sig))
            return (2, _geom)
        except Exception:
            pass
        conf = sig.get("confidence")
        if conf is not None:
            try:
                return (3, -float(conf))
            except (TypeError, ValueError):
                pass
        return (4, idx)

    ranked = [sig for _, sig in sorted(list(enumerate(dte_candidates)), key=_sort_key)]
    out: List[Dict[str, Any]] = []
    for i, sig in enumerate(ranked, 1):
        side = sig.get("side") or "LONG"
        side_u = side.upper() if isinstance(side, str) else "LONG"
        opt_label = "PUT" if side_u == "SHORT" else "CALL"
        # Preserve hydrated SO/0DTE priority fields (RSI, MACD, VWAP, orb_data) for PO snapshots.
        row = dict(sig)
        row.update(
            {
                "symbol": sig.get("symbol", "UNKNOWN"),
                "side": side_u,
                "direction": side_u,
                "option_type": "put" if side_u == "SHORT" else "call",
                "option_type_label": opt_label,
                "eligibility_score": sig.get("confidence", 0.0),
                "target_delta": sig.get("target_delta"),
                "spread_width": sig.get("spread_width"),
                "spread_type": sig.get("spread_type", "debit"),
                "momentum_score": sig.get("momentum_score", 0.0),
                "strategy_type": sig.get("strategy_type", "debit_spread"),
                "priority_score": sig.get("priority_score", sig.get("confidence", 0.0)),
                "hard_gate_passed": False,
                "hard_gate_reason": "Rejected by Convex Eligibility Filter",
            }
        )
        out.append(row)
    return out


def stamp_so_priority_for_0dte(so_signals: List[Dict[str, Any]]) -> None:
    """Copy SO priority_score onto so_priority_score for 0DTE Convex mirror (no execution rank)."""
    for sig in so_signals or []:
        if not isinstance(sig, dict):
            continue
        ps = sig.get("priority_score")
        if ps is not None:
            try:
                sig["so_priority_score"] = float(ps)
            except (TypeError, ValueError):
                pass


def _signal_symbol_side_key(sig: Dict[str, Any]) -> Tuple[str, str]:
    sym = str(sig.get("symbol") or "").upper()
    side = str(sig.get("side") or sig.get("direction") or "LONG").upper()
    if side in ("PUT", "SHORT"):
        side = "SHORT"
    else:
        side = "LONG"
    return sym, side


def filter_cutoff_rejected_from_0dte_pool(
    signals: List[Dict[str, Any]],
    rejected_keys: Any,
    *,
    log_blocks: bool = True,
) -> List[Dict[str, Any]]:
    """
    Drop symbols the 0DTE 7:30 cutoff already rejected, even if an SO twin still exists.

    Jul 22 2026: GLD failed 0DTE cutoff (penny under ORB high) then re-entered Convex
    via ``pending_so + pending_dte`` merge and burned a selector attempt.
    """
    blocked = {tuple(k) for k in (rejected_keys or set()) if k}
    if not blocked or not signals:
        return list(signals or [])
    kept: List[Dict[str, Any]] = []
    for sig in signals:
        if not isinstance(sig, dict):
            continue
        key = _signal_symbol_side_key(sig)
        if key in blocked and key[0]:
            if log_blocks:
                log.info(
                    "0DTE_RECHECK_SO_RESURRECT_BLOCKED | symbol=%s | side=%s | "
                    "reason=cutoff_rejected_earlier",
                    key[0],
                    key[1],
                )
            continue
        kept.append(sig)
    return kept


def merge_dte_result_into_deferred_alert(system: Any, result: Dict[str, Any]) -> None:
    """Patch deferred Signal Collection payload with post-rank 0DTE funnel fields."""
    payload = getattr(system, "_deferred_so_signal_collection_payload", None)
    if not isinstance(payload, dict):
        return
    for key in (
        "dte0_signals_qualified",
        "dte0_signals_list",
        "dte0_collection_candidates",
        "dte0_convex_passed",
        "dte0_hard_gate_passed",
        "dte0_pending_execution",
        "hard_gated_symbols",
    ):
        if key in result:
            payload[key] = result[key]


async def enrich_signals_with_options_chain_for_convex(
    system: Any,
    signals: List[Dict[str, Any]],
) -> None:
    """Fetch broker chain OI skew before Convex pc_oi gate @ 7:30 (not PO snapshot path)."""
    from easy0DTE.modules.convex_chain_skew import enrich_signals_with_chain_skew_for_convex

    await enrich_signals_with_chain_skew_for_convex(system, signals)


def propagate_options_chain_fields_to_pools(
    source_rows: List[Dict[str, Any]],
    *target_pools: List[Dict[str, Any]],
) -> int:
    """Copy chain skew fields onto collection pool rows (Convex pc_oi path)."""
    from easy0DTE.modules.convex_chain_skew import propagate_chain_skew_fields_to_pools

    return propagate_chain_skew_fields_to_pools(source_rows, *target_pools)


async def run_orb_0dte_pipeline_at_730(
    system: Any,
    *,
    pending_so_signals: List[Dict[str, Any]],
    pending_dte_signals: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    Collection (geometry) → chain enrich (broker) → Convex (pc_oi + ORB≥0.25%) → Hard Gate → rank/cap @ 7:30.
    Chain enrich runs here; Convex / Hard Gate / rank+cap run inside listen_to_orb_signals().
    """
    from zoneinfo import ZoneInfo

    from .config_loader import get_config_value

    empty: Dict[str, Any] = {
        "dte0_signals_list": [],
        "dte0_signals_qualified": 0,
        "dte0_collection_candidates": 0,
        "dte0_convex_passed": 0,
        "dte0_hard_gate_passed": 0,
        "dte0_pending_execution": 0,
        "hard_gated_symbols": [],
    }
    if not getattr(system, "dte0_manager", None):
        return empty

    pending_so = list(pending_so_signals or [])
    stamp_so_priority_for_0dte(pending_so)

    pending_dte = list(pending_dte_signals or [])
    pending_dte = system._hydrate_dte_signals_for_convex(pending_so, pending_dte)
    for _pre_sig in pending_so:
        await system._backfill_so_signal_tech_fields(_pre_sig)
    pending_dte = system._hydrate_dte_signals_for_convex(pending_so, pending_dte)
    for _pre_dte in pending_dte:
        await system._backfill_so_signal_tech_fields(_pre_dte)

    dte0_collection_candidates = len(pending_dte)
    ranked_dte_collection = build_ranked_dte_collection_list(pending_dte)
    system._dte0_options_found_count = len(ranked_dte_collection)
    system._dte0_pipeline_collection_count = len(ranked_dte_collection)
    system._dte0_pipeline_convex_qualified_count = 0
    system._dte0_pipeline_hard_gate_passed_count = 0
    system._dte0_pipeline_execution_queue_count = 0
    system._dte0_convex_rejected_count = 0

    combined_for_dte = system._dedupe_orb_signals_by_symbol_side(pending_so + pending_dte)
    combined_for_dte = filter_cutoff_rejected_from_0dte_pool(
        combined_for_dte,
        getattr(system, "_dte_cutoff_rejected_keys", None),
    )

    # Session VWAP/MACD overwrite daily yfinance tech before Convex L3/L4 gates.
    try:
        await system.apply_session_tech_for_convex(
            combined_for_dte,
            pending_so,
            pending_dte,
            ranked_dte_collection,
        )
        # Re-hydrate so DTE rows pick up session fields stamped on SO twins.
        pending_dte = system._hydrate_dte_signals_for_convex(pending_so, pending_dte)
        combined_for_dte = system._dedupe_orb_signals_by_symbol_side(pending_so + pending_dte)
        combined_for_dte = filter_cutoff_rejected_from_0dte_pool(
            combined_for_dte,
            getattr(system, "_dte_cutoff_rejected_keys", None),
        )
    except Exception as _session_tech_exc:
        log.warning(
            "0DTE_SESSION_TECH | stage=pipeline_failed | err=%s | falling_back_to_prior_tech",
            _session_tech_exc,
        )

    log.info(
        "0DTE_PIPELINE | stage=convex_input | combined=%d | so=%d | dte=%d | "
        "grep=0DTE_COLLECTION_FUNNEL_SUMMARY",
        len(combined_for_dte),
        len(pending_so),
        len(pending_dte),
    )

    _portfolio_red_day_for_convex = bool(getattr(system, "_red_day_filter_blocked", False))
    if not _portfolio_red_day_for_convex and ranked_dte_collection:
        _rd_preview = [dict(s) for s in ranked_dte_collection]
        for _sig in _rd_preview:
            system._lightweight_enrich_signal_for_red_day(_sig)
        _portfolio_red_day_for_convex, _rd_preview_reason = system._evaluate_portfolio_red_day_pattern(
            _rd_preview
        )
        if _portfolio_red_day_for_convex:
            log.info(
                "0DTE_CONVEX_RED_DAY_PREVIEW | portfolio_red_day=true | reason=%s",
                _rd_preview_reason or "pattern",
            )
            if _rd_preview_reason and not getattr(system, "_red_day_reason", None):
                system._red_day_reason = _rd_preview_reason

    try:
        await enrich_signals_with_options_chain_for_convex(system, combined_for_dte)
        propagated = propagate_options_chain_fields_to_pools(
            combined_for_dte,
            pending_dte,
            pending_so,
            ranked_dte_collection,
        )
        if propagated:
            log.info(
                "0DTE_CONVEX_PC_OI | stage=chain_propagate | rows=%d | grep=0DTE_CONVEX_PC_OI",
                propagated,
            )
    except Exception as _pc_oi_exc:
        log.warning("0DTE_CONVEX_PC_OI | chain_enrich_failed | %s", _pc_oi_exc)

    dte0_manager = system.dte0_manager
    dte0_signals = await dte0_manager.listen_to_orb_signals(
        combined_for_dte,
        orb_strategy_manager=system.orb_strategy_manager,
        portfolio_red_day=_portfolio_red_day_for_convex,
    )
    system._0dte_convex_processed_today = True

    convex_qualified_count = int(getattr(dte0_manager, "_last_convex_qualified_count", 0) or 0)
    hard_gate_passed_count = int(getattr(dte0_manager, "_last_hard_gate_passed_count", 0) or 0)
    convex_pass_keys = set(getattr(dte0_manager, "_last_convex_pass_keys", None) or set())
    hard_gate_passed_key_set = set(getattr(dte0_manager, "_last_hard_gate_passed_keys", None) or set())
    hard_gate_failed_reasons = dict(getattr(dte0_manager, "_last_hard_gate_failed_reasons", None) or {})

    hard_gate_failed_list: List[Tuple[str, str, str]] = []
    for key, reason in hard_gate_failed_reasons.items():
        parts = str(key).rsplit("_", 1)
        side = parts[1].upper() if len(parts) == 2 else "LONG"
        sym = parts[0] if len(parts) == 2 else str(key)
        opt_label = "PUT" if side == "SHORT" else "CALL"
        hard_gate_failed_list.append((sym, opt_label, reason))

    dte0_signals_for_execution: List[Any] = list(dte0_signals or [])
    dte0_signals_list: List[Dict[str, Any]] = []
    execution_queue_keys = {
        f"{str(sig.symbol).upper()}_{'SHORT' if str(getattr(sig, 'direction', 'LONG')).upper() == 'SHORT' else 'LONG'}"
        for sig in dte0_signals_for_execution
    }

    if dte0_signals:
        log.info(
            "0DTE_PIPELINE | stage=listen_complete | convex=%d | hard_gate=%d | execution_queue=%d",
            convex_qualified_count,
            hard_gate_passed_count,
            len(dte0_signals_for_execution),
        )
        for signal in dte0_signals_for_execution:
            dte0_signals_list.append(
                {
                    "symbol": signal.symbol,
                    "direction": signal.direction,
                    "option_type": signal.option_type,
                    "option_type_label": signal.option_type_label,
                    "eligibility_score": signal.eligibility_result.eligibility_score,
                    "target_delta": signal.target_delta,
                    "spread_width": signal.spread_width,
                    "spread_type": getattr(signal, "spread_type", "debit"),
                    "momentum_score": getattr(signal, "momentum_score", 0.0),
                    "strategy_type": getattr(signal, "strategy_type", "debit_spread"),
                    "priority_rank": getattr(signal, "priority_rank", 0),
                    "priority_score": getattr(signal, "priority_score", 0.0),
                    "hard_gate_passed": True,
                    "hard_gate_reason": None,
                }
            )

        system._pending_dte0_signals = dte0_signals_for_execution
        system._dte0_convex_rejected_count = max(0, len(ranked_dte_collection) - convex_qualified_count)
        system._dte0_pipeline_convex_qualified_count = convex_qualified_count
        system._dte0_pipeline_hard_gate_passed_count = hard_gate_passed_count
        system._dte0_pipeline_execution_queue_count = len(dte0_signals_for_execution)
        dte0_convex_passed = convex_qualified_count
        dte0_hard_gate_passed = hard_gate_passed_count
        dte0_pending_execution = len(dte0_signals_for_execution)
    else:
        log.warning(
            "0DTE_PIPELINE | stage=listen_complete | convex=%d | hard_gate=%d | execution_queue=0",
            convex_qualified_count,
            hard_gate_passed_count,
        )
        system._dte0_convex_rejected_count = max(0, len(ranked_dte_collection) - convex_qualified_count)
        system._pending_dte0_signals = []
        system._dte0_pipeline_convex_qualified_count = convex_qualified_count
        system._dte0_pipeline_hard_gate_passed_count = hard_gate_passed_count
        system._dte0_pipeline_execution_queue_count = 0
        dte0_convex_passed = convex_qualified_count
        dte0_hard_gate_passed = hard_gate_passed_count
        dte0_pending_execution = 0

    try:
        system._persist_0dte_pipeline_metrics_to_gcs()
    except Exception:
        pass

    hard_gated_symbols: List[Dict[str, Any]] = []
    if ranked_dte_collection:
        from .orb_0dte_priority_fields import (
            build_symbol_side_signal_lookup,
            merge_priority_fields_into_0dte_pipeline_row,
        )

        dte_source_lookup = build_symbol_side_signal_lookup(pending_dte)
        so_source_lookup = build_symbol_side_signal_lookup(pending_so)
        by_key = {
            f"{(s.get('symbol') or '').upper()}_{(s.get('direction') or '').upper()}": s
            for s in (dte0_signals_list or [])
            if isinstance(s, dict)
        }
        merged_list: List[Dict[str, Any]] = []
        hard_gate_failed_map = dict(hard_gate_failed_reasons)
        priority_dropped_keys = getattr(dte0_manager, "_last_priority_dropped_keys", None) or set()
        zero_ps_dropped_keys = getattr(dte0_manager, "_last_zero_ps_dropped_keys", None) or set()
        structure_skip_keys = getattr(dte0_manager, "_last_structure_skip_keys", None) or set()
        convex_audit = getattr(dte0_manager, "_last_convex_audit_by_key", None) or {}
        from modules.unified_0dte_account import orb_0dte_priority_rank_final_batch_max

        max_pos_cap = int(
            getattr(system.dte0_manager, "max_positions", None)
            or orb_0dte_priority_rank_final_batch_max(get_config_value)
        )
        pipeline_stage_tally: Dict[str, int] = {}
        for base in ranked_dte_collection:
            key = f"{(base.get('symbol') or '').upper()}_{(base.get('direction') or '').upper()}"
            enriched = dict(base)
            if key in by_key:
                enriched.update(by_key[key])
            if key in execution_queue_keys:
                decision_stage = "pending_execution_queue"
                decision_reason = "passed_convex_hard_gate_and_priority_cap"
            elif key in hard_gate_failed_map:
                decision_stage = "hard_gate_reject"
                decision_reason = str(hard_gate_failed_map.get(key) or "hard_gate_failed")
            elif key in zero_ps_dropped_keys:
                decision_stage = "zero_priority_score_reject"
                decision_reason = "priority_score_at_or_below_floor"
            elif key in priority_dropped_keys:
                decision_stage = "priority_rank_cap_reject"
                decision_reason = f"not_in_top_{max_pos_cap}_by_priority_after_hard_gate"
            elif key in structure_skip_keys:
                decision_stage = "structure_selection_reject"
                decision_reason = "strategy_type_no_trade"
            elif key in convex_audit and not convex_audit[key].get("convex_eligible"):
                decision_stage = "convex_reject"
                decision_reason = (
                    convex_audit[key].get("all_reasons_short")
                    or convex_audit[key].get("top_reason")
                    or "convex_ineligible"
                )
            elif key in convex_pass_keys:
                decision_stage = "hard_gate_reject"
                decision_reason = "hard_gate_failed_without_reason"
            else:
                decision_stage = "pipeline_gap"
                decision_reason = "missing_audit_map_or_precollection_symbol"
            reason_out = decision_reason if isinstance(decision_reason, str) else str(decision_reason)
            if len(reason_out) > 380:
                reason_out = reason_out[:377] + "..."
            if key in convex_audit:
                ca = convex_audit[key]
                enriched["convex_eligible"] = ca.get("convex_eligible")
                enriched["convex_score"] = ca.get("convex_score")
                enriched["convex_top_reason"] = ca.get("top_reason")
                enriched["convex_all_reasons_short"] = ca.get("all_reasons_short")
                qgr = ca.get("quality_gate_report")
                if isinstance(qgr, dict):
                    enriched["quality_gate_report"] = qgr
            # Prefer live QG report from hydrated source row when audit missed it
            source_row = dte_source_lookup.get(key) or so_source_lookup.get(key)
            if (
                not isinstance(enriched.get("quality_gate_report"), dict)
                and isinstance(source_row, dict)
                and isinstance(source_row.get("quality_gate_report"), dict)
            ):
                enriched["quality_gate_report"] = source_row.get("quality_gate_report")
            enriched["pipeline_decision_stage"] = decision_stage
            enriched["pipeline_decision_reason"] = reason_out
            enriched["hard_gate_passed"] = key in hard_gate_passed_key_set
            if key in hard_gate_failed_map:
                enriched["hard_gate_reason"] = hard_gate_failed_map.get(key)
            enriched = merge_priority_fields_into_0dte_pipeline_row(enriched, source_row)
            pipeline_stage_tally[decision_stage] = int(pipeline_stage_tally.get(decision_stage, 0)) + 1
            merged_list.append(enriched)
            if not enriched.get("hard_gate_passed", True):
                hard_gated_symbols.append(
                    {
                        "symbol": enriched.get("symbol", "UNKNOWN"),
                        "reason": enriched.get("hard_gate_reason") or reason_out,
                    }
                )
        log.info(
            "0DTE_COLLECTION_FUNNEL_SUMMARY | collection_underlyings=%d | stage_counts=%s",
            len(ranked_dte_collection),
            ",".join(f"{k}={v}" for k, v in sorted(pipeline_stage_tally.items())),
        )
        dte0_signals_list = merged_list
        try:
            _dte_snap_date = getattr(system, "_signal_collection_date", None) or datetime.now(
                ZoneInfo("America/Los_Angeles")
            ).strftime("%Y-%m-%d")
            _dte_snap_scanned = int(getattr(system, "_last_signal_collection_total_scanned", 0) or 0) or (
                len(system.symbol_list) if getattr(system, "symbol_list", None) else 0
            )
            # Full options microstructure (viability, representative_*, greeks, totals)
            # — Convex only stamps the 6-field pc_oi pack; this fills the rest for PO.
            try:
                if hasattr(system, "_enrich_orb_0dte_pipeline_rows_with_options_chain"):
                    await system._enrich_orb_0dte_pipeline_rows_with_options_chain(merged_list)
            except Exception as _enrich_exc:
                log.debug("ORB1 Breakout options microstructure enrich skipped: %s", _enrich_exc)
            # After contract fields land, merge observe stamps + Layer D into QG report
            try:
                from easy0DTE.modules.quality_gate_stack import (
                    PATH_ORB,
                    append_research_stamp_and_contract_gates,
                )

                for _row in merged_list:
                    if not isinstance(_row, dict):
                        continue
                    append_research_stamp_and_contract_gates(
                        PATH_ORB,
                        _row,
                        prior_report=_row.get("quality_gate_report"),
                        stamp_target=_row,
                    )
            except Exception as _qg_exc:
                log.debug("ORB1 Breakout QG research stamp merge skipped: %s", _qg_exc)
            system._save_orb_0dte_pipeline_snapshot(
                pipeline_rows=merged_list,
                date_key=_dte_snap_date,
                total_scanned=_dte_snap_scanned,
                pipeline_extra={
                    "dte0_pipeline_collection_count": len(ranked_dte_collection),
                    "dte0_pipeline_convex_qualified_count": int(
                        getattr(system, "_dte0_pipeline_convex_qualified_count", 0) or 0
                    ),
                    "dte0_convex_rejected_count": int(
                        getattr(system, "_dte0_convex_rejected_count", 0) or 0
                    ),
                    "dte0_pipeline_hard_gate_passed_count": int(
                        getattr(system, "_dte0_pipeline_hard_gate_passed_count", 0) or 0
                    ),
                    "dte0_pipeline_execution_queue_count": int(
                        getattr(system, "_dte0_pipeline_execution_queue_count", 0) or 0
                    ),
                    "pipeline_stage_tally": dict(pipeline_stage_tally),
                    "shared_so_rank_before_convex": True,
                    "options_microstructure_enrich": True,
                },
            )
        except Exception as _snap_exc:
            log.debug("ORB1 Breakout execution-window snapshot skipped: %s", _snap_exc)

    return {
        "dte0_signals_list": dte0_signals_list,
        "dte0_signals_qualified": len(dte0_signals_list),
        "dte0_collection_candidates": dte0_collection_candidates,
        "dte0_convex_passed": dte0_convex_passed,
        "dte0_hard_gate_passed": dte0_hard_gate_passed,
        "dte0_pending_execution": dte0_pending_execution,
        "hard_gated_symbols": hard_gated_symbols,
    }
