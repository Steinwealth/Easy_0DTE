#!/usr/bin/env python3
"""
Profit Optimizer — passive session flight recorder and post-exit shadow tracker
for executed ORB SO / ORB 0DTE / Trendline 0DTE trades.

Records live premium paths, continues exact-contract shadow quotes after exit,
and persists to GCS for post-session path review. Not an offline exit replay engine.

Read-only: never modifies stops, trails, entries, exits, or ranking.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from zoneinfo import ZoneInfo

from profit_optimizer.entry_quality import build_entry_execution_quality
from profit_optimizer.profit_optimizer_models import (
    STRATEGY_BREAKOUT_0DTE,
    STRATEGY_ORB_0DTE,
    STRATEGY_ORB_SO,
    STRATEGY_TRENDLINE_0DTE,
    TradeEntrySnapshot,
    breakout_profit_optimizer_enabled,
    trendline_profit_optimizer_enabled,
)
from profit_optimizer.safe_hooks import run_safe
from profit_optimizer.sampling_policy import load_sampling_config
from profit_optimizer.trade_flight_recorder import TradeFlightRecorder
from profit_optimizer.time_utils import normalize_timestamp_iso

log = logging.getLogger(__name__)
PT = ZoneInfo("America/Los_Angeles")

_PO_SINGLETON: Optional["ProfitOptimizer"] = None


def profit_optimizer_enabled() -> bool:
    try:
        from modules.config_loader import get_config_value
    except Exception:
        return True
    raw = get_config_value("ENABLE_PROFIT_OPTIMIZER", "true")
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _log_interval_sec() -> float:
    try:
        from modules.config_loader import get_config_value
        return float(get_config_value("PROFIT_OPTIMIZER_LOG_MIN_INTERVAL_SEC", "30") or 30)
    except Exception:
        return 30.0


class ProfitOptimizer:
    """Facade used by PrimeTradingSystem (observe-only hooks)."""

    def __init__(self) -> None:
        self._recorder: Optional[TradeFlightRecorder] = None
        self._session_date = ""

    def begin_session(self, session_date: Optional[str] = None) -> None:
        new_date = session_date or datetime.now(PT).strftime("%Y-%m-%d")
        if self._recorder is not None and self._session_date == new_date:
            return
        self._session_date = new_date
        early, window, late = load_sampling_config()
        self._recorder = TradeFlightRecorder(
            session_date=self._session_date,
            log_min_interval_sec=_log_interval_sec(),
            early_sample_sec=early,
            early_window_sec=window,
            late_sample_sec=late,
        )
        self._recorder.hydrate_from_storage()

    @property
    def recorder(self) -> Optional[TradeFlightRecorder]:
        return self._recorder

    def on_orb_so_executed(
        self,
        *,
        trade_id: str,
        symbol: str,
        direction: str,
        entry_time: str,
        underlying_entry_price: float,
        contracts: int = 0,
        position_size: Optional[float] = None,
        selector_metadata: Optional[Dict[str, Any]] = None,
        extra: Optional[Dict[str, Any]] = None,
        strike: Optional[float] = None,
        expiration: str = "",
        entry_premium: Optional[float] = None,
        contract_identity: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self._recorder:
            return
        merged_extra = dict(extra or {})
        if contract_identity and entry_premium and float(entry_premium) > 0:
            merged_extra.setdefault("premium_shadow_enabled", True)
            merged_extra.setdefault("paired_0dte", True)
        entry = TradeEntrySnapshot(
            trade_id=str(trade_id),
            date=self._session_date,
            symbol=str(symbol).upper(),
            strategy_path=STRATEGY_ORB_SO,
            direction=str(direction or "LONG").upper(),
            entry_time=normalize_timestamp_iso(entry_time),
            underlying_entry_price=float(underlying_entry_price),
            strike=strike,
            expiration=str(expiration or ""),
            entry_premium=entry_premium,
            contracts=int(contracts or 0),
            position_size=position_size,
            contract_identity=dict(contract_identity or {}),
            selector_metadata=dict(selector_metadata or {}),
            extra=merged_extra,
        )
        self._recorder.start_trade(entry)

    def attach_so_premium_shadow(
        self,
        *,
        trade_id: str,
        contract_identity: Dict[str, Any],
        entry_premium: float,
        expiration: str = "",
        strike: Optional[float] = None,
        selector_metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        if not self._recorder:
            return False
        return self._recorder.attach_so_premium_shadow(
            trade_id,
            contract_identity=contract_identity,
            entry_premium=entry_premium,
            expiration=expiration,
            strike=strike,
            selector_metadata=selector_metadata,
        )

    def on_orb_0dte_executed(
        self,
        *,
        trade_id: str,
        symbol: str,
        direction: str,
        entry_time: str,
        underlying_entry_price: float,
        strike: Optional[float],
        expiration: str,
        entry_bid: Optional[float],
        entry_ask: Optional[float],
        entry_mid: Optional[float],
        entry_premium: float,
        contracts: int,
        position_size: Optional[float] = None,
        contract_identity: Optional[Dict[str, Any]] = None,
        selector_metadata: Optional[Dict[str, Any]] = None,
        entry_execution_quality: Optional[Dict[str, Any]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self._recorder:
            return
        eq = entry_execution_quality
        if not isinstance(eq, dict) or not eq:
            eq = build_entry_execution_quality(
                entry_bid=entry_bid,
                entry_ask=entry_ask,
                entry_mid=entry_mid,
                fill_price=entry_premium,
                entry_premium_source=str((extra or {}).get("entry_premium_source") or ""),
            )
        entry = TradeEntrySnapshot(
            trade_id=str(trade_id),
            date=self._session_date,
            symbol=str(symbol).upper(),
            strategy_path=STRATEGY_ORB_0DTE,
            direction=str(direction or "LONG").upper(),
            entry_time=normalize_timestamp_iso(entry_time),
            underlying_entry_price=float(underlying_entry_price),
            strike=strike,
            expiration=str(expiration or ""),
            entry_bid=entry_bid,
            entry_ask=entry_ask,
            entry_mid=entry_mid,
            entry_premium=float(entry_premium),
            contracts=int(contracts or 0),
            position_size=position_size,
            contract_identity=dict(contract_identity or {}),
            selector_metadata=dict(selector_metadata or {}),
            entry_execution_quality=dict(eq),
            extra=dict(extra or {}),
        )
        self._recorder.start_trade(entry)

    def on_trendline_0dte_executed(
        self,
        *,
        trade_id: str,
        symbol: str,
        direction: str,
        entry_time: str,
        underlying_entry_price: float,
        strike: Optional[float] = None,
        expiration: str = "",
        entry_bid: Optional[float] = None,
        entry_ask: Optional[float] = None,
        entry_mid: Optional[float] = None,
        entry_premium: float = 0.0,
        contracts: int = 1,
        position_size: Optional[float] = None,
        contract_identity: Optional[Dict[str, Any]] = None,
        selector_metadata: Optional[Dict[str, Any]] = None,
        entry_execution_quality: Optional[Dict[str, Any]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not trendline_profit_optimizer_enabled() or not self._recorder:
            return
        eq = entry_execution_quality
        if not isinstance(eq, dict) or not eq:
            eq = build_entry_execution_quality(
                entry_bid=entry_bid,
                entry_ask=entry_ask,
                entry_mid=entry_mid,
                fill_price=entry_premium,
            )
        entry = TradeEntrySnapshot(
            trade_id=str(trade_id),
            date=self._session_date,
            symbol=str(symbol).upper(),
            strategy_path=STRATEGY_TRENDLINE_0DTE,
            direction=str(direction or "LONG").upper(),
            entry_time=normalize_timestamp_iso(entry_time),
            underlying_entry_price=float(underlying_entry_price),
            strike=strike,
            expiration=str(expiration or ""),
            entry_bid=entry_bid,
            entry_ask=entry_ask,
            entry_mid=entry_mid,
            entry_premium=float(entry_premium),
            contracts=int(contracts or 0),
            position_size=position_size,
            contract_identity=dict(contract_identity or {}),
            selector_metadata=dict(selector_metadata or {}),
            entry_execution_quality=dict(eq),
            extra=dict(extra or {}),
        )
        self._recorder.start_trade(entry)

    def on_breakout_0dte_executed(
        self,
        *,
        trade_id: str,
        symbol: str,
        direction: str,
        entry_time: str,
        underlying_entry_price: float,
        strike: Optional[float] = None,
        expiration: str = "",
        entry_bid: Optional[float] = None,
        entry_ask: Optional[float] = None,
        entry_mid: Optional[float] = None,
        entry_premium: float = 0.0,
        contracts: int = 0,
        position_size: Optional[float] = None,
        contract_identity: Optional[Dict[str, Any]] = None,
        selector_metadata: Optional[Dict[str, Any]] = None,
        entry_execution_quality: Optional[Dict[str, Any]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not breakout_profit_optimizer_enabled() or not self._recorder:
            return
        eq = entry_execution_quality
        if not isinstance(eq, dict) or not eq:
            eq = build_entry_execution_quality(
                entry_bid=entry_bid,
                entry_ask=entry_ask,
                entry_mid=entry_mid,
                fill_price=entry_premium,
            )
        entry = TradeEntrySnapshot(
            trade_id=str(trade_id),
            date=self._session_date,
            symbol=str(symbol).upper(),
            strategy_path=STRATEGY_BREAKOUT_0DTE,
            direction=str(direction or "LONG").upper(),
            entry_time=normalize_timestamp_iso(entry_time),
            underlying_entry_price=float(underlying_entry_price),
            strike=strike,
            expiration=str(expiration or ""),
            entry_bid=entry_bid,
            entry_ask=entry_ask,
            entry_mid=entry_mid,
            entry_premium=float(entry_premium),
            contracts=int(contracts or 0),
            position_size=position_size,
            contract_identity=dict(contract_identity or {}),
            selector_metadata=dict(selector_metadata or {}),
            entry_execution_quality=dict(eq),
            extra=dict(extra or {}),
        )
        self._recorder.start_trade(entry)

    def on_trendline_0dte_emit_shadow(
        self,
        *,
        trade_id: str,
        symbol: str,
        direction: str,
        entry_time: str,
        underlying_entry_price: float,
        strike: Optional[float] = None,
        expiration: str = "",
        entry_premium: float = 0.0,
        contracts: int = 1,
        selector_metadata: Optional[Dict[str, Any]] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Shadow path for Trendline emits that did not fill (measurement phase)."""
        if not trendline_profit_optimizer_enabled() or not self._recorder:
            return
        try:
            from modules.config_loader import get_config_value

            if str(get_config_value("ENABLE_TRENDLINE_EMIT_SHADOW", "false")).strip().lower() not in (
                "1",
                "true",
                "yes",
                "on",
            ):
                return
        except Exception:
            pass
        if float(entry_premium or 0.0) <= 0:
            return
        entry = TradeEntrySnapshot(
            trade_id=str(trade_id),
            date=self._session_date,
            symbol=str(symbol).upper(),
            strategy_path=STRATEGY_TRENDLINE_0DTE,
            direction=str(direction or "LONG").upper(),
            entry_time=normalize_timestamp_iso(entry_time),
            underlying_entry_price=float(underlying_entry_price),
            strike=strike,
            expiration=str(expiration or ""),
            entry_premium=float(entry_premium),
            contracts=int(contracts or 1),
            selector_metadata=dict(selector_metadata or {}),
            extra={**(extra or {}), "emit_shadow": True, "filled": False},
        )
        self._recorder.start_trade(entry)
        rec = self._recorder._records.get(str(trade_id))
        if rec:
            rec.status = "shadow"
            rec.shadow_tracking = True

    def observe_0dte_monitor_tick(
        self,
        *,
        trade_id: str,
        underlying_price: float,
        option_quote: Optional[Dict[str, Any]] = None,
        osnap: Optional[Dict[str, Any]] = None,
        quote_grade: str = "",
    ) -> None:
        if not self._recorder:
            return
        oq = option_quote if isinstance(option_quote, dict) else {}
        snap = osnap if isinstance(osnap, dict) else {}
        bid = _pick(oq, "bid")
        ask = _pick(oq, "ask")
        mid = _pick(oq, "mid_price", "mid")
        mark = _pick(oq, "mark") or _f(snap.get("current_effective_premium"))
        spread_pct = _pick(oq, "spread_pct") or _f(snap.get("spread_pct"))
        spread_w = None
        if bid is not None and ask is not None:
            spread_w = abs(float(ask) - float(bid))
        prem_src = str(
            snap.get("last_premium_source")
            or snap.get("premium_source")
            or oq.get("quote_source")
            or ""
        )
        rec = self._recorder._records.get(str(trade_id))
        is_shadow = bool(rec and rec.status == "shadow")
        shadow_exact = bool(oq.get("shadow_quote_exact"))
        shadow_sym = str(oq.get("shadow_option_symbol") or "")
        if not shadow_sym:
            lqs = oq.get("leg_quotes") if isinstance(oq.get("leg_quotes"), list) else []
            if lqs and isinstance(lqs[0], dict):
                shadow_sym = str(lqs[0].get("option_symbol") or lqs[0].get("leg_id") or "")
        try:
            from modules.option_executable_pricing import long_option_sell_to_close_price

            exec_bid = long_option_sell_to_close_price(
                bid=float(bid) if bid is not None else None,
                ask=float(ask) if ask is not None else None,
                mid=float(mid) if mid is not None else None,
                last=_pick(oq, "last"),
            )
        except ImportError:
            exec_bid = float(bid) if bid is not None else None
        # Prefer PremiumEngine / stealth effective mark when present (true-to-live = bid STC).
        eff_snap = _f(snap.get("current_effective_premium"))
        if eff_snap is not None and eff_snap > 0:
            exec_bid = float(eff_snap)
        # Path mark for MFE/MAE must match sell-to-close economics (bid), not mid.
        if exec_bid is not None and exec_bid > 0:
            mark = float(exec_bid)
        elif bid is not None and float(bid) > 0:
            mark = float(bid)
        # cumulative_volume: keep broker 0; absent stays None (never fake 0).
        cum_vol = None
        for _vk in ("cumulative_volume", "volume"):
            if oq.get(_vk) is not None:
                try:
                    _vv = float(oq.get(_vk))
                    if _vv == _vv and _vv >= 0:
                        cum_vol = _vv
                        break
                except (TypeError, ValueError):
                    continue
        tick_fields: Dict[str, Any] = {
            "timestamp": normalize_timestamp_iso(
                snap.get("timestamp")
                or oq.get("observation_timestamp")
                or snap.get("observation_timestamp")
                or _now_iso()
            ),
            "underlying_price": underlying_price,
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "mark": mark,
            "executable_bid_mark": exec_bid,
            "last": _pick(oq, "last"),
            "bid_size": _pick(oq, "bid_size"),
            "ask_size": _pick(oq, "ask_size"),
            "cumulative_volume": cum_vol,
            "volume": cum_vol,
            "spread_width": spread_w if spread_w is not None else _pick(oq, "spread_width"),
            "spread_pct": spread_pct,
            "quote_age_seconds": _pick(oq, "quote_age_seconds") or _f(snap.get("quote_age_seconds")),
            "quote_age_ms": _pick(oq, "quote_age_ms") or _f(snap.get("quote_age_ms")),
            "evaluated": snap.get("evaluated") if snap.get("evaluated") is not None else oq.get("evaluated"),
            "defer_reason": str(snap.get("defer_reason") or oq.get("defer_reason") or ""),
            "market_snapshot_id": str(
                snap.get("market_snapshot_id")
                or snap.get("monitor_tick_id")
                or oq.get("monitor_tick_id")
                or ""
            ),
            "underlying_last": _f(snap.get("underlying_last") or oq.get("underlying_last") or underlying_price),
            "underlying_observation_timestamp": str(
                snap.get("underlying_observation_timestamp")
                or oq.get("underlying_observation_timestamp")
                or ""
            )
            or None,
            "underlying_age_ms": _f(snap.get("underlying_age_ms") or oq.get("underlying_age_ms")),
            "underlying_source": str(snap.get("underlying_source") or oq.get("underlying_source") or ""),
            "underlying_reused": snap.get("underlying_reused")
            if snap.get("underlying_reused") is not None
            else oq.get("underlying_reused"),
            "underlying_stale": snap.get("underlying_stale")
            if snap.get("underlying_stale") is not None
            else oq.get("underlying_stale"),
            "last_successful_eval_ts": snap.get("last_successful_eval_ts"),
            "seconds_since_last_successful_eval": _f(snap.get("seconds_since_last_successful_eval")),
            "force_path_sample": bool(snap.get("force_path_sample") or oq.get("force_path_sample")),
            "quote_quality": quote_grade or str(snap.get("mark_quality") or ""),
            "premium_source": _normalize_premium_source(prem_src, snap),
            "mark_quality": str(snap.get("mark_quality") or ""),
            "mark_is_exit_grade": bool(snap.get("mark_is_exit_grade")),
            "price_source_telemetry": str(snap.get("price_source_telemetry") or ""),
            "broker_quote_timestamp": str(
                oq.get("broker_quote_timestamp") or snap.get("broker_quote_timestamp") or ""
            )
            or None,
            "observation_timestamp": str(
                oq.get("observation_timestamp")
                or snap.get("observation_timestamp")
                or snap.get("timestamp")
                or ""
            )
            or None,
            "monitor_tick_id": str(snap.get("monitor_tick_id") or oq.get("monitor_tick_id") or ""),
            "eval_now": str(snap.get("eval_now") or oq.get("eval_now") or "") or None,
            "eval_age_s": _f(snap.get("eval_age_s") if snap.get("eval_age_s") is not None else oq.get("eval_age_s")),
            "exit_reason": str(snap.get("exit_reason") or oq.get("exit_reason") or "") or None,
            "prem_vel_10": _f(snap.get("prem_vel_10")),
            "path_v2_peak": _f(snap.get("path_v2_peak")),
            "path_pret": _f(snap.get("path_pret")),
            "path_v2_reason": str(snap.get("path_v2_reason") or "") or None,
            "contracts": snap.get("contracts"),
            "quantity": snap.get("quantity"),
            "opening_fees": _f(snap.get("opening_fees")),
            "closing_fees": _f(snap.get("closing_fees")),
            "round_trip_fees": _f(snap.get("round_trip_fees")),
            "entry_premium": _f(snap.get("entry_premium")),
            "actual_exit_premium": _f(snap.get("actual_exit_premium")),
            "actual_exit_bid": _f(snap.get("actual_exit_bid")),
            "contract_symbol": str(
                oq.get("contract_symbol")
                or oq.get("shadow_option_symbol")
                or snap.get("contract_symbol")
                or ""
            ),
            "reused_last_exact": bool(oq.get("reused_last_exact") or snap.get("reused_last_exact")),
            "trade_id": str(trade_id or oq.get("trade_id") or ""),
            "option_side": str(
                snap.get("option_side")
                or oq.get("option_side")
                or oq.get("option_type")
                or ""
            ),
            # Owned-contract + chain skew when monitor/osnap already has them
            # (periodic refresh stamps osnap; do not pull full chain every ~2s).
            "open_interest": _f(
                _first_present(
                    oq.get("open_interest"),
                    oq.get("openInterest"),
                    snap.get("open_interest"),
                    snap.get("representative_open_interest"),
                )
            ),
            "delta": _f(_first_present(oq.get("delta"), snap.get("delta"), snap.get("representative_delta"))),
            "gamma": _f(_first_present(oq.get("gamma"), snap.get("gamma"))),
            "theta": _f(_first_present(oq.get("theta"), snap.get("theta"))),
            "iv": _f(
                _first_present(
                    oq.get("iv"),
                    oq.get("implied_volatility"),
                    snap.get("iv"),
                    snap.get("implied_volatility"),
                )
            ),
            "call_chain_volume": _f(
                _first_present(snap.get("call_chain_volume"), oq.get("call_chain_volume"))
            ),
            "put_chain_volume": _f(
                _first_present(snap.get("put_chain_volume"), oq.get("put_chain_volume"))
            ),
            "put_call_volume_ratio": _f(
                _first_present(snap.get("put_call_volume_ratio"), oq.get("put_call_volume_ratio"))
            ),
            "put_call_oi_ratio": _f(
                _first_present(snap.get("put_call_oi_ratio"), oq.get("put_call_oi_ratio"))
            ),
            "direction_volume_pct": _f(
                _first_present(snap.get("direction_volume_pct"), oq.get("direction_volume_pct"))
            ),
            "call_chain_open_interest": _f(
                _first_present(snap.get("call_chain_open_interest"), oq.get("call_chain_open_interest"))
            ),
            "put_chain_open_interest": _f(
                _first_present(snap.get("put_chain_open_interest"), oq.get("put_chain_open_interest"))
            ),
            "total_chain_volume": _f(
                _first_present(snap.get("total_chain_volume"), oq.get("total_chain_volume"))
            ),
            "total_chain_open_interest": _f(
                _first_present(snap.get("total_chain_open_interest"), oq.get("total_chain_open_interest"))
            ),
            "representative_volume": _f(
                _first_present(snap.get("representative_volume"), oq.get("representative_volume"))
            ),
            "representative_open_interest": _f(
                _first_present(
                    snap.get("representative_open_interest"),
                    oq.get("representative_open_interest"),
                )
            ),
            "representative_delta": _f(
                _first_present(snap.get("representative_delta"), oq.get("representative_delta"))
            ),
            "representative_bid": _f(_first_present(snap.get("representative_bid"), oq.get("representative_bid"))),
            "representative_ask": _f(_first_present(snap.get("representative_ask"), oq.get("representative_ask"))),
            "macd": _f(_first_present(snap.get("macd"), oq.get("macd"))),
            "macd_signal": _f(_first_present(snap.get("macd_signal"), oq.get("macd_signal"))),
            "macd_histogram": _f(_first_present(snap.get("macd_histogram"), oq.get("macd_histogram"))),
            "prior_macd_histogram": _f(
                _first_present(snap.get("prior_macd_histogram"), oq.get("prior_macd_histogram"))
            ),
            "macd_above_zero": snap.get("macd_above_zero")
            if snap.get("macd_above_zero") is not None
            else oq.get("macd_above_zero"),
            "macd_cross": str(snap.get("macd_cross") or oq.get("macd_cross") or "") or None,
            "macd_hold_ok_call": snap.get("macd_hold_ok_call")
            if snap.get("macd_hold_ok_call") is not None
            else oq.get("macd_hold_ok_call"),
            "macd_exit_signal_call": snap.get("macd_exit_signal_call")
            if snap.get("macd_exit_signal_call") is not None
            else oq.get("macd_exit_signal_call"),
            "macd_hist_sign": snap.get("macd_hist_sign")
            if snap.get("macd_hist_sign") is not None
            else oq.get("macd_hist_sign"),
            "sma_20": _f(_first_present(snap.get("sma_20"), oq.get("sma_20"))),
            "sma_50": _f(_first_present(snap.get("sma_50"), oq.get("sma_50"))),
            "ema_12": _f(_first_present(snap.get("ema_12"), oq.get("ema_12"))),
            "ema_26": _f(_first_present(snap.get("ema_26"), oq.get("ema_26"))),
            "rsi": _f(_first_present(snap.get("rsi"), oq.get("rsi"))),
            "volume_1m": _f(_first_present(snap.get("volume_1m"), oq.get("volume_1m"))),
            "vwap": _f(_first_present(snap.get("vwap"), oq.get("vwap"))),
            "vwap_distance_pct": _f(
                _first_present(snap.get("vwap_distance_pct"), oq.get("vwap_distance_pct"))
            ),
            "vwap_position": str(
                _first_present(snap.get("vwap_position"), oq.get("vwap_position"), "")
            )
            or None,
            # Equity participation (session_tech hub) — must copy or path stays null.
            "volume_ratio": _f(_first_present(snap.get("volume_ratio"), oq.get("volume_ratio"))),
            "orb_volume_ratio": _f(
                _first_present(
                    snap.get("orb_volume_ratio"),
                    oq.get("orb_volume_ratio"),
                    snap.get("volume_ratio"),
                    oq.get("volume_ratio"),
                )
            ),
            "volume_ratio_missing": snap.get("volume_ratio_missing")
            if snap.get("volume_ratio_missing") is not None
            else oq.get("volume_ratio_missing"),
            "session_bar_count": _f(
                _first_present(snap.get("session_bar_count"), oq.get("session_bar_count"))
            ),
            "volume_ratio_source": str(
                _first_present(snap.get("volume_ratio_source"), oq.get("volume_ratio_source"), "")
            ),
            "tech_source": str(_first_present(snap.get("tech_source"), oq.get("tech_source"), "")),
            "rsi_missing": snap.get("rsi_missing")
            if snap.get("rsi_missing") is not None
            else oq.get("rsi_missing"),
            "session_tech_bar_at": str(
                _first_present(snap.get("session_tech_bar_at"), oq.get("session_tech_bar_at"), "")
            )
            or None,
            "session_tech_asof_at": str(
                _first_present(
                    snap.get("session_tech_asof_at"),
                    oq.get("session_tech_asof_at"),
                    "",
                )
            )
            or None,
            "session_tech_observed_at": str(
                _first_present(
                    snap.get("session_tech_observed_at"),
                    oq.get("session_tech_observed_at"),
                    "",
                )
            )
            or None,
            "pve3_bar_at": str(
                _first_present(snap.get("pve3_bar_at"), oq.get("pve3_bar_at"), "")
            )
            or None,
            "price_volume_efficiency_3m": _f(
                _first_present(
                    snap.get("price_volume_efficiency_3m"),
                    oq.get("price_volume_efficiency_3m"),
                )
            ),
            "price_change_3m_pct": _f(
                _first_present(snap.get("price_change_3m_pct"), oq.get("price_change_3m_pct"))
            ),
            "volume_3m": _f(_first_present(snap.get("volume_3m"), oq.get("volume_3m"))),
            "macd_missing": snap.get("macd_missing")
            if snap.get("macd_missing") is not None
            else oq.get("macd_missing"),
            "vwap_missing": snap.get("vwap_missing")
            if snap.get("vwap_missing") is not None
            else oq.get("vwap_missing"),
            # ATR / Bollinger / momentum — listed in SESSION_TECH_PATH_KEYS; must copy.
            "atr": _f(_first_present(snap.get("atr"), oq.get("atr"))),
            "momentum_10": _f(_first_present(snap.get("momentum_10"), oq.get("momentum_10"))),
            "bollinger_upper": _f(
                _first_present(snap.get("bollinger_upper"), oq.get("bollinger_upper"))
            ),
            "bollinger_middle": _f(
                _first_present(snap.get("bollinger_middle"), oq.get("bollinger_middle"))
            ),
            "bollinger_lower": _f(
                _first_present(snap.get("bollinger_lower"), oq.get("bollinger_lower"))
            ),
            "bollinger_width": _f(
                _first_present(snap.get("bollinger_width"), oq.get("bollinger_width"))
            ),
            "bollinger_position": _f(
                _first_present(snap.get("bollinger_position"), oq.get("bollinger_position"))
            ),
            "distance_from_vwap_atr": _f(
                _first_present(
                    snap.get("distance_from_vwap_atr"),
                    oq.get("distance_from_vwap_atr"),
                )
            ),
            "otm_pct": _f(_first_present(snap.get("otm_pct"), oq.get("otm_pct"))),
            "call_share": _f(_first_present(snap.get("call_share"), oq.get("call_share"))),
            "put_share": _f(_first_present(snap.get("put_share"), oq.get("put_share"))),
            "dir_pct_change_5m": _f(
                _first_present(snap.get("dir_pct_change_5m"), oq.get("dir_pct_change_5m"))
            ),
            "underlying_l1_imbalance": _f(
                _first_present(
                    snap.get("underlying_l1_imbalance"),
                    oq.get("underlying_l1_imbalance"),
                )
            ),
            "rs_vs_spy": _f(_first_present(snap.get("rs_vs_spy"), oq.get("rs_vs_spy"))),
        }
        # Derive same-side direction volume % when chain vols present but pct missing.
        option_side_hint = str(
            snap.get("option_side")
            or snap.get("expected_option_side")
            or oq.get("option_type")
            or snap.get("side")
            or ""
        ).lower()
        try:
            from profit_optimizer.path_chain_skew import (
                derive_direction_volume_pct,
                maybe_replace_complement_dirpct,
                option_side_token,
                stamp_review_microstructure,
            )

            side = option_side_token(option_side_hint, oq.get("option_type"), snap.get("side"))
            stamp_review_microstructure(tick_fields, option_side=side, overwrite=False)
            derived = derive_direction_volume_pct(
                tick_fields.get("call_chain_volume"),
                tick_fields.get("put_chain_volume"),
                option_side=side,
            )
            if derived is not None:
                replaced = maybe_replace_complement_dirpct(
                    tick_fields.get("direction_volume_pct"), derived
                )
                tick_fields["direction_volume_pct"] = (
                    derived if replaced is None else replaced
                )
        except Exception:
            pass
        if tick_fields.get("direction_volume_pct") is None:
            try:
                cv = tick_fields.get("call_chain_volume")
                pv = tick_fields.get("put_chain_volume")
                if cv is not None and pv is not None and float(cv) + float(pv) > 0:
                    total = float(cv) + float(pv)
                    if "put" in option_side_hint:
                        tick_fields["direction_volume_pct"] = float(pv) / total
                    elif "call" in option_side_hint:
                        tick_fields["direction_volume_pct"] = float(cv) / total
            except (TypeError, ValueError, ZeroDivisionError):
                pass
        # Inherit null chain/owned fields from prior path (esp. shadow ticks).
        try:
            from profit_optimizer.path_chain_skew import fill_tick_fields_from_prior_path

            prior = getattr(rec, "premium_path", None) if rec is not None else None
            fill_tick_fields_from_prior_path(
                tick_fields, prior, option_side=option_side_hint
            )
        except Exception:
            pass
        try:
            from profit_optimizer.trading_hooks import stamp_owned_monitor_review_keys

            stamp_owned_monitor_review_keys(
                None,
                tick_fields,
                price=_f(tick_fields.get("underlying_price") or underlying_price),
                strike=_f(tick_fields.get("strike") or snap.get("strike") or oq.get("strike")),
                option_side=option_side_hint,
            )
        except Exception:
            pass
        if is_shadow:
            tick_fields["shadow_quote_exact"] = shadow_exact
            tick_fields["shadow_option_symbol"] = shadow_sym
            trust = str(
                oq.get("shadow_trust")
                or snap.get("shadow_trust")
                or ""
            )
            if trust:
                tick_fields["shadow_trust"] = trust
            if oq.get("quote_valid") is not None:
                tick_fields["quote_valid"] = bool(oq.get("quote_valid"))
            if oq.get("freshness_status") or snap.get("freshness_status"):
                tick_fields["freshness_status"] = str(
                    oq.get("freshness_status") or snap.get("freshness_status")
                )
            if oq.get("contract_match_status") or snap.get("contract_match_status"):
                tick_fields["contract_match_status"] = str(
                    oq.get("contract_match_status") or snap.get("contract_match_status")
                )
            # Preserve exact-contract vs chain source — never emit bare ``exact``.
            if shadow_exact and not trust:
                tick_fields["premium_source"] = "exact_quote"
                tick_fields["shadow_trust"] = "trusted_exact"
                tick_fields["quote_quality"] = quote_grade or "live_single_leg"
                tick_fields["mark_quality"] = str(snap.get("mark_quality") or "live_single_leg")
            elif trust == "trusted_exact":
                tick_fields["premium_source"] = "exact_quote"
            elif trust in ("trusted_chain", "degraded_chain"):
                # Keep chain premium_source already set on tick_fields.
                pass
        else:
            # Live shared-snapshot trust labels.
            src_l = str(tick_fields.get("premium_source") or "").lower()
            try:
                from modules.exit_grade_durability import quote_source_is_exact_contract

                if quote_source_is_exact_contract(src_l):
                    tick_fields["shadow_trust"] = "trusted_exact"
                elif "fresh_chain" in src_l or src_l.startswith("chain"):
                    tick_fields["shadow_trust"] = "trusted_chain"
            except Exception:
                if "exact_contract" in src_l:
                    tick_fields["shadow_trust"] = "trusted_exact"
                elif "fresh_chain" in src_l or src_l.startswith("chain"):
                    tick_fields["shadow_trust"] = "trusted_chain"
            if snap.get("freshness_status"):
                tick_fields["freshness_status"] = str(snap.get("freshness_status"))
        self._recorder.observe_premium_tick(trade_id, tick_fields)
        self._recorder.observe_underlying_tick(
            trade_id,
            {
                "underlying_price": underlying_price
                if underlying_price is not None
                else tick_fields.get("underlying_price"),
                "underlying_last": tick_fields.get("underlying_last"),
                "timestamp": tick_fields.get("timestamp") or _now_iso(),
                "volume": tick_fields.get("volume"),
            },
        )

    def observe_so_price_tick(
        self,
        *,
        trade_id: str,
        symbol: str,
        underlying_price: float,
        volume: Optional[float] = None,
    ) -> None:
        if not self._recorder:
            return
        _ = symbol
        self._recorder.observe_underlying_tick(
            trade_id,
            {"underlying_price": underlying_price, "volume": volume, "timestamp": _now_iso()},
        )

    def observe_exit(
        self,
        *,
        trade_id: str,
        exit_premium: Optional[float] = None,
        exit_underlying: Optional[float] = None,
        exit_reason: str = "",
        realized_pnl_pct: Optional[float] = None,
        extra: Optional[Dict[str, Any]] = None,
        frozen_symbol_bid: Optional[float] = None,
    ) -> None:
        if not self._recorder:
            return
        extra_bag = extra if isinstance(extra, dict) else {}
        frozen = frozen_symbol_bid
        if frozen is None:
            frozen = extra_bag.get("frozen_symbol_bid") or extra_bag.get("entry_option_bid")
        if frozen is None:
            oq = extra_bag.get("option_quote")
            if isinstance(oq, dict):
                frozen = oq.get("bid")
        self._recorder.capture_exit(
            trade_id,
            {
                "exit_premium": exit_premium,
                "exit_underlying": exit_underlying,
                "exit_reason": exit_reason,
                "realized_pnl_pct": realized_pnl_pct,
                "exit_time": _now_iso(),
                "frozen_symbol_bid": frozen,
                "extra": extra,
            },
        )

    def finalize_session(self, *, run_note: str = "session_finalize") -> None:
        if not self._recorder:
            return
        self._recorder.persist_daily(run_note=run_note)


def _now_iso() -> str:
    return normalize_timestamp_iso(datetime.now(ZoneInfo("UTC")))


def get_profit_optimizer() -> Optional[ProfitOptimizer]:
    global _PO_SINGLETON
    if not profit_optimizer_enabled():
        return None
    if _PO_SINGLETON is None:
        _PO_SINGLETON = ProfitOptimizer()
    return _PO_SINGLETON


def reset_profit_optimizer_singleton() -> None:
    """Test helper — clear module singleton."""
    global _PO_SINGLETON
    _PO_SINGLETON = None


def safe_observe(label: str, fn: Any) -> None:
    """Trading hooks should call this wrapper — never raise into execution."""
    run_safe(label, fn)


def _pick(d: Dict[str, Any], *keys: str) -> Optional[float]:
    for k in keys:
        if k in d and d[k] is not None:
            try:
                v = float(d[k])
                if v > 0 or k in ("spread_pct",):
                    return v
            except (TypeError, ValueError):
                continue
    return None


def _f(x: Any) -> Optional[float]:
    try:
        if x is None:
            return None
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _normalize_premium_source(source: str, snap: Dict[str, Any]) -> str:
    s = str(source or "").strip().lower()
    telem = str(snap.get("price_source_telemetry") or "").lower()
    if s in ("option_mark", "option_mid", "spread_mtm_mid_last"):
        return "exact_quote"
    if s == "exact":
        return "chain_strike_match"
    if s in ("option_last",):
        return "nearest"
    if "cached" in s:
        return "cached_quote"
    if "partial" in s or snap.get("mark_quality") == "partial_leg":
        return "partial_leg"
    if "synthetic" in s or "modeled" in s or telem == "synthetic":
        return "synthetic_modeled"
    if s:
        return s
    return "unknown"
