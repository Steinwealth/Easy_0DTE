"""
RESEARCH app watchlist — single source: data/watchlist/0dte_list.csv

ORB SO, ORB1 Breakout, and Trendline 0DTE share this list for ORB capture and
signal collection (no core_list merge or dedup).

Cash-settled indexes (SPX/RUT/…) are omitted from the CSV itself — do not
re-filter here.
"""
from __future__ import annotations

import logging
import os
from typing import List

log = logging.getLogger(__name__)

DEFAULT_WATCHLIST_PATH = os.getenv(
    "0DTE_WATCHLIST_PATH",
    "data/watchlist/0dte_list.csv",
)


def _watchlist_search_paths() -> List[str]:
    paths: List[str] = []
    env_path = os.getenv("0DTE_WATCHLIST_PATH", "").strip()
    if env_path:
        paths.append(env_path)
    extra = os.getenv("0DTE_WATCHLIST_CSV_EXTRA_PATHS", "").strip()
    if extra:
        for part in extra.split(","):
            part = part.strip()
            if part:
                paths.append(part)
    paths.extend(
        [
            "/app/data/watchlist/0dte_list.csv",
            DEFAULT_WATCHLIST_PATH,
            "data/watchlist/0dte_list.csv",
        ]
    )
    seen = set()
    ordered: List[str] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            ordered.append(path)
    return ordered


def _read_symbols_from_csv(path: str) -> List[str]:
    import pandas as pd

    df = pd.read_csv(path, comment="#")
    if "symbol" not in df.columns:
        df = pd.read_csv(
            path,
            comment="#",
            header=None,
            names=["symbol", "tier", "category", "notes"],
        )
    return [str(s).strip().upper() for s in df["symbol"].tolist() if str(s).strip()]


def load_watchlist_symbols() -> List[str]:
    """Load symbols in CSV row order from 0dte_list.csv (ORB SO + 0DTE + Trendline)."""
    for path in _watchlist_search_paths():
        if not os.path.exists(path):
            continue
        try:
            symbols = _read_symbols_from_csv(path)
            if symbols:
                log.info("Loaded %s symbols from %s", len(symbols), path)
                return symbols
            log.warning("0dte_list.csv at %s parsed but returned no symbols", path)
        except Exception as csv_err:
            log.warning("Failed to read watchlist CSV at %s: %s", path, csv_err)

    try:
        from easy0DTE.modules.prime_0dte_strategy_manager import load_0dte_symbols

        symbols = load_0dte_symbols()
        if symbols:
            return symbols
    except Exception as import_err:
        log.error(
            "Watchlist loader fallback via easy0DTE failed: %s",
            import_err,
            exc_info=True,
        )

    raw = os.getenv("0DTE_SYMBOLS_FALLBACK", "SPY,QQQ,IWM")
    parts = [p.strip().upper() for p in str(raw).split(",") if p.strip()]
    if parts:
        log.error(
            "CRITICAL: 0dte_list.csv unavailable — using 0DTE_SYMBOLS_FALLBACK (%s symbols)",
            len(parts),
        )
        return parts
    return ["SPY", "QQQ", "IWM"]
