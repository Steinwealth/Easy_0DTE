"""Premium Engine — thin orchestration over existing 0DTE pricing behavior.

Owns quote resolution for the held contract, STC executable premium (bid),
PnL, HWM/LWM/MFE/MAE ratchets, and degraded classification. Returns an
immutable PremiumSnapshot plus a PremiumStateUpdate patch.

Does NOT:
  - reconcile broker positions
  - create/delete registry rows
  - decide SELL/HOLD
  - submit closes or send alerts

Live writes of the patch must go through BrokerPositionSynchronizer.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

from modules.option_executable_pricing import (
    long_option_sell_to_close_price,
    true_to_live_executable_marks_enabled,
)
from modules.position_registry_contract import (
    HWM_RATCHET_TRUSTED_SOURCES,
    PremiumEvaluationRequest,
    PremiumSnapshot,
    PremiumStateUpdate,
    QuoteContext,
    utc_now,
)
from modules.unified_0dte_premium_bucket import classify_0dte_monitor_premium_bucket

log = logging.getLogger(__name__)

_DEGRADED_BUCKETS = frozenset(
    {
        "modeled_proxy",
        "current_position_value_fallback",
        "missing_or_invalid",
    }
)


def _positive(value: Any) -> Optional[float]:
    try:
        v = float(value)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def _nested(obj: Any, key: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _position_id(position: Any) -> str:
    if isinstance(position, dict):
        return str(position.get("position_id") or "")
    return str(getattr(position, "position_id", "") or "")


def _position_qty(position: Any) -> int:
    if isinstance(position, dict):
        return int(position.get("quantity") or 0)
    return int(getattr(position, "quantity", 0) or 0)


def _position_entry(position: Any) -> float:
    if isinstance(position, dict):
        return float(position.get("average_fill") or position.get("entry_price") or 0.0)
    avg = getattr(position, "average_fill", None)
    if avg is not None:
        try:
            v = float(avg)
            if v > 0:
                return v
        except (TypeError, ValueError):
            pass
    return float(getattr(position, "entry_price", 0.0) or 0.0)


def _position_type(position: Any, ctx: QuoteContext) -> str:
    pt = str(ctx.position_type or "").strip().lower()
    if pt:
        return pt
    if isinstance(position, dict):
        return str(position.get("position_type") or "lotto").lower()
    raw = str(getattr(position, "position_type", "") or "lotto").lower()
    if raw in ("debit_spread", "credit_spread", "lotto"):
        return raw
    return "lotto"


def _contract_symbol(position: Any) -> str:
    if isinstance(position, dict):
        return str(
            position.get("occ_contract_symbol")
            or position.get("contract_symbol")
            or position.get("occ_symbol")
            or ""
        )
    occ = str(getattr(position, "occ_contract_symbol", "") or "")
    if occ:
        return occ
    md = getattr(position, "metadata", None)
    if isinstance(md, dict):
        for key in ("occ_symbol", "option_symbol", "contract_symbol"):
            v = md.get(key)
            if v:
                return str(v)
    try:
        from modules.broker_live_audits import _position_contract_key

        _, contract = _position_contract_key(position)
        return str(contract or "")
    except Exception:
        return ""


def _prior_premium_state(position: Any, ctx: QuoteContext) -> Dict[str, Any]:
    md = getattr(position, "metadata", None) if not isinstance(position, dict) else position
    meta = md if isinstance(md, dict) else {}
    osnap = meta.get("option_stealth") if isinstance(meta.get("option_stealth"), dict) else {}
    return {
        "hwm": _positive(ctx.prior_hwm)
        or _positive(meta.get("premium_hwm"))
        or _positive(osnap.get("premium_hwm"))
        or _positive(getattr(position, "premium_hwm", None)),
        "lwm": _positive(ctx.prior_lwm)
        or _positive(meta.get("premium_lwm"))
        or _positive(osnap.get("premium_lwm"))
        or _positive(getattr(position, "premium_lwm", None)),
        "mfe": ctx.prior_mfe_pct
        if ctx.prior_mfe_pct is not None
        else (
            meta.get("mfe_pct")
            if meta.get("mfe_pct") is not None
            else getattr(position, "mfe_pct", None)
        ),
        "mae": ctx.prior_mae_pct
        if ctx.prior_mae_pct is not None
        else (
            meta.get("mae_pct")
            if meta.get("mae_pct") is not None
            else getattr(position, "mae_pct", None)
        ),
        "last_reliable": _positive(ctx.last_reliable_premium)
        or _positive(meta.get("last_reliable_premium"))
        or _positive(osnap.get("last_reliable_premium")),
        "degraded_since": ctx.degraded_since or meta.get("degraded_since"),
    }


def extract_leg_quote_from_chain(
    contracts: Any,
    target_strike: float,
) -> Dict[str, Any]:
    """Mirror OptionsTradingExecutor strike match → bid/ask/mid/last."""
    if not isinstance(contracts, list) or target_strike is None:
        return {"source": "missing_or_invalid"}
    exact = None
    nearest = None
    nearest_diff = None
    for contract in contracts:
        try:
            c_strike = float(_nested(contract, "strike", 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        diff = abs(c_strike - float(target_strike))
        if diff < 0.01:
            exact = contract
            break
        if nearest is None or diff < float(nearest_diff or 1e9):
            nearest = contract
            nearest_diff = diff
    chosen = exact
    if chosen is None and nearest is not None and float(nearest_diff or 999.0) <= 0.5:
        chosen = nearest
        source = "nearest_strike"
    elif chosen is not None:
        # Strike equality on a chain slice is NOT exact-contract identity.
        source = "chain_strike_match"
    else:
        return {"source": "missing_or_invalid"}

    bid = _positive(_nested(chosen, "bid", 0.0))
    ask = _positive(_nested(chosen, "ask", 0.0))
    mid = _positive(_nested(chosen, "mid_price", 0.0))
    if mid is None and bid is not None and ask is not None:
        mid = (bid + ask) / 2.0
    last = _positive(_nested(chosen, "last", 0.0))
    return {
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "last": last,
        "source": source,
        "quote_source": source,
        "has_position_value": False,
        "contract_identity_matched": False,
        "contract_matched": False,
    }


def resolve_quote_fields(ctx: QuoteContext) -> Dict[str, Any]:
    """Resolve bid/ask/mark/last + source from option_quote or chain."""
    oq = dict(ctx.option_quote or {}) if isinstance(ctx.option_quote, dict) else {}
    bid = _positive(oq.get("bid"))
    ask = _positive(oq.get("ask"))
    mid = _positive(oq.get("mid") or oq.get("mark") or oq.get("mid_price"))
    last = _positive(oq.get("last"))
    source = str(oq.get("source") or oq.get("quote_source") or "").strip()
    try:
        from modules.exit_grade_durability import canonicalize_quote_source

        source = canonicalize_quote_source(source)
    except Exception:
        if source.lower() == "exact":
            source = "chain_strike_match"

    if bid is None and ask is None and mid is None and last is None and isinstance(ctx.chain, dict):
        otype = str(ctx.option_type or "").strip().lower()
        key = "calls" if otype.startswith("c") else "puts" if otype.startswith("p") else ""
        contracts = ctx.chain.get(key) if key else None
        if isinstance(contracts, list) and ctx.target_strike is not None:
            resolved = extract_leg_quote_from_chain(contracts, float(ctx.target_strike))
            bid = resolved.get("bid")
            ask = resolved.get("ask")
            mid = resolved.get("mid")
            last = resolved.get("last")
            source = str(resolved.get("source") or source or "missing_or_invalid")
            oq = {**oq, **{k: v for k, v in resolved.items() if v is not None}}

    if not source:
        if bid is not None and ask is not None:
            source = "unverified_quote"
        elif last is not None:
            source = "option_last"
        elif mid is not None:
            source = "option_mid"
        else:
            source = "missing_or_invalid"

    if mid is None and bid is not None and ask is not None:
        mid = (bid + ask) / 2.0

    mark = mid  # diagnostic
    return {
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "mark": mark,
        "last": last,
        "source": source,
        "oq_orb": oq,
    }


def quote_allows_hwm_ratchet(
    *,
    source: str,
    bid: Optional[float],
    ask: Optional[float],
    context_allow: bool = True,
) -> bool:
    """HWM / favorable MFE may rise only on trustworthy STC-capable quotes.

    Denied: synthetic, stale, mark-only, ask-only, zero/missing bid, crossed market.
    """
    if not context_allow:
        return False
    src = str(source or "").strip().lower()
    try:
        from modules.exit_grade_durability import canonicalize_quote_source

        src = canonicalize_quote_source(src).strip().lower()
    except Exception:
        if src == "exact":
            src = "chain_strike_match"
    if not src or src not in HWM_RATCHET_TRUSTED_SOURCES:
        return False
    if "stale" in src or "synthetic" in src or "proxy" in src or "modeled" in src:
        return False
    try:
        b = float(bid) if bid is not None else None
    except (TypeError, ValueError):
        b = None
    if b is None or b <= 0:
        return False
    try:
        a = float(ask) if ask is not None else None
    except (TypeError, ValueError):
        a = None
    if a is not None and a > 0 and b > a:
        return False  # crossed
    return True


def compute_executable_exit_price(*, bid, ask, mid, last) -> Optional[float]:
    """Long CALL/PUT STC = bid-only for true-to-live exits when synthetic prohibited."""
    try:
        from modules.dte0_exit_monitor_config import synthetic_exit_quotes_allowed

        bid_only = not synthetic_exit_quotes_allowed()
    except Exception:
        bid_only = True
    px = float(
        long_option_sell_to_close_price(
            bid=bid, ask=ask, mid=mid, last=last, bid_only=bid_only
        )
    )
    if bid is None and ask is None and mid is None and last is None:
        return None
    if bid_only and (bid is None or float(bid) <= 0):
        return None
    return px if px > 0 else None


def compute_unrealized_pnl(
    *,
    position_type: str,
    entry: float,
    executable: float,
    quantity: int,
) -> Tuple[Optional[float], Optional[float]]:
    """Return (net dollars, liquidatable pct). Long debit/lotto: STC bid vs entry fill.

    Dollars include modeled open+close fees (true-to-live bookkeeping).
    Pct stays premium fill-gross so Profit Capture HWM/stop math is bid vs entry.
    """
    if entry <= 0 or quantity <= 0 or executable is None or executable <= 0:
        return None, None
    pt = str(position_type or "lotto").lower()
    is_credit = pt == "credit_spread"
    try:
        from modules.options_trade_economics import net_result_pnl_dollars

        dollars = net_result_pnl_dollars(
            entry_fill=float(entry),
            exit_fill=float(executable),
            quantity=int(quantity),
            is_credit_spread=is_credit,
            position_type=pt,
        )
    except Exception:
        if is_credit:
            dollars = (entry - executable) * quantity * 100.0
        else:
            dollars = (executable - entry) * quantity * 100.0
    if is_credit:
        pct = (entry - executable) / entry
    else:
        pct = (executable - entry) / entry
    return float(dollars), float(pct)


def ratchet_premium_extremes(
    *,
    executable: Optional[float],
    entry: float,
    pnl_pct: Optional[float],
    prior: Dict[str, Any],
    allow_hwm: bool,
) -> Dict[str, Optional[float]]:
    """Ratchet HWM up (gated), LWM down (always), MFE/MAE from pnl_pct."""
    hwm = prior.get("hwm")
    lwm = prior.get("lwm")
    mfe = prior.get("mfe")
    mae = prior.get("mae")

    if executable is not None and executable > 0:
        if hwm is None:
            hwm = float(executable)
        elif allow_hwm and float(executable) > float(hwm):
            hwm = float(executable)
        if lwm is None:
            lwm = float(executable)
        elif float(executable) < float(lwm):
            lwm = float(executable)

    if pnl_pct is not None:
        try:
            p = float(pnl_pct)
        except (TypeError, ValueError):
            p = None
        if p is not None:
            if allow_hwm:
                if mfe is None or p > float(mfe):
                    mfe = p
            if mae is None or p < float(mae):
                mae = p
    elif entry > 0 and lwm is not None:
        # Reconstruct MAE from LWM when no live pct yet
        try:
            mae = (float(lwm) - float(entry)) / float(entry)
        except ZeroDivisionError:
            pass

    return {"hwm": hwm, "lwm": lwm, "mfe": mfe, "mae": mae}


def snapshot(
    position: Any = None,
    chain: Any = None,
    now: Optional[datetime] = None,
    *,
    request: Optional[PremiumEvaluationRequest] = None,
    quote_context: Optional[QuoteContext] = None,
) -> Tuple[PremiumSnapshot, PremiumStateUpdate]:
    """Build frozen PremiumSnapshot + patch. Prefer ``request=``; other args for shorthand."""
    if request is not None:
        position = request.position
        quote_context = request.quote_context
        now = request.evaluated_at
    if quote_context is None:
        quote_context = QuoteContext(chain=chain if isinstance(chain, dict) else None)
    elif chain is not None and quote_context.chain is None:
        quote_context = QuoteContext(
            option_quote=quote_context.option_quote,
            chain=chain if isinstance(chain, dict) else quote_context.chain,
            target_strike=quote_context.target_strike,
            option_type=quote_context.option_type,
            position_type=quote_context.position_type,
            prior_hwm=quote_context.prior_hwm,
            prior_lwm=quote_context.prior_lwm,
            prior_mfe_pct=quote_context.prior_mfe_pct,
            prior_mae_pct=quote_context.prior_mae_pct,
            allow_hwm_ratchet=quote_context.allow_hwm_ratchet,
            last_reliable_premium=quote_context.last_reliable_premium,
            degraded_since=quote_context.degraded_since,
            quote_timestamp=quote_context.quote_timestamp,
        )
    evaluated_at = now or utc_now()
    if evaluated_at.tzinfo is None:
        evaluated_at = evaluated_at.replace(tzinfo=timezone.utc)

    pid = _position_id(position)
    qty = max(0, _position_qty(position))
    entry = _position_entry(position)
    ptype = _position_type(position, quote_context)
    contract = _contract_symbol(position)
    prior = _prior_premium_state(position, quote_context)

    fields = resolve_quote_fields(quote_context)
    bid, ask, mark, last = fields["bid"], fields["ask"], fields["mark"], fields["last"]
    source = fields["source"]

    try:
        from modules.dte0_exit_monitor_config import (
            exit_grade_source_allowed,
            liquidatable_metrics,
            synthetic_exit_quotes_allowed,
        )

        allow_synth = synthetic_exit_quotes_allowed()
        source_ok = exit_grade_source_allowed(str(source or ""))
    except Exception:
        allow_synth = False
        source_ok = "synthetic" not in str(source or "").lower()

        def liquidatable_metrics(**kwargs):  # type: ignore
            return None, kwargs.get("prior_hwm_bid"), None, None, False, False

    # Prefer exact-contract quotes; never treat synthetic as executable
    if not allow_synth and not source_ok:
        executable = None
        bid_for_exit = None
    else:
        bid_for_exit = bid
        executable = compute_executable_exit_price(bid=bid, ask=ask, mid=mark, last=last)

    dollars, pct = compute_unrealized_pnl(
        position_type=ptype,
        entry=entry,
        executable=float(executable) if executable is not None else 0.0,
        quantity=qty,
    )
    if executable is None:
        dollars, pct = None, None

    # HWM / MFE: bid-only liquidatable series (ask/mark spikes cannot raise HWM)
    allow_hwm = quote_allows_hwm_ratchet(
        source=str(source or ""),
        bid=bid_for_exit,
        ask=ask,
        context_allow=bool(quote_context.allow_hwm_ratchet) and bool(source_ok),
    )
    if allow_hwm and bid_for_exit is not None:
        extremes = ratchet_premium_extremes(
            executable=float(bid_for_exit),
            entry=entry,
            pnl_pct=pct,
            prior=prior,
            allow_hwm=True,
        )
    else:
        extremes = ratchet_premium_extremes(
            executable=None,
            entry=entry,
            pnl_pct=None,
            prior=prior,
            allow_hwm=False,
        )

    liq_pnl, liq_hwm, liq_mfe, giveback, armed, triggered = liquidatable_metrics(
        entry_fill_price=float(entry),
        current_bid=bid_for_exit,
        prior_hwm_bid=prior.get("hwm") if isinstance(prior.get("hwm"), (int, float)) else extremes.get("hwm"),
        arm_pct=10.0,
        giveback_pct=7.0,
    )
    if liq_hwm is not None:
        extremes["hwm"] = float(liq_hwm)
    if liq_mfe is not None:
        # Store fractional MFE for stealth compatibility (legacy max_pnl_pct is fraction)
        extremes["mfe"] = float(liq_mfe) / 100.0

    # Analytical mark MFE (display only — never controls exits)
    analytical_mark_mfe = None
    if mark is not None and entry > 0:
        prior_mark_mfe = None
        try:
            prior_mark_mfe = float((quote_context.prior_mfe_pct or 0.0))
        except (TypeError, ValueError):
            prior_mark_mfe = None
        mark_pnl = (float(mark) / entry - 1.0)
        analytical_mark_mfe = max(float(prior_mark_mfe or 0.0), mark_pnl) if mark_pnl > 0 else prior_mark_mfe

    bucket = classify_0dte_monitor_premium_bucket(
        source=source,
        osnap={},
        oq_orb=fields.get("oq_orb") or {},
    )
    if not source_ok:
        bucket = "missing_or_invalid"
    degraded = bucket in _DEGRADED_BUCKETS or executable is None or not source_ok
    degradation_reason = bucket if degraded else None
    if executable is None and not degradation_reason:
        degradation_reason = "pricing_unavailable"
    exit_grade = bool(not degraded and source_ok and bid_for_exit is not None and float(bid_for_exit) > 0)

    quote_ts = quote_context.quote_timestamp or evaluated_at
    age = None
    try:
        age = max(0.0, (evaluated_at - quote_ts).total_seconds())
    except Exception:
        age = None

    entry_notional = float(entry) * float(qty) * 100.0 if entry > 0 and qty > 0 else None
    current_pos_value = float(executable) * float(qty) * 100.0 if executable and qty > 0 else None

    degraded_since = prior.get("degraded_since")
    if degraded:
        if not degraded_since:
            degraded_since = evaluated_at
    else:
        degraded_since = None

    last_reliable = prior.get("last_reliable")
    if executable is not None and not degraded:
        last_reliable = float(executable)

    data_quality = "trusted" if not degraded else "degraded"
    diagnostics = {
        "true_to_live_executable_marks": bool(true_to_live_executable_marks_enabled()),
        "position_type": ptype,
        "allow_hwm_ratchet": bool(allow_hwm),
        "allow_hwm_ratchet_context": bool(quote_context.allow_hwm_ratchet),
        "premium_bucket": bucket,
        "liquidatable_bid_mfe_pct": liq_mfe,
        "analytical_mark_mfe_pct": (
            float(analytical_mark_mfe) * 100.0 if analytical_mark_mfe is not None else None
        ),
        "mfe_giveback_armed": bool(armed),
        "mfe_giveback_triggered": bool(triggered),
        "exit_grade": bool(exit_grade),
        "hwm_price_field": "bid",
        "delta": (quote_context.option_quote or {}).get("delta")
        if isinstance(quote_context.option_quote, dict)
        else None,
        "gamma": (quote_context.option_quote or {}).get("gamma")
        if isinstance(quote_context.option_quote, dict)
        else None,
        "theta": (quote_context.option_quote or {}).get("theta")
        if isinstance(quote_context.option_quote, dict)
        else None,
        "vega": (quote_context.option_quote or {}).get("vega")
        if isinstance(quote_context.option_quote, dict)
        else None,
        "implied_volatility": (
            (quote_context.option_quote or {}).get("implied_volatility")
            or (quote_context.option_quote or {}).get("iv")
        )
        if isinstance(quote_context.option_quote, dict)
        else None,
    }

    snap = PremiumSnapshot(
        position_id=pid,
        contract_symbol=contract,
        quantity=qty,
        entry_fill_price=float(entry),
        executable_exit_price=executable,
        bid=bid,
        ask=ask,
        mark=mark,
        current_value=float(executable) if executable is not None else None,
        unrealized_pnl_dollars=dollars,
        unrealized_pnl_pct=pct,
        premium_hwm=extremes["hwm"],
        premium_lwm=extremes["lwm"],
        mfe_pct=float(extremes["mfe"]) if extremes["mfe"] is not None else None,
        mae_pct=float(extremes["mae"]) if extremes["mae"] is not None else None,
        quote_source=source,
        quote_timestamp=quote_ts,
        quote_age_seconds=age,
        degraded=bool(degraded),
        degradation_reason=degradation_reason,
        entry_notional=entry_notional,
        last=last,
        current_position_value=current_pos_value,
        premium_bucket=bucket,
        data_quality=data_quality,
        diagnostics=diagnostics,
        current_bid=bid,
        current_ask=ask,
        current_mark=mark,
        liquidatable_pnl_pct=liq_pnl,
        liquidatable_hwm_bid=liq_hwm,
        liquidatable_mfe_pct=liq_mfe,
        giveback_from_hwm_pct=giveback,
        analytical_mark_mfe_pct=(
            float(analytical_mark_mfe) * 100.0 if analytical_mark_mfe is not None else None
        ),
        mfe_giveback_armed=bool(armed),
        mfe_giveback_triggered=bool(triggered),
        exit_grade=bool(exit_grade),
    )

    patch = PremiumStateUpdate(
        position_id=pid,
        premium_hwm=snap.premium_hwm,
        premium_lwm=snap.premium_lwm,
        mfe_pct=snap.mfe_pct,
        mae_pct=snap.mae_pct,
        last_reliable_premium=float(last_reliable) if last_reliable is not None else None,
        last_quote_timestamp=quote_ts,
        premium_health=data_quality,
        degraded_since=degraded_since if isinstance(degraded_since, datetime) else None,
        current_position_value=snap.current_value,
        unrealized_pnl_dollars=dollars,
        unrealized_pnl_pct=pct,
    )
    return snap, patch


def evaluate(request: PremiumEvaluationRequest) -> Tuple[PremiumSnapshot, PremiumStateUpdate]:
    """Primary API: ``snapshot, patch = premium_engine.evaluate(request)``."""
    return snapshot(request=request)


def unavailable_snapshot(
    position: Any,
    *,
    evaluated_at: Optional[datetime] = None,
    reason: str = "pricing_unavailable",
) -> Tuple[PremiumSnapshot, PremiumStateUpdate]:
    """Structured diagnostics when quotes cannot be resolved."""
    now = evaluated_at or utc_now()
    ctx = QuoteContext(option_quote={"source": "missing_or_invalid"})
    return snapshot(position=position, quote_context=ctx, now=now)


__all__ = [
    "QuoteContext",
    "PremiumEvaluationRequest",
    "snapshot",
    "evaluate",
    "unavailable_snapshot",
    "compute_executable_exit_price",
    "compute_unrealized_pnl",
    "resolve_quote_fields",
    "extract_leg_quote_from_chain",
]
