"""Normalize and match option contract identities across OCC / E*TRADE formats.

E*TRADE Market Quote API expects option symbols as:
  ``underlier:year:month:day:optionType:strikePrice``
  e.g. ``SLV:2026:7:20:CALL:51.5``

Internal ledgers and registries typically store spaced OCC:
  ``SLV 260720C00051500``

Quote responses identify contracts via Product fields (symbol/callPut/expiry*/strikePrice)
and optionally ``osiKey`` — not necessarily the OCC string used as the request key.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from modules.position_registry_contract import parse_occ_symbol


@dataclass(frozen=True)
class OptionContractIdentity:
    """Strategy-neutral owned-contract identity."""

    underlying: str
    expiration: str  # YYYY-MM-DD
    strike: float
    call_put: str  # call | put

    @property
    def key(self) -> Tuple[str, str, float, str]:
        return (
            self.underlying,
            self.expiration,
            round(float(self.strike), 6),
            self.call_put,
        )


def _cp_norm(raw: Any) -> str:
    s = str(raw or "").strip().upper()
    if s in ("C", "CALL"):
        return "call"
    if s in ("P", "PUT"):
        return "put"
    s2 = s.lower()
    if s2.startswith("call"):
        return "call"
    if s2.startswith("put"):
        return "put"
    return s2


def identity_from_parts(
    *,
    underlying: Any,
    expiration: Any = None,
    expiry_year: Any = None,
    expiry_month: Any = None,
    expiry_day: Any = None,
    strike: Any = None,
    call_put: Any = None,
) -> Optional[OptionContractIdentity]:
    und = str(underlying or "").strip().upper()
    cp = _cp_norm(call_put)
    try:
        strike_f = float(strike)
    except (TypeError, ValueError):
        return None
    exp = str(expiration or "").strip()
    if not exp and expiry_year is not None and expiry_month is not None and expiry_day is not None:
        try:
            exp = f"{int(expiry_year):04d}-{int(expiry_month):02d}-{int(expiry_day):02d}"
        except (TypeError, ValueError):
            exp = ""
    if not und or not exp or strike_f is None or cp not in ("call", "put"):
        return None
    # Normalize YYYYMMDD → YYYY-MM-DD
    exp_compact = exp.replace("-", "")
    if re.fullmatch(r"\d{8}", exp_compact):
        exp = f"{exp_compact[0:4]}-{exp_compact[4:6]}-{exp_compact[6:8]}"
    return OptionContractIdentity(
        underlying=und,
        expiration=exp,
        strike=float(strike_f),
        call_put=cp,
    )


def identity_from_occ(occ: str) -> Optional[OptionContractIdentity]:
    parsed = parse_occ_symbol(occ)
    if not parsed.get("underlying") or parsed.get("strike") is None or not parsed.get("call_put"):
        return None
    return identity_from_parts(
        underlying=parsed.get("underlying"),
        expiration=parsed.get("expiration"),
        strike=parsed.get("strike"),
        call_put=parsed.get("call_put"),
    )


def identity_from_underscore_symbol(symbol: str) -> Optional[OptionContractIdentity]:
    """Parse profit-optimizer / leg_id forms like ``NOW_20260720_140.0_call``."""
    raw = str(symbol or "").strip()
    m = re.match(
        r"^([A-Za-z0-9.\-]+)_(\d{8})_([0-9]+(?:\.[0-9]+)?)_(call|put)$",
        raw,
        flags=re.IGNORECASE,
    )
    if not m:
        return None
    und, ymd, strike_s, cp = m.groups()
    return identity_from_parts(
        underlying=und,
        expiration=f"{ymd[0:4]}-{ymd[4:6]}-{ymd[6:8]}",
        strike=float(strike_s),
        call_put=cp,
    )


def identity_from_any_option_symbol(symbol: str) -> Optional[OptionContractIdentity]:
    """OCC, colon quote form, OSI, or underscore leg-id."""
    raw = str(symbol or "").strip()
    if not raw:
        return None
    return (
        identity_from_occ(raw)
        or identity_from_etrade_quote_symbol(raw)
        or identity_from_underscore_symbol(raw)
        or identity_from_osi_key(raw)
    )


def identity_from_osi_key(osi_key: str) -> Optional[OptionContractIdentity]:
    """Parse OSI / padded OCC forms such as ``SLV------260720C00051500``."""
    raw = str(osi_key or "").strip().upper()
    if not raw:
        return None
    # Collapse repeated dashes used as underlier padding.
    m = re.match(r"^([A-Z0-9.\-]+?)(?:-+)?(\d{6})([CP])(\d{8})$", raw.replace(" ", ""))
    if not m:
        return identity_from_occ(raw)
    und, yymmdd, cp, strike_milli = m.groups()
    und = und.rstrip("-")
    try:
        strike = int(strike_milli) / 1000.0
        expiration = f"20{yymmdd[0:2]}-{yymmdd[2:4]}-{yymmdd[4:6]}"
    except (TypeError, ValueError):
        return None
    return identity_from_parts(
        underlying=und,
        expiration=expiration,
        strike=strike,
        call_put=cp,
    )


def identity_from_product(product: Dict[str, Any], *, osi_key: str = "") -> Optional[OptionContractIdentity]:
    if not isinstance(product, dict):
        product = {}
    ident = identity_from_parts(
        underlying=product.get("symbol") or product.get("rootSymbol"),
        expiry_year=product.get("expiryYear"),
        expiry_month=product.get("expiryMonth"),
        expiry_day=product.get("expiryDay"),
        strike=product.get("strikePrice") if product.get("strikePrice") is not None else product.get("strike"),
        call_put=product.get("callPut") or product.get("optionType"),
    )
    if ident is not None:
        return ident
    if osi_key:
        return identity_from_osi_key(osi_key)
    # Last resort: product.symbol may itself be OCC / colon quote symbol.
    sym = str(product.get("symbol") or "").strip()
    if ":" in sym:
        return identity_from_etrade_quote_symbol(sym)
    return identity_from_occ(sym)


def identity_from_etrade_quote_symbol(symbol: str) -> Optional[OptionContractIdentity]:
    """Parse ``UNDERLIER:YYYY:M:D:CALL|PUT:strike`` quote-path symbols."""
    parts = str(symbol or "").strip().split(":")
    if len(parts) != 6:
        return None
    und, year, month, day, opt_type, strike = parts
    return identity_from_parts(
        underlying=und,
        expiry_year=year,
        expiry_month=month,
        expiry_day=day,
        strike=strike,
        call_put=opt_type,
    )


def identities_equal(a: Optional[OptionContractIdentity], b: Optional[OptionContractIdentity]) -> bool:
    if a is None or b is None:
        return False
    return a.key == b.key


def identity_to_etrade_quote_symbol(ident: OptionContractIdentity) -> str:
    """Official quote path: underlier:year:month:day:CALL|PUT:strikePrice."""
    y, m, d = ident.expiration.split("-")
    opt = "CALL" if ident.call_put == "call" else "PUT"
    strike_s = f"{float(ident.strike):.10f}".rstrip("0").rstrip(".")
    return f"{ident.underlying}:{int(y)}:{int(m)}:{int(d)}:{opt}:{strike_s}"


def identity_to_osi_key(ident: OptionContractIdentity) -> str:
    """OCC-style OSI key used by some E*TRADE Product payloads."""
    ymd = ident.expiration.replace("-", "")
    yymmdd = ymd[2:] if len(ymd) >= 8 else ymd
    cp = "C" if ident.call_put == "call" else "P"
    strike_milli = int(round(float(ident.strike) * 1000))
    root = str(ident.underlying or "").upper().ljust(6)[:6]
    return f"{root}{yymmdd}{cp}{strike_milli:08d}"


def occ_to_etrade_quote_symbol(occ: str) -> str:
    """Convert spaced/compact OCC (or underscore leg-id) to E*TRADE quote symbol.

    Official quote path format:
      underlier:year:month:day:optionType:strikePrice
    """
    ident = identity_from_any_option_symbol(occ)
    if ident is None:
        # Already colon-form?
        if ":" in str(occ or ""):
            return str(occ).strip()
        return str(occ or "").strip()
    return identity_to_etrade_quote_symbol(ident)


def etrade_quote_symbol_candidates(occ: str) -> List[str]:
    """Primary colon symbol plus formatting alternates for E*TRADE 1002 recovery.

    Observed Jul 21 2026: primary ``COIN:2026:7:21:CALL:195`` returned 1002
    (invalid symbol / product details). Try padded dates, ``.0`` strikes, and OSI.
    """
    ident = identity_from_any_option_symbol(occ)
    if ident is None:
        primary = occ_to_etrade_quote_symbol(occ)
        return [primary] if primary else []

    y, m, d = ident.expiration.split("-")
    yi, mi, di = int(y), int(m), int(d)
    opt = "CALL" if ident.call_put == "call" else "PUT"
    strike = float(ident.strike)
    strike_trim = f"{strike:.10f}".rstrip("0").rstrip(".")
    strike_int = int(strike) if strike.is_integer() else None

    out: List[str] = []

    def _add(sym: str) -> None:
        s = str(sym or "").strip()
        if s and s not in out:
            out.append(s)

    # Primary (docs form): unpadded month/day, trimmed strike.
    _add(f"{ident.underlying}:{yi}:{mi}:{di}:{opt}:{strike_trim}")
    # Zero-padded month/day (some desks reject unpadded).
    _add(f"{ident.underlying}:{yi}:{mi:02d}:{di:02d}:{opt}:{strike_trim}")
    if strike_int is not None:
        _add(f"{ident.underlying}:{yi}:{mi}:{di}:{opt}:{strike_int}")
        _add(f"{ident.underlying}:{yi}:{mi}:{di}:{opt}:{strike_int}.0")
        _add(f"{ident.underlying}:{yi}:{mi:02d}:{di:02d}:{opt}:{strike_int}")
        _add(f"{ident.underlying}:{yi}:{mi:02d}:{di:02d}:{opt}:{strike_int}.0")
    else:
        # Keep one extra decimal for half-strikes (197.50 vs 197.5).
        _add(f"{ident.underlying}:{yi}:{mi}:{di}:{opt}:{strike:.2f}")
        _add(f"{ident.underlying}:{yi}:{mi:02d}:{di:02d}:{opt}:{strike:.2f}")
    # Cash-index roots (SPX/RUT/…) are not traded — do not invent SPXW alternates.
    # Keep only formatting variants for the stamped underlying (equity/ETF OCC).
    # OSI key — last-resort alternate accepted by some quote parsers.
    _add(identity_to_osi_key(ident))
    return out


def occ_from_identity(ident: OptionContractIdentity) -> str:
    """Rebuild spaced OCC from structured identity."""
    ymd = ident.expiration.replace("-", "")
    yymmdd = ymd[2:] if len(ymd) >= 8 else ymd
    cp = "C" if ident.call_put == "call" else "P"
    strike_milli = int(round(float(ident.strike) * 1000))
    return f"{ident.underlying} {yymmdd}{cp}{strike_milli:08d}"


def sanitize_identity_fields(product: Dict[str, Any], *, osi_key: str = "") -> Dict[str, Any]:
    """Safe diagnostic payload — contract identity only, no secrets."""
    return {
        "symbol": product.get("symbol"),
        "securityType": product.get("securityType"),
        "callPut": product.get("callPut"),
        "expiryYear": product.get("expiryYear"),
        "expiryMonth": product.get("expiryMonth"),
        "expiryDay": product.get("expiryDay"),
        "strikePrice": product.get("strikePrice") if product.get("strikePrice") is not None else product.get("strike"),
        "osiKey": osi_key or product.get("osiKey") or "",
    }
