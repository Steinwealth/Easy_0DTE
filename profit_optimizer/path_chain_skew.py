"""Shared call/put chain skew + review microstructure for entry/exit/path."""

from __future__ import annotations

from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

def order_keys(keys):
    """Keep the written order when the Priority Optimizer usefulness list is absent."""
    try:
        from modules.po_key_usefulness import order_keys as _po_order

        return _po_order(keys)
    except Exception:
        return tuple(dict.fromkeys(keys))

# Session call/put expansion + skew (chain-level).
PATH_CHAIN_SKEW_KEYS = (
    "call_chain_volume",
    "put_chain_volume",
    "put_call_volume_ratio",
    "put_call_oi_ratio",
    "call_chain_open_interest",
    "put_chain_open_interest",
    "direction_volume_pct",
    "total_chain_volume",
    "total_chain_open_interest",
)

# Owned-contract fields stamped from sparse chain refresh / entry features.
PATH_OWNED_CONTRACT_KEYS = (
    "delta",
    "open_interest",
    "gamma",
    "theta",
    "iv",
    "representative_volume",
    "representative_open_interest",
    "representative_delta",
    "representative_bid",
    "representative_ask",
    "representative_strike",
)

# Depth / contract tape (exact quote path — not synthesized).
PATH_DEPTH_KEYS = (
    "bid_size",
    "ask_size",
    "cumulative_volume",
    "last",
)

# Session underlying tech for MACD hold/exit + participation review (1m RTH).
SESSION_TECH_PATH_KEYS = (
    "macd",
    "macd_signal",
    "macd_histogram",
    "prior_macd_histogram",
    "macd_above_zero",
    "macd_cross",
    "macd_hold_ok_call",
    "macd_exit_signal_call",
    "macd_hist_sign",
    "sma_20",
    "sma_50",
    "ema_12",
    "ema_26",
    "rsi",
    "vwap",
    "vwap_distance_pct",
    "vwap_position",
    # Equity participation (PLTR Jul 29: thin volume → small underlying move).
    "volume_ratio",
    "orb_volume_ratio",
    # Current 3m efficiency is consumed by the PATH_V2 20% Save exit.
    "price_volume_efficiency_3m",
    "price_change_3m_pct",
    "volume_3m",
    "session_tech_bar_at",
    "session_tech_asof_at",
    "pve3_bar_at",
    # Receipt/upsert clock used by Priority Optimizer freshness policy.
    "session_tech_observed_at",
    "volume_ratio_missing",
    "volume_ratio_source",
    "tech_source",
    "rsi_missing",
    "macd_missing",
    "vwap_missing",
    "session_bar_count",
    # Current-session ATR/BB/momentum from the same 1m hub bars (not entry FF).
    "atr",
    "momentum_10",
    "bollinger_upper",
    "bollinger_middle",
    "bollinger_lower",
    "bollinger_width",
    "bollinger_position",
    "rs_vs_spy",
    "open",
    "high",
    "low",
    "volume",
    "volume_1m",
    "distance_from_vwap_atr",
)

# Live Trade Profile keys derived on the owned tick for exit mining (no extra API).
OWNED_TICK_REVIEW_KEYS = (
    "otm_pct",
    "call_share",
    "put_share",
    "dir_pct_change_5m",
    "underlying_l1_imbalance",
)

PATH_CHAIN_SKEW_KEYS = order_keys(PATH_CHAIN_SKEW_KEYS)
PATH_OWNED_CONTRACT_KEYS = order_keys(PATH_OWNED_CONTRACT_KEYS)
PATH_DEPTH_KEYS = order_keys(PATH_DEPTH_KEYS)
SESSION_TECH_PATH_KEYS = order_keys(SESSION_TECH_PATH_KEYS)
OWNED_TICK_REVIEW_KEYS = order_keys(OWNED_TICK_REVIEW_KEYS)


def overlay_live_session_tech(osnap: Any, cached: Any) -> None:
    """Write current-session tech onto the live snap. Never keep an entry PVE."""
    if not isinstance(osnap, dict) or not isinstance(cached, dict):
        return
    for key, value in cached.items():
        if value is not None:
            osnap[key] = value


_PATH_COPY_KEYS = order_keys(
    PATH_CHAIN_SKEW_KEYS
    + PATH_OWNED_CONTRACT_KEYS
    + PATH_DEPTH_KEYS
    + SESSION_TECH_PATH_KEYS
    + OWNED_TICK_REVIEW_KEYS
)

# Flattened onto TradeEntrySnapshot / exit_observation for desk review.
# volume_ratio / orb_volume_ratio live only in SESSION_TECH_PATH_KEYS (no duplicate).
REVIEW_MICROSTRUCTURE_KEYS = order_keys(
    PATH_CHAIN_SKEW_KEYS
    + PATH_OWNED_CONTRACT_KEYS
    + SESSION_TECH_PATH_KEYS
    + (
        "bid_size",
        "ask_size",
        "cumulative_volume",
        "volume",  # underlying share volume when present on PO snaps
        "implied_volatility",
    )
)


def _f(val: Any) -> Optional[float]:
    if val is None or val == "":
        return None
    try:
        out = float(val)
    except (TypeError, ValueError):
        return None
    if out != out:  # NaN
        return None
    return out


def option_side_token(*cands: Any) -> str:
    """CALL/PUT only — never treat bear/bull geometry as an option side."""
    for cand in cands:
        s = str(cand or "").strip().lower()
        if not s:
            continue
        if s in ("put", "p", "puts"):
            return "put"
        if s in ("call", "c", "calls"):
            return "call"
        if s.endswith("put") and "call" not in s:
            return "put"
        if s.endswith("call") and "put" not in s:
            return "call"
    return ""


def derive_direction_volume_pct(
    call_vol: Any,
    put_vol: Any,
    *,
    option_side: str = "",
) -> Optional[float]:
    try:
        cv = float(call_vol)
        pv = float(put_vol)
    except (TypeError, ValueError):
        return None
    total = cv + pv
    if total <= 0:
        return None
    side = option_side_token(option_side)
    if side == "put":
        return pv / total
    if side == "call":
        return cv / total
    # Unknown side: still expose call share as a neutral tape read.
    return cv / total


def maybe_replace_complement_dirpct(
    existing: Any,
    derived: Optional[float],
    *,
    epsilon: float = 0.02,
) -> Optional[float]:
    """If stamped dir% is the opposite-side share, return the same-side derived value."""
    if derived is None:
        return None
    try:
        cur = float(existing)
    except (TypeError, ValueError):
        return derived
    if abs(cur - derived) <= epsilon:
        return cur
    if abs((cur + derived) - 1.0) <= epsilon:
        return derived
    return cur


def _alias_owned_from_representative(out: Dict[str, Any], src: Mapping[str, Any]) -> None:
    if out.get("delta") is None and src.get("representative_delta") is not None:
        out["delta"] = src.get("representative_delta")
    if out.get("open_interest") is None and src.get("representative_open_interest") is not None:
        out["open_interest"] = src.get("representative_open_interest")
    if out.get("iv") is None:
        if src.get("iv") is not None:
            out["iv"] = src.get("iv")
        elif src.get("implied_volatility") is not None:
            out["iv"] = src.get("implied_volatility")
    if out.get("representative_volume") is None and src.get("representative_volume") is not None:
        out["representative_volume"] = src.get("representative_volume")
    if out.get("representative_delta") is None and src.get("representative_delta") is not None:
        out["representative_delta"] = src.get("representative_delta")
    if out.get("representative_open_interest") is None and src.get(
        "representative_open_interest"
    ) is not None:
        out["representative_open_interest"] = src.get("representative_open_interest")


def _derive_same_side_chain_volume(out: Dict[str, Any], *, option_side: str = "") -> None:
    """same_side_chain_volume is the owned side's chain volume at this event."""
    if out.get("same_side_chain_volume") not in (None, ""):
        return
    side = option_side_token(
        option_side,
        out.get("option_side"),
        out.get("expected_option_side"),
        out.get("option_type"),
        out.get("side"),
    )
    if side == "call":
        val = _f(out.get("call_chain_volume") if out.get("call_chain_volume") not in (None, "") else out.get("call_volume"))
    elif side == "put":
        val = _f(out.get("put_chain_volume") if out.get("put_chain_volume") not in (None, "") else out.get("put_volume"))
    else:
        val = None
    if val is not None:
        out["same_side_chain_volume"] = val


def _derive_chain_totals(out: Dict[str, Any]) -> None:
    if out.get("total_chain_volume") is None:
        cv = _f(out.get("call_chain_volume"))
        pv = _f(out.get("put_chain_volume"))
        if cv is not None and pv is not None:
            out["total_chain_volume"] = cv + pv
    _derive_same_side_chain_volume(out)
    if out.get("total_chain_open_interest") is None:
        coi = _f(out.get("call_chain_open_interest"))
        poi = _f(out.get("put_chain_open_interest"))
        if coi is not None and poi is not None:
            out["total_chain_open_interest"] = coi + poi
    if out.get("put_call_volume_ratio") is None:
        cv = _f(out.get("call_chain_volume"))
        pv = _f(out.get("put_chain_volume"))
        if cv is not None and pv is not None and cv > 0:
            out["put_call_volume_ratio"] = pv / cv
    if out.get("put_call_oi_ratio") is None:
        coi = _f(out.get("call_chain_open_interest"))
        poi = _f(out.get("put_chain_open_interest"))
        if coi is not None and poi is not None and coi > 0:
            out["put_call_oi_ratio"] = poi / coi


def skew_dict_from_mapping(src: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    if not isinstance(src, Mapping):
        return {}
    out: Dict[str, Any] = {}
    for k in _PATH_COPY_KEYS:
        if src.get(k) is not None:
            out[k] = src.get(k)
    _alias_owned_from_representative(out, src)
    _derive_chain_totals(out)
    derived = derive_direction_volume_pct(
        out.get("call_chain_volume"),
        out.get("put_chain_volume"),
        option_side=option_side_token(
            src.get("option_side"),
            src.get("expected_option_side"),
            src.get("option_type"),
            src.get("side"),
        ),
    )
    if derived is not None:
        if out.get("direction_volume_pct") is None:
            out["direction_volume_pct"] = derived
        else:
            replaced = maybe_replace_complement_dirpct(out.get("direction_volume_pct"), derived)
            if replaced is not None:
                out["direction_volume_pct"] = replaced
    return out


def last_nonnull_path_chain_skew(
    premium_path: Optional[Iterable[Any]],
    *,
    prefer_phase: str = "live",
) -> Dict[str, Any]:
    """Newest non-null value of each skew field.

    A later tick that carries chain volume only must not hide delta, open
    interest, or representative volume already observed on an earlier tick.
    Live rows win over shadow rows. A later value is never replaced by an older one.
    """
    rows = [r for r in (premium_path or []) if isinstance(r, (dict, Mapping))]
    if not rows:
        return {}

    merged: Dict[str, Any] = {}

    def _absorb(row: Mapping[str, Any]) -> None:
        for key, value in skew_dict_from_mapping(row).items():
            if value is None or merged.get(key) is not None:
                continue
            merged[key] = value

    prefer = str(prefer_phase or "").strip().lower()
    if prefer:
        for row in reversed(rows):
            phase = str(row.get("path_phase") or "").strip().lower()
            if phase and phase != prefer:
                continue
            _absorb(row)
    if merged.get("call_chain_volume") is None and merged.get("put_chain_volume") is None:
        for row in reversed(rows):
            _absorb(row)
    return merged


def apply_skew_onto_osnap(
    osnap: Dict[str, Any],
    skew: Mapping[str, Any],
    *,
    option_side: str = "",
    overwrite: bool = False,
) -> None:
    """Copy skew keys onto osnap; optionally overwrite existing values."""
    if not isinstance(osnap, dict) or not isinstance(skew, Mapping):
        return
    for k in _PATH_COPY_KEYS:
        if skew.get(k) is None:
            continue
        if overwrite or osnap.get(k) is None:
            osnap[k] = skew.get(k)
    side = str(option_side or osnap.get("option_side") or "").lower()
    if side and not osnap.get("option_side"):
        osnap["option_side"] = side
    stamp_review_microstructure(osnap, option_side=side, overwrite=overwrite)


def seed_osnap_chain_skew_from_sources(
    osnap: Dict[str, Any],
    *,
    premium_path: Optional[Iterable[Any]] = None,
    path_chain_skew: Optional[Mapping[str, Any]] = None,
    option_side: str = "",
) -> Dict[str, Any]:
    """Forward-fill osnap from meta cache then last path sample (live preferred)."""
    used: Dict[str, Any] = {}
    if isinstance(path_chain_skew, Mapping):
        used = skew_dict_from_mapping(path_chain_skew)
        apply_skew_onto_osnap(osnap, used, option_side=option_side, overwrite=False)
    path_skew = last_nonnull_path_chain_skew(premium_path, prefer_phase="live")
    if path_skew:
        apply_skew_onto_osnap(osnap, path_skew, option_side=option_side, overwrite=False)
        used = {**used, **{k: v for k, v in path_skew.items() if used.get(k) is None}}
    return used


def fill_tick_fields_from_prior_path(
    tick_fields: Dict[str, Any],
    premium_path: Optional[Iterable[Any]],
    *,
    option_side: str = "",
) -> None:
    """Fill owned/session-tech from prior ticks. Chain volumes stay empty when stale.

    Inheriting ``call_chain_volume`` from the last tick froze ``*_now`` for the
    whole trade. The discovery refresh is the one chain writer.
    """
    if not isinstance(tick_fields, dict):
        return
    need_owned = any(
        tick_fields.get(k) is None
        for k in ("delta", "open_interest", "representative_volume")
    )
    need_session_tech = any(tick_fields.get(k) is None for k in SESSION_TECH_PATH_KEYS)
    if need_owned:
        skew = last_nonnull_path_chain_skew(premium_path, prefer_phase="live")
        if skew:
            owned = {
                k: skew.get(k)
                for k in PATH_OWNED_CONTRACT_KEYS
                if skew.get(k) is not None
            }
            apply_skew_onto_osnap(tick_fields, owned, option_side=option_side, overwrite=False)
        else:
            stamp_review_microstructure(tick_fields, option_side=option_side, overwrite=False)
    else:
        stamp_review_microstructure(tick_fields, option_side=option_side, overwrite=False)
    if need_session_tech:
        rows = [r for r in (premium_path or []) if isinstance(r, (dict, Mapping))]
        for row in reversed(rows):
            filled_any = False
            for k in SESSION_TECH_PATH_KEYS:
                if tick_fields.get(k) is not None:
                    continue
                if row.get(k) is None:
                    continue
                tick_fields[k] = row.get(k)
                filled_any = True
            if tick_fields.get("orb_volume_ratio") is None and tick_fields.get("volume_ratio") is not None:
                tick_fields["orb_volume_ratio"] = tick_fields["volume_ratio"]
            if filled_any or any(row.get(k) is not None for k in SESSION_TECH_PATH_KEYS):
                # Prefer newest row that carries any session-tech; stop after first hit.
                if filled_any:
                    break


def stamp_review_microstructure(
    row: Dict[str, Any],
    *,
    option_side: str = "",
    overwrite: bool = False,
    sources: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """Fill review fields in-place: call/put vol+OI, dir%, owned delta/OI/volume, totals.

    Never invents zeros for missing broker fields — only copies / derives when inputs exist.
    """
    if not isinstance(row, dict):
        return {}
    side = option_side_token(
        option_side,
        row.get("option_side"),
        row.get("expected_option_side"),
        row.get("option_type"),
        row.get("side"),
    )
    # Merge optional source bags first (entry cache, osnap, leg).
    for src in sources or ():
        if not isinstance(src, Mapping):
            continue
        compact = skew_dict_from_mapping(src)
        for k, v in compact.items():
            if v is None:
                continue
            if overwrite or row.get(k) is None:
                row[k] = v
        for k in REVIEW_MICROSTRUCTURE_KEYS:
            if src.get(k) is None:
                continue
            if overwrite or row.get(k) is None:
                row[k] = src.get(k)

    _alias_owned_from_representative(row, row)
    _derive_chain_totals(row)
    _derive_same_side_chain_volume(row, option_side=side)
    derived = derive_direction_volume_pct(
        row.get("call_chain_volume"),
        row.get("put_chain_volume"),
        option_side=side,
    )
    if derived is not None:
        if overwrite or row.get("direction_volume_pct") is None:
            row["direction_volume_pct"] = derived
        else:
            replaced = maybe_replace_complement_dirpct(row.get("direction_volume_pct"), derived)
            if replaced is not None:
                row["direction_volume_pct"] = replaced
    if side and not row.get("option_side"):
        row["option_side"] = side
    return skew_dict_from_mapping(row)


def collect_microstructure_from_system(system: Any, symbol: str) -> Dict[str, Any]:
    """Pull latest cached entry/path chain features for a symbol."""
    sym = str(symbol or "").strip().upper()
    if not sym or system is None:
        return {}
    out: Dict[str, Any] = {}
    by_sym = getattr(system, "_path_chain_skew_by_symbol", None)
    if isinstance(by_sym, dict) and isinstance(by_sym.get(sym), dict):
        out.update(skew_dict_from_mapping(by_sym[sym]))
    for cache_name in (
        "_trendline_po_entry_features_by_symbol",
        "_breakout_po_entry_features_by_symbol",
        "_orb_0dte_po_entry_features_by_symbol",
    ):
        cache = getattr(system, cache_name, None) or {}
        if not isinstance(cache, dict) or sym not in cache:
            continue
        entry = cache[sym]
        if isinstance(entry, list):
            entry = next((row for row in reversed(entry) if isinstance(row, dict)), None)
        if not isinstance(entry, dict):
            continue
        nested = entry.get("_path_chain_skew") if isinstance(entry.get("_path_chain_skew"), dict) else {}
        stamp_review_microstructure(out, sources=(nested, entry), overwrite=False)
        break
    return out


def merge_review_fields_onto_dict(
    dest: Dict[str, Any],
    *sources: Mapping[str, Any],
    option_side: str = "",
    overwrite: bool = False,
) -> None:
    """Copy REVIEW_MICROSTRUCTURE_KEYS from sources onto dest, then stamp derives."""
    if not isinstance(dest, dict):
        return
    bags = [s for s in sources if isinstance(s, Mapping)]
    stamp_review_microstructure(
        dest, option_side=option_side, overwrite=overwrite, sources=bags
    )
