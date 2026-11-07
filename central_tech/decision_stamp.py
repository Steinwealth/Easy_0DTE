"""The single decision stamp (SINGLE_TP_ADMIT, Oct 13 2026).

The ONLY place the admit-time decision keys are assembled. One immutable dict
feeds the Trade Profile evaluation, the decision record, the handoff snapshot
(``po_decision_digest``) and the executor hand-off. A sha256 digest over the
canonical JSON of the keys proves every consumer read the same values.

Consumers must not mutate ``stamp.keys`` (it is a MappingProxyType).
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Dict, Mapping, Optional

# Wide-only extension keys (not in the 25-key template); producer = this module.
EXTENSION_KEYS = ("equity_volume_ratio",)
DERIVED_KEYS = ("call_share", "put_share")
# Picked-contract keys that replace the representative chain values.
# Oct 13 2026: option_l1_imbalance comes from the picked contract's own bid/ask
# sizes on the (cached) chain, so the stamp describes the contract bought.
PICK_KEYS = ("delta", "otm_pct", "option_l1_imbalance")


def _num(raw: Any) -> Optional[float]:
    if raw is None or raw == "" or isinstance(raw, bool):
        return None
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def shares(call_vol: Any, put_vol: Any):
    c, p = _num(call_vol), _num(put_vol)
    if c is None or p is None or not (c + p) > 0:
        return None, None
    return c / (c + p), p / (c + p)


def canonical_digest(keys: Mapping[str, Any]) -> str:
    body = json.dumps({k: keys[k] for k in sorted(keys)}, sort_keys=True,
                      separators=(",", ":"), default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DecisionStamp:
    path: str
    side: str
    slot: Any
    symbol: str
    keys: Mapping[str, Any]
    digest: str
    pick: Mapping[str, Any] = field(default_factory=dict)


def equity_volume_ratio_from_row(row: Mapping[str, Any]) -> Optional[float]:
    if row.get("volume_ratio_missing"):
        return None
    for k in ("equity_volume_ratio", "volume_ratio", "orb_volume_ratio"):
        v = _num(row.get(k))
        if v is not None:
            return v
    return None


def build_decision_stamp(
    path: str,
    side: str,
    slot: Any,
    *,
    symbol: str,
    base_values: Mapping[str, Any],
    row: Mapping[str, Any],
    pick: Optional[Mapping[str, Any]] = None,
    ages: Optional[Mapping[str, Any]] = None,
) -> DecisionStamp:
    """``base_values``: the TP decision keys read once from the hydrated row
    (``trade_profile.decision_value_keys()``). Adds the derived shares, the
    Wide extensions and the picked contract's delta / otm_pct.

    ``ages``: optional source ages at the gate. Families older than
    ``decision_freeze.max_age_by_source()`` are left empty so the digest
    hashes the same keys the profile scores.
    """
    keys: Dict[str, Any] = {k: _num(v) for k, v in dict(base_values).items()}
    cs, ps = shares(keys.get("call_chain_volume"), keys.get("put_chain_volume"))
    keys["call_share"], keys["put_share"] = cs, ps
    keys["equity_volume_ratio"] = equity_volume_ratio_from_row(row)
    pk = dict(pick or {})
    if pk.get("feasible"):
        for k in PICK_KEYS:
            v = _num(pk.get(k))
            if v is not None:
                keys[k] = v
    if ages:
        try:
            from central_tech.decision_freeze import apply_source_age_caps

            keys = apply_source_age_caps(keys, dict(ages))
        except Exception:
            pass
    return DecisionStamp(
        path=str(path), side=str(side).upper(), slot=slot, symbol=str(symbol).upper(),
        keys=MappingProxyType(dict(keys)), digest=canonical_digest(keys),
        pick=MappingProxyType(pk),
    )
