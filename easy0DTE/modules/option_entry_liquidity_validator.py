"""
Shared 0DTE option entry liquidity / execution-safety validator (ORB + Trendline).

Final contract eligibility before execution uses canonical policy from:
  0DTE_ENTRY_MIN_VOLUME, 0DTE_ENTRY_MIN_OPEN_INTEREST,
  0DTE_MIN_ENTRY_PREMIUM, 0DTE_MAX_ENTRY_PREMIUM

Path-specific spread caps remain owned by spread_policy (ORB 8% vs Trendline 30%).
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple, Union

log = logging.getLogger(__name__)

# Canonical reason codes (Telegram / ledger / diagnostics)
REASON_NO_BID = "option_no_bid"
REASON_NO_ASK = "option_no_ask"
REASON_SPREAD_TOO_WIDE = "option_spread_too_wide"
REASON_LOW_VOLUME = "option_low_volume"
REASON_LOW_OPEN_INTEREST = "low_open_interest"
REASON_QUOTE_STALE = "option_quote_stale"
REASON_PREMIUM_TOO_LOW = "option_premium_too_low"
REASON_PREMIUM_TOO_HIGH = "option_premium_too_high"
REASON_TOO_EXPENSIVE = "option_too_expensive_for_budget"
REASON_LIVE_CHAIN_REQUIRED = "option_live_chain_required"
REASON_LIQUIDITY_FAILED = "option_liquidity_failed"

# Short TTL so select + execute_lotto_sleeve double-check reuse one result when quotes are fresh.
_LIQUIDITY_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_LIQUIDITY_CACHE_TTL_DEFAULT_SEC = 3.0
_LIQUIDITY_CACHE_MAX = 256


def _liquidity_cache_ttl_sec() -> float:
    raw = os.getenv("OPTION_ENTRY_LIQUIDITY_CACHE_TTL_SEC")
    if raw is None or str(raw).strip() == "":
        return _LIQUIDITY_CACHE_TTL_DEFAULT_SEC
    try:
        return max(0.0, float(str(raw).strip()))
    except (TypeError, ValueError):
        return _LIQUIDITY_CACHE_TTL_DEFAULT_SEC


def clear_option_entry_liquidity_cache() -> None:
    _LIQUIDITY_CACHE.clear()


def _liquidity_cache_key(
    contract: Any,
    *,
    path: str,
    max_spread_pct_points: float,
    budget: Optional[float],
    quote_age_seconds: Optional[float],
    chain_source: Optional[str],
    require_live_chain: bool,
    demo_mode: bool,
    min_volume: Optional[int],
    min_open_interest: Optional[int],
    min_premium: Optional[float],
    max_premium: Optional[float],
) -> str:
    fields = _contract_fields(contract)
    return "|".join(
        [
            str(path or "orb"),
            str(fields.get("symbol") or getattr(contract, "symbol", "") or ""),
            str(fields.get("strike") or getattr(contract, "strike", "") or ""),
            str(fields.get("option_type") or getattr(contract, "option_type", "") or ""),
            str(fields.get("expiration") or getattr(contract, "expiration", "") or ""),
            f"{float(fields.get('bid') or 0.0):.4f}",
            f"{float(fields.get('ask') or 0.0):.4f}",
            str(fields.get("volume") if fields.get("volume") is not None else ""),
            str(fields.get("open_interest") if fields.get("open_interest") is not None else ""),
            f"{float(max_spread_pct_points):.4f}",
            f"{float(budget):.2f}" if budget is not None else "",
            f"{float(quote_age_seconds):.1f}" if quote_age_seconds is not None else "",
            str(chain_source or ""),
            "1" if require_live_chain else "0",
            "1" if demo_mode else "0",
            str(min_volume if min_volume is not None else ""),
            str(min_open_interest if min_open_interest is not None else ""),
            f"{float(min_premium):.4f}" if min_premium is not None else "",
            f"{float(max_premium):.4f}" if max_premium is not None else "",
        ]
    )

LEGACY_TO_CANONICAL = {
    "low_volume": REASON_LOW_VOLUME,
    "low_open_interest": REASON_LOW_OPEN_INTEREST,
    "option_low_open_interest": REASON_LOW_OPEN_INTEREST,  # legacy
    "spread_too_wide": REASON_SPREAD_TOO_WIDE,
    "spread_gt_20pct": REASON_SPREAD_TOO_WIDE,
    "quote_stale": REASON_QUOTE_STALE,
    "missing_bid_or_ask": REASON_NO_BID,
    "missing_bid_ask": REASON_NO_BID,
    "no_bid": REASON_NO_BID,
    "contract_too_expensive_for_slot": REASON_TOO_EXPENSIVE,
    "no_affordable_contract_for_slot": REASON_TOO_EXPENSIVE,
    "live_chain_required_unavailable": REASON_LIVE_CHAIN_REQUIRED,
    "volume_below_min": REASON_LOW_VOLUME,
    "open_interest_below_min": REASON_LOW_OPEN_INTEREST,
    "delta_out_of_band": REASON_LIQUIDITY_FAILED,
    # Legacy alias — replaced by side-specific premium reason codes.
    "premium_out_of_band": REASON_LIQUIDITY_FAILED,
    "option_premium_too_high": REASON_PREMIUM_TOO_HIGH,
    "no_contract_in_band": REASON_LIQUIDITY_FAILED,
    "liquidity_or_premium_filters": REASON_LIQUIDITY_FAILED,
    "hard_gate": REASON_LIQUIDITY_FAILED,
    "contract_selection_failed": REASON_LIQUIDITY_FAILED,
}

_ENTRY_MIN_VOLUME_KEY = "0DTE_ENTRY_MIN_VOLUME"
_ENTRY_MIN_OI_KEY = "0DTE_ENTRY_MIN_OPEN_INTEREST"
_ENTRY_MIN_PREMIUM_KEY = "0DTE_ENTRY_MIN_PREMIUM"
_LIVE_MAX_QUOTE_AGE_KEY = "0DTE_LIVE_MAX_QUOTE_AGE_SECONDS"


def _int_env_first(keys: Tuple[str, ...], default: int) -> int:
    for key in keys:
        raw = os.getenv(key)
        if raw is None or str(raw).strip() == "":
            continue
        try:
            return int(float(str(raw).strip()))
        except (TypeError, ValueError):
            continue
    return int(default)


def _float_env_first(keys: Tuple[str, ...], default: float) -> float:
    for key in keys:
        raw = os.getenv(key)
        if raw is None or str(raw).strip() == "":
            continue
        try:
            return float(str(raw).strip())
        except (TypeError, ValueError):
            continue
    return float(default)


def _bool_env(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def entry_min_volume() -> int:
    return max(0, _int_env_first(
        (
            _ENTRY_MIN_VOLUME_KEY,
            "TRENDLINE_OPTION_MIN_VOLUME",
            "0DTE_MIN_VOLUME",
            "0DTE_OPTIONS_MIN_VOLUME",
        ),
        50,
    ))


def entry_min_open_interest() -> int:
    return max(0, _int_env_first(
        (
            _ENTRY_MIN_OI_KEY,
            "TRENDLINE_OPTION_MIN_OPEN_INTEREST",
            "0DTE_MIN_OPEN_INTEREST",
            "0DTE_OPTIONS_MIN_OPEN_INTEREST",
        ),
        100,
    ))


def entry_min_premium() -> float:
    return max(0.0, _float_env_first(
        (
            "0DTE_MIN_ENTRY_PREMIUM",
            _ENTRY_MIN_PREMIUM_KEY,
            "0DTE_SINGLE_LEG_DIRECTIONAL_PREMIUM_MIN",
            "TRENDLINE_OPTION_MIN_PREMIUM",
        ),
        0.03,
    ))


def entry_max_premium() -> float:
    """Per-share cap for all 0DTE single-leg entries. 0 disables the cap."""
    return max(0.0, _float_env_first(("0DTE_MAX_ENTRY_PREMIUM",), 0.0))


def live_max_quote_age_seconds() -> float:
    return max(0.0, _float_env_first(
        (
            _LIVE_MAX_QUOTE_AGE_KEY,
            "ORB_0DTE_LIVE_MAX_QUOTE_AGE_SECONDS",
        ),
        10.0,
    ))


def canonical_reason_code(raw: Optional[str]) -> str:
    code = str(raw or "").strip().lower()
    if not code:
        return REASON_LIQUIDITY_FAILED
    mapped = LEGACY_TO_CANONICAL.get(code)
    if mapped:
        return mapped
    if code.startswith("option_"):
        return code
    return code


_DELTA_ABSENT = object()


def _positive_float(value: Any) -> Optional[float]:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")) or v <= 0:
        return None
    return v


def _int_field(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return default


def _contract_fields(contract: Any) -> Dict[str, Any]:
    """Normalize OptionContract, chain dict, or _ContractPick into quote fields."""
    if isinstance(contract, dict):
        bid = contract.get("bid")
        ask = contract.get("ask")
        mid = contract.get("mid") or contract.get("mid_price")
        last = contract.get("last")
        vol = contract.get("volume")
        oi = contract.get("open_interest") or contract.get("openInterest")
        spread_pct = contract.get("bid_ask_spread_pct") or contract.get("spread_pct")
        quote_age = contract.get("quote_age_seconds")
        premium = contract.get("premium") or contract.get("estimated_contract_price")
        if premium is None and ask is not None:
            premium = ask
        fields = {
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "last": last,
            "volume": vol,
            "open_interest": oi,
            "spread_pct": spread_pct,
            "quote_age_seconds": quote_age,
            "premium": premium,
        }
        if "delta" in contract:
            fields["delta"] = contract.get("delta")
        return _apply_pick_bid_ask_preference(fields, contract)

    bid = getattr(contract, "bid", None)
    ask = getattr(contract, "ask", None)
    mid = getattr(contract, "mid_price", None) or getattr(contract, "mid", None)
    last = getattr(contract, "last", None)
    vol = getattr(contract, "volume", None)
    oi = getattr(contract, "open_interest", None)
    spread_pct = getattr(contract, "bid_ask_spread_pct", None)
    quote_age = getattr(contract, "quote_age_seconds", None)
    delta = getattr(contract, "delta", _DELTA_ABSENT)
    premium = getattr(contract, "premium", None)
    if premium is None:
        try:
            from modules.option_executable_pricing import long_option_buy_to_open_price

            premium = long_option_buy_to_open_price(
                bid=_positive_float(bid),
                ask=_positive_float(ask),
                mid=_positive_float(mid),
                last=_positive_float(last),
            )
        except Exception:
            premium = ask if _positive_float(ask) else mid
    fields = {
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "last": last,
        "volume": vol,
        "open_interest": oi,
        "spread_pct": spread_pct,
        "quote_age_seconds": quote_age,
        "premium": premium,
    }
    if delta is not _DELTA_ABSENT:
        fields["delta"] = delta
    return _apply_pick_bid_ask_preference(fields, contract)


def _apply_pick_bid_ask_preference(fields: Dict[str, Any], contract: Any) -> Dict[str, Any]:
    """Prefer pick.bid/pick.ask (or stored entry quotes) before synthetic premium spread."""
    bid = _positive_float(fields.get("bid"))
    ask = _positive_float(fields.get("ask"))
    if bid is not None and ask is not None:
        fields["bid"] = bid
        fields["ask"] = ask
        return fields

    pick = getattr(contract, "pick", None)
    if pick is not None:
        pb = _positive_float(getattr(pick, "bid", None))
        pa = _positive_float(getattr(pick, "ask", None))
        if pb is not None and pa is not None:
            fields["bid"] = pb
            fields["ask"] = pa
            return fields

    for bk, ak in (
        ("entry_bid", "entry_ask"),
        ("pick_bid", "pick_ask"),
    ):
        pb = _positive_float(getattr(contract, bk, None) if not isinstance(contract, dict) else contract.get(bk))
        pa = _positive_float(getattr(contract, ak, None) if not isinstance(contract, dict) else contract.get(ak))
        if pb is not None and pa is not None:
            fields["bid"] = pb
            fields["ask"] = pa
            return fields

    # Do not synthesize bid/ask here — entry gates require live quotes; monitor uses orb_monitor_quote_resolution.
    return fields


def _spread_pct_points(
    bid: float,
    ask: float,
    mid: Optional[float],
    spread_pct_attr: Optional[float],
) -> float:
    if spread_pct_attr is not None:
        try:
            return float(spread_pct_attr)
        except (TypeError, ValueError):
            pass
    if bid > 0 and ask > 0:
        m = mid if mid and mid > 0 else (bid + ask) / 2.0
        if m > 0:
            return (ask - bid) / m * 100.0
    return 0.0


def should_enforce_quote_age(*, demo_mode: bool, require_live_chain: bool) -> bool:
    """LIVE always; DEMO when live chain required or true-to-live executable marks."""
    if not demo_mode:
        return True
    if require_live_chain:
        return True
    raw = os.getenv("OPTION_TRUE_TO_LIVE_EXECUTABLE_MARKS")
    if raw is None or str(raw).strip() == "":
        raw = os.getenv("OPTION_STEALTH_0DTE_TRUE_TO_LIVE_EXECUTABLE_MARKS")
    if raw is None or str(raw).strip() == "":
        return True
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def validate_option_entry_liquidity(
    contract: Any,
    *,
    path: str = "orb",
    max_spread_pct_points: float,
    budget: Optional[float] = None,
    quote_age_seconds: Optional[float] = None,
    chain_source: Optional[str] = None,
    require_live_chain: bool = False,
    demo_mode: bool = True,
    synthetic_allowed: bool = False,
    min_volume: Optional[int] = None,
    min_open_interest: Optional[int] = None,
    min_premium: Optional[float] = None,
    max_premium: Optional[float] = None,
    enforce_quote_age: Optional[bool] = None,
    require_quote_fields: bool = True,
) -> Dict[str, Any]:
    """
    Shared final execution-safety validator for ORB and Trendline 0DTE entries.

    max_spread_pct_points: percent points (8.0 = 8%), matches OptionContract.bid_ask_spread_pct.
    Short TTL cache collapses select + submit double-checks when quotes are unchanged.
    """
    ttl = _liquidity_cache_ttl_sec()
    cache_key = ""
    if ttl > 0:
        cache_key = _liquidity_cache_key(
            contract,
            path=path,
            max_spread_pct_points=max_spread_pct_points,
            budget=budget,
            quote_age_seconds=quote_age_seconds,
            chain_source=chain_source,
            require_live_chain=require_live_chain,
            demo_mode=demo_mode,
            min_volume=min_volume,
            min_open_interest=min_open_interest,
            min_premium=min_premium,
            max_premium=max_premium,
        )
        hit = _LIQUIDITY_CACHE.get(cache_key)
        if hit is not None:
            ts, cached = hit
            if (time.monotonic() - ts) <= ttl:
                out = dict(cached)
                metrics = dict(out.get("metrics") or {})
                metrics["liquidity_cache_hit"] = True
                out["metrics"] = metrics
                return out
            _LIQUIDITY_CACHE.pop(cache_key, None)

    result = _validate_option_entry_liquidity_uncached(
        contract,
        path=path,
        max_spread_pct_points=max_spread_pct_points,
        budget=budget,
        quote_age_seconds=quote_age_seconds,
        chain_source=chain_source,
        require_live_chain=require_live_chain,
        demo_mode=demo_mode,
        synthetic_allowed=synthetic_allowed,
        min_volume=min_volume,
        min_open_interest=min_open_interest,
        min_premium=min_premium,
        max_premium=max_premium,
        enforce_quote_age=enforce_quote_age,
        require_quote_fields=require_quote_fields,
    )
    if ttl > 0 and cache_key:
        if len(_LIQUIDITY_CACHE) >= _LIQUIDITY_CACHE_MAX:
            # Drop oldest roughly by insertion order (CPython 3.7+).
            oldest = next(iter(_LIQUIDITY_CACHE), None)
            if oldest is not None:
                _LIQUIDITY_CACHE.pop(oldest, None)
        store = dict(result)
        metrics = dict(store.get("metrics") or {})
        metrics["liquidity_cache_hit"] = False
        store["metrics"] = metrics
        _LIQUIDITY_CACHE[cache_key] = (time.monotonic(), store)
    return result


def _validate_option_entry_liquidity_uncached(
    contract: Any,
    *,
    path: str = "orb",
    max_spread_pct_points: float,
    budget: Optional[float] = None,
    quote_age_seconds: Optional[float] = None,
    chain_source: Optional[str] = None,
    require_live_chain: bool = False,
    demo_mode: bool = True,
    synthetic_allowed: bool = False,
    min_volume: Optional[int] = None,
    min_open_interest: Optional[int] = None,
    min_premium: Optional[float] = None,
    max_premium: Optional[float] = None,
    enforce_quote_age: Optional[bool] = None,
    require_quote_fields: bool = True,
) -> Dict[str, Any]:
    fields = _contract_fields(contract)
    bid_f = _positive_float(fields.get("bid")) or 0.0
    ask_f = _positive_float(fields.get("ask")) or 0.0
    mid_f = _positive_float(fields.get("mid"))
    vol = _int_field(fields.get("volume"))
    oi = _int_field(fields.get("open_interest"))
    prem = _positive_float(fields.get("premium")) or 0.0
    if prem <= 0 and ask_f > 0:
        prem = ask_f
    spread_pts = _spread_pct_points(
        bid_f,
        ask_f,
        mid_f,
        _positive_float(fields.get("spread_pct")),
    )
    min_vol = int(min_volume if min_volume is not None else entry_min_volume())
    min_oi = int(min_open_interest if min_open_interest is not None else entry_min_open_interest())
    min_prem = float(min_premium if min_premium is not None else entry_min_premium())
    max_prem = float(max_premium if max_premium is not None else entry_max_premium())
    max_quote_age = live_max_quote_age_seconds()
    q_age = quote_age_seconds
    if q_age is None and fields.get("quote_age_seconds") is not None:
        try:
            q_age = float(fields.get("quote_age_seconds"))
        except (TypeError, ValueError):
            q_age = None

    metrics: Dict[str, Any] = {
        "bid": bid_f,
        "ask": ask_f,
        "spread_pct": spread_pts,
        "volume": vol,
        "open_interest": oi,
        "quote_age_seconds": q_age,
        "premium": prem,
        "min_volume": min_vol,
        "min_open_interest": min_oi,
        "max_spread_pct": float(max_spread_pct_points),
        "min_premium": min_prem,
        "max_premium": max_prem,
        "budget": float(budget) if budget is not None else None,
        "path": str(path or "orb"),
        "chain_source": str(chain_source or ""),
    }

    def _fail(code: str, text: str) -> Dict[str, Any]:
        canonical = canonical_reason_code(code)
        return {
            "passed": False,
            "reason_code": canonical,
            "reason_text": text,
            "metrics": metrics,
        }

    if require_live_chain and not synthetic_allowed:
        src = str(chain_source or "").strip().lower()
        blocked_sources = ("demo_synthetic", "synthetic", "blocked", "unavailable")
        if src in blocked_sources or (demo_mode and src and "synthetic" in src):
            return _fail(
                REASON_LIVE_CHAIN_REQUIRED,
                f"Live option chain required but chain_source={chain_source or 'unknown'}",
            )

    has_quote = bid_f > 0 and ask_f > 0
    if require_quote_fields and not has_quote:
        if bid_f <= 0:
            return _fail(REASON_NO_BID, "Missing or zero bid")
        return _fail(REASON_NO_ASK, "Missing or zero ask")
    if has_quote:
        if bid_f <= 0:
            return _fail(REASON_NO_BID, "Missing or zero bid")
        if ask_f <= 0:
            return _fail(REASON_NO_ASK, "Missing or zero ask")

    if enforce_quote_age is None:
        enforce_quote_age = should_enforce_quote_age(
            demo_mode=demo_mode,
            require_live_chain=require_live_chain,
        )
    if enforce_quote_age and max_quote_age > 0 and q_age is not None:
        try:
            if float(q_age) > max_quote_age:
                return _fail(
                    REASON_QUOTE_STALE,
                    f"Quote age {float(q_age):.2f}s > max {max_quote_age:.2f}s",
                )
        except (TypeError, ValueError):
            pass

    try:
        from modules.spread_policy import cheap_allowance_applies as _sp_cheap
    except ImportError:  # pragma: no cover
        _sp_cheap = lambda _b, _a: False  # noqa: E731
    if (
        has_quote
        and max_spread_pct_points > 0
        and spread_pts > max_spread_pct_points + 1e-9
        and not _sp_cheap(bid_f, ask_f)
    ):
        return _fail(
            REASON_SPREAD_TOO_WIDE,
            f"Spread {spread_pts:.2f}% > max {max_spread_pct_points:.2f}%",
        )

    vol_known = fields.get("volume") is not None
    oi_known = fields.get("open_interest") is not None
    if require_quote_fields and has_quote and not vol_known:
        return _fail(
            "contract_liquidity_data_error",
            "Selected contract is missing volume",
        )
    if require_quote_fields and has_quote and not oi_known:
        return _fail(
            "contract_liquidity_data_error",
            "Selected contract is missing open interest",
        )
    delta_raw = fields.get("delta", _DELTA_ABSENT)
    if delta_raw is not _DELTA_ABSENT:
        try:
            delta_num = float(delta_raw)
        except (TypeError, ValueError):
            delta_num = None
        if delta_num is None or delta_num != delta_num:
            return _fail(
                "contract_quote_data_error",
                "Selected contract delta is missing or not finite",
            )
    if has_quote and vol_known and min_vol > 0 and vol < min_vol:
        return _fail(
            REASON_LOW_VOLUME,
            f"Volume {vol} < min_volume {min_vol}",
        )

    if has_quote and oi_known and min_oi > 0 and oi < min_oi:
        return _fail(
            REASON_LOW_OPEN_INTEREST,
            f"Open interest {oi} < min_open_interest {min_oi}",
        )

    if min_prem > 0 and prem < min_prem - 1e-9:
        return _fail(
            REASON_PREMIUM_TOO_LOW,
            f"Premium {prem:.4f} < min_premium {min_prem:.4f}",
        )

    if max_prem > 0 and prem > max_prem + 1e-9:
        return _fail(
            REASON_PREMIUM_TOO_HIGH,
            f"Premium {prem:.4f} > max_premium {max_prem:.4f}",
        )

    if budget is not None and float(budget) > 0 and prem > 0:
        est_cost = prem * 100.0
        if est_cost > float(budget) * 1.02:
            return _fail(
                REASON_TOO_EXPENSIVE,
                f"Estimated cost ${est_cost:.2f} > budget ${float(budget):.2f}",
            )

    return {
        "passed": True,
        "reason_code": None,
        "reason_text": None,
        "metrics": metrics,
    }


def validator_diagnostics_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    """Flatten validator result for order_payload / Telegram rows."""
    if not isinstance(result, dict):
        return {}
    metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
    out: Dict[str, Any] = {
        "reject_reason": result.get("reason_code"),
        "reject_reason_text": result.get("reason_text"),
    }
    for key in (
        "volume",
        "open_interest",
        "spread_pct",
        "min_volume",
        "min_open_interest",
        "max_spread_pct",
        "min_premium",
        "premium",
        "quote_age_seconds",
        "budget",
        "bid",
        "ask",
        "chain_source",
    ):
        if metrics.get(key) is not None:
            out[key] = metrics.get(key)
    return {k: v for k, v in out.items() if v is not None}


def format_canonical_reject_telemetry(result: Dict[str, Any]) -> str:
    """Human-readable reject line with measured vs configured values."""
    if not isinstance(result, dict) or result.get("passed"):
        return ""
    code = str(result.get("reason_code") or "")
    m = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
    parts = [f"reason={code}"]
    if code == REASON_LOW_VOLUME:
        parts.append(f"volume={m.get('volume')}")
        parts.append(f"min_volume={m.get('min_volume')}")
    elif code == REASON_LOW_OPEN_INTEREST:
        parts.append(f"open_interest={m.get('open_interest')}")
        parts.append(f"min_open_interest={m.get('min_open_interest')}")
    elif code == REASON_SPREAD_TOO_WIDE:
        parts.append(f"spread_pct={m.get('spread_pct')}")
        parts.append(f"max_spread_pct={m.get('max_spread_pct')}")
    elif code == REASON_QUOTE_STALE:
        parts.append(f"quote_age_seconds={m.get('quote_age_seconds')}")
        parts.append(f"max_quote_age_seconds={live_max_quote_age_seconds()}")
    elif code == REASON_PREMIUM_TOO_LOW:
        parts.append(f"premium={m.get('premium')}")
        parts.append(f"min_premium={m.get('min_premium')}")
    elif code == REASON_PREMIUM_TOO_HIGH:
        parts.append(f"premium={m.get('premium')}")
        parts.append(f"max_premium={m.get('max_premium')}")
    elif code == REASON_TOO_EXPENSIVE:
        parts.append(f"premium={m.get('premium')}")
        parts.append(f"budget={m.get('budget')}")
    return " | ".join(str(p) for p in parts if p)


def log_execution_policy_audit(logger: Optional[logging.Logger] = None) -> None:
    """Emit single canonical cold-start execution policy line."""
    lg = logger or log
    try:
        from modules.spread_policy import orb_entry_max_spread_points, trendline_entry_max_spread_points
    except ImportError:
        orb_entry_max_spread_points = lambda: 8.0  # type: ignore[misc, assignment]
        trendline_entry_max_spread_points = lambda: 30.0  # type: ignore[misc, assignment]

    require_live = _bool_env("REQUIRE_LIVE_OPTION_DATA", True)
    demo_synth = _bool_env("0DTE_DEMO_SYNTHETIC_CHAIN", False)
    tl_synth = _bool_env("TRENDLINE_ALLOW_SYNTHETIC_ENTRY", False)

    lg.info(
        "0DTE_EXECUTION_POLICY_AUDIT | entry_min_volume=%d | entry_min_open_interest=%d | "
        "entry_min_premium=%.4f | entry_max_premium=%.4f | "
        "orb_max_spread_pct=%.3f | trendline_max_spread_pct=%.3f | "
        "live_max_quote_age_seconds=%.1f | entry_fill=ask | exit_fill=bid | "
        "live_chain_required=%s | demo_synthetic_chain=%s | trendline_synthetic_entry=%s",
        entry_min_volume(),
        entry_min_open_interest(),
        entry_min_premium(),
        entry_max_premium(),
        float(orb_entry_max_spread_points()),
        float(trendline_entry_max_spread_points()),
        live_max_quote_age_seconds(),
        str(require_live).lower(),
        str(demo_synth).lower(),
        str(tl_synth).lower(),
    )
