"""Trade Profile decision-time feature freeze (Oct 7 2026).

One concise object per Trade Profile evaluation, assembled from the in-app
central tech hub data that discovery already cached:

* ``values``   - every template key of the signal's side (+ derived shares) exactly
                 as the gate scored them,
* ``ages_sec`` / ``asof`` - per-source age at the gate and the source clock
                 (chain, underlying quote/L1, SPY, session 1m bars, 5m chain prior),
* ``gate_at``  - the decision clock.

The gate and the handoff snapshot both read this one object, so the handoff
records the decision-time values verbatim (RUNTIME_DECISION_DATA_REVIEW
2026-10-07, fixes A1-A8).

Broker cost: nothing here polls on a cadence. When a source a cell actually
gates is older than its cap, ONE targeted single-symbol refresh is allowed
(chain: one chain call; quote/SPY: one get_quotes call carrying the symbol and
SPY together). Refreshes are de-duplicated per symbol (async lock + re-check)
and rate limited by :class:`RefreshBudget` (per-session and per-minute caps,
configurable by env). When the budget is exhausted the gate defers (or rejects,
``TP_FREEZE_STALE_POLICY``) instead of deciding on stale data, and logs it.

The missing-key rule is unchanged (missing passes, except the per-cell warm-up
``missing_reject`` keys). A key older than its cap after the one refresh is
not scored as missing-pass: the entry waits (or rejects when
``TP_FREEZE_STALE_POLICY=reject``). This module makes keys fresh and records ages.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)

FREEZE_SCHEMA = "tp_decision_freeze_v1"

# ---------------------------------------------------------------------------
# Config (env, with conservative defaults). All are read per call so a test or
# an operator override applies without a restart of the module.
# ---------------------------------------------------------------------------


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw in (None, ""):
        return float(default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


def _env_int(name: str, default: int) -> int:
    return int(_env_float(name, float(default)))


def freeze_enabled() -> bool:
    raw = str(os.environ.get("TP_FREEZE_ENABLED", "true")).strip().lower()
    return raw not in ("0", "false", "no", "off")


def chain_max_age_sec() -> float:
    return max(5.0, _env_float("TP_FREEZE_CHAIN_MAX_AGE_SEC", 60.0))


def quote_max_age_sec() -> float:
    # Decision quote may be the scan quote for up to 2 minutes. A 45s cap
    # made Trade Profile call get_quotes once per name and one Oct 29 task
    # ran 486s before the break check finished.
    return max(2.0, _env_float("TP_FREEZE_QUOTE_MAX_AGE_SEC", 120.0))


def spy_max_age_sec() -> float:
    return max(5.0, _env_float("TP_FREEZE_SPY_MAX_AGE_SEC", 45.0))


def session_tech_max_age_sec() -> float:
    return max(10.0, _env_float("TP_FREEZE_SESSION_TECH_MAX_AGE_SEC", 90.0))


def prior_5m_horizon_sec() -> float:
    return _env_float("TP_FREEZE_5M_HORIZON_SEC", 300.0)


def prior_5m_tolerance_sec() -> float:
    return max(10.0, _env_float("TP_FREEZE_5M_PRIOR_TOL_SEC", 90.0))


def refresh_dedupe_sec() -> float:
    return max(1.0, _env_float("TP_FREEZE_REFRESH_DEDUPE_SEC", 20.0))


def stale_policy() -> str:
    raw = str(os.environ.get("TP_FREEZE_STALE_POLICY", "defer")).strip().lower()
    return raw if raw in ("defer", "reject") else "defer"


def budget_limits(kind: str) -> Tuple[int, int]:
    """(per-session cap, per-minute cap) for ``chain`` or ``quote`` refreshes."""
    if kind == "chain":
        return (
            _env_int("TP_FREEZE_CHAIN_REFRESH_BUDGET_SESSION", 300),
            _env_int("TP_FREEZE_CHAIN_REFRESH_PER_MIN", 8),
        )
    return (
        _env_int("TP_FREEZE_QUOTE_REFRESH_BUDGET_SESSION", 300),
        _env_int("TP_FREEZE_QUOTE_REFRESH_PER_MIN", 10),
    )


def max_age_by_source() -> Dict[str, float]:
    """The one scoring-age table. Every stamp, hydrate, and chain refresh reads this."""
    return {
        "chain": chain_max_age_sec(),
        "quote": quote_max_age_sec(),
        "spy": spy_max_age_sec(),
        "session_tech": session_tech_max_age_sec(),
    }


def apply_source_age_caps(
    values: Dict[str, Any],
    ages: Optional[Dict[str, Any]],
    *,
    caps: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Leave a key empty when its source family is older than the scoring cap.

    dir% (chain_5m) is not capped here — it is being phased out.
    """
    out = dict(values or {})
    if not ages:
        return out
    limits = caps or max_age_by_source()
    for key in list(out.keys()):
        src = KEY_SOURCE.get(key)
        if src in (None, "chain_5m"):
            continue
        age = _f(ages.get(src))
        cap = _f(limits.get(src))
        if age is not None and cap is not None and age > cap:
            out[key] = None
    return out


# ---------------------------------------------------------------------------
# Key -> source family
# ---------------------------------------------------------------------------

KEY_SOURCE: Dict[str, str] = {
    "call_chain_volume": "chain",
    "put_chain_volume": "chain",
    "call_chain_open_interest": "chain",
    "put_chain_open_interest": "chain",
    "total_chain_vol_oi": "chain",
    "call_chain_vol_oi": "chain",
    "put_chain_vol_oi": "chain",
    "call_share": "chain",
    "put_share": "chain",
    "chain_sanity_best_same_side_spread_pct": "chain",
    "delta": "chain",
    "otm_pct": "chain",
    "iv": "chain",
    "theta": "chain",
    "gamma": "chain",
    "vega": "chain",
    "option_l1_imbalance": "chain",
    "dir_pct_change_5m": "chain_5m",
    "underlying_l1_imbalance": "quote",
    "spy_change_pct": "spy",
    "atr": "session_tech",
    "rsi": "session_tech",
    "distance_from_vwap_atr": "session_tech",
    "price_volume_efficiency_3m": "session_tech",
    "break_distance_pct": "break",
}

AGE_CAPPED_SOURCES = ("chain", "quote", "spy", "session_tech")

# Keys cleared from the decision row before central_tech re-stamps them, so a
# value carried from signal creation / discovery is never scored as current.
SESSION_REFRESH_KEYS: Tuple[str, ...] = (
    "rsi",
    "rsi_14",
    "atr",
    "vwap",
    "vwap_distance_pct",
    "abs_vwap_distance_pct",
    "distance_from_vwap_atr",
    "price_volume_efficiency_3m",
    "price_volume_efficiency_5m",
    "price_change_1m_pct",
    "price_change_3m_pct",
    "price_change_5m_pct",
    "volume_1m",
    "volume_3m",
    "volume_5m",
    "session_tech_bar_at",
)
QUOTE_REFRESH_KEYS: Tuple[str, ...] = (
    "underlying_bid_size",
    "underlying_ask_size",
    "underlying_l1_imbalance",
    "underlying_l1_imbalance_delta_5s",
    "underlying_l1_imbalance_delta_15s",
    "underlying_spread_pct",
    "underlying_quote_age_sec",
    "underlying_quote_stale",
)
SPY_REFRESH_KEYS: Tuple[str, ...] = ("spy_change_pct", "rs_vs_spy")
CHAIN_REFRESH_KEYS: Tuple[str, ...] = (
    "call_chain_volume",
    "put_chain_volume",
    # desk aliases: ensure_volume_research_fields copies call_volume back
    # into call_chain_volume when the canonical key is missing.
    "call_volume",
    "put_volume",
    "call_chain_open_interest",
    "put_chain_open_interest",
    "total_chain_volume",
    "total_chain_open_interest",
    "put_call_volume_ratio",
    "put_call_oi_ratio",
    "total_chain_vol_oi",
    "call_chain_vol_oi",
    "put_chain_vol_oi",
    "iv",
    "implied_volatility",
    "representative_iv",
    "delta",
    "representative_delta",
    "otm_pct",
    "representative_strike",
    "gamma",
    "theta",
    "vega",
    "chain_sanity_best_same_side_spread_pct",
    "chain_sanity_best_same_side_volume",
    "representative_spread_pct",
    "avg_bid_ask_spread_pct",
    "option_l1_imbalance",
    "option_bid_size",
    "option_ask_size",
    "representative_bid",
    "representative_ask",
    "direction_volume_pct",
    # t166u: dir_pct_change_5m prior meta (age / strict|fallback / reason).
    "dir_pct_prior_age_s",
    "dir_pct_prior_window",
    "dir_pct_missing_reason",
    "dir_pct_approach_lead_s",
    "dir_pct_approach_source",
)
# Indicator keys that may be restored from the signal row when central_tech
# cannot produce them, but only when the carry is young enough (logged).
CARRY_RESTORABLE_KEYS: Tuple[str, ...] = (
    "rsi",
    "atr",
    "vwap_distance_pct",
    "distance_from_vwap_atr",
    "price_volume_efficiency_3m",
    "price_change_3m_pct",
    "volume_3m",
)


def _f(raw: Any) -> Optional[float]:
    if raw is None or raw == "" or isinstance(raw, bool):
        return None
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    if val != val:
        return None
    return val


def _iso(epoch: Optional[float]) -> Optional[str]:
    if epoch is None:
        return None
    try:
        return datetime.fromtimestamp(float(epoch), timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _parse_epoch(raw: Any) -> Optional[float]:
    if raw in (None, ""):
        return None
    if isinstance(raw, datetime):
        dt = raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        val = float(raw)
        if val > 1e12:
            val /= 1000.0
        return val if val > 1e9 else None
    try:
        dt = datetime.fromisoformat(str(raw).strip().replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


_NESTED_SOURCES = ("metadata", "setup_payload", "qg_admit")


def _strip_nested(sub: Dict[str, Any], keyset: set, carried: Dict[str, Any]) -> Dict[str, Any]:
    cp = {}
    for k, v in sub.items():
        if k in keyset:
            if v is not None and k not in carried:
                carried[k] = v
            continue
        if k in ("qg_admit", "setup_payload") and isinstance(v, dict):
            v = _strip_nested(v, keyset, carried)
        cp[k] = v
    return cp


def clear_keys(row: Dict[str, Any], keys: Iterable[str], *, nested: bool = True) -> Dict[str, Any]:
    """Pop ``keys`` from ``row``; return what was there (non-None).

    ``nested`` (default): ``stamp_admit_tech`` -> ``promote_qg_admit_fields``
    re-lifts missing keys from ``row['metadata']`` / ``setup_payload`` /
    ``qg_admit`` (the values carried from signal creation). Those nested
    dicts are replaced on the ROW by shallow copies without ``keys``; the
    original objects (e.g. ``signal.metadata``) are not mutated.
    """
    carried: Dict[str, Any] = {}
    if not isinstance(row, dict):
        return carried
    keyset = set(keys)
    for key in keyset:
        if key in row:
            val = row.pop(key)
            if val is not None:
                carried[key] = val
    if nested:
        for nk in _NESTED_SOURCES:
            sub = row.get(nk)
            if isinstance(sub, dict) and any(
                k in keyset or k in ("qg_admit", "setup_payload") for k in sub
            ):
                row[nk] = _strip_nested(sub, keyset, carried)
    return carried


# ---------------------------------------------------------------------------
# Cell -> sources
# ---------------------------------------------------------------------------


def cell_keys(path: str, side: str, slot: Any = None) -> Optional[Tuple[str, ...]]:
    try:
        from easy0DTE.modules.trade_profile import cell_gated_keys

        return cell_gated_keys(path, side, slot)
    except Exception:
        return None


def cell_sources(path: str, side: str, slot: Any = None) -> set:
    keys = cell_keys(path, side, slot)
    if keys is None:
        return set(AGE_CAPPED_SOURCES) | {"chain_5m", "break"}
    return {KEY_SOURCE.get(k, "other") for k in keys}


# ---------------------------------------------------------------------------
# Refresh budget (per process / per session day, PT)
# ---------------------------------------------------------------------------


class RefreshBudget:
    def __init__(self) -> None:
        self._mu = threading.Lock()
        self._day: Optional[str] = None
        self.used: Dict[str, int] = {"chain": 0, "quote": 0}
        self.denied: Dict[str, int] = {"chain": 0, "quote": 0}
        self.deduped: Dict[str, int] = {"chain": 0, "quote": 0}
        self._minute: Dict[str, List[float]] = {"chain": [], "quote": []}
        self._last: Dict[Tuple[str, str], float] = {}
        self._alocks: Dict[Tuple[str, str], asyncio.Lock] = {}

    @staticmethod
    def _pt_day(now: float) -> str:
        try:
            from zoneinfo import ZoneInfo

            return datetime.fromtimestamp(now, ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
        except Exception:
            return datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d")

    def _roll(self, now: float) -> None:
        day = self._pt_day(now)
        if day != self._day:
            self._day = day
            self.used = {"chain": 0, "quote": 0}
            self.denied = {"chain": 0, "quote": 0}
            self.deduped = {"chain": 0, "quote": 0}
            self._minute = {"chain": [], "quote": []}
            self._last = {}

    def lock(self, kind: str, key: str) -> asyncio.Lock:
        k = (kind, str(key).upper())
        lk = self._alocks.get(k)
        if lk is None:
            lk = asyncio.Lock()
            self._alocks[k] = lk
        return lk

    def recent_refresh_age(self, kind: str, key: str, now: Optional[float] = None) -> Optional[float]:
        now = time.time() if now is None else now
        with self._mu:
            t = self._last.get((kind, str(key).upper()))
        return None if t is None else max(0.0, now - t)

    def try_acquire(self, kind: str, key: str, now: Optional[float] = None) -> Tuple[bool, str]:
        now = time.time() if now is None else now
        sess_cap, min_cap = budget_limits(kind)
        with self._mu:
            self._roll(now)
            window = [t for t in self._minute.get(kind, []) if now - t < 60.0]
            self._minute[kind] = window
            if self.used.get(kind, 0) >= sess_cap:
                self.denied[kind] = self.denied.get(kind, 0) + 1
                return False, "budget_session"
            if len(window) >= min_cap:
                self.denied[kind] = self.denied.get(kind, 0) + 1
                return False, "budget_minute"
            self.used[kind] = self.used.get(kind, 0) + 1
            window.append(now)
            self._last[(kind, str(key).upper())] = now
            return True, "ok"

    def note_dedupe(self, kind: str) -> None:
        with self._mu:
            self.deduped[kind] = self.deduped.get(kind, 0) + 1

    def stats(self) -> Dict[str, Any]:
        with self._mu:
            return {
                "day": self._day,
                "used": dict(self.used),
                "denied": dict(self.denied),
                "deduped": dict(self.deduped),
                "limits": {k: budget_limits(k) for k in ("chain", "quote")},
            }


_GLOBAL_BUDGET = RefreshBudget()


def hard_cap_throttle_active(system: Any) -> bool:
    """OPTION_API_HARD_CAP (140 real broker calls/min) is throttling: no extra
    freeze refreshes until it clears (defer/reject per policy instead)."""
    try:
        return float(getattr(system, "_options_api_hard_cap_extra_sec", 0.0) or 0.0) > 0.0
    except (TypeError, ValueError):
        return False


def get_refresh_budget(system: Any = None) -> RefreshBudget:
    if system is None:
        return _GLOBAL_BUDGET
    bud = getattr(system, "_tp_freeze_refresh_budget", None)
    if not isinstance(bud, RefreshBudget):
        bud = RefreshBudget()
        try:
            setattr(system, "_tp_freeze_refresh_budget", bud)
        except Exception:
            return _GLOBAL_BUDGET
    return bud


# ---------------------------------------------------------------------------
# Source ages (one clock per source: the time the app observed it)
# ---------------------------------------------------------------------------


def chain_age_sec(chain_mgr: Any, symbol: str, spot: Optional[float] = None) -> Optional[float]:
    if chain_mgr is None or not hasattr(chain_mgr, "cached_chain_age_seconds"):
        return None
    sym = str(symbol or "").strip().upper()
    for kwargs in ({"underlying_price": spot} if spot else {}, {}):
        try:
            age = chain_mgr.cached_chain_age_seconds(sym, **kwargs)
        except TypeError:
            continue
        except Exception:
            return None
        if age is not None:
            return float(age)
    return None


def quote_state(system: Any, symbol: str, now: Optional[float] = None) -> Dict[str, Any]:
    """Underlying L1 quote age by receipt time (the L1 ring sample ``t``)."""
    now = time.time() if now is None else now
    out: Dict[str, Any] = {"age_sec": None, "observed_at": None, "exchange_age_sec": None}
    if system is None:
        return out
    try:
        from modules.session_tech_admit import _ensure_equity_l1_ring

        ring = _ensure_equity_l1_ring(system).get(str(symbol or "").strip().upper()) or []
    except Exception:
        ring = []
    if not ring:
        return out
    latest = ring[-1]
    t = _f(latest.get("t"))
    if t is not None:
        out["age_sec"] = max(0.0, now - t)
        out["observed_at"] = _iso(t)
    ex = _parse_epoch(latest.get("quote_ts"))
    if ex is not None:
        out["exchange_age_sec"] = max(0.0, now - ex)
    return out


def spy_state(now: Optional[float] = None) -> Dict[str, Any]:
    out: Dict[str, Any] = {"age_sec": None, "observed_at": None, "spy_change_pct": None}
    try:
        from modules.spy_session_quote import spy_snapshot

        snap = spy_snapshot(max_age_sec=1e12)
    except Exception:
        snap = None
    if isinstance(snap, dict):
        out["age_sec"] = _f(snap.get("age_sec"))
        out["spy_change_pct"] = _f(snap.get("spy_change_pct"))
        now = time.time() if now is None else now
        if out["age_sec"] is not None:
            # Receipt clock (monotonic age mapped to wall time).
            out["observed_at"] = _iso(now - float(out["age_sec"]))
    return out


def session_tech_state(system: Any, symbol: str, now: Optional[float] = None) -> Dict[str, Any]:
    """Age of the shared 1m bar cache for the symbol (last quote upsert)."""
    now = time.time() if now is None else now
    out: Dict[str, Any] = {"age_sec": None, "observed_at": None}
    if system is None:
        return out
    sym = str(symbol or "").strip().upper()
    ts_map = getattr(system, "_session_tech_bar_fetched_at", None)
    t = _f(ts_map.get(sym)) if isinstance(ts_map, dict) else None
    if t is None:
        cache = getattr(system, "_session_tech_bar_cache", None)
        bars = cache.get(sym) if isinstance(cache, dict) else None
        if isinstance(bars, list) and bars and isinstance(bars[-1], dict):
            t = _parse_epoch(bars[-1].get("timestamp") or bars[-1].get("datetime"))
    if t is not None:
        out["age_sec"] = max(0.0, now - t)
        out["observed_at"] = _iso(t)
    return out


# ---------------------------------------------------------------------------
# Targeted refreshes
# ---------------------------------------------------------------------------


def _quote_to_dict(q: Any) -> Optional[Dict[str, Any]]:
    if q is None:
        return None
    if isinstance(q, dict):
        d = dict(q)
    else:
        d = {}
        for attr in (
            "symbol",
            "last_price",
            "change",
            "change_pct",
            "volume",
            "bid",
            "ask",
            "high",
            "low",
            "open",
            "bid_size",
            "ask_size",
            "date_time_utc",
            "date_time",
            "previous_close",
        ):
            if hasattr(q, attr):
                d[attr] = getattr(q, attr)
    last = _f(d.get("last_price") if d.get("last_price") is not None else d.get("last"))
    if last is not None:
        d.setdefault("last", last)
        d.setdefault("last_price", last)
        d.setdefault("price", last)
        d.setdefault("current_price", last)
    if d.get("volume") is not None:
        d.setdefault("totalVolume", d.get("volume"))
    if d.get("date_time_utc") is not None:
        d.setdefault("quote_ts", d.get("date_time_utc"))
    return d


async def _fetch_quotes(system: Any, symbols: Sequence[str]) -> Dict[str, Dict[str, Any]]:
    client = None
    try:
        client = system._resolve_etrade_market_data_client()
    except Exception:
        client = None
    if client is None or not hasattr(client, "get_quotes"):
        return {}

    def _call() -> Any:
        try:
            return client.get_quotes(list(symbols), fail_fast=True)
        except TypeError:
            return client.get_quotes(list(symbols))

    try:
        raw = await asyncio.to_thread(_call)
    except Exception as exc:
        log.info("TP_FREEZE_REFRESH | kind=quote | symbols=%s | action=failed | err=%s", ",".join(symbols), exc)
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for q in raw or []:
        d = _quote_to_dict(q)
        if not d:
            continue
        sym = str(d.get("symbol") or "").strip().upper()
        if sym:
            out[sym] = d
    recorder = getattr(system, "_record_option_api_usage", None)
    if callable(recorder):
        try:
            recorder(chain_calls=0, quote_calls=1, symbols_active=len(symbols))
        except Exception:
            pass
    return out


def _ingest_quotes(system: Any, quotes: Dict[str, Dict[str, Any]]) -> None:
    """Feed a targeted quote into the same central stores discovery feeds."""
    if not quotes:
        return
    try:
        from modules.session_tech_indicators import ingest_session_tech_quotes

        ingest_session_tech_quotes(system, quotes)  # 1m bars + L1 ring
    except Exception:
        try:
            from modules.session_tech_admit import record_equity_l1_from_quotes

            record_equity_l1_from_quotes(system, quotes)
        except Exception:
            pass
    try:
        from modules.spy_session_quote import record_quote_batch

        record_quote_batch(quotes)
    except Exception:
        pass


def _quote_needs(system: Any, symbol: str, sources: set, now: float) -> Tuple[List[str], Dict[str, Any]]:
    states = {
        "quote": quote_state(system, symbol, now),
        "session_tech": session_tech_state(system, symbol, now),
        "spy": spy_state(now),
    }
    caps = max_age_by_source()
    need_sym = False
    need_spy = False
    for src in ("quote", "session_tech"):
        if src in sources:
            age = states[src]["age_sec"]
            if age is None or age > caps[src]:
                need_sym = True
    if "spy" in sources:
        age = states["spy"]["age_sec"]
        if age is None or age > caps["spy"]:
            need_spy = True
    symbols: List[str] = []
    if need_sym:
        symbols.append(str(symbol).strip().upper())
    if need_spy and "SPY" not in symbols:
        symbols.append("SPY")
    return symbols, states


async def ensure_fresh_quote_sources(
    system: Any,
    symbol: str,
    *,
    sources: set,
    log_tag: str = "TP_FREEZE",
) -> Dict[str, Any]:
    """Age-check quote / session-tech / SPY for the sources a cell gates; when one
    is over its cap, run at most ONE get_quotes([symbol, SPY]) under the budget."""
    result: Dict[str, Any] = {"refresh": "none", "symbols": []}
    sym = str(symbol or "").strip().upper()
    if system is None or not sym or not freeze_enabled():
        return result
    # The discovery scan just quoted this name. Do not issue another
    # get_quotes per symbol. On 2026-10-29 a 45s age cap did that for the
    # whole window and one task ran 486s (22 live quotes) before the break
    # check finished. The scan quote stays the decision quote for 2 minutes.
    try:
        from modules.open_position_quote_orchestration import MERGED_DISCOVERY_CACHE_ATTR

        cache = getattr(system, MERGED_DISCOVERY_CACHE_ATTR, None)
        if isinstance(cache, dict):
            age = time.monotonic() - float(cache.get("mono") or 0.0)
            have = cache.get("symbols") or set()
            if not isinstance(have, set):
                have = {str(s or "").strip().upper() for s in have}
            if 0.0 <= age <= 120.0 and sym in have:
                result["refresh"] = "scan_quote"
                result["scan_quote_age_sec"] = round(age, 1)
                if age > quote_max_age_sec():
                    log.info(
                        "%s | stage=tp_freeze_refresh | kind=quote | symbol=%s | "
                        "action=scan_quote | age_sec=%.1f | grep=TP_FREEZE_REFRESH",
                        log_tag,
                        sym,
                        age,
                    )
                return result
    except Exception:
        pass
    now = time.time()
    symbols, states = _quote_needs(system, sym, sources, now)
    result["states_before"] = states
    if not symbols:
        return result
    budget = get_refresh_budget(system)
    key = ",".join(symbols)
    async with budget.lock("quote", key):
        now = time.time()
        symbols, states = _quote_needs(system, sym, sources, now)
        if not symbols:
            budget.note_dedupe("quote")
            result["refresh"] = "deduped"
            return result
        result["symbols"] = list(symbols)
        if hard_cap_throttle_active(system):
            ok, why = False, "hard_cap_throttle"
        else:
            ok, why = budget.try_acquire("quote", ",".join(symbols), now)
        if not ok:
            result["refresh"] = f"budget_exhausted:{why}"
            log.warning(
                "%s | stage=tp_freeze_refresh | kind=quote | symbol=%s | symbols=%s | "
                "action=budget_exhausted | reason=%s | grep=TP_FREEZE_BUDGET",
                log_tag,
                sym,
                ",".join(symbols),
                why,
            )
            return result
        quotes = await _fetch_quotes(system, symbols)
        if quotes:
            _ingest_quotes(system, quotes)
            result["refresh"] = "live"
        else:
            result["refresh"] = "failed"
        log.info(
            "%s | stage=tp_freeze_refresh | kind=quote | symbol=%s | symbols=%s | action=%s | "
            "age_quote=%s | age_session_tech=%s | age_spy=%s | grep=TP_FREEZE_REFRESH",
            log_tag,
            sym,
            ",".join(symbols),
            result["refresh"],
            _fmt_age(states["quote"]["age_sec"]),
            _fmt_age(states["session_tech"]["age_sec"]),
            _fmt_age(states["spy"]["age_sec"]),
        )
    return result


def plan_chain_refresh(
    chain_mgr: Any,
    symbol: str,
    *,
    spot: Optional[float] = None,
    legacy_reuse_sec: float = 300.0,
) -> Dict[str, Any]:
    """Decide cache vs live for the decision chain.

    ``cache``  - cached chain age <= TP cap.
    ``live``   - reason ``missing_or_expired`` (pre-existing live fetch: the old
                 policy also fetched) or ``tp_age_cap`` (cache exists but is older
                 than the TP cap: the extra, budgeted refresh).
    """
    age = chain_age_sec(chain_mgr, symbol, spot)
    cap = chain_max_age_sec()
    if age is not None and age <= cap:
        try:
            from easy0DTE.modules.convex_chain_skew import (
                chain_has_two_sided_quotes,
                peek_cached_options_chain,
            )

            cached = peek_cached_options_chain(chain_mgr, symbol, spot)
            if cached is not None and not chain_has_two_sided_quotes(cached):
                return {
                    "action": "live",
                    "age_sec": age,
                    "cap_sec": cap,
                    "reason": "no_two_sided_quotes",
                }
        except Exception:
            pass
        return {"action": "cache", "age_sec": age, "cap_sec": cap, "reason": "fresh"}
    if age is None or age > float(legacy_reuse_sec):
        return {"action": "live", "age_sec": age, "cap_sec": cap, "reason": "missing_or_expired"}
    return {"action": "live", "age_sec": age, "cap_sec": cap, "reason": "tp_age_cap"}


def acquire_chain_refresh(system: Any, symbol: str, plan: Dict[str, Any], *, log_tag: str = "TP_FREEZE") -> Dict[str, Any]:
    """Budget gate for a ``tp_age_cap`` chain refresh. Pre-existing fetches pass."""
    plan = dict(plan or {})
    if plan.get("action") != "live" or plan.get("reason") != "tp_age_cap":
        return plan
    budget = get_refresh_budget(system)
    if hard_cap_throttle_active(system):
        ok, why = False, "hard_cap_throttle"
    else:
        ok, why = budget.try_acquire("chain", symbol)
    if ok:
        return plan
    plan["action"] = "defer"
    plan["reason"] = f"tp_freeze_budget:{why}"
    log.warning(
        "%s | stage=tp_freeze_refresh | kind=chain | symbol=%s | chain_age=%s | cap=%s | "
        "action=budget_exhausted | reason=%s | policy=%s | grep=TP_FREEZE_BUDGET",
        log_tag,
        symbol,
        _fmt_age(plan.get("age_sec")),
        plan.get("cap_sec"),
        why,
        stale_policy(),
    )
    return plan


# ---------------------------------------------------------------------------
# Freeze context (gate side) and freeze object
# ---------------------------------------------------------------------------


def _fmt_age(age: Any) -> str:
    val = _f(age)
    return "NA" if val is None else f"{val:.1f}"


def collect_freeze_context(
    system: Any,
    row: Dict[str, Any],
    *,
    symbol: str,
    chain_mgr: Any = None,
    chain_plan: Optional[Dict[str, Any]] = None,
    quote_refresh: Optional[Dict[str, Any]] = None,
    carry: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Measure every source age right before the Trade Profile evaluation."""
    now = time.time()
    sym = str(symbol or "").strip().upper()
    spot = _f(row.get("current_price") or row.get("underlying_price") or row.get("price"))
    c_age = chain_age_sec(chain_mgr, sym, spot) if chain_mgr is not None else None
    if c_age is None:
        c_age = _f(row.get("tp_chain_age_sec"))
    q = quote_state(system, sym, now)
    s = spy_state(now)
    st = session_tech_state(system, sym, now)
    prior = _f(row.get("dir_pct_change_5m_prior_age_sec"))
    ages = {
        "chain": c_age,
        "quote": q["age_sec"],
        "spy": s["age_sec"],
        "session_tech": st["age_sec"],
        "dir5m_prior": prior,
    }
    asof = {
        "chain": _iso(now - c_age) if c_age is not None else None,
        "quote": q["observed_at"],
        "spy": s["observed_at"],
        "session_tech": st["observed_at"],
    }
    return {
        "measured_at": _iso(now),
        "ages_sec": ages,
        "asof": asof,
        "quote_exchange_age_sec": q.get("exchange_age_sec"),
        "chain_source": str(row.get("chain_attempt") or (chain_plan or {}).get("action") or "NA"),
        "refresh": {
            "chain": (chain_plan or {}).get("reason") if (chain_plan or {}).get("action") != "cache" else "none",
            "chain_action": (chain_plan or {}).get("action"),
            "quote": (quote_refresh or {}).get("refresh", "none"),
        },
        "carry": dict(carry or {}),
        "dir5m": {
            "prior_age_s": _f(row.get("dir_pct_prior_age_s")),
            "window": row.get("dir_pct_prior_window"),
            "missing_reason": row.get("dir_pct_missing_reason"),
            "approach_lead_s": _f(row.get("dir_pct_approach_lead_s")),
            "approach_source": row.get("dir_pct_approach_source"),
            # t204u: clocks so the daily certification can prove causality
            # (prior_observed_at < now_chain_observed_at <= asof_at <= freeze).
            "now_chain_age_s": _f(row.get("dir_pct_now_chain_age_s")),
            "now_chain_observed_at": row.get("dir_pct_now_chain_observed_at"),
            "prior_observed_at": row.get("dir_pct_prior_observed_at"),
            "asof_at": row.get("dir_pct_asof_at"),
        },
    }


def stale_gated(cell: Optional[Sequence[str]], ages: Dict[str, Any]) -> List[str]:
    """Gated keys whose source is over its cap (``key:source:age``)."""
    caps = max_age_by_source()
    out: List[str] = []
    keys = list(cell) if cell is not None else list(KEY_SOURCE)
    for key in keys:
        src = KEY_SOURCE.get(key)
        if src not in caps:
            continue
        age = _f(ages.get(src))
        if age is not None and age > caps[src]:
            out.append(f"{key}:{src}:{age:.0f}")
    return out


def freeze_value_keys(side: str) -> Tuple[str, ...]:
    try:
        from easy0DTE.modules.trade_profile import template_keys

        keys = list(template_keys(side))
    except Exception:
        keys = list(KEY_SOURCE)
    for extra in ("call_share", "put_share"):
        if extra not in keys:
            keys.append(extra)
    return tuple(keys)


def _share(values: Dict[str, Any], key: str) -> Optional[float]:
    c = _f(values.get("call_chain_volume"))
    p = _f(values.get("put_chain_volume"))
    if c is None or p is None or c + p <= 0:
        return None
    return (p if key == "put_share" else c) / (c + p)


def build_decision_freeze(
    *,
    values: Dict[str, Any],
    path: str,
    side: str,
    slot: Any,
    symbol: str,
    trade_id: Any = None,
    profile: str = "",
    profile_version: str = "",
    admitted: Optional[bool] = None,
    reason: str = "",
    stage: str = "",
    missing: Sequence[str] = (),
    context: Optional[Dict[str, Any]] = None,
    gate_at: Optional[str] = None,
) -> Dict[str, Any]:
    ctx = dict(context or {})
    side_u = str(side or "").strip().upper()
    keys = freeze_value_keys(side_u)
    vals: Dict[str, Any] = {}
    for key in keys:
        val = values.get(key)
        if val is None and key in ("call_share", "put_share"):
            val = _share(values, key)
        vals[key] = _f(val) if val is not None else None
    cell = cell_keys(path, side_u, slot)
    ages = dict(ctx.get("ages_sec") or {})
    missing_gated = [k for k in (cell or ()) if vals.get(k) is None and values.get(k) is None]
    return {
        "schema": FREEZE_SCHEMA,
        "gate_at": gate_at or datetime.now(timezone.utc).isoformat(),
        "symbol": str(symbol or "").upper(),
        "path": path,
        "side": side_u,
        "slot": slot,
        "trade_id": trade_id,
        "profile": profile,
        "profile_version": profile_version,
        "admitted": admitted,
        "reason": reason,
        "stage": stage,
        "values": vals,
        "cell_keys": list(cell) if cell is not None else None,
        "missing": list(missing or ()),
        "missing_gated": missing_gated,
        "stale_gated": stale_gated(cell, ages),
        "ages_sec": ages,
        "asof": dict(ctx.get("asof") or {}),
        "chain_age_sec": _f(ages.get("chain")),
        "chain_asof": (ctx.get("asof") or {}).get("chain"),
        "max_age_sec": max_age_by_source(),
        "quote_exchange_age_sec": ctx.get("quote_exchange_age_sec"),
        "chain_source": ctx.get("chain_source"),
        "refresh": dict(ctx.get("refresh") or {}),
        "carry_keys": sorted((ctx.get("carry") or {}).keys()),
        "dir5m": dict(ctx.get("dir5m") or {}),
        "context_measured": bool(ctx),
    }


def freeze_log_suffix(freeze: Optional[Dict[str, Any]]) -> str:
    if not isinstance(freeze, dict):
        return ""
    ages = freeze.get("ages_sec") or {}
    ref = freeze.get("refresh") or {}
    return (
        f"freeze gate_at={freeze.get('gate_at')} "
        f"ages=chain:{_fmt_age(ages.get('chain'))},quote:{_fmt_age(ages.get('quote'))},"
        f"spy:{_fmt_age(ages.get('spy'))},session_tech:{_fmt_age(ages.get('session_tech'))},"
        f"dir5m_prior:{_fmt_age(ages.get('dir5m_prior'))} "
        f"chain_src={freeze.get('chain_source') or 'NA'} "
        f"refresh=chain:{ref.get('chain') or 'none'},quote:{ref.get('quote') or 'none'} "
        f"stale=[{','.join(freeze.get('stale_gated') or [])}] "
        f"missing_gated=[{','.join(freeze.get('missing_gated') or [])}] "
        f"carry=[{','.join(freeze.get('carry_keys') or [])}]"
    )


def find_freeze(row: Any) -> Optional[Dict[str, Any]]:
    """The gate freeze carried on a row (row, metadata, setup payload)."""
    if not isinstance(row, dict):
        return None
    fz = row.get("po_decision_freeze")
    if isinstance(fz, dict) and fz.get("schema") == FREEZE_SCHEMA:
        return fz
    for nested in ("metadata", "setup_payload", "orb_signal"):
        sub = row.get(nested)
        if isinstance(sub, dict):
            fz = sub.get("po_decision_freeze")
            if isinstance(fz, dict) and fz.get("schema") == FREEZE_SCHEMA:
                return fz
    return None


_ASOF_TO_ROW = {
    "chain": ("po_chain_asof_at", "chain_observed_at"),
    "quote": ("po_quote_asof_at", "quote_observed_at"),
    "spy": ("po_spy_asof_at", "spy_observed_at"),
    "session_tech": ("po_session_tech_asof_at", "session_tech_bar_at"),
}


def apply_freeze_to_row(row: Dict[str, Any], freeze: Optional[Dict[str, Any]] = None) -> List[str]:
    """Make the handoff row carry exactly the frozen decision values + clocks.

    Returns the keys whose pre-apply row value differed from the freeze (the
    parity log). Values the freeze does not have are left alone.
    """
    if not isinstance(row, dict):
        return []
    fz = freeze if isinstance(freeze, dict) else find_freeze(row)
    if not isinstance(fz, dict):
        return []
    diffs: List[str] = []
    for key, val in (fz.get("values") or {}).items():
        cur = _f(row.get(key))
        if (cur is None) != (val is None) or (
            cur is not None and val is not None and abs(cur - float(val)) > 1e-9 * max(1.0, abs(float(val)))
        ):
            diffs.append(key)
        row[key] = val
        if key == "iv" and val is not None:
            row["implied_volatility"] = val
    for src, (clock, source_field) in _ASOF_TO_ROW.items():
        at = (fz.get("asof") or {}).get(src)
        if at:
            row[clock] = at
            row[source_field] = at
    row["po_decision_freeze"] = fz
    row["po_profile_decision"] = {
        "profile": fz.get("profile"),
        "side": fz.get("side"),
        "admitted": fz.get("admitted"),
        "reason": fz.get("reason"),
        "profile_version": fz.get("profile_version"),
        "scored_at": fz.get("gate_at"),
        "keys": dict(fz.get("values") or {}),
        "decision_digest": fz.get("decision_digest"),
    }
    if fz.get("gate_at"):
        row["po_decision_freeze_at"] = fz.get("gate_at")
        row["po_decision_gate_at"] = fz.get("gate_at")
    ages = fz.get("ages_sec") or {}
    for src in ("chain", "quote", "spy", "session_tech", "dir5m_prior"):
        row[f"tp_age_{src}_sec"] = ages.get(src)
    chain_age = _f(ages.get("chain"))
    if chain_age is None:
        chain_age = _f(fz.get("chain_age_sec"))
    if chain_age is not None:
        row["tp_age_chain_sec"] = chain_age
        row["tp_chain_age_sec"] = chain_age
        row["chain_age_sec"] = chain_age
    chain_asof = (fz.get("asof") or {}).get("chain") or fz.get("chain_asof")
    if chain_asof:
        row.setdefault("chain_asof", chain_asof)
    return diffs
