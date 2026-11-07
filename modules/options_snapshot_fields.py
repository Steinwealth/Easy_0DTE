"""
Options chain snapshot fields for Priority Optimizer (E*TRADE chain + viability diag).

Builds sparse feature dicts from OptionsChainManager viability diagnostics,
chain sanity snapshots, and representative OptionContract quotes.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

try:
    from .po_feature_schema import FEATURE_KEYS_OPTIONS_MICROSTRUCTURE
except ImportError:
    FEATURE_KEYS_OPTIONS_MICROSTRUCTURE = (
        "viability_score",
        "avg_bid_ask_spread_pct",
        "representative_spread_pct",
        "representative_delta",
        "representative_open_interest",
        "representative_volume",
        "representative_bid",
        "representative_ask",
        "representative_strike",
        "representative_price",
        "representative_price_source",
        "total_chain_volume",
        "total_chain_open_interest",
        "valid_bid_ask_contracts",
        "spread_candidate_count",
        "itm_contracts",
        "chain_age_seconds",
        "chain_fetch_source",
        "chain_stale",
        "liquidity_class",
        "implied_volatility",
        "gamma",
        "theta",
        "vega",
        "put_call_volume_ratio",
        "put_call_oi_ratio",
        "call_chain_volume",
        "put_chain_volume",
        "call_chain_open_interest",
        "put_chain_open_interest",
        "chain_sanity_best_same_side_spread_pct",
        "chain_sanity_best_same_side_volume",
        "chain_sanity_best_same_side_oi",
        "atm_near_bid_ask",
        "selector_reject_summary",
        "po_options_owned_strike",
    )

log = logging.getLogger(__name__)


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


def compute_put_call_ratios(chain: Optional[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    """Volume and OI put/call ratios — shared with Convex pc_oi runtime (`convex_chain_skew`)."""
    from easy0DTE.modules.convex_chain_skew import compute_put_call_ratios_from_chain

    return compute_put_call_ratios_from_chain(chain)


def extract_greeks_from_contract(contract: Any) -> Dict[str, Optional[float]]:
    """Greeks on the contract at this read.

    ``delta`` is the absolute value, matching the research snapshots. A later
    +20% or exit overlay replaces the handoff delta with this read.
    """
    if contract is None:
        return {
            "delta": None,
            "implied_volatility": None,
            "gamma": None,
            "theta": None,
            "vega": None,
        }
    delta = _safe_float(getattr(contract, "delta", None))
    if delta is None and isinstance(contract, dict):
        delta = _safe_float(contract.get("delta"))
    iv = _safe_float(getattr(contract, "implied_volatility", None) or getattr(contract, "iv", None))
    if iv is None and isinstance(contract, dict):
        iv = _safe_float(contract.get("implied_volatility") or contract.get("iv"))
    return {
        "delta": abs(float(delta)) if delta is not None else None,
        "implied_volatility": iv,
        "gamma": _safe_float(getattr(contract, "gamma", None) if not isinstance(contract, dict) else contract.get("gamma")),
        "theta": _safe_float(getattr(contract, "theta", None) if not isinstance(contract, dict) else contract.get("theta")),
        "vega": _safe_float(getattr(contract, "vega", None) if not isinstance(contract, dict) else contract.get("vega")),
    }


def extract_greeks_from_mapping(*sources: Any) -> Dict[str, Optional[float]]:
    """First live greek print from quote blobs, OptionGreeks, or already-stamped rows."""
    out = {
        "delta": None,
        "implied_volatility": None,
        "iv": None,
        "gamma": None,
        "theta": None,
        "vega": None,
    }
    for src in sources:
        if src is None:
            continue
        blobs: list = []
        if isinstance(src, dict):
            blobs.append(src)
            nested = src.get("OptionGreeks") or src.get("optionGreeks") or src.get("greeks")
            if isinstance(nested, dict):
                blobs.append(nested)
            diag = src.get("diagnostics")
            if isinstance(diag, dict):
                blobs.append(diag)
                nested = diag.get("OptionGreeks") or diag.get("greeks")
                if isinstance(nested, dict):
                    blobs.append(nested)
        else:
            blobs.append(src)
        for blob in blobs:
            got = extract_greeks_from_contract(blob)
            if isinstance(blob, dict) and got.get("implied_volatility") is None:
                got["implied_volatility"] = _safe_float(blob.get("iv") or blob.get("impliedVolatility"))
            for key, val in got.items():
                if val is None or out.get(key) is not None:
                    continue
                out[key] = val
        if all(out.get(k) is not None for k in ("delta", "gamma", "theta", "vega")):
            break
    if out.get("implied_volatility") is not None:
        out["iv"] = out["implied_volatility"]
    elif out.get("iv") is not None:
        out["implied_volatility"] = out["iv"]
    return out


def stamp_live_owned_greeks(dest: Any, *sources: Any, overwrite: bool = True) -> int:
    """Write live owned-contract greeks onto a row/osnap. Returns keys stamped."""
    if dest is None or not hasattr(dest, "get"):
        return 0
    got = extract_greeks_from_mapping(*sources)
    n = 0
    for key in ("delta", "gamma", "theta", "vega", "implied_volatility", "iv"):
        val = got.get(key)
        if val is None:
            continue
        if not overwrite and dest.get(key) not in (None, ""):
            continue
        dest[key] = val
        n += 1
    return n


def resolve_preferred_strike_from_row(row: Optional[Dict[str, Any]]) -> Optional[float]:
    """Owned / requested strike for exit/entry PO snaps (prefer exact contract over ATM proxy)."""
    if not isinstance(row, dict):
        return None
    for key in ("strike", "matched_strike", "representative_strike", "requested_strike", "po_options_owned_strike", "listed_exchange_strike"):
        try:
            val = float(row.get(key))
            if val > 0:
                return val
        except (TypeError, ValueError):
            continue
    meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    for nest in (meta, meta.get("signal_snapshot") if isinstance(meta.get("signal_snapshot"), dict) else {}):
        if not isinstance(nest, dict):
            continue
        for key in ("strike", "listed_exchange_strike", "matched_strike"):
            try:
                val = float(nest.get(key) or 0.0)
                if val > 0:
                    return val
            except (TypeError, ValueError):
                continue
    return None


def _find_contract_by_strike(contracts: List[Any], strike: Optional[float]) -> Any:
    if strike is None:
        return None
    try:
        target = float(strike)
    except (TypeError, ValueError):
        return None
    best = None
    best_diff = None
    for c in contracts or []:
        try:
            cs = float(getattr(c, "strike", 0) or 0)
        except (TypeError, ValueError):
            continue
        diff = abs(cs - target)
        if diff < 1e-6:
            return c
        if best_diff is None or diff < best_diff:
            best_diff = diff
            best = c
    # Only accept near strike (within $0.51) — avoid wrong wing when chain sparse.
    if best is not None and best_diff is not None and best_diff <= 0.51:
        return best
    return None


def _stamp_representative_from_owned_contract(
    features: Dict[str, Any],
    contract: Any,
    *,
    source: str = "owned_strike_exit",
) -> None:
    """Overwrite representative_* + greeks with the owned (or preferred) chain contract."""
    if contract is None or not isinstance(features, dict):
        return
    try:
        strike = float(getattr(contract, "strike", 0) or 0)
    except (TypeError, ValueError):
        strike = None
    if strike and strike > 0:
        features["representative_strike"] = strike
    delta = _safe_float(getattr(contract, "delta", None))
    if delta is not None:
        features["representative_delta"] = abs(float(delta))
    oi = getattr(contract, "open_interest", None)
    try:
        if oi is not None:
            features["representative_open_interest"] = int(oi)
    except (TypeError, ValueError):
        pass
    vol = getattr(contract, "volume", None)
    try:
        if vol is not None:
            features["representative_volume"] = int(vol)
    except (TypeError, ValueError):
        pass
    bid = _safe_float(getattr(contract, "bid", None))
    ask = _safe_float(getattr(contract, "ask", None))
    if bid is not None:
        features["representative_bid"] = bid
    if ask is not None:
        features["representative_ask"] = ask
    mid = None
    try:
        mid = float(getattr(contract, "mid_price"))
    except Exception:
        if bid is not None and ask is not None and bid > 0 and ask > 0:
            mid = (bid + ask) / 2.0
        elif bid is not None and bid > 0:
            mid = bid
    if mid is not None and mid > 0:
        features["representative_price"] = mid
    if bid is not None and ask is not None and mid and mid > 0:
        features["representative_spread_pct"] = abs(ask - bid) / mid
    features["representative_price_source"] = source
    # Owned/selected OCC book sizes for PO option L1 (never ATM proxy sizes).
    try:
        bid_size = getattr(contract, "bid_size", None)
        if bid_size is None and isinstance(contract, dict):
            bid_size = contract.get("bid_size") or contract.get("bidSize")
        ask_size = getattr(contract, "ask_size", None)
        if ask_size is None and isinstance(contract, dict):
            ask_size = contract.get("ask_size") or contract.get("askSize")
        if bid_size is not None:
            features["option_bid_size"] = int(float(bid_size))
        if ask_size is not None:
            features["option_ask_size"] = int(float(ask_size))
    except (TypeError, ValueError):
        pass
    for k, v in extract_greeks_from_contract(contract).items():
        if v is not None:
            features[k] = v


def build_options_microstructure_features(
    *,
    viability_diag: Optional[Dict[str, Any]] = None,
    chain_sanity: Optional[Dict[str, Any]] = None,
    chain_meta: Optional[Dict[str, Any]] = None,
    chain: Optional[Dict[str, Any]] = None,
    representative_contract: Any = None,
    selector_reject_summary: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Merge viability diag, chain sanity, fetch meta, and representative contract
    into a sparse options microstructure feature map.
    """
    out: Dict[str, Any] = {}
    vd = dict(viability_diag or {})
    cs = dict(chain_sanity or {})
    meta = dict(chain_meta or {})

    mapping = {
        "viability_score": vd.get("viability_score"),
        "avg_bid_ask_spread_pct": vd.get("avg_bid_ask_spread_pct"),
        "representative_spread_pct": vd.get("representative_spread_pct"),
        "representative_delta": vd.get("representative_delta"),
        "representative_open_interest": vd.get("representative_open_interest"),
        "representative_volume": vd.get("representative_volume"),
        "representative_bid": vd.get("representative_bid"),
        "representative_ask": vd.get("representative_ask"),
        "representative_strike": vd.get("representative_strike"),
        "representative_price": vd.get("representative_price"),
        "representative_price_source": vd.get("representative_price_source"),
        "total_chain_volume": vd.get("total_volume"),
        "total_chain_open_interest": vd.get("total_open_interest"),
        "valid_bid_ask_contracts": vd.get("valid_bid_ask_contracts"),
        "spread_candidate_count": vd.get("spread_candidate_count"),
        "itm_contracts": vd.get("itm_contracts"),
        "chain_sanity_best_same_side_spread_pct": cs.get("best_same_side_spread_pct"),
        "chain_sanity_best_same_side_volume": cs.get("best_same_side_volume"),
        "chain_sanity_best_same_side_oi": cs.get("best_same_side_oi"),
        "atm_near_bid_ask": cs.get("atm_near_bid_ask"),
        "liquidity_class": cs.get("liquidity_class"),
        "chain_age_seconds": meta.get("age_seconds") or meta.get("chain_age_seconds"),
        "chain_observed_at": meta.get("timestamp") or meta.get("observed_at"),
        "chain_fetch_source": meta.get("source"),
        "chain_stale": meta.get("stale"),
        "selector_reject_summary": selector_reject_summary,
    }
    for key in FEATURE_KEYS_OPTIONS_MICROSTRUCTURE:
        val = mapping.get(key)
        if val is None and key in vd:
            val = vd.get(key)
        if val is not None:
            out[key] = val

    out.update(compute_put_call_ratios(chain))
    out.update(extract_greeks_from_contract(representative_contract))
    observed = meta.get("timestamp") or meta.get("observed_at")
    if observed not in (None, ""):
        out["chain_observed_at"] = observed

    return {k: v for k, v in out.items() if v is not None}


async def fetch_options_snapshot_for_symbol(
    options_chain_manager: Any,
    *,
    symbol: str,
    option_type: str,
    underlying_price: float,
    target_delta: Optional[float] = None,
    preferred_strike: Optional[float] = None,
    allow_stale_cache_sec: Optional[float] = None,
    prefer_cache_only: bool = False,
) -> Dict[str, Any]:
    """
    Fetch E*TRADE (or demo synthetic) chain and return options microstructure features.

    When ``preferred_strike`` is set (exit/entry owned contract), representative_* and
    greeks are stamped from that strike so Hold research sees exit-time contract data
    — not only an ATM viability proxy.

    ``allow_stale_cache_sec`` reuses the in-process chain TTL (Layer C / discovery)
    without a live pull. ``prefer_cache_only`` skips live fetch on cache miss so
    open-position exact OCC keeps the broker budget.
    """
    sym = str(symbol or "").strip().upper()
    side = str(option_type or "call").lower()
    if not sym or underlying_price <= 0:
        return {}

    fetch_kwargs: Dict[str, Any] = {
        "expiry": None,
        "underlying_price": float(underlying_price),
        "use_cache": True,
    }
    if allow_stale_cache_sec is not None:
        try:
            fetch_kwargs["allow_stale_cache_sec"] = float(allow_stale_cache_sec)
        except (TypeError, ValueError):
            pass
    if prefer_cache_only:
        # Peek first; only call fetch when a usable cache entry exists so we never
        # issue a live chain while exact OCC owns the broker budget.
        age = None
        try:
            age = options_chain_manager.cached_chain_age_seconds(sym)
        except Exception:
            age = None
        ttl = float(getattr(options_chain_manager, "cache_ttl", 300) or 300)
        allow = float(allow_stale_cache_sec or 0.0)
        usable = max(ttl, allow)
        if age is None or (usable > 0 and float(age) > usable):
            return {
                "po_options_enrichment_status": "failed",
                "po_options_enrichment_deferred_reason": "prefer_cache_only_miss",
                "chain_fetch_source": "cache_miss",
            }
        fetch_kwargs["allow_stale_cache_sec"] = max(allow, float(age) + 1.0)

    from easy0DTE.modules.options_chain_manager import fetch_0dte_intent_chain

    chain = await fetch_0dte_intent_chain(options_chain_manager, symbol=sym, **fetch_kwargs)
    meta = {}
    if hasattr(options_chain_manager, "get_last_chain_fetch_meta"):
        meta = dict(options_chain_manager.get_last_chain_fetch_meta(sym) or {})
    return options_features_from_chain(
        options_chain_manager,
        chain,
        symbol=sym,
        option_type=side,
        underlying_price=float(underlying_price),
        target_delta=target_delta,
        preferred_strike=preferred_strike,
        chain_meta=meta,
    )


def options_features_from_chain(
    options_chain_manager: Any,
    chain: Optional[Dict[str, Any]],
    *,
    symbol: str,
    option_type: str,
    underlying_price: float,
    target_delta: Optional[float] = None,
    preferred_strike: Optional[float] = None,
    chain_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Options microstructure from a chain already in hand. Never fetches.

    Shared by the live/cache fetch above and the PO entry cache-peek fill, so
    both produce identical keys (representative_*, viability, chain sanity,
    greeks, option L1) from the same chain.
    """
    sym = str(symbol or "").strip().upper()
    side = str(option_type or "call").lower()
    try:
        from modules.orb_0dte_opening_execution import build_chain_sanity_snapshot
    except Exception:
        build_chain_sanity_snapshot = None  # type: ignore
    viability_score, viability_diag = options_chain_manager.compute_viability_score(
        chain=chain or {},
        option_type=side,
        underlying_price=float(underlying_price),
        target_delta=target_delta,
    )
    if isinstance(viability_diag, dict):
        viability_diag.setdefault("viability_score", viability_score)

    chain_sanity: Dict[str, Any] = {}
    if build_chain_sanity_snapshot is not None and chain:
        try:
            chain_sanity = build_chain_sanity_snapshot(
                chain,
                side,
                float(underlying_price),
                sym,
            )
        except Exception as exc:
            log.debug("chain_sanity snapshot skipped for %s: %s", sym, exc)

    meta = dict(chain_meta or {})

    contracts = list((chain or {}).get(side + "s", []) or [])
    rep_contract = None
    owned_source = None
    if preferred_strike is not None:
        rep_contract = _find_contract_by_strike(contracts, preferred_strike)
        if rep_contract is not None:
            owned_source = "owned_strike"
    if rep_contract is None:
        try:
            rep_strike = viability_diag.get("representative_strike") if isinstance(viability_diag, dict) else None
            rep_contract = _find_contract_by_strike(contracts, rep_strike)
        except Exception:
            rep_contract = None

    features = build_options_microstructure_features(
        viability_diag=viability_diag,
        chain_sanity=chain_sanity,
        chain_meta=meta,
        chain=chain,
        representative_contract=rep_contract,
    )
    if owned_source and rep_contract is not None:
        _stamp_representative_from_owned_contract(
            features,
            rep_contract,
            source=owned_source,
        )
        features["po_options_owned_strike"] = float(preferred_strike) if preferred_strike else None
    elif (
        rep_contract is not None
        and preferred_strike is None
        and features.get("option_bid_size") is None
    ):
        # (When a preferred/owned strike is named but not on this chain, its
        # sizes are unknown. Do not borrow the representative's book.)
        # No owned strike (pre-fill / representative only): still carry the
        # representative contract's L1 sizes so option_l1_imbalance is real.
        try:
            from central_tech.option_l1 import _size_pair_from_contract, l1_imbalance

            b, a = _size_pair_from_contract(rep_contract)
            if b is not None or a is not None:
                features["option_bid_size"] = b
                features["option_ask_size"] = a
                imb = l1_imbalance(b, a)
                if imb is not None:
                    features["option_l1_imbalance"] = imb
                features["option_l1_source"] = "chain_representative_strike"
        except Exception:
            pass
    if features.get("option_l1_imbalance") is None and (
        features.get("option_bid_size") is not None or features.get("option_ask_size") is not None
    ):
        # Owned/selected strike sizes: derive the imbalance from the same book.
        try:
            from central_tech.option_l1 import l1_imbalance

            imb = l1_imbalance(features.get("option_bid_size"), features.get("option_ask_size"))
            if imb is not None:
                features["option_l1_imbalance"] = imb
                features.setdefault(
                    "option_l1_source",
                    "chain_selected_strike" if owned_source else "chain_representative_strike",
                )
        except Exception:
            pass
    return features


def peek_cached_chain_with_age(
    options_chain_manager: Any,
    symbol: str,
) -> "tuple[Optional[Dict[str, Any]], Optional[float], Optional[float]]":
    """Newest non-empty cached chain for ``symbol``: (chain, age_sec, fetched_epoch).

    No broker call. Unlike ``peek_cached_options_chain`` this reports the cache
    timestamp so callers can bound staleness and stamp ``chain_observed_at``.
    """
    import time as _time

    sym = str(symbol or "").strip().upper()
    cache = getattr(options_chain_manager, "chain_cache", None) if options_chain_manager is not None else None
    if not sym or not isinstance(cache, dict) or not cache:
        return None, None, None
    best = None
    best_ts = None
    prefix = f"{sym}_"
    for key, cached in list(cache.items()):
        if not str(key).startswith(prefix) or not isinstance(cached, dict):
            continue
        chain = cached.get("chain")
        if not isinstance(chain, dict) or not (chain.get("calls") or chain.get("puts")):
            continue
        try:
            ts = float(cached.get("timestamp") or 0.0)
        except (TypeError, ValueError):
            continue
        if ts <= 0:
            continue
        if best_ts is None or ts > best_ts:
            best, best_ts = chain, ts
    if best is None or best_ts is None:
        return None, None, None
    return best, max(0.0, _time.time() - best_ts), best_ts


def merge_options_features_into_row(row: Dict[str, Any], options_features: Optional[Dict[str, Any]]) -> None:
    """In-place merge of options microstructure onto a pipeline/snapshot row."""
    if not options_features or not isinstance(row, dict):
        return
    for key, val in options_features.items():
        if val is None:
            continue
        # Gate-time Moon inputs stay. Owned-contract enrich must not replace them.
        if key in (
            "representative_call_delta",
            "representative_put_delta",
            "representative_call_gamma",
            "representative_put_gamma",
        ) and row.get(key) not in (None, ""):
            continue
        row[key] = val
    try:
        from profit_optimizer.path_chain_skew import stamp_review_microstructure

        stamp_review_microstructure(
            row,
            option_side=str(
                row.get("option_side")
                or row.get("expected_option_side")
                or row.get("direction")
                or row.get("option_type")
                or ""
            ),
            overwrite=False,
        )
    except Exception:
        pass


def _parse_option_side_token(raw: Any) -> Optional[str]:
    """Return CALL/PUT from a single field value, or None if not decisive."""
    if raw is None:
        return None
    s = str(raw).strip().upper()
    if s in ("CALL", "C"):
        return "CALL"
    if s in ("PUT", "P"):
        return "PUT"
    # Tolerate accidental LONG/SHORT inside option fields.
    if s in ("LONG", "BULL", "BULLISH"):
        return "CALL"
    if s in ("SHORT", "BEARISH"):
        return "PUT"
    return None


def _option_side_from_trade_id(trade_id: Any) -> Optional[str]:
    """Parse CALL/PUT from trade ids like D-260731-OKLO-40c-618 / D-QQQ-668p-783."""
    tid = str(trade_id or "").strip().upper()
    if not tid:
        return None
    # Prefer the contract token immediately before the trailing numeric suffix.
    m = re.search(r"[-_](\d+(?:\.\d+)?)([CP])(?:[-_]|$)", tid)
    if m:
        return "CALL" if m.group(2) == "C" else "PUT"
    if re.search(r"(?:^|[-_])C(?:[-_]|$)", tid) and not re.search(r"(?:^|[-_])P(?:[-_]|$)", tid):
        return "CALL"
    if re.search(r"(?:^|[-_])P(?:[-_]|$)", tid) and not re.search(r"(?:^|[-_])C(?:[-_]|$)", tid):
        return "PUT"
    return None


def canonicalize_option_side_label(row: Optional[Dict[str, Any]] = None, **hints: Any) -> str:
    """
    Trade Side / Direction stamp: CALL or PUT only (never LONG/SHORT).

    Prefer live option-side fields over stale ``option_type_label`` (Jul 28: exit
    snaps carried PUT labels on CALL trades after break/TA carry-forward).
    Legacy LONG→CALL, SHORT→PUT. Do **not** map bare bear/bull line geometry —
    that lives in ``line_geometry``.
    """
    merged: Dict[str, Any] = {}
    if isinstance(row, dict):
        merged.update(row)
    merged.update({k: v for k, v in hints.items() if v is not None})
    md = merged.get("metadata") if isinstance(merged.get("metadata"), dict) else {}

    # 1) Authoritative option-side fields (never trust label ahead of these).
    for key in (
        "option_type",
        "expected_option_side",
        "option_side",
    ):
        raw = merged.get(key)
        if raw is None:
            raw = md.get(key)
        parsed = _parse_option_side_token(raw)
        if parsed:
            return parsed

    # 2) Trade-id contract token (…-40c-… / …-668p-…).
    tid_side = _option_side_from_trade_id(merged.get("trade_id") or md.get("trade_id"))
    if tid_side:
        return tid_side

    # 3) Display label (may be stale on exit snaps — checked after truth fields).
    for key in ("option_type_label",):
        raw = merged.get(key)
        if raw is None:
            raw = md.get(key)
        parsed = _parse_option_side_token(raw)
        if parsed:
            return parsed

    # 4) Generic side/direction — CALL/PUT or LONG/SHORT only.
    for key in ("side", "direction"):
        raw = merged.get(key)
        if raw is None:
            continue
        s = str(raw).strip().upper()
        if s in ("CALL", "C", "LONG", "BULLISH"):
            return "CALL"
        if s in ("PUT", "P", "SHORT", "BEARISH"):
            return "PUT"
        # Skip bear/bull — Trendline line geometry, not trade side.
    return "CALL"


def resolve_option_type_from_row(row: Dict[str, Any]) -> str:
    """CALL/PUT side for chain fetch — shared by ORB1 Breakout and Trendline snapshots."""
    return canonicalize_option_side_label(row).lower()


def resolve_underlying_price_from_row(row: Dict[str, Any]) -> float:
    for key in (
        "current_price",
        "price",
        "entry_underlying_price",
        "close_at_break",
        "entry_price",
    ):
        try:
            val = float(row.get(key) or 0.0)
        except (TypeError, ValueError):
            continue
        if val > 0:
            return val
    meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    try:
        val = float(meta.get("entry_underlying_price") or 0.0)
        if val > 0:
            return val
    except (TypeError, ValueError):
        pass
    return 0.0


def resolve_target_delta_from_row(row: Dict[str, Any]) -> Optional[float]:
    for key in ("target_delta", "representative_delta", "delta_at_entry"):
        raw = row.get(key)
        if raw is None:
            continue
        try:
            val = float(raw)
            if val > 0:
                return val
        except (TypeError, ValueError):
            continue
    meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    try:
        val = float(meta.get("target_delta") or 0.0)
        return val if val > 0 else None
    except (TypeError, ValueError):
        return None


async def enrich_row_with_options_chain(
    options_chain_manager: Any,
    row: Dict[str, Any],
    *,
    allow_stale_cache_sec: Optional[float] = None,
    prefer_cache_only: bool = False,
) -> Dict[str, Any]:
    """
    Fetch chain + merge microstructure onto one snapshot row (ORB1 Breakout + Trendline).
    Returns sparse options features (may be empty when chain unavailable).
    """
    if not isinstance(row, dict) or options_chain_manager is None:
        return {}
    sym = str(row.get("symbol") or "").strip().upper()
    spot = resolve_underlying_price_from_row(row)
    if not sym or spot <= 0:
        return {}
    opt_type = resolve_option_type_from_row(row)
    try:
        opts = await fetch_options_snapshot_for_symbol(
            options_chain_manager,
            symbol=sym,
            option_type=opt_type,
            underlying_price=spot,
            target_delta=resolve_target_delta_from_row(row),
            preferred_strike=resolve_preferred_strike_from_row(row),
            allow_stale_cache_sec=allow_stale_cache_sec,
            prefer_cache_only=prefer_cache_only,
        )
        merge_options_features_into_row(row, opts)
        return opts
    except Exception as exc:
        log.debug("0DTE options enrich skipped for %s: %s", sym, exc)
        return {}


def enrich_row_with_options_chain_sync(
    options_chain_manager: Any,
    row: Dict[str, Any],
    *,
    timeout_seconds: float = 20.0,
    allow_stale_cache_sec: Optional[float] = None,
    prefer_cache_only: bool = False,
) -> Dict[str, Any]:
    """Sync wrapper for break/emit hooks that cannot await."""
    if not isinstance(row, dict) or options_chain_manager is None:
        return {}
    sym = str(row.get("symbol") or "").strip().upper()
    spot = resolve_underlying_price_from_row(row)
    if not sym or spot <= 0:
        return {}
    opt_type = resolve_option_type_from_row(row)
    try:
        opts = fetch_options_snapshot_sync(
            options_chain_manager,
            symbol=sym,
            option_type=opt_type,
            underlying_price=spot,
            target_delta=resolve_target_delta_from_row(row),
            preferred_strike=resolve_preferred_strike_from_row(row),
            timeout_seconds=timeout_seconds,
            allow_stale_cache_sec=allow_stale_cache_sec,
            prefer_cache_only=prefer_cache_only,
        )
        merge_options_features_into_row(row, opts)
        return opts
    except Exception as exc:
        log.debug("0DTE options enrich (sync) skipped for %s: %s", sym, exc)
        return {}


def fetch_options_snapshot_sync(
    options_chain_manager: Any,
    *,
    symbol: str,
    option_type: str,
    underlying_price: float,
    target_delta: Optional[float] = None,
    preferred_strike: Optional[float] = None,
    timeout_seconds: float = 20.0,
    allow_stale_cache_sec: Optional[float] = None,
    prefer_cache_only: bool = False,
) -> Dict[str, Any]:
    """Run async chain fetch from sync snapshot hooks (break / handoff)."""
    import asyncio
    import concurrent.futures

    coro = fetch_options_snapshot_for_symbol(
        options_chain_manager,
        symbol=symbol,
        option_type=option_type,
        underlying_price=underlying_price,
        target_delta=target_delta,
        preferred_strike=preferred_strike,
        allow_stale_cache_sec=allow_stale_cache_sec,
        prefer_cache_only=prefer_cache_only,
    )
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, coro)
        return future.result(timeout=timeout_seconds)
