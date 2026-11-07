"""Admit-time session tech that used to live in the Priority Optimizer observe module.

VWAP distance, price/volume efficiency, relative volume, the equity L1 ring,
break-distance fraction, and the RSI/ATR gap-fill. The session-tech hub
re-exports the public functions. No Priority Optimizer import.
"""

from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

_L1_RING_MAX = 120
_RTH_MINUTES = 390.0
_UNDERLYING_QUOTE_STALE_SEC = 15.0

def _f(val: Any) -> Optional[float]:
    if val is None or val == "":
        return None
    try:
        out = float(val)
    except (TypeError, ValueError):
        return None
    if out != out or math.isinf(out):
        return None
    return out


def _i(val: Any) -> Optional[int]:
    f = _f(val)
    if f is None:
        return None
    try:
        return int(f)
    except (TypeError, ValueError):
        return None


def l1_imbalance(bid_size: Any, ask_size: Any) -> Optional[float]:
    b = _f(bid_size)
    a = _f(ask_size)
    if b is None or a is None:
        return None
    denom = b + a
    if denom <= 0:
        return None
    return (b - a) / denom


def _ensure_equity_l1_ring(system: Any) -> Dict[str, List[Dict[str, Any]]]:
    ring = getattr(system, "_equity_l1_ring", None)
    if not isinstance(ring, dict):
        ring = {}
        try:
            setattr(system, "_equity_l1_ring", ring)
        except Exception:
            pass
    return ring


def record_equity_l1_sample(system: Any, symbol: str, quote: Any) -> None:
    """Append one L1 sample from an existing equity quote poll (no new API)."""
    if system is None or not isinstance(quote, dict):
        return
    sym = str(symbol or quote.get("symbol") or "").strip().upper()
    if not sym:
        return
    bid = _f(quote.get("bid"))
    ask = _f(quote.get("ask"))
    last = _f(quote.get("last") or quote.get("price"))
    bid_size = _i(quote.get("bid_size"))
    ask_size = _i(quote.get("ask_size"))
    if bid is None and ask is None and last is None and bid_size is None and ask_size is None:
        return
    mid = None
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        mid = (bid + ask) / 2.0
    elif last is not None and last > 0:
        mid = last
    sample = {
        "t": time.time(),
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "last": last,
        "bid_size": bid_size,
        "ask_size": ask_size,
        "session_volume": _f(
            quote.get("totalVolume")
            if quote.get("totalVolume") is not None
            else quote.get("volume")
        ),
        "average_volume": _f(quote.get("average_volume")),
        "quote_ts": quote.get("date_time_utc") or quote.get("quote_ts") or quote.get("timestamp"),
    }
    ring = _ensure_equity_l1_ring(system)
    buf = ring.setdefault(sym, [])
    buf.append(sample)
    if len(buf) > _L1_RING_MAX:
        del buf[: len(buf) - _L1_RING_MAX]


def record_equity_l1_from_quotes(system: Any, quotes: Optional[Dict[str, Any]]) -> int:
    if not isinstance(quotes, dict) or not quotes:
        return 0
    n = 0
    for raw_sym, quote in quotes.items():
        if not isinstance(quote, dict):
            continue
        before = len(_ensure_equity_l1_ring(system).get(str(raw_sym).upper(), []))
        record_equity_l1_sample(system, str(raw_sym), quote)
        after = len(_ensure_equity_l1_ring(system).get(str(raw_sym).upper(), []))
        if after > before:
            n += 1
    return n


def _sample_near(buf: Sequence[Dict[str, Any]], age_sec: float) -> Optional[Dict[str, Any]]:
    if not buf:
        return None
    now = time.time()
    target = now - float(age_sec)
    best = None
    best_abs = None
    for s in buf:
        t = _f(s.get("t"))
        if t is None:
            continue
        # Prefer samples at or before target within a generous window.
        if t > now + 0.5:
            continue
        dist = abs(t - target)
        if t <= target + 1.0:
            score = dist
        else:
            score = dist + 1000.0  # penalize samples newer than target
        if best_abs is None or score < best_abs:
            best_abs = score
            best = s
    if best is None:
        return None
    # Require roughly the right horizon (±70% of age, min 3s).
    tol = max(3.0, float(age_sec) * 0.7)
    if abs(float(best["t"]) - target) > tol and float(age_sec) >= 5.0:
        # Fall back to oldest sample if ring is shorter than horizon.
        oldest = buf[0]
        if _f(oldest.get("t")) is not None and now - float(oldest["t"]) >= float(age_sec) * 0.4:
            return oldest
        return None
    return best


def _sample_near_at(
    buf: Sequence[Dict[str, Any]],
    as_of_epoch: float,
    age_sec: float,
) -> Optional[Dict[str, Any]]:
    if not buf:
        return None
    target = float(as_of_epoch) - float(age_sec)
    best = None
    best_abs = None
    for s in buf:
        t = _f(s.get("t"))
        if t is None or t > float(as_of_epoch) + 0.25:
            continue
        dist = abs(t - target)
        if best_abs is None or dist < best_abs:
            best_abs = dist
            best = s
    if best is None:
        return None
    tol = max(3.0, float(age_sec) * 0.7)
    if best_abs is not None and best_abs > tol and float(age_sec) >= 5.0:
        oldest = buf[0]
        ot = _f(oldest.get("t"))
        if ot is not None and float(as_of_epoch) - ot >= float(age_sec) * 0.4:
            return oldest
        return None
    return best


def _pt_minutes_since_rth_open(now: Optional[datetime] = None) -> Optional[float]:
    try:
        from zoneinfo import ZoneInfo

        loc = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("America/Los_Angeles"))
    except Exception:
        return None
    open_m = 6 * 60 + 30
    cur_m = loc.hour * 60 + loc.minute + loc.second / 60.0
    elapsed = cur_m - open_m
    if elapsed < 0:
        return 0.0
    if elapsed > _RTH_MINUTES:
        return _RTH_MINUTES
    return float(elapsed)


def _expected_session_fraction(now: Optional[datetime] = None) -> Optional[float]:
    """
    Linear RTH fraction heuristic for time-adjusted relative volume.

    Not a true historical TOD volume curve — fallback until multi-day TOD
    baselines exist. PO should treat as ``relative_volume_baseline=linear_rth``.
    """
    elapsed = _pt_minutes_since_rth_open(now)
    if elapsed is None:
        return None
    return max(1.0 / _RTH_MINUTES, min(1.0, elapsed / _RTH_MINUTES))


def _bars_for_symbol(system: Any, symbol: str) -> List[Dict[str, Any]]:
    cache = getattr(system, "_session_tech_bar_cache", None)
    if not isinstance(cache, dict):
        return []
    bars = cache.get(str(symbol or "").strip().upper()) or []
    if not isinstance(bars, list):
        return []
    return [b for b in bars if isinstance(b, dict)]


def _parse_ts(raw: Any) -> Optional[datetime]:
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        return raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, (int, float)):
        epoch = float(raw)
        if epoch > 1e12:
            epoch /= 1000.0
        try:
            return datetime.fromtimestamp(epoch, tz=timezone.utc)
        except Exception:
            return None
    if isinstance(raw, str):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except Exception:
            return None
    return None


def closed_session_bars(
    bars: Sequence[Dict[str, Any]],
    *,
    as_of: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """
    Drop the currently-forming PT minute so entry research does not include
    post-decision ticks inside the open bar.
    """
    if not bars:
        return []
    as_of_dt = as_of or datetime.now(timezone.utc)
    if as_of_dt.tzinfo is None:
        as_of_dt = as_of_dt.replace(tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo

        as_of_pt = as_of_dt.astimezone(ZoneInfo("America/Los_Angeles"))
        open_key = (as_of_pt.date(), as_of_pt.hour, as_of_pt.minute)
    except Exception:
        open_key = None

    out: List[Dict[str, Any]] = []
    for b in bars:
        ts = _parse_ts(b.get("timestamp") or b.get("datetime"))
        if ts is None:
            out.append(b)
            continue
        if ts > as_of_dt:
            continue  # future leakage guard
        if open_key is not None:
            try:
                from zoneinfo import ZoneInfo

                loc = ts.astimezone(ZoneInfo("America/Los_Angeles"))
                key = (loc.date(), loc.hour, loc.minute)
                if key == open_key:
                    continue  # forming minute
            except Exception:
                pass
        out.append(b)
    return out


def _sum_volume(bars: Sequence[Dict[str, Any]], n: int) -> Optional[float]:
    if n <= 0 or not bars:
        return None
    chunk = bars[-n:]
    total = 0.0
    any_v = False
    for b in chunk:
        v = _f(b.get("volume"))
        if v is None:
            continue
        # Skip estimate/placeholder bars for participation research.
        if b.get("volume_estimate") or b.get("volume_placeholder"):
            continue
        total += max(0.0, v)
        any_v = True
    return total if any_v else None


def _close_n_ago(bars: Sequence[Dict[str, Any]], n: int) -> Optional[float]:
    if not bars or n < 0 or len(bars) <= n:
        return None
    return _f(bars[-(n + 1)].get("close") if n > 0 else bars[-1].get("close"))


def _bar_structure(bar: Optional[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    out: Dict[str, Optional[float]] = {
        "body_pct": None,
        "range_pct": None,
        "upper_wick_pct": None,
        "lower_wick_pct": None,
        "close_position": None,
    }
    if not isinstance(bar, dict):
        return out
    o = _f(bar.get("open"))
    h = _f(bar.get("high"))
    l = _f(bar.get("low"))
    c = _f(bar.get("close"))
    if None in (o, h, l, c) or h is None or l is None:
        return out
    rng = h - l
    if rng <= 0 or c is None or o is None:
        return out
    body = abs(c - o)
    upper = h - max(o, c)
    lower = min(o, c) - l
    out["body_pct"] = body / rng
    out["range_pct"] = (rng / c) if c > 0 else None
    out["upper_wick_pct"] = max(0.0, upper) / rng
    out["lower_wick_pct"] = max(0.0, lower) / rng
    out["close_position"] = (c - l) / rng
    return out


def _consecutive_directional_bars(bars: Sequence[Dict[str, Any]], lookback: int = 20) -> Optional[int]:
    if len(bars) < 2:
        return None
    chunk = bars[-lookback:]
    direction = 0
    count = 0
    for i in range(len(chunk) - 1, 0, -1):
        c0 = _f(chunk[i].get("close"))
        c1 = _f(chunk[i - 1].get("close"))
        if c0 is None or c1 is None:
            break
        d = 1 if c0 > c1 else (-1 if c0 < c1 else 0)
        if d == 0:
            break
        if direction == 0:
            direction = d
            count = 1
            continue
        if d != direction:
            break
        count += 1
    return count if count > 0 else 0


def _hh_ll_counts(bars: Sequence[Dict[str, Any]], lookback: int = 10) -> Tuple[Optional[int], Optional[int]]:
    if len(bars) < 2:
        return None, None
    chunk = bars[-lookback:]
    hh = 0
    ll = 0
    for i in range(1, len(chunk)):
        h0 = _f(chunk[i].get("high"))
        h1 = _f(chunk[i - 1].get("high"))
        l0 = _f(chunk[i].get("low"))
        l1 = _f(chunk[i - 1].get("low"))
        if h0 is not None and h1 is not None and h0 > h1:
            hh += 1
        if l0 is not None and l1 is not None and l0 < l1:
            ll += 1
    return hh, ll


def derive_participation_features(
    bars: Sequence[Dict[str, Any]],
    *,
    session_volume: Optional[float] = None,
    average_volume: Optional[float] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Participation proxies from 1m bars + cumulative session volume."""
    out: Dict[str, Any] = {
        "session_volume": _f(session_volume),
        "volume_delta_1m": None,
        "session_vs_avg_volume": None,
        "volume_1m": None,
        "volume_3m": None,
        "volume_5m": None,
        "volume_rate_1m": None,
        "volume_rate_3m": None,
        "volume_rate_5m": None,
        "volume_acceleration_1m_vs_5m": None,
        "volume_vs_prior_1m": None,
        "volume_vs_prior_5m": None,
        "volume_pct_of_day_5m": None,
        "relative_volume_time_adjusted": None,
        "relative_volume_baseline": None,
        "price_change_1m_pct": None,
        "price_change_3m_pct": None,
        "price_change_5m_pct": None,
        "price_volume_efficiency_1m": None,
        "price_volume_efficiency_3m": None,
        "price_volume_efficiency_5m": None,
    }
    v1 = _sum_volume(bars, 1)
    v3 = _sum_volume(bars, 3)
    v5 = _sum_volume(bars, 5)
    out["volume_1m"] = v1
    out["underlying_volume_1m"] = v1
    out["volume_3m"] = v3
    out["volume_5m"] = v5
    out["volume_delta_1m"] = v1
    if v1 is not None:
        out["volume_rate_1m"] = v1 / 60.0
    if v3 is not None:
        out["volume_rate_3m"] = v3 / 180.0
    if v5 is not None:
        out["volume_rate_5m"] = v5 / 300.0
    if out["volume_rate_1m"] is not None and out["volume_rate_5m"] is not None:
        if out["volume_rate_5m"] > 0:
            out["volume_acceleration_1m_vs_5m"] = out["volume_rate_1m"] / out["volume_rate_5m"]
        else:
            out["volume_acceleration_1m_vs_5m"] = None

    # Prior windows: previous 1m bar vs current; previous 5m block vs current 5m.
    if len(bars) >= 2:
        prior_1 = _f(bars[-2].get("volume"))
        if prior_1 is not None and prior_1 > 0 and v1 is not None:
            out["volume_vs_prior_1m"] = v1 / prior_1
    if len(bars) >= 10 and v5 is not None:
        prior_5 = _sum_volume(bars[-10:-5], 5)
        if prior_5 is not None and prior_5 > 0:
            out["volume_vs_prior_5m"] = v5 / prior_5

    sess = _f(session_volume)
    if sess is None and bars:
        # Sum trusted minute volumes as weak session proxy.
        tot = 0.0
        any_v = False
        for b in bars:
            v = _f(b.get("volume"))
            if v is None or b.get("volume_estimate") or b.get("volume_placeholder"):
                continue
            tot += max(0.0, v)
            any_v = True
        if any_v:
            sess = tot
            out["session_volume"] = sess
    avg = _f(average_volume)
    if sess is not None and avg is not None and avg > 0:
        out["session_vs_avg_volume"] = sess / avg
        frac = _expected_session_fraction(now)
        if frac is not None and frac > 0:
            expected = avg * frac
            if expected > 0:
                out["relative_volume_time_adjusted"] = sess / expected
                out["relative_volume_baseline"] = "linear_rth"
    if v5 is not None and sess is not None and sess > 0:
        out["volume_pct_of_day_5m"] = v5 / sess

    # Price × volume efficiency: |Δprice%| per share of recent volume (scaled ×1e6).
    c0 = _f(bars[-1].get("close")) if bars else None
    for n, key_p, key_e, vol in (
        (1, "price_change_1m_pct", "price_volume_efficiency_1m", v1),
        (3, "price_change_3m_pct", "price_volume_efficiency_3m", v3),
        (5, "price_change_5m_pct", "price_volume_efficiency_5m", v5),
    ):
        if c0 is None or len(bars) <= n:
            continue
        c_prev = _f(bars[-(n + 1)].get("close"))
        if c_prev is None or c_prev <= 0:
            continue
        chg = (c0 - c_prev) / c_prev * 100.0
        out[key_p] = chg
        if vol is not None and vol > 0:
            out[key_e] = abs(chg) / vol * 1_000_000.0
    return out


def derive_candle_structure_features(bars: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "entry_bar_body_pct": None,
        "entry_bar_range_pct": None,
        "entry_bar_upper_wick_pct": None,
        "entry_bar_lower_wick_pct": None,
        "entry_bar_close_position": None,
        "prior_bar_body_pct": None,
        "prior_bar_close_position": None,
        "consecutive_directional_bars": None,
        "recent_higher_high_count": None,
        "recent_lower_low_count": None,
    }
    if not bars:
        return out
    entry = _bar_structure(bars[-1])
    out["entry_bar_body_pct"] = entry["body_pct"]
    out["entry_bar_range_pct"] = entry["range_pct"]
    out["entry_bar_upper_wick_pct"] = entry["upper_wick_pct"]
    out["entry_bar_lower_wick_pct"] = entry["lower_wick_pct"]
    out["entry_bar_close_position"] = entry["close_position"]
    if len(bars) >= 2:
        prior = _bar_structure(bars[-2])
        out["prior_bar_body_pct"] = prior["body_pct"]
        out["prior_bar_close_position"] = prior["close_position"]
    out["consecutive_directional_bars"] = _consecutive_directional_bars(bars)
    hh, ll = _hh_ll_counts(bars, lookback=10)
    out["recent_higher_high_count"] = hh
    out["recent_lower_low_count"] = ll
    return out


def derive_break_geometry_features(
    row: Dict[str, Any],
    bars: Sequence[Dict[str, Any]],
    *,
    freeze_ts: Optional[datetime] = None,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "break_distance_atr": None,
        "break_distance_orb_ratio": None,
        "distance_from_vwap_atr": None,
        "distance_from_orb_edge_atr": None,
        "entry_extension_from_break_pct": None,
        "bars_since_break": None,
        "seconds_since_break": None,
        "max_extension_since_break": None,
        "retracement_from_post_break_high": None,
    }
    md = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    atr = _f(row.get("atr") or md.get("atr"))
    price = _f(
        row.get("underlying_price")
        or row.get("current_price")
        or row.get("close")
        or md.get("current_price")
    )
    as_of = freeze_ts or datetime.now(timezone.utc)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    bdp = _f(row.get("break_distance_pct") if row.get("break_distance_pct") is not None else md.get("break_distance_pct"))
    if bdp is not None and atr is not None and atr > 0 and price is not None and price > 0:
        # break_distance_pct is a fraction of price → convert to ATR units.
        out["break_distance_atr"] = abs(bdp) * price / atr

    orb_high = _f(row.get("orb_high") or md.get("orb_high"))
    orb_low = _f(row.get("orb_low") or md.get("orb_low"))
    orb_range = None
    if orb_high is not None and orb_low is not None and orb_high > orb_low:
        orb_range = orb_high - orb_low
    if orb_range is None:
        orb_range = _f(row.get("orb_range") or md.get("orb_range"))
    if bdp is not None and orb_range is not None and orb_range > 0 and price is not None:
        out["break_distance_orb_ratio"] = abs(bdp) * price / orb_range

    vwap = _f(row.get("vwap") or md.get("vwap"))
    if price is not None and vwap is not None and atr is not None and atr > 0:
        out["distance_from_vwap_atr"] = (price - vwap) / atr

    if price is not None and atr is not None and atr > 0 and orb_high is not None and orb_low is not None:
        if price >= orb_high:
            edge = orb_high
        elif price <= orb_low:
            edge = orb_low
        else:
            # Inside ORB: distance to nearest edge.
            edge = orb_high if abs(price - orb_high) <= abs(price - orb_low) else orb_low
        out["distance_from_orb_edge_atr"] = (price - edge) / atr

    close_at_break = _f(
        row.get("close_at_break")
        if row.get("close_at_break") is not None
        else md.get("close_at_break")
    )
    line_at_break = _f(
        row.get("line_price_at_break")
        if row.get("line_price_at_break") is not None
        else md.get("line_price_at_break")
    )
    break_ref = close_at_break or line_at_break
    # ORB / Breakout: when close_at_break was never stamped, the broken ORB edge
    # is the structural reference (CALL → high, PUT → low).
    if break_ref is None and orb_high is not None and orb_low is not None:
        side_hint = str(
            row.get("side")
            or row.get("option_type")
            or row.get("trigger_direction")
            or md.get("expected_option_side")
            or ""
        ).upper()
        if "PUT" in side_hint or side_hint in ("SHORT", "DOWN", "BEAR"):
            break_ref = orb_low
        else:
            break_ref = orb_high
    if price is not None and break_ref is not None and break_ref > 0:
        out["entry_extension_from_break_pct"] = (price - break_ref) / break_ref

    # Timing from known break stamps when present.
    # Clocks terminate at freeze_ts / as_of (decision time), never fill time.
    break_ts_raw = (
        row.get("break_timestamp")
        or md.get("break_timestamp")
        or row.get("break_ts")
        or md.get("break_ts")
        or row.get("break_time")
        or md.get("break_time")
        or         row.get("structure_break_ts")
        or md.get("structure_break_ts")
        or row.get("first_break_timestamp")
        or md.get("first_break_timestamp")
    )
    break_dt = None
    if isinstance(break_ts_raw, datetime):
        break_dt = break_ts_raw if break_ts_raw.tzinfo else break_ts_raw.replace(tzinfo=timezone.utc)
    elif isinstance(break_ts_raw, (int, float)):
        break_dt = datetime.fromtimestamp(float(break_ts_raw), tz=timezone.utc)
    elif isinstance(break_ts_raw, str) and break_ts_raw.strip():
        try:
            break_dt = datetime.fromisoformat(break_ts_raw.replace("Z", "+00:00"))
            if break_dt.tzinfo is None:
                break_dt = break_dt.replace(tzinfo=timezone.utc)
        except Exception:
            break_dt = None
    if break_dt is not None:
        if row.get("break_timestamp") in (None, ""):
            row["break_timestamp"] = break_dt.isoformat()
        sec = max(0.0, (as_of - break_dt).total_seconds())
        out["seconds_since_break"] = sec
        out["bars_since_break"] = int(sec // 60.0)
        try:
            from easyBreakout.breakout_session_tech import minutes_from_730_pt

            mins = minutes_from_730_pt(break_dt)
            if mins is not None:
                out["minutes_from_730_to_break"] = mins
        except Exception:
            pass

    # Max extension / retracement from bars after break when bar timestamps exist.
    if break_dt is not None and bars and break_ref is not None and break_ref > 0:
        side = str(row.get("side") or row.get("trigger_direction") or md.get("expected_option_side") or "").upper()
        post_ext = []
        for b in bars:
            ts_u = _parse_ts(b.get("timestamp") or b.get("datetime"))
            if ts_u is None:
                continue
            if ts_u < break_dt or ts_u > as_of:
                continue
            h = _f(b.get("high"))
            l = _f(b.get("low"))
            if "PUT" in side or side in ("SHORT", "DOWN", "BEAR"):
                if l is not None:
                    post_ext.append((break_ref - l) / break_ref)
            else:
                if h is not None:
                    post_ext.append((h - break_ref) / break_ref)
        if post_ext:
            max_ext = max(post_ext)
            out["max_extension_since_break"] = max_ext
            if price is not None:
                if "PUT" in side or side in ("SHORT", "DOWN", "BEAR"):
                    cur_ext = (break_ref - price) / break_ref
                else:
                    cur_ext = (price - break_ref) / break_ref
                if max_ext > 0:
                    out["retracement_from_post_break_high"] = max(0.0, (max_ext - cur_ext) / max_ext)
    return out


def derive_underlying_l1_features(
    system: Any,
    symbol: str,
    *,
    quote: Optional[Dict[str, Any]] = None,
    freeze_ts: Optional[datetime] = None,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "underlying_bid_size": None,
        "underlying_ask_size": None,
        "underlying_l1_imbalance": None,
        "underlying_l1_imbalance_delta_5s": None,
        "underlying_l1_imbalance_delta_15s": None,
        "underlying_spread_pct": None,
        "underlying_quote_age_sec": None,
        "underlying_quote_stale": None,
    }
    freeze_epoch = freeze_ts.timestamp() if isinstance(freeze_ts, datetime) else None
    ring_all = _ensure_equity_l1_ring(system).get(str(symbol).upper()) or []
    if freeze_epoch is not None:
        ring = [s for s in ring_all if _f(s.get("t")) is not None and float(s["t"]) <= freeze_epoch + 0.25]
    else:
        ring = list(ring_all)
    latest = dict(ring[-1]) if ring else {}
    # Only merge live quote when not freezing (exit / live mode).
    if freeze_epoch is None and isinstance(quote, dict):
        for k in ("bid", "ask", "bid_size", "ask_size", "last", "timestamp", "date_time_utc", "quote_ts"):
            if quote.get(k) is not None:
                latest[k] = quote.get(k)
        if quote.get("bid_size") is not None:
            latest["bid_size"] = _i(quote.get("bid_size"))
        if quote.get("ask_size") is not None:
            latest["ask_size"] = _i(quote.get("ask_size"))
        if not ring:
            latest["t"] = time.time()

    bid_size = _i(latest.get("bid_size"))
    ask_size = _i(latest.get("ask_size"))
    out["underlying_bid_size"] = bid_size
    out["underlying_ask_size"] = ask_size
    imb = l1_imbalance(bid_size, ask_size)
    out["underlying_l1_imbalance"] = imb

    bid = _f(latest.get("bid"))
    ask = _f(latest.get("ask"))
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        mid = (bid + ask) / 2.0
        if mid > 0:
            out["underlying_spread_pct"] = (ask - bid) / mid

    # Quote age relative to freeze (decision) or wall clock (live).
    age_ref = freeze_epoch if freeze_epoch is not None else time.time()
    age = None
    qts = latest.get("date_time_utc") or latest.get("quote_ts")
    if isinstance(qts, (int, float)) and float(qts) > 1e9:
        epoch = float(qts) / 1000.0 if float(qts) > 1e12 else float(qts)
        age = max(0.0, age_ref - epoch)
    elif isinstance(qts, str) and qts.strip():
        dt = _parse_ts(qts)
        if dt is not None:
            age = max(0.0, age_ref - dt.timestamp())
    if age is None and _f(latest.get("t")) is not None:
        age = max(0.0, age_ref - float(latest["t"]))
    out["underlying_quote_age_sec"] = age
    if age is not None:
        out["underlying_quote_stale"] = bool(age > _UNDERLYING_QUOTE_STALE_SEC)

    if imb is not None and ring:
        # Sample-near relative to freeze epoch when present.
        if freeze_epoch is not None:
            s5 = _sample_near_at(ring, freeze_epoch, 5.0)
            s15 = _sample_near_at(ring, freeze_epoch, 15.0)
        else:
            s5 = _sample_near(ring, 5.0)
            s15 = _sample_near(ring, 15.0)
        if s5 is not None:
            imb5 = l1_imbalance(s5.get("bid_size"), s5.get("ask_size"))
            if imb5 is not None:
                out["underlying_l1_imbalance_delta_5s"] = imb - imb5
        if s15 is not None:
            imb15 = l1_imbalance(s15.get("bid_size"), s15.get("ask_size"))
            if imb15 is not None:
                out["underlying_l1_imbalance_delta_15s"] = imb - imb15
    return out


def _latest_quote_from_system(system: Any, symbol: str) -> Optional[Dict[str, Any]]:
    for attr in ("_trendline_last_quotes", "_breakout_last_quotes", "_discovery_last_quotes"):
        qmap = getattr(system, attr, None)
        if isinstance(qmap, dict):
            q = qmap.get(symbol) or qmap.get(symbol.upper())
            if isinstance(q, dict):
                return q
    ring = _ensure_equity_l1_ring(system).get(symbol)
    if ring:
        return dict(ring[-1])
    return None


def _qg_admit_is_put_side(row: Dict[str, Any]) -> bool:
    side = str(row.get("side") or row.get("option_side") or row.get("direction") or "").strip().upper()
    if side in ("PUT", "P", "SHORT", "BEARISH"):
        return True
    for key in ("option_type", "option_type_label", "expected_option_side"):
        raw = str(row.get(key) or "").strip().upper()
        if raw in ("PUT", "P", "SHORT"):
            return True
    return False


def _stamp_path_ss_local_aliases(row: Dict[str, Any]) -> int:
    """Side-relative wick/close + otm/delta aliases from data already on the row."""
    if not isinstance(row, dict):
        return 0
    n = 0
    put = _qg_admit_is_put_side(row)
    if row.get("wick_against") is None:
        wick = _f(
            row.get("entry_bar_lower_wick_pct") if put else row.get("entry_bar_upper_wick_pct")
        )
        if wick is not None:
            row["wick_against"] = wick
            n += 1
    if row.get("close_with") is None:
        pos = _f(row.get("entry_bar_close_position"))
        if pos is not None:
            row["close_with"] = (1.0 - float(pos)) if put else float(pos)
            n += 1
    if row.get("chosen_delta") is None:
        d = _f(
            row.get("representative_delta")
            or row.get("delta")
            or row.get("fill_delta")
        )
        if d is not None:
            row["chosen_delta"] = abs(float(d))
            n += 1
    if row.get("delta") is None and row.get("chosen_delta") is not None:
        row["delta"] = row["chosen_delta"]
        n += 1
    if row.get("otm_pct") is None:
        strike = _f(
            row.get("strike")
            or row.get("representative_strike")
            or row.get("chosen_strike")
        )
        spot = _f(
            row.get("current_price")
            or row.get("underlying_price")
            or row.get("entry_underlying_price")
            or row.get("close")
        )
        if strike is not None and spot is not None and spot > 0:
            if put:
                raw = (spot - strike) / spot
            else:
                raw = (strike - spot) / spot
            row["otm_pct"] = float(raw)  # signed: +OTM, 0 ATM, -ITM (Oct 13)
            n += 1
    return n


def _minutes_from_730_to_break(row: Dict[str, Any]) -> Optional[float]:
    """Minutes from 07:30 PT to the break clock. Missing clock → None (do not invent)."""
    if not isinstance(row, dict):
        return None
    existing = _f(row.get("minutes_from_730_to_break"))
    if existing is not None:
        return existing
    break_dt: Optional[datetime] = None
    for raw in _break_ts_candidates(row):
        break_dt = _parse_ts(raw)
        if break_dt is not None:
            break
    try:
        from easyBreakout.breakout_session_tech import minutes_from_730_pt
    except Exception:
        return _f(row.get("minutes_from_730"))
    if break_dt is not None:
        return minutes_from_730_pt(break_dt)
    return _f(row.get("minutes_from_730"))


def _stamp_path_ss_derived_fields(row: Dict[str, Any]) -> int:
    """Fill Path-SS keys that are arithmetic aliases of stamps already on the row."""
    if not isinstance(row, dict):
        return 0
    n = 0
    call_v = _f(row.get("call_chain_volume"))
    put_v = _f(row.get("put_chain_volume"))
    if row.get("total_chain_volume") is None and call_v is not None and put_v is not None:
        row["total_chain_volume"] = float(call_v) + float(put_v)
        n += 1
    call_oi = _f(row.get("call_chain_open_interest"))
    put_oi = _f(row.get("put_chain_open_interest"))
    if (
        row.get("total_chain_open_interest") is None
        and call_oi is not None
        and put_oi is not None
    ):
        row["total_chain_open_interest"] = float(call_oi) + float(put_oi)
        n += 1
    if (
        row.get("put_call_volume_ratio") is None
        and call_v is not None
        and put_v is not None
        and float(call_v) > 0
    ):
        row["put_call_volume_ratio"] = float(put_v) / float(call_v)
        n += 1
    if row.get("orb_volume_ratio") is None:
        vr = _f(row.get("volume_ratio"))
        if vr is not None:
            row["orb_volume_ratio"] = vr
            n += 1
    if row.get("minutes_from_730_to_break") is None:
        mins = _minutes_from_730_to_break(row)
        if mins is not None:
            row["minutes_from_730_to_break"] = mins
            n += 1
    if row.get("open_interest") is None:
        oi = _f(row.get("representative_open_interest"))
        if oi is not None:
            row["open_interest"] = oi
            n += 1
    if row.get("abs_vwap_distance_pct") is None:
        dist = _f(row.get("vwap_distance_pct"))
        if dist is not None:
            row["abs_vwap_distance_pct"] = abs(float(dist))
            n += 1
    n += _stamp_break_distance_pct_fraction(row)
    n += _stamp_pve_3m_from_row(row)
    return n


def _stamp_break_distance_pct_fraction(row: Dict[str, Any]) -> int:
    """PO-schema fraction of price. Missing stamps stay empty (do not invent 0.0)."""
    if not isinstance(row, dict) or row.get("break_distance_pct") is not None:
        return 0
    price = _f(
        row.get("current_price")
        or row.get("underlying_price")
        or row.get("close")
        or row.get("price")
        or row.get("entry_underlying")
    )
    if price is None or price <= 0:
        return 0
    md = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    put = _qg_admit_is_put_side(row)
    if put:
        edge = _f(
            row.get("orb_low")
            or (md.get("orb_low") if isinstance(md, dict) else None)
            or row.get("line_price_at_break")
            or (md.get("line_price_at_break") if isinstance(md, dict) else None)
        )
        if edge is None or edge <= 0:
            return 0
        row["break_distance_pct"] = (float(edge) - float(price)) / float(edge)
        return 1
    edge = _f(
        row.get("orb_high")
        or (md.get("orb_high") if isinstance(md, dict) else None)
        or row.get("line_price_at_break")
        or (md.get("line_price_at_break") if isinstance(md, dict) else None)
    )
    if edge is None or edge <= 0:
        return 0
    row["break_distance_pct"] = (float(price) - float(edge)) / float(edge)
    return 1


def _stamp_pve_3m_from_row(row: Dict[str, Any]) -> int:
    """PVE from a price change and volume already on the decision row."""
    if not isinstance(row, dict) or row.get("price_volume_efficiency_3m") is not None:
        return 0
    chg = _f(row.get("price_change_3m_pct"))
    vol = _f(row.get("volume_3m"))
    if chg is None or vol is None or vol <= 0:
        return 0
    row["price_volume_efficiency_3m"] = abs(float(chg)) / float(vol) * 1_000_000.0
    return 1


def _stamp_spy_rs_from_quote_cache(system: Any, row: Dict[str, Any], symbol: str) -> int:
    """RS / SPY change from already-cached equity quotes. No broker fetch."""
    if system is None or not isinstance(row, dict):
        return 0
    if (
        row.get("spy_change_pct") is not None
        and row.get("rs_vs_spy") is not None
    ):
        return 0
    try:
        from central_tech.spy_change import stamp_rs_vs_spy_from_quote_caches

        before_spy = row.get("spy_change_pct")
        before_rs = row.get("rs_vs_spy")
        stamp_rs_vs_spy_from_quote_caches(row, system, symbol)
        n = 0
        if before_spy is None and row.get("spy_change_pct") is not None:
            n += 1
        if before_rs is None and row.get("rs_vs_spy") is not None:
            n += 1
        return n
    except Exception:
        return 0


def _stamp_path_ss_from_cached_chain(system: Any, row: Dict[str, Any], symbol: str) -> int:
    """Path-SS greeks / spread / sanity from the same Layer C chain cache."""
    if system is None or not isinstance(row, dict):
        return 0
    need = any(
        row.get(k) is None
        for k in (
            "representative_spread_pct",
            "avg_bid_ask_spread_pct",
            "chain_sanity_best_same_side_spread_pct",
            "gamma",
            "theta",
            "vega",
            "representative_bid",
            "representative_ask",
            "spread_candidate_count",
            "total_chain_volume",
            "representative_delta",
            "option_bid_size",
            "option_l1_imbalance",
        )
    )
    if not need:
        return 0
    try:
        from easy0DTE.modules.convex_chain_skew import (
            resolve_options_chain_manager,
            stamp_path_ss_chain_fields_from_cache,
        )

        mgr = resolve_options_chain_manager(system)
        return int(stamp_path_ss_chain_fields_from_cache(mgr, row) or 0)
    except Exception:
        return 0


def _fill_missing_session_tech_from_cached_bars(
    system: Any,
    row: Dict[str, Any],
    symbol: str,
) -> int:
    """Fill RSI / BB / ATR / VWAP from already-cached 1m bars. No broker fetch."""
    if system is None or not isinstance(row, dict):
        return 0
    need = any(
        row.get(k) is None
        for k in (
            "rsi",
            "bollinger_position",
            "bollinger_width",
            "bollinger_upper",
            "bollinger_middle",
            "bollinger_lower",
            "atr",
            "vwap_distance_pct",
            "macd",
            "macd_signal",
            "session_bar_count",
            "momentum_10",
        )
    )
    if not need:
        return 0
    bars = _bars_for_symbol(system, symbol)
    if not bars:
        return 0
    try:
        from modules.session_tech_indicators import (
            compute_session_tech_from_bars,
            session_tech_warmup_closes,
        )

        tech = compute_session_tech_from_bars(
            bars,
            price=_f(row.get("current_price") or row.get("underlying_price")),
            warmup_closes=session_tech_warmup_closes(system, symbol),
        )
    except Exception:
        return 0
    if not tech:
        return 0
    n = 0
    # Do not invent volume_ratio 1.0 onto the row — participation stamp owns that.
    fill_keys = (
        "rsi",
        "rsi_14",
        "bollinger_position",
        "bollinger_width",
        "bollinger_upper",
        "bollinger_middle",
        "bollinger_lower",
        "atr",
        "vwap_distance_pct",
        "vwap",
        "macd",
        "macd_signal",
        "macd_histogram",
        "session_bar_count",
        "momentum_10",
        "volume_1m",
        "session_volume",
        "tech_warmup_used",
        "tech_warmup_bars",
    )
    for key in fill_keys:
        if row.get(key) is not None:
            continue
        if key in ("rsi", "rsi_14") and bool(tech.get("rsi_missing")):
            continue
        val = tech.get(key)
        if val is not None:
            row[key] = val
            n += 1
    try:
        from modules.session_tech_indicators import ensure_bollinger_complete

        status = ensure_bollinger_complete(row)
        n += len(status.get("derived") or [])
    except Exception:
        pass
    return n


def _restamp_break_geometry_if_needed(
    system: Any,
    row: Dict[str, Any],
    symbol: str,
) -> int:
    if not isinstance(row, dict):
        return 0
    need = any(
        row.get(k) is None
        for k in (
            "break_distance_atr",
            "distance_from_vwap_atr",
            "distance_from_orb_edge_atr",
            "minutes_from_730_to_break",
        )
    )
    if not need:
        return 0
    bars = _bars_for_symbol(system, symbol) if system is not None else []
    try:
        geom = derive_break_geometry_features(row, bars)
    except Exception:
        return 0
    n = 0
    for key, val in geom.items():
        if val is not None and row.get(key) is None:
            row[key] = val
            n += 1
    return n


def _break_ts_candidates(row: Dict[str, Any]) -> List[Any]:
    """Collect break clocks from row + nested metadata / setup / qg_admit."""
    if not isinstance(row, dict):
        return []
    md = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    setup = md.get("setup_payload") if isinstance(md.get("setup_payload"), dict) else {}
    if not isinstance(setup, dict):
        setup = row.get("setup_payload") if isinstance(row.get("setup_payload"), dict) else {}
    nested = md.get("qg_admit") if isinstance(md.get("qg_admit"), dict) else {}
    if not nested:
        nested = setup.get("qg_admit") if isinstance(setup.get("qg_admit"), dict) else {}
    if not nested:
        nested = row.get("qg_admit") if isinstance(row.get("qg_admit"), dict) else {}
    keys = (
        "break_timestamp",
        "break_ts",
        "break_time",
        "structure_break_ts",
        "first_break_timestamp",
        "material_break_at",
    )
    out: List[Any] = []
    for src in (row, md, setup, nested):
        if not isinstance(src, dict):
            continue
        for key in keys:
            val = src.get(key)
            if val is not None and val != "":
                out.append(val)
    return out


def refresh_seconds_since_break(
    row: Dict[str, Any],
    *,
    now: Optional[datetime] = None,
    clear_stale_zero: bool = True,
) -> int:
    """
    Always recompute ``seconds_since_break`` from break clocks at admit time.

    Stale ``0.0`` from same-bar quality / break-detect is kept by overwrite=False
    hydrate and falsely trips Path SS ``seconds<=N`` pockets (ORB1/TL on 9.4).
    When a break clock exists, overwrite the stamp. When it does not and the
    current value is exactly 0.0, clear to None so missing-stamps-do-not-skip.
    """
    if not isinstance(row, dict):
        return 0
    as_of = now or datetime.now(timezone.utc)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=timezone.utc)
    break_dt: Optional[datetime] = None
    for raw in _break_ts_candidates(row):
        break_dt = _parse_ts(raw)
        if break_dt is not None:
            break
    n = 0
    if break_dt is not None:
        if break_dt.tzinfo is None:
            break_dt = break_dt.replace(tzinfo=timezone.utc)
        sec = max(0.0, (as_of - break_dt).total_seconds())
        prev = _f(row.get("seconds_since_break"))
        if prev is None or abs(float(prev) - sec) > 1e-9:
            row["seconds_since_break"] = sec
            n += 1
        row["bars_since_break"] = int(sec // 60.0)
        # Keep TL alias in sync when present on the admit path.
        if row.get("seconds_from_material_break") is not None or "seconds_from_material_break" in row:
            row["seconds_from_material_break"] = sec
        return n
    if clear_stale_zero:
        prev = _f(row.get("seconds_since_break"))
        if prev is not None and abs(prev) <= 1e-12:
            row["seconds_since_break"] = None
            if row.get("seconds_from_material_break") is not None:
                try:
                    if abs(float(row.get("seconds_from_material_break") or 0.0)) <= 1e-12:
                        row["seconds_from_material_break"] = None
                except (TypeError, ValueError):
                    pass
            n += 1
    return n


def stamp_admit_tech(
    system: Any,
    row: Dict[str, Any],
    *,
    symbol: Optional[str] = None,
    chain_trajectory: bool = True,
) -> int:
    """Fill Trade Profile tech from the session hub and central_tech.

    This is the live admit stamp. It does not write a Priority Optimizer snapshot
    and it does not import one. Rolling Dynamic Volume averages are not here.

    ``chain_trajectory=False`` skips the 5-minute chain deltas
    (``dir_pct_change_5m`` / ``*_chain_volume_delta_5m``). The quality gate uses
    it for every stamp that runs *before* its chain fetch: those deltas must be
    measured on the fetched chain, not on the stale volumes the row arrived with
    (R6, Oct 5: stamp-before-fetch left exact 0.0 deltas that the
    fill-only-if-missing restamp never replaced).
    """
    if not isinstance(row, dict):
        return 0
    from central_tech.option_l1 import derive_option_l1_features
    from central_tech.chain_ring import derive_chain_trajectory_features
    from central_tech.chain_ratios import stamp_chain_ratios
    from modules.qg_admit_snapshot import collect_qg_admit_snapshot, promote_qg_admit_fields

    promote_qg_admit_fields(row)
    n = 0
    sym = str(symbol or row.get("symbol") or "").strip().upper()
    if system is not None and sym:
        try:
            from modules.volume_data import ensure_volume_research_fields

            ensure_volume_research_fields(row)
        except Exception:
            pass
        quote = _latest_quote_from_system(system, sym)
        bars = list(_bars_for_symbol(system, sym))
        vol_map = getattr(system, "_session_tech_last_session_volume", None)
        session_volume = _f(vol_map.get(sym)) if isinstance(vol_map, dict) else None
        if session_volume is None and isinstance(quote, dict):
            session_volume = _f(
                quote.get("totalVolume") if quote.get("totalVolume") is not None else quote.get("volume")
            )
        average_volume = _f(quote.get("average_volume")) if isinstance(quote, dict) else None
        if average_volume is None:
            average_volume = _f(row.get("average_volume"))
        blobs = [
            derive_underlying_l1_features(system, sym, quote=quote, freeze_ts=None),
            derive_option_l1_features(row),
            derive_participation_features(
                bars,
                session_volume=session_volume,
                average_volume=average_volume,
                now=None,
            ),
            derive_candle_structure_features(bars),
            derive_break_geometry_features(row, bars, freeze_ts=None),
        ]
        if chain_trajectory:
            blobs.append(
                derive_chain_trajectory_features(system, row, symbol=sym, freeze_ts=None)
            )
        for blob in blobs:
            for key, val in blob.items():
                if val is None:
                    continue
                if row.get(key) is None:
                    row[key] = val
                    n += 1
        if n:
            row["po_observe_trajectory_stamp"] = True
    n += _fill_missing_session_tech_from_cached_bars(system, row, sym)
    try:
        from modules.session_tech_indicators import ensure_bollinger_complete

        status = ensure_bollinger_complete(row)
        n += len(status.get("derived") or [])
    except Exception:
        pass
    n += _restamp_break_geometry_if_needed(system, row, sym)
    n += refresh_seconds_since_break(row)
    n += _stamp_side_signed_aliases(row)
    n += _stamp_path_ss_local_aliases(row)
    n += _stamp_path_ss_derived_fields(row)
    n += _stamp_spy_rs_from_quote_cache(system, row, sym)
    n += _stamp_path_ss_from_cached_chain(system, row, sym)
    try:
        from central_tech.option_l1 import stamp_option_l1_imbalance_from_sizes

        n += stamp_option_l1_imbalance_from_sizes(row)
    except Exception:
        pass
    n += _stamp_path_ss_derived_fields(row)
    n += stamp_chain_ratios(row)
    if n:
        try:
            snap = collect_qg_admit_snapshot(row)
            if snap:
                row["qg_admit"] = snap
        except Exception:
            pass
    return n


def _stamp_side_signed_aliases(row: Dict[str, Any]) -> int:
    """Stamp ``vwap_with`` / ``l1_delta_5s_with`` from unsigned schema keys."""
    if not isinstance(row, dict):
        return 0
    side = str(row.get("side") or row.get("option_side") or "").strip().upper()
    put = side in ("PUT", "P", "SHORT", "BEARISH") or _qg_admit_is_put_side(row)
    n = 0
    if row.get("vwap_with") is None:
        dist = _f(row.get("vwap_distance_pct"))
        if dist is not None:
            row["vwap_with"] = -float(dist) if put else float(dist)
            n += 1
    if row.get("l1_delta_5s_with") is None:
        imb = _f(row.get("underlying_l1_imbalance_delta_5s"))
        if imb is not None:
            row["l1_delta_5s_with"] = -float(imb) if put else float(imb)
            n += 1
    return n

