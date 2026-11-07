"""Trade Profile admit. One accept/reject for each path and side.

Default profile: 10.5 (flat Trade Profiles, docs/0.TradeProfiles.md sections 1-2;
easy0DTE/configs/trade_profile_10.5.json; see the 10.5 block at the end of this
file). Each path/side is one AND list of inclusive per-key ranges over that
side's 27-key template (plus at most one ``not (A and B)`` line); a missing
value passes its line, except the warm-up keys a cell lists in
``missing_reject_keys`` (ORB1 and TL0, CALL and PUT: those of atr,
price_volume_efficiency_3m, dir_pct_change_5m, distance_from_vwap_atr, rsi
that the cell has a range line for), where a missing value rejects with
``tp105_{slug}_missing_{key}`` (Eric, Oct 5 2026).

TRADE_PROFILE_VERSION=10.2nh (or 10.2 / 10.3) runs the 10.2/10.3 profile that
ran live through Oct 5 (10.2 without its 2d/10d history rules, greek rules on):
the 10/1 Superior tables below plus the edits in
easy0DTE/configs/trade_profile_10.2_nohist.json; see the 10.2/10.3 block.
TRADE_PROFILE_VERSION=10.1 runs the 10/1 Superior profile described next,
unchanged.

The 10/1 Superior TP Current profile:
The stamped 9/28 profile runs first. The 9/30 overlay then rejects a pass
that hits a loss guard, or allows a reject that hits an add-back lane. The
mined max-net rules then add or remove that decision. Chain, ATR/RSI, and
forward-loss overlays run only after that admit.

A base key the profile expects starts as a reject when it is missing, and
the reason names the key. A 9/30 allow or a mined allow can still admit that
trade when its own values are present. RSI, directional % change, and option
L1 are sparse on the profiles that are often without them. A missing overlay
or mined comparison does not fire.

The gate reads keys already on the signal. It does not fetch quotes or recompute
chain volume or dir% change.
"""

from __future__ import annotations

import csv
import copy
import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger(__name__)

# Guide order. One list. Profiles name these keys. They do not define another order.
TRADE_PROFILE_KEYS: Tuple[str, ...] = (
    "call_chain_volume",
    "put_chain_volume",
    "call_chain_open_interest",
    "put_chain_open_interest",
    "break_distance_pct",
    "price_volume_efficiency_3m",
    "atr",
    "distance_from_vwap_atr",
    "rsi",
    "dir_pct_change_5m",
    "spy_change_pct",
    "underlying_l1_imbalance",
    "option_l1_imbalance",
    "chain_sanity_best_same_side_spread_pct",
    "delta",
    "otm_pct",
    "iv",
    "theta",
    "gamma",
    "vega",
)
PROFILE_KEYS = TRADE_PROFILE_KEYS
CANONICAL_PROFILE_KEYS = TRADE_PROFILE_KEYS

# Final Trade Profile Template (docs/0.TradeProfiles.md section 1): 27 keys per
# side, in this grouping and order. CALL and PUT share the names but differ in
# order inside groups. The 10.5 profiles are written on these lists; the
# 10.2/10.3 and 10.1 tables above keep the legacy 20-key order.
TRADE_PROFILE_GROUPS_CALL: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("Chain Participation", (
        "call_chain_volume",
        "put_chain_volume",
        "total_chain_vol_oi",
        "call_chain_vol_oi",
        "put_chain_vol_oi",
        "put_chain_open_interest",
        "call_chain_open_interest",
        "call_share",
        "put_share",
    )),
    ("Option / Market Quality", (
        "theta",
        "chain_sanity_best_same_side_spread_pct",
        "atr",
        "vega",
    )),
    ("Trade / Break Structure", (
        "price_volume_efficiency_3m",
        "break_distance_pct",
        "otm_pct",
        "iv",
        "gamma",
    )),
    ("Directional / Market Context", (
        "spy_change_pct",
        "dir_pct_change_5m",
        "distance_from_vwap_atr",
        "rsi",
        "bollinger_position",
        "bollinger_width",
    )),
    ("Order-Book / Contract Context", (
        "underlying_l1_imbalance",
        "option_l1_imbalance",
        "delta",
    )),
)
TRADE_PROFILE_GROUPS_PUT: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("Chain Participation", (
        "call_chain_volume",
        "put_chain_volume",
        "total_chain_vol_oi",
        "put_chain_vol_oi",
        "call_chain_vol_oi",
        "put_chain_open_interest",
        "call_chain_open_interest",
        "put_share",
        "call_share",
    )),
    ("Option / Market Quality", (
        "theta",
        "atr",
        "chain_sanity_best_same_side_spread_pct",
        "vega",
    )),
    ("Trade / Break Structure", (
        "price_volume_efficiency_3m",
        "break_distance_pct",
        "gamma",
        "otm_pct",
        "iv",
    )),
    ("Directional / Market Context", (
        "dir_pct_change_5m",
        "spy_change_pct",
        "distance_from_vwap_atr",
        "rsi",
        "bollinger_position",
        "bollinger_width",
    )),
    ("Order-Book / Contract Context", (
        "option_l1_imbalance",
        "underlying_l1_imbalance",
        "delta",
    )),
)
TRADE_PROFILE_KEYS_CALL: Tuple[str, ...] = tuple(
    key for _group, keys in TRADE_PROFILE_GROUPS_CALL for key in keys
)
TRADE_PROFILE_KEYS_PUT: Tuple[str, ...] = tuple(
    key for _group, keys in TRADE_PROFILE_GROUPS_PUT for key in keys
)
# Oct 7 2026 decision freeze: the three 10.5 vol/OI ratio keys travel with the
# canonical keys so the handoff row and the executed relock carry the exact
# values the gate scored (they were dropped before; see
# RUNTIME_DECISION_DATA_REVIEW_2026-10-07).
TP_RATIO_KEYS: Tuple[str, ...] = (
    "total_chain_vol_oi",
    "call_chain_vol_oi",
    "put_chain_vol_oi",
)
PROFILE_ROW_KEYS: Tuple[str, ...] = (
    TRADE_PROFILE_KEYS + TP_RATIO_KEYS + ("bollinger_position", "bollinger_width", "trendline_slot")
)
_KEY_ORDER = {key: index for index, key in enumerate(TRADE_PROFILE_KEYS)}

Rule = Tuple[str, float]  # ("MIN"|"MAX", level)

_GREEKS = frozenset({"delta", "otm_pct", "iv", "theta", "gamma", "vega"})


def _opt(*keys: str) -> frozenset:
    return frozenset(keys) | _GREEKS


# Missing does not reject. Confirmed on the 14–25 Sep handoffs: these keys
# are under 90% on that profile, or they are PASS on every mined profile
# (directional change, option L1, and the six greeks). Same-side open
# interest is expected on every profile.
_OPTIONAL_MISSING: Dict[str, Dict[str, frozenset]] = {
    "ORB1": {
        "CALL": _opt("break_distance_pct", "dir_pct_change_5m", "rsi", "option_l1_imbalance"),
        "PUT": _opt("break_distance_pct", "dir_pct_change_5m", "option_l1_imbalance"),
    },
    "TL0": {
        "CALL": _opt("dir_pct_change_5m", "rsi", "option_l1_imbalance"),
        "PUT": _opt("dir_pct_change_5m", "rsi", "option_l1_imbalance"),
    },
    "ORB2": {
        "CALL": _opt(
            "dir_pct_change_5m",
            "chain_sanity_best_same_side_spread_pct",
            "option_l1_imbalance",
        ),
        "PUT": _opt(
            "dir_pct_change_5m",
            "chain_sanity_best_same_side_spread_pct",
            "option_l1_imbalance",
        ),
    },
    "TL1": {
        "CALL": _opt(
            "dir_pct_change_5m",
            "chain_sanity_best_same_side_spread_pct",
            "option_l1_imbalance",
        ),
        "PUT": _opt(
            "dir_pct_change_5m",
            "chain_sanity_best_same_side_spread_pct",
            "option_l1_imbalance",
        ),
    },
    "TL2+": {
        "CALL": _opt(
            "dir_pct_change_5m",
            "chain_sanity_best_same_side_spread_pct",
            "option_l1_imbalance",
        ),
        "PUT": _opt(
            "dir_pct_change_5m",
            "chain_sanity_best_same_side_spread_pct",
            "option_l1_imbalance",
        ),
    },
}


# Active cuts only, on the canonical key. The other side's volume and open
# interest are PASS here. An overlay names that side when it uses it.
_PROFILES: Dict[str, Dict[str, Dict[str, Rule]]] = {
    "ORB1": {
        "CALL": {
            "call_chain_volume": ("MIN", 21146.0),
            "distance_from_vwap_atr": ("MAX", 2.089),
            "rsi": ("MIN", 54.686),
        },
        "PUT": {
            "put_chain_volume": ("MIN", 5720.0),
            "put_chain_open_interest": ("MAX", 56511.0),
            "spy_change_pct": ("MIN", -0.344),
        },
    },
    "TL0": {
        "CALL": {
            "call_chain_volume": ("MIN", 19595.0),
            "price_volume_efficiency_3m": ("MIN", 0.013),
            "atr": ("MIN", 0.868),
            "distance_from_vwap_atr": ("MAX", 1.631),
            "rsi": ("MAX", 52.635),
            "underlying_l1_imbalance": ("MIN", -0.870),
            "chain_sanity_best_same_side_spread_pct": ("MIN", 0.732),
        },
        "PUT": {
            "put_chain_volume": ("MAX", 518921.0),
            "put_chain_open_interest": ("MIN", 60692.0),
            "price_volume_efficiency_3m": ("MIN", 0.0399),
            "atr": ("MAX", 1.048),
            "rsi": ("MIN", 35.947),
            "chain_sanity_best_same_side_spread_pct": ("MAX", 6.290),
        },
    },
    "ORB2": {
        "CALL": {
            "call_chain_volume": ("MIN", 5632.0),
            "call_chain_open_interest": ("MAX", 300748.0),
            "break_distance_pct": ("MIN", 0.000909),
            "price_volume_efficiency_3m": ("MAX", 0.436),
            "atr": ("MIN", 0.0275),
            "distance_from_vwap_atr": ("MAX", 16.047),
            "rsi": ("MIN", 67.139),
            "underlying_l1_imbalance": ("MAX", 0.905),
            "chain_sanity_best_same_side_spread_pct": ("MIN", 1.307),
        },
        "PUT": {
            "put_chain_volume": ("MIN", 20042.0),
            "put_chain_open_interest": ("MAX", 115744.0),
            "price_volume_efficiency_3m": ("MAX", 0.435),
            "atr": ("MAX", 0.321),
            "distance_from_vwap_atr": ("MIN", -18.445),
            "rsi": ("MAX", 19.505),
        },
    },
    "TL1": {
        "CALL": {
            "call_chain_volume": ("MIN", 6650.0),
            "call_chain_open_interest": ("MAX", 57442.0),
            "break_distance_pct": ("MIN", 0.000897),
            "price_volume_efficiency_3m": ("MAX", 0.665),
            "atr": ("MIN", 0.0382),
            "distance_from_vwap_atr": ("MIN", -4.174),
            "rsi": ("MIN", 54.286),
            "underlying_l1_imbalance": ("MAX", 0.321),
        },
        "PUT": {
            "put_chain_volume": ("MIN", 6792.0),
            "put_chain_open_interest": ("MAX", 88233.0),
            "break_distance_pct": ("MIN", 0.000945),
            "atr": ("MAX", 0.0675),
            "distance_from_vwap_atr": ("MAX", 1.072),
            "chain_sanity_best_same_side_spread_pct": ("MIN", 23.157),
        },
    },
    "TL2+": {
        "CALL": {
            "call_chain_volume": ("MIN", 120711.0),
            "break_distance_pct": ("MAX", 0.007473),
            "price_volume_efficiency_3m": ("MAX", 0.582),
            "atr": ("MIN", 0.0675),
            "distance_from_vwap_atr": ("MIN", -23.603),
            "underlying_l1_imbalance": ("MAX", 0.473),
        },
        "PUT": {
            "put_chain_volume": ("MIN", 10074.0),
            "break_distance_pct": ("MAX", 0.011794),
            "distance_from_vwap_atr": ("MAX", 10.369),
            "underlying_l1_imbalance": ("MAX", -0.771),
        },
    },
}

def apply_profile_decision(row: Dict[str, Any]) -> int:
    """Put the 18 scored profile keys back on a handoff row.

    The handoff is rebuilt after the accept or reject. A number the profile
    scored stays that number. A key the profile did not have can still be
    filled from a source at or before the freeze.
    """
    if not isinstance(row, dict):
        return 0
    record = row.get("po_profile_decision")
    if not isinstance(record, dict):
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        record = meta.get("po_profile_decision") if isinstance(meta, dict) else None
    if not isinstance(record, dict):
        setup = row.get("setup_payload") if isinstance(row.get("setup_payload"), dict) else {}
        record = setup.get("po_profile_decision") if isinstance(setup, dict) else None
    keys = record.get("keys") if isinstance(record, dict) else None
    if not isinstance(keys, dict):
        return 0
    n = 0
    for key, value in keys.items():
        if value is None:
            continue
        if row.get(key) != value:
            row[key] = value
            n += 1
    row["po_profile_decision_applied"] = True
    return n


def copy_profile_fields(src: Dict[str, Any], dest: Dict[str, Any]) -> int:
    """Copy decision keys onto the signal. Does not overwrite a value already set."""
    if not isinstance(src, dict) or not isinstance(dest, dict):
        return 0
    n = 0
    for key in PROFILE_ROW_KEYS:
        if dest.get(key) is not None or src.get(key) is None:
            continue
        dest[key] = src.get(key)
        n += 1
    return n


def _profile_value(src: Dict[str, Any], key: str) -> Any:
    val = src.get(key)
    if key == "iv" and val in (None, ""):
        val = src.get("implied_volatility")
    return val


def stamp_flight_profile_keys(dest: Dict[str, Any], *sources: Any) -> int:
    """Put Trade Profile keys on a flight-recorder entry from decision sources.

    The scored profile record wins, then the first later source that has a
    value. A key nobody has stays absent. This does not recompute a key.
    """
    if not isinstance(dest, dict):
        return 0
    scored: List[Dict[str, Any]] = []
    rest: List[Dict[str, Any]] = []
    for src in sources:
        if not isinstance(src, dict):
            continue
        record = src.get("po_profile_decision")
        if isinstance(record, dict) and isinstance(record.get("keys"), dict):
            scored.append(record["keys"])
        rest.append(src)
    bags = scored + rest
    n = 0
    for key in PROFILE_ROW_KEYS:
        chosen = None
        found = False
        for src in bags:
            val = _profile_value(src, key)
            if val in (None, ""):
                continue
            chosen = val
            found = True
            break
        if not found or dest.get(key) == chosen:
            continue
        dest[key] = chosen
        n += 1
    if dest.get("po_profile_decision") is None:
        for src in rest:
            record = src.get("po_profile_decision")
            if isinstance(record, dict):
                dest["po_profile_decision"] = record
                n += 1
                break
    return n


def field_name(concept: str, side: str) -> str:
    """Map a legacy vol/oi name to the side's canonical key. Canonical names pass through."""
    side_u = str(side or "").strip().upper()
    if concept == "vol":
        return "call_chain_volume" if side_u == "CALL" else "put_chain_volume"
    if concept == "oi":
        return "call_chain_open_interest" if side_u == "CALL" else "put_chain_open_interest"
    return concept


def _opposite_participation(side: str) -> frozenset:
    """The other side's volume and open interest. PASS on the base profile."""
    if str(side or "").strip().upper() == "CALL":
        return frozenset(("put_chain_volume", "put_chain_open_interest"))
    return frozenset(("call_chain_volume", "call_chain_open_interest"))


def _slot_number(raw: Any) -> Optional[int]:
    if isinstance(raw, bool) or raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        if isinstance(raw, float) and raw != raw:
            return None
        return int(raw)
    slot = str(raw).strip().lower().replace(" ", "").replace("_", "")
    if not slot:
        return None
    if slot.startswith("trendline") and slot[9:].isdigit():
        return int(slot[9:])
    if slot.startswith("tl") and slot[2:].isdigit():
        return int(slot[2:])
    if slot.isdigit():
        return int(slot)
    return None


def profile_name(path: str, slot: Any = None) -> Optional[str]:
    """ORB1, ORB2, TL0, TL1, or TL2+. None when the path or slot cannot be placed."""
    key = str(path or "").strip().lower()
    if key in ("orb_0dte", "orb", "orb1"):
        return "ORB1"
    if key in ("breakout_0dte", "breakout", "orb2", "bo"):
        return "ORB2"
    if key in ("trendline_0dte", "trendline", "tl"):
        n = _slot_number(slot)
        if n is None:
            return None
        if n <= 0:
            return "TL0"
        if n == 1:
            return "TL1"
        return "TL2+"
    return None


def canonical_trendline_slot(slot: Any) -> Optional[str]:
    """Return the Trendline profile bucket without losing numeric slot data."""
    number = _slot_number(slot)
    if number is None:
        return None
    if number <= 0:
        return "trendline0"
    if number == 1:
        return "trendline1"
    return "trendline2"


# Profile versions. TRADE_PROFILE_VERSION picks the one that decides; the
# default is the 10.5 flat profile. 10.16 and 10.17 are historical continuation
# profiles; 10.23 is the current local flat continuation profile.
# reserved for research replay. TRADE_PROFILE_VERSION=10.2nh (10.2 / 10.3)
# is the instant rollback to the 10.2/10.3 profile that ran through Oct 5;
# TRADE_PROFILE_VERSION=10.1 rolls back to 10/1 Superior.
VERSION_10_1 = "10.1"
VERSION_10_2 = "10.2nh"
VERSION_10_5 = "10.5"
VERSION_10_16 = "10.16"
VERSION_10_17 = "10.17"
VERSION_10_18 = "10.18"
VERSION_10_20 = "10.20"
VERSION_10_21 = "10.21"
VERSION_10_22 = "10.22"
VERSION_10_23 = "10.23"
DEFAULT_PROFILE_VERSION = VERSION_10_5
TRADE_PROFILE_VERSION_ENV = "TRADE_PROFILE_VERSION"
TRADE_PROFILE_JSON_ENV = "TRADE_PROFILE_JSON"
TRADE_PROFILE_105_JSON_ENV = "TRADE_PROFILE_105_JSON"
TRADE_PROFILE_1016_JSON_ENV = "TRADE_PROFILE_1016_JSON"
TRADE_PROFILE_1017_JSON_ENV = "TRADE_PROFILE_1017_JSON"
TRADE_PROFILE_1018_JSON_ENV = "TRADE_PROFILE_1018_JSON"
TRADE_PROFILE_1020_JSON_ENV = "TRADE_PROFILE_1020_JSON"
TRADE_PROFILE_1021_JSON_ENV = "TRADE_PROFILE_1021_JSON"
TRADE_PROFILE_1022_JSON_ENV = "TRADE_PROFILE_1022_JSON"
TRADE_PROFILE_1023_JSON_ENV = "TRADE_PROFILE_1023_JSON"
_VERSION_ALIASES = {
    "10.5": VERSION_10_5,
    "10.5flat": VERSION_10_5,
    "10.5_flat": VERSION_10_5,
    "tp105": VERSION_10_5,
    "10.16": VERSION_10_16,
    "10.16flat": VERSION_10_16,
    "10.16_flat": VERSION_10_16,
    "tp1016": VERSION_10_16,
    "10.17": VERSION_10_17,
    "10.17flat": VERSION_10_17,
    "10.17_flat": VERSION_10_17,
    "tp1017": VERSION_10_17,
    "10.18": VERSION_10_18,
    "10.18flat": VERSION_10_18,
    "10.18_flat": VERSION_10_18,
    "tp1018": VERSION_10_18,
    "10.20": VERSION_10_20,
    "tp1020": VERSION_10_20,
    "10.21": VERSION_10_21,
    "tp1021": VERSION_10_21,
    "10.22": VERSION_10_22,
    "tp1022": VERSION_10_22,
    "10.23": VERSION_10_23,
    "10.23flat": VERSION_10_23,
    "10.23_flat": VERSION_10_23,
    "tp1023": VERSION_10_23,
    "10.1": VERSION_10_1,
    "10.1superior": VERSION_10_1,
    "superior": VERSION_10_1,
    "legacy": VERSION_10_1,
    "10.2nh": VERSION_10_2,
    "10.2": VERSION_10_2,
    "10.3": VERSION_10_2,
    "10.2/10.3": VERSION_10_2,
    "10.2_nohist": VERSION_10_2,
    "nohist": VERSION_10_2,
    # SINGLE_TP_ADMIT (10/13): TRADE_PROFILE_VERSION=wide selects TP Wide in
    # easy0DTE/modules/single_tp_admit.py; this legacy module keeps 10.5.
    "wide": VERSION_10_5,
}


@dataclass
class TradeProfileDecision:
    admitted: bool
    profile: str
    side: str
    reason: str
    failed: List[Dict[str, Any]] = field(default_factory=list)
    version: str = VERSION_10_1
    # Guide-format reason (base_MIN_{key}, tp930_{key}, ...). Same as reason
    # for tp101_superior_* and tp102v2_*. Set by the 10.2/10.3 gate.
    ref_reason: str = ""
    stage: str = ""
    values: Dict[str, Any] = field(default_factory=dict)
    missing: List[str] = field(default_factory=list)

    def fields(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "profile": self.profile,
            "side": self.side,
            "failed": self.failed,
            "profile_version": self.version,
        }
        if self.ref_reason:
            out["ref_reason"] = self.ref_reason
        if self.stage:
            out["stage"] = self.stage
        return out


def active_rules(profile: str, side: str) -> Dict[str, Rule]:
    return dict(_PROFILES.get(profile, {}).get(str(side or "").strip().upper(), {}))


def optional_when_missing(profile: str, side: str) -> frozenset:
    return _OPTIONAL_MISSING.get(profile, {}).get(str(side or "").strip().upper(), _GREEKS)


def _is_missing(raw: Any) -> bool:
    if raw is None or raw == "":
        return True
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return True
    return value != value


# 9/30 overlay. A guard is any-clause and runs only after the stamped profile
# passes. A lane is all-clauses and runs only after the stamped profile rejects.
# A missing value does not satisfy a clause. Comparisons are exact.
_Clause = Tuple[str, str, float]
_REJECT_GUARDS: Dict[Tuple[str, str], Tuple[_Clause, ...]] = {
    ("TL1", "CALL"): (
        ("rsi", "GE", 80.26149714),
        ("dir_pct_change_5m", "GT", 0.138896844),
        ("chain_sanity_best_same_side_spread_pct", "GT", 22.22222222),
    ),
    ("ORB2", "CALL"): (
        ("distance_from_vwap_atr", "LE", 5.090928841),
        ("dir_pct_change_5m", "GT", 0.06562268708),
    ),
    ("ORB1", "PUT"): (
        ("put_chain_open_interest", "LT", 7464.0),
        ("break_distance_pct", "LT", 0.00144571346),
        ("atr", "LT", 0.2105990601),
    ),
}
_ALLOW_LANES: Dict[Tuple[str, str], Tuple[Tuple[str, Tuple[_Clause, ...]], ...]] = {
    ("TL2+", "PUT"): (
        (
            "spread_volume",
            (
                ("put_chain_volume", "GE", 848450.9),
                ("chain_sanity_best_same_side_spread_pct", "LE", 1.7212931994),
            ),
        ),
    ),
    ("TL0", "PUT"): (
        (
            "l1_rsi",
            (
                ("rsi", "GE", 61.437022418),
                ("underlying_l1_imbalance", "LE", -0.81864211739),
            ),
        ),
        (
            "dir_rsi",
            (
                ("rsi", "LE", 23.9897590265),
                ("dir_pct_change_5m", "LE", -0.48304131025),
            ),
        ),
        (
            "dir_flush",
            (("dir_pct_change_5m", "LE", -0.65146965445),),
        ),
    ),
    ("ORB2", "PUT"): (
        (
            "atr_spread",
            (
                ("atr", "GE", 0.74719237465),
                ("chain_sanity_best_same_side_spread_pct", "LE", 0.93501636281),
            ),
        ),
    ),
    ("TL0", "CALL"): (
        ("low_oi", (("call_chain_open_interest", "LE", 4136.88),)),
    ),
}

# 10/1 Superior overlays. Any clause rejects. A missing required key rejects.
# A missing sparse key does not fire. The TL2+ PUT ATR cut is the joined
# handoff value; the guide's printed 0.01024 sits just below that trade.
_SUPERIOR_GUARDS: Tuple[Tuple[str, str, Tuple[_Clause, ...]], ...] = (
    ("chain", "TL2+ CALL", (
        ("call_chain_volume", "LE", 62454.0),
        ("put_chain_volume", "LE", 45908.0),
    )),
    ("chain", "ORB2 CALL", (
        ("call_chain_volume", "LE", 5330.0),
        ("put_chain_volume", "LE", 3146.0),
    )),
    ("chain", "TL1 CALL", (
        ("call_chain_open_interest", "GE", 73296.0),
        ("put_chain_open_interest", "GE", 85225.0),
    )),
    ("chain", "TL0 CALL", (
        ("call_chain_volume", "LE", 458.0),
        ("put_chain_volume", "LE", 248.0),
    )),
    ("chain", "ORB1 CALL", (
        ("put_chain_volume", "LE", 2188.0),
        ("put_chain_open_interest", "GE", 142285.0),
    )),
    ("chain", "TL1 PUT", (
        ("call_chain_volume", "LE", 13436.0),
        ("put_chain_volume", "LE", 6432.0),
    )),
    ("chain", "ORB2 PUT", (
        ("call_chain_volume", "LE", 22754.0),
        ("put_chain_volume", "LE", 18062.0),
    )),
    ("chain", "TL2+ PUT", (
        ("call_chain_open_interest", "LE", 21991.0),
        ("put_chain_open_interest", "LE", 11463.0),
    )),
    ("chain", "TL0 PUT", (
        ("call_chain_volume", "LE", 2219.0),
        ("put_chain_open_interest", "LE", 5750.0),
    )),
    ("chain", "ORB1 PUT", (
        ("call_chain_volume", "LE", 2525.0),
        ("put_chain_volume", "LE", 2258.0),
    )),
    ("atr_rsi", "TL1 CALL", (
        ("atr", "LE", 0.03774),
        ("rsi", "GE", 79.86),
    )),
    ("atr_rsi", "ORB2 CALL", (
        ("atr", "LE", 0.02741),
        ("rsi", "LE", 32.33),
    )),
    ("atr_rsi", "TL0 CALL", (
        ("atr", "LE", 0.05341),
        ("rsi", "LE", 30.87),
    )),
    ("atr_rsi", "TL2+ CALL", (("atr", "LE", 0.06636),)),
    ("atr_rsi", "ORB1 CALL", (("rsi", "LE", 52.71),)),
    ("atr_rsi", "TL1 PUT", (
        ("atr", "GE", 0.29914),
        ("rsi", "LE", 11.53),
    )),
    ("atr_rsi", "ORB2 PUT", (
        ("atr", "LE", 0.02564),
        ("rsi", "GE", 37.98),
    )),
    ("atr_rsi", "TL2+ PUT", (
        ("atr", "LE", 0.01024285714),
        ("rsi", "GE", 62.93),
    )),
    ("atr_rsi", "ORB1 PUT", (
        ("atr", "LE", 0.20725),
        ("rsi", "GE", 46.01),
    )),
    ("atr_rsi", "TL0 PUT", (
        ("atr", "LE", 0.04433),
        ("atr", "GE", 5.53833),
    )),
    ("forward_loss", "ORB1 PUT", (
        ("spy_change_pct", "LE", -0.34094),
        ("otm_pct", "LE", 0.00110),
    )),
    ("forward_loss", "ORB2 CALL", (("delta", "GE", 0.46048),)),
    ("forward_loss", "TL0 PUT", (("theta", "GE", -0.48098),)),
    ("forward_loss", "TL2+ CALL", (("gamma", "GE", 0.27798),)),
)

_MinedRule = Tuple[str, str, str, str, float]
_OP_FROM_CSV = {">=": "GE", "<=": "LE", ">": "GT", "<": "LT"}


_MINED_RULES_PATH = Path(__file__).resolve().parent / "greedy_mined_profile_rules.csv"


def _load_mined_rules() -> Tuple[_MinedRule, ...]:
    path = _MINED_RULES_PATH
    rules: List[_MinedRule] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            action = str(row.get("action") or "").strip().lower()
            profile = str(row.get("profile") or "").strip()
            key = str(row.get("key") or "").strip()
            op = _OP_FROM_CSV.get(str(row.get("op") or "").strip())
            if action not in ("allow", "reject") or not profile or not key or op is None:
                continue
            rules.append((action, profile, key, op, float(row["threshold"])))
    return tuple(rules)


# A missing or unreadable rules file must not crash the import (and with it the
# trading loop). The gate then rejects every signal with
# trade_profile_config_error and logs why.
_MINED_RULES_ERROR = ""
try:
    _MINED_RULES: Tuple[_MinedRule, ...] = _load_mined_rules()
except Exception as _mined_exc:  # pragma: no cover - exercised by test_tp_10_2_failsafe
    _MINED_RULES = ()
    _MINED_RULES_ERROR = f"{type(_mined_exc).__name__}: {_mined_exc}"
    log.error(
        "TP_PROFILE_LOAD | status=error | file=%s | error=%s | effect=every signal rejects "
        "with trade_profile_config_error",
        _MINED_RULES_PATH,
        _MINED_RULES_ERROR,
    )


def _profile_slug(profile: str, side: str) -> str:
    name = {"ORB1": "orb1", "ORB2": "orb2", "TL0": "tl0", "TL1": "tl1", "TL2+": "tl2p"}.get(
        profile, profile.lower().replace("+", "p")
    )
    return f"{name}_{str(side or '').strip().lower()}"


def _clause_hit(values: Dict[str, Any], clause: _Clause) -> Optional[Dict[str, Any]]:
    key, op, level = clause
    raw = values.get(key)
    if _is_missing(raw):
        return None
    value = float(raw)
    fired = (
        (op == "GT" and value > level)
        or (op == "GE" and value >= level)
        or (op == "LT" and value < level)
        or (op == "LE" and value <= level)
    )
    if not fired:
        return None
    return {"key": key, "op": op, "level": level, "value": value}


def _clauses_in_key_order(clauses: Tuple[_Clause, ...]) -> Tuple[_Clause, ...]:
    """Earlier Trade Profile keys are checked first. Same key keeps its written order."""
    return tuple(sorted(clauses, key=lambda clause: _KEY_ORDER.get(clause[0], 99)))


def _first_reject_guard(
    profile: str, side: str, values: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    for clause in _clauses_in_key_order(_REJECT_GUARDS.get((profile, side), ())):
        hit = _clause_hit(values, clause)
        if hit is not None:
            hit["overlay"] = "reject"
            return hit
    return None


def _matching_allow_lane(
    profile: str, side: str, values: Dict[str, Any]
) -> Optional[str]:
    for lane_id, clauses in _ALLOW_LANES.get((profile, side), ()):
        if clauses and all(_clause_hit(values, clause) is not None for clause in clauses):
            return lane_id
    return None


def _missing_hit(key: str) -> Dict[str, Any]:
    return {"key": key, "op": "MISSING", "level": None, "value": None}


def _apply_mined_rules(
    label: str,
    slug: str,
    values: Dict[str, Any],
    admitted: bool,
    reason: str,
    failed: List[Dict[str, Any]],
) -> Tuple[bool, str, List[Dict[str, Any]]]:
    """Walk the mined max-net rules in file order. A missing value does not match."""
    for action, profile, key, op, level in _MINED_RULES:
        if profile != label:
            continue
        hit = _clause_hit(values, (key, op, level))
        if hit is None:
            continue
        if action == "allow":
            admitted = True
            reason = f"tp101_superior_allow_{slug}_{key}"
            continue
        admitted = False
        reason = f"tp101_superior_mined_{slug}_{key}"
        failed = [hit]
    return admitted, reason, failed


def _superior_overlay(
    label: str, slug: str, values: Dict[str, Any]
) -> Optional[Tuple[str, Dict[str, Any]]]:
    """Chain, then ATR/RSI, then forward-loss. A missing key does not fire."""
    for family, profile, clauses in _SUPERIOR_GUARDS:
        if profile != label:
            continue
        ordered = _clauses_in_key_order(clauses)
        for key, op, level in ordered:
            hit = _clause_hit(values, (key, op, level))
            if hit is not None:
                return f"tp101_superior_{family}_{slug}_{key}", hit
    return None


def evaluate_trade_profile(
    path: str,
    side: str,
    values: Dict[str, Any],
    *,
    slot: Any = None,
    version: Optional[str] = None,
) -> TradeProfileDecision:
    """Accept or reject with the active profile version.

    TRADE_PROFILE_VERSION (env) picks it; default 10.5. ``10.16`` and
    ``10.17`` and ``10.18`` select flat research continuation profiles;
    ``10.20`` through ``10.23`` are the later flat profiles (10.23 = current).
    ``10.2nh`` (``10.2`` /
    ``10.3``) runs the 10.2/10.3 profile; ``10.1`` runs the 10/1 Superior
    profile unchanged. A profile whose rules could not be loaded rejects with
    ``trade_profile_config_error``.
    """
    ver = resolve_profile_version(version)
    if ver in (VERSION_10_5, VERSION_10_16, VERSION_10_17, VERSION_10_18, VERSION_10_20, VERSION_10_21, VERSION_10_22, VERSION_10_23):
        decision = _evaluate_10_5(path, side, values, slot=slot)
        # Both versions share the flat-profile evaluator; the selected version
        # still needs to survive into the decision record.
        decision.version = ver
        return decision
    if ver == VERSION_10_1:
        if _MINED_RULES_ERROR:
            return _config_error_decision(path, side, slot, ver, _MINED_RULES_ERROR)
        decision = _evaluate_10_1(path, side, values, slot=slot)
        decision.version = VERSION_10_1
        return decision
    return _evaluate_10_2(path, side, values, slot=slot)


def _evaluate_10_1(
    path: str,
    side: str,
    values: Dict[str, Any],
    *,
    slot: Any = None,
) -> TradeProfileDecision:
    """10/1 Superior. Accept or reject. Unknown path or trendline slot rejects.

    Order: stamped 9/28 profile, 9/30 overlay, mined max-net rules, then the
    chain, ATR/RSI, and forward-loss overlays. A base key the profile expects
    starts as a reject when it is missing. A 9/30 allow or a mined allow can
    still admit when its own values are present. A missing overlay or mined
    comparison does not fire.
    """
    side_u = str(side or "").strip().upper()
    if side_u in ("C", "LONG", "BULLISH"):
        side_u = "CALL"
    elif side_u in ("P", "SHORT", "BEARISH"):
        side_u = "PUT"
    name = profile_name(path, slot)
    if side_u not in ("CALL", "PUT"):
        return TradeProfileDecision(False, name or "", side_u, "trade_profile_side_missing")
    if name is None:
        reason = (
            "trade_profile_slot_missing"
            if str(path or "").strip().lower() in ("trendline_0dte", "trendline", "tl")
            else "trade_profile_unknown_path"
        )
        return TradeProfileDecision(False, "", side_u, reason)

    rules = active_rules(name, side_u)
    optional = optional_when_missing(name, side_u)
    opposite = _opposite_participation(side_u)
    failed: List[Dict[str, Any]] = []
    for key in TRADE_PROFILE_KEYS:
        if key in opposite:
            continue
        raw = values.get(key)
        if _is_missing(raw):
            if key not in optional:
                failed.append(_missing_hit(key))
            continue
        rule = rules.get(key)
        if rule is None:
            continue
        op, level = rule
        value = float(raw)
        breach = (op == "MIN" and value < level) or (op == "MAX" and value > level)
        if breach:
            failed.append({"key": key, "op": op, "level": level, "value": value})

    admitted = False
    reason = ""
    if not failed:
        guard = _first_reject_guard(name, side_u, values)
        if guard is None:
            admitted = True
            reason = f"trade_profile_{name}_{side_u}"
        else:
            reason = (
                f"tp930_{guard['key']}_{guard['value']}_{guard['op'].lower()}_{guard['level']}"
            )
            failed = [guard]
    else:
        lane = _matching_allow_lane(name, side_u, values)
        if lane is not None:
            admitted = True
            reason = f"tp930_allow_{name}_{side_u}_{lane}"
        else:
            first = failed[0]
            if first.get("op") == "MISSING":
                reason = f"missing_{first['key']}"
            else:
                reason = (
                    f"{first['key']}_{first['value']}_{'lt' if first['op'] == 'MIN' else 'gt'}_{first['level']}"
                )
    label = f"{name} {side_u}"
    slug = _profile_slug(name, side_u)
    admitted, reason, failed = _apply_mined_rules(
        label, slug, values, admitted, reason, failed
    )
    if not admitted:
        return TradeProfileDecision(False, name, side_u, reason, failed)
    overlay = _superior_overlay(label, slug, values)
    if overlay is not None:
        overlay_reason, hit = overlay
        return TradeProfileDecision(False, name, side_u, overlay_reason, [hit])
    return TradeProfileDecision(True, name, side_u, reason, failed)


def values_from_row(row: Dict[str, Any], side: str) -> Dict[str, Any]:
    """Pull the canonical profile fields off a signal row. Does not invent missing keys."""
    del side
    out: Dict[str, Any] = {}
    if not isinstance(row, dict):
        return out
    for key in CANONICAL_PROFILE_KEYS:
        val = row.get(key)
        if key == "iv" and val in (None, ""):
            val = row.get("implied_volatility")
        if val is not None:
            out[key] = val
    return out


# ---------------------------------------------------------------------------
# 10.2/10.3 Trade Profile (10.2 without its 2d/10d history rules, greek rules on)
#
# Spec: easy0DTE/configs/trade_profile_10.2_nohist.json (copied verbatim from
# 10.2_nohist_trade_profiles.json) and docs/0.TradeProfiles.md sections 1-3.
# The JSON is a set of edits on 10/1 Superior: the base, 9/30, mined and
# overlay tables above stay the source for everything the JSON does not
# change. Thresholds are the JSON floats at full precision.
#
# Evaluation order (JSON evaluation_order):
#   1 base 9/28 levels minus removed levels (missing keys still reject unless
#     optional), 2 9/30 rejects / allow lanes minus removed rejects,
#   3 mined rules in CSV order minus removed rows (a tightened allow fires only
#     if all its AND clauses hold), 4 10.2 allow lanes (only if still rejected),
#   5 if admitted: 10.1 overlays chain -> atr_rsi -> forward_loss minus removed
#     clauses, 6 10.2 reject guards in JSON order (AND of clauses; first match
#     rejects), 7 otherwise admit.
# A missing value never satisfies a clause. In 10.2 clauses (tightened AND
# clauses, 10.2 lanes and guards) dir_pct_change_5m / spy_change_pct exactly
# 0.0 count as missing. PUT delta is read as |delta| at every step.
# ---------------------------------------------------------------------------

DEFAULT_PROFILE_JSON = (
    Path(__file__).resolve().parent.parent / "configs" / "trade_profile_10.2_nohist.json"
)
_OP_FROM_JSON = {">=": "GE", "<=": "LE", ">": "GT", "<": "LT"}
_ZERO_IS_MISSING_102 = frozenset({"dir_pct_change_5m", "spy_change_pct"})
# sha256 of the greedy_mined_profile_rules.csv the 10.2/10.3 profile and its
# 4,117-row parity were built on (60 data rows). A different file still loads
# when every JSON edit matches, but TP_PROFILE_LOAD warns.
EXPECTED_MINED_CSV_SHA256 = "17a47faeb71814c64d4724f9213ccd5673e0e96123bc248cff96f871d712870b"
_MATCH_TOL = 1e-9
_FAMILY_ORDER = ("chain", "atr_rsi", "forward_loss")

# Same-snapshot chain ratios and 5-min flow keys the 10.2 rules read. They are
# stamped on the decision row by central_tech (chain_ratios.stamp_chain_ratios
# and chain_ring.derive_chain_trajectory_features via stamp_admit_tech). The
# gate only reads them; it never computes or fetches them.
_EXTRA_102_KEYS_FALLBACK: Tuple[str, ...] = (
    "call_chain_volume_share",
    "call_put_chain_volume_ratio",
    "put_call_chain_volume_ratio",
    "call_chain_vol_oi",
    "put_chain_vol_oi",
    "total_chain_vol_oi",
    "put_chain_volume_delta_5m",
)

_Clause102 = Tuple[str, str, float]
_MinedRule102 = Tuple[str, str, str, str, float, Tuple[_Clause102, ...]]


@dataclass
class _Profile102:
    profiles: Dict[str, Dict[str, Dict[str, Rule]]]
    reject930: Dict[Tuple[str, str], Tuple[_Clause, ...]]
    allow930: Dict[Tuple[str, str], Tuple[Tuple[str, Tuple[_Clause, ...]], ...]]
    mined: Tuple[_MinedRule102, ...]
    overlays: Tuple[Tuple[str, str, Tuple[_Clause, ...]], ...]
    allow_lanes: Tuple[Tuple[str, str, Tuple[_Clause102, ...]], ...]
    guards: Tuple[Tuple[str, str, Tuple[_Clause102, ...]], ...]
    extra_keys: Tuple[str, ...]
    name: str = ""
    json_path: str = ""
    json_sha256: str = ""
    csv_sha256: str = ""
    counts: Dict[str, int] = field(default_factory=dict)


_PROFILE_102: Optional[_Profile102] = None
_PROFILE_102_ERROR = ""
_UNKNOWN_VERSION_WARNED: set = set()


def resolve_profile_version(version: Optional[str] = None) -> str:
    """10.5, 10.2nh or 10.1. Env TRADE_PROFILE_VERSION; unset or unknown -> the
    default (DEFAULT_PROFILE_VERSION, 10.5)."""
    raw = version if version is not None else os.getenv(TRADE_PROFILE_VERSION_ENV, "")
    key = str(raw or "").strip().lower().replace(" ", "")
    if not key:
        return DEFAULT_PROFILE_VERSION
    resolved = _VERSION_ALIASES.get(key)
    if resolved is None:
        if key not in _UNKNOWN_VERSION_WARNED:
            _UNKNOWN_VERSION_WARNED.add(key)
            log.warning(
                "TP_PROFILE_VERSION | value=%r | unknown | using=%s",
                raw,
                DEFAULT_PROFILE_VERSION,
            )
        return DEFAULT_PROFILE_VERSION
    return resolved


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except Exception:
        return ""


def _close(a: float, b: float) -> bool:
    return abs(float(a) - float(b)) <= _MATCH_TOL * max(1.0, abs(float(a)), abs(float(b)))


def _json_clauses(items: Any, where: str) -> Tuple[_Clause102, ...]:
    out: List[_Clause102] = []
    for item in items or ():
        op = _OP_FROM_JSON.get(str(item.get("op") or "").strip())
        key = str(item.get("key") or "").strip()
        if op is None or not key:
            raise ValueError(f"{where}: bad clause {item!r}")
        out.append((key, op, float(item["threshold"])))
    if not out:
        raise ValueError(f"{where}: no clauses")
    return tuple(out)


def _split_label(label: str) -> Tuple[str, str]:
    parts = str(label or "").split()
    if len(parts) != 2 or parts[1] not in ("CALL", "PUT") or parts[0] not in _PROFILES:
        raise ValueError(f"unknown path/side {label!r}")
    return parts[0], parts[1]


def _build_profile_102(spec: Dict[str, Any]) -> _Profile102:
    """Apply the JSON edits to the 10/1 Superior tables. Every removal and
    tightening must match exactly one 10.1 item by key and threshold."""
    profiles = {n: {s: dict(r) for s, r in sides.items()} for n, sides in _PROFILES.items()}
    reject930 = {k: list(v) for k, v in _REJECT_GUARDS.items()}
    mined: List[List[Any]] = [list(m) + [()] for m in _MINED_RULES]
    overlays = [(f, p, list(c)) for f, p, c in _SUPERIOR_GUARDS]
    removed = spec.get("removed_from_10_1") or {}

    # The JSON names mined rows by their 1-indexed CSV data row. Those rows must
    # be where the JSON says, or this CSV is not the one the profile was built on.
    for item in list(removed.get("mined_rows") or ()) + list(spec.get("tightened_mined_allows") or ()):
        row_no = item.get("csv_row")
        if row_no is None:
            continue
        idx = int(row_no) - 1
        op = _OP_FROM_JSON[str(item["op"]).strip()]
        if not (0 <= idx < len(_MINED_RULES)):
            raise ValueError(f"csv_row {row_no} outside the mined rules file")
        m = _MINED_RULES[idx]
        if not (
            m[0] == item["action"] and m[1] == item["profile"] and m[2] == item["key"]
            and m[3] == op and _close(m[4], item["threshold"])
        ):
            raise ValueError(f"csv_row {row_no} is {m!r}, JSON expects {item!r}")

    for item in removed.get("base_levels") or ():
        name, side = _split_label(item["profile"])
        key = str(item["key"])
        if key not in profiles[name][side]:
            raise ValueError(f"base level not found: {item!r}")
        del profiles[name][side][key]

    for item in removed.get("reject930") or ():
        name, side = _split_label(item["profile"])
        op = _OP_FROM_JSON[str(item["op"]).strip()]
        hits = [
            c for c in reject930.get((name, side), [])
            if c[0] == item["key"] and c[1] == op and _close(c[2], item["threshold"])
        ]
        if len(hits) != 1:
            raise ValueError(f"9/30 reject match {len(hits)} != 1: {item!r}")
        reject930[(name, side)].remove(hits[0])

    for item in removed.get("mined_rows") or ():
        op = _OP_FROM_JSON[str(item["op"]).strip()]
        hits = [
            i for i, m in enumerate(mined)
            if m[0] == item["action"] and m[1] == item["profile"] and m[2] == item["key"]
            and m[3] == op and _close(m[4], item["threshold"])
        ]
        if len(hits) != 1:
            raise ValueError(f"mined row match {len(hits)} != 1: {item!r}")
        del mined[hits[0]]

    for item in removed.get("overlay_clauses") or ():
        op = _OP_FROM_JSON[str(item["op"]).strip()]
        n_hit = 0
        for family, prof, clauses in overlays:
            if family != item["family"] or prof != item["profile"]:
                continue
            for c in list(clauses):
                if c[0] == item["key"] and c[1] == op and _close(c[2], item["threshold"]):
                    clauses.remove(c)
                    n_hit += 1
        if n_hit != 1:
            raise ValueError(f"overlay clause match {n_hit} != 1: {item!r}")

    for item in spec.get("tightened_mined_allows") or ():
        op = _OP_FROM_JSON[str(item["op"]).strip()]
        hits = [
            i for i, m in enumerate(mined)
            if m[0] == item["action"] and m[1] == item["profile"] and m[2] == item["key"]
            and m[3] == op and _close(m[4], item["threshold"])
        ]
        if len(hits) != 1:
            raise ValueError(f"tightened row match {len(hits)} != 1: {item!r}")
        mined[hits[0]][5] = _json_clauses(item.get("and_clauses"), "tightened_mined_allows")

    if spec.get("derived_participation_lanes"):
        raise ValueError("derived_participation_lanes is not supported by this gate")

    allow_lanes = []
    for item in spec.get("new_allow_lanes") or ():
        _split_label(item["profile"])
        allow_lanes.append(
            (item["profile"], str(item["reason"]), _json_clauses(item.get("all"), "new_allow_lanes"))
        )
    guards = []
    for item in spec.get("new_reject_guards") or ():
        _split_label(item["profile"])
        guards.append(
            (item["profile"], str(item["reason"]), _json_clauses(item.get("all"), "new_reject_guards"))
        )

    extra: List[str] = []
    for _label, _reason, clauses in allow_lanes + guards:
        for key, _op, _lv in clauses:
            if key not in TRADE_PROFILE_KEYS and key not in extra:
                extra.append(key)
    for m in mined:
        for key, _op, _lv in m[5]:
            if key not in TRADE_PROFILE_KEYS and key not in extra:
                extra.append(key)

    counts = {
        "reject_guards": len(guards),
        "allow_lanes": len(allow_lanes),
        "tightened": sum(1 for m in mined if m[5]),
        "removed_base": len(removed.get("base_levels") or ()),
        "removed_930": len(removed.get("reject930") or ()),
        "removed_mined": len(removed.get("mined_rows") or ()),
        "removed_overlay": len(removed.get("overlay_clauses") or ()),
        "mined_rows": len(mined),
    }
    return _Profile102(
        profiles=profiles,
        reject930={k: tuple(v) for k, v in reject930.items()},
        allow930=dict(_ALLOW_LANES),
        mined=tuple((m[0], m[1], m[2], m[3], m[4], tuple(m[5])) for m in mined),
        overlays=tuple((f, p, tuple(c)) for f, p, c in overlays),
        allow_lanes=tuple(allow_lanes),
        guards=tuple(guards),
        extra_keys=tuple(extra),
        name=str(spec.get("name") or ""),
        counts=counts,
    )


def load_profile_102(path: Optional[Path] = None) -> _Profile102:
    """Read the 10.2/10.3 JSON and build the profile. Raises on any problem."""
    if _MINED_RULES_ERROR:
        raise RuntimeError(f"mined rules not loaded: {_MINED_RULES_ERROR}")
    json_path = Path(path or os.getenv(TRADE_PROFILE_JSON_ENV) or DEFAULT_PROFILE_JSON)
    with json_path.open(encoding="utf-8") as handle:
        spec = json.load(handle)
    profile = _build_profile_102(spec)
    profile.json_path = str(json_path)
    profile.json_sha256 = _sha256(json_path)
    profile.csv_sha256 = _sha256(_MINED_RULES_PATH)
    return profile


def _init_profile_102() -> None:
    global _PROFILE_102, _PROFILE_102_ERROR
    try:
        _PROFILE_102 = load_profile_102()
        _PROFILE_102_ERROR = ""
        c = _PROFILE_102.counts
        if _PROFILE_102.csv_sha256 != EXPECTED_MINED_CSV_SHA256:
            log.warning(
                "TP_PROFILE_LOAD | version=%s | status=warning | mined_csv_sha256=%s | "
                "expected=%s | the mined rules file differs from the one the 10.2/10.3 "
                "parity was run on",
                VERSION_10_2,
                _PROFILE_102.csv_sha256,
                EXPECTED_MINED_CSV_SHA256,
            )
        log.info(
            "TP_PROFILE_LOADED | version=%s | active=%s | json=%s | json_sha256=%s | "
            "mined_csv_sha256=%s | guards=%d | lanes=%d | tightened=%d | removed base=%d "
            "930=%d mined=%d overlay=%d | mined_rows=%d | extra_keys=%s",
            VERSION_10_2,
            resolve_profile_version(),
            _PROFILE_102.json_path,
            _PROFILE_102.json_sha256,
            _PROFILE_102.csv_sha256,
            c["reject_guards"],
            c["allow_lanes"],
            c["tightened"],
            c["removed_base"],
            c["removed_930"],
            c["removed_mined"],
            c["removed_overlay"],
            c["mined_rows"],
            ",".join(_PROFILE_102.extra_keys),
        )
    except Exception as exc:
        _PROFILE_102 = None
        _PROFILE_102_ERROR = f"{type(exc).__name__}: {exc}"
        log.error(
            "TP_PROFILE_LOAD | version=%s | status=error | error=%s | effect=when %s is "
            "active every signal rejects with trade_profile_config_error",
            VERSION_10_2,
            _PROFILE_102_ERROR,
            VERSION_10_2,
        )


def profile_102_status() -> Dict[str, Any]:
    """Load state for readiness checks and tests."""
    prof = _PROFILE_102
    return {
        "active_version": resolve_profile_version(),
        "loaded": prof is not None,
        "error": _PROFILE_102_ERROR or _MINED_RULES_ERROR,
        "json_path": prof.json_path if prof else "",
        "json_sha256": prof.json_sha256 if prof else "",
        "mined_csv_sha256": prof.csv_sha256 if prof else "",
        "counts": dict(prof.counts) if prof else {},
        "extra_keys": list(prof.extra_keys) if prof else [],
    }


def decision_value_keys() -> Tuple[str, ...]:
    """Every key any loaded profile can read: the 20 canonical keys, then the
    10.2 ratio / flow keys (which include the three vol/OI ratios of the 10.5
    27-key templates). The 10.2 base loop uses only the canonical keys."""
    prof = _PROFILE_102
    extra = prof.extra_keys if prof is not None else _EXTRA_102_KEYS_FALLBACK
    out = list(TRADE_PROFILE_KEYS) + [k for k in extra if k not in TRADE_PROFILE_KEYS]
    for key in TRADE_PROFILE_KEYS_CALL + TRADE_PROFILE_KEYS_PUT:
        if key not in out:
            out.append(key)
    return tuple(out)


def _clause_hit_102(values: Dict[str, Any], clause: _Clause102) -> bool:
    """10.2 clause. Missing never hits; dir/spy exactly 0.0 count as missing."""
    key, op, level = clause
    raw = values.get(key)
    if _is_missing(raw):
        return False
    value = float(raw)
    if key in _ZERO_IS_MISSING_102 and value == 0.0:
        return False
    return (
        (op == "GT" and value > level)
        or (op == "GE" and value >= level)
        or (op == "LT" and value < level)
        or (op == "LE" and value <= level)
    )


def _config_error_decision(
    path: str, side: str, slot: Any, version: str, error: str
) -> TradeProfileDecision:
    side_u = str(side or "").strip().upper()
    if side_u in ("C", "LONG", "BULLISH"):
        side_u = "CALL"
    elif side_u in ("P", "SHORT", "BEARISH"):
        side_u = "PUT"
    return TradeProfileDecision(
        False,
        profile_name(path, slot) or "",
        side_u,
        "trade_profile_config_error",
        [{"key": "config", "op": "ERROR", "level": None, "value": str(error)[:200]}],
        version=version,
        ref_reason="trade_profile_config_error",
        stage="config_error",
    )


def _evaluate_10_2(
    path: str,
    side: str,
    values_in: Dict[str, Any],
    *,
    slot: Any = None,
) -> TradeProfileDecision:
    prof = _PROFILE_102
    if prof is None:
        return _config_error_decision(
            path, side, slot, VERSION_10_2, _PROFILE_102_ERROR or "profile not loaded"
        )
    side_u = str(side or "").strip().upper()
    if side_u in ("C", "LONG", "BULLISH"):
        side_u = "CALL"
    elif side_u in ("P", "SHORT", "BEARISH"):
        side_u = "PUT"
    name = profile_name(path, slot)

    values: Dict[str, Any] = dict(values_in or {})
    if side_u == "PUT" and not _is_missing(values.get("delta")):
        values["delta"] = abs(float(values["delta"]))
    missing = [k for k in decision_value_keys() if _is_missing(values.get(k))]

    def _done(admitted: bool, reason: str, ref: str, stage: str, failed=None, prof_name=None):
        return TradeProfileDecision(
            admitted,
            prof_name if prof_name is not None else (name or ""),
            side_u,
            reason,
            list(failed or []),
            version=VERSION_10_2,
            ref_reason=ref,
            stage=stage,
            values=values,
            missing=missing,
        )

    if side_u not in ("CALL", "PUT"):
        return _done(False, "trade_profile_side_missing", "trade_profile_side_missing", "route")
    if name is None:
        reason = (
            "trade_profile_slot_missing"
            if str(path or "").strip().lower() in ("trendline_0dte", "trendline", "tl")
            else "trade_profile_unknown_path"
        )
        return _done(False, reason, reason, "route", prof_name="")

    # Step 1: base 9/28 levels (20 canonical keys only; a missing ratio is
    # never a base reject).
    rules = prof.profiles.get(name, {}).get(side_u, {})
    optional = optional_when_missing(name, side_u)
    opposite = _opposite_participation(side_u)
    failed: List[Dict[str, Any]] = []
    for key in TRADE_PROFILE_KEYS:
        if key in opposite:
            continue
        raw = values.get(key)
        if _is_missing(raw):
            if key not in optional:
                failed.append(_missing_hit(key))
            continue
        rule = rules.get(key)
        if rule is None:
            continue
        op, level = rule
        value = float(raw)
        if (op == "MIN" and value < level) or (op == "MAX" and value > level):
            failed.append({"key": key, "op": op, "level": level, "value": value})

    # Step 2: 9/30 rejects (base passed) or allow lanes (base failed).
    admitted = False
    if not failed:
        guard = None
        for clause in _clauses_in_key_order(prof.reject930.get((name, side_u), ())):
            guard = _clause_hit(values, clause)
            if guard is not None:
                guard["overlay"] = "reject"
                break
        if guard is None:
            admitted = True
            reason = f"trade_profile_{name}_{side_u}"
            ref = reason
            stage = "base_pass"
        else:
            reason = f"tp930_{guard['key']}_{guard['value']}_{guard['op'].lower()}_{guard['level']}"
            ref = f"tp930_{guard['key']}"
            stage = "tp930_reject"
            failed = [guard]
    else:
        lane = None
        for lane_id, clauses in prof.allow930.get((name, side_u), ()):
            if clauses and all(_clause_hit(values, c) is not None for c in clauses):
                lane = lane_id
                break
        if lane is not None:
            admitted = True
            reason = f"tp930_allow_{name}_{side_u}_{lane}"
            ref = f"tp930_allow_{lane}"
            stage = "tp930_allow"
        else:
            first = failed[0]
            if first.get("op") == "MISSING":
                reason = f"missing_{first['key']}"
            else:
                reason = (
                    f"{first['key']}_{first['value']}_"
                    f"{'lt' if first['op'] == 'MIN' else 'gt'}_{first['level']}"
                )
            ref = f"base_{first['op']}_{first['key']}"
            stage = "base_reject"

    # Step 3: mined rules, CSV order, last match wins.
    label = f"{name} {side_u}"
    slug = _profile_slug(name, side_u)
    for action, prof_label, key, op, level, and_clauses in prof.mined:
        if prof_label != label:
            continue
        hit = _clause_hit(values, (key, op, level))
        if hit is None:
            continue
        if and_clauses and not all(_clause_hit_102(values, c) for c in and_clauses):
            continue
        if action == "allow":
            admitted = True
            reason = ref = f"tp101_superior_allow_{slug}_{key}"
            stage = "mined_allow"
            continue
        admitted = False
        reason = ref = f"tp101_superior_mined_{slug}_{key}"
        stage = "mined_reject"
        failed = [hit]

    # Step 4: 10.2 allow lanes, only if still rejected.
    if not admitted:
        for lane_label, lane_reason, clauses in prof.allow_lanes:
            if lane_label == label and all(_clause_hit_102(values, c) for c in clauses):
                admitted = True
                reason = ref = lane_reason
                stage = "tp102_allow"
                break
    if not admitted:
        return _done(False, reason, ref, stage, failed)

    # Step 5: 10.1 overlays, family order, canonical key order within a family.
    for family in _FAMILY_ORDER:
        for fam, prof_label, clauses in prof.overlays:
            if fam != family or prof_label != label:
                continue
            for clause in _clauses_in_key_order(clauses):
                hit = _clause_hit(values, clause)
                if hit is not None:
                    why = f"tp101_superior_{family}_{slug}_{clause[0]}"
                    return _done(False, why, why, f"overlay_{family}", [hit])

    # Step 6: 10.2 reject guards, JSON order, AND of clauses.
    for guard_label, guard_reason, clauses in prof.guards:
        if guard_label != label:
            continue
        if all(_clause_hit_102(values, c) for c in clauses):
            hits = [
                {"key": k, "op": o, "level": lv, "value": float(values[k])} for k, o, lv in clauses
            ]
            return _done(False, guard_reason, guard_reason, "tp102_guard", hits)

    # Step 7: admit.
    return _done(True, reason, ref, stage, failed)


def _fmt_value(raw: Any) -> str:
    if _is_missing(raw):
        return "NA"
    return repr(float(raw))


def decision_values_line(
    decision: TradeProfileDecision,
    values: Dict[str, Any],
    *,
    symbol: str = "",
    path: str = "",
    slot: Any = None,
    trade_id: Any = None,
    shadow: Optional[TradeProfileDecision] = None,
    freeze_suffix: str = "",
) -> str:
    """One TP_DECISION_VALUES line: version, path/side, verdict, reason and every
    key the profile can read, as the gate read it (PUT delta as |delta| on 10.2
    and 10.5). A 10.5 line lists the side's 27-key template in template order,
    then the other keys the shadow profile reads."""
    read = decision.values if decision.values else values
    keys = _decision_log_keys(decision)
    missing = decision.missing if decision.values else [k for k in keys if _is_missing(read.get(k))]
    parts = [
        "TP_DECISION_VALUES",
        f"{symbol or '?'} {path or '?'} {decision.side or '?'} slot={slot if slot is not None else 'NA'} "
        f"trade_id={trade_id if trade_id not in (None, '') else 'NA'}",
        f"version={decision.version} profile={decision.profile or 'NA'} "
        f"admit={1 if decision.admitted else 0} reason={decision.reason} "
        f"ref_reason={decision.ref_reason or decision.reason} stage={decision.stage or 'NA'}",
        " ".join(f"{k}={_fmt_value(read.get(k))}" for k in keys),
        f"missing=[{','.join(missing)}]",
    ]
    if shadow is not None:
        parts.append(
            f"shadow={shadow.version}:{1 if shadow.admitted else 0}:{shadow.reason}"
        )
    if freeze_suffix:
        # Oct 7 2026: decision-freeze ages + gate clock ride at the end so the
        # existing positional parsers (values at 3, missing at 4) still work.
        parts.append(str(freeze_suffix))
    return " | ".join(parts)


# ---------------------------------------------------------------------------
# 10.5 Trade Profiles (flat), docs/0.TradeProfiles.md sections 1-2.
#
# Spec: easy0DTE/configs/trade_profile_10.5.json. Every path/side (ORB1, TL0,
# TL1, TL2+, Breakout; Breakout = the breakout_0dte path, internal profile
# name ORB2) lists the side's full 27-key template in template grouping and
# order; a key is PASS or an inclusive [min, max] range (either side may be
# open). TL1 CALL and Breakout CALL add one ``not (A and B)`` line.
#
# A trade admits only when it passes every line for its path/side. A missing
# value passes its line; dir_pct_change_5m / spy_change_pct exactly 0.0 count
# as missing (JSON semantics.zero_is_missing, the research engine
# convention).
#
# Exception (Eric, Oct 5 2026 17:10 PT; 17:35 PT scope): a cell may list
# ``missing_reject_keys``. Those keys are the bar / chain-ring warm-up keys
# (RSI, ATR, VWAP-ATR distance, 3m price-volume efficiency, 5m dir% change)
# that the cell gates with a range line. In that cell a missing value
# (None/NaN, or 0.0 for a zero_is_missing key) REJECTS with
# ``tp105_{slug}_missing_{key}``. Only ORB1 and TL0 (CALL and PUT) list them
# (ORB1 CALL: pve_3m, vwap-atr, rsi; ORB1 PUT: pve_3m; TL0 CALL: rsi;
# TL0 PUT: dir_5m, rsi); every other key and cell keeps missing = pass. The check
# runs in the side's template order together with the range lines. A not(A and B) line rejects only when both parts hold; a
# missing part does not hold. Lines are checked
# in template order, then the not(A and B) line; every failing line is kept
# in ``failed`` and the first one names the reason. Thresholds are the JSON
# floats verbatim. No 10.1/10.2 table, mined rule or overlay is used.
#
# 10/6/2026 adds (Eric): an entry may set ``min_strict`` / ``max_strict`` true;
# that side is then exclusive (value <= min / value >= max fails), otherwise
# bounds stay inclusive. A cell may list ``derived_lines``: a line on a key
# DERIVED from template keys (registry DERIVED_KEYS_105: put_share =
# put_chain_volume / (call_chain_volume + put_chain_volume) and call_share =
# call_chain_volume / (call_chain_volume + put_chain_volume)),
# computed from the same stamped values, never read from a new source. It is
# checked right after its ``after`` template key. Either input missing, or
# call + put <= 0, makes it missing -> passes (derived keys cannot be
# missing_reject keys, so in ORB1 CALL a missing call_share passes even though
# the cell's warm-up keys reject when missing). Lines added: TL1 PUT
# put_share > 0.1927 (derived), Breakout PUT iv < 0.7238, Breakout CALL
# chain_sanity_best_same_side_spread_pct > 0.6329 (t160u: now inclusive >= 0.579709); t104u: ORB1 CALL
# call_share > 0.5555 (derived), TL2+ PUT put_share > 0.2341 (derived).
# Reasons keep the tp105_{slug}_min_{key} / _max_{key} form (e.g.
# tp105_tl1_put_min_put_share, tp105_orb1_call_min_call_share,
# tp105_tl2p_put_min_put_share).
# ---------------------------------------------------------------------------

DEFAULT_PROFILE_105_JSON = (
    Path(__file__).resolve().parent.parent / "configs" / "trade_profile_10.5.json"
)
_CELL_PATHS_105 = ("ORB1", "TL0", "TL1", "TL2+", "Breakout")
_CELL_TO_PROFILE_105 = {"ORB1": "ORB1", "TL0": "TL0", "TL1": "TL1", "TL2+": "TL2+", "Breakout": "ORB2"}
_PROFILE_TO_CELL_105 = {v: k for k, v in _CELL_TO_PROFILE_105.items()}
_SLUG_105 = {"ORB1": "orb1", "TL0": "tl0", "TL1": "tl1", "TL2+": "tl2p", "Breakout": "breakout"}

_Line105 = Tuple[str, Optional[float], Optional[float]]  # (key, min, max)
_NotAnd105 = Tuple[str, Tuple[_Clause102, ...]]  # (reason, clauses)


@dataclass
class _Cell105:
    label: str  # doc label, e.g. "Breakout CALL"
    profile: str  # internal profile name, e.g. "ORB2"
    side: str
    slug: str  # e.g. "breakout_call"
    lines: Tuple[_Line105, ...]
    not_and: Tuple[_NotAnd105, ...]
    template: Tuple[Tuple[str, Tuple[str, ...]], ...]
    # Keys whose missing value rejects in this cell (template order). Empty =
    # every missing key passes (the 10.5 default).
    missing_reject: Tuple[str, ...] = ()
    # 10/6/2026: exclusive sides, key -> (min_strict, max_strict); absent = inclusive.
    strict: Dict[str, Tuple[bool, bool]] = field(default_factory=dict)
    # 10/6/2026: lines on derived keys (key, min, max), each checked right after
    # the template key in ``derived_after[key]``.
    derived: Tuple[_Line105, ...] = ()
    derived_after: Dict[str, str] = field(default_factory=dict)


# Warm-up keys allowed in ``missing_reject_keys``: the 10.5 template keys that
# are None early in the session only because the 1m bar history or the 5m
# chain ring is not deep enough yet. Chain / greek / L1 / SPY / break keys are
# not warm-up keys and stay missing = pass everywhere.
WARMUP_MISSING_REJECT_KEYS: frozenset = frozenset({
    "atr",
    "price_volume_efficiency_3m",
    "dir_pct_change_5m",
    "distance_from_vwap_atr",
    "rsi",
})


def _put_share_105(values: Dict[str, Any]) -> Optional[float]:
    """put_chain_volume / (call_chain_volume + put_chain_volume); None (missing)
    when either volume is missing or the total is not > 0."""
    put, call = values.get("put_chain_volume"), values.get("call_chain_volume")
    if _is_missing(put) or _is_missing(call):
        return None
    put_f, call_f = float(put), float(call)
    total = put_f + call_f
    if not total > 0:
        return None
    return put_f / total


def _call_share_105(values: Dict[str, Any]) -> Optional[float]:
    """call_chain_volume / (call_chain_volume + put_chain_volume); None (missing)
    when either volume is missing or the total is not > 0."""
    put, call = values.get("put_chain_volume"), values.get("call_chain_volume")
    if _is_missing(put) or _is_missing(call):
        return None
    put_f, call_f = float(put), float(call)
    total = put_f + call_f
    if not total > 0:
        return None
    return call_f / total


# Derived keys a 10.5 cell may gate (10/6/2026): key -> (formula text the JSON
# must repeat verbatim, template inputs, function). These are derived template
# keys whose values are computed from the stamped chain-volume inputs.
DERIVED_KEYS_105: Dict[str, Tuple[str, Tuple[str, ...], Any]] = {
    "put_share": (
        "put_chain_volume / (call_chain_volume + put_chain_volume)",
        ("put_chain_volume", "call_chain_volume"),
        _put_share_105,
    ),
    "call_share": (  # t104u (Eric, 10/6/2026)
        "call_chain_volume / (call_chain_volume + put_chain_volume)",
        ("call_chain_volume", "put_chain_volume"),
        _call_share_105,
    ),
}


# Default check slot for a derived share in a pure-flat cell (key checked
# right after this template key), matching every 10.16-10.23 JSON "after".
DERIVED_AFTER_105: Dict[Tuple[str, str], str] = {
    ("CALL", "call_share"): "put_chain_volume",
    ("CALL", "put_share"): "call_chain_open_interest",
    ("PUT", "put_share"): "call_chain_volume",
    ("PUT", "call_share"): "call_chain_volume",
}
_FLAT_ENTRY_KEYS_105 = {"key", "min", "max"}


def _expand_flat_entry_105(e: Dict[str, Any], side: str, label: str) -> Dict[str, Any]:
    """Normalize one pure-flat entry; legacy entries (rule/doc/derived) pass through."""
    if not isinstance(e, dict):
        raise ValueError(f"{label}: flat entries must be objects")
    if set(e) - _FLAT_ENTRY_KEYS_105:
        return e  # legacy explicit entry (rule PASS, doc, derived fields, strict)
    key = str(e.get("key"))
    if "min" not in e and "max" not in e:
        return {"key": key, "rule": "PASS"}
    if key in DERIVED_KEYS_105:
        formula, inputs, _fn = DERIVED_KEYS_105[key]
        out = {"key": key, "derived": True, "formula": formula, "inputs": list(inputs),
               "after": DERIVED_AFTER_105[(side, key)]}
        out.update({k: e[k] for k in ("min", "max") if k in e})
        return out
    return e


def _strict_flags_105(e: Dict[str, Any], where: str, lo: Optional[float], hi: Optional[float]) -> Tuple[bool, bool]:
    flags = []
    for side, bound in (("min", lo), ("max", hi)):
        raw = e.get(f"{side}_strict", False)
        if not isinstance(raw, bool):
            raise ValueError(f"{where}: {side}_strict must be true/false")
        if raw and bound is None:
            raise ValueError(f"{where}: {side}_strict without a {side}")
        flags.append(raw)
    if lo is not None and hi is not None and (flags[0] or flags[1]) and lo >= hi:
        raise ValueError(f"{where}: strict range {lo}..{hi} is empty")
    return flags[0], flags[1]


@dataclass
class _Profile105:
    cells: Dict[Tuple[str, str], _Cell105]
    zero_is_missing: frozenset
    put_delta: str = "abs"
    name: str = ""
    version: str = VERSION_10_5
    json_path: str = ""
    json_sha256: str = ""
    counts: Dict[str, Any] = field(default_factory=dict)


_PROFILE_105: Optional[_Profile105] = None
_PROFILE_105_ERROR = ""


def template_groups(side: str) -> Tuple[Tuple[str, Tuple[str, ...]], ...]:
    """The side's 27-key Final Trade Profile Template, grouped."""
    return TRADE_PROFILE_GROUPS_PUT if str(side or "").strip().upper() == "PUT" else TRADE_PROFILE_GROUPS_CALL


def template_keys(side: str) -> Tuple[str, ...]:
    """The side's 27 template keys in template order."""
    return TRADE_PROFILE_KEYS_PUT if str(side or "").strip().upper() == "PUT" else TRADE_PROFILE_KEYS_CALL


def _finite(raw: Any, where: str) -> float:
    if isinstance(raw, bool):
        raise ValueError(f"{where}: bad number {raw!r}")
    value = float(raw)
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError(f"{where}: bad number {raw!r}")
    return value


def _build_profile_105(spec: Dict[str, Any]) -> _Profile105:
    """Validate a flat 10.5-family JSON against the code templates and compile it.
    Any mismatch (order, grouping, unknown or repeated key, bad bound) raises."""
    profile_version = str(spec.get("version") or "")
    if profile_version not in (VERSION_10_5, VERSION_10_16, VERSION_10_17, VERSION_10_18, VERSION_10_20, VERSION_10_21, VERSION_10_22, VERSION_10_23):
        raise ValueError(
            f"version {profile_version!r} not in {(VERSION_10_5, VERSION_10_16, VERSION_10_17, VERSION_10_18, VERSION_10_20, VERSION_10_21, VERSION_10_22, VERSION_10_23)!r}"
        )
    sem = spec.get("semantics") or {}
    if str(sem.get("missing") or "") != "pass" or str(sem.get("bounds") or "") != "inclusive":
        raise ValueError("semantics must be missing=pass, bounds=inclusive")
    allowed_mr = frozenset(str(k) for k in (sem.get("missing_reject_allowed_keys") or ()))
    if allowed_mr and allowed_mr != WARMUP_MISSING_REJECT_KEYS:
        raise ValueError(
            "semantics.missing_reject_allowed_keys differs from the code warm-up key list: "
            f"{sorted(allowed_mr)} != {sorted(WARMUP_MISSING_REJECT_KEYS)}"
        )
    put_delta = str(sem.get("put_delta") or "")
    if put_delta != "abs":
        raise ValueError("semantics.put_delta must be abs")
    zero = frozenset(str(k) for k in (sem.get("zero_is_missing") or ()))
    if not zero <= set(TRADE_PROFILE_KEYS_CALL):
        raise ValueError(f"zero_is_missing has unknown keys: {sorted(zero - set(TRADE_PROFILE_KEYS_CALL))}")

    templates = spec.get("templates") or {}
    for side, code_groups in (("CALL", TRADE_PROFILE_GROUPS_CALL), ("PUT", TRADE_PROFILE_GROUPS_PUT)):
        got = tuple(
            (str(g.get("group")), tuple(str(k) for k in g.get("keys") or ()))
            for g in templates.get(side) or ()
        )
        if got != code_groups:
            raise ValueError(f"{side} template order/grouping differs from TRADE_PROFILE_GROUPS_{side}")

    profiles = spec.get("profiles") or {}
    want = [f"{p} {s}" for p in _CELL_PATHS_105 for s in ("CALL", "PUT")]
    if sorted(profiles) != sorted(want):
        raise ValueError(f"profiles must be exactly {want}, got {sorted(profiles)}")

    cells: Dict[Tuple[str, str], _Cell105] = {}
    n_range = n_not = n_bounds = n_mr = n_strict = n_derived = 0
    per_cell: Dict[str, int] = {}
    derived_per_cell: Dict[str, Tuple[str, ...]] = {}
    mr_per_cell: Dict[str, Tuple[str, ...]] = {}
    for label in want:
        path_name, side = label.split()
        cell = profiles[label] or {}
        code_groups = template_groups(side)
        flat_entries = cell.get("keys")
        if flat_entries is not None:
            # New continuation profiles may use one flat, fully explicit
            # 27-key list per path/side. Normalize it to the historical
            # grouped representation before compiling so replay semantics
            # stay identical while the JSON remains easy to edit.
            if not isinstance(flat_entries, (list, tuple)):
                raise ValueError(f"{label}: keys must be a list")
            # 10/23/2026 (Eric, t52u) pure-flat cells: an entry may be just
            # {key}, {key,min}, {key,max} or {key,min,max}. A bare {key} is
            # PASS (unbounded); a bounded call_share/put_share entry gets its
            # derivation (formula/inputs/after) from DERIVED_KEYS_105 /
            # DERIVED_AFTER_105 in code. Cell exceptions (not_and,
            # missing_reject_keys) may live in top-level ``cell_policies``.
            flat_entries = [_expand_flat_entry_105(e, side, label) for e in flat_entries]
            policy = (spec.get("cell_policies") or {}).get(label)
            if policy is not None:
                if not isinstance(policy, dict) or set(policy) - {"not_and", "missing_reject_keys"}:
                    raise ValueError(f"cell_policies/{label}: only not_and / missing_reject_keys allowed")
                if any(k in cell for k in policy):
                    raise ValueError(f"cell_policies/{label}: also set inside the cell")
                cell = dict(cell)
                cell.update(copy.deepcopy(policy))
            actual_flat = tuple(str(e.get("key")) for e in flat_entries)
            if actual_flat != template_keys(side):
                raise ValueError(f"{label}: keys differ from the {side} template order")
            groups = []
            share_entries = []
            for gname, gkeys in code_groups:
                group_entries = []
                for key in gkeys:
                    if key in DERIVED_KEYS_105:
                        continue
                    entry = next(e for e in flat_entries if str(e.get("key")) == key)
                    group_entries.append(entry)
                groups.append({"group": gname, "keys": group_entries})
            for entry in flat_entries:
                key = str(entry.get("key"))
                if key in DERIVED_KEYS_105 and str(entry.get("rule") or "").upper() != "PASS":
                    share_entries.append(entry)
            cell = dict(cell)
            cell["groups"] = groups
            cell["derived_lines"] = share_entries
        groups = cell.get("groups") or ()
        if tuple(str(g.get("group")) for g in groups) != tuple(g for g, _k in code_groups):
            raise ValueError(f"{label}: groups differ from the {side} template")
        lines: List[_Line105] = []
        strict: Dict[str, Tuple[bool, bool]] = {}
        for g, (gname, gkeys) in zip(groups, code_groups):
            entries = g.get("keys") or ()
            actual_keys = tuple(str(e.get("key")) for e in entries)
            # Historical profiles predate the 27-key schema and omit the two
            # derived share entries. Treat those omissions as implicit PASS;
            # future profiles must declare the full 27-key group explicitly.
            legacy_keys = tuple(
                key for key in gkeys if key not in {"call_share", "put_share"}
            )
            if actual_keys != gkeys and not (
                gname == "Chain Participation" and actual_keys == legacy_keys
            ):
                raise ValueError(f"{label}/{gname}: keys differ from the {side} template order")
            for e in entries:
                key = str(e["key"])
                if str(e.get("rule") or "").upper() == "PASS":
                    if "min" in e or "max" in e or "min_strict" in e or "max_strict" in e:
                        raise ValueError(f"{label}/{key}: PASS with a bound")
                    continue
                lo = _finite(e["min"], f"{label}/{key}/min") if "min" in e else None
                hi = _finite(e["max"], f"{label}/{key}/max") if "max" in e else None
                if lo is None and hi is None:
                    raise ValueError(f"{label}/{key}: neither PASS nor a bound")
                if lo is not None and hi is not None and lo > hi:
                    raise ValueError(f"{label}/{key}: min {lo} > max {hi}")
                s_lo, s_hi = _strict_flags_105(e, f"{label}/{key}", lo, hi)
                if s_lo or s_hi:
                    strict[key] = (s_lo, s_hi)
                    n_strict += s_lo + s_hi
                lines.append((key, lo, hi))
                n_bounds += (lo is not None) + (hi is not None)
        derived: List[_Line105] = []
        derived_after: Dict[str, str] = {}
        raw_derived = cell.get("derived_lines") or ()
        if not isinstance(raw_derived, (list, tuple)):
            raise ValueError(f"{label}: derived_lines must be a list")
        for e in raw_derived:
            key = str(e.get("key"))
            where = f"{label}/derived/{key}"
            if key not in DERIVED_KEYS_105:
                raise ValueError(f"{where}: unknown derived key (known: {sorted(DERIVED_KEYS_105)})")
            if key not in template_keys(side):
                raise ValueError(f"{where}: derived key is not in the {side} template")
            if key in {line_key for line_key, _lo, _hi in lines} or key in derived_after:
                raise ValueError(f"{where}: repeated template key")
            formula, inputs, _fn = DERIVED_KEYS_105[key]
            if e.get("derived") is not True or str(e.get("formula") or "") != formula:
                raise ValueError(f"{where}: must say derived=true and formula {formula!r}")
            if tuple(str(k) for k in e.get("inputs") or ()) != inputs:
                raise ValueError(f"{where}: inputs must be {list(inputs)}")
            after = str(e.get("after") or "")
            if after not in template_keys(side):
                raise ValueError(f"{where}: after {after!r} is not a {side} template key")
            lo = _finite(e["min"], f"{where}/min") if "min" in e else None
            hi = _finite(e["max"], f"{where}/max") if "max" in e else None
            if lo is None and hi is None:
                raise ValueError(f"{where}: needs a min or max")
            if lo is not None and hi is not None and lo > hi:
                raise ValueError(f"{where}: min {lo} > max {hi}")
            s_lo, s_hi = _strict_flags_105(e, where, lo, hi)
            if s_lo or s_hi:
                strict[key] = (s_lo, s_hi)
                n_strict += s_lo + s_hi
            derived.append((key, lo, hi))
            derived_after[key] = after
            n_bounds += (lo is not None) + (hi is not None)
        slug = f"{_SLUG_105[path_name]}_{side.lower()}"
        not_and: List[_NotAnd105] = []
        for item in cell.get("not_and") or ():
            clauses = _json_clauses(item.get("all"), f"{label}/not_and")
            if len(clauses) != 2:
                raise ValueError(f"{label}: not_and needs exactly 2 clauses")
            for key, _op, _lv in clauses:
                if key not in template_keys(side):
                    raise ValueError(f"{label}: not_and key {key!r} not in the {side} template")
            reason = str(item.get("reason") or f"tp105_{slug}_not_{clauses[0][0]}_and_{clauses[1][0]}")
            not_and.append((reason, clauses))
            n_bounds += 2
        raw_mr = cell.get("missing_reject_keys")
        if raw_mr is None:
            raw_mr = ()
        if not isinstance(raw_mr, (list, tuple)):
            raise ValueError(f"{label}: missing_reject_keys must be a list")
        mr_list = [str(k) for k in raw_mr]
        if len(set(mr_list)) != len(mr_list):
            raise ValueError(f"{label}: missing_reject_keys has a repeated key")
        for key in mr_list:
            if key not in template_keys(side):
                raise ValueError(f"{label}: missing_reject key {key!r} not in the {side} template")
            if key not in WARMUP_MISSING_REJECT_KEYS:
                raise ValueError(
                    f"{label}: missing_reject key {key!r} is not a warm-up key "
                    f"({sorted(WARMUP_MISSING_REJECT_KEYS)})"
                )
            if key not in {k for k, _lo, _hi in lines}:
                # Eric, Oct 5 17:35 PT: require only keys the cell gates.
                raise ValueError(f"{label}: missing_reject key {key!r} has no range line in this cell")
        mr_set = set(mr_list)
        missing_reject = tuple(k for k in template_keys(side) if k in mr_set)
        n_range += len(lines) + len(derived)
        n_derived += len(derived)
        n_not += len(not_and)
        n_mr += len(missing_reject)
        per_cell[label] = len(lines) + len(derived) + len(not_and)
        if derived:
            derived_per_cell[label] = tuple(k for k, _lo, _hi in derived)
        if "missing_reject_keys" in cell:
            mr_per_cell[label] = missing_reject  # declared (may be empty)
        profile = _CELL_TO_PROFILE_105[path_name]
        cells[(profile, side)] = _Cell105(
            label=label,
            profile=profile,
            side=side,
            slug=slug,
            lines=tuple(lines),
            not_and=tuple(not_and),
            template=code_groups,
            missing_reject=missing_reject,
            strict=strict,
            derived=tuple(derived),
            derived_after=derived_after,
        )
    counts = {
        "cells": len(cells),
        "guards": n_range + n_not,
        "range_lines": n_range,
        "not_and": n_not,
        "bounds": n_bounds,
        "template_call": len(TRADE_PROFILE_KEYS_CALL),
        "template_put": len(TRADE_PROFILE_KEYS_PUT),
        "per_cell": per_cell,
        "missing_reject": n_mr,
        "missing_reject_per_cell": mr_per_cell,
        "derived_lines": n_derived,
        "derived_per_cell": derived_per_cell,
        "strict_bounds": n_strict,
    }
    return _Profile105(
        cells=cells,
        zero_is_missing=zero,
        put_delta=put_delta,
        name=str(spec.get("name") or ""),
        version=profile_version,
        counts=counts,
    )


def load_profile_105(path: Optional[Path] = None) -> _Profile105:
    """Read the active flat 10.5-family JSON and build the profile."""
    if path is not None:
        json_path = Path(path)
    elif resolve_profile_version() == VERSION_10_23:
        json_path = Path(
            os.getenv(TRADE_PROFILE_1023_JSON_ENV)
            or Path(__file__).resolve().parent.parent / "configs" / "trade_profile_10.23.json"
        )
    elif resolve_profile_version() == VERSION_10_22:
        json_path = Path(
            os.getenv(TRADE_PROFILE_1022_JSON_ENV)
            or Path(__file__).resolve().parent.parent / "configs" / "trade_profile_10.22.json"
        )
    elif resolve_profile_version() == VERSION_10_21:
        json_path = Path(
            os.getenv(TRADE_PROFILE_1021_JSON_ENV)
            or Path(__file__).resolve().parent.parent / "configs" / "trade_profile_10.21.json"
        )
    elif resolve_profile_version() == VERSION_10_20:
        json_path = Path(
            os.getenv(TRADE_PROFILE_1020_JSON_ENV)
            or Path(__file__).resolve().parent.parent / "configs" / "trade_profile_10.20.json"
        )
    elif resolve_profile_version() == VERSION_10_18:
        json_path = Path(
            os.getenv(TRADE_PROFILE_1018_JSON_ENV)
            or Path(__file__).resolve().parent.parent / "configs" / "trade_profile_10.18.json"
        )
    elif resolve_profile_version() == VERSION_10_17:
        json_path = Path(
            os.getenv(TRADE_PROFILE_1017_JSON_ENV)
            or Path(__file__).resolve().parent.parent / "configs" / "trade_profile_10.17.json"
        )
    elif resolve_profile_version() == VERSION_10_16:
        json_path = Path(
            os.getenv(TRADE_PROFILE_1016_JSON_ENV)
            or Path(__file__).resolve().parent.parent / "configs" / "trade_profile_10.16.json"
        )
    else:
        json_path = Path(os.getenv(TRADE_PROFILE_105_JSON_ENV) or DEFAULT_PROFILE_105_JSON)
    with json_path.open(encoding="utf-8") as handle:
        spec = json.load(handle)
    # Continuation profiles may inherit an unchanged rule set while carrying
    # their own version and replay metadata. This keeps a replay alias exact
    # without duplicating a large JSON rules document.
    inherited_name = spec.get("inherits_from") if isinstance(spec, dict) else None
    if inherited_name:
        base_path = (json_path.parent / str(inherited_name)).resolve()
        with base_path.open(encoding="utf-8") as handle:
            base_spec = json.load(handle)
        merged = dict(base_spec)
        for key in ("name", "version", "status", "duplicate_of", "source", "replay_summary", "replay_artifact", "replay_manifest", "inherits_from"):
            if key in spec:
                merged[key] = spec[key]
        overrides = spec.get("profile_overrides") or []
        if overrides:
            merged["profiles"] = copy.deepcopy(base_spec.get("profiles", {}))
            for override in overrides:
                if not isinstance(override, dict):
                    raise ValueError("profile_overrides entries must be objects")
                profile_label = str(override.get("profile") or "")
                key = str(override.get("key") or "")
                target = merged["profiles"].get(profile_label)
                if target is None or not key:
                    raise ValueError(f"profile_overrides: unknown profile/key {profile_label!r}/{key!r}")
                found = False
                for group in target.get("groups", []):
                    for entry in group.get("keys", []):
                        if entry.get("key") != key:
                            continue
                        if "min" in override:
                            entry["min"] = override["min"]
                            entry.pop("rule", None)
                        if "max" in override:
                            entry["max"] = override["max"]
                            entry.pop("rule", None)
                        if "doc" in override:
                            entry["doc"] = override["doc"]
                        found = True
                        break
                    if found:
                        break
                if not found:
                    # Continuation profiles may also override a derived
                    # template key such as call_share/put_share. These
                    # entries live in derived_lines rather than groups, but
                    # are still part of the flat 27-key profile contract.
                    for entry in target.get("derived_lines", []):
                        if entry.get("key") != key:
                            continue
                        if "min" in override:
                            entry["min"] = override["min"]
                        if "max" in override:
                            entry["max"] = override["max"]
                        if "doc" in override:
                            entry["doc"] = override["doc"]
                        found = True
                        break
                if not found:
                    raise ValueError(f"profile_overrides: key {key!r} not found in {profile_label!r}")
        spec = merged
    profile = _build_profile_105(spec)
    profile.json_path = str(json_path)
    profile.json_sha256 = _sha256(json_path)
    return profile


def _init_profile_105() -> None:
    global _PROFILE_105, _PROFILE_105_ERROR
    try:
        _PROFILE_105 = load_profile_105()
        _PROFILE_105_ERROR = ""
        c = _PROFILE_105.counts
        log.info(
            "TP_PROFILE_LOADED | version=%s | active=%s | json=%s | json_sha256=%s | "
            "guards=%d | range_lines=%d | not_and=%d | bounds=%d | cells=%d | "
            "template=CALL:%d,PUT:%d | zero_is_missing=%s | per_cell=%s | "
            "derived=%s | strict_bounds=%d | "
            "missing_reject=%d | missing_reject_cells=%s | missing_default=pass",
            _PROFILE_105.version,
            resolve_profile_version(),
            _PROFILE_105.json_path,
            _PROFILE_105.json_sha256,
            c["guards"],
            c["range_lines"],
            c["not_and"],
            c["bounds"],
            c["cells"],
            c["template_call"],
            c["template_put"],
            ",".join(sorted(_PROFILE_105.zero_is_missing)),
            ",".join(f"{k}:{v}" for k, v in c["per_cell"].items()),
            ";".join(f"{k}:{'+'.join(v)}" for k, v in (c.get("derived_per_cell") or {}).items()) or "none",
            c.get("strict_bounds", 0),
            c.get("missing_reject", 0),
            ";".join(
                f"{k}:{'+'.join(v) or 'none'}" for k, v in (c.get("missing_reject_per_cell") or {}).items()
            )
            or "none",
        )
    except Exception as exc:
        _PROFILE_105 = None
        _PROFILE_105_ERROR = f"{type(exc).__name__}: {exc}"
        log.error(
            "TP_PROFILE_LOAD | version=%s | status=error | error=%s | effect=when %s is "
            "active every signal rejects with trade_profile_config_error",
            resolve_profile_version(),
            _PROFILE_105_ERROR,
            resolve_profile_version(),
        )


def profile_105_status() -> Dict[str, Any]:
    """10.5 load state for readiness checks and tests."""
    prof = _PROFILE_105
    return {
        "active_version": resolve_profile_version(),
        "version": prof.version if prof else resolve_profile_version(),
        "loaded": prof is not None,
        "error": _PROFILE_105_ERROR,
        "json_path": prof.json_path if prof else "",
        "json_sha256": prof.json_sha256 if prof else "",
        "counts": dict(prof.counts) if prof else {},
        "zero_is_missing": sorted(prof.zero_is_missing) if prof else [],
        "missing_reject": {
            label: list(keys)
            for label, keys in ((prof.counts.get("missing_reject_per_cell") or {}).items() if prof else ())
        },
    }


def cell_gated_keys(path: str, side: str, slot: Any = None) -> Optional[Tuple[str, ...]]:
    """Template keys this path/side/slot cell actually reads (10.5-family only).

    Range-line keys, ``not (A and B)`` clause keys, the inputs of its derived
    lines and its missing-reject keys. ``None`` when 10.5 is not the active
    profile or the cell is unknown (callers then treat every key as used).
    """
    if resolve_profile_version() not in (VERSION_10_5, VERSION_10_16, VERSION_10_17, VERSION_10_18, VERSION_10_20, VERSION_10_21, VERSION_10_22, VERSION_10_23):
        return None
    cell = profile_105_cell(path, side, slot)
    if cell is None:
        return None
    keys: List[str] = []
    for key, _lo, _hi in cell.lines:
        keys.append(key)
    for _reason, clauses in cell.not_and:
        for clause in clauses:
            keys.append(clause[0])
    for dkey, _lo, _hi in cell.derived:
        keys.extend(DERIVED_KEYS_105.get(dkey, ("", (), None))[1])
    keys.extend(cell.missing_reject)
    return tuple(dict.fromkeys(k for k in keys if k))


def profile_105_cell(path: str, side: str, slot: Any = None) -> Optional[_Cell105]:
    """The compiled 10.5-family cell for this path/side/slot, or None."""
    prof = _PROFILE_105
    name = profile_name(path, slot)
    if prof is None or name is None:
        return None
    return prof.cells.get((name, str(side or "").strip().upper()))


def _missing_105(values: Dict[str, Any], key: str, zero: frozenset) -> bool:
    raw = values.get(key)
    if _is_missing(raw):
        return True
    return key in zero and float(raw) == 0.0


def _evaluate_10_5(
    path: str,
    side: str,
    values_in: Dict[str, Any],
    *,
    slot: Any = None,
) -> TradeProfileDecision:
    prof = _PROFILE_105
    if prof is None:
        return _config_error_decision(
            path, side, slot, resolve_profile_version(), _PROFILE_105_ERROR or "profile not loaded"
        )
    side_u = str(side or "").strip().upper()
    if side_u in ("C", "LONG", "BULLISH"):
        side_u = "CALL"
    elif side_u in ("P", "SHORT", "BEARISH"):
        side_u = "PUT"
    name = profile_name(path, slot)

    values: Dict[str, Any] = dict(values_in or {})
    if prof.put_delta == "abs" and side_u == "PUT" and not _is_missing(values.get("delta")):
        values["delta"] = abs(float(values["delta"]))
    # The share fields are first-class template values. Compute them from the
    # same decision-time chain volumes before logging or evaluating, while
    # retaining legacy cell-level derived_lines as a compatible declaration.
    for dkey, (_formula, _inputs, fn) in DERIVED_KEYS_105.items():
        values[dkey] = fn(values)
    log_keys = _log_keys_105(side_u)
    missing = [k for k in log_keys if _is_missing(values.get(k))]

    def _done(admitted: bool, reason: str, stage: str, failed=None, prof_name=None):
        return TradeProfileDecision(
            admitted,
            prof_name if prof_name is not None else (name or ""),
            side_u,
            reason,
            list(failed or []),
            version=prof.version,
            ref_reason=reason,
            stage=stage,
            values=values,
            missing=missing,
        )

    if side_u not in ("CALL", "PUT"):
        return _done(False, "trade_profile_side_missing", "route")
    if name is None:
        reason = (
            "trade_profile_slot_missing"
            if str(path or "").strip().lower() in ("trendline_0dte", "trendline", "tl")
            else "trade_profile_unknown_path"
        )
        return _done(False, reason, "route", prof_name="")
    cell = prof.cells.get((name, side_u))
    if cell is None:
        return _done(False, "trade_profile_unknown_path", "route")

    for dkey, _dlo, _dhi in cell.derived:
        # Derived from the stamped template values only; any caller value is overwritten.
        values[dkey] = DERIVED_KEYS_105[dkey][2](values)
    zero = prof.zero_is_missing
    failed: List[Dict[str, Any]] = []
    first_reason = ""
    first_stage = ""
    mr = frozenset(cell.missing_reject)
    for key, lo, hi in _cell_checks_105(cell):
        if _missing_105(values, key, zero):
            if key in mr:
                failed.append({"key": key, "op": "MISSING", "level": None, "value": values.get(key)})
                if not first_reason:
                    first_reason, first_stage = f"tp105_{cell.slug}_missing_{key}", "tp105_missing"
            continue
        if lo is None and hi is None:
            continue  # missing-reject only (PASS line): present = pass
        value = float(values[key])
        s_lo, s_hi = cell.strict.get(key, (False, False))
        if lo is not None and (value <= lo if s_lo else value < lo):
            failed.append({"key": key, "op": "MIN", "level": lo, "value": value})
            if not first_reason:
                first_reason, first_stage = f"tp105_{cell.slug}_min_{key}", "tp105_range"
        elif hi is not None and (value >= hi if s_hi else value > hi):
            failed.append({"key": key, "op": "MAX", "level": hi, "value": value})
            if not first_reason:
                first_reason, first_stage = f"tp105_{cell.slug}_max_{key}", "tp105_range"
    for reason, clauses in cell.not_and:
        if all(
            not _missing_105(values, k, zero) and _clause_hit_105(float(values[k]), o, lv)
            for k, o, lv in clauses
        ):
            failed.append({
                "key": "+".join(k for k, _o, _lv in clauses),
                "op": "NOT_AND",
                "level": [lv for _k, _o, lv in clauses],
                "value": [float(values[k]) for k, _o, _lv in clauses],
            })
            if not first_reason:
                first_reason, first_stage = reason, "tp105_not_and"
    if failed:
        return _done(False, first_reason, first_stage, failed)
    return _done(True, f"tp105_admit_{cell.slug}", "tp105_admit")


def _cell_checks_105(cell: _Cell105) -> Tuple[_Line105, ...]:
    """Range lines plus missing-reject keys, merged in the side's template order.
    A missing-reject key with no range line is (key, None, None). A derived line
    follows its ``after`` template key."""
    if not cell.missing_reject and not cell.derived:
        return cell.lines
    cached = getattr(cell, "_checks_cache", None)
    if cached is not None:
        return cached
    by_key = {k: (k, lo, hi) for k, lo, hi in cell.lines}
    out: List[_Line105] = []
    for key in template_keys(cell.side):
        if key in by_key:
            out.append(by_key[key])
        elif key in cell.missing_reject:
            out.append((key, None, None))
        for line in cell.derived:
            if cell.derived_after.get(line[0]) == key:
                out.append(line)
    checks = tuple(out)
    object.__setattr__(cell, "_checks_cache", checks)
    return checks


def _clause_hit_105(value: float, op: str, level: float) -> bool:
    return (
        (op == "GT" and value > level)
        or (op == "GE" and value >= level)
        or (op == "LT" and value < level)
        or (op == "LE" and value <= level)
    )


def _log_keys_105(side_u: str) -> Tuple[str, ...]:
    """The side's 27 template keys in template order, then every other key the
    shadow profile reads (so one line carries both verdicts' inputs)."""
    if side_u in ("CALL", "PUT"):
        head = template_keys(side_u)
    else:
        head = TRADE_PROFILE_KEYS_CALL
    return head + tuple(k for k in decision_value_keys() if k not in head)


def _decision_log_keys(decision: TradeProfileDecision) -> Tuple[str, ...]:
    if decision.version in (VERSION_10_5, VERSION_10_16, VERSION_10_17, VERSION_10_18, VERSION_10_20, VERSION_10_21, VERSION_10_22, VERSION_10_23):
        return _log_keys_105(str(decision.side or "").upper())
    return decision_value_keys()


def _init_profiles() -> None:
    """Load every profile at import; the active one logs TP_PROFILE_LOADED first."""
    if resolve_profile_version() in (VERSION_10_5, VERSION_10_16, VERSION_10_17, VERSION_10_18, VERSION_10_20, VERSION_10_21, VERSION_10_22, VERSION_10_23):
        _init_profile_105()
        _init_profile_102()
    else:
        _init_profile_102()
        _init_profile_105()


_init_profiles()
