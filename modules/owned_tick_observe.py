"""Owned-position premium_path observation (evaluated + deferred).

Deferred lots still get an OCC tape row. Strategy latches are not advanced here:
this function never calls stealth.evaluate().
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)


def _meta(pos: Any) -> Dict[str, Any]:
    md = getattr(pos, "metadata", None)
    if not isinstance(md, dict):
        md = {}
        try:
            pos.metadata = md
        except Exception:
            pass
    return md


def _unified_tick_id(system: Any, tick_id: str = "") -> str:
    return str(
        tick_id
        or getattr(system, "_unified_0dte_monitor_tick_id", "")
        or getattr(system, "_unified_0dte_tick_id", "")
        or ""
    ).strip()


def owned_tick_eval(meta: Optional[Dict[str, Any]], tick_id: str = "") -> Optional[Dict[str, Any]]:
    """Return this tick's eval bag. Empty or mismatched tick_id never reuses."""
    tid = str(tick_id or "").strip()
    if not tid or not isinstance(meta, dict):
        return None
    bag = meta.get("_owned_tick_eval")
    if not isinstance(bag, dict):
        return None
    if str(bag.get("tick_id") or "") != tid:
        return None
    return bag


def reuse_owned_tick_eval(meta: Optional[Dict[str, Any]], tick_id: str = "") -> tuple:
    """Reuse this tick's result, including HOLD (out is None).

    Returns (True, out) when this (trade, tick) was already evaluated.
    """
    bag = owned_tick_eval(meta, tick_id)
    if bag is not None and bag.get("evaluated") and "out" in bag:
        return True, bag.get("out")
    return False, None


def _eval_now_iso(now: Any) -> str:
    if now is None:
        return datetime.now(timezone.utc).isoformat()
    if isinstance(now, datetime):
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return now.isoformat()
    text = str(now).strip()
    return text


def _already_wrote_eval(system: Any, trade_id: str, tick_id: str) -> bool:
    tid = str(trade_id or "").strip()
    tick = str(tick_id or "").strip()
    if not tid or not tick or system is None:
        return False
    bag = getattr(system, "_path_v2_eval_written", None)
    if not isinstance(bag, set):
        bag = set()
        try:
            system._path_v2_eval_written = bag
        except Exception:
            return False
    key = (tid, tick)
    if key in bag:
        return True
    bag.add(key)
    return False


def count_path_v2_eval(
    system: Any,
    trade_id: str,
    *,
    tick_id: str = "",
    eval_now: Any = None,
    eval_age_s: Any = None,
) -> int:
    """Count one completed evaluate, before the JSONL write."""
    from modules.owned_eval_scheduler import last_successful_eval_ts, seconds_since_last_successful_eval

    last_ts = last_successful_eval_ts(system, trade_id)
    since = seconds_since_last_successful_eval(system, trade_id)
    n = note_path_v2_eval(system, trade_id, interval_s=since if last_ts > 0 else None)
    log.info(
        "PATH_V2_EVAL_COUNT | trade_id=%s | tick_id=%s | n=%d | eval_now=%s | "
        "eval_age_s=%s | grep=PATH_V2_EVAL_COUNT",
        str(trade_id or ""),
        str(tick_id or ""),
        n,
        _eval_now_iso(eval_now) if eval_now is not None else None,
        eval_age_s,
    )
    return n


def note_path_v2_eval(system: Any, trade_id: str, *, interval_s: Optional[float] = None) -> int:
    """Count evaluations and collect per-trade intervals for p50/p95."""
    tid = str(trade_id or "").strip()
    if not tid or system is None:
        return 0
    counts = getattr(system, "_path_v2_eval_counts", None)
    if not isinstance(counts, dict):
        counts = {}
        try:
            system._path_v2_eval_counts = counts
        except Exception:
            counts = {}
    n = int(counts.get(tid, 0) or 0) + 1
    counts[tid] = n
    if interval_s is not None:
        try:
            gap = float(interval_s)
        except (TypeError, ValueError):
            gap = None
        if gap is not None and 0.0 <= gap < 1e6:
            ivals = getattr(system, "_path_v2_eval_intervals", None)
            if not isinstance(ivals, dict):
                ivals = {}
                try:
                    system._path_v2_eval_intervals = ivals
                except Exception:
                    ivals = {}
            ivals.setdefault(tid, []).append(gap)
    return n


def log_eval_interval_percentiles(system: Any, *, tick_id: str = "") -> None:
    ivals = getattr(system, "_path_v2_eval_intervals", None)
    if not isinstance(ivals, dict) or not ivals:
        return
    all_gaps: list = []
    for rows in ivals.values():
        if isinstance(rows, list):
            all_gaps.extend(float(x) for x in rows if x is not None)
    if not all_gaps:
        return
    all_gaps.sort()
    n = len(all_gaps)

    def _pct(p: float) -> float:
        idx = min(n - 1, max(0, int(round((p / 100.0) * (n - 1)))))
        return float(all_gaps[idx])

    log.info(
        "PATH_V2_EVAL_INTERVAL | tick_id=%s | n=%d | p50=%.3f | p95=%.3f | "
        "grep=PATH_V2_EVAL_INTERVAL",
        str(tick_id or ""),
        n,
        _pct(50.0),
        _pct(95.0),
    )


def _positive_int(val: Any) -> Optional[int]:
    try:
        if val is None or val == "":
            return None
        n = int(val)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def _positive_float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        n = float(val)
    except (TypeError, ValueError):
        return None
    if n != n or n <= 0:
        return None
    return n


def stamp_eval_record_fields(
    snap: Dict[str, Any],
    *,
    position: Any = None,
    option_quote: Optional[Dict[str, Any]] = None,
) -> None:
    """Stamp live eval/exit fields that compact JSONL must keep.

    Uses position quantity and modeled $0.65×2 fees. Does not invent a fill;
    copies actual_exit_* only when already present on the position or snap.
    """
    if not isinstance(snap, dict):
        return
    oq = option_quote if isinstance(option_quote, dict) else {}
    for key in (
        "broker_quote_timestamp",
        "observation_timestamp",
        "quote_age_seconds",
        "quote_age_ms",
    ):
        if snap.get(key) in (None, "") and oq.get(key) not in (None, ""):
            snap[key] = oq.get(key)
    qty = None
    md = _meta(position) if position is not None else {}
    for cand in (
        getattr(position, "quantity", None) if position is not None else None,
        getattr(position, "contracts", None) if position is not None else None,
        md.get("broker_quantity") if isinstance(md, dict) else None,
        md.get("filled_quantity") if isinstance(md, dict) else None,
        snap.get("contracts"),
        snap.get("quantity"),
        oq.get("contracts"),
        oq.get("quantity"),
    ):
        qty = _positive_int(cand)
        if qty:
            break
    if qty:
        snap["contracts"] = qty
        snap["quantity"] = qty
        try:
            from modules.options_trade_economics import compute_closing_fees, compute_opening_fees

            snap["opening_fees"] = compute_opening_fees(quantity=qty)
            snap["closing_fees"] = compute_closing_fees(quantity=qty)
            snap["round_trip_fees"] = round(
                float(snap["opening_fees"]) + float(snap["closing_fees"]), 2
            )
        except Exception:
            pass
    if snap.get("entry_premium") is None:
        for cand in (
            getattr(position, "entry_price", None) if position is not None else None,
            getattr(position, "entry_premium", None) if position is not None else None,
            md.get("entry_premium") if isinstance(md, dict) else None,
            oq.get("entry_premium"),
        ):
            ep = _positive_float(cand)
            if ep is not None:
                snap["entry_premium"] = ep
                break
    if snap.get("actual_exit_premium") is None:
        for cand in (
            md.get("actual_exit_premium") if isinstance(md, dict) else None,
            md.get("exit_fill_price") if isinstance(md, dict) else None,
        ):
            px = _positive_float(cand)
            if px is not None:
                snap["actual_exit_premium"] = px
                snap.setdefault("actual_exit_bid", px)
                break


def observe_owned_path_tick(
    system: Any,
    *,
    trade_id: str,
    position: Any = None,
    option_quote: Optional[Dict[str, Any]] = None,
    osnap: Optional[Dict[str, Any]] = None,
    underlying_obs: Any = None,
    evaluated: bool = True,
    defer_reason: str = "",
    monitor_tick_id: str = "",
    quote_grade: str = "",
    out: Any = None,
    eval_now: Any = None,
    eval_age_s: Any = None,
    exit_reason: str = "",
) -> None:
    """Write one premium_path row. Does not call stealth.evaluate()."""
    tid = str(trade_id or "").strip()
    if not tid or system is None:
        return
    oq = dict(option_quote) if isinstance(option_quote, dict) else {}
    snap = dict(osnap) if isinstance(osnap, dict) else {}
    pv = snap.get("path_v2") if isinstance(snap.get("path_v2"), dict) else {}
    if pv.get("peak") is not None and snap.get("path_v2_peak") is None:
        snap["path_v2_peak"] = pv.get("peak")
    if snap.get("path_pret") is None:
        samples = pv.get("prem_samples") if isinstance(pv, dict) else None
        if isinstance(samples, list) and samples:
            try:
                snap["path_pret"] = samples[-1][1]
            except (TypeError, IndexError, KeyError):
                pass
    if exit_reason and snap.get("path_v2_reason") is None:
        snap["path_v2_reason"] = str(exit_reason)
    tick_id = _unified_tick_id(
        system,
        str(
            monitor_tick_id
            or snap.get("monitor_tick_id")
            or oq.get("monitor_tick_id")
            or ""
        ),
    )
    # Write-only dedupe. Never skip an evaluation from here — the choke
    # blocks a second evaluate of the same (trade_id, tick_id).
    if evaluated and _already_wrote_eval(system, tid, tick_id):
        return
    und_px = 0.0
    if underlying_obs is not None:
        try:
            from modules.underlying_freshness import stamp_underlying_provenance

            stamp_underlying_provenance(snap, underlying_obs)
            und_px = float(getattr(underlying_obs, "price_or_zero", 0.0) or 0.0)
        except Exception:
            und_px = 0.0
    if und_px <= 0:
        try:
            und_px = float(snap.get("underlying_price") or snap.get("current_underlying_price") or 0.0)
        except (TypeError, ValueError):
            und_px = 0.0

    from modules.owned_eval_scheduler import last_successful_eval_ts, seconds_since_last_successful_eval

    last_ts = last_successful_eval_ts(system, tid)
    since = seconds_since_last_successful_eval(system, tid)
    snap["evaluated"] = bool(evaluated)
    snap["defer_reason"] = str(defer_reason or "") if not evaluated else ""
    snap["market_snapshot_id"] = tick_id
    snap["monitor_tick_id"] = tick_id
    snap["last_successful_eval_ts"] = last_ts if last_ts > 0 else None
    snap["seconds_since_last_successful_eval"] = None if last_ts <= 0 else float(since)
    snap["force_path_sample"] = True
    if eval_now is not None:
        snap["eval_now"] = _eval_now_iso(eval_now)
    try:
        if eval_age_s is not None:
            snap["eval_age_s"] = float(eval_age_s)
    except (TypeError, ValueError):
        pass
    if exit_reason:
        snap["exit_reason"] = str(exit_reason)
    tel = getattr(out, "telemetry", None) if out is not None else None
    if tel is None and isinstance(out, (tuple, list)) and len(out) >= 3 and isinstance(out[2], dict):
        tel = out[2]
    if isinstance(tel, dict) and tel.get("prem_vel_10") is not None:
        snap["prem_vel_10"] = tel.get("prem_vel_10")
    elif isinstance(snap.get("path_v2"), dict) and snap["path_v2"].get("prem_vel_10") is not None:
        snap["prem_vel_10"] = snap["path_v2"].get("prem_vel_10")
    if oq.get("quote_age_ms") is None and oq.get("quote_age_seconds") is not None:
        try:
            oq["quote_age_ms"] = float(oq.get("quote_age_seconds")) * 1000.0
        except (TypeError, ValueError):
            pass
    if snap.get("quote_age_ms") is None:
        if oq.get("quote_age_ms") is not None:
            snap["quote_age_ms"] = oq.get("quote_age_ms")
        elif snap.get("quote_age_seconds") is not None:
            try:
                snap["quote_age_ms"] = float(snap.get("quote_age_seconds")) * 1000.0
            except (TypeError, ValueError):
                pass
    stamp_eval_record_fields(snap, position=position, option_quote=oq)

    try:
        from profit_optimizer.trading_hooks import observe_0dte_tick

        observe_0dte_tick(
            system,
            trade_id=tid,
            underlying_price=float(und_px or 0.0),
            option_quote=oq,
            osnap=snap,
            quote_grade=str(quote_grade or snap.get("shadow_trust") or ""),
        )
    except Exception as exc:
        log.debug("owned_path_observe_skip | trade_id=%s | err=%s", tid, exc)

    pos = position
    if pos is None:
        try:
            pos = (getattr(system, "positions", None) or {}).get(tid)
        except Exception:
            pos = None
    if pos is None:
        return
    md = _meta(pos)
    bag = {
        "evaluated": bool(evaluated),
        "defer_reason": str(defer_reason or ""),
        "tick_id": tick_id,
        "eval_now": snap.get("eval_now"),
        "eval_age_s": snap.get("eval_age_s"),
        "exit_reason": str(exit_reason or ""),
    }
    if evaluated:
        bag["out"] = out
    md["_owned_tick_eval"] = bag
    osnap_live = md.get("option_stealth")
    if not isinstance(osnap_live, dict):
        osnap_live = {}
        md["option_stealth"] = osnap_live
    osnap_live["evaluated"] = bool(evaluated)
    osnap_live["defer_reason"] = str(defer_reason or "") if not evaluated else ""
    osnap_live["market_snapshot_id"] = tick_id
    osnap_live["monitor_tick_id"] = tick_id
    osnap_live["last_successful_eval_ts"] = snap.get("last_successful_eval_ts")
    osnap_live["seconds_since_last_successful_eval"] = snap.get("seconds_since_last_successful_eval")
    if underlying_obs is not None:
        try:
            from modules.underlying_freshness import stamp_underlying_provenance

            stamp_underlying_provenance(osnap_live, underlying_obs)
        except Exception:
            pass


def record_path_v2_eval_row(
    system: Any,
    *,
    trade_id: str,
    position: Any = None,
    option_quote: Optional[Dict[str, Any]] = None,
    osnap: Optional[Dict[str, Any]] = None,
    underlying_obs: Any = None,
    monitor_tick_id: str = "",
    quote_grade: str = "",
    out: Any = None,
    eval_now: Any = None,
    eval_age_s: Any = None,
    exit_reason: str = "",
    decision: Any = None,
) -> None:
    """Single choke write after evaluate_path_v2_tick / stealth.evaluate."""
    tel = None
    if decision is not None:
        tel = getattr(decision, "diagnostics", None) or getattr(decision, "telemetry", None)
        if not exit_reason:
            exit_reason = str(
                getattr(decision, "reason", "") or getattr(decision, "exit_reason", "") or ""
            )
    if out is not None and isinstance(out, (tuple, list)):
        if len(out) >= 2 and not exit_reason:
            exit_reason = str(out[1] or "")
        if len(out) >= 3 and isinstance(out[2], dict):
            tel = out[2]
    snap = dict(osnap) if isinstance(osnap, dict) else {}
    if eval_age_s is None and isinstance(tel, dict) and tel.get("age_seconds") is not None:
        eval_age_s = tel.get("age_seconds")
    if isinstance(tel, dict) and tel.get("prem_vel_10") is not None:
        snap["prem_vel_10"] = tel.get("prem_vel_10")
    observe_owned_path_tick(
        system,
        trade_id=trade_id,
        position=position,
        option_quote=option_quote,
        osnap=snap,
        underlying_obs=underlying_obs,
        evaluated=True,
        monitor_tick_id=monitor_tick_id,
        quote_grade=quote_grade,
        out=out,
        eval_now=eval_now,
        eval_age_s=eval_age_s,
        exit_reason=exit_reason,
    )


def observe_deferred_owned_tick(
    system: Any,
    pos: Any,
    *,
    tick_id: str,
    defer_reason: str = "eval_budget",
) -> None:
    """OCC observation for a budget-deferred owned lot (no stealth.evaluate)."""
    tid = str(getattr(pos, "position_id", "") or "").strip()
    if not tid:
        return
    md = _meta(pos)
    existing = owned_tick_eval(md, tick_id)
    if existing is not None:
        return
    oq = md.get("exact_quote") if isinstance(md.get("exact_quote"), dict) else None
    if not isinstance(oq, dict):
        oq = md.get("option_quote") if isinstance(md.get("option_quote"), dict) else {}
    osnap = md.get("option_stealth") if isinstance(md.get("option_stealth"), dict) else {}
    obs = None
    try:
        from modules.underlying_freshness import resolve_underlying_observation

        obs = resolve_underlying_observation(system, str(getattr(pos, "symbol", "") or ""))
    except Exception:
        obs = None
    observe_owned_path_tick(
        system,
        trade_id=tid,
        position=pos,
        option_quote=oq,
        osnap=osnap,
        underlying_obs=obs,
        evaluated=False,
        defer_reason=str(defer_reason or "eval_budget"),
        monitor_tick_id=str(tick_id or ""),
    )
    log.info(
        "EVAL_BUDGET_DEFER | trade_id=%s | symbol=%s | defer_reason=%s | evaluated=false | "
        "path_row=true | tick_id=%s",
        tid,
        str(getattr(pos, "symbol", "") or ""),
        str(defer_reason or "eval_budget"),
        str(tick_id or ""),
    )
