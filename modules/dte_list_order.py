"""0dte_list.csv tier + row-order helpers for discovery rotation.

CSV ``tier`` (1 = highest liquidity / Core Daily) and row order prefer top
names in WAIT_BREAK rotation without replacing state/score ranking.
"""

from __future__ import annotations

import csv
import logging
import os
from pathlib import Path
from typing import Dict, Optional, Tuple

log = logging.getLogger(__name__)

# symbol -> (tier_int, row_index)
_cached_map: Optional[Dict[str, Tuple[int, int]]] = None
_cached_mtime: float = 0.0


def _candidate_paths() -> list:
    roots = []
    try:
        here = Path(__file__).resolve().parents[1]
        roots.append(here)
    except Exception:
        pass
    roots.append(Path("/app"))
    roots.append(Path("."))
    out = []
    for root in roots:
        out.append(root / "data" / "watchlist" / "0dte_list.csv")
        out.append(root / "easyCollector" / "data" / "watchlist" / "0dte_list.csv")
    env = os.getenv("0DTE_LIST_PATH") or os.getenv("DTE0_LIST_PATH")
    if env:
        out.insert(0, Path(env))
    return out


def load_0dte_symbol_tier_order(*, force_reload: bool = False) -> Dict[str, Tuple[int, int]]:
    """Return ``{SYMBOL: (tier, row_index)}`` from ``0dte_list.csv``.

    Missing symbols default to tier=99, row=10_000 at lookup time.
    """
    global _cached_map, _cached_mtime
    path: Optional[Path] = None
    for cand in _candidate_paths():
        try:
            if cand.is_file():
                path = cand
                break
        except Exception:
            continue
    if path is None:
        if _cached_map is None:
            _cached_map = {}
        return _cached_map

    try:
        mtime = path.stat().st_mtime
    except Exception:
        mtime = 0.0
    if (
        not force_reload
        and _cached_map is not None
        and mtime
        and abs(mtime - _cached_mtime) < 1e-6
    ):
        return _cached_map

    out: Dict[str, Tuple[int, int]] = {}
    try:
        with path.open(newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            for idx, row in enumerate(reader):
                sym = str(row.get("symbol") or "").strip().upper()
                if not sym:
                    continue
                try:
                    tier = int(float(row.get("tier") or 99))
                except (TypeError, ValueError):
                    tier = 99
                out[sym] = (max(1, tier), idx)
    except Exception as exc:
        log.warning("0DTE_LIST_ORDER | load_failed | path=%s | error=%s", path, exc)
        if _cached_map is not None:
            return _cached_map
        return {}

    _cached_map = out
    _cached_mtime = mtime
    log.info(
        "0DTE_LIST_ORDER | loaded | path=%s | symbols=%d | tier1=%d",
        path,
        len(out),
        sum(1 for t, _ in out.values() if t == 1),
    )
    return out


def symbol_tier_row(symbol: str) -> Tuple[int, int]:
    """``(tier, row_index)`` — lower tier / earlier row = higher preference."""
    sym = str(symbol or "").strip().upper()
    mapping = load_0dte_symbol_tier_order()
    if sym in mapping:
        return mapping[sym]
    return (99, 10_000)


def rotation_tier_key(symbol: str) -> Tuple[int, int, str]:
    """Sort key for WAIT_BREAK fair-rotation pools (tier → row → symbol)."""
    sym = str(symbol or "").strip().upper()
    tier, row = symbol_tier_row(sym)
    return (int(tier), int(row), sym)
