"""Exact-contract option premium resolution for EOD flatten (ORB + Trendline).

Never uses underlying stock/ETF quote as option exit premium.
Never uses synthetic / mark / mid / last-only / cached chain without fresh bridge.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Tuple

log = logging.getLogger(__name__)


async def resolve_eod_exit_premiums(
    *,
    system: Any,
    positions: List[Any],
) -> Tuple[Dict[str, float], List[Dict[str, Any]]]:
    """Return (trade_id -> bid premium, unresolved exposure rows)."""
    from modules.active_option_contract_registry import ActiveOptionContractRegistry
    from modules.dte0_exit_monitor_config import resolve_exit_monitor_config
    from modules.exact_contract_option_quotes import (
        fetch_chain_bridge_bids,
        fetch_exact_contract_quotes,
    )
    from modules.exit_grade_policy import classify_exit_grade_source, may_trigger_normal_profit_exit

    cfg = resolve_exit_monitor_config()
    registry = ActiveOptionContractRegistry()
    mode = str(getattr(system, "mode", None) or "")
    try:
        from modules.runtime_mode import resolve_runtime_mode

        mode = str(resolve_runtime_mode() or mode or "demo")
    except Exception:
        mode = mode or "demo"
    contracts = registry.sync_from_positions(positions, mode=mode)
    symbols = registry.unique_option_symbols()
    premiums: Dict[str, float] = {}
    unresolved: List[Dict[str, Any]] = []

    if not positions:
        return premiums, unresolved

    quote_result = await fetch_exact_contract_quotes(
        symbols,
        data_manager=getattr(system, "data_manager", None),
        timeout_sec=cfg.exit_quote_timeout_sec,
        max_age_sec=cfg.exit_quote_max_age_sec,
        concurrency=cfg.quote_batch_concurrency,
    )
    quotes = getattr(quote_result, "quotes", None) or {}

    missing: List[Any] = []
    for c in contracts:
        q = quotes.get(c.option_symbol)
        bid = getattr(q, "bid", None) if q is not None else None
        src = str(getattr(q, "source", "") or "exact_quote") if q is not None else ""
        age_sec = None
        if q is not None and getattr(q, "age_ms", None) is not None:
            age_sec = float(q.age_ms) / 1000.0
        rejected = bool(getattr(q, "rejected", False)) if q is not None else True
        verdict = classify_exit_grade_source(
            premium_source=src or "exact_quote",
            raw_bid=bid,
            quote_age_sec=age_sec,
            max_age_sec=cfg.exit_quote_max_age_sec,
            contract_matched=True,
            skip_cache_bridge=True,
        )
        if (not rejected) and may_trigger_normal_profit_exit(verdict) and verdict.bid is not None:
            premiums[c.trade_id] = float(verdict.bid)
            log.info(
                "EOD_EXACT_CONTRACT_QUOTE | trade_id=%s | occ=%s | bid=%s | source=%s",
                c.trade_id,
                c.option_symbol,
                verdict.bid,
                src,
            )
        else:
            pos = next(
                (p for p in positions if str(getattr(p, "position_id", "")) == c.trade_id),
                None,
            )
            if pos is not None:
                missing.append(pos)

    # Positions with no OCC still unresolved
    contract_ids = {c.trade_id for c in contracts}
    for p in positions:
        tid = str(getattr(p, "position_id", "") or "")
        if tid and tid not in contract_ids and tid not in premiums:
            missing.append(p)

    if missing and cfg.force_chain_refresh_bridge:
        try:
            bridged = await fetch_chain_bridge_bids(
                system,
                missing,
                max_age_sec=cfg.exit_quote_max_age_sec,
                skip_cache=True,
            )
            for i, (tid, bid) in enumerate((bridged or {}).items()):
                verdict = classify_exit_grade_source(
                    premium_source="fresh_chain_refresh",
                    raw_bid=bid,
                    quote_age_sec=0.0,
                    max_age_sec=cfg.exit_quote_max_age_sec,
                    contract_matched=True,
                    skip_cache_bridge=True,
                )
                if may_trigger_normal_profit_exit(verdict) and verdict.bid is not None:
                    premiums[str(tid)] = float(verdict.bid)
                    log.info(
                        "EOD_EXACT_CONTRACT_BRIDGE | trade_id=%s | bid=%s | source=fresh_chain_refresh",
                        tid,
                        bid,
                    )
                if i % 4 == 0:
                    await asyncio.sleep(0)
        except Exception as br_err:
            log.warning("EOD_EXACT_CONTRACT_BRIDGE | failed | err=%s", br_err)

    for p in positions:
        tid = str(getattr(p, "position_id", "") or "")
        if tid not in premiums:
            row = {
                "trade_id": tid,
                "symbol": str(getattr(p, "symbol", "") or ""),
                "strategy": str(getattr(p, "strategy_type", "") or ""),
                "reason": "no_valid_option_bid",
            }
            unresolved.append(row)
            log.error(
                "EOD_UNRESOLVED_EXPOSURE | trade_id=%s | symbol=%s | reason=no_valid_option_bid",
                tid,
                getattr(p, "symbol", ""),
            )

    return premiums, unresolved
