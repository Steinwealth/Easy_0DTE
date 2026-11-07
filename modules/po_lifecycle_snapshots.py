"""Record-only mid-trade Priority Optimizer snapshots for Profit Capture research.

Three types besides the existing 20p file (which stays in po_mfe20_snapshot.py):
age_120s, first_red, pullback. Arm is O(1) on the monitor tick. Persist
uses the same exit-snapshot hydrate as 20p. Nothing here changes Path V2,
admits, sizing, or exits. The 50p snapshot is retired (Oct 23, 2026).

Pullback (Oct 23, 2026) is a capped ladder of up to PO_PB_MAX_SHOTS (4) shots per
trade, not a once-only / _cN re-arm. See "Pullback ladder" below. Each shot is
its own create-if-absent object named ``<trade_id>__pullback_s{n}``.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from concurrent.futures import TimeoutError as FuturesTimeout
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from modules.po_lifecycle_moments import (
    PURPOSE_TO_FAMILY,
    PURPOSE_TO_STAGE_SUFFIX,
    SESSION_TECH_EVENT_MAX_AGE_S,
    event_clock_key,
    session_tech_may_refresh,
)

log = logging.getLogger(__name__)

_CLAIM_LOCK = threading.Lock()
_SAVED: Dict[str, set] = {
    "age_120s": set(),
    "first_red": set(),
    "pullback": set(),
}
_INFLIGHT: Dict[str, set] = {
    "age_120s": set(),
    "first_red": set(),
    "pullback": set(),
}
_WRITE_TIMES: List[float] = []
_WRITE_LOCK = threading.Lock()


def _env_flag(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return bool(default)
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _env_num(name: str, default: float) -> float:
    try:
        raw = os.environ.get(name)
        return float(raw) if raw not in (None, "") else float(default)
    except (TypeError, ValueError):
        return float(default)


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env_num(name, default))
    except (TypeError, ValueError):
        return int(default)


def research_lifecycle_enabled() -> bool:
    """Final never arms. Research defaults on; each type has its own flag."""
    try:
        from modules.runtime_mode import gcs_app_label

        if str(gcs_app_label() or "").upper() == "FINAL":
            return False
    except Exception:
        pass
    return True


def _type_enabled(snap_type: str) -> bool:
    if not research_lifecycle_enabled():
        return False
    flags = {
        "age_120s": "PO_SNAP_AGE120_ENABLED",
        "first_red": "PO_SNAP_FIRST_RED_ENABLED",
        "pullback": "PO_SNAP_PULLBACK_ENABLED",
    }
    return _env_flag(flags[snap_type], True)


def _as_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_dt(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    if hasattr(value, "timestamp"):
        dt = value
        return dt if getattr(dt, "tzinfo", None) else dt.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except Exception:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _now_dt(now: Any = None) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    parsed = _parse_dt(now)
    return parsed if parsed is not None else datetime.now(timezone.utc)


def _earliest_dt(*values: Any) -> Optional[datetime]:
    found = [dt for dt in (_parse_dt(v) for v in values) if dt is not None]
    return min(found) if found else None


def _remember_opened_at(
    osnap: Dict[str, Any],
    opened_at: Any = None,
    pos: Any = None,
    metadata: Any = None,
) -> Optional[datetime]:
    """Keep the earliest fill/open clock. A later Path V2 or restart clock cannot win."""
    meta = metadata if isinstance(metadata, dict) else {}
    candidates = [
        osnap.get("po_opened_at"),
        opened_at,
        osnap.get("opened_at"),
        osnap.get("entry_time"),
        osnap.get("fill_time"),
        meta.get("opened_at"),
        meta.get("entry_time"),
        meta.get("fill_time"),
        meta.get("entry_ts"),
    ]
    if pos is not None:
        candidates.extend(
            [
                getattr(pos, "opened_at", None),
                getattr(pos, "entry_time", None),
                getattr(pos, "fill_time", None),
                getattr(pos, "entry_ts", None),
            ]
        )
        pos_meta = getattr(pos, "metadata", None)
        if isinstance(pos_meta, dict):
            candidates.extend(
                [
                    pos_meta.get("opened_at"),
                    pos_meta.get("entry_time"),
                    pos_meta.get("fill_time"),
                ]
            )
    earliest = _earliest_dt(*candidates)
    if earliest is not None:
        osnap["po_opened_at"] = earliest.isoformat()
    return earliest


def _seconds_since_entry(
    osnap: Dict[str, Any],
    now: Any = None,
    opened_at: Any = None,
    pos: Any = None,
    metadata: Any = None,
) -> Optional[float]:
    if not isinstance(osnap, dict):
        return None
    opened = _remember_opened_at(osnap, opened_at, pos=pos, metadata=metadata)
    if opened is None:
        prev = _as_float(osnap.get("po_age_seconds"))
        return prev if prev is not None and prev >= 0 else None
    age = max(0.0, (_now_dt(now) - opened).total_seconds())
    prev = _as_float(osnap.get("po_age_seconds"))
    if prev is not None and prev > age:
        age = prev
    return age


def _stage_for(pos: Any, metadata: dict, snap_type: str) -> Tuple[str, str]:
    hint = ""
    try:
        from modules.unified_0dte_account import _position_strategy_type

        hint = str(_position_strategy_type(pos) or "").lower()
    except Exception:
        hint = str(getattr(pos, "strategy_type", "") or "").lower()
    if not hint:
        for key in ("strategy_type", "source_path", "strategy"):
            hint = str((metadata or {}).get(key) or "").lower()
            if hint:
                break
    suffix = PURPOSE_TO_STAGE_SUFFIX[snap_type]
    if "breakout" in hint:
        return f"breakout_{suffix}", "easy_breakout_0dte"
    if "trendline" in hint or "tline" in hint:
        return f"trendline_{suffix}", "easy_trendline_0dte"
    return f"orb_0dte_{suffix}", "easy_orb_0dte"


def _identity(trade_id: str, snap_type: str, osnap: Dict[str, Any]) -> str:
    # Pullback shots are named per shot by pullback_identity(); this is the
    # once-only identity for the other mid-trade types.
    return str(trade_id or "").strip()


def _already_saved(snap_type: str, identity: str, osnap: Dict[str, Any]) -> bool:
    if identity and identity in _SAVED[snap_type]:
        return True
    return bool(osnap.get(f"po_{snap_type}_snapshot_saved"))


def arm_lifecycle_from_live(
    osnap: Any,
    pret: Any,
    peak: Any = None,
    *,
    now: Any = None,
    opened_at: Any = None,
    age_seconds: Any = None,
    pos: Any = None,
    metadata: Any = None,
    trade_id: Any = None,
    pb_track_only: bool = False,
    exit_tick: bool = False,
) -> None:
    """Arm the four new snapshots from the live bid return and running peak.

    Pullback: ``pb_track_only`` updates the running bid high only (the pre-Path-V2
    call); ``exit_tick`` marks the tick that decided the exit, which never fires a
    shot (the ladder fires on ticks before the exit tick only).

    O(1) dict updates. No I/O. Safe to call twice on the same tick (before and
    after Path V2), same as 20p. Age is wall-clock since the earliest fill
    time. Path V2 ``age_seconds`` is a hint only when no open clock exists.
    The running peak never moves down.
    """
    if not isinstance(osnap, dict) or not research_lifecycle_enabled():
        return
    live = _as_float(pret)
    run_peak = _as_float(peak)
    if run_peak is None:
        run_peak = _as_float(osnap.get("max_pnl_pct"))
    held_peak = _as_float(osnap.get("po_lifecycle_peak"))
    if live is not None and run_peak is not None:
        run_peak = max(run_peak, live)
    elif live is not None:
        run_peak = live
    if held_peak is not None:
        run_peak = max(run_peak, held_peak) if run_peak is not None else held_peak
    age = _seconds_since_entry(
        osnap, now=now, opened_at=opened_at, pos=pos, metadata=metadata
    )
    if age is None:
        age = _as_float(age_seconds)
    if age is not None:
        osnap["po_age_seconds"] = age
    if live is not None:
        osnap["po_lifecycle_pret"] = live
    if run_peak is not None:
        osnap["po_lifecycle_peak"] = run_peak

    if _type_enabled("age_120s"):
        _arm_age_120s(osnap, age)
    if _type_enabled("first_red") and live is not None:
        _arm_first_red(osnap, live)
    if _type_enabled("pullback") and live is not None:
        tid_pb = str(
            trade_id
            or osnap.get("po_pb_trade_id")
            or getattr(pos, "position_id", "")
            or (metadata.get("trade_id") if isinstance(metadata, dict) else "")
            or ""
        ).strip()
        _arm_pullback(
            osnap,
            live,
            now=now,
            trade_id=tid_pb,
            age=age,
            allow_fire=not pb_track_only,
            exit_tick=bool(exit_tick),
        )


def _arm_age_120s(osnap: Dict[str, Any], age: Optional[float]) -> None:
    if osnap.get("po_age_120s_snapshot_saved"):
        osnap["po_age_120s_snapshot_due"] = False
        return
    need = max(1.0, _env_num("PO_SNAP_AGE_SEC", 120.0))
    if age is None or age < need:
        return
    if osnap.get("po_age_120s_first_cross_value") is None:
        osnap["po_age_120s_first_cross_value"] = age
    from modules.po_event_seal import note_event

    note_event(osnap, "age_120s")
    osnap["po_age_120s_snapshot_due"] = True


def _arm_first_red(osnap: Dict[str, Any], pret: float) -> None:
    if osnap.get("po_first_red_snapshot_saved"):
        osnap["po_first_red_snapshot_due"] = False
        return
    if pret >= 0:
        return
    if osnap.get("po_first_red_first_cross_value") is None:
        osnap["po_first_red_first_cross_value"] = pret
    from modules.po_event_seal import note_event

    note_event(osnap, "first_red")
    osnap["po_first_red_snapshot_due"] = True


# ==========================================================================
# Pullback ladder (Oct 23, 2026). Record-only; never changes Path V2.
#
# H = running max of the executable bid return (bid / entry fill - 1), kept in
# memory on every tick (no extra requests). Shot 1 arms at H >= PO_PB_ARM_H and
# fires on the first tick (before the exit tick) with  r <= H - dip(H):
#     dip = max(MIN_DIP, LOW_K * H)   for H <  PO_PB_BAND_H (0.20)
#     dip = max(MIN_DIP, HI_K  * H)   for H >= PO_PB_BAND_H
# After shot n (H_fire = H at that tick) the next shot needs a new high:
#     Hmin = max(NEXT_MIN_H, H_fire + NEXT_MIN_ADD, H_fire * MULTS[n-1])
# and then fires on the first dip after H >= Hmin. At most PO_PB_MAX_SHOTS (4).
# Each shot: one frozen hub read at the fire tick (no sync refresh, hard
# timeout), one create-if-absent write (seal first), object key
# ``<trade_id>__pullback_s{n}``. The writer never re-reads tech.
# ==========================================================================
_PB_EPS = 1e-12
_PB_DEFAULT_MULTS = (1.25, 1.5, 1.5, 1.5)
_PB_PATH_V2_STATE_KEY = "path_v2"  # modules.path_v2_exit._STATE_KEY
_PB_HUB_POOL: Optional[Any] = None
_PB_HUB_POOL_LOCK = threading.Lock()
_PB_LATE_TECH_LAG_S = 5.0


def _pb_cfg() -> Dict[str, Any]:
    mults: List[float] = []
    raw = os.environ.get("PO_PB_NEXT_MULTS")
    for part in str(raw if raw not in (None, "") else "").replace(" ", "").split(","):
        if not part:
            continue
        try:
            val = float(part)
        except ValueError:
            mults = []
            break
        if val > 0:
            mults.append(val)
    if not mults:
        mults = list(_PB_DEFAULT_MULTS)
    return {
        "arm_h": _env_num("PO_PB_ARM_H", 0.15),
        "band_h": _env_num("PO_PB_BAND_H", 0.20),
        "low_k": _env_num("PO_PB_LOW_K", 0.70),
        "hi_k": _env_num("PO_PB_HI_K", 0.10),
        "min_dip": _env_num("PO_PB_MIN_DIP", 0.03),
        "max_shots": max(1, _env_int("PO_PB_MAX_SHOTS", 4)),
        "mults": mults,
        "next_min_h": _env_num("PO_PB_NEXT_MIN_H", 0.30),
        "next_min_add": _env_num("PO_PB_NEXT_MIN_ADD", 0.05),
        "hub_timeout": max(0.1, _env_num("PO_PB_HUB_TIMEOUT_S", 3.0)),
    }


def pb_dip(h: float, cfg: Optional[Dict[str, Any]] = None) -> float:
    """Required fall from the running high ``h`` before a shot fires."""
    c = cfg or _pb_cfg()
    k = c["low_k"] if h < c["band_h"] else c["hi_k"]
    return max(c["min_dip"], k * h)


def pb_next_hmin(shots_fired: int, h_fire: float, cfg: Optional[Dict[str, Any]] = None) -> float:
    """Running high the bid must reach before shot ``shots_fired + 1`` can fire."""
    c = cfg or _pb_cfg()
    mults = c["mults"]
    mult = mults[min(max(1, int(shots_fired)) - 1, len(mults) - 1)]
    return max(c["next_min_h"], h_fire + c["next_min_add"], h_fire * mult)


def pullback_identity(trade_id: str, n: int) -> str:
    """Object identity for shot ``n``: ``<trade_id>__pullback_s{n}``."""
    return f"{str(trade_id or '').strip()}__pullback_s{int(n)}"


def _pb_pending(osnap: Dict[str, Any]) -> List[Dict[str, Any]]:
    raw = osnap.get("po_pb_pending")
    if not isinstance(raw, list):
        return []
    return [s for s in raw if isinstance(s, dict) and s.get("n")]


def _pb_drop_pending(osnap: Dict[str, Any], n: int) -> None:
    raw = osnap.get("po_pb_pending")
    if isinstance(raw, list):
        osnap["po_pb_pending"] = [s for s in raw if not (isinstance(s, dict) and int(s.get("n") or 0) == int(n))]


def _pb_mark_saved(osnap: Dict[str, Any], n: int, ident: str) -> None:
    osnap[f"po_pbs{int(n)}_saved"] = True
    _pb_drop_pending(osnap, n)
    if ident:
        _SAVED["pullback"].add(ident)
    osnap["po_pullback_snapshot_due"] = bool(_pb_pending(osnap))
    osnap["po_pullback_snapshot_saved"] = int(osnap.get("po_pb_shots") or 0) >= _pb_cfg()["max_shots"] and not _pb_pending(osnap)


def _pb_track_ul(osnap: Dict[str, Any], now_ts: float) -> None:
    """Clock of the favorable underlying extreme (Path V2 S1 ul_mfe). O(1), no I/O."""
    st = osnap.get(_PB_PATH_V2_STATE_KEY)
    if not isinstance(st, dict):
        return
    mfe = _as_float(st.get("ul_mfe"))
    if mfe is None:
        return
    prev = _as_float(osnap.get("po_pb_ul_ext"))
    if prev is None or mfe > prev + _PB_EPS:
        osnap["po_pb_ul_ext"] = mfe
        osnap["po_pb_ul_ext_ts"] = now_ts


def _pb_ul_fields(osnap: Dict[str, Any]) -> Dict[str, Any]:
    """Underlying fields frozen at the fire tick. Values are fractions (0.012 = 1.2%).

    Favorable = up for calls, down for puts (Path V2 S1 sign-adjusted). Fields only,
    never a gate. ``pullback_ul_extreme_minus_prem_peak_s`` > 0 means the underlying
    extreme came after the premium peak.
    """
    st = osnap.get(_PB_PATH_V2_STATE_KEY)
    st = st if isinstance(st, dict) else {}
    ext_ts = _as_float(osnap.get("po_pb_ul_ext_ts"))
    h_ts = _as_float(osnap.get("po_pb_H_ts"))
    age_ms = _as_float(osnap.get("underlying_age_ms"))
    return {
        "pullback_ul_fav_move_pct": _as_float(st.get("ul_dir")),
        "pullback_ul_run_extreme_pct": _as_float(st.get("ul_mfe")),
        "pullback_ul_retrace_pct": _as_float(st.get("ul_dd")),
        "pullback_ul_retrace_frac_of_move": _as_float(st.get("ul_retr")),
        "pullback_ul_s1a_move_max_pct": _as_float(st.get("ulr_umax")),
        "pullback_ul_s1a_retrace_pct": _as_float(st.get("ulr_dd")),
        "pullback_ul_s1a_retrace_frac_of_move": _as_float(st.get("ulr_retr")),
        "pullback_ul_extreme_minus_prem_peak_s": (
            round(ext_ts - h_ts, 3) if ext_ts is not None and h_ts is not None else None
        ),
        "pullback_ul_age_s": (age_ms / 1000.0) if age_ms is not None else None,
    }


def _pb_day_pt() -> str:
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")


def _pb_recover_latch(osnap: Dict[str, Any], trade_id: str, cfg: Dict[str, Any]) -> int:
    """Rebuild the shot latch from sealed/canonical ``...pullback_s{n}`` objects. Read-only.

    Returns the number of shots found. Never creates or edits an object.
    """
    tid = str(trade_id or "").strip()
    if not tid:
        return 0
    try:
        from modules.po_canonical_store import canonical_name_for, read_body, seal_object_name
    except Exception:
        return 0
    day = _pb_day_pt()
    last_n = 0
    last_h: Optional[float] = None
    for n in range(1, int(cfg["max_shots"]) + 1):
        name = canonical_name_for(day, "pullback", pullback_identity(tid, n))
        body = None
        try:
            body = read_body(name) or read_body(seal_object_name(name))
        except Exception:
            body = None
        if not body:
            break
        last_n = n
        try:
            payload = json.loads(body)
            ex = payload.get("extra") if isinstance(payload, dict) else {}
            last_h = _as_float((ex or {}).get("pullback_H_fire")) or last_h
        except Exception:
            pass
    if last_n:
        osnap["po_pb_shots"] = last_n
        if last_h is not None:
            osnap["po_pb_last_H_fire"] = last_h
            osnap["po_pb_next_hmin"] = pb_next_hmin(last_n, last_h, cfg)
            held = _as_float(osnap.get("po_pb_H"))
            osnap["po_pb_H"] = last_h if held is None else max(held, last_h)
        for n in range(1, last_n + 1):
            osnap[f"po_pbs{n}_saved"] = True
        log.warning(
            "PO_PULLBACK_SNAPSHOT | latch_recovered | trade_id=%s | shots=%d | last_H_fire=%s",
            tid,
            last_n,
            last_h,
        )
    return last_n


def _arm_pullback(
    osnap: Dict[str, Any],
    pret: float,
    *,
    now: Any = None,
    trade_id: str = "",
    age: Optional[float] = None,
    allow_fire: bool = True,
    exit_tick: bool = False,
) -> None:
    """Track the bid high H and arm the next shot. O(1), no I/O (except one lazy
    read-only latch recovery when the process may have restarted mid-trade)."""
    cfg = _pb_cfg()
    live = float(pret)
    now_ts = _now_dt(now).timestamp()
    if trade_id and not osnap.get("po_pb_trade_id"):
        osnap["po_pb_trade_id"] = trade_id
    if "po_pb_first_age" not in osnap:
        osnap["po_pb_first_age"] = age
    h_prev = _as_float(osnap.get("po_pb_H"))
    high = live if h_prev is None else max(h_prev, live)
    if h_prev is None or high > h_prev + _PB_EPS:
        osnap["po_pb_H"] = high
        osnap["po_pb_H_ts"] = now_ts
    _pb_track_ul(osnap, now_ts)
    if exit_tick:
        osnap["po_pb_exit_tick"] = True
    if osnap.get("po_pb_exit_tick") or not allow_fire:
        return

    def _gate() -> Tuple[int, float]:
        fired_n = int(osnap.get("po_pb_shots") or 0)
        hmin_v = _as_float(osnap.get("po_pb_next_hmin"))
        return fired_n, (cfg["arm_h"] if hmin_v is None else hmin_v)

    fired, hmin = _gate()
    if fired >= cfg["max_shots"] or high < hmin - _PB_EPS:
        return
    if not osnap.get("po_pb_recovered"):
        osnap["po_pb_recovered"] = True
        first_age = _as_float(osnap.get("po_pb_first_age"))
        if trade_id and (first_age is None or first_age > 30.0):
            try:
                _pb_recover_latch(osnap, trade_id, cfg)
            except Exception:
                pass
            h_now = _as_float(osnap.get("po_pb_H"))
            high = h_now if h_now is not None else high
            fired, hmin = _gate()
            if fired >= cfg["max_shots"] or high < hmin - _PB_EPS:
                return
    dip = pb_dip(high, cfg)
    if live > high - dip + _PB_EPS:
        return

    n = fired + 1
    from modules.po_event_seal import note_event

    event_at = note_event(osnap, "pullback", clock_key=f"po_pbs{n}_event_at")
    osnap["po_pullback_event_at"] = event_at
    running_high = max(
        [v for v in (_as_float(osnap.get("max_pnl_pct")), _as_float(osnap.get("po_lifecycle_peak")), high) if v is not None]
    )
    h_ts = _as_float(osnap.get("po_pb_H_ts"))
    shot: Dict[str, Any] = {
        "n": n,
        "event_at": event_at,
        "H_fire": high,
        "fire_ret": live,
        "dip": dip,
        "running_high": running_high,
        "age_s": _as_float(osnap.get("po_age_seconds")),
        "seconds_since_H": round(now_ts - h_ts, 3) if h_ts is not None else None,
        "fired_ts": now_ts,
        "next_hmin": pb_next_hmin(n, high, cfg) if n < cfg["max_shots"] else None,
        "live": {
            "snapshot_premium": osnap.get("last_effective_premium"),
            "option_bid": osnap.get("option_bid"),
            "option_ask": osnap.get("option_ask"),
            "option_bid_size": osnap.get("option_bid_size") or osnap.get("bid_size"),
            "option_ask_size": osnap.get("option_ask_size") or osnap.get("ask_size"),
            "delta": osnap.get("delta"),
            "gamma": osnap.get("gamma"),
            "theta": osnap.get("theta"),
            "vega": osnap.get("vega"),
            "iv": osnap.get("iv") or osnap.get("implied_volatility"),
            "strike": osnap.get("strike"),
            "underlying": osnap.get("current_underlying_price"),
        },
    }
    shot.update(_pb_ul_fields(osnap))
    pending = osnap.get("po_pb_pending")
    if not isinstance(pending, list):
        pending = []
    pending.append(shot)
    osnap["po_pb_pending"] = pending
    fires = osnap.get("po_pb_fires")
    if not isinstance(fires, list):
        fires = []
    fires.append({"n": n, "H_fire": high, "fire_ret": live, "event_at": event_at})
    osnap["po_pb_fires"] = fires
    osnap["po_pb_shots"] = n
    osnap["po_pb_last_H_fire"] = high
    osnap["po_pb_next_hmin"] = pb_next_hmin(n, high, cfg)
    osnap["po_pullback_snapshot_due"] = True
    osnap["po_pullback_first_cross_value"] = live


def pullback_exit_fields(osnap: Any) -> Dict[str, Any]:
    """Exit-snapshot flags: ``pullback_missing`` / ``late_fallback`` when the bid
    high reached the arm level but no shot fired before the exit tick."""
    if not isinstance(osnap, dict) or not _type_enabled("pullback"):
        return {}
    high = _as_float(osnap.get("po_pb_H"))
    if high is None:
        return {}
    cfg = _pb_cfg()
    shots = int(osnap.get("po_pb_shots") or 0)
    reached = high >= cfg["arm_h"] - _PB_EPS
    if reached and shots == 0 and not osnap.get("po_pb_recovered"):
        osnap["po_pb_recovered"] = True
        try:
            shots = _pb_recover_latch(osnap, str(osnap.get("po_pb_trade_id") or ""), cfg)
        except Exception:
            shots = 0
    missing = bool(reached and shots == 0)
    return {
        "pullback_H_at_exit": high,
        "pullback_shots_fired": shots,
        "pullback_shots_max": cfg["max_shots"],
        "pullback_missing": missing,
        "late_fallback": missing,
    }


def _pb_pool() -> Any:
    global _PB_HUB_POOL
    with _PB_HUB_POOL_LOCK:
        if _PB_HUB_POOL is None:
            from concurrent.futures import ThreadPoolExecutor

            _PB_HUB_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="po-pb-hub")
        return _PB_HUB_POOL


def _pb_read_hub(system: Any, symbol: str, now_utc: Any, timeout_s: float) -> Any:
    """One hub read per shot. Refresh only when the cache is older than 90s."""

    def _call() -> Any:
        from modules import session_tech_indicators as sti
        from modules.po_lifecycle_moments import (
            SESSION_TECH_EVENT_MAX_AGE_S,
            session_tech_may_refresh,
        )

        return sti.read_session_tech_at(
            system,
            str(symbol or ""),
            now_utc=now_utc,
            max_age_s=SESSION_TECH_EVENT_MAX_AGE_S,
            allow_sync_refresh=session_tech_may_refresh("pullback"),
            refresh_timeout_s=float(timeout_s),
        )

    return _pb_pool().submit(_call).result(timeout=float(timeout_s))


def _pb_tech_error(reason: str) -> Dict[str, Any]:
    return {
        "tech_available": False,
        "tech_stale": True,
        "tech_error": str(reason),
        "freshness_s": None,
    }


def _freeze_pullback_tech(system: Any, osnap: Dict[str, Any], *, now_utc: Any = None, symbol: str = "") -> None:
    """ONE hub read per pending shot, stored as ``po_pullback_tech_{n}``. Never repeated."""
    pending = _pb_pending(osnap)
    if not pending:
        return
    cfg = _pb_cfg()
    now = now_utc if now_utc is not None else datetime.now(timezone.utc)
    for shot in pending:
        n = int(shot["n"])
        key = f"po_pullback_tech_{n}"
        if isinstance(osnap.get(key), dict) and osnap.get(key):
            continue
        out: Dict[str, Any]
        try:
            tech = _pb_read_hub(system, symbol, now, cfg["hub_timeout"])
        except (TimeoutError, FuturesTimeout):
            out = _pb_tech_error("hub_timeout")
        except Exception as exc:
            out = _pb_tech_error(f"hub_error:{type(exc).__name__}")
        else:
            if isinstance(tech, dict) and tech:
                out = dict(tech)
                out["tech_available"] = True
                out["tech_error"] = None
                out["tech_stale"] = not bool(out.get("tech_fresh"))
                out["freshness_s"] = out.get("tech_age_s")
                fired_ts = _as_float(shot.get("fired_ts"))
                lag = (_now_dt(now).timestamp() - fired_ts) if fired_ts is not None else None
                if lag is not None and lag > _PB_LATE_TECH_LAG_S:
                    out["tech_stale"] = True
                    out["tech_error"] = "read_after_fire"
                    out["tech_read_lag_s"] = round(lag, 3)
            else:
                out = _pb_tech_error("hub_empty")
        osnap[key] = out


def _pb_recover_if_present(
    ident: str, shot: Dict[str, Any], osnap: Dict[str, Any], metadata: Dict[str, Any]
) -> bool:
    """True when this shot's object already exists (skip), or its seal was promoted."""
    n = int(shot.get("n") or 0)
    from modules.po_canonical_store import durable_event_present, load_sealed_payload
    from modules.po_event_seal import clock_born_this_process_event, remember_event

    day = _pb_day_pt()
    if durable_event_present(day, "pullback", ident):
        log.info("PO_PULLBACK_SNAPSHOT | skip_exists | identity=%s", ident)
        _pb_mark_saved(osnap, n, ident)
        return True
    sealed = load_sealed_payload(day, "pullback", ident)
    if isinstance(sealed, dict):
        from modules.execution_feature_snapshot import persist_execution_snapshot

        persist_execution_snapshot(sealed)
        remember_event("pullback", ident, sealed)
        log.info("PO_PULLBACK_SNAPSHOT | skip_seal_exists_promoted | identity=%s", ident)
        _pb_mark_saved(osnap, n, ident)
        return True
    clock = shot.get("event_at")
    if clock not in (None, "") and not clock_born_this_process_event("pullback", clock):
        osnap[f"po_pbs{n}_seal_status"] = "recovery_unsealed"
        _pb_drop_pending(osnap, n)
        log.warning("PO_PULLBACK_SNAPSHOT | recovery_unsealed | identity=%s | event_at=%s", ident, clock)
        return True
    return False


def _pb_note_write_rate(trade_id: str) -> None:
    max_per_min = max(1, _env_int("PO_SNAP_MAX_PER_MIN", 40))
    now = time.time()
    with _WRITE_LOCK:
        alive = [t for t in _WRITE_TIMES if now - t <= 60.0]
        _WRITE_TIMES[:] = alive
        if len(alive) >= max_per_min:
            log.warning(
                "PO_SNAP | over_min_cap | trade_id=%s | writes_last_60s=%d | cap=%d | writing_anyway",
                trade_id,
                len(alive),
                max_per_min,
            )
        _WRITE_TIMES.append(now)


def _persist_pullback_shots(
    system: Any,
    pos: Any,
    metadata: Dict[str, Any],
    osnap: Dict[str, Any],
    trade_id: str,
    current_underlying: Any,
    premium: Any,
) -> None:
    """Write every pending shot in order, once each. Failures stay pending."""
    cfg = _pb_cfg()
    jobs: List[Tuple[Dict[str, Any], str]] = []
    with _CLAIM_LOCK:
        for shot in sorted(_pb_pending(osnap), key=lambda s: int(s.get("n") or 0)):
            n = int(shot.get("n") or 0)
            ident = pullback_identity(trade_id, n)
            if not trade_id or n < 1 or n > cfg["max_shots"]:
                _pb_drop_pending(osnap, n)
                continue
            if ident in _SAVED["pullback"] or osnap.get(f"po_pbs{n}_saved"):
                _pb_mark_saved(osnap, n, ident)
                continue
            if osnap.get(f"po_pbs{n}_seal_status") in ("build_failed", "recovery_unsealed"):
                _pb_drop_pending(osnap, n)
                continue
            if ident in _INFLIGHT["pullback"]:
                continue
            try:
                if _pb_recover_if_present(ident, shot, osnap, metadata):
                    continue
            except Exception:
                pass
            _pb_note_write_rate(trade_id)
            _INFLIGHT["pullback"].add(ident)
            jobs.append((shot, ident))
    sync = _env_flag("PO_SNAP_SYNC", True) or bool(getattr(system, "_po_lifecycle_sync", False))
    for shot, ident in jobs:
        if sync:
            _write_pullback_shot(system, pos, metadata, osnap, shot, ident, current_underlying, premium)
            continue
        threading.Thread(
            target=_write_pullback_shot,
            args=(system, pos, metadata, osnap, shot, ident, current_underlying, premium),
            name="po-snap-pullback",
            daemon=True,
        ).start()


def _write_pullback_shot(
    system: Any,
    pos: Any,
    metadata: Dict[str, Any],
    osnap: Dict[str, Any],
    shot: Dict[str, Any],
    ident: str,
    current_underlying: Any,
    premium: Any,
) -> None:
    """One create-if-absent write for one shot. Uses the frozen tech only."""
    from modules.po_event_seal import mark_persisted_event, remember_event, sealed_event

    n = int(shot.get("n") or 0)
    cfg = _pb_cfg()
    event_at = shot.get("event_at")
    if event_at in (None, ""):
        log.warning("PO_PULLBACK_SNAPSHOT | missing_event_clock | identity=%s", ident)
        with _CLAIM_LOCK:
            _INFLIGHT["pullback"].discard(ident)
        return
    tech = osnap.get(f"po_pullback_tech_{n}")
    if not isinstance(tech, dict) or not tech:
        # The writer re-reads the hub when it gets no frozen dict. Never let it.
        tech = _pb_tech_error("not_frozen")
        osnap[f"po_pullback_tech_{n}"] = tech
    live = shot.get("live") if isinstance(shot.get("live"), dict) else {}
    snap_px = live.get("snapshot_premium")
    if snap_px is None and premium is not None:
        snap_px = getattr(premium, "executable_exit_price", None)
    und = _as_float(live.get("underlying")) or _as_float(current_underlying) or _as_float(
        osnap.get("current_underlying_price")
    ) or 0.0
    stage, strategy = _stage_for(pos, metadata, "pullback")
    symbol = str(getattr(pos, "symbol", "") or "")
    real_tid = str(getattr(pos, "position_id", "") or metadata.get("trade_id") or "").strip()
    h_fire = _as_float(shot.get("H_fire"))
    fire_ret = _as_float(shot.get("fire_ret"))
    depth = None
    if h_fire not in (None, 0) and fire_ret is not None:
        depth = 1.0 - (fire_ret / h_fire)
    block = {
        "snapshot_type": "pullback",
        "event_ts": event_at,
        "seconds_since_entry": shot.get("age_s"),
        "pret": fire_ret,
        "peak_pret": h_fire,
        "seconds_since_peak": shot.get("seconds_since_H"),
        "rearm_cycle": n,
        "pullback_seq": n,
        "pullback_shots_max": cfg["max_shots"],
        "H_fire": h_fire,
        "pullback_depth": depth,
    }
    extra: Dict[str, Any] = {
        "event": "pullback",
        "trade_id": real_tid,
        "source_trade_id": real_tid,
        "po_event_identity": ident,
        "po_pullback_event_at": event_at,
        "po_snapshot_event_at": event_at,
        "mfe_pct": h_fire,
        "hwm_pnl_pct": h_fire,
        "peak_pct": h_fire,
        "current_pnl_pct": fire_ret,
        "snapshot_premium": snap_px,
        "first_cross_value": fire_ret,
        "via": "po_lifecycle_snapshots",
        "lifecycle_event": block,
        "strike": getattr(pos, "strike", None) or live.get("strike") or metadata.get("strike"),
        "option_bid": live.get("option_bid"),
        "option_ask": live.get("option_ask"),
        "option_bid_size": live.get("option_bid_size"),
        "option_ask_size": live.get("option_ask_size"),
        "delta": live.get("delta"),
        "gamma": live.get("gamma"),
        "theta": live.get("theta"),
        "vega": live.get("vega"),
        "iv": live.get("iv"),
        "pullback_seq": n,
        "pullback_shots_max": cfg["max_shots"],
        "pullback_H_fire": h_fire,
        "pullback_running_high": shot.get("running_high"),
        "pullback_fire_return": fire_ret,
        "pullback_dip": shot.get("dip"),
        "pullback_depth": depth,
        "pullback_seconds_since_H": shot.get("seconds_since_H"),
        "pullback_next_hmin": shot.get("next_hmin"),
        "tech_available": bool(tech.get("tech_available")),
        "tech_stale": bool(tech.get("tech_stale")),
        "tech_error": tech.get("tech_error"),
        "freshness_s": tech.get("freshness_s"),
    }
    for k in (
        "pullback_ul_fav_move_pct",
        "pullback_ul_run_extreme_pct",
        "pullback_ul_retrace_pct",
        "pullback_ul_retrace_frac_of_move",
        "pullback_ul_s1a_move_max_pct",
        "pullback_ul_s1a_retrace_pct",
        "pullback_ul_s1a_retrace_frac_of_move",
        "pullback_ul_extreme_minus_prem_peak_s",
        "pullback_ul_age_s",
    ):
        extra[k] = shot.get(k)
    if tech.get("tech_read_lag_s") is not None:
        extra["tech_read_lag_s"] = tech.get("tech_read_lag_s")
    try:
        from modules.po_snapshot_hydration import fill_missing_tp_keys_from_osnap

        fill_missing_tp_keys_from_osnap(extra, osnap)
        fill_missing_tp_keys_from_osnap(extra, live)
    except Exception:
        pass
    try:
        ok = system._save_0dte_exit_priority_snapshot(
            stage=stage,
            snapshot_strategy=strategy,
            symbol=symbol,
            trade_id=real_tid,
            option_side=getattr(pos, "option_side", None) or metadata.get("option_side"),
            entry_premium=getattr(pos, "entry_price", None),
            exit_underlying=und if und and und > 0 else None,
            position=pos,
            purpose="pullback",
            extra=extra,
            frozen_tech=tech,
        )
    except Exception as exc:
        log.warning(
            "PO_PULLBACK_SNAPSHOT | failed | trade_id=%s | identity=%s | err=%s",
            real_tid,
            ident,
            exc,
        )
        with _CLAIM_LOCK:
            _INFLIGHT["pullback"].discard(ident)
        return
    with _CLAIM_LOCK:
        _INFLIGHT["pullback"].discard(ident)
        if ok:
            remember_event("pullback", ident, sealed_event("pullback", ident))
            mark_persisted_event("pullback", ident)
            _pb_mark_saved(osnap, n, ident)
            log.info(
                "PO_PULLBACK_SNAPSHOT | saved | trade_id=%s | identity=%s | seq=%d | H_fire=%s | ret=%s | "
                "symbol=%s | tech_available=%s",
                real_tid,
                ident,
                n,
                h_fire,
                fire_ret,
                symbol,
                tech.get("tech_available"),
            )


_FIRST_RED_QUOTE_LIMIT_S = 45.0
_FIRST_RED_HOLD_S = 15.0


def stamp_monitor_option_quote_clock(
    osnap: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None,
    premium: Any = None,
) -> None:
    """Copy the live OCC quote clock already on this monitor tick. No fetch."""
    if not isinstance(osnap, dict):
        return
    oq = None
    if isinstance(metadata, dict):
        cand = metadata.get("exact_quote") or metadata.get("last_exact_quote")
        if isinstance(cand, dict):
            oq = cand
    if oq is None and premium is not None:
        raw = getattr(premium, "raw", None) or getattr(premium, "option_quote", None)
        if isinstance(raw, dict):
            oq = raw
        ts = getattr(premium, "broker_quote_timestamp", None)
        if ts not in (None, ""):
            osnap["po_monitor_quote_at"] = ts
        age_ms = getattr(premium, "broker_quote_age_ms", None)
        if age_ms is None:
            age_ms = getattr(premium, "quote_age_ms", None)
        if age_ms not in (None, ""):
            osnap["po_monitor_quote_age_ms"] = age_ms
    if not isinstance(oq, dict):
        return
    ts = (
        oq.get("broker_quote_timestamp")
        or oq.get("quote_timestamp")
        or oq.get("timestamp")
    )
    if ts not in (None, ""):
        osnap["po_monitor_quote_at"] = ts
    age_ms = oq.get("broker_quote_age_ms")
    if age_ms is None:
        age_ms = oq.get("quote_age_ms")
    if age_ms not in (None, ""):
        osnap["po_monitor_quote_age_ms"] = age_ms
    age_s = oq.get("quote_age_seconds")
    if age_s not in (None, ""):
        osnap["po_monitor_quote_age_sec"] = age_s
    record_owned_contract_sample(osnap, oq)


OWNED_SAMPLE_KEY = "owned_contract_samples"
OWNED_SAMPLE_MIN_SPACING_S = 15.0
OWNED_SAMPLE_MAX = 40
OWNED_DELTA_TARGET_S = 300.0
OWNED_DELTA_MIN_S = 120.0
OWNED_DELTA_MAX_S = 480.0


def _owned_quote_number(quote: Dict[str, Any], *keys: str) -> Optional[float]:
    for key in keys:
        val = quote.get(key)
        if val in (None, ""):
            continue
        try:
            num = float(val)
        except (TypeError, ValueError):
            continue
        if num != num or num < 0:
            continue
        return num
    return None


def record_owned_contract_sample(
    osnap: Optional[Dict[str, Any]],
    quote: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
    *,
    now: Optional[float] = None,
) -> None:
    """Keep OCC volume/OI prints on the open trade. No extra fetch."""
    if not isinstance(osnap, dict):
        return
    oq = quote if isinstance(quote, dict) else None
    if oq is None and isinstance(metadata, dict):
        cand = metadata.get("exact_quote") or metadata.get("last_exact_quote")
        oq = cand if isinstance(cand, dict) else None
    if not isinstance(oq, dict):
        return
    vol = _owned_quote_number(
        oq, "volume", "cumulative_volume", "totalVolume", "total_volume"
    )
    oi = _owned_quote_number(
        oq, "open_interest", "openInterest", "open_Interest"
    )
    if vol is None and oi is None:
        return
    stamp = float(now if now is not None else time.time())
    samples = osnap.get(OWNED_SAMPLE_KEY)
    if not isinstance(samples, list):
        samples = []
        osnap[OWNED_SAMPLE_KEY] = samples
    if samples:
        last = samples[-1] if isinstance(samples[-1], dict) else None
        if isinstance(last, dict):
            try:
                dt = stamp - float(last.get("t") or 0.0)
            except (TypeError, ValueError):
                dt = 1e9
            if dt < OWNED_SAMPLE_MIN_SPACING_S:
                if vol is not None:
                    last["volume"] = vol
                if oi is not None:
                    last["oi"] = oi
                last["t"] = stamp
                return
    samples.append({"t": stamp, "volume": vol, "oi": oi})
    if len(samples) > OWNED_SAMPLE_MAX:
        del samples[:-OWNED_SAMPLE_MAX]


def stamp_owned_contract_deltas(
    row: Optional[Dict[str, Any]],
    osnap: Optional[Dict[str, Any]],
    *,
    now: Optional[float] = None,
) -> None:
    """Fill owned 5m keys from OCC samples already on the trade. Honest null if warm-up."""
    if not isinstance(row, dict):
        return
    row["owned_contract_volume_delta_5m"] = None
    row["owned_contract_oi_change"] = None
    samples = osnap.get(OWNED_SAMPLE_KEY) if isinstance(osnap, dict) else None
    if not isinstance(samples, list) or not samples:
        return
    stamp = float(now if now is not None else time.time())
    cur = samples[-1] if isinstance(samples[-1], dict) else None
    if not isinstance(cur, dict):
        return
    prior = None
    best = None
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        try:
            age = stamp - float(sample.get("t") or 0.0)
        except (TypeError, ValueError):
            continue
        if age < OWNED_DELTA_MIN_S or age > OWNED_DELTA_MAX_S:
            continue
        err = abs(age - OWNED_DELTA_TARGET_S)
        if best is None or err < best:
            best = err
            prior = sample
    if prior is None:
        return
    if cur.get("volume") is not None and prior.get("volume") is not None:
        try:
            row["owned_contract_volume_delta_5m"] = float(cur["volume"]) - float(prior["volume"])
        except (TypeError, ValueError):
            pass
    if cur.get("oi") is not None and prior.get("oi") is not None:
        try:
            row["owned_contract_oi_change"] = float(cur["oi"]) - float(prior["oi"])
        except (TypeError, ValueError):
            pass


def _quote_age_s(osnap: Dict[str, Any], now: Optional[datetime] = None) -> Optional[float]:
    observed = osnap.get("po_monitor_quote_at") or osnap.get("broker_quote_timestamp")
    if observed in (None, ""):
        age_ms = osnap.get("po_monitor_quote_age_ms")
        if age_ms not in (None, ""):
            try:
                return max(0.0, float(age_ms) / 1000.0)
            except (TypeError, ValueError):
                pass
        age_s = osnap.get("po_monitor_quote_age_sec")
        if age_s not in (None, ""):
            try:
                return max(0.0, float(age_s))
            except (TypeError, ValueError):
                pass
        observed = osnap.get("quote_observed_at") or osnap.get("underlying_observed_at")
    if observed in (None, ""):
        age = osnap.get("quote_age_sec") or osnap.get("underlying_quote_age_sec")
        try:
            return float(age) if age not in (None, "") else None
        except (TypeError, ValueError):
            return None
    asof = _parse_iso(observed)
    if asof is None:
        return None
    stamp = now if now is not None else datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return max(0.0, (stamp - asof).total_seconds())


def first_red_ready_to_write(osnap: Dict[str, Any], *, now: Optional[datetime] = None) -> bool:
    """Hold first_red until the quote is under 45s, then write or flag it."""
    age = _quote_age_s(osnap, now)
    if age is None:
        osnap["po_first_red_quote_flag"] = "quote_clock_missing"
        return True
    if age <= _FIRST_RED_QUOTE_LIMIT_S:
        osnap["po_first_red_quote_stale"] = False
        osnap.pop("po_first_red_quote_flag", None)
        return True
    event_at = _parse_iso(osnap.get("po_first_red_event_at"))
    stamp = now if now is not None else datetime.now(timezone.utc)
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    held = (stamp - event_at).total_seconds() if event_at is not None else _FIRST_RED_HOLD_S
    if held < _FIRST_RED_HOLD_S:
        osnap["po_first_red_quote_hold"] = True
        return False
    osnap["po_first_red_quote_stale"] = True
    osnap["po_first_red_quote_flag"] = "quote_over_45s"
    return True


def _due_types(osnap: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for snap_type in ("age_120s", "first_red"):
        if osnap.get(f"po_{snap_type}_snapshot_due") and not osnap.get(
            f"po_{snap_type}_snapshot_saved"
        ):
            if snap_type == "first_red" and not first_red_ready_to_write(osnap):
                continue
            out.append(snap_type)
    return out


def _mark_saved(osnap: Dict[str, Any], metadata: Dict[str, Any], snap_type: str, identity: str) -> None:
    osnap[f"po_{snap_type}_snapshot_saved"] = True
    osnap[f"po_{snap_type}_snapshot_due"] = False
    metadata[f"po_{snap_type}_snapshot_saved"] = True
    if identity:
        _SAVED[snap_type].add(identity)


def _mark_skipped(osnap: Dict[str, Any], snap_type: str) -> None:
    osnap[f"po_{snap_type}_seal_status"] = "skipped"
    osnap[f"po_{snap_type}_snapshot_due"] = False


def _under_caps(trade_id: str) -> bool:
    """Per-trade cap can drop extras. The per-minute budget never delays a first-due write."""
    max_per_trade = max(1, _env_int("PO_SNAP_MAX_PER_TRADE", 6))
    used = 0
    for bucket in _SAVED.values():
        used += sum(1 for ident in bucket if ident == trade_id or ident.startswith(f"{trade_id}_c"))
    if used >= max_per_trade:
        return False
    max_per_min = max(1, _env_int("PO_SNAP_MAX_PER_MIN", 40))
    now = time.time()
    with _WRITE_LOCK:
        alive = [t for t in _WRITE_TIMES if now - t <= 60.0]
        _WRITE_TIMES[:] = alive
        if len(alive) >= max_per_min:
            log.warning(
                "PO_SNAP | over_min_cap | trade_id=%s | writes_last_60s=%d | cap=%d | writing_anyway",
                trade_id,
                len(alive),
                max_per_min,
            )
        _WRITE_TIMES.append(now)
    return True


def _recover_if_present(snap_type: str, identity: str, osnap: Dict[str, Any], metadata: Dict[str, Any]) -> bool:
    if not identity:
        return False
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from modules.po_canonical_store import durable_event_present, load_sealed_payload
    from modules.po_event_seal import (
        clock_born_this_process_event,
        remember_event,
    )

    family = PURPOSE_TO_FAMILY[snap_type]
    day = datetime.now(ZoneInfo("America/Los_Angeles")).strftime("%Y-%m-%d")
    if durable_event_present(day, family, identity):
        _mark_saved(osnap, metadata, snap_type, identity)
        return True
    sealed = load_sealed_payload(day, family, identity)
    if isinstance(sealed, dict):
        from modules.execution_feature_snapshot import persist_execution_snapshot

        persist_execution_snapshot(sealed)
        remember_event(snap_type, identity, sealed)
        osnap[f"po_{snap_type}_seal_status"] = "recovered_seal"
        _mark_saved(osnap, metadata, snap_type, identity)
        return True
    clock = osnap.get(event_clock_key(snap_type))
    if (
        clock not in (None, "")
        and not clock_born_this_process_event(snap_type, clock)
    ):
        osnap[f"po_{snap_type}_seal_status"] = "recovery_unsealed"
        _mark_skipped(osnap, snap_type)
        log.warning(
            "PO_%s_SNAPSHOT | recovery_unsealed | identity=%s | event_at=%s",
            snap_type.upper(),
            identity,
            clock,
        )
        return True
    return False


def _write_one(
    system: Any,
    pos: Any,
    metadata: Dict[str, Any],
    osnap: Dict[str, Any],
    snap_type: str,
    identity: str,
    current_underlying: Any,
    premium: Any,
) -> None:
    from modules.po_event_seal import mark_persisted_event, remember_event, sealed_event

    clock_key = event_clock_key(snap_type)
    event_at = osnap.get(clock_key)
    if event_at in (None, ""):
        log.warning(
            "PO_%s_SNAPSHOT | missing_event_clock | identity=%s",
            snap_type.upper(),
            identity,
        )
        with _CLAIM_LOCK:
            osnap[f"po_{snap_type}_snapshot_inflight"] = False
            _INFLIGHT[snap_type].discard(identity)
        return
    snap_px = osnap.get("last_effective_premium")
    if snap_px is None and premium is not None:
        snap_px = getattr(premium, "executable_exit_price", None)
    und = _as_float(current_underlying) or _as_float(osnap.get("current_underlying_price")) or 0.0
    stage, strategy = _stage_for(pos, metadata, snap_type)
    symbol = str(getattr(pos, "symbol", "") or "")
    real_tid = str(getattr(pos, "position_id", "") or metadata.get("trade_id") or "").strip()
    peak = _as_float(osnap.get("po_lifecycle_peak")) or _as_float(osnap.get("max_pnl_pct"))
    pret = _as_float(osnap.get("po_lifecycle_pret")) or _as_float(osnap.get("current_pnl_pct"))
    extra = {
        "event": snap_type,
        "trade_id": identity,
        "source_trade_id": real_tid,
        clock_key: event_at,
        "po_snapshot_event_at": event_at,
        "mfe_pct": peak,
        "hwm_pnl_pct": peak,
        "peak_pct": peak,
        "current_pnl_pct": pret,
        "snapshot_premium": snap_px,
        "first_cross_value": osnap.get(f"po_{snap_type}_first_cross_value"),
        "via": "po_lifecycle_snapshots",
        "lifecycle_event": _lifecycle_event_block(osnap, snap_type, event_at, pret, peak),
        "po_first_red_quote_stale": osnap.get("po_first_red_quote_stale") if snap_type == "first_red" else None,
        "po_first_red_quote_flag": osnap.get("po_first_red_quote_flag") if snap_type == "first_red" else None,
        "strike": getattr(pos, "strike", None) or osnap.get("strike") or metadata.get("strike"),
        "option_bid": osnap.get("option_bid"),
        "option_ask": osnap.get("option_ask"),
        "option_bid_size": osnap.get("option_bid_size") or osnap.get("bid_size"),
        "option_ask_size": osnap.get("option_ask_size") or osnap.get("ask_size"),
        "delta": osnap.get("delta"),
        "gamma": osnap.get("gamma"),
        "theta": osnap.get("theta"),
        "vega": osnap.get("vega"),
        "iv": osnap.get("iv") or osnap.get("implied_volatility"),
    }
    try:
        from modules.po_snapshot_hydration import fill_missing_tp_keys_from_osnap

        fill_missing_tp_keys_from_osnap(extra, osnap)
    except Exception:
        pass
    try:
        ok = system._save_0dte_exit_priority_snapshot(
            stage=stage,
            snapshot_strategy=strategy,
            symbol=symbol,
            trade_id=real_tid,
            option_side=getattr(pos, "option_side", None) or metadata.get("option_side"),
            entry_premium=getattr(pos, "entry_price", None),
            exit_underlying=und if und and und > 0 else None,
            position=pos,
            purpose=snap_type,
            extra=extra,
            frozen_tech=osnap.get(f"po_{snap_type}_tech")
            if isinstance(osnap.get(f"po_{snap_type}_tech"), dict)
            else None,
        )
    except Exception as exc:
        log.warning(
            "PO_%s_SNAPSHOT | failed | trade_id=%s | identity=%s | err=%s",
            snap_type.upper(),
            real_tid,
            identity,
            exc,
        )
        with _CLAIM_LOCK:
            osnap[f"po_{snap_type}_snapshot_inflight"] = False
            _INFLIGHT[snap_type].discard(identity)
        return
    with _CLAIM_LOCK:
        osnap[f"po_{snap_type}_snapshot_inflight"] = False
        _INFLIGHT[snap_type].discard(identity)
        if ok:
            remember_event(snap_type, identity, sealed_event(snap_type, identity))
            mark_persisted_event(snap_type, identity)
            _mark_saved(osnap, metadata, snap_type, identity)
            log.info(
                "PO_%s_SNAPSHOT | saved | trade_id=%s | identity=%s | symbol=%s | stage=%s",
                snap_type.upper(),
                real_tid,
                identity,
                symbol,
                stage,
            )


def _lifecycle_event_block(
    osnap: Dict[str, Any],
    snap_type: str,
    event_at: str,
    pret: Optional[float],
    peak: Optional[float],
) -> Dict[str, Any]:
    peak_v = peak
    pullback_depth = None
    if peak_v not in (None, 0) and pret is not None:
        try:
            pullback_depth = 1.0 - (float(pret) / float(peak_v))
        except (TypeError, ValueError, ZeroDivisionError):
            pullback_depth = None
    return {
        "snapshot_type": snap_type,
        "event_ts": event_at,
        "seconds_since_entry": osnap.get("po_age_seconds"),
        "pret": pret,
        "peak_pret": peak_v,
        "rearm_cycle": None,
        "pullback_depth": pullback_depth if snap_type == "pullback" else None,
    }


def freeze_due_lifecycle_tech(
    system: Any,
    osnap: Any,
    *,
    now_utc: Any = None,
    symbol: str = "",
) -> Any:
    """One hub read for every lifecycle family that armed this tick.

    Independent of the 20% Save exit. Same time-correct hub as the 20p snapshot.
    """
    if not isinstance(osnap, dict):
        return None
    _freeze_pullback_tech(system, osnap, now_utc=now_utc, symbol=symbol)
    due = [snap_type for snap_type in _due_types(osnap)]
    need = [
        snap_type
        for snap_type in due
        if not isinstance(osnap.get(f"po_{snap_type}_tech"), dict)
        or not osnap.get(f"po_{snap_type}_tech")
    ]
    if not need:
        existing = next(
            (
                osnap.get(f"po_{snap_type}_tech")
                for snap_type in due
                if isinstance(osnap.get(f"po_{snap_type}_tech"), dict)
            ),
            None,
        )
        return existing
    try:
        from modules.session_tech_indicators import read_session_tech_at

        # Refresh only when age_120s is in this shared read and the cache is
        # older than 90s. first_red alone stays cache-only. The hub skips
        # the broker call when the monitor is busy.
        tech = read_session_tech_at(
            system,
            str(symbol or ""),
            now_utc=now_utc or datetime.now(timezone.utc),
            max_age_s=SESSION_TECH_EVENT_MAX_AGE_S,
            allow_sync_refresh=any(session_tech_may_refresh(snap_type) for snap_type in need),
            refresh_timeout_s=3.0,
        )
    except Exception:
        tech = None
    if isinstance(tech, dict) and tech:
        for snap_type in need:
            osnap[f"po_{snap_type}_tech"] = tech
        return tech
    return None


def maybe_persist_lifecycle_snapshots(
    system: Any,
    pos: Any,
    metadata: Any,
    current_underlying: Any = None,
    premium: Any = None,
) -> None:
    """Write any armed lifecycle snapshots. Failures stay due for the next tick."""
    if not isinstance(metadata, dict) or not research_lifecycle_enabled():
        return
    osnap = metadata.get("option_stealth")
    if not isinstance(osnap, dict):
        return
    try:
        stamp_monitor_option_quote_clock(osnap, metadata, premium)
    except Exception:
        pass
    try:
        arm_lifecycle_from_live(
            osnap,
            osnap.get("po_lifecycle_pret")
            if osnap.get("po_lifecycle_pret") is not None
            else osnap.get("current_pnl_pct"),
            osnap.get("po_lifecycle_peak")
            if osnap.get("po_lifecycle_peak") is not None
            else osnap.get("max_pnl_pct"),
            now=datetime.now(timezone.utc),
            pos=pos,
            metadata=metadata,
            trade_id=str(
                getattr(pos, "position_id", "") or metadata.get("trade_id") or ""
            ).strip(),
            # Production persist runs after evaluate, including the Path V2 exit
            # tick. Do not arm a shot on that tick (stealth already set the flag).
            exit_tick=bool(osnap.get("po_pb_exit_tick")),
        )
    except Exception:
        pass
    trade_id = str(getattr(pos, "position_id", "") or metadata.get("trade_id") or "").strip()
    due = _due_types(osnap)
    pb_pending = _pb_pending(osnap)
    if not due and not pb_pending:
        return
    freeze_due_lifecycle_tech(
        system,
        osnap,
        now_utc=datetime.now(timezone.utc),
        symbol=str(getattr(pos, "symbol", "") or ""),
    )
    jobs: List[str] = []
    with _CLAIM_LOCK:
        for snap_type in due:
            identity = _identity(trade_id, snap_type, osnap)
            if _already_saved(snap_type, identity, osnap):
                _mark_saved(osnap, metadata, snap_type, identity)
                continue
            if osnap.get(f"po_{snap_type}_seal_status") in ("build_failed", "recovery_unsealed"):
                _mark_skipped(osnap, snap_type)
                continue
            try:
                if _recover_if_present(snap_type, identity, osnap, metadata):
                    continue
            except Exception:
                pass
            if osnap.get(f"po_{snap_type}_snapshot_inflight") or (
                identity and identity in _INFLIGHT[snap_type]
            ):
                continue
            if not _under_caps(trade_id):
                log.warning(
                    "PO_%s_SNAPSHOT | drop | cap | trade_id=%s | identity=%s",
                    snap_type.upper(),
                    trade_id,
                    identity,
                )
                continue
            osnap[f"po_{snap_type}_snapshot_inflight"] = True
            if identity:
                _INFLIGHT[snap_type].add(identity)
            jobs.append(snap_type)
    # Default sync, same as 20p. Cloud Run can kill a daemon thread before GCS
    # commit; daily collect needs the seal+canonical write on this tick.
    sync = _env_flag("PO_SNAP_SYNC", True) or bool(getattr(system, "_po_lifecycle_sync", False))
    for snap_type in jobs:
        identity = _identity(trade_id, snap_type, osnap)
        if sync:
            _write_one(system, pos, metadata, osnap, snap_type, identity, current_underlying, premium)
            continue
        threading.Thread(
            target=_write_one,
            args=(system, pos, metadata, osnap, snap_type, identity, current_underlying, premium),
            name=f"po-snap-{snap_type}",
            daemon=True,
        ).start()
    if pb_pending:
        _persist_pullback_shots(system, pos, metadata, osnap, trade_id, current_underlying, premium)


def stamp_lifecycle_row(row: Dict[str, Any], extra: Optional[Dict[str, Any]] = None) -> None:
    """Time-correct extras for a mid-trade lifecycle snapshot. Never invents clocks."""
    if not isinstance(row, dict):
        return
    event_at = row.get("po_snapshot_event_at") or row.get("po_tech_asof_at")
    gaps = list(row.get("po_clock_gaps") or [])
    session_at = row.get("po_session_tech_asof_at")
    age = _clock_age_s(event_at, session_at)
    if age is not None:
        row["po_session_tech_age_s"] = age
        if age > 90.0 and "session_tech_stale" not in gaps:
            gaps.append("session_tech_stale")
    chain_at = row.get("po_chain_asof_at")
    chain_age = _clock_age_s(event_at, chain_at)
    if chain_age is not None:
        row["po_chain_age_s"] = chain_age
        if chain_age > 60.0 and "chain_stale" not in gaps:
            gaps.append("chain_stale")
    spy_at = row.get("po_spy_asof_at") or row.get("spy_observed_at")
    spy_age = _clock_age_s(event_at, spy_at)
    if spy_age is not None:
        row["po_spy_age_s"] = spy_age
        if spy_age > 45.0 and "spy_stale" not in gaps:
            gaps.append("spy_stale")
    if row.get("volume") in (None, ""):
        for key in ("session_volume", "cumulative_volume", "underlying_volume_1m", "volume_1m"):
            if row.get(key) not in (None, ""):
                row["volume"] = row.get(key)
                break
    _stamp_since_entry_deltas(row)
    _fill_midtrade_aliases(row, extra)
    if gaps:
        row["po_clock_gaps"] = gaps
    block = row.get("lifecycle_event")
    if not isinstance(block, dict) and isinstance(extra, dict):
        block = extra.get("lifecycle_event") if isinstance(extra.get("lifecycle_event"), dict) else {}
    if isinstance(block, dict):
        block.setdefault("snapshot_type", row.get("event") or row.get("po_tech_asof"))
        block.setdefault("event_ts", event_at)
        block.setdefault("seconds_since_entry", row.get("po_age_seconds") or block.get("seconds_since_entry"))
        block.setdefault("pret", row.get("current_pnl_pct"))
        block.setdefault("peak_pret", row.get("peak_pct") or row.get("hwm_pnl_pct"))
        row["lifecycle_event"] = block


def _clock_age_s(event_at: Any, asof_at: Any) -> Optional[float]:
    if event_at in (None, "") or asof_at in (None, ""):
        return None
    try:
        ev = _parse_iso(event_at)
        af = _parse_iso(asof_at)
        if ev is None or af is None:
            return None
        return max(0.0, (ev - af).total_seconds())
    except Exception:
        return None


def _parse_iso(value: Any) -> Optional[datetime]:
    if hasattr(value, "timestamp"):
        dt = value
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    text = str(value)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except Exception:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _fill_midtrade_aliases(row: Dict[str, Any], extra: Optional[Dict[str, Any]] = None) -> None:
    """Fill size / 5m / owned-strike aliases from the live extra or already-stamped keys."""
    src = extra if isinstance(extra, dict) else {}
    for dest, keys in (
        ("option_bid_size", ("option_bid_size", "bid_size")),
        ("option_ask_size", ("option_ask_size", "ask_size")),
        ("bid_size", ("bid_size", "option_bid_size")),
        ("ask_size", ("ask_size", "option_ask_size")),
        ("dir_pct_change_5m", ("dir_pct_change_5m",)),
        ("sma_50", ("sma_50",)),
        ("bollinger_width", ("bollinger_width",)),
        ("strike", ("strike", "po_options_owned_strike")),
    ):
        if row.get(dest) not in (None, ""):
            continue
        for key in keys:
            val = src.get(key) if src.get(key) not in (None, "") else row.get(key)
            if val not in (None, ""):
                row[dest] = val
                break
    try:
        from modules.session_tech_indicators import ensure_bollinger_complete

        ensure_bollinger_complete(row)
    except Exception:
        pass
    try:
        from modules.po_snapshot_hydration import stamp_tp_moment_cache

        stamp_tp_moment_cache(row, osnap=extra if isinstance(extra, dict) else None)
    except Exception:
        pass
    dir5m = row.get("po_dir5m")
    if isinstance(dir5m, dict) and row.get("dir_pct_change_5m") in (None, ""):
        if dir5m.get("dir_pct_change_5m") not in (None, ""):
            row["dir_pct_change_5m"] = dir5m.get("dir_pct_change_5m")
    if row.get("strike") not in (None, "") and row.get("po_options_owned_strike") in (None, ""):
        row["po_options_owned_strike"] = row.get("strike")


def _stamp_since_entry_deltas(row: Dict[str, Any]) -> None:
    age = _as_float(row.get("po_age_seconds") or row.get("seconds_since_entry"))
    window_lt = bool(age is not None and age < 300.0)
    row["window_lt_5m"] = window_lt
    for side in ("call", "put"):
        now_v = _as_float(row.get(f"{side}_chain_volume"))
        entry_v = _as_float(
            row.get(f"{side}_chain_volume_at_entry")
            or row.get(f"entry_{side}_chain_volume")
        )
        key = f"{side}_chain_volume_delta_since_entry"
        if now_v is not None and entry_v is not None:
            row[key] = now_v - entry_v
        elif window_lt and now_v is not None and row.get(f"{side}_chain_volume_delta_5m") in (None, ""):
            row[key] = None
            row[f"{side}_chain_volume_delta_5m_since_entry"] = None
    if window_lt:
        row.setdefault("dir_pct_change_5m_since_entry", row.get("dir_pct_change_5m"))


def reset_lifecycle_snapshots_for_tests() -> None:
    for bucket in _SAVED.values():
        bucket.clear()
    for bucket in _INFLIGHT.values():
        bucket.clear()
    with _WRITE_LOCK:
        _WRITE_TIMES.clear()
