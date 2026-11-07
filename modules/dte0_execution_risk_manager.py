#!/usr/bin/env python3
"""
0DTE Execution Risk Manager — Liquidity / ADV Guard (ORB + Trendline + Breakout).

Position Sizing Policy (upstream, unchanged):
  sacred buffer → trade only 90% of equity (0DTE_TRADING_CAPITAL_PCT)
  equal 1/N slots (Account0DTE.env 0DTE_SLOT_COUNT) → requested_qty = slot_affordable(...)

This module is the Risk Management guard on that requested size. It does **not**
replace slot sizing. It only clamps (or fail-closed rejects) when the slot-sized
qty would exceed market-executable limits for that contract:

  approved = min(requested_slot_qty, BP, ask_size×entry_depth%, ADV%)

Liquidity Guard v1.7: volume / OI / exit-bid qty caps off (pct=0). Bid size is
MQ evidence (LIVE_MIN_BID_SIZE) and is **not** a qty cap. Entry depth default
is 200% of displayed ask. When slot qty is already within all market caps,
winning_governor=slot_request. LIVE_MAX_CONTRACTS_PER_TRADE=0 keeps the
artificial hard max off so growth from $5k scales with the book until
liquidity binds.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional, Tuple, TYPE_CHECKING

if TYPE_CHECKING:
    from easy0DTE.modules.options_chain_manager import OptionContract
    from modules.broker_adapter import BrokerAccountBalance, BrokerAdapter

# Fill-alert Liq Cap line — size-down governors only (not BP / position_clamp / MQ reject).
LIQUIDITY_GUARD_ALERT_CAP_GOVERNORS = frozenset(
    {
        "volume_participation",
        "oi_participation",
        "entry_book_depth",
        "exit_book_depth",
        "book_depth",
        "underlying_adv",
    }
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ClampStep:
    """One quantity governor step for audit / winning-governor selection."""

    name: str
    qty: int
    detail: Optional[Dict[str, Any]] = None


@dataclass
class ExecutionRiskResult:
    approved_quantity: int
    blocked: bool
    block_reason: Optional[str]
    audit_data: Dict[str, Any] = field(default_factory=dict)


def _cfg_int(key: str, default: int) -> int:
    try:
        from modules.config_loader import get_config_value

        raw = get_config_value(key, default, convert_type=False)
    except ImportError:
        import os

        raw = os.getenv(key, str(default))
    if raw is None:
        return default
    try:
        return int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return default


def _cfg_float(key: str, default: float) -> float:
    try:
        from modules.config_loader import get_config_value

        raw = get_config_value(key, default, convert_type=False)
    except ImportError:
        import os

        raw = os.getenv(key, str(default))
    if raw is None:
        return default
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _cfg_bool(key: str, default: bool) -> bool:
    try:
        from modules.config_loader import get_config_value

        raw = get_config_value(key, "true" if default else "false", convert_type=False)
    except ImportError:
        import os

        raw = os.getenv(key, "true" if default else "false")
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _floor_div_usd(total_usd: float, per_unit_usd: float) -> int:
    if per_unit_usd <= 0 or total_usd <= 0:
        return 0
    return int(math.floor((total_usd + 1e-9) / per_unit_usd))


def _optional_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _spread_pct_threshold(config_value: float) -> float:
    """Config may be fraction (0.10 = 10%) or percent (10.0). Compare to bid_ask_spread_pct."""
    from modules.spread_policy import spread_points_from_env

    return spread_points_from_env(float(config_value))


def live_entry_max_spread_fraction() -> float:
    """Fraction (0.10 = 10%) for Trendline pick gate — matches LIVE_ENTRY market-quality."""
    from modules.spread_policy import live_entry_max_spread_fraction as _fn

    return _fn()


def live_entry_max_spread_points() -> float:
    """Percent points (10.0 = 10%) for bid_ask_spread_pct comparisons."""
    from modules.spread_policy import live_entry_max_spread_points as _fn

    return _fn()


def orb_opening_entry_max_spread_pct(
    *,
    momentum_score: float = 0.0,
    opening_active: bool = False,
    selector_max_spread_pct: Optional[float] = None,
    selected_spread_pct: Optional[float] = None,
) -> float:
    """
    Strict ORB single-leg entry spread cap (% points).

    Path-owned cap only — no momentum, opening, or refresh slack widening.
    """
    from modules.spread_policy import orb_entry_max_spread_points

    _ = (momentum_score, opening_active, selector_max_spread_pct, selected_spread_pct)
    return float(orb_entry_max_spread_points())


def _contract_symbol(contract: "OptionContract") -> str:
    sym = str(getattr(contract, "symbol", "") or "")
    side = str(getattr(contract, "option_type", "") or "").upper()[:1]
    strike = float(getattr(contract, "strike", 0.0) or 0.0)
    exp = str(getattr(contract, "expiry", "") or "")
    return f"{sym}_{exp}_{strike}_{side}"


def _effective_buying_power(account_balance: Optional["BrokerAccountBalance"]) -> Optional[float]:
    if account_balance is None:
        return None
    bp = float(getattr(account_balance, "buying_power", 0.0) or 0.0)
    cash = float(getattr(account_balance, "cash_available", 0.0) or 0.0)
    if bp > 0:
        return bp
    if cash > 0:
        return cash
    return None


def _record_governor(audit: Dict[str, Any], step: ClampStep, before: int) -> None:
    """Track clamps and which governor last reduced qty (binding winner)."""
    history = audit.setdefault("governor_steps", [])
    history.append(
        {
            "name": step.name,
            "before": before,
            "after": int(step.qty),
            "detail": step.detail or {},
        }
    )
    if int(step.qty) < int(before):
        audit["winning_governor"] = step.name
        audit["winning_governor_qty"] = int(step.qty)


def _gate_position_clamp(
    approved_qty: int,
    contract: "OptionContract",
    audit: Dict[str, Any],
) -> Tuple[int, Optional[str]]:
    max_contracts = _cfg_int("LIVE_MAX_CONTRACTS_PER_TRADE", 0)
    before = approved_qty
    applied = False

    if max_contracts > 0:
        approved_qty = min(approved_qty, max_contracts)
        if approved_qty < before:
            applied = True
            log.info(
                "LIVE_ENTRY_QUANTITY_CLAMP | gate=position_clamp | symbol=%s | before=%d | after=%d | max_contracts=%d",
                contract.symbol,
                before,
                approved_qty,
                max_contracts,
            )

    audit["position_clamp_applied"] = applied
    audit["live_max_contracts_per_trade"] = max_contracts
    _record_governor(
        audit,
        ClampStep("position_clamp", max(0, approved_qty), {"max_contracts": max_contracts}),
        before,
    )
    return max(0, approved_qty), None


def _gate_buying_power_clamp(
    approved_qty: int,
    contract: "OptionContract",
    account_balance: Optional["BrokerAccountBalance"],
    audit: Dict[str, Any],
) -> Tuple[int, Optional[str]]:
    budget = _effective_buying_power(account_balance)
    ask = float(getattr(contract, "ask", 0.0) or 0.0)
    before = approved_qty
    applied = False
    if budget is None or ask <= 0 or approved_qty <= 0:
        audit["buying_power_clamp_applied"] = False
        audit["buying_power_budget"] = budget
        _record_governor(audit, ClampStep("buying_power", approved_qty, {"budget": budget}), before)
        return approved_qty, None

    per_contract = ask * 100.0
    max_affordable = _floor_div_usd(budget, per_contract)
    if max_affordable < approved_qty:
        applied = True
        log.info(
            "LIVE_ENTRY_BUYING_POWER_CLAMP | symbol=%s | before=%d | after=%d | buying_power=%.2f | ask=%.4f",
            contract.symbol,
            approved_qty,
            max(0, max_affordable),
            budget,
            ask,
        )
        approved_qty = max(0, max_affordable)

    audit["buying_power_clamp_applied"] = applied
    audit["buying_power_budget"] = budget
    _record_governor(
        audit,
        ClampStep("buying_power", approved_qty, {"budget": budget, "ask": ask}),
        before,
    )
    return approved_qty, None


def _gate_liquidity_participation(
    approved_qty: int,
    contract: "OptionContract",
    audit: Dict[str, Any],
) -> Tuple[int, Optional[str]]:
    """Vol/OI participation. Fail-closed when pct > 0 and volume/OI missing or zero.

    Volume / OI qty caps are off at v1.6 (pct=0). When volume pct > 0:
      volume_cap = max(LIVE_OPTION_VOLUME_MIN_CONTRACTS, floor(volume × pct))
    Missing/zero volume still fail-closed if pct > 0.
    """
    vol_pct = _cfg_float("LIVE_OPTION_VOLUME_PARTICIPATION_PCT", 0.0)
    oi_pct = _cfg_float("LIVE_OPTION_OI_PARTICIPATION_PCT", 0.0)
    vol_min = max(0, _cfg_int("LIVE_OPTION_VOLUME_MIN_CONTRACTS", 5))
    before = approved_qty
    applied = False

    volume = _optional_int(getattr(contract, "volume", None))
    oi = _optional_int(getattr(contract, "open_interest", None))
    audit["contract_volume"] = volume
    audit["contract_open_interest"] = oi
    audit["volume_participation_pct"] = vol_pct
    audit["oi_participation_pct"] = oi_pct
    audit["volume_min_contracts_allowance"] = vol_min

    volume_cap: Optional[int] = None
    oi_cap: Optional[int] = None

    if vol_pct > 0:
        if volume is None or volume <= 0:
            audit["participation_clamp_applied"] = False
            audit["volume_cap_raw"] = None
            audit["volume_cap"] = None
            _record_governor(
                audit,
                ClampStep("volume_participation", 0, {"volume": volume, "fail_closed": True}),
                before,
            )
            return 0, "option_volume_missing"
        volume_cap_raw = int(math.floor(volume * vol_pct))
        volume_cap = max(vol_min, volume_cap_raw)
        audit["volume_cap_raw"] = volume_cap_raw
        audit["volume_cap"] = volume_cap
        new_qty = min(approved_qty, max(0, volume_cap))
        if new_qty < approved_qty:
            applied = True
            approved_qty = new_qty

    if oi_pct > 0:
        if oi is None or oi <= 0:
            audit["participation_clamp_applied"] = False
            _record_governor(
                audit,
                ClampStep("oi_participation", 0, {"oi": oi, "fail_closed": True}),
                before,
            )
            return 0, "option_open_interest_missing"
        oi_cap = int(math.floor(oi * oi_pct))
        audit["oi_cap"] = oi_cap
        new_qty = min(approved_qty, max(0, oi_cap))
        if new_qty < approved_qty:
            applied = True
            approved_qty = new_qty

    if applied:
        log.info(
            "LIVE_ENTRY_PARTICIPATION_CLAMP | symbol=%s | before=%d | after=%d | "
            "volume=%s | volume_cap_raw=%s | volume_cap=%s | vol_min=%d | oi=%s | oi_cap=%s",
            contract.symbol,
            before,
            approved_qty,
            volume,
            audit.get("volume_cap_raw"),
            volume_cap,
            vol_min,
            oi,
            oi_cap,
        )

    audit["participation_clamp_applied"] = applied
    # Prefer more specific governor name when both could bind — record final as oi if oi reduced last
    gov = "oi_participation" if (oi_pct > 0 and oi is not None and oi > 0) else "volume_participation"
    if vol_pct > 0 and volume is not None and volume > 0 and volume_cap is not None:
        if oi_pct > 0 and oi is not None and oi > 0 and oi_cap is not None:
            gov = "oi_participation" if oi_cap <= volume_cap else "volume_participation"
        else:
            gov = "volume_participation"
    _record_governor(
        audit,
        ClampStep(
            gov,
            approved_qty,
            {
                "volume": volume,
                "oi": oi,
                "volume_cap_raw": audit.get("volume_cap_raw"),
                "volume_cap": volume_cap,
                "volume_min_contracts": vol_min,
                "oi_cap": oi_cap,
            },
        ),
        before,
    )
    return max(0, approved_qty), None


def _gate_book_depth_participation(
    approved_qty: int,
    contract: "OptionContract",
    audit: Dict[str, Any],
) -> Tuple[int, Optional[str]]:
    """
    Liquidity Guard v1.5 book clamp:
      entry_depth_cap = floor(ask_size × LIVE_OPTION_BOOK_DEPTH_PARTICIPATION_PCT)
        default 2.0 — do not buy more than 2× displayed ask
      exit_depth_cap  = floor(bid_size × LIVE_OPTION_EXIT_BOOK_DEPTH_PARTICIPATION_PCT)
        default 0 — bid is **not** a qty cap (MQ LIVE_MIN_BID_SIZE still rejects)

    Fail-closed only for sides whose participation pct is > 0 and size is missing.
    """
    entry_pct = _cfg_float("LIVE_OPTION_BOOK_DEPTH_PARTICIPATION_PCT", 2.0)
    exit_pct = _cfg_float("LIVE_OPTION_EXIT_BOOK_DEPTH_PARTICIPATION_PCT", 0.0)
    before = approved_qty
    bid_size = _optional_int(getattr(contract, "bid_size", None))
    ask_size = _optional_int(getattr(contract, "ask_size", None))
    audit["bid_size"] = bid_size
    audit["ask_size"] = ask_size
    audit["book_depth_participation_pct"] = entry_pct
    audit["exit_book_depth_participation_pct"] = exit_pct

    if entry_pct <= 0 and exit_pct <= 0:
        audit["book_depth_clamp_applied"] = False
        audit["entry_depth_cap"] = None
        audit["exit_depth_cap"] = None
        _record_governor(audit, ClampStep("book_depth", approved_qty, {"enabled": False}), before)
        return approved_qty, None

    if entry_pct > 0 and ask_size is None:
        audit["book_depth_clamp_applied"] = False
        audit["entry_depth_cap"] = None
        audit["exit_depth_cap"] = None
        _record_governor(
            audit,
            ClampStep(
                "entry_book_depth",
                0,
                {"bid_size": bid_size, "ask_size": ask_size, "fail_closed": True},
            ),
            before,
        )
        return 0, "option_book_depth_missing"

    if exit_pct > 0 and bid_size is None:
        audit["book_depth_clamp_applied"] = False
        audit["entry_depth_cap"] = None
        audit["exit_depth_cap"] = None
        _record_governor(
            audit,
            ClampStep(
                "exit_book_depth",
                0,
                {"bid_size": bid_size, "ask_size": ask_size, "fail_closed": True},
            ),
            before,
        )
        return 0, "option_book_depth_missing"

    entry_depth_cap: Optional[int] = None
    exit_depth_cap: Optional[int] = None
    depth_cap = approved_qty
    if entry_pct > 0 and ask_size is not None:
        entry_depth_cap = int(math.floor(ask_size * entry_pct))
        depth_cap = min(depth_cap, max(0, entry_depth_cap))
    if exit_pct > 0 and bid_size is not None:
        exit_depth_cap = int(math.floor(bid_size * exit_pct))
        depth_cap = min(depth_cap, max(0, exit_depth_cap))

    min_book = None
    if bid_size is not None and ask_size is not None:
        min_book = min(bid_size, ask_size)
    audit["entry_depth_cap"] = entry_depth_cap
    audit["exit_depth_cap"] = exit_depth_cap
    audit["min_book_size"] = min_book
    audit["book_depth_cap"] = depth_cap

    new_qty = min(approved_qty, max(0, depth_cap))
    applied = new_qty < approved_qty
    entry_c = entry_depth_cap if entry_depth_cap is not None else approved_qty
    exit_c = exit_depth_cap if exit_depth_cap is not None else approved_qty
    if exit_pct > 0 and exit_c < entry_c:
        gov_name = "exit_book_depth"
    elif entry_pct > 0 and (exit_pct <= 0 or entry_c < exit_c):
        gov_name = "entry_book_depth"
    else:
        gov_name = "book_depth"
    if applied:
        log.info(
            "LIVE_ENTRY_BOOK_DEPTH_CLAMP | symbol=%s | before=%d | after=%d | "
            "bid_size=%s | ask_size=%s | entry_cap=%s | exit_cap=%s | min_book=%s | "
            "entry_pct=%.4f | exit_pct=%.4f | side=%s",
            contract.symbol,
            before,
            new_qty,
            bid_size,
            ask_size,
            entry_depth_cap,
            exit_depth_cap,
            min_book,
            entry_pct,
            exit_pct,
            gov_name,
        )
    approved_qty = new_qty
    audit["book_depth_clamp_applied"] = applied
    _record_governor(
        audit,
        ClampStep(
            gov_name,
            approved_qty,
            {
                "bid_size": bid_size,
                "ask_size": ask_size,
                "min_book": min_book,
                "entry_depth_cap": entry_depth_cap,
                "exit_depth_cap": exit_depth_cap,
                "depth_cap": depth_cap,
                "entry_pct": entry_pct,
                "exit_pct": exit_pct,
            },
        ),
        before,
    )
    return max(0, approved_qty), None


def _gate_underlying_adv_participation(
    approved_qty: int,
    contract: "OptionContract",
    audit: Dict[str, Any],
) -> Tuple[int, Optional[str]]:
    """Option notional ≤ ADV_dollars × pct. Fail-closed when pct > 0 and ADV unknown/zero."""
    adv_pct = _cfg_float("LIVE_UNDERLYING_ADV_PARTICIPATION_PCT", 0.02)
    before = approved_qty
    ask = float(getattr(contract, "ask", 0.0) or 0.0)
    symbol = str(getattr(contract, "symbol", "") or "")
    audit["underlying_adv_participation_pct"] = adv_pct

    if adv_pct <= 0:
        audit["adv_clamp_applied"] = False
        _record_governor(audit, ClampStep("underlying_adv", approved_qty, {"enabled": False}), before)
        return approved_qty, None

    adv_dollars = 0.0
    try:
        from modules.adv_data_manager import get_adv_manager

        adv_manager = get_adv_manager()
        adv_dollars = float(adv_manager.get_adv(symbol) or 0.0)
        if adv_dollars <= 0:
            try:
                floors = 0
                if hasattr(adv_manager, "apply_liquid_adv_floors"):
                    floors = int(adv_manager.apply_liquid_adv_floors([symbol]) or 0)
                if float(adv_manager.get_adv(symbol) or 0.0) <= 0 and hasattr(
                    adv_manager, "apply_default_adv_floors"
                ):
                    floors += int(adv_manager.apply_default_adv_floors([symbol]) or 0)
                if floors:
                    adv_dollars = float(adv_manager.get_adv(symbol) or 0.0)
                    log.warning(
                        "LIVE_ENTRY_ADV_FLOOR_RECOVERED | symbol=%s | adv_dollars=%.0f | reason=lookup_missing",
                        symbol,
                        adv_dollars,
                    )
            except Exception as floor_exc:
                log.warning(
                    "LIVE_ENTRY_ADV_FLOOR_RECOVERY_FAILED | symbol=%s | error=%s",
                    symbol,
                    floor_exc,
                )
    except Exception as exc:
        log.warning(
            "LIVE_ENTRY_ADV_LOOKUP_FAILED | symbol=%s | error=%s",
            symbol,
            exc,
        )
        adv_dollars = 0.0

    audit["underlying_adv_dollars"] = adv_dollars
    if ask <= 0:
        audit["adv_clamp_applied"] = False
        _record_governor(
            audit,
            ClampStep(
                "underlying_adv",
                0,
                {"adv_dollars": adv_dollars, "ask": ask, "fail_closed": True},
            ),
            before,
        )
        return 0, "option_ask_missing"
    if adv_dollars <= 0:
        audit["adv_clamp_applied"] = False
        _record_governor(
            audit,
            ClampStep(
                "underlying_adv",
                0,
                {"adv_dollars": adv_dollars, "ask": ask, "fail_closed": True},
            ),
            before,
        )
        log.warning(
            "LIVE_ENTRY_ADV_MISSING | symbol=%s | adv_dollars=%.2f | ask=%.4f | "
            "hint=ensure_adv_ready_on_startup_or_gcs_cache",
            symbol,
            adv_dollars,
            ask,
        )
        return 0, "underlying_adv_missing"

    notional_cap = adv_dollars * adv_pct
    per_contract = ask * 100.0
    adv_qty_cap = _floor_div_usd(notional_cap, per_contract)
    new_qty = min(approved_qty, max(0, adv_qty_cap))
    applied = new_qty < approved_qty
    if applied:
        log.info(
            "LIVE_ENTRY_ADV_CLAMP | symbol=%s | before=%d | after=%d | "
            "adv_dollars=%.2f | pct=%.5f | notional_cap=%.2f | ask=%.4f",
            symbol,
            before,
            new_qty,
            adv_dollars,
            adv_pct,
            notional_cap,
            ask,
        )
    approved_qty = new_qty
    audit["adv_clamp_applied"] = applied
    audit["underlying_adv_notional_cap"] = notional_cap
    _record_governor(
        audit,
        ClampStep(
            "underlying_adv",
            approved_qty,
            {"adv_dollars": adv_dollars, "notional_cap": notional_cap, "adv_qty_cap": adv_qty_cap},
        ),
        before,
    )
    return max(0, approved_qty), None


def _gate_market_quality(
    contract: "OptionContract",
    audit: Dict[str, Any],
    *,
    max_spread_pct: Optional[float] = None,
    strategy_type: str = "",
    demo_mode: bool = False,
) -> tuple[bool, Optional[str]]:
    from easy0DTE.modules.option_entry_liquidity_validator import (
        REASON_LIQUIDITY_FAILED,
        validate_option_entry_liquidity,
        validator_diagnostics_payload,
    )

    if max_spread_pct is not None and float(max_spread_pct) > 0:
        max_spread = float(max_spread_pct)
    else:
        from modules.spread_policy import entry_max_spread_points_for_strategy

        max_spread = entry_max_spread_points_for_strategy(strategy_type)

    path = "trendline" if "trendline" in str(strategy_type or "").lower() else "orb"
    q_age = getattr(contract, "quote_age_seconds", None)
    val = validate_option_entry_liquidity(
        contract,
        path=path,
        max_spread_pct_points=max_spread,
        quote_age_seconds=float(q_age) if q_age is not None else None,
        demo_mode=bool(demo_mode),
        enforce_quote_age=None,
        require_quote_fields=True,
    )
    if not val.get("passed"):
        code = str(val.get("reason_code") or REASON_LIQUIDITY_FAILED)
        metrics = val.get("metrics") if isinstance(val.get("metrics"), dict) else {}
        audit["market_quality_passed"] = False
        audit.update(validator_diagnostics_payload(val))
        audit["spread_pct"] = metrics.get("spread_pct")
        audit["max_spread_pct"] = metrics.get("max_spread_pct")
        log.warning(
            "LIVE_ENTRY_MARKET_QUALITY_REJECT | symbol=%s | reason=%s | detail=%s",
            contract.symbol,
            code,
            str(val.get("reason_text") or ""),
        )
        return False, code

    min_depth_default = 0 if demo_mode else 5
    min_ask_size = _cfg_int("LIVE_MIN_ASK_SIZE", min_depth_default)
    min_bid_size = _cfg_int("LIVE_MIN_BID_SIZE", min_ask_size if min_ask_size > 0 else 0)
    ask_size = _optional_int(getattr(contract, "ask_size", None))
    bid_size = _optional_int(getattr(contract, "bid_size", None))
    audit["bid_size"] = bid_size
    audit["ask_size"] = ask_size

    # Fail-closed on missing sizes when min thresholds are enabled.
    if min_ask_size > 0 and ask_size is None:
        audit["market_quality_passed"] = False
        log.warning(
            "LIVE_ENTRY_MARKET_QUALITY_REJECT | symbol=%s | reason=ask_size_missing | min=%d",
            contract.symbol,
            min_ask_size,
        )
        return False, "ask_size_missing"
    if min_bid_size > 0 and bid_size is None:
        audit["market_quality_passed"] = False
        log.warning(
            "LIVE_ENTRY_MARKET_QUALITY_REJECT | symbol=%s | reason=bid_size_missing | min=%d",
            contract.symbol,
            min_bid_size,
        )
        return False, "bid_size_missing"

    if min_ask_size > 0 and ask_size is not None and ask_size < min_ask_size:
        audit["market_quality_passed"] = False
        log.warning(
            "LIVE_ENTRY_MARKET_QUALITY_REJECT | symbol=%s | reason=ask_size_too_small | ask_size=%s | min=%d",
            contract.symbol,
            ask_size,
            min_ask_size,
        )
        return False, "ask_size_too_small"
    if min_bid_size > 0 and bid_size is not None and bid_size < min_bid_size:
        audit["market_quality_passed"] = False
        log.warning(
            "LIVE_ENTRY_MARKET_QUALITY_REJECT | symbol=%s | reason=bid_size_too_small | bid_size=%s | min=%d",
            contract.symbol,
            bid_size,
            min_bid_size,
        )
        return False, "bid_size_too_small"

    audit["market_quality_passed"] = True
    audit["spread_pct"] = float(getattr(contract, "bid_ask_spread_pct", 0.0) or 0.0)
    return True, None


def apply_live_execution_risk_limits(
    *,
    quantity: int,
    contract: "OptionContract",
    strategy_type: str,
    demo_mode: bool,
    broker_adapter: Optional["BrokerAdapter"] = None,
    account_balance: Optional["BrokerAccountBalance"] = None,
    max_spread_pct: Optional[float] = None,
) -> ExecutionRiskResult:
    """
    Clamp Live 0DTE entry quantity through the Liquidity / ADV Guard.

    Upstream `quantity` is already Position-Sized (slot from 90% / N).
    This guard only reduces qty when market caps bind; otherwise returns
    the slot request unchanged (winning_governor=slot_request).

    Demo: size clamps audit-only unless LIVE_SIZE_CAP_ENFORCE_IN_DEMO=true.
    Demo + true-to-live marks: market-quality (spread/depth) gate always enforced.
    Live: always enforce; fail-closed when approved qty < 1 or market quality fails.
    """
    _ = broker_adapter  # reserved; quote refresh runs upstream
    enforce_clamps = (not demo_mode) or _cfg_bool("LIVE_SIZE_CAP_ENFORCE_IN_DEMO", False)
    _ttl_raw = None
    try:
        from modules.config_loader import get_config_value as _gcv_ttl

        _ttl_raw = _gcv_ttl("OPTION_TRUE_TO_LIVE_EXECUTABLE_MARKS", None, convert_type=False)
        if _ttl_raw is None or (isinstance(_ttl_raw, str) and not str(_ttl_raw).strip()):
            _ttl_raw = _gcv_ttl(
                "OPTION_STEALTH_0DTE_TRUE_TO_LIVE_EXECUTABLE_MARKS", None, convert_type=False
            )
    except ImportError:
        import os as _os_ttl

        _ttl_raw = _os_ttl.getenv("OPTION_TRUE_TO_LIVE_EXECUTABLE_MARKS")
        if _ttl_raw is None or not str(_ttl_raw).strip():
            _ttl_raw = _os_ttl.getenv("OPTION_STEALTH_0DTE_TRUE_TO_LIVE_EXECUTABLE_MARKS")
    _ttl_marks = (
        True
        if _ttl_raw is None or (isinstance(_ttl_raw, str) and not str(_ttl_raw).strip())
        else str(_ttl_raw).strip().lower() in ("1", "true", "yes", "on")
    )
    enforce_market_quality = (not demo_mode) or enforce_clamps or _ttl_marks
    requested = max(0, int(quantity or 0))
    ask = float(getattr(contract, "ask", 0.0) or 0.0)
    premium = ask if ask > 0 else float(getattr(contract, "mid_price", 0.0) or 0.0)
    entry_cost_before = premium * requested * 100.0

    audit: Dict[str, Any] = {
        "strategy_type": strategy_type,
        "symbol": getattr(contract, "symbol", ""),
        "contract_symbol": _contract_symbol(contract),
        "requested_quantity": requested,
        "premium": premium,
        "entry_cost_before": entry_cost_before,
        "demo_mode": demo_mode,
        "enforce": enforce_clamps,
        "enforce_market_quality": enforce_market_quality,
        "position_clamp_applied": False,
        "buying_power_clamp_applied": False,
        "participation_clamp_applied": False,
        "book_depth_clamp_applied": False,
        "adv_clamp_applied": False,
        "market_quality_passed": True,
        "winning_governor": None,
        "governor_steps": [],
        "blocked": False,
        "block_reason": None,
    }

    if requested <= 0:
        audit["approved_quantity"] = 0
        audit["entry_cost_after"] = 0.0
        audit["blocked"] = True
        audit["block_reason"] = "invalid_requested_quantity"
        _emit_audit(audit)
        return ExecutionRiskResult(
            approved_quantity=0,
            blocked=True if enforce_clamps else False,
            block_reason="invalid_requested_quantity",
            audit_data=audit,
        )

    simulated_qty = requested
    fail_reason: Optional[str] = None

    for gate_fn in (
        _gate_position_clamp,
        lambda q, c, a: _gate_buying_power_clamp(q, c, account_balance, a),
        _gate_liquidity_participation,
        _gate_book_depth_participation,
        _gate_underlying_adv_participation,
    ):
        simulated_qty, fail_reason = gate_fn(simulated_qty, contract, audit)
        if fail_reason:
            break

    audit["simulated_approved_quantity"] = simulated_qty

    # Prefer market-limit fail-closed reasons (vol/OI/depth/ADV) when clamps already rejected.
    if enforce_clamps and fail_reason:
        # Still evaluate market quality into audit for diagnostics.
        quality_ok, quality_reason = _gate_market_quality(
            contract,
            audit,
            max_spread_pct=max_spread_pct,
            strategy_type=strategy_type,
            demo_mode=demo_mode,
        )
        # Prefer actionable market-quality reason when MQ also fails (desk triage).
        # Keep clamp reason as secondary so ADV / depth / vol gaps remain visible.
        if not quality_ok and quality_reason:
            primary_reason = quality_reason
            audit["secondary_block_reason"] = fail_reason
        else:
            primary_reason = fail_reason
        audit["blocked"] = True
        audit["block_reason"] = primary_reason
        audit["approved_quantity"] = 0
        audit["entry_cost_after"] = 0.0
        if not audit.get("winning_governor"):
            audit["winning_governor"] = (
                "market_quality" if primary_reason == quality_reason else fail_reason
            )
        _emit_audit(audit)
        return ExecutionRiskResult(
            approved_quantity=0,
            blocked=True,
            block_reason=primary_reason,
            audit_data=audit,
        )

    quality_ok, quality_reason = _gate_market_quality(
        contract,
        audit,
        max_spread_pct=max_spread_pct,
        strategy_type=strategy_type,
        demo_mode=demo_mode,
    )

    if enforce_market_quality and not quality_ok:
        audit["blocked"] = True
        audit["block_reason"] = quality_reason
        audit["approved_quantity"] = 0
        audit["entry_cost_after"] = 0.0
        audit["winning_governor"] = "market_quality"
        _emit_audit(audit)
        return ExecutionRiskResult(
            approved_quantity=0,
            blocked=True,
            block_reason=quality_reason,
            audit_data=audit,
        )

    if enforce_clamps:
        approved_qty = simulated_qty
    else:
        approved_qty = requested

    if enforce_clamps and approved_qty < 1:
        if not quality_ok:
            reason = quality_reason or "market_quality_failed"
        elif fail_reason:
            reason = fail_reason
        elif simulated_qty < 1:
            reason = "insufficient_buying_power_or_clamps"
        else:
            reason = "quantity_below_minimum"
        audit["blocked"] = True
        audit["block_reason"] = reason
        audit["approved_quantity"] = 0
        audit["entry_cost_after"] = 0.0
        if not audit.get("winning_governor"):
            audit["winning_governor"] = reason
        _emit_audit(audit)
        return ExecutionRiskResult(
            approved_quantity=0,
            blocked=True,
            block_reason=reason,
            audit_data=audit,
        )

    entry_cost_after = premium * approved_qty * 100.0
    audit["approved_quantity"] = approved_qty
    audit["entry_cost_after"] = entry_cost_after
    audit["blocked"] = False
    if approved_qty == requested and not audit.get("winning_governor"):
        audit["winning_governor"] = "slot_request"
    _emit_audit(audit)

    return ExecutionRiskResult(
        approved_quantity=approved_qty,
        blocked=False,
        block_reason=None,
        audit_data=audit,
    )


def _fmt_participation_ratio(value: Any, *, default: float) -> str:
    try:
        frac = float(value)
    except (TypeError, ValueError):
        frac = float(default)
    text = f"{frac:.4f}".rstrip("0").rstrip(".")
    return text if text else "0"


def _fmt_participation_pct_label(value: Any, *, default: float) -> str:
    try:
        frac = float(value)
    except (TypeError, ValueError):
        frac = float(default)
    pct = frac * 100.0
    if abs(pct - round(pct)) < 1e-6:
        return f"{int(round(pct))}%"
    text = f"{pct:.4f}".rstrip("0").rstrip(".")
    return f"{text}%"


def format_liquidity_guard_cap_alert_line(
    execution_risk: Optional[Mapping[str, Any]],
) -> Optional[str]:
    """Telegram fill-alert suffix when Liquidity Guard sized down the slot qty.

    Returns ``Liq Cap: ⚖️ …`` for market governors only. Omits buying_power,
    position_clamp, slot_request, and market_quality rejects.
    """
    if not isinstance(execution_risk, Mapping):
        return None
    gov = str(execution_risk.get("winning_governor") or "").strip()
    if gov not in LIQUIDITY_GUARD_ALERT_CAP_GOVERNORS:
        return None
    try:
        requested = int(execution_risk.get("requested_quantity"))
        approved = int(execution_risk.get("approved_quantity"))
    except (TypeError, ValueError):
        requested = -1
        approved = -1
    if requested >= 0 and approved >= 0 and approved >= requested:
        return None

    if gov == "volume_participation":
        pct = _fmt_participation_ratio(
            execution_risk.get("volume_participation_pct"),
            default=0.05,
        )
        return f"Liq Cap: ⚖️ Option Vol {pct}"
    if gov == "oi_participation":
        pct = _fmt_participation_ratio(
            execution_risk.get("oi_participation_pct"),
            default=0.05,
        )
        return f"Liq Cap: ⚖️ Option OI {pct}"
    if gov == "entry_book_depth":
        pct = _fmt_participation_pct_label(
            execution_risk.get("book_depth_participation_pct"),
            default=1.0,
        )
        return f"Liq Cap: ⚖️ Book Ask {pct}"
    if gov == "exit_book_depth":
        pct = _fmt_participation_pct_label(
            execution_risk.get("exit_book_depth_participation_pct")
            or execution_risk.get("book_depth_participation_pct"),
            default=0.0,
        )
        return f"Liq Cap: ⚖️ Book Bid {pct}"
    if gov == "book_depth":
        pct = _fmt_participation_pct_label(
            execution_risk.get("book_depth_participation_pct"),
            default=1.0,
        )
        return f"Liq Cap: ⚖️ Book {pct}"
    if gov == "underlying_adv":
        pct = _fmt_participation_pct_label(
            execution_risk.get("underlying_adv_participation_pct"),
            default=0.02,
        )
        return f"Liq Cap: ⚖️ Price ADV {pct}"
    return None


def _emit_audit(audit: Dict[str, Any]) -> None:
    log.info(
        "LIVE_ENTRY_EXECUTION_RISK_AUDIT | strategy=%s | symbol=%s | requested=%s | approved=%s | "
        "premium=%.4f | cost_before=%.2f | cost_after=%.2f | position_clamp=%s | "
        "buying_power_clamp=%s | participation_clamp=%s | book_depth_clamp=%s | adv_clamp=%s | "
        "winning_governor=%s | market_quality=%s | blocked=%s | reason=%s",
        audit.get("strategy_type"),
        audit.get("symbol"),
        audit.get("requested_quantity"),
        audit.get("approved_quantity"),
        float(audit.get("premium") or 0.0),
        float(audit.get("entry_cost_before") or 0.0),
        float(audit.get("entry_cost_after") or 0.0),
        audit.get("position_clamp_applied"),
        audit.get("buying_power_clamp_applied"),
        audit.get("participation_clamp_applied"),
        audit.get("book_depth_clamp_applied"),
        audit.get("adv_clamp_applied"),
        audit.get("winning_governor"),
        audit.get("market_quality_passed"),
        audit.get("blocked"),
        audit.get("block_reason"),
    )


def _leg_attr(leg: Any, *names: str, default: Any = None) -> Any:
    if isinstance(leg, dict):
        for name in names:
            if name in leg and leg[name] is not None:
                return leg[name]
        return default
    for name in names:
        val = getattr(leg, name, None)
        if val is not None:
            return val
    return default


def _match_chain_contract(
    chain_leg: Any,
    contract: "OptionContract",
) -> bool:
    try:
        strike = float(_leg_attr(chain_leg, "strike", default=0.0) or 0.0)
        otype = str(_leg_attr(chain_leg, "option_type", default="") or "").lower()
        return (
            abs(strike - float(contract.strike)) < 1e-4
            and otype == str(contract.option_type or "").lower()
        )
    except (TypeError, ValueError):
        return False


def _leg_to_option_contract(base: "OptionContract", leg: Any) -> "OptionContract":
    from easy0DTE.modules.options_chain_manager import OptionContract

    bid = float(_leg_attr(leg, "bid", "bid_price", default=0.0) or 0.0)
    ask = float(_leg_attr(leg, "ask", "ask_price", default=0.0) or 0.0)
    return OptionContract(
        symbol=base.symbol,
        strike=float(_leg_attr(leg, "strike", default=base.strike) or base.strike),
        expiry=str(_leg_attr(leg, "expiry", default=base.expiry) or base.expiry),
        option_type=str(
            _leg_attr(leg, "option_type", default=base.option_type) or base.option_type
        ),
        bid=bid,
        ask=ask,
        last=float(_leg_attr(leg, "last", default=0.0) or 0.0),
        volume=int(_leg_attr(leg, "volume", default=0) or 0),
        open_interest=int(
            _leg_attr(leg, "open_interest", "openInterest", default=0) or 0
        ),
        delta=float(_leg_attr(leg, "delta", default=base.delta) or base.delta),
        gamma=float(_leg_attr(leg, "gamma", default=base.gamma) or base.gamma),
        theta=float(_leg_attr(leg, "theta", default=base.theta) or base.theta),
        vega=float(_leg_attr(leg, "vega", default=base.vega) or base.vega),
        implied_volatility=float(
            _leg_attr(leg, "implied_volatility", default=base.implied_volatility)
            or base.implied_volatility
        ),
    )


def _stamp_book_sizes(target: "OptionContract", source: Any) -> None:
    """Copy bid_size / ask_size onto contract (setattr — not dataclass fields)."""
    ask_size = _leg_attr(source, "ask_size", "askSize")
    bid_size = _leg_attr(source, "bid_size", "bidSize")
    if ask_size is not None:
        setattr(target, "ask_size", _optional_int(ask_size))
    if bid_size is not None:
        setattr(target, "bid_size", _optional_int(bid_size))


def _leg_has_refresh_fields(leg: Any) -> bool:
    """True when leg has bid/ask/vol/OI and both book sizes for cache reuse."""
    try:
        bid = float(_leg_attr(leg, "bid", "bid_price", default=0.0) or 0.0)
        ask = float(_leg_attr(leg, "ask", "ask_price", default=0.0) or 0.0)
    except (TypeError, ValueError):
        return False
    if bid <= 0 or ask <= 0:
        return False
    if _leg_attr(leg, "volume") is None:
        return False
    if _leg_attr(leg, "open_interest", "openInterest") is None:
        return False
    if _optional_int(_leg_attr(leg, "bid_size", "bidSize")) is None:
        return False
    if _optional_int(_leg_attr(leg, "ask_size", "askSize")) is None:
        return False
    return True


def _find_matching_leg(chain: Optional[Dict[str, Any]], contract: "OptionContract") -> Any:
    if not isinstance(chain, dict):
        return None
    legs = list(chain.get("calls") or []) + list(chain.get("puts") or [])
    for leg in legs:
        if _match_chain_contract(leg, contract):
            return leg
    return None


def _apply_matched_leg_refresh(
    contract: "OptionContract",
    leg: Any,
    *,
    source: str,
    quote_age_seconds: float,
) -> "OptionContract":
    refreshed = _leg_to_option_contract(contract, leg)
    setattr(refreshed, "quote_age_seconds", float(quote_age_seconds))
    _stamp_book_sizes(refreshed, leg)
    if getattr(refreshed, "ask_size", None) is None:
        prior_ask = getattr(contract, "ask_size", None)
        if prior_ask is not None:
            setattr(refreshed, "ask_size", prior_ask)
    if getattr(refreshed, "bid_size", None) is None:
        prior_bid = getattr(contract, "bid_size", None)
        if prior_bid is not None:
            setattr(refreshed, "bid_size", prior_bid)
    log.info(
        "LIVE_ENTRY_QUOTE_REFRESHED | symbol=%s | source=%s | cache_age_s=%.2f | "
        "bid=%.4f | ask=%.4f | volume=%d | oi=%d | bid_size=%s | ask_size=%s",
        refreshed.symbol,
        source,
        float(quote_age_seconds),
        refreshed.bid,
        refreshed.ask,
        refreshed.volume,
        refreshed.open_interest,
        getattr(refreshed, "bid_size", None),
        getattr(refreshed, "ask_size", None),
    )
    return refreshed


def _try_refresh_from_chain(
    contract: "OptionContract",
    chain: Optional[Dict[str, Any]],
    *,
    source: str,
    cache_age_sec: float,
    max_age_sec: float,
    require_complete: bool,
) -> Optional["OptionContract"]:
    if chain is None:
        return None
    if cache_age_sec > max_age_sec:
        log.info(
            "LIVE_ENTRY_QUOTE_REFRESH_SKIP_STALE | symbol=%s | source=%s | age_s=%.2f | max_s=%.2f",
            getattr(contract, "symbol", ""),
            source,
            cache_age_sec,
            max_age_sec,
        )
        return None
    leg = _find_matching_leg(chain, contract)
    if leg is None:
        return None
    if require_complete and not _leg_has_refresh_fields(leg):
        # Allow reuse if the inbound contract already carries book sizes from select.
        try:
            leg_bid = float(_leg_attr(leg, "bid", "bid_price", default=0.0) or 0.0)
            leg_ask = float(_leg_attr(leg, "ask", "ask_price", default=0.0) or 0.0)
        except (TypeError, ValueError):
            leg_bid, leg_ask = 0.0, 0.0
        prior_ok = (
            _optional_int(getattr(contract, "bid_size", None)) is not None
            and _optional_int(getattr(contract, "ask_size", None)) is not None
            and leg_bid > 0
            and leg_ask > 0
            and _leg_attr(leg, "volume") is not None
            and _leg_attr(leg, "open_interest", "openInterest") is not None
        )
        if not prior_ok:
            log.info(
                "LIVE_ENTRY_QUOTE_REFRESH_SKIP_INCOMPLETE | symbol=%s | source=%s | "
                "reason=missing_vol_oi_or_book_sizes",
                getattr(contract, "symbol", ""),
                source,
            )
            return None
    return _apply_matched_leg_refresh(
        contract,
        leg,
        source=source,
        quote_age_seconds=max(0.0, float(cache_age_sec)),
    )


async def _hydrate_entry_book_sizes(
    contract: "OptionContract",
    broker_adapter: Optional["BrokerAdapter"],
) -> Optional["OptionContract"]:
    """Fill bid_size/ask_size from Market Quote when the chain snapshot omitted them."""
    try:
        from modules.exact_contract_option_quotes import fetch_exact_contract_quotes
        from modules.option_contract_identity import identity_from_parts, occ_from_identity
    except Exception:
        return None
    ident = identity_from_parts(
        underlying=getattr(contract, "symbol", None),
        expiration=getattr(contract, "expiry", None),
        strike=getattr(contract, "strike", None),
        call_put=getattr(contract, "option_type", None),
    )
    if ident is None:
        return None
    symbols: list[str] = []
    osi = str(getattr(contract, "osi_key", "") or "").strip()
    if osi:
        symbols.append(osi)
    occ = occ_from_identity(ident)
    if occ and occ not in symbols:
        symbols.append(occ)
    if not symbols:
        return None
    trader = None
    if broker_adapter is not None:
        trader = getattr(broker_adapter, "etrade_trader", None) or broker_adapter
    try:
        batch = await fetch_exact_contract_quotes(
            symbols,
            etrade_trader=trader,
            timeout_sec=1.2,
            broker_lane="entry_book_size",
        )
    except Exception as exc:
        log.info(
            "LIVE_ENTRY_QUOTE_REFRESH | action=exact_book_size_error | symbol=%s | error=%s",
            getattr(contract, "symbol", ""),
            exc,
        )
        return None
    quotes = getattr(batch, "quotes", None) or {}
    quote = None
    for key in symbols:
        cand = quotes.get(key)
        if cand is not None and not bool(getattr(cand, "rejected", False)):
            quote = cand
            break
    if quote is None:
        for cand in quotes.values():
            if cand is not None and not bool(getattr(cand, "rejected", False)):
                quote = cand
                break
    if quote is None:
        return None
    asz = _optional_int(getattr(quote, "ask_size", None))
    bsz = _optional_int(getattr(quote, "bid_size", None))
    if asz is None and bsz is None:
        return None
    if asz is not None:
        setattr(contract, "ask_size", asz)
    if bsz is not None:
        setattr(contract, "bid_size", bsz)
    log.info(
        "LIVE_ENTRY_QUOTE_REFRESH | action=exact_book_size | symbol=%s | strike=%s | "
        "bid_size=%s | ask_size=%s",
        getattr(contract, "symbol", ""),
        getattr(contract, "strike", ""),
        getattr(contract, "bid_size", None),
        getattr(contract, "ask_size", None),
    )
    return contract


async def refresh_contract_quotes_if_enabled(
    contract: "OptionContract",
    broker_adapter: Optional["BrokerAdapter"],
    *,
    warm_chain: Optional[Dict[str, Any]] = None,
    warm_chain_age_sec: Optional[float] = None,
    options_chain_manager: Any = None,
) -> "OptionContract":
    """
    Refresh bid/ask/volume/OI/bid_size/ask_size before Liquidity Guard.

    Prefer select-time warm_chain / OptionsChainManager cache when age ≤
    LIVE_QUOTE_REFRESH_CACHE_MAX_AGE_SEC (default 5s) and the matched leg has
    complete fields. Force a broker chain fetch only when reuse is unavailable.
    """
    if not _cfg_bool("LIVE_REFRESH_QUOTES_BEFORE_SUBMIT", True):
        return contract

    max_age = _cfg_float("LIVE_QUOTE_REFRESH_CACHE_MAX_AGE_SEC", 5.0)
    if max_age < 0:
        max_age = 0.0

    # 1) Explicit warm handoff from select/execute path
    reused = _try_refresh_from_chain(
        contract,
        warm_chain,
        source="warm_handoff",
        cache_age_sec=float(warm_chain_age_sec if warm_chain_age_sec is not None else 0.0),
        max_age_sec=max_age,
        require_complete=True,
    )
    if reused is not None:
        return reused

    # 2) OptionsChainManager in-process cache (select / pc_oi just warmed it)
    if options_chain_manager is not None and hasattr(
        options_chain_manager, "peek_cached_chain"
    ):
        try:
            peeked_chain, peeked_age = options_chain_manager.peek_cached_chain(
                getattr(contract, "symbol", ""),
                getattr(contract, "expiry", None),
                max_age_sec=max_age,
            )
        except Exception as exc:
            log.debug(
                "LIVE_ENTRY_QUOTE_REFRESH_PEEK_FAILED | symbol=%s | error=%s",
                getattr(contract, "symbol", ""),
                exc,
            )
            peeked_chain, peeked_age = None, None
        if peeked_chain is not None and peeked_age is not None:
            reused = _try_refresh_from_chain(
                contract,
                peeked_chain,
                source="manager_cache",
                cache_age_sec=float(peeked_age),
                max_age_sec=max_age,
                require_complete=True,
            )
            if reused is not None:
                return reused

    # 3) The admit already priced this strike. A second chain fetch changes the
    # quote and sits between selection and the order. Use the selected bid/ask
    # when book sizes are already on the contract. If sizes are missing, the
    # chain snapshot often omitted bidSize/askSize — hydrate from Market Quote.
    try:
        _sel_bid = float(getattr(contract, "bid", 0.0) or 0.0)
        _sel_ask = float(getattr(contract, "ask", 0.0) or 0.0)
    except (TypeError, ValueError):
        _sel_bid = _sel_ask = 0.0
    _sizes_ok = (
        _optional_int(getattr(contract, "ask_size", None)) is not None
        and _optional_int(getattr(contract, "bid_size", None)) is not None
    )
    if _sel_bid > 0.0 and _sel_ask > 0.0 and _sizes_ok:
        log.info(
            "LIVE_ENTRY_QUOTE_REFRESH | action=use_selected | symbol=%s | strike=%s | bid=%.4f | ask=%.4f",
            getattr(contract, "symbol", ""),
            getattr(contract, "strike", ""),
            _sel_bid,
            _sel_ask,
        )
        return contract
    if _sel_bid > 0.0 and _sel_ask > 0.0 and not _sizes_ok:
        sized = await _hydrate_entry_book_sizes(contract, broker_adapter)
        if sized is not None:
            return sized

    # Force broker fetch only when the selected contract has no bid/ask.
    # Prefer OptionsChainManager (same-day→nearest) over raw SelectedED chain.
    expiry = str(getattr(contract, "expiry", "") or "").replace("-", "")
    chain = None
    from easy0DTE.modules.options_chain_manager import fetch_0dte_intent_chain

    chain = await fetch_0dte_intent_chain(
        options_chain_manager,
        getattr(contract, "symbol", ""),
        expiry=expiry or None,
        use_cache=True,
    )
    if not chain:
        return contract

    leg = _find_matching_leg(chain, contract)
    if leg is not None:
        return _apply_matched_leg_refresh(
            contract,
            leg,
            source="broker_chain",
            quote_age_seconds=0.0,
        )

    log.warning(
        "LIVE_ENTRY_QUOTE_REFRESH_MISS | symbol=%s | strike=%s | type=%s",
        contract.symbol,
        contract.strike,
        contract.option_type,
    )
    return contract
