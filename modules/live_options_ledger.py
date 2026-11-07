#!/usr/bin/env python3
"""
Durable GCS ledger for broker-confirmed LIVE options closes.

Persists closed-trade records to ``{gcs_namespace}/live_options_history.json`` so EOD
stats (today / week / all-time) survive Cloud Run restarts and redeployments.
Only writes when MODE=live and close is broker-confirmed.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

log = logging.getLogger(__name__)

LEDGER_VERSION = 1

# Optional certification fields appended to each closed trade (LIVE ledger v1+).
CERTIFICATION_FIELD_NAMES = (
    "strategy_path",
    "orb_strategy_type",
    "option_symbol",
    "strike",
    "expiration",
    "option_side",
    "target_delta",
    "fill_delta",
    "entry_price",
    "entry_bid",
    "entry_ask",
    "entry_mid",
    "entry_spread_pct",
    "entry_slippage_vs_mid_pct",
    "exit_mid",
    "exit_slippage_vs_mid_pct",
    "exit_bid_signal",
    "exit_slippage_vs_bid_pct",
)


def _optional_float(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _first_norm_leg(norm: Dict[str, Any]) -> Dict[str, Any]:
    legs = norm.get("legs")
    if isinstance(legs, list) and legs and isinstance(legs[0], dict):
        return legs[0]
    return {}


def _lotto_contract_dict(position: Any, meta: Dict[str, Any]) -> Dict[str, Any]:
    lotto = getattr(position, "lotto_contract", None)
    if isinstance(lotto, dict):
        return lotto
    if lotto is not None and hasattr(lotto, "to_dict"):
        try:
            return lotto.to_dict()
        except Exception:
            pass
    norm = meta.get("normalized_options") if isinstance(meta.get("normalized_options"), dict) else {}
    leg0 = _first_norm_leg(norm)
    leg_id = str(leg0.get("leg_id") or "")
    if leg_id:
        return {"leg_id": leg_id}
    return {}


def extract_certification_fields_from_position(position: Any) -> Dict[str, Any]:
    """
    Extract strike/δ/slippage fields already present on the position metadata.
    Missing values are returned as None — never raises.
    """
    meta = dict(getattr(position, "metadata", None) or {})
    norm = meta.get("normalized_options") if isinstance(meta.get("normalized_options"), dict) else {}
    exec_pl = meta.get("execution_payload") if isinstance(meta.get("execution_payload"), dict) else {}
    sig = meta.get("signal_snapshot") if isinstance(meta.get("signal_snapshot"), dict) else {}
    eq = meta.get("entry_execution_quality") if isinstance(meta.get("entry_execution_quality"), dict) else {}
    exit_eq = meta.get("exit_execution_quality") if isinstance(meta.get("exit_execution_quality"), dict) else {}
    leg0 = _first_norm_leg(norm)
    lotto = _lotto_contract_dict(position, meta)

    strategy_path = str(
        norm.get("strategy_type")
        or norm.get("source_path")
        or meta.get("strategy_path")
        or ""
    ).strip() or None
    orb_strategy_type = str(
        norm.get("orb_strategy_type")
        or getattr(position, "strategy_type", "")
        or meta.get("strategy_type")
        or ""
    ).strip() or None

    option_side = str(
        leg0.get("option_side")
        or lotto.get("option_type")
        or getattr(position, "option_side", "")
        or sig.get("option_side")
        or ""
    ).lower() or None

    strike = _optional_float(
        leg0.get("strike")
        or lotto.get("strike")
        or getattr(position, "strike", None)
        or sig.get("strike")
    )
    expiration = str(
        norm.get("expiration_ymd")
        or lotto.get("expiry")
        or getattr(position, "expiration", "")
        or sig.get("expiry_ymd")
        or exec_pl.get("expiration")
        or ""
    ).strip() or None

    option_symbol = str(
        leg0.get("leg_id")
        or lotto.get("symbol")
        or exec_pl.get("option_symbol")
        or ""
    ).strip() or None

    fill_delta = _optional_float(
        exec_pl.get("delta_at_entry")
        or exec_pl.get("delta")
        or norm.get("delta_at_entry")
        or leg0.get("delta_at_entry")
        or lotto.get("delta")
        or getattr(position, "delta", None)
        or sig.get("delta_at_entry")
    )
    if fill_delta is not None:
        fill_delta = abs(fill_delta)

    target_delta = _optional_float(
        sig.get("target_delta")
        or meta.get("target_delta")
        or exec_pl.get("target_delta")
    )

    entry_price = _optional_float(
        getattr(position, "entry_price", None)
        or exec_pl.get("fill_price")
        or leg0.get("entry_price")
        or lotto.get("mid_price")
        or norm.get("entry_debit")
    )
    entry_bid = _optional_float(
        exec_pl.get("quote_bid")
        or eq.get("entry_bid")
        or lotto.get("bid")
    )
    entry_ask = _optional_float(
        exec_pl.get("quote_ask")
        or eq.get("entry_ask")
        or lotto.get("ask")
    )
    entry_mid = _optional_float(
        exec_pl.get("quote_mid")
        or eq.get("entry_mid")
        or lotto.get("mid_price")
    )
    entry_spread_pct = _optional_float(
        eq.get("entry_spread_pct")
        or exec_pl.get("spread_width_pct")
    )
    if entry_spread_pct is None:
        lotto_spread = _optional_float(lotto.get("bid_ask_spread_pct"))
        if lotto_spread is not None:
            entry_spread_pct = lotto_spread / 100.0

    entry_slippage_vs_mid_pct = _optional_float(
        eq.get("entry_slippage_vs_mid_pct")
        or exec_pl.get("slippage_vs_mid_pct")
        or exec_pl.get("slippage_vs_mid")
    )

    exit_mid = _optional_float(
        exit_eq.get("exit_mid")
        or exit_eq.get("quote_mid")
    )
    exit_slippage_vs_mid_pct = _optional_float(
        exit_eq.get("exit_slippage_vs_mid_pct")
        or exit_eq.get("slippage_vs_mid_pct")
    )
    exit_bid_signal = _optional_float(
        exit_eq.get("exit_bid_signal")
        or exit_eq.get("quote_bid")
    )
    exit_slippage_vs_bid_pct = _optional_float(
        exit_eq.get("exit_slippage_vs_bid_pct")
    )

    return {
        "strategy_path": strategy_path,
        "orb_strategy_type": orb_strategy_type,
        "option_symbol": option_symbol,
        "strike": strike,
        "expiration": expiration,
        "option_side": option_side,
        "target_delta": target_delta,
        "fill_delta": fill_delta,
        "entry_price": entry_price,
        "entry_bid": entry_bid,
        "entry_ask": entry_ask,
        "entry_mid": entry_mid,
        "entry_spread_pct": entry_spread_pct,
        "entry_slippage_vs_mid_pct": entry_slippage_vs_mid_pct,
        "exit_mid": exit_mid,
        "exit_slippage_vs_mid_pct": exit_slippage_vs_mid_pct,
        "exit_bid_signal": exit_bid_signal,
        "exit_slippage_vs_bid_pct": exit_slippage_vs_bid_pct,
    }


def _empty_certification_fields() -> Dict[str, Any]:
    return {name: None for name in CERTIFICATION_FIELD_NAMES}


def gcs_ledger_path() -> str:
    from modules.runtime_mode import gcs_namespace

    return f"{gcs_namespace()}/live_options_history.json"


def _target_account_id() -> str:
    raw = os.getenv("ETRADE_0DTE_ACCOUNT_ID") or os.getenv("0DTE_ETRADE_ACCOUNT_ID") or ""
    return str(raw).strip()


def _starting_balance() -> float:
    try:
        return float(os.getenv("0DTE_LIVE_STARTING_BALANCE", "5014.75") or 5014.75)
    except (TypeError, ValueError):
        return 5014.75


def _parse_iso_ts(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        text = str(raw).replace("Z", "+00:00")
        return datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return None


def _get_gcs():
    try:
        from modules.gcs_persistence import get_gcs_persistence

        return get_gcs_persistence()
    except Exception as exc:
        log.warning("LIVE_OPTIONS_LEDGER | gcs_unavailable | %s", exc)
        return None


def _live_ledger_max_closed_trades() -> int:
    raw = os.getenv("LIVE_OPTIONS_LEDGER_MAX_CLOSED_TRADES", "0")
    try:
        return int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return 0


def _apply_live_closed_trades_retention(trades: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep the most recent closed trades; retention disabled when cap <= 0."""
    max_trades = _live_ledger_max_closed_trades()
    if max_trades <= 0 or len(trades) <= max_trades:
        return trades
    dropped = len(trades) - max_trades
    log.info(
        "LIVE_OPTIONS_LEDGER | pruned | kept=%d | dropped=%d | cap=%d",
        max_trades,
        dropped,
        max_trades,
    )
    return trades[-max_trades:]


def empty_ledger() -> Dict[str, Any]:
    from modules.equity_curve_book import empty_book

    seed = _starting_balance()
    book = empty_book(seed)
    return {
        "version": LEDGER_VERSION,
        "account_id": _target_account_id(),
        "starting_balance": seed,
        "booked_equity": book["booked_equity"],
        "peak_equity": book["peak_equity"],
        "withdrawals_total": book["withdrawals_total"],
        "equity_curve": book["equity_curve"],
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "closed_trades": [],
    }


def load_ledger() -> Dict[str, Any]:
    """Load ledger from GCS; return empty scaffold when missing."""
    gcs = _get_gcs()
    if gcs is None or not gcs.enabled:
        return empty_ledger()

    path = gcs_ledger_path()
    try:
        if not gcs.file_exists(path):
            return empty_ledger()
        raw = gcs.read_string(path)
        if not raw:
            return empty_ledger()
        data = json.loads(raw)
        if not isinstance(data, dict):
            return empty_ledger()
        trades = data.get("closed_trades")
        if not isinstance(trades, list):
            data["closed_trades"] = []
        return data
    except Exception as exc:
        log.warning("LIVE_OPTIONS_LEDGER | load_failed | path=%s | %s", path, exc)
        return empty_ledger()


def _save_ledger(data: Dict[str, Any]) -> bool:
    gcs = _get_gcs()
    if gcs is None or not gcs.enabled:
        log.warning("LIVE_OPTIONS_LEDGER | save_skipped | gcs_disabled")
        return False

    payload = dict(data)
    payload["version"] = LEDGER_VERSION
    payload["account_id"] = _target_account_id() or payload.get("account_id") or ""
    existing_seed = payload.get("starting_balance")
    try:
        seed = float(existing_seed) if existing_seed is not None and str(existing_seed).strip() != "" else _starting_balance()
    except (TypeError, ValueError):
        seed = _starting_balance()
    payload["starting_balance"] = seed
    from modules.equity_curve_book import restore_or_backfill

    trades = payload.get("closed_trades")
    payload["closed_trades"] = trades if isinstance(trades, list) else []
    book = restore_or_backfill(payload, seed, payload["closed_trades"])
    payload["booked_equity"] = book["booked_equity"]
    payload["peak_equity"] = book["peak_equity"]
    payload["withdrawals_total"] = book["withdrawals_total"]
    payload["equity_curve"] = book["equity_curve"]
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()

    path = gcs_ledger_path()
    try:
        ok = gcs.upload_string(path, json.dumps(payload, indent=2, default=str))
        if ok:
            log.info(
                "LIVE_OPTIONS_LEDGER | saved | path=%s | closed_trades=%d",
                path,
                len(payload["closed_trades"]),
            )
        return bool(ok)
    except Exception as exc:
        log.error("LIVE_OPTIONS_LEDGER | save_failed | path=%s | %s", path, exc)
        return False


def build_closed_trade_record(
    *,
    trade_id: str,
    symbol: str,
    realized_pnl: float,
    closed_at: Optional[datetime] = None,
    entry_time: Optional[datetime] = None,
    strategy_type: str = "ORB_0DTE",
    broker_order_id: str = "",
    filled_exit_price: float = 0.0,
    close_reason: str = "",
    quantity: int = 1,
    position: Any = None,
    certification: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    now = datetime.now(timezone.utc)
    closed = closed_at or now
    entry = entry_time or closed
    record: Dict[str, Any] = {
        "trade_id": str(trade_id).strip(),
        "symbol": str(symbol or "").upper().strip(),
        "strategy_type": str(strategy_type or "ORB_0DTE"),
        "realized_pnl": round(float(realized_pnl), 2),
        "quantity": int(quantity or 1),
        "closed_at": closed.isoformat(),
        "entry_time": entry.isoformat(),
        "broker_order_id": str(broker_order_id or ""),
        "filled_exit_price": round(float(filled_exit_price), 4),
        "close_reason": str(close_reason or ""),
        "account_id": _target_account_id(),
        "persisted_at": now.isoformat(),
    }
    cert = certification if isinstance(certification, dict) else None
    if cert is None and position is not None:
        try:
            cert = extract_certification_fields_from_position(position)
        except Exception as exc:
            log.debug("LIVE_OPTIONS_LEDGER | certification_extract_failed | %s", exc)
            cert = _empty_certification_fields()
    if cert is None:
        cert = _empty_certification_fields()
    econ = {}
    if position is not None:
        meta_pos = dict(getattr(position, "metadata", None) or {})
        if isinstance(meta_pos.get("economic_summary"), dict):
            econ = meta_pos["economic_summary"]
    for key in CERTIFICATION_FIELD_NAMES:
        val = cert.get(key)
        if isinstance(val, float):
            record[key] = round(val, 6) if key in ("strike", "target_delta", "fill_delta") else round(val, 4)
        else:
            record[key] = val
    if econ:
        record["economic_summary"] = econ
    if position is not None:
        try:
            from modules.calendar_expiry_metadata import (
                EXPIRY_TRADE_FIELD_KEYS,
                copy_expiry_fields,
                merge_expiry_trade_fields,
            )

            meta_pos = dict(getattr(position, "metadata", None) or {})
            norm = meta_pos.get("normalized_options") if isinstance(meta_pos.get("normalized_options"), dict) else {}
            expiry_fields: Dict[str, Any] = {}
            copy_expiry_fields(norm, expiry_fields)
            copy_expiry_fields(meta_pos, expiry_fields)
            merge_expiry_trade_fields(
                expiry_fields,
                selected_expiry=expiry_fields.get("selected_expiry_ymd")
                or expiry_fields.get("expiry_ymd")
                or norm.get("expiration_ymd")
                or getattr(position, "expiration", None),
                prefer_existing=True,
            )
            for key in EXPIRY_TRADE_FIELD_KEYS:
                if key in expiry_fields:
                    record[key] = expiry_fields.get(key)
        except Exception:
            pass
    return record


def append_closed_trade(record: Dict[str, Any]) -> bool:
    """
    Append one broker-confirmed close to the LIVE GCS ledger (dedupe by trade_id).
    No-op in demo mode or when trade_id is missing.
    """
    from modules.runtime_mode import is_live_mode

    if not is_live_mode():
        return False

    trade_id = str(record.get("trade_id") or "").strip()
    if not trade_id:
        log.warning("LIVE_OPTIONS_LEDGER | append_skipped | reason=missing_trade_id")
        return False

    ledger = load_ledger()
    trades: List[Dict[str, Any]] = list(ledger.get("closed_trades") or [])
    existing_ids = {str(t.get("trade_id") or "").strip() for t in trades if isinstance(t, dict)}
    if trade_id in existing_ids:
        log.debug("LIVE_OPTIONS_LEDGER | append_skipped | trade_id=%s | reason=duplicate", trade_id)
        return True

    from modules.equity_curve_book import apply_event, restore_or_backfill

    seed = float(ledger.get("starting_balance") or _starting_balance())
    book = restore_or_backfill(ledger, seed, trades)
    pnl = float(record.get("realized_pnl") or 0.0)
    book = apply_event(book, delta=pnl, t=record.get("closed_at"), kind="exit")
    trades.append(record)
    ledger["closed_trades"] = _apply_live_closed_trades_retention(trades)
    ledger["booked_equity"] = book["booked_equity"]
    ledger["peak_equity"] = book["peak_equity"]
    ledger["withdrawals_total"] = book["withdrawals_total"]
    ledger["equity_curve"] = book["equity_curve"]
    ok = _save_ledger(ledger)
    if ok:
        log.info(
            "LIVE_OPTIONS_LEDGER | append | trade_id=%s | symbol=%s | pnl=%.2f",
            trade_id,
            record.get("symbol"),
            float(record.get("realized_pnl") or 0.0),
        )
    return ok


def persist_live_close_from_lifecycle(
    *,
    trade_id: str,
    symbol: str,
    strategy_type: str,
    lifecycle_rec: Dict[str, Any],
    quantity: int = 1,
    position: Any = None,
    mock_executor: Any = None,
) -> bool:
    """Persist from ORB1 Breakout lifecycle close_confirmation block."""
    if not bool(lifecycle_rec.get("close_confirmed")):
        return False
    cc = dict(lifecycle_rec.get("close_confirmation") or {})
    if not bool(cc.get("close_confirmed", True)):
        return False

    closed_at = _parse_iso_ts(cc.get("close_confirmed_timestamp"))
    record = build_closed_trade_record(
        trade_id=trade_id,
        symbol=symbol,
        strategy_type=strategy_type,
        realized_pnl=float(cc.get("realized_pnl_dollar") or 0.0),
        closed_at=closed_at,
        broker_order_id=str(cc.get("broker_order_id") or ""),
        filled_exit_price=float(cc.get("filled_exit_price") or 0.0),
        close_reason=str(cc.get("close_reason") or ""),
        quantity=quantity,
        position=position,
    )
    from modules.runtime_mode import is_demo_mode, is_live_mode

    if is_live_mode():
        return append_closed_trade(record)
    if is_demo_mode():
        from types import SimpleNamespace

        stub = SimpleNamespace(
            position_id=trade_id,
            symbol=symbol,
            strategy_type=strategy_type,
            realized_pnl=float(cc.get("realized_pnl_dollar") or 0.0),
            quantity=quantity,
            entry_price=float(cc.get("entry_price") or 0.0),
            entry_time=closed_at,
            position_type="lotto",
            metadata={"strategy_type": strategy_type, "close_reason": cc.get("close_reason")},
        )
        return persist_demo_close_from_position(
            stub,
            exit_reason=str(cc.get("close_reason") or ""),
            exit_price=float(cc.get("filled_exit_price") or 0.0),
            mock_executor=mock_executor,
        )
    return False


def persist_live_close_from_position(
    position: Any,
    *,
    exit_reason: str = "",
    broker_order_id: str = "",
    filled_exit_price: Optional[float] = None,
) -> bool:
    """Persist from a broker-confirmed OptionsPosition after execute_exit."""
    meta = dict(getattr(position, "metadata", None) or {})
    if not meta.get("broker_close_confirmed"):
        return False

    trade_id = str(getattr(position, "position_id", "") or "").strip()
    if not trade_id:
        return False

    pnl = float(getattr(position, "realized_pnl", 0.0) or 0.0)
    exit_px = filled_exit_price
    if exit_px is None:
        exit_px = float(getattr(position, "current_value", 0.0) or 0.0)

    entry_time = getattr(position, "entry_time", None)
    if entry_time is not None and hasattr(entry_time, "tzinfo") and entry_time.tzinfo is None:
        entry_time = entry_time.replace(tzinfo=timezone.utc)

    record = build_closed_trade_record(
        trade_id=trade_id,
        symbol=str(getattr(position, "symbol", "") or ""),
        strategy_type=str(getattr(position, "strategy_type", "") or "ORB_0DTE"),
        realized_pnl=pnl,
        closed_at=datetime.now(timezone.utc),
        entry_time=entry_time if isinstance(entry_time, datetime) else None,
        broker_order_id=str(broker_order_id or meta.get("broker_close_order_id") or ""),
        filled_exit_price=float(exit_px),
        close_reason=str(exit_reason or ""),
        quantity=int(getattr(position, "quantity", 1) or 1),
        position=position,
    )
    return append_closed_trade(record)


def persist_demo_close_from_position(
    position: Any,
    *,
    exit_reason: str = "",
    exit_price: Optional[float] = None,
    mock_executor: Any = None,
) -> bool:
    """Persist demo soak closes to namespace-scoped mock_options_history.json (GCS)."""
    from modules.runtime_mode import is_demo_mode

    if not is_demo_mode():
        return False

    exit_px = float(
        exit_price
        if exit_price is not None
        else getattr(position, "current_value", None)
        or getattr(position, "exit_price", 0.0)
        or 0.0
    )
    mock = mock_executor
    if mock is None:
        # Never construct a cold MockOptionsExecutor here — it would RMW GCS
        # against a second in-memory book and can wipe active Demo opens.
        log.warning(
            "DEMO_OPTIONS_LEDGER | persist_skipped | reason=missing_mock_executor | trade_id=%s",
            str(getattr(position, "position_id", "") or ""),
        )
        return False

    if hasattr(mock, "record_external_closed_position"):
        return bool(
            mock.record_external_closed_position(
                position,
                exit_price=exit_px,
                exit_reason=str(exit_reason or ""),
            )
        )
    return False


def persist_options_close_from_position(
    position: Any,
    *,
    exit_reason: str = "",
    broker_order_id: str = "",
    filled_exit_price: Optional[float] = None,
    mock_executor: Any = None,
) -> bool:
    """Route close persistence: live broker ledger vs demo mock_options_history."""
    from modules.runtime_mode import is_demo_mode, is_live_mode

    if is_live_mode():
        return persist_live_close_from_position(
            position,
            exit_reason=exit_reason,
            broker_order_id=broker_order_id,
            filled_exit_price=filled_exit_price,
        )
    if is_demo_mode():
        return persist_demo_close_from_position(
            position,
            exit_reason=exit_reason,
            exit_price=filled_exit_price,
            mock_executor=mock_executor,
        )
    return False


class _LedgerClosedRow:
    """EOD-compatible row (matches stats_dict_from_positions expectations)."""

    __slots__ = ("realized_pnl", "entry_time", "closed_at", "trade_id")

    def __init__(
        self,
        *,
        realized_pnl: float,
        entry_time: datetime,
        closed_at: Optional[datetime],
        trade_id: str,
    ) -> None:
        self.realized_pnl = realized_pnl
        self.entry_time = entry_time
        self.closed_at = closed_at
        self.trade_id = trade_id


def _row_from_gcs_trade(trade: Dict[str, Any]) -> Optional[_LedgerClosedRow]:
    if not isinstance(trade, dict):
        return None
    trade_id = str(trade.get("trade_id") or "").strip()
    if not trade_id:
        return None
    closed_at = _parse_iso_ts(trade.get("closed_at"))
    entry_time = _parse_iso_ts(trade.get("entry_time")) or closed_at
    if entry_time is None:
        entry_time = datetime.now(timezone.utc)
    return _LedgerClosedRow(
        realized_pnl=float(trade.get("realized_pnl") or 0.0),
        entry_time=entry_time,
        closed_at=closed_at,
        trade_id=trade_id,
    )


def _row_from_lifecycle(trade_id: str, rec: Dict[str, Any]) -> Optional[_LedgerClosedRow]:
    if not isinstance(rec, dict) or not bool(rec.get("close_confirmed")):
        return None
    cc = dict(rec.get("close_confirmation") or {})
    pnl = float(cc.get("realized_pnl_dollar") or 0.0)
    closed_at = _parse_iso_ts(cc.get("close_confirmed_timestamp"))
    entry_time = closed_at or datetime.now(timezone.utc)
    return _LedgerClosedRow(
        realized_pnl=pnl,
        entry_time=entry_time,
        closed_at=closed_at,
        trade_id=str(trade_id),
    )


def collect_live_eod_closed_rows(system: Any) -> Tuple[List[_LedgerClosedRow], str]:
    """
    Merge durable GCS ledger with in-memory lifecycle closes (session not yet persisted).
    Returns (rows, source_tag).
    """
    from modules.runtime_mode import is_demo_mode, is_live_mode

    if is_demo_mode():
        from modules.eod_report_helpers import collect_demo_eod_closed_rows

        demo_rows, source = collect_demo_eod_closed_rows(system)
        rows: List[_LedgerClosedRow] = []
        for row in demo_rows:
            pid = str(getattr(row, "position_id", "") or getattr(row, "trade_id", "") or "")
            rows.append(
                _LedgerClosedRow(
                    realized_pnl=float(getattr(row, "realized_pnl", 0.0) or 0.0),
                    entry_time=getattr(row, "entry_time", None) or getattr(row, "opened_at", None),
                    closed_at=getattr(row, "closed_at", None),
                    trade_id=pid,
                )
            )
        return rows, source or "demo_mock_ledger"

    if not is_live_mode():
        from modules.broker_live_audits import lifecycle_closed_positions

        lifecycle = dict(getattr(system, "_orb_0dte_lifecycle", None) or {})
        return list(lifecycle_closed_positions(lifecycle)), "broker_lifecycle"

    ledger = load_ledger()
    gcs_trades = [t for t in (ledger.get("closed_trades") or []) if isinstance(t, dict)]
    rows: List[_LedgerClosedRow] = []
    seen: Set[str] = set()

    for trade in gcs_trades:
        row = _row_from_gcs_trade(trade)
        if row is None or row.trade_id in seen:
            continue
        rows.append(row)
        seen.add(row.trade_id)

    lifecycle = dict(getattr(system, "_orb_0dte_lifecycle", None) or {})
    for trade_id, rec in lifecycle.items():
        tid = str(trade_id).strip()
        if not tid or tid in seen:
            continue
        row = _row_from_lifecycle(tid, rec)
        if row is None:
            continue
        rows.append(row)
        seen.add(tid)

    if gcs_trades:
        source = "broker_lifecycle_gcs"
    else:
        source = "broker_lifecycle"
    return rows, source


def load_live_ledger_for_eod_fallback() -> Optional[List[_LedgerClosedRow]]:
    """Cold-start EOD when system has no executor: load GCS ledger only."""
    from modules.runtime_mode import is_demo_mode, is_live_mode

    if is_demo_mode():
        try:
            from easy0DTE.modules.mock_options_executor import MockOptionsExecutor

            mock = MockOptionsExecutor()
            closed = list(getattr(mock, "closed_positions", None) or [])
            if not closed:
                return None
            rows: List[_LedgerClosedRow] = []
            for pos in closed:
                pid = str(getattr(pos, "position_id", "") or "")
                rows.append(
                    _LedgerClosedRow(
                        realized_pnl=float(getattr(pos, "realized_pnl", 0.0) or 0.0),
                        entry_time=getattr(pos, "entry_time", None),
                        closed_at=getattr(pos, "closed_at", None),
                        trade_id=pid,
                    )
                )
            return rows if rows else None
        except Exception as exc:
            log.warning("LIVE_OPTIONS_LEDGER | demo_mock_fallback_failed | %s", exc)
            return None

    if not is_live_mode():
        return None

    ledger = load_ledger()
    trades = ledger.get("closed_trades") or []
    if not trades:
        return None

    rows: List[_LedgerClosedRow] = []
    for trade in trades:
        row = _row_from_gcs_trade(trade)
        if row is not None:
            rows.append(row)
    return rows if rows else None
