"""Single Trade-Profile admit path.

Discovery Scan -> Must Execute -> single stamp -> Trade Profile -> accept/reject
-> sizing / caps.

Must Execute (feasibility only, never quality):
  M1 signal geometry (discovery / watch, unchanged)
  M2 cooldown / day-exclude (unchanged)
  M3 book-full precheck -> max_open_positions_reached (unchanged, runs first)
  M4 one chain fetch via chain_fetch_manager (unchanged)
  M5 data readiness -> data_wait:<key> (hold) / data_unavailable:<key> (retire)
  M6 Call / Put Skip Signals (GATE_call_ss / GATE_put_ss, kept OUTSIDE the TP)
     -> skip_signal:<call|put>
  M7 one shared contract pick -> contract_unavailable:<why>
Then ONE stamp (central_tech.decision_stamp) and ONE TP evaluation
(TRADE_PROFILE_VERSION=wide|10.5|10.16|10.17|10.18|10.20|10.21|10.22|10.23) -> tp_admit:<cell> / tp_reject:<key>:<min|max|missing|not_and>.

This is the only admit. PO snapshot writers read ``po_decision_freeze`` /
``po_profile_decision`` / ``quality_gate_report`` plus ``po_decision_digest``.
"""
from __future__ import annotations

import json
import logging
import math
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

log = logging.getLogger(__name__)

VERSION_ENV = "TRADE_PROFILE_VERSION"
SHADOW_ENV = "TRADE_PROFILE_SHADOW_VERSION"
WIDE_JSON_ENV = "TRADE_PROFILE_WIDE_JSON"
VERSION_WIDE = "wide"
VERSION_105 = "10.5"
VERSION_1016 = "10.16"
VERSION_1017 = "10.17"
VERSION_1018 = "10.18"
VERSION_1020 = "10.20"
VERSION_1021 = "10.21"
VERSION_1022 = "10.22"
VERSION_1023 = "10.23"
DEFAULT_WIDE_JSON = Path(__file__).resolve().parent.parent / "configs" / "trade_profile_wide.json"

# ---------------------------------------------------------------------------
# Missing-value policy comes ONLY from the loaded profile JSON (Eric 10/13 13:52):
#   10.5 -> trade_profile_10.5.json (missing=pass; per-cell missing_reject_keys;
#           zero_is_missing) evaluated by trade_profile._evaluate_10_5 itself.
#   wide -> trade_profile_wide.json semantics.missing (default) + per-cell
#           missing_reject_keys / missing_defer_keys + semantics.zero_is_missing.
# No code-side table.
ZERO_IS_MISSING_DEFAULT = frozenset({"dir_pct_change_5m", "spy_change_pct"})
_CELL_FROM_PROFILE = {"ORB1": "ORB1", "TL0": "TL0", "TL1": "TL1", "TL2+": "TL2+", "ORB2": "Breakout"}


def _norm_version(raw: Optional[str]) -> str:
    v = str(raw or "").strip().lower()
    if v in ("wide", "wide_collect", "tpwide", "tp_wide"):
        return VERSION_WIDE
    if v in ("10.16", "10.16flat", "10.16_flat", "tp1016"):
        return VERSION_1016
    if v in ("10.17", "10.17flat", "10.17_flat", "tp1017"):
        return VERSION_1017
    if v in ("10.18", "10.18flat", "10.18_flat", "tp1018"):
        return VERSION_1018
    if v in ("10.20", "tp1020"):
        return VERSION_1020
    if v in ("10.21", "tp1021"):
        return VERSION_1021
    if v in ("10.22", "tp1022"):
        return VERSION_1022
    if v in ("10.23", "10.23flat", "10.23_flat", "tp1023"):
        return VERSION_1023
    if v in ("10.5", "10.5flat", "10.5_flat", "tp105"):
        return VERSION_105
    if v in ("off", "none", "false", "0"):
        return "off"
    return VERSION_105


def active_version() -> str:
    return _norm_version(os.getenv(VERSION_ENV))


def shadow_version() -> str:
    raw = os.getenv(SHADOW_ENV)
    if raw is None or str(raw).strip() == "":
        return "off"
    v = _norm_version(raw)
    return "off" if v == active_version() else v


def _side(raw: Any) -> str:
    s = str(raw or "").strip().upper()
    if s in ("PUT", "P", "SHORT", "BEAR", "BEARISH"):
        return "PUT"
    return "CALL"


def cell_name(path: str, slot: Any = None) -> Optional[str]:
    from easy0DTE.modules.trade_profile import profile_name

    prof = profile_name(path, slot)
    return _CELL_FROM_PROFILE.get(prof) if prof else None


def is_missing(keys: Mapping[str, Any], key: str) -> bool:
    raw = keys.get(key)
    if raw is None or raw == "" or isinstance(raw, bool):
        return True
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return True
    if not math.isfinite(v):
        return True
    zim = (_WIDE_META or {}).get("zero_is_missing", ZERO_IS_MISSING_DEFAULT)
    return key in zim and v == 0.0


# ---------------------------------------------------------------------------
# TP Wide loader (fail closed)
_WIDE: Optional[Dict[Tuple[str, str], Dict[str, Any]]] = None
_WIDE_ERROR = ""
_WIDE_META: Optional[Dict[str, Any]] = None


def _load_wide(path: Optional[Path] = None) -> Dict[Tuple[str, str], Dict[str, Any]]:
    from central_tech.decision_stamp import EXTENSION_KEYS
    from easy0DTE.modules.trade_profile import TRADE_PROFILE_KEYS_CALL

    p = Path(path or os.getenv(WIDE_JSON_ENV) or DEFAULT_WIDE_JSON)
    spec = json.loads(p.read_text(encoding="utf-8"))
    if spec.get("version") != VERSION_WIDE:
        raise ValueError(f"version {spec.get('version')!r} != 'wide'")
    known = set(TRADE_PROFILE_KEYS_CALL) | {"call_share", "put_share"} | set(EXTENSION_KEYS)
    want = {f"{c} {s}" for c in ("ORB1", "TL0", "TL1", "TL2+", "Breakout") for s in ("CALL", "PUT")}
    profiles = spec.get("profiles") or {}
    if set(profiles) != want:
        raise ValueError(f"profiles must be exactly {sorted(want)}")
    out: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for label, cell in profiles.items():
        c, s = label.rsplit(" ", 1)
        lines = []
        for e in cell.get("lines") or ():
            k = str(e["key"])
            if k not in known:
                raise ValueError(f"{label}: unknown key {k!r}")
            if e.get("extension") and k not in EXTENSION_KEYS:
                raise ValueError(f"{label}: {k!r} is not a registered extension")
            lo = float(e["min"]) if "min" in e else None
            hi = float(e["max"]) if "max" in e else None
            if lo is None and hi is None:
                raise ValueError(f"{label}/{k}: needs min or max")
            if lo is not None and hi is not None and lo > hi:
                raise ValueError(f"{label}/{k}: min > max")
            lines.append((k, lo, hi, bool(e.get("min_strict")), bool(e.get("max_strict"))))
        defer = tuple(str(k) for k in cell.get("missing_defer_keys") or ())
        rej = tuple(str(k) for k in cell.get("missing_reject_keys") or ())
        out[(c, s)] = {"lines": tuple(lines), "defer": defer, "reject": rej}
    sem = spec.get("semantics") or {}
    default = str(sem.get("missing_default") or "pass").lower()
    if default not in ("pass", "reject"):
        raise ValueError(f"semantics.missing_default {default!r} must be pass|reject")
    global _WIDE_META
    _WIDE_META = {"missing_default": default,
                  "zero_is_missing": frozenset(sem.get("zero_is_missing") or ZERO_IS_MISSING_DEFAULT)}
    return out


def init_wide() -> None:
    global _WIDE, _WIDE_ERROR
    try:
        _WIDE = _load_wide()
        _WIDE_ERROR = ""
        log.info(
            "TP_PROFILE_LOADED | version=wide | active=%s | shadow=%s | cells=%d | "
            "missing_policy=profile_json | missing_default=%s | extensions=equity_volume_ratio | "
            "skip_signals=outside_tp",
            active_version(), shadow_version(), len(_WIDE),
            (_WIDE_META or {}).get("missing_default"),
        )
    except Exception as exc:  # fail closed
        _WIDE = None
        _WIDE_ERROR = f"{type(exc).__name__}: {exc}"
        log.error("TP_PROFILE_LOAD | version=wide | status=error | error=%s", _WIDE_ERROR)


def wide_status() -> Dict[str, Any]:
    return {"loaded": _WIDE is not None, "error": _WIDE_ERROR,
            "cells": len(_WIDE or {}), "missing_default": (_WIDE_META or {}).get("missing_default"),
            "missing_reject": {f"{c} {s}": list(v.get("reject", ())) for (c, s), v in (_WIDE or {}).items()},
            "missing_defer": {f"{c} {s}": list(v.get("defer", ())) for (c, s), v in (_WIDE or {}).items()}}


# ---------------------------------------------------------------------------
def gated_keys(version: str, cell: str, side: str) -> Tuple[str, ...]:
    """Keys the cell reads (missing-policy universe), in template order."""
    side = _side(side)
    if version == VERSION_WIDE:
        spec = (_WIDE or {}).get((cell, side)) or {}
        return tuple(dict.fromkeys(k for k, *_ in spec.get("lines", ())))
    from easy0DTE.modules import trade_profile as tp

    prof = getattr(tp, "_PROFILE_105", None)
    pname = {"Breakout": "ORB2"}.get(cell, cell)
    c = prof.cells.get((pname, side)) if prof else None
    if c is None:
        return ()
    keys: List[str] = [k for k, _lo, _hi in c.lines]
    for _r, clauses in c.not_and:
        keys.extend(cl[0] for cl in clauses)
    keys.extend(k for k, _lo, _hi in c.derived)
    keys.extend(c.missing_reject)
    order = list(tp.template_keys(side)) + ["call_share", "put_share"]
    uniq = list(dict.fromkeys(keys))
    return tuple(sorted(uniq, key=lambda k: order.index(k) if k in order else 99))


_TP105_RE = re.compile(r"^tp105_[a-z0-9]+_(call|put)_(min|max|missing)_(.+)$")


def _map_105_reason(reason: str, failed: List[Dict[str, Any]]) -> str:
    m = _TP105_RE.match(reason or "")
    if m:
        return f"tp_reject:{m.group(3)}:{m.group(2)}"
    if failed and failed[0].get("op") == "NOT_AND":
        return f"tp_reject:{failed[0].get('key')}:not_and"
    return f"tp_reject:{reason or 'unknown'}"


def evaluate(stamp: Any, version: Optional[str] = None) -> Dict[str, Any]:
    """Evaluate one stamp. Reads ``stamp.keys`` only; never mutates it."""
    from central_tech.decision_stamp import canonical_digest

    ver = _norm_version(version) if version is not None else active_version()
    side = _side(stamp.side)
    cell = cell_name(stamp.path, stamp.slot)
    keys = stamp.keys
    out: Dict[str, Any] = {"version": ver, "cell": cell, "side": side,
                           "evaluated_digest": canonical_digest(keys), "failed": []}

    def done(admitted: Optional[bool], reason: str, stage: str, **kw):
        out.update(admitted=admitted, reason=reason, stage=stage, **kw)
        return out

    if cell is None:
        return done(False, "tp_reject:route:missing", "route")
    if ver == VERSION_WIDE and _WIDE is None:
        return done(False, "tp_reject:config_error:missing", "config", error=_WIDE_ERROR)
    gk = gated_keys(ver, cell, side)
    if ver in (VERSION_105, VERSION_1016, VERSION_1017) and not gk:
        return done(False, "tp_reject:config_error:missing", "config")
    if ver == VERSION_WIDE:
        spec = _WIDE[(cell, side)]
        default_reject = (_WIDE_META or {}).get("missing_default") == "reject"
        # 1) missing policy from the profile JSON (template order)
        for k in gk:
            if not is_missing(keys, k):
                continue
            if k in spec.get("defer", ()):
                return done(None, f"data_wait:{k}", "data_wait")
            if default_reject or k in spec.get("reject", ()):
                out["failed"].append({"key": k, "op": "MISSING"})
                return done(False, f"tp_reject:{k}:missing", "tp_missing")
        # 2) range lines (missing keys that reach here pass)
        for k, lo, hi, s_lo, s_hi in spec["lines"]:
            if is_missing(keys, k):
                continue
            v = float(keys[k])
            if lo is not None and (v <= lo if s_lo else v < lo):
                out["failed"].append({"key": k, "op": "MIN", "level": lo, "value": v})
                return done(False, f"tp_reject:{k}:min", "tp_range")
            if hi is not None and (v >= hi if s_hi else v > hi):
                out["failed"].append({"key": k, "op": "MAX", "level": hi, "value": v})
                return done(False, f"tp_reject:{k}:max", "tp_range")
        return done(True, f"tp_admit:{cell} {side}", "tp_admit")
    # 10.5/10.16/10.17/10.18: the profile engine applies the JSON's own missing policy
    # (missing=pass, per-cell missing_reject_keys, zero_is_missing). No pre-policy.
    from easy0DTE.modules.trade_profile import _evaluate_10_5

    d = _evaluate_10_5(stamp.path, side, dict(keys), slot=stamp.slot)
    out["legacy_reason"] = d.reason
    out["failed"] = list(d.failed or [])
    if d.admitted:
        return done(True, f"tp_admit:{cell} {side}", "tp_admit")
    return done(False, _map_105_reason(d.reason, out["failed"]), "tp105")


# ---------------------------------------------------------------------------
# Shared contract picker (M7): the ONE contract-selection unit.
def _f(c: Any, *names: str) -> Optional[float]:
    for n in names:
        raw = c.get(n) if isinstance(c, dict) else getattr(c, n, None)
        if raw is None:
            continue
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(v):
            return v
    return None


def _envf(name: str, default: float) -> float:
    try:
        raw = os.getenv(name)
        return float(raw) if raw not in (None, "") else default
    except (TypeError, ValueError):
        return default


def picker_limits(path: str) -> Dict[str, float]:
    from easy0DTE.modules.admit_contract_band import executor_delta_band
    from easy0DTE.modules.quality_gate_stack import _resolve_otm_limits

    dmin, dmax = executor_delta_band()
    _prefer, research, otm_on = _resolve_otm_limits(path)
    return {
        "delta_min": dmin, "delta_max": dmax,
        "otm_max": research if otm_on else 1.0,
        "premium_min": _envf("0DTE_MIN_ENTRY_PREMIUM", 0.03),
        "premium_max": _envf("0DTE_MAX_ENTRY_PREMIUM", 2.50),
        "min_volume": _envf("0DTE_ENTRY_MIN_VOLUME", 50),
        "min_oi": _envf("0DTE_ENTRY_MIN_OPEN_INTEREST", 100),
        # One canonical spread rule (percent of mid, per path, default 8%,
        # plus a one-tick $ allowance): modules/spread_policy.spread_check.
        # 0DTE_PICKER_MAX_SPREAD_PCT removed 10/13.
        "spread_max_pct": _path_spread_cap(path),
    }


def _path_spread_cap(path: str) -> float:
    from modules.spread_policy import path_spread_cap_points

    return float(path_spread_cap_points(path))


_FILTER_ORDER = ("no_quote", "delta_out_of_band", "otm", "premium_low", "premium_high", "volume", "oi", "spread")


def pick_contract(chain: Any, *, path: str, side: str, spot: Any) -> Dict[str, Any]:
    """{feasible: True|False|None, reason, strike, delta, otm_pct, ask, bid, spread_pct, ...}.
    None = the chain cannot answer (no chain / spot / deltas): not a reject."""
    out: Dict[str, Any] = {"feasible": None, "reason": "no_chain"}
    if not isinstance(chain, dict):
        return out
    try:
        s = float(spot or 0.0)
    except (TypeError, ValueError):
        s = 0.0
    if s <= 0:
        out["reason"] = "no_spot"
        return out
    is_put = _side(side) == "PUT"
    contracts = chain.get("puts" if is_put else "calls") or []
    lim = picker_limits(path)
    out.update(limits=lim)
    elim = {k: 0 for k in _FILTER_ORDER}
    feasible = []
    seen = 0
    for c in contracts if isinstance(contracts, list) else []:
        strike, delta = _f(c, "strike"), _f(c, "delta_est", "delta")
        if strike is None or strike <= 0 or delta is None or delta == 0:
            continue
        if (not is_put and strike < s) or (is_put and strike > s):
            continue
        seen += 1
        bid, ask = _f(c, "bid"), _f(c, "ask")
        ad = abs(delta)
        # Signed Trade Profile OTM: positive is OTM, negative is ITM. The
        # picker still filters ITM above, but the stamp preserves semantics.
        otm = (s - strike) / s if is_put else (strike - s) / s
        if ask is None or ask <= 0 or bid is None or bid <= 0:
            elim["no_quote"] += 1; continue
        if not (lim["delta_min"] - 1e-6 <= ad <= lim["delta_max"] + 1e-6):
            elim["delta_out_of_band"] += 1; continue
        if otm > lim["otm_max"] + 1e-12:
            elim["otm"] += 1; continue
        if ask < lim["premium_min"] - 1e-12:
            elim["premium_low"] += 1; continue
        if lim["premium_max"] > 0 and ask > lim["premium_max"] + 1e-12:
            elim["premium_high"] += 1; continue
        vol, oi = _f(c, "volume"), _f(c, "open_interest", "openInterest", "oi")
        if vol is not None and vol < lim["min_volume"]:
            elim["volume"] += 1; continue
        if oi is not None and oi < lim["min_oi"]:
            elim["oi"] += 1; continue
        from modules.spread_policy import spread_check

        _sc = spread_check(bid, ask, path)
        sp = _sc["spread_pct"]
        if sp is None or not _sc["ok"]:
            elim["spread"] += 1; continue
        feasible.append((otm, -ad, strike, ad, ask, bid, sp, c))
    out["eliminated"] = elim
    out["n_otm_side"] = seen
    if seen == 0:
        out["reason"] = "no_deltas_on_chain" if not contracts else "no_otm_side_contract"
        out["feasible"] = None if not contracts else False
        return out
    if not feasible:
        # The reason is the filter that removed the most candidates (ties: pipeline order).
        why = max(_FILTER_ORDER, key=lambda k: (elim[k], -_FILTER_ORDER.index(k)))
        out.update(feasible=False, reason=why)
        return out
    otm, _nd, strike, ad, ask, bid, sp, c = min(feasible, key=lambda t: (t[0], t[1]))
    # Picked contract's own L1 sizes from the same cached chain (no broker call).
    bsz, asz = _f(c, "bid_size", "bidSize"), _f(c, "ask_size", "askSize")
    l1 = None
    if bsz is not None and asz is not None and bsz >= 0 and asz >= 0 and (bsz + asz) > 0:
        l1 = (bsz - asz) / (bsz + asz)
    out.update(option_bid_size=bsz, option_ask_size=asz, option_l1_imbalance=l1)
    out.update(feasible=True, reason="ok", strike=strike, delta=round(ad, 4), otm_pct=round(otm, 4),
               ask=ask, bid=bid, spread_pct=round(sp, 3),
               volume=vol, open_interest=oi,
               symbol=(c.get("symbol") if isinstance(c, dict) else getattr(c, "symbol", None)))
    return out


def execution_pick(source: Any) -> Optional[Dict[str, Any]]:
    """The contract Trade Profile already chose, when strike and bid/ask are live."""
    boxes: List[Any] = []
    if isinstance(source, dict):
        boxes.append(source)
        meta = source.get("metadata")
        if isinstance(meta, dict):
            boxes.append(meta)
    else:
        meta = getattr(source, "metadata", None)
        if isinstance(meta, dict):
            boxes.append(meta)
        # ORB1 keeps the freeze on DTE0Signal.orb_signal, not on metadata.
        orb = getattr(source, "orb_signal", None)
        if isinstance(orb, dict):
            boxes.append(orb)
            nested = orb.get("metadata")
            if isinstance(nested, dict):
                boxes.append(nested)
    for box in boxes:
        if not isinstance(box, dict):
            continue
        freeze = box.get("po_decision_freeze")
        if not isinstance(freeze, dict):
            continue
        single = freeze.get("single_tp")
        if not isinstance(single, dict):
            continue
        pick = single.get("pick")
        if not isinstance(pick, dict) or pick.get("feasible") is not True:
            continue
        try:
            strike = float(pick.get("strike") or 0.0)
            bid = float(pick.get("bid") or 0.0)
            ask = float(pick.get("ask") or 0.0)
        except (TypeError, ValueError):
            continue
        if strike > 0.0 and bid > 0.0 and ask > 0.0:
            return pick
    return None


def chain_hint_from_pick(pick: Mapping[str, Any]) -> Dict[str, Any]:
    """Order hint for the picked strike. Premium is the ask (buy to open)."""
    bid = float(pick["bid"])
    ask = float(pick["ask"])
    hint: Dict[str, Any] = {
        "strike": float(pick["strike"]),
        "delta": float(pick.get("delta") or 0.0),
        "bid": bid,
        "ask": ask,
        "premium": ask,
        "mid": (bid + ask) / 2.0,
        "spread_pct": pick.get("spread_pct"),
        "source": "single_tp_pick",
        "chain_source": "single_tp_pick",
        "selected_by": "single_tp",
        "option_symbol": pick.get("symbol"),
    }
    if pick.get("volume") is not None:
        hint["volume"] = pick.get("volume")
    if pick.get("open_interest") is not None:
        hint["open_interest"] = pick.get("open_interest")
        hint["openInterest"] = pick.get("open_interest")
    bsz = pick.get("option_bid_size")
    if bsz is None:
        bsz = pick.get("bid_size") or pick.get("bidSize")
    asz = pick.get("option_ask_size")
    if asz is None:
        asz = pick.get("ask_size") or pick.get("askSize")
    if bsz is not None:
        hint["bid_size"] = bsz
        hint["option_bid_size"] = bsz
    if asz is not None:
        hint["ask_size"] = asz
        hint["option_ask_size"] = asz
    return hint


# One listed strike. High-priced names use $1 strikes; this does not walk the chain.
STRIKE_MARGIN_DOLLARS = 1.0


def price_margin(ask: float) -> float:
    """How far the ask may move before the order stops chasing it.

    Floor $0.05, cap $0.15. A $0.45 contract can tick to $0.50. A $2 contract
    can tick $0.15. Wider than that, the order keeps the selected ask.
    """
    return max(0.05, min(0.15, round(0.10 * float(ask), 2)))


def _leg_strike(leg: Any) -> Optional[float]:
    raw = getattr(leg, "strike", None)
    if raw is None and isinstance(leg, dict):
        raw = leg.get("strike")
    try:
        strike = float(raw)
    except (TypeError, ValueError):
        return None
    return strike if strike > 0.0 else None


def _leg_quote(leg: Any) -> Tuple[float, float]:
    bid = getattr(leg, "bid", None)
    ask = getattr(leg, "ask", None)
    if isinstance(leg, dict):
        bid = leg.get("bid", bid)
        ask = leg.get("ask", ask)
    try:
        return float(bid or 0.0), float(ask or 0.0)
    except (TypeError, ValueError):
        return 0.0, 0.0


def _copy_leg_size(hint: Dict[str, Any], leg: Any) -> None:
    vol = getattr(leg, "volume", None)
    oi = getattr(leg, "open_interest", None)
    bsz = getattr(leg, "bid_size", None)
    if bsz is None:
        bsz = getattr(leg, "bidSize", None)
    asz = getattr(leg, "ask_size", None)
    if asz is None:
        asz = getattr(leg, "askSize", None)
    if isinstance(leg, dict):
        vol = leg.get("volume", vol)
        oi = leg.get("open_interest", leg.get("openInterest", oi))
        if bsz is None:
            bsz = leg.get("bid_size", leg.get("bidSize"))
        if asz is None:
            asz = leg.get("ask_size", leg.get("askSize"))
    if vol is not None:
        hint["volume"] = vol
    if oi is not None:
        hint["open_interest"] = oi
        hint["openInterest"] = oi
    if bsz is not None:
        hint["bid_size"] = bsz
        hint["option_bid_size"] = bsz
    if asz is not None:
        hint["ask_size"] = asz
        hint["option_ask_size"] = asz


def align_pick_to_chain(
    chain: Any,
    option_type: str,
    pick: Mapping[str, Any],
) -> Tuple[Dict[str, Any], Any]:
    """Order quote for a pick that is already chosen.

    Same strike when that leg is on the chain. If its ask moved inside
    ``price_margin``, use the newer bid/ask. If it moved further, keep the
    selected bid/ask on that same strike. If the exact strike is missing,
    the nearest listed strike within $1 is used only when its ask is still
    inside the price margin. Otherwise the original pick is the order.
    """
    hint = chain_hint_from_pick(pick)
    hint["align"] = "pick"
    if not isinstance(chain, dict):
        return hint, None
    side = str(option_type or "").lower()
    key = "puts" if side == "put" else "calls"
    try:
        target = float(pick.get("strike") or 0.0)
        ask0 = float(pick.get("ask") or 0.0)
    except (TypeError, ValueError):
        return hint, None
    if target <= 0.0 or ask0 <= 0.0:
        return hint, None
    margin = price_margin(ask0)
    exact = None
    nearest = None
    nearest_gap: Optional[float] = None
    for leg in chain.get(key) or []:
        strike = _leg_strike(leg)
        if strike is None:
            continue
        gap = abs(strike - target)
        if gap <= 0.001:
            exact = leg
            break
        if gap <= STRIKE_MARGIN_DOLLARS and (nearest_gap is None or gap < nearest_gap):
            nearest = leg
            nearest_gap = gap
    if exact is not None:
        bid, ask = _leg_quote(exact)
        if bid > 0.0 and ask > 0.0 and abs(ask - ask0) <= margin + 1e-9:
            hint.update(
                bid=bid,
                ask=ask,
                premium=ask,
                mid=(bid + ask) / 2.0,
                align="same_strike_quote",
            )
            _copy_leg_size(hint, exact)
        else:
            hint["align"] = "same_strike_held_quote"
        return hint, exact
    if nearest is not None and nearest_gap is not None:
        bid, ask = _leg_quote(nearest)
        if bid > 0.0 and ask > 0.0 and abs(ask - ask0) <= margin + 1e-9:
            strike = float(_leg_strike(nearest) or target)
            hint.update(
                strike=strike,
                bid=bid,
                ask=ask,
                premium=ask,
                mid=(bid + ask) / 2.0,
                align="nearest_listed",
                strike_shift=round(strike - target, 4),
            )
            _copy_leg_size(hint, nearest)
            return hint, nearest
    return hint, None


def match_listed_leg(chain: Any, option_type: str, pick: Mapping[str, Any]) -> Any:
    """The listed chain leg at the picked strike, when it still has a bid and ask."""
    if not isinstance(chain, dict):
        return None
    side = str(option_type or "").lower()
    key = "puts" if side == "put" else "calls"
    try:
        target = float(pick.get("strike") or 0.0)
    except (TypeError, ValueError):
        return None
    if target <= 0.0:
        return None
    for leg in chain.get(key) or []:
        raw = getattr(leg, "strike", None)
        if raw is None and isinstance(leg, dict):
            raw = leg.get("strike")
        try:
            strike = float(raw)
        except (TypeError, ValueError):
            continue
        if abs(strike - target) > 0.001:
            continue
        bid = getattr(leg, "bid", None)
        ask = getattr(leg, "ask", None)
        if isinstance(leg, dict):
            bid = leg.get("bid", bid)
            ask = leg.get("ask", ask)
        try:
            if float(bid or 0.0) > 0.0 and float(ask or 0.0) > 0.0:
                return leg
        except (TypeError, ValueError):
            continue
    return None


def size_contracts(premium: float, cap: Optional[float] = None) -> int:
    """$600 per position: floor(cap / (premium*100)), min 1."""
    cap = float(cap if cap is not None else _envf("0DTE_MAX_POSITION_SIZE_SUM", 600.0))
    if not premium or premium <= 0:
        return 0
    return max(1, int(math.floor(cap / (float(premium) * 100.0) + 1e-9)))


# ---------------------------------------------------------------------------
REJECT_PREFIXES = ("max_open_positions_reached", "data_wait", "data_unavailable", "contract_unavailable",
                   "skip_signal", "tp_reject", "tp_admit", "capacity")


def reason_class(reason: str) -> str:
    r = str(reason or "")
    for p in REJECT_PREFIXES:
        if r == p or r.startswith(p + ":"):
            return p
    return "legacy"


def legacy_to_taxonomy(reason: str) -> str:
    """Map legacy reject codes onto the single-TP taxonomy (desk / alerts)."""
    r = str(reason or "")
    if reason_class(r) != "legacy":
        return r
    if r.startswith("contract_band_unavailable_"):
        return "contract_unavailable:" + r[len("contract_band_unavailable_"):]
    if r in ("dir_vol_missing_chain_volume",):
        return "data_unavailable:chain"
    if r in ("dir_pct_data_unavailable",):
        return "data_unavailable:call_share"
    if r in ("required_data_not_ready",):
        return "data_wait:chain"
    m = _TP105_RE.match(r)
    if m:
        return f"tp_reject:{m.group(3)}:{m.group(2)}"
    if "call_ss" in r:
        return "skip_signal:call"
    if "put_ss" in r:
        return "skip_signal:put"
    return r


init_wide()


# Process-level reference to the options chain manager (set where the gate
# resolves it) so the picker reads the same cached decision chain with no API call.
_CHAIN_MGR: Any = None


def register_chain_manager(mgr: Any) -> None:
    global _CHAIN_MGR
    if mgr is not None:
        _CHAIN_MGR = mgr


def registered_chain_manager() -> Any:
    return _CHAIN_MGR
