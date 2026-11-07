"""
Unified session tech indicators — VWAP / volume_ratio / RSI / MACD for all 0DTE paths.

Canonical path (ORB Convex, Trendline quality, Breakout quality):
  ensure_session_tech_bars → get_session_tech → apply_session_tech
  tech_source=session_1m_rth

Bar source (central hub — do not fork per path):
  1) Broker batch quotes → minute OHLCV aggregation into ``system._session_tech_bar_cache``
  2) Broker day OHLC seed via ``data_manager.get_batch_intraday_data`` when <2 bars
  3) Bounded Yahoo RTH 1m backfill when priced closes < RSI depth (15) — same Jul 16
     session-tech exception Trendline historically used. Quote-minute polls alone leave
     early Breakout entries with invent RSI-50 / MACD-null (Aug 4 CIFR/CRWV/NOW/IBM).

Daily multi-day VWAP/MACD inflate ``vwap_distance_pct`` and poison gates — never use
those for session tech. Prices / levels / chains stay broker-only (Rev 00236).

Master switch (compat name): ``0DTE_CONVEX_SESSION_TECH`` (default true).
Yahoo 1m backfill switch: ``0DTE_SESSION_TECH_YF_1M_BACKFILL`` (default true).
"""

from __future__ import annotations

import logging
import math
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

log = logging.getLogger(__name__)

SESSION_TECH_SOURCE = "session_1m_rth"
DEFAULT_SESSION_TECH_MAX_AGE_SEC = 90.0
# RSI-14 needs period+1 closes; MACD needs ~26. Backfill when under RSI depth.
SESSION_TECH_RSI_MIN_CLOSES = 15
SESSION_TECH_YF_1M_BATCH_CAP = 12


def session_tech_enabled() -> bool:
    """Session tech master switch (env key kept for Cloud Run compat)."""
    raw = os.getenv("0DTE_CONVEX_SESSION_TECH", "true")
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def session_tech_yf_1m_backfill_enabled() -> bool:
    """Bounded Yahoo RTH 1m history when quote minutes are too thin for RSI/MACD."""
    raw = os.getenv("0DTE_SESSION_TECH_YF_1M_BACKFILL", "true")
    return str(raw).strip().lower() not in ("0", "false", "no", "off")


def session_tech_max_age_sec(default: float = DEFAULT_SESSION_TECH_MAX_AGE_SEC) -> float:
    raw = os.getenv("0DTE_SESSION_TECH_MAX_AGE_SEC", "")
    if raw is None or str(raw).strip() == "":
        return float(default)
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return float(default)


def _f(val: Any, default: float = 0.0) -> float:
    try:
        if val is None or val == "":
            return default
        out = float(val)
        return out if math.isfinite(out) else default
    except (TypeError, ValueError):
        return default


def _ema(prices: Sequence[float], period: int) -> float:
    if not prices:
        return 0.0
    if len(prices) < period:
        return sum(prices) / len(prices)
    mult = 2.0 / (period + 1.0)
    ema = float(prices[0])
    for px in prices[1:]:
        ema = (float(px) * mult) + (ema * (1.0 - mult))
    return ema


def _macd_series(prices: Sequence[float]) -> List[float]:
    """MACD line value once enough closes exist (>=26)."""
    if len(prices) < 26:
        return []
    out: List[float] = []
    for i in range(26, len(prices) + 1):
        window = prices[:i]
        out.append(_ema(window, 12) - _ema(window, 26))
    return out


def _macd_line(prices: Sequence[float]) -> Optional[float]:
    series = _macd_series(prices)
    return float(series[-1]) if series else None


def _macd_signal(prices: Sequence[float]) -> Optional[float]:
    series = _macd_series(prices)
    if len(series) < 9:
        return None
    return float(_ema(series, 9))


def macd_histogram(prices: Sequence[float]) -> Optional[float]:
    line = _macd_line(prices)
    sig = _macd_signal(prices)
    if line is None or sig is None:
        return None
    return float(line) - float(sig)


def derive_macd_cross_state(
    *,
    macd: Optional[float],
    macd_signal: Optional[float],
    macd_histogram: Optional[float],
    prior_macd_histogram: Optional[float] = None,
) -> Dict[str, Any]:
    """CALL hold/exit MACD state (NFLX/AMZN Jul 29: hold while bullish; exit on bear cross)."""
    out: Dict[str, Any] = {
        "macd_above_zero": None,
        "macd_cross": None,
        "macd_hold_ok_call": None,
        "macd_exit_signal_call": None,
        "macd_hist_sign": None,
    }
    if macd is None or macd_signal is None or macd_histogram is None:
        return out
    hist = float(macd_histogram)
    line = float(macd)
    sig = float(macd_signal)
    out["macd_above_zero"] = bool(line > 0)
    out["macd_hist_sign"] = 1 if hist > 0 else (-1 if hist < 0 else 0)
    cross = "none"
    if prior_macd_histogram is not None:
        try:
            prior = float(prior_macd_histogram)
            if prior < 0 and hist >= 0:
                cross = "bull_cross"
            elif prior > 0 and hist <= 0:
                cross = "bear_cross"
        except (TypeError, ValueError):
            cross = "none"
    out["macd_cross"] = cross
    bullish = hist > 0 and line > sig
    bearish = hist < 0 and line < sig
    out["macd_hold_ok_call"] = bool(bullish)
    out["macd_exit_signal_call"] = bool(cross == "bear_cross" or bearish)
    return out


def session_vwap_from_bars(bars: Sequence[Dict[str, Any]]) -> float:
    """Typical-price VWAP over session bars."""
    num = 0.0
    den = 0.0
    for b in bars:
        vol = _f(b.get("volume"), 0.0)
        if vol <= 0:
            vol = 1.0
        high = _f(b.get("high"))
        low = _f(b.get("low"))
        close = _f(b.get("close"))
        tp = (high + low + close) / 3.0
        num += tp * vol
        den += vol
    if den <= 0 and bars:
        return _f(bars[-1].get("close"))
    return num / max(den, 1e-9)


def atr_from_bars(bars: Sequence[Dict[str, Any]], period: int = 14) -> Optional[float]:
    """ATR from current session OHLC bars (None when too thin)."""
    if len(bars) < 2:
        return None
    trs: List[float] = []
    prev_close = _f(bars[0].get("close"))
    for b in bars[1:]:
        high = _f(b.get("high"), prev_close)
        low = _f(b.get("low"), prev_close)
        close = _f(b.get("close"), prev_close)
        if close <= 0 and prev_close <= 0:
            continue
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        if math.isfinite(tr) and tr >= 0:
            trs.append(float(tr))
        if close > 0:
            prev_close = close
    if not trs:
        return None
    window = trs[-int(period) :] if len(trs) >= int(period) else trs
    return float(sum(window) / len(window))


def bollinger_from_closes(
    closes: Sequence[float], period: int = 20, num_std: float = 2.0
) -> Dict[str, Optional[float]]:
    """Bollinger from current session closes; empty dict values when too thin.

    ``bollinger_position`` = % of band height from lower→upper at last close
    (legacy 89-point formula). 0 ≈ lower band, 50 ≈ mid, 100 ≈ upper.
    """
    empty = {
        "bollinger_upper": None,
        "bollinger_middle": None,
        "bollinger_lower": None,
        "bollinger_width": None,
        "bollinger_position": None,
    }
    if len(closes) < max(2, int(period) // 2):
        return empty
    window = [float(c) for c in closes[-int(period) :]]
    mid = float(sum(window) / len(window))
    var = sum((c - mid) ** 2 for c in window) / max(len(window), 1)
    std = math.sqrt(max(var, 0.0))
    upper = mid + float(num_std) * std
    lower = mid - float(num_std) * std
    width = (upper - lower) / mid if mid > 1e-12 else None
    band = upper - lower
    last = float(closes[-1])
    if band > 1e-12:
        position = ((last - lower) / band) * 100.0
    else:
        position = 50.0
    return {
        "bollinger_upper": float(upper),
        "bollinger_middle": float(mid),
        "bollinger_lower": float(lower),
        "bollinger_width": float(width) if width is not None else None,
        "bollinger_position": float(position),
    }


def ensure_bollinger_complete(
    row: Optional[Dict[str, Any]],
    *,
    price: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Fill BB gaps in-place for PO / path rows using the same %B math as the hub.

    - Derives ``bollinger_position`` when bands exist but position is missing.
    - Derives ``bollinger_width`` when upper/lower/middle exist but width is missing.
    - Never invents bands from quote high/low — only completes from real bands + price.

    Returns ``{"ok": bool, "derived": [...], "missing": [...]}``.
    """
    status: Dict[str, Any] = {"ok": False, "derived": [], "missing": []}
    if not isinstance(row, dict):
        status["missing"] = [
            "bollinger_upper",
            "bollinger_middle",
            "bollinger_lower",
            "bollinger_width",
            "bollinger_position",
        ]
        return status

    def _f(v: Any) -> Optional[float]:
        try:
            if v is None or v == "":
                return None
            return float(v)
        except (TypeError, ValueError):
            return None

    upper = _f(row.get("bollinger_upper"))
    mid = _f(row.get("bollinger_middle"))
    lower = _f(row.get("bollinger_lower"))
    width = _f(row.get("bollinger_width"))
    position = _f(row.get("bollinger_position"))
    px = _f(price)
    if px is None:
        for k in (
            "underlying_price",
            "current_price",
            "close",
            "last_price",
            "price",
            "entry_underlying",
            "entry_underlying_price",
        ):
            px = _f(row.get(k))
            if px is not None and px > 0:
                break

    if mid is None and upper is not None and lower is not None:
        mid = (upper + lower) / 2.0
        row["bollinger_middle"] = float(mid)
        status["derived"].append("bollinger_middle")

    if (
        width is None
        and upper is not None
        and lower is not None
        and mid is not None
        and abs(mid) > 1e-12
    ):
        width = (upper - lower) / mid
        row["bollinger_width"] = float(width)
        status["derived"].append("bollinger_width")

    if position is None and upper is not None and lower is not None:
        band = upper - lower
        # Prefer live price; fall back to mid so position is never left null when bands exist.
        ref = px if px is not None and px > 0 else mid
        if band > 1e-12 and ref is not None:
            position = ((float(ref) - lower) / band) * 100.0
            row["bollinger_position"] = float(position)
            status["derived"].append("bollinger_position")
        elif band <= 1e-12:
            row["bollinger_position"] = 50.0
            status["derived"].append("bollinger_position_flat_band")

    for k in (
        "bollinger_upper",
        "bollinger_middle",
        "bollinger_lower",
        "bollinger_width",
        "bollinger_position",
    ):
        if row.get(k) is None:
            status["missing"].append(k)
    status["ok"] = not status["missing"]
    return status


def vwap_position_label(price: Optional[float], vwap: Optional[float]) -> str:
    """above / below / at / unknown — shared by ORB/BO/TL hub stamps."""
    if price is None or vwap is None:
        return "unknown"
    try:
        px = float(price)
        v = float(vwap)
    except (TypeError, ValueError):
        return "unknown"
    if v <= 0 or not math.isfinite(px) or not math.isfinite(v):
        return "unknown"
    if px > v * 1.0005:
        return "above"
    if px < v * 0.9995:
        return "below"
    return "at"


def momentum_from_closes(closes: Sequence[float], lookback: int = 10) -> Optional[float]:
    """Session close momentum pct over lookback bars."""
    if len(closes) < int(lookback) + 1:
        return None
    base = float(closes[-(int(lookback) + 1)])
    if abs(base) < 1e-12:
        return None
    return ((float(closes[-1]) - base) / base) * 100.0


def rsi_from_closes(closes: Sequence[float], period: int = 14) -> float:
    """
    RSI from closes. Returns neutral 50.0 when series is too short for a real RSI-14.

    Callers must also check ``rsi_missing`` from ``compute_session_tech_from_bars`` —
    do not treat invent-50 as a trusted gate input.
    """
    if len(closes) < 2:
        return 50.0
    # Full RSI-14 needs period+1 closes; shorter windows are untrusted.
    if len(closes) < int(period) + 1:
        return 50.0
    window = [float(c) for c in closes[-(int(period) + 1) :]]
    gains: List[float] = []
    losses: List[float] = []
    for i in range(1, len(window)):
        d = window[i] - window[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_gain = sum(gains) / max(len(gains), 1)
    avg_loss = sum(losses) / max(len(losses), 1)
    if avg_loss <= 1e-12:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def rsi_is_trusted(closes: Sequence[float], period: int = 14) -> bool:
    """True when closes support a real RSI-period (not invent-50)."""
    return len([c for c in closes if _f(c) > 0]) >= int(period) + 1


def _bar_kind(bar: Dict[str, Any]) -> str:
    return str(bar.get("bar_kind") or "").strip().lower()


def bar_has_usable_minute_volume(bar: Dict[str, Any]) -> bool:
    """
    True when bar volume is a real per-minute print (not day-seed / pad / 15m / placeholder).

    Day OHLC seeds carry cumulative session volume; first-tick estimates use sess*0.001;
    intraday_15m bars are a different interval — never mix into minute volume_ratio
    (Jul 31 ORB hard gate ~0.00 miss).
    """
    if not isinstance(bar, dict):
        return False
    if _bar_kind(bar) in ("day_seed", "pad_anchor", "intraday_15m"):
        return False
    if bar.get("volume_placeholder") or bar.get("volume_estimate"):
        return False
    vol = _f(bar.get("volume"), 0.0)
    # Placeholder upserts use exactly 1.0 when broker omits session volume.
    return vol > 1.0


def usable_minute_volumes(
    bars: Sequence[Dict[str, Any]], lookback: int = 20
) -> List[float]:
    vols: List[float] = []
    for b in list(bars or [])[-lookback:]:
        if bar_has_usable_minute_volume(b):
            vols.append(_f(b.get("volume"), 0.0))
    return vols


def volume_ratio_from_bars(bars: Sequence[Dict[str, Any]], lookback: int = 20) -> float:
    """
    Current usable minute volume / avg of prior usable minutes.

    Returns neutral 1.0 when volume data is missing or untrusted (caller must also
    check ``bars_lack_usable_volume_dicts`` / ``volume_ratio_missing``).
    """
    if not bars:
        return 1.0
    usable = [
        b for b in list(bars)[-lookback:] if bar_has_usable_minute_volume(b)
    ]
    if len(usable) < 2:
        return 1.0
    cur = _f(usable[-1].get("volume"), 0.0)
    hist = [_f(b.get("volume"), 0.0) for b in usable[:-1] if _f(b.get("volume"), 0.0) > 0]
    if not hist or cur <= 0:
        return 1.0
    avg = sum(hist) / len(hist)
    ratio = cur / max(avg, 1e-9)
    if not math.isfinite(ratio) or ratio <= 0:
        return 1.0
    # Scale-mismatch safety net (day cumulative slipped into minute series).
    if ratio < 0.01 or ratio > 100.0:
        return 1.0
    return ratio


def bars_lack_usable_volume_dicts(bars: Sequence[Dict[str, Any]]) -> bool:
    """True when we lack ≥2 real minute volumes for a trusted volume_ratio."""
    if not bars:
        return True
    return len(usable_minute_volumes(bars)) < 2


def ohlcv_bars_to_session_dicts(bars: Sequence[Any]) -> List[Dict[str, Any]]:
    """Convert Trendline OHLCVBar (or duck-typed) objects to session-tech dict bars."""
    out: List[Dict[str, Any]] = []
    for b in bars or []:
        try:
            if isinstance(b, dict):
                close = _f(b.get("close"))
                if close <= 0:
                    continue
                out.append(
                    {
                        "timestamp": b.get("timestamp") or b.get("datetime") or b.get("ts"),
                        "datetime": b.get("datetime") or b.get("timestamp") or b.get("ts"),
                        "open": _f(b.get("open"), close),
                        "high": _f(b.get("high"), close),
                        "low": _f(b.get("low"), close),
                        "close": close,
                        "volume": _f(b.get("volume"), 0.0),
                    }
                )
                continue
            close = _f(getattr(b, "close", None))
            if close <= 0:
                continue
            ts = getattr(b, "ts", None) or getattr(b, "timestamp", None)
            out.append(
                {
                    "timestamp": ts,
                    "datetime": ts,
                    "open": _f(getattr(b, "open", None), close),
                    "high": _f(getattr(b, "high", None), close),
                    "low": _f(getattr(b, "low", None), close),
                    "close": close,
                    "volume": _f(getattr(b, "volume", None), 0.0),
                }
            )
        except Exception:
            continue
    return out


def _bar_timestamp(bar: Any) -> Optional[str]:
    """The bar's own time. Not the time the snapshot file is written."""
    if not isinstance(bar, dict):
        return None
    raw = bar.get("timestamp") or bar.get("datetime") or bar.get("ts")
    if raw in (None, ""):
        return None
    if isinstance(raw, datetime):
        return raw.isoformat()
    return str(raw)


def parse_session_tech_clock(raw: Any) -> Optional[datetime]:
    """Parse a hub bar/as-of clock to aware UTC."""
    if raw in (None, ""):
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
    text = str(raw).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except Exception:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def session_tech_asof_from_bar(bar_at: Any) -> Optional[str]:
    """Bar close = start of that minute + 60s. Freshness uses this, not fetch time.

    Hub minutes are often stamped at the last tick in the minute, not the open.
    Tick + 60s made the as-of clock sit after the event, so PO dropped the
    clock and left RSI/MACD/BB looking filled. The minute close is the bar.
    """
    dt = parse_session_tech_clock(bar_at)
    if dt is None:
        return None
    minute_open = dt.replace(second=0, microsecond=0)
    return (minute_open + timedelta(seconds=60)).isoformat()


def bars_closed_at_or_before(
    bars: Sequence[Dict[str, Any]],
    event_at: Any,
) -> List[Dict[str, Any]]:
    """Keep 1m bars whose minute close is at or before the event. No fetch."""
    event = parse_session_tech_clock(event_at)
    if event is None:
        return list(bars or [])
    out: List[Dict[str, Any]] = []
    for bar in bars or []:
        close_dt = parse_session_tech_clock(session_tech_asof_from_bar(_bar_timestamp(bar)))
        if close_dt is None:
            continue
        if close_dt <= event:
            out.append(bar)
    return out


def _age_seconds(now_utc: datetime, asof: Any) -> Optional[float]:
    dt = parse_session_tech_clock(asof)
    if dt is None:
        return None
    return max(0.0, (now_utc - dt).total_seconds())


def _attach_pve3_from_bars(
    tech: Dict[str, Any],
    bars: Sequence[Dict[str, Any]],
    *,
    now_utc: Optional[datetime] = None,
) -> None:
    """Same admit PVE3 formula, on the same 1m bars as RSI/MACD."""
    try:
        from modules.session_tech_admit import derive_participation_features

        pve = derive_participation_features(
            bars,
            session_volume=tech.get("session_volume"),
            now=now_utc or datetime.now(timezone.utc),
        )
    except Exception as exc:
        log.debug("SESSION_TECH_PVE_DERIVE_FAIL | error=%s", exc)
        pve = {}
    for key in (
        "price_volume_efficiency_3m",
        "price_change_3m_pct",
        "volume_3m",
    ):
        if pve.get(key) is not None:
            tech[key] = pve[key]
    bar_at = tech.get("session_tech_bar_at")
    tech["pve3_bar_at"] = bar_at
    tech["session_tech_asof_at"] = session_tech_asof_from_bar(bar_at)


def compute_session_tech_from_bars(
    bars: Sequence[Dict[str, Any]],
    *,
    price: Optional[float] = None,
    min_bars_for_macd: int = 34,
    warmup_closes: Optional[Sequence[Any]] = None,
    now_utc: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """
    Build path-agnostic session tech stamp from today's RTH 1m bars.

    One bar is enough for a usable VWAP / volume stamp (Cloud Run cold starts
    often only have a single quote minute before day-OHLC seed lands). Returns
    None only when there are no priced bars.

    ``warmup_closes`` (Oct 5 2026): real 1m closes from the end of the prior
    RTH session (``modules.session_tech_warmup``), oldest first. They are used
    only for the close-series indicators (RSI-14, MACD 12/26/9, SMA-20/50,
    EMA-12/26, Bollinger-20, momentum-10) and only when today's closes are too
    few for that indicator. VWAP, ATR, volume and bar-structure keys stay
    today-only. ``tech_warmup_used`` names every indicator that used them.
    """
    if not bars:
        return None

    closes = [_f(b.get("close")) for b in bars if _f(b.get("close")) > 0]
    if not closes:
        return None
    warm = [_f(c) for c in (warmup_closes or ()) if _f(c) > 0]
    ext = warm + closes if warm else closes
    warm_used: List[str] = []

    def _series(need: int, name: str) -> Sequence[float]:
        """Today's closes when deep enough; else prior-session + today when that is."""
        if len(closes) >= need or not warm:
            return closes
        if len(ext) >= need:
            warm_used.append(name)
            return ext
        return closes

    vwap = session_vwap_from_bars(bars)
    if vwap <= 0:
        return None

    last_px = float(price) if price is not None and float(price) > 0 else closes[-1]
    vwap_distance_pct = ((last_px - vwap) / vwap) * 100.0

    macd: Optional[float] = None
    macd_sig: Optional[float] = None
    macd_hist: Optional[float] = None
    prior_hist: Optional[float] = None
    m_closes = _series(int(min_bars_for_macd), "macd")
    if len(m_closes) >= min_bars_for_macd:
        macd = _macd_line(m_closes)
        macd_sig = _macd_signal(m_closes)
        if macd is not None and macd_sig is not None:
            macd_hist = float(macd) - float(macd_sig)
        if len(m_closes) >= min_bars_for_macd + 1:
            prior_hist = macd_histogram(list(m_closes)[:-1])

    # Session-bar SMA/EMA (1m closes). Broker daily SMA still preferred when hydrate works;
    # these fill TL/BO PO snaps that historically left sma_* null.
    s20 = _series(20, "sma_20")
    s50 = _series(50, "sma_50")
    e12 = _series(12, "ema_12")
    e26 = _series(26, "ema_26")
    sma_20 = float(sum(s20[-20:]) / 20.0) if len(s20) >= 20 else float(sum(closes) / len(closes))
    sma_50 = float(sum(s50[-50:]) / 50.0) if len(s50) >= 50 else float(sum(closes) / len(closes))
    ema_12 = float(_ema(e12, 12)) if len(e12) >= 12 else float(closes[-1])
    ema_26 = float(_ema(e26, 26)) if len(e26) >= 26 else float(closes[-1])

    volume_ratio_missing = bars_lack_usable_volume_dicts(bars)
    vol_ratio = 1.0 if volume_ratio_missing else float(volume_ratio_from_bars(bars))
    # volume_ratio_from_bars returns neutral 1.0 on scale mismatch — re-check raw.
    usable_vols = usable_minute_volumes(bars)
    volume_1m = float(usable_vols[-1]) if usable_vols else None
    session_volume = float(sum(usable_vols)) if usable_vols else None
    if not volume_ratio_missing:
        raw_usable = usable_minute_volumes(bars)
        if len(raw_usable) >= 2:
            raw_ratio = raw_usable[-1] / max(sum(raw_usable[:-1]) / len(raw_usable[:-1]), 1e-9)
            if not math.isfinite(raw_ratio) or raw_ratio < 0.01 or raw_ratio > 100.0:
                volume_ratio_missing = True
                vol_ratio = 1.0
    r_closes = _series(int(SESSION_TECH_RSI_MIN_CLOSES), "rsi")
    rsi_missing = not rsi_is_trusted(r_closes, 14)
    # Never invent RSI-50 into stamps — gates soft-skip via rsi_missing; PO must not
    # look "populated" with a fake neutral (Aug 4 Breakout entry snaps).
    rsi = None if rsi_missing else float(rsi_from_closes(r_closes, 14))
    macd_missing = macd_hist is None
    cross = derive_macd_cross_state(
        macd=macd,
        macd_signal=macd_sig,
        macd_histogram=macd_hist,
        prior_macd_histogram=prior_hist,
    )
    atr = atr_from_bars(bars, 14)
    bb = bollinger_from_closes(_series(20, "bollinger"), 20)
    mom10 = momentum_from_closes(_series(11, "momentum_10"), 10)

    tech = {
        "vwap": float(vwap),
        "vwap_distance_pct": float(vwap_distance_pct),
        "vwap_position": vwap_position_label(last_px, vwap),
        "macd": macd,
        "macd_signal": macd_sig,
        "macd_histogram": macd_hist,
        "prior_macd_histogram": prior_hist,
        "sma_20": sma_20,
        "sma_50": sma_50,
        "ema_12": ema_12,
        "ema_26": ema_26,
        "rsi": rsi,
        "rsi_14": rsi,
        "volume_ratio": vol_ratio,
        "atr": atr,
        "momentum_10": mom10,
        "bollinger_upper": bb.get("bollinger_upper"),
        "bollinger_middle": bb.get("bollinger_middle"),
        "bollinger_lower": bb.get("bollinger_lower"),
        "bollinger_width": bb.get("bollinger_width"),
        "bollinger_position": bb.get("bollinger_position"),
        "tech_source": SESSION_TECH_SOURCE,
        "volume_ratio_source": SESSION_TECH_SOURCE,
        "vwap_source": SESSION_TECH_SOURCE,
        "session_bar_count": int(len(bars)),
        "session_tech_bar_at": _bar_timestamp(bars[-1]) if bars else None,
        "session_last_close": float(closes[-1]),
        "open": _f(bars[-1].get("open"), None) if bars else None,
        "high": _f(bars[-1].get("high"), None) if bars else None,
        "low": _f(bars[-1].get("low"), None) if bars else None,
        "volume": volume_1m,
        "volume_1m": volume_1m,
        "session_volume": session_volume,
        "vwap_missing": False,
        "volume_ratio_missing": volume_ratio_missing,
        "rsi_missing": rsi_missing,
        "macd_missing": macd_missing,
        "tech_warmup_used": ",".join(dict.fromkeys(warm_used)),
        "tech_warmup_bars": int(len(warm)) if warm_used else 0,
        **cross,
    }
    _attach_pve3_from_bars(tech, bars, now_utc=now_utc)
    return tech


def apply_session_tech(target: Any, tech: Dict[str, Any]) -> None:
    """
    Write session tech fields onto a signal dict, candidate object, or metadata blob.

    Path-agnostic apply — ORB signals, BO candidates, and generic dicts all use this.
    """
    if not tech:
        return

    keys = (
        "vwap",
        "vwap_distance_pct",
        "vwap_position",
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
        "rsi_14",
        "volume_ratio",
        "atr",
        "momentum_10",
        "bollinger_upper",
        "bollinger_middle",
        "bollinger_lower",
        "bollinger_width",
        "bollinger_position",
        "tech_source",
        "volume_ratio_source",
        "vwap_source",
        "session_bar_count",
        "session_tech_bar_at",
        "session_tech_asof_at",
        "pve3_bar_at",
        "price_volume_efficiency_3m",
        "price_change_3m_pct",
        "volume_3m",
        "volume_1m",
        "session_volume",
        "open",
        "high",
        "low",
        "volume",
        "volume_ratio_missing",
        "vwap_missing",
        "rsi_missing",
        "macd_missing",
        "tech_warmup_used",
        "tech_warmup_bars",
    )

    if isinstance(target, dict):
        for key in keys:
            if key in tech and tech[key] is not None:
                target[key] = tech[key]
        # Explicit trust flags (False is valid — do not skip).
        for flag in ("volume_ratio_missing", "vwap_missing", "rsi_missing", "macd_missing"):
            if flag in tech:
                target[flag] = bool(tech.get(flag))
        # Clear invent placeholders when hub says missing (do not leave RSI-50 / MACD-0).
        if bool(tech.get("rsi_missing")):
            target["rsi"] = None
            target["rsi_14"] = None
        if bool(tech.get("macd_missing")):
            target["macd_histogram"] = None
            target["prior_macd_histogram"] = None
            target["macd_signal"] = None
            target["macd"] = None
        if bool(tech.get("volume_ratio_missing")):
            # Do not invent 1.0 onto the signal — PO hydrates real tape at fill.
            target["volume_ratio"] = None
            target["orb_volume_ratio"] = None
        elif tech.get("volume_ratio") is not None:
            target["orb_volume_ratio"] = tech["volume_ratio"]
        md = target.get("metadata")
        if not isinstance(md, dict):
            md = {}
            target["metadata"] = md
        md["tech_source"] = tech.get("tech_source", SESSION_TECH_SOURCE)
        md["session_bar_count"] = tech.get("session_bar_count")
        md["vwap_distance_pct"] = tech.get("vwap_distance_pct")
        md["macd_histogram"] = tech.get("macd_histogram")
        md["sma_20"] = tech.get("sma_20")
        md["sma_50"] = tech.get("sma_50")
        md["volume_ratio_source"] = tech.get("volume_ratio_source", SESSION_TECH_SOURCE)
        md["vwap_source"] = tech.get("vwap_source", SESSION_TECH_SOURCE)
        md["volume_ratio_missing"] = bool(tech.get("volume_ratio_missing"))
        md["vwap_missing"] = bool(tech.get("vwap_missing"))
        md["rsi_missing"] = bool(tech.get("rsi_missing"))
        md["macd_missing"] = bool(tech.get("macd_missing"))
        if bool(tech.get("volume_ratio_missing")):
            md["volume_ratio"] = None
            md["orb_volume_ratio"] = None
        if tech.get("volume_1m") is not None:
            md["volume_1m"] = tech.get("volume_1m")
        if tech.get("session_volume") is not None:
            md["session_volume"] = tech.get("session_volume")
        if bool(tech.get("rsi_missing")):
            md["rsi"] = None
            md["rsi_14"] = None
        try:
            ensure_bollinger_complete(target)
        except Exception:
            pass
        return

    for key in (
        "vwap",
        "vwap_distance_pct",
        "rsi",
        "volume_ratio",
        "sma_20",
        "sma_50",
        "volume_ratio_missing",
        "vwap_missing",
        "rsi_missing",
        "macd_missing",
    ):
        if tech.get(key) is not None and hasattr(target, key):
            try:
                setattr(target, key, tech[key])
            except Exception:
                pass
    # Clear invent placeholders on object targets (BO cand / TL quality rows).
    if bool(tech.get("rsi_missing")):
        for attr in ("rsi", "rsi_14"):
            if hasattr(target, attr):
                try:
                    setattr(target, attr, None)
                except Exception:
                    pass
    if bool(tech.get("macd_missing")):
        for attr in ("macd_histogram", "prior_macd_histogram", "macd", "macd_signal"):
            if hasattr(target, attr):
                try:
                    setattr(target, attr, None)
                except Exception:
                    pass
    meta = getattr(target, "metadata", None)
    if isinstance(meta, dict):
        for key in (
            "tech_source",
            "volume_ratio_source",
            "vwap_source",
            "session_bar_count",
            "vwap_distance_pct",
            "macd_histogram",
            "macd",
            "sma_20",
            "sma_50",
            "rsi",
            "volume_ratio",
            "vwap",
            "volume_ratio_missing",
            "vwap_missing",
            "rsi_missing",
            "macd_missing",
        ):
            if tech.get(key) is not None:
                meta[key] = tech[key]
        for flag in ("volume_ratio_missing", "vwap_missing", "rsi_missing", "macd_missing"):
            if flag in tech:
                meta[flag] = bool(tech.get(flag))
        if bool(tech.get("rsi_missing")):
            meta["rsi"] = None
            meta["rsi_14"] = None
        if bool(tech.get("macd_missing")):
            meta["macd_histogram"] = None
            meta["prior_macd_histogram"] = None
        try:
            target.metadata = meta
        except Exception:
            pass


def apply_session_tech_to_signal(signal: Dict[str, Any], tech: Dict[str, Any]) -> None:
    """Backward-compatible alias for ORB Convex callers."""
    apply_session_tech(signal, tech)


def quote_last_price(quote: Any) -> float:
    """Last/tradeable price from a broker quote dict (E*TRADE batch fields)."""
    if not isinstance(quote, dict):
        return 0.0
    for key in ("last", "lastPrice", "last_price", "close", "price", "mark"):
        px = _f(quote.get(key), 0.0)
        if px > 0:
            return px
    return 0.0


def quote_session_volume(quote: Any) -> Optional[float]:
    """Cumulative session volume from broker quote fields."""
    if not isinstance(quote, dict):
        return None
    for key in ("totalVolume", "total_volume", "dayVolume", "day_volume", "volume"):
        raw = quote.get(key)
        if raw is None or raw == "":
            continue
        val = _f(raw, 0.0)
        if val > 0:
            return val
    return None


def _pt_minute_key(ts: datetime) -> Tuple[Any, int, int]:
    try:
        from zoneinfo import ZoneInfo

        loc = ts.astimezone(ZoneInfo("America/Los_Angeles"))
    except Exception:
        loc = ts
    return (loc.date(), int(loc.hour), int(loc.minute))


def _normalize_ts(ts: Optional[datetime] = None) -> datetime:
    if ts is None:
        return datetime.now(timezone.utc)
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def _ensure_session_tech_store(
    system: Any,
) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, float], Dict[str, float]]:
    """
    Return (bar_cache, fetched_at, last_session_volume) on ``system``.

    Important: do not use ``getattr(...) or {}`` — an empty dict is falsy and
    would allocate a *new* cache, splitting writers and wiping bars.
    """
    cache = getattr(system, "_session_tech_bar_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        setattr(system, "_session_tech_bar_cache", cache)
    ts_map = getattr(system, "_session_tech_bar_fetched_at", None)
    if not isinstance(ts_map, dict):
        ts_map = {}
        setattr(system, "_session_tech_bar_fetched_at", ts_map)
    vol_map = getattr(system, "_session_tech_last_session_volume", None)
    if not isinstance(vol_map, dict):
        vol_map = {}
        setattr(system, "_session_tech_last_session_volume", vol_map)
    return cache, ts_map, vol_map


def upsert_session_tech_minute_bar(
    system: Any,
    symbol: str,
    price: float,
    *,
    ts: Optional[datetime] = None,
    session_volume: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """
    Aggregate a live broker quote into one OHLCV bar per PT clock minute.

    This is the central bar builder for session tech (shared by ORB / TL / BO / PO).
    """
    sym = str(symbol or "").strip().upper()
    px = float(price or 0.0)
    if not sym or px <= 0:
        return None

    cache, ts_map, vol_map = _ensure_session_tech_store(system)

    now_ts = _normalize_ts(ts)
    key = _pt_minute_key(now_ts)
    bars = cache.setdefault(sym, [])

    vol_delta = 0.0
    volume_estimate = False
    if session_volume is not None:
        sess = _f(session_volume, 0.0)
        if sess > 0:
            last_sess = _f(vol_map.get(sym), 0.0)
            if last_sess > 0 and sess >= last_sess:
                vol_delta = max(0.0, sess - last_sess)
            elif last_sess <= 0:
                # First sight of cumulative session volume — approximate one minute.
                # Mark estimate so volume_ratio does not treat this as trusted.
                vol_delta = max(1.0, sess * 0.001)
                volume_estimate = True
            vol_map[sym] = sess

    if bars:
        last = bars[-1]
        last_ts = last.get("timestamp") or last.get("datetime")
        try:
            if isinstance(last_ts, datetime):
                last_key = _pt_minute_key(_normalize_ts(last_ts))
            else:
                last_key = None
        except Exception:
            last_key = None
        if last_key == key:
            new_vol = _f(last.get("volume"), 0.0)
            if vol_delta > 0:
                new_vol = max(new_vol, 0.0) + vol_delta
                if not volume_estimate:
                    last.pop("volume_estimate", None)
                    last.pop("volume_placeholder", None)
            last["high"] = max(_f(last.get("high"), px), px)
            last["low"] = min(_f(last.get("low"), px), px)
            last["close"] = px
            last["volume"] = new_vol
            last["timestamp"] = now_ts
            last["datetime"] = now_ts
            if volume_estimate:
                last["volume_estimate"] = True
            ts_map[sym] = time.time()
            return last

    prev = _f(bars[-1].get("close"), px) if bars else px
    placeholder = vol_delta <= 0
    bar = {
        "timestamp": now_ts,
        "datetime": now_ts,
        "open": prev,
        "high": max(prev, px),
        "low": min(prev, px),
        "close": px,
        # Placeholder 1.0 when broker omits session volume — avoids perpetual
        # volume_ratio_missing fail-closed on quote-only cold ticks for VWAP.
        "volume": vol_delta if vol_delta > 0 else 1.0,
        "volume_placeholder": placeholder,
        "volume_estimate": bool(volume_estimate),
        "bar_kind": "minute",
    }
    bars.append(bar)
    # Cap growth (full RTH ~390 minutes)
    if len(bars) > 500:
        del bars[:-480]
    ts_map[sym] = time.time()
    return bar


def ingest_session_tech_quotes(
    system: Any,
    quotes: Optional[Dict[str, Any]],
) -> int:
    """Upsert broker quotes into the shared session-tech minute cache. Returns symbols updated."""
    if not isinstance(quotes, dict) or not quotes:
        return 0
    n = 0
    now = datetime.now(timezone.utc)
    for raw_sym, quote in quotes.items():
        sym = str(raw_sym or "").strip().upper()
        if not sym:
            continue
        px = quote_last_price(quote)
        if px <= 0:
            continue
        if upsert_session_tech_minute_bar(
            system,
            sym,
            px,
            ts=now,
            session_volume=quote_session_volume(quote),
        ):
            n += 1
    # Side-effect-free for trading: record L1 ring for PO observe trajectory.
    try:
        from modules.session_tech_admit import record_equity_l1_from_quotes

        record_equity_l1_from_quotes(system, quotes)
    except Exception:
        pass
    return n


def seed_session_tech_bars(
    system: Any,
    symbol_bars: Dict[str, Sequence[Any]],
    *,
    bar_kind: str = "intraday_15m",
) -> int:
    """
    Merge provider / path bars into the shared session-tech cache.

    Default ``bar_kind=intraday_15m`` so TL/BO 15m seeds contribute to VWAP/RSI/MACD
    but are excluded from minute ``volume_ratio`` (see ``bar_has_usable_minute_volume``).

    Pass ``bar_kind="minute"`` only for true 1m RTH series.

    Returns number of symbols updated. Prefer ``ensure_session_tech_bars`` /
    ``ingest_session_tech_quotes`` for live hydration.
    """
    if not symbol_bars:
        return 0
    kind = str(bar_kind or "intraday_15m").strip().lower() or "intraday_15m"
    cache, ts_map, _vol = _ensure_session_tech_store(system)
    now = time.time()
    updated = 0
    for raw_sym, bars in symbol_bars.items():
        sym = str(raw_sym or "").strip().upper()
        if not sym or not bars:
            continue
        as_dicts = ohlcv_bars_to_session_dicts(bars)
        if len(as_dicts) < 1:
            continue
        for d in as_dicts:
            if not d.get("bar_kind"):
                d["bar_kind"] = kind
        existing = cache.get(sym) or []
        # Prefer richer series; never shrink a good cache with a single day bar.
        # Prefer keeping existing minute bars when seeding 15m on top of a warm cache.
        existing_minutes = [
            b for b in existing if _bar_kind(b) in ("", "minute") or bar_has_usable_minute_volume(b)
        ]
        if kind == "intraday_15m" and len(existing_minutes) >= 2:
            # Keep minutes for volume_ratio; merge 15m only if cache is otherwise empty.
            continue
        if kind == "minute" and as_dicts:
            # Yahoo/RTH 1m backfill: fill history under broker quote minutes.
            # Prefer existing broker volume prints when the same minute collides.
            def _bar_ts(b: Dict[str, Any]) -> Any:
                return b.get("timestamp") or b.get("datetime") or b.get("ts")

            has_ts = any(_bar_ts(b) is not None for b in list(as_dicts) + list(existing))
            if has_ts:
                by_key: Dict[str, Dict[str, Any]] = {}

                def _minute_key(b: Dict[str, Any], idx: int) -> str:
                    ts = _bar_ts(b)
                    try:
                        if hasattr(ts, "isoformat"):
                            if getattr(ts, "tzinfo", None) is None:
                                ts = ts.replace(tzinfo=timezone.utc)
                            return ts.astimezone(timezone.utc).strftime("%Y%m%d%H%M")
                    except Exception:
                        pass
                    return f"{ts!s}:{idx}"

                for i, b in enumerate(as_dicts):
                    by_key[_minute_key(b, i)] = b
                for i, b in enumerate(existing):
                    k = _minute_key(b, 10_000 + i)
                    # Prefer broker usable-volume minutes; otherwise keep Yahoo OHLC history.
                    if bar_has_usable_minute_volume(b) or (
                        k not in by_key and _bar_ts(b) is not None
                    ):
                        by_key[k] = b
                    elif bar_has_usable_minute_volume(b):
                        by_key[k] = b
                def _utc_sort_key(b: Dict[str, Any]) -> str:
                    # Yahoo bars carry PT-aware times, broker upserts UTC: sort on
                    # one clock (str() of mixed offsets misorders 06:50-07:00 vs 13:46Z).
                    ts = _bar_ts(b)
                    try:
                        if hasattr(ts, "astimezone"):
                            if getattr(ts, "tzinfo", None) is None:
                                ts = ts.replace(tzinfo=timezone.utc)
                            return ts.astimezone(timezone.utc).isoformat()
                    except Exception:
                        pass
                    return str(ts or "")

                merged_min = sorted(by_key.values(), key=_utc_sort_key)
                if len(merged_min) >= 2:
                    cache[sym] = merged_min
                    ts_map[sym] = now
                    updated += 1
                continue
            # No timestamps (unit fixtures): replace/append like pre-merge path.
            if len(as_dicts) >= len(existing) or len(existing) < 2:
                if kind == "intraday_15m" and existing:
                    # Append 15m behind minutes so VWAP has history without replacing minutes.
                    merged = list(existing)
                    for d in as_dicts:
                        if any(_ohlcv_nearly_identical(d, e) for e in merged):
                            continue
                        merged.append(d)
                    cache[sym] = merged
                else:
                    cache[sym] = as_dicts if len(as_dicts) >= 2 else (existing + as_dicts)
                if len(cache[sym]) >= 2:
                    ts_map[sym] = now
                    updated += 1
            continue
        if len(as_dicts) >= len(existing) or len(existing) < 2:
            if existing:
                # Keep any existing minute bars, then append non-minute context bars.
                merged = list(existing)
                for d in as_dicts:
                    if any(_ohlcv_nearly_identical(d, e) for e in merged):
                        continue
                    merged.append(d)
                cache[sym] = merged
            else:
                cache[sym] = list(as_dicts)
            if len(cache[sym]) >= 2:
                ts_map[sym] = now
                updated += 1
    return updated


def _ohlcv_nearly_identical(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """True when two session bars share the same OHLC (+ volume)."""
    keys = ("open", "high", "low", "close", "volume")
    for k in keys:
        if abs(_f(a.get(k)) - _f(b.get(k))) > 1e-9:
            return False
    return True


def _pad_thin_session_bars(
    cache: Dict[str, List[Dict[str, Any]]],
    ts_map: Dict[str, float],
    symbols: Sequence[str],
) -> int:
    """
    Guarantee >=2 bars when only a single quote/day bar exists.

    Uses the bar's own open as a prior-minute anchor so VWAP/vol compute can run
    on the same Cloud Run tick (multi-instance cold caches never accumulate).
    Pad anchors are excluded from trusted volume_ratio (bar_kind=pad_anchor).
    """
    from datetime import timedelta

    padded = 0
    for raw in symbols or []:
        sym = str(raw or "").strip().upper()
        if not sym:
            continue
        bars = list(cache.get(sym) or [])
        if len(bars) != 1:
            continue
        b = dict(bars[0])
        close_px = _f(b.get("close"))
        if close_px <= 0:
            continue
        open_px = _f(b.get("open"), close_px)
        if open_px <= 0:
            open_px = close_px
        ts = b.get("timestamp") or b.get("datetime")
        if isinstance(ts, datetime):
            anchor_ts = _normalize_ts(ts) - timedelta(minutes=1)
        else:
            anchor_ts = datetime.now(timezone.utc) - timedelta(minutes=1)
        vol = max(_f(b.get("volume"), 0.0), 1.0)
        anchor = {
            "timestamp": anchor_ts,
            "datetime": anchor_ts,
            "open": open_px,
            "high": max(open_px, close_px),
            "low": min(open_px, close_px),
            "close": open_px,
            "volume": vol,
            "bar_kind": "pad_anchor",
            "volume_placeholder": True,
        }
        if _f(b.get("volume"), 0.0) <= 0:
            b["volume"] = 1.0
            b["volume_placeholder"] = True
        cache[sym] = [anchor, b]
        ts_map[sym] = time.time()
        padded += 1
    return padded


def _priced_close_count(bars: Sequence[Dict[str, Any]]) -> int:
    return len([b for b in (bars or []) if _f(b.get("close")) > 0])


def _symbols_needing_fetch(
    system: Any,
    symbols: Sequence[str],
    *,
    max_age_sec: float,
) -> List[str]:
    cache, ts_map, _vol = _ensure_session_tech_store(system)
    now = time.time()
    need: List[str] = []
    seen: Set[str] = set()
    for raw in symbols or []:
        sym = str(raw or "").strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        bars = cache.get(sym) or []
        # Still chase day-OHLC seed when thin (<2) for richer history, but
        # compute_session_tech_from_bars can stamp from 1 bar after pad.
        if len(bars) < 2:
            need.append(sym)
            continue
        # Pad/day_seed/estimate bars satisfy count but not volume_ratio — keep
        # polling broker quotes (same path TL/BO watch already uses) until we
        # have ≥2 usable minute deltas. Do not early-return on warm-looking pads.
        if bars_lack_usable_volume_dicts(bars):
            need.append(sym)
            continue
        # RSI/MACD depth: quote-rotation leaves early Breakout symbols with 3–13
        # minutes — need RTH 1m backfill until ≥ RSI_MIN closes.
        if _priced_close_count(bars) < int(SESSION_TECH_RSI_MIN_CLOSES):
            need.append(sym)
            continue
        if max_age_sec <= 0:
            continue
        fetched_at = float(ts_map.get(sym, 0.0) or 0.0)
        if fetched_at <= 0 or (now - fetched_at) > max_age_sec:
            need.append(sym)
    return need


async def _backfill_yf_rth_1m_for_thin_rsi(
    system: Any,
    symbols: Sequence[str],
) -> int:
    """
    Seed today's RTH 1m bars via Yahoo when quote minutes are too thin for RSI.

    Broker quote deltas remain authoritative for volume_ratio (re-ingested after).
    Returns number of symbols seeded.
    """
    if not session_tech_yf_1m_backfill_enabled():
        return 0
    cache, _ts, _vol = _ensure_session_tech_store(system)
    thin: List[str] = []
    seen: Set[str] = set()
    for raw in symbols or []:
        sym = str(raw or "").strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        if _priced_close_count(cache.get(sym) or []) < int(SESSION_TECH_RSI_MIN_CLOSES):
            thin.append(sym)
    if not thin:
        return 0
    thin = thin[: int(SESSION_TECH_YF_1M_BATCH_CAP)]
    try:
        from modules.prime_data_manager import _yf_trendline_watch_intraday_bars

        fetched = await _yf_trendline_watch_intraday_bars(thin, priority=3)
    except Exception as exc:
        log.warning(
            "SESSION_TECH | stage=yf_1m_backfill_failed | n=%d | err=%s | grep=SESSION_TECH",
            len(thin),
            exc,
        )
        return 0
    if not isinstance(fetched, dict) or not fetched:
        return 0
    seeded = seed_session_tech_bars(system, fetched, bar_kind="minute")
    if seeded:
        log.info(
            "SESSION_TECH | stage=yf_1m_backfill | requested=%d | seeded=%d | "
            "grep=SESSION_TECH|BREAKOUT_SESSION_TECH",
            len(thin),
            seeded,
        )
    return int(seeded or 0)


async def ensure_session_tech_bars(
    system: Any,
    symbols: Sequence[str],
    *,
    max_age_sec: Optional[float] = None,
    quotes: Optional[Dict[str, Any]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Hydrate/refresh shared RTH 1m bars for any path (ORB / TL / BO / PO).

    Broker quotes → minute bars, day OHLC when thin, Yahoo RTH 1m when RSI-thin.
    """
    cache, ts_map, vol_map = _ensure_session_tech_store(system)

    if not session_tech_enabled():
        log.info("SESSION_TECH | skipped | reason=0DTE_CONVEX_SESSION_TECH=false")
        return cache

    age = float(max_age_sec) if max_age_sec is not None else session_tech_max_age_sec()
    need = _symbols_needing_fetch(system, symbols, max_age_sec=age)

    # Always ingest caller-supplied broker quotes (keeps minute bars warm every scan).
    ingested = 0
    if isinstance(quotes, dict) and quotes:
        ingested = ingest_session_tech_quotes(system, quotes)

    if not need and ingested == 0:
        # Still try RSI backfill for requested symbols that are thin after ingest.
        await _backfill_yf_rth_1m_for_thin_rsi(system, symbols)
        return cache

    dm = getattr(system, "data_manager", None)
    broker_hydrated = 0
    try:
        fetch_syms = list(need) if need else []
        # If we only ingested a subset, still refresh symbols that remain thin.
        fetch_syms = [s for s in fetch_syms if len(cache.get(s) or []) < 2 or s in need]
        broker_quotes: Dict[str, Any] = {}
        if dm and fetch_syms:
            try:
                broker_quotes = await dm.get_batch_quotes(fetch_syms, skip_cache=True) or {}
            except TypeError:
                broker_quotes = await dm.get_batch_quotes(fetch_syms) or {}
            except Exception as qex:
                log.warning(
                    "SESSION_TECH | stage=broker_quotes_failed | n=%d | err=%s | grep=SESSION_TECH",
                    len(fetch_syms),
                    qex,
                )
                broker_quotes = {}
            if broker_quotes:
                broker_hydrated = ingest_session_tech_quotes(system, broker_quotes)

        # Day OHLC seed for symbols still under 2 bars (broker intraday = today's OHLC).
        still = [s for s in (need or fetch_syms) if len(cache.get(s) or []) < 2]
        if dm and still:
            try:
                intraday = await dm.get_batch_intraday_data(still, interval="15m", bars=1) or {}
            except Exception as iex:
                intraday = {}
                log.debug(
                    "SESSION_TECH | stage=broker_intraday_failed | n=%d | err=%s",
                    len(still),
                    iex,
                )
            if intraday:
                # Day bar first, then any live minute bars so compute_session_tech has >=2.
                for sym, rows in intraday.items():
                    u = str(sym or "").strip().upper()
                    day_bars = ohlcv_bars_to_session_dicts(rows or [])
                    if not day_bars:
                        continue
                    existing = list(cache.get(u) or [])
                    if len(existing) >= 2:
                        continue
                    # Keep live minutes even when last == day close (old filter
                    # collapsed same-close ticks back to 1 bar → perpetual gaps).
                    merged = list(day_bars)
                    for b in existing:
                        if day_bars and _ohlcv_nearly_identical(b, day_bars[0]):
                            continue
                        merged.append(b)
                    # Day OHLC volume is cumulative session total — never treat as
                    # a minute print in volume_ratio (Jul 31 ~0.00 hard-gate miss).
                    day_vol = _f(merged[0].get("volume"), 0.0)
                    merged[0]["bar_kind"] = "day_seed"
                    if day_vol <= 0:
                        merged[0]["volume"] = 1.0
                        merged[0]["volume_placeholder"] = True
                    if day_vol > 0 and _f(vol_map.get(u), 0.0) <= 0:
                        # Seed cumulative map so the next quote yields a real delta.
                        vol_map[u] = day_vol
                    cache[u] = merged
                    ts_map[u] = time.time()
                    broker_hydrated += 1
                # Re-ingest quotes so the latest minute sits on top of day OHLC.
                if broker_quotes:
                    ingest_session_tech_quotes(system, broker_quotes)

        # RSI/MACD depth: Yahoo RTH 1m when still thin after broker path.
        yf_syms = list(dict.fromkeys([*(need or []), *[str(s or "").upper() for s in (symbols or [])]]))
        await _backfill_yf_rth_1m_for_thin_rsi(system, yf_syms)
        # Live quote minutes must sit on top of Yahoo history for volume_ratio.
        if isinstance(quotes, dict) and quotes:
            ingest_session_tech_quotes(system, quotes)
        elif broker_quotes:
            ingest_session_tech_quotes(system, broker_quotes)

        missing_pre_pad = [s for s in need if len(cache.get(s) or []) < 2]
        log.info(
            "SESSION_TECH | stage=hydrate | requested=%d | broker_quote_upserts=%d | "
            "caller_quote_upserts=%d | missing=%d | max_age_sec=%.0f | "
            "grep=SESSION_TECH|0DTE_SESSION_TECH",
            len(need),
            broker_hydrated,
            ingested,
            len(missing_pre_pad),
            age,
        )
        if missing_pre_pad:
            log.warning(
                "SESSION_TECH | stage=hydrate_gaps | missing_symbols=%s",
                ",".join(missing_pre_pad[:24])
                + ("..." if len(missing_pre_pad) > 24 else ""),
            )
    except Exception as exc:
        log.warning(
            "SESSION_TECH | bar_fetch_failed | n=%d | err=%s | grep=0DTE_SESSION_TECH",
            len(need),
            exc,
        )

    # Same-tick pad after broker attempts (also runs when broker throws): cold
    # Cloud Run instances never accumulate minutes across requests.
    pad_syms = list(dict.fromkeys([*(need or []), *[str(s or "") for s in (symbols or [])]]))
    padded = _pad_thin_session_bars(cache, ts_map, pad_syms)
    if padded:
        log.info(
            "SESSION_TECH | stage=pad_thin_bars | padded=%d | grep=SESSION_TECH",
            padded,
        )
    return cache


def ensure_session_tech_bars_sync(
    system: Any,
    symbols: Sequence[str],
    *,
    max_age_sec: Optional[float] = None,
    timeout_seconds: float = 12.0,
    quotes: Optional[Dict[str, Any]] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Sync wrapper so PO entry/exit/monitor stamps can hydrate bars without await."""
    import asyncio
    import concurrent.futures

    coro = ensure_session_tech_bars(
        system, symbols, max_age_sec=max_age_sec, quotes=quotes
    )
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(asyncio.run, coro)
        return future.result(timeout=float(timeout_seconds))


_TECH_REFRESH_LOCK = threading.Lock()
_TECH_REFRESH_GATHER_S = 0.04


def _refresh_session_tech_bars_batched(
    system: Any,
    symbol: str,
    *,
    timeout_seconds: float,
) -> None:
    """One broker bar fetch per gather window. Every waiting symbol is included.

    Snapshot persist must still fill. Do not skip because the monitor is busy.
    Several due names on the same tick share one 3s refresh instead of stacking.
    """
    sym = str(symbol or "").strip().upper()
    if not sym:
        return
    tick = getattr(system, "_unified_0dte_monitor_tick_id", None) if system is not None else None
    with _TECH_REFRESH_LOCK:
        done_tick = getattr(system, "_po_tech_refresh_done_tick", None) if system is not None else None
        done = getattr(system, "_po_tech_refresh_done", None) if system is not None else None
        if tick is not None and done_tick == tick and isinstance(done, set) and sym in done:
            return
        batch = getattr(system, "_po_tech_refresh_batch", None) if system is not None else None
        batch_tick = getattr(system, "_po_tech_refresh_batch_tick", None) if system is not None else None
        if system is None or batch_tick != tick or not isinstance(batch, dict) or batch.get("closed"):
            batch = {
                "syms": set(),
                "done": threading.Event(),
                "error": None,
                "closed": False,
            }
            if system is not None:
                setattr(system, "_po_tech_refresh_batch", batch)
                setattr(system, "_po_tech_refresh_batch_tick", tick)
            leader = True
        else:
            leader = False
        batch["syms"].add(sym)
    if leader:
        time.sleep(_TECH_REFRESH_GATHER_S)
        with _TECH_REFRESH_LOCK:
            batch["closed"] = True
            names = list(batch["syms"])
        try:
            ensure_session_tech_bars_sync(
                system, names, timeout_seconds=float(timeout_seconds)
            )
        except Exception as exc:
            batch["error"] = exc
        else:
            if system is not None:
                prior = getattr(system, "_po_tech_refresh_done", set())
                if getattr(system, "_po_tech_refresh_done_tick", None) != tick or not isinstance(prior, set):
                    prior = set()
                prior.update(names)
                setattr(system, "_po_tech_refresh_done", prior)
                setattr(system, "_po_tech_refresh_done_tick", tick)
        batch["done"].set()
        if system is not None:
            with _TECH_REFRESH_LOCK:
                if getattr(system, "_po_tech_refresh_batch", None) is batch:
                    setattr(system, "_po_tech_refresh_batch", None)
    else:
        batch["done"].wait(timeout=float(timeout_seconds) + 0.25)
    err = batch.get("error")
    if err is not None:
        raise err


async def ensure_session_tech_bar_cache(
    system: Any,
    symbols: Sequence[str],
) -> Dict[str, List[Dict[str, Any]]]:
    """Backward-compatible alias — prefers TTL refresh via ensure_session_tech_bars."""
    return await ensure_session_tech_bars(system, symbols)


def get_session_tech(
    system: Any,
    symbol: str,
    *,
    price: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Compute session tech stamp from the shared cache (call after ensure).

    Live Path V2 / monitor use the current hub, including the forming minute.
    Priority Optimizer persist uses ``read_session_tech_at`` so only bars that
    have already closed by the event clock are in the stamp.
    """
    cache, _ts, _vol = _ensure_session_tech_store(system)
    sym = str(symbol or "").strip().upper()
    bars = cache.get(sym) or []
    if not bars:
        return None
    return compute_session_tech_from_bars(
        bars, price=price, warmup_closes=session_tech_warmup_closes(system, sym)
    )


def get_session_tech_asof(
    system: Any,
    symbol: str,
    event_at: Any,
    *,
    price: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Session tech from cached bars whose minute close is ≤ event. No fetch."""
    cache, _ts, _vol = _ensure_session_tech_store(system)
    sym = str(symbol or "").strip().upper()
    sliced = bars_closed_at_or_before(cache.get(sym) or [], event_at)
    if not sliced:
        return None
    event = parse_session_tech_clock(event_at)
    return compute_session_tech_from_bars(
        sliced,
        price=price,
        warmup_closes=session_tech_warmup_closes(system, sym),
        now_utc=event,
    )


def _annotate_hub_read(
    tech: Optional[Dict[str, Any]],
    *,
    now_utc: datetime,
    max_age_s: float,
    refreshed_sync: bool = False,
    refresh_skip: Optional[str] = None,
) -> Dict[str, Any]:
    import uuid

    out: Dict[str, Any] = dict(tech or {})
    out["hub_read_id"] = str(uuid.uuid4())
    out["hub_read_at"] = now_utc.isoformat()
    out["refreshed_sync"] = bool(refreshed_sync)
    if refresh_skip:
        out["refresh_skip"] = refresh_skip
    asof = out.get("session_tech_asof_at") or session_tech_asof_from_bar(
        out.get("session_tech_bar_at")
    )
    pve_asof = session_tech_asof_from_bar(out.get("pve3_bar_at") or out.get("session_tech_bar_at"))
    out["session_tech_asof_at"] = asof
    tech_age = _age_seconds(now_utc, asof)
    pve_age = _age_seconds(now_utc, pve_asof)
    out["tech_age_s"] = tech_age
    out["pve3_age_s"] = pve_age
    cap = float(max_age_s)
    out["tech_fresh"] = tech_age is not None and tech_age <= cap
    out["pve3_fresh"] = (
        out.get("price_volume_efficiency_3m") is not None
        and pve_age is not None
        and pve_age <= cap
    )
    return out


def read_session_tech_at(
    system: Any,
    symbol: str,
    *,
    now_utc: Any,
    price: Optional[float] = None,
    max_age_s: float = 75.0,
    allow_sync_refresh: bool = False,
    refresh_timeout_s: float = 3.0,
) -> Dict[str, Any]:
    """One hub read sliced to bars closed by ``now_utc``. No extra broker call.

    The 20p Priority Optimizer snapshot owns this call. The 20% Save Profit
    Capture exit may reuse the same frozen dict on the same tick. Freshness
    is the bar close clock (session_tech_asof_at).
    """
    now = now_utc if isinstance(now_utc, datetime) else parse_session_tech_clock(now_utc)
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    sym = str(symbol or "").strip().upper()
    tech = get_session_tech_asof(system, sym, now, price=price)
    annotated = _annotate_hub_read(tech, now_utc=now, max_age_s=max_age_s)
    try:
        from modules.po_session_tech_tick import (
            eod_flattening,
            session_tech_tick_failed,
            session_tech_tick_primed,
        )

        if eod_flattening(system):
            allow_sync_refresh = False
            annotated["refresh_skip"] = "eod_flatten"
        elif session_tech_tick_failed(system):
            allow_sync_refresh = False
            annotated["refresh_skip"] = "refresh_failed"
        elif session_tech_tick_primed(system):
            allow_sync_refresh = False
            annotated["refresh_skip"] = "tick_primed"
    except Exception:
        pass
    need_refresh = allow_sync_refresh and (
        tech is None
        or annotated.get("price_volume_efficiency_3m") is None
        or not annotated.get("pve3_fresh")
        or not annotated.get("tech_fresh")
    )
    if not need_refresh:
        return annotated
    try:
        _refresh_session_tech_bars_batched(
            system, sym, timeout_seconds=float(refresh_timeout_s)
        )
    except Exception as exc:
        log.debug("SESSION_TECH_SAVE20_REFRESH_FAIL | symbol=%s | error=%s", sym, exc)
        annotated["refresh_skip"] = "refresh_failed"
        return annotated
    tech2 = get_session_tech_asof(system, sym, now, price=price)
    return _annotate_hub_read(
        tech2, now_utc=now, max_age_s=max_age_s, refreshed_sync=True
    )


def session_tech_warmup_closes(system: Any, symbol: str) -> List[float]:
    """Prior-session warm-up closes for ``symbol`` (today's seed only); [] when none."""
    try:
        from modules.session_tech_warmup import warmup_closes

        return warmup_closes(system, symbol)
    except Exception:
        return []


def session_tech_for_cached_symbol(
    system: Any,
    symbol: str,
    *,
    price: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Backward-compatible alias for get_session_tech."""
    return get_session_tech(system, symbol, price=price)


def is_session_tech_data_gap(failed_reasons: Sequence[str]) -> bool:
    """
    True when failures are only missing VWAP/volume data (soft-skip, never day-block).
    """
    if not failed_reasons:
        return False
    gap_tokens = ("volume_ratio_missing", "vwap_missing")
    cleaned = []
    for r in failed_reasons:
        s = str(r or "").strip().lower()
        if not s:
            continue
        head = s.split(":", 1)[0].strip()
        cleaned.append(head)
    if not cleaned:
        return False
    return all(c in gap_tokens for c in cleaned)
