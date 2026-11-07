#!/usr/bin/env python3
"""
Breakout options executor — reuses Trendline lotto open path with strategy_type=breakout_0dte.

Adds Jul 27 contract gates:
  - Prefer near-ATM (OTM ≤ ~10–12%; research soft ceiling ≤15%)
  - Soft-reject deep lotto; stamp otm_pct + chosen δ
  - Cheap-lotto size guard (premium < $0.15 + large qty unless near ATM)
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from easyTrendline.trendline_options_executor import TrendlineOptionsExecutor

log = logging.getLogger(__name__)


def _breakout_cfg(system_or_none: Any = None) -> Any:
    try:
        from easyBreakout.breakout_config_loader import load_breakout_config_from_env

        if system_or_none is not None:
            cached = getattr(system_or_none, "_breakout_config", None)
            if cached is not None:
                return cached
        try:
            from modules.config_loader import get_config_value

            return load_breakout_config_from_env(get_config_value)
        except Exception:
            return load_breakout_config_from_env()
    except Exception:
        from easyBreakout.breakout_models import BreakoutConfig

        return BreakoutConfig()


class BreakoutOptionsExecutor(TrendlineOptionsExecutor):
    """Same contract selection / unified lotto open; tags fills as breakout_0dte."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # Skip Trendline geometry veto — Breakout uses flat ORB high/low levels.
        kwargs.setdefault("trendline_signal_config", None)
        super().__init__(*args, **kwargs)
        self.path_strategy_type = "breakout_0dte"
        self.path_strategy_name = "easyBreakout_0DTE"
        self.path_entry_type = "orb_range_breakout"
        self.path_trigger_type = "orb_breakout_continuation"
        self.path_log_prefix = "BREAKOUT"
        self._otm_gate_reject_log_ts: Dict[Tuple[str, str], float] = {}

    def _bo_cfg(self) -> Any:
        return _breakout_cfg(getattr(self, "system", None) or getattr(self, "trading_system", None))

    def _log_otm_gate_reject(
        self,
        *,
        stage: str,
        symbol: Any,
        reason: Any,
        deep_skipped: int = 0,
        otm_pct: Any = None,
        coalesce_sec: float = 60.0,
    ) -> None:
        """INFO-level expected rejects; coalesce per symbol+reason to cut log spam."""
        sym = str(symbol or "?").upper()
        why = str(reason or "unknown")
        key = (sym, f"{stage}:{why}")
        now = time.time()
        cache = getattr(self, "_otm_gate_reject_log_ts", None)
        if not isinstance(cache, dict):
            cache = {}
            self._otm_gate_reject_log_ts = cache
        last = float(cache.get(key, 0.0) or 0.0)
        if now - last < float(coalesce_sec):
            return
        cache[key] = now
        if len(cache) > 64:
            cutoff = now - max(120.0, float(coalesce_sec) * 2.0)
            self._otm_gate_reject_log_ts = {
                k: v for k, v in cache.items() if float(v or 0.0) >= cutoff
            }
        if stage == "fallback_reject":
            log.info(
                "BREAKOUT_OTM_GATE | stage=fallback_reject | symbol=%s | otm_pct=%s | reason=%s",
                sym,
                otm_pct if otm_pct is not None else "none",
                why,
            )
            return
        log.info(
            "BREAKOUT_OTM_GATE | stage=reject | symbol=%s | reason=%s | deep_skipped=%d | "
            "note=research_exception_watch_amat_class",
            sym,
            why,
            int(deep_skipped),
        )

    def _stamp_otm_on_hint(
        self,
        hint: Dict[str, Any],
        signal: Any,
        underlying_spot: float,
        bo_cfg: Any,
    ) -> Optional[Dict[str, Any]]:
        from easyBreakout.breakout_contract_gates import (
            compute_otm_pct,
            evaluate_otm_gate,
            stamp_contract_fields,
        )

        side = str(getattr(signal, "option_side", "") or "").lower()
        try:
            strike = float(hint.get("strike") or 0.0)
        except (TypeError, ValueError):
            strike = 0.0
        otm = compute_otm_pct(underlying_spot, strike, side)
        try:
            delta = float(hint.get("delta")) if hint.get("delta") is not None else None
        except (TypeError, ValueError):
            delta = None
        decision = evaluate_otm_gate(
            otm,
            enabled=bool(getattr(bo_cfg, "otm_gate_enabled", True)),
            prefer_max=float(getattr(bo_cfg, "prefer_max_otm_pct", 0.055) or 0.055),
            research_max=float(getattr(bo_cfg, "research_max_otm_pct", 0.055) or 0.055),
        )
        if not decision.ok:
            self._chain_resolve_reject_reason = decision.reason or "contract_otm_too_deep"
            self._log_otm_gate_reject(
                stage="fallback_reject",
                symbol=getattr(signal, "symbol", ""),
                reason=decision.reason,
                otm_pct=f"{otm:.4f}" if otm is not None else "none",
            )
            return None
        out = dict(hint)
        out["otm_pct"] = otm
        out["chosen_delta"] = delta
        out["fill_delta"] = delta
        out["otm_research_exception"] = bool(decision.research_exception)
        stamp_contract_fields(
            signal,
            otm_pct=otm,
            chosen_delta=delta,
            research_exception=bool(decision.research_exception),
        )
        return out

    async def _resolve_chain_hint(
        self,
        signal: Any,
        spot: float,
        chain_hint: Optional[Dict[str, Any]],
        *,
        max_contract_cost: Optional[float] = None,
    ):
        hint, source = await super()._resolve_chain_hint(
            signal, spot, chain_hint, max_contract_cost=max_contract_cost
        )
        if hint is None:
            return None, source
        if str(hint.get("selected_by") or "") == "single_tp":
            return hint, source
        stamped = self._stamp_otm_on_hint(hint, signal, float(spot), self._bo_cfg())
        if stamped is None:
            return None, source
        return stamped, source

    async def _fetch_live_chain_hint(
        self,
        signal: Any,
        underlying_spot: float,
        *,
        relaxed: bool = False,
        max_contract_cost: Optional[float] = None,
        prefer_manager_cache: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Select Breakout contract: delta band + OTM preference (near ATM first)."""
        from easyBreakout.breakout_contract_gates import (
            compute_otm_pct,
            evaluate_otm_gate,
            stamp_contract_fields,
        )
        from easyTrendline.trendline_options_executor import (
            _expiry_ymd_trendline,
            _normalize_expiry_ymd,
        )

        side = str(getattr(signal, "option_side", "") or "").lower()
        if side not in ("call", "put"):
            self._chain_resolve_reject_reason = "no_contract_in_band"
            return None

        cfg = self.option_config
        bo_cfg = self._bo_cfg()
        expiry = _expiry_ymd_trendline()
        chain, chain_source = await self._fetch_options_chain_dict(
            symbol=signal.symbol,
            expiry=expiry,
            underlying_spot=underlying_spot,
            strike_count=30 if relaxed else 20,
            prefer_manager_cache=prefer_manager_cache,
        )
        self._chain_resolve_last_source = str(chain_source or "unavailable")
        if not isinstance(chain, dict):
            self._chain_resolve_reject_reason = "live_chain_required_unavailable"
            self._chain_resolve_filter_stats = {
                "total_contracts": 0,
                "after_bid": 0,
                "after_delta": 0,
                "after_premium": 0,
                "after_spread": 0,
                "after_volume": 0,
                "after_open_interest": 0,
                "chain_source": self._chain_resolve_last_source,
            }
            return None

        contracts = chain.get("calls" if side == "call" else "puts", [])
        if not isinstance(contracts, list) or not contracts:
            self._chain_resolve_reject_reason = "no_contract_in_band"
            return None

        from easy0DTE.modules.option_entry_liquidity_validator import (
            entry_max_premium,
            entry_min_open_interest,
            entry_min_premium,
            entry_min_volume,
        )
        from easy0DTE.modules.unified_contract_select import (
            contract_ask,
            near_atm_sort_key,
            pick_with_premium_delta_walk,
        )
        try:
            from easy0DTE.modules.dte0_entry_selection import abs_contract_delta
        except ImportError:
            abs_contract_delta = lambda c: abs(float(getattr(c, "delta", 0.0) or 0.0))  # type: ignore

        hint_min_vol = entry_min_volume()
        hint_min_oi = entry_min_open_interest()
        min_prem = entry_min_premium()
        max_prem = entry_max_premium()
        cfg_delta_max = float(cfg.delta_max)
        max_delta_with_tolerance = cfg_delta_max + self.delta_tolerance
        from modules.spread_policy import cheap_allowance_applies as _sp_cheap
        from modules.spread_policy import path_spread_cap_points as _sp_cap

        max_spread_pct = _sp_cap("breakout_0dte") / 100.0
        strike_step = self._option_strike_step(signal.symbol, underlying_spot)

        prefer_max = float(getattr(bo_cfg, "prefer_max_otm_pct", 0.055) or 0.055)
        research_max = float(getattr(bo_cfg, "research_max_otm_pct", 0.055) or 0.055)
        otm_enabled = bool(getattr(bo_cfg, "otm_gate_enabled", True))

        total = 0
        after_bid = 0
        after_oi = 0
        after_vol = 0
        after_spread = 0
        after_delta = 0
        after_premium = 0
        # (prefer_rank, otm, -delta, dist, contract)
        in_band: List[Tuple[float, float, float, float, Any]] = []
        deep_skipped = 0
        for c in contracts:
            try:
                strike = float(getattr(c, "strike", 0.0) or 0.0)
                delta = abs_contract_delta(c)
                bid = float(getattr(c, "bid", 0.0) or 0.0)
                ask = float(getattr(c, "ask", 0.0) or 0.0)
                oi = int(getattr(c, "open_interest", 0) or 0)
                vol = int(getattr(c, "volume", 0) or 0)
            except (TypeError, ValueError):
                continue
            if strike <= 0 or delta <= 0:
                continue
            total += 1
            if not relaxed and not (bid > 0 and ask > 0):
                continue
            after_bid += 1
            if not relaxed and hint_min_oi > 0 and oi < hint_min_oi:
                continue
            after_oi += 1
            if not relaxed and hint_min_vol > 0 and vol < hint_min_vol:
                continue
            after_vol += 1
            if bid > 0 and ask > 0:
                mid = (bid + ask) / 2.0
                if mid > 0 and ((ask - bid) / mid) > max_spread_pct and not _sp_cheap(bid, ask):
                    continue
            elif not relaxed:
                continue
            after_spread += 1
            if side == "call" and strike < underlying_spot:
                continue
            if side == "put" and strike > underlying_spot:
                continue
            # Allow δ below min so premium/δ walk can promote toward ATM.
            if delta > max_delta_with_tolerance + 1e-6:
                continue
            after_delta += 1
            otm = compute_otm_pct(underlying_spot, strike, side)
            if otm is None:
                continue
            if otm_enabled and otm > research_max + 1e-12:
                deep_skipped += 1
                continue
            if max_prem > 0 and contract_ask(c) > max_prem + 1e-12:
                continue
            after_premium += 1
            dist_penalty = abs(strike - underlying_spot) / max(underlying_spot, 1e-9)
            prefer_rank = 0.0 if otm <= prefer_max + 1e-12 else 1.0
            in_band.append((prefer_rank, float(otm), -float(delta), dist_penalty, c))

        self._chain_resolve_filter_stats = {
            "total_contracts": total,
            "after_bid": after_bid,
            "after_delta": after_delta,
            "after_premium": after_premium,
            "after_spread": after_spread,
            "after_volume": after_vol,
            "after_open_interest": after_oi,
            "chain_source": self._chain_resolve_last_source,
            "otm_deep_skipped": deep_skipped,
        }

        if deep_skipped:
            log.info(
                "BREAKOUT_OTM_GATE | stage=filter | symbol=%s | deep_skipped=%d | "
                "research_max=%.2f | grep=BREAKOUT_OTM_GATE",
                signal.symbol,
                deep_skipped,
                research_max,
            )

        if not in_band:
            self._chain_resolve_reject_reason = (
                "contract_otm_too_deep" if deep_skipped else (
                    "option_premium_too_high" if after_delta > 0 and after_premium <= 0 else "no_contract_in_band"
                )
            )
            self._log_otm_gate_reject(
                stage="reject",
                symbol=signal.symbol,
                reason=self._chain_resolve_reject_reason,
                deep_skipped=int(deep_skipped),
            )
            return None

        if max_contract_cost is not None and float(max_contract_cost) > 0.0:
            cap = float(max_contract_cost) * 1.02
            affordable = [
                row for row in in_band if (contract_ask(row[-1]) * 100.0) <= cap
            ]
            if affordable:
                in_band = affordable
            else:
                min_cost = min(contract_ask(row[-1]) * 100.0 for row in in_band)
                log.warning(
                    "BREAKOUT_CHAIN_NO_AFFORDABLE | symbol=%s | min_in_band_cost=%.2f | "
                    "slot_cap=%.2f | n_in_band=%d",
                    signal.symbol,
                    min_cost,
                    float(max_contract_cost),
                    len(in_band),
                )
                self._chain_resolve_no_affordable = True
                self._chain_resolve_reject_reason = "no_affordable_contract_for_slot"
                return None

        # Prefer ≤ prefer_max, then lower OTM, then higher delta (near ATM).
        in_band.sort(key=near_atm_sort_key)
        best, walk_reject, walk_diag = pick_with_premium_delta_walk(
            in_band,
            delta_min=float(cfg.delta_min),
            delta_max=float(max_delta_with_tolerance),
            min_premium=float(min_prem),
            abs_delta_fn=abs_contract_delta,
            symbol=str(signal.symbol),
            path="breakout",
        )
        if best is None:
            self._chain_resolve_reject_reason = walk_reject or "no_contract_in_band"
            log.warning(
                "BREAKOUT_UNIFIED_SELECT_REJECT | symbol=%s | reason=%s | diag=%s",
                signal.symbol,
                self._chain_resolve_reject_reason,
                walk_diag,
            )
            return None
        bid = float(getattr(best, "bid", 0.0) or 0.0)
        ask = float(getattr(best, "ask", 0.0) or 0.0)
        mid = float(getattr(best, "mid_price", 0.0) or 0.0)
        premium = ask if ask > 0 else (mid if mid > 0 else max(0.01, bid))
        strike = float(getattr(best, "strike", 0.0) or 0.0)
        delta = abs_contract_delta(best)
        otm = compute_otm_pct(underlying_spot, strike, side)
        otm_steps = max(1, int(round(abs(strike - underlying_spot) / max(strike_step, 1e-9))))
        decision = evaluate_otm_gate(
            otm,
            enabled=otm_enabled,
            prefer_max=prefer_max,
            research_max=research_max,
        )
        if not decision.ok:
            self._chain_resolve_reject_reason = decision.reason or "contract_otm_too_deep"
            return None

        # BO-QG-A10 post-select: |δ| / IV caps (soft-skip when IV missing).
        try:
            from easy0DTE.modules.quality_gate_stack import (
                PATH_BO,
                GateContext,
                run_stack,
            )

            iv_raw = float(getattr(best, "implied_volatility", 0.0) or 0.0) or None
            a10_ctx = GateContext(
                path=PATH_BO,
                symbol=str(signal.symbol or "").upper(),
                side="CALL" if side == "call" else "PUT",
                delta=float(delta),
                extras={
                    "delta": float(delta),
                    "iv": iv_raw,
                    "implied_volatility": iv_raw,
                },
            )
            a10 = run_stack(
                PATH_BO,
                a10_ctx,
                gate_ids=["delta_abs", "IV"],
                stop_on_reject=True,
            )
            if not a10.admitted:
                fr = a10.first_reject()
                reason = (
                    f"gate_stack:{fr.gate_id}:{fr.reason}"
                    if fr is not None
                    else "gate_stack:a10_reject"
                )
                self._chain_resolve_reject_reason = reason
                log.warning(
                    "BREAKOUT_QG_A10_REJECT | symbol=%s | reason=%s | delta=%.4f | iv=%s | "
                    "grep=BREAKOUT_QG_A10_REJECT|GATE_STACK_REPORT",
                    signal.symbol,
                    reason,
                    float(delta),
                    iv_raw,
                )
                return None
        except Exception as a10_exc:
            log.debug("BREAKOUT_QG_A10 skipped: %s", a10_exc)

        self._chain_resolve_reject_reason = None
        exp_ymd = _normalize_expiry_ymd(
            getattr(best, "expiry", None)
            or getattr(best, "expiration", None)
            or chain.get("selected_expiry")
            or chain.get("expiry")
        )
        calendar_today = _expiry_ymd_trendline()
        hint_out: Dict[str, Any] = {
            "strike": strike,
            "delta": delta,
            "premium": premium,
            "bid": bid,
            "ask": ask,
            "openInterest": int(getattr(best, "open_interest", 0) or 0),
            "volume": int(getattr(best, "volume", 0) or 0),
            "otm_steps": otm_steps,
            "otm_pct": otm,
            "chosen_delta": delta,
            "fill_delta": delta,
            "otm_research_exception": bool(decision.research_exception),
            "source": "live_chain",
            "chain_source": self._chain_resolve_last_source,
            "expiry_ymd": exp_ymd or calendar_today,
            "expiry": exp_ymd or calendar_today,
            "selected_expiry": exp_ymd or calendar_today,
            "osi_key": str(getattr(best, "osi_key", "") or ""),
            "display_symbol": str(getattr(best, "display_symbol", "") or ""),
            "gamma": float(getattr(best, "gamma", 0.0) or 0.0),
            "theta": float(getattr(best, "theta", 0.0) or 0.0),
            "vega": float(getattr(best, "vega", 0.0) or 0.0),
            "implied_volatility": float(getattr(best, "implied_volatility", 0.0) or 0.0),
        }
        stamp_contract_fields(
            signal,
            otm_pct=otm,
            chosen_delta=delta,
            research_exception=bool(decision.research_exception),
        )
        if decision.research_exception:
            log.info(
                "BREAKOUT_OTM_GATE | stage=research_exception | symbol=%s | otm_pct=%.4f | "
                "prefer_max=%.2f | research_max=%.2f | delta=%.4f | strike=%.2f | "
                "note=within_15pct_watch_amat_class",
                signal.symbol,
                float(otm or 0.0),
                prefer_max,
                research_max,
                delta,
                strike,
            )
        else:
            log.info(
                "BREAKOUT_OTM_GATE | stage=pass | symbol=%s | otm_pct=%.4f | delta=%.4f | strike=%.2f",
                signal.symbol,
                float(otm or 0.0),
                delta,
                strike,
            )
        return hint_out

    def build_execution_payload(self, signal: Any, **kwargs: Any) -> Dict[str, Any]:
        payload = super().build_execution_payload(signal, **kwargs)
        bo_cfg = self._bo_cfg()
        from easyBreakout.breakout_contract_gates import evaluate_cheap_lotto_guard

        self._last_breakout_reject = None
        md = getattr(signal, "metadata", None) if signal is not None else None
        md = md if isinstance(md, dict) else {}
        try:
            otm = payload.get("otm_pct")
            if otm is None:
                otm = md.get("otm_pct")
            if otm is not None:
                otm = float(otm)
        except (TypeError, ValueError):
            otm = None
        try:
            prem = float(payload.get("estimated_contract_price") or 0.0)
        except (TypeError, ValueError):
            prem = 0.0
        try:
            qty = int(payload.get("quantity") or 0)
        except (TypeError, ValueError):
            qty = 0

        if otm is not None:
            payload["otm_pct"] = otm
        if md.get("chosen_delta") is not None:
            payload["chosen_delta"] = md.get("chosen_delta")
            payload["fill_delta"] = md.get("chosen_delta")
        if md.get("otm_research_exception") is not None:
            payload["otm_research_exception"] = md.get("otm_research_exception")

        decision = evaluate_cheap_lotto_guard(
            premium=prem,
            quantity=qty,
            otm_pct=otm,
            enabled=bool(getattr(bo_cfg, "cheap_lotto_guard_enabled", True)),
            max_premium=float(getattr(bo_cfg, "cheap_lotto_max_premium", 0.15) or 0.15),
            max_qty=int(getattr(bo_cfg, "cheap_lotto_max_qty", 8) or 8),
            near_atm_otm_pct=float(getattr(bo_cfg, "cheap_lotto_near_atm_otm_pct", 0.05) or 0.05),
            downsize_to=int(getattr(bo_cfg, "cheap_lotto_downsize_to", 1) or 1),
            mode=str(getattr(bo_cfg, "cheap_lotto_mode", "reject") or "reject"),
        )
        if decision.action == "downsize":
            payload["quantity"] = int(decision.quantity)
            try:
                unit = float(payload.get("estimated_contract_price") or prem)
                payload["estimated_total_cost"] = float(decision.quantity) * unit * 100.0
            except Exception:
                pass
            payload["cheap_lotto_action"] = "downsize"
            payload["cheap_lotto_reason"] = decision.reason
            log.warning(
                "BREAKOUT_CHEAP_LOTTO | stage=downsize | symbol=%s | %s | qty=%d",
                getattr(signal, "symbol", ""),
                decision.reason,
                int(decision.quantity),
            )
        elif not decision.ok:
            payload["quantity"] = 0
            payload["reject_reason"] = decision.reason or "cheap_lotto_reject"
            payload["cheap_lotto_action"] = "reject"
            payload["cheap_lotto_reason"] = decision.reason
            self._last_breakout_reject = decision.reason or "cheap_lotto_reject"
            log.warning(
                "BREAKOUT_CHEAP_LOTTO | stage=reject | symbol=%s | %s | "
                "grep=BREAKOUT_CHEAP_LOTTO",
                getattr(signal, "symbol", ""),
                decision.reason,
            )
        return payload

    async def execute(self, signal: Any, *args: Any, **kwargs: Any) -> Any:
        self._last_breakout_reject = None
        result = await super().execute(signal, *args, **kwargs)
        reject = getattr(self, "_last_breakout_reject", None)
        if (
            reject
            and result is not None
            and not bool(getattr(result, "success", False))
            and str(getattr(result, "error", "") or "") == "contract_too_expensive_for_slot"
        ):
            from easyTrendline.trendline_options_executor import TrendlineTradeResult

            pl = dict(getattr(result, "order_payload", None) or {})
            pl["reject_reason"] = reject
            pl["cheap_lotto_action"] = "reject"
            return TrendlineTradeResult(
                symbol=result.symbol,
                direction=result.direction,
                success=False,
                executed_at=result.executed_at,
                signal_time=result.signal_time,
                account_mode=result.account_mode,
                order_payload=pl,
                error=reject,
            )
        return result
