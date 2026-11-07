"""SPY change and relative strength from quote caches already in the process.

Long-used Trade Profile key ``spy_change_pct``. No Priority Optimizer import.
A missing quote stays missing. Numeric zero from an omitted broker field is
not treated as a real change.
"""

from __future__ import annotations

import os
import time
from typing import Any, Dict, Optional, Tuple


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)) or default)
    except Exception:
        return float(default)


def po_cache_quote_max_age_sec() -> float:
    """Max age for discovery/path equity quotes reused by the tech stamp.

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


# One SPY change for the whole book. Discovery rotates 24 names and usually
# does not include SPY, so the profile was rejecting missing_spy_change_pct.
_SPY_CHANGE_CACHE: Dict[str, Any] = {"mono": 0.0, "pct": None}
def _spy_change_ttl_sec() -> float:
    try:
        from central_tech.decision_freeze import spy_max_age_sec

        return float(spy_max_age_sec())
    except Exception:
        return 45.0


# Equity quote caches that discovery / path watches already keep warm.
_QUOTE_CACHE_ATTRS = (
    "_discovery_merged_quote_cache",
    "_discovery_last_quotes",
    "_trendline_last_quotes",
    "_breakout_last_quotes",
)

def _quote_change_pct(q: Any) -> Optional[float]:
    """Session change %. Never returns invent ``0.0`` from missing broker fields.

    E*TRADE batch quotes often omit ``changePercent``; parsers historically coerced
    that to ``0.0``, which then short-circuited ``previous_close`` derivation and
    stamped fake ``rs_vs_spy=0.0`` into PO densify (Sep 8 Demo). Treat numeric
    zero as absent and derive from last vs previous_close when possible.
    """
    if q is None:
        return None
    for attr in ("change_pct", "changePercent", "change_percent", "pct_change"):
        raw = q.get(attr) if isinstance(q, dict) else getattr(q, attr, None)
        if raw is None or raw == "":
            continue
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        if val != 0.0:
            return val
        # 0.0 may be invent — fall through to previous_close derive.
    # Derive from last vs previous_close when broker batch omitted change_pct.
    last = _quote_last(q)
    prev = None
    for attr in ("previous_close", "previousClose", "prev_close", "close_prev"):
        raw = q.get(attr) if isinstance(q, dict) else getattr(q, attr, None)
        if raw is None:
            continue
        try:
            prev = float(raw)
            break
        except (TypeError, ValueError):
            continue
    if last is not None and prev is not None and prev > 0:
        return ((float(last) - float(prev)) / float(prev)) * 100.0
    return None


def _quote_last(q: Any) -> Optional[float]:
    if q is None:
        return None
    for attr in ("last", "last_price", "close", "price", "mark", "bid", "ask"):
        raw = q.get(attr) if isinstance(q, dict) else getattr(q, attr, None)
        if raw is None:
            continue
        try:
            val = float(raw)
        except (TypeError, ValueError):
            continue
        if val > 0:
            return val
    return None


def _all_quote_bags(system: Any) -> list:
    """All known in-process quote bags, newest mono first."""
    bags: list = []
    if system is None:
        return bags
    for attr in _QUOTE_CACHE_ATTRS:
        try:
            cache = getattr(system, attr, None)
        except Exception:
            continue
        if not isinstance(cache, dict):
            continue
        # Merged discovery cache: {mono, symbols, quotes}
        if isinstance(cache.get("quotes"), dict):
            bags.append((cache.get("quotes") or {}, cache.get("mono")))
            continue
        bags.append((cache, None))
    bags.sort(
        key=lambda item: float(item[1] or 0.0),
        reverse=True,
    )
    return bags


def _iter_quote_bags(system: Any) -> Tuple[Dict[str, Any], Optional[float]]:
    """Return the newest (quotes_dict, mono_ts) from known in-process caches."""
    bags = _all_quote_bags(system)
    if not bags:
        return {}, None
    return bags[0]


def _find_quote_in_bag(quotes: Dict[str, Any], sym: str) -> Optional[Any]:
    q = quotes.get(sym)
    if q is not None:
        return q
    for k, v in quotes.items():
        if str(k or "").strip().upper() == sym:
            return v
    return None


def lookup_cached_equity_quote(
    system: Any,
    symbol: str,
    *,
    max_age_sec: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Return a normalized quote dict from discovery/path caches when fresh enough.

    Searches **all** quote bags (not only the newest) so SPY can resolve from
    discovery while the symbol sits in a path-specific cache.
    """
    sym = str(symbol or "").strip().upper()
    if not sym or system is None:
        return None
    max_age = float(max_age_sec if max_age_sec is not None else po_cache_quote_max_age_sec())
    bags = _all_quote_bags(system)
    if not bags:
        return None

    best: Optional[Dict[str, Any]] = None
    best_age: Optional[float] = None
    for quotes, mono in bags:
        if not isinstance(quotes, dict) or not quotes:
            continue
        age = None
        if mono is not None:
            try:
                age = max(0.0, time.monotonic() - float(mono))
            except Exception:
                age = None
            if age is not None and max_age > 0 and age > max_age:
                continue
        q = _find_quote_in_bag(quotes, sym)
        if q is None:
            continue
        last = _quote_last(q)
        if last is None or last <= 0:
            continue
        out: Dict[str, Any] = {
            "symbol": sym,
            "current_price": last,
            "last_price": last,
            "data_source": "quote_cache",
            "data_quality": "good",
            "po_quote_cache_age_sec": age,
        }
        if isinstance(q, dict):
            for src, dst in (
                ("bid", "bid"),
                ("ask", "ask"),
                ("open", "open"),
                ("high", "high"),
                ("low", "low"),
                ("volume", "volume"),
                ("change", "change"),
                ("change_pct", "change_pct"),
                ("changePercent", "change_pct"),
                ("previous_close", "previous_close"),
                ("previousClose", "previous_close"),
            ):
                if q.get(src) is not None and out.get(dst) is None:
                    out[dst] = q.get(src)
        else:
            for attr, dst in (
                ("bid", "bid"),
                ("ask", "ask"),
                ("open", "open"),
                ("high", "high"),
                ("low", "low"),
                ("volume", "volume"),
                ("change", "change"),
                ("change_pct", "change_pct"),
                ("previous_close", "previous_close"),
            ):
                val = getattr(q, attr, None)
                if val is not None and out.get(dst) is None:
                    out[dst] = val
        chg = _quote_change_pct(q)
        if chg is not None:
            out["change_pct"] = chg
        # Prefer fresher bag; otherwise first hit.
        if best is None or (
            age is not None and (best_age is None or age < best_age)
        ):
            best = out
            best_age = age
            # Exact mono match that is freshest — keep scanning for better age.
    return best


def cached_spy_change_pct(system: Any) -> Optional[float]:
    """Session SPY percent change from the discovery SPY pin."""
    try:
        from modules.spy_session_quote import spy_change_pct

        pinned = spy_change_pct(max_age_sec=_spy_change_ttl_sec())
    except Exception:
        pinned = None
    if pinned is not None:
        _SPY_CHANGE_CACHE["pct"] = float(pinned)
        _SPY_CHANGE_CACHE["mono"] = time.monotonic()
        return float(pinned)
    now = time.monotonic()
    cached = _SPY_CHANGE_CACHE.get("pct")
    try:
        age = now - float(_SPY_CHANGE_CACHE.get("mono") or 0.0)
    except (TypeError, ValueError):
        age = _spy_change_ttl_sec()
    if cached is not None and age < _spy_change_ttl_sec():
        try:
            return float(cached)
        except (TypeError, ValueError):
            pass
    pct = None
    # Oct 7 2026 (fix A4): one SPY age cap. The 120 s fallback let a 2-minute
    # SPY print score as current; the fallback now honors the same 45 s TTL.
    spy_q = lookup_cached_equity_quote(system, "SPY", max_age_sec=_spy_change_ttl_sec())
    if spy_q is not None:
        pct = _quote_change_pct(spy_q)
    if pct is None:
        pct = _fetch_spy_change_pct(system)
    if pct is None:
        return None
    _SPY_CHANGE_CACHE["pct"] = float(pct)
    _SPY_CHANGE_CACHE["mono"] = now
    try:
        from modules.spy_session_quote import record_spy_quote

        record_spy_quote({"change_pct": float(pct)})
    except Exception:
        pass
    return float(pct)


def _fetch_spy_change_pct(system: Any) -> Optional[float]:
    if system is None or not hasattr(system, "_resolve_etrade_market_data_client"):
        return None
    try:
        client = system._resolve_etrade_market_data_client()
    except Exception:
        return None
    if client is None or not hasattr(client, "get_quotes"):
        return None
    try:
        quotes = client.get_quotes(["SPY"])
    except Exception:
        return None
    if not quotes:
        return None
    return _quote_change_pct(quotes[0])


def rs_vs_spy_from_quote_caches(
    system: Any,
    symbol: str,
    *,
    max_age_sec: Optional[float] = None,
) -> Optional[float]:
    """RS vs SPY from cached equity quotes (no broker call)."""
    stamped = stamp_rs_vs_spy_from_quote_caches(None, system, symbol, max_age_sec=max_age_sec)
    return stamped.get("rs_vs_spy") if stamped else None


def stamp_rs_vs_spy_from_quote_caches(
    row: Optional[Dict[str, Any]],
    system: Any,
    symbol: str,
    *,
    max_age_sec: Optional[float] = None,
) -> Dict[str, Any]:
    """Compute RS + SPY change from caches; optionally stamp onto ``row``."""
    out: Dict[str, Any] = {}
    sym_q = lookup_cached_equity_quote(system, symbol, max_age_sec=max_age_sec)
    spy_q = lookup_cached_equity_quote(system, "SPY", max_age_sec=max_age_sec)
    sym_chg = _quote_change_pct(sym_q) if sym_q else None
    if sym_chg is None:
        try:
            from modules.spy_session_quote import symbol_change_pct

            sym_chg = symbol_change_pct(symbol)
        except Exception:
            sym_chg = None
    spy_chg = _quote_change_pct(spy_q) if spy_q else None
    if spy_chg is None:
        spy_chg = cached_spy_change_pct(system)
    if spy_chg is None and sym_chg is None:
        return out
    try:
        if spy_chg is not None:
            out["spy_change_pct"] = float(spy_chg)
        try:
            from modules.spy_session_quote import spy_snapshot

            pinned = spy_snapshot()
        except Exception:
            pinned = None
        if isinstance(pinned, dict) and pinned.get("spy_observed_at"):
            out["spy_observed_at"] = pinned.get("spy_observed_at")
            if out.get("spy_change_pct") is None and pinned.get("spy_change_pct") is not None:
                out["spy_change_pct"] = float(pinned["spy_change_pct"])
            if pinned.get("spy_price") is not None and out.get("spy_price") is None:
                out["spy_price"] = pinned.get("spy_price")
        spy_last = _quote_last(spy_q)
        if spy_last is not None:
            out["spy_price"] = float(spy_last)
        if sym_chg is not None and spy_chg is not None:
            out["rs_vs_spy"] = float(sym_chg) - float(spy_chg)
    except (TypeError, ValueError):
        return out
    if isinstance(row, dict):
        for k, v in out.items():
            if v is None:
                continue
            if row.get(k) is None or (
                k in ("rs_vs_spy", "spy_change_pct")
                and _is_invent_zero(row.get(k))
            ):
                row[k] = v
    return out


def _is_invent_zero(val: Any) -> bool:
    try:
        return val is not None and float(val) == 0.0
    except (TypeError, ValueError):
        return False
