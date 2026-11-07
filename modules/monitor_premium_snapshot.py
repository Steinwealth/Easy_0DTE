"""Shared monitor premium snapshot — one normalized truth for live exits + PO.

E*TRADE exact-contract request
    → normalized premium snapshot
    → trusted_exact | trusted_chain | degraded_chain | gap
    → live exit engine + Profit Optimizer

Synthetic marks are never produced here. Gaps never silently become zero or
carry-forward of a prior mark.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

TRUST_EXACT = "trusted_exact"
TRUST_CHAIN = "trusted_chain"
TRUST_DEGRADED_CHAIN = "degraded_chain"
TRUST_GAP = "gap"

_CHAIN_SOURCES = (
    "fresh_chain_refresh",
    "fresh_chain_force_refresh",  # legacy
    "fresh_chain_bridge",
    "chain",
    "chain_bridge",
)


def _s(v: Any) -> str:
    return str(v or "").strip()


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        out = float(v)
        return out if out == out else None
    except (TypeError, ValueError):
        return None


def extract_cumulative_volume(source: Optional[Dict[str, Any]]) -> Optional[float]:
    """Map quote volume aliases → cumulative_volume. Absent stays None (never fake 0)."""
    if not isinstance(source, dict):
        return None
    for key in (
        "cumulative_volume",
        "volume",
        "totalVolume",
        "total_volume",
        "TotalVolume",
    ):
        parsed = _f(source.get(key))
        if parsed is None:
            continue
        if parsed < 0:
            continue
        return parsed
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def classify_shadow_trust(
    *,
    premium_source: str = "",
    exact_hit: bool = False,
    quote_valid: bool = False,
    freshness_status: str = "",
    spread_pct: Optional[float] = None,
    contract_match_status: str = "",
) -> str:
    """Map broker outcome → research trust class."""
    if not quote_valid:
        return TRUST_GAP
    src = _s(premium_source).lower()
    fresh = _s(freshness_status).lower()
    match = _s(contract_match_status).lower()
    if exact_hit or "exact_contract" in src:
        if "stale" in fresh or "unavailable" in fresh or "absent" in fresh:
            # Exact identity but weak clock — still exact identity for PO trust.
            return TRUST_EXACT
        return TRUST_EXACT
    if any(token in src for token in _CHAIN_SOURCES) or match in (
        "chain_strike_expiry_match",
        "chain_occ_match",
        "matched",
    ):
        weak = (
            "stale" in fresh
            or "partial" in fresh
            or "weak" in fresh
            or (spread_pct is not None and float(spread_pct) > 0.50)
        )
        return TRUST_DEGRADED_CHAIN if weak else TRUST_CHAIN
    return TRUST_GAP


def is_recordable_research_quote(fields: Dict[str, Any]) -> bool:
    """PO may record trusted_exact / trusted_chain / degraded_chain (not gap/synthetic)."""
    trust = _s(fields.get("shadow_trust") or fields.get("trust_class"))
    if trust == TRUST_GAP or not trust:
        # Legacy exact gate compatibility.
        if bool(fields.get("shadow_quote_exact")):
            trust = TRUST_EXACT
        else:
            src = _s(fields.get("premium_source") or fields.get("quote_source")).lower()
            if "exact_contract" in src:
                trust = TRUST_EXACT
            elif any(t in src for t in _CHAIN_SOURCES):
                trust = TRUST_CHAIN
            else:
                return False
    if trust not in (TRUST_EXACT, TRUST_CHAIN, TRUST_DEGRADED_CHAIN):
        return False
    if str(fields.get("premium_source") or "").lower().startswith("synthetic"):
        return False
    mark = _f(fields.get("mark") or fields.get("executable_bid_mark") or fields.get("bid"))
    if mark is None or mark <= 0:
        return False
    return True


def build_premium_snapshot(
    *,
    trade_id: str = "",
    exact_quote: Optional[Dict[str, Any]] = None,
    chain_quote: Optional[Dict[str, Any]] = None,
    underlying_price: Optional[float] = None,
    requested_occ: str = "",
    requested_aliases: Optional[List[str]] = None,
    exact_failure_reason: str = "",
    chain_failure_reason: str = "",
    broker_response_status: str = "",
    tick_id: str = "",
    reused_last_exact: Optional[bool] = None,
) -> Dict[str, Any]:
    """Build one normalized premium observation for live + PO consumers."""
    aliases = [a for a in (requested_aliases or []) if _s(a)]
    reused = (
        bool(reused_last_exact)
        if reused_last_exact is not None
        else _s(broker_response_status).lower() in ("reused_last_exact", "prefer_reused_exact")
    )
    base: Dict[str, Any] = {
        "trade_id": _s(trade_id),
        "timestamp": _now_iso(),
        "observation_timestamp": None,
        "broker_quote_timestamp": None,
        "underlying_price": _f(underlying_price),
        "requested_occ": _s(requested_occ),
        "contract_symbol": _s(requested_occ),
        "requested_aliases": aliases,
        "exact_failure_reason": _s(exact_failure_reason)[:160],
        "chain_failure_reason": _s(chain_failure_reason)[:160],
        "broker_response_status": _s(broker_response_status)[:120],
        "monitor_tick_id": _s(tick_id),
        "quote_valid": False,
        "shadow_trust": TRUST_GAP,
        "premium_source": "",
        "freshness_status": "",
        "contract_match_status": "",
        "bid": None,
        "ask": None,
        "mid": None,
        "mark": None,
        "last": None,
        "bid_size": None,
        "ask_size": None,
        "cumulative_volume": None,
        "spread_width": None,
        "spread_pct": None,
        "quote_age_seconds": None,
        "reused_last_exact": False,
        "shadow_quote_exact": False,
        "shadow_option_symbol": _s(requested_occ),
        "mark_quality": "",
        "mark_is_exit_grade": False,
        "delta": None,
        "gamma": None,
        "theta": None,
        "vega": None,
        "implied_volatility": None,
        "iv": None,
    }

    if isinstance(exact_quote, dict) and exact_quote:
        bid = _f(exact_quote.get("bid"))
        ask = _f(exact_quote.get("ask"))
        mid = _f(exact_quote.get("mid") or exact_quote.get("mark"))
        mark = _f(exact_quote.get("mark") or exact_quote.get("mid") or bid)
        if mark is not None and mark > 0:
            fresh = _s(exact_quote.get("freshness_status") or "broker_timestamp_ok")
            age_ms = _f(exact_quote.get("quote_age_ms") or exact_quote.get("broker_quote_age_ms"))
            spread_w = None
            spread_pct = None
            if bid is not None and ask is not None:
                spread_w = abs(float(ask) - float(bid))
                if mid and mid > 0:
                    spread_pct = spread_w / float(mid)
            age_sec = (age_ms / 1000.0) if age_ms is not None else _f(exact_quote.get("quote_age_seconds"))
            identity_ok = bool(
                exact_quote.get("contract_identity_matched", True)
                if "contract_identity_matched" in exact_quote
                else exact_quote.get("contract_matched", True)
            )
            exit_grade = bool(
                identity_ok
                and age_sec is not None
                and float(age_sec) > 0
                and "stale" not in fresh.lower()
            )
            trust = classify_shadow_trust(
                premium_source="exact_quote",
                exact_hit=True,
                quote_valid=True,
                freshness_status=fresh,
                spread_pct=spread_pct,
                contract_match_status="exact_occ",
            )
            # timestamp = monitor/write clock; broker_quote_timestamp stays separate.
            monitor_ts = base["timestamp"]
            broker_ts = (
                _s(
                    exact_quote.get("broker_quote_timestamp")
                    or exact_quote.get("quote_timestamp")
                )
                or None
            )
            cum_vol = extract_cumulative_volume(exact_quote)
            base.update(
                {
                    "timestamp": monitor_ts,
                    "observation_timestamp": monitor_ts,
                    "broker_quote_timestamp": broker_ts,
                    "bid": bid,
                    "ask": ask,
                    "mid": mid,
                    "mark": mark,
                    "last": _f(exact_quote.get("last") or exact_quote.get("last_trade_price")),
                    "bid_size": _f(exact_quote.get("bid_size")),
                    "ask_size": _f(exact_quote.get("ask_size")),
                    "cumulative_volume": cum_vol,
                    "spread_width": spread_w,
                    "spread_pct": spread_pct,
                    "quote_age_seconds": age_sec,
                    "quote_age_ms": age_ms,
                    "quote_age_basis": _s(exact_quote.get("quote_age_basis")),
                    "quote_valid": True,
                    "shadow_trust": trust,
                    "premium_source": "exact_quote",
                    "quote_source": "exact_quote",
                    "freshness_status": fresh or "exact_contract",
                    "contract_match_status": "exact_occ",
                    "contract_identity_matched": identity_ok,
                    "reused_last_exact": reused,
                    "shadow_quote_exact": True,
                    "shadow_option_symbol": _s(
                        exact_quote.get("option_symbol") or requested_occ
                    ),
                    "contract_symbol": _s(
                        exact_quote.get("option_symbol") or requested_occ
                    ),
                    "mark_quality": "live_single_leg" if exit_grade else "cached_recent",
                    "mark_is_exit_grade": bool(exit_grade),
                    "osi_symbol": _s(exact_quote.get("osi_symbol") or exact_quote.get("osiKey")),
                    "delta": _f(exact_quote.get("delta")),
                    "gamma": _f(exact_quote.get("gamma")),
                    "theta": _f(exact_quote.get("theta")),
                    "vega": _f(exact_quote.get("vega")),
                    "implied_volatility": _f(
                        exact_quote.get("implied_volatility") or exact_quote.get("iv")
                    ),
                    "iv": _f(exact_quote.get("iv") or exact_quote.get("implied_volatility")),
                }
            )
            return base

    if isinstance(chain_quote, dict) and chain_quote:
        bid = _f(chain_quote.get("bid"))
        ask = _f(chain_quote.get("ask"))
        mid = _f(chain_quote.get("mid"))
        mark = _f(chain_quote.get("mark") or chain_quote.get("bid") or mid)
        if mark is not None and mark > 0:
            fresh = _s(chain_quote.get("freshness_status") or "chain_fresh")
            src = _s(chain_quote.get("premium_source") or "fresh_chain_refresh")
            match = _s(chain_quote.get("contract_match_status") or "chain_strike_expiry_match")
            spread_w = None
            spread_pct = _f(chain_quote.get("spread_pct"))
            if bid is not None and ask is not None:
                spread_w = abs(float(ask) - float(bid))
                if (mid or mark) and float(mid or mark) > 0:
                    spread_pct = spread_w / float(mid or mark)
            trust = classify_shadow_trust(
                premium_source=src,
                exact_hit=False,
                quote_valid=True,
                freshness_status=fresh,
                spread_pct=spread_pct,
                contract_match_status=match,
            )
            monitor_ts = base["timestamp"]
            broker_ts = (
                _s(
                    chain_quote.get("broker_quote_timestamp")
                    or chain_quote.get("quote_timestamp")
                )
                or None
            )
            cum_vol = extract_cumulative_volume(chain_quote)
            base.update(
                {
                    "timestamp": monitor_ts,
                    "observation_timestamp": monitor_ts,
                    "broker_quote_timestamp": broker_ts,
                    "bid": bid,
                    "ask": ask,
                    "mid": mid if mid is not None else mark,
                    "mark": mark,
                    "last": _f(chain_quote.get("last") or chain_quote.get("last_trade_price")),
                    "bid_size": _f(chain_quote.get("bid_size")),
                    "ask_size": _f(chain_quote.get("ask_size")),
                    "cumulative_volume": cum_vol,
                    "spread_width": spread_w,
                    "spread_pct": spread_pct,
                    "quote_age_seconds": _f(chain_quote.get("quote_age_seconds")),
                    "quote_age_ms": _f(chain_quote.get("quote_age_ms")),
                    "quote_valid": True,
                    "shadow_trust": trust,
                    "premium_source": src,
                    "quote_source": src,
                    "freshness_status": fresh,
                    "contract_match_status": match,
                    "reused_last_exact": False,
                    "shadow_quote_exact": False,
                    "shadow_option_symbol": _s(
                        chain_quote.get("option_symbol") or requested_occ
                    ),
                    "contract_symbol": _s(
                        chain_quote.get("option_symbol") or requested_occ
                    ),
                    "mark_quality": "chain_live",
                    "mark_is_exit_grade": False,
                    "delta": _f(chain_quote.get("delta")),
                    "gamma": _f(chain_quote.get("gamma")),
                    "theta": _f(chain_quote.get("theta")),
                    "vega": _f(chain_quote.get("vega")),
                    "implied_volatility": _f(
                        chain_quote.get("implied_volatility") or chain_quote.get("iv")
                    ),
                    "iv": _f(chain_quote.get("iv") or chain_quote.get("implied_volatility")),
                }
            )
            return base

    # Gap — no mark fields (never zero / never carry-forward).
    base["shadow_trust"] = TRUST_GAP
    base["quote_valid"] = False
    base["premium_source"] = ""
    base["contract_match_status"] = "unmatched"
    base["observation_timestamp"] = base["timestamp"]
    if exact_failure_reason:
        base["freshness_status"] = "exact_miss"
    elif chain_failure_reason:
        base["freshness_status"] = "chain_miss"
    else:
        base["freshness_status"] = "no_broker_mark"
    return base


def snapshot_to_option_quote(snapshot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Convert a valid snapshot into the option_quote dict PO observe expects."""
    if not isinstance(snapshot, dict) or not snapshot.get("quote_valid"):
        return None
    mark = _f(snapshot.get("mark"))
    if mark is None or mark <= 0:
        return None
    cum_vol = extract_cumulative_volume(snapshot)
    return {
        "bid": snapshot.get("bid"),
        "ask": snapshot.get("ask"),
        "mid": snapshot.get("mid"),
        "mid_price": snapshot.get("mid"),
        "mark": mark,
        "last": snapshot.get("last"),
        "bid_size": snapshot.get("bid_size"),
        "ask_size": snapshot.get("ask_size"),
        "cumulative_volume": cum_vol,
        "volume": cum_vol,
        "spread_width": snapshot.get("spread_width"),
        "spread_pct": snapshot.get("spread_pct"),
        "quote_age_seconds": snapshot.get("quote_age_seconds"),
        "quote_source": snapshot.get("premium_source"),
        "premium_source": snapshot.get("premium_source"),
        "shadow_quote_exact": bool(snapshot.get("shadow_quote_exact")),
        "shadow_option_symbol": snapshot.get("shadow_option_symbol"),
        "contract_symbol": snapshot.get("contract_symbol") or snapshot.get("shadow_option_symbol"),
        "shadow_trust": snapshot.get("shadow_trust"),
        "quote_valid": True,
        "freshness_status": snapshot.get("freshness_status"),
        "contract_match_status": snapshot.get("contract_match_status"),
        "timestamp": snapshot.get("timestamp") or snapshot.get("observation_timestamp"),
        "broker_quote_timestamp": snapshot.get("broker_quote_timestamp"),
        "observation_timestamp": snapshot.get("observation_timestamp") or snapshot.get("timestamp"),
        "monitor_tick_id": snapshot.get("monitor_tick_id"),
        "reused_last_exact": bool(snapshot.get("reused_last_exact")),
        "trade_id": snapshot.get("trade_id"),
        "delta": snapshot.get("delta"),
        "gamma": snapshot.get("gamma"),
        "theta": snapshot.get("theta"),
        "vega": snapshot.get("vega"),
        "implied_volatility": snapshot.get("implied_volatility") or snapshot.get("iv"),
        "iv": snapshot.get("iv") or snapshot.get("implied_volatility"),
    }


def open_or_extend_gap_interval(
    path_audit: Dict[str, Any],
    *,
    trade_id: str,
    reason: str = "",
    requested_occ: str = "",
    requested_aliases: Optional[List[str]] = None,
    last_known_underlying: Optional[float] = None,
    exact_failure_reason: str = "",
    chain_failure_reason: str = "",
    broker_response_status: str = "",
    last_valid_quote_timestamp: str = "",
    timestamp: Optional[str] = None,
) -> Dict[str, Any]:
    """Compress consecutive identical misses into one gap interval."""
    pa = dict(path_audit or {})
    intervals = pa.get("shadow_gap_intervals")
    if not isinstance(intervals, list):
        intervals = []
    ts = _s(timestamp) or _now_iso()
    aliases = [a for a in (requested_aliases or []) if _s(a)]
    exact_r = _s(exact_failure_reason or reason)[:160]
    chain_r = _s(chain_failure_reason)[:160]
    status = _s(broker_response_status or reason)[:120]

    active = intervals[-1] if intervals else None
    same = (
        isinstance(active, dict)
        and active.get("closed") is not True
        and _s(active.get("exact_failure_reason")) == exact_r
        and _s(active.get("chain_failure_reason")) == chain_r
        and _s(active.get("requested_occ")) == _s(requested_occ)
    )
    if same and isinstance(active, dict):
        active["timestamp_end"] = ts
        active["attempt_count"] = int(active.get("attempt_count") or 0) + 1
        if last_known_underlying is not None:
            active["last_known_underlying"] = float(last_known_underlying)
        if last_valid_quote_timestamp:
            active["last_valid_quote_timestamp"] = last_valid_quote_timestamp
            try:
                from profit_optimizer.audit import parse_timestamp_utc

                last = parse_timestamp_utc(last_valid_quote_timestamp)
                now = parse_timestamp_utc(ts)
                if last and now:
                    active["seconds_since_last_valid_quote"] = max(
                        0.0, (now - last).total_seconds()
                    )
            except Exception:
                pass
    else:
        if isinstance(active, dict) and active.get("closed") is not True:
            active["closed"] = True
            active["timestamp_end"] = active.get("timestamp_end") or ts
        sec_since = None
        if last_valid_quote_timestamp:
            try:
                from profit_optimizer.audit import parse_timestamp_utc

                last = parse_timestamp_utc(last_valid_quote_timestamp)
                now = parse_timestamp_utc(ts)
                if last and now:
                    sec_since = max(0.0, (now - last).total_seconds())
            except Exception:
                sec_since = None
        intervals.append(
            {
                "trade_id": _s(trade_id),
                "timestamp_start": ts,
                "timestamp_end": ts,
                "attempt_count": 1,
                "requested_occ": _s(requested_occ),
                "requested_aliases": aliases[:12],
                "last_known_underlying": _f(last_known_underlying),
                "exact_failure_reason": exact_r,
                "chain_failure_reason": chain_r,
                "broker_response_status": status,
                "last_valid_quote_timestamp": _s(last_valid_quote_timestamp),
                "seconds_since_last_valid_quote": sec_since,
                "closed": False,
            }
        )
    pa["shadow_gap_intervals"] = intervals[-50:]
    pa["shadow_gap_count"] = int(pa.get("shadow_gap_count") or 0) + 1
    pa["last_shadow_gap_reason"] = exact_r or chain_r or status
    return pa


def close_active_gap_interval(path_audit: Dict[str, Any], *, timestamp: Optional[str] = None) -> Dict[str, Any]:
    pa = dict(path_audit or {})
    intervals = pa.get("shadow_gap_intervals")
    if not isinstance(intervals, list) or not intervals:
        return pa
    active = intervals[-1]
    if isinstance(active, dict) and active.get("closed") is not True:
        active["closed"] = True
        active["timestamp_end"] = _s(timestamp) or active.get("timestamp_end") or _now_iso()
    return pa
