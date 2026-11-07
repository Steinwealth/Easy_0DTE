#!/usr/bin/env python3
"""
Rehydrate open 0DTE positions after Cloud Run restart.

Demo: ``mock_options_history.json`` active_positions (ORB1 Breakout + Trendline unified).
Live: broker get_positions() → BrokerPositionSynchronizer (sole writer).

Strategy recovery hierarchy (deterministic):
  1. Persisted lifecycle record matching OCC + broker position
  2. Broker order metadata / client order ID
  3. Existing local registry record
  4. Signal / execution ledger match
  5. Else recovered_unknown
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)


def _get_options_executor(system: Any) -> Any:
    dte0 = getattr(system, "dte0_manager", None)
    if dte0 and getattr(dte0, "options_executor", None):
        return dte0.options_executor
    return None


def _position_strategy_type(pos: Any) -> str:
    st = str(getattr(pos, "strategy_type", "") or "").strip().lower()
    if st:
        return st
    md = getattr(pos, "metadata", None)
    if isinstance(md, dict):
        norm = md.get("normalized_options")
        if isinstance(norm, dict):
            return str(
                norm.get("strategy_type") or norm.get("orb_strategy_type") or ""
            ).strip().lower()
        ep = md.get("execution_payload")
        if isinstance(ep, dict):
            return str(ep.get("strategy_type") or "").strip().lower()
    return ""


def _entry_underlying_from_position(pos: Any) -> float:
    md = getattr(pos, "metadata", None)
    if isinstance(md, dict):
        norm = md.get("normalized_options")
        if isinstance(norm, dict):
            try:
                u = float(norm.get("entry_underlying_price") or 0.0)
                if u > 0:
                    return u
            except (TypeError, ValueError):
                pass
        for key in ("execution_payload", "signal_snapshot"):
            blob = md.get(key)
            if isinstance(blob, dict):
                try:
                    u = float(blob.get("entry_underlying_price") or 0.0)
                    if u > 0:
                        return u
                except (TypeError, ValueError):
                    pass
    try:
        lc = getattr(pos, "lotto_contract", None)
        if lc is not None:
            u = float(getattr(lc, "underlying_price", 0.0) or 0.0)
            if u > 0:
                return u
    except (TypeError, ValueError):
        pass
    return 0.0


def _sync_system_position_registry(system: Any, pos: Any = None) -> None:
    """Refresh derived system.positions from the authoritative executor/mock registry.

    Always performs a full atomic rebuild. The unused ``pos`` argument is retained
    for call-site compatibility only and must not perform single-row upserts.
    """
    del pos  # never mutate individual mirror rows — rebuild from authority
    rebuild_system_position_registry(system)


def rebuild_system_position_registry(system: Any) -> None:
    """Atomically rebuild system.positions from options_executor.get_open_positions()."""
    if system is None:
        return
    dte0 = getattr(system, "dte0_manager", None)
    executor = getattr(dte0, "options_executor", None) if dte0 else None
    if executor is None:
        executor = getattr(system, "options_executor", None)
    trusted: Dict[str, Any] = {}
    if executor is not None and hasattr(executor, "get_open_positions"):
        try:
            for p in executor.get_open_positions() or []:
                pid = str(getattr(p, "position_id", "") or "").strip()
                if pid:
                    trusted[pid] = p
        except Exception:
            return
    # Atomic replace keeps sole-writer semantics on the derived mirror.
    try:
        system.positions = trusted
    except Exception:
        registry = getattr(system, "positions", None)
        if isinstance(registry, dict):
            for pid in list(registry.keys()):
                if pid not in trusted:
                    registry.pop(pid, None)
            registry.update(trusted)


def _occ_keys(occ: str) -> set:
    raw = str(occ or "").strip().upper()
    if not raw:
        return set()
    compact = raw.replace(" ", "")
    return {raw, compact}


def _lifecycle_occ_match(rec: Dict[str, Any], occ_keys: set) -> bool:
    if not isinstance(rec, dict):
        return False
    candidates = []
    for key in ("occ_symbol", "option_symbol", "contract_symbol"):
        v = rec.get(key)
        if v:
            candidates.append(str(v))
    extras = rec.get("extras") if isinstance(rec.get("extras"), dict) else {}
    for key in ("occ_symbol", "option_symbol", "contract_symbol"):
        v = extras.get(key)
        if v:
            candidates.append(str(v))
    meta = rec.get("metadata") if isinstance(rec.get("metadata"), dict) else {}
    for key in ("occ_symbol", "option_symbol"):
        v = meta.get(key)
        if v:
            candidates.append(str(v))
    norm = meta.get("normalized_options") if isinstance(meta.get("normalized_options"), dict) else {}
    legs = norm.get("legs") if isinstance(norm.get("legs"), list) else []
    if legs and isinstance(legs[0], dict):
        for key in ("occ_symbol", "option_symbol", "leg_id"):
            v = legs[0].get(key)
            if v:
                candidates.append(str(v))
    for c in candidates:
        if _occ_keys(c) & occ_keys:
            return True
    return False


def resolve_strategy_recovery(
    system: Any,
    *,
    occ_symbol: str,
    broker_row: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str, Dict[str, Any]]:
    """Return (strategy, management_policy, evidence) via recovery hierarchy."""
    from modules.position_registry_contract import (
        ManagementPolicy,
        STRATEGY_RECOVERED_UNKNOWN,
        StrategyRecoverySource,
    )

    occ_keys = _occ_keys(occ_symbol)
    evidence: Dict[str, Any] = {"occ_symbol": occ_symbol}

    # 1. Persisted lifecycle match
    if system is not None:
        for attr in ("_orb_0dte_lifecycle", "_trendline_lifecycle"):
            lifecycle = getattr(system, attr, None)
            if not isinstance(lifecycle, dict):
                continue
            for trade_id, rec in lifecycle.items():
                if not _lifecycle_occ_match(rec if isinstance(rec, dict) else {}, occ_keys):
                    continue
                st = str(
                    (rec or {}).get("strategy_type")
                    or (rec or {}).get("strategy")
                    or ""
                ).strip()
                if st and st.lower() not in ("", STRATEGY_RECOVERED_UNKNOWN, "unknown_0dte"):
                    evidence.update(
                        {
                            "recovery_source": StrategyRecoverySource.PERSISTED_LIFECYCLE.value,
                            "lifecycle_trade_id": trade_id,
                            "lifecycle_attr": attr,
                        }
                    )
                    return st, ManagementPolicy.STRATEGY_OWNED.value, evidence

    # 2. Broker order metadata / client order ID
    raw: Dict[str, Any] = {}
    if isinstance(broker_row, dict):
        raw = broker_row.get("raw") if isinstance(broker_row.get("raw"), dict) else broker_row
    client_oid = str(
        raw.get("clientOrderId")
        or raw.get("client_order_id")
        or raw.get("orderId")
        or ""
    ).strip()
    if client_oid and system is not None:
        for attr in ("_orb_0dte_lifecycle", "_trendline_lifecycle"):
            lifecycle = getattr(system, attr, None)
            if not isinstance(lifecycle, dict):
                continue
            if client_oid in lifecycle:
                rec = lifecycle[client_oid]
                st = str((rec or {}).get("strategy_type") or "").strip()
                if st:
                    evidence.update(
                        {
                            "recovery_source": StrategyRecoverySource.BROKER_ORDER_METADATA.value,
                            "client_order_id": client_oid,
                        }
                    )
                    return st, ManagementPolicy.STRATEGY_OWNED.value, evidence
            for trade_id, rec in lifecycle.items():
                if not isinstance(rec, dict):
                    continue
                extras = rec.get("extras") if isinstance(rec.get("extras"), dict) else {}
                if client_oid in (
                    str(rec.get("broker_order_id") or ""),
                    str(rec.get("client_order_id") or ""),
                    str(extras.get("broker_order_id") or ""),
                ):
                    st = str(rec.get("strategy_type") or "").strip()
                    if st:
                        evidence.update(
                            {
                                "recovery_source": StrategyRecoverySource.BROKER_ORDER_METADATA.value,
                                "client_order_id": client_oid,
                                "lifecycle_trade_id": trade_id,
                            }
                        )
                        return st, ManagementPolicy.STRATEGY_OWNED.value, evidence

    # 3. Existing local registry record
    executor = _get_options_executor(system) if system is not None else None
    if executor is not None:
        try:
            from modules.broker_live_audits import _position_contract_key

            for pos in (getattr(executor, "positions", None) or {}).values():
                _, contract = _position_contract_key(pos)
                if not (_occ_keys(str(contract or "")) & occ_keys):
                    continue
                st = str(getattr(pos, "strategy_type", "") or "").strip()
                if st and st.lower() not in (STRATEGY_RECOVERED_UNKNOWN, "unknown_0dte"):
                    evidence.update(
                        {
                            "recovery_source": StrategyRecoverySource.LOCAL_REGISTRY.value,
                            "existing_position_id": getattr(pos, "position_id", ""),
                        }
                    )
                    return st, ManagementPolicy.STRATEGY_OWNED.value, evidence
        except Exception:
            pass

    # 4. Signal / execution ledger (best-effort)
    if system is not None:
        for attr in ("_orb_0dte_execution_ledger", "_options_signal_ledger", "_execution_ledger"):
            ledger = getattr(system, attr, None)
            if not isinstance(ledger, dict):
                continue
            for lid, entry in ledger.items():
                if not isinstance(entry, dict):
                    continue
                entry_occ = str(
                    entry.get("occ_symbol")
                    or entry.get("option_symbol")
                    or entry.get("contract_symbol")
                    or ""
                )
                if not (_occ_keys(entry_occ) & occ_keys):
                    continue
                st = str(entry.get("strategy_type") or entry.get("strategy") or "").strip()
                if st:
                    evidence.update(
                        {
                            "recovery_source": StrategyRecoverySource.SIGNAL_EXECUTION_LEDGER.value,
                            "ledger_attr": attr,
                            "ledger_id": lid,
                        }
                    )
                    return st, ManagementPolicy.STRATEGY_OWNED.value, evidence

    evidence["recovery_source"] = StrategyRecoverySource.RECOVERED_UNKNOWN.value
    return STRATEGY_RECOVERED_UNKNOWN, ManagementPolicy.EMERGENCY_SAFE.value, evidence


def build_rehydrated_live_position(
    *,
    system: Any,
    executor: Any,
    occ_symbol: str,
    broker_qty: int,
    broker_row: Optional[Dict[str, Any]] = None,
) -> Any:
    """Build a full OptionsPosition for broker-only import (caller writes via synchronizer)."""
    from easy0DTE.modules.options_chain_manager import OptionContract
    from easy0DTE.modules.options_types import OptionsPosition
    from modules.position_registry_contract import (
        EntrySource,
        ReconciliationStatus,
        STRATEGY_RECOVERED_UNKNOWN,
        parse_occ_symbol,
        recovered_unknown_meta,
        utc_now,
    )

    parsed = parse_occ_symbol(occ_symbol)
    underlying = str(parsed.get("underlying") or "").strip().upper()
    if not underlying:
        underlying = (
            occ_symbol.split()[0].strip().upper()
            if " " in occ_symbol
            else occ_symbol[:6].strip().upper()
        )

    raw: Dict[str, Any] = {}
    if isinstance(broker_row, dict):
        raw = broker_row.get("raw") if isinstance(broker_row.get("raw"), dict) else broker_row
    entry_px = 0.0
    for field in ("costPerShare", "averagePrice", "pricePaid", "Price"):
        try:
            entry_px = float(raw.get(field) or 0.0)
        except (TypeError, ValueError):
            entry_px = 0.0
        if entry_px > 0:
            break
    if entry_px <= 0:
        entry_px = 0.01

    strategy, management_policy, evidence = resolve_strategy_recovery(
        system, occ_symbol=occ_symbol, broker_row=broker_row
    )
    position_id = f"REHYDRATE_{underlying}_{int(time.time())}_{abs(int(broker_qty))}"
    if evidence.get("lifecycle_trade_id"):
        candidate = str(evidence["lifecycle_trade_id"])
        if candidate and candidate not in (getattr(executor, "positions", {}) or {}):
            position_id = candidate

    meta = recovered_unknown_meta(occ_symbol=occ_symbol, broker_qty=abs(int(broker_qty)))
    meta.update(evidence)
    meta["strategy"] = strategy
    meta["strategy_type"] = strategy
    meta["management_policy"] = management_policy
    meta["entry_source"] = EntrySource.BROKER_RECOVERY.value
    meta["broker_average_fill"] = float(entry_px)
    meta["expiration"] = parsed.get("expiration") or ""
    meta["strike"] = parsed.get("strike")
    meta["call_put"] = parsed.get("call_put") or ""
    meta["reconciliation_status"] = ReconciliationStatus.REPAIRED.value
    meta["reconciliation_timestamp"] = utc_now().isoformat()
    meta["monitor_identity"] = (
        "trendline_0dte" if "trendline" in str(strategy).lower() else "orb_0dte"
    )
    meta["exit_policy"] = (
        "emergency_safe" if management_policy != "strategy_owned" else "strategy_owned"
    )
    meta["degraded_data_state"] = {
        "degraded": strategy == STRATEGY_RECOVERED_UNKNOWN,
        "reason": evidence.get("recovery_source"),
    }
    meta["premium_hwm"] = None
    meta["premium_lwm"] = None
    meta["mfe_pct"] = None
    meta["mae_pct"] = None
    meta["last_reliable_premium"] = None
    meta["broker_order_ids"] = []
    for oid_key in ("orderId", "order_id", "clientOrderId", "client_order_id"):
        if raw.get(oid_key):
            meta["broker_order_ids"].append(str(raw.get(oid_key)))

    lotto = None
    exp = str(parsed.get("expiration") or "")
    strike = parsed.get("strike")
    cp = str(parsed.get("call_put") or "call")
    if exp and strike:
        try:
            lotto = OptionContract(
                symbol=underlying,
                strike=float(strike),
                expiry=exp,
                option_type=cp,
                bid=float(entry_px),
                ask=float(entry_px),
                last=float(entry_px),
                volume=0,
                open_interest=0,
                delta=0.0,
                gamma=0.0,
                theta=0.0,
                vega=0.0,
                implied_volatility=0.0,
            )
        except Exception:
            lotto = None

    entry_ts = utc_now()
    for ts_key in ("dateTime", "dateAcquired", "openDateTime", "acquiredDate"):
        raw_ts = raw.get(ts_key)
        if not raw_ts:
            continue
        try:
            if isinstance(raw_ts, (int, float)):
                entry_ts = datetime.fromtimestamp(float(raw_ts), tz=timezone.utc)
            else:
                text = str(raw_ts).replace("Z", "+00:00")
                entry_ts = datetime.fromisoformat(text)
            break
        except Exception:
            continue
    entry_ts_naive = entry_ts.replace(tzinfo=None) if entry_ts.tzinfo else entry_ts

    return OptionsPosition(
        position_id=position_id,
        symbol=underlying,
        position_type="lotto",
        lotto_contract=lotto,
        entry_price=float(entry_px),
        entry_time=entry_ts_naive,
        quantity=abs(int(broker_qty)),
        current_value=float(entry_px),
        unrealized_pnl=0.0,
        strategy_type=strategy,
        metadata=meta,
    )


async def _rehydrate_live_options_from_broker(system: Any, executor: Any) -> int:
    if getattr(executor, "demo_mode", True):
        return 0
    adapter = getattr(executor, "broker_adapter", None)
    if adapter is None or not getattr(adapter, "is_available", lambda: False)():
        return 0

    from modules.broker_live_audits import _broker_position_keys, _position_contract_key
    from modules.broker_position_synchronizer import get_or_create_synchronizer

    try:
        broker_rows = await adapter.get_positions()
    except Exception as err:
        log.warning("0DTE_OPEN_RECOVERY | broker_get_positions_failed | error=%s", err)
        return 0

    broker_keys = _broker_position_keys(broker_rows or [])
    local_contracts = set()
    for pos in (getattr(executor, "positions", None) or {}).values():
        _, contract = _position_contract_key(pos)
        if contract:
            local_contracts.add(str(contract).upper())

    sync = get_or_create_synchronizer(system)
    rehydrated = 0
    for key, brow in broker_keys.items():
        qty = int(float(brow.get("quantity") or 0.0))
        if qty <= 0:
            continue
        sym = str(brow.get("symbol") or "").strip()
        if not sym:
            continue
        sym_u = sym.upper()
        if sym_u in local_contracts or key.upper() in local_contracts:
            continue
        if " " not in sym and len(sym) <= 5:
            continue

        position = build_rehydrated_live_position(
            system=system,
            executor=executor,
            occ_symbol=sym,
            broker_qty=abs(qty),
            broker_row=brow,
        )
        sync.put_position(executor, position)
        _sync_system_position_registry(system, position)
        local_contracts.add(sym_u)
        rehydrated += 1
        log.warning(
            "0DTE_OPEN_RECOVERY | source=broker | trade_id=%s | symbol=%s | occ=%s | qty=%d | "
            "strategy=%s | recovery=%s",
            position.position_id,
            position.symbol,
            sym,
            abs(qty),
            position.strategy_type,
            (position.metadata or {}).get("recovery_source"),
        )
    return rehydrated


async def resync_open_0dte_positions_on_startup(system: Any) -> Dict[str, Any]:
    """
    Session-start recovery for unified ORB1 Breakout + Trendline 0DTE open books and stealth engines.
    """
    summary: Dict[str, Any] = {
        "open_positions_seen": 0,
        "stealth_trendline_registered": 0,
        "stealth_orb_registered": 0,
        "live_broker_rehydrated": 0,
    }

    executor = _get_options_executor(system)
    if executor is None:
        return summary

    open_positions: List[Any] = list(executor.get_open_positions() or [])
    summary["open_positions_seen"] = len(open_positions)

    for pos in open_positions:
        pid = str(getattr(pos, "position_id", "") or "").strip()
        if not pid:
            continue
        _sync_system_position_registry(system, pos)
        underlying = _entry_underlying_from_position(pos)
        if underlying <= 0 and hasattr(system, "data_manager") and system.data_manager:
            sym = str(getattr(pos, "symbol", "") or "").strip()
            if sym:
                try:
                    quotes = await system.data_manager.get_batch_quotes([sym])
                    q = (quotes or {}).get(sym) or {}
                    underlying = float(
                        q.get("last") or q.get("price") or q.get("close") or 0.0
                    )
                except Exception:
                    underlying = 0.0

        st = _position_strategy_type(pos)
        md = getattr(pos, "metadata", None)
        if not isinstance(md, dict):
            md = {}
            try:
                pos.metadata = md
            except Exception:
                pass

        if "trendline" in st:
            if hasattr(system, "_ensure_trendline_options_stealth_engine") and hasattr(
                system, "_register_trendline_stealth_if_missing"
            ):
                stealth = system._ensure_trendline_options_stealth_engine()
                if system._register_trendline_stealth_if_missing(
                    stealth, pos, md, float(underlying or 0.0)
                ):
                    summary["stealth_trendline_registered"] += 1
        else:
            if hasattr(system, "_register_orb_0dte_stealth_after_execution"):
                before = md.get("option_stealth")
                system._register_orb_0dte_stealth_after_execution(
                    pos, current_underlying=float(underlying or 0.0)
                )
                after = md.get("option_stealth")
                if after and after != before:
                    summary["stealth_orb_registered"] += 1

    summary["live_broker_rehydrated"] = await _rehydrate_live_options_from_broker(
        system, executor
    )
    for pos in open_positions:
        _rebuild_path_v2_restore(system, pos)
    return summary


def _entry_time_iso_from_position(pos: Any, trade: Dict[str, Any]) -> str:
    """Prefer the fill clock, then the first saved path row — never process start."""
    from profit_optimizer.time_utils import normalize_timestamp_iso

    candidates: List[Any] = [
        getattr(pos, "entry_time", None),
        getattr(pos, "opened_at", None),
        getattr(pos, "filled_at", None),
        getattr(pos, "created_at", None),
    ]
    md = getattr(pos, "metadata", None)
    if isinstance(md, dict):
        candidates.extend((md.get("entry_time"), md.get("opened_at")))
        for key in ("normalized_options", "execution_payload"):
            blob = md.get(key)
            if isinstance(blob, dict):
                candidates.append(blob.get("entry_time"))
    for row in trade.get("premium_path") or []:
        if isinstance(row, dict):
            candidates.append(row.get("eval_now") or row.get("timestamp"))
            break
    for raw in candidates:
        if raw in (None, ""):
            continue
        try:
            text = raw.isoformat() if hasattr(raw, "isoformat") else str(raw)
            iso = normalize_timestamp_iso(text)
            if iso:
                return iso
        except Exception:
            continue
    return ""


def _rebuild_path_v2_restore(system: Any, pos: Any) -> None:
    """Reload JSONL paths into the flight recorder and premium tracker after restart."""
    try:
        from modules.canonical_closed_trade import us_market_session_date
        from profit_optimizer.path_jsonl import attach_jsonl_paths
        from profit_optimizer.profit_optimizer import get_profit_optimizer
        from profit_optimizer.profit_optimizer_models import (
            STRATEGY_BREAKOUT_0DTE,
            STRATEGY_ORB_0DTE,
            STRATEGY_TRENDLINE_0DTE,
        )

        tid = str(getattr(pos, "position_id", "") or "").strip()
        if not tid:
            return
        session = us_market_session_date()
        st = _position_strategy_type(pos)
        if "trendline" in st:
            path = STRATEGY_TRENDLINE_0DTE
        elif "breakout" in st:
            path = STRATEGY_BREAKOUT_0DTE
        else:
            path = STRATEGY_ORB_0DTE
        md = getattr(pos, "metadata", None)
        meta = md if isinstance(md, dict) else {}
        norm = meta.get("normalized_options") if isinstance(meta.get("normalized_options"), dict) else {}
        exec_pl = meta.get("execution_payload") if isinstance(meta.get("execution_payload"), dict) else {}
        try:
            entry_premium = float(
                getattr(pos, "entry_price", None)
                or norm.get("entry_value")
                or exec_pl.get("estimated_contract_price")
                or 0.0
            )
        except (TypeError, ValueError):
            entry_premium = 0.0
        trade = {
            "trade_id": tid,
            "status": "open",
            "entry": {
                "trade_id": tid,
                "date": session,
                "symbol": str(getattr(pos, "symbol", "") or "").upper(),
                "strategy_path": path,
                "direction": str(getattr(pos, "direction", "LONG") or "LONG").upper(),
                "underlying_entry_price": _entry_underlying_from_position(pos),
                "entry_premium": entry_premium,
                "strike": getattr(pos, "strike", None),
                "expiration": str(
                    norm.get("expiration_ymd") or getattr(pos, "expiration", "") or ""
                ),
                "contracts": int(getattr(pos, "quantity", 0) or 1),
            },
        }
        attach_jsonl_paths(session, trade)
        entry_iso = _entry_time_iso_from_position(pos, trade)
        if entry_iso:
            trade["entry"]["entry_time"] = entry_iso
        po = None
        getter = getattr(system, "_get_profit_optimizer", None)
        if callable(getter):
            try:
                po = getter()
            except Exception:
                po = None
        if po is None:
            po = get_profit_optimizer()
        recorder = getattr(po, "recorder", None) if po is not None else None
        rebuilt = False
        if recorder is not None:
            records = getattr(recorder, "_records", None)
            existing = records.get(tid) if isinstance(records, dict) else None
            trade_p = list(trade.get("premium_path") or [])
            trade_u = list(trade.get("underlying_path") or [])
            if existing is not None:
                if len(trade_p) > len(list(getattr(existing, "premium_path", None) or [])):
                    existing.premium_path = trade_p
                if len(trade_u) > len(list(getattr(existing, "underlying_path", None) or [])):
                    existing.underlying_path = trade_u
                trackers = getattr(recorder, "_trackers", None)
                tr = trackers.get(tid) if isinstance(trackers, dict) else None
                if tr is not None:
                    live_hwm = float(getattr(tr, "_hwm_premium", 0.0) or 0.0)
                    entry_px = float(getattr(tr, "entry_premium", 0.0) or 0.0)
                    if live_hwm > entry_px + 1e-9 and list(getattr(tr, "premium_path", None) or []):
                        rebuilt = True
                    else:
                        tr.rehydrate_from_saved(
                            existing.premium_path, existing.underlying_path
                        )
                        rebuilt = True
                elif hasattr(recorder, "_restore_trade_from_dict"):
                    rebuilt = bool(recorder._restore_trade_from_dict(trade))
            elif hasattr(recorder, "_restore_trade_from_dict"):
                rebuilt = bool(recorder._restore_trade_from_dict(trade))
        n_p = len(trade.get("premium_path") or [])
        n_u = len(trade.get("underlying_path") or [])
        log.info(
            "PATH_V2_RESTORE | trade_id=%s | session=%s | premium_rows=%d | "
            "underlying_rows=%d | rebuilt=%s",
            tid,
            session,
            n_p,
            n_u,
            str(bool(rebuilt)).lower(),
        )
    except Exception as exc:
        log.warning("PATH_V2_RESTORE | rebuild_failed | err=%s", exc)
