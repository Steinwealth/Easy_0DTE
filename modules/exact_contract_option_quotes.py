"""Batched exact-contract REST quotes for open 0DTE positions.

Uses E*TRADE Market Quote API. Requests use the official colon option format
(``underlier:year:month:day:CALL|PUT:strike``); owned contracts are matched by
structured identity (underlying / expiry / strike / call-put), not fragile OCC
string equality on ``Product.symbol``.

Never uses the options-chain cache for exit valuation.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import quote

from modules.option_contract_identity import (
    OptionContractIdentity,
    etrade_quote_symbol_candidates,
    identity_from_any_option_symbol,
    identity_from_etrade_quote_symbol,
    identity_from_occ,
    identity_from_osi_key,
    identity_from_parts,
    identity_from_product,
    identities_equal,
    identity_to_etrade_quote_symbol,
    occ_from_identity,
    occ_to_etrade_quote_symbol,
    sanitize_identity_fields,
)

log = logging.getLogger(__name__)

# Hard-fail quarantine: after all 1002 formatting alternates miss, skip the OCC
# from primary batches + recovery for a TTL so one dead contract cannot burn the
# ~2s open-position monitor cadence.
_EXACT_QUOTE_HARD_FAIL_CACHE: Dict[str, float] = {}
_EXACT_QUOTE_HARD_FAIL_TTL_SEC = 120.0
_EXACT_QUOTE_RECOVERY_MAX_WALL_SEC = 0.45
OCC_FETCH_HANG_S = 30.0
_OCC_FETCH_INFLIGHT = 0


def occ_fetch_in_flight() -> bool:
    return int(_OCC_FETCH_INFLIGHT) > 0


def _occ_fetch_begin() -> bool:
    global _OCC_FETCH_INFLIGHT
    if _OCC_FETCH_INFLIGHT > 0:
        return False
    _OCC_FETCH_INFLIGHT += 1
    return True


def _occ_fetch_end() -> None:
    global _OCC_FETCH_INFLIGHT
    _OCC_FETCH_INFLIGHT = max(0, int(_OCC_FETCH_INFLIGHT) - 1)


def reset_occ_fetch_inflight_for_tests() -> None:
    global _OCC_FETCH_INFLIGHT
    _OCC_FETCH_INFLIGHT = 0


async def await_uncancelled_executor(
    fn: Callable[[], Any],
    *,
    slow_s: float,
    hang_s: float = OCC_FETCH_HANG_S,
    log_action: str = "exact_quote",
) -> Any:
    """Run sync broker I/O on the default executor and never timeout-cancel it.

    ``asyncio.wait_for`` on ``run_in_executor`` raises TimeoutError and leaves
    the worker running; ``fut.result()`` is then CancelledError and a completed
    quote batch is discarded. Await the future; only log slow/hung.
    """
    loop = asyncio.get_running_loop()
    fut = loop.run_in_executor(None, fn)

    async def _watch() -> None:
        try:
            await asyncio.sleep(max(0.05, float(slow_s)))
            if not fut.done():
                log.warning(
                    "EXACT_CONTRACT_QUOTE | action=%s_slow | elapsed_s=%.1f | cancel=false",
                    log_action,
                    float(slow_s),
                )
            extra = max(0.0, float(hang_s) - float(slow_s))
            if extra > 0:
                await asyncio.sleep(extra)
            if not fut.done():
                log.warning(
                    "EXACT_CONTRACT_QUOTE | action=%s_hung | elapsed_s=%.1f | cancel=false",
                    log_action,
                    float(hang_s),
                )
        except asyncio.CancelledError:
            return

    watcher = asyncio.create_task(_watch(), name=f"{log_action}-watch")
    try:
        return await fut
    finally:
        watcher.cancel()


def _exact_quote_hard_fail_key(occ: str, ident: Optional[OptionContractIdentity] = None) -> str:
    if ident is not None:
        return f"{ident.underlying}|{ident.expiration}|{ident.call_put}|{round(float(ident.strike), 6)}"
    parsed = identity_from_any_option_symbol(occ)
    if parsed is not None:
        return f"{parsed.underlying}|{parsed.expiration}|{parsed.call_put}|{round(float(parsed.strike), 6)}"
    return str(occ or "").strip().upper()


def _exact_quote_hard_fail_ttl() -> float:
    try:
        import os

        return max(30.0, float(os.environ.get("0DTE_EXACT_QUOTE_HARD_FAIL_TTL_SEC", _EXACT_QUOTE_HARD_FAIL_TTL_SEC)))
    except Exception:
        return _EXACT_QUOTE_HARD_FAIL_TTL_SEC


def _exact_quote_recovery_wall_sec() -> float:
    try:
        import os

        return max(0.1, float(os.environ.get("0DTE_EXACT_QUOTE_1002_RECOVERY_MAX_WALL_SEC", _EXACT_QUOTE_RECOVERY_MAX_WALL_SEC)))
    except Exception:
        return _EXACT_QUOTE_RECOVERY_MAX_WALL_SEC


def exact_quote_hard_fail_active(occ: str, *, ident: Optional[OptionContractIdentity] = None, now: Optional[float] = None) -> bool:
    key = _exact_quote_hard_fail_key(occ, ident)
    if not key:
        return False
    expires = _EXACT_QUOTE_HARD_FAIL_CACHE.get(key)
    if expires is None:
        return False
    now_m = float(now if now is not None else time.monotonic())
    if expires <= now_m:
        _EXACT_QUOTE_HARD_FAIL_CACHE.pop(key, None)
        return False
    return True


def mark_exact_quote_hard_fail(
    occ: str,
    *,
    ident: Optional[OptionContractIdentity] = None,
    reason: str = "1002_all_formats_miss",
    now: Optional[float] = None,
) -> None:
    key = _exact_quote_hard_fail_key(occ, ident)
    if not key:
        return
    now_m = float(now if now is not None else time.monotonic())
    ttl = _exact_quote_hard_fail_ttl()
    _EXACT_QUOTE_HARD_FAIL_CACHE[key] = now_m + ttl
    log.warning(
        "EXACT_CONTRACT_QUOTE_HARD_FAIL | occ=%s | key=%s | ttl_sec=%.0f | reason=%s | "
        "action=quarantine_skip_batch",
        occ,
        key,
        ttl,
        reason,
    )


def clear_exact_quote_hard_fail(occ: str, *, ident: Optional[OptionContractIdentity] = None) -> None:
    key = _exact_quote_hard_fail_key(occ, ident)
    if key:
        _EXACT_QUOTE_HARD_FAIL_CACHE.pop(key, None)


def reset_exact_quote_hard_fail_cache() -> None:
    """Test / session helper."""
    _EXACT_QUOTE_HARD_FAIL_CACHE.clear()


@dataclass
class ExactContractQuote:
    option_symbol: str
    bid: Optional[float] = None
    ask: Optional[float] = None
    last: Optional[float] = None
    mark: Optional[float] = None
    bid_size: Optional[float] = None
    ask_size: Optional[float] = None
    # Cumulative session volume when broker provides it; None if absent (never fake 0).
    volume: Optional[float] = None
    open_interest: Optional[float] = None
    quote_status: str = ""
    source: str = "exact_quote"
    # Compatibility: quote_timestamp mirrors broker_quote_timestamp when verified.
    quote_timestamp: Optional[datetime] = None
    broker_quote_timestamp: Optional[datetime] = None
    request_started_at: float = 0.0
    response_received_at: float = 0.0
    parsed_at: float = 0.0
    rtt_ms: float = 0.0
    request_rtt_ms: float = 0.0
    age_ms: Optional[float] = None  # Stage-B age basis (broker or conservative receipt)
    broker_quote_age_ms: Optional[float] = None
    local_receipt_age_ms: Optional[float] = None
    cache_age_ms: Optional[float] = None
    quote_age_basis: str = "none"  # broker_timestamp | local_receipt_conservative | none
    freshness_verified: bool = False
    freshness_status: str = "broker_timestamp_absent"
    contract_identity_matched: bool = False
    rejected: bool = False
    rejection_reason: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)
    delta: Optional[float] = None
    gamma: Optional[float] = None
    theta: Optional[float] = None
    vega: Optional[float] = None
    implied_volatility: Optional[float] = None

    def to_option_quote_dict(self) -> Dict[str, Any]:
        age_ms = self.age_ms
        age_sec = (float(age_ms) / 1000.0) if age_ms is not None else None
        return {
            "bid": self.bid,
            "ask": self.ask,
            "last": self.last,
            "mid": self.mark,
            "mark": self.mark,
            "bid_size": self.bid_size,
            "ask_size": self.ask_size,
            "volume": self.volume,
            "totalVolume": self.volume,
            "total_volume": self.volume,
            "cumulative_volume": self.volume,
            "open_interest": self.open_interest,
            "openInterest": self.open_interest,
            "source": self.source,
            "quote_source": self.source,
            "quote_status": self.quote_status,
            "quote_timestamp": self.quote_timestamp.isoformat() if self.quote_timestamp else None,
            "broker_quote_timestamp": (
                self.broker_quote_timestamp.isoformat() if self.broker_quote_timestamp else None
            ),
            "request_started_at": self.request_started_at,
            "response_received_at": self.response_received_at,
            "parsed_at": self.parsed_at,
            "request_rtt_ms": self.request_rtt_ms or self.rtt_ms,
            # Never expose HTTP RTT as quote_age — only broker or conservative receipt.
            "quote_age_ms": age_ms,
            "quote_age_seconds": age_sec,
            "broker_quote_age_ms": self.broker_quote_age_ms,
            "local_receipt_age_ms": self.local_receipt_age_ms,
            "cache_age_ms": self.cache_age_ms,
            "quote_age_basis": self.quote_age_basis,
            "freshness_verified": self.freshness_verified,
            "freshness_status": self.freshness_status,
            "contract_identity_matched": bool(self.contract_identity_matched),
            "contract_matched": bool(self.contract_identity_matched),
            "option_symbol": self.option_symbol,
            "delta": self.delta,
            "gamma": self.gamma,
            "theta": self.theta,
            "vega": self.vega,
            "implied_volatility": self.implied_volatility,
            "iv": self.implied_volatility,
        }


@dataclass
class ExactQuoteBatchResult:
    quotes: Dict[str, ExactContractQuote]
    batch_count: int = 0
    request_count: int = 0
    symbols_requested: int = 0
    symbols_returned: int = 0
    http_429: int = 0
    http_5xx: int = 0
    timeouts: int = 0
    retried: int = 0
    dropped: int = 0
    rtt_ms_samples: List[float] = field(default_factory=list)
    limitation: str = ""
    used_chain_bridge: bool = False

    def p95_rtt_ms(self) -> Optional[float]:
        if not self.rtt_ms_samples:
            return None
        s = sorted(self.rtt_ms_samples)
        idx = min(len(s) - 1, max(0, int(round(0.95 * (len(s) - 1)))))
        return float(s[idx])


def _positive(value: Any) -> Optional[float]:
    """Strictly positive (>0). Do not use for bids — zero bid is valid."""
    try:
        v = float(value)
        return v if math.isfinite(v) and v > 0 else None
    except (TypeError, ValueError):
        return None


def _volume_or_none(value: Any) -> Optional[float]:
    """Parse cumulative volume; keep 0 when broker reports it; None when absent."""
    if value is None or value == "":
        return None
    try:
        v = float(value)
        if v != v or v < 0:
            return None
        return v
    except (TypeError, ValueError):
        return None


def _first_volume(*candidates: Any) -> Optional[float]:
    for c in candidates:
        parsed = _volume_or_none(c)
        if parsed is not None:
            return parsed
    return None


def _bid_or_none(value: Any) -> Optional[float]:
    """Parse bid without truthiness — valid live bid of 0.00 is kept."""
    if value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v) or v < 0:
        return None
    return v


def encode_option_symbol_for_quote_url(symbol: str) -> str:
    """URL-encode quote path symbols; keep colon grammar intact for E*TRADE options."""
    return quote(str(symbol or "").strip(), safe=":")


def partition_symbols(symbols: Sequence[str], batch_size: int = 25) -> List[List[str]]:
    size = max(1, int(batch_size or 25))
    uniq: List[str] = []
    seen = set()
    for s in symbols:
        key = str(s or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        uniq.append(key)
    return [uniq[i : i + size] for i in range(0, len(uniq), size)]


def prepare_exact_quote_requests(
    symbols: Sequence[str],
) -> Tuple[List[str], Dict[str, str], Dict[str, OptionContractIdentity]]:
    """Map owned OCC / osiKey / colon symbols → E*TRADE quote request params.

    Market Quote expects colon form (``QQQ:2026:7:22:PUT:691``). Dash-padded
    OSI roots (``QQQ---260722P…``) are accepted as *identity input* but must be
    converted — E*TRADE rejects them as ``QQQ--- is an invalid symbol``.

    Returns:
      request_symbols: symbols to send on the wire
      request_to_occ: request_param → original owned symbol key
      occ_to_identity: original symbol → structured identity
    """
    request_symbols: List[str] = []
    request_to_occ: Dict[str, str] = {}
    occ_to_identity: Dict[str, OptionContractIdentity] = {}
    seen_req = set()

    def _is_colon_quote_form(sym: str) -> bool:
        s = str(sym or "").strip()
        return bool(s) and ":" in s and s.count(":") >= 5

    for raw in symbols:
        occ = str(raw or "").strip()
        if not occ:
            continue
        ident = identity_from_any_option_symbol(occ)
        if ident is None:
            # Pass through — diagnostics will show match miss if unusable.
            req = occ_to_etrade_quote_symbol(occ) if (" " in occ or "_" in occ) else occ
        else:
            occ_to_identity[occ] = ident
            # Prefer canonical OCC key for round-trip stability.
            occ_canon = occ_from_identity(ident)
            occ_to_identity[occ_canon] = ident
            if _is_colon_quote_form(occ):
                # Already Market Quote wire form — keep as-is.
                req = occ
            else:
                # Spaced OCC, underscore leg-id, or dash-padded OSI → colon.
                req = identity_to_etrade_quote_symbol(ident)
        if req in seen_req:
            # Same contract requested under alternate OCC spelling — keep first OCC.
            request_to_occ.setdefault(req, occ)
            continue
        seen_req.add(req)
        request_symbols.append(req)
        request_to_occ[req] = occ
    return request_symbols, request_to_occ, occ_to_identity


def identity_from_quote_object(q: Any) -> Optional[OptionContractIdentity]:
    """Build identity from an ETradeQuote / duck-typed quote object."""
    product = getattr(q, "raw_product", None)
    if not isinstance(product, dict):
        product = {}
    osi = str(getattr(q, "osi_key", "") or product.get("osiKey") or "")
    ident = identity_from_product(product, osi_key=osi)
    if ident is not None:
        return ident
    if osi:
        ident = identity_from_osi_key(osi)
        if ident is not None:
            return ident
    sym = str(getattr(q, "symbol", "") or "").strip()
    if ":" in sym:
        return identity_from_etrade_quote_symbol(sym)
    return identity_from_occ(sym)


def match_quote_to_requested_occ(
    q: Any,
    *,
    request_param: str,
    requested_occ: str,
    requested_identity: Optional[OptionContractIdentity],
) -> Tuple[bool, str]:
    """Return (matched, match_method).

    Exact-contract acceptance requires identity equality on underlier, expiration,
    option type, and strike when structured identity is available. String echo
    alone is insufficient when a requested identity was parsed.
    """
    returned_ident = identity_from_quote_object(q)
    if requested_identity is not None:
        if returned_ident is None:
            return False, "response_missing_structured_identity"
        if identities_equal(requested_identity, returned_ident):
            # Prefer confirming wire/osi alignment when present.
            req_osi = str(request_param or "").replace(" ", "").upper()
            ret_osi = str(getattr(q, "osi_key", "") or "").replace(" ", "").upper()
            if req_osi and ret_osi and ("-" in req_osi) and req_osi == ret_osi:
                return True, "structured_product+osi_key"
            return True, "structured_product"
        return False, "identity_mismatch"
    # No requested identity — degrade to string equality (diagnostics only).
    ret_sym = str(getattr(q, "symbol", "") or "").replace(" ", "").upper()
    req_occ = str(requested_occ or "").replace(" ", "").upper()
    if ret_sym and req_occ and ret_sym == req_occ:
        return True, "occ_string"
    osi = str(getattr(q, "osi_key", "") or "").replace(" ", "").upper()
    if osi and req_occ and req_occ in osi.replace("-", ""):
        return True, "osi_key"
    req_p = str(request_param or "").strip().upper()
    if ret_sym and req_p and ret_sym == req_p.replace(" ", ""):
        return True, "request_param_echo"
    return False, "identity_mismatch"


def identity_from_parts_safe(d: Dict[str, Any]) -> Optional[OptionContractIdentity]:
    from modules.option_contract_identity import identity_from_parts

    return identity_from_parts(
        underlying=d.get("underlying"),
        expiration=d.get("expiration"),
        strike=d.get("strike"),
        call_put=d.get("call_put"),
    )



def _parse_broker_quote_timestamp(q: Any) -> Optional[datetime]:
    """Best-effort broker market timestamp from E*TRADE quote fields.

    Authoritative fields observed on successful Market Quote responses:
      - All.dateTimeUTC / QuoteData.dateTimeUTC (epoch seconds; parsed onto
        ETradeQuote.date_time_utc)
      - All.dateTime (string) when present
    HTTP response timing and local receipt are never treated as broker age.
    """
    candidates = (
        getattr(q, "date_time_utc", None),
        getattr(q, "dateTimeUTC", None),
        getattr(q, "dateTime", None),
        getattr(q, "datetime", None),
        getattr(q, "date_time", None),
        getattr(q, "quoteTime", None),
        getattr(q, "timeOfQuote", None),
        getattr(q, "quote_timestamp", None),
    )
    # Also accept dict-like raw / raw_all from parser
    for raw_attr in ("raw", "raw_all", "raw_quote"):
        raw = getattr(q, raw_attr, None)
        if isinstance(raw, dict):
            candidates = candidates + (
                raw.get("dateTimeUTC"),
                raw.get("dateTime"),
                raw.get("datetime"),
                raw.get("quoteTime"),
            )
    for raw_ts in candidates:
        if raw_ts is None or raw_ts == "":
            continue
        if isinstance(raw_ts, datetime):
            return raw_ts if raw_ts.tzinfo else raw_ts.replace(tzinfo=timezone.utc)
        if isinstance(raw_ts, (int, float)):
            try:
                # E*TRADE dateTimeUTC is typically epoch seconds; ms if huge.
                v = float(raw_ts)
                if v > 1e12:
                    v = v / 1000.0
                return datetime.fromtimestamp(v, tz=timezone.utc)
            except (OSError, OverflowError, ValueError):
                continue
        s = str(raw_ts).strip()
        if not s:
            continue
        try:
            if s.isdigit():
                v = float(s)
                if v > 1e12:
                    v /= 1000.0
                return datetime.fromtimestamp(v, tz=timezone.utc)
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        except (TypeError, ValueError, OSError, OverflowError):
            continue
    return None


def _greeks_from_quote_obj(q: Any) -> Dict[str, Optional[float]]:
    try:
        from modules.options_snapshot_fields import extract_greeks_from_mapping
    except Exception:
        extract_greeks_from_mapping = None  # type: ignore
    raw_all = getattr(q, "raw_all", None) if not isinstance(q, dict) else q.get("raw_all")
    if extract_greeks_from_mapping is None:
        return {
            "delta": None,
            "gamma": None,
            "theta": None,
            "vega": None,
            "implied_volatility": None,
        }
    got = extract_greeks_from_mapping(q, raw_all if isinstance(raw_all, dict) else None)
    return {
        "delta": got.get("delta"),
        "gamma": got.get("gamma"),
        "theta": got.get("theta"),
        "vega": got.get("vega"),
        "implied_volatility": got.get("implied_volatility"),
    }


def _quote_from_etrade_quote(q: Any, *, option_symbol: str, started: float, ended: float) -> ExactContractQuote:
    bid = _bid_or_none(getattr(q, "bid", None))
    ask = _positive(getattr(q, "ask", None))
    last = _positive(getattr(q, "last", None) or getattr(q, "last_price", None))
    mark = None
    if bid is not None and ask is not None:
        mark = (bid + ask) / 2.0
    elif last is not None:
        mark = last
    parsed_at = time.time()
    rtt_ms = max(0.0, (ended - started) * 1000.0)
    broker_ts = _parse_broker_quote_timestamp(q)
    freshness_verified = broker_ts is not None
    broker_age_ms = None
    if broker_ts is not None:
        broker_age_ms = max(0.0, (parsed_at - broker_ts.timestamp()) * 1000.0)
        # Reject absurd future timestamps beyond 5s clock skew
        if broker_ts.timestamp() - parsed_at > 5.0:
            freshness_verified = False
            broker_ts = None
            broker_age_ms = None
    product = getattr(q, "raw_product", None)
    if not isinstance(product, dict):
        product = {}
    osi = str(getattr(q, "osi_key", "") or product.get("osiKey") or "")
    returned_ident = identity_from_quote_object(q)
    raw = {
        "symbol": getattr(q, "symbol", option_symbol),
        "request_symbol": getattr(q, "request_symbol", "") or "",
        "osiKey": osi,
        "securityType": getattr(q, "security_type", "") or product.get("securityType") or "",
        "callPut": getattr(q, "call_put", "") or product.get("callPut") or "",
        "expiryYear": getattr(q, "expiry_year", None)
        if getattr(q, "expiry_year", None) is not None
        else product.get("expiryYear"),
        "expiryMonth": getattr(q, "expiry_month", None)
        if getattr(q, "expiry_month", None) is not None
        else product.get("expiryMonth"),
        "expiryDay": getattr(q, "expiry_day", None)
        if getattr(q, "expiry_day", None) is not None
        else product.get("expiryDay"),
        "strikePrice": getattr(q, "strike_price", None)
        if getattr(q, "strike_price", None) is not None
        else product.get("strikePrice"),
        "dateTimeUTC": getattr(q, "date_time_utc", None),
        "product": sanitize_identity_fields(product, osi_key=osi),
    }
    if returned_ident is not None:
        raw["matched_identity"] = {
            "underlying": returned_ident.underlying,
            "expiration": returned_ident.expiration,
            "strike": returned_ident.strike,
            "call_put": returned_ident.call_put,
        }
    receipt_age_ms = max(0.0, (parsed_at - float(ended or parsed_at)) * 1000.0)
    vol = _first_volume(
        getattr(q, "volume", None),
        getattr(q, "total_volume", None),
        getattr(q, "totalVolume", None),
        getattr(q, "cumulative_volume", None),
        product.get("volume") if isinstance(product, dict) else None,
        product.get("totalVolume") if isinstance(product, dict) else None,
        product.get("total_volume") if isinstance(product, dict) else None,
    )
    oi = _first_volume(
        getattr(q, "open_interest", None),
        getattr(q, "openInterest", None),
        product.get("openInterest") if isinstance(product, dict) else None,
        product.get("open_interest") if isinstance(product, dict) else None,
    )
    return ExactContractQuote(
        option_symbol=option_symbol,
        bid=bid,
        ask=ask,
        last=last,
        mark=mark,
        bid_size=_positive(getattr(q, "bid_size", None) or getattr(q, "bidSize", None)),
        ask_size=_positive(getattr(q, "ask_size", None) or getattr(q, "askSize", None)),
        volume=vol,
        open_interest=oi,
        quote_status=str(getattr(q, "quote_status", "") or getattr(q, "status", "") or ""),
        source="exact_quote",
        quote_timestamp=broker_ts,
        broker_quote_timestamp=broker_ts,
        request_started_at=started,
        response_received_at=ended,
        parsed_at=parsed_at,
        rtt_ms=rtt_ms,
        request_rtt_ms=rtt_ms,
        age_ms=broker_age_ms,
        broker_quote_age_ms=broker_age_ms,
        local_receipt_age_ms=receipt_age_ms,
        quote_age_basis="broker_timestamp" if freshness_verified else "none",
        freshness_verified=freshness_verified,
        freshness_status=(
            "broker_timestamp_verified" if freshness_verified else "broker_timestamp_absent"
        ),
        raw=raw,
        **_greeks_from_quote_obj(q),
    )


def apply_max_age(
    quote: ExactContractQuote,
    *,
    max_age_sec: float,
    now: Optional[float] = None,
    soft_stale_ok: bool = False,
) -> ExactContractQuote:
    """Apply freshness without converting HTTP RTT into broker quote age.

    Distinguishes:
      - broker_timestamp_verified → age from broker dateTimeUTC/dateTime
        (or ``quote_timestamp`` when that is the broker clock)
      - broker_timestamp_absent → conservative local receipt age for Stage B
      - request_rtt_ms kept separately (never used as quote age)

    Exit-grade safety: any present broker clock older than ``max_age_sec`` is
    rejected (``stale_exact_quote``), even if callers only set
    ``quote_timestamp`` without flipping ``freshness_verified``.

    When ``soft_stale_ok`` (Selected-ED / weekly / non-0DTE closest contract), a
    usable bid is kept exit-grade even past ``max_age_sec`` — age alone must not
    block triggered exits. Freshness is still recorded for telemetry.
    """
    now_ts = float(now if now is not None else time.time())
    # Local receipt age = time since response bytes received (not RTT).
    try:
        receipt_base = float(quote.response_received_at or quote.parsed_at or now_ts)
        quote.local_receipt_age_ms = max(0.0, (now_ts - receipt_base) * 1000.0)
    except Exception:
        quote.local_receipt_age_ms = None

    def _mark_soft_stale(age: float, *, basis: str, cap: float) -> None:
        quote.rejected = False
        quote.rejection_reason = None
        # Keep exact_quote so exit-grade allowlists still accept the mark.
        if str(quote.source or "").strip().lower().startswith("stale"):
            quote.source = "exact_quote"
        quote.freshness_status = "broker_timestamp_soft_stale_exit_ok"
        raw = quote.raw if isinstance(quote.raw, dict) else {}
        raw = dict(raw)
        raw["soft_stale"] = True
        raw["soft_stale_age_sec"] = float(age)
        raw["soft_stale_max_age_sec"] = float(cap)
        raw["soft_stale_basis"] = str(basis)
        quote.raw = raw

    # Normalize broker clock aliases so stale quote_timestamp cannot bypass rejection.
    broker_ts = quote.broker_quote_timestamp or quote.quote_timestamp
    if broker_ts is not None:
        quote.broker_quote_timestamp = broker_ts
        quote.quote_timestamp = broker_ts
        quote.freshness_verified = True

    if quote.freshness_verified and quote.broker_quote_timestamp is not None:
        try:
            age = max(0.0, now_ts - quote.broker_quote_timestamp.timestamp())
            quote.broker_quote_age_ms = age * 1000.0
            quote.age_ms = quote.broker_quote_age_ms
            quote.quote_age_basis = "broker_timestamp"
            quote.freshness_status = "broker_timestamp_verified"
            if age > float(max_age_sec):
                if soft_stale_ok and quote.bid is not None:
                    _mark_soft_stale(age, basis="broker_timestamp", cap=float(max_age_sec))
                else:
                    quote.rejected = True
                    quote.rejection_reason = f"quote_age_exceeded:{age:.3f}s>{max_age_sec}"
                    quote.source = "stale_exact_quote"
                    quote.freshness_status = "broker_timestamp_stale"
        except Exception:
            quote.freshness_verified = False
            quote.freshness_status = "broker_timestamp_absent"
            quote.broker_quote_age_ms = None
            quote.age_ms = None
            quote.quote_age_basis = "none"

    if not quote.freshness_verified:
        # Keep source as exact_quote; apply conservative receipt policy.
        quote.freshness_status = "broker_timestamp_absent_conservative_receipt"
        quote.quote_age_basis = "local_receipt_conservative"
        receipt_age_sec = (
            float(quote.local_receipt_age_ms) / 1000.0
            if quote.local_receipt_age_ms is not None
            else None
        )
        if receipt_age_sec is None:
            quote.age_ms = None
            quote.quote_age_basis = "none"
            quote.freshness_status = "broker_timestamp_absent"
        else:
            quote.age_ms = float(quote.local_receipt_age_ms)
            # Tighter of configured max_age and 3s conservative ceiling.
            conservative_cap = min(float(max_age_sec), 3.0)
            if receipt_age_sec > conservative_cap:
                if soft_stale_ok and quote.bid is not None:
                    _mark_soft_stale(
                        receipt_age_sec, basis="local_receipt", cap=float(conservative_cap)
                    )
                else:
                    quote.rejected = True
                    quote.rejection_reason = (
                        f"receipt_age_exceeded:{receipt_age_sec:.3f}s>{conservative_cap}"
                    )
                    quote.source = "stale_exact_quote"
                    quote.freshness_status = "broker_timestamp_absent_receipt_stale"

    # Valid bid of 0.00 is not missing — only None is missing.
    if quote.bid is None:
        quote.rejected = True
        quote.rejection_reason = quote.rejection_reason or "missing_bid"
    return quote


def validate_exact_contract_acceptance(
    quote: ExactContractQuote,
    *,
    expected: Optional[OptionContractIdentity],
    matched: bool,
) -> ExactContractQuote:
    """Commit B acceptance: owned identity match + usable exact valuation."""
    if not matched:
        quote.rejected = True
        quote.rejection_reason = quote.rejection_reason or "contract_identity_mismatch"
        quote.source = "missing_exact_quote"
        return quote
    if expected is not None:
        raw_ident = quote.raw.get("matched_identity")
        got = identity_from_parts_safe(raw_ident) if isinstance(raw_ident, dict) else None
        if got is None:
            got = identity_from_occ(quote.option_symbol)
        if got is not None and not identities_equal(expected, got):
            quote.rejected = True
            quote.rejection_reason = "owned_contract_identity_mismatch"
            quote.source = "missing_exact_quote"
            return quote
    if quote.bid is None and (quote.ask is None or float(quote.ask or 0) <= 0) and (
        quote.mark is None or float(quote.mark or 0) <= 0
    ):
        quote.rejected = True
        quote.rejection_reason = quote.rejection_reason or "missing_exact_valuation"
    return quote


async def fetch_exact_contract_quotes(
    symbols: Sequence[str],
    *,
    etrade_trader: Any = None,
    data_manager: Any = None,
    timeout_sec: float = 1.5,
    max_age_sec: float = 3.0,
    batch_size: int = 25,
    concurrency: int = 2,
    broker_lane: str = "open_exact_occ",
    soft_stale_ok: bool = False,
) -> ExactQuoteBatchResult:
    """Fetch fresh bid/ask for owned option contracts via Market Quote API.

    ``soft_stale_ok``: Selected-ED / weekly / non-0DTE — keep usable bids past the
    age gate so triggered exits are not blocked by broker clock lag.
    """
    result = ExactQuoteBatchResult(quotes={})
    # Skip hard-fail quarantined OCCs from the wire batch so one dead contract
    # cannot steal the ~2s open-position cadence.
    live_symbols: List[str] = []
    now_m = time.monotonic()
    for raw in symbols or []:
        occ = str(raw or "").strip()
        if not occ:
            continue
        ident = identity_from_any_option_symbol(occ)
        if exact_quote_hard_fail_active(occ, ident=ident, now=now_m):
            result.quotes[occ] = ExactContractQuote(
                option_symbol=occ,
                rejected=True,
                rejection_reason="exact_quote_hard_fail_quarantine",
                source="hard_fail_quarantine",
                raw={
                    "hard_fail_quarantine": True,
                    "hard_fail_key": _exact_quote_hard_fail_key(occ, ident),
                },
            )
            continue
        live_symbols.append(occ)
    request_symbols, request_to_occ, occ_to_identity = prepare_exact_quote_requests(
        live_symbols
    )
    batches = partition_symbols(request_symbols, batch_size=batch_size)
    result.batch_count = len(batches)
    result.symbols_requested = sum(len(b) for b in batches) + len(
        [1 for q in result.quotes.values() if getattr(q, "source", "") == "hard_fail_quarantine"]
    )
    if not batches:
        return result

    trader = etrade_trader
    if trader is None and data_manager is not None:
        trader = getattr(data_manager, "etrade_trader", None) or getattr(
            getattr(data_manager, "broker_provider", None), "etrade_trader", None
        )
    if trader is None or not (
        hasattr(trader, "get_quotes") or hasattr(trader, "get_option_contract_quotes")
    ):
        result.limitation = "etrade_quote_adapter_unavailable"
        log.error("EXACT_CONTRACT_QUOTE | limitation=%s", result.limitation)
        return result

    sem = asyncio.Semaphore(max(1, int(concurrency)))

    async def _one_batch(batch: List[str]) -> None:
        async with sem:
            started = time.time()
            result.request_count += 1
            http_status = None
            try:
                from modules.broker_api_pacing import amarket_api_request_slot

                async with amarket_api_request_slot(
                    reason="exact_occ",
                    lane=str(broker_lane or "open_exact_occ"),
                ):
                    # Prefer dedicated helper when present (URL-encodes path symbols).
                    # Do not wait_for-cancel. A 1.5s timeout discards a completed
                    # executor result (11/5 quote_lat p50 5.7s). HTTP hang bound is
                    # OCC_FETCH_HANG_S so fail_fast does not abort a live 5–8s call.
                    hang_s = float(OCC_FETCH_HANG_S)
                    if hasattr(trader, "get_option_contract_quotes"):
                        def _occ_batch(b=batch):
                            try:
                                return trader.get_option_contract_quotes(
                                    b, fail_fast=True, timeout_sec=hang_s
                                )
                            except TypeError:
                                return trader.get_option_contract_quotes(b)

                        quotes_list = await await_uncancelled_executor(
                            _occ_batch,
                            slow_s=float(timeout_sec),
                            hang_s=hang_s,
                            log_action="exact_quote",
                        )
                    else:
                        quotes_list = await await_uncancelled_executor(
                            lambda b=batch: trader.get_quotes(
                                b, fail_fast=True, timeout_sec=hang_s
                            ),
                            slow_s=float(timeout_sec),
                            hang_s=hang_s,
                            log_action="exact_quote",
                        )
                ended = time.time()
                rtt = (ended - started) * 1000.0
                result.rtt_ms_samples.append(rtt)

                returned_rows: List[Any] = list(quotes_list or [])
                # Optional status attached by trader helpers.
                if hasattr(trader, "last_quote_http_status"):
                    try:
                        http_status = getattr(trader, "last_quote_http_status")
                    except Exception:
                        http_status = None

                unmatched = set(batch)
                used_quote_idxs = set()
                for req_sym in batch:
                    occ = request_to_occ.get(req_sym, req_sym)
                    expected = occ_to_identity.get(occ) or identity_from_occ(occ)
                    matched_q = None
                    match_method = "none"
                    for idx, q in enumerate(returned_rows):
                        if idx in used_quote_idxs:
                            continue
                        ok, method = match_quote_to_requested_occ(
                            q,
                            request_param=req_sym,
                            requested_occ=occ,
                            requested_identity=expected,
                        )
                        if ok:
                            matched_q = q
                            match_method = method
                            used_quote_idxs.add(idx)
                            unmatched.discard(req_sym)
                            break

                    hit = matched_q is not None
                    if not hit:
                        returned_fields = []
                        for q in returned_rows:
                            product = getattr(q, "raw_product", None)
                            if not isinstance(product, dict):
                                product = {
                                    "symbol": getattr(q, "symbol", None),
                                    "callPut": getattr(q, "call_put", None),
                                    "expiryYear": getattr(q, "expiry_year", None),
                                    "expiryMonth": getattr(q, "expiry_month", None),
                                    "expiryDay": getattr(q, "expiry_day", None),
                                    "strikePrice": getattr(q, "strike_price", None),
                                    "securityType": getattr(q, "security_type", None),
                                }
                            returned_fields.append(
                                sanitize_identity_fields(
                                    product,
                                    osi_key=str(getattr(q, "osi_key", "") or ""),
                                )
                            )
                        log.info(
                            "EXACT_CONTRACT_QUOTE_MATCH | requested_occ_symbol=%s | "
                            "request_parameter_sent=%s | http_status=%s | "
                            "response_contract_count=%d | match_decision=miss | "
                            "match_method=%s | returned_symbol_fields=%s",
                            occ,
                            req_sym,
                            http_status if http_status is not None else "n/a",
                            len(returned_rows),
                            match_method,
                            returned_fields[:2],
                        )
                    else:
                        log.debug(
                            "EXACT_CONTRACT_QUOTE_MATCH | requested_occ_symbol=%s | "
                            "match_decision=hit | match_method=%s | response_contract_count=%d",
                            occ,
                            match_method,
                            len(returned_rows),
                        )
                        returned_fields = []

                    if matched_q is None:
                        result.quotes[occ] = ExactContractQuote(
                            option_symbol=occ,
                            rejected=True,
                            rejection_reason="symbol_not_in_response",
                            source="missing_exact_quote",
                            request_started_at=started,
                            response_received_at=ended,
                            rtt_ms=rtt,
                            raw={
                                "request_parameter_sent": req_sym,
                                "response_contract_count": len(returned_rows),
                                "returned_symbol_fields": returned_fields[:8],
                                "http_status": http_status,
                                "match_method": match_method,
                                "winning_symbol_format": (
                                    "colon_wire"
                                    if (":" in str(req_sym) and str(req_sym).count(":") >= 5)
                                    else "other"
                                ),
                                "miss_class": (
                                    "batch_omission"
                                    if len(returned_rows) > 0
                                    else "etrade_intermittent_omit"
                                ),
                            },
                        )
                        continue

                    eq = _quote_from_etrade_quote(
                        matched_q, option_symbol=occ, started=started, ended=ended
                    )
                    eq.contract_identity_matched = True
                    if not isinstance(eq.raw, dict):
                        eq.raw = {}
                    eq.raw["request_parameter_sent"] = req_sym
                    eq.raw["match_method"] = match_method
                    eq.raw["winning_symbol_format"] = (
                        "colon_wire"
                        if (":" in str(req_sym) and str(req_sym).count(":") >= 5)
                        else "other"
                    )
                    eq.raw["recovery_succeeded"] = False
                    eq = apply_max_age(
                        eq,
                        max_age_sec=max_age_sec,
                        now=ended,
                        soft_stale_ok=bool(soft_stale_ok),
                    )
                    eq = validate_exact_contract_acceptance(
                        eq, expected=expected, matched=True
                    )
                    result.quotes[occ] = eq
                    if not eq.rejected:
                        clear_exact_quote_hard_fail(occ, ident=expected)
                        result.symbols_returned += 1

                # E*TRADE 1002 / empty QuoteData: retry formatting alternates
                # (padded dates, .0 strikes, OSI) via get_quotes — not get_option_contract_quotes
                # which would re-map to the same primary colon symbol.
                if unmatched and hasattr(trader, "get_quotes"):
                    miss_occs: List[str] = []
                    miss_primary: Dict[str, str] = {}
                    miss_expected: Dict[str, Optional[OptionContractIdentity]] = {}
                    miss_alts: Dict[str, List[str]] = {}
                    for req_sym in list(unmatched):
                        occ = request_to_occ.get(req_sym, req_sym)
                        expected = occ_to_identity.get(occ) or identity_from_occ(occ)
                        if exact_quote_hard_fail_active(occ, ident=expected):
                            continue
                        # Formatting alternates cannot create a listing. If this
                        # expiry is already memo-empty, quarantine and skip recovery.
                        try:
                            from modules.option_expiry_memo import is_empty as _exp_empty
                        except Exception:
                            _exp_empty = None  # type: ignore
                        if (
                            _exp_empty is not None
                            and expected is not None
                            and _exp_empty(expected.underlying, expected.expiration)
                        ):
                            mark_exact_quote_hard_fail(
                                occ,
                                ident=expected,
                                reason="empty_expiry_memo_skip_1002_recovery",
                            )
                            prev_miss = result.quotes.get(occ)
                            if prev_miss is not None:
                                raw_m = dict(getattr(prev_miss, "raw", None) or {})
                                raw_m["hard_fail_quarantine"] = True
                                raw_m["empty_expiry_memo"] = True
                                prev_miss.raw = raw_m
                            continue
                        alts = [
                            c
                            for c in etrade_quote_symbol_candidates(occ)
                            if c and c != req_sym
                        ]
                        if not alts:
                            continue
                        miss_occs.append(occ)
                        miss_primary[occ] = req_sym
                        miss_expected[occ] = expected
                        miss_alts[occ] = alts

                    max_rounds = max((len(v) for v in miss_alts.values()), default=0)
                    recovery_started_m = time.monotonic()
                    recovery_wall = _exact_quote_recovery_wall_sec()
                    for round_i in range(max_rounds):
                        if (time.monotonic() - recovery_started_m) >= recovery_wall:
                            log.warning(
                                "EXACT_CONTRACT_QUOTE_1002_RECOVERY | budget_exhausted | "
                                "wall_sec=%.2f | rounds_done=%d",
                                recovery_wall,
                                round_i,
                            )
                            break
                        still_missing = [
                            occ
                            for occ in miss_occs
                            if result.quotes.get(occ) is not None
                            and bool(getattr(result.quotes[occ], "rejected", False))
                            and getattr(result.quotes[occ], "rejection_reason", "")
                            == "symbol_not_in_response"
                        ]
                        if not still_missing:
                            break
                        alt_batch: List[str] = []
                        alt_to_occ: Dict[str, str] = {}
                        for occ in still_missing:
                            alts = miss_alts.get(occ) or []
                            if round_i >= len(alts):
                                continue
                            alt = alts[round_i]
                            alt_batch.append(alt)
                            alt_to_occ[alt] = occ
                        if not alt_batch:
                            continue
                        result.request_count += 1
                        recover_started = time.time()
                        try:
                            from modules.broker_api_pacing import (
                                LANE_OWNED_RECOVERY,
                                amarket_api_request_slot,
                            )

                            async with amarket_api_request_slot(
                                reason="owned_recovery",
                                lane=LANE_OWNED_RECOVERY,
                            ):
                                recover_rows = list(
                                    await await_uncancelled_executor(
                                        lambda b=list(alt_batch): trader.get_quotes(
                                            b,
                                            fail_fast=True,
                                            timeout_sec=float(OCC_FETCH_HANG_S),
                                        ),
                                        slow_s=float(timeout_sec),
                                        hang_s=float(OCC_FETCH_HANG_S),
                                        log_action="exact_quote_1002",
                                    )
                                    or []
                                )
                        except Exception as recover_err:
                            log.warning(
                                "EXACT_CONTRACT_QUOTE_1002_RECOVERY | round=%d | "
                                "batch_failed | error=%s",
                                round_i + 1,
                                recover_err,
                            )
                            continue
                        recover_ended = time.time()
                        recover_rtt = (recover_ended - recover_started) * 1000.0
                        result.rtt_ms_samples.append(recover_rtt)
                        used_recover = set()
                        for alt, occ in alt_to_occ.items():
                            expected = miss_expected.get(occ)
                            matched_q = None
                            match_method = "none"
                            for idx, q in enumerate(recover_rows):
                                if idx in used_recover:
                                    continue
                                ok, method = match_quote_to_requested_occ(
                                    q,
                                    request_param=alt,
                                    requested_occ=occ,
                                    requested_identity=expected,
                                )
                                if ok:
                                    matched_q = q
                                    match_method = method
                                    used_recover.add(idx)
                                    break
                            log.info(
                                "EXACT_CONTRACT_QUOTE_1002_RECOVERY | requested_occ_symbol=%s | "
                                "primary_parameter=%s | retry_parameter=%s | round=%d | "
                                "match_decision=%s | match_method=%s | response_contract_count=%d",
                                occ,
                                miss_primary.get(occ),
                                alt,
                                round_i + 1,
                                "hit" if matched_q is not None else "miss",
                                match_method,
                                len(recover_rows),
                            )
                            if matched_q is None:
                                prev_miss = result.quotes.get(occ)
                                if prev_miss is not None:
                                    prev_raw = dict(getattr(prev_miss, "raw", None) or {})
                                    prev_raw["recovery_attempted"] = True
                                    prev_raw["recovery_succeeded"] = False
                                    prev_raw["1002_recovery_round"] = round_i + 1
                                    prev_miss.raw = prev_raw
                                continue
                            eq = _quote_from_etrade_quote(
                                matched_q,
                                option_symbol=occ,
                                started=recover_started,
                                ended=recover_ended,
                            )
                            eq.contract_identity_matched = True
                            eq = apply_max_age(
                                eq,
                                max_age_sec=max_age_sec,
                                now=recover_ended,
                                soft_stale_ok=bool(soft_stale_ok),
                            )
                            eq = validate_exact_contract_acceptance(
                                eq, expected=expected, matched=True
                            )
                            eq.raw = dict(eq.raw or {})
                            eq.raw["request_parameter_sent"] = alt
                            eq.raw["primary_parameter_sent"] = miss_primary.get(occ)
                            eq.raw["1002_recovery_round"] = round_i + 1
                            eq.raw["recovery_attempted"] = True
                            eq.raw["recovery_succeeded"] = not bool(eq.rejected)
                            eq.raw["winning_symbol_format"] = (
                                "colon_wire"
                                if (":" in str(alt) and str(alt).count(":") >= 5)
                                else "osi_or_alt"
                            )
                            prev = result.quotes.get(occ)
                            was_rejected = bool(
                                prev is not None and getattr(prev, "rejected", False)
                            )
                            result.quotes[occ] = eq
                            if not eq.rejected:
                                clear_exact_quote_hard_fail(occ, ident=expected)
                                if was_rejected:
                                    result.symbols_returned += 1
                                unmatched.discard(miss_primary.get(occ, ""))

                    # Exhausted formatting alternates with no hit → quarantine.
                    for occ in miss_occs:
                        q = result.quotes.get(occ)
                        if q is None or not getattr(q, "rejected", False):
                            continue
                        if getattr(q, "rejection_reason", "") != "symbol_not_in_response":
                            continue
                        raw = dict(getattr(q, "raw", None) or {})
                        if not raw.get("recovery_attempted") or raw.get("recovery_succeeded"):
                            continue
                        mark_exact_quote_hard_fail(
                            occ,
                            ident=miss_expected.get(occ),
                            reason="1002_all_formats_miss",
                        )
                        raw["hard_fail_quarantine"] = True
                        q.raw = raw

                if unmatched and not returned_rows:
                    # Only keep empty_quote_response if recovery also failed.
                    still_empty = any(
                        result.quotes.get(request_to_occ.get(r, r)) is not None
                        and bool(
                            getattr(
                                result.quotes[request_to_occ.get(r, r)],
                                "rejected",
                                False,
                            )
                        )
                        for r in unmatched
                    )
                    if still_empty:
                        result.limitation = result.limitation or "empty_quote_response"
            except asyncio.TimeoutError:
                result.timeouts += 1
                result.dropped += 1
                log.warning("EXACT_CONTRACT_QUOTE | timeout | batch_size=%d", len(batch))
                for req_sym in batch:
                    occ = request_to_occ.get(req_sym, req_sym)
                    result.quotes[occ] = ExactContractQuote(
                        option_symbol=occ,
                        rejected=True,
                        rejection_reason="timeout",
                        source="timeout_exact_quote",
                        raw={"request_parameter_sent": req_sym},
                    )
            except Exception as err:
                err_s = str(err).lower()
                if "429" in err_s or "rate" in err_s:
                    result.http_429 += 1
                if "50" in err_s:
                    result.http_5xx += 1
                result.dropped += 1
                log.warning("EXACT_CONTRACT_QUOTE | batch_failed | error=%s", err)
                for req_sym in batch:
                    occ = request_to_occ.get(req_sym, req_sym)
                    result.quotes.setdefault(
                        occ,
                        ExactContractQuote(
                            option_symbol=occ,
                            rejected=True,
                            rejection_reason=f"batch_error:{err}",
                            source="error_exact_quote",
                            raw={"request_parameter_sent": req_sym},
                        ),
                    )

    if not _occ_fetch_begin():
        result.limitation = "occ_fetch_inflight"
        log.warning(
            "EXACT_CONTRACT_QUOTE | limitation=occ_fetch_inflight | requested=%d",
            result.symbols_requested,
        )
        return result
    try:
        # Sequential inner batches. gather() held pri-0 on both waves so UL
        # never interleaved, and stacked two OCC HTTP. After each batch
        # except the last, yield the lane so a waiting equity_batch can stamp
        # before OCC batch 2. Do not raise 4s/8s TTL.
        from modules.broker_api_pacing import yield_lane_to_equity_batch

        n_batches = len(batches)
        for i, batch in enumerate(batches):
            await _one_batch(batch)
            if i + 1 < n_batches:
                await yield_lane_to_equity_batch()
    finally:
        _occ_fetch_end()
    log.info(
        "EXACT_CONTRACT_QUOTE | requested=%d | returned=%d | batches=%d | timeouts=%d | http_429=%d | p95_rtt_ms=%s",
        result.symbols_requested,
        result.symbols_returned,
        result.batch_count,
        result.timeouts,
        result.http_429,
        f"{result.p95_rtt_ms():.1f}" if result.p95_rtt_ms() is not None else "n/a",
    )
    return result


async def _resolve_underlying_spot(system: Any, und: str) -> Optional[float]:
    dm = getattr(system, "data_manager", None)
    if dm is None or not hasattr(dm, "get_quote"):
        return None
    try:
        q = await dm.get_quote(und)
        if isinstance(q, dict):
            spot = float(q.get("last") or q.get("price") or q.get("mark") or 0.0)
            return spot if spot > 0 else None
    except Exception:
        return None
    return None


def _resolve_options_chain_manager(system: Any) -> Any:
    """Prefer dte0 OptionsChainManager — PrimeDataManager has no option chain."""
    from easy0DTE.modules.options_chain_manager import resolve_options_chain_manager

    return resolve_options_chain_manager(system)


def _ymd_to_iso_expiry(ymd: str) -> Optional[str]:
    """YYYYMMDD → YYYY-MM-DD for chain fetches; None if invalid."""
    n = _normalize_expiry_ymd(ymd)
    if len(n) != 8:
        return None
    return f"{n[0:4]}-{n[4:6]}-{n[6:8]}"


def _listed_expiry_if_stamped_day_empty(
    und: str,
    stamped_ymd: str,
    *,
    hint_ymd: str = "",
) -> Optional[str]:
    """When the stamped OCC day is a memo-empty same-day, return the listed day.

    Same door as entry: ``option_expiry_memo`` nearest / expiredate. Does not
    walk calendar days. Used so Monday weekly fills are quoted on Friday OCC
    instead of 1002-retrying today's missing contract.
    """
    try:
        from modules.option_expiry_memo import (
            is_empty,
            nearest_for,
            normalize_expiry_iso,
            session_today_iso,
        )
    except Exception:
        return None
    und_u = str(und or "").strip().upper()
    stamped_iso = normalize_expiry_iso(stamped_ymd)
    today = session_today_iso()
    if not und_u or not stamped_iso:
        return None
    hint_iso = normalize_expiry_iso(hint_ymd)
    if hint_iso and hint_iso != stamped_iso:
        return hint_iso
    if stamped_iso != today:
        return None
    if not is_empty(und_u, stamped_iso):
        return None
    nearest = nearest_for(und_u)
    if nearest and nearest != stamped_iso:
        return nearest
    return None


async def _fetch_live_option_chain_dict(
    system: Any,
    und: str,
    *,
    expiry_ymd: str = "",
) -> Optional[Dict[str, Any]]:
    """Fetch a live chain dict for repair / bridge.

    When ``expiry_ymd`` is set (open-position contract expiry), fetch **that day
    only**. Entry already chose 0DTE or weekly DTE 1–4; monitor must not
    rediscover expiry via same-day-first / multi-day walks.
    """
    und_u = str(und or "").strip().upper()
    if not und_u:
        return None
    spot = await _resolve_underlying_spot(system, und_u)
    expiry_iso = _ymd_to_iso_expiry(expiry_ymd) if expiry_ymd else None
    from easy0DTE.modules.options_chain_manager import fetch_0dte_intent_chain

    chain = await fetch_0dte_intent_chain(
        None,
        und_u,
        system=system,
        expiry=expiry_iso,
        use_cache=False,
        underlying_price=spot,
    )
    if isinstance(chain, dict) and (chain.get("calls") or chain.get("puts") or chain):
        return chain
    log.warning(
        "CHAIN_FETCH | door=fetch_0dte_intent_chain | underlying=%s | expiry=%s | empty=true",
        und_u,
        expiry_iso or "default",
    )
    return None


def _chain_contracts_list(chain: Any) -> List[Any]:
    if not chain:
        return []
    if isinstance(chain, list):
        return list(chain)
    if not isinstance(chain, dict):
        return []
    contracts = list(chain.get("contracts") or chain.get("options") or [])
    if not contracts and ("calls" in chain or "puts" in chain):
        contracts = list(chain.get("calls") or []) + list(chain.get("puts") or [])
    return contracts


def _bridge_contract_key(underlying: Any, occ: Any, strike: Any, option_type: Any, expiry: Any) -> str:
    try:
        strike_s = f"{float(strike):.6f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        strike_s = str(strike or "").strip()
    return "|".join(
        [
            str(underlying or "").strip().upper(),
            str(expiry or "").replace("-", "").strip(),
            str(option_type or "").strip().lower()[:1],
            strike_s,
            str(occ or "").replace(" ", "").strip().upper(),
        ]
    )


def _bridge_negative_cache(system: Any) -> Dict[str, float]:
    cache = getattr(system, "_chain_bridge_negative_contract_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        try:
            setattr(system, "_chain_bridge_negative_contract_cache", cache)
        except Exception:
            return {}
    return cache


def _bridge_negative_ttl(max_age_sec: float) -> float:
    return max(30.0, min(180.0, float(max_age_sec or 0.0) * 20.0))


def _bridge_is_negative_cached(system: Any, key: str, *, now_m: float) -> bool:
    if not key:
        return False
    expires_at = _bridge_negative_cache(system).get(key)
    if expires_at is None:
        return False
    if float(expires_at) <= now_m:
        _bridge_negative_cache(system).pop(key, None)
        return False
    return True


def _bridge_mark_negative(system: Any, key: str, *, now_m: float, max_age_sec: float) -> None:
    if key:
        _bridge_negative_cache(system)[key] = now_m + _bridge_negative_ttl(max_age_sec)


def _bridge_clear_negative(system: Any, key: str) -> None:
    if key:
        _bridge_negative_cache(system).pop(key, None)


async def fetch_chain_bridge_bids(
    system: Any,
    positions: Sequence[Any],
    *,
    max_age_sec: float = 3.0,
    skip_cache: bool = True,
) -> Dict[str, float]:
    """Fresh matching chain bids grouped by underlying — never synthetic, never aged cache.

    Returns trade_id -> bid for contracts that match exactly (strike/expiry/CP).
    """
    from modules.active_option_contract_registry import resolve_occ_symbol_from_position

    out: Dict[str, float] = {}
    if not skip_cache:
        log.warning("CHAIN_BRIDGE | skip_cache_required=true | refusing_cached_bridge")
        return out

    # Group positions by underlying. Skip contracts that a fresh chain recently
    # proved absent, so an invalid broker symbol does not force-refresh every tick.
    now_m = time.monotonic()
    by_und: Dict[str, List[Tuple[Any, str, str, Any, str, str]]] = {}
    for p in positions or []:
        und = str(getattr(p, "symbol", "") or "").strip().upper()
        if not und:
            continue
        occ, _u, strike, otype, expiry = resolve_occ_symbol_from_position(p)
        key = _bridge_contract_key(und, occ, strike, otype, expiry)
        if _bridge_is_negative_cached(system, key, now_m=now_m):
            continue
        by_und.setdefault(und, []).append((p, occ, strike, otype, expiry, key))

    for und, plist in by_und.items():
        chain = await _fetch_live_option_chain_dict(system, und)
        if not chain:
            continue
        contracts = _chain_contracts_list(chain)

        for p, occ, strike, otype, expiry, key in plist:
            tid = str(getattr(p, "position_id", "") or "")
            matched_bid = None
            for c in contracts or []:
                if not isinstance(c, dict):
                    # object-like
                    c_strike = float(getattr(c, "strike", 0) or 0)
                    c_type = str(getattr(c, "option_type", getattr(c, "type", "")) or "").lower()
                    c_exp = str(getattr(c, "expiry", getattr(c, "expiration", "")) or "").replace("-", "")
                    c_bid = getattr(c, "bid", None)
                    c_occ = str(getattr(c, "symbol", getattr(c, "option_symbol", "")) or "")
                else:
                    c_strike = float(c.get("strike") or 0)
                    c_type = str(c.get("option_type") or c.get("type") or "").lower()
                    c_exp = str(c.get("expiry") or c.get("expiration") or "").replace("-", "")
                    c_bid = c.get("bid")
                    c_occ = str(c.get("symbol") or c.get("option_symbol") or "")
                if occ and c_occ and occ.replace(" ", "") == c_occ.replace(" ", ""):
                    matched_bid = _bid_or_none(c_bid)
                    break
                # Require explicit expiry on both sides — empty expiry must not soft-match
                # nearest-listed contracts (false "live" marks on wrong weeklies).
                exp_n = str(expiry or "").replace("-", "").strip()
                c_exp_n = str(c_exp or "").replace("-", "").strip()
                expiry_ok = bool(exp_n and c_exp_n and (exp_n in c_exp_n or c_exp_n in exp_n))
                if (
                    abs(c_strike - float(strike or 0)) < 1e-6
                    and otype[:1].lower() in c_type
                    and expiry_ok
                ):
                    matched_bid = _bid_or_none(c_bid)
                    break
            if matched_bid is not None:
                _bridge_clear_negative(system, key)
                out[tid] = float(matched_bid)
                log.info(
                    "CHAIN_BRIDGE_HIT | trade_id=%s | underlying=%s | bid=%s | source=fresh_chain_refresh | max_age_sec=%s",
                    tid,
                    und,
                    matched_bid,
                    max_age_sec,
                )
            else:
                _bridge_mark_negative(system, key, now_m=now_m, max_age_sec=max_age_sec)
    return out


async def fetch_chain_bridge_quotes_for_continuations(
    system: Any,
    continuations: Sequence[Dict[str, Any]],
    *,
    max_age_sec: float = 3.0,
) -> Dict[str, Dict[str, Any]]:
    """Match live chain quotes for PO shadow continuations missing exact OCC hits.

    Returns trade_id -> quote dict suitable for monitor_premium_snapshot (trusted_chain).
    """
    out: Dict[str, Dict[str, Any]] = {}
    if not continuations:
        return out
    now_m = time.monotonic()
    by_und: Dict[str, List[Tuple[Dict[str, Any], Dict[str, Any], str, Optional[float], str, str, str]]] = {}
    for cont in continuations:
        if not isinstance(cont, dict):
            continue
        und = str(cont.get("symbol") or "").strip().upper()
        if not und:
            continue
        legs = cont.get("legs") if isinstance(cont.get("legs"), list) else []
        leg0 = legs[0] if legs and isinstance(legs[0], dict) else {}
        occ = str(
            leg0.get("broker_occ_symbol")
            or leg0.get("option_symbol")
            or (
                cont.get("option_symbols")[0]
                if isinstance(cont.get("option_symbols"), list) and cont.get("option_symbols")
                else ""
            )
            or ""
        ).strip()
        try:
            strike = float(leg0.get("strike")) if leg0.get("strike") is not None else None
        except (TypeError, ValueError):
            strike = None
        otype = str(leg0.get("option_type") or leg0.get("side") or "call").lower()
        expiry = str(leg0.get("expiration") or "").replace("-", "")
        key = _bridge_contract_key(und, occ, strike, otype, expiry)
        if _bridge_is_negative_cached(system, key, now_m=now_m):
            continue
        by_und.setdefault(und, []).append((cont, leg0, occ, strike, otype, expiry, key))

    for und, clist in by_und.items():
        chain = await _fetch_live_option_chain_dict(system, und)
        if not chain:
            continue
        contracts = _chain_contracts_list(chain)
        for cont, leg0, occ, strike, otype, expiry, key in clist:
            tid = str(cont.get("trade_id") or "").strip()
            matched: Optional[Dict[str, Any]] = None
            match_status = ""
            for c in contracts or []:
                if isinstance(c, dict):
                    c_strike = float(c.get("strike") or 0)
                    c_type = str(c.get("option_type") or c.get("type") or "").lower()
                    c_exp = str(c.get("expiry") or c.get("expiration") or "").replace("-", "")
                    c_bid = c.get("bid")
                    c_ask = c.get("ask")
                    c_occ = str(c.get("symbol") or c.get("option_symbol") or "")
                else:
                    c_strike = float(getattr(c, "strike", 0) or 0)
                    c_type = str(getattr(c, "option_type", getattr(c, "type", "")) or "").lower()
                    c_exp = str(getattr(c, "expiry", getattr(c, "expiration", "")) or "").replace("-", "")
                    c_bid = getattr(c, "bid", None)
                    c_ask = getattr(c, "ask", None)
                    c_occ = str(getattr(c, "symbol", getattr(c, "option_symbol", "")) or "")
                if occ and c_occ and occ.replace(" ", "") == c_occ.replace(" ", ""):
                    matched = {"bid": _bid_or_none(c_bid), "ask": _bid_or_none(c_ask), "option_symbol": c_occ or occ}
                    match_status = "chain_occ_match"
                    break
                exp_n = str(expiry or "").replace("-", "").strip()
                c_exp_n = str(c_exp or "").replace("-", "").strip()
                expiry_ok = bool(exp_n and c_exp_n and (exp_n in c_exp_n or c_exp_n in exp_n))
                if (
                    strike is not None
                    and abs(c_strike - float(strike)) < 1e-6
                    and otype[:1].lower() in c_type
                    and expiry_ok
                ):
                    matched = {"bid": _bid_or_none(c_bid), "ask": _bid_or_none(c_ask), "option_symbol": c_occ or occ}
                    match_status = "chain_strike_expiry_match"
                    break
            if not tid or not matched:
                _bridge_mark_negative(system, key, now_m=now_m, max_age_sec=max_age_sec)
                continue
            bid = matched.get("bid")
            ask = matched.get("ask")
            mark = bid if bid is not None else ask
            if mark is None:
                _bridge_mark_negative(system, key, now_m=now_m, max_age_sec=max_age_sec)
                continue
            _bridge_clear_negative(system, key)
            mid = None
            if bid is not None and ask is not None:
                mid = (float(bid) + float(ask)) / 2.0
            out[tid] = {
                "bid": bid,
                "ask": ask,
                "mid": mid,
                "mark": mark,
                "option_symbol": matched.get("option_symbol") or occ,
                "premium_source": "fresh_chain_refresh",
                "quote_source": "fresh_chain_refresh",
                "freshness_status": "chain_fresh",
                "contract_match_status": match_status,
                "quote_age_seconds": float(max_age_sec),
            }
            log.info(
                "CHAIN_BRIDGE_CONTINUATION_HIT | trade_id=%s | underlying=%s | mark=%s | match=%s",
                tid,
                und,
                mark,
                match_status,
            )
    return out


def _normalize_expiry_ymd(raw: Any) -> str:
    s = str(raw or "").strip().replace("-", "")
    if len(s) >= 8 and s[:8].isdigit():
        return s[:8]
    return ""


def _contract_side_matches(c_type: str, option_type: str) -> bool:
    ct = str(c_type or "").strip().lower()
    ot = str(option_type or "").strip().lower()
    if not ct or not ot:
        return False
    return ot[:1] in ct or ct.startswith(ot[:3])


def _safe_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _chain_leg_fields(c: Any) -> Dict[str, Any]:
    """Normalize a chain contract (dict or object) to comparable fields."""
    if isinstance(c, dict):
        strike = _safe_float(c.get("strike"))
        otype = str(c.get("option_type") or c.get("type") or c.get("callPut") or "")
        expiry = _normalize_expiry_ymd(c.get("expiry") or c.get("expiration"))
        osi = str(c.get("osi_key") or c.get("osiKey") or "").strip()
        bid = _safe_float(c.get("bid"))
        ask = _safe_float(c.get("ask"))
        last = _safe_float(c.get("last") or c.get("lastPrice"))
        delta = _safe_float(c.get("delta"))
        und = str(c.get("symbol") or c.get("underlying") or "").strip().upper()
    else:
        strike = _safe_float(getattr(c, "strike", None))
        otype = str(
            getattr(c, "option_type", None)
            or getattr(c, "type", None)
            or getattr(c, "call_put", "")
            or ""
        )
        expiry = _normalize_expiry_ymd(
            getattr(c, "expiry", None) or getattr(c, "expiration", None)
        )
        osi = str(getattr(c, "osi_key", "") or getattr(c, "osiKey", "") or "").strip()
        bid = _safe_float(getattr(c, "bid", None))
        ask = _safe_float(getattr(c, "ask", None))
        last = _safe_float(getattr(c, "last", None))
        delta = _safe_float(getattr(c, "delta", None))
        und = str(getattr(c, "symbol", "") or "").strip().upper()
    if osi:
        try:
            from modules.option_contract_identity import identity_from_osi_key

            ident = identity_from_osi_key(osi)
            if ident is not None:
                expiry = _normalize_expiry_ymd(ident.expiration) or expiry
                if strike is None:
                    strike = float(ident.strike)
                if not otype:
                    otype = str(ident.call_put or "")
                if not und:
                    und = str(ident.underlying or "").upper()
        except Exception:
            pass
    mid = None
    if bid is not None and ask is not None and bid > 0 and ask > 0:
        mid = (bid + ask) / 2.0
    elif last is not None and last > 0:
        mid = last
    elif bid is not None and bid > 0:
        mid = bid
    elif ask is not None and ask > 0:
        mid = ask
    return {
        "underlier": und,
        "strike": float(strike or 0.0),
        "option_type": otype,
        "expiry_ymd": expiry,
        "osi_key": osi,
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "delta": delta,
    }


def _extract_entry_identity_evidence(pos: Any, *, und: str, strike: float, otype: str, stamped: str, old_occ: str) -> Dict[str, Any]:
    """Collect entry evidence used to validate a recovered chain leg."""
    meta = getattr(pos, "metadata", None)
    if not isinstance(meta, dict):
        meta = {}
    norm = meta.get("normalized_options") if isinstance(meta.get("normalized_options"), dict) else {}
    legs = norm.get("legs") if isinstance(norm.get("legs"), list) else []
    leg0 = legs[0] if legs and isinstance(legs[0], dict) else {}
    payload = meta.get("execution_payload") if isinstance(meta.get("execution_payload"), dict) else {}
    econ = meta.get("trade_economics_entry") if isinstance(meta.get("trade_economics_entry"), dict) else {}
    lc = getattr(pos, "lotto_contract", None)

    entry_delta = (
        _safe_float(leg0.get("delta_at_entry"))
        or _safe_float(norm.get("delta_at_entry"))
        or _safe_float(payload.get("delta_at_entry"))
        or _safe_float(getattr(lc, "delta", None) if lc is not None else None)
    )
    entry_premium = (
        _safe_float(getattr(pos, "entry_price", None))
        or _safe_float(leg0.get("entry_price"))
        or _safe_float(norm.get("entry_debit") or norm.get("entry_value"))
        or _safe_float(econ.get("fill_per_share") or econ.get("entry_premium"))
        or _safe_float(payload.get("premium") or payload.get("entry_premium"))
    )
    entry_bid = _safe_float(leg0.get("entry_bid") or leg0.get("bid") or norm.get("entry_bid"))
    entry_ask = _safe_float(leg0.get("entry_ask") or leg0.get("ask") or norm.get("entry_ask"))

    # Open-position expiry is fixed at fill (Demo ledger / Live broker). Prefer the
    # stamped OCC / selected_expiry_ymd already on the trade — never rediscover DTE.
    known_open_expiry = _normalize_expiry_ymd(
        stamped
        or meta.get("selected_expiry_ymd")
        or meta.get("expiry_ymd")
        or meta.get("requested_expiry_ymd")
        or norm.get("expiration_ymd")
        or payload.get("selected_expiry")
        or payload.get("expiry_ymd")
        or payload.get("expiry")
        or ""
    )
    entry_expiry_hint = _normalize_expiry_ymd(
        meta.get("selected_expiry_ymd")
        or payload.get("selected_expiry")
        or meta.get("entry_listed_expiry")
        or ""
    )
    if entry_expiry_hint and known_open_expiry and entry_expiry_hint == known_open_expiry:
        entry_expiry_hint = ""

    chain_source = str(
        meta.get("entry_chain_source")
        or payload.get("chain_source")
        or norm.get("source_path")
        or ""
    ).strip()

    return {
        "trade_id": str(getattr(pos, "position_id", "") or ""),
        "underlier": str(und or "").upper(),
        "option_type": str(otype or "call").lower(),
        "strike": float(strike),
        "stamped_expiry": known_open_expiry or stamped,
        "old_occ": str(old_occ or ""),
        "entry_delta": entry_delta,
        "entry_premium": entry_premium,
        "entry_bid": entry_bid,
        "entry_ask": entry_ask,
        "entry_expiry_hint": entry_expiry_hint,
        "chain_source": chain_source,
        "known_open_expiry": known_open_expiry or stamped,
    }


def _premium_compatible(entry_premium: Optional[float], chain_mid: Optional[float]) -> bool:
    """Allow repair only when chain mid is economically plausible vs entry fill."""
    if entry_premium is None or entry_premium <= 0:
        return True  # no evidence → do not block on premium
    if chain_mid is None or chain_mid <= 0:
        return False  # have entry premium but chain leg has no mark → reject
    # Absolute floor for cheap options + relative band for richer premiums.
    tol = max(0.50, 0.65 * float(entry_premium))
    return abs(float(chain_mid) - float(entry_premium)) <= tol


def _delta_compatible(entry_delta: Optional[float], chain_delta: Optional[float]) -> bool:
    if entry_delta is None:
        return True
    if chain_delta is None:
        return True  # missing greeks should not hard-fail when premium/strike match
    return abs(abs(float(chain_delta)) - abs(float(entry_delta))) <= 0.20


def _validate_recovered_chain_leg(
    *,
    evidence: Dict[str, Any],
    chain: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Pick one chain leg that matches identity + entry evidence; else None.

    Hard requirements: underlier (when present on leg), option_type, strike.
    Soft/required-when-present: entry premium, entry delta, entry expiry hint.
    Never invent a nearest-expiry remap from underlier+strike alone without a
    concrete matched leg on the returned chain.
    """
    if not chain:
        return None
    und = str(evidence.get("underlier") or "").upper()
    otype = str(evidence.get("option_type") or "call").lower()
    strike = float(evidence.get("strike") or 0.0)
    if not und or strike <= 0:
        return None

    selected = _normalize_expiry_ymd(
        (chain or {}).get("selected_expiry") or (chain or {}).get("expiry") or ""
    )
    known = _normalize_expiry_ymd(
        evidence.get("known_open_expiry")
        or evidence.get("stamped_expiry")
        or ""
    )
    hint = _normalize_expiry_ymd(evidence.get("entry_expiry_hint") or "")
    entry_premium = evidence.get("entry_premium")
    entry_delta = evidence.get("entry_delta")

    candidates: List[Dict[str, Any]] = []
    for c in _chain_contracts_list(chain):
        fields = _chain_leg_fields(c)
        if fields["strike"] <= 0:
            continue
        if abs(fields["strike"] - strike) >= 1e-6:
            continue
        if not _contract_side_matches(fields["option_type"], otype):
            continue
        leg_und = str(fields.get("underlier") or "").upper()
        # Chain legs often store underlier-only symbol; accept blank or matching root.
        if leg_und and leg_und not in (und, und.split()[0] if und else und):
            # Allow underlier root match (e.g. "COIN" vs "COIN--…").
            if not und.startswith(leg_und) and not leg_und.startswith(und):
                continue
        if not fields.get("expiry_ymd"):
            continue
        # Open position already selected expiry — only accept that contract day.
        if known and fields["expiry_ymd"] != known:
            continue
        if not _premium_compatible(entry_premium, fields.get("mid")):
            continue
        if not _delta_compatible(entry_delta, fields.get("delta")):
            continue

        score = 0.0
        reasons = ["strike", "option_type"]
        if known and fields["expiry_ymd"] == known:
            score += 8.0
            reasons.append("known_open_expiry")
        if fields.get("osi_key"):
            score += 5.0
            reasons.append("osi_key")
        if selected and fields["expiry_ymd"] == selected:
            score += 4.0
            reasons.append("selected_expiry")
        if hint and fields["expiry_ymd"] == hint:
            score += 6.0
            reasons.append("entry_expiry_hint")
        if entry_premium is not None and fields.get("mid") is not None:
            score += 3.0
            reasons.append("entry_premium")
        if entry_delta is not None and fields.get("delta") is not None:
            score += 2.0
            reasons.append("entry_delta")
        if evidence.get("chain_source"):
            score += 0.5
            reasons.append("entry_chain_source")

        candidates.append({**fields, "match_score": score, "match_reasons": reasons})

    if not candidates:
        return None

    # Prefer highest score; require uniqueness on expiry among top-tier matches.
    candidates.sort(key=lambda x: float(x.get("match_score") or 0.0), reverse=True)
    best = candidates[0]
    top = [c for c in candidates if abs(float(c["match_score"]) - float(best["match_score"])) < 1e-9]
    expiries = {str(c["expiry_ymd"]) for c in top}
    if len(expiries) > 1:
        # Ambiguous nearest-expiry collision — refuse blind remap.
        log.warning(
            "OCC_EXPIRY_REPAIR | stage=guard_reject | trade_id=%s | underlying=%s | "
            "strike=%s | side=%s | reason=ambiguous_expiry_candidates | expiries=%s",
            evidence.get("trade_id"),
            und,
            strike,
            otype,
            ",".join(sorted(expiries)),
        )
        return None

    # Require a concrete selected leg — never "nearest underlier strike" without evidence.
    if not best.get("expiry_ymd"):
        return None
    if not best.get("osi_key") and not (entry_premium or hint or selected):
        # Without osiKey, require at least one independent evidence signal.
        log.warning(
            "OCC_EXPIRY_REPAIR | stage=guard_reject | trade_id=%s | underlying=%s | "
            "strike=%s | side=%s | reason=insufficient_entry_evidence",
            evidence.get("trade_id"),
            und,
            strike,
            otype,
        )
        return None

    return best


def _apply_atomic_identity_repair(
    pos: Any,
    *,
    evidence: Dict[str, Any],
    matched: Dict[str, Any],
    system: Any = None,
) -> bool:
    """Update OCC / osiKey / aliases / PO identity only — never economic/monitoring state."""
    und = str(evidence.get("underlier") or "").upper()
    otype = str(evidence.get("option_type") or "call").lower()
    strike = float(evidence.get("strike") or 0.0)
    old_occ = str(evidence.get("old_occ") or "")
    stamped = str(evidence.get("stamped_expiry") or "")
    new_exp = str(matched.get("expiry_ymd") or "")
    chain_osi = str(matched.get("osi_key") or "").strip()
    if not new_exp:
        return False

    try:
        from easy0DTE.modules.etrade_options_api import format_occ_option_symbol

        new_occ = format_occ_option_symbol(und, new_exp, otype, strike)
    except Exception:
        from modules.option_contract_identity import identity_from_parts, occ_from_identity

        ident = identity_from_parts(
            underlying=und,
            expiration=f"{new_exp[0:4]}-{new_exp[4:6]}-{new_exp[6:8]}",
            strike=strike,
            call_put=otype,
        )
        new_occ = occ_from_identity(ident) if ident is not None else ""
    if not new_occ:
        return False

    quote_req = ""
    try:
        from modules.option_contract_identity import (
            identity_from_parts,
            identity_to_etrade_quote_symbol,
        )

        ident = identity_from_parts(
            underlying=und,
            expiration=f"{new_exp[0:4]}-{new_exp[4:6]}-{new_exp[6:8]}",
            strike=strike,
            call_put=otype,
        )
        if ident is not None:
            # Market Quote wire form — never store dash-padded OSI as quote request
            # (E*TRADE rejects QQQ--- / QBTS-- as invalid symbols).
            quote_req = identity_to_etrade_quote_symbol(ident)
    except Exception:
        quote_req = ""
    if not quote_req and chain_osi:
        quote_req = chain_osi

    meta = getattr(pos, "metadata", None)
    if not isinstance(meta, dict):
        meta = {}
        try:
            pos.metadata = meta
        except Exception:
            return False

    # Snapshot economic/monitoring keys — must remain unchanged.
    _frozen_econ = {
        k: meta.get(k)
        for k in (
            "trade_economics_entry",
            "orb_0dte_durability",
            "0dte_session_verification",
            "option_stealth",
            "pending_close",
            "close_lifecycle",
            "broker_order_id",
            "broker_fill_confirmed",
            "fill_path",
            "entry_source",
        )
        if k in meta
    }
    entry_price_before = getattr(pos, "entry_price", None)
    qty_before = getattr(pos, "quantity", None)

    expiry_changed = not (stamped and stamped == new_exp)
    side_fixed = bool(old_occ) and (
        ("C" in old_occ.replace(" ", "").upper()[-9:-8] and otype == "put")
        or ("P" in old_occ.replace(" ", "").upper()[-9:-8] and otype == "call")
    )
    existing_quote = str(
        meta.get("broker_quote_request_symbol") or meta.get("osi_key") or meta.get("osi_symbol") or ""
    ).strip()
    if not expiry_changed and not side_fixed and existing_quote and existing_quote == quote_req:
        return False

    # --- identity only ---
    meta["occ_symbol"] = new_occ
    meta["option_symbol"] = new_occ
    meta["contract_symbol"] = new_occ
    meta["expiration"] = new_exp
    meta["expiry"] = new_exp
    meta["expiry_ymd"] = new_exp
    meta["occ_expiry_repaired"] = True
    meta["occ_expiry_repaired_from"] = stamped
    meta["occ_expiry_repaired_to"] = new_exp
    meta["occ_identity_repair_reasons"] = list(matched.get("match_reasons") or [])
    if quote_req:
        meta["broker_quote_request_symbol"] = quote_req
        meta["osi_key"] = chain_osi or quote_req
        meta["osi_symbol"] = chain_osi or quote_req

    norm = meta.get("normalized_options") if isinstance(meta.get("normalized_options"), dict) else {}
    if norm:
        norm["expiration_ymd"] = new_exp
        if quote_req:
            norm["broker_quote_request_symbol"] = quote_req
            norm["osi_key"] = chain_osi or quote_req
            norm["osi_symbol"] = chain_osi or quote_req
        legs = norm.get("legs") if isinstance(norm.get("legs"), list) else []
        for leg in legs:
            if not isinstance(leg, dict):
                continue
            # Identity fields only — keep entry_price / delta_at_entry / economics.
            leg["expiration"] = new_exp
            leg["expiry"] = new_exp
            leg["occ_symbol"] = new_occ
            leg["option_symbol"] = new_occ
            side = str(leg.get("option_side") or leg.get("option_type") or otype or "call").lower()
            leg["leg_id"] = f"{und}_{new_exp}_{strike}_{side}"
            if quote_req:
                leg["broker_quote_request_symbol"] = quote_req
                leg["osi_key"] = chain_osi or quote_req
                leg["osi_symbol"] = chain_osi or quote_req
        meta["normalized_options"] = norm

    lc = getattr(pos, "lotto_contract", None)
    if lc is not None:
        try:
            lc.expiry = new_exp
            if hasattr(lc, "osi_key") and chain_osi:
                lc.osi_key = chain_osi
        except Exception:
            pass

    # Restore any accidentally overwritten econ keys (defensive).
    for k, v in _frozen_econ.items():
        meta[k] = v
    if entry_price_before is not None:
        try:
            pos.entry_price = entry_price_before
        except Exception:
            pass
    if qty_before is not None:
        try:
            pos.quantity = qty_before
        except Exception:
            pass

    # Registry + Profit Optimizer identity (aliases / frozen OCC / quote request).
    trade_id = str(evidence.get("trade_id") or "")
    if trade_id:
        try:
            from modules.active_option_contract_registry import (
                ActiveOptionContract,
                get_active_option_contract_registry,
                strategy_from_position,
            )

            reg = get_active_option_contract_registry()
            existing = reg.get(trade_id)
            mode = str(getattr(existing, "mode", None) or meta.get("mode") or "demo")
            qty = int(getattr(pos, "quantity", None) or getattr(existing, "quantity", 1) or 1)
            entry_fill = float(
                getattr(pos, "entry_price", None)
                or getattr(existing, "entry_fill_price", 0.0)
                or 0.0
            )
            reg.upsert(
                ActiveOptionContract(
                    trade_id=trade_id,
                    strategy=strategy_from_position(pos)
                    if existing is None
                    else getattr(existing, "strategy", "") or strategy_from_position(pos),
                    underlying=und,
                    option_symbol=new_occ,
                    expiration=new_exp,
                    strike=strike,
                    option_type=otype,
                    quantity=max(1, qty),
                    entry_fill_price=entry_fill,
                    mode=mode,
                    pending_close=bool(getattr(existing, "pending_close", False)),
                    extras={
                        "broker_quote_request_symbol": quote_req or "",
                        "osi_key": chain_osi or quote_req or "",
                        "occ_expiry_repaired_from": stamped,
                        "occ_expiry_repaired_to": new_exp,
                    },
                )
            )
        except Exception:
            pass

    if system is not None and trade_id:
        symbol_map: Dict[str, str] = {}
        if old_occ and new_occ and old_occ != new_occ:
            symbol_map[old_occ] = new_occ
        if existing_quote and quote_req and existing_quote != quote_req:
            symbol_map[existing_quote] = quote_req
        try:
            from profit_optimizer.trading_hooks import rewrite_continuation_option_symbols

            rewrite_continuation_option_symbols(system, trade_id, symbol_map)
        except Exception:
            pass
        try:
            po = getattr(system, "profit_optimizer", None)
            rec = None
            if po is not None and getattr(po, "recorder", None) is not None:
                rec = po.recorder._records.get(trade_id)
            if rec is not None and isinstance(getattr(rec.entry, "contract_identity", None), dict):
                ci = rec.entry.contract_identity
                ci["broker_occ_symbol"] = new_occ
                ci["option_symbol"] = new_occ
                ci["normalized_occ_symbol"] = new_occ
                ci["expiration"] = new_exp
                if quote_req:
                    ci["broker_quote_request_symbol"] = quote_req
                    ci["osi_symbol"] = chain_osi or quote_req
                if isinstance(ci.get("legs"), list):
                    for leg in ci["legs"]:
                        if not isinstance(leg, dict):
                            continue
                        leg["option_symbol"] = new_occ
                        leg["broker_occ_symbol"] = new_occ
                        if quote_req:
                            leg["broker_quote_request_symbol"] = quote_req
                rec.entry.contract_identity = ci
        except Exception:
            pass

    log.warning(
        "OCC_EXPIRY_REPAIR | trade_id=%s | underlying=%s | strike=%s | side=%s | "
        "from_expiry=%s | to_expiry=%s | from_occ=%s | to_occ=%s | quote_req=%s | "
        "match_reasons=%s | economics_unchanged=true",
        trade_id or "?",
        und,
        strike,
        otype,
        stamped or "?",
        new_exp,
        old_occ or "?",
        new_occ,
        quote_req or "?",
        ",".join(matched.get("match_reasons") or []),
    )
    return True


async def _resolve_validated_chain_leg(
    system: Any,
    *,
    und: str,
    evidence: Dict[str, Any],
    stamped: str = "",
) -> Optional[Dict[str, Any]]:
    """Validate the open position's **already-selected** contract expiry.

    Demo ledger and Live broker positions carry the fill expiry (0DTE or weekly
    DTE 1–4). Monitor/repair fetch **that day only** — never walk calendar days.
    Exception: a same-day OCC stamp whose day is memo-empty uses the fill's
    ``selected_expiry_ymd`` or ``option_expiry_memo.nearest_for`` (one listed
    day, same door as entry).
    """
    known = _normalize_expiry_ymd(
        stamped
        or evidence.get("known_open_expiry")
        or evidence.get("stamped_expiry")
        or evidence.get("entry_expiry_hint")
        or ""
    )
    if not known:
        log.warning(
            "OCC_EXPIRY_REPAIR | stage=guard_reject | trade_id=%s | underlying=%s | "
            "strike=%s | side=%s | reason=no_known_open_expiry",
            evidence.get("trade_id"),
            und,
            evidence.get("strike"),
            evidence.get("option_type"),
        )
        return None

    evidence = dict(evidence)
    evidence["known_open_expiry"] = known
    evidence["stamped_expiry"] = known

    listed_iso = _listed_expiry_if_stamped_day_empty(
        und,
        known,
        hint_ymd=str(evidence.get("entry_expiry_hint") or ""),
    )
    fetch_ymd = _normalize_expiry_ymd(listed_iso) if listed_iso else known
    validate_evidence = dict(evidence)
    if listed_iso and fetch_ymd and fetch_ymd != known:
        validate_evidence["known_open_expiry"] = fetch_ymd
        validate_evidence["stamped_expiry"] = fetch_ymd
        log.info(
            "OCC_EXPIRY_REPAIR | stage=empty_same_day_use_listed | underlying=%s | "
            "stamped=%s | listed=%s | hint=%s",
            und,
            known,
            fetch_ymd,
            evidence.get("entry_expiry_hint") or "",
        )

    chain = await _fetch_live_option_chain_dict(system, und, expiry_ymd=fetch_ymd)
    matched = _validate_recovered_chain_leg(evidence=validate_evidence, chain=chain)
    if matched is not None:
        return matched

    log.info(
        "OCC_EXPIRY_REPAIR | stage=known_expiry_miss | underlying=%s | strike=%s | "
        "side=%s | known_expiry=%s | reason=no_validated_leg_on_open_position_expiry",
        und,
        evidence.get("strike"),
        evidence.get("option_type"),
        known,
    )
    return None


async def repair_owned_contract_expiry_from_chain(
    system: Any,
    positions: Sequence[Any],
) -> int:
    """Guarded identity repair: map wrong-day OCC → validated listed chain leg.

    Validates recovered legs against underlier, option_type, strike, selected chain
    leg, and entry delta/premium / chain-source evidence when available. Updates
    identity atomically (OCC, osiKey, registry aliases, quote request, PO identity)
    without changing economic or monitoring state.
    """
    from modules.active_option_contract_registry import resolve_occ_symbol_from_position

    repaired = 0
    for p in positions or []:
        meta = getattr(p, "metadata", None)
        if not isinstance(meta, dict):
            meta = {}
            try:
                p.metadata = meta
            except Exception:
                pass
        # Skip recent successful validations — forward expiry walks are the cadence killer.
        try:
            ok_mono = float(meta.get("_occ_expiry_ok_mono") or 0.0)
        except Exception:
            ok_mono = 0.0
        if ok_mono > 0.0 and (time.monotonic() - ok_mono) < 300.0:
            continue
        occ, und, strike, otype, expiry = resolve_occ_symbol_from_position(p)
        if not und or float(strike or 0) <= 0:
            continue
        stamped = _normalize_expiry_ymd(expiry)
        evidence = _extract_entry_identity_evidence(
            p,
            und=und,
            strike=float(strike),
            otype=str(otype or "call"),
            stamped=stamped,
            old_occ=str(occ or ""),
        )
        matched = await _resolve_validated_chain_leg(
            system, und=und, evidence=evidence, stamped=stamped
        )
        if matched is None:
            log.warning(
                "OCC_EXPIRY_REPAIR | stage=guard_reject | trade_id=%s | underlying=%s | "
                "strike=%s | side=%s | stamped=%s | reason=no_validated_chain_leg",
                evidence.get("trade_id"),
                und,
                strike,
                otype,
                stamped or "?",
            )
            continue

        matched_exp = _normalize_expiry_ymd(matched.get("expiry_ymd") or "")
        known = _normalize_expiry_ymd(
            evidence.get("known_open_expiry") or stamped or ""
        )
        # Already on the open-position expiry — no identity rewrite (monitor must not
        # "re-select" or normalize-for-its-own-sake). Mark validated and continue.
        if known and matched_exp and matched_exp == known:
            try:
                meta["_occ_expiry_ok_mono"] = time.monotonic()
            except Exception:
                pass
            continue

        if _apply_atomic_identity_repair(p, evidence=evidence, matched=matched, system=system):
            repaired += 1
        try:
            meta["_occ_expiry_ok_mono"] = time.monotonic()
        except Exception:
            pass
    return repaired


async def _listed_expiry_ymd_for_contract(
    system: Any,
    *,
    und: str,
    strike: float,
    otype: str,
    stamped: str = "",
) -> Optional[str]:
    """Confirm the open position's known expiry is listed for strike/side.

    Does **not** rediscover DTE or walk calendar days. Entry already selected
    0DTE or weekly; ``stamped`` is that contract day.
    """
    stamped_n = _normalize_expiry_ymd(stamped)
    if not stamped_n:
        log.warning(
            "OCC_EXPIRY_REPAIR | stage=listed_expiry | underlying=%s | strike=%s | "
            "side=%s | skip=no_known_open_expiry",
            und,
            strike,
            otype,
        )
        return None

    async def _expiries_for_strike(chain: Optional[Dict[str, Any]]) -> List[str]:
        if not chain:
            return []
        out: List[str] = []
        for c in _chain_contracts_list(chain):
            osi = ""
            if isinstance(c, dict):
                c_strike = float(c.get("strike") or 0)
                c_type = str(c.get("option_type") or c.get("type") or c.get("callPut") or "")
                c_exp = _normalize_expiry_ymd(c.get("expiry") or c.get("expiration"))
                osi = str(c.get("osi_key") or c.get("osiKey") or "").strip()
            else:
                c_strike = float(getattr(c, "strike", 0) or 0)
                c_type = str(
                    getattr(c, "option_type", None)
                    or getattr(c, "type", None)
                    or getattr(c, "call_put", "")
                    or ""
                )
                c_exp = _normalize_expiry_ymd(
                    getattr(c, "expiry", None) or getattr(c, "expiration", None)
                )
                osi = str(getattr(c, "osi_key", "") or getattr(c, "osiKey", "") or "").strip()
            if osi:
                try:
                    from modules.option_contract_identity import identity_from_osi_key

                    ident = identity_from_osi_key(osi)
                    if ident is not None and ident.expiration:
                        c_exp = _normalize_expiry_ymd(ident.expiration)
                except Exception:
                    pass
            if abs(c_strike - float(strike)) < 1e-6 and _contract_side_matches(c_type, otype):
                if c_exp and c_exp not in out:
                    out.append(c_exp)
        return out

    chain = await _fetch_live_option_chain_dict(system, und, expiry_ymd=stamped_n)
    listed_expiries = await _expiries_for_strike(chain)
    if stamped_n in listed_expiries:
        return stamped_n

    log.info(
        "OCC_EXPIRY_REPAIR | stage=listed_expiry | underlying=%s | strike=%s | side=%s | "
        "known_expiry=%s | reason=strike_absent_on_open_position_expiry | no_calendar_walk=true",
        und,
        strike,
        otype,
        stamped_n,
    )
    return None


def _rewrite_symbol_with_expiry(symbol: str, new_exp_ymd: str) -> str:
    """Preserve colon vs OCC style while swapping expiry."""
    raw = str(symbol or "").strip()
    ident = identity_from_any_option_symbol(raw)
    if ident is None:
        return raw
    ymd = _normalize_expiry_ymd(new_exp_ymd)
    if len(ymd) != 8:
        return raw
    new_ident = identity_from_parts(
        underlying=ident.underlying,
        expiration=f"{ymd[0:4]}-{ymd[4:6]}-{ymd[6:8]}",
        strike=float(ident.strike),
        call_put=ident.call_put,
    )
    if new_ident is None:
        return raw
    if ":" in raw:
        return identity_to_etrade_quote_symbol(new_ident)
    return occ_from_identity(new_ident)


async def repair_quote_symbols_expiry_from_chain(
    system: Any,
    symbols: Sequence[str],
) -> Dict[str, str]:
    """Map wrong-day quote symbols → validated listed-expiry wire symbols.

    Uses the same chain-leg guard as position repair (underlier/side/strike +
    selected leg / osiKey). Does not invent nearest expiry from strike alone.
    """
    out: Dict[str, str] = {}
    for raw in symbols or []:
        sym = str(raw or "").strip()
        if not sym or sym in out:
            continue
        ident = identity_from_any_option_symbol(sym)
        if ident is None:
            continue
        stamped = _normalize_expiry_ymd(ident.expiration)
        evidence = {
            "trade_id": "",
            "underlier": ident.underlying,
            "option_type": str(ident.call_put or "call"),
            "strike": float(ident.strike),
            "stamped_expiry": stamped,
            "known_open_expiry": stamped,
            "old_occ": sym,
            "entry_delta": None,
            "entry_premium": None,
            "entry_bid": None,
            "entry_ask": None,
            "entry_expiry_hint": "",
            "chain_source": "",
        }
        matched = await _resolve_validated_chain_leg(
            system, und=ident.underlying, evidence=evidence, stamped=stamped
        )
        if matched is None:
            continue
        listed = str(matched.get("expiry_ymd") or "")
        if not listed:
            continue
        # Only rewrite when listed expiry differs. Do NOT replace a correct spaced
        # OCC with dash-padded chain osiKey — that poisoned monitoring with
        # QQQ--- / QBTS-- quote requests and force-exited no_data (Jul 22 2026).
        if listed == stamped:
            continue
        new_sym = _rewrite_symbol_with_expiry(sym, listed)
        if not new_sym or new_sym == sym:
            continue
        out[sym] = new_sym
        log.warning(
            "OCC_EXPIRY_REPAIR | stage=quote_symbol | underlying=%s | strike=%s | side=%s | "
            "from_expiry=%s | to_expiry=%s | from_symbol=%s | to_symbol=%s | match_reasons=%s",
            ident.underlying,
            ident.strike,
            ident.call_put,
            stamped or "?",
            listed,
            sym,
            new_sym,
            ",".join(matched.get("match_reasons") or []),
        )
    return out


async def repair_continuation_option_symbols_from_chain(
    system: Any,
    continuations: Sequence[Dict[str, Any]],
) -> int:
    """Rewrite profit-optimizer shadow continuation symbols to listed expiry."""
    repaired = 0
    for cont in continuations or []:
        if not isinstance(cont, dict):
            continue
        trade_id = str(cont.get("trade_id") or "").strip()
        option_symbols = list(cont.get("option_symbols") or [])
        if not option_symbols:
            legs = cont.get("legs") if isinstance(cont.get("legs"), list) else []
            option_symbols = [
                str(leg.get("option_symbol") or "").strip()
                for leg in legs
                if isinstance(leg, dict) and str(leg.get("option_symbol") or "").strip()
            ]
        if not option_symbols:
            continue
        sym_map = await repair_quote_symbols_expiry_from_chain(system, option_symbols)
        if not sym_map:
            continue
        new_symbols: List[str] = []
        for sym in option_symbols:
            new_symbols.append(sym_map.get(sym, sym))
        cont["option_symbols"] = new_symbols
        legs = cont.get("legs") if isinstance(cont.get("legs"), list) else []
        for leg in legs:
            if not isinstance(leg, dict):
                continue
            old = str(leg.get("option_symbol") or "").strip()
            if old in sym_map:
                leg["option_symbol"] = sym_map[old]
        if trade_id:
            try:
                from profit_optimizer.trading_hooks import rewrite_continuation_option_symbols

                rewrite_continuation_option_symbols(system, trade_id, sym_map)
            except Exception as err:
                log.warning(
                    "OCC_EXPIRY_REPAIR | stage=continuation_persist | trade_id=%s | error=%s",
                    trade_id,
                    err,
                )
        repaired += 1
        log.warning(
            "OCC_EXPIRY_REPAIR | stage=continuation | trade_id=%s | repairs=%s | symbols=%s",
            trade_id or "?",
            len(sym_map),
            ",".join(f"{k}->{v}" for k, v in sym_map.items()),
        )
    return repaired
