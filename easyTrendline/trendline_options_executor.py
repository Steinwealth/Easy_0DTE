#!/usr/bin/env python3
"""
Dedicated options executor for Trendline strategy path.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from easy0DTE.modules.options_execution_normalize import (
    build_trendline_normalized_metadata,
    log_metadata_normalized,
    log_position_type_normalized,
    normalize_execution_result,
    validate_normalized_options_for_stealth,
)

from .trendline_models import (
    TrendlineConfig,
    TrendlineDirection,
    TrendlineOptionSelectionConfig,
    TrendlineTradeResult,
    TrendlineTradeSignal,
)

log = logging.getLogger(__name__)

_TRENDLINE_ENTRY_MOD = None


def sync_payload_to_filled_quantity(
    *,
    payload: Dict[str, Any],
    requested_quantity: int,
    filled_quantity: int,
    fill_premium: float,
) -> Tuple[int, float]:
    """Align order_payload qty/cost to the post-risk-clamp fill size.

    Liquidity Guard / book-depth can reduce contracts inside ``execute_lotto_sleeve``
    after budget sizing. Without this sync, fill alerts and PO ``position_size``
    keep the pre-clamp budget (Aug 7 IREN: alert 40/$626 vs filled 17/$255).

    Returns ``(quantity, estimated_total_cost)``.
    """
    req = max(0, int(requested_quantity or 0))
    filled = max(0, int(filled_quantity or 0))
    qty = filled if filled > 0 else max(1, req)
    prem = float(fill_premium or 0.0)
    if prem <= 0:
        try:
            prem = float(payload.get("estimated_contract_price") or payload.get("premium") or 0.0)
        except (TypeError, ValueError):
            prem = 0.0
    total = prem * qty * 100.0 if prem > 0 and qty > 0 else 0.0
    try:
        from modules.options_trade_economics import compute_opening_fees

        total += float(compute_opening_fees(quantity=qty, position_type="lotto") or 0.0)
    except Exception:
        pass
    payload["requested_quantity"] = req if req > 0 else qty
    payload["quantity"] = int(qty)
    payload["estimated_total_cost"] = float(total)
    if req > 0 and filled > 0 and filled != req:
        payload["liquidity_qty_clamped"] = True
        payload["requested_quantity"] = req
    return int(qty), float(total)


def resolve_true_position_size_usd(
    *,
    contracts: int,
    entry_premium: float,
    entry_cost: Optional[float] = None,
    estimated_total_cost: Optional[float] = None,
) -> Optional[float]:
    """Prefer actual contracts×premium (+fees) when payload cost implies a different qty."""
    qty = int(contracts or 0)
    prem = float(entry_premium or 0.0)
    notional = qty * prem * 100.0 if qty > 0 and prem > 0 else 0.0
    fees = 0.0
    if notional > 0:
        try:
            from modules.options_trade_economics import compute_opening_fees

            fees = float(compute_opening_fees(quantity=qty, position_type="lotto") or 0.0)
        except Exception:
            fees = 0.0
    true_cost = notional + fees if notional > 0 else 0.0

    for cand in (entry_cost, estimated_total_cost):
        try:
            c = float(cand) if cand is not None else 0.0
        except (TypeError, ValueError):
            c = 0.0
        if c <= 0 or prem <= 0 or qty <= 0:
            continue
        implied = c / (prem * 100.0)
        # Stale pre-clamp cost (e.g. 40 vs 17) — replace with true fill notional.
        if abs(implied - float(qty)) > 0.6:
            return float(true_cost) if true_cost > 0 else float(c)
        return float(c)
    return float(true_cost) if true_cost > 0 else None


def _tl_path_cap_points(executor, legacy_points: float) -> float:
    """Per-path spread cap. TL and BO share this executor."""
    _ = legacy_points
    from modules.spread_policy import path_spread_cap_points

    return float(path_spread_cap_points(getattr(executor, "path_strategy_type", "trendline_0dte")))


def _effective_trendline_max_spread_fraction(
    config_max: float,
    *,
    symbol: str = "",
    relax_spread_check: bool = False,
) -> float:
    """Path-owned Trendline cap (30% default) — no global LIVE broker MIN."""
    from modules.spread_policy import spread_fraction_from_env, trendline_pick_spread_cap_fraction

    path_cap = spread_fraction_from_env(float(config_max))
    return trendline_pick_spread_cap_fraction(
        symbol,
        relax_spread_check=relax_spread_check,
        path_cap_fraction=path_cap,
    )


def _dte0_entry_selection():
    global _TRENDLINE_ENTRY_MOD
    if _TRENDLINE_ENTRY_MOD is not None:
        return _TRENDLINE_ENTRY_MOD
    import importlib.util

    path = os.path.normpath(
        os.path.join(
            os.path.dirname(__file__),
            "..",
            "easy0DTE",
            "modules",
            "dte0_entry_selection.py",
        )
    )
    spec = importlib.util.spec_from_file_location("dte0_entry_selection_trendline", path)
    if spec is None or spec.loader is None:
        raise ImportError("dte0_entry_selection module not found")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _TRENDLINE_ENTRY_MOD = mod
    return mod


def _expiry_ymd_trendline() -> str:
    """US session calendar day for 0DTE chain lookup (E*TRADE YYYYMMDD)."""
    try:
        from zoneinfo import ZoneInfo

        return datetime.now(ZoneInfo("America/New_York")).strftime("%Y%m%d")
    except Exception:
        return datetime.now(timezone.utc).strftime("%Y%m%d")


def _normalize_expiry_ymd(raw: Any) -> str:
    s = str(raw or "").strip().replace("-", "")
    if len(s) >= 8 and s[:8].isdigit():
        return s[:8]
    return ""


@dataclass
class _ContractPick:
    strike: float
    delta_est: float
    premium: float
    otm_steps: int
    bid: Optional[float] = None
    ask: Optional[float] = None
    open_interest: Optional[int] = None
    volume: Optional[int] = None
    expiry_ymd: Optional[str] = None
    expiry_selection: Optional[Dict[str, Any]] = None

    @property
    def delta(self) -> float:
        """Alias for dte0_entry_selection / validate_trendline_delta_before_execute."""
        return float(self.delta_est)


class TrendlineOptionsExecutor:
    """
    Consume TrendlineTradeSignal and create execution payload/results.

    Demo-first implementation with delta / OTM-aware contract selection (0DTE intent).
    """

    def __init__(
        self,
        demo_mode: Optional[bool] = None,
        account_manager: Optional[Any] = None,
        option_config: Optional[TrendlineOptionSelectionConfig] = None,
        option_quote_api: Optional[Any] = None,
        require_live_chain_data: bool = True,
        trendline_signal_config: Optional[TrendlineConfig] = None,
        options_trading_executor: Optional[Any] = None,
        options_chain_manager: Optional[Any] = None,
    ) -> None:
        if demo_mode is None:
            try:
                from modules.runtime_mode import is_demo_mode

                demo_mode = is_demo_mode()
            except ImportError:
                demo_mode = os.getenv("MODE", "demo").lower() != "live"
        self.demo_mode = bool(demo_mode)
        self.account_manager = account_manager
        self.option_config = option_config or TrendlineOptionSelectionConfig()
        self.trendline_signal_config = trendline_signal_config
        self.option_quote_api = option_quote_api
        self.options_trading_executor = options_trading_executor
        self.options_chain_manager = options_chain_manager
        self.require_live_chain_data = bool(require_live_chain_data)
        self.options_chain_cache: Dict[str, Dict[str, Any]] = {}
        self._executed_trade_ids: set[str] = set()
        self._executed_candidate_ids: set[str] = set()
        self._last_contract_reject_diagnostics: Dict[str, Any] = {}
        self._chain_resolve_no_affordable = False
        self._chain_resolve_reject_reason: Optional[str] = None
        self._chain_resolve_filter_stats: Dict[str, Any] = {}
        # Path identity — BreakoutOptionsExecutor overrides these.
        self.path_strategy_type = "trendline_0dte"
        self.path_strategy_name = "easyTrendline_0DTE"
        self.path_entry_type = "trendline_break"
        self.path_trigger_type = "trendline_break_momentum"
        self.path_log_prefix = "TRENDLINE"
        self._chain_resolve_last_source: str = "unavailable"
        try:
            self.delta_tolerance = max(0.0, float(os.getenv("TRENDLINE_DELTA_TOLERANCE", "0.02")))
        except ValueError:
            self.delta_tolerance = 0.02

    def _reset_chain_resolve_diagnostics(self) -> None:
        self._chain_resolve_no_affordable = False
        self._chain_resolve_reject_reason = None
        self._chain_resolve_filter_stats = {}
        self._chain_resolve_last_source = "unavailable"

    def _log_prefix(self) -> str:
        return str(getattr(self, "path_log_prefix", "TRENDLINE") or "TRENDLINE").strip().upper()

    def _log_chain_filter_summary(self, *, symbol: str, reason: str, pc_oi_warmed: bool) -> None:
        stats = self._chain_resolve_filter_stats if isinstance(self._chain_resolve_filter_stats, dict) else {}
        log.warning(
            "%s_CHAIN_FILTER_SUMMARY | symbol=%s | reason=%s | total_contracts=%s | "
            "after_bid=%s | after_delta=%s | after_premium=%s | after_spread=%s | "
            "after_volume=%s | after_open_interest=%s | chain_source=%s | pc_oi_warmed=%s",
            self._log_prefix(),
            symbol,
            reason,
            stats.get("total_contracts", 0),
            stats.get("after_bid", 0),
            stats.get("after_delta", 0),
            stats.get("after_premium", 0),
            stats.get("after_spread", 0),
            stats.get("after_volume", 0),
            stats.get("after_open_interest", 0),
            str(self._chain_resolve_last_source or stats.get("chain_source") or "unavailable"),
            str(bool(pc_oi_warmed)).lower(),
        )

    def _validate_final_entry_execution(
        self,
        *,
        pick: _ContractPick,
        signal: TrendlineTradeSignal,
        sizing_budget: float,
        chain_source: str,
    ) -> Dict[str, Any]:
        """Pre-execution shared safety gate (quote age, canonical liquidity floors)."""
        from easy0DTE.modules.option_entry_liquidity_validator import (
            validate_option_entry_liquidity,
        )
        from modules.spread_policy import trendline_entry_max_spread_points

        q_age = None
        mgr = self._resolve_options_chain_manager()
        if mgr is not None and hasattr(mgr, "get_last_chain_fetch_meta"):
            try:
                meta = mgr.get_last_chain_fetch_meta(signal.symbol) or {}
                if meta.get("quote_age_seconds") is not None:
                    q_age = float(meta.get("quote_age_seconds"))
            except Exception:
                q_age = None
        allow_synthetic = os.getenv("TRENDLINE_ALLOW_SYNTHETIC_ENTRY", "false").lower() == "true"
        return validate_option_entry_liquidity(
            pick,
            path="trendline",
            max_spread_pct_points=float(_tl_path_cap_points(self, trendline_entry_max_spread_points())),
            budget=float(sizing_budget) if sizing_budget > 0 else None,
            quote_age_seconds=q_age,
            chain_source=chain_source,
            require_live_chain=self.require_live_chain_data,
            demo_mode=self.demo_mode,
            synthetic_allowed=allow_synthetic and self.demo_mode,
            enforce_quote_age=True,
            require_quote_fields=True,
        )

    def _resolve_options_chain_manager(self) -> Optional[Any]:
        """Shared 0DTE chain manager wired from main.py (pc_oi gate cache owner)."""
        return getattr(self, "options_chain_manager", None)

    async def _fetch_options_chain_dict(
        self,
        *,
        symbol: str,
        expiry: str,
        underlying_spot: float,
        strike_count: int = 20,
        prefer_manager_cache: bool = False,
    ) -> Tuple[Optional[Dict[str, Any]], str]:
        """
        Fetch chain for contract selection via the SINGLE 0DTE expiry policy
        (``OptionsChainManager`` / ``fetch_0dte_intent_chain``).

        No raw E*TRADE SelectedED fallback — that path could pick Friday (~3 DTE).
        """
        manager = self._resolve_options_chain_manager()
        try:
            from easy0DTE.modules.options_chain_manager import fetch_0dte_intent_chain

            chain = await fetch_0dte_intent_chain(
                manager,
                symbol,
                expiry=expiry,
                underlying_price=float(underlying_spot),
                use_cache=True,
            )
            if isinstance(chain, dict) and (chain.get("calls") or chain.get("puts")):
                source = "chain_cache" if prefer_manager_cache else "chain_manager"
                log.info(
                    "TRENDLINE_CHAIN_FETCH | symbol=%s | source=%s | expiry=%s | "
                    "selected=%s | mode=%s",
                    symbol,
                    source,
                    expiry,
                    chain.get("selected_expiry") or chain.get("expiry"),
                    chain.get("expiry_selection_mode"),
                )
                return chain, source
            log.warning(
                "TRENDLINE_CHAIN_FETCH | symbol=%s | source=chain_manager | empty=true | expiry=%s",
                symbol,
                expiry,
            )
        except Exception as exc:
            log.warning(
                "TRENDLINE_CHAIN_FETCH | symbol=%s | source=chain_manager | err=%s",
                symbol,
                exc,
            )
        return None, "unavailable"

    def _unified_cash_balance(self) -> float:
        """Cash on the unified 0DTE ledger (mock or live executor)."""
        ex = self.options_trading_executor
        if ex is not None:
            mock = getattr(ex, "mock_executor", None)
            if mock is not None:
                fn = getattr(mock, "available_balance", None)
                if callable(fn):
                    return float(fn())
                return float(getattr(mock, "account_balance", 0.0) or 0.0)
            if not getattr(ex, "demo_mode", self.demo_mode):
                adapter = getattr(ex, "broker_adapter", None)
                if adapter is not None:
                    bal = getattr(adapter, "_last_account_balance", None)
                    if bal is not None:
                        return float(getattr(bal, "cash_available", 0.0) or 0.0)
        try:
            from modules.unified_0dte_account import unified_demo_starting_balance

            return float(unified_demo_starting_balance())
        except Exception:
            return float(os.getenv("0DTE_DEMO_STARTING_BALANCE", "5000.0"))

    def _otm_steps_deterministic(self, symbol: str) -> int:
        cfg = self.option_config
        if cfg.strike_mode != "otm_1_to_2":
            return 1
        if _dte0_entry_selection().prefer_lowest_entry_delta():
            return 2
        h = sum(ord(c) for c in symbol.upper()) % 2
        if cfg.lotto_mode:
            return 2 if h == 0 else 1
        return 1 + h

    def _otm_steps_to_try(self, symbol: str) -> Tuple[int, ...]:
        primary = self._otm_steps_deterministic(symbol)
        if primary == 2:
            return (2, 1)
        return (1, 2)

    @staticmethod
    def _option_strike_step(symbol: str, spot: float) -> float:
        """Align with options_chain_manager synthetic/demo chain strike grid."""
        sym_u = str(symbol or "").upper()
        s = max(float(spot or 0.0), 0.01)
        if sym_u in ("RUT", "NDX") or s >= 1500:
            return 10.0
        if s >= 200:
            return 5.0
        if s >= 50:
            return 2.5
        if s >= 15:
            return 1.0
        return 0.5

    def _strike_for_side(
        self,
        symbol: str,
        spot: float,
        option_side: str,
        otm_steps: int,
    ) -> float:
        inc = self._option_strike_step(symbol, spot)
        side = str(option_side).lower()
        if side == "call":
            base = math.ceil(spot / inc) * inc
            return base + (otm_steps - 1) * inc
        base = math.floor(spot / inc) * inc
        return base - (otm_steps - 1) * inc

    def _estimate_delta(self, spot: float, strike: float, option_side: str, otm_steps: int) -> float:
        """Heuristic 0DTE delta proxy (not a pricing model)."""
        side = str(option_side).lower()
        if spot <= 0:
            return 0.25
        if side == "call":
            m = (spot - strike) / spot
        else:
            m = (strike - spot) / spot
        d = 0.45 + 2.2 * m - 0.04 * otm_steps
        return max(0.08, min(0.55, d))

    @staticmethod
    def _break_geometry_aligns_signal(signal: TrendlineTradeSignal) -> bool:
        be = signal.break_event
        close_p = float(be.close_price or 0.0)
        line_p = float(be.trendline_price or 0.0)
        if signal.direction == TrendlineDirection.BULL:
            return close_p < line_p
        return close_p > line_p

    def _estimate_premium(self, spot: float, delta: float, otm_steps: int) -> float:
        """Conservative per-share premium for demo sizing."""
        base = 0.08 + 0.25 * max(0.0, 0.42 - delta) + 0.02 * otm_steps
        return max(0.03, round(base + spot * 0.00015, 2))

    def _snap_strike_to_exchange_grid(
        self,
        symbol: str,
        spot: float,
        side: str,
        raw_strike: float,
    ) -> float:
        """Snap estimated strike to listed increment so monitor can match chain strikes."""
        step = self._option_strike_step(symbol, spot)
        if step <= 0:
            return float(raw_strike)
        side_l = str(side or "").lower()
        snapped = round(float(raw_strike) / step) * step
        if side_l == "call" and snapped <= spot:
            snapped += step
        elif side_l == "put" and snapped >= spot:
            snapped -= step
        return float(snapped)

    def select_contract(
        self,
        signal: TrendlineTradeSignal,
        underlying_spot: float,
        slot_capital: float,
        chain_hint: Optional[Dict[str, Any]] = None,
        *,
        relax_spread_check: bool = False,
    ) -> Tuple[Optional[_ContractPick], Optional[str]]:
        """
        Pick 0DTE long call/put consistent with delta band and OTM mode.
        chain_hint may include bid, ask, openInterest, volume for rejection checks.
        """
        self._last_contract_reject_diagnostics = {}
        cfg = self.option_config
        side = str(signal.option_side or "").lower()
        otm_steps = self._otm_steps_to_try(signal.symbol)
        otm = otm_steps[0]
        strike = self._strike_for_side(signal.symbol, underlying_spot, side, otm)
        delta_est = self._estimate_delta(underlying_spot, strike, side, otm)
        live_chain_selected = False
        expiry_ymd: Optional[str] = None
        expiry_selection_hint: Optional[Dict[str, Any]] = None
        if chain_hint:
            try:
                strike_hint = float(chain_hint.get("strike")) if chain_hint.get("strike") is not None else None
            except (TypeError, ValueError):
                strike_hint = None
            try:
                delta_hint = float(chain_hint.get("delta")) if chain_hint.get("delta") is not None else None
            except (TypeError, ValueError):
                delta_hint = None
            expiry_ymd = _normalize_expiry_ymd(
                chain_hint.get("expiry_ymd")
                or chain_hint.get("expiry")
                or chain_hint.get("expiration")
                or chain_hint.get("selected_expiry")
            ) or None
            try:
                from modules.calendar_expiry_metadata import (
                    expiry_fields_from_chain,
                    extract_expiry_fields_from_mapping,
                )

                expiry_selection_hint = extract_expiry_fields_from_mapping(chain_hint)
                if not expiry_selection_hint:
                    expiry_selection_hint = expiry_fields_from_chain(chain_hint) or None
            except Exception:
                expiry_selection_hint = None
            if strike_hint is not None and delta_hint is not None:
                strike = strike_hint
                delta_est = abs(delta_hint)
                live_chain_selected = True
        max_delta_with_tolerance = cfg.delta_max + self.delta_tolerance
        locked_pick = bool(chain_hint and str(chain_hint.get("selected_by") or "") == "single_tp")
        if (
            not locked_pick
            and (delta_est < cfg.delta_min - 1e-6 or delta_est > max_delta_with_tolerance + 1e-6)
        ):
            if live_chain_selected:
                log.warning(
                    "TRENDLINE_PIPELINE | stage=contract_rejected | symbol=%s | reason=delta_out_of_band | "
                    "delta=%.4f | strike=%.2f | source=live_chain",
                    signal.symbol,
                    delta_est,
                    strike,
                )
                return None, "delta_out_of_band"
            picked = False
            for alt_otm in otm_steps[1:]:
                strike2 = self._strike_for_side(signal.symbol, underlying_spot, signal.option_side, alt_otm)
                d2 = self._estimate_delta(underlying_spot, strike2, signal.option_side, alt_otm)
                if cfg.delta_min <= d2 <= max_delta_with_tolerance:
                    otm, strike, delta_est = alt_otm, strike2, d2
                    picked = True
                    break
            if not picked:
                log.warning(
                    "TRENDLINE_PIPELINE | stage=contract_rejected | symbol=%s | reason=delta_out_of_band | delta=%.4f",
                    signal.symbol,
                    delta_est,
                )
                return None, "delta_out_of_band"
        if cfg.delta_max + 1e-6 < delta_est <= max_delta_with_tolerance + 1e-6:
            log.info(
                "TRENDLINE_DELTA_TOLERANCE_USED | symbol=%s | delta=%.4f | max=%.4f | tolerance=%.4f",
                signal.symbol,
                delta_est,
                cfg.delta_max,
                self.delta_tolerance,
            )

        prem = self._estimate_premium(underlying_spot, delta_est, otm)
        bid = ask = oi = vol = None
        if chain_hint:
            bid = chain_hint.get("bid")
            ask = chain_hint.get("ask")
            oi = chain_hint.get("openInterest") or chain_hint.get("open_interest")
            vol = chain_hint.get("volume")
            if chain_hint.get("premium") is not None:
                try:
                    prem = max(0.01, float(chain_hint.get("premium")))
                    live_chain_selected = True
                except (TypeError, ValueError):
                    pass
        if bid is not None and ask is not None:
            bid_f, ask_f = float(bid), float(ask)
            hint_mid = chain_hint.get("mid") if chain_hint else None
            try:
                mark_mid = float(hint_mid) if hint_mid is not None else 0.0
            except (TypeError, ValueError):
                mark_mid = 0.0
            mid = (
                (bid_f + ask_f) / 2.0
                if bid_f > 0 and ask_f > 0
                else (mark_mid if mark_mid > 0 else max(prem, 0.01))
            )
            prem = max(prem, ask_f if ask_f > 0 else mid)

        from easy0DTE.modules.option_entry_liquidity_validator import (
            validate_option_entry_liquidity,
            validator_diagnostics_payload,
        )
        from modules.spread_policy import trendline_entry_max_spread_points

        chain_src = str((chain_hint or {}).get("chain_source") or "")
        q_age = (chain_hint or {}).get("quote_age_seconds")
        allow_synthetic = os.getenv("TRENDLINE_ALLOW_SYNTHETIC_ENTRY", "false").lower() == "true"
        val = validate_option_entry_liquidity(
            {
                "bid": bid,
                "ask": ask,
                "volume": vol,
                "open_interest": oi,
                "premium": prem,
                "delta": delta_est,
                "quote_age_seconds": q_age,
            },
            path="trendline",
            max_spread_pct_points=float(_tl_path_cap_points(self, trendline_entry_max_spread_points(
                relax_spread_check=relax_spread_check,
            ))),
            budget=float(slot_capital) if slot_capital > 0 else None,
            quote_age_seconds=float(q_age) if q_age is not None else None,
            chain_source=chain_src or None,
            require_live_chain=self.require_live_chain_data,
            demo_mode=self.demo_mode,
            synthetic_allowed=allow_synthetic and self.demo_mode,
            enforce_quote_age=None,
            require_quote_fields=bool(
                not locked_pick
                and (
                    live_chain_selected
                    or (bid is not None and ask is not None and float(bid or 0) > 0 and float(ask or 0) > 0)
                )
            ),
        )
        if not val.get("passed"):
            code = str(val.get("reason_code") or "option_liquidity_failed")
            self._last_contract_reject_diagnostics = validator_diagnostics_payload(val)
            log.warning(
                "TRENDLINE_OPTION_REJECT_ILLIQUID | symbol=%s | %s",
                signal.symbol,
                str(val.get("reason_text") or code),
            )
            log.warning(
                "TRENDLINE_PIPELINE | stage=contract_rejected | symbol=%s | reason=%s",
                signal.symbol,
                code,
            )
            return None, code

        if not live_chain_selected:
            requested = float(strike)
            strike = self._snap_strike_to_exchange_grid(signal.symbol, underlying_spot, side, strike)
            if abs(strike - requested) >= 0.01:
                log.warning(
                    "TRENDLINE_STRIKE_ALIGNED | symbol=%s | requested_strike=%.2f | matched_strike=%.2f | "
                    "diff=%.2f | source=exchange_grid",
                    signal.symbol,
                    requested,
                    strike,
                    abs(strike - requested),
                )

        pick = _ContractPick(
            strike=strike,
            delta_est=delta_est,
            premium=prem,
            otm_steps=otm,
            bid=bid if bid is not None else None,
            ask=ask if ask is not None else None,
            open_interest=int(oi) if oi is not None else None,
            volume=int(vol) if vol is not None else None,
            expiry_ymd=expiry_ymd,
            expiry_selection=expiry_selection_hint,
        )
        log.info(
            "%s_PIPELINE | stage=contract_selected | symbol=%s | option_side=%s | strike=%.2f | delta=%.3f | premium=%.4f | otm_steps=%d | source=%s",
            self._log_prefix(),
            signal.symbol,
            signal.option_side,
            strike,
            delta_est,
            prem,
            otm,
            "live_chain" if live_chain_selected else "estimated",
        )
        log.warning(
            "%s_OPTION_SELECTION | symbol=%s | option_side=%s | strike=%.2f | delta=%.3f | premium=%.4f | otm_steps=%d | source=%s",
            self._log_prefix(),
            signal.symbol,
            signal.option_side,
            strike,
            delta_est,
            prem,
            otm,
            "live_chain" if live_chain_selected else "estimated",
        )
        return pick, None

    def _listed_strike_for_execution(
        self,
        symbol: str,
        spot: float,
        side: str,
        pick: _ContractPick,
        chain_hint: Optional[Dict[str, Any]],
    ) -> float:
        """Use exact chain-listed strike when contract came from live chain data."""
        listed_strike = float(pick.strike)
        if chain_hint and chain_hint.get("strike") is not None:
            listed_strike = float(chain_hint["strike"])
        if chain_hint and str(chain_hint.get("selected_by") or "") == "single_tp":
            return listed_strike
        chain_src = str((chain_hint or {}).get("source") or (chain_hint or {}).get("chain_source") or "")
        if chain_src in (
            "live_chain",
            "degraded_selector",
            "degraded_opening_selector",  # legacy alias
            "live_retry",
            "cache",
            "single_tp_pick",
        ):
            return listed_strike
        snapped = self._snap_strike_to_exchange_grid(symbol, spot, side, listed_strike)
        if abs(snapped - listed_strike) > 1e-6:
            log.warning(
                "TRENDLINE_STRIKE_ALIGNED | symbol=%s | raw=%.4f | snapped=%.4f",
                symbol,
                listed_strike,
                snapped,
            )
        return snapped

    @staticmethod
    def _chain_hint_premium(hint: Dict[str, Any]) -> float:
        try:
            prem = float(hint.get("premium")) if hint.get("premium") is not None else None
        except (TypeError, ValueError):
            prem = None
        if prem is not None and prem > 0:
            return prem
        try:
            bid = float(hint.get("bid") or 0.0)
            ask = float(hint.get("ask") or 0.0)
            mid = float(hint.get("mid_price") or 0.0)
        except (TypeError, ValueError):
            bid = ask = mid = 0.0
        if ask > 0:
            return ask
        if mid > 0:
            return mid
        return max(0.01, bid)

    @staticmethod
    def _chain_hint_contract_cost(hint: Dict[str, Any]) -> float:
        return TrendlineOptionsExecutor._chain_hint_premium(hint) * 100.0

    @staticmethod
    def _chain_hint_within_budget(
        hint: Dict[str, Any],
        max_contract_cost: Optional[float],
    ) -> bool:
        if max_contract_cost is None or float(max_contract_cost) <= 0.0:
            return True
        return (
            TrendlineOptionsExecutor._chain_hint_contract_cost(hint)
            <= float(max_contract_cost) * 1.02
        )

    async def _resolve_chain_hint(
        self,
        signal: TrendlineTradeSignal,
        spot: float,
        chain_hint: Optional[Dict[str, Any]],
        *,
        max_contract_cost: Optional[float] = None,
    ) -> Tuple[Optional[Dict[str, Any]], str]:
        self._reset_chain_resolve_diagnostics()
        md = signal.metadata if isinstance(getattr(signal, "metadata", None), dict) else {}
        pc_oi_warmed = bool(md.get("pc_oi_chain_warmed"))
        if chain_hint is None:
            try:
                from easy0DTE.modules.single_tp_admit import chain_hint_from_pick, execution_pick

                picked = execution_pick(signal)
                if picked is not None:
                    chain_hint = chain_hint_from_pick(picked)
                    try:
                        from easy0DTE.modules.convex_chain_skew import peek_cached_options_chain
                        from easy0DTE.modules.single_tp_admit import align_pick_to_chain

                        chain_mgr = getattr(self, "options_chain_manager", None)
                        if chain_mgr is None:
                            ts = getattr(self, "trading_system", None)
                            chain_mgr = getattr(ts, "options_chain_manager", None)
                        cached = peek_cached_options_chain(chain_mgr, signal.symbol, spot)
                        if isinstance(cached, dict):
                            aligned, _leg = align_pick_to_chain(
                                cached, str(signal.option_side or ""), picked
                            )
                            chain_hint = aligned
                    except Exception:
                        log.debug("single_tp cache align skipped", exc_info=True)
                    log.info(
                        "%s_CHAIN | source=single_tp_pick | symbol=%s | strike=%s | bid=%s | ask=%s | align=%s",
                        self._log_prefix(),
                        signal.symbol,
                        chain_hint.get("strike"),
                        chain_hint.get("bid"),
                        chain_hint.get("ask"),
                        chain_hint.get("align") or "pick",
                    )
            except Exception:
                log.debug("single_tp pick handoff skipped", exc_info=True)
        if chain_hint is not None:
            if self._chain_hint_within_budget(chain_hint, max_contract_cost):
                if str(chain_hint.get("selected_by") or "") == "single_tp":
                    self._chain_resolve_last_source = "single_tp_pick"
                    return chain_hint, "single_tp_pick"
                self._chain_resolve_last_source = "preloaded"
                return chain_hint, "preloaded"
            log.warning(
                "TRENDLINE_CHAIN_PRELOADED_OVER_BUDGET | symbol=%s | cost=%.2f | cap=%.2f",
                signal.symbol,
                self._chain_hint_contract_cost(chain_hint),
                float(max_contract_cost or 0.0),
            )
            self._chain_resolve_no_affordable = True
            self._chain_resolve_reject_reason = "no_affordable_contract_for_slot"
            self._chain_resolve_last_source = "preloaded_over_budget"
            return None, "preloaded_over_budget"
        chain_source = "live_retry"
        max_attempts = 2 if pc_oi_warmed else 5
        for attempt in range(max_attempts):
            relaxed = attempt >= 1 if pc_oi_warmed else attempt >= 2
            chain_hint = await self._fetch_live_chain_hint(
                signal,
                spot,
                relaxed=relaxed,
                max_contract_cost=max_contract_cost,
                prefer_manager_cache=pc_oi_warmed,
            )
            if chain_hint:
                self.options_chain_cache[signal.symbol] = dict(chain_hint)
                break
            if attempt + 1 >= max_attempts:
                break
            wait_sec = 0.25 if pc_oi_warmed else 0.5 * (attempt + 1)
            log.warning(
                "TRENDLINE_CHAIN_RETRY | symbol=%s | attempt=%d | relaxed=%s | wait_sec=%.2f | pc_oi_warmed=%s",
                signal.symbol,
                attempt + 1,
                str(relaxed).lower(),
                wait_sec,
                str(pc_oi_warmed).lower(),
            )
            await asyncio.sleep(wait_sec)
        if chain_hint is None:
            chain_hint = await self._fetch_degraded_chain_hint(
                signal,
                spot,
                max_contract_cost=max_contract_cost,
            )
            if chain_hint:
                chain_source = "degraded_selector"
                self._chain_resolve_last_source = chain_source
                self.options_chain_cache[signal.symbol] = dict(chain_hint)
        if chain_hint is None:
            cached = self.options_chain_cache.get(signal.symbol)
            if cached and self._chain_hint_within_budget(cached, max_contract_cost):
                chain_hint = dict(cached)
                chain_source = "cache"
                self._chain_resolve_last_source = chain_source
                log.warning(
                    "TRENDLINE_CHAIN_CACHE_USED | symbol=%s | demo_mode=%s",
                    signal.symbol,
                    str(bool(self.demo_mode)).lower(),
                )
            elif cached:
                log.warning(
                    "TRENDLINE_CHAIN_CACHE_OVER_BUDGET | symbol=%s | cost=%.2f | cap=%.2f",
                    signal.symbol,
                    self._chain_hint_contract_cost(cached),
                    float(max_contract_cost or 0.0),
                )
                self._chain_resolve_no_affordable = True
                self._chain_resolve_reject_reason = "no_affordable_contract_for_slot"
                self._chain_resolve_last_source = "cache_over_budget"
        if chain_hint is None and not self._chain_resolve_reject_reason:
            if str(self._chain_resolve_last_source or "") in ("", "unavailable"):
                self._chain_resolve_reject_reason = "live_chain_required_unavailable"
            else:
                self._chain_resolve_reject_reason = "no_contract_in_band"
        if chain_hint is None and self._chain_resolve_reject_reason:
            self._log_chain_filter_summary(
                symbol=signal.symbol,
                reason=str(self._chain_resolve_reject_reason),
                pc_oi_warmed=pc_oi_warmed,
            )
        return chain_hint, chain_source

    async def _fetch_live_chain_hint(
        self,
        signal: TrendlineTradeSignal,
        underlying_spot: float,
        *,
        relaxed: bool = False,
        max_contract_cost: Optional[float] = None,
        prefer_manager_cache: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Select a concrete contract from live chain for Trendline execution."""
        side = str(signal.option_side or "").lower()
        if side not in ("call", "put"):
            self._chain_resolve_reject_reason = "no_contract_in_band"
            return None
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

        cfg = self.option_config
        from easy0DTE.modules.option_entry_liquidity_validator import (
            entry_max_premium,
            entry_min_open_interest,
            entry_min_premium,
            entry_min_volume,
        )
        from easy0DTE.modules.unified_contract_select import (
            build_ranked_row,
            contract_ask,
            near_atm_sort_key,
            pick_with_premium_delta_walk,
        )
        from easyBreakout.breakout_contract_gates import compute_otm_pct, evaluate_otm_gate

        hint_min_vol = entry_min_volume()
        hint_min_oi = entry_min_open_interest()
        min_prem = entry_min_premium()
        max_prem = entry_max_premium()
        max_delta_with_tolerance = cfg.delta_max + self.delta_tolerance
        from modules.spread_policy import cheap_allowance_applies as _sp_cheap
        from modules.spread_policy import path_spread_cap_points as _sp_cap

        max_spread_pct = _sp_cap(getattr(self, "path_strategy_type", "trendline_0dte")) / 100.0
        try:
            from easy0DTE.modules.dte0_entry_selection import abs_contract_delta
        except ImportError:
            abs_contract_delta = lambda c: abs(float(getattr(c, "delta", 0.0) or 0.0))  # type: ignore[misc, assignment]
        strike_step = self._option_strike_step(signal.symbol, underlying_spot)
        prefer_max_otm = float(os.getenv("TRENDLINE_PREFER_MAX_OTM_PCT", os.getenv("0DTE_ENTRY_OTM_PCT_PREFER", os.getenv("BREAKOUT_PREFER_MAX_OTM_PCT", "0.055"))))
        research_max_otm = float(os.getenv("TRENDLINE_RESEARCH_MAX_OTM_PCT", os.getenv("0DTE_ENTRY_OTM_PCT_MAX", os.getenv("BREAKOUT_RESEARCH_MAX_OTM_PCT", "0.055"))))

        total = 0
        after_bid = 0
        after_oi = 0
        after_vol = 0
        after_spread = 0
        after_delta = 0
        after_premium = 0
        deep_skipped = 0
        ranked: list = []
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
            if not relaxed:
                if hint_min_oi > 0 and oi is not None and int(oi) < hint_min_oi:
                    continue
            after_oi += 1
            if not relaxed:
                if hint_min_vol > 0 and vol is not None and int(vol) < hint_min_vol:
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
            # Keep δ ≤ max(+tol); allow below min so walk-closer can promote toward ATM.
            if delta > max_delta_with_tolerance + 1e-6:
                continue
            after_delta += 1
            otm = compute_otm_pct(underlying_spot, strike, side)
            otm_dec = evaluate_otm_gate(
                otm,
                enabled=True,
                prefer_max=prefer_max_otm,
                research_max=research_max_otm,
            )
            if not otm_dec.ok:
                deep_skipped += 1
                continue
            ask_for_cap = contract_ask(c)
            if max_prem > 0 and ask_for_cap > max_prem + 1e-12:
                continue
            after_premium += 1
            row = build_ranked_row(
                c,
                underlying_spot=underlying_spot,
                option_side=side,
                prefer_max_otm=prefer_max_otm,
                abs_delta_fn=abs_contract_delta,
                otm_fn=compute_otm_pct,
            )
            if row is not None:
                ranked.append(row)

        self._chain_resolve_filter_stats = {
            "total_contracts": total,
            "after_bid": after_bid,
            "after_delta": after_delta,
            "after_premium": after_premium,
            "after_spread": after_spread,
            "after_volume": after_vol,
            "after_open_interest": after_oi,
            "otm_deep_skipped": deep_skipped,
            "chain_source": self._chain_resolve_last_source,
        }

        if not ranked:
            if total <= 0:
                reason = "no_contract_in_band"
            elif after_bid <= 0:
                reason = "no_bid"
            elif after_oi <= 0:
                reason = "open_interest_below_min"
            elif after_vol <= 0:
                reason = "volume_below_min"
            elif after_spread <= 0:
                reason = "spread_gt_20pct" if float(max_spread_pct) >= 0.20 - 1e-9 else "spread_too_wide"
            elif deep_skipped <= 0 and after_delta > 0 and after_premium <= 0:
                reason = "option_premium_too_high"
            elif deep_skipped > 0 and after_premium <= 0:
                reason = "contract_otm_too_deep"
            elif after_delta <= 0:
                reason = "delta_out_of_band"
            else:
                reason = "no_contract_in_band"
            self._chain_resolve_reject_reason = reason
            return None

        if max_contract_cost is not None and float(max_contract_cost) > 0.0:
            cap = float(max_contract_cost) * 1.02
            affordable = [
                row for row in ranked if (contract_ask(row[-1]) * 100.0) <= cap
            ]
            if affordable:
                ranked = affordable
            else:
                min_cost = min(contract_ask(row[-1]) * 100.0 for row in ranked)
                log.warning(
                    "%s_CHAIN_NO_AFFORDABLE | symbol=%s | min_in_band_cost=%.2f | slot_cap=%.2f | n_in_band=%d",
                    self._log_prefix(),
                    signal.symbol,
                    min_cost,
                    float(max_contract_cost),
                    len(ranked),
                )
                self._chain_resolve_no_affordable = True
                self._chain_resolve_reject_reason = "no_affordable_contract_for_slot"
                return None

        ranked.sort(key=near_atm_sort_key)
        best, walk_reject, walk_diag = pick_with_premium_delta_walk(
            ranked,
            delta_min=float(cfg.delta_min),
            delta_max=float(max_delta_with_tolerance),
            min_premium=float(min_prem),
            abs_delta_fn=abs_contract_delta,
            symbol=str(signal.symbol),
            path="trendline",
        )
        if best is None:
            self._chain_resolve_reject_reason = walk_reject or "no_contract_in_band"
            log.warning(
                "%s_UNIFIED_SELECT_REJECT | symbol=%s | reason=%s | diag=%s",
                self._log_prefix(),
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
        otm_steps = max(1, int(round(abs(strike - underlying_spot) / strike_step)))
        self._chain_resolve_reject_reason = None
        exp_ymd = _normalize_expiry_ymd(
            getattr(best, "expiry", None)
            or getattr(best, "expiration", None)
            or (chain.get("selected_expiry") if isinstance(chain, dict) else None)
            or (chain.get("expiry") if isinstance(chain, dict) else None)
        )
        calendar_today = _expiry_ymd_trendline()
        if exp_ymd and exp_ymd != calendar_today:
            log.info(
                "TRENDLINE_CHAIN_EXPIRY_SELECTED | symbol=%s | calendar_today=%s | listed=%s | strike=%.2f",
                signal.symbol,
                calendar_today,
                exp_ymd,
                strike,
            )
        hint_out: Dict[str, Any] = {
            "strike": strike,
            "delta": delta,
            "premium": premium,
            "bid": bid,
            "ask": ask,
            "openInterest": int(getattr(best, "open_interest", 0) or 0),
            "volume": int(getattr(best, "volume", 0) or 0),
            "otm_steps": otm_steps,
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
        try:
            from modules.calendar_expiry_metadata import (
                copy_expiry_fields,
                expiry_fields_from_chain,
            )

            copy_expiry_fields(expiry_fields_from_chain(chain if isinstance(chain, dict) else None), hint_out)
            if not hint_out.get("selected_expiry_ymd") and (exp_ymd or calendar_today):
                merge_src = expiry_fields_from_chain(
                    {
                        **(chain if isinstance(chain, dict) else {}),
                        "selected_expiry": exp_ymd or calendar_today,
                    }
                )
                copy_expiry_fields(merge_src, hint_out)
        except Exception:
            pass
        return hint_out

    @staticmethod
    def _trendline_degraded_selector_allowed(signal: TrendlineTradeSignal) -> bool:
        md = signal.metadata if isinstance(signal.metadata, dict) else {}
        if bool(md.get("reclaim_detected")):
            return False
        if not bool(md.get("distance_increasing")):
            return False
        bq = float(md.get("break_quality_score") or md.get("break_quality") or 0.0)
        if bq > 0 and bq < 0.45:
            return False
        return bool(md.get("expansion_quality_pass") or md.get("follow_through") or bq >= 0.55)

    async def _fetch_degraded_chain_hint(
        self,
        signal: TrendlineTradeSignal,
        underlying_spot: float,
        *,
        max_contract_cost: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        """ORB opening-preservation selector for strong-structure Trendline breaks."""
        try:
            from modules.orb_0dte_opening_execution import (
                classify_liquidity_class,
                opening_selector_params,
                select_nearest_liquid_opening_contract,
            )
        except ImportError:
            return None
        side = str(signal.option_side or "").lower()
        if side not in ("call", "put"):
            return None
        expiry = _expiry_ymd_trendline()
        md = signal.metadata if isinstance(getattr(signal, "metadata", None), dict) else {}
        chain, _chain_source = await self._fetch_options_chain_dict(
            symbol=signal.symbol,
            expiry=expiry,
            underlying_spot=float(underlying_spot),
            strike_count=24,
            prefer_manager_cache=bool(md.get("pc_oi_chain_warmed")),
        )
        self._chain_resolve_last_source = str(_chain_source or self._chain_resolve_last_source or "unavailable")
        if not isinstance(chain, dict):
            if not self._chain_resolve_reject_reason:
                self._chain_resolve_reject_reason = "live_chain_required_unavailable"
            return None
        contracts = list(chain.get("calls" if side == "call" else "puts", []) or [])
        liq = classify_liquidity_class(signal.symbol)
        params = opening_selector_params(
            liquidity_class=liq,
            underlying_price=float(underlying_spot),
            convex_score=0.96,
            mode="convex_preservation",
        )
        selected, audit = select_nearest_liquid_opening_contract(
            contracts,
            option_type=side,
            current_price=float(underlying_spot),
            params=params,
            convex_score=0.96,
            liquidity_class=liq,
        )
        if not selected:
            reason = str(audit.get("reject_reason") or "no_contract_in_band")
            log.warning(
                "%s_DEGRADED_SELECTOR_REJECTED | symbol=%s | reason=%s",
                self._log_prefix(),
                signal.symbol,
                reason,
            )
            self._chain_resolve_reject_reason = reason
            return None
        log.warning(
            "%s_DEGRADED_SELECTOR_ACCEPTED | symbol=%s | strike=%.2f | delta=%.3f | spread_pct=%.2f",
            self._log_prefix(),
            signal.symbol,
            float(selected.strike),
            float(selected.delta or 0.0),
            float(selected.bid_ask_spread_pct),
        )
        hint = {
            "strike": float(selected.strike),
            "delta": abs(float(selected.delta or 0.0)),
            "premium": float(selected.ask or selected.mid_price or 0.0),
            "bid": float(selected.bid),
            "ask": float(selected.ask),
            "openInterest": int(selected.open_interest or 0),
            "volume": int(selected.volume or 0),
            "source": "degraded_selector",
        }
        exp_ymd = _normalize_expiry_ymd(
            getattr(selected, "expiry", None)
            or getattr(selected, "expiration", None)
            or (chain.get("selected_expiry") if isinstance(chain, dict) else None)
            or (chain.get("expiry") if isinstance(chain, dict) else None)
        )
        if exp_ymd:
            hint["expiry_ymd"] = exp_ymd
            hint["expiry"] = exp_ymd
            hint["selected_expiry"] = exp_ymd
        try:
            from modules.calendar_expiry_metadata import (
                copy_expiry_fields,
                expiry_fields_from_chain,
            )

            copy_expiry_fields(
                expiry_fields_from_chain(
                    {
                        **(chain if isinstance(chain, dict) else {}),
                        "selected_expiry": exp_ymd or chain.get("selected_expiry"),
                    }
                    if isinstance(chain, dict)
                    else {"selected_expiry": exp_ymd}
                ),
                hint,
            )
        except Exception:
            pass
        if not self._chain_hint_within_budget(hint, max_contract_cost):
            log.warning(
                "%s_DEGRADED_SELECTOR_OVER_BUDGET | symbol=%s | cost=%.2f | cap=%.2f",
                self._log_prefix(),
                signal.symbol,
                self._chain_hint_contract_cost(hint),
                float(max_contract_cost or 0.0),
            )
            self._chain_resolve_no_affordable = True
            self._chain_resolve_reject_reason = "no_affordable_contract_for_slot"
            return None
        self._chain_resolve_reject_reason = None
        return hint

    def _trendline_dict(self, signal: TrendlineTradeSignal) -> Dict[str, Any]:
        tl = signal.trendline
        return {
            "slope_per_second": tl.slope_per_second,
            "intercept": tl.intercept,
            "direction": tl.direction.value,
            "symbol": tl.symbol,
        }

    def build_execution_payload(
        self,
        signal: TrendlineTradeSignal,
        slot_index: int = 0,
        slot_capital: Optional[float] = None,
        trendline_daily_trade_number: int = 0,
        contract: Optional[_ContractPick] = None,
    ) -> Dict[str, object]:
        # Orchestrator passes entry_budget = min(1/N tradable pool, cash outside buffer).
        if slot_capital is not None:
            budget = max(0.0, float(slot_capital))
        else:
            from modules.unified_0dte_account import unified_trendline_slot_count, unified_trading_capital_pct

            slot_count = max(1, unified_trendline_slot_count())
            alloc_pct = max(0.0, min(100.0, unified_trading_capital_pct()))
            tradable = max(0.0, self._unified_cash_balance() * (alloc_pct / 100.0))
            budget = tradable / float(slot_count)
            log.warning(
                "TRENDLINE_PIPELINE | stage=slot_sizing_fallback | symbol=%s | slot_count=%d | budget=%.2f",
                signal.symbol,
                slot_count,
                budget,
            )
        size_mult = 1.0
        try:
            md = signal.metadata if isinstance(signal.metadata, dict) else {}
            size_mult = float(md.get("entry_size_multiplier") or 1.0)
        except (TypeError, ValueError):
            size_mult = 1.0
        size_mult = max(0.10, min(1.00, size_mult))
        budget *= size_mult
        unit_price = float(contract.premium) if contract else self._fallback_unit_price(signal)
        try:
            from modules.option_executable_pricing import long_option_buy_to_open_price

            if contract and float(getattr(contract, "premium", 0) or 0) > 0:
                unit_price = float(contract.premium)
        except ImportError:
            pass
        from easyTrendline.trendline_capital_allocation import slot_affordable_quantity

        qty = int(
            slot_affordable_quantity(
                budget=float(budget or 0.0),
                unit_cost=float(max(unit_price * 100.0, 0.0)),
            )
        )
        if qty < 1:
            log.warning(
                "TRENDLINE_PIPELINE | stage=slot_qty_zero | symbol=%s | budget=%.2f | unit_cost=%.2f | "
                "reason=contract_too_expensive_for_slot",
                signal.symbol,
                float(budget or 0.0),
                float(unit_price * 100.0),
            )
        total_cost = qty * unit_price * 100.0
        try:
            from modules.options_trade_economics import compute_opening_fees

            total_cost += compute_opening_fees(quantity=qty, position_type="lotto")
        except ImportError:
            pass

        meta = dict(signal.metadata or {})
        intent_raw = meta.pop("execution_intent", None)
        if intent_raw is not None:
            try:
                from modules.execution_intent import execution_intent_to_dict

                intent_dict = execution_intent_to_dict(intent_raw)
                if intent_dict is not None:
                    meta["execution_intent"] = intent_dict
            except Exception:
                pass
        exp_ymd = ""
        if contract is not None:
            exp_ymd = _normalize_expiry_ymd(getattr(contract, "expiry_ymd", None))
        if not exp_ymd:
            exp_ymd = _expiry_ymd_trendline()
        side = str(signal.option_side or "").lower()
        path_st = str(getattr(self, "path_strategy_type", None) or "trendline_0dte")
        payload: Dict[str, object] = {
            "symbol": signal.symbol,
            "underlying_symbol": signal.symbol,
            "direction": signal.direction.value,
            "option_side": signal.option_side,
            "strategy": path_st,
            "strategy_name": str(getattr(self, "path_strategy_name", None) or "easyTrendline_0DTE"),
            "strategy_type": path_st,
            "entry_type": str(getattr(self, "path_entry_type", None) or "trendline_break"),
            "source_path": path_st,
            "trigger_type": str(getattr(self, "path_trigger_type", None) or "trendline_break_momentum"),
            "signal_time": signal.emitted_at.isoformat(),
            "estimated_contract_price": unit_price,
            "quantity": qty,
            "estimated_total_cost": total_cost,
            "slot_index": slot_index,
            "slot_capital": budget,
            "trendline_daily_trade_number": trendline_daily_trade_number,
            "setup_type": meta.get("setup_type"),
            "trigger_direction": meta.get("trigger_direction"),
            "expected_option_side": signal.option_side,
            "trendline_structure_source": meta.get("trendline_structure_source", "pre_730_price_action"),
            "trendline_for_exit": self._trendline_dict(signal),
            "expiry_ymd": exp_ymd,
            "expiration_ymd": exp_ymd,
            "meta": meta,
            # Per-contract tradable value (ORB-aligned semantics); multiplies by qty × 100 for dollars.
            "entry_value": float(unit_price),
            "entry_debit": float(unit_price),
            "entry_credit": None,
            "current_value": float(unit_price),
            "position_type": "single_leg_long_put" if side == "put" else "single_leg_long_call",
            "trade_id": "",
        }
        payload["position_type"] = payload.get(
            "position_type",
            "single_leg_long_put" if side == "put" else "single_leg_long_call",
        )
        if contract:
            payload["strike"] = contract.strike
            payload["delta_at_entry"] = contract.delta_est
            payload["otm_steps"] = contract.otm_steps
            payload["position_type"] = "single_leg_long_put" if side == "put" else "single_leg_long_call"
            payload["entry_value"] = float(contract.premium)
            payload["entry_debit"] = float(contract.premium)
            payload["current_value"] = float(contract.premium)
            payload["legs"] = [
                {
                    "leg_id": f"{signal.symbol}_{exp_ymd}_{contract.strike}_{side}",
                    "symbol": signal.symbol,
                    "long_or_short": "long",
                    "option_side": side,
                    "strike": float(contract.strike),
                    "quantity": int(qty),
                    "entry_price": float(contract.premium),
                    "delta_at_entry": float(contract.delta_est),
                }
            ]
        try:
            from modules.calendar_expiry_metadata import merge_expiry_trade_fields

            sel = getattr(contract, "expiry_selection", None) if contract is not None else None
            merge_expiry_trade_fields(
                payload,  # type: ignore[arg-type]
                selected_expiry=exp_ymd,
                requested_expiry=_expiry_ymd_trendline(),
                expiry_selection_mode=(sel or {}).get("expiry_selection_mode") if isinstance(sel, dict) else None,
                fallback_reason=(sel or {}).get("fallback_reason") if isinstance(sel, dict) else None,
                fallback_days=(sel or {}).get("fallback_days") if isinstance(sel, dict) else None,
                prefer_existing=bool(isinstance(sel, dict) and sel),
            )
            if isinstance(sel, dict):
                for _k in (
                    "expiry_selection_mode",
                    "fallback_reason",
                    "fallback_days",
                    "requested_expiry_ymd",
                    "selected_expiry_ymd",
                    "session_date",
                ):
                    if sel.get(_k) is not None and sel.get(_k) != "":
                        payload[_k] = sel.get(_k)
                # Recompute calendar truth after copying selection mode.
                merge_expiry_trade_fields(
                    payload,  # type: ignore[arg-type]
                    selected_expiry=payload.get("selected_expiry_ymd") or exp_ymd,
                    requested_expiry=payload.get("requested_expiry_ymd") or _expiry_ymd_trendline(),
                    session_date=payload.get("session_date"),
                    expiry_selection_mode=str(payload.get("expiry_selection_mode") or ""),
                    fallback_reason=str(payload.get("fallback_reason") or ""),
                    fallback_days=payload.get("fallback_days"),
                    prefer_existing=False,
                )
        except Exception:
            pass
        return payload

    @staticmethod
    def _fallback_unit_price(signal: TrendlineTradeSignal) -> float:
        base = 0.35
        confidence_boost = min(0.35, max(0.0, signal.confidence - 0.5))
        return round(base + confidence_boost, 2)

    async def execute(
        self,
        signal: TrendlineTradeSignal,
        *,
        slot_index: int = 0,
        slot_capital: Optional[float] = None,
        entry_budget: Optional[float] = None,
        buffer_reserve: float = 0.0,
        available_cash: Optional[float] = None,
        trendline_daily_trade_number: int = 0,
        chain_hint: Optional[Dict[str, Any]] = None,
    ) -> TrendlineTradeResult:
        """Execute signal through demo ledger now; live hook reserved for next pass."""
        now = datetime.now(timezone.utc)
        cfg_tl = self.trendline_signal_config
        if cfg_tl is not None:
            aligned = self._break_geometry_aligns_signal(signal)
            if not aligned:
                meta = signal.metadata or {}
                bd = float(meta.get("break_distance") or 0.0)
                br = float(meta.get("body_ratio") or 0.0)
                exec_reason = str(meta.get("execution_reason") or "").strip().lower()
                catastrophic_reversal = bool(
                    str(meta.get("catastrophic_reversal") or "").strip().lower() == "true"
                )
                impossible_geometry = abs(float(getattr(signal.break_event, "trendline_price", 0.0) or 0.0)) <= 0.0
                if exec_reason == "executed_valid_confirmation" and not catastrophic_reversal and not impossible_geometry:
                    log.warning(
                        "TRENDLINE_EXECUTOR_DIRECTION_ADVISORY | symbol=%s | trade_id=%s | break_distance=%.6f | "
                        "body_ratio=%.4f | execution_reason=%s | action=continue",
                        signal.symbol,
                        str(meta.get("candidate_id") or ""),
                        bd,
                        br,
                        exec_reason,
                    )
                elif bd >= float(cfg_tl.direction_override_strong_break_threshold) and br >= float(
                    cfg_tl.direction_override_strong_body_ratio
                ):
                    log.warning(
                        "TRENDLINE_DIRECTION_OVERRIDE | symbol=%s | trade_id=%s | break_distance=%.6f | body_ratio=%.4f | "
                        "thresholds_break=%.6f | thresholds_body=%.4f",
                        signal.symbol,
                        str(meta.get("candidate_id") or ""),
                        bd,
                        br,
                        float(cfg_tl.direction_override_strong_break_threshold),
                        float(cfg_tl.direction_override_strong_body_ratio),
                    )
                else:
                    log.warning(
                        "TRENDLINE_DIRECTION_REJECT | symbol=%s | trade_id=%s | break_distance=%.6f | body_ratio=%.4f",
                        signal.symbol,
                        str(meta.get("candidate_id") or ""),
                        bd,
                        br,
                    )
                    log.warning(
                        "TRENDLINE_HARD_VETO_AUDIT | symbol=%s | stage=executor_direction | veto_reason=direction_mismatch | "
                        "catastrophic=%s | stale=false | severe_chop=false | invalid_structure=false | liquidity_block=false | executor_called=true",
                        signal.symbol,
                        str(bool(catastrophic_reversal or impossible_geometry)).lower(),
                    )
                    return TrendlineTradeResult(
                        symbol=signal.symbol,
                        direction=signal.direction,
                        success=False,
                        executed_at=now,
                        signal_time=signal.emitted_at,
                        account_mode="demo" if self.demo_mode else "live",
                        order_payload={"reject_reason": "direction_mismatch"},
                        error="direction_mismatch",
                    )
        spot = float(signal.break_event.close_price)
        # Sacred buffer: size only from cash outside reserve; slot_capital is tradable 1/N target.
        sizing_budget = (
            max(0.0, float(entry_budget))
            if entry_budget is not None
            else max(0.0, float(slot_capital or 0.0))
        )
        if sizing_budget <= 0.0:
            log.warning(
                "TRENDLINE_PIPELINE | stage=sizing_rejected | symbol=%s | reason=buffer_reserve_exhausted | "
                "buffer_reserve=%.2f | cash=%.2f",
                signal.symbol,
                float(buffer_reserve or 0.0),
                float(self._unified_cash_balance()),
            )
            return TrendlineTradeResult(
                symbol=signal.symbol,
                direction=signal.direction,
                success=False,
                executed_at=now,
                signal_time=signal.emitted_at,
                account_mode="demo" if self.demo_mode else "live",
                order_payload={"reject_reason": "buffer_reserve_exhausted"},
                error="buffer_reserve_exhausted",
            )
        budget = sizing_budget

        chain_hint, chain_source = await self._resolve_chain_hint(
            signal, spot, chain_hint, max_contract_cost=budget
        )
        if chain_hint is None and self.require_live_chain_data:
            allow_synthetic_entry = os.getenv("TRENDLINE_ALLOW_SYNTHETIC_ENTRY", "false").lower() == "true"
            if self.demo_mode and allow_synthetic_entry:
                chain_source = "demo_synthetic"
                log.warning(
                    "TRENDLINE_CHAIN_FALLBACK | symbol=%s | using synthetic contract | allow_synthetic_entry=true",
                    signal.symbol,
                )
            else:
                no_affordable = bool(getattr(self, "_chain_resolve_no_affordable", False))
                reject_reason = str(getattr(self, "_chain_resolve_reject_reason", None) or "").strip()
                if no_affordable:
                    reject_reason = "no_affordable_contract_for_slot"
                elif not reject_reason:
                    reject_reason = (
                        "live_chain_required_unavailable"
                        if str(getattr(self, "_chain_resolve_last_source", "") or "") in ("", "unavailable")
                        else "no_contract_in_band"
                    )
                filter_stats = getattr(self, "_chain_resolve_filter_stats", {}) or {}
                log.info(
                    "%s_EXECUTION_BLOCKED | symbol=%s | reason=%s | chain_source=%s",
                    self._log_prefix(),
                    signal.symbol,
                    reject_reason,
                    str(getattr(self, "_chain_resolve_last_source", "") or chain_source or "blocked"),
                )
                log.warning(
                    "%s_PIPELINE | stage=execution_blocked | symbol=%s | reason=%s | "
                    "demo_mode=%s | allow_synthetic_entry=%s | entry_budget=%.2f | "
                    "total_contracts=%s | after_bid=%s | after_delta=%s | after_premium=%s | "
                    "after_spread=%s | after_volume=%s | after_open_interest=%s | chain_source=%s",
                    self._log_prefix(),
                    signal.symbol,
                    reject_reason,
                    str(bool(self.demo_mode)).lower(),
                    str(allow_synthetic_entry).lower(),
                    sizing_budget,
                    filter_stats.get("total_contracts", 0),
                    filter_stats.get("after_bid", 0),
                    filter_stats.get("after_delta", 0),
                    filter_stats.get("after_premium", 0),
                    filter_stats.get("after_spread", 0),
                    filter_stats.get("after_volume", 0),
                    filter_stats.get("after_open_interest", 0),
                    str(getattr(self, "_chain_resolve_last_source", "") or chain_source or "blocked"),
                )
                reject_payload: Dict[str, Any] = {
                    "reject_reason": reject_reason,
                    "chain_source": str(getattr(self, "_chain_resolve_last_source", "") or "blocked"),
                    "entry_budget": sizing_budget,
                    "filter_stats": dict(filter_stats),
                }
                if slot_capital is not None:
                    reject_payload["slot_capital_target"] = float(slot_capital)
                if available_cash is not None:
                    reject_payload["available_cash"] = float(available_cash)
                return TrendlineTradeResult(
                    symbol=signal.symbol,
                    direction=signal.direction,
                    success=False,
                    executed_at=now,
                    signal_time=signal.emitted_at,
                    account_mode="demo" if self.demo_mode else "live",
                    order_payload=reject_payload,
                    error=reject_reason,
                )
        if (
            chain_hint is not None
            and budget > 0.0
            and not self._chain_hint_within_budget(chain_hint, budget)
        ):
            est_cost = self._chain_hint_contract_cost(chain_hint)
            log.warning(
                "TRENDLINE_PIPELINE | stage=contract_rejected | symbol=%s | reason=contract_too_expensive_for_slot | "
                "est=%.2f cap=%.2f | chain_source=%s",
                signal.symbol,
                est_cost,
                budget,
                chain_source,
            )
            reject_payload = {
                "reject_reason": "contract_too_expensive_for_slot",
                "estimated_contract_cost": est_cost,
                "slot_capital_target": float(slot_capital or budget),
                "entry_budget": sizing_budget,
                "premium_per_share": self._chain_hint_premium(chain_hint),
                "chain_source": chain_source,
            }
            if available_cash is not None:
                reject_payload["available_cash"] = float(available_cash)
            return TrendlineTradeResult(
                symbol=signal.symbol,
                direction=signal.direction,
                success=False,
                executed_at=now,
                signal_time=signal.emitted_at,
                account_mode="demo" if self.demo_mode else "live",
                order_payload=reject_payload,
                error="contract_too_expensive_for_slot",
            )
        log.warning(
            "%s_EXECUTION_CONTINUED | symbol=%s | chain_source=%s",
            self._log_prefix(),
            signal.symbol,
            chain_source,
        )
        pick, reject = self.select_contract(signal, spot, budget, chain_hint=chain_hint)
        _tl_trade_id = str((getattr(signal, "metadata", None) or {}).get("trade_id") or "").strip()
        if pick is not None:
            try:
                from modules.trendline_measurement import record_contract_selection

                record_contract_selection(
                    _tl_trade_id,
                    pick=pick,
                    selector_extra={
                        "chain_source": chain_source,
                        "expected_move_pct": (getattr(signal, "metadata", None) or {}).get(
                            "expected_move_pct"
                        ),
                    },
                )
            except Exception:
                pass
        if pick is None:
            try:
                from modules.trendline_measurement import record_contract_selection

                record_contract_selection(
                    _tl_trade_id,
                    pick=None,
                    reject_reason=str(reject or "contract_selection_failed"),
                )
            except Exception:
                pass
            reject_payload: Dict[str, Any] = {"reject_reason": reject}
            diag = getattr(self, "_last_contract_reject_diagnostics", None)
            if isinstance(diag, dict) and diag:
                reject_payload.update(diag)
            if slot_capital is not None:
                reject_payload.setdefault("slot_capital_target", float(slot_capital))
            reject_payload.setdefault("entry_budget", sizing_budget)
            reject_payload.setdefault("chain_source", chain_source)
            if available_cash is not None:
                reject_payload["available_cash"] = float(available_cash)
            log.warning(
                "TRENDLINE_PIPELINE | stage=execution_blocked | symbol=%s | reason=%s | spot=%.4f",
                signal.symbol,
                reject or "contract_selection_failed",
                spot,
            )
            return TrendlineTradeResult(
                symbol=signal.symbol,
                direction=signal.direction,
                success=False,
                executed_at=now,
                signal_time=signal.emitted_at,
                account_mode="demo" if self.demo_mode else "live",
                order_payload=reject_payload,
                error=reject or "contract_selection_failed",
            )

        try:
            from easy0DTE.modules.dte0_entry_selection import validate_trendline_delta_before_execute
        except ImportError:
            import importlib.util
            import os as _os

            _path = _os.path.join(
                _os.path.dirname(_os.path.dirname(__file__)),
                "easy0DTE",
                "modules",
                "dte0_entry_selection.py",
            )
            _spec = importlib.util.spec_from_file_location("dte0_entry_selection_tl", _path)
            _mod = importlib.util.module_from_spec(_spec)
            assert _spec.loader is not None
            _spec.loader.exec_module(_mod)
            validate_trendline_delta_before_execute = _mod.validate_trendline_delta_before_execute

        pick_delta = abs(
            float(getattr(pick, "delta_est", None) or getattr(pick, "delta", 0.0) or 0.0)
        )
        try:
            from easy0DTE.modules.unified_contract_select import missing_or_zero_delta
        except Exception:
            missing_or_zero_delta = lambda d: abs(float(d or 0.0)) < 0.005  # type: ignore
        if missing_or_zero_delta(pick_delta):
            log.warning(
                "TRENDLINE_PIPELINE | stage=execution_blocked | symbol=%s | "
                "reason=delta_out_of_band | delta=%.4f | note=missing_or_zero_delta_transient",
                signal.symbol,
                pick_delta,
            )
            return TrendlineTradeResult(
                symbol=signal.symbol,
                direction=signal.direction,
                success=False,
                executed_at=now,
                signal_time=signal.emitted_at,
                account_mode="demo" if self.demo_mode else "live",
                order_payload={
                    "reject_reason": "delta_out_of_band",
                    "selected_delta": pick_delta,
                    "transient_delta_reject": True,
                },
                error="delta_out_of_band",
            )
        tl_passes, _ = validate_trendline_delta_before_execute(signal.symbol, pick_delta)
        if not tl_passes:
            try:
                from modules.trendline_measurement import record_contract_selection

                record_contract_selection(
                    _tl_trade_id,
                    pick=pick,
                    reject_reason="delta_out_of_band",
                    selector_extra={"selected_delta": pick_delta},
                )
            except Exception:
                pass
            log.warning(
                "TRENDLINE_PIPELINE | stage=execution_blocked | symbol=%s | reason=delta_out_of_band | delta=%.4f",
                signal.symbol,
                pick_delta,
            )
            return TrendlineTradeResult(
                symbol=signal.symbol,
                direction=signal.direction,
                success=False,
                executed_at=now,
                signal_time=signal.emitted_at,
                account_mode="demo" if self.demo_mode else "live",
                order_payload={"reject_reason": "delta_out_of_band", "selected_delta": pick_delta},
                error="delta_out_of_band",
            )

        final_val = self._validate_final_entry_execution(
            pick=pick,
            signal=signal,
            sizing_budget=sizing_budget,
            chain_source=chain_source,
        )
        if not final_val.get("passed"):
            from easy0DTE.modules.option_entry_liquidity_validator import (
                validator_diagnostics_payload,
            )

            code = str(final_val.get("reason_code") or "option_liquidity_failed")
            reject_payload = validator_diagnostics_payload(final_val)
            reject_payload.setdefault("chain_source", chain_source)
            if slot_capital is not None:
                reject_payload.setdefault("slot_capital_target", float(slot_capital))
            reject_payload.setdefault("entry_budget", sizing_budget)
            if available_cash is not None:
                reject_payload["available_cash"] = float(available_cash)
            log.warning(
                "TRENDLINE_PIPELINE | stage=execution_blocked | symbol=%s | reason=%s | spot=%.4f",
                signal.symbol,
                code,
                spot,
            )
            return TrendlineTradeResult(
                symbol=signal.symbol,
                direction=signal.direction,
                success=False,
                executed_at=now,
                signal_time=signal.emitted_at,
                account_mode="demo" if self.demo_mode else "live",
                order_payload=reject_payload,
                error=code,
            )

        payload = self.build_execution_payload(
            signal,
            slot_index=slot_index,
            slot_capital=sizing_budget,
            trendline_daily_trade_number=trendline_daily_trade_number,
            contract=pick,
        )
        if int(payload.get("quantity") or 0) < 1:
            log.warning(
                "TRENDLINE_PIPELINE | stage=contract_rejected | symbol=%s | reason=contract_too_expensive_for_slot | "
                "est=%.2f cap=%.2f | chain_source=%s",
                signal.symbol,
                float(payload.get("estimated_total_cost") or 0.0)
                or float(payload.get("estimated_contract_price") or 0.0) * 100.0,
                float(sizing_budget or 0.0),
                chain_source,
            )
            return TrendlineTradeResult(
                symbol=signal.symbol,
                direction=signal.direction,
                success=False,
                executed_at=now,
                signal_time=signal.emitted_at,
                account_mode="demo" if self.demo_mode else "live",
                order_payload={
                    "reject_reason": "contract_too_expensive_for_slot",
                    "estimated_contract_cost": float(payload.get("estimated_contract_price") or 0.0) * 100.0,
                    "slot_capital_target": float(slot_capital or sizing_budget or 0.0),
                    "entry_budget": float(sizing_budget or 0.0),
                    "chain_source": chain_source,
                },
                error="contract_too_expensive_for_slot",
            )
        if slot_capital is not None:
            payload["slot_capital_target"] = float(slot_capital)
        payload["buffer_reserve"] = float(buffer_reserve or 0.0)
        payload["entry_budget"] = sizing_budget
        payload["entry_underlying_price"] = spot
        payload["chain_source"] = chain_source

        return await self._execute_unified_lotto_open(
            signal=signal,
            pick=pick,
            chain_hint=chain_hint,
            payload=payload,
            spot=spot,
            sizing_budget=sizing_budget,
            slot_capital=slot_capital,
            buffer_reserve=buffer_reserve,
            trendline_daily_trade_number=trendline_daily_trade_number,
            chain_source=chain_source,
            now=now,
        )

    async def _execute_unified_lotto_open(
        self,
        *,
        signal: TrendlineTradeSignal,
        pick: _ContractPick,
        chain_hint: Optional[Dict[str, Any]],
        payload: Dict[str, Any],
        spot: float,
        sizing_budget: float,
        slot_capital: Optional[float],
        buffer_reserve: float,
        trendline_daily_trade_number: int,
        chain_source: str = "unknown",
        now: datetime,
    ) -> TrendlineTradeResult:
        """Demo + Live open via shared OptionsTradingExecutor (unified mock/broker ledger)."""
        executor = self.options_trading_executor
        account_mode = "demo" if self.demo_mode else "live"
        if executor is None:
            log.error(
                "TRENDLINE_PIPELINE | stage=execution_blocked | symbol=%s | reason=options_executor_unavailable",
                signal.symbol,
            )
            return TrendlineTradeResult(
                symbol=signal.symbol,
                direction=signal.direction,
                success=False,
                executed_at=now,
                signal_time=signal.emitted_at,
                account_mode=account_mode,
                order_payload=payload,
                error="options_executor_unavailable",
            )

        adapter = getattr(executor, "broker_adapter", None)
        use_broker = getattr(executor, "_use_broker_fills", lambda: not self.demo_mode)()
        bal = None
        cash_avail = 0.0
        if use_broker:
            if adapter is None or not getattr(adapter, "is_available", lambda: False)():
                log.error(
                    "TRENDLINE_PIPELINE | stage=execution_blocked | symbol=%s | reason=broker_adapter_unavailable",
                    signal.symbol,
                )
                return TrendlineTradeResult(
                    symbol=signal.symbol,
                    direction=signal.direction,
                    success=False,
                    executed_at=now,
                    signal_time=signal.emitted_at,
                    account_mode=account_mode,
                    order_payload=payload,
                    error="broker_adapter_unavailable",
                )
            bal = await adapter.get_account_balance()
            if bal is None or float(bal.cash_available) <= 0:
                return TrendlineTradeResult(
                    symbol=signal.symbol,
                    direction=signal.direction,
                    success=False,
                    executed_at=now,
                    signal_time=signal.emitted_at,
                    account_mode=account_mode,
                    order_payload=payload,
                    error="broker_account_unavailable",
                )
        elif getattr(executor, "_use_mock_fills", lambda: self.demo_mode)():
            cash_avail = 0.0
            mock = getattr(executor, "mock_executor", None)
            if mock is not None:
                fn = getattr(mock, "available_balance", None)
                cash_avail = float(fn()) if callable(fn) else float(getattr(mock, "account_balance", 0.0) or 0.0)
                reserve = float(buffer_reserve or 0.0)
                if cash_avail <= reserve:
                    return TrendlineTradeResult(
                        symbol=signal.symbol,
                        direction=signal.direction,
                        success=False,
                        executed_at=now,
                        signal_time=signal.emitted_at,
                        account_mode=account_mode,
                        order_payload=payload,
                        error="insufficient_unified_demo_balance",
                    )

        side = str(signal.option_side or "").lower()
        listed_strike = self._listed_strike_for_execution(
            signal.symbol, spot, side, pick, chain_hint
        )

        from easy0DTE.modules.options_chain_manager import OptionContract

        expiry = str(payload.get("expiry_ymd") or "")
        if len(expiry) == 8:
            expiry_fmt = f"{expiry[:4]}-{expiry[4:6]}-{expiry[6:8]}"
        else:
            expiry_fmt = expiry
        bid_raw = (chain_hint or {}).get("bid")
        if bid_raw is None:
            bid_raw = getattr(pick, "bid", None)
        ask_raw = (chain_hint or {}).get("ask")
        if ask_raw is None:
            ask_raw = getattr(pick, "ask", None)
        if bid_raw is None or ask_raw is None:
            return TrendlineTradeResult(
                symbol=signal.symbol,
                direction=signal.direction,
                success=False,
                executed_at=now,
                signal_time=signal.emitted_at,
                account_mode=account_mode,
                order_payload=payload,
                error="missing_live_bid_ask",
            )
        bid = float(bid_raw)
        ask = float(ask_raw)
        if bid <= 0 or ask <= 0:
            return TrendlineTradeResult(
                symbol=signal.symbol,
                direction=signal.direction,
                success=False,
                executed_at=now,
                signal_time=signal.emitted_at,
                account_mode=account_mode,
                order_payload=payload,
                error="missing_live_bid_ask",
            )
        mid = float((chain_hint or {}).get("mid") or pick.premium)
        quantity = int(payload.get("quantity") or 1)
        try:
            from modules.option_executable_pricing import long_option_buy_to_open_price

            ask_fill = long_option_buy_to_open_price(bid=bid, ask=ask, mid=mid)
        except ImportError:
            ask_fill = ask if ask > 0 else mid
        est_total = ask_fill * quantity * 100.0
        try:
            from modules.options_trade_economics import compute_opening_fees

            est_total += compute_opening_fees(quantity=quantity, position_type="lotto")
        except ImportError:
            pass
        if getattr(executor, "_use_mock_fills", lambda: self.demo_mode)():
            cash_for_sizing = float(cash_avail) - float(buffer_reserve or 0.0)
        elif bal is not None:
            cash_for_sizing = float(bal.cash_available)
        else:
            return TrendlineTradeResult(
                symbol=signal.symbol,
                direction=signal.direction,
                success=False,
                executed_at=now,
                signal_time=signal.emitted_at,
                account_mode=account_mode,
                order_payload=payload,
                error="execution_balance_unavailable",
            )
        if est_total > max(0.0, cash_for_sizing):
            return TrendlineTradeResult(
                symbol=signal.symbol,
                direction=signal.direction,
                success=False,
                executed_at=now,
                signal_time=signal.emitted_at,
                account_mode=account_mode,
                order_payload=payload,
                error="insufficient_unified_balance",
            )

        contract = OptionContract(
            symbol=signal.symbol,
            strike=listed_strike,
            expiry=expiry_fmt,
            option_type=side,
            bid=bid,
            ask=ask,
            last=mid,
            volume=int((chain_hint or {}).get("volume") or 0),
            open_interest=int(
                (chain_hint or {}).get("openInterest")
                or (chain_hint or {}).get("open_interest")
                or 0
            ),
            delta=float(pick.delta_est),
            gamma=float((chain_hint or {}).get("gamma") or 0.01),
            theta=float((chain_hint or {}).get("theta") or -0.02),
            vega=float((chain_hint or {}).get("vega") or 0.05),
            implied_volatility=float((chain_hint or {}).get("implied_volatility") or 0.35),
            osi_key=str((chain_hint or {}).get("osi_key") or ""),
            display_symbol=str((chain_hint or {}).get("display_symbol") or ""),
        )
        # Stamp book sizes from select-time hint when present (Liquidity Guard).
        for _src_key, _dst in (
            (("bid_size", "bidSize", "option_bid_size"), "bid_size"),
            (("ask_size", "askSize", "option_ask_size"), "ask_size"),
        ):
            _raw = None
            for _k in _src_key:
                if (chain_hint or {}).get(_k) is not None:
                    _raw = (chain_hint or {}).get(_k)
                    break
            if _raw is not None:
                try:
                    setattr(contract, _dst, int(float(_raw)))
                except (TypeError, ValueError):
                    pass

        from modules.spread_policy import trendline_entry_max_spread_points

        entry_spread_cap_pts = _tl_path_cap_points(self, trendline_entry_max_spread_points())

        path_st = str(getattr(self, "path_strategy_type", None) or "trendline_0dte")
        path_name = str(getattr(self, "path_strategy_name", None) or "easyTrendline_0DTE")
        # Warm handoff: prefer full select-time chain from OptionsChainManager cache.
        _warm_chain = None
        _warm_age = None
        _mgr = self._resolve_options_chain_manager()
        if _mgr is not None and hasattr(_mgr, "peek_cached_chain"):
            try:
                _max_age = float(
                    os.getenv("LIVE_QUOTE_REFRESH_CACHE_MAX_AGE_SEC", "5") or "5"
                )
            except (TypeError, ValueError):
                _max_age = 5.0
            try:
                _warm_chain, _warm_age = _mgr.peek_cached_chain(
                    signal.symbol,
                    expiry_fmt,
                    max_age_sec=_max_age,
                )
            except Exception:
                _warm_chain, _warm_age = None, None
        position = await executor.execute_lotto_sleeve(
            contract,
            quantity=quantity,
            send_open_alert=False,
            strategy_type=path_st,
            direction=str(signal.direction.value if hasattr(signal.direction, "value") else signal.direction),
            spread_type="single_leg",
            entry_max_spread_pct=entry_spread_cap_pts,
            warm_chain=_warm_chain if isinstance(_warm_chain, dict) else None,
            warm_chain_age_sec=_warm_age,
        )
        if position is None:
            block_reason = (
                str(getattr(executor, "_last_execution_block_reason", "") or "").strip()
                or "unified_open_failed"
            )
            reject_payload = dict(payload)
            reject_payload["reject_reason"] = block_reason
            return TrendlineTradeResult(
                symbol=signal.symbol,
                direction=signal.direction,
                success=False,
                executed_at=now,
                signal_time=signal.emitted_at,
                account_mode=account_mode,
                order_payload=reject_payload,
                error=block_reason,
            )

        # Post Liquidity Guard / book-depth: sleeve may have reduced qty. Sync
        # payload + est_total before alerts / PO / normalized metadata.
        requested_quantity = int(quantity)
        filled_quantity = int(getattr(position, "quantity", 0) or 0)
        fill_prem = float(
            getattr(position, "entry_price", 0.0) or ask_fill or getattr(pick, "premium", 0.0) or 0.0
        )
        quantity, est_total = sync_payload_to_filled_quantity(
            payload=payload,
            requested_quantity=requested_quantity,
            filled_quantity=filled_quantity,
            fill_premium=fill_prem,
        )
        if requested_quantity > 0 and filled_quantity > 0 and filled_quantity != requested_quantity:
            log.warning(
                "%s_PIPELINE | stage=execution | action=qty_synced_after_risk_clamp | "
                "symbol=%s | requested_qty=%d | filled_qty=%d | fill_premium=%.4f | "
                "estimated_total_cost=%.2f",
                str(getattr(self, "path_log_prefix", None) or "TRENDLINE"),
                signal.symbol,
                requested_quantity,
                filled_quantity,
                fill_prem,
                float(est_total),
            )
        try:
            if float(getattr(position, "entry_cost", 0.0) or 0.0) <= 0.0 and float(est_total) > 0.0:
                position.entry_cost = float(est_total)
        except Exception:
            pass

        meta = getattr(position, "metadata", None) or {}
        if use_broker and not meta.get("broker_fill_confirmed"):
            log.error(
                "TRENDLINE_PIPELINE | stage=execution_blocked | symbol=%s | reason=broker_fill_not_confirmed",
                signal.symbol,
            )
            return TrendlineTradeResult(
                symbol=signal.symbol,
                direction=signal.direction,
                success=False,
                executed_at=now,
                signal_time=signal.emitted_at,
                account_mode=account_mode,
                order_payload=payload,
                error="broker_fill_not_confirmed",
            )

        position_id = str(getattr(position, "position_id", "") or "")
        payload["trade_id"] = position_id
        payload["chain_source"] = str(chain_source or payload.get("chain_source") or "unknown")
        payload["broker_order_id"] = meta.get("broker_order_id")
        payload["listed_exchange_strike"] = listed_strike
        payload["raw_strike"] = float(pick.strike)
        payload["strategy_type"] = path_st
        # Liquidity Guard audit for fill Telegram Liq Cap line (ORB1/TL/BO).
        if isinstance(meta, dict) and isinstance(meta.get("execution_risk"), dict):
            payload["execution_risk"] = dict(meta["execution_risk"])
        # Exact selected OCC book sizes for PO option L1 (not representative ATM).
        for _k in ("bid_size", "ask_size"):
            _v = getattr(contract, _k, None)
            if _v is None and isinstance(meta, dict):
                _v = meta.get(_k)
                if _v is None and isinstance(meta.get("execution_risk"), dict):
                    _v = meta["execution_risk"].get(_k)
            if _v is None and isinstance(chain_hint, dict):
                _v = chain_hint.get(_k) or chain_hint.get(
                    "bidSize" if _k == "bid_size" else "askSize"
                )
            if _v is not None and payload.get(_k) is None:
                try:
                    payload[_k] = int(float(_v))
                except (TypeError, ValueError):
                    pass
        # Must stamp strategy_type=path_st (not default trendline_0dte). Breakout
        # reuses this builder; Jul 27 mis-tag caused exit alerts to silent-skip as
        # trendline_owned_path despite source_path=breakout_0dte.
        norm = normalize_execution_result(
            build_trendline_normalized_metadata(
                trade_id=position_id,
                underlying_symbol=signal.symbol,
                expiration_ymd=str(payload.get("expiry_ymd") or ""),
                option_side=str(signal.option_side),
                strike=float(listed_strike),
                quantity=quantity,
                entry_premium_per_contract=float(getattr(position, "entry_price", 0.0) or pick.premium),
                delta_at_entry=float(pick.delta_est),
                setup_type=(signal.metadata or {}).get("setup_type"),
                trigger_direction=(signal.metadata or {}).get("trigger_direction"),
                strategy_name=path_name,
                strategy_type=path_st,
                source_path=path_st,
                osi_key=str(
                    getattr(contract, "osi_key", "")
                    or (chain_hint or {}).get("osi_key")
                    or ""
                ),
                expiry_selection={
                    k: payload.get(k)
                    for k in (
                        "session_date",
                        "expiry_ymd",
                        "calendar_dte",
                        "is_0dte",
                        "expiry_selection_mode",
                        "requested_expiry_ymd",
                        "selected_expiry_ymd",
                        "fallback_days",
                        "fallback_reason",
                        "dte",
                    )
                    if payload.get(k) is not None
                },
            )
        )
        norm_ok, norm_missing = validate_normalized_options_for_stealth(norm)
        if not norm_ok:
            log.warning(
                "OPTIONS_STEALTH_FAILSAFE | stage=trendline_normalized_invalid | trade_id=%s | missing=%s",
                position_id,
                ",".join(norm_missing) if norm_missing else "unknown",
            )
        if not isinstance(meta, dict):
            meta = {}
            position.metadata = meta
        meta["normalized_options"] = norm
        meta["execution_payload"] = dict(payload)
        meta["entry_chain_source"] = str(payload.get("chain_source") or chain_source or "unknown")
        _occ = str(norm.get("occ_symbol") or "").strip()
        if _occ:
            meta["occ_symbol"] = _occ
            meta["option_symbol"] = _occ
            meta["contract_symbol"] = _occ
        _osi = str(norm.get("broker_quote_request_symbol") or norm.get("osi_key") or "").strip()
        if _osi:
            meta["broker_quote_request_symbol"] = _osi
            meta["osi_key"] = _osi
            meta["osi_symbol"] = _osi
            meta["exit_grade_enrollment_eligible"] = True
        else:
            meta["exit_grade_enrollment_eligible"] = False
            meta["exit_grade_enrollment_blocker"] = "missing_frozen_quote_wire"
        try:
            if contract is not None and hasattr(contract, "osi_key"):
                setattr(contract, "osi_key", _osi)
        except Exception:
            pass
        meta["signal_snapshot"] = {
            "breakout_confirmed": True,
            "entry_underlying_price": spot,
            "break_timestamp": signal.break_event.candle_ts.isoformat(),
            "option_side": signal.option_side,
            "direction": signal.direction.value,
            "setup_type": (signal.metadata or {}).get("setup_type"),
            "trigger_direction": (signal.metadata or {}).get("trigger_direction"),
            "trendline_structure_source": (signal.metadata or {}).get(
                "trendline_structure_source", "pre_730_price_action"
            ),
            "trendline_slot": (signal.metadata or {}).get("trendline_slot")
            or ((signal.metadata or {}).get("setup_payload") or {}).get("trendline_slot")
            or "trendline1",
            "envelope_role": (signal.metadata or {}).get("envelope_role")
            or ((signal.metadata or {}).get("setup_payload") or {}).get("envelope_role"),
            "priority_score": (signal.metadata or {}).get("priority_score")
            or (signal.metadata or {}).get("simple_quality_score")
            or getattr(signal, "priority_score", None),
            "simple_quality_score": (signal.metadata or {}).get("simple_quality_score")
            or getattr(signal, "priority_score", None),
            "rsi": (signal.metadata or {}).get("rsi"),
            "volume_ratio": (signal.metadata or {}).get("volume_ratio"),
            "vwap": (signal.metadata or {}).get("vwap"),
            "vwap_position": (signal.metadata or {}).get("vwap_position"),
            "vwap_distance_pct": (signal.metadata or {}).get("vwap_distance_pct"),
            "break_distance_pct": (signal.metadata or {}).get("break_distance_pct"),
            "break_trade_id": (signal.metadata or {}).get("break_trade_id")
            or (signal.metadata or {}).get("trade_id"),
            "strategy_type": path_st,
            "source_path": path_st,
            "strike": listed_strike,
            "delta_at_entry": pick.delta_est,
            "entry_premium_per_contract": float(getattr(position, "entry_price", 0.0) or pick.premium),
            "expiry_ymd": payload.get("expiry_ymd"),
            "entry_value": float(getattr(position, "entry_price", 0.0) or pick.premium),
            "current_value": float(getattr(position, "entry_price", 0.0) or pick.premium),
        }
        if getattr(executor, "_use_mock_fills", lambda: self.demo_mode)():
            meta["simulated_fill_confirmed"] = True
            meta["fill_path"] = "demo_simulated_ledger"
            mock_bal = getattr(executor, "mock_executor", None)
            if mock_bal is not None:
                meta["account_equity_at_entry"] = float(
                    getattr(mock_bal, "account_balance", 0.0) or 0.0
                )
        elif use_broker:
            meta["broker_fill_confirmed"] = True
        mock = getattr(executor, "mock_executor", None)
        if mock is not None and position_id in getattr(mock, "active_positions", {}):
            mp = mock.active_positions[position_id]
            mp.metadata = meta
            mp.strategy_type = path_st
        try:
            position.strategy_type = path_st
        except Exception:
            pass
        log_position_type_normalized(
            position_id, norm["position_type"], norm.get("legacy_position_type", path_st)
        )
        log_metadata_normalized(position_id, norm["position_type"], signal.symbol)
        candidate_id = str((getattr(signal, "metadata", None) or {}).get("candidate_id") or "")
        if position_id:
            self._executed_trade_ids.add(position_id)
        if candidate_id:
            self._executed_candidate_ids.add(candidate_id)
        _pfx = str(getattr(self, "path_log_prefix", None) or "TRENDLINE")
        log.warning(
            "%s_PIPELINE | stage=execution | action=filled | symbol=%s | trade_id=%s | mode=%s | listed_strike=%.4f | strategy_type=%s",
            _pfx,
            signal.symbol,
            position_id,
            account_mode,
            listed_strike,
            path_st,
        )
        fill_status = "broker_filled" if use_broker else "simulated_filled"
        payload["fill_status"] = fill_status
        return TrendlineTradeResult(
            symbol=signal.symbol,
            direction=signal.direction,
            success=True,
            executed_at=now,
            signal_time=signal.emitted_at,
            account_mode=account_mode,
            position_id=position_id,
            order_payload=payload,
            details={
                "status": fill_status,
                "broker_order_id": meta.get("broker_order_id"),
                "listed_exchange_strike": listed_strike,
                "entry_cost": est_total,
                "quantity": quantity,
            },
        )

    async def sync_live_positions_with_broker(self) -> Dict[str, Any]:
        """
        Reconcile Trendline Live registry with broker get_positions().

        Returns summary dict for startup audit / soak validation.
        """
        try:
            from modules.runtime_mode import broker_execution_enabled
        except ImportError:
            broker_execution_enabled = lambda: not self.demo_mode  # type: ignore
        if not broker_execution_enabled():
            return {"skipped": True, "reason": "broker_execution_disabled"}
        executor = self.options_trading_executor
        if executor is None:
            return {"ok": False, "error": "executor_unavailable"}
        adapter = getattr(executor, "broker_adapter", None)
        if adapter is None or not getattr(adapter, "is_available", lambda: False)():
            return {"ok": False, "error": "broker_adapter_unavailable"}
        broker_positions = await adapter.get_positions()
        local_ids = set(getattr(executor, "positions", {}).keys())
        broker_symbols = {
            str(p.get("symbol") or p.get("Symbol") or "") for p in (broker_positions or [])
        }
        orphan_local = [
            pid for pid in local_ids
            if not any(str(getattr(executor.positions.get(pid), "symbol", "")) in broker_symbols for _ in [0])
        ]
        log.info(
            "TRENDLINE_BROKER_SYNC | local=%d | broker=%d | orphan_local=%d",
            len(local_ids),
            len(broker_positions or []),
            len(orphan_local),
        )
        return {
            "ok": True,
            "local_count": len(local_ids),
            "broker_count": len(broker_positions or []),
            "orphan_local": orphan_local,
            "executed_trade_ids": len(self._executed_trade_ids),
        }

    @staticmethod
    def _new_position_id(symbol: str) -> str:
        ts = datetime.now(timezone.utc).strftime("%y%m%d_%H%M%S_%f")[-12:]
        return f"TL_{symbol}_{ts}"
