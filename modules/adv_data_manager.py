#!/usr/bin/env python3
"""
ADV Data Manager — Liquidity / ADV Guard (0DTE only)
====================================================

Average Daily Volume (dollar) cache for the **Liquidity / ADV Guard** used by
ORB1 Breakout · Trendline 0DTE · Breakout 0DTE via
``dte0_execution_risk_manager.apply_live_execution_risk_limits()``.

**Data policy (broker-token-first):**

1. Durable cache — local ``data/adv_cache.json`` + GCS ``{app}/adv_cache.json``
2. Broker quotes — ``averageVolume × last`` (E*TRADE AllQuoteDetails; other
   brokers implement the same fields on the quote dict)
3. Named liquid floors — catastrophe anti-block for mega-liquid names
4. Optional default floor — ``LIQUIDITY_GUARD_DEFAULT_ADV_FLOOR_USD`` (``0`` =
   disabled / fail-closed after named floors; no generic $200M substitute)
5. Yahoo / yfinance — **off by default**; only when
   ``LIQUIDITY_GUARD_ALLOW_YFINANCE_ADV=true`` (research/backfill)

**Not used by ORB SO ETF.** Slip Guard / ``get_adv_limit()`` retired.

See docs/LiquidityGuard.md.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger("adv_data_manager")

_GCS_ADV_RELATIVE = "adv_cache.json"

# Last-resort ADV$ floors when broker/GCS empty (catastrophe anti-block).
_LIQUID_ADV_FLOOR_USD: Dict[str, float] = {
    "SPY": 3.0e10,
    "QQQ": 2.0e10,
    "IWM": 8.0e9,
    "NVDA": 2.0e10,
    "TSLA": 1.2e10,
    "AAPL": 1.5e10,
    "MSFT": 1.2e10,
    "AMZN": 1.2e10,
    "META": 1.0e10,
    "GOOGL": 8.0e9,
    "AMD": 6.0e9,
    "HOOD": 1.5e9,
    "MAGS": 5.0e8,
    "SLV": 8.0e8,
    "GDX": 6.0e8,
    "INTC": 2.0e9,
    "ORCL": 2.5e9,
    "QCOM": 2.0e9,
    "MSTR": 2.0e9,
    "ASTS": 3.0e8,
    "HIMS": 4.0e8,
    "FLY": 2.0e8,
    "CRCL": 3.0e8,
    "PLTR": 3.0e9,
    "SMCI": 1.5e9,
    "ARM": 1.5e9,
    "AVGO": 4.0e9,
    "NFLX": 4.0e9,
    "COIN": 1.5e9,
    "SOFI": 8.0e8,
    "RIVN": 1.0e9,
    "MARA": 8.0e8,
    "IBIT": 2.0e9,
    "GLD": 2.0e9,
}


def _gcs_adv_path() -> str:
    try:
        from modules.runtime_mode import gcs_namespaced_coord_prefix

        return gcs_namespaced_coord_prefix(_GCS_ADV_RELATIVE)
    except Exception:
        ns = (os.getenv("GCS_APP_NAMESPACE") or "research_app").strip("/")
        return f"{ns}/{_GCS_ADV_RELATIVE}"


def _lookback_days() -> int:
    raw = os.getenv("LIQUIDITY_GUARD_ADV_LOOKBACK_DAYS", "90")
    try:
        return max(30, int(float(raw)))
    except (TypeError, ValueError):
        return 90


def _allow_yfinance_adv() -> bool:
    return str(os.getenv("LIQUIDITY_GUARD_ALLOW_YFINANCE_ADV", "false")).strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _default_adv_floor_usd() -> float:
    raw = os.getenv("LIQUIDITY_GUARD_DEFAULT_ADV_FLOOR_USD", "0")
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 0.0


def _quote_adv_dollars(quote: Any) -> Optional[float]:
    """Derive ADV$ from a broker quote dict / object (averageVolume × last)."""
    if quote is None:
        return None
    if not isinstance(quote, dict):
        quote = {
            "last": getattr(quote, "last_price", None) or getattr(quote, "last", None),
            "average_volume": getattr(quote, "average_volume", None),
            "previous_close": getattr(quote, "previous_close", None),
            "volume": getattr(quote, "volume", None),
        }

    def _f(*keys: str) -> Optional[float]:
        for k in keys:
            if k not in quote or quote[k] is None or quote[k] == "":
                continue
            try:
                v = float(quote[k])
            except (TypeError, ValueError):
                continue
            if v > 0:
                return v
        return None

    avg_vol = _f("average_volume", "averageVolume", "avg_volume", "avgVolume")
    price = _f("last", "lastPrice", "last_price", "close", "previous_close", "previousClose")
    if avg_vol is None or price is None:
        return None
    return float(avg_vol) * float(price)


class ADVDataManager:
    """ADV$ cache for 0DTE Liquidity / ADV Guard only (broker-token-first)."""

    def __init__(self) -> None:
        self.adv_cache: Dict[str, float] = {}
        self.last_refresh: Optional[datetime] = None
        self.lookback_days = _lookback_days()
        self.cache_file = Path("data/adv_cache.json")

        log.info(
            "ADV_DATA_MANAGER_INIT | scope=liquidity_guard_0dte | lookback_days=%d | "
            "allow_yfinance=%s | default_floor_usd=%.0f",
            self.lookback_days,
            _allow_yfinance_adv(),
            _default_adv_floor_usd(),
        )
        self._load_cache_from_gcs()
        self._load_cache_from_file()
        log.info(
            "ADV_DATA_MANAGER_READY | symbols_loaded=%d | last_refresh=%s | stale=%s",
            len(self.adv_cache),
            self.last_refresh.isoformat() if self.last_refresh else None,
            self.is_data_stale(),
        )

    def get_adv(self, symbol: str) -> float:
        """ADV in dollars for a symbol (0 if unknown)."""
        sym = (symbol or "").strip().upper()
        if not sym:
            return 0.0
        if sym in self.adv_cache:
            return float(self.adv_cache[sym] or 0.0)
        for key, val in self.adv_cache.items():
            if str(key).strip().upper() == sym:
                return float(val or 0.0)
        return 0.0

    def has_adv(self, symbol: str) -> bool:
        return self.get_adv(symbol) > 0.0

    def missing_symbols(self, symbols: List[str]) -> List[str]:
        return [
            str(s).strip().upper()
            for s in (symbols or [])
            if str(s).strip() and not self.has_adv(str(s))
        ]

    def ensure_ready(
        self,
        symbols: List[str],
        *,
        force: bool = False,
        broker_quotes: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Fill ADV$ for requested symbols.

        Always attempts to fill *missing* names even when the cache is otherwise
        fresh (fixes cold starts that only had a handful of floor symbols).
        """
        symbols = [str(s).strip().upper() for s in (symbols or []) if str(s).strip()]
        missing = self.missing_symbols(symbols)
        need_full = force or not self.adv_cache or self.is_data_stale(max_age_hours=24)

        if need_full or missing:
            reason = (
                "force"
                if force
                else (
                    "empty"
                    if not self.adv_cache
                    else ("stale" if self.is_data_stale() else f"missing_{len(missing)}")
                )
            )
            targets = symbols if need_full else missing
            log.info(
                "ADV_ENSURE_READY | action=refresh | reason=%s | targets=%d | "
                "broker_quotes=%d | allow_yfinance=%s",
                reason,
                len(targets),
                len(broker_quotes or {}),
                _allow_yfinance_adv(),
            )
            if targets:
                self.refresh_adv_data(targets, broker_quotes=broker_quotes)
            else:
                log.warning("ADV_ENSURE_READY | action=abort_refresh | reason=no_symbols")
        else:
            age_h = (
                (datetime.utcnow() - self.last_refresh).total_seconds() / 3600.0
                if self.last_refresh
                else None
            )
            log.info(
                "ADV_ENSURE_READY | action=skip_fresh | symbols_loaded=%d | age_hours=%s",
                len(self.adv_cache),
                f"{age_h:.1f}" if age_h is not None else "n/a",
            )

        floors = self.apply_liquid_adv_floors(symbols or list(_LIQUID_ADV_FLOOR_USD))
        default_floors = self.apply_default_adv_floors(symbols)
        if floors or default_floors:
            log.info(
                "ADV_ENSURE_READY | named_floors=%d | default_floors=%d",
                floors,
                default_floors,
            )
        still_missing = self.missing_symbols(symbols)
        if still_missing:
            log.warning(
                "ADV_ENSURE_READY | still_missing=%d | sample=%s | "
                "hint=broker_averageVolume_or_raise_default_floor",
                len(still_missing),
                ",".join(still_missing[:15]),
            )
        return self.get_stats()

    def refresh_adv_from_broker_quotes_force(self, quotes: Dict[str, Any]) -> int:
        """Always write ADV$ from broker quotes when averageVolume×last is valid."""
        if not quotes:
            return 0
        filled = 0
        for key, quote in quotes.items():
            sym = str(key).strip().upper()
            if not sym:
                continue
            dollars = _quote_adv_dollars(quote)
            if dollars is None or dollars <= 0:
                continue
            self.adv_cache[sym] = float(dollars)
            filled += 1
        if filled:
            self.last_refresh = datetime.utcnow()
            log.info(
                "ADV_BROKER_QUOTE_FORCE | filled=%d | cache_symbols=%d",
                filled,
                len(self.adv_cache),
            )
        return filled

    def apply_liquid_adv_floors(self, symbols: Optional[List[str]] = None) -> int:
        """Conservative ADV$ floors for known liquid names when broker/GCS empty."""
        targets = [
            str(s).strip().upper()
            for s in (symbols or list(_LIQUID_ADV_FLOOR_USD))
            if str(s).strip()
        ]
        applied = 0
        for sym in targets:
            floor = _LIQUID_ADV_FLOOR_USD.get(sym)
            if floor is None or self.get_adv(sym) > 0:
                continue
            self.adv_cache[sym] = float(floor)
            applied += 1
            log.warning(
                "ADV_FLOOR_APPLIED | symbol=%s | adv_dollars=%.0f | "
                "hint=broker_or_gcs_missing_use_conservative_floor",
                sym,
                floor,
            )
        if applied and self.last_refresh is None:
            self.last_refresh = datetime.utcnow()
        if applied:
            self._save_cache_to_file()
            self._save_cache_to_gcs()
        return applied

    def apply_default_adv_floors(self, symbols: List[str]) -> int:
        """Desk-wide conservative ADV$ for any still-missing watchlist symbol."""
        floor = _default_adv_floor_usd()
        if floor <= 0:
            return 0
        applied = 0
        for sym in self.missing_symbols(symbols):
            self.adv_cache[sym] = float(floor)
            applied += 1
            log.warning(
                "ADV_DEFAULT_FLOOR_APPLIED | symbol=%s | adv_dollars=%.0f | "
                "hint=set_LIQUIDITY_GUARD_DEFAULT_ADV_FLOOR_USD_0_to_disable",
                sym,
                floor,
            )
        if applied and self.last_refresh is None:
            self.last_refresh = datetime.utcnow()
        if applied:
            self._save_cache_to_file()
            self._save_cache_to_gcs()
        return applied

    def refresh_adv_data(
        self,
        symbols: List[str],
        broker_quotes: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, float]:
        """Refresh ADV$ for symbols: broker quotes first, optional yfinance last."""
        symbols = [str(s).strip().upper() for s in (symbols or []) if str(s).strip()]
        log.info(
            "ADV_REFRESH_START | symbols=%d | lookback_days=%d | broker_quotes=%d | "
            "allow_yfinance=%s",
            len(symbols),
            self.lookback_days,
            len(broker_quotes or {}),
            _allow_yfinance_adv(),
        )

        if broker_quotes:
            keyed = {str(k).strip().upper(): v for k, v in broker_quotes.items() if str(k).strip()}
            subset = {s: keyed[s] for s in symbols if s in keyed}
            self.refresh_adv_from_broker_quotes_force(subset)

        still = self.missing_symbols(symbols)
        if still and _allow_yfinance_adv():
            self._refresh_adv_via_yfinance(still)
        elif still:
            log.info(
                "ADV_REFRESH_YFINANCE_SKIPPED | missing=%d | "
                "LIQUIDITY_GUARD_ALLOW_YFINANCE_ADV=false",
                len(still),
            )

        self.last_refresh = datetime.utcnow()
        success = len(symbols) - len(self.missing_symbols(symbols))
        log.info(
            "ADV_REFRESH_COMPLETE | success=%d/%d | still_missing=%d",
            success,
            len(symbols),
            len(self.missing_symbols(symbols)),
        )
        self._save_cache_to_file()
        self._save_cache_to_gcs()
        return dict(self.adv_cache)

    def _refresh_adv_via_yfinance(self, symbols: List[str]) -> int:
        """Optional research/backfill path — not the trading default."""
        success_count = 0
        failed_symbols: List[str] = []
        log.warning(
            "ADV_REFRESH_YFINANCE_START | symbols=%d | "
            "note=third_party_fallback_explicitly_enabled",
            len(symbols),
        )
        for symbol in symbols:
            try:
                import yfinance as yf

                ticker = yf.Ticker(symbol)
                hist = ticker.history(period="1y", auto_adjust=True)
                if hist is None or hist.empty:
                    hist = ticker.history(period="6mo", auto_adjust=True)

                if hist is None or hist.empty or len(hist) < 30:
                    log.warning(
                        "ADV_REFRESH_SYMBOL_SKIP | source=yfinance | symbol=%s | "
                        "reason=insufficient_data | days=%d",
                        symbol,
                        len(hist) if hist is not None else 0,
                    )
                    failed_symbols.append(symbol)
                    continue

                recent_hist = hist.tail(self.lookback_days)
                avg_volume = float(recent_hist["Volume"].mean())
                current_price = float(hist["Close"].iloc[-1])
                adv_dollars = avg_volume * current_price
                self.adv_cache[symbol] = float(adv_dollars)
                success_count += 1
            except Exception as exc:
                log.error(
                    "ADV_REFRESH_SYMBOL_FAIL | source=yfinance | symbol=%s | error=%s",
                    symbol,
                    exc,
                )
                failed_symbols.append(symbol)

        log.info(
            "ADV_REFRESH_YFINANCE_COMPLETE | success=%d/%d | failed=%d",
            success_count,
            len(symbols),
            len(failed_symbols),
        )
        return success_count

    def is_data_stale(self, max_age_hours: int = 24) -> bool:
        if not self.last_refresh:
            return True
        age = (datetime.utcnow() - self.last_refresh).total_seconds() / 3600.0
        return age > max_age_hours

    def get_stats(self) -> Dict[str, Any]:
        age_hours = None
        if self.last_refresh:
            age_hours = (datetime.utcnow() - self.last_refresh).total_seconds() / 3600.0
        return {
            "scope": "liquidity_guard_0dte",
            "symbols_loaded": len(self.adv_cache),
            "last_refresh": self.last_refresh.isoformat() if self.last_refresh else None,
            "data_age_hours": age_hours,
            "is_stale": self.is_data_stale(),
            "lookback_days": self.lookback_days,
            "allow_yfinance": _allow_yfinance_adv(),
            "default_floor_usd": _default_adv_floor_usd(),
            "gcs_path": _gcs_adv_path(),
        }

    def _cache_payload(self) -> Dict[str, Any]:
        return {
            "adv_data": self.adv_cache,
            "last_refresh": self.last_refresh.isoformat() if self.last_refresh else None,
            "lookback_days": self.lookback_days,
            "scope": "liquidity_guard_0dte",
            "source_policy": "broker_cache_floor_optional_yfinance",
        }

    def _apply_cache_payload(self, cache_data: Dict[str, Any], *, source: str) -> None:
        incoming = cache_data.get("adv_data") or {}
        if not isinstance(incoming, dict):
            return
        for key, val in incoming.items():
            sym = str(key).strip().upper()
            try:
                dollars = float(val or 0.0)
            except (TypeError, ValueError):
                continue
            if dollars <= 0:
                continue
            if sym not in self.adv_cache or float(self.adv_cache.get(sym) or 0.0) <= 0:
                self.adv_cache[sym] = dollars

        refresh_str = cache_data.get("last_refresh")
        if refresh_str:
            try:
                parsed = datetime.fromisoformat(str(refresh_str).replace("Z", "+00:00"))
                if parsed.tzinfo is not None:
                    parsed = parsed.replace(tzinfo=None)
                if self.last_refresh is None or parsed > self.last_refresh:
                    self.last_refresh = parsed
            except Exception:
                pass

        log.info(
            "ADV_CACHE_LOADED | source=%s | symbols=%d | last_refresh=%s",
            source,
            len(self.adv_cache),
            self.last_refresh.isoformat() if self.last_refresh else None,
        )

    def _save_cache_to_file(self) -> None:
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.cache_file, "w") as f:
                json.dump(self._cache_payload(), f, indent=2)
            log.debug("ADV_CACHE_SAVED | source=local | path=%s", self.cache_file)
        except Exception as exc:
            log.error("ADV_CACHE_SAVE_FAIL | source=local | error=%s", exc)

    def _load_cache_from_file(self) -> None:
        try:
            if not self.cache_file.exists():
                log.debug("ADV_CACHE_MISS | source=local | path=%s", self.cache_file)
                return
            with open(self.cache_file, "r") as f:
                cache_data = json.load(f)
            if isinstance(cache_data, dict):
                self._apply_cache_payload(cache_data, source="local")
        except Exception as exc:
            log.warning("ADV_CACHE_LOAD_FAIL | source=local | error=%s", exc)

    def _save_cache_to_gcs(self) -> None:
        try:
            from modules.gcs_persistence import get_gcs_persistence

            gcs = get_gcs_persistence()
            if not gcs or not getattr(gcs, "enabled", False):
                log.debug("ADV_CACHE_SAVE_SKIP | source=gcs | reason=disabled")
                return
            path = _gcs_adv_path()
            ok = gcs.upload_string(path, json.dumps(self._cache_payload(), indent=2))
            if ok:
                log.info(
                    "ADV_CACHE_SAVED | source=gcs | path=%s | symbols=%d",
                    path,
                    len(self.adv_cache),
                )
            else:
                log.warning("ADV_CACHE_SAVE_FAIL | source=gcs | path=%s", path)
        except Exception as exc:
            log.warning("ADV_CACHE_SAVE_FAIL | source=gcs | error=%s", exc)

    def _load_cache_from_gcs(self) -> None:
        try:
            from modules.gcs_persistence import get_gcs_persistence

            gcs = get_gcs_persistence()
            if not gcs or not getattr(gcs, "enabled", False):
                log.debug("ADV_CACHE_MISS | source=gcs | reason=disabled")
                return
            path = _gcs_adv_path()
            raw = gcs.read_string(path)
            if not raw:
                log.debug("ADV_CACHE_MISS | source=gcs | path=%s", path)
                return
            cache_data = json.loads(raw)
            if isinstance(cache_data, dict):
                self._apply_cache_payload(cache_data, source="gcs")
        except Exception as exc:
            log.warning("ADV_CACHE_LOAD_FAIL | source=gcs | error=%s", exc)


_adv_manager_instance: Optional[ADVDataManager] = None


def get_adv_manager() -> ADVDataManager:
    global _adv_manager_instance
    if _adv_manager_instance is None:
        _adv_manager_instance = ADVDataManager()
    return _adv_manager_instance


def reset_adv_manager_for_tests() -> None:
    global _adv_manager_instance
    _adv_manager_instance = None
