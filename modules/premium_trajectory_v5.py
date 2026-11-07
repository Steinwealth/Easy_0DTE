"""
Owned-contract premium trajectory for PO entry and exit snapshots (schema v5).

State anchors are current / ~30s ago / ~5m ago. Derived movement always names
its horizon (``premium_change_pct_30s``, never a bare ``premium_change_pct``).

``premium`` is the executable owned-contract mark (path ``mark``, else bid).
``option_bid`` / ``option_mid`` / ``option_ask`` stay separate so a spread
change is not mistaken for premium movement.

Lookbacks use the open trade's premium_path, earlier chain bids already recorded for the symbol, and the snapshot row as "now". Underlying 30s and 5m can also come from the discovery equity marks. A missing prior print stays null.
Slope, R², acceleration, and 5m sign flips read only the horizon window
(``now - horizon <= t <= now + 0.25``). High, low, drawdown, recovery, and
anchors still see the full owned path.
A historical tick older than 15s of quote age is dropped. An anchor is null
when no clean tick sits inside the tolerance of that horizon. Slope and R²
need at least three clean points spanning half the window.

Observe-only. These keys do not gate entries or exits.

``premium_trajectory_quality`` is ``insufficient`` when no quote is usable,
``current_only`` when neither slope exists, ``ok_30s`` when only the
30-second slope exists, ``ok_5m_only`` when only the 5-minute slope exists,
and ``ok_5m`` when both exist.
"""

from __future__ import annotations

import bisect
import sys
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

FEATURE_KEYS_PREMIUM_TRAJECTORY_V5: tuple[str, ...] = (
    "premium",
    "premium_30s_ago",
    "premium_5m_ago",
    "option_bid",
    "option_bid_30s_ago",
    "option_bid_5m_ago",
    "option_mid",
    "option_mid_30s_ago",
    "option_mid_5m_ago",
    "option_ask",
    "option_ask_30s_ago",
    "option_ask_5m_ago",
    "underlying_price_30s_ago",
    "underlying_price_5m_ago",
    "option_bid_size_30s_ago",
    "option_bid_size_5m_ago",
    "option_ask_size_30s_ago",
    "option_ask_size_5m_ago",
    "option_l1_imbalance_30s_ago",
    "option_l1_imbalance_5m_ago",
    "option_spread_pct",
    "option_spread_pct_30s_ago",
    "option_spread_pct_5m_ago",
    "option_spread_to_premium",
    "option_spread_to_premium_30s_ago",
    "option_spread_to_premium_5m_ago",
    "implied_volatility_30s_ago",
    "implied_volatility_5m_ago",
    "delta_30s_ago",
    "delta_5m_ago",
    "premium_change_pct_30s",
    "premium_change_pct_5m",
    "premium_slope_30s",
    "premium_slope_5m",
    "premium_slope_r2_30s",
    "premium_slope_r2_5m",
    "underlying_change_pct_30s",
    "underlying_change_pct_5m",
    "premium_ul_elasticity_30s",
    "premium_ul_elasticity_5m",
    "premium_hwm",
    "premium_drawdown_from_hwm_pct",
    "seconds_since_hwm",
    "premium_lwm",
    "premium_recovery_from_lwm_pct",
    "seconds_since_lwm",
    "premium_negative_velocity_streak_sec",
    "premium_positive_velocity_streak_sec",
    "premium_velocity_sign_flips_5m",
    "premium_ul_direction_agree",
    "premium_acceleration_30s_vs_5m",
    "premium_trajectory_quality",
)

_HORIZON_30 = 30.0
_HORIZON_5M = 300.0
_TOL_30 = 10.0
_TOL_5M = 45.0
_MIN_AGE_30 = 15.0
_MIN_AGE_5M = 150.0
_SLOPE_MIN_POINTS = 3
_SLOPE_MIN_SPAN_30 = 15.0
_SLOPE_MIN_SPAN_5M = 150.0
_QUOTE_STALE_SEC = 15.0
_FLAT_PREMIUM = 1e-9
_MOVE_EPS = 1e-4


def _f(val: Any) -> Optional[float]:
    if val is None or isinstance(val, bool):
        return None
    try:
        out = float(val)
    except (TypeError, ValueError):
        return None
    if out != out:  # NaN
        return None
    return out


def _positive(raw: Dict[str, Any], *keys: str) -> Optional[float]:
    for key in keys:
        val = _f(raw.get(key))
        if val is not None and val > 0:
            return val
    return None


def _epoch(val: Any) -> Optional[float]:
    if isinstance(val, datetime):
        dt = val if val.tzinfo else val.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        t = float(val)
        if t > 1e12:
            t /= 1000.0
        if t > 0:
            return t
        return None
    if isinstance(val, str) and val.strip():
        try:
            dt = datetime.fromisoformat(val.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    return None


def _quote_age_sec(raw: Dict[str, Any]) -> Optional[float]:
    age = _f(raw.get("quote_age_seconds"))
    if age is None:
        ms = _f(raw.get("quote_age_ms"))
        if ms is not None:
            age = ms / 1000.0
    return age


def _l1_imbalance(bid_size: Optional[float], ask_size: Optional[float]) -> Optional[float]:
    if bid_size is None or ask_size is None:
        return None
    denom = float(bid_size) + float(ask_size)
    if denom <= 0:
        return None
    return (float(bid_size) - float(ask_size)) / denom


def _spread_pct(bid: Optional[float], ask: Optional[float], mid: Optional[float]) -> Optional[float]:
    if bid is None or ask is None or mid is None or mid <= 0 or ask < bid:
        return None
    return (ask - bid) / mid


def _spread_to_premium(bid: Optional[float], ask: Optional[float], premium: Optional[float]) -> Optional[float]:
    if bid is None or ask is None or premium is None or premium <= 0 or ask < bid:
        return None
    return (ask - bid) / premium


def _change_pct(now_v: Optional[float], ago_v: Optional[float]) -> Optional[float]:
    if now_v is None or ago_v is None or ago_v == 0:
        return None
    return (float(now_v) - float(ago_v)) / float(ago_v)


def normalize_quote_sample(
    raw: Dict[str, Any],
    *,
    now_epoch: Optional[float] = None,
    allow_stale: bool = False,
) -> Optional[Dict[str, Any]]:
    """One clean owned-contract observation. ``premium`` prefers mark, else bid, else mid."""
    if not isinstance(raw, dict):
        return None
    age = _quote_age_sec(raw)
    if not allow_stale and age is not None and age > _QUOTE_STALE_SEC:
        return None
    bid = _positive(raw, "option_bid", "representative_bid", "bid")
    ask = _positive(raw, "option_ask", "representative_ask", "ask")
    mid = _positive(raw, "option_mid", "mid", "representative_price")
    if mid is None and bid is not None and ask is not None and ask >= bid:
        mid = (bid + ask) / 2.0
    premium = _positive(raw, "premium", "mark", "executable_exit_price")
    if premium is None:
        premium = bid if bid is not None else mid
    if premium is None or premium <= 0:
        return None
    t = _epoch(
        raw.get("t")
        or raw.get("observation_timestamp")
        or raw.get("timestamp")
    )
    if t is None:
        t = now_epoch
    if t is None:
        return None
    return {
        "t": float(t),
        "premium": float(premium),
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "underlying": _positive(raw, "underlying_price", "underlying_last", "exit_underlying"),
        "bid_size": _f(raw.get("option_bid_size") if raw.get("option_bid_size") is not None else raw.get("bid_size")),
        "ask_size": _f(raw.get("option_ask_size") if raw.get("option_ask_size") is not None else raw.get("ask_size")),
        "iv": _positive(raw, "implied_volatility", "iv"),
        "delta": _f(raw.get("delta") if raw.get("delta") is not None else raw.get("representative_delta")),
    }


def _premium_path_for_trade(system: Any, trade_id: str) -> Sequence[Any]:
    """Live premium_path for this trade.

    The flight recorder is the process singleton from ``get_profit_optimizer()``.
    It is not stored on ``system.profit_optimizer``. Entry, exit, and 20p stamps
    must read that same recorder or every lookback stays ``current_only``.
    """
    tid = str(trade_id or "").strip()
    if not tid:
        return ()
    candidates: List[Any] = []
    if system is not None:
        candidates.append(getattr(system, "profit_optimizer", None))
        candidates.append(getattr(system, "_profit_optimizer", None))
        getter = getattr(system, "_get_profit_optimizer", None)
        if callable(getter):
            try:
                candidates.append(getter())
            except Exception:
                pass
    mod = sys.modules.get("profit_optimizer.profit_optimizer")
    getter = getattr(mod, "get_profit_optimizer", None) if mod is not None else None
    if not callable(getter):
        try:
            from profit_optimizer.profit_optimizer import get_profit_optimizer as getter
        except Exception:
            getter = None
    if callable(getter):
        try:
            candidates.append(getter())
        except Exception:
            pass
    for po in candidates:
        recorder = getattr(po, "recorder", None) if po is not None else None
        records = getattr(recorder, "_records", None) if recorder is not None else None
        if not isinstance(records, dict):
            continue
        rec = records.get(tid)
        raw_path = getattr(rec, "premium_path", None) if rec is not None else None
        if isinstance(raw_path, list) and raw_path:
            return raw_path
    return ()


def collect_premium_trajectory_samples(
    system: Any,
    row: Dict[str, Any],
    *,
    now_epoch: float,
) -> List[Dict[str, Any]]:
    """Path ticks up to ``now_epoch``, with the snapshot row pinned as the current quote."""
    samples: List[Dict[str, Any]] = []
    tid = str((row or {}).get("trade_id") or "").strip()
    path: Sequence[Any] = _premium_path_for_trade(system, tid) if tid else ()
    for tick in path:
        if not isinstance(tick, dict):
            continue
        sample = normalize_quote_sample(tick, allow_stale=False)
        if sample is None or sample["t"] > float(now_epoch) + 0.25:
            continue
        samples.append(sample)
    sym = str((row or {}).get("symbol") or "").strip().upper()
    row_strike = _row_contract_strike(row)
    if sym and system is not None:
        try:
            from central_tech.chain_ring import chain_ring_premium_samples
        except Exception:
            chain_ring_premium_samples = None  # type: ignore
        if chain_ring_premium_samples is not None:
            for prior in chain_ring_premium_samples(system, sym):
                # Contract identity: when the row names its strike, a symbol-ring
                # quote for another (or an unknown) strike is not this contract's
                # history. Do not borrow it.
                if row_strike is not None and not _same_strike(prior.get("strike"), row_strike):
                    continue
                sample = normalize_quote_sample(prior, allow_stale=False)
                if sample is None or sample["t"] > float(now_epoch) + 0.25:
                    continue
                samples.append(sample)
    # Exact-contract book ring (central_tech): every live chain fetch keeps the
    # near-spot book with sizes, IV and delta. This is the only pre-fill history
    # for the selected contract, so entry *_30s_ago / *_5m_ago (incl. option L1)
    # can fill when the chain was fetched ~30s / ~5m before the decision.
    side_tok = _side_token(row)
    if sym and row_strike is not None and side_tok:
        try:
            from central_tech.contract_book_ring import contract_quote_priors
        except Exception:
            contract_quote_priors = None  # type: ignore
        if contract_quote_priors is not None:
            for prior in contract_quote_priors(
                sym, option_type=side_tok, strike=row_strike, before_epoch=now_epoch
            ):
                sample = normalize_quote_sample(prior, allow_stale=False)
                if sample is None or sample["t"] > float(now_epoch) + 0.25:
                    continue
                samples.append(sample)
    current = normalize_quote_sample(row, now_epoch=now_epoch, allow_stale=True)
    if current is not None:
        row_ts = _epoch(row.get("observation_timestamp") or row.get("timestamp") or row.get("t"))
        if row_ts is None or row_ts > float(now_epoch) + 0.25:
            current["t"] = float(now_epoch)
        samples = [s for s in samples if abs(s["t"] - current["t"]) > 1.0]
        samples.append(current)
    samples.sort(key=lambda s: float(s["t"]))
    return samples


_ROW_STRIKE_KEYS = (
    "po_options_owned_strike",
    "strike",
    "matched_strike",
    "listed_exchange_strike",
    "selected_strike",
    "option_strike",
)


def _row_contract_strike(row: Optional[Dict[str, Any]]) -> Optional[float]:
    """Strike of the contract this snapshot is about (owned / selected)."""
    if not isinstance(row, dict):
        return None
    for key in _ROW_STRIKE_KEYS:
        val = _f(row.get(key))
        if val is not None and val > 0:
            return val
    md = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    val = _f(md.get("strike"))
    if val is not None and val > 0:
        return val
    return None


def _same_strike(a: Any, b: Any) -> bool:
    fa = _f(a)
    fb = _f(b)
    if fa is None or fb is None:
        return False
    return abs(fa - fb) <= 0.011


def _anchor(
    samples: Sequence[Dict[str, Any]],
    *,
    now: float,
    horizon: float,
    tolerance: float,
    min_age: float,
) -> Optional[Dict[str, Any]]:
    target = float(now) - float(horizon)
    best = None
    best_abs = None
    for sample in samples:
        age = float(now) - float(sample["t"])
        if age < min_age or sample["t"] > float(now) + 0.25:
            continue
        dist = abs(float(sample["t"]) - target)
        if dist > tolerance:
            continue
        if best_abs is None or dist < best_abs:
            best = sample
            best_abs = dist
    return best


def _linreg(samples: Sequence[Dict[str, Any]]) -> tuple[Optional[float], Optional[float]]:
    """OLS premium vs seconds. Slope is premium per second. R² is 1 on a flat line."""
    if len(samples) < _SLOPE_MIN_POINTS:
        return None, None
    xs = [float(s["t"]) for s in samples]
    ys = [float(s["premium"]) for s in samples]
    n = float(len(xs))
    mx = sum(xs) / n
    my = sum(ys) / n
    varx = sum((x - mx) ** 2 for x in xs)
    if varx <= 1e-12:
        return None, None
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    slope = cov / varx
    vary = sum((y - my) ** 2 for y in ys)
    if vary <= 1e-18:
        return slope, 1.0
    ss_res = sum((y - (my + slope * (x - mx))) ** 2 for x, y in zip(xs, ys))
    r2 = 1.0 - (ss_res / vary)
    if r2 < 0.0:
        r2 = 0.0
    elif r2 > 1.0:
        r2 = 1.0
    return slope, r2


def _sample_time(sample: Dict[str, Any]) -> float:
    return float(sample["t"])


def _bounded_time_window(
    samples: Sequence[Dict[str, Any]],
    *,
    now: float,
    horizon: float,
) -> Sequence[Dict[str, Any]]:
    """Quotes with ``now - horizon <= t <= now + 0.25``, in existing order.

    ``derive_premium_trajectory_v5`` sorts by timestamp before slope and
    sign-flip. On that ordered list the cut is a binary search, so prints
    older than the horizon are not walked. Inclusive bounds match the
    previous linear filter. Equal timestamps keep their relative order.
    Irregular gaps stay in the returned slice; the fit still uses each
    quote's own timestamp.
    """
    if not samples:
        return samples
    lo_t = float(now) - float(horizon)
    hi_t = float(now) + 0.25
    lo = bisect.bisect_left(samples, lo_t, key=_sample_time)
    hi = bisect.bisect_right(samples, hi_t, key=_sample_time)
    if lo == 0 and hi == len(samples):
        return samples
    return samples[lo:hi]


def _window_slope(
    samples: Sequence[Dict[str, Any]],
    *,
    now: float,
    horizon: float,
    min_span: float,
) -> tuple[Optional[float], Optional[float]]:
    window = _bounded_time_window(samples, now=now, horizon=horizon)
    if len(window) < _SLOPE_MIN_POINTS:
        return None, None
    span = float(window[-1]["t"]) - float(window[0]["t"])
    if span < min_span:
        return None, None
    return _linreg(window)


def _velocity_streaks(samples: Sequence[Dict[str, Any]]) -> tuple[Optional[float], Optional[float]]:
    if len(samples) < 2:
        return None, None
    sign = 0
    i = len(samples) - 1
    while i > 0:
        delta = float(samples[i]["premium"]) - float(samples[i - 1]["premium"])
        if abs(delta) <= _FLAT_PREMIUM:
            i -= 1
            continue
        sign = 1 if delta > 0 else -1
        break
    if sign == 0:
        return 0.0, 0.0
    i = len(samples) - 1
    start = i
    while i > 0:
        delta = float(samples[i]["premium"]) - float(samples[i - 1]["premium"])
        if sign < 0 and delta > _FLAT_PREMIUM:
            break
        if sign > 0 and delta < -_FLAT_PREMIUM:
            break
        start = i - 1
        i -= 1
    dur = float(samples[-1]["t"]) - float(samples[start]["t"])
    if sign < 0:
        return dur, 0.0
    return 0.0, dur


def _sign_flips(samples: Sequence[Dict[str, Any]], *, now: float) -> Optional[int]:
    window = _bounded_time_window(samples, now=now, horizon=_HORIZON_5M)
    if len(window) < 2:
        return None
    flips = 0
    prev = 0
    for i in range(1, len(window)):
        delta = float(window[i]["premium"]) - float(window[i - 1]["premium"])
        if abs(delta) <= _FLAT_PREMIUM:
            continue
        sign = 1 if delta > 0 else -1
        if prev != 0 and sign != prev:
            flips += 1
        prev = sign
    return flips


def _favorable_ul(ul_now: Optional[float], ul_ago: Optional[float], side: str) -> Optional[float]:
    if ul_now is None or ul_ago is None or ul_now <= 0 or ul_ago <= 0:
        return None
    if side == "PUT":
        return (ul_ago / ul_now) - 1.0
    if side == "CALL":
        return (ul_now / ul_ago) - 1.0
    return None


def _side_token(row: Optional[Dict[str, Any]]) -> str:
    if not isinstance(row, dict):
        return ""
    raw = str(
        row.get("option_side")
        or row.get("expected_option_side")
        or row.get("side")
        or row.get("option_type")
        or row.get("option_type_label")
        or ""
    ).strip().upper()
    if "PUT" in raw or raw in ("SHORT", "BEAR", "BEARISH", "P"):
        return "PUT"
    if "CALL" in raw or raw in ("LONG", "BULL", "BULLISH", "C", "UP"):
        return "CALL"
    return ""


def _empty() -> Dict[str, Any]:
    return {key: None for key in FEATURE_KEYS_PREMIUM_TRAJECTORY_V5}


def _anchor_fields(prefix_map: Dict[str, str], sample: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Map a lookback sample onto the ``*_30s_ago`` / ``*_5m_ago`` keys."""
    out = {dest: None for dest in prefix_map.values()}
    if sample is None:
        return out
    bid = sample.get("bid")
    ask = sample.get("ask")
    mid = sample.get("mid")
    premium = sample.get("premium")
    values = {
        "premium": premium,
        "option_bid": bid,
        "option_mid": mid,
        "option_ask": ask,
        "underlying_price": sample.get("underlying"),
        "option_bid_size": sample.get("bid_size"),
        "option_ask_size": sample.get("ask_size"),
        "option_l1_imbalance": _l1_imbalance(sample.get("bid_size"), sample.get("ask_size")),
        "option_spread_pct": _spread_pct(bid, ask, mid),
        "option_spread_to_premium": _spread_to_premium(bid, ask, premium),
        "implied_volatility": sample.get("iv"),
        "delta": sample.get("delta"),
    }
    for src, dest in prefix_map.items():
        out[dest] = values.get(src)
    return out


_AGO_30 = {
    "premium": "premium_30s_ago",
    "option_bid": "option_bid_30s_ago",
    "option_mid": "option_mid_30s_ago",
    "option_ask": "option_ask_30s_ago",
    "underlying_price": "underlying_price_30s_ago",
    "option_bid_size": "option_bid_size_30s_ago",
    "option_ask_size": "option_ask_size_30s_ago",
    "option_l1_imbalance": "option_l1_imbalance_30s_ago",
    "option_spread_pct": "option_spread_pct_30s_ago",
    "option_spread_to_premium": "option_spread_to_premium_30s_ago",
    "implied_volatility": "implied_volatility_30s_ago",
    "delta": "delta_30s_ago",
}
_AGO_5M = {
    "premium": "premium_5m_ago",
    "option_bid": "option_bid_5m_ago",
    "option_mid": "option_mid_5m_ago",
    "option_ask": "option_ask_5m_ago",
    "underlying_price": "underlying_price_5m_ago",
    "option_bid_size": "option_bid_size_5m_ago",
    "option_ask_size": "option_ask_size_5m_ago",
    "option_l1_imbalance": "option_l1_imbalance_5m_ago",
    "option_spread_pct": "option_spread_pct_5m_ago",
    "option_spread_to_premium": "option_spread_to_premium_5m_ago",
    "implied_volatility": "implied_volatility_5m_ago",
    "delta": "delta_5m_ago",
}


def derive_premium_trajectory_v5(
    samples: Sequence[Dict[str, Any]],
    *,
    now: float,
    side: str = "",
) -> Dict[str, Any]:
    """Fill every v5 key. Missing history stays null. Quality names how much was usable."""
    out = _empty()
    usable = [s for s in samples if s.get("premium") is not None and float(s["t"]) <= float(now) + 0.25]
    usable.sort(key=lambda s: float(s["t"]))
    if not usable:
        out["premium_trajectory_quality"] = "insufficient"
        return out

    current = usable[-1]
    bid = current.get("bid")
    ask = current.get("ask")
    mid = current.get("mid")
    premium = float(current["premium"])
    out["premium"] = premium
    out["option_bid"] = bid
    out["option_ask"] = ask
    out["option_mid"] = mid
    out["option_spread_pct"] = _spread_pct(bid, ask, mid)
    out["option_spread_to_premium"] = _spread_to_premium(bid, ask, premium)

    ago30 = _anchor(
        usable, now=now, horizon=_HORIZON_30, tolerance=_TOL_30, min_age=_MIN_AGE_30
    )
    ago5 = _anchor(
        usable, now=now, horizon=_HORIZON_5M, tolerance=_TOL_5M, min_age=_MIN_AGE_5M
    )
    out.update(_anchor_fields(_AGO_30, ago30))
    out.update(_anchor_fields(_AGO_5M, ago5))

    out["premium_change_pct_30s"] = _change_pct(premium, out["premium_30s_ago"])
    out["premium_change_pct_5m"] = _change_pct(premium, out["premium_5m_ago"])
    out["underlying_change_pct_30s"] = _change_pct(current.get("underlying"), out["underlying_price_30s_ago"])
    out["underlying_change_pct_5m"] = _change_pct(current.get("underlying"), out["underlying_price_5m_ago"])

    def _elasticity(prem_chg: Optional[float], ul_chg: Optional[float]) -> Optional[float]:
        if prem_chg is None or ul_chg is None or abs(ul_chg) < _MOVE_EPS:
            return None
        return prem_chg / ul_chg

    out["premium_ul_elasticity_30s"] = _elasticity(
        out["premium_change_pct_30s"], out["underlying_change_pct_30s"]
    )
    out["premium_ul_elasticity_5m"] = _elasticity(
        out["premium_change_pct_5m"], out["underlying_change_pct_5m"]
    )

    slope30, r2_30 = _window_slope(
        usable, now=now, horizon=_HORIZON_30, min_span=_SLOPE_MIN_SPAN_30
    )
    slope5, r2_5 = _window_slope(
        usable, now=now, horizon=_HORIZON_5M, min_span=_SLOPE_MIN_SPAN_5M
    )
    out["premium_slope_30s"] = slope30
    out["premium_slope_r2_30s"] = r2_30
    out["premium_slope_5m"] = slope5
    out["premium_slope_r2_5m"] = r2_5
    if slope30 is not None and slope5 is not None:
        out["premium_acceleration_30s_vs_5m"] = slope30 - slope5

    hwm_s = max(usable, key=lambda s: (float(s["premium"]), float(s["t"])))
    lwm_s = min(usable, key=lambda s: (float(s["premium"]), -float(s["t"])))
    hwm = float(hwm_s["premium"])
    lwm = float(lwm_s["premium"])
    out["premium_hwm"] = hwm
    out["premium_lwm"] = lwm
    out["seconds_since_hwm"] = float(now) - float(hwm_s["t"])
    out["seconds_since_lwm"] = float(now) - float(lwm_s["t"])
    out["premium_drawdown_from_hwm_pct"] = (hwm - premium) / hwm if hwm > 0 else None
    out["premium_recovery_from_lwm_pct"] = (premium - lwm) / lwm if lwm > 0 else None

    neg, pos = _velocity_streaks(usable)
    out["premium_negative_velocity_streak_sec"] = neg
    out["premium_positive_velocity_streak_sec"] = pos
    out["premium_velocity_sign_flips_5m"] = _sign_flips(usable, now=now)

    fav = _favorable_ul(current.get("underlying"), out["underlying_price_30s_ago"], side)
    prem_chg = out["premium_change_pct_30s"]
    if (
        fav is not None
        and prem_chg is not None
        and abs(fav) >= _MOVE_EPS
        and abs(prem_chg) >= _MOVE_EPS
    ):
        out["premium_ul_direction_agree"] = (prem_chg > 0) == (fav > 0)

    # Label only. Slopes, anchors, and high/low are already written.
    # ok_5m_only is a 5-minute fit with no 30-second fit. Older rows stored
    # that case as current_only; this does not rewrite those files.
    if slope30 is not None and slope5 is not None:
        out["premium_trajectory_quality"] = "ok_5m"
    elif slope5 is not None:
        out["premium_trajectory_quality"] = "ok_5m_only"
    elif slope30 is not None:
        out["premium_trajectory_quality"] = "ok_30s"
    else:
        out["premium_trajectory_quality"] = "current_only"
    return out


def _fill_underlying_lookback(row: Dict[str, Any], blob: Dict[str, Any], *, now_epoch: float) -> None:
    """30s and 5m underlying prints from the discovery mark ring when the option path has none."""
    if blob.get("underlying_price_30s_ago") is not None and blob.get("underlying_price_5m_ago") is not None:
        return
    sym = str((row or {}).get("symbol") or "").strip().upper()
    if not sym:
        return
    try:
        from .spy_session_quote import underlying_mark_ago
    except Exception:
        return
    if blob.get("underlying_price_30s_ago") is None:
        ago = underlying_mark_ago(
            sym, now_epoch=now_epoch, ago_sec=_HORIZON_30, tolerance_sec=_TOL_30
        )
        if ago is not None:
            blob["underlying_price_30s_ago"] = ago
    if blob.get("underlying_price_5m_ago") is None:
        ago = underlying_mark_ago(
            sym, now_epoch=now_epoch, ago_sec=_HORIZON_5M, tolerance_sec=_TOL_5M
        )
        if ago is not None:
            blob["underlying_price_5m_ago"] = ago


def _current_underlying(samples: Sequence[Dict[str, Any]], row: Dict[str, Any]) -> Optional[float]:
    if samples:
        cur = samples[-1].get("underlying")
        if cur is not None and cur > 0:
            return float(cur)
    return _positive(row or {}, "underlying_price", "current_price", "underlying_last", "exit_underlying")


def _derive_underlying_change_fields(
    blob: Dict[str, Any],
    *,
    current_underlying: Optional[float],
    side: str,
) -> None:
    """Underlying 30s / 5m change and the fields built on it.

    ``derive_premium_trajectory_v5`` computes these from option-path anchors only.
    ``_fill_underlying_lookback`` then fills ``underlying_price_30s_ago`` /
    ``_5m_ago`` from the discovery equity-mark ring, but the change, elasticity
    and direction-agree keys were never recomputed. Oct 5: underlying_price_5m_ago
    filled on 64% of handoffs while underlying_change_pct_5m stayed 100% null.
    Only missing keys are filled. A missing anchor stays None (warm-up).
    """
    if current_underlying is None or current_underlying <= 0:
        return
    for horizon in ("30s", "5m"):
        chg_key = f"underlying_change_pct_{horizon}"
        ago = _f(blob.get(f"underlying_price_{horizon}_ago"))
        if blob.get(chg_key) is None and ago is not None and ago > 0:
            blob[chg_key] = _change_pct(current_underlying, ago)
        el_key = f"premium_ul_elasticity_{horizon}"
        prem_chg = _f(blob.get(f"premium_change_pct_{horizon}"))
        ul_chg = _f(blob.get(chg_key))
        if (
            blob.get(el_key) is None
            and prem_chg is not None
            and ul_chg is not None
            and abs(ul_chg) >= _MOVE_EPS
        ):
            blob[el_key] = prem_chg / ul_chg
    if blob.get("premium_ul_direction_agree") is None:
        fav = _favorable_ul(current_underlying, _f(blob.get("underlying_price_30s_ago")), side)
        prem_chg = _f(blob.get("premium_change_pct_30s"))
        if (
            fav is not None
            and prem_chg is not None
            and abs(fav) >= _MOVE_EPS
            and abs(prem_chg) >= _MOVE_EPS
        ):
            blob["premium_ul_direction_agree"] = (prem_chg > 0) == (fav > 0)


def apply_premium_trajectory_v5(
    system: Any,
    row: Dict[str, Any],
    *,
    now_epoch: float,
    overwrite: bool = False,
) -> int:
    """Stamp v5 keys onto a snapshot row. Overwrite replaces prior anchors, including nulls."""
    if not isinstance(row, dict):
        return 0
    samples = collect_premium_trajectory_samples(system, row, now_epoch=now_epoch)
    side = _side_token(row)
    blob = derive_premium_trajectory_v5(samples, now=now_epoch, side=side)
    _fill_underlying_lookback(row, blob, now_epoch=now_epoch)
    _derive_underlying_change_fields(
        blob, current_underlying=_current_underlying(samples, row), side=side
    )
    n = 0
    for key, val in blob.items():
        if not overwrite and row.get(key) is not None:
            continue
        if row.get(key) != val:
            row[key] = val
            n += 1
    return n
