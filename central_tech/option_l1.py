"""Option bid/ask imbalance for the central tech hub.

`option_l1_imbalance` is a long-used Trade Profile key, read from the chain
row already on the signal. No broker fetch. No Priority Optimizer import.
Not a Dynamic Volume rolling key.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

_OPTION_L1_SOURCE = "option_l1_source"

def _f(val: Any) -> Optional[float]:
    if val is None or val == "":
        return None
    try:
        out = float(val)
    except (TypeError, ValueError):
        return None
    if out != out or math.isinf(out):
        return None
    return out


def _i(val: Any) -> Optional[int]:
    f = _f(val)
    if f is None:
        return None
    try:
        return int(f)
    except (TypeError, ValueError):
        return None


def l1_imbalance(bid_size: Any, ask_size: Any) -> Optional[float]:
    b = _f(bid_size)
    a = _f(ask_size)
    if b is None or a is None:
        return None
    denom = b + a
    if denom <= 0:
        return None
    return (b - a) / denom


def _size_pair_from_mapping(src: Any) -> Tuple[Optional[int], Optional[int]]:
    if not isinstance(src, dict):
        return None, None
    bid = _i(
        src.get("option_bid_size")
        if src.get("option_bid_size") is not None
        else src.get("bid_size")
        if src.get("bid_size") is not None
        else src.get("bidSize")
    )
    ask = _i(
        src.get("option_ask_size")
        if src.get("option_ask_size") is not None
        else src.get("ask_size")
        if src.get("ask_size") is not None
        else src.get("askSize")
    )
    return bid, ask


def _size_pair_from_contract(obj: Any) -> Tuple[Optional[int], Optional[int]]:
    if obj is None:
        return None, None
    if isinstance(obj, dict):
        return _size_pair_from_mapping(obj)
    bid = _i(getattr(obj, "bid_size", None) or getattr(obj, "bidSize", None))
    ask = _i(getattr(obj, "ask_size", None) or getattr(obj, "askSize", None))
    return bid, ask


def apply_selected_contract_option_l1(
    row: Dict[str, Any],
    *,
    order_payload: Optional[Dict[str, Any]] = None,
    prefer_owned: bool = False,
) -> Dict[str, Any]:
    """
    Resolve option L1 sizes for the selected / owned OCC contract.

    Priority (executed / prefer_owned): exact owned quote → order_payload /
    liquidity-guard audit → row metadata → selected candidate contract.

    Never falls back to an unrelated ATM representative when contract identity
    is already known. Handoff may use the candidate contract when that is all
    Easy knows.
    """
    out: Dict[str, Any] = {
        "option_bid_size": None,
        "option_ask_size": None,
        "option_l1_imbalance": None,
        _OPTION_L1_SOURCE: None,
    }
    if not isinstance(row, dict):
        return out
    md = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    pl = order_payload if isinstance(order_payload, dict) else {}
    stage = str(row.get("stage") or row.get("po_snapshot_stage") or "")
    owned = bool(prefer_owned) or ("options_executed" in stage) or bool(
        row.get("po_options_owned_strike") or pl.get("strike") or pl.get("listed_exchange_strike")
    )

    owned_sources: List[Tuple[str, Any]] = [
        ("row_option_l1", row),
        ("order_payload", pl),
        ("execution_risk", pl.get("execution_risk") if isinstance(pl.get("execution_risk"), dict) else None),
        (
            "liquidity_guard",
            pl.get("liquidity_guard_audit")
            if isinstance(pl.get("liquidity_guard_audit"), dict)
            else None,
        ),
        ("metadata", md),
        (
            "metadata_execution_risk",
            md.get("execution_risk") if isinstance(md.get("execution_risk"), dict) else None,
        ),
        ("exact_quote", md.get("exact_quote") if isinstance(md.get("exact_quote"), dict) else None),
        (
            "exact_contract_quote",
            md.get("exact_contract_quote")
            if isinstance(md.get("exact_contract_quote"), dict)
            else None,
        ),
        ("frozen_quote", md.get("frozen_quote") if isinstance(md.get("frozen_quote"), dict) else None),
        (
            "selected_contract",
            row.get("selected_contract")
            if isinstance(row.get("selected_contract"), (dict,))
            else md.get("selected_contract"),
        ),
        (
            "chain_hint",
            pl.get("chain_hint") if isinstance(pl.get("chain_hint"), dict) else None,
        ),
    ]

    bid_size: Optional[int] = None
    ask_size: Optional[int] = None
    source_name: Optional[str] = None
    for name, src in owned_sources:
        if src is None:
            continue
        b, a = _size_pair_from_mapping(src) if isinstance(src, dict) else _size_pair_from_contract(src)
        if b is None and a is None:
            continue
        bid_size, ask_size = b, a
        source_name = name
        break

    # Owned-strike chain stamp: sizes only when representative is the owned/selected contract.
    if bid_size is None and ask_size is None:
        rep_src = str(row.get("representative_price_source") or "").strip().lower()
        owned_rep = rep_src in (
            "owned_strike",
            "owned_strike_exit",
            "selected_candidate",
            "selected_contract",
            "exact_quote",
            "exact_contract",
        )
        if owned_rep or (not owned and rep_src):
            # Sizes may already have been stamped beside representative_* by chain enrich.
            b = _i(row.get("option_bid_size"))
            a = _i(row.get("option_ask_size"))
            if b is not None or a is not None:
                bid_size, ask_size = b, a
                source_name = f"representative_{rep_src or 'candidate'}"

    # Explicit guard: do not invent sizes from a non-owned representative quote.
    if (
        bid_size is None
        and ask_size is None
        and owned
        and str(row.get("representative_price_source") or "").strip().lower()
        not in (
            "owned_strike",
            "owned_strike_exit",
            "selected_candidate",
            "selected_contract",
            "exact_quote",
            "exact_contract",
            "",
        )
    ):
        out[_OPTION_L1_SOURCE] = "skipped_non_owned_representative"
        return out

    out["option_bid_size"] = bid_size
    out["option_ask_size"] = ask_size
    out["option_l1_imbalance"] = l1_imbalance(bid_size, ask_size)
    if bid_size is not None or ask_size is not None:
        out[_OPTION_L1_SOURCE] = source_name or ("owned_exact" if owned else "selected_candidate")
        # Executed entry keeps the handoff book. Fill sizes are not a backfill.
        if "options_executed" in stage:
            return out
        if row.get("option_bid_size") is None and bid_size is not None:
            row["option_bid_size"] = bid_size
        if row.get("option_ask_size") is None and ask_size is not None:
            row["option_ask_size"] = ask_size
        if row.get("option_l1_imbalance") is None and out["option_l1_imbalance"] is not None:
            row["option_l1_imbalance"] = out["option_l1_imbalance"]
        row.setdefault(_OPTION_L1_SOURCE, out[_OPTION_L1_SOURCE])
    return out


def derive_option_l1_features(
    row: Dict[str, Any],
    *,
    order_payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Option L1 sizes for the selected/owned OCC — not an unrelated representative."""
    resolved = apply_selected_contract_option_l1(
        row,
        order_payload=order_payload,
    )
    return {
        "option_bid_size": resolved.get("option_bid_size"),
        "option_ask_size": resolved.get("option_ask_size"),
        "option_l1_imbalance": resolved.get("option_l1_imbalance"),
    }



# ---------------------------------------------------------------------------
# Oct 5 fix: option L1 sizes straight from a chain already in hand.
#
# Gate rows (TL / ORB2 / ORB1 admit) carry the chain the Layer C hydrate just
# fetched, but nothing copied the contract book sizes onto the row. The Trade
# Profile key ``option_l1_imbalance`` and the PO handoff/executed snapshots
# then stayed null whenever no owned-contract enrich ran (Oct 5: 7/33 handoffs,
# all ``chain_fetch_source=row_seed``). These helpers read bid/ask size from the
# chain contract — never a broker call.
# ---------------------------------------------------------------------------

_SELECTED_STRIKE_KEYS = (
    "strike",
    "po_options_owned_strike",
    "matched_strike",
    "listed_exchange_strike",
    "requested_strike",
)


def _contract_strike(contract: Any) -> Optional[float]:
    if isinstance(contract, dict):
        raw = contract.get("strike")
        if raw is None:
            raw = contract.get("strike_price")
    else:
        raw = getattr(contract, "strike", None)
        if raw is None:
            raw = getattr(contract, "strike_price", None)
    return _f(raw)


def _side_contracts(chain: Any, option_type: str) -> List[Any]:
    if not isinstance(chain, dict):
        return []
    side = str(option_type or "").strip().lower()
    if side in ("c", "call", "long", "bull", "bullish"):
        key = "calls"
    elif side in ("p", "put", "short", "bear", "bearish"):
        key = "puts"
    else:
        return []
    contracts = chain.get(key)
    return list(contracts) if isinstance(contracts, (list, tuple)) else []


def _find_by_strike(contracts: List[Any], strike: Optional[float], tol: float) -> Any:
    if strike is None:
        return None
    best = None
    best_diff: Optional[float] = None
    for c in contracts:
        cs = _contract_strike(c)
        if cs is None:
            continue
        diff = abs(cs - float(strike))
        if best_diff is None or diff < best_diff:
            best, best_diff = c, diff
    if best is not None and best_diff is not None and best_diff <= tol:
        return best
    return None


def option_l1_from_chain(
    chain: Any,
    *,
    option_type: str,
    selected_strike: Any = None,
    representative_strike: Any = None,
    underlying_price: Any = None,
) -> Dict[str, Any]:
    """Option L1 sizes from a chain dict for the selected (else representative,
    else nearest-spot) contract on ``option_type``'s side.

    Returns ``{}`` when the chain has no sized contract. Never fetches.
    """
    contracts = _side_contracts(chain, option_type)
    if not contracts:
        return {}
    contract = None
    source = None
    sel = _f(selected_strike)
    if sel is not None and sel > 0:
        contract = _find_by_strike(contracts, sel, 0.011)
        if contract is not None:
            source = "chain_selected_strike"
        else:
            # Contract identity is known but not on this chain: do not invent
            # sizes from a different strike.
            return {}
    if contract is None:
        rep = _f(representative_strike)
        if rep is not None and rep > 0:
            contract = _find_by_strike(contracts, rep, 0.011)
            if contract is not None:
                source = "chain_representative_strike"
    if contract is None:
        spot = _f(underlying_price)
        if spot is not None and spot > 0:
            contract = _find_by_strike(contracts, spot, float("inf"))
            if contract is not None:
                source = "chain_atm"
    if contract is None:
        return {}
    bid, ask = _size_pair_from_contract(contract)
    if bid is None and ask is None:
        return {}
    return {
        "option_bid_size": bid,
        "option_ask_size": ask,
        "option_l1_imbalance": l1_imbalance(bid, ask),
        _OPTION_L1_SOURCE: source,
        "option_l1_strike": _contract_strike(contract),
    }


def stamp_option_l1_from_chain(
    row: Dict[str, Any],
    chain: Any,
    *,
    option_type: str,
    underlying_price: Any = None,
) -> int:
    """Fill missing option L1 sizes / imbalance on ``row`` from ``chain``.

    Sizes already on the row (owned / selected contract) are kept; only the
    imbalance is derived from them when it is missing.
    """
    if not isinstance(row, dict):
        return 0
    n = 0
    have_b = _i(row.get("option_bid_size"))
    have_a = _i(row.get("option_ask_size"))
    if have_b is None and have_a is None:
        selected = None
        for key in _SELECTED_STRIKE_KEYS:
            v = _f(row.get(key))
            if v is not None and v > 0:
                selected = v
                break
        got = option_l1_from_chain(
            chain,
            option_type=option_type,
            selected_strike=selected,
            representative_strike=row.get("representative_strike"),
            underlying_price=underlying_price
            if underlying_price is not None
            else (row.get("current_price") or row.get("underlying_price") or row.get("price")),
        )
        if got:
            for key in ("option_bid_size", "option_ask_size"):
                if got.get(key) is not None:
                    row[key] = got[key]
                    n += 1
            if got.get(_OPTION_L1_SOURCE):
                row[_OPTION_L1_SOURCE] = got[_OPTION_L1_SOURCE]
            if got.get("option_l1_strike") is not None:
                row.setdefault("option_l1_strike", got["option_l1_strike"])
    n += stamp_option_l1_imbalance_from_sizes(row)
    return n


def stamp_option_l1_imbalance_from_sizes(row: Dict[str, Any]) -> int:
    """``option_l1_imbalance`` from sizes already on the row, when missing."""
    if not isinstance(row, dict) or row.get("option_l1_imbalance") is not None:
        return 0
    imb = l1_imbalance(row.get("option_bid_size"), row.get("option_ask_size"))
    if imb is None:
        return 0
    row["option_l1_imbalance"] = imb
    return 1
