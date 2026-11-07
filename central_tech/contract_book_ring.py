"""Per-contract option book ring for the central tech hub.

Every live 0DTE chain fetch (``OptionsChainManager.fetch_options_chain`` after a
broker hit, never a cache hit) records a compact book of the near-spot
contracts on both sides: strike, bid, ask, bid/ask size, IV, delta, volume and
open interest, plus the spot passed to the fetch and the fetch time.

Readers ask for one exact contract (symbol, side, strike) and get its earlier
observations. Premium trajectory v5 uses them as the ``*_30s_ago`` /
``*_5m_ago`` anchors at decision time (handoff / executed), before the trade has
its own premium path. Without this ring the entry lookbacks for
``option_l1_imbalance``, option sizes, IV and delta could never fill: the
symbol chain ring only kept chain totals and an ATM bid with no strike match
and no sizes.

Rules:
- Exact contract only. A sample for another strike is never returned.
- A book whose quotes are identical to the last recorded book for that symbol is
  the same observation re-read from cache. It is skipped, so the original fetch
  time is kept.
- No broker call. No Priority Optimizer import. Module-level, bounded.
"""

from __future__ import annotations

import math
import os
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

_LOCK = threading.Lock()
_RING: Dict[str, List[Dict[str, Any]]] = {}
_MAX_SAMPLES_PER_SYMBOL = 80
_MAX_SYMBOLS = 400
_STRIKES_PER_SIDE = 8
_STRIKE_TOL = 0.011


def _f(val: Any) -> Optional[float]:
    if val is None or val == "" or isinstance(val, bool):
        return None
    try:
        out = float(val)
    except (TypeError, ValueError):
        return None
    if out != out or math.isinf(out):
        return None
    return out


def _attr(contract: Any, *names: str) -> Any:
    for name in names:
        if isinstance(contract, dict):
            val = contract.get(name)
        else:
            val = getattr(contract, name, None)
        if val is not None:
            return val
    return None


def contract_book_ring_enabled() -> bool:
    raw = str(os.getenv("CENTRAL_TECH_CONTRACT_BOOK_RING", "true") or "true").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _side_key(option_type: Any) -> str:
    s = str(option_type or "").strip().lower()
    if s in ("c", "call", "calls", "long", "bull", "bullish"):
        return "call"
    if s in ("p", "put", "puts", "short", "bear", "bearish"):
        return "put"
    return ""


def _compact_contract(contract: Any) -> Optional[Dict[str, Any]]:
    strike = _f(_attr(contract, "strike", "strike_price"))
    if strike is None or strike <= 0:
        return None
    bid = _f(_attr(contract, "bid"))
    ask = _f(_attr(contract, "ask"))
    if (bid is None or bid <= 0) and (ask is None or ask <= 0):
        return None
    bid_size = _f(_attr(contract, "bid_size", "bidSize"))
    ask_size = _f(_attr(contract, "ask_size", "askSize"))
    delta = _f(_attr(contract, "delta"))
    return {
        "strike": strike,
        "bid": bid,
        "ask": ask,
        "bid_size": bid_size,
        "ask_size": ask_size,
        "iv": _f(_attr(contract, "implied_volatility", "iv")),
        # Absolute, matching the research snapshot ``delta`` convention.
        "delta": abs(delta) if delta is not None else None,
        "volume": _f(_attr(contract, "volume")),
        "open_interest": _f(_attr(contract, "open_interest")),
    }


def _near_spot(contracts: List[Any], spot: Optional[float]) -> List[Dict[str, Any]]:
    rows = [c for c in (_compact_contract(x) for x in contracts or []) if c is not None]
    if not rows:
        return []
    if spot is None or spot <= 0:
        # No spot and no deltas: there is no way to pick the near-spot strikes.
        return []
    rows.sort(key=lambda r: abs(float(r["strike"]) - float(spot)))
    rows = rows[: _STRIKES_PER_SIDE * 2]
    rows.sort(key=lambda r: float(r["strike"]))
    return rows


def _atm_center(chain: Dict[str, Any]) -> Optional[float]:
    """Strike of the call whose |delta| is nearest 0.5 (spot proxy when no spot was passed)."""
    best = None
    best_d = None
    for c in chain.get("calls") or []:
        k = _f(_attr(c, "strike", "strike_price"))
        d = _f(_attr(c, "delta"))
        if k is None or d is None:
            continue
        dist = abs(abs(d) - 0.5)
        if best_d is None or dist < best_d:
            best, best_d = k, dist
    return best


def _book_signature(book: Dict[str, List[Dict[str, Any]]]) -> Tuple:
    sig = []
    for side in ("call", "put"):
        for r in book.get(side) or []:
            sig.append((side, r["strike"], r["bid"], r["ask"], r["bid_size"], r["ask_size"], r["volume"]))
    return tuple(sig)


def record_chain_contract_book(
    symbol: str,
    chain: Any,
    *,
    underlying_price: Any = None,
    ts: Optional[float] = None,
) -> bool:
    """Record one live chain observation. Returns True when a sample was added."""
    if not contract_book_ring_enabled() or not isinstance(chain, dict):
        return False
    sym = str(symbol or "").strip().upper()
    if not sym:
        return False
    spot = _f(underlying_price)
    center = spot if spot is not None and spot > 0 else _atm_center(chain)
    book = {
        "call": _near_spot(list(chain.get("calls") or []), center),
        "put": _near_spot(list(chain.get("puts") or []), center),
    }
    if not book["call"] and not book["put"]:
        return False
    sample = {
        "t": float(ts if ts is not None else time.time()),
        "underlying_price": spot,
        "book": book,
        "_sig": _book_signature(book),
    }
    with _LOCK:
        buf = _RING.get(sym)
        if buf is None:
            if len(_RING) >= _MAX_SYMBOLS:
                # Drop the symbol with the oldest last sample.
                oldest = min(_RING.items(), key=lambda kv: kv[1][-1]["t"] if kv[1] else 0.0)[0]
                _RING.pop(oldest, None)
            buf = _RING.setdefault(sym, [])
        if buf and buf[-1].get("_sig") == sample["_sig"]:
            # Same quotes: a cached chain re-read. Keep the first fetch time.
            return False
        if buf and sample["t"] < float(buf[-1]["t"]):
            # Out of order: never rewrite history.
            return False
        buf.append(sample)
        if len(buf) > _MAX_SAMPLES_PER_SYMBOL:
            del buf[: len(buf) - _MAX_SAMPLES_PER_SYMBOL]
    return True


def contract_quote_priors(
    symbol: str,
    *,
    option_type: Any,
    strike: Any,
    before_epoch: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Earlier observations of one exact contract, oldest first.

    Each item is shaped for ``premium_trajectory_v5.normalize_quote_sample``:
    ``t``, ``option_bid``, ``option_ask``, ``option_bid_size``,
    ``option_ask_size``, ``implied_volatility``, ``delta``, ``underlying_price``,
    ``strike``.
    """
    sym = str(symbol or "").strip().upper()
    side = _side_key(option_type)
    k = _f(strike)
    if not sym or not side or k is None or k <= 0:
        return []
    with _LOCK:
        buf = list(_RING.get(sym) or [])
    out: List[Dict[str, Any]] = []
    for sample in buf:
        t = _f(sample.get("t"))
        if t is None:
            continue
        if before_epoch is not None and t > float(before_epoch) + 0.25:
            continue
        for row in (sample.get("book") or {}).get(side) or []:
            if abs(float(row["strike"]) - k) > _STRIKE_TOL:
                continue
            out.append(
                {
                    "t": t,
                    "option_bid": row.get("bid"),
                    "option_ask": row.get("ask"),
                    "option_bid_size": row.get("bid_size"),
                    "option_ask_size": row.get("ask_size"),
                    "implied_volatility": row.get("iv"),
                    "delta": row.get("delta"),
                    "underlying_price": sample.get("underlying_price"),
                    "strike": row.get("strike"),
                    "contract_book_source": "central_tech.contract_book_ring",
                }
            )
            break
    return out


def clear_contract_book_ring() -> None:
    """Tests only."""
    with _LOCK:
        _RING.clear()
