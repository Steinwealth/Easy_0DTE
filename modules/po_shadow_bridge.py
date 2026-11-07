"""Profit Optimizer shadow bridge.

The flight recorder lives in profit_optimizer.trading_hooks. These wrappers
keep that work off prime_trading_system.py. Snapshot writes that used to sit
inside the exit observer stay a separate call.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("prime_trading_system_optimized")


class ProfitOptimizerBridgeMixin:
    def _get_profit_optimizer(self):
        """FINAL: read-only profit_optimizer flight recorder (never affects execution)."""
        try:
            from profit_optimizer.trading_hooks import ensure_profit_optimizer

            return ensure_profit_optimizer(self)
        except Exception as exc:
            log.warning("PROFIT_OPTIMIZER | action=init_failed | error=%s", exc)
            return None

    def _profit_optimizer_track_0dte_fill(self, **kwargs) -> None:
        try:
            from profit_optimizer.trading_hooks import track_0dte_fill

            track_0dte_fill(self, **kwargs)
        except Exception as exc:
            log.debug("PROFIT_OPTIMIZER | track_0dte_fill_skip | error=%s", exc)

    def _profit_optimizer_track_trendline_fill(self, **kwargs) -> None:
        try:
            from profit_optimizer.trading_hooks import track_trendline_fill

            track_trendline_fill(self, **kwargs)
        except Exception as exc:
            log.debug("PROFIT_OPTIMIZER | track_trendline_fill_skip | error=%s", exc)

    def _profit_optimizer_track_breakout_fill(self, **kwargs) -> None:
        try:
            from profit_optimizer.trading_hooks import track_breakout_fill

            track_breakout_fill(self, **kwargs)
        except Exception as exc:
            log.debug("PROFIT_OPTIMIZER | track_breakout_fill_skip | error=%s", exc)

    def _profit_optimizer_track_so_fill(self, **kwargs) -> None:
        try:
            from profit_optimizer.trading_hooks import track_so_fill

            track_so_fill(self, **kwargs)
        except Exception as exc:
            log.debug("PROFIT_OPTIMIZER | track_so_fill_skip | error=%s", exc)

    def _profit_optimizer_index_dte0_collection(self, signals) -> None:
        try:
            from profit_optimizer.trading_hooks import index_dte0_collection

            index_dte0_collection(self, signals)
        except Exception as exc:
            log.debug("PROFIT_OPTIMIZER | index_dte0_collection_skip | error=%s", exc)

    async def _profit_optimizer_attach_so_premium_shadow(self, **kwargs) -> None:
        try:
            from profit_optimizer.trading_hooks import attach_so_premium_shadow

            await attach_so_premium_shadow(self, **kwargs)
        except Exception as exc:
            log.debug("PROFIT_OPTIMIZER | attach_so_premium_shadow_skip | error=%s", exc)

    def _profit_optimizer_observe_so_tick(self, symbol, market_data) -> None:
        try:
            from profit_optimizer.trading_hooks import observe_so_tick

            observe_so_tick(self, symbol, market_data)
        except Exception as exc:
            log.debug("PROFIT_OPTIMIZER | observe_so_tick_skip | error=%s", exc)

    def _profit_optimizer_observe_so_exit(self, symbol, market_data, decision) -> None:
        try:
            from profit_optimizer.trading_hooks import observe_so_exit

            observe_so_exit(self, symbol, market_data, decision)
        except Exception as exc:
            log.debug("PROFIT_OPTIMIZER | observe_so_exit_skip | error=%s", exc)

    def _profit_optimizer_observe_0dte_tick(self, **kwargs) -> None:
        try:
            from profit_optimizer.trading_hooks import observe_0dte_tick

            observe_0dte_tick(self, **kwargs)
        except Exception as exc:
            log.debug("PROFIT_OPTIMIZER | observe_0dte_tick_skip | error=%s", exc)

    def _profit_optimizer_observe_0dte_exit(self, **kwargs) -> None:
        """Record PO exit + Priority Optimizer exit snapshot (ORB path)."""
        try:
            from profit_optimizer.trading_hooks import observe_0dte_exit

            observe_0dte_exit(
                self,
                trade_id=str(kwargs.get("trade_id") or ""),
                exit_premium=float(kwargs.get("exit_premium") or 0.0),
                exit_underlying=float(kwargs.get("exit_underlying") or 0.0),
                exit_reason=str(kwargs.get("exit_reason") or ""),
                realized_pnl_pct=float(
                    kwargs.get("realized_pnl_pct")
                    if kwargs.get("realized_pnl_pct") is not None
                    else (kwargs.get("realized_pnl") or 0.0)
                ),
                frozen_symbol_bid=kwargs.get("frozen_symbol_bid"),
                extra={
                    k: v
                    for k, v in kwargs.items()
                    if k
                    not in (
                        "trade_id",
                        "exit_premium",
                        "exit_underlying",
                        "exit_reason",
                        "realized_pnl_pct",
                        "realized_pnl",
                        "frozen_symbol_bid",
                    )
                }
                or None,
            )
        except Exception as exc:
            log.debug("PROFIT_OPTIMIZER | observe_0dte_exit_skip | error=%s", exc)
        # Priority Optimizer exit artifact — independent of PO flight recorder
        try:
            if kwargs.get("_skip_po_exit_snapshot"):
                return None
            trade_id = str(kwargs.get("trade_id") or "")
            stage_hint = str(
                kwargs.get("strategy")
                or kwargs.get("source_path")
                or kwargs.get("path")
                or kwargs.get("strategy_type")
                or ""
            ).lower()
            pos = kwargs.get("position")
            if not stage_hint and pos is not None:
                try:
                    from modules.unified_0dte_account import _position_strategy_type

                    stage_hint = str(_position_strategy_type(pos) or "").lower()
                except Exception:
                    stage_hint = str(getattr(pos, "strategy_type", "") or "").lower()
            trendline_close = "trendline" in stage_hint or "tline" in stage_hint
            if not trendline_close:
                try:
                    for c in getattr(self, "_pending_trendline_candidates", None) or []:
                        if str(getattr(c, "executed_trade_id", "") or "") == trade_id:
                            trendline_close = True
                            break
                except Exception:
                    pass
            sym = str(kwargs.get("symbol") or "")
            if not sym and pos is not None:
                sym = str(getattr(pos, "symbol", "") or "")
            if not sym:
                try:
                    from .po_snapshot_hydration import resolve_underlying_symbol

                    sym = resolve_underlying_symbol(
                        symbol=sym,
                        trade_id=trade_id,
                        position=pos,
                    )
                except Exception:
                    pass
            snap_kwargs = dict(
                symbol=sym,
                trade_id=trade_id,
                option_side=kwargs.get("option_side"),
                exit_reason=str(kwargs.get("exit_reason") or ""),
                exit_premium=kwargs.get("exit_premium"),
                entry_premium=kwargs.get("entry_premium"),
                realized_pnl=kwargs.get("realized_pnl"),
                realized_pnl_pct=kwargs.get("realized_pnl_pct"),
                exit_underlying=kwargs.get("exit_underlying"),
                position=pos,
                extra={"via": "profit_optimizer_observe_0dte_exit"},
            )
            if trendline_close:
                self._save_trendline_exit_priority_snapshot(**snap_kwargs)
            elif "breakout" in stage_hint:
                self._save_breakout_exit_priority_snapshot(**snap_kwargs)
            else:
                self._save_orb_0dte_exit_priority_snapshot(**snap_kwargs)
        except Exception as exc:
            log.warning("0DTE exit snapshot via observe failed: %s", exc)
        return None

    def _profit_optimizer_finalize_eod(self) -> None:
        try:
            from profit_optimizer.trading_hooks import finalize_eod

            finalize_eod(self)
        except Exception as exc:
            log.warning("PROFIT_OPTIMIZER | finalize_eod_failed | error=%s", exc)

