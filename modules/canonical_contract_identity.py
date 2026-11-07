"""Canonical broker contract identity for Profit Optimizer + exact-quote lookup.

Descriptive labels such as ``NOW_20260720_140.0_call`` may remain as display
aliases. The primary quote key is always the real broker OCC / E*TRADE request
symbol known at entry — never reconstructed later from parts when OCC was known.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from modules.option_contract_identity import (
    identity_from_any_option_symbol,
    identity_from_parts,
    identity_to_etrade_quote_symbol,
    occ_from_identity,
)


def _s(v: Any) -> str:
    return str(v or "").strip()


def _upper(v: Any) -> str:
    return _s(v).upper()


def _opt_side(v: Any) -> str:
    raw = _s(v).lower()
    if raw.startswith("p"):
        return "put"
    if raw.startswith("c"):
        return "call"
    return raw


def _strike(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _display_label(
    *,
    underlier: str,
    expiration: str,
    strike: Optional[float],
    option_type: str,
) -> str:
    exp = _s(expiration).replace("-", "")
    if not underlier or not exp or strike is None or option_type not in ("call", "put"):
        return ""
    strike_s = f"{float(strike):.10f}".rstrip("0").rstrip(".")
    return f"{underlier}_{exp}_{strike_s}_{option_type}"


def _is_descriptive_leg_label(sym: str) -> bool:
    """True for storage labels like ``NOW_20260720_140.0_call`` (not broker OCC)."""
    return bool(
        "_" in sym
        and sym.count("_") >= 3
        and " " not in sym
        and ":" not in sym
    )


def resolve_broker_occ_candidate(
    *candidates: Any,
    underlier: str = "",
    expiration: str = "",
    strike: Any = None,
    option_type: str = "",
) -> str:
    """Prefer a real broker OCC/OSI/colon symbol; fall back to one-time parts build."""
    for raw in candidates:
        sym = _s(raw)
        if not sym or _is_descriptive_leg_label(sym):
            continue
        ident = identity_from_any_option_symbol(sym)
        if ident is not None:
            return occ_from_identity(ident)
        if " " in sym:
            return sym
    # One-time reconstruction only when no broker symbol was provided at entry.
    ident = identity_from_parts(
        underlying=underlier,
        expiration=expiration,
        strike=strike,
        call_put=option_type,
    )
    if ident is None:
        return ""
    return occ_from_identity(ident)


def build_canonical_identity_fields(
    *,
    broker_occ_symbol: str = "",
    osi_symbol: str = "",
    underlier: str = "",
    expiration: str = "",
    option_type: str = "",
    strike: Any = None,
    display_label: str = "",
    broker_quote_request_symbol: str = "",
) -> Dict[str, Any]:
    """Immutable identity bundle stamped at TRACK_START."""
    und = _upper(underlier)
    opt = _opt_side(option_type)
    strike_f = _strike(strike)
    exp = _s(expiration).replace("-", "")
    if len(exp) == 8 and exp.isdigit():
        exp_dash = f"{exp[0:4]}-{exp[4:6]}-{exp[6:8]}"
    else:
        exp_dash = _s(expiration)
        exp = exp_dash.replace("-", "")

    broker_occ = _s(broker_occ_symbol)
    raw_broker = broker_occ
    if not broker_occ:
        broker_occ = resolve_broker_occ_candidate(
            underlier=und,
            expiration=exp_dash or exp,
            strike=strike_f,
            option_type=opt,
        )
        raw_broker = broker_occ

    ident = identity_from_any_option_symbol(broker_occ) or identity_from_parts(
        underlying=und,
        expiration=exp_dash or exp,
        strike=strike_f,
        call_put=opt,
    )
    normalized = occ_from_identity(ident) if ident is not None else broker_occ
    # Keep the broker-stamped OCC string when provided; normalized is the alias key.
    if raw_broker and identity_from_any_option_symbol(raw_broker) is not None and not _is_descriptive_leg_label(raw_broker):
        broker_occ = raw_broker
    else:
        broker_occ = normalized or broker_occ
    quote_req = _s(broker_quote_request_symbol)
    # Prefer Market Quote colon wire over dash-padded OSI (SMH---… / AAPL--…).
    if quote_req and ":" not in quote_req:
        ident_from_req = identity_from_any_option_symbol(quote_req)
        if ident_from_req is not None:
            quote_req = identity_to_etrade_quote_symbol(ident_from_req)
        elif ident is not None:
            quote_req = identity_to_etrade_quote_symbol(ident)
    if not quote_req and ident is not None:
        quote_req = identity_to_etrade_quote_symbol(ident)
    elif not quote_req and broker_occ:
        from modules.option_contract_identity import occ_to_etrade_quote_symbol

        quote_req = occ_to_etrade_quote_symbol(broker_occ)

    osi = _s(osi_symbol)
    label = _s(display_label) or _display_label(
        underlier=und or (ident.underlying if ident else ""),
        expiration=exp or (ident.expiration.replace("-", "") if ident else ""),
        strike=strike_f if strike_f is not None else (ident.strike if ident else None),
        option_type=opt or (ident.call_put if ident else ""),
    )

    return {
        "broker_occ_symbol": broker_occ or normalized,
        "normalized_occ_symbol": normalized or broker_occ,
        "osi_symbol": osi,
        "underlier": und or (ident.underlying if ident else ""),
        "expiration": exp or (ident.expiration.replace("-", "") if ident else ""),
        "option_type": opt or (ident.call_put if ident else ""),
        "strike": strike_f if strike_f is not None else (float(ident.strike) if ident else None),
        "broker_quote_request_symbol": quote_req,
        "display_label": label,
        # Back-compat: option_symbol is the canonical broker OCC (not display label).
        "option_symbol": broker_occ or normalized or label,
    }


def quote_lookup_aliases(identity: Dict[str, Any]) -> List[str]:
    """Ordered alias keys for exact_quotes_by_contract lookup."""
    out: List[str] = []
    for key in (
        "broker_occ_symbol",
        "normalized_occ_symbol",
        "broker_quote_request_symbol",
        "osi_symbol",
        "option_symbol",
        "display_label",
    ):
        val = _s(identity.get(key))
        if val and val not in out:
            out.append(val)
    legs = identity.get("legs") if isinstance(identity.get("legs"), list) else []
    for leg in legs:
        if not isinstance(leg, dict):
            continue
        for key in (
            "broker_occ_symbol",
            "normalized_occ_symbol",
            "broker_quote_request_symbol",
            "option_symbol",
            "osi_symbol",
        ):
            val = _s(leg.get(key))
            if val and val not in out:
                out.append(val)
    return out


def lookup_quote_by_aliases(
    quotes_by_contract: Dict[str, Any],
    identity_or_aliases: Any,
) -> Optional[Dict[str, Any]]:
    """Return the first matching quote dict for any alias key."""
    if not isinstance(quotes_by_contract, dict) or not quotes_by_contract:
        return None
    if isinstance(identity_or_aliases, dict):
        aliases = quote_lookup_aliases(identity_or_aliases)
    elif isinstance(identity_or_aliases, (list, tuple)):
        aliases = [_s(a) for a in identity_or_aliases if _s(a)]
    else:
        aliases = [_s(identity_or_aliases)] if _s(identity_or_aliases) else []
    for alias in aliases:
        hit = quotes_by_contract.get(alias)
        if isinstance(hit, dict):
            return hit
    return None


def register_quote_aliases(
    quotes_by_contract: Dict[str, Any],
    quote: Dict[str, Any],
    *,
    primary_key: str = "",
    identity: Optional[Dict[str, Any]] = None,
    extra_aliases: Optional[Sequence[str]] = None,
) -> Dict[str, Any]:
    """Index one quote dict under canonical OCC + normalized + OSI + request aliases."""
    if not isinstance(quotes_by_contract, dict) or not isinstance(quote, dict):
        return quotes_by_contract
    aliases: List[str] = []
    if primary_key:
        aliases.append(_s(primary_key))
    if identity:
        aliases.extend(quote_lookup_aliases(identity))
    for key in (
        "option_symbol",
        "broker_occ_symbol",
        "normalized_occ_symbol",
        "osi_symbol",
        "osiKey",
        "broker_quote_request_symbol",
        "request_symbol",
    ):
        val = _s(quote.get(key))
        if val:
            aliases.append(val)
    if extra_aliases:
        aliases.extend(_s(a) for a in extra_aliases if _s(a))
    # Also derive OCC / colon from any alias we already have.
    seed = next((a for a in aliases if a), "")
    if seed:
        ident = identity_from_any_option_symbol(seed)
        if ident is not None:
            aliases.append(occ_from_identity(ident))
            aliases.append(identity_to_etrade_quote_symbol(ident))
    seen = set()
    for alias in aliases:
        if not alias or alias in seen:
            continue
        seen.add(alias)
        quotes_by_contract[alias] = quote
    return quotes_by_contract
