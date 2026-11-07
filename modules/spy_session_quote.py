"""One SPY session quote for the whole app.

Discovery adds SPY to the equity batch every scan (about every 10 seconds).
Trade profile, relative strength, and Priority Optimizer clocks read this
store. They do not each call get_quotes(["SPY"]).
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

SPY_SYMBOL = "SPY"
_MAX_AGE_SEC = 45.0
_RING_MAX = 48
_RING_MIN_SPACING_SEC = 8.0

_spy: Dict[str, Any] = {
    "pct": None,
    "price": None,
    "observed_at": None,
    "mono": 0.0,
}
_symbol: Dict[str, Dict[str, Any]] = {}
_marks: Dict[str, List[Dict[str, float]]] = {}


def with_spy(symbols: Sequence[str]) -> List[str]:
    """Put SPY on the batch without taking a rotate slot from the list window."""
    out: List[str] = []
    seen = set()
    for raw in (SPY_SYMBOL, *list(symbols or [])):
        sym = str(raw or "").strip().upper()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        out.append(sym)
    return out


def _num(raw: Any) -> Optional[float]:
    if raw is None or raw == "":
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if val != val:
        return None
    return val


def _field(quote: Any, *names: str) -> Any:
    if quote is None:
        return None
    for name in names:
        if isinstance(quote, dict):
            raw = quote.get(name)
        else:
            raw = getattr(quote, name, None)
        if raw not in (None, ""):
            return raw
    return None


def _change_pct(quote: Any) -> Optional[float]:
    """Session change percent. A broker 0.0 is missing, not a flat SPY."""
    for name in ("change_pct", "changePercent", "change_percent", "pct_change"):
        val = _num(_field(quote, name))
        if val is not None and val != 0.0:
            return val
    last = _num(_field(quote, "last_price", "last", "current_price", "price"))
    prev = _num(_field(quote, "previous_close", "previousClose", "prev_close"))
    if last is not None and prev is not None and prev > 0 and last > 0:
        return ((last - prev) / prev) * 100.0
    return None


def _price(quote: Any) -> Optional[float]:
    return _num(_field(quote, "last_price", "last", "current_price", "price"))


def _observed_at(quote: Any) -> str:
    raw = _field(
        quote,
        "quote_timestamp",
        "timestamp",
        "observed_at",
        "broker_quote_timestamp",
        "date_time",
    )
    if raw not in (None, ""):
        text = str(raw).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            if parsed <= now:
                return parsed.isoformat()
        except Exception:
            pass
    return datetime.now(timezone.utc).isoformat()


def _quote_for(quotes: Any, symbol: str) -> Any:
    if isinstance(quotes, dict):
        if symbol in quotes:
            return quotes[symbol]
        for key, val in quotes.items():
            if str(key).strip().upper() == symbol:
                return val
    if isinstance(quotes, list):
        for item in quotes:
            sym = str(_field(item, "symbol", "product", "ticker") or "").strip().upper()
            if sym == symbol:
                return item
    return None


def record_quote_batch(quotes: Any) -> None:
    """Remember SPY and a short equity mark ring from one discovery batch."""
    if not quotes:
        return
    spy = _quote_for(quotes, SPY_SYMBOL)
    if spy is not None:
        pct = _change_pct(spy)
        price = _price(spy)
        if pct is not None or price is not None:
            _spy["pct"] = pct if pct is not None else _spy.get("pct")
            _spy["price"] = price if price is not None else _spy.get("price")
            _spy["observed_at"] = _observed_at(spy)
            _spy["mono"] = time.monotonic()
    now = time.time()
    symbols: List[str] = []
    if isinstance(quotes, dict):
        symbols = [str(k).strip().upper() for k in quotes.keys()]
    elif isinstance(quotes, list):
        symbols = [
            str(_field(item, "symbol", "product", "ticker") or "").strip().upper()
            for item in quotes
        ]
    for sym in symbols:
        if not sym:
            continue
        quote = _quote_for(quotes, sym)
        pct = _change_pct(quote)
        price = _price(quote)
        if pct is not None or (price is not None and price > 0):
            _symbol[sym] = {
                "pct": pct,
                "price": price,
                "observed_at": _observed_at(quote),
                "mono": time.monotonic(),
            }
        if price is None or price <= 0:
            continue
        buf = _marks.setdefault(sym, [])
        if buf and now - float(buf[-1]["t"]) < _RING_MIN_SPACING_SEC:
            continue
        buf.append({"t": now, "price": float(price)})
        if len(buf) > _RING_MAX:
            del buf[: len(buf) - _RING_MAX]


def record_spy_quote(quote: Any) -> None:
    """A direct SPY fetch still lands in the same store."""
    if quote is None:
        return
    record_quote_batch({SPY_SYMBOL: quote})


def spy_snapshot(*, max_age_sec: float = _MAX_AGE_SEC) -> Optional[Dict[str, Any]]:
    """Current SPY percent, price, and quote time. None when the batch is stale."""
    pct = _spy.get("pct")
    if pct is None:
        return None
    try:
        age = time.monotonic() - float(_spy.get("mono") or 0.0)
    except (TypeError, ValueError):
        return None
    if age > float(max_age_sec):
        return None
    return {
        "spy_change_pct": float(pct),
        "spy_price": _spy.get("price"),
        "spy_observed_at": _spy.get("observed_at"),
        "age_sec": age,
    }


def spy_change_pct(*, max_age_sec: float = _MAX_AGE_SEC) -> Optional[float]:
    snap = spy_snapshot(max_age_sec=max_age_sec)
    if not snap:
        return None
    return float(snap["spy_change_pct"])


def symbol_snapshot(symbol: str, *, max_age_sec: float = _MAX_AGE_SEC) -> Optional[Dict[str, Any]]:
    """Last discovery quote for one symbol. None when that print is older than the clock limit."""
    snap = _symbol.get(str(symbol or "").strip().upper())
    if not isinstance(snap, dict):
        return None
    try:
        age = time.monotonic() - float(snap.get("mono") or 0.0)
    except (TypeError, ValueError):
        return None
    if age > float(max_age_sec):
        return None
    return {
        "change_pct": snap.get("pct"),
        "price": snap.get("price"),
        "observed_at": snap.get("observed_at"),
        "age_sec": age,
    }


def symbol_observed_at(symbol: str, *, max_age_sec: float = _MAX_AGE_SEC) -> Optional[str]:
    snap = symbol_snapshot(symbol, max_age_sec=max_age_sec)
    if not snap:
        return None
    observed = snap.get("observed_at")
    return str(observed) if observed not in (None, "") else None


def symbol_change_pct(symbol: str, *, max_age_sec: float = _MAX_AGE_SEC) -> Optional[float]:
    snap = symbol_snapshot(symbol, max_age_sec=max_age_sec)
    if not snap or snap.get("change_pct") is None:
        return None
    return float(snap["change_pct"])


def underlying_mark_ago(
    symbol: str,
    *,
    now_epoch: float,
    ago_sec: float,
    tolerance_sec: float,
) -> Optional[float]:
    """Last discovery print near ``now - ago``. Used for underlying 30s and 5m keys."""
    buf = _marks.get(str(symbol or "").strip().upper()) or []
    target = float(now_epoch) - float(ago_sec)
    best = None
    best_dist = None
    for sample in buf:
        ts = float(sample["t"])
        if ts > float(now_epoch) + 0.25:
            continue
        dist = abs(ts - target)
        if dist > float(tolerance_sec):
            continue
        if best_dist is None or dist < best_dist:
            best = float(sample["price"])
            best_dist = dist
    return best


def reset_for_tests() -> None:
    _spy.update({"pct": None, "price": None, "observed_at": None, "mono": 0.0})
    _symbol.clear()
    _marks.clear()
