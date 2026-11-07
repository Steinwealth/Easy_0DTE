"""
Broker chain OI/volume skew for Convex pc_oi gate @ 7:30.

Runtime path only — uses OptionsChainManager (E*TRADE or demo synthetic per Data.md).
Separate from Priority Optimizer snapshot collection (`modules/options_snapshot_fields.py`).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

CHAIN_SKEW_FIELDS: tuple[str, ...] = (
    "put_call_oi_ratio",
    "put_call_volume_ratio",
    "call_chain_open_interest",
    "put_chain_open_interest",
    "call_chain_volume",
    "put_chain_volume",
    "representative_call_iv",
    "representative_put_iv",
    "representative_iv",
    "representative_call_delta",
    "representative_put_delta",
    "representative_call_gamma",
    "representative_put_gamma",
    "representative_call_strike",
    "representative_put_strike",
    # 9.21 Path SS — same-chain microstructure (no extra broker fetch)
    "total_chain_volume",
    "total_chain_open_interest",
    "representative_bid",
    "representative_ask",
    "representative_spread_pct",
    "representative_volume",
    "representative_open_interest",
    "avg_bid_ask_spread_pct",
    "spread_candidate_count",
    "valid_bid_ask_contracts",
    "chain_sanity_best_same_side_spread_pct",
    "chain_sanity_best_same_side_volume",
    "gamma",
    "theta",
    "vega",
    "open_interest",
)


def _safe_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def _safe_int(val: Any) -> Optional[int]:
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _contract_metric(contract: Any, attr: str) -> int:
    if contract is None:
        return 0
    if isinstance(contract, dict):
        val = contract.get(attr, 0)
    else:
        val = getattr(contract, attr, 0)
    try:
        return int(val or 0)
    except (TypeError, ValueError):
        return 0


def _contract_float(contract: Any, *attrs: str) -> Optional[float]:
    if contract is None:
        return None
    for attr in attrs:
        if isinstance(contract, dict):
            raw = contract.get(attr)
        else:
            raw = getattr(contract, attr, None)
        val = _safe_float(raw)
        if val is not None:
            return val
    return None


def _atm_contract_metrics(
    contracts: Any, underlying_price: float
) -> Dict[str, Optional[float]]:
    """ATM IV / |delta| / gamma / strike from the same chain fetch (no extra broker call)."""
    empty: Dict[str, Optional[float]] = {
        "iv": None,
        "delta": None,
        "gamma": None,
        "strike": None,
    }
    spot = _safe_float(underlying_price)
    if spot is None or spot <= 0:
        return dict(empty)
    best_iv: Optional[float] = None
    best_delta: Optional[float] = None
    best_gamma: Optional[float] = None
    best_strike: Optional[float] = None
    best_dist: Optional[float] = None
    closest_strike: Optional[float] = None
    closest_delta: Optional[float] = None
    closest_gamma: Optional[float] = None
    closest_dist: Optional[float] = None
    for c in contracts or []:
        strike = _contract_float(c, "strike", "strike_price")
        if strike is None:
            continue
        dist = abs(float(strike) - float(spot))
        raw_delta = _contract_float(c, "delta", "option_delta")
        abs_delta = abs(float(raw_delta)) if raw_delta is not None else None
        if abs_delta is not None and abs_delta < 1e-9:
            abs_delta = None
        gamma = _contract_float(c, "gamma")
        if gamma is not None and float(gamma) <= 0:
            gamma = None
        if closest_dist is None or dist < closest_dist:
            closest_dist = dist
            closest_strike = strike
            closest_delta = abs_delta
            closest_gamma = gamma
        iv = _contract_float(c, "implied_volatility", "iv")
        if iv is None or iv <= 0:
            continue
        if iv > 5.0:
            iv = iv / 100.0
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best_iv = iv
            best_delta = abs_delta
            best_gamma = gamma
            best_strike = strike
    return {
        "iv": best_iv,
        "delta": best_delta if best_delta is not None else closest_delta,
        "gamma": best_gamma if best_gamma is not None else closest_gamma,
        "strike": best_strike if best_strike is not None else closest_strike,
    }


def _atm_iv_from_contracts(contracts: Any, underlying_price: float) -> Optional[float]:
    """ATM implied vol from the same chain fetch (no extra broker call)."""
    return _atm_contract_metrics(contracts, underlying_price).get("iv")


def _otm_fraction(spot: float, strike: float, side: str) -> Optional[float]:
    """Signed OTM fraction vs spot (0.01 = 1%; negative = ITM)."""
    if spot <= 0 or strike <= 0:
        return None
    if side == "LONG":
        raw = (float(strike) - float(spot)) / float(spot)
    else:
        raw = (float(spot) - float(strike)) / float(spot)
    return float(raw)


def compute_representative_iv_from_chain(
    chain: Optional[Dict[str, Any]],
    underlying_price: float,
) -> Dict[str, Optional[float]]:
    """Side ATM IV / |delta| / strike from an already-fetched 0DTE chain."""
    empty: Dict[str, Optional[float]] = {
        "representative_call_iv": None,
        "representative_put_iv": None,
        "representative_iv": None,
        "representative_call_delta": None,
        "representative_put_delta": None,
        "representative_call_gamma": None,
        "representative_put_gamma": None,
        "representative_delta": None,
        "representative_call_strike": None,
        "representative_put_strike": None,
        "representative_strike": None,
    }
    if not chain or not isinstance(chain, dict):
        return empty
    call_m = _atm_contract_metrics(chain.get("calls") or [], underlying_price)
    put_m = _atm_contract_metrics(chain.get("puts") or [], underlying_price)
    call_iv, put_iv = call_m.get("iv"), put_m.get("iv")
    call_d, put_d = call_m.get("delta"), put_m.get("delta")
    call_g, put_g = call_m.get("gamma"), put_m.get("gamma")
    call_k, put_k = call_m.get("strike"), put_m.get("strike")
    return {
        "representative_call_iv": call_iv,
        "representative_put_iv": put_iv,
        "representative_iv": call_iv if call_iv is not None else put_iv,
        "representative_call_delta": call_d,
        "representative_put_delta": put_d,
        "representative_call_gamma": call_g,
        "representative_put_gamma": put_g,
        "representative_delta": call_d if call_d is not None else put_d,
        "representative_call_strike": call_k,
        "representative_put_strike": put_k,
        "representative_strike": call_k if call_k is not None else put_k,
    }


def compute_put_call_ratios_from_chain(chain: Optional[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    """Volume and OI put/call ratios plus per-side chain totals from a fetched broker chain."""
    empty: Dict[str, Optional[float]] = {
        "put_call_volume_ratio": None,
        "put_call_oi_ratio": None,
        "call_chain_volume": None,
        "put_chain_volume": None,
        "call_chain_open_interest": None,
        "put_chain_open_interest": None,
    }
    if not chain or not isinstance(chain, dict):
        return empty
    call_vol = call_oi = put_vol = put_oi = 0
    for c in chain.get("calls", []) or []:
        call_vol += _contract_metric(c, "volume")
        call_oi += _contract_metric(c, "open_interest")
    for p in chain.get("puts", []) or []:
        put_vol += _contract_metric(p, "volume")
        put_oi += _contract_metric(p, "open_interest")
    vol_ratio = (put_vol / call_vol) if call_vol > 0 else None
    oi_ratio = (put_oi / call_oi) if call_oi > 0 else None
    return {
        "put_call_volume_ratio": _safe_float(vol_ratio),
        "put_call_oi_ratio": _safe_float(oi_ratio),
        "call_chain_volume": _safe_int(call_vol),
        "put_chain_volume": _safe_int(put_vol),
        "call_chain_open_interest": _safe_int(call_oi),
        "put_chain_open_interest": _safe_int(put_oi),
        "total_chain_volume": _safe_int(call_vol + put_vol),
        "total_chain_open_interest": _safe_int(call_oi + put_oi),
    }


def resolve_underlying_price_from_signal(signal: Dict[str, Any]) -> float:
    """Best-effort spot from ORB collection row (no broker call)."""
    for key in (
        "current_price",
        "price",
        "entry_underlying_price",
        "close_at_break",
        "entry_price",
    ):
        try:
            val = float(signal.get(key) or 0.0)
        except (TypeError, ValueError):
            continue
        if val > 0:
            return val
    meta = signal.get("metadata") if isinstance(signal.get("metadata"), dict) else {}
    try:
        val = float(meta.get("entry_underlying_price") or 0.0)
        if val > 0:
            return val
    except (TypeError, ValueError):
        pass
    return 0.0


def merge_chain_skew_into_signal(signal: Dict[str, Any], skew: Dict[str, Any]) -> None:
    if not isinstance(signal, dict) or not skew:
        return
    for key in CHAIN_SKEW_FIELDS:
        val = skew.get(key)
        if val is not None:
            signal[key] = val
    # Side-matched ATM IV from the same chain fetch. setdefault so a later
    # chosen-contract IV on the execution snapshot is not overwritten.
    side = resolve_signal_side(signal)
    iv = (
        skew.get("representative_call_iv")
        if side == "LONG"
        else skew.get("representative_put_iv")
    )
    if iv is None:
        iv = skew.get("representative_iv")
    if iv is not None:
        signal.setdefault("representative_iv", iv)
        signal.setdefault("implied_volatility", iv)
        signal.setdefault("iv", iv)
    # ATM |delta| + strike from the same chain (path SS otm/delta). setdefault
    # so Layer D chosen-contract values are not overwritten.
    delta = (
        skew.get("representative_call_delta")
        if side == "LONG"
        else skew.get("representative_put_delta")
    )
    if delta is None:
        delta = skew.get("representative_delta")
    abs_delta = _safe_float(delta)
    if abs_delta is not None:
        abs_delta = abs(float(abs_delta))
        if abs_delta < 1e-9:
            abs_delta = None
    if abs_delta is not None:
        signal.setdefault("representative_delta", abs_delta)
        signal.setdefault("chosen_delta", abs_delta)
        signal.setdefault("delta", abs_delta)
    strike = (
        skew.get("representative_call_strike")
        if side == "LONG"
        else skew.get("representative_put_strike")
    )
    if strike is None:
        strike = skew.get("representative_strike")
    strike_f = _safe_float(strike)
    if strike_f is not None:
        signal.setdefault("representative_strike", strike_f)
    if signal.get("otm_pct") is None:
        otm_strike = _safe_float(signal.get("strike") or signal.get("representative_strike"))
        spot = resolve_underlying_price_from_signal(signal)
        if otm_strike is not None and spot > 0:
            otm = _otm_fraction(spot, float(otm_strike), side)
            if otm is not None:
                signal["otm_pct"] = otm
    # Desk/log label — options chain side volume (not equity session ratio).
    signal["volume_kind_chain"] = "chain_side"
    md = signal.get("metadata")
    if not isinstance(md, dict):
        md = {}
        signal["metadata"] = md
    md["volume_kind_chain"] = "chain_side"
    if iv is not None:
        md.setdefault("representative_iv", iv)
        md.setdefault("implied_volatility", iv)
        md.setdefault("iv", iv)
    if abs_delta is not None:
        md.setdefault("representative_delta", abs_delta)
        md.setdefault("chosen_delta", abs_delta)
        md.setdefault("delta", abs_delta)
    # Gate Moon inputs. Same chain fetch, each side's own near-spot delta.
    # Not representative_delta, which a later owned-contract stamp may replace.
    for moon_key in ("representative_call_delta", "representative_put_delta"):
        moon_val = _safe_float(skew.get(moon_key))
        if moon_val is None:
            moon_val = _safe_float(signal.get(moon_key))
        if moon_val is None:
            continue
        moon_val = abs(float(moon_val))
        if moon_val < 1e-9:
            continue
        signal[moon_key] = moon_val
        md[moon_key] = moon_val
    # Same near-spot contract as the side delta. Call Moon reads the call gamma.
    for gamma_key in ("representative_call_gamma", "representative_put_gamma"):
        gamma_val = _safe_float(skew.get(gamma_key))
        if gamma_val is None:
            gamma_val = _safe_float(signal.get(gamma_key))
        if gamma_val is None or float(gamma_val) <= 0:
            continue
        signal[gamma_key] = float(gamma_val)
        md[gamma_key] = float(gamma_val)
    side_gamma_key = (
        "representative_call_gamma" if side == "LONG" else "representative_put_gamma"
    )
    side_gamma = _safe_float(signal.get(side_gamma_key))
    if side_gamma is not None and side_gamma > 0:
        signal["gamma"] = side_gamma
        md["gamma"] = side_gamma
    if strike_f is not None:
        md.setdefault("representative_strike", strike_f)
    if signal.get("otm_pct") is not None:
        md.setdefault("otm_pct", signal.get("otm_pct"))
    _stamp_chain_totals_on_signal(signal)


def _stamp_chain_totals_on_signal(signal: Dict[str, Any]) -> None:
    """Derive total chain volume / OI from already-stamped side totals. No fetch."""
    if not isinstance(signal, dict):
        return
    call_v = _safe_float(signal.get("call_chain_volume"))
    put_v = _safe_float(signal.get("put_chain_volume"))
    if signal.get("total_chain_volume") is None and call_v is not None and put_v is not None:
        signal["total_chain_volume"] = call_v + put_v
    call_oi = _safe_float(signal.get("call_chain_open_interest"))
    put_oi = _safe_float(signal.get("put_chain_open_interest"))
    if (
        signal.get("total_chain_open_interest") is None
        and call_oi is not None
        and put_oi is not None
    ):
        signal["total_chain_open_interest"] = call_oi + put_oi
    if (
        signal.get("put_call_volume_ratio") is None
        and call_v is not None
        and put_v is not None
        and float(call_v) > 0
    ):
        signal["put_call_volume_ratio"] = float(put_v) / float(call_v)


def _contract_spread_pct(contract: Any) -> Optional[float]:
    sp = _contract_float(contract, "bid_ask_spread_pct")
    if sp is not None:
        return sp
    bid = _contract_float(contract, "bid")
    ask = _contract_float(contract, "ask")
    if bid is None or ask is None or bid <= 0 or ask <= 0:
        return None
    mid = (float(bid) + float(ask)) / 2.0
    if mid <= 0:
        return None
    return abs(float(ask) - float(bid)) / mid


def _closest_contract(contracts: Any, underlying_price: float) -> Any:
    spot = _safe_float(underlying_price)
    if spot is None or spot <= 0:
        return None
    best = None
    best_dist: Optional[float] = None
    for c in contracts or []:
        strike = _contract_float(c, "strike", "strike_price")
        if strike is None:
            continue
        dist = abs(float(strike) - float(spot))
        if best_dist is None or dist < best_dist:
            best_dist = dist
            best = c
    return best


def peek_cached_options_chain(
    options_chain_manager: Any,
    symbol: str,
    underlying_price: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Return a cached 0DTE chain dict without a live broker fetch."""
    if options_chain_manager is None:
        return None
    sym = str(symbol or "").strip().upper()
    if not sym:
        return None
    cache = getattr(options_chain_manager, "chain_cache", None)
    if not isinstance(cache, dict) or not cache:
        return None
    try:
        cache_key, _expiry = options_chain_manager._chain_cache_key(
            sym, expiry=None, underlying_price=underlying_price
        )
        cached = cache.get(cache_key)
        if isinstance(cached, dict) and isinstance(cached.get("chain"), dict):
            chain = cached["chain"]
            if (chain.get("calls") or chain.get("puts")):
                return chain
    except Exception:
        pass
    prefix = f"{sym}_"
    for key, cached in cache.items():
        if not str(key).startswith(prefix) or not isinstance(cached, dict):
            continue
        chain = cached.get("chain")
        if isinstance(chain, dict) and (chain.get("calls") or chain.get("puts")):
            return chain
    return None


def _leg_bid_ask(leg: Any) -> tuple:
    bid = getattr(leg, "bid", None)
    ask = getattr(leg, "ask", None)
    if isinstance(leg, dict):
        bid = leg.get("bid", bid)
        ask = leg.get("ask", ask)
    try:
        bid_f = float(bid) if bid is not None else None
        ask_f = float(ask) if ask is not None else None
    except (TypeError, ValueError):
        return None, None
    return bid_f, ask_f


def chain_has_two_sided_quotes(chain: Any) -> bool:
    """True when any listed call or put has bid>0 and ask>0."""
    if not isinstance(chain, dict):
        return False
    for key in ("calls", "puts"):
        for leg in chain.get(key) or []:
            bid, ask = _leg_bid_ask(leg)
            if bid is not None and ask is not None and bid > 0.0 and ask > 0.0:
                return True
    return False


def compute_path_ss_chain_microstructure(
    chain: Optional[Dict[str, Any]],
    *,
    underlying_price: float,
    option_type: str,
    options_chain_manager: Any = None,
) -> Dict[str, Any]:
    """Path-SS chain keys from an already-fetched / cached chain. No broker call."""
    if not chain or not isinstance(chain, dict):
        return {}
    spot = _safe_float(underlying_price) or 0.0
    side = str(option_type or "call").strip().lower()
    if side in ("c", "long", "call"):
        side = "call"
    else:
        side = "put"
    out: Dict[str, Any] = {}
    ratios = compute_put_call_ratios_from_chain(chain)
    out.update({k: v for k, v in ratios.items() if v is not None})
    ivs = compute_representative_iv_from_chain(chain, spot) if spot > 0 else {}
    out.update({k: v for k, v in ivs.items() if v is not None})

    contracts = list(chain.get(side + "s") or [])
    if options_chain_manager is not None and hasattr(
        options_chain_manager, "compute_viability_score"
    ):
        try:
            _score, diag = options_chain_manager.compute_viability_score(
                chain, side, float(spot) if spot > 0 else 1.0
            )
            if isinstance(diag, dict):
                if diag.get("avg_bid_ask_spread_pct") is not None:
                    out.setdefault(
                        "avg_bid_ask_spread_pct", diag.get("avg_bid_ask_spread_pct")
                    )
                if diag.get("representative_spread_pct") is not None:
                    out.setdefault(
                        "representative_spread_pct", diag.get("representative_spread_pct")
                    )
                if diag.get("representative_bid") is not None:
                    out.setdefault("representative_bid", diag.get("representative_bid"))
                if diag.get("representative_ask") is not None:
                    out.setdefault("representative_ask", diag.get("representative_ask"))
                if diag.get("representative_volume") is not None:
                    out.setdefault(
                        "representative_volume", diag.get("representative_volume")
                    )
                if diag.get("representative_open_interest") is not None:
                    out.setdefault(
                        "representative_open_interest",
                        diag.get("representative_open_interest"),
                    )
                if diag.get("spread_candidate_count") is not None:
                    out.setdefault(
                        "spread_candidate_count", diag.get("spread_candidate_count")
                    )
                if diag.get("valid_bid_ask_contracts") is not None:
                    out.setdefault(
                        "valid_bid_ask_contracts", diag.get("valid_bid_ask_contracts")
                    )
        except Exception:
            pass

    try:
        from modules.orb_0dte_opening_execution import build_chain_sanity_snapshot

        sanity = build_chain_sanity_snapshot(
            chain, side, float(spot) if spot > 0 else 0.0, ""
        )
        if isinstance(sanity, dict):
            if sanity.get("best_same_side_spread_pct") is not None:
                out.setdefault(
                    "chain_sanity_best_same_side_spread_pct",
                    sanity.get("best_same_side_spread_pct"),
                )
            if sanity.get("best_same_side_volume") is not None:
                out.setdefault(
                    "chain_sanity_best_same_side_volume",
                    sanity.get("best_same_side_volume"),
                )
    except Exception:
        pass

    atm = _closest_contract(contracts, spot)
    if atm is not None:
        bid = _contract_float(atm, "bid")
        ask = _contract_float(atm, "ask")
        if bid is not None:
            out.setdefault("representative_bid", bid)
        if ask is not None:
            out.setdefault("representative_ask", ask)
        sp = _contract_spread_pct(atm)
        if sp is not None:
            out.setdefault("representative_spread_pct", sp)
        vol = _contract_metric(atm, "volume")
        if vol:
            out.setdefault("representative_volume", vol)
        oi = _contract_metric(atm, "open_interest")
        if oi:
            out.setdefault("representative_open_interest", oi)
            out.setdefault("open_interest", oi)
        gamma = _contract_float(atm, "gamma")
        theta = _contract_float(atm, "theta")
        vega = _contract_float(atm, "vega")
        if gamma is not None:
            out.setdefault("gamma", gamma)
        if theta is not None:
            out.setdefault("theta", theta)
        if vega is not None:
            out.setdefault("vega", vega)

    spreads: List[float] = []
    quoted = 0
    best_spread: Optional[float] = None
    best_vol = 0
    for c in contracts:
        sp = _contract_spread_pct(c)
        bid = _contract_float(c, "bid")
        ask = _contract_float(c, "ask")
        if bid is not None and ask is not None and bid > 0 and ask > 0:
            quoted += 1
            if sp is not None:
                spreads.append(float(sp))
                if best_spread is None or float(sp) < best_spread:
                    best_spread = float(sp)
        vol = _contract_metric(c, "volume")
        if vol > best_vol:
            best_vol = vol
    if spreads and out.get("avg_bid_ask_spread_pct") is None:
        out["avg_bid_ask_spread_pct"] = sum(spreads) / float(len(spreads))
    if out.get("spread_candidate_count") is None:
        out["spread_candidate_count"] = quoted
    if best_spread is not None:
        out.setdefault("chain_sanity_best_same_side_spread_pct", best_spread)
    if best_vol:
        out.setdefault("chain_sanity_best_same_side_volume", best_vol)
    return {k: v for k, v in out.items() if v is not None}


def stamp_path_ss_chain_fields_from_cache(
    options_chain_manager: Any,
    signal: Dict[str, Any],
) -> int:
    """Fill missing Path-SS chain keys from the in-process chain cache. No live fetch."""
    if not isinstance(signal, dict) or options_chain_manager is None:
        return 0
    sym = str(signal.get("symbol") or "").strip().upper()
    spot = resolve_underlying_price_from_signal(signal)
    chain = peek_cached_options_chain(
        options_chain_manager, sym, underlying_price=spot or None
    )
    if not chain:
        return 0
    side = resolve_signal_side(signal)
    option_type = "call" if side == "LONG" else "put"
    micro = compute_path_ss_chain_microstructure(
        chain,
        underlying_price=spot,
        option_type=option_type,
        options_chain_manager=options_chain_manager,
    )
    n = 0
    for key, val in micro.items():
        if val is None:
            continue
        if signal.get(key) is None:
            signal[key] = val
            n += 1
    # Option L1 (bid/ask size → option_l1_imbalance) from the same chain. The
    # Trade Profile and PO handoff read it; nothing else stamps it pre-select.
    try:
        from central_tech.option_l1 import stamp_option_l1_from_chain

        n += int(
            stamp_option_l1_from_chain(
                signal,
                chain,
                option_type=option_type,
                underlying_price=spot or None,
            )
            or 0
        )
    except Exception:
        pass
    _stamp_chain_totals_on_signal(signal)
    return n


def resolve_options_chain_manager(system: Any) -> Any:
    """OptionsChainManager from trading system or dte0_manager (broker-injected in main.py)."""
    mgr = None
    if hasattr(system, "_options_chain_manager"):
        mgr = system._options_chain_manager()
    if mgr is None:
        dte0 = getattr(system, "dte0_manager", None)
        mgr = getattr(dte0, "options_chain_manager", None) if dte0 else None
    if mgr is not None:
        # SINGLE_TP_ADMIT picker reads the same cached chain (no API call).
        try:
            from easy0DTE.modules.single_tp_admit import register_chain_manager

            register_chain_manager(mgr)
        except Exception:
            pass
    return mgr


def _record_chain_api_usage(system: Any) -> None:
    recorder = getattr(system, "_record_option_api_usage", None)
    if callable(recorder):
        try:
            recorder(chain_calls=1, quote_calls=0, symbols_active=1)
        except Exception:
            pass


def _unique_symbol_spots(signals: List[Dict[str, Any]]) -> List[Tuple[str, float]]:
    """One fetch per underlying — best spot wins when multiple rows share a symbol."""
    best: Dict[str, float] = {}
    for sig in signals or []:
        if not isinstance(sig, dict):
            continue
        sym = str(sig.get("symbol") or "").strip().upper()
        if not sym:
            continue
        spot = resolve_underlying_price_from_signal(sig)
        if spot <= 0:
            continue
        prev = best.get(sym, 0.0)
        if spot > prev:
            best[sym] = spot
    return sorted(best.items())


async def fetch_chain_skew_for_symbol(
    options_chain_manager: Any,
    *,
    symbol: str,
    underlying_price: float,
    system: Any = None,
    allow_stale_cache_sec: Optional[float] = None,
    force_live: bool = False,
) -> Dict[str, Any]:
    """Fetch 0DTE chain via broker and return pc_oi + side totals (sparse dict).

    ``force_live`` (Oct 7 2026 decision freeze): bypass the chain cache for the
    one targeted, budgeted refresh when the cached chain is older than the
    Trade Profile cap.
    """
    sym = str(symbol or "").strip().upper()
    if not sym or underlying_price <= 0 or options_chain_manager is None:
        return {}
    kwargs: Dict[str, Any] = {
        "expiry": None,
        "underlying_price": float(underlying_price),
        "use_cache": not bool(force_live),
    }
    if (not force_live) and allow_stale_cache_sec is not None:
        kwargs["allow_stale_cache_sec"] = float(allow_stale_cache_sec)
    from easy0DTE.modules.options_chain_manager import fetch_0dte_intent_chain

    chain = await fetch_0dte_intent_chain(
        options_chain_manager,
        symbol=sym,
        system=system,
        **kwargs,
    )
    try:
        meta = options_chain_manager.get_last_chain_fetch_meta(sym)
    except Exception:
        meta = {}
    if not (isinstance(meta, dict) and meta.get("from_cache")):
        _record_chain_api_usage(system)
    skew = compute_put_call_ratios_from_chain(chain)
    skew.update(compute_representative_iv_from_chain(chain, float(underlying_price)))
    if isinstance(chain, dict):
        exp = str(chain.get("selected_expiry") or chain.get("expiry") or "").strip()
        if exp:
            skew["selected_expiry"] = exp
            skew["expiry"] = exp
    try:
        sel = getattr(options_chain_manager, "last_expiry_selection", None) or {}
        exp2 = str(sel.get("selected_expiry") or sel.get("expiry_ymd") or "").strip()
        if exp2 and not skew.get("selected_expiry"):
            skew["selected_expiry"] = exp2
            skew["expiry"] = exp2
    except Exception:
        pass
    return {k: v for k, v in skew.items() if v is not None}


async def enrich_signal_with_chain_skew(
    options_chain_manager: Any,
    signal: Dict[str, Any],
    *,
    system: Any = None,
    prefetched_skew: Optional[Dict[str, Any]] = None,
    allow_stale_cache_sec: Optional[float] = None,
    force_live: bool = False,
) -> Dict[str, Any]:
    """Fetch broker chain skew and merge onto one Convex input row."""
    if not isinstance(signal, dict) or options_chain_manager is None:
        return {}
    sym = str(signal.get("symbol") or "").strip().upper()
    spot = resolve_underlying_price_from_signal(signal)
    if not sym or spot <= 0:
        return {}
    try:
        if prefetched_skew is not None:
            skew = dict(prefetched_skew)
        else:
            skew = await fetch_chain_skew_for_symbol(
                options_chain_manager,
                symbol=sym,
                underlying_price=spot,
                system=system,
                allow_stale_cache_sec=allow_stale_cache_sec,
                force_live=force_live,
            )
        merge_chain_skew_into_signal(signal, skew)
        # Oct 7 2026: stamp the chain's own observation clock (cache age, or ~0
        # after a live fetch) so the ring sample and the 5m delta use it.
        try:
            import time as _time

            _age = options_chain_manager.cached_chain_age_seconds(
                sym, underlying_price=spot
            )
            if _age is None:
                _age = options_chain_manager.cached_chain_age_seconds(sym)
            if _age is not None:
                signal["tp_chain_age_sec"] = float(_age)
                signal["tp_chain_observed_epoch"] = _time.time() - float(_age)
        except Exception:
            pass
        try:
            side = resolve_signal_side(signal)
            option_type = "call" if side == "LONG" else "put"
            # Layer C already fetched (or reused cache). Stamp remaining Path-SS
            # micro from that same in-process chain — no second broker pull.
            n_micro = stamp_path_ss_chain_fields_from_cache(
                options_chain_manager, signal
            )
            if n_micro and option_type:
                log.debug(
                    "0DTE_CONVEX_PC_OI | path_ss_chain_micro | symbol=%s | filled=%d",
                    sym,
                    n_micro,
                )
        except Exception:
            pass
        # Warm PO chain ~5m ring on every Layer C / confirm hydrate so handoff
        # and execute snaps have a prior ≥60s before fill (no extra broker poll).
        if system is not None and skew:
            try:
                from central_tech.chain_ring import record_chain_skew_sample

                sample = dict(skew)
                for k in (
                    "side",
                    "option_side",
                    "expected_option_side",
                    "option_type",
                    "option_type_label",
                    "direction",
                    "representative_volume",
                    "cumulative_volume",
                    "open_interest",
                ):
                    if signal.get(k) is not None and sample.get(k) is None:
                        sample[k] = signal.get(k)
                # Prefer post-merge row volumes (includes same-side dir% stamps).
                for k in (
                    "call_chain_volume",
                    "put_chain_volume",
                    "direction_volume_pct",
                    "representative_volume",
                    "cumulative_volume",
                ):
                    if signal.get(k) is not None:
                        sample[k] = signal.get(k)
                _obs = signal.get("tp_chain_observed_epoch")
                record_chain_skew_sample(
                    system,
                    sym,
                    sample,
                    ts=float(_obs) if isinstance(_obs, (int, float)) else None,
                )
            except Exception:
                pass
        return skew
    except Exception as exc:
        log.debug(
            "0DTE_CONVEX_PC_OI | chain_skew_fetch_skipped | symbol=%s | err=%s",
            sym,
            exc,
        )
        return {}


def resolve_signal_side(signal: Dict[str, Any]) -> str:
    """Normalized LONG/SHORT from collection row (CALL→LONG, PUT→SHORT)."""
    side = str(signal.get("side") or signal.get("direction") or "LONG").upper()
    if side in ("LONG", "SHORT"):
        return side
    if side in ("CALL", "BULLISH", "C"):
        return "LONG"
    if side in ("PUT", "BEARISH", "P"):
        return "SHORT"
    for key in ("option_type", "option_type_label", "expected_option_side", "option_side"):
        raw = signal.get(key)
        if raw is None:
            continue
        s = str(raw).strip().upper()
        if s in ("CALL", "C", "LONG"):
            return "LONG"
        if s in ("PUT", "P", "SHORT"):
            return "SHORT"
    return "LONG"


def _symbol_side_key(sig: Dict[str, Any]) -> str:
    sym = str(sig.get("symbol") or "").strip().upper()
    return f"{sym}_{resolve_signal_side(sig)}"


def apply_skew_map_to_rows(
    rows: List[Dict[str, Any]],
    skew_by_symbol: Dict[str, Dict[str, Any]],
) -> int:
    """Merge fetched chain skew onto every row matching symbol (LONG + SHORT rows)."""
    merged = 0
    for sig in rows or []:
        if not isinstance(sig, dict):
            continue
        sym = str(sig.get("symbol") or "").strip().upper()
        prefetched = skew_by_symbol.get(sym)
        if not prefetched:
            continue
        merge_chain_skew_into_signal(sig, prefetched)
        merged += 1
    return merged


def propagate_chain_skew_fields_to_pools(
    source_rows: List[Dict[str, Any]],
    *target_pools: List[Dict[str, Any]],
) -> int:
    """Copy chain skew fields from enriched rows onto collection pool rows."""
    by_symbol: Dict[str, Dict[str, Any]] = {}
    by_key: Dict[str, Dict[str, Any]] = {}
    for row in source_rows or []:
        if not isinstance(row, dict) or row.get("put_call_oi_ratio") is None:
            continue
        sym = str(row.get("symbol") or "").strip().upper()
        if sym:
            by_symbol[sym] = row
        by_key[_symbol_side_key(row)] = row
    merged = 0
    for pool in target_pools:
        for sig in pool or []:
            if not isinstance(sig, dict):
                continue
            sym = str(sig.get("symbol") or "").strip().upper()
            src = by_key.get(_symbol_side_key(sig)) or by_symbol.get(sym)
            if not src:
                continue
            merge_chain_skew_into_signal(sig, src)
            merged += 1
    if merged:
        log.debug("0DTE_CONVEX_PC_OI | propagated_chain_fields | rows=%d", merged)
    return merged


def _broker_api_pacing_module():
    """Load pacing helpers without importing modules/__init__ (heavy side effects)."""
    import importlib.util
    from pathlib import Path

    cached = getattr(_broker_api_pacing_module, "_cached", None)
    if cached is not None:
        return cached
    path = Path(__file__).resolve().parents[2] / "modules/broker_api_pacing.py"
    spec = importlib.util.spec_from_file_location("_broker_api_pacing_runtime", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"broker_api_pacing not found at {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _broker_api_pacing_module._cached = mod  # type: ignore[attr-defined]
    return mod


async def enrich_signals_with_chain_skew_for_convex(
    system: Any,
    signals: List[Dict[str, Any]],
) -> None:
    """Fetch E*TRADE chain OI skew (put_call_oi_ratio) before Convex pc_oi gate @ 7:30."""
    apace_market_api_after_request = _broker_api_pacing_module().apace_market_api_after_request

    manager = resolve_options_chain_manager(system)
    if manager is None:
        log.warning("0DTE_CONVEX_PC_OI | chain_manager_missing | put_call_oi enrich skipped")
        return

    rows = [s for s in (signals or []) if isinstance(s, dict)]
    for sig in rows:
        if resolve_underlying_price_from_signal(sig) <= 0 and hasattr(system, "_lightweight_enrich_signal_for_red_day"):
            try:
                system._lightweight_enrich_signal_for_red_day(sig)
            except Exception:
                pass

    symbol_spots = _unique_symbol_spots(rows)
    skew_by_symbol: Dict[str, Dict[str, Any]] = {}
    for sym, spot in symbol_spots:
        try:
            skew = await fetch_chain_skew_for_symbol(
                manager,
                symbol=sym,
                underlying_price=spot,
                system=system,
            )
            if skew.get("put_call_oi_ratio") is not None:
                skew_by_symbol[sym] = skew
            else:
                log.warning(
                    "0DTE_CONVEX_PC_OI | chain_fetch_no_pc_oi | symbol=%s | grep=0DTE_CONVEX_PC_OI",
                    sym,
                )
        except Exception as exc:
            log.warning(
                "0DTE_CONVEX_PC_OI | chain_fetch_failed | symbol=%s | err=%s | grep=0DTE_CONVEX_PC_OI",
                sym,
                exc,
            )
        await apace_market_api_after_request(reason="convex_chain_enrich")

    merged_rows = apply_skew_map_to_rows(rows, skew_by_symbol)
    if skew_by_symbol and merged_rows == 0:
        log.error(
            "0DTE_CONVEX_PC_OI | chain_skew_merge_failed | fetched_symbols=%d | rows=%d | "
            "grep=0DTE_CONVEX_PC_OI",
            len(skew_by_symbol),
            len(rows),
        )

    total = len(rows)
    enriched = sum(1 for sig in rows if sig.get("put_call_oi_ratio") is not None)
    no_spot = sum(1 for sig in rows if resolve_underlying_price_from_signal(sig) <= 0)
    fetch_failed = max(0, total - enriched - no_spot)
    log.info(
        "0DTE_CONVEX_PC_OI | stage=chain_enrich | signals=%d | unique_symbols=%d | "
        "put_call_oi_populated=%d | no_spot=%d | fetch_failed=%d | "
        "volume_kind=chain_side | grep=0DTE_CONVEX_PC_OI",
        total,
        len(symbol_spots),
        enriched,
        no_spot,
        fetch_failed,
    )
    if total > 0 and enriched < total:
        log.warning(
            "0DTE_CONVEX_PC_OI | coverage_incomplete | populated=%d/%d | "
            "symbols_without_pc_oi_will_fail_convex",
            enriched,
            total,
        )
