#!/usr/bin/env python3
"""PrimeTradingSystem → profit_optimizer bridge (read-only flight recorder hooks)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from zoneinfo import ZoneInfo

from profit_optimizer.metadata_extract import (
    build_contract_identity,
    build_selector_metadata_0dte,
    build_selector_metadata_so,
)
from profit_optimizer.profit_optimizer import get_profit_optimizer, safe_observe
from profit_optimizer.so_premium_shadow import build_paired_contract_identity, index_dte0_signal

log = logging.getLogger(__name__)
_PT = ZoneInfo("America/Los_Angeles")


def _f(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _resolve_position_size_usd(
    *,
    contracts: int,
    entry_premium: float,
    entry_cost: Any = None,
    estimated_total_cost: Any = None,
) -> Optional[float]:
    """Persist true fill notional when estimated_total_cost is a stale pre-clamp budget.

    Aug 7 IREN: contracts=17 but estimated_total_cost=$626 (budget qty 40). Prefer
    contracts × premium × 100 (+ opening fees) when implied qty diverges.
    """
    try:
        from easyTrendline.trendline_options_executor import resolve_true_position_size_usd

        return resolve_true_position_size_usd(
            contracts=int(contracts or 0),
            entry_premium=float(entry_premium or 0.0),
            entry_cost=_f(entry_cost),
            estimated_total_cost=_f(estimated_total_cost),
        )
    except Exception:
        qty = int(contracts or 0)
        prem = float(entry_premium or 0.0)
        if qty > 0 and prem > 0:
            return float(qty * prem * 100.0)
        return _f(entry_cost) or _f(estimated_total_cost)


def _entry_time_iso(position: Any) -> str:
    et = getattr(position, "entry_time", None)
    if et is not None and hasattr(et, "isoformat"):
        try:
            return et.isoformat()
        except Exception:
            pass
    return datetime.now(timezone.utc).isoformat()


def _first_leg(norm: Dict[str, Any]) -> Dict[str, Any]:
    legs = norm.get("legs")
    if isinstance(legs, list) and legs and isinstance(legs[0], dict):
        return legs[0]
    return {}


def _extract_entry_quotes(
    pos_meta: Optional[Dict[str, Any]],
    position: Any,
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    norm = pos_meta.get("normalized_options") if isinstance(pos_meta, dict) else {}
    if not isinstance(norm, dict):
        norm = {}
    exec_pl = pos_meta.get("execution_payload") if isinstance(pos_meta, dict) else {}
    if not isinstance(exec_pl, dict):
        exec_pl = {}
    leg0 = _first_leg(norm)
    # Prefer true entry quotes; fall back to representative bid/ask stamped on fill meta.
    bid = _f(
        leg0.get("bid")
        or exec_pl.get("entry_bid")
        or getattr(position, "entry_bid", None)
        or (pos_meta or {}).get("representative_bid")
        or (pos_meta or {}).get("entry_bid")
        or exec_pl.get("representative_bid")
        or leg0.get("representative_bid")
    )
    ask = _f(
        leg0.get("ask")
        or exec_pl.get("entry_ask")
        or getattr(position, "entry_ask", None)
        or (pos_meta or {}).get("representative_ask")
        or (pos_meta or {}).get("entry_ask")
        or exec_pl.get("representative_ask")
        or leg0.get("representative_ask")
    )
    mid = _f(
        leg0.get("mid")
        or leg0.get("mid_price")
        or exec_pl.get("entry_mid")
        or getattr(position, "entry_mid", None)
        or (pos_meta or {}).get("entry_mid")
    )
    if mid is None and bid is not None and ask is not None:
        mid = (float(bid) + float(ask)) / 2.0
    return bid, ask, mid


def ensure_profit_optimizer(system: Any):
    po = get_profit_optimizer()
    if po is None:
        return None
    session_date = datetime.now(_PT).strftime("%Y-%m-%d")
    safe_observe("begin_session", lambda: po.begin_session(session_date))
    return po


def _seal_trade_profile_metadata(
    system: Any,
    dest: Dict[str, Any],
    *,
    signal: Any,
    pos_meta: Any,
    symbol: str,
    trade_id: str,
    path: str,
    option_side: str,
) -> None:
    """Copy the Trade Profile keys from the decision row onto this fill.

    The handoff and the profile score are the sources. A later fill stamp
    does not replace a value those sources already have.
    """
    if not isinstance(dest, dict):
        return
    try:
        from easy0DTE.modules.trade_profile import stamp_flight_profile_keys
    except Exception:
        return
    sig_meta = getattr(signal, "metadata", None)
    if not isinstance(sig_meta, dict):
        sig_meta = {}
    snap = pos_meta.get("signal_snapshot") if isinstance(pos_meta, dict) else None
    setup = sig_meta.get("setup_payload") if isinstance(sig_meta.get("setup_payload"), dict) else None
    qg = sig_meta.get("qg_admit") if isinstance(sig_meta.get("qg_admit"), dict) else None
    handoff = None
    lookup = getattr(system, "_lookup_po_entry_features_cache", None)
    if callable(lookup):
        try:
            handoff = lookup(
                symbol=str(symbol or ""),
                path=str(path or ""),
                trade_id=str(trade_id or "") or None,
                freeze_at=str(sig_meta.get("po_decision_freeze_at") or ""),
                option_side=str(option_side or ""),
            )
        except Exception:
            handoff = None
    stamp_flight_profile_keys(
        dest,
        handoff,
        sig_meta,
        snap if isinstance(snap, dict) else None,
        setup,
        qg,
        pos_meta if isinstance(pos_meta, dict) else None,
    )


def track_0dte_fill(
    system: Any,
    *,
    position: Any,
    signal: Any,
    entry_spot: float,
    trade_id: str,
    pos_meta: Optional[Dict[str, Any]] = None,
    option_type: str = "",
    strategy_type: str = "",
) -> None:
    po = ensure_profit_optimizer(system)
    tid = str(trade_id or getattr(position, "position_id", "") or "").strip()
    if not po or not tid:
        return
    meta = pos_meta if isinstance(pos_meta, dict) else {}
    norm = meta.get("normalized_options") if isinstance(meta.get("normalized_options"), dict) else {}
    exec_pl = meta.get("execution_payload") if isinstance(meta.get("execution_payload"), dict) else {}
    leg0 = _first_leg(norm)
    symbol = str(getattr(position, "symbol", "") or getattr(signal, "symbol", "") or "").upper()
    direction = str(getattr(signal, "direction", "LONG") or "LONG").upper()
    entry_premium = _f(
        getattr(position, "entry_price", None)
        or norm.get("entry_value")
        or exec_pl.get("estimated_contract_price")
        or leg0.get("entry_price")
    )
    if entry_premium is None or entry_premium <= 0:
        return
    strike = _f(leg0.get("strike") or getattr(position, "strike", None))
    expiration = str(
        norm.get("expiration_ymd")
        or getattr(position, "expiration", "")
        or exec_pl.get("expiration_ymd")
        or exec_pl.get("expiry_ymd")
        or ""
    )
    contracts = int(getattr(position, "quantity", 0) or norm.get("contracts") or 1)
    bid, ask, mid = _extract_entry_quotes(meta, position)
    contract_identity = build_contract_identity(
        position=position,
        norm=norm,
        exec_pl=exec_pl,
        signal=signal,
    )
    selector_metadata = build_selector_metadata_0dte(signal=signal, norm=norm, pos_meta=meta)
    if strategy_type:
        selector_metadata["strategy_type"] = str(strategy_type)
    try:
        from profit_optimizer.path_chain_skew import (
            collect_microstructure_from_system,
            merge_review_fields_onto_dict,
            stamp_review_microstructure,
        )

        option_side = str(
            option_type
            or getattr(position, "option_side", None)
            or leg0.get("option_type")
            or leg0.get("option_side")
            or ""
        )
        merge_review_fields_onto_dict(
            selector_metadata,
            collect_microstructure_from_system(system, symbol),
            meta.get("path_chain_skew") if isinstance(meta.get("path_chain_skew"), dict) else {},
            meta,
            exec_pl,
            leg0,
            option_side=option_side,
            overwrite=False,
        )
        # Depth from fill leg when present.
        for dk, src_keys in (
            ("bid_size", ("bid_size", "bidSize")),
            ("ask_size", ("ask_size", "askSize")),
            ("cumulative_volume", ("cumulative_volume", "volume", "totalVolume")),
        ):
            if selector_metadata.get(dk) is not None:
                continue
            for sk in src_keys:
                v = _f(leg0.get(sk) or exec_pl.get(sk) or meta.get(sk))
                if v is not None:
                    selector_metadata[dk] = v
                    break
        stamp_review_microstructure(selector_metadata, option_side=option_side, overwrite=False)
        # Force session MACD/SMA onto entry (ensure bars if cache empty).
        try:
            tech_row = {
                "symbol": symbol,
                "current_price": float(entry_spot or 0.0) or None,
            }
            stamp_fn = getattr(system, "_stamp_session_tech_on_po_row", None)
            if callable(stamp_fn):
                stamp_fn(tech_row, symbol=symbol, ensure_bars=True)
                merge_review_fields_onto_dict(
                    selector_metadata, tech_row, option_side=option_side, overwrite=False
                )
        except Exception:
            pass
    except Exception:
        pass
    try:
        from modules.calendar_expiry_metadata import (
            EXPIRY_TRADE_FIELD_KEYS,
            copy_expiry_fields,
            merge_expiry_trade_fields,
        )

        expiry_meta: Dict[str, Any] = {}
        copy_expiry_fields(norm, expiry_meta)
        copy_expiry_fields(exec_pl, expiry_meta)
        copy_expiry_fields(meta, expiry_meta)
        merge_expiry_trade_fields(
            expiry_meta,
            selected_expiry=expiry_meta.get("selected_expiry_ymd") or expiration,
            requested_expiry=expiry_meta.get("requested_expiry_ymd"),
            session_date=expiry_meta.get("session_date"),
            prefer_existing=True,
        )
        for _ek in EXPIRY_TRADE_FIELD_KEYS:
            if _ek in expiry_meta:
                selector_metadata[_ek] = expiry_meta.get(_ek)
    except Exception:
        expiry_meta = {}
    extra = {
        "option_type": str(option_type or "").lower(),
        "strategy_type": str(strategy_type or ""),
        "entry_premium_source": str(meta.get("entry_premium_source") or exec_pl.get("premium_source") or ""),
        **{k: expiry_meta[k] for k in expiry_meta},
    }
    try:
        from profit_optimizer.path_chain_skew import REVIEW_MICROSTRUCTURE_KEYS

        for k in REVIEW_MICROSTRUCTURE_KEYS:
            if selector_metadata.get(k) is not None and extra.get(k) is None:
                extra[k] = selector_metadata.get(k)
    except Exception:
        pass
    _seal_trade_profile_metadata(
        system,
        selector_metadata,
        signal=signal,
        pos_meta=meta,
        symbol=symbol,
        trade_id=tid,
        path="orb",
        option_side=str(option_type or getattr(position, "option_side", "") or ""),
    )

    def _run() -> None:
        po.on_orb_0dte_executed(
            trade_id=tid,
            symbol=symbol,
            direction=direction,
            entry_time=_entry_time_iso(position),
            underlying_entry_price=float(entry_spot or 0.0),
            strike=strike,
            expiration=expiration,
            entry_bid=bid,
            entry_ask=ask,
            entry_mid=mid,
            entry_premium=float(entry_premium),
            contracts=contracts,
            position_size=_resolve_position_size_usd(
                contracts=contracts,
                entry_premium=float(entry_premium),
                entry_cost=getattr(position, "entry_cost", None),
                estimated_total_cost=(
                    (getattr(position, "metadata", None) or {}).get("execution_payload") or {}
                ).get("estimated_total_cost")
                if isinstance(getattr(position, "metadata", None), dict)
                else None,
            ),
            contract_identity=contract_identity,
            selector_metadata=selector_metadata,
            extra=extra,
        )

    safe_observe("track_0dte_fill", _run)


def track_trendline_fill(
    system: Any,
    *,
    position: Any,
    signal: Any,
    trade_id: str,
    order_payload: Optional[Dict[str, Any]] = None,
    entry_spot: float = 0.0,
) -> None:
    po = ensure_profit_optimizer(system)
    tid = str(trade_id or getattr(position, "position_id", "") or "").strip()
    if not po or not tid:
        return
    meta = getattr(position, "metadata", None)
    if not isinstance(meta, dict):
        meta = {}
    norm = meta.get("normalized_options") if isinstance(meta.get("normalized_options"), dict) else {}
    exec_pl = order_payload if isinstance(order_payload, dict) else {}
    if not exec_pl and isinstance(meta.get("execution_payload"), dict):
        exec_pl = meta.get("execution_payload") or {}
    leg0 = _first_leg(norm)
    symbol = str(getattr(position, "symbol", "") or getattr(signal, "symbol", "") or "").upper()
    direction = "CALL" if str(getattr(signal, "option_side", "call") or "call").lower() == "call" else "PUT"
    entry_premium = _f(
        getattr(position, "entry_price", None)
        or norm.get("entry_value")
        or exec_pl.get("estimated_contract_price")
        or leg0.get("entry_price")
    )
    if entry_premium is None or entry_premium <= 0:
        return
    strike = _f(leg0.get("strike") or exec_pl.get("strike") or getattr(position, "strike", None))
    expiration = str(
        norm.get("expiration_ymd")
        or exec_pl.get("expiry_ymd")
        or exec_pl.get("expiration_ymd")
        or getattr(position, "expiration", "")
        or ""
    )
    contracts = int(getattr(position, "quantity", 0) or exec_pl.get("quantity") or 1)
    bid, ask, mid = _extract_entry_quotes(meta, position)
    contract_identity = build_contract_identity(
        position=position,
        norm=norm,
        exec_pl=exec_pl,
        signal=signal,
    )
    selector_metadata = build_selector_metadata_0dte(signal=signal, norm=norm, pos_meta=meta)
    selector_metadata["strategy_type"] = "trendline_0dte"
    sig_meta = getattr(signal, "metadata", None)
    if isinstance(sig_meta, dict):
        for k in ("setup_type", "trigger_direction", "break_archetype", "trendline_mode"):
            if sig_meta.get(k) is not None:
                selector_metadata[k] = sig_meta.get(k)
    try:
        from profit_optimizer.path_chain_skew import (
            REVIEW_MICROSTRUCTURE_KEYS,
            collect_microstructure_from_system,
            merge_review_fields_onto_dict,
            stamp_review_microstructure,
        )

        option_side_tl = str(
            getattr(signal, "option_side", None)
            or direction
            or leg0.get("option_type")
            or ""
        )
        merge_review_fields_onto_dict(
            selector_metadata,
            collect_microstructure_from_system(system, symbol),
            meta.get("path_chain_skew") if isinstance(meta.get("path_chain_skew"), dict) else {},
            meta,
            exec_pl,
            leg0,
            isinstance(sig_meta, dict) and sig_meta or {},
            option_side=option_side_tl,
            overwrite=False,
        )
        for dk, src_keys in (
            ("bid_size", ("bid_size", "bidSize")),
            ("ask_size", ("ask_size", "askSize")),
            ("cumulative_volume", ("cumulative_volume", "volume", "totalVolume")),
        ):
            if selector_metadata.get(dk) is not None:
                continue
            for sk in src_keys:
                v = _f(leg0.get(sk) or exec_pl.get(sk) or meta.get(sk))
                if v is not None:
                    selector_metadata[dk] = v
                    break
        stamp_review_microstructure(selector_metadata, option_side=option_side_tl, overwrite=False)
        try:
            tech_row = {
                "symbol": symbol,
                "current_price": float(entry_spot or 0.0) or None,
            }
            stamp_fn = getattr(system, "_stamp_session_tech_on_po_row", None)
            if callable(stamp_fn):
                stamp_fn(tech_row, symbol=symbol, ensure_bars=True)
                merge_review_fields_onto_dict(
                    selector_metadata, tech_row, option_side=option_side_tl, overwrite=False
                )
        except Exception:
            pass
    except Exception:
        REVIEW_MICROSTRUCTURE_KEYS = ()  # type: ignore
    try:
        from modules.calendar_expiry_metadata import (
            EXPIRY_TRADE_FIELD_KEYS,
            copy_expiry_fields,
            merge_expiry_trade_fields,
        )

        expiry_meta_tl: Dict[str, Any] = {}
        copy_expiry_fields(norm, expiry_meta_tl)
        copy_expiry_fields(exec_pl, expiry_meta_tl)
        copy_expiry_fields(meta, expiry_meta_tl)
        merge_expiry_trade_fields(
            expiry_meta_tl,
            selected_expiry=expiry_meta_tl.get("selected_expiry_ymd") or expiration,
            requested_expiry=expiry_meta_tl.get("requested_expiry_ymd"),
            session_date=expiry_meta_tl.get("session_date"),
            prefer_existing=True,
        )
        for _ek in EXPIRY_TRADE_FIELD_KEYS:
            if _ek in expiry_meta_tl:
                selector_metadata[_ek] = expiry_meta_tl.get(_ek)
    except Exception:
        expiry_meta_tl = {}
    _seal_trade_profile_metadata(
        system,
        selector_metadata,
        signal=signal,
        pos_meta=meta,
        symbol=symbol,
        trade_id=tid,
        path="trendline",
        option_side=str(getattr(signal, "option_side", "") or direction or ""),
    )

    def _run() -> None:
        extra_tl = {k: expiry_meta_tl[k] for k in expiry_meta_tl}
        try:
            for k in REVIEW_MICROSTRUCTURE_KEYS:
                if selector_metadata.get(k) is not None and extra_tl.get(k) is None:
                    extra_tl[k] = selector_metadata.get(k)
        except Exception:
            pass
        po.on_trendline_0dte_executed(
            trade_id=tid,
            symbol=symbol,
            direction=direction,
            entry_time=_entry_time_iso(position),
            underlying_entry_price=float(entry_spot or 0.0),
            strike=strike,
            expiration=expiration,
            entry_bid=bid,
            entry_ask=ask,
            entry_mid=mid,
            entry_premium=float(entry_premium),
            contracts=contracts,
            position_size=_resolve_position_size_usd(
                contracts=contracts,
                entry_premium=float(entry_premium),
                entry_cost=getattr(position, "entry_cost", None),
                estimated_total_cost=(
                    (getattr(position, "metadata", None) or {}).get("execution_payload") or {}
                ).get("estimated_total_cost")
                if isinstance(getattr(position, "metadata", None), dict)
                else None,
            ),
            contract_identity=contract_identity,
            selector_metadata=selector_metadata,
            extra={"source_path": "trendline_0dte", **extra_tl},
        )

    safe_observe("track_trendline_fill", _run)


def track_breakout_fill(
    system: Any,
    *,
    signal: Any,
    result: Any,
    position: Any = None,
    trade_id: str = "",
    order_payload: Optional[Dict[str, Any]] = None,
    entry_spot: float = 0.0,
) -> None:
    """Record a Breakout 0DTE fill into profit_optimizer (shadow flight recorder)."""
    po = ensure_profit_optimizer(system)
    tid = str(
        trade_id
        or getattr(result, "position_id", "")
        or (getattr(position, "position_id", "") if position is not None else "")
        or ""
    ).strip()
    if not po or not tid:
        return
    try:
        from profit_optimizer.profit_optimizer_models import breakout_profit_optimizer_enabled

        if not breakout_profit_optimizer_enabled():
            return
    except Exception:
        return

    # Prefer live unified position when available (richer contract identity / quotes).
    if position is None:
        try:
            from modules.unified_0dte_account import get_unified_position

            position = get_unified_position(system, tid)
        except Exception:
            position = None

    order_payload = (
        order_payload
        if isinstance(order_payload, dict)
        else (getattr(result, "order_payload", None) or {})
    )
    meta: Dict[str, Any] = {}
    if position is not None and isinstance(getattr(position, "metadata", None), dict):
        meta = dict(position.metadata or {})
    norm = meta.get("normalized_options") if isinstance(meta.get("normalized_options"), dict) else {}
    exec_pl = order_payload if isinstance(order_payload, dict) else {}
    if not exec_pl and isinstance(meta.get("execution_payload"), dict):
        exec_pl = meta.get("execution_payload") or {}
    leg0 = _first_leg(norm)
    symbol = str(
        getattr(position, "symbol", "")
        or getattr(signal, "symbol", "")
        or exec_pl.get("symbol")
        or ""
    ).upper()
    direction = (
        "CALL"
        if str(getattr(signal, "option_side", "call") or "call").lower() == "call"
        else "PUT"
    )
    entry_premium = _f(
        getattr(position, "entry_price", None)
        or norm.get("entry_value")
        or exec_pl.get("estimated_contract_price")
        or leg0.get("entry_price")
    )
    if entry_premium is None or entry_premium <= 0:
        return
    if entry_spot <= 0:
        entry_spot = float(
            _f(
                (getattr(signal, "metadata", None) or {}).get("entry_underlying_price")
                if isinstance(getattr(signal, "metadata", None), dict)
                else None
            )
            or _f(getattr(getattr(signal, "break_event", None), "close_price", None))
            or 0.0
        )
    contracts = int(
        getattr(position, "quantity", 0) or exec_pl.get("quantity") or 1
    )
    strike = _f(
        leg0.get("strike")
        or exec_pl.get("listed_exchange_strike")
        or exec_pl.get("strike")
        or getattr(position, "strike", None)
    )
    expiration = str(
        norm.get("expiration_ymd")
        or exec_pl.get("expiry_ymd")
        or exec_pl.get("expiration_ymd")
        or getattr(position, "expiration", "")
        or ""
    )
    bid, ask, mid = _extract_entry_quotes(meta, position) if position is not None else (None, None, None)
    contract_identity = build_contract_identity(
        position=position,
        norm=norm,
        exec_pl=exec_pl,
        signal=signal,
    )
    selector_metadata = build_selector_metadata_0dte(signal=signal, norm=norm, pos_meta=meta)
    selector_metadata["strategy_type"] = "breakout_0dte"
    selector_metadata["setup_type"] = selector_metadata.get("setup_type") or "orb_range_breakout"
    sig_meta = getattr(signal, "metadata", None)
    if isinstance(sig_meta, dict):
        for k in ("setup_type", "trigger_direction", "break_distance_pct", "continuation_pct", "quality_score"):
            if sig_meta.get(k) is not None:
                selector_metadata[k] = sig_meta.get(k)
    try:
        from profit_optimizer.path_chain_skew import (
            REVIEW_MICROSTRUCTURE_KEYS,
            collect_microstructure_from_system,
            merge_review_fields_onto_dict,
            stamp_review_microstructure,
        )

        option_side_bo = str(
            direction
            or getattr(position, "option_side", None)
            or getattr(signal, "option_side", None)
            or leg0.get("option_type")
            or ""
        )
        merge_review_fields_onto_dict(
            selector_metadata,
            collect_microstructure_from_system(system, symbol),
            meta.get("path_chain_skew") if isinstance(meta.get("path_chain_skew"), dict) else {},
            meta,
            exec_pl,
            leg0,
            option_side=option_side_bo,
            overwrite=False,
        )
        for dk, src_keys in (
            ("bid_size", ("bid_size", "bidSize")),
            ("ask_size", ("ask_size", "askSize")),
            ("cumulative_volume", ("cumulative_volume", "volume", "totalVolume")),
        ):
            if selector_metadata.get(dk) is not None:
                continue
            for sk in src_keys:
                v = _f(leg0.get(sk) or exec_pl.get(sk) or meta.get(sk))
                if v is not None:
                    selector_metadata[dk] = v
                    break
        stamp_review_microstructure(selector_metadata, option_side=option_side_bo, overwrite=False)
        try:
            tech_row = {
                "symbol": symbol,
                "current_price": float(entry_spot or 0.0) or None,
            }
            stamp_fn = getattr(system, "_stamp_session_tech_on_po_row", None)
            if callable(stamp_fn):
                stamp_fn(tech_row, symbol=symbol, ensure_bars=True)
                merge_review_fields_onto_dict(
                    selector_metadata, tech_row, option_side=option_side_bo, overwrite=False
                )
        except Exception:
            pass
    except Exception:
        REVIEW_MICROSTRUCTURE_KEYS = ()  # type: ignore
    try:
        from modules.calendar_expiry_metadata import (
            EXPIRY_TRADE_FIELD_KEYS,
            copy_expiry_fields,
            merge_expiry_trade_fields,
        )

        expiry_meta_bo: Dict[str, Any] = {}
        copy_expiry_fields(norm, expiry_meta_bo)
        copy_expiry_fields(exec_pl, expiry_meta_bo)
        copy_expiry_fields(meta, expiry_meta_bo)
        merge_expiry_trade_fields(
            expiry_meta_bo,
            selected_expiry=expiry_meta_bo.get("selected_expiry_ymd") or expiration,
            requested_expiry=expiry_meta_bo.get("requested_expiry_ymd"),
            session_date=expiry_meta_bo.get("session_date"),
            prefer_existing=True,
        )
        for _ek in EXPIRY_TRADE_FIELD_KEYS:
            if _ek in expiry_meta_bo:
                selector_metadata[_ek] = expiry_meta_bo.get(_ek)
    except Exception:
        expiry_meta_bo = {}

    extra_bo: Dict[str, Any] = {"source_path": "breakout_0dte", **expiry_meta_bo}
    try:
        for k in REVIEW_MICROSTRUCTURE_KEYS:
            if selector_metadata.get(k) is not None and extra_bo.get(k) is None:
                extra_bo[k] = selector_metadata.get(k)
    except Exception:
        pass
    _seal_trade_profile_metadata(
        system,
        selector_metadata,
        signal=signal,
        pos_meta=meta,
        symbol=symbol,
        trade_id=tid,
        path="breakout",
        option_side=str(getattr(signal, "option_side", "") or direction or ""),
    )

    def _run() -> None:
        if hasattr(po, "on_breakout_0dte_executed"):
            po.on_breakout_0dte_executed(
                trade_id=tid,
                symbol=symbol,
                direction=direction,
                entry_time=_entry_time_iso(position) if position is not None else datetime.now(timezone.utc).isoformat(),
                underlying_entry_price=float(entry_spot or 0.0),
                strike=strike,
                expiration=expiration,
                entry_bid=bid,
                entry_ask=ask,
                entry_mid=mid,
                entry_premium=float(entry_premium),
                contracts=contracts,
                position_size=_resolve_position_size_usd(
                    contracts=contracts,
                    entry_premium=float(entry_premium),
                    entry_cost=getattr(position, "entry_cost", None) if position is not None else None,
                    estimated_total_cost=exec_pl.get("estimated_total_cost"),
                ),
                contract_identity=contract_identity,
                selector_metadata=selector_metadata,
                extra=extra_bo,
            )
        else:
            po.on_trendline_0dte_executed(
                trade_id=tid,
                symbol=symbol,
                direction=direction,
                entry_time=_entry_time_iso(position) if position is not None else datetime.now(timezone.utc).isoformat(),
                underlying_entry_price=float(entry_spot or 0.0),
                strike=strike,
                expiration=expiration,
                entry_bid=bid,
                entry_ask=ask,
                entry_mid=mid,
                entry_premium=float(entry_premium),
                contracts=contracts,
                position_size=_resolve_position_size_usd(
                    contracts=contracts,
                    entry_premium=float(entry_premium),
                    entry_cost=getattr(position, "entry_cost", None) if position is not None else None,
                    estimated_total_cost=exec_pl.get("estimated_total_cost"),
                ),
                contract_identity=contract_identity,
                selector_metadata=selector_metadata,
                extra=extra_bo,
            )

    safe_observe("track_breakout_fill", _run)


def track_so_fill(
    system: Any,
    *,
    trade_id: str,
    symbol: str,
    direction: str,
    entry_time: str,
    underlying_entry_price: float,
    contracts: int = 0,
    position_size: Optional[float] = None,
    signal: Any = None,
    selector_metadata: Optional[Dict[str, Any]] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    po = ensure_profit_optimizer(system)
    tid = str(trade_id or "").strip()
    if not po or not tid:
        return
    sel = selector_metadata if isinstance(selector_metadata, dict) else build_selector_metadata_so(signal=signal)

    def _run() -> None:
        po.on_orb_so_executed(
            trade_id=tid,
            symbol=str(symbol or "").upper(),
            direction=str(direction or "LONG").upper(),
            entry_time=entry_time,
            underlying_entry_price=float(underlying_entry_price or 0.0),
            contracts=int(contracts or 0),
            position_size=position_size,
            selector_metadata=sel,
            extra=extra,
        )

    safe_observe("track_so_fill", _run)


def index_dte0_collection(system: Any, signals: Any) -> None:
    if not signals:
        return
    idx = getattr(system, "_profit_optimizer_dte0_index", None)
    if not isinstance(idx, dict):
        idx = {}
        system._profit_optimizer_dte0_index = idx
    for sig in signals:
        row = index_dte0_signal(sig)
        if not row:
            continue
        key = (str(row.get("symbol") or "").upper(), str(row.get("direction") or "LONG").upper())
        idx[key] = row


async def attach_so_premium_shadow(
    system: Any,
    *,
    trade_id: str,
    symbol: str,
    option_type: str,
    strike: float,
    expiration: str,
    entry_premium: float,
    option_symbol: str = "",
    selector_metadata: Optional[Dict[str, Any]] = None,
) -> None:
    po = ensure_profit_optimizer(system)
    tid = str(trade_id or "").strip()
    if not po or not tid or float(entry_premium or 0.0) <= 0:
        return
    contract_identity = build_paired_contract_identity(
        symbol=symbol,
        option_type=option_type,
        strike=float(strike),
        expiration=str(expiration or ""),
        option_symbol=option_symbol,
        entry_premium=float(entry_premium),
    )

    def _run() -> None:
        po.attach_so_premium_shadow(
            trade_id=tid,
            contract_identity=contract_identity,
            entry_premium=float(entry_premium),
            expiration=str(expiration or ""),
            strike=float(strike),
            selector_metadata=selector_metadata,
        )

    safe_observe("attach_so_premium_shadow", _run)


def observe_so_tick(system: Any, symbol: str, market_data: Any) -> None:
    po = ensure_profit_optimizer(system)
    if not po:
        return
    md = market_data if isinstance(market_data, dict) else {}
    trade_id = str(md.get("trade_id") or md.get("position_id") or "").strip()
    if not trade_id:
        return
    px = _f(md.get("price") or md.get("last") or md.get("underlying_price"))
    if px is None:
        return
    vol = _f(md.get("volume"))

    def _run() -> None:
        po.observe_so_price_tick(
            trade_id=trade_id,
            symbol=str(symbol or "").upper(),
            underlying_price=float(px),
            volume=vol,
        )

    safe_observe("observe_so_tick", _run)


def observe_so_exit(system: Any, symbol: str, market_data: Any, decision: Any) -> None:
    po = ensure_profit_optimizer(system)
    if not po:
        return
    md = market_data if isinstance(market_data, dict) else {}
    dec = decision if isinstance(decision, dict) else {}
    trade_id = str(md.get("trade_id") or md.get("position_id") or dec.get("trade_id") or "").strip()
    if not trade_id:
        return
    exit_underlying = _f(md.get("price") or md.get("last") or dec.get("exit_underlying"))
    exit_reason = str(dec.get("exit_reason") or dec.get("reason") or "")
    realized = _f(dec.get("realized_pnl_pct") or dec.get("pnl_pct"))

    def _run() -> None:
        po.observe_exit(
            trade_id=trade_id,
            exit_underlying=exit_underlying,
            exit_reason=exit_reason,
            realized_pnl_pct=realized,
            extra={"symbol": str(symbol or "").upper()},
        )

    safe_observe("observe_so_exit", _run)


_SESSION_TICK_FLOATS = (
    "rsi",
    "macd",
    "macd_signal",
    "macd_histogram",
    "vwap",
    "vwap_distance_pct",
    "volume_1m",
    "volume_3m",
    "volume_ratio",
    "orb_volume_ratio",
    "price_volume_efficiency_3m",
    "price_change_3m_pct",
    "atr",
    "bollinger_width",
    "bollinger_position",
    "distance_from_vwap_atr",
)
_SESSION_TICK_FLAGS = (
    "rsi_missing",
    "macd_missing",
    "vwap_missing",
    "volume_ratio_missing",
)
_SESSION_TICK_CLOCKS = (
    "session_tech_bar_at",
    "session_tech_asof_at",
    "pve3_bar_at",
)


def _blank_session_tick(dest: Dict[str, Any]) -> None:
    for key in _SESSION_TICK_FLOATS:
        dest[key] = None
    for key in _SESSION_TICK_FLAGS:
        dest[key] = True
    for key in _SESSION_TICK_CLOCKS:
        dest[key] = None


def stamp_live_rsi(system: Any, symbol: str, dest: Dict[str, Any], *, price: Optional[float] = None) -> None:
    """Stamp this tick from the cached 1m session tech. No bar fetch and no broker read.

    RSI, MACD, VWAP, PVE3, Bollinger, and VWAP-ATR distance are the same
    in-memory 1m bars the PO snapshots use. No bar fetch and no broker read.
    A missing value stays null with its missing flag.
    """
    if not isinstance(dest, dict):
        return
    sym = str(symbol or "").strip().upper()
    if not sym:
        _blank_session_tick(dest)
        return
    try:
        from modules.session_tech_indicators import get_session_tech, session_tech_enabled
    except Exception:
        _blank_session_tick(dest)
        return
    if not session_tech_enabled():
        _blank_session_tick(dest)
        return
    px: Optional[float] = None
    try:
        if price is not None and float(price) > 0:
            px = float(price)
    except (TypeError, ValueError):
        px = None
    try:
        tech = get_session_tech(system, sym, price=px)
    except Exception:
        tech = None
    if not isinstance(tech, dict):
        _blank_session_tick(dest)
        return
    for key in _SESSION_TICK_CLOCKS:
        clock = tech.get(key)
        dest[key] = clock if clock not in (None, "") else None
    if dest.get("pve3_bar_at") in (None, "") and dest.get("session_tech_bar_at") not in (None, ""):
        dest["pve3_bar_at"] = dest.get("session_tech_bar_at")
    for key in _SESSION_TICK_FLOATS:
        raw = tech.get(key)
        try:
            dest[key] = float(raw) if raw is not None else None
        except (TypeError, ValueError):
            dest[key] = None
    for key in _SESSION_TICK_FLAGS:
        flag = tech.get(key)
        dest[key] = bool(flag) if flag is not None else dest.get(key) is None
    if dest.get("rsi") is None:
        dest["rsi_missing"] = True
    if bool(tech.get("rsi_missing")):
        dest["rsi"] = None
        dest["rsi_missing"] = True
    if bool(tech.get("macd_missing")):
        dest["macd"] = None
        dest["macd_signal"] = None
        dest["macd_histogram"] = None
        dest["macd_missing"] = True
    if bool(tech.get("vwap_missing")):
        dest["vwap"] = None
        dest["vwap_distance_pct"] = None
        dest["vwap_missing"] = True
    if dest.get("orb_volume_ratio") is None and dest.get("volume_ratio") is not None:
        dest["orb_volume_ratio"] = dest.get("volume_ratio")
    if dest.get("volume_ratio") is not None:
        dest["volume_ratio_missing"] = False
    else:
        dest["volume_ratio_missing"] = True
    if bool(tech.get("volume_ratio_missing")):
        dest["volume_ratio"] = None
        dest["orb_volume_ratio"] = None
        dest["volume_ratio_missing"] = True
    if dest.get("distance_from_vwap_atr") is None and px is not None:
        vwap = dest.get("vwap")
        atr = dest.get("atr")
        try:
            if vwap is not None and atr is not None and float(atr) > 0:
                dest["distance_from_vwap_atr"] = (float(px) - float(vwap)) / float(atr)
        except (TypeError, ValueError, ZeroDivisionError):
            pass


def stamp_owned_monitor_review_keys(
    system: Any,
    dest: Dict[str, Any],
    *,
    price: Optional[float] = None,
    strike: Optional[float] = None,
    option_side: str = "",
) -> None:
    """Fill Trade Profile review keys from cache / fields already on the tick.

    Never calls a broker API. OTM uses spot+strike+side. Shares and 5m dir%
    use chain volumes already on the snap. Underlying L1 uses the equity L1
    ring already recorded from prior quote polls.
    """
    if not isinstance(dest, dict):
        return
    px = _f(price) if price is not None else _f(dest.get("underlying_price"))
    vwap = _f(dest.get("vwap"))
    atr = _f(dest.get("atr"))
    if dest.get("distance_from_vwap_atr") is None and px is not None and vwap is not None and atr is not None and atr > 0:
        dest["distance_from_vwap_atr"] = (px - vwap) / atr

    k = _f(strike) if strike is not None else _f(dest.get("strike"))
    side = str(
        option_side
        or dest.get("option_side")
        or dest.get("option_type")
        or dest.get("direction")
        or ""
    )
    if dest.get("otm_pct") is None and px is not None and k is not None:
        try:
            from easyBreakout.breakout_contract_gates import compute_otm_pct

            dest["otm_pct"] = compute_otm_pct(px, k, side)
        except Exception:
            pass

    call_v = _f(dest.get("call_chain_volume_now") or dest.get("call_chain_volume"))
    put_v = _f(dest.get("put_chain_volume_now") or dest.get("put_chain_volume"))
    if call_v is not None and put_v is not None and (call_v + put_v) > 0:
        total = call_v + put_v
        dest["call_share"] = call_v / total
        dest["put_share"] = put_v / total

    side_l = side.lower()
    now_side = put_v if "put" in side_l else call_v if "call" in side_l else None
    ago_side = _f(
        dest.get("put_chain_volume_5m_ago") if "put" in side_l else dest.get("call_chain_volume_5m_ago")
    )
    if dest.get("dir_pct_change_5m") is None and now_side is not None and ago_side is not None and ago_side > 0:
        dest["dir_pct_change_5m"] = (now_side - ago_side) / ago_side

    if dest.get("underlying_l1_imbalance") is None and system is not None:
        sym = str(dest.get("symbol") or "").strip().upper()
        if sym:
            try:
                from modules.session_tech_admit import derive_underlying_l1_features

                l1 = derive_underlying_l1_features(system, sym, quote=None)
                if l1.get("underlying_l1_imbalance") is not None:
                    dest["underlying_l1_imbalance"] = l1.get("underlying_l1_imbalance")
            except Exception:
                pass


def observe_0dte_tick(
    system: Any,
    *,
    trade_id: str,
    underlying_price: float,
    option_quote: Optional[Dict[str, Any]] = None,
    osnap: Optional[Dict[str, Any]] = None,
    quote_grade: str = "",
) -> None:
    po = ensure_profit_optimizer(system)
    tid = str(trade_id or "").strip()
    if not po or not tid:
        return

    def _run() -> None:
        und = float(underlying_price or 0.0)
        snap_in = osnap if isinstance(osnap, dict) else {}
        # Owned-tick provenance already resolved. Never initiate a broker equity
        # get_quote from observe (evaluate() is also cache-only).
        src = str(snap_in.get("underlying_source") or "")
        if und <= 0.0 and not src:
            try:
                from modules.underlying_spot_stamp import (
                    resolve_cached_underlying,
                )

                # Prefer symbol from recorder entry. Cache lookup only.
                sym = ""
                rec = po.recorder._records.get(tid) if po.recorder else None
                if rec is not None and getattr(rec, "entry", None) is not None:
                    sym = str(getattr(rec.entry, "symbol", "") or "").strip().upper()
                if sym:
                    cached = resolve_cached_underlying(system, sym)
                    if cached is not None:
                        und = float(cached)
            except Exception:
                pass
        snap_out = snap_in if isinstance(snap_in, dict) else {}
        sym = str(snap_out.get("symbol") or "").strip().upper()
        if not sym and po.recorder is not None:
            rec = po.recorder._records.get(tid)
            entry = getattr(rec, "entry", None) if rec is not None else None
            sym = str(getattr(entry, "symbol", "") or "").strip().upper()
        stamp_live_rsi(system, sym, snap_out, price=float(und or 0.0))
        rec = po.recorder._records.get(tid) if po.recorder is not None else None
        entry = getattr(rec, "entry", None) if rec is not None else None
        stamp_owned_monitor_review_keys(
            system,
            snap_out,
            price=float(und or 0.0),
            strike=_f(snap_out.get("strike") or getattr(entry, "strike", None)),
            option_side=str(
                snap_out.get("option_side")
                or snap_out.get("option_type")
                or getattr(entry, "option_side", "")
                or ""
            ),
        )
        po.observe_0dte_monitor_tick(
            trade_id=tid,
            underlying_price=float(und or 0.0),
            option_quote=option_quote if isinstance(option_quote, dict) else {},
            osnap=snap_out,
            quote_grade=str(quote_grade or ""),
        )

    safe_observe("observe_0dte_tick", _run)


def _frozen_symbol_bid_from_exit_args(extra: Optional[Dict[str, Any]]) -> Optional[float]:
    extra = extra if isinstance(extra, dict) else {}
    for key in ("frozen_symbol_bid", "entry_option_bid", "option_bid"):
        val = _f(extra.get(key))
        if val is not None and val > 0:
            return val
    oq = extra.get("option_quote")
    if isinstance(oq, dict):
        val = _f(oq.get("bid"))
        if val is not None and val > 0:
            return val
        legs = oq.get("leg_quotes")
        if isinstance(legs, list) and legs and isinstance(legs[0], dict):
            val = _f(legs[0].get("bid"))
            if val is not None and val > 0:
                return val
    pos = extra.get("position")
    if pos is not None:
        val = _f(getattr(pos, "option_bid", None) or getattr(pos, "current_bid", None))
        if val is not None and val > 0:
            return val
        meta = getattr(pos, "metadata", None)
        if isinstance(meta, dict):
            bags = [meta, meta.get("option_stealth") or {}]
            for bag in bags:
                if not isinstance(bag, dict):
                    continue
                val = _f(bag.get("option_bid") or bag.get("last_option_bid") or bag.get("bid"))
                if val is not None and val > 0:
                    return val
    return None


def observe_0dte_exit(
    system: Any,
    *,
    trade_id: str,
    exit_premium: float = 0.0,
    exit_underlying: float = 0.0,
    exit_reason: str = "",
    realized_pnl_pct: float = 0.0,
    extra: Optional[Dict[str, Any]] = None,
    frozen_symbol_bid: Optional[float] = None,
) -> None:
    po = ensure_profit_optimizer(system)
    tid = str(trade_id or "").strip()
    if not po or not tid:
        return
    extra_bag = dict(extra) if isinstance(extra, dict) else {}
    frozen = frozen_symbol_bid if frozen_symbol_bid is not None else extra_bag.get("frozen_symbol_bid")
    if frozen is None:
        frozen = _frozen_symbol_bid_from_exit_args(extra_bag)
    if frozen is not None:
        extra_bag["frozen_symbol_bid"] = frozen

    def _run() -> None:
        po.observe_exit(
            trade_id=tid,
            exit_premium=float(exit_premium or 0.0),
            exit_underlying=float(exit_underlying or 0.0),
            exit_reason=str(exit_reason or ""),
            realized_pnl_pct=float(realized_pnl_pct),
            extra=extra_bag or None,
            frozen_symbol_bid=_f(frozen),
        )

    safe_observe("observe_0dte_exit", _run)


def unified_monitor_continuations(system: Any) -> List[Dict[str, Any]]:
    """Contracts that keep the unified exact-quote cadence alive after exit."""
    po = ensure_profit_optimizer(system)
    if not po or not po.recorder:
        return []
    out = po.recorder.list_unified_monitor_continuations()
    _kick_shadow_persist(system, po)
    return out


def persist_expired_shadows(system: Any) -> int:
    """Finalize queued post-exit shadows and write one checkpoint."""
    po = ensure_profit_optimizer(system)
    if not po or not po.recorder:
        return 0
    fn = getattr(po.recorder, "finalize_expired_shadows", None)
    if not callable(fn):
        return 0
    return int(fn() or 0)


def flush_po_persist_on_shutdown(system: Any, *, timeout_sec: float = 30.0) -> None:
    """Flush latest dirty PO state off the eval path using the persist worker."""
    po = ensure_profit_optimizer(system)
    rec = getattr(po, "recorder", None) if po is not None else None
    if rec is None:
        return
    flush = getattr(rec, "flush_persist_sync", None)
    if callable(flush):
        flush(timeout_sec=float(timeout_sec))
    gcs = getattr(rec, "flush_gcs_sync", None)
    if callable(gcs):
        gcs(timeout_sec=min(30.0, float(timeout_sec)))


def _kick_shadow_persist(system: Any, po: Any) -> None:
    rec = getattr(po, "recorder", None)
    if rec is None or not getattr(rec, "has_pending_shadow_work", lambda: False)():
        return
    try:
        from modules.unified_0dte_position_monitor import get_unified_0dte_position_monitor

        if get_unified_0dte_position_monitor(system)._schedule_po_shadow_persist(
            system, recorder=rec
        ):
            return
    except Exception:
        pass
    try:
        rec.finalize_expired_shadows()
    except Exception:
        log.debug("PROFIT_OPTIMIZER | shadow_persist_kick_failed", exc_info=True)


def rewrite_continuation_option_symbols(
    system: Any,
    trade_id: str,
    symbol_map: Dict[str, str],
) -> bool:
    """Persist listed-expiry rewrites into frozen shadow contract identity."""
    po = ensure_profit_optimizer(system)
    if not po or not po.recorder or not symbol_map:
        return False
    rewrite = getattr(po.recorder, "rewrite_contract_option_symbols", None)
    if not callable(rewrite):
        return False
    return bool(rewrite(str(trade_id or "").strip(), symbol_map))


def observe_unified_monitor_continuations(
    system: Any,
    *,
    continuations: List[Dict[str, Any]],
    exact_quotes_by_occ: Dict[str, Dict[str, Any]],
    tick_id: str = "",
    premium_snapshots_by_trade: Optional[Dict[str, Dict[str, Any]]] = None,
    sample_at_now: bool = False,
) -> None:
    """Record post-exit samples from the shared monitor premium snapshot.

    Prefers pre-built snapshots (exact or matched chain). Falls back to aliased
    exact-quote lookup. Never synthesizes marks; gaps are compressed intervals.
    """
    from modules.canonical_contract_identity import lookup_quote_by_aliases, quote_lookup_aliases
    from modules.monitor_premium_snapshot import (
        TRUST_EXACT,
        TRUST_GAP,
        build_premium_snapshot,
        snapshot_to_option_quote,
    )

    po = ensure_profit_optimizer(system)
    if not po or not po.recorder:
        return
    snapshots = premium_snapshots_by_trade if isinstance(premium_snapshots_by_trade, dict) else {}
    for trade in continuations:
        trade_id = str(trade.get("trade_id") or "").strip()
        symbol = str(trade.get("symbol") or "").strip().upper()
        legs = trade.get("legs") if isinstance(trade.get("legs"), list) else []
        if not trade_id or not legs:
            continue

        underlying = float(
            getattr(system, "_orb_0dte_last_underlying_by_symbol", {}).get(symbol, 0.0)
            or 0.0
        )
        # Prefer equity already on the shared premium snapshot or open-book cache.
        # Do not fetch a fresh underlying per closed trade — that contended with exits.
        try:
            from modules.underlying_spot_stamp import coerce_underlying_price

            snap_preview = snapshots.get(trade_id) if isinstance(snapshots.get(trade_id), dict) else None
            snap_und = coerce_underlying_price((snap_preview or {}).get("underlying_price"))
            if snap_und is not None:
                underlying = float(snap_und)
            elif symbol:
                underlying = float(
                    getattr(system, "_orb_0dte_last_underlying_by_symbol", {}).get(symbol, 0.0)
                    or underlying
                    or 0.0
                )
        except Exception:
            pass
        if underlying <= 0.0:
            try:
                rec = po.recorder._records.get(trade_id) if po.recorder else None
                if rec is not None:
                    underlying = float(getattr(rec.entry, "underlying_entry_price", 0.0) or 0.0)
            except Exception:
                underlying = 0.0

        snap = snapshots.get(trade_id) if isinstance(snapshots.get(trade_id), dict) else None
        if snap is None:
            # Build from aliased exact map (and optional chain attached on leg).
            leg0 = legs[0] if isinstance(legs[0], dict) else {}
            aliases = quote_lookup_aliases(leg0)
            if not aliases:
                aliases = [str(leg0.get("option_symbol") or "").strip()]
            exact_q = lookup_quote_by_aliases(exact_quotes_by_occ, aliases)
            if exact_q is None:
                try:
                    from modules.dte0_exit_monitor_config import resolve_exit_monitor_config

                    max_age = float(resolve_exit_monitor_config().exit_quote_max_age_sec or 8.0)
                except Exception:
                    max_age = 8.0
                exact_q = po.recorder.last_reusable_exact_quote(trade_id, max_age_sec=max_age)
            chain_q = None
            if exact_q is None:
                chain_cache = getattr(system, "_unified_0dte_chain_quotes_by_trade", None)
                if isinstance(chain_cache, dict):
                    chain_q = chain_cache.get(trade_id)
            snap = build_premium_snapshot(
                trade_id=trade_id,
                exact_quote=exact_q,
                chain_quote=chain_q if isinstance(chain_q, dict) else None,
                underlying_price=underlying,
                requested_occ=str(
                    leg0.get("broker_occ_symbol") or leg0.get("option_symbol") or ""
                ),
                requested_aliases=aliases,
                exact_failure_reason="" if exact_q else f"exact_quote_missing:{aliases[0] if aliases else 'unknown'}",
                chain_failure_reason="" if chain_q else ("chain_quote_missing" if exact_q is None else ""),
                broker_response_status="ok" if (exact_q or chain_q) else "miss",
                tick_id=tick_id,
            )
        elif str(snap.get("shadow_trust") or "") != TRUST_EXACT and not snap.get("shadow_quote_exact"):
            # Prefer last trusted exact over chain-only when age gate rejected the fresh fetch.
            try:
                from modules.dte0_exit_monitor_config import resolve_exit_monitor_config

                max_age = float(resolve_exit_monitor_config().exit_quote_max_age_sec or 8.0)
            except Exception:
                max_age = 8.0
            reused = po.recorder.last_reusable_exact_quote(trade_id, max_age_sec=max_age)
            if reused is not None:
                snap = build_premium_snapshot(
                    trade_id=trade_id,
                    exact_quote=reused,
                    chain_quote=None,
                    underlying_price=underlying,
                    requested_occ=str(snap.get("requested_occ") or ""),
                    requested_aliases=list(snap.get("requested_aliases") or []),
                    exact_failure_reason="",
                    chain_failure_reason="prefer_reused_exact",
                    broker_response_status="reused_last_exact",
                    tick_id=tick_id,
                    reused_last_exact=True,
                )

        if sample_at_now and isinstance(snap, dict) and snap.get("quote_valid"):
            # The broker batch is still in flight. Keep the 2s research tape
            # from the last exact print and record this tick's clock.
            prior_ts = snap.get("broker_quote_timestamp") or snap.get("timestamp")
            snap = dict(snap)
            now_iso = datetime.now(timezone.utc).isoformat()
            snap["timestamp"] = now_iso
            snap["observation_timestamp"] = now_iso
            if prior_ts and not snap.get("broker_quote_timestamp"):
                snap["broker_quote_timestamp"] = prior_ts
            broker_ts = snap.get("broker_quote_timestamp") or prior_ts
            if broker_ts and now_iso:
                try:
                    broker_at = datetime.fromisoformat(str(broker_ts).replace("Z", "+00:00"))
                    observed_at = datetime.fromisoformat(str(now_iso).replace("Z", "+00:00"))
                    if broker_at.tzinfo is None:
                        broker_at = broker_at.replace(tzinfo=timezone.utc)
                    age = max(0.0, (observed_at - broker_at).total_seconds())
                    prior_age = _f(snap.get("quote_age_seconds"))
                    snap["quote_age_seconds"] = age if prior_age is None else max(prior_age, age)
                except (TypeError, ValueError):
                    pass
            snap["reused_last_exact"] = True

        if not snap or snap.get("shadow_trust") == TRUST_GAP or not snap.get("quote_valid"):
            po.recorder.note_shadow_quote_miss(
                trade_id,
                reason=str(
                    snap.get("exact_failure_reason")
                    or snap.get("chain_failure_reason")
                    or "premium_snapshot_gap"
                )
                if isinstance(snap, dict)
                else "premium_snapshot_gap",
                requested_occ=str((snap or {}).get("requested_occ") or ""),
                requested_aliases=list((snap or {}).get("requested_aliases") or []),
                last_known_underlying=underlying if underlying > 0 else None,
                exact_failure_reason=str((snap or {}).get("exact_failure_reason") or ""),
                chain_failure_reason=str((snap or {}).get("chain_failure_reason") or ""),
                broker_response_status=str((snap or {}).get("broker_response_status") or "miss"),
            )
            continue

        oq = snapshot_to_option_quote(snap)
        if not oq:
            po.recorder.note_shadow_quote_miss(
                trade_id,
                reason="snapshot_not_executable",
                requested_occ=str(snap.get("requested_occ") or ""),
            )
            continue

        # Shadow osnap historically omitted chain skew → 100% null call/put/dir%
        # on post-exit path. Forward-fill from last live path + optional sparse refresh.
        from profit_optimizer.path_chain_skew import seed_osnap_chain_skew_from_sources

        option_side = ""
        strike = None
        path_skew_meta = None
        premium_path = None
        try:
            rec = po.recorder._records.get(trade_id)
            if rec is not None:
                premium_path = getattr(rec, "premium_path", None)
                entry = getattr(rec, "entry", None)
                ci = getattr(entry, "contract_identity", None) if entry is not None else None
                if isinstance(ci, dict):
                    option_side = str(
                        ci.get("option_side") or ci.get("option_type") or ""
                    )
                    try:
                        strike = float(ci.get("strike") or 0.0) or None
                    except (TypeError, ValueError):
                        strike = None
                    legs = ci.get("legs") if isinstance(ci.get("legs"), list) else []
                    if legs and isinstance(legs[0], dict):
                        if not option_side:
                            option_side = str(
                                legs[0].get("option_type") or legs[0].get("option_side") or ""
                            )
                        if strike is None:
                            try:
                                strike = float(legs[0].get("strike") or 0.0) or None
                            except (TypeError, ValueError):
                                strike = None
                pa = getattr(rec, "path_audit", None)
                if isinstance(pa, dict) and isinstance(pa.get("path_chain_skew"), dict):
                    path_skew_meta = pa.get("path_chain_skew")
        except Exception:
            pass

        osnap = {
            "timestamp": snap.get("timestamp"),
            "mark_quality": snap.get("mark_quality") or "live_single_leg",
            "mark_is_exit_grade": bool(snap.get("mark_is_exit_grade")),
            "monitor_tick_id": tick_id,
            "premium_source": snap.get("premium_source"),
            "last_premium_source": snap.get("premium_source"),
            "shadow_trust": snap.get("shadow_trust"),
            "freshness_status": snap.get("freshness_status"),
            "contract_match_status": snap.get("contract_match_status"),
            "broker_quote_timestamp": snap.get("broker_quote_timestamp"),
            "observation_timestamp": snap.get("observation_timestamp") or snap.get("timestamp"),
            "contract_symbol": snap.get("contract_symbol") or snap.get("shadow_option_symbol"),
            "reused_last_exact": bool(snap.get("reused_last_exact")),
            "option_side": option_side,
        }
        seed_osnap_chain_skew_from_sources(
            osnap,
            premium_path=premium_path,
            path_chain_skew=path_skew_meta,
            option_side=option_side,
        )
        # Cache-only RSI for this shadow tick. Do not fetch bars or a chain.
        stamp_live_rsi(
            system,
            symbol,
            osnap,
            price=float(snap.get("underlying_price") or underlying or 0.0),
        )

        po.observe_0dte_monitor_tick(
            trade_id=trade_id,
            underlying_price=float(snap.get("underlying_price") or underlying or 0.0),
            option_quote=oq,
            osnap=osnap,
            quote_grade=str(snap.get("shadow_trust") or "shadow"),
        )


def finalize_eod(system: Any) -> None:
    po = ensure_profit_optimizer(system)
    if not po:
        return
    safe_observe("finalize_eod", lambda: po.finalize_session(run_note="eod_scheduler_flatten"))
