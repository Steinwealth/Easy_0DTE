"""
Central volume + session-tech request catalog (ORB1 Breakout / Trendline / Breakout).

Volume is not one field. Always request through this module (or the hubs it wraps)
so callers know which kind they are using.

=============================================================================
Kind                         Keys / return                  Request via
-----------------------------------------------------------------------------
equity_session_tech          vwap, rsi, macd*, volume_ratio hydrate_equity_session_tech
                             *_missing, tech_source         → request_equity_session_tech
                                                            → apply_equity_session_tech
equity_session_ratio         volume_ratio (+ missing)       resolve_equity_volume_for_gate
quote_session_cumulative     totalVolume / dayVolume        equity_session_volume_from_quote
quote_write_through          minute bar deltas              ingest_equity_session_quotes
chain_side                   call/put_chain_volume, ratios  chain_side_volumes_from_chain
direction_volume_pct         same-side chain vol share      direction_volume_pct_from_signal
pc_oi                        put_call_oi_ratio              (OI — not volume; Convex helper)
orb_window                   orb_volume                     ORB manager (structure only)
contract_liquidity           contract .volume               option entry liquidity
volume_research_bundle       all VOLUME_RESEARCH_KEYS       ensure_volume_research_fields
=============================================================================

Do NOT feed orb_window / contract / daily-Yahoo volume into equity hard-gate ratio.
Do NOT mix day_seed / intraday_15m / pad_anchor into minute volume_ratio (hub enforces).
direction_volume_pct (chain volume share) is NOT put_call_oi_ratio (OI) — keep both.
ORB/PP must reuse ingest_equity_session_quotes on existing E*TRADE quote polls
(same path as Trendline/Breakout watch) — never invent a parallel bar source.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

# --- Kind constants (log / gate labels) ---
VOLUME_KIND_EQUITY_SESSION_RATIO = "equity_session_ratio"
VOLUME_KIND_QUOTE_CUMULATIVE = "quote_session_cumulative"
VOLUME_KIND_CHAIN_SIDE = "chain_side"
VOLUME_KIND_DIRECTION_PCT = "direction_volume_pct"
VOLUME_KIND_ORB_WINDOW = "orb_window"
VOLUME_KIND_CONTRACT = "contract_liquidity"


class VolumeKind:
    """Namespace for volume kind labels (same strings as module constants)."""

    EQUITY_SESSION_RATIO = VOLUME_KIND_EQUITY_SESSION_RATIO
    QUOTE_CUMULATIVE = VOLUME_KIND_QUOTE_CUMULATIVE
    CHAIN_SIDE = VOLUME_KIND_CHAIN_SIDE
    DIRECTION_PCT = VOLUME_KIND_DIRECTION_PCT
    ORB_WINDOW = VOLUME_KIND_ORB_WINDOW
    CONTRACT = VOLUME_KIND_CONTRACT


def equity_session_volume_from_quote(quote: Any) -> Optional[float]:
    """Cumulative session volume from a broker quote (minute-bar builder input only)."""
    from modules.session_tech_indicators import quote_session_volume

    return quote_session_volume(quote)


def ingest_equity_session_quotes(system: Any, quotes: Optional[Dict[str, Any]]) -> int:
    """
    Write-through broker batch quotes into the shared session-tech minute cache.

    Same path Trendline/Breakout watch already use (E*TRADE ``totalVolume`` /
    ``volume`` deltas → ``upsert_session_tech_minute_bar``). Call on every
    existing equity quote poll so ORB/PP inherit warm bars by 7:30 — do not
    invent a second bar source.
    """
    from modules.session_tech_indicators import ingest_session_tech_quotes

    return int(ingest_session_tech_quotes(system, quotes) or 0)


def request_equity_session_tech(
    system: Any,
    symbol: str,
    *,
    price: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    """Read equity session stamp (VWAP/RSI/MACD/volume_ratio) from the shared hub."""
    from modules.session_tech_indicators import get_session_tech

    return get_session_tech(system, symbol, price=price)


async def hydrate_equity_session_tech(
    system: Any,
    symbols: Sequence[str],
    *,
    quotes: Optional[Dict[str, Any]] = None,
    max_age_sec: Optional[float] = None,
) -> Dict[str, Any]:
    """Hydrate shared RTH bars (broker quotes → minute deltas). Same hub for ORB/TL/BO."""
    from modules.session_tech_indicators import ensure_session_tech_bars

    return await ensure_session_tech_bars(
        system, symbols, quotes=quotes, max_age_sec=max_age_sec
    )


def apply_equity_session_tech(target: Any, tech: Dict[str, Any]) -> None:
    """Stamp hub fields onto a signal dict / candidate (sets orb_volume_ratio alias)."""
    from modules.session_tech_indicators import apply_session_tech

    apply_session_tech(target, tech)


def chain_side_volumes_from_chain(chain: Optional[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    """Call/put chain volume + OI totals and put/call ratios from a broker chain."""
    from easy0DTE.modules.convex_chain_skew import compute_put_call_ratios_from_chain

    return compute_put_call_ratios_from_chain(chain)


def direction_volume_pct_from_signal(signal: Dict[str, Any]) -> Optional[float]:
    """Same-side options chain volume share (dir%) for Convex / TL / BO gates."""
    from easy0DTE.modules.convex_eligibility_filter import same_side_direction_volume_pct

    return same_side_direction_volume_pct(signal)


def resolve_equity_volume_for_gate(
    tech_or_signal: Any,
) -> Dict[str, Any]:
    """
    Resolve equity session volume_ratio for gates.

    Returns:
      ratio, missing, source, kind=equity_session_ratio

    Treats missing / micro stubs (<0.01) as data gaps (missing=True, ratio=1.0 neutral).
    """
    kind = VOLUME_KIND_EQUITY_SESSION_RATIO
    empty = {
        "ratio": 1.0,
        "missing": True,
        "source": None,
        "kind": kind,
    }
    if tech_or_signal is None:
        return empty

    if isinstance(tech_or_signal, dict):
        row = tech_or_signal
        md = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    else:
        row = {}
        for key in (
            "volume_ratio",
            "orb_volume_ratio",
            "volume_ratio_missing",
            "volume_ratio_source",
            "tech_source",
        ):
            if hasattr(tech_or_signal, key):
                row[key] = getattr(tech_or_signal, key)
        md = getattr(tech_or_signal, "metadata", None)
        if not isinstance(md, dict):
            md = {}
        # DTE0Signal.orb_signal path
        orb = getattr(tech_or_signal, "orb_signal", None)
        if isinstance(orb, dict):
            for k, v in orb.items():
                if k not in row or row.get(k) is None:
                    row[k] = v
            orb_md = orb.get("metadata")
            if isinstance(orb_md, dict):
                md = {**orb_md, **md}

    missing = bool(row.get("volume_ratio_missing") or md.get("volume_ratio_missing"))
    raw = row.get("volume_ratio")
    if raw is None:
        raw = row.get("orb_volume_ratio")
    if raw is None:
        raw = md.get("volume_ratio")
    try:
        ratio = float(raw) if raw is not None else 1.0
    except (TypeError, ValueError):
        return empty

    if not math.isfinite(ratio) or ratio <= 0.0 or ratio < 0.01:
        missing = True
        ratio = 1.0

    source = (
        row.get("volume_ratio_source")
        or md.get("volume_ratio_source")
        or row.get("tech_source")
        or md.get("tech_source")
    )
    return {
        "ratio": float(ratio) if not missing else 1.0,
        "missing": bool(missing),
        "source": source,
        "kind": kind,
        "raw_ratio": float(raw) if raw is not None else None,
    }


# Keys the Priority Optimizer densify / path research expect for volume study.
VOLUME_RESEARCH_KEYS: Tuple[str, ...] = (
    "volume_ratio",
    "orb_volume_ratio",
    "volume_ratio_missing",
    "volume_ratio_source",
    "tech_source",
    "session_bar_count",
    "call_chain_volume",
    "put_chain_volume",
    "call_volume",
    "put_volume",
    "put_call_volume_ratio",
    "put_call_oi_ratio",
    "call_chain_open_interest",
    "put_chain_open_interest",
    "total_chain_volume",
    "total_chain_open_interest",
    "direction_volume_pct",
    "representative_volume",
    "cumulative_volume",
)


def ensure_volume_research_fields(
    row: Optional[Dict[str, Any]],
    *,
    option_side: str = "",
) -> Dict[str, Any]:
    """
    Derive / alias volume research fields in-place for PO entry/exit/path rows.

    Does **not** invent equity volume_ratio when missing — stamps
    ``volume_ratio_missing`` instead. Derives chain totals + direction_volume_pct
    when both sides exist. Returns completeness diagnostics.
    """
    status: Dict[str, Any] = {
        "ok": False,
        "missing_keys": [],
        "derived": [],
    }
    if not isinstance(row, dict):
        status["missing_keys"] = list(VOLUME_RESEARCH_KEYS)
        return status

    md = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}

    # --- Equity session ratio ---
    vol_res = resolve_equity_volume_for_gate(row)
    if row.get("volume_ratio") is None and row.get("orb_volume_ratio") is not None:
        row["volume_ratio"] = row.get("orb_volume_ratio")
        status["derived"].append("volume_ratio<=orb_volume_ratio")
    if row.get("orb_volume_ratio") is None and row.get("volume_ratio") is not None:
        row["orb_volume_ratio"] = row.get("volume_ratio")
        status["derived"].append("orb_volume_ratio<=volume_ratio")
    if vol_res.get("missing"):
        row["volume_ratio_missing"] = True
        # Do not invent 1.0 onto PO rows — fill hydrate stamps real tape.
    else:
        if row.get("volume_ratio_missing") is None:
            row["volume_ratio_missing"] = False
    if row.get("volume_ratio_source") is None:
        src = vol_res.get("source") or md.get("volume_ratio_source") or md.get("tech_source")
        if src:
            row["volume_ratio_source"] = src
            status["derived"].append("volume_ratio_source")
    if row.get("tech_source") is None and (
        row.get("volume_ratio_source") is not None or md.get("tech_source") is not None
    ):
        row["tech_source"] = row.get("volume_ratio_source") or md.get("tech_source")

    # --- Chain sides / OI / dir% ---
    try:
        from profit_optimizer.path_chain_skew import (
            derive_direction_volume_pct,
            maybe_replace_complement_dirpct,
            option_side_token,
            stamp_review_microstructure,
        )

        side = option_side_token(
            option_side,
            row.get("option_side"),
            row.get("expected_option_side"),
            row.get("option_type"),
            row.get("side"),
        )
        stamp_review_microstructure(row, option_side=side, overwrite=False)
        derived = derive_direction_volume_pct(
            row.get("call_chain_volume"),
            row.get("put_chain_volume"),
            option_side=side,
        )
        if derived is not None:
            if row.get("direction_volume_pct") is None:
                row["direction_volume_pct"] = float(derived)
                status["derived"].append("direction_volume_pct")
            else:
                replaced = maybe_replace_complement_dirpct(
                    row.get("direction_volume_pct"), derived
                )
                if replaced is not None and replaced != row.get("direction_volume_pct"):
                    row["direction_volume_pct"] = float(replaced)
                    status["derived"].append("direction_volume_pct_same_side")
    except Exception:
        # Fallback: lightweight totals + dir%
        try:
            cv = float(row["call_chain_volume"]) if row.get("call_chain_volume") is not None else None
            pv = float(row["put_chain_volume"]) if row.get("put_chain_volume") is not None else None
        except (TypeError, ValueError):
            cv = pv = None
        if cv is not None and pv is not None:
            if row.get("total_chain_volume") is None:
                row["total_chain_volume"] = cv + pv
                status["derived"].append("total_chain_volume")
            if row.get("direction_volume_pct") is None:
                d = direction_volume_pct_from_signal(row)
                if d is not None:
                    row["direction_volume_pct"] = float(d)
                    status["derived"].append("direction_volume_pct")

    # Desk aliases — always run (outside path_chain_skew try) so densify never
    # leaves call_volume/put_volume null when chain_* vols exist.
    if row.get("call_volume") is None and row.get("call_chain_volume") is not None:
        row["call_volume"] = row.get("call_chain_volume")
        status["derived"].append("call_volume<=call_chain_volume")
    if row.get("put_volume") is None and row.get("put_chain_volume") is not None:
        row["put_volume"] = row.get("put_chain_volume")
        status["derived"].append("put_volume<=put_chain_volume")
    if row.get("call_chain_volume") is None and row.get("call_volume") is not None:
        row["call_chain_volume"] = row.get("call_volume")
        status["derived"].append("call_chain_volume<=call_volume")
    if row.get("put_chain_volume") is None and row.get("put_volume") is not None:
        row["put_chain_volume"] = row.get("put_volume")
        status["derived"].append("put_chain_volume<=put_volume")

    # --- Owned quote depth: do not invent; only alias when one form present ---
    if row.get("cumulative_volume") is None and row.get("representative_volume") is not None:
        # Not the same kind — do not alias representative → cumulative.
        pass

    missing: List[str] = []
    for k in VOLUME_RESEARCH_KEYS:
        if k == "volume_ratio_missing":
            if row.get(k) is None:
                missing.append(k)
            continue
        if row.get(k) is None:
            missing.append(k)
    status["missing_keys"] = missing
    # Core research set (allow representative/cumulative null on pure entry snaps).
    status["ok"] = (
        row.get("volume_ratio_missing") is not None
        and row.get("call_chain_volume") is not None
        and row.get("put_chain_volume") is not None
        and row.get("direction_volume_pct") is not None
        and (row.get("volume_ratio") is not None)
    )
    return status
