"""Cache-first helpers for Priority Optimizer entry/exit snapshots.

Goal: keep full PO field fill while preferring discovery quote caches, session
1m tech, and options-chain TTL over extra E*TRADE get_quotes / live chain pulls
— especially important once Path SS reject volume rises (reject snaps themselves
do not call the broker; entry/exit still must not steal the 2s OCC budget).
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from .po_feature_schema import FEATURE_KEYS_OPTIONS_MICROSTRUCTURE

log = logging.getLogger(__name__)

from central_tech.spy_change import (
    cached_spy_change_pct,
    lookup_cached_equity_quote,
    rs_vs_spy_from_quote_caches,
    stamp_rs_vs_spy_from_quote_caches,
)


# Minimum options microstructure to skip a live chain pull (Layer C / entry carry).
# Layer C skew alone is NOT enough — owned contract volume/OI/liquidity come from
# full enrich. Skipping enrich when only skew is present left TL/BO entry snaps with
# null representative_volume / cumulative_volume / open_interest / liquidity_class
# (Sep 4 regression) and blocked owned_contract_volume_delta_5m.
_OPTIONS_READY_KEYS = (
    "put_call_oi_ratio",
    "call_chain_volume",
    "put_chain_volume",
    "direction_volume_pct",
    "call_chain_open_interest",
    "put_chain_open_interest",
)

# Owned / desk depth required before cache_row short-circuit (not in Layer C skew).
_OPTIONS_OWNED_DEPTH_KEYS = (
    "representative_volume",
    "cumulative_volume",
)

# Session / carry TA that makes a broker comprehensive call optional.
_TA_READY_KEYS = (
    "macd",
    "macd_histogram",
    "rsi",
    "vwap",
    "sma_20",
    "bollinger_position",
)


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or default)
    except Exception:
        return float(default)


def po_cache_quote_max_age_sec() -> float:
    """Max age for discovery/path equity quotes reused by PO hydrate.

    Same table as the gate: ``decision_freeze.quote_max_age_sec`` (120).
    ``0DTE_PO_CACHE_QUOTE_MAX_AGE_SEC`` still overrides when set.
    """
    if os.getenv("0DTE_PO_CACHE_QUOTE_MAX_AGE_SEC") not in (None, ""):
        return max(5.0, _env_float("0DTE_PO_CACHE_QUOTE_MAX_AGE_SEC", 120.0))
    try:
        from central_tech.decision_freeze import quote_max_age_sec

        return float(quote_max_age_sec())
    except Exception:
        return 120.0


def po_entry_chain_allow_stale_sec() -> float:
    """Reuse Layer C / discovery chain this long for entry PO.

    Same table as the gate: ``decision_freeze.chain_max_age_sec`` (60).
    """
    if os.getenv("0DTE_PO_ENTRY_CHAIN_ALLOW_STALE_SEC") not in (None, ""):
        return max(0.0, _env_float("0DTE_PO_ENTRY_CHAIN_ALLOW_STALE_SEC", 60.0))
    try:
        from central_tech.decision_freeze import chain_max_age_sec

        return float(chain_max_age_sec())
    except Exception:
        return 60.0


def po_exit_chain_allow_stale_sec() -> float:
    """Reuse cached chain for exit / mid-trade PO only while it is still fresh."""
    if os.getenv("0DTE_PO_EXIT_CHAIN_ALLOW_STALE_SEC") not in (None, ""):
        return max(0.0, _env_float("0DTE_PO_EXIT_CHAIN_ALLOW_STALE_SEC", 60.0))
    try:
        from central_tech.decision_freeze import chain_max_age_sec

        return float(chain_max_age_sec())
    except Exception:
        return 60.0




def session_ta_sufficient_for_po(row: Optional[Dict[str, Any]]) -> bool:
    """True when session/carry already stamped enough TA to skip broker comprehensive."""
    if not isinstance(row, dict):
        return False
    present = sum(1 for k in _TA_READY_KEYS if row.get(k) is not None and row.get(k) != "")
    # Need most session-authoritative fields; SMA can come from session 1m.
    return present >= 4 and row.get("macd") is not None and row.get("rsi") is not None


def options_microstructure_sufficient(row: Optional[Dict[str, Any]]) -> bool:
    """True when entry carry has chain skew AND owned contract depth for PO fill.

    Chain skew (≥2 of ``_OPTIONS_READY_KEYS``) is required but not sufficient:
    also need owned volume (rep or cumulative), OI, and liquidity_class so
    trajectory deltas like ``owned_contract_volume_delta_5m`` can hydrate from
    a warm ring without a second live pull when stale cache enrich can fill.
    """
    if not isinstance(row, dict):
        return False
    hits = sum(1 for k in _OPTIONS_READY_KEYS if row.get(k) is not None)
    if hits < 2:
        return False
    has_owned_vol = any(row.get(k) is not None and row.get(k) != "" for k in _OPTIONS_OWNED_DEPTH_KEYS)
    has_oi = row.get("open_interest") is not None or row.get("representative_open_interest") is not None
    has_liq = row.get("liquidity_class") not in (None, "")
    return bool(has_owned_vol and has_oi and has_liq)


OPTION_L1_CARRY_KEYS = (
    "option_bid_size",
    "option_ask_size",
    "option_l1_imbalance",
    "option_l1_source",
    "option_l1_strike",
)


def extract_options_features_from_row(row: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Copy options microstructure already on the row (admit / carry / prior enrich)."""
    if not isinstance(row, dict):
        return {}
    out: Dict[str, Any] = {}
    for key in FEATURE_KEYS_OPTIONS_MICROSTRUCTURE:
        val = row.get(key)
        if val is not None and val != "":
            out[key] = val
    meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    skew = meta.get("path_chain_skew") if isinstance(meta.get("path_chain_skew"), dict) else {}
    for key, val in skew.items():
        if key in FEATURE_KEYS_OPTIONS_MICROSTRUCTURE and val is not None and out.get(key) is None:
            out[key] = val
    for moon_key in (
        "representative_call_delta",
        "representative_put_delta",
        "representative_call_gamma",
        "representative_put_gamma",
    ):
        moon_val = skew.get(moon_key)
        if moon_val not in (None, "") and out.get(moon_key) is None:
            out[moon_key] = moon_val
    # Option L1 is not in FEATURE_KEYS_OPTIONS_MICROSTRUCTURE (schema count is
    # frozen), but the gate row carries it. Oct 5: 7/26 TL handoffs took this
    # cache_row path and dropped option_l1_imbalance to None.
    qg = row.get("qg_admit") if isinstance(row.get("qg_admit"), dict) else {}
    for l1_key in OPTION_L1_CARRY_KEYS:
        if out.get(l1_key) is not None:
            continue
        for src in (row, qg, meta, skew):
            val = src.get(l1_key) if isinstance(src, dict) else None
            if val not in (None, ""):
                out[l1_key] = val
                break
    if out:
        out.setdefault("po_options_enrichment_status", "cache_row")
        out.setdefault("chain_fetch_source", out.get("chain_fetch_source") or "row_seed")
    return out


_OHLV_KEYS = ("open", "high", "low", "volume")


def stamp_session_ohlv_from_quote_cache(
    row: Optional[Dict[str, Any]],
    system: Any,
    symbol: str,
    *,
    overwrite: bool = False,
    max_age_sec: Optional[float] = None,
) -> int:
    """Day open / high / low / volume from the discovery quote cache. No broker call.

    The session-tech short-circuit (``session_ta_sufficient_for_po``) skips the
    broker comprehensive merge, which was the only writer of these schema keys.
    Oct 5: null on 78% of exits and on every Breakout handoff although the quote
    cache held them. ``overwrite`` is for 20p / exit (moment fields).
    """
    if not isinstance(row, dict) or system is None:
        return 0
    cached = lookup_cached_equity_quote(system, symbol, max_age_sec=max_age_sec)
    if not cached:
        return 0
    n = 0
    for key in _OHLV_KEYS:
        raw = cached.get(key)
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        if val != val or val <= 0:
            continue
        if not overwrite and row.get(key) not in (None, ""):
            continue
        row[key] = val
        n += 1
    if n:
        row.setdefault("po_ohlv_source", "quote_cache")
    return n


def stamp_underlying_l1_from_cache(
    row: Optional[Dict[str, Any]],
    system: Any,
    symbol: str,
    *,
    max_age_sec: float = 20.0,
) -> int:
    """Underlying L1 from the in-process ring. No get_quotes.

    Writes only when the newest ring sample is inside the quote cap. A stale
    sample stays missing so training does not treat an old L1 as the event.
    """
    if not isinstance(row, dict) or system is None:
        return 0
    if row.get("underlying_l1_imbalance") not in (None, ""):
        return 0
    try:
        from modules.session_tech_admit import derive_underlying_l1_features

        l1 = derive_underlying_l1_features(system, str(symbol or "").strip().upper(), quote=None)
    except Exception:
        return 0
    imb = l1.get("underlying_l1_imbalance") if isinstance(l1, dict) else None
    if imb is None:
        return 0
    age = l1.get("underlying_quote_age_sec")
    try:
        age_f = float(age) if age is not None else None
    except (TypeError, ValueError):
        age_f = None
    if age_f is not None and age_f > float(max_age_sec):
        return 0
    row["underlying_l1_imbalance"] = imb
    if l1.get("underlying_bid_size") is not None:
        row.setdefault("underlying_bid_size", l1.get("underlying_bid_size"))
    if l1.get("underlying_ask_size") is not None:
        row.setdefault("underlying_ask_size", l1.get("underlying_ask_size"))
    if age_f is not None:
        row["underlying_quote_age_sec"] = age_f
    row.setdefault("po_l1_source", "equity_l1_ring")
    return 1


def build_comprehensive_from_quote_cache(
    system: Any,
    symbol: str,
    *,
    row: Optional[Dict[str, Any]] = None,
    max_age_sec: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Lightweight comprehensive dict from quote cache + optional row/session fields."""
    cached = lookup_cached_equity_quote(system, symbol, max_age_sec=max_age_sec)
    if not cached:
        return None
    data = dict(cached)
    data["symbol"] = str(symbol or "").strip().upper()
    # Overlay session/row TA so merge_comprehensive still sees SMA/MACD/etc.
    src = row if isinstance(row, dict) else {}
    for key in (
        "sma_20",
        "sma_50",
        "ema_12",
        "ema_26",
        "macd",
        "macd_signal",
        "macd_histogram",
        "atr",
        "rsi",
        "rsi_14",
        "vwap",
        "vwap_distance_pct",
        "volume_ratio",
        "bollinger_upper",
        "bollinger_middle",
        "bollinger_lower",
        "bollinger_width",
        "bollinger_position",
        "momentum_10",
        "rs_vs_spy",
    ):
        if src.get(key) is not None and data.get(key) is None:
            data[key] = src.get(key)
    if data.get("rs_vs_spy") is None:
        rs = rs_vs_spy_from_quote_caches(system, symbol, max_age_sec=max_age_sec)
        if rs is not None:
            data["rs_vs_spy"] = rs
    data["data_source"] = "quote_cache+session"
    data["data_quality"] = "good"
    return data
