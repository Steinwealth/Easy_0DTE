"""Tiny desk action feed — same moment as Telegram, not the 33MB ledger.

The hosted desk polls ``{ns}/desk_public/actions.json`` (~few KB). Writes run on a
background thread with a GCS RPC timeout so the trading event loop never waits.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

MAX_ACTIONS = 40
_LOCK = threading.Lock()
_ACTIONS: List[Dict[str, Any]] = []
_SEQ = 0
_WORKER: Optional[threading.Thread] = None
_DIRTY = False
_HYDRATED = False

CACHE_CONTROL = "public, max-age=0, no-cache"


def desk_actions_gcs_path() -> str:
    try:
        from modules.runtime_mode import gcs_namespace

        ns = gcs_namespace()
    except Exception:
        ns = "research_app"
    return f"{ns}/desk_public/actions.json"


def _iso(raw: Any) -> str:
    if isinstance(raw, datetime):
        dt = raw if raw.tzinfo else raw.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    if raw:
        return str(raw)
    return datetime.now(timezone.utc).isoformat()


def _num(raw: Any) -> Optional[float]:
    try:
        n = float(raw)
    except (TypeError, ValueError):
        return None
    return n if n == n else None  # noqa: PLR0124 NaN


def path_from_position(position: Any) -> str:
    md = getattr(position, "metadata", None) or {}
    if not isinstance(md, dict):
        md = {}
    no = md.get("normalized_options") if isinstance(md.get("normalized_options"), dict) else {}
    blob = " ".join(
        str(x or "")
        for x in (
            getattr(position, "strategy_type", None),
            md.get("strategy_type"),
            no.get("strategy_type"),
            no.get("strategy_name"),
            md.get("strategy_name"),
        )
    ).lower()
    if "trendline" in blob or " tl" in f" {blob}":
        return "TL"
    if "orb1" in blob:
        return "ORB1"
    if "orb2" in blob or "breakout" in blob:
        return "ORB2"
    if "orb" in blob or "single" in blob:
        return "ORB1"
    return "—"


def side_from_position(position: Any) -> str:
    side = str(getattr(position, "option_side", None) or "").upper()
    if "PUT" in side:
        return "PUT"
    if "CALL" in side:
        return "CALL"
    pid = str(getattr(position, "position_id", "") or "")
    if pid.lower().endswith("p") or "p-" in pid.lower() or pid[-1:].lower() == "p":
        if "p" in pid.lower().split("-")[-1]:
            return "PUT"
    lotto = getattr(position, "lotto_contract", None)
    ot = ""
    if isinstance(lotto, dict):
        ot = str(lotto.get("option_type") or "")
    elif lotto is not None:
        ot = str(getattr(lotto, "option_type", "") or "")
    if "put" in ot.lower():
        return "PUT"
    if "call" in ot.lower():
        return "CALL"
    if "p" in pid.lower() and "c" not in pid.lower().split("-")[-1]:
        return "PUT"
    return "CALL" if "c" in pid.lower() else "—"


def realized_pct(position: Any, pnl: Optional[float]) -> Optional[float]:
    if pnl is None:
        return None
    md = getattr(position, "metadata", None)
    md_dict = md if isinstance(md, dict) else {}
    entry_econ = md_dict.get("trade_economics_entry") if isinstance(md_dict.get("trade_economics_entry"), dict) else {}
    stamped = _num(entry_econ.get("total_cost_basis"))
    if stamped and stamped > 0:
        cost = stamped
    else:
        entry = _num(getattr(position, "entry_price", None)) or 0.0
        qty = _num(getattr(position, "quantity", None)) or 0.0
        premium = entry * qty * 100.0
        if premium <= 0:
            return None
        blob = str(getattr(position, "position_type", "") or "").lower()
        no = md_dict.get("normalized_options") if isinstance(md_dict.get("normalized_options"), dict) else {}
        is_credit = "credit" in blob or "credit" in str(no.get("position_type") or md_dict.get("position_type") or "").lower()
        open_fees = _num(entry_econ.get("opening_fees"))
        if open_fees is None:
            econ = md_dict.get("economic_summary") if isinstance(md_dict.get("economic_summary"), dict) else {}
            econ_entry = econ.get("entry") if isinstance(econ.get("entry"), dict) else {}
            open_fees = _num(econ_entry.get("opening_fees"))
        if open_fees is None:
            open_fees = round(qty * 0.65, 2) if qty else 0.0
        cost = premium if is_credit else premium + float(open_fees or 0.0)
    if cost <= 0:
        return None
    return round((float(pnl) / cost) * 10000.0) / 100.0


def compact_action(kind: str, position: Any, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    extra = extra if isinstance(extra, dict) else {}
    pid = str(getattr(position, "position_id", None) or extra.get("trade_id") or "")
    kind_s = "exit" if str(kind).lower().startswith("exit") or str(kind).lower() == "close" else "entry"
    entry = _num(getattr(position, "entry_price", None))
    exit_px = _num(extra.get("exit_price")) or _num(getattr(position, "exit_price", None)) or _num(
        getattr(position, "current_value", None)
    )
    qty = _num(getattr(position, "quantity", None)) or 0.0
    is_credit = False
    md = getattr(position, "metadata", None)
    if isinstance(md, dict):
        no = md.get("normalized_options") if isinstance(md.get("normalized_options"), dict) else {}
        blob = str(no.get("position_type") or md.get("position_type") or getattr(position, "position_type", "") or "").lower()
        is_credit = "credit" in blob
    else:
        is_credit = "credit" in str(getattr(position, "position_type", "") or "").lower()
    pnl = _num(extra.get("pnl"))
    if kind_s == "exit" and entry and qty:
        try:
            from modules.options_trade_economics import (
                net_result_pnl_dollars,
                net_result_pnl_fraction,
                resolve_true_to_live_exit_fill,
            )

            md_dict = md if isinstance(md, dict) else {}
            ttl_exit = resolve_true_to_live_exit_fill(
                exit_fill=float(exit_px or 0.0),
                metadata=md_dict,
                mode=str(md_dict.get("runtime_mode") or extra.get("mode") or ""),
            )
            if ttl_exit > 0:
                exit_px = ttl_exit
            if not (exit_px and exit_px > 0):
                if pnl is None:
                    pnl = _num(getattr(position, "realized_pnl", None))
            else:
                entry_econ = md_dict.get("trade_economics_entry") if isinstance(md_dict.get("trade_economics_entry"), dict) else {}
                econ_sum = md_dict.get("economic_summary") if isinstance(md_dict.get("economic_summary"), dict) else {}
                econ_entry = econ_sum.get("entry") if isinstance(econ_sum.get("entry"), dict) else {}
                econ_exit = econ_sum.get("exit") if isinstance(econ_sum.get("exit"), dict) else {}
                open_fees = entry_econ.get("opening_fees")
                if open_fees is None:
                    open_fees = econ_entry.get("opening_fees")
                close_fees = econ_exit.get("closing_fees")
                cost_basis = entry_econ.get("total_cost_basis")
                if cost_basis is None:
                    cost_basis = econ_entry.get("total_cost_basis")
                pnl = net_result_pnl_dollars(
                    entry_fill=entry,
                    exit_fill=float(exit_px or 0.0),
                    quantity=int(qty),
                    is_credit_spread=is_credit,
                    opening_fees=open_fees,
                    closing_fees=close_fees,
                )
                fill_frac = net_result_pnl_fraction(
                    entry_fill=entry,
                    exit_fill=float(exit_px or 0.0),
                    quantity=int(qty),
                    is_credit_spread=is_credit,
                    opening_fees=open_fees,
                    closing_fees=close_fees,
                    cost_basis=_num(cost_basis),
                )
                extra = dict(extra)
                if fill_frac is not None:
                    extra["pnl_pct"] = round(float(fill_frac) * 10000.0) / 100.0
        except Exception:
            if pnl is None:
                pnl = _num(getattr(position, "realized_pnl", None))
    elif pnl is None:
        pnl = _num(getattr(position, "realized_pnl", None))
    ts = extra.get("ts") or getattr(position, "closed_at" if kind_s == "exit" else "entry_time", None)
    action_id = extra.get("id") or f"{kind_s}-{pid}"
    pnl_pct = extra.get("pnl_pct")
    if pnl_pct is None:
        pnl_pct = realized_pct(position, pnl)
    return {
        "id": action_id,
        "kind": kind_s,
        "trade_id": pid,
        "symbol": str(getattr(position, "symbol", None) or extra.get("symbol") or ""),
        "path": extra.get("path") or path_from_position(position),
        "side": extra.get("side") or side_from_position(position),
        "premium": entry,
        "exit_premium": exit_px if kind_s == "exit" else None,
        "pnl": pnl if kind_s == "exit" else None,
        "pnl_pct": pnl_pct if kind_s == "exit" else None,
        "ts": _iso(ts),
        "sort": extra.get("sort"),
    }


def ring_append(actions: List[Dict[str, Any]], action: Dict[str, Any], max_n: int = MAX_ACTIONS) -> List[Dict[str, Any]]:
    aid = str(action.get("id") or "")
    out = [a for a in actions if str(a.get("id") or "") != aid]
    out.append(action)
    if len(out) > max_n:
        out = out[-max_n:]
    return out


def _hydrate_locked() -> None:
    global _ACTIONS, _SEQ, _HYDRATED
    if _HYDRATED:
        return
    _HYDRATED = True
    try:
        from modules.gcs_persistence import get_gcs_persistence

        gcs = get_gcs_persistence()
        raw = gcs.read_string(desk_actions_gcs_path()) if gcs and gcs.enabled else None
        if not raw:
            return
        data = json.loads(raw)
        acts = data.get("actions") if isinstance(data, dict) else None
        if isinstance(acts, list):
            _ACTIONS = [a for a in acts if isinstance(a, dict) and a.get("id")][-MAX_ACTIONS:]
        try:
            _SEQ = int(data.get("seq") or 0)
        except (TypeError, ValueError):
            _SEQ = 0
    except Exception as err:
        log.debug("desk_action_pulse hydrate skipped | %s", err)


def _flush_sync() -> None:
    global _DIRTY, _SEQ
    with _LOCK:
        _hydrate_locked()
        payload = {
            "seq": _SEQ,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "actions": list(_ACTIONS),
        }
        _DIRTY = False
    body = json.dumps(payload, separators=(",", ":"), default=str)
    try:
        from modules.gcs_persistence import get_gcs_persistence

        gcs = get_gcs_persistence()
        if not gcs or not gcs.enabled:
            return
        ok = gcs.upload_string(
            desk_actions_gcs_path(),
            body,
            cache_control=CACHE_CONTROL,
            content_type="application/json; charset=utf-8",
            public_read=True,
        )
        if ok:
            log.info("DESK_ACTION_PULSE | wrote %d actions | seq=%s", len(payload["actions"]), payload["seq"])
    except Exception as err:
        log.warning("DESK_ACTION_PULSE | upload failed | %s", err)


def _worker() -> None:
    global _WORKER
    while True:
        with _LOCK:
            if not _DIRTY:
                _WORKER = None
                return
        _flush_sync()


def emit_desk_action(kind: str, position: Any, extra: Optional[Dict[str, Any]] = None) -> None:
    """Queue one fill/close onto the public desk pulse. Never blocks the caller."""
    global _SEQ, _DIRTY, _WORKER, _ACTIONS
    if position is None:
        return
    try:
        action = compact_action(kind, position, extra)
    except Exception as err:
        log.debug("desk_action_pulse compact failed | %s", err)
        return
    if not action.get("id"):
        return
    with _LOCK:
        _hydrate_locked()
        _SEQ += 1
        action["seq"] = _SEQ
        _ACTIONS = ring_append(_ACTIONS, action)
        _DIRTY = True
        if _WORKER is not None and _WORKER.is_alive():
            return
        _WORKER = threading.Thread(target=_worker, name="desk-action-pulse", daemon=True)
        _WORKER.start()


def reset_desk_actions_for_seed() -> str:
    """Wipe the public Event Tape ring after a demo ledger reseed.

    Session bells live on snapshot.json, not here. Call before the executor
    can emit a new fill so Cloud Run memory cannot republish the old 40.
    """
    global _ACTIONS, _SEQ, _DIRTY, _HYDRATED
    path = desk_actions_gcs_path()
    with _LOCK:
        _hydrate_locked()
        _ACTIONS = []
        _HYDRATED = True
        _SEQ += 1
        _DIRTY = True
    _flush_sync()
    log.info("DEMO_SIM_LEDGER_SEED | desk_public actions cleared | path=%s | seq=%s", path, _SEQ)
    return path
